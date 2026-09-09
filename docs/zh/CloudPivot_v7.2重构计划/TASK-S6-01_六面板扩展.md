# TASK-S6-01 六面板扩展（消化流水线 / 能力地图 / 审批收件箱 / ROI / 审计导出）

> 所属阶段：S6 UI 面板｜依赖：S2（数据/事件）、S3（流水线）、S5（评测/成本）｜预估：8–12 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §7（六面板 / 七动作 / UI 五坑 / 永不自动化五类）/P7.2-24（组件名清单/可解释性）/§13.3（联动）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

在云枢现有前端（`yunshu-ui/src/pages/hub/memory/` 技能中心：skill-center、skill-assess-manager、workflow 等）基础上，扩展 v7.2 **六面板**中的治理可观测面板（消费 S0-S5 全部数据设施），遵循 UI 五坑红线：

**目标面板与数据源：**

| 面板 | 数据源 | 说明 |
|---|---|---|
| 消化流水线（P0） | S3 stage 事件/digest.stage + descriptor.evolution | 泳道五列：轨迹采集→模式挖掘→Skill 生成→验收→灰度（对齐 §7 流水线泳道） |
| 能力地图（P1） | S1-01 registry.list_with_trust() | 能力×provenance/risk/data_class/stage 网格（消费 S1-01 遗留 #3 Registry 运行时接线） |
| 审批收件箱（P0） | S4-01 审批 + S2-03 approval 事件 | 支持批量裁决（同策略同风险一键批）；缺 undo_hint 不出现审批气泡 |
| 成本 ROI（P1） | S5-03 成本 + S3-03 ROI 报告 | ROIChart（月省/投入/阈值） |
| 自愈事故（P1） | S4-03 自愈事件 + S2-02 审计 | 有事故自动展开；备份健康卡片 |
| 记忆/技能库（P2） | 既有技能中心 + S5-01 | 折叠 |

**红线（§7 UI 五坑）**：① 别把自动化改成人审 ② 别全量实时渲染（虚拟滚动阈值 500 条 §11.2）③ 别藏入口（隐式入口留可点击记录）④ 别建独立 Web App（在云枢工作台内扩展）⑤ 别让看板说谎（不可追溯的"97%"比没有数字更危险——每个数字可溯源到数据/审计）。

## 二、执行步骤

### 步骤 1：数据 API 盘点与补齐
- 盘点现有前端调用（hub/memory 技能中心 API：assess/feed/audit 等——S0-01 更名后已为 review/assess 系）。
- 补齐后端只读 API（消费各阶段设施）：
  - 消化流水线：`GET /api/cp/digestion/pipeline`（stage 分布 + 泳道事件，聚合 <200ms §2.6 P7.2-24）；
  - 能力地图：`GET /api/cp/descriptors/map`（registry.list_with_trust + stage）；
  - 审批收件箱：`GET /api/cp/approvals/inbox`（+批量裁决 POST）；
  - ROI：`GET /api/cp/roi`（S5-03 成本聚合 + S3-03 ROI 报告）；
  - 审计导出：`GET /api/cp/audit/export`（S2-02 链式审计，CSV/JSON）。
- 前端 API 客户端 + 类型（对齐既有 apiClient 模式，自动带 token）。

### 步骤 2：六面板组件
- 新增组件（对齐 P7.2-24 组件名清单）：StatusBadge（状态灯五态：灰/蓝/黄/绿/红）/ StagePipeline（泳道五列）/ CapabilityMap / ApprovalInbox（批量裁决）/ ROIChart / IncidentCard / ReasonChain（"它现在在想什么"可解释性，非原始 JSON）。
- 虚拟滚动 ≥500 条阈值（§11.2）；P0 面板默认展开，P1/P2 折叠（§7 优先级）。
- **可解释性**：流水线卡片（如 `[cp.fs.write] borrowed→mirrored | 样本 57 | 97% | p99 420ms | ROI 月省¥340 vs 投入¥2200`——数字可溯源到台账/审计，不显示不可追溯百分比）。

### 步骤 3：动作与流程接线（只读优先，写动作走既有审批）
- 七动作中 UI 侧动作：熔断/回滚按钮（走 S4-03 整包回滚入口，须审批）、审批气泡（缺 undo_hint 不出现——查 descriptor.governance）、溯源 Diff（stage 变更 diff 查看）。
- 永不自动化五类（发布/转账/删库/改权限/push --force）→ UI 显式确认绑定单次 action + 60s 时效（§7）——接线工具执行前置（S4-03 已落后端约束，前端补确认 UI）。
- 隐式入口（右键"沉淀为 Skill"一级 + 快捷键）保留可点击记录（§7）。

### 步骤 4：审计导出与安全渲染
- 审计导出（S2-02）：管理员导出链式审计 CSV/JSON，含验签摘要（verify 结果）。
- UI 安全渲染（§5.7 机制 6，配合 S4-03）：HTML 白名单/script 全禁/外链图片代理/iframe sandbox/CSP；系统信息只走结构化槽位，外来文本恒带 TaintBadge 底色徽章；审批按钮区 DOM 隔离固定 zIndex。

### 步骤 5：回归与归档
- 回归：前端 `tsc -b --noEmit` + `eslint` 零告警；vitest（新增组件用例）；后端 API 单测；既有技能中心页面零回归。
- 撰写 `TASK-S6-01_验收报告.md`（含页面截图或 E2E 快照说明）。

## 三、预期成果

1. 后端只读/审批 API 组 + 前端类型化客户端。
2. 六面板组件（StatusBadge/StagePipeline/CapabilityMap/ApprovalInbox/ROIChart/IncidentCard/ReasonChain）。
3. 七动作 UI 接线（只读为主、写动作走审批）+ 永不自动化五类确认 UI。
4. 审计导出 + 安全渲染（TaintBadge/审批 DOM 隔离/CSP）。
5. `TASK-S6-01_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] 消化流水线泳道五列可展示真实 stage 事件（非 mock 数据），数字可溯源
- [ ] 能力地图来自 registry.list_with_trust（真实台账）
- [ ] 审批收件箱支持批量裁决；缺 undo_hint 不出现审批气泡
- [ ] ROI 数据来自 S5-03/S3-03（含月省/投入/阈值）
- [ ] 虚拟滚动阈值 500 生效；聚合 API <200ms、明细分页 <1s（实测）
- [ ] 永不自动化五类操作 UI 显式确认 + 60s 时效
- [ ] UI 安全渲染验证：外来文本 TaintBadge、无 script 注入、审批按钮区 DOM 隔离
- [ ] 审计导出含验签摘要
- [ ] 前端 tsc/eslint 零告警；vitest 新增用例全绿；既有技能中心零回归
- [ ] 无"不可追溯百分比"（五坑⑤红线自查通过）
