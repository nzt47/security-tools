# 架构规则校验报告

- **状态**: ✅ 通过
- **Trace ID**: `acbe9d8dbef44801`
- **扫描根目录**: `agent`
- **校验规则数**: 7
- **违规总数**: 4
- **未豁免违规**: 0
- **已豁免违规**: 4
- **耗时**: 3683.80 ms

## 高 严重度（4 项）

| 规则 | 源模块 | 目标模块 | 文件:行 | 状态 | 建议 |
|------|--------|----------|---------|------|------|
| no_circular_dependency | `agent.error_handler` | `agent.monitoring.observability_config` | agent\error_handler.py:364 | 🚫 豁免 | 通过依赖倒置或中间层解耦，或使用 lazy_loader 延迟加载 |
| no_circular_dependency | `agent.monitoring.prometheus` | `agent.monitoring.observability_config` | agent\monitoring\prometheus.py:60 | 🚫 豁免 | 通过依赖倒置或中间层解耦，或使用 lazy_loader 延迟加载 |
| no_circular_dependency | `agent.monitoring.loki` | `agent.monitoring.observability_config` | agent\monitoring\loki.py:47 | 🚫 豁免 | 通过依赖倒置或中间层解耦，或使用 lazy_loader 延迟加载 |
| no_circular_dependency | `agent.monitoring.alert_notifier` | `agent.monitoring.observability_config` | agent\monitoring\alert_notifier.py:96 | 🚫 豁免 | 通过依赖倒置或中间层解耦，或使用 lazy_loader 延迟加载 |
