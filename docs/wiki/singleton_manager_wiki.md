# SingletonManager 统一单例管理 Wiki

> 最后更新：2026-08-09
> 维护者：团队共享
> 主文档：[迁移总结报告](../SingletonManager_Migration_Summary_Report.md) ｜ [迁移实施计划](../SingletonManager_Migration_Plan.md) ｜ [迁移清单](../SingletonManager_Migration_Checklist.md)

---

## 概述

项目统一将"模块级全局变量 + 延迟初始化"的单例收口到 [`agent/utils/singleton_manager.py`](../../agent/utils/singleton_manager.py)，提供：

- **双重检查锁定**：并发首次初始化只构造一次，线程安全
- **可重置**：`reset_singleton(name)` 供测试隔离，配套各模块 `reset_xxx()` 便捷函数
- **config 注入**：`get_singleton(name, {"xxx_config": ...})` dict 通道
- **cleanup 钩子**：注册时 `cleanup_fn(instance)`，reset 时自动回收资源（stop 线程 / 卸载 hooks 等）
- **向后兼容**：各模块保留 `try/except ImportError` 导入块与 fallback 变量

## 统计信息（51 个单例）

截至 2026-08-09，SingletonManager 注册的**唯一单例名共 51 个**（排除测试注册 `foo`），按模块分布：

| 模块 | 单例名 | 迁移状态 |
|------|--------|---------|
| `agent/task_scheduler.py` | `task_scheduler` | ✅ 高优先级 |
| `agent/system_prompt_config.py` | `system_prompt_manager` | ✅ 高优先级 |
| `agent/logging_utils.py` | `audit_logger` / `safety_monitor` | ✅ 高优先级 |
| `agent/log_system/safe_logger.py` | `safe_logger_audit_logger` / `safe_logger_safety_monitor` | ✅ 高优先级（方案 B） |
| `agent/monitoring/self_healer.py` | `self_healer` | ✅ 高优先级 |
| `agent/monitoring/search.py` | `search_performance_monitor` | ✅ 高优先级 |
| `agent/monitoring/alert_notifier.py` | `alert_notifier` | ✅ 中优先级 |
| `agent/monitoring/alert_manager.py` | `alert_manager` | ✅ 中优先级 |
| `agent/monitoring/alert_evaluator.py` | `alert_evaluator` | ✅ 中优先级 |
| `agent/monitoring/performance.py` | `performance_alert_manager` | ✅ 中优先级 |
| `agent/disaster_recovery.py` | `disaster_recovery` / `config_hot_reloader` | ✅ 中优先级 |
| `agent/llm_monitor.py` | `llm_monitor` | ✅ 中优先级 |
| `agent/mcp_executor.py` | `mcp_executor` | ✅ 中优先级 |
| `agent/health/health_score.py` | `health_score_calculator` | ✅ 中优先级 |
| `agent/scheduling.py` | `schedule_scheduler` | ✅ 低优先级修正收口 |
| `agent/utils/sensitive_data_filter.py` | `sensitive_data_filter` | ✅ 低优先级修正收口 |

**既有已收口单例（迁移前即使用 SingletonManager）**：`ab_test_manager`、`async_executor`、`api_gateway`、`auto_tuner`、`failure_analyzer`、`failure_collector`、`feedback_manager`、`security_checker`、`lazy_loader`、`async_lazy_loader`、`chaos_injector`、`optimized_storage`、`performance_optimization_manager`、`safety_guard`、`state_manager`、`optimized_metrics_collector`、`observability_optimization_manager`、`observability_config`、`loki_client`、`error_reporter`、`resource_monitor`、`prompt_version_manager`、`access_logger`、`prompt_storage`、`sampling_manager`、`prompt_registry`、`trace_cache`、`prompt_deployment_manager`、`trace_storage`、`global_index`、`health_calculator`、`prometheus_exporter` 等 35 个。

## 测试数据（299 项新增单测）

15 个 `tests/unit/test_*_singleton.py` 测试文件共 **299 项**，覆盖：单例唯一性、注册、reset/GC/幂等、config 通道、cleanup 钩子、并发双检锁、fallback、生命周期与异常处理。全量实测通过。

## 暂缓模块（2 个）

| 模块 | 暂缓理由 |
|------|---------|
| `rate_limiter` | 命名注册表语义（`_global_limiters` 按名缓存多实例）与单实例语义不匹配；备选方案见 [rate_limiter 暂缓方案](rate_limiter_migration_wiki.md) |
| `tool_router_hybrid` | 已双检锁 + reset 规范化，仅缺 register，收益有限 |

## 经验要点

1. **config 通道双形态**：仅当 dict 含特定键（`{"xxx_config": ...}`）才解包，直接传入的 dict/对象原样传递。
2. **cleanup 钩子**：有 start/stop 或资源生命周期的模块用 `stop()`（幂等）；无资源生命周期不注册。
3. **外部副作用模块**（llm_monitor）：cleanup 必须恢复被补丁的宿主类方法（`uninstall_hooks()`），防闭包悬空引用。
4. **工厂必须是模块级 def**，不能 lambda 闭包。
5. **测试隔离**：迁移后 fallback 变量恒为 None，测试必须用 `reset_xxx()`。
