# 四层可见性趋势周报

- **生成时间**：2026-06-28T01:02:13.570836+00:00
- **Trace ID**：`b1ecfa70c011419b`
- **报告周期**：2026-06-21T01:02:13.488799+00:00 ~ 2026-06-28T01:02:13.488799+00:00
- **数据源**：http://localhost:9091
- **生成耗时**：82.02 ms
- **总体状态**：✅ 通过

## 概览统计

| 指标 | 当前值 | 周期初 | 趋势 | 变化 |
| --- | --- | --- | --- | --- |
| `overall_status` | 1 | 1 | ❓ | 数据不足 |
| `threshold_violations` | 5.00 项 | 5.00 项 | ❓ | 数据不足 |
| `passing_layers` | 1.00 层 | 1.00 层 | ❓ | 数据不足 |
| `report_duration` | 3.90 秒 | 3.90 秒 | ❓ | 数据不足 |
| `runtime_structured_log_coverage` | — | — | ❓ | 数据缺失 |
| `runtime_trace_coverage` | — | — | ❓ | 数据缺失 |
| `runtime_health_endpoints` | 2.00 个 | 2.00 个 | ❓ | 数据不足 |
| `verification_test_coverage` | — | — | ❓ | 数据缺失 |
| `verification_boundary_test_coverage` | — | — | ❓ | 数据缺失 |
| `business_track_event_coverage` | — | — | ❓ | 数据缺失 |
| `business_dashboard_count` | 9.00 个 | 9.00 个 | ❓ | 数据不足 |
| `business_alert_rules_count` | 13.00 条 | 13.00 条 | ❓ | 数据不足 |
| `architecture_dependency_graph_nodes` | 215.00 个 | 215.00 个 | ❓ | 数据不足 |
| `architecture_rule_violations` | — | — | ❓ | 数据缺失 |
| `architecture_impact_analysis_coverage` | 100.0% | 100.0% | ❓ | 数据不足 |
| `verification_contract_test_count` | 3.00 个 | 3.00 个 | ❓ | 数据不足 |

## 总体状态

### 总体可见性状态（0=pass, 1=fail, 2=degraded）

- **指标名**：`overall_status`
- **数据点数**：1
- **当前值**：1
- **周期初值**：1
- **趋势**：❓ 数据不足

### 阈值违规项总数

- **指标名**：`threshold_violations`
- **数据点数**：1
- **当前值**：5.00 项
- **周期初值**：5.00 项
- **趋势**：❓ 数据不足

### 通过层数（0-4）

- **指标名**：`passing_layers`
- **数据点数**：1
- **当前值**：1.00 层
- **周期初值**：1.00 层
- **趋势**：❓ 数据不足

### 报告生成耗时（秒）

- **指标名**：`report_duration`
- **数据点数**：1
- **当前值**：3.90 秒
- **周期初值**：3.90 秒
- **趋势**：❓ 数据不足

## 运行时可见

### 结构化日志覆盖率

> ⚠️ **数据缺失**：Prometheus 返回空数据（指标可能尚未采集）

### 链路追踪覆盖率

> ⚠️ **数据缺失**：Prometheus 返回空数据（指标可能尚未采集）

### 健康检查端点数

- **指标名**：`runtime_health_endpoints`
- **数据点数**：1
- **当前值**：2.00 个
- **周期初值**：2.00 个
- **趋势**：❓ 数据不足

## 验证过程可见

### 测试覆盖率

> ⚠️ **数据缺失**：Prometheus 返回空数据（指标可能尚未采集）

### 边界测试覆盖率

> ⚠️ **数据缺失**：Prometheus 返回空数据（指标可能尚未采集）

### 契约测试数

- **指标名**：`verification_contract_test_count`
- **数据点数**：1
- **当前值**：3.00 个
- **周期初值**：3.00 个
- **趋势**：❓ 数据不足

## 业务价值可见

### 埋点覆盖率

> ⚠️ **数据缺失**：Prometheus 返回空数据（指标可能尚未采集）

### 看板数量

- **指标名**：`business_dashboard_count`
- **数据点数**：1
- **当前值**：9.00 个
- **周期初值**：9.00 个
- **趋势**：❓ 数据不足

### 告警规则数

- **指标名**：`business_alert_rules_count`
- **数据点数**：1
- **当前值**：13.00 条
- **周期初值**：13.00 条
- **趋势**：❓ 数据不足

## 架构影响可见

### 依赖图节点数

- **指标名**：`architecture_dependency_graph_nodes`
- **数据点数**：1
- **当前值**：215.00 个
- **周期初值**：215.00 个
- **趋势**：❓ 数据不足

### 架构规则违规数（越少越好）

> ⚠️ **数据缺失**：Prometheus 返回空数据（指标可能尚未采集）

### 影响分析覆盖率

- **指标名**：`architecture_impact_analysis_coverage`
- **数据点数**：1
- **当前值**：100.0%
- **周期初值**：100.0%
- **趋势**：❓ 数据不足

## 健康检查

| 检查项 | 状态 |
| --- | --- |
| Prometheus 可达性 | ✅ 已连接 |
| 查询成功率 | 10/16 |
| 报告生成状态 | ✅ 通过 |

---
_由 `scripts/generate_visibility_trend.py` 自动生成_