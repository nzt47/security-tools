# TASK-S2-01 验收报告 — Trace 统一规格 + TraceContext 透传

> 归档日期：2026-09-10
> 所属计划：CloudPivot v7.2 重构计划（S2 数据与可观测层 · 首任务）
> 任务：[TASK-S2-01_Trace统一与透传.md](TASK-S2-01_Trace统一与透传.md)
> 依赖输入：TASK-S1-01（`agent/descriptors/` 契约层 + Registry）、TASK-S1-02（存量台账回填）
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §2.7（TraceContext）/
> §3.4（Trace 规格）/ P7.1-19（workspace_id）/ P7.2-08（workspace = 逻辑租户）
> 状态：✅ 验收通过（验收清单 8/8 核验，见 §三）

---

## 一、执行摘要

1. **统一 Trace 门面交付**：新增 `agent/observability/trace_v2.py`（1241 行），在既有
   `tool_trace.py`（工具级 SQLite 轨迹）**之上**扩展统一 schema，提供向上兼容的公共
   Trace 门面。**不删除任何既有写入方**——`tool_trace.py` / `subscriber.py` /
   `tracer.py` / orchestrator 的既有轨迹设施零改动，trace_v2 为增量第三层。
2. **字段对齐 §3.4 全量落地**：`UnifiedTrace` 覆盖 trace_id / task_id / capability_id
   / actor(human|auto|sub_agent) / tenancy(tenant+workspace) /
   request(args_redacted+args_hash+idempotency_key) /
   response(status+output_redacted+output_hash+error_code) / timing / cost /
   side_effects / parent_trace_id / schema_version（26 个扁平字段，`to_dict()` 全量可测）。
3. **TraceContext 穿透（§2.7）**：结构化 `TraceContext`
   （trace_id/task_id/tenant_id/workspace_id/subject_id/policy_version）以 ContextVar
   承载，提供 `current()/enter()/exit()/child()`；`child()` 生成新 trace_id 并**自动串联**
   `parent_trace_id`。三层接线：会话层（`SessionManager.workspace_id_for`）→ 任务编排层
   （orchestrator `process()` 工具执行段）→ 工具执行链（`ToolCallingService._execute_safe`）。
4. **脱敏先于哈希**：`redact → hash → 持久化` 顺序由 `redact_then_hash()` 硬编码，
   复用既有 `agent.utils.sensitive_data_filter`（不可用时内置兜底掩码）；密钥注入用例
   断言台账内原文不可恢复（演示台账实测 `api_key: "********"`，`secret leaked: False`）。
5. **append-only 台账**：`unified_traces` 表**只 INSERT**；任务级主 Trace 在 `finish()`
   一次性落账（不用 UPDATE 收尾）；`clear()` 为测试专用显式动作（全模块唯一 DELETE）。
6. **双表同库，不新增第二存储轨**：新表 `unified_traces` 与既有 `tool_traces` **同库**
   （`agent/data/tool_trace.db`），沿用既有 SQLite 批量异步 writer 模式
   （入队 → daemon 线程批量写 → 失败降级 ring buffer → flush/stop 优雅关闭），
   同时保留 `:memory:` 共享缓存模式供测试。
7. **workspace_id 不变量（P7.1-19）**：缺 workspace_id 的持久化记录**默认显式降级**
   （ring buffer + 告警 + 计数，不阻断主路径），`strict=True` 时抛
   `MissingWorkspaceError`；两条路径均有单测。
8. **S1-01 遗留 #3 收口**：`load_runtime_descriptors()` 把内置/MCP 运行时工具真实装载进
   `DescriptorRegistry`（内置取 `agent.tools.list_tools()`）；`capability_reference()`
   把 trace 的 capability_id 解析为带 origin/provenance（+ borrowed 的 trace_policy）
   的引用；端到端演示中 3/3 能力级 Trace 的 `capability_id` 与台账 join 通过
   （`cp.builtin.*` → `provenance=verified`）。
9. **零回归**：全量 `tests/unit` 扫描 **12524 passed / 0 failed**（含本任务新增 120 例），
   本任务直接相关定向套件 910 passed / 0 failed。

---

## 二、预期成果对照（任务 §三）

| # | 预期成果 | 交付 | 验收 |
|---|---|---|---|
| 1 | 统一 Trace schema + TraceContext + TraceFacade（读侧复用既有 writer） | ✅ `agent/observability/trace_v2.py`：`UnifiedTrace`/`TraceContext`/`TraceFacade`/`UnifiedTraceStore` | ✅ |
| 2 | 工具链/orchestrator/会话层 TraceContext 透传接线；端到端 parent 链演示 | ✅ `tool_calling._record_unified_tool_trace`（4 处调用点）+ `orchestrator._begin/_end_unified_task_trace`（process LLM 段 try/finally）+ `SessionManager.workspace_id_for`；`scripts/demo_s2_01_trace.py` | ✅ |
| 3 | trace ↔ descriptor(capability_id) join 验证（消费 S1-01 遗留 #3） | ✅ `load_runtime_descriptors()` 运行时装载 + `capability_reference()` 引用解析（borrowed/opaque 带 origin/provenance + trace_policy）+ join 单测 12 例 + 演示 3/3 join 通过 | ✅ |
| 4 | 读取接口（按 capability/chain/task 聚合） | ✅ `list_by_capability()` / `chain()` / `task_summary()` / `query()` / `snapshot_stats()` / `write_stats()` | ✅ |
| 5 | TASK-S2-01_验收报告.md | ✅ 本文件 | ✅ |

---

## 三、验收清单逐条核验（任务 §四）

### ✅ 1. UnifiedTrace 字段覆盖 §3.4（含 schema_version、parent_trace_id、workspace_id）；缺失字段持久化失败或显式降级有测试

- `UnifiedTrace.to_dict()` 26 字段与 §3.4 逐项对齐（`TestUnifiedTraceSchema::test_to_dict_covers_all_spec_fields`
  以集合相等断言，新增/漏字段即失败）；`schema_version` 默认 `SCHEMA_VERSION=1`；
  `parent_trace_id` 与 `tenancy.workspace_id` 为独立字段。
- 域级校验 `UnifiedTrace.validate()` 返回问题清单：缺 trace_id/task_id、非法 actor、
  非法 status、**缺 workspace_id**（`test_validate_flags_missing_workspace` 等 5 例）。
- **缺 workspace_id 两条处置路径均有测试**：
  - 显式降级：`test_missing_workspace_degrades_explicitly`（返回 False、计入
    `workspace_degraded` 计数、记录仍可见于 ring buffer 不丢失）；
  - 写入失败：`test_missing_workspace_strict_raises` / `test_store_strict_default_raises` /
    `test_facade_record_strict_propagates`（抛 `MissingWorkspaceError`）。
- 往返序列化：`to_dict()` → `from_dict()` → `to_dict()` 逐字段相等（`test_roundtrip_dict`）。

### ✅ 2. 端到端任务可产出一串可串联 Trace（task 主 + tool 子），全程含 workspace_id

- 单测：`TestEndToEndFixFailedTestTask::test_task_and_tool_traces_chain_with_workspace`
  构造「修复失败测试」任务 → 1 条任务级主 Trace + 3 条工具级子 Trace，
  `chain()` 还原为 4 条且 3 条子 Trace 的 `parent_trace_id` 全部指向主 Trace，
  **全部行的 `tenancy.workspace_id` 相同且非空**。
- 工具链接线实测：`TestToolCallingPropagation::test_execute_safe_records_child_trace_when_context_active`
  经 `ToolCallingService._execute_safe` 真实调用路径产出子 Trace（parent/tenant/workspace/args_hash 齐备）。
- 演示脚本实跑（`python scripts/demo_s2_01_trace.py`）：

```
台账：data/trace_v2_demo.db
workspace_id（workspace-hash，P7.1-19）：ws_188f4f1a4f4c6f18
任务主 Trace：87b18cfef014404c（success）

parent 链（主 → 子）：
  - 87b18cfef014404c  parent=(root)              (task)                   actor=auto      status=success ws=ws_188f4f1a4f4c6f18  13.383ms
  - 21dee56e7e9c4505  parent=87b18cfef014404c    cp.builtin.read_file     actor=human     status=success ws=ws_188f4f1a4f4c6f18
  - 650cd111b5b1422a  parent=87b18cfef014404c    cp.builtin.shell_execute actor=auto      status=error   ws=ws_188f4f1a4f4c6f18
  - 9a84fd90fac64ea2  parent=87b18cfef014404c    cp.builtin.write_file    actor=sub_agent status=success ws=ws_188f4f1a4f4c6f18

parent 链全部指向主 Trace：True
全程含 workspace_id：True
任务聚合：{"task_id":"fix-failed-test-demo","step_count":3,"success_count":2,"failed_count":1,
          "success_rate":0.6667,"total_cost_usd":0.0012,"total_tokens":240,
          "capabilities":["cp.builtin.read_file","cp.builtin.shell_execute","cp.builtin.write_file"],
          "workspace_id":"ws_188f4f1a4f4c6f18"}
```

- ContextVar 无泄漏：orchestrator 工具执行段以 `try/finally` 收尾
  （`test_end_writes_task_trace_and_clears_context` 断言 `TraceContext.current() is None`；
  `test_end_with_mismatched_trace_id_is_noop` 断言不误清他人上下文）。

### ✅ 3. 脱敏在哈希之前（密钥注入用例断言原文不可恢复）

- 顺序硬编码于单一入口 `redact_then_hash(data) -> (redacted, hash(redacted))`；
  `TestRedactionBeforeHash::test_redact_then_hash_hashes_redacted_not_original`
  断言 `hash(redacted) != hash(原文)`（若先哈希后脱敏，二者相等 → 用例即失败）。
- 密钥注入用例：`test_facade_persists_only_redacted_args_and_output`（args 含
  `api_key=sk-test-...` 占位密钥、output 含 `Bearer sk-test-...`）→ 断言持久化记录 JSON 中
  **原文子串不存在**，仅存 redacted + `args_hash`/`output_hash`。
- 演示台账实测（`data/trace_v2_demo.db`，注入 `sk-test-DEMO-SHOULD-BE-REDACTED`）：

```
rows: 4
secret leaked: False
payload: {... "args_redacted": {"path": "tests/test_demo.py", "api_key": "********"},
          "args_hash": "a69fa357339b7233", ...}
```

- 既有红action 管道复用：`redact()` 主路径调 `sensitive_data_filter.filter_sensitive_data`；
  过滤器不可用（`test_fallback_redact_when_filter_unavailable`，注入 RuntimeError）时
  走内置兜底掩码，原文同样不出现在结果中。审计动作 `trace.redact` 的事件语义由
  S2-02 承接（本任务只保证顺序与不落原文）。

### ✅ 4. trace.append-only：无更新/删除写路径（或仅在显式治理动作下）

- 源码级断言：`test_no_update_write_path_in_source`（全模块无 `UPDATE unified_traces`）、
  `test_writer_statement_is_insert_only`（`_write_to_db` 内仅 `INSERT`，无 UPDATE/DELETE）、
  `test_only_delete_is_in_clear`（全模块唯一 `DELETE FROM unified_traces` 位于
  `clear()`，docstring 标注**测试专用**显式动作）。
- 语义级：任务级主 Trace 在 `finish()` **一次性落账**（不先写后改），
  `test_finish_writes_task_level_trace_and_clears_context`；
  `test_records_are_immutable_after_write` 断言重复读取内容不变。

### ✅ 5. trace.capability_id 可 join descriptors 台账（真实 MCP/内置工具登记后 join 通过）

- 运行时接线：`load_runtime_descriptors(registry, builtin_entries=..., mcp_tools=...)`
  调 `descriptors.bridge.register_bridge_view` 真实装载（内置默认取
  `agent.tools.list_tools()`；MCP 取 list_tools 结果）；只写 Descriptor 层，advisory。
- **capability 引用（§3.4「borrowed/opaque 须带 origin/provenance 引用」）**：
  `capability_reference(capability_id, registry)` / `capability_reference_for_trace(trace)`
  把 trace 的 capability_id 解析为引用（source_type/source_id/provenance/stage/
  **trace_policy**/risk/data_class/external_endpoint）；MCP 外来能力实测
  `source_type=mcp, provenance=declared, stage=borrowed, trace_policy=trace:filesystem:call-side(S2-ledger-pending)`
  ——foreign/opaque 能力的来源与证据在引用中显式暴露，未登记能力返回
  `joined=False, provenance=None`（**不伪造**）。
- 单测 12 例：内置登记、MCP 登记、**join 通过**（`registry.get(trace.capability_id)`
  非 None 且 `source_type=builtin`）、未登记能力不 join（诚实口径）、空清单 advisory、
  borrowed 引用 7 例（含 trace_policy 断言、registry 异常 advisory、真实台账懒加载）。
- 演示实测：

```
capability ↔ descriptor join（S1-01 遗留 #3）：
  - cp.builtin.read_file         joined=True source=builtin provenance=verified
  - cp.builtin.shell_execute     joined=True source=builtin provenance=verified
  - cp.builtin.write_file        joined=True source=builtin provenance=verified
```

### ✅ 6. 既有 tool_trace/subscriber/orchestrator 套件零回归

| 套件 | 文件数 | 结果 |
|---|---|---|
| **全量 `tests/unit`（含本任务新增 120 例）** | **全部** | ✅ **12524 passed / 0 failed**（302 skipped / 13 xfailed / 4 xpassed；24:22） |
| tool_trace + tool_router_hybrid + message_handler + feedback_engineering + circuit_breaker | 5 | ✅ **141 passed / 0 failed** |
| orchestrator（refactor/reject/concurrency/wf-learning）+ tool_calling（refactor/comprehensive） | 6 | ✅ **279 passed / 0 failed** |
| session_manager + tracing + observability（session×4 / tracing×4 / observability×2 等） | 9 | ✅ **311 passed / 0 failed** |
| descriptors（models/validator/bridge/registry/backfill） | 5 | ✅ **179 passed / 0 failed**（与 S1-02 基线一致） |

定向套件合计 **910 passed / 0 failed**（本任务直接相关面），全量 `tests/unit` 扫描
**12524 passed / 0 failed**（零回归、零新增失败）。

### ✅ 7. 新增单测全绿、覆盖率 ≥80%

- `tests/unit/test_trace_v2.py`（**101 例**）+ `tests/unit/test_trace_v2_integration.py`
  （**21 例**）= **122 例**（Windows 本地全绿；Linux CI 上 `test_windows_case_insensitive_paths`
  按 `skipif(os.name != 'nt')` 跳过 → 121 passed / 1 skipped）。
- `agent/observability/trace_v2.py` 覆盖率（branch=True，仅跑本任务两套件）：

```
Name                              Stmts   Miss Branch BrPart  Cover
agent\observability\trace_v2.py     558     33    144     14    93%
```

**93% ≥ 80%**（未覆盖行集中在内置兜底掩码的正则分支与极端降级路径）。

### ✅ 8. 读取接口输出可被 S3-01 模式挖掘（≥20 条同类）与 S5 评测直接消费（有样例）

- S3 模式挖掘：`list_by_capability(capability_id, limit)` 支持 ≥20 条同类轨迹计量
  （`test_list_by_capability_limit_supports_s3_threshold`：25 条同能力轨迹可取 20/25）；
  `test_chain_supports_s3_pattern_mining_threshold` 断言每行含 `args_hash` /
  `response.status` / `timing.duration_ms`（S5 评测与去噪输入齐备）。
- `task_summary(task_id)` 输出步数/成功率/成本/token/能力清单/workspace_id，供
  ACR 成本（S2-03）与 S6 面板直接消费。
- `write_stats()` 产出 `data/trace_stats.json`（数据源声明），实跑内容：

```json
{
  "total": 4, "success_count": 3, "success_rate": 0.75,
  "by_capability": {"cp.builtin.read_file": 1, "cp.builtin.shell_execute": 1, "cp.builtin.write_file": 1},
  "by_actor": {"auto": 2, "human": 1, "sub_agent": 1},
  "workspace_degraded": 0, "schema_version": 1, "append_only": true
}
```

---

## 四、关键设计裁定（云枢对设计文档未闭合点的显式化）

| # | 议题 | 裁定 | 理由 |
|---|---|---|---|
| D1 | 「复用既有 writer」vs「新增存储轨」 | 新表 `unified_traces` **与 `tool_traces` 同库**（`agent/data/tool_trace.db`），沿用既有 SQLite 批量异步 writer **模式**（入队/批量/降级 ring buffer/flush/stop）；`UnifiedTraceStore` 自包含 | 任务书括注明确允许「如需独立表则双表同库」；自包含实现**零改动 tool_trace.py**，把回归风险压到最低（既有 5 条 flush 竞态不变量 T1–T5 不受影响）。存储介质仍是同一个 SQLite 台账，未新增第二存储轨 |
| D2 | append-only 与「任务级 Trace 收尾」的矛盾 | 任务级主 Trace **只在 `finish()` 落账一次**（不在 `start()` 写中间态），故无需 UPDATE | 满足 append-only；任务状态/计时在收尾时已确定，无信息损失 |
| D3 | 缺 workspace_id 的处置 | 默认**显式降级**（ring buffer + warning + `workspace_degraded` 计数），`strict=True` 抛 `MissingWorkspaceError` | 对齐任务书「写入失败或显式降级」，同时守【不易】不阻断主路径；两路径均有单测，非静默 |
| D4 | tenant 与 workspace 关系 | `tenant_id = workspace_id = workspace-hash`（单仓库本地部署） | 对齐 P7.2-08「workspace(repository) = 逻辑租户（tenant_id = workspace-hash）」；S5-01 多租户隔离可在此之上细分 subject |
| D5 | 工具级 Trace 的写入条件 | **仅当存在任务级 TraceContext 时**记录统一子 Trace；无上下文则跳过 | 避免无上下文的散调用污染共享台账；也保证既有 `test_tool_calling_execute_safe_*` 用例路径不新建统一 Trace（零回归） |
| D6 | `capability_id` 取值口径 | 能力级行取 **descriptor capability_id**（`cp.<source>.<name>`）；工具链接线暂以工具名（call-site id）落账，join 由 Registry 侧完成 | 工具名 → descriptor id 的运行时改写需要 registry 常驻查找（成本/降级风险），留待 S3-01 结合能力地图统一；本任务已交付 `load_runtime_descriptors()` 与 join 证明 |
| D7 | `TraceContext.child()` 的落点 | 本任务 `record()` 直接用 `parent_trace_id = 当前任务 trace_id` 串联；`child()` 作为**显式派生**入口交付（供 S4-04 subagent 委派：actor=sub_agent、parent=编排任务） | 与任务书 §2.7「parent_trace_id 由 child() 自动串联」一致；工具级无需多层派生，避免 trace 深度膨胀 |

---

## 五、联动与文件清单

### 5.1 代码改动（新增为主；存量改动均最小、additive）

| 文件 | 内容 |
|---|---|
| `agent/observability/trace_v2.py`（新增，1241 行） | `UnifiedTrace`(+Tenancy/Request/Response/Timing/Cost/SideEffects)、`TraceContext`、`TraceFacade`、`UnifiedTraceStore`、`redact/hash_content/redact_then_hash`、`derive_workspace_id`、`load_runtime_descriptors`、`capability_reference`/`capability_reference_for_trace`、`MissingWorkspaceError` |
| `agent/observability/__init__.py`（存量，additive） | 导出 trace_v2 公共 API（27 个符号）；既有 `subscriber` 导入路径零改动 |
| `agent/session_manager.py`（存量，+26 行） | 新增 `SessionManager.workspace_id_for(session_id)`：绑定工作区/默认工作区 → workspace-hash（trace_v2 不可用时本地兜底） |
| `agent/tool_calling.py`（存量，+30 行） | 新增 `_record_unified_tool_trace()`；`_execute_safe` 四条返回路径各追加一次 best-effort 统一子 Trace（既有 `start_trace/finish_trace` 与采样策略零改动） |
| `agent/orchestrator/orchestrator.py`（存量，+52 行） | 新增模块级 `_begin_unified_task_trace()`/`_end_unified_task_trace()`；`process()` 的 LLM 调用段加 `try/finally`（工具执行期透传 TraceContext，收尾落账任务主 Trace） |
| `scripts/demo_s2_01_trace.py`（新增，184 行） | 端到端演示 + `data/trace_stats.json` 摘要输出（`--db/--stats/--json`） |
| `.gitignore`（+3 行） | `data/trace_stats.json`、`data/trace_v2_demo.db`（运行时台账摘要，同 `data/descriptors.json` 性质不入库） |
| `tests/unit/test_trace_v2.py`（新增，925 行） | **101 例**（schema 10 / 脱敏哈希 12 / TraceContext 10 / workspace_id 7 / store 读写 13 / workspace 不变量 6 / append-only 6 / facade 15 / capability join 5 / capability 引用〔borrowed provenance〕7 / 兜底降级 10） |
| `tests/unit/test_trace_v2_integration.py`（新增，339 行） | **21 例**（会话层 4 / 工具链 6 / 编排层 7 / 端到端 4） |
| 报告（runtime，gitignore） | `data/trace_stats.json`、`data/trace_v2_demo.db` |

### 5.2 既有接口零破坏核验

- `tool_trace.py` / `subscriber.py` / `tracer.py` **零改动**（既有 ToolTraceRecord 11 字段、
  采样策略、flush 计数、stop 优雅关闭全部保持）；
- `tool_calling._execute_safe` 的返回值、重试语义、既有 tool_trace 记录路径**逐行保持**
  （新增调用点全部 best-effort 且异常吞掉）；
- orchestrator `process()` 返回值与早退路径不变（新增的 `finally` 只做落账 + 清上下文，
  不改变控制流）；`_begin/_end` 任何异常均被吞掉（`test_begin_failure_returns_empty` /
  `test_end_failure_is_swallowed`）；
- `SessionManager` 仅新增方法，既有会话/工作区/分组行为零变化；
- `agent/observability/__init__.py` 由 2 行注释扩展为导出面，未移除任何既有符号。

---

## 六、执行证据（2026-09-10 实跑）

```
新增套件：122 例（Windows 全绿；Linux CI 121 passed / 1 skipped〔Windows 专属用例〕）
覆盖率：agent/observability/trace_v2.py 93%（Stmts 558 / Miss 33 / Branch 144 / BrPart 14）
全量扫描：python -m pytest tests/unit -q -p no:randomly
          → 12524 passed / 0 failed / 302 skipped / 13 xfailed / 4 xpassed（24:22，修复前口径）
CI 全量：云枢系统测试流程 21/21 job success（含单元测试 6 shard + 集成 4 shard + E2E + 性能 + 安全）
既有定向回归：141 + 279 + 311 + 179 = 910 passed / 0 failed
  - tool_trace 65 + tool_router_hybrid_integration 14 + message_handler 17
    + feedback_engineering 16 + circuit_breaker_three_level 29 = 141
  - orchestrator（refactor 75 / reject 40 / concurrency 3 / wf_learning 15）
    + tool_calling（refactor 30 / comprehensive 116）= 279
  - session_manager（concurrency / comprehensive / workspace_binding / group_store）
    + tracing（context_propagation / coverage / missing_functions）
    + observability（config / track_event）= 311
  - descriptors（models / validator / bridge / registry / backfill）= 179
  - orchestrator 边界（tests/boundary/test_orchestrator_boundary.py）= 29 passed
演示实跑：scripts/demo_s2_01_trace.py → 链条 4 行、parent 链 3/3、join 3/3、
         全程含 workspace_id=True、任务成功率 0.6667、trace_stats.json 已写出
         注入密钥 sk-test-DEMO-SHOULD-BE-REDACTED → 台账内 secret leaked: False
```

---

## 七、遗留清单（不阻塞本任务验收；随主线消费）

| # | 遗留 | 归属 |
|---|---|---|
| 1 | 工具名 → descriptor capability_id 的**运行时改写**（当前工具级落账用工具名，join 需 registry 侧查找） | S3-01 结合能力地图统一（本任务已交付 `load_runtime_descriptors()` + join 证明） |
| 2 | orchestrator **规划接线（wire）路径**未接任务级统一 Trace（当前覆盖 LLM 调用/工具执行主路径；规划成功时跳过 LLM 段，故无 task 主 Trace） | S3-01（规划引擎产出与消化流水线合流时一并接线） |
| 3 | `trace.redact` 审计事件入链（本任务只保证 redact→hash→持久化顺序与不落原文） | S2-02 链式审计（AuditLog 承接） |
| 4 | 统一 Trace → events.v1 信封（`task.closed` 等）与 ACR 成本归集 | S2-03（成本字段 `cost.*` 已就位） |
| 5 | subagent 委派用 `TraceContext.child()`（actor=sub_agent、parent=编排任务） | S4-04（child() 已交付并单测） |
| 6 | 台账跨进程并发写（单机锁内串行，未做跨进程锁） | S2 生产化（同 S1-02 遗留 #4） |
| 7 | `unified_traces` 表历史数据的保留/归档策略（当前无 TTL） | S2/S5（与审计链保留策略一并定） |

---

*补记一：本任务对既有三套轨迹设施的定位是**「统一而不另起炉灶」**——`tool_trace`（工具级
SQLite 轨迹）、`subscriber`（任务/会话级内存 span）、`tracer`（trace_id 生成）全部保留，
`trace_v2` 在其上提供 §3.4 规格的统一台账与 §2.7 的 TraceContext 透传，三者在同一调用链上
通过 trace_id / parent_trace_id 天然对齐，无重复写入语义。*

*补记二：`TraceFacade.record()` 的 parent 串联规则为「存在当前任务 TraceContext 时，
trace_id 为新生成子 id、parent_trace_id=当前任务 trace_id；无上下文时不臆造父链」——
后者使离线/单测场景的独立记录不产生悬挂父引用（`test_record_without_context_has_no_parent`）。*

*补记三（交付收尾，2026-09-10）：首轮 push（`b9f17236`）CI 的「硬编码密码扫描（全分支）」
job 报 1 处 gitleaks 误报——演示/单测的**假密钥占位串** `sk-demo-…` 命中规则 8
`\b(sk|sk-ant)-[A-Za-z0-9_\-]{20,}\b`。已改用仓库既有白名单约定的 `sk-test-` 前缀
（`.github/gitleaks-config.toml` 白名单 `^sk-(test|secret|real|instance)…`），语义与验证强度
不变；修复提交 `3dc065cf` 该 job **success**。详见
[S2-01_交付结案报告_20260910.md](S2-01_交付结案报告_20260910.md) §4.1。*
