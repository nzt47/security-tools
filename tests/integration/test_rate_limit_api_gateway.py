"""API网关限流全链路测试

测试覆盖：
1. 全局级限流的全链路验证
2. 用户级限流的隔离性
3. 接口级限流的精准性
4. 限流触发后的响应格式正确性
5. 限流解除后的自动恢复
"""

import pytest
import time
import json

pytestmark = pytest.mark.integration
pytest.timeout = 30


class TestRateLimitApiGateway:
    """API网关限流全链路测试"""

    def test_global_rate_limit_full_pipeline(self):
        """测试全局级限流的全链路验证"""
        from agent.rate_limiter import RateLimiter, RateLimitStrategy, RateLimitError

        limiter = RateLimiter(max_concurrent=10, strategy=RateLimitStrategy.REJECT)
        
        for _ in range(98):
            limiter.check(endpoint="/api/chat", user_id="user1")
            limiter.release()

        success_count = 0
        fail_count = 0

        for _ in range(5):
            if limiter.check(endpoint="/api/chat", user_id="user1"):
                success_count += 1
                limiter.release()
            else:
                fail_count += 1

        assert fail_count >= 3

        time.sleep(1.0)

        success_after_wait = 0
        for _ in range(3):
            if limiter.check(endpoint="/api/chat", user_id="user1"):
                success_after_wait += 1
                limiter.release()

        assert success_after_wait >= 1

    def test_user_level_rate_limit_isolation(self):
        """测试用户级限流的隔离性"""
        from agent.rate_limiter import RateLimiter

        limiter = RateLimiter(max_concurrent=10)

        limiter.register_rule("user", capacity=3, refill_rate=1.0)

        for user_id in ["user_a", "user_b", "user_c"]:
            for _ in range(3):
                assert limiter.check(endpoint="/api/chat", user_id=user_id) is True
                limiter.release()

        for user_id in ["user_a", "user_b", "user_c"]:
            result = limiter.check(endpoint="/api/chat", user_id=user_id)
            assert result is False

        other_user_result = limiter.check(endpoint="/api/chat", user_id="user_d")
        assert other_user_result is True
        limiter.release()

    def test_endpoint_level_rate_limit_precision(self):
        """测试接口级限流的精准性"""
        from agent.rate_limiter import RateLimiter

        limiter = RateLimiter(max_concurrent=10)

        limiter.register_rule("endpoint/api/chat", capacity=2, refill_rate=1.0)
        limiter.register_rule("endpoint/api/search", capacity=5, refill_rate=1.0)

        chat_success = 0
        for _ in range(5):
            if limiter.check(endpoint="api/chat", user_id="user1"):
                chat_success += 1
                limiter.release()

        assert chat_success == 2

        search_success = 0
        for _ in range(6):
            if limiter.check(endpoint="api/search", user_id="user1"):
                search_success += 1
                limiter.release()

        assert search_success == 5

    def test_rate_limit_response_format_correctness(self):
        """测试限流触发后的响应格式正确性"""
        from agent.rate_limiter import RateLimiter, RateLimitStrategy

        limiter = RateLimiter(max_concurrent=10, strategy=RateLimitStrategy.REJECT)

        for _ in range(100):
            limiter.check(endpoint="/api/test", user_id="test_user")
            limiter.release()

        result = limiter.check(endpoint="/api/test", user_id="test_user")
        assert result is False

        status = limiter.get_status()

        assert "global_bucket" in status
        assert "tokens" in status["global_bucket"]

        assert "strategy" in status
        assert status["strategy"] == "reject"

        assert "current_concurrent" in status
        assert status["current_concurrent"] >= 0

    def test_rate_limit_auto_recovery_after_release(self):
        """测试限流解除后的自动恢复"""
        from agent.rate_limiter import RateLimiter

        limiter = RateLimiter(max_concurrent=10)
        
        for _ in range(99):
            limiter.check(endpoint="/api/chat", user_id="user1")
            limiter.release()

        limiter.check(endpoint="/api/chat", user_id="user1")

        result_after_consume = limiter.check(endpoint="/api/chat", user_id="user2")
        assert result_after_consume is False

        time.sleep(1.5)

        result_after_wait = limiter.check(endpoint="/api/chat", user_id="user3")
        assert result_after_wait is True
        limiter.release()

        limiter.reset()

        result_after_reset = limiter.check(endpoint="/api/chat", user_id="user4")
        assert result_after_reset is True
        limiter.release()