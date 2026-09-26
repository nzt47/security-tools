# -*- coding: utf-8 -*-
"""G1-B 守卫：技能描述的**唯一事实源**不得再分叉（G-1 … G-7）。

任务卡：G1-B（描述治理实施）。裁定依据：G1-A §7.1（唯一源 = skill.md front matter）
与 §9.6（本文件要覆盖的 7 组断言）。

## 为什么需要这个文件

改造前「技能描述」在仓库里有 **10 处**存储、同一条技能最多 **4 套互不相同的文案**
（G1-A §0 C1 / §2.2 实测）。G1-B 把描述的**唯一事实源**收敛为：

    data/skills_repo/<id>/skill.md 的 front matter
        description     英文原文（检索 + 模型可见；G1-A R2：改中文会让触发句式
                        覆盖从 17/23 掉到 13/23）
        description_zh  中文展示文案（UI 专用；逐字搬运，M2 未改一字）

其余各"面"一律**向它取数**，不再自持一份：

    UI 面       yunshu-ui/.../skills.tsx       读 description_zh（回落 description）
    合并视图    registry.as_legacy_rows()      描述取文件轨优先 + description_zh
    legacy 快照 store._collect_legacy_rows()   同上（口径必须与合并视图一致）
    Capability  callability.build_manifest()   skill 条目 description = front matter
    Descriptor  agent/descriptors/backfill.py  description 取文件轨（G1-B/M8 修）

本文件守的就是这五处**不许各自再长出第二份文案**。

## 每条断言都能「故意制造分叉 ⇒ 变红」

见每个测试的 docstring「红路」。其中 G-1 的红路已在实施期实测过：把主轨描述写成
"A"、文件轨写成 "B"，改造前的实现（主轨先占位）会输出 "A"，而本文件断言输出必须
是 "B" ⇒ 退回旧实现立刻红灯。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

# ────────────────────────────────────────────────────────────
#  路径与常量
# ────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "data" / "skills_repo"
OVERLAY = ROOT / "data" / "skills_descriptions_overlay.json"
BASELINE = REPO / ".migration" / "descriptions.baseline.json"
PLUGINS_SKILLS = ROOT / "plugins" / "skills.py"

#: M2 的目标集（= 主轨与文件轨描述不同的那 15 条，全部是 pd-* 技能）。
#: **不从基线文件读**：基线是"当时的快照"，断言要守的是"现在这 15 条不许再分叉"，
#: 所以这里固化 id 集合；基线文件另行断言逐字相等。
M2_TARGET_IDS = (
    "pd-brainstorming-697b717a-skill",
    "pd-dispatching-parallel-agents-b8065ccd-skill",
    "pd-executing-plans-95cbf64a-skill",
    "pd-finishing-a-development-branch-e085de5a-skill",
    "pd-frontend-design-77ea5c4e-skill",
    "pd-receiving-code-review-8934157e-skill",
    "pd-requesting-code-review-ca5ae995-skill",
    "pd-subagent-driven-development-8c375695-skill",
    "pd-systematic-debugging-556faa20-skill",
    "pd-test-driven-development-8562c8ad-skill",
    "pd-using-git-worktrees-d516703a-skill",
    "pd-using-superpowers-3aea3fc9-skill",
    "pd-verification-before-completion-af010352-skill",
    "pd-writing-plans-f846e3a2-skill",
    "pd-writing-skills-5da20e67-skill",
)

#: 主轨独有、**刻意不纳入事实源域**的 id（G1-B0 §4.5 H-3 的裁定 + G1-C 的执行结果）。
#:
#: 【G1-C 更新 2026-09-26：7 → 2】原值是 H-3"7 条主轨独有"的全集。G1-C 按 H-3 的
#: **分类**执行了其中 5 条（迁移进 data/skills_repo/<id>/skill.md ⇒ 它们不再是"主轨
#: 独有"），并按同一裁定把另外 2 条刻意留下：
#:   · 已迁移（从本 allowlist 移除）：code-observability / engineering-test-delivery /
#:     frontend-state-sync / self-explanatory-ui / testing-anti-patterns；
#:   · 刻意保留：global-core-principles（常驻行为准则「适用于所有对话…始终生效」⇒ 不是
#:     可路由技能，H-3 裁定不进检索）、skill / 易之三义（其主轨 description 曾被错填成
#:     指令内容，G1-C 第 2 步只修了文案；按 H-3「先修字段再定」本轮不定纳入）。
#:
#: 断言强度**未放宽**：仍是「实际主轨独有集合必须**恰好**等于本 allowlist」，多了少了
#: 都红。G1-C 只是把集合从 7 缩到 2（原始设计就写明"若为它们补 skill.md，本 allowlist
#: 必须同步缩小"），不是把断言改宽或删掉。
KNOWN_MAIN_TRACK_ONLY = frozenset({
    "global-core-principles",
    "skill",
})


# ────────────────────────────────────────────────────────────
#  夹具（全部经**生产路径**取数，不自己写 YAML 解析器）
# ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def file_store():
    from agent.skills_mgmt.file_store import SkillFileStore
    return SkillFileStore()


@pytest.fixture(scope="module")
def meta_index(file_store):
    """文件轨元数据索引（= 唯一事实源的生产读路径）"""
    return file_store.load_metadata_index(refresh=True) or {}


@pytest.fixture(scope="module")
def repo_ids(meta_index):
    return frozenset(meta_index)


@pytest.fixture(scope="module")
def baseline():
    if not BASELINE.exists():
        pytest.fail("M1 基线缺失：" + str(BASELINE)
                    + "（G1-B/M1 的产物，描述治理的回滚与验收都依赖它）")
    return json.loads(BASELINE.read_text(encoding="utf-8"))


@pytest.fixture
def iso_svc(tmp_path):
    """隔离技能服务（主轨 JSON + 文件轨 repo 都在 tmp 下，绝不碰生产 data/）"""
    from agent.skills_mgmt import SkillsMgmtService
    return SkillsMgmtService(
        store_path=str(tmp_path / "skills_mgmt.json"),
        repo_path=str(tmp_path / "skills_repo"),
    )


def _seed_both_tracks(svc, sid, main_desc, file_desc, *, zh=""):
    """同一 id 在两条轨上各写一份描述（G-1 的构造器）"""
    svc.file_store.create(
        sid, {"id": sid, "name": sid, "description": file_desc,
              "description_zh": zh, "enabled": True},
        instruction="# body")
    skill = svc.creator.create_manual({
        "id": sid, "name": sid, "description": main_desc,
        "content": "# x", "content_type": "markdown"})
    svc.store.upsert(skill)
    return skill


# ────────────────────────────────────────────────────────────
#  G-1  合并视图不得引入第二份文案
# ────────────────────────────────────────────────────────────

class TestG1MergedViewSingleSource:
    """G-1：as_legacy_rows() 的 description 必须来自**文件轨**（skill.md）。

    红路（实施期已实测）：主轨写 "A"、文件轨写 "B"，把 registry.py 的合并规则
    退回"主轨先占位" ⇒ 输出变 "A" ⇒ 本类第一个测试红灯。
    """

    def test_dual_track_prefers_file_track(self, iso_svc):
        from agent.skills_mgmt.registry import SkillRegistry
        _seed_both_tracks(iso_svc, "g1-dual", "MAIN-A", "FILE-TRACK-B")
        rows = SkillRegistry(service=iso_svc).as_legacy_rows()
        row = next(r for r in rows if r["id"] == "g1-dual")
        assert row["description"] == "FILE-TRACK-B", (
            "合并视图取了主轨文案 ⇒ 描述又有两份（G1-A C2 的根因）")
        assert "description_zh" in row, "行形状缺少 description_zh 列"

    def test_escape_hatch_falls_back_to_main_track(self, iso_svc, monkeypatch):
        """逃生开关 CP_SKILL_DESC_FROM_FILE_TRACK=0 ⇒ 回退主轨（不改代码即可回滚）"""
        from agent.skills_mgmt.registry import SkillRegistry
        _seed_both_tracks(iso_svc, "g1-dual2", "MAIN-A", "FILE-TRACK-B",
                          zh="中文说明")
        monkeypatch.setenv("CP_SKILL_DESC_FROM_FILE_TRACK", "0")
        rows = SkillRegistry(service=iso_svc).as_legacy_rows()
        row = next(r for r in rows if r["id"] == "g1-dual2")
        assert row["description"] == "MAIN-A"
        # zh 仍取文件轨（界面文案不随英文回滚而丢失）
        assert row["description_zh"] == "中文说明"

    def test_file_track_branch_carries_description_zh(self, iso_svc):
        """文件轨**独有**分支（主轨未注册）也必须带 description_zh 列"""
        from agent.skills_mgmt.registry import SkillRegistry
        svc = iso_svc
        svc.file_store.create(
            "g1-file-only",
            {"id": "g1-file-only", "name": "fo", "description": "EN-ONLY",
             "description_zh": "中文说明", "enabled": True},
            instruction="# body")
        rows = SkillRegistry(service=svc).as_legacy_rows()
        row = next(r for r in rows if r["id"] == "g1-file-only")
        assert row["description"] == "EN-ONLY"
        assert row["description_zh"] == "中文说明"


# ────────────────────────────────────────────────────────────
#  G-2  overlay 不得含死键 / 不得含永不生效键
# ────────────────────────────────────────────────────────────

class TestG2OverlayNoDeadKeys:
    """G-2：overlay 的每个 key 都必须对应真实技能 id。

    红路：往 overlay 加一条 email-helper（skills_repo / skills_mgmt / skills.json
    三处均无此实体）⇒ 红灯。M6 之后 overlay 应为空对象。
    """

    def test_overlay_is_empty_object(self):
        assert OVERLAY.exists(), f"overlay 文件缺失: {OVERLAY}"
        data = json.loads(OVERLAY.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert data == {}, (
            "overlay 非空 ⇒ 描述又出现了第二份存储（G1-A C10：4 条里 3 条结构性"
            "永不生效、1 条是死键）。M6 已清空为 {}，且 M0 已冻结两条写路径。")

    def test_overlay_keys_exist_in_repo(self, repo_ids):
        data = json.loads(OVERLAY.read_text(encoding="utf-8"))
        dead = sorted(k for k in data if k not in repo_ids)
        assert dead == [], f"overlay 含死键（无 skill.md 实体）: {dead}"


# ────────────────────────────────────────────────────────────
#  G-3  _CURATED_DESCRIPTIONS 与 overlay 不得共存
# ────────────────────────────────────────────────────────────

_CURATED_RE = re.compile(r"^\s*_CURATED_DESCRIPTIONS\s*=", re.M)


class TestG3CuratedRemoved:
    """G-3：M6 之后 _CURATED_DESCRIPTIONS 必须已从代码里消失。

    红路：把该字面量加回 plugins/skills.py ⇒ 红灯（它正是 overlay 的唯一写源，
    留着就等于留着"随时重建 overlay"的开关）。
    """

    def test_no_curated_descriptions_definition(self):
        src = PLUGINS_SKILLS.read_text(encoding="utf-8")
        assert not _CURATED_RE.search(src), (
            "plugins/skills.py 里又出现了 _CURATED_DESCRIPTIONS="
            "（overlay 的写源，G1-A 风险 R11）")

    def test_overlay_and_curated_not_both_present(self):
        data = json.loads(OVERLAY.read_text(encoding="utf-8"))
        src = PLUGINS_SKILLS.read_text(encoding="utf-8")
        has_curated = bool(_CURATED_RE.search(src))
        assert not (has_curated and data), (
            "overlay 与 _CURATED_DESCRIPTIONS 同时非空 ⇒ 描述进入「删了又回来」循环")

    def test_curated_descriptions_not_referenced_in_code(self):
        """运行时代码（agent/ plugins/）不得再**使用**该符号。

        口径说明：只认"活的代码引用"（`_CURATED_DESCRIPTIONS` 后跟 `[` / `.` /
        `()` / `=` / 行尾），**不算注释与文档**里的同名字样 —— 本卡在 M6 的删除点
        留了引用它的墓碑注释（说明为什么删、删了会怎样），那是可读性资产而不是引用，
        把它算成违规会让这条守卫变成"不许解释自己"的假红灯。
        """
        live = re.compile(r"_CURATED_DESCRIPTIONS\s*(?:\[|\.|\()")
        assign = re.compile(r"^\s*_CURATED_DESCRIPTIONS\s*=", re.M)
        hits = []
        for base in ("agent", "plugins"):
            for p in (ROOT / base).rglob("*.py"):
                try:
                    txt = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:  # pragma: no cover
                    continue
                if assign.search(txt):
                    hits.append(f"{p.relative_to(ROOT)}:定义")
                for i, line in enumerate(txt.splitlines(), 1):
                    code = line.split("#", 1)[0]
                    if live.search(code):
                        hits.append(f"{p.relative_to(ROOT)}:{i}")
        assert hits == [], f"_CURATED_DESCRIPTIONS 仍被运行时代码引用: {hits}"


# ────────────────────────────────────────────────────────────
#  G-4 / G-5  persona 段与内置扩展的 id 必须 ⊆ 技能 id
# ────────────────────────────────────────────────────────────

class TestG4PersonaPrompts:
    """G-4：_SKILL_PROMPTS 的 key 必须每个都在 skills_repo 里有实体。

    红路：给 _SKILL_PROMPTS 加一个不存在的 id（monkeypatch）⇒ 红灯。
    这条守的是"改名/删目录后 persona 段**静默消失**、无任何日志"的老问题。
    """

    @staticmethod
    def _persona_cls():
        # 实测：模块里定义的是 `DigitalLifePersonaMixin`（`agent/digital_life_persona.py:34`），
        # 不是 `DigitalLifePersona` —— 后者不存在，import 会 ImportError。
        from agent.digital_life_persona import DigitalLifePersonaMixin
        return DigitalLifePersonaMixin

    @classmethod
    def _prompts(cls):
        return cls._persona_cls()._SKILL_PROMPTS

    def test_prompt_keys_subset_of_repo_ids(self, repo_ids):
        missing = sorted(k for k in self._prompts() if k not in repo_ids)
        assert missing == [], (
            f"persona 提示词引用了不存在的技能 id: {missing}"
            "（该段会被静默跳过、无日志）")

    def test_deliberate_fork_turns_red(self, repo_ids, monkeypatch):
        """红路自证：注入一个假 id ⇒ 同一断言必须变红"""
        cls = self._persona_cls()
        patched = dict(cls._SKILL_PROMPTS)
        patched["g1b-nonexistent-skill"] = "## 幻觉段"
        monkeypatch.setattr(cls, "_SKILL_PROMPTS", patched)
        missing = sorted(k for k in cls._SKILL_PROMPTS
                         if k not in repo_ids)
        assert missing == ["g1b-nonexistent-skill"], (
            "红路失效：注入假 id 后断言仍然通过 ⇒ 这条守卫是空转的")

    def test_enabled_prompt_is_actually_injected(self):
        """每个 key（在 enabled 时）必须真的产出非空提示段"""
        cls = self._persona_cls()
        prompts = dict(cls._SKILL_PROMPTS)
        persona = cls.__new__(cls)
        persona._cached_skill_instructions = None
        persona._loaded_skill_ids = []

        class _Reg:
            @staticmethod
            def list_enabled_ids():
                return list(prompts)

        import agent.skills_mgmt.registry as reg_mod
        real = reg_mod.SkillRegistry
        reg_mod.SkillRegistry = _Reg  # type: ignore[assignment]
        try:
            text = cls._build_skill_instructions(persona)
        finally:
            reg_mod.SkillRegistry = real
        for sid, prompt in prompts.items():
            assert prompt in text, f"技能 {sid} 的提示段没有被注入"


class TestG5BuiltinExtensions:
    """G-5：BUILTIN_EXTENSIONS["skill"] 的 id 必须 ⊆ skills_repo 的 id。

    红路：删掉一个技能目录（或往内置表加一个 id）⇒ 红灯。
    """

    @staticmethod
    def _builtin_ids():
        from agent.extensions.base import BUILTIN_EXTENSIONS
        return [s["id"] for s in BUILTIN_EXTENSIONS.get("skill", [])]

    def test_builtin_ids_subset_of_repo(self, repo_ids):
        missing = sorted(i for i in self._builtin_ids() if i not in repo_ids)
        assert missing == [], f"内置技能表引用了不存在的 id: {missing}"

    def test_builtin_ids_are_unique(self):
        ids = self._builtin_ids()
        assert len(ids) == len(set(ids)), "内置技能表有重复 id"

    def test_deliberate_fork_turns_red(self, repo_ids):
        """红路自证：把一个内置 id 改成不存在的值 ⇒ 同一断言必须变红"""
        probe = list(self._builtin_ids()) + ["g1b-nonexistent-builtin"]
        missing = sorted(i for i in probe if i not in repo_ids)
        assert missing == ["g1b-nonexistent-builtin"]


# ────────────────────────────────────────────────────────────
#  G-6  manifest / descriptor / 信封三面的描述必须等于 skill.md
# ────────────────────────────────────────────────────────────

class TestG6ManifestDescription:
    """G-6：build_manifest() 的 skill 条目 description 必须非空且逐字等于 skill.md。

    红路：往 data/capability_manifest.json 手工改一个字符 ⇒ 磁盘清单断言红灯
    （不补 sync_capability_manifest.py 的 _FIELD_SPEC 的 "description"，
    --check 抓不到这种漂移，本类就是那道唯一防线）。
    """

    @pytest.fixture(scope="class")
    def manifest(self):
        from agent.lines.callability import build_manifest
        return build_manifest()

    def test_manifest_skill_entries_have_description(self, manifest, repo_ids):
        skills = [e for e in manifest["entries"] if e.get("kind") == "skill"]
        assert len(skills) == len(repo_ids), (
            f"清单里的 skill 条数 {len(skills)} != skills_repo 的 {len(repo_ids)}")
        empty = sorted(e["tool_name"] for e in skills
                       if not str(e.get("description") or "").strip())
        assert empty == [], f"清单 skill 条目描述为空: {empty}"

    def test_manifest_description_equals_skill_md(self, manifest, meta_index):
        bad = []
        for e in manifest["entries"]:
            if e.get("kind") != "skill":
                continue
            sid = e["tool_name"]
            want = str((meta_index.get(sid) or {}).get("description") or "")
            if str(e.get("description") or "") != want:
                bad.append(sid)
        assert bad == [], f"清单描述与 skill.md 不一致: {bad}"

    def test_disk_manifest_description_equals_skill_md(self, meta_index):
        """磁盘上**已提交**的清单也必须一致（守住"手工改清单"）"""
        path = ROOT / "data" / "capability_manifest.json"
        assert path.exists(), f"清单缺失: {path}"
        data = json.loads(path.read_text(encoding="utf-8"))
        bad = []
        for e in data.get("entries") or []:
            if e.get("kind") != "skill":
                continue
            sid = e.get("tool_name")
            want = str((meta_index.get(sid) or {}).get("description") or "")
            if str(e.get("description") or "") != want:
                bad.append(sid)
        assert bad == [], (
            f"磁盘清单的 skill 描述与 skill.md 不一致: {bad}"
            "（清单是派生物，请重跑 scripts/sync_capability_manifest.py）")

    def test_envelope_skill_description_nonempty(self):
        """生产信封：build_registry().list_envelope() 的 skill 描述必须非空"""
        from agent.capregistry import build_registry
        env = build_registry().list_envelope()
        skills = [i for i in env["data"]["items"] if i.get("kind") == "skill"]
        assert skills, "信封里没有 skill 条目"
        empty = sorted(i.get("name") for i in skills
                       if not str(i.get("description") or "").strip())
        assert empty == [], f"信封 skill 描述为空: {empty}"

    def test_descriptor_registry_description_equals_skill_md(self, meta_index):
        """descriptor 台账（治理面）的描述也必须等于 skill.md（M8 的回填目标）"""
        path = ROOT / "data" / "descriptors.json"
        if not path.exists():
            pytest.skip("descriptor 台账不存在（本机未跑过 M8）")
        from agent.descriptors.registry import DescriptorRegistry
        reg = DescriptorRegistry(path=path, autosave=False)
        reg.load()
        bad = []
        for sid, meta in meta_index.items():
            d = reg.get("cp.skill." + sid)
            if d is None:
                bad.append(f"{sid}:MISSING")
                continue
            if str(d.capability.description or "") != str(meta.get("description") or ""):
                bad.append(sid)
        assert bad == [], f"descriptor 描述与 skill.md 不一致: {bad}"

    def test_asset_loader_prefers_file_track(self, meta_index):
        """descriptors 的资产装载器必须与合并视图同口径（描述取文件轨）。

        红路：把 load_skill_assets() 退回"主轨优先"⇒ 15 条 pd-* 变红灯。
        """
        from agent.descriptors.backfill import load_skill_assets
        assets = {a["id"]: a for a in load_skill_assets()}
        bad = [sid for sid in M2_TARGET_IDS
               if str(assets.get(sid, {}).get("description") or "")
               != str(meta_index[sid]["description"])]
        assert bad == [], f"资产装载器仍取主轨文案: {bad}"


# ────────────────────────────────────────────────────────────
#  G-7  legacy 快照必须等于合并视图
# ────────────────────────────────────────────────────────────

class TestG7LegacySnapshot:
    """G-7：legacy 快照的每一行 description 必须等于合并视图的对应行。

    红路：只改 skill.md 而不重建快照 ⇒ 红灯（这正是改造前 15 处 DIFF 的复现）。
    """

    def test_real_snapshot_matches_merged_view(self):
        path = ROOT / "data" / "skills.json"
        if not path.exists():
            pytest.skip("legacy 快照不存在（CI 环境 data/skills.json 被 gitignore）")
        from agent.skills_mgmt.registry import SkillRegistry
        rows = {r["id"]: r for r in SkillRegistry().as_legacy_rows()}
        legacy = {s["id"]: s
                  for s in json.loads(path.read_text(encoding="utf-8"))["skills"]}
        bad = [sid for sid, row in legacy.items()
               if sid in rows and str(row.get("description") or "")
               != str(rows[sid].get("description") or "")]
        assert bad == [], (
            f"legacy 快照与合并视图不一致: {bad}"
            "（快照是派生物，请重建 store.sync_to_legacy_skills_json()）")

    def test_rebuild_is_idempotent_and_matches(self, tmp_path):
        """隔离环境下重建快照，断言逐行等于合并视图（且重建幂等）"""
        from agent.skills_mgmt.store import SkillStore
        from agent.skills_mgmt.registry import SkillRegistry

        store = SkillStore(path=str(tmp_path / "skills_mgmt.json"))
        n1 = store.sync_to_legacy_skills_json()
        snap1 = (tmp_path / "skills.json").read_text(encoding="utf-8")
        n2 = store.sync_to_legacy_skills_json()
        snap2 = (tmp_path / "skills.json").read_text(encoding="utf-8")
        assert n1 == n2 and snap1 == snap2, "legacy 快照重建不幂等"
        assert n1 > 0, "隔离快照重建出 0 行"

        rows = {r["id"]: r for r in SkillRegistry(service=None).as_legacy_rows()}
        legacy = {s["id"]: s for s in json.loads(snap1)["skills"]}
        assert set(legacy) == set(rows), "快照 id 集合 != 合并视图 id 集合"
        bad = [sid for sid in legacy
               if str(legacy[sid].get("description") or "")
               != str(rows[sid].get("description") or "")]
        assert bad == [], f"重建后的快照仍与合并视图不一致: {bad}"

    def test_snapshot_mirror_is_byte_identical(self):
        """legacy 主快照与镜像副本（agent/data/skills.json）必须逐字节相同"""
        a = ROOT / "data" / "skills.json"
        b = ROOT / "agent" / "data" / "skills.json"
        if not (a.exists() and b.exists()):
            pytest.skip("两份 legacy 快照之一不存在（CI 环境被 gitignore）")
        assert a.read_bytes() == b.read_bytes(), (
            "legacy 主快照与镜像副本已分叉（store.py 一次调用应同时重建两份）")


# ────────────────────────────────────────────────────────────
#  M2 / M3 的验收断言（唯一源的落地证据）
# ────────────────────────────────────────────────────────────

class TestSingleSourcePayload:
    """把 M2（写中文）与 M3（合并不变量）的验收断言也固化下来。"""

    def test_description_zh_equals_baseline(self, meta_index, baseline):
        skills = baseline["skills"]
        bad = [sid for sid in M2_TARGET_IDS
               if str(meta_index[sid].get("description_zh") or "")
               != str(skills[sid]["s2"])]
        assert bad == [], f"description_zh 与 M1 基线不逐字相等: {bad}"

    def test_description_still_english_original(self, meta_index, baseline):
        """H-1 裁定：description 必须逐字保持英文原文（改中文会砸检索）"""
        skills = baseline["skills"]
        bad = [sid for sid in M2_TARGET_IDS
               if str(meta_index[sid].get("description") or "")
               != str(skills[sid]["s1"])]
        assert bad == [], f"description 被改动（应与 M1 基线逐字相同）: {bad}"

    def test_merged_view_description_equals_skill_md(self, meta_index, baseline):
        """M3 验收：15/15 的合并视图 description == skill.md；description_zh == 基线"""
        from agent.skills_mgmt.registry import SkillRegistry
        rows = {r["id"]: r for r in SkillRegistry().as_legacy_rows()}
        assert len(rows) == 30, f"合并视图行数应为 30，实得 {len(rows)}"
        bad_en = [sid for sid in M2_TARGET_IDS
                  if str(rows[sid]["description"])
                  != str(meta_index[sid]["description"])]
        bad_zh = [sid for sid in M2_TARGET_IDS
                  if str(rows[sid].get("description_zh") or "")
                  != str(baseline["skills"][sid]["s2"])]
        assert bad_en == [], f"合并视图 description 未取文件轨: {bad_en}"
        assert bad_zh == [], f"合并视图 description_zh 与基线不符: {bad_zh}"

    def test_main_track_only_allowlist_is_exact(self, repo_ids):
        """H-3 裁定：主轨独有集合必须**恰好**等于已知 7 条（多了少了都红）"""
        mgmt_path = ROOT / "data" / "skills_mgmt.json"
        if not mgmt_path.exists():
            pytest.skip("主轨文件不存在（CI 环境被 gitignore）")
        main_ids = set(json.loads(mgmt_path.read_text(encoding="utf-8")))
        actual = main_ids - set(repo_ids)
        assert actual == set(KNOWN_MAIN_TRACK_ONLY), (
            "主轨独有集合已变化：新增 " + str(sorted(actual - KNOWN_MAIN_TRACK_ONLY))
            + " / 消失 " + str(sorted(KNOWN_MAIN_TRACK_ONLY - actual))
            + "（若为它们补了 skill.md，请同步缩小 KNOWN_MAIN_TRACK_ONLY）")


# ────────────────────────────────────────────────────────────
#  M7  V-guard：向量文本的"是否并入 description_zh"由**同一个开关**决定
#      （【G1C-UA】取代原"永不并入"裁定，见下）
# ────────────────────────────────────────────────────────────

class TestVectorHashUnaffected:
    """【G1C-UA 取代 G1-B/M7 的 V-guard 裁定】

    **原裁定**：`_build_vector_text` 不得拼入 `description_zh`，理由是"会导致一次
    不必要的全量重编码（BGE-m3 4.25 GB）"。

    **为什么被取代**：向量腿原本是 `loader._meta_to_meta_text` 的**同形独立实现**，
    G1C-U1 给 loader 那份并入了中文、向量腿没有 ⇒ 同一个语义出现了**两套口径**。
    G1C-UA 让三条腿（TF-IDF / BM25 / 向量）共用同一份字段列表与同一个开关
    `CP_SKILL_META_INCLUDE_ZH`。本类**保留原裁定的代价诉求**并把它变成可验证的：

      · 开关**关** ⇒ 向量文本与哈希**逐字回到改前** ⇒ **不触发任何重编码**（保留回滚能力）；
      · 开关**开**（默认）⇒ 内容哈希变 ⇒ 首次启动走**一次**全量重编码
        （G1C-UA 实测：BGE-m3 28 条纯编码 46.7 s，单条 1.668 s；模型加载另计 ≈ 18 s）。

    红路：让 `_build_vector_text` **无视开关**地拼入 `description_zh`
    （= 第二个开关 / 又一个同形实现）⇒ 第一条断言红。
    """

    def _va(self, file_store):
        from agent.skills_mgmt.vector_adapter import SkillVectorAdapter
        va = SkillVectorAdapter.__new__(SkillVectorAdapter)  # 不初始化后端/模型
        va.fs = file_store
        va.body_summary_chars = 200
        return va

    def test_switch_off_restores_pre_change_vector_hash(self, meta_index, file_store,
                                                        monkeypatch):
        """开关关 ⇒ 向量文本与哈希**逐字**回到改前（不是"差不多"，是 md5 相等）"""
        import hashlib
        monkeypatch.setenv("CP_SKILL_META_INCLUDE_ZH", "0")
        va = self._va(file_store)
        bad = []
        for sid, meta in meta_index.items():
            base = {k: v for k, v in meta.items() if k != "description_zh"}
            parts = [
                base.get("name") or sid or "",
                base.get("description", ""),
                " ".join(base.get("tags", []) or []),
                base.get("category", ""),
            ]
            front = " ".join(p for p in parts if p)
            body = (va.fs.load_instruction(sid) or "")[: va.body_summary_chars]
            legacy_text = f"{front}\n{body}" if body else front
            _t, h = va._vector_text_and_hash(meta, sid)
            if h != hashlib.md5(legacy_text.encode("utf-8")).hexdigest():
                bad.append(sid)
        assert bad == [], f"开关关时向量哈希未逐字回到改前（回滚能力被破坏）: {bad}"

    def test_switch_on_changes_vector_hash_only_for_dual_track(self, meta_index, file_store,
                                                               monkeypatch):
        """开关开 ⇒ 只有带 `description_zh` 的技能文本/哈希变化（其余逐字不变）"""
        va = self._va(file_store)
        monkeypatch.setenv("CP_SKILL_META_INCLUDE_ZH", "0")
        off = {sid: va._vector_text_and_hash(m, sid)[1] for sid, m in meta_index.items()}
        monkeypatch.delenv("CP_SKILL_META_INCLUDE_ZH", raising=False)
        on = {sid: va._vector_text_and_hash(m, sid)[1] for sid, m in meta_index.items()}
        changed = {sid for sid in meta_index if off[sid] != on[sid]}
        dual = {sid for sid, m in meta_index.items()
                if str(m.get("description_zh") or "").strip()}
        assert changed == dual, (
            "向量哈希的变化集合与双轨集合不符：多改 %r / 少改 %r"
            % (sorted(changed - dual), sorted(dual - changed)))


# ────────────────────────────────────────────────────────────
#  H-5  双口径：文件缺失不得被当成"通过"
# ────────────────────────────────────────────────────────────

class TestSkipIsNotFalseGreen:
    """H-5：compare_skills_legacy_vs_repo.py 的双口径。

    红路：CI 环境（无 legacy 文件）允许 PASS-SKIP **且结论必须显式可见**；
    迁移校验环境（--verify）必须非零退出。改造前两种口径都是"静默 ALL_MATCH"。
    """

    SCRIPT = ROOT / "scripts" / "compare_skills_legacy_vs_repo.py"

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(ROOT), timeout=300)

    def test_ci_mode_reports_explicit_pass_skip(self):
        out = self._run("--ci", "--legacy", str(ROOT / "data" / "__g1b_absent__.json"))
        text = out.stdout + out.stderr
        assert out.returncode == 0, text
        assert "PASS-SKIP" in text, (
            "CI 口径必须给出**显式** PASS-SKIP 结论，不能静默通过：\n" + text)

    def test_verify_mode_fails_when_legacy_missing(self):
        out = self._run("--verify", "--legacy", str(ROOT / "data" / "__g1b_absent__.json"))
        text = out.stdout + out.stderr
        assert out.returncode != 0, (
            "迁移校验口径下 legacy 缺失必须非零退出（否则又是"
            "「检查不通过被当成通过」）：\n" + text)
        assert "FAIL" in text, text

    def test_real_repo_has_zero_field_diffs(self):
        """真实仓库：字段差异必须为 0（只允许"仅旧格式 7 条"这一项已知偏差）"""
        if not (ROOT / "data" / "skills.json").exists():
            pytest.skip("legacy 快照不存在（CI 环境）")
        out = self._run("--verify")
        text = out.stdout + out.stderr
        # 注意：不能用裸 "DIFF" 判定 —— 结论行 "字段对比结果: HAS_DIFF" 里也含它，
        # 会造成恒红的假失败（实施期实测）。只认逐行的 ` DIFF` 标记。
        assert not re.search(r"\sDIFF\s*$", text, re.M), (
            "字段对比仍存在 DIFF：\n" + text[-2000:])


# ────────────────────────────────────────────────────────────
#  C3 交办的不变量：.index/cache.json 必须与 skill.md 同源
# ────────────────────────────────────────────────────────────

class TestIndexCacheSource:
    """G1-A §9.6 第 2 条：23/23 的 meta.hash 必须等于 skill.md 的 md5。

    红路：手工编辑 data/skills_repo/.index/cache.json（改任一条 hash）⇒ 红灯。
    """

    def test_cache_hash_equals_skill_md_md5(self, meta_index):
        """缓存结构（实施期实测）：

            {"cache_version": "1.0",
             "skills":   {<id>: <解析后的 front matter>},        ← 无 hash
             "meta":     {<id>: {"mtime":…, "hash": md5 原始字节}}, ← hash 在这里
             "main_track": {…}, "main_track_meta": {…}}

        故 hash 校验读 `meta`，不读 `skills`（后者只有解析结果）。
        """
        import hashlib
        cache_path = REPO / ".index" / "cache.json"
        if not cache_path.exists():
            pytest.skip("索引缓存不存在（生成物，未构建过检索缓存）")
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        meta = data.get("meta")
        if not isinstance(meta, dict) or not meta:
            pytest.skip("索引缓存无 meta 段（结构变了？由 C3 卡守）")
        bad = []
        checked = 0
        for sid, ent in meta.items():
            if not isinstance(ent, dict):
                continue
            h = ent.get("hash")
            if not h:
                continue
            md = REPO / sid / "skill.md"
            if not md.exists():
                bad.append(f"{sid}:skill.md-missing")
                continue
            checked += 1
            # 必须用 read_bytes（原始字节）算 —— Windows 换行翻译会让 hash 永不命中
            # （index_cache.py 的注释专门记过这个坑）
            real = hashlib.md5(md.read_bytes()).hexdigest()
            if str(h) != real:
                bad.append(sid)
        assert checked > 0, "索引缓存里没有任何可校验的 hash（结构变了？）"
        assert bad == [], f"索引缓存的 hash 与 skill.md 不符: {bad}"
