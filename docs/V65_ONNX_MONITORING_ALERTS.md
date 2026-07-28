# v6.5 ONNX Reranker 监控告警规则

> **文档定位**：基于 `agent/skills_mgmt/reranker.py` 现有结构化 JSON 日志埋点设计的监控告警规则
> **核心场景**：P99 延迟突增 + 模型加载失败 + 降级率异常 + 推理失败率
> **SLO 基线**：P99 ≤ 500ms（实测 P99 258ms，留 1.94x 安全冗余）
> **关联文档**：[V65_ONNX_DEPLOYMENT_PLAYBOOK.md](./V65_ONNX_DEPLOYMENT_PLAYBOOK.md) | [V65_ONNX_QUANTIZATION_PLAN.md](./V65_ONNX_QUANTIZATION_PLAN.md)

---

## 0. 设计原则

| 三义 | 约束 |
|------|------|
| **不易** | 规则字段必须严格对齐 `reranker.py` 日志的 `action` / `module_name` / `duration_ms` 等键名，禁臆造 |
| **变易** | 阈值通过环境变量/Alertmanager 模板参数化，支持灰度调整；多渠道通知（钉钉+Issue+邮件）|
| **简易** | 规则按"症状→阈值→响应"三段式描述，值班人员 30s 内可定位 |

---

## 1. 日志埋点清单（监控数据源）

### 1.1 Reranker 模块所有 `action` 枚举

下表为 `agent/skills_mgmt/reranker.py` 当前所有结构化日志埋点，监控规则**必须**基于此设计：

| `action` | 级别 | 关键字段 | 含义 | 监控用途 |
|----------|------|---------|------|---------|
| `onnx.loaded` | INFO | `model`, `onnx_file`, `inputs`, `load_time_s` | ONNX 加载成功 | 加载耗时监控 |
| `onnx.load_failed` | WARNING | `model`, `error` | ONNX 加载失败 | **P0 告警：加载失败** |
| `onnx.skip` | WARNING | `reason` (`model_path_not_local_dir` / `onnx_file_not_found`), `expected_path` | ONNX 跳过 | **P1 告警：配置错误** |
| `onnx.fallback_to_pytorch` | INFO | `reason` | ONNX 降级到 PyTorch | 降级率监控 |
| `onnx.predict_failed` | WARNING | `trace_id`, `error` | ONNX 推理失败 | **P1 告警：推理失败** |
| `pytorch.loaded` | INFO | `model` | PyTorch 后端加载成功 | 后端分布监控 |
| `pytorch.load_failed` | WARNING | `model`, `error` | PyTorch 加载失败 | **P0 告警：双后端全挂** |
| `rerank.completed` | INFO | `trace_id`, `query`, `candidate_count`, `result_count`, `top_score`, `duration_ms` | rerank 成功 | **P99 延迟监控** |
| `rerank.fallback` | WARNING | `trace_id`, `reason` (`model_unavailable`), `candidate_count`, `duration_ms` | 降级到原始排序 | **P1 告警：降级率** |
| `rerank.predict_failed` | WARNING | `trace_id`, `error`, `duration_ms` | 推理失败 | **P1 告警：推理失败** |
| `rerank.disabled` | INFO | `trace_id`, `reason` | Reranker 被禁用 | 配置监控 |

### 1.2 日志样例

```json
{"trace_id":"a1b2c3d4e5f67890","module_name":"reranker","action":"rerank.completed","query":"帮我识别语音并转成文字","candidate_count":5,"result_count":3,"top_score":0.9521,"duration_ms":247.83}
{"module_name":"reranker","action":"onnx.loaded","model":"C:/Users/.../jina-reranker-v2-base-multilingual","onnx_file":"model_quantized.onnx","inputs":["input_ids","attention_mask"],"load_time_s":1.12}
{"module_name":"reranker","action":"onnx.load_failed","model":"C:/Users/.../jina-reranker-v2-base-multilingual","error":"[ONNXRuntimeError] : 1 : FAIL : Load model from ... failed"}
{"trace_id":"a1b2c3d4e5f67890","module_name":"reranker","action":"onnx.predict_failed","error":"onnxruntime inference failed"}
```

---

## 2. 监控指标定义

### 2.1 PromQL 指标提取规则（log-based metrics）

通过 Loki / Promtail 或 Vector 从 JSON 日志提取为 Prometheus 指标：

```yaml
# promtail/scrape_configs.yml — 从 reranker 日志提取指标
metrics_scrape_configs:
  # ── 指标 1: rerank 延迟直方图 ──
  - name: rerank_duration_ms
    kind: histogram
    buckets: [50, 100, 200, 300, 400, 500, 750, 1000, 2000, 5000]
    match:
      module_name: reranker
      action: rerank.completed
    value_field: duration_ms
    labels:
      - trace_id  # 仅作分组，不聚合

  # ── 指标 2: 加载失败计数器 ──
  - name: reranker_load_failed_total
    kind: counter
    match:
      module_name: reranker
      action:
        - onnx.load_failed
        - onnx.skip
        - pytorch.load_failed
    labels:
      - action
      - reason
      - model

  # ── 指标 3: 降级率计数器 ──
  - name: reranker_fallback_total
    kind: counter
    match:
      module_name: reranker
      action:
        - rerank.fallback
        - rerank.predict_failed
        - onnx.fallback_to_pytorch
    labels:
      - action
      - reason

  # ── 指标 4: 推理成功计数器（用于失败率分母）──
  - name: reranker_completed_total
    kind: counter
    match:
      module_name: reranker
      action: rerank.completed
```

### 2.2 派生指标公式

| 指标名 | PromQL |
|--------|--------|
| P99 延迟 | `histogram_quantile(0.99, sum(rate(rerank_duration_ms_bucket[5m])) by (le))` |
| P95 延迟 | `histogram_quantile(0.95, sum(rate(rerank_duration_ms_bucket[5m])) by (le))` |
| P50 延迟 | `histogram_quantile(0.50, sum(rate(rerank_duration_ms_bucket[5m])) by (le))` |
| SLO 违规率 | `sum(rate(rerank_duration_ms_bucket{le="500"}[5m])) / sum(rate(rerank_duration_ms_count[5m]))` |
| 加载失败率 | `rate(reranker_load_failed_total[5m])` |
| 降级率 | `rate(reranker_fallback_total[5m]) / (rate(reranker_completed_total[5m]) + rate(reranker_fallback_total[5m]))` |

---

## 3. 告警规则（Alertmanager）

### 3.1 P0 — 紧急告警（立即响应，<5min）

#### 3.1.1 ONNX 模型加载失败

```yaml
# ── P0: ONNX 加载失败 ──
- alert: RerankerOnnxLoadFailed
  expr: increase(reranker_load_failed_total{action="onnx.load_failed"}[5m]) > 0
  for: 0s  # 立即触发
  labels:
    severity: critical
    team: skill-mgmt
    category: onnx-load-failure
  annotations:
    summary: "ONNX Reranker 模型加载失败"
    description: |
      模型 {{ $labels.model }} 加载失败，已降级到 PyTorch 后端。
      错误: 请查看 logs 中 action=onnx.load_failed 的 error 字段。
      影响: 推理延迟可能从 258ms 飙升到 7960ms（30x 劣化），可能触发 SLO 违规。
      响应: 参考 V65_ONNX_DEPLOYMENT_PLAYBOOK.md §4.2.1 切换 ONNX 变体
    runbook_url: "https://github.com/nzt47/security-tools/blob/master/docs/V65_ONNX_DEPLOYMENT_PLAYBOOK.md"
```

#### 3.1.2 双后端全挂（ONNX + PyTorch 均失败）

```yaml
# ── P0: 双后端全挂，reranker 完全失效 ──
- alert: RerankerAllBackendsDown
  expr: |
    increase(reranker_load_failed_total{action="onnx.load_failed"}[5m]) > 0
    and
    increase(reranker_load_failed_total{action="pytorch.load_failed"}[5m]) > 0
  for: 0s
  labels:
    severity: critical
    team: skill-mgmt
    category: dual-backend-failure
  annotations:
    summary: "Reranker 双后端全部失败，已降级到 RRF 原始排序"
    description: |
      ONNX 和 PyTorch 后端均加载失败，reranker 完全失效。
      当前所有 rerank 调用降级为 RRF 排序（无 Cross-Encoder 精排）。
      影响: P@3 精度预期下降 18.5%，用户体验劣化。
      紧急操作:
        1. 检查模型文件是否被删除: ls $SKILL_RERANKER_MODEL/onnx/
        2. 临时关闭 reranker: SKILL_RERANKER_ENABLED=false（避免日志噪声）
        3. 排查系统资源: 是否 OOM/磁盘满
      响应: 参考 V65_ONNX_DEPLOYMENT_PLAYBOOK.md §4.2.3
```

### 3.2 P1 — 重要告警（15min 内响应）

#### 3.2.1 P99 延迟突增（重点）

```yaml
# ── P1: P99 延迟超过 SLO（500ms）──
- alert: RerankerP99LatencySLOBreach
  expr: |
    histogram_quantile(0.99, sum(rate(rerank_duration_ms_bucket[5m])) by (le)) > 500
  for: 5m  # 持续 5min 超阈值才告警，避免毛刺
  labels:
    severity: warning
    team: skill-mgmt
    category: latency-slo-breach
  annotations:
    summary: "Reranker P99 延迟 {{ $value }}ms 超过 500ms SLO"
    description: |
      当前 P99 延迟: {{ $value }}ms（SLO: 500ms，基线: 258ms）。
      可能原因:
        1. ONNX 降级到 PyTorch（检查 onnx.fallback_to_pytorch 日志）
        2. CPU 负载过高（检查 system load）
        3. batch_size 异常增大（检查 candidate_count 分布）
        4. 模型文件损坏导致 retry 风暴
      排查命令:
        grep '"action":"onnx.fallback_to_pytorch"' logs/digital_life.log | tail -20
        grep '"action":"rerank.completed"' logs/digital_life.log | jq '.duration_ms' | sort -n | tail -10
```

#### 3.2.2 P99 延迟突增（相对基线 2 倍）

```yaml
# ── P1: P99 延迟突增（相对 1h 前基线 2 倍以上）──
- alert: RerankerP99LatencySpike
  expr: |
    histogram_quantile(0.99, sum(rate(rerank_duration_ms_bucket[5m])) by (le)) >
    2 * histogram_quantile(0.99, sum(rate(rerank_duration_ms_bucket[5m] offset 1h)) by (le))
    and
    histogram_quantile(0.99, sum(rate(rerank_duration_ms_bucket[5m])) by (le)) > 300
  for: 10m
  labels:
    severity: warning
    team: skill-mgmt
    category: latency-spike
  annotations:
    summary: "Reranker P99 延迟突增（相对 1h 基线 2 倍以上）"
    description: |
      当前 P99: {{ $value }}ms，相对 1 小时前基线翻倍且超过 300ms 警戒线。
      此告警专门捕获"未超 SLO 但异常增长"的趋势，提前预警性能劣化。
      常见诱因:
        - CPU 频率降频（笔记本省电模式）
        - 其他进程争抢 CPU
        - ONNX Runtime 内存碎片化（长稳压测显示 1000 次迭代 RSS 增量 -0.01MB，正常应无增长）
```

#### 3.2.3 ONNX 推理失败率过高

```yaml
# ── P1: ONNX 推理失败率 > 5% ──
- alert: RerankerOnnxPredictFailureRateHigh
  expr: |
    sum(rate(reranker_load_failed_total{action="onnx.predict_failed"}[5m]))
    /
    (sum(rate(reranker_completed_total[5m])) + sum(rate(reranker_load_failed_total{action="onnx.predict_failed"}[5m])))
    > 0.05
  for: 5m
  labels:
    severity: warning
    team: skill-mgmt
    category: predict-failure-rate
  annotations:
    summary: "ONNX 推理失败率 {{ $value | humanizePercentage }}（阈值 5%）"
    description: |
      ONNX 推理失败率持续超过 5%，可能原因:
        - ONNX 模型文件部分损坏（load 成功但 predict 失败）
        - 输入数据格式异常（tokenizer 输出与模型输入不匹配）
        - 内存不足导致推理中断
      排查: grep '"action":"onnx.predict_failed"' logs/digital_life.log | tail -20
```

#### 3.2.4 降级率过高

```yaml
# ── P1: rerank 降级率 > 10% ──
- alert: RerankerFallbackRateHigh
  expr: |
    sum(rate(reranker_fallback_total[5m]))
    /
    (sum(rate(reranker_completed_total[5m])) + sum(rate(reranker_fallback_total[5m])))
    > 0.10
  for: 10m
  labels:
    severity: warning
    team: skill-mgmt
    category: fallback-rate
  annotations:
    summary: "Reranker 降级率 {{ $value | humanizePercentage }}（阈值 10%）"
    description: |
      降级率持续超过 10%，意味着 10% 的查询未走 Cross-Encoder 精排。
      注: reranker.disabled（手动关闭）不计入降级率，仅统计 fallback/predict_failed。
      影响: 整体排序质量下降，但功能可用。
      响应: 参考 V65_ONNX_DEPLOYMENT_PLAYBOOK.md §4 故障排查
```

### 3.3 P2 — 提示告警（1h 内关注）

#### 3.3.1 ONNX 配置错误（model_path_not_local_dir / onnx_file_not_found）

```yaml
# ── P2: ONNX 跳过（配置错误）──
- alert: RerankerOnnxConfigError
  expr: increase(reranker_load_failed_total{action="onnx.skip"}[1h]) > 0
  for: 1m
  labels:
    severity: info
    team: skill-mgmt
    category: config-error
  annotations:
    summary: "ONNX 跳过：{{ $labels.reason }}"
    description: |
      原因: {{ $labels.reason }}
      - model_path_not_local_dir: SKILL_RERANKER_MODEL 指向 HF 仓库 ID 而非本地路径
      - onnx_file_not_found: onnx/<variant> 文件不存在
      当前已降级到 PyTorch，但性能劣化 30x。
      修复: 检查 .env 中 SKILL_RERANKER_MODEL 和 SKILL_RERANKER_ONNX_VARIANT
```

#### 3.3.2 加载耗时过长

```yaml
# ── P2: ONNX 加载耗时 > 10s ──
- alert: RerankerOnnxLoadSlow
  expr: |
    increase(reranker_load_failed_total{action="onnx.loaded"}[1h]) > 0
    and on()
    avg_over_time(reranker_onnx_load_time_s[1h]) > 10
  for: 5m
  labels:
    severity: info
    team: skill-mgmt
    category: slow-load
  annotations:
    summary: "ONNX 加载耗时 {{ $value }}s（基线 1.12s）"
    description: |
      ONNX 模型加载耗时异常，可能原因:
        - 磁盘 I/O 慢（HDD 读取 266MB 模型）
        - 模型文件碎片化
        - 首次加载需 mmap，后续应缓存
      注: 此告警仅在进程重启后触发，常驻进程不影响。
```

---

## 4. 告警分级与响应 SLA

| 级别 | 触发条件 | 响应 SLA | 通知渠道 | 升级路径 |
|------|---------|---------|---------|---------|
| **P0 Critical** | ONNX 加载失败 / 双后端全挂 | 5min 内确认 | 钉钉 + Issue + 邮件 + 电话 | 15min 未响应升级到 P0-OnCall |
| **P1 Warning** | P99 超 SLO / 推理失败率 >5% / 降级率 >10% | 15min 内确认 | 钉钉 + Issue | 1h 未响应升级到 P1-OnCall |
| **P2 Info** | 配置错误 / 加载慢 | 1h 内关注 | 钉钉 | 不升级 |

---

## 5. 通知渠道集成

### 5.1 钉钉机器人（推荐，实时）

```yaml
# alertmanager/config.yml — 钉钉 webhook
receivers:
  - name: dingtalk-critical
    webhook_configs:
      - url: "${DINGTANG_WEBHOOK}"
        send_resolved: true
    dingtalk_config:
      msgtype: markdown
      title: "🚨 {{ .Status | toUpper }}: {{ .CommonLabels.alertname }}"
      text: |
        ## {{ .Status | toUpper }} - {{ .CommonLabels.alertname }}

        **级别**: {{ .CommonLabels.severity }}
        **团队**: {{ .CommonLabels.team }}
        **触发时间**: {{ .StartsAt.Format "2006-01-02 15:04:05" }}

        ### 告警详情
        {{ range .Alerts }}
        {{ .Annotations.description }}
        {{ end }}

        ### 响应手册
        {{ .CommonAnnotations.runbook_url }}
```

### 5.2 GitHub Issue（P0/P1 自动创建）

```yaml
# .github/workflows/reranker-alert-issue.yml
name: Reranker 告警 Issue 自动创建
on:
  workflow_dispatch:
    inputs:
      alert_name:
        description: '告警名称'
        required: true
      severity:
        description: '告警级别'
        required: true
      description:
        description: '告警详情'
        required: true

jobs:
  create_issue:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/github-script@v7
        with:
          script: |
            const alertName = '${{ github.event.inputs.alert_name }}';
            const severity = '${{ github.event.inputs.severity }}';
            const desc = '${{ github.event.inputs.description }}';

            // 去重: 查找最近 24h 内相同告警的 open issue
            const issues = await github.rest.issues.listForRepo({
              owner: context.repo.owner,
              repo: context.repo.repo,
              state: 'open',
              labels: `reranker-alert,${severity}`,
              since: new Date(Date.now() - 86400000).toISOString()
            });

            const existing = issues.data.find(i => i.title.includes(alertName));
            if (existing) {
              // 已存在则追加评论（聚合相同告警）
              await github.rest.issues.createComment({
                owner: context.repo.owner,
                repo: context.repo.repo,
                issue_number: existing.number,
                body: `**再次触发** - ${new Date().toISOString()}\n\n${desc}`
              });
              return;
            }

            await github.rest.issues.create({
              owner: context.repo.owner,
              repo: context.repo.repo,
              title: `[${severity.toUpperCase()}] ${alertName}`,
              body: [
                `## 告警详情`,
                '',
                desc,
                '',
                '## 响应手册',
                'https://github.com/nzt47/security-tools/blob/master/docs/V65_ONNX_DEPLOYMENT_PLAYBOOK.md',
                '',
                '---',
                '此 Issue 由 Reranker 监控告警自动创建，修复后请关闭。'
              ].join('\n'),
              labels: ['reranker-alert', severity, 'auto-generated']
            });
```

### 5.3 邮件（默认启用）

GitHub Actions 默认向 commit 作者发送失败邮件，无需额外配置。

---

## 6. Grafana Dashboard 面板设计

### 6.1 推荐面板布局

```
┌─────────────────────────────────────────────────────────────┐
│  Reranker 监控大盘                                            │
├──────────────────────────┬──────────────────────────────────┤
│  P99/P95/P50 延迟趋势     │  SLO 达成率（500ms 内）           │
│  (time series, 5m 粒度)   │  (stat: 99.5%)                   │
├──────────────────────────┼──────────────────────────────────┤
│  加载失败次数（按 action） │  降级率趋势                       │
│  (bar gauge by action)    │  (time series, 阈值线 10%)        │
├──────────────────────────┼──────────────────────────────────┤
│  推理 QPS                 │  候选数分布（candidate_count）    │
│  (time series)            │  (histogram)                      │
├──────────────────────────┴──────────────────────────────────┤
│  最近 20 条告警事件表                                        │
│  (table: time, alertname, severity, status)                 │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 关键 PromQL 查询

```promql
# 面板 1: P99/P95/P50 延迟
P99: histogram_quantile(0.99, sum(rate(rerank_duration_ms_bucket[5m])) by (le))
P95: histogram_quantile(0.95, sum(rate(rerank_duration_ms_bucket[5m])) by (le))
P50: histogram_quantile(0.50, sum(rate(rerank_duration_ms_bucket[5m])) by (le))

# 面板 2: SLO 达成率
1 - (sum(rate(rerank_duration_ms_bucket{le="500"}[5m])) / sum(rate(rerank_duration_ms_count[5m])))

# 面板 3: 加载失败次数（按 action 分组）
sum(increase(reranker_load_failed_total[1h])) by (action)

# 面板 4: 降级率
sum(rate(reranker_fallback_total[5m])) / (sum(rate(reranker_completed_total[5m])) + sum(rate(reranker_fallback_total[5m])))

# 面板 5: QPS
sum(rate(reranker_completed_total[5m]))
```

---

## 7. 告警抑制与聚合规则

### 7.1 抑制规则（避免告警风暴）

```yaml
# alertmanager/inhibit_rules.yml
inhibit_rules:
  # ── P0 双后端全挂时，抑制 P1 降级率告警（已知降级，无需重复告警）──
  - source_match:
      alertname: RerankerAllBackendsDown
      severity: critical
    target_match:
      alertname: RerankerFallbackRateHigh
      severity: warning
    equal: ['team']

  # ── P0 ONNX 加载失败时，抑制 P2 加载慢告警 ──
  - source_match:
      alertname: RerankerOnnxLoadFailed
      severity: critical
    target_match:
      alertname: RerankerOnnxLoadSlow
      severity: info
    equal: ['team']

  # ── P1 P99 SLO 违规时，抑制 P2 配置错误告警（根因相同）──
  - source_match:
      alertname: RerankerP99LatencySLOBreach
      severity: warning
    target_match:
      alertname: RerankerOnnxConfigError
      severity: info
    equal: ['team']
```

### 7.2 聚合规则（相同告警合并）

```yaml
# alertmanager/route.yml — 按告警名+级别分组，5min 聚合一次
route:
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h  # 同一告警 4h 内不重复通知
  receiver: dingtalk-critical
  routes:
    - match:
        severity: critical
      receiver: dingtalk-critical
      repeat_interval: 1h  # P0 告警 1h 重复一次
    - match:
        severity: warning
      receiver: dingtalk-warning
      repeat_interval: 4h
    - match:
        severity: info
      receiver: dingtalk-info
      repeat_interval: 12h
```

---

## 8. 告警响应手册（Runbook）

### 8.1 P0: ONNX 加载失败

```bash
# 步骤 1: 确认告警
grep '"action":"onnx.load_failed"' logs/digital_life.log | tail -5

# 步骤 2: 检查模型文件
ls -lh $SKILL_RERANKER_MODEL/onnx/
# 预期: model_quantized.onnx ~266MB

# 步骤 3: 切换 ONNX 变体（快速恢复）
# 编辑 .env:
#   SKILL_RERANKER_ONNX_VARIANT=model_int8.onnx  # P99 363ms，次优
# 重启服务

# 步骤 4: 若所有 ONNX 变体均失败，降级到 PyTorch
# 编辑 .env:
#   SKILL_RERANKER_USE_ONNX=false
# 注意: PyTorch P99 7960ms，会触发 P1 延迟告警，需尽快恢复 ONNX

# 步骤 5: 若 PyTorch 也失败，紧急关闭 reranker
# 编辑 .env:
#   SKILL_RERANKER_ENABLED=false
# 影响: 降级到 RRF 排序，P@3 下降 18.5%，但功能可用
```

### 8.2 P1: P99 延迟突增

```bash
# 步骤 1: 确认是否降级到 PyTorch
grep '"action":"onnx.fallback_to_pytorch"' logs/digital_life.log | tail -5
# 若有记录 → ONNX 不可用是根因，按 P0 流程修复 ONNX

# 步骤 2: 检查 CPU 负载
top -bn1 | head -20
# 若 load average > CPU 核数 → 资源争抢，扩容或限流

# 步骤 3: 检查 batch_size 异常
grep '"action":"rerank.completed"' logs/digital_life.log | \
  jq '.candidate_count' | sort -n | uniq -c | tail -10
# 若 candidate_count 异常大（>20）→ 上游 RRF 召回数过多，调整 top_k

# 步骤 4: 检查长稳压测基线（正常 RSS 增量应 <1MB/1000 次迭代）
ps -o rss= -p $(pgrep -f reranker)
# 若 RSS 持续增长 → 内存泄漏，重启服务并提 Issue
```

### 8.3 P1: 降级率过高

```bash
# 步骤 1: 区分降级类型
grep -c '"action":"rerank.fallback"' logs/digital_life.log  # 模型不可用
grep -c '"action":"rerank.predict_failed"' logs/digital_life.log  # 推理失败
grep -c '"action":"onnx.predict_failed"' logs/digital_life.log  # ONNX 推理失败

# 步骤 2: 若 predict_failed 占多数 → 模型文件损坏
# 重新下载模型: python scripts/predownload_model.py --reranker

# 步骤 3: 若 fallback 占多数 → 模型加载失败，按 P0 流程修复
```

---

## 9. 阈值回顾与调优

### 9.1 阈值基线（来自实测数据）

| 指标 | 基线值 | 告警阈值 | SLO | 备注 |
|------|--------|---------|-----|------|
| P99 延迟 | 258ms | 500ms (SLO) | 500ms | 实测留 1.94x 裕度 |
| P95 延迟 | 180ms | 400ms | - | 辅助指标 |
| 加载耗时 | 1.12s | 10s | - | 进程启动时才触发 |
| 降级率 | 0% | 10% | - | 正常应为 0% |
| 推理失败率 | 0% | 5% | - | 正常应为 0% |
| 长稳 RSS 增量 | -0.01MB/1000 次 | +10MB/1000 次 | - | 内存泄漏警戒 |

### 9.2 阈值回顾周期

- **每周**: 检查 P99/P95 趋势，若持续低于基线 50% 可下调阈值
- **每月**: 重新评估 SLO 达成率，调整阈值使误报率 <5%
- **每次模型升级**: 重新跑 `scripts/benchmark_v65_onnx_long_stability.py` 更新基线

### 9.3 环境变量化阈值（可选）

为支持灰度调整，可将阈值环境变量化（需在告警规则模板中引用）：

```bash
# .env 新增（可选，默认值即上述阈值）
RERANKER_ALERT_P99_MS=500           # P99 SLO 阈值
RERANKER_ALERT_FALLBACK_RATE=0.10   # 降级率阈值
RERANKER_ALERT_PREDICT_FAIL_RATE=0.05  # 推理失败率阈值
RERANKER_ALERT_LOAD_SLOW_S=10       # 加载慢阈值
```

---

## 10. 部署清单

### 10.1 一次性配置

- [ ] 部署 Promtail/Vector 采集 `logs/digital_life.log`
- [ ] 配置 log-based metrics 提取规则（§2.1）
- [ ] 部署 Alertmanager 并加载本文件规则
- [ ] 配置钉钉机器人 webhook 到 `${DINGTANG_WEBHOOK}` secret
- [ ] 部署 `.github/workflows/reranker-alert-issue.yml`
- [ ] Grafana 导入 §6 面板 JSON

### 10.2 验证步骤

```bash
# 1. 触发 P0 告警（临时改错模型路径）
export SKILL_RERANKER_MODEL=/nonexistent/path
python -c "from agent.skills_mgmt.reranker import SkillReranker; SkillReranker()._load_model()"
# 预期: 1min 内钉钉收到 P0 告警

# 2. 触发 P1 延迟告警（临时改用 PyTorch 后端）
export SKILL_RERANKER_USE_ONNX=false
# 跑一轮负载，P99 应超 500ms 触发告警

# 3. 验证抑制规则
# 同时触发 P0 和 P1，确认 P1 被抑制
```

---

## 11. 变更记录

| 日期 | 版本 | 变更 | 负责人 |
|------|------|------|--------|
| 2026-07-28 | v1.0 | 初始版本，覆盖 P0/P1/P2 三级告警 | - |

---

## 附录 A: 与现有 CI 告警的关系

本文件与 [.github/workflows/ci-failure-notify.yml](../.github/workflows/ci-failure-notify.yml) 互补：

| 维度 | CI 失败通知 | 本文件（Reranker 监控）|
|------|------------|----------------------|
| 触发源 | GitHub Actions workflow 失败 | 生产环境运行时指标 |
| 监控对象 | CI 流水线 | Reranker 推理服务 |
| 数据源 | workflow_run 事件 | 结构化 JSON 日志 |
| 响应时效 | 异步（workflow 完成后）| 实时（5min 窗口） |
| 通知渠道 | 钉钉 + Issue + 邮件 | 钉钉 + Issue + 邮件 |

**两者不冲突，可并存**：CI 通知关注构建/测试失败，本文件关注生产运行时性能与可用性。
