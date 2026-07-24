# guard_trace 链路追踪日志示例

> 展示 `orchestrator._call_llm_v2` 跨服务调用时，`guard_trace` 如何串联护栏的 start / end 事件。
> 生成方式：`python demo_guard_trace.py`（脚本见 [demo_guard_trace.py](../demo_guard_trace.py)）。

## 1. 场景

```
orchestrator._call_llm_v2
   └─ guard_trace.start            ← 生成唯一 trace_id, 记录入口
      └─ _guard_llm_output(...)    ← 调用护栏 (关联同一 trace_id)
         ├─ _guard_llm_output.enter
         ├─ _guard_llm_output.result
         └─ _guard_llm_output.critical_strategy
   └─ guard_trace.end              ← 同一 trace_id, 记录耗时与出口
```

`guard_trace` 是 16 位 hex（`uuid.uuid4().hex[:16]`），贯穿一次护栏调用的全部日志事件。
跨服务/跨进程排查时，在日志系统中按 `guard_trace=<id>` 聚合即可还原完整调用链。

## 2. 日志示例（结构化 JSON）

以下为实际运行输出（`guard_trace=0163605cf64945c6`），每行均为 `log_dict()` 产出的结构化日志：

```json
{"module_name": "orchestrator", "action": "orchestrator.guard_trace.start", "message": "[链路追踪] 护栏调用开始 | guard_trace=0163605cf64945c6 | input_len=60 | intent=处理用户请求", "guard_trace": "0163605cf64945c6"}

{"module_name": "orchestrator", "action": "orchestrator._guard_llm_output.enter", "message": "[护栏] 入口 | response_len=60 | loaded_skills=2 | intent=处理用户请求", "guard_trace": "0163605cf64945c6"}

{"module_name": "orchestrator", "action": "orchestrator._guard_llm_output.result", "message": "[护栏] 校验完成 | severity=critical | findings=3 | has_sanitized=True", "guard_trace": "0163605cf64945c6", "severity": "critical"}

{"module_name": "orchestrator", "action": "orchestrator._guard_llm_output.critical_strategy", "message": "[护栏] critical 降级策略 | has_sanitized=True | 决策=返回脱敏输出 | 建议=保留脱敏信息继续流程", "guard_trace": "0163605cf64945c6", "severity": "critical"}

{"module_name": "orchestrator", "action": "orchestrator.guard_trace.end", "message": "[链路追踪] 护栏调用结束 | guard_trace=0163605cf64945c6 | duration_ms=51.2 | output_len=35", "guard_trace": "0163605cf64945c6", "duration_ms": 51.2}
```

## 3. trace_id 串联分析

| # | action | guard_trace | 作用 |
|---|--------|-------------|------|
| 1 | `orchestrator.guard_trace.start` | `0163605cf64945c6` | 护栏调用开始，记录 input_len / intent |
| 2 | `orchestrator._guard_llm_output.enter` | `0163605cf64945c6` | 护栏入口，记录 response_len / loaded_skills |
| 3 | `orchestrator._guard_llm_output.result` | `0163605cf64945c6` | 校验完成，记录 severity / findings |
| 4 | `orchestrator._guard_llm_output.critical_strategy` | `0163605cf64945c6` | critical 降级策略决策 |
| 5 | `orchestrator.guard_trace.end` | `0163605cf64945c6` | 护栏调用结束，记录 duration_ms / output_len |

**串联机制**：start 与 end 使用同一个 `guard_trace` 值，中间所有内部日志（enter/result/critical_strategy）均携带该值。
`duration_ms` 由 end 事件计算（`end_time - start_time`），可直接定位慢调用。

## 4. 跨服务排查方法

```bash
# 在 ELK / Loki / CloudWatch 中按 guard_trace 聚合, 还原完整调用链
query: guard_trace="0163605cf64945c6"

# 预期返回 5 条日志 (start → enter → result → critical_strategy → end)
# 若只看到 start 没看到 end → 护栏调用中途异常/卡死
# 若 duration_ms 异常大 → 校验逻辑性能问题 (如正则回溯)
```

## 5. 与 orchestrator 代码的对应关系

代码位置：`agent/orchestrator/orchestrator.py` 的 `_call_llm_v2` 方法。

```python
# [链路追踪] guard_trace 贯穿护栏调用边界, 便于排查跨服务调用
_gtrace = uuid.uuid4().hex[:16]
_gt0 = time.time()
logger.info(log_dict({
    'module_name': 'orchestrator',
    'action': 'orchestrator.guard_trace.start',
    'message': '[链路追踪] 护栏调用开始 | guard_trace=%s | input_len=%d | intent=%s'
               % (_gtrace, len(response), (user_input or '')[:40]),
}))
response = self._guard_llm_output(response, user_input)   # ← 护栏调用 (内部日志关联 _gtrace)
logger.info(log_dict({
    'module_name': 'orchestrator',
    'action': 'orchestrator.guard_trace.end',
    'message': '[链路追踪] 护栏调用结束 | guard_trace=%s | duration_ms=%.1f | output_len=%d'
               % (_gtrace, (time.time() - _gt0) * 1000, len(response)),
}))
```

`_guard_llm_output` 内部各分支（enter / result / critical / critical_strategy / pass / exception）均通过 `log_dict` 输出结构化日志，
排查时按 `guard_trace` 聚合即可看到完整的护栏决策链路。
