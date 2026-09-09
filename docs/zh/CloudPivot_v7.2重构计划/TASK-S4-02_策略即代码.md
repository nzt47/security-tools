# TASK-S4-02 策略即代码（OPA 或等效实现 + 策略模拟器）

> 所属阶段：S4 治理与安全｜依赖：S2-02（审计）、S2-03（policy.denied 事件）｜预估：5–8 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §5.6（OPA/Rego + 决策缓存 p99<5ms + 收件箱只收例外）/P7.1-20（OPA 只出决策，egress guard 执行）/P7.2-19（策略模拟器）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

把 v7.2 **策略即代码**落到云枢既有权限/护栏设施上（P4 修正：单机不强制引入 OPA WASM，采用**等效声明式策略引擎**，保留未来换 OPA 的接口形状）：

1. **Policy schema**（§3.11 Policy）：`{id, version, owner, effect: allow|deny|ask, match, message_template, effective_range, break_glass_ttl_min, signature}`；match 使用 OPA 子集语义的等效判定器。
2. **决策引擎**：`check(policy_ctx) -> {allow|deny|ask, policy_id}`；决策缓存 p99 <5ms；决策入审计与 policy.decision 埋点（S2-03）。
3. **职责边界（P7.1-20）**：OPA/策略引擎**只出决策不做网络动作**；数据出域由 egress guard（进程外网络代理/网络白名单）强制执行——云枢侧落地为"策略决策 + 执行点分离"的架构（决策在 policy 引擎，执行在网络层/权限网关）。LLM 禁止在 Rego 中生成 http.send（云枢等效引擎同样禁止网络副作用）。
4. **收件箱只收例外**（§5.6）：未被策略覆盖/需要 break-glass 的例外进人工收件箱（复用 hitl/takeover_queue）。
5. **策略模拟器（P7.2-19）**：对历史 PolicyDecision 重放候选新策略，报告 deny→allow 变更数与高危命中清单；策略变更 PR 必附模拟报告。

## 二、执行步骤

### 步骤 1：现状盘点与接口形状
- 盘点云枢现有权限/护栏：`agent/guardrails/`（input_guard/output_guard/output_schema）、RBAC/权限路由（`permission_arch.md`、skills 权限）、`agent/network/`（若有出域/代理）、`secure_config`。
- 定义 `PolicyEngine` 接口（check/load/validate/audit）——形状兼容未来 OPA WASM 替换（match 表达式抽象层）。

### 步骤 2：策略模型与引擎
- 新增 `agent/policy/`：`Policy`（schema 对齐 §3.11）、`PolicyStore`（版本化 + effective_range + 签名校验）、`PolicyEngine.check(policy_ctx)`（遍历生效策略，首个命中 effect 生效；决策缓存 LRU，p99<5ms）。
- match 表达式：实现 OPA 子集的等效判定（input.capability/tenant/actor 字段路径比较 + 集合成员 + 布尔组合）——文档化支持子集与不支持语法。
- 出域决策：data_class=secret 且目标外部 → deny（决策层）；实际网络执行由 egress guard/网络白名单强制（落地清单见步骤 4）。

### 步骤 3：策略模拟器
- `simulate(policy_candidate, since_days=7)`：重放历史 policy.decision 记录（S2-03 数据），输出 deny→allow 变更数、高危命中清单（原 deny 现 allow 的条目）、影响面报告。
- 集成到策略变更流程：变更 PR 模板要求附模拟报告（对齐 P7.2-19 合入门禁）。

### 步骤 4：执行点分离接线（P7.1-20 落地）
- 明确"决策 → 执行"两层：策略引擎只输出 {allow|deny|ask, policy_id}；执行由既有权限网关/网络层落实。落地至少一个真实执行点接线：工具/技能执行的权限检查（PermissionGateway 或等价）改走 PolicyEngine + 执行点双确认。
- 出域链路：盘点现有外发路径（web 工具/HTTP/MCP 外部端点），确认 secret 数据出域被 deny（单测覆盖"读本地密钥→外发 HTTP"链路监测，§5.7 机制 4）。

### 步骤 5：回归与归档
- 回归：guardrails/权限/审批相关套件零回归；新增单测（策略加载/版本/决策缓存/模拟器/出域拒绝/LLM 无网络副作用）≥40 例、覆盖率 ≥80%。
- 撰写 `TASK-S4-02_验收报告.md`（含策略模拟器样例报告）。

## 三、预期成果

1. `agent/policy/`：Policy/PolicyStore/PolicyEngine（接口兼容 OPA 替换）+ match 子集文档。
2. 决策缓存（p99<5ms）+ policy.decision 审计埋点。
3. 策略模拟器 + 变更 PR 合入门禁模板。
4. 至少一个真实执行点的"决策-执行"分离接线 + secret 出域拒绝链路。
5. `TASK-S4-02_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] Policy schema 对齐 §3.11；版本化 + effective_range + 签名生效
- [ ] PolicyEngine.check 返回 {allow|deny|ask, policy_id}；决策缓存 p99<5ms（实测）
- [ ] 决策入链式审计 + policy.decision 埋点（与 S2-02/S2-03 联动）
- [ ] secret 出域 → deny（决策层用例 + 执行层强制用例：外发被拦）
- [ ] LLM 无法在策略中写网络副作用（无 http.send 类能力；引擎纯决策）
- [ ] 模拟器可对历史决策重放并输出 deny→allow 变更 + 高危清单；PR 门禁检查存在
- [ ] 既有 guardrails/权限/审批套件零回归；新增单测全绿、覆盖率 ≥80%
