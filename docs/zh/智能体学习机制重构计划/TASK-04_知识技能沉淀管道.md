# TASK-04：知识→技能沉淀管道

## 0. 任务标识

| 字段 | 值 |
|---|---|
| 任务编号 | TASK-04 |
| 所属阶段 | 主线阶段 4/5 |
| 前置依赖 | TASK-02（反思产物入检索面）、TASK-03（KPI 验收依据） |
| 并行建议 | 可与 TASK-06 并行 |
| 涉及主文档 | `docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§4.4 断点 4、5；§3.4 补充项 4、5） |

## 1. 背景（为什么做）

设计思路的核心闭环"经验蒸馏→Skill 沉淀→检索复用"在云枢断在两处：

1. **知识→Skill 断链**：`agent/knowledge/`（素材→Note→卡片五步闭环）与 `agent/skills_mgmt/`（Skill 三层检索/审核/版本）两系统相互独立。grep 证实 knowledge 对 skills_mgmt 仅引用 RRF 常量，**无任何创建 Skill 的调用**。
2. **沉淀管道未调度**：`agent/skills_mgmt/memory_abstractor.py`（记忆/反馈→技能草稿，含 Jaccard 聚类、质量门控、`auto_register` 默认 False）代码完整但**全局无调用方**；`CHANGELOG` 甚至记载过其"死代码导入修复"。

已有可复用资产：`workflow_learning/skill_converter.py` 的**质量门控模式**（success_count≥5 且 confidence≥0.7 才可升格）与 `creator.py` 的 AI 生成器（LLM 骨架生成+降级模板）——本任务照此模式扩圈，不新造。

## 2. 目标描述（做什么）

1. 实现**知识卡片→Skill 草稿**连接器：已 approved 的知识卡片（`agent/knowledge/card.py` 的 Card）可转换为 Skill DRAFT（复用 `SkillCreator` 骨架生成）。
2. 接线 **`memory_abstractor` 调度**：定时 + 事件触发，自动产 DRAFT 草稿（`auto_register` 分级：默认 false 仅产草稿，不自动注册）。
3. 强化**入库强制审核链**：Skill 从 DRAFT 晋升 PUBLISHED 必须经过 `reviewer` 三重审核（默认强制，可配置豁免开关，但豁免必须留审计日志）。

## 3. 不变式约束（不易——禁止触碰）

- **禁止删除**：`distill.py` 的 `draft→approved` 人工审批门控（`promote_to_card` 强制要求 `approved + distilled=True` 的逻辑不改）。
- **禁止修改** `skills_mgmt` 现有 Skill 模型/状态机/`create_manual`/`reviewer` 接口签名（只能在其上追加调用链）。
- **禁止修改** `workflow_learning/skill_converter.py` 及其自动升格链路（它在线上运行，不可动）。
- **保留**：`memory_abstractor.auto_register` 默认 False 语义（自动沉淀只产 DRAFT 草稿，不擅自注册技能）。
- **保留** `merge_skills` 的 Jaccard≥0.7 去重/聚类行为。
- 所有新连接器/调度默认关闭（`learning.precipitate_enabled: false`），开启后产出必须全部经过审核链。

## 4. 执行步骤

### Step 1：知识卡片→Skill 连接器
新增 `agent/knowledge/skill_bridge.py`（放 knowledge 侧，避免 skills_mgmt 反向依赖 knowledge；若架构规则限制 knowledge→skills_mgmt 依赖，则放 `agent/skills_mgmt/knowledge_bridge.py` 并反向引用 knowledge 的只读 API）：
- `card_to_skill_draft(card) -> skill_id`：取 approved 卡片（`CardStore` 状态机 approved 且 `distilled=True`）的 `one_line_insight / core_points / knowledge_points`，复用 `creator.AIAssistedGenerator.generate`（LLM 生成 Skill 的 description/body/示例，LLM 不可用降级模板），产出 Skill DRAFT。
- **去重**：先跑 `DuplicateDetector`（Jaccard≥0.7 + 内容哈希），重复则跳过并记录。
- **幂等**：每张卡片记录 `converted_to_skill` 标记（写回卡片 frontmatter 或独立映射表），避免重复转换。
- 提供 CLI 入口（参照 `knowledge/__main__.py` 风格）：`python -m agent.knowledge convert-cards [--dry-run]`。

### Step 2：接线 memory_abstractor 调度
- 在 `agent/scheduling.py`（或 `task_scheduler.py`，遵项目既有调度收口）注册定时任务：
  - **定时**：每日一次（config `learning.precipitate.interval_hours`，默认 24）跑 `OfflineEvolver`/`MemoryAbstractor.abstract_batch`——从反馈/工作流/长期记忆中提取技能草稿候选（自动走其既有质量门控：min_cluster_size / min_success_rate / max_existing_dup_jaccard）。
  - **事件**：`IntegrationHook.on_executed` 已存在，可在 skill 执行成功后触发轻量抽象（可选，第二期）。
- `auto_register` 保持默认 False：抽象产物只落 `data/skills_mgmt.json` 的 DRAFT + 审计日志 + TASK-03 的"沉淀增量"KPI 计数。

### Step 3：强制审核链
- 审核 `service.py` 的晋升路径：DRAFT→PENDING_REVIEW→APPROVED→PUBLISHED。
- 新增 config `skills_mgmt.review.enforce_before_publish: true`（默认 true）：
  - true：任何 `publish` 调用前必须存在 `ReviewResult`（三维评分通过 `review_thresholds`），否则拒绝并提示先 `review`；
  - false（显式豁免）：允许跳过，但必须写审计日志（`audit_file`）记录"豁免发布"。
- 保留既有 `review_all_pending` HTTP 路由；`reject` 路径不变。
- **不动** `workflow_learning` 自动升格链路（其自身已带质量门控，不属于本强制链范围；如与强制链冲突，在变更说明中记录裁决，不改其代码）。

### Step 4：补测试（TDD）
新增 `tests/unit/test_knowledge_skill_bridge.py` + `tests/unit/test_precipitate_scheduler.py` + `tests/unit/test_review_enforcement.py`：
- 桥接：approved 卡片→Skill DRAFT（字段映射正确）；未 approved 卡片拒绝；重复卡片幂等跳过；LLM 不可用走模板降级。
- 调度：定时任务注册存在且默认不执行（开关关）；开关开后 dry-run 产草稿不注册。
- 强制审核：无 ReviewResult 时 publish 被拒；review 通过后 publish 成功；`enforce_before_publish=false` 时豁免发布并写审计。

### Step 5：回归与门禁
- `python -m pytest tests/unit -q` 全绿；新用例全绿；质量门禁见 §6。

## 5. 预期成果（交付物）

1. `agent/skills_mgmt/knowledge_bridge.py`（或等价）连接器 + CLI 入口。
2. `memory_abstractor` 定时调度接线（每日 + 可选事件钩子）。
3. `skills_mgmt.review.enforce_before_publish` 强制审核链（默认 true）。
4. 配置：`learning.precipitate_enabled` / `learning.precipitate.interval_hours` / `skills_mgmt.review.enforce_before_publish`（含注释）。
5. 测试：3 个新测试文件（≥ 12 用例）。
6. 变更说明：`docs/zh/智能体学习机制重构计划/变更说明/TASK-04_变更说明.md`（含连接器 schema、调度参数、审核链决策记录）。

## 6. 评估标准（验收条件）

### 功能验收
- [ ] 用一条 approved 卡片实测：`convert-cards --dry-run` 输出 Skill DRAFT 草稿（字段完整）；重复执行零新增。
- [ ] 未 approved 卡片转换被拒绝（报错或跳过，不含 DRAFT 产出）。
- [ ] `precipitate_enabled=true` 且 `auto_register=false`：抽象器定时跑一轮后只产 DRAFT + 审计日志，不新增 PUBLISHED。
- [ ] `enforce_before_publish=true`：无审核记录的 Skill 发布被拒；走完 `review`（三维达标）后发布成功。
- [ ] TASK-03 的"沉淀增量"KPI 随本任务操作递增。

### 测试要求
- [ ] 新增 ≥ 12 用例全部通过；`python -m pytest tests/unit -q` 全绿。

### 质量门禁
- [ ] `python scripts/pre_commit_ci_guard.py --static-only --strict` 零新增告警。
- [ ] `python -m agent.observability.arch_rules --check` 通过（knowledge 与 skills_mgmt 的跨模块依赖必须单向，禁止循环；必要时用 lazy_loader 或抽象层隔离）。

## 7. 工程约束（仓库规则）

- 同 TASK-01 §7（git 精确路径、commit -F、hook 环境变量、勿碰并行会话文件、UTF-8 无 BOM）。
- **特别注意**：`agent/knowledge/*` 与 `tests/unit/*` 有大量并行会话未提交改动，本任务新增文件不得覆盖它们，改动 `card.py`/`service.py` 前先 `git diff` 核对基线。
- 卡片字段消费以 `agent/knowledge/schema.py` 的 Card dataclass 为准；若字段在并行会话有改动，以合并后为准并在变更说明记录。
