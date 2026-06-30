#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sentry 错误上报集成单元测试

覆盖维度：
- 配置解析（环境变量、采样率校验、DSN 格式校验）
- Sentry SDK 初始化（启用/禁用/失败降级）
- 敏感信息过滤（_filter_sensitive_recursive、_is_sensitive_key、_sentry_before_send）
- 主动上报 API（capture_error、capture_message 在未初始化时返回 None）
- 边界场景（DSN 缺失、采样率越界、sentry-sdk 未安装）

测试目标：覆盖率 ≥ 80%，所有失败路径均有断言。
"""

import os
import sys
import importlib
import logging
import pytest
from unittest.mock import patch, MagicMock

# 重置模块状态确保测试隔离
def _reset_module():
    """重置 error_reporting_config 模块的全局状态"""
    import agent.error_reporting_config as mod
    mod._sentry_initialized = False
    mod._sentry_init_lock = None
    mod._sensitive_patterns = mod._DEFAULT_SENSITIVE_PATTERNS


# ─── 配置解析测试 ────────────────────────────────────────────────────

class TestErrorReportingConfig:
    """错误上报配置测试"""

    def test_get_config_contains_sentry_section(self):
        """get_config 必须包含 sentry 段"""
        from agent.error_reporting_config import get_config
        config = get_config()
        assert "sentry" in config
        sentry = config["sentry"]
        # 默认 DSN 为空，enabled 应为 False
        assert sentry["enabled"] is False
        assert sentry["dsn"] == ""
        assert sentry["environment"] in ("dev", "development", "production", "staging")
        assert 0 <= sentry["sample_rate"] <= 1
        assert 0 <= sentry["traces_sample_rate"] <= 1
        assert sentry["server_name"] == "yunshu-backend"
        assert sentry["min_level"] == "error"

    def test_sentry_enabled_when_dsn_set(self, monkeypatch):
        """SENTRY_DSN 配置后 sentry.enabled 应为 True"""
        monkeypatch.setenv("SENTRY_DSN", "https://abc@example.com/1")
        from agent.error_reporting_config import get_config
        config = get_config()
        assert config["sentry"]["enabled"] is True
        assert config["sentry"]["dsn"] == "https://abc@example.com/1"

    def test_sentry_environment_configurable(self, monkeypatch):
        """SENTRY_ENVIRONMENT 可通过环境变量配置"""
        monkeypatch.setenv("SENTRY_ENVIRONMENT", "staging")
        from agent.error_reporting_config import get_config
        config = get_config()
        assert config["sentry"]["environment"] == "staging"

    def test_sentry_sample_rate_default(self, monkeypatch):
        """未配置 SENTRY_SAMPLE_RATE 时默认 1.0"""
        monkeypatch.delenv("SENTRY_SAMPLE_RATE", raising=False)
        from agent.error_reporting_config import get_config
        config = get_config()
        assert config["sentry"]["sample_rate"] == 1.0

    def test_sentry_sample_rate_invalid_raises(self, monkeypatch):
        """采样率越界应抛出带业务错误码的 ValueError"""
        monkeypatch.setenv("SENTRY_SAMPLE_RATE", "1.5")
        from agent.error_reporting_config import (
            get_config,
            SENTRY_ERR_RATE_INVALID,
        )
        with pytest.raises(ValueError) as exc_info:
            get_config()
        assert SENTRY_ERR_RATE_INVALID in str(exc_info.value)

    def test_sentry_sample_rate_non_numeric_raises(self, monkeypatch):
        """非数值采样率应抛出 ValueError"""
        monkeypatch.setenv("SENTRY_SAMPLE_RATE", "abc")
        from agent.error_reporting_config import get_config
        with pytest.raises(ValueError):
            get_config()

    def test_sentry_traces_sample_rate_zero_by_default(self, monkeypatch):
        """链路采样率默认 0（仅错误不采链路）"""
        monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
        from agent.error_reporting_config import get_config
        config = get_config()
        assert config["sentry"]["traces_sample_rate"] == 0.0


# ─── Sentry SDK 初始化测试 ──────────────────────────────────────────

class TestSentryInit:
    """Sentry SDK 初始化测试"""

    def setup_method(self):
        _reset_module()

    def teardown_method(self):
        _reset_module()

    def test_init_sentry_returns_false_when_disabled(self, monkeypatch):
        """DSN 未配置时 init_sentry 返回 False"""
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        from agent.error_reporting_config import init_sentry, is_sentry_enabled
        assert init_sentry() is False
        assert is_sentry_enabled() is False

    def test_init_sentry_returns_false_when_dsn_empty(self, monkeypatch):
        """DSN 为空字符串时 init_sentry 返回 False"""
        monkeypatch.setenv("SENTRY_DSN", "")
        from agent.error_reporting_config import init_sentry
        assert init_sentry() is False

    def test_init_sentry_returns_false_when_dsn_invalid(self, monkeypatch):
        """DSN 格式非法（不以 http(s):// 开头）时 init_sentry 返回 False"""
        monkeypatch.setenv("SENTRY_DSN", "not-a-url")
        from agent.error_reporting_config import init_sentry, is_sentry_enabled
        result = init_sentry()
        assert result is False
        assert is_sentry_enabled() is False

    def test_init_sentry_returns_false_when_sdk_missing(self, monkeypatch):
        """sentry-sdk 未安装时 init_sentry 返回 False（不抛异常）"""
        monkeypatch.setenv("SENTRY_DSN", "https://abc@example.com/1")
        # 模拟 sentry_sdk 导入失败
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sentry_sdk" or name.startswith("sentry_sdk."):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            from agent.error_reporting_config import init_sentry
            result = init_sentry()
            assert result is False
        finally:
            builtins.__import__ = real_import

    def test_init_sentry_returns_true_when_sdk_available(self, monkeypatch):
        """sentry-sdk 安装且 DSN 合法时 init_sentry 返回 True"""
        _reset_module()
        monkeypatch.setenv("SENTRY_DSN", "https://abc@example.com/1")
        # Mock sentry_sdk 以避免真实上报
        mock_sentry = MagicMock()
        mock_logging_integration = MagicMock()
        with patch.dict(sys.modules, {
            "sentry_sdk": mock_sentry,
            "sentry_sdk.integrations": MagicMock(),
            "sentry_sdk.integrations.logging": MagicMock(),
        }):
            # patch LoggingIntegration
            with patch("sentry_sdk.integrations.logging.LoggingIntegration",
                       mock_logging_integration):
                from agent.error_reporting_config import init_sentry, is_sentry_enabled
                result = init_sentry()
                assert result is True
                assert is_sentry_enabled() is True
                # 确认 sentry_sdk.init 被调用
                mock_sentry.init.assert_called_once()

    def test_init_sentry_idempotent(self, monkeypatch):
        """重复调用 init_sentry 不会重复初始化"""
        _reset_module()
        monkeypatch.setenv("SENTRY_DSN", "https://abc@example.com/1")
        mock_sentry = MagicMock()
        with patch.dict(sys.modules, {
            "sentry_sdk": mock_sentry,
            "sentry_sdk.integrations": MagicMock(),
            "sentry_sdk.integrations.logging": MagicMock(),
        }):
            with patch("sentry_sdk.integrations.logging.LoggingIntegration"):
                from agent.error_reporting_config import init_sentry
                init_sentry()
                init_sentry()  # 第二次应直接返回 True
                # sentry_sdk.init 只应被调用一次
                assert mock_sentry.init.call_count == 1


# ─── 敏感信息过滤测试 ────────────────────────────────────────────────

class TestSensitiveFilter:
    """敏感信息过滤测试"""

    def test_is_sensitive_key_password(self):
        """password 字段应被识别为敏感"""
        from agent.error_reporting_config import _is_sensitive_key
        assert _is_sensitive_key("password") is True
        assert _is_sensitive_key("Password") is True
        assert _is_sensitive_key("user_password") is True

    def test_is_sensitive_key_token(self):
        """token 字段应被识别为敏感"""
        from agent.error_reporting_config import _is_sensitive_key
        assert _is_sensitive_key("token") is True
        assert _is_sensitive_key("access_token") is True
        assert _is_sensitive_key("refresh_token") is True
        assert _is_sensitive_key("api_key") is True

    def test_is_sensitive_key_id_card(self):
        """身份证字段应被识别为敏感"""
        from agent.error_reporting_config import _is_sensitive_key
        assert _is_sensitive_key("id_card") is True
        assert _is_sensitive_key("idcard") is True

    def test_is_sensitive_key_bank_card(self):
        """银行卡字段应被识别为敏感"""
        from agent.error_reporting_config import _is_sensitive_key
        assert _is_sensitive_key("bank_card") is True
        assert _is_sensitive_key("card_number") is True

    def test_is_sensitive_key_non_sensitive(self):
        """非敏感字段应返回 False"""
        from agent.error_reporting_config import _is_sensitive_key
        assert _is_sensitive_key("username") is False
        assert _is_sensitive_key("email") is False
        assert _is_sensitive_key("action") is False
        assert _is_sensitive_key("") is False
        assert _is_sensitive_key(None) is False

    def test_filter_sensitive_dict(self):
        """字典中的敏感字段值应被替换为 [REDACTED]"""
        from agent.error_reporting_config import _filter_sensitive_recursive
        data = {
            "username": "alice",
            "password": "secret123",
            "token": "abc-xyz",
        }
        filtered = _filter_sensitive_recursive(data)
        assert filtered["username"] == "alice"
        assert filtered["password"] == "[REDACTED]"
        assert filtered["token"] == "[REDACTED]"

    def test_filter_sensitive_nested(self):
        """嵌套字典中的敏感字段应被过滤"""
        from agent.error_reporting_config import _filter_sensitive_recursive
        data = {
            "user": {
                "name": "bob",
                "password": "p@ss",
            },
            "items": [
                {"id": 1, "api_key": "key1"},
                {"id": 2, "name": "item2"},
            ],
        }
        filtered = _filter_sensitive_recursive(data)
        assert filtered["user"]["name"] == "bob"
        assert filtered["user"]["password"] == "[REDACTED]"
        assert filtered["items"][0]["api_key"] == "[REDACTED]"
        assert filtered["items"][1]["name"] == "item2"

    def test_filter_sensitive_list(self):
        """列表中的敏感字段应被过滤"""
        from agent.error_reporting_config import _filter_sensitive_recursive
        data = [
            {"password": "a"},
            {"token": "b"},
        ]
        filtered = _filter_sensitive_recursive(data)
        assert filtered[0]["password"] == "[REDACTED]"
        assert filtered[1]["token"] == "[REDACTED]"

    def test_filter_sensitive_primitives_unchanged(self):
        """原始类型应原样返回"""
        from agent.error_reporting_config import _filter_sensitive_recursive
        assert _filter_sensitive_recursive(42) == 42
        assert _filter_sensitive_recursive("hello") == "hello"
        assert _filter_sensitive_recursive(None) is None
        assert _filter_sensitive_recursive(True) is True

    def test_filter_sensitive_string_token_pattern(self):
        """字符串中的 token=xxx 模式应被遮罩"""
        from agent.error_reporting_config import _filter_sensitive_recursive
        s = "auth token=abcdef123456 header"
        filtered = _filter_sensitive_recursive(s)
        assert "abcdef123456" not in filtered
        assert "[REDACTED]" in filtered

    def test_before_send_filters_extra(self):
        """before_send 钩子应过滤 extra 字段"""
        from agent.error_reporting_config import _sentry_before_send
        event = {
            "extra": {
                "username": "alice",
                "password": "secret",
            }
        }
        result = _sentry_before_send(event, {})
        assert result["extra"]["password"] == "[REDACTED]"
        assert result["extra"]["username"] == "alice"

    def test_before_send_filters_request_headers(self):
        """before_send 钩子应过滤 request.headers 中的敏感字段"""
        from agent.error_reporting_config import _sentry_before_send
        event = {
            "request": {
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer xxx",
                    "X-Api-Key": "secret",
                }
            }
        }
        result = _sentry_before_send(event, {})
        assert result["request"]["headers"]["X-Api-Key"] == "[REDACTED]"
        # Authorization 不在默认敏感列表中，但 api_key 是
        assert result["request"]["headers"]["Content-Type"] == "application/json"

    def test_before_send_injects_trace_id(self):
        """before_send 钩子应注入 trace_id 到 tags"""
        from agent.error_reporting_config import _sentry_before_send
        with patch("agent.error_reporting_config._safe_get_trace_id",
                   return_value="test-trace-123"):
            event = {"extra": {}}
            result = _sentry_before_send(event, {})
            assert result["tags"]["trace_id"] == "test-trace-123"

    def test_before_send_swallows_exceptions(self):
        """before_send 钩子内部异常应被吞掉，返回原 event"""
        from agent.error_reporting_config import _sentry_before_send
        # event 不是字典，filter_sensitive_recursive 会原样返回，不应抛异常
        event = "not-a-dict"
        result = _sentry_before_send(event, {})
        assert result == event


# ─── capture_error / capture_message 测试 ────────────────────────────

class TestCaptureAPI:
    """主动上报 API 测试"""

    def setup_method(self):
        _reset_module()

    def teardown_method(self):
        _reset_module()

    def test_capture_error_returns_none_when_not_initialized(self):
        """未初始化时 capture_error 返回 None"""
        from agent.error_reporting_config import capture_error
        result = capture_error(ValueError("test"))
        assert result is None

    def test_capture_message_returns_none_when_not_initialized(self):
        """未初始化时 capture_message 返回 None"""
        from agent.error_reporting_config import capture_error, capture_message
        result = capture_message("test message")
        assert result is None

    def test_capture_error_filters_sensitive_context(self, monkeypatch):
        """capture_error 应过滤敏感上下文"""
        _reset_module()
        monkeypatch.setenv("SENTRY_DSN", "https://abc@example.com/1")
        mock_sentry = MagicMock()
        mock_sentry.capture_exception.return_value = "event-123"
        # push_scope() 返回上下文管理器，其 __enter__ 返回 scope 对象
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope
        with patch.dict(sys.modules, {
            "sentry_sdk": mock_sentry,
            "sentry_sdk.integrations": MagicMock(),
            "sentry_sdk.integrations.logging": MagicMock(),
        }):
            with patch("sentry_sdk.integrations.logging.LoggingIntegration"):
                from agent.error_reporting_config import init_sentry, capture_error
                init_sentry()
                result = capture_error(
                    ValueError("test"),
                    context={"password": "secret", "username": "alice"},
                )
                assert result == "event-123"
                # scope.set_context 应被调用，参数为 ("extra", filtered_dict)
                mock_scope.set_context.assert_called()
                args = mock_scope.set_context.call_args
                filtered = args[0][1]
                assert filtered["password"] == "[REDACTED]"
                assert filtered["username"] == "alice"

    def test_capture_error_failure_returns_none(self, monkeypatch):
        """capture_error 内部异常应返回 None（不抛出）"""
        _reset_module()
        monkeypatch.setenv("SENTRY_DSN", "https://abc@example.com/1")
        mock_sentry = MagicMock()
        mock_sentry.capture_exception.side_effect = Exception("network error")
        with patch.dict(sys.modules, {
            "sentry_sdk": mock_sentry,
            "sentry_sdk.integrations": MagicMock(),
            "sentry_sdk.integrations.logging": MagicMock(),
        }):
            with patch("sentry_sdk.integrations.logging.LoggingIntegration"):
                from agent.error_reporting_config import init_sentry, capture_error
                init_sentry()
                result = capture_error(ValueError("test"))
                assert result is None


# ─── SentryReporter 集成测试 ─────────────────────────────────────────

class TestSentryReporter:
    """SentryReporter 类测试"""

    def test_sentry_reporter_returns_false_when_disabled(self):
        """Sentry 启用时 enabled=False 应返回 False"""
        from agent.monitoring.error_reporter import SentryReporter, ErrorReport
        reporter = SentryReporter({"enabled": False, "min_level": "error"})
        report = ErrorReport(
            error_type="TestError",
            error_message="test",
            traceback="",
            timestamp="2026-01-01T00:00:00",
            level="error",
        )
        assert reporter.send(report) is False

    def test_sentry_reporter_returns_false_when_not_initialized(self):
        """Sentry 未初始化时 send 应返回 False"""
        from agent.monitoring.error_reporter import SentryReporter, ErrorReport
        reporter = SentryReporter({
            "enabled": True,
            "min_level": "error",
            "dsn": "https://abc@example.com/1",
        })
        report = ErrorReport(
            error_type="TestError",
            error_message="test",
            traceback="",
            timestamp="2026-01-01T00:00:00",
            level="error",
        )
        # 未调用 init_sentry 时 is_sentry_enabled() 返回 False
        with patch("agent.error_reporting_config.is_sentry_enabled",
                   return_value=False):
            assert reporter.send(report) is False

    def test_sentry_reporter_calls_capture_error(self):
        """Sentry 启用且初始化时 send 应调用 capture_error"""
        from agent.monitoring.error_reporter import SentryReporter, ErrorReport
        reporter = SentryReporter({
            "enabled": True,
            "min_level": "error",
            "dsn": "https://abc@example.com/1",
        })
        report = ErrorReport(
            error_type="TestError",
            error_message="test error",
            traceback="",
            timestamp="2026-01-01T00:00:00",
            level="error",
            trace_id="trace-123",
            user_id="user-456",
        )
        with patch("agent.error_reporting_config.is_sentry_enabled",
                   return_value=True), \
             patch("agent.error_reporting_config.capture_error",
                   return_value="event-abc") as mock_capture:
            result = reporter.send(report)
            assert result is True
            mock_capture.assert_called_once()
            # 验证 trace_id / user_id 传入 context
            call_kwargs = mock_capture.call_args.kwargs
            assert call_kwargs["trace_id"] == "trace-123"
            assert call_kwargs["user_id"] == "user-456"

    def test_sentry_reporter_failure_returns_false(self):
        """capture_error 抛异常时 send 应返回 False（不抛出）"""
        from agent.monitoring.error_reporter import SentryReporter, ErrorReport
        reporter = SentryReporter({
            "enabled": True,
            "min_level": "error",
            "dsn": "https://abc@example.com/1",
        })
        report = ErrorReport(
            error_type="TestError",
            error_message="test",
            traceback="",
            timestamp="2026-01-01T00:00:00",
            level="error",
        )
        with patch("agent.error_reporting_config.is_sentry_enabled",
                   return_value=True), \
             patch("agent.error_reporting_config.capture_error",
                   side_effect=Exception("boom")):
            result = reporter.send(report)
            assert result is False


# ─── 边界场景测试 ────────────────────────────────────────────────────

class TestEdgeCases:
    """边界场景测试"""

    def test_set_sensitive_patterns_replaces_defaults(self):
        """set_sensitive_patterns 应替换默认模式"""
        from agent.error_reporting_config import (
            set_sensitive_patterns,
            _is_sensitive_key,
        )
        # 替换为仅匹配 custom_field
        set_sensitive_patterns(("custom_field",))
        try:
            assert _is_sensitive_key("custom_field") is True
            assert _is_sensitive_key("password") is False  # 已被替换
        finally:
            # 恢复默认
            from agent.error_reporting_config import _DEFAULT_SENSITIVE_PATTERNS
            set_sensitive_patterns(_DEFAULT_SENSITIVE_PATTERNS)

    def test_safe_get_trace_id_failure_returns_empty(self):
        """_safe_get_trace_id 在 tracing 不可用时应返回空串"""
        from agent.error_reporting_config import _safe_get_trace_id
        with patch("agent.monitoring.tracing.get_trace_id",
                   side_effect=ImportError("no module")):
            assert _safe_get_trace_id() == ""
