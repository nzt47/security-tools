# 发布说明：SingletonManager 统一单例管理迁移完成

> 日期：2026-08-09 ｜ 分支：develop（`78b216f3` / `e53d6251` / `3f125385` 已推送）

---

## 📢 一句话摘要

项目 15 个模块的散落单例已统一收口到 SingletonManager，**51 个单例统一管理、299 项新增单元测试全部通过、零回归**。

## ✨ 关键变更

- **统一单例模式**：全项目单例统一为"双检锁 + 可重置 + config 注入 + cleanup 钩子"一套实现，每个模块保留向后兼容 fallback。
- **迁移范围**：高优先级 5 + 中优先级 8 + 低优先级复核收口 2，共 15 个模块（task_scheduler / system_prompt_config / logging_utils+safe_logger / self_healer / search / alert_notifier / alert_manager / alert_evaluator / performance / disaster_recovery / llm_monitor / mcp_executor / health_score / scheduling / sensitive_data_filter）。
- **顺带修复**：`AlertManager` 构造调用不存在方法的既有 bug（该模块此前从未成功构造）。
- **新增能力**：`llm_monitor` 增加 `uninstall_hooks()`，消除闭包悬空引用风险。

## 👥 对团队的影响

- **API 完全兼容**：各模块公共函数签名与行为不变，调用方零改动。
- **测试隔离变化**：迁移后测试重置统一使用各模块的 `reset_xxx()` 函数（直接给模块级变量赋 None 不再生效）。
- **新增测试资产**：15 个 `test_*_singleton.py` 文件（299 项）纳入常规回归。

## ⚠️ 注意事项

- 暂缓 2 个模块：`rate_limiter`（命名注册表语义）、`tool_router_hybrid`（已规范化），含备选方案文档，待团队评审。

## 📚 文档

- 技术复盘：[SingletonManager_Migration_Retrospective.md](SingletonManager_Migration_Retrospective.md)
- 迁移总结：[SingletonManager_Migration_Summary_Report.md](SingletonManager_Migration_Summary_Report.md)
- rate_limiter 方案对比：[rate_limiter_refactor_analysis.md](rate_limiter_refactor_analysis.md)
