"""工具检索质量评估测试 — 20 query recall@5

加载 tests/fixtures/tool_retrieval_eval.json,对每个 query 调用
HybridRetriever.query(text, top_k=5),计算 recall@5,整体 ≥ 0.8 才通过。

【不易】fixture ground truth 不可改(人工标注,反映真实用户意图)
【变易】alpha 可配,默认 0.5;评估走纯 BM25(CI 环境一致性)
【简易】只算 recall@5,不算 precision(白名单本来就是 top-k 召回)

注意:
- 用 HybridRetriever.query() 而非 hybrid_select_tools(),因为前者直接返回
  [(tool_name, score)] 便于算 recall;后者会做 alias merge,影响 ground truth 对齐
- 单 query 测试用 parametrize 暴露失败明细,但单 query 失败不阻塞整体断言
"""
import json
import os
from unittest.mock import patch

import pytest


# ════════════════════════════════════════════════════════════
#  公共 fixture
# ════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _disable_embedding_probe(monkeypatch):
    """评估测试默认禁用 Embedding(走纯 BM25,与 CI 环境一致)

    Why: CI Linux SIGILL + Windows 0xC0000005 已知问题,Embedding 不可用。
         评估测试必须与生产降级路径一致,才能反映真实检索质量。
    """
    monkeypatch.setenv("AGENT_HYBRID_EMBEDDING", "0")


@pytest.fixture(autouse=True)
def _reset_hybrid_singleton():
    """每个测试前后重置 HybridRetriever 单例 + 探测缓存"""
    from agent.tool_router_hybrid import reset_hybrid_retriever
    import agent.tool_router_hybrid as mod
    reset_hybrid_retriever()
    mod._PROBE_RESULT = None
    yield
    reset_hybrid_retriever()
    mod._PROBE_RESULT = None


@pytest.fixture
def eval_data():
    """加载评估 fixture(20 query + ground truth)"""
    fixture_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "tests", "fixtures", "tool_retrieval_eval.json"
    )
    with open(fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def retriever():
    """构建真实 retriever(基于 data/tool_index.json,70 工具)"""
    from agent.tool_router_hybrid import HybridRetriever
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return HybridRetriever(index_path=os.path.join(project_root, "data", "tool_index.json"))


def _compute_recall(selected: list[str], ground_truth: list[str]) -> float:
    """recall@k = |selected ∩ ground_truth| / |ground_truth|"""
    if not ground_truth:
        return 1.0
    return len(set(selected) & set(ground_truth)) / len(ground_truth)


# ════════════════════════════════════════════════════════════
#  TestRetrievalQuality
# ════════════════════════════════════════════════════════════

class TestRetrievalQuality:
    """20 query 整体 recall@5 评估"""

    def test_overall_recall_at_5_above_threshold(self, eval_data, retriever, capsys):
        """整体 recall@5 ≥ 0.8(20 query 平均)

        Why: 验收标准要求 recall@5 ≥ 0.8。整体平均能容忍个别边界 case 失败,
             反映检索系统的真实可用性。
        """
        results = []
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            for q in eval_data["queries"]:
                selected = retriever.query(q["query"], top_k=5)
                tool_names = [name for name, _ in (selected or [])[:5]]
                recall = _compute_recall(tool_names, q["ground_truth"])
                results.append((q["id"], q["query"], tool_names, q["ground_truth"], recall))

        overall = sum(r[4] for r in results) / len(results) if results else 0.0
        with capsys.disabled():
            print("\n" + "=" * 70)
            print("工具检索质量评估(BM25 单路,AGENT_HYBRID_EMBEDDING=0)")
            print("=" * 70)
            for qid, query, selected, gt, recall in results:
                status = "PASS" if recall == 1.0 else ("PARTIAL" if recall > 0 else "FAIL")
                print(f"{status} {qid} recall={recall:.2f}  query={query!r}")
                print(f"     selected     = {selected}")
                print(f"     ground_truth = {gt}")
            print("=" * 70)
            print(f"整体 recall@5 = {overall:.4f}  (阈值 >= 0.80)")
            pass_count = sum(1 for r in results if r[4] == 1.0)
            print(f"通过率: {pass_count}/{len(results)} query 完全命中")
            print("=" * 70)

        assert overall >= 0.8, (
            f"整体 recall@5 = {overall:.4f} < 0.8,失败 query 见上方输出。"
        )

    @pytest.mark.parametrize("q_idx", range(20))
    def test_single_query_recall(self, eval_data, retriever, q_idx):
        """每个 query 单独测试(暴露失败明细,任一失败都打印诊断)

        Why: 整体断言可能掩盖个别 query 退化,单 query 测试精确定位失败点。
             失败信息含 selected vs ground_truth 对比,便于诊断 BM25 召回问题。
        """
        q = eval_data["queries"][q_idx]
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            selected = retriever.query(q["query"], top_k=5)
        tool_names = [name for name, _ in (selected or [])[:5]]
        recall = _compute_recall(tool_names, q["ground_truth"])

        if recall < 1.0:
            missing = set(q["ground_truth"]) - set(tool_names)
            pytest.fail(
                f"{q['id']} recall@5={recall:.2f}  query={q['query']!r}  "
                f"selected={tool_names}  ground_truth={q['ground_truth']}  "
                f"missing={missing}"
            )

    def test_fixture_integrity(self, eval_data):
        """fixture 结构完整性:20 query,每个含 id/query/ground_truth"""
        assert "queries" in eval_data
        assert len(eval_data["queries"]) == 20
        for q in eval_data["queries"]:
            assert "id" in q
            assert "query" in q
            assert "ground_truth" in q
            assert isinstance(q["ground_truth"], list)
            assert len(q["ground_truth"]) > 0
