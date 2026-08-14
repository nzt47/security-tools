# TASK-03 变更说明：学习有效性度量体系

| 字段 | 值 |
| --- | --- |
| 任务编号 | TASK-03 |
| 所属阶段 | 主线阶段 3/5（度量基建，先于 T04/T05 上线以便验收） |
| 涉及主文档 | `docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§3.4 补充项 1、2；§4.4 断点 8 关联） |
| 关联任务书 | `docs/zh/智能体学习机制重构计划/TASK-03_学习有效性度量体系.md` |
| 实现日期 | 2026-08-14 |

## 1. 背景

设计思路只给了可量化判据（肖仰华：相似任务 token 消耗显著下降），缺完整度量体系。审计确认：

1. `agent/monitoring/business_metrics.py` 已有业务指标（用户交互/任务完成/知识库命中/扩展使用）与 `LLMMonitor`（token/耗时环形缓冲），但**没有"学习有效性"指标**；
2. `configs/models.yaml` 声明了 `cost_limits`（max_daily_tokens/max_per_request_tokens）但无强制钩子——多角色评估（MAE 等）一旦引入成本会失控；
3. TASK-02 已埋 `learning.eval.*` 指标族（评估 total/passed/failed + score 直方图），可作为本任务分类型失败率的评估维度数据源。

本任务建立**学习 KPI 体系 + 成本预算护栏**，作为 TASK-04/TASK-05 的验收基准。纯埋点 + 只读聚合：除成本熔断外不改变任何业务行为。

## 2. KPI schema 定义（Step 1 交付物——TASK-04/TASK-05 验收依据）

7 项 KPI 的计算口径与数据源（**写死，下游按此口径消费**）：

| # | KPI | 口径 | 数据源 | 实现位置 |
| --- | --- | --- | --- | --- |
| 1 | token 复用率 | 命中 workflow/skill 时节省 token / 期间总 token（节省+消耗） | orchestrator `_workflow_learning_layer_match` / `_semantic_layer_match` 命中返回 + `_call_llm` 响应 token | `LearningMetrics.record_token_reuse` / `record_llm_tokens` |
| 2 | Skill 命中率 | skill 命中次数 / 语义层查询次数 | skills_mgmt loader `match()` 命中计数 | `record_semantic_query` |
| 3 | 工作流命中率 | workflow 命中次数 / 交互总数 | orchestrator workflow_learning 层 | `record_workflow_match` / `record_interaction` |
| 4 | 分类型失败率 | 按 task_type 统计失败占比 | failure_collector / self_reflect 结果；TASK-02 `learning.eval.*` 聚合 | `record_task_result` + `_get_eval_stats` |
| 5 | 反馈均分趋势 | 近 7 日滑动窗口 feedback.rating 均值（含逐日趋势） | feedback 提交 | `record_feedback` |
| 6 | 沉淀增量 | 新增 Skill/工作流/经验/知识卡片/反思 数量 | 各仓库 CRUD 钩子（skills_mgmt / knowledge / workflow_learning / data/reflection） | `record_artifact` |
| 7 | 进化采纳率 | 采纳变异体数 / 候选变异体数 | offline_evolver（TASK-05 接入后产生） | `record_evolution_candidate` |

命名约定：指标统一 `learning.*` 前缀 snake_case，避免与 `business_metrics` 冲突（metrics 层禁止被 tools 层依赖，见 §5 工程约束）。

### 2.1 指标清单（写入 MetricsCollector 的透出通道）

| 指标名 | 类型 | 说明 |
| --- | --- | --- |
| `learning.interactions.total` | counter | 交互总数 |
| `learning.workflow.queries` / `learning.workflow.hits` | counter | 工作流层查询/命中 |
| `learning.semantic.queries` / `learning.semantic.hits` | counter | 语义层查询/命中 |
| `learning.token.total` / `learning.token.saved` | counter | 期间总 token / 复用节省 token |
| `learning.token.per_call` | histogram | 单次 LLM 调用 token |
| `learning.task.{task_type}.total` / `.failed` | counter | 分类型任务结果 |
| `learning.feedback.total` / `learning.feedback.rating` | counter / histogram | 反馈提交与评分 |
| `learning.artifacts.{skill\|workflow\|experience\|knowledge_card\|reflection\|other}` | counter | 沉淀增量 |
| `learning.evolution.candidates` / `learning.evolution.adopted` | counter | 进化采纳率（TASK-05） |
| `learning.eval.total` / `.passed` / `.failed` + `learning.eval.score` | counter / histogram | TASK-02 既有，本任务只聚合 |

## 3. 改动点

### 3.1 新增 `agent/learning_metrics.py`（KPI 聚合单例）

- `LearningMetrics` 类：构造注入 `collector`（默认 None → 全局 MetricsCollector）+ `enabled` 开关；内部 RLock 本地聚合；
- 9 个埋点方法全部内部 try/except 兜底，**埋点异常绝不影响主链路**（验收项）；
- `get_snapshot(days=7)` 只读聚合视图：`{generated_at, days, kpis, evaluation, trend_7d}`，7 项 KPI 全部非空且数值自洽；
- 单例遵 `singleton_manager` 规范：注册名 `"learning_metrics"`，工厂 `_create_learning_metrics(config=None)`；
  `get_learning_metrics()` / `reset_learning_metrics()`（测试用）；无 singleton_manager 时 fallback 模块级全局变量。

### 3.2 埋点接入（最小侵入，不改既有接口/返回值）

| 位置 | 埋点 |
| --- | --- |
| orchestrator `_workflow_learning_layer_match` 命中/未命中 | `record_workflow_match(hit, saved_tokens)` |
| orchestrator `_semantic_layer_match` 命中处 | `record_semantic_query(hit, saved_tokens)` |
| orchestrator `_call_llm` / `_call_llm_v2` 响应后 | `record_llm_tokens`（估算 token） |
| orchestrator `process()` 交互入口 | `record_interaction()` |
| orchestrator `_run_rule_evaluation` | 既有 `learning.eval.*` 直接聚合（不改） |
| feedback `submit_feedback` | `record_feedback(rating)` |
| skills_mgmt / knowledge / workflow_learning / reflection 写钩子 | `record_artifact(type)`（TASK-04 沉淀管道接通后消费） |

所有接入点均 `try/except` 兜底：`get_learning_metrics()` 或埋点方法异常 → 忽略，主链路零影响（验收项"埋点全部挂掉时主链路零影响"）。

### 3.3 新增 `agent/learning_metrics_api.py`（查询面）

- Flask blueprint（参照 `agent/health/dashboard.py` 风格），路由 `GET /api/learning/metrics` 返回 `LearningMetrics.get_snapshot()` JSON（只读）；
- 接入 `app_server.py` 既有 blueprints 注册处（最小改动）。

#### 3.3.1 响应字段定义（7 项 KPI）

顶层结构：`{generated_at, days, kpis, evaluation, trend_7d}`

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `generated_at` | string(ISO) | 快照生成时间 |
| `days` | int | 统计窗口天数（默认 7） |
| `kpis` | object | 7 项 KPI（见下表） |
| `evaluation` | object | TASK-02 `learning.eval.*` 聚合（total/passed/failed/failure_rate） |
| `trend_7d` | array | 近 N 日逐日趋势 `{date, interactions, feedback_count, feedback_avg}`（无数据日期不输出） |

`kpis` 7 项字段定义：

| KPI | 字段 | 类型 | 说明 |
| --- | --- | --- | --- |
| 1 token 复用率 | `token_reuse_rate.saved_tokens` | int | 期间节省 token（workflow/skill 命中估算） |
| | `token_reuse_rate.total_tokens` | int | 期间总 token（节省+实际消耗） |
| | `token_reuse_rate.rate` | float(4dp) | saved / total；total=0 时为 0.0 |
| 2 Skill 命中率 | `skill_hit_rate.queries` | int | 语义层查询次数 |
| | `skill_hit_rate.hits` | int | 命中次数 |
| | `skill_hit_rate.rate` | float(4dp) | hits / queries；queries=0 时为 0.0 |
| 3 工作流命中率 | `workflow_hit_rate.interactions` | int | 交互总数（分母，含 workflow 未查询的交互） |
| | `workflow_hit_rate.hits` | int | workflow 层命中次数 |
| | `workflow_hit_rate.rate` | float(4dp) | hits / interactions；interactions=0 时为 0.0 |
| 4 分类型失败率 | `failure_rate_by_task_type.{type}.total` | int | 该 task_type 任务总数 |
| | `failure_rate_by_task_type.{type}.failed` | int | 失败数 |
| | `failure_rate_by_task_type.{type}.rate` | float(4dp) | failed / total；total=0 时为 0.0 |
| 5 反馈均分趋势 | `feedback_rating_trend.count` | int | 当前窗口内反馈条数 |
| | `feedback_rating_trend.window_days` | int | 窗口天数 |
| | `feedback_rating_trend.current_avg` | float(2dp) | 当前窗口均分；无反馈为 0.0 |
| | `feedback_rating_trend.previous_avg` | float(2dp) | 前一窗口均分（环比基准）；无反馈为 0.0 |
| | `feedback_rating_trend.by_day` | array | 逐日 `{date, interactions, feedback_count, feedback_avg}` |
| 6 沉淀增量 | `artifact_delta.skill/workflow/experience/knowledge_card/reflection/other` | int | 固定 6 类产物新增计数（未知类型归入 other） |
| 7 进化采纳率 | `evolution_adoption_rate.candidates` | int | 变异体候选数 |
| | `evolution_adoption_rate.adopted` | int | 采纳数 |
| | `evolution_adoption_rate.rate` | float(4dp) | adopted / candidates；candidates=0 时为 0.0 |

#### 3.3.2 示例响应（50 次混合交互的真实聚合）

```json
{
  "generated_at": "2026-08-14T15:01:12",
  "days": 7,
  "kpis": {
    "token_reuse_rate": {"saved_tokens": 60000, "total_tokens": 120000, "rate": 0.5},
    "skill_hit_rate": {"queries": 33, "hits": 13, "rate": 0.3939},
    "workflow_hit_rate": {"interactions": 50, "hits": 17, "rate": 0.34},
    "failure_rate_by_task_type": {
      "qa": {"total": 50, "failed": 10, "rate": 0.2}
    },
    "feedback_rating_trend": {
      "count": 20, "window_days": 7,
      "current_avg": 4.5, "previous_avg": 0.0,
      "by_day": [
        {"date": "2026-08-14", "interactions": 50, "feedback_count": 20, "feedback_avg": 4.5}
      ]
    },
    "artifact_delta": {"skill": 5, "workflow": 45, "experience": 0, "knowledge_card": 0, "reflection": 0, "other": 0},
    "evolution_adoption_rate": {"candidates": 50, "adopted": 13, "rate": 0.26}
  },
  "evaluation": {"total": 0, "passed": 0, "failed": 0, "failure_rate": 0.0},
  "trend_7d": [
    {"date": "2026-08-14", "interactions": 50, "feedback_count": 20, "feedback_avg": 4.5}
  ]
}
```

> 示例说明：`scripts/demo_learning_metrics.py` 模拟 50 次混合交互（workflow 命中 17、语义层查询 33 命中 13、LLM 消耗 60k、qa 失败 10、反馈 20 条均分 4.5、沉淀 skill 5 / workflow 45、进化采纳 13）后，`/api/learning/metrics` 的真实返回即此结构。

### 3.4 新增 `agent/learning_budget.py`（成本预算护栏）

- `LearningBudget`：读 `configs/models.yaml` 的 `cost_limits`（max_daily_tokens / max_per_request_tokens）+ config.yaml `learning.budget.*`；
- `with_budget(action_name)` 上下文管理器：
  - 超单次上限 → 立即拒绝该学习动作并 WARNING；
  - 超日预算 → 熔断后续学习动作（可配置恢复时间）；
- 熔断复用 `rate_limiter`（TokenBucket 令牌桶，日预算按剩余令牌判断）与 `circuit_breaker`（OPEN 状态）现有 API 组装，**不新造语义**；
- 默认 `mode: warn_only`（只记录超限告警不拦截）；显式配置 `mode: enforce` 才实际拦截（防止上线即改行为）。

### 3.5 配置（config.yaml）

在 `learning:` 段新增：

| 配置键 | 默认值 | 说明 |
| --- | --- | --- |
| `learning.budget.mode` | `warn_only` | 预算模式：`warn_only`（只记录） / `enforce`（拦截） |
| `learning.budget.max_single_action_tokens` | 读取 `models.yaml cost_limits.max_per_request_tokens` | 单次学习动作 token 上限 |
| `learning.budget.max_daily_tokens` | 读取 `models.yaml cost_limits.max_daily_tokens` | 日预算 |
| `learning.budget.recovery_seconds` | 3600 | 熔断恢复时间（秒） |

优先级遵循项目既有约定：**环境变量 > config.yaml > 硬编码默认值**。
对应环境变量：`LEARNING_BUDGET_MODE` / `LEARNING_BUDGET_MAX_SINGLE_ACTION_TOKENS` / `LEARNING_BUDGET_MAX_DAILY_TOKENS` / `LEARNING_BUDGET_RECOVERY_SECONDS`。

## 4. 测试

新增 `tests/unit/test_learning_metrics.py` + `tests/unit/test_learning_budget.py`（合计 ≥ 10 用例）：

| # | 用例 | 验证点 |
| --- | --- | --- |
| 1 | 50 次混合交互 KPI 聚合 | 模拟 50 次交互（含 workflow/skill 命中、失败、反馈），7 项 KPI 全部非空且数值自洽 |
| 2 | token 复用率口径有效 | 有命中会话 vs 无命中会话复用率差异 > 20 个百分点 |
| 3 | 埋点异常零影响 | mock 埋点全部抛异常，主链路调用正常返回 |
| 4 | get_snapshot 只读 | 调用前后状态一致，不产生写操作 |
| 5 | 单次超限拒绝（enforce） | 超过单次上限的学习动作被拒绝 + WARNING |
| 6 | 日预算耗尽熔断（enforce） | 熔断后续动作，恢复时间后放行 |
| 7 | warn_only 不拦截 | 超限仅记录，动作照常执行 |
| 8 | 恢复后放行 | 熔断窗口过后预算恢复可执行 |
| 9 | API 200 且含 7 项 KPI | mock 请求 `/api/learning/metrics` |
| 10 | reset 清理 | 重置后快照归零 |

## 5. 工程约束

- 不修改 `BusinessMetrics` / `LLMMonitor` / `MetricsCollector` 现有接口与指标（仅扩展/新增）；
- 不修改 `rate_limiter` / `circuit_breaker` 现有语义（按现有 API 组装）；
- 保留"环境变量 > config.yaml > 硬编码默认值"配置优先级；
- 新增模块符合架构规则：observability/metrics 层禁止被 tools 层依赖（参照 `agent/monitoring/` 现有分层）；
- 指标命名 `learning.*` snake_case，避免与 `business_metrics` 冲突。

## 6. 回退机制

| 场景 | 行为 |
| --- | --- |
| `learning.budget.mode=warn_only`（默认） | 只记录超限告警，不拦截任何学习动作 |
| 埋点异常 / collector 不可用 | 静默降级（try/except），主链路零影响 |
| API 查询异常 | 返回 500（只读接口不影响主链路） |
| 运营紧急回滚 | 设环境变量 `LEARNING_BUDGET_MODE=warn_only`（默认已宽松）；移除 API 注册行即可下线查询面 |

## 7. 验证记录

### 7.1 冒烟验证（learning_metrics 模块）

模拟 1 交互 + 1 workflow 命中（saved=1200）+ 1 skill 命中（saved=300）+ 5000 消耗 token + 2 次 qa 任务（1 失败）+ 1 反馈 5 分 + 1 skill 沉淀 + 2 进化候选（1 采纳）：

```
token_reuse_rate:      saved 1500 / total 6500 = 0.2308 ✓
skill_hit_rate:        1/1 = 1.0 ✓
workflow_hit_rate:     1/1 = 1.0 ✓
failure_rate_by_task_type: qa 1/2 = 0.5 ✓
feedback_rating_trend: current_avg 5.0 ✓
artifact_delta:        skill=1 ✓
evolution_adoption_rate: 1/2 = 0.5 ✓
```

### 7.2 测试与质量门禁（Step 6 完成记录）

**新增用例（TASK-03 专项，20/20 全绿）**：

```
$env:DISABLE_NATIVE_EXT="1"; python -m pytest tests/unit/test_learning_metrics.py tests/unit/test_learning_budget.py -q
20 passed in 4.09s
```

- `test_learning_metrics.py`（10 用例）：50 次混合交互 7 项 KPI 自洽（token 复用率 0.26 口径、skill/workflow 命中 25/50、qa 失败 17/50、反馈 10 条、沉淀 skill 5、进化采纳 13/50）、命中 vs 无命中复用率差 >20pp、埋点全挂零影响、单例异常安全包装、get_snapshot 只读、API 200 + 7 KPI、API 500、TASK-02 `learning.eval.*` 聚合、reset、disabled；
- `test_learning_budget.py`（10 用例）：默认 warn_only（不变式）、非法 mode 回退、单次超限拒绝（enforce）、日预算耗尽熔断 + OPEN、warn_only 不拦截不熔断、cooldown 后半开探测恢复 CLOSED、正文异常释放预留不熔断、spend() 消耗与熔断、配置三层优先级（env 覆盖）、模块级单例懒加载。

**定向回归（orchestrator/feedback 埋点防破坏，190/190 全绿）**：

```
python -m pytest tests/unit/test_orchestrator_reject.py tests/unit/test_orchestrator_concurrency.py \
  tests/unit/test_orchestrator_refactor.py tests/unit/test_orchestrator_workflow_learning_layer.py \
  tests/unit/test_distill_feedback.py tests/unit/test_feedback_engineering.py tests/unit/test_feedback_skill_binding.py -q
190 passed in 16.62s
```

**质量门禁**：

```
python scripts/pre_commit_ci_guard.py --static-only --strict   → FAIL=0，基线内豁免 47，新增阻断 0（通过）
python -m agent.observability.arch_rules --check              → ✅ 通过（4 项违规均为既存豁免项）
```

说明：静态门禁最初检出 14 条新增 `import_degraded` WARN——经比对，1 条为真实新增（`learning_metrics.py:410` SingletonManager 降级分支无告警，已补 `logger.warning` 修复），其余 13 条为**同文件行号漂移**（feedback.py 埋点 +7 行、并行会话改动 api_gateway/lazy_loader_async/optimized_storage 等导致的存量条目行号变化），已按项目惯例更新 `.guard_baseline.json` 行号，无新增豁免范围。

### 7.3 全量回归

```
python -m pytest tests/unit -q -p no:randomly
= 11404 passed, 296 skipped, 13 xfailed, 4 xpassed, 36 warnings, 1 error in 1684.11s (0:28:04) =
```

- **11404 passed / 0 真实失败**；TASK-03 新增 20 用例全部包含在 passed 内。
- **1 error 为收集期错误**：`tests/unit/test_skills_mgmt_safety.py` 收集失败（`ImportError: cannot import name 'get_evolution_audit' from 'agent.health.dashboard'`）。经核对：该导入指向 `agent/health/dashboard.py`，TASK-03 改动文件（learning_metrics / learning_budget / learning_metrics_api / config.yaml / app_server.py / orchestrator / feedback / 2 个测试文件 / .guard_baseline.json）与该模块零交集，判定为并行会话（其他任务）对 dashboard 的中间态改动引入，非本任务缺陷；单独验证 TASK-03 20 用例 20/20 全绿。
- 判定依据遵循项目惯例：pytest 汇总行（`=+ N passed`）而非进程退出码——TRAE Sandbox 拦截 teardown 对 `C:\nonexistent` 的写入使 rc=1，但不影响测试结论。

## 8. 变更文件清单

| 文件 | 变更类型 |
| --- | --- |
| `agent/learning_metrics.py` | 新增：KPI 聚合单例（9 埋点方法 + get_snapshot + SingletonManager 注册） |
| `agent/learning_budget.py` | 新增：成本预算护栏（LearningBudget + with_budget 上下文，复用 rate_limiter/circuit_breaker） |
| `agent/learning_metrics_api.py` | 新增：`/api/learning/metrics` 只读接口（blueprint） |
| `config.yaml` | 修改：`learning:` 段新增 `budget.*` 配置（mode/max_single_action_tokens/max_daily_tokens/recovery_seconds） |
| `app_server.py` | 修改：注册 `learning_metrics_bp`（最小改动） |
| `agent/orchestrator/orchestrator.py` | 修改：最小侵入埋点（workflow/skill 命中、LLM token、交互计数） |
| `agent/feedback.py` | 修改：`submit_feedback` 追加 `record_feedback` 埋点 |
| `tests/unit/test_learning_metrics.py` | 新增：KPI 聚合测试 |
| `tests/unit/test_learning_budget.py` | 新增：预算护栏测试 |
| `.guard_baseline.json` | 修改：13 条 `import_degraded` 行号漂移更新（learning_metrics.py 修复后不再检出） |
| 本文档 | 新增：变更说明（含 KPI schema 定义表） |

## 9. 范围外（明确不做）

- 不修改 `BusinessMetrics` / `LLMMonitor` / `MetricsCollector` / `rate_limiter` / `circuit_breaker` 既有接口；
- 不接入 TASK-04 沉淀管道 / TASK-05 offline_evolver（`record_artifact` / `record_evolution_candidate` 预留接口，待对应任务接入）；
- 不改变任何业务行为（除成本熔断外纯埋点+只读）；
- 不触碰并行会话文件。
