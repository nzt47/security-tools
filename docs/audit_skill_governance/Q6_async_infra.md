# Q6 审计：现有异步/后台任务机制（可复用于方案 3.6）

> 审计者：DSH（主审计）｜时间：2026-09-25｜方式：**只读**（未启动服务、未跑 pytest、未 commit）
> 说明：原派发的 Q6 子代理超时未产出，本节由主审计直接完成；结论均有 `file:line` 证据。

## 0. 结论速览

| 问题 | 结论 |
|---|---|
| 是否已有可复用的异步任务设施？ | **有，而且是完整的**：`AsyncExecutor` 具备 submit/status/result/cancel/list + JSONL 持久化 + TTL，且有 HTTP 端点暴露 |
| 能否直接承载方案 3.6「长任务异步模式」？ | **能承载 80%**；缺口是 ①线程池只有 **3** 个 worker ②状态存**进程内存 + JSONL**（非 SQLite trace）③`cancel` 是**协作式**（不能中断已进入的工具调用） |
| 取消语义是否可靠？ | **不可靠**：`future.cancel()` 对**已在运行**的 future 返回 False；无 subprocess/HTTP 级中断 |
| 背压三件套现状 | ①全局并发上限**未启用**（`check()` 走的是旧 token-bucket 路径，不碰 `max_concurrent`）②每技能限流**有**（token bucket）③超时硬截止**部分有**（工具级有，HTTP 排队级没有） |
| 是否有全局任务队列 | **无**。`tools/__init__.py` 的 `_RateLimiter()` 用**默认构造**（`max_concurrent=100`），而 `check()`→`_check_old()` 不使用并发计数 |

## 1. 现有后台任务设施清单

| # | 设施 | 位置 | 持久化 | 取消 | 超时 | 并发上限 |
|---|---|---|---|---|---|---|
| 1 | `AsyncExecutor`（核心） | `agent/async_executor.py:27` | **JSONL** `data/async_tasks.jsonl`（`:49`）+ 内存 `self._tasks`（`:46`） | ✅ `cancel()` `:188`（协作式） | ✅ `submit(timeout=…)` `:52` | `ThreadPoolExecutor(max_workers=3)` `:45`（默认 3，可配 `:428`） |
| 2 | 后台任务 HTTP 面 | `agent/server_routes/routes_background.py:53-113` | 复用 1 | ✅ `POST /api/background/tasks/<id>/cancel` `:113` | — | 复用 1 |
| 3 | 端点 | `GET /api/background/tasks` `:60`、`GET …/<id>` `:85`、`GET …/<id>/result` `:98` | — | — | — | — |
| 4 | 定时任务调度器 | `agent/scheduling.py:143 class Scheduler`（`threading.Thread` `:188`，`RLock` `:155`，`Event` `:159`） | `data/`（`load_from_json`，见 `app_server.py:1853`） | 停线程 | 无 | 单线程 |
| 5 | 工具闸门（异步路径也过闸） | `agent/tools/__init__.py:402-408` | — | — | — | — |
| 6 | 限流器 | `agent/rate_limiter.py:254 class RateLimiter`，实例在 `agent/tools/__init__.py:23` | 内存 | — | — | 见 §3 |

**§1 关键细节**：`AsyncExecutor` 的 `submit()` 内部调用 `agent.tools.call`（`agent/async_executor.py:22 from agent.tools import call as call_tool`，`:280` 注释明示「经 agent.tools.call ⇒ 过 tool_gate」）⇒ **异步任务不会绕过确认门**（这点与 S3 的技能脚本链形成对比）。

## 2. waitress 线程模型与并发关系

| 项 | 实测 |
|---|---|
| 启动点 | `app_server.py:1923-1926`：`serve(app, host="127.0.0.1", port=5678, threads=16)` |
| 传参 | **只有 4 个 kwarg**；`connection_limit=100`、`channel_timeout=120`、`backlog=1024`、`cleanup_interval=30` 全走出厂默认 |
| 可配置性 | **无**：全仓 `waitress` 只命中该一处，无 env / 无 config.yaml 键 |
| 请求模型 | `POST /api/chat`（`routes_chat.py:258`）与 `POST /api/chat/stream`（`plugins/chat.py:1414`）均为**同步 Flask 视图**，在 waitress 线程内同步执行 |
| LLM 池 | 另有一个 16 线程的 LLM 池（`orchestrator.py:175-177,4006-4008`），与 waitress 16 线程 **1:1** |
| 最坏并发 | **16**（waitress 线程数）；第 17–100 个连接进 OS backlog，**无排队超时** |
| 实测吞吐 | **20 并发 / 0 失败 / wall 3.4 s / p50 2.27 s / p90 3.18 s** ⇒ 简单请求下 16 线程够用 |

## 3. 背压三件套现状（方案 5.x / 3.6 的前置）

| 组件 | 状态 | 证据 |
|---|---|---|
| 全局并发上限 | **未启用** | `agent/tools/__init__.py:23` `_RateLimiter()` 用默认构造（`rate_limiter.py:268 max_concurrent=100`）；但 `:411` 调的是 `check(name)`，其单参形态走 `:402 _check_old()` ⇒ **token bucket 路径，不经过 `_acquire_concurrent()`**（`:374`）。真正会占用并发额度的是 `_check_multi_level()`（`:319-372`），**生产无调用者** |
| `release()` 配对 | **缺失** | `_check_old()` 不 acquire 并发额度，故 `tools/__init__.py` 也从不调用 `release()`（`:393`）⇒ 即便切到新路径也会泄漏额度 |
| 每技能/分类限流 | ✅ **有** | `rate_limiter.py:474 register_rule` + `:480 _get_bucket`，按 `_category_from_name/_meta`（`:106/:117`）分类 |
| 超时硬截止（工具级） | ✅ 有 | 工具各自超时；子进程类 30 s（`subagent/sandbox.py:196`、`skills_mgmt/executor.py:129`） |
| 超时硬截止（HTTP 排队级） | ❌ **无** | waitress `channel_timeout` 不是排队超时（`server.py:342-351` 只回收空闲通道） |
| 用户级/端点级限流 | ✅ 有能力 | `_check_multi_level` + `_get_endpoint_bucket`/`_get_user_bucket`（`:491/:505`）——**未被生产使用** |

**参数初值建议**：`max_concurrent = 8`（16 线程的一半，给健康采集/后台留余量）；排队超时 `= 20 s`（对齐 C1 的 15 s 预算 + 余量）；每技能限流沿用现有分类桶，把 `shell_execute`/`run_program` 等 L2/L3 技能单独设低容量（如 capacity=2, refill=0.2/s）。

## 4. 复用 vs 新建（方案 3.6 结论）

| 3.6 的要求 | 复用 / 新建 | 落点 |
|---|---|---|
| 立即返回任务句柄 + 预计进度 | ✅ **复用** | `AsyncExecutor.submit()` 返回 `task_id`；`GET /api/background/tasks/<id>` 已可查 |
| 落 SQLite trace | ⚠️ **需改造** | 现为 JSONL（`async_executor.py:49`）。方案要求「落现有 SQLite trace」⇒ 应改为写 `agent/data/tool_trace.db::unified_traces`（表已存在：`trace_id/task_id/capability_id/status/duration_ms/total_tokens/cost_usd`） |
| 预估执行时长 → 自动转异步 | ❌ **新建** | 无任何时长预估模型；且 `unified_traces` 的 `duration_ms` 全为测试数据（0.03 ms 级）⇒ **需先有 F3 的真实耗时数据** |
| 用户改意图 → abort 在途任务 | ⚠️ **半可用** | `cancel()`（`:188`）只能取消**未开始**的 future；对已进入 `agent.tools.call` 的执行**无法中断**。真正的取消需要：把长任务改为**子进程 + 可 kill 句柄**，或给工具加 `cancel_token` 检查点 |
| 背压三件套 | ⚠️ **部分复用** | 每技能限流可复用；全局并发 + 排队超时需新建（§3） |
| 并发度 | ⚠️ **需调参** | `max_workers=3` 对长任务偏小；且与 waitress 16 + LLM 16 叠加后线程总量需重新核算 |

## 5. 对方案 3.6 的直接修正建议

1. **不要新建任务框架**，把 `AsyncExecutor` 的持久化后端从 JSONL 改为 `unified_traces`，`max_workers` 提到 6–8。
2. **「abort 在途任务」必须降级表述**：当前能力是「取消排队中的任务 + 阻止后续轮次」，不是「中断正在执行的调用」。要真正中断，需先做 C1（超时生效）+ 子进程化。
3. **`<5 s 决策 + >5 s 执行」的 5 s 阈值需要数据支撑**：当前 `unified_traces.duration_ms` 无真实数据，应排在 F3 之后。
4. **顺序**：C2（背压）→ F3（真实耗时数据）→ 3.6 异步模式。**在背压与真实耗时数据到位前落地 3.6，只会把「同步阻塞」变成「异步堆积」**。
