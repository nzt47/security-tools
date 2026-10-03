"""回归守卫（安全·2026-10-03）：鉴权豁免清单**不得**覆盖变更型端点。

【为什么单列一个文件】本仓这一类缺陷已发生**两次**：
  - 第一次（记录在 agent/server_auth.py:205-211）：豁免项 "/api/health" 用前缀匹配，
    把 PUT /api/health/weights 与 POST /api/health/score/calculate 一并放开；
  - 第二次（审计 H-5）：/api/replay/upload 因「sendBeacon 无法携带自定义请求头」被整条
    豁免，而它自身又漏了 @require_token ⇒ 一个写端点完全无鉴权。
两次成因相同：看装饰器的人看不到豁免清单，看豁免清单的人不知道哪条是写路由。

本文件只测纯函数（不导入 app_server，避免 50s+ 的重导入与 Prometheus 重复注册）。
app_server.audit_auth_allowlist() 在启动期用同一函数做真实 url_map 自检。
"""
from __future__ import annotations

import pytest

from agent.server_auth import find_allowed_write_endpoints, path_is_allowlisted


def rules(*items):
    """构造 (rule, methods) 迭代器，模拟 app.url_map.iter_rules()。"""
    return list(items)


REAL_WORLD = rules(
    ("/api/health", ("GET", "HEAD", "OPTIONS")),
    ("/api/health/probe-trend", ("GET", "HEAD", "OPTIONS")),
    ("/api/health/weights", ("PUT", "OPTIONS")),
    ("/api/health/score/calculate", ("POST", "OPTIONS")),
    ("/api/heartbeat", ("GET", "HEAD", "OPTIONS")),
    ("/api/diagnostics/metrics", ("GET", "HEAD", "OPTIONS")),
    ("/api/observability/alerts", ("GET", "POST", "OPTIONS")),
    ("/api/replay/upload", ("POST", "OPTIONS")),
    ("/api/replay/cleanup", ("POST", "OPTIONS")),
    ("/chat", ("GET", "HEAD", "OPTIONS")),
    ("/static/<path:subpath>", ("GET", "HEAD", "OPTIONS")),
)


class TestFindAllowedWriteEndpoints:
    def test_干净清单无命中(self):
        allow = ["/api/health", "/api/health/probe-trend", "/api/heartbeat",
                 "/api/diagnostics/metrics", "/metrics", "/static/*", "/", "/chat"]
        assert find_allowed_write_endpoints(allow, REAL_WORLD) == []

    def test_检出_H5_的_replay_upload(self):
        """审计 H-5 原形：写端点被整条豁免。"""
        hits = find_allowed_write_endpoints(["/api/health", "/api/replay/upload"], REAL_WORLD)
        assert hits == ["/api/replay/upload (POST)"], hits

    def test_检出同路径多方法的写侧(self):
        """GET+POST 同路径时，整条豁免会连 POST 一起放开（本次实测踩到）。"""
        hits = find_allowed_write_endpoints(["/api/observability/alerts"], REAL_WORLD)
        assert hits == ["/api/observability/alerts (POST)"], hits

    def test_检出历史上的_health_前缀事故(self):
        """第一次事故的形态：通配符把子路径写端点一并豁免。"""
        hits = find_allowed_write_endpoints(["/api/health/*"], REAL_WORLD)
        assert "/api/health/weights (PUT)" in hits
        assert "/api/health/score/calculate (POST)" in hits

    def test_精确匹配不误伤子路径(self):
        """/api/health 精确匹配不得覆盖 /api/health/probe-trend 或 weights。"""
        hits = find_allowed_write_endpoints(["/api/health"], REAL_WORLD)
        assert hits == [], hits

    def test_只读豁免永不命中(self):
        allow = ["/api/health/probe-trend", "/api/heartbeat", "/api/diagnostics/metrics",
                 "/metrics", "/api/business/prometheus", "/", "/chat", "/static/*"]
        assert find_allowed_write_endpoints(allow, REAL_WORLD) == []

    def test_默认豁免清单无写端点(self):
        """与 app_server.py 的 CP_API_AUTH_ALLOW 默认值保持一致。"""
        default = "/api/health,/metrics,/api/business/prometheus".split(",")
        assert find_allowed_write_endpoints(default, REAL_WORLD) == []

    @pytest.mark.parametrize("bad", ["/api/replay/upload", "/api/replay/cleanup"])
    def test_replay_写端点不得被豁免(self, bad):
        assert find_allowed_write_endpoints([bad], REAL_WORLD) != []