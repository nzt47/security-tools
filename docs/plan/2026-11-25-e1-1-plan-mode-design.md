# E1-1 计划模式（Plan Mode）设计 — 2026-11-25

> 状态：设计定稿，待实现。范围：Phase 1 第一项（DSH `plan-mode` 对齐）。
> 对齐目标是**能力语义与验收**，不照搬 Cordis/TS 实现。

## 1. 目标与验收（来自路线图，逐条可测）

1. 会话级 `plan_mode` 开关；进入后模型只做只读调研并产出计划。
2. 计划以 Markdown 落 `data/plans/<session>.md`，**版本化**、可回滚。
3. 计划卡 UI：**批准 / 反馈 / 拒绝 / 回滚**；拒绝或反馈后继续规划。
4. **会话恢复后状态保留**（刷新页面、重启后端都成立）。
5. 与权限预设/审批的关系：计划模式是"工作方式"，不是权限档位；审批边界照旧。

## 2. 关键取舍（先定，避免实现期摇摆）

| 问题 | 决策 | 理由 |
|---|---|---|
| 状态落哪 | `data/plans/<session>.json`（元数据）+ `<session>.md`（最新计划）+ `<session>.vN.md`（历史版本） | 会话状态已有两层（`data/sessions/<id>/meta.json` + sessions.json）。plan_mode 是**跨入口可读的工作方式状态**，且要带版本历史；独立小存储比改 `meta.json` 的写入路径更内聚，且不把 SessionManager 变成业务状态宿主。Markdown 单独成文件满足"计划可读、可 diff、可回滚" |
| 工具还是命令 | **命令**（`/plan`、`/plan exit|approve|reject`）+ HTTP API（计划卡按钮）。**不新增模型工具** | 路线图允许"工具或命令"。新增工具要付 5 处连锁（tool_index/BM25 S0/路由基线/能力清单/路由冲突）并带来"模型不调用工具"的失败模式；命令是确定性的、可单测的。DSH 的 `exit_plan_mode` 工具语义由"计划模式下助手回复即计划候选"承接 |
| 计划怎么提交 | **计划模式下的每次助手回复 = 一个计划版本**（自动入卡，`status=pending_approval`）。兜底：`/plan submit` 取当前会话最后一条助手消息 | 零协议解析（不依赖模型吐特定标记）、不会"模型忘了调用工具"；版本号让噪音可控（卡片只展示最新版，历史在回滚列表） |
| 批准后怎么继续 | 计划卡按钮 → `POST /api/plan/decision`（服务端改状态）→ 前端把返回的 `next_message` 作为普通用户消息发送 | 复用现有对话链路（记忆、审计、落盘一份口径），不造"服务端自己发起 LLM 轮"的第二条链路 |
| 与权限预设/审批 | 不绑定。计划模式只追加"只读调研、先出计划"的系统提示；工具闸门/审批/沙箱完全按现有权限档位 | 与 DSH 一致（计划模式仍可用全部工具，靠权限预设约束）；避免两套授权语义 |
| 回滚 | `action=rollback&version=N`：把 `vN.md` 提升为新版本（version+1），状态回 `pending_approval`、`plan_mode=true` | 不删除历史；回滚本身也是"一次新计划"，UI 与审计都能看到 |

## 3. 状态机

```
idle ──/plan 或 计划模式按钮──> planning
planning ──助手回复落盘──> pending_approval（version+1）
pending_approval ──approve──> approved（plan_mode=false）
pending_approval ──reject/feedback──> planning（plan_mode 仍为 true；记录意见）
approved ──rollback(vN)──> pending_approval（plan_mode=true）
任意态 ──/plan exit──> idle（plan_mode=false，保留计划文件与历史）
```

`status ∈ {idle, planning, pending_approval, approved, rejected}`；`rejected` 是瞬时态，
记录该版本被拒后立即回到 `planning`（会话继续规划时卡片仍可见）。

## 4. 后端改动

- `agent/context/plan_mode.py`（新）：纯文件存储 + 原子写 + 进程内锁。
  - `get_state(session_id)` / `enter` / `exit` / `record_turn_plan(session_id, text)` /
    `decide(session_id, action, feedback=None, version=None)` / `delete_plan(session_id)`。
  - 会话键安全化：`[A-Za-z0-9_-]{1,64}` 直用，否则 `sha1[:16]`（防路径穿越；与
    `create_session` 的 ID 校验同向）。
  - 不用 env 读取点（测试用 `set_plans_dir()` 重定向，仿 `plan_tools._TODOS_PATH_OVERRIDE`）。
- `agent/server_routes/routes_plan.py`（新，3 条路由）：
  - `GET  /api/plan?session_id=` → 状态 + 最新计划 + 版本列表（只读，不挂令牌）。
  - `POST /api/plan/mode` `{session_id, enabled}` → 进入/退出（`@require_token`）。
  - `POST /api/plan/decision` `{session_id, action, feedback?, version?}` → 决策 + `next_message`。
- `plugins/chat.py`（主 UI 链路 `/api/chat/stream`）：
  1. 解析 `/plan*` 命令族：`/plan`（进入）、`/plan exit`、`/plan status`、`/plan submit`
     （把上一条助手回复提交为计划）、`/plan rollback <版本号>`、`/plan approve`、
     `/plan reject [意见]`、`/plan feedback <意见>`；纯状态命令不进 LLM；
  2. `_workbench_real_stream`：计划模式激活时向 SYSTEM_PROMPT 追加只读/出计划指令；
  3. 助手回复收尾时（`done` 之前）：`record_turn_plan()`，**先发 `plan` 事件再发 `done`**
     （前端收到 done 即 break，顺序错了卡片不出现）。
- `agent/session_manager.delete_session`：顺带清理该会话计划（失败不阻断删除）。
- 会话恢复：状态全在磁盘，`GET /api/plan` 即读即得；不需要内存态。

## 5. 前端改动（第一次动 UI，按封装规范）

- `src/api/endpoints.ts`：`PLAN` / `PLAN_MODE` / `PLAN_DECISION` 三个**字面量**。
- `src/lib/sse.ts`：`StreamEvent` 增 `{type:'plan', plan: PlanState}`。
- `src/lib/planApi.ts`：`getPlan(sessionId)` / `setPlanMode` / `decide`（走 `apiClient.request`）。
- `src/stores/useLayoutStore.ts`：新增 `plan: PlanState | null` + `setPlan`；
  SSE `plan` 分支写入（仿 `approval` 分支）。
- `src/components/workbench/chat/PlanCard.tsx`（新，弹层/卡片放 `components/`）：
  - 标题用 `SectionTitle`/`text-ui` 字号写标签 className；按钮用 `Button`；
  - 批准 → `decide('approve')` 后 `sendMessage(next_message)`；
  - 反馈 → 内联 textarea → `decide('feedback', text)` 后发送；
  - 拒绝 → `decide('reject')` 后发送（保留 plan_mode，继续规划）；
  - 版本列表（最多 5 条）带「回滚」；退出计划模式按钮。
- `src/components/workbench/panels/ChatPanel.tsx`：
  - 工具栏加「计划模式」开关（紧邻 `PermissionModeMenu`，同一语义层：一个管工作方式）；
  - 消息区与输入区之间挂 `PlanCard`；`activeSessionId` 变化时 `getPlan()` 回填（会话恢复）。
- **深链**：计划卡只嵌会话页、不新增页面/叶子 ⇒ 不登记深链（`导航重组方案.md`：
  叶子 key 全不动）。`?panel=session` 不变。
- 守卫：新增 `<h*>` 必带 `text-*`；满宽按钮带 `justify-start|center`；不动基线。

## 6. 验证与回滚

- 单测：`tests/unit/test_plan_mode.py`（状态机/持久化/版本/越权键/决策/路由）；
  `PlanCard.test.tsx` + `sse`/store 的 `plan` 事件；
- 真机探针 `.local/verify_e1_1_plan_mode.py`：`/plan` 命令进出、计划落盘与版本、
  GET 读回、approve/reject/rollback、`plan` 事件先于 `done`；
- 派生面：新增 3 条路由 ⇒ `config/route_ui_intent.json` 标注 +
  **干净 worktree** 重派生 `reports/route_ui_coverage.json`、`reports/ui_reachability.json`；
- 回滚：删 `data/plans/` 不影响对话；`/plan exit` 一键退出；代码按 Epic 单提交可 revert。

## 7. 明确不做（留给后续）

- 不做"计划模式下强制只读"的硬闸门（DSH 也靠权限预设约束；硬拦需新工具分类，另立 Epic）。
- 不做 `/api/chat`（非流式编排路径）的计划事件（主 UI 走 `/api/chat/stream`；JSON 契约不动）。
- 不做计划 diff 视图（版本文件可 `diff`，UI 只给版本号与回滚）。
