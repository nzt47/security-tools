# TASK-S0-01 宿主形态与术语对齐（RFC + digest 更名收敛）

> 所属阶段：S0 对齐层｜依赖：无｜预估：2–3 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md`（审计缺陷 P1/P2/P3，见 01 号审计报告 §4）
> 验收报告将归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

消除 v7.2 设计文档与云枢现状之间的**形态错位与术语二义**，为后续所有重构任务提供可执行的映射基线与术语纪律。本任务交付两类产物：

1. **宿主形态与范围 RFC**：明确 v7.2（IDE 伴侣形态：VS Code 扩展/node/pnpm/VSIX 生态）与云枢（数字生命体：Python 服务 + Web/Electron 工作台）的关系，裁定哪些章节作为"机制/契约吸收"，哪些章节作为"参考形态暂不落地"，形成可追溯的架构决策。
2. **术语更名收敛**：解决 "digest/消化" 一词两义（云枢现有 `digest_skill = review()` 评审语义 vs v7.2 七态内化语义），完成更名改造，保证重构施工不再产生双语义污染。

## 二、执行步骤

### 步骤 1：盘点 digest 术语现状使用面
- 全量定位后端 `digest` 使用点：`agent/skills_mgmt/{assessor.py, service.py, models.py, reviewer.py, cleanup.py, creator.py, enhancer.py, categorizer.py, index_cache.py, log_archiver.py, memory_abstractor.py, offline_evolver.py, store.py}`（已实测约 40+67+25 处主要命中）。
- 全量定位路由使用点：`agent/server_routes/routes_skills_mgmt.py`（约 25 处，含 `/api/skills-mgmt/digest/*` 系列路由：run-all/curate/merge-safe/feed/events/stream/<skill_id> 等）。
- 全量定位前端使用点：`yunshu-ui/src/pages/hub/memory/` 下 `skill-center.tsx`、`skill-content-modal.tsx`、`skill-digest-manager.tsx`、`workflow.tsx`。
- 输出 `digest 使用面清单`（文件 × 行段 × 语义归类：评审语义 / 事件语义 / 存储文件名语义）。

### 步骤 2：起草宿主形态与范围 RFC
- 按 v7.2 §13.1 RFC 模板（背景 → 目标 → 方案对比 A/B → 推荐及理由 → 风险与缓解 → 回滚计划 → 架构四问 → 决策日志）撰写。
- 方案对比必须覆盖：
  - A. v7.2 作为**机制/契约清单**吸收进云枢（推荐——与 01 号审计报告结论一致，复用 skills_mgmt/workflow_learning/process_distill/EVO/self_healing 已有资产，只补契约与缺口）；
  - B. v7.2 作为独立 IDE 伴侣产品另起工程（不推荐——重复造轮子、双维护、许可证/数据不出域冲突）；
  - C. 渐进双形态（工作台为主、VS Code 插件评估入 P5 Backlog）。
- 明确裁决：哪些 v7.2 章节落地（§3.2 ToolDescriptor/§3.3 状态机/§4.5 SkillFactory/§5 安全治理/§6 指标 等）、哪些降级参考（§11.5 VS Code 扩展、§11.1 node/pnpm 环境、§1.7 "不做独立 Web App" 与云枢 Web UI 冲突的处理）。

### 步骤 3：设计术语映射表与更名方案
- 产出 v7.2 ↔ 云枢术语映射表（至少覆盖：digest/消化、Skill、Capability/Tool、七态阶段、Trace、Approval、stage.promote 等）。
- **更名裁定（用户已确认方向）**：云枢现有 digest（评审语义）更名为 `review`/`assess` 系（建议：`digest_skill`→`review_skill` 别名保留兼容，`digest_all`→`assess_all`，`digest_events`→`assessment_events`，`digest_feed`→`assessment_feed`，`digest_verdict`→`review_verdict`，`SkillDigestAssessor`→`SkillAssessor`，事件文件 `skills_digest_events.jsonl`→新名 + 旧名软链接兼容）；v7.2 七态内化语义后续统一称"内化流水线（Internalization）"，不使用 digest 一词。
- 输出**兼容策略**：对外 API/路由先"新名为主 + 旧名别名/重定向"，至少一个 minor 版本内双通，再择机移除旧名；存储文件读兼容旧档。

### 步骤 4：实施更名（后端 + 路由 + 前端 + 存储）
- 后端：按步骤 3 方案批量更名，保留旧名 deprecated 别名（不删除对外行为）；`digest_skill()` 保留为 `review()` 的兼容别名并在 docstring 标注"评审语义，与 v7.2 内化语义无关"。
- 路由：`/api/skills-mgmt/digest/*` 提供新命名路由（如 `/api/skills-mgmt/assess/*`）并 301/兼容旧路由；更新前端调用。
- 前端：更新 4 个文件的变量/文案/接口调用；UI 文案中"消化"改为"评审/评估"，避免与内化混淆。
- 存储：`skills_digest_events.jsonl` 迁移/兼容（读旧、写新 + 旧文件软链或双写 ≤1 版本），`cleanup.py`/`log_archiver.py` 同步更新。
- 文档：更新技能中心相关文档中的 digest 术语（至少本任务产出的映射表与 docs/zh/技能中心与消化体系收尾交付总结 相关引用处加注）。

### 步骤 5：回归与归档
- 运行技能中心相关测试套件（`test_skills_digest_assessor.py` 60 例、`test_skills_classifier.py`、`test_skill_lifecycle.py`、`test_reviewer.py`、`test_review_enforcement.py` 等）+ 全量回归抽样；前端 `tsc -b --noEmit` + `eslint`。
- 撰写验收报告（含使用面清单、更名对照表、兼容验证、测试结果）归档本目录。

## 三、预期成果

1. `RFC-宿主形态与范围.md`：含推荐方案 A 与章节落地/降级清单、决策日志（存 `docs/rfc/` 或本目录，按项目惯例）。
2. `术语映射表.md`（v7.2 ↔ 云枢，含消化对象三层说明）。
3. 后端/前端/存储更名改造提交（新名为主、旧名兼容别名）。
4. `TASK-S0-01_验收报告.md`：测试结果 + 兼容验证 + 遗留清单。

## 四、评估标准（验收清单）

- [ ] RFC 走完评审（架构四问必答），推荐方案明确且理由充分
- [ ] digest 使用面清单完整（后端/路由/前端/存储全覆盖，与实测命中数对得上）
- [ ] 更名后全库无"digest 表示评审"的语义残留（grep 复核仅剩兼容别名与注释说明）
- [ ] 旧 API/路由/存储在新名下仍可读（兼容验证用例通过）
- [ ] 技能中心测试套件全绿；全量回归无新增失败
- [ ] 前端 `tsc -b --noEmit` 与 `eslint` 零告警
- [ ] 术语映射表覆盖清单所列全部概念，且无自相矛盾
