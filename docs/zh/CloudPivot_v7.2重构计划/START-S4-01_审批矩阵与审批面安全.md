# START-S4-01 审批矩阵与审批面安全（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S4-01_审批矩阵与审批面安全.md`](TASK-S4-01_审批矩阵与审批面安全.md)（★ 执行会话必须完整阅读）
> 批次与通用约定 → [`PARALLEL_并行铺开总表.md`](PARALLEL_并行铺开总表.md)
> 基线：`master` / `15eae00d`｜波次：**第一波**｜预估：3–5 人日
> ⚠ **S4 轨枢纽**：S4-03 与 S4-04 均依赖本任务交付的 Actor 矩阵 / 授权语义 → 尽早结案可解锁第二波。

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s401`**。
2. **两项开工前裁定已由 Owner 关闭（2026-09-11）**，任务书步骤 1 已写入结论，直接执行即可：
   - **A 身份层 → 令牌 → 用户映射（A3）**：每使用者独立令牌，配置表映射 actor；解析顺序「会话/映射表 → 头 → 降级 `ui:<addr>`」；不做 session 登录体系、不信任反代头，但解析层须可替换（P5 可升级 A1/A2）。
   - **B PII 口径 → 掩码 + HMAC 哈希**：入链 `actor_ip_masked`（沿用 `10.0.xxx.xxx`）+ `actor_ip_hash`（HMAC-SHA256，密钥入 SecretStore、不可还原）；**原始 IP 不落盘**。
3. 本任务是 S4-03 / S4-04 的前置；若它们已并行开工，本任务应尽早给出矩阵与授权清单的**稳定接口形状**。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S4-01 — 审批 Actor 矩阵与审批面安全（对齐 v7.2 §7.0 / §5.7⑦）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S4-01_审批矩阵与审批面安全.md（★ 必须先完整阅读，尤其步骤 1 的两项已定裁定）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_并行铺开总表.md
【所属计划】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\00_总览_审计结论与重构总计划.md
【上游设计文档】C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md
                （相关章节：§7.0 Actor 权限矩阵 / §5.7⑦ 审批面安全 / P7.2-24 审计平权）
【预估】3–5 人日
【状态】Owner 裁定 A/B 已关闭；依赖 S2-02/S2-03 已结案

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s401 --base master
此后所有 git 操作在 .worktrees/s401/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master

━━━ 二、任务目标（摘要）━━━
1. **Actor 权限矩阵落地（§7.0）**：ActorType = human / auto(skill) / sub_agent；**后端单表校验，前端不拥有额外权限**。
   关键行：审批 Approve/Deny 仅 human；查看轨迹/记忆 auto 仅自身 scope、sub_agent ❌；执行 capability auto 限 scope、sub_agent 限授权子集；
   写入记忆 auto 仅工作记忆、sub_agent ❌；切换熔炉/改策略/强制推进 stage 仅 human（二次认证/reason 必填+审计）
2. **审批面安全（§5.7⑦）**：审批 token 与登录会话强绑定（禁分享式链接）、CSRF 保护、链接时效 ≤15 分钟、
   越权尝试 → 告警 + 审计、destructive 强制二次认证、前端审批按钮区 DOM 隔离
3. **身份层落地（裁定 A3）**：令牌 → 用户映射表解析；命中标 `identity_source=token_map`，未命中降级并标 `degraded`
4. **PII 口径落地（裁定 B）**：掩码 + HMAC 哈希双字段入链，原始 IP 不落盘

━━━ 三、已就绪前置：勿重复实现 ━━━
  • 审批设施：agent/skills_mgmt/approval.py（ApprovalFlow: submit/approve/reject/merge/route_level/submit 分级）、
              agent/human_in_the_loop/（hitl / ethics / takeover_queue）
  • 审计与事件（S2 交付）：agent/audit/facade.py::audit.record(action, actor, subject, payload...)（治理类事件已建镜像规则，勿重复留痕）
              agent/observability/acr.py::record_approval(...)　agent/observability/events.py::emit()
  • 认证设施：agent/server_auth.py（现有共享令牌校验，改造起点）
  • **S3-03 已交付的真实业务链路**：object_type=**stage.promote**（ManualPromoteRequest / ManualReviewQueue，
              复用 skills_mgmt.approval 的 L2 分级语义）→ 直接作为矩阵落地与 human 专属的**现成验收用例**，勿造 mock

━━━ 四、必须接收的移交遗留 ━━━
  • S2-02 #1 UI 身份层缺失（现为 `ui:<remote_addr>` 降级；`require_token` 仅共享令牌）→ 由裁定 A3 落地
  • S2-02 #11 认证 IP 的 PII 口径 → 由裁定 B 落地
  • S2-03 #13 埋点 actor 归因沿用降级口径 → 与本任务**统一口径**（`identity_source` 一致）
  • S2-02 #3 审批无 HTTP 路由（仅服务层网关）→ 新增路由会自动被全局审计包装，注意勿双写
  • S2-02 #10 端点访问日志（AccessLogger）语义为"访问"非"状态变更" → 需先裁定口径再并入

━━━ 五、本任务特有硬约束 ━━━
1. **后端单表校验是唯一权威**：前端不可拥有额外权限；矩阵判定必须表驱动（加行不改逻辑）
2. 越权路径（auto/sub_agent 调 approve/reject、sub_agent 读审批、非 human 强制推进 stage）**必须被拒 + 审计 + 告警**
3. 认证 IP 原始值不得落盘（HMAC 密钥经 SecretStore；无密钥时明确降级并标注）
4. 令牌映射表为空时**回退既有行为**（不得因新机制导致后台无法审批）

━━━ 六、验收与交付物 ━━━
交付：1) ActorType + 权限判定表（后端表驱动）；2) 审批入口 actor 校验 + 越权告警审计；
      3) 审批面安全（会话绑定/时效/二次认证/DOM 隔离/CSRF）；4) 身份层映射解析（裁定 A3）+ PII 双字段（裁定 B）；
      5) TASK-S4-01_验收报告.md；6) S4-01_交付结案报告_<日期>.md；7) 更新 00_总览 状态行
验收：逐条对照任务书 §四（含 S2-02 #1/#11、S2-03 #13 收口项与 stage.promote 真实链路用例）

━━━ 七、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_approval*.py tests/unit/test_hitl*.py tests/unit/test_skill_approval*.py（按实际）
  • 邻接回归：pytest tests/unit/test_skills_mgmt.py + 审计/事件邻接
  • 全量抽查：pytest -m "not slow" -p no:randomly
  • kwarg 扫描两条：--path agent --min-risk HIGH；--path tests --min-risk HIGH
  • mypy 新增/改动模块 + 既有阻塞模块；importlinter lint --config .importlinter
  • 若改前端审批区：tsc -b --noEmit + eslint 零告警 + vitest 相关用例
  • 真实提交场景验证 pre-commit；跑完门禁后检查 git status 产物漂移

━━━ 八、上游已知坑 ━━━
1. 审批相关用例若落盘，**必须显式传路径或 autouse 隔离**（否则污染运行时目录，S3-02/S3-03 两次教训）
2. 审计写入有镜像规则：治理类事件已入链，**勿在审批路径重复留痕**（会产生双份记录）
3. 认证类改动注意既有 RBAC 结案产物（permission_arch / 权限路由），勿与其冲突
4. 前端审批按钮区 DOM 隔离与 zIndex 需与既有布局协调，避免遮挡类回归

━━━ 九、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：身份层与 PII 两项裁定的落地证据（配置项 + 单测 + 一条真实审计记录样例）
```

---

## 三、开工自查清单

- [ ] 已读任务书，确认裁定 A3 / B 的落地口径
- [ ] `--base master`、id=`s401`；worktree 内工作
- [ ] 矩阵表驱动；auto/sub_agent 越权路径全部被拒 + 审计 + 告警
- [ ] `stage.promote` 真实链路作为验收用例（非 mock）
- [ ] 原始 IP 未落盘；掩码 + HMAC 双字段入链
- [ ] 令牌映射为空时回退既有行为
- [ ] 未与 S2-02 审计镜像规则重复留痕
- [ ] 本地门禁全绿（含双路径 kwarg 扫描）；覆盖率 ≥80%
- [ ] 双远端同点推送
