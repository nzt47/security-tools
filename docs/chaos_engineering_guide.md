# 混沌工程操作指南

## 概述

混沌工程是一种通过主动注入故障来验证系统韧性和可观测性的实践方法。本指南描述了如何使用 `chaos_injector` 模块进行故障注入测试。

## 故障注入工具

### 支持的故障类型

| 故障类型 | 说明 | 适用场景 |
|---------|------|---------|
| `NETWORK_DELAY` | 网络延迟注入 | 测试超时处理、降级机制 |
| `NETWORK_TIMEOUT` | 网络超时注入 | 测试超时重试逻辑 |
| `SERVICE_UNAVAILABLE` | 服务不可用 | 测试下游服务故障处理 |
| `MEMORY_PRESSURE` | 内存压力 | 测试内存耗尽场景 |
| `CPU_PRESSURE` | CPU压力 | 测试CPU密集场景 |
| `CONCURRENT_PRESSURE` | 高并发压力 | 测试并发控制 |

### 快速开始

```python
from agent.monitoring import get_chaos_injector, FaultType, chaos_fault

# 获取故障注入器实例
injector = get_chaos_injector()

# 方式1: 使用上下文管理器（推荐）
with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=3000):
    # 在此上下文中的操作会受到3秒延迟
    make_request()

# 方式2: 手动注入和清理
injector.inject_network_delay(delay_ms=5000)
# 执行测试操作
injector.clear_fault(FaultType.NETWORK_DELAY)

# 方式3: 使用装饰器
@with_chaos_injection(FaultType.NETWORK_DELAY)
def make_request(url):
    # 此函数调用会受到故障影响
    pass
```

## 详细使用方法

### 1. 网络延迟故障

```python
# 注入3秒延迟，持续5秒
injector.inject_network_delay(
    delay_ms=3000,      # 延迟毫秒数
    probability=1.0,    # 触发概率 (0-1)
    duration_ms=5000,   # 持续时间，None表示持续直到手动清除
    target_service="api-service"  # 目标服务（可选）
)
```

### 2. 网络超时故障

```python
# 注入超时，所有请求都会触发超时
injector.inject_network_timeout(
    probability=1.0,    # 触发概率
    duration_ms=10000   # 持续10秒
)
```

### 3. 服务不可用故障

```python
# 模拟下游服务返回503错误
injector.inject_service_unavailable(
    service_name="downstream-api",  # 服务名称
    error_code=503,                # HTTP错误码
    probability=1.0,               # 触发概率
    duration_ms=5000               # 持续时间
)
```

### 4. 内存压力故障

```python
# 分配512MB内存
injector.inject_memory_pressure(
    target_mb=512,      # 目标内存占用(MB)
    duration_ms=10000   # 持续10秒
)
```

### 5. CPU压力故障

```python
# 消耗CPU资源
injector.inject_cpu_pressure(
    duration_ms=5000    # 持续5秒
)
```

### 6. 清除故障

```python
# 清除特定故障
injector.clear_fault(FaultType.NETWORK_DELAY)

# 清除所有故障
injector.clear_all()
```

## 混沌测试流程

### 标准测试流程

```
┌─────────────────────────────────────────────────────────────┐
│                    混沌测试流程                             │
├─────────────────────────────────────────────────────────────┤
│  1. 准备阶段                                               │
│     └─ 记录测试前的系统状态（指标、日志、追踪）              │
│                                                            │
│  2. 故障注入                                               │
│     └─ 使用 chaos_fault 上下文管理器注入故障               │
│                                                            │
│  3. 执行测试                                               │
│     └─ 执行待验证的业务操作                                │
│                                                            │
│  4. 验证阶段                                               │
│     ├─ 验证追踪链路完整性                                   │
│     ├─ 验证指标变化正确性                                   │
│     ├─ 验证日志包含足够信息                                 │
│     └─ 验证告警正确触发                                     │
│                                                            │
│  5. 恢复阶段                                               │
│     └─ 自动清除故障，恢复系统正常状态                        │
│                                                            │
│  6. 报告生成                                               │
│     └─ 生成测试报告和改进建议                               │
└─────────────────────────────────────────────────────────────┘
```

### 测试用例模板

```python
def test_network_delay_scenario():
    """测试网络延迟场景下的可观测性"""
    
    # 1. 记录测试前状态
    metrics_before = capture_metrics()
    
    # 2. 注入故障
    with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=3000):
        # 3. 执行测试操作
        with TraceContext("Service", "operation") as ctx:
            try:
                result = perform_operation()
            except Exception as e:
                # 4. 验证异常被正确捕获
                verify_error_reported(e, ctx.trace_id)
    
    # 5. 验证指标变化
    metrics_after = capture_metrics()
    verify_metrics_change(metrics_before, metrics_after)
    
    # 6. 验证追踪完整性
    verify_trace_completeness()
```

## 可观测性验证检查清单

### 追踪链路验证

- [ ] 追踪ID在故障场景下正常生成
- [ ] Span正确记录异常状态
- [ ] 错误事件被正确记录
- [ ] 分布式上下文正确传递

### 指标验证

- [ ] 错误计数器正确递增
- [ ] 延迟直方图正确更新
- [ ] 熔断器状态指标正确反映
- [ ] 资源使用指标正确采集

### 日志验证

- [ ] 异常信息包含trace_id
- [ ] 错误级别正确设置
- [ ] 错误堆栈完整记录
- [ ] 上下文信息足够定位问题

### 告警验证

- [ ] 告警规则正确触发
- [ ] 告警级别正确设置
- [ ] 告警包含足够上下文
- [ ] 告警通知正确发送

## 安全注意事项

### 生产环境注意事项

1. **禁用默认**：混沌注入器默认不启用任何故障
2. **权限控制**：确保只有授权人员可以执行故障注入
3. **影响范围**：明确故障影响的服务和用户范围
4. **恢复机制**：确保故障可以快速清除
5. **监控告警**：在故障注入期间关闭相关告警或调整阈值

### 最佳实践

- 仅在非高峰时段进行混沌测试
- 从低影响的故障类型开始
- 逐步增加故障强度和范围
- 确保有明确的回滚计划
- 记录所有故障注入操作

## 运行混沌测试

### 运行完整测试套件

```bash
python scripts/run_chaos_test.py
```

### 测试输出

测试完成后会生成：
- 控制台输出测试结果
- 详细的Markdown报告文件
- 故障注入统计信息

## 报告分析

### 报告结构

```
混沌工程测试报告
├── 测试概述          # 测试总数、通过/失败数
├── 测试详情          # 各测试场景的详细结果
├── 改进建议          # 基于问题的改进建议
└── 统计摘要          # 故障注入统计信息
```

### 常见问题与建议

| 问题类型 | 可能原因 | 建议措施 |
|---------|---------|---------|
| 追踪ID缺失 | OpenTelemetry未正确初始化 | 检查配置，确保依赖安装 |
| 延迟未生效 | 故障未正确注入 | 检查故障类型和参数 |
| 内存未释放 | GC未及时清理 | 增加清理间隔或手动触发GC |
| 指标未更新 | 指标收集器未正确配置 | 检查Prometheus配置 |

## API 参考

### ChaosInjector 类

#### 方法列表

| 方法 | 说明 | 参数 |
|------|------|------|
| `inject_network_delay()` | 注入网络延迟 | delay_ms, probability, duration_ms, target_service |
| `inject_network_timeout()` | 注入网络超时 | probability, duration_ms |
| `inject_service_unavailable()` | 注入服务不可用 | service_name, error_code, probability, duration_ms |
| `inject_memory_pressure()` | 注入内存压力 | target_mb, duration_ms |
| `inject_cpu_pressure()` | 注入CPU压力 | duration_ms |
| `clear_fault()` | 清除指定故障 | fault_type |
| `clear_all()` | 清除所有故障 | 无 |
| `get_active_faults()` | 获取活跃故障列表 | 无 |
| `get_stats()` | 获取统计信息 | 无 |

### 上下文管理器

#### chaos_fault

```python
with chaos_fault(fault_type, **kwargs):
    # 在此上下文中执行会受到故障影响
    pass
```

参数：
- `fault_type`: FaultType枚举值
- `**kwargs`: 对应故障类型的参数

### 装饰器

#### with_chaos_injection

```python
@with_chaos_injection(FaultType.NETWORK_DELAY, target_service="api")
def my_function():
    pass
```

参数：
- `fault_type`: FaultType枚举值
- `target_service`: 目标服务（可选）

## 附录

### FaultType 枚举

```python
FaultType.NETWORK_DELAY      # 网络延迟
FaultType.NETWORK_TIMEOUT    # 网络超时
FaultType.SERVICE_UNAVAILABLE # 服务不可用
FaultType.MEMORY_PRESSURE    # 内存压力
FaultType.CPU_PRESSURE       # CPU压力
FaultType.CONCURRENT_PRESSURE # 并发压力
```

### 配置建议

| 场景 | 推荐配置 |
|------|---------|
| 开发环境 | 可使用较高强度故障 |
| 测试环境 | 中等强度，定期执行 |
| 生产环境 | 谨慎使用，低概率故障 |

### 参考资源

- [混沌工程原理](https://principlesofchaos.org/)
- [OpenTelemetry 文档](https://opentelemetry.io/docs/)
- [Prometheus 监控](https://prometheus.io/docs/)
