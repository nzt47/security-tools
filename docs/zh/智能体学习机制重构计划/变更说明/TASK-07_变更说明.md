# TASK-07 变更说明：自主权分级与输出验证门控

| 字段 | 值 |
| --- | --- |
| 任务编号 | TASK-07 |
| 所属阶段 | 并行轨道（安全护栏，完全独立） |
| 前置依赖 | 无 |
| 涉及主文档 | `docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§3.2 D 相关；§4.4 断点 7、8） |
| 关联任务书 | `docs/zh/智能体学习机制重构计划/TASK-07_自主权分级与输出验证门控.md` |
| 实现日期 | 2026-08-14 |

## 1. 背景

设计思路要求"自主权分级管控（L1-L5）"与"验证门控（功能/安全/泛化）"。审计发现云枢现状：

1. **无 L1-L5 行为自主权分级**：实际管控是三套正交机制（PermissionSystem 两级阻断/确认、ThreeLevelCircuitBreaker SESSION→USER→GLOBAL 三级熔断、GracefulDegrade NORMAL→…→EMERGENCY 五级健康降级），均非"按自主程度划分的行为分级"；
2. **输出验证门控缺位**：`HealthAssessor` 是被动采样评分器，系统真实门控仅两处（`workflow_learning/skill_converter.py` 学习产物门控、orchestrator DST 上下文软门控），LLM 输出内容/质量的运行时验证不存在。

## 2. 不变式约束（未触碰清单）

| 约束 | 落实情况 |
| --- | --- |
| 禁止修改 `permission_system.py` / `circuit_breaker.py` / `graceful_degrade.py` 判定逻辑与接口 | ✅ 零改动；L1-L5 仅做映射与视图 |
| 禁止修改 orchestrator 既有 OutputGuard（PII 遮盖）行为 | ✅ 新门控在 [orchestrator.py](file:///c:/Users/Administrator/agent/agent/orchestrator/orchestrator.py) OutputGuard 之后追加，不替换 |
| 保留 `verification.conservative_mode: true` 语义（默认只记录不拦截） | ✅ `output_validator.conservative_mode` 默认 true，兜底读 `verification.conservative_mode` |
| 保留 `config.yaml` 现有 `verification.*` 配置结构 | ✅ 仅新增 `verification.output_validator.*` 新段 |
| LLM-as-Judge 只做接口预留 + 规则降级，默认路径零额外 LLM 调用 | ✅ `mode=rule_based` 默认；`llm_based` 未配置时静默降级规则层 |

## 3. L1-L5 分级表与映射矩阵（表格驱动的单向验证锚点）

| 等级 | 行为边界 | 允许工具类别 | 聚合确认范围 | 审计 | 映射到现有机制 |
| --- | --- | --- | --- | --- | --- |
| L1 只读观察 | 仅感知/检索/对话，零副作用 | 只读集（READONLY） | all（一切非只读需确认） | 是 | 工具白名单=只读集；PermissionSystem 全黑名单兜底 |
| L2 低风险自主 | 可执行低风险工具（检索/计算/读文件） | READONLY + LOW_RISK | none | 否 | 现有 BLOCKLIST 之外的默认路径 |
| L3 中风险需确认（默认） | 写文件/改配置等需二次确认 | + MEDIUM_RISK | medium_and_above | 否 | `DANGEROUS_PATTERNS`/`SENSITIVE_DIRS` 确认链 |
| L4 高风险专家 | 系统级操作，全审计 | 全类别 | high_only | 是 | 熔断 SESSION 级 + 审计日志 + HITL 确认 |
| L5 完全自主 | 受全局熔断与日配额约束的全能力 | 全类别 | none | 否 | GLOBAL 级熔断 + `rate_limiter` 配额 |

代码锚点：`agent/autonomy.py::AutonomyPolicy.POLICY_TABLE` 与上表逐行一致（单测 `TestLevelMappingTable` 逐行校验）。

> 映射是**聚合视图**：等级策略的"确认范围/审计要求"叠加为视图字段，不改变任一既有机制的触发语义（PermissionSystem 判定结果原样透传 `AutonomyVerdict.base_*`）。

## 4. 改动点

### 4.1 新增 `agent/autonomy.py`（Step 1 L1-L5 分级）

| 符号 | 说明 |
| --- | --- |
| `AutonomyLevel`（枚举 L1-L5） | 等级定义，`from_value` 宽容解析（'L3'/'l3'/'3'），非法回退 L3 |
| `ToolCategory`（readonly/low_risk/medium_risk/high_risk） | 工具行为分类（声明式关键字表 `classify_action`） |
| `LevelPolicy`（frozen dataclass） | 单等级策略：允许类别/确认范围/审计要求/机制说明 |
| `AutonomyPolicy` | `POLICY_TABLE` 分级表 + `aggregate()` 聚合视图 + `table()` 导出 + 配置覆盖 |
| `AutonomyVerdict` | 聚合视图 dataclass（`to_dict()` 可查询），`base_*` 原样透传 |
| `AutonomyContext` / `get_autonomy_level()` | ContextVar 上下文注入（遵 `agent/monitoring/tracing.py` Token 式恢复模式，`__exit__` 绝不抛异常） |
| `set_session_level()` / `resolve_autonomy_level()` | 会话级覆盖注册表（RLock 仅内存变更）+ 等级解析 |

等级解析优先级：**会话级覆盖 > `AUTONOMY_DEFAULT_LEVEL` 环境变量 > `config.yaml autonomy.default_level` > 默认 L3**。

### 4.2 新增 `agent/verification/output_validator.py`（Step 2 输出验证门控）

| 符号 | 说明 |
| --- | --- |
| `Verdict` | 验证结果（ok/issues/score/mode/retried/degraded） |
| `OutputValidator.validate(response, task_type)` | 规则层 5 类检查（空输出/超长/缺关键字段/格式不符/PII 泄漏）+ LLM 层接口预留 |
| `OutputValidator.check_and_act(...)` | 门控处置：保守模式仅记录；非保守 + `enable_retry` 经 `retry_fn` 重试一次，仍失败返回原响应（降级保底，不丢弃） |
| `load_validator_config()` / `build_validator_from_config()` | 配置加载与构建（失败降级默认值） |

规则层细节：
- **空输出**：`None`/空白 → `empty_output`（score 0）；
- **超长**：`len > max_output_length` → `output_too_long`；
- **缺关键字段**：按 `task_type` 声明式标记（summary_report 需含 结论/总结/摘要 等），全部缺失 → `missing_required_field`；
- **格式不符**：`task_type` 不在 `verification.schema_validation.supported_types` 声明内 → `unsupported_task_type`；
- **PII 泄漏**：复用 `agent/guardrails/output_guard.py` 的 `_pii_patterns()` 反向查找（应被遮盖而未遮盖）→ `pii_leak`；
- **LLM 层**（接口预留）：`mode=llm_based` 且 `llm_client`/`llm_config` 配置启用时调用（复用 `critic_evaluation.llm_config`）；未配置静默降级返回 None 由规则层兜底，默认路径零 LLM 调用。

记录通道（保守模式）：`learning.eval.total / passed / failed` 计数器（复用 TASK-02 指标族，`LearningMetrics._get_eval_stats` 聚合）+ 结构化审计日志；埋点/日志异常内部吞掉，主链路零影响。

### 4.3 orchestrator 接入（Step 2 运行时）

[orchestrator.py](file:///c:/Users/Administrator/agent/agent/orchestrator/orchestrator.py) 三处最小改动：

1. `chat()`：进入前 `resolve_autonomy_level(session_id)` → `with AutonomyContext(level)` 包裹 `process()`（会话级自主权上下文注入）；
2. `process()` 第五步半：OutputGuard 之后追加 `_output_validator.check_and_act(response, task_type="text_response")`（懒加载属性 `_output_validator`，构建失败返回 None 主链路零影响；保守模式下响应零变化）；
3. 新增 `check_action_with_autonomy(action, context)`：**可查询**的自主权聚合视图（内部仍调 `_permission.check_action`，既有判定语义零变化；`request_permission` 保持原样）。

### 4.4 配置（Step 2）

- `config.yaml` 新增顶层 `autonomy:` 段：`default_level: L3`（默认保守）、`per_level_policy: {}`（按需覆盖）；
- `config.yaml` `verification:` 段新增 `output_validator:` 子段：`enabled/mode(rule_based)/conservative_mode(true)/max_output_length(8000)/enable_retry(true)/max_retries(1)`；
- 旧段全部不动。

## 5. 测试

新增 `tests/unit/test_autonomy.py`（22 用例）+ `tests/unit/test_output_validator.py`（19 用例），共 **41 用例**（要求 ≥14）：

- 分级：5 等级映射与任务书表格逐行一致；L1 会话写工具聚合视图标注越级（可查询）且既有判定零变化；L3 写操作视图叠加确认要求；L4 审计；L5 无越级；会话级覆盖；默认 L3；ContextVar 栈式恢复与异常安全；环境变量覆盖；非法等级回退；配置覆盖不污染默认表；
- 门控：4 类失败样例（空/超长/缺字段/PII）+ 格式不符全部命中；保守模式只记录、响应零影响、写 `learning.eval.*`；非保守 + retry 重试成功/仍失败返回原响应/无 retry_fn 不丢弃；默认 rule_based 零 LLM 调用；llm_based 未配置静默降级；验证器抛错降级返回原响应；retry_fn 抛错不崩溃；分数叠加扣分。

回归结果：
- 新增用例 41 passed（含 2 新文件）；
- 安全相关：`test_permission_system.py` + `test_permission_edge_cases.py` + `test_verification.py` + `test_routing_observability.py` 107 passed（PermissionSystem 行为零变化对照）；
- orchestrator 主链路：`test_orchestrator_refactor.py` + `test_response_workflows.py` + `test_response_builder.py` 121 passed；`test_orchestrator三层路由_e2e.py -m "not slow"` 9 passed；
- 配置/护栏：`test_routes_config_validation.py` + `test_scripts_config_snapshot.py` + `test_safety_guard.py` + `test_skill_output_guard.py` + `test_digital_life_comprehensive.py` 155 passed。

全量单测回归（`python -m pytest tests/unit -q --timeout=300`，排除 2 个已验证环境噪音文件，35:01）：

- **11538 passed / 299 skipped / 13 xfailed / 12 xpassed，0 真实失败**（TASK-07 相关 test_autonomy 22 + test_output_validator 19 + orchestrator 主链路 + api_gateway 全绿）；
- 16 failed + 1 error 逐项核实**全部为并行会话半成品产物**（`M agent/subagent/sandbox.py` + 新增 `test_sandbox_execution_guard.py` 10 失败、`M sensor/behavior_sensor.py` + `test_behavior_drift.py` 1 error、`M sensor/change_detector.py` + 新增 `test_novelty_pipeline.py` 3 失败、`?? agent/monitoring/lock_watchdog.py` 引入 `lock_hold_timeouts_total` 指标 2 失败），与本任务零关联；
- 2 个环境噪音文件（并行负载下 `t.join()`/子进程超 60s 被 pytest-timeout 掐死，单独运行均通过）在排除后单独验证：`test_ci_guard_fix_regression.py` 19 passed（65.9s）、`test_web_search_concurrency.py` 5 passed（52.1s）；
- 判定依据：pytest 汇总行 `= 16 failed, 11538 passed ... in 2101.34s =`（rc=1 系 teardown 阶段 sandbox 拦截 `C:\nonexistent` 写入所致，非测试失败，与 T-4 教训(4) 一致）。

## 6. 质量门禁

- `python -m agent.observability.arch_rules --check` ✅ 通过（0 新增未豁免违规）；
- `python scripts/pre_commit_ci_guard.py --static-only --strict` ✅ FAIL=0，新增阻断 WARN=0。

## 7. 交付物清单

1. `agent/autonomy.py`（L1-L5 枚举/策略/上下文/聚合视图）；
2. `agent/verification/__init__.py` + `agent/verification/output_validator.py`；
3. `agent/orchestrator/orchestrator.py`（3 处最小接入）；
4. `config.yaml`（`autonomy.*` + `verification.output_validator.*`）；
5. `tests/unit/test_autonomy.py` + `tests/unit/test_output_validator.py`（41 用例）；
6. 本变更说明。

## 8. 已知边界与后续

- L1-L5 聚合视图目前由 orchestrator 层 `check_action_with_autonomy` 与 `agent/autonomy.py` 纯函数提供；`agent/tools/*.py` 内直接调 `dl._permission.check_action` 的执行路径保持原样（行为零变化），如需工具级越级告警可在各工具内调 `AutonomyPolicy.aggregate` 读取视图；
- `mode=llm_based` 的 LLM 调用实现为接口预留（本期不产生默认路径调用），后续接入需在 `_validate_with_llm` 中实现并挂接 `critic_evaluation.llm_config`；
- 会话级覆盖目前是进程内注册表（`set_session_level`），跨进程/持久化按需扩展。
