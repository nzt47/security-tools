# 任务2：学习KPI数据源补齐与触发条件监控

## 0. 任务标识

| 字段 | 值 |
|---|---|
| 任务编号 | 任务2（自进化机制重构计划 · 阶段一） |
| 所属计划 | `docs/zh/进化机制重构计划/自进化机制重构计划/进化机制理想设计.md` |
| 前置依赖 | 无（建议早于任务3 上线，供放行判据使用） |
| 并行建议 | 与任务1 并行 |
| 下游依赖 | 任务3（放行判据）、任务4（护栏联动）、任务5（Judge 触发）、任务7（复杂度 KPI） |

## 1. 背景（为什么做）

TASK-08 报告 §3.3/§5.2 的全部远期触发条件声称"依托 TASK-03 7 项 KPI 计算，无主观门槛"。但审计发现**多项 KPI 在当前接线下无数据源或口径缺失**：

| KPI | 现状（2026-08-21 代码审计） |
|---|---|
| KPI#1 token 复用率 | 有数据源：`record_workflow_match` → `record_token_reuse`（orchestrator 已接线）；`record_semantic_query` **无调用方** |
| KPI#2 Skill 命中率 | 有数据源（workflow/skill 匹配路径） |
| KPI#3 工作流命中率 | 有数据源（`record_workflow_match`） |
| KPI#4 分类型失败率 | **`record_task_result` 在全部生产代码中零调用方 → 恒空**；报告 §5.2"KPI#4 连续 4 周 >30%"的触发条件结构性无法满足 |
| KPI#5 反馈均分趋势 | 有数据源（`agent/feedback.py` L345 → `record_feedback`） |
| KPI#6 沉淀增量 | 有数据源（`agent/knowledge/skill_bridge.py`、`precipitate.py` → `record_artifact`） |
| KPI#7 进化采纳率 | `evolution_scheduler.py` 仅在非 dry-run 时写 `record_evolution_candidate`（当前 dry_run=true → 恒 0）；**无分母口径与最小候选基数** |
| 持久化 | LearningMetrics SQLite 持久化已实现但**默认关闭**，`config.yaml` 未配置 → "连续 4 周"跨重启统计不可得 |

本任务补齐数据源、定义 KPI#7 口径、启用持久化，并建设**触发条件监控**（周级滚动统计 + 面板 + 告警），使报告 §5.2 触发条件真正可计算。

## 2. 目标描述（做什么）

1. 补齐 7 项 KPI 的生产数据源（重点：`record_task_result` 接线、`record_llm_tokens`/`record_semantic_query` 接线）。
2. 定义并实现 **KPI#7 口径**：分母 = 周期进化候选数，纳入统计需满足最小候选基数（如连续 4 周每周候选 ≥N，N 可配置默认 5）；与任务3 放行联动（放行后才产生真实候选）。
3. **持久化正式启用**：`config.yaml` 增加 `learning.metrics.persistence` 配置段（默认关、灰度开启），跨重启累积"连续 4 周"统计。
4. **触发监控**：周级滚动统计查询 + `/api/learning/metrics` 扩展 + 监控面板/告警（复用 Prometheus/Grafana 既有设施），覆盖报告 §5.2 全部触发条件。
5. 产出触发条件计算说明文档（口径/边界/示例）。

## 3. 不变式约束（不易——禁止触碰）

- 埋点纯计数、只读聚合，**不改变任何业务行为**（TASK-03 原不变式继续有效）；埋点异常零影响主链路。
- 禁止修改 `LearningMetrics` 已有 KPI 聚合口径与 `get_snapshot()` 输出结构（下游 `/api/learning/metrics` 依赖）；新增统计走扩展字段。
- 持久化保持"默认关闭 + 显式启用"，启用后 I/O 不进锁、DB 失败自动降级（沿用 `3f37612d` 设计）。
- 触发条件"可度量"口径与报告 §5.2 一致；若修正口径（如 KPI#4 复杂度维度），必须在变更说明中披露（仿照报告 §8 自检风格）。

## 4. 执行步骤

### Step 1：数据源盘点
- 阅读 `agent/learning_metrics.py`（7 项 KPI 聚合 + 持久化实现）与 `agent/orchestrator/orchestrator.py` 埋点调用点，确认已接线/未接线清单（见背景表）。

### Step 2：KPI 数据源补齐
- **KPI#4**：在 orchestrator 任务执行收尾与 feedback 失败路径接入 `record_task_result(task_type, success)`；`task_type` 取自路由/任务元数据（`task_type` 字段，无则归 `other`）。若规划 wire 已产出复杂度，追加 `judged_complexity` 扩展键（为任务7 复杂度维度预留）。
- **KPI#1 语义查询路径**：`record_semantic_query` 接入 `knowledge/search.py` 命中处（观察模式）。
- **token 计量**：`record_llm_tokens` 接入 `LLMMonitor` 旁路（不改变 LLMMonitor 本身）。

### Step 3：KPI#7 口径定义与实现
- 定义采纳率 = 周期内被采纳候选数 / 周期内进化候选数；候选基数门槛：周候选数 < N（默认 5）时该周采纳率标记 `insufficient_data`（不参与"连续 4 周"统计）。
- `record_evolution_candidate` 增加候选计数侧通道；`get_snapshot()` 的 `evolution_adoption_rate` 增加 `candidates`、`insufficient_data` 字段（向后兼容）。

### Step 4：持久化正式启用（灰度）
- `config.yaml` 新增 `learning.metrics.persistence`（默认 `enabled: false`；`enabled: true` 时走 `LEARNING_METRICS_PERSIST_*` 环境变量），灰度开启后验证跨重启累积与"连续 4 周"统计。

### Step 5：触发监控
- 新增周级滚动统计查询接口（扩展 `/api/learning/metrics` 或新增只读端点）：以周为单位输出 7 项 KPI 时间序列，标注触发条件命中状态（报告 §5.2 五条触发条件逐条映射）。
- 监控面板：Grafana Dashboard 新增"学习触发条件"面板（复用 `monitoring/` 既有设施）；告警规则覆盖 §5.2 触发条件阈值（连续 4 周判定由查询层计算，告警引用其结果）。

### Step 6：测试与文档
- 新增单测：KPI#4 接线（task_type 计数）、KPI#7 口径（基数不足不计入、跨 4 周滚动）、持久化启用后跨重启恢复、触发条件命中计算（构造示例数据验证 §5.2 每条触发条件可被计算）。
- 产出触发条件计算说明 + 变更说明。

## 5. 预期成果（交付物）

1. 7 项 KPI 全部具备生产数据源（代码审计可证每项至少一个调用方）。
2. KPI#7 口径实现（含候选基数门槛与 `insufficient_data` 语义）。
3. `learning.metrics.persistence` 配置段 + 灰度启用验证记录。
4. 触发条件监控面板/告警 + 周级滚动统计端点。
5. 变更说明 + 触发条件计算说明文档。

## 6. 评估标准（验收条件）

### 内容验收
- [ ] 每项 KPI 至少有一个生产调用方（git grep 审计 + 单测覆盖）。
- [ ] KPI#4 在 orchestrator 任务收尾路径被写入（单测证明：构造成功/失败任务 → 分类型失败率变化正确）。
- [ ] KPI#7 在候选基数不足时标记 `insufficient_data`，不进入"连续 4 周"统计（单测证明）。
- [ ] 持久化启用后，模拟重启（新实例加载 DB）数据不丢，"连续 4 周"窗口可累计（单测证明）。
- [ ] §5.2 五条触发条件逐条可由监控查询计算（示例数据演示，无"主观判断型"门槛）。

### 过程验收
- [ ] 埋点路径零业务行为变化（默认路径零新增 LLM 调用，LLMMonitor 断言）。
- [ ] 持久化 I/O 不进锁；DB 失败自动降级 + warning（沿用既有设计，测试证明）。
- [ ] 全量回归通过：`python -m pytest tests/unit -q` 全绿。

## 7. 工程约束（仓库规则）

- 新增/修改限：`agent/learning_metrics.py`（扩展字段，禁止改既有口径）、orchestrator 埋点调用点、`knowledge/search.py` 观察埋点、`config.yaml`、`agent/learning_metrics_api.py`、`monitoring/` 面板与告警文件。
- 所有开关进 `config.yaml`/`.env`（`LEARNING_METRICS_PERSIST_*`），遵循既有优先级约定。
- git 提交走精确路径 add；文档提交参照既有 `docs/zh/` 风格。
