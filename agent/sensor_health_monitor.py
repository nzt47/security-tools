"""传感器健康监控器

实现传感器读取失败自动重启机制：
- 跟踪传感器读取失败次数
- 连续失败超过阈值时触发自动重启
- 提供可配置的失败阈值和回调机制
"""

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class SensorHealthMonitor:
    """传感器健康监控器
    
    监控传感器读取健康状态，当连续失败超过指定次数时触发自动重启。
    
    Args:
        max_failures: 最大连续失败次数，超过此值触发重启，默认为 3
        reset_interval: 失败计数重置间隔（秒），默认为 60 秒
        restart_callback: 重启回调函数
        enabled: 是否启用监控，默认为 True
    """

    def __init__(
        self,
        max_failures: int = 3,
        reset_interval: int = 60,
        restart_callback: Optional[Callable[[], None]] = None,
        enabled: bool = True
    ):
        self.max_failures = max_failures
        self.reset_interval = reset_interval
        self.restart_callback = restart_callback
        self.enabled = enabled
        
        # 失败计数
        self._failure_count = 0
        self._last_failure_time = 0.0
        
        # 线程锁
        self._lock = threading.Lock()
        
        # 重置计时器
        self._reset_timer = None
        self._schedule_reset()
        
        logger.info(f"[SensorHealth] 初始化完成 - 最大失败次数: {max_failures}, 重置间隔: {reset_interval}s")

    def _schedule_reset(self):
        """定时重置失败计数"""
        if self._reset_timer:
            self._reset_timer.cancel()
        
        self._reset_timer = threading.Timer(
            self.reset_interval,
            self._reset_failure_count
        )
        self._reset_timer.daemon = True
        self._reset_timer.start()

    def _reset_failure_count(self):
        """重置失败计数（定时器回调）"""
        with self._lock:
            if self._failure_count > 0:
                logger.info(f"[SensorHealth] 超时重置失败计数: {self._failure_count} -> 0")
                self._failure_count = 0
        self._schedule_reset()

    def record_failure(self, error: Optional[Exception] = None):
        """记录一次传感器读取失败
        
        Args:
            error: 失败的异常信息（可选）
        """
        if not self.enabled:
            return
            
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            error_msg = f" ({error})" if error else ""
            logger.info(
                f"[SensorHealth] 传感器读取失败 #{self._failure_count}/{self.max_failures}{error_msg}"
            )
            
            # 检查是否需要触发重启
            if self._failure_count >= self.max_failures:
                logger.info(
                    f"[SensorHealth] ⚠️ 传感器连续失败 {self.max_failures} 次，触发自动重启"
                )
                
                # 触发重启回调
                if self.restart_callback:
                    try:
                        self.restart_callback()
                    except Exception as e:
                        logger.error(f"[SensorHealth] 重启回调执行失败: {e}")
                
                # 重置计数
                self._failure_count = 0

    def record_success(self):
        """记录一次传感器读取成功"""
        if not self.enabled:
            return
            
        with self._lock:
            if self._failure_count > 0:
                logger.info(f"[SensorHealth] 读取成功，重置失败计数: {self._failure_count} -> 0")
                self._failure_count = 0

    def get_status(self) -> dict:
        """获取健康状态"""
        with self._lock:
            return {
                'failure_count': self._failure_count,
                'max_failures': self.max_failures,
                'enabled': self.enabled,
                'last_failure_time': self._last_failure_time
            }

    def is_healthy(self) -> bool:
        """检查传感器是否健康"""
        with self._lock:
            return self._failure_count < self.max_failures

    def set_restart_callback(self, callback: Callable[[], None]):
        """设置重启回调函数"""
        self.restart_callback = callback

    def enable(self):
        """启用监控"""
        self.enabled = True
        logger.info("[SensorHealth] 监控已启用")

    def disable(self):
        """禁用监控"""
        self.enabled = False
        logger.info("[SensorHealth] 监控已禁用")

    def shutdown(self):
        """关闭监控器"""
        if self._reset_timer:
            self._reset_timer.cancel()
        logger.info("[SensorHealth] 监控器已关闭")


class SensorHealthMonitorSingleton:
    """传感器健康监控器单例"""
    
    _instance = None
    _lock = threading.Lock()
    
    @classmethod
    def get_instance(
        cls,
        max_failures: int = 3,
        reset_interval: int = 60,
        restart_callback: Optional[Callable[[], None]] = None
    ) -> SensorHealthMonitor:
        """获取单例实例"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = SensorHealthMonitor(
                    max_failures=max_failures,
                    reset_interval=reset_interval,
                    restart_callback=restart_callback
                )
            return cls._instance
    
    @classmethod
    def reset_instance(cls):
        """重置单例实例"""
        with cls._lock:
            if cls._instance:
                cls._instance.shutdown()
                cls._instance = None


# 便捷函数
def get_sensor_health_monitor() -> SensorHealthMonitor:
    """获取传感器健康监控器实例"""
    return SensorHealthMonitorSingleton.get_instance()


def monitor_sensor_reading(func):
    """传感器读取监控装饰器
    
    装饰传感器读取方法，自动记录成功/失败状态。
    
    Example:
        @monitor_sensor_reading
        def collect_quick(self):
            # 传感器读取逻辑
            pass
    """
    def wrapper(*args, **kwargs):
        monitor = get_sensor_health_monitor()
        try:
            result = func(*args, **kwargs)
            monitor.record_success()
            return result
        except Exception as e:
            monitor.record_failure(e)
            raise
    return wrapper
