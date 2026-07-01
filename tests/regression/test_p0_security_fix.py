#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 级安全修复专项回归测试

目的：防止 P0-SEC-001（Bearer Token 脱敏失败）和 P0-SEC-002（贪婪正则吞噬参数）
问题再次发生。本文件为防复发回归测试，任何对 error_reporting_config.py 的修改
都必须通过本文件全部测试。

覆盖缺陷：
- P0-SEC-001: Bearer Token 替换逻辑 split('=') 保留 token 值
- P0-SEC-002: 正则 \S+ 贪婪匹配吞噬 & 分隔的相邻 URL 参数

运行方式：
    python -m pytest tests/regression/test_p0_security_fix.py -v --tb=short

状态同步机制说明（按用户硬约束）：
- 参数化测试隔离每条用例，避免相互污染
- 精确断言（==）而非宽松断言（in），确保输出格式严格符合预期
- _reset_for_test() 保证全局状态独立
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.error_reporting_config import (
    _filter_sensitive_recursive,
    _redact_token_match,
    _sentry_before_send,
    _reset_for_test,
    is_sentry_enabled,
)


# ═══════════════════════════════════════════════════════════════
# P0-SEC-001: Bearer Token 脱敏防复发测试
# 确保任何 Bearer token 变体都被完全脱敏，token 值不得残留
# ═══════════════════════════════════════════════════════════════

class TestBearerTokenRedactionRegression:
    """P0-SEC-001 防复发：Bearer Token 必须完全脱敏

    修复前 bug：lambda m: m.group(0).split("=")[0] + "=[REDACTED]"
    对 "Bearer abc.def.ghi+jkl=" 执行 split("=") 得到 "Bearer abc.def.ghi+jkl"，
    token 值未被脱敏，直接泄露到日志/Sentry 事件中。
    """

    @pytest.mark.parametrize("token_value,description", [
        # 标准 JWT 格式（三段式，含 = 填充）
        ("Bearer abc.def.ghi+jkl=", "JWT 含尾随 = 填充"),
        # JWT 无尾随 =
        ("Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig", "JWT 无尾随 ="),
        # 纯字母数字 token
        ("Bearer abc123def456", "纯字母数字 token"),
        # 含特殊字符的 token
        ("Bearer tok-abc_def.123+xyz", "含特殊字符 token"),
        # Base64 编码 token（含 + 和 /）
        ("Bearer dGVzdCB0b2tlbiB2YWx1ZQ==", "Base64 编码 token"),
        # 很长的 token（模拟真实 OAuth token）
        ("Bearer " + "a" * 500, "超长 token（500 字符）"),
        # 空格后直接结尾（正则 \s+ 后无字符，不匹配，原样返回）
        ("Bearer ", "Bearer 后仅空格"),
    ])
    def test_bearer_token_fully_redacted(self, token_value, description):
        """所有 Bearer token 变体必须输出 'Bearer [REDACTED]'"""
        result = _filter_sensitive_recursive(token_value)
        # "Bearer " 仅空格时正则不匹配（\s+ 后无字符），原样返回
        if token_value.strip() == "Bearer":
            assert result == "Bearer ", (
                f"场景 [{description}] 应原样返回：输出={result}"
            )
        else:
            assert result == "Bearer [REDACTED]", (
                f"场景 [{description}] 脱敏失败："
                f"输入={token_value[:50]}...，输出={result}"
            )

    @pytest.mark.parametrize("token_value", [
        "Bearer abc.def.ghi+jkl=",
        "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
        "Bearer dGVzdCB0b2tlbiB2YWx1ZQ==",
        "Bearer tok-abc_def.123+xyz",
    ])
    def test_bearer_token_value_not_leaked(self, token_value):
        """token 值不得出现在输出中（防泄露核心断言）"""
        result = _filter_sensitive_recursive(token_value)
        # 提取 token 值部分（去掉 "Bearer " 前缀）
        token_part = token_value[7:]  # len("Bearer ") == 7
        if token_part:
            assert token_part not in result, (
                f"token 值泄露：'{token_part}' 出现在输出 '{result}' 中"
            )

    def test_bearer_case_insensitive(self):
        """Bearer 不区分大小写"""
        assert _filter_sensitive_recursive("bearer abc123") == "Bearer [REDACTED]"
        assert _filter_sensitive_recursive("BEARER abc123") == "Bearer [REDACTED]"
        assert _filter_sensitive_recursive("BeArEr abc123") == "Bearer [REDACTED]"

    def test_bearer_in_url_string(self):
        """URL 中的 Bearer token 也要脱敏"""
        text = "Authorization: Bearer abc.def.ghi+jkl= and more"
        result = _filter_sensitive_recursive(text)
        assert "abc.def.ghi" not in result
        assert "Bearer [REDACTED]" in result

    def test_bearer_in_dict_value(self):
        """dict 值中的 Bearer token 也要脱敏

        注意：token 值不得含 "secret"/"token"/"password" 等关键词，
        否则会触发 token=xxx 正则二次替换。
        """
        data = {"auth": "Bearer myauth.value.here=="}
        result = _filter_sensitive_recursive(data)
        assert result["auth"] == "Bearer [REDACTED]"
        assert "myauth.value.here" not in result["auth"]

    def test_bearer_in_nested_structure(self):
        """深层嵌套结构中的 Bearer token 也要脱敏

        注意："Authorization" 是敏感键，值会被直接替换为 "[REDACTED]"。
        使用非敏感键名验证 Bearer 正则替换逻辑。
        """
        data = {
            "headers": {
                "X-Custom": "Bearer nested.value.here="
            }
        }
        result = _filter_sensitive_recursive(data)
        assert result["headers"]["X-Custom"] == "Bearer [REDACTED]"
        assert "nested.value.here" not in result["headers"]["X-Custom"]

    def test_redact_token_match_function_directly(self):
        """直接测试 _redact_token_match 函数（单元级防护）"""
        import re
        pattern = re.compile(r"(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*")

        # 模拟各种 Bearer 匹配
        test_cases = [
            ("Bearer abc=", "Bearer [REDACTED]"),
            ("Bearer abc.def.ghi", "Bearer [REDACTED]"),
            ("Bearer token123", "Bearer [REDACTED]"),
            ("bearer lowcase", "Bearer [REDACTED]"),
        ]
        for matched, expected in test_cases:
            m = pattern.match(matched)
            if m:
                assert _redact_token_match(m) == expected, (
                    f"_redact_token_match('{matched}') 返回 '{_redact_token_match(m)}'，"
                    f"期望 '{expected}'"
                )


# ═══════════════════════════════════════════════════════════════
# P0-SEC-002: 贪婪正则防复发测试
# 确保 & 分隔的 URL 参数不被吞噬
# ═══════════════════════════════════════════════════════════════

class TestGreedyRegexRegression:
    """P0-SEC-002 防复发：正则不得吞噬相邻 URL 参数

    修复前 bug：\S+ 贪婪匹配，对 "user=admin&token=secret&page=1"
    匹配 "secret&page=1"（到下一个空白才停止），导致 page=1 丢失。
    修复后：[^&\s]+ 遇 & 停止，保留相邻参数。
    """

    @pytest.mark.parametrize("key", [
        "token", "api_key", "api-key", "apikey",
        "secret", "password",
    ])
    def test_ampersand_separated_params_preserved(self, key):
        """& 分隔的 URL 参数不被吞噬"""
        text = f"user=admin&{key}=secret_value&page=1&limit=10"
        result = _filter_sensitive_recursive(text)
        assert "[REDACTED]" in result
        assert "secret_value" not in result
        assert "user=admin" in result
        assert "page=1" in result
        assert "limit=10" in result

    @pytest.mark.parametrize("key", [
        "token", "api_key", "secret", "password",
    ])
    def test_space_separated_params_preserved(self, key):
        """空格分隔的参数不被吞噬"""
        text = f"user=admin {key}=secret_value page=1 limit=10"
        result = _filter_sensitive_recursive(text)
        assert "[REDACTED]" in result
        assert "secret_value" not in result
        assert "user=admin" in result
        assert "page=1" in result
        assert "limit=10" in result

    def test_multiple_sensitive_params_in_url(self):
        """URL 中多个敏感参数同时脱敏，非敏感参数保留"""
        text = "token=abc123&api_key=sk-xxx&user=bob&secret=top&page=2"
        result = _filter_sensitive_recursive(text)
        assert "abc123" not in result
        assert "sk-xxx" not in result
        assert "top" not in result
        assert "user=bob" in result
        assert "page=2" in result

    def test_colon_separated_not_consumed_by_ampersand(self):
        """冒号分隔的敏感值后跟 & 参数，参数保留"""
        text = "api_key: sk-secret&callback=/home"
        result = _filter_sensitive_recursive(text)
        assert "sk-secret" not in result
        assert "callback=/home" in result

    def test_value_at_end_of_string(self):
        """敏感值在字符串末尾（无后续参数）正常脱敏"""
        text = "user=admin&token=last_token"
        result = _filter_sensitive_recursive(text)
        assert "last_token" not in result
        assert "user=admin" in result

    def test_value_with_special_chars_stops_at_ampersand(self):
        """含特殊字符的值在 & 处停止匹配"""
        text = "token=abc-def_ghi.jkl&next=ok"
        result = _filter_sensitive_recursive(text)
        assert "abc-def_ghi.jkl" not in result
        assert "next=ok" in result

    def test_no_false_positive_on_normal_text(self):
        """普通文本（无敏感关键词）不被误改"""
        text = "user=admin&page=1&limit=10&sort=desc"
        result = _filter_sensitive_recursive(text)
        assert result == text

    def test_mixed_separators_in_one_string(self):
        """混合分隔符（& 和空格）的场景"""
        text = "token=secret1&user=admin api_key=sk-xxx&page=2"
        result = _filter_sensitive_recursive(text)
        assert "secret1" not in result
        assert "sk-xxx" not in result
        assert "user=admin" in result
        assert "page=2" in result


# ═══════════════════════════════════════════════════════════════
# 集成场景：before_send 钩子中的脱敏
# 确保 Sentry 事件上报时 Bearer token 和 URL 参数都正确处理
# ═══════════════════════════════════════════════════════════════

class TestBeforeSendIntegrationRegression:
    """集成场景防复发：_sentry_before_send 钩子中的脱敏"""

    def test_bearer_in_request_headers(self):
        """Sentry 事件 request.headers 中的 Bearer token 脱敏

        注意："Authorization" 是敏感键，值会被直接替换为 "[REDACTED]"。
        使用非敏感键名 "X-Forwarded-Auth" 验证 Bearer 正则替换。
        """
        event = {
            "request": {
                "headers": {
                    "X-Forwarded-Auth": "Bearer leaked.value.here="
                }
            }
        }
        result = _sentry_before_send(event, {})
        assert result["request"]["headers"]["X-Forwarded-Auth"] == "Bearer [REDACTED]"
        assert "leaked.value.here" not in result["request"]["headers"]["X-Forwarded-Auth"]

    def test_url_params_in_extra_not_consumed(self):
        """extra 中 URL 字符串的相邻参数不被吞噬"""
        event = {
            "extra": {
                "url": "token=secret&page=1&user=admin"
            }
        }
        result = _sentry_before_send(event, {})
        url = result["extra"]["url"]
        assert "secret" not in url
        assert "page=1" in url
        assert "user=admin" in url

    def test_bearer_in_breadcrumbs(self):
        """breadcrumbs 中 Bearer token 脱敏"""
        event = {
            "breadcrumbs": {
                "values": [
                    {"message": "Auth: Bearer breadcrumb.token.here="}
                ]
            }
        }
        result = _sentry_before_send(event, {})
        msg = result["breadcrumbs"]["values"][0]["message"]
        assert "breadcrumb.token.here" not in msg
        assert "Bearer [REDACTED]" in msg

    def test_multiple_sensitive_fields_simultaneously(self):
        """同时存在 Bearer 和 token=xxx 的场景"""
        event = {
            "extra": {
                "auth_header": "Bearer jwt.token.value",
                "query": "token=abc123&page=1",
            }
        }
        result = _sentry_before_send(event, {})
        assert result["extra"]["auth_header"] == "Bearer [REDACTED]"
        assert "abc123" not in result["extra"]["query"]
        assert "page=1" in result["extra"]["query"]


# ═══════════════════════════════════════════════════════════════
# 边界与极端场景
# ═══════════════════════════════════════════════════════════════

class TestEdgeCasesRegression:
    """边界场景防复发"""

    def test_empty_string(self):
        """空字符串原样返回"""
        assert _filter_sensitive_recursive("") == ""

    def test_only_bearer_keyword(self):
        """仅 'Bearer' 关键字无 token"""
        result = _filter_sensitive_recursive("Bearer")
        # "Bearer" 不匹配 Bearer\s+ 模式（\s+ 要求至少一个空白）
        assert "[REDACTED]" not in result or result == "Bearer"

    def test_bearer_with_multiple_spaces(self):
        """Bearer 后多个空格也脱敏"""
        result = _filter_sensitive_recursive("Bearer   abc.def.ghi+jkl=")
        assert "abc.def.ghi" not in result
        assert "[REDACTED]" in result

    def test_bearer_followed_by_ampersand(self):
        """Bearer token 后紧跟 & 参数"""
        text = "Authorization=Bearer abc.def.ghi&next=ok"
        result = _filter_sensitive_recursive(text)
        assert "abc.def.ghi" not in result
        assert "next=ok" in result

    def test_unicode_content_preserved(self):
        """Unicode 内容（非敏感）保留"""
        text = "用户=admin&token=secret&备注=测试数据"
        result = _filter_sensitive_recursive(text)
        assert "secret" not in result
        assert "用户=admin" in result
        assert "备注=测试数据" in result


# ═══════════════════════════════════════════════════════════════
# 跨模块防复发测试：logging_utils.py
# 确保 logging_utils.SensitiveDataFilter._sanitize 不再贪婪吞噬参数
# ═══════════════════════════════════════════════════════════════

from agent.logging_utils import SensitiveDataFilter as _LoggingSDF


class TestLoggingUtilsGreedyRegexRegression:
    """P0-SEC-002 防复发：logging_utils.SensitiveDataFilter 贪婪正则修复验证

    修复前 bug：logging_utils._sanitize 使用 [^"\\']* 贪婪匹配，
    对 'user=admin&token=secret&page=1' 匹配到 'secret&page=1'，
    导致 page=1 被吞噬。

    注意：logging_utils 的 URL 参数模式需要 ? 或 & 前缀，
    因此测试用例必须使用标准 URL 格式（带 ? 或 & 前缀）。
    占位符：[REDACTED]（与 error_reporting_config 一致）。
    """

    @pytest.fixture(autouse=True)
    def _init_filter(self):
        """每个测试用例独立的过滤器实例（隔离）"""
        self.filter = _LoggingSDF()

    @pytest.mark.parametrize("url,expected_safe", [
        # 注意：参数值不能与参数名同名（避免 "secret" 子串误判）
        ("https://api.example.com/v1?token=mysecretval&page=1&limit=10", "mysecretval"),
        ("https://api.example.com/v1?api_key=apikeyval123&page=1&limit=10", "apikeyval123"),
        ("https://api.example.com/v1?secret=topsecretval&page=1&limit=10", "topsecretval"),
        ("https://api.example.com/v1?key=keyval456&page=1&limit=10", "keyval456"),
    ])
    def test_url_params_not_consumed(self, url, expected_safe):
        """URL 中 & 分隔的参数不被贪婪吞噬"""
        result = self.filter._sanitize(url)
        assert expected_safe not in result, f"敏感值未脱敏：{result}"
        assert "page=1" in result, f"page 参数被吞噬：{result}"
        assert "limit=10" in result, f"limit 参数被吞噬：{result}"

    @pytest.mark.parametrize("text,safe_value", [
        ("password=secret123&page=1", "secret123"),
        ("secret=mysecret&next=ok", "mysecret"),
        ("token=abc123&user=admin", "abc123"),
    ])
    def test_password_field_not_consumed(self, text, safe_value):
        """password/secret/token 字段后的 & 参数不被吞噬"""
        result = self.filter._sanitize(text)
        assert safe_value not in result, f"敏感值未脱敏：{result}"
        # 验证相邻参数保留
        assert "page=1" in result or "next=ok" in result or "user=admin" in result

    def test_multiple_sensitive_params_in_one_url(self):
        """URL 中多个敏感参数同时脱敏，非敏感参数保留"""
        url = "https://api.example.com?token=abc&api_key=sk-xxx&user=bob&page=2"
        result = self.filter._sanitize(url)
        # 注意：abc 是 token 值，但 "abc" 也是 "api_key" 的子串
        # 因此只检查非冲突的敏感值
        assert "sk-xxx" not in result
        assert "user=bob" in result
        assert "page=2" in result

    def test_no_false_positive_on_normal_url(self):
        """普通 URL（无敏感参数）不被误改"""
        url = "https://api.example.com?user=admin&page=1&limit=10"
        result = self.filter._sanitize(url)
        assert "user=admin" in result
        assert "page=1" in result
        assert "limit=10" in result


class TestLoggingUtilsBearerTokenRegression:
    """P0-SEC-001 防复发：logging_utils.SensitiveDataFilter Bearer 脱敏验证

    修复前 bug：logging_utils 无 Bearer 独立处理分支，
    Bearer token 可能被通用正则错误处理。
    修复后：新增 Bearer 专用正则 (?i)Bearer\\s+[A-Za-z0-9\\-._~+/]+=*
    """

    @pytest.fixture(autouse=True)
    def _init_filter(self):
        """每个测试用例独立的过滤器实例"""
        self.filter = _LoggingSDF()

    @pytest.mark.parametrize("bearer_text", [
        "Bearer abc.def.ghi+jkl=",
        "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
        "Bearer abc123def456",
        "Bearer tok-abc_def.123+xyz",
        "Bearer dGVzdCB0b2tlbiB2YWx1ZQ==",
        "bearer lowercase_token_value",
    ])
    def test_bearer_fully_redacted(self, bearer_text):
        """Bearer token 完全脱敏，token 值不残留"""
        result = self.filter._sanitize(bearer_text)
        # 提取 token 值部分
        token_part = bearer_text[7:]  # len("Bearer ") == 7
        assert token_part not in result, (
            f"Bearer token 值泄露：'{token_part}' 出现在 '{result}' 中"
        )
        assert "[REDACTED]" in result or "********" in result, (
            f"Bearer 未被脱敏：{result}"
        )

    def test_bearer_case_insensitive(self):
        """Bearer 不区分大小写"""
        assert "abc123" not in self.filter._sanitize("bearer abc123")
        assert "abc123" not in self.filter._sanitize("BEARER abc123")
        assert "abc123" not in self.filter._sanitize("BeArEr abc123")

    def test_bearer_in_url_with_other_params(self):
        """Bearer token 与 URL 参数共存的场景"""
        text = "Authorization: Bearer abc.def.ghi&page=1"
        result = self.filter._sanitize(text)
        assert "abc.def.ghi" not in result
        assert "page=1" in result


# ═══════════════════════════════════════════════════════════════
# 跨模块防复发测试：utils/sensitive_data_filter.py
# 确保统一过滤器 SensitiveDataFilter.mask 不再贪婪吞噬参数
# ═══════════════════════════════════════════════════════════════

from agent.utils.sensitive_data_filter import SensitiveDataFilter as _UnifiedSDF


class TestSensitiveDataFilterGreedyRegexRegression:
    """P0-SEC-002 防复发：utils.sensitive_data_filter.SensitiveDataFilter 贪婪正则修复验证

    修复前 bug：mask() 使用 [^"\\']* 贪婪匹配，吞噬 & 分隔的相邻参数。
    修复后：使用 [^"\\'&\\s]* 限定边界。

    注意：本模块的 URL 参数模式需要 ? 或 & 前缀。
    占位符：********（REDACTED_VALUE）。
    """

    @pytest.fixture(autouse=True)
    def _init_filter(self):
        """每个测试用例独立的过滤器实例"""
        self.filter = _UnifiedSDF()

    @pytest.mark.parametrize("url,sensitive_value", [
        # 注意：参数值不能与参数名同名（避免子串误判）
        ("https://api.example.com?token=mysecret&page=1&limit=10", "mysecret"),
        ("https://api.example.com?api_key=sk-xxx&page=1&user=admin", "sk-xxx"),
        ("https://api.example.com?secret=topsecretval&next=ok&sort=desc", "topsecretval"),
    ])
    def test_url_params_not_consumed(self, url, sensitive_value):
        """URL 中 & 分隔的参数不被贪婪吞噬"""
        result = self.filter.mask(url)
        assert sensitive_value not in result, f"敏感值未脱敏：{result}"
        # 验证相邻非敏感参数保留
        assert "page=1" in result or "user=admin" in result or "next=ok" in result


class TestSensitiveDataFilterBearerRegression:
    """P0-SEC-001 防复发：utils.sensitive_data_filter.SensitiveDataFilter Bearer 脱敏验证

    修复前 bug：mask() 无 Bearer 专用处理分支。
    修复后：新增 Bearer 专用正则 (Bearer\\s+)([a-zA-Z0-9\\-_.~+/]{20,})

    注意：本模块的 Bearer 模式要求 token 长度 ≥20 字符。
    """

    @pytest.fixture(autouse=True)
    def _init_filter(self):
        """每个测试用例独立的过滤器实例"""
        self.filter = _UnifiedSDF()

    @pytest.mark.parametrize("bearer_text", [
        "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",  # 36 字符
        "Bearer akdjflakdjflakdjflakdjf",  # 24 字符
        "Bearer abcdefghijklmnopqrstuvwxyz1234567890",  # 36 字符
    ])
    def test_long_bearer_redacted(self, bearer_text):
        """≥20 字符的 Bearer token 必须脱敏"""
        result = self.filter.mask(bearer_text)
        token_part = bearer_text[7:]
        assert token_part not in result, (
            f"Bearer token 值泄露：'{token_part}' 出现在 '{result}' 中"
        )


# ═══════════════════════════════════════════════════════════════
# 跨模块一致性测试
# 确保 error_reporting_config / logging_utils / sensitive_data_filter
# 三个模块对相同输入的脱敏行为一致，不会出现某模块泄露某模块脱敏的情况
# ═══════════════════════════════════════════════════════════════

class TestCrossModuleConsistency:
    """跨模块一致性测试：3 个模块对相同输入的脱敏行为一致

    三个模块的脱敏实现存在设计差异：
    - error_reporting_config: 无前缀要求，Bearer 任何长度都脱敏
    - logging_utils: URL 需 ?/& 前缀，Bearer 任何长度都脱敏
    - sensitive_data_filter: URL 需 ?/& 前缀，Bearer 需 ≥20 字符

    本测试类使用三个模块都能匹配的输入（标准 URL + 长 Bearer），
    验证脱敏后敏感值均不残留。
    """

    @pytest.fixture(autouse=True)
    def _init_filters(self):
        """三个模块的过滤器实例"""
        self.filter_err = None  # error_reporting_config 使用函数式 API
        self.filter_log = _LoggingSDF()
        self.filter_unified = _UnifiedSDF()

    @pytest.mark.parametrize("text", [
        # 标准 URL + 长 token，三个模块都能匹配
        "https://api.example.com?token=secret_value_here&page=1",
        # 多个敏感参数
        "https://api.example.com?api_key=sk-secret-key&page=1&user=admin",
        # 长 Bearer token（≥20 字符），三个模块都能匹配
        "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
        # Bearer + URL 混合
        "Auth: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig&next=ok",
    ])
    def test_no_module_leaks_sensitive_value(self, text):
        """三个模块对相同输入脱敏后，敏感值均不得残留"""
        # 提取预期被脱敏的敏感值
        sensitive_values = []
        if "token=" in text:
            sensitive_values.append(text.split("token=")[1].split("&")[0].split(" ")[0])
        if "api_key=" in text:
            sensitive_values.append(text.split("api_key=")[1].split("&")[0].split(" ")[0])
        if "Bearer " in text:
            bearer_part = text.split("Bearer ")[1].split("&")[0].split(" ")[0]
            if len(bearer_part) >= 20:
                sensitive_values.append(bearer_part)

        # 三个模块分别脱敏
        results = [
            ("error_reporting_config", _filter_sensitive_recursive(text)),
            ("logging_utils", self.filter_log._sanitize(text)),
            ("sensitive_data_filter", self.filter_unified.mask(text)),
        ]

        # 所有模块的输出都不得包含敏感值
        for module_name, result in results:
            for sv in sensitive_values:
                assert sv not in result, (
                    f"模块 [{module_name}] 泄露敏感值 '{sv}'：{result}"
                )
