# -*- coding: utf-8 -*-
"""调度任务的「零 token」声明口径（全仓）。

用户 2026-10-29：引擎页里「未标注」的任务要全部标注，而且**其它任务也要跟进** ——
新加的任务不许再漏；把 dry_run 关了会烧 token 的，必须写明。

锁四件事：
① `list_tasks()` 如实带出三态 + 判据说明 + 条件性（UI 直接消费，不再有「未标注」）；
② **全仓**所有 `add_cron_task / add_interval_task` 调用都必须显式传 `zero_token`
   （不是白名单式的几个文件 —— 新增注册点漏声明会直接红）；
③ 条件性的（`zero_token_conditional=True`）必须写 `zero_token_note`，说明"关掉 dry_run 会怎样"；
④ 没声明就是 None（UI 显示「未声明」），不能被默认成 True/False。
"""
from __future__ import annotations

import ast
from pathlib import Path

from agent.task_scheduler import TaskScheduler

ROOT = Path(__file__).resolve().parents[2]
_REG_ATTRS = ("add_cron_task", "add_interval_task")


def _registration_calls():
    """全仓注册点：(路径, 调用节点)。"""
    files = [ROOT / "app_server.py", *sorted((ROOT / "agent").rglob("*.py"))]
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in _REG_ATTRS:
                yield path, node


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


def test_every_registration_declares_zero_token():
    """全仓守卫：任何一个注册点没写 zero_token，这条就红。"""
    missing = []
    for path, call in _registration_calls():
        kws = {k.arg for k in call.keywords if k.arg}
        if "zero_token" not in kws:
            missing.append("%s:%d" % (path.relative_to(ROOT), call.lineno))
    assert missing == [], "这些注册点没声明 zero_token：" + "、".join(missing)


def test_conditional_tasks_explain_what_disabling_dry_run_does():
    """条件性任务必须写明"关掉 dry_run 会怎样"（用户明确要求）。"""
    bad = []
    for path, call in _registration_calls():
        kws = {k.arg: k.value for k in call.keywords if k.arg}
        flag = kws.get("zero_token_conditional")
        if not (isinstance(flag, ast.Constant) and flag.value is True):
            continue
        note = kws.get("zero_token_note")
        text = note.value if isinstance(note, ast.Constant) else ""
        if not str(text or "").strip() or "dry_run" not in str(text):
            bad.append("%s:%d" % (path.relative_to(ROOT), call.lineno))
    assert bad == [], "条件性任务缺少「dry_run 关掉会怎样」的说明：" + "、".join(bad)
