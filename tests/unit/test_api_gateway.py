"""api_gateway 单元测试

覆盖 agent/api_gateway.py 的四个类:
  - ApiKeyManager: 创建/验证/更新/删除/用量/列表
  - AccessLogger: 日志写入/读取/统计聚合
  - QuotaManager: 配额设置/检查/消耗/周期重置
  - ApiGateway: 端点注册/认证/scope 校验/请求处理/中间件/Swagger/统计

设计原则: AAA; mock 文件 IO (Path/open) 与 RateLimiter, 不触碰真实磁盘。
"""
# pylint: disable=redefined-outer-name,missing-function-docstring,protected-access

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from agent.api_gateway import (
    ApiGateway,
    ApiKeyManager,
    AccessLogger,
    QuotaManager,
    AuthMethod,
    get_api_gateway,
)


@pytest.fixture
def api_key_manager():
    with patch.object(ApiKeyManager, "_load_keys", lambda self: None), \
         patch.object(ApiKeyManager, "_save_keys", lambda self: None):
        yield ApiKeyManager()


# ═══════════════════════════════════════════════════════════════
# AuthMethod
# ═══════════════════════════════════════════════════════════════

class TestAuthMethod:
    def test_values(self):
        assert AuthMethod.API_KEY.value == "api_key"
        assert AuthMethod.OAUTH.value == "oauth"
        assert AuthMethod.BEARER.value == "bearer"
        assert AuthMethod.NONE.value == "none"

    def test_string_enum(self):
        assert str(AuthMethod.BEARER) == "AuthMethod.BEARER"


# ═══════════════════════════════════════════════════════════════
# ApiKeyManager
# ═══════════════════════════════════════════════════════════════

class TestApiKeyManager:
    def test_create_key_defaults(self, api_key_manager):
        info = api_key_manager.create_key("user-1", "测试密钥")
        assert info["user_id"] == "user-1"
        assert info["description"] == "测试密钥"
        assert info["scopes"] == ["read", "write"]
        assert info["enabled"] is True
        assert info["usage_count"] == 0
        assert info["quota_remaining"] == 10000
        assert len(info["key"]) == 64  # secrets.token_hex(32)

    def test_create_key_custom_scopes(self, api_key_manager):
        info = api_key_manager.create_key("user-1", scopes=["admin"])
        assert info["scopes"] == ["admin"]
        assert info["key"] in api_key_manager._api_keys

    def test_validate_key_enabled(self, api_key_manager):
        info = api_key_manager.create_key("user-1")
        assert api_key_manager.validate_key(info["key"]) is info

    def test_validate_key_disabled(self, api_key_manager):
        info = api_key_manager.create_key("user-1")
        info["enabled"] = False
        assert api_key_manager.validate_key(info["key"]) is None

    def test_validate_key_unknown(self, api_key_manager):
        assert api_key_manager.validate_key("nonexistent") is None

    def test_update_key(self, api_key_manager):
        info = api_key_manager.create_key("user-1")
        assert api_key_manager.update_key(info["key"], {"description": "新描述"}) is True
        assert api_key_manager._api_keys[info["key"]]["description"] == "新描述"

    def test_update_key_unknown(self, api_key_manager):
        assert api_key_manager.update_key("nope", {}) is False

    def test_delete_key(self, api_key_manager):
        info = api_key_manager.create_key("user-1")
        assert api_key_manager.delete_key(info["key"]) is True
        assert info["key"] not in api_key_manager._api_keys

    def test_delete_key_unknown(self, api_key_manager):
        assert api_key_manager.delete_key("nope") is False

    def test_increment_usage(self, api_key_manager):
        info = api_key_manager.create_key("user-1")
        api_key_manager.increment_usage(info["key"])
        assert api_key_manager._api_keys[info["key"]]["usage_count"] == 1
        assert api_key_manager._api_keys[info["key"]]["quota_remaining"] == 9999
        assert api_key_manager._api_keys[info["key"]]["last_used_at"] != ""

    def test_increment_usage_quota_zero(self, api_key_manager):
        info = api_key_manager.create_key("user-1")
        api_key_manager._api_keys[info["key"]]["quota_remaining"] = 0
        api_key_manager.increment_usage(info["key"])
        # quota_remaining 为 0 时不再递减, 但 usage_count 仍增加
        assert api_key_manager._api_keys[info["key"]]["usage_count"] == 1
        assert api_key_manager._api_keys[info["key"]]["quota_remaining"] == 0

    def test_increment_usage_unknown_key(self, api_key_manager):
        api_key_manager.increment_usage("nope")  # 不应抛异常

    def test_get_key_info(self, api_key_manager):
        info = api_key_manager.create_key("user-1")
        assert api_key_manager.get_key_info(info["key"]) is info
        assert api_key_manager.get_key_info("nope") is None

    def test_list_keys_all(self, api_key_manager):
        api_key_manager.create_key("user-1")
        api_key_manager.create_key("user-2")
        assert len(api_key_manager.list_keys()) == 2

    def test_list_keys_by_user(self, api_key_manager):
        api_key_manager.create_key("user-1")
        api_key_manager.create_key("user-2")
        keys = api_key_manager.list_keys(user_id="user-1")
        assert len(keys) == 1
        assert keys[0]["user_id"] == "user-1"


# ═══════════════════════════════════════════════════════════════
# AccessLogger
# ═══════════════════════════════════════════════════════════════

class TestAccessLogger:
    @pytest.fixture
    def logger(self):
        with patch("agent.api_gateway.get_trace_id", return_value="trace-1"):
            l = AccessLogger()
            l._log_file = None  # 阻止真实写盘
            yield l

    def test_log_access(self, logger):
        logger.log_access({"endpoint": "/api/health", "user_id": "u1", "status_code": 200})
        assert len(logger._logs) == 1
        entry = logger._logs[0]
        assert entry["trace_id"] == "trace-1"
        assert entry["endpoint"] == "/api/health"
        assert entry["timestamp"] != ""

    def test_log_access_cap_at_10000(self, logger):
        for i in range(10005):
            logger._logs.append({"timestamp": datetime.now().isoformat(), "i": i})
        logger.log_access({"endpoint": "/x"})
        assert len(logger._logs) <= 10000

    def test_write_log_error(self, logger):
        # _log_file 无法打开时不应抛异常
        logger._log_file = None
        with patch("builtins.open", side_effect=OSError("disk full")):
            logger._write_log({"k": "v"})  # 捕获 OSError 记录 warning

    def test_get_logs_all(self, logger):
        for i in range(5):
            logger.log_access({"endpoint": f"/e{i}", "user_id": "u1"})
        logs = logger.get_logs(limit=3)
        assert len(logs) == 3

    def test_get_logs_by_user(self, logger):
        logger.log_access({"endpoint": "/a", "user_id": "u1"})
        logger.log_access({"endpoint": "/b", "user_id": "u2"})
        logs = logger.get_logs(user_id="u1")
        assert len(logs) == 1
        assert logs[0]["endpoint"] == "/a"

    def test_get_stats_default_24h(self, logger):
        logger.log_access({"endpoint": "/a", "user_id": "u1", "status_code": 200})
        stats = logger.get_stats()
        assert stats["total_requests"] == 1
        assert stats["period"] == "24h"
        assert stats["status_codes"] == {"200": 1}
        assert stats["endpoints"] == {"/a": 1}
        assert stats["users"] == {"u1": 1}

    def test_get_stats_7d(self, logger):
        logger.log_access({"endpoint": "/a", "user_id": "u1"})
        assert logger.get_stats(period="7d")["total_requests"] == 1

    def test_get_stats_unknown_period_defaults_1h(self, logger):
        logger.log_access({"endpoint": "/a", "user_id": "u1"})
        stats = logger.get_stats(period="weird")
        assert stats["period"] == "weird"  # start_time 按 1h 计算
        assert stats["total_requests"] == 1

    def test_count_by_status(self, logger):
        logger._logs = [
            {"status_code": 200}, {"status_code": 200}, {"status_code": 500},
        ]
        assert logger._count_by_status(logger._logs) == {"200": 2, "500": 1}

    def test_count_by_endpoint_top10_sorted(self, logger):
        logger._logs = [{"endpoint": "/a"}] * 3 + [{"endpoint": "/b"}] * 5
        result = logger._count_by_endpoint(logger._logs)
        assert list(result.items()) == [("/b", 5), ("/a", 3)]

    def test_count_by_user(self, logger):
        logger._logs = [{"user_id": "u1"}] * 2 + [{}]
        result = logger._count_by_user(logger._logs)
        assert result == {"u1": 2, "anonymous": 1}


# ═══════════════════════════════════════════════════════════════
# QuotaManager
# ═══════════════════════════════════════════════════════════════

class TestQuotaManager:
    @pytest.fixture
    def qm(self):
        return QuotaManager()

    def test_check_quota_no_quota(self, qm):
        assert qm.check_quota("u1", "api_calls") is True

    def test_set_and_check(self, qm):
        qm.set_quota("u1", "api_calls", 10, period="day")
        assert qm.check_quota("u1", "api_calls", 5) is True
        qm.consume_quota("u1", "api_calls", 5)  # used=5
        assert qm.check_quota("u1", "api_calls", 6) is False  # 5+6 > 10

    def test_consume_quota(self, qm):
        qm.set_quota("u1", "api_calls", 10)
        assert qm.consume_quota("u1", "api_calls", 5) is True
        assert qm._quotas["u1:api_calls"]["used"] == 5
        assert qm.consume_quota("u1", "api_calls", 6) is False
        assert qm._quotas["u1:api_calls"]["used"] == 5

    def test_consume_quota_no_quota(self, qm):
        assert qm.consume_quota("u1", "x") is True

    def test_reset_if_needed_day(self, qm):
        qm.set_quota("u1", "api_calls", 10)
        q = qm._quotas["u1:api_calls"]
        q["used"] = 5
        q["last_reset"] = (datetime.now() - timedelta(days=1)).isoformat()
        qm._reset_if_needed(q)
        assert q["used"] == 0

    def test_reset_if_needed_hour(self, qm):
        qm.set_quota("u1", "api_calls", 10, period="hour")
        q = qm._quotas["u1:api_calls"]
        q["used"] = 3
        q["last_reset"] = (datetime.now() - timedelta(hours=2)).isoformat()
        qm._reset_if_needed(q)
        assert q["used"] == 0

    def test_reset_if_needed_month(self, qm):
        qm.set_quota("u1", "api_calls", 10, period="month")
        q = qm._quotas["u1:api_calls"]
        q["used"] = 3
        q["last_reset"] = (datetime.now() - timedelta(days=40)).isoformat()
        qm._reset_if_needed(q)
        assert q["used"] == 0

    def test_get_quota_status_no_quota(self, qm):
        status = qm.get_quota_status("u1", "api_calls")
        assert status["limit"] == -1
        assert status["used"] == 0

    def test_get_quota_status_with_quota(self, qm):
        qm.set_quota("u1", "api_calls", 10, period="day")
        qm.consume_quota("u1", "api_calls", 4)
        status = qm.get_quota_status("u1", "api_calls")
        assert status["limit"] == 10
        assert status["used"] == 4
        assert status["remaining"] == 6
        assert status["period"] == "day"


# ═══════════════════════════════════════════════════════════════
# ApiGateway
# ═══════════════════════════════════════════════════════════════

def _make_request(**attrs):
    req = MagicMock()
    req.path = attrs.pop("path", "/api/test")
    req.method = attrs.pop("method", "GET")
    headers = attrs.pop("headers", {})
    req.headers = MagicMock()
    req.headers.get.side_effect = lambda k, d="": headers.get(k, d)
    for k, v in attrs.items():
        setattr(req, k, v)
    return req


@pytest.fixture
def gateway():
    with patch("agent.api_gateway.get_rate_limiter", return_value=MagicMock(check=MagicMock(return_value=True))):
        g = ApiGateway()
        g._api_key_manager._load_keys = lambda: None
        g._api_key_manager._save_keys = lambda: None
        g._access_logger._log_file = None
        yield g


class TestApiGateway:
    def test_register_endpoint(self, gateway):
        def handler(req):
            return {"ok": True}
        gateway.register_endpoint("/api/x", "get", handler, auth_required=False)
        key = "GET:/api/x"
        assert key in gateway._endpoints
        assert gateway._endpoints[key]["method"] == "GET"
        assert gateway._endpoints[key]["auth_required"] is False
        assert gateway._endpoints[key]["scopes"] == []

    def test_register_endpoint_custom_scopes(self, gateway):
        gateway.register_endpoint("/api/x", "post", lambda r: {}, scopes=["admin"], summary="s", description="d")
        ep = gateway._endpoints["POST:/api/x"]
        assert ep["scopes"] == ["admin"]
        assert ep["summary"] == "s"
        assert ep["description"] == "d"

    def test_add_middleware(self, gateway):
        mw = MagicMock()
        gateway.add_middleware(mw)
        assert mw in gateway._middleware

    def test_authenticate_bearer(self, gateway):
        key_info = gateway._api_key_manager.create_key("u1")
        req = _make_request(headers={"Authorization": f"Bearer {key_info['key']}"})
        assert gateway.authenticate(req) is key_info

    def test_authenticate_api_key_header(self, gateway):
        key_info = gateway._api_key_manager.create_key("u1")
        req = _make_request(headers={"Authorization": f"Api-Key {key_info['key']}"})
        assert gateway.authenticate(req) is key_info

    def test_authenticate_x_api_key(self, gateway):
        key_info = gateway._api_key_manager.create_key("u1")
        req = _make_request(headers={"X-API-Key": key_info["key"]})
        assert gateway.authenticate(req) is key_info

    def test_authenticate_none(self, gateway):
        assert gateway.authenticate(_make_request(headers={})) is None

    def test_check_scopes_empty(self, gateway):
        assert gateway.check_scopes({"scopes": []}, []) is True

    def test_check_scopes_pass(self, gateway):
        assert gateway.check_scopes({"scopes": ["read", "write"]}, ["read"]) is True

    def test_check_scopes_fail(self, gateway):
        assert gateway.check_scopes({"scopes": ["read"]}, ["admin"]) is False

    def test_handle_request_404(self, gateway):
        result = gateway.handle_request(_make_request(path="/nope"))
        assert result["status_code"] == 404

    def test_handle_request_unauthorized(self, gateway):
        gateway.register_endpoint("/api/secure", "get", lambda r: {"ok": True})
        result = gateway.handle_request(_make_request(path="/api/secure", headers={}))
        assert result["status_code"] == 401

    def test_handle_request_forbidden(self, gateway):
        key_info = gateway._api_key_manager.create_key("u1", scopes=["read"])
        gateway.register_endpoint("/api/secure", "get", lambda r: {"ok": True}, scopes=["admin"])
        req = _make_request(path="/api/secure", headers={"X-API-Key": key_info["key"]})
        result = gateway.handle_request(req)
        assert result["status_code"] == 403

    def test_handle_request_rate_limited(self, gateway):
        key_info = gateway._api_key_manager.create_key("u1")
        gateway.register_endpoint("/api/secure", "get", lambda r: {"ok": True})
        gateway._rate_limiter.check.return_value = False
        req = _make_request(path="/api/secure", headers={"X-API-Key": key_info["key"]})
        result = gateway.handle_request(req)
        assert result["status_code"] == 429

    def test_handle_request_quota_exceeded(self, gateway):
        key_info = gateway._api_key_manager.create_key("u1")
        gateway.register_endpoint("/api/secure", "get", lambda r: {"ok": True})
        with patch.object(gateway._quota_manager, "check_quota", return_value=False):
            req = _make_request(path="/api/secure", headers={"X-API-Key": key_info["key"]})
            result = gateway.handle_request(req)
        assert result["status_code"] == 429

    def test_handle_request_success_anonymous(self, gateway):
        gateway.register_endpoint("/api/open", "get", lambda r: {"ok": True}, auth_required=False)
        result = gateway.handle_request(_make_request(path="/api/open"))
        assert result["ok"] is True
        assert result.get("status_code", 200) == 200

    def test_handle_request_success_auth(self, gateway):
        key_info = gateway._api_key_manager.create_key("u1")
        gateway.register_endpoint("/api/secure", "get", lambda r: {"ok": True})
        req = _make_request(path="/api/secure", headers={"X-API-Key": key_info["key"]})
        result = gateway.handle_request(req)
        assert result["ok"] is True
        # 用量递增
        assert gateway._api_key_manager._api_keys[key_info["key"]]["usage_count"] == 1

    def test_handle_request_middleware_called(self, gateway):
        mw = MagicMock()
        gateway.add_middleware(mw)
        gateway.register_endpoint("/api/open", "get", lambda r: {"ok": True}, auth_required=False)
        gateway.handle_request(_make_request(path="/api/open"))
        mw.assert_called_once()

    def test_handle_request_handler_exception(self, gateway):
        def boom(req):
            raise ValueError("boom")
        gateway.register_endpoint("/api/open", "get", boom, auth_required=False)
        result = gateway.handle_request(_make_request(path="/api/open"))
        assert result["status_code"] == 500
        assert "boom" in result["error"]

    def test_generate_swagger_doc_empty(self, gateway):
        doc = gateway.generate_swagger_doc()
        assert doc["openapi"] == "3.0.0"
        assert doc["paths"] == {}
        assert "ApiKeyAuth" in doc["components"]["securitySchemes"]

    def test_generate_swagger_doc_with_endpoints(self, gateway):
        gateway.register_endpoint("/api/a", "get", lambda r: {}, auth_required=True, summary="SA")
        gateway.register_endpoint("/api/a", "post", lambda r: {}, auth_required=False)
        doc = gateway.generate_swagger_doc()
        assert doc["paths"]["/api/a"]["get"]["summary"] == "SA"
        assert doc["paths"]["/api/a"]["get"]["security"] == [{"ApiKeyAuth": []}]
        assert doc["paths"]["/api/a"]["post"]["security"] == []

    def test_get_stats(self, gateway):
        gateway.register_endpoint("/api/a", "get", lambda r: {})
        gateway._api_key_manager.create_key("u1")
        gateway._api_key_manager._api_keys = {}  # 隔离真实文件中的历史 key
        gateway._api_key_manager.create_key("u1")
        gateway._access_logger.log_access({"endpoint": "/api/a"})
        stats = gateway.get_stats()
        assert stats["endpoints"] == 1
        assert stats["api_keys"] == 1
        assert stats["access_logs"] == 1
        assert "rate_limiter" in stats

    def test_get_api_gateway_singleton(self):
        with patch("agent.api_gateway._SINGLETON_AVAILABLE", False), \
             patch("agent.api_gateway._create_api_gateway", return_value=MagicMock()) as factory:
            g1 = get_api_gateway()
            g2 = get_api_gateway()
            assert g1 is g2
            factory.assert_called_once()
