# TASK-S3-01 存量 stage 入轨报告（L4 收口 + L3 占位串切换）

> 归档日期：2026-09-11
> 所属任务：[TASK-S3-01 消化流水线统一](TASK-S3-01_消化流水线统一.md) §零 L3 / L4、§二 步骤 4
> 台账：`data/descriptors.json`（运行时台账，**非 git 跟踪**）
> 执行入口：`scripts/run_s3_01_ingest.py`（干跑默认 / `--execute` 实跑）
> 机读报告：`data/digestion/s3_01_ingest_report.json`
> 台账备份：`data/digestion/descriptors_pre_ingest_20260911_013027.json`（入轨前逐字快照）
> 状态：✅ **25 条 S1-02 warning 清零；占位 `trace_policy` 全部切换为真实台账引用**

---

## 1. 执行摘要

| 项 | 干跑 | 实跑 |
|---|---|---|
| 台账资产总数 | 32 | 32 |
| stage 为空（未入轨） | **28** | — |
| 首次入轨成功 | 0（干跑不写） | **28** |
| 首次入轨失败 | 0 | **0** |
| 入轨后仍为空的资产 | — | **0** |
| 占位 `trace_policy` | 4 | — |
| 占位 `trace_policy` 已切换 | 0（干跑不写） | **4** |
| 切换失败 | 0 | **0** |
| 链式审计新增（`descriptor.stage`） | — | **28** 条（seq 10980–11007） |
| `digest.stage` 事件 | — | **28** 条（唯一 event_id，幂等） |

**逐条命令与输出**

```
$ python scripts/run_s3_01_ingest.py --dry-run
[干跑] 台账=data/descriptors.json
  资产总数=32 未入轨=28 按来源={'builtin': 3, 'skill': 25}
  入轨成功=0 失败=0 残留未入轨=28
  占位 trace_policy=4 已切换=0
  报告=data\digestion\s3_01_ingest_report.json

$ python scripts/run_s3_01_ingest.py --execute
[实跑] 台账=data/descriptors.json
  备份=data\digestion\descriptors_pre_ingest_20260911_013027.json
  资产总数=32 未入轨=28 按来源={'builtin': 3, 'skill': 25}
  入轨成功=28 失败=0 残留未入轨=0
  占位 trace_policy=4 已切换=4
  报告=data\digestion\s3_01_ingest_report.json
```

**入轨后台账终态**

```
total 32
stage dist: {'borrowed': 32}
empty stage: 0
placeholder policies: 0
```

---

## 2. 与 S1-02 遗留的勾稽（25 条 warning 的来源与清零）

S1-02 结案报告 §5 遗留 #3 原文：

> stage 未入轨 25 条 warning（无验收证据不置位） → S3-01 步骤 4 首次入轨报告

| 口径 | 数量 | 说明 |
|---|---|---|
| S1-02 记录的 warning | **25** | 29 条技能类资产中 stage 为空者（另 4 条外来导入已按 §3.2 判为 `borrowed`） |
| 本次实跑入轨的 `skill` 类 | **25** | 与上表**逐条对应**，全部以 `borrowed` 起步 |
| 本次实跑入轨的 `builtin` 类 | 3 | S2-01 的 `load_runtime_descriptors()` 新增登记（`cp.builtin.read_file` / `shell_execute` / `write_file`），当时同样 stage 为空 |
| 合计 | **28** | 入轨后 `stage` 为空者 **0** |

> **为什么 builtin 也入 `borrowed`**：`borrowed` 是七态主轨的**最低证据态**。内置工具
> `provenance=verified`（本地代码即证据），但 S0-02 §3.4 明确 `native` 需 **30 天零回退
> 台账**，缺口不置位（宁可冗余不可误合）——故存量一律以 `borrowed` 起步，待台账积累后
> 再由 S3-02/S3-03 与 S6-01 能力地图推进。该裁定逐条写入审计 `reason` 字段（见 §4）。

---

## 3. 入轨态与轨迹引用策略（L3 同批收口）

每条入轨资产同时获得**真实统一轨迹台账引用**（非 S2 期占位串），构造器与
`agent/descriptors/bridge.py::ledger_trace_policy()` **同源**（单一实现，无第二套拼接逻辑）：

```
trace:<source>[:<route>]:ledger=unified_traces@agent/data/tool_trace.db
                          #capability_id=<能力ID>#read=UnifiedTraceStore.list_by_capability
```

四个要素齐备 ⇒ 人工或工具都能反查「该 borrowed 能力的完整轨迹落在哪张表、按什么键、
用哪个读接口取」：

| 要素 | 值 | 来源 |
|---|---|---|
| 表 | `unified_traces` | S2-01 `trace_v2.py`（append-only，与 `tool_traces` 同库） |
| 库 | `agent/data/tool_trace.db` | S2-01「不新增第二存储介质」裁定 |
| join 键 | `capability_id` | 即 L1 改写后的落账键（见 §5） |
| 读接口 | `UnifiedTraceStore.list_by_capability` | S2-01 交付的 S3 挖掘入口 |

按来源分三档（`route` 保留原策略语义）：

| source_type | route | 示例 |
|---|---|---|
| `skill`（外来导入） | `import-ledger` | `trace:skill-import:import-ledger:ledger=…` |
| `mcp` | `call-side` | `trace:<server>:call-side:ledger=…` |
| `builtin` | `call-side` | `trace:builtin:call-side:ledger=…` |

### 3.1 存量 4 条 borrowed 的占位串切换（L3 落点）

入轨前 `data/descriptors.json` 中 4 条外来导入技能的 `trace_policy` 仍是 S1-01 期占位串：

| capability_id | 切换前（占位） |
|---|---|
| `cp.skill.code-observability` | `trace:skill:code-observability:import-ledger(S2-pending)` |
| `cp.skill.frontend-state-sync` | `trace:skill:frontend-state-sync:import-ledger(S2-pending)` |
| `cp.skill.self-explanatory-ui` | `trace:skill:self-explanatory-ui:import-ledger(S2-pending)` |
| `cp.skill.testing-anti-patterns` | `trace:skill:testing-anti-patterns:import-ledger(S2-pending)` |

切换**走 `registry.update_fields({"evolution.trace_policy": ...})`**，其审计动作是
`descriptor.patch`（**字段级**变更）而**不是** `descriptor.stage`（stage 未变），
故不会与 stage 迁移的留痕语义混淆，也不发 `digest.stage` 事件 —— 避免 S2-03 §4.4
记录的"一动作多记录"缺陷复现（用例
`test_s3_01_handover.py::test_policy_refresh_uses_patch_audit_not_stage` 固化该不变量）。

### 3.2 默认串的源码侧切换（同 L3）

| 文件 | 变更 |
|---|---|
| `agent/descriptors/bridge.py` | 新增 `ledger_trace_policy()` / `TRACE_LEDGER_*` 常量；`descriptor_from_mcp_tool()` 与 `skill_to_descriptor()` 的默认串由 `…(S2-ledger-pending)` / `…(S2-pending)` 改为真实台账引用 |
| `tests/unit/test_trace_v2.py` | S1 既有断言 `assert "S2-ledger-pending" in ref["trace_policy"]` → 拆为「占位串已退场」+「真实引用四要素」两条断言（含新增用例 `test_trace_policy_is_real_ledger_reference_not_placeholder`） |

---

## 4. 逐条入轨记录（节选，完整见机读报告）

每条记录含 `capability_id` / `to` / `applied` / `verdict` / `event_id` / `audit_seq` /
`audit_hash` / `reasons`。以下为前 3 条与后 2 条（`by_source_type` = `{builtin: 3, skill: 25}`）：

| capability_id | to | applied | event_id | audit_seq |
|---|---|---|---|---|
| `cp.builtin.read_file` | `borrowed` | ✅ | `ev_9f00192d3adc3fd8b450f19ff8f6f317` | 10980 |
| `cp.builtin.shell_execute` | `borrowed` | ✅ | `ev_…` | 10981 |
| `cp.builtin.write_file` | `borrowed` | ✅ | `ev_…` | 10982 |
| …（25 条 skill 类，同上） | `borrowed` | ✅ | `ev_…` | 10983–11007 |
| `cp.skill.voice_interaction`（末条） | `borrowed` | ✅ | `ev_777d303391bccea31cce53a317df0112` | 11007 |

**审计 `reason`（逐条同构，可 grep）**

```
TASK-S3-01 首次入轨：stage 为空 → borrowed（七态主轨最低证据态）；
source_type=<builtin|skill>；不置 native（需 30 天零回退台账，S0-02 §3.4）
（run=<digest_run_id>）
```

**三处联动一致性**（§13.3）：每条入轨同时产生

1. `descriptor.evolution.stage` = `borrowed`（台账字段，`reg.get(cid)` 可验）；
2. 链式审计 `descriptor.stage`（`data/audit/audit_chain.db`，subject=`capability:<cid>`）；
3. `digest.stage` 事件（events.v1 信封，`payload.scope="first_entry_backfill"`）。

用例固化：`test_s3_01_handover.py::TestL4StageIngestion::test_ingestion_is_audited_and_evented`
断言「2 条资产 ⇒ 链上恰好 2 条 `descriptor.stage` + 恰好 2 条 `digest.stage` 事件」。

---

## 5. 与 L1（工具名 → capability_id 改写）的闭环

入轨资产的 `capability_id` 即 L1 改写后的落账键，故三者的键**逐字相同**：

```
工具链落账键    cp.builtin.read_file          （agent/tool_calling.py::resolve_tool_capability_id）
descriptor 台账 cp.builtin.read_file          （data/descriptors.json）
trace_policy    #capability_id=cp.builtin.read_file
```

**可复现验证（用例）**：

- `test_s3_01_handover.py::TestL1ToolChainLedgerPoint::test_trace_joins_real_ledger`
  —— 经 `_execute_safe("read_file", ...)` 真实落账后，用**真实** `DescriptorRegistry()`
  取同键 descriptor，断言非 None（即 join 成功）；
- `test_s3_01_handover.py::TestL1CapabilityRewrite::test_resolve_matches_ledger_key`
  —— 改写结果与登记键逐字相同。

**可复现验证（演示）**：`scripts/demo_s3_01_digestion.py` 步骤 2 输出

```
resolve_tool_capability_id('read_file') = cp.builtin.read_file
与 descriptor 台账 join: True
```

---

## 6. 处置取向与回滚

| 项 | 取向 |
|---|---|
| 入轨范围 | **全部** stage 为空的资产（28 条），不止 S1-02 的 25 条 —— 残余空缺是下一轮的 warning 源头，一并清零 |
| 起始态 | 一律 `borrowed`（最低证据态）；`native`/`internalized` **不自动置位**（S0-02 §3.4 需 30 天台账与验收门） |
| 幂等 | 再次执行 `--execute` ⇒ `empty_stage=0`、`plan=[]`、无新增审计/事件（用例 `test_idempotent_second_run`） |
| 干跑 | `--dry-run`（默认）产出完整计划（含逐条 `trace_policy` 与 `rationale`），**零写入** |
| 回滚 | 入轨前台账已逐字备份至 `data/digestion/descriptors_pre_ingest_20260911_013027.json`；如需回滚，覆盖回 `data/descriptors.json` 即可（链式审计为 append-only，**不回滚**，保留"曾入轨"的事实） |
| 失败不静默 | 单条入轨失败 ⇒ 记入 `failed[]` 并**保持原 stage**；脚本 exit code 非 0（本次 0 失败） |

---

## 7. 残留清单

| # | 残留 | 归属 | 阻塞性 |
|---|---|---|---|
| 1 | `borrowed` 只是起点；`borrowed → mirrored` 需每个能力累积 ≥20 条同类轨迹 —— 当前真实台账尚无足够轨迹，故**没有任何真实能力在本次入轨中推进到 `mirrored`**（如实记录，不静默推进） | S3-01 管道已就绪，随真实流量累积触发；门控与证据模型见 `agent/digestion/stage.py` | 不阻塞 |
| 2 | `mirrored → shadow` 等的门控归 S3-02（判定集/回放沙箱）与 S3-03（shadow/灰度）；本任务对下游边返回 `deferred_to_downstream` | S3-02 / S3-03 | 不阻塞 |
| 3 | S1-02 遗留 #2「NEEDS_REVIEW 7 条/6 资产」属 provenance/risk 人工复核，与本报告（stage 入轨）不同域，未在本次处置 | S3-03 手动 promote 通道 + 人工复核 | 不阻塞 |

---

## 8. 结论

- ✅ **25 条 S1-02 stage 未入轨 warning 清零**（实际入轨 28 条 = 25 skill + 3 builtin），
  残留 stage 空缺 **0**；
- ✅ **4 条占位 `trace_policy` 全部切换**为真实统一台账引用，源码侧默认串同步切换，
  S1 既有断言同步更新；
- ✅ 三处联动（descriptor 字段 + 链式审计 + `digest.stage` 事件）逐条一致，且
  **无重复留痕**（stage 写入审计由 registry 独占）；
- ✅ 幂等、可干跑、可回滚、失败不静默 —— 均有用例覆盖。
