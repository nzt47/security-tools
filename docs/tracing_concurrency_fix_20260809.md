# trace_id 栈式管理并发安全加固记录

> 日期：2026-08-09
> 提交：`a822fb41`（fix(tracing)）
> 模块：`agent/monitoring/tracing.py`

## 背景与问题

`TraceContext` 通过 ContextVar（`_current_trace_id`）在线程/协程内传递 trace_id，
并在 `__enter__` 保存旧值、`__exit__` 恢复旧值，形成"栈式"管理。

原实现存在三个并发安全隐患：

1. **手动 `set(旧值)` 恢复不可靠**：`__exit__` 用 `_current_trace_id.set(self._old_trace_id)`
   盲目覆盖。若 `with` 块内 trace_id 被其他逻辑修改（如并发协程、线程池复用、手动
   `set_trace_id` 未配对恢复），退出时旧值会**覆盖中间状态**，无法感知污染。
2. **无冲突检测**：trace_id 被外部修改时没有任何告警，污染源难以定位。
3. **`run_with_context` 同样用 `set(旧值)` 恢复**：线程池传播场景下存在相同风险。

## 修复方案

### 1. Token 式恢复（核心）

`__enter__` 用 `ContextVar.set()` 返回 Token，`__exit__` 用 `ContextVar.reset(Token)`
精确恢复到"本次 set 之前"的值：

```python
# __enter__：set 返回 Token，记录"本次 set 之前"的状态
self._token = _current_trace_id.set(self.trace_id)
self._span_token = _current_span_id.set(self.span_id)

# __exit__：reset(Token) 精确恢复，不受 with 块内其他 set 操作影响
_current_trace_id.reset(self._token)
_current_span_id.reset(self._span_token)
```

这是 ContextVar 官方推荐的恢复方式，语义确定：无论 `with` 块内发生了多少次 set，
`reset` 总是回到本上下文 `__enter__` 时的精确状态。

### 2. 冲突检测

`__exit__` 时若发现当前 trace_id 不等于本上下文内设置的值，说明存在外部/并发污染，
输出结构化告警日志：

```python
if self._token is not None and current_tid != self.trace_id:
    logger.warning(json.dumps({
        "trace_id": self.trace_id,
        "module_name": "tracing",
        "action": "trace_context.conflict_detected",
        "message": "退出上下文时 trace_id 已被外部修改，存在并发污染风险",
        "expected": self.trace_id,
        "actual": current_tid,
    }, ensure_ascii=False))
```

### 3. 防御性降级

`reset(Token)` 跨 context（不同协程/线程配对 `__enter__`/`__exit__`）时会抛
`ValueError`。`__exit__` 捕获异常并降级为手动恢复，**保证 `__exit__` 永不抛异常**
（否则会掩盖 `with` 块内的原始异常）：

```python
try:
    if self._token is not None:
        _current_trace_id.reset(self._token)
    if self._span_token is not None:
        _current_span_id.reset(self._span_token)
except Exception as exc:
    logger.warning(json.dumps({...}, ensure_ascii=False))
    try:
        _current_trace_id.set(self._old_trace_id)
        _current_span_id.set(self._old_span_id)
    except Exception:
        pass
```

### 4. run_with_context Token 化

线程池传播场景下，设置/恢复均用 Token，finally 中**逆序 reset**（后 set 先恢复，
保证嵌套安全）。

## 场景覆盖验证

| 场景 | 验证方式 | 结果 |
|------|---------|------|
| 独立调用唯一性 | 10 次独立 `with` → 10 个不同 trace_id | ✅ |
| 嵌套上下文共享 | 内层复用外层 ID，退出恢复外层 | ✅ |
| 外部传播 | `set_trace_id` 后 `with` 复用，退出保留外部值 | ✅ |
| 异常安全 | `with` 块内抛异常，退出正确恢复 | ✅ |
| asyncio 并发隔离 | 10 个并发协程 → 10 个唯一 trace_id | ✅ |
| 冲突检测+精确恢复 | 块内 `set_trace_id("POLLUTED")` 模拟污染，退出恢复外部值 | ✅ |
| 线程池传播 | `run_with_context` 在线程内设置并恢复为 None | ✅ |
| span_id 栈式恢复 | 嵌套 span 不同，退出逐级恢复 | ✅ |

## 验证结果

- 单元测试：`tests/unit/test_monitoring_tracing.py` + `test_performance_alert.py` — **51 passed**
- 并发专项：`.fix_backups/verify_concurrency.py` — **11/11 通过**
- 完整回归：见 `full_regression.log`（对比基线，确认无新回归）

## 附带修复（同 commit）

| 文件 | 内容 |
|------|------|
| `agent/error_handler.py` | `CircuitBreaker`/`ErrorHandler` 锁升级为 `RLock`，防重入死锁 |
| `tests/unit/test_performance_alert.py` | 重置正确的模块单例（`_perf_module._alert_manager`） |
| `scripts/protect_source_files.ps1` | 文件保护脚本（check/watch/restore），防 IDE 自动还原 |
| `.gitignore` / `.vscode/settings.json` | 排除 `.fix_backups/` 目录与文件监视 |

## 使用注意事项

1. **线程池任务不会继承主线程 trace_id**（ContextVar 天然隔离）——如需传播请用
   `run_with_context` 或显式参数传递。
2. `__exit__` 冲突告警日志（`trace_context.conflict_detected`）仅记录不阻断——
   若频繁出现，说明存在并发污染源，需排查线程池复用或协程调度。
3. 不要在跨协程/线程中手动配对 `__enter__`/`__exit__`——Token reset 会触发防御降级
   （能工作但不推荐）。

## 相关链接

- 源码：`agent/monitoring/tracing.py`
- 验证脚本：`.fix_backups/verify_concurrency.py`
- 回归日志：`.fix_backups/full_regression.log`
