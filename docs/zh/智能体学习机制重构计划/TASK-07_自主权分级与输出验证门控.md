# TASK-07：自主权分级与输出验证门控

## 0. 任务标识

| 字段 | 值 |
|---|---|
| 任务编号 | TASK-07 |
| 所属阶段 | 并行轨道（安全护栏，完全独立） |
| 前置依赖 | 无 |
| 并行建议 | 可与任意任务并行 |
| 涉及主文档 | `docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§3.2 D 相关；§4.4 断点 7、8） |

## 1. 背景（为什么做）

设计思路要求"自主权分级管控（L1-L5）"与"验证门控（功能/安全/泛化）"。

审计发现云枢现状：

1. **无 L1-L5 行为自主权分级**：全仓库检索 `autonomy|L1..L5|自主权` 命中均为缓存层级（L1/L2/L3 cache）与 TLM 注释标签。实际管控是**三套正交机制**：PermissionSystem（两级阻断/确认）、ThreeLevelCircuitBreaker（SESSION→USER→GLOBAL 三级熔断）、GracefulDegrade（NORMAL→…→EMERGENCY 五级健康降级）。三者均非"按自主程度划分的行为分级"。
2. **输出验证门控缺位**：`HealthAssessor` 是被动采样评分器（3 维、硬编码阈值、无拦截出口），不是门控；系统真实门控仅两处——`workflow_learning/skill_converter.py` 的学习产物门控与 orchestrator DST 上下文软门控。**LLM 输出内容/质量的运行时验证不存在**（`config.yaml verification.schema_validation.enabled=true` 已有 Schema 校验骨架，但 `schema_validation_enabled: false`、`critic_evaluation_enabled: false`，且 Schema 校验针对结构化输出，非内容质量）。

## 2. 目标描述（做什么）

1. 落地 **L1-L5 行为自主权分级**：定义分级表并映射到既有三套机制，形成**聚合视图 + 运行时上下文注入**（不改既有执行链语义），支持按会话/用户配置自主等级。
2. 实现**轻量输出验证门控**：规则校验（格式/长度/关键字段）+ 可选 LLM-as-Judge（保守模式默认仅记录），在 orchestrator 响应返回前执行，失败→重试或保守降级。

## 3. 不变式约束（不易——禁止触碰）

- **禁止修改** `permission_system.py` / `circuit_breaker.py` / `graceful_degrade.py` 现有判定逻辑与接口（L1-L5 只做**映射与视图**，不改变任一现有机制的触发语义）。
- **禁止修改** orchestrator 既有 OutputGuard（PII 遮盖）行为——新门控在其后追加，不替换。
- **保留** `verification.conservative_mode: true` 语义：门控默认只记录不拦截。
- **保留** `config.yaml` 现有 `verification.*` 配置结构（新增段可加，旧段不动）。
- LLM-as-Judge 本期只做**接口预留 + 规则降级**（参照 `critic.py` 的 `RULE_BASED`/`LLM_DRIVEN` 双模式惯例），默认走规则；禁止在默认路径引入额外 LLM 调用成本。

## 4. 执行步骤

### Step 1：L1-L5 分级定义与映射（先文档后代码）
在变更说明中先落分级表：

| 等级 | 行为边界 | 映射到现有机制 |
|---|---|---|
| L1 只读观察 | 仅感知/检索/对话，零副作用 | 工具白名单=只读集；PermissionSystem 全黑名单兜底 |
| L2 低风险自主 | 可执行低风险工具（检索/计算/读文件） | 现有 BLOCKLIST 之外的默认路径 |
| L3 中风险需确认 | 写文件/改配置等需二次确认 | `DANGEROUS_PATTERNS`/`SENSITIVE_DIRS` 确认链 |
| L4 高风险专家 | 系统级操作，全审计 | 熔断 SESSION 级 + 审计日志 + HITL 确认 |
| L5 完全自主 | 受全局熔断与日配额约束的全能力 | GLOBAL 级熔断 + `rate_limiter` 配额 |

- 新增 `agent/autonomy.py`：`AutonomyLevel` 枚举 + `AutonomyPolicy`（等级→工具白名单/确认要求/审计要求映射，纯声明式）+ `AutonomyContext`（当前会话等级，ContextVar 注入，遵 `agent/monitoring/tracing.py` 的 ContextVar 模式）。
- 运行时接入：`agent/permission_system.py` 现有 `check()` 调用点注入 AutonomyContext（**只读该上下文做策略聚合，不改其判定结果**）；orchestrator 的 tool 执行前根据等级聚合"确认/审计"要求。
- 默认等级：`config` `autonomy.default_level: L3`（保守），支持环境变量/会话级覆盖。

### Step 2：输出验证门控
新增 `agent/verification/output_validator.py`：
- `OutputValidator.validate(response, task_type) -> Verdict(ok, issues, score)`：
  - **规则层**（零 Token）：空输出/超长截断/关键字段缺失/格式不符（复用 `verification.schema_validation` 的 supported_types 声明）/PII 泄漏（复用既有脱敏规则反查）；
  - **LLM 层**（接口预留）：`mode=rule_based`（默认）时跳过；`mode=llm_based` 且配置启用时调 LLM 判相关性/完整性（复用 `verification.critic_evaluation.llm_config`）。
- 在 orchestrator 响应返回前（OutputGuard 之后）追加调用：
  - `conservative_mode=true`（默认）：仅记录 verdict 到 metrics（`learning.eval_*` 复用 TASK-02 指标族）与审计日志；
  - `conservative_mode=false` 且 `verification.schema_validation.enable_retry`：失败→按现有重试配置重试一次→仍失败返回原响应（不阻断，降级保底）。
- **不做**：不拦截不重试时不得丢弃用户响应。

### Step 3：补测试（TDD）
新增 `tests/unit/test_autonomy.py` + `tests/unit/test_output_validator.py`：
- 分级：5 个等级各自映射正确；L1 会话下尝试写操作被聚合策略标注为越级（既有机制仍按原语义执行）；等级可会话级覆盖。
- 门控：规则层命中 4 类失败样例（空/超长/缺字段/PII）；conservative 模式只记录；非保守+retry 走重试；LLM 层未配置时静默降级规则；验证器抛错主链路正常。

### Step 4：回归与门禁
- `python -m pytest tests/unit -q` 全绿；新用例全绿；质量门禁见 §6。

## 5. 预期成果（交付物）

1. `agent/autonomy.py`（L1-L5 枚举/策略/上下文）+ `agent/verification/output_validator.py`。
2. orchestrator 接入输出门控（保守模式）与自主权上下文注入。
3. 配置：`autonomy.default_level` / `autonomy.per_level_policy` / `verification.output_validator.*`（含 mode 默认 rule_based、conservative 默认 true、注释）。
4. 测试：2 个新测试文件（≥ 14 用例）。
5. 变更说明：`docs/zh/智能体学习机制重构计划/变更说明/TASK-07_变更说明.md`（含 L1-L5 分级表与既有三套机制的映射矩阵）。

## 6. 评估标准（验收条件）

### 功能验收
- [ ] L1-L5 分级表文档化且代码映射与表一致（表格驱动的单向验证）。
- [ ] L1 会话下执行写工具：聚合视图标注越级（可查询）；既有 PermissionSystem 行为零变化（对照改造前用例）。
- [ ] 输出验证在 4 类构造失败样例上全部命中；`conservative_mode=true` 时用户响应零影响。
- [ ] 验证器/分级上下文异常时 orchestrator 主链路正常（降级验证）。
- [ ] 默认路径（mode=rule_based）零额外 LLM 调用（可用 LLMMonitor 断言无增量调用）。

### 测试要求
- [ ] 新增 ≥ 14 用例全部通过；`python -m pytest tests/unit -q` 全绿。

### 质量门禁
- [ ] `python scripts/pre_commit_ci_guard.py --static-only --strict` 零新增告警。
- [ ] `python -m agent.observability.arch_rules --check` 通过。

## 7. 工程约束（仓库规则）

- 同 TASK-01 §7（git 精确路径、commit -F、hook 环境变量、勿碰并行会话文件、UTF-8 无 BOM）。
- 本任务涉及安全边界：**任何"修改既有拦截语义"的实现一律不通过**；L1-L5 只能是聚合视图+上下文注入。
- `agent/permission_system.py` 有既有单测（`tests/unit/test_api_gateway.py` 等），改动后必须跑相关安全类测试。
