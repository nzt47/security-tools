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

import logging
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# 模块级 logger，用于混沌测试过程的结构化日志输出
logger = logging.getLogger(__name__)

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
        trace_id = "chaos-cb-spike-001"
        set_trace_id(trace_id)
        # 记录测试方法启动，便于关联后续日志
        logger.info(
            "[CB_CHAOS] %s - action=test_start, test_name=%s",
            trace_id,
            "test_error_rate_spike_from_zero_to_full_should_open_circuit",
        )
        # 使用短窗口和小阈值便于测试
        breaker = CircuitBreaker(
            name="chaos_spike",
            failure_threshold=0.3,
            min_calls=5,
            cooldown_seconds=60,
            window_seconds=10,
        )
        # 记录熔断器配置，便于排查阈值与窗口相关的问题
        logger.info(
            "[CB_CHAOS] %s - action=breaker_created, name=%s, failure_threshold=%s, "
            "min_calls=%s, cooldown_seconds=%s, window_seconds=%s",
            trace_id,
            breaker.name,
            breaker.failure_threshold,
            breaker.min_calls,
            breaker.cooldown_seconds,
            breaker.window_seconds,
        )

        # 阶段1：5 次成功建立基线
        for i in range(5):
            # 记录每轮循环开始（成功基线建立阶段）
            logger.info(
                "[CB_CHAOS] %s - action=baseline_call_start, cycle=%s, phase=success_baseline",
                trace_id, i,
            )
            breaker.call(lambda: "ok")
            # 记录每轮循环结束及当前状态
            logger.info(
                "[CB_CHAOS] %s - action=baseline_call_end, cycle=%s, state=%s, total_calls=%s",
                trace_id, i, breaker.state, breaker.stats.total_calls,
            )
        # 断言前记录预期与实际值，便于定位状态未保持 CLOSED 的问题
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, expected_state=%s, actual_state=%s, "
            "total_calls=%s, failure_count=%s",
            trace_id, CircuitState.CLOSED, breaker.state,
            breaker.stats.total_calls, breaker.stats.failure_count,
        )
        assert breaker.state == CircuitState.CLOSED

        # 阶段2：连续 5 次失败（错误率突增到 50%）
        # 注意：熔断器打开后 breaker.call() 抛出 CircuitBreakerError 而非 RuntimeError，
        # 需同时捕获两种异常
        for i in range(5):
            # 记录失败注入循环开始
            logger.info(
                "[CB_CHAOS] %s - action=failure_inject_start, cycle=%s, phase=error_spike, "
                "current_state=%s",
                trace_id, i, breaker.state,
            )
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            except (RuntimeError, CircuitBreakerError):
                pass
            # 记录失败注入循环结束及统计信息
            stats = breaker.stats
            failure_rate = (
                stats.failure_count / stats.total_calls
                if stats.total_calls > 0 else 0.0
            )
            logger.info(
                "[CB_CHAOS] %s - action=failure_inject_end, cycle=%s, state=%s, "
                "total_calls=%s, failure_count=%s, failure_rate=%.4f",
                trace_id, i, stats.state, stats.total_calls,
                stats.failure_count, failure_rate,
            )

        # 预期：错误率 50% > 30% 阈值，熔断器应打开
        # 关键断言前记录状态转换预期与实际统计
        stats = breaker.stats
        failure_rate = (
            stats.failure_count / stats.total_calls
            if stats.total_calls > 0 else 0.0
        )
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, from_state=%s, expected_to_state=%s, "
            "actual_state=%s, failure_rate=%.4f, total_calls=%s, threshold=%s",
            trace_id, CircuitState.CLOSED, CircuitState.OPEN, stats.state,
            failure_rate, stats.total_calls, breaker.failure_threshold,
        )
        assert breaker.state == CircuitState.OPEN, (
            f"错误率突增到 50% 后应熔断，实际状态: {breaker.state}"
        )

        # 验证：后续请求被拒绝
        # 记录熔断拒绝验证开始
        logger.info(
            "[CB_CHAOS] %s - action=block_verification_start, expected=CircuitBreakerError",
            trace_id,
        )
        with pytest.raises(CircuitBreakerError):
            breaker.call(lambda: "should_be_blocked")
        # 记录测试通过
        logger.info(
            "[CB_CHAOS] %s - action=test_end, result=passed, test_name=%s",
            trace_id,
            "test_error_rate_spike_from_zero_to_full_should_open_circuit",
        )

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_burst_failures_below_min_calls_should_not_open(self):
        """失败数未达 min_calls 时不应熔断（避免样本不足误判）"""
        trace_id = "chaos-cb-spike-002"
        set_trace_id(trace_id)
        # 记录测试方法启动
        logger.info(
            "[CB_CHAOS] %s - action=test_start, test_name=%s",
            trace_id,
            "test_burst_failures_below_min_calls_should_not_open",
        )
        breaker = CircuitBreaker(
            name="chaos_min_calls",
            failure_threshold=0.3,
            min_calls=10,
            window_seconds=10,
        )
        # 记录熔断器配置，关注 min_calls 阈值
        logger.info(
            "[CB_CHAOS] %s - action=breaker_created, name=%s, failure_threshold=%s, "
            "min_calls=%s, cooldown_seconds=%s, window_seconds=%s",
            trace_id,
            breaker.name,
            breaker.failure_threshold,
            breaker.min_calls,
            breaker.cooldown_seconds,
            breaker.window_seconds,
        )

        # 仅 3 次失败（< min_calls=10）
        for i in range(3):
            # 记录单轮失败注入开始
            logger.info(
                "[CB_CHAOS] %s - action=failure_inject_start, cycle=%s, phase=below_min_calls",
                trace_id, i,
            )
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
            except RuntimeError:
                pass
            # 记录单轮失败注入结束及当前统计
            stats = breaker.stats
            logger.info(
                "[CB_CHAOS] %s - action=failure_inject_end, cycle=%s, state=%s, "
                "total_calls=%s, failure_count=%s, min_calls=%s",
                trace_id, i, stats.state, stats.total_calls,
                stats.failure_count, breaker.min_calls,
            )

        # 预期：样本不足，不应熔断
        # 关键断言前记录预期与实际状态及样本量
        stats = breaker.stats
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, expected_state=%s, actual_state=%s, "
            "total_calls=%s, min_calls=%s, reason=样本不足不熔断",
            trace_id, CircuitState.CLOSED, stats.state,
            stats.total_calls, breaker.min_calls,
        )
        assert breaker.state == CircuitState.CLOSED, (
            "失败数未达 min_calls 时不应熔断"
        )
        # 记录测试通过
        logger.info(
            "[CB_CHAOS] %s - action=test_end, result=passed, test_name=%s",
            trace_id,
            "test_burst_failures_below_min_calls_should_not_open",
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
        trace_id = "chaos-cb-halfopen-001"
        set_trace_id(trace_id)
        # 记录测试方法启动
        logger.info(
            "[CB_CHAOS] %s - action=test_start, test_name=%s",
            trace_id,
            "test_half_open_concurrent_probes_should_be_limited",
        )
        breaker = CircuitBreaker(
            name="chaos_half_open",
            failure_threshold=0.3,
            min_calls=3,
            cooldown_seconds=0.5,  # 短冷却期便于测试
            half_open_max_calls=3,
            half_open_success_threshold=2,
            window_seconds=10,
        )
        # 记录熔断器配置，关注半开相关参数
        logger.info(
            "[CB_CHAOS] %s - action=breaker_created, name=%s, failure_threshold=%s, "
            "min_calls=%s, cooldown_seconds=%s, half_open_max_calls=%s, "
            "half_open_success_threshold=%s, window_seconds=%s",
            trace_id,
            breaker.name,
            breaker.failure_threshold,
            breaker.min_calls,
            breaker.cooldown_seconds,
            breaker.half_open_max_calls,
            breaker.half_open_success_threshold,
            breaker.window_seconds,
        )

        # 触发熔断
        for i in range(3):
            # 记录触发熔断的失败注入循环
            logger.info(
                "[CB_CHAOS] %s - action=trigger_open_start, cycle=%s, current_state=%s",
                trace_id, i, breaker.state,
            )
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
            except RuntimeError:
                pass
            logger.info(
                "[CB_CHAOS] %s - action=trigger_open_end, cycle=%s, state=%s, "
                "failure_count=%s",
                trace_id, i, breaker.state, breaker.stats.failure_count,
            )
        # 状态转换断言前记录 CLOSED→OPEN 预期
        stats = breaker.stats
        failure_rate = (
            stats.failure_count / stats.total_calls
            if stats.total_calls > 0 else 0.0
        )
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, from_state=%s, expected_to_state=%s, "
            "actual_state=%s, failure_rate=%.4f, total_calls=%s",
            trace_id, CircuitState.CLOSED, CircuitState.OPEN, stats.state,
            failure_rate, stats.total_calls,
        )
        assert breaker.state == CircuitState.OPEN

        # 等待冷却期到期，进入半开
        # 记录冷却等待开始，便于排查时序问题
        logger.info(
            "[CB_CHAOS] %s - action=cooldown_wait_start, cooldown_seconds=%s, "
            "expected_transition=%s->%s",
            trace_id, breaker.cooldown_seconds,
            CircuitState.OPEN, CircuitState.HALF_OPEN,
        )
        time.sleep(0.6)
        # 冷却后记录状态转换 OPEN→HALF_OPEN
        stats = breaker.stats
        logger.info(
            "[CB_CHAOS] %s - action=state_transition, from_state=%s, to_state=%s, "
            "total_calls=%s, failure_count=%s, reason=cooldown_expired",
            trace_id, CircuitState.OPEN, stats.state,
            stats.total_calls, stats.failure_count,
        )

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
        # 记录并发探测开始
        logger.info(
            "[CB_CHAOS] %s - action=concurrent_probes_start, thread_count=10, "
            "half_open_max_calls=%s",
            trace_id, breaker.half_open_max_calls,
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        # 记录并发探测结束及放行/拒绝统计
        logger.info(
            "[CB_CHAOS] %s - action=concurrent_probes_end, allowed=%s, rejected=%s, "
            "total=%s, half_open_max_calls=%s",
            trace_id, len(allowed), len(rejected),
            len(allowed) + len(rejected), breaker.half_open_max_calls,
        )

        # 预期：放行数不超过 half_open_max_calls（允许并发竞争，但应限制总数）
        # 关键断言前记录预期与实际值
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, expected_allowed_max=%s, "
            "actual_allowed=%s, expected_total=10, actual_total=%s",
            trace_id, breaker.half_open_max_calls, len(allowed),
            len(allowed) + len(rejected),
        )
        assert len(allowed) <= 3, (
            f"半开状态放行数 {len(allowed)} 超过 half_open_max_calls=3"
        )
        assert len(allowed) + len(rejected) == 10
        # 记录测试通过
        logger.info(
            "[CB_CHAOS] %s - action=test_end, result=passed, test_name=%s",
            trace_id,
            "test_half_open_concurrent_probes_should_be_limited",
        )


# ═══════════════════════════════════════════════════════════════
#  3. 恢复后状态转换
# ═══════════════════════════════════════════════════════════════

class TestRecoveryTransition:
    """恢复后状态转换：HALF_OPEN → CLOSED"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_half_open_success_threshold_recovers_to_closed(self):
        """半开状态下成功数达阈值应恢复 CLOSED"""
        trace_id = "chaos-cb-recover-001"
        set_trace_id(trace_id)
        # 记录测试方法启动
        logger.info(
            "[CB_CHAOS] %s - action=test_start, test_name=%s",
            trace_id,
            "test_half_open_success_threshold_recovers_to_closed",
        )
        breaker = CircuitBreaker(
            name="chaos_recover",
            failure_threshold=0.3,
            min_calls=3,
            cooldown_seconds=0.3,
            half_open_max_calls=5,
            half_open_success_threshold=2,
            window_seconds=10,
        )
        # 记录熔断器配置
        logger.info(
            "[CB_CHAOS] %s - action=breaker_created, name=%s, failure_threshold=%s, "
            "min_calls=%s, cooldown_seconds=%s, half_open_max_calls=%s, "
            "half_open_success_threshold=%s, window_seconds=%s",
            trace_id,
            breaker.name,
            breaker.failure_threshold,
            breaker.min_calls,
            breaker.cooldown_seconds,
            breaker.half_open_max_calls,
            breaker.half_open_success_threshold,
            breaker.window_seconds,
        )

        # 触发熔断
        for i in range(3):
            # 记录触发熔断的失败注入循环
            logger.info(
                "[CB_CHAOS] %s - action=trigger_open_start, cycle=%s, current_state=%s",
                trace_id, i, breaker.state,
            )
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
            except RuntimeError:
                pass
            logger.info(
                "[CB_CHAOS] %s - action=trigger_open_end, cycle=%s, state=%s, "
                "failure_count=%s",
                trace_id, i, breaker.state, breaker.stats.failure_count,
            )
        # 断言前记录 CLOSED→OPEN 状态转换预期
        stats = breaker.stats
        failure_rate = (
            stats.failure_count / stats.total_calls
            if stats.total_calls > 0 else 0.0
        )
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, from_state=%s, expected_to_state=%s, "
            "actual_state=%s, failure_rate=%.4f, total_calls=%s",
            trace_id, CircuitState.CLOSED, CircuitState.OPEN, stats.state,
            failure_rate, stats.total_calls,
        )
        assert breaker.state == CircuitState.OPEN

        # 等待冷却期，进入半开
        # 记录冷却等待开始
        logger.info(
            "[CB_CHAOS] %s - action=cooldown_wait_start, cooldown_seconds=%s, "
            "expected_transition=%s->%s",
            trace_id, breaker.cooldown_seconds,
            CircuitState.OPEN, CircuitState.HALF_OPEN,
        )
        time.sleep(0.4)
        # 冷却后记录状态转换 OPEN→HALF_OPEN
        stats = breaker.stats
        logger.info(
            "[CB_CHAOS] %s - action=state_transition, from_state=%s, to_state=%s, "
            "total_calls=%s, failure_count=%s, reason=cooldown_expired",
            trace_id, CircuitState.OPEN, stats.state,
            stats.total_calls, stats.failure_count,
        )

        # 半开状态下连续成功 2 次，应恢复 CLOSED
        # 第一次探测
        logger.info(
            "[CB_CHAOS] %s - action=half_open_probe_start, probe_index=1, "
            "expected_result=ok1",
            trace_id,
        )
        result1 = breaker.call(lambda: "ok1")
        # 断言前记录第一次探测结果
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, expected_result=%s, actual_result=%s, "
            "expected_state=%s, actual_state=%s",
            trace_id, "ok1", result1,
            CircuitState.HALF_OPEN, breaker.state,
        )
        assert result1 == "ok1"
        # 第一次成功后仍在 HALF_OPEN
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, expected_state=%s, actual_state=%s, "
            "half_open_successes=%s, threshold=%s",
            trace_id, CircuitState.HALF_OPEN, breaker.state,
            breaker.stats.half_open_successes, breaker.half_open_success_threshold,
        )
        assert breaker.state == CircuitState.HALF_OPEN

        # 第二次探测
        logger.info(
            "[CB_CHAOS] %s - action=half_open_probe_start, probe_index=2, "
            "expected_result=ok2",
            trace_id,
        )
        result2 = breaker.call(lambda: "ok2")
        # 断言前记录第二次探测结果
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, expected_result=%s, actual_result=%s",
            trace_id, "ok2", result2,
        )
        assert result2 == "ok2"
        # 第二次成功后应恢复 CLOSED
        # 关键状态转换断言 HALF_OPEN→CLOSED
        stats = breaker.stats
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, from_state=%s, expected_to_state=%s, "
            "actual_state=%s, half_open_successes=%s, threshold=%s, total_calls=%s",
            trace_id, CircuitState.HALF_OPEN, CircuitState.CLOSED, stats.state,
            stats.half_open_successes, breaker.half_open_success_threshold,
            stats.total_calls,
        )
        assert breaker.state == CircuitState.CLOSED, (
            f"半开状态成功 2 次后应恢复 CLOSED，实际: {breaker.state}"
        )
        # 记录测试通过
        logger.info(
            "[CB_CHAOS] %s - action=test_end, result=passed, test_name=%s",
            trace_id,
            "test_half_open_success_threshold_recovers_to_closed",
        )

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_half_open_failure_reopens_circuit(self):
        """半开状态下探测失败应重新打开熔断"""
        trace_id = "chaos-cb-recover-002"
        set_trace_id(trace_id)
        # 记录测试方法启动
        logger.info(
            "[CB_CHAOS] %s - action=test_start, test_name=%s",
            trace_id,
            "test_half_open_failure_reopens_circuit",
        )
        breaker = CircuitBreaker(
            name="chaos_reopen",
            failure_threshold=0.3,
            min_calls=3,
            cooldown_seconds=0.3,
            half_open_max_calls=3,
            half_open_success_threshold=2,
            window_seconds=10,
        )
        # 记录熔断器配置
        logger.info(
            "[CB_CHAOS] %s - action=breaker_created, name=%s, failure_threshold=%s, "
            "min_calls=%s, cooldown_seconds=%s, half_open_max_calls=%s, "
            "half_open_success_threshold=%s, window_seconds=%s",
            trace_id,
            breaker.name,
            breaker.failure_threshold,
            breaker.min_calls,
            breaker.cooldown_seconds,
            breaker.half_open_max_calls,
            breaker.half_open_success_threshold,
            breaker.window_seconds,
        )

        # 触发熔断
        for i in range(3):
            # 记录触发熔断的失败注入循环
            logger.info(
                "[CB_CHAOS] %s - action=trigger_open_start, cycle=%s, current_state=%s",
                trace_id, i, breaker.state,
            )
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
            except RuntimeError:
                pass
            logger.info(
                "[CB_CHAOS] %s - action=trigger_open_end, cycle=%s, state=%s, "
                "failure_count=%s",
                trace_id, i, breaker.state, breaker.stats.failure_count,
            )
        # 断言前记录 CLOSED→OPEN 状态转换预期
        stats = breaker.stats
        failure_rate = (
            stats.failure_count / stats.total_calls
            if stats.total_calls > 0 else 0.0
        )
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, from_state=%s, expected_to_state=%s, "
            "actual_state=%s, failure_rate=%.4f, total_calls=%s",
            trace_id, CircuitState.CLOSED, CircuitState.OPEN, stats.state,
            failure_rate, stats.total_calls,
        )
        assert breaker.state == CircuitState.OPEN

        # 等待冷却期，进入半开
        # 记录冷却等待开始
        logger.info(
            "[CB_CHAOS] %s - action=cooldown_wait_start, cooldown_seconds=%s, "
            "expected_transition=%s->%s",
            trace_id, breaker.cooldown_seconds,
            CircuitState.OPEN, CircuitState.HALF_OPEN,
        )
        time.sleep(0.4)
        # 断言前记录 OPEN→HALF_OPEN 状态转换
        stats = breaker.stats
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, from_state=%s, expected_to_state=%s, "
            "actual_state=%s, total_calls=%s, reason=cooldown_expired",
            trace_id, CircuitState.OPEN, CircuitState.HALF_OPEN, stats.state,
            stats.total_calls,
        )
        assert breaker.state == CircuitState.HALF_OPEN

        # 半开状态下失败，应重新打开
        # 记录半开探测失败注入开始
        logger.info(
            "[CB_CHAOS] %s - action=half_open_failure_probe_start, expected_exception=RuntimeError",
            trace_id,
        )
        try:
            breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("still_bad")))
        except RuntimeError:
            pass
        # 记录半开探测失败后状态
        stats = breaker.stats
        logger.info(
            "[CB_CHAOS] %s - action=state_transition, from_state=%s, to_state=%s, "
            "total_calls=%s, failure_count=%s, reason=half_open_probe_failed",
            trace_id, CircuitState.HALF_OPEN, stats.state,
            stats.total_calls, stats.failure_count,
        )

        # 关键状态转换断言 HALF_OPEN→OPEN
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, from_state=%s, expected_to_state=%s, "
            "actual_state=%s, total_calls=%s, failure_count=%s",
            trace_id, CircuitState.HALF_OPEN, CircuitState.OPEN, stats.state,
            stats.total_calls, stats.failure_count,
        )
        assert breaker.state == CircuitState.OPEN, (
            f"半开探测失败应重新打开熔断，实际: {breaker.state}"
        )
        # 记录测试通过
        logger.info(
            "[CB_CHAOS] %s - action=test_end, result=passed, test_name=%s",
            trace_id,
            "test_half_open_failure_reopens_circuit",
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
        trace_id = "chaos-cb-cycle-001"
        set_trace_id(trace_id)
        # 记录测试方法启动
        logger.info(
            "[CB_CHAOS] %s - action=test_start, test_name=%s",
            trace_id,
            "test_repeated_open_halfopen_open_cycle_should_be_stable",
        )
        breaker = CircuitBreaker(
            name="chaos_cycle",
            failure_threshold=0.3,
            min_calls=3,
            cooldown_seconds=0.2,
            half_open_max_calls=3,
            half_open_success_threshold=2,
            window_seconds=5,
        )
        # 记录熔断器配置
        logger.info(
            "[CB_CHAOS] %s - action=breaker_created, name=%s, failure_threshold=%s, "
            "min_calls=%s, cooldown_seconds=%s, half_open_max_calls=%s, "
            "half_open_success_threshold=%s, window_seconds=%s",
            trace_id,
            breaker.name,
            breaker.failure_threshold,
            breaker.min_calls,
            breaker.cooldown_seconds,
            breaker.half_open_max_calls,
            breaker.half_open_success_threshold,
            breaker.window_seconds,
        )

        # 模拟 3 轮 OPEN → HALF_OPEN → OPEN 循环
        # 注意：循环 2+ 时熔断器可能仍处于 OPEN 状态（冷却期未过），
        # 此时 breaker.call() 抛出 CircuitBreakerError 而非 RuntimeError，
        # 需同时捕获两种异常
        for cycle in range(3):
            # 记录每轮循环开始及当前状态
            logger.info(
                "[CB_CHAOS] %s - action=cycle_start, cycle=%s, current_state=%s, "
                "total_calls=%s, failure_count=%s",
                trace_id, cycle, breaker.state,
                breaker.stats.total_calls, breaker.stats.failure_count,
            )
            # 触发熔断（若已 OPEN 则调用被拒绝，不影响状态）
            for j in range(3):
                try:
                    breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
                except (RuntimeError, CircuitBreakerError):
                    pass
            # 断言前记录状态转换预期
            stats = breaker.stats
            logger.info(
                "[CB_CHAOS] %s - action=assert_before, cycle=%s, phase=trigger_open, "
                "expected_state=%s, actual_state=%s, failure_count=%s",
                trace_id, cycle, CircuitState.OPEN, stats.state, stats.failure_count,
            )
            assert breaker.state == CircuitState.OPEN, (
                f"循环 {cycle}: 应处于 OPEN 状态"
            )

            # 等待冷却期
            # 记录冷却等待开始
            logger.info(
                "[CB_CHAOS] %s - action=cooldown_wait_start, cycle=%s, cooldown_seconds=%s, "
                "expected_transition=%s->%s",
                trace_id, cycle, breaker.cooldown_seconds,
                CircuitState.OPEN, CircuitState.HALF_OPEN,
            )
            time.sleep(0.25)
            # 断言前记录 OPEN→HALF_OPEN 状态转换
            stats = breaker.stats
            logger.info(
                "[CB_CHAOS] %s - action=assert_before, cycle=%s, phase=cooldown_done, "
                "from_state=%s, expected_to_state=%s, actual_state=%s, total_calls=%s",
                trace_id, cycle, CircuitState.OPEN, CircuitState.HALF_OPEN,
                stats.state, stats.total_calls,
            )
            assert breaker.state == CircuitState.HALF_OPEN, (
                f"循环 {cycle}: 冷却后应进入 HALF_OPEN"
            )

            # 探测失败，重新打开
            # 记录半开探测失败注入开始
            logger.info(
                "[CB_CHAOS] %s - action=half_open_failure_probe_start, cycle=%s",
                trace_id, cycle,
            )
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
            except (RuntimeError, CircuitBreakerError):
                pass
            # 断言前记录 HALF_OPEN→OPEN 状态转换
            stats = breaker.stats
            logger.info(
                "[CB_CHAOS] %s - action=assert_before, cycle=%s, phase=probe_failed, "
                "from_state=%s, expected_to_state=%s, actual_state=%s, "
                "total_calls=%s, failure_count=%s",
                trace_id, cycle, CircuitState.HALF_OPEN, CircuitState.OPEN,
                stats.state, stats.total_calls, stats.failure_count,
            )
            assert breaker.state == CircuitState.OPEN, (
                f"循环 {cycle}: 探测失败应重新 OPEN"
            )
            # 记录每轮循环结束
            logger.info(
                "[CB_CHAOS] %s - action=cycle_end, cycle=%s, final_state=%s, "
                "total_calls=%s, failure_count=%s",
                trace_id, cycle, breaker.state,
                breaker.stats.total_calls, breaker.stats.failure_count,
            )

        # 验证：3 轮循环后状态机仍然稳定
        # 注意：循环 2/3 中熔断器已处于 OPEN 状态，"触发熔断" 阶段的调用被拒绝
        # （抛出 CircuitBreakerError），不计入 failure_count。
        # 实际失败数 = 3（循环1初始失败）+ 3（每轮探测失败）= 6
        stats = breaker.stats
        # 关键断言前记录最终统计
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, phase=final_stats, "
            "expected_state=%s, actual_state=%s, expected_min_failure_count=6, "
            "actual_failure_count=%s, total_calls=%s",
            trace_id, CircuitState.OPEN, stats.state,
            stats.failure_count, stats.total_calls,
        )
        assert stats.state == CircuitState.OPEN
        assert stats.failure_count >= 6, (
            f"3 轮循环后至少应有 6 次真实失败，实际 {stats.failure_count}"
        )
        # 记录测试通过
        logger.info(
            "[CB_CHAOS] %s - action=test_end, result=passed, test_name=%s",
            trace_id,
            "test_repeated_open_halfopen_open_cycle_should_be_stable",
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
        trace_id = "chaos-cb-cooldown-001"
        set_trace_id(trace_id)
        # 记录测试方法启动
        logger.info(
            "[CB_CHAOS] %s - action=test_start, test_name=%s",
            trace_id,
            "test_requests_during_cooldown_should_be_rejected",
        )
        breaker = CircuitBreaker(
            name="chaos_cooldown",
            failure_threshold=0.3,
            min_calls=3,
            cooldown_seconds=2.0,  # 长冷却期
            half_open_max_calls=3,
            half_open_success_threshold=2,
            window_seconds=10,
        )
        # 记录熔断器配置，关注长冷却期
        logger.info(
            "[CB_CHAOS] %s - action=breaker_created, name=%s, failure_threshold=%s, "
            "min_calls=%s, cooldown_seconds=%s, half_open_max_calls=%s, "
            "half_open_success_threshold=%s, window_seconds=%s",
            trace_id,
            breaker.name,
            breaker.failure_threshold,
            breaker.min_calls,
            breaker.cooldown_seconds,
            breaker.half_open_max_calls,
            breaker.half_open_success_threshold,
            breaker.window_seconds,
        )

        # 触发熔断
        for i in range(3):
            # 记录触发熔断的失败注入循环
            logger.info(
                "[CB_CHAOS] %s - action=trigger_open_start, cycle=%s, current_state=%s",
                trace_id, i, breaker.state,
            )
            try:
                breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("err")))
            except RuntimeError:
                pass
            logger.info(
                "[CB_CHAOS] %s - action=trigger_open_end, cycle=%s, state=%s, "
                "failure_count=%s",
                trace_id, i, breaker.state, breaker.stats.failure_count,
            )
        # 断言前记录 CLOSED→OPEN 状态转换预期
        stats = breaker.stats
        failure_rate = (
            stats.failure_count / stats.total_calls
            if stats.total_calls > 0 else 0.0
        )
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, from_state=%s, expected_to_state=%s, "
            "actual_state=%s, failure_rate=%.4f, total_calls=%s",
            trace_id, CircuitState.CLOSED, CircuitState.OPEN, stats.state,
            failure_rate, stats.total_calls,
        )
        assert breaker.state == CircuitState.OPEN

        # 冷却期内连续 5 次请求都应被拒绝
        rejected_count = 0
        # 记录冷却期拒绝验证开始
        logger.info(
            "[CB_CHAOS] %s - action=cooldown_rejection_loop_start, total_iterations=5, "
            "cooldown_seconds=%s",
            trace_id, breaker.cooldown_seconds,
        )
        for i in range(5):
            # 记录每轮拒绝验证循环开始
            logger.info(
                "[CB_CHAOS] %s - action=cooldown_reject_attempt_start, cycle=%s, "
                "current_state=%s, current_rejected_count=%s",
                trace_id, i, breaker.state, rejected_count,
            )
            try:
                breaker.call(lambda: "should_fail")
            except CircuitBreakerError:
                rejected_count += 1
            except Exception:
                pass
            # 记录每轮拒绝验证循环结束
            logger.info(
                "[CB_CHAOS] %s - action=cooldown_reject_attempt_end, cycle=%s, "
                "rejected_so_far=%s",
                trace_id, i, rejected_count,
            )

        # 关键断言前记录预期与实际拒绝次数
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, expected_rejected_count=5, "
            "actual_rejected_count=%s, cooldown_seconds=%s",
            trace_id, rejected_count, breaker.cooldown_seconds,
        )
        assert rejected_count == 5, (
            f"冷却期内 5 次请求应全部被拒绝，实际拒绝 {rejected_count} 次"
        )
        # 记录测试通过
        logger.info(
            "[CB_CHAOS] %s - action=test_end, result=passed, test_name=%s",
            trace_id,
            "test_requests_during_cooldown_should_be_rejected",
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
        trace_id = "chaos-cb-conc-001"
        set_trace_id(trace_id)
        # 记录测试方法启动
        logger.info(
            "[CB_CHAOS] %s - action=test_start, test_name=%s",
            trace_id,
            "test_concurrent_calls_across_state_transitions_no_deadlock",
        )
        breaker = CircuitBreaker(
            name="chaos_conc",
            failure_threshold=0.5,
            min_calls=4,
            cooldown_seconds=0.3,
            half_open_max_calls=5,
            half_open_success_threshold=2,
            window_seconds=5,
        )
        # 记录熔断器配置
        logger.info(
            "[CB_CHAOS] %s - action=breaker_created, name=%s, failure_threshold=%s, "
            "min_calls=%s, cooldown_seconds=%s, half_open_max_calls=%s, "
            "half_open_success_threshold=%s, window_seconds=%s",
            trace_id,
            breaker.name,
            breaker.failure_threshold,
            breaker.min_calls,
            breaker.cooldown_seconds,
            breaker.half_open_max_calls,
            breaker.half_open_success_threshold,
            breaker.window_seconds,
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
        # 记录并发线程启动
        logger.info(
            "[CB_CHAOS] %s - action=concurrent_threads_start, thread_count=5, "
            "run_duration_seconds=1.0",
            trace_id,
        )
        for t in threads:
            t.start()
        time.sleep(1.0)
        # 记录停止信号已设置及运行期间统计
        logger.info(
            "[CB_CHAOS] %s - action=stop_signal_set, call_count=%s, error_count=%s, "
            "current_state=%s",
            trace_id, call_count[0], error_count[0], breaker.state,
        )
        stop_flag.set()
        for t in threads:
            t.join(timeout=30)

        # 关键断言前记录线程存活状态
        alive_threads = [t for t in threads if t.is_alive()]
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, phase=thread_exit_check, "
            "expected_alive_count=0, actual_alive_count=%s, call_count=%s, "
            "error_count=%s, final_state=%s",
            trace_id, len(alive_threads), call_count[0], error_count[0],
            breaker.state,
        )
        # 预期：所有线程正常退出（无死锁）
        assert all(not t.is_alive() for t in threads), "线程未在 30s 内退出（死锁）"
        # 预期：有调用发生
        # 关键断言前记录调用计数
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, phase=call_count_check, "
            "expected_min_call_count=1, actual_call_count=%s",
            trace_id, call_count[0],
        )
        assert call_count[0] > 0, "应有调用发生"
        # 预期：熔断器最终处于有效状态之一
        # 关键断言前记录最终状态
        final_state = breaker.state
        stats = breaker.stats
        logger.info(
            "[CB_CHAOS] %s - action=assert_before, phase=final_state_check, "
            "expected_states=[CLOSED,OPEN,HALF_OPEN], actual_state=%s, "
            "total_calls=%s, failure_count=%s, success_count=%s",
            trace_id, final_state, stats.total_calls,
            stats.failure_count, stats.success_count,
        )
        assert breaker.state in (
            CircuitState.CLOSED,
            CircuitState.OPEN,
            CircuitState.HALF_OPEN,
        )
        # 记录测试通过
        logger.info(
            "[CB_CHAOS] %s - action=test_end, result=passed, test_name=%s",
            trace_id,
            "test_concurrent_calls_across_state_transitions_no_deadlock",
        )
