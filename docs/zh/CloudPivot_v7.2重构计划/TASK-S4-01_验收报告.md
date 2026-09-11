# TASK-S4-01 验收报告 — 审批 Actor 矩阵与审批面安全（对齐 §7.0 / §5.7⑦）

> 任务书：[`TASK-S4-01_审批矩阵与审批面安全.md`](TASK-S4-01_审批矩阵与审批面安全.md)
> 分发壳：[`START-S4-01_审批矩阵与审批面安全.md`](START-S4-01_审批矩阵与审批面安全.md)
> 基线：`master` / `dced7c4f`（生成时）｜worktree：`s401`｜验收日期：2026-09-11
> 结论：**验收清单 9/9 通过**（含 S2-02 #1/#11、S2-03 #13 三项收口）

---

## 一、结论摘要

| # | 验收项（任务书 §四） | 结论 | 关键证据 |
|---|---|---|---|
| 1 | §7.0 矩阵核心行全部可判定（后端表驱动，前端不拥有额外权限） | ✅ | `agent/security/actor_matrix.py`（33 行内置表 = 11 操作 × 3 执行体）；`TestMatrixCoreRows` 逐行断言；`test_matrix_doc_and_rows_cover_same_groups` 防文档漂移 |
| 2 | auto/skill 与 sub_agent 调审批 → 拒绝 + 审计 + 告警（越权用例） | ✅ | `TestViolationTriplet` / `TestApprovalFlowEnforcement`；链上 `policy.denied` + `[SECURITY][APPROVAL_VIOLATION]` 告警 |
| 3 | human 审批记录 actor=登录会话；记录入链式审计与 approval 事件 | ✅ | `TestHumanApprovalAudit`；`_audit` 写 `approval.approved`（S2-02）+ `acr.record_approval`（S2-03） |
| 4 | 审批 token 会话绑定（分享式链接失效用例）；链接时效 ≤15min 生效 | ✅ | `TestLinkBinding::test_shared_link_fails_in_another_session`；`test_link_expires_within_15_minutes`（900s 硬上限） |
| 5 | destructive 审批触发二次认证（无二次认证不可通过） | ✅ | `TestDestructiveSecondFactor`（4 例，含「无确认码 403 且状态不变」） |
| 6 | 前端审批按钮区 DOM 隔离 + CSRF 保护验证 | ✅ | `TestFrontendIsolation`（Shadow DOM + 固定 zIndex 2147483000 + `isolation: isolate` + `X-CSRF-Token`） |
| 7 | 既有 approval/EVO/hitl 套件零回归；新增单测全绿、覆盖率 ≥80% | ✅ | 邻接回归 419 passed/0 failed；新增 **267** 例全绿；`agent/security` 覆盖率 **94%** |
| 8 | 【S2-02 #1】UI 身份层方案已裁定并落地；actor 不再仅靠 `ui:<remote_addr>` 降级 | ✅ | 裁定 A3：`agent/security/identity.py`；`TestTokenMapIdentityInUiAudit`（UI 写路由审计实测 `identity_source=token_map`） |
| 9 | 【S2-02 #11】认证 IP 的 PII 口径落地（掩码/哈希一致且可关联） | ✅ | 裁定 B：`agent/security/pii.py`；`TestIpPii`；UI 写路由链上**不再写 `remote_addr` 原文** |
| 10 | 【S2-03 #13】埋点 actor 归因口径与审批口径统一（`identity_source` 一致） | ✅ | 审计 / 埋点 / 审批三处共用 `identity.resolve_identity()`（`test_identity_audit_fields_unified`、`test_ui_middleware_uses_same_resolver`） |

---

## 二、交付物清单

### 2.1 新增模块（`agent/security/`，2532 行）

| 文件 | 行数 | 职责 |
|---|---|---|
| `agent/security/actor_matrix.py` | 571 | **§7.0 权限判定表**（后端唯一权威）+ `ActorType`/`decide()`（fail-closed） |
| `agent/security/identity.py` | 401 | **裁定 A3**：令牌 → 用户映射、`identity_source` 统一口径、可替换解析器 |
| `agent/security/pii.py` | 217 | **裁定 B**：IP 掩码 + HMAC-SHA256（无密钥显式降级，原文不落盘） |
| `agent/security/alerts.py` | 220 | 越权告警（结构化日志 + 计数 + 可注入外部通道 + 同源升级） |
| `agent/security/approval_guard.py` | 355 | 审批入口执行体校验 + 越权三件套（拒绝/审计/告警） |
| `agent/security/approval_session.py` | 424 | 会话绑定 / 链接时效 ≤900s / CSRF / destructive 二次认证 |
| `agent/security/governance_bridge.py` | 179 | S1-02 descriptor 桥接（risk / undo_hint / 补偿动作 / TaintBadge） |
| `agent/security/__init__.py` | 165 | 包公共面（92 个导出） |

### 2.2 新增 HTTP 审批面与前端

| 文件 | 行数 | 说明 |
|---|---|---|
| `agent/server_routes/routes_approval.py` | 411 | 9 条路由：`whoami` / `session`(POST,DELETE) / `pending` / `link`(POST,GET) / `second-factor` / `<id>/approve` / `<id>/reject` / `console` |
| `templates/approval_console.html` | 33 | 审批控制台宿主页（最小宿主，可挂任意页面） |
| `static/js/approval_console.js` | 324 | 审批按钮区：Shadow DOM 挂载、CSRF 头、链接/二次认证流程 |
| `static/css/approval_console.css` | 26 | 固定 zIndex + `isolation: isolate` + `contain` |

### 2.3 既有模块接线（行为向后兼容）

| 文件 | 改动 |
|---|---|
| `agent/skills_mgmt/approval.py` | `ApprovalRecord` 增 7 个身份/PII 字段；`submit/approve/reject/merge/mark_manual_executed` 增 `actor_ctx=`/`second_factor_ok=`；新增 `_guard()` 与 `ApprovalPermissionError`；审计载荷增身份/PII/治理字段，`record_id` 改走 `technical` 通道（保证可查） |
| `agent/audit/ui_middleware.py` | 身份解析统一到 `identity.resolve_identity`（零回归保留旧降级链）；审计载荷改为掩码 + HMAC，**删去 `remote_addr` 原文** |
| `agent/server_auth.py` | `require_token` 支持共享令牌 **+** 每使用者独立令牌；`resolve_request_identity()`；令牌校验后绑定审计上下文 |
| `agent/digestion/internalize.py` | `apply_manual_promote` 增 §7.0「强制推进 stage」human 专属校验（安全包不可用则回退既有行为） |
| `agent/server_routes/__init__.py` | 注册审批路由 |

### 2.4 新增单测（8 套件 2358 行 / **267** 例）

| 套件 | 例数 | 覆盖对象 |
|---|---|---|
| `tests/unit/test_security_actor_matrix.py` | 53 | §7.0 矩阵逐行、fail-closed、表驱动扩展 |
| `tests/unit/test_security_identity_pii.py` | 45 | 裁定 A3 + 裁定 B |
| `tests/unit/test_security_approval_guard.py` | 57 | 越权三件套、勿双写、真实链路审计 |
| `tests/unit/test_security_approval_session.py` | 43 | 会话绑定/时效/CSRF/二次认证 + 前端资产 |
| `tests/unit/test_approval_routes.py` | 32 | HTTP 审批面端到端 |
| `tests/unit/test_s4_01_stage_promote_chain.py` | 17 | **S3-03 真实 `stage.promote` 链路**（非 mock） |
| `tests/unit/test_s4_01_ui_identity_pii.py` | 6 | UI 写路由身份/PII 收口 |
| `tests/unit/test_s4_01_server_auth.py` | 18 | 认证层（共享/独立令牌、回退行为） |
| `tests/unit/conftest.py`（追加） | — | S4-01 隔离 fixtures（`agent.security` 进程级单例逐测试复位） |

---

## 三、Owner 两项裁定的落地证据

### 3.1 裁定 A3：令牌 → 用户映射（S2-02 #1 / S2-03 #13）

**配置项**

| 配置 | 语义 | 默认 |
|---|---|---|
| `CP_UI_TOKENS` | 映射表 `<token>:<name>[:<scope>[:<actor_type>]]`，逗号分隔；支持 `sha256:<hex>:<name>` 指纹写法（配置不必存令牌原文） | 空（= 回退既有行为） |
| `CP_UI_TOKENS_FILE` | 映射表文件路径（每行一条，`#` 注释） | 空 |
| `CP_IDENTITY_RESOLVER` | 解析器实现名（P5 → A1/A2 预留） | `token_map` |

**解析顺序**（`identity.IdentityResolver.resolve`）：映射表 → 显式 actor → 身份头 → Cookie → 令牌指纹 → `ui:<remote_addr>` → `ui:unknown`。

**命中/未命中口径**：命中 `identity_source=token_map`（`identity_tier=authoritative`）；未命中仍走既有降级链并标 `identity_degraded=true`（**不臆造用户名**）。

**证据**

```
$ python -m pytest tests/unit/test_security_identity_pii.py -q
45 passed
```

- 命中：`test_token_map_hit_is_authoritative`、`test_token_map_hit_from_bearer_header`
- 未命中：`test_token_map_miss_degrades_and_marks`（降级为令牌指纹且 `degraded=True`）
- **空表回退**：`test_empty_table_preserves_legacy_chain`（头/Cookie/指纹/remote_addr 五级逐字不变）
- 解析层可替换：`test_resolver_is_replaceable`（注入 A1 会话式解析器后上层零改动）
- UI 写路由收口：`test_s4_01_ui_identity_pii.py::test_mapped_token_yields_authoritative_actor`
- 口径统一：`test_identity_audit_fields_unified` / `test_ui_middleware_uses_same_resolver`

### 3.2 裁定 B：掩码 + HMAC 哈希（S2-02 #11）

**配置项**

| 配置 | 语义 | 默认 |
|---|---|---|
| `CP_IP_HMAC_KEY` | HMAC 密钥（十六进制或原始字节串），建议经 SecretStore/.env 注入 | 空 |
| `CP_IP_HMAC_KEY_FILE` | 密钥文件路径（0600） | 空 |
| `CP_IP_HMAC_AUTOGEN` | 是否在默认路径 `data/audit/ip_hmac_key` 自动生成 32 字节随机密钥 | `0`（**默认关闭**，避免落盘副作用） |

**入链字段（稳定契约）**：`actor_ip_masked`（`10.0.xxx.xxx`）、`actor_ip_hash`（HMAC-SHA256，**无密钥时不写该键**）、`actor_ip_hash_status`（`hmac_sha256` / `degraded_no_key` / `no_ip`）、`actor_ip_present`。**原始 IP 不落盘**。

**证据**

```
$ python -m pytest tests/unit/test_security_identity_pii.py -q -k "IpPii or Pii"
15 passed
```

- 掩码与仓库既有口径逐值一致：`test_mask_matches_repo_convention`（对账 `utils/sensitive_data_filter.mask_ip`）
- HMAC 非裸哈希：`test_hash_ip_is_hmac_not_bare_hash`（与 `sha256(ip)` 不同）
- 可关联：`test_same_ip_same_hash_allows_association` / `test_same_source_association`
- **无密钥显式降级**：`test_no_key_degrades_without_raw_ip`（不写空哈希、**不退化**为裸哈希、不含原文）
- 落盘实证：`test_raw_ip_never_persisted_in_jsonl`、`test_raw_ip_never_persisted_in_audit_chain`、`test_s4_01_ui_identity_pii.py::test_masked_and_hash_written_no_raw_ip`

**一条真实审计记录样例**（`stage.promote` 提交后链上 `approval.approved`，密钥经 `CP_IP_HMAC_KEY` 注入）：

```json
{
  "action": "approval.approved",
  "actor": "alice",
  "subject": "stage.promote:cp.builtin.read_file",
  "source": "agent",
  "record_id": "appr-20260912012941977998-86acb3e0",
  "status": "approved",
  "payload": {
    "actor_type": "human",
    "identity_source": "token_map",
    "actor_ip_masked": "10.0.xxx.xxx",
    "actor_ip_hash": "2b3e8ca643aacc4ab08e17d338d5370208fbaceb5c86a14d770eacf62f418e78",
    "actor_ip_hash_status": "hmac_sha256",
    "object_type": "stage.promote",
    "object_id": "cp.builtin.read_file",
    "level": "L1",
    "state": "approved",
    "trigger": "api",
    "manual_required": false,
    "undo_hint": "回滚 stage 至 shadow",
    "compensating_action": "执行 stage_migrate(shadow)",
    "undo_hint_status": "resolved",
    "reason": "人工核对④⑤⑥通过",
    "legacy": "approval_records.jsonl"
  }
}
```

> 说明：`record_id` 位于载荷**顶层**（经 `technical=` 通道写入）而非 `payload` 内层 ——
> 统一脱敏器的启发式会把 24 位关联键部分掩码（实测 `appr-202609********374219-...`），
> 导致「按 record_id 查链」失效。回归用例：`test_record_id_queryable_on_chain`。

---

## 四、越权三件套（拒绝 + 审计 + 告警）实证

| 越权路径 | 结果 | 证据 |
|---|---|---|
| auto(skill) 调 `approve` | 拒 + 审计 + 告警 | `test_auto_approve_rejected_with_audit_and_alert`（状态保持 `pending_review`） |
| auto / sub_agent 调 `reject` | 同上 | `test_all_non_human_approval_paths_rejected`（4 组参数化） |
| sub_agent 调 `merge` / `mark_manual_executed` | 同上 | `test_sub_agent_merge_and_archive_rejected` |
| sub_agent 读审批面板 | `403` + 拒绝判定 | `test_non_human_view_panel_denied_and_audited`、路由 `test_sub_agent_identity_cannot_open_panel` |
| auto / sub_agent 提交 `stage.promote` | 拒 | `test_auto_cannot_submit_stage_promote`、`test_s4_01_stage_promote_chain.py` |
| 非 human 强制推进 stage（应用侧） | 拒，stage 未迁移 | `test_non_human_cannot_apply_stage_promote`（`denied_by_matrix=True`） |

**审计唯一写入点（勿双写）**：`policy.denied` 事件由 `AUDIT_MIRROR_TYPES` 自动镜像入链；
`approval_guard.report_denial` **不调用** `audit.record()`，故一次越权只产生
**一条**链上记录（`test_deny_writes_single_policy_denied_audit_record`）。

**告警**：`[SECURITY][APPROVAL_VIOLATION]` 结构化 ERROR 日志 + 进程内计数（按 `actor_ip_hash`
聚合 ⇒「同一来源多次越权」分析）+ 可注入外部通道（`set_alert_sink`）+ 阈值升级
（`CP_SECURITY_ALERT_THRESHOLD` 默认 3 / 窗口 300s）。

**实现期修复的两处真实缺陷**（均带回归用例）：

1. **重复越权被幂等吞掉**：事件幂等键未含尝试序号时，同操作/同对象的第 2 次越权被判为重放，
   链上只剩第一条 ⇒ 加 `attempt_seq`（`test_repeated_violations_each_leave_one_audit_record`）。
2. **跨进程撞键**：`seq` 每进程从 1 重启，重启后第一条越权与上一进程首条撞键被丢弃
   ⇒ 幂等键加入**进程命名空间**（pid + 随机盐，`denial_event_key`）；
   `test_denial_key_resists_cross_process_collision`。

---

## 五、`stage.promote` 真实链路（非 mock）

全程调用 S3-03 真实 API：`InternalizeEngine.manual_promote` → `confirm_manual_promote`
→ `apply_manual_promote`（`tests/unit/test_s4_01_stage_promote_chain.py`，17 例）。

| 要求（任务书步骤 2） | 证据 |
|---|---|
| `stage.promote` 审批属 human 专属 | `TestStagePromoteHumanOnly`（auto / sub_agent 各 2 例） |
| `undo_hint` / 补偿动作可回溯 | `test_governance_fields_traceable_after_approval`；未解析时如实标 `undo_hint_status` |
| 同时落 S2-02 链式审计 + S2-03 `record_approval` 事件 | `test_approval_lands_chain_audit_and_events`（链上 `approval.submit`/`approval.approved`；事件 `approval.required`/`approval`/`intervention`） |
| **勿双写** | `test_no_double_write_of_approval_lifecycle`（`approved` 恰 1 条） |
| human 全路径生效 | `test_submit_approve_apply_migrates_stage`（`applied=True` 且 registry 收到 stage 写回） |
| ④⑤⑥ 一票否决不受影响 | `test_quality_gate_still_blocks_manual_channel` |
| 机制失败回退既有行为 | `test_security_package_failure_falls_back` |

---

## 六、质量证据

| 项 | 结果 |
|---|---|
| 新增单测 | **267 例**全绿（8 套件） |
| 新增模块覆盖率 | `agent/security` **94%**（actor_matrix 98 / identity 97 / pii 86 / alerts 89 / guard 94 / session 97 / bridge 85）；`routes_approval.py` **91%**；`server_auth.py` **94%** |
| 邻接回归 | 419 passed / 0 failed（`test_audit_*` / `test_s2_03_integration` / `test_skills_mgmt*` / `test_hitl` / `test_digestion_internalize` / `test_digestion_shadow`） |
| 越权用例 | 覆盖 6 类越权路径（见 §四） |
| kwarg 冲突扫描 | `--path agent --min-risk HIGH` → 0 处；`--path tests --min-risk HIGH` → 0 处 |
| mypy | 改动/新增模块 **0 error**（仓库既有 1216 项存量报错见 §八） |
| import-linter | **2 kept / 0 broken** |
| 前端 | `node --check static/js/approval_console.js` 通过；DOM 隔离/CSRF 由 `TestFrontendIsolation` 断言 |
| 落盘纪律 | 全套件显式传 tmp 路径或 autouse 隔离；`test_no_runtime_pollution` 断言无运行时目录写入 |

---

## 七、口径裁定与边界（明确「不做什么」）

1. **审计三条记录的语义分工（刻意不合并、不双写）**
   - `ui.routes_approval_approve.post`：**访问/尝试**语义（全局 UI 写路由包装，S2-02，
     含被拒请求）——对应 S2-02 遗留 #10 的口径裁定：端点访问≠状态变更，二者分列；
   - `approval.approved`：**状态变更**语义，由审批域唯一产出；
   - `policy.denied`：**越权**语义（事件镜像）。
   故审批路由**不使用** `@audit_action`（会再产生一条语义重复记录）。
2. **不做**：完整 session 登录体系（A1）、可信反代头（A2）——解析层已留替换点（P5）。
3. **不做**：`sub_agent` 授权清单本体（S4-04）——本任务只交付**判定语义与接口形状**
   （`ActorContext.authorized_capabilities` + `SCOPE_AUTHORIZED_SUBSET`）。
4. **不做**：审批 HTTP 面的「应用 promote」端点（S3-03 的 `apply_manual_promote` 仍由
   服务层/CLI 驱动）；矩阵对应用侧的 human 专属校验已在其入口生效。
5. **不改**：`app_server.py` 内既有的 `require_token` 副本（与 `agent/server_auth.py` 同源）。
   审批面与 `agent/server_routes/*` 走新实现；该副本的去重列入遗留 #2。
6. **`record_id` 通道**：审批链上关联键经 `technical=` 写入（不脱敏），避免被启发式掩码。

---

## 八、遗留问题（逐条带归属与阻塞性判定）

| # | 遗留 | 归属 | 阻塞性 |
|---|---|---|---|
| 1 | `my_agent` 侧 webhook 未接线：越权告警默认只到结构化日志 + 进程内计数 + 可注入 sink；生产级通知（AlertManager/webhook/Slack）需运维接线 `set_alert_sink` | S4-03 / 运维 | 不阻塞（机制与接口已就绪） |
| 2 | `app_server.py::require_token` 仍为历史副本（未走 `server_auth` 新实现，故其路由不识别每使用者令牌） | 后续清理（S6-01 或专项） | 不阻塞（审批面与 server_routes 均已走新实现；副本与旧行为逐字一致） |
| 3 | 仓库既有 mypy 存量报错 **1216 项 / 172 文件**（含 `agent/env_config_manager.py`、`network_config.py`、`agent/digestion/internalize.py:984`）；本任务改动文件 0 新增 | 各模块归属（存量） | 不阻塞（CI 未以 mypy 全绿为门） |
| 4 | `agent/descriptors` registry 未在审批路径默认加载：`install_descriptor_resolvers()` 由路由首次请求时接线；纯服务层调用（不经路由）时风险按「未知」处理（**不臆造 destructive**） | S4-04（授权/风险清单齐备后统一接线） | 不阻塞（如实标注，不静默降级放行） |
| 5 | 前端审批区落在 Flask 侧（`templates/`+`static/`，与既有 health/log/observability 看板同构），未嵌入 `yunshu-ui` React 外壳 | S6-01（六面板扩展，届时统一挂载） | 不阻塞（组件为 Shadow DOM 自治单元，可被任意宿主挂载） |
| 6 | 审批会话/链接/确认码仅进程内（重启即失效，属**有意**的 fail-closed 设计），多副本部署需粘性会话或外置存储 | P5 生产化 | 不阻塞（单机形态下语义正确） |

---

## 九、验收命令速查

```powershell
# 本任务全部单测
python -m pytest tests/unit/test_security_*.py tests/unit/test_approval_routes.py `
                 tests/unit/test_s4_01_*.py -q -p no:randomly

# 覆盖率（新增模块）
python -m pytest tests/unit/test_security_*.py tests/unit/test_approval_routes.py `
                 tests/unit/test_s4_01_*.py -q -p no:randomly `
       --cov=agent/security --cov=agent.server_routes.routes_approval `
       --cov=agent.server_auth --cov-report=term-missing

# 邻接回归
python -m pytest tests/unit/test_audit_ui_middleware.py tests/unit/test_audit_integration.py `
                 tests/unit/test_s2_03_integration.py tests/unit/test_skills_mgmt_safety.py `
                 tests/unit/test_hitl.py tests/unit/test_digestion_internalize.py `
                 tests/unit/test_digestion_shadow.py tests/unit/test_skills_mgmt.py -q -p no:randomly

# 门禁
python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH
python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH
lint-imports --config .importlinter
python -m mypy agent/security/ agent/skills_mgmt/approval.py agent/audit/ui_middleware.py `
              agent/server_auth.py agent/server_routes/routes_approval.py
node --check static/js/approval_console.js
```
