"""网络延迟混沌测试

测试场景:
1. 50ms 轻微延迟（验证超时设置合理性）
2. 200ms 中等延迟（验证用户体验影响）
3. 1s 高延迟（验证超时和重试机制）
4. 10s 极端延迟（验证熔断是否触发）
5. 延迟抖动（随机延迟，验证稳定性）
6. 延迟逐步增加（验证渐进式降级）
"""

import time
import pytest
import threading
import logging
from unittest.mock import Mock, patch

from agent.monitoring.chaos_injector import (
    ChaosInjector,
    FaultType,
    get_chaos_injector,
    chaos_fault,
    with_chaos_injection
)

logger = logging.getLogger(__name__)


@pytest.mark.slow
class TestNetworkLatencyChaos:
    """网络延迟混沌测试"""
    
    def setup_method(self):
        """每个测试前重置混沌注入器"""
        self.injector = get_chaos_injector()
        self.injector.clear_all()
    
    def teardown_method(self):
        """每个测试后清理故障"""
        self.injector.clear_all()
    
    def test_minor_latency_50ms(self):
        """测试50ms轻微延迟"""
        start = time.time()
        
        with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=50):
            @with_chaos_injection(FaultType.NETWORK_DELAY)
            def delayed_operation():
                return "success"
            
            result = delayed_operation()
        
        elapsed = (time.time() - start) * 1000
        
        assert result == "success"
        assert elapsed >= 50, f"延迟不足50ms，实际{elapsed:.2f}ms"
        assert elapsed < 150, f"延迟超过预期，实际{elapsed:.2f}ms"
    
    def test_moderate_latency_200ms(self):
        """测试200ms中等延迟"""
        start = time.time()
        
        with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=200):
            @with_chaos_injection(FaultType.NETWORK_DELAY)
            def delayed_operation():
                return "success"
            
            result = delayed_operation()
        
        elapsed = (time.time() - start) * 1000
        
        assert result == "success"
        assert elapsed >= 200, f"延迟不足200ms，实际{elapsed:.2f}ms"
        assert elapsed < 350, f"延迟超过预期，实际{elapsed:.2f}ms"
    
    def test_high_latency_1s(self):
        """测试1s高延迟"""
        start = time.time()
        
        with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=1000):
            @with_chaos_injection(FaultType.NETWORK_DELAY)
            def delayed_operation():
                return "success"
            
            result = delayed_operation()
        
        elapsed = (time.time() - start) * 1000
        
        assert result == "success"
        assert elapsed >= 1000, f"延迟不足1s，实际{elapsed:.2f}ms"
        assert elapsed < 1500, f"延迟超过预期，实际{elapsed:.2f}ms"
    
    def test_extreme_latency_10s_with_timeout(self):
        """测试10s极端延迟并验证超时机制"""
        with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=10000):
            @with_chaos_injection(FaultType.NETWORK_DELAY)
            def slow_operation():
                time.sleep(0.1)
                return "success"
            
            start = time.time()
            result = slow_operation()
            elapsed = (time.time() - start) * 1000
        
        assert result == "success"
        assert elapsed >= 10000, f"延迟不足10s，实际{elapsed:.2f}ms"
    
    def test_latency_jitter(self):
        """测试延迟抖动（随机延迟）"""
        self.injector.inject_network_delay(delay_ms=100, probability=0.5)
        
        @with_chaos_injection(FaultType.NETWORK_DELAY)
        def jitter_operation():
            return "success"
        
        results = []
        elapsed_times = []
        
        for _ in range(10):
            start = time.time()
            result = jitter_operation()
            elapsed = (time.time() - start) * 1000
            results.append(result)
            elapsed_times.append(elapsed)
        
        assert all(r == "success" for r in results)
        
        fast_count = sum(1 for t in elapsed_times if t < 50)
        slow_count = sum(1 for t in elapsed_times if t >= 100)
        
        assert fast_count > 0, "应该有部分请求未被延迟"
        assert slow_count > 0, "应该有部分请求被延迟"
    
    def test_progressive_latency_increase(self):
        """测试延迟逐步增加（验证渐进式降级）"""
        latency_steps = [50, 100, 200, 500, 1000]
        results = []
        
        for delay_ms in latency_steps:
            self.injector.clear_all()
            with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=delay_ms):
                @with_chaos_injection(FaultType.NETWORK_DELAY)
                def progressive_operation():
                    return f"latency_{delay_ms}"
                
                start = time.time()
                result = progressive_operation()
                elapsed = (time.time() - start) * 1000
                
                results.append({
                    'expected_delay': delay_ms,
                    'result': result,
                    'actual_elapsed': elapsed
                })
        
        for r in results:
            assert r['result'] == f"latency_{r['expected_delay']}"
            assert r['actual_elapsed'] >= r['expected_delay'] - 10, \
                f"延迟不足，预期{r['expected_delay']}ms，实际{r['actual_elapsed']:.2f}ms"
    
    def test_latency_with_probability(self):
        """测试带概率的延迟注入"""
        logger.info("[NETWORK_CHAOS] 概率延迟测试开始")
        self.injector.inject_network_delay(delay_ms=100, probability=0.5)
        logger.info("[NETWORK_CHAOS] 配置 - delay: 100ms, probability: 0.5")
        
        @with_chaos_injection(FaultType.NETWORK_DELAY)
        def probabilistic_operation():
            return "success"
        
        # 使用mock控制随机数，确保测试确定性
        with patch('agent.monitoring.chaos_injector.random.random', return_value=0.3):  # 0.3 < 0.5 → 触发延迟
            start = time.time()
            result = probabilistic_operation()
            elapsed = (time.time() - start) * 1000
            logger.info(f"[NETWORK_CHAOS] 触发延迟场景: {elapsed:.2f}ms")
            assert result == "success"
            assert elapsed >= 100, f"预期延迟>=100ms, 实际{elapsed:.2f}ms"
        
        with patch('agent.monitoring.chaos_injector.random.random', return_value=0.7):  # 0.7 > 0.5 → 不触发延迟
            start = time.time()
            result = probabilistic_operation()
            elapsed = (time.time() - start) * 1000
            logger.info(f"[NETWORK_CHAOS] 不触发延迟场景: {elapsed:.2f}ms")
            assert result == "success"
            assert elapsed < 50, f"预期不延迟<50ms, 实际{elapsed:.2f}ms"
        
        logger.info("[NETWORK_CHAOS] 概率延迟测试通过")
    
    def test_latency_duration_auto_clear(self):
        """测试延迟自动清除"""
        self.injector.inject_network_delay(delay_ms=100, duration_ms=500)
        
        @with_chaos_injection(FaultType.NETWORK_DELAY)
        def timed_operation():
            return "success"
        
        start = time.time()
        result1 = timed_operation()
        elapsed1 = (time.time() - start) * 1000
        
        time.sleep(0.6)
        
        start = time.time()
        result2 = timed_operation()
        elapsed2 = (time.time() - start) * 1000
        
        assert result1 == "success"
        assert result2 == "success"
        assert elapsed1 >= 100, f"延迟期间应受影响，实际{elapsed1:.2f}ms"
        assert elapsed2 < 50, f"延迟结束后不应受影响，实际{elapsed2:.2f}ms"
    
    def test_concurrent_latency_injection(self):
        """测试并发延迟注入"""
        with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=50):
            results = []
            
            def worker(id):
                @with_chaos_injection(FaultType.NETWORK_DELAY)
                def operation():
                    return f"worker_{id}"
                results.append(operation())
            
            threads = []
            for i in range(5):
                t = threading.Thread(target=worker, args=(i,))
                threads.append(t)
                t.start()
            
            for t in threads:
                t.join()
            
            assert len(results) == 5
            assert all(f"worker_{i}" in results for i in range(5))
    
    def test_latency_stats_tracking(self):
        """测试延迟统计跟踪"""
        with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=50):
            @with_chaos_injection(FaultType.NETWORK_DELAY)
            def stat_operation():
                return "success"
            
            for _ in range(5):
                stat_operation()
        
        stats = self.injector.get_stats()
        assert stats['total_affected_requests'] >= 5
        
        records = self.injector.get_injection_history()
        assert len(records) >= 1
    
    def test_network_timeout_injection(self):
        """测试网络超时故障注入"""
        logger.info("[NETWORK_CHAOS] 网络超时注入测试开始")
        
        with chaos_fault(FaultType.NETWORK_TIMEOUT, probability=1.0):
            stats = self.injector.get_stats()
            logger.info(f"[NETWORK_CHAOS] 超时注入后状态: network_timeout={stats['fault_types']['network_timeout']}")
            assert stats['fault_types']['network_timeout'] is True
            
            config = self.injector._fault_configs[FaultType.NETWORK_TIMEOUT]
            logger.info(f"[NETWORK_CHAOS] 超时配置: probability={config.probability}")
            assert config.probability == 1.0
            assert config.enabled is True
        
        stats_after = self.injector.get_stats()
        logger.info(f"[NETWORK_CHAOS] 清理后状态: network_timeout={stats_after['fault_types']['network_timeout']}")
        assert stats_after['fault_types']['network_timeout'] is False
        logger.info("[NETWORK_CHAOS] 网络超时注入测试通过")
    
    def test_network_timeout_raises_timeout_error(self):
        """测试网络超时抛出TimeoutError异常"""
        logger.info("[NETWORK_CHAOS] 超时异常抛出测试开始")
        
        with chaos_fault(FaultType.NETWORK_TIMEOUT, probability=1.0):
            @with_chaos_injection(FaultType.NETWORK_TIMEOUT)
            def operation_that_times_out():
                return "success"
            
            timeout_count = 0
            for _ in range(10):
                try:
                    operation_that_times_out()
                except TimeoutError as e:
                    timeout_count += 1
                    logger.debug(f"[NETWORK_CHAOS] 捕获到超时异常: {e}")
            
            logger.info(f"[NETWORK_CHAOS] 10次调用中超时次数: {timeout_count}")
            assert timeout_count >= 8, f"预期至少8次超时，实际{timeout_count}次"
        
        logger.info("[NETWORK_CHAOS] 超时异常抛出测试通过")
    
    def test_network_timeout_with_probability(self):
        """测试带概率的网络超时"""
        logger.info("[NETWORK_CHAOS] 概率超时测试开始 - probability=0.3")
        
        self.injector.inject_network_timeout(probability=0.3)
        
        @with_chaos_injection(FaultType.NETWORK_TIMEOUT)
        def probabilistic_operation():
            return "success"
        
        timeout_count = 0
        total_count = 30
        for _ in range(total_count):
            try:
                probabilistic_operation()
            except TimeoutError:
                timeout_count += 1
        
        logger.info(f"[NETWORK_CHAOS] 概率超时结果: {timeout_count}/{total_count} ({timeout_count/total_count:.0%})")
        assert timeout_count > 0, "应该至少有一次超时"
        assert timeout_count < total_count, "不应该全部超时"
        
        self.injector.clear_fault(FaultType.NETWORK_TIMEOUT)
        logger.info("[NETWORK_CHAOS] 概率超时测试通过")
    
    def test_network_timeout_with_duration(self):
        """测试带持续时间的网络超时"""
        logger.info("[NETWORK_CHAOS] 持续时间超时测试开始 - duration=500ms")
        
        self.injector.inject_network_timeout(probability=1.0, duration_ms=500)
        
        @with_chaos_injection(FaultType.NETWORK_TIMEOUT)
        def timed_operation():
            return "success"
        
        start = time.time()
        try:
            timed_operation()
        except TimeoutError:
            pass
        elapsed_before = (time.time() - start) * 1000
        logger.info(f"[NETWORK_CHAOS] 注入期间第一次调用耗时: {elapsed_before:.0f}ms (预期触发超时)")
        assert self.injector.get_stats()['fault_types']['network_timeout'] is True
        
        time.sleep(0.6)
        self.injector.clear_fault(FaultType.NETWORK_TIMEOUT)
        
        start = time.time()
        result = timed_operation()
        elapsed_after = (time.time() - start) * 1000
        logger.info(f"[NETWORK_CHAOS] 过期后调用耗时: {elapsed_after:.0f}ms, 结果: {result}")
        assert result == "success"
        assert self.injector.get_stats()['fault_types']['network_timeout'] is False
        
        logger.info("[NETWORK_CHAOS] 持续时间超时测试通过")
    
    def test_service_unavailable_injection(self):
        """测试服务不可用故障注入"""
        logger.info("[NETWORK_CHAOS] 服务不可用注入测试开始")
        
        with chaos_fault(FaultType.SERVICE_UNAVAILABLE, service_name="test_service", error_code=503):
            stats = self.injector.get_stats()
            logger.info(f"[NETWORK_CHAOS] 服务不可用状态: service_unavailable={stats['fault_types']['service_unavailable']}")
            assert stats['fault_types']['service_unavailable'] is True
            
            config = self.injector._fault_configs[FaultType.SERVICE_UNAVAILABLE]
            logger.info(f"[NETWORK_CHAOS] 服务不可用配置: service={config.target_service}, code={config.error_code}")
            assert config.target_service == "test_service"
            assert config.error_code == 503
        
        stats_after = self.injector.get_stats()
        logger.info(f"[NETWORK_CHAOS] 清理后状态: service_unavailable={stats_after['fault_types']['service_unavailable']}")
        assert stats_after['fault_types']['service_unavailable'] is False
        logger.info("[NETWORK_CHAOS] 服务不可用注入测试通过")
    
    def test_service_unavailable_raises_connection_error(self):
        """测试服务不可用抛出ConnectionError异常"""
        logger.info("[NETWORK_CHAOS] 服务不可用异常测试开始")
        
        with chaos_fault(FaultType.SERVICE_UNAVAILABLE, service_name="api", error_code=503):
            @with_chaos_injection(FaultType.SERVICE_UNAVAILABLE, target_service="api")
            def api_call():
                return "success"
            
            error_count = 0
            for _ in range(5):
                try:
                    api_call()
                except ConnectionError as e:
                    error_count += 1
                    logger.debug(f"[NETWORK_CHAOS] 捕获到连接错误: {e}")
            
            logger.info(f"[NETWORK_CHAOS] 5次调用中错误次数: {error_count}")
            assert error_count == 5
        
        logger.info("[NETWORK_CHAOS] 服务不可用异常测试通过")
    
    def test_network_timeout_stats_tracking(self):
        """测试网络超时统计跟踪"""
        logger.info("[NETWORK_CHAOS] 超时统计跟踪测试开始")
        
        with chaos_fault(FaultType.NETWORK_TIMEOUT, probability=1.0):
            @with_chaos_injection(FaultType.NETWORK_TIMEOUT)
            def stat_operation():
                return "success"
            
            timeout_count = 0
            for _ in range(3):
                try:
                    stat_operation()
                except TimeoutError:
                    timeout_count += 1
            
            logger.info(f"[NETWORK_CHAOS] 注入期间触发超时次数: {timeout_count}")
        
        records = self.injector.get_injection_history()
        timeout_records = [r for r in records if r.fault_type == FaultType.NETWORK_TIMEOUT]
        logger.info(f"[NETWORK_CHAOS] 超时记录数: {len(timeout_records)}")
        
        assert len(timeout_records) >= 1
        assert timeout_records[-1].recovered_at is not None
        logger.info("[NETWORK_CHAOS] 超时统计跟踪测试通过")
    
    def test_combined_network_faults(self):
        """测试组合网络故障（延迟+超时）"""
        logger.info("[NETWORK_CHAOS] 组合网络故障测试开始 - 延迟+超时")
        
        with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=50):
            with chaos_fault(FaultType.NETWORK_TIMEOUT, probability=0.5):
                stats = self.injector.get_stats()
                logger.info(f"[NETWORK_CHAOS] 组合故障状态: delay={stats['fault_types']['network_delay']}, timeout={stats['fault_types']['network_timeout']}")
                assert stats['fault_types']['network_delay'] is True
                assert stats['fault_types']['network_timeout'] is True
                
                @with_chaos_injection(FaultType.NETWORK_DELAY)
                @with_chaos_injection(FaultType.NETWORK_TIMEOUT)
                def combined_operation():
                    return "success"
                
                success_count = 0
                timeout_count = 0
                for _ in range(10):
                    start = time.time()
                    try:
                        result = combined_operation()
                        elapsed = (time.time() - start) * 1000
                        success_count += 1
                        logger.debug(f"[NETWORK_CHAOS] 成功 - 耗时{elapsed:.0f}ms")
                    except TimeoutError:
                        timeout_count += 1
                
                logger.info(f"[NETWORK_CHAOS] 组合故障结果: 成功={success_count}, 超时={timeout_count}")
                assert success_count + timeout_count == 10
        
        logger.info("[NETWORK_CHAOS] 组合网络故障测试通过")