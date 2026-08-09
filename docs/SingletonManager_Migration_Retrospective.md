# SingletonManager 迁移技术复盘

> 日期：2026-08-09 ｜ 状态：✅ 已归档
> 关联：[迁移总结报告](SingletonManager_Migration_Summary_Report.md) ｜ [迁移实施计划](SingletonManager_Migration_Plan.md) ｜ [迁移清单](SingletonManager_Migration_Checklist.md)
> Git：`78b216f3`（代码+测试）`e53d6251`（文档）

---

## 一、背景与目标

项目长期存在"模块级全局变量 + 延迟初始化"的散落单例实现，各模块自行维护锁与初始化逻辑，导致：重复实现、测试隔离困难（无法重置）、并发安全不统一。

**目标**：全部收口到 [`agent/utils/singleton_manager.py`](../agent/utils/singleton_manager.py)（统一双检锁、可重置、config 注入、cleanup 钩子），同时保留向后兼容 fallback。

**结果**：15 个模块迁移完成（高优先级 5 + 中优先级 8 + 低优先级修正收口 2），51 个单例统一管理，299 项新增单测全部通过，零回归。

---

## 二、迁移全流程

| 阶段 | 内容 | 关键产出 |
|------|------|---------|
| 0. 摸底 | 扫描全项目旧式单例，34 个已收口 + 18 个旧模式 | 优先级评估报告（高 5 / 中 8 / 低 4） |
| 1. 高优先级（5 模块） | task_scheduler / system_prompt_config / logging_utils+safe_logger / self_healer / search | 6 步模板 + 5 模块代码示例 |
| 2. 中优先级（8 模块） | alert_notifier / alert_manager / alert_evaluator / performance / disaster_recovery / llm_monitor / mcp_executor / health_score | 每模块独立迁移 + 单测 |
| 3. 低优先级复核 | 收口 scheduling + sensitive_data_filter；暂缓 rate_limiter + tool_router_hybrid | 复核结论 + 重构备选方案 |
| 4. 归档 | README / 总结报告 / 复盘 / wiki / git 收口 | 2 个 commit |

**迁移模板（6 步，最终定型）：**

```
1. 顶部 try/except ImportError 导入 register_singleton/get_singleton/reset_singleton
2. 保留 _xxx = None fallback 变量（向后兼容）
3. 新增 _create_xxx(config=None) 工厂（模块级 def，不能 lambda）
4. getter 优先 get_singleton(name)，fallback 走旧逻辑
5. 新增 reset_xxx()（同时 reset_singleton + 置空 fallback）
6. 文件末尾 register_singleton(name, _create_xxx, cleanup_fn=...)
```

---

## 三、遇到的坑与解决（核心复盘价值）

### 3.1 config 通道双形态误解包（self_healer）⚠️ 最具复用价值

- **现象**：self_healer 工厂用 `config.get("self_healer_config") if isinstance(config, dict)` 解包，fallback 直接传入的 `{"enabled": False}` 被误当通道包，配置全部丢失，首跑 3 项测试失败。
- **根因**：SingletonManager 的 dict 通道 `{"xxx_config": 原配置}` 与调用方直接传入的普通 dict 无法靠 `isinstance` 区分。
- **修复**：**仅当 dict 含特定键才解包**。
- **教训**：config 通道存在"双形态"，标准为——`isinstance(config, dict) and "xxx_config" in config` 才解包，否则原样传递。此标准在后续 8 个模块中复用。

### 3.2 alert_manager 构造既有 bug（迁移顺带发现）

- **现象**：18 项测试全失败，构造必抛 AttributeError。
- **根因**：`AlertManager.__init__` 调用不存在的 `evaluator.set_on_alert_state_change`（observability 提交改名 `set_on_state_change`），**该模块构造从未成功过**。
- **修复**：经用户确认删除无效调用。
- **教训**：迁移前对目标模块的**构造路径完整性**要先行验证，迁移测试可能暴露掩盖已久的故障。

### 3.3 测试 spy 替换陷阱（task_scheduler）

- **现象**：替换 `module._create_scheduler` 计数构造次数无效，总是 1。
- **根因**：SingletonManager 在 `register_singleton` 时**已捕获工厂函数引用**，之后替换模块属性不影响注册表。
- **修复**：替换真实类（`module.Scheduler = CountingScheduler`）计数。
- **教训**：单测 spy 必须替换**类**而非工厂函数。

### 3.4 测试直接赋值 fallback 变量无效（多模块）

- **现象**：`module._xxx = None` 重置无效。
- **根因**：迁移后 getter 优先走 `get_singleton`，模块 fallback 变量恒为 None，赋值无意义。
- **修复**：各模块补 `reset_xxx()`，测试改用 reset 函数。
- **教训**：迁移后测试隔离的**唯一正确入口是 reset 函数**。

### 3.5 线程收敛断言设计（alert_evaluator）

- **现象**：断言 stop 后线程退出失败——默认实例 `evaluation_interval=30s` 线程长 sleep，`stop()` 的 `join(timeout=5)` 超时。
- **修复**：线程收敛断言须用 SingletonManager 通道首建**小间隔实例**。
- **教训**：涉及后台线程的模块，测试要用短间隔参数化实例验证收敛。

### 3.6 方案 B 决策（logging_utils + safe_logger）

- **场景**：safe_logger 与 logging_utils 有同名单例（audit_logger / safety_monitor），方案 A 是共享实例，方案 B 是独立注册。
- **决策**：先查 module_name 依赖——无外部生产调用方依赖，但两模块类差异（action 命名、msg/message 字段、duration_ms）远超 module_name，共享实例会改变日志语义 → **选方案 B**。
- **教训**：合并决策看**语义等价性**，而非仅看字段名相似度。

### 3.7 外部副作用模块的 cleanup（llm_monitor）

- **现象**：install_hooks 替换 `LLMService` 三个类方法，原代码无卸载逻辑。
- **风险**：reset 后旧实例被 GC，但宿主类闭包仍悬空引用旧 monitor，行为不可预测。
- **修复**：模块级备份原始方法 + 新增 `uninstall_hooks()`（幂等），cleanup 钩子调用。
- **教训**：monkey-patch 宿主类方法的模块，cleanup 必须**恢复被补丁方法**。

### 3.8 单例名冲突（performance._alert_manager）

- **风险**：`performance.py` 内 getter 名 `get_alert_manager` 与 alert_manager 模块重名，若注册同名会实例共享。
- **修复**：注册名用 `performance_alert_manager` 区分。
- **教训**：**注册名 ≠ getter 名**，重名场景必须显式区分。

### 3.9 其他小坑

| 坑 | 修复 |
|----|------|
| search 测试 history_count 断言 `== 1` 失败（构造加载数据文件既有 100 条历史） | 改相对增量 `before + 1` |
| 测试假设 `stop_alert_manager` 模块级函数存在（实际只有实例方法 `stop()`） | 改 `start_alert_manager()` + `manager.stop()` |
| mcp_executor 测试写错属性名 `_initialized`（实际公开属性 `initialized`） | 修正属性名 |
| sensitive_data_filter `mask_ip` 断言 `192.168.1.xxx` 失败（实际掩后两段） | 修正断言 `192.168.xxx.xxx` |
| 低优先级 scheduling 引用数低估（报告 1 处，实际 code_tools.py 5 处 + 后台线程） | 复核后收口迁移，暂缓理由修正 |
| 双单例模块 disaster_recovery 需两个工厂/两个 cleanup | 分别注册 `disaster_recovery` / `config_hot_reloader` |

### 3.10 文档数字一致性（复盘教训）

- **现象**：总结报告写 296 项单测，实测 299；README 曾写"55 个单例"，脚本实测 51。
- **教训**：归档前**以实测数据为准**，统计类数字应通过工具验证（`pytest --collect-only` / 脚本统计注册名），不凭记忆汇总。

---

## 四、最终结果

| 指标 | 数值 |
|------|------|
| 迁移模块 | 15（高 5 + 中 8 + 低修正 2） |
| 统一单例 | 51 个（本次新增 19 个） |
| 新增单测 | 299 项（15 个文件，实测） |
| 核心回归 | test_singleton_manager + test_singleton_performance 26 项通过 |
| 顺带修复 | 1 个既有 bug（alert_manager 构造）+ 1 个新增能力（llm_monitor uninstall_hooks） |
| 暂缓模块 | 2（rate_limiter / tool_router_hybrid），附备选方案 |

---

## 五、经验沉淀（团队可复用清单）

1. **config 通道双形态**：`isinstance(config, dict) and "xxx" in config` 才解包。
2. **cleanup 钩子**：有资源生命周期的用 `stop()`（幂等）；无资源不注册。
3. **外部副作用模块**：cleanup 恢复被补丁的宿主方法（install/uninstall 对）。
4. **工厂必须模块级 def**，不能 lambda。
5. **测试隔离唯一入口是 reset 函数**；spy 替换类而非工厂。
6. **注册名 ≠ getter 名**，重名场景显式区分。
7. **文档数字以实测为准**，统计类数字工具验证。

## 六、后续建议

1. rate_limiter：按 [重构备选方案](rate_limiter_registry_refactor_draft.md) + [方案对比分析](rate_limiter_refactor_analysis.md) 评审后决定是否收口。
2. tool_router_hybrid：已规范化，仅在追求全量一致性时低成本补 register。
3. 本复盘与模板可沉淀为团队规范，供新模块单例化时参考。
