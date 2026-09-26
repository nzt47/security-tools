# Q8 并发审计：waitress 线程数 / 并发上限 / 与子进程 worker 的并发关系

> 审计对象：云枢（Yunshu）Windows 单机 AI Agent 系统
> 仓库根：`C:\Users\Administrator\agent`
> 审计方式：**只读**。全部数字来自实际读取文件、实际执行只读 PowerShell/pip 命令与历史日志实测，不采信注释/文档中的声称值。
> 审计时间：2026-09-25 18:08（本机时钟）
> 结论若无直接证据均显式标注 **【推测】**。

---

## 0. 环境与基线实测

| 项 | 实测值 | 采集方式 |
|---|---|---|
| CPU | 逻辑处理器 **12** / 物理核 **6**（`Win32_Processor`） | `Get-CimInstance Win32_Processor` |
| 内存 | **16,003,420,160 B = 14.90 GiB**（`Win32_ComputerSystem`） | `Get-CimInstance` |
| C 盘剩余 | **220,933,001,216 B ≈ 205.8 GiB**（已用 725.8 GiB） | `Get-PSDrive C` |
| 默认 Python | **3.12.0**，`C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe` | `python --version` |
| venv | `C:\Users\Administrator\agent\venv` **存在但无解释器**：`Test-Path venv\Scripts\python.exe` = **False**；全目录递归搜 `python.exe` = **0 个**（venv 下只有 `Lib`、`Scripts` 两个目录） | `Get-ChildItem -Recurse -Filter python.exe` |
| 主包规模 | `agent/` **622 个 .py / 253,717 行**（任务书称 25.4 万行，实测一致） | `Get-ChildItem -Recurse -Filter *.py` + 逐文件 `Measure-Object -Line` |
| tests/ | 890 个 .py | 同上 |

**关键包版本（取自默认 Python 3.12 的 site-packages，即 `python app_server.py` 实际使用的解释器）**

| 包 | 版本 |
|---|---|
| Flask | 3.1.3 |
| **waitress** | **3.0.2** |
| openai | 2.24.0 |
| sentence-transformers | 5.6.0 |
| torch | 2.13.0+cpu |
| numpy | 2.4.6 |
| **pytest** | 9.1.1 |
| chromadb | 1.5.9 |
| onnxruntime | 1.20.1 |
| httpx | 0.28.1 |
| psutil | 7.2.2 |

> ⚠️ **实测差异**：仓库内存在 `venv/` 目录，易被误认为虚拟环境入口；但它没有 `python.exe`（也不存在 `.venv`，`Test-Path .venv` = False）。`start_yunshu.bat:28` 调用的 `python` 走的是 **系统 Python 3.12**，不是 venv。任何"依赖装在 venv 里"的假设都不成立。

---

## 1. waitress 实际启动参数：默认值 vs 生效值

唯一启动点：**`app_server.py:1923-1926`**

```python
1923:    from waitress import serve
1924:    # threads 8→16: 高并发压测发现 LLM 长耗时请求占满线程导致排队（Task queue 高发），
1925:    # 提升线程容量缓解排队；LLM 外呼另有 60s 看门狗兜底（orchestrator._run_llm_bounded）
1926:    serve(app, host="127.0.0.1", port=5678, threads=16)
```

**只传了 4 个 kwarg，其余全部走 waitress 3.0.2 出厂默认**（默认值取自 `...\site-packages\waitress\adjustments.py`，逐行核对）：

| 参数 | 代码位置 | waitress 默认 | **实际生效值** | 是否可被 config/env 覆盖 |
|---|---|---|---|---|
| `threads` | `app_server.py:1926` | 4（`adjustments.py:149`） | **16**（硬编码字面量） | ❌ **否**。全仓 `*.py` grep `WAITRESS|waitress` 仅命中 `app_server.py:1923` 一处 `serve`；无 env、无 config.yaml、无 config.py 读取 |
| `connection_limit` | 未传 | **100**（`adjustments.py:231`） | **100** | ❌ 否（unset） |
| `channel_timeout` | 未传 | **120**（`adjustments.py:237`） | **120** | ❌ 否（unset） |
| `backlog` | 未传 | **1024**（`adjustments.py:199`） | **1024** | ❌ 否（unset） |
| `cleanup_interval` | 未传 | **30**（`adjustments.py:234`） | **30** | ❌ 否（unset） |
| `url_scheme` / `trusted_proxy` / `ident` | 未传 | `http` / `None` / `waitress`（`adjustments.py:183/152/190`） | 同默认 | ❌ 否 |

**语义实测（读 waitress 源码，非推测）**

- `connection_limit` 是**接受新连接**的闸门：`waitress/server.py:270-289`，`len(self._map) >= connection_limit` 时 `self.in_connection_overflow = True` 并停止监听新连接；此时新连接堆在 **OS backlog(1024)**（`server.py:259` `self.socket.listen(self.adj.backlog)`）。
- `channel_timeout` **不是**排队请求的超时：`server.py:342-351` 的 `maintenance()` 只关闭 `not channel.requests and channel.last_activity < cutoff` 的通道。而请求在下发任务**之前**就已入 `channel.requests`（`channel.py:229-236`：`self.requests.append(self.request)` → `self.server.add_task(self)`）⇒ **已完成解析但排在队列里等线程的请求不会被 channel_timeout 回收，服务端无排队超时**。120s 只对"连上但没发完整请求"的空闲连接生效。

**文档漂移（必须指出）**

- `gunicorn_config.py:10` 注释写 "`app_server.py:1618  serve(app, host="127.0.0.1", port=5678, threads=16)`" —— **实测该调用在 `app_server.py:1926`**，行号漂移 308 行。
- `gunicorn_config.py:42` `workers = min(cpu*2+1, 8)`、`worker_class="sync"`、`timeout=120` 是**多进程 gunicorn 模型，本机 Windows 单机部署完全不生效**（无任何入口 import 它；gunicorn 亦不兼容 Windows）。二者不可混谈。

---

## 2. 子进程 worker 清单

### 2.1 汇总表

| # | worker | 启动代码位置 | 进程数 | 生命周期 | 重启策略 | 协议 | 单次调用超时 |
|---|---|---|---|---|---|---|---|
| W1 | **Embedding（工具检索）** | `agent/tool_router_hybrid.py:991-992` `Popen([sys.executable, "-c", _WORKER_SCRIPT_EMBEDDING, model])` | **1**（单例，`_ensure_worker` 双检锁防双 Popen，见 `:983-990`） | 常驻，随父进程；`_cleanup_proc` `:1157-1173`：先写 `{"type":"exit"}`、wait 5s，再 `kill` | ✅ 有：`_maybe_restart_in_background` `:848-880`，**上限 3 次**（`:135 _WORKER_MAX_RESTARTS=3`），退避 5s→10s→20s，封顶 300s（`:136-137`）；用尽后永久 BM25-only | 行分隔 JSON over stdin/stdout（`{"type":"encode","texts":[...]}`） | ready **30s**（`:121 _WORKER_READY_TIMEOUT`）；encode **30s**（`:126 _WORKER_ENCODE_TIMEOUT`） |
| W2 | **Reranker（技能精排）** | `agent/tool_router_reranker.py:302-303` `Popen([sys.executable, "-c", _WORKER_SCRIPT, model, max_length])` | **1** | 常驻，随父进程；`_cleanup_proc` `:350-369`：先 `{"type":"exit"}`、wait 2s，再 `kill` | ❌ **无自动重启**：任何失败走 `_cleanup_proc()` 并置 `_init_failed=True` ⇒ 本进程内永久降级 | 行分隔 JSON over stdin/stdout | 启动 **60s**（`:59 _WORKER_STARTUP_TIMEOUT`）；单次 predict **30s**（`:66 _PREDICT_READ_TIMEOUT`） |
| W3 | **capregistry stdio（MCP）** | `agent/capregistry/loader.py:409-416`（`_JsonRpcProcess.__init__`），由 `StdioLoader._spawn` `:506-525` 调用 | **默认 0 个**；首次 invoke 时起 **1** 个（`:536-537`） | 常驻到 `StdioLoader.close()` `:584-592`；`_JsonRpcProcess.close` `:460-476`：关 stdin → `terminate()` wait 5s → `kill()` | ❌ 无重启；失败走基类 backoff（`:221` `reset_timeout=30.0, window_seconds=60.0, max_attempts=2`） | JSON-RPC 2.0 行分隔，MCP `initialize`（protocolVersion 2024-11-05）→ `tools/call` | **20s**（`CP_CAPABILITY_HTTP_TIMEOUT` 默认 "20"，`:1027`） |
| W4 | **Embedding 可用性探测** | `agent/tool_router_hybrid.py:355-361` `subprocess.run([...])` | 一次性短命进程 | 探测完即退 | 不适用 | 退出码 + stdout 含 `PROBE_OK` | **60s**（`:116 _PROBE_TIMEOUT`） |
| W5 | 子代理沙箱命令 | `agent/subagent/sandbox.py:585-596` | 每次调用 1 个短命进程 | 调用即起即退 | 不适用 | `communicate()` | **30s**（`SandboxLimits.timeout_s=30.0`，`:196`） |
| W6 | 技能脚本执行 | `agent/skills_mgmt/executor.py:148/207` | 每次调用短命进程 | 调用即起即退 | 不适用 | 由 executor 封装 | **30s**（`:129 _DEFAULT_TIMEOUT=30`） |

### 2.2 ⚠️ 任务书锚点纠错：`skills_mgmt/vector_adapter.py` **没有子进程**

任务书把 "`agent/skills_mgmt/vector_adapter.py`（Embedding worker 子进程）" 列为锚点。**实测该文件不含任何子进程代码**：

- `grep "subprocess|Popen|multiprocessing"` over `agent/skills_mgmt/vector_adapter.py` ⇒ **0 命中**。
- 它在**请求线程内**加载 BGE-m3：`vector_adapter.py:260` `SentenceTransformer(self.model_name, device="cpu")`，`_DEFAULT_MODEL = "BAAI/bge-m3"`（`:48`），1024 维，**in-process**。
- 真正的 embedding 子进程 worker 在 **`agent/tool_router_hybrid.py`（W1）**，与技能向量检索是两条独立链路。

### 2.3 子进程相关的两个**实测缺陷**

**(A) `StdioLoader` 的预热池对并发零收益**
`loader.py:529-532` 在 `prewarm>0` 时确实会起 N 个进程填 `self._pool`，但 `_do_invoke` **恒取 `self._pool[0]`**：

```python
534:    def _do_invoke(self, handle: Handle, args: Mapping[str, Any]) -> Any:
535:        with self._pool_lock:
536:            if not self._pool:
537:                self._pool.append(self._spawn())
538:            proc = self._pool[0]          # ← 永远只用第 0 个
539:        resp = proc.request("tools/call", ...)
```
⇒ 池大小 N 只消耗内存与句柄，不产生任何并行度。且 `prewarm` 默认 0（`loader.py:1023` `int(os.environ.get("CP_CAPABILITY_STDIO_PREWARM","0"))`），`.env` 与 `config.yaml` 中**均无** `CP_CAPABILITY_STDIO_PREWARM`/`CP_CAPABILITY_HTTP_TIMEOUT` 键（实测 grep 0 命中）⇒ **生效值 prewarm=0、timeout=20s**。

**(B) 强杀父进程会遗留孤儿子进程**
`server_port_guard.py:206-207` 是 `taskkill /F /PID <pid>`（**无 `/T`**，timeout=3）。W1/W2 的 Popen 未加入 Windows Job Object，也无 `atexit`/`SIGTERM` 兜底链路（`/F` = TerminateProcess，无信号投递 —— `server_port_guard.py:19` 自述）⇒ **每次重启都会遗留上一代的 embedding/reranker 子进程**。【推测】孤儿进程会继续持有 `~\AppData\Local\Programs\Python` 与模型内存，实测当前无 python 进程残留（见 §5），故该风险未在当前时刻兑现。

---

## 3. 并发关系图：一个 HTTP 请求在 waitress 线程里能阻塞多久

### 3.1 请求路径与逐段阻塞预算

主对话端点 `POST /api/chat` 是**同步 Flask 视图**（`agent/server_routes/routes_chat.py:258-260`），在 **waitress 线程内同步执行**，末尾 `routes_chat.py:345-349` 直接同步调用 `Yunshu.chat(...)`。单请求的阻塞段：

| 段 | 位置 | 名义上限 | **实测/代码实际上限** | 备注 |
|---|---|---|---|---|
| ① 安全检查 | `routes_chat.py:296` | 无显式上限 | in-process，快 | 不构成瓶颈 |
| ② 语义层技能召回 | `orchestrator.py:2374` `svc.loader.match(...)` → `vector_adapter.py:659` | 注释称 **2s**（`:717 _SEARCH_TIMEOUT_SECONDS = 2.0`） | ❌ **超时失效，实际无界**（见 §3.2） | 首次调用还会触发 `ensure_indexed()`（`:700-702`）在锁内加载 BGE-m3 |
| ③ 技能 Cross-Encoder 精排 | `skills_mgmt/reranker.py:741-756` | **3s**（`:140 _DEFAULT_RERANK_TIMEOUT = 3.0`） | **3s（真实生效）** | 该处**刻意**用 `ex.shutdown(wait=False)` 规避 with 块，与 §3.2 形成同仓内自相矛盾 |
| ④ 工具混合检索 encode | `tool_router_hybrid.py:1184` 持锁 → `:1200` `_encode_via_worker` | **30s**（W1 `_WORKER_ENCODE_TIMEOUT`） | **30s + 全局锁串行** | 首启还会触发 W4 探测子进程（≤60s） |
| ⑤ **LLM 外呼** | `orchestrator.py:3999-4008` `_run_llm_bounded` | **60s**（`:177 _LLM_CALL_TIMEOUT = int(os.getenv("LLM_CALL_TIMEOUT","60"))`） | **请求线程 60s 返回**；但底层线程 **最长 ~1800s** 不回收（见 §3.3） | `LLM_CALL_TIMEOUT` **未在 `.env` 中设置**（实测 grep 无此键）⇒ 生效默认 **60s** |

**单请求最坏阻塞（同步等待链，最坏情形）**
```
60s(首次 embedding 探测, W4, _PROBE_LOCK 串行)
+ 无界(②技能向量检索, 首次含 BGE-m3 4.35GB 载入)
+ 3s (③精排)
+ 30s(④encode, 且被 EmbeddingIndex._lock 全局串行)
+ 60s(⑤LLM 看门狗上限)
────────────────────────────────────────
理论最坏 ≈ 153s+（不含 ② 的无界部分）
稳态典型 ≈ 0.1s(②) + 0.02s(④缓存命中) + 1~60s(⑤) ≈ 1~60s
```

### 3.2 实证缺陷：`vector_adapter` 的 2s 超时被 `with` 块抵消

```python
736:        t0 = _time.time()
737:        try:
738:            with ThreadPoolExecutor(max_workers=1) as executor:      # ← 退出时 shutdown(wait=True)
739:                future = executor.submit(self._search_impl, ...)
742:                results = future.result(timeout=self._SEARCH_TIMEOUT_SECONDS)   # 2.0s
```
`future.result(timeout=2.0)` 抛 `FutureTimeout` 后，异常穿过 `with` ⇒ `ThreadPoolExecutor.__exit__` 调 `shutdown(wait=True)` ⇒ **阻塞等待那个 2s 都没跑完的 `_search_impl` 线程真正结束**。因此 `_SEARCH_TIMEOUT_SECONDS=2.0` **在语义上不成立**，`search()` 的阻塞时长等于 `_search_impl` 的实际耗时（首次含 BGE-m3 加载）。文档串 `:731` 甚至自述"线程池超时后工作线程继续跑但结果被丢弃"，与代码行为相反。

**同仓反证**：`agent/skills_mgmt/reranker.py:735-736` 明确写着
> `# 【变易】不用 with 块：with 退出会 shutdown(wait=True) 阻塞等待后台线程`
> `#         手动管理 + shutdown(wait=False) 让超时后立即返回`

即该仓库**已经知道**这个陷阱并在 reranker 修正，但 `vector_adapter.py:738` 未同步修正。

### 3.3 LLM 看门狗：请求线程有界，但底层线程无界

```python
175: _LLM_CALL_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="llm_call_watchdog")
177: _LLM_CALL_TIMEOUT = int(os.getenv("LLM_CALL_TIMEOUT", "60"))
...
4006:        _t = timeout or _LLM_CALL_TIMEOUT
4007:        _fut = _LLM_CALL_POOL.submit(fn)
4008:        return _fut.result(timeout=_t)      # 不做 cancel、不杀线程
```
- 池是 **模块级全局单例，`max_workers=16`**（`:175-176`），与 waitress `threads=16` **恰好 1:1**。
- LLM HTTP 客户端：`agent/model_router/adapters.py:93-99`，`OpenAI(**kwargs)` **只传 `api_key` 与 `base_url`，未传 `timeout`、未传 `max_retries`** ⇒ 生效 openai 2.24.0 出厂默认（实测）：`DEFAULT_TIMEOUT = Timeout(connect=5.0, read=600, write=600, pool=600)`、`DEFAULT_MAX_RETRIES = 2`。
- ⇒ 单次 `_run_llm_bounded` 超时后，被放弃的 worker 线程要等 openai 自己返回，**最坏 600s 读超时 × (1+2 次重试) ≈ 1800s** 才释放槽位。
- ⇒ **实测推论**：连续 16 个慢 LLM 请求即可让 16 个 pool worker 全部被僵尸调用占住；此后所有请求的 `_fut.result(60)` 必然在 60s 后超时，`orchestrator.py:4190-4195` 统一返回"（LLM 响应超时）"文案，系统进入**长达 10~30 分钟的"全员超时"态**。

### 3.4 最坏并发上限

| 层次 | 上限 | 证据 |
|---|---|---|
| 可被并发服务的 HTTP 请求 | **16** | `app_server.py:1926 threads=16` |
| 同时打开的连接 | **100**（超出后停止 accept，堆 OS backlog=1024） | waitress 默认 `adjustments.py:231`、`server.py:270-289` |
| 同时执行的 LLM 外呼 | **16**（全局池） | `orchestrator.py:175` |
| 同时执行的 embedding encode | **1**（单 worker + `EmbeddingIndex._lock` 全串行） | `tool_router_hybrid.py:1184/1200`、`:991` |
| 同时执行的技能向量检索 | **1**（`vector_adapter._lock`，`vector_adapter.py:122`） | `vector_adapter.py:700-702` |
| 全局并发信号量 | **无** | 见 §4 |
| **无排队超时** | 第 17~100 个请求在服务端**无限期排队**，只能靠客户端自身超时 | `server.py:342-351` + `channel.py:229-236` |

---

## 4. 限流 / 并发上限实现实况

| 问 | 实测结论 | 证据 |
|---|---|---|
| HTTP 层有全局并发闸门吗？ | ❌ **没有**。`app_server.py` 全文 grep `rate_limiter|RateLimiter|get_rate_limiter|max_concurrent` = **0 命中** | `app_server.py` |
| 有每技能限流吗？ | ❌ 无。`agent/skills_mgmt/` 下无任何限流器；技能调用不落限流 | `agent/skills_mgmt/` |
| 有队列吗？ | 仅有 waitress 内部**无超时**的任务队列（§3.4）；应用层无显式队列 | 同上 |
| 唯一落地的限流点 | ✅ **工具调用层**：`agent/tools/__init__.py:23` `_rate_limiter = _RateLimiter()`，`:411` `if not _rate_limiter.check(name): return {"ok":False,"error":"调用频率过高…","retry_after":…}` | `:22-23, :410-413` |
| 该限流器的实际参数 | 构造无参 ⇒ `_old_api=False`，但 `check(name)` 只有 `tool_name` ⇒ 走 `_check_old` **按类别的令牌桶**：`default=(10, 1.0)`、`network=(5, 0.5)`、`shell=(2, 0.2)`、`file=(15, 1.0)`（=容量, 每秒补充） | `rate_limiter.py:278-283`（取值）、`:314-315`（分派）、`:402-427`（桶实现，非 `TokenBucket` 类，而是 `self._buckets[category] = {"tokens":…}` 内联字典） |
| 类里的 `max_concurrent=100` 并发闸门生效吗？ | ❌ **形同虚设**。`_check_multi_level`/`_acquire_concurrent`（`:374-391`）只在传 `endpoint`/`user_id` 时被调用；`release()`（`:393-398`）**全仓无调用者**（grep `rate_limiter.release` ⇒ 唯一命中是 `agent/self_healing/watchdog_singleton.py:247` 的另一个对象）⇒ 一旦启用必然只增不减、永久堵死 | `rate_limiter.py:319-398` |
| 另有独立限流器？ | `agent/modules_api.py:73` `RateLimiter(max_concurrent=30, strategy=REJECT)` —— 属"模块综合 API"，**不在对话主链路** | `modules_api.py:73` |
| 幂等/竞态保护 | `agent/tool_gate.py:576` `check_tool_call` 集中式闸门（fail-open，异常即放行），非限流语义 | `tools/__init__.py:402-408` |

### 4.1 参数初值建议

| 参数 | 现值 | 建议初值 | 理由（基于本文实测） |
|---|---|---|---|
| waitress `threads` | 16（硬编码）`app_server.py:1926` | **维持 16**，改为从 `config.yaml` 读 `server.threads` | 上位约束是全局 LLM 池 16；单方面调大只会把压力搬到池队列 |
| `_LLM_CALL_POOL.max_workers` | 16 `orchestrator.py:175` | **与 threads 解耦**：≥ threads 的 1.5 倍（24），或改为按请求 `cancel()` + 短超时 | 现为 1:1，任一僵尸调用即等价损失一个 waitress 线程 |
| openai 客户端 `timeout` | **未设**（默认 read=600s）`adapters.py:99` | **`timeout=Timeout(connect=5, read=45)`**、`max_retries=1` | 应用级看门狗 60s 与 600s 读超时差 10 倍，僵尸线程寿命被放大 |
| `LLM_CALL_TIMEOUT` | 60（`.env` 未设）`orchestrator.py:177` | 写进 `.env` 显式 = **45** | 与 read=45 对齐，避免"看门狗先行、socket 后到"的双重等待 |
| HTTP 入口全局并发信号量 | **缺失** | **新增 `BoundedSemaphore(12)`** 包住 `/api/chat`，超限立即 `429 + Retry-After` | 12 < 16 留出余量给 `/api/health` 等探针，避免聊天打满全部线程 |
| `vector_adapter._SEARCH_TIMEOUT_SECONDS` | 2.0（**实际失效**）`vector_adapter.py:717` | **先修 `with` 陷阱**（改 `ex.shutdown(wait=False)`，照抄 `reranker.py:735-756`），保持 2.0 | 不修代码，调数值无意义 |
| `_WORKER_ENCODE_TIMEOUT` | 30s `tool_router_hybrid.py:126` | **8s** | 注释自述实测 query 编码 10-20ms；30s 是"死锁保护"而非容量，过长会把锁等待放大 30s×N |
| `_WORKER_MAX_RESTARTS` | 3 `tool_router_hybrid.py:135` | 保持 3（合理） | 该值有明确实测依据（`:129-132` 记录 2026-09-19 崩溃风暴） |
| `CP_CAPABILITY_HTTP_TIMEOUT` | 20s（env 默认）`loader.py:1027` | 保持 20s，但**显式写进 `.env`** | 目前完全依赖代码默认，配置面不可见 |
| `connection_limit` | 100（waitress 默认） | **显式 = 32** | 100 个连接 × 无排队超时 = 最多 84 个请求静默悬挂 |
| `channel_timeout` | 120（waitress 默认） | 保持 120 | 只对空闲连接生效，与请求排队无关（§1 已实测语义） |

---

## 5. 实测运行时状况

### 5.1 当前端口与进程

| 检查 | 实测结果 |
|---|---|
| `netstat -ano | Select-String ':5678'` | **无任何输出 ⇒ 5678 无监听、无连接** |
| `netstat -ano | Select-String ':3080'` | `127.0.0.1:3080 LISTENING 10952` + 3 条 ESTABLISHED（对端 pid 8092） |
| `Get-Process python` | **空 ⇒ 无任何 Python 进程**（无残留孤儿 worker） |

⇒ 与任务书"只有 node 在 3080"一致，且**确认没有遗留的子进程 worker**。

### 5.2 日志里的最后一次启动

最新一次真实后端启动 = **2026-09-22 22:45**：

| 事实 | 值 | 证据 |
|---|---|---|
| 启动证据文件 | `logs/backend_20260922_224531.err.log`（文件名内嵌启动时刻 22:45:31） | 文件列表 |
| 首条带时间戳日志 | `22:45:34` | 文件第 3 行起 |
| **waitress 开始监听** | **`22:46:29 [INFO] waitress : Serving on http://127.0.0.1:5678`**（该文件第 **1308** 行） | `Select-String` |
| ⇒ **冷启动耗时** | **58s**（22:45:31→22:46:29）／首条日志算 55s | 同上 |
| 末条带时间戳日志 | `23:27:23`（共 882 条） | `Select-String '^\d\d:\d\d:\d\d'` |
| 存活时长 | **≈ 42 分钟**后停止 | 同上 |
| 退出原因 | 尾部 20 行为常规健康探针（`probe.l3_llm_tool.failed` / `probe.l4_business.failed` / `probe.l5_semantic.completed`），**无 traceback** ⇒ 非崩溃退出【推测：人工关闭或外部 kill】 | `Get-Content -Tail 25` |

**冷启动耗时 4 次独立实测（含今日视角的最新一批）**

| 日志 | 首条时间戳 | `Serving on` 所在行 | 耗时 |
|---|---|---|---|
| `logs/_run13.log` | 23:10:28 | 1247（23:11:48） | **80s** |
| `logs/_run12.log` | 18:04:03 | 1247（18:05:28） | **85s** |
| `logs/_run11.log` | 13:54:22 | 1247（13:55:35） | **73s** |
| `logs/_run10.log` | 13:23:49 | 1246（13:25:08） | **79s** |
| `logs/backend_20260922_224531.err.log` | 22:45:34 | 1308（22:46:29） | **55s** |

⇒ **实测冷启动 55~85s，均值 ≈ 74s**，与 `start_yunshu.bat:27` 注释 "cold start takes about 60-90s" 一致（该注释本次**被证实**）。

### 5.3 崩溃循环 / 重启风暴证据

| 日期 | 重启类日志数 | 明细 |
|---|---|---|
| 2026-09-18 | 4 | `app_server_restart_20260918_231013/233117*.log` 等 |
| **2026-09-19** | **≥ 11** | 07:43 / 07:48 / 07:58 / 06:56 / 23:11 / 23:19 / 23:22 / 23:35 / 23:39 / 23:42 / 23:52 / 23:54 / 23:55（`server_restart_*` 8 个 + `app_server_restart_*` 5 个） |
| **2026-09-22** | **9** | `backend_detached_20260922_{075014,080405,081201,121744,123524,125213,131547}.err.log` 7 个 + `backend_20260922_{195523,224531}.err.log` 2 个 ⇒ 07:50→13:15 的 **5.5 小时内 7 次重启** |

- 该现象与代码注释自述吻合：`tool_router_hybrid.py:131` "`崩溃-重启风暴(2026-09-19 实测一轮 9 次服务重启)`" —— **本次实测独立复现了该现象**（9/19 至少 11 个重启日志）。
- `logs/errors.log` 尾部内容为 **pytest 测试用例的异常**（`tests/unit/test_monitoring_decorators.py:401` `raise ValueError("retry error")`），不是生产崩溃；该文件按天分组的正则 (`^\d{4}-\d{2}-\d{2}`) **0 命中**，说明其格式不含行首日期，不能用于时间线统计。
- `data/logs/2026-09-25.jsonl`（今日，10:38 写入）**仅 12 条** `agent-config / change` 记录（`resource_monitor.history_size: 1440 ↔ 321` 反复翻转），**不含任何 app_server 启动记录** ⇒ 进一步确认今天后端未运行。

### 5.4 taskkill 逻辑实测确认

| 事实 | 位置 |
|---|---|
| `app_server.py` 启动早期调用端口清理 | `app_server.py:1843-1847` `from agent.server_port_guard import cleanup_port_listeners; cleanup_port_listeners(5678)`（`except` 兜底不阻断启动） |
| 实际 kill 命令 | `agent/server_port_guard.py:206-207` `run(["taskkill","/F","/PID",pid], capture_output=True, timeout=3)`（win32 分支；非 win32 走 `os.kill(pid, SIGTERM)` `:210`） |
| 无 `/T` | 仅 `/F /PID`，**不杀进程树** |
| 副作用自述 | `app_server.py:1840-1841`：`/F == TerminateProcess` ⇒ `_install_graceful_shutdown_hooks()`（`:1916`）在这条路径上**不可能执行** |
| 留痕先于 kill | `server_port_guard.py:11`、`_build_record` `:123-158`（记 actor/pid/ppid/argv/target/kill_cmd） |
| `start_yunshu.bat` 的规避 | `:21-28`：先 `curl -s -f --max-time 2 http://127.0.0.1:5678/api/health`，健康则 `goto backend_up` 跳过启动，避免双启互相 kill |

### 5.5 「冷启动 60–90s」期间的行为评估

| 时段 | 客户端可见行为 | 证据 |
|---|---|---|
| T+0 ~ T+55..85s（`app_server.py:1782` 起执行到 `:1926 serve()` 之间） | **端口未监听** ⇒ `curl`/`Invoke-WebRequest` 得到 **连接被拒（ECONNREFUSED）**，HTTP 404/503 都不会出现。Electron 前端此时的一切 API 调用**直接失败**，而不是排队 | `serve()` 在 `:1926`，此前所有 import/插件注册/健康采集/端口清理都在同步执行 |
| 同上 | `start_yunshu.bat:41-51` 每轮 `ping -n 3`(≈2s) + `curl --max-time 2` 轮询，**最多 60 轮**（`:43 if %_tries% gtr 60`）⇒ 最长等待 ≈ 120s，超时打 `[WARN] Backend not ready after ~120s`（`:44`） | `start_yunshu.bat:39-51` |
| 启动期间是否会触发 taskkill | **会**：`:1845` 清理 5678 在 `serve()` **之前**执行 ⇒ 若启动中途失败，**旧实例已被杀掉且新实例未上线**，出现零服务窗口 | `app_server.py:1843-1847` 早于 `:1926` |
| 首次业务请求额外冷延迟 | ① W4 embedding 探测子进程（`data/.embedding_probe` 写于 **2026-07-23**，TTL=`7*24*3600` 已过期 ⇒ **下次启动必然重新探测**，`tool_router_hybrid.py:153/315-320`），最坏 **60s**，且由 `_PROBE_LOCK`（`:281/392`）**全局串行**；② `vector_adapter.ensure_indexed()` 首次在锁内加载 BGE-m3（本机缓存 `~/.cache/huggingface/hub/models--BAAI--bge-m3` 实测 **4,352.9 MB** 已存在） | `tool_router_hybrid.py:355-361, 379-426`；`vector_adapter.py:700-702`；HF 缓存实测 |

⇒ **冷启动窗口内不存在"排队等线程"问题（服务根本没起），存在的是"硬拒绝 + 旧实例已被杀"的组合风险。**

---

## 6. 结论清单（按优先级）

| # | 结论 | 严重度 | 证据 |
|---|---|---|---|
| 1 | waitress `threads=16` **硬编码**，`connection_limit/channel_timeout/backlog` 全走默认，**四者均无 config/env 覆盖路径** | 中 | `app_server.py:1926`；waitress 默认见 `adjustments.py:149/199/231/237` |
| 2 | 全局 LLM 池 `max_workers=16` 与 waitress `threads=16` **1:1**，且超时后**不 cancel**；openai 客户端 read 超时 **600s × 3 次尝试**未设 | **高** | `orchestrator.py:175-177, 4006-4008`；`model_router/adapters.py:93-99`；openai 2.24.0 `DEFAULT_TIMEOUT/DEFAULT_MAX_RETRIES` 实测 |
| 3 | `vector_adapter` 的 2s 超时被 `with ThreadPoolExecutor` 的 `shutdown(wait=True)` **实际抵消**，同仓 `reranker.py:735-736` 已修而同文件未修 | **高** | `vector_adapter.py:717, 736-748` vs `reranker.py:735-756` |
| 4 | 无任何 HTTP 层全局并发闸门；唯一限流器只管工具调用，其 `max_concurrent=100` 分支因 `release()` 无调用者而**不可启用** | **高** | `app_server.py`（0 命中）；`tools/__init__.py:23,411`；`rate_limiter.py:278-283,374-398` |
| 5 | 服务端**无请求排队超时**：17~100 号请求在 `connection_limit` 内无限期排队，只靠客户端超时 | 中 | `server.py:270-289, 342-351` + `channel.py:229-236` |
| 6 | 单个 embedding 子进程 + 全串行锁（`EmbeddingIndex._lock`）⇒ 16 并发下的 encode 串行放大，最坏 16×30s | 中 | `tool_router_hybrid.py:991-992, 1184, 1200, 126` |
| 7 | `StdioLoader` 预热池恒取 `_pool[0]`，`prewarm>0` 对并发无收益；且默认 prewarm=0、timeout=20 全部靠代码默认，配置面不可见 | 中 | `loader.py:529-538, 1023, 1027` |
| 8 | `taskkill /F /PID`（无 `/T`）不清理子进程树，且 `/F` 使优雅落盘钩子失效；实测当前无孤儿残留 | 中 | `server_port_guard.py:206-207`；`app_server.py:1840-1841, 1916, 1843-1847` |
| 9 | **实测冷启动 55~85s（均值≈74s）**，5 次独立取证；冷启动窗口内是**连接被拒**而非排队；端口清理发生在 `serve()` 之前会制造零服务窗口 | 中 | `_run10/11/12/13.log`、`backend_20260922_224531.err.log:1308`；`start_yunshu.bat:21-28, 41-51` |
| 10 | 重启风暴**实测复现**：9/19 ≥11 次、9/22 5.5 小时 7 次；与 `tool_router_hybrid.py:131` 注释自述一致 | 中 | `logs/server_restart_*`、`logs/backend_detached_*`、`logs/app_server_restart_*` |
| 11 | **文档漂移**：`gunicorn_config.py:10` 引用 `app_server.py:1618`（实际 1926）；`app_server.py:1168` 引用 `tool_router_hybrid.py:86`（实际常量在 `:135`） | 低 | 两处逐行核对 |
| 12 | **任务书锚点纠错**：`agent/skills_mgmt/vector_adapter.py` **无任何子进程**（`subprocess/Popen/multiprocessing` grep 0 命中）；真正的 embedding worker 在 `tool_router_hybrid.py:991` | — | grep 实测 |
| 13 | 机器规格：12 逻辑核 / 6 物理核 / 14.90 GiB 内存 / C 盘剩余 205.8 GiB / Python 3.12.0；仓库 `venv/` 目录**无 python.exe** | — | §0 实测 |

### 6.1 主要风险情景（一句话）

> 16 个 waitress 线程 + 16 个不回收的 LLM 线程槽 + 600s 的 socket 读超时 + 0 个准入闸门 ⇒ **一次 LLM 服务端抖动即可把整机 16 个线程全部钉死 10~30 分钟**，期间所有 `/api/chat` 统一返回"（LLM 响应超时）"（`orchestrator.py:4190-4195`），且第 17~100 个连接会在服务端无声排队直到客户端自己放弃。

---

## 附：本次审计使用的只读取证命令（可复现）

```powershell
# waitress 默认值
python -c "import waitress.adjustments as a,os;print(os.path.dirname(a.__file__))"
Select-String -Path "<site-packages>\waitress\adjustments.py" -Pattern '^\s+(threads|connection_limit|channel_timeout|backlog|cleanup_interval)\s*='
# 冷启动耗时
Select-String -Path logs\_run12.log -Pattern 'waitress\s+: Serving on'
# 当前端口 / 进程
netstat -ano | Select-String ':5678|:3080' ; Get-Process python
# openai 默认超时
python -c "import openai._constants as c;print(c.DEFAULT_TIMEOUT,c.DEFAULT_MAX_RETRIES)"
# 包版本
python -m pip list --format=freeze
```

**未执行**（遵循只读约束）：未启动任何服务、未运行 pytest 全量、未 git commit、未修改除本报告外的任何文件。`.env` 仅以正则提取**键名**，全程未输出任何值。
