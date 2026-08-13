# TASK-01 变更说明：规划引擎接入主链路

| 字段 | 值 |
| --- | --- |
| 任务编号 | TASK-01 |
| 所属阶段 | 主线阶段 1/5（闭环贯通起点） |
| 涉及主文档 | `docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§4.4 断点 1） |
| 关联任务书 | `docs/zh/智能体学习机制重构计划/TASK-01_规划引擎接入主链路.md` |
| 实现日期 | 2026-08-13 |

## 1. 背景

云枢规划引擎 `planning/`（PlanningCore / ReActLoop / TaskDecomposer / Reflector / PlanDB）是完整实现，但此前 orchestrator 主链路仅在 LLM 调用后追加一行 `get_stats()` 统计文本，从未真正调用 `_planner.chat()`（"已建未用"，D7 缺陷）。

本任务只解决"接线"，不新增规划能力：将 `PlanningCore.chat()`（ReAct 路径）以保守灰度方式接入 orchestrator 主链路，是"执行→反思→沉淀"闭环的地基断点。

## 2. 改动点

### 2.1 配置（config.yaml）

在 `planning:` 段新增 3 个灰度配置（含中文注释，说明 Why）：

| 配置键 | 默认值 | 说明 |
| --- | --- | --- |
| `planning.wire_enabled` | `false` | 灰度开关。默认关闭，未开启前生产行为与现状逐字节等价 |
| `planning.wire_min_complexity` | `COMPLEX` | 触发规划的最低复杂度（TRIVIAL/SIMPLE/NORMAL/COMPLEX） |
| `planning.wire_timeout_seconds` | `30` | 规划调用超时（秒），超时回退 LLM |

优先级遵循项目既有约定：**环境变量 > config.yaml > 硬编码默认值**。
对应环境变量：`PLANNING_WIRE_ENABLED` / `PLANNING_WIRE_MIN_COMPLEXITY` / `PLANNING_WIRE_TIMEOUT_SECONDS`。

### 2.2 orchestrator.py（agent/orchestrator/orchestrator.py）

1. **模块级复杂度分级**：新增 `_judge_wire_complexity()` / `_wire_complexity_meets()`。
   评分公式复用 `PlanningCore._needs_planning()` 现成逻辑（`complex_count + 0.5×action_count`），
   分级语义对齐 enhanced_planner（TRIVIAL/SIMPLE/NORMAL/MODERATE/COMPLEX，MODERATE 作为 NORMAL 兼容别名）。
2. **配置加载器**：类方法 `_load_planning_wire_config()`，三层优先级（硬编码默认 → config.yaml → 环境变量），
   与 `_load_reject_config` 同源模式，config.yaml 缺失/解析失败不影响主链路。
3. **process() 接线分支**（第三步三半，位于拒识 return 之后、LLM 调用之前）：
   - 命中条件：`wire_enabled=true` 且 `_planner` 可用且任务复杂度 ≥ `wire_min_complexity`；
   - 规划成功：`response = plan_result.response`，跳过 LLM 调用与置信度兜底，
     同时 `_planning_mode = False` 抑制旧"第四步半"规划段（防双规划），
     响应 metadata 标注 `routed_by: planning`（供 TASK-03 埋点）；
   - 规划失败/超时/空响应：静默回退原 LLM 路径，WARNING 日志记录降级原因（不中断用户请求）。

### 2.3 测试

- 新增 `tests/unit/test_planning_wire.py`（5 用例）：
  1. `wire_enabled=false` → 不调用 `_planner.chat()`（灰度 inert）；
  2. `wire_enabled=true` + COMPLEX 任务 → 调用 `_planner.chat()`，响应为规划结果，`routed_by=planning`；
  3. `chat()` 抛异常 → 回退 LLM 直答，日志含降级 WARNING；
  4. 简单任务 → 不触发规划；
  5. `chat()` 超时 → 回退 LLM 直答，链路耗时受控。
- `tests/unit/test_planning_defect_d7.py` 转正（4 用例）：
  1. 生产配置 `planning.enabled=true`；
  2. 灰度开关 `wire_enabled` 默认 `false`；
  3. `wire_enabled=true` + 复杂任务 → orchestrator 调用 `_planner.chat()`（实断言）；
  4. `wire_enabled=false` → 不调用 `_planner.chat()`。

## 3. 回退机制

| 场景 | 行为 |
| --- | --- |
| `wire_enabled=false`（默认） | 接线分支整体 inert，走原 LLM 路径，逐字节等价 |
| `chat()` 抛异常 | WARNING 记录 → 回退 LLM 直答 |
| `chat()` 超时（`wire_timeout_seconds`，经 `asyncio.wait_for` 硬超时） | 中断 → 回退 LLM 直答 |
| `chat()` 返回 None / 空响应 | WARNING 记录 → 回退 LLM 直答 |
| 旧"第四步半"规划段（`planning_mode` 触发） | wire 规划成功时被 `_planning_mode=False` 门控，不产生双规划 |
| 运营紧急回滚 | 设环境变量 `PLANNING_WIRE_ENABLED=false` 即可（无需发版） |

## 4. 灰度步骤

1. 默认状态（`wire_enabled=false`）：生产行为与改造前逐字节等价，无需干预；
2. 预发/灰度环境：设 `PLANNING_WIRE_ENABLED=true`（可配合 `PLANNING_WIRE_MIN_COMPLEXITY` 调触发面），
   观察 `orchestrator.process.wire.*` 日志与响应 metadata `routed_by=planning` 是否按预期出现；
3. 观察项：wire 规划成功率 / 回退率（`orchestrator.process.wire.fallback` WARNING 计数）、
   链路耗时是否超预算、是否出现双规划（旧段未抑制）；
4. 稳定后逐步放大流量；异常随时 `PLANNING_WIRE_ENABLED=false` 一键回滚。

## 5. 验证记录

### 5.1 测试

```
$ python -m pytest tests/unit/test_planning_wire.py tests/unit/test_planning_defect_d7.py -v
9 passed in 3.99s（新用例 5 + D7 转正 4）

$ python -m pytest tests/unit/test_planning_stage5_e2e.py \
    tests/unit/test_planning_defect_d7.py \
    tests/unit/test_planning_wire.py \
    tests/unit/test_orchestrator_reject.py \
    tests/unit/test_orchestrator_refactor.py -q
134 passed（含 stage5 E2E 旧 D7 段 10 个，确认旧段未被破坏）
```

> 注：`python -m pytest tests/unit -q` 全量回归在本地沙箱中因 chromadb（pydantic_settings 访问
> 系统路径）被沙箱拦截无法完成；已跑与本次改动直接相关的 orchestrator/planning 全量子集 134 例全绿。
> CI 环境无沙箱限制，可执行全量回归确认。

### 5.2 质量门禁

- `python -m agent.observability.arch_rules --check`：✅ 通过（未豁免违规 0，orchestrator→planning 无循环依赖）。
- `python scripts/pre_commit_ci_guard.py --static-only --strict`：⚠️ FAIL 12 条新增 WARN，均为
  `import_degraded` 类型，指向 api_gateway.py / lazy_loader_async.py / optimized_storage.py /
  chaos_injector.py / loki.py 等 **HEAD 存量文件**（`.guard_baseline.json` 未覆盖），
  与本次改动文件（orchestrator.py / config.yaml / 两个测试文件）零交集，非本任务引入。
- `ruff check`（本次改动文件）：✅ 通过（orchestrator.py 存量 12 处 F401/F821/F841 与本次改动无关，未触碰）。

## 6. 变更文件清单

| 文件 | 变更类型 |
| --- | --- |
| `config.yaml` | 修改：`planning:` 段新增 `wire_enabled` / `wire_min_complexity` / `wire_timeout_seconds`（含注释） |
| `agent/orchestrator/orchestrator.py` | 修改：模块级复杂度分级 + `_load_planning_wire_config()` + process() 接线分支 + metadata 标注 |
| `tests/unit/test_planning_wire.py` | 新增：5 用例 |
| `tests/unit/test_planning_defect_d7.py` | 修改：D7 断言转正（4 用例） |
| 本文档 | 新增：变更说明 |

## 7. 范围外（明确不做）

- 不修改 `planning/core.py` 中 `PlanningCore.chat/plan/execute_plan` 签名与行为；
- 不做规划引擎自身重构/优化；
- 不触碰并行会话未提交文件（`agent/knowledge/*`、`tests/unit/*` 等）；
- 旧"第四步半"规划段（commit 439bf52e 引入的 LLM 后规划）本次仅加门控，不删除（待后续任务收口）。
