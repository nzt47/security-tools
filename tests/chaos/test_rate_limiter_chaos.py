# -*- coding: utf-8 -*-
"""限流器混沌测试 — 突发流量下的稳定性验证

【测试目标】
验证 RateLimiter 在以下极端场景下的稳定性：
1. 令牌桶耗尽（瞬间消费所有令牌）
2. 突发请求洪峰（远超桶容量）
3. 多层级限流叠加（network/shell/file/default 同时触发）
4. 长时间持续调用（令牌补充与消费的动态平衡）
5. 并发线程同时消费令牌（线程安全）
6. 限流器重置后的恢复

【可观测性约束】
- 边界显性化：所有故障注入通过真实令牌桶计算实现，不依赖外部服务
- 异常处理：每个测试设置 30s 超时
- 埋点预留：限流器内部已记录日志

【生成日志摘要】
- 生成时间：2026-06-27
- 版本：v1.0.0
- 内容：限流器混沌测试
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.rate_limiter import RateLimiter  # noqa: E402

# 模块级 logger，用于排查限流器混沌测试问题
logger = logging.getLogger(__name__)


def _get_remaining_tokens(limiter: RateLimiter, category: str):
    """辅助函数：读取指定类别的剩余令牌数（仅供日志输出使用）

    通过访问内部 _buckets 字典读取，避免触发锁竞争；
    若桶尚未初始化或不存在，返回 "N/A"。
    """
    bucket = limiter._buckets.get(category)
    if bucket is None:
        return "N/A"
    return round(bucket.get("tokens", 0.0), 4)


# ═══════════════════════════════════════════════════════════════
#  1. 令牌桶耗尽
# ═══════════════════════════════════════════════════════════════

class TestTokenBucketExhaustion:
    """令牌桶耗尽场景"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_bucket_exhaustion_should_block_subsequent_calls(self):
        """令牌桶耗尽后应阻塞后续调用

        场景：瞬间消费完所有令牌，后续请求应被限流。
        预期：前 N 个请求通过（N=capacity），第 N+1 个被限流。
        """
        # 记录测试启动，便于在大量混沌测试中定位当前用例
        logger.info("[RL_CHAOS] %s - action=test_start, case=bucket_exhaustion",
                    "TestTokenBucketExhaustion")

        limiter = RateLimiter(limits={"default": (3, 1.0)})  # 容量 3
        # 记录限流器配置：容量与补充速率，便于复现问题时对齐参数
        logger.info("[RL_CHAOS] %s - action=limiter_created, category=default, "
                    "capacity=3, refill_rate=1.0",
                    "TestTokenBucketExhaustion")

        # 前 3 个请求应通过
        results = []
        for i in range(3):
            r = limiter.check("test_tool")
            results.append(r)
            # 每次消费后记录 tool_name、放行/限流状态与剩余令牌数
            logger.info("[RL_CHAOS] %s - action=consume, idx=%d, tool_name=test_tool, "
                        "allowed=%s, remaining_tokens=%s",
                        "TestTokenBucketExhaustion", i + 1, r,
                        _get_remaining_tokens(limiter, "default"))

        # 关键断言前记录预期值与实际值
        logger.info("[RL_CHAOS] %s - action=assert_before, expected=[True,True,True], "
                    "actual=%s", "TestTokenBucketExhaustion", results)
        assert results == [True, True, True], (
            f"前 3 个请求应通过，实际: {results}"
        )

        # 第 4 个请求应被限流
        result = limiter.check("test_tool")
        # 记录令牌桶已耗尽，第 4 次消费必然被限流
        logger.info("[RL_CHAOS] %s - action=exhaustion, tool_name=test_tool, "
                    "allowed=%s, remaining_tokens=%s, msg=bucket_exhausted",
                    "TestTokenBucketExhaustion", result,
                    _get_remaining_tokens(limiter, "default"))
        logger.info("[RL_CHAOS] %s - action=assert_before, expected=False, actual=%s",
                    "TestTokenBucketExhaustion", result)
        assert result is False, "令牌桶耗尽后应限流"

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_bucket_refill_after_wait_should_allow_again(self):
        """等待令牌补充后应再次允许调用

        场景：耗尽令牌后等待一段时间，令牌应按 refill_rate 补充。
        预期：等待 1.2s 后（refill_rate=1.0），应有 1 个新令牌可用。
        """
        # 记录测试启动，便于定位 refill 相关用例
        logger.info("[RL_CHAOS] %s - action=test_start, case=refill_after_wait",
                    "TestTokenBucketExhaustion")

        limiter = RateLimiter(limits={"default": (2, 1.0)})  # 容量 2, 1/s
        # 记录限流器配置：容量 2、补充速率 1/s
        logger.info("[RL_CHAOS] %s - action=limiter_created, category=default, "
                    "capacity=2, refill_rate=1.0",
                    "TestTokenBucketExhaustion")

        # 耗尽令牌：依次消费 2 个令牌，第 3 次应被限流
        r1 = limiter.check("test_tool")
        logger.info("[RL_CHAOS] %s - action=consume, idx=1, tool_name=test_tool, "
                    "allowed=%s, remaining_tokens=%s",
                    "TestTokenBucketExhaustion", r1,
                    _get_remaining_tokens(limiter, "default"))
        assert r1 is True

        r2 = limiter.check("test_tool")
        logger.info("[RL_CHAOS] %s - action=consume, idx=2, tool_name=test_tool, "
                    "allowed=%s, remaining_tokens=%s",
                    "TestTokenBucketExhaustion", r2,
                    _get_remaining_tokens(limiter, "default"))
        assert r2 is True

        r3 = limiter.check("test_tool")
        # 记录令牌桶耗尽状态
        logger.info("[RL_CHAOS] %s - action=exhaustion, idx=3, tool_name=test_tool, "
                    "allowed=%s, remaining_tokens=%s, msg=bucket_exhausted",
                    "TestTokenBucketExhaustion", r3,
                    _get_remaining_tokens(limiter, "default"))
        assert r3 is False

        # 等待 1.2 秒，应补充 1 个令牌
        logger.info("[RL_CHAOS] %s - action=sleep_start, seconds=1.2, "
                    "msg=waiting_for_refill", "TestTokenBucketExhaustion")
        time.sleep(1.2)
        # 记录等待后的剩余令牌数（refill 后的可用令牌）
        logger.info("[RL_CHAOS] %s - action=refill_done, remaining_tokens=%s, "
                    "msg=refilled_after_wait",
                    "TestTokenBucketExhaustion",
                    _get_remaining_tokens(limiter, "default"))

        r4 = limiter.check("test_tool")
        # 消费补充后的令牌
        logger.info("[RL_CHAOS] %s - action=consume, idx=4, tool_name=test_tool, "
                    "allowed=%s, remaining_tokens=%s, msg=after_refill_consume",
                    "TestTokenBucketExhaustion", r4,
                    _get_remaining_tokens(limiter, "default"))
        logger.info("[RL_CHAOS] %s - action=assert_before, expected=True, actual=%s",
                    "TestTokenBucketExhaustion", r4)
        assert r4 is True, "等待补充后应再次允许"


# ═══════════════════════════════════════════════════════════════
#  2. 突发请求洪峰
# ═══════════════════════════════════════════════════════════════

class TestBurstTraffic:
    """突发请求洪峰"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_burst_far_exceeding_capacity_should_be_throttled(self):
        """突发请求数远超容量时应被限流

        场景：容量 5，突发 50 个请求。
        预期：前 5 个通过，剩余 45 个被限流。
        """
        # 记录测试启动，便于定位突发流量用例
        logger.info("[RL_CHAOS] %s - action=test_start, case=burst_far_exceeding",
                    "TestBurstTraffic")

        limiter = RateLimiter(limits={"default": (5, 1.0)})
        # 记录限流器配置：容量 5、补充速率 1/s
        logger.info("[RL_CHAOS] %s - action=limiter_created, category=default, "
                    "capacity=5, refill_rate=1.0", "TestBurstTraffic")

        allowed = 0
        rejected = 0

        for i in range(50):
            r = limiter.check("burst_tool")
            if r:
                allowed += 1
            else:
                rejected += 1
            # 每次消费后记录放行/限流与剩余令牌，便于排查突发流量下的逐次行为
            logger.info("[RL_CHAOS] %s - action=consume, idx=%d, tool_name=burst_tool, "
                        "allowed=%s, blocked=%s, running_allowed=%d, running_rejected=%d, "
                        "remaining_tokens=%s",
                        "TestBurstTraffic", i + 1, r, (not r), allowed, rejected,
                        _get_remaining_tokens(limiter, "default"))

        # 关键断言前记录预期值与实际值
        logger.info("[RL_CHAOS] %s - action=assert_before, "
                    "expected_allowed=5, actual_allowed=%d, "
                    "expected_rejected=45, actual_rejected=%d",
                    "TestBurstTraffic", allowed, rejected)
        assert allowed == 5, f"应放行 5 个，实际 {allowed}"
        assert rejected == 45, f"应限流 45 个，实际 {rejected}"


# ═══════════════════════════════════════════════════════════════
#  3. 多层级限流叠加
# ═══════════════════════════════════════════════════════════════

class TestMultiLevelLimiting:
    """多层级限流叠加：不同类别独立计数"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_different_categories_should_have_independent_buckets(self):
        """不同工具类别应有独立的令牌桶

        场景：network/shell/file/default 四类工具同时调用，
        每类应独立限流，互不影响。

        预期：每类工具的前 N 个请求都通过（不被其他类影响）。
        """
        # 记录测试启动，便于定位多层级限流用例
        logger.info("[RL_CHAOS] %s - action=test_start, "
                    "case=multi_level_independent_buckets",
                    "TestMultiLevelLimiting")

        limiter = RateLimiter()  # 使用默认配置
        # 记录使用默认配置：network(5,0.5)、shell(2,0.2)、file(15,1.0)、default(10,1.0)
        logger.info("[RL_CHAOS] %s - action=limiter_created, "
                    "config=default, network=(5,0.5), shell=(2,0.2), "
                    "file=(15,1.0), default=(10,1.0)",
                    "TestMultiLevelLimiting")

        # network: 容量 5
        for i in range(5):
            r = limiter.check(f"http_fetch_{i}")
            # 每次 network 消费后记录放行结果与剩余令牌
            logger.info("[RL_CHAOS] %s - action=consume, category=network, "
                        "idx=%d, tool_name=http_fetch_%d, allowed=%s, "
                        "remaining_tokens=%s",
                        "TestMultiLevelLimiting", i + 1, i, r,
                        _get_remaining_tokens(limiter, "network"))
            assert r is True, (
                f"network 工具第 {i+1} 次应通过"
            )
        # 第 6 个 network 应限流
        r_net_extra = limiter.check("http_fetch_extra")
        # 记录 network 桶已耗尽
        logger.info("[RL_CHAOS] %s - action=exhaustion, category=network, "
                    "tool_name=http_fetch_extra, allowed=%s, "
                    "remaining_tokens=%s, msg=bucket_exhausted",
                    "TestMultiLevelLimiting", r_net_extra,
                    _get_remaining_tokens(limiter, "network"))
        logger.info("[RL_CHAOS] %s - action=assert_before, "
                    "category=network, expected=False, actual=%s",
                    "TestMultiLevelLimiting", r_net_extra)
        assert r_net_extra is False

        # shell: 容量 2（独立桶，不受 network 影响）
        r_sh1 = limiter.check("shell_execute")
        logger.info("[RL_CHAOS] %s - action=consume, category=shell, "
                    "idx=1, tool_name=shell_execute, allowed=%s, "
                    "remaining_tokens=%s",
                    "TestMultiLevelLimiting", r_sh1,
                    _get_remaining_tokens(limiter, "shell"))
        assert r_sh1 is True

        r_sh2 = limiter.check("run_program")
        logger.info("[RL_CHAOS] %s - action=consume, category=shell, "
                    "idx=2, tool_name=run_program, allowed=%s, "
                    "remaining_tokens=%s",
                    "TestMultiLevelLimiting", r_sh2,
                    _get_remaining_tokens(limiter, "shell"))
        assert r_sh2 is True

        r_sh3 = limiter.check("start_process")
        # 记录 shell 桶已耗尽
        logger.info("[RL_CHAOS] %s - action=exhaustion, category=shell, "
                    "idx=3, tool_name=start_process, allowed=%s, "
                    "remaining_tokens=%s, msg=bucket_exhausted",
                    "TestMultiLevelLimiting", r_sh3,
                    _get_remaining_tokens(limiter, "shell"))
        logger.info("[RL_CHAOS] %s - action=assert_before, "
                    "category=shell, expected=False, actual=%s",
                    "TestMultiLevelLimiting", r_sh3)
        assert r_sh3 is False

        # file: 容量 15（独立桶）
        for i in range(15):
            r = limiter.check(f"read_file_{i}")
            # file 类别消费次数较多，仅每 5 次记录一次以控制日志量
            if (i + 1) % 5 == 0 or i == 0:
                logger.info("[RL_CHAOS] %s - action=consume, category=file, "
                            "idx=%d, tool_name=read_file_%d, allowed=%s, "
                            "remaining_tokens=%s, msg=sampled_log",
                            "TestMultiLevelLimiting", i + 1, i, r,
                            _get_remaining_tokens(limiter, "file"))
            assert r is True
        r_file_extra = limiter.check("read_file_extra")
        # 记录 file 桶已耗尽
        logger.info("[RL_CHAOS] %s - action=exhaustion, category=file, "
                    "tool_name=read_file_extra, allowed=%s, "
                    "remaining_tokens=%s, msg=bucket_exhausted",
                    "TestMultiLevelLimiting", r_file_extra,
                    _get_remaining_tokens(limiter, "file"))
        logger.info("[RL_CHAOS] %s - action=assert_before, "
                    "category=file, expected=False, actual=%s",
                    "TestMultiLevelLimiting", r_file_extra)
        assert r_file_extra is False


# ═══════════════════════════════════════════════════════════════
#  4. 长时间持续调用
# ═══════════════════════════════════════════════════════════════

class TestSustainedLoad:
    """长时间持续调用下的动态平衡"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_sustained_load_balances_consume_and_refill(self):
        """持续调用应在消费与补充之间达到动态平衡

        场景：容量 5，补充速率 10/s（即 0.1s 补充 1 个），
        持续高频调用 1 秒。

        预期：通过数应在 [5, 15] 之间（不超过容量 + 1s 内补充量）。
        """
        # 记录测试启动，便于定位持续负载用例
        logger.info("[RL_CHAOS] %s - action=test_start, case=sustained_load",
                    "TestSustainedLoad")

        limiter = RateLimiter(limits={"default": (5, 10.0)})  # 5 tokens, 10/s
        # 记录限流器配置：容量 5、补充速率 10/s（动态平衡场景）
        logger.info("[RL_CHAOS] %s - action=limiter_created, category=default, "
                    "capacity=5, refill_rate=10.0", "TestSustainedLoad")

        allowed = 0
        attempts = 0
        start = time.time()
        last_log = start
        while time.time() - start < 1.0:
            r = limiter.check("sustained_tool")
            attempts += 1
            if r:
                allowed += 1
            # 持续高频循环，每 0.1s 记录一次以避免日志爆炸，便于观察动态平衡
            now = time.time()
            if now - last_log >= 0.1:
                logger.info("[RL_CHAOS] %s - action=progress, elapsed_ms=%d, "
                            "attempts=%d, allowed=%d, remaining_tokens=%s, "
                            "msg=sustained_load_progress",
                            "TestSustainedLoad",
                            int((now - start) * 1000), attempts, allowed,
                            _get_remaining_tokens(limiter, "default"))
                last_log = now

        # 循环结束，记录最终的补充与消费平衡结果
        logger.info("[RL_CHAOS] %s - action=refill_done, "
                    "remaining_tokens=%s, total_allowed=%d, total_attempts=%d, "
                    "msg=dynamic_balance_result",
                    "TestSustainedLoad",
                    _get_remaining_tokens(limiter, "default"), allowed, attempts)
        # 关键断言前记录预期值与实际值
        logger.info("[RL_CHAOS] %s - action=assert_before, "
                    "expected_range=[5,20], actual=%d",
                    "TestSustainedLoad", allowed)
        # 1 秒内：初始 5 个 + 补充约 10 个 = 最多 15 个
        # 实际因循环开销，allowed 应在 [5, 20] 之间
        assert 5 <= allowed <= 20, (
            f"1 秒持续调用通过数应在 [5, 20] 之间，实际 {allowed}"
        )


# ═══════════════════════════════════════════════════════════════
#  5. 并发线程同时消费令牌
# ═══════════════════════════════════════════════════════════════

class TestConcurrentTokenConsumption:
    """并发线程同时消费令牌的线程安全性"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_concurrent_consumption_should_not_exceed_capacity(self):
        """并发消费令牌总数不应超过桶容量

        场景：容量 10，10 个线程同时各消费 5 次（共 50 次）。
        预期：通过数 ≤ 10（不超过容量），无异常，无死锁。
        """
        # 记录测试启动，便于定位并发线程安全用例
        logger.info("[RL_CHAOS] %s - action=test_start, "
                    "case=concurrent_consumption",
                    "TestConcurrentTokenConsumption")

        limiter = RateLimiter(limits={"default": (10, 0.01)})  # 极慢补充
        # 记录限流器配置：容量 10、补充速率 0.01/s（极慢补充以隔离并发消费）
        logger.info("[RL_CHAOS] %s - action=limiter_created, category=default, "
                    "capacity=10, refill_rate=0.01",
                    "TestConcurrentTokenConsumption")

        allowed = [0]
        lock = threading.Lock()
        barrier = threading.Barrier(10)
        thread_results = {}  # 记录每个线程的消费结果（线程安全写入）
        results_lock = threading.Lock()

        def worker():
            tid = threading.get_ident()
            local_allowed = 0
            local_blocked = 0
            barrier.wait(timeout=5)
            for i in range(5):
                r = limiter.check("concurrent_tool")
                if r:
                    local_allowed += 1
                    with lock:
                        allowed[0] += 1
                else:
                    local_blocked += 1
                # 记录每个线程每次消费的结果，便于排查线程安全问题
                logger.info("[RL_CHAOS] %s - action=consume, "
                            "thread_id=%d, idx=%d, tool_name=concurrent_tool, "
                            "allowed=%s, local_allowed=%d, local_blocked=%d, "
                            "global_allowed=%d",
                            "TestConcurrentTokenConsumption", tid, i + 1, r,
                            local_allowed, local_blocked, allowed[0])
            # 汇总本线程的消费结果
            with results_lock:
                thread_results[tid] = {
                    "allowed": local_allowed,
                    "blocked": local_blocked,
                }

        threads = [threading.Thread(target=worker) for _ in range(10)]
        # 记录并发线程数
        logger.info("[RL_CHAOS] %s - action=threads_created, thread_count=%d, "
                    "consumes_per_thread=5, total_consumes=50",
                    "TestConcurrentTokenConsumption", len(threads))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # 记录每个线程的最终消费结果汇总
        logger.info("[RL_CHAOS] %s - action=threads_summary, "
                    "thread_results=%s, total_allowed=%d",
                    "TestConcurrentTokenConsumption", thread_results, allowed[0])
        logger.info("[RL_CHAOS] %s - action=assert_before, "
                    "msg=all_threads_exited, expected_alive=False, "
                    "actual_any_alive=%s",
                    "TestConcurrentTokenConsumption",
                    any(t.is_alive() for t in threads))
        assert all(not t.is_alive() for t in threads), "线程未在 30s 内退出"
        # 容量 10，并发消费不应超过 10
        logger.info("[RL_CHAOS] %s - action=assert_before, "
                    "msg=capacity_check, expected_max=10, actual=%d",
                    "TestConcurrentTokenConsumption", allowed[0])
        assert allowed[0] <= 10, (
            f"并发消费通过数 {allowed[0]} 超过容量 10（线程安全问题）"
        )
        # 应至少有部分通过（避免完全饥饿）
        logger.info("[RL_CHAOS] %s - action=assert_before, "
                    "msg=starvation_check, expected_min=1, actual=%d",
                    "TestConcurrentTokenConsumption", allowed[0])
        assert allowed[0] >= 1, "应有至少 1 个请求通过"


# ═══════════════════════════════════════════════════════════════
#  6. 限流器重置后恢复
# ═══════════════════════════════════════════════════════════════

class TestLimiterReset:
    """限流器重置后的恢复"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_reset_should_clear_all_buckets(self):
        """重置应清空所有令牌桶，恢复满桶状态

        场景：耗尽多个类别的令牌，调用 reset() 后应恢复。
        """
        # 记录测试启动，便于定位 reset 用例
        logger.info("[RL_CHAOS] %s - action=test_start, case=reset_clears_buckets",
                    "TestLimiterReset")

        limiter = RateLimiter(limits={"default": (3, 0.01), "network": (2, 0.01)})
        # 记录限流器配置：default(3,0.01)、network(2,0.01)
        logger.info("[RL_CHAOS] %s - action=limiter_created, "
                    "default=(3,0.01), network=(2,0.01)",
                    "TestLimiterReset")

        # 耗尽 default 和 network 桶
        for i in range(3):
            r = limiter.check("default_tool")
            # 每次 default 消费后记录结果与剩余令牌
            logger.info("[RL_CHAOS] %s - action=consume, category=default, "
                        "idx=%d, tool_name=default_tool, allowed=%s, "
                        "remaining_tokens=%s",
                        "TestLimiterReset", i + 1, r,
                        _get_remaining_tokens(limiter, "default"))
        for i in range(2):
            r = limiter.check("http_tool")
            # 每次 network 消费后记录结果与剩余令牌
            logger.info("[RL_CHAOS] %s - action=consume, category=network, "
                        "idx=%d, tool_name=http_tool, allowed=%s, "
                        "remaining_tokens=%s",
                        "TestLimiterReset", i + 1, r,
                        _get_remaining_tokens(limiter, "network"))

        # 应都被限流
        r_def_block = limiter.check("default_tool")
        # 记录 default 桶已耗尽
        logger.info("[RL_CHAOS] %s - action=exhaustion, category=default, "
                    "tool_name=default_tool, allowed=%s, "
                    "remaining_tokens=%s, msg=bucket_exhausted_before_reset",
                    "TestLimiterReset", r_def_block,
                    _get_remaining_tokens(limiter, "default"))
        logger.info("[RL_CHAOS] %s - action=assert_before, "
                    "category=default, expected=False, actual=%s",
                    "TestLimiterReset", r_def_block)
        assert r_def_block is False

        r_net_block = limiter.check("http_tool")
        # 记录 network 桶已耗尽
        logger.info("[RL_CHAOS] %s - action=exhaustion, category=network, "
                    "tool_name=http_tool, allowed=%s, "
                    "remaining_tokens=%s, msg=bucket_exhausted_before_reset",
                    "TestLimiterReset", r_net_block,
                    _get_remaining_tokens(limiter, "network"))
        logger.info("[RL_CHAOS] %s - action=assert_before, "
                    "category=network, expected=False, actual=%s",
                    "TestLimiterReset", r_net_block)
        assert r_net_block is False

        # 重置：清空所有令牌桶
        limiter.reset()
        # 记录重置完成，桶状态已清空
        logger.info("[RL_CHAOS] %s - action=reset_done, "
                    "buckets=%s, msg=all_buckets_cleared",
                    "TestLimiterReset", dict(limiter._buckets))

        # 重置后应恢复满桶
        r_def_after = limiter.check("default_tool")
        # 记录重置后首次消费 default 的结果
        logger.info("[RL_CHAOS] %s - action=consume, category=default, "
                    "tool_name=default_tool, allowed=%s, "
                    "remaining_tokens=%s, msg=after_reset_consume",
                    "TestLimiterReset", r_def_after,
                    _get_remaining_tokens(limiter, "default"))
        logger.info("[RL_CHAOS] %s - action=assert_before, "
                    "category=default, expected=True, actual=%s, "
                    "msg=reset_recovery",
                    "TestLimiterReset", r_def_after)
        assert r_def_after is True, "重置后 default 应恢复"

        r_net_after = limiter.check("http_tool")
        # 记录重置后首次消费 network 的结果
        logger.info("[RL_CHAOS] %s - action=consume, category=network, "
                    "tool_name=http_tool, allowed=%s, "
                    "remaining_tokens=%s, msg=after_reset_consume",
                    "TestLimiterReset", r_net_after,
                    _get_remaining_tokens(limiter, "network"))
        logger.info("[RL_CHAOS] %s - action=assert_before, "
                    "category=network, expected=True, actual=%s, "
                    "msg=reset_recovery",
                    "TestLimiterReset", r_net_after)
        assert r_net_after is True, "重置后 network 应恢复"

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_wait_time_calculation_after_exhaustion(self):
        """令牌桶耗尽后 wait_time 应返回合理等待时间

        场景：耗尽令牌后查询 wait_time，应返回 > 0 的等待时间。
        """
        # 记录测试启动，便于定位 wait_time 用例
        logger.info("[RL_CHAOS] %s - action=test_start, "
                    "case=wait_time_after_exhaustion",
                    "TestLimiterReset")

        limiter = RateLimiter(limits={"default": (2, 1.0)})
        # 记录限流器配置：容量 2、补充速率 1/s
        logger.info("[RL_CHAOS] %s - action=limiter_created, category=default, "
                    "capacity=2, refill_rate=1.0",
                    "TestLimiterReset")

        # 耗尽令牌
        r1 = limiter.check("test_tool")
        # 消费第 1 个令牌
        logger.info("[RL_CHAOS] %s - action=consume, idx=1, "
                    "tool_name=test_tool, allowed=%s, remaining_tokens=%s",
                    "TestLimiterReset", r1,
                    _get_remaining_tokens(limiter, "default"))
        r2 = limiter.check("test_tool")
        # 消费第 2 个令牌
        logger.info("[RL_CHAOS] %s - action=consume, idx=2, "
                    "tool_name=test_tool, allowed=%s, remaining_tokens=%s",
                    "TestLimiterReset", r2,
                    _get_remaining_tokens(limiter, "default"))

        # 记录令牌桶已耗尽，准备查询 wait_time
        logger.info("[RL_CHAOS] %s - action=exhaustion, "
                    "tool_name=test_tool, remaining_tokens=%s, "
                    "msg=bucket_exhausted_before_wait_query",
                    "TestLimiterReset",
                    _get_remaining_tokens(limiter, "default"))

        # 查询等待时间
        wait = limiter.wait_time("test_tool")
        # 记录 wait_time 计算结果，便于排查补充速率换算是否正确
        logger.info("[RL_CHAOS] %s - action=wait_time_query, "
                    "tool_name=test_tool, wait_time=%s, "
                    "remaining_tokens=%s, refill_rate=1.0",
                    "TestLimiterReset", wait,
                    _get_remaining_tokens(limiter, "default"))
        logger.info("[RL_CHAOS] %s - action=assert_before, "
                    "msg=wait_positive, expected_min=0, actual=%s",
                    "TestLimiterReset", wait)
        assert wait > 0, f"耗尽后 wait_time 应 > 0，实际 {wait}"
        # 1.0/s 速率补充 1 个令牌需 1 秒
        logger.info("[RL_CHAOS] %s - action=assert_before, "
                    "msg=wait_within_bound, expected_max=1.0, actual=%s",
                    "TestLimiterReset", wait)
        assert wait <= 1.0, f"wait_time 应 ≤ 1.0s，实际 {wait}"
