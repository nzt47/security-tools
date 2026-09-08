# TASK-S0-02 消化对象三层盘点确认（原子工具 / 工作流 / SKILL.md）

> 所属阶段：S0 对齐层｜依赖：TASK-S0-01（术语映射）｜预估：2 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §3.1/§3.3/§3.7（审计缺陷 T8，见 01 号审计报告 §3）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

明确 v7.2"消化"概念在云枢中作用的**对象分层**，消除"消化对象不清"（T8）导致的施工错位。云枢现状存在三类可"被消化/被固化"的资产，各自生命周期与载体不同：

| 层 | 云枢载体 | 现状生命周期 | 与 v7.2 状态机（§3.3 七态）的关系 |
|---|---|---|---|
| L1 原子工具 / Capability | 运行时技能/工具、MCP 工具（`mcp_adapter.py`）、内置 capability | 启用/停用 | 最接近 v7.2 ToolDescriptor（cp.<source>.<upstream>）对象 |
| L2 工作流序列 | `workflow_learning` LearnedWorkflow（`data/learned_workflows.json`）+ `process_distill` 产物 | workflow→Skill 升格（skill_converter 质量门控） | 是"轨迹→Skill"的中间形态 |
| L3 SKILL.md 编排资产 | `skills_mgmt` Skill 资产（JSON 轨 + skills_repo 文件轨） | DRAFT→PENDING_REVIEW→APPROVED/PUBLISHED→DEPRECATED→ARCHIVED | 是 v7.2 SKILL.md（§3.7）对应的最终编排单元 |

本任务产出：**消化对象三层盘点确认 + 映射矩阵复核**，确定 v7.2 状态机七态各自作用于哪一层（或定义层间转换器），为 S1 契约层与 S3 消化流水线提供明确输入。

## 二、执行步骤

### 步骤 1：盘点三层资产现状
- **L1**：枚举运行时技能/工具来源（builtin/custom/MCP/claude/community/ai_generated，参考 `models.py` `SkillCategory`）；确认 `mcp_adapter.py` 的能力发现机制；确认哪些对象有"幂等/副作用/风险"描述（对照 v7.2 §3.2 trust 字段缺口）。
- **L2**：盘点 `workflow_learning`（learner/generator/skill_converter 链路）与 `process_distill`（sources→distiller→merge→solidify）产物；确认"轨迹→workflow→Skill"的现有转换点与门控条件（success_count≥5、confidence≥0.7 等）。
- **L3**：盘点 `skills_mgmt` Skill 资产规模与状态分布（data/skills.json / skills_repo 文件轨）；确认 config_schema/output_schema 覆盖情况。
- 输出 `三层资产盘点表`（层 × 载体 × 规模/状态分布 × 生命周期入口/出口 × 与 v7.2 概念对应）。

### 步骤 2：状态机作用域判定
- 对 v7.2 §3.3 七态（borrowed→mirrored→shadow→internalized→native + permanent_borrowed/deprecated）逐一回答：作用于 L1/L2/L3 中的哪几层？转移条件（轨迹≥20/判定集/灰度 72h/30 天零回退）在哪层可测？
- 特别裁决：
  - L2（workflow 序列）是否纳入七态，还是仅作为 L1→L3 的中间过渡不入态？
  - L3（SKILL.md）的现有 draft→published 状态机与七态的对应（draft≈borrowed？published≈internalized？）——给出**不破坏现有语义**的映射，明确哪些阶段新增（shadow/灰度）到哪一层。
- 输出 `状态机作用域矩阵`（七态 × L1/L2/L3 × 适用/不适用 × 理由 × 现有替代机制）。

### 步骤 3：层间转换器识别
- 识别并记录"层间转换"现状钩子：L2→L3（workflow_learning.skill_converter）、L1→L3（外来技能导入 create_manual/install）、L1→L2（learned workflow 由工具调用序列构成）。
- 标注每个转换器的**输入门控/质量门/审批点**（对照 v7.2 三层组合拳 §2.4 与委派萃取 §3.9）。

### 步骤 4：产出映射矩阵并评审
- 复核/更新 01 号审计报告 §6 差距矩阵中与消化对象相关的行；补齐"哪一层缺 shadow/判定集/内化触发"的缺口行。
- 与 TASK-S0-01 术语映射表合并评审一次，消除交叉不一致。

## 三、预期成果

1. `三层资产盘点表`（含规模与状态分布）。
2. `状态机作用域矩阵`（七态 × L1/L2/L3 的适用判定）。
3. 层间转换器清单（现状钩子 + 缺口）。
4. 更新后的消化对象映射矩阵章节（可并入 S0-01 术语映射表或独立成节）。
5. `TASK-S0-02_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] 三层资产盘点表覆盖 L1/L2/L3 全部现有载体，规模数据可复核
- [ ] 七态 × 三层的适用判定逐格给出且理由充分，无"都适用/都不适用"的含糊格
- [ ] L3 现有状态机与 v7.2 七态映射不破坏现有语义（存量资产状态不受影响）
- [ ] 层间转换器清单标注了每个转换点的门控/质量门/审批点
- [ ] 与 S0-01 术语映射表无交叉矛盾（联合评审通过）
- [ ] 盘点结论被 S1（契约层）与 S3（消化流水线）任务作为输入引用
