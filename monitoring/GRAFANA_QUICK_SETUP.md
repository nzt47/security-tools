# Grafana 仪表盘快速配置指南

> 本指南提供快速配置 Grafana 仪表盘以实时查看云枢 Prometheus 指标的完整步骤。

## 一、快速启动（推荐）

### 方式 1：使用 Docker Compose（一键启动，推荐）

```bash
# 在【仓库根目录】执行，不要在 monitoring/ 下执行
docker compose -f docker-compose.monitoring.yml up -d

# 查看服务状态
docker compose -f docker-compose.monitoring.yml ps
```

启动后访问：
- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3000 (默认账号: admin/admin)

> **为什么必须写 `-f docker-compose.monitoring.yml`**：`monitoring/docker-compose.yml` 是旧快照，
> 它挂载的 `./monitoring/prometheus`、`./monitoring/grafana_dashboards` 相对该文件解析后指向
> `monitoring/monitoring/...`（不存在），而且会加载 `job_name` 为旧名的 scrape 配置（规则里的
> `job="yunshu"` 会全部匹配不到 ⇒ 告警永不触发）。权威配置只有仓库根目录那一份。

### 方式 2：手动 docker run（不推荐，仅在不能用 compose 时）

```bash
# Prometheus：只挂被权威配置引用的文件
docker run -d --name yunshu-prometheus -p 9090:9090 \
  -v ${PWD}/monitoring/prometheus.yml:/etc/prometheus/prometheus.yml \
  -v ${PWD}/monitoring/alerts.yml:/etc/prometheus/alerts.yml \
  -v ${PWD}/monitoring/recording_rules.yml:/etc/prometheus/recording_rules.yml \
  prom/prometheus --config.file=/etc/prometheus/prometheus.yml

# Grafana：挂 provisioning 的【两个】目录
docker run -d --name yunshu-grafana -p 3000:3000 \
  -v ${PWD}/monitoring/grafana/dashboards:/etc/grafana/provisioning/dashboards \
  -v ${PWD}/monitoring/grafana/datasources:/etc/grafana/provisioning/datasources \
  -e GF_METRICS_ENABLED=true \
  grafana/grafana
```

> 手动起的容器**不在** `yunshu-monitoring` 网络上，Grafana 数据源 URL 不能再用 `http://prometheus:9090`
> （要改成宿主可达地址）；这也正是推荐用 compose 的原因。
> `monitoring/start_monitoring.ps1` / `start_monitoring.sh` 内部执行的是不带 `-f` 的 `docker-compose up -d`，
> 会命中上面的旧快照，**不要用它们启动监控栈**。

---

## 二、导入仪表盘

### 方式 1：通过 Grafana UI 导入

1. 登录 Grafana (http://localhost:3000)
2. 左侧菜单 → **Dashboards** → **Import**
3. 选择 `monitoring/grafana/dashboards/` 下的 JSON 文件上传（**只有这个目录会被自动加载**）：
   - `business_metrics.json` —— 业务指标监控（数据最全，推荐先看这个）
   - `yunshu-skill-quality.json` —— 技能质量与幻觉
   - `reranker-dashboard.json` —— Reranker 延迟/降级
   - `hpa-skill-retrieval-dashboard.json` —— 技能检索延迟与 QPS
   - `yunshu-alerts-monitor.json` —— 告警规则监视
   - `yunshu-monitor.json` —— 请求速率/CPU/内存/安全拦截/95 分位（2026-10-02 修复后 6/6 面板有数据）
   - `yunshu-full-monitoring.json` —— 全链路（26 个内容面板：13 出数 + 2 待 5xx + 10 个部署/CI 无数据源 + 1 个无指标）
   - `p99-latency-tracker.json` / `skill-hpa-monitor.json` —— 技能检索 SLO / HPA（修复后分别 9/11、4/8 出数，
     空的是 k8s 侧面板，板级说明已写明原因）
4. 选择 Prometheus 数据源（uid `prometheus`）
5. 点击 **Import**

> 注意：`monitoring/grafana_dashboards/` 是**未被 compose 挂载**的旧目录，放那里的 JSON（含模板生成的看板）
> 必须手动 Import；旧的 `yunshu_v2_dashboard.json` 等 4 个看板已删除（它们 100% 引用不存在的指标名）。

### 方式 2：通过 API 导入

```bash
# 设置 Grafana API Key（在 Grafana UI 中创建）
GRAFANA_API_KEY="your_api_key_here"

# 导入全链路监控仪表盘
curl -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GRAFANA_API_KEY" \
  -d @monitoring/grafana/dashboards/yunshu-full-monitoring.json \
  http://localhost:3000/api/dashboards/db
```

### 方式 3：自动加载（使用 Provisioning）

Grafana 会自动加载 `grafana/provisioning/dashboards/` 目录下的仪表盘：

```yaml
# monitoring/grafana/dashboards/dashboard.yml (已配置，与仓库一致)
apiVersion: 1

providers:
  - name: 'Yunshu Dashboard'
    orgId: 1
    folder: ''            # 看板落在根目录，没有单独的文件夹
    folderUid: ''
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30   # 改 JSON 后最多 30 秒自动生效
    allowUiUpdates: true        # UI 改动会在下次扫描被 JSON 覆盖
    options:
      path: /etc/grafana/provisioning/dashboards
```

---

## 三、配置 Prometheus 数据源

### 自动配置（Provisioning）

数据源已通过 `monitoring/grafana/datasources/prometheus.yml` 自动配置：

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: true
    jsonData:
      timeInterval: "15s"
      httpMethod: POST
```

### 手动配置

1. Grafana UI → **Configuration** → **Data Sources**
2. 点击 **Add data source**
3. 选择 **Prometheus**
4. 配置参数：
   - **Name**: `Prometheus`
   - **URL**: `http://localhost:9090` 或 `http://prometheus:9090`
   - **Scrape interval**: `15s`
   - **HTTP Method**: `POST`
5. 点击 **Save & Test**
6. 确认显示绿色提示："Data source is working"

---

## 四、仪表盘面板说明（2026-10-02 修复后）

> **状态怎么读**：看板 JSON 已在 2026-10-02 的修复批次中对齐真实指标名。
> 看到空面板先对号入座：**保留但无数据源**（部署/CI 共 10 个面板，面板上已写说明）＞
> **本部署无对应指标**（活跃用户数）＞ **查询已修好但要等 5xx**（错误率类）——这三类都不是故障。
> 例外：`p99-latency-tracker.json` 的「P99 SLO 合规率」「HPA 阈值超标率」两个面板仍带
> `namespace="$namespace"`（**还没修**，见下面"其余看板怎么选"），k8s 侧 kube_*/cAdvisor/k6 面板
> 则永远没有数据源。
> 指标名的判定标准：`GET http://127.0.0.1:5678/metrics`、`GET /api/business/prometheus` 的实际输出
> （合并清单 `_scratch/live_names.txt`）+ `monitoring/recording_rules.yml`、`monitoring/health_recording_rules.yml`
> 里的 `yunshu:xxx:5m` 形态 recording rule。

### 全链路监控仪表盘 (`yunshu-full-monitoring.json`)

该看板 **26 个内容面板 = 13 出数 + 2 待 5xx + 10 无数据源 + 1 无指标**。逐面板对照：

#### 1. 服务健康状态概览
| 面板 | 原写法（已废弃） | 现在的实际查询 | 状态 |
|------|----------------|---------------|------|
| 服务状态 | `up{job="yunshu-app"}` | `up{job="yunshu"}` | ✅ 已于 2026-10-02 修复 |
| 24小时可用率 | — | `yunshu:uptime_percent:24h` | ✅ recording rule |
| CPU 使用率 | `system_cpu_usage_percent` | `yunshu_cpu_usage_percent` | ✅ 已于 2026-10-02 修复 |
| 内存使用率 | `system_memory_usage_percent` | `yunshu_memory_usage_percent` | ✅ 已于 2026-10-02 修复 |
| 响应时间 (P95) | — | `yunshu:latency_p95:5m` | ✅ recording rule |
| 错误率 | `Yunshu_error_total` 派生 | `yunshu:error_rate:5m * 100` | ✅ 已修复；**待 5xx**（本部署无 5xx 序列 ⇒ 暂不出数） |

#### 2. 请求流量与响应时间
| 面板 | 原写法（已废弃） | 现在的实际查询 | 状态 |
|------|----------------|---------------|------|
| 请求速率趋势 | `sum(rate(http_requests_total[5m])) by (endpoint)` | `sum(rate(yunshu_http_request_total[5m])) by (method, status)` | ✅ 已修复（该指标没有 `endpoint` 标签） |
| 响应时间分布 | — | `yunshu:latency_p50:5m` / `yunshu:latency_p95:5m` / `yunshu:latency_p99:5m` | ✅ recording rule（单位：秒） |

#### 3. 错误与告警监控
| 面板 | 原写法（已废弃） | 现在的实际查询 | 状态 |
|------|----------------|---------------|------|
| 错误分布趋势 | `sum(increase(Yunshu_error_total[5m])) by (severity)` | `sum(increase(yunshu_http_request_total{status=~"5.."}[5m])) by (status)` | ✅ 已修复；**待 5xx** |
| 告警统计 (每小时) | `sum(increase(Yunshu_alert_total[1h])) by (severity)` | `sum(increase(yunshu_security_blocks_total[1h])) by (level)` | ✅ 已于 2026-10-02 修复（真实标签是 `level`） |

#### 4. 部署与回滚监控（⚠ 本部署未接入数据源）
行标题已改为「部署与回滚监控（⚠ 本部署未接入数据源）」，**以下 5 个面板保留但恒空**，
每个面板都加了 `description` 说明原因（指向 `docs/CICD_METRICS_DEPLOYMENT_CHECKLIST.md`）：

| 面板 | 现在的查询（保留） | 状态 |
|------|------------------|------|
| 部署状态 | `Yunshu_deployment_status` | ⚠️ 保留、恒空（面板说明已加） |
| 24小时回滚次数 | `sum(increase(Yunshu_rollback_total[24h]))` | ⚠️ 同上 |
| 部署耗时 | `Yunshu_deployment_duration_seconds` | ⚠️ 同上 |
| 24小时部署失败次数 | `sum(increase(Yunshu_deployment_failures_total[24h]))` | ⚠️ 同上 |
| 部署历史趋势 | `sum(increase(Yunshu_deployment_total[1h])) by (status)` | ⚠️ 同上 |

#### 5. CI/CD 流水线监控（⚠ 本部署未接入数据源）
行标题同样已标注；**5 个面板保留但恒空**（无 pushgateway / CI 采集 job，改查询救不活）：

| 面板 | 现在的查询（保留） | 状态 |
|------|------------------|------|
| CI 流水线耗时 | `Yunshu_ci_pipeline_duration_seconds` | ⚠️ 保留、恒空（面板说明已加） |
| 测试覆盖率 | `Yunshu_ci_test_coverage_percent` | ⚠️ 同上 |
| 24小时测试失败次数 | `sum(increase(Yunshu_ci_test_failures_total[24h]))` | ⚠️ 同上 |
| 24小时构建失败次数 | `sum(increase(Yunshu_ci_build_failures_total[24h]))` | ⚠️ 同上 |
| CI/CD 各阶段运行趋势 | `sum(rate(Yunshu_ci_pipeline_runs_total[1h])) by (stage)` | ⚠️ 同上 |

#### 6. 业务指标监控
| 面板 | 原写法（已废弃） | 现在的实际查询 | 状态 |
|------|----------------|---------------|------|
| 记忆总数 | `Yunshu_memory_count` | `sum(yunshu_memory_storage_total)` | ✅ 已于 2026-10-02 修复（标题未改，读作"记忆存储量"） |
| 活跃用户数（本部署无指标） | `yunshu:active_users:5m`（仍未改） | 同上 —— 该 recording rule 已从 `recording_rules.yml` 删除 | ⚠️ 保留 + 标题/说明标注，恒空；**不要**用 `yunshu_user_logins_total`（登录计数）顶替 |
| 安全拦截总数 | `sum(security_blocks_total)` | `sum(yunshu_security_blocks_total)` | ✅ 已于 2026-10-02 修复 |
| 工具调用成功率 (%) | — | `yunshu:tool_call_success_rate:5m * 100` | ✅ recording rule |
| Token 使用量 (每小时) | `yunshu:token_usage:1h` | `sum(increase(llm_tokens_total[1h]))` | ✅ 已于 2026-10-02 修复 |
| 每小时成本 | `yunshu:cost_per_hour` | `sum(increase(llm_cost_usd_total[1h]))` | ✅ 已于 2026-10-02 修复 |

### 其余看板怎么选

| 看板 | 现状（2026-10-02 修复后） |
|------|------------------------|
| `business_metrics.json` | ✅ 11/11 有数据（1 个 Loki 面板除外），**日常排查首选** |
| `yunshu-skill-quality.json`、`reranker-dashboard.json` | ✅ 全部有数据 |
| `yunshu-monitor.json` | ✅ **6/6 有数据**（原 5 类无前缀旧名已全部改名，不再是全空看板） |
| `yunshu-alerts-monitor.json` | ✅ 10/11 有数据；错误率面板已修好，待出现 5xx 才出数 |
| `hpa-skill-retrieval-dashboard.json` | ⚠️ 4/6 有数据；「HPA 副本数变化」「CPU 利用率」需 kube-state-metrics / cAdvisor（本部署无采集源，板级说明已加） |
| `p99-latency-tracker.json` | ⚠️ 9/11 有数据；「P99 SLO 合规率」「HPA 阈值超标率」两个面板**仍带** `namespace="$namespace"`（变量当前值 `production`）⇒ 仍空，建议删掉该过滤 |
| `skill-hpa-monitor.json` | ⚠️ 4/8 有数据；HPA 副本数、CPU、k6 压测、扩缩容事件 4 个面板无采集源（板级说明已加） |

---

## 五、告警配置

### Grafana 告警规则示例

以下表达式全部只用真实指标；`up` / `ALERTS` 是 Prometheus 内置序列。

#### 1. 服务宕机告警
```yaml
条件: up{job="yunshu"} == 0      # job 名以 monitoring/prometheus.yml 为准（旧文写的 job 名在本部署不存在）
评估周期: 1m
持续时间: 1m
通知渠道: Email/Webhook/Slack
```

#### 2. 高错误率告警
```yaml
条件: yunshu:error_rate:5m * 100 > 5
评估周期: 1m
持续时间: 2m
严重级别: warning
```

> ⚠️ **本部署暂时看不到这条序列**：`yunshu:error_rate:5m` 的分子是
> `yunshu_http_request_total{status=~"5.."}`，而实测只有 200/401/404 等状态码、**没有 5xx 序列** ⇒
> "空 ÷ 有值 = 空"，面板与告警都取不到数（不是 0）。出现 5xx 后即恢复正常。
> 想监控真实流量健康度，用 `business-metrics` 看板里按 `status` 分组的请求速率（401/404 占比）。

#### 3. 响应时间告警
```yaml
条件: yunshu:latency_p95:5m * 1000 > 1000   # recording rule 单位是秒，×1000 换成毫秒
评估周期: 1m
持续时间: 3m
严重级别: warning
```

#### 4. 业务指标抓取失败（替代原"部署失败告警"）
原第 4/5 条（`yunshu_deployment_failures_total`、`yunshu_rollback_total`）依赖的指标
**本部署未接入数据源（无 pushgateway / CI 采集 job）⇒ 规则不会触发，属于死规则**，已删。
改用真实可用的采集健康规则：

```yaml
条件: up{job="yunshu-business"} == 0    # 业务指标端点 /api/business/prometheus 抓取失败
评估周期: 5m
持续时间: 5m
严重级别: critical
```

#### 5. 危险操作拦截告警
```yaml
条件: sum(yunshu_security_blocks_total{level="critical"}) > 0
评估周期: 1m
持续时间: 0s
严重级别: critical
```

#### 6. 交互耗时告警（业务端点是分位数样本，不是直方图）
```yaml
条件: max(yunshu_interaction_duration_seconds{quantile="0.95"}) > 3   # 单位：秒
评估周期: 1m
持续时间: 2m
严重级别: warning
```

### 配置通知渠道

#### Email 通知
1. Grafana UI → **Alerting** → **Contact points**
2. 添加 Email 联系人
3. 配置 SMTP（在 `grafana.ini` 中）：
```ini
[smtp]
enabled = true
host = smtp.example.com:587
user = your_email@example.com
password = your_password
from_address = grafana@example.com
```

#### Webhook 通知
```json
{
  "name": "Webhook",
  "type": "webhook",
  "settings": {
    "url": "http://your-webhook-endpoint.com/alerts",
    "httpMethod": "POST"
  }
}
```

#### 企业微信/钉钉通知
使用 Webhook 方式，配置企业微信/钉钉机器人 Webhook URL。

---

## 六、验证步骤

### 1. 验证 Prometheus 数据采集

```bash
# 检查 Prometheus Targets
curl http://localhost:9090/api/v1/targets

# 查询指标（job 名 = yunshu，权威来源 monitoring/prometheus.yml）
curl 'http://localhost:9090/api/v1/query?query=up{job="yunshu"}'
curl 'http://localhost:9090/api/v1/query?query=up{job="yunshu-business"}'
curl 'http://localhost:9090/api/v1/query?query=yunshu:error_rate:5m'

# 确认指标名真实存在（写看板/告警前必做）
curl -s http://127.0.0.1:5678/metrics | grep '^# TYPE' | grep -E 'security_blocks|interaction|cpu_usage'
curl -s http://127.0.0.1:5678/api/business/prometheus | grep '^# TYPE' | grep interaction
```

### 2. 验证 Grafana 数据源

```bash
# 测试数据源连接
curl -H "Authorization: Bearer $GRAFANA_API_KEY" \
  http://localhost:3000/api/datasources/proxy/1/api/v1/query?query=up
```

### 3. 验证仪表盘数据

在 Grafana UI 中：
1. 打开仪表盘
2. 检查各面板是否显示数据
3. 确认无 "No data" 提示
4. 检查时间范围设置（默认: now-1h to now）

---

## 七、常见问题排查

### 问题 1: 仪表盘显示 "No data"

**原因**:
- Prometheus 未采集到指标
- 数据源配置错误
- 时间范围不匹配

**解决方案**:
```bash
# 1. 检查 Prometheus Targets（4 个 job 是否 UP）
curl http://localhost:9090/targets

# 2. 检查应用是否暴露指标（端口是 5678，不是 8000）
curl -s http://127.0.0.1:5678/metrics | head
curl -s http://127.0.0.1:5678/api/business/prometheus | head

# 3. 检查【被挂载】的权威配置（不是 monitoring/prometheus/prometheus.yml 那份未挂载的快照）
cat monitoring/prometheus.yml
```

> 先排除「面板本来就该是空的」（2026-10-02 修复后的空面板只剩三类，都不是故障）：
> ① 部署/CI 共 10 个面板——保留但无数据源，面板上已写说明；
> ② `yunshu-full-monitoring` 的「活跃用户数（本部署无指标）」——本部署没有该指标；
> ③ 错误率/错误分布 3 个面板——查询已修好，但本部署没有 5xx 序列，出现 5xx 才出数。
> 另外 `p99-latency-tracker.json` 的「P99 SLO 合规率」「HPA 阈值超标率」两个面板仍带
> `namespace="$namespace"` ⇒ 仍空（这是**还没修**，不是设计如此）；k8s 侧的
> `kube_*` / cAdvisor / k6 面板则永远没有数据源。

### 问题 2: 数据源连接失败

**原因**:
- Prometheus 未启动
- 网络隔离（Docker 网络问题）
- URL 配置错误

**解决方案**:
```bash
# 1. 检查 Prometheus 状态
docker ps | grep prometheus

# 2. 检查网络连接（compose 里的网络名是 yunshu-monitoring，不是 monitoring_default）
docker network inspect yunshu-monitoring

# 3. 使用正确的 URL
# Docker Compose: http://prometheus:9090
# 本地运行: http://localhost:9090
```

### 问题 3: 告警不触发

**原因**:
- 告警规则配置错误
- 通知渠道未配置
- 告警状态未达到触发条件

**解决方案**:
```bash
# 1. 检查告警规则状态
curl -H "Authorization: Bearer $GRAFANA_API_KEY" \
  http://localhost:3000/api/alerts

# 2. 检查通知渠道
curl -H "Authorization: Bearer $GRAFANA_API_KEY" \
  http://localhost:3000/api/alert-notifications

# 3. 手动测试告警
# 在 Grafana UI 中点击 "Test" 按钮
```

---

## 八、性能优化建议

### 1. 数据保留策略

保留期是 **Prometheus 的启动参数**，写在 `docker-compose.monitoring.yml` 的 `command` 里
（`prometheus.yml` 里的 `storage.tsdb` 段 Prometheus **不读**，写了也不生效——旧文档此处是错的）：

```yaml
command:
  - '--config.file=/etc/prometheus/prometheus.yml'
  - '--storage.tsdb.path=/prometheus'
  - '--storage.tsdb.retention.time=30d'   # 保留 30 天（新增行）
  - '--storage.tsdb.retention.size=10GB'  # 最大存储（可选）
  - '--web.enable-lifecycle'
```

改完执行 `docker compose -f docker-compose.monitoring.yml up -d` 生效；回退 = 删掉新增那两行。

### 2. Recording Rules（预聚合）

使用 `monitoring/recording_rules.yml` 中定义的预聚合规则：
- `yunshu:error_rate:5m` - 错误率预聚合
- `yunshu:latency_p50:5m` / `yunshu:latency_p95:5m` / `yunshu:latency_p99:5m` - 响应时间分位预聚合（单位：秒）
- `yunshu:security_blocks:1h` - 每小时安全拦截（按 `level`/`rule`）
- `yunshu:uptime_percent:24h` - 可用率预聚合

### 3. 仪表盘刷新频率

根据需求调整刷新频率：
- 实时监控: 5s - 10s
- 日常监控: 30s - 1m
- 历史分析: 5m - 15m

---

## 九、进阶配置

### 1. 多环境仪表盘

为不同环境创建仪表盘副本：
- Development: `yunshu-dev-monitoring.json`
- Staging: `yunshu-staging-monitoring.json`
- Production: `yunshu-prod-monitoring.json`

### 2. 自定义变量

在仪表盘 Templating 中添加变量：
- `environment`: 环境选择 (dev/staging/production)
- `endpoint`: API 端点选择
- `instance`: 实例选择

### 3. 嵌入外部系统

将 Grafana 仪表盘嵌入到其他系统：
```html
<iframe 
  src="http://grafana:3000/d/yunshu-full-monitoring?kiosk=1" 
  width="100%" 
  height="600px"
></iframe>
```

---

## 十、相关文档

- [Prometheus 官方文档](https://prometheus.io/docs/introduction/overview/)
- [Grafana 官方文档](https://grafana.com/docs/grafana/latest/)
- [Grafana Dashboard JSON Schema](https://grafana.com/developers/grafana/docs/json-schema/)
- [Prometheus Recording Rules](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/)
- [Grafana Alerting](https://grafana.com/docs/grafana/latest/alerting/)

---

**文档版本**: 2.1（2026-10-02 对齐看板 JSON 修复：第 4 节改为「原写法（已废弃）→ 现在的实际查询 → 状态」）  
**最后更新**: 2026-10-02  
**维护者**: 云枢开发团队