# -*- coding: utf-8 -*-
"""技能管理路由的**错误卫生**：未预期异常不得把原文写进响应体。

2026-11-02（CodeQL B2 批次）：`routes_skills_mgmt.py` 原有 78 条
`py/stack-trace-exposure`，全部来自 `return jsonify({"ok": False, "error": str(e)})`。
统一收口到 `_fail(e, ...)` 后：响应体只给固定文案，原文 + 堆栈进日志（§8 陷阱 15）。

本用例守两件事：
1. 异常路径的响应体**不含**异常原文、也不含异常类名；
2. `_fail` 的返回形状仍是 `{ok: false, error: <固定文案>}` + 指定状态码（前端契约不变）。
"""
from __future__ import annotations

import pytest
from flask import Flask

from agent import server_auth
from agent.server_routes import routes_skills_mgmt as routes


class _Boom:
    """任何属性访问都抛带密钥字样的异常。"""

    def __getattr__(self, _name):  # noqa: D105
        def _raise(*_a, **_k):
            raise RuntimeError("SECRET_INTERNAL_PATH=/root/.ssh/id_rsa")
        return _raise


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(server_auth, "authorize_request", lambda: (True, "t", "t"))
    monkeypatch.setattr(routes, "_svc", lambda: _Boom())
    app = Flask(__name__)
    routes.register_routes(app, state=None)
    return app.test_client()


@pytest.mark.parametrize("path", [
    "/api/skills-mgmt/health",
    "/api/skills-mgmt/cleanup/report",
])
def test_异常路径不泄漏原文(client, path):
    resp = client.get(path)
    assert resp.status_code == 500
    body = resp.get_data(as_text=True)
    assert "SECRET_INTERNAL_PATH" not in body
    assert "RuntimeError" not in body
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"] and "详见" in data["error"]


def test_fail_保持状态码与形状():
    app = Flask(__name__)
    with app.app_context():
        resp, code = routes._fail(RuntimeError("boom"), "自定义文案", 400)
        assert code == 400
        assert resp.get_json() == {"ok": False, "error": "自定义文案"}
