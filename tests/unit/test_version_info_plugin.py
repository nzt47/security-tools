# -*- coding: utf-8 -*-
"""plugins/version_info.py：只读构建/版本端点注册与封装。"""
from __future__ import annotations

import os
import sys
import types


def test_plugin_registered_and_route_served(monkeypatch):
    # 不导入真实 app_server（重，且会拉起调度器）：注入一个不含 require_token 的假模块，
    # plugin_api._resolve_host_decorator 会回退到 agent.server_auth。
    fake = types.ModuleType("app_server")
    monkeypatch.setitem(sys.modules, "app_server", fake)

    import plugins.version_info as m
    from flask import Flask

    assert m.PLUGIN.name == "version_info"
    assert m.PLUGIN.blueprint is m.bp

    app = Flask(__name__)
    app.register_blueprint(m.bp)
    assert "/api/version" in {str(r) for r in app.url_map.iter_rules()}

    resp = app.test_client().get("/api/version")
    assert resp.status_code == 200
    body = resp.get_json()
    payload = body.get("data", body)
    assert "backend" in payload
    assert "frontend" in payload
    assert payload["process"]["pid"] == os.getpid()
