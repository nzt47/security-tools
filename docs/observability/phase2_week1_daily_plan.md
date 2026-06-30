# 阶段 2 第一周每日执行清单

> 生成时间：2026-06-28
> 对应里程碑：M1（预估 23h）
> 分支：phase2-visibility-convergence
> 前置文档：[phase2_execution_plan.md](./phase2_execution_plan.md)

## 本周目标

| 指标 | 起始值 | 周末目标 | 进度 |
|------|--------|---------|------|
| trace_coverage | 16.7% | 60% ✅ | 装饰器全部添加完成 |
| structured_log_coverage | 26.5% | 40% | 核心模块日志转换完成（SL-001~005） |

## 每日任务清单

### Day 1（周一）— trace_coverage 重构 + SL-001

| 时段 | 任务ID | 任务内容 | 文件 | 预估工时 |
|------|--------|---------|------|---------|
| 上午 | TC-001 | 删除 routes_business_dashboard.py 本地 trace_route 定义，改为导入 | [routes_business_dashboard.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_business_dashboard.py) | 0.5h |
| 上午 | TC-002 | 删除 routes_dashboard.py 本地 trace_route 定义，改为导入 | [routes_dashboard.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_dashboard.py) | 0.5h |
| 上午 | TC-003 | 删除 routes_logging.py 本地 trace_route 定义，改为导入 | [routes_logging.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_logging.py) | 0.5h |
| 下午 | SL-001 | 转换 p6_snapshot.py 全部 logger 为 JSON 格式（152处） | [p6_snapshot.py](file:///c:/Users/Administrator/agent/agent/p6_snapshot.py) | 5h |
| 下班前 | 验证 | 运行 `python scripts/verify_structured_log.py agent/p6_snapshot.py` | - | 0.5h |

**Day 1 工时合计：7h**

**验收标准**：
- TC-001~003 完成后，3 个文件中使用 `from agent.server_routes.tracing_decorator import trace_route`
- SL-001 完成后，p6_snapshot.py 的 structured_log 覆盖率 = 100%
- 运行 `python -m pytest tests/ -k p6_snapshot -v` 全部通过

---

### Day 2（周二）— trace_coverage 装饰器添加 + SL-002

| 时段 | 任务ID | 任务内容 | 文件 | 预估工时 |
|------|--------|---------|------|---------|
| 上午 | TC-004 | 为 routes_chat.py 添加 @trace_route 装饰器 | [routes_chat.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_chat.py) | 0.5h |
| 上午 | TC-005 | 为 routes_config.py 添加 @trace_route 装饰器 | [routes_config.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_config.py) | 0.3h |
| 上午 | TC-006 | 为 routes_memory.py 添加 @trace_route 装饰器 | [routes_memory.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_memory.py) | 0.4h |
| 下午 | SL-002 | 转换 lifecycle_manager.py 全部 logger 为 JSON 格式（82处） | [lifecycle_manager.py](file:///c:/Users/Administrator/agent/agent/orchestrator/lifecycle_manager.py) | 3h |
| 下班前 | 验证 | 运行验证脚本 + `python scripts/visibility_report.py --config config.yaml --json-only` | - | 0.5h |

**Day 2 工时合计：4.7h**

**验收标准**：
- TC-004~006 完成后，trace_coverage 从 16.7% 提升至约 32%
- SL-002 完成后，lifecycle_manager.py 的 structured_log 覆盖率 = 100%

---

### Day 3（周三）— trace_coverage 收尾 + SL-003

| 时段 | 任务ID | 任务内容 | 文件 | 预估工时 |
|------|--------|---------|------|---------|
| 上午 | TC-007 | 为 routes_llm_monitor.py 添加 @trace_route | [routes_llm_monitor.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_llm_monitor.py) | 0.3h |
| 上午 | TC-008 | 为 routes_monitoring.py 添加 @trace_route | [routes_monitoring.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_monitoring.py) | 0.3h |
| 上午 | TC-009 | 为 routes_panorama.py 添加 @trace_route | [routes_panorama.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_panorama.py) | 0.3h |
| 上午 | TC-010 | 为 routes_permission.py 添加 @trace_route | [routes_permission.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_permission.py) | 0.3h |
| 下午 | TC-011 | 为 routes_personality.py 添加 @trace_route | [routes_personality.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_personality.py) | 0.2h |
| 下午 | TC-012 | 为 routes_sessions.py 添加 @trace_route | [routes_sessions.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_sessions.py) | 0.3h |
| 下午 | TC-013 | 为 routes_skills.py 添加 @trace_route | [routes_skills.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_skills.py) | 0.3h |
| 下午 | TC-014 | 为 routes_subagent.py 添加 @trace_route | [routes_subagent.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_subagent.py) | 0.3h |
| 下午 | TC-015 | 为 routes_system_prompt.py 添加 @trace_route | [routes_system_prompt.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_system_prompt.py) | 0.3h |
| 下午 | TC-016 | 为 routes_workspace.py 添加 @trace_route | [routes_workspace.py](file:///c:/Users/Administrator/agent/agent/server_routes/routes_workspace.py) | 0.3h |
| 下班前 | SL-003 | 开始转换 network_config.py（72处），完成约 50% | [network_config.py](file:///c:/Users/Administrator/agent/agent/network_config.py) | 1h |

**Day 3 工时合计：4.0h**

**验收标准**：
- TC-007~016 全部完成后，trace_coverage 从 32% 提升至 **60% ✅**（阶段 2 目标达成）
- 运行 `python scripts/visibility_report.py --config config.yaml` 确认 trace_coverage ≥ 60%

---

### Day 4（周四）— SL-003 完成 + SL-004 + SL-005

| 时段 | 任务ID | 任务内容 | 文件 | 预估工时 |
|------|--------|---------|------|---------|
| 上午 | SL-003 | 完成 network_config.py 剩余转换（72处） | [network_config.py](file:///c:/Users/Administrator/agent/agent/network_config.py) | 2h |
| 上午 | SL-004 | 转换 orchestrator.py 全部 logger 为 JSON 格式（37处） | [orchestrator.py](file:///c:/Users/Administrator/agent/agent/orchestrator/orchestrator.py) | 2h |
| 下午 | SL-005 | 转换 logging_utils.py 全部 logger 为 JSON 格式（28处） | [logging_utils.py](file:///c:/Users/Administrator/agent/agent/logging_utils.py) | 2h |
| 下班前 | 验证 | 运行验证脚本批量检查 + visibility_report | - | 0.5h |

**Day 4 工时合计：6.5h**

**验收标准**：
- SL-003~005 全部完成后，structured_log_coverage 从 26.5% 提升至约 **40%**
- 运行 `python scripts/verify_structured_log.py agent/orchestrator/` 确认转换完成
- 运行 `python scripts/verify_structured_log.py agent/network_config.py agent/logging_utils.py`

---

### Day 5（周五）— 集成验证 + 提交 + 周报

| 时段 | 任务ID | 任务内容 | 文件 | 预估工时 |
|------|--------|---------|------|---------|
| 上午 | 验证 | 运行完整可见性报告，确认本周指标提升 | - | 1h |
| 上午 | 测试 | 运行全量测试套件确保无回归 | - | 1h |
| 下午 | 提交 | Git commit 本周所有改动（TC-001~016, SL-001~005） | - | 1h |
| 下午 | 周报 | 整理本周进度，更新 phase2_execution_plan.md 中的状态 | - | 0.5h |
| 下午 | 规划 | 预览下周任务（SL-006~010 监控模块日志转换） | - | 0.5h |

**Day 5 工时合计：4h**

**验收标准**：
- `python scripts/visibility_report.py --config config.yaml` 退出码 = 0
- trace_coverage ≥ 60% ✅
- structured_log_coverage ≥ 40%
- 全量测试通过率 ≥ 95%

---

## 本周工时汇总

| 日期 | 工时 | 主要任务 |
|------|------|---------|
| Day 1（周一） | 7h | TC-001~003 + SL-001 |
| Day 2（周二） | 4.7h | TC-004~006 + SL-002 |
| Day 3（周三） | 4h | TC-007~016 + SL-003 开始 |
| Day 4（周四） | 6.5h | SL-003~005 |
| Day 5（周五） | 4h | 集成验证 + 提交 + 周报 |
| **合计** | **26.2h** | |

## 本周交付物清单

- [ ] TC-001~003：3 个文件删除本地 trace_route 定义
- [ ] TC-004~016：13 个路由文件添加 @trace_route 装饰器
- [ ] SL-001：p6_snapshot.py 日志转换（111处）
- [ ] SL-002：lifecycle_manager.py 日志转换（82处）
- [ ] SL-003：network_config.py 日志转换（72处）
- [ ] SL-004：orchestrator.py 日志转换（37处）
- [ ] SL-005：logging_utils.py 日志转换（28处）
- [ ] trace_coverage 达到 60%
- [ ] structured_log_coverage 达到 40%
- [ ] Git commit 包含所有改动
- [ ] 全量测试通过

## 每日验证命令

每天下班前执行以下命令：

```bash
# 1. 验证当日转换的文件
python scripts/verify_structured_log.py <当日修改的文件>

# 2. 运行可见性报告（确认指标提升）
python scripts/visibility_report.py --config config.yaml --output docs/observability/visibility_report.md

# 3. 运行相关测试（确保无回归）
python -m pytest tests/ -k "<当日修改模块>" -v --tb=short

# 4. 检查 Git 改动
git diff --stat
```
