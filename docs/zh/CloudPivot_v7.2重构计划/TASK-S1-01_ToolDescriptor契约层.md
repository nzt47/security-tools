# TASK-S1-01 ToolDescriptor v2.1 契约层（模型 + 校验器 + Registry）

> 所属阶段：S1 契约层｜依赖：S0-01、S0-02｜预估：5–8 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §2.6/§3.2/§3.3.1（审计缺口 G1，见 01 号审计报告 §6 矩阵 S1 行）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

在云枢中落地 **ToolDescriptor v2.1 契约模型 + 校验器 + Registry**，作为 v7.2 归一化优先（不变量 #1）与消化状态机的契约底座。对象范围为 TASK-S0-02 裁定的**消化对象（L1 原子工具为主，L3 SKILL.md 资产以轻量 Descriptor 视图承载）**。

设计对齐 v7.2 §3.2 的字段组：

```
meta(id/version/created_at)
origin(source_type/source_id/provenance)      # provenance 四级: unknown|declared|verified|signed
tenancy(tenant_id/scope)
capability(name/description/input_schema/output_schema)   # input_schema 字段级 cp.hint 注记（P7.2-24）
trust(risk_level 四级/data_class 四级/requires_approval)  # destructive⇒必审批
runtime(timeout_ms/retry_policy/idempotent)
evolution(stage 七态/internalize_attempts/shadow_config)
quality(success_rate/p99_latency/sample_count/regression_baseline_id)  # P7.2-24
governance(policy_ref/audit_level/undo_hint/compensating_action)
```

约束（§3.2）：`risk=destructive ⇒ requires_approval ∧ undo_hint ∧ compensating_action 全必填`；`secret ⇒ 禁外部端点`；`borrowed ⇒ 必记完整轨迹`。ID 规则：`cp.<source_id>.<upstream_id>`，schema 相同走三路投票 ≥0.9 去重合并，不同走 variant。

## 二、执行步骤

### 步骤 1：契约模型落地（新增模块，不破坏现有）
- 新建 `agent/descriptors/` 包（或 `agent/skills_mgmt/descriptors.py`，按项目分层惯例与 S0-01 RFC 裁决选择，**建议独立包** `agent/descriptors/` 以便跨 skills_mgmt/mcp/workflow_learning 复用）：
  - `models.py`：`ToolDescriptor`（Pydantic v2，字段组如上）、`ProvenanceLevel`/`RiskLevel`/`DataClass` 枚举、`EvolutionStage`（七态：borrowed/mirrored/shadow/internalized/native/permanent_borrowed/deprecated）、`DescriptorValidationError`。
  - `validator.py`：`validate_descriptor(d) -> DescriptorValidationResult`——强制不变量：destructive 三件套必填；secret 禁外部端点；borrowed 必须带 trace 引用策略；ID 规则校验（`cp.<source_id>.<upstream_id>` 格式）。
  - `registry.py`：`DescriptorRegistry`——按 capability_id 注册/查询/列出；三路投票去重合并（schema 相同、相似度 ≥0.9）与 variant 分裂（schema 不同）；alias 表维护（合并留别名）；`load()/save()` 持久化（JSON 或复用现有 SQLite/JSON 存储轨，遵循云枢存储惯例与备份策略）。
- 新增单测：模型字段校验、不变量（destructive 三件套缺失即拒绝）、ID 冲突合并/分裂、alias 往返、registry 持久化（预计 ≥40 例）。

### 步骤 2：与现有资产桥接（双写视图，不改存量主轨）
- 从现有 MCP 工具/运行时技能自动生成或补齐 Descriptor 视图：`mcp_adapter.py` list_tools 结果 → descriptor（source_type=mcp）；内置工具 → descriptor（source_type=builtin）；CLAUDE/community 类 SKILL.md 资产 → 轻量 descriptor 视图（source_type=skill，evolution.stage 映射自 TASK-S0-02 的状态机作用域矩阵）。
- 提供 `bridge_to_skill(skill_id)`：Skill ↔ Descriptor 的字段映射（id/name/description/config_schema→capability.input_schema、output_schema 等），**只读映射，不反向写 Skill**（守不易）。
- 幂等与失败隔离：桥接失败不阻断技能中心主流程（advisory 语义）。

### 步骤 3：provenance/trust/undo_hint 字段回填起点
- 本任务只定义模型与校验；**存量资产字段回填由 TASK-S1-02 承担**。本任务交付"回填的写入 API"（`update_trust(capability_id, patch)`、`mark_provenance(capability_id, level, evidence)` 等），并校验写前不变量。
- 输出能力清单导出（供 UI 能力地图后续使用）：`registry.list_with_trust()`。

### 步骤 4：文档与联动
- 依据 v7.2 §13.3 联动表：本任务触碰 ToolDescriptor schema ⇒ 校验器 + （后续）渲染联动；登记 doc-drift 快照（如项目有 schema snapshot 机制则同步更新 ALLOWLIST）。
- 更新术语映射表/消化对象矩阵（如 S1 裁决影响 L3 轻量视图则回写 S0-02 结论）。
- 回归：技能中心 + MCP 相关测试套件全绿；全量回归无新增失败。

## 三、预期成果

1. `agent/descriptors/` 新包（models/validator/registry + 单测 ≥40 例，覆盖率 ≥80%）。
2. MCP/内置工具/外部技能的 Descriptor 视图桥接（读侧）。
3. trust/provenance 回填写入 API（供 S1-02 使用）。
4. Descriptor 能力清单导出（供后续能力地图）。
5. `TASK-S1-01_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] ToolDescriptor 覆盖 §3.2 全部字段组（meta/origin/tenancy/capability/trust/runtime/evolution/quality/governance），枚举值域与文档一致
- [ ] 校验器强制三不变量：destructive 三件套必填 / secret 禁外部端点 / borrowed 必记轨迹策略
- [ ] ID 规则 `cp.<source_id>.<upstream_id>` 生效；schema 相同合并（三路 ≥0.9）与不同 variant 分裂均有用例
- [ ] MCP list_tools → descriptor 自动生成演示通过；Skill↔Descriptor 只读桥接不写回
- [ ] 桥接失败不阻断技能中心主流程（advisory 语义用例）
- [ ] 新增单测全绿且覆盖率 ≥80%；技能中心/MCP 既有套件无回归
- [ ] doc-drift/schema 快照联动登记完成（若项目启用）
