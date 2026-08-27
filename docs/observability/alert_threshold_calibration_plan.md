# 待决策项 2：告警阈值生产基线校准建议

> **编制时间**: 2026-06-26
> **关联文档**: [entry_assigned_monitoring_plan.md](entry_assigned_monitoring_plan.md) §七-2
> **当前告警规则**: `VoiceEntryUnassignedHigh`（`deploy/monitoring/prometheus/alert_rules.yml` L146-156）
> **状态**: 建议稿，待生产基线数据验证后确认

---

## 一、当前阈值及其问题

### 1.1 现状

```yaml
- alert: VoiceEntryUnassignedHigh
  expr: increase(yunshu_voice_entry_unassigned_total[10m]) > 3
  for: 2m
  severity: warning
```

### 1.2 存在的问题

| 问题 | 说明 |
|------|------|
| 阈值来源未经校准 | `>3 次/10min` 为拍脑袋值，未基于生产基线 |
| 指标语义特殊性 | `entry_assigned=false` 理论上**应恒为 0**（正常请求都能解析 JSON）；任何 >0 即代表客户端协议异常或攻击扫描 |
| `for: 2m` 过短 | 2 分钟持续窗口可能捕获瞬时抖动，造成误报；现有告警 `for` 多为 5m/10m |
| 无总量参照 | 当前只统计异常次数，未对照请求总量，无法评估异常率 |

### 1.3 关键判断

该指标与「内存超阈值」「覆盖率过低」等**连续型指标**不同，属于**应恒为 0 的离散异常计数**。
因此阈值校准思路不适用「分位数法」，而应采用 **「理想值 = 0，允许瞬态噪声」** 策略。

---

## 二、校准方法（三阶段部署）

### 阶段 A：观察期（2 周）— 采集基线

**目标**: 采集生产环境 `yunshu_voice_entry_unassigned_total` 的真实分布，确认噪声水平。

**动作**: 阈值暂放宽至 `>10 次/10min`，`for: 10m`，避免误报干扰判断。

```yaml
- alert: VoiceEntryUnassignedHigh
  expr: increase(yunshu_voice_entry_unassigned_total[10m]) > 10
  for: 10m
```

**基线采集查询**（Grafana Explore / PromQL）：

```promql
# 日异常总量
sum(increase(yunshu_voice_entry_unassigned_total[24h]))

# 异常率（需与请求总量对照，路径标签格式需人工核实）
sum(rate(yunshu_voice_entry_unassigned_total[5m])) 
  / 
sum(rate(yunshu_http_request_total{path=~".*voice/listen.*"}[5m]))
```

> ⚠️ **需人工核实**: `yunshu_http_request_total` 的 `path` 标签是否包含 `/api/voice/listen`；
> 若路径标签缺失，改用 `sum(rate(yunshu_voice_entry_unassigned_total[5m]))` 单独看绝对量。

### 阶段 B：校准期 — 设定正式阈值

基于阶段 A 数据，按以下决策树设定：

| 观察期观测值 | 正式阈值建议 | 理由 |
|-------------|-------------|------|
| 异常总量 ≈ 0（<1 次/周） | `> 0`（任何一次即告警），`for: 15m` | 理想值 0，瞬时噪声极低，可收紧 |
| 异常总量 1~5 次/天 | `> 3 次/10min`，`for: 10m` | 保留 3 次噪声冗余 |
| 异常总量 >5 次/天 | 先排查根因（可能是攻击扫描/客户端 bug），**修完再收紧** | 基线本身偏高，需先治理 |

**推荐默认值**（若基线不可得）：`> 3 次/10min`，`for: 10m`

理由：
- `for` 从 2m 提至 10m，与同组「结构化日志覆盖率过低」「阈值违规项数过多」等规则一致（均 10m）
- 10 分钟窗口内 3 次异常，对真实故障（持续 BadRequest）仍能及时捕捉，同时过滤偶发噪声

### 阶段 C：收敛期 — 持续优化

- 连续 2 周无告警后，将阈值逐步收紧至 `> 0`（`for: 15m`）
- 观察告警误报率（FPR）：目标 <10%

---

## 三、推荐配置（当前即建议采用）

```yaml
# 11. 语音接口参数解析前异常（entry_assigned 监控，阶段三）
- alert: VoiceEntryUnassignedHigh
  expr: increase(yunshu_voice_entry_unassigned_total[10m]) > 3
  for: 10m                       # ← 2m → 10m，防瞬时抖动
  labels:
    severity: warning
    category: observability
    layer: runtime
  annotations:
    summary: "语音接口异常发生在参数解析前"
    description: "10 分钟内 entry_assigned=false 次数超过 3 次，可能为 BadRequest 或客户端协议异常"
    runbook_url: "https://wiki.internal/yunshu/runbooks/voice-entry-unassigned-high"
```

**唯一变更**: `for: 2m → 10m`。`expr` 阈值 `>3 次/10min` 保持默认，待观察期数据后按 §二-B 决策树校准。

---

## 三-A、告警覆盖扩展（2026-06-26 补覆盖）

对 `alert_rules.yml` 做了覆盖缺口分析（原 14 条仅覆盖「四层可见性 + LinkCache」，
缺 Flask 业务运行时异常告警），并补齐 8 条规则，规则总数 14 → 22（含 LinkCache 4 条）。

### P1（高优先级，已落地）

| 规则 | 表达式 | for | severity |
|------|--------|-----|----------|
| `CircuitBreakerOpen` | `yunshu_circuit_breaker_state{state="open"} == 1` | 2m | critical |
| `Http5xxRateHigh` | 5xx 占比 >5%（`clamp_min` 防除零） | 5m | warning |

### P2（中优先级，已落地）

| 规则 | 表达式 | for | severity |
|------|--------|-----|----------|
| `RateLimitTriggerHigh` | `increase(yunshu_rate_limit_trigger_total[10m]) > 50` | 5m | warning |
| `SecurityBlockHigh` | `increase(yunshu_security_blocks_total[10m]) > 10` | 5m | warning |
| `LlmCallFailureHigh` | LLM error 占比 >20% | 5m | warning |
| `TaskCompletionLow` | `yunshu_task_completion_rate < 80` | 10m | warning |
| `MemoryHitRateLow` | `yunshu_memory_search_hit_rate < 50` | 10m | warning |

### 覆盖状态

- 四层可见性指标：10 条
- 业务运行时异常：8 条（语音/熔断/5xx/限流/安全/LLM/任务/记忆）
- LinkCache：4 条
- **合计 22 条，覆盖缺口全部补齐**

### 阈值校准提醒

P1/P2 阈值为合理默认值（未基于生产基线），建议在观察期通过
`promtool check rules` + Grafana Explore 结合实际数据校准；
通用采集思路可复用 `scripts/collect_voice_entry_baseline.py`。

---

## 四、验证方案

| 验证项 | 方法 | 预期 |
|--------|------|------|
| 规则语法 | `promtool check rules deploy/monitoring/prometheus/alert_rules.yml` | 通过 |
| 指标存在性 | `curl localhost:9090/api/v1/query?query=yunshu_voice_entry_unassigned_total` | 返回空系列（未触发过）或正常 |
| 告警触发 | 连发 4 次非法 JSON 请求到 `/api/voice/listen` | 10 分钟窗口内计数 >3，`for` 满后 firing |
| 误报率 | 2 周观察期统计 | FPR <10% |

---

## 五、结论

1. **建议立即执行**：`for: 2m → 10m`（低成本、防抖动）
2. **观察 2 周**：采集异常总量与异常率基线
3. **按决策树校准**：多数情况下应最终收敛到 `>0 / 15m`（因该指标理想值为 0）
4. 若观察期即发现高频异常（>5 次/天），优先排查根因而非调整阈值

---

## 六、变更记录

| 日期 | 变更 | 依据 |
|------|------|------|
| 2026-06-26 | 生成校准建议 | 本报告 |
| 2026-06-26 | `for: 2m → 10m` 落地（alert_rules.yml L149） | 本报告 §三 |
| （待填） | 观察期数据汇总 | 待执行 |
