# TASK-S5-01 记忆四层 + 租户隔离 / 遗忘（对齐 §4.3 / P7.2-08）

> 所属阶段：S5 记忆与评测｜依赖：S2-01（TraceContext tenant/workspace）｜预估：4–6 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §4.3（记忆四层 + TTL + 遗忘三触发）/P7.2-08（IDE 租户映射与记忆隔离）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

把 v7.2 **记忆四层模型与租户隔离（P7.2-08）**落到云枢现有记忆/知识设施上（`agent/memory/` 黑匣子/滚动摘要、`agent/knowledge/` 卡片/检索——复用为主，补齐"事实/偏好/策略"分层与隔离语义）：

1. **四层记忆**：工作（working）/ 事实（fact）/ 偏好（preference）/ 策略（strategy）+ TTL；现有 MemoryEntry 形态（§3.11：type/content_redacted/scope/source_task_id/confidence/ttl_expires_at/forget_candidate）。
2. **租户映射（P7.2-08）**：workspace(repository) = 逻辑租户（tenant_id = workspace-hash，来源 S2-01 TraceContext）；subject_id = 登录用户。事实/策略按租户隔离；偏好跟随 subject 跨租户携带；**个人偏好绝不污染企业策略记忆**（企业策略记忆 org 级只读下发）。
3. **遗忘三触发**（§4.3）：成功率 30 天 < 基线×0.7 / 来源失效 / 删除权；快照留 30 天；"做梦"= 低频 + 只读快照 + 产出 PR（本任务只落遗忘与隔离；做梦聚合入 S5-02/后续）。
4. **被遗忘权（§8/§10）**：记忆物理删除、审计标识符匿名化——删的是记忆不是证据（审计链 S2-02 保留）。

## 二、执行步骤

### 步骤 1：现状盘点与分层映射
- 盘点 memory（工作/滚动摘要/黑匣子）、knowledge（卡片/双链）、`memory_abstractor`、preference 类存储现状；确认哪些已带 tenant/workspace/subject 字段。
- 输出 `记忆分层映射表`（v7.2 四层 ↔ 云枢现有载体 ↔ 缺口）。

### 步骤 2：四层模型与租户隔离落地
- 扩展记忆条目模型（对齐 §3.11 MemoryEntry）：type(fact|preference|strategy|working)、tenant_id/workspace_id、subject_id、scope(project:<hash>|global)、ttl_expires_at、forget_candidate。
- 隔离规则实现（P7.2-08）：
  - 事实/策略记忆写入与召回均限定 tenant_id（workspace-hash）；
  - 偏好记忆跟随 subject_id 跨租户携带（global 偏好 + project 事实召回优先级：project 事实 > global 偏好，同级新者胜——§4.3）；
  - 企业策略记忆（若启用）org 级只读下发，禁止个人偏好写入策略层。
- 写入 API 强制 tenancy（缺 workspace/tenant 字段拒绝或显式降级，联动 S2-01 不变量）。

### 步骤 3：遗忘与 TTL
- 遗忘三触发落地：① 成功率 30 天 < 基线×0.7（数据源：S2-01 台账 quality）② 来源失效（descriptor deprecated/来源摘除）③ 删除权（用户显式删除/被遗忘权）。
- 遗忘执行：条目标记 forget_candidate → 快照留 30 天 → 物理删除 + 审计匿名化（记忆删、证据留）。
- TTL：ttl_expires_at 到期自动降级候选（工作记忆短 TTL、事实/策略长 TTL 可配）。

### 步骤 4：回归与归档
- 回归：memory/knowledge/skills_memory_abstractor 相关套件零回归；新增单测（四层类型、租户隔离矩阵——同租户可见/跨租户不可见/偏好跨租户跟随/策略不可污染、TTL、三触发遗忘、删除权匿名化）≥40 例、覆盖率 ≥80%。
- 撰写 `TASK-S5-01_验收报告.md`。

## 三、预期成果

1. `记忆分层映射表` + 四层模型落地（§3.11 MemoryEntry 对齐）。
2. 租户隔离（workspace-hash 逻辑租户 + 偏好随 subject 跨租户 + 策略只读下发）。
3. 遗忘三触发 + TTL + 快照 30 天 + 删除权匿名化。
4. `TASK-S5-01_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] 记忆条目模型对齐 §3.11（type/tenant/subject/scope/ttl/forget_candidate）
- [ ] 租户隔离矩阵单测通过：同租户事实/策略可见、跨租户不可见
- [ ] 偏好记忆跨租户跟随 subject（携带验证）；不污染策略层（写入被拒用例）
- [ ] 企业策略记忆只读下发（启用时个人不可写）
- [ ] 遗忘三触发各自有触发用例；快照留 30 天；删除后审计匿名化但链保留
- [ ] TTL 到期自动降级候选生效
- [ ] 既有 memory/knowledge 套件零回归；新增单测全绿、覆盖率 ≥80%
