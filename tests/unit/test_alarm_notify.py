"""告警外发（`agent/alarms/notify.py`，2026-11-12）

【判据】出站通道早就有了（channel_bridge.dispatch_alert），缺的是"台账 → 通道"的接线。
本文件钉住接线的六条口径：
① 默认只发 critical；warning 需显式开（否则噪音把通道变成第二个静默源）；
② 游标按**行数**推进，重启不重发、轮转不静默丢；
③ 静默（silence）期间不发，但游标照走（解除静默后不倒灌旧告警）；
④ 每轮最多 MAX_PER_ROUND 条，积压下一轮继续；
⑤ 通道没装 = 只推进游标（不是失败），不制造假失败日志；
⑥ 任何异常不外抛（外发是旁路，绝不能拖垮采集线程）。
"""
from __future__ import annotations

import json

import pytest

from agent.alarms import notify
from agent.alarms.store import AlarmStore


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(notify.ENV_ENABLED, raising=False)
    monkeypatch.delenv(notify.ENV_INCLUDE_WARNING, raising=False)
    yield


def _events(tmp_path, records):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
                    encoding="utf-8")
    return path


def _ev(**kw):
    base = {"event": "firing", "at": "2026-11-12T12:00:00+08:00", "id": 1,
            "key": "health.l1_process", "kind": "health", "severity": "critical",
            "state": "firing", "title": "L1 进程降级", "count": 1}
    base.update(kw)
    return base


class _Recorder:
    def __init__(self, result=None):
        self.calls = []
        self._result = result or {"ok": True, "sent": ["ch1"]}

    def __call__(self, alert, transition):
        self.calls.append((alert, transition))
        return self._result


def test_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv(notify.ENV_ENABLED, "false")
    rec = _Recorder()
    out = notify.dispatch_pending(events_path=_events(tmp_path, [_ev()]),
                                  cursor_path=tmp_path / "cur.json", dispatcher=rec)
    assert out["status"] == "disabled"
    assert rec.calls == []


def test_firing_critical_is_sent_and_cursor_advances(tmp_path):
    rec = _Recorder()
    cur = tmp_path / "cur.json"
    out = notify.dispatch_pending(events_path=_events(tmp_path, [_ev()]),
                                  cursor_path=cur, dispatcher=rec)
    assert out["sent"] == 1 and out["failed"] == 0
    assert len(rec.calls) == 1 and rec.calls[0][1] == "firing"
    assert rec.calls[0][0].labels["key"] == "health.l1_process"
    assert notify.load_cursor(cur) == 1

    # 再跑一轮：同一事件不得重发
    out2 = notify.dispatch_pending(events_path=_events(tmp_path, [_ev()]),
                                   cursor_path=cur, dispatcher=rec)
    assert out2["sent"] == 0 and len(rec.calls) == 1


def test_warning_skipped_by_default_sent_when_enabled(tmp_path, monkeypatch):
    rec = _Recorder()
    cur = tmp_path / "cur.json"
    path = _events(tmp_path, [_ev(severity="warning")])
    out = notify.dispatch_pending(events_path=path, cursor_path=cur, dispatcher=rec)
    assert out["sent"] == 0 and out["skipped"] == 1
    assert notify.load_cursor(cur) == 1          # 跳过也推进，避免解除开关后倒灌

    monkeypatch.setenv(notify.ENV_INCLUDE_WARNING, "true")
    cur2 = tmp_path / "cur2.json"
    out2 = notify.dispatch_pending(events_path=path, cursor_path=cur2, dispatcher=rec)
    assert out2["sent"] == 1


def test_resolved_transition(tmp_path):
    rec = _Recorder()
    notify.dispatch_pending(events_path=_events(tmp_path, [_ev(event="resolved",
                                                              state="resolved")]),
                            cursor_path=tmp_path / "cur.json", dispatcher=rec)
    assert [c[1] for c in rec.calls] == ["resolved"]


def test_silenced_alarm_is_skipped_but_cursor_advances(tmp_path):
    store = AlarmStore(tmp_path / "state.json", tmp_path / "store-events.jsonl")
    alarm = store.raise_alarm("health.l1_process", kind="health", severity="critical",
                              title="L1 进程降级")
    store.silence(alarm.id, minutes=30)
    rec = _Recorder()
    cur = tmp_path / "cur.json"
    out = notify.dispatch_pending(store=store,
                                  events_path=_events(tmp_path, [_ev()]),
                                  cursor_path=cur, dispatcher=rec)
    assert out["sent"] == 0 and out["skipped"] == 1
    assert rec.calls == []
    assert notify.load_cursor(cur) == 1


def test_round_cap_and_resume(tmp_path):
    rec = _Recorder()
    cur = tmp_path / "cur.json"
    path = _events(tmp_path, [_ev(id=i, key="k%d" % i) for i in range(7)])
    first = notify.dispatch_pending(events_path=path, cursor_path=cur, dispatcher=rec)
    assert first["sent"] == notify.MAX_PER_ROUND
    assert notify.load_cursor(cur) == notify.MAX_PER_ROUND

    second = notify.dispatch_pending(events_path=path, cursor_path=cur, dispatcher=rec)
    assert second["sent"] == 7 - notify.MAX_PER_ROUND
    assert notify.load_cursor(cur) == 7


def test_cursor_beyond_file_resets(tmp_path):
    """事件文件被轮转/截断（游标 > 行数）⇒ 从头读，宁可重发一条也不静默丢。"""
    cur = tmp_path / "cur.json"
    notify.save_cursor(99, cur)
    rec = _Recorder()
    out = notify.dispatch_pending(events_path=_events(tmp_path, [_ev(), _ev(id=2, key="k2")]),
                                  cursor_path=cur, dispatcher=rec)
    assert out["sent"] == 2
    assert notify.load_cursor(cur) == 2


def test_dispatcher_exception_is_swallowed(tmp_path):
    def boom(alert, transition):
        raise RuntimeError("channel exploded")

    out = notify.dispatch_pending(events_path=_events(tmp_path, [_ev()]),
                                  cursor_path=tmp_path / "cur.json", dispatcher=boom)
    assert out["failed"] == 1
    assert notify.load_cursor(tmp_path / "cur.json") == 1


def test_no_enabled_channel_is_not_a_failure(tmp_path):
    rec = _Recorder(result={"ok": True, "sent": [], "reason": "no_enabled_channel"})
    out = notify.dispatch_pending(events_path=_events(tmp_path, [_ev()]),
                                  cursor_path=tmp_path / "cur.json", dispatcher=rec)
    assert out["sent"] == 1 and out["failed"] == 0
    assert notify.load_cursor(tmp_path / "cur.json") == 1


def test_status_reports_last_round(tmp_path):
    rec = _Recorder()
    cur = tmp_path / "cur.json"
    notify.dispatch_pending(events_path=_events(tmp_path, [_ev()]),
                            cursor_path=cur, dispatcher=rec)
    st = notify.status(cursor_path=cur, events_path=_events(tmp_path, [_ev()]))
    assert st["last_round"]["sent"] == 1
    assert st["last_round"]["at"]


def test_status_reports_queued(tmp_path):
    path = _events(tmp_path, [_ev(), _ev(id=2, key="k2")])
    cur = tmp_path / "cur.json"
    notify.save_cursor(1, cur)
    st = notify.status(cursor_path=cur, events_path=path)
    assert st["queued"] == 1 and st["event_lines"] == 2 and st["cursor_lines"] == 1


# ════════════════════════════════════════════════════════════
#  ⑦ 覆盖缺口首现提示（2026-11-12，monitoring-gap-detector）
#     —— 唯一**不受** CP_ALARM_NOTIFY_WARNING 过滤的 warning：调用方按指纹去重，
#     每处缺口一辈子只走一次，不构成"每轮都响"的噪音；压成不发等于让它继续静默。
# ════════════════════════════════════════════════════════════

def _gap(gap_id="agent/lonely/", kind="package"):
    return {"kind": kind, "id": gap_id,
            "why": "声明了模块元数据但没有状态源", "fix": "补 MODULE_STATUS_SOURCE"}


def test_dispatch_gaps_sends_warning_even_when_warning_filter_off():
    rec = _Recorder()
    out = notify.dispatch_gaps([_gap()], dispatcher=rec)
    assert out["sent"] == ["package:agent/lonely/"]
    assert out["failed"] == []
    (alert, transition), = rec.calls
    assert transition == "firing"
    assert alert["severity"] == "warning"
    assert alert["labels"]["kind"] == notify.GAP_KEY_LABEL
    assert "补 MODULE_STATUS_SOURCE" in alert["message"]


def test_dispatch_gaps_respects_limit():
    rec = _Recorder()
    rows = [_gap("x%d" % i) for i in range(notify.MAX_GAP_PER_ROUND + 3)]
    out = notify.dispatch_gaps(rows, dispatcher=rec)
    assert out["attempted"] == notify.MAX_GAP_PER_ROUND
    assert len(rec.calls) == notify.MAX_GAP_PER_ROUND


def test_dispatch_gaps_counts_no_channel_as_failed():
    """没通道 ≠ 发出去了：调用方据此**不记指纹**，配好通道后还能收到第一次提示。"""
    rec = _Recorder(result={"ok": True, "sent": [], "reason": "no_enabled_channel"})
    out = notify.dispatch_gaps([_gap()], dispatcher=rec)
    assert out["sent"] == [] and out["failed"] == ["package:agent/lonely/"]
    assert out["ok"] is False
    assert out["reason"] == "no_enabled_channel"


def test_dispatch_gaps_swallows_dispatcher_exception():
    def boom(alert, transition):
        raise RuntimeError("channel exploded")

    out = notify.dispatch_gaps([_gap()], dispatcher=boom)
    assert out["failed"] == ["package:agent/lonely/"]
    assert out["ok"] is False


def test_dispatch_gaps_disabled_by_env(monkeypatch):
    monkeypatch.setenv(notify.ENV_ENABLED, "false")
    rec = _Recorder()
    out = notify.dispatch_gaps([_gap()], dispatcher=rec)
    assert out["reason"] == "disabled" and rec.calls == []
