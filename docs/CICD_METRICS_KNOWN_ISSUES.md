# CI/CD 指标埋点已知问题与规避说明

> **创建日期**：2026-07-30
> **关联变更**：Prometheus 规则变更（CHG-2026-0730）+ CI/CD 埋点集成
> **适用范围**：`.github/workflows/ci-cd.yml` + `scripts/cicd_metrics_push.py`

---

## 一、中风险 1：pushgateway Counter 累计语义局限

### 问题描述

pushgateway 设计初衷是接收短期任务的指标推送，**不维护状态**。Counter 类型指标（如 `Yunshu_ci_pipeline_runs_total`）在 pushgateway 中有以下局限：

| 推送方式 | 行为 | 对 Counter 的影响 |
|---|---|---|
| `push_to_gateway` | DELETE + PUT | 每次推送清空同 job 旧值，Counter 被重置 |
| `pushadd_to_gateway` | 只 PUT 不 DELETE | 保留其他指标，但同 job+标签的 Counter 仍被替换 |

### 已采取的修复（方案 1）

`scripts/cicd_metrics_push.py` 已改用 `pushadd_to_gateway` + `grouping_key={"run_id": GITHUB_RUN_ID}`：

- **pushadd** 避免清除其他 job 的指标
- **grouping_key={run_id}** 让每次 CI 运行成为独立时间序列，Counter 不被后续运行替换
- Prometheus 用 `sum(increase(Yunshu_ci_pipeline_runs_total[1h]))` 聚合所有 run_id 的增量

### 残留风险与规避

**风险**：pushgateway 不自动清理历史 run_id 数据，长期运行后指标膨胀。

**规避措施**：

1. **定期清理**（推荐）：配置 cron 定时清理 7 天前的 run_id 数据
   ```bash
   # 清理 7 天前的 ci-cd-* job 数据（需 pushgateway 支持 admin API）
   curl -X DELETE http://monitoring.internal:9091/metrics/job/ci-cd-build/run_id/<old_run_id>
   ```

2. **pushgateway 启动参数**：设置 `--persistence.interval=5m` 和 `--persistence.file` 让 pushgateway 持久化数据，配合外部清理脚本

3. **Prometheus 配置**：在 `prometheus.yml` 中设置 `honor_labels: true`，并配置 `metric_relabel_configs` 过滤过期的 run_id

### dashboard PromQL 适配建议

加 `run_id` 标签后，dashboard 中 Counter 类查询**必须用 `sum()` 聚合**：

```promql
# ✅ 正确：聚合所有 run_id
sum by (stage) (increase(Yunshu_ci_pipeline_runs_total[1h]))

# ❌ 错误：直接 rate 会产生多个时间序列，面板混乱
rate(Yunshu_ci_pipeline_runs_total[1h])
```

Gauge 类指标（如 `Yunshu_ci_test_coverage_percent`）不受影响，可直接查询最新值：

```promql
# ✅ Gauge：取最新值
Yunshu_ci_test_coverage_percent
```

---

## 二、中风险 2：coverage/duration 参数提取依赖

### 问题描述

CI/CD 流水线中 `--coverage` 和 `--duration` 参数的提取依赖外部工具，工具不可用时会降级。

### coverage 提取依赖

| 依赖 | 用途 | 不可用时降级 |
|---|---|---|
| `pytest-cov` | 生成 `coverage.json` | 覆盖率降级为 0，dashboard 面板显示 0% |
| `coverage.json` 文件 | 提取 `totals.percent_covered` | 同上 |

**规避措施**：
- CI 环境的 `requirements.txt` 中已包含 `pytest-cov`
- `cicd_metrics_push.py` 的 `Extract coverage` step 用 `|| echo "0"` 降级，不阻塞流水线
- 若覆盖率持续为 0，检查 `pytest-cov` 是否安装：`pip list | grep pytest-cov`

### duration 精度限制

| 限制 | 说明 |
|---|---|
| 时钟精度 | GitHub Actions runner 使用 `date +%s`（秒级），短时部署（<5s）可能显示为 0 |
| 时区差异 | runner 使用 UTC，`date +%s` 返回 Unix 时间戳，不受时区影响 |
| DEPLOY_START 未设置 | 用 `${DEPLOY_START:-$DEPLOY_END}` 防御，duration 降级为 0 |

**规避措施**：
- 短时部署可忽略 duration（<5s 的部署耗时本身无监控价值）
- 若需毫秒级精度，改用 Python 的 `time.time()`：
  ```bash
  python -c "import time; print(time.time())"
  ```

---

## 三、低风险：deploy 阶段无 Deploying 中间状态

### 问题描述

`cicd_metrics_push.py` 在 deploy 成功时直接设置 `Stable(0)`，失败时设置 `Failed(3)`，跳过 `Deploying(1)` 中间状态。

### 影响

dashboard 的"部署状态"面板可能永远不显示 `Deploying` 状态。

### 规避措施（可选增强）

在 `deployment-ready` job 开头添加 step 推送 Deploying 状态：

```yaml
      - name: Mark deployment in progress
        run: |
          python scripts/cicd_metrics_push.py --stage deploy --env production --success
          # 注：需 cicd_metrics_push.py 新增 --status 参数支持仅设置 Deploying(1)
```

当前方案接受此局限——`Deploying` 状态持续时间通常很短（秒级），监控价值有限。

---

## 四、检查清单

集成后请按以下清单验证：

- [ ] pushgateway 可达：`curl http://monitoring.internal:9091/-/healthy`
- [ ] CI 运行后 pushgateway 有指标：`curl http://monitoring.internal:9091/metrics | grep Yunshu_ci`
- [ ] Prometheus 能 scrape pushgateway：检查 `prometheus.yml` 中 pushgateway job 配置
- [ ] dashboard 覆盖率面板有数据：触发一次 CI 运行后检查
- [ ] dashboard 部署耗时面板有数据：触发一次部署后检查
- [ ] Counter 查询用 `sum(increase(...))`：检查 dashboard PromQL
- [ ] 历史数据清理计划：配置 cron 或定期手动清理

---

## 五、相关文件

| 文件 | 说明 |
|---|---|
| `scripts/cicd_metrics_push.py` | CI/CD 指标推送脚本（含 pushadd 修复） |
| `scripts/cicd_metrics_instrumentation_example.py` | 埋点示例代码 |
| `.github/workflows/ci-cd.yml` | CI/CD 流水线（含埋点 step） |
| `agent/monitoring/prometheus.py` | PrometheusMetricsExporter（10 个指标定义） |
| `monitoring/prometheus/alert_rules.yml` | 告警规则（含部署/回滚告警） |
| `monitoring/grafana/dashboards/yunshu-full-monitoring.json` | 全量监控面板 |
