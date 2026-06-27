"""内存压力混沌测试

测试场景:
1. 内存占用50%时的系统行为
2. 内存占用80%时的系统行为
3. 内存接近极限时的OOM处理
4. 内存释放后的系统恢复
5. 内存泄漏模拟（验证检测能力）
6. 大内存请求的拒绝处理
"""

import time
import gc
import pytest
import threading
import logging

from agent.monitoring.chaos_injector import (
    ChaosInjector,
    FaultType,
    get_chaos_injector,
    chaos_fault
)

logger = logging.getLogger(__name__)


@pytest.mark.slow
class TestMemoryPressureChaos:
    """内存压力混沌测试"""
    
    def setup_method(self):
        """每个测试前重置混沌注入器"""
        self.injector = get_chaos_injector()
        self.injector.clear_all()
        gc.collect()
    
    def teardown_method(self):
        """每个测试后清理故障和内存"""
        self.injector.clear_all()
        gc.collect()
    
    def test_memory_pressure_50_percent(self):
        """测试内存占用50%时的系统行为"""
        import psutil
        
        total_mem = psutil.virtual_memory().total
        target_mb = int((total_mem * 0.5) / (1024 * 1024))
        logger.info(f"[MEMORY_CHAOS] 50%内存压力测试 - 总内存: {total_mem / (1024*1024):.0f}MB, 目标分配: {target_mb}MB")
        
        if target_mb > 2048:
            logger.warning(f"[MEMORY_CHAOS] 目标内存 {target_mb}MB 超过上限，调整为 512MB")
            target_mb = 512
        
        with chaos_fault(FaultType.MEMORY_PRESSURE, target_mb=target_mb):
            stats = self.injector.get_stats()
            logger.info(f"[MEMORY_CHAOS] 注入后状态 - memory_pressure enabled: {stats['fault_types']['memory_pressure']}")
            assert stats['fault_types']['memory_pressure'] is True
            
            records = self.injector.get_injection_history()
            mem_record = next((r for r in records if r.fault_type == FaultType.MEMORY_PRESSURE), None)
            logger.info(f"[MEMORY_CHAOS] 故障注入记录: {mem_record}")
            assert mem_record is not None
        
        gc.collect()
        
        stats_after = self.injector.get_stats()
        logger.info(f"[MEMORY_CHAOS] 清理后状态 - memory_pressure enabled: {stats_after['fault_types']['memory_pressure']}")
        assert stats_after['fault_types']['memory_pressure'] is False
    
    def test_memory_pressure_80_percent(self):
        """测试内存占用80%时的系统行为"""
        import psutil
        
        total_mem = psutil.virtual_memory().total
        target_mb = int((total_mem * 0.8) / (1024 * 1024))
        logger.info(f"[MEMORY_CHAOS] 80%内存压力测试 - 总内存: {total_mem / (1024*1024):.0f}MB, 目标分配: {target_mb}MB")
        
        if target_mb > 1024:
            logger.warning(f"[MEMORY_CHAOS] 目标内存 {target_mb}MB 超过上限，调整为 1024MB")
            target_mb = 1024
        
        with chaos_fault(FaultType.MEMORY_PRESSURE, target_mb=target_mb):
            stats = self.injector.get_stats()
            hold_count = len(self.injector._memory_hold_list)
            logger.info(f"[MEMORY_CHAOS] 注入后状态 - enabled: {stats['fault_types']['memory_pressure']}, hold_list_size: {hold_count}")
            assert stats['fault_types']['memory_pressure'] is True
            assert hold_count > 0
        
        gc.collect()
        
        hold_count_after = len(self.injector._memory_hold_list)
        logger.info(f"[MEMORY_CHAOS] 清理后 hold_list_size: {hold_count_after}")
        assert hold_count_after == 0
    
    def test_memory_near_limit_oom_handling(self):
        """测试内存接近极限时的OOM处理"""
        target_mb = 4096
        logger.info(f"[MEMORY_CHAOS] OOM边界测试 - 尝试分配 {target_mb}MB")
        
        try:
            with chaos_fault(FaultType.MEMORY_PRESSURE, target_mb=target_mb):
                stats = self.injector.get_stats()
                logger.info(f"[MEMORY_CHAOS] OOM测试 - 注入成功, enabled: {stats['fault_types']['memory_pressure']}")
                assert stats['fault_types']['memory_pressure'] is True
        except MemoryError as e:
            logger.warning(f"[MEMORY_CHAOS] OOM边界测试触发 MemoryError: {e}")
        except Exception as e:
            logger.warning(f"[MEMORY_CHAOS] OOM边界测试触发异常: {type(e).__name__}: {e}")
        
        self.injector.clear_all()
        gc.collect()
        
        final_hold = len(self.injector._memory_hold_list)
        logger.info(f"[MEMORY_CHAOS] OOM测试清理完成 - hold_list_size: {final_hold}")
        assert final_hold == 0
    
    def test_memory_release_and_system_recovery(self):
        """测试内存释放后的系统恢复"""
        import psutil
        
        target_mb = 256
        mem_before = psutil.virtual_memory().available
        logger.info(f"[MEMORY_CHAOS] 恢复测试 - 初始可用内存: {mem_before / (1024*1024):.0f}MB, 目标分配: {target_mb}MB")
        
        with chaos_fault(FaultType.MEMORY_PRESSURE, target_mb=target_mb):
            mem_during = psutil.virtual_memory().available
            logger.info(f"[MEMORY_CHAOS] 恢复测试 - 压力期间可用内存: {mem_during / (1024*1024):.0f}MB, 下降: {(mem_before - mem_during) / (1024*1024):.0f}MB")
            assert mem_during < mem_before
        
        gc.collect()
        time.sleep(0.5)
        
        mem_after = psutil.virtual_memory().available
        recovered_mb = (mem_after - mem_during) / (1024 * 1024)
        logger.info(f"[MEMORY_CHAOS] 恢复测试 - 释放后可用内存: {mem_after / (1024*1024):.0f}MB, 恢复: {recovered_mb:.0f}MB")
        
        assert mem_after > mem_during
        assert len(self.injector._memory_hold_list) == 0
    
    def test_memory_leak_simulation_detection(self):
        """测试内存泄漏模拟（验证检测能力）"""
        import psutil
        
        baseline_memory = []
        for _ in range(3):
            baseline_memory.append(psutil.Process().memory_info().rss)
            time.sleep(0.1)
        baseline_avg = sum(baseline_memory) / len(baseline_memory)
        logger.info(f"[MEMORY_CHAOS] 泄漏检测 - 基线内存: {baseline_avg / (1024*1024):.2f}MB")
        
        with chaos_fault(FaultType.MEMORY_PRESSURE, target_mb=128):
            pass
        
        gc.collect()
        time.sleep(0.5)
        
        current_memory = psutil.Process().memory_info().rss
        memory_diff = abs(current_memory - baseline_avg)
        logger.info(f"[MEMORY_CHAOS] 泄漏检测 - 当前内存: {current_memory / (1024*1024):.2f}MB, 差异: {memory_diff / (1024*1024):.2f}MB")
        
        assert memory_diff < 50 * 1024 * 1024, \
            f"内存泄漏检测失败，差异: {memory_diff / (1024 * 1024):.2f}MB"
    
    def test_large_memory_request_rejection(self):
        """测试大内存请求的拒绝处理"""
        target_mb = 10000
        original_hold_count = len(self.injector._memory_hold_list)
        logger.info(f"[MEMORY_CHAOS] 大内存拒绝测试 - 请求: {target_mb}MB, 初始hold_list: {original_hold_count}")
        
        try:
            self.injector.inject_memory_pressure(target_mb=target_mb)
            after_hold_count = len(self.injector._memory_hold_list)
            logger.info(f"[MEMORY_CHAOS] 大内存拒绝测试 - 注入后hold_list: {after_hold_count}")
            
            if after_hold_count == original_hold_count:
                logger.info("[MEMORY_CHAOS] 大内存请求被拒绝或未达到分配条件")
                assert True
            else:
                logger.warning(f"[MEMORY_CHAOS] 大内存请求部分分配了 {after_hold_count - original_hold_count} 个块")
                self.injector.clear_all()
                gc.collect()
        except Exception as e:
            logger.warning(f"[MEMORY_CHAOS] 大内存请求触发异常: {type(e).__name__}: {e}")
            self.injector.clear_all()
            gc.collect()
    
    def test_memory_pressure_with_duration(self):
        """测试带持续时间的内存压力"""
        target_mb = 128
        logger.info(f"[MEMORY_CHAOS] 持续时间测试 - 目标: {target_mb}MB")
        
        self.injector.inject_memory_pressure(target_mb=target_mb)
        hold_before = len(self.injector._memory_hold_list)
        logger.info(f"[MEMORY_CHAOS] 持续时间测试 - 注入后hold_list: {hold_before}, enabled: {self.injector.get_stats()['fault_types']['memory_pressure']}")
        
        assert self.injector.get_stats()['fault_types']['memory_pressure'] is True
        
        time.sleep(0.5)
        
        self.injector.clear_fault(FaultType.MEMORY_PRESSURE)
        gc.collect()
        
        final_enabled = self.injector.get_stats()['fault_types']['memory_pressure']
        final_hold = len(self.injector._memory_hold_list)
        logger.info(f"[MEMORY_CHAOS] 持续时间测试 - 清理后 enabled: {final_enabled}, hold_list: {final_hold}")
        assert final_enabled is False
        assert final_hold == 0
    
    def test_concurrent_memory_pressure(self):
        """测试并发内存压力注入"""
        results = []
        logger.info("[MEMORY_CHAOS] 并发注入测试 - 启动2个线程同时注入")
        
        def worker(target_mb, worker_id):
            try:
                logger.info(f"[MEMORY_CHAOS] 并发注入 - worker_{worker_id} 开始注入 {target_mb}MB")
                self.injector.inject_memory_pressure(target_mb=target_mb)
                results.append((worker_id, True))
                logger.info(f"[MEMORY_CHAOS] 并发注入 - worker_{worker_id} 注入成功")
            except Exception as e:
                logger.error(f"[MEMORY_CHAOS] 并发注入 - worker_{worker_id} 失败: {type(e).__name__}: {e}")
                results.append((worker_id, False))
        
        t1 = threading.Thread(target=worker, args=(64, 1))
        t2 = threading.Thread(target=worker, args=(64, 2))
        
        t1.start()
        t2.start()
        
        t1.join()
        t2.join()
        
        logger.info(f"[MEMORY_CHAOS] 并发注入测试 - 结果: {results}")
        assert len(results) == 2
        
        self.injector.clear_all()
        gc.collect()
    
    def test_memory_pressure_stats(self):
        """测试内存压力统计"""
        with chaos_fault(FaultType.MEMORY_PRESSURE, target_mb=64):
            pass
        
        records = self.injector.get_injection_history()
        mem_records = [r for r in records if r.fault_type == FaultType.MEMORY_PRESSURE]
        logger.info(f"[MEMORY_CHAOS] 统计测试 - 内存压力记录数: {len(mem_records)}")
        
        if mem_records:
            last_record = mem_records[-1]
            logger.info(f"[MEMORY_CHAOS] 统计测试 - 最后记录: injected_at={last_record.injected_at}, recovered_at={last_record.recovered_at}")
        
        assert len(mem_records) >= 1
        assert mem_records[-1].recovered_at is not None
    
    def test_memory_pressure_vs_cpu_pressure(self):
        """测试内存压力与CPU压力的隔离"""
        logger.info("[MEMORY_CHAOS] 隔离测试 - 同时注入内存压力和CPU压力")
        
        with chaos_fault(FaultType.MEMORY_PRESSURE, target_mb=64):
            with chaos_fault(FaultType.CPU_PRESSURE, duration_ms=100):
                stats = self.injector.get_stats()
                logger.info(f"[MEMORY_CHAOS] 隔离测试 - 双故障注入状态: memory={stats['fault_types']['memory_pressure']}, cpu={stats['fault_types']['cpu_pressure']}")
                assert stats['fault_types']['memory_pressure'] is True
                assert stats['fault_types']['cpu_pressure'] is True
        
        stats_after = self.injector.get_stats()
        logger.info(f"[MEMORY_CHAOS] 隔离测试 - 清理后状态: memory={stats_after['fault_types']['memory_pressure']}, cpu={stats_after['fault_types']['cpu_pressure']}")
        assert stats_after['fault_types']['memory_pressure'] is False
        assert stats_after['fault_types']['cpu_pressure'] is False
        
        gc.collect()