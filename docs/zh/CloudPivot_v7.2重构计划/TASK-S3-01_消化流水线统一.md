# TASK-S3-01 消化流水线统一（轨迹→模式挖掘→Skill 生成→stage 迁移）

> 所属阶段：S3 消化流水线｜依赖：S0-02（三层盘点）、S1-02（descriptor 台账）、S2-01（统一 Trace `agent/observability/trace_v2.py`）、S2-02（链式审计 `agent/audit/`）、S2-03（events.v1 `agent/observability/events.py` + `acr.py`/`utc.py`）｜预估：8–12 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §3.3/§3.3.1（七态 + 产线对照）/§4.5（SkillFactory 流水线）/P7.2-01（内化触发）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`
> ⚠️ **本任务是 S2 遗留的主要接收方（Owner 已裁定，见 S2-01 结案报告 §5/§6.3 与 S2-03 结案报告 §5）**：开工第一步先接收并清账 §零 的 4 项遗留。

---

## 零、开工前置：接收 S2 移交遗留（Owner 已裁定归本任务，须先清账）

| # | 移交项 | 来源 | 具体落点 | 处置要求 |
|---|---|---|---|---|
| L1 | 工具名 → descriptor `capability_id` 运行时改写 | S2-01 遗留 #1 | 工具级 trace 落账当前用工具名，join 由 registry 侧完成 | 随能力地图统一，在流水线入口/工具链落账点完成改写 |
| L2 | orchestrator **规划 wire 路径**未接任务级统一 Trace（该路径任务不进 ACR 分母） | S2-01 遗留 #2 + S2-03 遗留 #1（同源） | `agent/orchestrator/orchestrator.py:1173-1176` 的 `else:`（wire 规划成功）分支仅设 `ts_llm`/`llm_duration_ms`，缺 `_begin_unified_task_trace()`/`_end_unified_task_trace()`；任务级 Trace 与 `task.closed` 仅存在于相邻 LLM 段 try/finally（`:1108`/`:1169`） | S2-01/S2-03 均显式指派本任务在"规划产出与消化流水线合流时一并接线" |
| L3 | `data/descriptors.json` 中 borrowed 能力 `trace_policy` 仍为占位串（`…(S2-pending)`） | S2-01 遗留 #9（Owner 裁定并入本任务，不单列小任务） | 改 `agent/descriptors/bridge.py` 默认串为真实 ledger 引用 + 同步 S1 既有测试断言 | 随能力地图统一修订 |
| L4 | stage 未入轨 25 条 warning（存量 29 条 descriptor） | S1-02 遗留 | 见步骤 4 | 首次入轨（borrowed 起步）+ 产出"入轨报告" |

> L1-L3 与本任务主线的交汇点：**能力台账（descriptor ↔ trace）与消化产物的 capability 归一**。三项均须在本任务内收口，并在验收报告逐条标注收口证据（文件 + 用例 + 演示）。

## 一、目标描述

把云枢现有的**三轨固化链路**统一接入 v7.2 消化流水线并驱动 L1 原子工具的七态 stage 迁移（消费 S0-02 裁定：L1 为七态主轨、L3 叠加 evolution.stage、L2 中间过渡不入态）：

- 现有三轨：① `skills_mgmt` 评审-消化（assess/review 权威评审）；② `process_distill`（素材→子代理蒸馏→workflow/Skill 固化）；③ `workflow_learning`（轨迹→LearnedWorkflow→skill_converter 升格 Skill）。
- 目标流水线（§4.5）：轨迹采集（S2-01 台账）→ 脱敏 → **轨迹清洗**（T3 修正：去噪/归一/同任务判定）→ **模式挖掘**（LCS + 决策树，≥20 条同类）→ 泛化（具体值→参数占位符）→ SKILL.md 生成 → 确定性回放沙箱 → 验收门（S3-02）→ shadow/灰度（S3-03）→ 转正 → 签名。
- 本任务范围 = **S2-01 台账 → 轨迹清洗 → 模式挖掘 → SKILL.md/工作流产物生成 → stage 迁移（borrowed→mirrored 及 L3 叠加）**；判定集与 shadow/灰度由 S3-02/S3-03 承接，但本任务需产出其前置（清洗/挖掘/生成的确定性管道）。

## 二、执行步骤

### 步骤 1：统一管道编排（消费 S2-01 已交付读取接口）
- 新增 `agent/digestion/`（或扩展 process_distill 为编排层，按 S0-01 RFC 分层裁决）——消化流水线门面 `DigestionService.pipeline(capability_id | trace_set)`：
  - 输入：S2-01 台账读取接口 `UnifiedTraceStore.list_by_capability(capability_id, limit)` / `chain(trace_id)` / `task_summary(task_id)` / `query(...)`（`agent/observability/trace_v2.py`，已完成交付）；条数门槛 ≥20 条同类（清洗后计数）；
  - 输出：`DigestionReport`（轨迹数/清洗后数/候选模式/SKILL.md 草稿/stage 建议）；
  - 事件：产出与 stage 变更经 `agent/observability/events.py` 的 `emit()`（events.v1 信封，含 `digest.stage` 等类型）+ 审计 `agent/audit/facade.py::audit.record(...)`（治理类事件已由 S2-03 建立镜像规则，勿重复留痕）。
- **L1 收口**：流水线入口与工具链落账点完成"工具名 → capability_id"改写（对齐 S2-01 `capability_reference()` / `load_runtime_descriptors()`）。
- 明确三轨复用关系：workflow_learning 的序列学习（L2）与 process_distill 的素材蒸馏保留各自入口，但**统一经本管道落 L3 产物**（SKILL.md + evolution.stage 叠加），避免第三套固化逻辑。
- **L2 收口**：`orchestrator.py:1173-1176`（wire 规划成功分支）补 `_begin_unified_task_trace()`/`_end_unified_task_trace()`，使规划路径任务同样产生任务级 Trace 与 `task.closed`（进入 ACR 分母）；补后须验证该分支与相邻 LLM 段 try/finally（`:1108`/`:1169`）无重复开闭。

### 步骤 2：轨迹清洗（T3 修正落地）
- 清洗规则：剔除失败重试/探索前缀、合并同类重复步骤、失败轨迹保留并标注（负样本，供 S5 评测与 negative_intent 使用）、归一参数化（路径/时间戳/随机值→占位符）。
- "同类轨迹"判定键：capability_id + task intent 归一 + 结果状态（成功/失败）——定义并写文档（T3 要求）。
- 单测：噪声轨迹清洗后模式可提取；同任务不同路径的归一并集判定。

### 步骤 3：模式挖掘与生成
- 双路挖掘（§4.5）：LCS（步骤序列最长公共子序列）+ 决策树（分支条件提取，输入 ≥20 条清洗后同类轨迹）；产出候选模式（步骤骨架 + 参数槽）。
- 生成：复用/桥接 `workflow_learning/skill_converter.py`（质量门控升格）与 `process_distill/solidify.py`（skill.md 编译 front matter + 步骤正文）——候选模式 → SKILL.md 草稿（draft 态，不自动发布）。
- 泛化规则：具体值→参数占位符、硬编码路径→变量（现有 converter 已有类似逻辑，补齐即可）。

### 步骤 4：stage 迁移驱动（borrowed→mirrored 及 L3 叠加）
- 依据 §3.3：borrowed→mirrored 条件 = 轨迹 ≥20 + 模式可提取；mirrored 验收物 = 副作用画像 + 候选模式。
- 实现 `stage_migrate(capability_id, to_stage, evidence)`（写 descriptor.evolution.stage + 审计 + digest.stage 事件——联动 S2-03/审计 S2-02）。
- **消费 S1-02 遗留（25 条 stage 未入轨 warning）**：对存量 29 条 descriptor 中 stage 为空的资产执行首次入轨（borrowed 起步），补齐 warning 清单（产出"入轨报告"）。
- 迁移失败/证据不足 → 保持原 stage 并记录（不静默推进，符合"宁可冗余不可误合"）。

### 步骤 5：回归与归档
- 回归：process_distill/workflow_learning/skills 相关套件零回归；新增单测（清洗/挖掘/泛化/入轨/审计事件）≥50 例、覆盖率 ≥80%。
- 撰写 `TASK-S3-01_验收报告.md`（含一段真实轨迹→SKILL.md 草稿的端到端样例）。

## 三、预期成果

1. `DigestionService` 统一管道 + 三轨复用说明。
2. 轨迹清洗模块（规则 + 同类判定键定义）。
3. LCS+决策树模式挖掘 + SKILL.md 草稿生成桥接（draft 态）。
4. stage_migrate（borrowed→mirrored）驱动 + 存量 25 条 warning 入轨报告。
5. **S2 遗留收口 4 项**（L1 工具名→capability_id 改写 / L2 规划 wire 路径任务级 Trace 与 task.closed / L3 trace_policy 占位串切换 / L4 stage 入轨）。
6. `TASK-S3-01_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] 真实/模拟轨迹 ≥20 条同类可产出候选模式（样例通过）
- [ ] 清洗规则有单测覆盖（噪声轨迹→提取模式；失败轨迹保留为负样本）
- [ ] 同类轨迹判定键定义明确且无自相矛盾
- [ ] 产物为 draft 态 SKILL.md，不自动发布/不越过审批
- [ ] stage 迁移写 descriptor + 审计 + digest.stage 事件（三处联动一致，§13.3）
- [ ] 存量 25 条 stage 未入轨资产全部入轨（borrowed 起步）并有报告
- [ ] 迁移证据不足不静默推进（用例覆盖）
- [ ] 既有 process_distill/workflow_learning/skills 套件零回归；新增单测全绿、覆盖率 ≥80%
- [ ] 端到端样例（轨迹→SKILL.md 草稿）在验收报告中可复现
- [ ] **【L1】** 工具链落账的 `capability_id` 与 `data/descriptors.json` 台账可 join（改写后用例 + 演示）
- [ ] **【L2】** `orchestrator.py` wire 规划成功分支产出任务级 Trace 与 `task.closed`（该路径任务计入 ACR 分母）；无重复开闭（用例断言）
- [ ] **【L3】** borrowed 能力 `trace_policy` 为真实 ledger 引用（非 `(S2-pending)` 占位）；`agent/descriptors/bridge.py` 默认串与 S1 既有断言同步更新
- [ ] **【L4】** stage 入轨报告归档，25 条 warning 清零或清单化残留
- [ ] 四项遗留均在验收报告逐条给出收口证据（文件 + 用例 + 演示）
