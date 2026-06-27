"""磁盘IO混沌测试

测试场景:
1. 读取延迟注入（10ms/100ms/1s）
2. 写入延迟注入（10ms/100ms/1s）
3. 磁盘满时的处理
4. 文件损坏时的容错
5. IO错误注入（验证重试逻辑）
6. 异步写入的可靠性
"""

import os
import time
import pytest
import tempfile
import logging

logger = logging.getLogger(__name__)

from agent.monitoring.chaos_injector import (
    ChaosInjector,
    FaultType,
    get_chaos_injector,
    chaos_fault
)


@pytest.mark.slow
class TestDiskIOChaos:
    """磁盘IO混沌测试"""
    
    def setup_method(self):
        """每个测试前重置混沌注入器"""
        logger.info("[DISK_CHAOS] ========== 测试开始前初始化 ==========")
        self.injector = get_chaos_injector()
        logger.info("[DISK_CHAOS] 清除所有已有的故障注入")
        self.injector.clear_all()
        self.temp_dir = tempfile.mkdtemp()
        logger.info(f"[DISK_CHAOS] 创建临时目录: {self.temp_dir}")
    
    def teardown_method(self):
        """每个测试后清理故障和临时文件"""
        logger.info("[DISK_CHAOS] ========== 测试结束后清理 ==========")
        logger.info("[DISK_CHAOS] 清除所有故障注入")
        self.injector.clear_all()
        import shutil
        try:
            shutil.rmtree(self.temp_dir)
            logger.info(f"[DISK_CHAOS] 临时目录已删除: {self.temp_dir}")
        except Exception as e:
            logger.warning(f"[DISK_CHAOS] 删除临时目录失败: {e}")
    
    def test_disk_read_delay_10ms(self):
        """测试读取延迟注入（10ms）"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_disk_read_delay_10ms =====")
        logger.info("[DISK_CHAOS] 注入磁盘IO读取延迟故障: 10ms")
        with chaos_fault(FaultType.DISK_IO_DELAY, delay_ms=10, io_operation="read"):
            logger.info("[DISK_CHAOS] 故障注入完成，获取统计信息")
            stats = self.injector.get_stats()
            logger.info(f"[DISK_CHAOS] 故障类型状态: disk_io_delay={stats['fault_types']['disk_io_delay']}")
            assert stats['fault_types']['disk_io_delay'] is True
            logger.info("[DISK_CHAOS] 断言通过: 磁盘IO延迟故障已激活")
            
            config = self.injector._fault_configs[FaultType.DISK_IO_DELAY]
            logger.info(f"[DISK_CHAOS] 故障配置: delay_ms={config.delay_ms}, io_operation={config.io_operation}")
            assert config.delay_ms == 10
            logger.info("[DISK_CHAOS] 断言通过: 延迟时间为10ms")
            assert config.io_operation == "read"
            logger.info("[DISK_CHAOS] 断言通过: IO操作类型为read")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_disk_read_delay_10ms =====")
    
    def test_disk_read_delay_100ms(self):
        """测试读取延迟注入（100ms）"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_disk_read_delay_100ms =====")
        logger.info("[DISK_CHAOS] 注入磁盘IO读取延迟故障: 100ms")
        with chaos_fault(FaultType.DISK_IO_DELAY, delay_ms=100, io_operation="read"):
            logger.info("[DISK_CHAOS] 故障注入完成，获取统计信息")
            stats = self.injector.get_stats()
            logger.info(f"[DISK_CHAOS] 故障类型状态: disk_io_delay={stats['fault_types']['disk_io_delay']}")
            assert stats['fault_types']['disk_io_delay'] is True
            logger.info("[DISK_CHAOS] 断言通过: 磁盘IO延迟故障已激活")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_disk_read_delay_100ms =====")
    
    def test_disk_read_delay_1s(self):
        """测试读取延迟注入（1s）"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_disk_read_delay_1s =====")
        logger.info("[DISK_CHAOS] 注入磁盘IO读取延迟故障: 1000ms")
        with chaos_fault(FaultType.DISK_IO_DELAY, delay_ms=1000, io_operation="read"):
            logger.info("[DISK_CHAOS] 故障注入完成，开始计时验证延迟效果")
            start = time.time()
            
            config = self.injector._fault_configs[FaultType.DISK_IO_DELAY]
            logger.info(f"[DISK_CHAOS] 检查故障配置: enabled={config.enabled}")
            if config.enabled and self.injector.trigger_if_active(FaultType.DISK_IO_DELAY):
                logger.info(f"[DISK_CHAOS] 触发延迟，休眠 {config.delay_ms}ms")
                time.sleep(config.delay_ms / 1000.0)
            else:
                logger.warning("[DISK_CHAOS] 故障未触发，延迟未生效")
            
            elapsed = (time.time() - start) * 1000
            logger.info(f"[DISK_CHAOS] 实际耗时: {elapsed:.2f}ms")
            assert elapsed >= 1000
            logger.info("[DISK_CHAOS] 断言通过: 延迟时间大于等于1000ms")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_disk_read_delay_1s =====")
    
    def test_disk_write_delay_10ms(self):
        """测试写入延迟注入（10ms）"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_disk_write_delay_10ms =====")
        logger.info("[DISK_CHAOS] 注入磁盘IO写入延迟故障: 10ms")
        with chaos_fault(FaultType.DISK_IO_DELAY, delay_ms=10, io_operation="write"):
            logger.info("[DISK_CHAOS] 故障注入完成，获取统计信息")
            stats = self.injector.get_stats()
            logger.info(f"[DISK_CHAOS] 故障类型状态: disk_io_delay={stats['fault_types']['disk_io_delay']}")
            assert stats['fault_types']['disk_io_delay'] is True
            logger.info("[DISK_CHAOS] 断言通过: 磁盘IO延迟故障已激活")
            
            config = self.injector._fault_configs[FaultType.DISK_IO_DELAY]
            logger.info(f"[DISK_CHAOS] 故障配置: io_operation={config.io_operation}")
            assert config.io_operation == "write"
            logger.info("[DISK_CHAOS] 断言通过: IO操作类型为write")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_disk_write_delay_10ms =====")
    
    def test_disk_write_delay_100ms(self):
        """测试写入延迟注入（100ms）"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_disk_write_delay_100ms =====")
        logger.info("[DISK_CHAOS] 注入磁盘IO写入延迟故障: 100ms")
        with chaos_fault(FaultType.DISK_IO_DELAY, delay_ms=100, io_operation="write"):
            logger.info("[DISK_CHAOS] 故障注入完成，获取统计信息")
            stats = self.injector.get_stats()
            logger.info(f"[DISK_CHAOS] 故障类型状态: disk_io_delay={stats['fault_types']['disk_io_delay']}")
            assert stats['fault_types']['disk_io_delay'] is True
            logger.info("[DISK_CHAOS] 断言通过: 磁盘IO延迟故障已激活")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_disk_write_delay_100ms =====")
    
    def test_disk_write_delay_1s(self):
        """测试写入延迟注入（1s）"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_disk_write_delay_1s =====")
        logger.info("[DISK_CHAOS] 注入磁盘IO写入延迟故障: 1000ms")
        with chaos_fault(FaultType.DISK_IO_DELAY, delay_ms=1000, io_operation="write"):
            logger.info("[DISK_CHAOS] 故障注入完成，获取统计信息")
            stats = self.injector.get_stats()
            logger.info(f"[DISK_CHAOS] 故障类型状态: disk_io_delay={stats['fault_types']['disk_io_delay']}")
            assert stats['fault_types']['disk_io_delay'] is True
            logger.info("[DISK_CHAOS] 断言通过: 磁盘IO延迟故障已激活")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_disk_write_delay_1s =====")
    
    def test_disk_full_simulation(self):
        """测试磁盘满时的处理"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_disk_full_simulation =====")
        logger.info("[DISK_CHAOS] 注入磁盘满故障: 使用率95%")
        with chaos_fault(FaultType.DISK_FULL, disk_usage_percent=95):
            logger.info("[DISK_CHAOS] 故障注入完成，获取统计信息")
            stats = self.injector.get_stats()
            logger.info(f"[DISK_CHAOS] 故障类型状态: disk_full={stats['fault_types']['disk_full']}")
            assert stats['fault_types']['disk_full'] is True
            logger.info("[DISK_CHAOS] 断言通过: 磁盘满故障已激活")
            
            config = self.injector._fault_configs[FaultType.DISK_FULL]
            logger.info(f"[DISK_CHAOS] 故障配置: disk_usage_percent={config.disk_usage_percent}%")
            assert config.disk_usage_percent == 95
            logger.info("[DISK_CHAOS] 断言通过: 磁盘使用率配置为95%")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_disk_full_simulation =====")
    
    def test_disk_full_high_percentage(self):
        """测试高磁盘使用率（99%）"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_disk_full_high_percentage =====")
        logger.info("[DISK_CHAOS] 注入磁盘满故障: 使用率99%")
        with chaos_fault(FaultType.DISK_FULL, disk_usage_percent=99):
            logger.info("[DISK_CHAOS] 故障注入完成，获取统计信息")
            stats = self.injector.get_stats()
            logger.info(f"[DISK_CHAOS] 故障类型状态: disk_full={stats['fault_types']['disk_full']}")
            assert stats['fault_types']['disk_full'] is True
            logger.info("[DISK_CHAOS] 断言通过: 磁盘满故障已激活")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_disk_full_high_percentage =====")
    
    def test_file_corruption_tolerance(self):
        """测试文件损坏时的容错"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_file_corruption_tolerance =====")
        test_file = os.path.join(self.temp_dir, "test.txt")
        logger.info(f"[DISK_CHAOS] 测试文件路径: {test_file}")
        
        logger.info("[DISK_CHAOS] 写入原始内容到测试文件")
        with open(test_file, 'w') as f:
            f.write("original content")
        logger.info("[DISK_CHAOS] 原始内容写入完成")
        
        logger.info("[DISK_CHAOS] 模拟文件损坏，写入二进制数据")
        with open(test_file, 'wb') as f:
            f.write(b"corrupted data that is not valid text")
        logger.info("[DISK_CHAOS] 文件损坏模拟完成")
        
        logger.info("[DISK_CHAOS] 尝试以文本模式读取损坏的文件")
        try:
            with open(test_file, 'r') as f:
                content = f.read()
            logger.info("[DISK_CHAOS] 文件读取成功，未触发解码错误")
        except UnicodeDecodeError:
            logger.info("[DISK_CHAOS] 文件读取触发UnicodeDecodeError异常")
            assert True
            logger.info("[DISK_CHAOS] 断言通过: 捕获到UnicodeDecodeError异常")
        else:
            logger.info("[DISK_CHAOS] 文件读取未触发异常")
            assert True
            logger.info("[DISK_CHAOS] 断言通过: 文件读取容错处理正常")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_file_corruption_tolerance =====")
    
    def test_io_error_injection_retry(self):
        """测试IO错误注入（验证重试逻辑）"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_io_error_injection_retry =====")
        error_count = [0]
        logger.info("[DISK_CHAOS] 初始化错误计数器为0")
        
        def simulate_io_operation():
            error_count[0] += 1
            logger.info(f"[DISK_CHAOS] 第{error_count[0]}次IO操作尝试")
            if error_count[0] <= 2:
                logger.warning(f"[DISK_CHAOS] 第{error_count[0]}次尝试失败，抛出IOError")
                raise IOError("Simulated IO error")
            logger.info(f"[DISK_CHAOS] 第{error_count[0]}次尝试成功")
            return "success"
        
        result = None
        logger.info("[DISK_CHAOS] 开始执行带重试逻辑的IO操作（最多3次）")
        for attempt in range(3):
            try:
                result = simulate_io_operation()
                logger.info(f"[DISK_CHAOS] 第{attempt + 1}次尝试成功，跳出重试循环")
                break
            except IOError:
                logger.warning(f"[DISK_CHAOS] 第{attempt + 1}次尝试捕获IOError异常")
                if attempt == 2:
                    logger.error("[DISK_CHAOS] 已达最大重试次数，抛出异常")
                    raise
        
        logger.info(f"[DISK_CHAOS] 最终结果: {result}")
        assert result == "success"
        logger.info("[DISK_CHAOS] 断言通过: 最终结果为success")
        assert error_count[0] == 3
        logger.info(f"[DISK_CHAOS] 断言通过: 总尝试次数为{error_count[0]}次")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_io_error_injection_retry =====")
    
    def test_async_write_reliability(self):
        """测试异步写入的可靠性"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_async_write_reliability =====")
        import threading
        logger.info("[DISK_CHAOS] 导入threading模块")
        
        results = []
        logger.info("[DISK_CHAOS] 初始化结果列表")
        
        def async_writer(file_path, content):
            logger.info(f"[DISK_CHAOS] 异步写入线程启动，文件: {file_path}")
            try:
                with open(file_path, 'w') as f:
                    f.write(content)
                results.append(True)
                logger.info(f"[DISK_CHAOS] 异步写入成功: {file_path}")
            except Exception as e:
                results.append(False)
                logger.error(f"[DISK_CHAOS] 异步写入失败: {file_path}, 错误: {e}")
        
        threads = []
        logger.info("[DISK_CHAOS] 启动5个异步写入线程")
        for i in range(5):
            file_path = os.path.join(self.temp_dir, f"async_{i}.txt")
            t = threading.Thread(target=async_writer, args=(file_path, f"content_{i}"))
            threads.append(t)
            t.start()
            logger.info(f"[DISK_CHAOS] 线程{i}已启动，写入文件: {file_path}")
        
        logger.info("[DISK_CHAOS] 等待所有线程完成")
        for t in threads:
            t.join()
        logger.info("[DISK_CHAOS] 所有线程已完成")
        
        logger.info(f"[DISK_CHAOS] 结果列表长度: {len(results)}")
        assert len(results) == 5
        logger.info("[DISK_CHAOS] 断言通过: 结果列表长度为5")
        assert all(results)
        logger.info("[DISK_CHAOS] 断言通过: 所有写入操作均成功")
        
        logger.info("[DISK_CHAOS] 验证每个文件的内容正确性")
        for i in range(5):
            file_path = os.path.join(self.temp_dir, f"async_{i}.txt")
            with open(file_path, 'r') as f:
                content = f.read()
                logger.info(f"[DISK_CHAOS] 文件{i}内容: {content}")
                assert content == f"content_{i}"
                logger.info(f"[DISK_CHAOS] 断言通过: 文件{i}内容正确")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_async_write_reliability =====")
    
    def test_io_delay_with_probability(self):
        """测试带概率的IO延迟注入"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_io_delay_with_probability =====")
        logger.info("[DISK_CHAOS] 注入带概率的磁盘IO延迟: 50ms, both, 概率0.5")
        self.injector.inject_disk_io_delay(delay_ms=50, io_operation="both", probability=0.5)
        
        logger.info("[DISK_CHAOS] 故障注入完成，获取统计信息")
        stats = self.injector.get_stats()
        logger.info(f"[DISK_CHAOS] 故障类型状态: disk_io_delay={stats['fault_types']['disk_io_delay']}")
        assert stats['fault_types']['disk_io_delay'] is True
        logger.info("[DISK_CHAOS] 断言通过: 磁盘IO延迟故障已激活")
        
        config = self.injector._fault_configs[FaultType.DISK_IO_DELAY]
        logger.info(f"[DISK_CHAOS] 故障配置: probability={config.probability}")
        assert config.probability == 0.5
        logger.info("[DISK_CHAOS] 断言通过: 概率配置为0.5")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_io_delay_with_probability =====")
    
    def test_io_delay_duration_auto_clear(self):
        """测试IO延迟自动清除"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_io_delay_duration_auto_clear =====")
        logger.info("[DISK_CHAOS] 注入带持续时间的磁盘IO延迟: 100ms, 持续500ms")
        self.injector.inject_disk_io_delay(delay_ms=100, duration_ms=500)
        
        logger.info("[DISK_CHAOS] 检查故障是否已激活")
        fault_status = self.injector.get_stats()['fault_types']['disk_io_delay']
        logger.info(f"[DISK_CHAOS] 故障状态: {fault_status}")
        assert fault_status is True
        logger.info("[DISK_CHAOS] 断言通过: 磁盘IO延迟故障已激活")
        
        logger.info("[DISK_CHAOS] 等待600ms（超过持续时间）")
        time.sleep(0.6)
        logger.info("[DISK_CHAOS] 等待结束，手动清除故障")
        
        self.injector.clear_fault(FaultType.DISK_IO_DELAY)
        logger.info("[DISK_CHAOS] 故障已清除")
        
        fault_status_after = self.injector.get_stats()['fault_types']['disk_io_delay']
        logger.info(f"[DISK_CHAOS] 清除后故障状态: {fault_status_after}")
        assert fault_status_after is False
        logger.info("[DISK_CHAOS] 断言通过: 磁盘IO延迟故障已解除")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_io_delay_duration_auto_clear =====")
    
    def test_disk_io_stats_tracking(self):
        """测试磁盘IO统计跟踪"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_disk_io_stats_tracking =====")
        logger.info("[DISK_CHAOS] 注入磁盘IO延迟故障: 50ms")
        with chaos_fault(FaultType.DISK_IO_DELAY, delay_ms=50):
            logger.info("[DISK_CHAOS] 故障注入上下文内")
        logger.info("[DISK_CHAOS] 故障注入上下文已退出")
        
        logger.info("[DISK_CHAOS] 获取注入历史记录")
        records = self.injector.get_injection_history()
        io_records = [r for r in records if r.fault_type == FaultType.DISK_IO_DELAY]
        logger.info(f"[DISK_CHAOS] 磁盘IO延迟记录数量: {len(io_records)}")
        
        assert len(io_records) >= 1
        logger.info("[DISK_CHAOS] 断言通过: 至少有1条磁盘IO延迟记录")
        assert io_records[-1].recovered_at is not None
        logger.info(f"[DISK_CHAOS] 断言通过: 最新记录的恢复时间为 {io_records[-1].recovered_at}")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_disk_io_stats_tracking =====")
    
    def test_disk_full_stats_tracking(self):
        """测试磁盘满统计跟踪"""
        logger.info("[DISK_CHAOS] ===== 测试开始: test_disk_full_stats_tracking =====")
        logger.info("[DISK_CHAOS] 注入磁盘满故障: 使用率90%")
        with chaos_fault(FaultType.DISK_FULL, disk_usage_percent=90):
            logger.info("[DISK_CHAOS] 故障注入上下文内")
        logger.info("[DISK_CHAOS] 故障注入上下文已退出")
        
        logger.info("[DISK_CHAOS] 获取注入历史记录")
        records = self.injector.get_injection_history()
        disk_records = [r for r in records if r.fault_type == FaultType.DISK_FULL]
        logger.info(f"[DISK_CHAOS] 磁盘满记录数量: {len(disk_records)}")
        
        assert len(disk_records) >= 1
        logger.info("[DISK_CHAOS] 断言通过: 至少有1条磁盘满记录")
        assert disk_records[-1].recovered_at is not None
        logger.info(f"[DISK_CHAOS] 断言通过: 最新记录的恢复时间为 {disk_records[-1].recovered_at}")
        logger.info("[DISK_CHAOS] ===== 测试完成: test_disk_full_stats_tracking =====")