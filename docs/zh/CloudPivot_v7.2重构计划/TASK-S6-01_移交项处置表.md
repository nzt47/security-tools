# TASK-S6-01 移交项处置表（U1–U10）

> 任务：TASK-S6-01 六面板扩展｜基线 `master` / `5984e03e`｜worktree `s601`
> 生成日期：2026-09-12
> 说明：本表是 `TASK-S6-01_六面板扩展.md` §0.2 的**逐条收口证据**。
> 每条给出：处置结论（收口 / 登记后续）、落点（文件 : 函数）、证据（命令 / 用例 / 端点）。

---

## 总览

| # | 移交项 | 结论 | 落点 | 证据 |
|---|---|---|---|---|
| U1 | 机制 6 前端组件常量 | ✅ **收口** | `GET /api/cp/security/render-state`；`components.tsx::TaintBadge/ApprovalZone` | 端点实测 + `components.test.tsx`（U1 组 6 例） |
| U2 | 前端审批区统一挂载 | ✅ **收口** | `components.tsx::ApprovalZone`（Shadow DOM 自治单元）；`index.tsx::ApprovalRow` | `panels.test.tsx::八`（影子树断言，3 例） |
| U3 | 越权告警聚合面板 | ✅ **收口** | `GET /api/cp/security/authz-alerts`；`AuditExportPanel` 内嵌视图 | 端点实测（`policy.denied` 314 条 / `by_source_key`）+ `test_s6_01_ui_panels.py::TestSecurityAndAuthz` |
| U4 | 熔断/回滚按钮接线（L5 强制审批） | ✅ **收口**（且比移交要求更严） | `routes_ui_panels.py::_do_rollback/_do_action` | `TestSevenActions`（7 例，含子集拒绝） |
| U5 | 记忆→组装注入（策略 > 事实 > 偏好） | ✅ **收口（呈现 + 契约）**；注入执行本体按 S5-01 既有实现 | `data.py::memory_skills_view::recall_priority`；`MemorySkillsPanel` | 端点实测（`order=['strategy','fact','preference','working']`）+ 面板用例 |
| U6 | 状态灯 / 首屏性能实测 | ✅ **收口（实测并回填）** | `scripts/dev/cp_perf_probe.py`、`perf.test.tsx`；`docs/PERF_BUDGET_REBASED.md` §二/§五/§八 | 实测 JSON（`reports/s6_01/*.json`）+ 文档回填 |
| U7 | 周期性评估调度点（`evaluate_brakes()`） | ✅ **接线（只读观测）+ 部署编排登记** | `GET /api/cp/roi` 暴露刹车状态（`over_budget`/`ratio`/`theta`） | 端点实测；调度编排见"登记"节 |
| U8 | 策略/成本数字口径 | ✅ **收口（双口径并列 + 不可混用断言）** | `data.py::roi_view::policy_latency`；`RoiPanel` | 面板用例（U8 断言）+ UI 文案 |
| U9 | `app_server.py::require_token` 历史副本 | ✅ **收口（登记专项 + 本任务不动其语义）** | 见"登记"节 | 本任务新增路由**全部**走 `agent/server_auth.require_token` |
| U10 | 委派回收率接线 | ✅ **收口** | `data.py::roi_view::slo_metrics`（`compute_metrics(delegations=...)`） | `test_s6_01_ui_panels.py::test_slo_metrics_include_u10_delegation_recovery` |

**结论：U1–U10 全部收口或明确登记；无一项悬空。**

---

## U1 机制 6 前端组件：TaintBadge / 审批区 Shadow DOM / 边界词确认 UI

**移交要求**：前端按其常量渲染，**勿自定义**五类边界词 / 60s 上限 /
`accepts_text_approval=False` / TaintBadge 三个 class 名 / 审批区 `z-index` 固定值。

**处置（收口）**

1. **常量单一来源端点**（新增，本任务）：
   `GET /api/cp/security/render-state` →
   `safe_render.safe_render_state()` + `boundary_words.boundary_state()` +
   `injection_defense.defense_status()`（一次给全，避免前端多处拼装）。
2. **前端消费方式**：`usePanel.ts::useSecurityState()`（进程内单例缓存）；
   `components.tsx::TaintBadge` **拿不到常量就返回 `null`**（宁可没有徽章也不自定义）；
   `ApprovalZone` 的 `z-index / isolation / contain` 全部取 `approval_zone.style`。
3. **未自定义的证据**（前端源码不出现字面量）：
   - `2147483000` 只出现在后端 `safe_render.APPROVAL_ZONE_STYLE` 与从常量取值处；
   - 五类边界词只由 `boundary_words.NEVER_AUTOMATED` 提供（前端只做类别键
     `_`→`-` 的 URL 形式转换，见 `routes_ui_panels.NEVER_AUTOMATED_ACTIONS` 注释）；
   - 60s 由后端 `MAX_CONFIRMATION_TTL_SECONDS` 决定，签发端点**不接受 ttl 参数**。

**实测证据**

```
GET /api/cp/security/render-state →
  boundary_words.never_automated  = ["transfer","publish","drop_database","permission_change","force_push"]  （5 类）
  boundary_words.max_ttl_seconds  = 60.0
  boundary_words.accepts_text_approval = false
  boundary_words.single_action_bound   = true
  safe_render.taint_badge         = {class:"cp-taint-badge", base_class:"cp-taint-badge--has-bg", attr:"data-cp-taint-source"}
  safe_render.approval_zone.style = {"position":"fixed","z-index":"2147483000","isolation":"isolate","pointer-events":"auto"}
```

**用例**：`components.test.tsx`（TaintBadge 2 例 / ApprovalZone 2 例）、
`panels.test.tsx`（类别词表 1 例 / TTL 不可覆盖 1 例）、
`test_s6_01_ui_panels.py::TestSecurityAndAuthz::test_render_state_exposes_u1_constants`。

---

## U2 前端审批区挂载统一（Flask 侧 → 工作台）

**移交要求**：统一挂载到工作台（**Shadow DOM 保留**，可挂任意宿主）。

**处置（收口）**

- 新增 `components.tsx::ApprovalZone`：React 组件内 `host.attachShadow({mode:'open'})`，
  样式**内联注入影子树**（不依赖 Tailwind，因为影子树拿不到宿主样式表），
  React 子树经 `createPortal` 渲染进影子树内挂载点。
- 落点：
  - `index.tsx::ApprovalRow` —— 审批收件箱每行的批准/驳回按钮；
  - `index.tsx::ApprovalInboxPanel` / `AbsoluteActionBar` —— 七动作按钮区。
- **Flask 侧既有审批台保留不动**（`templates/approval_console.html` +
  `static/js/approval_console.js`，含 `attachShadow` 与 `z-index: 2147483000`），
  两者共用同一后端审批链路（`/api/approval/*`），互不替代。

**实测证据（E2E）**

`panels.test.tsx::八` 断言：
- `document.querySelector('[data-cp-approval-zone="true"]').shadowRoot` 存在；
- `data-cp-z-index="2147483000"`、`data-cp-shadow-root="true"`；
- **light DOM 查不到"熔断"按钮、影子树内查得到** —— 这就是 DOM 隔离生效的证据。

浏览器截图：`reports/s6_01/shots/approvals.png`（含审批区与 TaintBadge）。

---

## U3 越权告警聚合面板

**移交要求**：口径已具备（`actor_ip_hash` + 窗口阈值），**面板消费未建** → 本任务补。

**处置（收口）**

- 端点 `GET /api/cp/security/authz-alerts`，**两个口径并列披露**（不得混用）：

| 口径 | 来源 | 特性 |
|---|---|---|
| 实时 | `security/alerts.py::get_denial_stats()` | **进程内计数**，重启归零 ⇒ 标 `volatile=true` |
| 耐久 | `policy.denied` 事件流（`iter_events`） | 跨重启可追溯；按 `actor_ip_hash` 分组；**原始 IP 不落盘**（裁定 B） |

- 阈值/窗口口径随附：`CP_SECURITY_ALERT_THRESHOLD`、`CP_SECURITY_ALERT_WINDOW_SECONDS`。
- 面板落点：`AuditExportPanel` → "越权告警聚合（U3）"折叠区。

**实测证据**

```
GET /api/cp/security/authz-alerts?days=30 →
  realtime.volatile = true
  durable.total.value = 314
  durable.by_source_key = { <hmac-hash>: n, ... }      （source_key = actor_ip_hash 或掩码）
  durable.pii_note = "source_key = actor_ip_hash（HMAC-SHA256，密钥入 SecretStore）；原始 IP 不落盘（裁定 B）"
```

**用例**：`test_s6_01_ui_panels.py::TestSecurityAndAuthz::test_authz_alerts_*`（3 例，含真实事件聚合）。

---

## U4 熔断 / 回滚按钮接线（**不得旁路**）

**移交要求**：整包回滚入口 `release_bundle.rollback_bundle(bundle_hash, applier=...)`；
**L5 级 `requires_approval=True`、组件子集一律拒绝** → UI 按钮必须走审批。

**处置（收口，且比移交要求更严）**

`routes_ui_panels.py::_do_rollback`：

| 要求 | 实现 |
|---|---|
| 只接受整包 | 只读 `bundle_hash`；请求体带 `components` ⇒ **400 `partial_rollback_rejected`**（前端层先拒，后端原子性闸门仍在） |
| L5 强制审批 | 回传 `level="L5"` + `requires_approval=<levels.LEVEL_SPECS[L5].requires_approval>`（**读事实，不硬编码**）；实测恒 `True` |
| 不旁路 | 调用 `rollback_bundle(bundle_hash, components=None, dry_run=True, ...)`；`applier` **不由本端点提供**（本模块没有执行能力，落地由部署侧注入） |
| 子集由下游再拒 | `rollback_bundle` 自身的 `check_atomic_request()` 仍是最后一道闸门（`PartialRollbackError` + L4 事故卡） |

**另加**：熔断/降级/摘除/熔炉开关等写动作统一走
`POST /api/cp/actions/<action>`，**先矩阵鉴权**（`§7.0` 单表）后执行；
未登记动作 fail-closed（400）。

**实测证据**

| 请求 | 结果 |
|---|---|
| `POST /api/cp/actions/rollback {}` | 403（`requires_reason=true`，矩阵前置条件） |
| `POST /api/cp/actions/rollback {"target":"","reason":"…"}` | 400 `missing_bundle_hash`，`level=L5`，`requires_approval=true` |
| `POST /api/cp/actions/rollback {"target":"h…","reason":"…","components":["code"]}` | 400 `partial_rollback_rejected` |
| `POST /api/cp/actions/rollback {"target":"0"*32,"reason":"…"}` | 409 `rollback_rejected`（台账无此整包，**不伪造成功**） |
| `POST /api/cp/actions/not-a-real-action` | 400 `unknown_action`（fail-closed） |
| sub_agent 执行 `switch_forge` | 403 + `denied_by_matrix=true` |

**用例**：`test_s6_01_ui_panels.py::TestSevenActions`（10 例）。

---

## U5 记忆 → 组装注入（策略 > 事实 > 偏好）

**移交要求**：按 `taxonomy.recall_priority_key()` 接线，或明确登记后续。

**处置（收口：呈现 + 契约透出；注入执行本体不重复实现）**

- **不重复实现注入**：组装注入的实际执行点在 S5-01 已交付的
  `LayeredMemoryStore.recall()`（内部即按 `taxonomy.recall_priority_key()` 排序）
  与既有 `ContextAssembler`。本任务**不新写第二套排序**（那会造成口径漂移）。
- **本任务接线的是"可解释性与契约"**：
  - 端点 `GET /api/cp/memory/skills` 的 `recall_priority` 块给出
    **顺序 / 来源 / 公式 / 契约文案**，全部取自后端 `taxonomy.LAYER_ORDER`
    与 `recall_priority_key`（`callable=True` 为真实性断言）；
  - 面板 `MemorySkillsPanel` 把该顺序作为**优先级契约**上屏，并显示
    "组装注入（P7.2-10）按本顺序；本面板只呈现，不改变召回结果"。
- **如实声明边界**：本任务**未声称**"组装注入已端到端接通"——
  该项归 S5-01（其结案报告已明示"未声称 P7.2-10 组装注入已接通"）。

**实测证据**

```
GET /api/cp/memory/skills →
  recall_priority.order    = ["strategy","fact","preference","working"]
  recall_priority.source   = "agent/memory/taxonomy.py::recall_priority_key()"
  recall_priority.formula  = "策略 > 事实 > 偏好（P7.2-10）；同层 project 事实 > global 偏好，同级新者胜（§4.3）"
  recall_priority.callable = true
```

**用例**：`test_s6_01_ui_panels.py::TestMemorySkillsPanel`（2 例）、
`panels.test.tsx::6`（2 例）。

---

## U6 状态灯 / 首屏性能实测（**并回填**）

**移交要求**：按 `docs/PERF_BUDGET_REBASED.md` §五 复测方案实测并**标注 clock 口径**。

**处置（收口：真实浏览器实测 + 文档回填）**

| 项 | 实测（headless Chromium，n=30） | 预算 | 判定 |
|---|---|---|---|
| 状态灯（**同步**口径：状态变更 → 强制 reflow） | p50 **0.5 ms** / p95 **0.7 ms** | <50 ms | ✅ 余量 ≈71× |
| 状态灯（**异步**口径：状态变更 → 下一帧） | p50 56.5 ms / p95 104.6 ms | <50 ms | ⚠️ 该口径受 vsync 量化（基线帧间隔 p50 66.7 ms），**下限即 >50 ms**，如实披露 |
| 首屏 FCP | **500 ms** | 验收线 <1.5 s | ✅ |
| 首屏 LCP | **500 ms** | 验收线 ≤2 s | ✅ |
| 首屏 DOMContentLoaded / load | 266.1 / 268.4 ms | — | — |

时钟口径：`browser performance.now()` + `PerformanceObserver(paint, LCP)`；
环境：headless Chromium（playwright，本机）；落盘
`reports/s6_01/status_badge_workbench_first_screen_and_status_light.json`。

**文档回填**：`docs/PERF_BUDGET_REBASED.md`
- §一 结论先行 新增第 6 条（实测结论）；
- §二 表格 #1/#2 由 `❓` 改为实测值；
- §五 两项标记"已补测完成"（Watchdog / 熔断阈值生效**仍为未验证**，未编造）；
- **新增 §八**：完整复测方法、两个口径的原始数据、口径说明、复跑命令。

**用例**：`perf.test.tsx`（jsdom 口径，2 例，结果经 `cp_perf_sink` 落盘）、
`scripts/dev/cp_perf_probe.py`（真实浏览器口径）。

---

## U7 周期性评估调度点（`evaluate_brakes()`）

**移交要求**：建议随本任务或部署编排接入；`allow_outbound()` 已有惰性兜底。

**处置（接线只读观测 + 明确登记部署编排）**

- **本任务提供的接线**：`GET /api/cp/roi` 暴露刹车完整状态
  （`over_budget` / `ratio` / `baseline_cents_per_day` / `theta` /
  `fasting_in_ratio` / `fasting_out_ratio` / `shadow` 开销审计 / 口径版本），
  使 `evaluate_brakes()` 的判定结果**可被人看到**（此前只有 `allow_outbound()`
  的惰性兜底，没有统一观测面）。
- **登记（部署编排）**：周期性调用 `agent.monitoring.cost_brake.evaluate_brakes()`
  的**调度落点归部署侧**（S5-03 已交付该函数与 `register_*` 形态的既有调度约定）。
  本任务**不新增调度器**——理由：调度周期与阈值属部署决策（`config.yaml` /
  `CP_BUDGET_*`），在业务任务里硬编码一个周期会造成第二事实源。
- **归属与阻塞性**：归 S5-03 / 部署编排；**不阻塞**本任务验收。

---

## U8 策略/成本数字口径（**不得混用**）

**移交要求**：面板展示须注明口径（决策本体 p99 0.047ms vs 全量埋点 3.76/7.97ms），
不得混用。

**处置（收口：双口径并列 + 机器可读的不可混用标记）**

- `data.py::roi_view` 输出 `policy_latency`：

| 字段 | 值 | 口径 |
|---|---|---|
| `decision_body` | 0.0474 ms | 决策本体 p99（缓存命中路径，**不含埋点开销**） |
| `full_instrumentation.hit_ms` / `miss_ms` | 3.764 / 7.97 ms | 全量埋点下三档实测（含链式审计+埋点） |
| `comparability` | `"two_scopes_not_interchangeable"` | **机器可读**的"不可互换"标记 |

- 面板 `RoiPanel` 把两者**并列**渲染，并显示红色提示
  "两个口径**不可互换/相减/平均**"；`decision_body.note` 亦逐字写明。
- 成本侧同样标注：`cost_schema_version`（`utc.v1` 与 `s5-03.v1` 两个字符串
  **并存且各自如实呈现**）、`calibration_version`、`source_of_truth="events"`。

**用例**：`test_s6_01_ui_panels.py::TestRoiPanel::test_calibration_and_two_scope_latency`、
`panels.test.tsx::4`（U8 断言 1 例）。

---

## U9 `app_server.py::require_token` 历史副本清理

**现状（S4-01 结案时登记）**：`app_server.py:298` 仍是历史副本
（不走 `server_auth` 新实现，故其自身路由不识别每使用者令牌），
与升级前行为逐字一致；**不阻塞**。

**处置（收口：登记专项 + 本任务零耦合）**

- **本任务全部新增路由**（`/api/cp/*`，16 条）一律使用
  `agent.server_auth.require_token`（新实现，支持 `CP_UI_TOKENS` 映射表），
  **不引用** `app_server.py` 的副本；
- **未删除副本**：删除会改变 `app_server.py` 中既有一批路由的鉴权语义
  （从"共享令牌"变为"共享令牌 + 映射表"），属**既有公开行为变更**，
  与本任务"守不易"纪律冲突，也与 S4-01 的处置结论一致；
- **登记专项**：`app_server.py::require_token` 副本的收敛（改为复用
  `server_auth.require_token`）作为**独立清理项**，归属"技术债清理"专项，
  建议与 `CP_UI_TOKENS` 全量启用一并做（届时可一次性验证 293 条写路由的
  身份归因变化）。
- **阻塞性**：不阻塞本任务；本任务已通过"新路由不引入新副本"阻止债务扩大。

---

## U10 委派回收率接线

**移交要求**：行契约 + 计算函数已交付 → 接线即可计算（与 S4-04 产物对接）。

**处置（收口）**

- `data.py::roi_view` 调用
  `agent.eval.metrics.compute_metrics(..., delegations=<行序列>)`，
  行契约即 S5-02 交付的
  `delegation_recovery_contract()`：`required=["delegation_id","capability_id"]`、
  `triple_fields=["artifact","trace","reflection"]`。
- 面板 `SloMetricsTable` 呈现该行（含 `numerator/denominator/samples/status/source/formula`），
  样本 <20 自动落到 `insufficient_samples`（只披露不考核）。
- **数据源现状如实呈现**：无真实委派数据源时行契约进入 `/api/cp/roi`（`delegations=None`），
  面板显示 `—` 与 `framework_only`/`insufficient_samples`，**不填 0**。
- 与 S4-04 的对接点：S4-04 交付的委派记录（回收三件套）可作为
  `delegations` 入参注入（`roi_view(delegations=...)` 已开放该参数）。

**用例**：`test_s6_01_ui_panels.py::TestRoiPanel::test_slo_metrics_include_u10_delegation_recovery`
（断言 `value==0.5`、`numerator/denominator`、`contract`、`incomplete`）、
`panels.test.tsx::4`（面板出现"委派回收率"行）。

---

## 附：本任务**新增发现**的两项（非 §0.2 移交，但如实记录）

| # | 发现 | 影响 | 处置 |
|---|---|---|---|
| N1 | `agent/server_routes/__init__.py::register_all_routes` **无调用方**（S2-02 盘点已记为死代码），而 `routes_approval` 只在其中登记 ⇒ **审批 HTTP 面此前从未真正注册**（`/api/approval/*` 在运行中的 app_server 里不可达） | 高：S4-01 交付的审批面安全（会话绑定/CSRF/链接≤900s/二次认证）在生产入口不可达；审批收件箱也会失去其唯一写链路 | **已修复**：`app_server.py` 新增显式注册段（审批面 + 本任务面板路由），并在注释中写明根因。本任务**不改** `routes_approval.py` 的对外行为 |
| N2 | 运行时审计链在 **seq=17 处已存在 `prev_hash_mismatch`**（仓库主工作区 `data/audit/audit_chain.db` 同一现象，非本任务引入） | 中：说明该台账历史上被非链式写入过；不影响本任务，但"审计导出含验签摘要"会**如实报红** | **如实呈现，不掩盖**：面板与导出文件把 `verify.ok=false` / `first_bad_seq=17` 原样上屏（这正是五坑⑤"别让看板说谎"的正向用例）；台账修复归审计域专项 |
