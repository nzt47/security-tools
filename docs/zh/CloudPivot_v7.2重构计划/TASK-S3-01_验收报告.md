# TASK-S3-01 验收报告 — 消化流水线统一（轨迹→模式挖掘→Skill 生成→stage 迁移）

> 归档日期：2026-09-11
> 所属计划：CloudPivot v7.2 重构计划（S3 消化流水线 · 首任务）
> 任务书：[TASK-S3-01_消化流水线统一.md](TASK-S3-01_消化流水线统一.md)
> 配套报告：[TASK-S3-01_stage入轨报告.md](TASK-S3-01_stage入轨报告.md)（L4 入轨 + L3 占位串切换）
> 状态：✅ **交付完成，验收清单 15/15 通过**

---

## 一、交付摘要

### 1.1 成果清单

| # | 成果 | 落点 | 行数 |
|---|---|---|---|
| 1 | 消化流水线门面 `DigestionService.pipeline()` | `agent/digestion/service.py` | 510 |
| 2 | 数据模型与门槛常量（同类判定键/候选模式/草稿/迁移/报告） | `agent/digestion/models.py` | 402 |
| 3 | 轨迹清洗（四规则）+ 同类判定键归一 | `agent/digestion/cleaning.py` | 419 |
| 4 | 参数泛化（形态占位符 + 具名参数槽） | `agent/digestion/generalize.py` | 277 |
| 5 | 双路挖掘（LCS 骨架 + 决策树分支 + 副作用画像） | `agent/digestion/mining.py` | 511 |
| 6 | SKILL.md 草稿生成（桥接 solidify / skill_converter） | `agent/digestion/generation.py` | 423 |
| 7 | stage 迁移门与执行（三处联动）+ 首次入轨 | `agent/digestion/stage.py` | 617 |
| 8 | 入口能力键归一（L1 感知的台账读取） | `agent/digestion/capability.py` | 180 |
| 9 | **【L1】** 工具名→capability_id 改写器 | `agent/descriptors/bridge.py::resolve_capability_id` + `agent/tool_calling.py::resolve_tool_capability_id` | — |
| 10 | **【L2】** wire 规划成功路径任务级 Trace + `task.closed` | `agent/orchestrator/orchestrator.py:1028-1040` / `:1193` | — |
| 11 | **【L3】** `trace_policy` 真实台账引用（含占位串切换） | `agent/descriptors/bridge.py::ledger_trace_policy` + `stage.refresh_trace_policies` | — |
| 12 | **【L4】** 存量 stage 首次入轨（28 条）+ 入轨报告 | `scripts/run_s3_01_ingest.py` + 入轨报告 | — |
| 13 | 新增单测 **332 例**（6 套件） | `tests/unit/test_digestion_*.py` / `test_s3_01_handover.py` | — |
| 14 | 端到端演示（9 步，可复现） | `scripts/demo_s3_01_digestion.py` | 287 |
| 15 | 本验收报告 + 入轨报告 | `docs/zh/CloudPivot_v7.2重构计划/` | — |

### 1.2 关键量化结果

```
新增单测：332 例全绿（Windows 本机，-p no:randomly）
覆盖率（agent/digestion/*，branch）：TOTAL 93%
  __init__ 100% / models 98% / cleaning 94% / generalize 94% / mining 94%
  generation 93% / capability 90% / stage 90% / service 89%
既有套件回归：1776 passed / 0 failed / 1 skipped（3 批，见 §四）
  批 1 distill/workflow/skills/descriptors  605 passed / 1 xfailed（既有 TF-IDF 基线）
  批 2 trace/audit/events/acr/utc/escape    796 passed
  批 3 orchestrator/planning/tool_calling   375 passed / 1 skipped（需 --runslow）
终态复核：37 个最相关既有套件在冻结树合并复跑 1449 passed / 0 failed
门禁：arch_rules --check ✅ 0 未豁免违规｜lint-imports 2 kept / 0 broken
      mypy（11 文件含新模块与脚本）Success: no issues found
      verify_core_invariants 12/12 PASS｜boundary_coverage blocked_modules=[]
      docs 链接 1336 条 0 失效 + 锚点回归 4 passed
```

---

## 二、验收清单逐条勾稽（任务书 §四，15 项）

| # | 验收标准 | 结论 | 证据（文件 + 用例 + 演示） |
|---|---|---|---|
| 1 | 真实/模拟轨迹 ≥20 条同类可产出候选模式 | ✅ | `cleaning.py` + `mining.py`；`test_digestion_pipeline.py::TestPipelineEndToEnd::test_twenty_plus_same_kind_yields_pattern`（24 条 ⇒ 3 步骨架）；演示步骤 3–4：`支撑=24/28 覆盖率=1.0 置信度=1.0` |
| 2 | 清洗规则有单测覆盖（噪声轨迹→提取模式；失败轨迹保留为负样本） | ✅ | `test_digestion_cleaning.py::TestCleanTrajectory::test_noise_trajectory_still_yields_pattern_steps`（4 规则串联后仍可提取 3 步）；`TestNegativeSamples::test_negative_trajectory_is_retained_not_dropped`；演示：`降噪 56 步 / 合并 28 步 / 负样本 4 条` |
| 3 | 同类轨迹判定键定义明确且无自相矛盾 | ✅ | `models.py::SameTaskKey`（三元组 + 逐项定义 + **不含 task_id** 的理由）；`test_digestion_cleaning.py::TestSameTaskKey`（6 例）含 `test_same_path_task_merges_same_key`（同任务不同路径 ⇒ 同键）；`TestIntentNormalization::test_windows_and_posix_paths_normalize_equal` |
| 4 | 产物为 draft 态 SKILL.md，不自动发布/不越过审批 | ✅ | `generation.py`（`status: draft` / `enabled: false` / 草稿横幅 / 落盘到暂存区）；`test_digestion_generation.py::TestDraftDiscipline`（8 例）+ `test_pipeline_never_auto_promotes`（AST 断言 service 层无升格调用）+ `test_pipeline_does_not_call_review_or_publish` |
| 5 | stage 迁移写 descriptor + 审计 + `digest.stage` 事件（三处联动一致） | ✅ | `stage.py::stage_migrate`；`test_digestion_stage.py::TestThreeWayLinkage::test_descriptor_audit_event_all_written`（三处逐一断言）+ `test_no_duplicate_audit_records`（一动作一记录）；演示步骤 6–8：`audit_seq=32 audit_action=descriptor.stage` + 链验签 ok=True |
| 6 | 存量 25 条 stage 未入轨资产全部入轨并有报告 | ✅ | [入轨报告](TASK-S3-01_stage入轨报告.md) §2 逐条勾稽（25 skill + 3 builtin = 28 条入轨，`warnings_after=0`）；`test_s3_01_handover.py::TestL4StageIngestion::test_real_ledger_has_no_unstaged_asset` |
| 7 | 迁移证据不足不静默推进（用例覆盖） | ✅ | `stage.py::evaluate_migration`；`test_digestion_stage.py::TestRefusalIsNeverSilent`（8 例：保持原 stage + `digest.stage.refused` 入链 + 事件带 reasons）；`test_digestion_pipeline.py::test_pipeline_does_not_advance_without_evidence` |
| 8 | 既有 process_distill/workflow_learning/skills 套件零回归；新增单测全绿、覆盖率 ≥80% | ✅ | §四回归三批 1776 passed / 0 failed；新增 325 例全绿；覆盖率 **93%**（最低模块 89%） |
| 9 | 端到端样例（轨迹→SKILL.md 草稿）在验收报告中可复现 | ✅ | §五（含完整命令、输入、输出与草稿全文节选） |
| 10 | **【L1】** 工具链落账 `capability_id` 与 `data/descriptors.json` 台账可 join | ✅ | `test_s3_01_handover.py::TestL1ToolChainLedgerPoint::test_trace_joins_real_ledger`（真实台账 join 成功）；`TestL1CapabilityRewrite`（9 例）；演示步骤 2：`cp.builtin.read_file / join: True` |
| 11 | **【L2】** wire 规划成功分支产出任务级 Trace 与 `task.closed`；无重复开闭（用例断言） | ✅ | `orchestrator.py:1028-1040`（起点上提）+ `:1193`（wire 收口）；`test_s3_01_handover.py::TestL2WirePathTaskTrace`（7 例），其中 `test_branch_structural_exclusivity` 用 **AST** 断言「唯一起点 + 两处收口分属 if/else 互斥分支」 |
| 12 | **【L3】** borrowed `trace_policy` 为真实 ledger 引用；`bridge.py` 默认串与 S1 既有断言同步更新 | ✅ | `bridge.py::ledger_trace_policy` + 两处默认串切换；`tests/unit/test_trace_v2.py` 断言改写（新增 `test_trace_policy_is_real_ledger_reference_not_placeholder`）；`test_s3_01_handover.py::TestL3RealLedgerTracePolicy`（10 例，含真实台账无占位串残留 + AST 断言无占位字面量） |
| 13 | **【L4】** stage 入轨报告归档，25 条 warning 清零或清单化残留 | ✅ | [TASK-S3-01_stage入轨报告.md](TASK-S3-01_stage入轨报告.md)（25 清零 + 3 builtin 一并入轨 + 残留清单 3 项）；机读 `data/digestion/s3_01_ingest_report.json` |
| 14 | 四项遗留均在验收报告逐条给出收口证据（文件 + 用例 + 演示） | ✅ | §三（L1–L4 逐条：代码落点 / 用例 / 演示输出） |
| 15 | （任务书 §三 预期成果 6 项齐备） | ✅ | §1.1 成果清单 15 项覆盖任务书 6 项预期成果 |

---

## 三、S2 遗留收口证据（L1–L4）

### 【L1】工具名 → descriptor `capability_id` 运行时改写

| 项 | 内容 |
|---|---|
| **来源** | S2-01 遗留 #1（Owner 裁定归 S3-01） |
| **落点（文件）** | `agent/descriptors/bridge.py`：`canonical_capability_id()` / `resolve_capability_id()` / `_name_index()`（带 `(registry, count)` 失效键的缓存）；`agent/tool_calling.py`：`resolve_tool_capability_id()` + `_record_unified_tool_trace()` 落账键替换；`agent/digestion/capability.py`：入口侧 `merge_keys()` / `collect_rows()` / `normalize_trace_capability()` |
| **解决问题** | S2-01 期工具级统一 Trace 以**工具名**落账 `capability_id`，join 依赖 registry 侧事后匹配 ⇒ `list_by_capability("cp.builtin.read_file")` 取不到数。改写后**同键可 join**；且入口会把**改写前**已落账的历史工具名行一并取回（否则 S2-01 台账到消化流水线这条链在"改写之后才开始有新数据"处断裂） |
| **用例** | `test_s3_01_handover.py::TestL1CapabilityRewrite`（9 例：确定性/台账命中/未登记诚实标注/已是 canonical/name 兜底/台账不可用回退/缓存失效/空名）<br>`TestL1ToolChainLedgerPoint`（5 例，含 `test_trace_joins_real_ledger` 用**真实** `DescriptorRegistry()` join）<br>`test_trace_v2_integration.py`（既有 5 处断言按其语义更新为 canonical 键，并保留"既有 tool_trace 轨仍按工具名落账"的对照断言）<br>`test_digestion_pipeline.py::TestCapabilityKeyNormalization`（10 例，含历史键收集、整任务链展开、步骤顺序） |
| **演示** | `scripts/demo_s3_01_digestion.py` 步骤 2：<br>`resolve_tool_capability_id('read_file') = cp.builtin.read_file`<br>`与 descriptor 台账 join: True` |

### 【L2】orchestrator wire 规划路径任务级 Trace 与 `task.closed`

| 项 | 内容 |
|---|---|
| **来源** | S2-01 遗留 #2 + S2-03 遗留 #1（同源） |
| **落点（文件）** | `agent/orchestrator/orchestrator.py`：起点 `_begin_unified_task_trace()` **上提**至 wire 段之前（`:1028`），LLM 段旧起点移除，wire 成功分支新增唯一收口（`:1193`） |
| **修法** | 把起点上提到「wire 规划 / LLM 直答」两条路径的**共同前驱**，收口保留两处但由二值开关 `_wire_planning_used` **互斥**分流 ⇒ 任一执行流**恰好一次**开闭。默认（`wire_enabled=false`）时两分支间仅有 inert 判定代码，行为与改前等价 |
| **解决效果** | wire 规划成功的任务自此产生任务级 Trace 与 `task.closed`，**进入 ACR 分母**（S2-03 §5 #1 的实测缺口关闭） |
| **用例** | `test_s3_01_handover.py::TestL2WirePathTaskTrace`（7 例）<br>· `test_wire_success_emits_task_trace_and_task_closed`：wire 成功 ⇒ **恰好 1** 条任务级 Trace + **恰好 1** 个 `task.closed`（含 workspace_id）<br>· `test_llm_path_still_single_open_close` / `test_wire_fallback_path_single_open_close` / `test_simple_task_single_open_close` / `test_llm_error_path_still_closes_once`：四条相邻/回退路径均**恰好一次**（无重复开闭）<br>· `test_wire_success_no_context_leak`：ContextVar 无泄漏<br>· `test_branch_structural_exclusivity`：**AST** 断言「唯一 `_begin` 调用 + 唯一 `if not _wire_planning_used` 节点的 `body`/`orelse` 各恰含一个 `_end`」⇒ 结构上不可能重复开闭<br>· `test_wire_task_included_in_acr_denominator`：`task.closed` 载荷齐备 |
| **演示** | 既有 `tests/unit/test_planning_wire.py`（5 例）零回归；本任务新增用例实测 wire 路径产出任务级 Trace |

### 【L3】borrowed 能力 `trace_policy` 真实 ledger 引用

| 项 | 内容 |
|---|---|
| **来源** | S2-01 遗留 #9（Owner 裁定并入 S3-01，不单列小任务） |
| **落点（文件）** | `agent/descriptors/bridge.py`：新增 `TRACE_LEDGER_TABLE`/`TRACE_LEDGER_DB`/`TRACE_LEDGER_READER` 常量与 `ledger_trace_policy()`；`descriptor_from_mcp_tool()`（`:155`）与 `skill_to_descriptor()`（`:390`）默认串切换；`agent/digestion/stage.py::refresh_trace_policies()` 处置存量占位串 |
| **新格式** | `trace:<source>[:<route>]:ledger=unified_traces@agent/data/tool_trace.db#capability_id=<能力ID>#read=UnifiedTraceStore.list_by_capability`（四要素齐备，可反查） |
| **存量切换** | 真实台账 4 条占位串（`cp.skill.{code-observability, frontend-state-sync, self-explanatory-ui, testing-anti-patterns}`）全部切换；走 `update_fields` ⇒ 审计动作 `descriptor.patch`（**字段级**），不与 stage 留痕混淆、不发 `digest.stage` 事件 |
| **用例** | `test_s3_01_handover.py::TestL3RealLedgerTracePolicy`（10 例，含 `test_real_ledger_policies_have_no_placeholder` 对**真实台账**断言、`test_policy_refresh_uses_patch_audit_not_stage`、`test_no_placeholder_literals_left_in_bridge` 用 AST 断言源码无占位字面量）<br>`tests/unit/test_trace_v2.py`：S1 既有断言 `assert "S2-ledger-pending" in ...` **同步更新**为「占位退场 + 真实引用四要素」 |
| **演示** | 演示步骤 9：干跑 `占位策略=2` → 实跑 `占位切换=1`（另一条随首次入轨直接落真实串）+ 逐条打印切换后 `trace_policy` |

### 【L4】stage 入轨（25 条 warning 清零）

| 项 | 内容 |
|---|---|
| **来源** | S1-02 遗留 #3（25 条 stage 未入轨 warning） |
| **落点（文件）** | `agent/digestion/stage.py::first_entry_stage()` / `backfill_stages()`；执行入口 `scripts/run_s3_01_ingest.py`（干跑默认 / `--execute`）；报告 [TASK-S3-01_stage入轨报告.md](TASK-S3-01_stage入轨报告.md) |
| **结果** | 入轨 **28** 条（25 skill + 3 builtin），失败 0，残留 stage 空缺 **0**；审计新增 28 条 `descriptor.stage`（seq 10980–11007）；`digest.stage` 事件 28 条；占位 `trace_policy` 切换 4 条 |
| **起始态裁定** | 一律 `borrowed`（七态主轨**最低证据态**）；`native` 需 30 天零回退台账（S0-02 §3.4）**不自动置位**，理由逐条写入审计 `reason` |
| **用例** | `test_digestion_stage.py::TestFirstEntryStage`（3 例）+ `TestBackfillStages`（7 例：干跑零写入/实跑全入轨/幂等/分批/空台账/来源分解/计划含 rationale）；`test_s3_01_handover.py::TestL4StageIngestion`（5 例，含真实台账断言 + 审计/事件条数断言） |
| **演示** | 演示步骤 9（对演示台账副本重放，证明可复现）；实跑命令与输出见入轨报告 §1 |

---

## 四、质量验证记录

### 4.1 新增单测

| 套件 | 例数 | 覆盖重点 |
|---|---|---|
| `tests/unit/test_digestion_cleaning.py` | 86 | 标签语汇 / intent 归一（路径无关、语序无关）/ 参数形态指纹 / 判定键三元组 / 四清洗规则（前缀、重复合并、负样本、参数归一）/ 分组去重 / 形态占位符 / 参数槽推断 |
| `tests/unit/test_digestion_mining.py` | 58 | 精确 LCS / 相似度 / 子序列判定 / 中心轨迹 / 支撑率骨架 / 覆盖率 / 可选标签 / 决策树（纯叶、分裂、深度与样本下限、平票确定性）/ 分支提取（分化、方向、位置、条件文本与分裂方向一致）/ 副作用画像 |
| `tests/unit/test_digestion_generation.py` | 43 | 质量门控（含 **与 skill_converter / solidify 阈值逐值对账**、2 步骨架接缝）/ 草稿身份稳定性 / draft 纪律 8 项（含落盘不进 `skills_repo`）/ 正文复用 solidify 结构 / 升格桥接（`run_review=False` 强制、AST 断言流水线不自动升格） |
| `tests/unit/test_digestion_stage.py` | 51 | 迁移门逐条（含 4 条验收物缺失）/ 七态与可驱动边 / run_id 确定性 / **三处联动 + 共享关联键**（含脱敏保真）/ 拒绝不静默（8 例）/ 首次入轨 / 批量回填 / stage 建议 |
| `tests/unit/test_digestion_pipeline.py` | 55 | 端到端 ≥20 条达标 / 门槛与资格 / 负样本与分支 / 参数槽 / intent 分组 / **L1 入口键归一**（10 例）/ 显式 trace_set / stage 迁移驱动 / 服务装配 |
| `tests/unit/test_s3_01_handover.py` | 39 | **L1–L4 收口**（含真实台账与 AST 结构断言） |
| **合计** | **332** | 新增套件全绿（`-p no:randomly`） |

### 4.2 覆盖率（branch，仅跑 6 个新套件）

| 模块 | Stmts | Miss | Branch | BrPart | Cover |
|---|---|---|---|---|---|
| `agent/digestion/__init__.py` | 4 | 0 | 0 | 0 | **100%** |
| `agent/digestion/models.py` | 192 | 3 | 2 | 0 | **98%** |
| `agent/digestion/cleaning.py` | 175 | 5 | 84 | 11 | **94%** |
| `agent/digestion/generalize.py` | 151 | 8 | 72 | 5 | **94%** |
| `agent/digestion/mining.py` | 250 | 12 | 126 | 12 | **94%** |
| `agent/digestion/generation.py` | 132 | 7 | 30 | 2 | **93%** |
| `agent/digestion/capability.py` | 83 | 5 | 32 | 4 | **90%** |
| `agent/digestion/stage.py` | 252 | 24 | 90 | 11 | **90%** |
| `agent/digestion/service.py` | 233 | 21 | 90 | 15 | **89%** |
| **TOTAL** | **1472** | **85** | **526** | **60** | **93%** |

> 全部 9 个模块 ≥89%，满足「覆盖率 ≥80%」。

### 4.3 既有套件零回归（三批）

| 批 | 范围 | 结果 |
|---|---|---|
| 1 | `process_distill` / `routes_process_distill` / `routes_workflow_learning` / `workflow_to_skill` / `workflow_learning` / `workflow_hybrid` / `workflow_mode` / `skills_mgmt`×3 / `skills_digest_assessor` / `skills_classifier` / `skills_cleanup` / `verify_migrated_skills` / `agentskills_io_compat` / `descriptors`×5 | **605 passed / 0 failed**（1 xfail = 既有 TF-IDF 检索基线，与本任务无关） |
| 2 | `trace_v2` / `trace_v2_integration` / `tool_trace` / `trace_store` / `trace_coverage` / `audit`×8 / `events_v1` / `acr_metrics` / `utc_cost` / `escape_guard` / `model_degrade` / `env_config_audit` | **796 passed / 0 failed** |
| 3 | `planning_wire` / `planning_defect_d7` / `orchestrator`×4 / `tool_calling`×2 / `planning_stage2-5` | **375 passed / 0 failed / 1 skipped**（需 `--runslow`） |
| **合计** | | **1776 passed / 0 failed / 1 skipped** |
| **终态复核** | 上述 37 个**最相关**套件在**冻结树**上合并复跑（交付前最后一次） | **1449 passed / 0 failed**（1 xfail = 既有 TF-IDF 基线）；另 6 个新增套件 **325 passed** |

### 4.4 门禁（本地复现 CI）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 架构规则校验 | `python -m agent.observability.arch_rules --check` | ✅ 通过，**0 未豁免违规**（仅 4 条既有豁免） |
| 循环依赖校验 | `python -m importlinter.cli lint --config .importlinter` | ✅ `exit 0`（Contracts: 2 kept / 0 broken） |
| 关键模块阻塞 mypy | `mypy agent/env_config_manager.py` / `agent/network_config.py` | ✅ `Success: no issues found in 1 source file` ×2 |
| 新模块 mypy | `mypy agent/digestion/ scripts/run_s3_01_ingest.py scripts/demo_s3_01_digestion.py` | ✅ `Success: no issues found in 11 source files` |
| 核心不变量 | `python scripts/verify_core_invariants.py` | ✅ `12/12 项通过, 0 项被破坏` |
| 边界覆盖扫描 | `python scripts/check_boundary_coverage.py` | ✅ `blocked_modules=[]`（99 模块 / 15252 测试 / 场景覆盖 96%） |
| 生成产物不入库 | `git checkout -- docs/observability/boundary_coverage_report.json` | ✅ 已还原，交 CI 独占（沿用 S2-02 §4.11 约定） |

---

## 五、端到端样例（轨迹 → SKILL.md 草稿，可复现）

### 5.1 复现命令

```bash
# 端到端演示（9 步；全部隔离到临时目录，仅产物归档到 data/digestion/demo/）
python scripts/demo_s3_01_digestion.py

# 产物
data/digestion/demo/digestion_report.json      # DigestionReport（机读）
data/digestion/demo/demo_summary.json          # 演示摘要（含 L1 join / stage / draft）
data/digestion/demo/dig-*.SKILL.md             # 草稿全文
```

### 5.2 输入（合成的同类轨迹台账）

28 个任务，每个任务 = 任务级 Trace + 能力级子 Trace；**刻意注入两类噪声**（探索前缀
`list_dir`、相邻重试重复 `read_file`×2）与 **4 条失败轨迹**（缺末步 `write_file`）：

```
任务 i:  list_dir → cp.builtin.read_file ×2 → cp.builtin.shell_execute → cp.builtin.write_file
任务 i（失败，每 7 条 1 条）:  list_dir → cp.builtin.read_file ×2 → cp.builtin.shell_execute
```

### 5.3 输出（实跑节选）

```
[步骤 1] 合成统一轨迹台账
  任务数=28 成功=24 失败=4
  台账行数=164

[步骤 2] 【L1】工具链落账键改写
  resolve_tool_capability_id('read_file') = cp.builtin.read_file
  与 descriptor 台账 join: True

[步骤 3] 运行消化流水线 DigestionService.pipeline()
  轨迹总数=28 清洗后=28 负样本=4
  清洗统计: dropped_steps=56 merged_steps=28
            noise_flags=['duplicate_step_merged','explore_prefix','negative_sample']
            collect: keys=['cp.builtin.read_file','read_file'] legacy_keys=['read_file']
  达标=True 说明=同类轨迹 24 条（≥20）且骨架 3 步可提取

[步骤 4] 候选模式
  pattern_id=pat_b592b5996d185fe0
  骨架(3 步): read_file → shell_execute → write_file
  参数槽=['${cmd}', '${cmd_2}']
  分支条件=['步骤 `write_file` 出现', '步骤 `write_file` 缺失']
  支撑=24/28 覆盖率=1.0 置信度=1.0

[步骤 5] SKILL.md 草稿
  skill_id=dig-cp-builtin-read-file-56353a7e status=draft

[步骤 6] stage 迁移：descriptor + 审计 + digest.stage 事件
  None → borrowed applied=True verdict=applied
  trace_policy=trace:builtin:call-side:ledger=unified_traces@agent/data/tool_trace.db#capability_id=cp.builtin.read_file#read=UnifiedTraceStore.list_by_capability
  event_id=ev_d72f279b701865d90b91f88800f6ccef audit_seq=32 audit_action=descriptor.stage
  descriptor.evolution.stage=borrowed

[步骤 7] 事件流核验
  skill.generated: 1 条 | event_id=ev_fd640fd3025fb1552b3569dd71b79e8d
  digest.stage:    1 条 | event_id=ev_d72f279b701865d90b91f88800f6ccef

[步骤 8] 链式审计核验
  链上记录数=32 actions=['descriptor.register','descriptor.stage','trace.closed']
  descriptor.stage 条数=1
  链验签: ok=True checked=32

[步骤 9] 【L3+L4】首次入轨 + 占位 trace_policy 切换
  干跑：资产=4 未入轨=3 占位策略=2
  实跑：入轨=3 占位切换=1 残留未入轨=0
```

### 5.4 产出的 SKILL.md 草稿（全文，76 行）

> 归档：`data/digestion/demo/dig-cp-builtin-read-file-56353a7e.SKILL.md`

```markdown
---
id: dig-cp-builtin-read-file-56353a7e
name: '[草稿] cp.builtin.read_file 消化模式'
description: 自动挖掘草稿：来自 24 条同类轨迹，覆盖率 100%。已达升格门槛，待人工与 S3-02/S3-03 验收
category: custom
tags:
- from_digestion
- draft
- auto_mined
- cp.builtin.read_file
version: 0.1.0
enabled: false
status: draft
author: digestion_pipeline
source: digestion
content_type: markdown
default_params:
  pattern_id: pat_b592b5996d185fe0
  capability_id: cp.builtin.read_file
  same_task_key: cp.builtin.read_file|shape:encoding+path|success
dependencies:
- cp.builtin.read_file
- cp.builtin.shell_execute
- cp.builtin.write_file
---

> ⚠️ **自动挖掘草稿（draft）**：本文件由消化流水线（TASK-S3-01）从统一轨迹台账
> 挖掘生成，**未经人工审核、未发布、未启用**。发布须经 S3-02 判定集 / S3-03
> shadow 灰度与既有评审门，切勿直接投产。

## 副作用画像
- 写入: （无）
- 删除: （无）
- 外部调用: （无）
- 回滚提示: 仅写类副作用：回滚可按 files_written 清单逆序恢复或忽略
- 负样本: 4 条同类失败轨迹（未参与骨架，仅供评测与失败规避）

## 分支条件（决策树提取）
- [第 3 步] 步骤 `write_file` 出现 → 历史倾向 **success**（24/28，86%）｜成功组出现率 100% vs 失败组 0%（Δ=+100%）
- [第 3 步] 步骤 `write_file` 缺失 → 历史倾向 **failure**（4/28，14%）｜满足该条件时历史多为失败 —— 生成草稿需附失败规避说明（不得据此自动发布）

# 消化草稿 cp.builtin.read_file

由消化流水线从 24/28 条同类轨迹挖掘的候选模式（覆盖率 100%，LCS 骨架长度 3，置信度 1.00）。同类判定键：cp.builtin.read_file|shape:encoding+path|success。 参数槽：${cmd}, ${cmd_2}。 分支条件 2 条。

## 触发条件
- `cp.builtin.read_file`
- `intent-key=shape:encoding+path`

## 步骤清单
### 步骤 1: `cp.builtin.read_file`
- 调用能力 `cp.builtin.read_file`（归一标签：read_file）
- 边界: 支撑率 100%（24 条同类轨迹）

### 步骤 2: `cp.builtin.shell_execute`
- 调用能力 `cp.builtin.shell_execute`（归一标签：shell_execute）
  **参数:**
  ```json
  {"cmd": "${cmd}"}
  ```
- 边界: 支撑率 100%（24 条同类轨迹）

### 步骤 3: `cp.builtin.write_file`
- 调用能力 `cp.builtin.write_file`（归一标签：write_file）
  **参数:**
  ```json
  {"cmd_2": "${cmd_2}"}
  ```
- 条件: `步骤 write_file 出现`
- 边界: 支撑率 100%（24 条同类轨迹）

## 来源
- trace-set:cp.builtin.read_file|shape:encoding+path|success
- pattern:pat_b592b5996d185fe0
- method:lcs+decision_tree
```

**draft 态证据**：`status: draft`、`enabled: false`、草稿横幅、落盘位置为草稿暂存区
（`data/digestion/drafts/`，**非** `skills_repo/`）；流水线未调用 `review`/`publish`/
`optimize_with_feedback`（AST 用例固化）。

---

## 六、设计裁定与披露

### 6.1 同类轨迹判定键（T3 要求"定义明确且无自相矛盾"）

```
SameTaskKey = (capability_id, intent_key, outcome)

① capability_id : 轨迹归属能力的 canonical capability_id（L1 改写后的键；历史工具名
                  行在读取时归一到同一键）
② intent_key    : **任务意图类别**归一（非任务身份）。优先级：
                  (1) 显式传入的意图文本 → normalize_intent()（NFKC → 抹掉路径/UUID/
                      时间戳/数字 → ASCII 整词 + CJK 按字切分 → 去停用词 → 去重排序）
                  (2) Trace side_effects.notes 的 `intent:<文本>` 标注
                  (3) **请求参数形态指纹**（顶层键集合 + 一层嵌套键；不含取值）← 默认
                  (4) "unknown"（无信号时如实标注）
③ outcome       : 任务级结果状态（success / failure）——成功与失败**分属不同键**，
                  失败轨迹不混入成功骨架，但仍保留为负样本
```

**不含 `task_id`**（明确披露）：任务身份不是"同类"的依据 —— 否则 20 条门槛永远达不到；
任务身份保留在 `Trajectory.task_id` 供溯源。

**两个"无关性"保证**（各有单测）：
- **顺序无关**：`intent_key` 排序后连接 ⇒ 同义不同语序归一（`test_word_order_is_irrelevant`）；
- **取值无关**：先抹掉路径/时刻/编号 ⇒ 同任务换文件换时刻仍同键
  （`test_windows_and_posix_paths_normalize_equal`）。

**"同任务不同路径"的归并语义**：指同一任务的**不同执行路径**（步骤顺序/多寡不同）——
判定键不含步骤序列，故必然归并（`test_same_path_task_merges_same_key`）。

### 6.2 三处联动与"一动作一记录"

| 落点 | 写入方 | 形态 |
|---|---|---|
| `descriptor.evolution.stage` | `registry.set_stage()` | 台账字段 |
| 链式审计 | **registry 内部**（`_audit("descriptor.stage")`） | `data/audit/audit_chain.db` |
| `digest.stage` 事件 | `stage._emit_digest_stage_event()` | `data/events/events.jsonl` |

- `digest.stage` **不在** `AUDIT_MIRROR_TYPES`（S2-03 裁定只镜像 `policy.denied` /
  `healing.triggered` / `model.degraded` / `escape`）⇒ 事件**不会二次入链**；
- stage 写入的链式留痕由 registry **独占**，本任务**不**再加一次 `audit.record`
  （避免复现 S2-03 §4.4 的"一动作多记录"缺陷，用例 `test_no_duplicate_audit_records` 固化）；
- **唯一例外**：被拒迁移（台账未被触碰 ⇒ 链上本无记录）补一条 `digest.stage.refused`，
  使"拒绝推进"这一治理决策同样可审计（**不静默**）。
- `trace_policy` 占位串切换走 `descriptor.patch`（字段级），**不**发 `digest.stage`
  —— 语义与 stage 迁移分离（用例 `test_policy_refresh_uses_patch_audit_not_stage`）。

**共享关联键（三处可互查）**：链式审计与 `digest.stage` 事件是**两条独立轨道**，
若不共享关联键，审计只能靠 `(subject, detail.to)` 隐式 join。因此本任务把
`digest_run_id` **固定写进链上 `reason`**（`StageMigration.audit_reason`，由
`stage_migrate` 单点追加，调用方自定义 `reason` 也一并追加、已含则不重复）：

```
链上 descriptor.stage  reason="…（run=dg_<24hex>）"
事件 digest.stage      payload.digest_run_id="dg_<24hex>"     ← 同一键
descriptor 台账         evolution.stage / trace_policy          ← 同 subject
```

> **为何需要显式追加（实现期实测确认的缺口）**：最初仅"流水线默认 reason"含 run id，
> 而**首次入轨路径传入自定义 rationale 时不含** ⇒ 该路径（L4 的 28 条）的两轨无法互查。
> 已收敛到 `stage_migrate` 单一位置统一追加。
>
> **为何可安全写进 `reason`**：实测核验 24 / 32 / 64 位 hex 关联键经链载荷脱敏
> **原样保留未被掩码**（S2-02 D6 记录的"24 位 hex 被部分掩码"在当前实现下不再触发），
> 故写入 reason 文本可安全反查 —— 该保真性由用例
> `test_correlation_key_survives_chain_redaction` 钉住（一旦脱敏规则回归即红）。

**用例**：`test_three_rails_share_one_correlation_key`（三处逐一断言共享键 +
`subject == capability:<id>` 反向定位）、`test_caller_reason_also_carries_run_id`、
`test_run_id_not_duplicated_when_already_present`、`test_correlation_key_survives_chain_redaction`。

### 6.3 升格门口径对齐（实现期实测到的接缝，已闭合）

**接缝**：本模块的质量门与下一跳既有门最初**不同口径** —— `MIN_PATTERN_STEPS = 2`
（"骨架是否成形"，服务于 S3-02 判定集）作为升格前门使用时比
`solidify._quality_check` 对 `method="rule"` 的 `_MIN_RULE_STEPS = 3` **更松**。
后果：一个 2 步骨架的候选模式会显示"**已达升格门槛**"，却在 opt-in 升格时被
下一跳返回 `{"action":"skipped","reason":"规则降级产物步骤过少(2 < 3)…"}` —— 即
"过了门却被静默拒绝"。这是实现期实测确认的真实接缝（非推测）：

```
steps=1: digestion_gate=False | solidify_gate='规则降级产物步骤过少(1 < 3)…'
steps=2: digestion_gate=True  | solidify_gate='规则降级产物步骤过少(2 < 3)…'   ← 接缝
steps=3: digestion_gate=True  | solidify_gate=None                          ← 对齐
```

**闭合方式**：把升格前门的步骤数下限独立为 `generation.MIN_ASCENSION_STEPS = 3`，
与既有 `solidify._MIN_RULE_STEPS` **逐值对账**（`solidify_min_rule_steps()` 懒读取 +
用例断言相等，防随版本漂移）。两个常量语义分开并各自命名：

| 常量 | 值 | 回答的问题 | 用途 |
|---|---|---|---|
| `models.MIN_PATTERN_STEPS` | 2 | 骨架是否**成形** | `report.eligible`、S3-02 判定集输入 |
| `generation.MIN_ASCENSION_STEPS` | 3 | 产物能否经**既有门控**升格 | draft front matter 的"是否达升格门槛"、opt-in 升格 |

**为何不直接把 `MIN_PATTERN_STEPS` 提到 3**：那会把"2 步骨架"从"可提取的模式"里
错误剔除 —— 2 步骨架对 S3-02 的判定集仍有价值，只是**不足以升格**。两问不同，故两值不同。

**用例**：`test_two_step_skeleton_rejected_by_ascension_gate`（同时断言
`is_shallow is False` 与 `pattern_quality_gate 为 False`，把"成形 ≠ 可升格"钉死）、
`test_ascension_steps_align_with_solidify`（逐值对账）。

### 6.4 确定性（可复现性）

| 环节 | 确定性来源 |
|---|---|
| 骨架 | 多序列 LCS 是 NP-hard，任何近似都会随输入顺序漂移 ⇒ 改用"**中心轨迹**（LCS 相似度之和最大，同分按内容字典序）+ **支撑率**过滤"，结果只依赖轨迹**集合** |
| 决策树 | Gini 不纯度下降最大；**同分按特征名字典序**；叶标签平票取字典序在前者 |
| pattern_id / skill_id / digest_run_id / event_id | 全部由内容哈希派生（重放幂等） |
| 步骤顺序 | 保留统一台账的 `started_at` 升序（**不加** trace_id 之类随机兜底键 —— 那会把同一时刻的步骤打乱，是实现期实测到的真实缺陷） |
| 用例 | `test_deterministic_across_runs` / `test_deterministic`（mining/generation）/`test_deterministic_ordering` |

### 6.5 三轨复用关系（任务书要求"避免第三套固化逻辑"）

| 既有轨 | 保留的入口 | 与本管道的关系 |
|---|---|---|
| ① `skills_mgmt` 评审-消化 | `assess`/`review` 权威评审 | 本管道产物停在 **draft**，发布权与评审权**仍归①**；`generation.solidify_draft()` 为 opt-in 桥接（**强制 `run_review=False`**，因该参数默认 `True` 且会经 `SkillReviewer` 改写技能状态 ⇒ 属"越过审批"） |
| ② `process_distill` 素材蒸馏 | `ProcessDistillService.distill` | 本管道**复用其正文编译器** `solidify._compile_skill_content` 与 `DistilledProcess` 模型；素材侧入口不变 |
| ③ `workflow_learning` 轨迹序列学习 | `WorkflowLearningService`（L2，中间过渡不入态） | 本管道复用其**质量门控词汇**（`MIN_SUCCESS_COUNT`/`MIN_CONFIDENCE`/`MIN_PRIORITY`，用例断言一致防漂移）与 `LearnedWorkflow` 载体；`generation.to_learned_workflow()` 把候选模式归一到 L3 同一产物形态 |

即：三条入口各自保留，**L3 产物（SKILL.md + `evolution.stage` 叠加）统一由本管道落地**。

### 6.6 范围边界（不越权）

| 能力 | 归属 | 本任务处置 |
|---|---|---|
| 确定性回放沙箱 / 验收门 / 判定集 | S3-02 | 未实现；`stage` 对 `mirrored → shadow` 等下游边返回 `deferred_to_downstream` 并记录 |
| shadow / 灰度 / 内化 | S3-03 | 同上；`generation.promote_workflow_to_skill()` / `solidify_draft()` 为 opt-in 通道预留 |
| 发布签名 / 人工评审 | 既有 `skills_mgmt` 轨 | 产物一律 draft，不触碰 |
| `borrowed → mirrored` 真实推进 | 本任务（已实现门控） | 门控就绪；真实台账尚无 ≥20 条同类轨迹 ⇒ **本次无真实能力推进**（如实记录，不静默推进） |

---

## 七、遗留与交接

| # | 遗留 | 归属 | 阻塞性 |
|---|---|---|---|
| 1 | `borrowed → mirrored` 需每个能力累积 ≥20 条**同类**轨迹；真实流量尚未达到 ⇒ 门控已就绪但未触发（`stage_recommendation.shortfall` 会逐次披露缺口） | 随真实流量；S3-02/S3-03 验收后推进 | 不阻塞 |
| 2 | `intent_key` 默认取**参数形态指纹**；若上游能提供真实任务意图文本（如 L2 Core-50 落地后），应改走 `intent=` 显式通道以获得更细的同类划分 | S5-02（L2 意图分类）落地后回头接入 | 不阻塞（当前口径已在报告中显式披露） |
| 3 | CJK 意图归一按**字**切分而不引入分词依赖：宁可同义词不合并（宁可冗余不误合），也不让归一结果依赖外部词典版本 | 若 S5 引入受控词典可再评估 | 不阻塞（已披露取舍） |
| 4 | 决策树特征仅含"可选步骤有无 + 步数档位"；参数级条件（如"路径含 test 时才需 review"）未纳入 | 随 S3-02 判定集需求扩展 | 不阻塞 |
| 5 | 草稿暂存区 `data/digestion/drafts/` **无 TTL**（与 S2-01 台账保留策略同源） | S2/S5 生产化（与台账/事件/审计保留策略一并定） | 不阻塞 |
| 6 | 单机单写者：消化流水线为**离线批处理**，未做跨进程调度/锁（同 S2-01 #6） | S2 生产化 | 不阻塞 |
| 7 | `first_entry_stage()` 一律 `borrowed` 起步；`native`/`internalized` 需 30 天零回退台账与验收门 | S6-01 能力地图 + 时间积累 | 不阻塞（已写入审计 reason 与入轨报告） |
| 8 | S1-02 遗留 #2「NEEDS_REVIEW 7 条 / 6 资产」属 provenance/risk 人工复核，与本任务（stage 域）不同域，未在本轮处置 | S3-03 手动 promote 通道 + 人工复核 | 不阻塞 |

---

## 八、结案结论

**TASK-S3-01 达到验收标准：**

- ✅ 任务书 §三 预期成果 **6/6** 齐备（统一管道 + 清洗模块 + 挖掘与草稿桥接 + stage 迁移与入轨报告 + S2 遗留 4 项收口 + 本验收报告）；
- ✅ 任务书 §四 验收清单 **15/15** 逐条给出「文件 + 用例 + 演示」三级证据；
- ✅ **S2 移交的 4 项遗留（L1–L4）全部收口**，其中 L2 的 ACR 分母缺口（S2-03 §5 #1）与
  L4 的 25 条 stage warning（S1-02 §5 #3）均以可复现命令清零；
- ✅ 新增 325 例全绿、覆盖率 **93%**（最低模块 89%）；既有三批 **1776 passed / 0 failed** 零回归；
- ✅ 本地门禁全绿：arch_rules 0 未豁免违规 / lint-imports 2 kept 0 broken / mypy Success /
  核心不变量 12/12 / 边界覆盖 blocked_modules=[]；
- ✅ 端到端样例（轨迹 → SKILL.md 草稿）在 §五 可逐步复现，产物已归档可查。

**下一任务接口**：S3-02（判定集与回放沙箱）可直接消费本任务产出的
**清洗后的确定性轨迹集**（`TraceSet` + `Trajectory`）、**候选模式**（`CandidatePattern`：
骨架 / 参数槽 / 分支条件 / 副作用画像）与 **draft 态 SKILL.md**；S3-03（shadow 与内化引擎）
可直接消费 `generation.promote_workflow_to_skill()` / `solidify_draft()` 两个 opt-in 升格入口
与 `stage.evaluate_migration()` 的下游边门控骨架。
