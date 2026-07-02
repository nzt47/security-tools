# 四层可见性覆盖报告

- **生成时间**：2026-07-01T12:23:05.959367
- **Trace ID**：`90dca19e0d5841c8`
- **生成耗时**：3374.04 ms
- **总体状态**：✅ 通过

## 四层概览

| 层级 | 描述 | 状态 |
| --- | --- | --- |
| 运行时可见 | 结构化日志、链路追踪、健康检查端点 | ✅ 通过 |
| 验证过程可见 | 测试覆盖率、边界测试、契约测试 | ✅ 通过 |
| 业务价值可见 | 埋点覆盖率、看板数、告警规则数 | ✅ 通过 |
| 架构影响可见 | 依赖图、架构规则、变更影响分析 | ✅ 通过 |

## 运行时可见

_结构化日志、链路追踪、健康检查端点_

| 指标 | 数值 | 阈值 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| `structured_log_coverage` | 71.9% | ≥ 55 | ✅ | 包含 trace_id/module_name/action/duration_ms 的日志占比 |
| `trace_coverage` | 92.0% | ≥ 16 | ✅ | 使用 @trace_route 或 TraceContext 的路由占比 |
| `health_endpoints` | 2个 | ≥ 1 | ✅ | 健康检查端点数量 |

## 验证过程可见

_测试覆盖率、边界测试、契约测试_

| 指标 | 数值 | 阈值 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| `test_coverage` | 5.1% | ≥ 0 | ✅ | 代码测试覆盖率（来自 coverage.xml 真实 line-rate） |
| `boundary_test_coverage` | 21.1% | ≥ 12 | ✅ | 边界测试用例占总测试比例 |
| `contract_test_count` | 3个 | ≥ 3 | ✅ | Pact 契约测试数量 |
| `exception_coverage` | 81.5% | ≥ 80 | ✅ | 含 try/except/raise 异常处理的核心模块占比 |

## 业务价值可见

_埋点覆盖率、看板数、告警规则数_

| 指标 | 数值 | 阈值 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| `track_event_coverage` | 51.7% | ≥ 50 | ✅ | 包含 trackEvent/track( 调用的核心模块占比 |
| `dashboard_count` | 9个 | ≥ 3 | ✅ | 监控看板数量 |
| `alert_rules_count` | 13条 | ≥ 5 | ✅ | Prometheus 告警规则数量 |

## 架构影响可见

_依赖图、架构规则、变更影响分析_

| 指标 | 数值 | 阈值 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| `dependency_graph_nodes` | 220个 | ≥ 10 | ✅ | 模块依赖图节点数 |
| `arch_rule_violations` | 0个 | ≥ 0 | ✅ | 架构规则违规数（越少越好） |
| `impact_analysis_coverage` | 100.0% | ≥ 80 | ✅ | 变更影响分析报告覆盖率 |

## 阈值检查

- ✅ 所有指标均达到阈值要求

---
_由 `scripts/visibility_report.py` 自动生成_