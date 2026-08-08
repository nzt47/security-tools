# SingletonManager 迁移阶段性汇报

> 日期：2026-08-09
> 目的：团队同步迁移进度与关键经验
> 关联：[迁移实施计划](SingletonManager_Migration_Plan.md) / [迁移清单](SingletonManager_Migration_Checklist.md) / [完成报告](SingletonManager_Migration_Completion_Report.md)

---

## 一、整体进展

SingletonManager 统一收口迁移分三阶段推进，当前进度：

| 阶段 | 计划数 | 已完成 | 状态 |
|------|--------|--------|------|
| 高优先级 | 5 模块（7 单例） | 5 模块 | ✅ 全部完成 |
| 中优先级 | 8 模块 | 8 模块 | ✅ 全部完成（2026-08-09） |
| 低优先级 | 4 模块 | 2 模块（scheduling / sensitive_data_filter） | 🚧 复核后部分收口 |

统一收口单例总数 **51 个**（含本次迁移新增 19 个单例），核心测试 `test_singleton_manager.py` + `test_singleton_performance.py`（26 项）无回归。

---

## 二、本期重点：alert_manager 迁移（2026-08-08 完成）

### 2.1 迁移对象

- 模块：[agent/monitoring/alert_manager.py](file:///c:/Users/Administrator/agent/agent/monitoring/alert_manager.py)
- 旧模式：模块级全局变量 `_alert_manager = None` + 延迟初始化 getter，无锁、无重置能力
- 单例名：`alert_manager`

### 2.2 迁移方案要点

| 要素 | 实现 |
|------|------|
| 工厂 | `_create_alert_manager(config)`：config 走 `{"config_path": <str>}` 通道解包（**仅当 dict 含该键才解包**） |
| getter | `get_alert_manager(config_path)`：未初始化且传入 path 时走 `get_singleton("alert_manager", {"config_path": config_path})` |
| cleanup 钩子 | `_cleanup_alert_manager(manager)` 调 `manager.stop()`（幂等） |
| reset | `reset_alert_manager()`：同时 `reset_singleton` + 置空 fallback |
| 注册 | 文件末尾 `register_singleton("alert_manager", _create_alert_manager, cleanup_fn=...)` |
| 兼容 | `try/except ImportError` 导入 + `_alert_manager` fallback 变量保留，旧代码路径始终可运行 |

### 2.3 顺带修复的既有 Bug

**问题**：`AlertManager.__init__` 在初始化组件时调用 `evaluator.set_on_alert_state_change(...)`，而 `AlertEvaluator` 只有 `set_on_state_change` 方法——**该方法不存在，导致 AlertManager 构造必然抛 AttributeError，构造从未成功过**。

**原因**：observability 提交改名 `set_on_alert_state_change` → `set_on_state_change` 时漏改调用方。

**处理**：删除无效调用（保留 L183 正确的 `set_on_state_change` 回调），经确认后修复。构造恢复可用。

### 2.4 测试验证

新增 [tests/unit/test_alert_manager_singleton.py](file:///c:/Users/Administrator/agent/tests/unit/test_alert_manager_singleton.py)，5 个测试类共 **19 项全部通过**：

| 测试类 | 覆盖点 | 项数 |
|--------|--------|------|
| `TestAlertManagerSingleton` | 实例唯一性 / 注册到管理器 / config_path 首建生效 / 初始化后忽略 / reset / GC 释放 / 幂等 | 7 |
| `TestAlertManagerLifecycle` | start→stop 状态恢复 / start 幂等 / 模块级 start + 实例 stop / 未启动 stop 安全 | 4 |
| `TestAlertManagerCleanupHook` | reset 停止运行中实例 / 级联停止 evaluator+healer / 未启动 reset 安全 | 3 |
| `TestAlertManagerConcurrency` | 并发首建仅初始化一次（双检锁）/ 并发取同一实例 | 2 |
| `TestAlertManagerFallback` | fallback 单例性 / fallback 直传 config_path / fallback reset | 3 |

---

## 三、迁移经验沉淀（可复用）

1. **config 通道双形态区分**（反复踩坑后确立标准）：
   - SingletonManager dict 通道包 `{"<name>_config": 原配置}` / `{"config_path": ...}` → **工厂需解包**；
   - 直接传入的 dict/str/对象 → **原样传递**；
   - 判定标准：**仅当 dict 含特定键才解包**，否则一律原样。
2. **cleanup 钩子**签名 `cleanup_fn(instance)`，必须幂等安全（reset 时实例可能未 start）。
3. **测试陷阱**：
   - 替换 `module._create_xxx` 不生效（注册时已捕获函数引用）→ 需替换真实类计数构造次数；
   - 迁移后 fallback 恒为 None，测试 `module._xxx = None` 直接赋值无效 → 必须用 reset 函数。
4. **有 start/stop 或资源生命周期的模块**：cleanup 用 `stop()`；纯数据模块无 cleanup。
5. **工厂必须是模块级 `def`**，不能是 lambda 闭包；`register_singleton` 置于文件末尾（getter 定义之后）。

---

## 四、剩余模块与下一步

### 待迁移（维持暂缓 2 模块）

| 模块 | 单例 | 备注 |
|------|------|------|
| `rate_limiter` | — | 命名注册表语义不匹配，需 per-name 特殊设计 |
| `tool_router_hybrid` | — | 已双检锁 + reset 规范化，仅缺 register |

### 建议

1. **迁移全部收口（15/18 模块）**：高 5 + 中 8 + 低 2 已完成，累计新增单测 299 项（15 个文件实测）全部通过。
2. `rate_limiter` / `tool_router_hybrid` 维持暂缓——理由见评估报告与清单复核结论。
3. 后续如需全量收口，`rate_limiter` 可设计"按名注册子单例"方案单独评估。

---

## 五、风险与说明

- ⚠️ **历史状态修正**：此前清单中 `alert_manager / alert_evaluator` 合并勾选，实际仅 alert_manager 完成；2026-08-09 已补迁移 alert_evaluator（23 项单测通过），状态不一致已消除。
- **config_loader 说明**：原计划示例请求指向 config_loader，项目中实际对应 `p6_config_loader.py` 的 `P6ConfigLoader`（普通类，非单例），无需迁移。
- 迁移全过程保持向后兼容：fallback 变量始终保留，任意模块可独立回滚。
