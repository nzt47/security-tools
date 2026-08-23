# LLM-as-Judge 双假设判别报告

> 计划：`docs/zh/进化机制重构计划/自进化机制重构计划/进化机制理想设计.md`（阶段三 · 任务5）
> 关联：任务2（KPI 数据源与触发监控）、任务3（L2 闭环受控放行 · 真实候选数据源）
> 版本：v1.0（2026-08-22，采集窗口开启快照）
> 配套：`任务5_LLM-as-Judge双假设验证_变更说明.md`（实现细节与测试）

---

## 一、结论（摘要）

**当前建议：不启用 LLM-as-Judge（干预模式）。**

依据（二值结论，无模糊空间）：
1. **判别前置未满足**：截至本报告快照，双通道有效判定样本 **0** < 最小判别基数
   **5**（`learning.judge.min_candidates`），判别结论为 `insufficient_data`
   （预设规则第 1 条），任何"启用"决策在数据上不成立；
2. **否决条件已解除但未达启用线**：预算护栏已灰度提升为 `enforce`（仅学习动作，
   主链路零影响），LLM 型评估角色的前置必要条件已满足；但"分歧率 ≥ 10% 且
   Judge 采纳率 − 规则采纳率 ≥ +10pp"的假设 A 证据尚未采集；
3. **成本纪律**：`LEARNING_JUDGE_ENABLED=false`（默认）→ 当前零 LLM 调用、零成本；
   采集窗口（2 周或候选数 ≥ 5，取先到者）开启后，全部调用经 `learning.budget`
   enforce 熔断保护。

**复核条件**：采集窗口结束后（或候选数 ≥ 5），按本报告 §3 判别规则与 §4 数据口径
重新计算；若结论转为 `hypothesis_a_eval_insufficient`（支持引入），按报告
TASK-08 §3.3 流程进入 dry-run 2 周评估（KPI 改善才启用）；若为
`hypothesis_b_candidate_quality`（候选质量差），转向改进候选生成，不引入 Judge。

---

## 二、背景与实验设计（任务标识）

### 2.1 因果歧义（理论缺陷 T3）

低进化采纳率（KPI#7 <5% 连续 4 周）存在两种互斥归因：

| 假设 | 归因 | 需要的动作 |
| --- | --- | --- |
| A：评估不精细 | 规则评估无法区分候选质量，好候选被误拒 | 引入更精细的 LLM-as-Judge |
| B：候选质量差 | 变异/生成环节产出本身就差，评估正确拒绝 | 改进候选生成，**不引入 Judge**（否则只增成本无收益） |

报告 TASK-08 §3.3 只设计了"dry-run 记录 2 周"，未设计 A/B 判别。本实验用
**同候选集双通道对照**解决：同一候选集同时过规则评估通道与 Judge 通道，比较
**分歧率**（两通道判定不一致的候选占比）与**采纳率差异**（若按 Judge 判定会
采纳/拒绝的比例 vs 规则通道采纳率）。

### 2.2 实验要素

| 要素 | 设计 |
| --- | --- |
| 候选集 | 任务3 observe/confirm 真实候选（`data/learning/rollout_audit.jsonl` preview/approved，只读追溯） |
| 规则通道 | 候选记录中的既有规则结论（只读回放）；无记录时用对齐镜像纯函数（不修改既有评估器） |
| Judge 通道 | LLM 判定（duck-typed 客户端 + 脱敏管道），输出 accept/reject + confidence |
| 判定对齐 | 两通道输出同一结构 `{verdict: accept/reject}`，方可计算分歧 |
| 零干预 | 任何判定只写 `judge_audit.jsonl` + 学习度量扩展字段，**不改变任何提交/采纳/回滚决策**（审计每条含 `intervention: false`） |
| 成本保护 | 全部 LLM 调用经 `learning.budget`（enforce）记账，超限熔断跳过，不伪造指标 |

### 2.3 采集周期

2 周（`learning.judge.collection_weeks`），或有效判定样本 ≥ `min_candidates`（默认 5），
取先到者。数据源为任务3 observe 态积累的真实候选 + Judge 通道 dry-run 运行记录。

---

## 三、判别规则（预设，可复现）

`judge_channel.discriminate()` 纯函数实现，输入为 §4 统计口径：

| 序号 | 条件（输入） | 结论 | 建议 |
| --- | --- | --- | --- |
| 1 | 有效判定样本 < `min_candidates`（默认 5），或任一比率不可计算 | `insufficient_data` | **不启用**（继续采集至 2 周或候选数达标） |
| 2 | 分歧率 < `disagreement_threshold`（默认 10%） | `hypothesis_b_candidate_quality`（假设 B） | **不引入 Judge**，转向改进候选生成 |
| 3 | 分歧率 ≥ 10% 且 (Judge 采纳率 − 规则采纳率) × 100 ≥ `min_adoption_delta_pp`（默认 +10pp） | `hypothesis_a_eval_insufficient`（假设 A） | **支持引入**，按报告 §3.3 dry-run 2 周流程评估启用 |
| 4 | 其余（分歧率高但采纳率差异 < +10pp） | `inconclusive` | **不启用**（继续采集或复核 Judge 评估 prompt 后重试） |

要点：
- **建议恒为二值**（`not_introduce` / `evaluate_introduce`），每条结论附 `basis`
  （样本量、分歧率、两通道采纳率、采纳率差异、阈值），可复算、无主观门槛；
- **阈值含等号**（分歧率 = 10% 且差异 = +10pp → 假设 A，单测覆盖边界）；
- 规则方向已用合成数据验证：假设 A 数据（分歧率 30% + 差异 +30pp → 支持引入）、
  假设 B 数据（分歧率 2% → 不引入），单测证明结论方向与预设一致。

---

## 四、数据口径与当前采集快照

### 4.1 口径

| 指标 | 定义 | 出处 |
| --- | --- | --- |
| 有效判定样本（judged） | `judge_status="judged"` 的候选（两通道均产出判定） | `judge_audit.jsonl` + `learning.judge.*` 计数 |
| 分歧率 | 分歧候选数 / judged | `judge_disagreement_rate` |
| 规则通道采纳率 | 规则通道判 accept / judged | `rule_adoption_rate` |
| Judge 通道采纳率（implied） | Judge 判 accept / judged（若按 Judge 判定会采纳的比例） | `judge_implied_adoption_rate` |
| 采纳率差异 | (implied − rule) × 100（pp） | `adoption_rate_delta_pp` |
| 预算熔断跳过 | `budget_blocked` 候选数（成本保护证据） | `budget_blocked` |
| token 成本 | 预估 token（字符/4 启发式，仅 LLM 实际调用后入账） | `tokens_used` |
| 数据充分性 | judged < min_candidates → `insufficient_data=true` | `insufficient_data` |

口径与任务2 监控扩展字段一致：`get_snapshot()["judge_dryrun"]` /
`get_weekly_kpis()[...]["judge"]`（纯增量，KPI#1~#7 既有口径零改动）。

### 4.2 当前快照（采集窗口开启时点）

数据源：`python -m agent.learning.judge_channel --status`（2026-08-22）。

| 指标 | 值 |
| --- | --- |
| 候选数（candidates） | 0 |
| 有效判定样本（judged） | 0 |
| 分歧率 | 不可计算（无样本） |
| 规则通道采纳率 | 不可计算 |
| Judge 通道采纳率（implied） | 不可计算 |
| 采纳率差异（pp） | 不可计算 |
| 预算熔断跳过 | 0 |
| token 成本（预估） | 0 |
| 数据充分性 | `insufficient_data = true`（0 < 5） |
| 判别结论 | `insufficient_data` → 建议 `not_introduce` |

> 说明：采集窗口刚开启，任务3 observe 候选尚在积累（`rollout_audit.jsonl` 目前为
> 占位状态）。本快照为判别框架的启动基线；正式报告的"数据"章节由采集窗口结束后
> 的 `--report` 输出填充（§3 规则与 §4 口径固定，结果可复现）。

---

## 五、成本核算（对照报告 §3.1 成本模型）

### 5.1 单位成本模型（dry-run 采集期）

Judge 通道单候选成本 = 单次 LLM 调用（prompt = 候选载荷 + 评审提示词）：

```
预估 token = len(prompt 字符) / 4（中英文混合启发式，与 §3.1 口径一致）
单候选预估上限 = LEARNING_JUDGE_MAX_ESTIMATED_TOKENS = 2000（默认）
```

| 项 | 说明 |
| --- | --- |
| 预算记账 | 每次调用经 `learning.budget.with_budget("judge_channel", estimated)`（enforce）；超单次上限/日预算 → `budget_blocked` 跳过，零成本入账 |
| 日预算 | `learning.budget.max_daily_tokens = 1,000,000`（与学习动作共用；熔断保护全部学习动作） |
| 熔断语义 | 日预算耗尽 → 熔断器 OPEN，后续 Judge 调用被拦（`circuit_open`），不部分执行 |
| 诚实入账 | 仅 LLM 实际调用后记录预估成本（解析失败也入账）；被预算拦截的调用零成本 |
| 否决条件 | `budget.mode != enforce` → 全部候选 `budget_not_enforce`，零 LLM 调用（当前已 enforce，不触发） |

### 5.2 成本结论

- 当前（enabled=false）：**零 LLM 调用、零 token 成本**；
- 采集窗口（enabled=true）：每候选 ≤ 2000 预估 token；按周候选量估算，2 周窗口
  预计数百至数千 token 量级，远低于日预算 100 万 token，熔断为极端保护；
- 引入决策（远期）：若判别结论为支持引入，报告 §3.3 要求 dry-run 2 周且 KPI 改善
  才启用——启用前成本为"记录期成本"，启用后成本受同一预算护栏约束。

---

## 六、置信度限制（样本不足时的判定限制）

1. **最小判别基数**：judged < 5 时判别结论标记 `insufficient_data`，**绝不判命中**
   （与任务2 触发条件同款保守语义，防止小样本噪声驱动决策）；
2. **候选来源覆盖**：当前候选仅来自任务3 observe 预演（decision=preview）；
   confirm 提交（approved）候选数量有限时，判别结论对"真实提交场景"外推受限；
3. **规则通道同源性**：规则通道判定依赖候选记录中的既有结论（只读回放）；
   记录缺失的候选被诚实跳过（`no_rule_verdict`），不伪造规则判定；
4. **LLM 判定质量**：Judge 通道的 confidence 字段仅供审计参考，不参与判别；
   解析失败（`parse_failed`）的候选不计入有效判定，防止噪声；
5. **时间窗口**：2 周为最短采集窗口；若周候选基数长期不足（<5），判别结论保持
   `insufficient_data` 直至候选积累达标——宁可不判，不误判。

---

## 七、启用/不启用建议（明确结论）

**当前建议：不启用（`not_introduce`）。**

- **启用前置（全部满足才进入启用评估）**：
  ① 判别结论 = `hypothesis_a_eval_insufficient`（分歧率 ≥ 10% 且采纳率差异 ≥ +10pp）；
  ② 任务2 触发条件 TC-1 命中（KPI#7 连续 4 周 <5% 且 KPI#1 环比无提升，候选基数达标）；
  ③ 报告 §3.3 dry-run 2 周记录且 KPI 改善；
  ④ 决策层书面批准（远期里程碑门）。
- **不启用路径**：判别结论 = `insufficient_data`（当前）或 `hypothesis_b_candidate_quality`
  或 `inconclusive` → 不引入 Judge；其中假设 B 情形转向改进候选生成（`offline_evolver`
  / `feedback_agent` 变异/生成环节，另行立项）。
- **零成本守约**：本任务全程 dry-run，未触发任何"干预模式"；`LEARNING_JUDGE_ENABLED`
  保持 false 时零 LLM 调用（默认路径零新增成本，LLMMonitor 可断言）。

---

## 八、附件

- 判别计算：`python -m agent.learning.judge_channel --discriminate`（输出结论 + basis）；
- 报告数据快照：`python -m agent.learning.judge_channel --report`；
- 审计追溯：`data/learning/judge_audit.jsonl`（每条含 `intervention: false` +
  `judge_status` + 判定 + 成本）；
- 变更说明：`任务5_LLM-as-Judge双假设验证_变更说明.md`（实现、测试、不变式守约）。
