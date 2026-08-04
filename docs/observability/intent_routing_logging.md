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
- **指标**：`record_intent_layer("semantic")`（位于 `orchestrator.py:1086`，仅在 instruction 加载成功且非空后触发，守 INV-2）
- **埋点诊断日志**：`orchestrator.semantic.metric_total`（INFO，`orchestrator.py:1086-1108`）
  - **触发时机**：每次 semantic 埋点成功后立即输出（try/except 包裹，异常静默降级，不传播）
  - **结构化字段**（用于 Loki/Promtail 日志采集与 Grafana 诊断面板）：

    | 字段 | 类型 | 含义 | 用途 |
    |---|---|---|---|
    | `metric_total` | int | 当前所有 layer 计数总和（分母） | 验证分母同步：每次埋点后 total 应单调递增 |
    | `layer_counts` | dict | 各 layer 当前计数（如 `{"llm":1,"semantic":1}`） | 验证分母构成：layer_counts 总和应 = metric_total |
    | `skill_id` | str | 当前命中的 skill ID | 关联 skill 配置异常排查 |
    | `top1_score` | float | 语义检索 top1 分数 | 监控语义召回质量（< 0.3 触发 RRF 误召回熔断） |
    | `instruction_len` | int | instruction 文本长度 | 验证 INV-2：instruction 加载成功（>0）才埋点 |
    | `instruction_loaded` | bool | 是否成功加载 instruction（恒 True） | dashboard 过滤"埋点时机正确性"的显式标记 |

  - **采集策略**（Promtail pipeline_stages 示例）：
    ```yaml
    # 提取 structured log 字段为 Loki labels（仅高基数字段做 label）
    - json:
        expressions:
          action: action
          metric_total: metric_total
          skill_id: skill_id
          top1_score: top1_score
          instruction_loaded: instruction_loaded
    - labels:
        action:
        instruction_loaded:
    ```
  - **关键不变量**：`metric_total == sum(layer_counts.values())`，且 ratio 总和 = 1.0（分母同步）

### 第三步三：拒识检查
- **拒识**：`orchestrator.process.reject`（WARNING）
  - 字段：输入长度、阈值
- **指标**：`record_intent_layer("reject")`

### 第四步：LLM 大模型层
- **调用前**：`record_intent_layer("llm")`（INV-4：调用前埋点，计"尝试"）
- **置信度校验**：`orchestrator.process.llm.confidence`（INFO）
  - 字段：`置信度=high/low`、`耗时`、`响应长度`
- **低置信度告警**：`orchestrator.process.llm.low_confidence`（WARNING）
- **调用失败（TD-1，2026-08-02 实施）**：`record_intent_layer("llm_error")`
  - 位于 except 分支，为 llm 的失败子指标（与 fallback 同模式）
  - 面板 10 用 `llm_error / llm` 计算 LLM 错误率（llm 计全部尝试，分母不为 0）
- **失败日志**：`orchestrator.process.fail`（ERROR）

## 三、Prometheus 指标

| 指标名 | 类型 | Labels | 用途 |
|---|---|---|---|
| `yunshu_intent_layer_total` | Counter | `layer` | 各层命中次数（rule/template/semantic/llm/llm_low_confidence_fallback/llm_error/reject） |
| `yunshu_intent_layer_ratio` | Gauge | `layer` | 各层实时占比（基于模块级 `_intent_layer_counts` 分母同步，总和恒 = 1.0） |

### Grafana 占比计算 PromQL

#### 方案 A（推荐）：基于 Counter `rate()` 的实时流量分布

```promql
# ── 1. 意图层分布饼图（5 主层，排除 fallback 子指标，归一化到 1.0）──
# 排除 llm_low_confidence_fallback 防止 llm 占比被稀释（fallback 是 llm 的设计性子指标）
sum by (layer) (rate(yunshu_intent_layer_total{layer=~"rule|template|semantic|llm|reject"}[5m]))
  / on() group_left
sum(rate(yunshu_intent_layer_total{layer=~"rule|template|semantic|llm|reject"}[5m]))

# ── 2. LLM 兜底率（独立面板，fallback 占 llm 的比例）──
# 验证 dashboard 是否正常：此值应 ∈ [0, 1]，低置信度流量越高此值越大
sum(rate(yunshu_intent_layer_total{layer="llm_low_confidence_fallback"}[5m]))
  / on() group_left
sum(rate(yunshu_intent_layer_total{layer="llm"}[5m]))
```

#### 方案 B：基于 Gauge 的累计占比（无需 `rate()`，适合低流量场景）

```promql
# 直接读取 yunshu_intent_layer_ratio Gauge（分母已同步）
yunshu_intent_layer_ratio{layer=~"rule|template|semantic|llm|reject"}
```

#### `rate()` vs Gauge 选型说明

| 场景 | 推荐 | 原因 |
|---|---|---|
| 高流量（>10 QPS） | `rate()` | 平滑短期波动，反映实时流量分布 |
| 低流量（<1 QPS） | Gauge | `rate()` 在低流量下抖动大，Gauge 直接反映累计占比 |
| 历史趋势对比 | `rate()` | Gauge 无单调性，无法 `rate()`，仅适合当前快照 |

> **关键不变量**：无论选哪种方案，所有 layer ratio 总和恒 = 1.0（分母同步机制守护）。
> 验证查询：`sum(yunshu_intent_layer_ratio)` 应 = 1.0（或 6 层并列时包含 fallback 仍 = 1.0）。
> 验证脚本：`python scripts/verify_semantic_metric_log.py`

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
