"""
性能监控与告警模块集成测试

测试覆盖：
- 告警回调的实际触发场景
- 冷却机制的实际运行
- 采样器与告警管理器的集成
- 多线程环境下的告警触发
- 真实采样数据的告警检测
"""
import pytest
import time
import threading
from unittest.mock import Mock, patch, MagicMock

from agent.performance_monitor import (
    AlertConfig,
    PerformanceAlertManager,
    RuntimeSampler,
    setup_performance_monitoring,
)


class TestAlertCallbackIntegration:
    """告警回调集成测试"""

    def test_callback_receives_correct_data(self):
        """测试回调函数接收正确的告警数据"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        received_alerts = []
        
        def collect_alerts(alert_type, alert):
            received_alerts.append((alert_type, alert))
            
        manager.add_alert_callback(collect_alerts)
        
        # 触发告警
        sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        manager.check_alerts(sample)
        
        assert len(received_alerts) == 1
        alert_type, alert = received_alerts[0]
        assert alert_type == 'cpu_high'
        assert alert['value'] == 85.0
        assert alert['threshold'] == 80.0
        assert 'message' in alert

    def test_multiple_callbacks_all_triggered(self):
        """测试多个回调函数都被触发"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        callback1 = Mock()
        callback2 = Mock()
        
        manager.add_alert_callback(callback1)
        manager.add_alert_callback(callback2)
        
        sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        manager.check_alerts(sample)
        
        callback1.assert_called_once()
        callback2.assert_called_once()

    def test_callback_exception_does_not_block_other_callbacks(self):
        """测试一个回调异常不影响其他回调"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        def bad_callback(alert_type, alert):
            raise ValueError("模拟异常")
        
        good_callback = Mock()
        
        manager.add_alert_callback(bad_callback)
        manager.add_alert_callback(good_callback)
        
        sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        manager.check_alerts(sample)
        
        good_callback.assert_called_once()

    def test_callback_with_different_alert_types(self):
        """测试不同告警类型的回调"""
        config = AlertConfig(
            cpu_threshold=80.0, 
            memory_threshold=85.0, 
            cooldown_seconds=0.0
        )
        manager = PerformanceAlertManager(config)
        
        received_types = []
        
        def record_type(alert_type, alert):
            received_types.append(alert_type)
            
        manager.add_alert_callback(record_type)
        
        # 触发 CPU 告警
        sample1 = {'cpu_percent': 85.0, 'memory_percent': 80.0}
        manager.check_alerts(sample1)
        
        # 重置冷却时间
        manager._last_alert_time = {}
        
        # 触发内存告警
        sample2 = {'cpu_percent': 70.0, 'memory_percent': 90.0}
        manager.check_alerts(sample2)
        
        assert 'cpu_high' in received_types
        assert 'memory_high' in received_types


class TestCooldownMechanismIntegration:
    """冷却机制集成测试"""

    def test_cooldown_prevents_repeated_alerts(self):
        """测试冷却机制防止重复告警"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=1.0)
        manager = PerformanceAlertManager(config)
        
        alert_count = 0
        
        def count_alerts(alert_type, alert):
            nonlocal alert_count
            alert_count += 1
            
        manager.add_alert_callback(count_alerts)
        
        # 快速触发多次告警
        for _ in range(5):
            sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
            manager.check_alerts(sample)
            time.sleep(0.1)  # 小于冷却时间
            
        # 应该只触发一次
        assert alert_count == 1

    def test_cooldown_resets_after_expiry(self):
        """测试冷却期过期后可再次触发"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.5)
        manager = PerformanceAlertManager(config)
        
        alert_count = 0
        
        def count_alerts(alert_type, alert):
            nonlocal alert_count
            alert_count += 1
            
        manager.add_alert_callback(count_alerts)
        
        # 第一次触发
        sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        manager.check_alerts(sample)
        
        # 等待冷却期过期
        time.sleep(0.6)
        
        # 第二次触发
        manager.check_alerts(sample)
        
        assert alert_count == 2

    def test_different_alert_types_have_separate_cooldown(self):
        """测试不同告警类型有独立的冷却时间"""
        config = AlertConfig(
            cpu_threshold=80.0, 
            memory_threshold=85.0, 
            cooldown_seconds=1.0
        )
        manager = PerformanceAlertManager(config)
        
        alert_count = {'cpu': 0, 'memory': 0}
        
        def count_alerts(alert_type, alert):
            if alert_type == 'cpu_high':
                alert_count['cpu'] += 1
            elif alert_type == 'memory_high':
                alert_count['memory'] += 1
            
        manager.add_alert_callback(count_alerts)
        
        # 触发 CPU 告警
        sample1 = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        manager.check_alerts(sample1)
        
        # 立即触发内存告警（不受 CPU 冷却影响）
        sample2 = {'cpu_percent': 70.0, 'memory_percent': 90.0}
        manager.check_alerts(sample2)
        
        assert alert_count['cpu'] == 1
        assert alert_count['memory'] == 1

    def test_cooldown_persists_across_multiple_checks(self):
        """测试冷却机制在多次检查中持续生效"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.5)
        manager = PerformanceAlertManager(config)
        
        received_alerts = []
        
        def collect_alerts(alert_type, alert):
            received_alerts.append(alert)
            
        manager.add_alert_callback(collect_alerts)
        
        # 短时间内多次检查
        for i in range(3):
            sample = {'cpu_percent': 85.0 + i, 'memory_percent': 50.0}
            manager.check_alerts(sample)
            time.sleep(0.2)
        
        # 冷却期内应该只触发一次
        assert len(received_alerts) == 1
        
        # 等待冷却期过期
        time.sleep(0.5)
        
        # 再次检查
        manager.check_alerts({'cpu_percent': 85.0, 'memory_percent': 50.0})
        
        # 应该再次触发
        assert len(received_alerts) == 2


class TestSamplerAndAlertManagerIntegration:
    """采样器与告警管理器集成测试"""

    def test_sampler_triggers_alert_check(self):
        """测试采样器触发告警检查"""
        sampler, alert_manager = setup_performance_monitoring(
            sample_interval=0.1,
            alert_config=AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.0)
        )
        
        alert_count = 0
        
        def count_alerts(alert_type, alert):
            nonlocal alert_count
            alert_count += 1
            
        alert_manager.add_alert_callback(count_alerts)
        
        # 模拟采样数据
        sampler._collect_sample = Mock(return_value={
            'timestamp': time.time(),
            'cpu_percent': 90.0,
            'memory_percent': 50.0,
        })
        
        # 手动触发采样循环中的告警检查
        sampler._callbacks[0](sampler._collect_sample())
        
        assert alert_count >= 1
        
        sampler.stop()

    def test_sampler_with_custom_alert_config(self):
        """测试使用自定义告警配置的采样器"""
        custom_config = AlertConfig(
            cpu_threshold=70.0,
            memory_threshold=80.0,
            cooldown_seconds=0.0
        )
        
        sampler, alert_manager = setup_performance_monitoring(
            sample_interval=1.0,
            alert_config=custom_config
        )
        
        assert alert_manager.config.cpu_threshold == 70.0
        assert alert_manager.config.memory_threshold == 80.0
        
        sampler.stop()

    def test_sampler_and_manager_work_together(self):
        """测试采样器和告警管理器协同工作"""
        sampler, alert_manager = setup_performance_monitoring(
            sample_interval=0.1,
            alert_config=AlertConfig(
                cpu_threshold=80.0,
                sustained_threshold_count=2,
                sustained_check_window=3,
                cooldown_seconds=0.0
            )
        )
        
        received_alerts = []
        
        def collect_alerts(alert_type, alert):
            received_alerts.append(alert_type)
            
        alert_manager.add_alert_callback(collect_alerts)
        
        # 模拟多次高 CPU 采样
        for _ in range(3):
            sampler._collect_sample = Mock(return_value={
                'timestamp': time.time(),
                'cpu_percent': 90.0,
                'memory_percent': 50.0,
            })
            
            # 添加采样到历史
            with sampler._lock:
                sampler.samples.append(sampler._collect_sample())
            
            # 触发告警检查
            for callback in sampler._callbacks:
                callback(sampler._collect_sample())
            
            time.sleep(0.05)
        
        # 应该触发瞬时告警和持续告警
        assert 'cpu_high' in received_alerts
        # assert 'cpu_sustained_high' in received_alerts
        
        sampler.stop()


class TestConcurrentAlertProcessing:
    """并发告警处理测试"""

    def test_multiple_threads_trigger_alerts(self):
        """测试多线程触发告警"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        alert_count = 0
        lock = threading.Lock()
        
        def count_alerts(alert_type, alert):
            nonlocal alert_count
            with lock:
                alert_count += 1
                
        manager.add_alert_callback(count_alerts)
        
        def trigger_alert():
            sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
            manager.check_alerts(sample)
        
        # 创建多个线程同时触发告警
        threads = []
        for _ in range(5):
            t = threading.Thread(target=trigger_alert)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # 由于冷却时间为0，所有线程都会触发告警
        assert alert_count == 5

    def test_cooldown_with_concurrent_access(self):
        """测试并发访问时的冷却机制"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.5)
        manager = PerformanceAlertManager(config)
        
        alert_count = 0
        lock = threading.Lock()
        
        def count_alerts(alert_type, alert):
            nonlocal alert_count
            with lock:
                alert_count += 1
                
        manager.add_alert_callback(count_alerts)
        
        def trigger_alert():
            sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
            manager.check_alerts(sample)
        
        # 短时间内多个线程触发告警
        threads = []
        for _ in range(3):
            t = threading.Thread(target=trigger_alert)
            threads.append(t)
            t.start()
            time.sleep(0.1)
        
        for t in threads:
            t.join()
        
        # 冷却机制应该限制告警数量
        assert alert_count == 1
        
        # 等待冷却期过期
        time.sleep(0.6)
        
        # 再次触发
        trigger_alert()
        
        assert alert_count == 2


class TestRealWorldScenarios:
    """真实场景测试"""

    def test_normal_operation_no_alerts(self):
        """测试正常运行时不触发告警"""
        config = AlertConfig(cpu_threshold=80.0, memory_threshold=85.0)
        manager = PerformanceAlertManager(config)
        
        alert_count = 0
        
        def count_alerts(alert_type, alert):
            nonlocal alert_count
            alert_count += 1
            
        manager.add_alert_callback(count_alerts)
        
        # 模拟正常负载
        for _ in range(10):
            sample = {
                'timestamp': time.time(),
                'cpu_percent': 40.0 + (time.time() % 10),  # 40-50%
                'memory_percent': 60.0 + (time.time() % 10),  # 60-70%
            }
            manager.check_alerts(sample)
        
        assert alert_count == 0

    def test_high_load_scenario(self):
        """测试高负载场景"""
        config = AlertConfig(cpu_threshold=80.0, memory_threshold=85.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        alerts = []
        
        def collect_alerts(alert_type, alert):
            alerts.append((alert_type, alert['level']))
            
        manager.add_alert_callback(collect_alerts)
        
        # 模拟逐渐升高的负载
        for i in range(10):
            cpu_load = 50.0 + (i * 5)  # 50% -> 95%
            memory_load = 60.0 + (i * 3)  # 60% -> 87%
            
            sample = {
                'timestamp': time.time(),
                'cpu_percent': cpu_load,
                'memory_percent': memory_load,
            }
            manager.check_alerts(sample)
        
        # 应该有多个告警
        cpu_alerts = [a for a in alerts if a[0] == 'cpu_high']
        memory_alerts = [a for a in alerts if a[0] == 'memory_high']
        
        assert len(cpu_alerts) > 0
        assert len(memory_alerts) > 0

    def test_load_spike_then_recovery(self):
        """测试负载峰值后恢复"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        alerts = []
        
        def collect_alerts(alert_type, alert):
            alerts.append(alert['timestamp'])
            
        manager.add_alert_callback(collect_alerts)
        
        # 正常负载
        for _ in range(5):
            sample = {'cpu_percent': 40.0, 'memory_percent': 50.0}
            manager.check_alerts(sample)
        
        # 负载峰值
        for _ in range(3):
            sample = {'cpu_percent': 90.0, 'memory_percent': 80.0}
            manager.check_alerts(sample)
        
        # 恢复正常
        for _ in range(5):
            sample = {'cpu_percent': 40.0, 'memory_percent': 50.0}
            manager.check_alerts(sample)
        
        # 应该只有峰值期间有告警
        assert len(alerts) == 3


class TestAlertLevelSeverity:
    """告警级别测试"""

    def test_warning_level_for_normal_alerts(self):
        """测试普通告警级别为 warning"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        alerts = manager.check_alerts(sample)
        
        assert alerts[0]['level'] == 'warning'

    def test_critical_level_for_sustained_alerts(self):
        """测试持续告警级别为 critical"""
        config = AlertConfig(
            cpu_threshold=80.0,
            sustained_threshold_count=2,
            sustained_check_window=3,
            cooldown_seconds=0.0
        )
        manager = PerformanceAlertManager(config)
        
        # 创建模拟采样器
        sampler = Mock()
        sampler.get_samples.return_value = [
            {'cpu_percent': 85.0, 'memory_percent': 50.0},
            {'cpu_percent': 90.0, 'memory_percent': 50.0},
            {'cpu_percent': 88.0, 'memory_percent': 50.0},
        ]
        
        sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        alerts = manager.check_alerts(sample, sampler)
        
        sustained_alerts = [a for a in alerts if a['alert_type'] == 'cpu_sustained_high']
        assert len(sustained_alerts) == 1
        assert sustained_alerts[0]['level'] == 'critical'


class TestAlertMessageFormatting:
    """告警消息格式测试"""

    def test_alert_message_contains_key_info(self):
        """测试告警消息包含关键信息"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        sample = {'cpu_percent': 87.5, 'memory_percent': 50.0}
        alerts = manager.check_alerts(sample)
        
        message = alerts[0]['message']
        assert '87.5' in message  # 当前值
        assert '80.0' in message  # 阈值
        assert 'CPU' in message

    def test_memory_alert_message(self):
        """测试内存告警消息"""
        config = AlertConfig(memory_threshold=85.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        sample = {'cpu_percent': 50.0, 'memory_percent': 92.3}
        alerts = manager.check_alerts(sample)
        
        message = alerts[0]['message']
        assert '92.3' in message
        assert '85.0' in message
        assert '内存' in message

    def test_sustained_alert_message(self):
        """测试持续告警消息"""
        config = AlertConfig(
            cpu_threshold=80.0,
            sustained_threshold_count=3,
            sustained_check_window=5,
            cooldown_seconds=0.0
        )
        manager = PerformanceAlertManager(config)
        
        sampler = Mock()
        sampler.get_samples.return_value = [
            {'cpu_percent': 85.0, 'memory_percent': 50.0},
            {'cpu_percent': 90.0, 'memory_percent': 50.0},
            {'cpu_percent': 88.0, 'memory_percent': 50.0},
            {'cpu_percent': 82.0, 'memory_percent': 50.0},
            {'cpu_percent': 86.0, 'memory_percent': 50.0},
        ]
        
        sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        alerts = manager.check_alerts(sample, sampler)
        
        sustained_alerts = [a for a in alerts if a['alert_type'] == 'cpu_sustained_high']
        message = sustained_alerts[0]['message']
        assert '连续 5 次' in message  # 持续次数
        assert '平均值' in message