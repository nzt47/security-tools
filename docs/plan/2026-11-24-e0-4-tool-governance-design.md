# E0-4 设计：工具治理小件（重复调用提醒）（2026-11-24）

> 路线图 `docs/plan/2026-11-24-dsh-parity-roadmap.md` §3 Phase 0 / E0-4。本页只写**决定与边界**，
> 并如实记录「哪一件已经有了、哪一件本次没做」。

## 1. 三件的现状核对（先查再写，避免重复造）

| DSH 小件 | 云枢现状 | 本轮处置 |
|---|---|---|
| `repeat-tool-reminder` | 无（模型在同参循环里没有任何提示） | **本轮交付** |
| `tool-call-timeout-policy` | **已有**：`agent/timeout_budget.py` 的分层上界（`CP_TOOL_HANDLER_TIMEOUT_SEC` 默认 1800s）+ 跟随工具自述超时 + run 级 deadline 传播；超时回结构化 `timeout_error_payload` | 不重复造；缺的是「协作式取消」（Python handler 无法被中断）→ 记余项 |
| `time-context` | 无 | **本轮未做**（理由见 §3）→ 记余项 |

## 2. 重复调用提醒（交付）

- 位置：`agent/tools/governance.py`，由 `tools.call()`（唯一汇聚点，覆盖工作台 SSE 与
  orchestrator 直连）在 handler 返回后调用——放在污点打标/spill 之前，于是提醒也会进
  spill 的落盘文本。
- 计数键：`tool + sha256(参数 JSON, sort_keys)[:16]`；**线程内**滑动窗口（默认 300s）；
  窗口外重新计数；有界 256 个键（满了清一半，避免长会话内存增长）。
- 提醒点：默认 `3,5,8`（与 DSH 默认一致），命中点才在结果里加字段：
  `_repeat = {tool, count, thresholds, window_seconds, hint}`；不阻断调用。
- 排除名单：轮询/状态类（`todo_status` 系列、`get_status`、`get_sensor_summary`、
  `list_async_tasks`、`list_processes`、`read_spill`）——同参重复是合法用法，提醒只会干扰。
- 审计：`tool_repeat_suspected`（count/thresholds；**不记参数原文**）。
- 与 DSH 的差异（如实写）：DSH 注入一条 user-role 提醒消息；云枢放在结果字段里
  （模型读到的工具结果本来就是 JSON），语义相同、形状按本仓「结果即上下文」口径。

## 3. 时间上下文（本轮未做，附证据）

把「当前时间 + 本步耗时」塞进**每个工具结果**（`_meta.at` / `_meta.duration_ms`）会被
仓内既有的「结果整字典相等」契约断言拦住：实跑 7 处 unit 直接红
（`test_capability_loader` / `test_injection_end_to_end` / `test_backpressure`），
且无法预知 integration/e2e 里还有多少同类断言。
⇒ 正确做法是在 **chat SSE 的消息通道**或 **orchestrator 的易变尾簇**里选**一个**正式注入点
（两处各写一套就是本仓明令避免的第二份口径），需要先定口径再动手。
本轮因此只交付重复提醒，并把这条登记在 `data/handoff_next.json::dsh-parity-e0-4-remaining`。

## 4. 开关与回滚

| env | 默认 | 说明 |
|---|---|---|
| `CP_TOOL_REPEAT_REMINDER` | true | 置 0 ⇒ 结果逐字回到旧形状（不提醒、不计数） |
| `CP_TOOL_REPEAT_THRESHOLDS` | `3,5,8` | 逗号分隔、去重升序；非法/不足两个值回落默认 |
| `CP_TOOL_REPEAT_WINDOW_SECONDS` | 300 | 10–3600 秒 |

均为 A 级、hot 生效（下一次工具调用即生效）。

## 5. 边界（如实写）

- 计数是**进程内/线程内**状态，不落库：多 worker / 重启后从 0 开始；跨进程的「全局重复」
  不在本层承诺。
- 只对 `dict` 结果注记；非 dict 结果（少数工具）不提醒。
- 提醒不改变工具语义与权限；`_repeat` 是**新增键**，消费方不得依赖它不存在。

## 6. 验收（真机）

- `.local/verify_e0_4_governance.py` 7/7：前两次不提醒、第 3 次 `_repeat.count=3`、
  换参数不提醒、排除名单不提醒、关闭开关后回到旧形状、审计有记录、结果无 `_meta`。
- 单测 `tests/unit/test_tool_governance.py`（17）；conftest 加 autouse 复位
  （口径 §10：测试隔离用 autouse fixture——否则前一个用例的两次调用会让后一个用例第 1 次
  就拿到 `_repeat`，整字典相等的断言随机红）。
- 干净 worktree：`scan_settings --check` 零缺口（新建 3 条 env 全部按字面量读取）、
  `sync_capability_manifest --check` 147 条一致；settings/治理/背压/能力 75 项全过。
