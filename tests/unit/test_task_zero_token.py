# -*- coding: utf-8 -*-
"""调度任务的「零 token」声明口径。

用户 2026-10-29：引擎页里「未标注」的任务要全部标注（不能让人靠猜）。
这里锁三件事：
① `list_tasks()` 如实带出三态 + 判据说明 + 条件性（UI 直接消费）；
② 本轮补声明的六个注册点**源码级**都显式传了 `zero_token`（新任务漏声明时这条会红）；
③ 条件性的任务必须同时给 `zero_token_conditional=True` —— 否则「零 token」会被读成永远成立。
"""
from __future__ import annotations

import ast
from pathlib import Path

from agent.task_scheduler import TaskScheduler

ROOT = Path(__file__).resolve().parents[2]

#: 本轮补声明/已有的注册点（都必须是显式声明）
DECLARED_SITES = (
    "app_server.py",
    "agent/skills_mgmt/feedback_agent.py",
    "agent/skills_mgmt/evolution_scheduler.py",
    "agent/skills_mgmt/lifecycle.py",
    "agent/monitoring/slo_report_scheduler.py",
    "agent/subagent/peers.py",
)


def test_list_tasks_serializes_zero_token_metadata():
    sched = TaskScheduler()
    sched.add_interval_task("demo", func=lambda: None, interval_seconds=60,
                            zero_token=True, owner="x",
                            zero_token_note="不调 LLM",
                            zero_token_conditional=True)
    row = [t for t in sched.list_tasks() if t["name"] == "demo"][0]
    assert row["zero_token"] is True
    assert row["zero_token_note"] == "不调 LLM"
    assert row["zero_token_conditional"] is True


def test_undeclared_task_stays_none():
    """没声明就是 None（UI 显示「未声明」），**不能**被默认成 True/False。"""
    sched = TaskScheduler()
    sched.add_interval_task("bare", func=lambda: None, interval_seconds=60)
    row = [t for t in sched.list_tasks() if t["name"] == "bare"][0]
    assert row["zero_token"] is None
    assert row["zero_token_note"] == ""
    assert row["zero_token_conditional"] is False


def _add_task_calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in ("add_cron_task", "add_interval_task")]


def test_declared_registration_sites_pass_zero_token():
    missing = []
    for rel in DECLARED_SITES:
        calls = _add_task_calls(ROOT / rel)
        assert calls, rel + " 里没找到 add_*_task 调用（守卫失效？）"
        for call in calls:
            kws = {k.arg for k in call.keywords if k.arg}
            if "zero_token" not in kws:
                missing.append("%s:%d" % (rel, call.lineno))
    assert missing == [], "这些注册点没声明 zero_token：" + "、".join(missing)


def test_conditional_sites_mark_conditional():
    """带 dry_run 开关的学习任务：条件性必须显式标记，否则 UI 会读成永远零 token。"""
    for rel in ("agent/skills_mgmt/feedback_agent.py",
                "agent/skills_mgmt/evolution_scheduler.py"):
        for call in _add_task_calls(ROOT / rel):
            kws = {k.arg: k.value for k in call.keywords if k.arg}
            assert "zero_token_conditional" in kws, rel + " 缺少 zero_token_conditional"
            note = kws.get("zero_token_note")
            assert isinstance(note, ast.Constant) and str(note.value or "").strip(), \
                rel + " 缺少判据说明（zero_token_note）"
