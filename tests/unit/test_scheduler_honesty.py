"""调度诚实度：action 未实现时不得“创建成功、实际空跑”（P1）。"""
from __future__ import annotations

import json
import sys
import types

import pytest
from flask import Flask

import plugins.mcp_scheduler as sched_plugin


def test_action_execution_defaults_off(monkeypatch):
    from agent import scheduling
    monkeypatch.delenv(scheduling.ACTION_EXECUTION_ENV, raising=False)
    assert scheduling.action_execution_enabled() is False
    monkeypatch.setenv(scheduling.ACTION_EXECUTION_ENV, "1")
    assert scheduling.action_execution_enabled() is True


def test_log_execution_records_executed_flag(monkeypatch, tmp_path):
    from agent import scheduling
    hist = tmp_path / "history.jsonl"
    monkeypatch.setattr(scheduling, "SCHEDULE_HISTORY_FILE", hist)
    s = scheduling.Scheduler()
    s.log_execution("t1", False, "已触发但动作未执行", executed=False)
    row = json.loads(hist.read_text(encoding="utf-8").splitlines()[0])
    assert row["success"] is False
    assert row["executed"] is False
    assert "动作未执行" in row["result"]


def test_schedules_create_refuses_unimplemented_action(monkeypatch):
    from agent import scheduling
    monkeypatch.delenv(scheduling.ACTION_EXECUTION_ENV, raising=False)

    app = Flask(__name__)
    app.register_blueprint(sched_plugin.bp)

    fake = types.ModuleType("app_server")
    fake.require_token = lambda f: f
    fake.log_request = lambda *a, **k: (lambda f: f)
    saved = sys.modules.get("app_server")
    sys.modules["app_server"] = fake
    try:
        client = app.test_client()
        resp = client.post("/api/schedules", json={
            "name": "demo", "action": "run_shell_command", "interval_minutes": 5,
        })
        body = resp.get_json()
        assert resp.status_code == 400
        assert body["error_code"] == "NOT_IMPLEMENTED"
        assert body["implemented"] is False
    finally:
        if saved is not None:
            sys.modules["app_server"] = saved
        else:
            sys.modules.pop("app_server", None)
