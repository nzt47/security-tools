# TASK-S5-01 记忆分层映射表（v7.2 四层 ↔ 云枢现有载体 ↔ 缺口）

> 产出物：TASK-S5-01 步骤 1「现状盘点与分层映射」的结论
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §3.11 / §4.3 / P7.2-08 / P7.2-10 / §8 / §10
> 落点：`agent/memory/{taxonomy,tenancy,layered_store,forgetting,identity}.py`
> 日期：2026-09-12

---

## 一、分层映射（v7.2 四层 ↔ 云枢载体 ↔ 缺口）

| v7.2 层（§4.3） | 云枢既有载体（盘点结论） | 既有缺口 | S5-01 补齐方式 |
|---|---|---|---|
| **working 工作** | ① `agent/memory/short_term_memory.py::ShortTermMemory`（进程内 `dict`，`task_id` + `expires_at`，默认 TTL 300s，无落盘）② `LongTermMemory`（可作落盘兜底，但无 TTL 语义） | 无租户/主体维度（仅 `key` + `task_id`）；不落盘；无 §3.11 字段；短 TTL 不可配 | 新增 `working` 分片（**按租户** `<root>/tenants/<slug>/working.db`），TTL 默认 8h 可配（`MEMORY_TTL_WORKING`）；既有 `ShortTermMemory` **零改动**（内存态工作记忆保持原样，分层 `working` = 需落盘的短期记忆） |
| **fact 事实** | ① `LongTermMemory`（SQLite `long_term_memory` 表：`content/importance/tags/metadata`）② 同库 `user_profile` 表（文档自称"四层记忆架构之用户档案卡层"）③ `agent/knowledge/`（Markdown 卡片 + BM25/RRF 检索，项目知识面） | 三者**均无** `tenant_id/workspace_id/subject_id/scope/ttl_expires_at/forget_candidate/content_hash`（rep 级 grep 命中 0）；无租户隔离；`Card.scope` 是"适用边界"而非租户 | 新增 `fact` 分片（**按租户**：`<root>/tenants/<slug>/fact.db`），TTL 默认 180d；§3.11 字段完整落地；`knowledge` 卡片**不改动**（见 §五 边界声明） |
| **preference 偏好** | ① `MemoryRouter._classify_context` 的"偏好/设置/配置/喜欢/习惯"关键词 → 路由到 `long_term` ② `LongTermMemory.save_profile(preferences=...)`（JSON 列）③ `HolographicAdapter` 的 `metadata` | 偏好**未被识别为独立层**；无"跟随 subject 跨租户携带"语义；无主体级隔离（同机任意用户可读） | 新增 `preference` 分片（**按 subject**：`<root>/subjects/<slug>/preference.db`），恒 `global` scope；隔离载体 = `subject_id`（跨租户携带，P7.2-08） |
| **strategy 策略** | ① `agent/descriptors/`（`ToolDescriptor.evolution.stage` 七态、`governance.policy_ref`）② `SKILL.md` frontmatter `scope: local|project|org` ③ `IncidentCard.in_strategy_memory`（§3.11 已定义字段） | **没有策略记忆载体**：`IncidentCard` 里的 `in_strategy_memory` 指向一个不存在的存储；无 org 级只读下发实现；无"个人不可写"闸门 | 新增 `strategy` 分片两态：org 级 `<root>/org/strategy.db`（只读下发，任意租户可读）+ 租户内 `<root>/tenants/<slug>/strategy.db`；**个人通道写入一律拒绝**（`strategy_layer_readonly`） |

> **命名澄清（重要）**：`agent/skills_mgmt/memory_abstractor.py::MemoryEntry`（`source/source_id/task_text/success/tool_calls/params/tags/session_id/signal_strength`）是**技能萃取用的信号条目**，与 §3.11 的 `MemoryEntry` 同名但语义完全不同。S5-01 按 §3.11 逐字落地新模型于 `agent/memory/taxonomy.py`，并**不改动** `memory_abstractor`（其既有行为与用例零回归）。

---

## 二、§3.11 字段落点

| §3.11 字段 | 实现位置 | 说明 |
|---|---|---|
| `id` | `MemoryEntry.id` | `mem_<16hex>`（`new_memory_id()`），落既有引擎的 `key` 主键 |
| `tenant_id` | `MemoryEntry.tenant_id` | = workspace-hash（P7.2-08），来源 S2-01 `derive_workspace_id()` |
| `subject_id` | `MemoryEntry.subject_id` | 登录用户标识 |
| `type` | `MemoryEntry.type` → `MemoryType` | `fact` / `preference` / `strategy` / `working` |
| `content_redacted` | `MemoryEntry.content_redacted` | 脱敏后文本（复用 `SensitiveDataFilter.detect_and_sanitize`） |
| `content_hash` | `MemoryEntry.content_hash` | **由脱敏后文本派生**（§3.4：脱敏先于哈希） |
| `scope` | `MemoryEntry.scope` | `project:<workspace-hash>` 或 `global` |
| `source_task_id` | `MemoryEntry.source_task_id` | 来源任务 |
| `confidence` | `MemoryEntry.confidence` | 0..1（落既有 `importance` 时映射到 1..4，刻意不产生 5） |
| `created_at` | `MemoryEntry.created_at` | 可用注入时钟 |
| `last_hit_at` | `MemoryEntry.last_hit_at` | 召回命中回写（`record_hits=True`） |
| `ttl_expires_at` | `MemoryEntry.ttl_expires_at` | `None` = 永不到期（`ttl_seconds=0`） |
| `forget_candidate` | `MemoryEntry.forget_candidate` | + `forget_reason`（触发取证） |
| `schema_version` | `MemoryEntry.schema_version` | 当前 `1` |

**云枢工程扩展字段**（全部带默认值，按 §3.11 最小集构造亦合法）：
`source_capability_id`（触发②来源指针）/ `source_trace_id` / `org_level`（org 级只读下发）/ `degraded` + `degradation_reason`（显式降级标注，硬约束 1）/ `forget_reason` / `hit_count` / `extra`。

---

## 三、租户隔离矩阵（P7.2-08 三条铁律 —— 可执行口径）

`agent/memory/tenancy.py::ISOLATION_MATRIX` 的机器可读形态（`isolation_matrix_rows()` 原样输出）：

| 层 | 隔离载体 | 个人通道可写 | 跨租户可见 | 只读 | 默认作用域 | 备注 |
|---|---|---|---|---|---|---|
| `working` | `tenant_id` | ✅ | ❌ | ❌ | `project` | 工作记忆：短 TTL，随租户隔离 |
| `fact` | `tenant_id` | ✅ | ❌ | ❌ | `project` | 事实记忆：**跨租户不可见**（反例口径） |
| `preference` | `subject_id` | ✅ | ✅（随 subject 携带） | ❌ | `global` | 偏好记忆：**换租户仍可见、换主体不可见** |
| `strategy` | `tenant_id(__org__)` | ❌（**写入被拒**） | ✅（org 级只读下发） | ✅ | `org` | 企业策略记忆：**个人写入绝不进策略层** |

**写入守卫（硬约束 1）**：缺 `tenant_id` / `workspace_id` / `subject_id` 时——

- 默认 **拒绝**（`MemoryWriteRejected`，reason = `missing_tenant_id` / `missing_workspace_id` / `missing_subject_id`），口径对齐 S2-01 `MissingWorkspaceError`；
- 显式开启 `MEMORY_TENANCY_ALLOW_DEGRADE=1` 后改为 **显式降级**：落 `<root>/degraded/unscoped.db` 隔离区 + `degraded=True` + `degradation_reason` + 审计 `memory.write.degraded`，**默认召回不包含**（避免"降级"变成静默全局泄漏）；
- 未显式给 `scope` 时**不静默降为 global**：project 类层缺 workspace ⇒ 拒绝（有单测断言）。

**越界守卫**：显式 `scope=project:<hash>` 与上下文 workspace 不一致 ⇒ 拒绝（`scope_workspace_mismatch`）。

---

## 四、召回优先级口径（§4.3 + P7.2-10）

排序键 `recall_priority_key(entry) = (注入层序, 作用域序, -created_at, id)`（升序，越小越优先）：

| 维度 | 取值 | 依据 |
|---|---|---|
| 注入层序 | 策略 0 < 事实 1 < 偏好 2 < 工作 3 | P7.2-10（规避规则 > 项目约束 > 风格） |
| 作用域序 | `project` 0 < `global` 1 | §4.3 |
| 时间 | `-created_at`（同级新者胜） | §4.3 |
| 兜底 | `id` 升序（保证全序、确定性） | 工程纪律 |

由「层序优先」可得 §4.3 原文口径：`project 事实 (1,0,…)` 优于 `global 偏好 (2,1,…)` —— **project 事实 > global 偏好**成立；同时 P7.2-10 的 `策略 > 事实 > 偏好` 与作用域无关地成立。

---

## 五、遗忘三触发与数据源（§4.3 / §8）

| 触发 | 条件 | 数据源（复用，不新建） | 实现 |
|---|---|---|---|
| ① 成功率劣化 | 窗口（默认 30 天，`MEMORY_FORGET_WINDOW_DAYS`）成功率 **< 基线 × 0.7**（`MEMORY_FORGET_SUCCESS_RATIO`），且样本数 ≥ **20**（`MEMORY_FORGET_MIN_SAMPLES`，沿用"每能力 ≥20 条同类轨迹"口径纪律） | S2-01 `agent/observability/trace_v2.py::UnifiedTraceStore.query(capability_id, since)`；基线取 S1-01 `ToolDescriptor.quality.success_rate`（`sample_count<=0` 视为**无基线**，不触发） | `TraceQualitySource` + `ForgettingEngine.evaluate_success_rate()` |
| ② 来源失效 | descriptor `evolution.stage == deprecated` 或来源**摘除**（`registry.get()` → `None`） | S1-01 `agent/descriptors/registry.py`（`get` / `resolve_alias`）；alias 归并到 canonical 后**再判定**，改名不算失效；注册表不可用时**不判定失效**（避免误删） | `SourceValidityChecker` + `evaluate_source()` |
| ③ 删除权 | 用户显式删除 / 被遗忘权 | 显式 API | `ForgettingEngine.erase_subject()` |
| 补充 TTL | 条目 `ttl_expires_at` 到期 → 自动降级为遗忘候选 | 条目自身字段 | `ForgettingEngine.apply_ttl()` |

**执行纪律**：任何物理删除前**先落快照**（①/② 类 `full` 模式，留 30 天可回滚）；③ 类用 `hash_only` **墓碑快照**（内容置空、`subject_id` 换伪引用，只留 `content_hash`），并对既有 `full` 快照中的该主体条目**就地墓碑化** —— 否则 30 天快照会把"记忆物理删除"抵消。删除默认 **dry-run**（`run(execute=False)` / `forget(dry_run=True)`），须显式开启才落盘。

---

## 六、落点与可调参数

**分片布局**（物理分库即隔离；不新建第二套记忆库，每个分片就是一个既有 `LongTermMemory` 实例）：

```
<MEMORY_LAYERS_ROOT 默认 ~/.cloudpivot/vault/memory/layers>/
  org/strategy.db                         # 企业策略记忆（org 级只读下发）
  tenants/<slug(tenant)>/working.db       # 工作记忆
  tenants/<slug(tenant)>/fact.db          # 事实记忆（按租户隔离）
  tenants/<slug(tenant)>/strategy.db      # 租户内策略（SYSTEM 通道回填）
  subjects/<slug(subject)>/preference.db  # 偏好记忆（随 subject 跨租户携带）
  degraded/unscoped.db                    # 缺租户字段的显式降级隔离区（默认召回不含）
  identity/subject_salts.json             # 审计伪名盐表（匿名化 = 销毁盐）
<MEMORY_SNAPSHOT_ROOT 默认 ~/.cloudpivot/vault/snapshots>/    # §8：项目树之外
  <YYYYMMDD>/<snapshot_id>.json + index.json
```

> 默认根目录刻意落在**项目树之外**（§8 P7.2-18：备份/归档不得位于项目树内），也避免记忆数据被误提交（并行会话 index 卫生）。

| 参数 | 环境变量 | 默认 |
|---|---|---|
| 分片根 | `MEMORY_LAYERS_ROOT` | `~/.cloudpivot/vault/memory/layers` |
| 快照根 | `MEMORY_SNAPSHOT_ROOT` | `~/.cloudpivot/vault/snapshots` |
| 盐表根 | `MEMORY_IDENTITY_ROOT` | `<分片根>/identity` |
| 记忆审计开关 | `MEMORY_LAYERS_AUDIT` | 开（`0` 关闭） |
| 缺租户字段处置 | `MEMORY_TENANCY_ALLOW_DEGRADE` | 关（即**拒绝**） |
| TTL（秒） | `MEMORY_TTL_WORKING` / `_FACT` / `_PREFERENCE` / `_STRATEGY` | 8h / 180d / 365d / 365d |
| 快照保留 | `MEMORY_SNAPSHOT_RETENTION_DAYS` | 30 |
| 触发①窗口/阈值/样本门槛 | `MEMORY_FORGET_WINDOW_DAYS` / `_SUCCESS_RATIO` / `_MIN_SAMPLES` | 30 / 0.7 / 20 |

非法值一律回退默认并告警（批次总表 §三 硬约束 3）。

---

## 七、边界声明（守不易 / 范围纪律）

1. **不改既有检索公开行为**：`LongTermMemory` / `HolographicAdapter` / `Mem0Adapter` / `MemoryRouter` / `ContextAssembler` / `ShortTermMemory` **零改动**。分层叠加通过 `_LayeredLongTermMemory`（`LongTermMemory` 子类）实现，且只在返回的 `MemoryResult.metadata` 中**追加** `layer` 键 —— 既有 `key/importance/tags/sensitive/verified` 一个不动。
2. **`agent/knowledge/` 不改动**：卡片 `Card.scope` 是"适用边界"而非租户（`schema.py:44`），该包**零 tenancy 字段**；知识面作为项目知识载体保持现状。跨租户可见性经由卡片 `metadata` / 每租户 `wiki_root` 实现属另一条线（S6/后续），本任务不侵入（同时避免与并行波次在 `agent/knowledge/` 上产生语义冲突）。
3. **`memory_abstractor` 未接线**：其 `_load_long_term_memories()` 读默认路径 `LongTermMemory()`，不受分层记忆影响；本任务只保证其**邻接回归零回归**（`tests/unit/test_skills_mgmt.py` 全绿）。
4. **P7.2-10 组装注入未接线**：`ContextAssembler` 的 system 区注入（策略 > 事实 > 偏好）留待 S6/后续；本任务提供 `LayeredMemoryStore.recall()` 作为注入源，并在 `taxonomy.recall_priority_key()` 中把该优先级**固化为可测口径**。
5. **"做梦"不入本任务**：§4.3 的"低频 + 只读快照 + 产出 PR"聚合归 S5-02/后续；本任务只落遗忘与隔离（任务书 §一.3 明确）。
6. **真实能力内化口径**：触发①的样本门槛 20 沿用项目口径纪律 —— 未达门槛时**不判定劣化**，不声称"已实现真实能力内化"。
