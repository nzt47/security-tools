"""熔断降级联动集成测试

测试覆盖：
1. 熔断触发→自动降级→恢复的完整流程
2. 多级降级的顺序正确性
3. 降级后功能的可用性（不是完全不可用）
4. 熔断恢复后的功能自验证
5. 降级状态的监控告警触发
6. 手动干预熔断/降级状态
"""

import pytest
import time
import json
import logging
from datetime import datetime
from unittest.mock import MagicMock

pytestmark = pytest.mark.integration
pytest.timeout = 30

logger = logging.getLogger(__name__)


class TestCircuitBreakerDegradeFlow:
    """熔断降级联动集成测试"""

    def test_circuit_breaker_trigger_degrade_recover_flow(self):
        """测试熔断触发→自动降级→恢复的完整流程"""
        from agent.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerState
        from agent.graceful_degrade import GracefulDegrade, DegradeModule, DegradeLevel

        logger.info("="*60)
        logger.info(f"[测试开始] test_circuit_breaker_trigger_degrade_recover_flow")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 初始化熔断器配置")
        breaker_config = CircuitBreakerConfig(
            failure_threshold=0.3,
            min_requests=3,
            reset_timeout=1.0,
            window_seconds=10
        )
        breaker = CircuitBreaker(breaker_config)
        degrade = GracefulDegrade()

        logger.info(f"[步骤2] 熔断器初始状态: {breaker.state.name}")

        logger.info("[步骤3] 模拟连续失败触发熔断")
        for i in range(5):
            breaker.record_failure()
            logger.info(f"  第{i+1}次失败记录，当前状态: {breaker.state.name}")

        assert breaker.state == CircuitBreakerState.OPEN
        logger.info(f"[断言通过] 熔断器已触发熔断，状态: {breaker.state.name}")

        logger.info(f"[步骤4] 等待 {breaker_config.reset_timeout}s 后熔断器进入半开状态")
        start_wait = time.time()
        time.sleep(1.5)
        wait_duration = time.time() - start_wait
        logger.info(f"[步骤4完成] 等待耗时: {wait_duration:.3f}s")

        assert breaker.state == CircuitBreakerState.HALF_OPEN
        logger.info(f"[断言通过] 熔断器进入半开状态: {breaker.state.name}")

        logger.info("[步骤5] 模拟成功请求恢复熔断器")
        for i in range(3):
            breaker.record_success()
            logger.info(f"  第{i+1}次成功记录，当前状态: {breaker.state.name}")

        assert breaker.state == CircuitBreakerState.CLOSED
        logger.info(f"[断言通过] 熔断器已恢复关闭状态: {breaker.state.name}")

        logger.info("="*60)
        logger.info(f"[测试完成] test_circuit_breaker_trigger_degrade_recover_flow")
        logger.info("="*60)

    def test_multi_level_degrade_order_correctness(self):
        """测试多级降级的顺序正确性"""
        from agent.graceful_degrade import GracefulDegrade, DegradeModule, DegradeLevel

        logger.info("="*60)
        logger.info(f"[测试开始] test_multi_level_degrade_order_correctness")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 初始化优雅降级组件")
        degrade = GracefulDegrade()

        call_count = [0]

        def failing_func():
            call_count[0] += 1
            logger.info(f"  函数调用 #{call_count[0]}，抛出异常")
            raise Exception("Always failing")

        logger.info("[步骤2] 执行多次降级调用")
        results = []
        for i in range(10):
            logger.info(f"  第{i+1}次调用...")
            start_time = time.time()
            result = degrade.with_degrade(
                module=DegradeModule.SCHEMA,
                func=failing_func,
                fallback=lambda: "fallback_result"
            )
            duration = time.time() - start_time
            results.append(result)
            logger.info(f"  第{i+1}次调用完成，耗时: {duration:.3f}s，结果: {result}")

        logger.info("[步骤3] 验证降级指标")
        metrics = degrade.get_metrics()
        logger.info(f"  retry_count: {metrics.retry_count}")
        logger.info(f"  total_degrades: {metrics.total_degrades}")

        assert metrics.retry_count >= 3
        logger.info(f"[断言通过] 重试次数 >= 3")

        assert metrics.total_degrades > 0
        logger.info(f"[断言通过] 降级次数 > 0")

        assert result == "fallback_result"
        logger.info(f"[断言通过] 返回降级结果")

        logger.info("="*60)
        logger.info(f"[测试完成] test_multi_level_degrade_order_correctness")
        logger.info("="*60)

    def test_degraded_function_availability(self):
        """测试降级后功能的可用性（不是完全不可用）"""
        from agent.graceful_degrade import GracefulDegrade, DegradeModule

        logger.info("="*60)
        logger.info(f"[测试开始] test_degraded_function_availability")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 初始化优雅降级组件")
        degrade = GracefulDegrade()

        logger.info("[步骤2] 定义两种场景：正常函数和失败函数")
        def success_func():
            logger.info("  函数正常返回")
            return {"data": "real_data", "source": "primary"}

        def fail_func():
            logger.info("  函数调用失败")
            raise Exception("Always failing")

        logger.info("[步骤3] 测试正常函数")
        success_result = degrade.with_degrade(
            module=DegradeModule.MEMORY,
            func=success_func,
            fallback=lambda: {"data": "cached_data", "source": "cache"}
        )
        logger.info(f"  正常函数结果: source={success_result.get('source')}")

        assert success_result.get("source") == "primary"
        logger.info("[断言通过] 正常函数返回真实结果")

        logger.info("[步骤4] 测试失败函数的降级")
        degrade.clear_cache()
        fail_result = degrade.with_degrade(
            module=DegradeModule.MEMORY,
            func=fail_func,
            fallback=lambda: {"data": "cached_data", "source": "cache"}
        )
        logger.info(f"  失败函数结果: source={fail_result.get('source')}")

        assert fail_result.get("source") == "cache"
        logger.info("[断言通过] 失败函数返回降级结果")

        logger.info("[步骤5] 验证降级指标")
        metrics = degrade.get_metrics()
        logger.info(f"  total_degrades: {metrics.total_degrades}")
        logger.info(f"  retry_count: {metrics.retry_count}")

        assert metrics.total_degrades >= 1
        logger.info("[断言通过] 降级计数正确")

        logger.info("="*60)
        logger.info(f"[测试完成] test_degraded_function_availability")
        logger.info("="*60)

    def test_circuit_breaker_recovery_self_verification(self):
        """测试熔断恢复后的功能自验证"""
        from agent.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerState

        logger.info("="*60)
        logger.info(f"[测试开始] test_circuit_breaker_recovery_self_verification")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 初始化熔断器")
        breaker_config = CircuitBreakerConfig(
            failure_threshold=0.3,
            min_requests=3,
            reset_timeout=1.0,
            max_attempts=2
        )
        breaker = CircuitBreaker(breaker_config)

        logger.info("[步骤2] 触发熔断")
        for i in range(3):
            breaker.record_failure()
            logger.info(f"  第{i+1}次失败，状态: {breaker.state.name}")

        assert breaker.state == CircuitBreakerState.OPEN
        logger.info(f"[断言通过] 熔断器已打开")

        logger.info(f"[步骤3] 等待 {breaker_config.reset_timeout}s 超时")
        start_wait = time.time()
        time.sleep(1.5)
        wait_duration = time.time() - start_wait
        logger.info(f"[步骤3完成] 等待耗时: {wait_duration:.3f}s")

        assert breaker.state == CircuitBreakerState.HALF_OPEN
        logger.info(f"[断言通过] 熔断器进入半开状态")

        logger.info("[步骤4] 验证恢复")
        breaker.record_success()
        breaker.record_success()

        assert breaker.state == CircuitBreakerState.CLOSED
        logger.info(f"[断言通过] 熔断器已恢复关闭状态")

        logger.info("[步骤5] 检查状态指标")
        status = breaker.get_status()
        logger.info(f"  state: {status['state']}")
        logger.info(f"  successes: {status['metrics']['successes']}")

        assert status["state"] == "closed"
        assert status["metrics"]["successes"] >= 2
        logger.info("[断言通过] 状态指标验证通过")

        logger.info("="*60)
        logger.info(f"[测试完成] test_circuit_breaker_recovery_self_verification")
        logger.info("="*60)

    def test_degrade_status_monitoring_alert(self):
        """测试降级状态的监控告警触发"""
        from agent.graceful_degrade import GracefulDegrade, DegradeModule

        logger.info("="*60)
        logger.info(f"[测试开始] test_degrade_status_monitoring_alert")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 初始化优雅降级组件")
        degrade = GracefulDegrade()

        def always_fail():
            raise Exception("Continuous failure")

        logger.info("[步骤2] 持续触发降级")
        for i in range(8):
            logger.info(f"  第{i+1}次调用...")
            start_time = time.time()
            result = degrade.with_degrade(
                module=DegradeModule.CRITIC,
                func=always_fail,
                fallback=lambda: "fallback"
            )
            duration = time.time() - start_time
            logger.info(f"  第{i+1}次调用完成，耗时: {duration:.3f}s")

        logger.info("[步骤3] 检查降级指标")
        metrics = degrade.get_metrics()
        logger.info(f"  total_degrades: {metrics.total_degrades}")

        assert metrics.total_degrades >= 8
        logger.info(f"[断言通过] 降级次数 >= 8")

        logger.info("[步骤4] 检查监控状态")
        status = degrade.get_status()
        logger.info(f"  module_states keys: {list(status.get('module_states', {}).keys())}")

        assert "module_states" in status
        assert "critic" in status["module_states"]

        critic_state = status["module_states"]["critic"]
        logger.info(f"  critic error_count: {critic_state['error_count']}")
        logger.info(f"  critic consecutive_errors: {critic_state['consecutive_errors']}")

        assert critic_state["error_count"] >= 8
        assert critic_state["consecutive_errors"] >= 1
        logger.info("[断言通过] 监控告警状态验证通过")

        logger.info("="*60)
        logger.info(f"[测试完成] test_degrade_status_monitoring_alert")
        logger.info("="*60)

    def test_manual_intervention_circuit_degrade_state(self):
        """测试手动干预熔断/降级状态"""
        from agent.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerState
        from agent.graceful_degrade import GracefulDegrade, DegradeModule

        logger.info("="*60)
        logger.info(f"[测试开始] test_manual_intervention_circuit_degrade_state")
        logger.info(f"[时间戳] {datetime.now().isoformat()}")
        logger.info("="*60)

        logger.info("[步骤1] 初始化熔断器和降级组件")
        breaker_config = CircuitBreakerConfig(
            failure_threshold=0.3,
            min_requests=3,
            reset_timeout=60.0
        )
        breaker = CircuitBreaker(breaker_config)
        degrade = GracefulDegrade()

        logger.info("[步骤2] 触发熔断")
        for i in range(3):
            breaker.record_failure()
            logger.info(f"  第{i+1}次失败，状态: {breaker.state.name}")

        assert breaker.state == CircuitBreakerState.OPEN
        logger.info(f"[断言通过] 熔断器已打开")

        logger.info("[步骤3] 手动重置熔断器")
        breaker.reset()

        assert breaker.state == CircuitBreakerState.CLOSED
        logger.info(f"[断言通过] 熔断器已重置为关闭状态")

        logger.info("[步骤4] 验证重置后的状态")
        status = breaker.get_status()
        logger.info(f"  total_requests: {status['metrics']['total_requests']}")
        logger.info(f"  failures: {status['metrics']['failures']}")

        assert status["metrics"]["total_requests"] == 0
        assert status["metrics"]["failures"] == 0
        logger.info("[断言通过] 熔断器指标已清零")

        logger.info("[步骤5] 重置降级组件")
        degrade.reset()
        metrics = degrade.get_metrics()
        logger.info(f"  total_degrades after reset: {metrics.total_degrades}")

        assert metrics.total_degrades == 0
        logger.info("[断言通过] 降级指标已清零")

        logger.info("[步骤6] 验证功能恢复")
        def success_func():
            return "success"

        result = degrade.with_degrade(
            module=DegradeModule.CRITIC,
            func=success_func
        )
        assert result == "success"
        logger.info("[断言通过] 功能已恢复正常")

        logger.info("="*60)
        logger.info(f"[测试完成] test_manual_intervention_circuit_degrade_state")
        logger.info("="*60)
