# 任务2：学习KPI数据源补齐与触发条件监控 — 变更说明

> 计划：`docs/zh/进化机制重构计划/自进化机制重构计划/进化机制理想设计.md`（阶段一）
> 日期：2026-08-22
> 状态：已实现（含单测 19 例新增全绿 + 既有 18 例回归通过）

---

## 一、背景（为什么做）

TASK-08 报告 §3.3/§5.2 的全部远期触发条件声称"依托 TASK-03 7 项 KPI 计算，无主观门槛"，
但 2026-08-21 全量代码审计发现多项 KPI 在当前接线下**无数据源或口径缺失**：

| KPI | 审计结论（2026-08-21） | 本任务处理 |
|---|---|---|
| KPI#1 token 复用率 | `record_workflow_match` 已接线；`record_semantic_query` 需补知识检索通道 | knowledge/search.py 观察埋点（开关默认关） |
| KPI#4 分类型失败率 | **`record_task_result` 在全部生产代码中零调用方 → 恒空**，§5.2"连续 4 周 >30%"结构性无法满足 | orchestrator 全部任务收尾路径 + feedback 成功/失败路径接线 |
| KPI#7 进化采纳率 | 无分母口径与最小候选基数；dry-run 时恒 0 | 定义最小候选基数（默认 5）+ `insufficient_data` 语义 |
| 持久化 | SQLite 已实现但默认关闭，config.yaml 未配置 → "连续 4 周"跨重启统计不可得 | `learning.metrics.persistence` 配置段（默认关、灰度开启） |
| 触发监控 | 无周级滚动统计、无面板、无告警 | 周级查询 + `/api/learning/metrics/{weekly,trigger}` + Prometheus gauge + Grafana 面板 + 告警规则 |

本任务使报告 §5.2 五条触发条件**真正可计算**，并为任务3（放行判据）、任务4（护栏联动）、
任务5（Judge 触发）、任务7（复杂度 KPI）提供数据基座。

## 二、交付内容

### 1. KPI 数据源补齐（Step 2）

**KPI#4 `record_task_result` 生产接线**（`agent/orchestrator/orchestrator.py`）：

- orchestrator `process()` 全部 10 处任务收尾路径接入 `_emit_learning_metric("record_task_result", ...)`，
  `task_type` 按路由层划分（独立分类，不互相污染）：
  `input_guard`（输入护栏拦截，失败）、`workflow`（规则层命中，成功）、
  `behavior_reject`（行为/人格拒绝，失败）、`template`（模板层，成功）、
  `workflow_learning`（工作流学习层，成功）、`semantic`（语义层，成功）、
  `reject`（未知意图软拒识，成功——系统正常兜底响应）、
  `llm`（LLM 路径：成功 / 失败 / 低置信度降级，后两者计失败）、
  `planning`（wire 规划成功时）。
- 空 task_type 归 `unknown`（`record_task_result` 既有兜底）。
- **judged_complexity 扩展键**：wire 已产出复杂度（`_wire_judged`，仅 `wire_enabled=true`
  时计算）时随 `llm`/`planning` 收尾路径携带，独立计入复杂度维度桶
  （为任务7 复杂度维度 KPI 预留；默认配置 wire_enabled=false → 无复杂度数据，零影响）。
- **feedback 路径**（`agent/feedback.py`）：`submit_feedback` 成功 → `record_task_result("feedback", True)`；
  异常路径 → `record_task_result("feedback", False)`（埋点零影响，异常照常抛出）。

**KPI#1 语义查询通道**（`agent/knowledge/search.py`，观察模式）：

- `KnowledgeSearch.search()` 命中处接入 `record_semantic_query(hit=bool(hits), saved_tokens=0)`。
- **默认关闭**（`LEARNING_METRICS_OBSERVE_KNOWLEDGE_SEARCH=false`，配置见
  `config.yaml learning.metrics.observe_knowledge_search`）：知识卡片检索与 orchestrator
  语义层技能匹配是不同通道，默认不污染 KPI#2 Skill 命中率分母。
- `saved_tokens=0`：知识检索不跳过 LLM 调用，不计入 token 复用率（口径不变）。

**KPI#1 token 计量旁路**：`record_llm_tokens` 补齐标准 `_call_llm` 路径（与 `_call_llm_v2`
同款，`LLMMonitor.estimate_tokens(response)` 估算，不改变 LLMMonitor 本身）——双路径覆盖，
默认路径零新增 LLM 调用。

### 2. KPI#7 口径定义与实现（Step 3）

- **口径**：采纳率 = 周期内被采纳候选数 / 周期内进化候选数（`record_evolution_candidate`）。
- **最小候选基数**：周候选数 < N（`learning.metrics.min_candidates`，默认 5）时，该周采纳率
  标记 `insufficient_data`，**不参与"连续 4 周"统计**（绝不判命中）。
- 实现：`get_weekly_kpis()` 的 `evolution` 桶含 `insufficient_data` 字段；
  `get_snapshot()` 的 `evolution_adoption_rate` 增加 `insufficient_data` / `min_candidates`
  扩展字段（向后兼容，既有 candidates/adopted/rate 不变）。
- 与任务3 联动：任务3 放行后才产生真实候选（evolver 非 dry-run 写 `record_evolution_candidate`）；
  放行前候选数为 0 → 周级 `insufficient_data`，符合"无真实候选不统计"语义。

### 3. 持久化正式启用（Step 4，灰度）

- `config.yaml` 新增 `learning.metrics.persistence`（默认 `enabled: false`）：
  `path` / `flush_batch_size` / `retention_days`，环境变量 `LEARNING_METRICS_PERSIST_*` 覆盖。
- 默认关闭行为与纯内存完全一致（TASK-03 不变式）；`enabled: true` 时走既有 SQLite 落库
  （I/O 不进锁、DB 失败自动降级为内存聚合 + warning，沿用 `3f37612d` 设计）。
- 新增**日粒度事件镜像 `_daily_events`**：与 SQLite `lm_daily_agg` 同形同源，持久化开关无关
  恒在内存维护——周级统计/触发判定不依赖持久化；持久化开启时二者同步写、重启后回填。
- 灰度验证记录：见《任务2_触发条件计算说明.md》§5（重启恢复单测证明跨重启累积）。

### 4. 触发监控（Step 5）

- **查询层**（`agent/learning_metrics.py`）：
  - `get_weekly_kpis(weeks=8)`：以 ISO 周为桶输出 7 项 KPI 周序列（token/skill/workflow/
    task/complexity/feedback/artifact/evolution），口径与 `get_snapshot()` 一致。
  - `evaluate_trigger_conditions(weeks=4, min_candidates, replay_coverage, audit_ok, ...)`：
    报告 §3.3/§5.2 **五条触发条件逐条计算**，逐周标注命中状态；窗口内任一周不可度量 →
    `insufficient_data`（绝不判命中）；外部输入缺失（回放统计/审计/G1-G5/批准）→ `unknown`。
- **API**（`agent/learning_metrics_api.py`）：新增只读端点
  `GET /api/learning/metrics/weekly`、`GET /api/learning/metrics/trigger`；
  既有 `/api/learning/metrics` 结构不变。
- **Prometheus**（`agent/monitoring/learning_trigger_metrics.py`，新增）：
  `yunshu_learning_trigger_condition{condition, status}` gauge，查询层每次计算后同步刷新，
  告警引用其结果（"连续 4 周"判定完全由查询层计算，守"无主观门槛"过程验收项）。
- **告警规则**（`monitoring/prometheus/rules/learning_trigger_alerts.yml`，新增）：
  TC-1~TC-5 五条 + 数据可用性看护（`insufficient_data` 计数告警）。
- **Grafana 面板**（`monitoring/grafana/dashboards/yunshu_learning_triggers.json`，新增）：
  "学习触发条件"面板（5 条件 stat + 状态矩阵 table + 数据可用性 + 口径说明）。

### 5. 测试与文档（Step 6）

- 新增 `tests/unit/test_learning_metrics_triggers.py`（19 例）：KPI#4 task_type 计数与
  judged_complexity 扩展键、生产调用方源码审计、KPI#7 基数门槛与"连续 4 周"阻断、
  五条触发条件示例数据逐条可计算、持久化重启后周窗口累计、观察埋点默认关、
  API 扩展端点。
- 文档：本变更说明 + 《任务2_触发条件计算说明.md》。

## 三、口径披露（守"不易"）

1. **触发条件窗口**：判定窗口 = 最近 N 个**有数据** ISO 周（含当前进行周；N 默认 4）。
   系统运行不足一个窗口 → `insufficient_data`。
2. **不可度量语义**：窗口内任一周无交互（KPI#3 无法度量）、无任务数据（KPI#4 无法度量）、
   候选基数不足（KPI#7）或环比前周缺失（KPI#1/KPI#5）→ 该条件 `insufficient_data`，
   **绝不判命中**（保守，宁缺毋滥）。
3. **环比口径**：KPI#1 环比无提升 = 最新周复用率 ≤ 前一周复用率（双方均需有 token 数据）；
   KPI#5 均分下降 = 最新周均分 < 前一周均分（双方均需有反馈数据）。
4. **KPI#4 触发阈值**：任一分类型失败率 >30%（原始 failed/total 比较，非四舍五入展示值）。
5. **KPI#6 停滞**：周沉淀产物数 == 0（连续 4 周）。
6. **复杂度维度**：judged_complexity 为任务7 预留扩展键；默认配置 wire_enabled=false 时
   无复杂度数据（零影响），任务7 统一复杂度判定源后启用。
7. **修复的既有缺陷**：`_prune_daily_stats` 原实现把 `defaultdict` 降级为普通 dict，
   导致新日期首次访问 KeyError 被静默吞掉（record_interaction/record_feedback 日粒度数据
   静默丢失，KPI#5 逐日趋势失真）。本任务修复为重建 defaultdict（同工厂自动补键），
   **不改任何 KPI 聚合公式**，仅使既有口径按设计正确工作（快照数值只增不减）。

## 四、验收对照

| 验收项 | 结果 |
|---|---|
| 每项 KPI 至少一个生产调用方 | git grep 审计 + 单测 `test_production_wiring_audit` |
| KPI#4 在 orchestrator 任务收尾路径被写入 | 10 处收尾路径 + feedback 双路径，单测证明分类型失败率变化正确 |
| KPI#7 基数不足标记 insufficient_data、不进"连续 4 周" | `test_kpi7_insufficient_data_blocks_4week_streak` |
| 持久化启用后模拟重启数据不丢、"连续 4 周"可累计 | `test_persistence_restart_weekly_window_accumulates` |
| §5.2 五条触发条件逐条可计算 | `test_tc1~tc5` 示例数据演示，无主观门槛 |
| 埋点零业务行为变化、默认路径零新增 LLM 调用 | 观察埋点默认关（`test_knowledge_search_observe_gating`）；token 计量为响应后估算，不新增调用 |
| 持久化 I/O 不进锁、DB 失败自动降级 | 沿用既有实现（`test_degradation_*` 回归通过） |
| 全量回归 | `python -m pytest tests/unit -q`：**11964 passed / 293 skipped**，本任务相关模块测试全绿（learning_metrics 18+5+19、orchestrator、feedback、knowledge_search 共 244 例）；80 failed + 14 errors 全部为沙箱环境性失败（子进程派生 `[WinError 5]`、git 操作、向量库/嵌入模型/sqlite-vec 缺失），与本任务改动零交集 |

## 五、变更文件清单

| 文件 | 变更 |
|---|---|
| `agent/learning_metrics.py` | record_task_result 扩展键、日粒度事件镜像、周级统计、触发判定、配置解析、`_prune_daily_stats` 修复、record_token_reuse/llm_tokens/semantic_query/artifact/evolution 支持 ts |
| `agent/orchestrator/orchestrator.py` | 10 处 record_task_result 埋点 + `_call_llm` record_llm_tokens |
| `agent/feedback.py` | record_task_result("feedback") 成功/失败路径 |
| `agent/knowledge/search.py` | record_semantic_query 观察埋点（默认关） |
| `agent/learning_metrics_api.py` | weekly/trigger 端点 |
| `agent/monitoring/learning_trigger_metrics.py` | 新增：触发条件 gauge |
| `config.yaml` | `learning.metrics`（min_candidates / observe_knowledge_search / persistence / trigger_monitoring） |
| `monitoring/prometheus/rules/learning_trigger_alerts.yml` | 新增：五条触发条件 + 数据可用性告警 |
| `monitoring/grafana/dashboards/yunshu_learning_triggers.json` | 新增：学习触发条件面板 |
| `tests/unit/test_learning_metrics_triggers.py` | 新增：19 例单测 |
| `docs/zh/进化机制重构计划/自进化机制重构计划/任务2_触发条件计算说明.md` | 新增：口径/边界/示例 |
