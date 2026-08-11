# D13 预算优化与交互验证技术复盘

> 生成日期: 2026-08-11
> 关联缺陷: D13（无资源/成本约束——ReActLoop 的 config 被保存但从未读取）
> 涉及代码: [react.py](../../planning/react.py) · [models/react.py](../../planning/models/react.py)
> 关联测试: [test_planning_defect_d13.py](../../tests/unit/test_planning_defect_d13.py) · [capability_baseline](../../tests/unit/test_planning_capability_baseline.py)

## 1. 背景：D13 缺陷复盘

原始缺陷（P2）：ReAct 循环对 `config["timeout_seconds"]` 等预算配置**只保存、不读取**——
- 无 deadline（迭代级超时）
- 无 token/cost 预算
- 无资源调度概念

后果：恶意/失控任务可无限迭代（仅靠 `max_iterations` 兜底）、慢工具可拖死循环、成本不可控。

## 2. 实现演进（三步落地）

### 2.1 第一步：deadline 迭代级预算（既有 D13 修复）

循环入口每轮检查 `elapsed >= deadline_seconds` → 终止并返回预算错误。这是预算体系的**地基**（【不易】防失控安全边界）。

### 2.2 第二步：token/cost 三层预算 + 硬超时（commit `05dd2eac`）

| 层 | 机制 | 说明 |
|---|---|---|
| token | `_estimate_tokens`（`len//3`）累计 prompt+response | 超限终止，`token_used` 透出可观测 |
| cost | `token/1000 × token_price_per_1k`（默认 0.002$） | 超限终止，`cost` 透出 |
| tool_timeout | 异步工具 `asyncio.wait_for` 硬超时 | 慢工具不拖死循环；同步工具由 deadline 兜底 |

关键取舍：
- **估算而非精确**：LLM 返回纯文本无 usage 结构 → `len//3` 近似，接口预留精确切换（`ReActResult.token_used/cost` 已透出）。
- **三层独立可配**：`timeout_seconds` / `token_budget` / `cost_budget` / `tool_timeout_seconds` 各自独立，不互相依赖，可单独启用。

### 2.3 第三步：预算超限「征求用户」分支（commit `e1ac11ff`）

规格要求「预算超限 → 征求用户」，此前超限直接终止。设计：

```python
_budget_result(detail, steps, iteration, elapsed):
    if self.budget_ask_user:          # 可配置开关，默认 False
        return 等待用户输入信号 + 预算详情   # 与 D3 ask_user 同语义
    return 直接终止 + 预算错误            # 向后兼容
```

三义权衡：
- 【不易】三层预算终止语义是不可变安全边界——征求用户分支**不削弱**预算检查，仅改变超限后的行为（暂停而非硬失败），且**默认关闭**保持既有行为零破坏。
- 【变易】`budget_ask_user` 配置化：纯自动环境（无用户）不受影响；交互环境可启用。
- 【简易】复用 `_budget_result` 单点收敛三分支，与 D3「等待用户输入」信号共用语义，上层无需新协议。

## 3. 交互验证（mock 场景，2/2 通过）

构造 mock 场景验证完整交互闭环：

### 场景 A：超限 → 征求 → 用户提高预算继续 → 完成

```text
token 预算=1，启用 budget_ask_user
→ 第 2 轮迭代超限(227/1)：返回「等待用户输入：超出token预算(227/1)」暂停信号
→ 用户决策：提高预算后重新执行（token_budget=10000）
→ 成功：「库存充足，完成下单」（1 步完成，token 226）
```

### 场景 B：超限 → 征求 → 用户终止 → 零浪费

```text
cost 预算=$0.0000001，启用 budget_ask_user
→ 超限($0.000454)：返回「等待用户输入：超出成本预算(...)」
→ 用户决策：终止 → 不发起新一轮执行，资源零浪费
```

验证结论：暂停信号、预算详情携带、继续/终止双分支全部生效。

## 4. 性能对比（本地实测）

| 场景 | 无保护 | 有保护 | 收益 |
|---|---|---|---|
| 10s 慢工具 | 10s+ | 0.08s（tool_timeout=0.05s） | **~125x** |
| token 超限征求用户 | 继续消耗 LLM | 0ms 即时暂停 | 零浪费 |
| 预算保护生效时机 | — | 迭代级入口检查 | 超限即停 |

## 5. 测试与回归

- 专项 6 用例（D13 文件）：deadline / token / cost / 硬超时 / 征求用户开启 / 征求用户默认关闭
- planning 全量：194 passed / 0 failed（新增 2 用例后）
- 默认行为零破坏：`test_budget_ask_user_default_off` 验证未配置时仍直接终止

## 6. 已知限制（已文档化，非缺陷）

| 限制 | 说明 |
|---|---|
| token 估算精度 | `len//3` 近似；LLM 透出 usage 后切换精确统计，接口已预留 |
| 同步工具硬超时不可达 | `asyncio.wait_for` 无法中断阻塞事件循环的同步调用；由迭代级 deadline 兜底 |

## 7. 复盘结论

### 做得对的
1. **分层落地**：deadline → token/cost → 征求用户，每步独立可回滚，回归风险可控。
2. **默认向后兼容**：`budget_ask_user` 默认 False，新增能力不破坏既有调用方。
3. **信号语义复用**：征求用户沿用 D3「等待用户输入」协议，上层无需新增处理分支。

### 可改进
1. **估算精度**：LLM 层透出 usage 后应从估算切换到精确统计（接口已预留）。
2. **征求用户的上层消费**：当前暂停信号由调用方处理；后续可在 PlanningCore 层实现「预算超限 → 展示详情 → 用户确认」的正式 HITL 流程（与 config.yaml 的 human_in_the_loop 体系对齐）。
3. **基准固化**：本次性能数据为临时基准脚本产出，可沉淀为 tests/performance 下的可重复基准用例。
