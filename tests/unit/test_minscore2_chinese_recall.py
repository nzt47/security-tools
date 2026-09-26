# -*- coding: utf-8 -*-
"""MINSCORE-2 · 特征化锁：生产 min_score=0.3 把中文召回从 8/8 打到 4/8

## 本文件锁什么（三层，全部走**生产入口** SkillLoader.match() / Orchestrator._bounded_relevance）

1. **判据的本质**：质量闸/腿级过滤比较的那个「有界相似度」**不是相似度**，而是
   _match_score = H / N（H = 该技能命中的 query token 数，N = query token 数）。
   ⇒ 与文档长度**完全无关**、与 query 长度**严格成反比**。
2. **生产四格（向量腿不可用 = config.yaml 声明的降级模式）**：
   min_score=0.3 ⇒ 中文 **4/8**；min_score=0.01 ⇒ 中文 **8/8**；英文两档都是 8/8。
3. **根因是「腿被调用方的 min_score 清空」**：被拒的 4 条在 0.3 下**两条腿都空**，
   降到 0.0/0.01 立刻有候选（且正解就在里面）。
4. **跨文件阻塞（本卡实测）**：即使把 loader 修好，编排层 _semantic_layer_match 的
   第二道闸用**同一个 0.3** 比**同一个 H/N** ⇒ 同样 4 条被判 low_bounded_relevance 降级 LLM
   ⇒ **只在 loader.py 里修 = 端到端零收益**。详见 docs/audit_skill_governance/MINSCORE1.md。

## 这不是「守卫缺陷应当存在」

断言的是**当前可复现的事实与机制不变量**。谁把中文召回修回 8/8，本文件必然变红 ——
那是**预期的**：请同步更新本文件里的数字与 MINSCORE1.md 的结论，而不是把断言删掉。
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

    def test_chinese_recall_at_production_min_score_is_4_of_8(self, loader, queries):
        """生产阈值 0.3 + 向量腿不可用 ⇒ 中文 4/8（当前事实）。"""
        got = {r["id"]: (r["expected_skill"] in _ids(
            loader, r["query"], min_score=PRODUCTION_MIN_SCORE,
            use_vector=True, use_bm25=True, fusion_mode="rrf"))
            for r in queries if r["lang"] == "zh"}
        hit = sum(got.values())
        assert hit == 4, (
            "特征化锁：当前中文召回 %d/8，本文件登记的是 4/8。"
            "若这个数字变了 ⇒ 判据/常量被改动，请同步更新本文件与 "
            "docs/audit_skill_governance/MINSCORE1.md（这是预期的红，不是放宽断言）。"
            "逐条=%r" % (hit, got))
        assert sorted(k for k, v in got.items() if not v) == list(ZH_MISS_AT_PRODUCTION), (
            "被清空的必须是这 4 条（长中文 query / 低覆盖率），实际 %r"
            % (sorted(k for k, v in got.items() if not v),))

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

    @pytest.mark.parametrize("qid", ZH_MISS_AT_PRODUCTION)
    def test_leg_has_candidates_below_the_threshold_only(self, loader, queries, qid):
        """同一 query：0.3 ⇒ 空；0.0 ⇒ 有候选。**只差一个阈值**，与「没东西可召回」无关。"""
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


# ═══════════════════════════════════════════════════════════════════
#  4. 跨文件阻塞：编排层第二道闸用同一个 0.3 比同一个 H/N
# ═══════════════════════════════════════════════════════════════════

class TestOrchestratorSecondGateBlocksALoaderOnlyFix:

    def test_rescued_candidates_are_still_rejected_by_the_orchestrator(self, loader, queries):
        """把腿救活（= 本卡实测的候选修法 C2/C3 的产物）后，编排层仍然拒绝。

        ⇒ **只在 loader.py 里修（腿级地板与调用方阈值解耦）= 端到端零收益**：
          被救回的候选在 Orchestrator._bounded_relevance(top1) < min_score(0.3)
          下仍然判 low_bounded_relevance 降级 LLM。
          根因是**同一个 0.3 被用在两处、比的都是 H/N**。
        """
        from agent.orchestrator.orchestrator import Orchestrator
        rescued, blocked = [], []
        for r in queries:
            if r["lang"] != "zh":
                continue
            res = loader.match(r["query"], top_k=5, enabled_only=True,
                               min_score=LOADER_DEFAULT_MIN_SCORE,
                               use_vector=True, use_bm25=True, fusion_mode="rrf")
            if not res.matches:
                continue
            top1 = res.matches[0]
            if top1.skill_id != r["expected_skill"]:
                continue
            rescued.append(r["id"])
            rel = Orchestrator._bounded_relevance(top1)
            if rel is not None and rel < PRODUCTION_MIN_SCORE:
                blocked.append((r["id"], rel))
        missing = [q for q in ZH_MISS_AT_PRODUCTION if q not in rescued]
        assert missing == [], (
            "前置：0.01 下这 4 条的正解必须都能被 loader 救回，实际缺席 %r" % (missing,))
        assert [b[0] for b in blocked] == list(ZH_MISS_AT_PRODUCTION), (
            "这 4 条被 loader 交出来之后，编排层第二道闸必须仍然全部拦下"
            "（同一个 0.3 比同一个 H/N）；实际被拦 %r。若这里变了（例如有人把 "
            "min_score 或 _bounded_relevance 改了），请更新本断言与 MINSCORE1.md 的"
            "「跨文件阻塞」结论" % (blocked,))
