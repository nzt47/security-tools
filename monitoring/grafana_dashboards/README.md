# 云枢 Grafana 看板目录

> ⚠️ **本目录不会被 compose 自动加载。** Grafana 实际加载的是 `monitoring/grafana/dashboards/`
> （compose 把该目录挂到 `/etc/grafana/provisioning/dashboards`）。本目录的 JSON 必须手动 Import，
> 或先复制到 `monitoring/grafana/dashboards/`。
>
> ⚠️ **生成看板前必须确认引用的指标名真实存在**：模板生成的是占位指标名
> （`yunshu_<模块>_total` 等），真实指标清单以 `GET http://127.0.0.1:5678/metrics` +
> `GET /api/business/prometheus` 的实际输出为准（合并清单见 `docs/closeout/监控清理_evidence_20261002/live_metric_names.txt`），
> 记录规则见 `monitoring/recording_rules.yml`。名字对不上 = 面板永久 No data，而且不会报错。
> 生成前先跑 `python scripts/generate_dashboard.py --module <模块> --dry-run`：
> 它**只预览不写文件**，会列出即将写入看板的全部引用指标，逐个到上面两份清单里核对。

本目录存放可导入 Grafana 的看板 JSON 文件，分为两类：

## 一、模板生成的功能看板（feature dashboards）

由 `scripts/generate_dashboard.py`（仓库根 `scripts/` 目录）基于模板
`templates/feature_template.json` 生成，每个看板包含 4 个标准面板，
指标命名遵循 `yunshu_<模块>_<动作>` 规范。

| 文件 | 模块 | 看板 UID | 用途 |
| --- | --- | --- | --- |
| *（本目录当前无生成产物）* | — | — | — |

> **2026-10-02 删除**：`yunshu_chat_dashboard.json`、`yunshu_business_dashboard.json`、
> `yunshu_memory_dashboard.json` 三个模板看板已删除——它们 100% 引用本部署不存在的指标名。
> 对话/业务/记忆的监控改由 `monitoring/grafana/dashboards/business_metrics.json` 等看板承担。
> 需要新看板时用下面的命令重新生成，**并先做指标名核对**。

### 标准面板说明

每个功能看板包含以下 4 个面板：

1. **调用量 / QPS** —— `sum(rate(yunshu_<module>_total[5m]))`，1m 与 5m 双曲线
2. **成功率 / 失败计数** —— `success="true"` 占比（双 Y 轴，右侧显示失败次数）
3. **P50/P90/P99 耗时** —— `histogram_quantile` 分位数统计（单位：秒）
4. **转化漏斗（24h）** —— 24 小时累计 总调用 / 成功 / 失败 环形图

### 生成新看板

```bash
# 生成指定模块看板
python scripts/generate_dashboard.py --module <module_name> --output monitoring/grafana_dashboards/yunshu_<module_name>_dashboard.json

# 预览引用指标（不写文件）
python scripts/generate_dashboard.py --module <module_name> --dry-run
```

模块名规则：小写字母 / 数字 / 下划线，首字符必须为字母（正则 `^[a-z][a-z0-9_]*$`）。

### 导入步骤

1. 打开 Grafana → Dashboards → Import
2. 上传本目录中的 JSON 文件（**本目录不被自动加载，必须手动导入**）
3. 选择 Prometheus 数据源（uid: `prometheus`，由 `monitoring/grafana/datasources/prometheus.yml` 固定）
4. 点击 Import 完成导入
5. 导入后逐面板确认有数据；No data 时先查指标名是否存在，再怀疑数据源

## 二、存量看板

| 文件 | 用途 |
| --- | --- |
| `yunshu_health_dashboard.json` | 系统健康度综合看板（运行时 / 验证 / 业务 / 架构四层） |
| `yunshu_resource_release_dashboard.json` | 资源发布看板 |

> **2026-10-02 删除**：`yunshu_v2_dashboard.json`（V2 总览看板）已删除——它整篇引用
> `Yunshu_*` 大写命名空间与已不存在的 V2 模块指标。V2 总览能力见
> `monitoring/grafana/dashboards/yunshu-full-monitoring.json`
> （**注意其部署/CI 类面板在本部署无数据源，必然为空**）。
>
> 保留的这两份看板同样要先核对指标名：**引用无前缀旧名（`http_requests_total`、
> `system_cpu_usage_percent` 等）或 CI/部署类指标的面板必然是空的**。

## 三、相关资源

- 看板模板：`templates/feature_template.json`
- 生成脚本：`scripts/generate_dashboard.py`（仓库根 `scripts/`）
- **实际被自动加载的看板目录**：`monitoring/grafana/dashboards/`（逐看板说明见 `monitoring/GRAFANA_SETUP_GUIDE.md` 第 4 节）
- 指标埋点：`agent/monitoring/business_metrics.py`（`BusinessMetricsCollector`）
- 告警规则：`monitoring/alerts.yml`
- 可见性阈值：`config.yaml` → `visibility_thresholds.business.dashboard_count`
