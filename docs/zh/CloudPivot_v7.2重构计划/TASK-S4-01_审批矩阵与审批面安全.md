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

### 步骤 1：现状盘点
- 盘点 ApprovalFlow（submit/approve/reject/merge/mark_manual_executed/route_level）与 human_in_the_loop（hitl/ethics/takeover_queue）现有 actor 字段与权限校验点。
- 盘点谁在调用审批：skill 发布/审批、EVO 受控编辑、S3-03 手动 promote、未来熔炉切换；确认各调用方的 actor 类型标注。

### 步骤 2：Actor 矩阵落地（后端单表校验）
- 定义 `ActorType`（human/auto(skill)/sub_agent）+ 权限判定表（对齐 §7.0 矩阵行）；在 ApprovalFlow/审批入口加 actor 校验：auto/sub_agent 调用 approve/reject → 拒绝 + 审计（越权告警事件）。
- human 操作记录 actor=登录用户（会话绑定）；审批记录入 S2-02 链式审计（approval.granted 等 action）与 S2-03 事件（approval.required）。
- sub_agent 执行 capability 仅授权子集：对接 S4-04（subagent 真实现工具裁剪）的授权清单。

### 步骤 3：审批面安全
- 会话绑定：审批 token 与登录会话强绑定（禁分享式链接）；超时/换会话 → 失效。
- 审批链接时效 ≤15 分钟（可配）；过期 → 提示重新发起。
- destructive 二次认证：risk=destructive 的审批要求二次认证（输入口令/确认码），复用 `server_auth.py`/会话校验设施。
- 越权尝试 → 审计告警（对齐既有 alert 设施）+ 事件 policy.denied/越权事件。
- 前端：审批按钮区 DOM 隔离固定 zIndex、CSRF 头携带、TaintBadge（若外来内容进审批上下文）。

### 步骤 4：回归与归档
- 回归：skills approval/EVO/human_in_the_loop 套件零回归；新增单测（矩阵行判定、auto/sub_agent 越权拒绝、会话绑定、时效、二次认证、越权告警）≥30 例、覆盖率 ≥80%。
- 撰写 `TASK-S4-01_验收报告.md`。

## 三、预期成果

1. ActorType + 权限判定表（后端单表，对齐 §7.0）。
2. 审批入口 actor 校验 + 越权告警审计。
3. 审批面安全（会话绑定/时效/二次认证/前端 DOM 隔离）。
4. `TASK-S4-01_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] §7.0 矩阵核心行全部可判定（后端表驱动，前端不拥有额外权限）
- [ ] auto/skill 与 sub_agent 调审批 → 拒绝 + 审计 + 告警（越权用例）
- [ ] human 审批记录 actor=登录会话；记录入链式审计与 approval 事件
- [ ] 审批 token 会话绑定（分享式链接失效用例）；链接时效 ≤15min 生效
- [ ] destructive 审批触发二次认证（无二次认证不可通过）
- [ ] 前端审批按钮区 DOM 隔离 + CSRF 保护验证
- [ ] 既有 approval/EVO/hitl 套件零回归；新增单测全绿、覆盖率 ≥80%
