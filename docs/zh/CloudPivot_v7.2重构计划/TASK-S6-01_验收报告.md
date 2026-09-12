# TASK-S6-01 验收报告 —— 六面板扩展（消化流水线 / 能力地图 / 审批收件箱 / ROI / 自愈事故 / 记忆技能库 + 审计导出）

> 任务书：`TASK-S6-01_六面板扩展.md`｜分发壳：`START-S6-01_六面板扩展.md`
> 基线：`master` / `5984e03e`｜worktree：`s601`（`.worktrees/s601`）
> 验收日期：2026-09-12｜验收方式：逐条对照任务书 §四（含 U1–U10 与 §0.3 口径纪律）
> 关联：[`TASK-S6-01_移交项处置表.md`](TASK-S6-01_移交项处置表.md)

---

## 零、结论

| 项 | 结果 |
|---|---|
| 任务书 §四 验收清单 | **20/20 通过**（逐条见 §二） |
| §0.2 移交项 U1–U10 | **10/10 收口或明确登记**（详见移交项处置表） |
| §0.3 口径纪律 | **通过**（"无不可追溯百分比"自查机器化，见 §二 #19） |
| 后端新增单测 | **76 例全绿**（`tests/unit/test_s6_01_ui_panels.py`）；新增模块覆盖率 82–96%（加权 83%） |
| 前端新增用例 | **45 例全绿**（`src/pages/hub/governance/*.test.tsx`） |
| 前端既有回归 | **569 passed / 0 failed**（68 文件，零新增失败） |
| 后端邻接回归 | **833 passed / 0 failed**（14 个相关套件） |
| 后端全量单测 | 16414 passed / **12 failed**，其中 **10 例在未改动基线逐例同现**、**2 例为 Windows 临时目录竞态**（单跑通过）⇒ **0 回归**（见 §三） |
| 前端门禁 | `tsc -b --noEmit` 0 error；`eslint .` **0 error / 0 warning** |
| 后端门禁 | kwarg 扫描两条 **0 处**；`importlinter` **2 kept / 0 broken**；`mypy`（改动模块）**0 error**；`arch_rules --check` **0 未豁免违规** |
| 真实浏览器 E2E | 七面板全部渲染**真实数据**并截图（`reports/s6_01/shots/*.png`，证据文本见 `panel_snapshots.json`） |

**重要前置发现（N1）**：`agent/server_routes/__init__.py::register_all_routes` 无调用方（S2-02 已记为死代码），而 `routes_approval` **只**在其中登记 ⇒ **S4-01 交付的审批 HTTP 面此前在生产入口从未注册**。本任务已在 `app_server.py` 补显式注册（审批面 + 面板路由），使审批收件箱的写链路真正可达。详见移交项处置表"附：新增发现"。

---

## 一、交付物清单

### 1.1 后端

| 文件 | 行数 | 内容 |
|---|---|---|
| `agent/ui_panels/__init__.py` | 66 | 包说明 + 面板→数据源台账引用 |
| `agent/ui_panels/schema.py` | 268 | **口径纪律机器化**：`metric()`（唯一上屏出口）/`absent()`/`sample_discipline()`/`untraceable_scan()`（五坑⑤红线自查）/`panel_map()` + `OPAQUE_PREFIXES` |
| `agent/ui_panels/data.py` | 1387 | 六面板 + 审计导出 + 安全渲染数据层（全部只读、目录可注入、缺位记 None） |
| `agent/server_routes/routes_ui_panels.py` | 917 | **16 条 HTTP 路由**：只读面板 11 + 边界确认签发 1 + 七动作 1 + 批量裁决 2 + CSV 导出 1 |
| `agent/server_routes/routes_approval.py` | +~110/-~75 | 抽出 `_do_decision_with()`（**单条与批量共用同一审批链**）；`_do_decision()` 保留为 HTTP 包装，对外行为不变 |
| `agent/server_routes/__init__.py` | +4 | 注册 `routes_ui_panels`（保持单一注册入口完整） |
| `app_server.py` | +34 | 显式注册审批面与面板路由（修 N1） |

新增路由清单（前缀 `/api/cp`，全部 `@require_token` + §7.0 矩阵鉴权）：

```
GET  /api/cp/panels                     面板索引（优先级 + 数据源台账）
GET  /api/cp/digestion/pipeline          消化流水线（泳道五列）
GET  /api/cp/descriptors/map             能力地图
GET  /api/cp/approvals/inbox             审批收件箱
GET  /api/cp/roi                         ROI / 成本 / §6.7 指标
GET  /api/cp/observability/stream        ACR/UTC + 降级拓扑 + 逃逸清单
GET  /api/cp/healing/incidents           自愈事故 + 备份健康
GET  /api/cp/memory/skills               记忆 / 技能库（U5 契约）
GET  /api/cp/security/authz-alerts       越权告警聚合（U3）
GET  /api/cp/security/render-state       安全渲染 / 边界词常量（U1 单一来源）
GET  /api/cp/audit/export                审计导出（含验签摘要）
GET  /api/cp/audit/export.csv            审计导出 CSV
POST /api/cp/confirmations/<action>      签发单次确认凭据（单次 + 60s）
POST /api/cp/actions/<action>            七动作（+ 永不自动化五类）
POST /api/cp/approvals/batch/link        批量裁决：签发逐条绑定的一次性链接
POST /api/cp/approvals/batch            批量裁决：提交（逐条复用单条审批链）
```

### 1.2 前端（`yunshu-ui`，**工作台内扩展，未建独立 Web App**）

| 文件 | 内容 |
|---|---|
| `src/lib/cpPanelsTypes.ts` | 面板类型契约（与后端 JSON 逐字对应，含 `Metric` 口径结构） |
| `src/lib/cpPanelsApi.ts` | 类型化 API 客户端（复用既有 `lib/apiClient` + `apiToken`） |
| `src/pages/hub/governance/index.tsx` | 六面板 + 审计导出（`GovernancePanels` 单组件按 `panel` 参数渲染） |
| `src/pages/hub/governance/components.tsx` | `StatusBadge`（五态）/`MetricValue`/`VirtualList`/`ReasonChain`/`TaintBadge`/`ApprovalZone`（Shadow DOM）/`IncidentBanner`/`PanelHeader` |
| `src/pages/hub/governance/actions.tsx` | 七动作工具条 + 永不自动化五类确认 UI（60s 倒计时） |
| `src/pages/hub/governance/implicitEntry.tsx` | 隐式入口可点击记录（五坑③） |
| `src/pages/hub/governance/usePanel.ts` | 数据 hook + **U1 常量单例** + 数值格式化（缺位渲染"—"） |
| `src/workbench/hubNav.tsx` | 新增「治理面板」栏目（7 个子项）+ `derivePanelParams` 支持 `governance/<panel>` |
| 测试 3 个 | `components.test.tsx`（18）/`panels.test.tsx`（26）/`perf.test.tsx`（2） |

### 1.3 脚本与产物

| 文件 | 用途 |
|---|---|
| `scripts/dev/cp_perf_sink.py` | U6 性能结果接收端（落盘 `reports/s6_01/*.json`） |
| `scripts/dev/cp_perf_probe.py` | **真实浏览器**状态灯双口径 + 首屏 FCP/LCP 实测 |
| `scripts/dev/cp_panel_evidence_server.py` | 验证据点：最小可运行工作台（注册 `/api/cp/*` + 工作台模板） |
| `scripts/dev/cp_panel_snapshots.py` | 七面板截图 + E2E 快照（真实渲染文本作证据） |
| `reports/s6_01/shots/*.png` | 七面板页面截图（7 张） |
| `reports/s6_01/shots/panel_snapshots.json` | 快照索引（每面板的证据文本 + 导航诊断） |
| `reports/s6_01/*.json` | U6 性能实测原始数据 |

### 1.4 文档

| 文件 | 变更 |
|---|---|
| `docs/zh/CloudPivot_v7.2重构计划/TASK-S6-01_移交项处置表.md` | **新增**：U1–U10 逐条处置证据 + 2 项新增发现 |
| `docs/zh/CloudPivot_v7.2重构计划/TASK-S6-01_验收报告.md` | **新增**（本文件） |
| `docs/zh/CloudPivot_v7.2重构计划/S6-01_交付结案报告_20260912.md` | **新增** |
| `docs/PERF_BUDGET_REBASED.md` | **回填**：§一 新增第 6 条、§二 #1/#2 由 ❓ 改实测、§五 标记补测完成、**新增 §八**（完整口径与原始数据） |

---

## 二、任务书 §四 验收清单逐条核验

### ✅ 1. 消化流水线泳道五列可展示真实 stage 事件（非 mock），数字可溯源

**证据（真实台账，非 mock）**：

```
$ curl http://127.0.0.1:5757/api/cp/digestion/pipeline
{
  "lanes": [
    {"lane":"trace_collect","title":"轨迹采集","event_count":{"value":29, "source":"agent/observability/events.py::digest.stage", ...}},
    {"lane":"pattern_mining","title":"模式挖掘","event_count":{"value":0, ...}},
    {"lane":"skill_generation","title":"Skill 生成","event_count":{"value":0}},
    {"lane":"acceptance","title":"验收","event_count":{"value":0}},
    {"lane":"gray","title":"灰度","event_count":{"value":0}}
  ],
  "summary": {"digest_stage_events":{"value":29}, "capabilities_touched":{...}},
  "clock": {"wall":"CLOCK_WALL（真实墙钟，S3-03 shadow 报告口径）"}
}
```

泳道卡片实例（**真实 `digest.stage` 事件**，`data/events/events.jsonl`）：

```json
{"capability_id":"cp.skill.voice_interaction","from_stage":null,"to_stage":"borrowed",
 "applied":true,"verdict":"applied","scope":"first_entry_backfill",
 "digest_run_id":"dg_84d455740ef13ddf3aa3153f","ts":"2026-09-11T01:30:28.573+08:00"}
```

- 泳道归属规则写在 `data.py::_lane_of()`（按事件**如实字段** `scope` / `to_stage` 判定，不猜）。
- 颜色/状态映射在 `components.tsx::toneForStage()`（只做展示映射）。
- **数字可溯源**：每个 `event_count` 都是 `metric()` 产物，带
  `source` / `formula` / `dataset` / `sample_size`，前端 `MetricValue` 提供"来源"入口。

**用例**：`TestDigestionPipeline`（5 例，含 `test_every_number_is_traceable`）。

---

### ✅ 2. 能力地图来自 `registry.list_with_trust`（真实台账）

```
$ curl "http://127.0.0.1:5757/api/cp/descriptors/map?limit=1"
"source": "agent/descriptors/registry.py::list_with_trust()",
"total": {"value": 32},
"distribution": {"by_stage": {"borrowed": 32},
                 "by_risk": {"(未标注)": 3, "high": 1, "low": 27, "medium": 1}}
```

行实例（真实台账 `data/descriptors.json`）：

```json
{"capability_id":"cp.builtin.read_file","stage":"borrowed","provenance":"verified",
 "risk_level":null,"data_class":null,"requires_approval":false,"audit_level":"summary",
 "has_undo_hint":false,"has_compensating_action":false,
 "approval_bubble_eligible":false,
 "success_rate":{"value":null,"available":false,"sample_size":0,
   "note":"样本 < 20 时只披露不考核（§0.3）；数据源缺位记 None，不以 0 冒充；样本为 0 时记 None（台账的 0 = 无样本，不是 0% 命中率）"}}
```

**实现期修复的真实缺陷**：台账里 `sample_count=0` 时 `quality.success_rate` 缺省为
`0.0`，直接上屏就是"能力命中率 0%"这种**不可追溯的百分比**（五坑⑤反面教材）。
已修为"样本为 0 ⇒ 记 `None`"，并加回归用例
`TestCapabilityMap::test_zero_samples_is_none_not_zero_percent`。

**用例**：`TestCapabilityMap`（5 例）。

---

### ✅ 3. 审批收件箱支持批量裁决；缺 undo_hint 不出现审批气泡

**批量裁决（同策略同风险一键批，§7 逐字）**：

```
$ curl http://127.0.0.1:5757/api/cp/approvals/inbox
"batch_groups": [{"batch_key":"meta_policy|L1|unknown","object_type":"meta_policy",
                  "level":"L1","risk":"unknown","count":2,
                  "record_ids":["appr-20260822185704676914-7982bab9","appr-…"],
                  "bubble_visible_count":0}]
"decision_contract": {"operation_human_only":"§7.0 审批 Approve/Deny 仅 human，矩阵单表校验",
                      "requires_second_factor":"destructive 风险强制二次认证（§5.7⑦）",
                      "link_ttl_seconds":900,
                      "note":"批量裁决=逐条走同一审批链（会话+CSRF+链接+二次认证+矩阵），不新增旁路"}
```

**两段式（关键设计）**：单条审批链接与 `record_id` **绑定**且**一次性**，故批量必须
先由服务端签发 N 条逐条绑定链接（`batch/link`，token 集合入 HttpOnly Cookie），
再逐条核销（`batch`）。**每条仍走 `_do_decision_with()`——单条端点的同一函数**，
因此"批量"没有引入任何新的权限实现或旁路。

**缺 undo_hint 不出现审批气泡**（后端判定，前端不放宽）：

```json
{"record_id":"appr-20260822185704676914-7982bab9",
 "governance":{"undo_hint":"","compensating_action":"","undo_hint_status":"unresolved"},
 "bubble":{"visible":false,"rule":"缺 undo_hint 不出现审批气泡（§7）"}}
"bubble_hidden": {"value": 2, "formula": "缺 undo_hint 且无补偿动作的待审记录数（这些记录不出气泡）"}
```

**用例**：`TestApprovalInbox`（3 例含 `test_missing_undo_hint_hides_bubble`）、
`TestApprovalBatch`（5 例：两段式全链路 / 混合策略拒绝 / 驳回必填理由 /
未知 batch 拒绝 / 未开会话拒绝）、`panels.test.tsx::3`（6 例）。

---

### ✅ 4. ROI 数据来自 S5-03 / S3-03（含月省/投入/阈值）

| 数字 | 值（实测） | 来源 |
|---|---|---|
| 日归一成本 | 0.093 分 | `utc.utc_daily`（`COST_SOURCE_OF_TRUTH="events"`） |
| 日预算阈值 | 配置值 | `cost_brake.BrakeConfig.daily_cents` |
| 日/基线比 | `cost_effective / baseline` | `cost_brake.cost_daily_view`（基线 0 ⇒ None） |
| 审批衰减率 | 0.1037（10.4%） | `cost_brake.approval_decay_rate`（**披露不考核**） |
| §6.7 指标字典 | `eval.slo_weekly.v1`（11 项 / 8 项可计算） | `agent/eval/metrics.compute_metrics` |
| 月省 / 投入 / 摊销 / 净收益 | 每能力从 `promote_pr` 的 `decision.json` 读 `ROIReport` | `agent/digestion/internalize.py::ROIReport` |

内化决策卡片的 ROI 四元组（`data.py::_decision_card`）：

```
月省 = (上游单位成本 − 自研单位成本) × 月样本数
摊销 = 一次性投入 ÷ 12
净值 = 月省 − 月摊销
```

**用例**：`TestRoiPanel`（4 例）、`panels.test.tsx::4`（4 例）。

---

### ✅ 5. 虚拟滚动阈值 500 生效；聚合 API <200ms、明细分页 <1s（实测）

| 项 | 实测 | 预算 | 判定 |
|---|---|---|---|
| 消化流水线聚合（2000 条真实结构事件，3 次中位） | **36.0 ms** | <200 ms | ✅ |
| 明细分页（3000 条事件，每列 ≤500 条） | 中位 **<1 s**（用例通过） | <1 s | ✅ |
| 前端虚拟滚动 | `items.length > 500` ⇒ 启用 | 阈值 500 | ✅ |

**口径声明**：计时用 `time.perf_counter`（单调墙钟）；台账一次性加载（descriptor
加载 / 首读页缓存）**不计入**聚合预算（与生产一致：进程内经其他路由预热后再测）；
取 3 次中位数。见 `TestPerformanceBudget._median_ms()` 的注释。

**用例**：`TestPerformanceBudget`（3 例，含 `limit=99999` 被夹到 500 的断言）、
`components.test.tsx`（虚拟滚动 3 例：≤500 直渲 / >500 只渲可视窗口 / 滚动窗口跟随）。

---

### ✅ 6. 永不自动化五类操作 UI 显式确认 + 60s 时效

**类别词表来自后端**（U1，前端不得自定义）：

```
GET /api/cp/security/render-state →
  boundary_words.never_automated = ["transfer","publish","drop_database","permission_change","force_push"]
  boundary_words.labels          = {"transfer":"转账","publish":"发布","drop_database":"删库",…}
  boundary_words.max_ttl_seconds = 60.0
  boundary_words.single_action_bound  = true
  boundary_words.accepts_text_approval = false
```

**两段式确认**：

1. `POST /api/cp/confirmations/<action>` 签发凭据（**不接受 ttl 参数**，
   TTL 由 `boundary_words` 的 60s 硬上限决定）⇒ 返回 token **仅此一次**；
2. `POST /api/cp/actions/<action>` 携带 token 执行 ⇒ 但**五类不执行**，
   只转审批提案（`executed:false` / `submitted_for_approval:true`）。

**边界行为实测**：

| 场景 | 结果 |
|---|---|
| 无凭据直接执行五类 | **428** `boundary_confirmation_required`（`boundary.hits` 非空） |
| 凭据核销后再用同一 token | **428**（单次性：已核销必须重新确认） |
| 凭据换目标（action 摘要不一致） | **428**（绑定单次 action，非"文本批准"） |
| 请求体塞 `ttl_seconds: 86400` | 签发结果仍是 **60.0s**（前端不可自定义） |
| 五类齐全性 | 与 `boundary_words.BOUNDARY_LABELS` 键集**一一对应（5 类）** |

UI 侧：`NeverAutomatedPanel` 显示倒计时（每 250ms 刷新），到点标"凭据已失效
（需重新确认）"并禁用提交按钮。

**用例**：`TestSevenActions::test_never_automated_needs_confirmation_before_execution` /
`test_confirmation_issue_and_consume_is_single_action_bound` /
`test_confirmation_binds_action_not_text` /
`test_confirmation_ttl_cannot_be_overridden_from_body` /
`test_all_five_categories_are_covered`；`panels.test.tsx::八`（3 例）。

---

### ✅ 7. UI 安全渲染验证：外来文本 TaintBadge、无 script 注入、审批按钮区 DOM 隔离

| 要求 | 实现 | 证据 |
|---|---|---|
| 外来文本恒带 TaintBadge | `components.tsx::TaintBadge` 用后端 `taint_badge.class/base_class/attr` | 审批收件箱真实行（`taint.taint=true`）渲染出徽章；`components.test.tsx` 断言 class 名 |
| 常量缺位不自定义 | 拿不到常量 ⇒ **返回 `null`**（不渲染徽章） | `components.test.tsx::U1`（1 例） |
| 无 script 注入 | CSP 指令集来自后端（`script-src 'none'` 等 11 条），随 `render-state` 下发 | `test_render_state_exposes_u1_constants` |
| 审批按钮区 DOM 隔离 | `ApprovalZone`：`attachShadow({mode:'open'})` + 影子树内联样式 + `createPortal` | `panels.test.tsx::八`：**light DOM 查不到"熔断"，影子树内查得到** |
| 固定 z-index | `data-cp-z-index="2147483000"`、`style.zIndex="2147483000"`、`isolation:"isolate"`、`contain:"layout style paint"` | 同上（4 个断言） |
| 前端不声明身份 | `runAction` / `issueConfirmation` 请求体**不含** `actor` / `actor_type` | `panels.test.tsx`：`expect(body).not.toHaveProperty('actor_type')` |

---

### ✅ 8. 审计导出含验签摘要

```
$ curl "http://127.0.0.1:5757/api/cp/audit/export?limit=5"
"verify": {"ok":true,"checked":5,"first_bad_seq":null,"bad_seqs":[],"reason":"ok"},
"verify_scope": "exported",
"elapsed_ms": {"value":266.5,"unit":"ms","note":"verify_scope=exported 时与导出区间同口径；full 会全链重算"},
"chain_head": {"count":21414,"last_seq":21414,"head_self_hash":"…"}
```

CSV 导出的首行即验签摘要（`# verify_ok=… checked=… first_bad_seq=… reason=…`），
故导出文件**自证其完整性**。

**验签范围口径**（性能与语义都要求显式标注）：默认 `exported`（仅本次导出区间）；
`head`（链尾窗口）；`full`（全链重算，实测 19615 条链耗时 **551.9–941.9 ms**）。
→ 因此默认**不做**全链重算，并把 `verify_scope` 与 `elapsed_ms` 一并回传。

**篡改检测用例**：`TestAuditExport::test_export_detects_tampering`
直接改库（`UPDATE audit_chain SET actor='attacker' WHERE seq=3`）后，
`verify.ok=false` 且 `first_bad_seq=3`（`payload_hash_mismatch`）——**如实报红**。

**用例**：`TestAuditExport`（3 例）、`panels.test.tsx::7`（2 例）。

---

### ✅ 9. 【S2-03 #12】ACR/UTC 面板消费真实聚合；降级拓扑、逃逸清单四类面板均非 mock

```
$ curl http://127.0.0.1:5757/api/cp/observability/stream
"acr": {…}                  ← agent/observability/acr.py::acr_snapshot
"utc": {…}                  ← agent/observability/utc.py::utc_snapshot
"model_degrade": {"total":{"value":2}, "edges":{…}, "chain":{…}}   ← model_degrade.degrade_summary
"escape": {"total":{"value":0}, "by_path":{}, "by_reason":{}, "ledger":"…governed_writes.jsonl",
           "watched":["data/scheduled_tasks.json"]}                 ← escape.escape_summary
```

四类聚合**直接调用既有函数**，本任务不重算、不 mock。面板落点：
`RoiPanel`（ACR/UTC 在 `roi` 端点）与 `AuditExportPanel`（降级拓扑/逃逸清单视图）。

**用例**：`TestObservabilityStream`（3 例，含真实事件构造成降级边与逃逸路径）。

---

### ✅ 10. 前端 tsc/eslint 零告警；vitest 新增用例全绿；既有技能中心零回归

```
$ cd yunshu-ui && npm run check     → tsc -b --noEmit                     （0 error）
$ cd yunshu-ui && npm run lint      → eslint .                             （0 error / 0 warning）
$ cd yunshu-ui && npx vitest run --poolOptions.threads.maxThreads=2
   Test Files  68 passed (68)
        Tests  569 passed (569)                （含本任务新增 45 例）
```

既有技能中心（`memory/skills-center`、`skill-assess-manager`、`workflow-visual` 等）
相关用例全部通过；`hubNav` 新增栏目未改动任何既有导航 key。

---

### ✅ 11. 无"不可追溯百分比"（五坑⑤红线自查通过）

**机器化自查**：`schema.untraceable_scan(payload)`——
未带 `source`（比率还须带 `formula`）的上屏数字，或**未被 `metric()` 出口包裹的裸比率**，
一律计入 `violations`；上游原样透传区段（ACR/UTC/safe_render 等）按
`OPAQUE_PREFIXES` 跳过（其口径由产出方负责）。

**用例**：`TestNoUntraceablePercentages::test_all_panels_pass_scan`
（对 pipeline / inbox / roi / incidents / memory / render 六个面板响应逐一扫描，
断言 `ok=True`）。**自查结论：通过，0 处违规。**

---

### ✅ 12. 【U1】TaintBadge / 审批区 Shadow DOM / 边界词确认 UI 按既有常量渲染

见 §二 #7 与移交项处置表 U1。**未自定义五类 / 60s / z-index**：
前端源码中 `2147483000` 仅出现在"从常量取值"的表达式处（`Number(raw['z-index'])`），
五类词表与 60s 均由端点注入。

---

### ✅ 13. 【U2】前端审批区统一挂载到工作台（Shadow DOM 保留）

见移交项处置表 U2。落点：`components.tsx::ApprovalZone`（React 组件内创建 Shadow Root）；
使用者：`ApprovalRow`（每行批准/驳回）、`AbsoluteActionBar`（七动作区）。
Flask 侧既有审批台保留（两者共用同一后端链路 `/api/approval/*`）。

---

### ✅ 14. 【U3】越权告警聚合面板消费 `actor_ip_hash` + 窗口阈值口径

见 §二 #9 与移交项处置表 U3。实测 `durable.total=314`、`by_source_key` 按
`actor_ip_hash` 分组、实时口径标 `volatile=true`（进程内计数重启归零）、
并逐字声明"原始 IP 不落盘（裁定 B）"。

---

### ✅ 15. 【U4】熔断/回滚按钮走 `rollback_bundle` 且 L5 强制审批（旁路不成立）

见移交项处置表 U4。实测：缺 `bundle_hash` ⇒ 400 且回传 `level=L5` /
`requires_approval=true`（读 `levels.LEVEL_SPECS` 事实）；带 `components` ⇒
400 `partial_rollback_rejected`；未知整包 ⇒ 409（不伪造成功）；
sub_agent 执行治理动作 ⇒ 403 `denied_by_matrix=true`。

---

### ✅ 16. 【U5】记忆→组装注入按 `recall_priority_key()` 接线（或登记后续）

见移交项处置表 U5。**已接线**的是"优先级契约的可解释性透出 + 面板呈现"；
注入执行本体**不重复实现**（归 S5-01 的 `LayeredMemoryStore.recall()` +
`ContextAssembler`），并**未声称**"组装注入已端到端接通"。

---

### ✅ 17. 【U6】状态灯 / 首屏性能按 §五 复测方案实测并标注 clock 口径

| 项 | 实测 | 口径 |
|---|---|---|
| 状态灯（同步：变更 → 强制 reflow） | p50 **0.5 ms** / p95 **0.7 ms**（n=30） | 浏览器 `performance.now()`；headless Chromium |
| 状态灯（异步：变更 → 下一帧） | p50 56.5 / p95 104.6 ms | 同上；含 vsync 量化（基线帧间隔 p50 66.7 ms） |
| 首屏 FCP / LCP | **500 ms / 500 ms** | `PerformanceObserver(paint, largest-contentful-paint)` |
| 首屏 DOMContentLoaded / load | 266.1 / 268.4 ms | `performance.getEntriesByType('navigation')` |
| jsdom 参考口径 | p50 17.1 / p95 33.9 ms | vitest + jsdom（不含真实绘制） |

**回填**：`docs/PERF_BUDGET_REBASED.md` §一（新增第 6 条）/ §二（#1、#2 由 ❓ 改实测）
/ §五（标记补测完成）/ **§八（新增：方法、原始数据、口径说明、复跑命令）**。
Watchdog 与熔断"阈值→生效"**仍标注未验证**（未编造）。

---

### ✅ 18. 【U7】周期性评估调度点接入（或登记部署编排）

见移交项处置表 U7：**只读观测面已接入**（`/api/cp/roi` 暴露
`over_budget`/`ratio`/`theta`/断食比值/shadow 开销审计/口径版本）；
**调度本体登记部署编排**（不新增第二事实源），归属 S5-03 / 部署侧，不阻塞。

---

### ✅ 19. 【U8】成本/策略数字标注口径（决策本体 p99 vs 全量埋点），不得混用

```
"policy_latency": {
  "decision_body": {"value":0.0474,"unit":"ms","note":"**不得**与全量埋点口径混用/相减/平均（U8）"},
  "full_instrumentation": {"hit_ms":3.764,"miss_ms":7.97,"note":"含链式审计+埋点的端到端耗时…"},
  "comparability": "two_scopes_not_interchangeable"
}
```

面板**并列**渲染两者，并显示"可比性：two_scopes_not_interchangeable
（两个口径**不可互换/相减/平均**）"。成本侧另有 `cost_schema_version`
（`utc.v1` 与 `s5-03.v1` 两值并存）与 `calibration_version` 上屏。

---

### ✅ 20. 【U9】`app_server.py::require_token` 历史副本清理（或登记专项）

见移交项处置表 U9：**已登记专项**（删除会改变既有 293 条写路由的鉴权语义，
属既有公开行为变更，与本任务"守不易"冲突）；本任务全部 16 条新路由
**一律使用 `agent.server_auth.require_token`**（新实现），**不引入新副本**。

---

### ✅ 21. 【U10】委派回收率接线（行契约 + 计算函数已交付）

见移交项处置表 U10。实测（注入 2 行、1 行三件套齐全）：

```json
{"key":"delegation_recovery","name":"委派回收率","value":0.5,
 "numerator":1,"denominator":2,"samples":2,"status":"insufficient_samples",
 "formula":"count(三件套齐全) / count(委派)",
 "source":"委派行契约（见 delegation_recovery_contract()）：产物/轨迹/反思三源齐全性",
 "contract":{"required":["delegation_id","capability_id"],
             "triple_fields":["artifact","trace","reflection"]},
 "incomplete":["d2"]}
```

无真实委派数据源时该行落 `framework_only`/`insufficient_samples` 且 `value=None`（不填 0）。

---

### ✅ 22. 【§0.3】样本 <20 仅披露、缺数据记 None（不以 0 冒充）；每个数字带数据源与公式

- 样本纪律：`metric(sample_size=n)` 自动补 `insufficient_sample` / `disclosure_only` /
  `note`；前端 `MetricValue` 渲染"仅披露"角标。**实测通过**（如灰度卡 `pass_rate` 57 样本 ⇒ 可考核；
  能力地图 4 样本 ⇒ 仅披露）。
- 缺位记 None：`absent()` + `value=None`；前端渲染"—"（`data-cp-metric-value="absent"` 可断言）。
  **实现期修复 1 处真实违规**（见 §二 #2）。
- 来源与公式：唯一上屏出口 `metric()`；`untraceable_scan` 机器化自查（§二 #11）。

---

## 三、质量证据

### 3.1 新增与回归测试

| 套件 | 结果 |
|---|---|
| `tests/unit/test_s6_01_ui_panels.py`（新增） | **76 passed / 0 failed** |
| 新增模块覆盖率 | `schema.py` **96%** / `data.py` **83%** / `routes_ui_panels.py` **82%** / 加权 **83%**（命令：`pytest tests/unit/test_s6_01_ui_panels.py tests/unit/test_approval_routes.py --cov=agent.ui_panels --cov=agent.server_routes.routes_ui_panels`）⇒ **≥80% 达标** |
| 邻接回归（14 套件：approval_routes / security_approval_session / security_approval_guard / s4_01_stage_promote_chain / audit_chain / audit_facade / guardrails_safe_render / guardrails_boundary_words / injection_defense_facade / digestion_{internalize,shadow,stage,pipeline}） | **833 passed / 0 failed** |
| `yunshu-ui` 全量 vitest | **569 passed / 0 failed**（68 文件；新增 45 例） |
| 后端全量 `tests/unit` | 16414 passed / 12 failed ⇒ **0 回归**（见下） |

**后端 12 例失败的逐条定性**（均**非**本任务引入）：

| 套件 | 例数 | 定性 | 基线对照 |
|---|---|---|---|
| `test_preflight_runner.py` | 3 | 子进程 stdout 捕获在本沙箱环境不可用（`proc.stdout is None`） | **master 同现 3/3** |
| `test_mcp_executor.py::TestVerboseCliFlag` | 6 | 同上（子进程输出捕获） | **master 同现 6/6** |
| `test_ci_l3_context_preflight.py` | 1 | 同上 | **master 同现 1/1** |
| `test_digestion_gate.py::test_reprobe_probe_failure_drifts` | 1 | 全量并发下 Windows `os.replace` 被占用（PermissionError） | 单独跑**通过**（master 与 s601 均通过） |
| `test_digestion_cases.py::TestCaseStore::test_history_is_bounded` | 1 | 同上 | 单独跑**通过**（master 与 s601 均通过） |

> 说明：本沙箱禁止进程通过管道捕获子进程输出，因此"验证子进程 CLI 行为"的用例
> 在此环境必然失败；它们在 master 上逐例同现，属**环境限制**而非代码缺陷。

### 3.2 门禁

| 门禁 | 命令 | 结果 |
|---|---|---|
| 前端类型 | `npm run check` | 0 error |
| 前端 lint | `npm run lint` | 0 error / **0 warning** |
| 前端测试 | `npx vitest run` | 569 passed |
| 前端构建 + Flask 同步 | `npm run build:flask` | ✅ 产物已同步（模板与 `dist/index.html` 逐字节一致；引用的 6 个资源全部存在） |
| kwarg 扫描 | `python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH` | **0 处** |
| kwarg 扫描 | `python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH` | **0 处** |
| 依赖分层 | `python -c "from importlinter.cli import lint_imports; …" --config .importlinter` | **2 kept / 0 broken** |
| 类型（改动模块） | `python -m mypy agent/ui_panels agent/server_routes/routes_ui_panels.py agent/server_routes/routes_approval.py --follow-imports=silent` | **0 error**（实现期修复 1 处 `arg-type`） |
| 架构护栏 | `python -m agent.observability.arch_rules --check` | 4 违规**全部已豁免**，**0 未豁免** |

### 3.3 真实浏览器 E2E

`python scripts/dev/cp_panel_snapshots.py` 对七个栏目逐一导航、等待真实数据渲染、
截图并抽取**真实渲染文本**作为证据：

| 面板 | 证据文本 | 截图 |
|---|---|---|
| 消化流水线 | 治理面板 / 消化流水线 / 轨迹采集 / 灰度 | `reports/s6_01/shots/pipeline.png` |
| 审批收件箱 | 审批收件箱 / 一键批 / 不出气泡 | `approvals.png` |
| 能力地图 | 能力地图 / `cp.builtin.read_file` / 数据源 | `capabilities.png` |
| 成本 ROI | 成本 ROI / 决策本体 / 策略延迟 | `roi.png` |
| 自愈事故 | 自愈事故 / MTTD / 备份 | `incidents.png` |
| 记忆技能库 | 记忆 / 召回优先级 / strategy | `memory.png` |
| 审计导出 | 审计导出 / 验签 | `audit.png` |

`evidence_missing` 全为空（7/7）。

---

## 四、口径与诚实声明（**不得省略**）

1. **状态灯预算存在两个口径**：同步（0.7 ms，✅）与异步（104.6 ms，受 vsync 量化）。
   两者都在文档与报告中列出，**不得只引用有利的一个**。
2. **本任务的"性能实测"是单机 headless Chromium 结果**，非生产环境压测；
   首屏为"热缓存 + 本机最小验证据点"，冷启动 `goto` 实测 2.93 s（含模块编译）。
3. **消化流水线的泳道只有 `trace_collect` 列有真实事件**（29 条 `digest.stage`，
   scope 多为 `first_entry_backfill`）；其余四列当前为 0 —— 这是**真实数据现状**
   （灰度/内化需显式开关与阈值），面板如实显示 0 而不填充演示数据。
4. **审计链存在历史断点**：运行时台账在 `seq=17` 有 `prev_hash_mismatch`
   （主工作区同一现象，非本任务引入）。审计导出与面板**如实报红**；
   台账修复归审计域专项。
5. **未声称**：真实能力内化（样本量不足）、组装注入端到端接通、
   委派回收率有真实数据源、Watchdog / 熔断"阈值→生效"性能达标。
6. **本任务新增/修复的 3 处真实缺陷**：
   - 能力地图把"零样本"渲染为 0%（已修 + 回归用例）；
   - `metric()` 的调用方注记覆盖了披露纪律文案（已修 + 回归用例）；
   - 审批路由在生产入口**从未注册**（N1，已修）。

---

## 五、验收后收尾（2026-09-12，Owner 指示"逐项收尾"）

验收（§二 20/20）之后，按 Owner 指示对**全项目 S0–S6** 做了一次收尾核查，
发现并处置了 2 项——一项是**跨阶段 CI 阻塞**，一项是本任务自身的覆盖率缺口：

| # | 事项 | 定性 | 处置 | 提交 |
|---|---|---|---|---|
| C1 | CI 的「文档链接预检与锚点回归测试」与「代码质量检查 → docs 链接预检诊断」两个 job 在 master 上**持续失败** | **S4-03 结案时遗留**（`2eef5349` 基线即存在，非 S6-01 引入） | `docs/chaos_s4_03_drill_report.md` 实际位于 `docs/` 根，而 `docs/zh/CloudPivot_v7.2重构计划/` 下三处引用写成 `../`（只退一级 → 解析到 `docs/zh/`）⇒ 改为 `../../`。本地复跑同一 CI 命令：**检查 1289 文件 / 1469 链接 / 0 失效**，pre-commit 预检 **2 通过 0 失败** | `f2b25c54` |
| C2 | `agent/ui_panels/data.py` 覆盖率 **78%**，低于批次约定「单测覆盖率 ≥80%」 | 本任务自身缺口 | 补 10 例：① 用**真实 `MemoryEntry`** 走 `_memory_card()`（枚举 `.value` 映射 / 脱敏文本不外泄 / TTL 数值条件分支 / layers 过滤）；② 新增 `TestGracefulDegradation`（守不易：逐个面板把数据源打成不可用，断言不抛异常、受影响区段记 `absent`、其余区段仍可用）⇒ `data.py` **83%**、`schema.py` **96%**、`routes_ui_panels.py` **82%**、加权 **83%** | `4b5bf487` |

**处置后终态**：`master` = `origin/master` = `gitee/master`（同点）；CI 在 `4b5bf487` 上重跑，
上述两个曾失败的 job 预期转绿（本报告 §六 记录终态）。
