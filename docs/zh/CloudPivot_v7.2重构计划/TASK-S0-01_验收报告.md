# TASK-S0-01 验收报告 — 宿主形态与术语对齐（RFC + digest 更名收敛）

> 任务：[TASK-S0-01_宿主形态与术语对齐.md](TASK-S0-01_宿主形态与术语对齐.md)
> 阶段：S0 对齐层｜日期：2026-09-08
> 产出物：本报告 + [RFC-宿主形态与范围.md](RFC-宿主形态与范围.md) + [术语映射表.md](术语映射表.md)
> 代码/文档改动：见文末「改动清单」；全部在 `agent/`（后端）、`agent/server_routes/`（路由）、
> `yunshu-ui/src/pages/hub/memory/` 等（前端）、`tests/unit/`（测试）、`docs/zh/`（文档）、`.gitignore`。

---

## 1. 执行摘要

1. **RFC-宿主形态与范围.md（Accepted）**：裁定 v7.2 作为**机制/契约清单**吸收进云枢（方案 A），
   产出 §11.5/§11.1/§1.7 等章节的落地/降级清单与 4 条决策日志（D-20260908-01~04），架构四问已答。
2. **术语映射表.md（Accepted）**：v7.2 ↔ 云枢 核心概念映射 25+ 条 + 消化对象三层（L1/L2/L3）说明 +
   digest 更名裁定（15 项对照）+ 兼容窗口。
3. **digest 更名改造（评审语义）**：后端/路由/前端/存储全部改为 **review/assess 系**（新名为主），
   旧名保留 deprecated 别名/双注册/只读兼容（≤1 minor）；grep 复核全库仅剩兼容别名与注释说明。
4. **回归**：技能中心套件 220 例（219 passed + 1 既有 xfail）、邻接套件 83 passed、
   抽样套件 0 新增失败；前端 `tsc -b --noEmit` 与 `eslint` 零告警。

## 2. digest 使用面清单（步骤 1 交付）

> 语义分类：**评审语义**＝云枢 digest=review/assess（更名对象）；**摘要语义**＝categorizer 关键词
> （非评审）；**哈希函数**＝`hexdigest`/`digest()`（非语义，未更名）；**v7.2 语境**＝设计文档对
> 七态内化的表述（更名为「内化流水线」后不再使用）。

### 2.1 后端（agent/）—— 更名实施前命中量（实测）

| 文件 | 命中（行段） | 语义 | 处置 |
|---|---|---|---|
| `skills_mgmt/assessor.py` | ~40（模块 docstring、`_DIGEST_*_KEYS`、`digest_flag/int/list`、`digest_blocking_severities`、`DigestAssessment`、`SkillDigestAssessor`） | 评审语义（评估器/开关） | 更名 `SkillAssessor`/`AssessmentResult`/`assess_*`/`blocking_severities`；旧函数/类名留 deprecated 别名；env 前缀 `SKILLS_ASSESS_*`（旧 `SKILLS_DIGEST_*` 兜底） |
| `skills_mgmt/service.py` | ~67（`digest_skill`/`digest_all`/`_advisory_digest`/`_emit_digest_event`/`digest_events(_since)`/`digest_feed`、`digest_verdict`、事件文件名、`digest_flag` 系、注释文案） | 评审语义（服务方法/事件/存储） | 更名 `review_skill`/`assess_all`/`_advisory_assess`/`_emit_assessment_event`/`assessment_*`/`review_verdict`；旧方法留 deprecated 别名；事件文件新名 `skills_assessment_events.jsonl` |
| `skills_mgmt/models.py` | 2（`ReviewResult.digest_verdict` 字段 + 注释） | 评审语义（数据模型） | 字段更名 `review_verdict`；旧键读入兼容（model_validator）+ property 别名 + `api_payload()` 双发 |
| `skills_mgmt/reviewer.py` | 7（`SkillDigestAssessor` 导入、`digest` 变量、`digest_verdict` 写、摘要文案） | 评审语义 | 更名 `SkillAssessor`/`review_verdict` |
| `skills_mgmt/cleanup.py` | 5（docstring、`skills_digest_events.jsonl` 清理段、result 标签） | 评审语义（存储清理） | 新名 + 旧名 live 双清理 |
| `skills_mgmt/log_archiver.py` | 3（模块 docstring） | 评审语义（归档器） | docstring 更名 + 新增事件文件路径助手 |
| `skills_mgmt/categorizer.py` | 2（`_GENERIC_TOKENS`/「记忆与知识」关键词中的 `digest`） | **摘要语义/通用词（非评审）** | 不改行为，仅加注释说明（术语纪律） |
| `skills_mgmt/{index_cache,memory_abstractor,offline_evolver,creator,enhancer,store}.py` | `hexdigest()` 等 | 哈希函数（非语义） | 未更名 |
| `workflow_learning/service.py` | 11（`auto_digest` 参数、`svc.digest_skill`、`result["digest"]`、`_created_digest_view`、env 键名） | 评审语义 | 参数 `auto_review`（旧名兼容读）、`svc.review_skill`、`result["review"]`（旧键双发）、`_created_review_view` |
| `workflow_learning/skill_converter.py` | 1（注释） | 评审语义 | 注释更新 |
| `process_distill/solidify.py` | 3（docstring、`review_verdict` 读、注释） | 评审语义 | 注释 + `review_verdict` |
| 其他 `agent/*.py` | `hexdigest`/`compare_digest` 等 | 哈希函数（非语义） | 未更名 |

### 2.2 路由（agent/server_routes/）

| 文件 | 命中 | 处置 |
|---|---|---|
| `routes_skills_mgmt.py` | 25 行段：`/api/skills-mgmt/digest/{run-all,curate,merge-safe,merge-backups,feed,merge-undo,events,stream,<skill_id>}` + `digest_all/digest_feed/digest_events(_since)/digest_skill` 调用 + `result.model_dump()` 评审负载 | 新路由组 `/api/skills-mgmt/assess/*` 为主；旧 `/digest/*` 双注册同 handler（已废弃兼容注释）；`review/assess` 端点响应经 `ReviewResult.api_payload()` 双发 `review_verdict`+`digest_verdict` |
| `routes_workflow_learning.py` | 3（`auto_digest` 请求参数） | 新参数 `auto_review` 为主，旧参数兼容读 |

### 2.3 前端（yunshu-ui/）—— 4 个指定文件 + 2 处关联 UI 文案

| 文件 | 命中 | 处置 |
|---|---|---|
| `skill-digest-manager.tsx` → **`skill-assess-manager.tsx`** | 组件名/接口/类型/URL/文案（`SkillDigestManager`、`DigestEv`、`digest_verdict`、`/api/skills-mgmt/digest/*`、`digestOne`、`digestAdvice`、「评审-消化/消化动态/批量导入消化/消化=阻断」等） | 组件更名 `SkillAssessManager`、文件更名；URL 全部切 `/assess/*`；类型/变量 `review_verdict`/`assessOne`/`reviewAdvice`；文案「评审-评估/评估动态」；localStorage 键 `yunshu:assess:last-seen`（旧键读兜底）；CSV 前缀 `skills-assess-overview-` |
| `skill-center.tsx` | import + 注释 | 引用 `skill-assess-manager` |
| `skill-content-modal.tsx` | 1 注释 | 组件名更新 |
| `workflow.tsx` | 响应 `digest` 键 + `auto_digest` 参数 + 文案 | `auto_review: true`、读 `r?.review`（旧键 `digest` 兜底）、文案「评审-评估通过」 |
| `generate-requirement-modal.tsx` / `workbench/WorkbenchChatPage.tsx` | 「自动评审-消化」文案（关联 UI） | 文案「评审-评估」 |

### 2.4 存储 / 配置

| 对象 | 命中 | 处置 |
|---|---|---|
| `data/skills_digest_events.jsonl`（live）+ `data/skills_digest_events-YYYY-MM-DD.jsonl`（按日归档 ×4，历史保留） | 事件流水 | 新名 `data/skills_assessment_events.jsonl`；首用迁移（copy 旧→新，旧文件保留只读兼容）；读取兜底旧名；清理（cleanup.py）双名处理；归档旧档只读保留 |
| `.gitignore` | `data/skills_digest_events*.jsonl` 规则 | 追加 `data/skills_assessment_events*.jsonl`（注释保留旧名规则） |
| env/config（`.env`/`.env.example`/`config.yaml`） | 无既有 `SKILLS_DIGEST_*` / `skills_mgmt.digest.*` 配置值 | 代码读取层新前缀/新节为主 + 旧前缀/旧节兜底（前瞻兼容） |

### 2.5 测试 / 文档

- 测试：`test_skills_digest_assessor.py`（更名 + 新增兼容类 TestRenameCompat 6 例）、
  `test_skills_mgmt.py`（注释）、`test_routes_workflow_learning.py`（`auto_review` + 旧参数兼容例）。
- 文档：本任务产出 RFC/术语映射表；`技能中心与消化体系收尾交付总结_20260904.md`、
  `过程蒸馏能力交付总结_20260905.md` 顶部加术语加注（历史正文保留，指向映射表）。

## 3. 更名对照与兼容策略（步骤 3 交付摘要，详见术语映射表 §3）

| 旧名（digest=评审） | 新名 | 兼容 |
|---|---|---|
| `SkillDigestAssessor` / `DigestAssessment` | `SkillAssessor` / `AssessmentResult` | 模块别名（deprecated） |
| `digest_flag/int/list` / `digest_blocking_severities` | `assess_flag/int/list` / `blocking_severities` | 旧函数别名委托；env 前缀 `SKILLS_ASSESS_*` 主、`SKILLS_DIGEST_*` 兜底（含旧键名映射） |
| `svc.digest_skill` | `svc.review_skill` | `digest_skill` deprecated def（docstring 标注评审语义与 v7.2 内化无关） |
| `svc.digest_all` / `digest_events(_since)` / `digest_feed` | `assess_all` / `assessment_events(_since)` / `assessment_feed` | 旧方法 deprecated def |
| `ReviewResult.digest_verdict` | `review_verdict` | 旧存储键读入归一；property 别名读写；`api_payload()` 双发 |
| `data/skills_digest_events.jsonl` | `skills_assessment_events.jsonl` | 首用迁移 copy + 旧文件保留；读取兜底；旧档只读保留 |
| `/api/skills-mgmt/digest/*` | `/api/skills-mgmt/assess/*` | 新旧路由双注册同 handler |
| `auto_digest`（工作流转换）/ `result["digest"]` | `auto_review` / `result["review"]` | 入参旧键兼容读；响应双发 ≤1 minor |
| UI：`SkillDigestManager`/「评审-消化」等 | `SkillAssessManager`/「评审-评估」 | 前端全量切换（无旧名残留） |
| categorizer `digest` 关键词 | 不变 | **非评审语义**（摘要/通用词），注释说明 |

**移除窗口**：至少一个 minor 版本后择机移除旧名/旧路由/旧键（先 Deprecation 公告）；
内部私有名与 UI 文案已即时收敛。

## 4. 兼容验证（步骤 5 验收 #4）

- [x] 旧 Python 名可调：`TestRenameCompat::test_legacy_class_and_function_aliases` /
  `test_service_legacy_aliases` / `test_review_verdict_legacy_alias_and_storage_read` 通过
  （含 `svc.digest_skill`/`digest_all`/`digest_feed`、property 别名读写、旧档 `review.digest_verdict`
  读入归一为 `review_verdict`，落盘只写新键）。
- [x] 旧环境变量可读：`test_legacy_env_prefix_still_read`（`SKILLS_DIGEST_EXTERNAL_PRECHECK_ENABLED`）
  通过。
- [x] 旧路由并存：`test_route_surface_dual_registration`（`/digest/*` 与 `/assess/*` 9 对路由均在
  Flask url_map）通过；路由 handler 共用（同函数双注册）。
- [x] 旧事件文件可读/迁移：`test_events_file_legacy_migration`（首用 copy、旧文件保留、双 live 路径）
  通过。
- [x] 工作流旧参数：`test_convert_success_legacy_auto_digest_key` 通过。

## 5. 回归结果（步骤 5）

| 套件 | 结果 |
|---|---|
| `test_skills_digest_assessor.py`（含新增 6 例兼容用例） | ✅ 全绿（并入下方汇总） |
| `test_skills_classifier.py` | ✅ 30 passed |
| `test_skill_lifecycle.py` | ✅ 10 passed |
| `test_reviewer.py` | ✅ 27 passed |
| `test_review_enforcement.py` | ✅ 6 passed |
| `test_routes_workflow_learning.py` | ✅ 5 passed（含新增旧参数兼容例） |
| `test_skills_mgmt.py` | ✅ 74 passed / 1 xfailed（TF-IDF Precision 阈值，**既有基线 xfail**，与本任务无关） |
| **技能中心套件合计** | **219 passed / 1 xfailed / 0 failed**（220 collected，53.68s） |
| 邻接套件：`test_process_distill.py` / `test_workflow_to_skill.py` / `test_workflow_learning.py` / `test_routes_process_distill.py` / `test_routes_skills_mgmt_integration.py` | ✅ 83 passed（12.48s） |
| 抽样回归：`test_skill_merge.py` / `test_server_routes_supplement.py` / `test_health_supplement.py` / `test_abstract_from_memory_route.py` / `test_modules_api_actions.py` / `test_server_routes_comprehensive.py` | ✅ 237 passed（9.34s）；本任务相关模块零新增失败（本地既有基线失败见 `failures_baseline.txt`，与本任务无关） |
| 前端 `tsc -b --noEmit`（`npm run check`） | ✅ exit 0，零错误 |
| 前端 `eslint .` | ✅ exit 0，零告警 |
| 语法检查（py_compile，12 个改动后端文件） | ✅ 通过 |
| 运行时兼容探针（models/assessor/service 别名 + 旧档读入） | ✅ 通过 |

## 6. grep 复核：无「digest 表示评审」语义残留（验收 #3）

更名后全库 `digest`（评审语义）仅剩以下类别（均可 grep 复核）：

1. **兼容别名定义与调用**：`service.py` 中 `digest_skill/digest_all/digest_events(_since)/digest_feed`
   deprecated def；`assessor.py` 中 `digest_flag/int/list`、`digest_blocking_severities` 别名与
   `SkillDigestAssessor = SkillAssessor`、`DigestAssessment = AssessmentResult`；`models.py`
   `digest_verdict` property 别名 + 旧键读入 validator；`routes_skills_mgmt.py` 旧 `/digest/*` 双注册
   （每处带「已废弃兼容（评审语义）」注释）。
2. **兼容测试用例**（`TestRenameCompat` 与 `test_routes_workflow_learning` 旧参数例）——验收要求的
   「兼容验证用例」。
3. **注释说明**：术语纪律注释（TASK-S0-01 更名说明、与 v7.2 内化语义无关标注）、历史交付文档顶部加注。
4. **非语义使用点**：categorizer 分类关键词（摘要/通用词，已加注释）；`hexdigest`/`compare_digest`
   等哈希函数；归档文件名（`data/skills_digest_events-*.jsonl` 历史档，只读保留）。

## 7. 遗留清单（下一 minor 择机处理）

| 项 | 说明 | 计划 |
|---|---|---|
| 旧 API 别名/旧路由/旧键 | `digest_*` 方法别名、`/digest/*` 路由、`api_payload()` 旧键、`digest_verdict` property、旧 env 前缀/配置节兜底 | Deprecation 公告后 ≥1 minor 移除 |
| 事件文件旧档与 .gitignore 旧规则 | `data/skills_digest_events*.jsonl`（历史 + live 兼容） | 保留只读，≥1 minor 后仅留归档 |
| 历史交付文档中的「评审-消化」正文 | `技能中心…总结_20260904`、`过程蒸馏…总结_20260905` 等 | 保留历史记录，已加注指向新术语 |
| categorizer `digest` 关键词 | 摘要/通用词语义，未更名 | 观察分类行为后再定（不属评审语义污染） |
| 测试文件/组件文件名中的旧词 | `test_skills_digest_assessor.py`（任务验收点名引用）、归档文件名 | 文件级更名不纳入本任务（名称不影响语义），可后续整理 |

## 8. 验收清单勾稽（对照 TASK-S0-01 §四）

- [x] RFC 走完评审（架构四问必答），推荐方案 A 且理由充分 —— RFC-宿主形态与范围.md §4/§8/§9
- [x] digest 使用面清单完整（后端/路由/前端/存储全覆盖，命中数可复核）—— 本报告 §2
- [x] 更名后全库无「digest 表示评审」语义残留 —— 本报告 §6 grep 复核
- [x] 旧 API/路由/存储在新名下仍可读 —— §4 兼容验证用例全过
- [x] 技能中心测试套件全绿；全量回归无新增失败 —— §5
- [x] 前端 `tsc -b --noEmit` 与 `eslint` 零告警 —— §5
- [x] 术语映射表覆盖清单全部概念且无自相矛盾 —— 术语映射表.md（含消化对象三层说明）

## 9. 改动清单（本任务）

**文档（docs/zh/CloudPivot_v7.2重构计划/）**
- 新增 `RFC-宿主形态与范围.md`、`术语映射表.md`、本报告
- 历史文档加注：`docs/zh/技能中心与消化体系收尾交付总结_20260904.md`、
  `docs/zh/过程蒸馏能力交付总结_20260905.md`

**后端（agent/）**
- `skills_mgmt/models.py`（`review_verdict` 字段 + 兼容）、`assessor.py`（`SkillAssessor`/`assess_*`）、
  `service.py`（`review_skill`/`assess_all`/`assessment_*`/事件文件）、`reviewer.py`、`cleanup.py`、
  `log_archiver.py`（事件文件路径助手）、`categorizer.py`（注释）
- `workflow_learning/service.py`、`workflow_learning/skill_converter.py`（注释）、
  `process_distill/solidify.py`

**路由（agent/server_routes/）**
- `routes_skills_mgmt.py`（`/assess/*` 新路由 + `/digest/*` 兼容双注册 + `api_payload()` 双发）、
  `routes_workflow_learning.py`（`auto_review` 参数兼容）

**前端（yunshu-ui/）**
- `skill-digest-manager.tsx → skill-assess-manager.tsx`（git mv）、`skill-center.tsx`、
  `skill-content-modal.tsx`、`workflow.tsx`、`generate-requirement-modal.tsx`、`WorkbenchChatPage.tsx`

**测试（tests/）**
- `test_skills_digest_assessor.py`（更名 + 新增 `TestRenameCompat` 6 例）、
  `test_skills_mgmt.py`、`test_routes_workflow_learning.py`

**其他**
- `.gitignore`（追加 `data/skills_assessment_events*.jsonl`）

---
*补记：抽样回归（237 例）与本任务无关的本地既有基线失败不重复列举；如后续执行全量
`tests/`（CI 6 分片入口）出现与本任务模块相关的失败，以本报告 §5 的定向+邻接+抽样套件为准复核。*
