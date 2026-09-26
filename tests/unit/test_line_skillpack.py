"""主线技能包 —— L2 skills: 的注入侧身份减法（守门测试）

覆盖五件事：
  1. **策略顺序**（顺序敏感）：未装线 → 不限制；空声明 → 不限制；非空 → 白名单（保序去重）
  2. **unknown 如实报告**：声明了但目录里没有的 id 既不进 allowed、也不被静默吞掉
  3. **known 的唯一权威**：运行时技能目录优先，不可用时退回磁盘事实，两者都失败也不抛
  4. **校验**：LineProfile.validate(known_skills=…) 拦住 typo / 重复 / 空 id，且旧签名仍可用
  5. **注入侧**：_build_skill_instructions 只注入本线技能、**切线后缓存不串线**、
     _loaded_skill_ids 与实际注入一致（orchestrator 用它做幻觉校验）

【为什么第 5 条必须断言「真实文本 + 真实 id 列表」，而不是「缓存对象是字典」】
    本任务最容易漏的坑是**缓存键**：切线的第一次调用若命中上一条线的缓存，注入的
    就是上一条线的技能段 —— 且只错这一次，之后再怎么点都正常。断言「缓存是个 dict」
    对这类缺陷完全无感；只有断言「切到 B 线后文本里没有 A 线的技能、且 id 列表也对」
    才真正锁得住。

【为什么不 mock SkillPack 与 _build_skill_instructions 本身】
    被测的就是这两者：判定的策略顺序、以及与全局启用状态求交的那一步。
    测试只控制**环境输入**（启用集合、主线档案目录、数据源路径），逻辑全部走真实实现。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.lines import (  # noqa: E402
    LineProfile,
    SkillPack,
    describe_line,
    get_line_registry,
    invalidate_known_skills_cache,
    known_skill_ids,
    line_skill_pack,
    resolve_skill_pack,
)
from agent.lines import skillpack as SP  # noqa: E402


# ════════════════════════════════════════════════════════════
#  夹具 / 助手
# ════════════════════════════════════════════════════════════

# 【曾经的 autouse 规避夹具已删除（L6 修在源头，不再需要在测试侧打补丁）】
# 这里原本有一个 `_clear_known_cache`：每个用例前后显式
# `invalidate_known_skills_cache()`，用来绕开「known 的 memo 不跟着输入走」——
# 先跑的用例把技能目录指向 tmp_path 后，那份集合会留在进程里，后面基于真实目录的
# 断言就拿到旧值（单独跑绿、连跑红）。现在 memo 自带**输入快照指纹**
# （`agent/lines/skillpack.py::_known_fingerprint`），换目录/换数据源会自动失效，
# 规避夹具成了多余的一层（留着一个"不安 invalida 就红"的夹具，反而会把那条已经
# 修好的缺陷描述成仍然存在）。守门用例见 `TestKnownCacheFollowsInputs`。
# 契约 `invalidate_known_skills_cache()` 仍然保留，且仍在下面被用例验证。


def _profile(**kw) -> LineProfile:
    base = dict(id="t", name="t", skills=[])
    base.update(kw)
    return LineProfile(**base)


def _write_line_raw(profile: LineProfile) -> None:
    """绕过 registry.save 直接把档案写进生效目录

    为什么要绕过：save 会走 validate(known_skills=…) —— 它**正好**会拦住 typo。
    而这里要验的是「运行时遇到历史脏数据（档案里已经躺着一个写错的 id）时的表现」：
    不注入、如实报告、绝不抛。这种档案在数据校验接上之前就已经存在了。
    """
    from agent.lines.registry import lines_dir
    os.makedirs(lines_dir(), exist_ok=True)
    with open(os.path.join(lines_dir(), f"{profile.id}.yaml"), "w", encoding="utf-8") as f:
        f.write(profile.to_yaml())


@pytest.fixture
def force_enabled(monkeypatch):
    """把「全局启用集合」钉住（**环境输入**，不是被测逻辑）

    _build_skill_instructions 的被测部分 = 「本线技能包 ∩ 全局启用 ∩ _SKILL_PROMPTS」。
    这里只钉住中间那一项，两侧都走真实实现。
    """
    from agent.skills_mgmt.registry import SkillRegistry

    def _set(ids):
        monkeypatch.setattr(SkillRegistry, "list_enabled_ids", lambda self: list(ids))
    return _set


@pytest.fixture
def tmp_lines(tmp_path, monkeypatch):
    """把主线档案目录指到临时目录（真实档案读写，不 mock 注册表）"""
    monkeypatch.setenv("YUNSHU_AGENT_LINES_DIR", str(tmp_path))
    return get_line_registry()


# ════════════════════════════════════════════════════════════
#  1. 策略顺序（未装线 / 空声明 / 白名单）
# ════════════════════════════════════════════════════════════

class TestResolveSkillPackOrder:

    def test_未装线时不限制(self):
        """profile is None = 未装线 / 档案缺失 / 停用 ⇒ 不限制（保持旧行为）

        为什么必须如此：全仓接线纪律是「不装线 = 等于没改过」。技能侧若在这里
        fail-closed，不装线的用户会遇到「技能段凭空消失」。
        """
        pack = resolve_skill_pack(None, known={"a", "b"})
        assert pack.mode == "unrestricted"
        assert pack.source == "no-line"
        assert pack.allowed == () and pack.requested == ()
        assert pack.allows("任意技能") is True
        assert pack.filter(["x", "y"]) == ["x", "y"]

    def test_空声明的档案同样不限制(self):
        """空声明 = 未表态（≠ 表态为「空集」）⇒ 不限制

        为什么这条是本任务的核心判断：7 条主线当前没有一条声明 persona 指令型
        技能 id。照抄工具侧的 fail-closed 会让「装线」瞬间清空技能段，且是静默的。
        """
        pack = resolve_skill_pack(_profile(skills=[]), known={"a"})
        assert pack.mode == "unrestricted"
        assert pack.source == "empty-declaration"
        assert pack.allows("a") is True

    @pytest.mark.parametrize("blank", [[""], ["   "], ["", "  "]])
    def test_只有空白串的声明也算空(self, blank):
        pack = resolve_skill_pack(_profile(skills=blank), known={"a"})
        assert pack.mode == "unrestricted", f"空白串不是一次技能声明：{blank!r}"
        assert pack.source == "empty-declaration"

    def test_非空声明进白名单且保序去重(self):
        """allowed 必须**保序**（注入顺序可复现）并去掉重复项"""
        pack = resolve_skill_pack(
            _profile(skills=["b", "a", "b", "c"]), known={"a", "b", "c", "d"})
        assert pack.mode == "whitelist"
        assert pack.source == "whitelist"
        assert list(pack.allowed) == ["b", "a", "c"], "去重或排序被破坏了"
        assert list(pack.requested) == ["b", "a", "c"]
        assert pack.unknown == ()
        assert pack.line_id == "t"

    def test_known_里的技能不在声明里就不给(self):
        """白名单是**双向**的：没声明的即使全局启用也不注入"""
        pack = resolve_skill_pack(_profile(skills=["a"]), known={"a", "b"})
        assert pack.allows("a") is True
        assert pack.allows("b") is False
        assert pack.filter(["b", "a", "c"]) == ["a"], "filter 必须保序且只留 allowed"


# ════════════════════════════════════════════════════════════
#  2. unknown：如实报告，不进 allowed
# ════════════════════════════════════════════════════════════

class TestUnknownSkills:

    def test_未知技能被排除且如实出现在_unknown(self):
        """写错的技能 id 不许静默失效 —— 不注入，但必须在 unknown 里露面"""
        pack = resolve_skill_pack(
            _profile(skills=["a", "totally-unknown", "b"]), known={"a", "b"})
        assert list(pack.allowed) == ["a", "b"]
        assert list(pack.unknown) == ["totally-unknown"]
        assert pack.allows("totally-unknown") is False
        pack2 = resolve_skill_pack(_profile(skills=["z1", "a", "z2"]), known={"a"})
        assert list(pack2.unknown) == ["z1", "z2"], "unknown 也要保序"

    def test_目录为空时全部进_unknown(self):
        """目录整体不可用 ⇒ allowed 空、unknown 全量（如实，而不是假装通过）"""
        pack = resolve_skill_pack(_profile(skills=["a", "b"]), known=set())
        assert pack.mode == "whitelist"
        assert pack.allowed == ()
        assert list(pack.unknown) == ["a", "b"]

    def test_to_dict_带人读原因(self):
        """UI 只呈现不重算：关键结论必须都在 dict 里，且带中文原因"""
        d = resolve_skill_pack(_profile(skills=["a", "bad"]), known={"a"}).to_dict()
        assert d["mode"] == "whitelist" and d["unrestricted"] is False
        assert d["allowed"] == ["a"] and d["unknown"] == ["bad"]
        assert d["source"] == "whitelist" and d["source_label"]
        d2 = resolve_skill_pack(None).to_dict()
        assert d2["unrestricted"] is True
        assert "不限制" in d2["source_label"]

    def test_技能包不可变(self):
        """frozen dataclass：判定结果是只读事实（缓存键也要哈希得动）"""
        pack: SkillPack = resolve_skill_pack(_profile(skills=["a"]), known={"a"})
        assert isinstance(hash(pack), int)
        with pytest.raises(Exception):
            pack.mode = "unrestricted"


# ════════════════════════════════════════════════════════════
#  3. known 的唯一权威（fail-soft）
# ════════════════════════════════════════════════════════════

class TestKnownSkillIds:

    def test_真实目录里含文件轨的_persona_技能(self):
        """运行时目录必须同时覆盖两条轨（只在文件轨的 persona 技能最容易漏）"""
        ids = known_skill_ids()
        assert "self_reflection" in ids, (
            "persona 指令型技能（只在 data/skills_repo/<id>/skill.md）没被算进已知目录 —— "
            "用 loader 或 store 单看一条轨就会漏掉另一条")

    def test_干净_checkout_里仍认得出指令型技能(self, monkeypatch):
        """CI 的干净 checkout 里 data/skills_mgmt.json 不存在（被 .gitignore 忽略），
        而 7 条内置主线声明的指令型技能只活在那份台账里。

        声明表（tracked）必须兜住它们：否则同一份档案在开发机与 CI 会得到**不同的**
        unknown / allowed 判定，「保存会被校验拦下」就成了环境相关的行为。
        """
        monkeypatch.setattr(SP, "_registry_skill_ids",
                            lambda: frozenset({"self_reflection"}))
        monkeypatch.setattr(SP, "_disk_skill_ids", lambda: frozenset({"self_reflection"}))
        ids = known_skill_ids()
        for sid in ("global-core-principles", "engineering-test-delivery",
                    "code-observability", "testing-anti-patterns"):
            assert sid in ids, (
                f"{sid} 在干净 checkout 里被判成未知 ⇒ 内置主线的声明会被误报")

    def test_技能目录抛异常时退回磁盘事实(self, monkeypatch):
        """目录 API 抛异常 ⇒ 退回磁盘事实，**不向上抛**"""
        from agent.skills_mgmt.registry import SkillRegistry

        def _boom(self):
            raise RuntimeError("目录炸了")

        monkeypatch.setattr(SkillRegistry, "list_skill_ids", _boom)
        ids = known_skill_ids()
        assert ids, "磁盘事实兜底失败：known 空了，白名单会静默清空全部技能"
        assert "self_reflection" in ids

    def test_磁盘兜底汇总三个数据源(self, tmp_path, monkeypatch):
        """skills_repo 目录名 ∪ skills_mgmt.json 键 ∪ skills.json 的 id"""
        repo = tmp_path / "repo"
        (repo / "repo-skill").mkdir(parents=True)
        (repo / ".index").mkdir()          # 索引目录不是技能
        (repo / "_private").mkdir()        # 下划线开头同样跳过
        mgmt = tmp_path / "skills_mgmt.json"
        mgmt.write_text(json.dumps({"main-skill": {"id": "main-skill"}}), encoding="utf-8")
        legacy = tmp_path / "skills.json"
        legacy.write_text(json.dumps({"skills": [{"id": "legacy-skill"}, {}]}), encoding="utf-8")

        monkeypatch.setattr(SP, "_registry_skill_ids", lambda: frozenset())
        monkeypatch.setattr(SP, "_declared_skill_ids", lambda: frozenset())
        monkeypatch.setattr(SP, "SKILLS_REPO_DIR", str(repo))
        monkeypatch.setattr(SP, "SKILLS_MGMT_JSON", str(mgmt))
        monkeypatch.setattr(SP, "SKILLS_LEGACY_JSON", str(legacy))
        assert known_skill_ids() == frozenset({"repo-skill", "main-skill", "legacy-skill"})

    def test_单个数据源损坏不影响其余(self, tmp_path, monkeypatch):
        """一个 JSON 坏了只丢那一个来源，不许整份兜底失败"""
        repo = tmp_path / "repo"
        (repo / "repo-skill").mkdir(parents=True)
        bad = tmp_path / "bad.json"
        bad.write_text("{ 这不是 JSON", encoding="utf-8")

        monkeypatch.setattr(SP, "_registry_skill_ids", lambda: frozenset())
        monkeypatch.setattr(SP, "_declared_skill_ids", lambda: frozenset())
        monkeypatch.setattr(SP, "SKILLS_REPO_DIR", str(repo))
        monkeypatch.setattr(SP, "SKILLS_MGMT_JSON", str(bad))
        monkeypatch.setattr(SP, "SKILLS_LEGACY_JSON", str(tmp_path / "missing.json"))
        assert known_skill_ids() == frozenset({"repo-skill"})

    def test_全部来源失败返回空集且不缓存(self, tmp_path, monkeypatch):
        """两条路都读不到 ⇒ 空集（不抛）；且**失败不缓存**，恢复后下次必须能拿到"""
        monkeypatch.setattr(SP, "_registry_skill_ids", lambda: frozenset())
        monkeypatch.setattr(SP, "_declared_skill_ids", lambda: frozenset())
        monkeypatch.setattr(SP, "SKILLS_REPO_DIR", str(tmp_path / "nope"))
        monkeypatch.setattr(SP, "SKILLS_MGMT_JSON", str(tmp_path / "nope.json"))
        monkeypatch.setattr(SP, "SKILLS_LEGACY_JSON", str(tmp_path / "nope2.json"))
        assert known_skill_ids() == frozenset()
        monkeypatch.setattr(SP, "_registry_skill_ids", lambda: frozenset({"recovered"}))
        assert known_skill_ids() == frozenset({"recovered"}), (
            "空结果被缓存了 ⇒ 目录短暂不可用会把「没有技能」钉死到进程结束")


# ════════════════════════════════════════════════════════════
#  3b. memo 跟着输入走（L6：跨用例污染）
# ════════════════════════════════════════════════════════════

class TestKnownCacheFollowsInputs:
    """known 的 memo 必须**跟着输入走**，而不是只认「算过一次」

    【为什么这一组必须存在】此前 memo 只判「算过没有」，于是先跑的用例用
    monkeypatch 把技能目录指向 tmp_path 之后，那份"临时目录里的 known"会一直留在
    进程里；后面基于**真实目录**断言的用例就拿到旧值 —— 表现为「单独跑绿、连跑红」。
    两条并行工作流都不得不在自己的测试文件里加 autouse 的
    `invalidate_known_skills_cache()` 来规避，这本身就是缺陷的可见面。

    【为什么这组用例刻意不调用 invalidate】
        要证明的正是「不需要它」：只改**输入**（目录内容 / 来源路径 / 数据源函数），
        然后断言 known 跟着变。若哪天有人把指纹退回成"只判算过没有"，本组立刻红。
    """

    def test_技能目录内容变了_known跟着变_无需手工失效(self, tmp_path, monkeypatch):
        """同一个数据源函数、同一个路径，只往技能目录里**新增一个技能目录**

        这是最贴近实操的一种"输入变了"：夹具技能是在临时目录里**造出来**的，
        造之前与造之后路径完全一样，只有目录清单不同 —— 指纹必须看得见它。
        """
        repo = tmp_path / "repo"
        (repo / "fixture-a").mkdir(parents=True)
        monkeypatch.setattr(SP, "SKILLS_REPO_DIR", str(repo))
        monkeypatch.setattr(SP, "SKILLS_MGMT_JSON", str(tmp_path / "none.json"))
        monkeypatch.setattr(SP, "SKILLS_LEGACY_JSON", str(tmp_path / "none2.json"))
        monkeypatch.setattr(SP, "_registry_skill_ids", lambda: frozenset())

        first = known_skill_ids()
        assert "fixture-a" in first and "fixture-b" not in first

        (repo / "fixture-b").mkdir()            # ← 输入变了（路径与函数都没动）
        second = known_skill_ids()
        assert "fixture-b" in second, (
            "技能目录里新增了技能，known 却还是旧值 ⇒ memo 没跟着输入走（L6 的根因）")
        assert second is not first

    def test_来源路径被换掉后known不串到上一个目录(self, tmp_path):
        """把技能目录"挂到临时目录"再**摘下来**，known 必须回到真实目录

        这正是并行工作流踩到的场景（先跑的用例挂了 tmp 目录，后跑的用例基于真实目录
        断言）。用 `monkeypatch.context()` 进出一次即可复现"上一个用例"与"下一个用例"。
        """
        repo = tmp_path / "repo"
        (repo / "fixture-only").mkdir(parents=True)
        with pytest.MonkeyPatch.context() as m:
            m.setattr(SP, "SKILLS_REPO_DIR", str(repo))
            m.setattr(SP, "SKILLS_MGMT_JSON", str(tmp_path / "none.json"))
            m.setattr(SP, "SKILLS_LEGACY_JSON", str(tmp_path / "none2.json"))
            m.setattr(SP, "_registry_skill_ids", lambda: frozenset())
            during = known_skill_ids()
            assert "fixture-only" in during
            assert "self_reflection" not in during, "临时目录生效期间不该看见真实目录的技能"

        after = known_skill_ids()                # ← 相当于"下一个用例"的第一次调用
        assert "fixture-only" not in after, (
            "上一个用例的临时技能目录留在了 memo 里 ⇒ 后面所有基于真实目录的断言都会错")
        assert "self_reflection" in after, "patch 撤销后 known 没回到真实目录"

    def test_输入没变时仍然命中_memo(self):
        """修法必须**保住缓存**（否则等于把 memo 去掉）：输入不变时返回同一个对象

        观测手段用对象身份：命中 memo 时返回的就是那份 frozenset 本身，重算必然
        产生新对象。这条同时守住"缓存更新了但指纹没跟着更新"（那会让缓存永不命中，
        单次全量解析 ~13.4ms 的代价被摊到每一次调用上）。
        """
        first = known_skill_ids()
        assert known_skill_ids() is first, "输入没变却重算 ⇒ memo 退化成了没有缓存"

    def test_显式_invalidate_仍然可用(self):
        """公开面契约不变：显式失效后即使输入没变也必须重算（值当然不变）

        保留它的理由：指纹只覆盖本模块**看得见**的输入；绕过这些输入去改别人的
        模块级常量（如 `file_store._DEFAULT_REPO_PATH`）时，显式失效仍是逃生口。
        """
        first = known_skill_ids()
        invalidate_known_skills_cache()
        again = known_skill_ids()
        assert again == first
        assert again is not first, "显式 invalidate 之后没有重算 ⇒ 契约被破坏了"


# ════════════════════════════════════════════════════════════
#  4. validate：拦住 typo / 重复 / 空 id（旧签名仍可用）
# ════════════════════════════════════════════════════════════

class TestValidateSkills:

    def test_未注册的技能被拦住且指出是哪个_id(self):
        issues = _profile(skills=["good", "typo-skill"]).validate(known_skills={"good"})
        assert issues, "写错的技能 id 没有被拦住 —— 这正是本次要修的静默失效"
        joined = " ".join(issues)
        assert "typo-skill" in joined, f"错误消息必须指出是哪个 id: {issues}"

    def test_重复与空_id被拦住(self):
        dup = _profile(skills=["a", "a"]).validate()
        assert any("重复" in i for i in dup), dup
        empty = _profile(skills=[""]).validate()
        assert any("空" in i for i in empty), empty
        blank = _profile(skills=["  "]).validate()
        assert any("空" in i for i in blank), blank

    def test_不给_known_skills_时只做结构校验(self):
        """旧签名 validate(known_tools) 必须逐字兼容（位置传参的老调用方）"""
        assert _profile(skills=["任意不存在的技能"]).validate() == []
        assert _profile(skills=["任意不存在的技能"]).validate(set()) == []
        assert _profile(id="", skills=["a"]).validate({"t"}, {"a"}) != []

    def test_内置主线的技能声明全部真实存在(self):
        """7 条内置主线必须能通过「真实目录」口径的校验（数据级守门）"""
        known = set(known_skill_ids())
        assert known, "技能目录为空，本断言会假通过"
        checked = 0
        for line_id in get_line_registry().list_ids():
            profile = get_line_registry().load(line_id)
            assert profile is not None
            issues = profile.validate(known_skills=known)
            assert not issues, f"主线 {line_id} 的技能声明有问题: {issues}"
            checked += 1
        assert checked >= 7, f"只校验了 {checked} 条主线，样本过少"

    def test_每条内置主线都必须声明全部_persona_技能(self):
        """**行为回退守门**：persona 技能段必须对每条线都还在

        Why 这条必须存在（不是形式主义）：
          `_SKILL_PROMPTS` 里的 7 个 persona 技能在接线之前是**每轮都注入**的
          （全部 known 且全部 enabled，与是哪条线无关）。技能包接上注入侧之后，
          语义变成「本线 skills 声明 ∩ 全局启用 ∩ _SKILL_PROMPTS」⇒ 只要某条线
          漏声明一个 persona id，该线的这段提示词就**静默消失**。
          接线类改动必须行为逐字一致（与工具侧"未装线 = 旧行为"同一纪律），
          所以"某条线少一个 persona 段"只能在**显式改数据 + 改本用例 + 改文档**
          时发生，不能由任何重构顺手造成。
        """
        from agent.digital_life_persona import DigitalLifePersonaMixin

        persona = set(DigitalLifePersonaMixin._SKILL_PROMPTS)
        assert len(persona) == 7, (
            f"_SKILL_PROMPTS 的成员变了（{sorted(persona)}）—— 本用例的口径要跟着重新确认")
        for line_id in get_line_registry().list_ids():
            profile = get_line_registry().load(line_id)
            assert profile is not None
            missing = sorted(persona - set(profile.skills))
            assert not missing, (
                f"主线 {line_id} 未声明 persona 技能 {missing} ⇒ 该线的这段系统提示词会"
                f"**静默消失**（接线前它是每轮都注入的）。要让某条线放弃某个 persona 段，"
                f"必须显式删 id 并同步改这条用例与 docs/主线装配指南.md §6。")


# ════════════════════════════════════════════════════════════
#  5. 接线层：line_skill_pack / describe_line（永不抛）
# ════════════════════════════════════════════════════════════

class TestLineSkillPackWiring:

    def test_未装线时返回不限制(self, tmp_lines):
        pack = line_skill_pack()
        assert pack.unrestricted is True
        assert pack.source == "no-line"

    def test_档案解析异常时回退不限制且不抛(self, monkeypatch):
        """装配故障绝不阻断对话 —— 技能侧的等价物是「退回不限制」，不是「清空技能」"""
        from agent.lines import integration as I
        from agent.lines.registry import LineRegistryError

        class _Boom:
            def get_active(self):
                return "broken"

            def load(self, line_id):
                raise LineRegistryError("坏的档案")

        monkeypatch.setattr(I, "get_line_registry", lambda: _Boom())
        pack = I.line_skill_pack()
        assert pack.unrestricted is True
        assert pack.source == "profile-error"
        assert pack.allows("anything") is True

    def test_未预期的异常也不抛(self, monkeypatch):
        from agent.lines import integration as I

        def _boom(*a, **k):
            raise RuntimeError("炸")

        monkeypatch.setattr(I, "resolve_line_id", _boom)
        pack = I.line_skill_pack()
        assert pack.unrestricted is True and pack.source == "resolve-error"

    def test_停用主线回退不限制(self, tmp_lines):
        """停用 = 本轮不装线（与 assemble_for_line 同口径：回退旧路径）"""
        tmp_lines.save(_profile(id="dead", skills=["self_reflection"], enabled=False))
        tmp_lines.set_active("dead")
        pack = line_skill_pack()
        assert pack.unrestricted is True
        assert pack.source == "line-disabled"

    def test_装线后按档案做白名单(self, tmp_lines):
        _write_line_raw(_profile(id="eng", skills=["self_reflection", "no-such"]))
        tmp_lines.set_active("eng")
        pack = line_skill_pack()
        assert pack.mode == "whitelist" and pack.line_id == "eng"
        assert list(pack.allowed) == ["self_reflection"]
        assert list(pack.unknown) == ["no-such"]

    def test_保存路径拦住_typo(self, tmp_lines):
        """registry.save 必须把**真实技能目录**传进校验（与 known_tools 同款口径）

        这是"写错 id 静默失效"的第一道闸：保存时就失败，别等运行时。
        """
        from agent.lines.registry import LineRegistryError

        with pytest.raises(LineRegistryError) as ei:
            tmp_lines.save(_profile(id="eng", skills=["no-such-skill"]))
        assert "no-such-skill" in str(ei.value), (
            f"错误消息必须指出是哪个 id: {ei.value}")
        # 合法声明照常可存（别把闸门做成"谁都存不进去"）
        tmp_lines.save(_profile(id="eng", skills=["self_reflection"]))
        assert tmp_lines.load("eng").skills == ["self_reflection"]

    def test_describe_line_带技能段(self, tmp_lines):
        """状态面板要的「这条线现在什么样」必须包含技能段（后端算，前端不重算）"""
        tmp_lines.save(_profile(id="eng", skills=["self_reflection"]))
        tmp_lines.set_active("eng")
        info = describe_line()
        assert info["active"] == "eng"
        assert info["skills"]["mode"] == "whitelist"
        assert info["skills"]["allowed"] == ["self_reflection"]
        assert info["skills"]["source_label"]
        tmp_lines.set_active(None)
        assert describe_line()["skills"]["unrestricted"] is True


# ════════════════════════════════════════════════════════════
#  6. 注入侧：只注入本线技能 / 缓存按线分键 / _loaded_skill_ids 一致
# ════════════════════════════════════════════════════════════

class TestSkillInjection:

    @staticmethod
    def _mix():
        from agent.digital_life_persona import DigitalLifePersonaMixin

        class _P(DigitalLifePersonaMixin):
            pass
        return _P()

    def test_未装线时注入全局启用的全部_persona_技能(self, tmp_lines, force_enabled):
        """未装线 ⇒ 与「接上技能包之前」完全一样：全局启用 ∩ _SKILL_PROMPTS"""
        force_enabled(["self_reflection", "voice_interaction", "memory_summary"])
        p = self._mix()
        text = p._build_skill_instructions()
        assert set(p._loaded_skill_ids) == {
            "self_reflection", "memory_summary", "voice_interaction"}
        # 顺序 = _SKILL_PROMPTS 的声明顺序（注入文本必须可复现）
        assert p._loaded_skill_ids == [
            sid for sid in p._SKILL_PROMPTS
            if sid in {"self_reflection", "memory_summary", "voice_interaction"}]
        assert "自省反思" in text and "语音交互" in text

    def test_只注入本线允许的技能(self, monkeypatch, force_enabled):
        """本线没声明的技能不注入 —— 这就是「身份减法落在注入侧」"""
        from agent.lines import integration as I

        force_enabled(["self_reflection", "voice_interaction", "memory_summary"])

        def _pack(line_id=None):
            return SkillPack(
                line_id="dl", mode="whitelist",
                requested=("voice_interaction",), allowed=("voice_interaction",),
                source="whitelist")

        monkeypatch.setattr(I, "line_skill_pack", _pack)
        p = self._mix()
        text = p._build_skill_instructions()
        assert p._loaded_skill_ids == ["voice_interaction"]
        assert "语音交互" in text
        assert "自省反思" not in text, "未声明的技能被注入了"
        assert "记忆摘要" not in text, "未声明的技能被注入了"

    def test_切线后缓存不串线(self, tmp_lines, force_enabled):
        """**核心回归**：切换主线后第一次调用不得拿到上一条线的技能段

        单槽位缓存会在这里原样返回 A 线的文本（命中即 return，连 enabled 都不再查），
        且只错这一次 —— 典型的一次性幽灵缺陷，故用真实档案 + 真实档案目录来锁。
        """
        force_enabled(["self_reflection", "voice_interaction"])
        tmp_lines.save(_profile(id="line_a", skills=["self_reflection"]))
        tmp_lines.save(_profile(id="line_b", skills=["voice_interaction"]))

        tmp_lines.set_active("line_a")
        p = self._mix()
        text_a = p._build_skill_instructions()
        assert p._loaded_skill_ids == ["self_reflection"]
        assert "自省反思" in text_a and "语音交互" not in text_a

        tmp_lines.set_active("line_b")
        text_b = p._build_skill_instructions()
        assert "语音交互" in text_b, "切到 B 线后没拿到 B 线的技能段（缓存串线）"
        assert "自省反思" not in text_b, (
            "切到 B 线后仍带着 A 线的技能段 —— 缓存没有按技能包分键")
        assert p._loaded_skill_ids == ["voice_interaction"]

        # 切回 A 线（缓存命中路径也必须回填 id 列表）
        tmp_lines.set_active("line_a")
        assert p._build_skill_instructions() == text_a
        assert p._loaded_skill_ids == ["self_reflection"], (
            "缓存命中时没有回填 _loaded_skill_ids ⇒ 幻觉校验会拿错基准")

    def test_loaded_skill_ids_等于实际注入的_id(self, tmp_lines, force_enabled):
        """orchestrator 用 _loaded_skill_ids 做幻觉校验，它必须等于「真的拼进去了」

        （被本线过滤掉的技能不许出现在这个列表里，否则模型说「我用了 X」会被误判成幻觉。）
        """
        force_enabled(["self_reflection", "memory_summary", "voice_interaction"])
        tmp_lines.save(_profile(id="only_voice", skills=["voice_interaction"]))
        tmp_lines.set_active("only_voice")
        p = self._mix()
        text = p._build_skill_instructions()
        assert p._loaded_skill_ids == ["voice_interaction"]
        for sid in p._loaded_skill_ids:
            assert p._SKILL_PROMPTS[sid] in text
        for sid, prompt in p._SKILL_PROMPTS.items():
            if sid not in p._loaded_skill_ids:
                assert prompt not in text, f"{sid} 没进列表却进了文本"

    def test_声明了不存在的技能时该线技能段为空(self, tmp_lines, force_enabled):
        """typo 的后果是「本轮不注入」，而不是「注入全部」（不限制只在空声明时）"""
        force_enabled(["self_reflection"])
        _write_line_raw(_profile(id="typo_line", skills=["self_reflectoin"]))
        tmp_lines.set_active("typo_line")
        p = self._mix()
        assert p._build_skill_instructions() == ""
        assert p._loaded_skill_ids == []

    def test_置_None_仍能清空缓存(self, tmp_lines, force_enabled):
        """兼容语义：_cached_skill_instructions = None = 清空

        （_invalidate_status_cache 与 tests/unit/test_tools_prompt_alignment.py 都依赖它。）
        """
        force_enabled(["self_reflection"])
        tmp_lines.save(_profile(id="line_l", skills=["self_reflection"]))
        tmp_lines.set_active("line_l")
        p = self._mix()
        assert "自省反思" in p._build_skill_instructions()
        p._invalidate_status_cache()
        assert p._cached_skill_instructions is None
        assert "自省反思" in p._build_skill_instructions()
        p2 = self._mix()
        p2._cached_skill_instructions = None
        assert "自省反思" in p2._build_skill_instructions()

    def test_技能包不可用时按不限制处理(self, force_enabled, monkeypatch):
        """连技能包都拿不到（import 失败等）⇒ 退回全局启用集合，绝不打断对话"""
        force_enabled(["self_reflection"])
        p = self._mix()
        monkeypatch.setattr(p, "_current_skill_pack", lambda: None)
        assert "自省反思" in p._build_skill_instructions()
        assert p._loaded_skill_ids == ["self_reflection"]


# ════════════════════════════════════════════════════════════
#  7. HTTP 面：/preview 与 /validate 带技能段（纯计算、零副作用）
# ════════════════════════════════════════════════════════════

class TestRestSurfaceSkills:

    @pytest.fixture
    def client(self):
        """只注册主线路由的最小 app（不导入 app_server：那会连带 torch）"""
        flask = pytest.importorskip("flask")
        from agent.server_routes.routes_agent_lines import register_routes
        app = flask.Flask("line_skillpack_test")
        register_routes(app)
        return app.test_client()

    def test_preview_回传技能段与未知_id(self, client):
        resp = client.post("/api/agent-lines/preview", json={
            "id": "t", "skills": ["self_reflection", "typo-skill"],
        })
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["skills"]["mode"] == "whitelist"
        assert body["skills"]["allowed"] == ["self_reflection"]
        assert body["skills"]["unknown"] == ["typo-skill"]
        assert body["skills"]["source_label"]
        assert any("typo-skill" in i for i in body["issues"]), (
            f"校验没指出写错的技能 id: {body['issues']}")

    def test_validate_与_preview_同源(self, client):
        payload = {"id": "t", "skills": []}
        prev = client.post("/api/agent-lines/preview", json=payload).get_json()
        val = client.post("/api/agent-lines/validate", json=payload).get_json()
        assert prev["skills"] == val["skills"], "两个端点必须给同一份判定"
        assert prev["skills"]["unrestricted"] is True
        assert val["valid"] is True

    def test_preview_不落盘不改激活指针(self, tmp_lines, client):
        """零副作用：preview 只算不写（与既有纪律一致）"""
        tmp_lines.save(_profile(id="stable", skills=["self_reflection"]))
        tmp_lines.set_active("stable")
        ids_before = tmp_lines.list_ids()
        client.post("/api/agent-lines/preview",
                    json={"id": "other", "skills": ["self_reflection"]})
        assert tmp_lines.get_active() == "stable"
        assert tmp_lines.list_ids() == ids_before
        assert tmp_lines.load("other") is None
