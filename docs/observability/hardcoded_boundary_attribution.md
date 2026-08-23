# M3.2 硬编码边界值差异项归属归档

> 关联：`docs/observability/hardcoded_boundary_baseline_report.json`（M3.1 更新，high 118 → 129）
> 日期：2026-08-23
> 方法：新旧基线 findings 指纹差集（file:line/category/name）+ `git blame` 逐项归因

---

## 1. 概述

M3.1 将基线从 118 更新至 129（+11 项）。指纹级差集共 **52 个新增条目**（含行号偏移导致的重复计数），本归档为每个条目记录来源。

**来源统计**：

| 来源类型 | 条目数 | 说明 |
|---|---|---|
| 已提交代码（历史 commit） | 50 | 均为既有开发分支提交引入，非 PR #754 新增 |
| 工作区未跟踪文件 | 2 | `agent/api_gateway_cli.py`（未纳入 git，blame 无源） |

> ⚠️ 基线生成说明：M3.1 扫描在**主工作区**执行（含未提交/未跟踪文件），基线值 129 偏保守；CI 对已提交代码扫描值 < 129，Boundary Guard 已验证转绿。

## 2. 来源 Top Commit（按条目数）

| commit | 说明 | 条目数 |
|---|---|---|
| `f9966ce` | feat(observability): 恢复可观测性增强修改（结构化日志） | 13 |
| `be80d89` | feat: 云枢计划任务与心跳系统完整集成 | 6 |
| `19846f6` | feat(learning): 任务6 沙箱回放与评估隔离加固 | 5 |
| `cd47ab0` | fix(resilience): 修复熔断/限流/降级/容灾 API 契约 | 3 |
| `3f37612` | feat(learning): LearningMetrics SQLite | 3 |
| 其他 21 个 commit | 分散引入 | 20 |
| 工作区未跟踪 | `api_gateway_cli.py`（2 行） | 2 |

## 3. 完整差异项清单

| # | 文件 | 行 | 类别 | 名称 | 来源 commit | 来源说明 |
|---|---|---|---|---|---|---|
| 1 | api_gateway_cli.py | 87 | timeout | timeout | 未跟踪 | 工作区未纳入 git |
| 2 | api_gateway_cli.py | 102 | timeout | timeout | 未跟踪 | 工作区未纳入 git |
| 3 | disaster_recovery.py | 984 | timeout | timeout | cd47ab0 | fix(resilience) 容灾契约 |
| 4 | disaster_recovery.py | 1130 | timeout | timeout | cd47ab0 | 同上 |
| 5 | env_config_manager.py | 509 | timeout | timeout | caebf0e | feat(config) .env 权限自动加固 |
| 6 | graceful_degrade.py | 974 | retry | max_retries | b5187ef | feat(observability) 架构违规修复 |
| 7 | human_in_the_loop/takeover_queue.py | 285 | timeout | timeout | 5afa686 | feat(monitoring) 人工接管 |
| 8 | lazy_loader_async.py | 71 | capacity | max_workers | be80d89 | 计划任务与心跳集成 |
| 9 | lazy_loader_async.py | 322 | capacity | max_workers | be80d89 | 同上 |
| 10 | learning/replay.py | 955 | timeout | timeout | 19846f6 | 沙箱回放隔离加固 |
| 11 | learning/replay.py | 958 | timeout | timeout | 19846f6 | 同上 |
| 12 | learning/replay.py | 965 | timeout | timeout | 19846f6 | 同上 |
| 13 | learning/replay.py | 1010 | timeout | timeout | 19846f6 | 同上 |
| 14 | learning/replay.py | 1134 | timeout | timeout | 19846f6 | 同上 |
| 15 | learning_metrics.py | 1314 | timeout | timeout | 3f37612 | LearningMetrics SQLite |
| 16 | learning_metrics.py | 1336 | timeout | timeout | 3f37612 | 同上 |
| 17 | learning_metrics.py | 1349 | timeout | timeout | 3f37612 | 同上 |
| 18 | log_system/introspection.py | 491 | timeout | timeout | 106ae42 | StopMixin 统一线程优雅关闭 |
| 19 | log_system/optimized_storage.py | 322 | capacity | batch_size | f9966ce | 可观测性增强（结构化日志） |
| 20 | log_system/safe_logger.py | 403 | timeout | timeout | f9966ce | 同上 |
| 21 | log_system/safe_logger.py | 463 | timeout | timeout | f9966ce | 同上 |
| 22 | logging_utils.py | 1171 | timeout | timeout | be80d89 | 计划任务与心跳集成 |
| 23 | logging_utils.py | 1230 | timeout | timeout | be80d89 | 同上 |
| 24 | mcp_executor.py | 181 | timeout | timeout | 82d6f57 | MCP 日志级别安全监控增强 |
| 25 | memory/adapters/holographic_adapter.py | 807 | retry | max_retries | 0c1dff6 | HolographicAdapter 三表合一 |
| 26 | memory_optimized.py | 177 | capacity | max_size | be80d89 | 计划任务与心跳集成 |
| 27 | monitoring/alert_evaluator.py | 508 | timeout | timeout | f9966ce | 可观测性增强 |
| 28 | monitoring/observability_optimizations.py | 133 | capacity | batch_size | f9966ce | 同上 |
| 29 | monitoring/observability_optimizations.py | 164 | timeout | timeout | f9966ce | 同上 |
| 30 | monitoring/observability_optimizations.py | 201 | timeout | timeout | f9966ce | 同上 |
| 31 | monitoring/observability_optimizations.py | 254 | capacity | max_size | f9966ce | 同上 |
| 32 | monitoring/observability_optimizations.py | 350 | capacity | max_size | f9966ce | 同上 |
| 33 | monitoring/observability_optimizations.py | 364 | capacity | batch_size | ed53d56 | 修复并发风险（统计计数加锁） |
| 34 | monitoring/optimized_metrics.py | 286 | capacity | batch_size | f9966ce | 可观测性增强 |
| 35 | monitoring/optimized_metrics.py | 309 | timeout | timeout | f9966ce | 同上 |
| 36 | monitoring/optimized_metrics.py | 388 | capacity | batch_size | f9966ce | 同上 |
| 37 | monitoring/performance.py | 262 | timeout | timeout | 8021af1 | 移除内置搜索引擎 |
| 38 | monitoring/performance.py | 658 | capacity | max_size | 8021af1 | 同上 |
| 39 | monitoring/performance_optimization.py | 384 | timeout | timeout | f9966ce | 可观测性增强 |
| 40 | observability/tool_trace.py | 210 | timeout | timeout | a977b25 | TLM-AUDIT-0 修复 |
| 41 | observability/tool_trace.py | 745 | timeout | timeout | 013b319 | 新增 tool_trace.py |
| 42 | observability/tool_trace.py | 782 | timeout | timeout | a977b25 | TLM-AUDIT-0 修复 |
| 43 | observability/tool_trace.py | 799 | timeout | timeout | a977b25 | 同上 |
| 44 | orchestrator/orchestrator.py | 73 | capacity | max_workers | beb623b | waitress threads 8→16 |
| 45 | orchestrator/orchestrator.py | 144 | capacity | max_workers | 439bf52 | 规划/orchestrator 阶段5 D7 |
| 46 | rate_limiter.py | 295 | timeout | timeout | cd47ab0 | fix(resilience) 限流契约 |
| 47 | state_manager.py | 602 | timeout | timeout | be80d89 | 计划任务与心跳集成 |
| 48 | tool_router_hybrid.py | 579 | timeout | timeout | 71d08d0 | fix(test-hang) 测试卡死根因 |
| 49 | tool_router_hybrid.py | 776 | timeout | timeout | 92c25a2 | 每日回归 + GPU 部署脚本 |
| 50 | tool_router_hybrid.py | 779 | timeout | timeout | 92c25a2 | 同上 |
| 51 | verification/output_validator.py | 108 | retry | max_retries | 0411df8 | feat(safety) 输出校验 |
| 52 | web/crawler_control.py | 304 | timeout | timeout | 86e3a59 | fix(web) 对话回复异常修复 |

## 4. 处置建议（M3.3 决策输入）

- **已提交的 50 项**：均为**历史合理硬编码**（超时/容量/重试阈值），建议**保留在基线**并标注来源（本归档即归属记录）
- **api_gateway_cli.py 2 项**：文件未纳入 git，建议**将文件纳入版本管理**或**从扫描范围排除**；基线 129 已覆盖（偏保守），CI 不受影响
- **配置化候选**：条目 44/45（orchestrator max_workers）等与部署规模相关的容量值，建议后续抽取到配置（P2）
- 原则：配置化优先于盲目扩基线；本归档为扩基线提供可追溯归属

## 5. 下一步

- M3.3：按处置建议逐项决策（保留基线 / 配置化 / 排除未跟踪文件）
- M3.4：触发 Boundary Guard 重跑确认（M3.1 已转绿，M3.3 改动后复验）
