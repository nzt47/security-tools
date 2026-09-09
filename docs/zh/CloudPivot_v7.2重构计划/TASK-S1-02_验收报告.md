# TASK-S1-02 验收报告 — 存量技能资产 provenance/trust/undo_hint 字段回填

> 归档日期：2026-09-09
> 所属计划：CloudPivot v7.2 重构计划（S1 契约层）
> 任务：[TASK-S1-02_存量资产字段回填.md](TASK-S1-02_存量资产字段回填.md)
> 依赖输入：TASK-S1-01（Descriptor 模型 + 校验器 + Registry + 写入 API，
> `agent/descriptors/`）；S0-02 消化对象盘点（§3.2/§3.4 决策表）
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §2.3（manifest/provenance）/
> §3.2（trust 字段）/ P7.2-05（宁冗余勿误合）
> 状态：✅ 验收通过（验收清单 8/8 核验，见 §三）

---

## 一、执行摘要

1. **回填引擎交付**：新增 `agent/descriptors/backfill.py`（摸底 + 确定性规则 + 分批幂等
   回填 + 报告），消费 S1-01 写入 API（`register`/`mark_provenance`/`update_trust`/
   `set_governance`），**只写 Descriptor 层、绝不反向写 Skill 主轨**，不触碰存量
   状态机与发布门禁；模块级零依赖 skills_mgmt（读轨采用函数内懒加载，纯存储层直读）。
2. **摸底**：存量技能资产 **29 条**（主轨 21 + 文件轨独有 8），来源分布
   knowledge_distill 15 / legacy_migration 7 / external_agent 4 / manual 3；
   已有标记覆盖率：is_sensitive 2/29、config_schema 21/29、output_schema 0/29、
   来源记录 29/29、review 21/29、scripts 1/29。
3. **确定性回填规则**（PRV-1..8 / DC-1..4 / RK-1..5 / GV-0..1，规则 ID 入审计 reason）：
   provenance 按来源记录映射（builtin=verified、内部声明=declared、外来无签名=unknown）；
   data_class 自动带仅 internal（27/29），confidential 2 条**只出 candidate** 进
   NEEDS_REVIEW；risk 按执行面/破坏性指令/审批旁路映射（low 27 / medium 1 / high 1），
   destructive **绝不自动写入**（人工复核通道）。dry-run 两次结果**逐字节一致**。
4. **分批回填实施**：默认 200 条/批（实际 29 条单批），逐条经写 API 落审计轨
   （88 条审计：register 29 / provenance 29 / patch 30，actor=backfill:s1-02）；
   批级回滚经 `autosave=False + 批末 save()/失败 load()` 实现（单测注入故障验证
   整批回滚不残留半批）；重跑**幂等**（第二次 no-op 29、零写入）。
5. **外来安装 provenance 合并预检**（§2.3 manifest 思想，与既有安全预检合并）：
   `install_precheck` 结果并入 `provenance` 块（scheme+manifest → signed/declared/
   unknown + 升级路径）；`install`/`install_from_zip` 成功后 advisory 同步 Descriptor
   （台账与技能主轨同目录隔离，失败不阻断安装）。
6. **校验与回归**：回填后全量重校验 29/29 valid、0 error（31 条 warning 条目全部
   清单化：stage 未入轨 25 / provenance=unknown 4 / data_class 未回填 2）；
   descriptors 套件 **178 passed**（新增 54 + 既有 124）、技能中心+MCP 套件
   **403 passed / 1 xfailed**（与 S1-01 基线一致的 TF-IDF xfail）；本任务顺带修正
   S1-01 两个存量缺陷（CJK 名称误合并、demo 依赖共享台账），均有回归覆盖。

---

## 二、预期成果对照（任务 §三）

| # | 预期成果 | 交付 | 验收 |
|---|---|---|---|
| 1 | 存量资产 provenance/trust/undo_hint 字段回填（覆盖率报告 + dry-run 记录） | ✅ 29/29 全部回填；`data/descriptors.json`（台账，gitignore）含 29 条；
  `data/descriptors_s1_02/{摸底表,survey,dryrun,plan,run,validation,needs_*}*.json` | ✅ |
| 2 | NEEDS_UNDO_HINT / NEEDS_REVIEW 待补清单 | ✅ 资产级清单：NEEDS_REVIEW **7 条 / 6 资产**（含处置路径），NEEDS_UNDO_HINT 0（规则为 risk≥high 自动给出真实补偿描述；destructive 候选机制由单测覆盖） | ✅ |
| 3 | 外来技能安装路径 provenance 合并预检（与 install_precheck 集成） | ✅ `service.install_precheck` 并入 provenance 块；`service.install`/`install_from_zip` 接线同步；`import_queue` 行并入 provenance 建议 | ✅ |
| 4 | 全量 descriptor 重校验通过（或清单化残留） | ✅ 29/29 valid、0 error；31 条 warning 条目全部清单化（残留=待 S3 补验/待人工复核，见 §七） | ✅ |
| 5 | TASK-S1-02_验收报告.md | ✅ 本文件 | ✅ |

---

## 三、验收清单逐条核验（任务 §四）

### ✅ 1. 摸底表与实际资产规模一致（可复核计数）

摸底计数 = 主轨 `data/skills_mgmt.json` ∪ 文件轨 `data/skills_repo/<id>/skill.md`
（主轨权威，同 `skills_mgmt/registry.py` 双源口径）。单测
`TestSurvey::test_survey_counts_union/test_main_track_authoritative_over_file_track`
对合成数据独立重数验证；`TestRealInventorySmoke::test_real_inventory_survey_consistent`
对真实仓库独立重数：**29 = |main(21) ∪ repo(23)|**。摸底表：

- 资产规模：**29**（主轨 21 / 文件轨独有 8）
- 来源分布：`knowledge_distill: 15, legacy_migration: 7, external_agent: 4, manual: 3`
- 分类分布：`custom: 28, example: 1`；状态分布：`approved: 26, published: 3`
- 已有标记覆盖率：

| 标记 | 覆盖 | 说明 |
|---|---|---|
| is_sensitive | 2/29 | engineering-test-delivery、global-core-principles |
| config_schema | 21/29 | 主轨全有；文件轨 persona 8 条无 |
| output_schema | 0/29 | 存量全缺 |
| 来源记录(source) | 29/29 | 全量有来源串（manual/legacy_migration/knowledge_distill/external_agent） |
| review(verdict) | 21/29 | 主轨全有（ok）；文件轨 persona 无评审记录 |
| scripts | 1/29 | scripted-selftest（scripts/main.py） |

逐资产摸底行（id/track/category/source/status/is_sensitive/config_schema/output_schema/来源记录/review）：

| id | track | category | source | status | is_sensitive | config_schema | output_schema | 来源记录 | review |
|---|---|---|---|---|---|---|---|---|---|
| code-observability | main | custom | external_agent | published | False | True | False | True | True |
| context_aware | file_track | custom | legacy_migration | approved | False | False | False | True | False |
| emotion_expression | file_track | custom | legacy_migration | approved | False | False | False | True | False |
| engineering-test-delivery | main | custom | manual | published | True | True | False | True | True |
| frontend-state-sync | main | custom | external_agent | approved | False | True | False | True | True |
| global-core-principles | main | custom | manual | published | True | True | False | True | True |
| memory_summary | file_track | custom | legacy_migration | approved | False | False | False | True | False |
| pd-brainstorming-697b717a-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| pd-dispatching-parallel-agents-b8065ccd-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| pd-executing-plans-95cbf64a-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| pd-finishing-a-development-branch-e085de5a-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| pd-frontend-design-77ea5c4e-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| pd-receiving-code-review-8934157e-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| pd-requesting-code-review-ca5ae995-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| pd-subagent-driven-development-8c375695-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| pd-systematic-debugging-556faa20-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| pd-test-driven-development-8562c8ad-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| pd-using-git-worktrees-d516703a-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| pd-using-superpowers-3aea3fc9-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| pd-verification-before-completion-af010352-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| pd-writing-plans-f846e3a2-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| pd-writing-skills-5da20e67-skill | main | custom | knowledge_distill | approved | False | True | False | True | True |
| proactive_suggestion | file_track | custom | legacy_migration | approved | False | False | False | True | False |
| safety_guard | file_track | custom | legacy_migration | approved | False | False | False | True | False |
| scripted-selftest | file_track | example | manual | approved | False | False | False | True | False |
| self-explanatory-ui | main | custom | external_agent | approved | False | True | False | True | True |
| self_reflection | file_track | custom | legacy_migration | approved | False | False | False | True | False |
| testing-anti-patterns | main | custom | external_agent | approved | False | True | False | True | True |
| voice_interaction | file_track | custom | legacy_migration | approved | False | False | False | True | False |

### ✅ 2. 回填规则确定性可复现（dry-run 两次一致）；无"一刀切全 high/全 low"

- **确定性**：`plan_backfill()` 两次调用结果逐字节一致（`TestSurvey::test_deterministic_sorted_by_id`）；
  `run_backfill(dry_run=True)` 两次一致（`TestApplyFlow::test_dry_run_identical_and_non_mutating`）；
  实跑 CLI 输出 `dry-run 两次一致性: True`（`data/descriptors_s1_02/dryrun_latest.json`）。
- **非一刀切**：计划覆盖率
  - provenance：`unknown 4 / declared 25`（来源区分，非全 declared/全 unknown）；
  - data_class：applied `internal 27`；candidates `confidential 2`（人工复核）；
  - risk：applied `low 27 / medium 1（scripted-selftest，可执行面）/ high 1
    （global-core-principles，RK-5 破坏性免确认指令冲突）`；destructive candidates 0
    （规则永不自动写 destructive，机制由单测覆盖）。
- **护栏语境排除**：句级护栏判定（禁止/拦截/二次确认/审批后/正向可回滚恢复），
  `safety_guard` 等"提及危险操作但强制确认"的技能不被误升（单测
  `TestRiskRules::test_guard_rail_not_flagged`）；"无需弹窗确认"类免确认指令才触发
  RK-5 人工复核。

### ✅ 3. destructive 类三件套 100% 齐备或进待补清单且未静默放行

- 存量 29 条**无自动 destructive 写入**（destructive 候选必须人工复核——RK-4 规则
  只出 candidate + NEEDS_REVIEW/NEEDS_UNDO_HINT，见 `collect_needs`）；
- 写 API 强制：单测 `test_destructive_three_piece_enforced_by_validator` 验证
  `update_trust(risk=destructive)` 缺 undo/compensating 时抛
  `DescriptorValidationError`；`run_backfill` 批级校验双保险
  （`_verify_batch`：destructive ⇒ approval ∧ undo ∧ compensating）；
- destructive 候选的 NEEDS_UNDO_HINT 队列（含处置路径）由
  `TestNeedsQueues::test_destructive_candidate_undo_queue` 验证。

### ✅ 4. secret 类资产无外部端点配置（校验器通过）

- 存量无 secret 自动写入（secret 仅出 candidate：DC-4 参数含真实密钥 → 人工复核）；
- 写 API 强制：单测 `test_secret_external_endpoint_rejected_by_validator` 验证
  external_endpoint=True 时 `update_trust(data_class=secret)` 被校验器拒绝；
- 批级校验 `_verify_batch` 复核 secret ∧ external_endpoint ⇒ 拒绝。

### ✅ 5. 仅 provenance ≥ verified 的资产标记为可进自动化（其余标记人工单步）

- `automation_eligible = provenance ∈ {verified, signed}`；存量 29 条全部
  declared/unknown ⇒ `automation_eligible = 0`（诚实口径：无签名/无探针证据
  → 全部人工单步/受控，证据纪律对齐 S1-01 D8）；
- 批级校验断言 automation_eligible 与实际 provenance 一致（不一致即报错）；
- 单测 `TestProvenanceRules::test_builtin_verified` 覆盖 verified → 可自动化路径。

### ✅ 6. 回填逐条审计留痕；批量失败回滚不残留半批

- 审计：回填后台账 audit 88 条（register 29 / provenance 29 / patch 30），
  actor 全部 `backfill:s1-02`，reason 带规则 ID（PRV-x/DC-x/RK-x/GV-x）；
  人工复核通道（reviewer 提升）在 registry 审计轨同样留痕；
- 幂等：重跑第二次 `no_op 29、其余写入 0`（`TestApplyFlow::test_rerun_idempotent`）；
- 回滚：`run_backfill` 用 `autosave=False + 批末 save()`，批失败 `reg.load()` 恢复
  上一已提交批次；单测 `test_batch_rollback_no_half_state` 注入 `update_trust` 故障，
  断言失败批（gamma-import）整批不残留、此前批次保持、错误留痕；
- 保守合并：只升级/补空、绝不降级覆盖人工已置字段
  （`test_conservative_preserves_human_upgrade`：verified/confidential/high 保持）。

### ✅ 7. 既有技能中心/前端行为无回归（测试全绿）

| 套件 | 结果 |
|---|---|
| descriptors（既有 124 + 新增 54） | ✅ **178 passed / 0 failed**（4.2s） |
| 技能中心 + MCP 套件（14 文件，同 S1-01 基线） | ✅ **403 passed / 1 xfailed**（55.2s；xfail=既有 TF-IDF 基线） |
| 受影响文件复跑（test_skills_mgmt / test_skills_digest_assessor） | ✅ **141 passed / 1 xfailed**（27.4s） |

前端零改动（任务口径"如无 UI 改动则仅数据层"）；新字段由 S1-01
`list_with_trust()`（含 provenance/risk/data_class/undo 标记）供 S6 能力地图 UI 消费。
install_precheck/import_queue 仅**新增**返回键（provenance），既有键不变；
install/install_from_zip 的 Descriptor 同步为 advisory（失败仅记日志）。

### ✅ 8. 遗留待补清单明确到资产级且带处置路径

见 §七（NEEDS_REVIEW 7 条 / 6 资产，逐条带 asset_id/capability_id/scope/rule/处置路径；
NEEDS_UNDO_HINT 队列机制就绪、存量 0 条）。

---

## 四、确定性回填规则（步骤 2 落地，规则 ID 全部入审计 reason）

| 规则 | 输入特征 | 输出 |
|---|---|---|
| PRV-1 | category=builtin | verified（evidence=本地代码） |
| PRV-2 | source∈{manual,ai_assisted,workflow} | declared（内部声明） |
| PRV-3 | source∈{knowledge_distill,process_distill} | declared（蒸馏管线留痕） |
| PRV-4 | source=legacy_migration | declared（内部迁移记录） |
| PRV-5 | external_agent / github:/url:/registry:/market:/zip 等外来通道 / 外来类别 | **unknown**（无 manifest/签名）+ 升级路径 |
| PRV-0/7/8 | 安装路径：签名 / payload manifest+license / local 受控 | signed / declared / declared |
| DC-1 | 无数据信号 | internal（applied） |
| DC-2 | is_sensitive=True | confidential **candidate**（人工复核） |
| DC-3 | 内容收集个人数据（assessor DATA_COLLECT_SENSITIVE 语义） | confidential **candidate** |
| DC-4 | default_params 含真实密钥 | secret **candidate** |
| RK-1 | 指令型、无执行面/破坏性指令 | low |
| RK-2/2b | scripts/代码内容/依赖面 | medium（人工可下调） |
| RK-3 | 代码高危模式（shell/网络/动态执行/混淆） | high |
| RK-4 | 句级无护栏的破坏性指令 | destructive **candidate**（人工复核 + 三件套后写入） |
| RK-5 | 破坏性指令 + 免确认（审批旁路） | high + NEEDS_REVIEW（人工裁定） |
| GV-0 | destructive 候选 | 不静默生成补偿 → NEEDS_UNDO_HINT 队列 |
| GV-1 | risk≥high | undo_hint/compensating_action 引用真实机制（SkillRegistry.set_enabled / rollback_version） |

保守合并约束：provenance 仅单调提升/同级补证据；risk/data_class 现状为空或计划更严
才写；requires_approval 只 False→True；undo/补偿文本只在现状为空时补——人工 reviewer
已置字段永不被回填降级（对齐 S1-01 D6"合并声明不优于任何单方声明"）。

---

## 五、执行证据（2026-09-09 实跑）

```
实跑批次：1 批 × 29 条，全部 ok（回滚事件 0）
写入计数：register 29 / provenance 29 / data_class 27 / risk_level 29 /
          requires_approval 0 / undo_hint 1 / compensating_action 1 / no_op 0
          （data_class=27：2 条 confidential 候选待人工复核，未自动写入）
台账：data/descriptors.json —— 29 条 descriptor，aliases 0，variants 0
审计：88 条（register 29 / provenance 29 / patch 30），actor=backfill:s1-02
dry-run：两次一致性 True（data/descriptors_s1_02/dryrun_latest.json）
全量重校验：29/29 valid、0 error；warning 条目 31（stage 未入轨 25 /
          provenance=unknown 4 / data_class 未回填 2）——全部清单化（§七）
```

逐资产回填结果（抽查关键行；完整数据见 `data/descriptors.json`）：

| capability_id | provenance | risk | data_class | undo | stage |
|---|---|---|---|---|---|
| cp.skill.global-core-principles | declared | high | （待复核→confidential 候选） | ✅ | None |
| cp.skill.engineering-test-delivery | declared | low | （待复核→confidential 候选） | – | None |
| cp.skill.scripted-selftest | declared | medium | internal | – | None |
| cp.skill.code-observability | unknown | low | internal | – | borrowed |
| cp.skill.self_reflection | declared | low | internal | – | None |
| cp.skill.pd-brainstorming-697b717a-skill | declared | low | internal | – | None |

---

## 六、联动与文件清单

### 6.1 代码改动（新增为主；存量改动均最小、含 S1-01 缺陷修正）

| 文件 | 内容 |
|---|---|
| `agent/descriptors/backfill.py`（新增） | 摸底/规则/分批回填引擎 + 报告 + `classify_install_provenance` + `sync_skill_descriptor`；模块级零依赖 skills_mgmt |
| `agent/descriptors/__init__.py` | 公共 API 导出（backfill/bridge） |
| `agent/descriptors/registry.py`（存量微改） | **CJK 名称归一修正**：纯 CJK 名称不再清洗为空（消除"不同中文名相似度 1.0"误合并，守 P7.2-05 宁冗余勿误合） |
| `agent/descriptors/bridge.py`（存量微改） | `demo_pipeline` 改为一次性临时台账路径（不读不写共享运行时台账，消除对真实台账的隐式依赖） |
| `agent/skills_mgmt/service.py`（存量微改，additive） | `install_precheck` 并入 provenance 块；`install`/`install_from_zip` advisory 接线同步；`import_queue` 行并入 provenance 建议；`_classify_install_provenance`/`_sync_descriptor_advisory` 助手（台账与主轨同目录隔离） |
| `scripts/run_s1_02_backfill.py`（新增） | 摸底/干跑/实跑操作入口（`--dry-run`/`--json`） |
| `.gitignore` | + `data/descriptors_s1_02/` |
| `tests/unit/test_descriptors_backfill.py`（新增） | **54 例**（摸底 4 / provenance 9 / install 分类 6 / data_class 5 / risk 7 / needs 2 / apply 8 / CJK 2 / service 4 / 真实库存冒烟 2） |
| 报告（runtime，gitignore） | `data/descriptors.json`、`data/descriptors_s1_02/*`（摸底表/survey/dryrun/plan/run/needs/validation） |

### 6.2 既有接口零破坏核验

- Skill 主轨/文件轨 **零写入**（回填只写 Descriptor 台账；桥接只读守则保持）；
- `install_precheck` 既有键（ok/scheme/blocked/findings/compatibility_score…）不变，
  仅新增 `provenance`；`import_queue` 行仅新增 `provenance`；
- S1-01 审计轨、`list_with_trust()`、写入 API 语义不变（registry 仅名称相似度对
  CJK 修正，ASCII 行为 0 变化——既有 registry/bridge 测试全绿）。

---

## 七、遗留清单（待人工复核/后续阶段消费；不阻塞本任务验收）

### 7.1 NEEDS_REVIEW 待人工复核（7 条 / 6 资产；处置路径见各 disposal）

| # | 资产 | 维度 | 规则 | 原因（摘要） | 处置 |
|---|---|---|---|---|---|
| 1 | code-observability | provenance | PRV-5 | 外来（external_agent）无签名证据 → unknown | 补上游 manifest/license 声明或六步探针 → mark_provenance(verified,…) |
| 2 | frontend-state-sync | provenance | PRV-5 | 同上 | 同上 |
| 3 | self-explanatory-ui | provenance | PRV-5 | 同上 | 同上 |
| 4 | testing-anti-patterns | provenance | PRV-5 | 同上 | 同上 |
| 5 | engineering-test-delivery | data_class | DC-2 | is_sensitive=True → confidential 候选 | 人工复核确认后 update_trust(data_class=…) 写入 |
| 6 | global-core-principles | data_class | DC-2 | is_sensitive=True → confidential 候选 | 同上 |
| 7 | global-core-principles | risk | RK-5 | 内容含"破坏性操作无需弹窗确认"指令，与 v7.2 审批门冲突 | 人工裁定：确认破坏性则补三件套后 update_trust 升级 destructive；否则下调记录原因 |

### 7.2 NEEDS_UNDO_HINT

存量 **0 条**（risk≥high 的 global-core-principles 已生成引用 set_enabled/
rollback_version 的真实补偿描述）。队列机制就绪：destructive 候选 / 无法给出真实
补偿动作的 risk≥high 资产会进入该队列并带处置路径（`set_governance` 补写；补齐前
不写 destructive，校验器三件套强制不静默放行）——由
`TestNeedsQueues::test_destructive_candidate_undo_queue` 覆盖。

### 7.3 校验器 warning 残留（29 行全清单化，非 error）

- `evolution.stage 未入轨`（25 条）：存量无 30 天零回退/验收证据 → **S3 补验**（S0-02 §3.4）；
- `provenance=unknown`（4 条）：外来资产待人工提升（见 7.1）；
- `trust.data_class 未回填`（2 条）：confidential 候选待人工复核（见 7.1）。

### 7.4 交接

| # | 遗留 | 归属 |
|---|---|---|
| 1 | 7.1 人工复核清单消费（reviewer 提升通道 + 审计留痕已就绪） | 人工复核 / S2-S3 |
| 2 | MCP/内置工具 → Registry 运行时接线（本任务完成技能侧；MCP list_tools 真实装载入口） | S1-02 补/S6 |
| 3 | 能力地图 UI 消费 `list_with_trust()`（新字段徽章展示，本任务未改前端） | S6 |
| 4 | data/descriptors.json 多进程并发写（单机锁内串行，未做跨进程锁） | S2/生产化 |
| 5 | CI 终态复核（全量回归 slow 集） | Owner/CI |

---

*补记：本任务顺带修正 S1-01 两个与回填直接相关的缺陷（registry CJK 名称误合并、
demo_pipeline 依赖共享台账），均以单测/既有套件回归锁定；存量主轨（Skill/状态机/
发布门禁/前端）零改动。*
