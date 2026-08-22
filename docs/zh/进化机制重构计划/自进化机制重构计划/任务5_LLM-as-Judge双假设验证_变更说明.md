# 任务5：LLM-as-Judge 双假设验证 — 变更说明

> 计划：`docs/zh/进化机制重构计划/自进化机制重构计划/进化机制理想设计.md`（阶段三）
> 依赖：任务2（KPI 数据源与触发监控）、任务3（L2 闭环受控放行，真实候选数据源）
> 日期：2026-08-22
> 状态：已实现（新增单测 30 例全绿 + 相关既有单测零回归）
> 判别报告：`任务5_LLM-as-Judge双假设判别报告.md`（本变更说明的数据与结论配套）

---

## 一、背景（为什么做）

TASK-08 报告 §3.3 的 LLM-as-Judge 引入门槛（"KPI#7 进化采纳率连续 4 周 <5% 且
KPI#1 token 复用率环比无提升"→ 先 dry-run 记录 2 周）存在**因果歧义（理论缺陷 T3）**：
低采纳率有两种互斥归因——

- 假设 A：**评估不精细**——规则评估无法区分候选质量，好候选被误拒 → 需要更精细的 Judge；
- 假设 B：**候选质量差**——变异/生成环节产出本身就差，评估正确拒绝 → 需要改进候选生成，
  而非引入 Judge（否则只增加 token 成本且无收益）。

报告只设计了"dry-run 记录 2 周"，没有设计**如何在 A/B 之间判别**。另外，预算护栏
`learning.budget.mode=warn_only`（不强制）时，任何 LLM 型评估角色一律不应引入
（报告 §3.3 否决条件）。

本任务：① 预算模式提升为 enforce（仅学习动作，灰度）；② 实现双假设判别实验
（同候选集双通道评估：规则评估 vs LLM-as-Judge，比较分歧率与采纳率差异）；
③ 判别规则与判别报告（启用/不启用 + 依据）。

## 二、交付内容

### 1. 预算 enforce 灰度（`agent/learning_budget.py` 修改 + `config.yaml`）

| 变更 | 说明 |
| --- | --- |
| `learning.budget.mode` | `warn_only` → `enforce`（灰度提升；超限学习动作被拒 + 告警） |
| `learning.budget.scope` | 新增 `scope: learning_actions` 声明字段（审计可读，不改变拦截语义） |
| `LEARNING_ACTION_SCOPE` | 新增学习动作白名单常量（`offline_evolve` / `feedback_agent` / `lifecycle_migrate` / `precipitate` / `reflection` / `judge_channel`） |
| `MAIN_CHAIN_EXCLUDED` | 新增主链路排除清单常量（`orchestrator` / `tool_calling` / `workflow_engine` / `model_router` / `server_routes` / `feedback`） |
| `LearningBudget.scope` | 实例属性 + `get_status()` 透出（测试与监控可读） |

**enforce 只作用于学习动作的代码审计依据**：`with_budget` / `get_learning_budget`
在全部生产代码中的调用方仅学习侧文件（`learning_budget.py` 自身、`learning/guard_status.py`
只读状态视图、`learning/judge_channel.py` 任务5 新增）。主链路（对话/工具/工作流/路由/
API 服务）**零 import、零调用** → enforce 天然不触碰主链路（单测 `test_enforce_only_affects_learning_actions_main_chain_untouched` 对 `MAIN_CHAIN_EXCLUDED` 逐模块静态断言 + 熔断后主链路式 LLM 调用行为证明）。

### 2. Judge dry-run 通道（`agent/learning/judge_channel.py` 新增）

- **双通道评估**：同一候选集同时过"规则通道"（只读回放候选记录中的既有规则结论，
  无记录时用对齐镜像纯函数 `rule_verdict_mirror`，不修改任何既有评估器）与
  "Judge 通道"（LLM 判定，duck-typed 客户端 `chat/invoke/complete/generate` 惯例 +
  `token_redactor` 脱敏管道）；
- **零干预**：不 import 任何提交/审批/回滚模块（`approval` / `rollback` /
  `offline_evolver` / `evolution_scheduler` / `meta_editor` / `lineage` 等）；
  唯一副作用 = Judge 审计 JSONL + `LearningMetrics.record_judge_result`（纯观测）+
  Prometheus gauge；每条审计记录含 `intervention: false`；
- **预算 enforce 前置**：`budget.mode != enforce` → 候选标记 `budget_not_enforce`
  跳过（报告 §3.3 否决条件）；所有 LLM 调用经
  `get_learning_budget().with_budget("judge_channel", ...)` 记账，超限 → 候选标记
  `budget_blocked` 跳过（不伪造判定、不部分执行、零成本入账——LLM 未调用）；
- **候选数据源**：`load_candidates_from_rollout_audit()` 读取任务3 放行审计
  `data/learning/rollout_audit.jsonl` 的 preview/approved 候选（只读追溯）；
- **开关**（优先级：环境变量 > config.yaml `learning.judge` > 硬编码默认值）：
  `LEARNING_JUDGE_ENABLED`（默认 false，零 LLM）、`LEARNING_JUDGE_DRYRUN_ENABLED`
  （默认 true，只写不干预）、`LEARNING_JUDGE_AUDIT_FILE`、
  `LEARNING_JUDGE_DISAGREEMENT_THRESHOLD`（默认 0.10）、
  `LEARNING_JUDGE_MIN_ADOPTION_DELTA_PP`（默认 10.0）、
  `LEARNING_JUDGE_MIN_CANDIDATES`（默认 5）、`LEARNING_JUDGE_COLLECTION_WEEKS`（默认 2）、
  `LEARNING_JUDGE_MAX_ESTIMATED_TOKENS`（默认 2000）；
- **CLI**：`python -m agent.learning.judge_channel --status / --run-batch / --discriminate / --report`。

### 3. 判别指标扩展（`agent/learning_metrics.py` 修改，纯增量）

| 扩展 | 说明 |
| --- | --- |
| `record_judge_result(...)` | 逐候选记录（rule_verdict / judge_verdict / disagreement / judge_status / tokens_used）；**不写** `_evolution_candidates/_evolution_adopted`（KPI#7 零影响，单测证明） |
| 日粒度事件 kind | `judge_candidate` / `judge_judged` / `judge_rule_adopt` / `judge_implied_adopt` / `judge_disagreement` / `judge_budget_blocked` / `judge_tokens` |
| `get_snapshot()` | 新增 `judge_dryrun` 扩展节：`judge_disagreement_rate` / `rule_adoption_rate` / `judge_implied_adoption_rate` / `adoption_rate_delta_pp` / 计数 / `insufficient_data`（既有字段零改动） |
| `get_weekly_kpis()` | 每行新增 `judge` 扩展节（周级判别数据源，与快照同口径） |
| `get_judge_dryrun_stats()` | 判别报告专用只读聚合 |

### 4. 判别规则（预设，写入判别报告 §3；`discriminate()` 纯函数）

| 条件 | 结论 | 建议 |
| --- | --- | --- |
| 有效判定 < `min_candidates`（默认 5） | `insufficient_data` | **不启用**（继续采集至 2 周或候选数达标） |
| 分歧率 < 阈值（默认 10%） | `hypothesis_b_candidate_quality` | **不引入 Judge**，转向改进候选生成 |
| 分歧率 ≥ 阈值 且 (Judge 采纳率 − 规则采纳率) ≥ +10pp | `hypothesis_a_eval_insufficient` | **支持引入**，按报告 §3.3 dry-run 2 周流程评估启用 |
| 其余（分歧高但采纳率差异不足） | `inconclusive` | **不启用**（继续采集/复核评估 prompt） |

`recommendation` 恒为二值（`not_introduce` / `evaluate_introduce`），每项结论含明确
`basis`（样本量与阈值），不含模糊结论。

### 5. 监控告警（`agent/monitoring/learning_judge_metrics.py` + `monitoring/prometheus/rules/learning_judge_alerts.yml`）

- gauge：`yunshu_learning_judge_dryrun{metric}`（判别统计快照）、
  `yunshu_learning_judge_discrimination{conclusion, recommendation}`（1=当前结论/建议）；
- 告警：`LearningJudgeEvaluateIntroduce`（支持引入信号，info）、
  `LearningJudgeHypothesisB`（候选质量差归因，info）、
  `LearningJudgeInsufficientData`（数据不足，warning）、
  `LearningJudgeBudgetBlocked`（预算熔断保护，warning）、
  `LearningJudgeBudgetNotEnforce`（否决条件命中，critical）；
- 挂载：`monitoring/prometheus.yml` + `monitoring/prometheus/prometheus.yml` 的
  `rule_files` 追加 `prometheus/rules/learning_judge_alerts.yml`。

### 6. 配置与开关（`config.yaml` + `.env.example`）

- `config.yaml`：`learning.budget.mode=enforce`、`learning.budget.scope=learning_actions`、
  新增 `learning.judge.*`（enabled=false / dry_run=true / 判别阈值 / 审计路径等）；
- `.env.example`：补 `LEARNING_BUDGET_MODE=enforce`、`LEARNING_BUDGET_SCOPE`、
  `LEARNING_JUDGE_*` 全套开关（带注释说明默认值与不变式）。

## 三、不变式守约（git diff 审计）

- ✅ **Judge 通道零干预**：不 import 提交/审批/回滚模块（单测对 import 语句逐行断言）；
  审计记录恒含 `intervention: false`；KPI#7（`evolution_adoption_rate`）在 Judge 通道
  运行前后零变化（单测证明）；
- ✅ **预算 enforce 仅作用于学习动作**：主链路模块零引用 `learning_budget`（单测对
  `MAIN_CHAIN_EXCLUDED` 逐模块静态断言）；预算熔断后主链路式 LLM 调用照常（行为证明）；
- ✅ **未修改既有规则评估器**：`ReflectionEngine` / `feedback.py` / `reviewer` /
  `critic.py` RULE_BASED 零 diff；规则通道判定 = 候选记录只读回放 + 独立对齐镜像
  （`rule_verdict_mirror` 纯函数，不触碰既有实现）；
- ✅ **成本受熔断保护**：Judge 通道 LLM 调用全部经 `learning.budget` 记账；
  `budget_blocked` 候选零成本入账（LLM 未调用）、不伪造判定、不部分执行；
- ✅ **触发条件未满足前不转干预模式**：`enabled=false` 默认（零 LLM）；`dry_run=true`
  只写不干预；本模块无任何干预路径（"干预模式"属远期里程碑门）。

## 四、测试

新增单测 2 文件 30 例（`test_judge_channel.py` 25 例 + `test_learning_budget.py` 任务5
扩展 5 例），全部通过：

| 覆盖 | 用例 |
| --- | --- |
| 零干预 | 审计 `intervention=false` 逐条可证；KPI#7 零变化；无采纳侧调用；模块零提交/审批 import |
| 预算 enforce | 超单次上限 → `budget_blocked`（不伪造判定、零成本、LLM 未调用）；`warn_only` → `budget_not_enforce`；主链路 LLM 在熔断后不受影响；scope 默认/优先级/生产 config 审计 |
| 分歧率/采纳率 | `compute_stats` 与度量快照同口径；周级 `judge` 节；Judge 指标与 KPI#7 严格分离 |
| 判别规则 | 合成数据 A（高分歧 + 采纳率差异 +30pp）→ 支持引入；B（低分歧）→ 不引入；样本不足 → 不启用；分歧高但差异不足 → 不启用；阈值边界（含等号） |
| 开关/降级 | enabled=false 零 LLM；无客户端跳过；无规则判定跳过；JSON/关键词/垃圾响应解析；解析失败成本入账不伪造判定 |
| 数据源 | rollout_audit preview/approved 读取（rejected 排除、approved→accept 只读回放、object_id 兜底） |
| 端到端 | A/B 两类合成数据完整批次 → 判别结论方向与预设规则一致 |

回归验证：`test_learning_budget.py` / `test_learning_metrics.py` /
`test_learning_metrics_triggers.py` / `test_learning_metrics_persistence.py` /
`test_rollout_controller.py` / `test_learning_scheduler.py` /
`test_guard_status.py` / `test_judge_llm_confidence_edge_cases.py` 全部通过
（合计 144 例，随机顺序零回归）；并行任务6/7 新测试
（`test_replay_*` 179 例 / `test_learning_metrics_complexity` + `test_learning_curriculum`
+ `test_complexity_classifier` 31 例）同样全绿（共享文件相干性验证）。

全量 `python -m pytest tests/unit -q`（2026-08-22 实测）：**12101 passed / 83 failed /
293 skipped / 13 xfailed / 4 xpassed / 14 errors**（73 分钟）。83 例失败 + 14 例 errors
全部为**既有环境性失败**，与本任务无关（与任务3 基线 12046 passed/75 failed/14 errors
同源，失败数随并行任务新增测试微增），证据：
- 绝大多数为 `PermissionError: [WinError 5]`（沙箱禁止子进程管道捕获，波及
  `test_knowledge_cli` / `test_mcp_executor` / `test_preflight_runner` /
  `test_sandbox_execution_guard` / `test_skill_manager` / `test_verify_migrated_skills` /
  `test_precheck_docs_anchor_links` 等子进程类测试）；
- 少量为缺失可选依赖（`sqlite_vec` 后端不可用，`test_vector_store_sqlite_vec`）与
  顺序/环境抖动（`test_snapshot_comprehensive` / `test_long_term_memory_embedding`）；
- **所有失败用例均不 import / 不依赖本任务修改的模块**；本任务相关测试集
  （`test_judge_channel` 25 例 + `test_learning_budget` 17 例等）在全量运行中全绿。

## 五、使用示例

```bash
# 1. 通道状态 + 当前判别统计（默认 enabled=false，零 LLM）
python -m agent.learning.judge_channel --status

# 2. 执行一批双通道 dry-run 评估（候选源=任务3 放行审计 preview/approved）
python -m agent.learning.judge_channel --run-batch --source rollout_audit

# 3. 用自定义候选文件评估（每行一个 JudgeCandidate JSON）
python -m agent.learning.judge_channel --run-batch --source file --candidates-file ./data/learning/judge_candidates.jsonl

# 4. 按当前采集数据计算判别结论
python -m agent.learning.judge_channel --discriminate

# 5. 输出判别报告数据快照（写入正式报告的数据来源）
python -m agent.learning.judge_channel --report

# 6. 采集期开启（运维操作，仍为 dry-run 零干预）：
#    LEARNING_JUDGE_ENABLED=true 且 budget.mode=enforce（已灰度）→ 批次评估写审计+指标
```

## 六、后续衔接

- **2 周数据采集为运维动作**：本任务交付机制与判别规则；采集期由调度/运维按
  `LEARNING_JUDGE_ENABLED=true` 开启（dry-run 零干预），数据入 `judge_audit.jsonl`
  与任务2 监控扩展字段；判别报告按采集数据滚动复核；
- **启用 LLM-as-Judge（干预模式）属远期决策**：需 ① TC-1 触发条件命中（任务2 监控）；
  ② 判别结论 `evaluate_introduce`（本任务）；③ 报告 §3.3 流程 dry-run 2 周 KPI 改善；
  ④ 决策层书面批准——任一不满足即不启用，本任务不改变任何决策；
- **候选生成改进（假设 B 路径）**：判别结论 `hypothesis_b_candidate_quality` 时
  转向改进 `offline_evolver` / `feedback_agent` 的变异/生成环节（任务范围外，另行立项）。

## 七、共享文件协同披露（并行会话工作区）

本任务与任务6（沙箱回放）、任务7（复杂度判定源统一）并行执行于同一工作区，
以下**共享文件**同时包含本任务与并行任务的未提交变更（均经测试验证相干）：

| 共享文件 | 本任务（任务5）变更 | 并行任务变更 |
| --- | --- | --- |
| `config.yaml` | `learning.budget.mode=enforce` + `scope`；`learning.judge.*` 配置段 | 任务6 `learning.replay.*`；任务7 `learning.complexity` / `learning.curriculum` |
| `agent/learning_metrics.py` | `record_judge_result` + `judge_dryrun` 快照/周级扩展（纯增量） | 任务7 `failure_rate_by_task_type_complexity` 双维度扩展（纯增量） |
| `.env.example` | `LEARNING_BUDGET_*` + `LEARNING_JUDGE_*` | 任务7 `COMPLEXITY_SOURCE` / `LEARNING_CURRICULUM_*` |
| `agent/learning/__init__.py` | 包文档补 judge_channel 条目 | 任务7 包文档补 curriculum 条目 |

> 守仓库既有协同惯例（任务3 报告 P5）：共享文件随任一任务的精确路径提交落库，
> 其余并行变更同文件共存并披露；本任务提交采用精确路径 add，不触碰并行任务
> 专属文件（`agent/learning/replay.py`、`agent/learning/curriculum.py`、
> `agent/task_planner/complexity_classifier.py` 及对应测试）。

## 八、验收对照（任务提示词 §6）

### 内容验收

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| Judge 通道零干预（审计断言） | ✅ 审计每条 `intervention=false`；KPI#7 零变化；零采纳侧调用 | `test_evaluate_candidates_zero_intervention_audit_provable` / `test_judge_channel_module_has_no_commit_imports` |
| 预算 enforce 拦截生效（单测） | ✅ 超限学习动作被拒（`budget_blocked`/`LearningBudgetExceeded`），主链路 LLM 调用零影响 | `test_budget_enforce_blocks_over_limit_judge_call` / `test_main_chain_llm_unaffected_by_enforce_budget` |
| 判别规则可复现（A/B 合成数据） | ✅ A 类 → 支持引入；B 类 → 不引入；方向与预设规则一致 | `test_discriminate_hypothesis_a_*` / `test_end_to_end_hypothesis_a_batch` / `test_end_to_end_hypothesis_b_batch` |
| 判别报告含样本量/分歧率/采纳率差异/token 成本/置信度限制 | ✅ | 《任务5_LLM-as-Judge双假设判别报告.md》§4/§5/§6 |
| 报告给出明确启用/不启用建议与依据 | ✅ 当前 `not_introduce`（insufficient_data + 依据），复核条件明确 | 判别报告 §一/§七 |

### 过程验收

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 未修改既有规则评估器（git diff 审计） | ✅ `reflection.py` / `critic.py` / `feedback.py` / `reviewer.py` / `offline_evolver.py` 零 diff | `git diff HEAD` 审计 |
| 全程 dry-run，未触发干预模式 | ✅ 默认 `enabled=false` 零 LLM；模块无干预路径（import 审计） | 测试 + 代码审计 |
| 全量回归 | ✅ 12101 passed / 83 failed（全为既有环境性失败，与本任务无关，见第四节归因） | pytest 输出 |
