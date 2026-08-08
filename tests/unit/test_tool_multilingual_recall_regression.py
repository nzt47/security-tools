"""混合语言检索回归测试 — 中英混合查询 + 非英文工具别名通用性

背景:
  为英文查询召回稳定,在工具 description 追加英文检索别名(语义独占分配),
  形成混合语言描述。本测试把 scripts/dev/verify_english_recall.py 的
  混合语言场景(组2 中英混合 / 组4 非英文别名通用性)固化为独立回归用例:

1. 中英混合查询(极端混合场景):查询内中英混排(如 'extract pdf 里的文本'),
   中文 token 命中描述、英文 token 命中别名,双路互补,top1 必须命中期望工具
2. 非英文工具(日文/法文描述)别名通用性:
   - alias 正向:非英文描述 + 英文别名 → 英文查询 top1 命中(别名召回)
   - native 正向:原语言查询 → 命中同语言描述(能力不丢失)
   - negative 负向:英文查询不得命中无别名的非英文描述(零字面失效守卫)
3. 融合路(alpha=0.5)中英混合查询 top1(完整链路 + alpha 参数透传)

设计原则(与 test_tool_hybrid_lang_recall.py 一致):
- 不依赖真实 tool_index.json(内存工具集,避免数据漂移,回归可重复)
- 复用 agent.tool_router_hybrid.BM25Index / HybridRetriever(真实实现,非 mock)
- 默认 AGENT_HYBRID_EMBEDDING=0,走纯 BM25,秒级回归,避免子进程探测/模型加载
- 单例隔离:每个测试重置 HybridRetriever
"""
import json

import pytest


# ════════════════════════════════════════════════════════════
#  中英混合场景:10 个核心工具的内存索引(中文描述 + 英文别名)
#  description 与 data/tool_definitions/*.yaml 应用方案一致
# ════════════════════════════════════════════════════════════

MIXED_LANG_DESC: dict[str, str] = {
    "read_pdf": "读取 PDF 文件中的文本内容。Extract text from PDF files, parse PDF documents",
    "merge_pdf": "合并多个 PDF 文件为一个。Merge multiple PDF files into one, combine PDFs",
    "split_pdf": "拆分 PDF 文件为多个独立的 PDF。Split a PDF into separate pages, divide PDF",
    "get_pdf_info": "获取 PDF 文件的元信息。Get PDF metadata, page count, document info",
    "web_search": "搜索互联网信息。Search the web, find information online",
    "web_get": "发送 HTTP GET 请求获取网页内容。Fetch a web page by URL, get page content",
    "shell_execute": "在本地执行 shell 命令。Run shell commands, execute terminal commands",
    "read_file": "读取本地文件的全部内容。Read the content of a local text file",
    "search_files": "按文件名模式搜索文件。Find files by name or pattern, glob search",
    "get_weather": "查询天气信息。Get weather forecast, temperature for a city",
}

_PARAMS: dict[str, list[str]] = {
    "read_pdf": ["path", "pages"],
    "merge_pdf": ["paths", "output_path"],
    "split_pdf": ["path", "output_dir"],
    "get_pdf_info": ["path"],
    "web_search": ["query", "engine"],
    "web_get": ["url"],
    "shell_execute": ["command", "shell"],
    "read_file": ["path", "encoding"],
    "search_files": ["pattern", "root"],
    "get_weather": ["city", "format"],
}

# 中英混合查询(极端混合场景):查询内中英混排,与 verify_english_recall.py 组2 一致
MIXED_CASES: list[tuple[str, str]] = [
    ("extract pdf 里的文本", "read_pdf"),
    ("把多个 pdf merge 成一个文件", "merge_pdf"),
    ("split 这个 pdf 成多页", "split_pdf"),
    ("查 pdf 的 metadata 信息", "get_pdf_info"),
    ("在 web 上 search 信息", "web_search"),
    ("fetch 这个 url", "web_get"),
    ("跑个 shell command", "shell_execute"),
    ("读取本地 file 内容", "read_file"),
    ("按 pattern 找文件", "search_files"),
    ("get 北京的 weather", "get_weather"),
]


# ════════════════════════════════════════════════════════════
#  非英文工具(日文/法文)模拟数据 — 验证别名方案语言通用性
#  ± 英文别名:有别名应被英文召回,无别名不应被英文召回
# ════════════════════════════════════════════════════════════

MULTILINGUAL_TOOLS: list[dict] = [
    {
        "name": "ja_pdf",
        "description": "PDFファイルからテキストを抽出します。Extract text from PDF files, parse pdf documents",
        "lang": "ja",
        "has_alias": True,
    },
    {
        "name": "fr_pdf",
        "description": "Extraire le texte de fichiers PDF. Extract text from PDF files, parse pdf documents",
        "lang": "fr",
        "has_alias": True,
    },
    {
        "name": "ja_weather",
        "description": "都市の天気と気温を取得します",
        "lang": "ja",
        "has_alias": False,
    },
    {
        "name": "fr_weather",
        "description": "Obtenir la météo et la température d'une ville",
        "lang": "fr",
        "has_alias": False,
    },
]

# (query, kind, allowed) kind: alias=英文查询命中带别名工具 / native=原语言匹配 / negative=负向
MULTILINGUAL_CASES: list[tuple[str, str, set[str]]] = [
    ("extract text from pdf", "alias", {"ja_pdf", "fr_pdf"}),
    ("parse a pdf document", "alias", {"ja_pdf", "fr_pdf"}),
    ("météo de paris", "native", {"fr_weather"}),
    ("get weather in tokyo", "negative", set()),
]


# ════════════════════════════════════════════════════════════
#  公共 fixture(与 test_tool_hybrid_lang_recall.py 一致)
# ════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _disable_embedding_probe(monkeypatch):
    """所有测试禁用 Embedding 探测,走纯 BM25 路径(秒级回归)"""
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


# ════════════════════════════════════════════════════════════
#  工具构造与索引构建(与 verify_english_recall.py 一致)
# ════════════════════════════════════════════════════════════

def _make_tools(desc_map: dict[str, str]) -> list[dict]:
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
#  组1:中英混合查询(极端混合场景)top1 召回
# ════════════════════════════════════════════════════════════

class TestMixedCnEnQueries:
    """中英混合查询在混合语言描述索引下 top1 全对"""

    @pytest.fixture
    def mixed_bm25(self):
        return _build_bm25(_make_tools(MIXED_LANG_DESC))

    def test_mixed_cn_en_top1_all_correct(self, mixed_bm25):
        """10 条中英混排查询 top1 全部命中期望工具"""
        for query, expected in MIXED_CASES:
            results = mixed_bm25.search(query, top_k=10)
            assert results, f"查询 {query!r} 应有结果"
            assert results[0][0] == expected, (
                f"查询 {query!r} top1={results[0][0]} 应为 {expected}"
            )

    def test_mixed_cn_en_hybrid_alpha_05_top1_correct(self, tmp_path):
        """融合路(alpha=0.5)中英混合查询 top1 命中(完整链路 + alpha 透传)"""
        from agent.tool_router_hybrid import HybridRetriever

        index_data = {
            "generated_at": "2026-08-08T00:00:00",
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

        for query, expected in MIXED_CASES:
            results = retriever.query(query, top_k=5) or []
            assert results, f"查询 {query!r} 融合路应有结果"
            assert results[0][0] == expected, (
                f"查询 {query!r} 融合路 top1={results[0][0]} 应为 {expected}"
            )


# ════════════════════════════════════════════════════════════
#  组2:非英文工具(日文/法文)别名通用性
# ════════════════════════════════════════════════════════════

class TestMultilingualAliasGenerality:
    """别名方案对非英文描述语言通用:alias 正向 / native 正向 / negative 负向"""

    @pytest.fixture
    def multi_bm25(self):
        return _build_bm25(MULTILINGUAL_TOOLS)

    def test_alias_recall_english_query_hits_alias_tool(self, multi_bm25):
        """英文查询命中带英文别名的非英文描述工具(别名召回)"""
        for query, kind, allowed in MULTILINGUAL_CASES:
            if kind != "alias":
                continue
            results = multi_bm25.search(query, top_k=5)
            assert results, f"查询 {query!r} 应有结果"
            assert results[0][0] in allowed, (
                f"查询 {query!r} top1={results[0][0]} 应命中带别名工具 {sorted(allowed)}"
            )

    def test_native_language_match_preserved(self, multi_bm25):
        """原语言查询命中同语言描述(能力不丢失,不依赖别名)"""
        for query, kind, allowed in MULTILINGUAL_CASES:
            if kind != "native":
                continue
            results = multi_bm25.search(query, top_k=5)
            assert results, f"查询 {query!r} 应有结果"
            assert results[0][0] in allowed, (
                f"查询 {query!r} top1={results[0][0]} 应命中 {sorted(allowed)}"
            )

    def test_negative_no_alias_no_leak(self, multi_bm25):
        """负向:英文查询不得命中无别名的非英文描述(零字面失效守卫)"""
        for query, kind, allowed in MULTILINGUAL_CASES:
            if kind != "negative":
                continue
            top5 = [d for d, _ in multi_bm25.search(query, top_k=5)]
            leak = [n for n in top5 if n in {"ja_weather", "fr_weather"}]
            assert not leak, (
                f"查询 {query!r} 不应命中无别名非英文工具,实际 top5={top5}"
            )
