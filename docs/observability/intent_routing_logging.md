# 意图识别三层漏斗 — 日志埋点规范

> 适用于 orchestrator.py 主业务链路的意图识别日志体系。支持性能分析、问题定位、业务监控。

## 一、日志格式标准

所有日志统一使用 `log_dict()` 规范化（定义于 `agent/logging_utils.py:130`）。

### 必需字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `module_name` | str | 模块名（固定 `orchestrator`） |
| `action` | str | 操作标识（点分层级，如 `orchestrator.process.workflow`） |
| `message` | str | 人可读摘要（含关键数值） |
| `trace_id_ctx` | str | 链路追踪 ID（贯穿一次请求） |
| `duration_ms` | float | 耗时（毫秒），各层必填 |

### 日志级别约定

| 级别 | 场景 |
|---|---|
| `INFO` | 各层命中/未命中、决策摘要、耗时统计 |
| `DEBUG` | 中间结果、补全详情、降级细节 |
| `WARNING` | 降级告警、拒识、低置信度、ImportError |
| `ERROR` | 主链路异常（LLM 调用失败等） |

## 二、各层埋点清单

### 第零步：InputGuard 输入安全检查
- **命中拦截**：`orchestrator.process.guard`（WARNING）

### 第一步：WorkflowEngine 规则层
- **命中**：`orchestrator.process.workflow`（INFO）
  - 字段：`intent`、`confidence`、`execution_time_ms`
- **指标**：`record_intent_layer("rule")`

### 第二步半：DST 指代消解
- **补全成功**：`orchestrator.process.dst`（INFO）
  - message：`[DST] 省略句补全: "原始" → "补全后"`
- **补全失败**：`orchestrator.process.dst.error`（DEBUG）

### 第三步：IntentRouter 模板层
- **意图分类**：`orchestrator.process.log`（INFO）
  - message：`[路由] 意图=X, 置信度=Y (routing_input="...")`
- **模板命中**：`orchestrator.process.llm`（INFO）`使用本地模板，跳过 LLM 调用`
- **指标**：`record_intent_layer("template")`
- **ImportError**：`orchestrator.process.route.import_error`（WARNING）

### 第三步半：语义层（SkillLoader RRF）
- **命中**：`orchestrator.semantic.hit`（INFO）
  - 字段：`top1_skill`、`top1_score`、`match_count`、`retrieval_method`、`reranked`、`fallback_used`
- **未命中**：`orchestrator.semantic.miss`（INFO）
- **异常降级**：`orchestrator.semantic.error`（WARNING）
- **指标**：`record_intent_layer("semantic")`

### 第三步三：拒识检查
- **拒识**：`orchestrator.process.reject`（WARNING）
  - 字段：输入长度、阈值
- **指标**：`record_intent_layer("reject")`

### 第四步：LLM 大模型层
- **调用前**：`record_intent_layer("llm")`
- **置信度校验**：`orchestrator.process.llm.confidence`（INFO）
  - 字段：`置信度=high/low`、`耗时`、`响应长度`
- **低置信度告警**：`orchestrator.process.llm.low_confidence`（WARNING）
- **调用失败**：`orchestrator.process.fail`（ERROR）

## 三、Prometheus 指标

| 指标名 | 类型 | Labels | 用途 |
|---|---|---|---|
| `yunshu_intent_layer_total` | Counter | `layer` | 各层命中次数（rule/template/semantic/llm/reject） |

### Grafana 占比计算 PromQL

```promql
# 各层占比
sum by (layer) (rate(yunshu_intent_layer_total[5m]))
/ on() sum(rate(yunshu_intent_layer_total[5m]))

# 规则层命中率
sum(rate(yunshu_intent_layer_total{layer="rule"}[5m]))
/ sum(rate(yunshu_intent_layer_total[5m]))
```

## 四、链路追踪（TraceSpan）

各层通过 `trace_store.add_span()` 记录 span：
- `{trace_id}_workflow` — 规则层
- `{trace_id}_template` — 模板层
- `{trace_id}_semantic` — 语义层（含 top1_skill/score metadata）

## 五、环境变量配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ORCHESTRATOR_SEMANTIC_LAYER_ENABLED` | `true` | 语义层总开关 |
| `ORCHESTRATOR_SEMANTIC_MIN_SCORE` | `0.3` | 语义层命中阈值 |
| `ORCHESTRATOR_REJECT_MIN_LENGTH` | `3` | 拒识最小输入长度 |
