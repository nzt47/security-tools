# SingletonManager 高优先级迁移总结 — PPT 大纲

> 用途：向团队汇报 2026-08 SingletonManager 高优先级迁移成果
> 演讲时长建议：15-20 分钟 | 数据来源：[迁移完成报告](SingletonManager_Migration_Completion_Report.md)

## P1 封面
- 标题：统一单例管理（SingletonManager）高优先级迁移总结
- 副标题：34 → 39 单例统一收口，消除重复实现与测试隔离痛点
- 日期 / 汇报人 / 版本

## P2 背景：40+ 处重复单例模板
- 迁移前每个模块各自实现"全局变量 + get_xxx() 延迟初始化"，问题：
  - 重复实现：每个模块重复编写模板代码
  - 线程安全不一致：部分无锁，存在并发初始化竞争
  - 测试隔离困难：单例状态无法重置，测试间相互污染
  - 配置灵活性与清理钩子缺失
- 演示建议：左侧列旧代码模式，右侧列问题清单

## P3 方案：SingletonManager 统一 API
- `agent/utils/singleton_manager.py`：register / get / reset / is_initialized
- 核心能力：双重检查锁定（线程安全）、RLock 可重入、config 注入、cleanup 钩子
- 兼容策略：`try/except ImportError` 导入 + fallback 变量保留（向后兼容）
- 代码示例：旧模式 → 新模式（工厂 + getter + reset + 注册）

## P4 迁移范围总览
- 高优先级 5 模块（6 文件）全部完成，新增 7 单例
- 表格：模块 / 单例名 / 核心改动 / 验证结果
- 强调：公共 API 签名与行为不变

## P5 模块详情 1：task_scheduler（引用最广）
- 预注册 cron 任务移入工厂；cleanup 钩子 stop
- 附加修复：心跳检查不再误读 fallback
- 验证：单测 12 + 集成 114

## P6 模块详情 2：system_prompt_config（测试隔离痛点）
- conftest / orchestrator_refactor 直接赋值改为 reset 函数
- 收益：reset 后新实例重新加载配置，解决陈旧配置缓存
- 验证：单测 12 + 相关 75

## P7 模块详情 3：logging_utils + safe_logger（方案 B 决策）
- 检查发现两模块类差异远超 module_name（action 命名 / msg vs message / duration_ms）
- 方案 A（共享实例）会改变日志语义 → 采用方案 B 独立注册
- 验证：单测 19 + 相关 242

## P8 模块详情 4：self_healer（config 通道缺陷）
- 工厂解包修复：区分 SingletonManager dict 通道与直接 dict 配置
- cleanup 钩子：仅 running 时 stop
- 验证：单测 19 + 集成 100

## P9 模块详情 5：monitoring/search（生命周期收口）
- cleanup 钩子 stop（容错）；start/stop 状态往返 + 重启支持
- 验证：单测 15 + 既有 14

## P10 测试与质量保障
- 新增单测 77 项，回归验证约 700 项全部通过，无回归
- 核心回归：test_singleton_manager + test_singleton_performance 26 项
- 全量集成回归 12714 通过（排除 Windows C 扩展崩溃文件——项目已知问题）
- 覆盖重点：重置（GC 回收）、并发（8 线程只构造一次）、fallback、日志格式

## P11 性能影响
- 耗时：首次创建 1.93 us（旧 0.54 us）、重复获取 0.13 us（旧 0.06 us）—— 微秒级可忽略
- 内存：每单例 +0.62 KB 管理结构，39 单例 ≈ 24 KB
- 换取能力：双检锁 / 可重置 / config 注入 / 清理钩子
- 演示建议：放 Mermaid 对比图表

## P12 经验教训（关键）
1. 测试直接赋值 fallback 变量在迁移后无效 → 必须补 reset 函数并同步改测试
2. 工厂 config 解包需区分通道包与直接配置（self_healer / alert_notifier 两次踩坑）
3. 测试 spy 替换 `_create_xxx` 不生效（注册时已捕获引用）→ 替换真实类
4. 方案选择以语义安全为先：logging_utils/safe_logger 放弃消重选方案 B

## P13 后续计划
- 剩余 13 个中/低优先级模块：alert_manager / disaster_recovery / llm_monitor / mcp_executor 等
- 建议触碰相关功能时顺带迁移（避免为迁移而迁移）
- 待办清单与优先级报告已就绪

## P14 Q&A
- 预留讨论时间
- 附：文档索引（迁移指南 / 完成报告 / 性能报告 / 优先级报告 / 待办清单）
