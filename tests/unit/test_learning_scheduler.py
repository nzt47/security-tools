"""TASK-05 · 学习类定时任务统一注册测试

覆盖:
    1. 三个任务全关（enabled=false）→ register 返回 3 个 disabled，不注册任务
    2. 三个任务全开 → register 注册到 task_scheduler（反馈建议执行/周期进化/生命周期检查）
    3. unregister 按固定任务名全部注销

守【不易】: 只测统一注册入口的收口行为；各模块内部调度语义由
test_feedback_agent / test_evolver_schedule / test_skill_lifecycle 各自覆盖。
"""

from __future__ import annotations

import pytest

from agent.skills_mgmt.learning_scheduler import (
    register_learning_schedulers,
    unregister_learning_schedulers,
)

_MODULES = (
    "agent.skills_mgmt.feedback_agent",
    "agent.skills_mgmt.evolution_scheduler",
    "agent.skills_mgmt.lifecycle",
)

TASK_NAMES = {"反馈建议执行", "周期进化", "生命周期检查"}


@pytest.fixture(autouse=True)
def _cleanup():
    unregister_learning_schedulers()
    yield
    unregister_learning_schedulers()


def _scheduler_names() -> set:
    from agent.task_scheduler import get_scheduler
    return {t["name"] for t in get_scheduler().list_tasks()}


def _set_enabled(monkeypatch, value: bool) -> None:
    for mod in _MODULES:
        monkeypatch.setattr(f"{mod}._enabled", lambda: value)


def test_register_all_disabled(monkeypatch):
    """全关：返回 3 个 disabled，task_scheduler 无任务名。"""
    _set_enabled(monkeypatch, False)

    results = register_learning_schedulers()

    assert set(results) == {"feedback_agent", "evolution", "lifecycle"}
    assert all(r["status"] == "disabled" for r in results.values())
    assert not (TASK_NAMES & _scheduler_names())


def test_register_all_enabled(monkeypatch):
    """全开：3 个任务注册到 task_scheduler。"""
    _set_enabled(monkeypatch, True)

    try:
        results = register_learning_schedulers()

        assert all(r["status"] == "scheduled" for r in results.values())
        assert TASK_NAMES <= _scheduler_names()
    finally:
        unregister_learning_schedulers()


def test_unregister_removes_all(monkeypatch):
    """注册后注销：三个任务全部移除。"""
    _set_enabled(monkeypatch, True)
    register_learning_schedulers()
    assert TASK_NAMES <= _scheduler_names()

    results = unregister_learning_schedulers()

    assert all(results.values())  # 三个都注销成功
    assert not (TASK_NAMES & _scheduler_names())
