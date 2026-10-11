# -*- coding: utf-8 -*-
"""回调 outbox（离线回退）单测：opt-in、按序重放、幂等标记、fail-soft。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.subagent import callback_outbox as ob


def _env(tmp_path, on=True):
    return {ob.ENV_OUTBOX_DIR: (str(tmp_path) if on else "")}


def test_未配置时全是_noop(tmp_path):
    env = _env(tmp_path, on=False)
    assert ob.outbox_enabled(env) is False
    assert ob.enqueue("http://h/x", {"a": 1}, environ=env) is None
    assert ob.pending(environ=env) == []
    out = ob.replay(lambda u, p: {"delivered": True}, environ=env)
    assert out["degraded"] == "outbox_disabled" and out["attempted"] == 0


def test_enqueue_pending_and_replay(tmp_path):
    env = _env(tmp_path)
    rid = ob.enqueue("http://h/a", {"delegation_id": "d1"}, error="boom", environ=env)
    assert rid
    pend = ob.pending(environ=env)
    assert len(pend) == 1 and pend[0]["callback_url"] == "http://h/a"

    seen = []

    def disp(url, payload):
        seen.append((url, payload))
        return {"delivered": True, "mode": "http"}

    out = ob.replay(disp, environ=env)
    assert out == {"attempted": 1, "delivered": 1, "failed": 0, "remaining": 0, "degraded": ""}
    assert seen == [("http://h/a", {"delegation_id": "d1"})]
    assert ob.pending(environ=env) == [], "已投递不再 pending"


def test_replay_失败保留待重放(tmp_path):
    env = _env(tmp_path)
    ob.enqueue("http://h/a", {"x": 1}, environ=env)
    out = ob.replay(lambda u, p: {"delivered": False, "mode": "http"}, environ=env)
    assert out["delivered"] == 0 and out["failed"] == 1 and out["remaining"] == 1


def test_replay_单条异常不影响其余(tmp_path):
    env = _env(tmp_path)
    ob.enqueue("http://h/a", {"x": 1}, environ=env)
    ob.enqueue("http://h/b", {"x": 2}, environ=env)

    def disp(url, payload):
        if url.endswith("/a"):
            raise RuntimeError("boom")
        return {"delivered": True}

    out = ob.replay(disp, environ=env)
    assert out["attempted"] == 2 and out["delivered"] == 1 and out["failed"] == 1
    assert out["degraded"].startswith("replay_failed:")
    assert len(ob.pending(environ=env)) == 1


def test_peer_report_反映_outbox_开关(tmp_path, monkeypatch):
    from agent.subagent.peers import offline_fallback_report
    monkeypatch.delenv(ob.ENV_OUTBOX_DIR, raising=False)
    r0 = offline_fallback_report()
    assert r0["outbox"] is True and r0["enabled"] is False
    monkeypatch.setenv(ob.ENV_OUTBOX_DIR, str(tmp_path))
    r1 = offline_fallback_report()
    assert r1["enabled"] is True
