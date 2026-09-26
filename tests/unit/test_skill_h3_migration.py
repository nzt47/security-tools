# -*- coding: utf-8 -*-
"""G1-C 验收：H-3 的「5 条纳入 / 2 条不纳入」必须**真的落地**（含"真的可召回"）。

## 裁定（G1_DECISIONS.md · H-3，用户已确认）

7 条**主轨独有**技能（没有 skill.md 实体 ⇒ 永远不受唯一源约束、也永远不进检索）
按性质分两类：

    · 5 条**纳入**（本文件守的就是它们真的进了检索链路）：
        testing-anti-patterns / code-observability / engineering-test-delivery /
        frontend-state-sync / self-explanatory-ui
    · 2 条**不纳入**：
        global-core-principles（常驻行为准则「适用于所有对话…始终生效」，不是可路由技能）
        skill / 易之三义（主轨 description 曾被错填成指令内容 —— G1-C 第 2 步已修文案）

## 本文件守的四件事

1. **形状**：5 个 skill.md 的 front matter 键集合与既有 15 条**同款**（不自己发明字段）；
2. **逐字**：`description_zh` 逐字等于主轨原中文、正文逐字等于主轨 `content`（仅 EOL 归一）；
3. **唯一源**：迁移**没有**制造新的双描述冲突 —— 全仓 28 条的文件轨 `description`
   就是合并视图/清单/信封/legacy 快照用的那一份；且 20 条双轨技能满足
   `主轨 description == 文件轨 description_zh`（= G1-B 建立的结构，未新增第三种冲突形态）；
4. **可召回**：走**生产检索链路**（`load_metadata_index` / 倒排索引 / `SkillLoader.match` /
   `build_manifest` / `build_registry().list_envelope()`）证明它们进了索引与候选池 ——
   **不是**"我写了文件所以它应该生效"。

## 已知且如实登记的边界（见 docs/audit_skill_governance/G1C.md §7）

生产 Layer-1 的 TF-IDF **只拼 front matter 的 `description`（英文）**
（`loader._meta_to_meta_text()`），故**中文 query 对全部 28 条技能都召回弱**，
不是这 5 条特有的问题。本文件因此只用"与 description 同语言的查询"证明召回，
中文 query 的现状作为**实测对照**断言下来（避免把未修的东西写成已修）。
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "data" / "skills_repo"
MGMT = ROOT / "data" / "skills_mgmt.json"

#: H-3 裁定「纳入」的 5 条
MIGRATED = (
    "code-observability",
    "engineering-test-delivery",
    "frontend-state-sync",
    "self-explanatory-ui",
    "testing-anti-patterns",
)

#: H-3 裁定「不纳入」的 2 条（= 主轨独有集合的新契约）
NOT_MIGRATED = frozenset({"global-core-principles", "skill"})

#: 既有 23 个 skill.md **全部**都有的 10 个键（形状基线，实测 23/23）
SHAPE_KEYS = {
    "id", "name", "description", "category", "tags",
    "enabled", "status", "author", "source", "content_type",
}
#: 迁移后还应带上中文展示键（= 既有 15 条 description_zh 的形状）
SHAPE_KEYS_WITH_ZH = SHAPE_KEYS | {"description_zh"}


# ────────────────────────────────────────────────────────────
#  夹具：全部走生产入口取数
# ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def file_store():
    from agent.skills_mgmt.file_store import SkillFileStore
    return SkillFileStore()


@pytest.fixture(scope="module")
def meta_index(file_store):
    return file_store.load_metadata_index(refresh=True) or {}


@pytest.fixture(scope="module")
def main_track():
    if not MGMT.exists():
        pytest.skip("主轨 skills_mgmt.json 不存在（CI 环境被 gitignore）")
    return json.loads(MGMT.read_text(encoding="utf-8"))


def _fm(store, sid):
    """经生产解析器读 front matter（不自己写 YAML 解析）"""
    from agent.skills_mgmt.file_store import SkillMDParser
    txt = (REPO / sid / "skill.md").read_text(encoding="utf-8")
    return SkillMDParser.parse(txt)[0]


# ════════════════════════════════════════════════════════════
#  1. 形状与逐字搬运
# ════════════════════════════════════════════════════════════

class TestMigratedShapeAndVerbatim:

    def test_all_five_have_skill_md(self, meta_index):
        assert set(MIGRATED) <= set(meta_index), (
            "H-3 裁定纳入的技能没有 skill.md 实体 ⇒ 仍然不可召回")

    def test_front_matter_shape_matches_existing_corpus(self, file_store):
        """键集合必须与既有 15 条 description_zh 形态**完全相同**（不自己发明字段）"""
        bad = {}
        for sid in MIGRATED:
            got = set(_fm(file_store, sid))
            if got != SHAPE_KEYS_WITH_ZH:
                bad[sid] = {"missing": sorted(SHAPE_KEYS_WITH_ZH - got),
                            "extra": sorted(got - SHAPE_KEYS_WITH_ZH)}
        assert bad == {}, f"front matter 形状与既有语料不一致: {bad}"

    def test_description_zh_is_verbatim_main_track_text(self, file_store, main_track):
        """`description_zh` 逐字等于现有主轨文案（H-2 口径：不改一字）"""
        bad = [sid for sid in MIGRATED
               if str(_fm(file_store, sid).get("description_zh") or "")
               != str(main_track[sid]["description"])]
        assert bad == [], f"description_zh 不是主轨文案的逐字搬运: {bad}"

    def test_body_is_verbatim_main_track_content(self, file_store, main_track):
        """正文逐字等于主轨 `content`（唯一允许的差异：CRLF→LF 归一）"""
        bad = {}
        for sid in MIGRATED:
            want = str(main_track[sid]["content"]).replace("\r\n", "\n")
            _meta, body, _s, _t = file_store.read(sid)
            if body != want:
                bad[sid] = (len(body), len(want))
        assert bad == {}, f"正文与主轨 content 不一致（长度 实得/期望）: {bad}"

    def test_description_is_english_with_trigger_phrasing(self, file_store):
        """H-1 裁定：`description` 留英文，且带典型触发句式（Use when / Use throughout）"""
        bad = {}
        for sid in MIGRATED:
            d = str(_fm(file_store, sid).get("description") or "")
            if not d.startswith(("Use when", "Use throughout")):
                bad[sid] = d[:60]
            elif any("\u4e00" <= ch <= "\u9fff" for ch in d.replace("。由 1 份素材蒸馏生成", "")):
                bad[sid] = "含中文（应留英文）: " + d[:60]
        assert bad == {}, f"description 不是英文/缺触发句式: {bad}"


# ════════════════════════════════════════════════════════════
#  2. 没有制造新的双描述冲突
# ════════════════════════════════════════════════════════════

class TestNoNewDualDescriptionConflict:

    def test_merged_view_description_equals_skill_md_for_all(self, meta_index):
        """全仓 28 条：合并视图（管理页/legacy 快照的取数口）的描述 == skill.md"""
        from agent.skills_mgmt.registry import SkillRegistry
        rows = {r["id"]: r for r in SkillRegistry().as_legacy_rows()}
        bad = [sid for sid in meta_index
               if sid in rows
               and str(rows[sid].get("description") or "")
               != str(meta_index[sid].get("description") or "")]
        assert bad == [], f"合并视图与唯一源不一致: {bad}"

    def test_dual_track_invariant_main_desc_equals_file_zh(self, meta_index, main_track):
        """G1-B 建立的结构必须原样保持：双轨技能满足 `主轨 description == 文件轨 description_zh`。

        这一条是"**没有制造新的冲突形态**"的判据：迁移前 15 条 pd-* 满足它，
        迁移后 20 条（15 + 5）都满足它；若我为这 5 条另写了一份与主轨不同的中文，
        这条会立刻变红。
        """
        dual = [sid for sid in meta_index if sid in main_track]
        assert len(dual) == 20, f"双轨技能应为 20 条（15 pd-* + G1-C 的 5 条），实得 {len(dual)}"
        bad = [sid for sid in dual
               if str(main_track[sid].get("description") or "")
               != str(meta_index[sid].get("description_zh") or "")]
        assert bad == [], f"出现了第三种描述形态（主轨中文 != 文件轨中文）: {bad}"

    def test_main_track_only_set_is_exactly_two(self, meta_index, main_track):
        """H-3 的另一半：不纳入的那 2 条**必须仍然没有** skill.md（否则是偷偷纳入）"""
        actual = set(main_track) - set(meta_index)
        assert actual == set(NOT_MIGRATED), (
            f"主轨独有集合 = {sorted(actual)}，期望 {sorted(NOT_MIGRATED)}")


# ════════════════════════════════════════════════════════════
#  3. 真的可召回（生产检索链路）
# ════════════════════════════════════════════════════════════

#: 与各技能 description 同语言的查询（Layer-1 只拼英文 description，见模块 docstring）
QUERY = {
    "testing-anti-patterns": "testing anti-patterns mock behavior",
    "code-observability": "structured logs health check backend api",
    "engineering-test-delivery": "audit report automated test suite delivery",
    "frontend-state-sync": "abortcontroller race condition optimistic update",
    "self-explanatory-ui": "self explanatory interface visual hierarchy",
}


class TestActuallyRecallable:

    def test_in_model_visible_catalog(self, file_store):
        from agent.skills_mgmt.loader import SkillLoader
        ids = {m.get("id") for m in SkillLoader(file_store=file_store).list_all_metadata()}
        assert set(MIGRATED) <= ids, f"不在模型可见技能目录里: {sorted(set(MIGRATED) - ids)}"

    def test_in_inverted_index_candidate_pool(self, file_store, meta_index):
        """倒排索引（Layer-1 的候选池）里必须有它们自己的 token"""
        from agent.skills_mgmt.loader import SkillLoader
        ld = SkillLoader(file_store=file_store)
        inv = ld._get_inverted_index(dict(meta_index))
        missing = {}
        for sid in MIGRATED:
            toks = [t for t, bucket in inv.items() if sid in bucket]
            if len(toks) < 5:
                missing[sid] = len(toks)
        assert missing == {}, f"倒排索引里几乎没有它们的 token: {missing}"

    def test_tfidf_match_recalls_each_of_the_five(self, file_store, meta_index):
        """生产 Layer-1 检索（TF-IDF，**不开向量** —— 不开模型）必须召回这 5 条"""
        from agent.skills_mgmt.loader import SkillLoader
        ld = SkillLoader(file_store=file_store)
        miss = {}
        for sid, q in QUERY.items():
            res = ld.match(q, top_k=5, enabled_only=False, min_score=0.0)
            got = [m.skill_id for m in res.matches]
            if sid not in got:
                miss[sid] = got
        assert miss == {}, f"检索链路召回失败: {miss}"

    def test_recall_requires_the_file_entity(self, file_store):
        """反证：把索引里的这 5 条摘掉 ⇒ 候选池里立刻没有它们。

        这条守的是"上面那条召回断言不是空转的"：如果 tokens 来自别处（例如主轨
        注入），裁剪文件轨索引后仍会命中 ⇒ 变红。
        """
        from agent.skills_mgmt.loader import SkillLoader
        full = dict(file_store.load_metadata_index(refresh=False))
        assert set(MIGRATED) <= set(full), "前置不成立：文件轨索引里没有这 5 条"
        pruned = {k: v for k, v in full.items() if k not in MIGRATED}
        ld = SkillLoader(file_store=file_store)
        inv = ld._get_inverted_index(pruned)
        still = [sid for sid in MIGRATED
                 if any(sid in bucket for bucket in inv.values())]
        assert still == [], (
            f"裁剪文件轨索引后仍在候选池里 ⇒ 召回不是由迁移产生: {still}")

    def test_capability_manifest_and_envelope(self, meta_index):
        from agent.lines.callability import build_manifest
        from agent.capregistry import build_registry
        mf = build_manifest()
        skills = [e for e in mf["entries"] if e.get("kind") == "skill"]
        assert len(skills) == len(meta_index) == 28, (
            f"清单 skill 条数 {len(skills)} != 文件轨 {len(meta_index)}")
        byid = {e["tool_name"]: e for e in skills}
        bad = [sid for sid in MIGRATED
               if str(byid.get(sid, {}).get("description") or "")
               != str(meta_index[sid].get("description") or "")]
        assert bad == [], f"清单描述 != skill.md: {bad}"
        items = build_registry().list_envelope()["data"]["items"]
        sk = [i for i in items if i.get("kind") == "skill"]
        assert {i.get("name") for i in sk} >= set(MIGRATED)
        assert all(str(i.get("description") or "").strip() for i in sk), (
            "信封里存在描述为空的 skill 条目")

    def test_runtime_only_set_shrinks_to_the_two(self):
        """迁移的副作用面：runtime_only 标注必须正好剩下不纳入的 2 条"""
        from agent.lines.callability import build_manifest, runtime_only_skill_entries
        existing = {e["tool_name"] for e in build_manifest()["entries"]}
        got = {str(e.get("tool_name") or e.get("name"))
               for e in runtime_only_skill_entries(existing_names=existing)}
        assert got == set(NOT_MIGRATED), (
            f"runtime_only 标注 = {sorted(got)}，期望 {sorted(NOT_MIGRATED)}")


# ════════════════════════════════════════════════════════════
#  4. 第 2 步的数据质量修复（`skill` 的描述）
# ════════════════════════════════════════════════════════════

class TestSkillRecordDescriptionFixed:

    def test_is_no_longer_instruction_content(self, main_track):
        import re
        d = str(main_track["skill"]["description"])
        assert not re.search(r"(?m)^\s*\d+\.\s", d), (
            "仍是编号步骤（指令内容），不是描述")
        assert "<san_yi_analysis>" not in d, "仍把提示词片段当描述"

    def test_describes_what_it_is_and_when_to_use(self, main_track):
        d = str(main_track["skill"]["description"])
        assert 40 <= len(d) <= 400, f"描述长度不合理: {len(d)}"
        assert "不易" in d and "变易" in d and "简易" in d, (
            "没有说明这个技能是什么（易之三义）")
        assert "适用于" in d, "没有说明什么时候用（缺使用场景）"
