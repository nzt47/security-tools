# 技术债务登记表（Tech Debt Register）

> **用途**: 记录架构规则校验中已豁免的违规项，跟踪缓解措施与清零计划。
> **更新规则**: 每次架构规则校验后同步更新；新增豁免需经架构评审。
> **关联文件**: [legacy_exemptions.json](legacy_exemptions.json) | [circular_dependencies_troubleshooting.md](circular_dependencies_troubleshooting.md)

---

## 循环依赖（no_circular_dependency）

所有豁免项均为 `agent.monitoring` 子模块与 `agent.monitoring.observability_config` / `agent.error_handler` 之间的静态循环依赖。根因相同：监控组件在初始化时需要读取配置值，采用了函数内延迟 import（try/except 包裹）规避运行时循环，但 arch_rules 静态分析仍检测到 `from` 语句。

### ARCH-DEBT-003: prometheus → observability_config

| 字段 | 值 |
|------|-----|
| **源模块** | `agent.monitoring.prometheus` |
| **目标模块** | `agent.monitoring.observability_config` |
| **位置** | `agent/monitoring/prometheus.py:60` |
| **登记日期** | 2026-07-06 |
| **负责人** | observability-team |
| **状态** | 🟡 已缓解（延迟导入） |

**原因**: `prometheus.py` 在 `__init__` 的 try/except 块中延迟 import `get_prometheus_max_retries`，运行时安全。`observability_config` 仅导入 tracing 模块，arch_rules 静态分析检测到 from 语句。

**缓解措施**: 使用函数内延迟 import（try/except 包裹），运行期不会触发循环。

**清零方案**: 通过依赖注入将配置值作为参数传入 `PrometheusMetricsExporter`，消除静态依赖边。

---

### ARCH-DEBT-004: loki → observability_config

| 字段 | 值 |
|------|-----|
| **源模块** | `agent.monitoring.loki` |
| **目标模块** | `agent.monitoring.observability_config` |
| **位置** | `agent/monitoring/loki.py:46` |
| **登记日期** | 2026-07-06 |
| **负责人** | observability-team |
| **状态** | 🟡 已缓解（延迟导入） |

**原因**: `loki.py` 在 `__init__` 的 try/except 块中延迟 import `get_loki_push_timeout` / `get_loki_query_timeout`，运行时安全。

**缓解措施**: 使用函数内延迟 import（try/except 包裹），运行期不会触发循环。

**清零方案**: 通过依赖注入将超时配置值作为参数传入 Loki 推送器。

---

### ARCH-DEBT-005: alert_notifier → observability_config

| 字段 | 值 |
|------|-----|
| **源模块** | `agent.monitoring.alert_notifier` |
| **目标模块** | `agent.monitoring.observability_config` |
| **位置** | `agent/monitoring/alert_notifier.py:85` |
| **登记日期** | 2026-07-06 |
| **负责人** | observability-team |
| **状态** | 🟡 已缓解（延迟导入） |

**原因**: `alert_notifier.py` 在 `__init__` 的 try/except 块中延迟 import `get_alert_timeout`，运行时安全。

**缓解措施**: 使用函数内延迟 import（try/except 包裹），运行期不会触发循环。

**清零方案**: 通过依赖注入将告警超时配置值作为参数传入 AlertNotifier。

---

### ARCH-DEBT-006: error_handler → observability_config

| 字段 | 值 |
|------|-----|
| **源模块** | `agent.error_handler` |
| **目标模块** | `agent.monitoring.observability_config` |
| **位置** | `agent/error_handler.py:339` |
| **登记日期** | 2026-07-06 |
| **负责人** | observability-team |
| **状态** | 🟡 已缓解（延迟导入） |

**原因**: `error_handler.py` 在 `RetryPolicy.__init__` 中延迟 import `get_default_max_retries`，运行时安全。

**缓解措施**: 使用函数内延迟 import，运行期不会触发循环。

**清零方案**: 将默认重试次数作为参数传入 `RetryPolicy`，消除对 `observability_config` 的静态依赖。

---

### ARCH-DEBT-007: prometheus → error_handler

| 字段 | 值 |
|------|-----|
| **源模块** | `agent.monitoring.prometheus` |
| **目标模块** | `agent.error_handler` |
| **位置** | `agent/monitoring/prometheus.py:291` |
| **登记日期** | 2026-07-07 |
| **负责人** | observability-team |
| **状态** | 🟡 已缓解（延迟导入） |

**原因**: `prometheus.py` 在 `execute_with_error_handling` 方法内延迟 import `RetryPolicy`，运行时安全。与 ARCH-DEBT-003~006 同源（commit `f2542a94` 的延迟导入修复），当时漏登此豁免。

**缓解措施**: 使用函数内延迟 import（方法体内 `from agent.error_handler import RetryPolicy`）。

**清零方案**: 通过依赖注入将 `RetryPolicy` 实例作为参数传入 `PrometheusMetricsExporter`，彻底消除 `prometheus → error_handler` 静态依赖边。

---

## 统一清零计划

| 优先级 | 范围 | 方案 | 预估工时 |
|--------|------|------|---------|
| P2 | ARCH-DEBT-003~007 | 引入 `MonitoringConfig` 数据类，在监控组件初始化时通过构造函数注入配置值，消除所有延迟 import | 2-3 人日 |

**统一方案设计**:

```
# 目标架构：配置值通过构造函数注入，消除循环依赖
class MonitoringConfig:
    prometheus_max_retries: int
    loki_push_timeout: float
    loki_query_timeout: float
    alert_timeout: float
    default_max_retries: int

# 所有监控组件不再直接 import observability_config
class PrometheusMetricsExporter:
    def __init__(self, config: MonitoringConfig): ...
```

---

## 评审记录

| 日期 | 事件 | 操作人 |
|------|------|--------|
| 2026-07-06 | 创建豁免清单（ARCH-DEBT-003~006） | observability-team |
| 2026-07-07 | 补登 ARCH-DEBT-007（漏登记修复） | observability-team |
| 2026-08-01 | 归档至技术债务登记表 | CI 自动校验 + 人工确认 |
