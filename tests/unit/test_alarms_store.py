# -*- coding: utf-8 -*-
"""报警器事件库（agent/alarms/store.py）行为测试。

钉住四条口径：
1. **同 key 去重**：重复发现只 count+1、刷新 last_seen，不刷屏；
2. **恢复不删除**：条件消失 → state=resolved + resolved_at，且恢复本身也进未读；
3. **静默不等于删除**：静默期满自动重新计入未读，状态与 count 不变；
4. **落盘可回放**：新实例从 state.json 读回同一份当前态（重启不丢）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent.alarms.store import (
    DEFAULT_SILENCE_MINUTES,
    MAX_SILENCE_MINUTES,
    STATE_FIRING,
    STATE_RESOLVED,
    AlarmStore,
)

T0 = datetime(2026, 11, 11, 1, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> AlarmStore:
    return AlarmStore(tmp_path / "state.json", tmp_path / "events.jsonl")


def _raise(store: AlarmStore, key="health.l3_llm_tool", **kw):
    args = dict(kind="health", severity="critical", title="L3 降级", detail="err 40%")
    args.update(kw)
    return store.raise_alarm(key, now=T0, **args)


class TestResolvedReview:
    """「已恢复」是自动翻的；复核是人的动作（2026-11-13）。"""

    def test_恢复未确认计入_resolved_unacked(self, store):
        _raise(store)
        store.resolve("health.l3_llm_tool", now=T0)
        summary = store.summary()
        assert summary["resolved"] == 1
        assert summary["resolved_unacked"] == 1

    def test_确认后不再计入(self, store):
        alarm = _raise(store)
        store.resolve("health.l3_llm_tool", now=T0)
        store.ack(alarm.id, by="owner")
        assert store.summary()["resolved_unacked"] == 0

    def test_unacked_only_只回未确认(self, store):
        a = _raise(store)
        _raise(store, key="unit.skills", kind="unit", title="技能异常")
        store.resolve("health.l3_llm_tool", now=T0)
        store.resolve("unit.skills", now=T0)
        store.ack(a.id, by="owner")

        rows = store.list(state=STATE_RESOLVED, unacked_only=True)
        assert [r["key"] for r in rows] == ["unit.skills"]


class TestRaiseAndDedupe:
    def test_首次上报生成_firing_且未读(self, store):
        alarm = _raise(store)
        assert alarm.state == STATE_FIRING
        assert alarm.count == 1
        assert alarm.read is False
        assert alarm.id >= 1

    def test_同_key_重复只累计不新增行(self, store):
        _raise(store)
        again = store.raise_alarm("health.l3_llm_tool", kind="health", severity="warning",
                                  title="L3 降级", detail="err 30%",
                                  now=T0 + timedelta(minutes=5))
        assert again.id == 1, "同一个 key 不许长出新 id（否则同一问题会刷屏）"
        assert again.count == 2
        assert again.severity == "warning", "最新一轮的严重度/描述要覆盖（读数更新）"
        assert again.last_seen > again.first_seen

    def test_不同_key_各自一条(self, store):
        _raise(store)
        _raise(store, key="unit.skills", kind="unit", title="技能异常")
        assert store.count() == 2


class TestResolve:
    def test_恢复置_resolved_并保留记录(self, store):
        _raise(store)
        store.mark_read(1)
        resolved = store.resolve("health.l3_llm_tool", now=T0 + timedelta(minutes=1))
        assert resolved is not None and resolved.state == STATE_RESOLVED
        assert resolved.resolved_at, "恢复时间必须记下来（否则无法回答'什么时候好的'）"
        assert resolved.read is False, "恢复本身也要被看到"
        assert store.count() == 1, "恢复不删除记录"

    def test_未在_firing_的恢复是空操作(self, store):
        assert store.resolve("nope") is None

    def test_再次出现会_reopen_并重新未读(self, store):
        _raise(store)
        store.resolve("health.l3_llm_tool", now=T0 + timedelta(minutes=1))
        store.mark_all_read()
        reopened = store.raise_alarm("health.l3_llm_tool", kind="health", severity="critical",
                                     title="L3 降级", now=T0 + timedelta(minutes=30))
        assert reopened.state == STATE_FIRING
        assert reopened.resolved_at == ""
        assert reopened.read is False
        assert reopened.count == 2, "count 是累计发生次数，不是连续次数"


class TestReconcile:
    def test_只上报候选并把消失的恢复(self, store):
        _raise(store)
        stats = store.reconcile(
            {"unit.skills": {"kind": "unit", "severity": "warning", "title": "技能异常"}},
            now=T0 + timedelta(minutes=2),
        )
        assert stats == {"raised": 1, "resolved": 1}
        states = {a["key"]: a["state"] for a in store.list()}
        assert states["health.l3_llm_tool"] == STATE_RESOLVED
        assert states["unit.skills"] == STATE_FIRING

    def test_keep_里的告警不被恢复(self, store):
        """数据源不可用时不能把旧告警判成恢复（那等于伪造"已恢复"）。"""
        _raise(store)
        stats = store.reconcile({}, now=T0 + timedelta(minutes=2),
                                keep={"health.l3_llm_tool"})
        assert stats["resolved"] == 0
        assert store.list()[0]["state"] == STATE_FIRING


class TestReadAckSilence:
    def test_已读与未读计数(self, store):
        _raise(store)
        assert store.unread_count() == 1
        store.mark_read(1)
        assert store.unread_count() == 0
        assert store.get(1).read is True

    def test_全部已读返回更新条数(self, store):
        _raise(store)
        _raise(store, key="unit.skills", kind="unit", title="技能异常")
        assert store.mark_all_read() == 2
        assert store.mark_all_read() == 0

    def test_确认记录_谁在何时(self, store):
        _raise(store)
        assert store.ack(1, by="owner", now=T0 + timedelta(minutes=3)) is True
        alarm = store.get(1)
        assert alarm.acked is True and alarm.acked_by == "owner" and alarm.acked_at
        assert alarm.read is True

    def test_静默期满重新计入未读(self, store):
        _raise(store)
        until = store.silence(1, minutes=30, now=T0)
        assert until
        assert store.unread_count(now=T0) == 0, "静默期内不催"
        assert store.unread_count(now=T0 + timedelta(minutes=31)) == 1, "到期自动恢复未读"
        assert store.get(1).state == STATE_FIRING, "静默不改状态"

    def test_静默时长被夹到上限(self, store):
        _raise(store)
        until = store.silence(1, minutes=10 ** 6, now=T0)
        expected = (T0 + timedelta(minutes=MAX_SILENCE_MINUTES)).isoformat(timespec="seconds")
        assert until == expected

    def test_非法时长回退默认(self, store):
        _raise(store)
        until = store.silence(1, minutes="abc", now=T0)
        expected = (T0 + timedelta(minutes=DEFAULT_SILENCE_MINUTES)).isoformat(timespec="seconds")
        assert until == expected

    def test_不存在的_id_返回_False(self, store):
        assert store.mark_read(999) is False
        assert store.ack(999) is False
        assert store.silence(999) is None


class TestPersistence:
    def test_新实例读回同一份当前态与自增_id(self, tmp_path: Path):
        path, events = tmp_path / "state.json", tmp_path / "events.jsonl"
        first = AlarmStore(path, events)
        _raise(first)
        first.resolve("health.l3_llm_tool", now=T0 + timedelta(minutes=1))

        second = AlarmStore(path, events)
        rows = second.list()
        assert len(rows) == 1 and rows[0]["state"] == STATE_RESOLVED
        new_alarm = second.raise_alarm("unit.skills", kind="unit", severity="warning",
                                      title="技能异常", now=T0 + timedelta(minutes=5))
        assert new_alarm.id == 2, "id 必须接续（否则前端已读状态会串号）"

    def test_事件台账只追加不改写(self, tmp_path: Path):
        path, events = tmp_path / "state.json", tmp_path / "events.jsonl"
        store = AlarmStore(path, events)
        _raise(store)
        store.resolve("health.l3_llm_tool", now=T0 + timedelta(minutes=1))
        lines = [json.loads(x) for x in events.read_text(encoding="utf-8").splitlines() if x]
        assert [x["event"] for x in lines] == ["firing", "resolved"]
        assert all(x["key"] == "health.l3_llm_tool" for x in lines)

    def test_坏文件不抛异常(self, tmp_path: Path):
        path = tmp_path / "state.json"
        path.write_text("{not json", encoding="utf-8")
        store = AlarmStore(path, tmp_path / "events.jsonl")
        assert store.list() == []
        _raise(store)
        assert store.count() == 1


class TestSummary:
    def test_概览区分_firing_与_resolved(self, store):
        _raise(store)
        _raise(store, key="unit.skills", kind="unit", severity="warning", title="技能异常")
        store.resolve("unit.skills", now=T0 + timedelta(minutes=1))
        summary = store.summary()
        assert summary["total"] == 2
        assert summary["firing"] == 1 and summary["resolved"] == 1
        assert summary["firing_by_severity"]["critical"] == 1
        assert summary["unread"] == 2

class TestConsistency:
    """2026-11-13：state 与事件流一致性（"绿色已恢复"可信度的前提）。

    实测过的漂移：verify.channel_probe 的 state=resolved，而 events.jsonl 最后一条是 reopen。
    根因是"先写事件、再写 state"，state 落盘失败时内存态比磁盘新。
    """

    def test_新台账一致(self, store):
        _raise(store)
        assert store.consistency()["drift"] == 0

    def test_人为制造漂移能被发现(self, store):
        _raise(store)
        store.resolve("health.l3_llm_tool", now=T0 + timedelta(minutes=1))
        # 补一条 reopen 事件（模拟"事件写了、state 没落盘"的现场）
        with open(store._events_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "reopen", "key": "health.l3_llm_tool",
                                     "state": STATE_FIRING, "at": "2026-11-11T01:02:00+00:00"},
                                    ensure_ascii=False) + "\n")

        report = store.consistency()
        assert report["drift"] == 1
        assert report["drifts"][0]["state"] == STATE_RESOLVED
        assert report["drifts"][0]["last_event"] == "reopen"

    def test_sync_events_补齐后归零(self, store):
        _raise(store)
        store.resolve("health.l3_llm_tool", now=T0 + timedelta(minutes=1))
        with open(store._events_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "reopen", "key": "health.l3_llm_tool",
                                     "state": STATE_FIRING, "at": "2026-11-11T01:02:00+00:00"},
                                    ensure_ascii=False) + "\n")

        assert store.sync_events(["health.l3_llm_tool"]) == 1
        assert store.consistency()["drift"] == 0, "补一条 state_sync 事件后事件流应追上当前态"

    def test_落盘失败置_dirty_且_flush_能补写(self, store, monkeypatch):
        _raise(store)
        real_replace = __import__("os").replace

        def boom(src, dst):
            raise OSError("disk busy")

        monkeypatch.setattr("agent.alarms.store.os.replace", boom)
        assert store._save() is False
        assert store.dirty is True

        monkeypatch.setattr("agent.alarms.store.os.replace", real_replace)
        assert store.flush() is True
        assert store.dirty is False

    def test_consistency_读不到事件文件也不抛(self, store):
        store._events_path.unlink(missing_ok=True)
        report = store.consistency()
        assert report["drift"] == 0 and report["events_seen"] == 0
