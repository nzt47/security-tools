# SingletonManager 高优先级迁移完成报告

> 日期：2026-08-08
> 范围：迁移优先级评估报告中的**高优先级 5 模块**（task_scheduler / system_prompt_config / logging_utils+safe_logger / self_healer / monitoring.search）
> 结果：全部迁移完成，SingletonManager 统一收口单例总数 **34 → 39**

## 一、迁移总览

| 模块 | 新增单例 | 核心改动 | 验证结果 |
|------|----------|----------|----------|
| `agent/task_scheduler.py` | `task_scheduler` | 工厂承载预注册任务、cleanup 钩子 stop、心跳检查不再触发创建 | 单测 12 + 集成 114 通过 |
| `agent/system_prompt_config.py` | `system_prompt_manager` | 标准模板、`reset_system_prompt_manager()` 替换 conftest 直接赋值 | 单测 12 + 相关 75 通过 |
| `agent/logging_utils.py` | `audit_logger`、`safety_monitor` | 注册 2 单例 + reset 函数 | 单测 19 + 相关 242 通过 |
| `agent/log_system/safe_logger.py` | `safe_logger_audit_logger`、`safe_logger_safety_monitor` | **方案 B 独立注册**（日志格式差异） | 同上 |
| `agent/monitoring/self_healer.py` | `self_healer` | config 通道解包修复、cleanup 钩子 stop | 单测 19 + 集成 100 通过 |
| `agent/monitoring/search.py` | `search_performance_monitor` | 标准模板、cleanup 钩子 stop（容错） | 单测 15 + 既有 14 通过 |

本次共新增 **7 个单例**（logging_utils + safe_logger 合计 4 个），核心回归 `test_singleton_manager.py` + `test_singleton_performance.py` **26 项通过**（1.70s）。

## 二、各模块迁移细节

### 1. task_scheduler — 引用最广的旧单例

- **单例定义**：`_scheduler: Optional[TaskScheduler] = None`，getter 无锁、无重置能力，项目内引用 19 处。
- **创建副作用**：首次调用预注册 2 个 cron 任务（周报/日志清理）——整体移入 `_create_scheduler()` 工厂。
- **cleanup 钩子**：`_cleanup_scheduler()` 仅在 `running` 时 `stop()`，避免误停。
- **附加修复**：`perform_heartbeat_check()` 原直接读 `_scheduler` fallback（迁移后恒为 None 导致心跳永远报 stopped），改为 `is_initialized` 判空读取真实单例，不触发创建。
- **测试改造**：`reset_scheduler_singleton` fixture 原保存/恢复 `module._scheduler`（迁移后无效），改用 `reset_scheduler()`。
- **测试陷阱**：替换 `module._create_scheduler` 不生效（SingletonManager 注册时已捕获函数引用），单测改用替换 `TaskScheduler` 类计数构造次数。

### 2. system_prompt_config — 测试隔离痛点

- **单例定义**：`_manager: Optional[SystemPromptConfigManager] = None`，无锁、无 config、无清理需求。
- **核心痛点**：`tests/conftest.py:479` 与 `test_orchestrator_refactor.py:34/36` 直接赋值 `_manager = None`——SingletonManager 可重置能力的最典型应用场景。
- **迁移收益**：`reset_system_prompt_manager()` 后新实例重新加载配置，解决陈旧配置缓存问题（`load()` 带缓存，重置前返回旧值、重置后返回新值）。
- **测试改造**：conftest 与 orchestrator_refactor 两处直接赋值改为调用 reset 函数。

### 3. logging_utils + safe_logger — 方案 B（独立注册）

- **决策过程**：检查调用方发现 safe_logger 的 `get_audit_logger` / `get_safety_monitor` 无外部生产调用方（唯一引用 `handlers.py` 仅导入 `SensitiveDataFilter` 类）。但两模块类**差异远超 module_name**：
  - `action` 命名格式：`logging_utils.log_config_access.config_access` vs `config_access.user.user`
  - 消息字段名：`message` vs `msg`；`duration_ms` 有无
- **结论**：方案 A（共享实例）会把 safe_logger 侧日志整体变成 logging_utils 格式，语义不可控；且两模块类并非真正重复（格式不同），消重收益不成立。采用**方案 B**：各自独立注册唯一命名。
- **迁移收益**：统一收口 + 可重置 + 线程安全，零语义变化。

### 4. self_healer — config 通道缺陷修复

- **单例定义**：`_self_healer: Optional[SelfHealer] = None`，getter 带 `config` 参数（配置注入需求最典型模块）。
- **迁移缺陷（已修复）**：原工厂 `config.get("self_healer_config") if isinstance(config, dict)` 会把 fallback 路径直接传入的 dict 配置（`{"enabled": False}`）误当通道包解包 → 配置丢失。修复：**仅当 dict 含 `"self_healer_config"` 键才解包**。
- **cleanup 钩子**：`_cleanup_self_healer()` 仅 `_running` 时 `stop()`。
- **测试改造**：集成测试 2 处直接赋值改为 `reset_self_healer()`。

### 5. monitoring/search — 生命周期收口

- **单例定义**：`_performance_monitor: Optional[SearchPerformanceMonitor] = None`，`SearchPerformanceMonitor(StopMixin)` 带 daemon 监控线程。
- **cleanup 钩子**：`_cleanup_performance_monitor()` 调 `stop()`（try/except 容错，reset 时可能未 start）。
- **附带确认**：`agent/search_performance_monitor.py` 为薄包装（re-export），迁移自动覆盖；`test_search_performance_monitor.py` 中 4 处 `global _performance_monitor = None` 操作的是测试模块自身局部变量（未导入该符号），无副作用。
- **状态恢复**：start/stop 状态往返、stop 后重启（TLM-AUDIT-002）、重置后干净状态。

## 三、测试结果汇总

| 测试批次 | 结果 |
|----------|------|
| 核心回归：`test_singleton_manager.py` + `test_singleton_performance.py` | **26 通过**（1.70s） |
| task_scheduler：新单测 12 + 集成 114 | **126 通过** |
| system_prompt_config：新单测 12 + orchestrator_refactor 75 | **87 通过** |
| logging_utils/safe_logger：新单测 19 + 既有 40 | **59 通过** |
| 依赖方回归：digital_life/snapshot/lifecycle_manager | **183 通过** |
| self_healer：新单测 19 + 集成 100 + alert/import_smoke 88 | **207 通过，2 跳过** |
| monitoring/search：新单测 15 + 既有 14 | **29 通过** |

**合计：本次新增单测 77 项，涉及回归验证约 700 项测试通过，无回归。**

## 四、迁移中发现的问题与修复

| # | 问题 | 修复 |
|---|------|------|
| 1 | task_scheduler 心跳检查读取 fallback（迁移后恒 None）→ 心跳永远报 stopped | `is_initialized` 判空读取真实单例，不触发创建 |
| 2 | self_healer 工厂将 fallback 直接传入的 dict 配置误当通道包解包 → 配置丢失 | 仅当 dict 含 `self_healer_config` 键才解包 |
| 3 | 各模块测试直接赋值 fallback 变量（迁移后重置无效） | 补充 reset 函数并改造测试（conftest / orchestrator_refactor / task_scheduler / self_healer 集成） |
| 4 | 测试 spy 替换 `module._create_xxx` 不生效（SingletonManager 已捕获函数引用） | 改用替换真实类计数构造次数 |

## 五、性能影响

沿用 [性能基准测试报告](SingletonManager_Performance_Report.md) 结论：新模式耗时约为旧模式 2-4 倍（首次创建 1.931us vs 0.537us），但绝对开销为微秒级；内存每单例约 0.62KB 管理结构。按 39 个单例估算总开销约 **24 KB**，占进程内存可忽略。换取统一双检锁、可重置、config 注入与清理钩子能力。

## 六、遗留项与建议

- 剩余 **13 个中/低优先级模块**（约 14 个单例变量）仍为旧式单例：alert_notifier / alert_manager / alert_evaluator / monitoring.performance / disaster_recovery / llm_monitor / mcp_executor / health_score / scheduling / rate_limiter / tool_router_hybrid / sensitive_data_filter / logging 相关缓存。
- 建议按 [迁移优先级评估报告](SingletonManager_Migration_Priority_Report.md) 中优先级顺序分批推进；中优先级模块大多为纯单例（无 config/cleanup 需求），可直接套用标准模板。
- 迁移后统一运行全量回归（注意 Windows C 扩展 0xC0000005 崩溃规避：`OMP_NUM_THREADS=4` + `MKL_NUM_THREADS=4` + `DISABLE_NATIVE_EXT=1`）。

---

相关文档：[迁移指南](SingletonManager_Migration_Guide.md) / [实施计划](SingletonManager_Migration_Plan.md) / [优先级评估报告](SingletonManager_Migration_Priority_Report.md) / [性能基准测试报告](SingletonManager_Performance_Report.md) / [待办清单](SingletonManager_Migration_Checklist.md)
