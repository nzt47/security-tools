# TASK-02 变更说明：反思沉淀与认知评估上线

| 字段 | 值 |
| --- | --- |
| 任务编号 | TASK-02 |
| 所属阶段 | 主线阶段 2/5 |
| 涉及主文档 | `docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§4.4 断点 2、3） |
| 关联任务书 | `docs/zh/智能体学习机制重构计划/TASK-02_反思沉淀与认知评估上线.md` |
| 实现日期 | 2026-08-14 |

## 1. 背景

云枢的反思产物此前不闭环：

1. `orchestrator.self_reflect()` 每次交互产出反思，但只写入内存 `_reflection_history` + 日志，不进向量检索面；
2. `planning/reflector.py` 的 `learn_from_experience()` 无生产调用方，经验库恒空，`get_advice_for_task()` 永远命中不到；
3. `agent/cognitive/` 的认知评估套件（ReflectionEngine 6 维规则）生产零调用，`critic_evaluation_enabled: false`。

本任务把"反思→沉淀"接通，并以保守/观察模式上线认知规则评估（只记录不干预），为 TASK-03 度量体系提供 `learning.eval_*` 指标数据源。

## 2. 改动点

### 2.1 配置（config.yaml）

在 `learning:` 段新增 2 个观察模式开关（含中文注释，说明 Why）：

| 配置键 | 默认值 | 说明 |
| --- | --- | --- |
| `learning.reflection_persist` | `false` | 反思产物写入检索面开关。false（默认）：self_reflect 保持现状仅写内存/日志；true：额外写入向量检索面 |
| `learning.experience_persist` | `false` | 规划经验/教训落盘开关。false（默认）：execute_plan 收尾仅记调用意图日志；true：实际调 `learn_from_experience` 落盘 |

优先级遵循项目既有约定：**环境变量 > config.yaml > 硬编码默认值**。
对应环境变量：`LEARNING_REFLECTION_PERSIST` / `LEARNING_EXPERIENCE_PERSIST`。
评估开关复用既有 `features.critic_evaluation_enabled`（默认 false，语义不变），对应环境变量 `CRITIC_EVALUATION_ENABLED`。

### 2.2 orchestrator.py（agent/orchestrator/orchestrator.py）

1. **配置加载器**：类方法 `_load_learning_config()`，三层优先级（硬编码默认 → config.yaml → 环境变量），
   与 `_load_planning_wire_config` 同源模式；默认值 `{reflection_persist: False, critic_evaluation_enabled: False}`，
   config.yaml 缺失/解析失败不影响主链路。
2. **self_reflect() 追加接线**（不改既有行为与返回格式，仅追加调用）：
   - `critic_evaluation_enabled=true` 时在反思后调用 `_run_rule_evaluation(task, response)`（ReflectionEngine 6 维规则，零 Token）；
   - `reflection_persist=true` 时调用 `_persist_reflection(entry, task, eval_result)` 写入向量检索面。
3. **`_run_rule_evaluation()`**（保守模式，只记录不干预）：
   - 评估结果（score/passed）写入 `learning.eval.*` 指标族（`learning.eval.total` / `.passed` / `.failed` 计数器 + `learning.eval.score` 直方图），供 TASK-03 消费；
   - **不触发重试、不拦截响应**；评估器异常仅记 WARNING 降级返回 None，主链路不受影响。
4. **`_persist_reflection()`**（观察模式）：
   - `learning.reflection_persist=true` 时复用 `_vector_memory.add()` 写入检索面（type=reflection）；
   - 写入失败静默降级（WARNING），不中断主链路。

### 2.3 planning/core.py（执行收尾接线）

1. **配置加载器**：类方法 `_load_experience_persist_config()`（硬编码默认 → config.yaml `learning.experience_persist` → 环境变量 `LEARNING_EXPERIENCE_PERSIST`）。
2. **`_record_experience(plan, success)`**：`experience_persist=true` 时在 execute_plan 成功/失败收尾后
   `await reflector.learn_from_experience(plan.original_task, result)`（成功 → `ActionResult.success_result`，失败 → `failure_result`），
   产物落盘 `data/reflection/{experiences,lessons}.json`；调用异常仅记 WARNING 降级。
   **不改 `reflector.py` 任何签名与文件 schema。**
3. **三处收尾接线**（均在 `_record_plan_result` 埋点之后）：
   - 计划验证失败路径（`PlanValidationError` → FAILED）→ `_record_experience(success=False)`；
   - 正常执行收尾路径（COMPLETED/FAILED/CANCELLED）→ `_record_experience(success=(state==COMPLETED))`；
   - 状态转换异常路径（`InvalidStateTransitionError` → FAILED）→ `_record_experience(success=False)`。

### 2.4 反思产物 schema（供 TASK-04 对接，防两任务 schema 漂移）

反思产物写入检索面的 metadata 结构（**写死，TASK-04 沉淀管道按此消费**）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `type` | str | 固定 `"reflection"`（检索面文档类型标记） |
| `interaction` | int | 交互序号（= `_interaction_count`） |
| `task_id` | str | 任务标识（当前取交互序号字符串，后续规划任务可替换为 plan_id） |
| `input_hash` | str | 输入文本 SHA-1 前 12 位 hex（`hashlib.sha1(task.encode()).hexdigest()[:12]`） |
| `score` | float | 规则评估分数 [0,1]；未启用评估时恒 0.0 |
| `suggestions` | list[str] | 评估建议列表；未启用评估时空列表 |
| `created_at` | str | 反思产出时间（ISO 8601 UTC，= entry.timestamp） |

content 为反思文本（`反思(#N): <reflection_text>`）。

### 2.5 测试

新增 `tests/unit/test_reflection_pipeline.py`（8 用例）：

| # | 用例 | 验证点 |
| --- | --- | --- |
| 1 | `reflection_persist=true` 写入检索面 | `_vector_memory.add` 被调用且 metadata 含 schema 字段（type/task_id/input_hash/score/suggestions/created_at） |
| 2 | `reflection_persist=false` 零写入 | 不调 `add`，反思产物仍照常产出（行为与现状一致） |
| 3 | execute_plan 成功后落盘 experiences.json | 临时目录注入，成功条目含 task_description |
| 4 | execute_plan 失败后落盘 lessons.json | 失败条目 failure_point 正确 |
| 5 | `experience_persist=false` 不落盘 | 观察模式零写盘 |
| 6 | `critic_evaluation_enabled=true` 指标递增 | `learning.eval.total/passed` 计数器 + `learning.eval.score` 直方图 |
| 7 | 评估器抛异常主链路不受影响 | WARNING 降级，不埋点，反思仍返回 |
| 8 | 写入失败降级 | `add` 抛异常 → 反思仍返回，WARNING 记录 |

## 3. 回退机制

| 场景 | 行为 |
| --- | --- |
| `reflection_persist=false`（默认） | 反思不写检索面，行为与现状一致 |
| `experience_persist=false`（默认） | 规划收尾不落盘，仅 INFO 意图日志 |
| `critic_evaluation_enabled=false`（默认） | 不运行规则评估，零 Token 零指标 |
| 反思写入失败 / 评估器异常 / learn 异常 | WARNING 降级，主链路不中断 |
| 运营紧急回滚 | 设环境变量 `LEARNING_REFLECTION_PERSIST=false` / `LEARNING_EXPERIENCE_PERSIST=false` / `CRITIC_EVALUATION_ENABLED=false` 即可（无需发版） |

## 3.1 关键风险提示与缓解

| 风险点 | 说明 | 缓解 / 已知限制 |
| --- | --- | --- |
| `created_at` 被真实 VectorStore 覆盖 | `VectorStore.add()`（`memory/vector_store/vector_store.py:669`）强制 `metadata["created_at"] = 写入时刻`，覆盖本任务传入的 entry.timestamp（差异 < 1s） | TASK-04 消费方按"写入时刻"理解该字段；文档 schema 声明的 `created_at=entry.timestamp` 在生产检索面实际为写入时刻 |
| 检索面反思记录无去重 / 无容量控制 | `reflection_persist=true` 后每条对话写一条 `type=reflection` 记录；`input_hash` 已采集但当前未消费去重 | 已知限制：长会话 / 高频调用会持续膨胀检索面。TASK-04 沉淀管道应引入 input_hash 去重与容量管理 |
| `score=0.0` 语义歧义 | 未启用评估时 `score` 恒 0.0，与"评估分数为 0"无法区分（schema 无 `evaluated` 标记） | TASK-03/04 消费时结合评估开关状态判断；如需区分，后续版本可在 metadata 增补 `evaluated: bool` |
| 环境变量非法值静默关闭开关 | 非法值（不在 `true/1/yes` 集合）按实现视为 False，**会覆盖 config.yaml 的 true（不是回落）** | 运维设置环境变量前核对取值（上线检查清单 §1.2）；`scripts/verify_task02_config_effective.py` 场景 D 已覆盖该行为 |
| ReflectionEngine 每次交互实例化 | `_run_rule_evaluation` 每次 `new ReflectionEngine()`（6 维规则零 Token，构造开销可忽略） | 若未来评估器引入模型加载，需改为模块级单例复用 |

## 4. 灰度步骤

1. 默认状态（三开关均 false）：系统行为与改造前逐字节等价，无需干预；
2. 观察模式验证：设 `LEARNING_EXPERIENCE_PERSIST=true`，执行一个规划任务，确认 `data/reflection/experiences.json` 出现条目且 `get_advice_for_task` 可命中（D17 注入数据源非空）；
3. 反思检索面验证：设 `LEARNING_REFLECTION_PERSIST=true`，交互后检索面可查得 `type=reflection` 记录（含 schema 字段）；
4. 保守评估验证：设 `CRITIC_EVALUATION_ENABLED=true`，确认 metrics 出现 `learning.eval.*` 且随交互递增，响应不被拦截；
5. 异常随时环境变量一键回滚（§3）。

## 5. 验证记录

### 5.1 测试

```
$ python -m pytest tests/unit/test_reflection_pipeline.py -v
8 passed in 2.79s（用例 1-8 全绿）

$ python -m pytest tests/unit/test_reflection_pipeline.py tests/unit/test_planning_wire.py \
    tests/unit/test_planning_reflector.py tests/unit/test_planning_stage4.py -q
59 passed in 6.80s（相关既有回归全绿，无破坏）

$ python scripts/task02_reflection_simulate.py        # 本地模拟验证（见 §5.3）
场景 A/B/C 全 PASS：反思产物写入检索面且可查回，评估指标随交互递增
```

### 5.1.1 完整回归（2026-08-14 全量 tests/unit，分块独立进程）

首次全量单进程随机序跑到 67%（约 7700 项）零失败，被并行会话并发 pytest（tests/ 全量 + 专项，4+ 进程）
资源竞争强杀（无 pytest 汇总，rc=-1）——符合既有教训"并行会话并发跑 pytest 显著恶化慢路径"。
随后分 4 块（`-p no:randomly` 字典序、块间独立、timeout=120）补跑全量：

| 分块 | 结果 |
| --- | --- |
| chunk1 | 3232 passed / 16 skipped / 0 failed |
| chunk2 | 2666 passed / 60 skipped / 4 xpassed / 0 failed |
| chunk3 | 2056 passed / 16 skipped / 0 failed |
| chunk4 | 3417 passed / 204 skipped / 13 xfailed / **1 failed** |
| 合计 | 11371 passed / 296 skipped / 17 xfailed / 4 xpassed / **1 failed** |

唯一失败 `tests/unit/test_skills_mgmt_safety.py::TestAutoRollback::test_triggers_on_error_rate_rise`：
属**并行会话未提交的新模块**（`agent/skills_mgmt/rollback.py` 与测试文件均 untracked，判定 baseline 缺
error_rate 字段导致 error_rate 上升不触发回滚），与本任务改动（orchestrator/planning/config）零交集，
不归本任务处理（工程约定：禁止触碰并行会话文件）。

本任务改动相关定向测试（59 个 planning/reflection + 198 个 orchestrator/cognitive/metrics）全绿。

### 5.2 本地模拟验证（反思产物写入检索面）

`scripts/task02_reflection_simulate.py` 以注入最小依赖方式驱动真实 `orchestrator.self_reflect()`
（不拉起完整 process 链路），用可查回的 FakeVectorStore 验证接线点：

| 场景 | 开关 | 结果 |
| --- | --- | --- |
| A 默认状态 | 两开关 false | 反思照常产出，检索面写入数=0（与现状一致） |
| B 反思持久化 | `reflection_persist=true` | 检索面写入 1 条，metadata schema 完整（type/interaction/task_id/input_hash/score/suggestions/created_at），`search("反思")` 可查回 |
| C 持久化+评估 | 两开关 true | 反思写入 + 规则评估 score=1.0 落入记录，`learning.eval.total/passed` 递增，响应正常返回（保守模式） |

### 5.3 接线点日志清单（排查定位）

核心接线点均补 INFO 日志（log_dict 结构化，action 可 grep）：

| action | 位置 | 内容 |
| --- | --- | --- |
| `orchestrator.self_reflect.learning_gate` | self_reflect 接线入口 | 两开关当前状态（一屏可查"为何未写检索面/未评估"） |
| `orchestrator.self_reflect.eval.start` | 规则评估入口 | 评估被触发 + 入参规模（与下方 WARNING 配对定位） |
| `orchestrator.self_reflect.eval` / `.eval.fallback` | 评估完成 / 异常 | score/passed；异常 WARNING 降级 |
| `orchestrator.self_reflect.persist.start` / `.persist` / `.persist.skipped` / `.persist.fallback` | 写入开始 / 成功 / 检索面未初始化 / 失败 | 交互号 + 反思长度；WARNING 降级 |
| `[经验落盘]` 系列（planning/core.py） | `_record_experience` | 观察模式跳过 / 开始沉淀 / 调用失败 WARNING |

### 5.4 质量门禁

- `python -m agent.observability.arch_rules --check`：✅ 通过（未豁免违规 0；orchestrator→cognitive 引用无新增循环依赖）。
- `python scripts/pre_commit_ci_guard.py --static-only --strict`：⚠️ FAIL 12 条新增 WARN，均为
  `import_degraded` 类型，指向 api_gateway.py / lazy_loader_async.py / optimized_storage.py / chaos_injector.py /
  loki.py 等 **HEAD 存量文件的行号漂移**（`.guard_baseline.json` 记录行号落后于当前文件，如 api_gateway.py:488→521），
  与本次改动文件（orchestrator.py / planning/core.py / config.yaml / test_reflection_pipeline.py）零交集。
  TASK-01 变更说明 §5.2 记录的正是同一批 12 条漂移，属存量基线过期问题，非本任务引入。如需清理，
  应另行统一 `--update-baseline`（影响全仓库，交由维护者决策）。
- 本次改动文件对 guard `import_degraded` 检查零新增（复现扫描 47 条签名与基线 47 条交集，新增 12 条全部来自未修改文件）。

### 5.5 测试过程中的数据保护

本任务测试曾误将经验写入真实 `data/reflection/`（Reflector 的 persist_dir 只取显式参数，PlanningCore 不转发
config 中的 persist_dir，临时目录注入失效），已立即恢复 `experiences.json`（原 `[]`）与 `lessons.json`（删除测试条目），
并在测试中显式 `core.reflector.persist_dir = tmp_dir` 覆盖 + 清空内存库，杜绝再次污染。

## 6. 变更文件清单

| 文件 | 变更类型 |
| --- | --- |
| `config.yaml` | 修改：`learning:` 段新增 `reflection_persist` / `experience_persist`（默认 false，含注释） |
| `agent/orchestrator/orchestrator.py` | 修改：`_load_learning_config()` + `_run_rule_evaluation()` + `_persist_reflection()` + self_reflect 追加接线 + 接线点 INFO 日志 |
| `planning/core.py` | 修改：`_load_experience_persist_config()` + `_record_experience()` + execute_plan 三处收尾接线 + 落盘意图 INFO 日志 |
| `tests/unit/test_reflection_pipeline.py` | 新增：8 用例 |
| `scripts/task02_reflection_simulate.py` | 新增：本地模拟脚本（三场景验证反思写入检索面） |
| `scripts/task02_full_dialogue.py` | 新增：完整对话验证脚本（config 开关真实生效 + 真实检索面写入 + 评估指标递增） |
| `docs/zh/智能体学习机制重构计划/变更说明/TASK-02_上线检查清单.md` | 新增：上线检查清单（人工确认开关配置 + 日志监控点 + 验证/回滚路径） |
| 本文档 | 新增：变更说明（含反思产物 schema，供 TASK-04 对接） |

## 7. 范围外（明确不做）

- 不修改 `planning/reflector.py` 现有方法签名与文件格式（`experiences.json`/`lessons.json` schema 不变）；
- 不修改 `agent/cognitive/` 各评估器内部实现（ActorCritic / Debate / LLM_DRIVEN 本期保持休眠，不启用不实现）；
- 不改 `orchestrator.self_reflect()` 既有判定逻辑与输出格式（仅追加调用）；
- 不改 `workflow_learning` 既有学习/匹配/升格闭环；
- 不触碰并行会话未提交文件（`memory/vector_store/vector_store.py` 等）。
