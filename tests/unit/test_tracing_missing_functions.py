#!/usr/bin/env python3
"""tracing 模块缺失函数单元测试骨架

本文件覆盖 tracing.py 中当前无直接单元测试的函数：
1. record_span_attributes — span 属性持久化（结构化 JSON 日志降级实现）
2. _safe_call — 安全调用包装器（记录后重新抛出）
3. diagnose_opentelemetry_config — OpenTelemetry 配置诊断
4. init_observability — 可观测性初始化
5. record_request_metrics — HTTP 请求指标记录（结构化日志）
6. get_logger_with_context — 带追踪上下文的 logger
7. get_trace_storage / reset_trace_storage — 存储单例管理

注意事项：
- OpenTelemetry 缺失时相关测试应跳过（@pytest.mark.skipif）
- record_span_attributes / _safe_call 均为 best-effort，主流程不因异常中断
"""

import pytest
import json
import logging
import types
import builtins
from unittest.mock import Mock, patch, MagicMock

from agent.monitoring.tracing import (
    record_span_attributes,
    _safe_call,
    diagnose_opentelemetry_config,
    init_observability,
    record_request_metrics,
    get_logger_with_context,
    get_trace_storage,
    reset_trace_storage,
    is_opentelemetry_available,
    get_trace_id,
    set_trace_id,
)

OPENTELEMETRY_AVAILABLE = is_opentelemetry_available()


def _fake_import_failing(blocked_names):
    """构造 __import__ 包装器：仅对指定包名抛 ImportError，其余走真实导入"""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in blocked_names:
            raise ImportError(f"no {name}")
        return real_import(name, *args, **kwargs)
    return fake_import


# ──────────────────────────────────────────────────────────────────────────────
# 1. record_span_attributes — span 属性持久化
# ──────────────────────────────────────────────────────────────────────────────
class TestRecordSpanAttributes:
    """record_span_attributes：将 span 属性写入结构化 JSON 日志"""

    def test_uses_current_context_by_default(self):
        """未显式传 trace_id/span_id 时，应使用当前线程上下文"""
        set_trace_id("test-trace-123")
        with patch("agent.monitoring.tracing.logger") as mock_logger:
            record_span_attributes(retrieved_chunks=3, eval_score=0.85)
            # 验证调用了 logger.info 且 payload 包含当前 trace_id
            mock_logger.info.assert_called_once()
            payload = json.loads(mock_logger.info.call_args[0][0])
            assert payload["trace_id"] == "test-trace-123"
            assert payload["action"] == "span_attributes"
            assert payload["attributes"] == {"retrieved_chunks": 3, "eval_score": 0.85}

    def test_explicit_trace_and_span_ids(self):
        """显式传 trace_id/span_id 时优先使用"""
        with patch("agent.monitoring.tracing.logger") as mock_logger:
            record_span_attributes(trace_id="ext-1", span_id="sp-1", key="v")
            payload = json.loads(mock_logger.info.call_args[0][0])
            assert payload["trace_id"] == "ext-1"
            assert payload["span_id"] == "sp-1"

    def test_exception_does_not_propagate(self):
        """logger 抛异常时不应影响主流程（防御性保证）"""
        with patch("agent.monitoring.tracing.logger") as mock_logger:
            mock_logger.info.side_effect = RuntimeError("log failed")
            # 不应抛出异常
            record_span_attributes(foo="bar")


# ──────────────────────────────────────────────────────────────────────────────
# 2. _safe_call — 安全调用包装器
# ──────────────────────────────────────────────────────────────────────────────
class TestSafeCall:
    """_safe_call：捕获异常记录后重新抛出（边界显性化）"""

    def test_returns_result_on_success(self):
        """正常调用返回函数结果"""
        assert _safe_call(lambda x: x * 2, 5, action="test") == 10

    def test_passes_kwargs(self):
        """支持关键字参数透传"""
        assert _safe_call(lambda a, b=0: a + b, 3, b=4, action="test") == 7

    def test_reraises_exception_with_log(self):
        """异常被记录后重新抛出"""
        def boom():
            raise ValueError("boom")
        with patch("agent.monitoring.tracing.logger") as mock_logger:
            with pytest.raises(ValueError, match="boom"):
                _safe_call(boom, action="test_boom")
            # 记录结构化错误日志
            mock_logger.error.assert_called_once()
            payload = json.loads(mock_logger.error.call_args[0][0])
            assert payload["action"] == "test_boom.failed"
            assert "ValueError" in payload["error"]

    def test_default_action_name(self):
        """未指定 action 时使用默认值 'safe_call'"""
        with patch("agent.monitoring.tracing.logger") as mock_logger:
            with pytest.raises(Exception):
                _safe_call(lambda: 1 / 0)
            payload = json.loads(mock_logger.error.call_args[0][0])
            assert payload["action"] == "safe_call.failed"


# ──────────────────────────────────────────────────────────────────────────────
# 3. diagnose_opentelemetry_config — OpenTelemetry 配置诊断
# ──────────────────────────────────────────────────────────────────────────────
class TestDiagnoseOpentelemetryConfig:
    """diagnose_opentelemetry_config：返回结构化诊断信息"""

    def test_returns_expected_keys(self):
        """返回 dict 应包含全部约定字段，且不抛异常"""
        result = diagnose_opentelemetry_config()
        for key in ["opentelemetry_available", "tracer_initialized",
                    "tracer_provider_set", "sdk_version",
                    "detection_context", "issues"]:
            assert key in result, f"缺少字段: {key}"
        assert isinstance(result["issues"], list)

    def test_never_raises(self):
        """任何异常都应被捕获并记入 issues，绝不向上抛出"""
        with patch("agent.monitoring.tracing.logger"):
            result = diagnose_opentelemetry_config()
            assert isinstance(result, dict)

    def test_otel_unavailable_branch(self):
        """opentelemetry 包未安装：应标记不可用并记入 issues"""
        with patch("builtins.__import__",
                   side_effect=_fake_import_failing({"opentelemetry"})):
            result = diagnose_opentelemetry_config()
        assert result["opentelemetry_available"] is False
        assert any("未安装" in issue for issue in result["issues"])

    def test_sdk_missing_branch(self):
        """仅 API 可用、sdk 未安装：应记入 sdk 缺失 issue"""
        # 用假 opentelemetry 模块（无 sdk 子模块）替换真实模块
        fake_otel = types.ModuleType("opentelemetry")
        with patch.dict("sys.modules", {"opentelemetry": fake_otel}):
            with patch("builtins.__import__",
                       side_effect=_fake_import_failing({"opentelemetry.sdk"})):
                result = diagnose_opentelemetry_config()
        assert result["opentelemetry_available"] is True
        assert any("sdk 未安装" in issue for issue in result["issues"])

    def test_proxy_provider_branch(self):
        """TracerProvider 为默认 Proxy 实现：tracer_initialized 应为 False 并记入 issue"""
        proxy_provider = MagicMock()
        type(proxy_provider).__name__ = "ProxyTracerProvider"
        # 保留真实 opentelemetry 模块，仅让 get_tracer_provider 返回 Proxy 实现
        from opentelemetry import trace as _ot
        with patch.object(_ot, "get_tracer_provider", return_value=proxy_provider):
            result = diagnose_opentelemetry_config()
        assert result["tracer_initialized"] is False
        assert any("Proxy" in issue for issue in result["issues"])


# ──────────────────────────────────────────────────────────────────────────────
# 4. init_observability — 可观测性初始化
# ──────────────────────────────────────────────────────────────────────────────
class TestInitObservability:
    """init_observability：初始化可观测性配置"""

    def test_returns_bool(self):
        """返回布尔值（是否初始化成功）"""
        result = init_observability(service_name="test-service")
        assert isinstance(result, bool)

    def test_accepts_custom_service_name(self):
        """支持自定义 service_name 参数"""
        init_observability(service_name="custom-name")  # 不应抛异常

    def test_already_initialized_returns_true(self):
        """TracerProvider 已是 SDK 实现（非 Proxy）时应直接返回 True"""
        real_provider = MagicMock()
        type(real_provider).__name__ = "TracerProvider"
        with patch("agent.monitoring.tracing.logger"):
            from opentelemetry import trace as _ot
            with patch.object(_ot, "get_tracer_provider", return_value=real_provider):
                assert init_observability("svc") is True

    def test_sdk_missing_returns_false(self):
        """sdk 未安装时无法初始化，应返回 False"""
        proxy_provider = MagicMock()
        type(proxy_provider).__name__ = "ProxyTracerProvider"
        with patch("agent.monitoring.tracing.logger"):
            from opentelemetry import trace as _ot
            with patch.object(_ot, "get_tracer_provider", return_value=proxy_provider):
                with patch("builtins.__import__", side_effect=ImportError("no sdk")):
                    assert init_observability("svc") is False

    def test_initialization_exception_returns_false(self):
        """初始化过程中抛异常应返回 False 而非向上传播"""
        with patch("agent.monitoring.tracing.logger"):
            from opentelemetry import trace as _ot
            with patch.object(_ot, "get_tracer_provider", side_effect=RuntimeError("broken")):
                assert init_observability("svc") is False


# ──────────────────────────────────────────────────────────────────────────────
# 5. record_request_metrics — HTTP 请求指标记录
# ──────────────────────────────────────────────────────────────────────────────
class TestRecordRequestMetrics:
    """record_request_metrics：记录结构化请求指标日志"""

    def test_logs_structured_payload(self):
        """记录含 method/path/status_code/duration_ms 的结构化日志"""
        set_trace_id("trace-req-1")
        with patch("agent.monitoring.tracing.logger") as mock_logger:
            record_request_metrics("GET", "/api/chat", 200, 150.5)
            mock_logger.debug.assert_called_once()
            # logger.debug 采用 % 格式化：args[0] 是格式串，args[1:] 是占位参数
            fmt = mock_logger.debug.call_args[0][0]
            args = mock_logger.debug.call_args[0][1:]
            rendered = fmt % args
            payload = json.loads(rendered)
            assert payload["method"] == "GET"
            assert payload["path"] == "/api/chat"
            assert payload["status_code"] == 200
            assert payload["duration_ms"] == 150.5
            assert payload["trace_id"] == "trace-req-1"

    def test_trace_id_empty_when_no_context(self):
        """无 trace_id 时应记录空字符串而非 None"""
        set_trace_id(None)
        with patch("agent.monitoring.tracing.logger") as mock_logger:
            record_request_metrics("POST", "/test", 500, 10.0)
            fmt = mock_logger.debug.call_args[0][0]
            args = mock_logger.debug.call_args[0][1:]
            payload = json.loads(fmt % args)
            assert payload["trace_id"] == ""


# ──────────────────────────────────────────────────────────────────────────────
# 6. get_logger_with_context — 带追踪上下文的 logger
# ──────────────────────────────────────────────────────────────────────────────
class TestGetLoggerWithContext:
    """get_logger_with_context：返回带标准接口的 logger"""

    def test_returns_logger_with_standard_methods(self):
        """返回的 logger 具备全部标准日志方法"""
        logger = get_logger_with_context("test.module")
        for method in ["debug", "info", "warning", "error", "critical", "exception"]:
            assert hasattr(logger, method), f"缺少方法: {method}"
        assert isinstance(logger, logging.Logger)

    def test_logger_name_preserved(self):
        """logger 名称应与传入参数一致"""
        logger = get_logger_with_context("my.custom.logger")
        assert logger.name == "my.custom.logger"

    def test_logging_does_not_raise(self):
        """日志调用不应抛异常"""
        logger = get_logger_with_context("test")
        set_trace_id("trace-log-1")
        logger.info("test message")  # 不应抛异常
        logger.debug("debug message")


# ──────────────────────────────────────────────────────────────────────────────
# 7. get_trace_storage / reset_trace_storage — 存储单例管理
# ──────────────────────────────────────────────────────────────────────────────
class TestTraceStorageSingleton:
    """get_trace_storage / reset_trace_storage：单例生命周期管理"""

    def test_get_returns_singleton(self):
        """连续调用应返回同一实例"""
        s1 = get_trace_storage()
        s2 = get_trace_storage()
        assert s1 is s2

    def test_reset_creates_new_instance(self):
        """reset 后再次获取应得到新实例"""
        s1 = get_trace_storage()
        reset_trace_storage()
        s2 = get_trace_storage()
        assert s1 is not s2


# ──────────────────────────────────────────────────────────────────────────────
# 8. print_diagnosis_report / print_context_diagnosis — 诊断报告打印
# ──────────────────────────────────────────────────────────────────────────────
class TestDiagnosisReportPrint:
    """print_diagnosis_report / print_context_diagnosis：stdout 输出诊断信息"""

    def test_print_diagnosis_report_issues_present(self, capsys):
        """诊断报告应打印全部核心字段"""
        with patch("agent.monitoring.tracing.diagnose_opentelemetry_config",
                   return_value={
                       "opentelemetry_available": True,
                       "tracer_initialized": True,
                       "tracer_provider_set": True,
                       "sdk_version": "1.20.0",
                       "detection_context": {"trace_id": "t1", "span_id": "s1"},
                       "issues": [],
                   }):
            from agent.monitoring.tracing import print_diagnosis_report
            print_diagnosis_report()
        out = capsys.readouterr().out
        assert "OpenTelemetry 配置诊断报告" in out
        assert "opentelemetry_available: True" in out
        assert "sdk_version" in out
        assert "t1" in out

    def test_print_diagnosis_report_with_issues(self, capsys):
        """存在 issues 时应在报告中展示问题列表"""
        with patch("agent.monitoring.tracing.diagnose_opentelemetry_config",
                   return_value={
                       "opentelemetry_available": False,
                       "tracer_initialized": False,
                       "tracer_provider_set": False,
                       "sdk_version": None,
                       "detection_context": {"trace_id": None, "span_id": None},
                       "issues": ["opentelemetry 包未安装"],
                   }):
            from agent.monitoring.tracing import print_diagnosis_report
            print_diagnosis_report()
        out = capsys.readouterr().out
        assert "发现问题" in out
        assert "opentelemetry 包未安装" in out

    def test_print_context_diagnosis(self, capsys):
        """上下文诊断应打印 trace_id/span_id 与健康状态"""
        set_trace_id("ctx-trace-1")
        from agent.monitoring.tracing import print_context_diagnosis
        print_context_diagnosis()
        out = capsys.readouterr().out
        assert "当前追踪上下文" in out
        assert "ctx-trace-1" in out
        assert "健康状态" in out


# ──────────────────────────────────────────────────────────────────────────────
# 9. reset_trace_storage fallback 路径（_SINGLETON_AVAILABLE=False）
# ──────────────────────────────────────────────────────────────────────────────
class TestTraceStorageFallback:
    """_SINGLETON_AVAILABLE=False 时走线程锁 fallback 路径"""

    def test_fallback_singleton_path(self):
        """单例管理器不可用时，get_trace_storage 应通过锁创建并缓存"""
        with patch("agent.monitoring.tracing._SINGLETON_AVAILABLE", False):
            with patch("agent.monitoring.tracing._trace_storage_singleton", None):
                s1 = get_trace_storage()
                s2 = get_trace_storage()
                assert s1 is s2

    def test_fallback_reset_path(self):
        """fallback 模式下 reset 应清空缓存实例"""
        with patch("agent.monitoring.tracing._SINGLETON_AVAILABLE", False):
            with patch("agent.monitoring.tracing._trace_storage_singleton", None):
                s1 = get_trace_storage()
                reset_trace_storage()
                s2 = get_trace_storage()
                assert s1 is not s2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
