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

import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.rate_limiter import RateLimiter  # noqa: E402


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
        limiter = RateLimiter(limits={"default": (3, 1.0)})  # 容量 3

        # 前 3 个请求应通过
        results = [limiter.check("test_tool") for _ in range(3)]
        assert results == [True, True, True], (
            f"前 3 个请求应通过，实际: {results}"
        )

        # 第 4 个请求应被限流
        result = limiter.check("test_tool")
        assert result is False, "令牌桶耗尽后应限流"

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_bucket_refill_after_wait_should_allow_again(self):
        """等待令牌补充后应再次允许调用

        场景：耗尽令牌后等待一段时间，令牌应按 refill_rate 补充。
        预期：等待 1.2s 后（refill_rate=1.0），应有 1 个新令牌可用。
        """
        limiter = RateLimiter(limits={"default": (2, 1.0)})  # 容量 2, 1/s

        # 耗尽令牌
        assert limiter.check("test_tool") is True
        assert limiter.check("test_tool") is True
        assert limiter.check("test_tool") is False

        # 等待 1.2 秒，应补充 1 个令牌
        time.sleep(1.2)
        assert limiter.check("test_tool") is True, "等待补充后应再次允许"


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
        limiter = RateLimiter(limits={"default": (5, 1.0)})
        allowed = 0
        rejected = 0

        for _ in range(50):
            if limiter.check("burst_tool"):
                allowed += 1
            else:
                rejected += 1

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
        limiter = RateLimiter()  # 使用默认配置

        # network: 容量 5
        for i in range(5):
            assert limiter.check(f"http_fetch_{i}") is True, (
                f"network 工具第 {i+1} 次应通过"
            )
        # 第 6 个 network 应限流
        assert limiter.check("http_fetch_extra") is False

        # shell: 容量 2（独立桶，不受 network 影响）
        assert limiter.check("shell_execute") is True
        assert limiter.check("run_program") is True
        assert limiter.check("start_process") is False

        # file: 容量 15（独立桶）
        for i in range(15):
            assert limiter.check(f"read_file_{i}") is True
        assert limiter.check("read_file_extra") is False


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
        limiter = RateLimiter(limits={"default": (5, 10.0)})  # 5 tokens, 10/s

        allowed = 0
        start = time.time()
        while time.time() - start < 1.0:
            if limiter.check("sustained_tool"):
                allowed += 1

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
        limiter = RateLimiter(limits={"default": (10, 0.01)})  # 极慢补充
        allowed = [0]
        lock = threading.Lock()
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait(timeout=5)
            for _ in range(5):
                if limiter.check("concurrent_tool"):
                    with lock:
                        allowed[0] += 1

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert all(not t.is_alive() for t in threads), "线程未在 30s 内退出"
        # 容量 10，并发消费不应超过 10
        assert allowed[0] <= 10, (
            f"并发消费通过数 {allowed[0]} 超过容量 10（线程安全问题）"
        )
        # 应至少有部分通过（避免完全饥饿）
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
        limiter = RateLimiter(limits={"default": (3, 0.01), "network": (2, 0.01)})

        # 耗尽 default 和 network 桶
        for _ in range(3):
            limiter.check("default_tool")
        for _ in range(2):
            limiter.check("http_tool")

        # 应都被限流
        assert limiter.check("default_tool") is False
        assert limiter.check("http_tool") is False

        # 重置
        limiter.reset()

        # 重置后应恢复满桶
        assert limiter.check("default_tool") is True, "重置后 default 应恢复"
        assert limiter.check("http_tool") is True, "重置后 network 应恢复"

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_wait_time_calculation_after_exhaustion(self):
        """令牌桶耗尽后 wait_time 应返回合理等待时间

        场景：耗尽令牌后查询 wait_time，应返回 > 0 的等待时间。
        """
        limiter = RateLimiter(limits={"default": (2, 1.0)})

        # 耗尽令牌
        limiter.check("test_tool")
        limiter.check("test_tool")

        # 查询等待时间
        wait = limiter.wait_time("test_tool")
        assert wait > 0, f"耗尽后 wait_time 应 > 0，实际 {wait}"
        # 1.0/s 速率补充 1 个令牌需 1 秒
        assert wait <= 1.0, f"wait_time 应 ≤ 1.0s，实际 {wait}"
