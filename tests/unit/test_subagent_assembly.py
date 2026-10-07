"""分身装配单（agent/subagent/assembly.py）单元测试

覆盖任务书要求的七条（每条的"为什么"写在对应用例的 docstring 里）：
  1. 未点名 ⇒ 只读默认集（工具集 + mode=default-readonly），且与改动前 fan_out 行为一致
  2. 点名主线 ⇒ 工具集 = 该线装配结果（去 govern、去 §5.7 机制 3 硬禁），needs_approval 一致
  3. 主线不存在 / 已停用 / 档案损坏 ⇒ 抛 LineUnavailable，**不回退成全量授权**
  4. 技能面：白名单只带本线允许的；私人记忆/人格类被收紧剔除；标签取不到 ⇒ 不收紧且如实记账
  5. 提示词：prompt_note 非空 ⇒ 进 ctx.constraints 且带来源标注、metadata.prompt_source 正确；
     为空 ⇒ constraints 逐字不变
  6. 逐任务信封：skills_granted / skills_mode / prompt_source / prompt_note_applied
  7. 幂等：同一装配单被两个任务复用 ⇒ 不重复追加约束、不串线、不改写调用方入参

【不易】不起任何真实子进程、不调任何真实 LLM：执行器经 fan_out_tools._build_executor
        注入点替换为假件；dl 是 MagicMock（只暴露 _subagent_mgr / _llm）。
【不易】不 mock 被测逻辑：主线档案用**真实 LineRegistry**（目录经 YUNSHU_AGENT_LINES_DIR
        隔离到 tmp_path）、技能包用**真实 skillpack**、提示词用**真实 roles.compose_fragments**。
        只有"技能标签来源"（外部数据）与"工具候选池"（宿主注册表）在需要时被替换。
【不易】不触碰 data/**：需要"某个技能带 persona 标签"这类当前数据里没有的组合时，
        由用例**注入标签**（monkeypatch 标签来源），而不是去改仓库数据。
"""

from __future__ import annotations

import ast
import copy
import json
import re
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from agent import tools as _tools
from agent.lines import LineProfile, LineRegistry, get_line_registry, load_tool_meta
from agent.subagent import assembly as _assembly
from agent.subagent.assembly import LineUnavailable, resolve_subagent_assembly
from agent.subagent.executor import ExecutionOutcome
from agent.subagent.lifecycle import SubagentLifecycleManager
from agent.subagent.toolset import SubAgentToolset
from agent.tools import fan_out_tools, subagent_tools

#: 一份合法任务（八要素齐全；用例在此基础上做覆盖）
VALID_TASK = {
    "line": "engineering",
    "goal": "把 docs/zh 下的 12 篇设计稿抽取为可复现步骤序列",
    "constraints": ["只读仓库，不得修改任何文件"],
    "prior_artifacts": [],
    "prohibitions": [],
    "artifact_format": "JSON Lines：每行 {name, steps[]}",
    "budget_tokens": 20000,
    "timeout_seconds": 60,
    "callback_url": "internal://fan_out",
}


def _task(**overrides) -> dict:
    """合法任务 + 覆盖项（深拷贝：用例之间不互相污染）"""
    data = copy.deepcopy(VALID_TASK)
    data.update(overrides)
    return data


def _all_yaml_tool_names() -> List[str]:
    return sorted(load_tool_meta().keys())


def _expected_tools_for_line(line_id: str) -> List[str]:
    """该主线的期望授权集（口径与 tests/unit/test_fan_out.py 同源，用于交叉验证）"""
    from agent.lines import assemble

    meta = load_tool_meta()
    profile = get_line_registry().load(line_id)
    assert profile is not None, f"主线不存在: {line_id}"
    result = assemble(profile, sorted(meta.keys()), meta=meta)
    tools = list(result.tools)
    if not profile.allow_govern:
        tools = [t for t in tools if meta[t].plane != "govern"]
    hard = set(SubAgentToolset.hard_denied(tools))
    return [t for t in tools if t not in hard]


def _resolve(line_id: str, available: Any = None):
    """按装配单入口装配（候选池缺省 = data/tool_definitions 的全量工具名）"""
    meta = load_tool_meta()
    pool = _all_yaml_tool_names() if available is None else list(available)
    return resolve_subagent_assembly(line_id, get_line_registry(), meta, pool)


# ════════════════════════════════════════════════════════════
#  临时主线档案（真实 LineRegistry，目录隔离到 tmp_path）
# ════════════════════════════════════════════════════════════


@pytest.fixture()
def temp_lines(tmp_path, monkeypatch):
    """把主线档案目录指到 tmp_path（真实 LineRegistry，不是替身）

    为什么用环境变量而不是 MagicMock(stub)：本模块要串起"档案 → 技能包 → 技能标签
    → 装配单"整条链，任一环被替身吃掉就测不到真实口径。YUNSHU_AGENT_LINES_DIR 是
    LineRegistry 自带的隔离口（agent/lines/registry.py::lines_dir），因此**全局单例
    也读得到同一份 tmp 档案**——这点很关键：装配单里的技能包走的是全局注册表。
    """
    monkeypatch.setenv("YUNSHU_AGENT_LINES_DIR", str(tmp_path))
    return tmp_path


def _save_line(**fields) -> LineProfile:
    """用真实 LineRegistry.save 落一份临时档案（走真实校验路径）"""
    fields.setdefault("plane_weights", {"perceive": 1.0, "act": 1.0})
    fields.setdefault("max_tools", 6)
    profile = LineProfile(**fields)
    LineRegistry().save(profile)
    return profile


def _write_raw_line(dir_path, line_id: str, text: str) -> None:
    """直接写档案文本（用于 LineRegistry.save 校验不出来的形态：损坏档案、未知技能）"""
    (dir_path / f"{line_id}.yaml").write_text(text, encoding="utf-8")


def _skill_instruction(skill_id: str) -> str:
    """读一条技能的**正文**（读不到返回空串）

    本文件只用它做"正文有没有被注入"的子串比对：读不到就不比对（不臆断），
    但调用方必须自检"至少读到过一条"，否则断言会假通过。
    """
    try:
        from agent.skills_mgmt.loader import SkillLoader

        row = SkillLoader().load_instruction(skill_id) or {}
    except Exception:  # noqa: BLE001 技能库读不到不是本用例的被测对象
        return ""
    return str(row.get("instruction") or "").strip()


def _untagged_ids_from_note(note: str) -> List[str]:
    """从 skills_note 里解析出"另有 N 项未收紧（取不到标签）：[...]"那段的**实际清单**

    为什么必须解析而不是简单子串判断：同一段 note 的"剔除 N 个…：[...]"里也会出现技能
    id，只做子串判断会把"被剔除的"与"未收紧的"混为一谈——而这两侧正是本文件要分开钉的。
    """
    seg = next((s for s in note.split("；") if "取不到标签" in s), "")
    assert seg, f"skills_note 没有如实写明'取不到标签': {note}"
    matched = re.search(r"(\d+) 项未收紧（取不到标签）", seg)
    assert matched, f"取不到标签那段缺少数目: {seg}"
    ids = ast.literal_eval(seg[seg.index("["): seg.rindex("]") + 1])
    assert len(ids) == int(matched.group(1)), f"note 里的数目与清单长度不符: {seg}"
    return [str(i) for i in ids]


# ════════════════════════════════════════════════════════════
#  假执行器 / dl（fan_out 侧的接线断言用）
# ════════════════════════════════════════════════════════════


class _CapturingExecutor:
    """假执行器：记录 execute_many 入参（含逐任务工厂求值结果），按契约返回结果"""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def execute_many(self, delegations, *, max_concurrency=None, tools=(),
                     authorized_capabilities=None, tools_for=None, authorized_for=None,
                     credentials_for=None, parent_trace=None):
        items = list(delegations)
        self.calls.append({
            "delegations": items,
            "max_concurrency": max_concurrency,
            "requested": {
                ctx.delegation_id: list(tools_for(ctx) if tools_for is not None else tools)
                for ctx in items
            },
            "authorized": {
                ctx.delegation_id: list(authorized_for(ctx) if authorized_for is not None
                                        else (authorized_capabilities or ()))
                for ctx in items
            },
        })
        return [ExecutionOutcome(delegation_id=ctx.delegation_id, ok=True, tier="jsonl",
                                 payload={"summary": f"完成 {ctx.delegation_id}"},
                                 artifacts=(), duration_ms=1.0)
                for ctx in items]


@pytest.fixture(autouse=True)
def _isolate_registration():
    """用例结束后注销 fan_out，不污染全局工具注册表"""
    yield
    _tools.unregister("fan_out")


# 【曾经的 autouse 规避夹具 `_fresh_known_skills` 已删除（L6 修在源头）】
# 它每个用例前后显式清空 skillpack 的 known memo，用来绕开「别的用例挂载过临时
# 技能目录 ⇒ 本文件的断言拿到旧值」。现在该 memo 自带**输入快照指纹**
# （agent/lines/skillpack.py::_known_fingerprint），换目录/换数据源会自动失效，
# 本文件的断言天然基于**当下真实目录**，不需要在测试侧再补一层。
# 守门用例见 tests/unit/test_line_skillpack.py::TestKnownCacheFollowsInputs。


@pytest.fixture(autouse=True)
def _no_real_channel(monkeypatch):
    """关掉外部 CLI 通道，避免宿主环境变量影响用例（走 LLM 通道）"""
    monkeypatch.delenv("CP_SUBAGENT_AGENT_CLI", raising=False)


@pytest.fixture()
def full_pool(monkeypatch):
    """候选池 = data/tool_definitions/*.yaml 的全量工具名（宿主"什么都有"）"""
    names = _all_yaml_tool_names()
    monkeypatch.setattr(fan_out_tools, "_available_tool_names", lambda: list(names))
    return names


@pytest.fixture()
def make_tool(monkeypatch):
    """返回 (handler, executor)：真生命周期管理器 + 假执行器"""
    def _make():
        executor = _CapturingExecutor()
        dl = MagicMock()
        dl._subagent_mgr = SubagentLifecycleManager()
        dl._llm = MagicMock()
        monkeypatch.setattr(fan_out_tools, "_build_executor", lambda _llm: executor)
        fan_out_tools.register_all(dl)
        return _tools._registry["fan_out"]["handler"], executor
    return _make


# ════════════════════════════════════════════════════════════
#  1. 未点名 ⇒ 只读默认集
# ════════════════════════════════════════════════════════════


class TestDefaultReadOnly:
    def test_未点名走只读默认集(self):
        """没点名就按**最小集**给：这是 fail-closed 的那一半，不是"降级"

        期望集 = _default_subagent_tools 去掉 govern 平面、再去掉 §5.7 机制 3 硬禁项
        （与 assembly.py 的分支逐字对应；不相等的唯一可能是有条路径偷偷给多了）。
        """
        asm = _resolve("")
        meta = load_tool_meta()
        base = [t for t in subagent_tools._DEFAULT_SUBAGENT_TOOLS if t in meta]
        base = [t for t in base if meta[t].plane != "govern"]
        hard = set(SubAgentToolset.hard_denied(base))

        assert asm.mode == "default-readonly"
        assert asm.line_id == ""
        assert list(asm.tools) == [t for t in base if t not in hard]
        assert asm.tools, "只读默认集为空 ⇒ 用例失去意义"
        assert not set(asm.tools) & {"write_file", "edit", "shell_execute"}

    def test_未点名时不读全局激活指针(self, temp_lines):
        """本分支语义是"已确认未装线"，不能再回头读一次激活指针

        否则会造出"工具走只读默认集、技能面/提示词却按某条线生效"的错配
        （fan_out 已经把指针读空了）。
        """
        _save_line(id="probe_active", skills=["engineering-test-delivery"],
                   prompt_note="本线不该出现在未点名的装配单里")
        LineRegistry().set_active("probe_active")

        asm = _resolve("")
        assert asm.mode == "default-readonly" and asm.line_id == ""
        assert asm.prompt_note == "" and asm.prompt_source == ""
        assert asm.skills_mode == "unrestricted", "未点名却按某条线收紧了技能面"

    def test_与改动前的_fan_out_行为一致(self, make_tool, full_pool, monkeypatch):
        """回归锁：未装线时 fan_out 的可见集仍等于只读默认集（旧行为一字不改）"""
        monkeypatch.setattr(get_line_registry(), "get_active", lambda: None)
        handler, executor = make_tool()
        task = _task()
        task.pop("line")
        result = handler(tasks=[task])

        ctx = executor.calls[0]["delegations"][0]
        assert set(executor.calls[0]["authorized"][ctx.delegation_id]) == set(
            subagent_tools._DEFAULT_SUBAGENT_TOOLS)
        assert result["results"][0]["line"] == ""


# ════════════════════════════════════════════════════════════
#  2. 点名主线 ⇒ 按主线装配（工具 / 审批 / 投影）
# ════════════════════════════════════════════════════════════


class TestLineAssembly:
    @pytest.mark.parametrize("line_id", ["engineering", "dev", "assistant", "knowledge"])
    def test_工具集等于去治理去硬禁的装配结果(self, line_id):
        """点名主线时装配单必须与既有主线装配口径**逐项相同**（不重算、不加减）"""
        asm = _resolve(line_id)
        assert asm.mode == "line" and asm.line_id == line_id
        assert list(asm.tools) == _expected_tools_for_line(line_id)
        assert not set(asm.tools) & set(SubAgentToolset.hard_denied(_all_yaml_tool_names()))

    def test_needs_approval_与授权集一致(self):
        """需确认清单只能从**授权集内部**派生（不得列出没被授权的工具）"""
        meta = load_tool_meta()
        for line_id in ("engineering", "dev", "assistant", "digital_life", "harness"):
            asm = _resolve(line_id)
            assert list(asm.needs_approval) == [t for t in asm.tools if meta[t].needs_approval]
            assert set(asm.needs_approval) <= set(asm.tools)

    def test_显式允许治理的主线_治理工具带审批标注(self):
        """digital_life 显式 allow_govern=true：放行但必须进 needs_approval"""
        meta = load_tool_meta()
        asm = _resolve("digital_life")
        govern = {t for t in asm.tools if meta[t].plane == "govern"}
        assert govern, "口径自检：该线应放行治理工具"
        assert govern <= set(asm.needs_approval)
        assert "allow_govern" in asm.note

    def test_装配单_to_dict_字段齐全(self):
        """投影给 UI/状态面板的字段必须与 dataclass 逐项对应（少一个就是静默丢信息）"""
        asm = _resolve("engineering")
        data = asm.to_dict()
        assert set(data) == {
            "line_id", "mode", "tools", "needs_approval", "note",
            "skills", "skills_mode", "skills_note", "prompt_note", "prompt_source"}
        assert data["skills"] == list(asm.skills)
        assert data["prompt_source"] == asm.prompt_source
        assert len(asm) == len(asm.tools)


# ════════════════════════════════════════════════════════════
#  3. 主线不可用 ⇒ LineUnavailable，绝不回退全量授权
# ════════════════════════════════════════════════════════════


class TestLineUnavailable:
    def test_主线不存在抛异常(self, temp_lines):
        with pytest.raises(LineUnavailable) as exc:
            _resolve("no_such_line")
        assert "不存在" in str(exc.value)

    def test_主线已停用抛异常(self, temp_lines):
        _save_line(id="probe_disabled", enabled=False, prompt_note="停用线不该被装配")
        with pytest.raises(LineUnavailable) as exc:
            _resolve("probe_disabled")
        assert "已停用" in str(exc.value)

    def test_档案损坏抛异常(self, temp_lines):
        """损坏档案（YAML 合法但不是字典）同样按"不可用"处理，不静默降级"""
        _write_raw_line(temp_lines, "probe_broken", "- 这是一个列表\n- 不是字典\n")
        with pytest.raises(LineUnavailable) as exc:
            _resolve("probe_broken")
        assert "不可用" in str(exc.value)

    def test_三种不可用都不回退成全量授权(self, temp_lines):
        """把"点了名却装不上"当成"没点名"，等于用一个坏档案把权限放到最大

        同一份候选池下逐个点名：三个都必须抛，而不是返回任何一份装配单。
        """
        _save_line(id="probe_ok")
        _save_line(id="probe_disabled", enabled=False)
        _write_raw_line(temp_lines, "probe_broken", "- 列表\n")
        for line_id in ("no_such_line", "probe_disabled", "probe_broken"):
            with pytest.raises(LineUnavailable):
                _resolve(line_id)
        # 反向自检：同一目录下正常档案确实装得出东西（证明上面的异常不是"环境整体坏了"）
        ok = _resolve("probe_ok")
        assert ok.tools and set(ok.tools) < set(_all_yaml_tool_names())


# ════════════════════════════════════════════════════════════
#  4. 技能面（白名单 / 私人记忆收紧 / 标签取不到）
# ════════════════════════════════════════════════════════════


class TestSkills:
    # ── 【数据事实锁】以下三条是 2026-09-23 的真实数据现状（7 条主线已补进 7 个
    #    persona 技能）。它们**随数据变化**：先看 data/skills_repo/<id>/skill.md 的
    #    front matter tags 是不是被补了/清了，再决定改本清单还是改代码
    #    （见 docs/分身装配单.md §8 的局限登记）。

    #: 带 memory 标签 ⇒ 收紧（不下发给分身）
    MEMORY_TAGGED = "memory_summary"
    #: tags 为空 ⇒ 取不到标签 ⇒ 按纪律**保留**并在 skills_note 里点名
    UNTAGGED_KEPT = ("context_aware", "emotion_expression", "proactive_suggestion",
                     "safety_guard", "voice_interaction")
    #: 有标签（reflection/review/…）但不含私人语义 ⇒ 保留，且**不**进"取不到标签"清单
    TAGGED_NOT_PRIVATE = "self_reflection"

    def test_白名单模式只带本线允许的技能(self, temp_lines):
        """本线 skills 声明是**白名单**：分身拿到的技能 id ⊆ 声明（不是目录全量）"""
        from agent.lines import known_skill_ids

        _save_line(id="probe_skills",
                   skills=["global-core-principles", "engineering-test-delivery"])
        asm = _resolve("probe_skills")
        assert asm.skills_mode == "whitelist"
        assert set(asm.skills) == {"global-core-principles", "engineering-test-delivery"}
        assert set(asm.skills) < set(known_skill_ids()), "白名单没起到减法作用"

    def test_私人记忆类技能不下发给分身(self, temp_lines):
        """§5.7 机制 3：子代理不含私人记忆读写 ⇒ 本线技能包里的记忆类技能必须剔除

        memory_summary 的 front matter 标签里有 memory（真实数据），因此这条走的是
        **数据驱动**判据，不是 id 硬编码。
        """
        _save_line(id="probe_private",
                   skills=["memory_summary", "engineering-test-delivery"])
        asm = _resolve("probe_private")
        assert "memory_summary" not in asm.skills, "私人记忆类技能被下发给分身了"
        assert list(asm.skills) == ["engineering-test-delivery"]
        assert "剔除 1 个私人记忆" in asm.skills_note
        assert "memory_summary" in asm.skills_note

    def test_共享知识库技能不算私人记忆(self, temp_lines, monkeypatch):
        """标签 memory + knowledge 的技能是**共享知识库**，不是私人记忆

        边界与 agent/subagent/toolset.py::_derive_tool_operation_rules 的
        「memory 且非 knowledge」同源：把共享知识库一并剔除会掐掉 knowledge 线
        子代理的检索/入库能力——那是过度收紧，不是最小暴露。
        """
        _save_line(id="probe_kb", skills=["engineering-test-delivery"])
        monkeypatch.setattr(_assembly, "_skill_tag_index",
                            lambda: {"engineering-test-delivery": ("memory", "knowledge")})
        asm = _resolve("probe_kb")
        assert list(asm.skills) == ["engineering-test-delivery"]
        assert "个私人记忆" not in asm.skills_note, "共享知识库技能被当成私人记忆剔除了"

    def test_persona_标签同样被收紧(self, temp_lines, monkeypatch):
        """人格类判据走同一个词表（persona），不是"只认 memory 一个词"

        当前 data/skills_repo 里没有带 persona 标签的技能（**数据**现状，不是代码里
        没有这条分支），故用例注入标签，而不是去改仓库数据。
        """
        import agent.lines.skillpack as _sp

        monkeypatch.setattr(_assembly, "_skill_tag_index",
                            lambda: {"probe_persona_skill": ("persona",),
                                     "engineering-test-delivery": ()})
        real = _sp.known_skill_ids()
        monkeypatch.setattr(_sp, "known_skill_ids",
                            lambda: frozenset(set(real) | {"probe_persona_skill"}))
        _write_raw_line(temp_lines, "probe_persona", "\n".join([
            "id: probe_persona",
            "name: 人格探针线",
            "plane_weights: {perceive: 1.0}",
            "skills:",
            "  - probe_persona_skill",
            "  - engineering-test-delivery",
            "",
        ]))
        asm = _resolve("probe_persona")
        assert "probe_persona_skill" not in asm.skills
        assert "剔除 1 个" in asm.skills_note
        assert "probe_persona_skill" in asm.skills_note

    def test_标签取不到时不收紧且如实写明(self, temp_lines, monkeypatch):
        """标签取不到 ⇒ **不收紧**（不臆断），但必须在 skills_note 里如实记账

        为什么这么定：判据是"声明出来的事实"。声明取不到时默认收紧 = 静默的能力
        剥夺（使用者只看到"某技能莫名其妙不生效"）；默认不收紧至少**可审计**。
        """
        _save_line(id="probe_notags",
                   skills=["memory_summary", "engineering-test-delivery"])
        monkeypatch.setattr(_assembly, "_skill_tag_index", lambda: None)
        asm = _resolve("probe_notags")
        assert list(asm.skills) == ["memory_summary", "engineering-test-delivery"]
        assert "未收紧（取不到标签）" in asm.skills_note

    def test_单条技能标签缺失时不收紧该条并记账(self, temp_lines, monkeypatch):
        """部分缺失也算"取不到"：只对拿得到标签的那部分做判定"""
        _save_line(id="probe_partial",
                   skills=["memory_summary", "engineering-test-delivery"])
        monkeypatch.setattr(_assembly, "_skill_tag_index",
                            lambda: {"memory_summary": ("memory",)})
        asm = _resolve("probe_partial")
        assert list(asm.skills) == ["engineering-test-delivery"]
        assert "未收紧（取不到标签）" in asm.skills_note
        assert "engineering-test-delivery" in asm.skills_note

    def test_未装线时技能面不收紧但记账(self):
        """未装线 = 技能侧没有"身份"可减（skillpack 的纪律）⇒ 不收紧，如实说明"""
        asm = _resolve("")
        assert asm.skills_mode == "unrestricted"
        assert asm.skills, "未装线时技能面应等于运行时目录（去掉私人记忆类）"
        assert "未收紧" in asm.skills_note

    def test_真实标签来源里确实有记忆标签(self):
        """口径自检：数据驱动的判据必须真能从数据里取到标签

        这条红了**不要改代码**——先看 data/skills_repo/memory_summary/skill.md 的
        front matter tags 是不是被清空了（清空 = 收紧判据静默失效）。
        """
        index = _assembly._skill_tag_index()
        assert index, "技能标签来源整体取不到 ⇒ 收紧判据退化为不收紧"
        assert "memory_summary" in index, "口径自检：该技能不在标签来源里"
        assert "memory" in index["memory_summary"]

    def test_真实数据上收紧判据的两侧(self):
        """在**真实数据**上把判据的两侧同时钉住（只钉一侧会漏掉"永不收紧"的退化）

        上侧（收紧）：memory_summary 带 memory 标签 ⇒ 不下发给分身；
        下侧（不收紧）：front matter tags 为空的 persona 技能 ⇒ 保留，且 skills_note
                       里如实点名"未收紧（取不到标签）"的**就是这几个**；
        中间侧：self_reflection 有标签但不含私人语义 ⇒ 保留，且**不**算"取不到标签"。

        为什么用 engineering：它是本次补齐 persona 技能后技能面最大的一条线
        （4 个指令型 + 7 个 persona），两侧样本都齐。
        """
        profile = get_line_registry().load("engineering")
        index = _assembly._skill_tag_index()
        assert index, "技能标签来源不可用 ⇒ 本用例的前提（标签驱动）不成立"

        # ── 口径自检：这三条数据事实是本用例的依据 ──
        assert self.MEMORY_TAGGED in profile.skills, (
            f"口径自检：engineering 未声明 {self.MEMORY_TAGGED}")
        assert "memory" in (index.get(self.MEMORY_TAGGED) or ()), (
            f"{self.MEMORY_TAGGED} 的 memory 标签没了 ⇒ 收紧判据会静默失效（先查数据）")
        for sid in self.UNTAGGED_KEPT:
            assert sid in profile.skills, f"口径自检：engineering 未声明 {sid}"
            assert not index.get(sid), (
                f"口径自检：{sid} 现在有标签了 ⇒ 请更新本用例的数据事实锁（不是改判据）")

        asm = _resolve("engineering")
        assert asm.skills_mode == "whitelist"
        # 上侧：带 memory 标签的必须被剔除（即便本线确实声明了它）
        assert self.MEMORY_TAGGED not in asm.skills, "带 memory 标签的技能被下发给分身了"
        # 下侧：取不到标签的必须保留（不臆断剔除）
        assert set(self.UNTAGGED_KEPT) <= set(asm.skills), (
            "取不到标签的技能被臆断剔除了（应保留并在 note 里记账）")
        # 中间侧：有标签但不含私人语义的同样保留
        assert self.TAGGED_NOT_PRIVATE in asm.skills

        # note 必须**点名**：解析出"未收紧（取不到标签）"那段的实际清单
        untagged = _untagged_ids_from_note(asm.skills_note)
        assert set(self.UNTAGGED_KEPT) <= set(untagged), (
            f"skills_note 没有点名这些未收紧的技能: {asm.skills_note}")
        assert self.MEMORY_TAGGED not in untagged, "被剔除的技能混进了'未收紧'清单"
        assert self.TAGGED_NOT_PRIVATE not in untagged, "有标签的技能被记成'取不到标签'"
        # note 与实际结果自洽：本线声明 − 实际剔除 = 授予（未收紧的那些仍在授予集里）
        dropped = set(profile.skills) - set(asm.skills)
        assert dropped == {self.MEMORY_TAGGED}, (
            f"当前数据下预期只剔除 {self.MEMORY_TAGGED}，实际剔除了 {sorted(dropped)}："
            "若是补了/清了 front matter tags（数据变更），请更新本用例的数据事实锁；"
            "若不该剔除，查收紧判据")
        # note 与实际结果自洽：note 里说"未收紧"的那些，必须真的在授予集里
        assert set(untagged) <= set(asm.skills), "note 说未收紧，实际却没下发给分身"


# ════════════════════════════════════════════════════════════
#  5. 提示词片段（正文 + 来源）
# ════════════════════════════════════════════════════════════


class TestPromptFragment:
    def test_片段正文与来源来自角色层(self, temp_lines):
        """prompt_note 必须经 PromptFragment(role=line) + compose_fragments 合成

        来源串（line:<id>）因此由角色层产出，而不是本模块拼一个形似字符串；
        这里用真实 roles 模块再算一遍做交叉验证。
        """
        from agent.prompt_manager.roles import PromptFragment, compose_fragments

        _save_line(id="probe_prompt", prompt_note="本线先读后写，改完必须验证。")
        asm = _resolve("probe_prompt")
        expected = compose_fragments([PromptFragment(
            role="line", content="本线先读后写，改完必须验证。", source="line:probe_prompt")])
        assert asm.prompt_note == expected.text
        assert asm.prompt_source == expected.sources()[0] == "line:probe_prompt"

    def test_空片段时正文与来源都为空(self, temp_lines):
        """没有片段就没有片段：不追加任何东西、来源留空（行为与今天逐字一致）"""
        _save_line(id="probe_noprompt", prompt_note="")
        asm = _resolve("probe_noprompt")
        assert asm.prompt_note == "" and asm.prompt_source == ""

    def test_纯空白片段按空处理(self, temp_lines):
        """compose_fragments 把全空白片段丢进 dropped(reason=empty) ⇒ 同样留空"""
        _save_line(id="probe_blank", prompt_note="   \n  ")
        asm = _resolve("probe_blank")
        assert asm.prompt_note == "" and asm.prompt_source == ""

    def test_未装线时不带任何片段(self):
        asm = _resolve("")
        assert asm.prompt_note == "" and asm.prompt_source == ""


# ════════════════════════════════════════════════════════════
#  6. fan_out 接线：constraints / metadata / 信封
# ════════════════════════════════════════════════════════════


class TestFanOutWiring:
    def test_提示词片段进约束且带来源标注(self, make_tool, full_pool):
        """②约束是本工具里"应该怎么交付"的位置，片段随 task_file 物化给子代理

        进 constraints 而不是 system prompt：子代理的 system prompt 是云枢自有固定
        文本（§5.7 机制 1/2 不得拼接外来内容）。
        """
        profile = get_line_registry().load("engineering")
        handler, executor = make_tool()
        result = handler(tasks=[_task(line="engineering")])

        ctx = executor.calls[0]["delegations"][0]
        applied = [c for c in ctx.constraints if c.startswith("[主线 engineering]")]
        assert len(applied) == 1, "本线 prompt_note 没有恰好作为一条约束追加"
        assert profile.prompt_note in applied[0], "追加的约束不是档案原文"
        assert ctx.constraints[0] == VALID_TASK["constraints"][0], "原有约束被改动/换位"
        assert ctx.validate() == (), "追加约束后八要素契约被破坏"
        assert ctx.metadata["prompt_source"] == "line:engineering"

        item = result["results"][0]
        assert item["prompt_source"] == "line:engineering"
        assert item["prompt_note_applied"] is True

    def test_技能面只下发_id_不下发正文(self, make_tool, full_pool):
        """契约里只出现技能 id；技能**正文**不注入任何文本（§5.7 机制 1/2）

        本模块只下发"允许哪些 id"：授权面随契约走，正文由技能子系统按自己的
        注入纪律处理——分身不是"把技能正文塞进提示词"的旁路。

        【期望值不写死】与同一装配入口的结果对比（档案是数据，会随主线维护变化——
        2026-09-23 各线补进 7 个 persona 技能后，写死清单就变成"改数据即红"的假失败）。
        """
        profile = get_line_registry().load("engineering")
        expected = _resolve("engineering")
        handler, executor = make_tool()
        handler(tasks=[_task(line="engineering")])
        ctx = executor.calls[0]["delegations"][0]

        # ① 约束恰好两条：调用方原约束 + 本线片段（多一条就说明有正文/别的东西被塞进来）
        assert list(ctx.constraints) == [VALID_TASK["constraints"][0],
                                         f"[主线 engineering] {profile.prompt_note}"], (
            "除原约束与本线片段外还有其他内容被追加")
        # ② 授权面只下发 id，且与装配单逐项一致（id 里不可能有换行或正文）
        assert set(ctx.metadata["skills"]) == set(expected.skills)
        assert all(isinstance(s, str) and s and "\n" not in s
                   for s in ctx.metadata["skills"]), "技能面里混进了非 id 内容"
        # ③ 技能正文一个字符都没进契约：拿技能库里的真实正文逐条做子串比对
        surface = json.dumps({"constraints": list(ctx.constraints),
                              "metadata": dict(ctx.metadata)}, ensure_ascii=False)
        checked = 0
        for sid in ctx.metadata["skills"]:
            body = _skill_instruction(sid)
            if not body:
                continue
            checked += 1
            assert body not in surface, f"技能正文进了委派契约: {sid}"
        assert checked, "一条技能正文都没读到 ⇒ 本断言会假通过（先查技能库是否可读）"

    def test_信封逐任务含技能与提示词字段(self, make_tool, full_pool):
        handler, executor = make_tool()
        result = handler(tasks=[_task(line="engineering"), _task(line="digital_life")])

        first, second = result["results"]
        assert set(first["skills_granted"]) == set(_resolve("engineering").skills)
        assert set(second["skills_granted"]) == set(_resolve("digital_life").skills)
        assert set(first["skills_granted"]) != set(second["skills_granted"])
        assert first["skills_mode"] == second["skills_mode"] == "whitelist"
        assert first["prompt_source"] == "line:engineering"
        assert second["prompt_source"] == "line:digital_life"
        assert first["prompt_note_applied"] is True

        ctx0 = executor.calls[0]["delegations"][0]
        assert set(ctx0.metadata["skills"]) == set(first["skills_granted"])
        assert ctx0.metadata["skills_mode"] == first["skills_mode"]
        assert set(ctx0.metadata) >= {"authorized_capabilities", "line", "fan_out_index"}

    def test_未装线时信封为空且未应用提示词(self, make_tool, full_pool, monkeypatch):
        monkeypatch.setattr(get_line_registry(), "get_active", lambda: None)
        handler, executor = make_tool()
        task = _task()
        task.pop("line")
        task["constraints"] = ["只读仓库"]
        result = handler(tasks=[task])

        item = result["results"][0]
        assert item["line"] == ""
        assert item["skills_granted"], "未装线时技能面 = 运行时目录（未收紧）"
        assert item["skills_mode"] == "unrestricted"
        assert item["prompt_source"] == ""
        assert item["prompt_note_applied"] is False
        ctx = executor.calls[0]["delegations"][0]
        assert list(ctx.constraints) == ["只读仓库"], "未装线却追加了约束"
        assert ctx.metadata["prompt_source"] == ""
        assert ctx.metadata["skills_mode"] == "unrestricted"

    def test_提示词为空时约束逐字不变(self, make_tool, full_pool, temp_lines):
        _save_line(id="probe_quiet", prompt_note="")
        handler, executor = make_tool()
        given = ["只读仓库，不得修改任何文件", "不得访问网络"]
        handler(tasks=[_task(line="probe_quiet", constraints=list(given))])

        ctx = executor.calls[0]["delegations"][0]
        assert list(ctx.constraints) == given, "空片段却改动了 constraints"
        assert ctx.metadata["prompt_source"] == ""

    def test_同一主线两任务不重复追加也不串线(self, make_tool, full_pool):
        """同一装配单被两个任务复用：约束各追加一次；调用方入参不被改写

        "先复制再追加"是必须的：elements 里的 constraints 就是入参**同一个列表对象**，
        就地 append 会让第 2 个任务看到第 1 个追加过的内容（重复追加 + 串线），
        还会悄悄改掉调用方传进来的 tasks。
        """
        handler, executor = make_tool()
        shared = ["只读仓库"]
        tasks = [_task(line="engineering", constraints=shared),
                 _task(line="engineering", constraints=shared,
                       goal="把 README 的三段架构说明改写成表格")]
        result = handler(tasks=tasks)

        assert result["succeeded"] == 2
        assert shared == ["只读仓库"], "调用方入参的 constraints 被就地改写了"
        ctxs = executor.calls[0]["delegations"]
        assert len(ctxs) == 2
        for ctx in ctxs:
            applied = [c for c in ctx.constraints if c.startswith("[主线 engineering]")]
            assert len(applied) == 1, "同一装配单被复用后约束被追加了两次"
            assert len(ctx.constraints) == 2

    def test_不同主线不串线(self, make_tool, full_pool):
        handler, executor = make_tool()
        handler(tasks=[_task(line="engineering"), _task(line="dev")])
        eng, dev = executor.calls[0]["delegations"]
        eng_note = get_line_registry().load("engineering").prompt_note
        dev_note = get_line_registry().load("dev").prompt_note

        assert any(eng_note in c for c in eng.constraints)
        assert not any(dev_note in c for c in eng.constraints), "两条线的片段串了"
        assert any(dev_note in c for c in dev.constraints)
        assert not any(eng_note in c for c in dev.constraints)
        assert eng.metadata["prompt_source"] == "line:engineering"
        assert dev.metadata["prompt_source"] == "line:dev"

    def test_主线不可用时信封为空值(self, make_tool, full_pool):
        """主线不可用 ⇒ 该任务失败，四项如实为空/False（不是"没查"）"""
        handler, executor = make_tool()
        result = handler(tasks=[_task(line="no_such_line")])

        item = result["results"][0]
        assert item["status"] == "failed"
        assert item["error_code"] == fan_out_tools.E_FAN_OUT_LINE_UNAVAILABLE
        assert item["skills_granted"] == []
        assert item["skills_mode"] == ""
        assert item["prompt_source"] == ""
        assert item["prompt_note_applied"] is False
        assert item["tools_granted"] == []
        assert executor.calls == [], "主线不可用的任务不得被派发"

    def test_同一主线只算一次装配(self, make_tool, full_pool, monkeypatch):
        """逐任务缓存继续复用装配单对象：同一 line_id 只装配一次

        装配单里带了技能面与标签判定（要读技能目录），重复装配既费时也让
        "同一批里两个任务拿到不同结果"成为可能。
        """
        calls = {"n": 0}
        real = _assembly.resolve_subagent_assembly

        def _counting(line_id, registry, meta, available):
            calls["n"] += 1
            return real(line_id, registry, meta, available)

        monkeypatch.setattr(_assembly, "resolve_subagent_assembly", _counting)
        handler, _ = make_tool()
        handler(tasks=[_task(line="dev"), _task(line="dev"), _task(line="assistant")])
        assert calls["n"] == 2, f"同一 line_id 被反复装配（{calls['n']} 次）"
