# 旧模式单例迁移优先级评估报告

> 日期：2026-08-08
> 关联：[SingletonManager 迁移指南](SingletonManager_Migration_Guide.md) / [性能基准测试报告](SingletonManager_Performance_Report.md)

## 背景

SingletonManager 第一阶段已完成 **34 个单例** 统一收口。全项目扫描（`^_\w+: Optional[...] = None` 模式）发现仍有 **18 个模块**使用旧式"模块级全局变量 + 延迟初始化"单例（约 21 个单例变量）。本报告评估其迁移优先级，作为下一阶段改造依据。

> **状态更新（2026-08-08）**：高优先级 5 模块（#1 task_scheduler、#2 system_prompt_config、#3+#4 logging_utils/safe_logger、#5 self_healer、#6 monitoring/search）**已全部完成迁移**，统一收口单例总数 34 → **39**。本报告高优先级章节可作为迁移实施参考，迁移细节与验证见 [SingletonManager_Migration_Completion_Report.md](SingletonManager_Migration_Completion_Report.md)。剩余候选模块调整为 13 个。

## 候选模块全景

| # | 模块 | 单例变量 | 公共 getter | config 注入 | 线程锁 | 资源清理 | 测试直接赋值 | 引用量级 |
|---|------|----------|-------------|:---:|:---:|:---:|:---:|:---:|
| 1 | `agent/task_scheduler.py` | `_scheduler` | `get_scheduler()` | 无 | 无 | 预注册任务 | ✅ | 高（19 行） |
| 2 | `agent/system_prompt_config.py` | `_manager` | `get_manager()` | 无 | 无 | 无 | ✅ conftest + 2 处 | 中 |
| 3 | `agent/logging_utils.py` | `_audit_logger` `_safety_monitor` | `get_audit_logger()` `get_safety_monitor()` | 无 | 无 | 无 | 无 | 中 |
| 4 | `agent/log_system/safe_logger.py` | `_audit_logger` `_safety_monitor`（与 #3 重复） | 同名 getter | 无 | 无 | 无 | 无 | 中 |
| 5 | `agent/monitoring/self_healer.py` | `_self_healer` | `get_self_healer(config)` | ✅ | 无 | 自愈动作 | ✅ 2 处 | 高（10+） |
| 6 | `agent/monitoring/alert_notifier.py` | `_alert_notifier` | `get_alert_notifier(config)` | ✅ | 无 | 通知发送 | ✅ 4 处 | 中 |
| 7 | `agent/monitoring/alert_manager.py` | `_alert_manager` | `get_alert_manager(config_path)` | ✅ | 无 | start/stop | 无 | 中（5） |
| 8 | `agent/monitoring/alert_evaluator.py` | `_alert_evaluator` | `get_alert_evaluator()` | 无 | 无 | start/stop | 无 | 中 |
| 9 | `agent/monitoring/performance.py` | `_alert_manager` | `get_alert_manager(config: AlertConfig)` | ✅ | 无 | 无 | ✅ 1 处 | 中 |
| 10 | `agent/monitoring/search.py` | `_performance_monitor` | `get_performance_monitor()` | 无 | 无 | start/stop | 无 | 高（8+） |
| 11 | `agent/disaster_recovery.py` | `_dr_singleton` `_reloader_singleton` | `get_disaster_recovery()` `get_config_reloader()` | 无 | ✅ | 备份/恢复 | 无 | 中（5） |
| 12 | `agent/llm_monitor.py` | `_monitor` | `get_monitor()` | 无 | 无 | install_hooks | 无 | 中 |
| 13 | `agent/mcp_executor.py` | `_executor` | `get_mcp_executor()` | 无 | 无 | 执行器 | 无 | 中 |
| 14 | `agent/health/health_score.py` | `_default_calculator` | `get_health_calculator()` | 无 | 无 | 无 | 无 | 低（2） |
| 15 | `agent/scheduling.py` | `_schedule_scheduler` | `get_schedule_scheduler()` | 无 | 无 | 调度器 | ✅ 1 处 | 低（1） |
| 16 | `agent/tool_router_hybrid.py` | `_hybrid_instance` | `get_hybrid_retriever()` | 无 | ✅ | 无 | 无 | 中 |
| 17 | `agent/utils/sensitive_data_filter.py` | `_default_filter` | `get_default_filter()` | 无 | 无 | 无 | 无 | 低（10） |
| 18 | `agent/rate_limiter.py` | `_default_limiter` | `get_rate_limiter(name, **kwargs)` | ✅ | ✅ | 无 | 无 | 中 |

> 注：`get_alert_manager` 在 `alert_manager.py` 与 `performance.py` 中同名但属不同模块，迁移时单例名须区分（如 `alert_manager` / `performance_alert_manager`）。

## 优先级分级

### 🟢 高优先级 — 建议近期迁移（收益大、成本可控）

**1. `task_scheduler._scheduler`**
- 项目内引用最广的旧单例（19 处），无锁、无重置能力。
- 首次创建含预注册两个定时任务逻辑（周报/日志清理），工厂函数应承载该初始化。
- 测试 `test_task_scheduler_integration.py` 直接赋值 `module._scheduler = None`，迁移需同步补 reset 函数。

**2. `system_prompt_config._manager`**
- 全局配置查询入口（`is_section_enabled` 被 V2 功能广泛调用）。
- `tests/conftest.py` 与 `test_orchestrator_refactor.py` 均直接赋值 `_manager = None` 做测试隔离——正是 SingletonManager 可重置能力解决的核心痛点。

**3. `logging_utils` + `safe_logger` 的 `audit_logger` / `safety_monitor`**
- 同一类型（`AuditLogger` / `AgentSafetyMonitor`）在两个模块各建一份单例，**重复实现**。统一注册为 `audit_logger` / `safety_monitor` 单例后可共享实例、消除分叉。
- 无测试耦合，迁移风险低。

**4. `self_healer._self_healer`**
- 支持 config 注入（dict 通道可直接承载）、引用 10+ 处、测试 2 处直接赋值重置。
- 有真实资源生命周期（自愈动作/重启），cleanup 钩子有实际价值。

**5. `monitoring/search._performance_monitor`**
- `start_performance_monitor` / `stop_performance_monitor` 有明确资源生命周期，cleanup 钩子可自动停止监控。
- 引用 8+ 处，无测试耦合。

### 🟡 中优先级 — 可排期迁移

**6. `alert_notifier._alert_notifier`**：config 注入 + 通知资源；但测试 4 处直接赋值，迁移需同步改造（参考 chaos/performance_optimization 先例）。

**7. `alert_manager` / `alert_evaluator`**：均有 start/stop 生命周期，cleanup 收益；config_path/config 注入需求。

**8. `monitoring/performance._alert_manager`**：config 注入；测试 1 处直接赋值。

**9. `disaster_recovery`（2 单例）**：已有双检锁（线程安全达标），迁移收益主要是统一管理与备份提供者的清理钩子。

**10. `llm_monitor._monitor`**：`install_hooks` 有安装副作用，reset 需谨慎处理 hooks 卸载。

**11. `mcp_executor._executor`**：外部工具执行器，若存在连接资源可配 cleanup。

**12. `health_score._default_calculator`**：轻量无状态计算器，迁移成本低、收益一般。

### 🔴 低优先级 — 建议暂缓或不迁移

**13. `scheduling._schedule_scheduler`**：全项目仅 1 处引用，迁移性价比低。

**14. `rate_limiter._default_limiter`**：实为"命名注册表 + 默认缓存"（`_global_limiters` 多实例），与 SingletonManager 单实例语义不完全匹配，强行迁移需特殊设计。

**15. `tool_router_hybrid._hybrid_instance`**：已具备双重检查锁 + `reset_hybrid_retriever()`，规范度已达标，仅缺统一收口，收益有限。

**16. `utils/sensitive_data_filter._default_filter`**：纯函数辅助（`filter_sensitive_data`），无并发状态风险。

## 迁移成本预估

| 项 | 高优先级 5 模块 | 全部 18 模块 |
|----|-----------------|-------------|
| 新增 reset 函数 | 3 个（task_scheduler/system_prompt_config/self_healer，另 2 个无测试耦合可顺带补） | 约 10 个 |
| 测试改造 | 3 个文件（conftest、test_orchestrator_refactor、各集成测试） | 约 7 个文件 |
| 涉及生产代码 | 6 个文件（含 safe_logger 统一） | 18 个文件 |

## 建议

1. **第一阶段（高优先级）**：迁移 5 个模块，预计改动 6 个生产文件 + 3 个测试文件，即可消除引用最广、测试隔离痛点最集中的旧单例。
2. 中优先级模块在触碰相关功能时顺带迁移（避免为迁移而迁移）。
3. 低优先级模块维持现状，在迁移指南"例外与说明"中登记理由，防止后续重复评估。
