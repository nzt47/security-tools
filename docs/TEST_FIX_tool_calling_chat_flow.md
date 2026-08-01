# 单元测试修复文档：test_tool_calling_chat_flow

> **日期**: 2026-08-01
> **测试文件**: `tests/integration/test_digital_life_integration.py`
> **测试类**: `TestToolCallingIntegration`
> **测试方法**: `test_tool_calling_chat_flow`

---

## 一、测试目标

验证 DigitalLife 的 V2 工具调用对话流程：当 `_v2_lifetrace=True` 且 `_trace_recorder` 存在时，`process()` 应走 `_call_llm_v2` 路径，调用 `ToolCallingService.chat_with_steps`。

## 二、原有问题

### 短路返回导致 mock 未触发

orchestrator.py 的 `process()` 方法有 **5 层短路返回**，按顺序执行：

```
输入 → ① InputGuard(L180) → ② 工作流引擎(L191) → ③ 模板匹配(L332)
     → ④ 语义层匹配(L374) → ⑤ 拒识检查(L406, <3字符) → ⑥ LLM 调用(L420)
```

测试的 mock 设置在 **第⑥层**（`_call_llm_v2` / `ToolCallingService.chat_with_steps`），但请求在 **第③④层** 就被短路返回，导致 mock 未被调用。

### CI 失败日志

```
AssertionError: Expected 'chat_with_steps' to have been called.
```

## 三、修复方案

### 禁用三层短路返回

| 层级 | 短路逻辑 | 禁用方式 | 原因 |
|------|---------|---------|------|
| ③ 模板匹配 | `ResponseTemplates.for_intent()` 返回模板响应 | `patch(..., return_value=None)` | 避免模板命中后直接返回 |
| ④ 语义层匹配 | `_semantic_layer_match()` 命中短路返回 | `MagicMock(return_value=None)` | 避免语义层命中后直接返回 |
| ⑤ 拒识检查 | 输入 < 3 字符触发拒识 | 输入改为 "搜索天气信息"（5 字符） | 越过 `ORCHESTRATOR_REJECT_MIN_LENGTH=3` 阈值 |

### 修复后代码

```python
with patch('agent.tool_calling.ToolCallingService', return_value=mock_tc):
    # 【不易】禁用模板匹配和语义层匹配，确保走 V2 LLM 路径
    with patch('agent.response_workflows.ResponseTemplates.for_intent',
               return_value=None):
        digital_life = DigitalLife(config=config)
        digital_life._v2_lifetrace = True
        digital_life._trace_recorder = MagicMock()
        # 【变易】禁用语义层短路返回
        digital_life._semantic_layer_match = MagicMock(return_value=None)
        digital_life.start()

        # 触发 _call_llm_v2 路径
        result = digital_life.process("搜索天气信息")

        assert result["success"] is True
        mock_tc.chat_with_steps.assert_called()
```

## 四、LLM 路径覆盖

orchestrator.py L420-425 有两条 LLM 路径：

```python
if self._v2_lifetrace and self._trace_recorder:
    response = self._call_llm_v2(user_input, body_status)  # V2 路径
else:
    response = self._call_llm(user_input, body_status)      # 标准路径
```

本测试通过设置 `_v2_lifetrace=True` + `_trace_recorder=MagicMock()`，确保走 **V2 路径**，验证 `ToolCallingService.chat_with_steps` 被调用。

## 五、InputGuard 分析

**无需 mock InputGuard**。原因：

- `InputGuard.check("搜索天气信息")` 不匹配任何注入模式（INJECTION_PATTERNS）
- 返回 `GuardResult(GuardAction.ALLOW)`，不会触发 L181 的 BLOCK 分支
- 请求正常进入 L191 工作流引擎匹配

## 六、验证结果

```
1 passed in 1.12s ✅
```

## 七、三义校验

| 原则 | 体现 |
|------|------|
| **不易** | 测试目标不变——验证 V2 工具调用路径；禁用短路返回而非删除测试逻辑 |
| **变易** | mock 策略可扩展——其他测试可复用相同的禁用模式 |
| **简易** | 最小修改——仅添加 2 个 patch + 1 个 mock 赋值 + 输入改长，不改动测试断言 |
