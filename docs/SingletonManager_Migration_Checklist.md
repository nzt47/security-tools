# SingletonManager 迁移待办事项清单

> 来源：[迁移优先级评估报告](SingletonManager_Migration_Priority_Report.md) 高优先级模块
> 实施步骤与代码示例见：[迁移实施计划](SingletonManager_Migration_Plan.md)
> **归档状态（2026-08-09）**：✅ 高优先级 5 + 中优先级 8 + 低优先级修正收口 2 = **15 模块全部完成**；剩余 2 模块（rate_limiter / tool_router_hybrid）维持暂缓，理由见评估报告与[重构草稿](rate_limiter_registry_refactor_draft.md)。

## 🟢 高优先级（建议近期完成）

### 1. `agent/task_scheduler.py` — `get_scheduler()`
- [x] 添加 `try/except ImportError` 导入 SingletonManager API
- [x] 新增 `_create_scheduler()` 工厂（承载预注册周报/日志清理任务）
- [x] 新增 `_cleanup_scheduler()` 清理钩子（`running` 时 `stop()`）
- [x] 改造 `get_scheduler()` 优先走 `get_singleton("task_scheduler")`
- [x] 新增 `reset_scheduler()` 并注册单例
- [x] 改造 `test_task_scheduler_integration.py` 的 `reset_scheduler_singleton` fixture
- [x] 运行 `test_task_scheduler_integration.py` 验证（114 通过）+ 新增 `test_task_scheduler_singleton.py`（12 项：重置/并发/fallback/心跳）全部通过

### 2. `agent/system_prompt_config.py` — `get_manager()`
- [x] 添加 SingletonManager API 导入
- [x] 新增 `_create_manager()` 工厂
- [x] 改造 `get_manager()` 优先走 `get_singleton("system_prompt_manager")`
- [x] 新增 `reset_system_prompt_manager()`
- [x] `tests/conftest.py:479` 改为调用 `reset_system_prompt_manager()`
- [x] `test_orchestrator_refactor.py:34/36` 改为调用 reset 函数
- [x] 运行 `test_orchestrator_refactor.py`（75 通过）+ 新增 `test_system_prompt_config_singleton.py`（12 项：重置/隔离/并发/fallback）全部通过

### 3. `logging_utils.py` + `safe_logger.py` — audit_logger / safety_monitor 消重
- [x] 确定方案：**方案 B（独立注册）**——分析发现两模块类差异远超 module_name（action 命名、msg/message 字段、duration_ms 均不同），共享实例会改变 safe_logger 日志语义
- [x] `logging_utils.py` 注册 `audit_logger` / `safety_monitor` 单例
- [x] `safe_logger.py` 独立注册 `safe_logger_audit_logger` / `safe_logger_safety_monitor`
- [x] 确认调用方：safe_logger 单例无外部生产调用方（仅 handlers.py 导入 SensitiveDataFilter 类）
- [x] 运行测试：新增 `test_audit_safety_logging_singleton.py`（19 项）+ logging_utils（22）+ safe_logger（18）+ digital_life/snapshot/lifecycle（183）全部通过

### 4. `agent/monitoring/self_healer.py` — `get_self_healer(config)`
- [x] 添加 SingletonManager API 导入（含 `is_initialized`）
- [x] 新增 `_create_self_healer()` 工厂（config 通道解包 + 直接 dict 配置区分）
- [x] 改造 `get_self_healer()` 走 `get_singleton("self_healer")`（未初始化时传 config）
- [x] 新增 `reset_self_healer()` + `_cleanup_self_healer()` 清理钩子（stop 健康检查线程）
- [x] `test_self_healer_integration.py:82/970` 改为调用 reset 函数
- [x] 运行 `test_self_healer_integration.py`（100 通过）+ 新增 `test_self_healer_singleton.py`（19 项：自愈逻辑/异常恢复/重置/并发/fallback）+ alert/import_smoke（88）全部通过

### 5. `agent/monitoring/search.py` — `get_performance_monitor()`
- [x] 添加 SingletonManager API 导入
- [x] 新增 `_create_performance_monitor()` 工厂
- [x] 新增 `_cleanup_performance_monitor()` 清理钩子（`stop()` 容错）
- [x] 改造 `get_performance_monitor()` 走 `get_singleton("search_performance_monitor")`
- [x] 新增 `reset_performance_monitor()`
- [x] 运行 `test_search_performance_monitor.py`（14 通过）+ 新增 `test_search_performance_monitor_singleton.py`（15 项：状态恢复/并发/fallback）全部通过

### 全量收尾（5 模块完成后）
- [x] 运行 `test_singleton_manager.py` + `test_singleton_performance.py`（26 项通过，1.70s）
- [x] 更新 [迁移指南](SingletonManager_Migration_Guide.md) 已迁移清单（34 → 39 单例）
- [x] 更新 [迁移优先级报告](SingletonManager_Migration_Priority_Report.md) 状态（已完成节标注）
- [x] 生成 [迁移完成报告](SingletonManager_Migration_Completion_Report.md)
- [x] 更新 README 统一单例管理章节（39 单例 + 完成报告链接）

## 🟡 中优先级（触碰相关功能时顺带迁移）
- [x] `alert_notifier`（config 注入；测试 4 处直接赋值已同步改）✅ 2026-08-08 迁移完成
- [x] `alert_manager`（start/stop 生命周期 + cleanup 钩子）✅ 2026-08-08 迁移完成（顺带修复既有 bug：AlertManager 构造调用不存在的 evaluator 方法 `set_on_alert_state_change`，构造从未成功；删除无效调用后 19 项单测通过）
- [x] `alert_evaluator`（独立单例 `_alert_evaluator` + `get_alert_evaluator()`，无 config 参数、有 start/stop）✅ 2026-08-09 迁移完成（cleanup 钩子 stop 幂等；新增 `test_alert_evaluator_singleton.py` 23 项通过，回归 45 项无回归）
- [x] `monitoring/performance._alert_manager`（config 注入；测试 1 处直接赋值已改 reset 函数）✅ 2026-08-09 迁移完成（单例名 `performance_alert_manager` 与 alert_manager 区分；无 start/stop → 无 cleanup；新增 `test_performance_alert_manager_singleton.py` 22 项 + 既有 39 项通过）
- [x] `disaster_recovery`（2 单例 `disaster_recovery` / `config_hot_reloader`，已有双检锁）✅ 2026-08-09 迁移完成（cleanup 钩子分别 stop_backup_scheduler / stop，幂等；新增 `test_disaster_recovery_singleton.py` 28 项 + 既有 90 项通过）
- [x] `llm_monitor`（hooks 副作用：install_hooks 替换 LLMService 方法）✅ 2026-08-09 迁移完成（新增 `uninstall_hooks()` 恢复原始方法，cleanup 钩子卸载 hooks 防闭包悬空；新增 `test_llm_monitor_singleton.py` 21 项 + 回归 92 项通过）
- [x] `mcp_executor`（外部工具执行器）✅ 2026-08-09 迁移完成（工厂 config 通道 default_timeout；`_clients` 为内存模拟连接池 → 无 cleanup；新增 `test_mcp_executor_singleton.py` 26 项 + 既有 58 项通过）
- [x] `health_score._default_calculator`（轻量无状态）✅ 2026-08-09 迁移完成（单例名 `health_score_calculator`；工厂 config 通道 weights；无 cleanup；新增 `test_health_calculator_singleton.py` 27 项 + 既有 229 项通过）

### 中优先级全部完成 ✅ 2026-08-09（8/8 模块收口）

## 🔴 低优先级（复核后部分收口，2026-08-09）
- [x] `scheduling`（复核修正：实际 5 处生产调用 + 后台线程）✅ 迁移完成（单例名 `schedule_scheduler`；cleanup stop 幂等；新增 `test_schedule_scheduler_singleton.py` 20 项 + 既有 30 项通过）
- [x] `utils/sensitive_data_filter`（纯函数无状态）✅ 迁移完成（单例名 `sensitive_data_filter`；无 cleanup；新增 `test_sensitive_data_filter_singleton.py` 20 项通过）
- [ ] `rate_limiter`（命名注册表语义不匹配，需 per-name 特殊设计——**维持暂缓**）
- [ ] `tool_router_hybrid`（已双检锁 + reset 规范化——**维持暂缓**）

> 📌 复核结论：原 4 个暂缓模块中 `scheduling` 引用数被低估（1→5 处）且含后台线程，`sensitive_data_filter` 迁移成本极低，故收口迁移；`rate_limiter` / `tool_router_hybrid` 维持暂缓。
