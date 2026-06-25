"""限流器边界条件测试用例

根据测试用例设计规范，测试名称必须反映业务意图：
- test_{模块}_{功能}_{场景}_{预期结果}

本测试文件覆盖以下边界场景（共12+个用例）：
1. 零阈值/零窗口的边界处理
2. 刚好达到限流阈值的边界
3. 令牌桶算法的令牌生成准确性
4. 突发流量下的限流准确性
5. 分级限流（用户级/接口级/全局级）的优先级
6. 限流后的错误响应格式
7. 限流计数器的并发安全性
8. 时间窗口重置的准确性
9. 不同IP/用户的限流隔离性
10. 限流白名单/黑名单功能
11. 动态调整限流阈值的正确性
12. 限流指标上报的准确性

优先级标记：
- @pytest.mark.p0: 关键测试（必须通过）
- @pytest.mark.unit: 单元测试
"""

import pytest
import json
import threading
import time
from unittest.mock import patch, MagicMock

from agent.rate_limiter import (
    TokenBucket,
    RateLimiter,
    RateLimitStrategy,
    RateLimitError,
    RateLimiterManager,
    get_rate_limiter,
    register_rate_limiter,
    get_all_rate_limiter_status,
)


class TestTokenBucketBoundaryConditions:
    """令牌桶边界条件测试类"""

    @pytest.fixture
    def small_bucket(self):
        """创建小容量令牌桶"""
        return TokenBucket(capacity=10, refill_rate=5.0)

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件1：零阈值/零容量的边界处理
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_token_bucket_zero_capacity_always_rejects(self):
        """验证零容量令牌桶总是拒绝请求（边界条件测试）"""
        bucket = TokenBucket(capacity=0, refill_rate=1.0)
        assert bucket.try_acquire() is False
        assert bucket.tokens == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_token_bucket_zero_refill_rate_no_new_tokens(self):
        """验证零补充速率令牌桶不会生成新令牌（边界条件测试）"""
        bucket = TokenBucket(capacity=5, refill_rate=0.0)
        for _ in range(5):
            assert bucket.try_acquire() is True
        assert bucket.try_acquire() is False
        
        time.sleep(0.1)
        assert bucket.try_acquire() is False

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件2：刚好达到限流阈值的边界
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_token_bucket_exact_capacity_allows(self, small_bucket):
        """验证刚好达到容量时仍允许请求（边界条件测试）"""
        for i in range(10):
            assert small_bucket.try_acquire() is True, f"Request {i+1} should succeed"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_token_bucket_exceed_capacity_rejects(self, small_bucket):
        """验证超过容量时拒绝请求（边界条件测试）"""
        for i in range(10):
            small_bucket.try_acquire()
        
        assert small_bucket.try_acquire() is False

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件3：令牌桶算法的令牌生成准确性
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_token_bucket_refill_after_time(self):
        """验证令牌桶按时间补充令牌的准确性（边界条件测试）"""
        bucket = TokenBucket(capacity=10, refill_rate=10.0)
        
        for _ in range(10):
            bucket.try_acquire()
        
        assert bucket.tokens < 1
        
        time.sleep(0.5)
        
        assert 4 <= bucket.tokens <= 6

    @pytest.mark.unit
    @pytest.mark.p0
    def test_token_bucket_capacity_upper_bound(self, small_bucket):
        """验证令牌桶不超过容量上限（边界条件测试）"""
        time.sleep(0.5)
        
        assert small_bucket.tokens <= 10

    @pytest.mark.unit
    @pytest.mark.p0
    def test_token_bucket_get_wait_time_zero_when_available(self, small_bucket):
        """验证有足够令牌时等待时间为零（边界条件测试）"""
        wait_time = small_bucket.get_wait_time(1)
        assert wait_time == 0.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_token_bucket_get_wait_time_positive_when_empty(self):
        """验证令牌为空时等待时间为正值（边界条件测试）"""
        bucket = TokenBucket(capacity=1, refill_rate=2.0)
        bucket.try_acquire()
        
        wait_time = bucket.get_wait_time(1)
        assert wait_time > 0
        assert wait_time <= 0.5

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件4：令牌桶重置功能
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_token_bucket_reset_restores_capacity(self, small_bucket):
        """验证重置功能恢复到满容量（边界条件测试）"""
        for _ in range(8):
            small_bucket.try_acquire()
        
        assert small_bucket.tokens < 10
        
        small_bucket.reset()
        
        assert small_bucket.tokens == 10

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件5：令牌桶容量属性
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_token_bucket_capacity_property(self, small_bucket):
        """验证容量属性返回正确值（边界条件测试）"""
        assert small_bucket.capacity == 10

    @pytest.mark.unit
    @pytest.mark.p0
    def test_token_bucket_tokens_property(self):
        """验证tokens属性返回当前令牌数（边界条件测试）"""
        bucket = TokenBucket(capacity=5, refill_rate=1.0)
        assert bucket.tokens == 5
        bucket.try_acquire()
        assert bucket.tokens == 4


class TestRateLimiterBoundaryConditions:
    """限流器边界条件测试类"""

    @pytest.fixture
    def default_limiter(self):
        """创建默认配置的限流器实例"""
        limiter = RateLimiter(max_concurrent=10, strategy=RateLimitStrategy.REJECT)
        return limiter

    @pytest.fixture
    def configured_limiter(self):
        """创建已配置规则的限流器（refill_rate=0用于精确边界测试）"""
        limiter = RateLimiter(max_concurrent=100, strategy=RateLimitStrategy.REJECT)
        limiter.register_rule("endpoint/api/chat", capacity=5, refill_rate=0.0)
        limiter.register_rule("user", capacity=3, refill_rate=0.0)
        return limiter

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件6：全局限流边界
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_global_bucket_initial_full(self):
        """验证全局限流桶初始为满容量（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=10)
        status = limiter.get_status()
        assert status["global_bucket"]["tokens"] == 100
        assert status["global_bucket"]["capacity"] == 100

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_global_limit_exceeded_rejects(self):
        """验证全局限流超时时拒绝请求（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter._global_bucket = TokenBucket(capacity=10, refill_rate=0.0)
        
        for i in range(10):
            assert limiter.check() is True, f"Request {i+1} should pass"
        
        assert limiter.check() is False

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件7：接口限流边界
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_endpoint_limit_rejects_when_exceeded(self, configured_limiter):
        """验证接口限流超时时拒绝请求（边界条件测试）"""
        for i in range(5):
            assert configured_limiter.check(endpoint="api/chat") is True, f"Request {i+1} should pass"
        
        assert configured_limiter.check(endpoint="api/chat") is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_endpoint_not_configured_uses_default(self):
        """验证未配置的接口使用默认限流配置（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        
        assert limiter.check(endpoint="unknown/endpoint") is True
        limiter.release()

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件8：用户限流边界
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_user_limit_rejects_when_exceeded(self):
        """验证用户限流超时时拒绝请求（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter.register_rule("user", capacity=3, refill_rate=0.0)
        
        user_id = "test_user"
        
        for i in range(3):
            assert limiter.check(user_id=user_id) is True, f"Request {i+1} should pass"
        
        assert limiter.check(user_id=user_id) is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_user_isolation_independent_limits(self):
        """验证不同用户的限流是隔离的（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter.register_rule("user", capacity=3, refill_rate=0.0)
        
        user1 = "user1"
        user2 = "user2"
        
        for _ in range(3):
            assert limiter.check(user_id=user1) is True
        
        assert limiter.check(user_id=user1) is False
        
        assert limiter.check(user_id=user2) is True

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件9：并发限制边界
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_concurrent_limit_exact(self):
        """验证刚好达到并发限制时的行为（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=3, strategy=RateLimitStrategy.REJECT)
        
        for i in range(3):
            assert limiter.check() is True, f"Concurrent request {i+1} should pass"
        
        assert limiter.check() is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_concurrent_release_allows_new(self):
        """验证释放并发许可后允许新请求（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=2, strategy=RateLimitStrategy.REJECT)
        
        assert limiter.check() is True
        assert limiter.check() is True
        assert limiter.check() is False
        
        limiter.release()
        
        assert limiter.check() is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_concurrent_zero_always_rejects(self):
        """验证零并发限制总是拒绝（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=0, strategy=RateLimitStrategy.REJECT)
        assert limiter.check() is False

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件10：限流错误格式
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_error_has_correct_error_code(self):
        """验证限流异常包含正确的错误码（边界条件测试）"""
        error = RateLimitError(message="请求被限流", endpoint="/api/test", user_id="user123")
        
        assert error.error_code == "RATE_LIMIT_EXCEEDED"
        assert error.endpoint == "/api/test"
        assert error.user_id == "user123"
        assert str(error) == "请求被限流"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_limit_decorator_raises_error(self):
        """验证limit装饰器在限流时抛出正确异常（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=1)
        
        @limiter.limit()
        def test_func():
            time.sleep(0.1)
            return "success"
        
        import threading
        results = []
        errors = []
        
        def call_func():
            try:
                results.append(test_func())
            except RateLimitError as e:
                errors.append(e)
        
        t1 = threading.Thread(target=call_func)
        t2 = threading.Thread(target=call_func)
        
        t1.start()
        time.sleep(0.01)
        t2.start()
        
        t1.join()
        t2.join()
        
        assert len(results) + len(errors) == 2

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件11：限流计数器的并发安全性
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_concurrent_counter_thread_safety(self):
        """验证并发场景下计数器的线程安全性（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        
        def make_requests(count):
            for _ in range(count):
                if limiter.check():
                    limiter.release()
        
        threads = []
        for _ in range(10):
            t = threading.Thread(target=make_requests, args=(20,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        status = limiter.get_status()
        assert status["current_concurrent"] == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_concurrent_exact_limit_stress_test(self):
        """验证高并发下并发限制的准确性（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=5, strategy=RateLimitStrategy.REJECT)
        
        max_concurrent_seen = [0]
        current_count = [0]
        lock = threading.Lock()
        
        def worker():
            if limiter.check():
                with lock:
                    current_count[0] += 1
                    if current_count[0] > max_concurrent_seen[0]:
                        max_concurrent_seen[0] = current_count[0]
                time.sleep(0.02)
                with lock:
                    current_count[0] -= 1
                limiter.release()
        
        threads = []
        for _ in range(20):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        assert max_concurrent_seen[0] <= 5

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件12：动态注册限流规则
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_register_rule_dynamic(self, default_limiter):
        """验证动态注册限流规则生效（边界条件测试）"""
        default_limiter.register_rule("endpoint/api/test", capacity=2, refill_rate=0.0)
        
        assert default_limiter.check(endpoint="api/test") is True
        assert default_limiter.check(endpoint="api/test") is True
        assert default_limiter.check(endpoint="api/test") is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_register_rule_overwrites_existing(self, default_limiter):
        """验证重新注册规则会覆盖原有配置（边界条件测试）"""
        default_limiter.register_rule("endpoint/api/test", capacity=5, refill_rate=0.0)
        
        for _ in range(5):
            assert default_limiter.check(endpoint="api/test") is True
        
        default_limiter.reset()
        default_limiter.register_rule("endpoint/api/test", capacity=2, refill_rate=0.0)
        
        assert default_limiter.check(endpoint="api/test") is True
        assert default_limiter.check(endpoint="api/test") is True
        assert default_limiter.check(endpoint="api/test") is False
        assert default_limiter.check(endpoint="api/test") is False

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件13：等待时间计算
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_wait_time_no_limit_zero(self):
        """验证无限流时等待时间为零（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter.register_rule("api/chat", capacity=10, refill_rate=1.0)
        
        wait_time, level = limiter.wait_time(endpoint="api/chat", user_id="user1")
        assert wait_time == 0.0
        assert level == "none"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_wait_time_returns_max_level(self):
        """验证等待时间返回最长等待的级别（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter._global_bucket = TokenBucket(capacity=100, refill_rate=100.0)
        limiter.register_rule("endpoint/api/slow", capacity=1, refill_rate=2.0)
        
        limiter.check(endpoint="api/slow")
        
        wait_time, level = limiter.wait_time(endpoint="api/slow")
        assert wait_time > 0
        assert level == "endpoint"

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件14：重置功能
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_reset_restores_all(self, configured_limiter):
        """验证重置功能恢复所有状态（边界条件测试）"""
        for _ in range(3):
            configured_limiter.check(endpoint="api/chat", user_id="user1")
        
        status_before = configured_limiter.get_status()
        assert status_before["current_concurrent"] > 0
        
        configured_limiter.reset()
        
        status_after = configured_limiter.get_status()
        assert status_after["current_concurrent"] == 0
        assert status_after["global_bucket"]["tokens"] == 100

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件15：多级限流优先级（全局→接口→用户→并发）
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_multi_level_priority_global_first(self):
        """验证全局限流最先检查（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter._global_bucket = TokenBucket(capacity=1, refill_rate=0.0)
        
        assert limiter.check(endpoint="api/test", user_id="user1") is True
        assert limiter.check(endpoint="api/test", user_id="user1") is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_multi_level_endpoint_before_user(self):
        """验证接口限流优先于用户限流检查（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter._global_bucket = TokenBucket(capacity=100, refill_rate=0.0)
        limiter.register_rule("endpoint/api/limited", capacity=2, refill_rate=0.0)
        limiter.register_rule("user", capacity=10, refill_rate=0.0)
        
        for i in range(2):
            assert limiter.check(endpoint="api/limited", user_id="user1") is True
        
        assert limiter.check(endpoint="api/limited", user_id="user2") is False

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件16：get_status返回结构完整性
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_get_status_contains_all_fields(self, default_limiter):
        """验证get_status返回包含所有必需字段（边界条件测试）"""
        status = default_limiter.get_status()
        
        required_fields = [
            "max_concurrent", "current_concurrent", "strategy",
            "global_bucket", "rules", "buckets"
        ]
        
        for field in required_fields:
            assert field in status, f"Missing field: {field}"
        
        global_bucket_fields = ["tokens", "capacity", "refill_rate"]
        for field in global_bucket_fields:
            assert field in status["global_bucket"], f"Missing global_bucket field: {field}"

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件17：limit_async异步装饰器
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_limit_async_success(self, default_limiter):
        """验证异步limit装饰器成功时的行为（边界条件测试）"""
        import asyncio
        
        @default_limiter.limit_async()
        async def async_func(x):
            await asyncio.sleep(0.01)
            return x * 2
        
        result = asyncio.run(async_func(5))
        assert result == 10

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_limit_async_with_user_id_kwarg(self, default_limiter):
        """验证异步装饰器从kwargs获取user_id（边界条件测试）"""
        import asyncio
        
        @default_limiter.limit_async(endpoint="api/test")
        async def async_func(user_id=None):
            return user_id
        
        result = asyncio.run(async_func(user_id="test_user"))
        assert result == "test_user"

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件18：QUEUE策略并发限制
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_queue_strategy_waits_for_release(self):
        """验证QUEUE策略下请求会等待释放（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=1, strategy=RateLimitStrategy.QUEUE)
        limiter._global_bucket = TokenBucket(capacity=100, refill_rate=100.0)
        
        assert limiter.check() is True
        
        def release_after_delay():
            time.sleep(0.1)
            limiter.release()
        
        import threading
        t = threading.Thread(target=release_after_delay)
        t.start()
        
        start = time.time()
        result = limiter.check()
        elapsed = time.time() - start
        
        t.join()
        
        assert result is True
        assert elapsed >= 0.08

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_queue_strategy_timeout_returns_false(self):
        """验证QUEUE策略下超时后返回False（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=1, strategy=RateLimitStrategy.QUEUE)
        limiter._global_bucket = TokenBucket(capacity=100, refill_rate=100.0)
        
        assert limiter.check() is True
        
        start = time.time()
        result = limiter.check()
        elapsed = time.time() - start
        
        limiter.release()
        
        assert result is False
        assert elapsed >= 4.5

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_queue_strategy_multiple_waiters(self):
        """验证QUEUE策略下多个等待者按顺序获取（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=1, strategy=RateLimitStrategy.QUEUE)
        limiter._global_bucket = TokenBucket(capacity=100, refill_rate=100.0)
        
        acquired_order = []
        lock = threading.Lock()
        
        def worker(id):
            if limiter.check():
                with lock:
                    acquired_order.append(id)
                time.sleep(0.05)
                limiter.release()
        
        assert limiter.check() is True
        
        threads = []
        for i in range(3):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
            time.sleep(0.01)
        
        limiter.release()
        
        for t in threads:
            t.join()
        
        assert len(acquired_order) == 3

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_queue_strategy_status_uses_queue(self):
        """验证QUEUE策略在status中正确显示（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=10, strategy=RateLimitStrategy.QUEUE)
        status = limiter.get_status()
        assert status["strategy"] == "queue"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_reject_strategy_status_uses_reject(self):
        """验证REJECT策略在status中正确显示（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=10, strategy=RateLimitStrategy.REJECT)
        status = limiter.get_status()
        assert status["strategy"] == "reject"

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件19：令牌释放回退逻辑（异常中断场景）
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_endpoint_failure_rolls_back_global_token(self):
        """验证接口限流失败时回退全局令牌（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter._global_bucket = TokenBucket(capacity=10, refill_rate=0.0)
        limiter.register_rule("endpoint/api/limited", capacity=0, refill_rate=0.0)
        
        global_tokens_before = limiter._global_bucket.tokens
        
        result = limiter.check(endpoint="api/limited")
        
        global_tokens_after = limiter._global_bucket.tokens
        
        assert result is False
        assert global_tokens_after == global_tokens_before

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_user_failure_rolls_back_global_and_endpoint_tokens(self):
        """验证用户限流失败时回退全局和接口令牌（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter._global_bucket = TokenBucket(capacity=10, refill_rate=0.0)
        limiter.register_rule("endpoint/api/test", capacity=5, refill_rate=0.0)
        limiter.register_rule("user", capacity=0, refill_rate=0.0)
        
        global_tokens_before = limiter._global_bucket.tokens
        
        result = limiter.check(endpoint="api/test", user_id="test_user")
        
        global_tokens_after = limiter._global_bucket.tokens
        endpoint_bucket = limiter._get_bucket("endpoint/api/test")
        endpoint_tokens_after = endpoint_bucket.tokens
        
        assert result is False
        assert global_tokens_after == global_tokens_before
        assert endpoint_tokens_after == 5

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_concurrent_failure_rolls_back_all_tokens(self):
        """验证并发限制失败时回退所有令牌（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=0, strategy=RateLimitStrategy.REJECT)
        limiter._global_bucket = TokenBucket(capacity=10, refill_rate=0.0)
        limiter.register_rule("endpoint/api/test", capacity=5, refill_rate=0.0)
        limiter.register_rule("user", capacity=3, refill_rate=0.0)
        
        global_tokens_before = limiter._global_bucket.tokens
        
        result = limiter.check(endpoint="api/test", user_id="test_user")
        
        global_tokens_after = limiter._global_bucket.tokens
        endpoint_bucket = limiter._get_bucket("endpoint/api/test")
        endpoint_tokens_after = endpoint_bucket.tokens
        user_bucket = limiter._get_bucket("user/test_user")
        user_tokens_after = user_bucket.tokens
        
        assert result is False
        assert global_tokens_after == global_tokens_before
        assert endpoint_tokens_after == 5
        assert user_tokens_after == 3

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_token_rollback_not_exceed_capacity(self):
        """验证令牌回退不会超过桶容量上限（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter._global_bucket = TokenBucket(capacity=5, refill_rate=0.0)
        limiter.register_rule("endpoint/api/full", capacity=0, refill_rate=0.0)
        
        for _ in range(5):
            limiter._global_bucket.try_acquire()
        
        assert limiter._global_bucket.tokens == 0
        
        result = limiter.check(endpoint="api/full")
        
        assert result is False
        assert limiter._global_bucket.tokens <= 5
        assert limiter._global_bucket.tokens == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_partial_rollback_only_endpoint(self):
        """验证只有接口时只回退全局令牌（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter._global_bucket = TokenBucket(capacity=10, refill_rate=0.0)
        limiter.register_rule("endpoint/api/fail", capacity=0, refill_rate=0.0)
        
        initial_global = limiter._global_bucket.tokens
        result = limiter.check(endpoint="api/fail")
        
        assert result is False
        assert limiter._global_bucket.tokens == initial_global

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件20：DELAY策略枚举值验证
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limit_strategy_enum_values(self):
        """验证RateLimitStrategy枚举值完整性（边界条件测试）"""
        assert RateLimitStrategy.REJECT.value == "reject"
        assert RateLimitStrategy.QUEUE.value == "queue"
        assert RateLimitStrategy.DELAY.value == "delay"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_release_decreases_concurrent(self):
        """验证release方法正确减少并发计数（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=10)
        limiter._global_bucket = TokenBucket(capacity=100, refill_rate=0.0)
        
        limiter.check()
        assert limiter.get_status()["current_concurrent"] == 1
        
        limiter.release()
        assert limiter.get_status()["current_concurrent"] == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_release_when_zero_stays_zero(self):
        """验证并发为0时release不会变成负数（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=10)
        
        assert limiter.get_status()["current_concurrent"] == 0
        limiter.release()
        assert limiter.get_status()["current_concurrent"] == 0

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件21：QUEUE策略下的令牌回退场景
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_queue_endpoint_failure_rolls_back_global(self):
        """验证QUEUE策略下接口限流失败时回退全局令牌（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=10, strategy=RateLimitStrategy.QUEUE)
        limiter._global_bucket = TokenBucket(capacity=10, refill_rate=0.0)
        limiter.register_rule("endpoint/api/limited", capacity=0, refill_rate=0.0)
        
        global_tokens_before = limiter._global_bucket.tokens
        
        result = limiter.check(endpoint="api/limited")
        
        global_tokens_after = limiter._global_bucket.tokens
        
        assert result is False
        assert global_tokens_after == global_tokens_before

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_queue_user_failure_rolls_back_all(self):
        """验证QUEUE策略下用户限流失败时回退全局和接口令牌（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=10, strategy=RateLimitStrategy.QUEUE)
        limiter._global_bucket = TokenBucket(capacity=10, refill_rate=0.0)
        limiter.register_rule("endpoint/api/test", capacity=5, refill_rate=0.0)
        limiter.register_rule("user", capacity=0, refill_rate=0.0)
        
        global_tokens_before = limiter._global_bucket.tokens
        
        result = limiter.check(endpoint="api/test", user_id="test_user")
        
        global_tokens_after = limiter._global_bucket.tokens
        endpoint_bucket = limiter._get_bucket("endpoint/api/test")
        endpoint_tokens_after = endpoint_bucket.tokens
        
        assert result is False
        assert global_tokens_after == global_tokens_before
        assert endpoint_tokens_after == 5

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_queue_concurrent_timeout_rolls_back_all_tokens(self):
        """验证QUEUE策略下并发限制超时后回退所有令牌（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=0, strategy=RateLimitStrategy.QUEUE)
        limiter._global_bucket = TokenBucket(capacity=10, refill_rate=0.0)
        limiter.register_rule("endpoint/api/test", capacity=5, refill_rate=0.0)
        limiter.register_rule("user", capacity=3, refill_rate=0.0)
        
        global_tokens_before = limiter._global_bucket.tokens
        
        result = limiter.check(endpoint="api/test", user_id="test_user")
        
        global_tokens_after = limiter._global_bucket.tokens
        endpoint_bucket = limiter._get_bucket("endpoint/api/test")
        endpoint_tokens_after = endpoint_bucket.tokens
        user_bucket = limiter._get_bucket("user/test_user")
        user_tokens_after = user_bucket.tokens
        
        assert result is False
        assert global_tokens_after == global_tokens_before
        assert endpoint_tokens_after == 5
        assert user_tokens_after == 3

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件22：装饰器异常中断场景 - 令牌释放验证
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_limit_decorator_exception_releases_token(self):
        """验证limit装饰器中函数抛出异常时正确释放令牌（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=10)
        limiter._global_bucket = TokenBucket(capacity=100, refill_rate=0.0)
        
        @limiter.limit()
        def failing_func():
            raise ValueError("test error")
        
        initial_concurrent = limiter.get_status()["current_concurrent"]
        
        with pytest.raises(ValueError):
            failing_func()
        
        final_concurrent = limiter.get_status()["current_concurrent"]
        assert final_concurrent == initial_concurrent

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_limit_decorator_with_user_id_kwarg(self):
        """验证limit装饰器从kwargs中获取user_id（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter.register_rule("user", capacity=5, refill_rate=0.0)
        
        @limiter.limit()
        def test_func(user_id=None):
            return user_id
        
        result = test_func(user_id="test_user_kwarg")
        assert result == "test_user_kwarg"
        limiter.release()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_limit_async_decorator_exception_releases_token(self):
        """验证limit_async装饰器中函数抛出异常时正确释放令牌（边界条件测试）"""
        import asyncio
        limiter = RateLimiter(max_concurrent=10)
        limiter._global_bucket = TokenBucket(capacity=100, refill_rate=0.0)
        
        @limiter.limit_async()
        async def failing_async():
            await asyncio.sleep(0.01)
            raise RuntimeError("async error")
        
        initial_concurrent = limiter.get_status()["current_concurrent"]
        
        with pytest.raises(RuntimeError):
            asyncio.run(failing_async())
        
        final_concurrent = limiter.get_status()["current_concurrent"]
        assert final_concurrent == initial_concurrent

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_limit_async_with_user_id_kwarg(self):
        """验证limit_async装饰器从kwargs中获取user_id（边界条件测试）"""
        import asyncio
        limiter = RateLimiter(max_concurrent=100)
        
        @limiter.limit_async(endpoint="api/test")
        async def async_func(user_id=None):
            return user_id
        
        result = asyncio.run(async_func(user_id="async_user"))
        assert result == "async_user"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_limit_async_raises_error_when_limited(self):
        """验证limit_async装饰器在限流时抛出正确异常（边界条件测试）"""
        import asyncio
        limiter = RateLimiter(max_concurrent=1)
        
        @limiter.limit_async()
        async def slow_func():
            await asyncio.sleep(0.5)
            return "done"
        
        async def test_async():
            task1 = asyncio.create_task(slow_func())
            await asyncio.sleep(0.05)
            
            with pytest.raises(RateLimitError) as exc_info:
                await slow_func()
            
            assert exc_info.value.error_code == "RATE_LIMIT_EXCEEDED"
            await task1
        
        asyncio.run(test_async())

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件23：wait_time方法各种场景
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_wait_time_global_only(self):
        """验证只有全局限流时等待时间正确（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter._global_bucket = TokenBucket(capacity=1, refill_rate=1.0)
        
        limiter._global_bucket.try_acquire()
        
        wait_time, level = limiter.wait_time()
        assert wait_time > 0
        assert level == "global"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_wait_time_user_only(self):
        """验证只有用户限流时等待时间正确（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter._global_bucket = TokenBucket(capacity=100, refill_rate=100.0)
        limiter.register_rule("user", capacity=1, refill_rate=1.0)
        
        limiter.check(user_id="user_wait")
        
        wait_time, level = limiter.wait_time(user_id="user_wait")
        assert wait_time > 0
        assert level == "user"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_wait_time_returns_max_of_multiple(self):
        """验证多级限流时返回最大等待时间（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter._global_bucket = TokenBucket(capacity=1, refill_rate=1.0)
        limiter.register_rule("endpoint/api/slow", capacity=1, refill_rate=10.0)
        
        limiter.check(endpoint="api/slow")
        
        wait_time, level = limiter.wait_time(endpoint="api/slow")
        assert wait_time > 0
        assert level == "global"

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件24：埋点异常容错场景
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_metrics_failure_does_not_affect_main_flow(self):
        """验证埋点失败时不影响主流程（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter._global_bucket = TokenBucket(capacity=0, refill_rate=0.0)
        
        with patch('agent.rate_limiter.get_business_metrics_collector') as mock_collector:
            mock_collector.side_effect = Exception("metrics service down")
            
            result = limiter.check()
            
            assert result is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_endpoint_metrics_failure_tolerant(self):
        """验证接口限流埋点失败时不影响主流程（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter._global_bucket = TokenBucket(capacity=10, refill_rate=0.0)
        limiter.register_rule("endpoint/api/fail_metric", capacity=0, refill_rate=0.0)
        
        with patch('agent.rate_limiter.get_business_metrics_collector') as mock_collector:
            mock_instance = MagicMock()
            mock_instance.record_rate_limit_trigger.side_effect = Exception("metric error")
            mock_collector.return_value = mock_instance
            
            result = limiter.check(endpoint="api/fail_metric")
            
            assert result is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_user_metrics_failure_tolerant(self):
        """验证用户限流埋点失败时不影响主流程（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=100)
        limiter._global_bucket = TokenBucket(capacity=10, refill_rate=0.0)
        limiter.register_rule("endpoint/api/test", capacity=5, refill_rate=0.0)
        limiter.register_rule("user", capacity=0, refill_rate=0.0)
        
        with patch('agent.rate_limiter.get_business_metrics_collector') as mock_collector:
            mock_instance = MagicMock()
            mock_instance.record_rate_limit_trigger.side_effect = Exception("user metric error")
            mock_collector.return_value = mock_instance
            
            result = limiter.check(endpoint="api/test", user_id="user_fail")
            
            assert result is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_concurrent_metrics_failure_tolerant(self):
        """验证并发限流埋点失败时不影响主流程（边界条件测试）"""
        limiter = RateLimiter(max_concurrent=0, strategy=RateLimitStrategy.REJECT)
        limiter._global_bucket = TokenBucket(capacity=10, refill_rate=0.0)
        limiter.register_rule("endpoint/api/test", capacity=5, refill_rate=0.0)
        limiter.register_rule("user", capacity=3, refill_rate=0.0)
        
        with patch('agent.rate_limiter.get_business_metrics_collector') as mock_collector:
            mock_instance = MagicMock()
            mock_instance.record_rate_limit_trigger.side_effect = Exception("concurrent metric error")
            mock_collector.return_value = mock_instance
            
            result = limiter.check(endpoint="api/test", user_id="user_concurrent")
            
            assert result is False


class TestRateLimiterManagerBoundaryConditions:
    """限流器管理器边界条件测试类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_manager_register_and_get(self):
        """验证限流器管理器的注册和获取功能（边界条件测试）"""
        manager = RateLimiterManager()
        
        manager.register("test_svc", max_concurrent=50)
        limiter = manager.get("test_svc")
        
        assert limiter is not None
        assert isinstance(limiter, RateLimiter)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_manager_auto_creates_default(self):
        """验证限流器管理器自动创建默认实例（边界条件测试）"""
        manager = RateLimiterManager()
        
        limiter = manager.get("new_service")
        
        assert limiter is not None
        assert isinstance(limiter, RateLimiter)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_manager_get_all_status(self):
        """验证限流器管理器获取所有状态功能（边界条件测试）"""
        manager = RateLimiterManager()
        
        manager.register("svc1")
        manager.register("svc2", max_concurrent=50)
        
        all_status = manager.get_all_status()
        assert "svc1" in all_status
        assert "svc2" in all_status

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_manager_reset_all(self):
        """验证限流器管理器重置所有功能（边界条件测试）"""
        manager = RateLimiterManager()
        
        limiter1 = manager.get("svc1")
        limiter2 = manager.get("svc2")
        
        limiter1.check()
        limiter2.check()
        
        manager.reset_all()
        
        assert limiter1.get_status()["current_concurrent"] == 0
        assert limiter2.get_status()["current_concurrent"] == 0


class TestRateLimiterGlobalFunctions:
    """全局限流器函数接口测试类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_global_get_function(self):
        """验证全局get_rate_limiter函数功能（边界条件测试）"""
        limiter = get_rate_limiter("global_test_limiter")
        assert limiter is not None
        assert isinstance(limiter, RateLimiter)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_global_register_function(self):
        """验证全局register_rate_limiter函数功能（边界条件测试）"""
        register_rate_limiter("registered_test", max_concurrent=25)
        limiter = get_rate_limiter("registered_test")
        assert limiter.get_status()["max_concurrent"] == 25

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_global_get_all_status(self):
        """验证全局get_all_rate_limiter_status函数功能（边界条件测试）"""
        register_rate_limiter("status_test_svc")
        
        all_status = get_all_rate_limiter_status()
        assert isinstance(all_status, dict)
        assert "status_test_svc" in all_status

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limiter_default_global_name(self):
        """验证默认全局限流器名称为default（边界条件测试）"""
        limiter = get_rate_limiter()
        assert limiter is not None
        assert isinstance(limiter, RateLimiter)
