"""回归守卫（2026-10-03 · 审计 H-2 补实现）：API 网关适配层必须真的挂上且不越权。

【背景】agent/api_gateway_flask.py 长期缺失，app_server.py:2179 的 import 一直走
except ImportError ⇒ /api/open/* 与 /api/docs 整体不可用而进程报告健康。
本文件钉住三件事：
  ① 模块能挂载，/api/docs 产出 OpenAPI 3.0 且覆盖内部路由；
  ② 开放面**默认空**，未登记路径一律 404；
  ③ 内部路由**不会**因为被扫进文档就变成可经 /api/open/* 调用的公开端点。
"""
from __future__ import annotations

import importlib

import pytest
from flask import Flask, jsonify


@pytest.fixture
def gw(monkeypatch):
    monkeypatch.delenv("YUNSHU_OPEN_API_ENDPOINTS", raising=False)
    import agent.api_gateway_flask as m
    importlib.reload(m)  # 清空模块级 _open_endpoints，保证用例互不污染
    return m


@pytest.fixture
def app_with_gateway(gw):
    app = Flask(__name__)

    @app.route("/api/status")
    def _status():
        return jsonify({"ok": True})

    @app.route("/api/chat", methods=["POST"])
    def _chat():
        return jsonify({"ok": True})

    info = gw.register_gateway(app)
    app.config.update(TESTING=True)
    return app, info


class TestGatewayMount:
    def test_挂载成功并登记内部路由(self, app_with_gateway):
        _, info = app_with_gateway
        assert info["open_prefix"] == "/api/open"
        assert info["internal_scanned"] >= 2, "至少应扫到 /api/status 与 /api/chat"
        assert info["open_declared"] == 0, "默认不得开放任何端点（fail-closed）"

    def test_默认开放面为空(self, gw, app_with_gateway):
        assert gw.open_endpoints() == []


class TestDocs:
    def test_docs_返回_openapi3(self, app_with_gateway):
        app, _ = app_with_gateway
        resp = app.test_client().get("/api/docs")
        assert resp.status_code == 200
        doc = resp.get_json()
        assert doc["openapi"] == "3.0.0"

    def test_docs_覆盖内部路由(self, app_with_gateway):
        app, _ = app_with_gateway
        doc = app.test_client().get("/api/docs").get_json()
        assert "/api/status" in doc["paths"], "内部路由应出现在文档里（它是给内部人看的）"
        assert "post" in doc["paths"]["/api/chat"]


class TestOpenSurfaceIsClosedByDefault:
    def test_未登记的开放路径404(self, app_with_gateway):
        app, _ = app_with_gateway
        resp = app.test_client().get("/api/open/anything")
        assert resp.status_code == 404, "默认不开放任何端点，未登记路径必须 404"

    def test_内部路由不可经_open_前缀调用(self, app_with_gateway):
        """核心安全断言：被扫进文档 ≠ 被公开。"""
        app, _ = app_with_gateway
        c = app.test_client()
        for p in ("/api/open/api/status", "/api/open/status", "/api/open/api/chat"):
            assert c.get(p).status_code == 404, p + " 不应可达（否则扫描会顺带公开整个内部 API）"


class TestExplicitOpenRegistration:
    def test_拒绝非_open_前缀的登记(self, gw, app_with_gateway):
        """刻意的前缀硬检查：防止把内部路由误登记成公开端点。"""
        with pytest.raises(ValueError):
            gw.register_open_endpoint("GET", "/api/status", lambda r: {"ok": True})

    def test_显式登记后可调用且要求_API_Key(self, gw, app_with_gateway):
        _, _ = app_with_gateway
        gw.register_open_endpoint("GET", "/api/open/ping",
                                  lambda r: {"ok": True, "pong": True}, summary="探活")
        # 需要重新构造 app 才能带上新注册的端点？—— 不需要：网关是单例，路由已存在
        app2 = Flask(__name__)
        # 复用同一网关实例（get_api_gateway 是单例），只需再挂一次路由
        gw.register_gateway(app2)
        app2.config.update(TESTING=True)
        c = app2.test_client()
        assert c.get("/api/open/ping").status_code == 401, "开放端点默认要求 API Key"

    def test_登记后出现在文档里(self, gw, app_with_gateway):
        gw.register_open_endpoint("GET", "/api/open/ping2", lambda r: {"ok": True})
        app, _ = app_with_gateway
        doc = app.test_client().get("/api/docs").get_json()
        assert "/api/open/ping2" in doc["paths"]