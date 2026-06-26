#!/usr/bin/env python3
"""
故障注入工具 - 混沌工程模块

用于验证可观测性体系在异常场景下的有效性。

支持的故障类型：
1. 网络延迟/超时 - 模拟网络延迟和超时异常
2. 服务不可用 - 模拟下游服务故障
3. 内存压力 - 模拟资源耗尽
4. 高并发压力 - 模拟流量峰值

使用方法：
    from agent.monitoring.chaos_injector import ChaosInjector
    
    injector = ChaosInjector()
    
    # 注入网络延迟
    injector.inject_network_delay(delay_ms=5000)
    
    # 注入服务不可用
    injector.inject_service_unavailable(service_name="downstream-api")
    
    # 注入内存压力
    injector.inject_memory_pressure(target_mb=2048)
    
    # 清理所有故障
    injector.clear_all()
"""

import time
import random
import threading
import logging
import gc
import functools
from typing import Dict, Optional, Callable, Any, List
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _cpu_eater_process(duration_ms):
    """CPU消耗进程 - 定义在模块级别以便可以被pickle"""
    import time
    start = time.time()
    while True:
        if duration_ms and (time.time() - start) * 1000 >= duration_ms:
            break
        result = 0
        for i in range(1, 50000):
            result += i * i * i * i


class FaultType(Enum):
    """故障类型枚举"""
    NETWORK_DELAY = "network_delay"
    NETWORK_TIMEOUT = "network_timeout"
    SERVICE_UNAVAILABLE = "service_unavailable"
    MEMORY_PRESSURE = "memory_pressure"
    CONCURRENT_PRESSURE = "concurrent_pressure"
    CPU_PRESSURE = "cpu_pressure"
    DISK_IO_DELAY = "disk_io_delay"
    DISK_FULL = "disk_full"
    CONNECTION_POOL_EXHAUSTED = "connection_pool_exhausted"
    MESSAGE_LOSS = "message_loss"
    MESSAGE_OUT_OF_ORDER = "message_out_of_order"
    MESSAGE_DUPLICATE = "message_duplicate"


@dataclass
class FaultConfig:
    """故障配置"""
    fault_type: FaultType
    enabled: bool = False
    probability: float = 1.0  # 触发概率 0-1
    duration_ms: Optional[int] = None  # 持续时间(毫秒)，None表示持续直到手动清除
    target_service: Optional[str] = None  # 目标服务名
    delay_ms: Optional[int] = None  # 延迟毫秒数
    error_code: Optional[int] = None  # 错误码
    target_memory_mb: Optional[int] = None  # 目标内存占用(MB)
    io_operation: Optional[str] = None  # IO操作类型: read/write/both
    disk_usage_percent: Optional[int] = None  # 磁盘使用率百分比
    pool_size: Optional[int] = None  # 连接池大小
    message_loss_percent: Optional[int] = None  # 消息丢失百分比
    duplicate_count: Optional[int] = None  # 重复次数
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


@dataclass
class FaultInjectionRecord:
    """故障注入记录"""
    fault_type: FaultType
    config: FaultConfig
    injected_at: datetime
    triggered_count: int = 0
    affected_requests: int = 0
    recovered_at: Optional[datetime] = None


class ChaosInjector:
    """故障注入器核心类"""
    
    def __init__(self):
        self._fault_configs: Dict[FaultType, FaultConfig] = {}
        self._injection_records: List[FaultInjectionRecord] = []
        self._lock = threading.RLock()
        self._memory_pressure_thread: Optional[threading.Thread] = None
        self._memory_pressure_stop_event = threading.Event()
        self._memory_hold_list: List[bytearray] = []
        
        # 初始化默认配置
        for fault_type in FaultType:
            self._fault_configs[fault_type] = FaultConfig(fault_type=fault_type)
    
    def _check_probability(self, config: FaultConfig) -> bool:
        """检查是否应该触发故障（基于概率）"""
        if not config.enabled:
            return False
        if config.probability >= 1.0:
            return True
        return random.random() < config.probability
    
    def _check_duration(self, config: FaultConfig) -> bool:
        """检查故障是否在有效期内"""
        if not config.enabled:
            return False
        now = datetime.now()
        if config.start_time and now < config.start_time:
            return False
        if config.end_time and now > config.end_time:
            return False
        return True
    
    def inject_network_delay(self, delay_ms: int, probability: float = 1.0, 
                            duration_ms: Optional[int] = None, target_service: Optional[str] = None):
        """
        注入网络延迟故障
        
        Args:
            delay_ms: 延迟毫秒数
            probability: 触发概率 (0-1)
            duration_ms: 故障持续时间(毫秒)，None表示持续直到手动清除
            target_service: 目标服务名（可选）
        """
        with self._lock:
            config = self._fault_configs[FaultType.NETWORK_DELAY]
            config.enabled = True
            config.probability = probability
            config.delay_ms = delay_ms
            config.duration_ms = duration_ms
            config.target_service = target_service
            config.start_time = datetime.now()
            if duration_ms:
                config.end_time = config.start_time + \
                    timedelta(milliseconds=duration_ms)
            
            self._injection_records.append(FaultInjectionRecord(
                fault_type=FaultType.NETWORK_DELAY,
                config=config,
                injected_at=datetime.now()
            ))
            
            logger.info(f"[Chaos] 已注入网络延迟故障: delay={delay_ms}ms, probability={probability}, duration={duration_ms}ms")
    
    def inject_network_timeout(self, probability: float = 1.0, duration_ms: Optional[int] = None):
        """
        注入网络超时故障
        
        Args:
            probability: 触发概率 (0-1)
            duration_ms: 故障持续时间(毫秒)
        """
        with self._lock:
            config = self._fault_configs[FaultType.NETWORK_TIMEOUT]
            config.enabled = True
            config.probability = probability
            config.duration_ms = duration_ms
            config.start_time = datetime.now()
            if duration_ms:
                config.end_time = config.start_time + \
                    timedelta(milliseconds=duration_ms)
            
            self._injection_records.append(FaultInjectionRecord(
                fault_type=FaultType.NETWORK_TIMEOUT,
                config=config,
                injected_at=datetime.now()
            ))
            
            logger.info(f"[Chaos] 已注入网络超时故障: probability={probability}, duration={duration_ms}ms")
    
    def inject_service_unavailable(self, service_name: str, error_code: int = 503, 
                                  probability: float = 1.0, duration_ms: Optional[int] = None):
        """
        注入服务不可用故障
        
        Args:
            service_name: 服务名称
            error_code: 错误码，默认503
            probability: 触发概率 (0-1)
            duration_ms: 故障持续时间(毫秒)
        """
        with self._lock:
            config = self._fault_configs[FaultType.SERVICE_UNAVAILABLE]
            config.enabled = True
            config.probability = probability
            config.duration_ms = duration_ms
            config.target_service = service_name
            config.error_code = error_code
            config.start_time = datetime.now()
            if duration_ms:
                config.end_time = config.start_time + \
                    timedelta(milliseconds=duration_ms)
            
            self._injection_records.append(FaultInjectionRecord(
                fault_type=FaultType.SERVICE_UNAVAILABLE,
                config=config,
                injected_at=datetime.now()
            ))
            
            logger.info(f"[Chaos] 已注入服务不可用故障: service={service_name}, error_code={error_code}, probability={probability}")
    
    def inject_memory_pressure(self, target_mb: int, duration_ms: Optional[int] = None):
        """
        注入内存压力故障
        
        Args:
            target_mb: 目标内存占用(MB)
            duration_ms: 故障持续时间(毫秒)
        """
        with self._lock:
            # 停止之前的内存压力线程
            if self._memory_pressure_thread and self._memory_pressure_thread.is_alive():
                self._memory_pressure_stop_event.set()
                self._memory_pressure_thread.join(timeout=5)
            
            config = self._fault_configs[FaultType.MEMORY_PRESSURE]
            config.enabled = True
            config.probability = 1.0
            config.duration_ms = duration_ms
            config.target_memory_mb = target_mb
            config.start_time = datetime.now()
            if duration_ms:
                config.end_time = config.start_time + \
                    timedelta(milliseconds=duration_ms)
            
            # 立即分配内存（不使用线程，更快达到目标）
            self._memory_pressure_stop_event.clear()
            self._memory_hold_list = []
            
            target_bytes = target_mb * 1024 * 1024
            chunk_size = 50 * 1024 * 1024  # 每次分配50MB，更快达到目标
            
            while sum(len(chunk) for chunk in self._memory_hold_list) < target_bytes:
                try:
                    chunk = bytearray(chunk_size)
                    self._memory_hold_list.append(chunk)
                    current_mb = len(self._memory_hold_list) * 50
                    logger.debug(f"[Chaos] 内存压力: 已分配 {current_mb} MB")
                except MemoryError:
                    logger.warning("[Chaos] 内存分配失败，已达到系统限制")
                    break
            
            # 如果需要持续一段时间，启动维护线程
            if duration_ms:
                def memory_maintainer():
                    """内存维护线程，保持内存占用直到超时"""
                    start = time.time()
                    while not self._memory_pressure_stop_event.is_set():
                        if (time.time() - start) * 1000 >= duration_ms:
                            break
                        time.sleep(0.1)
                    
                    # 清理内存
                    self._memory_hold_list.clear()
                    gc.collect()
                    logger.info(f"[Chaos] 内存压力线程已停止")
                
                self._memory_pressure_thread = threading.Thread(target=memory_maintainer, daemon=True)
                self._memory_pressure_thread.start()
            
            self._injection_records.append(FaultInjectionRecord(
                fault_type=FaultType.MEMORY_PRESSURE,
                config=config,
                injected_at=datetime.now()
            ))
            
            logger.info(f"[Chaos] 已注入内存压力故障: target={target_mb}MB, duration={duration_ms}ms")
    
    def inject_cpu_pressure(self, duration_ms: Optional[int] = None):
        """
        注入CPU压力故障
        
        Args:
            duration_ms: 故障持续时间(毫秒)
        """
        with self._lock:
            config = self._fault_configs[FaultType.CPU_PRESSURE]
            config.enabled = True
            config.probability = 1.0
            config.duration_ms = duration_ms
            config.start_time = datetime.now()
            if duration_ms:
                config.end_time = config.start_time + \
                    timedelta(milliseconds=duration_ms)
            
            # 使用多进程消耗所有CPU核心（绕过GIL限制）
            import multiprocessing
            
            num_cores = multiprocessing.cpu_count()
            processes = []
            
            # 为每个核心启动一个进程（使用模块级函数以便pickle）
            for _ in range(num_cores):
                p = multiprocessing.Process(target=_cpu_eater_process, args=(duration_ms,))
                p.start()
                processes.append(p)
            
            # 如果有持续时间限制，启动监控线程等待结束后清理
            if duration_ms:
                def cleanup_monitor():
                    """监控并清理CPU压力进程"""
                    time.sleep(duration_ms / 1000.0 + 1)
                    for p in processes:
                        if p.is_alive():
                            p.terminate()
                    logger.info(f"[Chaos] 所有CPU压力进程已终止")
                
                threading.Thread(target=cleanup_monitor, daemon=True).start()
            
            self._injection_records.append(FaultInjectionRecord(
                fault_type=FaultType.CPU_PRESSURE,
                config=config,
                injected_at=datetime.now()
            ))
            
            logger.info(f"[Chaos] 已注入CPU压力故障: duration={duration_ms}ms, processes={num_cores}")
    
    def inject_disk_io_delay(self, delay_ms: int, io_operation: str = "both", 
                            probability: float = 1.0, duration_ms: Optional[int] = None):
        """
        注入磁盘IO延迟故障
        
        Args:
            delay_ms: IO延迟毫秒数
            io_operation: IO操作类型: read/write/both
            probability: 触发概率 (0-1)
            duration_ms: 故障持续时间(毫秒)
        """
        with self._lock:
            config = self._fault_configs[FaultType.DISK_IO_DELAY]
            config.enabled = True
            config.probability = probability
            config.delay_ms = delay_ms
            config.duration_ms = duration_ms
            config.io_operation = io_operation
            config.start_time = datetime.now()
            if duration_ms:
                config.end_time = config.start_time + \
                    timedelta(milliseconds=duration_ms)
            
            self._injection_records.append(FaultInjectionRecord(
                fault_type=FaultType.DISK_IO_DELAY,
                config=config,
                injected_at=datetime.now()
            ))
            
            logger.info(f"[Chaos] 已注入磁盘IO延迟故障: delay={delay_ms}ms, operation={io_operation}, probability={probability}")
    
    def inject_disk_full(self, disk_usage_percent: int = 95, duration_ms: Optional[int] = None):
        """
        注入磁盘满故障
        
        Args:
            disk_usage_percent: 模拟磁盘使用率百分比(0-100)
            duration_ms: 故障持续时间(毫秒)
        """
        with self._lock:
            config = self._fault_configs[FaultType.DISK_FULL]
            config.enabled = True
            config.probability = 1.0
            config.duration_ms = duration_ms
            config.disk_usage_percent = disk_usage_percent
            config.start_time = datetime.now()
            if duration_ms:
                config.end_time = config.start_time + \
                    timedelta(milliseconds=duration_ms)
            
            self._injection_records.append(FaultInjectionRecord(
                fault_type=FaultType.DISK_FULL,
                config=config,
                injected_at=datetime.now()
            ))
            
            logger.info(f"[Chaos] 已注入磁盘满故障: usage={disk_usage_percent}%")
    
    def inject_connection_pool_exhausted(self, pool_size: int = 0, 
                                        probability: float = 1.0, duration_ms: Optional[int] = None):
        """
        注入连接池耗尽故障
        
        Args:
            pool_size: 剩余连接数，0表示完全耗尽
            probability: 触发概率 (0-1)
            duration_ms: 故障持续时间(毫秒)
        """
        with self._lock:
            config = self._fault_configs[FaultType.CONNECTION_POOL_EXHAUSTED]
            config.enabled = True
            config.probability = probability
            config.duration_ms = duration_ms
            config.pool_size = pool_size
            config.start_time = datetime.now()
            if duration_ms:
                config.end_time = config.start_time + \
                    timedelta(milliseconds=duration_ms)
            
            self._injection_records.append(FaultInjectionRecord(
                fault_type=FaultType.CONNECTION_POOL_EXHAUSTED,
                config=config,
                injected_at=datetime.now()
            ))
            
            logger.info(f"[Chaos] 已注入连接池耗尽故障: remaining={pool_size}, probability={probability}")
    
    def inject_message_loss(self, loss_percent: int = 10, duration_ms: Optional[int] = None):
        """
        注入消息丢失故障
        
        Args:
            loss_percent: 消息丢失百分比(0-100)
            duration_ms: 故障持续时间(毫秒)
        """
        with self._lock:
            config = self._fault_configs[FaultType.MESSAGE_LOSS]
            config.enabled = True
            config.probability = loss_percent / 100.0
            config.duration_ms = duration_ms
            config.message_loss_percent = loss_percent
            config.start_time = datetime.now()
            if duration_ms:
                config.end_time = config.start_time + \
                    timedelta(milliseconds=duration_ms)
            
            self._injection_records.append(FaultInjectionRecord(
                fault_type=FaultType.MESSAGE_LOSS,
                config=config,
                injected_at=datetime.now()
            ))
            
            logger.info(f"[Chaos] 已注入消息丢失故障: loss={loss_percent}%")
    
    def inject_message_out_of_order(self, probability: float = 0.5, duration_ms: Optional[int] = None):
        """
        注入消息乱序故障
        
        Args:
            probability: 消息乱序概率 (0-1)
            duration_ms: 故障持续时间(毫秒)
        """
        with self._lock:
            config = self._fault_configs[FaultType.MESSAGE_OUT_OF_ORDER]
            config.enabled = True
            config.probability = probability
            config.duration_ms = duration_ms
            config.start_time = datetime.now()
            if duration_ms:
                config.end_time = config.start_time + \
                    timedelta(milliseconds=duration_ms)
            
            self._injection_records.append(FaultInjectionRecord(
                fault_type=FaultType.MESSAGE_OUT_OF_ORDER,
                config=config,
                injected_at=datetime.now()
            ))
            
            logger.info(f"[Chaos] 已注入消息乱序故障: probability={probability}")
    
    def inject_message_duplicate(self, duplicate_count: int = 2, 
                                probability: float = 0.5, duration_ms: Optional[int] = None):
        """
        注入消息重复故障
        
        Args:
            duplicate_count: 重复次数
            probability: 触发概率 (0-1)
            duration_ms: 故障持续时间(毫秒)
        """
        with self._lock:
            config = self._fault_configs[FaultType.MESSAGE_DUPLICATE]
            config.enabled = True
            config.probability = probability
            config.duration_ms = duration_ms
            config.duplicate_count = duplicate_count
            config.start_time = datetime.now()
            if duration_ms:
                config.end_time = config.start_time + \
                    timedelta(milliseconds=duration_ms)
            
            self._injection_records.append(FaultInjectionRecord(
                fault_type=FaultType.MESSAGE_DUPLICATE,
                config=config,
                injected_at=datetime.now()
            ))
            
            logger.info(f"[Chaos] 已注入消息重复故障: duplicates={duplicate_count}, probability={probability}")
    
    def trigger_if_active(self, fault_type: FaultType, target_service: Optional[str] = None) -> bool:
        """
        检查并触发故障
        
        Args:
            fault_type: 故障类型
            target_service: 目标服务（可选）
        
        Returns:
            是否触发故障
        """
        with self._lock:
            config = self._fault_configs.get(fault_type)
            if not config:
                return False
            
            # 检查是否启用
            if not config.enabled:
                return False
            
            # 检查持续时间
            if not self._check_duration(config):
                return False
            
            # 检查概率
            if not self._check_probability(config):
                return False
            
            # 检查目标服务 - 如果装饰器未指定 target_service，则匹配所有服务
            if config.target_service and target_service is not None and target_service != config.target_service:
                return False
            
            # 更新记录
            for record in reversed(self._injection_records):
                if record.fault_type == fault_type and record.recovered_at is None:
                    record.triggered_count += 1
                    record.affected_requests += 1
                    break
            
            return True
    
    def get_delay_ms(self, fault_type: FaultType) -> Optional[int]:
        """获取延迟毫秒数"""
        config = self._fault_configs.get(fault_type)
        if config and config.enabled and config.delay_ms:
            return config.delay_ms
        return None
    
    def clear_fault(self, fault_type: FaultType):
        """清除指定类型的故障"""
        with self._lock:
            config = self._fault_configs.get(fault_type)
            if config:
                config.enabled = False
                
                # 更新记录
                for record in reversed(self._injection_records):
                    if record.fault_type == fault_type and record.recovered_at is None:
                        record.recovered_at = datetime.now()
                        break
                
                # 特殊处理内存压力
                if fault_type == FaultType.MEMORY_PRESSURE:
                    self._memory_pressure_stop_event.set()
                    if self._memory_pressure_thread and self._memory_pressure_thread.is_alive():
                        self._memory_pressure_thread.join(timeout=5)
                    self._memory_hold_list.clear()
                    gc.collect()
            
            logger.info(f"[Chaos] 已清除故障: {fault_type.value}")
    
    def clear_all(self):
        """清除所有故障"""
        with self._lock:
            for fault_type in FaultType:
                self.clear_fault(fault_type)
            
            # 确保内存清理
            self._memory_pressure_stop_event.set()
            if self._memory_pressure_thread and self._memory_pressure_thread.is_alive():
                self._memory_pressure_thread.join(timeout=5)
            self._memory_hold_list.clear()
            gc.collect()
        
        logger.info("[Chaos] 已清除所有故障")
    
    def get_active_faults(self) -> List[FaultConfig]:
        """获取当前活跃的故障配置"""
        with self._lock:
            return [config for config in self._fault_configs.values() if config.enabled]
    
    def get_injection_history(self) -> List[FaultInjectionRecord]:
        """获取故障注入历史记录"""
        with self._lock:
            return list(self._injection_records)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取故障统计信息"""
        with self._lock:
            active_count = sum(1 for config in self._fault_configs.values() if config.enabled)
            total_injections = len(self._injection_records)
            total_triggered = sum(record.triggered_count for record in self._injection_records)
            total_affected = sum(record.affected_requests for record in self._injection_records)
            
            return {
                'active_faults': active_count,
                'total_injections': total_injections,
                'total_triggered': total_triggered,
                'total_affected_requests': total_affected,
                'fault_types': {ft.value: self._fault_configs[ft].enabled for ft in FaultType}
            }


# 全局单例
_global_chaos_injector = None
_global_chaos_lock = threading.Lock()


def get_chaos_injector() -> ChaosInjector:
    """获取全局故障注入器实例"""
    global _global_chaos_injector
    if _global_chaos_injector is None:
        with _global_chaos_lock:
            if _global_chaos_injector is None:
                _global_chaos_injector = ChaosInjector()
                logger.info("[Chaos] 全局故障注入器已初始化")
    return _global_chaos_injector


# 装饰器：在函数调用前检查并注入故障
def with_chaos_injection(fault_type: FaultType, target_service: Optional[str] = None):
    """
    故障注入装饰器
    
    用法：
        @with_chaos_injection(FaultType.NETWORK_DELAY)
        def make_request(url):
            # 实际请求逻辑
            pass
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            injector = get_chaos_injector()
            
            # 检查网络延迟
            if fault_type == FaultType.NETWORK_DELAY and injector.trigger_if_active(FaultType.NETWORK_DELAY, target_service):
                delay_ms = injector.get_delay_ms(FaultType.NETWORK_DELAY)
                if delay_ms:
                    logger.info(f"[Chaos] 触发网络延迟: {delay_ms}ms")
                    time.sleep(delay_ms / 1000.0)
            
            # 检查网络超时
            if injector.trigger_if_active(FaultType.NETWORK_TIMEOUT, target_service):
                logger.info("[Chaos] 触发网络超时")
                raise TimeoutError("Chaos injection: Network timeout")
            
            # 检查服务不可用
            if injector.trigger_if_active(FaultType.SERVICE_UNAVAILABLE, target_service):
                config = injector._fault_configs[FaultType.SERVICE_UNAVAILABLE]
                logger.info(f"[Chaos] 触发服务不可用: {config.target_service}")
                raise ConnectionError(f"Chaos injection: Service unavailable ({config.error_code})")
            
            return func(*args, **kwargs)
        return wrapper
    return decorator


# 上下文管理器：临时注入故障
class chaos_fault:
    """
    故障注入上下文管理器
    
    用法：
        with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=5000):
            # 在此上下文中网络操作会被延迟
            make_request()
    """
    
    def __init__(self, fault_type: FaultType, **kwargs):
        self.fault_type = fault_type
        self.kwargs = kwargs
        self.injector = get_chaos_injector()
    
    def __enter__(self):
        if self.fault_type == FaultType.NETWORK_DELAY:
            self.injector.inject_network_delay(**self.kwargs)
        elif self.fault_type == FaultType.NETWORK_TIMEOUT:
            self.injector.inject_network_timeout(**self.kwargs)
        elif self.fault_type == FaultType.SERVICE_UNAVAILABLE:
            self.injector.inject_service_unavailable(**self.kwargs)
        elif self.fault_type == FaultType.MEMORY_PRESSURE:
            self.injector.inject_memory_pressure(**self.kwargs)
        elif self.fault_type == FaultType.CPU_PRESSURE:
            self.injector.inject_cpu_pressure(**self.kwargs)
        elif self.fault_type == FaultType.DISK_IO_DELAY:
            self.injector.inject_disk_io_delay(**self.kwargs)
        elif self.fault_type == FaultType.DISK_FULL:
            self.injector.inject_disk_full(**self.kwargs)
        elif self.fault_type == FaultType.CONNECTION_POOL_EXHAUSTED:
            self.injector.inject_connection_pool_exhausted(**self.kwargs)
        elif self.fault_type == FaultType.MESSAGE_LOSS:
            self.injector.inject_message_loss(**self.kwargs)
        elif self.fault_type == FaultType.MESSAGE_OUT_OF_ORDER:
            self.injector.inject_message_out_of_order(**self.kwargs)
        elif self.fault_type == FaultType.MESSAGE_DUPLICATE:
            self.injector.inject_message_duplicate(**self.kwargs)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.injector.clear_fault(self.fault_type)
        return False
