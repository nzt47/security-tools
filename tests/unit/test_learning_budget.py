"""TASK-03 学习成本预算护栏 — with_budget 熔断测试

覆盖：默认 warn_only（不变式）、单次上限拒绝、日预算耗尽熔断 + OPEN、
warn_only 不拦截不熔断、cooldown 后半开探测恢复、正文异常释放预留不熔断、
spend() 实际消耗、配置三层优先级（env 覆盖）、模块级单例懒加载。

【不易】不修改 rate_limiter/circuit_breaker 语义，全部经其现有公开 API 组装。
"""

import time

import pytest

from agent.circuit_breaker import CircuitBreaker
from agent.learning_budget import (
    LearningBudget,
    LearningBudgetExceeded,
    _load_budget_config,
    get_learning_budget,
    reset_learning_budget,
)


def _make_budget(
    mode="enforce",
    max_single=0,
    max_daily=100,
    cooldown=60.0,
) -> LearningBudget:
    """构造隔离实例：注入独立熔断器，避免全局注册表跨用例污染"""
    breaker = CircuitBreaker(
        name="budget_test",
        failure_threshold=1.0,
        min_calls=1,
        cooldown_seconds=cooldown,
        half_open_max_calls=1,
        half_open_success_threshold=1,
    )
    return LearningBudget(
        config={
            "mode": mode,
            "max_single_action_tokens": max_single,
            "max_daily_tokens": max_daily,
        },
        breaker=breaker,
    )


def test_default_mode_is_warn_only():
    """不变式：cost_limits 强制化默认宽松（warn_only），防止上线即改行为"""
    lb = LearningBudget(config={})
    assert lb.mode == "warn_only"
    assert lb.max_single_action_tokens == 4000
    assert lb.max_daily_tokens == 1_000_000
    assert lb.recovery_seconds == 3600


def test_invalid_mode_falls_back_to_warn_only():
    lb = LearningBudget(config={"mode": "aggressive"})
    assert lb.mode == "warn_only"


def test_enforce_single_action_limit_rejects():
    """enforce：超单次上限 → 立即拒绝并 raise LearningBudgetExceeded"""
    lb = LearningBudget(config={"mode": "enforce", "max_single_action_tokens": 100})
    with pytest.raises(LearningBudgetExceeded) as ei:
        with lb.with_budget("offline_evolve", estimated_tokens=150):
            raise AssertionError("不应进入正文")
    assert ei.value.reason == "single_action_limit"
    assert ei.value.action_name == "offline_evolve"
    assert ei.value.tokens == 150
    assert ei.value.limit == 100
    assert ei.value.error_code == "LEARNING_BUDGET_EXCEEDED"


def test_enforce_daily_exhausted_trips_breaker():
    """enforce：日预算耗尽 → 熔断器 OPEN + tripped，后续动作被拦截"""
    lb = _make_budget(max_daily=100)
    with lb.with_budget("a", estimated_tokens=40):
        pass
    with lb.with_budget("b", estimated_tokens=40):
        pass
    with pytest.raises(LearningBudgetExceeded) as ei:
        with lb.with_budget("c", estimated_tokens=40):
            raise AssertionError("不应进入正文")
    assert ei.value.reason == "daily_exhausted"
    status = lb.get_status()
    assert status["breaker"]["state"] == "open"
    assert status["tripped"] is True


def test_warn_only_does_not_intercept_or_trip():
    """warn_only：超单次上限/日预算仅记录，正文照常执行，不熔断"""
    lb = _make_budget(mode="warn_only", max_single=100, max_daily=100)
    executed = []

    with lb.with_budget("evolve", estimated_tokens=500):  # 超单次上限
        executed.append(1)
    with lb.with_budget("evolve2", estimated_tokens=200):  # 仍超上限，不熔断
        executed.append(2)

    assert executed == [1, 2]
    status = lb.get_status()
    assert status["breaker"]["state"] == "closed"
    assert status["tripped"] is False


def test_recovery_half_open_probe_allows_after_cooldown():
    """enforce：熔断 cooldown 到期 → 半开探测成功 → 恢复 CLOSED 放行"""
    lb = _make_budget(max_daily=100, cooldown=0.05)
    with lb.with_budget("a", estimated_tokens=60):
        pass
    with pytest.raises(LearningBudgetExceeded) as ei_b:
        with lb.with_budget("b", estimated_tokens=60):  # 120 > 100 → 日预算耗尽熔断
            raise AssertionError("不应进入正文")
    assert ei_b.value.reason == "daily_exhausted"
    with pytest.raises(LearningBudgetExceeded) as ei:
        with lb.with_budget("c", estimated_tokens=10):  # 熔断期内拦截
            raise AssertionError("不应进入正文")
    assert ei.value.reason == "circuit_open"
    assert lb.get_status()["breaker"]["state"] == "open"

    time.sleep(0.1)  # 等待 cooldown 到期 → HALF_OPEN 探测
    with lb.with_budget("d", estimated_tokens=10):  # 半开探测成功 → CLOSED
        pass
    assert lb.get_status()["breaker"]["state"] == "closed"


def test_body_exception_releases_reserved_no_trip():
    """正文异常 → 释放已预留日预算 token，不触发熔断，后续可放行"""
    lb = _make_budget(max_daily=100)
    with pytest.raises(RuntimeError, match="boom"):
        with lb.with_budget("evolve", estimated_tokens=80):
            raise RuntimeError("boom")

    # 预留 80 已释放 → 后续 20 仍可放行，熔断器保持 CLOSED
    with lb.with_budget("next", estimated_tokens=20):
        pass
    assert lb.get_status()["breaker"]["state"] == "closed"


def test_spend_records_and_trips_when_exhausted():
    """spend：实际消耗计入日预算；超限返回 False 并熔断（enforce）"""
    lb = _make_budget(max_daily=100)
    assert lb.spend(60) is True
    assert lb.spend(60) is False  # 60+60 > 100 → 熔断
    assert lb.get_status()["breaker"]["state"] == "open"


def test_config_priority_env_overrides(monkeypatch):
    """配置优先级：环境变量 > config.yaml > models.yaml > 默认值"""
    monkeypatch.setenv("LEARNING_BUDGET_MODE", "enforce")
    monkeypatch.setenv("LEARNING_BUDGET_MAX_SINGLE_ACTION_TOKENS", "500")
    monkeypatch.setenv("LEARNING_BUDGET_MAX_DAILY_TOKENS", "123456")
    monkeypatch.setenv("LEARNING_BUDGET_RECOVERY_SECONDS", "30")
    cfg = _load_budget_config()
    assert cfg["mode"] == "enforce"
    assert cfg["max_single_action_tokens"] == 500
    assert cfg["max_daily_tokens"] == 123456
    assert cfg["recovery_seconds"] == 30


def test_module_singleton_lazy_singleton():
    """get_learning_budget：懒加载单例，reset 后重建"""
    reset_learning_budget()
    lb1 = get_learning_budget()
    lb2 = get_learning_budget()
    assert lb1 is lb2
    assert lb1.mode in ("warn_only", "enforce")
    assert lb1.get_status()["max_daily_tokens"] >= 1

    reset_learning_budget()
    lb3 = get_learning_budget()
    assert lb3 is not lb1
    reset_learning_budget()
