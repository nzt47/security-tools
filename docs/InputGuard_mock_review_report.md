# InputGuard Mock 策略核对报告

> **日期**: 2026-08-01
> **核对范围**: `docs/TEST_FIX_skip_ci_summary.md` 中关于 InputGuard 的描述
> **核对基准**: `agent/guardrails/input_guard.py` + `agent/orchestrator/orchestrator.py`

---

## 一、源码基准

### GuardAction 枚举

```python
# input_guard.py:23-26
class GuardAction(Enum):
    ALLOW = "allow"
    BLOCK = "block"
```

**2 个枚举值**：`ALLOW`（放行）、`BLOCK`（拦截）

### GuardResult 数据结构

```python
# input_guard.py:29-35
@dataclass
class GuardResult:
    action: GuardAction
    reason: str = ""
    matched_pattern: str = ""
    confidence: float = 0.0
```

**4 个属性**：`action`（动作）、`reason`（原因）、`matched_pattern`（匹配模式）、`confidence`（置信度）

### 注入模式

```python
# input_guard.py:38-59
INJECTION_PATTERNS = [...]  # 8 类 / 10 个正则
```

| 类别 | 正则数量 | 示例 |
|------|---------|------|
| 指令忽略 | 2 | `ignore previous instructions` |
| System Prompt 泄露 | 2 | `what is your system prompt` |
| 角色扮演越狱 | 1 | `act as DAN` |
| 编码绕过 | 1 | `base64 encode` |
| XML/JSON 注入 | 1 | `<system>` |
| DAN 越狱 | 1 | `DAN` |
| 分隔符绕过 | 1 | `new instructions` |
| 多语言混淆 | 1 | 10+ 个西里尔/阿拉伯字符 |

### InputGuard.check 方法

```python
# input_guard.py:68-86
def check(self, text: str) -> GuardResult:
    if not text or not text.strip():
        return GuardResult(GuardAction.BLOCK, "空输入", "empty_input")
    if len(text) > self.max_input_length:
        return GuardResult(GuardAction.BLOCK, f"输入超长 ...", "input_too_long")
    for pattern, category in INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            return GuardResult(GuardAction.BLOCK, f"检测到 {category}", ...)
    return GuardResult(GuardAction.ALLOW)
```

**返回逻辑**：空输入→BLOCK，超长→BLOCK，匹配注入→BLOCK，否则→ALLOW

### orchestrator.py 调用位置

```python
# orchestrator.py:179-187
# ── 第零步：InputGuard 输入安全检查 ──
guard_result = self._input_guard.check(user_input)
if guard_result.action == GuardAction.BLOCK:
    logger.warning(...)
    return ResponseBuilder.guard_blocked(
        guard_result.reason, guard_result.matched_pattern
    ).to_dict()
```

**L180 调用 check()，L181 判断 BLOCK，L185 提前返回**

---

## 二、逐条核对

### 1. 文档 L24：`① InputGuard(L180)`

| 项 | 内容 |
|---|------|
| 文档描述 | `① InputGuard(L180) → ② 工作流引擎(L191) → ...` |
| 源码验证 | L180: `guard_result = self._input_guard.check(user_input)` ✅ |
| 结论 | **准确** ✅ |

### 2. 文档 L72：`InputGuard 放行`（test_workflow_engine_match）

| 项 | 内容 |
|---|------|
| 文档描述 | `InputGuard 放行` |
| 源码验证 | "测试输入" 不匹配 8 类注入模式 → `GuardResult(GuardAction.ALLOW)` → L181 判断为 False → 不进入 BLOCK 分支 ✅ |
| 结论 | **准确** ✅ |

### 3. 文档 L110：`触发拒识但返回 success=True`（test_v2_features_disabled_backward_compatible）

| 项 | 内容 |
|---|------|
| 原描述 | `输入: "你好" (2 字符, 触发拒识但返回 success=True)` |
| 问题 | 假设请求走了第⑤层拒识检查（L406），但该测试**未禁用模板匹配③和语义层匹配④**，请求可能在③④层就被短路返回 |
| 实际路径 | `process("你好")` → ①InputGuard(ALLOW) → ②工作流(matched=False) → ③模板匹配(可能命中时间问候) → ④语义层(可能命中) → ⑤拒识(2字符<3)。无论走③④⑤哪条路径，`ResponseBuilder` 均返回 `success=True` |
| 结论 | **已修正** ✅ |

**修正后描述**：
> 未禁用模板/语义层，但无论走哪条路径（模板匹配③/语义层④/拒识⑤），`ResponseBuilder` 均返回 `success=True`；V2 属性断言通过

---

## 三、InputGuard Mock 策略总结

### 是否需要 mock InputGuard？

**结论：6 个测试均无需 mock InputGuard。**

| 测试 | 输入 | InputGuard 行为 | 是否需 mock |
|------|------|----------------|------------|
| test_chat_increment_interaction_count | "你好，请帮我" | ALLOW（不匹配注入模式） | 否 |
| test_behavior_can_execute_rejects_request | "请求资源" | ALLOW | 否 |
| test_workflow_engine_match | "测试输入" | ALLOW | 否 |
| test_tool_calling_chat_flow | "搜索天气信息" | ALLOW | 否 |
| test_memory_logging | "测试记忆功能" | ALLOW | 否 |
| test_v2_features_disabled_backward_compatible | "你好" | ALLOW | 否 |

### 原因

1. **6 个测试的输入均不匹配注入模式**：输入文本为中文日常用语，不包含 `ignore instructions`、`system prompt`、`DAN`、`base64` 等注入关键词
2. **InputGuard.check() 返回 ALLOW**：L181 的 BLOCK 分支不执行，请求正常进入第①步工作流引擎匹配
3. **mock InputGuard 反而增加复杂度**：违背【简易】原则

### 何时需要 mock InputGuard？

如果未来新增测试的输入包含注入关键词（如测试 InputGuard 本身的拦截功能），则需要：

```python
from agent.guardrails.input_guard import GuardAction, GuardResult

# 方案 A：mock check 返回 ALLOW（绕过拦截）
digital_life._input_guard = MagicMock()
digital_life._input_guard.check.return_value = GuardResult(GuardAction.ALLOW)

# 方案 B：mock check 返回 BLOCK（测试拦截行为）
digital_life._input_guard = MagicMock()
digital_life._input_guard.check.return_value = GuardResult(
    GuardAction.BLOCK, "检测到注入", "ignore_instructions"
)
```

---

## 四、核对结论

| 检查项 | 数量 | 状态 |
|--------|------|------|
| 文档中 InputGuard 描述 | 3 处 | 2 处准确 + 1 处已修正 |
| 源码一致性 | 全部 | ✅ 一致 |
| Mock 策略 | 6 个测试 | ✅ 均无需 mock InputGuard |
