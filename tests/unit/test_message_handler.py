"""MessageHandler / ResponseBuilder / TraceStore 测试"""

import pytest
from agent.orchestrator.message_handler import MessageHandler
from agent.orchestrator.response_builder import ResponseBuilder, Response
from agent.observability.subscriber import TraceStore, TraceSpan, trace_store


class TestMessageHandler:
    def test_parse(self):
        result = MessageHandler.parse("  hello  ")
        assert result["cleaned"] == "hello"
        assert result["length"] == 9

    def test_is_simple_query(self):
        assert MessageHandler.is_simple_query("你好")
        assert MessageHandler.is_simple_query("hi")
        assert MessageHandler.is_simple_query("谢谢")
        assert not MessageHandler.is_simple_query("今天天气怎么样")

    def test_detect_dissatisfaction(self):
        # 匹配: (回答|回复|答案)(错误|不对|错的|不准确)
        assert MessageHandler.detect_dissatisfaction("回答错误")
        # 匹配: (回答|回复|答案)(错误|不对|错的|不准确)
        assert MessageHandler.detect_dissatisfaction("答案不对")
        # 正常文本不应触发
        assert not MessageHandler.detect_dissatisfaction("今天天气很好")

    def test_is_follow_up(self):
        assert MessageHandler.is_follow_up({"text": "然后呢", "history_count": 2})
        assert MessageHandler.is_follow_up({"text": "再说详细点", "history_count": 3})
        assert not MessageHandler.is_follow_up({"text": "你好", "history_count": 0})

    def test_extract_keywords(self):
        kws = MessageHandler.extract_keywords("今天天气怎么样")
        assert isinstance(kws, list)
        assert len(kws) >= 0


class TestResponseBuilder:
    def test_success(self):
        resp = ResponseBuilder.success("hello")
        d = resp.to_dict()
        assert d["success"] is True
        assert d["data"] == "hello"

    def test_error(self):
        resp = ResponseBuilder.error("出错了")
        d = resp.to_dict()
        assert d["success"] is False
        assert d["error"] == "出错了"

    def test_guard_blocked(self):
        resp = ResponseBuilder.guard_blocked("注入检测", "DAN")
        d = resp.to_dict()
        assert d["success"] is False
        assert "安全护栏" in d["error"]

    def test_workflow_result(self):
        resp = ResponseBuilder.workflow_result(output="ok")
        assert resp.success
        assert resp.data["output"] == "ok"

    def test_offline(self):
        resp = ResponseBuilder.offline("无网络")
        assert "离线模式" in resp.data["text"]


class TestTraceStore:
    def test_start_and_end_trace(self):
        store = TraceStore(max_traces=10)
        store.start_trace("test_1", "hello")
        assert store.count == 1
        store.end_trace("test_1", "world")
        trace = store.get_trace("test_1")
        assert trace is not None
        assert trace.status == "success"
        assert trace.duration_ms is not None

    def test_add_span(self):
        store = TraceStore()
        store.start_trace("t2", "input")
        store.add_span("t2", TraceSpan(
            span_id="s1", operation="llm_call",
            start_time=100.0, end_time=101.0,
            duration_ms=1000, status="ok",
        ))
        trace = store.get_trace("t2")
        assert len(trace.spans) == 1
        assert trace.spans[0].operation == "llm_call"

    def test_get_recent(self):
        store = TraceStore()
        store.start_trace("a", "in1")
        store.start_trace("b", "in2")
        recent = store.get_recent(2)
        assert len(recent) == 2

    def test_query_by_status(self):
        store = TraceStore()
        store.start_trace("a", "in1")
        store.end_trace("a", "out1", status="error")
        store.start_trace("b", "in2")
        store.end_trace("b", "out2", status="success")
        results = store.query(status="error")
        assert len(results) == 1
        assert results[0].trace_id == "a"

    def test_delete_trace(self):
        store = TraceStore()
        store.start_trace("a", "in1")
        assert store.delete_trace("a") is True
        assert store.get_trace("a") is None

    def test_clear(self):
        store = TraceStore()
        store.start_trace("a", "in1")
        store.clear()
        assert store.count == 0

    def test_global_singleton(self):
        assert trace_store is not None
