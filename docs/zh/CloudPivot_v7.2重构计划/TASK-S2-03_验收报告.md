# TASK-S2-03 验收报告 — events.v1 信封化 + ACR/UTC 埋点承接

> 归档日期：2026-09-10
> 所属计划：CloudPivot v7.2 重构计划（S2 数据与可观测层 · 第三任务）
> 任务：[TASK-S2-03_事件与ACR埋点.md](TASK-S2-03_事件与ACR埋点.md)
> 依赖输入：TASK-S2-01（统一 Trace / TraceContext / UnifiedTrace.cost.*）、
> TASK-S2-02（链式审计 + 单写者纪律 + 存量归档轨）
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §3.6（events.v1 信封 + 九事件）/
> §6.1–6.6（ACR 北极星 / UTC / 埋点清单）/ §6.7（指标字典）/ §7（断食阈值，供 S5-03 消费）/
> P7.1-18（缓存命中不计 token / model.degraded 第 9 事件）/ T5·T6（审计报告已记录的
> 启发式与口径一致性缺陷）/ §3.8（任务生命周期 abandoned/逃逸）
> 状态：✅ 验收通过（验收清单 7/7 核验，见 §三）

---

## 一、执行摘要

1. **events.v1 信封与统一事件出口交付**：新增 `agent/observability/events.py`（1003 行）——
   `EventEnvelope`（**字段逐字对齐 §3.6**：`v / event_id / ts / correlation_id / actor /
   type / payload`，`to_dict()` 键集合即该 7 字段）、`EventStore`（追加写 JSONL +
   幂等去重 + 跨日归档 + 审计镜像）、模块级 `emit(type, payload, *, actor,
   correlation_id)`、读取/聚合基元（`iter_events` / `read_events` / `group_by_day`）。
2. **九事件 + §6.6 埋点清单枚举齐备**：`EventType`（`str` 混入枚举，15 个成员）——
   §3.6 八事件（`tool.called` / `digest.stage` / `skill.generated` / `approval.required` /
   `healing.triggered` / `metrics.delta` / `policy.denied` / `backup.health`）
   + P7.1-18 第 9 事件（`model.degraded`）+ §6.6 ACR/UTC 埋点（`task.closed` /
   `task.abandoned` / `approval` / `escape` / `intervention` / `cost`）。
3. **event_id 幂等（重放不重复计数）双保险**：`build_event_id()` 由
   「type + actor + correlation_id + 幂等键/载荷规范化 JSON」**确定性派生**
   （`ev_` + sha256 前 32 位）；**写入端**按 id 去重（重复发射不落盘、计数
   `duplicate_count`），**读取端**跨分片按 id 去重（重放/重复摄取不重复计数）。
   实测：21 条事件内容级重放 **折叠 21/21、新增 0 条**（§六.3 [6]）。
4. **ACR 埋点与汇总视图交付**：新增 `agent/observability/acr.py`（712 行）——
   §6.1 七项口径**逐项硬编码可断言**（Approve=1 / 条件=0.5 / 自动放行=0 /
   超时 Deny=2 / 重跑=1 / 逃逸=1 / 查看=0）；分母 = `closed + failed`
   **排除 explore/consult**（其介入同口径从分子排除）；`abandoned`（§3.8）单列；
   探索满意度（P7.1-16）单列；日 / ISO 周 / 窗口三视图 + `acr_snapshot.json`。
5. **UTC 成本埋点与口径归一交付**：新增 `agent/observability/utc.py`（651 行）——
   §6.6 字段齐备（task_id / workspace_id / subject_id / model / tokens_in / tokens_out /
   retries / shadow_overhead / cents + cache_hit / cost_raw_cents /
   cost_normalized_cents / anchor_model / coefficient_*）；**缓存命中不计 token**
   （P7.1-18）；归一公式 = 「锚模型价格 × 系数表」，默认系数取**价格比** ⇒
   与既有成本监控口径（`agent.model_router.cost_tracker.MODEL_COSTS`）**逐分一致**
   （`reconcile_pricing()` 4/4 模型 delta=0.000000）；日 / 周 / 快照（含近 7 日滚动
   基线与 ratio，供 S5-03 §7 断食消费）+ `utc_snapshot.json`。
6. **逃逸检测交付**：新增 `agent/observability/escape.py`（403 行）——受控写台账
   （`data/events/governed_writes.jsonl`）+ 指纹比对；首次见到存量文件**只登记基线
   不误报**；内容变更且未经受控写 ⇒ `escape {task_id, reason, capability_id,
   digest_before/after}` + `intervention{kind=escape, weight=1}`；能定位
   **被改的任务 id**（`changed_task_ids`）；检测过程**只读**，绝不回写用户改动。
7. **model.degraded 接线交付**：新增 `agent/observability/model_degrade.py`（286 行）——
   归一错误码 `E_MODEL_DEGRADED`（§11.6.0）、降级链解析（env > config.yaml >
   文档化默认链）、`report_model_degraded()`（**总是**发射 `{from, to, reason}`）、
   `handle_primary_failure()`（主模型失败收口）、`call_with_model_fallback()`
   （逐候选真实降级）。实测触发点 3 处：LLM 调用失败（`LLMMonitor`）、
   `LLMService.chat/summarize` 重试耗尽、`graceful_degrade._trigger_degrade`
   （模型类组件）。
8. **双轨接线（四个真实发生点）全部接通**（均 best-effort，绝不阻断主路径）：
   - **会话任务完成** → `task.closed`（`orchestrator.process()` 的 LLM 段
     `try/finally` 收口点，含成功与异常早退）；
   - **审批 submit / approve / 条件通过 / reject / 超时** →
     `approval.required` + `approval`（kind/latency_ms/fatigue_bucket）+ `intervention`；
     并**新建超时路径** `ApprovalFlow.expire_pending()`（§6.1「超时 Deny=2」此前
     在云枢**没有任何真实发生点**）；
   - **用户手工编辑任务文件** → `escape`（`agent/tools/task_tools.py` 受控写登记 +
     读取时检测）；
   - **LLM 调用记账** → `cost`（`LLMMonitor.record()` 唯一漏斗，含 task_id/workspace_id/
     subject_id 从 S2-01 TraceContext 注入）。
   另接通：人工重跑（`TaskScheduler.execute_now` → `rerun=1`）、人工查看待审批项
   （`SkillsManagementService.list_pending_approvals` → `view=0`）。
9. **零回归 + 新增全绿**：新增 6 套件 **218 例全绿**；与既有 29 个相关套件同进程一次跑
   **1755 passed / 0 failed / 268 skipped / 1 xfailed**（跳过与 xfail 均为既有标记）；
   `tests/unit` **全量扫描 13033 passed / 0 failed**（§六.5）。**CI 架构规则
   `no_circular_dependency` 从 ❌ 转为 ✅**（实现期发现并消除了一处新引入的循环依赖，见 §五 D11）。
10. **新增模块覆盖率 83%–92%**（branch=True，仅跑本任务 6 套件）：
    `events.py 85%` / `acr.py 92%` / `utc.py 83%` / `escape.py 90%` /
    `model_degrade.py 91%`（**逐个 ≥80%**，均值 88.2%）；
    **实现期实测修复 5 个真实缺陷**（4 个产品缺陷 + 1 处测试互扰，见 §五 补记二），
    均已加回归测试。

---

## 二、预期成果对照（任务 §三）

| # | 预期成果 | 交付 | 验收 |
|---|---|---|---|
| 1 | `agent/observability/events.py`：EventEnvelope + emit + 事件类型枚举 | ✅ 1003 行：`EventEnvelope` / `EventType`(15) / `EventStore` / `build_event_id` / `sanitize_payload` / `emit` / `iter_events` / 单写者登记 / 归档 / 审计镜像 | ✅ |
| 2 | ACR 埋点接入（审批流 + 任务模型）与汇总视图函数 | ✅ `acr.py` 712 行：§6.1 口径表 + `record_intervention/approval/escape/task_closed/task_abandoned` + `acr_summary/acr_daily/acr_weekly/acr_snapshot`；审批流 6 处接线 + orchestrator 收口点接线 | ✅ |
| 3 | UTC/成本埋点扩展与归一公式；日/周聚合输出 | ✅ `utc.py` 651 行：`normalize_cost` / `record_cost` / `utc_daily/weekly/window/snapshot` / `coefficient_table` / `reconcile_pricing` / `reconcile_cost_log`；`LLMMonitor` 接线 | ✅ |
| 4 | model.degraded 事件接线 | ✅ `model_degrade.py` 286 行 + 3 处真实触发点（LLM 失败 / 重试耗尽 / 组件降级） | ✅ |
| 5 | `TASK-S2-03_验收报告.md`（含样例 ACR/UTC 快照） | ✅ 本文件（§六.3 为实跑快照） | ✅ |

---

## 三、验收清单逐条核验（任务 §四）

### ✅ 1. EventEnvelope 字段对齐 §3.6；event_id 幂等（重放不重复计数）

- **字段**：`EventEnvelope.to_dict()` 的键**集合相等**断言
  （`test_dict_keys_are_exactly_spec_fields`：`tuple(keys) == ENVELOPE_FIELDS`
  且 `set(keys) == {v, event_id, ts, correlation_id, actor, type, payload}`）；
  实测样例（演示 [5]）：

```json
{
  "v": 1,
  "event_id": "ev_6b05ef64b93548a67f7ecef2a0260c7c",
  "ts": "2026-09-10T23:29:08.231+08:00",
  "correlation_id": "936c2de43bd84995",
  "actor": "auto",
  "type": "task.closed",
  "payload": { "task_id": "demo-task-1", "workspace_id": "ws_demo_s203",
               "intent": "build", "difficulty": "easy", "status": "closed",
               "intervened": true, "intervention_kind": "rerun", … }
}
```

- **`v` 语义裁定（D1）**：`v = 1`（int，信封版本号），schema 名常量
  `SCHEMA_NAME = "events.v1"`；`EventEnvelope.v` 与 `SCHEMA_VERSION` 同源，
  不重复冗余字段。
- **`ts` 语义（D2）**：ISO-8601 **带本地时区偏移 + 毫秒**
  （`2026-09-10T23:29:08.231+08:00`）——时间点无歧义，且 `ts[:10]` 即**本地日历日**，
  与云枢存量事件文件（`approval_records.jsonl` / `task_history.jsonl` 的本地 naive ISO）
  以及 ACR/UTC「按日/按周」口径一致。
- **幂等**：
  - 确定性派生：`build_event_id()` 同内容同 id、键序无关、内容变化即变化、
    显式 `idempotency_key` 时载荷微差不影响 id（4 例断言）；
  - 写入端：`EventStore.emit()` 重复 → 返回 `None` + `duplicate_count += 1`
    （`test_replay_not_written_twice`）；
  - 进程重启后仍幂等：构造新 store 会从活动文件**重建幂等索引**
    （`test_index_survives_restart`）；
  - 读取端：跨日分片按 `event_id` 去重（`test_reader_dedupes_across_shards`）。
  - **实跑**（演示 [6]）：`重放前 21 条 → 重放后 21 条（内容级重放折叠 21/21 条，
    新增 0 条）`。
- **载荷纪律**：事件载荷只放标识/标签/计数；`sanitize_payload()` 丢弃敏感键
  （`api_key`/`access_token`/`password`/`private_key_path`… 8 例）、截断超长字符串，
  且**不对 hex 摘要做脱敏启发式**（S2-02 裁定 D6 的实测教训：脱敏会把 24 位 hex
  部分掩码，导致关联键无法对齐），并有**度量字段白名单**保证 §6.6 cost schema
  不被启发式误伤（见 §五 补记二 ①）。

### ✅ 2. ACR 计数口径精确；分母排除 explore/consult；探索满意度单列

- **七项口径逐项断言**（`test_seven_core_weights_exact`）：

```
approve = 1.0    conditional = 0.5   auto_pass = 0.0   timeout_deny = 2.0
rerun   = 1.0    escape      = 1.0   view      = 0.0
```

- **分母规则**：`分母 = closed(成功) + failed`（`DENOMINATOR_STATUSES`）；
  **explore / consult 任务整体排除**（`EXCLUDED_INTENTS`），其介入**同口径从分子排除**；
  `abandoned`（§3.8）**不计入分母**，单列披露。
  固定场景断言（`_seed_window`：3 fix〔2 成功 1 失败〕+ 1 explore + 1 consult
  + 1 abandoned；介入 approve(1) + timeout_deny(2) 在 fix、approve(1) 在 explore）：

```
denominator = {closed: 2, failed: 1, total: 3}     # explore/consult/abandoned 不在内
excluded    = {intents: [explore, consult], tasks: 2}
abandoned   = 1
acr_numerator = 3.0     # 1 + 2（explore 的 approve 被排除）
ACR = 1.0
```

- **探索满意度（P7.1-16）单列**：`exploration = {tasks: 2, closed_success: 1,
  failed: 1, satisfaction: 0.5, satisfaction_basis: "closure_success_ratio(…)"}`，
  同时披露探索任务的介入权重与分项计数。
- **`reject` 的处理（D4）**：人工驳回是真实介入，但**不在 §6.1 七项内** →
  只进 `interventions.extension_weight` 单列（权重 1.0），**不进入 `acr_numerator`**，
  既不丢信号也不篡改 §6.1 口径（`test_reject_extension_kept_out_of_numerator`）。
- **未登记 kind 不静默**：权重按 0 计并写入 `unknown_kinds` 计数
  （`test_unknown_kind_disclosed`），`strict=True` 时抛 `ACRRuleError`。
- **审批迟滞 → 疲劳分桶**（§6.1 审批衰减率）：`instant(<10s) / quick(<1min) /
  deliberate(<10min) / slow(<1h) / stale(≥1h)`，落在 `approval` 事件
  （`latency_ms` + `fatigue_bucket`）。
- **实跑**（演示 [7]）：`分母 closed=1`；`分项计数 {approve:1, conditional:1,
  escape:1, reject:1, rerun:1, timeout_deny:1, view:1}`；`§6.1 分子=5.5`；
  `扩展项=1.0`；`ACR = 5.5`。

### ✅ 3. cost 埋点含 task_id/workspace_id/shadow_overhead/retries/缓存命中标记；缓存命中不计 token

- **字段齐备断言**（`test_payload_has_all_spec_fields`，13 字段逐个 `in payload`）。
- **缓存命中不计 token（P7.1-18）**：`billable_tokens_* = 0`、`cost_*_cents = 0`
  （但**原始 token 仍如实记录**），`shadow_overhead` 仍计入
  （`test_cache_hit_bills_no_tokens` / `test_cache_hit_still_counts_shadow_overhead`）。
- **S2-01 TraceContext 注入**：`task_id` / `workspace_id` / `subject_id` 与
  `correlation_id=trace_id` 由当前上下文自动填充（`test_task_id_injected_from_trace_context`），
  并在 `LLMMonitor.record()` 漏斗处自动携带（演示 [3] 三条 cost 事件
  `task_id=demo-task-1 ws=ws_demo_s203`）。
- **重试不重复计**：`retries` 作为字段记录；重试产生的 token 若已被上游计入
  `tokens_in/out` 则不重复累加，仅在显式 `retry_tokens_*` 时计入（`test_retry_tokens_added_only_when_explicit`）。
- **实跑**（演示 [3]/[8]）：

```
model=gpt-4        tok=2000/1000 计费tok=2000/1000 cache_hit=False 归一=12.0¢
model=gpt-4o-mini  tok=5000/5000 计费tok=0/0       cache_hit=True  归一=0.0¢
当日：llm_calls=3 cache_hits=1 计费token=3000/13000（缓存命中不计 token）
UTC = 归一成本 / 任务数 = 12.0 ¢/任务
```

### ✅ 4. 成本归一公式可复现（主力模型锚价 + 系数表）；与既有成本监控数据源一致

- **锚模型**（主力模型）解析优先级：`CP_UTC_ANCHOR_MODEL` > `config.yaml:llm.model`
  > 默认 `gpt-4o-mini`（`resolve_anchor_model()` 返回 `(model, source)`；
  本机实测 `("gpt-4", "config.yaml")`，env 覆盖时为 `("gpt-4", "env")`）。
- **公式（T6 修正）**：

```
cost_normalized_cents = Σ_{i∈{in,out}} (billable_tokens_i / 1000)
                        × anchor_price_cents_i × coefficient_i(model)
                      + shadow_overhead_cents
coefficient_i(model)  = price_i(model) / price_i(anchor)        # 默认 source=price_ratio
```

  `billable_tokens = 0 if cache_hit else tokens`（P7.1-18）。
  手算等值断言：`normalize_cost(1000, 500, "gpt-4") == 1000/1000×3.0 + 500/1000×6.0`
  （`test_formula_is_reproducible_by_hand`）。
- **与既有数据源一致**：模型单价表**唯一来源**是既有成本监控
  `agent.model_router.cost_tracker.MODEL_COSTS`（`model_costs() == MODEL_COSTS` 断言），
  未知模型沿用**同一个**兜底价 `{"input":0.01,"output":0.02}`；
  对账函数 `reconcile_pricing()` 对同 token 组合逐模型重算比对：

```
一致=True  4 个模型逐分相等（gpt-4 / gpt-3.5-turbo / gpt-4o-mini / unknown-model-probe，delta=0.000000）
```

  `reconcile_cost_log()` 另提供与既有数据文件 `data/cost_log.jsonl` 的对账
  （文件缺失时**如实返回 `rows=0, consistent=None`**，不伪造一致）。
- **系数表可覆盖（T6 的「换算系数」层）**：`CP_UTC_COEFFICIENTS`（JSON）覆盖后
  `source="override"`，归一结果按校准口径偏离属预期
  （`test_calibrated_coefficient_changes_result` / `test_pricing_diverges_after_calibration`）。
  **默认不做人为校准**——早期（W1–W9）无拟合数据，把未拟合系数当真值会引入新失真；
  此阶段归一化的作用是「统一口径 + 可对账」（对齐 T5/T6「只披露不考核」）。
- **日/周聚合 + S5-03 输入量**：`utc_daily / utc_weekly(ISO 周) / utc_window /
  utc_snapshot`（基线 = 近 N 日滚动日均归一成本、`ratio`、§7 阈值披露
  `{fasting_in:1.3, fasting_out:1.1, cooldown_hours:12}`，实现归 S5-03）；
  实测 ratio 机制：2 条历史成本 → 基线 2.0¢/日、`ratio=6.0`。

### ✅ 5. model.degraded 在降级/错误路径触发且有 from/to/reason

- 事件载荷 `{from, to, reason}`（+ `error_code=E_MODEL_DEGRADED` / `fallback_source` /
  `fallback_available` / `fallback_attempted` / `fallback_error` / task/workspace/subject）。
- **真实触发点 3 处**：
  1. `LLMMonitor.record()`：本次交互带 `error` → emit（覆盖 chat / summarize /
     tool_calling 三个 source，因为 record 是唯一漏斗）；
  2. `LLMService.chat()/summarize()`：所有重试耗尽 → `handle_primary_failure()`
     （**总是** emit；`CP_MODEL_FALLBACK_ENABLED=1` 时用**影子实例**真实切到候选模型
     重试，成功则返回降级结果，失败则如实记录 `fallback_error` 并保留原始异常）；
  3. `graceful_degrade._trigger_degrade()`：模型类组件（llm/model/chat/… 名字命中）
     进入降级 → emit；非模型组件（如 `memory_query`）**不臆造**模型事件。
- 实测（演示 [4]）：

```
降级链：['gpt-4o-mini','gpt-3.5-turbo']（来源 default） enabled=False（默认只观测不改行为）
gpt-4 -> gpt-4o-mini  ×1
payload: from=gpt-4 to=gpt-4o-mini reason=chat: APITimeoutError: request timed out…
         error_code=E_MODEL_DEGRADED attempted=False
```

- **诚实口径（D9）**：`to` 是**真实候选模型**（由降级链解析，不是占位串）；
  未真正切换时 `fallback_attempted=False` **如实标注**——不把「候选」冒充「已降级」。
  链尾无候选时 `to=""` + `fallback_available=False`（显式，不臆造）。
- 另有 `call_with_model_fallback()`（逐候选真实降级、每次切换 emit、全失败抛最后一个
  异常）与 `degrade_summary()`（按边 `from -> to` 与按原因类型汇总，供面板降级拓扑）。

### ✅ 6. 既有会话/审批/LLM 套件零回归；新增单测全绿、覆盖率 ≥80%

| 套件 | 结果 |
|---|---|
| **新增 6 套件**（`test_events_v1` 53 / `test_acr_metrics` 51 / `test_utc_cost` 37 / `test_escape_guard` 22 / `test_model_degrade` 23 / `test_s2_03_integration` 32） | ✅ **218 passed / 0 failed** |
| 既有 audit + trace（`test_audit` / `audit_chain` / `audit_facade` / `audit_integration` / `audit_migration` / `audit_ui_middleware` / `audit_logger_comprehensive` / `trace_v2` ×2） | ✅ 全绿（含在下方合计） |
| 既有 skills 审批/评审/谱系/评估（`skills_mgmt` / `skills_mgmt_safety` / `skills_mgmt_lineage` / `skills_digest_assessor`） | ✅ 全绿 |
| 既有调度/任务（`task_scheduler` / `task_scheduler_comprehensive` / `task_scheduler_singleton` / 集成 `task_scheduler_integration`） | ✅ 全绿 |
| 既有会话（`session_manager_comprehensive` / `session_manager_concurrency` / `session_workspace_binding` / `session_group_store`） | ✅ 全绿 |
| 既有 LLM/监控/降级（`llm_monitor_singleton` / `model_router` / 集成 `model_router_cost` / `graceful_degrade_comprehensive` / `graceful_degrade_scenarios` / `misc_modules_comprehensive` / `import_smoke`） | ✅ 全绿 |
| **合计（35 个套件：新增 6 + 既有 29）** | ✅ **1755 passed / 0 failed / 268 skipped / 1 xfailed**（83.0s） |

```
命令：python -m pytest <35 个文件> -q -p no:randomly
结果：1755 passed, 268 skipped, 1 xfailed, 1 warning in 83.03s
其中 268 skipped 全部为既有 `--runslow` 标记（task_scheduler 系列），
1 xfailed 为既有 TF-IDF 检索基线预期失败，均非本任务引入。
既有 29 个套件 = 上表 28 个 + `test_system_tools_core`（本任务回归发现并修复的
1 处测试互扰，见 §五 补记二 ⑤；修复后该套件 408 passed / 9 skipped）。
```

覆盖率（`--cov=agent.observability.*`，仅跑本任务 6 套件，branch=True）：

```
Name                                 Stmts   Miss Branch BrPart  Cover
agent\observability\__init__.py          7      0      0      0   100%
agent\observability\acr.py             293     20     80      6    92%
agent\observability\escape.py          200     20     48      4    90%
agent\observability\events.py          528     73    160     23    85%
agent\observability\model_degrade.py   118      8     36      6    91%
agent\observability\utc.py             315     47     68     11    83%
```

本任务**新增/改动模块逐个 ≥83%（5 个新模块均值 88.2%）**。包级 TOTAL 52% 含本任务未触及的
既有模块（`tool_trace.py` 22% / `trace_v2.py` 47% / `subscriber.py` 29% /
`arch_rules.py` 0% / `dependency_graph.py` 0%），与 S2-01/S2-02 报告的同口径披露方式一致
（S2-02 亦标注「含未改动的既有 `observability.py` 0%」）。

### ✅ 7. 真实任务链可产出 ACR+UTC 样例（验收报告含快照）

演示脚本 `scripts/demo_s2_03_events_acr.py`（325 行）实跑 9 步，**完整输出见 §六.3**；
产物：`data/events_demo/events.jsonl`（21 条 events.v1 事件）、
`data/acr_snapshot.json`、`data/utc_snapshot.json`（三者均已登记 `.gitignore`）。

---

## 四、埋点清单对照（§6.6）

| §6.6 埋点 | 落地事件 | 真实发生点（接线位置） | 字段 |
|---|---|---|---|
| `task.closed` | `task.closed` | `orchestrator.process()` → `_end_unified_task_trace()` → `_record_task_closed_metrics()` | intent / difficulty / status / intervened / intervention_kind / intervention_kinds / duration_ms / cost_cents（+task_id/workspace_id/subject_id） |
| `task.abandoned` | `task.abandoned` | `acr.record_task_abandoned()`（§3.8 生命周期；不计入分母，单列） | + abandon_reason |
| `approval` | `approval` + `approval.required` | `ApprovalFlow._append_record`（submit）/ `_transition`（approved/rejected）/ `merge` / `mark_manual_executed` / `expire_pending` | kind / record_id / state / level / latency_ms / fatigue_bucket / counted_as_intervention |
| `intervention` | `intervention` | 上述治理动作 + `TaskScheduler.execute_now`（rerun）+ `ApprovalFlow.record_view`（view）+ `escape` | kind / weight / task_id / source_ref / intent |
| `escape` | `escape` | `agent/tools/task_tools.py`（受控写登记 + `list_scheduled_tasks()` 检测） | task_id / reason / capability_id / path / digest_before / digest_after / changed_task_ids |
| `cost` | `cost` | `LLMMonitor.record()`（chat / summarize / tool_calling 唯一漏斗） | task_id / workspace_id / subject_id / model / provider / tokens_in / tokens_out / retries / duration_ms / shadow_overhead_ms / shadow_overhead_cents / cache_hit / cost_raw_cents / cost_normalized_cents / anchor_model / coefficient_* |
| 第 9 事件 | `model.degraded` | `LLMMonitor.record()`（error）→ `LLMService` 重试耗尽 → `graceful_degrade._trigger_degrade` | from / to / reason / error_code / fallback_source / fallback_attempted / fallback_error |

**§13.3 联动（事件 ↔ 审计写入）**：治理类事件 `escape` / `model.degraded` /
`policy.denied` / `healing.triggered` 同步镜像入 S2-02 链式台账（`AUDIT_MIRROR_TYPES`，
`CP_EVENTS_AUDIT_MIRROR=0` 可关）；`approval.required` **刻意不镜像**——审批状态机已把
`approval.submit/approved/…` 直接写链，重复镜像会破坏 S2-02 的「一动作一记录」不变量
（实现期实测该镜像曾使 `test_approval_lifecycle_recorded` 失败，已回退并加断言）。

**存量事件文件不迁移**：本任务只落「新事件出口 + 核心事件接入」，
`skills_assessment_events.jsonl` / `approval_records.jsonl` / `task_history.jsonl` /
`cost_log.jsonl` 等**保持只读**（归档轨由 S2-02 `agent.audit.migration` 处理），
不删除、不改写、不追溯；`skills_assessment_events` 经 S0-01 更名后的事件名保持不变。

---

## 五、关键设计裁定（云枢对设计文档未闭合点的显式化）

| # | 议题 | 裁定 | 理由 |
|---|---|---|---|
| D1 | 信封 `v` 取值 | `v = 1`（int 版本号），schema 名另由 `SCHEMA_NAME="events.v1"` 承载 | §3.6 只给字段名 `v`；若 `v` 直接存字符串 `"events.v1"` 则与「events.v1 信封」的命名重复冗余；版本号 + schema 名分离是常规信封设计 |
| D2 | `ts` 时区 | ISO-8601 **带本地偏移 + 毫秒**（非 UTC `Z`） | ① 时间点无歧义；② `ts[:10]` = 本地日历日，与云枢存量事件文件（本地 naive ISO）及 ACR/UTC「按日/按周」口径一致，避免 UTC 分日造成运营侧错位 |
| D3 | 事件落地介质 | 追加写 JSONL `data/events/events.jsonl`（**不新增 SQLite**） | 事件是**高频计量流**，与 S2-02 的低频高耐久审计链（WAL+synchronous=FULL）性质不同；JSONL 亦便于 S3/S5/S6 直接流式消费；**单写者纪律照搬 S2-02**（同路径第二 writer 抛 `SingleWriterViolationError`，只读方用 `EventStore.reader()`） |
| D4 | `reject` 不在 §6.1 七项内 | 只进 `extension_weight` 单列，**不进 ACR 分子** | §6.1 明确列出七项；人工驳回确为真实介入（不能丢信号），但擅自并入口径会使 ACR 与设计文档不可比——两者兼顾：单列披露 |
| D5 | ACR 分子分母的「同口径排除」 | 分子只累加 §6.1 七项且**排除 explore/consult 任务的介入** | 分母已排除 explore/consult；若分子仍计入其介入，比例会被系统性放大（口径自洽的要求） |
| D6 | 探索满意度公式（P7.1-16 未给公式） | 以「关闭成功率 = closed / (closed+failed)」为**代理指标**，并同时披露探索任务的介入权重与分项 | 源文档未给公式；代理指标可复现、口径透明，且把「探索是否被打断」的信息一并暴露，避免单指标误导 |
| D7 | 归一化系数默认值 | 默认系数 = **价格比**（`source="price_ratio"`），可用 `CP_UTC_COEFFICIENTS` 覆盖校准 | 价格比系数使归一成本与既有成本监控**逐分可对账**（验收要求「与既有数据源一致」）；T6 要求的「换算系数表」作为同一公式上的**可覆盖层**保留，但**默认不做未经数据拟合的人为校准**（T5/T6 早期只披露不考核） |
| D8 | UTC 分母取哪一类任务 | `utc_cents_per_task` 用 `closed+failed`（**全意图**）；另给 `utc_cents_per_task_acr_cohort`（ACR 同口径） | 成本与「任务意图是否属探索」无关，按 ACR 口径排除 explore/consult 会低估单位成本；两个口径同时给出，面板可按需取用并在指标字典标注 |
| D9 | 「真降级」是否默认开启 | **默认只发事件、不切模型**（`CP_MODEL_FALLBACK_ENABLED=0`）；置 1 才真实切换 | P4 分级实施：数据/可观测层任务不应静默改变生产链路行为；`fallback_attempted` 字段如实标注，既不夸大也不隐瞒 |
| D10 | 逃逸检测的判定基线 | 「受控写台账（sha256）+ 首次见到只登记基线」；检测**只读**、绝不回写/回滚用户改动 | 无文件系统级审计钩子时，指纹比对是单机可行且可复现的判据；首次见到即报会造成存量文件大面积误报；用户对本地文件的最终处置权在用户（守【不易】） |
| D11 | 服务层能否导入 observability | **不能**——改为 `ApprovalFlow.record_view()` 由审批域承载，服务层经注入的 `self._approval` 调用 | 实现期 CI 架构规则 `no_circular_dependency` 实测拦截：`observability.acr → events → trace_v2 → descriptors.bridge → skills_mgmt.store → registry → service` 既有链路 + 新增 `service → acr` 构成环；改由**不在该链路上的审批域**承接即解环，同时语义更内聚（审批域自己拥有审批指标 API） |
| D12 | 审计镜像范围 | 只镜像 `escape` / `model.degraded` / `policy.denied` / `healing.triggered` | 这四类此前在链上**没有**任何写入方；`approval.required` 已由审批状态机写链（`approval.submit`），重复镜像违反 S2-02「一动作一记录」不变量（实测会使既有单测失败） |

---

## 六、执行证据（2026-09-10 实跑）

### 6.1 新增套件与覆盖率

```
新增 6 套件：218 passed / 0 failed
  test_events_v1 53 / test_acr_metrics 51 / test_utc_cost 37 /
  test_escape_guard 22 / test_model_degrade 23 / test_s2_03_integration 32
覆盖率（branch=True，仅跑本任务 6 套件）：
  events.py 85%  acr.py 92%  utc.py 83%  escape.py 90%  model_degrade.py 91%
  __init__.py 100%（新增导出面）
```

### 6.2 既有套件回归

```
35 个套件（新增 6 + 既有 29，含本任务回归发现并修复互扰的 test_system_tools_core）：
  1755 passed / 0 failed / 268 skipped / 1 xfailed（83.03s）
  命令：python -m pytest <35 个文件> -q -p no:randomly
CI 架构规则：python -m agent.observability.arch_rules --check
  → 状态 ✅ 通过（未豁免违规 0；4 项均为既有豁免）
CI 阻塞型 mypy 目标（ci.yml「关键模块严格类型检查（阻塞）」）：
  agent/env_config_manager.py → Success: no issues found in 1 source file
  agent/network_config.py     → Success: no issues found in 1 source file
新增模块 mypy：5 个新模块 0 error（`mypy agent/observability/{events,acr,utc,escape,model_degrade}.py`）
```

### 6.3 演示实跑：`python scripts/demo_s2_03_events_acr.py`

```
事件流：data/events_demo/events.jsonl     Trace 台账：data/s2_03_demo_trace.db
锚模型：gpt-4（来源 env）

[1] 审批流 → approval.required / approval / intervention（§6.1 口径）
  提交 4 条 → approve / 条件通过 / 驳回 / 超时拒绝 1 条

[2] 逃逸检测：用户手工编辑受管任务文件（未经受控写入路径）
  escape   path=scheduled_tasks.json reason=untracked_content_change
           capability=cp.governed.scheduled_task.write changed=['hand-edited']
  重复检测：emitted=False（同一 (path, 新摘要) 只计一次）

[3] 会话任务链：TraceContext → 工具调用 → LLM 记账 → task.closed
  任务意图（启发式）：intent=build difficulty=easy
  任务级 trace_id：936c2de43bd84995（workspace_id=ws_demo_s203）
  cost 埋点（§6.6）：
    model=gpt-4         tok=0/0       计费tok=0/0        cache_hit=False 归一=0.0¢  task_id=demo-task-1 ws=ws_demo_s203
    model=gpt-4o-mini   tok=5000/5000 计费tok=0/0        cache_hit=True  归一=0.0¢  task_id=demo-task-1 ws=ws_demo_s203
    model=gpt-4         tok=2000/1000 计费tok=2000/1000  cache_hit=False 归一=12.0¢ task_id=demo-task-1 ws=ws_demo_s203
  task.closed: intent=build difficulty=easy status=closed intervened=True intervention_kind=rerun

[4] model.degraded（P7.1-18 第 9 事件 / §11.6.0 E_MODEL_DEGRADED）
  降级链：['gpt-4o-mini', 'gpt-3.5-turbo']（来源 default） enabled=False（默认只观测不改行为）
  gpt-4 -> gpt-4o-mini  ×1
  payload: from=gpt-4 to=gpt-4o-mini reason=chat: APITimeoutError: request timed out…
           error_code=E_MODEL_DEGRADED attempted=False

[5] events.v1 事件流总览
  事件总数=21  类型分布={"approval.required":4,"intervention":7,"approval":4,
                        "cost":3,"escape":1,"model.degraded":1,"task.closed":1}
  样例信封（§3.6 七字段，逐字）：
    {
      "v": 1,
      "event_id": "ev_6b05ef64b93548a67f7ecef2a0260c7c",
      "ts": "2026-09-10T23:29:08.231+08:00",
      "correlation_id": "936c2de43bd84995",
      "actor": "auto",
      "type": "task.closed",
      "payload": {
        "task_id": "demo-task-1",
        "workspace_id": "ws_demo_s203",
        "subject_id": "",
        "intent": "build",
        "difficulty": "easy",
        "status": "closed",
        "intervened": true,
        "intervention_kind": "rerun",
        "intervention_kinds": ["rerun", "view"],
        "duration_ms": 401.047,
        "error_code": "",
        "source": "orchestrator.process",
        "input_length": 14
      }
    }

[6] 幂等验证：同一批事件内容级重放 → 计数不变
  重放前 21 条 → 重放后 21 条（内容级重放折叠 21/21 条，新增 0 条）

[7] ACR 汇总（§6.1 口径 / 分母排除 explore·consult / 探索满意度单列）
  分母：closed=1 failed=0 total=1（排除 explore/consult 0 个任务；abandoned=0 单列）
  介入：events=7 总权重=6.5 §6.1 分子=5.5 扩展项权重=1.0 无法归属=4.5
  分项计数：{"approve":1,"conditional":1,"escape":1,"reject":1,"rerun":1,"timeout_deny":1,"view":1}
  分项权重：{"approve":1.0,"conditional":0.5,"escape":1.0,"reject":1.0,"rerun":1.0,"timeout_deny":2.0,"view":0.0}
  权重表：{"approve":1.0,"conditional":0.5,"auto_pass":0.0,"timeout_deny":2.0,"rerun":1.0,"escape":1.0,"view":0.0}
  ACR = 5.5   （ACR = Σ介入权重(§6.1 七项口径，排除 explore/consult 任务) / (closed + failed)）
  探索满意度（P7.1-16 单列）：tasks=0 satisfaction=None 介入权重=0.0
  周视图：{'start': '2026-09-07', 'end': '2026-09-13'} ACR=5.5

[8] UTC 汇总（单位任务成本；锚价 + 系数表；S5-03 断食/刹车数据源）
  锚价：{"input": 3.0, "output": 6.0} cents/1k tokens
  当日：llm_calls=3 cache_hits=1 计费token=3000/13000（缓存命中不计 token，P7.1-18）
  成本：raw=12.0¢  归一=12.0¢  shadow_overhead=0.0¢
  UTC = 归一成本 / 任务数 = 12.0 ¢/任务（ACR 同口径分母：12.0 ¢/任务）
  滚动基线：2.0¢/日（6 日，含 2 条 demo_seed 历史事件） ratio=6.0
  断食阈值（S5-03 消费，本任务只供度量）：
    {"fasting_in":1.3,"fasting_out":1.1,"cooldown_hours":12,"owner":"S5-03（本任务只提供度量，不实现断食状态机）"}
  系数表（模型 × 计价，T6「以主力模型为锚 + 换算系数」）：
    gpt-3.5-turbo  k_in=0.050000 k_out=0.033333 来源=price_ratio
    gpt-4          k_in=1.000000 k_out=1.000000 来源=price_ratio
    gpt-4o-mini    k_in=0.005000 k_out=0.010000 来源=price_ratio
  与既有成本监控（CostTracker/MODEL_COSTS）对账：一致=True（4 个模型逐分相等）

[9] 快照落盘
  data/acr_snapshot.json
  data/utc_snapshot.json
```

**快照读法（示例任务链）**
：当日 1 个 build 任务（成功关闭）× 7 类介入各 1 次 →
`§6.1 分子 = 1+0.5+2+1+1+0 = 5.5`（approve/conditional/timeout_deny/rerun/escape/view），
`扩展项 reject = 1.0` 单列 → **ACR = 5.5 / 1 = 5.5**；
介入中 4.5 权重发生在**无任务上下文**的治理侧（审批/逃逸，`correlation_id=unlinked`），
如实计入 `unattributed_weight`（生产环境下审批若发生在任务上下文内则自动归属该任务）。
UTC 侧：3 次 LLM 调用（1 次缓存命中不计 token）→ 12.0¢ 归一成本 / 1 个任务 = 12.0¢/任务。

### 6.4 幂等与并发实测

```
内容级重放：21 → 21（折叠 21/21）
并发写：6 线程 × 20 条 = 120 条，event_id 去重后仍 120（无竞态、无重复）
单写者：同路径第二个 writer 抛 SingleWriterViolationError（§5.5）
```

### 6.5 全量扫描

```
python -m pytest tests/unit -q -p no:randomly
→ 13033 passed / 0 failed / 302 skipped / 13 xfailed / 4 xpassed（1551.38s ≈ 25:51）
（首次扫描为 13032 passed / 1 failed：失败项 test_system_tools_core 的
  os.makedirs 全局 patch 互扰，修复后归零，见 §五 补记二 ⑤；
  跳过/xfail/xpass 均为既有标记，与 S2-01 记录的 12524 passed / 0 failed 基线同口径）
```

---

## 七、联动与文件清单

### 7.1 新增文件

| 文件 | 内容 |
|---|---|
| `agent/observability/events.py`（1003 行） | `EventEnvelope`、`EventType`（15 成员）、`EventStore`（append/emit/read/tail/stats/close/reader/interventions_for）、`build_event_id`、`canonical_payload`、`sanitize_payload`、`now_ts`/`event_day`/`shift_day`、单写者登记（`register_writer`/`release_writer`/`active_writers`）、`get_event_store`/`get_event_reader`、`default_correlation_id`/`trace_fields`、`emit`、`iter_events`/`read_events`/`group_by_day`/`filter_types`、异常族（`EventError`/`EventEnvelopeError`/`EventTypeError`/`SingleWriterViolationError`/`ReadOnlyEventStoreError`） |
| `agent/observability/acr.py`（712 行） | `INTERVENTION_WEIGHTS`（§6.1 七项 + 扩展 `reject`）、`CORE_/EXTENDED_INTERVENTION_WEIGHTS`、`classify_intent`/`classify_difficulty`/`fatigue_bucket`/`intervention_weight`、`record_intervention`/`record_approval`/`record_escape`/`record_task_closed`/`record_task_abandoned`、`acr_summary`/`acr_daily`/`acr_weekly`/`acr_snapshot`/`acr_by_day`/`write_acr_snapshot` |
| `agent/observability/utc.py`（651 行） | `model_costs`/`price_usd_per_1k`/`resolve_anchor_model`/`anchor_prices_cents`/`coefficient`/`coefficient_table`/`reset_config_cache`、`normalize_cost`、`record_cost`、`utc_daily`/`utc_window`/`utc_weekly`/`utc_snapshot`/`write_utc_snapshot`、`reconcile_pricing`/`reconcile_cost_log` |
| `agent/observability/escape.py`（403 行） | `GovernedWriteLedger`、`governed_write`（上下文管理器）、`record_governed_write`、`governed_capability`、`file_digest`、`watched_files`、`detect_escapes`、`_changed_task_ids`、`escape_summary` |
| `agent/observability/model_degrade.py`（286 行） | `E_MODEL_DEGRADED`、`DEFAULT_FALLBACK_CHAIN`、`fallback_enabled`、`resolve_fallback_chain`/`reset_chain_cache`/`next_fallback_model`、`report_model_degraded`、`handle_primary_failure`、`call_with_model_fallback`、`degrade_summary` |
| `scripts/demo_s2_03_events_acr.py`（325 行） | 9 步端到端演示 + ACR/UTC 快照落盘（`--json` 附 JSON 快照） |
| `tests/unit/test_events_v1.py`（456 行） | **53 例**：信封 13 / 事件类型 5 / 幂等 10 / 载荷纪律 6 / 出口纪律 9 / 读取 7 / 归档与镜像 4 |
| `tests/unit/test_acr_metrics.py`（354 行） | **51 例**：§6.1 权重 4 / 分类器 9 / 埋点写入 10 / 汇总口径 11 / 视图 5 |
| `tests/unit/test_utc_cost.py`（351 行） | **37 例**：价格与锚 8 / 归一公式 9 / 对账 4 / 埋点字段 5 / 聚合 11 |
| `tests/unit/test_escape_guard.py`（219 行） | **22 例**：台账与指纹 8 / 检测生命周期 14 |
| `tests/unit/test_model_degrade.py`（210 行） | **23 例**：链解析 6 / 事件载荷 6 / 主模型失败收口 4 / 降级链调用 4 / 汇总 2 |
| `tests/unit/test_s2_03_integration.py`（483 行） | **32 例**：task.closed 5 / 审批流 9 / 逃逸 4 / LLM 记账 5 / 其它真实发生点 8 / 端到端 1 |

### 7.2 存量文件改动（均为 additive / best-effort，异常在调用点内吞掉）

| 文件 | 改动 |
|---|---|
| `agent/observability/__init__.py` | 导出 events.v1 / ACR / UTC / escape / degrade 公共 API（**69 个符号**；既有 trace_v2 导出零移除） |
| `agent/skills_mgmt/approval.py` | 新增 `_emit_metrics()`（审批 → `approval.required`/`approval`/`intervention`）、`record_view()`（view=0）、`expire_pending()`（**新建超时 Deny=2 路径**）、`_pending_since()`；`_append_record`/`_transition`/`merge`/`mark_manual_executed` 四处接线；`_transition` 增 `metrics_kind`/`latency_ms`/`emit_metrics` 参数（默认行为不变） |
| `agent/skills_mgmt/service.py` | `list_pending_approvals()` 经 `self._approval.record_view()` 逐条记 view 介入（服务层**不导入** observability，避免循环依赖） |
| `agent/orchestrator/orchestrator.py` | `_end_unified_task_trace()` 增 `user_input` 形参 + 收口点补 `task.closed`；新增 `_record_task_closed_metrics()`；call site 传 `user_input` |
| `agent/llm_monitor.py` | `LLMInteraction` 增 7 个 additive 字段（task_id/workspace_id/subject_id/retries/shadow_overhead_ms/cache_hit/cost_normalized_cents）；`record()` 增 `_emit_observability()`（cost + model.degraded）；`create_from_api_call()` 增 4 个形参并从 TraceContext 注入叶子字段；新增 `_current_trace_field()`/`_current_task_id()` |
| `agent/tools/task_tools.py` | `_record_governed_write()`（受控写登记 + task_ids 快照）、`_detect_escapes()`（读取前检测）；create/delete/toggle 三处登记；`list_scheduled_tasks()` 读取前检测 |
| `agent/task_scheduler.py` | `execute_now()` 记 `rerun` 介入；新增模块级 `_record_rerun()` |
| `agent/graceful_degrade.py` | `_trigger_degrade()` 对模型类组件 emit `model.degraded`；新增 `_report_component_degrade()` 与 `_MODEL_COMPONENT_HINTS` |
| `memory/llm_service.py` | 新增 `_shadow_service()`/`_fallback_after_failure()`；`chat()`/`summarize()` 失败收口 emit `model.degraded`（`CP_MODEL_FALLBACK_ENABLED=1` 时真实降级重试） |
| `.gitignore` | ① 登记 S2-03 运行时产物（`data/events/`、`data/events_demo/`、`acr_snapshot.json`、`utc_snapshot.json`、`s2_03_demo_trace.db*`）；② 补齐既有遗漏：`data/skills_mgmt_review_audit*.jsonl`（基数名早已忽略，但 `log_archiver` 跨日产生的**按日分片** `-YYYY-MM-DD.jsonl` 未忽略——全量回归跨日实测发现归档分片污染工作区；非本任务引入，属顺手补齐） |

### 7.3 环境开关（全部带默认值，可运行时降级/回滚）

| 变量 | 默认 | 作用 |
|---|---|---|
| `CP_EVENTS_ENABLED` | `1` | 事件出口总开关（`0` = 完全静默） |
| `CP_EVENTS_DIR` | `data/events` | 事件目录（单测/演示隔离用） |
| `CP_EVENTS_AUDIT_MIRROR` | `1` | 治理类事件是否镜像入链式审计 |
| `CP_EVENTS_ARCHIVE` | `1` | 跨日是否调用 `log_archiver.archive_daily_file()` 归档 |
| `CP_ESCAPE_GUARD` | `1` | 逃逸守卫总开关 |
| `CP_ESCAPE_WATCH` | `data/scheduled_tasks.json` | 纳管的受管任务文件清单（`os.pathsep` 分隔） |
| `CP_UTC_ANCHOR_MODEL` | `config.yaml:llm.model` | 归一化锚模型（主力模型） |
| `CP_UTC_COEFFICIENTS` | 空 | 系数表覆盖（JSON，T6 校准层） |
| `CP_UTC_PRICES` | 空 | 单价覆盖（JSON，应急） |
| `CP_MODEL_FALLBACK_CHAIN` | 空 → 文档化默认链 | 降级链 |
| `CP_MODEL_FALLBACK_ENABLED` | `0` | 是否**真的**切换到降级模型 |

### 7.4 既有接口零破坏核验

- `ApprovalFlow` 既有方法签名与返回值零改动（`_transition` 新增参数**均有默认值**，
  `approve/reject/merge/mark_manual_executed` 调用点未变）；
- `LLMInteraction` 仅**追加**带默认值的字段，`to_dict()` 旧键集合不变（既有
  `test_llm_monitor_singleton` 断言 `records[0]["model"]`/`["error"]` 全绿）；
- `LLMMonitor.record()` 的既有行为（append + 环形裁剪）逐行保持，埋点在锁外 best-effort；
- `task_tools` 四个公开函数的返回值与白名单语义零改动（仅追加登记/检测调用）；
- `TaskScheduler.execute_now()` 返回 `run_task()` 结果不变；
- `graceful_degrade` 降级状态机语义不变（仅在 `_trigger_degrade` 尾部追加事件）；
- `LLMService.chat/summarize` 默认路径**逐行等价**（开关关闭时只发事件），
  原始 `LLMServiceError` 仍原样抛出（`from e` 保留因果链）；
- 事件写入/归档/镜像/逃逸检测/降级埋点**全部**包在 `try/except` 内并降级为 debug 日志，
  任意失败返回 None/空列表，**绝不阻断主路径**（`test_detection_failure_is_swallowed` 等 6 例）。

---

## 八、遗留清单（不阻塞本任务验收；随主线消费）

| # | 遗留 | 归属 |
|---|---|---|
| 1 | **规划 wire 路径无 `task.closed`**：`orchestrator.process()` 在规划接线成功时跳过 LLM 段（S2-01 遗留 #2 同源），该路径任务不进 ACR 分母 | S3-01（规划引擎与消化流水线合流时接线） |
| 2 | **`task.abandoned` 无自动判定**：仅提供 `record_task_abandoned()` 入口，会话超时/用户放弃的自动识别未接（§3.8 生命周期补齐） | S6/S2 生产化（需会话层超时策略） |
| 3 | **ACR 意图/难度为启发式**（词典 + 长度阈值，难度等权起步）：对齐审计报告 T5，早期（W1–W9）**只披露不考核**；未做数据拟合与阈值回归 | S5-02/S5-03（有了 L2 Core-50 基线后再拟合） |
| 4 | **探索满意度为代理指标**：源文档 P7.1-16 未给公式，当前以「关闭成功率」代理并披露介入权重 | S5-02（评测接入后可换为真实满意度信号） |
| 5 | **归一化系数默认未校准**：默认取价格比（与既有监控逐分一致），`CP_UTC_COEFFICIENTS` 校准层已就位但无拟合数据 | S5-03（拿到跨模型成本-效果数据后校准） |
| 6 | **UTC 只落“度量”，不实现断食/刹车状态机**：`utc_snapshot` 已给基线/ratio/阈值，规则判定归 S5-03 | S5-03（依赖已就绪） |
| 7 | **逃逸检测窗口有限**：仅覆盖「已登记受控写的受管文件」；未纳管文件、受控写登记**之前**发生的改动不可检出；无文件系统级审计钩子（单机降级） | S4-01（审批面安全）/ P5 Backlog（外部只追加存储） |
| 8 | **`change_detection` 依赖 JSON `tasks` 数组形态**：`_changed_task_ids()` 只对含 `tasks` 数组的 JSON 生效，其它受管文件只报 path/digest | 随受管文件清单扩展（`CP_ESCAPE_WATCH`）时一并定义 |
| 9 | **`cost_log.jsonl` 生产未接线**：既有 `CostTracker.record()` 在仓库内**无生产调用方**（仅测试），故 `reconcile_cost_log()` 在多数部署下为 `rows=0`；本任务已把真实记账点接到 `LLMMonitor.record()`（事件流），既有文件的写入方补齐另议 | S2 生产化（成本监控数据源统一） |
| 10 | **事件流保留/归档策略**：按日分片但**无 TTL**，与 S2-01 遗留 #7 / S2-02 遗留 #4 同源 | S2 生产化 / S5（与 trace/审计链保留策略一并定） |
| 11 | **跨进程并发写**：进程内单写者 + 追加写兜底；多 worker 共写同一 event 文件未做文件锁 | S2 生产化（同 S2-01 遗留 #6 / S2-02 遗留 #5） |
| 12 | **UI 六面板未消费本事件流**（ACR/UTC 面板、降级拓扑、逃逸清单） | S6-01（数据源已就绪：`acr_snapshot.json` / `utc_snapshot.json`） |
| 13 | **认证/身份与埋点 actor 归因**：审批/查看/逃逸的 actor 现为「显式 > 请求头 > Cookie > 令牌指纹 > `ui:<addr>`」降级（S2-02 遗留 #1），事件侧沿用同一口径 | S4-01（身份层补齐） |
| 14 | **`force_degrade()` 未发 `model.degraded`**：该入口是运营/混沌演练的**强制**降级（非失败驱动），为免噪声未接；如需演练可观测需另行裁定 | 随 §11.10 混沌演练清单补充项一并评估 |

---

*补记一：本任务对「事件信封化」的实现取向是**「新出口 + 核心接入、存量只读」**——不一次性
替换 `skills_assessment_events` / `approval_records` / `task_history` / `cost_log` 四类存量
事件文件（那会触发整版重写风险且必然遗漏），而是把「统一信封 + 幂等键 + 单写者 + 按日归档 +
审计镜像」建为不变量，让新的治理事件先走新出口，存量由 S2-02 归档轨只读承接。*

*补记二（实现期实测修复的 5 个真实缺陷，均已加回归测试）*：
① **敏感键子串匹配吞掉度量字段**：`sanitize_payload()` 原以子串匹配敏感键，
`tokens_in` 含 `token` → 计费 token 被替换为丢弃占位符 → `int()` 失败 → **UTC 归零**。
已改为「精确命中 + 分段命中 + 末段命中」并在白名单中保护 §6.6 度量字段
（`test_metric_fields_never_mangled`）。
② **`task.closed` 丢失 `workspace_id`**：收口顺序为
`TraceFacade.finish()`（**清空 ContextVar**）→ `record_task_closed()`，
后者再读 `trace_fields()` 已为空 → P7.1-19 不变量在任务级事件上静默失效。
已改为由 `ctx` 显式携带 `workspace_id/subject_id`（`test_success_path_emits_task_closed`
断言二者非空）。
③ **新增循环依赖**：`service → observability.acr` 与既有链路
（`acr → events → trace_v2 → descriptors.bridge → skills_mgmt.store → registry → service`）
构成环，CI 架构规则 `no_circular_dependency` 报 **未豁免违规 1 项**。已把 view 埋点下沉到
审批域 `ApprovalFlow.record_view()`（不在该链路上），架构校验恢复 ✅ 通过（§六.2）。
④ **`approval.required` 审计镜像造成重复留痕**：镜像使 S2-02 单测
`test_approval_lifecycle_recorded`（以链上动作序列断言「一动作一记录」）失败。
已从 `AUDIT_MIRROR_TYPES` 移除并加断言说明（§四「§13.3 联动」）。
⑤ **测试互扰：全局 `os.makedirs` patch**：事件出口与逃逸台账原用 `os.makedirs` 建目录，
而既有单测 `test_system_tools_core.py::test_save_tasks_creates_directory` 以
`patch('agent.tools.task_tools.os.makedirs')` + `assert_called_once()` 断言**调用次数**
（`os.makedirs` 是进程级全局函数，patch 后影响全进程）→ 首次全量扫描出现
**1 failed / 13032 passed**。已改用 `pathlib.Path.mkdir`（与 `log_archiver` 同风格，
且不经被 patch 的全局函数）→ 该套件 **408 passed / 0 failed**。

*补记三（`str, Enum` 混入的序列化陷阱）*：`class EventType(str, _enum.Enum)` 下
`str(EventType.COST)` 得到 `"EventType.COST"` 而非 `"cost"`，直接落盘会污染事件名。
`EventEnvelope.__post_init__` 与 `EventStore.emit()` 统一走 `normalize_type()`
（`Enum → .value`），并有 3 例断言（`test_enum_values_are_normalized` 等）。*

*补记四（`to_model` 的 `None` vs `""` 语义）*：`report_model_degraded()` 的 `to_model`
默认 `None` = 「未指定，解析真实候选」；显式 `""` = 「调用方明确无后续候选（链尾）」。
实现期实测两者混用会导致链尾事件被回填成链首候选（自造降级环），已分离语义并加断言
（`test_all_failed_raises_last_error` 断言 `("m2", "") in edges`）。*
