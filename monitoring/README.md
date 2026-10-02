# 云枢监控堆栈

> Prometheus + Grafana + 看板。**本文与 `GRAFANA_SETUP_GUIDE.md` 都以本部署实测为准**：
> 权威 scrape 配置是 `monitoring/prometheus.yml`（compose 挂载到 `/etc/prometheus/prometheus.yml`），
> 权威看板目录是 `monitoring/grafana/dashboards/`。

## 目录结构

```
monitoring/
├── prometheus.yml                  # 【权威】scrape 配置（4 个 job：yunshu / yunshu-business / prometheus / grafana）
├── alerts.yml                      # 告警规则（prometheus.yml 的 rule_files 逐条挂载，改一处要同时改挂载）
├── alerts_production.yml / alerts_safe_file_reader.yml / circuit_breaker_alerts.yml
├── health_alerts.yml               # 健康度体系告警
├── recording_rules.yml             # 预聚合记录规则（yunshu:xxx:5m）
├── health_recording_rules.yml      # 健康度体系记录规则（yunshu:health:*）
├── prometheus/                     # 历史快照目录（内含未被挂载的 prometheus.yml 副本 + rules/ 子目录）
├── grafana/
│   ├── dashboards/                 # ✅ 唯一被 compose 挂载、自动加载的看板目录（9 个看板 + dashboard.yml）
│   ├── datasources/prometheus.yml  # 数据源（uid=prometheus、url=http://prometheus:9090）
│   └── alerting/                   # Grafana 统一告警规则（*.yml 自动加载）
├── grafana_dashboards/             # ⚠️ 旧看板目录：**未被 compose 挂载**，需手动 Import（见其 README）
├── docker-compose.yml              # ⚠️ 旧快照，路径解析不到，勿用
├── GRAFANA_SETUP_GUIDE.md          # Grafana 接入指南（真实指标版）
├── GRAFANA_QUICK_SETUP.md          # 快速配置
└── GRAFANA_EMAIL_ALERT_GUIDE.md    # Email 告警
```

> 唯一可用的 compose 文件是**仓库根目录**的 `docker-compose.monitoring.yml`。

## 快速开始

```bash
# 1. 启动监控栈（在【仓库根目录】执行）
docker compose -f docker-compose.monitoring.yml up -d

# 2. 启动云枢本体（指标源：http://127.0.0.1:5678/metrics + /api/business/prometheus）
python app_server.py

# 3. 访问
#   Prometheus: http://localhost:9090
#   Grafana:    http://localhost:3000（默认 admin/admin）
# 4. 看板：Dashboards →「业务指标监控」（数据最全）/「云枢全链路监控仪表盘」/「v6.5 ONNX Reranker 监控大盘」
```

> **不要用 `monitoring/docker-compose.yml` 或 `monitoring/start_monitoring.ps1|sh` 启动**：
> 它们是旧快照，挂载路径（`./monitoring/prometheus` 等）相对该目录解析后不存在，
> 且加载的是 job 名为旧名的 scrape 配置 ⇒ `job="yunshu"` 的规则全部匹配不到。
> 回退：`docker compose -f docker-compose.monitoring.yml down`（加 `-v` 会删数据卷，慎用）。

## 服务访问

| 服务 | 地址 | 默认凭证 |
|------|------|---------|
| Prometheus | http://localhost:9090 | - |
| Grafana | http://localhost:3000 | admin / admin |

## 主要功能（对应真实看板）

| 能力 | 看板 | 关键真实指标 |
|------|------|-------------|
| 请求流量与延迟 | `grafana/dashboards/business_metrics.json`、`yunshu-full-monitoring.json` | `yunshu_http_request_total`（单数）、`yunshu_http_request_duration_seconds`、`yunshu:latency_p95:5m` |
| 安全拦截 | `business_metrics.json`、`yunshu-alerts-monitor.json` | `yunshu_security_blocks_total{level,rule,category}` |
| 意图/降级/熔断/限流 | `business_metrics.json` | `yunshu_intent_layer_ratio`、`yunshu_degrade_trigger_total`、`yunshu_circuit_breaker_state`、`yunshu_rate_limit_trigger_total` |
| 记忆 | `business_metrics.json` | `yunshu_memory_storage_total`、`yunshu_memory_search_hit_rate` |
| 技能质量 | `yunshu-skill-quality.json` | `yunshu_skill_hallucination_total`、`yunshu_skill_eval_score{quantile}` |
| Reranker | `reranker-dashboard.json` | `yunshu_rerank_duration_ms{quantile}`、`yunshu_reranker_fallback_total` |
| 技能检索延迟/QPS（HPA 源） | `hpa-skill-retrieval-dashboard.json` | `skill_match_latency_ms`、`skill_match_count_total` |

> 已删除的 4 个旧看板（`grafana_dashboards/yunshu_v2_dashboard.json`、`yunshu_chat_dashboard.json`、
> `yunshu_business_dashboard.json`、`yunshu_memory_dashboard.json`）100% 引用不存在的指标名，不要再用。
> 当前每个看板的数据现状（含"必然是空的面板"清单）见 `GRAFANA_SETUP_GUIDE.md` 第 4 节。

## 提供的 Prometheus 指标（本部署实测存在）

来源：`GET http://127.0.0.1:5678/metrics`（job `yunshu`）与 `GET /api/business/prometheus`（job `yunshu-business`）。
**写查询/告警前请先在这里或 `docs/closeout/监控清理_evidence_20261002/live_metric_names.txt` 里核对**。

| 指标名称 | 类型 | 说明 |
|---------|------|------|
| `yunshu_http_request_total` | Counter | HTTP 请求数（标签 `method/status/endpoint`，**单数**，复数名不存在） |
| `yunshu_http_request_duration_seconds` | Histogram | HTTP 耗时（**有** `_bucket`，可 `histogram_quantile`） |
| `yunshu_http_request_exceptions_total` | Counter | 请求异常数 |
| `yunshu_interaction_total` | Counter | 交互总次数 |
| `yunshu_interaction_duration_seconds` | 分位数样本 | 交互耗时 p50/p95/p99（`{quantile="0.95"}`，文本导出器**不输出** `_bucket`） |
| `yunshu_conversations_total` | Counter | 对话数（含 `status`） |
| `yunshu_message_type_total` | Counter | 消息类型分布 |
| `yunshu_tool_call_total` / `yunshu_tool_calls_total` | Counter | 工具调用（两个名字并存，看板用前者） |
| `yunshu_security_blocks_total` | Counter | 安全拦截（`level=critical/warning`、`rule`、`category`） |
| `yunshu_cpu_usage_percent` / `yunshu_memory_usage_percent` | Gauge | 进程 CPU / 内存使用率 |
| `yunshu_active_connections` | Gauge | 活跃连接数 |
| `yunshu_circuit_breaker_state` / `yunshu_rate_limit_trigger_total` | Gauge / Counter | 熔断状态 / 限流触发 |
| `yunshu_memory_storage_total` / `yunshu_memory_search_hit_rate` | Counter / Gauge | 记忆写入 / 检索命中率 |
| `yunshu_skill_hallucination_total` / `yunshu_skill_eval_score` | Counter / 分位数样本 | 技能幻觉次数 / 评估分 |
| `yunshu_rerank_duration_ms` / `yunshu_reranker_fallback_total` | 分位数样本 / Counter | Rerank 延迟 / 降级次数 |
| `skill_match_latency_ms` / `skill_match_count_total` | Histogram / Counter | 技能检索延迟 / QPS（HPA 数据源，标签 `layer/method/success`） |
| `llm_tokens_total` / `llm_cost_usd_total` | Counter | Token 用量 / LLM 成本 |
| `yunshu_user_logins_total` | Counter | 登录次数 |

**没有对应真实指标的**（看板里出现过但本部署不存在，别照抄）：`Yunshu_*` 全命名空间、
`yunshu_v2_module_*`、`Yunshu_memory_count`、`yunshu_memory_count`、`yunshu_alert_total`、
`yunshu_error_total`、`yunshu_deployment_*` / `yunshu_rollback_total` / `yunshu_ci_*`（CI/部署指标**无采集链路**）、
`http_requests_total`（无前缀复数）、`system_cpu_usage_percent`/`system_memory_usage_percent`/`security_blocks_total`（无前缀旧名）。

## 预聚合记录规则（recording rules）

定义在 `recording_rules.yml` / `health_recording_rules.yml`，形如 `yunshu:xxx:5m`（**带冒号**）：

| 记录规则 | 说明 |
|---------|------|
| `yunshu:requests_per_second:5m` | 每秒请求数 |
| `yunshu:error_rate:5m` | 错误率 |
| `yunshu:latency_p50:5m` / `yunshu:latency_p95:5m` / `yunshu:latency_p99:5m` | 响应时间分位（**单位：秒**） |
| `yunshu:uptime_percent:24h` | 24 小时可用率 |
| `yunshu:tool_call_success_rate:5m` | 工具调用成功率 |
| `yunshu:llm_calls_per_hour:1h` | 每小时 LLM 调用 |
| `yunshu:security_blocks:1h` | 每小时安全拦截（按 `level`/`rule`） |
| `yunshu:memory_usage_percent` / `yunshu_memory_mb_used` | 内存使用率 / 占用 MB |
| `yunshu:health:overall_score` 及 `yunshu:health:{stability,performance,quality,efficiency,availability,security}:*` | 健康度评估体系总分与各维度指标 |

## 手动操作

### 启动 Prometheus (不使用 Docker)

```bash
# 用【权威】配置启动（monitoring/prometheus/prometheus.yml 是未挂载的旧快照，job 名不对）
prometheus --config.file=./monitoring/prometheus.yml
```

> 裸跑时 `rule_files` 的相对路径按配置所在目录解析，`monitoring/` 下的规则文件能直接读到；
> 但 `prometheus.yml` 里的 `rules/lock_watchdog_alerts.yml` 等子目录规则需要 `monitoring/prometheus/rules`
> 与 `monitoring/rules` 的路径对应关系成立，裸跑前请先 `promtool check config` 校验。

### 启动 Grafana (不使用 Docker)

```bash
# 下载并安装 Grafana
# 然后启动
grafana-server
```

### 导入仪表盘

**正常情况下不需要手动导入**：`monitoring/grafana/dashboards/` 已被 compose 挂载到
`/etc/grafana/provisioning/dashboards`，Grafana 启动时自动加载其中 9 个看板，
改 JSON 最多 30 秒后自动生效。

需要手动导入的场景（例如把模板生成的看板拿到别的 Grafana 上）：

1. 打开 Grafana: http://localhost:3000
2. 左侧菜单 -> Dashboards -> Import
3. 上传 `monitoring/grafana/dashboards/` 下的 JSON（**这是唯一被自动加载的目录**）
   或 `monitoring/grafana_dashboards/` 下模板生成的 JSON（该目录**不会被自动加载**，必须手动导入）
4. 选择 Prometheus 数据源（uid `prometheus`）
5. 点击 Import

## 高级配置

### 修改数据保留时间

保留期是 **Prometheus 启动参数**，写在 `docker-compose.monitoring.yml` 的 `command` 里
（`prometheus.yml` 里的 `storage.tsdb` 段 Prometheus 不读，写了无效）：

```yaml
command:
  - '--config.file=/etc/prometheus/prometheus.yml'
  - '--storage.tsdb.path=/prometheus'
  - '--storage.tsdb.retention.time=30d'   # 新增：保留 30 天
  - '--web.enable-lifecycle'
```

改完 `docker compose -f docker-compose.monitoring.yml up -d`；回退 = 删掉新增行。

### 添加更多监控目标

编辑 **`monitoring/prometheus.yml`**（权威配置），并同步改 `docker-compose.monitoring.yml`
里对应的挂载/引用（新增规则文件必须同时出现在这两处，否则 Prometheus 启动即报 no such file）：

```yaml
scrape_configs:
  # 云枢应用指标（权威 job 名 = yunshu，规则文件里的 job="yunshu" 与它对齐）
  - job_name: 'yunshu'
    static_configs:
      - targets: ['host.docker.internal:5678']
    metrics_path: '/metrics'
    scrape_interval: 5s
  - job_name: 'your-other-app'
    static_configs:
      - targets: ['your-host:your-port']
```

> 改 job 名是**联动操作**：必须同时改 `prometheus.yml` 的 `job_name` 与所有规则文件里的
> `job="..."`，两者不一致 = `up{}` 序列为空 = 依赖 job 的告警永不触发。

### 配置告警通知

Grafana 支持多种通知渠道：
- Email
- Slack
- Webhook
- PagerDuty
- 等等

详见 `GRAFANA_SETUP_GUIDE.md`。

## 性能基准（按真实指标给阈值）

| 指标 | 正常范围 | 警告阈值 | 说明 |
|------|---------|---------|------|
| 交互耗时 p95 `yunshu_interaction_duration_seconds{quantile="0.95"}` | < 1s | > 3s | **单位是秒**（旧文档按毫秒写得出的 1000ms 阈值实际等于永不触发） |
| HTTP 95 分位 `histogram_quantile(0.95, sum(rate(yunshu_http_request_duration_seconds_bucket[5m])) by (le))` | < 300ms | > 1s | 只有 HTTP 层有真直方图 |
| 错误率 `yunshu:error_rate:5m` | < 1% | > 5% | recording rule |
| 安全拦截（Critical）`sum(yunshu_security_blocks_total{level="critical"})` | 0 | > 0 | 与告警规则同口径 |
| 技能检索 P99 `histogram_quantile(0.99, sum(rate(skill_match_latency_ms_bucket[5m])) by (le))` | < 32ms | > 40ms | 40ms 是 HPA 扩容触发点 |

## 故障排查

### Prometheus 无法连接

```bash
# 检查 Prometheus 是否运行
docker ps | grep prometheus

# 检查 Prometheus 日志（容器名以权威 compose 为准）
docker logs yunshu-prometheus

# 检查端口
netstat -an | grep 9090
```

### Grafana 仪表盘空白

1. 检查数据源是否配置正确
2. 确认 Prometheus 能抓取到数据
3. 检查时间范围设置

### 指标不显示

```bash
# 1. 云枢本体是否在暴露指标（端口 5678，不是 8000）
curl -s http://127.0.0.1:5678/metrics | head
curl -s http://127.0.0.1:5678/api/business/prometheus | head

# 2. Prometheus 是否抓到（两个 job 都应为 1）
curl 'http://localhost:9090/api/v1/query?query=up{job=~"yunshu.*"}'

# 3. 指标名是否真实存在——先核对再怀疑 Grafana
curl -s http://127.0.0.1:5678/metrics | grep '^# TYPE' | grep security_blocks
```

> 面板空的常见原因不是"服务挂了"，而是**查询里的指标名在本部署不存在**：
> 大写命名空间、无前缀旧名（`http_requests_total` 等）、job 名写成旧快照里的名字。
> 每个看板哪些面板必然为空，见 `GRAFANA_SETUP_GUIDE.md` 第 4 节。

## 相关文档

- [Grafana 使用指南](GRAFANA_SETUP_GUIDE.md) - 详细的配置和告警配置说明
- [Prometheus 官方文档](https://prometheus.io/docs/)
- [Grafana 官方文档](https://grafana.com/docs/)

## 许可证

本监控配置随云枢项目一起发布。

---

**最后更新**: 2026-10-02
**版本**: 2.0（指标表与看板路径按本部署实测校正）
