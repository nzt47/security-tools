# TASK-S3-02 EquivalenceCase 判定集资产 + 回放沙箱 + 验收门量化

> 所属阶段：S3 消化流水线｜依赖：**S3-01（已结案，前置已就绪）**、S2-01（Trace 台账）｜预估：5–8 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §3.1（EquivalenceCase 30-100 组）/§3.3（mirrored→shadow 唯一通行证）/§4.5（验收门硬闸）/P7.2-23（Seed Pack 12 技能判定基线）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`
> ✅ **前置状态（2026-09-11 核实）**：S3-01 已结案（验收 15/15、CI 21/21 job 全绿），并**直接交付本任务所需输入**——见 §零。
> 🚀 **新会话执行入口**：[START-S3-02_新会话启动提示词.md](START-S3-02_新会话启动提示词.md)（**分发壳**，非任务书：可直接整段复制给执行会话，内含 worktree 隔离命令与实测校验、本地门禁清单、S3-01 已知坑、回报格式）。

---

## 零、开工前置：S3-01 已交付的可直接复用资产（勿重复实现）

S3-01 交付 `agent/digestion/`（8 模块 3435 行、333 例单测、覆盖率 93%），本任务**直接消费**以下接口：

| 复用对象 | 位置 | 在本任务中的用途 |
|---|---|---|
| `Trajectory` / `TrajectoryStep` / `TraceSet` | `agent/digestion/models.py` | 判定集用例的**来源数据**（清洗后确定性轨迹，含噪声标记与负样本） |
| `clean_trajectory()` / `trajectory_from_rows()` / `same_task_key()` / `intent_key_for_trace()` / `classify_outcome()` / `mark_negative()` | `agent/digestion/cleaning.py` | 从 Trace 生成用例时复用其**同类判定键与清洗口径**（保证"用例属于同一能力同一意图"） |
| `CandidatePattern`（骨架/参数槽/分支条件/副作用画像）、`PatternStep`、`BranchCondition`、`ParameterSlot` | `agent/digestion/models.py` + `mining.py` + `generalize.py` | 判定集的"破坏性分支覆盖"判定依据（§4.5 验收门第 4 条）；`infer_parameter_slots`/`apply_slots` 复用于用例参数化 |
| `SkillDraft` / `build_skill_draft()` / `compile_skill_body()` | `agent/digestion/generation.py` | 回放沙箱的**被评测候选实现**（draft 态，不自动发布） |
| `pattern_quality_gate()` / `converter_gate_constants()` / `solidify_min_rule_steps()` | `agent/digestion/generation.py` | 与既有升格门口径**逐值对账**（S3-01 §4.3 已统一 `MIN_PATTERN_STEPS=2`（成形）与 `MIN_ASCENSION_STEPS=3`（可升格）——本任务判定集门槛须沿用同一语义，勿再引入第三套常量） |
| `DigestionService.pipeline()` / `DigestionReport` | `agent/digestion/service.py` | 端到端串接：轨迹 → 候选模式 → 生成 draft → **本任务判定集与验收门** |
| `stage_migrate()` / `evaluate_migration()` / `recommended_stage()` | `agent/digestion/stage.py` | 验收门通过后的 stage 推进入口（mirrored→shadow 的证据由本任务产出） |

> **S3-01 移交给本任务的扩展点（其结案报告 §5 遗留 #4）**：决策树特征当前仅含「可选/骨架步骤有无 + 步数档位」，**参数级条件**（如"路径含 test 时才需 review"）未纳入 —— 本任务若判定集需要参数级分支覆盖，在此处扩展（属本任务范围内的合理扩展）。

## 一、目标描述

建立 **EquivalenceCase 判定集资产库 + 确定性回放沙箱 + 验收门**（T1 修正落地：判定集是独立资产，带 origin_trace_id、脱离 Trace 90 天生命周期、可漂移重探）：

1. **判定集资产**：每个候选内化能力持 30–100 组等价用例（输入→期望输出/副作用集合）；用例可来自：Seed Pack（P7.2-23：≥12 技能 × ≥3 组起步）、Trace 自动生成（复制数据脱离其生命周期，带 origin_trace_id）、LLM 生成 + 人工抽检。
2. **回放沙箱**：确定性执行（对齐 §4.5 Shadow 的 record-and-replay：副作用绝不双写）；同一输入在"上游/现有实现"与"候选原生实现"双跑 diff。
3. **验收门硬闸（§4.5）**：≥20 条回放全过 / 成功率 ≥ 基线×0.98 / p99 ≤ 上游 / 覆盖破坏性分支——mirrored→shadow 与（后续）shadow→internalized 的通行证。
4. **漂移重探**（§4.5）：30 天或上游版本变化 → 简化探针 → schema 变化 → drifted → 判定集失效重生成。
5. 本任务交付判定集设施与验收门；**shadow 灰度运行由 S3-03 承接**。

## 二、执行步骤

### 步骤 1：判定集模型与存储
- 新增 `agent/digestion/cases.py`（与 S3-01 同包，勿另起包）：`EquivalenceCase`（id/capability_id/input/expected_output/expected_side_effects/kind/origin_trace_id/created_at/active）、`CaseSet`（capability 维度 30-100 组 + 版本）、`CaseStore`（JSON/SQLite 持久化，独立于 Trace 存储；Trace 90 天过期不影响判定集）。
- **用例生成通道（复用 §零 资产）**：① Seed Pack 预置；② 从 `TraceSet`/`Trajectory` 自动生成（经 `same_task_key()` 归组 + 复制数据脱离生命周期 + 记 `origin_trace_id`）；③ LLM 生成 + 人工抽检。
- Seed Pack 起步（P7.2-23）：预置 ≥12 技能 × ≥3 组等价用例（复用既有测试数据/样例，标记 provenance）。

### 步骤 2：回放沙箱
- 新增 `agent/digestion/sandbox.py`：
  - 确定性执行器：对候选实现（draft 代码/步骤化 SKILL 的骨架）执行用例；
  - record-and-replay 副作用记录（只读环境 + 副作用集合比对，绝不双写真实环境）；
  - 双跑 diff：上游实现 vs 候选原生（输出结构 schema 硬性 → 副作用集合硬性 → 非确定性 LLM-judge ≥0.85 软性 + 10% 人工抽检——对齐 §4.5 三层比对）。
- 沙箱资源配额（对齐 §5.2 TEST 约束：资源配额上限 + 无真实数据）。

### 步骤 3：验收门
- `acceptance_gate(capability_id) -> GateResult`：回放全过率、成功率 ≥ 基线×0.98、p99 ≤ 上游、破坏性分支覆盖——四条件齐才发通行证（写 digest.stage 事件 + 审计）。
- 失败清单化（哪些用例失败/哪层 diff 未过），供 S3-03 灰度期观察与人工介入。
- 基线来源：S2-01 台账 quality 统计（success_rate/p99）+ 上游实测（若可调用）或 shadow 期前既有实现基线。

### 步骤 4：漂移重探
- `reprobe(capability_id, trigger)`：30 天定时（复用 task_scheduler/evolution_scheduler）或上游版本变化触发 → 简化探针 → schema 变化 → 标记 drifted → 判定集失效重生成（重新从 Trace 采样 + Seed 回填）。

### 步骤 5：回归与归档
- 回归：digestion/descriptors/skills 相关套件零回归；新增单测（判定集 CRUD、回放确定性、三层比对、验收门四条件、漂移重探、Seed Pack 加载）≥50 例、覆盖率 ≥80%。
- 撰写 `TASK-S3-02_验收报告.md`（含一个真实能力的判定集→回放→验收门样例）。

## 三、预期成果

1. EquivalenceCase/CaseSet/CaseStore（30-100 组能力级资产）。
2. Seed Pack ≥12 技能 × ≥3 组预置用例。
3. 回放沙箱（record-and-replay + 双跑 diff 三层比对）。
4. 验收门（四条件硬闸 + 失败清单）。
5. 漂移重探机制。
6. `TASK-S3-02_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] 判定集资产带 origin_trace_id，Trace 过期不影响（独立存储验证）
- [ ] Seed Pack ≥12×≥3 组可加载且全部可回放
- [ ] 回放确定性：同输入同环境两次结果一致；副作用只记录不双写（用例断言无真实副作用）
- [ ] 三层比对实现（结构 schema → 副作用集合 → LLM-judge≥0.85 + 10% 人工抽检）；任一层失败有明确报告
- [ ] 验收门四条件（回放全过/成功率≥基线×0.98/p99≤上游/破坏性分支覆盖）齐才发通行证；失败清单化
- [ ] 漂移重探：30 天或上游版本变化触发；drifted 后判定集失效重生成路径可用
- [ ] 既有 digestion/descriptors/skills 套件零回归；新增单测全绿、覆盖率 ≥80%
- [ ] 真实能力样例（判定集→回放→验收门）在验收报告可复现
