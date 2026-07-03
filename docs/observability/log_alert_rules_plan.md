# 日志系统自动化监控告警规则规划

> 生成时间：2026-07-04
> 关联文档：[log_dict_refactoring_summary.md](./log_dict_refactoring_summary.md)、[phase2_branch_leftover_issues.md](./phase2_branch_leftover_issues.md)
> 部署形态：Prometheus + Alertmanager
> 阈值策略：平衡（与 CI 守门 `log-perf-guard.yml` 阈值对齐）

---

## 1. 背景与目标

### 1.1 背景
Phase 2 完成了 log_dict 重构，消除了日志系统的双重序列化开销，实现了 3.93x 单函数加速、+74.71% 吞吐量提升、-47.66% P99 延迟。但现有监控告警体系未针对 log_dict 重构后的新架构进行适配，存在以下缺口：

- **指标暴露缺口**：`agent/utils/perf_monitor.py` 仅输出日志，未通过 `prometheus_client` 暴露指标
- **告警规则缺口**：现有 `monitoring/prometheus/alert_rules.yml` 未覆盖 log_dict 性能退化、双重序列化回归等场景
- **Alertmanager 缺失**：`prometheus.yml` 中 `alertmanagers.targets` 为空列表，告警无法分发
- **日志异常检测规则未对齐**：`log_system/analyzer.py` 的规则引擎未覆盖 log_dict 特有异常模式

### 1.2 监控目标（4 类）
1. **性能回归守护**：守护 log_dict 重构收益，检测序列化耗时退化、吞吐量下降
2. **运行时异常检测**：Filter 链失败、敏感数据脱敏失效、emoji 处理错误、日志写入失败率上升
3. **容量与资源监控**：日志体积增长、磁盘占用、日志速率突增、Loki 推送失败
4. **业务可观测性**：通过日志聚合检测业务异常（错误率突增、特定 module_name/action 异常）

### 1.3 阈值策略（平衡）
- 性能退化 ≥ 20% 告警（与 CI 守门 `min-speedup 1.2` 对齐）
- P99 退化 ≥ 30% 告警（与 CI 守门 `max-p99-us 500` 对齐）
- 错误率 ≥ 1% 告警（与 CI 守门 `max-error-rate 0.01` 对齐）
- 容量增长 ≥ 50% 告警（避免过早告警）

---

## 2. 现状分析

### 2.1 已有基础设施
| 组件 | 位置 | 功能 |
|---|---|---|
| Prometheus 指标暴露 | `agent/monitoring/prometheus.py` | 通过 `/metrics` 端点暴露 Counter/Histogram/Gauge |
| Loki 日志客户端 | `agent/monitoring/loki.py` | 日志查询/推送，本地 `data/logs` 回退 |
| 业务指标 | `agent/monitoring/business_metrics.py` | 4 类业务指标定义（用户交互、任务、知识库、扩展） |
| 资源监控 | `agent/monitoring/resource_monitor.py` | 内存/线程/文件句柄泄漏检测 |
| 告警通知 | `agent/monitoring/alert_notifier.py` | 6 种渠道（邮件、钉钉、Webhook、企业微信、Slack、短信） |
| 告警管理 | `agent/monitoring/alert_manager.py` + `alert_evaluator.py` | Python 端告警评估与分发 |
| 日志分析引擎 | `agent/log_system/analyzer.py` | 两阶段分析（规则引擎 + 统计异常检测） |
| 现有告警规则 | `monitoring/prometheus/alert_rules.yml` | V2 安全/性能/内存告警 |
| CI 性能守门 | `.github/workflows/log-perf-guard.yml` | 性能回归检测 + 邮件通知 |

### 2.2 关键缺口
| 缺口 | 影响 | 解决方向 |
|---|---|---|
| perf_monitor.py 未暴露 Prometheus 指标 | log_dict 性能数据无法被 Prometheus 采集 | 新增 `LogDictPerfMetrics` 类，暴露 Histogram/Counter |
| 无 log_dict 专用告警规则 | 性能退化无法自动告警 | 新增 `log_dict_alert_rules.yml` |
| Alertmanager targets 为空 | Prometheus 告警无法分发 | 配置 `alertmanager.yml` + 启动 Alertmanager |
| 日志内容异常检测未覆盖 log_dict 模式 | Filter 链异常无法检测 | 扩展 `analyzer.py` 规则引擎 |
| 无日志速率/体积监控 | 日志突增无法及时发现 | 新增 `log_volume_metrics` 指标 |

---

## 3. 监控指标设计

### 3.1 性能回归守护指标（新增）

**位置**：`agent/utils/perf_monitor.py` 扩展

```yaml
# 新增指标定义（伪代码，实际在 prometheus.py 中注册）
- name: log_dict_call_duration_seconds
  type: Histogram
  help: "log_dict() 调用耗时（秒）"
  buckets: [0.000001, 0.000005, 0.00001, 0.00005, 0.0001, 0.0005, 0.001]
  labels: [mode]  # mode: "new" | "old"

- name: log_dict_calls_total
  type: Counter
  help: "log_dict() 调用次数"
  labels: [mode, status]  # status: "success" | "failure"

- name: log_dict_pipeline_duration_seconds
  type: Histogram
  help: "完整日志管道耗时（log_dict → filter → format）"
  buckets: [0.00001, 0.0001, 0.001, 0.01, 0.1, 1.0]
  labels: [handler_type]  # handler_type: "console" | "file"

- name: log_dict_throughput_ops_per_second
  type: Gauge
  help: "log_dict 管道吞吐量（ops/s）"
  labels: [scenario]  # scenario: "single_thread" | "multi_thread"
```

### 3.2 运行时异常检测指标（新增）

```yaml
- name: log_filter_chain_failures_total
  type: Counter
  help: "Filter 链处理失败次数"
  labels: [filter_name, error_type]
  # filter_name: "DictToJsonFilter" | "EmojiFilter" | "SensitiveDataFilter"
  # error_type: "serialization" | "emoji_decode" | "sanitize" | "type_error"

- name: log_sensitive_data_unmasked_total
  type: Counter
  help: "敏感数据未脱敏次数（采样检测）"
  labels: [field_type]  # field_type: "password" | "api_key" | "token" | "email" | "phone"

- name: log_writer_failures_total
  type: Counter
  help: "日志写入失败次数"
  labels: [handler_type, error_type]
  # handler_type: "file" | "console" | "loki"
  # error_type: "disk_full" | "permission" | "loki_push" | "encoding"

- name: log_emoji_decode_failures_total
  type: Counter
  help: "Emoji 处理失败次数（GBK 编码兼容问题）"
```

### 3.3 容量与资源监控指标（新增）

```yaml
- name: log_volume_bytes_total
  type: Counter
  help: "日志写入字节总数"
  labels: [handler_type, level]  # level: "DEBUG" | "INFO" | "WARNING" | "ERROR"

- name: log_records_per_second
  type: Gauge
  help: "每秒日志记录数"
  labels: [level]

- name: log_disk_usage_bytes
  type: Gauge
  help: "日志目录磁盘占用（字节）"
  labels: [log_dir]

- name: log_loki_push_latency_seconds
  type: Histogram
  help: "Loki 推送延迟（秒）"
  buckets: [0.01, 0.1, 0.5, 1, 5, 10]
```

### 3.4 业务可观测性指标（扩展现有）

复用 `business_metrics.py` 已有指标，新增维度：

```yaml
- name: log_module_error_rate
  type: Gauge
  help: "按 module_name 分组的错误日志率"
  labels: [module_name]

- name: log_action_anomaly_score
  type: Gauge
  help: "按 action 分组的异常分数（统计引擎输出）"
  labels: [module_name, action]
```

---

## 4. 告警规则设计

### 4.1 规则文件组织

```
monitoring/prometheus/
├── prometheus.yml              # 主配置（更新 alertmanagers targets）
├── alert_rules.yml             # 现有 V2 安全/性能告警（保留）
├── log_dict_alert_rules.yml    # 新增：log_dict 性能回归守护
├── log_runtime_alert_rules.yml # 新增：日志运行时异常检测
├── log_volume_alert_rules.yml  # 新增：容量与资源监控
└── log_business_alert_rules.yml # 新增：业务可观测性
```

### 4.2 性能回归守护告警（log_dict_alert_rules.yml）

```yaml
groups:
  - name: log_dict_performance_regression
    interval: 30s
    rules:
      # log_dict 单函数耗时退化 ≥ 20%
      - alert: LogDictCallDurationRegression
        expr: |
          histogram_quantile(0.95, 
            sum(rate(log_dict_call_duration_seconds_bucket{mode="new"}[5m])) by (le)
          ) >
          histogram_quantile(0.95, 
            sum(rate(log_dict_call_duration_seconds_bucket{mode="old"}[5m])) by (le)
          ) * 1.2
        for: 5m
        labels:
          severity: warning
          category: performance_regression
        annotations:
          summary: "log_dict 调用耗时退化 ≥ 20%"
          description: "新模式 P95 耗时 {{ $value }}s，相比旧模式基线退化 ≥ 20%，可能存在性能回归。"
          runbook_url: "docs/observability/log_dict_refactoring_summary.md"

      # 完整管道 P99 耗时退化 ≥ 30%
      - alert: LogPipelineP99Regression
        expr: |
          histogram_quantile(0.99,
            sum(rate(log_dict_pipeline_duration_seconds_bucket[5m])) by (le, handler_type)
          ) > 0.0005
        for: 5m
        labels:
          severity: warning
          category: performance_regression
        annotations:
          summary: "日志管道 P99 耗时超过 500μs"
          description: "handler_type={{ $labels.handler_type }} 的 P99 耗时 {{ $value }}s 超过阈值 500μs。"

      # 吞吐量下降 ≥ 20%（与基线对比）
      - alert: LogDictThroughputDrop
        expr: |
          log_dict_throughput_ops_per_second{scenario="multi_thread"} <
          log_dict_throughput_ops_per_second{scenario="multi_thread"} offset 1h * 0.8
        for: 10m
        labels:
          severity: warning
          category: performance_regression
        annotations:
          summary: "log_dict 吞吐量下降 ≥ 20%"
          description: "多线程吞吐量 {{ $value }} ops/s，相比 1 小时前下降 ≥ 20%。"

      # 双重序列化回归检测（check_double_serialization.py 增量扫描发现新增）
      - alert: DoubleSerializationRegression
        expr: log_dict_double_serialization_detected_total > 0
        for: 0m
        labels:
          severity: critical
          category: performance_regression
        annotations:
          summary: "检测到双重序列化回归"
          description: "发现 {{ $value }} 处新的 json.dumps + json.loads 双重序列化调用，请检查最近提交。"
```

### 4.3 运行时异常检测告警（log_runtime_alert_rules.yml）

```yaml
groups:
  - name: log_runtime_anomalies
    interval: 30s
    rules:
      # Filter 链失败率 ≥ 1%
      - alert: LogFilterChainFailureRate
        expr: |
          sum(rate(log_filter_chain_failures_total[5m])) by (filter_name) /
          (sum(rate(log_filter_chain_failures_total[5m])) by (filter_name) + 
           sum(rate(log_dict_calls_total{status="success"}[5m]))) > 0.01
        for: 5m
        labels:
          severity: critical
          category: runtime_anomaly
        annotations:
          summary: "Filter 链失败率 ≥ 1%"
          description: "filter={{ $labels.filter_name }} 失败率 {{ $value | humanizePercentage }}，请检查 Filter 实现。"

      # 敏感数据未脱敏（任何一次都告警）
      - alert: SensitiveDataUnmasked
        expr: log_sensitive_data_unmasked_total > 0
        for: 0m
        labels:
          severity: critical
          category: runtime_anomaly
        annotations:
          summary: "检测到敏感数据未脱敏"
          description: "field_type={{ $labels.field_type }} 出现未脱敏记录，请检查 SensitiveDataFilter 规则。"

      # 日志写入失败率 ≥ 1%
      - alert: LogWriterFailureRate
        expr: |
          sum(rate(log_writer_failures_total[5m])) by (handler_type) /
          (sum(rate(log_writer_failures_total[5m])) by (handler_type) + 1) > 0.01
        for: 5m
        labels:
          severity: warning
          category: runtime_anomaly
        annotations:
          summary: "日志写入失败率 ≥ 1%"
          description: "handler_type={{ $labels.handler_type }} 失败率 {{ $value | humanizePercentage }}。"

      # Emoji 处理失败突增
      - alert: EmojiDecodeFailuresSpike
        expr: rate(log_emoji_decode_failures_total[5m]) > 0.1
        for: 5m
        labels:
          severity: warning
          category: runtime_anomaly
        annotations:
          summary: "Emoji 处理失败突增"
          description: "Emoji 解码失败率 {{ $value }}/s，可能存在新的 emoji 字符未覆盖。"
```

### 4.4 容量与资源监控告警（log_volume_alert_rules.yml）

```yaml
groups:
  - name: log_volume_and_resource
    interval: 60s
    rules:
      # 日志速率突增（相比 1 小时前增长 ≥ 50%）
      - alert: LogRateSpike
        expr: |
          sum(rate(log_records_per_second[5m])) >
          sum(rate(log_records_per_second[5m] offset 1h)) * 1.5
        for: 10m
        labels:
          severity: warning
          category: volume
        annotations:
          summary: "日志速率突增 ≥ 50%"
          description: "当前 {{ $value }} records/s，相比 1 小时前增长 ≥ 50%。"

      # 日志磁盘占用 ≥ 80%
      - alert: LogDiskUsageHigh
        expr: log_disk_usage_bytes / 1024 / 1024 / 1024 > 10
        for: 5m
        labels:
          severity: warning
          category: volume
        annotations:
          summary: "日志磁盘占用 ≥ 10GB"
          description: "log_dir={{ $labels.log_dir }} 占用 {{ $value }}GB，请清理或归档。"

      # Loki 推送延迟 P95 ≥ 1s
      - alert: LokiPushLatencyHigh
        expr: |
          histogram_quantile(0.95,
            sum(rate(log_loki_push_latency_seconds_bucket[5m])) by (le)
          ) > 1
        for: 5m
        labels:
          severity: warning
          category: volume
        annotations:
          summary: "Loki 推送延迟 P95 ≥ 1s"
          description: "Loki 推送延迟 {{ $value }}s，可能存在网络问题或 Loki 服务过载。"

      # Loki 推送失败率 ≥ 5%
      - alert: LokiPushFailureRate
        expr: |
          sum(rate(log_writer_failures_total{handler_type="loki", error_type="loki_push"}[5m])) /
          (sum(rate(log_writer_failures_total{handler_type="loki"}[5m])) + 1) > 0.05
        for: 5m
        labels:
          severity: warning
          category: volume
        annotations:
          summary: "Loki 推送失败率 ≥ 5%"
          description: "Loki 推送失败率 {{ $value | humanizePercentage }}，日志可能丢失。"
```

### 4.5 业务可观测性告警（log_business_alert_rules.yml）

```yaml
groups:
  - name: log_business_observability
    interval: 30s
    rules:
      # 特定模块错误率突增
      - alert: ModuleErrorRateSpike
        expr: log_module_error_rate > 0.1
        for: 5m
        labels:
          severity: warning
          category: business
        annotations:
          summary: "模块错误率突增"
          description: "module_name={{ $labels.module_name }} 错误率 {{ $value | humanizePercentage }}，超过 10%。"

      # Action 异常分数超过阈值
      - alert: ActionAnomalyDetected
        expr: log_action_anomaly_score > 0.8
        for: 10m
        labels:
          severity: warning
          category: business
        annotations:
          summary: "Action 异常分数超过 0.8"
          description: "module={{ $labels.module_name }} action={{ $labels.action }} 异常分数 {{ $value }}，请检查业务逻辑。"

      # ERROR 级别日志突增（相比 1 小时前增长 ≥ 100%）
      - alert: ErrorLogSpike
        expr: |
          sum(rate(log_volume_bytes_total{level="ERROR"}[5m])) >
          sum(rate(log_volume_bytes_total{level="ERROR"}[5m] offset 1h)) * 2
        for: 10m
        labels:
          severity: critical
          category: business
        annotations:
          summary: "ERROR 日志突增 ≥ 100%"
          description: "ERROR 级别日志相比 1 小时前增长 ≥ 100%，可能存在严重故障。"
```

---

## 5. Alertmanager 配置

### 5.1 alertmanager.yml（新增）

**位置**：`monitoring/alertmanager.yml`

```yaml
global:
  resolve_timeout: 5m
  smtp_smarthost: 'smtp.example.com:587'
  smtp_from: 'alerts@yunshu.local'
  smtp_auth_username: 'alerts@yunshu.local'
  smtp_auth_password: '<password>'

# 告警抑制规则
inhibit_rules:
  # 如果 critical 告警触发，抑制同模块的 warning 告警
  - source_match:
      severity: critical
    target_match:
      severity: warning
    equal: ['category', 'module_name']

# 路由规则
route:
  group_by: ['alertname', 'category', 'module_name']
  group_wait: 30s        # 首次告警等待 30s 聚合
  group_interval: 5m     # 同组告警间隔 5m
  repeat_interval: 4h    # 重复告警间隔 4h
  receiver: 'default'

  routes:
    # critical 告警立即发送
    - match:
        severity: critical
      receiver: 'critical-channel'
      group_wait: 0s
      repeat_interval: 1h

    # 性能回归告警发送到性能团队
    - match:
        category: performance_regression
      receiver: 'perf-team'
      repeat_interval: 2h

    # 运行时异常告警发送到 SRE
    - match:
        category: runtime_anomaly
      receiver: 'sre-channel'

receivers:
  - name: 'default'
    email_configs:
      - to: 'maintainers@yunshu.local'

  - name: 'critical-channel'
    email_configs:
      - to: 'oncall@yunshu.local'
    webhook_configs:
      - url: 'http://localhost:5001/critical-alert'
    # 钉钉通知（可选）
    # webhook_configs:
    #   - url: 'https://oapi.dingtalk.com/robot/send?access_token=<token>'

  - name: 'perf-team'
    email_configs:
      - to: 'perf-team@yunshu.local'

  - name: 'sre-channel'
    email_configs:
      - to: 'sre@yunshu.local'
    webhook_configs:
      - url: 'http://localhost:5001/sre-alert'
```

### 5.2 prometheus.yml 更新

```yaml
alerting:
  alertmanagers:
    - static_configs:
        - targets: ['localhost:9093']  # Alertmanager 默认端口

rule_files:
  - 'alert_rules.yml'                # 现有 V2 告警
  - 'log_dict_alert_rules.yml'       # 新增：性能回归守护
  - 'log_runtime_alert_rules.yml'    # 新增：运行时异常
  - 'log_volume_alert_rules.yml'    # 新增：容量与资源
  - 'log_business_alert_rules.yml'  # 新增：业务可观测性
```

---

## 6. 指标暴露实现路径

### 6.1 perf_monitor.py 扩展

**位置**：`agent/utils/perf_monitor.py`

新增 `LogDictPerfMetrics` 类，将现有日志输出转换为 Prometheus 指标：

```python
# 伪代码设计
from prometheus_client import Histogram, Counter, Gauge

class LogDictPerfMetrics:
    """log_dict 性能指标 Prometheus 暴露"""
    
    call_duration = Histogram(
        'log_dict_call_duration_seconds',
        'log_dict() 调用耗时',
        buckets=[1e-6, 5e-6, 1e-5, 5e-5, 1e-4, 5e-4, 1e-3],
        labelnames=['mode']
    )
    calls_total = Counter(
        'log_dict_calls_total',
        'log_dict() 调用次数',
        labelnames=['mode', 'status']
    )
    # ... 其他指标
```

**集成点**：在 `perf_trace` 上下文管理器中调用 `LogDictPerfMetrics.call_duration.observe()`。

### 6.2 log_system/ 指标暴露扩展

**位置**：`agent/log_system/handlers.py` 或新增 `metrics.py`

在日志 handler 中埋点：
- `FileHandler.emit()` 失败时递增 `log_writer_failures_total`
- `DictToJsonFilter.filter()` 失败时递增 `log_filter_chain_failures_total`
- `SensitiveDataFilter.filter()` 后采样检测未脱敏记录，递增 `log_sensitive_data_unmasked_total`

### 6.3 analyzer.py 规则扩展

**位置**：`agent/log_system/analyzer.py`

扩展 `ThresholdRule` 规则集，新增 log_dict 专用规则：
- `LogDictSlowRule`：检测 `log_dict` 调用耗时 > 1ms 的记录
- `FilterChainErrorRule`：检测 Filter 链异常日志
- `SensitiveDataLeakRule`：检测疑似未脱敏的敏感数据模式

---

## 7. 部署与集成路径

### 7.1 部署组件清单

| 组件 | 部署方式 | 端口 |
|---|---|---|
| Prometheus | Docker / 二进制 | 9090 |
| Alertmanager | Docker / 二进制 | 9093 |
| Loki | Docker / 二进制 | 3100 |
| Grafana | Docker / 二进制 | 3000 |
| 应用（指标源） | 现有部署 | 8000 |

### 7.2 docker-compose.yml 示例

**位置**：`monitoring/docker-compose.yml`（新增）

```yaml
version: '3.8'
services:
  prometheus:
    image: prom/prometheus:v2.45.0
    volumes:
      - ./prometheus:/etc/prometheus
      - prometheus-data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.retention.time=30d'
    ports: ['9090:9090']

  alertmanager:
    image: prom/alertmanager:v0.26.0
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml
    ports: ['9093:9093']

  loki:
    image: grafana/loki:2.9.0
    volumes:
      - loki-data:/loki
    ports: ['3100:3100']

  grafana:
    image: grafana/grafana:10.0.0
    volumes:
      - grafana-data:/var/lib/grafana
    ports: ['3000:3000']
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin

volumes:
  prometheus-data:
  loki-data:
  grafana-data:
```

### 7.3 集成步骤

1. **应用侧**（本 PR 范围）：
   - 扩展 `perf_monitor.py` 暴露 Prometheus 指标
   - 在 `log_system/handlers.py` 中埋点失败计数
   - 扩展 `analyzer.py` 规则引擎

2. **基础设施侧**（后续 PR）：
   - 创建 `monitoring/docker-compose.yml`
   - 创建 `alertmanager.yml`
   - 更新 `prometheus.yml` 添加 alertmanager targets 和新规则文件
   - 创建 Grafana 仪表盘

3. **CI 集成**（可选）：
   - 在 `log-perf-guard.yml` 中添加 `promtool check rules` 步骤验证规则语法
   - 添加 `amtool check-config` 验证 alertmanager.yml 语法

---

## 8. 阈值标准与 CI 守门对齐

### 8.1 阈值对齐表

| 指标 | CI 守门阈值（log-perf-guard.yml） | 运行时告警阈值 | 关系 |
|---|---|---|---|
| 吞吐量 | ≥ 5000 ops/s | 退化 ≥ 20% | 运行时更宽松（相对退化） |
| P99 延迟 | ≤ 500μs | 退化 ≥ 30% | 运行时关注相对退化 |
| 错误率 | ≤ 1% | ≥ 1% | 完全对齐 |
| 加速比 | ≥ 1.2x | 退化 ≥ 20% | 等价（1.0 / 1.2 ≈ 0.83，退化 17%） |

### 8.2 告警严重级别

| 级别 | 触发条件 | 响应时间 | 通知渠道 |
|---|---|---|---|
| critical | 敏感数据未脱敏、双重序列化回归、ERROR 日志突增 ≥ 100% | 立即 | 邮件 + Webhook + 钉钉 |
| warning | 性能退化 ≥ 20%、Filter 失败率 ≥ 1%、容量超限 | 5-10 分钟 | 邮件 + Webhook |
| info | 速率突增、Loki 延迟 | 30 分钟 | 邮件 |

---

## 9. 实施路线图

### Phase A：指标暴露（本 PR 后续 PR-1）
- [ ] 扩展 `agent/utils/perf_monitor.py` 暴露 Prometheus 指标
- [ ] 在 `agent/log_system/handlers.py` 中埋点失败计数
- [ ] 在 `agent/monitoring/prometheus.py` 中注册新指标
- [ ] 单元测试：验证指标暴露正确性

### Phase B：告警规则定义（PR-2）
- [ ] 创建 `monitoring/prometheus/log_dict_alert_rules.yml`
- [ ] 创建 `monitoring/prometheus/log_runtime_alert_rules.yml`
- [ ] 创建 `monitoring/prometheus/log_volume_alert_rules.yml`
- [ ] 创建 `monitoring/prometheus/log_business_alert_rules.yml`
- [ ] 更新 `monitoring/prometheus/prometheus.yml` 引用新规则文件
- [ ] CI 集成：`promtool check rules` 验证语法

### Phase C：Alertmanager 部署（PR-3）
- [ ] 创建 `monitoring/alertmanager.yml`
- [ ] 创建 `monitoring/docker-compose.yml`
- [ ] 更新 `prometheus.yml` 的 `alertmanagers.targets`
- [ ] 验证告警链路：指标 → Prometheus → Alertmanager → 邮件/Webhook

### Phase D：日志分析引擎扩展（PR-4）
- [ ] 扩展 `agent/log_system/analyzer.py` 规则引擎
- [ ] 新增 log_dict 专用规则（LogDictSlowRule、FilterChainErrorRule 等）
- [ ] 集成 `alert_notifier.py` 分发异常检测结果

### Phase E：Grafana 仪表盘（PR-5）
- [ ] 创建 log_dict 性能仪表盘
- [ ] 创建日志系统健康仪表盘
- [ ] 创建告警概览仪表盘

---

## 10. 风险与缓解

| 风险 | 等级 | 缓解措施 |
|---|---|---|
| Prometheus 指标暴露影响应用性能 | 中 | 使用 `prometheus_client` 的异步暴露，采样间隔 ≥ 15s |
| 告警噪声过多导致告警疲劳 | 中 | 严格阈值 + 抑制规则 + group_wait 聚合 |
| Alertmanager 单点故障 | 低 | 部署多副本（后续） |
| 指标基数爆炸（高基数 label） | 中 | 避免高基数 label（如 trace_id、user_id） |
| Loki 推送失败导致日志丢失 | 中 | 本地 `data/logs` 回退存储 + 失败重试 |

---

## 11. 验收标准

### 11.1 功能验收
- [ ] Prometheus `/metrics` 端点暴露所有新增指标
- [ ] Prometheus 规则语法通过 `promtool check rules`
- [ ] Alertmanager 配置通过 `amtool check-config`
- [ ] 模拟性能退化能触发告警
- [ ] 模拟 Filter 失败能触发告警
- [ ] 告警能通过 Alertmanager 分发到邮件/Webhook

### 11.2 性能验收
- [ ] 指标暴露对应用主路径性能影响 < 1%
- [ ] Prometheus 采集间隔 15s 下，CPU 占用 < 2%
- [ ] Alertmanager 告警延迟 < 30s

### 11.3 文档验收
- [ ] 告警规则文档完整（每个告警含 summary、description、runbook_url）
- [ ] 运行手册（runbook）链接有效
- [ ] 阈值标准与 CI 守门对齐表清晰
