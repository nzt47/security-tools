# SingletonManager 高优先级迁移实施计划

> 日期：2026-08-08（更新 2026-08-09：追加中优先级 alert_notifier / alert_manager 完成记录与 alert_evaluator 示例）
> 依据：[迁移优先级评估报告](SingletonManager_Migration_Priority_Report.md) 高优先级 5 模块（task_scheduler / system_prompt_config / logging_utils+safe_logger / self_healer / monitoring.search）
> 关联：[迁移指南](SingletonManager_Migration_Guide.md) / [性能基准测试报告](SingletonManager_Performance_Report.md) / [阶段汇报](SingletonManager_Migration_Progress_Report.md)

## 执行状态（2026-08-09）

高优先级 **5 模块 + 中优先级 8 模块 + 低优先级修正收口 2 模块全部迁移完成**，SingletonManager 统一收口单例总数 **51 个**（本次迁移新增 19 个单例）。状态表如下：

| 模块 | 单例名 | 状态 | 验证 |
|------|--------|------|------|
| `agent/task_scheduler.py` | `task_scheduler` | ✅ 完成 | 单测 12 + 集成 114 通过 |
| `agent/system_prompt_config.py` | `system_prompt_manager` | ✅ 完成 | 单测 12 + 相关 75 通过 |
| `agent/logging_utils.py` + `safe_logger.py` | `audit_logger` / `safety_monitor` + 独立 2 个 | ✅ 完成（方案 B） | 单测 19 + 相关 242 通过 |
| `agent/monitoring/self_healer.py` | `self_healer` | ✅ 完成 | 单测 19 + 集成 100 通过 |
| `agent/monitoring/search.py` | `search_performance_monitor` | ✅ 完成 | 单测 15 + 相关 14 通过 |
| `agent/monitoring/alert_notifier.py` | `alert_notifier` | ✅ 完成（中优先级） | 单测 13 + 集成 82 通过 |
| `agent/monitoring/alert_manager.py` | `alert_manager` | ✅ 完成（中优先级） | 单测 19 通过（含既有 bug 修复） |
| `agent/monitoring/alert_evaluator.py` | `alert_evaluator` | ✅ 完成（中优先级） | 单测 23 通过 + 回归 45 通过 |
| `agent/monitoring/performance.py` | `performance_alert_manager` | ✅ 完成（中优先级） | 单测 22 + 既有 39 通过 |
| `agent/disaster_recovery.py` | `disaster_recovery` / `config_hot_reloader` | ✅ 完成（中优先级） | 单测 28 + 既有 90 通过 |
| `agent/llm_monitor.py` | `llm_monitor` | ✅ 完成（中优先级） | 单测 21 + 回归 92 通过 |
| `agent/mcp_executor.py` | `mcp_executor` | ✅ 完成（中优先级） | 单测 26 + 既有 58 通过 |
| `agent/health/health_score.py` | `health_score_calculator` | ✅ 完成（中优先级） | 单测 27 + 既有 229 通过 |

> ✅ 高优先级 5 模块 + 中优先级 8 模块全部完成（2026-08-09）

**alert_manager 迁移要点**（详细见 [阶段汇报](SingletonManager_Migration_Progress_Report.md)）：
- 工厂 `_create_alert_manager(config)`：config 走 `{"config_path": <str>}` 通道解包（仅当 dict 含该键才解包）。
- cleanup 钩子 `_cleanup_alert_manager(manager)` 调 `manager.stop()`（幂等）。
- getter 未初始化且传入 config_path 时走 `get_singleton("alert_manager", {"config_path": config_path})`。
- **顺带修复既有 bug**：`AlertManager.__init__` 原 L197 调用不存在的 `evaluator.set_on_alert_state_change`（应为 `set_on_state_change`），导致构造必然抛 AttributeError——删除无效调用，构造恢复可用。

剩余模块状态（详见 [迁移清单](SingletonManager_Migration_Checklist.md)）：
- 高优先级（5）+ 中优先级（8）+ 低优先级修正收口（2）共 **15 模块全部完成**。
- 维持暂缓 2 模块：`rate_limiter`（命名注册表语义不匹配）、`tool_router_hybrid`（已双检锁规范化）。

迁移细节与最终结论见 [SingletonManager_Migration_Completion_Report.md](SingletonManager_Migration_Completion_Report.md)。

## 一、通用迁移模板（6 步）

对每个模块按固定模式改造：

1. **保留 fallback 变量**：`_xxx = None` 保留（命名改为 `_global_xxx` 风格，注释 `# 保留作为 fallback`）。
2. **try/except 导入** SingletonManager API（`register_singleton` / `get_singleton` / `reset_singleton` / `is_initialized`），不可用时 `_SINGLETON_AVAILABLE = False` 回退旧实现。
3. **提取工厂函数** `_create_xxx(config=None)`：原 getter 内的创建逻辑（含初始化副作用，如预注册任务）移入工厂；config 字典解包。
4. **改造 getter**：优先 `get_singleton(name)`，fallback 走旧逻辑。
5. **新增 reset 函数** `reset_xxx()`：同时 `reset_singleton(name)` + 置空 fallback 变量（仅测试用）。
6. **getter 定义后注册**：`register_singleton(name, _create_xxx, cleanup_fn=...)`。

关键约束（沿用迁移指南）：
- 工厂必须是模块级 `def`，不能是 lambda 闭包。
- cleanup 钩子签名 `cleanup_fn(instance)`，须容错（reset 时实例可能未 start）。
- getter 公共签名与行为不变，向后兼容。

---

## 二、各模块实施步骤与代码示例

### 1. `agent/task_scheduler.py` — `get_scheduler()`

**现状**（[task_scheduler.py:420-443](file:///c:/Users/Administrator/agent/agent/task_scheduler.py#L420-L443)）：无锁、无重置能力、引用最广（19 处）；首次创建时预注册 2 个 cron 任务（周报 / 日志清理）；`TaskScheduler` 有 `start_daemon()` / `stop()`（[L342/L388](file:///c:/Users/Administrator/agent/agent/task_scheduler.py#L342)）。

**实施步骤**：工厂承载预注册任务初始化；cleanup 用 `stop()`（仅 `running` 时）；测试 `test_task_scheduler_integration.py` 的 `reset_scheduler_singleton` fixture 改用 reset 函数。

**代码示例**：

```python
# agent/task_scheduler.py 顶部（import 区之后）
_scheduler: Optional[TaskScheduler] = None  # 保留作为 fallback

try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = None


def _create_scheduler(config=None):
    """TaskScheduler 工厂（含预注册任务，供 SingletonManager 使用）"""
    sched = TaskScheduler()
    sched.add_cron_task(name="生成周报", func=generate_weekly_report,
                        day_of_week=0, hour=9, minute=0)
    sched.add_cron_task(name="清理旧日志", func=cleanup_old_logs,
                        day_of_week=None, hour=2, minute=0)
    return sched


def _cleanup_scheduler(sched):
    """清理钩子：停止调度器线程（仅测试重置时调用）"""
    if sched is not None and sched.running:
        sched.stop()


def get_scheduler() -> TaskScheduler:
    """获取调度器实例（单例）"""
    if _SINGLETON_AVAILABLE:
        return get_singleton("task_scheduler")
    global _scheduler
    if _scheduler is None:
        _scheduler = _create_scheduler()
    return _scheduler


def reset_scheduler():
    """重置调度器单例（仅用于测试）"""
    global _scheduler
    if _SINGLETON_AVAILABLE:
        reset_singleton("task_scheduler")
    _scheduler = None


if _SINGLETON_AVAILABLE:
    register_singleton("task_scheduler", _create_scheduler, cleanup_fn=_cleanup_scheduler)
```

> 注意：`register_singleton` 必须放在文件末尾（`generate_weekly_report` / `cleanup_old_logs` 定义之后），工厂体内引用在调用时才解析，无顺序问题。

**测试改造**（[test_task_scheduler_integration.py:51-58](file:///c:/Users/Administrator/agent/tests/integration/test_task_scheduler_integration.py#L51-L58)）：

```python
@pytest.fixture
def reset_scheduler_singleton():
    """重置全局 _scheduler 单例"""
    import agent.task_scheduler as module
    module.reset_scheduler()
    yield
    module.reset_scheduler()
```

---

### 2. `agent/system_prompt_config.py` — `get_manager()`

**现状**（[L541-548](file:///c:/Users/Administrator/agent/agent/system_prompt_config.py#L541-L548)）：无锁、无 reset；`tests/conftest.py:479` 与 `test_orchestrator_refactor.py:34/36` 直接赋值 `_manager = None` 做测试隔离。

**实施步骤**：标准模板；无清理钩子；将测试直接赋值改为调用 reset 函数。

**代码示例**：

```python
_manager: Optional[SystemPromptConfigManager] = None  # 保留作为 fallback

try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = None


def _create_manager(config=None):
    """SystemPromptConfigManager 工厂（供 SingletonManager 使用）"""
    return SystemPromptConfigManager()


def get_manager() -> SystemPromptConfigManager:
    if _SINGLETON_AVAILABLE:
        return get_singleton("system_prompt_manager")
    global _manager
    if _manager is None:
        _manager = _create_manager()
    return _manager


def reset_system_prompt_manager():
    """重置系统提示词配置管理器单例（仅用于测试）"""
    global _manager
    if _SINGLETON_AVAILABLE:
        reset_singleton("system_prompt_manager")
    _manager = None


if _SINGLETON_AVAILABLE:
    register_singleton("system_prompt_manager", _create_manager)
```

**测试改造**（conftest.py:477-481 与 test_orchestrator_refactor.py）：

```python
# conftest.py —— 替换直接赋值
import agent.system_prompt_config as _spc
_spc.reset_system_prompt_manager()
```

---

### 3. `agent/logging_utils.py` + `agent/log_system/safe_logger.py` — 重复实现消重

**现状**：两模块各定义了独立的 `AuditLogger` / `AgentSafetyMonitor` 类（类体一致，`safe_logger` 为副本，差异仅在日志 `module_name` 字段与审计文件路径），并各自维护一份单例。SingletonManager 是全局命名空间，**两模块不能注册同名单例**。

**方案 A（推荐，消重共享）**：`logging_utils` 作为主实现注册 `audit_logger` / `safety_monitor`，`safe_logger` 的 getter 委托共享单例。收益：消除重复实现；代价：safe_logger 侧日志 `module_name` 字段变为 `logging_utils`（需确认调用方不依赖该字段）。

**方案 B（保守，独立注册）**：`logging_utils` 注册 `audit_logger` / `safety_monitor`，`safe_logger` 注册 `safe_logger_audit_logger` / `safe_logger_safety_monitor`，两实例共存但各自被统一管理。收益：零语义变化；代价：重复类仍在。

**方案 A 代码示例**（logging_utils.py）：

```python
_audit_logger: Optional[AuditLogger] = None  # 保留作为 fallback
_safety_monitor: Optional[AgentSafetyMonitor] = None

try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = None


def _create_audit_logger(config=None):
    return AuditLogger()


def _create_safety_monitor(config=None):
    return AgentSafetyMonitor()


def get_audit_logger() -> AuditLogger:
    if _SINGLETON_AVAILABLE:
        return get_singleton("audit_logger")
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = _create_audit_logger()
    return _audit_logger


def get_safety_monitor() -> AgentSafetyMonitor:
    if _SINGLETON_AVAILABLE:
        return get_singleton("safety_monitor")
    global _safety_monitor
    if _safety_monitor is None:
        _safety_monitor = _create_safety_monitor()
    return _safety_monitor


def reset_audit_logger():
    """重置审计日志单例（仅用于测试）"""
    global _audit_logger
    if _SINGLETON_AVAILABLE:
        reset_singleton("audit_logger")
    _audit_logger = None


def reset_safety_monitor():
    """重置安全监控单例（仅用于测试）"""
    global _safety_monitor
    if _SINGLETON_AVAILABLE:
        reset_singleton("safety_monitor")
    _safety_monitor = None


if _SINGLETON_AVAILABLE:
    register_singleton("audit_logger", _create_audit_logger)
    register_singleton("safety_monitor", _create_safety_monitor)
```

safe_logger.py 委托（不再注册同名单例）：

```python
# safe_logger.py
def get_audit_logger() -> AuditLogger:
    if _SINGLETON_AVAILABLE:
        return get_singleton("audit_logger")  # 与 logging_utils 共享
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger
```

---

### 4. `agent/monitoring/self_healer.py` — `get_self_healer(config)`

**现状**（[L764-779](file:///c:/Users/Administrator/agent/agent/monitoring/self_healer.py#L764-L779)）：支持 config 参数、引用 10+ 处、测试 2 处直接赋值重置。参考 `performance_optimization` 迁移先例（对象包 dict 传入 + `is_initialized` 判空）。

**代码示例**：

```python
_self_healer: Optional[SelfHealer] = None  # 保留作为 fallback

try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton, is_initialized,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = is_initialized = None


def _create_self_healer(config=None):
    """SelfHealer 工厂（config 对象包 dict 传入，工厂解包）"""
    cfg = config.get("self_healer_config") if isinstance(config, dict) else config
    return SelfHealer(cfg)


def get_self_healer(config: Optional[Dict[str, Any]] = None) -> SelfHealer:
    if _SINGLETON_AVAILABLE:
        if config is not None and not is_initialized("self_healer"):
            return get_singleton("self_healer", {"self_healer_config": config})
        return get_singleton("self_healer")
    global _self_healer
    if _self_healer is None:
        _self_healer = _create_self_healer(config)
    return _self_healer


def reset_self_healer():
    """重置自愈管理器单例（仅用于测试）"""
    global _self_healer
    if _SINGLETON_AVAILABLE:
        reset_singleton("self_healer")
    _self_healer = None


if _SINGLETON_AVAILABLE:
    register_singleton("self_healer", _create_self_healer)
```

**测试改造**：`test_self_healer_integration.py:82/970` 的 `module._self_healer = None` 改为 `module.reset_self_healer()`。

---

### 5. `agent/monitoring/search.py` — `get_performance_monitor()`

**现状**（[L412-434](file:///c:/Users/Administrator/agent/agent/monitoring/search.py#L412-L434)）：`SearchPerformanceMonitor` 继承 `StopMixin` 有 `stop()`；模块级 `start_performance_monitor` / `stop_performance_monitor`；引用 8+ 处，无测试耦合。

**代码示例**：

```python
_performance_monitor: Optional[SearchPerformanceMonitor] = None  # 保留作为 fallback

try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = None


def _create_performance_monitor(config=None):
    """SearchPerformanceMonitor 工厂（供 SingletonManager 使用）"""
    return SearchPerformanceMonitor()


def _cleanup_performance_monitor(monitor):
    """清理钩子：停止性能监控线程"""
    if monitor is not None:
        try:
            monitor.stop()
        except Exception:
            pass


def get_performance_monitor() -> SearchPerformanceMonitor:
    if _SINGLETON_AVAILABLE:
        return get_singleton("search_performance_monitor")
    global _performance_monitor
    if _performance_monitor is None:
        _performance_monitor = _create_performance_monitor()
    return _performance_monitor


def reset_performance_monitor():
    """重置性能监控单例（仅用于测试）"""
    global _performance_monitor
    if _SINGLETON_AVAILABLE:
        reset_singleton("search_performance_monitor")
    _performance_monitor = None


if _SINGLETON_AVAILABLE:
    register_singleton("search_performance_monitor", _create_performance_monitor,
                       cleanup_fn=_cleanup_performance_monitor)
```

---

## 三、测试改造清单

| 模块 | 测试文件 | 改造点 |
|------|----------|--------|
| task_scheduler | `test_task_scheduler_integration.py` | `reset_scheduler_singleton` fixture 改 `reset_scheduler()` |
| system_prompt_config | `conftest.py:479`、`test_orchestrator_refactor.py:34/36` | 改 `reset_system_prompt_manager()` |
| self_healer | `test_self_healer_integration.py:82/970` | 改 `reset_self_healer()` |
| monitoring/search | — | 无直接赋值，仅验证 |
| logging_utils/safe_logger | 既有 audit/safety 相关测试 | 验证实例共享（方案 A） |

## 四、验证与回滚

**验证**：
1. `pytest tests/unit/test_singleton_manager.py tests/unit/test_singleton_performance.py -q`（26 项）。
2. 各模块集成测试 + `reset` 后弱引用 GC 回收断言（参考 `test_singleton_manager.py` 既有模式）。
3. 单模块提交后跑相关测试，全部通过再迁移下一模块。

**回滚**：每模块独立 commit；回滚仅 revert 该模块（fallback 变量始终保留，迁移后旧代码路径仍可运行）。

---

## 五、中优先级示例：`agent/monitoring/alert_evaluator.py` — `get_alert_evaluator()`（基于 alert_manager 经验）

> 说明：原请求提及 config_loader，但项目中实际对应 [p6_config_loader.py](file:///c:/Users/Administrator/agent/agent/p6_config_loader.py) 的 `P6ConfigLoader` 为**普通类（非单例）**，无需迁移；本节以真实存在的待迁移单例 `alert_evaluator` 作为示例（与 alert_manager 同属告警体系，迁移经验直接可复用）。

**现状**（[alert_evaluator.py:536-549](file:///c:/Users/Administrator/agent/agent/monitoring/alert_evaluator.py#L536-L549)）：模块级 `_alert_evaluator` + `get_alert_evaluator()`（无 config 参数、无锁、无 reset）；`AlertEvaluator` 有 `start()`/`stop()`（幂等，[L448/L469](file:///c:/Users/Administrator/agent/agent/monitoring/alert_evaluator.py#L448)）。被 [alert_manager.py:46-54](file:///c:/Users/Administrator/agent/agent/monitoring/alert_manager.py#L46-L54) 导入使用（`get_alert_evaluator` / `start_alert_evaluator`）。

**实施步骤**（沿用 alert_manager 经验）：
1. 工厂 `_create_alert_evaluator(config=None)`：getter 无 config 参数，工厂默认 `AlertEvaluator()`；若将来需要参数化，走 `{"evaluation_interval": ..., "pending_duration": ...}` 通道解包。
2. cleanup 钩子 `_cleanup_alert_evaluator(evaluator)` 调 `evaluator.stop()`（幂等）。
3. getter 签名不变：`get_alert_evaluator() -> AlertEvaluator`。
4. 新增 `reset_alert_evaluator()`（同时 `reset_singleton` + 置空 fallback）。
5. 文件末尾注册（在 `start_alert_evaluator` 定义之后）。

**代码示例**：

```python
# agent/monitoring/alert_evaluator.py 顶部（import 区之后）
_alert_evaluator: Optional[AlertEvaluator] = None  # 保留作为 fallback

try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = None


def _create_alert_evaluator(config=None):
    """AlertEvaluator 工厂（供 SingletonManager 使用）

    config 走 dict 通道（{"evaluation_interval"/"pending_duration"}），
    仅当 dict 含这些键才解包，否则用默认参数。
    """
    if isinstance(config, dict) and ("evaluation_interval" in config or "pending_duration" in config):
        return AlertEvaluator(**config)
    return AlertEvaluator()


def _cleanup_alert_evaluator(evaluator):
    """清理钩子：停止评估线程（仅测试重置时调用，stop 幂等）"""
    if evaluator is not None:
        evaluator.stop()


def get_alert_evaluator() -> AlertEvaluator:
    """获取全局告警评估器实例（单例）"""
    if _SINGLETON_AVAILABLE:
        return get_singleton("alert_evaluator")
    global _alert_evaluator
    if _alert_evaluator is None:
        _alert_evaluator = _create_alert_evaluator()
    return _alert_evaluator


def reset_alert_evaluator():
    """重置全局告警评估器单例（仅用于测试）"""
    global _alert_evaluator
    if _SINGLETON_AVAILABLE:
        reset_singleton("alert_evaluator")
    _alert_evaluator = None


# 注册单例工厂（置于文件末尾，确保 get_alert_evaluator / start_alert_evaluator 均已定义）
if _SINGLETON_AVAILABLE:
    register_singleton("alert_evaluator", _create_alert_evaluator,
                       cleanup_fn=_cleanup_alert_evaluator)
```

> 注意：与 alert_manager 不同，`get_alert_evaluator()` 无 config 参数，故 getter 无需 `is_initialized` 判断与通道传参；单例始终按默认参数构造。若后续 `start_alert_evaluator(evaluation_interval)` 需要参数化评估间隔，需在工厂中支持上述 dict 通道。

**测试改造**：`test_alert_evaluator` 相关测试若存在 `module._alert_evaluator = None` 直接赋值，改为 `module.reset_alert_evaluator()`（同 alert_manager 经验：迁移后 fallback 恒为 None，直接赋值无效）。
