"""T8.3 租户级配额与限流隔离单元测试

覆盖：
- RateLimiter：租户令牌桶隔离（租户 A 打满不影响租户 B、不影响未绑定租户）
- QuotaManager：租户配额 set/check/consume，与用户配额独立计数
- ApiGateway.handle_request：租户配额耗尽 → 429 Tenant quota exceeded；
  成功调用消耗租户配额；未配置配额默认放行
tenant_manager / rate limiter 均使用独立实例或 mock，不触达真实数据。
"""
from types import SimpleNamespace
from unittest import mock

import pytest

from agent.api_gateway import ApiGateway, QuotaManager
from agent.rate_limiter import RateLimiter


def _make_gateway(path="/test/tenant-quota", scopes=("write",)):
    gw = ApiGateway()
    gw.register_endpoint(
        path=path, method="GET",
        handler=lambda req: {"ok": True},
        auth_required=True, scopes=list(scopes),
    )
    return gw


def _key_info(tenant_id, key="k" * 64):
    return {
        "key": key, "user_id": "u1", "scopes": ["read", "write"],
        "tenant_id": tenant_id, "role": "member", "compat_until": "",
        "enabled": True, "usage_count": 0, "quota_remaining": 10000,
        "total_quota": 10000, "last_used_at": "", "created_at": "2026-08-16T00:00:00",
    }


class TestRateLimiterTenantIsolation:
    """租户级令牌桶隔离"""

    def _limiter(self, cap=1, rate=0.0001):
        limiter = RateLimiter()
        # 租户规则设为 1 令牌/极慢补充，便于快速验证隔离性
        limiter.register_rule("tenant", cap, rate)
        return limiter

    def test_tenant_bucket_exhaustion_blocks_same_tenant_only(self):
        limiter = self._limiter()
        # 租户 A 第一发通过，第二发被租户桶拦截
        assert limiter.check(endpoint="/test/quota", user_id="u1", tenant_id="A") is True
        assert limiter.check(endpoint="/test/quota", user_id="u1", tenant_id="A") is False

    def test_other_tenant_has_independent_bucket(self):
        limiter = self._limiter()
        assert limiter.check(endpoint="/test/quota", user_id="u1", tenant_id="A") is True
        # 租户 B 不受 A 的桶影响
        assert limiter.check(endpoint="/test/quota", user_id="u1", tenant_id="B") is True

    def test_unbound_request_not_blocked_by_tenant_rule(self):
        limiter = self._limiter()
        assert limiter.check(endpoint="/test/quota", user_id="u1", tenant_id="A") is True
        # 未绑定租户（tenant_id=None）不走租户桶，仍可放行
        assert limiter.check(endpoint="/test/quota", user_id="u1") is True

    def test_failed_tenant_check_rolls_back_global_and_endpoint_tokens(self):
        """失败时回退已消费的全局/接口令牌：全局与接口桶不因租户拒绝而泄漏"""
        limiter = self._limiter(cap=1, rate=0.0)
        assert limiter.check(endpoint="/test/quota", user_id="u1", tenant_id="A") is True
        # 第二次被租户桶拒绝 → 全局/接口令牌回退
        assert limiter.check(endpoint="/test/quota", user_id="u1", tenant_id="A") is False
        # 全局桶仍有 99 令牌（第一次消耗 1，第二次回退）→ 可继续服务其他调用
        assert limiter._global_bucket.tokens >= 99


class TestQuotaManagerTenant:
    """租户配额（与用户配额隔离，key 前缀 tenant:）"""

    def test_set_check_consume_tenant_quota(self):
        qm = QuotaManager()
        qm.set_tenant_quota("org_x", "api_calls", limit=2)
        assert qm.check_tenant_quota("org_x", "api_calls") is True
        assert qm.consume_tenant_quota("org_x", "api_calls") is True
        assert qm.consume_tenant_quota("org_x", "api_calls") is True
        assert qm.check_tenant_quota("org_x", "api_calls") is False
        assert qm.consume_tenant_quota("org_x", "api_calls") is False

    def test_tenant_quota_independent_of_user_quota(self):
        qm = QuotaManager()
        qm.set_quota("u1", "api_calls", limit=1)
        qm.set_tenant_quota("org_x", "api_calls", limit=5)
        assert qm.consume_quota("u1", "api_calls") is True
        assert qm.consume_quota("u1", "api_calls") is False   # 用户配额耗尽
        assert qm.check_tenant_quota("org_x", "api_calls") is True  # 租户配额不受影响

    def test_no_tenant_quota_defaults_pass(self):
        qm = QuotaManager()
        assert qm.check_tenant_quota("org_x", "api_calls") is True
        assert qm.consume_tenant_quota("org_x", "api_calls") is True

    def test_different_tenants_have_independent_quotas(self):
        qm = QuotaManager()
        qm.set_tenant_quota("org_x", "api_calls", limit=1)
        qm.set_tenant_quota("org_y", "api_calls", limit=1)
        assert qm.consume_tenant_quota("org_x", "api_calls") is True
        assert qm.check_tenant_quota("org_x", "api_calls") is False
        assert qm.check_tenant_quota("org_y", "api_calls") is True


class TestHandleRequestTenantQuota:
    """handle_request 集成：租户配额检查/消耗"""

    def _gateway(self, limit):
        gw = _make_gateway()
        gw._quota_manager.set_tenant_quota("org_x", "api_calls", limit=limit)
        return gw

    def _authorized_request(self, gw, tenant_id="org_x"):
        key = "k" * 64
        gw._api_key_manager._api_keys[key] = _key_info(tenant_id=tenant_id)
        return SimpleNamespace(path="/test/tenant-quota", method="GET",
                               headers={"X-API-Key": key})

    def test_tenant_quota_exceeded_returns_429(self):
        gw = self._gateway(limit=1)
        with mock.patch("agent.multi_tenant.tenant_manager.has_permission",
                        return_value=True):
            req = self._authorized_request(gw)
            assert gw.handle_request(req).get("status_code", 200) == 200
            resp = gw.handle_request(req)
        assert resp["status_code"] == 429
        assert "Tenant quota exceeded" in resp["error"]

    def test_tenant_quota_consumed_on_success(self):
        gw = self._gateway(limit=3)
        with mock.patch("agent.multi_tenant.tenant_manager.has_permission",
                        return_value=True):
            req = self._authorized_request(gw)
            gw.handle_request(req)
        quota = gw._quota_manager._quotas["tenant:org_x:api_calls"]
        assert quota["used"] == 1

    def test_without_tenant_binding_skips_tenant_quota(self):
        """未绑定租户的旧 Key：租户配额检查放行，配额不消耗"""
        gw = self._gateway(limit=1)
        gw._quota_manager.set_quota("u1", "api_calls", limit=100)
        key = "k" * 64
        gw._api_key_manager._api_keys[key] = _key_info(tenant_id="")
        req = SimpleNamespace(path="/test/tenant-quota", method="GET",
                              headers={"X-API-Key": key})
        resp = gw.handle_request(req)
        assert resp.get("status_code", 200) == 200
        # 未绑定租户：租户配额未被消耗（used 保持 0）
        assert gw._quota_manager._quotas["tenant:org_x:api_calls"]["used"] == 0
