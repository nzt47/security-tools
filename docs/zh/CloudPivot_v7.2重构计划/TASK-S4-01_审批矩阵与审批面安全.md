# TASK-S4-01 审批 Actor 矩阵与审批面安全（对齐 §7.0 / §5.7⑦）

> 所属阶段：S4 治理与安全｜依赖：S2-02（审计）、S2-03（approval 事件）｜预估：3–5 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §7.0（Actor 权限矩阵★）/§5.7⑦（审批面安全：会话绑定/CSRF/链接时效/destructive 二次认证/越权告警）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

把 v7.2 **Actor 权限矩阵（§7.0）与审批面安全（§5.7⑦）**落到云枢现有审批设施（`agent/skills_mgmt/approval.py` ApprovalFlow、`agent/human_in_the_loop/`）之上，统一 human/auto(skill)/sub_agent 三类执行体的权限边界，并补齐审批面安全细节：

**Actor 矩阵核心行（§7.0，后端同一张表校验——前端不拥有额外权限）：**

| 操作 | human | auto(skill) | sub_agent |
|---|---|---|---|
| 查看轨迹/记忆/面板 | ✅ | 仅自身 scope | ❌ |
| 审批 Approve/Deny | ✅ | ❌ | ❌ |
| 切换熔炉/修改策略 | ✅（二次认证/RFC） | ❌ | ❌ |
| 强制推进 stage / 摘除来源 | ✅（reason 必填+审计） | ❌ | ❌ |
| 执行 capability | ✅ | ✅ scope 内 | ✅ 授权子集 |
| 写入记忆 | ✅ | 仅工作记忆 | ❌ |

**审批面安全（§5.7⑦）：** 会话绑定（审批 token 与登录会话强绑定，禁链接分享）、CSRF 保护、审批链接时效 ≤15 分钟、越权尝试→告警+审计、destructive 强制二次认证、审批按钮区 DOM 隔离（前端）。

## 二、执行步骤

### 步骤 1：现状盘点（含 S2 移交的裁定项）
- 盘点 ApprovalFlow（submit/approve/reject/merge/mark_manual_executed/route_level）与 human_in_the_loop（hitl/ethics/takeover_queue）现有 actor 字段与权限校验点。
- 盘点谁在调用审批：skill 发布/审批、EVO 受控编辑、S3-03 手动 promote、未来熔炉切换；确认各调用方的 actor 类型标注。
- **【S2-02 遗留 #1 / S2-03 遗留 #13 —— 身份层方案：Owner 已裁定 2026-09-11，采用方案 A3「令牌 → 用户映射」】** 现状无 session/current_user 体系：`require_token` 仅共享令牌，UI actor 为"头/Cookie/令牌指纹/`ui:<remote_addr>`"降级，`identity_source` 已如实标注。
  - **裁定内容**：采用 **A3 令牌映射**——每个使用者分配独立令牌，配置表映射 token → actor 名（如 `CP_UI_TOKENS=<token>:<name>,...` 或等价配置项，密钥/令牌经 SecretStore 管理）；actor 解析顺序为「会话/映射表 → 头 → 降级 `ui:<addr>`」，命中映射表时 `identity_source=token_map`，未命中仍降级并标注 `degraded`。
  - **不做**（本次）：不引入完整 session 登录体系（A1）、不信任反代注入头（A2）——但 **actor 解析层须保持可替换**（S2-02 已抽象），以便企业侧（P5）平滑升级为 A1/A2。
  - **落地**：本任务内实现映射表解析 + 单测（命中/未命中/多令牌/空配置回退），并确保 §7.0 矩阵 human 行依据该 actor 判定。
- **【S2-02 遗留 #11 —— PII 口径：Owner 已裁定 2026-09-11，采用「掩码 + HMAC 哈希」】** 审计链永久保留（删不得），真实 IP 入链等于永久留存 PII。
  - **裁定内容**：入链两个字段——`actor_ip_masked`（沿用仓库既有脱敏口径 `10.0.0.7` → `10.0.xxx.xxx`）+ `actor_ip_hash`（HMAC-SHA256，密钥存 SecretStore，不可还原）；**原始 IP 不落盘**。保留关联能力（同一 IP → 同哈希，可做"同一来源多次越权"分析）。
  - **不做**：不写原文入链（方案②）；不用无密钥裸哈希（可被枚举反查）。

### 步骤 2：Actor 矩阵落地（后端单表校验）
- 定义 `ActorType`（human/auto(skill)/sub_agent）+ 权限判定表（对齐 §7.0 矩阵行）；在 ApprovalFlow/审批入口加 actor 校验：auto/sub_agent 调用 approve/reject → 拒绝 + 审计（越权告警事件）。
- human 操作记录 actor=登录用户（会话绑定，来源遵循步骤 1 裁定）；审批记录经 S2-02 已交付的统一门面入链：`agent/audit/facade.py::audit.record(action, actor, subject, payload...)`（治理类事件已建立镜像规则，避免重复留痕）；审批事件经 S2-03 的 `agent/observability/acr.py::record_approval(...)` 与 `events.py` 落账。
- sub_agent 执行 capability 仅授权子集：对接 S4-04（subagent 真实现工具裁剪）的授权清单。

### 步骤 3：审批面安全
- 会话绑定：审批 token 与登录会话强绑定（禁分享式链接）；超时/换会话 → 失效（依赖步骤 1 身份层裁定）。
- 审批链接时效 ≤15 分钟（可配）；过期 → 提示重新发起。
- destructive 二次认证：risk=destructive 的审批要求二次认证（输入口令/确认码），复用 `server_auth.py`/会话校验设施。
- 越权尝试 → 审计告警（对齐既有 alert 设施）+ 事件 policy.denied/越权事件（经 S2-03 `events.py::emit()`）。
- 前端：审批按钮区 DOM 隔离固定 zIndex、CSRF 头携带、TaintBadge（若外来内容进审批上下文）。

### 步骤 4：回归与归档
- 回归：skills approval/EVO/human_in_the_loop 套件零回归；新增单测（矩阵行判定、auto/sub_agent 越权拒绝、会话绑定、时效、二次认证、越权告警、身份来源标注）≥30 例、覆盖率 ≥80%。
- 撰写 `TASK-S4-01_验收报告.md`（含身份层裁定结论与 PII 口径记录）。

## 三、预期成果

1. ActorType + 权限判定表（后端单表，对齐 §7.0）。
2. 审批入口 actor 校验 + 越权告警审计。
3. 审批面安全（会话绑定/时效/二次认证/前端 DOM 隔离）。
4. **S2 遗留在本任务内的收口**：UI 身份层方案落地（#1）、认证 IP PII 口径落地（#11）、埋点 actor 归因口径统一（S2-03 #13）。
5. `TASK-S4-01_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] §7.0 矩阵核心行全部可判定（后端表驱动，前端不拥有额外权限）
- [ ] auto/skill 与 sub_agent 调审批 → 拒绝 + 审计 + 告警（越权用例）
- [ ] human 审批记录 actor=登录会话；记录入链式审计与 approval 事件
- [ ] 审批 token 会话绑定（分享式链接失效用例）；链接时效 ≤15min 生效
- [ ] destructive 审批触发二次认证（无二次认证不可通过）
- [ ] 前端审批按钮区 DOM 隔离 + CSRF 保护验证
- [ ] 既有 approval/EVO/hitl 套件零回归；新增单测全绿、覆盖率 ≥80%
- [ ] **【S2-02 #1】** UI 身份层方案已裁定并落地；actor 不再仅靠 `ui:<remote_addr>` 降级（或降级路径显式声明为最终态）
- [ ] **【S2-02 #11】** 认证 IP 的 PII 口径落地（掩码/哈希策略一致且可关联）
- [ ] **【S2-03 #13】** 埋点 actor 归因口径与审批口径统一（`identity_source` 一致）
