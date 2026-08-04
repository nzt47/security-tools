# v6.5 Grafana 面板快照 — 配置变更后状态确认

> **变更编号**: CHG-2026-0729-PROM-RULES
> **快照时间**: 2026-07-29 00:48 CST
> **Dashboard**: v6.5 ONNX Reranker 监控大盘 (`uid: reranker-onnx-v65`)
> **Grafana 版本**: 13.0.2
> **Prometheus 版本**: prom/prometheus:latest

---

## 1. 变更概述

本次快照验证 `prometheus.yml` 配置变更（补全 `rule_files` 引用）后，监控指标是否已更新为最新状态。

| 变更项 | 变更前 | 变更后 |
|--------|--------|--------|
| `rule_files` 引用数 | 1（`alerts.yml`）| 3（+`rules/reranker-alerts.yml` +`rules/yunshu-v6-query-pattern-alerts.yml`）|
| 加载规则数 | 16 | 38 |
| 告警分组数 | 8 | 15 |
| docker-compose volumes | 2 个文件挂载 | +`rules` 目录挂载 |

---

## 2. 配置变更确认

### 2.1 prometheus.yml 最终状态

```yaml
rule_files:
  - "alerts.yml"
  # [CHG-2026-0729] 补全 reranker + query-pattern 规则引用（22 条规则）
  - "rules/reranker-alerts.yml"
  - "rules/yunshu-v6-query-pattern-alerts.yml"
```

### 2.2 docker-compose.monitoring.yml 变更

```yaml
volumes:
  - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
  - ./monitoring/alerts.yml:/etc/prometheus/alerts.yml
  # [CHG-2026-0729] 挂载 rules 目录，使 reranker-alerts.yml 等子目录规则生效
  - ./monitoring/prometheus/rules:/etc/prometheus/rules:ro
  - prometheus_data:/prometheus
```

### 2.3 变更差异（git diff vs 备份）

```
+  # [CHG-2026-0729] 补全 reranker + query-pattern 规则引用（22 条规则）
+  - "rules/reranker-alerts.yml"
+  - "rules/yunshu-v6-query-pattern-alerts.yml"
```

---

## 3. Prometheus 规则加载验证

**查询方式**: `GET http://localhost:9090/api/v1/rules`

### 3.1 规则加载总览

| 指标 | 值 | 状态 |
|------|-----|------|
| 总规则数 | 38 | ✅ |
| 规则分组数 | 15 | ✅ |
| Prometheus 容器状态 | Up | ✅ |
| 配置加载日志 | "Completed loading of configuration file" | ✅ |

### 3.2 规则分组明细（15 groups）

| 分组 | 规则数 | 来源 | 变更状态 |
|------|--------|------|---------|
| yunshu_business | 2 | alerts.yml | 已有 |
| yunshu_http_errors | 2 | alerts.yml | 已有 |
| yunshu_latency | 2 | alerts.yml | 已有 |
| yunshu_monitoring | 2 | alerts.yml | 已有 |
| yunshu_resources | 2 | alerts.yml | 已有 |
| yunshu_security | 1 | alerts.yml | 已有 |
| yunshu_service_health | 2 | alerts.yml | 已有 |
| yunshu_skill_quality | 3 | alerts.yml | 已有 |
| **yunshu_reranker_p0** | **2** | **reranker-alerts.yml** | **🆕 新增** |
| **yunshu_reranker_p1** | **4** | **reranker-alerts.yml** | **🆕 新增** |
| **yunshu_reranker_p2** | **2** | **reranker-alerts.yml** | **🆕 新增** |
| **yunshu_v62_negative_intent_alerts** | **5** | **query-pattern-alerts.yml** | **🆕 新增** |
| **yunshu_v6_query_pattern_p0** | **3** | **query-pattern-alerts.yml** | **🆕 新增** |
| **yunshu_v6_query_pattern_p1** | **3** | **query-pattern-alerts.yml** | **🆕 新增** |
| **yunshu_v6_query_pattern_p2** | **3** | **query-pattern-alerts.yml** | **🆕 新增** |

**新增规则**: 22 条（8 reranker + 14 query-pattern）✅

### 3.3 P0 告警规则确认（ONNX 监控核心）

| 告警名 | 触发条件 | 状态 |
|--------|---------|------|
| RerankerOnnxLoadFailed | ONNX 加载失败 5min 内 > 0 | ✅ 已加载 |
| RerankerAllBackendsDown | ONNX + PyTorch 双后端全挂 | ✅ 已加载 |

---

## 4. Grafana 面板截图

**Dashboard URL**: `http://localhost:3000/d/reranker-onnx-v65/`
**面板数**: 10

### 截图清单

| 截图文件 | 说明 |
|----------|------|
| [v65-onnx-reranker-top.png](./snapshots/v65-onnx-reranker-top.png) | 面板顶部视图（P99 延迟、加载失败、降级率）|
| [v65-onnx-reranker-fullpage.png](./snapshots/v65-onnx-reranker-fullpage.png) | 整页截图（全部面板）|
| [v65-onnx-reranker-top-after-scroll.png](./snapshots/v65-onnx-reranker-top-after-scroll.png) | 滚动后视图 |
| [v65-onnx-reranker-viewport-current.png](./snapshots/v65-onnx-reranker-viewport-current.png) | 当前视口截图 |

### 面板状态（截图确认）

| 面板 | 数据状态 | 备注 |
|------|---------|------|
| P99 延迟 (SLO 500ms) | ✅ 有数据 | 测试指标注入期间显示数据 |
| 近 5min 加载失败次数 (P0) | No data | 无故障注入，正常 |
| 降级率 (阈值 10%) | ✅ 0% | 无降级，正常 |
| ONNX 推理失败率 (阈值 5%) | ✅ 可见 | 数据展示中 |
| 当前 ONNX Variant | ✅ model_quantized.onnx | 基线 variant |

---

## 5. 指标快照

**快照时间**: 2026-07-29 00:49:10 CST

### 5.1 数据源连通性

| Target | Job | 状态 |
|--------|-----|------|
| host.docker.internal:5678 | yunshu | ✅ up |
| localhost:9090 | prometheus | ✅ up |

### 5.2 Reranker 指标（测试注入）

| 指标 | 值 | 说明 |
|------|-----|------|
| `yunshu_reranker_load_total{backend=onnx,status=success}` | 72 | ONNX 加载成功计数 |
| `yunshu_rerank_duration_ms_count{backend=onnx}` | 72 | 推理次数 |
| `yunshu_reranker_current_variant{variant=model_quantized.onnx}` | 1 | 当前 variant |

> **注**: 测试期间通过临时指标注入器（端口 18081）模拟 reranker 运行数据。快照生成后已清理临时资源，恢复生产配置。

### 5.3 活跃告警

快照期间无告警 firing（测试指标为正常状态，未触发任何告警阈值）。

---

## 6. 数据流验证

```
[指标注入器 :18081] ──scrape──> [Prometheus :9090] ──query──> [Grafana :3000]
     (临时,已清理)              (rule_files: 3)            (dashboard: 10 panels)
                                  ↓
                           38 rules loaded
                           15 groups active
                           22 new rules (reranker + query-pattern)
```

### 验证链路

| # | 验证项 | 方法 | 结果 |
|---|--------|------|------|
| 1 | promtool 校验 | `promtool check config` | ✅ 3 rule files, 38 rules |
| 2 | 容器重建 | `docker-compose up -d prometheus` | ✅ Recreated & Started |
| 3 | 配置加载 | Prometheus 日志 | ✅ "Completed loading of configuration file" |
| 4 | 规则加载 | `GET /api/v1/rules` | ✅ 38 rules / 15 groups |
| 5 | 指标采集 | `GET /api/v1/query` | ✅ reranker 指标已采集 |
| 6 | Grafana 可达 | `GET /api/search` | ✅ 5 dashboards 含 reranker-onnx-v65 |
| 7 | 面板渲染 | 浏览器截图 | ✅ 4 张截图已保存 |

---

## 7. 临时资源清理确认

| 资源 | 操作 | 状态 |
|------|------|------|
| `_tmp_metric_injector.py` | 删除 | ✅ |
| `_tmp_snapshot.py` | 删除 | ✅ |
| prometheus.yml 临时 scrape target | 恢复 | ✅ |
| Prometheus reload | `POST /-/reload` | ✅ 200 |
| Exporter 进程（:18081）| 终止 | ✅ |

---

## 8. 状态总结

### ✅ 变更已生效

- **prometheus.yml**: `rule_files` 引用从 1 → 3，新增 22 条告警规则
- **docker-compose.monitoring.yml**: 新增 `rules` 目录挂载
- **Prometheus**: 38 rules / 15 groups 全部加载成功，无错误日志
- **Grafana**: Dashboard `reranker-onnx-v65` 可访问，10 面板正常渲染

### 持久化状态

变更已持久化到以下文件（未提交 git）：
- `monitoring/prometheus.yml`（已备份 `prometheus.yml.bak.20260729`）
- `docker-compose.monitoring.yml`（已备份 `docker-compose.monitoring.yml.bak.20260729`）

### 待办

- [ ] 提交 git（commit message 见 [变更日志](./V65_PROMETHEUS_RULES_CHANGELOG.md) 第 8 章）
- [ ] 生产环境 reranker 应用产生真实指标后，Grafana 面板将自动展示生产数据
