# 任务5：LLM-as-Judge 双假设验证 — 交付结案报告

> 日期：2026-08-22 | 项目：云枢自进化机制重构计划 · 阶段三 | 任务：任务5
> 关联：[任务5_LLM-as-Judge双假设验证.md](任务5_LLM-as-Judge双假设验证.md)（任务提示词）
> 关联：[任务5_LLM-as-Judge双假设验证_变更说明.md](任务5_LLM-as-Judge双假设验证_变更说明.md)、
> [任务5_LLM-as-Judge双假设判别报告.md](任务5_LLM-as-Judge双假设判别报告.md)
> 提交：`f076467c`（本任务交付，14 文件 +2683 行）
> 分支：`feat/m2-gitleaks`（已随并行会话推送入库：origin/GitHub = HEAD，gitee/Gitee 含本提交）

---

## 一、结案总论

**任务5 全部验收达标，LLM-as-Judge 双假设验证可结案。** 本任务解决元审计理论缺陷
**T3（LLM-as-Judge 引入逻辑的因果歧义）**：低进化采纳率既可能归因"评估不精细"
（假设 A，需要更精细的 Judge），也可能归因"候选质量差"（假设 B，需要改进候选生成）。
报告 TASK-08 §3.3 只设计了"dry-run 记录 2 周"，没有设计 A/B 判别。

本任务交付：
1. **预算 enforce 前置**：`learning.budget.mode` 灰度提升为 `enforce`（仅学习动作；
   `LEARNING_ACTION_SCOPE` 白名单 + `MAIN_CHAIN_EXCLUDED` 排除清单，主链路零 import
   零调用，测试证明）——解除报告 §3.3 否决条件（warn_only 不强制时一律不引入 LLM 型评估角色）；
2. **Judge dry-run 通道** `agent/learning/judge_channel.py`：同候选集双通道评估
   （规则通道只读回放 vs LLM Judge），**零干预**（不 import 提交/审批/回滚模块，
   审计每条 `intervention=false`，KPI#7 零变化），所有 LLM 调用经 `learning.budget`
   enforce 熔断保护（超限 `budget_blocked` 跳过，不伪造指标、不部分执行）；
3. **判别指标扩展**：`learning_metrics` 新增 `judge_disagreement_rate` /
   `judge_implied_adoption_rate` 等扩展字段（纯增量，既有 KPI 口径零改动）+ 监控
   gauge + 告警规则；
4. **判别规则与报告**：预设判别规则（分歧率 <10% → 假设 B 不引入；分歧率 ≥10% 且
   Judge 采纳率 − 规则采纳率 ≥ +10pp → 假设 A 支持引入；样本不足/证据不足 → 不启用）
   以纯函数实现 + A/B 合成数据单测验证方向；《判别报告》给出**明确建议：当前不启用**
   （有效判定样本 0 < 最小判别基数 5 → `insufficient_data`，复核条件明确）。

**验收核心结论**：
- Judge 通道零干预可证（审计断言 + KPI#7 零变化 + 模块零提交/审批 import）；
- 预算 enforce 拦截生效（超限学习动作被拒，单测证明）；主链路 LLM 调用零影响（测试证明）；
- 判别规则可复现（A/B 两类合成数据结论方向与预设规则一致，单测证明）；
- 判别报告含样本量/分歧率/采纳率差异/token 成本/置信度限制，建议二值明确；
- **未修改既有规则评估器**（`reflection.py` / `critic.py` / `feedback.py` /
  `reviewer.py` / `offline_evolver.py` 零 diff，git 审计）；
- 全程 dry-run，未触发任何"干预模式"（`enabled=false` 默认零 LLM）。

## 二、验收结果对照（任务提示词 §6）

### 内容验收

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| Judge 通道零干预：审计显示判定不影响任何提交/采纳决策 | ✅ 审计每条 `intervention=false`；KPI#7（`evolution_adoption_rate`）零变化；零采纳侧调用；模块不 import 提交/审批/回滚模块 | `test_evaluate_candidates_zero_intervention_audit_provable` / `test_judge_channel_module_has_no_commit_imports` |
| 预算 enforce 拦截生效 + 主链路零影响 | ✅ 超限学习动作被拒（`budget_blocked`/`LearningBudgetExceeded`）；主链路式 LLM 调用在熔断后照常 | `test_budget_enforce_blocks_over_limit_judge_call` / `test_main_chain_llm_unaffected_by_enforce_budget` / `test_enforce_only_affects_learning_actions_main_chain_untouched` |
| 判别规则可复现（A/B 合成数据） | ✅ A 类（分歧 30% + 差异 +30pp）→ 支持引入；B 类（分歧 2%）→ 不引入；端到端批次结论方向一致 | `test_discriminate_hypothesis_a_*` / `test_end_to_end_hypothesis_a_batch` / `test_end_to_end_hypothesis_b_batch` |
| 判别报告含样本量/分歧率/采纳率差异/token 成本/置信度限制 | ✅ 判别报告 §4（口径与快照）/§5（成本核算）/§6（置信度限制） | 《任务5_LLM-as-Judge双假设判别报告.md》 |
| 报告给出明确启用/不启用建议与依据 | ✅ 当前 `not_introduce`（`insufficient_data` + 依据）；复核条件（2 周或候选 ≥5）明确 | 判别报告 §一/§三/§七 |

### 过程验收

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 未修改既有规则评估器行为（git diff 审计） | ✅ `reflection.py` / `critic.py` / `feedback.py` / `reviewer.py` / `offline_evolver.py` / `feedback_agent.py` / `lifecycle.py` / `approval.py` / `rollback.py` 零 diff | `git diff HEAD` 审计（收尾复核） |
| 全程 dry-run，未触发干预模式 | ✅ `LEARNING_JUDGE_ENABLED=false` 默认（零 LLM）；模块无干预路径（import 审计）；审计 `mode=dry_run` | 测试 + 代码审计 |
| 全量回归 | ✅ `python -m pytest tests/unit -q`：12101 passed / 83 failed（全为既有环境性失败：WinError 5 子进程 / sqlite_vec 缺失，无一涉及本任务模块；与本任务相关测试集全绿） | pytest 输出（见第四节） |

## 三、交付物清单

### 新代码（工程约束允许路径）

| 文件 | 说明 |
| --- | --- |
| `agent/learning/judge_channel.py` | Judge dry-run 通道：双通道评估（规则只读回放 + LLM Judge）/ 预算 enforce 前置 / 熔断保护 / 判别规则纯函数 / 审计 / CLI（status/run-batch/discriminate/report） |
| `agent/monitoring/learning_judge_metrics.py` | Prometheus gauge（`yunshu_learning_judge_dryrun` / `yunshu_learning_judge_discrimination`） |

### 修改（工程约束允许路径）

| 文件 | 说明 |
| --- | --- |
| `agent/learning_budget.py` | enforce 灰度：`LEARNING_ACTION_SCOPE` 白名单 + `MAIN_CHAIN_EXCLUDED` 排除清单 + `scope` 声明字段（行为零变化） |
| `agent/learning_metrics.py` | 纯增量扩展：`record_judge_result` + `judge_dryrun` 快照/周级节（不改既有口径；含并行任务7 复杂度扩展共存） |
| `config.yaml` | `learning.budget.mode=enforce` + `scope`；新增 `learning.judge.*`（含并行任务6/7 配置段共存） |
| `.env.example` | `LEARNING_BUDGET_MODE/SCOPE` + `LEARNING_JUDGE_*` 全套开关（含并行任务7 开关共存） |
| `agent/learning/__init__.py` | 包文档补 judge_channel 条目（含并行任务7 条目共存） |
| `monitoring/prometheus.yml` / `monitoring/prometheus/prometheus.yml` | 挂载 `learning_judge_alerts.yml` |
| `monitoring/prometheus/rules/learning_judge_alerts.yml` | 5 条告警（支持引入信号 / 假设 B / 数据不足 / 预算熔断 / 否决条件命中） |

### 测试（新增 30 例全绿）

| 文件 | 覆盖 |
| --- | --- |
| `tests/unit/test_judge_channel.py`（25 例） | 零干预 / 预算熔断 / warn_only 否决 / 主链路零影响 / 分歧率与采纳率计算 / 判别规则 A/B/样本不足/边界 / 开关与降级 / 解析 / rollout_audit 数据源 / 端到端 A/B |
| `tests/unit/test_learning_budget.py`（任务5 扩展 5 例） | scope 声明与优先级 / 主链路零引用审计 / 生产 config enforce 灰度审计 |

### 文档

| 文件 | 说明 |
| --- | --- |
| `任务5_LLM-as-Judge双假设判别报告.md` | 判别结论 / 实验设计 / 判别规则 / 数据口径与快照 / 成本核算 / 置信度限制 / 启用建议 |
| `任务5_LLM-as-Judge双假设验证_变更说明.md` | 交付内容 / 不变式守约 / 测试 / 使用示例 / 共享文件协同披露 / 验收对照 |

## 四、回归验证

### 新增单测（30 例全绿）

```
tests/unit/test_judge_channel.py ......................... (25)
tests/unit/test_learning_budget.py ................. (17，含任务5 扩展 5 例)
= 30 例新增全绿
```

### 关联回归（116 例随机顺序全绿，零回归）

`test_judge_channel.py`(25) + `test_learning_budget.py`(17) + `test_learning_metrics.py`(13)
+ `test_learning_metrics_triggers.py`(19) + `test_rollout_controller.py`(25) +
`test_guard_status.py`(14) + `test_learning_scheduler.py`(3) = **116 passed**。
并行任务6/7 新测试（`test_replay_*` 179 / `test_learning_metrics_complexity` +
`test_learning_curriculum` + `test_complexity_classifier` 31）同样全绿（共享文件相干性验证）。

### 全量回归（`python -m pytest tests/unit -q`，2026-08-22 实测）

**12101 passed / 83 failed / 293 skipped / 13 xfailed / 4 xpassed / 14 errors**（73 分钟）。
失败全部为**既有环境性失败**，与本任务无关（与任务3 基线 12046 passed/75 failed/14 errors
同源；失败数随并行任务新增测试微增）：
- 绝大多数为 `PermissionError: [WinError 5]`（沙箱禁止子进程管道捕获，波及
  `test_knowledge_cli` / `test_mcp_executor` / `test_preflight_runner` /
  `test_sandbox_execution_guard` / `test_skill_manager` / `test_verify_migrated_skills` /
  `test_precheck_docs_anchor_links` 等子进程类测试）；
- 少量为缺失可选依赖（`sqlite_vec` 后端不可用）与顺序/环境抖动
  （`test_snapshot_comprehensive` / `test_long_term_memory_embedding`）；
- **所有失败用例均不 import / 不依赖本任务修改的模块**；本任务相关测试集在全量运行中全绿。

本地质量门禁：`scripts/pre_commit_ci_guard.py --static-only` 静态段 PASS（Singleton/CI
配置检查；git 同步段因沙箱 WinError 5 无法执行，属环境限制，CI 环境执行——与任务3 P6
同基线）。

## 五、遇到的问题与解决方案

| # | 问题 | 解决方案 | 状态 |
| --- | --- | --- | --- |
| P1 | Judge 通道需 LLM 客户端，但代码库无统一 `get_llm_client`（`routes_workflow_learning.py` 的导入为惰性降级模式） | 遵循项目既有 duck-typed 惯例（`chat/invoke/complete/generate`，同 `AIAssistedGenerator`/`skill_converter`）；客户端可注入，缺失 → `no_llm_client` 诚实跳过（不伪造判定） | ✅ 已解决 |
| P2 | 预算模式读取只支持 dict，注入的 `LearningBudget` 实例被判为"非 enforce" | `_budget_mode` 同时支持实例（`.mode` 属性）与 dict，缺失 → `""`（否决） | ✅ 已修复（单测覆盖） |
| P3 | 关键词兜底中 "yes"/"no" 单字标记过于贪婪（普通文本误命中） | 移除单字标记，仅保留明确语义标记（accept/adopt/approve/拒绝等）；垃圾输入 → 解析失败 → `parse_failed` 诚实跳过 | ✅ 已修复 |
| P4 | `evaluate_candidates` 的 `audit_file` 覆盖未透传 `evaluate_one`（测试审计落点错位） | 覆盖值并入 cfg 副本，保证逐候选审计落点一致 | ✅ 已修复 |
| P5 | 预算熔断路径的成本语义：被拦截的调用 LLM 未执行，不应入账 | `LearningBudgetExceeded` → `budget_blocked` + 零成本；其余异常（LLM 调用后）→ 预估成本入账（诚实核算） | ✅ 已修复 |
| P6 | 共享工作区：config.yaml/learning_metrics.py/.env.example/learning/__init__.py 与并行任务6/7 共存未提交变更 | 提交采用精确路径 add；共享文件含三方共存变更（均已测试验证相干），变更说明 §七 逐项披露（守任务3 P5 协同惯例）；并行会话已确认引用（任务6 结案报告 L7） | ✅ 已解决 |
| P7 | 冒烟测试残留 `data/learning/judge_audit.jsonl`（含伪造样本） | 删除残留文件（不提交伪造数据）；审计文件为运行时生成（与任务3 `rollout_audit.jsonl` 占位惯例一致，留待采集期生成） | ✅ 已解决 |
| P8 | 沙箱禁止 git 网络传输（任何远端操作 spawn 子进程管道被拒，含 https 公开仓库验证）；`danger-full-access` 升级被取消 | 推送由并行会话的分支推送完成（本提交 `f076467c` 已双远端入库）；gitee 侧 1 提交缺口（任务6 `d41d3830`）为任务6 会话提交，已列入遗留项 | ⚠️ 环境限制（见遗留项 R1/R2） |

## 六、遗留事项 / 后续衔接

| 遗留项 | 说明 | 建议 |
| --- | --- | --- |
| R1 | **2 周数据采集为运维动作**：判别报告当前为采集窗口开启基线（有效判定 0，建议 `not_introduce`）；正式判别需采集期数据 | 运维按 `LEARNING_JUDGE_ENABLED=true` 开启采集（仍 dry-run 零干预），候选 ≥5 或 2 周后用 `python -m agent.learning.judge_channel --report` 复核，结论转 `evaluate_introduce` 才进入报告 §3.3 启用流程（远期里程碑门） |
| R2 | **gitee 远端 1 提交缺口**：`d41d3830`（任务6 结案补充）在 origin 已入库、gitee 未同步（本会话沙箱禁止 git 网络传输，无法代推） | 任务6 会话或运维补推 `git push gitee feat/m2-gitleaks`（或由具备网络权限的环境统一推送） |
| R3 | **CI/CD 结果在 GitHub 侧确认**：本提交已推 origin，GitHub Actions 工作流自动触发；本地静态门禁已 PASS，全量单测已本地验证 | 在 GitHub Actions 页面确认 `feat/m2-gitleaks` 分支流水线结果（环境性失败基线同任务3/6/7 披露） |
| R4 | T8 多租户开放 API 等并行工作区未提交变更（`routes_tenants.py` 等） | 不属于本计划范围，由对应项目线自行提交 |
| R5 | 任务3 遗留：confirm/rollout 回归门禁基线需任务1 门禁 CLI 显式建立（NO_SAMPLES 拦截） | 延续任务1 遗留项，与本任务无耦合 |

## 七、结案确认

- ✅ Judge 通道零干预可证（审计 `intervention=false` + KPI#7 零变化 + 零采纳侧调用 +
  模块零提交/审批 import）
- ✅ 预算 enforce 灰度上线（仅学习动作）：超限学习动作被拒（单测证明），主链路 LLM
  调用零影响（测试证明）；否决条件（warn_only 不引入 LLM 型评估角色）已解除
- ✅ 判别规则可复现：A/B 两类合成数据结论方向与预设规则一致（单测证明）
- ✅ 判别报告含样本量/分歧率/采纳率差异/token 成本/置信度限制，建议二值明确
  （当前 `not_introduce`，复核条件明确）
- ✅ 未修改既有规则评估器（`reflection`/`critic`/`feedback`/`reviewer`/`offline_evolver`
  零 diff，git 审计）
- ✅ 全程 dry-run，未触发任何"干预模式"（默认 `enabled=false` 零 LLM）
- ✅ 新增单测 30 例 + 关联回归 116 例随机顺序全绿；全量回归 12101 passed，失败全为
  既有环境性失败且与本任务无关
- ✅ 提交 `f076467c`（14 文件 +2683 行）已入库，并随并行会话分支推送双远端
  （origin/GitHub = HEAD；gitee 含本提交）

**任务5 交付完成，可结案。**
