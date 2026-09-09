# TASK-S2-03 events 信封化 + ACR/UTC 埋点承接

> 所属阶段：S2 数据与可观测层｜依赖：S2-01（Trace）、S2-02（审计）｜预估：3–5 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §3.6（events.v1 信封 + 九事件）/§6.1-6.6（ACR 北极星 / UTC 刹车 / 埋点清单）/P7.1-18（model.degraded）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

把 v7.2 的**事件流（events.v1 信封）与 ACR/UTC 度量埋点**落地为云枢后续治理（审批衰减率、断食、评测、面板）的统一数据源：

1. **events.v1 信封**：事件统一为 `{v, event_id 幂等键, ts, correlation_id, actor, type, payload}`；覆盖 §3.6 八事件 + model.degraded（P7.1-18 第 9 事件）。现状云枢事件是分散 JSONL（skills_assessment_events / approval_records / task_history 等），本任务提供**统一事件出口**，逐步收敛而非一次性替换全部。
2. **ACR 埋点**（§6.1-6.6）：介入计数口径——Approve=1 / 条件=0.5 / 自动放行=0 / 超时 Deny=2 / 重跑=1 / 逃逸=1 / 查看=0；分母 = closed(成功)+failed，排除 explore/consult；explore/consult 另计探索满意度（P7.1-16）。
3. **UTC 成本埋点**（§6.2/§6.6 cost）：task_id/workspace_id/subject_id/model/tokens_in/out/retries/shadow_overhead/cents；缓存命中不计 token（P7.1-18）；口径归一（T6 修正：以主力模型为锚 + 换算系数）。
4. **双轨接线**：task.closed / approval / escape / cost 埋点挂到真实发生点（会话任务完成、审批 submit/approve/reject/超时、用户手工编辑任务文件=逃逸、LLM 调用记账）。

## 二、执行步骤

### 步骤 1：事件信封与出口
- 新增 `agent/observability/events.py`（或 audit 旁挂）：`EventEnvelope`（字段对齐 §3.6）、`emit(type, payload, *, actor, correlation_id)`（幂等键 event_id 生成；写入统一事件 JSONL 或 SQLite，遵循 S2-02 单写者纪律与存档逻辑 `log_archiver.py`）。
- 事件类型枚举：tool.called / digest.stage / skill.generated / approval.required / healing.triggered / metrics.delta / policy.denied / backup.health / model.degraded（事件名与 UI 面板、审计写入联动——§13.3 联动表）。
- **不强制迁移存量事件文件**：本任务先落"新事件出口 + 核心事件接入"，存量文件保持只读（S2-02 归档轨处理）；skills_assessment_events 等经 S0-01 更名后的事件名保持一致。

### 步骤 2：ACR 埋点
- 定义 `intervention` 事件与计数口径（§6.1）：接入审批流（`skills_mgmt/approval.py` ApprovalFlow.submit/approve/reject + 超时路径）与任务模型（session/chat 任务 closed/failed）。
- 落地字段：task.closed {intent, difficulty, intervened, intervention_kind}；approval {kind, latency_ms, fatigue_bucket, actor}；escape {task_id, reason, capability_id}。
- 计算 ACR 汇总视图（按日/周）：分母规则（closed+failed，排除 explore/consult）与探索满意度（P7.1-16）单列。

### 步骤 3：UTC 成本埋点与归一
- 在 LLM 调用记账点（现有 LLMMonitor / cost 记录）扩展：task_id/workspace_id 注入（来自 S2-01 TraceContext）、shadow_overhead 字段、retries、缓存命中标记（P7.1-18 命中不计 token 成本）。
- 成本归一公式化（T6 修正）：token 成本按主力模型锚价换算 + 系数表（模型 × 计价），产出 `cost_normalized_cents`；写指标字典（§6.7 口径表）。
- 接入既有 `verify_budget_break.py`/成本监控脚本的数据源，输出日/周 UTC 与成本聚合（供 S5-03 断食/刹车消费）。

### 步骤 4：模型降级事件（P7.1-18）
- 在模型降级链（若有 fallback 逻辑）或错误处理（§11.6.0 E_MODEL_DEGRADED）处 emit `model.degraded {from, to, reason}`。

### 步骤 5：回归与归档
- 回归：会话任务/审批/LLM 调用相关既有套件零回归；新增单测（信封字段/幂等/ACR 口径含分母排除/UTC 归一/降级事件）≥30 例、覆盖率 ≥80%。
- 撰写 `TASK-S2-03_验收报告.md`（含一次真实任务链的 ACR+UTC 样例输出）。

## 三、预期成果

1. `agent/observability/events.py`：EventEnvelope + emit + 事件类型枚举。
2. ACR 埋点接入（审批流 + 任务模型）与汇总视图函数。
3. UTC/成本埋点扩展与归一公式；日/周聚合输出。
4. model.degraded 事件接线。
5. `TASK-S2-03_验收报告.md`（含样例 ACR/UTC 快照）。

## 四、评估标准（验收清单）

- [ ] EventEnvelope 字段对齐 §3.6（v/event_id/ts/correlation_id/actor/type/payload）；event_id 幂等（重放不重复计数）
- [ ] ACR 计数口径精确（Approve=1/条件=0.5/超时 Deny=2/重跑=1/逃逸=1/查看=0）；分母排除 explore/consult，探索满意度单列
- [ ] cost 埋点含 task_id/workspace_id/shadow_overhead/retries/缓存命中标记；缓存命中不计 token 成本
- [ ] 成本归一公式可复现（主力模型锚价 + 系数表）；与既有成本监控数据源一致
- [ ] model.degraded 事件在降级/错误路径触发且有 from/to/reason
- [ ] 既有会话/审批/LLM 套件零回归；新增单测全绿、覆盖率 ≥80%
- [ ] 真实任务链可产出 ACR+UTC 样例（验收报告含快照）
