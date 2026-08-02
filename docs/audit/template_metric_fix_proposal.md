# P1 修复方案：template 埋点异常跳过风险

> **风险等级**：P1 高
> **命中点**：`agent/orchestrator/orchestrator.py:358`（`_record_intent_layer("template")`）
> **状态**：方案待评审（未实施）
> **关联文档**：~~orchestrator_intent_layer_audit.md~~ ⚠ (待确认) 命中点 2

---

## 一、问题描述

### 现状代码（L344-359）

```python
if template_response:
    logger.info(...)  # 业务日志
    self._set_thinking_mode("instinct")
    response = template_response
    self._last_was_template = True
    self._last_context_warning = None
    self._memory.score_and_save_message("user", user_input)
    self._memory.score_and_save_message("assistant", response)
    try:
        self._memory.infer_working_memory(user_input, response)
    except Exception:
        pass
    logger.info(...)  # 模板回复完成日志
    if trace_id:
        trace_store.add_span(trace_id, TraceSpan(...))   # ← 风险点 1
        trace_store.end_trace(trace_id, response)        # ← 风险点 2
    _record_intent_layer("template")                     # ← 埋点（依赖前面不抛异常）
    return ResponseBuilder.success(response).to_dict()
```

### 风险路径

```
trace_store.add_span / end_trace 抛异常
    ↓
跳过 _record_intent_layer("template")
    ↓
进入 except Exception 分支（L364）降级 LLM
    ↓
主链路继续到 L418 记录 _record_intent_layer("llm")
    ↓
结果：template 计数虚低 + llm 计数虚高（非双重计数，但指标失真）
```

### 违反的不变量

- **INV-3**：`_record_intent_layer` 不得放在可能抛异常的操作之后（除 Exception 会跳过埋点）
- **INV-2**：业务结果已确定（template 命中）后应保证埋点触发

---

## 二、根因分析

| 因素 | 说明 |
|------|------|
| trace_store 调用无异常保护 | `trace_store.add_span` 和 `end_trace` 直接调用，未用 try/except 包裹 |
| 埋点位置依赖前面语句成功 | `_record_intent_layer("template")` 在 trace_store 调用之后，前面异常会跳过 |
| except 分支未补偿埋点 | L364 的 `except Exception` 只记日志降级 LLM，未补记 template |

**对比**：semantic 层的 trace span 记录（L932-949）已用 try/except 保护，是正确做法。template 层未对齐。

---

## 三、修复方案对比

### 方案 A：保护 trace_store 调用（推荐）⭐

**思路**：给 trace_store 调用加 try/except，确保 trace 异常不影响埋点。与 semantic 层（L932-949）实现对齐。

**改动范围**：仅 L349-357，最小改动。

**修复后代码**：

```python
if template_response:
    logger.info(log_dict({...}))
    self._set_thinking_mode("instinct")
    response = template_response
    self._last_was_template = True
    self._last_context_warning = None
    self._memory.score_and_save_message("user", user_input)
    self._memory.score_and_save_message("assistant", response)
    try:
        self._memory.infer_working_memory(user_input, response)
    except Exception:
        pass
    logger.info(log_dict({...}))
    # 【不易】trace span 记录用 try/except 保护（P1 修复）：
    # 避免 trace_store.add_span / end_trace 异常跳过下方 _record_intent_layer("template") 埋点，
    # 与 semantic 层（L932-949）实现对齐。守 INV-3。
    if trace_id:
        try:
            trace_store.add_span(trace_id, TraceSpan(
                span_id=f"{trace_id}_template",
                operation="template_reply",
                status="success",
                metadata={"intent": intent,
                          "confidence": confidence.name},
            ))
            trace_store.end_trace(trace_id, response)
        except Exception as _trace_e:
            logger.warning(log_dict({
                'module_name': 'orchestrator',
                'action': 'orchestrator.process.template_trace_failed',
                'message': '[路由] template trace 记录失败（不影响埋点与主链路）: %s' % (_trace_e,)
            }))
    _record_intent_layer("template")
    return ResponseBuilder.success(response).to_dict()
```

**优点**：
- ✅ 最小改动，只加 try/except 包裹
- ✅ 与 semantic 层实现一致（L932-949 已有同样保护）
- ✅ trace 异常被记录（WARNING 级别），不静默吞掉
- ✅ 埋点必定触发，守 INV-2 / INV-3

**缺点**：
- ⚠️ trace 异常时 trace span 会丢失（但比埋点丢失影响小，trace 是辅助排查工具）

---

### 方案 B：埋点移到 try 块外 + finally 保证（更彻底）

**思路**：把 `_record_intent_layer("template")` 移到 try/except 结构之外，用临时变量标记是否命中，finally 中保证埋点。

**改动范围**：L336-365，较大改动。

**修复后代码骨架**：

```python
if template_response:
    _template_hit = True  # 标记命中
    try:
        # ... 现有 template 逻辑 ...
        if trace_id:
            try:
                trace_store.add_span(...)
                trace_store.end_trace(trace_id, response)
            except Exception:
                pass
    except Exception as e:
        logger.warning(...)
        _template_hit = False  # 异常时降级，不记 template
    finally:
        if _template_hit:
            _record_intent_layer("template")
    if _template_hit:
        return ResponseBuilder.success(response).to_dict()
```

**优点**：
- ✅ 埋点逻辑与业务逻辑完全解耦
- ✅ 异常路径不记 template（语义更准确）

**缺点**：
- ❌ 改动大，引入 `_template_hit` 状态变量，复杂度上升（违简易）
- ❌ 需重构 try/except/finally 结构，影响外层 L331-365 的控制流
- ❌ 与现有代码风格不一致（其他命中点都是直接调用）

---

### 方案 C：在 except 分支补偿埋点

**思路**：保持现有结构，在 L364 的 `except Exception` 分支补记 template。

**问题**：
- ❌ 语义错误：except 分支是"路由失败降级 LLM"，此时 template 实际未成功，不该记 template
- ❌ 仍会导致 llm 也被记录（双重计数），只是把"虚低+虚高"换成"虚高+虚高"
- ❌ 不推荐

---

## 四、推荐方案：A

**理由**：
1. **最小改动**（守简易）：仅加 try/except 包裹，不改变控制流
2. **对齐既有实现**：semantic 层（L932-949）已是此模式，保持一致性
3. **守不变量**：trace 异常不影响埋点（INV-3），业务结果确定后必埋点（INV-2）
4. **可观测**：trace 失败记 WARNING，不静默

---

## 五、验证方法

### 5.1 单元测试

新增测试用例：mock `trace_store.add_span` 抛异常，验证 `_record_intent_layer("template")` 仍被调用。

```python
def test_template_metric_survives_trace_exception(monkeypatch):
    """trace_store 异常时 template 埋点仍触发（P1 修复验证）"""
    # 模拟 trace_store.add_span 抛异常
    monkeypatch.setattr(trace_store, "add_span",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("trace boom")))
    # ... 构造 template 命中场景 ...
    orchestrator.process(user_input="你好", ...)
    # 验证 template 指标计数 +1
    assert counter_before_template + 1 == get_counter("template")
```

### 5.2 集成验证

```bash
# 1. 启动 mock 服务，注入 trace 异常
python scripts/mock_intent_layer_traffic.py --distribution rule:0,semantic:0,llm:0,template:100,reject:0

# 2. 跑诊断脚本对比
python scripts/diagnose_intent_layer.py --log-file orchestrator.log --since 60
# 期望：template 计数 = 业务 template 命中日志数（无丢失）
```

### 5.3 回归检查

修复后跑现有 reject 测试套件，确认未破坏：
```bash
python -m pytest tests/unit/test_orchestrator_reject.py -v
python -m pytest tests/integration/test_orchestrator三层路由_e2e.py -v
```

---

## 六、回滚方案

方案 A 改动局部，回滚简单：

```bash
# 回滚到修复前（删除 try/except 包裹，恢复直接调用）
git diff agent/orchestrator/orchestrator.py  # 查看改动
git checkout agent/orchestrator/orchestrator.py  # 单文件回滚
```

回滚不影响 P0（semantic 修复），两者独立。

---

## 七、实施清单

- [ ] 评审方案 A（本文件）
- [ ] 实施代码修改（替换 L349-357）
- [ ] 新增单元测试 `test_template_metric_survives_trace_exception`
- [ ] 跑回归测试 `test_orchestrator_reject.py` + `test_orchestrator三层路由_e2e.py`
- [ ] 更新 ~~orchestrator_intent_layer_audit.md~~ ⚠ (待确认) 命中点 2 风险等级为"已修复"
- [ ] PR 评审合并

---

## 八、版本历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-08-01 | v1.0 | 初版：方案 A/B/C 对比，推荐方案 A |
