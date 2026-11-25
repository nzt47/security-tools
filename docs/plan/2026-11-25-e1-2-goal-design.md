# E1-2 Goal（目标）设计 — 2026-11-25

> 状态：设计定稿，待实现（本轮只交付设计页；实现按 §9 的切片顺序）。
> 对齐目标：DSH `goal` + `goal-round-driver` + `tool-goal` + GoalBar 的**语义与验收**，
> 不照搬 Cordis/TS 实现。关联：E1-1 计划模式（同为会话级工作方式）、E0-3 检查点（完成证据）、
> E4-2 PTC/Workflow（不重叠）。

## 0. 一句话

**Goal = 一个会话里唯一一个"跨多轮持续推进直到完成"的目标**：模型可以创建/编辑/暂停/恢复/
完成/阻塞/清除；每轮结束由 driver 决定是否自动续跑（有轮次与预算上限）；UI 用一条 GoalBar
展示状态与进度；全过程事件溯源，重启/恢复/复盘都能还原。

## 1. 与既有机制的分工（**不造第二套状态**）

| 机制 | 语义 | 与 Goal 的关系 |
|---|---|---|
| `todo_write`（`data/todos.json`） | 会话内**清单**：做哪些步骤、每步状态 | Goal 的**子计划**可复用清单；Goal 不重复存步骤 |
| E1-1 `plan_mode` | 会话级**工作方式**：先只读调研出计划 | 计划批准后可以"立目标去执行"；Goal 不依赖 plan_mode |
| `planning` 引擎（`data/planning/plans.db`） | 任务 DAG 规划/检查点 | 内部规划器，不暴露给用户当目标；Goal 不复用它 |
| 调度（cron/`schedule_task`） | **周期性/定时**任务 | Goal 是**会话内连续推进**，不是 cron；到期提醒可另接 |
| 交接文档（`docs/handoff/口径.md`） | 跨会话的**人工**交接 | Goal 跨**轮次**（同一会话），不跨会话；两者不合并 |

> 判据：用户问"这个目标到哪了"应在一个地方回答（GoalBar + `/goal status`），
> 而不是让 todo / plan / handoff / scheduler 各说一套。

## 2. 数据模型与事件溯源

**路径**：`data/goals/<sha256(session_id)[:32]>/`（与 plan_mode 同款派生键，CodeQL 三铁律）

- `events.jsonl`：**唯一事实源**，append-only（每行一个事件，含 `seq`、`at`、`type`、`payload`）；
- `state.json`：投影快照（可随时由 events 重放得到；只是加速读，**不是**权威）。

**事件**（`seq` 单调递增；同会话单写者，进程内锁 + 追加后 fsync）：

~~~text
goal_created   {goal_id, title, why, acceptance[], max_rounds, budget?}
goal_edited    {title?, why?, acceptance?}
goal_paused    {reason?}
goal_resumed   {}
goal_completed {evidence, summary}
goal_blocked   {code, reason}          # code ∈ {need_user, external, permission, unclear}
goal_round     {round, session_turn_id?, note?}
goal_cleared   {}                       # 新目标从 goal_created 重新开始
~~~

**投影状态**：`{goal_id, title, why, acceptance[], state, round, max_rounds, budget,
created_at, updated_at, blocked{code,reason}?, evidence}`；
`state ∈ {active, paused, blocked, completed, cleared}`（无目标 = `idle`）。

**上限**：`max_rounds` 默认 **256**（DSH 同值），可创建时指定（1–1000）；事件日志按目标
保留最近 2000 条（超出时写 `log_trimmed` 标记，不删目标状态）。**单一目标**：新 `goal_created`
自动 `goal_cleared` 旧目标（同一事件流，可回放）。

## 3. 模型工具 `goal`（新增 1 个工具，走 5 处连锁）

单工具多动作（与 DSH `tool-goal` 同形），`action ∈ {create, edit, pause, resume, complete,
block, clear, status}`：

~~~json
{"name": "goal",
 "parameters": {"action": "create|edit|pause|resume|complete|block|clear|status",
                "title": "…", "why": "…", "acceptance": ["…"],
                "max_rounds": 256, "budget_tokens": 200000,
                "evidence": "…", "summary": "…", "code": "need_user", "reason": "…"}}
~~~

- `create` 时若无 `acceptance`，工具返回**提示**（不阻断）：目标必须有可验收条件；
- `complete` 必须带 `evidence`（命令输出/文件/检查点 id），否则拒绝（`code=missing_evidence`）；
- `block` 必须带 `code` + `reason`（`need_user` 表示等用户输入，driver 不再自动续跑）；
- 工具结果**只返回状态摘要**（≤400 字符），不把 events 全量塞回模型（预算）。

**5 处连锁清单**（实测步骤见交接 §11.221 E-1）：`sync_tool_index` → `gen_baseline_bm25_raw`
定新 S0 → 改 `_BM25_HALF_SATURATION` → `run_routing_reliability` → `eval_route_conflict` →
`sync_capability_manifest`（147→148）+ 盘点表 → 三个 `backfill_* --check`；
另改 `_DEFAULT_TOOL_CATEGORIES`、真实索引计数哨兵（97→98）、`RESULT_SCHEMA_REQUIRED_TOOLS`+FLOOR、
`test_capability_spec` 入库计数（97→98 / 147→148）。

## 4. Round driver（自动续跑）——**复用同一条对话链路**

**触发**：一轮正常收尾（`done` 之前）时，若 goal `state=active` 且 `round < max_rounds`
且未超预算 ⇒ 在 SSE 追加 `goal` 事件（先于 `done`），载荷带
`auto_continue=true` 与 `next_prompt`（服务端生成，含目标标题/当前轮次/下一步提示）。

**执行**：前端收到后**用现有 `sendMessage(next_prompt)` 续跑**（与用户手打同一链路：
记忆/审计/落盘/预算一套口径），不造"服务端自己发起 LLM 轮"的第二条通道。
`goal_round` 事件由服务端在下一次收尾时递增（以 SSE `turn_id` 去重，防前端重发/重连重复计数）。

**为什么不用服务端后台线程续跑**：`/api/chat/stream` 是请求级生成器，后台续跑会绕开
前端"停止生成"、权限档位 contextvar 与超时预算；首版用前端驱动，
**边界如实写进设计页**：页面关闭/断网即停（恢复后 GoalBar 显示"已暂停：无活动"，
用户点「继续」或重新发消息即恢复）。

**停止条件**（任一，写 `goal_paused`/`goal_blocked`/`goal_completed`）：
`round >= max_rounds`（`blocked code=budget`）、累计 token 超 `budget_tokens`、
用户暂停、模型 `complete`/`block`、会话被删除。

## 5. 用户侧命令（不依赖模型）

`/goal`（查看状态）、`/goal pause`、`/goal resume`、`/goal clear`、`/goal accept <条目>`
（把用户补充的验收条件写进事件流）。命令与 E1-1 同构：纯状态操作不进 LLM，
回复走 SSR 文本 + `goal` 事件（先于 `done`）。

## 6. HTTP API（3 条，新增路由须重派生两份 reports）

- `GET  /api/goal/state?session_id=` → 投影状态 + 最近 20 条事件摘要（只读）；
- `POST /api/goal/mode` `{session_id, action: pause|resume|clear}`（`@require_token`）；
- `POST /api/goal/accept` `{session_id, item}`（`@require_token`）。

`config/route_ui_intent.json`（家族键 `/api/goal/*`）+ `config/route_domains.json`
（归「对话与会话」）+ **干净 worktree** 重派生 `route_ui_coverage.json`、`ui_reachability.json`。

## 7. GoalBar（UI）

位置：**会话页工具栏下方、消息区上方**（一行；不新增页面/叶子 ⇒ 深链不变）。
内容：目标标题（截断 40 字）· 状态徽标（进行中/已暂停/阻塞/已完成）· 轮次 `12/256` ·
按钮「暂停/继续」「清除」「详情」；展开区显示 why、验收条件、最近事件（只读）。

与 E1-1 计划卡的关系：**二者可同时存在**（先计划、后立目标），视觉上计划卡在消息区下方、
GoalBar 在顶部；颜色区分（计划=琥珀、目标=青）。状态徽标/折叠展开必须遵
`docs/ui/封装规范.md`（标题字号写标签 className；按钮用 `Button`；0 新增 raw 控件）。

## 8. 回滚与安全

- 回滚 = `goal_cleared` 事件 + 重放；事件日志不删除（保留复盘）；`data/goals/` 随会话删除清理。
- 目标内容会进 system prompt（稳定块），**不改变**权限预设/审批/沙箱边界（与 E1-1 同口径）。
- 路径：sha256 派生键 + 正则捕获后再进路径（CodeQL 三铁律）；`os.listdir` 不拼路径。
- 自动续跑是**用户可见**的普通消息（审计链照记），不是静默后台轮。

## 9. 实现切片（每片独立提交/部署/CI，禁止大爆炸）

1. **E1-2a**：`agent/context/goal.py`（事件溯源 + 投影 + 单测）+ `/goal` 命令族 + **不含**
   模型工具与 UI（同 E0-3 的"先服务层"口径；**不造无引用的 HTTP 路由**）。
2. **E1-2b**：`goal` 工具 + 5 处连锁 + system prompt 片段 + 单测/探针。
3. **E1-2c**：round driver（SSE `goal` 事件 + 前端自动续跑 + 轮次/预算）+ 单测。
4. **E1-2d**：GoalBar（UI + 测试 + 截图）+ 3 条路由 + 派生面。

> 依赖顺序：a → b → c → d；a/b 可回滚（删 `data/goals/` 不影响对话），c 依赖 b 的
> `max_rounds`，d 依赖 a 的状态读。

## 10. 验收（实现后逐条可测）

1. 模型 `create` 后 GoalBar 出现，标题/验收条件与工具入参一致；
2. 收尾时 `goal` 事件先于 `done`，前端自动续跑一轮，`goal_round` 只 +1（重连不重复）；
3. `pause` 后不再自动续跑；`resume` 恢复；`clear` 后状态 idle 且事件流仍可回放；
4. `complete` 缺 `evidence` 被拒（`code=missing_evidence`）；`block` 缺 `code/reason` 被拒；
5. `max_rounds=2` 时第 3 轮不再续跑，状态 `blocked(code=budget)`；
6. 重启后端 / 刷新页面后 GoalBar 状态一致（事件重放 = 快照）；
7. 删除会话后 `data/goals/<key>/` 被清理；
8. 真机探针 `.local/verify_e1_2_goal.py`（不调模型，走命令/API）≥12 项；
9. `code scanning 0`、CI 30/30、三处版本一致。

## 11. 明确不做（本轮/首版）

- 跨会话/团队目标（属 E2-2 Agent Team）；
- 服务端后台续跑（首版前端驱动，边界见 §4）；
- LLM 判定"目标是否完成"（完成由模型声明 + evidence，用户可 `/goal clear` 否决）；
- 与调度器打通（"到点提醒目标"另立小项）。
