# 🚀 P1阶段：分布式追踪与性能监控规划

基于现有的详细日志系统，构建完整的**可观测性（Observability）**体系。

---

## 📊 1. 当前状态分析

### ✅ 已有的基础设施
- 详细的日志系统（INFO/WARNING/ERROR/DEBUG）
- 关键操作追踪（初始化、检索、保存、搜索）
- 错误堆栈记录
- 结构化日志格式

### 🎯 可提升空间
- ❌ 缺乏请求链路追踪（Trace ID）
- ❌ 缺乏性能指标收集（Latency、Throughput）
- ❌ 缺乏可视化仪表板
- ❌ 缺乏告警机制
- ❌ 缺乏跨服务追踪（分布式场景）

---

## 🏗️ 2. 整体架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                    可观测性平台架构                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │   日志      │    │   追踪      │    │   指标      │     │
│  │  (Logs)    │    │  (Traces)  │    │ (Metrics)  │     │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘     │
│         │                   │                   │            │
│         └───────────────────┼───────────────────┘            │
│                             │                                │
│                    ┌────────▼────────┐                      │
│                    │  可观测性后端   │                      │
│                    │ (OpenTelemetry)│                      │
│                    └────────┬────────┘                      │
│                             │                                │
│         ┌───────────────────┼───────────────────┐            │
│         │                   │                   │            │
│  ┌──────▼──────┐    ┌──────▼──────┐    ┌──────▼──────┐     │
│  │ Grafana     │    │ Prometheus  │    │ Jaeger      │     │
│  │ (可视化)    │    │ (指标存储)  │    │ (链路追踪)  │     │
│  └─────────────┘    └─────────────┘    └─────────────┘     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎯 3. 核心功能规划

### 3.1 分布式追踪系统

#### 目标
- 为每个请求生成唯一的 **Trace ID**
- 追踪请求在系统中的完整生命周期
- 识别性能瓶颈和错误根源

#### Trace ID 生成方案

```python
# agent/monitoring/tracing.py

import uuid
import logging
from functools import wraps
from typing import Optional
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# 使用 ContextVar 实现线程安全的上下文管理
_current_trace_id: ContextVar[Optional[str]] = ContextVar('trace_id', default=None)

class TraceContext:
    """追踪上下文管理器"""
    
    def __init__(self, service_name: str, operation: str):
        self.service_name = service_name
        self.operation = operation
        self.trace_id = None
        self.start_time = None
        
    def __enter__(self):
        # 生成或复用 Trace ID
        self.trace_id = _current_trace_id.get() or self._generate_trace_id()
        _current_trace_id.set(self.trace_id)
        
        # 记录开始时间
        self.start_time = self._now()
        
        # 打印入口日志
        logger.info(
            f"[{self.trace_id}] ➤ {self.service_name}.{self.operation} START",
            extra={
                'trace_id': self.trace_id,
                'service': self.service_name,
                'operation': operation,
                'event': 'start'
            }
        )
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = self._now() - self.start_time
        
        if exc_type:
            # 记录错误
            logger.error(
                f"[{self.trace_id}] ✗ {self.service_name}.{self.operation} ERROR "
                f"(duration={duration:.3f}s, error={exc_val})",
                extra={
                    'trace_id': self.trace_id,
                    'service': self.service_name,
                    'operation': operation,
                    'event': 'error',
                    'duration': duration,
                    'error': str(exc_val)
                }
            )
        else:
            # 记录成功
            logger.info(
                f"[{self.trace_id}] ✓ {self.service_name}.{self.operation} END "
                f"(duration={duration:.3f}s)",
                extra={
                    'trace_id': self.trace_id,
                    'service': self.service_name,
                    'operation': operation,
                    'event': 'end',
                    'duration': duration
                }
            )
        
        return False
    
    def _generate_trace_id(self) -> str:
        """生成Trace ID"""
        return f"{uuid.uuid4().hex[:16]}"
    
    def _now(self) -> float:
        """获取当前时间戳"""
        import time
        return time.time()

def trace(service: str, operation: str):
    """追踪装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with TraceContext(service, operation):
                return func(*args, **kwargs)
        return wrapper
    return decorator
```

#### 使用示例

```python
# 在 DigitalLife 中的使用
class DigitalLife:
    
    def chat(self, user_input: str) -> str:
        with TraceContext("DigitalLife", "chat"):
            logger.info(f"[{get_trace_id()}] Processing user input")
            
            with TraceContext("DigitalLife", "body_check"):
                readings = self.check_health()
            
            with TraceContext("DigitalLife", "llm_call"):
                response = self._call_llm(user_input, body_status)
            
            with TraceContext("DigitalLife", "memory_save"):
                self._save_to_memory(user_input, response)
            
            return response
    
    def _vector_memory_search(self, query: str):
        with TraceContext("VectorMemory", "search"):
            logger.info(f"[{get_trace_id()}] Searching: {query}")
            results = self._vector_memory.search(query)
            logger.info(f"[{get_trace_id()}] Found {len(results)} results")
            return results
```

---

### 3.2 性能指标收集

#### 核心指标

| 指标类型 | 指标名称 | 说明 | 单位 |
|---------|---------|------|------|
| **延迟** | `latency.chat` | 对话响应时间 | ms |
| **延迟** | `latency.memory.search` | 记忆搜索时间 | ms |
| **延迟** | `latency.memory.save` | 记忆保存时间 | ms |
| **吞吐量** | `throughput.chat` | 对话处理速率 | req/s |
| **计数** | `count.memory.total` | 记忆总数 | count |
| **计数** | `count.error` | 错误总数 | count |
| **速率** | `rate.memory.growth` | 记忆增长速度 | items/s |

#### 指标收集实现

```python
# agent/monitoring/metrics.py

import time
import logging
from typing import Dict, List
from dataclasses import dataclass, field
from collections import defaultdict
import threading

logger = logging.getLogger(__name__)

@dataclass
class Metric:
    """指标数据"""
    name: str
    value: float
    timestamp: float
    labels: Dict[str, str] = field(default_factory=dict)

class MetricsCollector:
    """指标收集器"""
    
    def __init__(self):
        self._metrics: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = defaultdict(int)
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        
        logger.info("[Metrics] 指标收集器已初始化")
    
    def record_latency(self, metric_name: str, duration: float, labels: Dict = None):
        """记录延迟指标"""
        with self._lock:
            self._histograms[metric_name].append(duration)
            self._metrics[f"{metric_name}_count"].append(1)
            
        logger.debug(
            f"[Metrics] {metric_name}: {duration*1000:.2f}ms",
            extra={'metric': metric_name, 'value': duration}
        )
    
    def increment_counter(self, counter_name: str, value: int = 1):
        """增加计数器"""
        with self._lock:
            self._counters[counter_name] += value
    
    def get_stats(self, metric_name: str) -> Dict[str, float]:
        """获取指标统计"""
        if metric_name not in self._histograms:
            return {}
        
        values = self._histograms[metric_name]
        if not values:
            return {}
        
        return {
            'count': len(values),
            'sum': sum(values),
            'avg': sum(values) / len(values),
            'min': min(values),
            'max': max(values),
            'p50': self._percentile(values, 0.5),
            'p95': self._percentile(values, 0.95),
            'p99': self._percentile(values, 0.99)
        }
    
    def _percentile(self, data: List[float], p: float) -> float:
        """计算百分位数"""
        sorted_data = sorted(data)
        index = int(len(sorted_data) * p)
        return sorted_data[min(index, len(sorted_data) - 1)]
    
    def get_all_metrics(self) -> Dict:
        """获取所有指标"""
        return {
            'histograms': {
                name: self.get_stats(name) 
                for name in self._histograms.keys()
            },
            'counters': dict(self._counters)
        }
    
    def reset(self):
        """重置指标"""
        with self._lock:
            self._metrics.clear()
            self._counters.clear()
            self._histograms.clear()
        logger.info("[Metrics] 指标已重置")

# 全局指标收集器实例
_global_collector = MetricsCollector()

def get_metrics_collector() -> MetricsCollector:
    return _global_collector
```

#### 自动性能追踪装饰器

```python
# agent/monitoring/auto_metrics.py

import time
import functools
from agent.monitoring.metrics import get_metrics_collector

def monitor_latency(metric_name: str):
    """性能监控装饰器"""
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

# 使用示例
class VectorStore:
    
    @monitor_latency("latency.memory.search")
    def search(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        # 原有逻辑...
        pass
    
    @monitor_latency("latency.memory.save")
    def add(self, content: str, metadata: Dict = None) -> str:
        # 原有逻辑...
        pass
```

---

### 3.3 追踪上下文集成

#### 完整集成示例

```python
# agent/digital_life.py 更新后的代码

from agent.monitoring.tracing import TraceContext, get_trace_id
from agent.monitoring.metrics import get_metrics_collector, monitor_latency
from functools import wraps

class DigitalLife:
    
    def __init__(self, config: dict = None):
        # ... 原有初始化 ...
        
        # 添加追踪和指标
        self._collector = get_metrics_collector()
        logger.info("[Metrics] 性能监控已启用")
    
    @monitor_latency("latency.digital_life.chat")
    def chat(self, user_input: str) -> str:
        """与云枢对话 - 完整追踪版本"""
        
        with TraceContext("DigitalLife", "chat") as ctx:
            logger.info(f"[{ctx.trace_id}] 💬 收到对话请求")
            logger.info(f"[{ctx.trace_id}]    用户输入: {user_input[:100]}")
            
            try:
                # 1. 检查身体状态
                with TraceContext("DigitalLife", "check_health"):
                    readings = self.check_health()
                    self._collector.increment_counter("count.health_check")
                
                # 2. 调用LLM
                with TraceContext("DigitalLife", "call_llm"):
                    body_status = self._build_body_status(readings)
                    response = self._call_llm(user_input, body_status)
                    self._collector.increment_counter("count.llm_call")
                
                # 3. 保存向量记忆
                with TraceContext("DigitalLife", "save_memory"):
                    if self._vector_memory:
                        self._save_to_memory(user_input, response)
                        self._collector.increment_counter("count.memory.save")
                
                # 4. 更新对话计数
                self._interaction_count += 1
                self._collector.increment_counter("count.chat.total")
                
                logger.info(f"[{ctx.trace_id}] ✅ 对话处理完成")
                return response
                
            except Exception as e:
                self._collector.increment_counter("count.error")
                logger.error(f"[{ctx.trace_id}] ❌ 对话处理失败: {e}")
                raise
    
    @monitor_latency("latency.vector_memory.search")
    def search_memory(self, query: str, top_k: int = 5) -> list:
        """搜索记忆 - 带追踪版本"""
        
        with TraceContext("VectorMemory", "search") as ctx:
            logger.info(f"[{ctx.trace_id}] 🔍 搜索记忆: '{query}'")
            
            if not self._vector_memory:
                logger.warning(f"[{ctx.trace_id}] 向量记忆不可用")
                return []
            
            try:
                results = self._vector_memory.search(query, top_k)
                logger.info(f"[{ctx.trace_id}] ✅ 找到 {len(results)} 条结果")
                return results
                
            except Exception as e:
                logger.error(f"[{ctx.trace_id}] ❌ 搜索失败: {e}")
                raise
```

---

### 3.4 健康检查与告警

#### 健康检查端点

```python
# agent/monitoring/health.py

from typing import Dict
import psutil
import time

class HealthChecker:
    """健康检查器"""
    
    def __init__(self):
        self.start_time = time.time()
    
    def check(self) -> Dict:
        """执行健康检查"""
        checks = {
            'status': 'healthy',
            'uptime': time.time() - self.start_time,
            'memory': self._check_memory(),
            'system': self._check_system(),
            'services': self._check_services()
        }
        
        # 如果有任何检查失败，标记为不健康
        if any(c.get('status') != 'healthy' for c in checks.values() if isinstance(c, dict)):
            checks['status'] = 'unhealthy'
        
        return checks
    
    def _check_memory(self) -> Dict:
        """检查向量记忆"""
        try:
            from agent.memory import VectorStore
            store = VectorStore()
            return {
                'status': 'healthy',
                'total_memories': len(store.items)
            }
        except Exception as e:
            return {
                'status': 'unhealthy',
                'error': str(e)
            }
    
    def _check_system(self) -> Dict:
        """检查系统资源"""
        return {
            'status': 'healthy',
            'cpu_percent': psutil.cpu_percent(),
            'memory_percent': psutil.virtual_memory().percent
        }
    
    def _check_services(self) -> Dict:
        """检查服务状态"""
        from agent.digital_life import DigitalLife
        return {
            'status': 'healthy' if DigitalLife else 'unhealthy'
        }

# 健康检查API
@app.route('/health')
async def health_check():
    checker = HealthChecker()
    return checker.check()
```

#### 告警规则

```python
# agent/monitoring/alerts.py

class AlertManager:
    """告警管理器"""
    
    def __init__(self):
        self.alerts = []
        self.thresholds = {
            'latency.p95': 1000,      # 95%延迟 > 1s
            'error_rate': 0.05,       # 错误率 > 5%
            'memory.count': 10000,    # 记忆数 > 10000
            'cpu_percent': 90,        # CPU > 90%
        }
    
    def check_and_alert(self, metrics: Dict):
        """检查指标并触发告警"""
        
        # 检查延迟
        latency_p95 = metrics.get('histograms', {}).get('latency.digital_life.chat', {}).get('p95', 0)
        if latency_p95 > self.thresholds['latency.p95']:
            self._send_alert('HIGH_LATENCY', f"95%延迟: {latency_p95*1000:.0f}ms")
        
        # 检查错误率
        error_count = metrics.get('counters', {}).get('count.error', 0)
        total_count = metrics.get('counters', {}).get('count.chat.total', 1)
        error_rate = error_count / max(total_count, 1)
        
        if error_rate > self.thresholds['error_rate']:
            self._send_alert('HIGH_ERROR_RATE', f"错误率: {error_rate*100:.1f}%")
        
        # 检查记忆数量
        memory_count = metrics.get('histograms', {}).get('count.memory.total', 0)
        if memory_count > self.thresholds['memory.count']:
            self._send_alert('MEMORY_OVERFLOW', f"记忆数: {memory_count}")
    
    def _send_alert(self, alert_type: str, message: str):
        """发送告警"""
        alert = {
            'type': alert_type,
            'message': message,
            'timestamp': time.time()
        }
        self.alerts.append(alert)
        logger.warning(f"🚨 ALERT [{alert_type}]: {message}")
```

---

## 📅 4. 实施路线图

### 第一阶段：基础追踪（1-2天）

#### Day 1: 追踪框架搭建
- [ ] 创建 `agent/monitoring/` 目录
- [ ] 实现 TraceContext 上下文管理器
- [ ] 实现 MetricsCollector 指标收集器
- [ ] 集成到 DigitalLife 主流程

#### Day 2: 关键路径追踪
- [ ] 为向量记忆操作添加追踪
- [ ] 为规划引擎添加追踪
- [ ] 添加自动性能监控装饰器
- [ ] 编写测试验证

### 第二阶段：可视化（2-3天）

#### Day 3: 数据导出
- [ ] 实现 Prometheus 格式导出
- [ ] 实现结构化日志导出（JSON格式）
- [ ] 添加 /metrics HTTP 端点

#### Day 4-5: 可视化配置
- [ ] 配置 Grafana 仪表板
- [ ] 配置 Jaeger 追踪查看器
- [ ] 创建预设告警规则

### 第三阶段：高级特性（3-4天）

#### Day 6-7: 分布式追踪
- [ ] 实现跨服务 Trace ID 传播
- [ ] 添加数据库连接追踪
- [ ] 添加外部API调用追踪

#### Day 8-9: 智能告警
- [ ] 实现动态阈值调整
- [ ] 添加告警聚合和去重
- [ ] 配置告警通知渠道（Webhook/Email）

---

## 🛠️ 5. 技术选型建议

### 轻量级方案（推荐小型项目）

| 组件 | 选择 | 理由 |
|------|------|------|
| 日志 | 现有结构化日志 | 已有详细日志，只需增强格式 |
| 追踪 | 自实现 TraceContext | 简单够用，无需额外依赖 |
| 指标 | 自实现 MetricsCollector | 轻量级，适合单机场景 |
| 可视化 | Grafana + Prometheus | 成熟方案，资源占用低 |

### 企业级方案（推荐大型项目）

| 组件 | 选择 | 理由 |
|------|------|------|
| 日志 | ELK Stack (Elasticsearch + Logstash + Kibana) | 强大的日志分析能力 |
| 追踪 | OpenTelemetry + Jaeger | 标准化，支持多语言 |
| 指标 | Prometheus + Grafana | 监控事实标准 |
| APM | SkyWalking / Pinpoint | 全链路追踪 |

### 推荐起步方案

```
✅ 当前: 已有详细日志
⬇️
✅ P1-1: 添加 Trace ID + MetricsCollector（1天）
⬇️
✅ P1-2: 添加 Prometheus 导出 + Grafana 可视化（2天）
⬇️
✅ P1-3: 添加告警系统（2天）
```

---

## 📊 6. 性能基准

### 预期改进

| 指标 | 当前 | 目标 | 改进 |
|------|------|------|------|
| 问题定位时间 | 30分钟 | 5分钟 | ⬇️ 83% |
| 性能瓶颈识别 | 人工分析 | 自动告警 | ⬇️ 95% |
| 系统透明度 | 黑盒 | 白盒 | ✅ 完全可见 |

### 监控覆盖率目标

| 模块 | 追踪覆盖 | 指标覆盖 | 告警覆盖 |
|------|---------|---------|---------|
| DigitalLife.chat | 100% | 100% | ✅ |
| VectorStore | 100% | 100% | ✅ |
| 规划引擎 | 80% | 80% | ✅ |
| LLM调用 | 100% | 100% | ✅ |

---

## 🎯 7. 快速开始指南

### 步骤1: 创建监控模块

```bash
mkdir -p agent/monitoring
touch agent/monitoring/__init__.py
touch agent/monitoring/tracing.py
touch agent/monitoring/metrics.py
touch agent/monitoring/health.py
```

### 步骤2: 复制提供的代码

将上述代码复制到对应文件即可。

### 步骤3: 运行测试

```python
# test_monitoring.py
from agent.monitoring import MetricsCollector, TraceContext

collector = MetricsCollector()

with TraceContext("Test", "operation"):
    # 模拟操作
    import time
    time.sleep(0.1)

stats = collector.get_all_metrics()
print(stats)
```

### 步骤4: 集成到 DigitalLife

```python
from agent.monitoring import TraceContext, get_metrics_collector

class DigitalLife:
    def chat(self, user_input: str) -> str:
        with TraceContext("DigitalLife", "chat"):
            # 原有逻辑...
            pass
```

---

## 📚 8. 文档清单

| 文档 | 内容 | 优先级 |
|------|------|--------|
| `MONITORING_ARCH.md` | 监控架构设计 | P0 |
| `TRACE_GUIDE.md` | 追踪使用指南 | P1 |
| `METRICS_REF.md` | 指标参考手册 | P1 |
| `ALERT_RULES.md` | 告警规则配置 | P2 |
| `GRAFANA_DASHBOARDS.md` | 可视化配置 | P2 |

---

## 🚀 下一步建议

1. **立即开始**: 实现 TraceContext + MetricsCollector（预计1天）
2. **快速验证**: 运行基础测试确保工作正常
3. **渐进扩展**: 根据需要添加 Prometheus/Grafana

这套方案的优势：
- ✅ **零外部依赖**: 可从零开始，逐步引入外部组件
- ✅ **与现有日志兼容**: 基于现有的日志系统增强
- ✅ **可渐进扩展**: 从简单追踪到完整可观测性平台
- ✅ **性能开销小**: 轻量级实现，不影响系统性能

您想现在开始实现第一阶段的基础追踪框架吗？
