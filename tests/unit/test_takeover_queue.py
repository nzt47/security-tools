#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务 7：人工接管队列单元测试

覆盖 takeover_queue.py 状态机与超时策略：
- 状态流转：open → assigned → resolved / open → resolved / open → timed_out
- 非法流转拒绝（resolved 后不可再流转、未知 ID、缺 owner/resolution）
- 超时二次通知（mock 时钟 + sweep）
- 查询接口 list_takeovers（按状态过滤、倒序）

验收标准对应：
- #5  接管队列 open 超过 timeout 转 timed_out 且二次通知
"""
from agent.human_in_the_loop.takeover_queue import (
    TakeoverQueue,
    TakeoverStatus,
)


class FakeClock:
    """可推进的 mock 时钟"""

    def __init__(self, start: float = 1000.0):
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


def _mk_alert(name: str = "high-cpu"):
    return {"name": name}


class TestTakeoverStateMachine:
    """状态机流转"""

    def test_open_to_assigned_to_resolved(self):
        """完整闭环：open → assigned → resolved"""
        q = TakeoverQueue(auto_sweep=False)
        rec = q.create_takeover(_mk_alert(), "需要人工介入", {"score": 99})
        assert rec.status == TakeoverStatus.OPEN

        assert q.assign(rec.takeover_id, "ops-team") is True
        stored = q.get(rec.takeover_id)
        assert stored.status == TakeoverStatus.ASSIGNED
        assert stored.owner == "ops-team"

        assert q.resolve(rec.takeover_id, "已重启节点并观察 10 分钟") is True
        stored = q.get(rec.takeover_id)
        assert stored.status == TakeoverStatus.RESOLVED
        assert stored.resolution == "已重启节点并观察 10 分钟"
        assert stored.resolved_at is not None

    def test_open_to_resolved(self):
        """open 直接 resolve 允许"""
        q = TakeoverQueue(auto_sweep=False)
        rec = q.create_takeover(_mk_alert(), "r")
        assert q.resolve(rec.takeover_id, "误报，已确认") is True
        assert q.get(rec.takeover_id).status == TakeoverStatus.RESOLVED

    def test_resolved_cannot_reassign(self):
        """resolved 后不可再流转（assign/resolve 均拒绝）"""
        q = TakeoverQueue(auto_sweep=False)
        rec = q.create_takeover(_mk_alert(), "r")
        q.resolve(rec.takeover_id, "已处置")
        assert q.assign(rec.takeover_id, "ops") is False
        assert q.resolve(rec.takeover_id, "再处置") is False

    def test_unknown_id_rejected(self):
        """未知 takeover_id 流转拒绝"""
        q = TakeoverQueue(auto_sweep=False)
        assert q.assign("nope", "ops") is False
        assert q.resolve("nope", "x") is False

    def test_assign_requires_owner(self):
        """assign 缺 owner 拒绝"""
        q = TakeoverQueue(auto_sweep=False)
        rec = q.create_takeover(_mk_alert(), "r")
        assert q.assign(rec.takeover_id, "") is False
        assert q.get(rec.takeover_id).status == TakeoverStatus.OPEN

    def test_resolve_requires_resolution(self):
        """resolve 缺 resolution 拒绝"""
        q = TakeoverQueue(auto_sweep=False)
        rec = q.create_takeover(_mk_alert(), "r")
        assert q.resolve(rec.takeover_id, "") is False
        assert q.get(rec.takeover_id).status == TakeoverStatus.OPEN

    def test_alert_object_supported(self):
        """alert 支持对象（含 .name）"""
        class _Alert:
            name = "obj-alert"
        q = TakeoverQueue(auto_sweep=False)
        rec = q.create_takeover(_Alert(), "r")
        assert rec.alert_name == "obj-alert"


class TestTakeoverTimeout:
    """超时策略（验收 #5，mock 时钟）"""

    def test_open_timeout_to_timed_out_with_second_notify(self):
        """open 超过 timeout → timed_out 且二次通知"""
        clock = FakeClock()
        events = []

        def notifier(record, event):
            events.append((record.alert_name, event))

        q = TakeoverQueue(
            takeover_timeout=30.0,
            notifier=notifier,
            clock=clock.now,
            auto_sweep=False,
        )
        rec = q.create_takeover(_mk_alert("x"), "需要接管")

        # 未到超时：sweep 不动
        clock.advance(29)
        assert q.sweep() == []
        assert q.get(rec.takeover_id).status == TakeoverStatus.OPEN

        # 超过超时：sweep 转 timed_out
        clock.advance(2)
        swept = q.sweep()
        assert swept == [rec.takeover_id]
        stored = q.get(rec.takeover_id)
        assert stored.status == TakeoverStatus.TIMED_OUT
        assert stored.timed_out_at is not None

        # 二次通知：created（入队）+ timed_out（超时）
        assert events[0] == ("x", "created")
        assert events[-1] == ("x", "timed_out")
        assert len(events) == 2

    def test_assigned_can_timeout(self):
        """assigned 后超时同样转 timed_out"""
        clock = FakeClock()
        q = TakeoverQueue(
            takeover_timeout=30.0,
            clock=clock.now,
            auto_sweep=False,
        )
        rec = q.create_takeover(_mk_alert(), "r")
        q.assign(rec.takeover_id, "ops")
        clock.advance(31)
        assert q.sweep() == [rec.takeover_id]
        assert q.get(rec.takeover_id).status == TakeoverStatus.TIMED_OUT

    def test_timed_out_cannot_resolve(self):
        """timed_out 条目 resolve 拒绝（不可跳越处置）"""
        clock = FakeClock()
        q = TakeoverQueue(takeover_timeout=1.0, clock=clock.now, auto_sweep=False)
        rec = q.create_takeover(_mk_alert(), "r")
        clock.advance(2)
        q.sweep()
        assert q.resolve(rec.takeover_id, "迟到处置") is False

    def test_timed_out_notifier_exception_is_safe(self):
        """通知回调异常不影响状态流转"""
        clock = FakeClock()
        q = TakeoverQueue(
            takeover_timeout=1.0,
            notifier=lambda record, event: (_ for _ in ()).throw(RuntimeError("notify fail")),
            clock=clock.now,
            auto_sweep=False,
        )
        rec = q.create_takeover(_mk_alert(), "r")
        clock.advance(2)
        assert q.sweep() == [rec.takeover_id]
        assert q.get(rec.takeover_id).status == TakeoverStatus.TIMED_OUT


class TestTakeoverQuery:
    """查询接口"""

    def test_list_takeovers_filter_by_status(self):
        q = TakeoverQueue(auto_sweep=False)
        r1 = q.create_takeover(_mk_alert("a"), "r1")
        r2 = q.create_takeover(_mk_alert("b"), "r2")
        q.resolve(r1.takeover_id, "done")

        open_items = q.list_takeovers(status=TakeoverStatus.OPEN)
        assert len(open_items) == 1
        assert open_items[0]["takeover_id"] == r2.takeover_id

        resolved_items = q.list_takeovers(status=TakeoverStatus.RESOLVED)
        assert len(resolved_items) == 1
        assert resolved_items[0]["resolution"] == "done"

    def test_list_takeovers_newest_first(self):
        """created_at 递增时钟下按最新优先排序"""
        clock = FakeClock()
        q = TakeoverQueue(clock=clock.now, auto_sweep=False)
        r1 = q.create_takeover(_mk_alert("a"), "r1")
        clock.advance(1)
        r2 = q.create_takeover(_mk_alert("b"), "r2")
        items = q.list_takeovers()
        assert [i["takeover_id"] for i in items] == [r2.takeover_id, r1.takeover_id]

    def test_list_takeovers_limit(self):
        q = TakeoverQueue(auto_sweep=False)
        for i in range(5):
            q.create_takeover(_mk_alert(f"a{i}"), f"r{i}")
        assert len(q.list_takeovers(limit=3)) == 3

    def test_record_to_dict(self):
        q = TakeoverQueue(auto_sweep=False)
        rec = q.create_takeover(_mk_alert("a"), "r", {"k": "v"})
        d = rec.to_dict()
        assert d["alert_name"] == "a"
        assert d["status"] == "open"
        assert d["evidence"] == {"k": "v"}
        assert d["owner"] is None
