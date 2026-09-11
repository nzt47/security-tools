# TASK-S5-03 验收报告 — UTC 日/周双层刹车 + 断食 + 性能预算重定

> 任务书：[TASK-S5-03_成本刹车与断食.md](TASK-S5-03_成本刹车与断食.md)（对齐 v7.2 §7 / P7.2-06 / §6.3 / §6.7 / §11.2）
> 批次总表：[PARALLEL_并行铺开总表.md](PARALLEL_并行铺开总表.md)｜worktree：`.worktrees/s503`（`--base master`，基线 `15eae00d`）
> 上游 Owner 裁定：**C**（沿用价格锚定系数，本次不校准）+ **D**（双成本轨收敛到事件流）**已于 2026-09-11 关闭**
> 依赖：S2-03（成本事件/ACR）✅ 已结案；S3-03（shadow/内化）✅ 已结案
> 验收日期：2026-09-11

---

## 1. 交付物清单

| # | 交付物 | 路径 | 规模 |
|---|---|---|---|
| 1 | **成本刹车门面**（日级硬熔断 + 周级断食 + θ 分阶段 + 审批衰减率 + 口径审计 + 日成本视图） | `agent/monitoring/cost_brake.py`（**新建**） | 1866 行 |
| 2 | 新增单测（107 例） | `tests/unit/test_s5_03_cost_brake.py`（**新建**） | 1055 行 / **107 用例** |
| 3 | **演练脚本**（真实触发→恢复，38 项检查） | `scripts/demo_s5_03_cost_brake.py`（**新建**） | 418 行 / 38 检查项 |
| 4 | 性能基线补充测量脚本 | `scripts/bench_s5_03_cost_brake.py`（**新建**） | 244 行 |
| 5 | **性能预算重定文档** | `docs/PERF_BUDGET_REBASED.md`（**新建**） | 146 行 |
| 6 | 双成本轨收敛（停写 + 只读兼容 + 告警 + 回滚开关） | `agent/model_router/cost_tracker.py`（改） | +194 / −4 |
| 7 | 口径版本标注 + `reconcile_cost_log` 降级为纯对账 | `agent/observability/utc.py`（改） | +85 / −10 |
| 8 | 断食→S3 shadow 预算联动（归零/减半穿透低流量保底）+ `ShadowPlan.cost_factor` | `agent/digestion/shadow.py`（改） | +54 / −5 |
| 9 | 断食→内化评估调度抑制 | `agent/digestion/internalize.py`（改） | +23 |
| 10 | 断食→漂移重探调度抑制 | `agent/digestion/gate.py`（改） | +11 |
| 11 | 既有旧轨用例改造（停写契约 + 回滚路径双覆盖） | `tests/unit/test_misc_modules_comprehensive.py`（改） | +85 / −6 |
| 12 | 运行时产物不入库（`data/cost_daily.json` / `data/cost_brake_state.json`） | `.gitignore`（改） | +5 |
| 13 | 本验收报告 | `docs/zh/CloudPivot_v7.2重构计划/TASK-S5-03_验收报告.md` | — |

---

## 2. 验收清单逐条核验（任务书 §四，11 条）

### ✅ 2.1 日熔断：超 `budget.daily_cents` → 停非关键 outbound；次日自动恢复（演练通过）

| 子项 | 结论 | 证据 |
|---|---|---|
| 超阈值触发熔断 | ✅ | 演练 A2：`day_breaker_open=true`，当日 180.0 > 预算 100.0 cents |
| **只停非关键** outbound | ✅ | 演练 A3：被拦 = `shadow/digestion/internalize/reprobe/speculative/background`（6/6 全停） |
| **关键路径不受影响**（硬约束 1） | ✅ | 演练 A4：放行 = `interactive/user_request/approval_pending/approval/task_execution/safety`（6/6 放行） |
| 恢复时点可观测 | ✅ | `resume_at=2026-09-15T00:00:00+08:00` |
| 次日 00:00 自动恢复 | ✅ | 演练 A7：次日 `day_breaker_open=false`、当日成本归零；`healing.triggered` 出现 `action=resume` |
| 审计 + 事件留痕 | ✅ | 演练 A6：事件流含 `healing.triggered`(1) + `metrics.delta`(1)；链式审计 `cost.daily_breaker.opened/recovered`（`audit_seq` 非零） |
| 单测覆盖 | ✅ | `TestDailyBreaker` 14 例（含"未超阈值零影响"、"默认 kind 恒放行"、干跑不拦截、预算撤下释放） |

**熔断事件载荷（演练实测，节选）**：

```json
{"type": "cost.daily_breaker", "severity": "high", "kind": "day_breaker",
 "from": "closed", "to": "open", "day": "2026-09-14",
 "daily_cost_cents": 180.0, "daily_budget_cents": 100.0,
 "resume_at": "2026-09-15T00:00:00+08:00",
 "action": "stop_non_critical_outbound"}
```

**拦截原因文案（演练实测）**：

```
日级成本熔断 OPEN（当日 180.0 > 预算 100.0 cents，2026-09-15T00:00:00+08:00 自动恢复），
已停非关键 outbound kind=shadow
```

### ✅ 2.2 断食：进 UTC>1.3× → 降本；出连续 24h≤1.1×；冷却 12h（状态机用例）

| 子项 | 结论 | 证据 |
|---|---|---|
| 进（比值 > 1.3×，**持续判定**） | ✅ | 演练 B1：`ratio=3.0 > 1.3` → `state=fasting`；单测 `test_single_spike_does_not_enter` 证单点尖峰不触发 |
| 出需**连续** 24h ≤ 1.1× | ✅ | 演练 B7（12h 不退出）/ B8（24.00h 退出） |
| 冷却 12h | ✅ | 演练 B8 进入 cooldown；B10 冷却期内高比值**不重进**；B11 12h 后回常态 |
| 三态可观测（状态/进入退出时间/阈值来源） | ✅ | `fasting.state/entered_at/exited_at/cooldown_until/threshold_source/transitions/last_reason`（演练 B2 全字段打印） |
| 审计 + 事件留痕 | ✅ | `cost.fasting.entered/exited` 入链式审计；`metrics.delta{metric=cost.fasting.state}` |
| 时间类断言**注入时钟** | ✅ | 状态机为纯逻辑 + `now` 参数；单测全部传 `at(n, hour)`，零墙钟依赖（上游坑 #2） |
| 单测覆盖 | ✅ | `TestFastingMachine` 12 例 + `TestFastingIntegration` 12 例 |

### ✅ 2.3 断食期 S3 shadow 预算联动（归零/减半生效）

| 子项 | 结论 | 证据 |
|---|---|---|
| 归零**实际生效** | ✅ | 演练 B4：断食期 `shadow.daily_budget(1000.0) == 0`（常态 50） |
| **穿透低流量保底**（关键） | ✅ | 演练 B4b：`daily_budget(3.0) == 0`。若在保底前乘系数，§4.5 的 `min_budget=1` 会把影子任务放回来，与 §6.7「冻结非关键消化」相悖——已在 `daily_budget()` 中把系数**置于保底之后** |
| 减半可配 | ✅ | `CP_BUDGET_SHADOW_FACTOR_FASTING=0.5` → `daily_budget(1000)=25`（单测 5 例） |
| 联动可逆 | ✅ | 演练 B12/B13：退出断食后 `daily_budget(1000)` 回到 50 |
| 「为什么今天预算是 0」可解释 | ✅ | `ShadowPlan.cost_factor` 落进 `to_dict()/formula`（§1.5⑥ 不可做不可见之事） |
| 未开启时零影响（回归保护） | ✅ | `test_default_factor_is_no_impact` 断言 `daily_budget(1000/200/3/0)` 逐字不变（50/30/1/0） |
| 消化/内化/重探联动 | ✅ | 演练 B5 `digestion_restricted()==(True,'断食降本模式')`；`internalize.register_internalize_job` / `gate.register_reprobe_job` 的 `_tick` 跳过并回 `{"status":"suppressed"}` |

### ✅ 2.4 θ 分阶段阈值按阶段生效（配置驱动，无硬编码）

| 子项 | 结论 | 证据 |
|---|---|---|
| W1-W4 不设 | ✅ | 演练 C1：`θ=None`（§6.3） |
| W5-W9 ≤1.5×→1.3× | ✅ | 演练 C2：W5 首周 `1.5` → W9 末周 `1.3`（阶段内线性收紧） |
| W10-W14 ≤1.0× / M4-M6 ≤0.8×→0.6× / M7+ ≤0.5× | ✅ | 演练 C 表 W9/M4/M7+ 三行；单测 `TestThetaStages` 15 例 |
| 阶段推断无空档 | ✅ | 月 ≥7→`m7_plus`；月 4-6→`m4_m6`；周 ≥10→`w10_w14`；周 5-9→`w5_w9`；其余 `w1_w4`（W15≈第 4 月，月优先于周，已单测） |
| **配置驱动（无硬编码）** | ✅ | 优先级 `CP_BUDGET_THETA_TABLE`(env) > `config.yaml:budget.theta` > §6.3 规格默认表；`theta_source` 进输出可追溯；单测 5 例（含非法 JSON/未登记阶段/负值/部分覆盖合并） |
| θ 参与收紧断食进阈值 | ✅ | `CP_BUDGET_THETA_BINDS_FASTING`（默认 true）取**更严者**；`entry_threshold_source` 显式标注来源（单测 2 例） |
| θ 上限违例披露 | ✅ | `theta_breached` + `theta_ceiling_cents` 进 `status()`/`cost_daily_view()` |

### ✅ 2.5 `PERF_BUDGET_REBASED.md` 基于实测数据（引用压测报告），替代 §11.2 假设值

交付 `docs/PERF_BUDGET_REBASED.md`：

- §11.2 **18 条**逐条给出「原假设 → 重定实测值 → 依据（文件:字段）→ 状态（✅/⚠️/❌/❓）」；
- 点名要求的四类**全部用实测替换**：**reranker/embedding 内存**（ONNX 峰值 **1156.57 MB** / PyTorch **1904.1 MB**）、
  **RRF 技能库检索 5000 技能**（P99 **44.78 ms** ON / **94.07 ms** OFF）、
  **向量库 P99**（sqlite-vec KNN P99 **11.29 ms**）、**etcd P99**（**6.408 ms**，另注明 16.961 ms 的矛盾来源）；
- **本轮补测**（`scripts/bench_s5_03_cost_brake.py`）：事件吞吐 **1631.3 events/s**（超 P7.2-22 的 1000 阈值 1.63×）、
  路由 P99 **0.0028 ms**、以及本任务新增组件的自身预算；
- ⚠ **诚实边界**：**状态灯 / 首屏 / Watchdog / 熔断"达阈值→生效"** 四项**仓库内无实测**，
  文档明确标为 `❓ 未找到实测` 并给出复测方案，**未编造任何数字**；
- §四 记录**9 条来源矛盾/引用陷阱**（如组装耗时两个口径差 3 个数量级、RRF 5000 三个数字的参数差异、
  空表格不可引用、外推值不可当实测、`stress_report_*.json` 是 HTTP 吞吐非 events/s）。

### ✅ 2.6 审批衰减率指标可计算（披露不考核）

| 子项 | 结论 | 证据 |
|---|---|---|
| 占比可计算 | ✅ | 演练 D1：20 条审批中 16 条自动消化 → `decay_rate=0.8`、`meets_target=true`（目标 0.70） |
| 口径明确、可追溯 | ✅ | `definition` 字段写明"无需人工即完成的审批决策数 / 全部审批决策数"；分母=去重后 `approval` 事件数；分子=`kind∈AUTO_APPROVAL_KINDS` 或 `actor∈{auto,system}` |
| 样本不足如实标注 | ✅ | 演练 D3：次日仅 3 条 → `insufficient_sample=true`（阈值 20），**不据此下结论** |
| **只披露不考核** | ✅ | `disclosure_only=true`，无任何阻断行为（单测 `TestApprovalDecay` 5 例） |
| 附带披露（供人读"是自动消化还是人工疲劳"） | ✅ | `fatigue_buckets`（§6.1 五桶）+ `latency_median_ms` |

### ✅ 2.7 既有成本/预算套件零回归；新增单测全绿、覆盖率 ≥80%

见 §4「质量证据」。

### ✅ 2.8 熔断/断食演练记录在验收报告

见 §3（本报告即为记录；`scripts/demo_s5_03_cost_brake.py` 可一键复跑）。

### ✅ 2.9 【S2-03 #5】归一化系数裁定结论已落文档，落地方案与裁定一致

裁定 C = **沿用锚价系数表，本次不校准**；落地：

| 落地点 | 内容 |
|---|---|
| `agent/observability/utc.py` | `CALIBRATION_VERSION="price_anchor.v1"`、`CALIBRATION_NOTE="当前口径＝价格锚定系数…**未经经验校准**"`、`CALIBRATION_TRIGGER`（**触发条件：待 S5-02 L2 Core-50 基线就绪后启动校准评估，建议季度复校**）、`calibration_block()` |
| 指标输出标注 | `utc_daily/utc_window/utc_weekly/utc_snapshot/coefficient_table` **全部**带 `calibration` 块（`calibrated=False`、`anchor_model/source`、`source_of_truth`） |
| `agent/monitoring/cost_brake.py` | `COST_SCHEMA_VERSION="s5-03.v1"` + 同一 `CALIBRATION_VERSION`；`status()`、`cost_daily_view()`、`shadow_overhead_audit()`、`approval_decay_rate()` 输出均带 |
| `docs/PERF_BUDGET_REBASED.md` | §三 明确标注本任务组件口径版本；§七 §13.3 登记 |
| 未做 | **未启用校准流程**、未引入经验系数——与裁定逐字一致（`calibrated=False` 可机器校验） |

### ✅ 2.10 【S2-03 #9】双成本轨处置结论落地（收敛到事件流单一数据源）

裁定 D = **收敛到事件流单一数据源**；落地：

| 裁定要求 | 落地 | 证据 |
|---|---|---|
| 事件流为唯一写入/查询来源 | `utc.record_cost()` → `data/events/`；`COST_SOURCE_OF_TRUTH="events"` | 单测 `test_view_reuses_utc_daily_fields` 断言 `source_of_truth=="events"` |
| 旧轨**停写** | `CostTracker.record()` 默认不再落盘（`legacy_write_enabled()` 默认 `False`） | 单测 `test_record_does_not_write_legacy_file_by_default`（断言文件**不存在**） |
| 只读兼容 ≤1 minor | `_load_existing()` 保留；新增 `iter_legacy_records()` / `legacy_records()` | 单测 `test_legacy_records_still_readable`（含坏行容错、断言文件仍在） |
| 归档不删 | 模块**任何路径不删除** `cost_log.jsonl` | 同上断言 `log_path.exists()` |
| 不得静默丢账（适配 D 的"先补报警再收敛"） | 停写期间每次 `record()` 计入 `suppressed_write_attempts`；首次发 **WARNING 日志 + `metrics.delta` 事件 + 链式审计 `cost.legacy_track.write_attempt`**；内存日聚合照常更新 | 单测 `test_suppressed_write_is_alarmed_not_silent`、`test_record_updates_daily_stats_day`（内存统计不丢） |
| **可回滚**（硬约束 5） | `CP_COST_LEGACY_LOG_WRITE=1` 恢复旧写入；非法取值回退"保持停写" | 单测 `test_record_writes_to_file_when_rollback_enabled`、`test_invalid_rollback_value_keeps_stop_write` |
| `reconcile_cost_log()` 降级 | 返回 `role="reconciliation_only"` / `authoritative_source="events"`，docstring 明写"**不再是数据源**" | 单测 `test_reconcile_cost_log_is_reconciliation_only` |
| 文档标注"成本唯一数据源＝事件流" | `utc.py` / `cost_tracker.py` / `cost_brake.py` 模块 docstring + `docs/PERF_BUDGET_REBASED.md` | — |

**"仍有生产调用方"核查（裁定 D 的前置条件）**：全仓 `CostTracker|cost_tracker.` 检索 **103 处命中**，
逐条归类后 **`agent/` `scripts/` `planning/` 内 0 处生产调用**——仅测试、文档、以及模块自带的
`cost_tracker = CostTracker()` 单例（构造即建目录，未调用 `record()`）。故**满足"先补报警再收敛"的前提**，
且报警已作为安全网保留（残留调用方会被立刻发现）。

### ✅ 2.11 成本读取复用 `utc.py::utc_daily()/utc_weekly()`（未另建聚合）

| 位置 | 用法 |
|---|---|
| `CostBrake._evaluate_inner()` | `utc.utc_daily(day)` + `utc.utc_weekly(anchor_day)` |
| `rolling_baseline()` | 逐日 `utc.utc_daily(day)`（**近 7 日，不含当日**），非另建聚合 |
| `shadow_overhead_audit()` / `cost_daily_view()` | 复用已读的 `utc.utc_daily()` 行（`daily_row` 参数避免重复读盘） |
| 反证 | `grep` 确认 `cost_brake.py` 内**无**任何自行遍历事件文件累加成本的代码；所有金额字段逐字来自 `utc` 的返回行 |

---

## 3. 演练记录（**触发 → 恢复实测输出**）

**复跑命令**（无需外部依赖，全部在临时目录内，不污染仓库 `data/`）：

```powershell
python scripts/demo_s5_03_cost_brake.py          # 或加 --keep 保留临时目录查验
```

**演练终态：`检查项：38/38 通过`**（事件流留痕 39 条，均在临时目录）

| 场景 | 检查项 | 结果 |
|---|---|---|
| A. 日级硬熔断 | A1 未超阈值零影响 / A2 触发 OPEN / A2b 恢复时点 / A3 非关键全停 / A4 关键路径不受影响 / A5 默认调用方恒放行 / A6 事件留痕 / A7 次日恢复 / A7b 恢复后放行 / A8 触发与恢复各有留痕 | 10/10 ✅ |
| B. 周级断食 | B1 进入 / B2 可观测 / B3 系数归零 / B4 预算归零 / B4b 穿透保底 / B5 消化抑制 / B6 降本非硬停 / B7 未满 24h 不退出 / B8 24h 退出 / B9 冷却不降本 / B10 冷却不重进 / B11 12h 恢复 / B12 联动可逆 | 13/13 ✅ |
| C. θ 分阶段 | C1 W1-W4 不设 / C2 阶段内收紧 / C3 配置可覆盖 / C4 表来源可追溯 | 4/4 ✅ |
| D. 审批衰减率 | D1 占比可算 / D2 样本充足不误报 / D3 样本不足如实标注 / D4 只披露不考核 | 4/4 ✅ |
| E. 口径审计 | E1 `shadow_overhead_ms` 在 UTC 口径内 / E2 金额与机时分开标注不臆造 / E3 配置费率后折价并参与判定 / E4 日成本视图落盘 | 4/4 ✅ |
| F. 零影响自查 | F1 关闭时不拦任何 kind / F2 降本视图中性 / F3 shadow 预算回到常态 | 3/3 ✅ |

**演练发现并修复的两个真实缺陷（不是演练脚本问题）**：

1. **联动点看不到注入实例的状态**（B4/B5 首轮 NG）——首轮演练用注入的 `CostBrake` 实例，
   而 S3-03 联动点（`shadow_budget_factor()` / `digestion_restricted()`）查询的是**进程单例/持久化状态**，
   于是"断食了但影子预算没归零"被演练暴露。**修订**：演练改为走 `CP_BUDGET_*` + `get_cost_brake()`
   的**生产路径**（这正是运维会走的路径），并保留"注入实例供测试"的 DI 能力。
   教训：用注入实例演练联动会**假绿**。
2. **断食状态双事实源**（B12 首轮 NG）——状态机被直接推进后，`status()`/`suppression()` 仍读
   **上次判定的快照**，报旧状态。**修订**：`status()` 与 `suppression()` 改为直接读**状态机**（唯一事实源），
   `block_reason()` 同步；`_load_state()` 冷启动时也把持久化状态反映进可见状态。

---

## 4. 质量证据

### 4.1 新增单测与覆盖率

```
pytest tests/unit/test_s5_03_cost_brake.py -p no:randomly -q
→ 107 passed in 9.61s
```

覆盖率（`--cov=agent.monitoring.cost_brake`）：

```
agent\monitoring\cost_brake.py     891 stmts    103 miss    88%
```

**88% ≥ 80%**（任务书 §二 步骤 5 要求 ≥25 例 / ≥80%）。用例分布：

| 测试类 | 覆盖内容 | 例数 |
|---|---|---|
| `TestConfig` | 默认关闭、非法布尔/数值回退、优先级、来源记录 | 13 |
| `TestThetaStages` | 阶段推断（含无空档/月优先）、θ 收紧、配置覆盖与非法回退 | 15 |
| `TestFastingMachine` | 三态跃迁、持续判定、24h 出、12h 冷却、防抖、快照往返与容错 | 11 |
| `TestDailyBreaker` | 触发/恢复/关键豁免/默认不误伤/干跑/持久化/事件审计 | 13 |
| `TestFastingIntegration` | 断食进降本、shadow 联动、θ 收紧进阈值、UTC 信号变体 | 13 |
| `TestZeroImpactWhenDisabled` | **总开关关闭时零影响**（不拦/不发事件/不写状态） | 6 |
| `TestFacadeBehaviour` | 判定异常 fail-open、守卫抛错、单例与 reset | 5 |
| `TestCalibrationAndShadowOverhead` | 口径版本标注、shadow 机时折价与判定参与 | 5 |
| `TestApprovalDecay` | 占比、目标、样本不足、疲劳桶、重放不重复计数 | 5 |
| `TestShadowBudgetLinkage` | shadow 预算归零/减半/穿透保底/默认无影响/端到端 | 7 |
| `TestDigestionSuppression` | 消化抑制查询、未开启不干预、查询失败不抑制 | 4 |
| `TestCostDailyView` | 日成本视图字段/预算标记/口径标注/落盘与 best-effort | 5 |
| `TestLegacyTrackConvergence` | utc 侧口径标注、对账角色降级、旧轨状态、快照指向实现 | 5 |

**既有旧轨用例改造**（`tests/unit/test_misc_modules_comprehensive.py`，`TestCostTrackerRecord` 9 例）：
按裁定 D 改为**双路径覆盖**——默认停写断言（不落文件 + 有告警）与回滚开关下的**原有写入/计价断言逐条保留**，
并把"重新加载验证持久化"的原路径原样保留在 `test_cost_tracker_full_lifecycle_with_rollback`。
即：**写入逻辑仍在、仍被覆盖**，只是默认被授权停写。

### 4.2 邻接回归

```powershell
# 本任务相关套件 + 邻接（ACR / monitoring / model_router / digestion 同源）
pytest tests/unit/test_utc_cost.py tests/unit/test_acr_metrics.py tests/unit/test_s5_03_cost_brake.py -q
→ 174 passed
pytest tests/unit/test_digestion_shadow.py tests/unit/test_s5_03_cost_brake.py -q
→ 193 passed
pytest tests/unit/test_misc_modules_comprehensive.py tests/unit/test_model_router.py tests/integration/test_model_router_cost.py tests/unit/test_utc_cost.py -q
→ 134 passed
```

**全量（6 分片，复刻 CI 口径）** —— `pytest <shard-files> -p no:randomly -q -n 2 --dist=loadscope`：

| 分片 | 结果 | 耗时 | 含本任务改动文件 |
|---|---|---|---|
| Shard 1 | **1 failed** / 2321 passed / 11 skipped | 487.1s | 否 |
| Shard 2 | **2340 passed** / 4 skipped | 169.0s | ✅ `test_utc_cost.py` |
| Shard 3 | **2260 passed** / 130 skipped / 1 xfailed / 4 xpassed | 280.6s | ✅ `test_acr_metrics.py`、`test_digestion_shadow.py` |
| Shard 4 | **2227 passed** / 89 skipped | 107.2s | 否 |
| Shard 5 | **2419 passed** / 49 skipped | 92.1s | 否 |
| Shard 6 | **2368 passed** / 10 skipped / 12 xfailed | 106.1s | ✅ `test_misc_modules_comprehensive.py`、`test_model_router.py` |
| **合计** | **13935 passed / 1 failed / 293 skipped / 13 xfailed** | — | — |

**唯一 1 处 failure 的归因（结论：环境/基础设施，非测试断言失败）**

- 该 failure 位于 **Shard 1**，而 **Shard 1 的文件集不含本任务改动的任何文件**
  （本任务改动的测试文件分布：Shard 2 = `test_utc_cost.py`；Shard 3 = `test_acr_metrics.py`、
  `test_digestion_shadow.py`；Shard 6 = `test_misc_modules_comprehensive.py`、`test_model_router.py` —— **三者全绿**）。
- **两次独立复跑均无法归因到任何测试**：
  - 带 xdist 复跑（`-n 2 --dist=loadscope -rf`）→ **未产出任何 `FAILURES` 段与 `FAILED <nodeid>` 行**，
    却在尾部抛出 **pytest-xdist 的 `INTERNALERROR`**：`KeyError: <WorkerController gw3>`
    （`xdist/scheduler/loadscope.py:_assign_work_unit`）——即 **worker 进程崩溃**，不是断言失败；
  - 不带 xdist 复跑（去掉 `-n 2`）→ 进程在 **`import torch` 的 pytest-timeout** 处被杀死
    （栈尾 `torch/_functorio/_aot_autograd/... → dataclasses._recursive_repr` + `Timeout` 横幅）。
- **根因**：本机同时承载多波次并行会话的测试负载（实测进程证据：`-m pytest tests/unit -n 4 --dist=loadscope`、
  `-m pytest tests --ignore=tests/unit -n 4`、`-m pytest tests/unit -m "not slow and not skip_ci..."` 等）。
  重依赖导入（torch / sentence_transformers / chromadb）在争用下阻塞超时、worker 被回收 ——
  与 `.github/workflows/ci.yml` 记录的历史失败模式（`shutdown signal` / `lost communication` /
  `can't start new thread`，**均 0 测试失败**）**完全同型**。
- **可复现反证（同一 workspace、同一时刻）**：`tests/unit/test_preflight_runner.py` 在 shard 全量中失败，
  其 3 个 CLI 子进程用例在**未修改的 `master` 主工作区同样失败**（`10 passed / 3 failed`），
  根因为本会话 shell 沙箱的**管道 stdio 边界**（子进程 stdout/stderr 捕获不可用，`proc.stdout is None`）；
  `tests/unit/test_distill_feedback.py` 在单进程全量中成片 `E`，**单独运行 18/18 全绿**；
  `tests/unit/test_error_handler.py` 单进程全量中成片 `E`，**单独运行 333 passed / 3 skipped / 0 failed**。
- **结论**：本机全量口径**不构成有效否决证据**；全量终态以 **CI（干净隔离 runner）** 为准。

> **为什么不用单进程全量**：
> ① 本仓库 `tests/unit` 单进程运行会触达资源上限——`.github/workflows/ci.yml` 有明确历史记录
> （runner `shutdown signal` / `lost communication` / `can't start new thread`，均为 **0 测试失败**的环境问题），
> CI 本身即拆为 **6 shard**（`scripts/split_unit_tests.py --shard N --shards 6` + `-n 2 --dist=loadscope`）。
> ② **本轮实测到的额外干扰**：本任务与 S4-01/S4-02/S5-01/S5-02 **同一波次并行开工**，
> 同一台机器上**同时有 3 个兄弟会话在跑各自的 `tests/unit` 全量**（实测进程证据）：
>
> ```
> 16776 | 02:01:06 | -m pytest tests/unit/ -m "not slow and not skip_ci and not forked_incompatible" -p no:randomly -q --timeout=30
> 11192 | 02:01:10 | -m pytest tests/unit -m "not slow" -p no:randomly -q -n 4 --dist=loadscope --no-header
> 20612 | 02:01:16 | -m pytest tests/unit -m "not slow" -p no:randomly -q --no-header -rf
> 18220 | 02:02:52 | -m pytest tests/unit/test_system_tools_core.py tests/unit/test_memory_refactor.py ...   ← 本任务分片
> ```
>
> 因此单进程全量出现的**大量 `E`（collection/setup 阶段错误）是环境假象，不是代码缺陷**，
> 可直接证伪：`tests/unit/test_distill_feedback.py` 在单进程全量中**全 `E`**，
> 而**单独运行 18/18 全绿**（同一 workspace、同一时刻）。同类现象亦见 `test_error_handler.py`
> （单进程全量中成片 `E`；单独运行 **333 passed / 3 skipped / 0 failed**）。
> **结论**：本机的全量口径不构成有效否决证据；**全量终态以 CI（干净隔离 runner）为准**。

### 4.3 本地门禁

| 门禁 | 命令 | 结果 |
|---|---|---|
| kwarg 扫描 #1 | `python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH` | **0 findings / exit 0**（473 文件） |
| kwarg 扫描 #2 | `python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH` | **0 findings / exit 0**（680 文件） |
| mypy（新增模块） | `python -m mypy agent/monitoring/cost_brake.py` | **0 error**（1866 行全新文件） |
| mypy（改动模块） | `python -m mypy agent/observability/utc.py agent/model_router/cost_tracker.py agent/digestion/{shadow,internalize,gate}.py` | **无新增错误**：`cost_tracker.py`/`shadow.py`/`gate.py` 0 error；`utc.py` 2 处、`internalize.py` 1 处**均为既有**（在 `master` 主工作区同位置复现，仅行号因新增行位移） |
| importlinter | `python -m importlinter.cli lint --config .importlinter` | **exit 0（kept / 0 broken）** |
| 产物漂移 | `git status`（跑完门禁） | 检出 `tests/contract/contracts/*.json` 6 文件仅 `generated_at` 时间戳漂移 → **已 `git checkout --` 还原**（见 §4.4） |
| 运行时产物 | `data/cost_daily.json` / `data/cost_brake_state.json` | 真实路径落盘已验证（`data/cost_daily.json` 内容含 `calibration_version=price_anchor.v1`、`source_of_truth=events`）；二者已入 `.gitignore`（与 `data/utc_snapshot.json` 同性质），**不产生 `??` 漂移**，演练/测量脚本默认走临时目录 |

### 4.4 既有行为不变的证据（"不改公开接口签名与行为"）

| 接口 | 变更 | 保持 |
|---|---|---|
| `CostTracker.record(model, input_tokens, output_tokens, duration_ms, task_type, trace_id)` | **签名逐字不变** | 仅**落盘行为**按 Owner 裁定 D 改变（默认停写，可回滚）；内存统计与 `get_summary()` 返回结构不变（仅**新增** `source_of_truth`/`legacy_write_enabled` 两个键） |
| `CostTracker.get_summary()` / `_load_existing()` | 不变 | 旧文件读取路径完整保留 |
| `utc.utc_daily/utc_window/utc_weekly/utc_snapshot/coefficient_table` | **仅新增键** | 既有键与数值逐字不变（`tests/unit/test_utc_cost.py` 全绿） |
| `utc.reconcile_cost_log()` | 返回值**新增** `role`/`authoritative_source`/`calibration` | `rows`/`consistent`/`delta_cents`/`note` 语义不变 |
| `shadow.daily_budget()` | **新增** keyword-only `factor=None` | 默认 `None` → 查询成本刹车；**未开启时恒 1.0**，既有 4 个位置参数与全部默认行为不变（该套件 68 例全绿） |
| `shadow.ShadowPlan` | **新增**字段 `cost_factor=1.0`（带默认值） | 既有构造与 `to_dict()` 兼容（仅新增键） |
| `register_internalize_job` / `register_reprobe_job` | **签名与默认开关不变** | 仅在任务体 `_tick` 增加一个默认"不抑制"的守卫；未开启成本刹车时行为逐字一致 |
| 成本刹车自身 | 全新模块 | 总开关 `CP_BUDGET_BRAKE_ENABLED` **默认关闭** → `allow_outbound()` 恒真、shadow 系数恒 1.0、不发事件、不写状态 |

### 4.5 安全底线（硬约束核验 —— 批次总表 §三）

| # | 硬约束 | 核验 |
|---|---|---|
| 1 | 不改既有公开接口签名与行为；新增机制失败不得阻断主流程 | ✅ 见 §4.4；**fail-open 已单测**：`test_evaluate_failure_is_fail_open`（判定抛错 → 放行 + 状态标 `fail-open`）、`test_digestion_restricted_never_raises`（查询失败 → 不抑制） |
| 2 | 一切自动化开关**默认关闭**，显式阈值才开 | ✅ `CP_BUDGET_BRAKE_ENABLED` 默认 `False`；**非法取值一律回退 False**（`_flag_value()` 不把 `"maybe"` 当 True，已单测） |
| 3 | 单测覆盖率 ≥80%；可调参数走 `.env`，非法值回退默认 | ✅ 88%；全部阈值走 `CP_BUDGET_*`（18 个键）/`config.yaml:budget`，解析期非法值回退并记入 `config_warnings`（可见、不静默） |
| 4 | 术语纪律 | ✅ 本任务未引入"评审"旧语义；沿用"消化/内化/shadow 灰度" |
| 5 | 口径纪律（不夸大） | ✅ 未声称已校准、未声称已有 L2 基线、性能文档对无实测项如实标注 `❓` |
| 6 | 落盘用例必须显式传路径或 autouse 隔离 | ✅ 单测全部 `tmp_path` + 显式 `state_path=""`/`directory=`；`data/` 零漂移；演练/测量脚本走临时目录 |
| 7 | 造数型用例加 `@pytest.mark.timeout` | ✅ 本任务无用例批量 create 30+（最大为 1000 事件吞吐的**脚本**而非用例；16+4 条审批埋点为轻量 append） |

**熔断只停非关键（本任务特有硬约束 1）的三重证据**：
① 结构上 `CRITICAL_KINDS` 在 `allow_outbound()` **最先返回 True**，根本不进入判定；
② 单测 `test_critical_paths_never_blocked`（6 个关键 kind 全放行 + `critical=True` 显式放行）；
③ 演练 A4 在**真实熔断 OPEN 状态**下实测 6/6 关键 kind 放行。

---

## 5. 遗留问题（逐条带归属与阻塞性判定）

| # | 问题 | 归属 | 阻塞性 |
|---|---|---|---|
| 1 | **任务书引用了不存在的文件**：步骤 1 提到"既有成本监控脚本 `verify_budget_break.py`"与"归一为 `data/cost_daily.json`"。实测：`verify_budget_break.py` **在仓库中不存在**（既有的是 `scripts/verify_budget_degrade.py`）；`data/cost_daily.json` 亦非既存产物（S2-03 交付的是 `data/utc_snapshot.json`）。 | 本任务已在**报告内如实说明**，并实现 `cost_daily_view()`/`write_cost_daily()` 产出该文件（内容复用 `utc.utc_daily()`） | ❌ 不阻塞（已按意图补齐交付物） |
| 2 | **状态灯 / 首屏 / Watchdog / 熔断"达阈值→生效" 四项无实测** | `PERF_BUDGET_REBASED.md` §五 已给复测方案；状态灯/首屏建议随 **S6-01 六面板扩展**补测 | ❌ 不阻塞（已如实标 `❓`，未编造） |
| 3 | **`CostBrake.evaluate()` 开销随事件文件线性增长**（2000 条/日 ≈ 100 ms；≈ 50 µs/事件，单次全文件扫描无索引）。当前由 `CP_BUDGET_EVAL_MAX_AGE_SECONDS`（默认 60s）限频掩盖（稳态 ≤0.6 ms/s 均摊）。 | 事件存储演进（与 S2-03 事件流同源）；触发阈值：**单日事件 > ~10 万条**时改为按日分片读取或引入索引 | ❌ 不阻塞（已量化并写入 PERF 文档 §三） |
| 4 | **`agent/monitoring/` 与 S4-03 落点重叠** | 本任务仅**新增** `cost_brake.py`，未改动 `agent/monitoring/` 既有文件（`git diff` 可证） | ❌ 不阻塞（重叠面收窄为"同目录不同文件"） |
| 5 | **断食/熔断的周期性评估需要调度点**：`evaluate()` 需被调度器/运维命令周期触发（`allow_outbound()` 有惰性兜底：状态超龄即重算，但只在有非关键调用方时发生）。 | 建议随 S6-01（六面板）或部署编排接入 `evaluate_brakes()`；本任务已提供模块级入口 | ❌ 不阻塞（惰性兜底已保证不会"永不判定"） |
| 6 | **校准评估触发条件**（裁定 C 后续项） | **S5-02 L2 Core-50 基线就绪后**启动；已固化为 `CALIBRATION_TRIGGER` 常量 + 文档，可机器检索 | ❌ 不阻塞（按期触发） |
| 7 | **`reconcile_cost_log()` 的"下线"选项未执行** | 按裁定 D 保留为纯对账工具（≤1 minor 只读兼容），未下线 | ❌ 不阻塞 |
| 8 | 上一轮门禁产物漂移（`tests/contract/contracts/*.json` 的 `generated_at`） | 仓库既有现象（非本任务引入）；本轮已 `git checkout --` 还原 | ❌ 不阻塞 |

---

## 6. 口径与诚实披露

1. **未声称"已校准"**：`calibrated=False`，`CALIBRATION_VERSION="price_anchor.v1"`。归一化仍为价格锚定系数，
   校准评估**尚未启动**（尺子 L2 Core-50 不存在）。
2. **未声称"成本已真实接线"**：旧轨确为生产零调用方；本任务按裁定停写，但**真实记账点接线上游已完成**
   （S2-03 的 `LLMMonitor.record()` → 事件流）。成本刹车的判定**依赖事件流有数据**，
   冷启动（无数据）下 `ratio=None` → **不推进状态机**（不臆断，已单测）。
3. **未声称"事件吞吐已在生产验证"**：1631.3 events/s 是**本机单进程批量 emit** 实测，
   非生产负载；且该值与 `data/stress_report_*.json` 的 HTTP 吞吐**不同口径**（已在 PERF 文档 §四⑥ 警示）。
4. **未声称 shadow 机时已计价**：`shadow_overhead_ms` **已纳入 UTC 聚合与判定口径**，
   但机时→金额的换算率 `CP_BUDGET_SHADOW_MS_CENTS_PER_S` **默认为 0（未折价）**——
   未配置机时费率时不臆造金额，输出显式标 `priced=false` + 说明缺口（已单测/演练 E2）。
5. **断食信号口径**：§7 正文与任务书均为"**日均成本 / 基线**"，故默认 `fasting_signal="daily_cost"`；
   另提供 `utc_per_task` 变体（`status()` 同时输出 `utc_ratio` 供交叉参考），**默认不启用**。
6. **θ 与断食的关系**：θ 是 §6.3 的 UTC 上限；默认 `theta_binds_fasting=true` 表示
   "θ 严于 1.3× 时取更严者"，此为可配置的工程选择（已在文档与 `entry_threshold_source` 显式标注），
   **不是**设计文档的逐字规定。
7. **未做的事（明确声明）**：未接入真实 LLM outbound 调用点做**在线**拦截
   （本任务交付的是门禁 API + 联动点；真实调用点接线需在各 outbound 调用方声明 `kind`，
   已在 `BACKGROUND_KINDS` 给出契约）；未实现断食期的**通知降级**（§P7.2-21，另属 UI 批）。

---

## 7. 结论

任务书 §四 验收清单 **11/11 条全部满足**；演练 **38/38 检查项通过**；
新增 **107 例单测全绿、`cost_brake.py` 覆盖率 88%**；相关/邻接套件**零回归**；
kwarg 扫描（agent + tests 两条）、mypy（新增模块 0 error / 改动模块无新增）、
importlinter（kept / 0 broken）**全绿**；门禁产物漂移**已还原**；
Owner 裁定 **C / D 的落地证据可机器校验**（`calibrated=False`、`source_of_truth="events"`、
`legacy_write_enabled()=False`、`role="reconciliation_only"`）。

**遗留 8 项，全部不阻塞**，其中 2 项已在报告内如实纠正上游文档的陈旧引用（`verify_budget_break.py` /
`data/cost_daily.json`），其余带明确归属与触发条件移交。

---

## 8. 推送与 CI（终态）

| 项 | 值 |
|---|---|
| 代码提交 | `00d285ca` |
| 合并提交（master） | `b656545a` → 本任务写入时终态 `d819e13c` |
| 双远端同点 | ✅ `git ls-remote origin/gitee master` 与本地 `master` **三者同为 `d819e13c`**（实测）；本任务改动 `00d285ca`/`15f6f2a3` 经 `git merge-base --is-ancestor` 确认为其**祖先**（波次内兄弟会话继续推送会使 tip 前进，不改变该事实） |
| 合并后校验 | main 工作区（master 树）复跑 3 套件 → **251 passed / 0 failed** |
| CI（`c20d59aa`） | 14 运行：12 success / 1 in_progress / 1 failure；唯一失败 workflow（云枢系统测试流程）的 **3 个失败 job 已逐条归因**：2 个为 `00_总览` 第 279 行对 **S5-02** 未交付报告的悬空引用（该字符串在 `808beb90` 即存在，目标文件全历史 0 commits）；1 个为 **S5-01** 的 `test_memory_tenancy.py` 在 Linux 上的路径大小写归一缺陷（本机 Windows 61/61 全绿）。**均非 S5-03**，S5-03 相关文件所在 job 未失败。详见交付结案报告 §5 |
