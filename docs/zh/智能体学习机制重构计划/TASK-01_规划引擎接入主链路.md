# TASK-01：规划引擎接入主链路

## 0. 任务标识

| 字段 | 值 |
|---|---|
| 任务编号 | TASK-01 |
| 所属阶段 | 主线阶段 1/5（闭环贯通起点） |
| 前置依赖 | 无 |
| 并行建议 | 可与 TASK-03、TASK-07 并行 |
| 涉及主文档 | `docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§4.4 断点 1） |

## 1. 背景（为什么做）

云枢的规划引擎 `planning/` 是一套完整实现（PlanningCore / ReActLoop / TaskDecomposer / Reflector / PlanDB），但处于**"已建未用"**状态：`config.yaml` 中 `planning.enabled: true`，而 orchestrator 主链路（`agent/orchestrator/orchestrator.py` 的 `process()`）仅在 LLM 调用后追加一行 `get_stats()` 统计文本，**从不调用 `_planner.chat()` / `plan()` / `execute_plan()`**。

- 证据：`tests/unit/test_planning_defect_d7.py` 明确断言该缺陷（当前 xfail）。
- 影响：设计思路中"规划层"与"反思层"（Reflector 的 plan_reflect / learn_from_experience）全部无法被生产流程触发，是整个"执行→反思→沉淀"闭环的地基断点。
- 本任务只解决"接线"，不新增规划能力。

## 2. 目标描述（做什么）

将 `PlanningCore.chat()`（ReAct 路径）以**保守灰度**方式接入 orchestrator 主链路：当任务被判定为复杂任务且灰度开关开启时，走规划引擎；规划引擎任何异常/超时/失败一律回退到原有 LLM 路径（守主链路稳定，0 行为退化）。

## 3. 不变式约束（不易——禁止触碰）

- **禁止删除**：orchestrator 现有 `process()` 链路步骤（InputGuard→Workflow→感知检查→DST→意图路由→LLM→OutputGuard→反思→记忆保存）一个都不能少，规划只在"LLM 调用前"插入新分支。
- **禁止修改**：`planning/core.py` 中 `PlanningCore.chat/plan/execute_plan` 的现有签名与行为（D7 修复只接线，不改引擎）。
- **保留**：`planning.enabled` 现有语义；`config.yaml` 注释中"环境变量 > config.yaml > 硬编码默认值"优先级约定。
- **保留**：现有 `test_planning_defect_d7.py` 的断言意图（只把 xfail 转正，不删除用例）。
- 灰度开关默认 `false`，正式环境未开启前生产行为**必须与现状逐字节等价**。

## 4. 执行步骤

### Step 1：新增灰度配置
在 `config.yaml` 的 `planning:` 段下新增（含中文注释，说明 Why）：
```yaml
planning:
  # TASK-01：规划引擎接入主链路（D7 接线）
  # 开关默认 false（灰度）；true 时复杂任务走 PlanningCore.chat()，异常自动回退 LLM
  wire_enabled: false
  # 触发规划的最低复杂度：TRIVIAL / SIMPLE / NORMAL / COMPLEX（复用 enhanced_planner 语义）
  wire_min_complexity: COMPLEX
  # 规划调用超时（秒），超时回退 LLM
  wire_timeout_seconds: 30
```
同时按项目约定支持环境变量覆盖（如 `PLANNING_WIRE_ENABLED`）。

### Step 2：在 orchestrator 插入规划分支
修改 `agent/orchestrator/orchestrator.py` 的 `process()`：
- 在**意图路由判定为需要 LLM 之后、调用 LLM 之前**，检查 `planning.wire_enabled` 且任务复杂度 ≥ `wire_min_complexity`（复杂度判定复用现有 `task_dispatcher.py` / 阈值逻辑，若没有则先用简单规则：关键词+动作词加权，参考 `PlanningCore._needs_planning()` 现成逻辑）。
- 命中则调用 `self._planner.chat(message, context)`（注意加超时保护，用 `asyncio.wait_for` 或同步超时包装）。
- **回退铁律**：`chat()` 抛异常 / 超时 / 返回异常结果 → 静默降级走原 LLM 路径，日志 WARNING 记录降级原因（不中断用户请求）。
- 规划成功时，将 ReAct 结果以原响应的形式继续走后续链路（OutputGuard→反思→记忆保存），并在响应元数据标注 `routed_by: planning`（供 TASK-03 埋点）。

### Step 3：接线 LifecycleManager 确认
检查 `agent/orchestrator/lifecycle_manager.py` 的 `_initialize_planning_engine()`，确认 `PlanningCore` 单例在 `process()` 中可访问；若当前仅生命周期持有实例，则在 orchestrator 中通过单例/懒加载获取，**不要重复实例化**（遵项目单例规范 `agent/utils/singleton_manager.py`）。

### Step 4：补测试（TDD，先写后改）
新增 `tests/unit/test_planning_wire.py`（或扩展现有 defect 测试文件）：
- 用例 1：`wire_enabled=false` 时 process() 不调用 `_planner.chat()`（mock 断言零调用）。
- 用例 2：`wire_enabled=true` + COMPLEX 任务 → 调用 `_planner.chat()` 且响应为规划结果。
- 用例 3：`wire_enabled=true` + `chat()` 抛异常 → 回退 LLM 路径，响应正常，日志含降级 WARNING。
- 用例 4：`wire_enabled=true` + 简单任务 → 不触发规划。
将 `test_planning_defect_d7.py` 的 xfail 移除，改为实断言（`wire_enabled=true` 时 orchestrator 调用 `_planner.chat()`；可通过 fixture 注入配置）。

### Step 5：回归与门禁
- 跑 `python -m pytest tests/unit -q`（全量回归不破坏）。
- 跑 `python -m pytest tests/unit/test_planning_defect_d7.py tests/unit/test_planning_wire.py -v`（新用例全绿）。
- 跑质量门禁（见 §6）。

## 5. 预期成果（交付物）

1. `config.yaml` 新增 `planning.wire_*` 三配置（含注释），支持环境变量覆盖。
2. `agent/orchestrator/orchestrator.py` 的 `process()` 新增规划分支 + 回退逻辑（代码量控制在 40 行内，保持可读）。
3. 新增 `tests/unit/test_planning_wire.py`（4 用例）+ `test_planning_defect_d7.py` xfail 转正。
4. 变更说明文档：`docs/zh/智能体学习机制重构计划/变更说明/TASK-01_变更说明.md`（背景/改动点/回退机制/灰度步骤/验证记录）。

## 6. 评估标准（验收条件）

### 功能验收
- [ ] `wire_enabled=false` 时，对同一组输入，process() 输出与改造前一致（可用 git stash 对比或快照比对）。
- [ ] `wire_enabled=true` 时，COMPLEX 任务确实走规划路径（日志/响应元数据可证）；简单任务不受影响。
- [ ] 模拟 `chat()` 抛错/超时，用户仍获得正常 LLM 响应（降级不中断）。

### 测试要求
- [ ] 新增用例 ≥ 4 个且全部通过；D7 测试 xfail 已移除并转正通过。
- [ ] `python -m pytest tests/unit -q` 全绿（不新增任何失败）。

### 质量门禁
- [ ] `python scripts/pre_commit_ci_guard.py --static-only --strict` 零新增告警。
- [ ] `python -m agent.observability.arch_rules --check` 通过（禁止 orchestrator→planning 引入循环依赖；若规划模块依赖 orchestrator 导致循环，用 `lazy_loader` 延迟导入并注明豁免理由）。

## 7. 工程约束（仓库规则）

- git：只 add 本任务相关文件（精确路径，禁止 `git add -A`/`git add .`）；commit 走 `-F` 提交信息文件（PowerShell 不支持 heredoc）；pre-commit/pre-push hook 需 `TLM_HOOK_SOURCE_REPO` 环境变量，失败时按 hook 提示处理（编码检查可用 `SKIP_ENCODING_CHECK=1` 跳过，但链接/锚点预检保留）。
- 工作区存在大量并行会话未提交文件（`agent/knowledge/*`、`tests/unit/*` 等），**禁止触碰、禁止误提交**。
- 新文件 UTF-8 无 BOM（.py）；.ps1 若涉及需 UTF-8 BOM。
- 不做超出本任务范围的优化（如顺手重构 planning 引擎）。
