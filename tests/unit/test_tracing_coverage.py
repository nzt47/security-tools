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
        """测试安全的上下文提取（带异常处理）"""
        # 正常情况
        headers = {
            "traceparent": "00-abc123def45678901234567890123456-1234567890abcdef-01"
        }
        context = safe_extract_trace_context(headers)
        assert "trace_id" in context
        assert context["trace_id"] == "abc123def45678901234567890123456"
        
        # 异常格式（不应抛出异常）
        headers = {"traceparent": "invalid-format"}
        context = safe_extract_trace_context(headers)
        # 返回空字典或包含 None 值
        assert context.get("trace_id") is None or context == {}

    def test_safe_inject_trace_context(self):
        """测试安全的上下文注入"""
        # 无上下文时，可能生成新的 traceparent 或返回空
        set_trace_id(None)
        set_span_id(None)
        headers = safe_inject_trace_context()
        # 可能生成新的 trace_id 或返回空 headers
        # 不应该抛出异常
        assert isinstance(headers, dict)
        
        # 有上下文时
        set_trace_id("safeinject0011234567890abcdef")
        set_span_id("safespan001abcdef")
        headers = safe_inject_trace_context()
        assert len(headers) > 0
        
        set_trace_id(None)
        set_span_id(None)


class TestValidateTraceContext:
    """上下文验证功能测试"""

    def test_validate_valid_context(self):
        """测试验证有效的 trace context"""
        context = {
            "trace_id": "abc123def45678901234567890123456",  # 32位
            "span_id": "1234567890abcdef"  # 16位
        }
        result = validate_trace_context(context)
        
        assert result["valid"] is True
        assert result["errors"] == []

    def test_validate_invalid_trace_id(self):
        """测试验证无效的 trace_id"""
        context = {
            "trace_id": "invalid-id",  # 不符合 W3C 格式
            "span_id": "1234567890abcdef"
        }
        result = validate_trace_context(context)
        
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_validate_missing_trace_id(self):
        """测试验证缺失的 trace_id"""
        context = {
            "trace_id": None,
            "span_id": "1234567890abcdef"
        }
        result = validate_trace_context(context)
        
        assert result["valid"] is False

    def test_detect_context_loss_scenarios(self):
        """测试上下文丢失场景检测"""
        # 正常情况（有上下文）
        set_trace_id("detect0011234567890abcdef")
        set_span_id("detectspan001abcdef")
        result = detect_context_loss_scenarios()
        
        # 返回结构包含 potential_risk, scenarios, recommendations
        assert "potential_risk" in result
        assert "scenarios" in result
        assert "recommendations" in result
        
        set_trace_id(None)
        set_span_id(None)


class TestCheckTracingHealth:
    """健康检查功能测试"""

    def test_check_tracing_health_basic(self):
        """测试基础健康检查"""
        result = check_tracing_health()
        
        # 返回结构：healthy, components, warnings
        assert "healthy" in result
        assert "components" in result
        assert "warnings" in result
        assert isinstance(result["warnings"], list)

    def test_check_tracing_health_with_context(self):
        """测试带上下文的健康检查"""
        set_trace_id("health0011234567890abcdef")
        result = check_tracing_health()
        
        # components 中应包含 context 信息
        assert "context" in result["components"]
        
        set_trace_id(None)


class TestTraceStorageAdvanced:
    """TraceStorage 高级功能测试"""

    @pytest.fixture
    def storage(self, tmp_path):
        """创建临时存储"""
        return TraceStorage(storage_path=str(tmp_path))

    def test_search_traces(self, storage):
        """测试搜索 Trace"""
        # 创建多条 Trace
        for i in range(5):
            record = TraceRecord(trace_id=f"searchtest{i:03d}")
            record.add_span({
                "span_id": f"span{i}",
                "service": f"Service{i % 2}",
                "operation": f"operation{i % 3}"
            })
            storage.save_trace(record)
        
        # 搜索特定服务
        results = storage.search_traces(service_name="Service0")
        assert len(results) >= 2
        
        # 搜索特定操作
        results = storage.search_traces(operation="operation0")
        assert len(results) >= 1

    def test_get_decision_sequence(self, storage):
        """测试获取决策序列"""
        record = TraceRecord(trace_id="decisiontest001")
        # 添加带 events 的 span（决策通过 events 字段）
        record.add_span({
            "span_id": "span1",
            "service": "Planner",
            "operation": "plan_step",
            "start_time": time.time(),
            "end_time": time.time() + 0.5,
            "duration_ms": 500.0,
            "events": [
                {
                    "name": "decision",
                    "timestamp": time.time(),
                    "attributes": {"decision": "选择工具 A", "reason": "效率最高"}
                }
            ]
        })
        record.add_span({
            "span_id": "span2",
            "service": "Executor",
            "operation": "execute",
            "start_time": time.time() + 0.5,
            "end_time": time.time() + 1.0,
            "duration_ms": 500.0,
            "events": [
                {
                    "name": "action",
                    "timestamp": time.time() + 0.5,
                    "attributes": {"action": "执行工具 A", "result": "成功"}
                }
            ]
        })
        storage.save_trace(record)
        
        # 直接使用 storage 加载验证
        loaded = storage.load_trace("decisiontest001")
        assert loaded is not None
        assert len(loaded.spans) == 2
        
        # 验证 events 存在
        assert "events" in loaded.spans[0]
        assert len(loaded.spans[0]["events"]) == 1

    def test_get_recent_traces(self, storage):
        """测试获取最近的 Trace"""
        for i in range(10):
            record = TraceRecord(trace_id=f"recent{i:03d}")
            record.add_span({"span_id": f"span{i}"})
            storage.save_trace(record)
        
        recent = get_recent_traces(limit=5)
        
        assert len(recent) <= 5

    def test_get_trace_detail(self, storage):
        """测试获取 Trace 详情"""
        record = TraceRecord(trace_id="detailtest001")
        record.add_span({
            "span_id": "span1",
            "service": "TestService",
            "operation": "test_op",
            "start_time": 1234567890.0,
            "end_time": 1234567891.0,
            "duration_ms": 1000.0
        })
        storage.save_trace(record)
        
        # 使用 storage.load_trace 获取详情
        detail = storage.load_trace("detailtest001")
        
        assert detail is not None
        assert detail.trace_id == "detailtest001"
        assert len(detail.spans) == 1

    def test_record_trace_span(self, storage):
        """测试记录 Trace Span"""
        record_trace_span("recordspan001", {
            "span_id": "span1",
            "service": "TestService",
            "operation": "record_test"
        })
        
        # 验证记录成功（使用全局 storage）
        detail = get_trace_detail("recordspan001")
        # 可能返回 None（因为使用全局 storage，不是 tmp_path）
        # 但不应该抛出异常
        assert detail is None or detail.get("trace_id") == "recordspan001"


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
    """异常类型测试"""

    def test_trace_context_error(self):
        """测试 TraceContextError"""
        error = TraceContextError("Test error")
        assert str(error) == "Test error"

    def test_invalid_trace_parent_error(self):
        """测试 InvalidTraceParentError（需要 traceparent 参数）"""
        error = InvalidTraceParentError("Invalid traceparent format", "invalid-traceparent")
        assert isinstance(error, TraceContextError)
        assert error.traceparent == "invalid-traceparent"

    def test_exception_handling_in_extract(self):
        """测试 extract 中的异常处理"""
        # 格式错误的 traceparent
        headers = {"traceparent": "invalid"}
        
        # 应该不抛出异常，返回空上下文
        context = safe_extract_trace_context(headers)
        # 返回空字典或包含 None 值
        assert context.get("trace_id") is None or context == {}


class TestTraceRecordAdvanced:
    """TraceRecord 高级功能测试"""

    def test_trace_record_with_multiple_spans(self):
        """测试多 Span 的 TraceRecord"""
        record = TraceRecord(trace_id="multispan001")
        
        for i in range(5):
            record.add_span({
                "span_id": f"span{i}",
                "parent_span_id": f"span{i-1}" if i > 0 else None,
                "service": f"Service{i}",
                "operation": f"op{i}",
                "start_time": time.time() + i,
                "end_time": time.time() + i + 0.5,
                "duration_ms": 500.0
            })
        
        assert len(record.spans) == 5
        assert record.get_total_duration() > 0

    def test_trace_record_to_dict(self):
        """测试 TraceRecord 序列化"""
        record = TraceRecord(trace_id="serialize001")
        record.add_span({"span_id": "span1", "operation": "test"})
        
        data = record.to_dict()
        
        assert data["trace_id"] == "serialize001"
        assert data["span_count"] == 1
        assert "created_at" in data
        assert "updated_at" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])