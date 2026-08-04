# 变更报告：Workflow Learning 自动闭环验证 + 路由可观测性埋点补提交

**日期**: 2026-08-04
**变更类型**: 功能开发（主线 1 Workflow Learning + 主线 2 路由埋点）补提交
**关联提交**:
- `041ceeaa` feat(orchestrator): 主业务链路路由可观测性埋点（主线 2）
- `5f53c393` feat(workflow_learning): 自动闭环验证工具链 + 拦截层单元测试（主线 1）
- 核心代码此前已由 `b6cdf633` 等提交包含（见"四、工作区状态说明"）

---

## 一、变更背景

工作区存在 22 项未提交的功能开发改动，按功能线拆分为 4 条主线。本次完成：

- **主线 1（Workflow Learning 自动闭环）**：LLM 成功交互后自动 `learn_from_interaction` → 工作流学习层拦截（0 Token 短路）→ 达标自动 `convert_to_skill` 升格
- **主线 2（路由可观测性埋点）**：统一层日志（四字段契约）、流量计数、请求上下文、最终路由决策日志
- **主线 3（技能安全扫描）**：文件已从磁盘消失且无法从 git 恢复，按用户决策放弃
- **主线 4（知识库重构）**：按用户决策暂不提交

## 二、变更内容

### 主线 2：路由可观测性埋点（041ceeaa，4 files, +658）

**新增 `agent/orchestrator/routing_observability.py`**（约 294 行）：
- `LAYER_*` 常量（9 个层：input_guard/workflow/template/workflow_learning/semantic/llm/output_guard/reject/behavior）
- `DECISION_*` 常量（9 个决策：hit/miss/block/pass/modified/success/fallback/error/reject）
- `RouteTraffic`：线程安全流量计数（attempts/hits/requests），每 N=50 次请求输出一次 INFO 占比汇总（`ORCHESTRATOR_TRAFFIC_REPORT_INTERVAL` 可配置）
- `RouteContext`：`contextvars.ContextVar` 单请求上下文，`add_layer` 累积各层中间结果，`duration_ms` 自 init 计时
- `log_layer_result()`：统一层日志入口，一次调用完成「结构化日志（四字段契约 trace_id_ctx/layer/decision/duration_ms）+ 流量计数 + 上下文累积」
- `emit_route_decision()`：每请求恰好一条最终决策日志（final_layer/layer_results/decision_basis/duration_ms）
- 【不易】任何异常静默降级为 DEBUG，不阻断主链路

**新增 `tests/unit/test_routing_observability.py`**（约 320 行）：四字段契约/级别约定/流量计数/上下文累积/决策日志/失败隔离，17 用例。

**新增 `scripts/verify_routing_logging.py`**（约 160 行）：采样验证两链路（语义命中短路径 / LLM 兜底长路径），捕获 handler 断言四字段/决策/计数/链路还原。

**修改 `docs/observability/intent_routing_logging.md`**：移除 2 个指向已丢失文件的失效链接（`trace_tracking_report.md` / `trace_tracking_sample_logs.json`），修复 pre-commit 链接预检 BLOCK。

### 主线 1：Workflow Learning 验证工具链（5f53c393，5 files, +933）

**新增 `tests/unit/test_orchestrator_workflow_learning_layer.py`**（约 320 行）：拦截层匹配/自动学习钩子/步骤提取，19 用例（TestExtractToolCallsFromSteps/TestWorkflowLearningLayerMatch/TestLearnWorkflowFromInteraction）。

**新增 `scripts/simulate_workflow_closed_loop.py`**（约 140 行）：8 轮闭环模拟（R1 学习 conf=0.3 → R2 门控未命中 → R3-R5 execute_by_id 收敛 0.18/0.33/0.45 → R6/R7 拦截命中+稳定拦截 skipped_llm=True → R8 无关未命中），临时目录数据隔离 + Mock ToolExecutor。

**新增 `scripts/parse_wfl_interception_logs.py`**（约 250 行）：解析 `orchestrator.wfl.{hit,miss,exec_failed,error,learned}` 日志，兼容 JSON 行与 dict repr，输出 markdown 报表（总体/按工作流/score&conf 分桶/延迟 P50/P95/P99）。

**新增 `scripts/stress_workflow_interception_upgrade.py`**（约 220 行）：预种 N 个达标工作流，阶段 A 基线 vs 阶段 B 升格线程干扰（后台 list_convertible + convert_to_skill mock），对比 P50/P95/P99/命中率。

**新增 `docs/WORKFLOW_LEARNING_PATCH_NOTES.md`**：两个补丁的变更说明（min_score 透传、TF-IDF 单文档退化修复），含兼容性分析和三义自检。

## 三、修复的问题（验证过程中发现并修复）

| # | 文件 | 问题 | 修复 |
|---|------|------|------|
| 1 | `tests/unit/test_routing_observability.py` | 6 处失败：5 处 JSON 风格断言（`"layer": "semantic"`）不匹配实际输出；1 处 `NameError: LAYER_TEMPLATE` 缺 import | 断言改为 Python dict repr 风格（`'layer': 'semantic'`）；import 补 `LAYER_TEMPLATE` |
| 2 | `scripts/verify_routing_logging.py` | 4 处 JSON 断言（`"final_layer": "semantic"` 等）导致 `StopIteration` | 同样改为 dict repr 风格 |
| 3 | `scripts/simulate_workflow_closed_loop.py` | Windows 下 `TemporaryDirectory.__exit__` 清理报 `NotADirectoryError: [WinError 267]`（repo 持有句柄），主循环断言全通过但退出码 1 | `tempfile.TemporaryDirectory(..., ignore_cleanup_errors=True)`（Python 3.10+） |
| 4 | `docs/observability/intent_routing_logging.md` | 引用已丢失文件的 2 个失效链接，pre-commit 链接预检 BLOCK | 删除失效链接行 |

## 四、工作区状态说明（重要）

调查发现：恢复 stash 主线 1+2 后，**核心代码修改已在 HEAD 中**（`b6cdf633` "fix: 补充提交遗漏的技能管理系统/工作流学习系统代码" 等提交已包含 orchestrator.py 的 WFL 层匹配/自动学习、lifecycle_manager.py 的自动升格、matcher.py 的 TF-IDF 平滑、executor/service 的 min_score 透传、config.yaml 配置）。本次提交仅补上了遗漏的 8 个未跟踪文件。

- 主线 1+2 核心代码：已在 HEAD（无需提交）
- 本次补提交：8 个未跟踪文件（4 个属主线 2 + 4 个属主线 1）
- 主线 3 文件（SECURITY_SCAN_RULES.md / test_security_scanner_malicious_skill.py / reviewer.py）：已丢失，git 无法恢复
- stash@{0}：无关改动 13 个文件保留未提交（监控文件单独提交到 develop）

## 五、验证结果

| 验证项 | 命令 | 结果 |
|--------|------|------|
| 单元测试 | `pytest tests/unit/test_routing_observability.py tests/unit/test_orchestrator_workflow_learning_layer.py tests/unit/test_workflow_learning.py` | 49/49 通过 |
| 集成测试 | `pytest tests/integration/test_orchestrator三层路由_e2e.py` | 11/11 通过 |
| 合计 | 上述 4 文件全量 | **60/60 通过** |
| 闭环模拟 | `python scripts/simulate_workflow_closed_loop.py` | ✅ 8 轮全部符合预期（学习→门控→收敛→拦截→降级），EXIT=0 |
| 埋点采样 | `python scripts/verify_routing_logging.py` | ✅ EXIT=0 |
| 日志解析 | `python scripts/parse_wfl_interception_logs.py --demo` | ✅ EXIT=0 |
| 核心不变量 | pre-commit `verify_core_invariants.py` | ✅ 12/12 通过 |
| 文档链接 | pre-commit 链接预检 | ✅ 593 链接 0 失效 |
| 锚点回归 | pre-commit 锚点回归测试 | ✅ 4/4 通过 |

## 六、经验教训（复盘要点）

1. **日志格式断言陷阱**：`log_dict()` 返回 dict 直接传给 logger，日志 message 是 **Python dict repr**（`{'layer': 'semantic'}`），非 JSON 行。测试断言应匹配单引号格式（`"'layer': 'semantic'"`），不要用 JSON 双引号。
2. **Windows tempfile 清理陷阱**：`TemporaryDirectory` 在 workflow repo 持有句柄时清理报 `NotADirectoryError: [WinError 267]`，需 `ignore_cleanup_errors=True`。
3. **工作区漂移风险**：核心代码可能被其他工作流/自动提交先行包含，提交前必须 `git status` 确认哪些文件真实未提交，避免重复提交或遗漏未跟踪文件。
4. **pre-commit 链接预检是硬门槛**：引用已丢失文件的链接会 BLOCK 提交，需先修复（删除失效引用或恢复文件）。
5. **git 陷阱**：不用 `git commit -- <paths>` 形式提交（pre-commit 运行期间工作区修改会被还原）；`TLM_HOOK_SOURCE_REPO` 环境变量需在同一命令内设置（每个 RunCommand 是新终端，环境变量不共享）。
