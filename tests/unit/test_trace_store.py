"""TraceStore 单元测试 — 链路追踪内存存储"""
import time

import pytest

from agent.observability.subscriber import TraceStore, TraceSpan, TraceRecord


class TestTraceStore:
    """TraceStore 基本功能测试"""

    def setup_method(self):
        self.store = TraceStore(max_traces=10)

    def test_start_trace(self):
        record = self.store.start_trace("trace_001", "用户输入")
        assert record.trace_id == "trace_001"
        assert record.input == "用户输入"
        assert record.status == "active"

    def test_end_trace(self):
        self.store.start_trace("t1")
        time.sleep(0.01)
        self.store.end_trace("t1", "输出结果", "success")
        record = self.store.get_trace("t1")
        assert record.output == "输出结果"
        assert record.status == "success"
        assert record.duration_ms > 0

    def test_add_span(self):
        self.store.start_trace("t2")
        span = TraceSpan(span_id="s1", operation="search", start_time=time.time())
        self.store.add_span("t2", span)

        record = self.store.get_trace("t2")
        assert len(record.spans) == 1
        assert record.spans[0].span_id == "s1"
        assert record.spans[0].operation == "search"

    def test_get_nonexistent_trace(self):
        assert self.store.get_trace("ghost") is None

    def test_get_recent(self):
        for i in range(5):
            self.store.start_trace(f"t_{i}")
        recent = self.store.get_recent(3)
        assert len(recent) == 3

    def test_get_recent_default(self):
        for i in range(5):
            self.store.start_trace(f"t_{i}")
        recent = self.store.get_recent()
        assert len(recent) == 5  # 全部不足10条

    def test_query_by_status(self):
        self.store.start_trace("active_trace")
        self.store.start_trace("done_trace")
        self.store.end_trace("done_trace", status="success")

        results = self.store.query(status="success")
        assert len(results) == 1
        assert results[0].trace_id == "done_trace"

    def test_query_no_match(self):
        self.store.start_trace("t1")
        results = self.store.query(status="error")
        assert results == []

    def test_delete_trace(self):
        self.store.start_trace("to_delete")
        assert self.store.count == 1
        assert self.store.delete_trace("to_delete") is True
        assert self.store.count == 0

    def test_delete_nonexistent(self):
        assert self.store.delete_trace("ghost") is False

    def test_clear(self):
        for i in range(5):
            self.store.start_trace(f"t_{i}")
        self.store.clear()
        assert self.store.count == 0

    def test_max_traces_eviction(self):
        store = TraceStore(max_traces=3)
        for i in range(5):
            store.start_trace(f"t_{i}")
        assert store.count == 3

    def test_stats(self):
        self.store.start_trace("t1")
        self.store.end_trace("t1", status="success")

        stats = self.store.stats
        assert stats["total"] >= 1
        assert "avg_duration_ms" in stats
        assert stats["max_traces"] == 10

    def test_count(self):
        assert self.store.count == 0
        self.store.start_trace("t1")
        assert self.store.count == 1

    def test_start_trace_replaces_if_exists(self):
        self.store.start_trace("t1")
        self.store.start_trace("t1")  # 重新开始，会覆盖
        assert self.store.count == 1

    def test_end_nonexistent_trace_no_error(self):
        # 结束不存在的 trace 不应报错
        self.store.end_trace("ghost", "output")  # should not raise
        assert True


class TestTraceSpan:
    """TraceSpan 数据类测试"""

    def test_span_creation(self):
        now = time.time()
        span = TraceSpan(span_id="s1", operation="llm_call", start_time=now)
        assert span.span_id == "s1"
        assert span.status == "unknown"

    def test_span_end_time_and_duration(self):
        span = TraceSpan(span_id="s2", operation="search", start_time=100.0)
        span.end_time = 102.0
        span.duration_ms = 2000.0
        assert span.duration_ms == 2000.0


class TestTraceRecord:
    """TraceRecord 数据类测试"""

    def test_default_status(self):
        record = TraceRecord(trace_id="t1")
        assert record.status == "active"

    def test_default_spans(self):
        record = TraceRecord(trace_id="t1")
        assert record.spans == []
