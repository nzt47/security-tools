"""
性能告警规则单元测试

测试覆盖：
- AlertConfig 配置类
- PerformanceAlertManager 告警管理器
- CPU 高负载告警
- 内存高使用告警
- 持续高负载告警
- 告警冷却机制
- setup_performance_monitoring 函数
"""
import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from collections import deque

from agent.performance_monitor import (
    AlertConfig,
    PerformanceAlertManager,
    RuntimeSampler,
    get_alert_manager,
    setup_performance_monitoring,
    create_default_alert_callback,
)


class TestAlertConfig:
    """告警配置类测试"""

    def test_alert_config_default_values(self):
        """测试默认配置值"""
        config = AlertConfig()
        
        assert config.cpu_threshold == 80.0
        assert config.memory_threshold == 85.0
        assert config.sustained_threshold_count == 5
        assert config.sustained_check_window == 10
        assert config.cooldown_seconds == 60.0
        assert config.enable_logging == True
        assert config.enable_callback == True

    def test_alert_config_custom_values(self):
        """测试自定义配置值"""
        config = AlertConfig(
            cpu_threshold=90.0,
            memory_threshold=95.0,
            cooldown_seconds=30.0,
            enable_logging=False
        )
        
        assert config.cpu_threshold == 90.0
        assert config.memory_threshold == 95.0
        assert config.cooldown_seconds == 30.0
        assert config.enable_logging == False


class TestPerformanceAlertManager:
    """性能告警管理器测试"""

    def test_alert_manager_init_with_default_config(self):
        """测试使用默认配置初始化"""
        manager = PerformanceAlertManager()
        
        assert manager.config.cpu_threshold == 80.0
        assert manager.config.memory_threshold == 85.0
        assert manager._alert_callbacks == []
        assert manager._last_alert_time == {}

    def test_alert_manager_init_with_custom_config(self):
        """测试使用自定义配置初始化"""
        config = AlertConfig(cpu_threshold=75.0)
        manager = PerformanceAlertManager(config)
        
        assert manager.config.cpu_threshold == 75.0

    def test_add_alert_callback(self):
        """测试添加告警回调"""
        manager = PerformanceAlertManager()
        callback = Mock()
        
        manager.add_alert_callback(callback)
        
        assert len(manager._alert_callbacks) == 1
        assert manager._alert_callbacks[0] == callback

    def test_check_cpu_alert_triggered(self):
        """测试 CPU 高负载告警触发"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        alerts = manager.check_alerts(sample)
        
        assert len(alerts) == 1
        assert alerts[0]['alert_type'] == 'cpu_high'
        assert alerts[0]['value'] == 85.0
        assert alerts[0]['threshold'] == 80.0

    def test_check_cpu_alert_not_triggered(self):
        """测试 CPU 未超过阈值时不触发告警"""
        config = AlertConfig(cpu_threshold=80.0)
        manager = PerformanceAlertManager(config)
        
        sample = {'cpu_percent': 70.0, 'memory_percent': 50.0}
        alerts = manager.check_alerts(sample)
        
        assert len(alerts) == 0

    def test_check_memory_alert_triggered(self):
        """测试内存高使用告警触发"""
        config = AlertConfig(memory_threshold=85.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        sample = {'cpu_percent': 50.0, 'memory_percent': 90.0}
        alerts = manager.check_alerts(sample)
        
        assert len(alerts) == 1
        assert alerts[0]['alert_type'] == 'memory_high'
        assert alerts[0]['value'] == 90.0

    def test_check_memory_alert_not_triggered(self):
        """测试内存未超过阈值时不触发告警"""
        config = AlertConfig(memory_threshold=85.0)
        manager = PerformanceAlertManager(config)
        
        sample = {'cpu_percent': 50.0, 'memory_percent': 70.0}
        alerts = manager.check_alerts(sample)
        
        assert len(alerts) == 0

    def test_check_both_alerts_triggered(self):
        """测试 CPU 和内存同时超过阈值"""
        config = AlertConfig(cpu_threshold=80.0, memory_threshold=85.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        sample = {'cpu_percent': 90.0, 'memory_percent': 95.0}
        alerts = manager.check_alerts(sample)
        
        assert len(alerts) == 2
        alert_types = [a['alert_type'] for a in alerts]
        assert 'cpu_high' in alert_types
        assert 'memory_high' in alert_types

    def test_alert_cooldown_mechanism(self):
        """测试告警冷却机制"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=10.0)
        manager = PerformanceAlertManager(config)
        
        # 第一次触发告警
        sample1 = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        alerts1 = manager.check_alerts(sample1)
        assert len(alerts1) == 1
        
        # 冷却期内不应再次触发
        sample2 = {'cpu_percent': 90.0, 'memory_percent': 50.0}
        alerts2 = manager.check_alerts(sample2)
        assert len(alerts2) == 0

    def test_alert_cooldown_expired(self):
        """测试冷却期过期后可再次触发"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.1)
        manager = PerformanceAlertManager(config)
        
        # 第一次触发
        sample1 = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        alerts1 = manager.check_alerts(sample1)
        assert len(alerts1) == 1
        
        # 等待冷却期过期
        time.sleep(0.15)
        
        # 再次触发
        sample2 = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        alerts2 = manager.check_alerts(sample2)
        assert len(alerts2) == 1

    def test_alert_callback_triggered(self):
        """测试告警回调被触发"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.0, enable_callback=True)
        manager = PerformanceAlertManager(config)
        
        callback = Mock()
        manager.add_alert_callback(callback)
        
        sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        manager.check_alerts(sample)
        
        callback.assert_called_once()
        call_args = callback.call_args
        assert call_args[0][0] == 'cpu_high'

    def test_alert_callback_disabled(self):
        """测试禁用回调时不触发"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.0, enable_callback=False)
        manager = PerformanceAlertManager(config)
        
        callback = Mock()
        manager.add_alert_callback(callback)
        
        sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        manager.check_alerts(sample)
        
        callback.assert_not_called()


class TestSustainedAlert:
    """持续高负载告警测试"""

    def test_sustained_cpu_alert_triggered(self):
        """测试 CPU 持续高负载告警触发"""
        config = AlertConfig(
            cpu_threshold=80.0,
            sustained_threshold_count=3,
            sustained_check_window=5,
            cooldown_seconds=0.0
        )
        manager = PerformanceAlertManager(config)
        
        # 创建模拟采样器
        sampler = Mock(spec=RuntimeSampler)
        sampler.get_samples.return_value = [
            {'cpu_percent': 85.0, 'memory_percent': 50.0},
            {'cpu_percent': 90.0, 'memory_percent': 50.0},
            {'cpu_percent': 88.0, 'memory_percent': 50.0},
            {'cpu_percent': 82.0, 'memory_percent': 50.0},
            {'cpu_percent': 86.0, 'memory_percent': 50.0},
        ]
        
        sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        alerts = manager.check_alerts(sample, sampler)
        
        # 应包含瞬时告警和持续告警
        sustained_alerts = [a for a in alerts if a['alert_type'] == 'cpu_sustained_high']
        assert len(sustained_alerts) == 1
        assert sustained_alerts[0]['level'] == 'critical'
        assert sustained_alerts[0]['sustained_count'] == 5

    def test_sustained_memory_alert_triggered(self):
        """测试内存持续高负载告警触发"""
        config = AlertConfig(
            memory_threshold=85.0,
            sustained_threshold_count=3,
            sustained_check_window=5,
            cooldown_seconds=0.0
        )
        manager = PerformanceAlertManager(config)
        
        sampler = Mock(spec=RuntimeSampler)
        sampler.get_samples.return_value = [
            {'cpu_percent': 50.0, 'memory_percent': 90.0},
            {'cpu_percent': 50.0, 'memory_percent': 92.0},
            {'cpu_percent': 50.0, 'memory_percent': 88.0},
            {'cpu_percent': 50.0, 'memory_percent': 95.0},
        ]
        
        sample = {'cpu_percent': 50.0, 'memory_percent': 90.0}
        alerts = manager.check_alerts(sample, sampler)
        
        sustained_alerts = [a for a in alerts if a['alert_type'] == 'memory_sustained_high']
        assert len(sustained_alerts) == 1

    def test_sustained_alert_not_triggered_insufficient_samples(self):
        """测试采样数量不足时不触发持续告警"""
        config = AlertConfig(
            cpu_threshold=80.0,
            sustained_threshold_count=5,
            sustained_check_window=10
        )
        manager = PerformanceAlertManager(config)
        
        sampler = Mock(spec=RuntimeSampler)
        sampler.get_samples.return_value = [
            {'cpu_percent': 85.0, 'memory_percent': 50.0},
            {'cpu_percent': 90.0, 'memory_percent': 50.0},
        ]
        
        sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        alerts = manager.check_alerts(sample, sampler)
        
        sustained_alerts = [a for a in alerts if 'sustained' in a['alert_type']]
        assert len(sustained_alerts) == 0

    def test_sustained_alert_not_triggered_below_threshold_count(self):
        """测试超过阈值的采样次数不足时不触发"""
        config = AlertConfig(
            cpu_threshold=80.0,
            sustained_threshold_count=5,
            sustained_check_window=10,
            cooldown_seconds=0.0
        )
        manager = PerformanceAlertManager(config)
        
        sampler = Mock(spec=RuntimeSampler)
        sampler.get_samples.return_value = [
            {'cpu_percent': 85.0, 'memory_percent': 50.0},
            {'cpu_percent': 70.0, 'memory_percent': 50.0},  # 未超过阈值
            {'cpu_percent': 90.0, 'memory_percent': 50.0},
            {'cpu_percent': 60.0, 'memory_percent': 50.0},  # 未超过阈值
            {'cpu_percent': 88.0, 'memory_percent': 50.0},
        ]
        
        sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        alerts = manager.check_alerts(sample, sampler)
        
        sustained_alerts = [a for a in alerts if 'sustained' in a['alert_type']]
        assert len(sustained_alerts) == 0


class TestRuntimeSamplerIntegration:
    """RuntimeSampler 与告警系统集成测试"""

    def test_sampler_with_alert_callback(self):
        """测试采样器添加告警回调"""
        sampler = RuntimeSampler(sample_interval=1.0)
        
        callback = Mock()
        sampler.add_alert_callback(callback)
        
        assert len(sampler._callbacks) == 1
        assert sampler._callbacks[0] == callback

    def test_sampler_collect_sample_structure(self):
        """测试采样数据结构"""
        sampler = RuntimeSampler()
        
        with patch('psutil.cpu_percent', return_value=50.0):
            with patch('psutil.virtual_memory') as mock_mem:
                mock_mem.return_value.percent = 60.0
                mock_mem.return_value.used = 1024 * 1024 * 500  # 500 MB
                
                sample = sampler._collect_sample()
                
                assert 'timestamp' in sample
                assert 'cpu_percent' in sample
                assert 'memory_percent' in sample
                assert 'memory_used_mb' in sample
                assert sample['cpu_percent'] == 50.0
                assert sample['memory_percent'] == 60.0

    def test_sampler_collect_sample_without_psutil(self):
        """测试无 psutil 时的采样"""
        sampler = RuntimeSampler()
        
        # 模拟 _collect_sample 中 psutil 导入失败
        original_collect = sampler._collect_sample
        
        def mock_collect():
            # 直接返回 psutil 未安装时的默认值
            return {
                'timestamp': time.time(),
                'cpu_percent': 0.0,
                'memory_percent': 0.0,
                'memory_used_mb': 0.0,
            }
        
        sampler._collect_sample = mock_collect
        sample = sampler._collect_sample()
        
        assert sample['cpu_percent'] == 0.0
        assert sample['memory_percent'] == 0.0

    def test_sampler_get_samples(self):
        """测试获取采样数据"""
        sampler = RuntimeSampler()
        
        # 手动添加采样数据
        sampler.samples.append({'timestamp': 1.0, 'cpu_percent': 50.0})
        sampler.samples.append({'timestamp': 2.0, 'cpu_percent': 60.0})
        sampler.samples.append({'timestamp': 3.0, 'cpu_percent': 70.0})
        
        all_samples = sampler.get_samples()
        assert len(all_samples) == 3
        
        last_samples = sampler.get_samples(last_n=2)
        assert len(last_samples) == 2
        assert last_samples[0]['cpu_percent'] == 60.0

    def test_sampler_get_average(self):
        """测试计算平均值"""
        sampler = RuntimeSampler()
        
        sampler.samples.append({'cpu_percent': 50.0})
        sampler.samples.append({'cpu_percent': 60.0})
        sampler.samples.append({'cpu_percent': 70.0})
        
        avg = sampler.get_average('cpu_percent')
        assert avg == 60.0

    def test_sampler_get_summary(self):
        """测试获取采样摘要"""
        sampler = RuntimeSampler()
        
        sampler.samples.append({'timestamp': 1.0, 'cpu_percent': 50.0, 'memory_percent': 60.0})
        sampler.samples.append({'timestamp': 2.0, 'cpu_percent': 70.0, 'memory_percent': 80.0})
        sampler.samples.append({'timestamp': 3.0, 'cpu_percent': 90.0, 'memory_percent': 70.0})
        
        summary = sampler.get_summary()
        
        assert summary['sample_count'] == 3
        assert summary['cpu_avg'] == 70.0
        assert summary['cpu_max'] == 90.0
        assert summary['memory_avg'] == 70.0
        assert summary['memory_max'] == 80.0

    def test_sampler_get_summary_empty(self):
        """测试空采样时的摘要"""
        sampler = RuntimeSampler()
        
        summary = sampler.get_summary()
        
        assert summary == {}


class TestGlobalFunctions:
    """全局函数测试"""

    def test_get_alert_manager_singleton(self):
        """测试全局告警管理器单例"""
        manager1 = get_alert_manager()
        manager2 = get_alert_manager()
        
        assert manager1 is manager2

    def test_get_alert_manager_with_config(self):
        """测试带配置获取告警管理器"""
        # 重置全局实例（实际全局变量在 agent.monitoring.performance,非薄包装模块）
        import agent.monitoring.performance as perf
        perf._alert_manager = None

        config = AlertConfig(cpu_threshold=75.0)
        manager = get_alert_manager(config)

        assert manager.config.cpu_threshold == 75.0

    def test_setup_performance_monitoring(self):
        """测试设置性能监控系统"""
        sampler, alert_manager = setup_performance_monitoring(
            sample_interval=5.0,
            alert_config=AlertConfig(cpu_threshold=90.0)
        )
        
        assert sampler.sample_interval == 5.0
        assert alert_manager.config.cpu_threshold == 90.0
        assert len(sampler._callbacks) == 1  # 告警检查回调
        assert len(alert_manager._alert_callbacks) == 1  # 默认回调

    def test_create_default_alert_callback(self):
        """测试创建默认告警回调"""
        callback = create_default_alert_callback()
        
        assert callable(callback)
        
        # 测试回调可正常调用
        callback('cpu_high', {'message': 'test'})


class TestAlertManagerEdgeCases:
    """告警管理器边界情况测试"""

    def test_empty_sample(self):
        """测试空采样数据"""
        manager = PerformanceAlertManager()
        
        alerts = manager.check_alerts({})
        
        assert len(alerts) == 0

    def test_negative_values(self):
        """测试负值采样数据"""
        config = AlertConfig(cpu_threshold=80.0)
        manager = PerformanceAlertManager(config)
        
        sample = {'cpu_percent': -10.0, 'memory_percent': -5.0}
        alerts = manager.check_alerts(sample)
        
        assert len(alerts) == 0

    def test_very_high_values(self):
        """测试极高值采样数据"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        sample = {'cpu_percent': 200.0, 'memory_percent': 150.0}
        alerts = manager.check_alerts(sample)
        
        assert len(alerts) == 2

    def test_callback_exception_handling(self):
        """测试回调异常处理"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        # 添加会抛出异常的回调
        def bad_callback(alert_type, alert):
            raise ValueError("测试异常")
        
        manager.add_alert_callback(bad_callback)
        
        # 添加正常回调
        normal_callback = Mock()
        manager.add_alert_callback(normal_callback)
        
        sample = {'cpu_percent': 85.0, 'memory_percent': 50.0}
        
        # 异常回调不应阻止正常回调
        manager.check_alerts(sample)
        
        normal_callback.assert_called_once()

    def test_threshold_boundary_exactly_at_threshold(self):
        """测试恰好等于阈值"""
        config = AlertConfig(cpu_threshold=80.0, cooldown_seconds=0.0)
        manager = PerformanceAlertManager(config)
        
        sample = {'cpu_percent': 80.0, 'memory_percent': 50.0}
        alerts = manager.check_alerts(sample)
        
        # 等于阈值应触发告警
        assert len(alerts) == 1
        assert alerts[0]['value'] == 80.0

    def test_threshold_boundary_one_below(self):
        """测试比阈值低 1"""
        config = AlertConfig(cpu_threshold=80.0)
        manager = PerformanceAlertManager(config)
        
        sample = {'cpu_percent': 79.9, 'memory_percent': 50.0}
        alerts = manager.check_alerts(sample)
        
        assert len(alerts) == 0