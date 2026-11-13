# -*- coding: utf-8 -*-
"""warning 摘要外发（agent/alarms/notify.py，2026-11-13）。

【为什么】默认只外发 critical，warning 全压在站内 —— 用户不盯页面时等于静默。
逐条发又会变噪音源。折中：**每小时最多一条汇总**，且静默优先。
"""
from __future__ import annotations

import json

import pytest

from agent.alarms import notify


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.delenv(notify.ENV_ENABLED, raising=False)
    monkeypatch.delenv(notify.ENV_INCLUDE_WARNING, raising=False)
    monkeypatch.delenv(notify.ENV_WARNING_DIGEST, raising=False)
    monkeypatch.setattr(notify, "_root", lambda: tmp_path)
    yield


def _events(tmp_path, records):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
                    encoding="utf-8")
    return path


def _ev(**kw):
    base = {"event": "firing", "at": "2026-11-13T12:00:00+08:00", "id": 1,
            "key": "unit.skills", "kind": "unit", "severity": "warning",
            "state": "firing", "title": "技能异常", "count": 1}
    base.update(kw)
    return base


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, alert, transition):
        self.calls.append((alert, transition))
        return {"ok": True, "sent": ["ch1"]}


def test_warning不当场逐条发_而是攒起来(tmp_path):
    rec = _Recorder()
    notify.dispatch_pending(events_path=_events(tmp_path, [_ev()]),
                            cursor_path=tmp_path / "cur.json", dispatcher=rec)

    assert rec.calls == [], "warning 不该逐条外发"
    state = notify.load_digest()
    assert state["pending"][0]["key"] == "unit.skills" and state["pending"][0]["count"] == 1


def test_下一轮合并成一条发出_且清空待发(tmp_path):
    rec = _Recorder()
    events = _events(tmp_path, [_ev(), _ev(key="scheduler.job", title="任务失败")])
    cur = tmp_path / "cur.json"
    notify.dispatch_pending(events_path=events, cursor_path=cur, dispatcher=rec)
    notify.dispatch_pending(events_path=events, cursor_path=cur, dispatcher=rec)

    assert len(rec.calls) == 1
    alert, transition = rec.calls[0]
    assert alert.name == notify.DIGEST_TITLE and transition == "firing"
    assert "技能异常" in alert.message and "任务失败" in alert.message
    assert notify.load_digest()["pending"] == []


def test_关闭后不攒也不发(tmp_path, monkeypatch):
    monkeypatch.setenv(notify.ENV_WARNING_DIGEST, "0")
    rec = _Recorder()
    notify.dispatch_pending(events_path=_events(tmp_path, [_ev()]),
                            cursor_path=tmp_path / "cur.json", dispatcher=rec)
    notify.dispatch_pending(events_path=_events(tmp_path, [_ev()]),
                            cursor_path=tmp_path / "cur.json", dispatcher=rec)

    assert rec.calls == [] and notify.load_digest()["pending"] == []


def test_发不出去时保留待发(tmp_path):
    class _Bad:
        def __init__(self):
            self.calls = 0

        def __call__(self, alert, transition):
            self.calls += 1
            return {"ok": False, "reason": "not_sent"}

    bad = _Bad()
    events = _events(tmp_path, [_ev()])
    cur = tmp_path / "cur.json"
    notify.dispatch_pending(events_path=events, cursor_path=cur, dispatcher=bad)
    notify.dispatch_pending(events_path=events, cursor_path=cur, dispatcher=bad)

    assert bad.calls == 1, "失败也算尝试过一次（有 1 小时间隔）"
    assert notify.load_digest()["pending"], "发不出去不能把待发丢掉"
