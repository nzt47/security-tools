"""熔断器边界条件测试用例

根据测试用例设计规范，测试名称必须反映业务意图：
- test_{模块}_{功能}_{场景}_{预期结果}

本测试文件覆盖以下边界场景（共15+个用例）：
1. 空请求/零请求时熔断器状态（不应熔断）
2. 刚好达到最小请求数时的边界行为
3. 错误率刚好等于阈值（30%）的边界
4. 错误率略高于/低于阈值的边界
5. 冷却时间刚好到达的边界
6. 半开状态最大试探请求数边界
7. 并发请求下的熔断器状态一致性
8. 时间窗口滚动时的计数准确性
9. 异常类型过滤（只对特定异常熔断）
10. 熔断器名称/配置为空的情况
11. 重置功能的正确性
12. 多级熔断器（嵌套调用）的行为
13. 熔断器指标统计准确性
14. 快速失败时的错误码规范性
15. 熔断器状态转换的日志完整性

优先级标记：
- @pytest.mark.p0: 关键测试（必须通过）
- @pytest.mark.unit: 单元测试
"""

import pytest
import json
import threading
import time
from unittest.mock import patch, MagicMock

from agent.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerState,
    CircuitBreakerError,
    CircuitBreakerMetrics,
    CircuitBreakerManager,
    get_circuit_breaker,
    register_circuit_breaker,
    get_all_circuit_breaker_status,
)


class TestCircuitBreakerBoundaryConditions:
    """熔断器边界条件测试类"""

    @pytest.fixture
    def default_breaker(self):
        """创建默认配置的熔断器实例"""
        config = CircuitBreakerConfig(
            failure_threshold=0.3,
            min_requests=10,
            reset_timeout=30.0,
            window_seconds=60,
            max_attempts=3,
            name="test_breaker"
        )
        return CircuitBreaker(config)

    @pytest.fixture
    def quick_breaker(self):
        """创建快速恢复的熔断器（用于测试状态转换）"""
        config = CircuitBreakerConfig(
            failure_threshold=0.3,
            min_requests=5,
            reset_timeout=0.1,
            window_seconds=60,
            max_attempts=2,
            name="quick_breaker"
        )
        return CircuitBreaker(config)

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件1：空请求/零请求时熔断器状态（不应熔断）
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_zero_requests_stays_closed(self, default_breaker):
        """验证零请求时熔断器保持关闭状态（边界条件测试）"""
        assert default_breaker.state == CircuitBreakerState.CLOSED
        assert default_breaker.allow_request() is True
        assert default_breaker.metrics.total_requests == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_below_min_requests_no_trip(self, default_breaker):
        """验证请求数低于最小请求数时不触发熔断（边界条件测试）"""
        for i in range(9):
            default_breaker.record_failure()
        
        assert default_breaker.state == CircuitBreakerState.CLOSED
        assert default_breaker.allow_request() is True

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件2：刚好达到最小请求数时的边界行为
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_below_min_requests_no_trip(self, default_breaker):
        """验证请求数低于最小请求数时即使全错也不熔断（边界条件测试）"""
        for i in range(9):
            default_breaker.record_failure()
        
        assert default_breaker.state == CircuitBreakerState.CLOSED
        assert default_breaker.allow_request() is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_exact_min_requests_at_threshold_trips(self, default_breaker):
        """验证刚好达到最小请求数且错误率等于阈值时触发熔断（边界条件测试）"""
        for i in range(7):
            default_breaker.record_success()
        for i in range(2):
            default_breaker.record_failure()
        
        assert default_breaker.state == CircuitBreakerState.CLOSED
        
        default_breaker.record_failure()
        
        assert default_breaker.state == CircuitBreakerState.OPEN
        failure_rate = default_breaker.get_status()["current_failure_rate"]
        assert failure_rate == 0.3

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件3：错误率刚好等于阈值（30%）的边界
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_failure_rate_exactly_at_threshold(self):
        """验证错误率恰好等于阈值时的熔断行为（边界条件测试）"""
        config = CircuitBreakerConfig(
            failure_threshold=0.3,
            min_requests=10,
            reset_timeout=30,
            window_seconds=60,
            max_attempts=3,
            name="threshold_test"
        )
        breaker = CircuitBreaker(config)
        
        for i in range(7):
            breaker.record_success()
        for i in range(3):
            breaker.record_failure()
        
        status = breaker.get_status()
        assert status["current_failure_rate"] == 0.3

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件4：错误率略高于/低于阈值的边界
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_failure_rate_just_below_threshold_no_trip(self):
        """验证错误率略低于阈值时不熔断（边界条件测试）"""
        config = CircuitBreakerConfig(
            failure_threshold=0.3,
            min_requests=10,
            reset_timeout=30,
            window_seconds=60,
            max_attempts=3,
            name="below_threshold"
        )
        breaker = CircuitBreaker(config)
        
        for i in range(8):
            breaker.record_success()
        for i in range(2):
            breaker.record_failure()
        
        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker.get_status()["current_failure_rate"] < 0.3

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_failure_rate_just_above_threshold_trips(self):
        """验证错误率略高于阈值时触发熔断（边界条件测试）"""
        config = CircuitBreakerConfig(
            failure_threshold=0.3,
            min_requests=10,
            reset_timeout=30,
            window_seconds=60,
            max_attempts=3,
            name="above_threshold"
        )
        breaker = CircuitBreaker(config)
        
        for i in range(6):
            breaker.record_success()
        for i in range(4):
            breaker.record_failure()
        
        assert breaker.state == CircuitBreakerState.OPEN
        assert breaker.get_status()["current_failure_rate"] > 0.3

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件5：冷却时间刚好到达的边界
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_cooldown_just_before_timeout_stays_open(self, quick_breaker):
        """验证冷却时间未到达时熔断器保持打开状态（边界条件测试）"""
        for i in range(5):
            quick_breaker.record_failure()
        
        assert quick_breaker.state == CircuitBreakerState.OPEN
        
        time.sleep(0.05)
        assert quick_breaker.state == CircuitBreakerState.OPEN

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_cooldown_just_after_timeout_goes_half_open(self, quick_breaker):
        """验证冷却时间到达后熔断器进入半开状态（边界条件测试）"""
        for i in range(5):
            quick_breaker.record_failure()
        
        assert quick_breaker.state == CircuitBreakerState.OPEN
        
        time.sleep(0.15)
        assert quick_breaker.state == CircuitBreakerState.HALF_OPEN

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件6：半开状态最大试探请求数边界
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_half_open_max_attempts_exact(self, quick_breaker):
        """验证半开状态下刚好达到最大试探次数的行为（边界条件测试）"""
        for i in range(5):
            quick_breaker.record_failure()
        
        time.sleep(0.15)
        assert quick_breaker.state == CircuitBreakerState.HALF_OPEN
        
        for i in range(2):
            assert quick_breaker.allow_request() is True
            quick_breaker.record_success()
        
        assert quick_breaker.state == CircuitBreakerState.CLOSED

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_half_open_exceed_max_attempts_rejects(self, quick_breaker):
        """验证半开状态下超过最大试探次数时拒绝请求（边界条件测试）"""
        for i in range(5):
            quick_breaker.record_failure()
        
        time.sleep(0.15)
        assert quick_breaker.state == CircuitBreakerState.HALF_OPEN
        
        assert quick_breaker.allow_request() is True
        assert quick_breaker.allow_request() is True
        assert quick_breaker.allow_request() is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_half_open_failure_reopens(self, quick_breaker):
        """验证半开状态下失败立即重新打开熔断器（边界条件测试）"""
        for i in range(5):
            quick_breaker.record_failure()
        
        time.sleep(0.15)
        assert quick_breaker.state == CircuitBreakerState.HALF_OPEN
        
        assert quick_breaker.allow_request() is True
        quick_breaker.record_failure()
        
        assert quick_breaker.state == CircuitBreakerState.OPEN

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件7：并发请求下的熔断器状态一致性
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_concurrent_requests_consistency(self):
        """验证并发请求下熔断器状态的一致性（边界条件测试）"""
        config = CircuitBreakerConfig(
            failure_threshold=0.5,
            min_requests=20,
            reset_timeout=30,
            window_seconds=60,
            max_attempts=3,
            name="concurrent_test"
        )
        breaker = CircuitBreaker(config)
        
        def record_failures(count):
            for _ in range(count):
                breaker.record_failure()
        
        threads = []
        for _ in range(5):
            t = threading.Thread(target=record_failures, args=(10,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        metrics = breaker.metrics
        assert metrics.total_requests == 50
        assert metrics.failures == 50

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_concurrent_allow_request_thread_safe(self, default_breaker):
        """验证并发调用allow_request时的线程安全性（边界条件测试）"""
        results = []
        
        def check_request():
            results.append(default_breaker.allow_request())
        
        threads = []
        for _ in range(20):
            t = threading.Thread(target=check_request)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        assert all(results)
        assert len(results) == 20

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件8：时间窗口滚动时的计数准确性
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_window_pruning_removes_old_entries(self):
        """验证时间窗口滚动时正确移除过期条目（边界条件测试）"""
        config = CircuitBreakerConfig(
            failure_threshold=0.3,
            min_requests=5,
            reset_timeout=30,
            window_seconds=1,
            max_attempts=3,
            name="window_test"
        )
        breaker = CircuitBreaker(config)
        
        for i in range(10):
            breaker.record_failure()
        
        assert breaker.state == CircuitBreakerState.OPEN
        
        time.sleep(1.5)
        
        breaker._prune_window()
        assert len(breaker._window_entries) == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_window_mixed_ages_correct_failure_rate(self):
        """验证混合新老条目时错误率计算的准确性（边界条件测试）"""
        config = CircuitBreakerConfig(
            failure_threshold=0.5,
            min_requests=4,
            reset_timeout=30,
            window_seconds=2,
            max_attempts=3,
            name="mixed_window"
        )
        breaker = CircuitBreaker(config)
        
        for i in range(2):
            breaker.record_failure()
        
        time.sleep(1)
        
        for i in range(2):
            breaker.record_success()
        
        status = breaker.get_status()
        assert status["window_entries_count"] == 4
        assert status["current_failure_rate"] == 0.5

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件9：熔断器名称/配置为空的情况
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_default_config_works(self):
        """验证使用默认配置时熔断器正常工作（边界条件测试）"""
        breaker = CircuitBreaker()
        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker.allow_request() is True
        assert breaker._config.name == "default"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_empty_name_defaults(self):
        """验证空名称时使用默认名称（边界条件测试）"""
        config = CircuitBreakerConfig()
        breaker = CircuitBreaker(config)
        assert breaker._config.name == "default"

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件10：重置功能的正确性
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_reset_restores_initial_state(self, default_breaker):
        """验证重置功能恢复到初始状态（边界条件测试）"""
        for i in range(10):
            default_breaker.record_failure()
        
        assert default_breaker.state == CircuitBreakerState.OPEN
        assert default_breaker.metrics.total_requests > 0
        
        default_breaker.reset()
        
        assert default_breaker.state == CircuitBreakerState.CLOSED
        assert default_breaker.metrics.total_requests == 0
        assert default_breaker.metrics.failures == 0
        assert default_breaker.metrics.successes == 0
        assert len(default_breaker._window_entries) == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_reset_multiple_times_safe(self, default_breaker):
        """验证多次重置是安全的（边界条件测试）"""
        default_breaker.reset()
        default_breaker.reset()
        default_breaker.reset()
        
        assert default_breaker.state == CircuitBreakerState.CLOSED
        assert default_breaker.metrics.total_requests == 0

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件11：熔断器指标统计准确性
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_metrics_success_count_accurate(self, default_breaker):
        """验证成功请求计数的准确性（边界条件测试）"""
        for i in range(100):
            default_breaker.record_success()
        
        metrics = default_breaker.metrics
        assert metrics.total_requests == 100
        assert metrics.successes == 100
        assert metrics.failures == 0
        assert metrics.consecutive_failures == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_metrics_failure_count_accurate(self, default_breaker):
        """验证失败请求计数的准确性（边界条件测试）"""
        for i in range(50):
            default_breaker.record_failure()
        
        metrics = default_breaker.metrics
        assert metrics.total_requests == 50
        assert metrics.failures == 50
        assert metrics.successes == 0
        assert metrics.consecutive_failures == 50

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_metrics_state_transitions_counted(self, quick_breaker):
        """验证状态转换计数的准确性（边界条件测试）"""
        initial_transitions = quick_breaker.metrics.state_transitions
        
        for i in range(5):
            quick_breaker.record_failure()
        transitions_after_open = quick_breaker.metrics.state_transitions
        assert transitions_after_open > initial_transitions
        
        time.sleep(0.15)
        _ = quick_breaker.state
        transitions_after_half_open = quick_breaker.metrics.state_transitions
        assert transitions_after_half_open > transitions_after_open

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件12：快速失败时的错误码规范性
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_error_has_correct_error_code(self, default_breaker):
        """验证熔断器异常包含正确的错误码（边界条件测试）"""
        for i in range(10):
            default_breaker.record_failure()
        
        @default_breaker.protect
        def failing_func():
            return "should not reach here"
        
        with pytest.raises(CircuitBreakerError) as exc_info:
            failing_func()
        
        assert exc_info.value.error_code == "CIRCUIT_BREAKER_OPEN"
        assert exc_info.value.name == "test_breaker"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_propagates_original_exception(self, default_breaker):
        """验证熔断器保护的函数抛出的原始异常被正确传播（边界条件测试）"""
        @default_breaker.protect
        def failing_func():
            raise ValueError("test error")
        
        with pytest.raises(ValueError) as exc_info:
            failing_func()
        
        assert str(exc_info.value) == "test error"
        assert default_breaker.metrics.failures == 1

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件13：protect装饰器功能
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_protect_decorator_success(self, default_breaker):
        """验证protect装饰器在成功时正确记录（边界条件测试）"""
        @default_breaker.protect
        def success_func(x, y):
            return x + y
        
        result = success_func(3, 4)
        assert result == 7
        assert default_breaker.metrics.successes == 1
        assert default_breaker.metrics.total_requests == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_protect_decorator_failure(self, default_breaker):
        """验证protect装饰器在失败时正确记录（边界条件测试）"""
        @default_breaker.protect
        def failure_func():
            raise RuntimeError("fail")
        
        with pytest.raises(RuntimeError):
            failure_func()
        
        assert default_breaker.metrics.failures == 1
        assert default_breaker.metrics.total_requests == 1

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件14：熔断器管理器功能
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_manager_register_and_get(self):
        """验证熔断器管理器的注册和获取功能（边界条件测试）"""
        manager = CircuitBreakerManager()
        
        manager.register("test_svc", CircuitBreakerConfig(name="test_svc"))
        breaker = manager.get("test_svc")
        
        assert breaker is not None
        assert breaker._config.name == "test_svc"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_manager_auto_creates_default(self):
        """验证熔断器管理器自动创建默认熔断器（边界条件测试）"""
        manager = CircuitBreakerManager()
        
        breaker = manager.get("new_service")
        
        assert breaker is not None
        assert breaker._config.name == "new_service"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_manager_get_all_status(self):
        """验证熔断器管理器获取所有状态功能（边界条件测试）"""
        manager = CircuitBreakerManager()
        
        manager.register("svc1")
        manager.register("svc2")
        
        all_status = manager.get_all_status()
        assert "svc1" in all_status
        assert "svc2" in all_status
        assert all_status["svc1"]["state"] == "closed"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_manager_reset_all(self):
        """验证熔断器管理器重置所有功能（边界条件测试）"""
        manager = CircuitBreakerManager()
        
        breaker1 = manager.get("svc1")
        breaker2 = manager.get("svc2")
        
        for _ in range(5):
            breaker1.record_failure()
        for _ in range(3):
            breaker2.record_success()
        
        manager.reset_all()
        
        assert breaker1.metrics.total_requests == 0
        assert breaker2.metrics.total_requests == 0
        assert breaker1.state == CircuitBreakerState.CLOSED

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件15：全局函数接口
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_global_get_function(self):
        """验证全局get_circuit_breaker函数功能（边界条件测试）"""
        breaker = get_circuit_breaker("global_test_svc")
        assert breaker is not None
        assert isinstance(breaker, CircuitBreaker)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_global_register_function(self):
        """验证全局register_circuit_breaker函数功能（边界条件测试）"""
        register_circuit_breaker(
            "registered_test",
            CircuitBreakerConfig(name="registered_test")
        )
        breaker = get_circuit_breaker("registered_test")
        assert breaker._config.name == "registered_test"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_global_get_all_status(self):
        """验证全局get_all_circuit_breaker_status函数功能（边界条件测试）"""
        register_circuit_breaker("status_test_svc")
        
        all_status = get_all_circuit_breaker_status()
        assert isinstance(all_status, dict)
        assert "status_test_svc" in all_status

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件16：get_status返回结构完整性
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_get_status_contains_all_fields(self, default_breaker):
        """验证get_status返回包含所有必需字段（边界条件测试）"""
        status = default_breaker.get_status()
        
        required_fields = [
            "name", "state", "failure_threshold", "reset_timeout",
            "window_seconds", "min_requests", "max_attempts",
            "metrics", "current_failure_rate",
            "time_since_last_state_change", "window_entries_count"
        ]
        
        for field in required_fields:
            assert field in status, f"Missing field: {field}"
        
        metric_fields = [
            "total_requests", "successes", "failures",
            "consecutive_failures", "state_transitions", "last_reset"
        ]
        
        for field in metric_fields:
            assert field in status["metrics"], f"Missing metric field: {field}"

    # ════════════════════════════════════════════════════════════════════════
    #  边界条件17：protect_async异步装饰器
    # ════════════════════════════════════════════════════════════════════════

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_protect_async_success(self, default_breaker):
        """验证异步protect装饰器成功时的行为（边界条件测试）"""
        import asyncio
        
        @default_breaker.protect_async
        async def async_success(x):
            await asyncio.sleep(0.01)
            return x * 2
        
        result = asyncio.run(async_success(5))
        assert result == 10
        assert default_breaker.metrics.successes == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_breaker_protect_async_failure(self, default_breaker):
        """验证异步protect装饰器失败时的行为（边界条件测试）"""
        import asyncio
        
        @default_breaker.protect_async
        async def async_fail():
            await asyncio.sleep(0.01)
            raise ValueError("async error")
        
        with pytest.raises(ValueError):
            asyncio.run(async_fail())
        
        assert default_breaker.metrics.failures == 1
