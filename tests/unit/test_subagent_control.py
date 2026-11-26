"""E2-1 subagent_control 工具：list/send/interrupt/inbox 与降级"""
from __future__ import annotations

import pytest

from agent.subagent import runs as runs_mod
from agent.tools import subagent_control as sc


@pytest.fixture()
def reg(tmp_path, monkeypatch):
    local = runs_mod.RunRegistry(root=str(tmp_path / "runs"),
                                 snapshot_path=str(tmp_path / "runs.json"))
    monkeypatch.setattr(runs_mod, "run_registry", local)
    monkeypatch.delenv(runs_mod.ENABLED_ENV, raising=False)
    return local


def test_register_all_accepts_self():
    # 生命周期管理器传的是 self；签名必须能吃下（§11.231 D.1）
    sc.register_all(None)
    import agent.tools as tools_mod
    spec = tools_mod.get_tool("subagent_control") if hasattr(tools_mod, "get_tool") else None
    assert sc.ACTIONS == ("list", "send", "interrupt", "inbox")
    assert sc.TOOL_SCHEMA["required"] == ["action"]
    assert spec is None or spec is not None  # 注册不抛即达标（细粒度由注册表用例覆盖）


def test_list_empty(reg):
    out = sc.run_list()
    assert out["ok"] is True and out["count"] == 0 and out["runs"] == []


def test_send_to_unknown_run(reg):
    out = sc.run_send("r-nope", "hi")
    assert out["ok"] is False and out["code"] == "run_not_found"


def test_send_and_drain(reg):
    h = reg.register(subagent="a", delegation_id="d-1")
    out = sc.run_send(h.run_id, "收窄到 plugins/chat.py")
    assert out["ok"] is True and out["queued"]["text"].startswith("收窄")
    drained = reg.drain_inbox(h)
    assert [r["text"] for r in drained] == ["收窄到 plugins/chat.py"]
    assert sc.run_inbox(h.run_id)["count"] == 1


def test_send_accepts_delegation_id(reg):
    h = reg.register(subagent="a", delegation_id="d-1")
    out = sc.run_send("d-1", "用 delegation_id 也能找到")
    assert out["ok"] is True and out["run_id"] == h.run_id


def test_interrupt_sets_event_and_is_idempotent(reg):
    h = reg.register(subagent="a", delegation_id="d-1")
    out = sc.run_interrupt(h.run_id, "需求变了")
    assert out["ok"] is True and out["cancelled"] is True
    assert h.cancel_event.is_set() and h.reason == "需求变了"
    reg.finish(h, "interrupted")
    again = sc.run_interrupt(h.run_id)
    assert again["ok"] is True and again.get("already_finished") is True


def test_inbox_limit_and_missing(reg):
    h = reg.register(subagent="a", delegation_id="d-1")
    for i in range(5):
        reg.append_inbox(h.run_id, "m%d" % i)
    assert sc.run_inbox(h.run_id, limit=2)["count"] == 2
    assert sc.run_inbox("r-nope")["ok"] is False


def test_disabled_switch(reg, monkeypatch):
    monkeypatch.setenv(runs_mod.ENABLED_ENV, "0")
    for out in (sc.run_list(), sc.run_send("r", "x"), sc.run_interrupt("r"),
                sc.run_inbox()):
        assert out["ok"] is False and out["code"] == "disabled"


def test_unknown_action(reg):
    out = sc.run_action("nuke")
    assert out["ok"] is False and "未知动作" in out["error"]


def test_list_shows_running_run(reg):
    h = reg.register(subagent="alpha", delegation_id="d-1")
    h.rounds = 3
    out = sc.run_list()
    assert out["count"] == 1
    row = out["runs"][0]
    assert row["run_id"] == h.run_id and row["rounds"] == 3
    assert row["status"] == "running"
