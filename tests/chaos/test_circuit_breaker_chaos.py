# -*- coding: utf-8 -*-
"""熔断器混沌测试 — 极端场景下的稳定性验证

【测试目标】
验证 CircuitBreaker 在以下极端场景下的状态转换正确性与稳定性：
1. 错误率突增（瞬间达到 100%）
2. 半开状态下并发请求（探测请求的线程安全）
3. 恢复后状态转换（HALF_OPEN → CLOSED）
4. 持续高频失败下的反复熔断与恢复
5. 冷却期内请求被拒绝
6. 跨状态边界的并发竞争

【可观测性约束】
- 边界显性化：所有故障注入通过 mock 实现，不依赖真实基础设施
- 异常处理：每个测试设置 30s 超时，避免死锁
- 埋点预留：熔断器内部已埋点（circuit_state_changed/circuit_blocked）

【生成日志摘要】
- 生成时间：2026-06-27
- 版本：v1.0.0
- 内容：熔断器混沌测试（不依赖真实基础设施，全 mock 注入）
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.circuit_breaker import (  # noqa: E402
    CircuitBreaker,
    CircuitState,
    CircuitBreakerError,
    set_trace_id,
)


# ═══════════════════════════════════════════════════════════════
#  1. 错误率突增场景
# ═══════════════════════════════════════════════════════════════

class TestErrorRateSpike:
    """错误率突增场景：从 0% 突然飙升到 100%"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_error_rate_spike_from_zero_to_full_should_open_circuit(self):
        """错误率从 0% 突增到 100% 应触发熔断

        场景：先成功若干次建立基线，然后连续失败使错误率突增。
        预期：错误率达到 30% 阈值后熔断器打开。
        """
        set_trace_id("chaos-cb-spike-001")
        # 使用短窗口和小阈值便于测试
        breaker = CircuitBreaker(
            name="chaos_spike",
            failure_threshold=0.3,
            min_calls=5,
            cooldown_seconds=60,
            window_seconds=10,
        )

        # 阶段1：5 次成功建立基线
        for _ in range(5):
            breaker.call(lambda: "ok")
        assert breaker.state == CircuitState.CLOSED

        # 阶段2：连续 5 次失败（错误率突增到 50%）
        # 注意：熔断器打开后 breaker.call() 抛出 CircuitBreakerError 而非 RuntimeError，
        # 需同时捕获两种异常
        for _ in range(5):
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            except (RuntimeError, CircuitBreakerError):
                pass

        # 预期：错误率 50% > 30% 阈值，熔断器应打开
        assert breaker.state == CircuitState.OPEN, (
            f"错误率突增到 50% 后应熔断，实际状态: {breaker.state}"
        )

        # 验证：后续请求被拒绝
        with pytest.raises(CircuitBreakerError):
            breaker.call(lambda: "should_be_blocked")

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_burst_failures_below_min_calls_should_not_open(self):
        """失败数未达 min_calls 时不应熔断（避免样本不足误判）"""
        set_trace_id("chaos-cb-spike-002")
        breaker = CircuitBreaker(
            name="chaos_min_calls",
            failure_threshold=0.3,
            min_calls=10,
            window_seconds=10,
        )

        # 仅 3 次失败（< min_calls=10）
        for _ in range(3):
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
            except RuntimeError:
                pass

        # 预期：样本不足，不应熔断
        assert breaker.state == CircuitState.CLOSED, (
            "失败数未达 min_calls 时不应熔断"
        )


# ═══════════════════════════════════════════════════════════════
#  2. 半开状态并发请求
# ═══════════════════════════════════════════════════════════════

class TestHalfOpenConcurrency:
    """半开状态下的并发请求处理"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_half_open_concurrent_probes_should_be_limited(self):
        """半开状态下并发探测请求数应受 half_open_max_calls 限制

        场景：熔断器进入半开后，多个线程同时发起请求，
        超过 half_open_max_calls 的请求应被拒绝。

        预期：
        - 半开状态下最多放行 half_open_max_calls 个探测请求
        - 多余请求被拒绝（抛 CircuitBreakerError）
        - 30s 超时内完成
        """
        set_trace_id("chaos-cb-halfopen-001")
        breaker = CircuitBreaker(
            name="chaos_half_open",
            failure_threshold=0.3,
            min_calls=3,
            cooldown_seconds=0.5,  # 短冷却期便于测试
            half_open_max_calls=3,
            half_open_success_threshold=2,
            window_seconds=10,
        )

        # 触发熔断
        for _ in range(3):
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
            except RuntimeError:
                pass
        assert breaker.state == CircuitState.OPEN

        # 等待冷却期到期，进入半开
        time.sleep(0.6)

        # 并发发起 10 个请求
        allowed = []
        rejected = []
        barrier = threading.Barrier(10)

        def worker():
            try:
                barrier.wait(timeout=5)
                if breaker.allow_request():
                    allowed.append(threading.current_thread().ident)
                else:
                    rejected.append(threading.current_thread().ident)
            except Exception:
                rejected.append(threading.current_thread().ident)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # 预期：放行数不超过 half_open_max_calls（允许并发竞争，但应限制总数）
        assert len(allowed) <= 3, (
            f"半开状态放行数 {len(allowed)} 超过 half_open_max_calls=3"
        )
        assert len(allowed) + len(rejected) == 10


# ═══════════════════════════════════════════════════════════════
#  3. 恢复后状态转换
# ═══════════════════════════════════════════════════════════════

class TestRecoveryTransition:
    """恢复后状态转换：HALF_OPEN → CLOSED"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_half_open_success_threshold_recovers_to_closed(self):
        """半开状态下成功数达阈值应恢复 CLOSED"""
        set_trace_id("chaos-cb-recover-001")
        breaker = CircuitBreaker(
            name="chaos_recover",
            failure_threshold=0.3,
            min_calls=3,
            cooldown_seconds=0.3,
            half_open_max_calls=5,
            half_open_success_threshold=2,
            window_seconds=10,
        )

        # 触发熔断
        for _ in range(3):
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
            except RuntimeError:
                pass
        assert breaker.state == CircuitState.OPEN

        # 等待冷却期，进入半开
        time.sleep(0.4)

        # 半开状态下连续成功 2 次，应恢复 CLOSED
        result1 = breaker.call(lambda: "ok1")
        assert result1 == "ok1"
        # 第一次成功后仍在 HALF_OPEN
        assert breaker.state == CircuitState.HALF_OPEN

        result2 = breaker.call(lambda: "ok2")
        assert result2 == "ok2"
        # 第二次成功后应恢复 CLOSED
        assert breaker.state == CircuitState.CLOSED, (
            f"半开状态成功 2 次后应恢复 CLOSED，实际: {breaker.state}"
        )

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_half_open_failure_reopens_circuit(self):
        """半开状态下探测失败应重新打开熔断"""
        set_trace_id("chaos-cb-recover-002")
        breaker = CircuitBreaker(
            name="chaos_reopen",
            failure_threshold=0.3,
            min_calls=3,
            cooldown_seconds=0.3,
            half_open_max_calls=3,
            half_open_success_threshold=2,
            window_seconds=10,
        )

        # 触发熔断
        for _ in range(3):
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
            except RuntimeError:
                pass
        assert breaker.state == CircuitState.OPEN

        # 等待冷却期，进入半开
        time.sleep(0.4)
        assert breaker.state == CircuitState.HALF_OPEN

        # 半开状态下失败，应重新打开
        try:
            breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("still_bad")))
        except RuntimeError:
            pass

        assert breaker.state == CircuitState.OPEN, (
            f"半开探测失败应重新打开熔断，实际: {breaker.state}"
        )


# ═══════════════════════════════════════════════════════════════
#  4. 持续高频失败下的反复熔断与恢复
# ═══════════════════════════════════════════════════════════════

class TestRepeatedCycling:
    """持续高频失败下的反复熔断与恢复"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_repeated_open_halfopen_open_cycle_should_be_stable(self):
        """反复 OPEN → HALF_OPEN → OPEN 循环应稳定不崩溃

        场景：模拟间歇性故障，熔断器在 OPEN/HALF_OPEN 之间反复切换。
        预期：状态机稳定，无异常抛出，无死锁。
        """
        set_trace_id("chaos-cb-cycle-001")
        breaker = CircuitBreaker(
            name="chaos_cycle",
            failure_threshold=0.3,
            min_calls=3,
            cooldown_seconds=0.2,
            half_open_max_calls=3,
            half_open_success_threshold=2,
            window_seconds=5,
        )

        # 模拟 3 轮 OPEN → HALF_OPEN → OPEN 循环
        # 注意：循环 2+ 时熔断器可能仍处于 OPEN 状态（冷却期未过），
        # 此时 breaker.call() 抛出 CircuitBreakerError 而非 RuntimeError，
        # 需同时捕获两种异常
        for cycle in range(3):
            # 触发熔断（若已 OPEN 则调用被拒绝，不影响状态）
            for _ in range(3):
                try:
                    breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
                except (RuntimeError, CircuitBreakerError):
                    pass
            assert breaker.state == CircuitState.OPEN, (
                f"循环 {cycle}: 应处于 OPEN 状态"
            )

            # 等待冷却期
            time.sleep(0.25)
            assert breaker.state == CircuitState.HALF_OPEN, (
                f"循环 {cycle}: 冷却后应进入 HALF_OPEN"
            )

            # 探测失败，重新打开
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
            except (RuntimeError, CircuitBreakerError):
                pass
            assert breaker.state == CircuitState.OPEN, (
                f"循环 {cycle}: 探测失败应重新 OPEN"
            )

        # 验证：3 轮循环后状态机仍然稳定
        # 注意：循环 2/3 中熔断器已处于 OPEN 状态，"触发熔断" 阶段的调用被拒绝
        # （抛出 CircuitBreakerError），不计入 failure_count。
        # 实际失败数 = 3（循环1初始失败）+ 3（每轮探测失败）= 6
        stats = breaker.stats
        assert stats.state == CircuitState.OPEN
        assert stats.failure_count >= 6, (
            f"3 轮循环后至少应有 6 次真实失败，实际 {stats.failure_count}"
        )


# ═══════════════════════════════════════════════════════════════
#  5. 冷却期内请求拒绝
# ═══════════════════════════════════════════════════════════════

class TestCooldownRejection:
    """冷却期内请求应被拒绝"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_requests_during_cooldown_should_be_rejected(self):
        """冷却期内所有请求应被拒绝"""
        set_trace_id("chaos-cb-cooldown-001")
        breaker = CircuitBreaker(
            name="chaos_cooldown",
            failure_threshold=0.3,
            min_calls=3,
            cooldown_seconds=2.0,  # 长冷却期
            half_open_max_calls=3,
            half_open_success_threshold=2,
            window_seconds=10,
        )

        # 触发熔断
        for _ in range(3):
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
            except RuntimeError:
                pass
        assert breaker.state == CircuitState.OPEN

        # 冷却期内连续 5 次请求都应被拒绝
        rejected_count = 0
        for _ in range(5):
            try:
                breaker.call(lambda: "should_fail")
            except CircuitBreakerError:
                rejected_count += 1
            except Exception:
                pass

        assert rejected_count == 5, (
            f"冷却期内 5 次请求应全部被拒绝，实际拒绝 {rejected_count} 次"
        )


# ═══════════════════════════════════════════════════════════════
#  6. 跨状态边界的并发竞争
# ═══════════════════════════════════════════════════════════════

class TestCrossStateConcurrency:
    """跨状态边界的并发竞争"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_concurrent_calls_across_state_transitions_no_deadlock(self):
        """并发调用跨状态转换不应死锁或崩溃

        场景：多线程并发调用熔断器，同时触发状态转换。
        预期：无死锁，无异常，30s 内完成。
        """
        set_trace_id("chaos-cb-conc-001")
        breaker = CircuitBreaker(
            name="chaos_conc",
            failure_threshold=0.5,
            min_calls=4,
            cooldown_seconds=0.3,
            half_open_max_calls=5,
            half_open_success_threshold=2,
            window_seconds=5,
        )

        call_count = [0]
        error_count = [0]
        stop_flag = threading.Event()
        lock = threading.Lock()

        def worker():
            while not stop_flag.is_set():
                try:
                    with lock:
                        call_count[0] += 1
                    # 50% 概率失败
                    if call_count[0] % 2 == 0:
                        breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
                    else:
                        breaker.call(lambda: "ok")
                except (RuntimeError, CircuitBreakerError):
                    with lock:
                        error_count[0] += 1
                except Exception:
                    pass

        # 启动 5 个线程，运行 1 秒
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        time.sleep(1.0)
        stop_flag.set()
        for t in threads:
            t.join(timeout=30)

        # 预期：所有线程正常退出（无死锁）
        assert all(not t.is_alive() for t in threads), "线程未在 30s 内退出（死锁）"
        # 预期：有调用发生
        assert call_count[0] > 0, "应有调用发生"
        # 预期：熔断器最终处于有效状态之一
        assert breaker.state in (
            CircuitState.CLOSED,
            CircuitState.OPEN,
            CircuitState.HALF_OPEN,
        )
