# TASK-02：反思沉淀与认知评估上线

## 0. 任务标识

| 字段 | 值 |
|---|---|
| 任务编号 | TASK-02 |
| 所属阶段 | 主线阶段 2/5 |
| 前置依赖 | TASK-01（规划接线后 Reflector 才可被触发） |
| 并行建议 | 可与 TASK-03 并行（度量埋点与本任务有重叠接口，注意统一） |
| 涉及主文档 | `docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§4.4 断点 2、3） |

## 1. 背景（为什么做）

设计思路的核心是"执行→反思→沉淀→复用→优化"。审计发现云枢的**反思产物不闭环**：

1. `orchestrator.self_reflect()`（`agent/orchestrator/orchestrator.py` L2742 附近）每次交互后产出反思，但结果只写入内存 `_reflection_history` + 黑匣子日志 `memory.save_log("self_reflect")`，**不进向量检索面**，对后续决策零影响。
2. `planning/reflector.py` 的 `learn_from_experience()`（经验/教训落盘 `data/reflection/*.json`）**无任何生产调用方**，经验库恒空；`get_advice_for_task()` 虽已实现（D17 注入历史经验），因库为空而永远命中不到。
3. `agent/cognitive/` 的认知评估套件（ReflectionEngine 6 维规则 / ActorCritic / Debate / KnowledgePrecipitate）**生产代码零调用**（`CognitiveLoop.evaluate()` 仅被测试/demo 引用），`config.yaml` 中 `critic_evaluation_enabled: false`。

本任务把"反思→沉淀"接通，并把轻量规则评估以保守模式上线。

## 2. 目标描述（做什么）

1. 让反思产物**进入检索面**：self_reflect 与 Reflector 经验写入可检索存储，供后续对话注入与 TASK-04 沉淀管道消费。
2. **接线** `learn_from_experience()`：在规划执行路径（TASK-01 打通后）成功/失败时自动沉淀经验/教训。
3. 认知规则评估以**保守模式**上线：`critic_evaluation_enabled=true` 时，在 self_reflect 后追加 `ReflectionEngine.evaluate()` 规则评估，产出记录进 metrics（供 TASK-03 的 KPI 消费），**只记录不干预**（ActorCritic/Debate 本期保持休眠，明确不做）。

## 3. 不变式约束（不易——禁止触碰）

- **禁止修改** `planning/reflector.py` 现有方法签名与文件格式（`experiences.json`/`lessons.json` schema 不变）。
- **禁止修改** `agent/cognitive/` 各评估器内部实现（本期只接线 ReflectionEngine 规则版；ActorCritic/Debate/LLM_DRIVEN 一律不启用、不实现）。
- **保留** `orchestrator.self_reflect()` 现有行为与输出格式（在其基础上追加写入，不改判定逻辑）。
- **保留** `config.yaml` 现有 `features.critic_evaluation_enabled` 的 false 默认语义——本任务接线后默认仍不生效，需人工开启。
- **保留** `workflow_learning` 的既有闭环（不得改动其学习/匹配/升格链路）。

## 4. 执行步骤

### Step 1：反思产物写入检索面（观察模式）
修改 `agent/orchestrator/orchestrator.py`：
- `self_reflect()` 结果追加结构化写入——复用 orchestrator 已有的 `VectorStore.add(conversation)` 写入点，新增 `VectorStore.add(record, doc_type="reflection")`（或等价现有方法，以最小改动为准），字段含：`task_id / input_hash / score / suggestions / created_at`。
- 若项目既有 `KnowledgePrecipitator`（`agent/cognitive/knowledge.py`，confidence≥0.5 时异步写 MemoryRouter）可复用，则优先通过它写入，避免重复造轮子。
- 全部写入走**观察模式**：config 新增 `learning.reflection_persist: false`（默认 false，开启后才写检索面；false 时保持现状只写日志）。
- 检索面写入失败静默降级（log WARNING，不中断主链路）。

### Step 2：接线 learn_from_experience
在 `planning/core.py`（或 orchestrator 的规划调用点）中：
- `execute_plan()` 成功/失败收尾时调用 `reflector.learn_from_experience(plan, result)`，产物落盘 `data/reflection/{experiences,lessons}.json`（方法已存在，只接调用）。
- 确保 ReAct 路径（`planning/react.py`）后续轮次通过 `get_advice_for_task()` 注入历史经验（D17 已实现注入逻辑，本任务验证其数据源非空即可，不改注入代码）。
- config 新增 `learning.experience_persist: false`（默认 false 观察，true 才落盘）。观察模式下仅记录调用意图日志，不实际写盘。

### Step 3：规则评估上线（保守）
- config `features.critic_evaluation_enabled: true` 时，在 `self_reflect()` 后调用 `ReflectionEngine.evaluate(...)`（`agent/cognitive/reflection.py`，6 维规则，零 Token）。
- 评估结果（score/passed/should_retry）写入 `agent/monitoring/metrics.py` 计数器/直方图（新增 `learning.eval_*` 指标族），供 TASK-03 看板消费；**不触发重试、不拦截响应**（保守模式）。
- 评估调用必须 `try/except` 全兜底（评估器异常不影响主链路）。

### Step 4：补测试（TDD）
新增 `tests/unit/test_reflection_pipeline.py`：
- 用例 1：`reflection_persist=true` 时 self_reflect 后检索面出现该记录（mock VectorStore 断言调用与字段）。
- 用例 2：`reflection_persist=false` 时零写入，行为与现状一致。
- 用例 3：`learn_from_experience` 在 execute_plan 成功后落盘 `experiences.json` 新增条目（临时目录注入）。
- 用例 4：`critic_evaluation_enabled=true` 时评估计数器递增；评估器抛异常时主链路不受影响。
- 用例 5：反思/评估写入失败时主链路正常返回（降级验证）。

### Step 5：回归与门禁
- `python -m pytest tests/unit -q` 全绿；`python -m pytest tests/unit/test_reflection_pipeline.py -v` 全绿。
- 质量门禁见 §6。

## 5. 预期成果（交付物）

1. `config.yaml` 新增 `learning.reflection_persist` / `learning.experience_persist` 两开关（默认 false，含注释）。
2. orchestrator 反思写入检索面（观察模式）+ ReflectionEngine 保守评估接线。
3. `planning/core.py` 执行收尾接线 `learn_from_experience`。
4. 新增 `tests/unit/test_reflection_pipeline.py`（≥5 用例）。
5. 变更说明文档：`docs/zh/智能体学习机制重构计划/变更说明/TASK-02_变更说明.md`（含反思产物字段 schema 定义，供 TASK-04 消费）。

## 6. 评估标准（验收条件）

### 功能验收
- [ ] 三个开关均默认 false 时，系统行为与改造前一致（回归零差异）。
- [ ] `reflection_persist=true` 时，一次交互后检索面可查得反思记录（含 schema 字段）。
- [ ] 规划执行成功后 `experiences.json` 新增条目；失败后 `lessons.json` 新增条目。
- [ ] `critic_evaluation_enabled=true` 时，metrics 出现 `learning.eval_*` 指标且随交互递增；响应不被评估拦截。

### 测试要求
- [ ] 新增 ≥ 5 用例全部通过；`python -m pytest tests/unit -q` 全绿。

### 质量门禁
- [ ] `python scripts/pre_commit_ci_guard.py --static-only --strict` 零新增告警。
- [ ] `python -m agent.observability.arch_rules --check` 通过（orchestrator→cognitive 引用遵循现有规则，不新增循环依赖）。

## 7. 工程约束（仓库规则）

- 同 TASK-01 §7：git 精确路径 add、commit 走 `-F`、hook 需 `TLM_HOOK_SOURCE_REPO`、禁止触碰并行会话文件、新文件 UTF-8 无 BOM。
- 本任务涉及 orchestrator 主链路，改动面控制在"追加写入/追加调用"内，禁止重构现有流程。
- 反思产物 schema 一旦定义，写死在变更说明中，供 TASK-04 对接（防两任务 schema 漂移）。
