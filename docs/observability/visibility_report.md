# 四层可见性覆盖报告

- **生成时间**：2026-06-27T01:01:05.634402
- **Trace ID**：`1105c4c53c3b49fc`
- **生成耗时**：2011.09 ms
- **总体状态**：❌ 阈值未达标
- **阈值违规**：4 项

## 四层概览

| 层级 | 描述 | 状态 |
| --- | --- | --- |
| 运行时可见 | 结构化日志、链路追踪、健康检查端点 | ❌ 阈值未达标 |
| 验证过程可见 | 测试覆盖率、边界测试、契约测试 | ❌ 阈值未达标 |
| 业务价值可见 | 埋点覆盖率、看板数、告警规则数 | ❌ 阈值未达标 |
| 架构影响可见 | 依赖图、架构规则、变更影响分析 | ✅ 通过 |

## 运行时可见

_结构化日志、链路追踪、健康检查端点_

| 指标 | 数值 | 阈值 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| `structured_log_coverage` | 6.9% | ≥ 30 | ❌ | 包含 trace_id/module_name/action/duration_ms 的日志占比 |
| `trace_coverage` | 0.0% | ≥ 30 | ❌ | 使用 @trace_route 或 TraceContext 的路由占比 |
| `health_endpoints` | 2个 | ≥ 1 | ✅ | 健康检查端点数量 |

## 验证过程可见

_测试覆盖率、边界测试、契约测试_

| 指标 | 数值 | 阈值 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| `test_coverage` | 40.0% | ≥ 40 | ✅ | 代码测试覆盖率（来自 coverage.xml 或 pyproject.toml） |
| `boundary_test_coverage` | 12.2% | ≥ 5 | ✅ | 边界测试用例占总测试比例 |
| `contract_test_count` | 0个 | ≥ 3 | ❌ | Pact 契约测试数量 |

## 业务价值可见

_埋点覆盖率、看板数、告警规则数_

| 指标 | 数值 | 阈值 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| `track_event_coverage` | 4.3% | ≥ 30 | ❌ | 包含 trackEvent/track( 调用的核心模块占比 |
| `dashboard_count` | 9个 | ≥ 3 | ✅ | 监控看板数量 |
| `alert_rules_count` | 13条 | ≥ 5 | ✅ | Prometheus 告警规则数量 |

## 架构影响可见

_依赖图、架构规则、变更影响分析_

| 指标 | 数值 | 阈值 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| `dependency_graph_nodes` | 215个 | ≥ 10 | ✅ | 模块依赖图节点数 |
| `arch_rule_violations` | 2个 | ≥ 0 | ✅ | 架构规则违规数（越少越好） |
| `impact_analysis_coverage` | 100.0% | ≥ 80 | ✅ | 变更影响分析报告覆盖率 |

## 阈值违规清单

- ❌ 运行时可见.structured_log_coverage: 实际=6.9%, 阈值=30%
- ❌ 运行时可见.trace_coverage: 实际=0.0%, 阈值=30%
- ❌ 验证过程可见.contract_test_count: 实际=0个, 阈值=3个
- ❌ 业务价值可见.track_event_coverage: 实际=4.3%, 阈值=30%

> ⚠️ **CI 阻断**：上述阈值未达标，请补充对应的可见性能力后重试。

---
_由 `scripts/visibility_report.py` 自动生成_