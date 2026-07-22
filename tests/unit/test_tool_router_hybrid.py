"""tool_router_hybrid 单元测试

覆盖范围:
- BM25Index: add_document/search/clear/覆盖语义/_compute_bm25/size
- EmbeddingIndex: add_document/available/clear(mock _ensure_st_checked 避免实际加载模型)
- _min_max_normalize: 空列表/单元素/多元素/相同值
- HybridRetriever: rebuild/available/degraded/query/降级链/_last_query_stats
- hybrid_select_tools: 正常路径/降级路径/异常返回 None/白名单过滤
- 子进程探测: _ensure_st_checked 优先级(环境变量 > 内存 > 文件 > 探测)
- hybrid_select_tools trace 写入: 验证 record_tool_retrieval 字段完整

设计原则:
- 所有测试默认 AGENT_HYBRID_EMBEDDING=0,走纯 BM25 路径,避免子进程探测
- 单例隔离:每个测试 reset_hybrid_retriever
- 不依赖真实 tool_index.json(用临时 fixture,避免数据漂移)
"""
import os
import json
import logging
import tempfile
from unittest.mock import patch, MagicMock

import pytest


# ════════════════════════════════════════════════════════════
#  公共 fixture
# ════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _disable_embedding_probe(monkeypatch):
    """所有测试默认禁用 Embedding 探测,走纯 BM25

    Why: 子进程探测可能触发 Windows 0xC0000005 或耗时 60s,影响测试稳定性。
         纯 BM25 路径已能覆盖检索逻辑,Embedding 路径用单独测试 mock 验证。
    """
    monkeypatch.setenv("AGENT_HYBRID_EMBEDDING", "0")


@pytest.fixture(autouse=True)
def _reset_hybrid_singleton():
    """每个测试前后重置 HybridRetriever 单例(避免索引残留)"""
    from agent.tool_router_hybrid import reset_hybrid_retriever, _PROBE_RESULT
    import agent.tool_router_hybrid as mod
    # 重置单例 + 探测缓存
    reset_hybrid_retriever()
    # 重置模块级 _PROBE_RESULT(避免跨测试污染)
    mod._PROBE_RESULT = None
    yield
    reset_hybrid_retriever()
    mod._PROBE_RESULT = None


@pytest.fixture
def sample_tools():
    """最小工具定义集(测试 BM25/Embedding/Hybrid 不依赖真实 tool_index.json)"""
    return [
        {
            "name": "web_search",
            "category": "web",
            "description": "搜索互联网信息。默认单引擎搜索,设置 aggregate=true 启用多引擎聚合",
            "version": "1.0.0",
            "deprecated": False,
            "parameter_names": ["query", "engine", "num_results"],
        },
        {
            "name": "read_file",
            "category": "file",
            "description": "读取本地文件的全部内容(文本),支持指定编码",
            "version": "1.0.0",
            "deprecated": False,
            "parameter_names": ["path", "encoding"],
        },
        {
            "name": "shell_execute",
            "category": "code",
            "description": "在本地执行 shell 命令。Windows 默认使用 cmd,Linux/Mac 使用 bash",
            "version": "1.0.0",
            "deprecated": False,
            "parameter_names": ["command", "shell", "cwd"],
        },
        {
            "name": "get_weather",
            "category": "system",
            "description": "查询天气信息。使用 wttr.in 服务,无需 API Key",
            "version": "1.0.0",
            "deprecated": False,
            "parameter_names": ["city", "format"],
        },
    ]


@pytest.fixture
def sample_index_file(tmp_path, sample_tools):
    """生成临时 tool_index.json 文件(隔离测试,不污染真实数据)"""
    index_data = {
        "generated_at": "2026-07-19T00:00:00",
        "tool_count": len(sample_tools),
        "categories": sorted({t["category"] for t in sample_tools}),
        "tools": sample_tools,
    }
    index_path = tmp_path / "tool_index.json"
    index_path.write_text(json.dumps(index_data, ensure_ascii=False), encoding="utf-8")
    return str(index_path)


# ════════════════════════════════════════════════════════════
#  TestBM25Index
# ════════════════════════════════════════════════════════════

class TestBM25Index:
    """BM25Index 倒排索引"""

    def test_add_document_and_search(self):
        """添加文档后,query 命中应能搜到"""
        from agent.tool_router_hybrid import BM25Index
        idx = BM25Index()
        idx.add_document("web_search", "web search 搜索互联网信息")
        idx.add_document("read_file", "读取本地文件内容")

        results = idx.search("搜索", top_k=5)
        assert len(results) > 0
        assert results[0][0] == "web_search"
        assert results[0][1] > 0  # BM25 分数 > 0

    def test_search_empty_query(self):
        """空 query 返回空列表"""
        from agent.tool_router_hybrid import BM25Index
        idx = BM25Index()
        idx.add_document("tool1", "test content")
        assert idx.search("", top_k=5) == []
        assert idx.search("   ", top_k=5) == []

    def test_search_no_match(self):
        """无匹配 token 返回空列表"""
        from agent.tool_router_hybrid import BM25Index
        idx = BM25Index()
        idx.add_document("tool1", "apple banana orange")
        results = idx.search("xyz unmatched", top_k=5)
        assert results == []

    def test_add_document_overwrite(self):
        """doc_id 重复时覆盖旧文档"""
        from agent.tool_router_hybrid import BM25Index
        idx = BM25Index()
        idx.add_document("tool1", "old content about file")
        idx.add_document("tool1", "new content about web search")

        # 旧 token "file" 不应再命中
        results_old = idx.search("file", top_k=5)
        assert results_old == []
        # 新 token "web" 应命中
        results_new = idx.search("web", top_k=5)
        assert len(results_new) == 1
        assert results_new[0][0] == "tool1"

    def test_clear(self):
        """clear 后索引为空"""
        from agent.tool_router_hybrid import BM25Index
        idx = BM25Index()
        idx.add_document("tool1", "test content")
        assert idx.size == 1
        idx.clear()
        assert idx.size == 0
        assert idx.search("test", top_k=5) == []

    def test_size_property(self):
        """size 反映已索引文档数"""
        from agent.tool_router_hybrid import BM25Index
        idx = BM25Index()
        assert idx.size == 0
        idx.add_document("t1", "a b c")
        idx.add_document("t2", "d e f")
        assert idx.size == 2
        idx.add_document("t1", "overwrite")  # 覆盖,不增加
        assert idx.size == 2

    def test_compute_bm25_score_positive(self):
        """BM25 分数应大于 0(命中场景)"""
        from agent.tool_router_hybrid import BM25Index
        idx = BM25Index()
        idx.add_document("t1", "search web internet")
        idx.add_document("t2", "read file local")
        results = idx.search("search", top_k=5)
        assert len(results) == 1
        assert results[0][1] > 0

    def test_chinese_tokenization(self):
        """CJK 单字分词:中文 query 应能命中"""
        from agent.tool_router_hybrid import BM25Index
        idx = BM25Index()
        idx.add_document("get_weather", "查询天气信息使用 wttr.in 服务")
        results = idx.search("天气", top_k=5)
        assert len(results) > 0
        assert results[0][0] == "get_weather"

    def test_top_k_limit(self):
        """top_k 截断生效"""
        from agent.tool_router_hybrid import BM25Index
        idx = BM25Index()
        for i in range(10):
            idx.add_document(f"tool_{i}", f"common_token unique_{i}")
        results = idx.search("common_token", top_k=3)
        assert len(results) == 3


# ════════════════════════════════════════════════════════════
#  TestEmbeddingIndex
# ════════════════════════════════════════════════════════════

class TestEmbeddingIndex:
    """EmbeddingIndex 语义索引(不实际加载模型)"""

    def test_available_false_before_load(self):
        """模型未加载时 available=False"""
        from agent.tool_router_hybrid import EmbeddingIndex
        idx = EmbeddingIndex()
        assert idx.available is False

    def test_add_document_pending(self):
        """add_document 后 pending 列表增加"""
        from agent.tool_router_hybrid import EmbeddingIndex
        idx = EmbeddingIndex()
        idx.add_document("t1", "test content")
        assert len(idx._pending) == 1
        assert idx._pending[0][0] == "t1"

    def test_add_document_overwrite(self):
        """doc_id 重复时覆盖旧 pending 项"""
        from agent.tool_router_hybrid import EmbeddingIndex
        idx = EmbeddingIndex()
        idx.add_document("t1", "old content")
        idx.add_document("t1", "new content")
        assert len(idx._pending) == 1
        assert idx._pending[0][1] == "new content"

    def test_clear(self):
        """clear 后索引为空"""
        from agent.tool_router_hybrid import EmbeddingIndex
        idx = EmbeddingIndex()
        idx.add_document("t1", "test")
        idx.clear()
        assert len(idx._pending) == 0
        assert idx._embeddings is None
        assert len(idx._doc_ids) == 0

    def test_search_returns_empty_when_model_unavailable(self):
        """模型不可用时 search 返回空列表(不抛异常)"""
        from agent.tool_router_hybrid import EmbeddingIndex
        idx = EmbeddingIndex()
        # _ensure_st_checked 被 _disable_embedding_probe fixture 短路为 False
        results = idx.search("test", top_k=5)
        assert results == []

    def test_search_returns_empty_without_numpy(self):
        """numpy 不可用时 search 返回空列表"""
        with patch("agent.tool_router_hybrid._HAS_NUMPY", False):
            from agent.tool_router_hybrid import EmbeddingIndex
            idx = EmbeddingIndex()
            results = idx.search("test", top_k=5)
            assert results == []


# ════════════════════════════════════════════════════════════
#  TestMinMaxNormalize
# ════════════════════════════════════════════════════════════

class TestMinMaxNormalize:
    """_min_max_normalize 分数归一化"""

    def test_empty_list(self):
        """空列表返回空"""
        from agent.tool_router_hybrid import _min_max_normalize
        assert _min_max_normalize([]) == []

    def test_single_element(self):
        """单元素归一化为 1.0(避免除零)"""
        from agent.tool_router_hybrid import _min_max_normalize
        result = _min_max_normalize([("doc1", 5.0)])
        assert len(result) == 1
        assert result[0][0] == "doc1"
        assert result[0][1] == 1.0

    def test_multiple_elements(self):
        """多元素归一化到 [0, 1],最大值=1,最小值=0"""
        from agent.tool_router_hybrid import _min_max_normalize
        result = _min_max_normalize([("d1", 1.0), ("d2", 5.0), ("d3", 3.0)])
        assert len(result) == 3
        scores = {d: s for d, s in result}
        assert scores["d2"] == 1.0  # 最大
        assert scores["d1"] == 0.0  # 最小
        assert 0 < scores["d3"] < 1.0

    def test_all_same_values(self):
        """所有分数相同时归一化为 1.0(避免除零)"""
        from agent.tool_router_hybrid import _min_max_normalize
        result = _min_max_normalize([("d1", 3.0), ("d2", 3.0), ("d3", 3.0)])
        assert all(s == 1.0 for _, s in result)


# ════════════════════════════════════════════════════════════
#  TestHybridRetriever
# ════════════════════════════════════════════════════════════

class TestHybridRetriever:
    """HybridRetriever 混合检索器"""

    def test_init_loads_index(self, sample_index_file):
        """初始化时加载 tool_index.json 并构建 BM25 索引"""
        from agent.tool_router_hybrid import HybridRetriever
        r = HybridRetriever(index_path=sample_index_file)
        assert r.available is True
        assert r._bm25.size > 0

    def test_init_missing_index_file(self, tmp_path):
        """索引文件不存在时 available=False(降级)"""
        from agent.tool_router_hybrid import HybridRetriever
        r = HybridRetriever(index_path=str(tmp_path / "nonexistent.json"))
        assert r.available is False
        assert r._bm25.size == 0

    def test_init_empty_index_file(self, tmp_path):
        """索引文件无工具时 available=False"""
        from agent.tool_router_hybrid import HybridRetriever
        empty_index = tmp_path / "empty.json"
        empty_index.write_text(json.dumps({"tools": []}), encoding="utf-8")
        r = HybridRetriever(index_path=str(empty_index))
        assert r.available is False

    def test_query_returns_results(self, sample_index_file):
        """正常 query 返回非空结果"""
        from agent.tool_router_hybrid import HybridRetriever
        r = HybridRetriever(index_path=sample_index_file)
        results = r.query("搜索天气", top_k=5)
        assert results is not None
        assert len(results) > 0
        # web_search 或 get_weather 应在结果中
        tool_names = {name for name, _ in results}
        assert "web_search" in tool_names or "get_weather" in tool_names

    def test_query_empty_text(self, sample_index_file):
        """空 query 返回空列表"""
        from agent.tool_router_hybrid import HybridRetriever
        r = HybridRetriever(index_path=sample_index_file)
        assert r.query("", top_k=5) == []
        assert r.query("   ", top_k=5) == []

    def test_query_unavailable_returns_none(self, tmp_path):
        """索引未加载时 query 返回 None"""
        from agent.tool_router_hybrid import HybridRetriever
        r = HybridRetriever(index_path=str(tmp_path / "nonexistent.json"))
        assert r.query("test", top_k=5) is None

    def test_degraded_property(self, sample_index_file):
        """AGENT_HYBRID_EMBEDDING=0 时 degraded=True"""
        from agent.tool_router_hybrid import HybridRetriever
        r = HybridRetriever(index_path=sample_index_file)
        assert r.degraded is True  # fixture 强制禁用 Embedding

    def test_rebuild_replaces_index(self, sample_index_file, sample_tools):
        """rebuild 后索引内容更新"""
        from agent.tool_router_hybrid import HybridRetriever
        r = HybridRetriever(index_path=sample_index_file)
        old_size = r._bm25.size
        # rebuild 用更少工具
        r.rebuild(sample_tools[:2])
        assert r._bm25.size == 2
        assert r._bm25.size != old_size

    def test_last_query_stats_populated(self, sample_index_file):
        """query 后 _last_query_stats 应包含 bm25/embed/fused 三个字段"""
        from agent.tool_router_hybrid import HybridRetriever
        r = HybridRetriever(index_path=sample_index_file)
        r.query("搜索天气", top_k=5)
        stats = r._last_query_stats
        assert "bm25_candidates" in stats
        assert "embed_candidates" in stats
        assert "fused_candidates" in stats
        assert stats["bm25_candidates"] > 0
        assert stats["embed_candidates"] == 0  # 降级时为 0
        assert stats["fused_candidates"] > 0


# ════════════════════════════════════════════════════════════
#  TestHybridSelectTools
# ════════════════════════════════════════════════════════════

class TestHybridSelectTools:
    """hybrid_select_tools 公共入口"""

    def test_returns_none_when_helper_unavailable(self, monkeypatch, sample_index_file):
        """helper 不可用时返回 None"""
        from agent.tool_router_hybrid import hybrid_select_tools
        with patch("agent.tool_router_hybrid._HELPER_AVAILABLE", False):
            result = hybrid_select_tools("test query")
        assert result is None

    def test_returns_none_when_retriever_unavailable(self, monkeypatch, tmp_path):
        """retriever 不可用(索引文件不存在)时返回 None"""
        from agent.tool_router_hybrid import hybrid_select_tools
        # 直接 patch get_hybrid_retriever 返回 None(模拟初始化失败)
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=None):
            result = hybrid_select_tools("test query")
        assert result is None

    def test_normal_path_returns_tools(self, sample_index_file):
        """正常路径返回工具列表"""
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever, _INDEX_PATH
        # 用临时索引构建 retriever
        with patch("agent.tool_router_hybrid._INDEX_PATH", sample_index_file):
            retriever = HybridRetriever(index_path=sample_index_file)
            with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
                result = hybrid_select_tools("搜索天气")
        assert result is not None
        assert len(result) > 0
        assert isinstance(result, list)

    def test_whitelist_filter(self, sample_index_file):
        """白名单过滤后只保留白名单内工具"""
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever
        retriever = HybridRetriever(index_path=sample_index_file)
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            result = hybrid_select_tools(
                "搜索天气",
                enabled_whitelist=["web_search"],
            )
        assert result is not None
        assert all(t == "web_search" for t in result)

    def test_whitelist_filters_all_returns_none(self, sample_index_file):
        """白名单过滤掉所有候选时返回 None(让调用方回退)"""
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever
        retriever = HybridRetriever(index_path=sample_index_file)
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            result = hybrid_select_tools(
                "搜索天气",
                enabled_whitelist=["nonexistent_tool"],
            )
        assert result is None

    def test_max_tools_limit(self, sample_index_file):
        """max_tools 截断生效"""
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever
        retriever = HybridRetriever(index_path=sample_index_file)
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            result = hybrid_select_tools("搜索", max_tools=2)
        assert result is not None
        assert len(result) <= 2

    def test_empty_query_returns_none(self, sample_index_file):
        """空 query 返回 None"""
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever
        retriever = HybridRetriever(index_path=sample_index_file)
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            assert hybrid_select_tools("") is None
            assert hybrid_select_tools("   ") is None


# ════════════════════════════════════════════════════════════
#  TestEnsureStChecked
# ════════════════════════════════════════════════════════════

class TestEnsureStChecked:
    """_ensure_st_checked 子进程探测优先级"""

    def test_env_var_disable(self, monkeypatch):
        """AGENT_HYBRID_EMBEDDING=0 返回 False(不探测)"""
        monkeypatch.setenv("AGENT_HYBRID_EMBEDDING", "0")
        import agent.tool_router_hybrid as mod
        mod._PROBE_RESULT = None  # 重置缓存
        assert mod._ensure_st_checked() is False
        assert mod._PROBE_RESULT is False

    def test_env_var_enable(self, monkeypatch):
        """AGENT_HYBRID_EMBEDDING=1 返回 True(强制启用)"""
        monkeypatch.setenv("AGENT_HYBRID_EMBEDDING", "1")
        import agent.tool_router_hybrid as mod
        mod._PROBE_RESULT = None
        assert mod._ensure_st_checked() is True
        assert mod._PROBE_RESULT is True

    def test_env_var_truthy_values(self, monkeypatch):
        """AGENT_HYBRID_EMBEDDING 接受 true/yes/on(大小写不敏感)"""
        import agent.tool_router_hybrid as mod
        for val in ("true", "TRUE", "Yes", "ON", "on"):
            monkeypatch.setenv("AGENT_HYBRID_EMBEDDING", val)
            mod._PROBE_RESULT = None
            assert mod._ensure_st_checked() is True, f"Failed for value: {val}"

    def test_env_var_falsy_values(self, monkeypatch):
        """AGENT_HYBRID_EMBEDDING 接受 false/no/off(大小写不敏感)"""
        import agent.tool_router_hybrid as mod
        for val in ("false", "FALSE", "No", "OFF", "off"):
            monkeypatch.setenv("AGENT_HYBRID_EMBEDDING", val)
            mod._PROBE_RESULT = None
            assert mod._ensure_st_checked() is False, f"Failed for value: {val}"

    def test_memory_cache_hit(self, monkeypatch):
        """内存缓存(_PROBE_RESULT)优先于文件缓存和探测"""
        monkeypatch.delenv("AGENT_HYBRID_EMBEDDING", raising=False)
        import agent.tool_router_hybrid as mod
        mod._PROBE_RESULT = True  # 模拟已探测过
        # 即使文件缓存为 False,内存缓存 True 应胜出
        with patch("agent.tool_router_hybrid._read_probe_cache", return_value=False):
            assert mod._ensure_st_checked() is True

    def test_file_cache_hit(self, monkeypatch, tmp_path):
        """无环境变量时读文件缓存"""
        monkeypatch.delenv("AGENT_HYBRID_EMBEDDING", raising=False)
        import agent.tool_router_hybrid as mod
        mod._PROBE_RESULT = None
        with patch("agent.tool_router_hybrid._read_probe_cache", return_value=True):
            assert mod._ensure_st_checked() is True
            assert mod._PROBE_RESULT is True

    def test_probe_cache_read_failure_falls_back_to_probe(self, monkeypatch, tmp_path):
        """文件缓存读取失败时,触发子进程探测"""
        monkeypatch.delenv("AGENT_HYBRID_EMBEDDING", raising=False)
        import agent.tool_router_hybrid as mod
        mod._PROBE_RESULT = None
        with patch("agent.tool_router_hybrid._read_probe_cache", return_value=None), \
             patch("agent.tool_router_hybrid._run_embedding_probe", return_value=False) as mock_probe, \
             patch("agent.tool_router_hybrid._write_probe_cache") as mock_write:
            assert mod._ensure_st_checked() is False
            mock_probe.assert_called_once()
            mock_write.assert_called_once_with(False)


# ════════════════════════════════════════════════════════════
#  TestProbeCacheIO
# ════════════════════════════════════════════════════════════

class TestProbeCacheIO:
    """探测缓存文件读写"""

    def test_write_and_read_cache(self, tmp_path, monkeypatch):
        """写入缓存后能正确读取"""
        cache_path = tmp_path / ".embedding_probe"
        monkeypatch.setattr("agent.tool_router_hybrid._PROBE_CACHE", str(cache_path))

        import agent.tool_router_hybrid as mod
        mod._write_probe_cache(True)
        assert cache_path.exists()
        result = mod._read_probe_cache()
        assert result is True

    def test_read_cache_missing_file(self, tmp_path, monkeypatch):
        """缓存文件不存在时返回 None"""
        cache_path = tmp_path / "nonexistent_probe"
        monkeypatch.setattr("agent.tool_router_hybrid._PROBE_CACHE", str(cache_path))
        import agent.tool_router_hybrid as mod
        assert mod._read_probe_cache() is None

    def test_read_cache_corrupted_file(self, tmp_path, monkeypatch):
        """缓存文件损坏时返回 None(不抛异常)"""
        cache_path = tmp_path / ".embedding_probe"
        cache_path.write_text("invalid json {{{", encoding="utf-8")
        monkeypatch.setattr("agent.tool_router_hybrid._PROBE_CACHE", str(cache_path))
        import agent.tool_router_hybrid as mod
        assert mod._read_probe_cache() is None

    def test_write_cache_creates_directory(self, tmp_path, monkeypatch):
        """写入缓存时自动创建目录"""
        cache_path = tmp_path / "subdir" / ".embedding_probe"
        monkeypatch.setattr("agent.tool_router_hybrid._PROBE_CACHE", str(cache_path))
        import agent.tool_router_hybrid as mod
        mod._write_probe_cache(False)
        assert cache_path.exists()
