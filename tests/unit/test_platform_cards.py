# -*- coding: utf-8 -*-
"""`/api/system/platform-cards`（平台能力卡）的单元测试。

守三件事：
1. 需要令牌（401 出口不能因为「都是只读」就放开）；
2. 三个分组都在（功能单位 / 工具 / 调度任务），且结构稳定；
3. 功能单位与 `/api/system/units` **同源**（同一套 status/summary），不另算一份。
"""
from __future__ import annotations

import pytest
from flask import Flask

from agent import server_auth
from agent.server_routes import routes_units


def _client() -> Flask:
    app = Flask(__name__)
    routes_units.register_routes(app)
    return app


def test_路由标记为需要令牌():
    """未配置令牌时 authorize_request 会放行（本机 dev 口径），所以不测 401 状态码，
    改测**静态事实**：视图函数带 REQUIRES_TOKEN_ATTR —— 这才是「有没有挂鉴权」的判据。"""
    app = _client()
    view = app.view_functions["api_system_platform_cards"]
    assert getattr(view, server_auth.REQUIRES_TOKEN_ATTR, False) is True


def test_三个分组齐全(monkeypatch):
    monkeypatch.setattr(server_auth, "authorize_request", lambda: (True, "test", "test"))
    app = _client()
    r = app.test_client().get("/api/system/platform-cards")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    kinds = {g["kind"] for g in d["groups"]}
    assert {"unit", "tool", "task"} <= kinds
    for g in d["groups"]:
        assert g["title"] and isinstance(g["items"], list)
        assert g["count"] == len(g["items"])


def test_功能单位与_units_同源(monkeypatch):
    monkeypatch.setattr(server_auth, "authorize_request", lambda: (True, "test", "test"))
    app = _client()
    c = app.test_client()
    cards = c.get("/api/system/platform-cards").get_json()
    units = c.get("/api/system/units").get_json()
    group = [g for g in cards["groups"] if g["kind"] == "unit"][0]
    by_id = {u["id"]: u for u in units["units"]}
    assert group["count"] == len(units["units"])
    for item in group["items"]:
        src = by_id[item["id"]]
        assert item["status"] == src["status"]
        assert item["summary"] == src["summary"]
