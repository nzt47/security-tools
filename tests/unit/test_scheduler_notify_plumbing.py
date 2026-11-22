"""系统命令任务的成功通知字段（P1 横切）不丢。"""
from __future__ import annotations

import json

from agent.tools import task_tools


class _FakeScheduler:
    running = True

    def __init__(self):
        self.calls = []

    def add_command_task(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_create_scheduled_task_keeps_notify_flag(monkeypatch, tmp_path):
    store = tmp_path / "scheduled_tasks.json"
    monkeypatch.setattr(task_tools, "SCHEDULED_TASKS_FILE", str(store))
    fake = _FakeScheduler()
    import agent.task_scheduler as ts
    monkeypatch.setattr(ts, "get_scheduler", lambda: fake)

    result = task_tools.create_scheduled_task(
        "demo", "echo hi", 60, notify_on_complete=True)
    assert result["ok"] is True
    assert result["task"]["notify_on_complete"] is True
    assert fake.calls and fake.calls[0][1]["notify_on_complete"] is True
    saved = json.loads(store.read_text(encoding="utf-8"))
    assert saved["tasks"][0]["notify_on_complete"] is True

    # 重启后从 JSON 恢复也要保留该字段（否则“成功通知”只在当次进程有效）。
    from agent.task_scheduler import TaskScheduler
    ts = TaskScheduler()
    assert ts.load_from_json(str(store)) == 1
    assert ts.tasks[0]["notify_on_complete"] is True
