"""传感器健康监控器单元测试"""

import pytest
import time
import threading
from agent.sensor_health_monitor import SensorHealthMonitor, SensorHealthMonitorSingleton


class TestSensorHealthMonitor:
    """传感器健康监控器测试类"""

    def test_initial_state(self):
        """测试初始状态"""
        monitor = SensorHealthMonitor(max_failures=3, reset_interval=60)
        
        assert monitor.get_status()['failure_count'] == 0
        assert monitor.get_status()['max_failures'] == 3
        assert monitor.get_status()['enabled'] == True
        assert monitor.is_healthy() == True
        
        monitor.shutdown()

    def test_single_failure(self):
        """测试单次失败"""
        monitor = SensorHealthMonitor(max_failures=3, reset_interval=60)
        
        monitor.record_failure()
        status = monitor.get_status()
        
        assert status['failure_count'] == 1
        assert monitor.is_healthy() == True
        
        monitor.shutdown()

    def test_consecutive_failures_trigger_restart(self):
        """测试连续失败触发重启"""
        restart_called = [False]
        
        def restart_callback():
            restart_called[0] = True
        
        monitor = SensorHealthMonitor(
            max_failures=3,
            reset_interval=60,
            restart_callback=restart_callback
        )
        
        # 前两次失败不触发重启
        monitor.record_failure()
        assert restart_called[0] == False
        monitor.record_failure()
        assert restart_called[0] == False
        
        # 第三次失败触发重启
        monitor.record_failure()
        assert restart_called[0] == True
        
        monitor.shutdown()

    def test_success_resets_count(self):
        """测试成功后重置计数"""
        monitor = SensorHealthMonitor(max_failures=3, reset_interval=60)
        
        # 两次失败
        monitor.record_failure()
        monitor.record_failure()
        assert monitor.get_status()['failure_count'] == 2
        
        # 一次成功重置计数
        monitor.record_success()
        assert monitor.get_status()['failure_count'] == 0
        
        monitor.shutdown()

    def test_partial_failures_then_success(self):
        """测试部分失败后成功"""
        restart_called = [False]
        
        def restart_callback():
            restart_called[0] = True
        
        monitor = SensorHealthMonitor(
            max_failures=3,
            reset_interval=60,
            restart_callback=restart_callback
        )
        
        # 两次失败
        monitor.record_failure()
        monitor.record_failure()
        
        # 成功重置
        monitor.record_success()
        assert monitor.get_status()['failure_count'] == 0
        assert restart_called[0] == False
        
        # 再三次失败才会触发重启
        monitor.record_failure()
        monitor.record_failure()
        monitor.record_failure()
        assert restart_called[0] == True
        
        monitor.shutdown()

    def test_timer_reset(self):
        """测试定时器自动重置（简化版，不实际等待）"""
        monitor = SensorHealthMonitor(max_failures=3, reset_interval=0.5)
        
        monitor.record_failure()
        assert monitor.get_status()['failure_count'] == 1
        
        # 等待定时器触发
        time.sleep(0.6)
        
        # 定时器应该已重置计数
        assert monitor.get_status()['failure_count'] == 0
        
        monitor.shutdown()

    def test_disable_monitor(self):
        """测试禁用监控器"""
        restart_called = [False]
        
        def restart_callback():
            restart_called[0] = True
        
        monitor = SensorHealthMonitor(
            max_failures=3,
            reset_interval=60,
            restart_callback=restart_callback
        )
        
        monitor.disable()
        
        # 失败不应该触发任何操作
        monitor.record_failure()
        monitor.record_failure()
        monitor.record_failure()
        
        assert restart_called[0] == False
        
        monitor.shutdown()

    def test_enable_monitor(self):
        """测试启用监控器"""
        restart_called = [False]
        
        def restart_callback():
            restart_called[0] = True
        
        monitor = SensorHealthMonitor(
            max_failures=3,
            reset_interval=60,
            restart_callback=restart_callback,
            enabled=False
        )
        
        monitor.enable()
        
        # 现在失败应该触发重启
        monitor.record_failure()
        monitor.record_failure()
        monitor.record_failure()
        
        assert restart_called[0] == True
        
        monitor.shutdown()

    def test_thread_safety(self):
        """测试线程安全"""
        monitor = SensorHealthMonitor(max_failures=100, reset_interval=60)
        
        def writer_thread():
            for _ in range(100):
                monitor.record_failure()
                monitor.record_success()
        
        threads = []
        for _ in range(10):
            t = threading.Thread(target=writer_thread)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # 最终计数应该是 0（每次失败后都有成功）
        assert monitor.get_status()['failure_count'] == 0
        
        monitor.shutdown()

    def test_set_restart_callback(self):
        """测试设置重启回调"""
        callback1_called = [False]
        callback2_called = [False]
        
        def callback1():
            callback1_called[0] = True
        
        def callback2():
            callback2_called[0] = True
        
        monitor = SensorHealthMonitor(max_failures=3, reset_interval=60)
        
        # 设置第一个回调
        monitor.set_restart_callback(callback1)
        monitor.record_failure()
        monitor.record_failure()
        monitor.record_failure()
        assert callback1_called[0] == True
        
        # 重置计数并设置第二个回调
        monitor.record_success()
        monitor.set_restart_callback(callback2)
        monitor.record_failure()
        monitor.record_failure()
        monitor.record_failure()
        assert callback2_called[0] == True
        
        monitor.shutdown()

    def test_failure_with_exception(self):
        """测试带异常信息的失败记录"""
        monitor = SensorHealthMonitor(max_failures=3, reset_interval=60)
        
        try:
            raise ValueError("Test exception")
        except ValueError as e:
            monitor.record_failure(e)
        
        assert monitor.get_status()['failure_count'] == 1
        
        monitor.shutdown()


class TestSensorHealthMonitorSingleton:
    """传感器健康监控器单例测试"""

    def test_singleton_instance(self):
        """测试单例实例"""
        # 重置单例
        SensorHealthMonitorSingleton.reset_instance()
        
        instance1 = SensorHealthMonitorSingleton.get_instance()
        instance2 = SensorHealthMonitorSingleton.get_instance()
        
        assert instance1 is instance2
        
        instance1.shutdown()

    def test_singleton_with_callback(self):
        """测试带回调的单例"""
        SensorHealthMonitorSingleton.reset_instance()
        
        callback_called = [False]
        
        def callback():
            callback_called[0] = True
        
        instance = SensorHealthMonitorSingleton.get_instance(
            max_failures=3,
            reset_interval=60,
            restart_callback=callback
        )
        
        instance.record_failure()
        instance.record_failure()
        instance.record_failure()
        
        assert callback_called[0] == True
        
        instance.shutdown()