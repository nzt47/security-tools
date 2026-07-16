# Grafana 三级熔断器仪表盘配置说明

> **仪表盘文件**: `monitoring/grafana_circuit_breaker_dashboard.json`
> **UID**: `circuit-breaker-dashboard`
> **Schema 版本**: 38
> **适用 Grafana 版本**: 9.0+
> **最后更新**: 2026-07-17

---

## 一、仪表盘概述

本仪表盘用于可视化三级熔断器（SESSION/USER/GLOBAL）的实时状态、触发趋势和告警信息，配合 Prometheus 告警规则 `monitoring/circuit_breaker_alerts.yml` 使用。

**指标来源**：`agent/monitoring/business_metrics.py` 的 `BusinessMetricsCollector`

**核心指标**：
| 指标名 | 类型 | Labels | 说明 |
|--------|------|--------|------|
| `yunshu_circuit_breaker_trigger_total` | counter | breaker_name, from_state, to_state, reason | 熔断器状态转换次数 |
| `yunshu_circuit_breaker_state` | gauge | breaker_name, state | 熔断器当前状态（1=活跃） |
| `yunshu_degrade_trigger_total` | counter | module, level, reason | 降级触发次数 |
| `yunshu_rate_limit_trigger_total` | counter | level, endpoint, user_id, reason | 限流触发次数 |

---

## 二、导入方式

### 方式 1：Grafana UI 导入（推荐）

1. 登录 Grafana Web 界面
2. 左侧菜单 → **Dashboards** → **Import**
3. 点击 **Upload JSON file**
4. 选择 `monitoring/grafana_circuit_breaker_dashboard.json`
5. 在 **Prometheus** 下拉框中选择数据源
6. 点击 **Import**

### 方式 2：API 导入

```bash
# 通过 Grafana HTTP API 导入
curl -X POST \
  http://<grafana-host>:3000/api/dashboards/db \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d "{
    \"dashboard\": $(cat monitoring/grafana_circuit_breaker_dashboard.json),
    \"overwrite\": true,
    \"folderUid\": null
  }"
```

### 方式 3：Provisioning 自动加载

在 Grafana provisioning 配置目录创建：

```yaml
# /etc/grafana/provisioning/dashboards/circuit_breaker.yml
apiVersion: 1
providers:
  - name: 'Circuit Breaker Dashboards'
    orgId: 1
    folder: '稳定性监控'
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: true
    options:
      path: /var/lib/grafana/dashboards
```

将 JSON 文件复制到 `/var/lib/grafana/dashboards/` 目录，Grafana 会自动加载。

---

## 三、数据源配置

| 配置项 | 值 | 说明 |
|--------|-----|------|
| 数据源类型 | Prometheus | 时序数据库 |
| 数据源名称 | `Prometheus` | 默认值，可通过仪表盘变量切换 |
| 变量名 | `datasource` | 支持运行时切换数据源 |

**Prometheus 数据源配置示例**：

```yaml
# /etc/grafana/provisioning/datasources/prometheus.yml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://<prometheus-host>:9090
    isDefault: true
    editable: true
```

---

## 四、面板说明

仪表盘共 10 个面板，分为 5 行布局：

### 第 1 行：概览面板（y=0, h=5）

| 面板 ID | 标题 | 类型 | PromQL | 阈值 | 用途 |
|---------|------|------|--------|------|------|
| 1 | 熔断器总体状态 | stat | `sum(yunshu_circuit_breaker_state{state="open"})` | green→yellow(1)→red(3) | 总览：OPEN 熔断器数量 |
| 2 | 当前 OPEN 熔断器 | stat | `yunshu_circuit_breaker_state{state="open"}` | green→red(1) | 列出所有 OPEN 的熔断器名称 |
| 3 | 最近 1h 触发次数 | stat | `increase(yunshu_circuit_breaker_trigger_total[1h])` | green→yellow(3)→red(10) | 按熔断器分组的触发次数 |
| 4 | 降级触发（1h） | stat | `increase(yunshu_degrade_trigger_total[1h])` | green→yellow(5)→red(20) | 按模块分组的降级次数 |

### 第 2 行：趋势图（y=5, h=8）

| 面板 ID | 标题 | 类型 | PromQL | 用途 |
|---------|------|------|--------|------|
| 10 | 熔断器触发趋势（按级别） | timeseries | `rate(yunshu_circuit_breaker_trigger_total[5m])` | 5 分钟速率趋势，按 breaker_name + from_state→to_state 分组 |

**图表配置**：
- 绘制方式：平滑曲线（smooth）
- 填充透明度：20%
- 图例：右侧表格，显示 mean 和 max
- 阈值：green→yellow(0.1)→red(0.5) ops

### 第 3 行：三级状态分布（y=13, h=6）

| 面板 ID | 标题 | 类型 | PromQL | 用途 |
|---------|------|------|--------|------|
| 20 | 三级熔断器状态分布 | stat | `sum by (state) (yunshu_circuit_breaker_state)` | CLOSED/OPEN/HALF_OPEN 总数 |
| 21 | SESSION 级熔断器 | stat | `yunshu_circuit_breaker_state{breaker_name=~".*session.*"}` | SESSION 级明细 |
| 22 | USER/GLOBAL 级熔断器 | stat | `yunshu_circuit_breaker_state{breaker_name=~".*(user\|global).*"}` | USER/GLOBAL 级明细 |

**状态颜色映射**：
| 状态 | 颜色 | 含义 |
|------|------|------|
| closed | green | 正常放行 |
| half_open | yellow | 半开探测中 |
| open | red | 熔断拒绝请求 |

### 第 4 行：详细趋势（y=19, h=8）

| 面板 ID | 标题 | 类型 | PromQL | 用途 |
|---------|------|------|--------|------|
| 30 | 状态转换累计（按 from→to） | timeseries | `increase(yunshu_circuit_breaker_trigger_total[1h])` | 1 小时累计，堆叠柱状图 |
| 31 | 限流触发趋势 | timeseries | `rate(yunshu_rate_limit_trigger_total[5m])` | 5 分钟速率，按 endpoint + level 分组 |

**面板 30 配置**：
- 绘制方式：柱状图（bars）
- 堆叠模式：normal
- 填充透明度：80%
- 图例：底部表格，显示 sum

### 第 5 行：告警规则引用（y=27, h=4）

| 面板 ID | 标题 | 类型 | 用途 |
|---------|------|------|------|
| 40 | 告警规则引用 | text | Markdown 表格，列出 8 条告警规则 |

---

## 五、告警规则关联

本仪表盘配合 `monitoring/circuit_breaker_alerts.yml` 使用，告警规则共 4 组 8 条：

| 告警名称 | 严重级别 | 触发条件 | 对应面板 |
|----------|----------|----------|----------|
| CircuitBreakerGlobalTriggered | critical | GLOBAL 级熔断触发 | 面板 1, 2, 22 |
| CircuitBreakerFrequentTrigger | warning | 5min 内触发 > 3 次 | 面板 3, 10 |
| CircuitBreakerStuckOpen | warning | OPEN 状态 > 10min | 面板 1, 2 |
| CircuitBreakerRecoveryLoop | warning | HALF_OPEN→OPEN 循环 > 3 次 | 面板 10, 30 |
| CircuitBreakerUserLevelTriggered | warning | USER 级熔断触发 | 面板 22 |
| DegradeFrequentTrigger | warning | 5min 内降级 > 5 次 | 面板 4 |
| RateLimitFrequentTrigger | warning | 5min 内限流 > 10 次 | 面板 31 |
| CircuitBreakerMetricsMissing | warning | 指标缺失 5min | 全部面板 |

**告警规则加载方式**：
```bash
# Prometheus 配置文件 prometheus.yml
rule_files:
  - /etc/prometheus/rules/circuit_breaker_alerts.yml
```

---

## 六、仪表盘变量

| 变量名 | 类型 | 查询 | 默认值 | 用途 |
|--------|------|------|--------|------|
| `datasource` | datasource | `prometheus` | `Prometheus` | 运行时切换数据源 |

**添加时间范围变量**（可选）：
如需按时间范围筛选，可在 Grafana UI 的 **Settings → Variables** 中添加：
- `time_range`：interval 变量，选项 `1h, 6h, 24h, 7d`

---

## 七、阈值说明

### 状态阈值

| 指标 | green | yellow | red | 说明 |
|------|-------|--------|-----|------|
| OPEN 熔断器数量 | 0 | 1-2 | ≥3 | 面板 1 |
| 1h 触发次数 | 0-2 | 3-9 | ≥10 | 面板 3 |
| 1h 降级次数 | 0-4 | 5-19 | ≥20 | 面板 4 |
| 触发速率（5min） | 0-0.09 | 0.1-0.49 | ≥0.5 | 面板 10 |
| 限流速率（5min） | 0-0.49 | 0.5-1.99 | ≥2 | 面板 31 |

### 颜色含义
- **green**：正常，无需关注
- **yellow**：警告，需关注但无需立即处理
- **red**：严重，需立即处理

---

## 八、时间配置

| 配置项 | 默认值 | 可选范围 |
|--------|--------|----------|
| 时间范围 | `now-6h` | 5m, 15m, 1h, 6h, 12h, 24h, 7d, 30d |
| 刷新间隔 | 30s | 5s, 10s, 30s, 1m, 5m, 15m, 30m, 1h, 2h, 1d |
| 时区 | browser | 跟随浏览器 |

---

## 九、文档链接

仪表盘顶部包含 2 个快捷链接：

| 链接 | 目标 |
|------|------|
| 部署检查清单 | `docs/DEPLOYMENT_CHECKLIST_circuit_breaker.md` |
| 技术文档 | `docs/circuit_breaker_and_log_redaction.md` |

---

## 十、常见问题

### Q1: 面板显示 "No data"

**原因**：Prometheus 未采集到熔断器指标

**排查步骤**：
1. 检查 `BusinessMetricsCollector` 是否已初始化
2. 验证 `/metrics` 端点是否输出 `yunshu_circuit_breaker_*` 指标
3. 检查 Prometheus 是否正确抓取目标
4. 运行巡检脚本：`python scripts/post_deploy_inspection.py --verbose`

### Q2: 面板 2（当前 OPEN）为空

**原因**：所有熔断器处于 CLOSED 状态（正常）

**说明**：这是预期行为，表示系统正常运行。当有熔断器触发时，面板会显示对应名称。

### Q3: 如何修改阈值

1. 进入 Grafana UI → 打开仪表盘
2. 点击面板标题 → **Edit**
3. 在 **Thresholds** 区域修改值
4. 保存仪表盘

### Q4: 如何添加新的熔断器指标

1. 在 `agent/monitoring/business_metrics.py` 中定义新指标
2. 在 `BUSINESS_METRICS_DEFINITIONS` 中注册
3. 在 Grafana UI 中添加新面板，使用新指标名
4. 导出更新后的 JSON 文件

### Q5: Provisioning 不自动加载

**排查步骤**：
1. 检查 Grafana 日志：`journalctl -u grafana-server -f`
2. 验证 JSON 文件权限：`chmod 644 /var/lib/grafana/dashboards/*.json`
3. 确认 provisioning 配置路径正确
4. 重启 Grafana：`systemctl restart grafana-server`

---

## 十一、相关文件

| 文件 | 说明 |
|------|------|
| [grafana_circuit_breaker_dashboard.json](grafana_circuit_breaker_dashboard.json) | 仪表盘 JSON 配置 |
| [circuit_breaker_alerts.yml](circuit_breaker_alerts.yml) | Prometheus 告警规则 |
| [../docs/DEPLOYMENT_CHECKLIST_circuit_breaker.md](../docs/DEPLOYMENT_CHECKLIST_circuit_breaker.md) | 部署检查清单 |
| [../docs/circuit_breaker_and_log_redaction.md](../docs/circuit_breaker_and_log_redaction.md) | 三级熔断器技术文档 |
| [../scripts/post_deploy_inspection.py](../scripts/post_deploy_inspection.py) | 上线后巡检脚本 |

---

**文档版本**: v1.0
**生成时间**: 2026-07-17
**维护者**: circuit_breaker 模块负责人
