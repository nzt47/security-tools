# 阶段 2 可见性指标收敛执行计划

> 生成时间：2026-06-28
> 当前阶段：阶段 1（已完成，exception_coverage 达标）
> 目标阶段：阶段 2（6 项指标全部收敛到阶段 2 目标值）
> 前置文档：[phase1_improvement_plan.md](./phase1_improvement_plan.md)

## 一、阶段 2 目标总览

| # | 指标 | 当前实测 | 阶段2目标 | 差距 | 优先级 | 预估工时 |
|---|------|---------|----------|------|--------|---------|
| 1 | structured_log_coverage | 26.5% | 70% | -43.5% | 高 | 38h |
| 2 | trace_coverage | 16.7% | 60% | -43.3% | 高 | 8h |
| 3 | test_coverage | 0.6% | 65% | -64.4% | 高 | 100h |
| 4 | boundary_test_coverage | 12.2% | 80% | -67.8% | 中 | 400h（分批） |
| 5 | exception_coverage | 71.6% | 80% | -8.4% | 中 | 6h |
| 6 | track_event_coverage | 7.4% | 50% | -42.6% | 中 | 12h |
| **合计** | | | | | | **564h** |

## 二、指标 1：structured_log_coverage（26.5% → 70%）

### 2.1 现状分析

- 总 logger 调用：1646 处（130 个文件）
- 已 JSON 格式：496 处（33 个文件）
- **需转换：1150 处（116 个文件）**

### 2.2 实施步骤

#### 步骤 1：核心模块转换（优先级 P0，预估 15h）

| 文件 | 需转换数 | 预估工时 |
|------|---------|---------|
| agent/p6_snapshot.py | 152 | 5h |
| agent/orchestrator/lifecycle_manager.py | 82 | 3h |
| agent/orchestrator/orchestrator.py | 37 | 2h |
| agent/network_config.py | 72 | 3h |
| agent/logging_utils.py | 28 | 2h |
| **小计** | **371** | **15h** |

转换模板：
```python
# 转换前
logger.info(f"任务 {task_id} 执行完成，耗时 {elapsed}s")

# 转换后
logger.info(json.dumps({
    "trace_id": trace_id,
    "module_name": "p6_snapshot",
    "action": "task.execute.complete",
    "duration_ms": round(elapsed * 1000, 2),
    "task_id": task_id,
}, ensure_ascii=False))
```

#### 步骤 2：监控模块转换（优先级 P1，预估 8h）

| 文件 | 需转换数 | 预估工时 |
|------|---------|---------|
| agent/monitoring/trace_http_client.py | 23 | 1.5h |
| agent/monitoring/resource_monitor.py | 17 | 1h |
| agent/monitoring/prometheus.py | 17 | 1h |
| agent/monitoring/chaos_injector.py | 20 | 1.5h |
| agent/monitoring/loki.py | 10 | 1h |
| agent/monitoring/business_metrics.py | 7 | 0.5h |
| agent/monitoring/decorators.py | 8 | 0.5h |
| agent/monitoring/alert_manager.py | 6 | 0.5h |
| agent/monitoring/alert_notifier.py | 3 | 0.5h |
| **小计** | **111** | **8h** |

#### 步骤 3：路由模块转换（优先级 P1，预估 5h）

| 文件 | 需转换数 | 预估工时 |
|------|---------|---------|
| agent/server_routes/routes_logging.py | 20 | 1h |
| agent/server_routes/routes_health.py | 10 | 0.5h |
| agent/server_routes/routes_memory.py | 9 | 0.5h |
| agent/server_routes/routes_chat.py | 7 | 0.5h |
| agent/server_routes/routes_dashboard.py | 7 | 0.5h |
| agent/server_routes/routes_replay.py | 7 | 0.5h |
| agent/server_routes/routes_system_prompt.py | 7 | 0.5h |
| agent/server_routes/tracing_middleware.py | 8 | 0.5h |
| 其余 7 个文件 | 17 | 0.5h |
| **小计** | **92** | **5h** |

#### 步骤 4：扩展与记忆模块转换（优先级 P2，预估 6h）

| 模块 | 文件数 | 需转换数 | 预估工时 |
|------|--------|---------|---------|
| extensions/ | 12 | 85 | 3h |
| memory/ | 6 | 51 | 2h |
| log_system/ | 7 | 47 | 1h |
| **小计** | **25** | **183** | **6h** |

#### 步骤 5：剩余模块转换（优先级 P3，预估 4h）

| 模块 | 文件数 | 需转换数 | 预估工时 |
|------|--------|---------|---------|
| cognitive/ | 8 | 35 | 1h |
| model_router/ | 2 | 17 | 0.5h |
| caching/ | 1 | 15 | 0.5h |
| lazy_loader/ | 2 | 18 | 0.5h |
| network/ | 1 | 19 | 0.5h |
| prompt_manager/ | 2 | 17 | 0.5h |
| observability/ | 2 | 9 | 0.3h |
| 其他散落文件 | 16 | 24 | 0.2h |
| **小计** | **33** | **154** | **4h** |

### 2.3 验证标准

- 转换后运行 `python scripts/visibility_report.py --config config.yaml`
- structured_log_coverage 实测值 ≥ 70%
- 所有现有测试无回归

## 三、指标 2：trace_coverage（16.7% → 60%）

### 3.1 现状分析

- 总路由文件：19 个 routes_*.py
- 已用 @trace_route：6 个文件（34 处装饰器）
- **需新增装饰器：13 个文件**

### 3.2 实施步骤

#### 步骤 1：重构重复装饰器定义（预估 1h）

删除 3 个文件中的本地重复 `trace_route` 定义，统一从 `tracing_decorator.py` 导入：

| 文件 | 本地定义行号 | 操作 |
|------|------------|------|
| routes_business_dashboard.py | 第 27 行 | 删除本地定义，改为 `from agent.server_routes.tracing_decorator import trace_route` |
| routes_dashboard.py | 第 63 行 | 同上 |
| routes_logging.py | 第 117 行 | 同上 |

#### 步骤 2：为 13 个路由文件添加装饰器（预估 5h）

| # | 文件 | register_routes 行号 | 预估路由数 | 预估工时 |
|---|------|---------------------|----------|---------|
| 1 | routes_chat.py | 105 | 8 | 0.5h |
| 2 | routes_config.py | 46 | 5 | 0.3h |
| 3 | routes_llm_monitor.py | 12 | 4 | 0.3h |
| 4 | routes_memory.py | 11 | 6 | 0.4h |
| 5 | routes_monitoring.py | 17 | 5 | 0.3h |
| 6 | routes_panorama.py | 160 | 4 | 0.3h |
| 7 | routes_permission.py | 18 | 5 | 0.3h |
| 8 | routes_personality.py | 9 | 3 | 0.2h |
| 9 | routes_sessions.py | 18 | 4 | 0.3h |
| 10 | routes_skills.py | 28 | 5 | 0.3h |
| 11 | routes_subagent.py | 13 | 4 | 0.3h |
| 12 | routes_system_prompt.py | 22 | 4 | 0.3h |
| 13 | routes_workspace.py | 19 | 4 | 0.3h |
| **合计** | | | **61** | **4h** |

装饰器添加模板：
```python
from agent.server_routes.tracing_decorator import trace_route, trace_async_route

def register_routes(app, state):
    @app.route("/api/chat/send", methods=["POST"])
    @trace_route(service_name="Chat")  # 新增装饰器，放在 @app.route 之上
    async def chat_send(request):
        ...
```

#### 步骤 3：验证（预估 1h）

- 运行 `python scripts/visibility_report.py --config config.yaml`
- trace_coverage 实测值 ≥ 60%
- 确认所有路由响应头中包含 `X-Trace-Id`

### 3.3 注意事项

- 异步路由使用 `@trace_async_route`，同步路由使用 `@trace_route`
- 装饰器必须放在 `@app.route(...)` 之上（最外层）
- `service_name` 参数应反映业务语义（如 "Chat"、"Memory"、"Config"）

## 四、指标 3：test_coverage（0.6% → 65%）

### 4.1 实施步骤

#### 步骤 1：修复 CI coverage.xml 生成（预估 2h）

- 确保 full-project-tests job 正确生成 coverage.xml
- 验证 CI 中 line-rate ≈ 19.7% → 目标 65%

#### 步骤 2：核心模块单元测试（预估 40h）

| 模块 | 预估测试数 | 预估工时 |
|------|----------|---------|
| orchestrator/ | 40 | 10h |
| workflow_engine/ | 30 | 8h |
| tool_calling/ | 25 | 6h |
| circuit_breaker.py | 15 | 4h |
| rate_limiter.py | 15 | 4h |
| memory/ | 20 | 5h |
| model_router/ | 15 | 3h |
| **小计** | **160** | **40h** |

#### 步骤 3：监控模块测试（预估 30h）

| 模块 | 预估测试数 | 预估工时 |
|------|----------|---------|
| monitoring/ (18 文件) | 80 | 20h |
| server_routes/ | 40 | 10h |
| **小计** | **120** | **30h** |

#### 步骤 4：剩余模块测试（预估 28h）

- extensions/、cognitive/、prompt_manager/ 等
- 预估 100 个测试，28h

## 五、指标 4：boundary_test_coverage（12.2% → 80%）

### 5.1 实施步骤

分批为 32 个模块补充边界测试，每批约 100 个用例：

| 批次 | 模块范围 | 预估用例数 | 预估工时 | 目标覆盖率 |
|------|---------|----------|---------|-----------|
| 批次 1 | 核心 5 模块 | 100 | 20h | 30% |
| 批次 2 | 监控 6 模块 | 120 | 24h | 50% |
| 批次 3 | 扩展 12 模块 | 240 | 48h | 65% |
| 批次 4 | 剩余 9 模块 | 180 | 36h | 80% |
| **合计** | **32 模块** | **640** | **128h** | **80%** |

> 注：阶段 2 目标 80% 需约 640 个新边界测试（非 7300 个，因为总测试基数也会增长）

## 六、指标 5：exception_coverage（71.6% → 80%）

### 6.1 实施步骤

- 当前 261 个文件中 187 个含异常处理，74 个无异常处理
- 为其中 22 个文件添加 try/except 即可达 80%

| 操作 | 文件数 | 预估工时 |
|------|--------|---------|
| 筛选需添加异常处理的文件 | 74 | 1h |
| 为 22 个文件添加 try/except | 22 | 4h |
| 验证 | - | 1h |
| **合计** | **22** | **6h** |

## 七、指标 6：track_event_coverage（7.4% → 50%）

### 7.1 实施步骤

- 当前 27 个模块中仅 2 个有 trackEvent 调用，25 个未埋点
- 为其中 12 个核心模块添加 trackEvent 即可达 50%

| 操作 | 模块数 | 预估工时 |
|------|--------|---------|
| 为 12 个核心模块添加 trackEvent | 12 | 10h |
| 验证埋点数据正确性 | - | 2h |
| **合计** | **12** | **12h** |

## 八、高优先级指标代码修改任务列表

### 8.1 structured_log_coverage 修改任务（TOP 10 文件）

| 任务ID | 文件 | 需转换数 | 优先级 | 依赖 |
|--------|------|---------|--------|------|
| SL-001 | agent/p6_snapshot.py | 152 | P0 | 无 |
| SL-002 | agent/orchestrator/lifecycle_manager.py | 82 | P0 | 无 |
| SL-003 | agent/network_config.py | 72 | P0 | 无 |
| SL-004 | agent/orchestrator/orchestrator.py | 37 | P0 | 无 |
| SL-005 | agent/logging_utils.py | 28 | P0 | 无 |
| SL-006 | agent/monitoring/trace_http_client.py | 23 | P1 | 无 |
| SL-007 | agent/monitoring/chaos_injector.py | 20 | P1 | 无 |
| SL-008 | agent/server_routes/routes_logging.py | 20 | P1 | TC-001 |
| SL-009 | agent/monitoring/resource_monitor.py | 17 | P1 | 无 |
| SL-010 | agent/monitoring/prometheus.py | 17 | P1 | 无 |

### 8.2 trace_coverage 修改任务

| 任务ID | 文件 | 操作 | 优先级 | 依赖 |
|--------|------|------|--------|------|
| TC-001 | routes_business_dashboard.py | 删除本地 trace_route 定义，改为导入 | P0 | 无 |
| TC-002 | routes_dashboard.py | 删除本地 trace_route 定义，改为导入 | P0 | 无 |
| TC-003 | routes_logging.py | 删除本地 trace_route 定义，改为导入 | P0 | 无 |
| TC-004 | routes_chat.py | 添加 @trace_route 装饰器 | P0 | TC-001 |
| TC-005 | routes_config.py | 添加 @trace_route 装饰器 | P0 | 无 |
| TC-006 | routes_memory.py | 添加 @trace_route 装饰器 | P0 | 无 |
| TC-007 | routes_llm_monitor.py | 添加 @trace_route 装饰器 | P1 | 无 |
| TC-008 | routes_monitoring.py | 添加 @trace_route 装饰器 | P1 | 无 |
| TC-009 | routes_panorama.py | 添加 @trace_route 装饰器 | P1 | 无 |
| TC-010 | routes_permission.py | 添加 @trace_route 装饰器 | P1 | 无 |
| TC-011 | routes_personality.py | 添加 @trace_route 装饰器 | P1 | 无 |
| TC-012 | routes_sessions.py | 添加 @trace_route 装饰器 | P1 | 无 |
| TC-013 | routes_skills.py | 添加 @trace_route 装饰器 | P1 | 无 |
| TC-014 | routes_subagent.py | 添加 @trace_route 装饰器 | P1 | 无 |
| TC-015 | routes_system_prompt.py | 添加 @trace_route 装饰器 | P1 | 无 |
| TC-016 | routes_workspace.py | 添加 @trace_route 装饰器 | P1 | 无 |

## 九、执行顺序与里程碑

```
里程碑 M1（预估 23h）          里程碑 M2（预估 30h）          里程碑 M3（预估 70h）
┌────────────────────┐      ┌────────────────────┐      ┌────────────────────┐
│ TC-001~006         │      │ SL-006~010         │      │ test_coverage      │
│ trace_coverage     │ ──→  │ 监控模块日志转换    │ ──→  │ 核心模块单元测试    │
│ 16.7% → 60% ✅     │      │ structured_log     │      │ 0.6% → 65% ✅      │
├────────────────────┤      ├────────────────────┤      ├────────────────────┤
│ SL-001~005         │      │ exception_coverage │      │ boundary_test      │
│ 核心模块日志转换    │      │ 71.6% → 80% ✅     │      │ 12.2% → 80% ✅     │
│ structured_log     │      ├────────────────────┤      ├────────────────────┤
│ 26.5% → 40%        │      │ track_event        │      │ 最终验证           │
│                    │      │ 7.4% → 50% ✅      │      │ 所有指标达标       │
└────────────────────┘      └────────────────────┘      └────────────────────┘
```

## 十、风险评估

| 风险项 | 影响 | 概率 | 缓解措施 |
|--------|------|------|---------|
| 日志批量转换引入格式错误 | 核心逻辑日志丢失 | 中 | 每个模块转换后单独运行测试验证 |
| @trace_route 装饰器循环依赖 | 路由注册失败 | 低 | 使用延迟导入，参照 routes_health.py 样板 |
| test_coverage CI 环境差异 | 本地与 CI line-rate 不一致 | 中 | 统一 CI 配置，确保 coverage.xml 正确生成 |
| boundary_test 工作量大 | 阶段 2 难以短期完成 | 高 | 分批执行，批次 1+2 完成即可达 50% |
| 本地 trace_route 重复定义删除后引用断裂 | 3 个文件路由报错 | 中 | 删除前确认所有引用点已改为导入 |
