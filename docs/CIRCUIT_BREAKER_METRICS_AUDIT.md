# circuit_breaker 状态变更监控埋点审查报告

> 审查时间: 2026-07-17
> 审查范围: `agent/memory/adapters/holographic_adapter.py` 中熔断机制相关代码
> 审查目的: 检查 circuit_breaker 状态变更的监控埋点是否遗漏，确保运维系统可实时告警

## 1. 审查结论

**发现 4 处埋点遗漏**，已全部修复。修复后熔断器所有状态变更均有结构化日志（`log_dict`），可供运维系统实时监控和告警。

## 2. 埋点遗漏清单与修复

### 2.1 遗漏 1：`_reset_vec_circuit` 恢复事件无结构化日志

| 项目 | 内容 |
|------|------|
| **位置** | `_reset_vec_circuit()` 方法（原 L188） |
| **问题** | 只有 `logger.info` 普通日志，无 `log_dict` 结构化日志，运维系统无法捕获恢复事件 |
| **影响** | 熔断恢复后运维系统无法自动告警"服务已恢复" |
| **修复** | 补充 `logger.info(log_dict({...action: 'vec.circuit_reset'...}))` |

### 2.2 遗漏 2：`_record_vec_failure` 未达阈值时无日志

| 项目 | 内容 |
|------|------|
| **位置** | `_record_vec_failure()` 方法 else 分支（原 L175-179） |
| **问题** | 失败计数累积时无任何日志，无法监控失败趋势（如 3/5、4/5 时运维系统无感知） |
| **影响** | 无法提前预警"即将熔断"，只能在熔断后被动告警 |
| **修复** | 补充 `logger.debug(log_dict({...action: 'vec.fail_count'...}))` 记录累积趋势 |

**设计决策**：使用 `debug` 级别而非 `info`，避免每次失败都产生日志（高频场景下日志爆炸）。运维系统可通过聚合 debug 日志计算失败速率。

### 2.3 遗漏 3：`search_vector` 降级路径无结构化日志

| 项目 | 内容 |
|------|------|
| **位置** | `search_vector()` 方法 `if not self._vec_available` 分支（原 L401-403） |
| **问题** | 熔断后每次 `search_vector` 都走降级路径返回 `[]`，但只有 `logger.info` 普通日志，无 `log_dict` |
| **影响** | 无法监控降级路径触发频率（多少请求被熔断短路） |
| **修复** | 补充 `logger.debug(log_dict({...action: 'vec.degraded_skip'...}))` |

**设计决策**：使用 `debug` 级别，因为熔断后每次 `search_vector` 都会触发，`info` 级会产生大量日志。运维系统聚合 debug 日志可计算降级请求占比。

### 2.4 遗漏 4：`_retry_vec_write` 重试耗尽无结构化日志

| 项目 | 内容 |
|------|------|
| **位置** | `_retry_vec_write()` 重试耗尽处（原 L763） |
| **问题** | 只有 `logger.error` 普通日志，无 `log_dict` 结构化日志 |
| **影响** | 向量写入持续失败时运维系统无法自动告警 |
| **修复** | 补充 `logger.error(log_dict({...action: 'vec.write_exhausted'...}))` |

## 3. 监控 action 清单（供运维系统配置告警规则）

修复后，熔断器所有状态变更均产生结构化日志，`action` 字段值如下：

| action | 级别 | 触发条件 | 运维告警建议 |
|--------|------|----------|-------------|
| `vec.circuit_break` | WARNING | 连续失败达阈值，自动熔断 | **P0 告警**：向量层已降级，需立即排查 |
| `vec.circuit_reset` | INFO | 后台探活成功，熔断器重置 | **恢复通知**：向量层已恢复可用 |
| `vec.fail_count` | DEBUG | 每次失败计数累积（未达阈值） | **趋势告警**：聚合计算失败速率，接近阈值时预警 |
| `vec.degraded_skip` | DEBUG | 熔断后 search_vector 走降级路径 | **降级频率告警**：聚合计算降级请求占比 |
| `vec.write_exhausted` | ERROR | 向量写入重试耗尽，写兜底表 | **P1 告警**：向量写入持续失败，检查 sqlite-vec 状态 |
| `search_vector.failed` | WARNING | search_vector 单次失败（含异常详情） | **故障告警**：聚合计算失败率 |
| `vec.import_failed` | WARNING | sqlite-vec 模块不可导入 | **P0 告警**：sqlite-vec 未安装或损坏 |
| `vec.load_failed` | WARNING | sqlite-vec 扩展加载全部失败 | **P0 告警**：扩展加载失败，降级运行 |
| `vec.init_failed` | WARNING | 向量表初始化异常 | **P0 告警**：向量层初始化失败 |

## 4. 日志级别设计原则

| 级别 | 使用场景 | 频率 |
|------|----------|------|
| `ERROR` | 重试耗尽、数据写入兜底表（需人工介入） | 低频 |
| `WARNING` | 熔断触发、加载失败（状态变更，需告警） | 低频 |
| `INFO` | 熔断恢复、正常路径日志（状态恢复） | 低频 |
| `DEBUG` | 失败计数累积、降级路径触发（高频，供聚合） | 高频 |

**原则**：
- 状态变更（熔断/恢复）用 WARNING/INFO（低频，直接告警）
- 高频事件（失败计数、降级短路）用 DEBUG（避免日志爆炸，供聚合分析）
- 数据丢失风险（重试耗尽）用 ERROR（需人工介入）

## 5. 运维告警规则建议

### 5.1 Prometheus/StatsD 聚合规则（参考）

```
# P0: 熔断触发（立即告警）
ALERT VecCircuitBreakerTripped
  ON log_action{action="vec.circuit_break"} > 0

# P1: 向量写入重试耗尽（5分钟内 >3 次告警）
ALERT VecWriteExhausted
  ON rate(log_count{action="vec.write_exhausted"}[5m]) > 0.05

# 预警: 失败速率接近阈值（10分钟内失败率 >50%）
ALERT VecFailureRateHigh
  ON rate(log_count{action="vec.fail_count"}[10m]) > 0.5

# 恢复通知: 熔断器重置
ALERT VecCircuitReset
  ON log_action{action="vec.circuit_reset"} > 0
```

### 5.2 降级频率监控

```python
# 运维系统可聚合 vec.degraded_skip 日志计算降级请求占比
# 如果降级占比 >10%，说明向量层持续不可用，需排查
degraded_ratio = count(action="vec.degraded_skip") / total_search_vector_requests
```

## 6. 测试验证

修复后运行 27 个单元测试全部通过（含 TestCircuitBreaker 5 + TestCircuitBreakerRecovery 5）：

```
============================= 27 passed in 4.89s ==============================
```

## 7. 不变量保持

- 【不易】修复仅补充日志，不改变熔断器逻辑（`_record_vec_failure`/`_reset_vec_circuit` 行为不变）
- 【不易】所有新增日志遵循项目 `log_dict` 规范（`module_name`/`action`/`msg` 三字段）
- 【变易】高频事件用 DEBUG 级别，低频状态变更用 WARNING/INFO，可按需调整
- 【简易】日志级别设计遵循"状态变更告警 + 高频聚合"原则，避免日志爆炸
