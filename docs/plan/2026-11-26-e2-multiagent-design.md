# E2 多代理协作（设计） — 2026-11-26

> 状态：**E2-1a 已实现（2026-11-26，引擎 + 工具）**；E2-1b（3 条路由 + 前端「运行中」块）、
> E2-2/E2-3/E2-4 仍为设计稿。已交付：`agent/subagent/runs.py`（RunRegistry）、执行器每轮
> drain 追加指令 + 轮间协作式打断、`subagent_control` 工具（list/send/interrupt/inbox）、
> 17 条单测 + 真机探针 `.local/verify_e2_1_control.py`。对齐 DSH 的子代理控制 / Team 任务板 / fork / Ralph 循环，
> **复用**本仓已有的 `delegate` / `fan_out` / 生命周期管理器 / `TaskBoard` / callback outbox。
> 勘察结论：本仓已具备「一次性委派 + 任务看板落盘 + 回调队列」，缺的是**运行中控制**
> （发消息 / 打断 / 发现 / 收件箱）、**Team 认领流**、**fork 上下文继承**、**Ralph 反复推进**。

## 1. 现状勘察（2026-11-26 实测，按文件）

| 能力 | 现状 | 文件 |
|---|---|---|
| 委派（同步） | `delegate` 工具 + `/api/subagent/delegate`、`/api/subagent/<name>/delegate` | `agent/tools/subagent_tools.py`、`routes_subagent.py` |
| 并发委派 | `fan_out（N 任务并发）` | `agent/tools/fan_out_tools.py` |
| 生命周期 | `SubagentLifecycleManager.create/destroy/delegate/delegate_many/gc/get_stats` | `agent/subagent/lifecycle.py` |
| 任务看板 | `TaskBoard.record_create/record_update/record_outcome/query/snapshot`（JSONL 落盘） | `agent/subagent/task_board.py` |
| 回调 | `callback_channel` + `callback_outbox`（落盘 + `pending/replay`） | `agent/subagent/callback_*.py` |
| 路由 | `/api/subagent/{list,capabilities,history,<name>,bundle,offline_pack,import,callback,create,<name>/destroy,<name>/execute,<name>/delegate,delegate,<name>/reload}` | `agent/server_routes/routes_subagent.py` |
| **运行中控制** | **无**（delegate 是同步阻塞，没有 run 句柄、没有 inbox、没有 interrupt） | — |
| **Team 认领流** | 只有落盘看板，没有 claim/release/handoff | — |
| **fork** | 无（子代理从零装配上下文） | — |
| **Ralph** | 无（goal 的 max_rounds 只作用于**本会话**，不作用于子代理） | — |

## 2. 目标与验收

1. **E2-1 运行中控制**：模型/UI 能 `list` 正在跑的子代理、给它 `send` 一条追加指令（下一轮生效）、
   `interrupt` 打断（协作式，不硬杀进程）、读 `inbox`（子代理回传/回调队列）。
2. **E2-2 Team 任务板**：任务可 `claim/release/handoff`，看板按状态分组，UI 在既有子代理页内呈现。
3. **E2-3 fork**：`delegate(fork=true)` 把父会话最近 N 条消息带进子代理上下文（可裁剪、可关闭）。
4. **E2-4 Ralph 循环**：同一任务反复推进直到满足验收（复用 E1-2 goal 的验收/证据口径），
   有轮次上限与止损（连续无进展即停）。

**验收（逐条可测）**：① `subagent_control(action=list)` 能看到正在跑的 run（含 subagent/耗时/轮次）；
② send 后子代理**下一轮**确实收到（执行轨迹里出现该消息）；③ interrupt 后 run 以
`interrupted` 结束且已产出内容保留；④ inbox 能读到回传；⑤ 看板 claim 后他人不能再领（乐观锁）；
⑥ fork 子代理的第一轮上下文含父会话摘要且 token 有上限；⑦ Ralph 在第 N 轮满足验收即停、
不满足则按上限停并给出原因；⑧ 既有 17 条探针 + E1 系列探针不回归；`ui:check` 全绿；CI 30/30。

## 3. 关键取舍

| 问题 | 决策 | 理由 |
|---|---|---|
| run 句柄放哪 | `agent/subagent/runs.py`：进程内 `RunRegistry`（run_id → {subagent, thread, cancel_event, inbox_path, started_at, rounds, last_output}） | delegate 现在是同步函数；要"发现/发消息/打断"必须有**运行期登记表**。落盘只存只读快照（`data/subagent_runs.json`），进程重启即失效（运行中 run 本来就不跨重启） |
| send 语义 | **不打断当前轮**：消息进 run 的 `inbox.jsonl`，执行器在**每轮开始前** drain 成一条 user 消息 | 与 DSH 同口径；不做"抢占式中断"，避免半截工具调用 |
| interrupt 语义 | 协作式：置 `cancel_event`，执行器在**每步之间**检查 ⇒ 结束本轮、保留已产出、outcome=`interrupted` | 硬杀线程/进程会破坏审计与工作区一致性 |
| 控制入口 | **一个工具** `subagent_control(action=list|send|interrupt|inbox)`（与 `goal` 的 8 动作同形） | 4 个独立工具 = 4 倍 5 处连锁与索引稀释；一个工具最省 |
| 看板认领 | JSONL + 乐观锁（`claim` 写入 `owner/claimed_at`，并发用文件锁 + 版本号；冲突回 409） | 与既有 TaskBoard 同存储；不引数据库 |
| fork 上下文 | 取父会话**最近 6 条**消息 + 摘要，总字符 ≤4000；`fork=false` 默认 | 全量上下文会灌爆子代理窗口；摘要复用 `session_insight` |
| Ralph 止损 | 最多 `max_rounds`（默认 4，上限 8）；连续 2 轮「无新证据/无新产出」即停 | 防止"一直转圈烧 token" |
| UI 位置 | 既有「子代理」页新增「运行中 / 任务板」两块，**不新增叶子页** | `ui_reachability` 按页面文件集派生，多一个 .tsx 就漂移 |

## 4. 接口与数据结构

### 4.1 运行期登记（`agent/subagent/runs.py`）

~~~python
@dataclass
class RunHandle:
    run_id: str          # r-<12hex>
    subagent: str        # 子代理名
    started_at: str
    rounds: int
    status: str          # running | done | interrupted | failed
    cancel_event: threading.Event
    inbox_path: str      # data/subagent_runs/<run_id>/inbox.jsonl
    last_output: str     # 摘要（≤500 字）
~~~

- `register(...)` / `unregister(...)` / `list_runs()` / `get(run_id)` / `snapshot()`（落 `data/subagent_runs.json`，运行期状态不提交）。
- 执行器（`agent/subagent/executor.py` 的轮循环）每轮开始前：`drain_inbox(run_id)` →
  追加为 user 消息；每步之间检查 `cancel_event`。

### 4.2 工具 `subagent_control`（E2-1）

~~~json
{"action": "list"}
{"action": "send", "run_id": "r-1a2b3c4d5e6f", "message": "把范围收窄到 plugins/chat.py"}
{"action": "interrupt", "run_id": "r-1a2b3c4d5e6f", "reason": "需求变了"}
{"action": "inbox", "limit": 10}
~~~

返回（统一形状）：`{ok, action, runs:[{run_id, subagent, status, rounds, elapsed_s, last_output}],
inbox:[{run_id, kind, text, at}]}`；`effect=write`（send/interrupt 会改变运行中的任务）⇒
`permission_level` 由治理轴派生（write+act ⇒ 需确认档），**默认 action=list 为只读**。

### 4.3 路由（E2-1/E2-2）

- `GET  /api/subagent/runs` → 运行中登记表（只读，UI「运行中」块）
- `POST /api/subagent/runs/<run_id>/message` `{message}` → 追加到 inbox（需令牌）
- `POST /api/subagent/runs/<run_id>/interrupt` `{reason}` → 置 cancel（需令牌）
- `GET  /api/team/board?status=&limit=` → 看板快照（复用 `TaskBoard.snapshot`）
- `POST /api/team/board/claim` `{task_id, owner}` / `POST /api/team/board/release` `{task_id}`
  / `POST /api/team/board/handoff` `{task_id, to}`（乐观锁，冲突 409）

### 4.4 fork（E2-3）

`delegate(..., fork=true, fork_messages=6)`：把父会话最近 6 条消息（含角色，正文合计 ≤4000 字）
作为子代理任务提示的前置上下文；`fork_summary=true` 时用 `session_insight.outline()` 摘要替代全文。
落盘时在任务记录的 `metadata.fork` 里写 `{from_session, messages, chars}`（可审计）。

### 4.5 Ralph（E2-4）

`delegate(..., ralph=true, ralph_rounds=4, acceptance="...")`：
- 每轮结束用 E1-2 goal 的**证据口径**判断验收（`evidence` 必须非空且能对上 acceptance 条目）；
- 达标记 `status=done`；否则下一轮把"还差什么"作为追加指令（走 inbox 同一条链路）；
- 上限或连续 2 轮无新证据 ⇒ `status=blocked` + `reason`（`need_user`/`unclear`），**不静默续跑**。

## 5. 安全 / 边界

- **不硬杀**：interrupt 只置标志；超时仍由既有 `--timeout` 兜底（进程级）。
- **并发**：inbox 追加用文件锁（与 `TaskBoard` 同一把锁实现）；run 登记表进程内，跨进程不共享。
- **权限**：`send|interrupt` 走 `effect=write` ⇒ 审批档位按治理轴；`list|inbox` 只读。
- **注入**：子代理回传文本进父上下文前用 `assert_untainted`（既有的 taint 检查）；
  父会话消息进子代理时**截断 + 标注来源**，不当系统指令。
- **不提交运行期状态**：`data/subagent_runs/**`、`data/subagent_runs.json`、看板 JSONL 的写入口径不变。
- **明确不做（首版）**：跨机器编排、子代理互相直接发消息（只经父）、Ralph 的自适应轮次。

## 6. 切片（每片单独提交/部署/CI）

1. **E2-1 运行中控制**：`runs.py` 登记 + 执行器 drain/检查 + `subagent_control` 工具（1 个新工具
   ⇒ 5 处连锁 + 派生面）+ 3 条路由 + UI「运行中」块。探针：list/send/interrupt/inbox。
2. **E2-2 Team 任务板**：`/api/team/board*` 4 条路由 + 乐观锁 + UI「任务板」块。探针：claim 冲突、
   handoff、按状态分组。
3. **E2-3 fork**：`delegate` 参数 + 上下文装配 + metadata 审计 + UI 勾选。探针：上下文确实带入、
   超长截断、关闭时不带。
4. **E2-4 Ralph**：`delegate` 参数 + 验收判定 + 止损 + 记录。探针：达标即停、上限停、无新证据停。

## 7. 回滚

- 运行中控制：`CP_SUBAGENT_CONTROL_ENABLED=0` ⇒ 工具回 `code="disabled"`、路由 404、UI 隐藏。
- Team 看板：`CP_TEAM_BOARD_ENABLED=0` ⇒ 路由 404（看板本身仍由 TaskBoard 落盘）。
- fork / Ralph：纯参数，缺省关闭 ⇒ 行为与改动前逐字一致。
- 工具级回滚仍走 YAML `enabled: false` + 干净 worktree 重派生。

## 8. E2-1 实施清单（可直接照做）

**代码（按依赖顺序）**

1. `agent/subagent/runs.py`（新）：`RunHandle` dataclass + `RunRegistry`（`register/get/list_runs/unregister/snapshot`）；
   `snapshot()` 落 `data/subagent_runs.json`（运行期状态，不提交）；`inbox_path` 在
   `data/subagent_runs/<run_id>/inbox.jsonl`；`append_inbox/drain_inbox` 用文件锁 + JSONL。
2. `agent/subagent/executor.py`：轮循环开头 `drain_inbox(run_id)` → 作为 user 消息追加；
   每步之间 `if handle.cancel_event.is_set(): outcome=interrupted; break`；
   `delegate()` 入口 `register(...)`、`finally: unregister(...)`（`lifecycle.py` 里那层更合适，若 delegate 在 lifecycle）。
3. `agent/tools/subagent_control.py`（新）：`run_list/run_send/run_interrupt/run_inbox` 四个纯函数
   + `register_all(self)`（**签名必须能接 self**，见 §11.231 D.1）；工具名 `subagent_control`，
   action 枚举 `list|send|interrupt|inbox`；`effect=read`（list/inbox）与 `write`（send/interrupt）
   混合 ⇒ 按最严的 `write` 登记，在 description 里写清"list/inbox 只读"。
4. `agent/orchestrator/lifecycle_manager.py`：注册新工具（与 ask/goal 同一处）。
5. `data/tool_definitions/subagent_control.yaml`：description 与 register() **逐字一致**；
   `plane/effect/risk` 用 `backfill_tool_planes.py` 的 PLANE_MAP 补条目后回填。
6. 路由（`agent/server_routes/routes_subagent.py` 追加或新模块）：
   `GET /api/subagent/runs`、`POST /api/subagent/runs/<run_id>/message`、
   `POST /api/subagent/runs/<run_id>/interrupt`；写端点 `@require_token`；
   `config/route_domains.json`（分身与主线）+ `config/route_ui_intent.json`（should_be_in_ui）。
7. 前端：既有子代理页加「运行中」块（`Table`/`Badge`/`Button`）：列 run_id/子代理/状态/轮次/耗时/
   最后输出；行内「发消息」「打断」两个动作 + 输入框；`lib/subagentApi.ts` 加三个方法。

**派生（干净 worktree，一次做完）**

- `scripts/sync_tool_index.py`（101→102）、`scripts/sync_capability_manifest.py`（152→153）+
  盘点表、`scripts/gen_baseline_bm25_raw.py`（S0 重标定）、`scripts/run_routing_reliability.py`、
  路由冲突 v1/v2 τ 重算（`eval_route_conflict.py` 的基线字典若变要同步）、
  7 条线 boost +1 / max_tools +1（若该工具要在主线可见）、计数哨兵（102/153/…）。
- `reports/route_ui_coverage.json` + `reports/ui_reachability.json` 重派生（3 条新路由 ⇒ 路由 531→534）。

**测试**

- `tests/unit/test_subagent_runs.py`：登记/注销/快照、inbox 追加与 drain、并发追加不丢行。
- `tests/unit/test_subagent_control.py`：list 空/有 run、send 到不存在 run 的降级、
  interrupt 置位、inbox 读取上限、`register_all(None)` 可调用。
- `tests/unit/test_subagent_control_routes.py`：3 条路由的 200/400/404/401 形状。
- 前端：子代理页「运行中」块渲染 + 两个动作调用（mock `subagentApi`）。

**真机探针 `.local/verify_e2_1_control.py`**

1. 起一个长任务（`delegate` 一个 sleep/多轮任务）→ `GET /api/subagent/runs` 能看到；
2. `POST .../message` → 子代理下一轮轨迹里出现该文本；
3. `POST .../interrupt` → run 变 `interrupted`、已产出保留；
4. `GET /api/subagent/runs` 列表为空（注销）；5) 既有 17 条 + E1 系列探针不回归。

**收尾**

- 部署（先停后端 → deploy → 三处一致）→ 探针全绿 → 文档（roadmap Phase 2 状态、
  handoff_next、交接 §11.233）→ 代码 CI 绿后再推 [skip ci] 文档。
