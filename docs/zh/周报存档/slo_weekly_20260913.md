# §6.7 指标周报（SLO）

- 窗口：2026-09-07 ~ 2026-09-13（7 天）
- 生成时间：2026-09-13T01:05:14+0800
- 事件流：(默认 data/events)（2499 条）
- 口径纪律：每个数字都带数据源与公式；数据源缺位记 `None`（不以 0 冒充）；样本 < 20 只披露不考核

| 指标 | 值 | 单位 | 目标 | 达标 | 样本 | 状态 | 数据源 | 公式 |
|---|---|---|---|---|---|---|---|---|
| 消化吞吐 | 1.0000 | 能力/周 | ≥2/周（W10 起） | 未达标 | 114 | ok | agent.observability.events（EV_DIGEST_STAGE，S3-01/S3-02 写入） | count(distinct capability_id | digest.stage.applied=True 且 to_stage='mirrored') × 7 / 窗口天数 |
| 内化转化率 | 0.0000 | 比率 | ≥10% | 未达标 | 32 | ok | agent.descriptors.registry.DescriptorRegistry（capability 台账 stage 字段） | (n_internalized + n_native) / n_total |
| 委派回收率 | — | 比率 | 100% | — | 0 | framework_only | 委派行契约（见 delegation_recovery_contract()）：产物/轨迹/反思三源齐全性 + 复评半边的 signal_kind（R4） | 两列分别计算：count(三件套齐全 ∧ signal_kind=k) / count(signal_kind=k)（k ∈ {mechanical, llm}）；**跨列不得合并** |
| 委派成功率 | — | 比率 | 披露不考核（样本 < 20 只披露） | — | 0 | framework_only | 委派行契约的 `review_passed`（或 reflection.cloudpivot_review.passed）+ `signal_kind`（R4） | 两列分别计算：count(review_passed=True ∧ signal_kind=k) / count(已判定 ∧ signal_kind=k)（k ∈ {mechanical, llm}）；**跨列不得合并** |
| 技能成功率 | — | 比率 | ≥上游 × 0.98 | — | 0 | source_unavailable | agent.digestion.shadow.ShadowLedger.rows()（S3-03 交付） | Σpassed / Σsampled（ShadowLedger 行）；上游基准 = 上游臂通过率 |
| MTTD / MTTR | — | ms | <3s / <30s | — | 0 | framework_only | agent.observability.events（EV_HEALING_TRIGGERED，S4-03 归口的发射方） | median(healing.triggered.mttd_ms) / median(healing.triggered.mttr_ms) |
| 审批衰减率 | 0.0962 | 比率 | ≥70% | 未达标 | 468 | ok | agent.observability.events（EV_APPROVAL，S2-03 埋点） | count(approval.kind='auto_pass') / count(approval) |
| 委派中位周期 | — | 天 | ≤14 天 | — | 0 | source_unavailable | agent.observability.events（EV_DIGEST_STAGE 的 stage 迁移时间戳） | median(internalized.ts − borrowed.ts)（按 capability_id 配对） |
| 弃单率 | 0.0000 | 比率 | 披露不考核 | — | 120 | ok | agent.observability.events（EV_TASK_CLOSED / EV_TASK_ABANDONED） | count(task.abandoned) / count(task.closed + task.abandoned) |
| 路由准确率 | — | 比率 | >85%（W10 起） | — | 0 | framework_only | 事后标注行契约（人工裁定 / 结果回测，见 routing_annotation_contract()） | count(router_choice == hindsight_best) / count(已标注决策) |
| 探索满意度（S2-03 遗留 #4 正式口径） | — | 比率 | 披露不考核 | — | 0 | source_unavailable | 👍率：agent.feedback.FeedbackManager.get_feedback_summary()；闭环率：acr.acr_summary()['exploration'] | 👍率 = like/(like+dislike)；闭环率 = closed/(closed+failed) |
| UTC（单位任务成本，辅助指标） | 0.8204 | cents/任务 | 由 S5-03 依 §6.3 阶段阈值设定 | — | 120 | ok | agent.observability.utc.utc_window()（S2-03 交付，与 S5-03 同源） | cost_normalized_cents / (closed + failed) |

## 披露与未达标数据源

- **委派回收率**（`delegation_recovery`，status=framework_only）：委派链路**已交付**（S4-04：八要素契约 + CLI 物理通道 + 回收三件套 + 真执行器，端到端样例 `scripts/demo_s4_04_delegation.py`），但生产侧**尚无委派调用点**（`agent/subagent/lifecycle.py::SubagentLifecycleManager.delegate()` 目前只被样例/测试驱动）⇒ 判定集内没有真实委派行，framework_only 源于**样本缺位**，**不是链路缺位**；**R4 起按判定主体分两列披露**：机械列（①产物结构/②测试 fail→pass/③副作用核对/④可回放性）与 LLM 兜底列**严禁混算** —— 把 LLM 判定的成功算进机械成功率等于谎报证据强度；行未标 signal_kind 时单独记`unlabeled` 列，不并入任一列
- **委派成功率**（`delegation_success_rate`，status=framework_only）：机械信号清单见 `delegation_signal_dictionary()`（单一事实源在 `agent/subagent/mechanical.py`）；两列**严禁混算**，且行未标 signal_kind 时记 `unlabeled` 列（不当机械、也不当 LLM）
- **技能成功率**（`skill_success_rate`，status=source_unavailable）：灰度台账为空（无 shadow 运行记录）
- **MTTD / MTTR**（`healing_latency`，status=framework_only）：`healing.triggered` 的**发射方已交付**：`agent/self_healing/levels.py::emit_healing_triggered`（被 Saga / 发布包调用），另有直接发射点（成本熔断 `agent/monitoring/cost_brake.py`、修复流水线 `agent/repair/pipeline.py`）；事件类型亦已登记（events.v1）。当前窗口内 0 条事件 ⇒ framework_only 源于**尚未发生被分级记录的自愈事件**，**不是缺发射方**；缺数据时返回 framework_only，不填 0
- **委派中位周期**（`delegation_cycle_days`，status=source_unavailable）：窗口内无成对的 borrowed→internalized 迁移
- **路由准确率**（`routing_accuracy`，status=framework_only）：事后最优尚无标注源（设计文档要求人工/结果回测标注）；本任务交付标注契约与计算函数，未标注时返回 framework_only
- **探索满意度（S2-03 遗留 #4 正式口径）**（`exploration_satisfaction`，status=source_unavailable）：闭环率是**代理口径**（原实现），👍率是直接信号；二者并列披露，替代关系为「👍率为主 + 闭环率兜底」，任一口径都不得单独用于考核
- **UTC（单位任务成本，辅助指标）**（`utc_cents_per_task`，status=ok）：本指标非 §6.7 条目，作为 UTC 刹车（S5-03）的数据源透传展示

## 探索满意度（双列口径）

- 👍率 = None（0/0，源 agent.feedback）
- 任务闭环率 = None（源 closure_success_ratio（关闭成功 / (关闭成功+失败)））
- 替代关系：正式口径 = 👍率（直接信号，源 agent.feedback）与任务闭环率（代理指标，源 acr.exploration）**并列双列**；闭环率单独使用会把「没被投诉的沉默放弃」算成满意，故不得单独用于考核
- 考核范围：explore/consult 任务排除在 ACR 分母之外；本指标披露不考核（P7.1-16）

## 委派指标（机械 / LLM **两列，严禁混算**，R4）

**委派回收率**（顶层 value=None，basis=—，mixed=False）：

| 判定主体 | 样本 | recovery_rate | 状态 | 说明 |
|---|---|---|---|---|
| mechanical | 0 | None | framework_only | — |
| llm | 0 | None | framework_only | — |
| unlabeled | 0 | None | framework_only | — |

**委派成功率**（顶层 value=None，basis=—，mixed=False）：

| 判定主体 | 样本 | success_rate | 状态 | 说明 |
|---|---|---|---|---|
| mechanical | 0 | None | framework_only | — |
| llm | 0 | None | framework_only | — |
| unlabeled | 0 | None | framework_only | — |

- 口径规则：两列分别计算，跨列不合并
- 机械信号清单（单一事实源 `agent/subagent/mechanical.py`）：
  - 1. `artifact_structure`（mechanical）：产物是否**结构性通过** task_file ⑤产物的格式声明（schema / 必需字段）
  - 2. `test_transition`（mechanical）：涉代码的委派：目标用例是否从 fail 变为 pass（可执行的直接证据）
  - 3. `side_effects`（mechanical）：**声明副作用 vs 实际副作用**集合是否一致
  - 4. `replayability`（mechanical）：产物能否**重放复现**（同一输入同一结果）
  - 5. `llm_review`（llm）：①-④ 均不适用时由云枢侧复评器给出的判定（ReflectionEngine / 规则复评）
