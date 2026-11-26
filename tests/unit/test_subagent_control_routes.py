"""E2-1b 运行中控制 HTTP 面测试（GET /api/subagent/runs + message / interrupt）

本文件锁死：
  1. `/api/subagent/runs` 是**静态路径**，必须命中运行中处理器，不能被
     `/api/subagent/<name>` 抢走（否则表现是"分身不存在: runs"的 404）；
  2. 形状：`{ok, runs, count, running, enabled, generated_at}`，run 字段可直接被 UI 渲染；
  3. message 空 ⇒ 400；未知 run ⇒ 404；正常 ⇒ 200 + 进 inbox（下一轮 drain 得到）；
  4. interrupt ⇒ 200 + cancel_event 置位；已结束的 run ⇒ already_finished；
  5. `CP_SUBAGENT_CONTROL_ENABLED=0` ⇒ 三条路由都 404（回滚口径，不是 500）。
"""
from __future__ import annotations

import pytest
from flask import Flask

from agent.server_routes import routes_subagent as routes_module
from agent.server_routes.routes_subagent import register_routes
from agent.subagent import runs as runs_mod


class _FakeYunshu:
    """这三条路由不碰分身管理器；给最小实现即可"""

    _subagent_mgr = None
    _llm = None

    def list_subagents(self):
        return []


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def _make():
        local = runs_mod.RunRegistry(root=str(tmp_path / "runs"),
                                     snapshot_path=str(tmp_path / "runs.json"))
        monkeypatch.setattr(runs_mod, "run_registry", local)
        monkeypatch.delenv(runs_mod.ENABLED_ENV, raising=False)
        state = type("S", (), {"Yunshu": _FakeYunshu()})()
        app = Flask(__name__)
        app.config.update(TESTING=True)
        register_routes(app, state)
        return app.test_client(), local
    return _make


class TestRunsList:
    def test_空表形状(self, make_client):
        client, _ = make_client()
        r = client.get("/api/subagent/runs")
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        assert body["ok"] is True
        assert body["count"] == 0 and body["runs"] == []
        assert body["running"] == 0 and body["enabled"] is True
        assert body["generated_at"]

    def test_列出运行中的_run_且不被_名称路由抢走(self, make_client):
        client, local = make_client()
        h = local.register(subagent="alpha", delegation_id="dlg-1")
        h.rounds = 2
        h.set_output("正在读 plugins/chat.py")
        body = client.get("/api/subagent/runs").get_json()
        assert body["count"] == 1
        row = body["runs"][0]
        assert row["run_id"] == h.run_id
        assert row["subagent"] == "alpha" and row["status"] == "running"
        assert row["rounds"] == 2 and row["last_output"].startswith("正在读")
        # 路由优先级：不是 /api/subagent/<name> 的 404（"分身不存在: runs"）
        assert "分身不存在" not in str(body)


class TestMessage:
    def test_追加指令进_inbox_并可被_drain(self, make_client):
        client, local = make_client()
        h = local.register(subagent="alpha", delegation_id="dlg-1")
        r = client.post(f"/api/subagent/runs/{h.run_id}/message",
                        json={"message": "收窄到 plugins/chat.py"})
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        assert body["ok"] is True and body["run_id"] == h.run_id
        assert body["queued"]["text"] == "收窄到 plugins/chat.py"
        assert [x["text"] for x in local.drain_inbox(h)] == ["收窄到 plugins/chat.py"]

    def test_空消息_400(self, make_client):
        client, local = make_client()
        h = local.register(subagent="alpha", delegation_id="dlg-1")
        r = client.post(f"/api/subagent/runs/{h.run_id}/message", json={"message": "   "})
        assert r.status_code == 400
        assert r.get_json()["code"] == "invalid_message"

    def test_未知_run_404(self, make_client):
        client, _ = make_client()
        r = client.post("/api/subagent/runs/r-nope/message", json={"message": "x"})
        assert r.status_code == 404
        assert r.get_json()["code"] == "run_not_found"

    def test_允许用_delegation_id_兜底(self, make_client):
        client, local = make_client()
        h = local.register(subagent="alpha", delegation_id="dlg-1")
        r = client.post("/api/subagent/runs/dlg-1/message", json={"message": "用兜底 id"})
        assert r.status_code == 200 and r.get_json()["run_id"] == h.run_id


class TestInterrupt:
    def test_置位取消事件(self, make_client):
        client, local = make_client()
        h = local.register(subagent="alpha", delegation_id="dlg-1")
        r = client.post(f"/api/subagent/runs/{h.run_id}/interrupt", json={"reason": "需求变了"})
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        assert body["ok"] is True and body["cancelled"] is True
        assert h.cancel_event.is_set()
        assert h.reason == "需求变了"

    def test_已结束的_run_回_already_finished(self, make_client):
        client, local = make_client()
        h = local.register(subagent="alpha", delegation_id="dlg-1")
        local.finish(h, "interrupted", "先前的打断")
        body = client.post(f"/api/subagent/runs/{h.run_id}/interrupt", json={}).get_json()
        assert body["ok"] is True and body.get("already_finished") is True

    def test_未知_run_404(self, make_client):
        client, _ = make_client()
        assert client.post("/api/subagent/runs/r-nope/interrupt", json={}).status_code == 404


class TestDisabled:
    def test_关闭开关三条路由都_404(self, make_client, monkeypatch):
        client, local = make_client()
        h = local.register(subagent="alpha", delegation_id="dlg-1")
        monkeypatch.setenv(runs_mod.ENABLED_ENV, "0")
        assert client.get("/api/subagent/runs").status_code == 404
        assert client.post(f"/api/subagent/runs/{h.run_id}/message",
                           json={"message": "x"}).status_code == 404
        assert client.post(f"/api/subagent/runs/{h.run_id}/interrupt",
                           json={}).status_code == 404


@pytest.fixture(scope="module")
def real_url_paths() -> set:
    """真实路由表（`import app_server` 后枚举；同进程只付一次导入代价）

    【为什么必须查真实入口】本仓有过"手搓 Flask 全绿、线上 404"的教训：
    `register_routes` 的单元测试只能证明"注册时写了这条规则"，证明不了
    生产装配真的调用了它。导入副作用（内建工具登记）用完整表还原。
    """
    from agent import tools as _tools

    saved = dict(_tools._registry)
    try:
        import app_server  # noqa: PLC0415 真实入口，与生产同一份注册代码
        yield {str(r.rule) for r in app_server.app.url_map.iter_rules()}
    finally:
        _tools._registry.clear()
        _tools._registry.update(saved)
        _tools._registry_version += 1


def test_三条运行中路由已在真实入口注册(real_url_paths):
    assert "/api/subagent/runs" in real_url_paths
    assert "/api/subagent/runs/<run_id>/message" in real_url_paths
    assert "/api/subagent/runs/<run_id>/interrupt" in real_url_paths

