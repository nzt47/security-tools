"""英文别名召回率单元测试 — 验证混合语言描述提升英文查询召回

背景:
  工具描述为纯中文时,BM25 对纯英文查询零字面命中(如 "extract text from pdf"
  只有 "pdf" 命中,extract/text 不命中中文),top1 经常错配。
  方案:在 YAML description 追加英文检索别名(语义独占分配),形成混合语言描述。

覆盖范围:
- BM25Index 级:同一英文查询,混合描述下 read_pdf 分数显著高于纯中文描述
- BM25Index 级:混合描述下英文查询 top1 全对(5/5),纯中文描述下存在错配
- HybridRetriever 级:alpha=0.5 融合路(Embedding 禁用 → 降级 BM25)英文查询 top1 命中
- 回归保护:追加英文别名后,中文查询召回不下降

设计原则(与 test_tool_router_hybrid.py 一致):
- 不依赖真实 tool_index.json(用内存工具集,避免数据漂移)
- 默认 AGENT_HYBRID_EMBEDDING=0,走纯 BM25,避免子进程探测/模型加载
- 单例隔离:每个测试 reset_hybrid_retriever
"""
import json
from unittest.mock import patch

import pytest


# ════════════════════════════════════════════════════════════
#  测试数据:纯中文描述 vs 混合语言描述(中文 + 英文别名)
# ════════════════════════════════════════════════════════════

PURE_CHINESE_DESC = {
    "read_pdf": "读取 PDF 文件中的文本内容（支持指定页码范围）",
    "merge_pdf": "合并多个 PDF 文件为一个",
    "split_pdf": "拆分 PDF 文件为多个独立的 PDF",
    "get_weather": "查询天气信息",
    "web_search": "搜索互联网信息",
}

# 英文别名语义独占分配(与 data/tool_definitions/*.yaml 应用方案一致)
MIXED_LANG_DESC = {
    "read_pdf": PURE_CHINESE_DESC["read_pdf"] + "。Extract text from PDF files, parse PDF documents",
    "merge_pdf": PURE_CHINESE_DESC["merge_pdf"] + "。Merge multiple PDF files into one, combine PDFs",
    "split_pdf": PURE_CHINESE_DESC["split_pdf"] + "。Split a PDF into separate pages, divide PDF",
    "get_weather": PURE_CHINESE_DESC["get_weather"] + "。Get weather forecast, temperature for a city",
    "web_search": PURE_CHINESE_DESC["web_search"] + "。Search the web, find information online",
}

# 英文查询 -> 期望命中的工具(模拟真实英文用户意图)
ENGLISH_CASES = [
    ("extract text from pdf", "read_pdf"),
    ("merge two pdf files", "merge_pdf"),
    ("split pdf into pages", "split_pdf"),
    ("get weather in beijing", "get_weather"),
    ("search the web for news", "web_search"),
]

# 中文查询 -> 期望命中的工具(回归保护)
CHINESE_CASES = [
    ("解析pdf", "read_pdf"),
    ("合并pdf", "merge_pdf"),
    ("拆分pdf", "split_pdf"),
    ("查询天气", "get_weather"),
    ("搜索网页", "web_search"),
]

_PARAMS = {
    "read_pdf": ["path", "pages"],
    "merge_pdf": ["paths", "output_path"],
    "split_pdf": ["path", "output_dir"],
    "get_weather": ["city", "format"],
    "web_search": ["query", "engine"],
}


def _make_tools(desc_map: dict) -> list[dict]:
    """构造工具集(模拟真实索引内容:name + parameter_names + description)"""
    return [
        {
            "name": name,
            "category": "test",
            "description": desc,
            "parameter_names": _PARAMS[name],
        }
        for name, desc in desc_map.items()
    ]


def _build_bm25(tools: list[dict]):
    """构建真实 BM25Index(索引内容与 HybridRetriever.rebuild 一致)"""
    from agent.tool_router_hybrid import BM25Index

    idx = BM25Index()
    for t in tools:
        content = (
            f"{t['name']} "
            f"{' '.join(t.get('parameter_names', []) or [])} "
            f"{t.get('description', '')}"
        )
        idx.add_document(t["name"], content)
    return idx


# ════════════════════════════════════════════════════════════
#  公共 fixture(与 test_tool_router_hybrid.py 一致)
# ════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _disable_embedding_probe(monkeypatch):
    """所有测试禁用 Embedding 探测,走纯 BM25 路径"""
    monkeypatch.setenv("AGENT_HYBRID_EMBEDDING", "0")


@pytest.fixture(autouse=True)
def _reset_hybrid_singleton():
    """每个测试前后重置 HybridRetriever 单例"""
    from agent.tool_router_hybrid import reset_hybrid_retriever
    import agent.tool_router_hybrid as mod

    reset_hybrid_retriever()
    mod._PROBE_RESULT = None
    yield
    reset_hybrid_retriever()
    mod._PROBE_RESULT = None


@pytest.fixture
def pure_bm25():
    return _build_bm25(_make_tools(PURE_CHINESE_DESC))


@pytest.fixture
def mixed_bm25():
    return _build_bm25(_make_tools(MIXED_LANG_DESC))


# ════════════════════════════════════════════════════════════
#  BM25 分数提升
# ════════════════════════════════════════════════════════════

class TestBm25ScoreImprovement:
    """混合语言描述下,英文查询 BM25 分数显著提升"""

    def test_mixed_desc_score_higher_than_pure(self, pure_bm25, mixed_bm25):
        """每个英文查询下,目标工具在混合描述索引中的 BM25 分 > 纯中文索引"""
        for query, expected in ENGLISH_CASES:
            pure_score = dict(pure_bm25.search(query, top_k=20)).get(expected, 0.0)
            mixed_score = dict(mixed_bm25.search(query, top_k=20)).get(expected, 0.0)
            assert mixed_score > pure_score, (
                f"查询 {query!r} 目标工具 {expected}: "
                f"混合描述分 {mixed_score:.4f} 应大于纯中文分 {pure_score:.4f}"
            )

    def test_pure_chinese_english_query_scores_zero(self, pure_bm25):
        """对照:纯中文描述下,英文查询中纯英文 token 无命中,
        目标工具分数仅来自 'pdf' 等共享 token,整体显著低于别名补充后"""
        # 'get weather in beijing' 的 token(get/weather/in/beijing)不命中任何中文描述
        results = pure_bm25.search("get weather in beijing", top_k=20)
        assert results == [], "纯中文描述下纯英文查询应无 BM25 命中(零字面失效)"


# ════════════════════════════════════════════════════════════
#  top1 召回正确性
# ════════════════════════════════════════════════════════════

class TestTop1Recall:
    """英文查询 top1 召回正确性"""

    def test_mixed_desc_top1_all_correct(self, mixed_bm25):
        """混合语言描述下,5 个英文查询 top1 全部命中期望工具"""
        for query, expected in ENGLISH_CASES:
            results = mixed_bm25.search(query, top_k=10)
            assert results, f"查询 {query!r} 应有结果"
            assert results[0][0] == expected, (
                f"查询 {query!r} top1={results[0][0]} 应为 {expected}"
            )

    def test_pure_chinese_desc_top1_mismatch_exists(self, pure_bm25):
        """对照:纯中文描述下英文查询存在 top1 错配(证明问题真实存在)"""
        correct = 0
        for query, expected in ENGLISH_CASES:
            results = pure_bm25.search(query, top_k=10)
            if results and results[0][0] == expected:
                correct += 1
        assert correct < len(ENGLISH_CASES), "纯中文描述下不应全部命中(否则无需别名方案)"

    def test_hybrid_retriever_alpha_05_top1_correct(self, tmp_path):
        """HybridRetriever(alpha=0.5)融合路:混合描述下英文查询 top1 命中期望工具

        注:Embedding 被禁用,融合退化为 BM25 分,但验证了完整链路 + alpha 参数透传。
        """
        from agent.tool_router_hybrid import HybridRetriever

        index_data = {
            "generated_at": "2026-08-07T00:00:00",
            "tool_count": len(_make_tools(MIXED_LANG_DESC)),
            "categories": ["test"],
            "tools": _make_tools(MIXED_LANG_DESC),
        }
        index_path = tmp_path / "tool_index.json"
        index_path.write_text(json.dumps(index_data, ensure_ascii=False), encoding="utf-8")

        retriever = HybridRetriever(alpha=0.5, index_path=str(index_path))
        assert retriever.available
        assert retriever._alpha == 0.5
        assert retriever.degraded is True  # Embedding 被 fixture 禁用

        for query, expected in ENGLISH_CASES:
            results = retriever.query(query, top_k=5)
            assert results, f"查询 {query!r} 应有结果"
            assert results[0][0] == expected, (
                f"查询 {query!r} 融合路 top1={results[0][0]} 应为 {expected}"
            )


# ════════════════════════════════════════════════════════════
#  回归保护:中文查询不因英文别名下降
# ════════════════════════════════════════════════════════════

class TestChineseRecallRegression:
    """追加英文别名后,中文查询召回不下降(不易:保护既有能力)"""

    def test_chinese_query_top1_unchanged_with_mixed_desc(self, pure_bm25, mixed_bm25):
        """核心回归:追加英文别名不改变中文查询的 top1 与 top5 召回集合

        Why 不断言完整排序:追加英文别名会改变文档长度,BM25 文档长度归一化
        (b=0.75) 使长文档分数略降,次名排序可能互换,但 top1 与召回集合不变,
        路由结果(top_k 截断 + 别名合并)不受影响。
        """
        for query, _ in CHINESE_CASES:
            pure_top1 = pure_bm25.search(query, top_k=10)[0][0]
            mixed_top1 = mixed_bm25.search(query, top_k=10)[0][0]
            assert mixed_top1 == pure_top1, (
                f"查询 {query!r} top1 不应因别名改变: "
                f"纯中文={pure_top1}, 混合={mixed_top1}"
            )
            pure_set = {d for d, _ in pure_bm25.search(query, top_k=5)}
            mixed_set = {d for d, _ in mixed_bm25.search(query, top_k=5)}
            assert mixed_set == pure_set, (
                f"查询 {query!r} top5 召回集合不应因别名改变: "
                f"纯中文={sorted(pure_set)}, 混合={sorted(mixed_set)}"
            )

    def test_chinese_query_still_recalls_expected_tool(self, mixed_bm25):
        """混合描述下,中文查询仍能在 top5 内召回期望工具(能力不丢失)"""
        for query, expected in CHINESE_CASES:
            rank = [d for d, _ in mixed_bm25.search(query, top_k=5)]
            assert expected in rank, f"查询 {query!r} top5={rank} 应包含 {expected}"
