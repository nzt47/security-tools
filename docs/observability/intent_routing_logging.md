# 意图识别三层漏斗 — 日志埋点规范

> 适用于 orchestrator.py 主业务链路的意图识别日志体系。支持性能分析、问题定位、业务监控。
>
> **任务6（横切）**：在主链路系统性添加日志埋点——各层耗时（`perf_counter` 配对计时）、
> 流量分布（每 N 次请求 INFO 汇总占比）、路由决策（决策依据 + 中间结果 + 最终选择）。
> 统一入口见 `agent/orchestrator/routing_observability.py`。

## 一、日志格式标准

所有日志统一使用 `log_dict()` 规范化（定义于 `agent/logging_utils.py:130`）。
主链路层日志一律经 `log_layer_result()` 输出（`routing_observability.py`），避免各层字段漂移。

### 必需字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `module_name` | str | 模块名（固定 `orchestrator`） |
| `action` | str | 操作标识（点分层级，如 `orchestrator.process.workflow`） |
| `message` | str | 人可读摘要（含关键数值） |
| `trace_id_ctx` | str | 链路追踪 ID（贯穿一次请求） |
| `duration_ms` | float | 耗时（毫秒），各层必填 |
| `layer` | str | 层名（`input_guard/workflow/template/semantic/llm/output_guard/reject`） |
| `decision` | str | 决策值（`hit/miss/block/pass/modified/success/fallback/error/reject`） |

> **【不易】四字段契约**：`trace_id_ctx + layer + decision + duration_ms` 为层日志必含字段，
> 由 `log_layer_result()` 强制组装，调用方只需传 `layer/outcome/duration_ms`。

### 统一层日志入口 `log_layer_result(layer, outcome, trace_id, *, level, message, duration_ms, score, **fields)`

一次调用同时完成三件事（任一失败静默降级为 DEBUG，不阻断主链路）：

1. **结构化日志**：按上表字段组装并输出
2. **流量计数**：`RouteTraffic.record()` — 层尝试/命中 +1
3. **路由上下文累积**：`RouteContext.add_layer()` — 记录该层中间结果供最终决策日志引用

### 日志级别约定

| 级别 | 场景 |
|---|---|
| `INFO` | 层命中/拦截/终态（决策摘要）、流量汇总占比 |
| `DEBUG` | 层未命中（中间结果，继续下沉漏斗）、调用明细 |
| `WARNING` | 拦截（InputGuard BLOCK）、拒识、低置信度降级、语义层异常 |
| `ERROR` | 主链路异常（LLM 调用失败等） |

> **验收对齐**：DEBUG 级可见完整中间结果（每层 hit/miss + 分数），INFO 级可见决策摘要
> （层命中 + 最终路由决策）。

### 最终路由决策日志 `emit_route_decision(final_layer, decision, trace_id, *, message, basis_extra)`

每次请求**恰好一条**（`action=orchestrator.process.route_decision`，INFO），可还原完整路由链路：

| 字段 | 含义 |
|---|---|
| `final_layer` | 最终处理层（workflow/template/semantic/llm/reject） |
| `layer_results` | 各层中间结果（`{layer: {outcome, duration_ms, score}}`，来自 RouteContext） |
| `decision_basis` | 决策依据（规则命中名/语义 top1 score/LLM 置信度等） |
| `duration_ms` | 请求总耗时（自 `RouteContext.init()` 起） |

## 二、各层埋点清单

> 所有层日志经 `log_layer_result()` 输出（含 `trace_id_ctx + layer + decision + duration_ms`）。
> 耗时均以 `time.perf_counter()` 配对计时（【变易】）；TraceSpan 时间戳单独用墙上时钟 `time.time()`，
> 两者不混用。

### 第零步：InputGuard 输入安全检查
- **拦截**：`log_layer_result(input_guard, block)`（WARNING，`action=orchestrator.process.guard`）
  - 字段：`reason`、`matched_pattern`，`duration_ms` 为 check() 耗时
  - 随后 `emit_route_decision(input_guard, block)` 终止链路
- **放行**：`log_layer_result(input_guard, pass)`（DEBUG）

### 第一步：WorkflowEngine 规则层
- **命中**：`log_layer_result(workflow, hit)`（INFO，`action=orchestrator.process.workflow`）
  - 字段：`score`（置信度）、`intent`、`confidence`、`execution_time_ms`
  - 随后 `emit_route_decision(workflow, hit)` 短路返回
- **未命中**：`log_layer_result(workflow, miss)`（DEBUG）
- **指标**：`record_intent_layer("rule")`

### 第二步半：DST 指代消解
- **补全成功**：`orchestrator.process.dst`（INFO）
  - message：`[DST] 省略句补全: "原始" → "补全后"`
- **补全失败**：`orchestrator.process.dst.error`（DEBUG）

### 第三步：IntentRouter 模板层
- **意图分类**：`orchestrator.process.log`（INFO）
  - message：`[路由] 意图=X, 置信度=Y (routing_input="...")`
- **模板命中**：`log_layer_result(template, hit)`（INFO，`action=orchestrator.process.llm`）
  - 字段：`score`（置信度 value）、`intent`、`confidence`（name）
  - 随后 `emit_route_decision(template, hit)` 短路返回
- **指标**：`record_intent_layer("template")`
- **ImportError**：`orchestrator.process.route.import_error`（WARNING）

### 第三步半：语义层（SkillLoader RRF）
- **命中**：`log_layer_result(semantic, hit)`（INFO，`action=orchestrator.semantic.hit`）
  - 字段：`score`（top1 score）、`skill_id`、`retrieval_method`、`reranked`、`fallback_used`
  - 埋点位置：instruction 加载成功且非空之后（守 INV-2，避免双重计数）
  - 随后 `emit_route_decision(semantic, hit)` 短路返回（含 `decision_basis`）
- **未命中**：`log_layer_result(semantic, miss)`（DEBUG，`action=orchestrator.semantic.miss`）
  - 三种降级形态均计入：`matches 为空` / `top1 score < 阈值` / `instruction 为空或加载失败`
- **异常降级**：`log_layer_result(semantic, error)`（WARNING，`action=orchestrator.semantic.error`）
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
- **拒识**：`log_layer_result(reject, reject)`（WARNING，`action=orchestrator.process.reject`）
  - 字段：`reject_type`（input_too_short/semantic_miss）、`intent`、`confidence`、`semantic_result`、`reject_threshold`
  - 随后 `emit_route_decision(reject, reject)` 终止链路（含 `reject_type/reason` 决策依据）
- **指标**：`record_intent_layer("reject")`

### 第四步：LLM 大模型层
- **调用前**：`record_intent_layer("llm")`（INV-4：调用前埋点，计"尝试"）
- **耗时统计**：`_ts_llm_pf = time.perf_counter()` 配对，成功/失败路径均计算 `llm_duration_ms`
- **成功**：`log_layer_result(llm, success)`（INFO，`action=orchestrator.process.llm`）
  - 字段：`duration_ms`、`llm_confidence`、`low_reason`、`response_length`
- **置信度校验**：`orchestrator.process.llm.confidence`（INFO）
  - 字段：`置信度=high/low`、`耗时`、`响应长度`
- **低置信度降级**：`log_layer_result(llm, fallback)` + `emit_route_decision(llm, fallback)`（WARNING）
  - 决策依据：`low_reason`（如空响应/过短/错误标记）
- **调用失败（TD-1，2026-08-02 实施）**：`record_intent_layer("llm_error")`
  - 位于 except 分支，为 llm 的失败子指标（与 fallback 同模式）
  - 面板 10 用 `llm_error / llm` 计算 LLM 错误率（llm 计全部尝试，分母不为 0）
- **失败日志**：`log_layer_result(llm, error)`（ERROR）+ `emit_route_decision(llm, error)` + `orchestrator.process.fail`（ERROR）

### 第五步：OutputGuard 输出安全检查
- **修改（PII 遮盖）**：`log_layer_result(output_guard, modified)`（INFO）
  - 字段：`redacted_fields`，`duration_ms` 为 check() 耗时
- **放行**：`log_layer_result(output_guard, pass)`（DEBUG）

### 正常完成路径（LLM 成功）
- **最终路由决策**：`emit_route_decision(llm, success)`（INFO）
  - 决策依据：`llm_duration_ms`、`output_guard_modified`、`redacted_fields`
  - `layer_results` 汇总全链路各层（input_guard → workflow → template → semantic → llm → output_guard）的 outcome/duration_ms/score

## 三、流量分布（每 N 次请求 INFO 汇总）

`RouteTraffic`（`routing_observability.py`）维护模块级计数，线程安全（持锁仅保护内存状态）：

| action | 级别 | 频率 | 说明 |
|---|---|---|---|
| `orchestrator.traffic.record` | DEBUG | 每次 | 层命中/未命中明细 |
| `orchestrator.traffic.summary` | INFO | 每 N 次请求（默认 50） | 汇总占比 |

汇总字段：
- `requests_total` — 请求总数（最终路由决策计数）
- `layer_hit_ratio` — 各层命中率（`hits / attempts`，hit/miss 均计尝试）
- `final_decision_ratio` — 最终路由分布（各层决策数 / 请求总数，总和 = 1.0）

> **命中判定**：`hit/block/modified/success` 计入层命中数（`_HIT_OUTCOMES`）；
> `miss/pass/error/reject/fallback` 仅计尝试。语义层 disabled/skip（未尝试）不计入。

## 四、Prometheus 指标

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

## 五、链路追踪（TraceSpan）

各层通过 `trace_store.add_span()` 记录 span：
- `{trace_id}_workflow` — 规则层
- `{trace_id}_template` — 模板层
- `{trace_id}_semantic` — 语义层（含 top1_skill/score metadata）
- `{trace_id}_llm` — LLM 调用（含 redacted_fields metadata）

> 注意：span 的 `start_time/end_time` 用墙上时钟 `time.time()`；层耗时（`duration_ms`）用
> `time.perf_counter()` 独立配对计时，二者不混用。

## 六、环境变量配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ORCHESTRATOR_SEMANTIC_LAYER_ENABLED` | `true` | 语义层总开关 |
| `ORCHESTRATOR_SEMANTIC_MIN_SCORE` | `0.3` | 语义层命中阈值 |
| `ORCHESTRATOR_REJECT_MIN_LENGTH` | `3` | 拒识最小输入长度 |
| `ORCHESTRATOR_TRAFFIC_REPORT_INTERVAL` | `50` | 流量占比汇总间隔（每 N 次请求输出一次 INFO 汇总） |

## 八、实战案例：复杂路由决策漏斗（mock 请求）

> 目标：用一条 mock 请求演示「日志可还原完整路由链路」，并作为新埋点的验收样例。
> 详细链路追踪报告见 [trace_tracking_report.md](./trace_tracking_report.md)；
> 可直接导入日志分析系统的实测日志样本见 [trace_tracking_sample_logs.json](./trace_tracking_sample_logs.json)。

### 8.1 场景

样本共 3 个场景、50 条实测日志（`trace_tracking_sample_logs.json`），覆盖「单轮全 miss 落 LLM」与
「多意图混合（多轮）短路/跳过」两类路由形态：

| trace_id | 场景 | 日志数 | final_layer |
|---|---|---|---|
| `trace_complex_001` | 单轮：完整漏斗全 miss，落点 LLM | 20 | `llm` |
| `trace_multi_001` | 多轮 T1：会议调度 → 模板命中短路返回 | 9 | `template` |
| `trace_multi_002` | 多轮 T2：PPT 转 PDF 追问 → 模板跳过，落点 LLM | 21 | `llm` |

场景 1「帮我写一首关于春天的诗」贯穿完整漏斗，最终落点 LLM（0 Token 短路全部未触发）：

```
input_guard pass → workflow miss → template miss → workflow_learning miss
→ semantic miss → reject 判定放行 → llm success → output_guard pass
```

场景 2/3 为同一实例连续两轮（`_last_was_template` 状态传递）：
- T1 会议调度：模板层命中即短路（`template.decision` hit），恰好 1 条 `route_decision`，不触发语义层/LLM；
- T2 追问：`is_follow_up=True` 跳过模板查表（`template.decision` 记录 follow_up 判定），继续下沉语义层 miss 后落 LLM。

### 8.2 复现

```bash
# 场景4（真实 process() + mock 重型依赖，固定 trace_id=trace_complex_001）
python scripts/verify_routing_logging.py
```

**统计验证实证**（把样本 JSON 中 template 相关记录喂给 scan 脚本，结果与手工核算一致）：

```bash
# 抽取 template.decision / template.miss 记录 → 管道喂 stdin 模式
python -c "import json,sys; data=json.load(open('docs/observability/trace_tracking_sample_logs.json', encoding='utf-8')); [print(json.dumps(r, ensure_ascii=False)) for r in data['logs'] if r.get('action') in ('orchestrator.process.template.decision','orchestrator.process.template.miss')]" \
  | python scripts/scan_template_miss_stats.py --log-dir -
```

| 指标 | 预期 | 实测 |
|---|---|---|
| 判定依据记录（template.decision） | 3 | 3 |
| 其中模板层被跳过（追问/不满，未查表） | 1 | 1 |
| 查表未命中（template.miss） | 1 | 1 |
| 原因分布 | follow_up=1, unknown_intent=1 | 一致 |
| 解析失败 | 0 | 0 |

### 8.3 决策依据链（来自实测日志）

| 层 | 决策依据 | 决策 |
|---|---|---|
| `input_guard` | 无匹配 pattern | `pass` |
| `workflow` | try_match 未命中 | `miss` |
| `template` | intent=unknown, confidence=LOW, follow_up=False（`template.decision`/`template.miss` DEBUG） | `miss` |
| `workflow_learning` | enabled=True, min_score=0.40, try_execute 未命中 | `miss` |
| `semantic` | min_score=0.30, fusion=rrf, 召回为空 | `miss` |
| reject 判定 | len_reject=False, semantic_reject=False（`reject.decision` DEBUG） | `pass_to_llm` |
| `llm` | 置信度 high (reason=normal) | `success`（final_layer） |

**场景 2 — `trace_multi_001`：模板命中短路（final_layer=template，9 条）**
`route_decision` 的 `decision_basis` 直接给出命中依据 `intent=schedule, confidence=HIGH`，
模板层 `score=1.0` 即短路返回，后续 workflow_learning / semantic / LLM 全程未触发（0 Token）：

| 层 | 决策依据 | 决策 |
|---|---|---|
| `input_guard` | 无匹配 pattern | `pass` |
| `workflow` | try_match 未命中 | `miss` |
| `template` | intent=schedule, confidence=HIGH, follow_up=False（`template.decision`） | `hit`（短路） |
| `route_decision` | final_layer=template, duration=2.06ms | `hit` |

**场景 3 — `trace_multi_002`：追问跳过模板 → 落 LLM（final_layer=llm，21 条）**
多轮状态 `_last_was_template=True` + `is_follow_up=True` 使模板层跳过查表（不计入
`template.miss`，计入 `skipped_lookup_records`），继续下沉语义层后落 LLM：

| 层 | 决策依据 | 决策 |
|---|---|---|
| `input_guard` | 无匹配 pattern | `pass` |
| `workflow` | try_match 未命中 | `miss` |
| `template` | is_follow_up=True, last_was_template=True（追问，跳过查表） | `skip` |
| `workflow_learning` | enabled=True, min_score=0.40, try_execute 未命中 | `miss` |
| `semantic` | min_score=0.30, fusion=rrf, 召回为空 | `miss` |
| reject 判定 | len_reject=False, semantic_reject=False（`reject.decision` DEBUG） | `pass_to_llm` |
| `llm` | 置信度 high | `success`（final_layer, duration=51.93ms） |

### 8.4 单条日志还原整链

`orchestrator.process.route_decision`（INFO，一次请求恰好一条）的 `layer_results` 汇总全部 6 层
中间结果（outcome/duration_ms/score），`decision_basis` 给出 LLM 耗时等依据：

```json
{"action": "orchestrator.process.route_decision", "trace_id_ctx": "trace_complex_001",
 "final_layer": "llm", "duration_ms": 51.86,
 "layer_results": {
   "input_guard":       {"outcome": "pass",    "duration_ms": 0.02},
   "workflow":          {"outcome": "miss",    "duration_ms": 0.02},
   "workflow_learning": {"outcome": "miss",    "duration_ms": 0.04},
   "semantic":          {"outcome": "miss",    "duration_ms": 0.09},
   "llm":               {"outcome": "success", "duration_ms": 0.02},
   "output_guard":      {"outcome": "pass",    "duration_ms": 0.02}}}
```

### 8.5 衍生工具

| 工具 | 用途 |
|---|---|
| `scripts/verify_routing_logging.py` | 场景4 采样验证（含复杂漏斗） |
| `scripts/scan_template_miss_stats.py` | 定期扫描日志，统计模板未命中原因分布（不满/追问/未知意图/已知意图无模板），支持定时增量模式 |
| `docs/observability/trace_tracking_report.md` | 完整链路追踪报告（耗时分布 + 决策依据） |
| `docs/observability/trace_tracking_sample_logs.json` | 实测日志样本（JSON，可导入 Loki/ELK） |
| `docs/observability/trace_tracking_sample_logs.csv` | 实测日志样本（CSV，utf-8-sig，时间线与 ELK 导入一致，便于导入本地数据库分析） |
| `scripts/elk_import_logs.py` | 样本 JSON → Elasticsearch Bulk API（dry-run 生成请求体 / `--url` 直连导入，`_id` 幂等） |
| `scripts/sample_logs_to_csv.py` | 样本 JSON → CSV（复用 ELK 导入的时间戳分配，嵌套对象存 JSON 字符串） |
| `scripts/cleanup_es_test.py` | 清理 ELK 验证容器（删除 tlm-es-dev 释放内存，可选 `--remove-image` / `--dry-run`） |
| `docs/observability/elk_kibana_snippets.md` | 已实测的 KQL / Dev Tools DSL 查询片段（Discover + 聚合 + 链路还原） |
| `docs/observability/perf_analysis_summary.md` | 路由性能分析摘要（实测 trace 分布 + 各层耗时占比 + 优化建议） |
| `scripts/configure_semantic_circuit.py` | semantic 层超时熔断配置校验/生成（rerank 超时、min_score、语义层阈值，写入 .env） |
| `scripts/gen_kibana_dashboard.py` | 从 snippets 生成 Kibana Saved Objects（NDJSON：index-pattern + 8 search + dashboard） |
| `docs/observability/kibana_orchestrator_dashboard.ndjson` | Kibana 仪表盘导入文件（Stack Management → Saved Objects → Import） |
| `scripts/cleanup_docker_es.py` | 一键清理 dev/test 环境多余 ES 容器和镜像（dry-run 默认，`--yes --remove-image` 执行） |

## 九、验收与验证

- **一次请求可还原完整路由链路**：查 `orchestrator.process.route_decision`（最终决策 + `layer_results` 决策依据）
- **格式统一、字段完整**：所有层日志含 `trace_id_ctx/layer/decision/duration_ms` 四字段
- **DEBUG 中间结果 / INFO 决策摘要**：层未命中为 DEBUG，层命中/终态/汇总为 INFO
- 验证脚本：`python scripts/verify_routing_logging.py`（任务6 采样验证）
