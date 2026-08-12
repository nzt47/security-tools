# TASK-05：反馈进化与生命周期自动化

## 0. 任务标识

| 字段 | 值 |
|---|---|
| 任务编号 | TASK-05 |
| 所属阶段 | 主线阶段 5/5（闭环收尾） |
| 前置依赖 | TASK-03（KPI）、TASK-04（沉淀管道与强制审核链） |
| 并行建议 | 可与 TASK-06、TASK-07 并行 |
| 涉及主文档 | `docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§4.4 断点 5；§3.4 补充项 4） |

## 1. 背景（为什么做）

设计思路强调"过程反馈驱动进化 + Skill 生命周期管理（压缩/聚类/淘汰）"。审计发现：

1. `agent/feedback.py` 已实现完整反馈闭环：`submit_feedback` → `recommended_action`（promote_to_published / consider_deprecate_or_merge / improve_params / keep），但**这些建议没有自动执行体**，只停留在 API 返回值。
2. `agent/skills_mgmt/offline_evolver.py`（帕累托前沿批量进化：候选筛选→变异→多目标评估→非支配排序→提交最优）管线完整但**无定时触发**（`run_evolution_demo.py` 用 mock 演示）。
3. Skill 生命周期状态机齐全（DRAFT→…→PUBLISHED→DEPRECATED→ARCHIVED）但**无"长期零使用自动淘汰"判定器**。

本任务把"反馈→进化→淘汰"接上**定时执行体**，全部动作默认 dry-run + 可回滚 + 审计，让"在线大飞轮"真正转起来。

## 2. 目标描述（做什么）

1. **feedback 建议自动执行体**：每日任务读取 `get_skill_feedback_summary()` 的 `recommended_action`，按建议自动执行（晋升/合并/淘汰/参数优化），全部走既有 API（`SkillEnhancer`/`SkillStore.merge_skills`/状态迁移），默认 dry-run。
2. **offline_evolver 定时调度**：注册周期进化任务（建议每周），提交门槛保持 `improvement≥0.05` + 版本快照可回滚。
3. **Skill 生命周期自动化**：零使用 N 天 → DEPRECATED；再 M 天仍零使用 → ARCHIVED；容量超阈值 → 建议检索升级（联动 `scale.upgrade_threshold`）。

## 3. 不变式约束（不易——禁止触碰）

- **禁止修改** `feedback.py` 的 `recommended_action` 生成逻辑与 SQLite schema。
- **禁止修改** `offline_evolver.py` 的进化算法与 `BatchEvolutionReport` 结构（只接调度与执行钩子）。
- **禁止修改** `SkillStore.merge_skills` / `SkillEnhancer.bump_version/rollback_version` / 状态机转换表。
- **保留**：所有自动动作默认 `dry_run: true`；正式执行必须产生审计日志（`audit_file`）并可一键关闭（总开关）。
- **保留** 人类干预能力：被自动处理过的 Skill 在人工 review 时可恢复（靠版本快照 rollback）。
- 淘汰判定**绝不删除文件**：DEPRECATED/ARCHIVED 只是状态迁移（`models.py` 状态机），物理删除仍只允许人工触发。

## 4. 执行步骤

### Step 1：feedback 自动执行体
新增 `agent/skills_mgmt/feedback_agent.py`（或扩展 service.py，遵最小改动）：
- `execute_recommendations(dry_run=True) -> Report`：遍历 `get_skill_feedback_summary()` 的建议：
  - `promote_to_published`：校验该 Skill 已过审核链（TASK-04 强制链），通过则晋升 PUBLISHED；
  - `consider_deprecate_or_merge`：取 Jaccard 最高相似 Skill，`merge_skills` 或先 DEPRECATED；
  - `improve_params`：调 `SkillEnhancer` 参数优化（高失败率参数重置默认）；
  - `keep`：跳过。
- 每个动作前 `bump_version` 打快照（可回滚）；动作写审计日志（skill_id/action/原因/结果/快照版本）。
- 挂载到 `agent/scheduling.py`/`task_scheduler.py` 定时任务（config `learning.feedback_agent.interval_hours`，默认 24）。

### Step 2：offline_evolver 定时调度
- 注册周级任务（config `learning.evolver.interval_days`，默认 7；`dry_run` 默认 true）：
  - 候选筛选沿用其既有逻辑（usage≥阈值 且 success_rate<目标）；
  - 每轮进化前快照可回滚（复用 `bump_version`）；
  - 提交门槛不变（improvement≥0.05）；变异失败/评估异常跳过不中断批量；
  - 产出 `BatchEvolutionReport` 写审计 + TASK-03"进化采纳率"KPI。
- 调度器注册处与 TASK-04 保持一致（同一调度收口，避免双调度器）。

### Step 3：生命周期自动化
新增 `agent/skills_mgmt/lifecycle.py`：
- `run_lifecycle_check(dry_run=True)`：扫描全部 Skill：
  - 状态 PUBLISHED 且 `last_used_at` 距今 > `unused_days`（默认 90）→ DEPRECATED；
  - 状态 DEPRECATED 且距今 > `archive_days`（默认 180）→ ARCHIVED；
  - 总数 > `scale.upgrade_threshold`（默认 30）→ 输出检索升级建议（写入报告，不自动改检索配置）。
- `last_used_at` 缺失时以 `usage_count==0` 且创建时间距今超阈值判定。
- 全部动作 dry-run 默认；正式执行写审计。

### Step 4：补测试（TDD）
新增 `tests/unit/test_feedback_agent.py` + `tests/unit/test_skill_lifecycle.py` + `tests/unit/test_evolver_schedule.py`：
- feedback agent：4 类建议各自触发正确动作；dry_run 零副作用；审核链未过时 promote 被拒；动作后版本快照存在且可 rollback。
- lifecycle：构造零使用 Skill 断言 DEPRECATED→ARCHIVED 时序；容量超限输出升级建议；dry_run 不迁移。
- evolver schedule：调度注册存在；dry_run 不提交变异体；提交后 KPI 计数递增。

### Step 5：回归与门禁
- `python -m pytest tests/unit -q` 全绿；新用例全绿；质量门禁见 §6。

## 5. 预期成果（交付物）

1. `agent/skills_mgmt/feedback_agent.py` + `agent/skills_mgmt/lifecycle.py` 两个新模块。
2. feedback 执行体 / evolver / lifecycle 三处定时调度注册（统一收口）。
3. 配置：`learning.feedback_agent.*` / `learning.evolver.*` / `learning.lifecycle.unused_days|archive_days`（含 dry_run 默认 true、总开关、注释）。
4. 测试：3 个新测试文件（≥ 15 用例）。
5. 变更说明：`docs/zh/智能体学习机制重构计划/变更说明/TASK-05_变更说明.md`（含动作矩阵表：建议类型×动作×回滚方式×审计字段）。

## 6. 评估标准（验收条件）

### 功能验收
- [ ] 构造含 4 类 `recommended_action` 的模拟反馈数据，`dry_run=true` 跑一轮：报告正确但零状态变更。
- [ ] `dry_run=false` 跑一轮：各类动作执行正确；每个动作有版本快照；审计日志完整。
- [ ] promote 一个未过审核链的 Skill → 被拒（与 TASK-04 强制链联动）。
- [ ] 零使用 Skill 按 `unused_days→archive_days` 时序迁移；全程不物理删除。
- [ ] evolver 周任务注册存在；dry_run 零提交；正式运行后"进化采纳率"KPI 有值。
- [ ] 任一自动任务抛错时其他任务不受影响（逐任务 try/except）。

### 测试要求
- [ ] 新增 ≥ 15 用例全部通过；`python -m pytest tests/unit -q` 全绿。

### 质量门禁
- [ ] `python scripts/pre_commit_ci_guard.py --static-only --strict` 零新增告警。
- [ ] `python -m agent.observability.arch_rules --check` 通过。

## 7. 工程约束（仓库规则）

- 同 TASK-01 §7（git 精确路径、commit -F、hook 环境变量、勿碰并行会话文件、UTF-8 无 BOM）。
- 所有自动写操作默认 dry-run 是本任务**不可变约束**：验收时任何"默认就执行写操作"的实现一律不通过。
- 与 TASK-04 共用调度收口；若并行执行，先合并 TASK-04 的调度注册代码再改。
