# TASK-S2-01 Trace 统一规格 + TraceContext 透传

> 所属阶段：S2 数据与可观测层｜依赖：S1-01（descriptors）、S1-02（台账）｜预估：5–8 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §2.7（TraceContext）/§3.4（Trace 规格）/P7.1-19（workspace_id）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

将 v7.2 的 **Trace 统一规格（§3.4）与 TraceContext 全链透传（§2.7）** 落地到云枢，形成消化流水线（S3）、审计（S2-02）、评测（S5）共用的**统一轨迹台账**。核心要求：

1. **统一而不另起炉灶**：云枢已有三套轨迹设施——`agent/observability/tool_trace.py`（工具级 SQLite 轨迹，ToolTraceRecord：trace_id/tool_name/input_hash/output_hash/latency/success/session_id/user_role/permission_decision，SHA256 前 16 位脱敏哈希 + ContextVar trace_id + 异步批量 writer）、`agent/observability/subscriber.py`（TraceSpan/TraceRecord/TraceStore，任务/会话级 span）、`agent/observability/tracer.py`（generate_trace_id）。本任务在 **tool_trace 基础之上扩展统一 schema**，提供向上兼容的公共 Trace 门面，不删除任何既有写入方。
2. **字段对齐 §3.4**：trace_id / task_id / capability_id（关联 descriptor，消费 S1-01 遗留 #3 Registry 接线）/ actor(human|auto|sub_agent) / tenancy(tenant/workspace) / request(args_redacted+args_hash+idempotency_key) / response(status+output_redacted+output_hash+error_code) / timing / cost / side_effects / parent_trace_id / schema_version；**脱敏发生在哈希之前**（复用既有红action 管道）。
3. **TraceContext 穿透**：trace_id/task_id/tenant_id/workspace_id/subject_id/policy_version 随调用链透传（ContextVar 已有雏形，扩展为结构化对象）；缺 workspace_id 的持久化记录写入失败或显式降级（P7.1-19）。
4. **append-only 台账**：统一轨迹只追加；为 S2-02 链式审计与 S3 模式挖掘（≥20 条同类轨迹）提供读取接口（按 capability/task/parent 查询）。

## 二、执行步骤

### 步骤 1：统一 Trace schema 设计（兼容增量）
- 新增 `agent/observability/trace_v2.py`（或扩展 tool_trace.py，按 S0-01 RFC 分层裁决）：
  - `UnifiedTrace`（Pydantic v2 或 dataclass）：字段组 meta/actor/tenancy/request/response/timing/cost/side_effects/parent/schema_version，对齐 §3.4；
  - `TraceContext`（dataclass + ContextVar）：trace_id/task_id/tenant_id/workspace_id/subject_id/policy_version，提供 `enter/current/child()`；`parent_trace_id` 由 child() 自动串联；
  - `TraceFacade`：`start(task_ctx) → trace_id`、`record(capability_id, args, ...) → UnifiedTrace`、`finish`、`query(capability_id, task_id, parent_id, since)`；**内部仍走既有 SQLite writer（批量异步），不新增第二存储轨**（如需独立表则双表同库）。
- schema 字段含 **capability_id 引用**：trace.capability_id 必须能 join `data/descriptors.json` 台账（S1-01/02 交付）；borrowed/opaque 类能力须带 origin/provenance 引用。

### 步骤 2：TraceContext 透传接线
- 在**工具执行链**（tool_router / tools 执行器，消费既有 `_trace_id_var`/`_permission_decision_var` 的挂点）、**orchestrator 任务编排**（task_id 注入）、**会话层**（session_manager，workspace_id 来源：会话绑定工作区/workspace-hash 默认）三个层面注入 TraceContext。
- task_id 定义：对齐会话任务模型（`plugins/chat.py` 会话持久化 / session_manager），任务开始生成 task_id，子工具 trace 引用之。
- workspace_id 来源（P7.1-19/P7.2-08）：会话工作区根目录哈希或显式 workspace_id；无工作区时取默认并记录。
- 验证：端到端一个"修复失败测试"任务 → 主 Trace（task 级）+ 子 Trace（tool 级）可串联（parent_trace_id 链），全部含 workspace_id。

### 步骤 3：脱敏与哈希顺序保障
- 复用既有敏感数据过滤管道（`sensitive_data_filter`/guardrails），确认 **redact → hash → 持久化** 顺序；审计动作（trace.redact）记录事件供 S2-02。
- 单测：构造含密钥/路径的输入输出，断言存储中仅 redacted+hash，原文不可恢复。

### 步骤 4：读取接口与聚合（供 S3/S5/S6）
- 提供：`list_by_capability(capability_id, limit)`（S3 模式挖掘 ≥20 条同类轨迹）、`chain(trace_id)`（父→子链）、`task_summary(task_id)`（聚合：步数/成本/成功率，供 ACR 成本与 S6 面板）。
- 输出 `data/trace_stats.json` 摘要（或查询函数），为 S3 判定集与 S5 评测提供数据源声明。

### 步骤 5：回归与归档
- 回归：tool_trace/subscriber/orchestrator 相关既有套件全绿；新增单测（schema 校验、ContextVar 穿透、parent 链、脱敏先于哈希、append-only、capability join）≥40 例、覆盖率 ≥80%。
- 撰写 `TASK-S2-01_验收报告.md`。

## 三、预期成果

1. 统一 Trace schema + TraceContext + TraceFacade（读侧复用既有 writer）。
2. 工具链/orchestrator/会话层 TraceContext 透传接线；端到端 parent 链演示。
3. trace ↔ descriptor(capability_id) join 验证（消费 S1-01 遗留 #3：内置/MCP 运行时登记入 Registry 后 trace 可 join）。
4. 读取接口（按 capability/chain/task 聚合）。
5. `TASK-S2-01_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] UnifiedTrace 字段覆盖 §3.4（含 schema_version、parent_trace_id、workspace_id）；缺失字段持久化失败或显式降级有测试
- [ ] 端到端任务可产出一串可串联 Trace（task 主 + tool 子），全程含 workspace_id
- [ ] 脱敏在哈希之前（密钥注入用例断言原文不可恢复）
- [ ] trace.append-only：无更新/删除写路径（或仅在显式治理动作下）
- [ ] trace.capability_id 可 join descriptors 台账（真实 MCP/内置工具登记后 join 通过）
- [ ] 既有 tool_trace/subscriber/orchestrator 套件零回归
- [ ] 新增单测全绿、覆盖率 ≥80%
- [ ] 读取接口输出可被 S3-01 模式挖掘（≥20 条同类）与 S5 评测直接消费（有样例）
