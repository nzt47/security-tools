import pytest
import os
from unittest.mock import patch
from agent.error_reporting_config import (
    get_config,
    _filter_sensitive_recursive,
    _sentry_before_send,
    _is_sensitive_key,
    set_sensitive_patterns,
    _reset_for_test,
    is_sentry_enabled,
)


class TestErrorReportingConfig:
    """错误上报配置测试"""

    def test_get_config_default(self):
        """测试获取默认配置"""
        config = get_config()
        
        assert config is not None
        assert isinstance(config, dict)
        
        # 检查控制台上报配置
        assert "console" in config
        assert config["console"]["enabled"] is True
        assert config["console"]["min_level"] == "warning"
        
        # 检查文件上报配置
        assert "file" in config
        assert config["file"]["enabled"] is True
        assert config["file"]["file_path"] == "./logs/digital_life_errors.log"
        assert config["file"]["min_level"] == "error"
        
        # 检查 webhook 上报配置
        assert "webhook" in config
        assert config["webhook"]["enabled"] is False
        assert config["webhook"]["url"] == ""
        assert config["webhook"]["timeout"] == 5
        
        # 检查 Slack 上报配置
        assert "slack" in config
        assert config["slack"]["enabled"] is False
        assert config["slack"]["channel"] == "#digital-life-alerts"
        assert config["slack"]["username"] == "Digital Life Bot"
        
        # 检查 email 上报配置
        assert "email" in config
        assert config["email"]["enabled"] is False

    def test_get_config_with_env_vars(self, monkeypatch):
        """测试通过环境变量覆盖配置"""
        monkeypatch.setenv("ERROR_REPORTING_CONSOLE_LEVEL", "debug")
        monkeypatch.setenv("ERROR_REPORTING_FILE_ENABLED", "false")
        monkeypatch.setenv("ERROR_REPORTING_FILE_PATH", "/custom/logs/errors.log")
        monkeypatch.setenv("ERROR_REPORTING_WEBHOOK_ENABLED", "true")
        monkeypatch.setenv("ERROR_REPORTING_WEBHOOK_URL", "https://example.com/webhook")
        monkeypatch.setenv("ERROR_REPORTING_WEBHOOK_TIMEOUT", "10")
        monkeypatch.setenv("ERROR_REPORTING_SLACK_ENABLED", "true")
        monkeypatch.setenv("ERROR_REPORTING_SLACK_CHANNEL", "#custom-channel")
        monkeypatch.setenv("ERROR_REPORTING_SLACK_USERNAME", "Custom Bot")
        
        config = get_config()
        
        assert config["console"]["min_level"] == "debug"
        assert config["file"]["enabled"] is False
        assert config["file"]["file_path"] == "/custom/logs/errors.log"
        assert config["webhook"]["enabled"] is True
        assert config["webhook"]["url"] == "https://example.com/webhook"
        assert config["webhook"]["timeout"] == 10
        assert config["slack"]["enabled"] is True
        assert config["slack"]["channel"] == "#custom-channel"
        assert config["slack"]["username"] == "Custom Bot"

    def test_get_config_env_var_case_insensitive(self, monkeypatch):
        """测试环境变量值不区分大小写"""
        monkeypatch.setenv("ERROR_REPORTING_FILE_ENABLED", "TRUE")
        monkeypatch.setenv("ERROR_REPORTING_WEBHOOK_ENABLED", "FALSE")
        
        config = get_config()
        
        assert config["file"]["enabled"] is True
        assert config["webhook"]["enabled"] is False

    def test_get_config_webhook_headers(self):
        """测试 webhook 请求头配置"""
        config = get_config()
        
        assert "headers" in config["webhook"]
        assert config["webhook"]["headers"]["Content-Type"] == "application/json"

    def test_get_config_timeout_default(self):
        """测试默认超时时间"""
        config = get_config()
        
        assert config["webhook"]["timeout"] == 5

    def test_get_config_slack_icon_emoji(self):
        """测试 Slack emoji 配置"""
        config = get_config()

        assert config["slack"]["icon_emoji"] == ":robot_face:"


# ═══════════════════════════════════════════════════════════════
# P0 高风险分支测试
# 覆盖行号：384-385（str token 替换）、403-413（before_send 主逻辑）
# 状态同步机制：monkeypatch 隔离全局状态，_reset_for_test 保证用例独立
# ═══════════════════════════════════════════════════════════════


class TestFilterSensitiveStringTokens:
    """P0-2: 覆盖 _filter_sensitive_recursive 中 str 分支（行 382-390）

    覆盖场景：
    - token=xxx 等号分隔模式
    - api_key: xxx 冒号分隔模式
    - Bearer xxx 认证头模式
    - 无匹配时原样返回
    """

    def test_token_equals_pattern(self):
        """token=xxx → token=[REDACTED]"""
        result = _filter_sensitive_recursive("token=abc123def456")
        assert "[REDACTED]" in result
        assert "abc123def456" not in result

    def test_api_key_colon_pattern(self):
        """api_key: xxx → api_key: [REDACTED]（冒号分隔）"""
        result = _filter_sensitive_recursive("api_key: sk-secret-value")
        assert "[REDACTED]" in result
        assert "sk-secret-value" not in result

    def test_apikey_hyphen_equals_pattern(self):
        """api-key=xxx → api-key=[REDACTED]"""
        result = _filter_sensitive_recursive("api-key=sk-xxxx")
        assert "[REDACTED]" in result

    def test_bearer_token_pattern(self):
        """Bearer xxx → Bearer [REDACTED]（P0-SEC-001 修复后 token 值完全脱敏）

        修复前：split('=') 保留 token 值 → 'Bearer abc.def.ghi+jkl=[REDACTED]'
        修复后：Bearer 模式独立处理 → 'Bearer [REDACTED]'
        """
        result = _filter_sensitive_recursive("Bearer abc.def.ghi+jkl=")
        assert "[REDACTED]" in result
        # 收紧断言：token 值必须被完全脱敏，不得残留
        assert "abc.def.ghi" not in result
        assert "abc.def.ghi+jkl" not in result
        assert result == "Bearer [REDACTED]"

    def test_bearer_token_without_trailing_equals(self):
        """Bearer token 无尾随 = 也应完全脱敏"""
        result = _filter_sensitive_recursive("Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
        assert result == "Bearer [REDACTED]"
        assert "eyJhbGciOiJIUzI1NiJ9" not in result

    def test_secret_equals_pattern(self):
        """secret=xxx → secret=[REDACTED]"""
        result = _filter_sensitive_recursive("secret=my_secret_value")
        assert "[REDACTED]" in result
        assert "my_secret_value" not in result

    def test_password_equals_pattern(self):
        """password=xxx → password=[REDACTED]"""
        result = _filter_sensitive_recursive("password=p@ssw0rd123")
        assert "[REDACTED]" in result
        assert "p@ssw0rd123" not in result

    def test_no_match_returns_original(self):
        """无敏感模式时原样返回"""
        original = "hello world 12345"
        result = _filter_sensitive_recursive(original)
        assert result == original

    def test_mixed_content_partial_redaction(self):
        """混合内容仅替换敏感部分（P0-SEC-002 修复后支持 & 分隔）

        修复前：\\S+ 贪婪匹配吞噬 &page=1 → 'user=admin&token=[REDACTED]'
        修复后：[^&\\s]+ 遇 & 停止 → 'user=admin&token=[REDACTED]&page=1'
        """
        # 场景1：& 分隔的 URL 查询参数
        text1 = "user=admin&token=sk-secret-123&page=1"
        result1 = _filter_sensitive_recursive(text1)
        assert "[REDACTED]" in result1
        assert "sk-secret-123" not in result1
        assert "admin" in result1
        assert "page=1" in result1  # 相邻参数不被吞噬

        # 场景2：空格分隔（原有兼容场景）
        text2 = "user=admin token=sk-secret-123 page=1"
        result2 = _filter_sensitive_recursive(text2)
        assert "[REDACTED]" in result2
        assert "sk-secret-123" not in result2
        assert "admin" in result2
        assert "page=1" in result2


class TestFilterSensitiveContainers:
    """P1 补充: 覆盖 list/tuple 递归分支（行 378-381）"""

    def test_list_with_nested_dicts(self):
        """list 内嵌含敏感字段的 dict"""
        result = _filter_sensitive_recursive([
            {"password": "secret1", "name": "alice"},
            {"api_key": "sk-xxx", "name": "bob"},
        ])
        assert result[0]["password"] == "[REDACTED]"
        assert result[0]["name"] == "alice"
        assert result[1]["api_key"] == "[REDACTED]"
        assert result[1]["name"] == "bob"

    def test_tuple_with_nested_dicts(self):
        """tuple 内嵌含敏感字段的 dict"""
        result = _filter_sensitive_recursive((
            {"token": "tok-abc"},
            {"name": "charlie"},
        ))
        assert isinstance(result, tuple)
        assert result[0]["token"] == "[REDACTED]"
        assert result[1]["name"] == "charlie"

    def test_deeply_nested_structure(self):
        """深层嵌套结构递归脱敏"""
        data = {
            "level1": {
                "level2": [
                    {"level3": {"password": "deep_secret"}}
                ]
            }
        }
        result = _filter_sensitive_recursive(data)
        assert result["level1"]["level2"][0]["level3"]["password"] == "[REDACTED]"


class TestSentryBeforeSendP0:
    """P0-1: 覆盖 _sentry_before_send 主逻辑（行 403-426）

    覆盖场景：
    - 脱敏 extra/request/breadcrumbs
    - trace_id 注入到 tags
    - breadcrumb 追加
    - 非 dict event 原样返回
    - 异常时不阻塞上报
    """

    def test_filters_sensitive_in_extra(self):
        """extra 中敏感字段被脱敏"""
        event = {
            "extra": {"password": "secret123", "api_key": "sk-xxx"},
        }
        result = _sentry_before_send(event, {})
        assert result["extra"]["password"] == "[REDACTED]"
        assert result["extra"]["api_key"] == "[REDACTED]"

    def test_injects_trace_id_to_tags(self):
        """trace_id 注入到 tags"""
        event = {"tags": {}}
        result = _sentry_before_send(event, {})
        assert "trace_id" in result["tags"]
        assert result["tags"]["trace_id"]  # 非空

    def test_injects_trace_id_with_mock(self):
        """使用 mock 验证 trace_id 值正确注入"""
        with patch("agent.error_reporting_config._safe_get_trace_id", return_value="trace-abc-123"):
            result = _sentry_before_send({"tags": {}}, {})
        assert result["tags"]["trace_id"] == "trace-abc-123"

    def test_appends_breadcrumb(self):
        """breadcrumb 被追加（行 416-426）"""
        event = {"breadcrumbs": {"values": []}}
        result = _sentry_before_send(event, {})
        values = result["breadcrumbs"]["values"]
        assert len(values) == 1
        assert values[0]["type"] == "debug"
        assert values[0]["category"] == "yunshu.before_send"
        assert "trace_id" in values[0]["data"]

    def test_creates_breadcrumbs_if_missing(self):
        """无 breadcrumbs 字段时自动创建"""
        event = {}
        result = _sentry_before_send(event, {})
        assert "breadcrumbs" in result
        assert "values" in result["breadcrumbs"]
        assert len(result["breadcrumbs"]["values"]) == 1

    def test_preserves_existing_breadcrumbs(self):
        """已有 breadcrumbs 时追加而非覆盖"""
        event = {
            "breadcrumbs": {
                "values": [{"message": "existing", "type": "navigation"}]
            }
        }
        result = _sentry_before_send(event, {})
        values = result["breadcrumbs"]["values"]
        assert len(values) == 2
        assert values[0]["message"] == "existing"
        assert values[1]["category"] == "yunshu.before_send"

    def test_non_dict_event_passthrough(self):
        """非 dict event 原样返回（行 407 else 分支）"""
        result = _sentry_before_send("not-a-dict", {})
        assert result == "not-a-dict"

    def test_non_dict_event_int(self):
        """int 类型 event 原样返回"""
        result = _sentry_before_send(42, {})
        assert result == 42

    def test_filters_request_headers(self):
        """request.headers 中敏感字段被脱敏"""
        event = {
            "request": {
                "headers": {
                    "Authorization": "Bearer abc.def.ghi",
                    "X-Api-Key": "sk-secret-key",
                    "Content-Type": "application/json",
                }
            }
        }
        result = _sentry_before_send(event, {})
        assert result["request"]["headers"]["Authorization"] == "[REDACTED]"
        assert result["request"]["headers"]["X-Api-Key"] == "[REDACTED]"
        assert result["request"]["headers"]["Content-Type"] == "application/json"

    def test_tags_not_dict_skips_trace_injection(self):
        """tags 为非 dict 类型时跳过 trace_id 注入（不抛异常）"""
        event = {"tags": ["not", "a", "dict"]}
        result = _sentry_before_send(event, {})
        # 不应抛异常，tags 保持原样
        assert result["tags"] == ["not", "a", "dict"]

    def test_full_event_flow(self):
        """完整事件流程：脱敏 + trace_id + breadcrumb"""
        event = {
            "event_id": "evt-001",
            "level": "error",
            "message": "测试错误",
            "extra": {"password": "p@ss", "order_id": "ORD-001"},
            "request": {
                "url": "http://localhost/api/test",
                "headers": {"Authorization": "Bearer xyz.abc"},
            },
            "tags": {"env": "test"},
            "breadcrumbs": {"values": [{"message": "step1"}]},
        }
        result = _sentry_before_send(event, {})

        # 脱敏验证
        assert result["extra"]["password"] == "[REDACTED]"
        assert result["extra"]["order_id"] == "ORD-001"
        assert result["request"]["headers"]["Authorization"] == "[REDACTED]"

        # trace_id 注入
        assert "trace_id" in result["tags"]

        # breadcrumb 追加
        values = result["breadcrumbs"]["values"]
        assert len(values) == 2
        assert values[1]["category"] == "yunshu.before_send"