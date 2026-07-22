# 熔断器日志埋点变更说明（代码审查备注）

> 变更类型: 监控埋点补充（不改变业务逻辑）
> 影响范围: `agent/memory/adapters/holographic_adapter.py`
> 审查重点: 日志级别设计 + action 字段命名 + 是否改变熔断器行为

## 变更摘要

本次变更补充 4 处 `circuit_breaker` 状态变更的监控埋点，使运维系统能够实时告警熔断/恢复事件，并聚合监控失败趋势与降级频率。**仅补充日志，不改变熔断器逻辑**。

## 不变量保证

- 【不易】`_record_vec_failure` / `_reset_vec_circuit` 的行为逻辑完全未变（仅 else 分支新增日志）
- 【不易】所有新增日志遵循项目 `log_dict` 规范（`module_name` / `action` / `msg` 三字段）
- 【不易】`save` / `search` / `search_vector` 接口签名未变
- 【变易】日志级别按频率分级（高频 DEBUG / 低频 WARNING/INFO/ERROR）
- 【简易】每处改动都是单行 `logger.X(log_dict({...}))` 追加，无复杂逻辑

## 4 处改动详情

### 改动 1: `_reset_vec_circuit` 恢复事件结构化日志

**位置**: `_reset_vec_circuit()` 方法（约 L188）

**改动前**:
```python
self._vec_fail_count = 0
self._vec_available = True
logger.info("[HolographicAdapter][vec] 熔断器重置: _vec_available=True, fail_count=0")
```

**改动后**:
```python
self._vec_fail_count = 0
self._vec_available = True
# [TLM-L2] 监控埋点：熔断恢复事件（结构化日志，供运维系统告警恢复）
logger.info(log_dict({'module_name': 'holographic_adapter', 'action': 'vec.circuit_reset', 'msg': '[HolographicAdapter][vec] 熔断器重置: _vec_available=True, fail_count=0'}))
logger.info("[HolographicAdapter][vec] 熔断器重置: _vec_available=True, fail_count=0")
```

**原因**: 原仅有普通日志，运维系统无法捕获"服务已恢复"事件。补充结构化日志后，可通过 `action=vec.circuit_reset` 配置恢复通知告警。

---

### 改动 2: `_record_vec_failure` 未达阈值时失败计数趋势日志

**位置**: `_record_vec_failure()` 方法 else 分支（约 L180）

**改动前**:
```python
self._vec_fail_count += 1
if self._vec_fail_count >= self._vec_fail_threshold and self._vec_available:
    self._vec_available = False
    logger.warning(log_dict({...action: 'vec.circuit_break'...}))
    logger.info("...")
# else 分支无任何日志
```

**改动后**:
```python
self._vec_fail_count += 1
if self._vec_fail_count >= self._vec_fail_threshold and self._vec_available:
    self._vec_available = False
    logger.warning(log_dict({...action: 'vec.circuit_break'...}))
    logger.info("...")
else:
    # [TLM-L2] 监控埋点：失败计数累积趋势（debug 级避免日志爆炸，供运维系统聚合告警）
    logger.debug(log_dict({'module_name': 'holographic_adapter', 'action': 'vec.fail_count', 'msg': f'[HolographicAdapter][vec] 失败计数累积: {self._vec_fail_count}/{self._vec_fail_threshold}'}))
```

**原因**: 原未达阈值时无日志，运维系统无法监控失败累积趋势（如 3/5、4/5 时无感知），只能在熔断后被动告警。补充 DEBUG 级日志后可聚合计算失败速率预警。

**级别选择 DEBUG 的原因**: 高频场景下每次失败都触发，INFO 级会产生大量日志。DEBUG 级默认不输出，运维系统按需开启聚合。

---

### 改动 3: `search_vector` 降级路径结构化日志

**位置**: `search_vector()` 方法 `if not self._vec_available` 分支（约 L405）

**改动前**:
```python
if not self._vec_available:
    logger.info("[HolographicAdapter][vec] search_vector: 向量层不可用，返回空列表")
    return []
```

**改动后**:
```python
if not self._vec_available:
    # [TLM-L2] 监控埋点：降级路径触发（debug 级避免日志爆炸，供运维系统监控降级频率）
    logger.debug(log_dict({'module_name': 'holographic_adapter', 'action': 'vec.degraded_skip', 'msg': '[HolographicAdapter][vec] search_vector 降级: 向量层不可用，返回空列表'}))
    logger.info("[HolographicAdapter][vec] search_vector: 向量层不可用，返回空列表")
    return []
```

**原因**: 熔断后每次 `search_vector` 都走降级路径返回 `[]`，但原仅有普通日志，无法监控降级请求占比。补充结构化日志后可计算降级频率。

**级别选择 DEBUG 的原因**: 熔断后每次 `search_vector` 都会触发（可能高频），INFO 级日志爆炸。DEBUG 级供运维聚合分析降级请求占比。

---

### 改动 4: `_retry_vec_write` 重试耗尽结构化日志

**位置**: `_retry_vec_write()` 重试耗尽处（约 L765）

**改动前**:
```python
# 重试耗尽，写兜底表
logger.error("[HolographicAdapter][vec] 向量写入重试耗尽 key=%s → 写兜底表 %s", key, self._VEC_FAILED_TABLE)
self._write_vec_failed(key, embedding, str(last_error))
self._record_vec_failure()
```

**改动后**:
```python
# 重试耗尽，写兜底表
# [TLM-L2] 监控埋点：向量写入重试耗尽（结构化日志，供运维系统告警写入失败）
logger.error(log_dict({'module_name': 'holographic_adapter', 'action': 'vec.write_exhausted', 'msg': f'[HolographicAdapter][vec] 向量写入重试耗尽 key={key} → 写兜底表 {self._VEC_FAILED_TABLE}'}))
logger.error("[HolographicAdapter][vec] 向量写入重试耗尽 key=%s → 写兜底表 %s", key, self._VEC_FAILED_TABLE)
self._write_vec_failed(key, embedding, str(last_error))
self._record_vec_failure()
```

**原因**: 原仅有普通 ERROR 日志，运维系统无法通过结构化字段告警"向量写入持续失败"。补充后可通过 `action=vec.write_exhausted` 配置 P1 告警。

## 日志级别设计原则

| 级别 | 使用场景 | 频率 | action |
|------|----------|------|--------|
| `ERROR` | 重试耗尽、数据写入兜底表 | 低频 | `vec.write_exhausted` |
| `WARNING` | 熔断触发、加载失败 | 低频 | `vec.circuit_break` |
| `INFO` | 熔断恢复 | 低频 | `vec.circuit_reset` |
| `DEBUG` | 失败计数累积、降级路径 | 高频 | `vec.fail_count`, `vec.degraded_skip` |

**核心原则**:
- 状态变更（熔断/恢复）用 WARNING/INFO（低频，直接告警）
- 高频事件（失败计数、降级短路）用 DEBUG（避免日志爆炸，供聚合分析）
- 数据丢失风险（重试耗尽）用 ERROR（需人工介入）

## 测试验证

修复后运行 27 个单元测试全部通过（含 TestCircuitBreaker 5 + TestCircuitBreakerRecovery 5）：

```
============================= 27 passed in 4.89s ==============================
```

## 审查建议关注点

1. **DEBUG 级别是否合适**：`vec.fail_count` 和 `vec.degraded_skip` 是否应改为 INFO？当前选择 DEBUG 是为避免高频日志爆炸，但若运维系统不采集 DEBUG 日志则需调整
2. **action 命名一致性**：所有 action 均以 `vec.` 前缀，与现有 `search_vector.failed` / `vec.import_failed` 保持一致
3. **msg 字段冗余**：结构化日志的 `msg` 字段与紧随其后的普通日志 `logger.info("...")` 内容重复，是否应删除普通日志？当前保留是为兼容现有日志检索工具
