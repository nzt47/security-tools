"""回归守卫（安全·2026-10-03 修复审计 H-5）：POST /api/replay/upload 必须强制鉴权。

背景：该端点此前**同时**满足两个"无鉴权"条件 ——
  1. 视图函数只有 @trace_route / @log_request，没有任何 @require_token；
  2. 路径被 .env 的 CP_API_AUTH_ALLOW 显式豁免（理由：sendBeacon 无法带自定义头）。
因此任意可访问 5678 端口者可无令牌上传回放数据。

修复方式（两处必须**同时**成立，缺一即回归）：
  - 后端：视图函数补 @require_token；
  - 配置：CP_API_AUTH_ALLOW 摘除 /api/replay/upload；
  - 前端：已配置令牌时跳过 sendBeacon，改走 fetch(keepalive)（见 sessionReplay.ts）。

本文件只覆盖后端两处（配置项由 test_auth_allowlist_no_write_endpoints 覆盖）。
"""
from __future__ import annotations

import importlib
import os

import pytest
from flask import Flask


@pytest.fixture
def replay_app(monkeypatch):
    """最小 Flask app + 真实令牌配置（否则 require_token 在无令牌时恒放行）。"""
    monkeypatch.setenv("FLASK_API_TOKEN", "unit-test-token-" + "x" * 40)
    monkeypatch.delenv("CP_UI_TOKENS", raising=False)
    monkeypatch.delenv("CP_API_AUTH_ALLOW", raising=False)
    import agent.server_auth as server_auth
    importlib.reload(server_auth)
    import agent.server_routes.routes_replay as routes_replay
    importlib.reload(routes_replay)
    app = Flask(__name__)
    routes_replay.register_routes(app, None)
    app.config.update(TESTING=True)
    yield app
    # ══════════════════════════════════════════════════════════════════
    # 【必须显式还原，否则污染后续测试 —— 2026-10-03 实测踩到】
    # agent.server_auth 在 import 期就把「令牌是否已配置」固化进模块状态。
    # 本 fixture 在**设了 FLASK_API_TOKEN** 的前提下 reload 它 ⇒ 模块停留在
    # 「已配置」态；而 monkeypatch 要到测试结束才撤销环境变量，于是后续用例
    # （如 tests/test_plugin_submit_url.py 走 plugins/status.py 的 @_require_token）
    # 会看到「已配置 ⇒ 校验」而不是它们预期的「未配置 ⇒ 放行」，凭空 2 条假红。
    # 修法：先 undo 环境变量，再按干净环境重建模块状态；顺序不能反。
    # ══════════════════════════════════════════════════════════════════
    monkeypatch.undo()
    importlib.reload(server_auth)
    importlib.reload(routes_replay)


def _post_upload(client, headers=None):
    return client.post("/api/replay/upload", json={"replay_id": "r1", "data": ""}, headers=headers or {})


class TestReplayUploadRequiresToken:
    def test_无令牌上传返回401(self, replay_app):
        resp = _post_upload(replay_app.test_client())
        assert resp.status_code == 401, (
            f"POST /api/replay/upload 无令牌时返回 {resp.status_code}（应为 401）—— "
            "审计 H-5 回归：该端点又变成了未授权可写。"
        )

    def test_错误令牌上传返回401(self, replay_app):
        resp = _post_upload(replay_app.test_client(), {"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401

    def test_正确令牌可进入业务逻辑_不再401(self, replay_app):
        resp = _post_upload(
            replay_app.test_client(),
            {"Authorization": "Bearer " + os.environ["FLASK_API_TOKEN"]},
        )
        assert resp.status_code != 401, "带正确令牌仍被拒，说明装饰器接错或令牌通道异常"

    def test_cleanup端点同样受保护(self, replay_app):
        resp = replay_app.test_client().post("/api/replay/cleanup", json={"days": 30})
        assert resp.status_code == 401