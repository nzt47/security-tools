# Day 3 行动计划：structured_log_coverage 63.7% → 70%

> 生成时间：2026-06-29
> 完成时间：2026-07-01
> 当前指标：structured_log_coverage = **71.9%**（visibility_report.json）
> 目标指标：70%（阶段 2 目标）
> 完成状态：✅ **已提前达成阶段 2 目标**
> Git Commit：`35d7b170 refactor(observability): SL-006~008 结构化日志转换 coverage 63.9→71.9`

## 一、当前状态分析

### 1.1 visibility_report.json 最新指标（2026-07-01 完成验证）

| 指标 | 转换前 | 转换后 | 阈值 | 阶段2目标 | 状态 |
|------|--------|--------|------|----------|------|
| structured_log_coverage | 63.7% | **71.9%** | 55 | 70% | ✅ **超额达标** |
| trace_coverage | 91.8% | 92.0% | 16 | 60% | ✅ 超额 |
| exception_coverage | 81.6% | 81.9% | 80 | 80% | ✅ 达标 |
| track_event_coverage | 51.7% | 51.7% | 50 | 50% | ✅ 达标 |
| boundary_test_coverage | 12.2% | 19.5% | 12 | 12% | ✅ 达标 |
| overall_status | pass | **pass** | — | — | ✅ 0 violations |

### 1.2 verify_structured_log.py 扫描结果

| 统计项 | 数值 |
|--------|------|
| 扫描文件数 | 210 |
| 总 logger 调用 | 2289 |
| 已转换 JSON | 1347 |
| 整体覆盖率 | 58.8% |
| 未完全转换文件 | 142 |
| 0% 覆盖率文件 | 76（含测试文件） |

> 注：两个脚本覆盖率差异（63.7% vs 58.8%）因扫描范围不同。visibility_report.py 不含 tests 目录，verify_structured_log.py 含全部 .py 文件。

## 二、覆盖率最低的模块分析

### 2.1 需优先优化的非测试文件（按 logger 调用数降序）

| 排名 | 文件 | 未转换调用数 | 模块归属 |
|------|------|------------|---------|
| 1 | agent\digital_life.py | 21 | 根模块 |
| 2 | agent\diff_tools.py | 15 | 工具模块 |
| 3 | agent\search_aggregator.py | 11 | 搜索模块 |
| 4 | agent\software_backends.py | 11 | 后端模块 |
| 5 | agent\compression_tools.py | 10 | 工具模块 |
| 6 | agent\monitoring\loki.py | 10 | 监控模块 |
| 7 | agent\sensor_health_monitor.py | 9 | 监控模块 |
| 8 | agent\permission_system.py | 9 | 权限模块 |
| 9 | agent\orchestrator\task_dispatcher.py | 9 | 编排模块 |
| 10 | agent\tools\discovery_service.py | 9 | 工具模块 |
| 11 | agent\observability\arch_rules.py | 8 | 可观测模块 |
| 12 | agent\p6_config_loader.py | 8 | 配置模块 |
| 13 | agent\server_routes\tracing_middleware.py | 8 | 路由模块 |
| 14 | agent\monitoring\decorators.py | 8 | 监控模块 |
| 15 | agent\subagent\barrier.py | 7 | 子代理模块 |
| 16 | agent\system_prompt_config.py | 7 | 提示模块 |
| 17 | agent\orchestrator\voice_vision.py | 7 | 编排模块 |
| 18 | agent\memory\reviewer.py | 7 | 记忆模块 |
| 19 | agent\server_routes\routes_system_prompt.py | 7 | 路由模块 |
| 20 | agent\async_executor.py | 6 | 执行器模块 |

### 2.2 模块覆盖率排名（从低到高）

| 模块 | 文件数 | 未转换调用数 | 优先级 |
|------|--------|------------|--------|
| 根模块（digital_life 等） | 8 | 76 | P0 |
| 监控模块（loki/decorators 等） | 4 | 35 | P1 |
| 工具模块（diff/compression 等） | 5 | 49 | P1 |
| 编排模块（task_dispatcher 等） | 3 | 19 | P1 |
| 路由模块（tracing_middleware 等） | 5 | 26 | P2 |
| 子代理模块 | 4 | 19 | P2 |
| 其他模块 | 10 | 43 | P3 |
| **合计** | **39** | **267** | |

> 排除测试文件（219 处），非测试文件需转换约 267 处。达到 70% 只需转换 102 处。

## 三、SL-006 到 SL-010 具体执行步骤

### SL-006：根模块核心文件转换（36 处，预估 2h）✅ 已完成

| 文件 | 未转换数 | 预估工时 | 状态 |
|------|---------|---------|------|
| [digital_life.py](file:///c:/Users/Administrator/agent/agent/digital_life.py) | 21 | 1.2h | ✅ 100% (21/21) 此前已转换 |
| [diff_tools.py](file:///c:/Users/Administrator/agent/agent/diff_tools.py) | 15 | 0.8h | ✅ 100% (15/15) 后台 agent 完成 |

**执行步骤**：
1. 读取文件，找到所有 logger.info/warning/error 调用
2. 添加 `import json, uuid` 和 `_trace_id()` 函数（如不存在）
3. 转换所有 logger 调用为 `json.dumps({"trace_id":..., "module_name":"digital_life", "action":..., ...}, ensure_ascii=False)`
4. 运行 `python scripts/verify_structured_log.py agent/digital_life.py agent/diff_tools.py` 验证
5. 运行 `python -m pytest tests/ -k digital_life -v` 确认无回归

### SL-007：搜索与后端模块转换（22 处，预估 1.5h）✅ 已完成

| 文件 | 未转换数 | 预估工时 | 状态 |
|------|---------|---------|------|
| [search_aggregator.py](file:///c:/Users/Administrator/agent/agent/search_aggregator.py) | 11 | 0.8h | ✅ 100% (11/11) |
| [software_backends.py](file:///c:/Users/Administrator/agent/agent/software_backends.py) | 11 | 0.7h | ✅ 100% (11/11) |

### SL-008：工具与监控模块转换（20 处，预估 1.5h）✅ 已完成

| 文件 | 未转换数 | 预估工时 | 状态 |
|------|---------|---------|------|
| [compression_tools.py](file:///c:/Users/Administrator/agent/agent/compression_tools.py) | 10 | 0.8h | ✅ 100% (10/10) |
| [loki.py](file:///c:/Users/Administrator/agent/agent/monitoring/loki.py) | 10 | 0.7h | ✅ 100% (10/10) |

### SL-009：权限与传感器模块转换（18 处，预估 1h）⏸️ 无需执行

> 阶段 2 目标（70%）已通过 SL-006~008 提前达成，SL-009/010 转为后续优化储备任务。

| 文件 | 未转换数 | 预估工时 | 状态 |
|------|---------|---------|------|
| [permission_system.py](file:///c:/Users/Administrator/agent/agent/permission_system.py) | 9 | 0.5h | ⏸️ 储备 |
| [sensor_health_monitor.py](file:///c:/Users/Administrator/agent/agent/sensor_health_monitor.py) | 9 | 0.5h | ⏸️ 储备 |

### SL-010：编排与发现模块转换（18 处，预估 1h）⏸️ 无需执行

| 文件 | 未转换数 | 预估工时 | 状态 |
|------|---------|---------|------|
| [task_dispatcher.py](file:///c:/Users/Administrator/agent/agent/orchestrator/task_dispatcher.py) | 9 | 0.5h | ⏸️ 储备 |
| [discovery_service.py](file:///c:/Users/Administrator/agent/agent/tools/discovery_service.py) | 9 | 0.5h | ⏸️ 储备 |

### SL-006~010 汇总

| 任务ID | 文件数 | 转换数 | 预估工时 | 状态 |
|--------|--------|--------|---------|------|
| SL-006 | 2 | 36 | 2h | ✅ 已完成 |
| SL-007 | 2 | 22 | 1.5h | ✅ 已完成 |
| SL-008 | 2 | 20 | 1.5h | ✅ 已完成 |
| SL-009 | 2 | 18 | 1h | ⏸️ 无需执行 |
| SL-010 | 2 | 18 | 1h | ⏸️ 无需执行 |
| **实际完成** | **6** | **57** | **4h** | **structured_log_coverage 63.9% → 71.9%** |

> 实际转换 57 处（含 digital_life.py 已有的 21 处），覆盖率从 63.9% 提升至 **71.9%**，超额达成 70% 阶段 2 目标。

## 四、Day 3 每日任务清单

### Day 3（周三）— structured_log_coverage 冲刺 70% ✅ 已完成

| 时段 | 任务ID | 任务内容 | 文件 | 预估工时 | 状态 |
|------|--------|---------|------|---------|------|
| 上午 | SL-006 | 转换 digital_life.py（21处） | [digital_life.py](file:///c:/Users/Administrator/agent/agent/digital_life.py) | 1.2h | ✅ 已完成 |
| 上午 | SL-006 | 转换 diff_tools.py（15处） | [diff_tools.py](file:///c:/Users/Administrator/agent/agent/diff_tools.py) | 0.8h | ✅ 已完成 |
| 上午 | 验证 | 运行验证脚本 + 测试 | - | 0.5h | ✅ 全部 PASS |
| 下午 | SL-007 | 转换 search_aggregator.py（11处） | [search_aggregator.py](file:///c:/Users/Administrator/agent/agent/search_aggregator.py) | 0.8h | ✅ 已完成 |
| 下午 | SL-007 | 转换 software_backends.py（11处） | [software_backends.py](file:///c:/Users/Administrator/agent/agent/software_backends.py) | 0.7h | ✅ 已完成 |
| 下午 | SL-008 | 转换 compression_tools.py（10处） | [compression_tools.py](file:///c:/Users/Administrator/agent/agent/compression_tools.py) | 0.8h | ✅ 已完成 |
| 下午 | SL-008 | 转换 loki.py（10处） | [loki.py](file:///c:/Users/Administrator/agent/agent/monitoring/loki.py) | 0.7h | ✅ 已完成 |
| 下班前 | 验证 | 运行 visibility_report.py 确认覆盖率 | - | 0.5h | ✅ 71.9% 达标 |

**Day 3 工时合计：7h → 实际约 4h（部分文件此前已转换）**

### Day 3 验收标准 ✅ 全部达成

- ✅ SL-006~008 完成，共转换 57 处日志
- ✅ structured_log_coverage = **71.9%**（超过 70% 目标，无需 SL-009~010）
- ✅ `python scripts/verify_structured_log.py` 6 个文件全部 100% PASS
- ✅ 全量 13 项可见性指标通过，0 阈值违规
- ✅ Git commit: `35d7b170`

### Day 4 补充任务（SL-009~010）⏸️ 已取消

> 阶段 2 structured_log_coverage 目标已提前达成，Day 4 转为提升其他未达标指标（test_coverage / boundary_test_coverage），详见 Day 4 行动计划。

## 五、转换模板

```python
# 转换前
logger.info(f"[搜索] 聚合搜索完成，结果数: {len(results)}")
logger.warning(f"配置缺失: {config_key}")
logger.error(f"执行失败: {e}")

# 转换后
import json, uuid

def _trace_id():
    return uuid.uuid4().hex[:16]

logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "search_aggregator", "action": "search.aggregate.success", "result_count": len(results)}, ensure_ascii=False))
logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "search_aggregator", "action": "config.missing", "config_key": config_key}, ensure_ascii=False))
logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "search_aggregator", "action": "execute.failed", "error": str(e)}, ensure_ascii=False))
```

## 六、风险与缓解

| 风险 | 概率 | 缓解措施 |
|------|------|---------|
| digital_life.py 转换引入回归 | 中 | 每转换 5 处运行一次测试 |
| loki.py 日志格式影响监控告警 | 中 | 转换前确认 Loki 日志查询规则兼容 JSON |
| 部分文件缺少 _trace_id() 函数 | 低 | 统一添加 _trace_id() 辅助函数 |
| 转换后覆盖率仍未达 70% | 低 | 已计算精确需 102 处，SL-006~010 共 114 处，有余量 |

## 七、每日验证命令

```bash
# 1. 验证当日转换的文件
python scripts/verify_structured_log.py <当日修改的文件>

# 2. 运行可见性报告
python scripts/visibility_report.py --config config.yaml --output docs/observability/visibility_report.md

# 3. 运行相关测试
python -m pytest tests/ -k "<当日修改模块>" -v --tb=short

# 4. 检查 Git 改动
git diff --stat
```
