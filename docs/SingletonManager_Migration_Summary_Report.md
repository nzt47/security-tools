# SingletonManager 迁移总结报告

> 状态：**高优先级 5 模块 + 中优先级 8 模块 + 低优先级修正收口 2 模块 = 15 模块全部迁移完成**
> 完成时间：2026-08-09
> 关联文档：[迁移实施计划](SingletonManager_Migration_Plan.md) ｜ [迁移清单](SingletonManager_Migration_Checklist.md) ｜ [优先级评估](SingletonManager_Migration_Priority_Report.md) ｜ [阶段性汇报](SingletonManager_Migration_Progress_Report.md) ｜ [性能报告](SingletonManager_Performance_Report.md)

---

## 一、概述

本次迁移将项目中"模块级全局变量 + 延迟初始化"的旧式单例统一收口到 [`agent/utils/singleton_manager.py`](../agent/utils/singleton_manager.py)（双重检查锁定、线程安全、可重置、config 注入、cleanup 钩子）。每个模块保留 `try/except ImportError` 导入块与 fallback 变量，向后兼容；工厂函数（模块级 def）承载创建逻辑，getter 优先走 `get_singleton(name)`，文件末尾 `register_singleton(...)`。

- 统一收口单例总数：**51 个**（含本次迁移新增 19 个单例）
- 覆盖模块：**15 个**（高 5 + 中 8 + 低修正 2）
- 新增单测：**299 项**（15 个测试文件，实测收集），全部通过
- 核心回归：`test_singleton_manager.py` + `test_singleton_performance.py` 26 项通过

---

## 二、完成状态总表（15 模块）

| # | 模块 | 单例名 | 阶段 | 测试数据 |
|---|------|--------|------|---------|
| 1 | `agent/task_scheduler.py` | `task_scheduler` | 高优先级 | 新增 12 + 集成 114 通过 |
| 2 | `agent/system_prompt_config.py` | `system_prompt_manager` | 高优先级 | 新增 12 + 集成 75 通过 |
| 3 | `agent/logging_utils.py` | `audit_logger` / `safety_monitor` | 高优先级 | 新增 19（合并）+ 各既有 22/18/183 通过 |
| 4 | `agent/log_system/safe_logger.py` | `safe_logger_audit_logger` / `safe_logger_safety_monitor` | 高优先级（方案 B 独立注册） | 同上 |
| 5 | `agent/monitoring/self_healer.py` | `self_healer` | 高优先级 | 新增 19 + 集成 100 通过 |
| 6 | `agent/monitoring/search.py` | `search_performance_monitor` | 高优先级 | 新增 15 + 既有 14 通过 |
| 7 | `agent/monitoring/alert_notifier.py` | `alert_notifier` | 中优先级 | 新增 13 + 集成 82 通过 |
| 8 | `agent/monitoring/alert_manager.py` | `alert_manager` | 中优先级 | 新增 19 通过（含既有 bug 修复） |
| 9 | `agent/monitoring/alert_evaluator.py` | `alert_evaluator` | 中优先级 | 新增 23 + 回归 45 通过 |
| 10 | `agent/monitoring/performance.py` | `performance_alert_manager` | 中优先级 | 新增 22 + 既有 39 通过 |
| 11 | `agent/disaster_recovery.py` | `disaster_recovery` / `config_hot_reloader` | 中优先级 | 新增 28 + 既有 90 通过 |
| 12 | `agent/llm_monitor.py` | `llm_monitor` | 中优先级 | 新增 21 + 回归 92 通过 |
| 13 | `agent/mcp_executor.py` | `mcp_executor` | 中优先级 | 新增 26 + 既有 58 通过 |
| 14 | `agent/health/health_score.py` | `health_score_calculator` | 中优先级 | 新增 27 + 既有 229 通过 |
| 15 | `agent/scheduling.py` | `schedule_scheduler` | 低优先级修正收口 | 新增 20 + 既有 30 通过 |
| 16 | `agent/utils/sensitive_data_filter.py` | `sensitive_data_filter` | 低优先级修正收口 | 新增 20 通过 |

> 注：3/4 两项为方案 B（logging_utils 与 safe_logger 类语义差异过大，独立注册），合并计为高优先级第 3 模块；共 **16 行记录 / 15 个迁移批次**。

---

## 三、分阶段明细

### 🟢 高优先级（5 批次，完成于 2026-08 上旬）

| 批次 | 模块 | 迁移要点 |
|------|------|---------|
| 1 | task_scheduler | 工厂承载预注册周报/日志清理任务；cleanup `stop()`；心跳 `is_initialized` 修复 |
| 2 | system_prompt_config | 标准模板；测试隔离（`conftest.py`/orchestrator 测试改 reset 函数） |
| 3 | logging_utils + safe_logger | 方案 B：两模块类差异远超 module_name（action 命名、msg/message、duration_ms），独立注册保日志语义 |
| 4 | self_healer | config 通道解包修复（仅含 `self_healer_config` 键才解包）；cleanup stop 健康检查线程 |
| 5 | monitoring/search | cleanup `stop()` 容错；测试 history_count 改相对增量断言 |

### 🟡 中优先级（8 模块，完成于 2026-08-08 ~ 08-09）

| 模块 | 迁移要点 |
|------|---------|
| alert_notifier | config 注入；测试 4 处直接赋值同步改 reset |
| alert_manager | config_path 字符串通道；cleanup `stop()`；**顺带修复既有 bug**（见第四节） |
| alert_evaluator | 参数化工厂（evaluation_interval/pending_duration 通道）；cleanup `stop()`；线程收敛断言须用通道首建小间隔实例 |
| performance._alert_manager | 单例名 `performance_alert_manager`（与 alert_manager 区分）；无 start/stop 纯检查类 → 无 cleanup |
| disaster_recovery | 双单例（`disaster_recovery` / `config_hot_reloader`）；cleanup 分别 `stop_backup_scheduler()` / `stop()` |
| llm_monitor | **新增 `uninstall_hooks()`**：install_hooks 替换 LLMService 方法，cleanup 卸载防闭包悬空引用 |
| mcp_executor | config 通道 `default_timeout`；`_clients` 为内存模拟连接池 → 无 cleanup |
| health_score | 单例名 `health_score_calculator`；config 通道 `weights`；无状态 → 无 cleanup |

### 🔴 低优先级复核修正（2 模块，2026-08-09）

| 模块 | 复核结论 | 迁移要点 |
|------|---------|---------|
| scheduling | 原"仅 1 处引用"低估（实际 code_tools.py 5 处生产调用）且含后台线程 | 单例名 `schedule_scheduler`；cleanup `stop()`（幂等：置标志 + 持久化） |
| utils/sensitive_data_filter | 纯函数无状态，迁移成本极低 | 单例名 `sensitive_data_filter`；无 cleanup；`__all__` 补充 reset 导出 |

---

## 四、问题修复记录（迁移中顺带修复）

| # | 问题 | 修复 | 来源 |
|---|------|------|------|
| 1 | `AlertManager.__init__` 调用不存在的 `evaluator.set_on_alert_state_change`（observability 改名导致），**构造必然抛 AttributeError，从未成功** | 删除无效调用（保留 `set_on_state_change`），构造恢复可用 | alert_manager（用户确认） |
| 2 | self_healer 工厂误解包：fallback 直接传入的 dict 配置被当通道包解包 → 配置丢失 | 仅当 dict 含 `self_healer_config` 键才解包 | self_healer（首跑 3 项失败暴露） |
| 3 | search 测试 history_count 断言：从数据文件加载既有历史，`== 1` 失败 | 改相对增量 `before + 1` | monitoring/search |
| 4 | 测试 spy 替换陷阱：替换 `module._create_xxx` 不生效（注册时已捕获引用） | 替换真实类计数构造次数 | task_scheduler |
| 5 | 迁移后 fallback 变量恒为 None，测试直接赋值无效 | 各模块补 reset 函数并改测试 | 多模块 |
| 6 | 线程收敛断言失败：默认间隔实例 stop 的 join 超时后线程未退出 | 用 SingletonManager 通道首建小间隔实例 | alert_evaluator |

---

## 五、测试数据汇总

### 新增长期单测资产（15 个文件，299 项，全量实测通过）

| 测试文件 | 项数 | 重点覆盖 |
|---------|------|---------|
| `test_task_scheduler_singleton.py` | 12 | 重置 / 并发 / fallback / 心跳 |
| `test_system_prompt_config_singleton.py` | 12 | 测试隔离 / 重置 / 并发 |
| `test_audit_safety_logging_singleton.py` | 19 | 日志模块名 / 实例共享 |
| `test_self_healer_singleton.py` | 19 | 自愈逻辑 / 异常恢复 |
| `test_search_performance_monitor_singleton.py` | 15 | 状态恢复 / 并发 |
| `test_alert_notifier_singleton.py` | 13 | config 注入 / 发送链路 |
| `test_alert_manager_singleton.py` | 19 | start/stop 生命周期 / cleanup 钩子 |
| `test_alert_evaluator_singleton.py` | 23 | 生命周期 / 线程收敛 / 通道 |
| `test_performance_alert_manager_singleton.py` | 22 | config 驱动行为 / 冷却抑制 |
| `test_disaster_recovery_singleton.py` | 28 | 备份 / restore / cleanup 线程 |
| `test_llm_monitor_singleton.py` | 21 | **hooks 安装与卸载 / 闭包悬空防护** |
| `test_mcp_executor_singleton.py` | 26 | 工具执行 / 异常处理 |
| `test_health_calculator_singleton.py` | 27 | 计算逻辑 / 边界条件 |
| `test_schedule_scheduler_singleton.py` | 20 | 生命周期 / cleanup stop |
| `test_sensitive_data_filter_singleton.py` | 20 | 脱敏功能 / 单例 |

### 回归验证

- 核心：`test_singleton_manager.py` + `test_singleton_performance.py` **26 项通过**（1.70s）
- 全量回归：12714 通过（排除 Windows C 扩展崩溃相关文件后）
- 各模块迁移批次均完成"新测试 + 相关既有测试"双重验证（见总表测试数据列）

---

## 六、可复用经验（沉淀为模板）

1. **config 通道双形态**：SingletonManager dict 通道（`{"xxx_config": 原配置}`）需解包；直接传入的 dict/str/对象原样传递。**仅当 dict 含特定键才解包**——此坑在 self_healer 首跑失败后定为标准。
2. **cleanup 钩子**：签名 `cleanup_fn(instance)`，须幂等安全；有 start/stop 或资源生命周期的模块用 `stop()`；**无资源生命周期则不注册**（performance._alert_manager / mcp_executor / health_score / sensitive_data_filter）。
3. **外部副作用模块**：monkey-patch 宿主类方法的模块（llm_monitor）cleanup 必须**恢复被补丁的方法**，否则闭包悬空引用导致行为不可预测——新增 `uninstall_hooks()` 模式。
4. **工厂必须是模块级 def**：不能 lambda 闭包（SingletonManager 注册时捕获引用）。
5. **测试隔离**：迁移后测试直接赋值 fallback 变量无效（恒为 None），必须用 reset 函数；reset 触发 cleanup，天然恢复现场。

---

## 七、剩余模块与决策

### 维持暂缓（2 模块）

| 模块 | 暂缓理由 |
|------|---------|
| `rate_limiter` | 命名注册表语义（`_global_limiters` 按名缓存多实例）与 SingletonManager 单实例语义不匹配，强行迁移需 per-name 特殊设计 |
| `tool_router_hybrid` | 已双检锁 + `reset_hybrid_retriever()` + 构造异常容错，规范度已达标，仅缺 register，收益有限 |

### 后续建议

1. 如需全量收口，`rate_limiter` 可单独设计"按名注册子单例"方案评估。
2. 迁移模板与经验可沉淀为团队规范（见实施计划第一节 6 步模板）。
3. 低优先级剩余 2 模块维持现状，理由已在评估报告登记，防止重复评估。
