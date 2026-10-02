# Grafana 接入指南（本部署真实可用版）

> **本文替代 2026-05-31 版，整篇重写。**
> 旧版通篇围绕 `Yunshu_v2_dashboard.json` + `Yunshu_*` 指标 + `job="Yunshu-v2"`，这三样在本部署**都不成立**：
> 看板文件已删除、`Yunshu_*`（大写 Y 命名空间）的 exporter 在 `agent/monitoring/prometheus.py` 里被注释掉、
> 权威 job 名是 `yunshu`。照旧版配置的结果是一屏 No data，所以不再保留旧写法。
>
> 本文只写**现场实测成立**的接法与指标名。改动本文前请先读第一节的三条硬事实。

## 目录

1. [三条硬事实（先读）](#一三条硬事实先读)
2. [起栈与验证](#二起栈与验证)
3. [数据源与看板的自动加载（Provisioning）](#三数据源与看板的自动加载provisioning)
4. [9 个看板：各自看什么（含修复后的空面板清单）](#四9-个看板各自看什么含修复后的空面板清单)
5. [告警查询示例（可直接粘贴）](#五告警查询示例可直接粘贴)
6. [排障](#六排障)
7. [数据保留](#七数据保留)
8. [通知渠道](#八通知渠道)

---

## 一、三条硬事实（先读）

| # | 事实 | 为什么 / 依据 |
|---|------|--------------|
| 1 | 本部署只有 4 个 scrape job：`yunshu`（应用 `/metrics`，`host.docker.internal:5678`，5s）、`yunshu-business`（业务 `/api/business/prometheus`，30s）、`prometheus`、`grafana` | `monitoring/prometheus.yml` 是 `docker-compose.monitoring.yml` **唯一**挂载到 `/etc/prometheus/prometheus.yml` 的配置。`monitoring/prometheus/prometheus.yml`（`Yunshu-v2`）与 `deploy/` 下那份（`yunshu-app`）只是**未被挂载的快照** ⇒ 任何 `job="Yunshu-v2"` / `job="yunshu-app"` 的查询都返回空序列 |
| 2 | `Yunshu_*`（大写 Y）指标**永远没有数据** | PromQL 指标名大小写敏感。那套 exporter 是 `PrometheusMetricsExporter(namespace="Yunshu")`，其构造代码已被注释、从未实例化 ⇒ 指标从未注册 |
| 3 | 只有 `monitoring/grafana/dashboards/` 会被 Grafana 自动加载 | compose 把 `./monitoring/grafana/dashboards` 挂到 `/etc/grafana/provisioning/dashboards`。旧目录 `monitoring/grafana_dashboards/` **没有挂载** ⇒ 放在那里的 JSON 不会被加载（见该目录 README 说明） |

**写查询前先自查指标名是否真实存在**，判定依据只有三处：

1. `GET http://127.0.0.1:5678/metrics`（`job="yunshu"` 的源）
2. `GET http://127.0.0.1:5678/api/business/prometheus`（`job="yunshu-business"` 的源）
   —— 上述两处的实测合并清单共 186 个名字，保存在 `docs/closeout/监控清理_evidence_20261002/live_metric_names.txt`；
3. recording rule：`monitoring/recording_rules.yml`、`monitoring/health_recording_rules.yml`，形如 `yunshu:error_rate:5m`（**带冒号**，是预聚合序列，不是 exporter 指标）。

三处都搜不到 = **本部署无对应指标**，不要凭"应该有"写上去。

**两个容易踩的坑**（都来自实测）：

- 业务端点是**文本导出器**，直方图只输出分位数样本、**不输出 `_bucket`** ⇒ 交互/工具/任务耗时只能用
  `yunshu_interaction_duration_seconds{quantile="0.95"}`，写 `histogram_quantile(..._bucket)` 一定空。
  例外：`yunshu_http_request_duration_seconds_bucket` 由 Flask exporter 提供，**有** `_bucket`。
- HTTP 请求计数是**单数** `yunshu_http_request_total`（标签 `method/status/endpoint`），
  复数 `yunshu_http_requests_total` 不存在（这是历史上告警"静默失效"的根因之一）。

---

## 二、起栈与验证

```bash
# 在【仓库根目录】执行，不要在 monitoring/ 下执行
docker compose -f docker-compose.monitoring.yml up -d
docker compose -f docker-compose.monitoring.yml ps
```

访问：Prometheus <http://localhost:9090> ；Grafana <http://localhost:3000>（默认 admin/admin，
可用环境变量 `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` 覆盖）。

**为什么要显式带 `-f docker-compose.monitoring.yml`**：`monitoring/docker-compose.yml` 是旧快照，
它挂的是 `./monitoring/prometheus`、`./monitoring/grafana_dashboards`——相对该文件所在目录解析后指向
`monitoring/monitoring/...`（**不存在**），且加载的是 `job_name: Yunshu-v2` 的旧 scrape 配置。
`monitoring/start_monitoring.ps1|sh` 里执行的是不带 `-f` 的 `docker-compose up -d`，在 monitoring 目录下
同样会命中这份旧快照 ⇒ **不要用它启动监控栈**。
（`monitoring/docker-compose.yml` 文件头 2026-10-02 已加上「失效的历史快照，请勿用它起栈」警告并写明回退方式，与本段描述一致。）

验证采集是否正常：

```bash
# 方式一：列出全部采集目标及其 job 名（应为 yunshu / yunshu-business / prometheus / grafana）
curl -s http://localhost:9090/api/v1/targets | grep -o '"job":"[^"]*"' | sort -u

# 方式二：查 up 值，返回 4 条序列，value 均为 "1" 才算正常
#   （grafana job 抓 grafana:3000，需与 Grafana 同网络）
curl -s 'http://localhost:9090/api/v1/query?query=up'
```

或在 Prometheus UI → Status → Targets 查看。

回退：`docker compose -f docker-compose.monitoring.yml down`。
**不要随手加 `-v`**：会一并删除 `prometheus_data` / `grafana_data` 两个卷，历史指标与 Grafana 内建状态一起丢。

---

## 三、数据源与看板的自动加载（Provisioning）

| 宿主机文件 | 容器内路径 | 作用 |
|-----------|-----------|------|
| `monitoring/grafana/datasources/prometheus.yml` | `/etc/grafana/provisioning/datasources` | 自动建数据源 Prometheus |
| `monitoring/grafana/dashboards/dashboard.yml` | `/etc/grafana/provisioning/dashboards` | 看板 provider：扫描同目录下所有 `*.json` |
| `monitoring/grafana/alerting/` | `/etc/grafana/provisioning/alerting` | Grafana 统一告警规则（`*.yml`） |

数据源关键字段（已固定，别改）：

- **Name**：`Prometheus`；**uid**：`prometheus`；**URL**：`http://prometheus:9090`；`isDefault: true`；`httpMethod: POST`。
- **为什么 URL 是容器名**：Grafana 与 Prometheus 同在 `yunshu-monitoring` 网络里，写 `localhost:9090`
  会指向 Grafana 容器自己。
- **为什么 uid 必须写死**：所有看板 JSON 的 `datasource.uid` 和 Grafana 告警规则的 `datasourceUid`
  都引用 `prometheus`；uid 一旦变成 Grafana 随机生成的值，面板与告警会整片断链。

看板 provider（`dashboard.yml`）：`updateIntervalSeconds: 30`、`allowUiUpdates: true`、
`path: /etc/grafana/provisioning/dashboards` 且 `folder: ''`（看板直接落在根目录，没有 `Yunshu` 文件夹）。

- **改看板的正确姿势**：改 `monitoring/grafana/dashboards/*.json`，最多 30 秒后自动生效，无需重启 Grafana。
- **为什么不能在 UI 里长期改**：provisioning 的看板在下一次扫描时会被 JSON 覆盖；UI 改动只适合临时试查询。
- **回退**：把 JSON 改回上一版即可（建议先复制一份 `*.json.bak`）。

---

## 四、9 个看板：各自看什么（含修复后的空面板清单）

`monitoring/grafana/dashboards/` 下共 9 个看板，全部会被自动加载。**先看结论**：

| 看板 | uid | 覆盖范围 | 数据现状（2026-10-02 修复批次后） |
|------|-----|---------|------------------------------|
| `business_metrics.json`（业务指标监控） | `business-metrics` | 请求速率/熔断/限流/交互/工具/任务/记忆/降级/意图分层 | ✅ 11 个面板，指标全部真实（1 个 Loki 面板除外） |
| `yunshu-skill-quality.json`（技能质量与幻觉） | `yunshu-skill-quality` | 幻觉次数/幻觉率/低分技能 Top-N/评估分分布 | ✅ 全部真实 |
| `reranker-dashboard.json`（v6.5 ONNX Reranker） | `reranker-onnx-v65` | Rerank P99/降级率/加载失败/QPS 按后端 | ✅ 全部真实 |
| `yunshu-monitor.json`（Yunshu Monitor） | `yunshu-monitor` | 请求速率/CPU/内存/安全拦截/95 分位 | ✅ **6/6 出数**（原 5 类无前缀旧名已全部改名，**不再是全空看板**） |
| `yunshu-alerts-monitor.json`（告警规则监视） | `yunshu-alerts` | 活跃告警数、错误率、95 分位、安全拦截、资源 | ✅ 10/11 出数；错误率面板已修好，但要等出现 5xx 才有数 |
| `yunshu-full-monitoring.json`（云枢全链路监控仪表盘） | `yunshu-full-monitoring` | 健康概览、流量、错误告警、部署回滚、CI/CD、业务 | ⚠️ 26 个内容面板 = 13 出数 + 2 待 5xx + 10 保留但无数据源 + 1 无对应指标 |
| `hpa-skill-retrieval-dashboard.json`（技能检索 HPA 5000 量级） | `hpa-skill-retrieval-5k` | 技能检索 P50/95/99、QPS、副本数、CPU | ⚠️ 4/6 出数；副本数、CPU 面板无 kube/cAdvisor 采集源 |
| `p99-latency-tracker.json`（P99 延迟追踪 SLO） | `p99-latency-tracker` | 技能检索 SLO、百分位、慢副本定位 | ✅ 9/11 出数；空的是 2 个 k8s 面板（`kube_deployment_status_replicas` / `container_cpu_usage_seconds_total`，本部署无采集方，见板级说明） |
| `skill-hpa-monitor.json`（技能检索 HPA 扩容监控） | `skill-hpa-monitor` | 同上 + k6 压测指标 | ⚠️ 4/8 出数；副本数、CPU、k6、扩缩容事件 4 个面板无采集源 |

> **修复批次（2026-10-02）的口径**：能靠"改查询"救活的面板全部改成了真实指标；
> 救不活的**保留但加上面板说明**；确实没有指标的**如实标注**。所以现在看到空面板，
> 先按 4.2 的三类对号入座，再考虑是不是故障。

### 4.1 可以直接信的看板

**业务指标监控（`business-metrics`）** —— 唯一一个指标名 100% 与现场一致的综合性看板：

| 面板 | 查询用的真实指标 |
|------|----------------|
| HTTP 请求速率（5m） | `sum(rate(yunshu_http_request_total[5m])) by (status)` |
| 熔断器状态 | `yunshu_circuit_breaker_state` |
| 限流触发速率 | `yunshu_rate_limit_trigger_total` |
| 交互次数 | `sum(yunshu_interaction_total)` |
| 工具调用次数（按工具） | `sum(yunshu_tool_call_total) by (tool_name)` |
| 任务完成率 / 记忆命中率 | `yunshu_task_completion_rate` / `yunshu_memory_search_hit_rate` |
| 降级触发次数（按模块） | `sum(yunshu_degrade_trigger_total) by (module)` |
| 意图识别各层占比 | `yunshu_intent_layer_ratio`、`yunshu_intent_layer_total{layer=...}` |

> 唯一例外：最后一个"semantic 埋点分母同步"面板走 **Loki** 数据源，本部署未挂 Loki ⇒ 该面板为空，
> 与指标名无关。

**技能质量与幻觉（`yunshu-skill-quality`）**：`yunshu_skill_hallucination_total`、
`yunshu_skill_eval_score{quantile="0.5|0.95|0.99"}`（业务端点输出的是分位数样本，看板写法正确）。

**Reranker 大盘（`reranker-onnx-v65`）**：`yunshu_rerank_duration_ms{quantile="0.99"}`、
`yunshu_reranker_load_total{status}`、`yunshu_reranker_fallback_total{reason}`、
`yunshu_reranker_completed_total{backend}`、`yunshu_reranker_predict_failed_total`、
`yunshu_reranker_load_time_seconds`，以及 `ALERTS{alertname=~"Reranker.*"}`。

### 4.2 修复后的现状：空面板只有三类

2026-10-02 的修复批次已经把 9 个看板里"改查询就能救活"的面板全部改成真实指标
（含批次末尾补修的 `p99-latency-tracker.json` 两个漏改面板）。
**按设计**还会空的面板只有下面三类（②③④），**都不是故障**：

| 类别 | 数量 | 特征 | 怎么办 |
|------|------|------|--------|
| ① 已修复 | 9 个看板的主体面板 | 查询已是真实指标且本部署有数据 | 直接用；若显示 No data 才是故障（按第六节排查） |
| ② 保留但无数据源 | 10 个面板（部署 ×5 + CI/CD ×5） | 面板上已带说明「本部署无数据源，不是故障」 | 改查询没用，要先补 pushgateway/CI 采集链路 |
| ③ 本部署无对应指标 | 1 个面板（活跃用户数） | 标题已带「（本部署无指标）」 | 不要找指标顶替；确有需求要先加埋点 |
| ④ 查询已修好但要等 5xx | 3 个面板（错误率 ×2 + 错误分布 ×1） | 本部署没有 5xx 序列 ⇒ 分子为空 | 出现 5xx 即出数；别把"空"读成"零错误" |
| ⑤ 原"未修完"已补齐 | 2 个面板（p99 看板的 SLO 合规率 / HPA 阈值超标率） | 原带 `namespace="$namespace"`（该指标没有这个标签） | ✅ 同批末尾已删除该过滤，现与同看板其它面板一致 |

#### ① 已修复：原写法（已废弃） → 现在的实际查询

| 看板 / 面板 | 原写法（已废弃） | 现在的实际查询 | 状态 |
|------------|----------------|---------------|------|
| 全链路：服务状态 | `up{job="yunshu-app"}` | `up{job="yunshu"}` | ✅ 已于 2026-10-02 修复 |
| 全链路：CPU / 内存使用率 | `system_cpu_usage_percent` / `system_memory_usage_percent` | `yunshu_cpu_usage_percent` / `yunshu_memory_usage_percent` | ✅ 已于 2026-10-02 修复 |
| 全链路：请求速率趋势 | `sum(rate(http_requests_total[5m])) by (endpoint)` | `sum(rate(yunshu_http_request_total[5m])) by (method, status)` | ✅ 已修复（`endpoint` 不是该指标的标签，实测只有 `method`/`status`，故按它们分组） |
| 全链路：安全拦截总数 | `sum(security_blocks_total)` | `sum(yunshu_security_blocks_total)` | ✅ 已于 2026-10-02 修复 |
| 全链路：告警统计（每小时） | `sum(increase(Yunshu_alert_total[1h])) by (severity)` | `sum(increase(yunshu_security_blocks_total[1h])) by (level)` | ✅ 已修复（真实标签是 `level`，不是 `severity`） |
| 全链路：记忆总数 | `Yunshu_memory_count` | `sum(yunshu_memory_storage_total)` | ✅ 已修复（面板标题未改，读作"记忆存储量"） |
| 全链路：Token 使用量（每小时） | `yunshu:token_usage:1h` | `sum(increase(llm_tokens_total[1h]))` | ✅ 已于 2026-10-02 修复 |
| 全链路：每小时成本 | `yunshu:cost_per_hour` | `sum(increase(llm_cost_usd_total[1h]))` | ✅ 已于 2026-10-02 修复 |
| 告警监视：95 分位延迟 | `http_request_duration_seconds_bucket` | `histogram_quantile(0.95, rate(yunshu_http_request_duration_seconds_bucket[5m]))` | ✅ 已于 2026-10-02 修复 |
| 告警监视：安全拦截速率/分钟 | `security_blocks_total` | `sum(rate(yunshu_security_blocks_total[5m])) * 60` | ✅ 已于 2026-10-02 修复 |
| 告警监视：CPU / 内存使用率 | `system_cpu_usage_percent` / `system_memory_usage_percent` | `yunshu_cpu_usage_percent` / `yunshu_memory_usage_percent` | ✅ 已于 2026-10-02 修复 |
| 告警监视：对话异常率 | `Yunshu_conversations_total` | `sum(rate(yunshu_conversations_total{status="exception"}[5m])) / sum(rate(yunshu_conversations_total[5m]))` | ✅ 已于 2026-10-02 修复 |
| `yunshu-monitor`：全部 6 个面板 | `http_requests_total`、`system_cpu_usage_percent`、`system_memory_usage_percent`、`security_blocks_total`、`http_request_duration_seconds_bucket` | `rate(yunshu_http_request_total[5m])`、`yunshu_cpu_usage_percent`、`yunshu_memory_usage_percent`、`sum(yunshu_security_blocks_total)`、`histogram_quantile(0.95, rate(yunshu_http_request_duration_seconds_bucket[5m]))` | ✅ 已于 2026-10-02 修复，**6/6 出数** |
| 三个 HPA 看板：技能检索延迟/QPS 类面板 | `skill_match_latency_ms_bucket{namespace="$namespace"}`、`skill_match_count_total{namespace="$namespace"}` | 同名查询**去掉 `namespace="$namespace"` 过滤** | ✅ 已于 2026-10-02 修复（该指标标签只有 `layer/method/success`） |

全链路看板里仍然依赖 recording rule 的面板同样可用：`yunshu:uptime_percent:24h`、
`yunshu:latency_p50:5m` / `yunshu:latency_p95:5m` / `yunshu:latency_p99:5m`、
`yunshu:tool_call_success_rate:5m`。

#### ② 保留但无数据源：部署 ×5 + CI/CD ×5（共 10 个面板）

这 10 个面板的查询**没有被改**——`Yunshu_deployment_*` / `Yunshu_rollback_total` / `Yunshu_ci_*`
由 pushgateway / CI 推送链路产生，本部署没有该链路，改查询也救不活。
修复批次的做法是**给每个面板加 `description`**（"【本部署无数据源，不是故障】…见
`docs/CICD_METRICS_DEPLOYMENT_CHECKLIST.md`"），两个行标题也改成「（⚠ 本部署未接入数据源）」
⇒ 打开看板就能看到原因，不必再翻文档。

| 面板 | 现查询（保留） | 状态 |
|------|---------------|------|
| 部署状态 / 24 小时回滚次数 / 部署耗时 / 24 小时部署失败次数 / 部署历史趋势 | `Yunshu_deployment_status`、`sum(increase(Yunshu_rollback_total[24h]))`、`Yunshu_deployment_duration_seconds`、`sum(increase(Yunshu_deployment_failures_total[24h]))`、`sum(increase(Yunshu_deployment_total[1h])) by (status)` | ⚠️ 保留、恒空（面板说明已加） |
| CI 流水线耗时 / 测试覆盖率 / 24 小时测试失败 / 24 小时构建失败 / CI/CD 各阶段趋势 | `Yunshu_ci_pipeline_duration_seconds`、`Yunshu_ci_test_coverage_percent`、`sum(increase(Yunshu_ci_test_failures_total[24h]))`、`sum(increase(Yunshu_ci_build_failures_total[24h]))`、`sum(rate(Yunshu_ci_pipeline_runs_total[1h])) by (stage)` | ⚠️ 保留、恒空（面板说明已加） |

恢复前提见 `docs/CICD_METRICS_DEPLOYMENT_CHECKLIST.md`（补采集链路，**不是**改 Grafana）。

#### ③ 本部署无对应指标：活跃用户数（1 个面板）

全链路看板的「活跃用户数（本部署无指标）」面板：查询仍是已作废的 `yunshu:active_users:5m`——
该 recording rule **已从 `monitoring/recording_rules.yml` 删除**（见该文件末尾"已删除的录制规则"，同为
无替代指标的还有 `yunshu:cost_per_hour`），本部署没有任何"活跃用户数"时间序列。
修复批次的处理是**保留面板 + 标题加「（本部署无指标）」+ 加 description 说明**，而不是随便找个指标顶替。

> 仓库里确有 `yunshu_user_logins_total`（登录计数），但它**不是活跃用户数**，不要拿它顶替这个面板。

#### ④ 查询已修好、但要等 5xx 才会出数（3 个面板）

`yunshu_http_request_total` 实测只有 200/401/404 等状态码，**没有 5xx 序列**；PromQL 里
"空 ÷ 有值 = 空"，所以这些面板会显示 No data 而不是 0：

| 面板 | 现在的实际查询 | 状态 |
|------|---------------|------|
| 全链路：错误率 | `yunshu:error_rate:5m * 100`（该 recording rule 的分子就是 `status=~"5.."`） | ✅ 已修复；出现 5xx 前不出数 |
| 全链路：错误分布趋势 | `sum(increase(yunshu_http_request_total{status=~"5.."}[5m])) by (status)` | ✅ 已修复；同上 |
| 告警监视：错误率 | `sum(rate(yunshu_http_request_total{status=~"5.."}[5m])) / sum(rate(yunshu_http_request_total[5m]))` | ✅ 已修复；同上 |

> **别把"错误率为空"当成"服务很健康"的证据**——它只是"还没有 5xx"。要看真实流量健康度，
> 用 `business-metrics` 看板按 `status` 分组的请求速率（含 401/404 占比）。

#### ⑤ 技能检索三看板：还空的面板都是 k8s 侧

- **告警规则监视（`yunshu-alerts`）**：4 个 `ALERTS{alertstate="firing",severity=...}` 面板与
  `up{job="yunshu"}` 面板正常（依赖 Prometheus 侧已加载的告警规则）。
- **`hpa-skill-retrieval-5k`（4/6 出数）**：有数据的是技能检索 P99 延迟、QPS、会话异常率、
  检索延迟分布；空的是「HPA 副本数变化」与「CPU 利用率」——需要 kube-state-metrics / cAdvisor，
  本部署没有采集源（QPS 面板里"按副本数求均值"那条查询同样因缺 `kube_deployment_status_replicas` 而无值）。
- **`skill-hpa-monitor`（4/8 出数）**：有数据的是 P99 趋势、QPS、活跃连接数
  （`yunshu_active_connections`）、检索错误率；空的是 HPA 副本数、CPU 使用率、k6 压测指标
  （无 k6 remote write）、HPA 扩缩容决策事件。
- 本部署技能检索是**进程内**能力，没有 K8s 副本与 HPA ⇒ 这几个面板改查询也没有意义。
- 这三个看板都加了**板级 `description`**：开板即说明"面向 k8s 部署，本部署下 kube_*/cAdvisor/k6
  面板必然为空，不是故障"。

### 4.3 已修复的看板（2026-10-02）

逐条写清这一批**实际改了什么**，方便回归时对账：

- **`yunshu-monitor.json` —— 不再是全空看板**：6 个面板涉及的 5 类无前缀旧名
  （`http_requests_total`、`system_cpu_usage_percent`、`system_memory_usage_percent`、
  `security_blocks_total`、`http_request_duration_seconds_bucket`）全部改成真实名 ⇒ 现在 **6/6 出数**。
- **`yunshu-full-monitoring.json`**：`up{job="yunshu-app"}` → `up{job="yunshu"}`；CPU/内存改 `yunshu_*`；
  请求速率趋势改 `sum(rate(yunshu_http_request_total[5m])) by (method, status)`；
  错误分布改 `sum(increase(yunshu_http_request_total{status=~"5.."}[5m])) by (status)`；
  告警统计改 `sum(increase(yunshu_security_blocks_total[1h])) by (level)`；
  记忆总数改 `sum(yunshu_memory_storage_total)`；安全拦截改 `sum(yunshu_security_blocks_total)`；
  Token / 成本改 `sum(increase(llm_tokens_total[1h]))` / `sum(increase(llm_cost_usd_total[1h]))`。
  部署 ×5 与 CI/CD ×5 面板**保留**并逐面板加 `description`；两个行标题标注「（⚠ 本部署未接入数据源）」；
  「活跃用户数」标题标注「（本部署无指标）」并加说明。
  结算：26 个内容面板 = **13 出数 + 2 待 5xx + 10 无数据源 + 1 无指标**。
- **`yunshu-alerts-monitor.json`**：6 处旧名（`Yunshu_conversations_total`、`http_requests_total`
  （含 `{status=~"5.."}` 那处）、`http_request_duration_seconds_bucket`、`security_blocks_total`、
  `system_cpu_usage_percent` / `system_memory_usage_percent`）全部改为真实名 ⇒ **10/11 出数**
  （差的 1 个是待 5xx 的错误率面板）。
- **`p99-latency-tracker.json` / `skill-hpa-monitor.json` / `hpa-skill-retrieval-dashboard.json`**：
  `skill_match_latency_ms*` 与 `skill_match_count_total` 上的 `namespace="$namespace"` 过滤已删除
  （该指标标签只有 `layer/method/success`），三个看板各加了**板级 `description`**，开板即说明
  "面向 k8s 部署，本部署下 kube_*/cAdvisor/k6 面板必然为空，不是故障"。
  ⇒ p99 看板 9/11、skill-hpa-monitor 4/8、hpa-skill-retrieval-5k 4/6 出数。
  > **补记（同批末尾补修）**：`p99-latency-tracker.json` 的「P99 SLO 合规率」与「HPA 阈值超标率」
  > 两个面板起初漏改（仍带 `namespace="$namespace"`）—— 该指标标签只有 `layer/method/success`。
  > 现已删除该过滤；该板剩下的 2 个空面板是 `kube_*` / `container_*`，属"本部署没有采集方"，
  > 由板级 `description` 覆盖。
- **旧目录 `monitoring/grafana_dashboards/`（不被 compose 加载）**：`yunshu_resource_release_dashboard.json`
  有板级 `description`，但它整板依赖的 `yunshu_resource_usage` 不在实测指标清单里
  （ResourceMonitor 无生产调用方）⇒ 恒空；该目录的看板都必须手动 Import，详见其 README。

---

## 五、告警查询示例（可直接粘贴）

以下表达式全部只用真实指标；`up` 与 `ALERTS` 是 Prometheus 内置序列（不是 exporter 指标）。

| 场景 | 查询 | 建议 For | 为什么这么写 |
|------|------|---------|-------------|
| 危险操作被拦截 | `sum(yunshu_security_blocks_total{level="critical"}) > 0` | 0s（立即） | `level` 标签实测存在（`critical`/`warning`），与 `monitoring/prometheus/alert_rules.yml` 的规则同源 |
| 交互耗时劣化 | `max(yunshu_interaction_duration_seconds{quantile="0.95"}) > 3` | 2m | 业务端点只输出分位数样本，**单位是秒**；旧文档写 `> 1000` 是拿毫秒当秒用 |
| HTTP 95 分位劣化 | `histogram_quantile(0.95, sum(rate(yunshu_http_request_duration_seconds_bucket[5m])) by (le)) > 1` | 5m | Flask exporter 提供真直方图，`_bucket` 确实存在（与业务端点不同） |
| 应用抓取失败 / 服务下线 | `up{job="yunshu"} == 0` | 1m | job 名必须用权威名 `yunshu` |
| 业务指标抓取失败 | `up{job="yunshu-business"} == 0` | 5m | 业务指标走独立 job，挂了不影响 `job="yunshu"` |
| Grafana 自身下线 | `up{job="grafana"} == 0` | 5m | 该 job 已存在（曾经缺失 ⇒ 这条规则长期是"死规则"） |
| 技能检索 SLO 破线 | `histogram_quantile(0.99, sum(rate(skill_match_latency_ms_bucket[5m])) by (le)) > 40` | 5m | HPA 阈值 40ms；buckets 已按 40ms 对齐 |
| 告警自身是否在响 | `count(ALERTS{alertstate="firing",severity="critical"})` | 0s | 用于"看门狗看告警" |
| 高错误率（可选） | `sum(rate(yunshu_http_request_total{status=~"4..|5.."}[5m])) / sum(rate(yunshu_http_request_total[5m])) > 0.05` | 2m | `yunshu:error_rate:5m` 只算 5xx，而本部署没有 5xx 序列 ⇒ 该 recording rule 目前无输出（4.2 第 ④ 类）；想真正监控就把 4xx 一起算进去 |

**写规则时注意两点**：

1. **不要用 `or on() vector(0)` 兜底**：指标名写错时它会返回 0，把"指标不存在"伪装成"指标正常"，历史上因此潜伏数周。
2. 5xx 率：`yunshu_http_request_total` 实测只出现过 200/401/404 等状态码，**没有 5xx 序列** ⇒
   `status=~"5.."` 分子为空，"空 ÷ 有值 = 空"，因此 `yunshu:error_rate:5m` 这类查询会返回 **No data
   而不是 0**——别把"空"当成"服务很健康"的证据（详见 `docs/perf/可观测性实测.md` 与本文 4.2 第 ④ 类）。
   如果确实要一条"错误率"规则，建议直接对真实状态码写，例如
   `sum(rate(yunshu_http_request_total{status=~"4..|5.."}[5m])) / sum(rate(yunshu_http_request_total[5m])) > 0.05`。

---

## 六、排障

| 症状 | 先查什么 | 常见根因 |
|------|---------|---------|
| 数据源 "Failed to connect" | `curl http://localhost:9090/-/healthy`；容器内 `curl http://prometheus:9090/-/healthy` | URL 写成 `localhost:9090`（Grafana 容器内指自己）；或不在同一 Docker 网络 |
| 整屏 No data | Prometheus → Status → Targets，看 `up{job="yunshu"}` | 云枢没起（`python app_server.py` 未跑）或 `host.docker.internal` 解析不到宿主 |
| 单个面板 No data | 把面板查询粘到 Prometheus 的 Graph 页面执行 | 指标名不存在（对照第 0 节自查三处：大写命名空间、无前缀旧名、job 名写成旧快照里的名字、带 `namespace` 过滤） |
| 看板加载不出来 | `docker logs yunshu-grafana \| grep -i provision` | JSON 语法错（Grafana 会跳过整个文件）、或 JSON 放在**未挂载**的 `monitoring/grafana_dashboards/` |
| 改了 JSON 不生效 | 等 30s（provider `updateIntervalSeconds`），或看是否在 UI 里改的 | UI 改动会被 provisioning 覆盖 |
| Grafana 告警不触发 | Alerting → Alert rules 的状态；Prometheus 侧 Alert 页 | 规则查询为空（见上）；通知渠道未配/未关联 |

---

## 七、数据保留

Prometheus 的保留期在 compose 的启动参数里（`docker-compose.monitoring.yml`，权威文件）：

```yaml
command:
  - '--config.file=/etc/prometheus/prometheus.yml'
  - '--storage.tsdb.path=/prometheus'
  - '--web.enable-lifecycle'
```

要改保留期，在 `command` 里追加一行再 `up -d`（**别照抄 `prometheus.yml` 里的 `storage.tsdb`**——
Prometheus 不读配置文件里的这段，它只认命令行参数）：

```yaml
  - '--storage.tsdb.retention.time=30d'
  - '--storage.tsdb.retention.size=10GB'
```

回退：删掉新增那两行，重新 `up -d`（已有数据不会被删，超期数据在下次 compaction 时清理）。

注意 `monitoring/docker-compose.yml`（旧快照）里虽然有 `retention.time=30d`，但那份文件整体不可用（见第二节）。

---

## 八、通知渠道

| 渠道 | 配置位置 | 要点 |
|------|---------|------|
| Email | `docker-compose.monitoring.yml` 的 grafana `environment` 追加 `GF_SMTP_*`，或 Grafana UI → Alerting → Contact points | 详见 `GRAFANA_EMAIL_ALERT_GUIDE.md` |
| Webhook | Contact points → Webhook | `{"url": "http://your-endpoint/alerts", "httpMethod": "POST"}` |
| Slack | Contact points → Slack | 填 Incoming Webhook URL |
| 企业微信 / 钉钉 | Contact points → Webhook | 用群机器人 Webhook URL 代替 |

回退：删掉对应 Contact point 与规则上的关联即可；`GF_SMTP_*` 环境变量删掉后重启 Grafana。

---

## 相关文档

- [Grafana 快速配置](GRAFANA_QUICK_SETUP.md) —— 最短路径起栈 + 面板速查
- [Email 告警配置](GRAFANA_EMAIL_ALERT_GUIDE.md) —— SMTP 与告警规则
- [监控目录说明](README.md)
- `monitoring/grafana_dashboards/README.md` —— 模板生成看板（**不会被自动加载**）
- [Prometheus 官方文档](https://prometheus.io/docs/introduction/overview/) ·
  [Grafana 官方文档](https://grafana.com/docs/grafana/latest/) ·
  [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)

---

**文档版本**: 2.1（整篇重写 + 2026-10-02 对齐看板 JSON 修复：第 4 节改为「原写法（已废弃）→ 现在的实际查询 → 状态」三列，4.3 记录已修复看板（含末尾补修的 2 个面板））
**最后更新**: 2026-10-02
**维护者**: 云枢开发团队
