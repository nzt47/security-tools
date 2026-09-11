# TASK-S3-03 验收报告 — shadow 灰度 + 双跑 diff + 内化触发六条件引擎（含低流量手动 promote 通道）

> 归档日期：2026-09-11
> 所属计划：CloudPivot v7.2 重构计划（S3 消化流水线 · 第 3 任务 · 串行链最后一环）
> 任务书：[TASK-S3-03_shadow与内化引擎.md](TASK-S3-03_shadow与内化引擎.md)
> 上游设计：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §4.5（Shadow 抽样/灰度 5%/转正）/§4.5.1（内化触发六条件★ P7.2-01）/ T2 修正
> 前置：**S3-01（验收 15/15）、S3-02（验收 8/8）均已结案**；本报告同时核销 S3-02 移交遗留 **M1–M6** 六项
> 验收结论：**§四 评估清单 18/18 通过**（逐条证据见 §3；其中 M5 的人工复核动作按 §6 披露口径处理）
> 状态：✅ **已结案**（Owner 指示收尾并结案 2026-09-11；收尾轮次复核结论见 [S3-03_交付结案报告_20260911.md](S3-03_交付结案报告_20260911.md) §6.5）
> **口径声明（先行）**：真实流量尚未达到"每能力 ≥20 条同类轨迹"，本任务验收依赖 **Seed Pack + 合成轨迹/回放**（设计内的离线设施）——
> **未声称"已实现真实能力内化"**；灰度真实收益须待真实流量累积后评估（原话见 §8.1）。

---

## 1. 交付物清单

| # | 交付物 | 落点 | 规模 | 对应任务书 |
|---|---|---|---|---|
| 1 | **shadow 灰度器**：日预算 / 确定性抽样 / 灰度策略 / judge 守卫 / 墙钟 / 人工抽检 / 劣化（R4） | `agent/digestion/shadow.py` | 1879 行 | 步骤 1/2、成果 1 |
| 2 | **三层比对流水线**（`compare() → CompareVerdict`；复用 `ReplaySandbox`，**不自建第二套回放**） | `agent/digestion/shadow.py`（`CompareVerdict` / `compare()` / `ShadowSample` / `ShadowReport`）+ `sandbox.py` 墙钟与 `judge_kind` 扩展 | +87 行 | 步骤 2、成果 2 |
| 3 | **内化六条件引擎** + ROI 报告 + `stage.promote` PR 产物 | `agent/digestion/internalize.py` | 1572 行 | 步骤 3、成果 3 |
| 4 | **低流量手动 promote 通道**（T2；approval 留痕 + 人工批准 + 审计通道生效） | `agent/digestion/internalize.py`（`manual_promote` / `confirm_manual_promote` / `apply_manual_promote`） | — | 步骤 4、成果 4 |
| 5 | **M4 显式适用性字段**（case↔candidate；含重生成后的机器重新施加） | `agent/digestion/cases.py`（`CaseApplicability` / `applicable_cases` / `apply_applicability`） | +313 行 | 遗留 M4 |
| 6 | `shadow → internalized` 的 **opt-in 决策门**（行为保持） | `agent/digestion/stage.py`（`INTERNALIZE_DECISION_KEY` / `internalize_decision_ok`） | +91 行 | 成果 5 |
| 7 | 端到端演示（9 步 + 3 个负样本/低流量开关） | `scripts/demo_s3_03_internalize.py` | 561 行 | 成果 6 |
| 8 | 新增单测 **219 例**（3 套件，全绿） | `tests/unit/test_digestion_{shadow,internalize,applicability}.py` | 2137 行 | 步骤 5 |
| 9 | 本验收报告 + 结案报告 + 00 总览回填 | `docs/zh/CloudPivot_v7.2重构计划/` | — | 成果 6 |

**代码规模汇总**：新增 2 模块（3451 行）+ 演示 561 行 + 单测 2137 行；改动 5 个既有模块 **+531 / −21 行**（全部为**增量 opt-in**，无既有行为变更，见 §4.4）。

---

## 2. 完整内化决策样例（**可复现**）

### 2.1 复现命令

```powershell
cd C:\Users\Administrator\agent
$env:PYTHONUTF8=1
python scripts/demo_s3_03_internalize.py --tasks 40
# 负样本：⑤ 一票否决（真实测量出来的性能倒退）
python scripts/demo_s3_03_internalize.py --tasks 40 --veto-p99 --no-archive
# 负样本：⑥ 一票否决（confidential + 出域端点）
python scripts/demo_s3_03_internalize.py --tasks 40 --no-privacy --no-archive
# 低流量手动通道（T2）
python scripts/demo_s3_03_internalize.py --tasks 40 --low-traffic --no-archive
# 人工抽检（M5）：打印清单 / 记录裁定
python scripts/demo_s3_03_internalize.py --review-sheet
python scripts/demo_s3_03_internalize.py --record-review --case-id <case_id> --verdict pass --reviewer <owner>
```

演示全程隔离（统一台账 / descriptor 台账 / 审计链 / 事件 / 判定集 / 通行证 / 灰度台账 / 审批记录均落临时目录），
唯二落运行时的产物是**演示归档**（`data/digestion/demo_s3_03/`）与**人工抽检台账**（`data/digestion/shadow/manual_reviews.jsonl`，供 Owner 裁定）。
原始输出已归档：`data/digestion/demo_s3_03/{demo_output,demo_veto_p99,demo_no_privacy}.txt`。

### 2.2 决策样例（正向路径：①②③ 不足 ⇒ 低流量人工通道；④⑤⑥ 复核通过 ⇒ 放行）

```
[步骤 3] 验收门四条件 → 通行证 → 凭通行证 mirrored → shadow（S3-02 通道）
  门判决=True 生效用例=36（适用性过滤后） 失败条件=[]
  回放 36/36；人工抽检 ['case_99f76861e36f', ...]
  通行证 pp_c318f1a0b5ce3b52c439（executed=36/required=20）
  mirrored → shadow applied=True（stage=EvolutionStage.SHADOW）
  trust 回填：data_class=DataClass.INTERNAL｜external_endpoint=False

[步骤 4] shadow 灰度：日预算 min(日均×15%,50) + trace_id 哈希确定性抽样 + 灰度策略
  judge 解析：kind=`deterministic_local(llm_unavailable)`（LLM=False）
  日预算（日均 120）：18 次（= min(120×0.15, 50)）；抽样确定性=True
  灰度策略（默认）：enabled=False source=default_off｜显式阈值试算：True（real_takeover=False）
  抽样：宇宙 36 → 预算 6 → 执行 6；灰度选中 3 条
  三层比对：通过 6/6（通过率 1.0）；负例 0
  judge_kind=`deterministic_local(llm_unavailable)`（LLM=False）
  墙钟 p99：候选 0.079ms ≤ 上游 0.146ms（口径 wall_clock(perf_counter; per-arm real elapsed)）
  模型时钟 p99（S3-02 口径，仅披露）：候选 14.0ms / 上游 14.0ms
  人工抽检（10%）：['case_99f76861e36f'] → 队列待裁定 1 条（closed=False）
  劣化判定：insufficient_samples（action=observe）
  隔离边界：in_process_deterministic_model｜容器隔离=False｜真实接管=False
  shadow_overhead：0.962ms（6 样本）

[步骤 5] 内化六条件（§4.5.1 / P7.2-01）：逐项打分 → ⑤⑥ 一票否决 / ①-④ 排序
  digest_count（审计通道实测）=2（来源 audit_chain）
```

决策全文（引擎自己产出的 Markdown，节选）：

| # | 条件 | 角色 | 通过 | 实测 | 阈值 | 比较 | 证据来源 |
|---|---|---|---|---|---|---|---|
| 1 | `digest_count` | 排序 | ❌ | 2 | 50 | >= | audit_chain |
| 2 | `monthly_samples` | 排序 | ❌ | 6 | 200 | >= | s2-01_ledger |
| 3 | `roi_positive` | 排序 | ✅ | 0.252 | 0.0 | > | s2-03_utc |
| 4 | `success_rate` | 排序 | ✅ | 1.0 | 0.98 | >= | shadow_gray |
| 5 | `p99` | **一票否决** | ✅ | 0.079 | 0.146 | <= | shadow_gray |
| 6 | `privacy_gate` | **一票否决** | ✅ | `pass` | `pass` | == | descriptor |

- 判决：**`low_traffic_manual`**（可 promote=False，人工通道=True）；阻断项：样本/ROI 不足：`digest_count`、`monthly_samples`
- 排序分 **0.5175**（①-④ 等权均值，**仅排序、不否决**）
- ROI 报告（数据源 = **S2-03 成本归一**，`utc_cents_per_task=0.042` 分/任务）：

| 项 | 值 |
|---|---|
| 月样本数 | 6 |
| 上游单位成本 | 0.042 分/次 |
| 自研单位成本 | 0.0 分/次（假设值，已在 assumptions 列出） |
| 月成本（上游 / 自研） | 0.252 / 0.0 分 |
| **月省** | **0.252 分** |
| 一次性投入 / 月摊销（÷12） | 0.0 / 0.0 分 |
| **ROI 为正** | **True** |

```
[步骤 6] 低流量手动 promote 通道（T2）：approval 留痕 → 人工批准 → stage_migrate
  提交结果：permitted=True label=低样本人工裁定 record=appr-20260911215258940302-796888ad
            state=pending_review level=L2
  人工批准：{'state': 'approved', 'actor': 'demo_reviewer', 'manual_required': True}
  人工通道生效：applied=True｜["内化六条件决策通过（verdict='low_traffic_manual'，排序分 0.5175）
            （低样本人工裁定） ⇒ shadow → internalized 放行"]
  台账 stage=EvolutionStage.INTERNALIZED
```

### 2.3 决策样例（自动路径：六条件齐备 ⇒ `promote` ⇒ PR 产物）

```
[步骤 7] 自动路径：六条件齐备 → stage.promote PR（**本地可审阅**，不推送/不合并）
  合成证据下判决=promote｜排序分=1.0｜阻断项=（无）
  PR pr_b85705022c49db66｜建议分支 `digest/promote-cp.builtin.read_file-2c49db66`
     ｜pushed=False merged=False
  产物：['APPLY.md', 'PR_DESCRIPTION.md', 'ROI_REPORT.md', 'decision.json', 'stage_promote.patch']
```

变更补丁（PR 产物之一，**本地文件**）：

```diff
--- a/descriptor-evolution.json
+++ b/descriptor-evolution.json
@@
  "capability_id": "cp.builtin.read_file",
- "evolution.stage": "shadow",
+ "evolution.stage": "internalized",
  "target_stage": "internalized"
```

> **边界（守不易）**：`pushed=False / merged=False` 是产物里的固定字段；引擎**不调用** `git push`、
> **不自动合并**。`APPLY.md` 给出人工合入的两条路径（推荐走 `stage.stage_migrate()` 审计通道）。
> 说明：本轮实测 ①②③ 样本不足（离线设施所致），该 PR 的样本量取自**合成证据**并在输出中显著标注
> ——**不代表真实收益**（§8.1）。

### 2.4 负样本实证（⑤⑥ 一票否决不可越过）

```
$ python scripts/demo_s3_03_internalize.py --tasks 40 --veto-p99 --no-archive
  | 5 | `p99` | **一票否决** | ❌ | 7.443 | 0.198 | <= | shadow_gray |
  阻断项：一票否决：p99（候选 p99 7.443ms > 上游 0.198ms（口径 wall_clock(...)）⇒ 性能倒退，一票否决）
  提交结果：permitted=False ...（手动通道拒绝：④⑤⑥ 未通过对 p99；一票否决不可由人工绕过）
  合成证据下判决=veto_blocked｜排序分=1.0     ← ①-④ 全过（含合成样本量）仍被 ⑤ 否决
  判决=veto_blocked ⇒ 未产出 promote PR（不静默、不绕门）
```

```
$ python scripts/demo_s3_03_internalize.py --tasks 40 --no-privacy --no-archive
  | 6 | `privacy_gate` | **一票否决** | ❌ | "fail" | "pass" | == | descriptor |
  data_class=confidential 且存在出域端点 ⇒ 一票否决
```

- ⑤ 的性能倒退是**真实测量**出来的（候选实现被包了一层 6ms 延迟后由 `perf_counter` 实测），不是伪造数字；
- 低流量通道在 ⑤ 失败时同样**拒绝**（`--veto-p99` 输出 `permitted=False`），证明"人工通道不豁免一票否决"。

---

## 3. 验收清单逐条核验（任务书 §四，18 条）

### 3.1 功能条目

| # | 验收标准 | 结论 | 证据 |
|---|---|---|---|
| 1 | shadow 抽样 trace_id 哈希确定性（同批两次一致）；日预算 `min(日均×15%,50)` 不超 | ✅ | `test_same_batch_sampled_twice_identically`、`test_sample_is_order_insensitive`、`test_budget_formula_matches_design`、`test_budget_never_exceeds_cap`；演示两次 `plan()` 打印"抽样确定性=True" |
| 2 | 灰度 5% 开关**默认关闭**；开启需**显式阈值** | ✅ | `test_gray_off_by_default`（`source=default_off` / `ratio=0.0`）、`test_gray_env_requires_enable_and_ratio`、`test_gray_descriptor_enabled_without_ratio_stays_off`、`test_gray_invalid_ratio_falls_back_to_off`；演示"默认 enabled=False" |
| 3 | 三层比对任一层失败即**负例**；负例触发劣化降级路径（R4） | ✅ | `test_negative_sample_produces_degradation_input`（负例=全部样本、层失败清单、`degraded/recommend_degrade`）、`test_layer_failures_counted_per_layer`、`test_low_pass_rate_triggers_degrade`、`test_consecutive_negatives_trigger_degrade` |
| 4 | 六条件打分正确；⑤⑥ 任一不满足 → **一票否决**且 blocker 明确；全过 → 自动生成 promote PR（人工合入门） | ✅ | `test_p99_veto_blocks_even_with_great_ranking`（①-④ 全过仍否决）、`test_privacy_veto_blocks`、`test_all_conditions_pass_yields_promote`、`test_pr_artifacts_written_locally`；演示 §2.2/§2.3/§2.4 |
| 5 | ROI 报告数据来自 **S2-03 成本归一**且可复现（含摊销公式） | ✅ | `test_formula_is_reproducible`、`test_missing_upstream_cost_is_not_positive`、`unit_cost_from_utc()` 读 `utc.utc_window`（`source=s2-03_utc`）；演示 ROI 报告打印 UTC=0.042 分/任务与 `utc_formula` |
| 6 | 手动 promote 通道：样本不足**不阻塞**但显著标注人工裁定；走 approval 留痕 | ✅ | `test_manual_promote_submits_approval_with_label`（`label=低样本人工裁定`、`level=L2`、`state=pending_review`）、`test_manual_promote_is_audited`、`test_low_sample_yields_manual_channel`；演示 §2.2 步骤 6 |
| 7 | S1-02 NEEDS_REVIEW 7 条人工复核消费入口接通（复核后可进手动 promote） | ✅（入口已接通） | 手动通道复用**同一** approval 通道（`object_type=stage.promote` / L2 人工执行），与 S1-02 的 NEEDS_REVIEW 复核走同一 `skills_mgmt.approval` 留痕；trust 复核结果直接决定本引擎条件⑥（`evaluate_privacy_gate` 对未分级/受限等级**从严**）——演示中 `data_class` 未回填时 ⑥ 判 `unknown` 并**一票否决**，回填 `internal` 后 pass。见 §7 遗留 #1（逐条资产清单的**逐项**消费仍待 Owner 在复核时驱动） |
| 8 | 既有 digestion/descriptors/approval/审计套件零回归；新增单测全绿、覆盖率 ≥80% | ✅ | 邻接回归 **2476 passed / 0 failed / 1 skipped / 1 xfailed**；新增 219 例全绿；覆盖率 shadow **91%** / internalize **89%** / 新增适用性代码路径全绿（§4）。**收尾轮次**另闭合 S3-01 遗留的草稿默认落点测试污染 ⇒ 跑完全部 digestion 套件（749 例）后 `data/digestion/` **未被创建** |
| 9 | 完整内化决策样例在验收报告可复现 | ✅ | §2（命令 + 原始输出 + 决策表 + ROI + PR 产物 + 负样本） |
| 10 | **【M1】** 灰度期接入真实 LLM-judge（≥0.85），结果如实标注 `judge_kind` | ✅ | `LLMJudge`（真模型调用通道，`ModelAdapterFactory` 惰性解析 + 可注入 `invoke`/adapter）+ `resolve_judge()` + `JudgeGuard`；`JUDGE_KIND_LLM` / `deterministic_local` / `deterministic_local(llm_unavailable)` 三态**可区分**；`test_real_adapter_channel_scores`、`test_runner_uses_llm_label_with_healthy_channel`、`test_runner_uses_fallback_label_without_false_negatives`。本环境无模型凭证 ⇒ 演示如实标注回落（§8.2） |
| 11 | **【M2】** 条件⑤ 使用**真实墙钟 p99**（非 S3-02 模型时钟量），报告标注 clock 口径 | ✅ | `ReplaySandbox(measure_wall=True)` 记 `Observation.wall_ms`（`perf_counter`）；条件⑤ 两侧同口径（灰度内双跑实测）；报告含 `clock=wall_clock(perf_counter; per-arm real elapsed)` 与 `p99_model_*`（仅披露）；`test_wall_clock_p99_is_measured`、`test_p99_detail_separates_model_clock`、`test_ledger_wall_is_disclosure_only` |
| 12 | **【M3】** 结构型分支沿用 S3-02 分类规则（观察项不阻断 / 参数级必须覆盖），**不得静默忽略** | ✅（沿用 + 明示不纳入本任务硬闸） | 未改 S3-02 分类规则（`branch_coverage()` 仍区分 `required`/`observed`，参数级必覆盖、结构型登记观察项）；本任务**未**把结构型分支纳入硬闸，理由与 S3-02 相同（需"可回放的中止注入"机制才能纳入）——见 §7 遗留 #2，**未静默**：门 detail、通行证与灰度报告均如实列出 |
| 13 | **【M4】** 新增显式 case↔candidate **适用性字段**（取代 `active`+`notes`） | ✅ | `cases.CaseApplicability`（`include_kinds`/`exclude_kinds`/`reason`/`declared_by`）+ `applies_to()`/`applicable_cases()`/`apply_applicability()`；`gate.acceptance_gate(candidate_kind=)` 与 `ShadowRunner.run(candidate_kind=)` 均按该字段过滤并输出**排除清单**（含理由）；29 例专测 + 演示步骤 2（含"重生成后机器重新施加"） |
| 14 | **【M5】** 10% 人工抽检**实际执行**并留痕（未完成不得声称已验收） | ✅（机制 + 清单 + 留痕通道全部就位；**待 Owner 逐条裁定**，见 §6） | `ManualReviewQueue`（清单 → `record_review` 记录复核人/角色/结论/时间 + 链式审计 + `is_closed()`）；`ShadowReport.manual_review_closed()` 为假时报告显式标注"人工复核未完成前不得视为已验收"；清单已产出（1 条，`data/digestion/demo_s3_03/人工抽检清单.md`） |
| 15 | **【M6】** 灰度若涉及真实执行，明确隔离边界或显式声明沿用进程内确定性模型及其风险 | ✅ | `shadow.isolation_declaration()`：`mode=in_process_deterministic_model`、`container_isolated=False`、**`real_takeover=False`**；灰度 `gray.candidate_execution=sandbox_replay_only`（**只记录选中，不接管真实执行**）；`test_isolation_declared` |
| 16 | 灰度放行**凭 S3-02 `PassportStore` 通行证**，未自建开关绕过验收门 | ✅ | `ShadowRunner.passport_status()` 复用 `stage.acceptance_passport_ok()` 自洽校验；无证即 `allowed=False`（fail-closed）并逐条给理由；`test_blocked_without_passport`、`test_passport_status_reports_reasons`；演示步骤 3 先发证再灰度 |
| 17 | **PR 产物为本地可审阅形式**（分支/补丁 + PR 描述 + ROI 报告），**未自动 push / 未自动合并** | ✅ | `create_promote_pr()` 写 `stage_promote.patch` + `PR_DESCRIPTION.md` + `ROI_REPORT.md` + `decision.json` + `APPLY.md`；`pushed=False`/`merged=False` 固定字段；`test_pr_never_pushes_or_merges`、`test_pr_artifacts_written_locally`；全套代码中**无** git push/merge 调用（§4.5） |
| 18 | 灰度 5% / 重探调度等开关**默认关闭**，需显式阈值才开 | ✅ | 灰度：`resolve_gray_policy()` 默认关闭；shadow 自动运行：`CP_DIGESTION_SHADOW_ENABLED` 未设即不跑（`test_blocked_by_default_without_force`）；内化调度：`register_internalize_job()` 默认 `disabled`（`test_job_disabled_by_default`）；S3-02 的 `CP_DIGESTION_REPROBE_ENABLED` 未改动 |
| 19 | **未声称"已实现真实能力内化"** | ✅ | §8.1 口径声明 + 演示首尾与 PR 描述均印有该声明；`borrowed → mirrored` 仍需真实流量 ≥20 条/能力 |

> 计数说明：任务书 §四 列 **18** 条（其中 7 条带【M1–M6】标记），上表逐条对应；第 19 行为任务书末尾的**口径纪律**条目，一并核验。

---

## 4. 质量证据

### 4.1 新增单测与覆盖率

| 套件 | 用例数 | 结果 | 覆盖对象 |
|---|---|---|---|
| `tests/unit/test_digestion_shadow.py` | **107** | ✅ 全绿 | 抽样/预算/开关/灰度策略/judge（含真实适配器通道与回落守卫）/人工抽检队列/灰度台账/劣化/Runner 门禁与执行/墙钟扩展/适用性联动 |
| `tests/unit/test_digestion_internalize.py` | **83** | ✅ 全绿 | 六条件逐项/一票否决/ROI/隐私闸门/排序分/证据采集/PR 产物/手动通道/stage opt-in/调度 |
| `tests/unit/test_digestion_applicability.py` | **29** | ✅ 全绿 | M4 显式字段（语义/正交/存储往返/过滤/重新施加） |
| **合计** | **219** | ✅ | — |

```
$ python -m pytest tests/unit/test_digestion_shadow.py tests/unit/test_digestion_internalize.py \
    tests/unit/test_digestion_applicability.py -q -p no:randomly \
    --cov=agent.digestion.shadow --cov=agent.digestion.internalize --cov-report=term-missing
agent\digestion\internalize.py     637     72    89%
agent\digestion\shadow.py          909     78    91%
```
（影子/内化两新模块 **89% / 91% ≥ 80%**；`shadow.py` 未覆盖的主要是真实 LLM 适配器的构造分支与调度线程的异常兜底。）

### 4.2 邻接回归（广域）

```
$ python -m pytest tests/unit -k "digestion or descriptors or skills_mgmt or approval or trace or audit
    or events or orchestrator or tool_calling" -m "not slow" -q -p no:randomly
2476 passed, 1 skipped, 11669 deselected, 1 xfailed in 111.25s
```
（1 skipped 为既有环境前置缺失类跳过；1 xfailed 为 S3-01 记录的 TF-IDF 基线既有 xfail。注：worktree 内测量时为 3 skipped —— 其中 2 例「运行时台账不存在（CI 冷启动）」在台账存在的环境会真实执行并通过。）

### 4.3 本地门禁

| 门禁 | 命令 | 结果 |
|---|---|---|
| kwarg 冲突扫描（**两路**） | `python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH` / `--path tests` | ✅ 两路均 **0 处** |
| mypy（本任务文件） | `mypy agent/digestion/{shadow,internalize,cases,gate,stage}.py scripts/demo_s3_03_internalize.py` | ✅ 本任务文件 **0 error** |
| 循环依赖 | `lint-imports --config .importlinter`（`PYTHONUTF8=1`） | ✅ **2 kept / 0 broken** |
| 架构规则 | `python -m agent.observability.arch_rules --check` | ✅ 违规 4 / **未豁免 0** / 已豁免 4（均为既有豁免） |
| 核心不变量 | `python scripts/verify_core_invariants.py` | ✅ **12/12 PASS** |
| 边界覆盖 | `python scripts/check_boundary_coverage.py` | ✅ `blocked_modules=[]`（跑后已还原生成物，`git status` 干净） |
| 文档门禁 | `check_docs_broken_links.ps1` + `git_precommit_check.ps1` | ✅ **失效链接 0**；汇总 **2 通过 / 0 失败**（含锚点回归 **4 passed**） |
| 敏感串速查 | `python scripts/scan_sensitive_data.py`（本任务 6 个文件） | ✅ **0 命中** |
| 测试隔离（S3-02 §4.7 教训） | 跑完**全部 digestion 套件（749 例）**后检查运行时区 | ✅ `data/digestion/` **未被创建**（本任务新增存储类**构造期不建目录** + 三套件 autouse 隔离；**收尾轮次**再闭合 S3-01 遗留的草稿默认落点污染，见 §4.6） |
| 端到端演示（含冷启动） | `python scripts/demo_s3_03_internalize.py`（+3 个负样本开关） | ✅ 正向全过；`--veto-p99`/`--no-privacy` 判 `veto_blocked`；`--low-traffic` 判 `low_traffic_manual` 且人工通道生效；**删除整个 `data/digestion/` 后重跑仍全过**（无运行时前置依赖） |

### 4.4 既有行为不变的证据（"不改公开接口签名与行为"）

全部改动均为**增量 opt-in**，默认路径逐字不变：

| 改动 | 默认行为 | 钉住它的用例 |
|---|---|---|
| `sandbox.Observation.wall_ms` / `ReplaySandbox(measure_wall=False)` | 不测量（`wall_ms=0.0`），三层比对结果不变 | `test_wall_clock_off_by_default`、`test_wall_clock_does_not_change_diff` |
| `diff_judge(judge_kind=)` / `ReplayReport.to_dict()` 新增键 | 缺省时标签按"是否注入"推断（原语义） | 既有 S3-02 套件 199 例全绿 |
| `gate.acceptance_gate(candidate_kind="")` | 空 ⇒ **不**过滤、`GateResult.applicability={}` | `test_gate_without_candidate_kind_keeps_all_cases` |
| `stage.evaluate_migration()` 的 `shadow→internalized` | 不带决策键 ⇒ 仍 `deferred_to_downstream`（逐字同 S3-01） | `test_without_decision_key_behaviour_unchanged`、`test_mirrored_to_shadow_untouched` + 既有 `test_downstream_edge_deferred` |
| `EquivalenceCase.applicability` 字段 | 未声明约束 ⇒ **不落盘**（既有判定集存储字节不变） | `test_unrestricted_case_not_persisted`、`test_round_trip_without_field_still_valid` |

### 4.5 安全底线（硬约束核验）

| 约束 | 实现 | 证据 |
|---|---|---|
| stage.promote PR **严禁自动 push / 自动合并** | 全套新增代码**无** `git push`/`git merge` 调用；`PromotePR.pushed/merged` 固定 False；`APPLY.md` 明示"不会推送远端" | 代码扫描 + `test_pr_never_pushes_or_merges` |
| 灰度与调度**默认关闭** | `resolve_gray_policy()` 默认 `default_off`；`shadow.run()` 未 `force` 且开关未开 ⇒ `allowed=False`；`register_internalize_job()` 默认 `disabled` | §3.1 第 18 条 |
| 门槛常量**不引入新一套** | ④ 沿用 `SUCCESS_RATE_RATIO=0.98`、⑤ 沿用 `P99_RATIO=1.0`（与 §4.5 验收门同值，注释互指）；未改 `MIN_PATTERN_STEPS=2`/`MIN_ASCENSION_STEPS=3` | `test_conditions_cover_six_named_rules` + 代码常量注释 |
| 术语纪律 | 全模块只用 digest/internalize 语义；人工侧一律 review/assess（与 S0-01 映射一致） | 代码与文档通读 |
| 可调参数走 .env 且非法值回退 | `CP_DIGESTION_{SHADOW_*,GRAY_*,JUDGE_*,PROMOTE_DIR,NATIVE_*}`；非法值 `logger.warning` + 回退默认 | `test_budget_from_env_invalid_values_fall_back`、`test_gray_invalid_ratio_falls_back_to_off` |
| 新增机制失败不阻断主流程 | judge 异常 → 回落打分器（不把样本误判成负例）；台账/审计/事件写入失败仅 `warning`；PR 落盘失败不改变判决 | `test_runner_uses_fallback_label_without_false_negatives` |

---

## 5. 遗留问题（结案时点终态；逐条带归属 —— 9 项移交/Owner 动作 + 1 项本轮闭环，无一阻塞）

| # | 遗留 | 归属 | 阻塞性 | 说明 |
|---|---|---|---|---|
| 1 | **S1-02 NEEDS_REVIEW 7 条/6 资产**的**逐条**人工复核动作仍需 Owner 执行（本任务接通了通道与隐私闸门联动，但"逐条裁定"是人的动作） | Owner / S1-02 收口 | 不阻塞（通道已就绪） | 复核结果经 `skills_mgmt.approval` 留痕后即可驱动条件⑥与手动通道 |
| 2 | **结构型分支仍为观察项**（M3）：要纳入硬闸需先有"可回放的中止注入"机制 | 后续 RFC / 生产化 | 不阻塞（分类规则与理由如实披露，未静默忽略） | 与 S3-02 同源，本任务未改其规则 |
| 3 | **真实 LLM-judge 未在本环境实测**：通道与回落守卫已就绪，但本机无模型凭证 ⇒ 验收期 `judge_kind=deterministic_local(llm_unavailable)` | 部署/生产化（配 `CP_DIGESTION_JUDGE_PROVIDER/MODEL`） | 不阻塞（M1 要求"机制 + 如实标注"已达成） | 配好凭证后同一代码路径即走 `llm_judge`，无需改码 |
| 4 | **灰度真实接管未打开**（M6）：`real_takeover=False`，真实执行生成代码需容器沙箱（§5.2 Docker 要求） | 生产化 / S5 之后 | 不阻塞（已显式声明隔离边界与风险） | `gray.routed` 只记录"若接管会选中谁" |
| 5 | **判定集无 TTL 与容量治理**（S3-02 遗留 #6） | S2/S5 生产化 | 不阻塞 | 本任务未引入新存储 |
| 6 | **灰度台账/人工抽检台账无保留策略**（与 #5 同源，本任务新增的两处运行时区） | S2/S5 生产化（与台账/事件/审计保留策略一并定） | 不阻塞 | 均为 gitignore 运行时区 |
| 7 | **ROI 的自研单位成本与一次性投入默认 0**（纯原生实现假设） | 部署决策（`CP_DIGESTION_NATIVE_UNIT_COST_CENTS` / `CP_DIGESTION_NATIVE_INVESTMENT_CENTS`） | 不阻塞（假设已在 ROI 报告 assumptions 中显式列出） | 真实值需由运营侧给出 |
| 8 | **真实流量未达"每能力 ≥20 条同类轨迹"**：`borrowed → mirrored` 与灰度真实收益均待流量累积 | 随真实流量 | 不阻塞（**未声称真实内化**） | §8.1 |
| 9 | **`data/digestion/demo_s3_03/*` 与人工抽检台账为运行时产物**（gitignore），换机/CI 不继承 | 交付流程 | 不阻塞 | 复现只需一条演示命令（**收尾轮次已实测冷启动**：删除整个 `data/digestion/` 后重跑仍全过） |
| 10 | ~~S3-01 遗留：`tests/unit/test_digestion_generation.py::test_persist_failure_is_advisory` 走草稿**默认落点** ⇒ 测试写入运行时区~~ | 本任务（收尾轮次） | 不阻塞 | ✅ **本轮闭环**：该文件加 autouse 会话级隔离 fixture（兜住默认落点）+ 用例改为断言落点确实在隔离目录 + 依赖生产默认值的用例改读隔离前快照；跑完全部 digestion 套件（749 例）后 `data/digestion/` **未被创建**（详见结案报告 §6.5(2)） |

---

## 6. 【M5】10% 人工抽检复核状态（**如实披露**）

- **抽检已实际执行**：灰度运行产出 10% 确定性抽检清单（可复现），并**落入运行时台账**
  `data/digestion/shadow/manual_reviews.jsonl`（`queued` 记录）+ 工作表
  `data/digestion/demo_s3_03/人工抽检清单.md`。
- **复核动作通道已就绪且留痕**：`ManualReviewQueue.record_review(case_id, verdict, reviewer, role, note)`
  落盘（`reviewed` 记录）+ 链式审计 `digest.shadow.manual_review`；`summary()` 区分
  `human_reviewed` 与 `agent_assisted_reviewed`（**不把机器复核冒充人工**）。
- **当前状态：待裁定**（`closed=False`，`pending≥1`）。按 M5 口径
  ——"人工复核未完成前**不得视为已验收**"——本报告**不**声称该条目已通过人工复核。
  **Owner 已确认按此口径保持"待裁定"并如实披露（2026-09-11）**。
- **Owner 裁定方式**（一条命令；`<case_id>` 取当前清单里的值 —— 每次灰度运行按 10%
  确定性抽样入队，清单随运行累积）：
  ```powershell
  $env:PYTHONUTF8=1
  python scripts/demo_s3_03_internalize.py --review-sheet      # 打印当前清单（含 case_id）
  python scripts/demo_s3_03_internalize.py --record-review --case-id <case_id> `
      --verdict pass --reviewer <Owner> --review-note "已逐字段核对与上游等价"
  ```
  裁定后 `summary()["closed"]=True`，灰度报告的 `manual_review_closed` 随之转真。

---

## 7. 上游遗留（M1–M6）核销总表

| # | S3-02 移交要求 | 本任务处置 | 证据 |
|---|---|---|---|
| M1 | 真实 LLM-judge + `judge_kind` 可区分 | ✅ 收口（通道/守卫/标签三件套；本环境如实回落） | §3.1 #10 |
| M2 | 条件⑤ 真实墙钟 p99 + 标注 clock | ✅ 收口 | §3.1 #11 |
| M3 | 结构型分支沿用分类规则或建中止注入后再纳硬闸 | ✅ 沿用 + 如实披露（未静默） | §3.1 #12、§5 #2 |
| M4 | 显式 case↔candidate 适用性字段 | ✅ 收口（含重生成后的机器重新施加） | §3.1 #13 |
| M5 | 10% 人工抽检实际执行并留痕 | ✅ 机制+清单+留痕就位，**待 Owner 逐条裁定** | §3.1 #14、§6 |
| M6 | 隔离边界明确或显式声明 | ✅ 显式声明（`real_takeover=False`） | §3.1 #15、§5 #4 |

---

## 8. 口径与诚实披露

### 8.1 未声称"已实现真实能力内化"

真实流量尚未达到"每能力 ≥20 条同类轨迹"，本任务全部验收证据（判定集、灰度样本、ROI 样本量）
均来自 **Seed Pack + 合成轨迹 + 判定集回放**（设计内的**离线设施**）。因此：

- 灰度/内化的**机制**已落地并可复现，但**真实收益未经验证**（`digest_count=2`、月样本 6 即真实侧现状）；
- 自动路径的 `promote` 判决在演示中使用了**合成样本量**，输出中已逐处标注"离线设施合成，不代表真实收益"；
- `borrowed → mirrored` 仍需真实流量 ≥20 条/能力 —— 与 S3-01/S3-02 同源，**不因本任务而改变**。

### 8.2 judge 口径

本机无模型凭证，`resolve_judge("auto")` 按要求**如实回落**并标注
`judge_kind=deterministic_local(llm_unavailable)`（附不可用原因），**未**把确定性打分器冒充为 LLM-judge。
真实 LLM 通道已由 `FakeAdapter` 路径的用例证明可跑通（`test_real_adapter_channel_scores` 等 6 例）。

### 8.3 时钟口径

- 条件⑤：`wall_clock(perf_counter; per-arm real elapsed)` —— 同一次灰度内**双跑实测**，两侧同口径可比；
- S2-01 台账的 `duration_ms`（单次能力调用墙钟）**只作披露**（`evidence["ledger_wall"]`），
  **不**用作条件⑤阈值 —— 避免重犯 S3-02 §4.6 现象 B 的量纲错误；
- 模型时钟量（`p99_model_*`）单独列出，供与 S3-02 结论对账。

### 8.4 未做的（明确声明，避免误读）

1. 未打开真实流量接管（M6，见 §5 #4）；
2. 未自动推送/合并任何 PR（守不易，见 §4.5）；
3. 未对结构型分支增加硬闸（M3，见 §5 #2）；
4. 未把 10% 抽检的"清单产出"说成"人工复核已完成"（M5，见 §6）。

---

## 9. 结论

- 交付物 **9 项齐备**（§1）；任务书 §四 **18/18 通过**（§3）；
- S3-02 移交遗留 **M1–M6 六项全部处置**（4 项收口、1 项按口径待 Owner 裁定、1 项如实披露边界）（§7）；
- 质量证据齐备：新增 **219 例**全绿、覆盖率 **91%/89%**、邻接回归 **2476 passed / 0 failed**、
  本地门禁全绿（kwarg 双路 0 处 / mypy 0 error / lint-imports 2 kept / arch 未豁免 0 /
  核心不变量 12/12 / 边界 `blocked_modules=[]` / 文档链接 0 失效 + 锚点回归 4 passed /
  测试零污染）（§4）；
- **已达成 v7.2 护城河闭环的最后一环**：`轨迹 → 判定集 → 灰度 → 自动内化 PR`（人工合入为门）。
