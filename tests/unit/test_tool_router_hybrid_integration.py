"""tool_router_hybrid 集成测试

覆盖范围:
- 验证 hybrid_select_tools 调用产生 record_tool_retrieval 结构化日志(用 caplog)
- 验证 trace 字段完整:query_hash/top_k/latency_ms/bm25_candidates/embed_candidates/fused_candidates/alpha/degraded/tools_preview
- 验证降级路径:AGENT_HYBRID_EMBEDDING=0 时 degraded=true
- 验证集成点 or fallback:hybrid_select_tools 返回 None 时,get_tools_for_input 兜底
- 验证 ToolTraceRecorder.record_tool_retrieval 方法存在且可调用

设计原则:
- 用真实 tool_index.json(70 工具)而非临时 fixture,验证端到端通路
- 所有测试默认 AGENT_HYBRID_EMBEDDING=0,走纯 BM25 路径
- caplog 捕获 logger.info 输出,解析 JSON 验证字段完整性
"""
import json
import logging
from unittest.mock import patch

import pytest


# ════════════════════════════════════════════════════════════
#  公共 fixture
# ════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _disable_embedding_probe(monkeypatch):
    """所有集成测试默认禁用 Embedding 探测,走纯 BM25"""
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


@pytest.fixture(autouse=True)
def _reset_tool_trace_singleton():
    """每个测试前后重置 ToolTraceRecorder 单例(避免日志污染)"""
    from agent.observability.tool_trace import ToolTraceRecorder
    ToolTraceRecorder.reset()
    yield
    ToolTraceRecorder.reset()


@pytest.fixture
def real_index_path():
    """真实 tool_index.json 路径(70 工具,端到端验证)"""
    import os
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, "data", "tool_index.json")


# ════════════════════════════════════════════════════════════
#  TestRecordToolRetrievalTrace
# ════════════════════════════════════════════════════════════

class TestRecordToolRetrievalTrace:
    """验证 hybrid_select_tools 产生完整的 record_tool_retrieval trace"""

    def test_trace_emitted_with_all_fields(self, real_index_path, caplog):
        """调用 hybrid_select_tools 后应产生 tool_retrieval trace,字段完整"""
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever

        # 用真实索引构建 retriever
        retriever = HybridRetriever(index_path=real_index_path)
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            with caplog.at_level(logging.INFO, logger="agent.observability.tool_trace"):
                result = hybrid_select_tools("搜索天气")

        # 验证返回了工具列表
        assert result is not None
        assert len(result) > 0

        # 验证 trace 日志产生
        trace_logs = [
            r for r in caplog.records
            if "tool_retrieval" in getattr(r, "message", "")
        ]
        assert len(trace_logs) >= 1, "应产生至少 1 条 tool_retrieval trace"

        # 解析 trace JSON
        trace_msg = trace_logs[-1].message
        trace_data = json.loads(trace_msg)
        assert trace_data["module_name"] == "tool_trace"
        assert trace_data["action"] == "tool_retrieval"

        # 验证所有必需字段存在
        required_fields = {
            "user_input_hash", "top_k", "latency_ms",
            "bm25_candidates", "embed_candidates", "fused_candidates",
            "alpha", "degraded", "tools_preview",
        }
        assert required_fields.issubset(trace_data.keys()), \
            f"缺少字段: {required_fields - trace_data.keys()}"

        # 验证字段类型与值合理性
        assert isinstance(trace_data["user_input_hash"], str)
        assert len(trace_data["user_input_hash"]) == 16  # SHA256 前 16 位
        assert trace_data["top_k"] == 10  # 默认值
        assert trace_data["latency_ms"] >= 0
        assert isinstance(trace_data["bm25_candidates"], int)
        assert isinstance(trace_data["embed_candidates"], int)
        assert isinstance(trace_data["fused_candidates"], int)
        assert trace_data["alpha"] == 0.5  # 默认值
        assert trace_data["degraded"] is True  # AGENT_HYBRID_EMBEDDING=0
        assert isinstance(trace_data["tools_preview"], list)

    def test_trace_query_hash_desensitized(self, real_index_path, caplog):
        """trace 中的 query 应脱敏(只存 hash,不存原文)"""
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever

        retriever = HybridRetriever(index_path=real_index_path)
        sensitive_query = "搜索mypassword123天气"
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            with caplog.at_level(logging.INFO, logger="agent.observability.tool_trace"):
                hybrid_select_tools(sensitive_query)

        # 验证原文不出现在日志中
        trace_logs = [
            r for r in caplog.records
            if "tool_retrieval" in getattr(r, "message", "")
        ]
        assert len(trace_logs) >= 1
        trace_msg = trace_logs[-1].message
        # 原文不应出现在 trace 中(只存 hash)
        assert "mypassword123" not in trace_msg
        # hash 应存在
        trace_data = json.loads(trace_msg)
        assert "user_input_hash" in trace_data
        assert len(trace_data["user_input_hash"]) == 16

    def test_trace_degraded_flag_true_when_embedding_disabled(self, real_index_path, caplog):
        """AGENT_HYBRID_EMBEDDING=0 时 trace 中 degraded=True"""
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever

        retriever = HybridRetriever(index_path=real_index_path)
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            with caplog.at_level(logging.INFO, logger="agent.observability.tool_trace"):
                hybrid_select_tools("搜索")

        trace_logs = [
            r for r in caplog.records
            if "tool_retrieval" in getattr(r, "message", "")
        ]
        trace_data = json.loads(trace_logs[-1].message)
        assert trace_data["degraded"] is True
        # 降级时 embed_candidates 应为 0
        assert trace_data["embed_candidates"] == 0

    def test_trace_bm25_candidates_positive(self, real_index_path, caplog):
        """trace 中 bm25_candidates 应 > 0(有候选)"""
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever

        retriever = HybridRetriever(index_path=real_index_path)
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            with caplog.at_level(logging.INFO, logger="agent.observability.tool_trace"):
                hybrid_select_tools("搜索天气")

        trace_logs = [
            r for r in caplog.records
            if "tool_retrieval" in getattr(r, "message", "")
        ]
        trace_data = json.loads(trace_logs[-1].message)
        assert trace_data["bm25_candidates"] > 0
        assert trace_data["fused_candidates"] > 0

    def test_trace_tools_preview_max_10(self, real_index_path, caplog):
        """tools_preview 最多 10 个工具"""
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever

        retriever = HybridRetriever(index_path=real_index_path)
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            with caplog.at_level(logging.INFO, logger="agent.observability.tool_trace"):
                hybrid_select_tools("搜索读取执行", max_tools=25)

        trace_logs = [
            r for r in caplog.records
            if "tool_retrieval" in getattr(r, "message", "")
        ]
        trace_data = json.loads(trace_logs[-1].message)
        assert len(trace_data["tools_preview"]) <= 10


# ════════════════════════════════════════════════════════════
#  TestToolTraceRecorderMethod
# ════════════════════════════════════════════════════════════

class TestToolTraceRecorderMethod:
    """直接验证 ToolTraceRecorder.record_tool_retrieval 方法"""

    def test_method_exists(self):
        """record_tool_retrieval 方法应存在"""
        from agent.observability.tool_trace import ToolTraceRecorder
        assert hasattr(ToolTraceRecorder, "record_tool_retrieval")
        assert callable(ToolTraceRecorder.record_tool_retrieval)

    def test_method_emits_structured_log(self, caplog, tmp_path):
        """直接调用方法应产生结构化日志"""
        from agent.observability.tool_trace import ToolTraceRecorder

        recorder = ToolTraceRecorder(db_path=str(tmp_path / "test.db"))
        with caplog.at_level(logging.INFO, logger="agent.observability.tool_trace"):
            recorder.record_tool_retrieval(
                query="测试查询",
                top_k=5,
                latency_ms=12.34,
                bm25_candidates=8,
                embed_candidates=3,
                fused_candidates=10,
                alpha=0.5,
                degraded=False,
                tools_preview=["tool_a", "tool_b"],
            )

        trace_logs = [
            r for r in caplog.records
            if "tool_retrieval" in getattr(r, "message", "")
        ]
        assert len(trace_logs) == 1
        trace_data = json.loads(trace_logs[0].message)
        assert trace_data["action"] == "tool_retrieval"
        assert trace_data["top_k"] == 5
        assert trace_data["latency_ms"] == 12.34
        assert trace_data["bm25_candidates"] == 8
        assert trace_data["embed_candidates"] == 3
        assert trace_data["fused_candidates"] == 10
        assert trace_data["alpha"] == 0.5
        assert trace_data["degraded"] is False
        assert trace_data["tools_preview"] == ["tool_a", "tool_b"]

    def test_method_does_not_persist_to_sqlite(self, tmp_path):
        """record_tool_retrieval 不应持久化到 SQLite(仅结构化日志)"""
        from agent.observability.tool_trace import ToolTraceRecorder

        db_path = tmp_path / "test.db"
        recorder = ToolTraceRecorder(db_path=str(db_path))
        recorder.record_tool_retrieval(
            query="测试",
            top_k=5,
            latency_ms=1.0,
            bm25_candidates=1,
            embed_candidates=0,
            fused_candidates=1,
            alpha=0.5,
            degraded=True,
            tools_preview=["t1"],
        )
        # 等待后台 writer 处理完队列
        recorder.flush(timeout=2.0)

        # 验证 SQLite 中无 tool_retrieval 记录(只有 ToolTraceRecord 持久化)
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute("SELECT COUNT(*) FROM tool_traces").fetchone()
            assert rows[0] == 0, "record_tool_retrieval 不应写入 tool_traces 表"
        finally:
            conn.close()


# ════════════════════════════════════════════════════════════
#  TestOrFallbackIntegration
# ════════════════════════════════════════════════════════════

class TestOrFallbackIntegration:
    """验证集成点 or fallback 模式:hybrid 失败时 get_tools_for_input 兜底"""

    def test_hybrid_returns_none_falls_back_to_get_tools_for_input(self):
        """hybrid_select_tools 返回 None 时,get_tools_for_input 应兜底成功"""
        import agent.tool_router_hybrid as hybrid_mod
        from agent.tool_router import get_tools_for_input

        # Why: from-import 拿到的是本地引用,patch 字符串路径不影响它,
        # 必须用 patch.object 修改模块属性,才能让模块内调用看到 None
        with patch.object(hybrid_mod, "hybrid_select_tools", return_value=None):
            hybrid_result = hybrid_mod.hybrid_select_tools("搜索天气")
            fallback_result = get_tools_for_input("搜索天气")

        # hybrid 返回 None,fallback 应返回非空列表
        assert hybrid_result is None
        assert fallback_result is not None
        assert len(fallback_result) > 0
        # fallback 应包含 web_search 等相关工具
        assert "web_search" in fallback_result

    def test_hybrid_succeeds_skips_get_tools_for_input(self, real_index_path):
        """hybrid 成功时,or 短路跳过 get_tools_for_input"""
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever

        retriever = HybridRetriever(index_path=real_index_path)
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            hybrid_result = hybrid_select_tools("搜索天气")

        # hybrid 应成功返回
        assert hybrid_result is not None
        assert len(hybrid_result) > 0

    def test_or_pattern_returns_first_truthy(self, real_index_path):
        """or 模式:hybrid 返回非空列表时,不应调用 get_tools_for_input"""
        from agent.tool_router import get_tools_for_input
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever

        retriever = HybridRetriever(index_path=real_index_path)
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            hybrid_result = hybrid_select_tools("搜索天气")

        # 模拟 or fallback:hybrid 成功,不调用 get_tools_for_input
        with patch("agent.tool_router.get_tools_for_input") as mock_fallback:
            result = hybrid_result or mock_fallback("搜索天气")
        # hybrid 成功,mock_fallback 不应被调用
        assert not mock_fallback.called
        assert result is hybrid_result


# ════════════════════════════════════════════════════════════
#  TestEndToEndQuery
# ════════════════════════════════════════════════════════════

class TestEndToEndQuery:
    """端到端查询测试(真实 tool_index.json)"""

    def test_query_returns_relevant_tools(self, real_index_path):
        """查询「搜索天气」应返回 web_search 和 get_weather"""
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever

        retriever = HybridRetriever(index_path=real_index_path)
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            result = hybrid_select_tools("搜索天气", max_tools=10)

        assert result is not None
        assert "web_search" in result
        assert "get_weather" in result

    def test_query_alias_merge(self, real_index_path):
        """别名合并:查询「执行命令」应返回 shell_execute,不返回 run_program"""
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever

        retriever = HybridRetriever(index_path=real_index_path)
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            result = hybrid_select_tools("执行 shell 命令", max_tools=25)

        assert result is not None
        assert "shell_execute" in result
        # run_program 是 shell_execute 的别名,应被移除
        assert "run_program" not in result

    def test_query_latency_under_50ms(self, real_index_path):
        """单次 query 延迟应 < 50ms(性能验收标准)"""
        import time
        from agent.tool_router_hybrid import hybrid_select_tools, HybridRetriever

        retriever = HybridRetriever(index_path=real_index_path)
        # 预热(第一次查询会触发 BM25 索引构建)
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            hybrid_select_tools("预热查询")

        # 实际测量
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=retriever):
            t0 = time.perf_counter()
            hybrid_select_tools("搜索天气")
            elapsed_ms = (time.perf_counter() - t0) * 1000

        assert elapsed_ms < 50, f"query 延迟 {elapsed_ms:.2f}ms 超过 50ms 阈值"
