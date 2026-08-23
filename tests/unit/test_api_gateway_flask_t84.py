"""T8.4 内部只读端点逐批开放（网关认证接管）单元测试

覆盖：
- should_gateway_handle：开放内部端点拦截 / 占位端点放行 / /api/open 全拦截 / 未注册放行
- _wrap_internal_view：Flask Response → dict 契约转换
- register_internal_endpoints：注册 auth_required=True + scope=read
- handle_request 集成：开放端点无 Key 401、scope 不足 403、带 Key 200
"""
from types import SimpleNamespace
from unittest import mock

import pytest

from agent.api_gateway import ApiGateway
from agent.api_gateway_flask import (
    should_gateway_handle,
    _wrap_internal_view,
    register_internal_endpoints,
    _INTERNAL_OPEN_BATCH1,
    _INTERNAL_OPEN_BATCH2,
)


class _Rule:
    def __init__(self, path, endpoint, methods=("GET",)):
        self.rule = path
        self.endpoint = endpoint
        self.methods = set(methods) | {"HEAD", "OPTIONS"}


def _flask_app(view_functions, rules):
    return SimpleNamespace(
        url_map=SimpleNamespace(iter_rules=lambda: rules),
        view_functions=view_functions,
    )


class TestShouldGatewayHandle:
    """网关拦截判定（before_request 白名单核心）"""

    def _gw(self, **endpoints):
        gw = ApiGateway()
        for key, ep in endpoints.items():
            gw._endpoints[key] = ep
        return gw

    def test_gateway_endpoint_intercepted_even_anonymous(self):
        # /api/open/echo auth_required=False 仍需拦截（保持探活链路）
        gw = self._gw(**{"GET:/api/open/echo": {"auth_required": False}})
        assert should_gateway_handle("/api/open/echo", "GET", gw) is True

    def test_open_internal_endpoint_intercepted(self):
        gw = self._gw(**{"GET:/api/news": {"auth_required": True}})
        assert should_gateway_handle("/api/news", "GET", gw) is True

    def test_placeholder_internal_endpoint_passthrough(self):
        # 文档占位端点（_scan_internal_routes 注册）不可被网关劫持
        gw = self._gw(**{"GET:/api/search": {"auth_required": False}})
        assert should_gateway_handle("/api/search", "GET", gw) is False

    def test_unregistered_path_passthrough(self):
        gw = self._gw()
        assert should_gateway_handle("/api/open/keys", "POST", gw) is False
        assert should_gateway_handle("/api/unknown", "GET", gw) is False

    def test_non_api_path_passthrough(self):
        gw = self._gw()
        assert should_gateway_handle("/index.html", "GET", gw) is False


class TestWrapInternalView:
    """Flask 视图函数 → 网关 handler dict 契约"""

    def _json_response(self, payload, status=200):
        return SimpleNamespace(get_json=lambda: payload, status_code=status)

    def test_json_response_merged_into_dict(self):
        handler = _wrap_internal_view(lambda: self._json_response(
            {"ok": True, "count": 3}))
        result = handler(SimpleNamespace(path="/api/news"))
        assert result["ok"] is True
        assert result["count"] == 3
        assert result["status_code"] == 200

    def test_error_response_status_preserved(self):
        handler = _wrap_internal_view(lambda: self._json_response(
            {"ok": False, "error": "boom"}, status=500))
        result = handler(SimpleNamespace(path="/x"))
        assert result["status_code"] == 500
        assert result["ok"] is False

    def test_non_json_response_wrapped(self):
        handler = _wrap_internal_view(lambda: "raw")
        result = handler(SimpleNamespace(path="/x"))
        assert result["data"] == "raw"
        assert result["ok"] is True


class TestRegisterInternalEndpoints:
    """T8.4 批量注册：覆盖占位端点，auth_required=True"""

    def test_registers_batch1_with_auth_and_read_scope(self):
        rules = [_Rule(path, f"view_{i}", methods=(method,))
                 for i, (path, method, _) in enumerate(_INTERNAL_OPEN_BATCH1)]
        app = _flask_app(
            {f"view_{i}": lambda req=None: {"ok": True}
             for i in range(len(rules))},
            rules,
        )
        gw = ApiGateway()
        count = register_internal_endpoints(app, gw)
        assert count == len(_INTERNAL_OPEN_BATCH1)
        for path, method, scope in _INTERNAL_OPEN_BATCH1:
            ep = gw._endpoints[f"{method}:{path}"]
            assert ep["auth_required"] is True
            assert ep["scopes"] == [scope]
            assert callable(ep["handler"])

    def test_registers_batch2_includes_audit_logs(self):
        """第二批：含 /api/audit/logs，均 auth_required=True + scope=read"""
        rules = [_Rule(path, f"view_{i}", methods=(method,))
                 for i, (path, method, _) in enumerate(_INTERNAL_OPEN_BATCH2)]
        app = _flask_app(
            {f"view_{i}": lambda req=None: {"ok": True}
             for i in range(len(rules))},
            rules,
        )
        gw = ApiGateway()
        count = register_internal_endpoints(app, gw, _INTERNAL_OPEN_BATCH2)
        assert count == len(_INTERNAL_OPEN_BATCH2)
        paths = {path for path, _, _ in _INTERNAL_OPEN_BATCH2}
        assert "/api/audit/logs" in paths
        for path, method, scope in _INTERNAL_OPEN_BATCH2:
            ep = gw._endpoints[f"{method}:{path}"]
            assert ep["auth_required"] is True
            assert ep["scopes"] == [scope]

    def test_missing_view_skipped(self):
        app = _flask_app({}, [])
        gw = ApiGateway()
        count = register_internal_endpoints(app, gw)
        assert count == 0


class TestHandleRequestOpenInternalEndpoint:
    """集成：开放内部端点走网关认证链路"""

    def _gateway_with_open_news(self):
        gw = ApiGateway()
        handler = _wrap_internal_view(lambda: SimpleNamespace(
            get_json=lambda: {"ok": True, "title": "news"}, status_code=200))
        gw.register_endpoint(path="/api/news", method="GET", handler=handler,
                             auth_required=True, scopes=["read"])
        return gw

    def test_no_key_returns_401(self):
        gw = self._gateway_with_open_news()
        req = SimpleNamespace(path="/api/news", method="GET", headers={})
        resp = gw.handle_request(req)
        assert resp["status_code"] == 401

    def test_insufficient_scope_returns_403(self):
        key = "k" * 64
        key_info = {
            "key": key, "user_id": "u1", "scopes": ["read"],
            "tenant_id": "", "role": "", "compat_until": "", "enabled": True,
            "usage_count": 0, "quota_remaining": 10000, "total_quota": 10000,
            "last_used_at": "", "created_at": "2026-08-16T00:00:00",
        }
        # 端点要求 write，Key 只有 read → 403
        gw2 = ApiGateway()
        gw2.register_endpoint(path="/api/news", method="GET",
                              handler=lambda req: {"ok": True},
                              auth_required=True, scopes=["write"])
        gw2._api_key_manager._api_keys[key] = key_info
        req = SimpleNamespace(path="/api/news", method="GET",
                              headers={"X-API-Key": key})
        resp = gw2.handle_request(req)
        assert resp["status_code"] == 403

    def test_valid_key_with_read_scope_returns_200(self):
        gw = self._gateway_with_open_news()
        key = "k" * 64
        gw._api_key_manager._api_keys[key] = {
            "key": key, "user_id": "u1", "scopes": ["read"],
            "tenant_id": "", "role": "", "compat_until": "", "enabled": True,
            "usage_count": 0, "quota_remaining": 10000, "total_quota": 10000,
            "last_used_at": "", "created_at": "2026-08-16T00:00:00",
        }
        req = SimpleNamespace(path="/api/news", method="GET",
                              headers={"X-API-Key": key})
        resp = gw.handle_request(req)
        assert resp.get("status_code", 200) == 200
        assert resp.get("title") == "news"

    def _gateway_with_audit_logs(self):
        gw = ApiGateway()
        handler = _wrap_internal_view(lambda: SimpleNamespace(
            get_json=lambda: {"ok": True, "logs": [], "count": 0}, status_code=200))
        gw.register_endpoint(path="/api/audit/logs", method="GET", handler=handler,
                             auth_required=True, scopes=["read"])
        return gw

    def test_audit_logs_no_key_returns_401(self):
        gw = self._gateway_with_audit_logs()
        req = SimpleNamespace(path="/api/audit/logs", method="GET", headers={})
        resp = gw.handle_request(req)
        assert resp["status_code"] == 401

    def test_audit_logs_with_key_returns_200(self):
        gw = self._gateway_with_audit_logs()
        key = "k" * 64
        gw._api_key_manager._api_keys[key] = {
            "key": key, "user_id": "u1", "scopes": ["read"],
            "tenant_id": "", "role": "", "compat_until": "", "enabled": True,
            "usage_count": 0, "quota_remaining": 10000, "total_quota": 10000,
            "last_used_at": "", "created_at": "2026-08-16T00:00:00",
        }
        req = SimpleNamespace(path="/api/audit/logs", method="GET",
                              headers={"X-API-Key": key})
        resp = gw.handle_request(req)
        assert resp.get("status_code", 200) == 200
        assert resp.get("logs") == []
