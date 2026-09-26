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

## 主轨来源：fixture（CI 冷启动真跑）+ real（按仓库既有约定 skip）—— 2026-09-27 CI-3

本文件守的「逐字搬运 / 双轨不变量 / 主轨独有集合 / runtime_only 集合」都要**主轨内容**，
而主轨文件 `data/skills_mgmt.json` 被 `.gitignore:224` 排除、**不在 HEAD**。照仓库既有约定
（`tests/unit/test_skill_description_single_source.py:453`、
`tests/unit/test_s3_01_handover.py:245` 同款），本文件把**同一批用例跑两个来源**：

| 来源 | 断言性质 | CI 冷启动 | 落点 |
|---|---|---|---|
| `fixture` | **机制**：判据本身成不成立、集合**恰好**等于、注入被真读到 | **真跑** | `MAIN_TRACK_FIXTURE`（冻结快照）写进 `tmp_path`，生产读路径 monkeypatch 过去 |
| `real` | **真实台账的具体内容**：这 5 条的中文/正文逐字等于**那份台账**的原文；双轨恰好 20 条 = 15 个 pd-* + 5 | **显式 skip（写明理由+本机复现方式）** | 仓库里那份运行期台账（`.gitignore:224`） |

⇒ 「机制」那一半在 CI 上**永不缺席**；「真实内容」那一半在**有台账的机器**上跑。
两条硬数字（**20 条 / 15 个 pd-***）一个字没删：fixture 来源按「真仓文件轨的 pd-* ∪ H-3 纳入的
5 条」**实读拼出**期望（依据见该用例 docstring），real 来源按真实台账判。

## 已知且如实登记的边界（见 docs/audit_skill_governance/G1C.md §7）

生产 Layer-1 的 TF-IDF **只拼 front matter 的 `description`（英文）**
（`loader._meta_to_meta_text()`），故**中文 query 对全部 28 条技能都召回弱**，
不是这 5 条特有的问题。本文件因此只用"与 description 同语言的查询"证明召回，
中文 query 的现状作为**实测对照**断言下来（避免把未修的东西写成已修）。
"""

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "data" / "skills_repo"
#: 真实主轨在生产里的落点见下方「主轨来源」一节（`MGMT`）。

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


# ────────────────────────────────────────────────────────────
#  主轨来源：fixture（CI 冷启动也真跑）+ real（按仓库既有约定 skip）
# ────────────────────────────────────────────────────────────
# 【为什么必须分两类（干净检出实测，不是推断）】
#   `data/skills_mgmt.json`（技能主轨）被 `.gitignore:224` 排除 ⇒ **不在 HEAD**，
#   而 CI 的 6 个 shard、coverage-ci、observability-ci、test.yml 跑的全是干净 checkout。
#   旧实现用 `pytest.skip("主轨不存在")` 兜底 6 处、唯独 runtime_only 那条吃硬断言 ⇒
#   干净检出实测「1 failed / 9 passed / 6 skipped」（该文件**不存在**时）或
#   「7 failed / 9 passed」（该文件**已被更早的用例创建成空对象**时）—— 同一个文件两种落点，
#   取决于同一 pytest 进程里更早的文件有没有把它建出来（CI3.md §2.5）。
# 【两类口径（照仓库既有约定）】
#   · fixture —— 断言的是**机制**：把 `MAIN_TRACK_FIXTURE`（迁移时刻的逐字快照，
#     出处/sha256 见文件末尾）写进 `tmp_path/skills_mgmt.json`，并把**生产读路径**
#     `agent.lines.callability.SKILLS_MGMT_PATH` monkeypatch 过去（绝不碰仓库 `data/`）
#     ⇒ **CI 冷启动也真跑**，这几条不变量在 CI 上永不缺席。
#   · real —— 断言的是**真实台账的具体内容**（"这 5 条的中文/正文逐字等于**那份台账**的原文"、
#     "双轨恰好 20 条 = 15 个 pd-* + 5"）：CI 冷启动那份台账没有内容 ⇒ 按既有约定
#     **显式 skip 并写明理由与本机复现方式**（`_real_main_track_or_skip`）。
#     注意判据是「**有内容**才算有」：空对象（干净检出里会被自动创建出来）与"不存在"
#     归为同一类 ⇒ skip 在两种冷启动形态下**都稳定**，不再随文件顺序漂移。
# 【断言强度】一条都没删、没放宽：数字（20 条 / 15 个 pd-*）仍在断言里，只是期望值改成从
#   「真仓文件轨的 pd-* ∪ H-3 纳入的 5 条」**实读拼出**（夹具少写一条不会静默变绿）。

#: 真实主轨台账在生产里的落点（`.gitignore:224` 排除 ⇒ **不在 HEAD**）
MGMT = ROOT / "data" / "skills_mgmt.json"

#: real 来源在 CI 冷启动下的 skip 理由（照既有约定：点明 gitignore + 本机怎么复现）
_SKIP_REAL_LEDGER = (
    "技能主轨 data/skills_mgmt.json 没有内容（CI 冷启动：该文件被 .gitignore:224 排除、不在 "
    "HEAD；干净检出上要么不存在，要么被更早的用例创建成一个空对象）。本条断言的是**这份真实"
    "台账的具体内容**，故按仓库既有约定显式 skip；同一条不变量已由本文件的 fixture 来源用例"
    "覆盖（同一批用例的另一个参数）。本机复现：在**有台账**的工作区直接跑本文件即可（该台账是"
    "运行期状态、由技能管理的写路径产生，本仓不提供重建脚本；本卡核对过的内容 sha256 = "
    "bcda9ecfbcf105b6965055418aa2ac1d5a2cbfa791ea2428520b19517af07a77，见 docs/audit_skill_governance/CI3.md §7）。"
)


def _real_main_track_or_skip():
    """真实主轨台账（**有内容**才算有）；CI 冷启动 ⇒ 显式 skip（仓库既有约定）"""
    if not MGMT.exists():
        pytest.skip(_SKIP_REAL_LEDGER)
    try:
        data = json.loads(MGMT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pytest.skip(_SKIP_REAL_LEDGER + "（文件存在但不是合法 JSON）")
    if not isinstance(data, dict) or not data:
        pytest.skip(_SKIP_REAL_LEDGER)
    return data


@pytest.fixture(params=["fixture", "real"])
def main_track_source(request, tmp_path, monkeypatch):
    """主轨来源：(主轨 dict, 主轨文件路径, 来源标签)

    · `fixture` ⇒ 冻结快照写进 `tmp_path` + 生产读路径指过去（CI 冷启动**真跑**）；
    · `real`    ⇒ 仓库里那份运行期台账（CI 冷启动**显式 skip**，理由见 `_SKIP_REAL_LEDGER`）。
    """
    if request.param == "fixture":
        path = tmp_path / "skills_mgmt.json"
        path.write_text(_MAIN_TRACK_FIXTURE_JSON, encoding="utf-8")
        from agent.lines import callability
        monkeypatch.setattr(callability, "SKILLS_MGMT_PATH", str(path))
        return MAIN_TRACK_FIXTURE, path, "fixture"
    return _real_main_track_or_skip(), MGMT, "real"


@pytest.fixture
def main_track(main_track_source):
    """主轨内容（本参数来源的那一份）"""
    return main_track_source[0]


@pytest.fixture
def main_track_file(main_track_source):
    """主轨文件路径（fixture=tmp 里的迷你台账；real=仓库里那份运行期台账）"""
    return main_track_source[1]


def _fm(store, sid):
    """经生产解析器读 front matter（不自己写 YAML 解析）"""
    from agent.skills_mgmt.file_store import SkillMDParser
    txt = (REPO / sid / "skill.md").read_text(encoding="utf-8")
    return SkillMDParser.parse(txt)[0]


def test_主轨读路径读到的就是本参数这一份(main_track_source):
    """**非空转自证**：无论哪个来源，生产读路径指的就是本用例这一份，且**真的读到了它**。

    没有这条，「主轨独有集合恰好 2 条」「runtime_only 恰好 2 条」都可能在
    "其实根本没读到主轨"时静默成立 —— 那正是本文件在干净检出上的**旧形态**
    （模式 A 6 条 skip / 模式 B 7 条 KeyError，见 CI3.md §2.5）。故直接问生产读路径要事实。
    """
    from agent.lines import callability

    track, path, source = main_track_source
    assert os.path.normcase(os.path.abspath(callability.SKILLS_MGMT_PATH)) == \
        os.path.normcase(os.path.abspath(str(path))), (
        "生产读路径没指向本用例的主轨来源(%s) ⇒ 夹具/台账没生效" % source)
    facts = callability._skill_sources(include_runtime_catalog=True)
    in_mgmt = {sid for sid, fact in facts.items() if fact.get("in_mgmt")}
    assert set(track) <= in_mgmt, (
        "主轨来源(%s)里的 id 没被生产读路径读到，缺 %s ⇒ 主轨断言会是空转的"
        % (source, sorted(set(track) - in_mgmt)))


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

        【CI-3 · 期望值不再写死 20，改为"从两端各自实读再比对"（断言未删、未放宽）】
        写死 20 的问题是：主轨换成夹具后，"20"这个数字失去了与**真仓**的锚点 ——
        夹具少写一条它也不会红。现在期望值由两个**各自可复算**的来源拼出：

          · `pd_from_repo` = 真仓文件轨里的 `pd-*` 技能（`data/skills_repo/pd-*/skill.md`，
            **git 里的产物**，不依赖任何运行期台账）—— 迁移前它们就是那 15 条双轨技能；
          · `MIGRATED`   = H-3 裁定「纳入」的 5 条（文件顶部的契约表）。

        然后断言「真仓 ∩ 夹具主轨」**恰好等于**这两者的并集：夹具多一条、少一条，
        或者有人偷偷把某条主轨技能从文件轨删掉/加进去，都会立刻变红。
        末尾仍保留 `== 20` 的字面契约（依据：15 + 5），使契约数字本身也是断言的一部分。
        """
        pd_from_repo = frozenset(sid for sid in meta_index if sid.startswith("pd-"))
        dual_set = set(meta_index) & set(main_track)
        expected_dual = set(pd_from_repo) | set(MIGRATED)
        assert dual_set == expected_dual, (
            "双轨集合与「真仓文件轨 %d 条 pd-* ∪ H-3 纳入的 %d 条」不一致："
            "夹具/文件轨多出 %r、缺少 %r"
            % (len(pd_from_repo), len(MIGRATED),
               sorted(dual_set - expected_dual), sorted(expected_dual - dual_set)))
        dual = sorted(dual_set)
        assert len(dual) == 20 == len(expected_dual), (
            "双轨技能应为 20 条（迁移前 %d 条 pd-* + H-3 纳入的 %d 条），实得 %d"
            % (len(pd_from_repo), len(MIGRATED), len(dual)))
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

    def test_runtime_only_set_shrinks_to_the_two(self, main_track_file):
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


# ════════════════════════════════════════════════════════════════════
#  夹具数据：技能主轨（`data/skills_mgmt.json`）在 G1-C/H-3 迁移时刻的**逐字快照**
#
#  · 出处：HEAD 工作区的主轨台账，sha256 = bcda9ecfbcf105b6965055418aa2ac1d5a2cbfa791ea2428520b19517af07a77
#    （22 条 = 20 条双轨技能 + 2 条主轨独有；复算方式见 docs/audit_skill_governance/CI3.md）
#  · 为什么冻结：`.gitignore:224` 排除该文件 ⇒ 不在 HEAD、干净检出上不存在（见文件头「主轨夹具」）
#  · 逐字保留：id / name / category / source / status / author / description /
#    content（5 条迁移技能 + 2 条主轨独有）/ content_type / tags / enabled / is_sensitive
#  · 归一：config_schema / output_schema 收敛为与 `test_s1_02_s3_01_fixpoint_guard._make_inputs`
#    同款的最小形状（本文件不依赖它们的细节）
#  · 15 条 pd-* 的 content 不参与本文件任何断言 ⇒ 留空以控制夹具体积
#
#  下方 `json.loads` 必须留在**文件末尾**：上面各夹具/用例都在**运行期**（模块导入
#  完成之后）才引用它，故定义顺序不影响执行。
# ════════════════════════════════════════════════════════════════════

_MAIN_TRACK_FIXTURE_JSON = r"""{
 "code-observability": {
  "author": "unknown",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "# 可观测性强制约束\r\n\r\n## 描述\r\n在生成任何功能模块代码时，遵循\"存在即可见\"原则，确保代码具备结构化日志、显性错误边界、关键埋点与健康检查能力，且不引入昂贵的第三方付费依赖。\r\n\r\n## 使用场景\r\n- 生成核心业务逻辑模块代码时。\r\n- 生成后端 API 接口时。\r\n- 涉及网络请求、数据校验等可能失败分支的代码时。\r\n- 涉及关键用户交互点（提交、筛选、支付等）的代码时。\r\n\r\n## 指令\r\n\r\n1. **结构化日志**：所有核心业务逻辑节点，必须输出 JSON 格式的 `console.log`，必须包含 `trace_id`、`module_name`、`action`、`duration_ms` 字段。\r\n2. **边界显性化**：对于可能失败的分支（如网络超时、数据校验失败），必须抛出带有明确业务错误码的 Error，而不是静默返回 `null`。\r\n3. **埋点预留**：在关键用户交互点（如提交、筛选、支付），预留 `trackEvent('event_name', {payload})` 的函数调用占位符。\r\n4. **健康检查**：如果生成的是后端 API，必须附带一个 `/health` 或 `/status` 接口，返回该模块依赖的数据库/缓存的连接状态。",
  "content_type": "markdown",
  "description": "生成功能模块代码或后端 API 时使用。遵循\"存在即可见\"原则，强制输出结构化日志、显性错误边界、埋点预留与健康检查接口，且不引入昂贵的第三方付费依赖。",
  "enabled": true,
  "id": "code-observability",
  "is_sensitive": false,
  "name": "code-observability",
  "output_schema": {},
  "source": "external_agent",
  "status": "published",
  "tags": [
   "external",
   "imported",
   "markdown"
  ]
 },
 "engineering-test-delivery": {
  "author": "workbench",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "# 工程化测试与过程管理规范\n\n## 描述\n在代码开发与生成的完整生命周期中，严格执行质量保障与过程管理规范，确保交付生产级高质量代码，并保证全流程可追溯、便于后续审查与排查。\n\n## 使用场景\n- 代码编写完成后的自测与质量验证阶段。\n- 需要生成自动化测试代码的交付场景。\n- 需要输出过程日志与审计报告的工程化交付场景。\n\n## 指令\n\n### 1. 代码输出规范\n- **代码格式**：所有代码必须使用 Markdown 代码块包裹，并注明编程语言（如 python）。\n\n注释规范：核心逻辑、复杂算法、关键配置必须包含清晰的中文/英文注释。\n异常处理：禁止输出无错误处理的裸代码，必须包含必要的 Try-Catch/异常捕获机制。\n防幻觉约束：严禁引用不存在的第三方库或虚构 API；若不确定，必须明确标注\"需人工核实\"。\n2. 代码测试规范\n全面自测机制：代码编写完成后，必须执行系统化验证，覆盖以下维度：\n功能测试（主/分支流程）\n边界测试（极端/异常输入）\n兼容性测试（多环境/设备）\n性能测试（负载/响应/资源占用）\n错误处理测试（异常捕获与友好提示）\n测试文档与修复：\n输出详细的测试用例记录（包含：测试目的、输入数据、预期输出、实际输出）。\n发现问题需立即定位修复，并执行回归测试验证。\n生产级质量标准：\n交付代码需无功能缺陷、无性能瓶颈、无安全隐患。\n测试覆盖率需达标，未覆盖部分必须提供明确的风险评估说明。\n3. 代码生成过程管理\n过程日志记录：\n在每次生成代码时，必须在代码块上方输出【生成日志摘要】。\n日志必须包含：生成时间戳、内容描述与版本、生成参数、模型配置、关键状态变化（确保在当前会话中完整记录，以便追溯）。\n自动化测试套件：\n代码生成后，必须立即输出对应的自动化测试代码，覆盖：单元测试、集成测试、功能测试、性能测试及安全测试。\n审计报告输出：\n测试完成后，整理并输出详细的审计报告，包含：日志摘要、测试结果分析、覆盖率统计、问题清单（含优先级）、修复验证结果。\n确保全流程可追溯，便于后续审查与排查。",
  "content_type": "markdown",
  "description": "在代码开发与生成的完整生命周期中，严格执行质量保障与过程管理规范，确保交付生产级高质量代码，并保证全流程可追溯、便于后续审查与排查。",
  "enabled": true,
  "id": "engineering-test-delivery",
  "is_sensitive": true,
  "name": "engineering-test-delivery",
  "output_schema": {},
  "source": "manual",
  "status": "published",
  "tags": [
   "imported",
   "external",
   "markdown",
   "指令型",
   "代码与工程"
  ]
 },
 "frontend-state-sync": {
  "author": "unknown",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "# 前后端状态绝对同步规范\r\n\r\n## 描述\r\n在生成任何涉及前后端交互、状态更新或数据展示的 Web 代码时，严格遵循\"前后端状态绝对同步\"原则，确保 UI 呈现\"所见即所得\"，彻底杜绝因网络延迟、异步时序错乱或并发操作导致的 UI 与后台数据割裂。\r\n\r\n## 使用场景\r\n- 生成列表加载、搜索联想、多 Tab 切换、表单提交等涉及异步请求的前端代码。\r\n- 采用乐观更新（Optimistic UI）的交互场景。\r\n- 输入框触发搜索/过滤的高频交互场景。\r\n- 生成 WebSocket 实时通信相关代码。\r\n- React/Vue 框架下的状态管理与副作用处理。\r\n\r\n## 指令\r\n\r\n### 1. 异步时序与竞态防御\r\n- **禁止盲目信任最后返回的请求**：在生成列表加载、搜索联想、多 Tab 切换等代码时，必须内置请求序号或版本号校验机制。只有当返回的 ID 等于当前最新请求 ID 时，才允许更新 UI。\r\n- **强制取消废弃请求**：在 React/Vue 等框架中生成异步请求代码时，必须默认使用 `AbortController`。当组件卸载、依赖项变更或用户连续触发时，自动取消前一个未完成的请求。\r\n\r\n### 2. 状态更新与回滚机制\r\n- **乐观更新必须配对回滚**：当采用 Optimistic UI 时，必须显式实现 `try/catch` 结构，并在 `catch` 块中利用闭包缓存的旧状态进行精准回滚，同时提供友好的错误提示。\r\n- **后端权威原则**：禁止在前端自行推导关键业务状态。写操作（POST/PUT/DELETE）成功后，若业务允许，优先通过重新拉取或后端推送的权威数据刷新本地状态，而非仅依赖前端数组的本地增删。\r\n\r\n### 3. 实时通信与防抖节流\r\n- **输入防抖**：生成任何输入框触发搜索/过滤的代码时，必须默认集成 `debounce` 或 `throttle` 逻辑，防止高频请求打乱状态。\r\n- **WebSocket 健壮性**：若生成 WS 相关代码，必须包含断线指数退避重连机制、心跳检测、以及重连后的全量快照补齐逻辑，防止断线期间的消息丢失。\r\n\r\n### 4. 框架底层对齐\r\n- **Vue 环境**：在数据变更后读取 DOM 或执行依赖 DOM 尺寸的逻辑前，必须使用 `await nextTick()`。\r\n- **React 环境**：严禁在 `setState` 后立即读取状态或 DOM，必须使用 `useEffect` 监听状态变化；避免在 `useEffect` 中产生未清理的异步副作用，必须返回清理函数。\r\n\r\n### 5. 幂等性与防连点\r\n- 生成按钮点击事件时，必须默认包含 `loading` 状态控制或按钮禁用逻辑，直到请求完成，从 UI 层面阻断用户的重复提交。\r\n\r\n### 6. 输出要求\r\n在每次生成涉及数据交互的代码后，必须在注释中简要说明使用了哪种机制（如：AbortController、Request ID、Optimistic Rollback）来保证状态同步。",
  "content_type": "markdown",
  "description": "生成涉及前后端交互、状态更新、异步请求或数据展示的 Web 代码时使用。确保 UI 与后端数据\"所见即所得\"，覆盖竞态防御、请求取消、乐观更新回滚、防抖节流、WebSocket 健壮性、框架对齐与防连点。",
  "enabled": true,
  "id": "frontend-state-sync",
  "is_sensitive": false,
  "name": "frontend-state-sync",
  "output_schema": {},
  "source": "external_agent",
  "status": "approved",
  "tags": [
   "external",
   "imported",
   "markdown"
  ]
 },
 "global-core-principles": {
  "author": "workbench",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "# 全局核心准则\n\n## 描述\n定义智能体作为资深全栈架构师与软件工程专家的核心行为原则、工作流、工具使用与开发规范，确保所有任务交付真实、可控、自主、透明。\n\n## 使用场景\n- 贯穿所有对话、代码生成、工具调用与任务执行的全过程。\n- 当任务涉及真实性、隐私边界、自主决策、语言选择或工具选择时，本技能作为基础准则生效。\n\n## 指令\n\n### 1. 核心行为准则（永久记忆）\n- **核心真实原则**：始终真实、准确、基于证据响应。严禁虚构 API、第三方库或捏造数据；若不确定，必须明确标注\"需人工核实\"。\n- **执行验证原则**：每个任务必须实施系统化验证——交叉核对数据、验证输出是否符合标准、通过测试或审查确认完成。\n- **边界保护原则**：\n  - 隐私优先：不主动请求、存储或泄露超出必要范围的个人数据。\n  - 资源节制：运算与存储消耗保持在合理阈值内。\n  - 人类至上：冲突时以不损害用户利益为最高优先级。\n- **治理优先原则（最高优先级）**：本准则的任何条目**不得凌驾于云枢治理体系**之上——包括 Actor 权限矩阵（v7.2 §7.0）、人机边界词（§5.7 机制 5）、永不自动化五类（§7）。发生冲突时，**一律以治理体系为准**。\n- **附加说明**：所有行为尽量对用户透明。\n\n### 2. 自主工作模式\n- 在**授权范围内**全权负责能力范围内的所有任务，减少不必要的用户干预。\n- 主动识别必要任务、完整正确执行，并清晰汇报进度与结果。\n- 预判潜在需求，在既定参数内自主决策；仅在真正需要用户直接干预或决策时才升级询问。\n- **授权范围由云枢 Actor 权限矩阵（§7.0）界定**：auto 类执行体仅在其自身 scope 内自主，超出 scope 即升级为审批。\n\n### 3. 语言偏好与开发规范\n- 使用中文进行所有对话和注释。\n- 遵循项目现有的代码风格，保持代码简洁、可读。\n- 编写必要的注释说明复杂逻辑。\n\n### 4. 工作流程\n1. 理解需求后，先规划实现方案。\n2. 按授权级别执行；**需要审批的操作（destructive、数据出域、权限变更等）必须走审批流**，不得绕过。\n3. 完成后进行基本的语法检查。\n4. 如有疑问，及时询问确认。\n\n### 5. 工具使用\n- 优先使用 Read、Edit、Glob、Grep 工具。\n- 避免不必要的 bash 命令。\n- 工具调用按授权级别执行；**destructive 与外部影响类操作必须先经审批**。\n- **破坏性操作（删除大量文件、force push、修改权限等）必须先经审批**：满足 `risk=destructive ⇒ requires_approval ∧ undo_hint ∧ compensating_action` 三件套后方可执行，**不得以\"免确认\"豁免**。\n\n### 6. 修订记录\n- **2026-09-12（Owner 裁定）**：删除原文中三处\"免确认\"类表述（工具调用免确认、代码修改免确认、以及破坏性操作免弹窗确认），统一改为**遵循云枢审批矩阵**，并新增「治理优先原则」。理由：原表述与 v7.2 §7.0 Actor 权限矩阵、§5.7 机制 5 人机边界词、§7 永不自动化五类直接冲突，会系统性绕过审批门。修订前原文保留于版本历史，可回滚。\n",
  "content_type": "markdown",
  "description": "资深软件工程专家的核心行为准则，涵盖真实透明、边界保护、自主工作、语言偏好、开发规范与工具使用。适用于所有对话、代码生成与任务执行场景，作为基础行为底线始终生效。",
  "enabled": true,
  "id": "global-core-principles",
  "is_sensitive": true,
  "name": "global-core-principles",
  "output_schema": {},
  "source": "manual",
  "status": "published",
  "tags": [
   "指令型",
   "代码与工程"
  ]
 },
 "pd-brainstorming-697b717a-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "在进行任何创造性工作（比如开发新功能、构建组件、添加功能或修改现有行为）之前，你【必须】先使用此流程。务必在动手写代码实现之前，先充分探索并明确用户的真实意图、具体需求和设计方案。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-brainstorming-697b717a-skill",
  "is_sensitive": false,
  "name": "brainstorming",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "brainstorming",
   "external",
   "distilled",
   "from_knowledge"
  ]
 },
 "pd-dispatching-parallel-agents-b8065ccd-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "适用于同时处理两个或更多独立任务的场景，这些任务无需共享状态，也没有先后顺序的依赖。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-dispatching-parallel-agents-b8065ccd-skill",
  "is_sensitive": false,
  "name": "dispatching-parallel-agents",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "parallel",
   "external",
   "dispatching",
   "from_knowledge",
   "agents",
   "distilled"
  ]
 },
 "pd-executing-plans-95cbf64a-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "当你有一份书面实施计划，准备在单独的会话中执行，并且需要设置审查节点时，就可以使用它。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-executing-plans-95cbf64a-skill",
  "is_sensitive": false,
  "name": "executing-plans",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "external",
   "plans",
   "executing",
   "from_knowledge",
   "distilled"
  ]
 },
 "pd-finishing-a-development-branch-e085de5a-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "当代码实现完毕、所有测试都通过，且你需要决定如何集成这些工作时，就可以使用它。它会为你提供合并（merge）、提交拉取请求（PR）或清理代码等结构化的选项，帮你顺利完成开发收尾。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-finishing-a-development-branch-e085de5a-skill",
  "is_sensitive": false,
  "name": "finishing-a-development-branch",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "branch",
   "external",
   "finishing",
   "development",
   "from_knowledge",
   "distilled"
  ]
 },
 "pd-frontend-design-77ea5c4e-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "打造独具特色、达到生产级标准且设计感拉满的前端界面。当用户需要构建 Web 组件、页面或应用时，请调用这项技能。生成的代码要有创意、够精致，坚决避开那种千篇一律的‘AI 味儿’。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-frontend-design-77ea5c4e-skill",
  "is_sensitive": false,
  "name": "frontend-design",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "external",
   "design",
   "frontend",
   "from_knowledge",
   "distilled"
  ]
 },
 "pd-receiving-code-review-8934157e-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "在收到代码审查（Code Review）的反馈时，在着手修改代码之前使用此方法。尤其是当反馈看起来不太清晰，或者在技术上存疑时——此时需要的是严谨的技术推敲和验证，而不是做做样子的附和或盲目照做。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-receiving-code-review-8934157e-skill",
  "is_sensitive": false,
  "name": "receiving-code-review",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "external",
   "review",
   "code",
   "receiving",
   "from_knowledge",
   "distilled"
  ]
 },
 "pd-requesting-code-review-ca5ae995-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "在完成任务、实现核心功能时，或者在合并代码前，用它来确认工作是否符合要求。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-requesting-code-review-ca5ae995-skill",
  "is_sensitive": false,
  "name": "requesting-code-review",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "external",
   "code",
   "review",
   "from_knowledge",
   "requesting",
   "distilled"
  ]
 },
 "pd-subagent-driven-development-8c375695-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "在当前会话中执行包含独立任务的实施计划时，请使用。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-subagent-driven-development-8c375695-skill",
  "is_sensitive": false,
  "name": "subagent-driven-development",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "external",
   "development",
   "from_knowledge",
   "subagent",
   "distilled",
   "driven"
  ]
 },
 "pd-systematic-debugging-556faa20-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "在提出修复方案之前，如果遇到任何 Bug、测试失败或异常行为，请先使用（该工具/方法）。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-systematic-debugging-556faa20-skill",
  "is_sensitive": false,
  "name": "systematic-debugging",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "external",
   "distilled",
   "systematic",
   "from_knowledge",
   "debugging"
  ]
 },
 "pd-test-driven-development-8562c8ad-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "在实现任何新功能或修复 Bug 时，请在编写具体实现代码之前使用。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-test-driven-development-8562c8ad-skill",
  "is_sensitive": false,
  "name": "test-driven-development",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "external",
   "test",
   "development",
   "from_knowledge",
   "distilled",
   "driven"
  ]
 },
 "pd-using-git-worktrees-d516703a-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "在开始需要与当前工作区隔离的功能开发，或者在执行实施计划之前使用。它会自动通过原生工具（或回退到 git worktree）来确保存在一个隔离的工作区。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-using-git-worktrees-d516703a-skill",
  "is_sensitive": false,
  "name": "using-git-worktrees",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "external",
   "worktrees",
   "from_knowledge",
   "using",
   "git",
   "distilled"
  ]
 },
 "pd-using-superpowers-3aea3fc9-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "在每次开启对话时使用——用于确立查找和使用技能的方式，要求在做出任何回应（包括澄清性问题）之前，都必须先调用技能工具（Skill tool）。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-using-superpowers-3aea3fc9-skill",
  "is_sensitive": false,
  "name": "using-superpowers",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "external",
   "superpowers",
   "from_knowledge",
   "using",
   "distilled"
  ]
 },
 "pd-verification-before-completion-af010352-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "在准备宣布工作已完成、问题已修复或测试已通过之前（比如在提交代码或创建 PR 之前），必须先运行验证命令并确认输出结果，绝不能凭空宣称成功。记住：先拿证据，再做断言。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-verification-before-completion-af010352-skill",
  "is_sensitive": false,
  "name": "verification-before-completion",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "external",
   "completion",
   "verification",
   "from_knowledge",
   "before",
   "distilled"
  ]
 },
 "pd-writing-plans-f846e3a2-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "在动手写代码之前，如果你手头有规格说明或多步骤任务的需求，就可以用它。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-writing-plans-f846e3a2-skill",
  "is_sensitive": false,
  "name": "writing-plans",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "external",
   "plans",
   "from_knowledge",
   "writing",
   "distilled"
  ]
 },
 "pd-writing-skills-5da20e67-skill": {
  "author": "process_distill",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "",
  "content_type": "markdown",
  "description": "适用于创建、编辑或验证 agent 技能（SKILL.md）之前或过程中，核心是将 TDD 应用于流程文档编写。预期产出: 一份经 RED-GREEN-REFACTOR 验证、无已知漏洞且可被其他 agent 正确触发和使用的 SKILL.md 文档。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "pd-writing-skills-5da20e67-skill",
  "is_sensitive": false,
  "name": "writing-skills",
  "output_schema": {},
  "source": "knowledge_distill",
  "status": "approved",
  "tags": [
   "创建技能",
   "external",
   "验证技能",
   "编辑技能",
   "from_knowledge",
   "distilled"
  ]
 },
 "self-explanatory-ui": {
  "author": "unknown",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "# 自解释 UI 设计规范\r\n\r\n## 描述\r\n记录并实施将功能说明与帮助信息直接集成到可视化界面的设计理念，确保未来所有 UI 开发工作严格遵循这一思路。开发高度自解释的用户界面，使用户无需预先学习或查阅外部文档，仅凭界面展示的信息就能理解功能用途并正确操作。\r\n\r\n## 使用场景\r\n- 进行前端 UI 组件或页面设计时。\r\n- 生成涉及用户交互的界面代码时。\r\n- 需要为新用户提供直观操作引导时。\r\n\r\n## 指令\r\n\r\n1. **直观性与一致性**：将功能说明与帮助信息直接集成到可视化界面中。通过视觉层次、图标提示、状态反馈等元素，确保界面展示的信息足以说明功能用途。\r\n2. **上下文帮助**：在用户操作的关键节点提供上下文相关的引导信息，确保用户仅凭界面展示即可正确操作，无需查阅外部文档。\r\n3. **可发现性**：确保新用户能够零学习成本地发现并完成核心任务的操作路径，关键功能的入口清晰显性。\r\n\r\n## 设计原则\r\n界面设计需遵循：直观性、一致性、可发现性。通过视觉层次、图标提示、状态反馈和上下文帮助等元素的综合运用，达成零学习成本目标。",
  "content_type": "markdown",
  "description": "进行界面设计或前端 UI 开发时使用。将功能说明与帮助信息直接集成到可视化界面中，通过视觉层次、图标提示、状态反馈和上下文帮助，实现零学习成本的自解释用户界面。",
  "enabled": true,
  "id": "self-explanatory-ui",
  "is_sensitive": false,
  "name": "self-explanatory-ui",
  "output_schema": {},
  "source": "external_agent",
  "status": "approved",
  "tags": [
   "external",
   "imported",
   "markdown"
  ]
 },
 "skill": {
  "author": "workbench",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "# Role: Yi-Jing Coding Agent\n\n## Core Philosophy (Priority: MAX)\n1. **不易(Invariance)**: 锁定业务内核/契约/安全边界为不可变量。变更前必须识别不变量。回归第一性原理。\n2. **变易(Adaptability)**: 按需演进，拒绝过度抽象。感知上下文动态权衡。大变更拆解为可回滚小步。应自动及时git提交，避免丢失。\n3. **简易(Simplicity)**: 最小充分解。显式>隐式。奥卡姆剃刀。代码须初级工程师30s可读。\n\n## Cognitive Protocol\n1. 编码前必输出 `<san_yi_analysis>`: [不易]约束识别 → [变易]扩展性评估 → [简易]最简方案确认。\n2. 原子推理，每步经三义校验。\n3. 三义冲突时显式说明权衡取舍。\n4. 生成后自检，违三义则修正再输出。\n\n## Coding Standards\n- Minimal Change: 仅改必需代码(不易)。\n- Defensive: 输入校验+错误处理(变易)。\n- Readability: 注释写Why(不易)，命名反映业务语义(简易)。\n- Test: 测试=不易护城河。新功能必加测，重构先补测。\n- Error Handling: 健壮但直白，禁嵌套地狱(变易+简易)。\n\n## Communication\n- 用【不易/变易/简易】标签沟通。\n- 需求模糊时按序追问: 不变的是什么→可能变的是什么→最简起步方案。\n- 简洁结构化，禁寒暄说教。中文保留英文技术术语，并在技术术语后面加括号标注意译中文。\n\n## Hard Constraints\n- 未阐明【不易】约束禁给重构方案。\n- 禁超需复杂度(违简易)。\n- 禁编造API/库。\n- 未经确认禁删现有代码(违不易)。\n- 禁生成难懂\"聪明代码\"(违简易)。\n- 安全操作须警告待确认(守不易)。\n- 用户要求违三义时指出风险并给替代方案。\n- 三义优先级 > 常规工程惯例。",
  "content_type": "markdown",
  "description": "以《易经》三义（不易 / 变易 / 简易）为框架的编码行为准则：先锁定业务内核、接口契约与安全边界等不变量，再评估可演进范围，最后收敛到最小充分解。适用于需要「改动最小、边界清晰、可回滚」的编码与重构任务，也适用于需求模糊时按「不变的是什么 → 可能变的是什么 → 最简起步方案」逐步澄清的场景。",
  "enabled": true,
  "id": "skill",
  "is_sensitive": false,
  "name": "易之三义",
  "output_schema": {},
  "source": "manual",
  "status": "approved",
  "tags": [
   "指令型",
   "代码与工程"
  ]
 },
 "testing-anti-patterns": {
  "author": "unknown",
  "category": "custom",
  "config_schema": {
   "properties": {},
   "type": "object"
  },
  "content": "# Testing Anti-Patterns\n\n**Load this reference when:** writing or changing tests, adding mocks, or tempted to add test-only methods to production code.\n\n## Overview\n\nTests must verify real behavior, not mock behavior. Mocks are a means to isolate, not the thing being tested.\n\n**Core principle:** Test what the code does, not what the mocks do.\n\n**Following strict TDD prevents these anti-patterns.**\n\n## The Iron Laws\n\n```\n1. NEVER test mock behavior\n2. NEVER add test-only methods to production classes\n3. NEVER mock without understanding dependencies\n```\n\n## Anti-Pattern 1: Testing Mock Behavior\n\n**The violation:**\n```typescript\n// ❌ BAD: Testing that the mock exists\ntest('renders sidebar', () => {\n  render(<Page />);\n  expect(screen.getByTestId('sidebar-mock')).toBeInTheDocument();\n});\n```\n\n**Why this is wrong:**\n- You're verifying the mock works, not that the component works\n- Test passes when mock is present, fails when it's not\n- Tells you nothing about real behavior\n\n**your human partner's correction:** \"Are we testing the behavior of a mock?\"\n\n**The fix:**\n```typescript\n// ✅ GOOD: Test real component or don't mock it\ntest('renders sidebar', () => {\n  render(<Page />);  // Don't mock sidebar\n  expect(screen.getByRole('navigation')).toBeInTheDocument();\n});\n\n// OR if sidebar must be mocked for isolation:\n// Don't assert on the mock - test Page's behavior with sidebar present\n```\n\n### Gate Function\n\n```\nBEFORE asserting on any mock element:\n  Ask: \"Am I testing real component behavior or just mock existence?\"\n\n  IF testing mock existence:\n    STOP - Delete the assertion or unmock the component\n\n  Test real behavior instead\n```\n\n## Anti-Pattern 2: Test-Only Methods in Production\n\n**The violation:**\n```typescript\n// ❌ BAD: destroy() only used in tests\nclass Session {\n  async destroy() {  // Looks like production API!\n    await this._workspaceManager?.destroyWorkspace(this.id);\n    // ... cleanup\n  }\n}\n\n// In tests\nafterEach(() => session.destroy());\n```\n\n**Why this is wrong:**\n- Production class polluted with test-only code\n- Dangerous if accidentally called in production\n- Violates YAGNI and separation of concerns\n- Confuses object lifecycle with entity lifecycle\n\n**The fix:**\n```typescript\n// ✅ GOOD: Test utilities handle test cleanup\n// Session has no destroy() - it's stateless in production\n\n// In test-utils/\nexport async function cleanupSession(session: Session) {\n  const workspace = session.getWorkspaceInfo();\n  if (workspace) {\n    await workspaceManager.destroyWorkspace(workspace.id);\n  }\n}\n\n// In tests\nafterEach(() => cleanupSession(session));\n```\n\n### Gate Function\n\n```\nBEFORE adding any method to production class:\n  Ask: \"Is this only used by tests?\"\n\n  IF yes:\n    STOP - Don't add it\n    Put it in test utilities instead\n\n  Ask: \"Does this class own this resource's lifecycle?\"\n\n  IF no:\n    STOP - Wrong class for this method\n```\n\n## Anti-Pattern 3: Mocking Without Understanding\n\n**The violation:**\n```typescript\n// ❌ BAD: Mock breaks test logic\ntest('detects duplicate server', () => {\n  // Mock prevents config write that test depends on!\n  vi.mock('ToolCatalog', () => ({\n    discoverAndCacheTools: vi.fn().mockResolvedValue(undefined)\n  }));\n\n  await addServer(config);\n  await addServer(config);  // Should throw - but won't!\n});\n```\n\n**Why this is wrong:**\n- Mocked method had side effect test depended on (writing config)\n- Over-mocking to \"be safe\" breaks actual behavior\n- Test passes for wrong reason or fails mysteriously\n\n**The fix:**\n```typescript\n// ✅ GOOD: Mock at correct level\ntest('detects duplicate server', () => {\n  // Mock the slow part, preserve behavior test needs\n  vi.mock('MCPServerManager'); // Just mock slow server startup\n\n  await addServer(config);  // Config written\n  await addServer(config);  // Duplicate detected ✓\n});\n```\n\n### Gate Function\n\n```\nBEFORE mocking any method:\n  STOP - Don't mock yet\n\n  1. Ask: \"What side effects does the real method have?\"\n  2. Ask: \"Does this test depend on any of those side effects?\"\n  3. Ask: \"Do I fully understand what this test needs?\"\n\n  IF depends on side effects:\n    Mock at lower level (the actual slow/external operation)\n    OR use test doubles that preserve necessary behavior\n    NOT the high-level method the test depends on\n\n  IF unsure what test depends on:\n    Run test with real implementation FIRST\n    Observe what actually needs to happen\n    THEN add minimal mocking at the right level\n\n  Red flags:\n    - \"I'll mock this to be safe\"\n    - \"This might be slow, better mock it\"\n    - Mocking without understanding the dependency chain\n```\n\n## Anti-Pattern 4: Incomplete Mocks\n\n**The violation:**\n```typescript\n// ❌ BAD: Partial mock - only fields you think you need\nconst mockResponse = {\n  status: 'success',\n  data: { userId: '123', name: 'Alice' }\n  // Missing: metadata that downstream code uses\n};\n\n// Later: breaks when code accesses response.metadata.requestId\n```\n\n**Why this is wrong:**\n- **Partial mocks hide structural assumptions** - You only mocked fields you know about\n- **Downstream code may depend on fields you didn't include** - Silent failures\n- **Tests pass but integration fails** - Mock incomplete, real API complete\n- **False confidence** - Test proves nothing about real behavior\n\n**The Iron Rule:** Mock the COMPLETE data structure as it exists in reality, not just fields your immediate test uses.\n\n**The fix:**\n```typescript\n// ✅ GOOD: Mirror real API completeness\nconst mockResponse = {\n  status: 'success',\n  data: { userId: '123', name: 'Alice' },\n  metadata: { requestId: 'req-789', timestamp: 1234567890 }\n  // All fields real API returns\n};\n```\n\n### Gate Function\n\n```\nBEFORE creating mock responses:\n  Check: \"What fields does the real API response contain?\"\n\n  Actions:\n    1. Examine actual API response from docs/examples\n    2. Include ALL fields system might consume downstream\n    3. Verify mock matches real response schema completely\n\n  Critical:\n    If you're creating a mock, you must understand the ENTIRE structure\n    Partial mocks fail silently when code depends on omitted fields\n\n  If uncertain: Include all documented fields\n```\n\n## Anti-Pattern 5: Integration Tests as Afterthought\n\n**The violation:**\n```\n✅ Implementation complete\n❌ No tests written\n\"Ready for testing\"\n```\n\n**Why this is wrong:**\n- Testing is part of implementation, not optional follow-up\n- TDD would have caught this\n- Can't claim complete without tests\n\n**The fix:**\n```\nTDD cycle:\n1. Write failing test\n2. Implement to pass\n3. Refactor\n4. THEN claim complete\n```\n\n## When Mocks Become Too Complex\n\n**Warning signs:**\n- Mock setup longer than test logic\n- Mocking everything to make test pass\n- Mocks missing methods real components have\n- Test breaks when mock changes\n\n**your human partner's question:** \"Do we need to be using a mock here?\"\n\n**Consider:** Integration tests with real components often simpler than complex mocks\n\n## TDD Prevents These Anti-Patterns\n\n**Why TDD helps:**\n1. **Write test first** → Forces you to think about what you're actually testing\n2. **Watch it fail** → Confirms test tests real behavior, not mocks\n3. **Minimal implementation** → No test-only methods creep in\n4. **Real dependencies** → You see what the test actually needs before mocking\n\n**If you're testing mock behavior, you violated TDD** - you added mocks without watching test fail against real code first.\n\n## Quick Reference\n\n| Anti-Pattern | Fix |\n|--------------|-----|\n| Assert on mock elements | Test real component or unmock it |\n| Test-only methods in production | Move to test utilities |\n| Mock without understanding | Understand dependencies first, mock minimally |\n| Incomplete mocks | Mirror real API completely |\n| Tests as afterthought | TDD - tests first |\n| Over-complex mocks | Consider integration tests |\n\n## Red Flags\n\n- Assertion checks for `*-mock` test IDs\n- Methods only called in test files\n- Mock setup is >50% of test\n- Test fails when you remove mock\n- Can't explain why mock is needed\n- Mocking \"just to be safe\"\n\n## The Bottom Line\n\n**Mocks are tools to isolate, not things to test.**\n\nIf TDD reveals you're testing mock behavior, you've gone wrong.\n\nFix: Test real behavior or question why you're mocking at all.",
  "content_type": "markdown",
  "description": "在以下情况请查阅这份参考指南：编写或修改测试、添加 Mock（测试替身），或者忍不住想往生产代码里加‘仅供测试使用’的方法时。由 1 份素材蒸馏生成",
  "enabled": true,
  "id": "testing-anti-patterns",
  "is_sensitive": false,
  "name": "testing-anti-patterns（testing-anti-patterns）",
  "output_schema": {},
  "source": "external_agent",
  "status": "approved",
  "tags": [
   "external",
   "imported",
   "markdown"
  ]
 }
}"""

MAIN_TRACK_FIXTURE = json.loads(_MAIN_TRACK_FIXTURE_JSON)
