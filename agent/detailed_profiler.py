"""详细性能日志埋点模块

功能：
- 模块加载时间记录
- 阶段耗时分析
- 性能瓶颈定位
- 详细调用链追踪

使用方法：
```python
from agent.detailed_profiler import profile, log_module_load

# 装饰器方式
@profile("模块名")
def my_function():
    pass

# 上下文管理器方式
with PerformanceContext("操作名") as ctx:
    # 操作
    pass

# 获取性能报告
from agent.detailed_profiler import get_performance_report
print(get_performance_report())
```
"""

import time
import threading
import logging
import traceback
from datetime import datetime
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from collections import defaultdict
from functools import wraps

logger = logging.getLogger(__name__)


@dataclass
class LoadEvent:
    """加载事件"""
    module_name: str
    event_type: str  # start, end, error
    timestamp: float
    elapsed_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    thread_id: Optional[int] = None


@dataclass
class StageTiming:
    """阶段计时"""
    stage_name: str
    start_time: float
    end_time: Optional[float] = None
    elapsed_ms: float = 0.0
    parent_stage: Optional[str] = None


class PerformanceTracker:
    """性能追踪器"""
    
    def __init__(self):
        self.events: List[LoadEvent] = []
        self.stage_timings: Dict[str, StageTiming] = {}
        self.module_load_times: Dict[str, List[float]] = defaultdict(list)
        self.current_stages: List[str] = []
        self._lock = threading.Lock()
        
        # 性能指标
        self.metrics = {
            'total_load_time_ms': 0.0,
            'slowest_module': None,
            'slowest_time_ms': 0.0,
            'fastest_module': None,
            'fastest_time_ms': float('inf'),
            'total_modules': 0,
            'failed_modules': 0
        }
    
    def record_load_start(self, module_name: str, details: Dict[str, Any] = None):
        """记录加载开始"""
        event = LoadEvent(
            module_name=module_name,
            event_type='start',
            timestamp=time.perf_counter(),
            details=details or {},
            thread_id=threading.get_ident()
        )
        
        with self._lock:
            self.events.append(event)
        
        logger.info(f"[Profiler] ▶ 开始加载模块: {module_name}")
    
    def record_load_end(self, module_name: str, elapsed_ms: float, details: Dict[str, Any] = None):
        """记录加载结束"""
        event = LoadEvent(
            module_name=module_name,
            event_type='end',
            timestamp=time.perf_counter(),
            elapsed_ms=elapsed_ms,
            details=details or {},
            thread_id=threading.get_ident()
        )
        
        with self._lock:
            self.events.append(event)
            self.module_load_times[module_name].append(elapsed_ms)
            
            # 更新指标
            self.metrics['total_load_time_ms'] += elapsed_ms
            self.metrics['total_modules'] += 1
            
            if elapsed_ms > self.metrics['slowest_time_ms']:
                self.metrics['slowest_module'] = module_name
                self.metrics['slowest_time_ms'] = elapsed_ms
            
            if elapsed_ms < self.metrics['fastest_time_ms']:
                self.metrics['fastest_module'] = module_name
                self.metrics['fastest_time_ms'] = elapsed_ms
        
        logger.info(
            f"[Profiler] ✅ 模块加载完成: {module_name}, "
            f"耗时: {elapsed_ms:.2f}ms"
        )
    
    def record_load_error(self, module_name: str, error: Exception):
        """记录加载错误"""
        event = LoadEvent(
            module_name=module_name,
            event_type='error',
            timestamp=time.perf_counter(),
            error=str(error),
            thread_id=threading.get_ident()
        )
        
        with self._lock:
            self.events.append(event)
            self.metrics['failed_modules'] += 1
        
        logger.error(
            f"[Profiler] ❌ 模块加载失败: {module_name}, "
            f"错误: {error}"
        )
    
    def start_stage(self, stage_name: str, parent_stage: Optional[str] = None):
        """开始阶段"""
        timing = StageTiming(
            stage_name=stage_name,
            start_time=time.perf_counter(),
            parent_stage=parent_stage
        )
        
        with self._lock:
            self.stage_timings[stage_name] = timing
            self.current_stages.append(stage_name)
        
        logger.debug(f"[Profiler] ▶ 开始阶段: {stage_name}")
    
    def end_stage(self, stage_name: str):
        """结束阶段"""
        with self._lock:
            if stage_name not in self.stage_timings:
                logger.warning(f"[Profiler] 阶段不存在: {stage_name}")
                return
            
            timing = self.stage_timings[stage_name]
            timing.end_time = time.perf_counter()
            timing.elapsed_ms = (timing.end_time - timing.start_time) * 1000
            
            if self.current_stages and self.current_stages[-1] == stage_name:
                self.current_stages.pop()
        
        logger.debug(
            f"[Profiler] ✅ 阶段完成: {stage_name}, "
            f"耗时: {timing.elapsed_ms:.2f}ms"
        )
    
    def get_report(self) -> Dict[str, Any]:
        """获取性能报告"""
        with self._lock:
            # 计算模块统计
            module_stats = {}
            for module, times in self.module_load_times.items():
                if times:
                    module_stats[module] = {
                        'count': len(times),
                        'total_ms': sum(times),
                        'avg_ms': sum(times) / len(times),
                        'min_ms': min(times),
                        'max_ms': max(times)
                    }
            
            # 计算阶段统计
            stage_stats = {}
            for name, timing in self.stage_timings.items():
                if timing.end_time:
                    stage_stats[name] = {
                        'elapsed_ms': timing.elapsed_ms,
                        'parent': timing.parent_stage
                    }
            
            return {
                'summary': self.metrics.copy(),
                'module_stats': module_stats,
                'stage_stats': stage_stats,
                'event_count': len(self.events),
                'failed_events': sum(1 for e in self.events if e.event_type == 'error')
            }
    
    def print_report(self):
        """打印性能报告"""
        report = self.get_report()
        
        print("\n" + "=" * 70)
        print("📊 性能分析报告")
        print("=" * 70)
        
        # 摘要
        summary = report['summary']
        print(f"\n总耗时: {summary['total_load_time_ms']:.2f}ms")
        print(f"加载模块数: {summary['total_modules']}")
        print(f"失败模块数: {summary['failed_modules']}")
        
        if summary['slowest_module']:
            print(f"\n最慢模块: {summary['slowest_module']} ({summary['slowest_time_ms']:.2f}ms)")
        if summary['fastest_module'] and summary['fastest_time_ms'] < float('inf'):
            print(f"最快模块: {summary['fastest_module']} ({summary['fastest_time_ms']:.2f}ms)")
        
        # 模块详情
        if report['module_stats']:
            print("\n📦 模块加载详情:")
            sorted_modules = sorted(
                report['module_stats'].items(),
                key=lambda x: x[1]['total_ms'],
                reverse=True
            )
            
            for module, stats in sorted_modules[:10]:  # 只显示前10
                print(
                    f"  {module:30s}: "
                    f"平均 {stats['avg_ms']:7.2f}ms, "
                    f"总计 {stats['total_ms']:8.2f}ms, "
                    f"次数 {stats['count']}"
                )
        
        # 阶段详情
        if report['stage_stats']:
            print("\n🔄 阶段耗时详情:")
            sorted_stages = sorted(
                report['stage_stats'].items(),
                key=lambda x: x[1]['elapsed_ms'],
                reverse=True
            )
            
            for stage, stats in sorted_stages[:10]:  # 只显示前10
                parent = f" (子阶段)" if stats['parent'] else ""
                print(f"  {stage:30s}: {stats['elapsed_ms']:8.2f}ms{parent}")
        
        print("\n" + "=" * 70)
    
    def reset(self):
        """重置追踪器"""
        with self._lock:
            self.events.clear()
            self.stage_timings.clear()
            self.module_load_times.clear()
            self.current_stages.clear()
            
            self.metrics = {
                'total_load_time_ms': 0.0,
                'slowest_module': None,
                'slowest_time_ms': 0.0,
                'fastest_module': None,
                'fastest_time_ms': float('inf'),
                'total_modules': 0,
                'failed_modules': 0
            }
        
        logger.info("[Profiler] 性能追踪器已重置")


# 全局追踪器实例
_tracker = PerformanceTracker()


def get_tracker() -> PerformanceTracker:
    """获取全局追踪器"""
    return _tracker


def get_performance_report() -> Dict[str, Any]:
    """获取性能报告（快捷函数）"""
    return _tracker.get_report()


def print_performance_report():
    """打印性能报告（快捷函数）"""
    _tracker.print_report()


class PerformanceContext:
    """性能追踪上下文管理器"""
    
    def __init__(self, name: str, details: Dict[str, Any] = None):
        self.name = name
        self.details = details or {}
        self.start_time = None
        self.elapsed_ms = 0.0
    
    def __enter__(self):
        self.start_time = time.perf_counter()
        _tracker.record_load_start(self.name, self.details)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.elapsed_ms = (time.perf_counter() - self.start_time) * 1000
        
        if exc_type:
            _tracker.record_load_error(self.name, exc_val)
        else:
            _tracker.record_load_end(self.name, self.elapsed_ms, self.details)
        
        return False


def profile(name: Optional[str] = None):
    """
    性能分析装饰器
    
    使用示例：
    ```python
    @profile("我的函数")
    def my_function():
        pass
    ```
    """
    def decorator(func: Callable) -> Callable:
        func_name = name or func.__name__
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            with PerformanceContext(func_name):
                return func(*args, **kwargs)
        
        return wrapper
    
    return decorator


def log_module_load(module_name: str):
    """
    模块加载日志装饰器
    
    使用示例：
    ```python
    @log_module_load("BodySensor")
    def load_body_sensor():
        return BodySensor()
    ```
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            _tracker.record_load_start(module_name)
            
            try:
                start = time.perf_counter()
                result = func(*args, **kwargs)
                elapsed = (time.perf_counter() - start) * 1000
                
                _tracker.record_load_end(module_name, elapsed)
                return result
                
            except Exception as e:
                _tracker.record_load_error(module_name, e)
                raise
        
        return wrapper
    
    return decorator


class StageTimer:
    """阶段计时器"""
    
    def __init__(self, stage_name: str, parent_stage: Optional[str] = None):
        self.stage_name = stage_name
        self.parent_stage = parent_stage
    
    def __enter__(self):
        _tracker.start_stage(self.stage_name, self.parent_stage)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        _tracker.end_stage(self.stage_name)
        return False
