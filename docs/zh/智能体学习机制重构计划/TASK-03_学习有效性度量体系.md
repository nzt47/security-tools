# TASK-03：学习有效性度量体系

## 0. 任务标识

| 字段 | 值 |
|---|---|
| 任务编号 | TASK-03 |
| 所属阶段 | 主线阶段 3/5（度量基建，先于 T04/T05 上线以便验收） |
| 前置依赖 | 无（建议在 TASK-04/TASK-05 之前完成） |
| 并行建议 | 可与 TASK-01、TASK-02、TASK-07 并行 |
| 涉及主文档 | `docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§3.4 补充项 1、2；§4.4 断点 8 关联） |

## 1. 背景（为什么做）

设计思路只给了一个可量化判据（肖仰华：相似任务 token 消耗显著下降），缺完整度量体系。审计发现云枢 `agent/monitoring/business_metrics.py` 已有业务指标（用户交互/任务完成/知识库命中/扩展使用）和 `LLMMonitor`（token/耗时环形缓冲），但**没有"学习有效性"指标**；`configs/models.yaml` 声明了 `cost_limits` 但无强制钩子——MAE 等多角色评估一旦引入（远期），成本会失控。

本任务建立**学习 KPI 体系 + 成本预算护栏**，作为 T04/T05 的验收基准（没有度量，就无法证明"学习有效"）。

## 2. 目标描述（做什么）

1. 定义并实现 7 项学习 KPI 埋点：token 复用率、Skill 命中率、工作流命中率、分类型失败率、反馈均分趋势、沉淀增量、进化采纳率。
2. 提供查询面：新 API `/api/learning/metrics`（或挂载既有 dashboard blueprint）。
3. 实现**学习动作成本预算与熔断**：单次学习动作 token 上限 + 日预算，超限熔断（复用 `rate_limiter` / `circuit_breaker`），并把 `configs/models.yaml` 的 `cost_limits` 从"声明"变为"执行"。

## 3. 不变式约束（不易——禁止触碰）

- **禁止修改** `BusinessMetrics` / `LLMMonitor` / `MetricsCollector` 现有接口与指标（只能扩展/新增模块或子类，不破坏既有消费者）。
- **禁止修改** `rate_limiter` / `circuit_breaker` 现有语义（只能按它们的现有 API 组装"学习预算"用例）。
- **保留** 项目"环境变量 > config.yaml > 硬编码默认值"配置优先级。
- 本任务**纯埋点+只读聚合**：除成本熔断外，不改变任何业务行为；`metrics` 查询 API 为只读。
- `cost_limits` 的强制化必须**默认宽松**（只记录超限告警，不拦截），显式配置拦截阈值后才拦截——防止上线即改行为。

## 4. 执行步骤

### Step 1：定义 KPI schema（先文档后代码）
在变更说明中先行定义 7 项 KPI 的计算口径与数据源：

| KPI | 口径 | 数据源 |
|---|---|---|
| token 复用率 | 命中 workflow/skill 时节省 token / 期间总 token | orchestrator + LLMMonitor |
| Skill 命中率 | skill 命中次数 / 语义层查询次数 | skills_mgmt loader 埋点 |
| 工作流命中率 | workflow 命中次数 / 交互总数 | orchestrator workflow_learning 层 |
| 分类型失败率 | 按 task_type 统计失败占比 | failure_collector / self_reflect 结果 |
| 反馈均分趋势 | 周滑动窗口 feedback.rating 均值 | feedback.db |
| 沉淀增量 | 新增 Skill/工作流/经验/知识卡片数量 | 各仓库 CRUD 钩子 |
| 进化采纳率 | 采纳变异体数 / 候选变异体数 | offline_evolver（T05 接入后产生） |

### Step 2：埋点落地
- 新增模块 `agent/learning_metrics.py`：`LearningMetrics` 单例（遵 `singleton_manager` 规范），聚合上述指标，内部用 `MetricsCollector` 的 histograms/counters 存储，含 `get_snapshot()` / `to_dict()`。
- 埋点位置（最小侵入）：
  - orchestrator：LLM 调用前后 token 差、workflow 命中、skill 命中、规划路由（TASK-01 的 `routed_by` 元数据）；
  - skills_mgmt loader `match()`：命中计数；
  - `self_reflect` / `ReflectionEngine` 评估：失败率（TASK-02 已埋 `learning.eval_*`，此处聚合）；
  - feedback 提交：均分趋势；
  - 各仓库 CRUD：沉淀增量（挂 `skills_mgmt` / `knowledge` / `workflow_learning` / `data/reflection` 的写钩子，或用既有 emit_metric）。
- 所有埋点必须 try/except 兜底，埋点异常绝不影响主链路。

### Step 3：查询面
- 新增 Flask blueprint `agent/learning_metrics_api.py`（参照 `agent/health/dashboard.py` 风格），路由 `/api/learning/metrics` 返回 `LearningMetrics.get_snapshot()` JSON（含各项 KPI 与近 7 日趋势）。
- 接入既有 server 注册处（最小改动）。

### Step 4：成本预算护栏
- 新增 `agent/learning_budget.py`：`LearningBudget`（读 `configs/models.yaml` 的 `cost_limits` + 新配置 `learning.budget`），维护"单次动作 token 上限 / 日预算 / 熔断状态"。
- 封装 `with_budget(action_name)` 上下文：超单次上限 → 立即拒绝该学习动作并 WARNING；超日预算 → 熔断后续学习动作（可配置恢复时间）。
- 熔断实现复用 `rate_limiter`（令牌桶）或 `circuit_breaker`（OPEN 状态），按现有 API 组装，不新造。
- 默认策略：`mode: warn_only`（只记录），`mode: enforce` 才实际拦截（配置开关）。

### Step 5：补测试（TDD）
新增 `tests/unit/test_learning_metrics.py` + `tests/unit/test_learning_budget.py`：
- 指标：模拟 50 次交互（含 workflow/skill 命中、失败、反馈），断言各 KPI 计算正确。
- 预算：单次超限拒绝；日预算耗尽熔断；warn_only 模式不拦截；恢复后放行。
- 查询面：mock 请求 `/api/learning/metrics` 返回 200 且含 7 项 KPI。

### Step 6：回归与门禁
- `python -m pytest tests/unit -q` 全绿；新用例全绿；质量门禁见 §6。

## 5. 预期成果（交付物）

1. `agent/learning_metrics.py`（KPI 聚合单例）+ `agent/learning_budget.py`（预算熔断）。
2. `agent/learning_metrics_api.py`（`/api/learning/metrics` 只读接口）。
3. `config.yaml` 新增 `learning.budget.*` 配置；`configs/models.yaml` cost_limits 接入执行。
4. 测试：`test_learning_metrics.py` + `test_learning_budget.py`（合计 ≥ 10 用例）。
5. 变更说明：`docs/zh/智能体学习机制重构计划/变更说明/TASK-03_变更说明.md`（含 KPI schema 定义表——T04/T05 的验收依据）。

## 6. 评估标准（验收条件）

### 功能验收
- [ ] 模拟 50 次混合交互后，`/api/learning/metrics` 返回 7 项 KPI 全部非空且数值可解释（自洽）。
- [ ] token 复用率在有 workflow/skill 命中的会话中显著高于无命中会话（>20 个百分点即可，证明口径有效）。
- [ ] `mode=enforce` 时，超过单次日预算的学习动作被拒绝并告警；`mode=warn_only` 时仅记录。
- [ ] 埋点全部挂掉（mock 异常）时主链路零影响。

### 测试要求
- [ ] 新增 ≥ 10 用例全部通过；`python -m pytest tests/unit -q` 全绿。

### 质量门禁
- [ ] `python scripts/pre_commit_ci_guard.py --static-only --strict` 零新增告警。
- [ ] `python -m agent.observability.arch_rules --check` 通过。

## 7. 工程约束（仓库规则）

- 同 TASK-01 §7（git 精确路径、commit 走 -F、hook 环境变量、勿碰并行会话文件、UTF-8 无 BOM）。
- 本任务新增模块须符合架构规则（observability/metrics 层禁止被 tools 层依赖等，参照 `agent/monitoring/` 现有分层）。
- 指标命名遵循既有 `snake_case` 与 `learning.*` 前缀约定，避免与 `business_metrics` 冲突。
