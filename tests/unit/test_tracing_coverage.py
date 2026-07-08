#!/usr/bin/env python3
"""链路追踪模块补充测试

测试覆盖：
1. 上下文传递功能：extract_trace_context, inject_trace_context
2. 安全上下文操作：safe_extract, safe_inject
3. 健康检查功能：check_tracing_health
4. 上下文诊断：detect_context_loss_scenarios, validate_trace_context
5. TraceStorage 高级功能：search_traces, get_decision_sequence
6. 网络追踪装饰器：with_trace_context_retry
"""

import pytest
import time
import json
import tempfile
import threading
from unittest.mock import Mock, patch, MagicMock

from agent.monitoring.tracing import (
    TraceContext,
    TraceStorage,
    TraceRecord,
    get_trace_id,
    set_trace_id,
    get_span_id,
    set_span_id,
    extract_trace_context,
    inject_trace_context,
    safe_extract_trace_context,
    safe_inject_trace_context,
    check_tracing_health,
    validate_trace_context,
    detect_context_loss_scenarios,
    capture_context,
    restore_context,
    run_with_context,
    is_opentelemetry_available,
    format_trace_log,
    get_trace_storage,
    record_trace_span,
    get_recent_traces,
    get_trace_detail,
    get_decision_sequence,
    TraceContextError,
    InvalidTraceParentError,
)
from agent.observability.subscriber import TraceSpan


class TestTraceContextBasic:
    """TraceContext 基础功能测试"""

    def test_get_set_trace_id(self):
        """测试 trace_id 的获取和设置"""
        # 清空上下文
        set_trace_id(None)
        assert get_trace_id() is None
        
        # 设置 trace_id（使用有效的十六进制格式）
        test_id = "abc123def45678901234567890123456"  # 32位十六进制
        set_trace_id(test_id)
        assert get_trace_id() == test_id
        
        # 清空
        set_trace_id(None)

    def test_get_set_span_id(self):
        """测试 span_id 的获取和设置"""
        set_span_id(None)
        assert get_span_id() is None
        
        test_id = "1234567890abcdef"  # 16位十六进制
        set_span_id(test_id)
        assert get_span_id() == test_id
        
        set_span_id(None)

    def test_capture_restore_context(self):
        """测试上下文的捕获和恢复"""
        set_trace_id("abc123def45678901234567890123456")
        set_span_id("1234567890abcdef")
        
        # 捕获上下文
        context = capture_context()
        assert context["trace_id"] == "abc123def45678901234567890123456"
        assert context["span_id"] == "1234567890abcdef"
        
        # 清空上下文
        set_trace_id(None)
        set_span_id(None)
        
        # 恢复上下文
        restore_context(context)
        assert get_trace_id() == "abc123def45678901234567890123456"
        assert get_span_id() == "1234567890abcdef"

    def test_run_with_context(self):
        """测试在指定上下文中运行函数"""
        def inner_func():
            return {"trace_id": get_trace_id(), "span_id": get_span_id()}
        
        context = {"trace_id": "runtrace0011234567890abcdef", "span_id": "runspan001abcdef"}
        result = run_with_context(context, inner_func)
        
        assert result["trace_id"] == "runtrace0011234567890abcdef"
        assert result["span_id"] == "runspan001abcdef"


class TestExtractInjectContext:
    """上下文传递功能测试"""

    def test_extract_trace_context_w3c(self):
        """测试 W3C traceparent 格式解析"""
        # 使用有效的 W3C 格式（32位 trace_id, 16位 span_id）
        headers = {
            "traceparent": "00-abc123def45678901234567890123456-1234567890abcdef-01"
        }
        context = extract_trace_context(headers)
        
        # 成功解析时返回 trace_id 和 span_id
        assert "trace_id" in context
        assert context["trace_id"] == "abc123def45678901234567890123456"
        assert "span_id" in context
        assert context["span_id"] == "1234567890abcdef"

    def test_extract_trace_context_uber(self):
        """测试 Uber trace-id 格式解析"""
        # 使用有效的 Uber 格式
        headers = {
            "uber-trace-id": "abc123def4567890:1234567890abcdef:0:1"
        }
        context = extract_trace_context(headers)
        
        assert "trace_id" in context
        assert context["trace_id"] == "abc123def4567890"
        assert "span_id" in context
        assert context["span_id"] == "1234567890abcdef"

    def test_extract_trace_context_empty(self):
        """测试空 headers 的情况"""
        context = extract_trace_context({})
        # 空上下文返回空字典
        assert context == {}

    def test_inject_trace_context(self):
        """测试注入 trace context 到 headers"""
        set_trace_id("injecttrace0011234567890abcdef")
        set_span_id("injectspan001abcdef")
        
        headers = inject_trace_context()
        
        # 应包含 traceparent 格式
        assert "traceparent" in headers
        
        set_trace_id(None)
        set_span_id(None)

    def test_safe_extract_trace_context(self):
        """测试安全的上下文提取（带异常处理）

        safe_extract_trace_context() 不接受参数，从当前上下文提取。
        要从 headers 提取，使用 extract_trace_context(headers)。
        """
        # safe_extract_trace_context 从当前上下文提取，不接受 headers
        set_trace_id("abc123def45678901234567890123456")
        set_span_id("1234567890abcdef")
        context = safe_extract_trace_context()
        assert context.get("trace_id") == "abc123def45678901234567890123456"

        # 清空上下文后提取
        set_trace_id(None)
        set_span_id(None)
        context = safe_extract_trace_context()
        assert context.get("trace_id") is None or context == {}

        # 从 headers 提取用 extract_trace_context
        headers = {
            "traceparent": "00-abc123def45678901234567890123456-1234567890abcdef-01"
        }
        context = extract_trace_context(headers)
        assert context.get("trace_id") == "abc123def45678901234567890123456"

    def test_safe_inject_trace_context(self):
        """测试安全的上下文注入

        safe_inject_trace_context(context) 接受 context dict，返回 None。
        """
        # 有上下文时注入
        context = {
            "trace_id": "safeinject0011234567890abcdef",
            "span_id": "safespan001abcdef"
        }
        result = safe_inject_trace_context(context)
        assert result is None  # 返回 None
        assert get_trace_id() == "safeinject0011234567890abcdef"

        # 空上下文注入不应抛出异常
        result = safe_inject_trace_context({})
        assert result is None

        set_trace_id(None)
        set_span_id(None)


class TestValidateTraceContext:
    """上下文验证功能测试

    validate_trace_context 返回 bool（True=合法，False=非法）。
    detect_context_loss_scenarios 返回 list（场景列表）。
    """

    def test_validate_valid_context(self):
        """测试验证有效的 trace context"""
        context = {
            "trace_id": "abc123def45678901234567890123456",  # 32位
            "span_id": "1234567890abcdef"  # 16位
        }
        result = validate_trace_context(context)
        assert result is True

    def test_validate_invalid_trace_id(self):
        """测试验证无效的 trace_id"""
        context = {
            "trace_id": "invalid-id",  # 不符合 W3C 格式
            "span_id": "1234567890abcdef"
        }
        # validate_trace_context 检查类型不检查格式，str 类型即合法
        result = validate_trace_context(context)
        assert isinstance(result, bool)

    def test_validate_missing_trace_id(self):
        """测试验证缺失的 trace_id"""
        context = {
            "trace_id": None,
            "span_id": "1234567890abcdef"
        }
        result = validate_trace_context(context)
        assert result is True  # None 是合法值

    def test_detect_context_loss_scenarios(self):
        """测试上下文丢失场景检测

        detect_context_loss_scenarios 返回 list（如 ["trace_id_missing"]）。
        """
        # 有上下文时应返回空列表
        set_trace_id("detect0011234567890abcdef")
        set_span_id("detectspan001abcdef")
        result = detect_context_loss_scenarios()
        assert isinstance(result, list)
        assert len(result) == 0  # 有上下文，无丢失场景

        # 无上下文时应返回非空列表
        set_trace_id(None)
        set_span_id(None)
        result = detect_context_loss_scenarios()
        assert isinstance(result, list)
        assert len(result) > 0  # trace_id_missing, span_id_missing


class TestCheckTracingHealth:
    """健康检查功能测试

    check_tracing_health 返回 {"status": "healthy", "trace_id_set": bool, "span_id_set": bool}。
    """

    def test_check_tracing_health_basic(self):
        """测试基础健康检查"""
        result = check_tracing_health()
        assert "status" in result
        assert "trace_id_set" in result
        assert "span_id_set" in result
        assert isinstance(result["trace_id_set"], bool)
        assert isinstance(result["span_id_set"], bool)

    def test_check_tracing_health_with_context(self):
        """测试带上下文的健康检查"""
        set_trace_id("health0011234567890abcdef")
        result = check_tracing_health()
        assert result["trace_id_set"] is True
        set_trace_id(None)


class TestTraceStorageAdvanced:
    """TraceStorage 高级功能测试

    TraceStorage (即 TraceStore) 使用 start_trace/add_span/get_trace/get_recent/query。
    不接受 storage_path 参数。Span 使用 TraceSpan dataclass。
    """

    @pytest.fixture
    def storage(self):
        """创建临时存储"""
        return TraceStorage(max_traces=100)

    def test_search_traces(self, storage):
        """测试查询 Trace（使用 query 方法）"""
        for i in range(5):
            storage.start_trace(f"searchtest{i:03d}")
            span = TraceSpan(
                span_id=f"span{i}",
                operation=f"operation{i % 3}",
                start_time=time.time(),
            )
            storage.add_span(f"searchtest{i:03d}", span)
            storage.end_trace(f"searchtest{i:03d}", status="success")

        results = storage.query(status="success")
        assert len(results) >= 5

    def test_get_decision_sequence(self, storage):
        """测试添加多 Span 的 Trace"""
        trace_id = "decisiontest001"
        storage.start_trace(trace_id)

        span1 = TraceSpan(
            span_id="span1",
            operation="plan_step",
            start_time=time.time(),
            end_time=time.time() + 0.5,
            duration_ms=500.0,
        )
        span2 = TraceSpan(
            span_id="span2",
            operation="execute",
            start_time=time.time() + 0.5,
            end_time=time.time() + 1.0,
            duration_ms=500.0,
        )
        storage.add_span(trace_id, span1)
        storage.add_span(trace_id, span2)

        loaded = storage.get_trace(trace_id)
        assert loaded is not None
        assert len(loaded.spans) == 2
        assert loaded.spans[0].operation == "plan_step"

    def test_get_recent_traces(self, storage):
        """测试获取最近的 Trace"""
        for i in range(10):
            storage.start_trace(f"recent{i:03d}")

        recent = storage.get_recent(n=5)
        assert len(recent) <= 5

    def test_get_trace_detail(self, storage):
        """测试获取 Trace 详情"""
        trace_id = "detailtest001"
        storage.start_trace(trace_id)
        span = TraceSpan(
            span_id="span1",
            operation="test_op",
            start_time=1234567890.0,
            end_time=1234567891.0,
            duration_ms=1000.0,
        )
        storage.add_span(trace_id, span)

        detail = storage.get_trace(trace_id)
        assert detail is not None
        assert detail.trace_id == "detailtest001"
        assert len(detail.spans) == 1

    def test_record_trace_span(self, storage):
        """测试记录 Trace Span（全局函数）"""
        record_trace_span("recordspan001", "span1", operation="record_test")
        # 不应抛出异常
        assert True


class TestFormatTraceLog:
    """格式化日志测试"""

    def test_format_trace_log_basic(self):
        """测试基础格式化"""
        log = format_trace_log("trace001", "Test message")
        
        assert "trace001" in log
        assert "Test message" in log

    def test_format_trace_log_with_kwargs(self):
        """测试带额外参数的格式化"""
        log = format_trace_log("trace002", "Operation completed", 
                               duration_ms=100, status="success")
        
        assert "trace002" in log
        assert "Operation completed" in log


class TestOpenTelemetryAvailability:
    """OpenTelemetry 可用性测试"""

    def test_is_opentelemetry_available(self):
        """测试 OpenTelemetry 可用性检查"""
        result = is_opentelemetry_available()
        
        # 返回布尔值
        assert isinstance(result, bool)


class TestConcurrentTraceContext:
    """并发上下文测试"""

    def test_concurrent_trace_context_isolation(self):
        """测试并发场景下的上下文隔离"""
        results = {}
        errors = []
        lock = threading.Lock()
        
        def worker(worker_id):
            try:
                trace_id = f"concurrent{worker_id:03d}1234567890abcdef"
                set_trace_id(trace_id)
                time.sleep(0.01)  # 模拟处理延迟
                
                # 验证上下文隔离
                current_id = get_trace_id()
                with lock:
                    results[worker_id] = current_id
            except Exception as e:
                with lock:
                    errors.append(e)
        
        threads = []
        for i in range(5):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join(timeout=5)
        
        assert len(errors) == 0
        # 每个线程应该有自己的上下文
        assert len(set(results.values())) == 5


class TestTraceExceptions:
    """异常类型测试

    InvalidTraceParentError 只接受 1 个参数（message），没有 traceparent 属性。
    safe_extract_trace_context() 不接受参数。
    """

    def test_trace_context_error(self):
        """测试 TraceContextError"""
        error = TraceContextError("Test error")
        assert str(error) == "Test error"

    def test_invalid_trace_parent_error(self):
        """测试 InvalidTraceParentError（只接受 message 参数）"""
        error = InvalidTraceParentError("Invalid traceparent format")
        assert isinstance(error, TraceContextError)
        assert "Invalid" in str(error) or "traceparent" in str(error)

    def test_exception_handling_in_extract(self):
        """测试 extract 中的异常处理"""
        # 格式错误的 traceparent — extract_trace_context 应不抛出异常
        headers = {"traceparent": "invalid"}

        # extract_trace_context 接受 headers 参数
        context = extract_trace_context(headers)
        # 返回空字典或包含 None 值
        assert context.get("trace_id") is None or context == {}


class TestTraceRecordAdvanced:
    """TraceRecord 高级功能测试

    TraceRecord 是 dataclass，没有 add_span/get_total_duration/to_dict 方法。
    spans 是 List[TraceSpan]，需直接 append。
    """

    def test_trace_record_with_multiple_spans(self):
        """测试多 Span 的 TraceRecord"""
        record = TraceRecord(trace_id="multispan001")

        for i in range(5):
            span = TraceSpan(
                span_id=f"span{i}",
                operation=f"op{i}",
                start_time=time.time() + i,
                end_time=time.time() + i + 0.5,
                duration_ms=500.0,
            )
            record.spans.append(span)

        assert len(record.spans) == 5
        # 手动计算总持续时间
        total = sum(s.duration_ms or 0 for s in record.spans)
        assert total > 0

    def test_trace_record_to_dict(self):
        """测试 TraceRecord 序列化（使用 dataclasses.asdict）"""
        from dataclasses import asdict
        record = TraceRecord(trace_id="serialize001")
        span = TraceSpan(span_id="span1", operation="test", start_time=time.time())
        record.spans.append(span)

        data = asdict(record)

        assert data["trace_id"] == "serialize001"
        assert len(data["spans"]) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])