# 云枢 Grafana 看板目录

本目录存放可导入 Grafana 的看板 JSON 文件，分为两类：

## 一、模板生成的功能看板（feature dashboards）

由 `scripts/generate_dashboard.py` 基于模板 `templates/feature_template.json` 生成，
每个看板包含 4 个标准面板，指标命名遵循 `yunshu_<模块>_<动作>` 规范。

| 文件 | 模块 | 看板 UID | 用途 |
| --- | --- | --- | --- |
| `yunshu_chat_dashboard.json` | chat | yunshu-chat-feature | 对话核心链路监控（QPS / 成功率 / P99 / 转化漏斗） |
| `yunshu_business_dashboard.json` | dashboard | yunshu-dashboard-feature | 业务大盘（仪表盘加载量 / 成功率 / P99 / 转化漏斗） |
| `yunshu_memory_dashboard.json` | memory | yunshu-memory-feature | 记忆模块监控（读写量 / 成功率 / P99 / 转化漏斗） |

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
2. 上传本目录中的 JSON 文件
3. 选择 Prometheus 数据源（uid: `Prometheus`）
4. 点击 Import 完成导入

## 二、存量看板

| 文件 | 用途 |
| --- | --- |
| `yunshu_health_dashboard.json` | 系统健康度综合看板（运行时 / 验证 / 业务 / 架构四层） |
| `yunshu_v2_dashboard.json` | V2 总览看板 |
| `yunshu_resource_release_dashboard.json` | 资源发布看板 |

## 三、相关资源

- 看板模板：`templates/feature_template.json`
- 生成脚本：`scripts/generate_dashboard.py`
- 指标埋点：`agent/monitoring/business_metrics.py`（`BusinessMetricsCollector`）
- 告警规则：`monitoring/alerts.yml`
- 可见性阈值：`config.yaml` → `visibility_thresholds.business.dashboard_count`
