"""/api/audit/logs 跨租户数据隔离修复单测（T8.4 剧本 B.3 缓解落地）

覆盖：
- AuditLogger.log 写侧 tenant_id 字段
- AuditLogger.filter_by_key：绑定租户过滤 / 未绑定空集+warning / 内部直调全量
- ApiGateway.handle_request 认证后注入 request._gateway_key_info（视图读取入口）
"""
import tempfile
from types import SimpleNamespace

from agent.api_gateway import ApiGateway
from agent.audit.logger import AuditLogger


def _make_logger():
    return AuditLogger(log_dir=tempfile.mkdtemp())


def _seed(logger, tenant_id, action="action_x"):
    logger.log(action=action, metadata={"k": tenant_id}, tenant_id=tenant_id)


class TestAuditLoggerTenantField:
    def test_log_writes_tenant_id(self):
        logger = _make_logger()
        logger.log(action="a", tenant_id="org_a")
        records = logger.query()
        assert records[0]["tenant_id"] == "org_a"

    def test_log_default_tenant_empty(self):
        logger = _make_logger()
        logger.log(action="a")
        assert logger.query()[0]["tenant_id"] == ""

    def test_auto_inject_from_request_context(self):
        """业务侧调用点无 tenant_id 时，从请求上下文自动注入（网关注入 _gateway_key_info）"""
        from flask import Flask, request
        logger = _make_logger()
        app = Flask(__name__)
        with app.test_request_context("/api/audit/logs"):
            request._gateway_key_info = {"tenant_id": "org_auto", "user_id": "u1"}
            logger.log(action="auto_inject")
        assert logger.query()[0]["tenant_id"] == "org_auto"

    def test_explicit_tenant_beats_inference(self):
        """显式 tenant_id 优先于上下文推断"""
        from flask import Flask, request
        logger = _make_logger()
        app = Flask(__name__)
        with app.test_request_context("/api/audit/logs"):
            request._gateway_key_info = {"tenant_id": "org_auto", "user_id": "u1"}
            logger.log(action="explicit", tenant_id="org_explicit")
        assert logger.query()[0]["tenant_id"] == "org_explicit"

    def test_auto_inject_without_request_context_empty(self):
        """无请求上下文（CLI/后台任务）→ 自动推断降级为空"""
        logger = _make_logger()
        logger.log(action="cli_task")
        assert logger.query()[0]["tenant_id"] == ""


class TestFilterByKey:
    def _logs(self):
        logger = _make_logger()
        _seed(logger, "org_a")
        _seed(logger, "org_b")
        _seed(logger, "")
        return logger.query()

    def test_bound_tenant_only_sees_own(self):
        logs = self._logs()
        got, warning = AuditLogger.filter_by_key(logs, {"tenant_id": "org_a"})
        assert warning is None
        assert len(got) == 1
        assert got[0]["tenant_id"] == "org_a"

    def test_unbound_key_gets_empty_and_warning(self):
        logs = self._logs()
        got, warning = AuditLogger.filter_by_key(logs, {"tenant_id": ""})
        assert got == []
        assert "未绑定租户" in warning

    def test_internal_channel_sees_all(self):
        logs = self._logs()
        got, warning = AuditLogger.filter_by_key(logs, None)
        assert warning is None
        assert len(got) == 3


class TestGatewayInjectsKeyInfo:
    """handle_request 认证后注入 request._gateway_key_info（隔离修复入口）"""

    def _gateway(self):
        gw = ApiGateway()
        gw.register_endpoint(path="/api/audit/logs", method="GET",
                             handler=lambda req: {"ok": True},
                             auth_required=True, scopes=["read"])
        return gw

    def _key(self, gw, tenant_id):
        key = "k" * 64
        gw._api_key_manager._api_keys[key] = {
            "key": key, "user_id": "u1", "scopes": ["read"],
            "tenant_id": tenant_id, "role": "member", "compat_until": "",
            "enabled": True, "usage_count": 0, "quota_remaining": 10000,
            "total_quota": 10000, "last_used_at": "", "created_at": "2026-08-16T00:00:00",
        }
        return key

    def test_bound_key_injects_tenant_id(self):
        from unittest import mock
        gw = self._gateway()
        key = self._key(gw, "org_a")
        with mock.patch("agent.multi_tenant.tenant_manager.has_permission",
                        return_value=True):
            req = SimpleNamespace(path="/api/audit/logs", method="GET",
                                  headers={"X-API-Key": key})
            gw.handle_request(req)
        assert getattr(req, "_gateway_key_info", {}).get("tenant_id") == "org_a"

    def test_legacy_key_injects_empty_tenant(self):
        gw = self._gateway()
        key = self._key(gw, "")
        req = SimpleNamespace(path="/api/audit/logs", method="GET",
                              headers={"X-API-Key": key})
        gw.handle_request(req)
        assert getattr(req, "_gateway_key_info", {}).get("tenant_id") == ""
