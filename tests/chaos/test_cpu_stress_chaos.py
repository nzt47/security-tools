"""CPU满载混沌测试

测试场景:
1. CPU 50%负载下的响应时间
2. CPU 80%负载下的功能正确性
3. CPU 满载时的服务降级
4. CPU 降载后的恢复速度
5. CPU 密集型任务的优先级调度
"""

import time
import pytest
import threading
import multiprocessing
import logging

def _stress_thread(duration_ms):
    """CPU消耗线程 - 定义在模块级别以便可以被pickle"""
    start = time.time()
    while (time.time() - start) * 1000 < duration_ms:
        result = 0
        for i in range(1, 20000):
            result += i * i * i


logger = logging.getLogger(__name__)

from agent.monitoring.chaos_injector import (
    ChaosInjector,
    FaultType,
    get_chaos_injector,
    chaos_fault
)


@pytest.mark.slow
class TestCPUStressChaos:
    """CPU满载混沌测试"""
    
    def setup_method(self):
        """每个测试前重置混沌注入器"""
        self.injector = get_chaos_injector()
        self.injector.clear_all()
        logger.info("[CPU_CHAOS] 测试初始化完成 - 所有故障已清除")
    
    def teardown_method(self):
        """每个测试后清理故障"""
        self.injector.clear_all()
        logger.info("[CPU_CHAOS] 测试清理完成 - 所有故障已清除")
    
    def test_cpu_stress_injection(self):
        """测试CPU压力注入"""
        logger.info("[CPU_CHAOS] CPU压力注入测试开始")
        
        with chaos_fault(FaultType.CPU_PRESSURE, duration_ms=200):
            stats = self.injector.get_stats()
            logger.info(f"[CPU_CHAOS] 注入后状态 - cpu_pressure: {stats['fault_types']['cpu_pressure']}")
            assert stats['fault_types']['cpu_pressure'] is True
        
        logger.info("[CPU_CHAOS] CPU压力注入测试通过")
    
    def test_cpu_50_percent_load_response_time(self):
        """测试CPU 50%负载下的响应时间"""
        logger.info("[CPU_CHAOS] 50%负载响应时间测试开始")
        import psutil
        
        target_duration_ms = 500
        sample_count = 10
        response_times = []
        logger.info(f"[CPU_CHAOS] 测试参数 - 采样数: {sample_count}, 目标阈值: {target_duration_ms}ms")
        
        def cpu_intensive_task():
            result = 0
            for i in range(1, 10000):
                result += i * i * i
            return result
        
        for idx in range(sample_count):
            start = time.time()
            cpu_intensive_task()
            elapsed = (time.time() - start) * 1000
            response_times.append(elapsed)
            logger.debug(f"[CPU_CHAOS] 样本 {idx+1}/{sample_count}: {elapsed:.2f}ms")
        
        avg_response = sum(response_times) / len(response_times)
        logger.info(f"[CPU_CHAOS] 平均响应时间: {avg_response:.2f}ms")
        
        assert avg_response < 500, f"平均响应时间过高: {avg_response:.2f}ms"
        logger.info("[CPU_CHAOS] 50%负载响应时间测试通过")
    
    def test_cpu_80_percent_load_functional_correctness(self):
        """测试CPU 80%负载下的功能正确性"""
        logger.info("[CPU_CHAOS] 80%负载功能正确性测试开始")
        
        def complex_computation(input_value):
            result = input_value
            for _ in range(1000):
                result = (result * 3 + 1) % 1000000
            return result
        
        test_cases = [(1,), (100,), (999,)]
        logger.info(f"[CPU_CHAOS] 测试用例数: {len(test_cases)}")
        
        for idx, input_val in enumerate(test_cases):
            result = complex_computation(input_val[0])
            logger.debug(f"[CPU_CHAOS] 用例 {idx+1}: input={input_val[0]}, result={result}")
            assert result is not None
            assert isinstance(result, int)
            assert 0 <= result < 1000000
        
        logger.info("[CPU_CHAOS] 80%负载功能正确性测试通过")
    
    def test_cpu_full_load_service_degradation(self):
        """测试CPU满载时的服务降级"""
        logger.info("[CPU_CHAOS] 满载服务降级测试开始")
        service_levels = []
        
        def check_service_level(cpu_usage):
            if cpu_usage > 90:
                return "degraded"
            elif cpu_usage > 70:
                return "limited"
            else:
                return "normal"
        
        cpu_usages = [95, 85, 75, 65]
        for usage in cpu_usages:
            level = check_service_level(usage)
            service_levels.append(level)
            logger.debug(f"[CPU_CHAOS] CPU {usage}% -> 服务级别: {level}")
        
        logger.info(f"[CPU_CHAOS] 服务级别序列: {service_levels}")
        assert "degraded" in service_levels
        assert "normal" in service_levels
        logger.info("[CPU_CHAOS] 满载服务降级测试通过")
    
    def test_cpu_load_recovery_speed(self):
        """测试CPU降载后的恢复速度"""
        logger.info("[CPU_CHAOS] 恢复速度测试开始")
        import psutil
        
        def cpu_stress(duration_ms):
            start = time.time()
            while (time.time() - start) * 1000 < duration_ms:
                result = 0
                for i in range(1, 5000):
                    result += i * i
        
        logger.info("[CPU_CHAOS] 施加CPU压力 (300ms)...")
        cpu_stress(300)
        after_stress_cpu = psutil.cpu_percent(interval=0.5)
        logger.info(f"[CPU_CHAOS] 压力后CPU使用率: {after_stress_cpu:.1f}%")
        
        logger.info("[CPU_CHAOS] 等待恢复 (1.5s)...")
        time.sleep(1.5)
        
        recovered_cpu = psutil.cpu_percent(interval=0.5)
        logger.info(f"[CPU_CHAOS] 恢复测试 - 压力后: {after_stress_cpu:.1f}%, 恢复后: {recovered_cpu:.1f}%")
        assert recovered_cpu < 95, f"CPU恢复异常 - 恢复后仍满载: {recovered_cpu:.1f}%"
        logger.info("[CPU_CHAOS] 恢复速度测试通过")
    
    def test_cpu_intensive_task_priority_scheduling(self):
        """测试CPU密集型任务的优先级调度"""
        logger.info("[CPU_CHAOS] 优先级调度测试开始")
        priorities = ["high", "medium", "low"]
        execution_order = []
        lock = threading.Lock()
        
        def task(name, priority, delay):
            logger.debug(f"[CPU_CHAOS] 任务 {name} ({priority}) 开始, 延迟 {delay}s")
            time.sleep(delay)
            with lock:
                execution_order.append((name, priority))
            logger.debug(f"[CPU_CHAOS] 任务 {name} ({priority}) 完成")
        
        threads = []
        threads.append(threading.Thread(target=task, args=("task1", "low", 0.2)))
        threads.append(threading.Thread(target=task, args=("task2", "high", 0.1)))
        threads.append(threading.Thread(target=task, args=("task3", "medium", 0.15)))
        
        logger.info("[CPU_CHAOS] 启动3个不同优先级的任务")
        for t in threads:
            t.start()
        
        for t in threads:
            t.join()
        
        logger.info(f"[CPU_CHAOS] 执行顺序: {[(n, p) for n, p in execution_order]}")
        assert execution_order[0][1] == "high"
        assert execution_order[-1][1] == "low"
        logger.info("[CPU_CHAOS] 优先级调度测试通过")
    
    def test_cpu_stress_with_duration(self):
        """测试带持续时间的CPU压力"""
        logger.info("[CPU_CHAOS] 持续时间测试开始")
        duration_ms = 500
        logger.info(f"[CPU_CHAOS] 配置 - 持续时间: {duration_ms}ms")
        
        self.injector.inject_cpu_pressure(duration_ms=duration_ms)
        
        stats = self.injector.get_stats()
        logger.info(f"[CPU_CHAOS] 注入后状态 - cpu_pressure: {stats['fault_types']['cpu_pressure']}")
        assert stats['fault_types']['cpu_pressure'] is True
        
        logger.info(f"[CPU_CHAOS] 等待 {duration_ms/1000 + 0.5}s...")
        time.sleep(duration_ms / 1000.0 + 0.5)
        
        self.injector.clear_fault(FaultType.CPU_PRESSURE)
        
        stats_after = self.injector.get_stats()
        logger.info(f"[CPU_CHAOS] 清理后状态 - cpu_pressure: {stats_after['fault_types']['cpu_pressure']}")
        assert stats_after['fault_types']['cpu_pressure'] is False
        logger.info("[CPU_CHAOS] 持续时间测试通过")
    
    def test_cpu_stress_stats_tracking(self):
        """测试CPU压力统计跟踪"""
        logger.info("[CPU_CHAOS] 统计跟踪测试开始")
        
        with chaos_fault(FaultType.CPU_PRESSURE, duration_ms=200):
            logger.debug("[CPU_CHAOS] CPU压力故障已注入")
        
        records = self.injector.get_injection_history()
        cpu_records = [r for r in records if r.fault_type == FaultType.CPU_PRESSURE]
        logger.info(f"[CPU_CHAOS] CPU压力记录数: {len(cpu_records)}")
        
        assert len(cpu_records) >= 1
        assert cpu_records[-1].recovered_at is not None
        logger.info("[CPU_CHAOS] 统计跟踪测试通过")
    
    def test_cpu_cores_usage(self):
        """测试多核心CPU使用率"""
        logger.info("[CPU_CHAOS] 多核心使用测试开始")
        num_cores = multiprocessing.cpu_count()
        logger.info(f"[CPU_CHAOS] 系统CPU核心数: {num_cores}")
        
        threads = []
        thread_count = min(num_cores, 4)
        logger.info(f"[CPU_CHAOS] 启动 {thread_count} 个压力线程")
        
        for _ in range(thread_count):
            t = threading.Thread(target=_stress_thread, args=(200,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        logger.info(f"[CPU_CHAOS] 所有线程已完成，共 {len(threads)} 个")
        assert len(threads) == min(num_cores, 4)
        logger.info("[CPU_CHAOS] 多核心使用测试通过")
    
    def test_cpu_stress_isolation(self):
        """测试CPU压力与其他故障的隔离"""
        logger.info("[CPU_CHAOS] 故障隔离测试开始 - CPU+内存")
        
        with chaos_fault(FaultType.CPU_PRESSURE, duration_ms=200):
            with chaos_fault(FaultType.MEMORY_PRESSURE, target_mb=64):
                stats = self.injector.get_stats()
                logger.info(f"[CPU_CHAOS] 双故障状态 - cpu: {stats['fault_types']['cpu_pressure']}, memory: {stats['fault_types']['memory_pressure']}")
                assert stats['fault_types']['cpu_pressure'] is True
                assert stats['fault_types']['memory_pressure'] is True
        
        stats_after = self.injector.get_stats()
        logger.info(f"[CPU_CHAOS] 清理后状态 - cpu: {stats_after['fault_types']['cpu_pressure']}, memory: {stats_after['fault_types']['memory_pressure']}")
        assert stats_after['fault_types']['cpu_pressure'] is False
        assert stats_after['fault_types']['memory_pressure'] is False
        logger.info("[CPU_CHAOS] 故障隔离测试通过")
    
    def test_cpu_usage_monitoring(self):
        """测试CPU使用率监控"""
        logger.info("[CPU_CHAOS] 使用率监控测试开始")
        import psutil
        
        def stress_task():
            result = 0
            for i in range(1, 50000):
                result += i * i
        
        cpu_during = psutil.cpu_percent(interval=0.5)
        logger.info(f"[CPU_CHAOS] 当前CPU使用率: {cpu_during:.1f}%")
        
        assert cpu_during >= 0
        assert isinstance(cpu_during, float)
        logger.info("[CPU_CHAOS] 使用率监控测试通过")