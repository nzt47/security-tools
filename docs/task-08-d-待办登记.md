# TASK-08 子工作流 D —— 判定为「不在本任务修复」的项与遗留待办登记

本文件由 TASK-08 子工作流 D（超时上界 / 重试预算收敛 / 启动期静默失败 / 数据竞争评估）
产出。任务书要求：**若某项判定不应在本任务实现，必须写明量化理由并登记为待办 —— 不得留空**。

凡标注「不改」的项，均给出**实测或代码位置级**的证据；凡标注「遗留」的项，均给出
**风险描述**与**建议修法**。

---

## 1. `task_timeout = 600s` 是否下调或分层 —— 判定：**不改**，但登记为待办

**位置**：`agent/tool_calling.py`（默认 600，可由 `tool_calling.task_timeout` 配置覆盖）

### 为什么不下调（量化理由）

1. **量纲不匹配，不能与 TR 3s 直接比较。**
   `task_timeout` 是**整个工具循环任务**的墙钟上界（最多 21 轮 LLM + 工具执行）；
   TR 3s 是**单次请求**的延迟目标。拿「任务级预算」比「单次请求目标」是范畴错误。
   实测确认它是**任务级**：`threading.Timer(self._task_timeout, self._timeout_event.set)`
   在 `chat_with_steps` 入口启动，覆盖整个 `for round_idx in range(self._max_rounds + 1)`。

2. **下调会削掉已文档化的工具契约。** 实测各工具自述上限（grep 证据）：
   | 工具 | 自述上限 |
   |---|---|
   | `test_tools` | 默认 300 / **最大 900** |
   | `lint_tools` | 默认 180 / **上限 600** |
   | `git_tools` | 默认 60 / 最大 120 |
   | `shell_tools` | 硬夹在 1–120 |
   | `code_tools` | 显式「不设置则不限时」 |
   若把任务上界压到 600 以下，`run_tests`（900）这类**合法的长任务**会被直接砍断。
   这不是"收紧防护"，而是"改坏功能"。

3. **它本身是有效上界，不是缺失的上界。** 与 E1j 的两条**无上界**路径不同，
   600s 是**已生效**的界（每个轮次开头 `if self._timeout_event.is_set():` 判定并终止循环）。
   故它不属于「超时缺失」，不需要"补"。

### 但存在一个**真实的语义缺口**（已登记为待办，不在本任务修）

**缺口**：任务超时**只在轮次边界被轮询**（`agent/tool_calling.py:346`），
它**不会中断**正在进行中的 LLM 调用或工具 handler。
⇒ 单个挂死的 handler 不会被 600s 的定时器打断，只会被**它自己的**上界打断。
⇒ 在本次改动后，单次 handler 的全局天花板默认是 **1800s > 600s**，
   即「单次工具调用」可以超过「整个任务」的预算 3 倍 —— 任务上界因此
   **不是**端到端的最坏耗时上界。

**为什么不在本任务修**：把 handler 天花板压到 600s 以下会直接违反上表第 2 条
（`test_tools` 合法需要 900s）。正确修法是**协作式取消**
（把 deadline 传进 handler / 让 `call_with_timeout` 支持中断信号），
那是一处**跨模块的接口变更**，超出子工作流 D 的范围，且会影响全部 90 个 handler。

**建议修法（待办）**：
1. 让 `ToolCallingService` 在派发每个工具前把**剩余预算**传给 `agent.tools.call()`
   （例如 `params["__deadline__"]` 或新增可选参数），使单次工具调用的上界 = `min(工具自身上界, 剩余任务预算)`；
2. `call_with_timeout` 增加 `cancel_event` 支持，使任务超时能真正打断在飞的 handler；
3. 补一条端到端测试：任务预算 5s + 挂死 handler ⇒ 总墙钟 ≤ 5s + ε。

---

## 2. 遗留：reranker 子进程 EOF 分支的 `stderr.read()` 仍**无上界**

**位置**：`agent/tool_router_reranker.py:322`
```python
err = self._proc.stderr.read() if self._proc.stderr else ""
```

**状态**：本任务已修复该文件的**两处** `stdout.readline()`（启动就绪读 → 60s；
predict 响应读 → 30s，均经 `_readline_with_timeout`），但这一处 `stderr.read()` 属于
**第三个**阻塞读点，未被覆盖。

**风险**：仅在 `ready_line` 为空（子进程已退出或无输出）时触发。
正常情况下子进程已退出 ⇒ 管道关闭 ⇒ `read()` 立即返回 EOF。
但若子进程**仍存活却不写 stderr**（例如卡在原生代码里），`read()` 会阻塞到
子进程被回收 —— 即**该分支仍可无限阻塞**。触发概率低（需子进程半死状态），
但性质与已修的另两处相同。

**建议修法**：复用同文件的 `_readline_with_timeout` 思路，为 stderr 增加
一个短上界（例如 5s）的读取；或先 `poll()` 确认进程已退出再读。
**登记原因**：该分支不在任务书点名的两处范围内，且改动需重新验证 EOF 语义，
故按"登记 + 说明风险"处理（任务书允许二选一）。

---

## 3. 遗留：MCP 重试的可重试异常集合**过窄**

**位置**：`mcp_services/mcp_client.py:134`
```python
def retry_on_failure(..., retry_exceptions: tuple = (TimeoutError,)):
```

**实测事实**：装饰器**只重试 `TimeoutError`**。而 `MCPClient.initialize()` 在收到
协议错误时抛的是 `RuntimeError`（`mcp_client.py`：`raise RuntimeError(f"MCP初始化失败: ...")`），
**不会被重试**。`call_tool` / `list_tools` 同样抛 `RuntimeError`。

**影响**：`max_retries` 的语义实际只对超时路径生效；协议级瞬时失败
（server 重启中、握手竞态）**一次都不重试**。这与"重试放大"是相反的偏差 ——
本任务的方向是**收敛**重试，故**不宜顺手放宽**（放宽会让本任务刚建立的预算
更容易被吃满，方向相反）。

**建议修法（需独立评估）**：把默认可重试集合扩为
`(TimeoutError, ConnectionError, RuntimeError)`，并**同时**把该路径接入
重试预算；两者必须一起改，否则等于单方面放宽放大。本任务只完成"预算侧"，
扩大集合属于策略决策，登记待办。

---

## 4. 遗留：`register_all_routes` 是死代码，且引用了**已删除**的模块

**位置**：`agent/server_routes/__init__.py:20`（函数）、`:54`
```python
from .routes_memory import register_routes as reg_memory
```

**实测事实**：
- `agent/server_routes/routes_memory.py` **不存在**（模块已被删除/改家）；
- `register_all_routes` **无任何调用方**（`app_server.py` 逐模块显式注册；
  该函数自己的模块注释也写明它是死代码，见 `routes_semantic_config.py` 的说明）。

**影响**：**当前不影响运行**（死代码不会被调用）⇒ 属**潜伏**缺陷而非线上缺陷。
但一旦有人为了"统一注册"而调用它，会立刻 `ModuleNotFoundError` 并且
（因为它没有逐模块 try/except）**直接中断启动**。

**建议修法**：删除 `register_all_routes`，或删掉该行 import 并补注释说明
「记忆类路由已改由 `plugins/memory.py` 提供」。**登记原因**：删改会让
`agent/server_routes/__init__.py` 的行为契约发生变化，需与 TASK-06 的路由
盘点一起做，避免与多租户/身份面的路由归属判断冲突。

---

## 5. 遗留：检查器尚未接入 CI / pre-commit

**产物**：`scripts/check_handler_timeouts.py`（本次新增，可非零退出）

**状态**：脚本可运行、有单测（`tests/unit/test_handler_timeout_scanner.py`，
含反例），但**未**登记进 `.pre-commit-config.yaml` 或 CI 流水线。

**风险**：不接入自动化 ⇒ 新写的无超时 handler 仍可静默合入。
本任务已把**检测手段**做出来，但"每次提交都跑"需要改 CI 配置，
属仓库级流程变更。

**建议修法**：在 pre-commit 或 CI 的 lint 阶段加
`python scripts/check_handler_timeouts.py`（非 `--strict`，避免对既有
81 个仅靠外层兜底的 handler 一次性报红）。

---

## 6. 遗留：81 个 handler 仅依赖分发层全局上界

**实测**（`python scripts/check_handler_timeouts.py`）：
- 注册为能力的 handler 共 **90** 个；
- 自身实现超时（B1）**9** 个；schema 声明标量超时（B2）**0** 个；
- **仅靠 `agent/tools/__init__.py::call()` 的全局上界兜底（B3）81 个**。

**影响**：这 81 个能力在 `CP_TOOL_HANDLER_TIMEOUT_SEC=0`（关闭总闸）时
会一起退化为**可无限阻塞**。默认值 1800s 已启用，故当前是安全的；
但"安全来自一个开关"是脆弱的。`--strict` 模式专门用于把这批能力列出来。

**建议修法**：按模块分批给高频/高风险工具补自身上界（优先 `web_*`、
`code_tools` 的 `schedule_task`/异步任务族）。
**登记原因**：逐个改 81 个工具属于跨模块批量改造，不在子工作流 D 范围内。

---

## 7. 遗留：丢更新复现脚本未纳入例行测量

**产物**：`scripts/repro_race_lost_update.py`（本次新增，可重测同一指标）

**实测结论（见验收报告 §4）**：
- 归档报告 `security-tools/stress-report.json`（**注意：不在 `data/`**）
  记录 `lost_rate 0.063` / `lost_updates 121031` / `threads 64`；
- 该竞态**已在 `e6b71281`（2026-08-13）修复**（加 `_counts_lock`），
  晚于报告生成 11 天；
- 本任务复测：**有锁 = 0 丢更新**；无锁形态在 64 线程 × 30000 次迭代下
  实测丢率 **0.008**（与报告的 0.063 同量级但不同值 —— 该现象**负载相关**，
  16/32/64 线程 × 5000 次迭代下均为 0.0）。

**风险**：报告生成后从未被重跑（仓库中存在 14 份逐字节相同的副本，MD5 一致），
说明没有任何例行机制在盯这个指标。

**建议修法**：把 `python scripts/repro_race_lost_update.py --mode locked`
以**零丢更新为断言**接入 nightly（当前 0 丢更新是稳定的，可作硬断言）；
不要用"无锁形态必须丢更新"当断言（负载相关，会 flaky）。
