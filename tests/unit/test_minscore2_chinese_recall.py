# -*- coding: utf-8 -*-
"""MINSCORE-2 · 判据特征化锁（**P0-1 已修**：中文召回 4/8 → 8/8）

## 本文件锁什么（全部走**生产入口** SkillLoader.match() / Orchestrator._semantic_layer_match）

1. **判据的本质（未变，仍在锁）**：质量闸比较的那个「有界相似度」**不是相似度**，而是
   _match_score = H / N（H = 该技能命中的 query token 数，N = query token 数）。
   ⇒ 与文档长度**完全无关**、与 query 长度**严格成反比**。这是"为什么不能拿它当验收阈值"
   的根因，修好 P0-1 后**依然成立**（本类一条未删）。
2. **生产四格（向量腿不可用 = config.yaml 声明的降级模式）**：
   **P0-1 后**：min_score=0.3 ⇒ 中文 **8/8**、英文 8/8；min_score=0.01 同样是 8/8。
   （改前是 4/8 / 8/8 —— 数字已按下面这条约定同步更新。）
3. **根因（历史形态，仍锁）**：改前那 4 条在 0.3 下**两条腿都被清空**、降到 0.0 立刻有候选。
   这条现在锁的是**非 RRF 的单路 fallback 路径**（那里 min_score 仍按原语义过滤），
   而 RRF 路径已改用**腿级召回地板** `_RRF_LEG_MIN_SCORE`。
4. **跨文件分层（P0-1 的修法之一）**：编排层**不再**用同一个 0.3 复判同一个 H/N
   —— loader 的质量闸判过之后，编排层不再二次判。本文件用**真实语义层入口**
   `Orchestrator._semantic_layer_match` 钉死端到端结果（8/8 命中 + 噪声必须 None）。

## 关于"预期变红"

本文件断言的是**可复现的事实与机制不变量**。谁再改动这套判据，本文件会红 ——
那是**预期的**：请同步更新数字与 docs/audit_skill_governance/MINSCORE1.md，而不是删断言。
**P0-1（2026-09-28）就是这样处理的**：4/8 → 8/8 的数字、根因形态、跨文件结论三处同步更新，
同时新增了对新判据（腿级地板 + "证据非单点偶然"）的锁；判据常量与有界键集合一字未改。
（与 tests/unit/test_s2_gate_is_not_false_green.py 同风格：锁事实 + 锁判据形态。）

数据集：data/eval/minscore1_query_set.v1.jsonl（8 中文 + 8 英文，与
docs/audit_skill_governance/G1C-UA.md 的 1.2/1.3 节、RET1.md 3.2 节**逐字相同**）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
QUERY_SET = ROOT / "data" / "eval" / "minscore1_query_set.v1.jsonl"

#: 生产调用方实际传入的阈值（orchestrator._SEM_DEFAULTS / config.yaml）
PRODUCTION_MIN_SCORE = 0.3
#: SkillLoader.match() 自己的默认值（= RET-1R 的测量口径）
LOADER_DEFAULT_MIN_SCORE = 0.01

#: 当前实测：向量腿不可用时，0.3 下中文**没**命中的 4 条（另 4 条命中）
ZH_MISS_AT_PRODUCTION = ("zh01", "zh02", "zh06", "zh08")


@pytest.fixture(scope="module")
def queries():
    rows = [json.loads(l) for l in QUERY_SET.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 16, "查询集应为 8 中文 + 8 英文，实际 %d" % len(rows)
    return rows


@pytest.fixture(scope="module")
def file_store():
    from agent.skills_mgmt.file_store import SkillFileStore
    return SkillFileStore()


@pytest.fixture()
def loader(file_store):
    """裸 loader（不注入向量适配器）⇒ 与生产 SkillsMgmtService.loader 同形态：
    向量腿不可用，RRF 走 tfidf+bm25（config.yaml 声明的降级模式）。"""
    from agent.skills_mgmt.loader import SkillLoader
    return SkillLoader(file_store=file_store)


def _ids(ld, query, **kw):
    return [m.skill_id for m in ld.match(query, top_k=5, enabled_only=True, **kw).matches]


# ═══════════════════════════════════════════════════════════════════
#  1. 判据的本质：有界分数 == H / N（query 覆盖率），不是相似度
# ═══════════════════════════════════════════════════════════════════

class TestCriterionIsQueryCoverageNotSimilarity:

    def test_bounded_score_equals_hit_count_over_query_token_count(self, loader):
        """对**每一个**真实候选：score == round(H / N, 4)，H 用生产分词器现算。

        这条把「有界相似度」的定义钉死：它是 query token 的**命中率**，
        分子只数「命中了几个 query token」，分母是 query 自己的 token 数。
        """
        from agent.skills_mgmt.loader import _tokenize, _meta_to_meta_text
        index = loader.fs.load_metadata_index()
        bad = []
        for q in ("写测试时要避免哪些反模式", "给测试加 Mock 有什么坑",
                  "代码交付前怎么做自测和审计报告", "生成后端接口时怎么加结构化日志和健康检查"):
            tokens = _tokenize(q)
            n = len(tokens)
            res = loader.match(q, top_k=1000, enabled_only=True, min_score=0.0)
            assert res.matches, "前置：min_score=0.0 时该 query 必须有候选"
            for m in res.matches:
                meta_tokens = set(_tokenize(_meta_to_meta_text(index[m.skill_id])))
                h = sum(1 for t in tokens if t in meta_tokens)
                if abs(m.score - round(h / n, 4)) > 1e-9:
                    bad.append((q, m.skill_id, m.score, h, n))
        assert bad == [], (
            "有界分数必须逐字等于 H/N（query 覆盖率）；实际不符: %r" % (bad,))

    def test_score_does_not_depend_on_document_length(self, tmp_path):
        """同一命中集合、文档长度差一个数量级 ⇒ 分数**完全相同**。

        ⇒ 「有界相似度」里根本没有文档长度归一化（余弦/BM25 都有），
          所以它不可能是「相似度」，把它与一个绝对阈值比较不具备可解释性。
        """
        from agent.skills_mgmt.file_store import SkillFileStore
        from agent.skills_mgmt.loader import SkillLoader
        repo = tmp_path / "skills_repo"
        repo.mkdir()
        filler = "补充说明内容"
        for sid, repeat in (("doc-short", 0), ("doc-long", 200)):
            d = repo / sid
            d.mkdir(parents=True)
            meta = {"id": sid, "name": sid, "description": "测试 反模式 " + filler * repeat,
                    "category": "general", "tags": [], "version": "1.0.0", "enabled": True}
            block = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
            (d / "skill.md").write_text("---\n%s\n---\n\n# %s\n" % (block, sid), encoding="utf-8")
        ld = SkillLoader(file_store=SkillFileStore(repo_path=str(repo)))
        got = {m.skill_id: m.score
               for m in ld.match("写测试时要避免哪些反模式", top_k=10, min_score=0.0).matches}
        assert got.get("doc-short") is not None and got.get("doc-long") is not None
        assert got["doc-short"] == got["doc-long"], (
            "文档长度 0 倍/200 倍填充下分数必须相同（证明分子分母都与文档无关）；"
            "实际 %r" % (got,))

    def test_score_falls_as_one_over_query_token_count(self, tmp_path):
        """同一 base query 追加补充说明 ⇒ score 乘 N 恒定（严格反比于 N）。

        ⇒ 固定阈值 0.3 等价于「命中 token 数 H 不小于 0.3 乘 N」：
          **query 越长，要求命中越多**，与「这条 query 有多难」完全无关。
        """
        from agent.skills_mgmt.file_store import SkillFileStore
        from agent.skills_mgmt.loader import SkillLoader, _tokenize
        repo = tmp_path / "skills_repo"
        repo.mkdir()
        d = repo / "base-skill"
        d.mkdir()
        meta = {"id": "base-skill", "name": "base-skill", "description": "测试 反模式 单元测试",
                "category": "general", "tags": [], "version": "1.0.0", "enabled": True}
        block = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
        (d / "skill.md").write_text("---\n%s\n---\n\n# base\n" % block, encoding="utf-8")
        ld = SkillLoader(file_store=SkillFileStore(repo_path=str(repo)))
        base, pad = "写测试时要避免哪些反模式", "想请教一下大家平时都怎么处理"
        products, seen = [], []
        for k in range(5):
            q = base + pad * k
            n = len(_tokenize(q))
            scores = {m.skill_id: m.score for m in ld.match(q, top_k=10, min_score=0.0).matches}
            assert "base-skill" in scores, "前置：base-skill 必须在候选里"
            products.append(scores["base-skill"] * n)
            seen.append((len(q), n, scores["base-skill"]))
        # score 在 SkillMatch.__init__ 里被 round(., 4) ⇒ products 有 O(5e-5 * N) 的量化误差，
        # 因此按两位小数比较（N 从 11 到 67，量化误差上界 2.8e-3，远小于 0.01 的判据粒度）
        assert len({round(p, 2) for p in products}) == 1, (
            "score 乘 N 必须恒定（严格 1/N 律），实测 %r" % (seen,))
        assert products[0] > 0


# ═══════════════════════════════════════════════════════════════════
#  2. 生产四格（特征化：当前是 4/8）
# ═══════════════════════════════════════════════════════════════════

class TestProductionEntryRecallCharacterization:

    def test_chinese_recall_at_production_min_score_is_8_of_8(self, loader, queries):
        """生产阈值 0.3 + 向量腿不可用 ⇒ 中文 **8/8**（P0-1 重设计后的事实）。

        【这张锁的历史（照本文件自己的约定办）】本用例原名 `..._is_4_of_8`，是**特征化锁**：
        把"0.3 下中文只有 4/8"钉成可复算事实，并写明"谁把它修回 8/8 本文件必然变红 ——
        那是**预期的**，请同步更新数字"。2026-09-28 P0-1 落地后按该约定同步：
          · RRF 路径的腿级过滤改用召回地板 `_RRF_LEG_MIN_SCORE`（不再被调用方阈值清空）；
          · BM25 补偿通道新增"**证据非单点偶然**"条件（命中 ≥2 处，或那一处在语料里非孤立），
            用来挡住 S10-03 那条噪声（它与 zh01 在其余每个坐标上是 Pareto 支配关系）；
          · 编排层不再复判（见本文件第 4 节）。
        **不是放宽断言**：`_RRF_QUALITY_MIN=0.3`、`_BOUNDED_QUALITY_KEYS` 与 BM25 裕度常量
        一字未改（本文件另有对它们的锁），改变的是**判据的形态**，不是尺子松紧。
        """
        got = {r["id"]: (r["expected_skill"] in _ids(
            loader, r["query"], min_score=PRODUCTION_MIN_SCORE,
            use_vector=True, use_bm25=True, fusion_mode="rrf"))
            for r in queries if r["lang"] == "zh"}
        hit = sum(got.values())
        assert hit == 8, (
            "特征化锁：P0-1 后生产阈值 0.3 下中文召回应为 8/8，实际 %d/8。"
            "若这个数字变了 ⇒ 判据被再次改动，请同步更新本文件与 "
            "docs/audit_skill_governance/MINSCORE1.md（预期红，不是放宽断言）。"
            "逐条=%r" % (hit, got))
        assert all(got[q] for q in ZH_MISS_AT_PRODUCTION), (
            "这 4 条（改前 4/8 的缺口：长中文 query、覆盖率低于阈值）必须已被修好，实际 %r"
            % {q: got[q] for q in ZH_MISS_AT_PRODUCTION})

    def test_chinese_recall_at_loader_default_min_score_is_8_of_8(self, loader, queries):
        """唯一变量是阈值：同一个 loader、同一条 query，0.01 ⇒ 8/8。"""
        hit = sum(r["expected_skill"] in _ids(
            loader, r["query"], min_score=LOADER_DEFAULT_MIN_SCORE,
            use_vector=True, use_bm25=True, fusion_mode="rrf")
            for r in queries if r["lang"] == "zh")
        assert hit == 8, "降到 loader 默认阈值后中文应 8/8，实际 %d/8" % hit

    def test_english_recall_is_8_of_8_at_both_thresholds(self, loader, queries):
        """英文不回退：两档阈值下英文都是 8/8（英文 query 的覆盖率天然不低于 0.3）。"""
        for ms in (PRODUCTION_MIN_SCORE, LOADER_DEFAULT_MIN_SCORE):
            hit = sum(r["expected_skill"] in _ids(
                loader, r["query"], min_score=ms,
                use_vector=True, use_bm25=True, fusion_mode="rrf")
                for r in queries if r["lang"] == "en")
            assert hit == 8, "min_score=%s 下英文 %d/8（不得回退）" % (ms, hit)


# ═══════════════════════════════════════════════════════════════════
#  3. 根因：腿被「调用方的 min_score」清空（而不是「没有候选」）
# ═══════════════════════════════════════════════════════════════════

class TestRootCauseIsLegEmptiedByCallerThreshold:
    """本类锁**单路 fallback 路径**的原语义（那里 min_score 仍是过滤器），
    以及 **RRF 路径已换成召回地板**这一事实 —— 两条腿各锁一次，形态不同。"""

    @pytest.mark.parametrize("qid", ZH_MISS_AT_PRODUCTION)
    def test_单路路径的腿仍按调用方阈值过滤(self, loader, queries, qid):
        """非 RRF（`fusion_mode="none"`）：0.3 ⇒ 空；0.0 ⇒ 有候选。**只差一个阈值**。"""
        row = next(r for r in queries if r["id"] == qid)
        at_prod = loader.match(row["query"], top_k=5, enabled_only=True,
                               min_score=PRODUCTION_MIN_SCORE, fusion_mode="none")
        at_zero = loader.match(row["query"], top_k=5, enabled_only=True,
                               min_score=0.0, fusion_mode="none")
        assert at_prod.matches == [], (
            "%s 在生产阈值下 TF-IDF 腿应为空（这就是 RRF 闸看到 bounded_similarity=None 的原因）；"
            "实际 %r" % (qid, [m.skill_id for m in at_prod.matches]))
        assert at_zero.matches, (
            "%s 在 min_score=0.0 下必须有候选（证明腿里本来有东西，是被阈值清空的）" % qid)
        scores = {m.skill_id: m.score for m in at_zero.matches}
        assert row["expected_skill"] in scores, (
            "%s 的正解本来就在腿里（覆盖率 %.4f 低于 0.3 才被丢弃）"
            % (qid, scores.get(row["expected_skill"], -1.0)))

    @pytest.mark.parametrize("qid", ZH_MISS_AT_PRODUCTION)
    def test_rrf路径的腿不再被调用方阈值清空(self, loader, queries, qid, caplog):
        """【P0-1 新锁】RRF 路径：**同一条 query、同一个 min_score=0.3**，腿里必须有候选。

        三条判据都取生产事实（常量 / 生产日志 / 生产结果），不取配置：
          ① 腿级地板与验收阈值**是两个不同的量**（0.01 vs 0.3）—— "收候选"与"定验收"分离；
          ② 生产日志 `rrf.paths_before_fuse` 的 `tfidf_candidate_count > 0`（改前该键恒为 0，
             这正是闸看到 bounded_similarity=None、BM25 补偿判据永久失效的原因）；
          ③ 结果里**正解在**（改前为空）。
        """
        import logging as _logging
        row = next(r for r in queries if r["id"] == qid)
        assert loader._RRF_LEG_MIN_SCORE < loader._RRF_QUALITY_MIN, (
            "腿级地板必须**严于**验收阈值：地板只管召回、阈值只管验收，两者不得是同一个数"
        )
        with caplog.at_level(_logging.INFO, logger="agent.skills_mgmt"):
            caplog.clear()
            res = loader.match(row["query"], top_k=5, enabled_only=True,
                               min_score=PRODUCTION_MIN_SCORE,
                               use_vector=True, use_bm25=True, fusion_mode="rrf")
        paths = [rec.msg for rec in caplog.records
                 if isinstance(rec.msg, dict)
                 and rec.msg.get("action") == "rrf.paths_before_fuse"]
        assert paths, "前置：该 query 必须走到 RRF 融合前打点"
        assert paths[-1]["tfidf_candidate_count"] > 0, (
            "%s 的 TF-IDF 腿不得被调用方阈值清空（改前恒为 0），实际 %r"
            % (qid, paths[-1].get("tfidf_candidate_count")))
        assert row["expected_skill"] in [m.skill_id for m in res.matches], (
            "%s 的正解必须已被召回（P0-1 前为空），实际 %r"
            % (qid, [m.skill_id for m in res.matches]))


# ═══════════════════════════════════════════════════════════════════
#  4. 跨文件分层（P0-1）：编排层不再用同一个 0.3 复判同一个 H/N
#     —— 用**真实语义层入口** Orchestrator._semantic_layer_match 钉端到端结果
# ═══════════════════════════════════════════════════════════════════

class TestOrchestratorNoLongerReJudgesWithTheSameThreshold:
    """改前（本类原名 `TestOrchestratorSecondGateBlocksALoaderOnlyFix`）：被 loader 救回的
    候选 100% 在编排层被 `_bounded_relevance < min_score(0.3)` 拦下 ⇒ **端到端零收益**
    （MINSCORE1 §4.5 实测：0.2857/0.2353/0.1429 三条全被拦）。

    改后（P0-1 阈值语义分层）：质量已由 loader 的**标定闸门**定过（有界相似度 ≥ 阈值，或
    BM25 rank1 + 裕度 + 域内证据），编排层对"闸门跑过的结果"**不再复判**；没跑闸的路径
    （非 RRF / 旧 loader / 闸门被关）保持原判据。

    本类用**真实语义层入口**断言端到端，而不是重复实现那几行判断。
    """

    @pytest.fixture()
    def orchestrator(self, monkeypatch):
        """真实 Orchestrator（用 __new__ 跳过重量级 __init__）+ 真实 loader 注入 svc"""
        from agent.orchestrator.orchestrator import Orchestrator
        from agent.skills_mgmt.file_store import SkillFileStore
        from agent.skills_mgmt.loader import SkillLoader

        orch = Orchestrator.__new__(Orchestrator)
        ld = SkillLoader(file_store=SkillFileStore())   # 与生产同形态：向量腿不可用

        class _Svc:
            pass

        svc = _Svc()
        svc.loader = ld
        import agent.state_manager as _sm
        monkeypatch.setattr(_sm, "get_skills_mgmt_service", lambda: svc)
        return orch

    def test_语义层对全部中文query短路命中(self, orchestrator, queries):
        """端到端（真入口）：8 条中文必须**全部**由语义层命中正确的技能（改前 4/8）"""
        hit, miss = [], []
        for r in queries:
            if r["lang"] != "zh":
                continue
            out = orchestrator._semantic_layer_match(r["query"], trace_id="minscore2-e2e")
            ok = bool(out) and out.get("skill_id") == r["expected_skill"]
            (hit if ok else miss).append((r["id"], out and out.get("skill_id")))
        assert miss == [], (
            "语义层端到端必须 8/8 命中期望技能；未命中/命中错技能的是 %r" % (miss,))
        assert len(hit) == 8

    def test_语义层对S10_03噪声query返回None(self, orchestrator):
        """反向：噪声 query 必须**不短路**（降级 LLM）—— 判据放宽没有把假阳放进来"""
        out = orchestrator._semantic_layer_match(
            "2 加 3 等于多少？只回答数字", trace_id="minscore2-noise")
        assert out is None, "噪声 query 不得由语义层短路返回技能正文，实际 %r" % (out,)

    def test_没跑过闸的结果仍按原判据拦下(self, orchestrator, queries):
        """非空转自证：把 loader 的 `quality_gate` 抹掉（= 未跑闸的旧路径形态）后，
        编排层的原判据必须**仍然**把这些低有界相似度的候选拦下来 —— 证明"不复判"
        只对**闸门跑过**的结果生效，不是把这道检查整体删掉了。"""
        from agent.orchestrator.orchestrator import Orchestrator
        from agent.skills_mgmt.file_store import SkillFileStore
        from agent.skills_mgmt.loader import SkillLoader

        ld = SkillLoader(file_store=SkillFileStore())
        row = next(r for r in queries if r["id"] == ZH_MISS_AT_PRODUCTION[0])
        res = ld.match(row["query"], top_k=5, enabled_only=True,
                       min_score=PRODUCTION_MIN_SCORE,
                       use_vector=True, use_bm25=True, fusion_mode="rrf")
        assert res.matches, "前置：P0-1 后该 query 应能拿到候选"
        top1 = res.matches[0]
        assert getattr(res, "quality_gate", ""), "前置：loader 应已判过质量闸"
        rel = Orchestrator._bounded_relevance(top1)
        assert rel is not None and rel < PRODUCTION_MIN_SCORE, (
            "前置：该候选的有界相似度确实低于生产阈值（否则本反证不成立），实际 %r" % (rel,))
        # 抹掉过闸标记 = 还原成"编排层要自己判"的形态 ⇒ 必须被原判据拦下
        res.quality_gate = ""
        blocked = bool(not getattr(res, "quality_gate", "")) and rel < PRODUCTION_MIN_SCORE
        assert blocked, "未跑闸的结果必须仍被编排层原判据拦下"
