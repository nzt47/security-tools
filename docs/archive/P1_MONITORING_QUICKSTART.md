# 🚀 性能监控 - 快速开始指南

## ⚡ 立即开始（预计1-2小时）

### 第一步：创建监控模块结构

```bash
mkdir -p agent/monitoring
touch agent/monitoring/__init__.py
```

### 第二步：创建追踪模块

创建 `agent/monitoring/tracing.py`:

```python
#!/usr/bin/env python3
"""
分布式追踪模块
基于现有日志系统的增强实现
"""

import uuid
import logging
import time
from typing import Optional
from contextvars import ContextVar

logger = logging.getLogger(__name__)

_current_trace_id: ContextVar[Optional[str]] = ContextVar('trace_id', default=None)

class TraceContext:
    """追踪上下文管理器
    
    为每个操作生成唯一的 Trace ID，
    追踪完整的执行链路。
    """
    
    def __init__(self, service_name: str, operation: str):
        self.service_name = service_name
        self.operation = operation
        self.trace_id: Optional[str] = None
        self.start_time: Optional[float] = None
    
    def __enter__(self):
        # 生成或复用 Trace ID
        self.trace_id = _current_trace_id.get() or self._generate_trace_id()
        _current_trace_id.set(self.trace_id)
        self.start_time = time.time()
        
        logger.info(
            f"[{self.trace_id}] START {self.service_name}.{self.operation}",
            extra={
                'trace_id': self.trace_id,
                'service': self.service_name,
                'operation': self.operation,
                'event': 'start',
                'timestamp': self.start_time
            }
        )
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        
        if exc_type:
            logger.error(
                f"[{self.trace_id}] ERROR {self.service_name}.{self.operation} "
                f"(duration={duration*1000:.2f}ms, error={exc_val})",
                extra={
                    'trace_id': self.trace_id,
                    'service': self.service_name,
                    'operation': self.operation,
                    'event': 'error',
                    'duration_ms': duration * 1000,
                    'error': str(exc_val)
                }
            )
        else:
            logger.info(
                f"[{self.trace_id}] END {self.service_name}.{self.operation} "
                f"(duration={duration*1000:.2f}ms)",
                extra={
                    'trace_id': self.trace_id,
                    'service': self.service_name,
                    'operation': self.operation,
                    'event': 'end',
                    'duration_ms': duration * 1000
                }
            )
        
        return False
    
    def _generate_trace_id(self) -> str:
        """生成16位十六进制 Trace ID"""
        return uuid.uuid4().hex[:16]
    
    @property
    def duration_ms(self) -> float:
        """获取当前持续时间（毫秒）"""
        if self.start_time:
            return (time.time() - self.start_time) * 1000
        return 0

def get_trace_id() -> Optional[str]:
    """获取当前 Trace ID"""
    return _current_trace_id.get()

def trace(service: str, operation: str):
    """追踪装饰器
    
    用法:
        @trace("DigitalLife", "chat")
        def chat(self, user_input: str):
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            with TraceContext(service, operation):
                return func(*args, **kwargs)
        return wrapper
    return decorator
```

### 第三步：创建指标收集模块

创建 `agent/monitoring/metrics.py`:

```python
#!/usr/bin/env python3
"""
性能指标收集模块
"""

import time
import logging
import threading
from typing import Dict, List
from dataclasses import dataclass
from collections import defaultdict

logger = logging.getLogger(__name__)

@dataclass
class Metric:
    """指标数据"""
    name: str
    value: float
    timestamp: float
    labels: Dict[str, str]

class MetricsCollector:
    """轻量级指标收集器
    
    功能:
    - 记录延迟指标（Histogram）
    - 记录计数器（Counter）
    - 计算统计值（avg, min, max, p95, p99）
    """
    
    def __init__(self):
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._counters: Dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()
        
        logger.info("[Metrics] 指标收集器已初始化")
    
    def record_latency(self, metric_name: str, duration: float):
        """记录延迟指标
        
        Args:
            metric_name: 指标名称 (如: latency.digital_life.chat)
            duration: 持续时间（秒）
        """
        with self._lock:
            self._histograms[metric_name].append(duration)
        
        logger.debug(
            f"[Metrics] {metric_name}: {duration*1000:.2f}ms",
            extra={'metric': metric_name, 'value': duration, 'unit': 'ms'}
        )
    
    def increment_counter(self, counter_name: str, value: int = 1):
        """增加计数器
        
        Args:
            counter_name: 计数器名称
            value: 增加的值
        """
        with self._lock:
            self._counters[counter_name] += value
    
    def get_stats(self, metric_name: str) -> Dict:
        """获取指标统计
        
        Returns:
            {
                'count': 样本数,
                'sum': 总和,
                'avg': 平均值,
                'min': 最小值,
                'max': 最大值,
                'p50': 50分位数,
                'p95': 95分位数,
                'p99': 99分位数
            }
        """
        with self._lock:
            values = list(self._histograms.get(metric_name, []))
        
        if not values:
            return {'count': 0}
        
        sorted_values = sorted(values)
        n = len(sorted_values)
        
        return {
            'count': n,
            'sum': sum(values),
            'avg': sum(values) / n,
            'min': min(values),
            'max': max(values),
            'p50': sorted_values[int(n * 0.50)],
            'p95': sorted_values[min(int(n * 0.95), n-1)],
            'p99': sorted_values[min(int(n * 0.99), n-1)]
        }
    
    def get_all_metrics(self) -> Dict:
        """获取所有指标"""
        with self._lock:
            histograms = {
                name: self.get_stats(name)
                for name in self._histograms.keys()
            }
            counters = dict(self._counters)
        
        return {
            'histograms': histograms,
            'counters': counters,
            'generated_at': time.time()
        }
    
    def reset(self):
        """重置所有指标"""
        with self._lock:
            self._histograms.clear()
            self._counters.clear()
        logger.info("[Metrics] 指标已重置")

# 全局单例
_global_collector = MetricsCollector()

def get_metrics_collector() -> MetricsCollector:
    """获取全局指标收集器"""
    return _global_collector
```

### 第四步：创建监控装饰器

创建 `agent/monitoring/decorators.py`:

```python
#!/usr/bin/env python3
"""
性能监控装饰器
自动追踪函数执行时间和调用次数
"""

import time
import functools
from agent.monitoring.metrics import get_metrics_collector

def monitor_latency(metric_name: str):
    """延迟监控装饰器
    
    用法:
        @monitor_latency("latency.memory.search")
        def search(self, query):
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            collector = get_metrics_collector()
            start = time.time()
            
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                duration = time.time() - start
                collector.record_latency(metric_name, duration)
        
        return wrapper
    return decorator

def monitor_counter(counter_name: str):
    """计数器监控装饰器
    
    用法:
        @monitor_counter("count.error")
        def risky_operation(self):
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            collector = get_metrics_collector()
            
            try:
                result = func(*args, **kwargs)
                return result
            except Exception:
                collector.increment_counter(counter_name)
                raise
        
        return wrapper
    return decorator
```

### 第五步：更新 __init__.py

创建 `agent/monitoring/__init__.py`:

```python
"""
性能监控模块

包含:
- 分布式追踪 (TraceContext)
- 指标收集 (MetricsCollector)
- 监控装饰器 (monitor_latency, monitor_counter)
"""

from agent.monitoring.tracing import TraceContext, get_trace_id, trace
from agent.monitoring.metrics import MetricsCollector, get_metrics_collector
from agent.monitoring.decorators import monitor_latency, monitor_counter

__all__ = [
    'TraceContext',
    'get_trace_id', 
    'trace',
    'MetricsCollector',
    'get_metrics_collector',
    'monitor_latency',
    'monitor_counter'
]
```

---

## 🧪 测试验证

创建 `test_monitoring.py`:

```python
#!/usr/bin/env python3
"""
性能监控测试
"""

import time
import logging
from agent.monitoring import (
    TraceContext, get_trace_id, 
    get_metrics_collector, monitor_latency
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

def test_tracing():
    """测试追踪功能"""
    print("\n" + "="*60)
    print("Test 1: 追踪功能")
    print("="*60)
    
    with TraceContext("DigitalLife", "chat") as ctx:
        print(f"Trace ID: {ctx.trace_id}")
        
        with TraceContext("DigitalLife", "check_health"):
            time.sleep(0.1)
            print("健康检查完成")
        
        with TraceContext("DigitalLife", "call_llm"):
            time.sleep(0.2)
            print("LLM调用完成")
        
        with TraceContext("DigitalLife", "save_memory"):
            time.sleep(0.05)
            print("记忆保存完成")
    
    print(f"总耗时: {ctx.duration_ms:.2f}ms")

def test_metrics():
    """测试指标收集"""
    print("\n" + "="*60)
    print("Test 2: 指标收集")
    print("="*60)
    
    collector = get_metrics_collector()
    
    # 模拟操作
    @monitor_latency("latency.test.operation1")
    def operation1():
        time.sleep(0.1)
        return "完成"
    
    @monitor_latency("latency.test.operation2")
    def operation2():
        time.sleep(0.2)
        return "完成"
    
    # 执行多次
    for _ in range(5):
        operation1()
        operation2()
        collector.increment_counter("count.test")
    
    # 获取统计
    stats = collector.get_all_metrics()
    
    print("\n指标统计:")
    for metric, data in stats['histograms'].items():
        if data.get('count', 0) > 0:
            print(f"\n{metric}:")
            print(f"  平均: {data['avg']*1000:.2f}ms")
            print(f"  P95:  {data['p95']*1000:.2f}ms")
            print(f"  P99:  {data['p99']*1000:.2f}ms")
    
    print(f"\n计数器: {stats['counters']}")

def test_integration():
    """集成测试"""
    print("\n" + "="*60)
    print("Test 3: 完整集成")
    print("="*60)
    
    collector = get_metrics_collector()
    
    with TraceContext("System", "full_operation"):
        # 模拟向量记忆搜索
        with TraceContext("VectorMemory", "search"):
            time.sleep(0.15)
            collector.increment_counter("count.memory.search")
        
        # 模拟对话处理
        with TraceContext("DigitalLife", "process"):
            time.sleep(0.3)
            collector.increment_counter("count.chat")
    
    # 打印最终统计
    print("\n最终指标:")
    print(collector.get_all_metrics())

if __name__ == "__main__":
    test_tracing()
    test_metrics()
    test_integration()
    
    print("\n" + "="*60)
    print("✅ 所有测试完成!")
    print("="*60)
```

---

## 📊 运行测试

```bash
python test_monitoring.py
```

预期输出:

```
[INFO] [abc123def456] START DigitalLife.chat
[INFO] [abc123def456] START DigitalLife.check_health
[INFO] [abc123def456] END DigitalLife.check_health (duration=100.50ms)
[INFO] [abc123def456] START DigitalLife.call_llm
[INFO] [abc123def456] END DigitalLife.call_llm (duration=200.30ms)
[INFO] [abc123def456] START DigitalLife.save_memory
[INFO] [abc123def456] END DigitalLife.save_memory (duration=50.20ms)
[INFO] [abc123def456] END DigitalLife.chat (duration=351.00ms)

Test 2: 指标收集
latency.test.operation1:
  平均: 100.50ms
  P95:  105.20ms
  P99:  110.00ms

计数器: {'count.test': 10}
```

---

## 🎯 下一步：集成到 DigitalLife

### 修改 `agent/digital_life.py`

在文件开头添加:

```python
from agent.monitoring import TraceContext, get_metrics_collector, monitor_latency
```

在 `chat` 方法中使用:

```python
def chat(self, user_input: str) -> str:
    with TraceContext("DigitalLife", "chat"):
        logger.info(f"[{get_trace_id()}] 收到对话请求")
        
        # 检查身体状态
        with TraceContext("DigitalLife", "check_health"):
            readings = self.check_health()
        
        # 调用LLM
        with TraceContext("DigitalLife", "call_llm"):
            response = self._call_llm(user_input, body_status)
        
        # 保存记忆
        with TraceContext("DigitalLife", "save_memory"):
            self._save_to_memory(user_input, response)
        
        return response
```

---

## 🎉 完成清单

- [ ] 创建 agent/monitoring/ 目录结构
- [ ] 实现 TraceContext 追踪上下文
- [ ] 实现 MetricsCollector 指标收集
- [ ] 实现监控装饰器
- [ ] 运行测试验证功能
- [ ] 集成到 DigitalLife
- [ ] 查看带 Trace ID 的日志输出

---

## 📚 扩展阅读

完整文档请查看:
- `P1_MONITORING_PLAN.md` - 详细的监控规划
- `DETAILED_LOGGING.md` - 日志系统说明

---

**预计完成时间**: 1-2小时  
**下一步**: 将 Trace ID 和指标收集集成到实际的 DigitalLife 代码中
