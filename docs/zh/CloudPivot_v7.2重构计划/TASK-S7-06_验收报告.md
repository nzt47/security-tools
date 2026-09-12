# TASK-S7-06 验收报告 —— 残留清理（判定集成本入 ROI / 委派成功率机械信号 / 单机降级设施表）

> 任务书：[`TASK-S7-06_残留清理.md`](TASK-S7-06_残留清理.md)｜批次总表：[`PARALLEL_S7批次总表.md`](PARALLEL_S7批次总表.md)
> 上游依据：[`V72_全计划收官审计报告.md`](V72_全计划收官审计报告.md) §8.5 残留 **R1 / R4 / R7**（§三 T1/T7、§六 缺口 3/8）
> 基线：`master` / `531515e0`｜worktree：`s706`（分支 `s706/main`）｜验收日期：2026-09-12
> 预估：3–4 人日｜依赖：S3-03 / S4-04 / S5-02 / S4-02 / S4-03（**均已结案**）

---

## 一、结论速览

| 残留 | 问题（审计原文摘要） | 本任务交付 | 判定 |
|---|---|---|---|
| **R1** | 判定集构建成本未计入 ROI 公式（T1 残留） | `stage=case_build` 成本埋点 + `ROIReport.case_build_cost_cents`（区间 + 方法 + 样本）+ **不含/含两种 ROI**（默认单列披露、不参与摊销） | ✅ **已清理** |
| **R4** | 委派成功率的"机械信号清单"未单独显式化（T7 残留） | 5 条优先级清单（**代码即清单**）+ 复评半边「机械优先 + LLM 兜底」带 `signal_kind` + 指标 `mechanical`/`llm` **两列严禁混算** | ✅ **已清理** |
| **R7** | 单机降级设施未汇总为单一表格（§5-8 残留） | [`../单机降级设施表.md`](../单机降级设施表.md)：7 项 × 四要素 + **21 条可追溯断言（已验证 13 / 未验证 8）** | ✅ **已清理** |

**测试总账**：新增 3 个套件 **97 例全绿**（28 + 57 + 12）；邻接三套件（`digestion` / `subagent` / `eval`）**零回归**。
**口径纪律**：估计值一律带方法与样本；无数据记 `None`（不以 0 冒充）；未验证项**不写成已验证**。

---

## 二、R1：判定集构建成本计入 ROI

### 2.1 采集（埋点存在且可查，含 `stage=case_build`）

| 项 | 落点 | 证据（单测） |
|---|---|---|
| 埋点位置 | `agent/digestion/cases.py::CaseStore.save()`（判定集落库唯一漏斗；**仅新版本**记录） | `test_save_new_version_records_cost_event` |
| 事件标注 | 载荷 `stage="case_build"` / `cost_class="case_build"` | `test_event_carries_stage_case_build` |
| 独立成本流 | `<判定集根>/_case_cost/`（`CP_DIGESTION_CASE_COST_DIR` 可覆盖） | `test_cost_dir_is_derived_from_case_root`、`test_cost_stream_is_independent_from_default_events` |
| 幂等 | 同版本重复落库不重复计费；漂移重生成（新版本）各自计费 | `test_same_version_resave_is_not_double_counted`、`test_regenerate_records_new_version_cost` |
| 通道归因 | 由用例 `kind` 自动归因（seed / trace / llm / manual） | `test_channel_attribution_by_case_kind`、`test_llm_channel_is_attributed_and_priced` |
| 不阻断构建 | 埋点失败仅告警，判定集照常落库 | `test_record_cost_failure_never_breaks_build` |

**原样输出（可复现）**

```powershell
> python -m pytest tests/unit/test_s7_06_case_build_cost.py -q
28 passed

> python -c "from agent.digestion import case_cost as CC; import json; print(json.dumps(CC.case_build_cost_ledger(), ensure_ascii=False, indent=1))"
[
 {"ts": "...", "event_id": "ev_...", "capability_id": "cp.builtin.read_file",
  "version": 1, "cases": 4, "channels": {"seed_pack": 4},
  "total_cents": 0.0, "manual_review_minutes": 0.0, "replay_cpu_ms": 0.0,
  "stage": "case_build"}
]

> python -c "from agent.digestion import case_cost as CC; import json; print(json.dumps(CC.case_build_cost_window(), ensure_ascii=False, indent=1))"
{"available": true, "total_cents": 0.0, "low_cents": 0.0, "high_cents": null,
 "events": 1, "samples": 1, "cases": 4, "llm_cents": 0.0, "manual_cents": 0.0,
 "replay_cents": 0.0, "explicit_cents": 0.0, "by_channel": {"seed_pack": {"cases": 4, "events": 1}},
 "lower_bound": true, "estimated": true,
 "estimation_method": "人工复核工时：**未登记**（构建路径未记录工时）⇒ 点值不含人工成本，属**下界**（不臆造工时时长）",
 "source": "agent.digestion.case_cost（cost 事件 stage=case_build）", ...}
```

### 2.2 双口径 ROI（**同时给出**不含 / 含）

设 `月省 = (上游单位成本 − 自研单位成本) × 月样本数`、`一次性投入 I`、`判定集构建成本 B`：

| 口径 | 摊销 | 净月收益 | ROI 为正 | 用途 |
|---|---|---|---|---|
| **不含**（与 S3-03 逐字一致） | `I / 12` | `月省 − I/12` | `月省 > I/12` | **条件③判定**（`score_roi` 只用它） |
| **含**（透明对照） | `(I + B) / 12` | `月省 − (I+B)/12` | `月省 > (I+B)/12` | **仅披露**（不参与条件③） |

**对照输出（测试数据：月样本 200、上游 0.5 分/次、一次性投入 100 分、判定集 60 分 + 未计价人工 25 分钟）**

| 口径 | 摊销基数（分/月） | 净月收益（分） | ROI 为正 |
|---|---|---|---|
| 不含判定集成本 | 8.333333 | 91.666667 | True |
| 含判定集成本（口径②，默认**不**计入摊销） | 8.333333 | 91.666667 | True |
| 含判定集成本（口径①，显式 `case_build_in_amortization=True`） | 13.333333 | 86.666667 | True |

**判别性用例**（`test_can_flip_including_basis_while_excluding_stays_positive`）：月省 10 分、判定集成本 1200 分 ⇒
**不含口径为正**（10 > 1/12）、**含口径为负**（10 < 1201/12）—— 两种数字并列可见，读者不需猜口径。

**为什么选口径②**（任务书 §二"建议"）：判定集是**一次性资产**，混入摊销会抬高月成本、
系统性压低内化意愿；无论默认取值如何，**含/不含两种 ROI 始终同时出现**（透明可对比）。

### 2.3 估计方法与样本（**不得编造数字**）

| 场景 | 输出行为 | 断言 |
|---|---|---|
| 无事件 | `total_cents=None` + 方法标签写明"不可得（记 None，不以 0 冒充）" | `test_no_events_is_none_not_zero` |
| 有工时但单价未配置 | 点值为**下界**、`high_cents=None`（不可估）、方法串标注 env 名 | `test_unpriced_manual_hours_make_lower_bound` |
| 配置单价（30 分/分钟） | `high_cents = 0 + 25×30 = 750.0` | `test_configured_rate_yields_estimable_range` |
| 非法单价 | 回退"未配置"（不猜费率） | `test_illegal_rate_falls_back_to_unpriced` |
| 工时未登记 | 方法串如实写"点值不含人工成本" | `test_manual_unrecorded_is_disclosed` |
| 每条汇总 | 必带 `source` / `samples` / `estimation_method` / `caveats` | `test_every_summary_carries_source_and_samples` |

**口径文档**：[`../判定集构建成本入ROI口径说明.md`](../判定集构建成本入ROI口径说明.md)（含"为什么不进 `data/events/`"、
区间语义、配置项、复现命令）；`docs/PERF_BUDGET_REBASED.md` 新增 **§九**声明"判定集构建成本**非**性能预算，
且不污染 `utc.utc_window()` 口径"。

### 2.4 兼容性（不改既有公开接口行为）

| 面 | 断言 |
|---|---|
| 既有 ROI 字段语义 | `test_both_rois_are_reported`（`amortized_monthly_cents` / `net_monthly_cents` / `positive` 与 S3-03 同值） |
| 条件③ 判据 | `test_score_roi_uses_excluding_basis` |
| 证据往返 | `test_roi_report_round_trips_through_evidence_dict`（`ROIReport(**data["roi"])` 不丢新字段） |
| ROI 面板 | `agent/ui_panels/data.py` **兼容叠加**（旧键原样保留）；`test_s6_01_ui_panels.py` **76 例全绿** |

---

## 三、R4：委派成功率机械信号清单

### 3.1 清单（5 条显式定义 + 优先级 + 适用条件）

**单一事实源**：`agent/subagent/mechanical.py::SIGNAL_SPECS`（**代码即清单**；指标字典**引用**而非复制）

| 优先级 | 信号 | 判定物 | 适用条件（不满足 ⇒ **不适用**，降级下一条） | 通过条件 |
|---|---|---|---|---|
| ① | `artifact_structure` | 产物是否结构性通过 `task_file` ⑤产物格式（schema / 必需字段） | 格式声明为**机器可验**形态（dict `required_fields`/`schema` 或 JSON 对象串）**且**有产物 | 产物非空且逐条满足字段名与类型约束 |
| ② | `test_transition` | 涉代码委派的**目标用例 fail→pass** | 给出 `{before, after}` 或 `{failing_ids, passed_ids}` | `after == pass`（逐号形态要求失败 id 全部转通过） |
| ③ | `side_effects` | **声明副作用 vs 实际副作用**集合一致 | 给出两集合（映射形态）或 S3-02 比对结果 | S3-02 `_sets_match` 语义（具体值优先、`${path}` 形态容忍） |
| ④ | `replayability` | 产物**可重放复现** | ≥2 次重放指纹或 `{runs, identical}` | ≥2 次且结果全同 |
| ⑤ | `llm_review` | 兜底复评（ReflectionEngine / 规则复评） | **①-④ 全不适用**（唯一触发条件） | 复评器 `passed`，且带 `judge_kind` |

**三条硬纪律（均有断言）**

1. **不适用 ≠ 通过**：`test_inapplicable_is_not_a_pass`、`test_single_run_is_not_applicable`、`test_natural_language_format_is_not_applicable`、`test_no_failing_ids_is_not_applicable`；
2. **机械优先**：`test_lower_priority_not_used_when_higher_applies`、`test_falls_through_when_higher_not_applicable`；
3. **口径复用不另立**：副作用比对复用 S3-02（`diff_side_effects` + `Observation` + `SIDE_EFFECT_KINDS`）；
   委派场景无第二臂时，把臂比对与内容指纹标 `not_applicable`（`test_arm_checks_are_marked_not_applicable`）。

### 3.2 复评接入（回收三件套复评半边 →「机械优先 + LLM 兜底」）

| 行为 | 断言 / 落点 |
|---|---|
| 机械可用 ⇒ **不调用**复评器（LLM 不得覆盖机械结论） | `test_mechanical_wins_over_reviewer`（spy 调用次数 0） |
| 机械不通过 ⇒ 判 fail 并给可执行建议 | `test_mechanical_failure_is_reported_as_failure` |
| 机械全不适用 ⇒ 落兜底复评，`signal_kind=llm` + `judge_kind` | `test_llm_fallback_when_mechanical_not_applicable` |
| 载荷显式键即证据（`test_evidence` / `side_effects` / `replay_evidence`） | `test_payload_declared_evidence_is_used` |
| 显式传入覆盖载荷派生 | `test_explicit_signal_evidence_overrides_payload` |
| 未标注的注入复评器 ⇒ 归 `llm` 列（**绝不默认成机械**） | `test_unlabeled_reviewer_defaults_to_llm_column`、`test_cloud_review_to_dict_defaults_to_llm_column` |
| 真实链路自动取到契约⑤产物格式 | `agent/subagent/executor.py` 步骤 9（`collect_from_outcome(..., context=ctx)`） |
| `Reflection.to_dict()` 带 `signal_kind` / `signal` | `test_reflection_to_dict_carries_signal_kind` |
| 缺复评 ⇒ 空串（**不当作机械**） | `test_missing_reflection_has_no_signal_kind` |
| `judge_kind` 与 S3-02 `shadow.JUDGE_KIND_*` **同词表** | `test_judge_kind_vocabulary_matches_s3_02` |

> **如实标注（不冒充 LLM judge）**：`ReflectionEngine` 当前是**确定性本地复评**（无模型调用），故 `judge_kind` 标
> `deterministic_local` 而非 `llm_judge`；`llm` 列因此是"**非机械兜底列**"，真实 LLM-judge 与规则复评靠
> `judge_kind` 区分（S3-02 M1 口径）。`CloudPivotReview` 新增字段全部**带默认值**（旧构造调用不受影响）。

### 3.3 指标两列（**严禁混算**）

`agent/eval/metrics.py`：`delegation_recovery`（回收率）与**新增** `delegation_success_rate`（成功率）
均按判定主体分列 —— `columns.mechanical` / `columns.llm` / `columns.unlabeled`（未标注单列，**不并入任一列**）。

**两列不混算的证据**（构造数据：机械 4/4 齐全、3/3 判定通过；LLM 0/2 齐全、0/2 通过；混合率 = 4/6 ≈ 0.667）

```text
顶层 value = None（status = mixed_signal_kinds；numerator/denominator 均为 None）
columns.mechanical.recovery_rate = 1.0（4 样本）｜columns.llm.recovery_rate = 0.0（2 样本）
断言：row["value"] is None 且 row["value"] != 4/6          ← test_top_level_blended_value_is_not_produced
成功率：顶层 value = 1.0（value_basis = mechanical）；columns.llm.success_rate = 0.0
```

| 规则 | 断言 |
|---|---|
| 跨判定主体 ⇒ **合并率不产出**（置空 + `mixed_signal_kinds` + 明示理由） | `test_top_level_blended_value_is_not_produced` |
| 成功率顶层只取**单列**（机械优先）并标注 `value_basis` | `test_success_rate_top_level_uses_single_column` |
| 未标注行单列 `unlabeled`，不进任一列 | `test_unlabeled_column_is_separate`、`test_nested_reflection_signal_kind_is_read` |
| 单一判定主体 ⇒ 不触发 mixed（顶层照常出数） | `test_single_kind_is_not_mixed` |
| 未标注旧行走既有路径（S5-02/S6-01 语义不变） | `test_legacy_rows_keep_previous_semantics` |
| 样本 < 20 ⇒ 只披露不考核（**每列各自判定**） | `test_small_sample_only_disclosed` |
| 数据源缺位仍 `framework_only`；契约带 `signal_kind`/`review_passed` | `test_no_source_keeps_framework_only` |
| 周报 Markdown 并列渲染两列 + 5 条清单 | `test_weekly_markdown_renders_both_columns` |
| 周报负载含 `delegation_signals`（与代码清单同源） | `test_compute_metrics_includes_success_rate`、`test_catalog_is_shared_with_metric_dictionary` |

---

## 四、R7：单机降级设施表

**交付物**：[`../单机降级设施表.md`](../单机降级设施表.md)

| 项 | v7.2 要求 → 单机实现（摘要） | 缺口（**如实标注**） | 升级路径 |
|---|---|---|---|
| Watchdog | 集群 + OS 服务注册 → 进程内单例 + **非阻塞 OS 文件锁** + `SplitBrainError` | 仅同机互斥；接管时延未实测（**R5**） | 领导者租约 → P5 |
| 审计存储 | 每日根 → 外部只追加 → 本地受保护文件 + ed25519 + **外层根链** | **删除链尾不可检出**；无 WORM | WORM/S3 Object Lock + 跨机锚定 → P5 |
| 出域控制 | 进程外代理 → `guardrails/egress_guard.py` **进程内执行点** + 策略求值 + 链路上下文 | 进程内绕过执行点的直连不受保护 | 独立 egress 代理 → 部署侧 |
| 生成代码执行 | Docker 沙箱 → **确定性回放**（`ReplaySandbox`）+ 子进程环境隔离 | **真实执行型产物未容器化** | 容器沙箱 → 生产化批次 |
| 策略引擎 | OPA/Rego + WASM → **等效声明式引擎**（接口形状兼容） | 策略语料不兼容（自有方言），无 WASM 沙箱 | 换 OPA → 按需 |
| 多租户 | 隔离验证 → **workspace-hash 逻辑租户** + 读取侧作用域过滤 | 无物理隔离/配额/计费；同库逻辑边界 | 真多租户 → P5 |
| 台账保留 | TTL / 冷归档 / 分库 → **现状无 TTL**（五类台账同源遗留） | 无统一保留策略、无冷归档/分库 | 生产化批次（RFC 已定边界） |

**统计（文档 §三 自陈，并由测试核对一致）**：21 条可追溯断言 —— **已验证 13 / 未验证 8**
（R5 性能 1、P5 集群/外部组件 3、生产化 2、部署侧 1、按需替换 1）。

**机器校验**（`tests/unit/test_s7_06_degradation_table.py`，12 例）

| 校验 | 断言 |
|---|---|
| 覆盖 | ≥7 项，且 Watchdog / 审计存储 / 出域控制 / 生成代码执行 / 策略引擎 / 多租户 / 台账保留 七主题齐 |
| 四要素 | ≥7 张"要求 / 实现 / 缺口 / 升级路径"四列表 |
| 可追溯 | 每条 `路径::符号` 目标文件存在且符号在文件内（≥10 条）；"已验证"行必须有**存在的证据文件** |
| 状态诚实 | 状态取值合法；未验证项必须带归属（P5 / R5 / 生产化 / 部署侧 / 按需）；两种标注都必须出现 |
| 统计一致 | 文档统计表与逐条明细**逐数核对**（13 / 8 / 21） |
| 链接 | 文档内相对链接目标全部存在（≥8 条；`../` 层级错误是历史红点） |
| 缺口不粉饰 | `删除链尾不可检出` / `真实执行型产物未容器化` / `现状无 TTL` **逐字出现** |

---

## 五、验收清单逐条核验（任务书 §四）

### 5.1 R1

| # | 验收标准 | 结论 | 证据 |
|---|---|---|---|
| 1 | 判定集构建成本可采集（埋点存在且事件可查，含 `stage=case_build` 标注） | ✅ | `CaseStore.save()` 埋点；`test_event_carries_stage_case_build`、`CC.case_build_cost_ledger()` 输出（§2.1） |
| 2 | `ROIReport` 同时输出**不含/含**两种 ROI，公式与估计方法写清 | ✅ | `formula_excluding_case_build` / `formula_including_case_build` + `markdown()` 双口径对照表（§2.2）；`test_both_rois_are_reported`、`test_dict_and_markdown_expose_both_formulas` |
| 3 | 估计值有方法与样本标注（不得凭空给数） | ✅ | `case_build_cost_method` / `case_build_cost_samples` / `case_build_cost_source`；无事件记 `None`（§2.3） |

### 5.2 R4

| # | 验收标准 | 结论 | 证据 |
|---|---|---|---|
| 4 | 机械信号清单 5 条显式定义（写入指标字典/文档），标明优先级与适用条件 | ✅ | `mechanical.SIGNAL_SPECS`（§3.1）+ `eval.metrics.delegation_signal_dictionary()` 同源引用；`test_five_signals_in_priority_order`、`test_catalog_is_shared_with_metric_dictionary` |
| 5 | 回收三件套复评**机械优先**执行，结果带 `signal_kind` | ✅ | `TriadCollector.build_reflection()` + `CloudPivotReview.signal_kind` / `Reflection.signal_kind`（§3.2） |
| 6 | 指标区分 `mechanical` / `llm` 两列，**不混算**（用例断言） | ✅ | `test_top_level_blended_value_is_not_produced`（value=None ≠ 4/6）、`test_columns_are_computed_independently` |
| 7 | 样本 <20 只披露不考核（S5-02 口径） | ✅ | `test_small_sample_only_disclosed`（每列 `insufficient_samples` + `min_samples=20`） |

### 5.3 R7

| # | 验收标准 | 结论 | 证据 |
|---|---|---|---|
| 8 | 覆盖 ≥7 项，每项含"要求 / 实现 / 缺口 / 升级路径" | ✅ | 7 项 × 四列表；`test_at_least_seven_items`、`test_each_item_has_four_columns` |
| 9 | 每项可追溯代码位置或验收报告；"已验证/未验证"标注齐全 | ✅ | §三 21 条断言；`test_code_paths_and_symbols_exist`、`test_evidence_test_files_exist`、`test_verified_and_unverified_are_both_present` |
| 10 | 新文档链接通过本地 docs 链接预检 | ✅ | `pwsh -File scripts/dev/check_docs_broken_links.ps1` → **检查 1303 个文件 / 1543 个链接 / 0 个失效**；另有 `test_all_relative_links_resolve` |

### 5.4 通用

| # | 验收标准 | 结论 | 证据 |
|---|---|---|---|
| 11 | `digestion`/`subagent`/`eval` 邻接套件零回归；新增单测全绿、覆盖率 ≥80% | ✅ | 见 §六（邻接回归 + 覆盖率） |
| 12 | 未编造任何数字（估计值均标注方法与样本） | ✅ | §2.3 + 文档 §四；未验证项逐条给批次/残留编号（§四） |

**任务书特有硬约束逐条**

| 硬约束 | 落点 |
|---|---|
| 不得编造数字；样本不足按 S5-02 口径只披露 | `None` 语义 + `insufficient_samples`（`test_no_events_is_none_not_zero` 等） |
| 两列口径严禁混算（用例断言） | `test_top_level_blended_value_is_not_produced` |
| R7 表"未验证"不得写成已验证 | `test_verified_and_unverified_are_both_present` + `test_summary_matches_rows`（统计与明细逐数核对） |
| 不改既有公开接口行为；新增字段兼容叠加 | §2.4 + `CloudPivotReview` 新字段带默认值 + `delegation_recovery` 旧行语义不变 |
| 新文档链接通过本地预检 | §5.3 第 10 条 |

---

## 六、质量证据

### 6.1 新增单测（按套件）

| 套件 | 例数 | 覆盖点 |
|---|---|---|
| `tests/unit/test_s7_06_case_build_cost.py` | **28** | 埋点 / 幂等 / 通道归因 / 区间估计 / 双口径 / 兼容性 / 证据接线 |
| `tests/unit/test_s7_06_mechanical_signals.py` | **57** | 清单定义 / 4 条机械信号语义 / 机械优先 / 复评接入 / 两列不混算 / 样本门槛 |
| `tests/unit/test_s7_06_degradation_table.py` | **12** | R7 文档机器校验（覆盖 / 四要素 / 可追溯 / 统计一致 / 链接 / 缺口不粉饰） |
| **合计** | **97** | 全绿 |

### 6.2 邻接回归（26 个套件，含本任务三套件）

```text
> python -m pytest tests/unit/test_digestion_cases.py test_digestion_sandbox.py test_digestion_internalize.py \
    test_digestion_shadow.py test_digestion_gate.py test_digestion_pipeline.py \
    test_subagent*.py（11 套件） test_eval_metrics.py test_eval_runner.py test_eval_checkers.py \
    test_eval_cases.py test_eval_baseline.py test_eval_anchor.py test_s7_06_*.py -q -p no:randomly

================================== 所有测试通过！✓ ===================================
  通过: 1381
  失败: 0
  跳过: 0
========================= 1381 passed in 74.71s (0:01:14) =========================
```

**面板套件**：`tests/unit/test_s6_01_ui_panels.py` **76 passed**（ROI 叠加字段与 SLO 透传未破既有断言）。

### 6.3 门禁（命令 + 原始输出）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 相关套件 | `pytest tests/unit/test_s7_06_*.py -q` | **97 passed**（28 + 57 + 12） |
| 邻接回归 | 见 §6.2 | **1381 passed / 0 failed** |
| kwarg 扫描 ① | `python scripts/scan_kwarg_conflicts.py --path agent` | **HIGH: 0 处**（MEDIUM 15 / LOW 74，均为既有项） |
| kwarg 扫描 ② | `python scripts/scan_kwarg_conflicts.py --path tests` | **HIGH: 0 处**（MEDIUM 2 / LOW 29） |
| mypy | `python -m mypy agent/digestion/{case_cost,cases,internalize}.py agent/subagent/{mechanical,collection,executor}.py agent/eval/metrics.py --ignore-missing-imports --warn-no-return --warn-return-any --follow-imports=silent` | **Success: no issues found in 7 source files** |
| importlinter | `lint-imports` | **Contracts: 2 kept, 0 broken** |
| docs 链接预检 | `pwsh -NoProfile -File scripts/dev/check_docs_broken_links.ps1` | **检查 1303 个文件 / 1543 个链接 / 0 个失效**（`[PASS] 阻塞模式：失效链接 0 <= 阈值 0`；含本任务 3 个新文档与 3 处文档改动） |
| pre-commit（真实提交场景） | `git commit`（**未用** `--no-verify`） | 见 §7 |
| 产物漂移 | 跑完门禁后 `git status` | 无意外产物（判定集根/成本台账/事件目录均未在生产路径落盘） |

> **顺手修的既有 mypy 债（1 处，如实登记）**：`agent/digestion/internalize.py` 的
> `int(monthly["value"] or 0)` 在 HEAD 版本（旧行号 984）即报 `call-overload`（dict 值被推断为 `object`）。
> 本次为改动模块加了 `monthly: Dict[str, Any]` 类型标注（**零行为变化**），使"改动模块 mypy 0 error"成立。

### 6.4 覆盖率（新增模块，实测）

```text
> python -m pytest tests/unit/test_s7_06_case_build_cost.py tests/unit/test_s7_06_mechanical_signals.py \
    -q --cov=agent.digestion.case_cost --cov=agent.subagent.mechanical --cov-report=term-missing
agent\digestion\case_cost.py     278     29    90%
agent\subagent\mechanical.py     243     22    91%
TOTAL                            521     51    90%
85 passed
```

| 模块 | 覆盖率 | 未覆盖行性质 |
|---|---|---|
| `agent/digestion/case_cost.py` | **90%** | 绝大多数为防御分支（store 关闭异常、非法载荷字段回退、展示分支） |
| `agent/subagent/mechanical.py` | **91%** | 同上（S3-02 复用件不可用/比对抛错的 `except` 分支） |

两者均 ≥ 80% 门槛；关键判定路径（判定/不适用三态、双口径、两列）**全部有断言覆盖**。


---

## 八、交付物清单

| # | 交付物 | 路径 |
|---|---|---|
| 1 | 判定集构建成本采集与聚合（R1） | `agent/digestion/case_cost.py`（新增） |
| 2 | 埋点接线（判定集落库唯一漏斗） | `agent/digestion/cases.py`（`CaseStore.save` / `regenerate_case_set`） |
| 3 | ROI 双口径 + 证据采集 | `agent/digestion/internalize.py`（`ROIReport` / `build_roi_report` / `case_build_cost_from_ledger`） |
| 4 | ROI 面板兼容叠加 | `agent/ui_panels/data.py` |
| 5 | 机械信号清单（R4） | `agent/subagent/mechanical.py`（新增） |
| 6 | 复评接入（机械优先 + LLM 兜底） | `agent/subagent/collection.py`、`agent/subagent/executor.py` |
| 7 | 指标两列 + 指标字典引用清单 | `agent/eval/metrics.py` |
| 8 | 单机降级设施表（R7） | [`../单机降级设施表.md`](../单机降级设施表.md)（新增） |
| 9 | ROI 口径说明（R1 文档） | [`../判定集构建成本入ROI口径说明.md`](../判定集构建成本入ROI口径说明.md)（新增） |
| 10 | 性能预算文档的口径边界声明 | `docs/PERF_BUDGET_REBASED.md` §九 |
| 11 | 新增单测 3 套件 97 例 | `tests/unit/test_s7_06_*.py` |
| 12 | 本验收报告 | `TASK-S7-06_验收报告.md` |
| 13 | 交付结案报告 | [`S7-06_交付结案报告_20260912.md`](S7-06_交付结案报告_20260912.md) |
| 14 | 总览状态行 + 残留注销 | [`00_总览_审计结论与重构总计划.md`](00_总览_审计结论与重构总计划.md) §4.3、[`V72_全计划收官审计报告.md`](V72_全计划收官审计报告.md) §8.5 / §三 / §六 |

---

## 九、遗留与边界（如实登记，不阻塞）

| # | 事项 | 归属 | 阻塞性 |
|---|---|---|---|
| 1 | 判定集构建的**人工工时/回放算力单价**未配置 ⇒ 成本点值为**下界**、区间右端不可估 | 部署侧配置（`CP_DIGESTION_CASE_BUILD_MANUAL_RATE_CENTS` / `..._REPLAY_RATE_CENTS`） | 不阻塞（口径已如实标注） |
| 2 | 无 LLM 生成用例的**自动**通道（通道③由调用方注入 token 实测值）⇒ 现网 `llm_cents` 取决于调用方是否上报 | S3-02 遗留（通道③）+ 使用方接线 | 不阻塞 |
| 3 | `llm` 列当前实际判定器为 `deterministic_local`（`ReflectionEngine` 无模型调用） | 认知子系统升级时改标 `llm_judge`（注释已写） | 不阻塞 |
| 4 | 委派行契约的 `signal_kind` / `review_passed` 需**数据源侧**填充（S4-04 行契约已给出可选字段） | S6-01 L6 / 使用方接线 | 不阻塞（缺省进 `unlabeled` 列，不并入任一列） |
| 5 | R5（Watchdog <10s 接管时延实测）维持未清 | S5-03 / 部署侧 | 不阻塞（R7 表已标 **未验证**） |
| 6 | 台账保留策略（TTL/冷归档/分库）未实现 | 生产化批次 | 不阻塞（R7 表已标 **未验证**） |

---

## 十、结论

- **R1 / R4 / R7 三项残留全部清理完毕**，且每一项都有**机器可复现的证据**（单测断言 + 原始输出 + 文档机器校验）；
- **无一项被冒充完成**：未验证项 8 条逐条给出批次或残留编号；估计值 100% 带方法与样本；无数据一律 `None`；
- **未改既有公开接口行为**：条件③ 判据、既有 ROI 字段、`delegation_recovery` 旧行语义与面板旧键全部保持不变（兼容叠加）；
- 邻接套件零回归、新增 97 例全绿、docs 链接预检 0 失效 ⇒ 满足任务书 §四 全部 12 条验收标准。
