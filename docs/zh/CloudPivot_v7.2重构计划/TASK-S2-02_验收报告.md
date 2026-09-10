# TASK-S2-02 验收报告 — 链式审计（AuditLog prev_hash / self_hash / 验签 / 审计平权）

> 归档日期：2026-09-10
> 所属计划：CloudPivot v7.2 重构计划（S2 数据与可观测层 · 第二任务）
> 任务：[TASK-S2-02_链式审计.md](TASK-S2-02_链式审计.md)
> 依赖输入：TASK-S2-01（统一 Trace / TraceContext，遗留 #3「`trace.redact` 事件入链」由本任务收口）
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §3.5（AuditLog 链式哈希 + 每日 Merkle 根）/
> P7.2-24（审计平权：UI 与 Agent 同表）/ §3.2（迁移策略：旧库只读 + 双写过渡 + 回滚窗口）/
> §5.5（WAL + 单写者）/ §11.2（审计性能预算）/ §11.10（混沌演练「注入篡改」）/ P4-分级实施
> 状态：✅ 验收通过（验收清单 8/8 核验，见 §四）

---

## 一、执行摘要

1. **链式审计核心交付**：新增 `agent/audit/chain.py`（1617 行）——`AuditEntry` /
   `AuditChain` / `verify_chain()` / `daily_merkle_root()` / `merkle_root` /
   `merkle_proof` / `verify_merkle_proof` / `RootsSigner`，覆盖 §3.5 全部字段与
   「追加即链、改中间任意一条即破坏后续全部」的不变量。
2. **self_hash 公式与 §3.5 逐字一致**：`self_hash = sha256(seq|ts|actor|action|subject|
   payload_hash|prev_hash)`；`payload_hash = sha256(规范化记录 JSON)`（含 source /
   trace_id / workspace_id / schema_version / payload），因此**改 payload 或元数据列
   同样被检出**（两级校验：链级 + 载荷级）——对 §3.5 的加强，不改变公式本身。
3. **审计平权（P7.2-24）落地**：统一门面 `agent/audit/facade.py::audit.record(...)`
   （`source="agent"|"ui"`）+ UI 写路由统一包装 `agent/audit/ui_middleware.py`
   （`install_flask_audit(app)` 已在 `app_server.py` 接线）。**UI 与 Agent 落同一张
   `audit_chain` 表、同一条 seq 链**，实测 `agent 16 条 + ui 3 条`（共 19 条）同表可查。
4. **Agent 侧接线 8 处**（均为 best-effort、不阻断主路径）：审批状态机
   (submit/approved/rejected/merged/archived)、评审豁免发布、能力台账
   (descriptor.register/unregister/provenance/stage/patch)、进化谱系写入、评估事件、
   **敏感操作面**（`logging_utils.AuditLogger`：配置访问/修改、权限变更、认证尝试、
   加密密钥访问、敏感操作）、**.env 配置变更**（`env_config_manager._audit_log`）、
   S2-01 Trace 关键事件（`trace.closed` / `trace.tool.error|blocked` / **`trace.redact`**）。
5. **单写者纪律（§5.5）**：进程内每 DB 路径唯一 writer（重复构造抛
   `SingleWriterViolationError`，后台线程用 `AuditChain.reader()` 只读）；seq 在
   `append()` 锁内分配（链序 = seq 序）；持久化由 writer 线程批量 INSERT
   （WAL + synchronous=FULL）；进程重启从持久化 max(seq) 续链。并发写实测
   8 线程 × 25 条 = 200 条，seq 无重复、无缺口、链自洽。
6. **每日 Merkle 根 + 单机降级**：`daily_merkle_root(date)` 写入**受保护**追加文件
   `data/audit/daily_roots.jsonl`（追加后 chmod 只读，追加前临时恢复写权限），记录含
   date / root_hash / leaf_count / 封印 seq 区间 / 首尾 self_hash / **ed25519 签名** /
   降级说明 / **外层根链 prev_entry_hash+entry_hash**（防整日根被删改）。
   本机 `cryptography 48.0.0` 可用 → 走真签名；无密钥/库缺失时降级为 sha256 自签占位
   并**显式记录降级**（P4 分级实施：外部只追加存储入 P5 Backlog）。
7. **验签 CLI**：`scripts/verify_audit_chain.py` —— 全链/区间重算比对、每日根重放、
   签名校验、台账摘要，退出码 `0=OK / 1=检出篡改 / 2=用法或 IO 错误`（可直接作 CI 与
   混沌演练判定）。
8. **篡改注入演练通过（§11.10）**：向链中间 `seq=10` 注入一条篡改 → `verify_chain()`
   与 CLI 均报 `first_bad_seq=10`，且**注入点之后的全部记录失败**（异常共 10 处 =
   seq 10..19），CLI 退出码 1；随后对**未篡改的原始台账**复验仍为 OK。
9. **兼容迁移（§3.2）**：新增 `agent/audit/migration.py` —— 存量 JSONL **只读归档
   （不删除、不追溯）**：纯审计轨按日分片置只读、系统记录本体（审批状态库、进化谱系）
   仅做只读镜像副本（原文件保持可写，避免冻结破坏运行时写入）；`LegacyTrack` 双写过渡
   （旧 JSONL + 新链，`audit_ref` 关联键对齐，实测一致率 100%）；`AUDIT_LEGACY_WRITE=0`
   / `AUDIT_DUAL_WRITE=0` 即切回「仅旧轨」（回滚窗口，零代码改动）。
10. **性能达标**：单条 `append` 实测**均值 0.018~0.019ms / p95 0.019~0.020ms / max
    0.054ms**（两次实跑；目标 <5ms，§11.2 量级）——append 只做 seq 分配 + 两级 sha256 +
    入队，落盘由 writer 线程批量完成。
11. **零回归 + 新增全绿**：新增 5 个套件 **278 例全绿**；与既有 34 个套件同进程一次跑
    **1373 passed / 0 failed / 3 skipped / 1 xfailed**。
12. **新增模块覆盖率**：`chain.py 84.4%` / `facade.py 83.5%` / `logger.py 82.0%` /
    `migration.py 88.8%` / `ui_middleware.py 85.7%`（包级 TOTAL 84.1%，含未改动的
    既有 `observability.py` 0%）——均 ≥ 80%。

---

## 二、步骤 1 盘点：审计写入面清单

> 盘点范围：全仓库 `agent/**`、`plugins/**`、`app_server.py`、`agent/server_routes/**`、
> `scripts/**`。判据：是否为「不可否认留痕轨」（audit-class）还是「运行时计量/状态」
> （metrics/state-class）。**行号为本报告核对时的源码位置**。

### 2.1 写入方 × 载体 × 事件类型 × 字段完备性 × 归属

| # | 写入方（file:line） | 载体 | 事件类型 | actor | action | subject | 归属 | 本任务处置 |
|---|---|---|---|---|---|---|---|---|
| 1 | `agent/audit/logger.py::AuditLogger.log` | `data/audit/audit_YYYYMMDD.jsonl`（追加） | 通用审计 | ✗（仅 metadata） | ✓（action） | ✗ | Agent（**库内无生产写入调用方**） | **双写**：旧 JSONL 逐字保留 + 新链（新增 `audit_ref` 关联键） |
| 2 | `agent/skills_mgmt/approval.py::_persist`（经 `_append_record`/`_transition`） | `data/approval_records.jsonl`（**全文件原子重写**，非追加） | 审批状态机 draft→pending_review→approved/rejected→merged/archived | ✓（actor 字段） | ✓（action） | ✓（object_type:object_id） | Agent + UI（服务层网关） | **链上留痕**：`approval.submit/approved/rejected/merged/archived`（旧文件不改） |
| 3 | `agent/skills_mgmt/review_gate.py::audit_exemption:60` | `data/skills_mgmt_review_audit.jsonl`（追加） | 评审豁免发布 | ✓ | 半（event） | ✓（skill_id） | Agent + UI（发布路由） | **链上留痕**：`skill.review_waiver_publish` |
| 4 | `agent/skills_mgmt/service.py::_emit_assessment_event:771` | `data/skills_assessment_events[-DATE].jsonl`（按日归档追加） | 评估/评审结果事件 | ✗ | 半（kind） | ✓（skill_id） | Agent | **链上留痕**：`skill.assess.{kind}` |
| 5 | `agent/skills_mgmt/lineage.py::EvolutionArchive.append:355` | `data/evolution_archive.jsonl`（+归档分层，全文件重写） | 进化谱系写入 | 半（归档摘要丢弃 actor） | ✓（decision） | ✓（object_id） | Agent | **链上留痕**：`lineage.append` |
| 6 | `agent/descriptors/registry.py::_audit:445` | **内存环**（`_audit_log`，容量上限，重启即失） | 能力台账增删改（register/unregister/provenance/stage/patch） | ✓ | ✓ | ✓（capability_id） | Agent | **链上留痕**：`descriptor.*`（内存环保留） |
| 7 | `app_server.py` 全局写路由（`before/after/teardown_request`） | **无（此前完全缺失）** | 所有 UI 写操作（POST/PUT/PATCH/DELETE） | ✗（无登录用户体系，见 §2.3） | 半（路径） | ✓（路径） | **UI** | **新建**：`ui.<endpoint>.<verb>` 全量入链 + 关键路由语义化 action |
| 8 | `agent/logging_utils.py::AuditLogger`（7 个敏感操作方法：`log_config_access` / `log_config_modification` / `log_secure_config_access` / `log_encryption_key_access` / `log_permission_change` / `log_authentication` / `log_sensitive_operation`，`:869-982`） | `logs/audit.log`（**文本行**，logging.FileHandler，非 JSONL） | 配置访问/修改、权限变更、认证尝试、加密密钥访问、敏感操作 | 半（`user=` 形参，默认 `system`） | ✓（方法名即动词语义） | ✓（config_key/resource/username） | Agent（唯一生产调用方 `scripts/diagnose.py`） | **链上留痕**：`config.access` / `config.modify` / `config.secure_access` / `config.encryption_key_access` / `permission.change` / `auth.attempt` / `sensitive.operation`（文本轨保留） |
| 9 | `agent/env_config_manager.py::EnvConfigManager._audit_log:199` | `logs/config_audit.jsonl`（追加） | `.env` 配置变更（set/delete） | 半（`getpass.getuser()`＝**OS 用户**，非 Web 用户） | ✓（set/delete） | ✓（key） | Agent（设置写后端） | **链上留痕**：`config.env_set` / `config.env_delete` |
| 10 | `agent/logging_utils.py::AuditLogger`（`agent/log_system/safe_logger.py:66` 同名副本，无生产调用方）、`agent/api_gateway.py::AccessLogger._write_log:135`（**未接线**，适配层 `api_gateway_flask.py` 不存在）、`agent/monitoring/sensitive_data_filter.py::AccessLogger.log_access:78`（`data/logs/observability_access.jsonl`，含 `user_id`） | 文本/JSONL/未接线 | 端点访问、密钥访问审计 | 半（有 user_id） | ✓（endpoint） | ✓ | 混合（访问日志，非业务写操作） | **本任务不接链**（端点访问日志非「状态变更留痕」语义），列入 §八 遗留 #6 |
| 11 | 其余 metrics/runtime 轨（`task_history.jsonl`、`async_tasks.jsonl`、`heartbeat_history.json`、`resource_monitor_history.jsonl`、`schedule_history.jsonl`、`cost_log.jsonl`、`skills_digest_events-*.jsonl` 等 12 类） | 各自 JSONL/JSON | 运行时计量、心跳、调度历史、成本 | 多无 | 多无 | 多无 | 混合 | **不接链**（非不可否认留痕语义）；纳入归档盘点清单，标记 `mirror_only` |
| 12 | 其余 skill 子域审计轨（`skill_lifecycle_audit.jsonl` / `feedback_agent_audit.jsonl` / `precipitate_audit.jsonl` / `evolution_schedule_audit.jsonl` / `novelty_audit.jsonl` / `skill_merge_backups.jsonl` / `rollback_state.jsonl`） | 各自 JSONL（追加） | 生命周期/反馈/沉淀/调度/新颖性/合并备份/回滚状态 | 无 | 半（event） | 半 | Agent | **本任务不接链**（写侧无 actor 归因，需先补字段，见 §八 遗留 #5） |

**关键盘点结论（决定接线策略的 4 条事实）**

1. **`AuditLogger.log()` 在仓库内没有任何生产写入调用方**——唯一引用是
   `plugins/admin.py:794` 的**只读查询**（管理后台审计列表 `/api/audit/...`）。
   即：S2-02 之前「云枢的审计」实际是由上表 #2~#6 这些**分散事件文件**承载的，
   而非 `logger.py`。因此本任务把链式轨建为**独立统一台账**，并把 #2~#6 逐个接上，
   而不是只改造 `logger.py`（改造它只解决"兼容升级"，不产生审计面）。
2. **审批与谱系是「系统记录本体」而非纯日志**：`approval.py::_persist` 与
   `lineage.py::_persist_active` 都是**全文件原子重写**（读全量→重写）。若把它们按
   "旧库只读"冻结，运行时会 PermissionError。故归档策略分两档（见 §2.2）。
3. **UI 侧此前完全没有审计，且没有身份体系**：`agent/server_auth.py::require_token`
   只比对共享令牌（无 session / `current_user` / cookie 用户），写路由
   `routes_skills_mgmt.py:666` 的发布动作**硬编码 `actor="api"`**。故 UI 审计的 actor
   按"显式 → 请求头 → Cookie → Bearer 令牌指纹 → `ui:<remote_addr>`"解析并**如实记录
   来源**（`identity_source`），绝不臆造用户名（身份层补齐归 S4-01，见 §八 遗留 #1）。
4. **写路由规模**：`agent/server_routes/*.py` 173 条 + `plugins/*.py` 120 条
   ≈ **293 条写路由**（`server_routes/__init__.py::register_all_routes` 为死代码，实际
   由 `app_server.py` 逐个 `register_routes(app, state)` 装载）。逐个改造不可维护，
   故采用「**全局写路由包装（构造即平权）** + 关键路由语义化装饰器」两层策略。

### 2.2 存量 JSONL 只读归档策略（`agent/audit/migration.py`）

| 归档模式 | 适用范围 | 处置 | 依据 |
|---|---|---|---|
| `archive_readonly` | 纯审计/事件轨：`data/audit/audit_*.jsonl`、`skills_assessment_events*.jsonl`、`skills_digest_events-*.jsonl`、`skills_mgmt_review_audit.jsonl` | 置只读（chmod 0o444）+ 清单登记；**跳过「今日」活动分片**（仍在写的不冻结） | 按日分片、旧日不再追加 → 冻结安全；"旧库只读" |
| `mirror_only` | 系统记录本体：`approval_records.jsonl`、`evolution_archive*.jsonl`（+ metrics 类） | **只做只读镜像副本**（`data/audit/legacy_archive/`，副本置只读），原文件保持可写 | 冻结会破坏运行时原子重写（§2.1 结论 2） |
| 共同不变量 | 全部 | **不删除、不导入链（不追溯）**、逐文件登记 sha256/行数/首尾时间戳/归档时间（`data/audit/legacy_archive_manifest.json`）；`ArchiveReport.deleted == retraced == 0` | 任务书 §一.5「不删除、不追溯」 |

### 2.3 UI 写路由接线点

| 层 | 位置 | 覆盖 |
|---|---|---|
| 全局包装（**审计平权由构造保证**） | `app_server.py:94` 之后 `install_flask_audit(app)`（`before_request` 采样身份/体指纹 → `after_request` 按状态码落账 → `teardown_request` 兜底） | 全部 293 条写路由（新增路由无需改造） |
| 语义化装饰器 | `routes_skills_mgmt.py:720` `@audit_action("skill.delete", subject_arg="skill_id")`；`:652` `@audit_action("skill.publish", ...)`；`routes_config.py:78` `@audit_action("config.write", payload_keys=("provider","model","base_url","api_endpoint"))` | 技能删除/发布、设置写（验收要求的三类） |
| 只读过滤 | 跳过 `GET/HEAD/OPTIONS` 与 `/static`、`/health`、`/metrics`、`/api/audit`（`AUDIT_UI_SKIP_PREFIXES` 可扩展） | 避免读操作污染审计 |
| 请求体裁剪 | 只记 sha256 + 字节数；>1MiB 或 `multipart/form-data` 记 `(skipped:large|multipart)` | 密钥/密码原文不入链、内存不膨胀 |

**审批 approve/reject 的 UI 入口事实**：本仓库**不存在审批 HTTP 路由**（`grep -r "/approval"`
仅命中文档与测试），审批只经服务层网关 `SkillsManagementService.approve_change/reject_change`
→ `ApprovalFlow.approve/reject`。故本任务把审批留痕接在**状态机唯一迁移漏斗**
`ApprovalFlow._transition`/`_append_record`（无论 UI、CLI 还是 Agent 调用都入链），
验收演示以「服务层审批迁移 + UI 技能删除/设置写」三者同表呈现。

---

## 三、预期成果对照（任务 §三）

| # | 预期成果 | 交付 | 验收 |
|---|---|---|---|
| 1 | `agent/audit/chain.py`：AuditEntry/AuditChain/verify_chain/daily_merkle_root | ✅ 1200 行：`AuditEntry`(+校验/规范化/行序列化)、`AuditChain`(append/flush/close/entries/iter_entries/stats/reader)、`verify_chain()`、`daily_merkle_root()`、`verify_daily_root()`、`merkle_root/proof`、`RootsSigner`、单写者登记 | ✅ |
| 2 | 统一审计门面（agent+ui 同表）与写路由接入 | ✅ `agent/audit/facade.py`：`audit.record(...)`（脱敏先于入链、actor 解析+来源标注、best-effort/strict）+ `agent/audit/ui_middleware.py`：`install_flask_audit` / `@audit_action` / `UIAuditRecorder`；`app_server.py` 已接线 | ✅ |
| 3 | 存量 JSONL 只读归档 + 双写过渡兼容 | ✅ `agent/audit/migration.py`：`inventory_legacy_files` / `archive_legacy_files`（只读/镜像两档、清单、不删除不追溯）/ `read_legacy_records` / `LegacyTrack`(双写+一致性+回滚窗口) ；`AuditLogger` 双写升级（旧格式逐字保留，仅加 `audit_ref`） | ✅ |
| 4 | `scripts/verify_audit_chain.py` 验签 CLI | ✅ 全链/区间验签 + 每日根重放 + 摘要 + JSON 输出 + 退出码 0/1/2 | ✅ |
| 5 | 篡改注入演练通过记录 + `TASK-S2-02_验收报告.md` | ✅ `scripts/demo_s2_02_audit.py` 实跑（§七.3）+ 本文件 | ✅ |

---

## 四、验收清单逐条核验（任务 §四）

### ✅ 1. self_hash 公式与 §3.5 一致；篡改中间任意一条，后续全部校验失败且定位到注入 seq

- 公式硬编码在单一函数 `self_hash_formula()`：`f"{seq}|{ts}|{actor}|{action}|"
  f"{subject}|{payload_hash}|{prev_hash}"`，`self_hash = sha256(该串)`；
  `payload_hash = sha256(规范化记录 JSON)`（`canonical_record_json`，sort_keys + 紧凑分隔符）。
  单测 `TestHashFormula`（10 例）含「手工 sha256 等值」「字段顺序/分隔符」「键序无关」
  「元数据绑定」逐项断言。
- **定位注入 seq**：`verify_chain()` 逐条四道校验（字段合法性 → seq 连续 → prev_hash
  对**重算值** → payload_hash/self_hash 两级），前驱取「重算的 self_hash」前向传播，
  故注入点之后**全部**失败。实测（§七.3）注入 `seq=10` →

```
TAMPERED — 首个异常 seq=10（payload_hash 重算不一致）: payload_hash 重算=31ff86e2… ≠ 存储=c23e59b4…；
异常共 10 处（注入点之后因哈希前向传播全部失败）
  - seq=10  payload_hash_mismatch  载荷或元数据被改
  - seq=11  prev_hash_mismatch     链断裂
  - seq=12  prev_hash_mismatch     链断裂
  …（seq 13..19 同类）
```

- 单测覆盖 5 类篡改：字段改（`test_field_tamper_detected_at_that_seq`）、载荷改
  （`test_payload_tamper_detected`）、self_hash 改、prev_hash 断、seq 缺行/删首行
  （`test_delete_middle_row_detected` / `test_delete_first_row_detected_via_genesis_link`），
  以及 `test_all_subsequent_records_fail_after_tamper` 断言 `bad_seqs == [3..8]` 且
  `checked == 2`（仅注入点之前通过）。
- **已知边界（诚实口径）**：仅删除**链尾**记录、且无外部锚点时不可检出（哈希链固有
  局限）；本任务以「每日 Merkle 根 + 外层根链 + 受保护只追加文件 + 签名」缩小该窗口，
  外部只追加存储仍入 P5 Backlog（§八 遗留 #2）。

### ✅ 2. 单写者约束生效（并发写测试无重复 seq/竞态）

- 机制：`_WRITER_REGISTRY`（路径 → 属主 token）在构造时登记，重复 writer 抛
  `SingleWriterViolationError`；`close()` 释放；`get_audit_chain()` 提供进程单例；
  后台线程用 `AuditChain.reader()`（不占登记、不启线程、`append` 抛 `ReadOnlyChainError`）。
- 单测：`TestSingleWriter`（6 例）——第二 writer 抛错、reader 不占位、单例复用、
  close 后可重建；`test_concurrent_appends_unique_seqs_no_race`：**8 线程 × 25 条**
  → 200 条 seq 去重后仍 200、`seqs == 1..200`、`verify_chain().ok`。
- 修复记录：压测中发现 `reset_audit_chains()` 原实现「先清登记、后关闭」会让
  「仍在 flush 的旧 writer」与「按旧 max(seq) 起链的新 writer」并发写同一路径，
  产生 `UNIQUE constraint failed: audit_chain.seq`。已改为**先关闭后清登记、全程持锁**，
  并新增 `_resync_seq()` + `IntegrityError` 分支（记录进 ring buffer + 显式告警，
  不静默丢弃、不永久降级）。

### ✅ 3. UI 操作（至少审批/技能删除/设置写）与 Agent 操作同表可查（平权演示）

- 同表定义：**同一张 `audit_chain` 表、同一条 seq 链**（`source` 列区分 `agent`/`ui`）。
  演示实跑（§七.3）19 条：`agent 16 + ui 3`，其中

```
17   ui      admin@yunshu     skill.delete        demo-skill-delete      （真实 DELETE /api/skills-mgmt/<id>）
18   ui      ui:127.0.0.1     config.write        ui:/api/config         （真实 POST /api/config）
19   ui      ui:127.0.0.1     ui.api_skills_mgmt_toggle.post  …          （真实 POST toggle，身份降级路径）
 1   agent   human            approval.submit     skill:demo-skill-a
 2   agent   reviewer         approval.approved   skill:demo-skill-a
 4   agent   reviewer         approval.rejected   prompt:demo-prompt-b
11   agent   admin            config.access       config:llm_api_key     （敏感操作面）
13   agent   admin            permission.change   shell_execute
14   agent   admin            auth.attempt        user:admin
16   agent   AdminWT          config.env_set      env:llm_model          （设置写后端）
```

- 集成测试 `TestUIAuditEquality::test_ui_and_agent_share_one_table_and_chain` 断言：
  seq 连续、`verify_chain().ok`、一条 SQL 同时取回两类来源（`source='agent'` 与
  `source='ui'` 计数）、且 **UI 记录的前驱 = 相邻 Agent 记录的 self_hash**（同一条链）。
- 单测 `test_ui_records_share_one_chain`：UI 路由记录与路由内 `audit.record` 调用
  （`actor_source=ui_request_context`）同链。

### ✅ 4. 每日 Merkle 根生成并可重放验证；无外部存储时降级路径明确记录

- `daily_merkle_root(date)`：叶子 = 当日记录的 `self_hash`（seq 序），奇数末节点上提，
  空日根 = `sha256("")`（**当日无记录也留根**，证明"无新增"）；写入
  `data/audit/daily_roots.jsonl`（**受保护**：追加后 chmod 只读、追加前临时恢复写权限）。
- 重放：`verify_daily_root(date)` 按根记录中的**封印 seq 区间**重算根 + 逐条两级哈希 +
  验签 + 外层根链连续性；成员证明 `merkle_proof/verify_merkle_proof` 亦单测（n=1..17
  逐叶验证）。
- 签名：`cryptography 48.0.0` 可用 → **ed25519 真签名**（公钥随根记录落盘，实测
  `签名=ed25519，有效`）；`signing_enabled=False`/库缺失/密钥不可用时降级为
  **sha256 自签占位**，根记录 `degraded=True` 且 `degraded_reason` 写明
  "无外部 Keychain/密钥，P4 分级实施降级"（单测 `TestSigning` 6 例覆盖两路径）。
- **设计裁定（D3）**：根是「封印时刻的前缀快照」——重放只覆盖记录中的 `first_seq..last_seq`，
  因此封印后又追加当日新记录不产生误报；封印区间内被删/被改必然检出
  （`root_hash_mismatch` / `leaf_count_mismatch` / `entry_hash_mismatch`）。
  实测：演示台账先封印 19 条、再追加 200 条性能探针，CLI 复验仍 **OK（19 条叶子）**。

### ✅ 5. 存量 JSONL 只读归档，无追溯性丢失；双写过渡期内新旧轨一致

- 只读归档：`archive_legacy_files()` 两档处置（§2.2），逐文件 sha256/行数/首尾时间戳
  登记入 `legacy_archive_manifest.json`；`deleted == 0`、`retraced == 0`（不删除、不追溯）；
  原文件内容逐字保留（单测 `test_original_content_preserved`）；
  `read_legacy_records()` 提供只读追溯查询（limit/offset/坏行跳过）。
- 幂等与安全：重复归档会先恢复副本写权限再覆盖（`test_archive_is_idempotent`）；
  「今日」活动分片跳过冻结（`test_active_shard_not_frozen`）；dry-run 零副作用。
- 双写过渡：`LegacyTrack.emit()` 同时写旧 JSONL 与新链，`audit_ref` 关联键对齐
  （经 `technical` 通道入链，避免脱敏启发式误伤——见 §五 D6）；
  `verify_consistency()` 按 `audit_track` 轨名过滤后比对，实测 **旧轨 5 / 新链 5 / 匹配 5
  = 一致率 100%**；漏写任一侧均被检出（`only_legacy` / `only_chain` 两向单测）。
- 回滚窗口：`AUDIT_DUAL_WRITE=0` → `AuditLogger` 行为与 S2-02 之前**逐字一致**
  （记录字段集合断言，且不创建链式台账）；`AUDIT_LEGACY_WRITE=0`/`legacy_enabled=False`
  → 旧轨停写、仅新链；`retire_after` 过期自动停写旧轨。

### ✅ 6. 混沌演练「注入篡改」项通过（verify CLI 报告）

- 演练脚本：`scripts/demo_s2_02_audit.py`（第 7 步）在台账**副本**上执行
  `UPDATE audit_chain SET actor='mallory', action='skill.delete' WHERE seq=<链中间一条>`，
  再跑 `verify_chain()` 与 CLI：

```
验签结论：TAMPERED — 首个异常 seq=10（payload_hash 重算不一致 …）；异常共 10 处
CLI --db <篡改副本> --roots <roots> --roots-check all --stats → 退出码 1（1=检出篡改）
```

- 演练后对**未篡改原台账**复验：`OK — 链完整，已校验 219 条`（CLI 退出码 0），
  证明检出结论来自注入本身而非环境噪声。
- 纳入自动化：`tests/unit/test_audit_integration.py::TestVerifyCli`
  （10 例：干净=0 / 篡改=1 且报告 seq / `--json` 可解析 / 缺库=2 / `--allow-missing`=0 /
  锚点区间 / 每日根篡改=1 / 注入点后续失败序列 `[3,4,5,6]`）。

### ✅ 7. 既有 audit/审批/评审套件零回归；新增单测全绿、覆盖率 ≥80%

| 套件 | 结果 |
|---|---|
| **新增 5 套件**（`test_audit_chain` 124 / `test_audit_facade` 42 / `test_audit_migration` 41 / `test_audit_ui_middleware` 46 / `test_audit_integration` 25） | ✅ **278 passed / 0 failed** |
| 既有 audit + trace 类（test_audit / audit_logger_comprehensive / audit_safety_logging_singleton / env_config_audit / env_config_manager / env_hot_reload / env_file_permissions / knowledge_audit_edge / logging_utils / log_dict ×2 / log_system_safe_logger / log_system_storage / 集成 audit_trace / trace_v2 ×2 / trace_coverage / trace_store） | ✅ 全绿（含在下方合计） |
| 既有 skills 审批/评审/谱系（skills_mgmt / skills_mgmt_safety / skills_mgmt_lineage / review_enforcement / reviewer / skills_digest_assessor） | ✅ 全绿 |
| 既有 descriptors（registry / bridge / models / validator / backfill） | ✅ 全绿 |
| 既有路由（集成 routes_config / routes_skills_mgmt；单测 server_routes_comprehensive / server_routes_supplement / routes_config_validation） | ✅ 全绿 |
| **合计（39 个套件，新增 5 + 既有 34）** | ✅ **1373 passed / 0 failed / 3 skipped / 1 xfailed**（112.0s） |

覆盖率（`--cov=agent.audit`，仅跑本任务 5 套件，branch=True）：

```
agent\audit\chain.py            1080    155    322     48  84.38%
agent\audit\facade.py            215     29     52      7  83.52%
agent\audit\logger.py            107     16     26      8  81.95%
agent\audit\migration.py         354     32     94     16  88.84%
agent\audit\ui_middleware.py     272     35     84     12  85.67%
agent\audit\observability.py      31     31      2      0   0.00%   ← 既有模块（本任务未改动、未引用）
TOTAL                           2059    298    580     91  84.05%
```

本任务改动/新增模块**逐个 ≥ 80%**；包级 TOTAL 84.16% 亦 ≥80%（其中 `observability.py`
为 S2-02 之前既有、本任务未触及的文件）。

### ✅ 8. 单条 append 性能达标（≥§11.2 量级内，实测记录）

- 实测（`scripts/demo_s2_02_audit.py` 第 8 步，n=200，Windows 本机；持久化摘要
  `data/audit/audit_stats.json`）：

```
第一次实跑：均值 0.0178ms / p50 0.0172ms / p95 0.0192ms / max 0.0535ms
持久化摘要：均值 0.0187ms / p50 0.0181ms / p95 0.0203ms / max 0.0535ms
（目标 <5.0ms → 两次均达标，余量 ≈ 260 倍）
```

- 结构保证：`append()` 仅做「seq 分配 + 两级 sha256 + 入队」（互斥锁内，无 IO）；
  SQLite 提交由 writer 线程**批量**执行（`WRITER_BATCH_SIZE=100`，WAL + synchronous=FULL，
  审计优先耐久）。单测 `TestPerformance` 以 200 次采样断言均值 <5ms 作为回归护栏。

---

## 五、关键设计裁定（云枢对设计文档未闭合点的显式化）

| # | 议题 | 裁定 | 理由 |
|---|---|---|---|
| D1 | 链式台账落哪 | **独立库** `data/audit/audit_chain.db`（任务书允许「独立 audit.db」） | 审计写入频率低、要求 WAL+synchronous=FULL 耐久，与 `tool_trace.db` 的高频轨迹写入分库，避免争锁与被轨迹体量拖累；路径可经 `AUDIT_DB_PATH` 覆盖 |
| D2 | `payload_hash` 绑什么 | 绑定**整条记录的规范化 JSON**（含 source/trace_id/workspace_id/schema_version/payload），而非仅业务 payload | §3.5 公式只把 `payload_hash` 纳入 self_hash，若它只哈希业务载荷，则改 `actor` 之外的元数据列可绕过；绑定全记录后 `verify_chain` 形成「链级 + 载荷级」两级校验，**加强而不改变 self_hash 公式** |
| D3 | 每日根与「当天还在写」 | 根 = **封印时刻的前缀快照**（记录 `first_seq/last_seq`），重放只覆盖该区间 | 避免「封印后追加即误报」；同时保证区间内删改必检出。生产侧 `auto_seal` 只封**已过完的日**，人工封印当日即为快照 |
| D4 | 无密钥时的签名 | ed25519 优先；不可用时 **sha256 自签占位**并写 `degraded=True + degraded_reason` | 对齐任务书「无 Keychain 时先 sha256 自签占位并记录降级」与 P4 分级实施；占位签名只提供一致性重算，不提供抗伪造，已在记录中显式声明 |
| D5 | 归档为何分两档 | 纯审计轨 `archive_readonly`；系统记录本体 `mirror_only` | 审批/谱系是**运行时状态本体**（全文件原子重写），冻结即 PermissionError 破坏运行；镜像副本 + 链上留痕同样满足「不删除、不追溯、可追溯查询」 |
| D6 | 关联键如何避开脱敏 | `audit_ref`/`audit_track` 经门面 `technical` 通道入链（**不经脱敏**），其余字段一律先脱敏 | 实测脱敏启发式会把 24 位随机 hex 关联键部分掩码（`71420114895d4c9997****ef`），导致两轨无法对齐；关联键是内部生成值，非用户输入 |
| D7 | UI 显式装饰器的落账时机 | **先执行视图、再按结果落账**（成功记状态码、4xx=rejected、5xx=error、抛异常=exception 后原样抛出） | 追加即链、事后无法补写结果字段；若在视图前落账，则审计记不到结果（与"审计要能回答发生了什么"相悖） |
| D8 | 全局包装与显式装饰器去重 | 显式装饰器落账后置 `g._audit_explicit`，全局包装跳过该请求 → **一请求一记录** | 避免同一次 UI 写操作产生两条语义重复记录（审计噪声会稀释可读性） |
| D9 | UI actor 无身份体系怎么办 | 「显式 → 头 → Cookie → Bearer 令牌指纹 → `ui:<remote_addr>`」逐级降级，并记录 `identity_source`/`actor_source` | 现状无登录用户（§2.1 结论 3）；**如实标注来源、不臆造用户名**，身份层补齐归 S4-01 |
| D10 | 兼容层改多少个字 | `AuditLogger` 旧 JSONL 格式**逐字保留**（仅 additive 增加 `audit_ref`），双写后 `flush_on_write` 默认 True | 既有 4 个套件断言旧字段集合与查询语义；durable-on-return 同时避免后台 writer 短暂占用台账文件（Windows 上会阻碍测试 teardown 删目录） |
| D11 | 文件句柄策略 | 文件库连接**用完即关**（WAL 属性随库持久化），仅 `:memory:` 用常驻连接 | 常驻连接会长期占住 `audit_chain.db`（Windows 无法删除/替换），并使"删库重建"场景出现 seq 撞车 |

---

## 六、联动与文件清单

### 6.1 新增文件

| 文件 | 内容 |
|---|---|
| `agent/audit/chain.py`（新增，1617 行） | `AuditEntry`、`AuditChain`（append/flush/close/entries/iter_entries/get/last_entry/count/seq_range/chain_head/stats/reader/verify_chain/daily_merkle_root/verify_daily_root/verify_daily_roots_all/clear）、`verify_chain()`、`ChainVerification`(+`bad_seqs`)、`DailyRoot`、`RootsVerification`、`RootsSigner`（ed25519 + sha256 降级）、`merkle_root/proof/verify_merkle_proof`、`compute_payload_hash/compute_self_hash/self_hash_formula`、`get_audit_chain/reset_audit_chains/active_writers`、异常族（`SingleWriterViolationError`/`ReadOnlyChainError`/`AuditEntryError`） |
| `agent/audit/facade.py`（新增，370 行） | `AuditFacade`（`record`/`record_agent`/`record_ui`/`record_trace_event`/`record_redact_event`/`recent`/`verify`/`snapshot`/`chain` 懒加载/`bind`）、进程单例 `audit`、`set_ui_actor/get_ui_context/reset_ui_actor`、`redact_payload` |
| `agent/audit/ui_middleware.py`（新增，435 行） | `UIAuditRecorder`（before/after/teardown 三钩子）、`install_flask_audit`、`@audit_action`、`resolve_ui_actor`/`token_fingerprint`/`action_from_request`/`_status_from_code`/`_response_status_code` |
| `agent/audit/migration.py`（新增，512 行） | `LEGACY_TARGETS`、`inventory_legacy_files`、`archive_legacy_files`(+`ArchiveReport`)、`read_legacy_records`、`is_readonly`、`legacy_write_allowed`、`LegacyTrack`(+`DualWriteResult`/`ConsistencyReport`)、`record_migration_event` |
| `scripts/verify_audit_chain.py`（新增，117 行） | 验签 CLI（全链/区间 + 每日根重放 + 摘要 + JSON + 退出码 0/1/2） |
| `scripts/demo_s2_02_audit.py`（新增，391 行） | 端到端演示：Agent/UI 同表、双写一致性、每日根重放、篡改注入演练、性能实测、`data/audit/audit_stats.json` 摘要 |
| `tests/unit/test_audit_chain.py` | **124 例**：哈希公式 10 / 记录模型 8 / 追加与 seq 12 / 单写者并发 6 / 读取接口 10 / 验签 14 / Merkle 12 / 每日根 18 / 签名 8 / append-only 6 / 降级 7 / 性能 2 |
| `tests/unit/test_audit_facade.py` | **42 例**：写入与来源 16 / 脱敏 5 / 开关与失败 8 / Trace 事件 5 / 读与单例 8 |
| `tests/unit/test_audit_migration.py` | **41 例**：盘点 5 / 归档 8 / 只读与退役 8 / 双写 12 / Logger 兼容 8 |
| `tests/unit/test_audit_ui_middleware.py` | **46 例**：身份解析 7 / 动作与过滤 12 / 真实路由落账 21 / 装饰器离线 6 |
| `tests/unit/test_audit_integration.py` | **25 例**：Agent 侧 10（审批/评审/台账/谱系/Trace/敏感操作/配置变更）/ UI 平权 5 / CLI 10 |

### 6.2 存量文件改动（均为 additive / best-effort，异常不阻断主路径）

| 文件 | 改动 |
|---|---|
| `agent/audit/__init__.py` | 导出链式层/门面/UI/迁移公共 API（既有 `AuditLogger`/`audit_logger` 零移除） |
| `agent/audit/logger.py` | 双写升级：旧 JSONL 逐字保留 + 新链（`audit_ref` 关联键）、`facade`/`track`/`chain`/`verify_chain`/`query_chain`/`flush`/`close`；`AUDIT_DUAL_WRITE` 回滚开关 |
| `agent/skills_mgmt/approval.py` | 新增 `ApprovalFlow._audit()`；`_append_record`/`_transition`/`merge`/`mark_manual_executed` 四处调用（审批全生命周期入链） |
| `agent/skills_mgmt/review_gate.py` | `audit_exemption()` 追加链式留痕（豁免发布是治理关键动作） |
| `agent/skills_mgmt/service.py` | `_emit_assessment_event()` 追加链式留痕 |
| `agent/skills_mgmt/lineage.py` | `EvolutionArchive.append()` 追加链式留痕（object_type 前缀去重） |
| `agent/descriptors/registry.py` | `_audit()` 追加链式留痕（内存环保留，action 前缀去重） |
| `agent/observability/trace_v2.py` | `redact_then_hash()` 脱敏实际发生→`trace.redact` 入链（**S2-01 遗留 #3 收口**，只记字段数与字段名）；`TraceFacade.record()` 失败/阻断→`trace.tool.error|blocked`；`finish()`→`trace.closed` |
| `agent/logging_utils.py` | 新增 `AuditLogger._chain_audit()` 助手；7 个敏感操作方法（配置访问/修改、安全配置访问、加密密钥访问、权限变更、认证尝试、敏感操作）追加链式留痕（文本轨 `logs/audit.log` 逐字保留；`log_sensitive_operation` 只记详情字段名，值二次脱敏） |
| `agent/env_config_manager.py` | `_audit_log()` 追加链式留痕（`config.env_set`/`config.env_delete`；旧 `logs/config_audit.jsonl` 保留） |
| `app_server.py` | `app = Flask(...)` 之后接线 `install_flask_audit(app)`（审计平权全局钩子，失败不阻断启动） |
| `agent/server_routes/routes_skills_mgmt.py` | `@audit_action("skill.delete"/"skill.publish")` + 防御式导入（审计模块不可用时退化为 no-op 装饰器） |
| `agent/server_routes/routes_config.py` | `@audit_action("config.write", payload_keys=...)` + 防御式导入 |
| `.gitignore` | 显式登记 S2-02 运行时产物（链式台账/每日根/签名密钥/归档副本/演示摘要） |

### 6.3 既有接口零破坏核验

- `AuditLogger` 旧 JSONL 记录字段集合与 `query()` 语义**逐字保持**（`AUDIT_DUAL_WRITE=0`
  时与 S2-02 之前完全一致，单测 `test_plain_path_has_no_audit_ref` 以字段集合相等断言）；
- 审批/评审/谱系/descriptor 的**旧写入路径与返回值零改动**（仅追加 best-effort 审计调用，
  异常在调用点内吞掉，`logger.debug` 记录）；
- `trace_v2` 既有 API/顺序语义不变（新增仅在 `redact_then_hash` 尾部与三处收尾点）；
- 路由装饰器用 `functools.wraps` 保持视图函数名（Flask endpoint 不变），审计模块导入失败时
  退化为 no-op 装饰器 → 路由功能不受影响；
- `app_server.py` 仅在 `Flask(...)` 之后追加注册，`AUDIT_UI_ENABLED=0` 可整体关闭。

---

## 七、执行证据（2026-09-10 实跑）

### 7.1 新增套件与覆盖率

```
新增 5 套件：278 passed / 0 failed
覆盖率（--cov=agent.audit，仅跑本任务 5 套件，branch=True）：
  chain.py 84.38%  facade.py 83.52%  logger.py 81.95%  migration.py 88.84%  ui_middleware.py 85.67%
  TOTAL 84.05%（含未改动 observability.py 0%）
```

### 7.2 既有套件回归

```
39 个套件（新增 5 + 既有 34：audit/trace/skills 审批评审谱系/descriptors/路由/logging/env）：
  1373 passed / 0 failed / 3 skipped / 1 xfailed（112.0s）
  命令：python -m pytest <39 个文件> -q -p no:randomly
其中既有相关面（audit + trace + skills + descriptors + 路由 + logging/env，34 个套件）
  全部零回归；3 skipped 与 1 xfailed 均为既有标记（环境/预期失败），非本任务引入。
```

### 7.3 演示实跑：`python scripts/demo_s2_02_audit.py`

```
台账：data/audit/audit_chain_demo.db
[1] Agent 侧治理动作已入链（审批 2 条 / Trace 10bf7a6568c043f9）
[2] UI 写路由：DELETE /api/skills-mgmt/<skill_id>  → HTTP 200（审计已入同一张表）
[2] UI 写路由：POST /api/config                    → HTTP 200（审计已入同一张表）
[2] UI 写路由：POST /api/skills-mgmt/<id>/toggle   → HTTP 200（审计已入同一张表）
[3] 双写过渡：新旧轨一致=是 旧轨 5 条 / 新链 5 条 / 匹配 5 条（一致率 100.00%）

[4] 审计平权验证（UI 与 Agent 同表 audit_chain）：
    1    agent  human           approval.submit                 skill:demo-skill-a
    2    agent  reviewer        approval.approved               skill:demo-skill-a
    3    agent  agent           approval.submit                 prompt:demo-prompt-b
    4    agent  reviewer        approval.rejected               prompt:demo-prompt-b
    5    agent  operator-demo   trace.redact                    trace                 （S2-01 遗留 #3）
    6    agent  auto            trace.tool.error                trace:9c00edd4…
    7    agent  auto            trace.closed                    trace:10bf7a65…
    8    agent  agent           descriptor.register             capability:cp.demo.audit_probe
    9    agent  human           descriptor.unregister           capability:cp.demo.audit_probe
    10   agent  human           skill.review_waiver_publish     skill:demo-skill-waived
    11   agent  admin           config.access                   config:llm_api_key
    12   agent  admin           config.modify                   config:llm_model
    13   agent  admin           permission.change               shell_execute
    14   agent  admin           auth.attempt                    user:admin（IP 脱敏 10.0.xxx.xxx）
    15   agent  admin           config.encryption_key_access    config:encryption_key
    16   agent  AdminWT         config.env_set                  env:llm_model（OS 用户，身份层缺口见遗留 #1）
    17   ui     admin@yunshu    skill.delete                    demo-skill-delete
    18   ui     ui:127.0.0.1    config.write                    ui:/api/config
    19   ui     ui:127.0.0.1    ui.api_skills_mgmt_toggle.post  ui:/api/skills-mgmt/…
    来源分布：{'agent': 16, 'ui': 3}
    同表可查（同一 audit_chain 表、同一 seq 链）：是
[5] 链式验签：OK — 链完整，已校验 19 条（seq 1..19，链头 self_hash=0f83a4d99580d22d…）
[6] 每日 Merkle 根：date=2026-09-10 root=d3ac7d1f278bebb4… 叶子=19 签名=ed25519 保护=True
    重放校验：OK — 每日根可重放：date=2026-09-10（19 条叶子，签名=ed25519，有效）

[7] 混沌演练 §11.10「向审计链注入一条篡改」：
  注入点：seq=10（原 skill.review_waiver_publish / actor=human → 被改为 skill.delete / mallory）
  验签结论：TAMPERED — 首个异常 seq=10（payload_hash 重算不一致…）；异常共 10 处
  失败明细：seq=10 payload_hash_mismatch；seq=11..19 prev_hash_mismatch（链断裂）
  CLI --db <篡改副本> --stats → 退出码 1（1=检出篡改）

[8] 性能实测：单条 append 均值 0.0179ms / p50 0.0173ms / p95 0.0195ms / max 0.0529ms
    （目标 <5.0ms → 达标）
[9] 摘要已写出：data/audit/audit_stats.json
```

### 7.4 验签 CLI 实跑（对未篡改的演示台账，含封印后追加 200 条性能探针）

```
> python scripts/verify_audit_chain.py --db data/audit/audit_chain_demo.db \
      --roots data/audit/daily_roots_demo.jsonl --roots-check all --stats
[链式校验] OK — 链完整，已校验 219 条（seq 1..219，链头 self_hash=a3d22e2c6f8d327d…）
[每日根] OK — 每日根可重放：date=2026-09-10 root=f5b46e7fc26a8b6a…（19 条叶子，签名=ed25519，有效）
[台账] 条数=219 seq=1..219 来源={'agent': 216, 'ui': 3} 每日根签名=ed25519
       链头 self_hash=a3d22e2c6f8d327d… 每日根=1 降级=False
exit=0
```

### 7.5 篡改注入的 CLI 输出（副本）

```
> python scripts/verify_audit_chain.py --db <篡改副本> --roots <roots> --roots-check all --stats
[链式校验] TAMPERED — 首个异常 seq=10（payload_hash 重算不一致（载荷/元数据被篡改））:
           payload_hash 重算=6ad399040b32d80f… ≠ 存储=2d6f964b19a1afef…；异常共 10 处
  注入点 seq=10（payload_hash_mismatch）——后续全部失败明细（前 20 条）：
    - seq=10  payload_hash_mismatch  …
    - seq=11  prev_hash_mismatch     prev_hash=85669a818c38808f… ≠ 上一条重算 self_hash=d482de50e2933a99…
    - seq=12  prev_hash_mismatch     …
exit=1
```

---

## 八、遗留清单（不阻塞本任务验收；随主线消费）

| # | 遗留 | 归属 |
|---|---|---|
| 1 | **UI 身份层缺失**：无登录用户/session，UI actor 现为「请求头/Cookie/令牌指纹/`ui:<remote_addr>`」降级（已如实标注 `identity_source`）；`routes_assets.py` 5 条写路由无鉴权 | S4-01（审批矩阵与审批面安全） |
| 2 | **外部只追加存储**（WORM/S3 Object Lock/远程 syslog）与跨机锚定：当前为单机降级（受保护文件 + 每日根 + 签名），删除链尾仍不可检出 | P5 Backlog（P4 分级实施已裁定） |
| 3 | **审批 HTTP 路由不存在**：审批只经服务层网关，UI 若有独立审批页需新增路由（届时自动被全局包装审计） | S4-01/S6 |
| 4 | 台账**保留/归档策略**（无 TTL，链式轨只增不减）与跨日分库 | S2 生产化 / S5（与 S2-01 遗留 #7 合并处理） |
| 5 | **跨进程并发写**（多 worker 下由 WSGI 多进程共写一台账）：现为进程内单写者 + `busy_timeout` 兜底；跨进程需文件锁或外部存储 | S2 生产化（同 S2-01 遗留 #6） |
| 6 | 全局门面 `audit.record` 的**脱敏口径**依赖 `trace_v2.redact`；`technical` 通道绕开脱敏，需在代码评审中守住"只放内部生成值"纪律 | S2/S4 代码规范 |
| 7 | `descriptor.*` 审计目前只进内存环 + 链；`descriptors.json` 台账本身未做链式绑定 | S3-01（能力地图） |
| 8 | 归档 CLI 化（当前归档入口为 `agent.audit.migration.archive_legacy_files()`，未提供 `scripts/archive_legacy_audit.py` 单命令入口） | S2 生产化（如需运维单命令） |
| 9 | 其余 skill 子域审计轨（`skill_lifecycle_audit` / `feedback_agent_audit` / `precipitate_audit` / `evolution_schedule_audit` / `novelty_audit` / `skill_merge_backups` / `rollback_state`）**写侧无 actor 字段**，未接链；建议先补 actor 归因再双写 | S3/S4（随 skill 域重构） |
| 10 | 端点访问日志（`api_gateway.AccessLogger` 未接线、`sensitive_data_filter.AccessLogger.log_access` 含 `user_id`）语义为「访问」而非「状态变更」，未接链；如需纳入需先裁定口径 | S4-01 / S6（可观测面） |
| 11 | 认证审计的客户端 IP 沿用仓库既有脱敏口径（`10.0.0.7` → `10.0.xxx.xxx`），如需原始 IP 归因需与 PII 口径一并裁定 | S4-01 |

---

*补记一：本任务对「审计平权」的实现取向是**「构造即平权」**——不依赖 293 条写路由逐条
改造（不可维护且必然遗漏），而是把 Flask 写方法全局钩子作为不变量；语义化装饰器只用于
给关键动作起可读名并补充业务字段（subject/request_fields），两者用 `g._audit_explicit`
去重，保证「一请求一记录」。*

*补记二：`payload_hash` 绑定整条记录而非仅业务载荷，是对 §3.5 的**加强**：§3.5 的
`self_hash` 公式逐字保持，但把 source/trace_id/workspace_id/schema_version/payload
一并纳入 `payload_hash` 的规范化 JSON，使「只改元数据列不改链式字段」的绕过路径被堵死
（`verify_chain` 的第 4 道校验即载荷级重算）。*

*补记三（实现期修复的 3 个真实缺陷，均已加回归测试）*：
① `reset_audit_chains()` 原「先清登记后关闭」导致新旧 writer 并发写同一路径 →
`UNIQUE constraint failed: audit_chain.seq`（已改先关后清 + 全程持锁 + seq 重同步告警）；
② 文件库常驻 sqlite 连接使 `audit_chain.db` 长期被占用（Windows 上目录无法删除、
"删库重建"场景 seq 撞车）→ 改为**用完即关**（仅 `:memory:` 常驻）；
③ 脱敏启发式把 24 位 hex `audit_ref` 部分掩码（`71420114895d4c9997****ef`）导致双写两轨
无法对齐 → 关联键改走 `technical` 通道并加不变性测试。*

*补记四（每日根语义）：`daily_merkle_root()` 是**封印快照**而非"当日全量"——根记录含
`first_seq/last_seq`，重放只覆盖该区间。生产侧 `auto_seal` 只在跨日时封"已过完的日"，
天然覆盖全天；人工当日封印则为快照，后续追加不会造成误报，区间内删改仍必检出。*
