# TD-4 前置梳理：BudgetManager 实例归属报告（2026-08-13）

> 只读调查（未改代码）。目标：判定 decomposer/reflector 记账应注入哪个 BudgetManager 实例，防重复计费/漏计。
> 范围：planning/core.py 组装链、budget.py 记账语义、decomposer/reflector LLM 调用点时序。

## 一、现状：4 组件记账矩阵

| 组件 | LLM 调用点 | 记账现状 | 实例归属 | 成本流向 |
|---|---|---|---|---|
| ReActLoop | [react.py L415-416](../../../planning/react.py#L409-L421) | ✅ 已记账 | 自建独立实例（L103） | `react_result.cost` → `record_plan_result`（core.py L535） |
| PlanExecutor | [executor.py L969-970](../../../planning/executor.py#L959-L977) | ✅ 已记账（本轮修复） | 自建独立实例（L170） | `plan.metadata["budget"]` → `_record_plan_result`（core.py L401） |
| TaskDecomposer | [decomposer.py L167](../../../planning/decomposer.py#L166-L182)/L176（JSON 重试）/L300（refine） | ❌ 漏记 | **无** | — |
| Reflector | [reflector.py L218](../../../planning/reflector.py#L216-L233)/L250（plan_reflect） | ❌ 漏记 | **无** | — |

**结论 1**：decomposer/reflector 构造签名（`llm_service, config, reflector` / `llm_service, memory_manager, config`）均无 budget_manager 参数，core.py 创建时（L108/L132）也未注入——漏记根因。

## 二、关键时序事实（决定归属方案）

1. **`budget.start()` 不重置 `_tokens/_cost`**（[budget.py L95](../../../planning/budget.py#L95) 仅重置 `_start` 时间戳）→ 实例上 `record_text` 在 start() 之前调用**同样累计**，最终 snapshot 包含 ✅
2. **decompose 先于 execute_plan**（core.py L235 decompose → L245 附近 execute_plan）→ 若注入 executor 实例，decompose 成本自然进入该实例累计 ✅
3. **plan_reflect 在 executor finally 回填之后**（execute_plan 结束回填快照 executor.py L513 → 返回 core.py L341 plan_reflect → L374 `_record_plan_result`）→ **若 reflector 记入 executor 实例，成本不会进入已回填的 `plan.metadata["budget"]`**，`_record_plan_result` 读不到 ⚠

## 三、归属方案判定

| 方案 | 描述 | 优点 | 缺点/风险 |
|---|---|---|---|
| **A（推荐）** | decomposer/reflector 均注入 **executor.budget_manager**（core.py L128/L134 注入点补注）；`_record_plan_result` 改为从 executor.budget_manager 取最新 snapshot（或 reflector 记账后刷新 plan.metadata） | 单实例覆盖 decompose→execute→reflect 全链路；一计划一实例一快照，语义最简；无需多实例合并 | 预算 check() 语义前移：超限判定从"执行阶段"变为"含分解"，可能更早触发 EXCEEDED_COST——**需回归 331 项规划子集** |
| B | decomposer/reflector 各建独立实例 | 隔离清晰 | 多实例成本需合并汇总（`record_plan_result` 单 cost 参数），违背【简易】；不采用 |

**结论 2（推荐）**：采用方案 A。注入点复用 core.py 现有依赖注入模式（L128/L134/L138 同构）；成本流向统一为"一次计划执行一个 BudgetManager"。

## 四、实现要点（供实现阶段）

1. `TaskDecomposer.__init__` / `Reflector.__init__` 增加可选 `budget_manager=None` 参数（向后兼容，默认不记账）
2. core.py L128/L134 注入点补 `self.decomposer.budget_manager = self.executor.budget_manager` / `self.reflector.budget_manager = ...`
3. decompose LLM 调用（L167/L176 重试路径同方法体内）成功后 `budget_manager.record_text(prompt/response)`；refine（L300）同理
4. reflector L218/L250 成功后 `record_text`；**plan_reflect 记账后刷新 `plan.metadata["budget"]` 或改 `_record_plan_result` 读最新 snapshot**（二选一，实现时定）
5. 回归：P1 计费 4/4、规划子集 331/331、stage5_e2e 10/10；扩展 P1 测试（含分解的计划 cost_total>0、decompose 与 ReAct 口径一致）

## 五、风险登记

- 预算超限语义变化（分解成本计入）→ 观察 EXCEEDED_COST 相关测试是否受影响
- extract_json_with_retry 内部 LLM 调用若不走 self.llm 则无法自然记账（需核实现阶段确认）
- ReAct 路径不受影响（react 独立实例，互不干扰）
