# -*- coding: utf-8 -*-
"""消息中心数据源（2026-11-11 起 = 报警台账投影）的行为测试。

【为什么单测这一层】`plugins/admin_api.py` 的 `/api/notification/*` 是管理后台消息中心的数据源：
它从"20 条硬编码演示通知"改成"报警台账投影"之后，**字段映射**就是唯一的接缝
（id / type / title / content / read / createdAt + 新增 severity/state）。
端点本身有 `_require_admin`，用真令牌打它要绕过登录态；而映射错不错与鉴权无关，
所以这里直接对着投影函数断言（守卫鉴权的用例另有 test_admin_api_guard）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.alarms.store import AlarmStore, reset_store


@pytest.fixture
def store(tmp_path: Path):
    s = AlarmStore(tmp_path / "state.json", tmp_path / "events.jsonl")
    reset_store(s)
    yield s
    reset_store(None)


def _rows():
    from plugins.admin_api import _notification_rows
    return _notification_rows()


class TestNotificationProjection:
    def test_假通知已下线_空台账就是空列表(self, store):
        assert _rows() == [], "没有报警时消息中心应为空（不再是 20 条演示数据）"

    def test_报警映射成通知行(self, store):
        alarm = store.raise_alarm(
            "health.l3_llm_tool", kind="health", severity="critical",
            title="L3 LLM/工具 降级（30 分）", detail="llm_calls=100 errors=20",
            source="/api/health/probe-trend",
        )
        row = _rows()[0]
        assert row["id"] == alarm.id
        assert row["type"] == "alert", "消息中心只承载报警类；其它类型不再编造"
        assert row["severity"] == "critical"
        assert row["title"] == "L3 LLM/工具 降级（30 分）"
        assert row["content"] == "llm_calls=100 errors=20"
        assert row["read"] is False
        assert row["createdAt"], "createdAt 不能为空（页面按它排序/显示）"
        assert row["state"] == "firing"

    def test_已读与恢复态一并透出(self, store):
        alarm = store.raise_alarm("unit.skills", kind="unit", severity="warning",
                                  title="技能异常", detail="近 7 日 0 次执行")
        store.resolve("unit.skills")
        row = _rows()[0]
        assert row["state"] == "resolved"
        store.mark_read(alarm.id)
        assert _rows()[0]["read"] is True

    def test_未读数与台账同源(self, store):
        from plugins.admin_api import admin_notification_unread  # noqa: F401  (导入即证明接线在)

        store.raise_alarm("audit.chain", kind="audit", severity="critical", title="审计链不一致")
        assert store.unread_count() == 1
