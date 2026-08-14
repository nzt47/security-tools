# TASK-05 变更说明：反馈进化与生命周期自动化

| 字段 | 值 |
| --- | --- |
| 任务编号 | TASK-05 |
| 所属阶段 | 主线阶段 5/5（闭环收尾） |
| 涉及主文档 | `docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§4.4 断点 5；§3.4 补充项 4） |
| 关联任务书 | `docs/zh/智能体学习机制重构计划/TASK-05_反馈进化与生命周期自动化.md` |
| 实现日期 | 2026-08-14 |

## 1. 背景

设计思路强调"过程反馈驱动进化 + Skill 生命周期管理（压缩/聚类/淘汰）"。审计发现三处断点：

1. `agent/feedback.py` 的 `get_skill_feedback_summary()` 已产出 `recommended_action`（promote_to_published / consider_deprecate_or_merge / improve_params / keep / no_data），但建议只停留在 API 返回值，**无自动执行体**；
2. `offline_evolver.py` 帕累托前沿批量进化管线完整，但**无周级定时触发**，且不产生 TASK-03 预留的"进化采纳率"KPI（`record_evolution_candidate`）；
3. Skill 生命周期状态机齐全（DRAFT→…→PUBLISHED→DEPRECATED→ARCHIVED），但**无"长期零使用自动淘汰"判定器**。

本任务把"反馈→进化→淘汰"接上定时执行体：全部动作默认 dry-run + 可回滚 + 审计 + 总开关，让"在线大飞轮"真正转起来。

## 2. 动作矩阵表（建议类型 × 动作 × 回滚方式 × 审计字段）

| 建议类型 | 触发判据（feedback.py 既有逻辑，未改） | 自动动作 | 回滚方式 | 审计字段 |
| --- | --- | --- | --- | --- |
| `promote_to_published` | 满意率 ≥90% 且反馈数 ≥5 | bump 快照 → 强制审核链 `publish()`（无 PASSED ReviewResult 被拒，记 rejected） | `rollback_version(skill_id, 快照版本)` | skill_id/action/promote/原因/结果/快照版本 |
| `consider_deprecate_or_merge` | 满意率 <50% 且反馈数 ≥5 | 有 Jaccard≥0.7 相似技能 → `merge_duplicate_skills`（保留方 bump 快照）；无 → 状态迁移 DEPRECATED | merge：保留方 `rollback_version`；deprecate：`rollback_version` 快照 | skill_id/action/deprecate_or_merge/原因/结果(merged\|deprecated)/merged_into/jaccard/快照版本 |
| `improve_params` | 平均评分 <3.0 | bump 快照 → `optimize_params(feedback_summary)` | `rollback_version(skill_id, 快照版本)` | skill_id/action/improve_params/原因/结果/快照版本/optimized |
| `keep` / `no_data` | 其余情况 | 跳过（零副作用） | — | 仅报告 planned，不写审计 |
| 生命周期 PUBLISHED 闲置 >90 天 | `last_used_at` 距今（缺失时 `usage_count==0` 且 `created_at` 距今） | 状态迁移 DEPRECATED（**不删文件**） | 人工改回 PUBLISHED（状态机合法转换） | skill_id/action/deprecate/from_status/to_status/闲置天数/阈值 |
| 生命周期 DEPRECATED 闲置 >180 天 | 同上 | 状态迁移 ARCHIVED（**不删文件**） | 人工改回 DEPRECATED/PUBLISHED | skill_id/action/archive/from_status/to_status/闲置天数/阈值 |
| 容量超限 >30 | 技能总数超 `scale.upgrade_threshold` | 检索升级建议（只写报告，**不改检索配置**） | — | 仅报告 suggestions |

## 3. 改动点

### 3.1 新增 `agent/skills_mgmt/feedback_agent.py`（Step 1 反馈建议自动执行体）

`FeedbackAgent` 类：

| 方法 | 说明 |
| --- | --- |
| `execute_recommendations(dry_run=True, days=30) -> Dict` | 遍历全部 Skill 的 `get_skill_feedback_summary()`，按 4 类建议分派（见动作矩阵表）；逐技能 try/except，任一失败不中断批量 |
| `schedule(*, interval_hours) -> Dict` | 注册每日任务（默认关闭，安全底线） |
| `unschedule() -> bool` | 按固定任务名 `反馈建议执行` 注销（可跨实例） |
| `_scheduled_run() -> None` | 调度触发：`execute_recommendations(dry_run=_dry_run())`，异常不抛出 |

**执行契约（任务书 §3 不变式）**：
- 每个动作前 `bump_version` 打版本快照（可回滚）；
- 正式执行逐动作写 JSONL 审计（`event=feedback_action`，字段见动作矩阵表）；dry-run 零副作用（不写审计、不改状态）；
- promote 与 TASK-04 强制审核链联动：无 PASSED ReviewResult 时 `service.publish` 抛 `SkillReviewError` → 记 rejected，不绕过审核；
- DEPRECATED 仅是状态迁移，绝不物理删除文件。

**排查日志（logger.info）**：核心分支均有详细日志，实际运行排障时可直接从控制台定位：
- 批次入口/出口：`execute_recommendations start/done dry_run=… processed=x/y executed=n rejected=n errors=n`；
- 分派点：`skill=<id> action=<类型> dry_run=<True|False> 开始执行 原因=<触发原因>`（keep/no_data 打"跳过"）；
- promote：成功 `promote 成功 skill=<id> 快照版本=<v> status=published` / 被拒 `promote 被拒 skill=<id> 错误=<审核拒绝原因>（强制审核链联动）`（WARNING）；
- merge：`merge 执行 skill=<src> -> <dst> jaccard=<相似度> 快照版本=<v>（保留方）`；
- deprecate：`deprecate 执行 skill=<id> 快照版本=<v>（仅状态迁移，不删文件）`；
- improve_params：`improve_params 执行 skill=<id> 快照版本=<v> optimized=<bool>`。

**本地演示脚本**：`scripts/demo_feedback_agent.py`（mock 4 类建议数据，temp 目录隔离，不污染真实数据）可复现 dry-run 零副作用与正式执行审计输出，供上线前预演与排障复现。

### 3.2 新增 `agent/skills_mgmt/evolution_scheduler.py`（Step 2 周级调度）

`EvolutionScheduler` 类（**不改 offline_evolver.py 算法与 BatchEvolutionReport 结构**，只接调度与执行钩子）：

| 方法 | 说明 |
| --- | --- |
| `run(*, dry_run=True, max_rounds, trigger) -> Dict` | dry_run：只 `_select_candidates()` 预演候选（只读 store，零提交/零 KPI/零审计）；正式：`service.evolve_batch(trigger="scheduler")`（提交门槛 improvement≥0.05 算法内部既有） |
| `schedule(*, interval_days) -> Dict` | 注册周级任务（默认关闭） |
| `unschedule() -> bool` | 按固定任务名 `周期进化` 注销 |
| `_scheduled_run() -> None` | 调度触发，异常不抛出 |

**KPI 接线（TASK-03 预留）**：正式运行后按 `evolved_count` / `skipped_count` / `failed_count` 逐条 `record_evolution_candidate(adopted)`，"进化采纳率"KPI 由零变有值；批次摘要写审计（`event=evolution_schedule_run`）。

### 3.3 新增 `agent/skills_mgmt/lifecycle.py`（Step 3 生命周期自动淘汰）

`LifecycleManager` 类：

| 方法 | 说明 |
| --- | --- |
| `run_lifecycle_check(dry_run=True) -> Dict` | 扫描全部 Skill：PUBLISHED 闲置 >unused_days→DEPRECATED；DEPRECATED 闲置 >archive_days→ARCHIVED；总数超阈值→检索升级建议（仅报告） |
| `schedule(*, interval_hours) -> Dict` / `unschedule()` | 每日检查任务注册/注销（默认关闭） |
| `_scheduled_run() -> None` | 调度触发，异常不抛出 |

**判定规则**：`last_used_at` 存在 → 以闲置天数计；缺失且 `usage_count==0` → 以 `created_at` 近似；`usage_count>0` 但缺失（异常数据）→ 保守不迁移（防误判）。全程不物理删除，物理删除仍仅人工允许。

### 3.4 三处调度注册统一收口

feedback 执行体 / evolver / lifecycle 全部经 `agent/task_scheduler.py` 的 `get_scheduler().add_interval_task()` 注册（与 TASK-04 precipitate 同一调度收口，**无双调度器**）；三个模块各自独立总开关（默认 false）+ dry_run（默认 true）+ 独立审计文件。

### 3.5 配置（config.yaml，含注释）

| 配置键 | 默认值 | 环境变量 | 说明 |
| --- | --- | --- | --- |
| `learning.feedback_agent.enabled` | `false` | `LEARNING_FEEDBACK_AGENT_ENABLED` | 反馈建议执行体总开关（默认关闭） |
| `learning.feedback_agent.interval_hours` | `24` | `LEARNING_FEEDBACK_AGENT_INTERVAL_HOURS` | 每日执行间隔（小时） |
| `learning.feedback_agent.dry_run` | `true` | `LEARNING_FEEDBACK_AGENT_DRY_RUN` | dry-run 默认 true（不可变约束） |
| `learning.feedback_agent.audit_file` | `./data/feedback_agent_audit.jsonl` | `LEARNING_FEEDBACK_AGENT_AUDIT_FILE` | 反馈动作审计日志 |
| `learning.evolver.enabled` | `false` | `LEARNING_EVOLVER_ENABLED` | 周期进化总开关（默认关闭） |
| `learning.evolver.interval_days` | `7` | `LEARNING_EVOLVER_INTERVAL_DAYS` | 周级进化间隔（天） |
| `learning.evolver.dry_run` | `true` | `LEARNING_EVOLVER_DRY_RUN` | dry-run 默认 true（不可变约束） |
| `learning.evolver.audit_file` | `./data/evolution_schedule_audit.jsonl` | `LEARNING_EVOLVER_AUDIT_FILE` | 进化批次审计日志 |
| `learning.lifecycle.enabled` | `false` | `LEARNING_LIFECYCLE_ENABLED` | 生命周期检查总开关（默认关闭） |
| `learning.lifecycle.interval_hours` | `24` | `LEARNING_LIFECYCLE_INTERVAL_HOURS` | 每日检查间隔（小时） |
| `learning.lifecycle.unused_days` | `90` | `LEARNING_LIFECYCLE_UNUSED_DAYS` | PUBLISHED→DEPRECATED 闲置阈值（天） |
| `learning.lifecycle.archive_days` | `180` | `LEARNING_LIFECYCLE_ARCHIVE_DAYS` | DEPRECATED→ARCHIVED 闲置阈值（天） |
| `learning.lifecycle.dry_run` | `true` | `LEARNING_LIFECYCLE_DRY_RUN` | dry-run 默认 true（不可变约束） |
| `learning.lifecycle.audit_file` | `./data/skill_lifecycle_audit.jsonl` | `LEARNING_LIFECYCLE_AUDIT_FILE` | 生命周期迁移审计日志 |
| `skills_mgmt.scale.upgrade_threshold` | `30`（既有） | `LEARNING_LIFECYCLE_UPGRADE_THRESHOLD` | 容量超限建议阈值（lifecycle 联动读取） |

## 4. 裁决记录（决策/适配）

| 编号 | 裁决 | 依据 |
| --- | --- | --- |
| R1 | **feedback 数据源**：`get_skill_feedback_summary` 为单技能粒度，`execute_recommendations` 遍历 `store.list_all()` 逐个调用（`no_data` 正常返回跳过），不新增批量接口 | 最小改动（任务书 §4 Step 1 "遵最小改动"） |
| R2 | **promote 审核链联动**：直接调 `service.publish()`（TASK-04 强制链），无 PASSED ReviewResult 抛 `SkillReviewError` → 捕获记 rejected 不绕过；不再自行校验 | 任务书 §3 不变式 + §4 Step 1 |
| R3 | **merge 目标**：`consider_deprecate_or_merge` 用 `SkillReviewer.find_duplicates_for(min_jaccard=0.7)` 取 Jaccard 最高相似技能 → `merge_duplicate_skills(skill, dst)`；快照打在保留方（merge 前状态可 rollback） | 任务书 §4 Step 1 "取 Jaccard 最高相似 Skill" |
| R4 | **evolver 调度不触碰 offline_evolver.py**：任务书要求"只接调度与执行钩子"且禁止改算法与报告结构。既有 `EVOLUTION_SCHEDULE_ENABLED=true` cron（每天 2 点）为并行会话既有配置保持不动；新增 `EvolutionScheduler` 以周级 interval + dry_run 预演 + KPI 接线实现任务书语义（默认关闭），避免重复进化 | 任务书 §3 不变式 + §4 Step 2 |
| R5 | **dry-run 语义**：feedback/lifecycle 的 dry-run 只产出报告（planned/actions），零状态变更、零审计写入（严格零副作用）；evolver 的 dry-run 只 `_select_candidates()` 预演候选（不跑 evolve_batch，零提交/零 KPI/零审计） | 任务书 §7 "默认就执行写操作的实现一律不通过" |
| R6 | **`VersionBump` 字段适配**：`svc.bump_version` 返回 `VersionBump`（字段 `old_version/new_version/changelog`），审计快照版本取 `new_version`（初版误用 `.version` 已修，见 §6 事故记录） | enhancer.py `VersionBump` dataclass |
| R7 | **反馈统计异常显性化**：`get_skill_feedback_summary` 对"无反馈"正常返回 `no_data`（不抛异常）；捕获到异常即真故障（反馈库不可用），记录 `errors` 不吞并（初版误当 no_data 吞掉已修） | 任务书 §6 "任一自动任务抛错时其他任务不受影响" |
| R8 | **lifecycle 闲置判定保守策略**：`last_used_at` 缺失且 `usage_count>0`（异常数据）返回 None 不迁移，宁可漏判不可误迁 | 任务书 §4 Step 3 "缺失时以 usage_count==0 且创建时间距今超阈值判定" |

## 5. 测试

| 文件 | 用例数 | 覆盖 |
| --- | --- | --- |
| `tests/unit/test_feedback_agent.py` | 10 | dry-run 4 类建议零副作用；promote 过审发布+快照+审计；promote 未过审被拒；merge 重复技能；无相似 DEPRECATED；improve_params 优化+快照+审计；keep 跳过；单技能异常不阻断；调度默认关闭/开启注册 |
| `tests/unit/test_skill_lifecycle.py` | 10 | 闲置天数判定（last_used/usage_zero 用 created_at/异常数据保守 None）；PUBLISHED→DEPRECATED；DEPRECATED→ARCHIVED；DEPRECATED→ARCHIVED 时序；近期使用不迁移；dry-run 零迁移零审计；容量超限建议 |
| `tests/unit/test_evolver_schedule.py` | 7 | 调度默认关闭/开启注册/注销；dry-run 预演零提交零 KPI 零审计；正式运行 KPI 递增+审计落盘；evolve_batch 异常不崩溃；dry_run 默认 true |

合计 27 用例（≥ 任务书 15 用例要求）。

**回归结果**：
- 新增 3 文件：`27 passed`；
- 相关既有（precipitate/review_enforcement/feedback_skill_binding）：`35 passed` 无回归；
- `pre_commit_ci_guard.py --static-only --strict`：FAIL=0，新增阻断 WARN=0（存量 47 条基线内豁免）；
- `python -m agent.observability.arch_rules --check`：通过（0 未豁免违规，4 项既有豁免与本任务无关）；
- 全量 `tests/unit -q -p no:randomly`（排除 4 个 D 类慢文件，见 §7）：**11424 passed / 164 skipped / 13 xfailed / 11 xpassed / 1 error（26:11）**；本任务 3 个测试文件 27 用例全量全绿；唯一 error 为既有 `test_planning_defect_d7.py`（`NameError: name 'pytest' is not defined`，既有 defect 看门狗文件缺陷，与本任务零关联）；rc=1 为 TRAE sandbox 拦截 teardown 写 `C:\nonexistent` 所致（项目记忆已知现象，判定以 `=+ \d+ passed` 汇总行为准）。

## 6. 回滚方法

1. **代码回滚**：删除 `feedback_agent.py` / `evolution_scheduler.py` / `lifecycle.py`，`git checkout` 还原 `config.yaml`；
2. **运行时开关**：三个总开关（`learning.feedback_agent.enabled` / `learning.evolver.enabled` / `learning.lifecycle.enabled`）均默认 `false`，一键关闭全部自动动作；
3. **dry-run 兜底**：即使开启总开关，`dry_run` 默认 `true`，不产生任何写操作；
4. **人类干预**：被自动处理过的 Skill 可经版本快照 `rollback_version` 恢复（promote/merge/improve 动作前均打快照）；DEPRECATED/ARCHIVED 为状态机合法转换，人工可改回。

## 7. 工程约束落实

- 触达文件：`config.yaml`（追加 3 组配置块，未动既有键）+ 3 个新模块 + 3 个新测试文件；未修改 `feedback.py` / `offline_evolver.py` / `models.py` / `store.py` / `enhancer.py` / `review_gate.py` / `service.py` 的任何既有逻辑（守【不易】不变式）；
- 全量回归排除 4 个 D 类慢文件（test_permission_system_concurrency / test_task_scheduler_comprehensive / test_memory_module / test_memory_optimized，项目记忆 2026-08-14 已标注 slow，conftest 自动 skip / `--runslow` 运行）；
- 提交须用 detached worktree 隔离 index（并行会话活跃，共享 index 有混入/清空风险，项目记忆 2026-08-14 教训）；改后立即 Read/Grep 验证防并行会话覆盖。

### 事故记录 2026-08-14：VersionBump 字段名误用（已修复）

- **现象**：初版 feedback_agent 三个动作分支用 `bump.version` 取快照版本，但 `enhancer.VersionBump` 字段为 `new_version`，运行时抛 `'VersionBump' object has no attribute 'version'`，导致 deprecate/improve 动作落入 errors（5 用例失败）。
- **修复**：全部改为 `bump.new_version`；同时修复 `_process_skill` 把反馈统计异常当 no_data 吞掉的隐性故障（R7）。修复后 27 用例全绿。
