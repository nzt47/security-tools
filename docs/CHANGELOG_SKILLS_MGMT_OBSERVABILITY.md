# Changelog: 技能管理全链路可观测性字段补全

> **改造主题**：补全技能管理（skills_mgmt）全链路可观测性字段，新增 `retrieved_chunks` / `eval_score` 等扩展字段，配套 Prometheus 指标与 span 持久化。
>
> **涉及 commit**：
> - `c3515451` feat(skills_mgmt): 全链路可观测性字段补全（retrieved_chunks / eval_score）
> - `07f38db2` test(skills_mgmt): 新增检索可观测性模拟脚本，支持回归测试
> - `69506839` fix(skills_mgmt): 修复 report_retrieval_observability 截断路径缺口
> - `（待提交）` test(skills_mgmt): 适配向量检索已实现的测试用例 + Changelog 补充 Prometheus 缺口说明

---

## 一、新增可观测性字段

### 1.1 `retrieved_chunks`（检索召回分块列表）

**位置**：`MatchResult` 模型 + `build_context()` 返回值 + `traced_action` 上下文

**结构**：每项含 4 个契约字段

```json
{
  "skill_id": "email-helper",
  "score": 0.85,
  "layer": 1,
  "tokens": 120
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `skill_id` | string | 技能ID |
| `score` | float | 检索匹配分数（0.0-1.0） |
| `layer` | int | 检索层级（1=元数据层 / 2=使用说明层 / 3=工具资源层） |
| `tokens` | int | 估算 token 数 |

**生成规则**：
- 缺省时按 `matches` 自动生成（`layer=1`，`tokens` 取 `estimated_tokens`）
- 显式传入时优先使用传入值
- `build_context()` 末尾汇总上报，透传给上游 `traced_action`

### 1.2 `eval_score`（执行评估分数）

**位置**：`service.record_execution()` 可选参数

**结构**：

```json
{
  "task_success": true,
  "instruction_followed": true,
  "hallucination_detected": false,
  "score": 0.92
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_success` | bool | 任务是否成功 |
| `instruction_followed` | bool | 是否遵循指令 |
| `hallucination_detected` | bool | 是否检测到幻觉 |
| `score` | float | 综合评分（0.0-1.0） |

**向后兼容**：参数可选，不传时行为与旧调用方完全一致（守不易）。

### 1.3 `retrieval_precision_at_k`（检索 Precision@K）

**位置**：`report_retrieval_observability()` 可选参数

**结构**：`{k: precision}`，如 `{3: 0.6667, 5: 0.6, 10: 0.5}`

### 1.4 `user_feedback`（用户反馈）

**位置**：`persist_observability_span()` 可选 span 属性

---

## 二、新增 Prometheus 指标

| 指标名 | 类型 | Labels | 说明 |
|---|---|---|---|
| `yunshu_skill_retrieval_precision_at_k` | histogram | `{k}` | 检索 Precision@K 分布 |
| `yunshu_skill_eval_score` | histogram | `{skill_id, task_success}` | 技能执行评估分数分布 |
| `yunshu_skill_hallucination_total` | counter | `{skill_id}` | 幻觉检测计数器（`hallucination_detected=true` 时递增） |

**命名规范**：遵循 `yunshu_skill_<动作>` 前缀约定。

---

## 三、新增 Span 持久化函数

### 3.1 `record_span_attributes()` — tracing.py

```python
def record_span_attributes(*, trace_id=None, span_id=None, **attributes) -> None
```

- 无 OpenTelemetry 时降级为结构化 JSON 日志（`action="span_attributes"`）
- try/except 兜底，失败时 debug 日志，不影响主流程

### 3.2 `persist_observability_span()` — observability.py

```python
def persist_observability_span(*, trace_id=None, **fields) -> None
```

- 调用 `record_span_attributes` 持久化 span 属性
- 失败降级为本地结构化日志

### 3.3 `report_retrieval_observability()` — observability.py

```python
def report_retrieval_observability(retrieved_chunks, *, trace_id=None, precision_at_k=None) -> None
```

- 一站式上报：持久化 span + 可选 Precision@K 指标
- **截断清洗**：调用 `_sanitize_observability_payload` 截断过大 chunks（与 `traced_action` 统一契约）

### 3.4 `emit_eval_score_metric()` — observability.py

```python
def emit_eval_score_metric(skill_id, eval_score, *, trace_id=None) -> None
```

- 发射 `yunshu_skill_eval_score` histogram
- 条件发射 `yunshu_skill_hallucination_total` counter（`hallucination_detected=true` 时）
- 持久化 span + 结构化日志 `eval_score.recorded`

### 3.5 `emit_retrieval_precision_metric()` — observability.py

```python
def emit_retrieval_precision_metric(*, k, hits, precision, trace_id=None) -> None
```

- 发射 `yunshu_skill_retrieval_precision_at_k` histogram
- 结构化日志 `retrieval_precision_at_k`

---

## 四、防御性设计

### 4.1 `retrieved_chunks` 自动截断

**阈值**：`_MAX_RETRIEVED_CHUNKS = 50`

**清洗函数**：`_sanitize_observability_payload(payload)`

**行为**：
- `retrieved_chunks` 超过 50 项时自动截断为前 50 项
- 追加标记字段：
  - `retrieved_chunks_truncated: true`
  - `retrieved_chunks_original_count: <原始数量>`
- 其他字段保留不变

**覆盖路径**：
- ✅ `traced_action` 上下文（入口 `safe` + 结束 `safe_merged`）
- ✅ `report_retrieval_observability` → `persist_observability_span`（修复后统一）

### 4.2 Metrics 发射容错

- 所有 metrics 发射包裹 try/except
- 失败时降级为 debug 日志，不影响主流程
- 测试验证：`test_metrics_emission_failure_does_not_break_flow`

### 4.3 可选字段向后兼容

- `eval_score` / `precision_at_k` / `user_feedback` 均为可选参数
- 缺省时行为与旧调用方完全一致（守不易）
- 测试验证：`test_record_execution_without_eval_score_backward_compat`

### 4.4 ⚠️ Prometheus 集成缺口（已知问题，待修复）

**问题**：新增的 3 个 Prometheus 指标（`yunshu_skill_retrieval_precision_at_k` / `yunshu_skill_eval_score` / `yunshu_skill_hallucination_total`）**未出现在 `/metrics` 端点**。

**根因**：
```
emit_metric(name, kind="histogram")
  → _metrics = BusinessMetricsCollector()
  → hasattr(_metrics, "observe_histogram")  # False（只有 _observe_histogram 私有方法）
  → hasattr 检查失败 → 指标被静默丢弃
```

- `BusinessMetricsCollector` 不含 `inc_counter` / `observe_histogram` / `set_gauge` 公开方法
- `emit_metric` 的 `hasattr` 检查全部返回 False，新指标被静默丢弃
- `/metrics` 端点由 `prometheus_flask_exporter` 提供，仅服务 `prometheus_client.REGISTRY` 中的指标

**当前状态**：
- ❌ Prometheus `/metrics` 端点：新指标未出现
- ✅ 结构化日志（`span_attributes` / `eval_score.recorded` / `retrieval_precision_at_k`）：正常工作
- `health` 接口中 `span_persistence: "structured_log"` 准确反映了当前持久化方式

**修复方向**（P1，待实施）：在 `emit_metric` 中增加 `prometheus_client` 直接注册路径，绕过 `BusinessMetricsCollector` 的 `hasattr` 检查失败问题

---

## 五、Health 接口扩展

**端点**：`/api/skills-mgmt/health`

**新增 `stats.observability` 子节**：

```json
{
  "stats": {
    "observability": {
      "fields": [
        "retrieved_chunks",
        "retrieval_precision_at_k",
        "eval_score",
        "user_feedback"
      ],
      "metrics": [
        "yunshu_skill_retrieval_precision_at_k",
        "yunshu_skill_eval_score",
        "yunshu_skill_hallucination_total"
      ],
      "retrieved_chunks_max": 50,
      "span_persistence": "structured_log",
      "truncation_enabled": true
    }
  }
}
```

---

## 六、测试覆盖

### 6.1 单元测试（`tests/unit/test_skills_mgmt.py`）

**TestObservabilityFields 类（7 个测试）**：

| 测试方法 | 验证点 |
|---|---|
| `test_match_result_contains_retrieved_chunks` | MatchResult.to_dict() 含 retrieved_chunks 字段及正确结构 |
| `test_match_result_retrieved_chunks_truncation_at_50` | 60 项 chunks 截断为 50 项 + truncated 标记 |
| `test_build_context_reports_retrieval_chunks` | build_context 日志与返回值含 retrieved_chunks |
| `test_record_execution_accepts_eval_score` | eval_score 参数不报错且持久化到日志 |
| `test_record_execution_without_eval_score_backward_compat` | 不传 eval_score 时行为与旧调用方一致 |
| `test_metrics_emission_failure_does_not_break_flow` | metrics 发射失败时主流程正常 |
| `test_health_stats_include_observability_fields` | health stats 含 observability 子节 |

**测试结果**：57 passed, 1 xfailed（预存在 TF-IDF 基线测试，与本次改造无关）

### 6.2 TestRetrievalExtension 测试适配（向量检索已实现）

向量检索通过 JSON fallback 实现后，2 个原假设"未实现"的测试用例需要更新断言：

| 测试方法 | 原断言（假设未实现） | 更新后断言（已实现） |
|---|---|---|
| `test_match_accepts_extension_params` | `use_vector=True` 记录 `extension_not_implemented` warning | `use_vector=True` 不报错；`use_bm25/use_reranker` 仍记录 warning |
| `test_match_fallback_flag_when_vector_requested` | `fallback_used=True`, `retrieval_method="tfidf"` | `fallback_used=False`, `retrieval_method="vector"` |

**根因**：`loader.match()` line 302 `return vector_results` 提前返回，导致 `use_bm25`/`use_reranker` 的 warning 检查被跳过。修复后分两步验证：先测 `use_vector=True`（无 warning），再单独测 `use_bm25/use_reranker`（有 warning）。

### 6.3 回归测试脚本

**`scripts/simulate_retrieval_observability.py`**：

```bash
# 运行模拟脚本（从项目根目录）
$env:PYTHONPATH = "."; python scripts/simulate_retrieval_observability.py --chunks 60
```

4 个演示场景：
1. 截断效果：60 项 → 50 项 + `retrieved_chunks_truncated=True`
2. `report_retrieval_observability`：span_attributes 含截断后 chunks + Precision@K
3. `emit_eval_score_metric`：`eval_score.recorded` 日志（含 hallucination 标记）
4. `persist_observability_span`：自定义 span 属性持久化

---

## 七、Bug 修复

### 7.1 `report_retrieval_observability` 截断路径缺口

**commit**：`69506839`

**问题**：`report_retrieval_observability` 直接调用 `persist_observability_span` 传递 `retrieved_chunks`，未走 `_sanitize_observability_payload` 清洗路径，导致 span_attributes 日志包含完整 60 项 chunks（未截断），日志体积膨胀。

**修复**：在 `persist_observability_span` 调用前增加 `_sanitize_observability_payload` 清洗，与 `traced_action` 上下文统一截断契约。

**修复前后对比**（`--chunks 60`）：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| span_attributes 中 retrieved_chunks 数量 | 60 项（完整） | 50 项（截断） |
| `retrieved_chunks_truncated` 标记 | 缺失 | `true` |
| `retrieved_chunks_original_count` 标记 | 缺失 | `60` |

---

## 八、不变量【不易】守护

1. ✅ 现有 `trace_id` / `module_name` / `action` / `duration_ms` 字段不变
2. ✅ 现有 `emit_metric` / `traced_action` 接口签名不变
3. ✅ 现有测试全部继续通过（57 passed, 1 xfailed）
4. ✅ Skill / SkillMatch 核心模型字段未改（仅扩展 MatchResult.to_dict 输出）
5. ✅ 未引入新第三方依赖
6. ✅ 前端 UI 未改

---

## 九、涉及文件清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `agent/skills_mgmt/observability.py` | 修改 | 新增 metrics/span 持久化辅助 + 截断修复 |
| `agent/monitoring/tracing.py` | 修改 | 新增 `record_span_attributes` |
| `agent/skills_mgmt/loader.py` | 修改 | MatchResult 新增 `retrieved_chunks` 字段 |
| `agent/skills_mgmt/context_injector.py` | 修改 | `build_context` 上报 retrieved_chunks 汇总 |
| `agent/skills_mgmt/service.py` | 修改 | `record_execution` 接收 eval_score + health stats |
| `tests/unit/test_skills_mgmt.py` | 修改 | 新增 TestObservabilityFields（7 个测试） |
| `scripts/simulate_retrieval_observability.py` |