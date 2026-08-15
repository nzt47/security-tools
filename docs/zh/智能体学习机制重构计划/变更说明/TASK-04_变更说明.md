# TASK-04 变更说明：知识→技能沉淀管道

| 字段 | 值 |
| --- | --- |
| 任务编号 | TASK-04 |
| 所属阶段 | 主线阶段 4/5 |
| 涉及主文档 | `docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§4.4 断点 4、5；§3.4 补充项 4、5） |
| 关联任务书 | `docs/zh/智能体学习机制重构计划/TASK-04_知识技能沉淀管道.md` |
| 实现日期 | 2026-08-14 |

## 1. 背景

设计闭环"经验蒸馏→Skill 沉淀→检索复用"在云枢断在两处：

1. **知识→Skill 断链**：`agent/knowledge/`（素材→Note→卡片五步闭环）与 `agent/skills_mgmt/`（Skill 三层检索/审核/版本）相互独立，knowledge 对 skills_mgmt 仅引用 RRF 常量，无任何创建 Skill 的调用；
2. **沉淀管道未调度**：`agent/skills_mgmt/memory_abstractor.py`（记忆/反馈→技能草稿，含 Jaccard 聚类、质量门控、`auto_register` 默认 False）代码完整但全局无调用方。

本任务补齐连接器（知识卡片→Skill DRAFT）、沉淀定时调度、发布强制审核链三块，全部默认关闭/默认强制，不动任何既有接口签名。

## 2. 改动点

### 2.1 新增 `agent/knowledge/skill_bridge.py`（Step 1 连接器）

`KnowledgeSkillBridge` 类，放 knowledge 侧（skills_mgmt 无反向依赖，架构规则单向无循环）：

| 方法 | 签名 | 说明 |
| --- | --- | --- |
| `card_to_skill_draft(card, *, dry_run=False) -> Optional[str]` | 单卡转换 | 返回 skill_id；跳过/失败返回 None，原因在 `last_result.reason` |
| `convert_cards(*, dry_run=False) -> List[Dict]` | 批量转换 | 逐卡结果 `{slug, skill_id, skipped, reason, ...}` |
| `is_eligible(card) -> bool` | 判定 | `status==current 且 metadata.distilled==True` |

**连接器 schema（卡片字段消费）**：

| 卡片字段 | 消费去向 |
| --- | --- |
| `title` | Skill `name`（截断至模型上限 200） |
| `insight`（one_line_insight）+ `content` 前 500 字（core_points/knowledge_points 载体） | Skill `intent` → `description` |
| `tags` | Skill `tags`（最多 10 个，空则 `["knowledge_card"]`） |
| `metadata.distilled` | 可转换判定（True 才可转换） |
| `metadata.converted_to_skill` | 幂等标记（转换成功后写回 frontmatter） |

**执行顺序**：判定 → 幂等跳过 → `AIAssistedGenerator.generate` 骨架生成（LLM 不可用自动模板降级，此时未落盘）→ `DuplicateDetector` 去重（Jaccard≥0.7 + 内容哈希，复用 `merge_skills` 同阈值语义）→ `creator._commit_new_skill` 落盘（复用防连点锁+版本快照+legacy 同步既有链路）→ 写回幂等标记 → `learning.artifacts.skill` KPI 计数。落盘冲突（同 id 已存在）按重复跳过。

**CLI 入口**（`agent/knowledge/__main__.py` 追加 `convert-cards` 子命令）：

```
python -m agent.knowledge convert-cards [--dry-run] [--wiki PATH] [--skills-store PATH]
```

退出码 0=成功；`--dry-run` 只预览产出草稿，不落盘、不写幂等标记。

### 2.2 新增 `agent/skills_mgmt/precipitate.py`（Step 2 调度）

`PrecipitateScheduler` 类，接线 `memory_abstractor.abstract_new_skills` 主入口（任务书所述 `abstract_batch` 方法不存在，以实际方法名接线，见裁决 R2），复用 `task_scheduler.get_scheduler().add_interval_task`（与 `offline_evolver.schedule` 同模式，daemon 由云枢主进程启动）。

| 方法 | 说明 |
| --- | --- |
| `schedule(*, interval_hours, days, max_skills, auto_register=False) -> Dict` | 注册定时任务；**auto_register 传入任何值都被强制 False**（不变式） |
| `unschedule() -> bool` | 按固定任务名 `技能沉淀` 注销（可跨实例） |
| `_scheduled_run() -> None` | 定时触发：跑一轮抽象，异常不抛出（调度线程稳定性） |

**调度参数（优先级：环境变量 > config.yaml > 默认值）**：

| 参数 | config.yaml | 环境变量 | 默认值 |
| --- | --- | --- | --- |
| 开关 | `learning.precipitate_enabled` | `LEARNING_PRECIPITATE_ENABLED` | `false`（默认关闭，安全底线） |
| 间隔（小时） | `learning.precipitate.interval_hours` | `LEARNING_PRECIPITATE_INTERVAL_HOURS` | `24` |
| 审计文件 | `learning.precipitate.audit_file` | `LEARNING_PRECIPITATE_AUDIT_FILE` | `./data/precipitate_audit.jsonl` |

`_scheduled_run` 对质量门控通过（`quality_gate_passed=True`）的草稿：写 JSONL 审计日志（`event=precipitate_draft`，含 draft_skill_id/cluster_size/success_rate/registered）+ `learning.artifacts.skill` 沉淀增量 KPI；失败草稿不审计不计数。**草稿不落盘**（`auto_register=False` 原语义，见裁决 R2）。

### 2.3 新增 `agent/skills_mgmt/review_gate.py` + `service.publish()`（Step 3 强制审核链）

| 函数 | 说明 |
| --- | --- |
| `enforce_review(skill, *, force, actor, reason) -> None` | 发布前强制审核校验；无 PASSED ReviewResult 抛 `SkillReviewError` |
| `audit_exemption(skill_id, *, actor, reason) -> None` | 豁免发布审计日志（JSONL 追加） |

`SkillsMgmtService.publish(skill_id, *, actor, force, reason) -> Skill`（**追加方法**，不改任何既有接口）：终态/驳回态拒绝发布 → `enforce_review` → 置 `PUBLISHED` 落盘 → `yunshu_skill_publish_total` 指标。

**配置（优先级：环境变量 > config.yaml > 默认值）**：

| 配置键 | 环境变量 | 默认值 |
| --- | --- | --- |
| `skills_mgmt.review.enforce_before_publish` | `SKILLS_REVIEW_ENFORCE_PUBLISH` | `true`（默认强制） |
| `skills_mgmt.review.audit_file` | `SKILLS_REVIEW_AUDIT_FILE` | `./data/skills_mgmt_review_audit.jsonl` |

PASSED 判据 = `skill.review.status == ReviewStatus.PASSED`（reviewer 置 PASSED 时已保证三维分达 `review_thresholds`，无需二次计算）。豁免路径（配置关闭或 `force=True`）必须写审计日志（`event=review_waiver_publish`，含 skill_id/actor/reason），留痕可追溯。

**HTTP 路由**（`agent/server_routes/routes_skills_mgmt.py` 追加）：`POST /api/skills-mgmt/<skill_id>/publish?force=&reason=`；既有 `review` / `review/batch` / `review/thresholds` 路由与 `reject` 路径一律不动。

### 2.4 配置（config.yaml，含注释）

| 配置键 | 默认值 | 说明 |
| --- | --- | --- |
| `learning.precipitate_enabled` | `false` | 沉淀调度开关（安全底线，默认关闭） |
| `learning.precipitate.interval_hours` | `24` | 沉淀定时任务间隔（小时） |
| `learning.precipitate.audit_file` | `./data/precipitate_audit.jsonl` | 沉淀草稿审计日志 |
| `skills_mgmt.review.enforce_before_publish` | `true` | 发布强制审核链（默认强制） |
| `skills_mgmt.review.audit_file` | `./data/skills_mgmt_review_audit.jsonl` | 豁免发布审计日志 |

## 3. 裁决记录（决策/适配）

| 编号 | 裁决 | 依据 |
| --- | --- | --- |
| R1 | **"approved 卡片"语义适配**：任务书 Step 1 要求"取 approved 卡片（CardStore 状态机 approved 且 distilled=True）"，但 `CardStatus` 仅 draft/current/archive/unknown，**无 approved 态**（approved 只存在于 `distill.py` 的 Note 层，`promote_to_card` 已强制 note approved+distilled 才能产卡）。经用户确认：可转换判定 = **`status==current`（人工确认有效态）+ `metadata.distilled==True`（已蒸馏产卡）**。未确认（draft/archive）或未蒸馏卡片拒绝转换 | 用户确认（AskUserQuestion 2026-08-14） |
| R2 | **草稿去向 + 方法名适配**：任务书 Step 2 所述 `abstract_batch` 方法不存在，实际主入口为 `abstract_new_skills`。且 `auto_register=False` 既有语义是"只返回草稿 dict、不写入 store"，与任务书"抽象产物只落 data/skills_mgmt.json 的 DRAFT"冲突。经用户确认：**草稿不落盘，仅审计日志 + 沉淀增量 KPI 计数**（严格遵守不变式"不擅自注册技能"）；调度接线 `abstract_new_skills(days, max_skills, auto_register=False)` | 用户确认（AskUserQuestion 2026-08-14）+ 不变式 §3 |
| R3 | **发布路径收口**：`service.py` 原无 `publish` 方法（DRAFT→…→PUBLISHED 仅存在于 `enhancer.optimize_params.promote_to_published` 自动晋升分支）。本任务**新增** `publish()` 方法承载强制审核链；`enhancer.promote_to_published` 自动晋升路径**保持不动**（其仅对 `status==APPROVED`（已审核通过）且成功率≥99%/使用≥10 的技能晋升，语义上已满足"先审核后发布"，与强制链不冲突）；`workflow_learning/skill_converter.py` 自动升格链路同样不动（自身带 success_count≥5 且 confidence≥0.7 质量门控，属独立闭环） | 任务书 §3 不变式 + §4 Step 3 |
| R4 | **pre_commit_ci_guard --strict 基线漂移（已消解）**：实现中途 `--static-only --strict` 曾报 14 条新增 `import_degraded` WARN（`learning_metrics.py:410`、`optimized_storage.py:533`、`api_gateway.py:521` 等），经逐文件核对**全部来自并行会话未提交改动**，本任务触达文件（skill_bridge/precipitate/review_gate/service/__main__/routes_skills_mgmt）经规则模式扫描**零命中**，未触碰并行会话文件、未改共享基线。**最终验收复跑：FAIL=0，新增阻断 WARN=0**（47 条存量 WARN 全在基线内豁免），漂移随并行会话提交已消解 | 项目记忆"勿碰并行会话文件" + Grep 核实 |
| R5 | **全量 `tests/unit -q` 回归（已执行）**：验收时两个并行会话 pytest 正在运行，若即时再起全量会争抢资源、触发 D 类慢路径强制终止（项目记忆 2026-08-14 Phase 1 监控链教训）。经用户确认"等并行跑完再全量"，并行 `tests/unit` 结束后（13:00）启动全量：`--randomly-seed=20260814 -p no:cacheprovider`，**11381 passed / 7 failed / 1 error（39:02）**；7 failed 与 1 error 经单独重跑与 Git 核对全部判定为已知环境噪音（K13 ast 漂移 / T-4 reload 污染 / 并行会话 untracked 测试文件），**本任务 19 用例全量全绿，无真实回归** | 用户确认（AskUserQuestion 2026-08-14）+ K13/T-4 判定惯例 |

## 4. 测试

| 文件 | 用例数 | 覆盖 |
| --- | --- | --- |
| `tests/unit/test_knowledge_skill_bridge.py` | 7 | 可转换卡片→DRAFT 字段映射；未 approved 拒绝；重复跳过；幂等零新增；dry-run 无副作用；LLM 降级模板；CLI 退出码 |
| `tests/unit/test_precipitate_scheduler.py` | 6 | 默认关闭不注册；开启注册（interval 生效）；unschedule 幂等；auto_register 强制 False；scheduled_run 审计+KPI+零落盘；异常吞掉 |
| `tests/unit/test_review_enforcement.py` | 6 | 无审核拒绝；PASSED 后成功；FAILED 拒绝；终态拒绝；force 豁免+审计；配置豁免+审计 |

合计 19 用例（≥ 任务书 12 用例要求）。

**回归结果**：
- 新增 3 文件：`19 passed`；
- 相关既有：`test_knowledge_cli / test_skills_mgmt / test_memory_skill_abstractor` → `163 passed, 1 xfailed`（xfailed 为既有 TF-IDF 基线阈值问题，与本任务无关）；
- **全量 `tests/unit -q --randomly-seed=20260814`（2026-08-14 14:00 完成）**：`11381 passed, 296 skipped, 13 xfailed, 4 xpassed, 7 failed, 1 error`（39:02）。本任务 3 个测试文件在全量中全部通过（19/19）。7 failed 单独重跑 `128 passed, 0 failed` 全部转绿，判定为已知环境噪音（K13 长进程 ast/linecache 漂移：test_orchestrator_reject ×2、test_skill_output_guard ×3；T-4 reload 顺序污染：test_optimized_storage ×2）；1 error 为并行会话 untracked 新测试文件 `test_skills_mgmt_safety.py` 引用不存在的 `get_evolution_audit`（与本任务零关联）；
- `python -m agent.observability.arch_rules --check`：0 未豁免违规（既有 4 项豁免与本任务无关），knowledge→skills_mgmt 单向依赖无循环；
- `pre_commit_ci_guard --static-only --strict`：本任务 6 个触达文件零命中；13 条新增 WARN（api_gateway/feedback/lazy_loader_async/optimized_storage/chaos_injector）全部来自并行会话文件，且基线文件（.guard_baseline.json）被并行会话改动（47→34 豁免项），漂移归属并行会话（同 R4 判定）。

## 5. 回滚方法

1. **连接器/调度/审核链**：删除 `skill_bridge.py` / `precipitate.py` / `review_gate.py`，`git checkout` 还原 `__main__.py` / `service.py` / `routes_skills_mgmt.py` / `config.yaml`；
2. **运行时开关**：`learning.precipitate_enabled` 回 `false`（默认关闭）即可停用沉淀调度；`skills_mgmt.review.enforce_before_publish` 回 `true`（默认强制）；
3. **幂等标记**：卡片 `metadata.converted_to_skill` 保留（后续重新转换会幂等跳过，如需重新转换删除该字段即可）；已产 DRAFT 技能保留在技能库，不自动删除。

## 6. 工程约束落实

- 本任务触达文件 `agent/knowledge/__main__.py`、`agent/skills_mgmt/service.py` 与并行会话存在共享/覆盖风险（已实测一次 parser 块被覆盖丢失），实现过程中已核对 Git 基线、改后立即 Read/Grep 验证；
- 新增文件未覆盖任何并行会话文件；`tests/unit` 三个新测试文件均为本任务指定文件名；
- 提交须用 detached worktree 隔离 index（并行会话活跃，共享 index 有混入/清空风险，项目记忆 2026-08-14 教训）。

### 事故记录 2026-08-14：接线被并行会话覆盖（已恢复）

- **现象**：`scripts/deploy_task04.py` 校验发现 `__main__.py`（convert-cards）、`service.py`（enforce_review）、`routes_skills_mgmt.py`（publish 路由）三处接线标记全部消失，`git status` 显示三者已回退为 clean（并行会话 d34bc708 提交周期中 stash/restore 冲掉我方未提交改动）。
- **恢复**：凭本会话早前完整 Read 的快照逐文件重新插入三处接线（publish 方法、CLI 子命令、HTTP 路由），随后 `deploy_task04.py` 全量校验 ALL PASS（含 19 用例）。
- **教训**（与项目记忆"工作区文件可能被并行会话覆盖回旧版"一致）：未提交的既有文件改动随时可能被并行会话覆盖，**接线类改动应尽早提交或落 detached worktree**；`deploy_task04.py` 的接线标记校验可第一时间发现此类丢失。
- **追加（第三次覆盖）**：CHANGELOG.md 的 TASK-04 条目在同日也被并行会话冲掉一次（文件回退为编辑前状态），已重新插入并核验。共享高频文件（CHANGELOG 等）的未提交改动同样处于被覆盖风险中，**交付文档改动应与代码改动一并尽早提交**。
