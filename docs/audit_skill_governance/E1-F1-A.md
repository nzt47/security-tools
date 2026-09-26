# E1-F1-A 修复卡报告

- **卡号**：E1-F1-A（修复卡）　**基线 HEAD**：`5c9ace10a4ca4bb96860db3a48debf9ddcf496bf`
- **上游定位卡**：E1-F1（`docs/audit_skill_governance/E1-F1.md`）
- **日期**：2026-09-26　**环境**：Windows / Python 3.12.0 系统解释器 / 6 卡并发
- **文件范围（硬约束内）**：`agent/tool_router_hybrid.py`、`agent/settings/registry.py`（仅登记）、
  `tests/unit/test_tool_router_hybrid_e1f1a.py`（新增）、`tests/unit/test_embedding_worker_crash_visibility.py`（**1 条断言改写**）、本报告
- **未做**：`git commit` / `git add` / 整文件 `git checkout`（回滚口径见 §12）

---

## 0. 一句话结论

**向量腿在本机真的起来了**：走生产入口、不设任何 env、用代码默认 120 s 超时，
`_ensure_worker()` 返回 **True @ 18.60 s**，`mode=hybrid`、`available=true`、
`cache_probe=hit_namespaced`；真实查询 `embed_candidates=38 / 18`。
**路由度量**：`bm25_only` 决策层 **27/50** vs `hybrid` **25/50**（**hybrid 更差**，如实报），
但下发层 40/50 → **46/50**、τ 通道 F1 0.794 → **0.902**（假阳 11 → 3）。
**优先级 1** 的「未就绪即 True」**已复现（改前 3/3，改后 0/3）**；
「查询↔向量错位」**未能端到端复现**，原因与试过什么写在 §2.4。
**返工 ①**：我第一次的缓存优先实现是**死分支**（裸模型名命中不了缓存）—— 已修，证据见 §3。

---

## 1. 交付物

| # | 交付物 | 位置 |
|---|---|---|
| 1 | 优先级 1 修复 + 差分证据 | §2 |
| 2 | 缓存优先（含 repo id 解析）+ 命中/未命中两条路实测 | §3 |
| 3 | 生产入口 mode/available/embed_candidates 前后对照 | §3.3 / §7 |
| 4 | `eval_route_conflict.py` bm25 vs hybrid 并列数字 | §8 |
| 5 | 孤儿清理前后 pid 证据 | §6 |
| 6 | 新 env 登记 | §10 |
| 7 | 未验证项与残留风险 | §11 |
| 8 | 回滚指令 | §12 |
| 9 | 进程与残留物自证 | §13 |

---

## 2. 优先级 1：「未就绪即 True」→ 差分证据

### 2.1 被检不变量

> **只有真的读到过 `ready` 那一行，才允许认为 worker 就绪；未就绪时任何线程都不许往管道写 encode。**

旧实现的判据是 `tool_router_hybrid.py:987-988`（HEAD）：

```python
if self._proc is not None and self._proc.poll() is None:
    return True          # ← 只回答"进程还活着吗"，不回答"准备好了吗"
```

### 2.2 "改前"是怎么来的（不是我自己拼的近似）

用 `git show HEAD:agent/tool_router_hybrid.py` 导出**基线提交**的模块到仓库外
（`%TEMP%\e1f1a\trh_before.py`），并**逐字核对**它与本卡开始前工作区里那两行完全一致
（HEAD `:987-988` = 上面那段）。因此 "before" 是**真基线代码**，不是我手写的复刻。

探针 `%TEMP%\e1f1a\probe_p1_diff.py`，**全部走生产方法**（`add_document` / `preheat` /
`_ensure_worker` / `search` / `worker_health`），只把 `_WORKER_SCRIPT_EMBEDDING` 换成一个
"加载 6 s 才 ready、并按文本首字母回可区分向量"的假 worker（`[1,0]` 给 `A*`，`[0,1]` 给其它），
这样"某次查询读到的是谁的响应"可以从**返回的 top1 doc 名**直接看出来。场景：

1. 正常启动一次 ⇒ 向量已存在（`_embeddings` 非空）；
2. `idx._proc.kill()`（模拟 worker 进程消失 = 模块设计要扛的崩溃场景）；
3. 线程 A 走 `_ensure_worker()` 重新拉起（新 worker 要 6 s 才 ready）；
4. **加载窗口内（t≈1.5 s）主线程再问一次 `_ensure_worker()`** ← 被检对象；
5. 立刻走请求路径 `search()`；
6. 等 A 收尾后再 `search()` 一次，看它读到谁的响应。

各跑 3 轮。

### 2.3 原始输出（改前 vs 改后）

```
=== SUMMARY ===
before(HEAD)     ensure_worker_while_loading=[True, True, True]
                 misaligned=[False, False, False]
                 search_during_load=[[['dA',1.0],['dB',0.0]], [['dA',1.0],['dB',0.0]], [['dA',1.0],['dB',0.0]]]
                 health_while_loading(mode/available/ready)=[('hybrid', True, None), ('hybrid', True, None), ('hybrid', True, None)]
after(worktree)  ensure_worker_while_loading=[False, False, False]
                 misaligned=[False, False, False]
                 search_during_load=[[], [], []]
                 health_while_loading(mode/available/ready)=[('hybrid', True, False), ('hybrid', True, False), ('hybrid', True, False)]
```

改后那三次调用留下的一行日志（说明"为什么这次查询没有向量腿"）：

```
{'action': 'embedding.worker.startup_in_progress', 'degrade_to': 'bm25_only',
 'note': '另一线程正在拉起/等待 worker：本次查询不碰管道，直接走 BM25'}
```

**读法**：

| 观测 | 改前 | 改后 | 判定 |
|---|---|---|---|
| 加载窗口内 `_ensure_worker()` | **True ×3** | **False ×3** | 缺陷已复现、已消除 |
| 窗口内那次 `search()` 是否写了管道 | **写了**（返回向量 `dA 1.0`，说明它把 encode 发进了一个"还没 ready"的 worker 并读回了响应） | **没写**（返回 `[]`） | 危险动作已消除 |
| `worker_health()["mode"]` 在该窗口 | `hybrid`（`worker_ready` 字段都不存在） | `worker_ready=False`，且 `available` 判据已纳入就绪（见 §7） | 撒谎面收窄 |

### 2.4 ★「查询↔向量错位」：**未能端到端复现**（如实报，附我试过什么）

我**没有**构造出可复现的错位（`misaligned=[False,False,False]`，改前改后都是 False）。
不是"没试"，而是试完之后我**找到了结构性的原因**：

1. **`search()` 在写管道之前有一道短路**：`with self._lock: if self._embeddings is None or
   len(self._doc_ids)==0: return []`。首次启动窗口里 `_embeddings` 就是 None ⇒ 请求线程
   **在写任何字节之前**就返回了。所以"首次启动 ⇒ 查询偷走 ready 行"这条路**走不通**。
2. **协议读端是 `io.BufferedReader`，它自带锁，先到者得**：握手的 `readline` 线程在 Popen 之后
   立刻阻塞（= 先到），查询的 `readline` 只能排在它后面 ⇒ 即使查询确实发了 encode，
   **ready 行仍然被握手线程读走**，查询读到的是它自己的响应（探针 3/3 都是这个结果：
   窗口内那次 `search` 竟然得到了**正确**的 `dA`）。
3. 我试过/考虑过的其它构造：让编码线程先于握手线程进入 `read()`（被 2 挡住）；
   在**首次启动**窗口构造（被 1 挡住）；在**重启**窗口构造（被 `_restarting` 守卫挡住：
   重启期间外部调用一律 False）。

⇒ **结论**：错位**不是**"首次启动窗口必然发生"，而是需要额外前提（查询写管道时
`_embeddings` 恰好非空、且它是 stdin/stdout 上的**第一个**读者）。
我在静态分析里找到过一条能凑齐前提的生产路径：`_restart_worker` 里
"**`_init_failed=True` 但 `_proc` 仍然活着**"时会**立即** `return True`（此时 `_embeddings` 是旧
worker 留下的、非空），随后 `_restarting` 已复位 ⇒ 请求线程可以写 —— 但要用生产事件触发
"`_init_failed=True` 且 `_proc` 活着"需要 `stdout_read_failed`（OSError）一类路径，
**我没能在不改内部的前提下手动触发它**，故不把它写成"已复现"。

**修复对这条残留路径也有效**：新实现里"就绪"由 `_worker_ready` 单一决定，
`_restart_worker` 那条早退路径返回 True 的前提（`_worker_ready` 已置位）在新代码里不可能与
"新 worker 还没 ready"同时成立（`_cleanup_proc` / `_init_failed` / `search` 三处都会清位）。

### 2.5 改了什么

| 机制 | 位置 | 作用 |
|---|---|---|
| `self._worker_ready = threading.Event()` | `EmbeddingIndex.__init__` | **就绪的唯一判据**；只在解析到 `ready` 那一行时置位 |
| `self._startup_lock = threading.Lock()` | 同上 | 握手（Popen + 等 ready + 编码 pending）**串行化**；请求路径用**非阻塞** acquire（拿不到就本次降级，不排队、不碰管道） |
| `_handshake_locked()` | 新方法 | 唯一允许"读 stdout 直到 ready"的地方，与置位同一临界区 |
| "活着但从未 ready" ⇒ 回收重来 | `_ensure_worker` | `embedding.worker.unready_reclaim`：不再乐观返回 True |
| `_encode_pending_locked()` 自持 `_lock` | 编码路径 | 原 docstring 写"调用方持锁"**是假的**（握手线程并不持锁）⇒ pending 编码与 query 编码会**同时**读写同一根管道。现在两个编码者互斥（RLock 可重入） |
| `search()` 取锁后**再判一次**就绪 | 请求路径 | 防御纵深：`_ensure_worker` 返回到写管道之间存在窗口 |
| `_init_failed=True` / `_cleanup_proc()` ⇒ 清 `_worker_ready` | 两处 | 杜绝"已判死但仍显示就绪"的组合态 |

---

## 3. 返工 ①：缓存优先 —— 模型名 → **能命中缓存的 repo id**

### 3.1 我第一次的实现为什么是死分支（自证）

第一版写的是 `snapshot_download(repo_id=model_name, local_files_only=True)`（只查一次原始名）。
而生产默认模型名是**裸名** `paraphrase-multilingual-MiniLM-L12-v2`（`_DEFAULT_MODEL`），
**`snapshot_download` 不会像 `SentenceTransformer` 那样替你补 `sentence-transformers/` 命名空间**：

```
FAIL paraphrase-multilingual-MiniLM-L12-v2                        0.00s -> LocalEntryNotFoundError
OK   sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2  0.00s -> C:\Users\Administrator\.cache\huggingface\hub\models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2\snapshots\e8f8c211226b894fcb81acc59f3b34ba3efd5f42
```

⇒ 那个 `local_files_only` 分支**每次都抛**、每次都静默回退在线 ⇒ 主审计实测的
`ready_timeout @120.0s` 就是这个原因。**我的第一次"验证"犯的错与 E1 卡同类**：
我拿一个**不经过生产入口**的样本（自己手写了带命名空间的名字）当成了证据。
这条我如实登记，也是返工的直接原因。

### 3.2 修法（`_cache_candidates` + `_load_model`，均在 worker 脚本内）

```python
def _cache_candidates(model_name):
    name = str(model_name or '').strip()
    if not name:   return []
    if '/' in name: return [(name, 'hit_namespaced')]              # 已是 repo id
    return [('sentence-transformers/' + name, 'hit_namespaced'),   # 先试命名空间
            (name, 'hit_bare')]                                    # 再试裸名
```

- 逐个候选试 `snapshot_download(local_files_only=True)`，**先命中先用**；
- 命中后 `SentenceTransformer(快照目录)`；该目录加载失败 ⇒ `load_failed`，**继续下一个候选**；
- 全部候选耗尽（或没装 `huggingface_hub`）⇒ **回退在线**（用**原始模型名**）；
- `cache_probe` 四态 `hit_namespaced / hit_bare / load_failed / miss`，随 `ready` 消息回父进程，
  父进程再透出到 `worker_health()["load_cache_probe"]` ⇒ **"快是因为缓存命中，还是网络恰好通"永远可区分**。

### 3.3 验收（生产入口，**不设任何 env、不抬超时**，代码默认 120 s）

```
env AGENT_HYBRID_WORKER_READY_TIMEOUT = None
env AGENT_HYBRID_EMBEDDING          = None
env HF_ENDPOINT / HF_HUB_OFFLINE    = None / None
_DEFAULT_MODEL                      = paraphrase-multilingual-MiniLM-L12-v2
_WORKER_READY_TIMEOUT (模块级)       = 120.0
EmbeddingIndex._WORKER_STARTUP_TIMEOUT = 120.0

=== get_hybrid_retriever() -> HybridRetriever  (t=0.00s) ===
t0 embedding_health = {"mode":"bm25_only", ..., "worker_ready":false, "available":false, "retriever_degraded":true}

=== _ensure_worker() 结果 ===
ok                 = True
first_true_at      = 18.60s (tries=1)
embedding_health   = {"mode":"hybrid","init_failed":false,"worker_alive":true,"worker_ready":true,
                      "load_cache_probe":"hit_namespaced","available":true,"failure_total":0,
                      "restart_attempts":0,"max_restart_attempts":3,"restarting":false,
                      "retry_exhausted":false,"next_restart_in_sec":null,"last_failure":{},
                      "retriever_degraded":false}
_load_time_sec     = 16.74
_load_source       = local_cache:C:\Users\Administrator\.cache\huggingface\hub\models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2\snapshots\e8f8c211...
_load_cache_probe  = hit_namespaced

=== 真实查询（生产入口 HybridRetriever.query）===
query='读取 PDF 的内容'          -> top5=['read_pdf','get_pdf_info','read_pdf_tables','split_pdf','merge_pdf']
   bm25_candidates=8 embed_candidates=38 fused_candidates=38
query='检索 lifetrace 中的历史对话' -> top5=['search_lifetrace','grep','submit_task','distill_process_from_knowledge','git']
   bm25_candidates=5 embed_candidates=18 fused_candidates=22

=== 退出前自证 ===
proc after close = None
```

**改前同入口同口径**（主审计复核 + 我自己的原始输出）：
`embedding.worker.ready_timeout, timeout_sec=120.0` ⇒ `_ensure_worker()=False @120.02s`、
`mode=bm25_only`、`init_failed=True`、`available=False`、`embed_candidates=0`。

### 3.4 反面对照：缓存未命中 ⇒ **回退在线且不崩**（进程内 monkeypatch，未删缓存）

探针 `probe_miss.py`：把 `snapshot_download` 换成必抛的桩（真缓存**一个字节都没动**）。

**MODE=miss（两条候选全未命中 + 在线不可达）**：

```
_ensure_worker() -> False   elapsed=150.30s
health = {"mode":"bm25_only","init_failed":true,"available":false,"worker_ready":false,"retriever_degraded":true}
marker 文件 = [
  'snapshot_download_raised:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
  'snapshot_download_raised:paraphrase-multilingual-MiniLM-L12-v2',
  'online_fallback_attempted:paraphrase-multilingual-MiniLM-L12-v2',   <-- 回退在线被真的走到，用的是原始模型名
  ...（后续 3 次退避重启同样如此）]
query ok=True embed_candidates=0 bm25_candidates=8
父进程未崩（已走到这里）；worker proc after close = None
```

**MODE=hit_bare（命名空间候选未命中、裸名命中）** ⇒ 第二候选被真的用到，
`cache_probe` 与 `load_source` 如实区分（另一轮运行，探针 `probe_hitbare_debug.py`）：

```
ok = True  elapsed = 35.4s
load_source = online:paraphrase-multilingual-MiniLM-L12-v2   cache_probe = miss
pending now = 0   doc_ids = 90   embeddings = (90, 384)
available = True  worker_ready = True
   {'action': 'embedding.worker.ready', 'load_time_sec': 33.68,
    'load_source': 'online:paraphrase-multilingual-MiniLM-L12-v2', 'cache_probe': 'miss'}
   {'action': 'embedding.encode_pending.complete', 'n_pending': 90, 'total_docs': 90, 'shape': [90, 384]}
```

**披露**：这一次"回退在线"的对照**真的发出了 HF 请求**（`SentenceTransformer(裸名)` 内部走
`hf_hub_download`，绕过了我打在 `snapshot_download` 上的桩），耗时 33.7 s 后**成功**。
它没有调用任何 LLM，但确实消耗了出网；这里如实登记（这恰好也证明：在线路径是
**30 s 必超时、120 s 恰好够**的长尾 —— §5 的取值依据之一）。

### 3.5 ★返工中的意外发现：worker 在"父进程式 Popen"下 100% 卡死（已修）

修好缓存后，生产入口**仍然** 120 s 超时。逐步定位（`diag_worker.py` 直跑正常、
`repro_popen.py` 复刻父进程的 Popen 就卡死）得到矩阵结论：

| 实验 | 形态 | 结果 |
|---|---|---|
| A / C | 真实脚本 + `stdin=PIPE`（-c / 文件两种） | **70 s 无任何 stdout/stderr** |
| B | 脚本里**关掉 stdin 抽水线程**，其余不变 | **17.3 s 打出 ready**（`load_source=local_cache:`） |
| D | 真实脚本 + `stdin=DEVNULL` | 0.3 s 退出（EOF ⇒ `os._exit(0)`，抽水线程按设计生效） |
| W1 / W2 | 最小复现：**任何**后台线程阻塞读 fd0 + `import sentence_transformers` | **45~60 s 卡死** |
| W3 | 无后台读线程 + 同一导入 | 15.9 s 正常 |
| X1 | 后台线程 `time.sleep(600)` + 同一导入 | 16.2 s 正常 |
| X2 | 后台线程读 stdin 但**立即 EOF** + 同一导入 | 15.9 s 正常 |
| X4 / X6 | 导入后再起线程 / 用**进程句柄等待**做看门狗 | 17.2 s / 15.0 s 正常 |

⇒ **机制**：torch 栈导入期间，另一个线程**阻塞在 stdin 管道的读上**会把整个解释器冻住
（与"有线程在阻塞"无关，只与"阻塞在 fd0"有关）。我第一版的 stdin 抽水线程因此让 worker
**永远不 ready**。
⇒ **改法（已落地）**：放弃 stdin 抽水，改用**父进程存活看门狗**，并把它放到 `main()` **第一行**
（X6 证明进程句柄等待与导入不冲突）；协议行仍由**主线程**读（`for line in sys.stdin` 保持唯一读者）。

---

## 4. 复核②：「23.2 s 就绪」的结论

- **我无法复现"23.2 s"这个具体数值**，但**方向与量级成立**，且**构成与当初的假设不同**。
- 修好 ① 之后，生产入口实测 **18.60 s**（同机、6 卡并发、不设 env、默认 120 s），其中
  `load_time_sec = 16.74`，而**其中约 15 s 是 `import sentence_transformers`（torch 栈）**，
  **纯模型加载只有 2.1 s**（`diag_worker.py`：导入 14.97 s、`SentenceTransformer(快照目录)` 2.10 s）。
  E1-F1 报的 `load_time_sec=4.0` 与我的 2.1 s 同量级 ⇒ 那条链**是通的**。
- **同时我必须指出**：修好 ① 之前，**任何**用裸模型名查缓存的实现都会静默回退在线，
  所以"23.2 s"这个读数**只可能**来自（a）当时用了带命名空间的模型名，或（b）在线路径恰好通的
  偶发快样本。我**没有**能力区分这两者（E1-F1 没留那次探针的 `load_source`）。
- **本卡的口径**：不把"23.2 s"当验收基线；新基线 = **生产入口 18.60 s / `cache_probe=hit_namespaced`
  / `load_time_sec=16.74`**（可复现、可归因），且明确 `mode=hybrid`。
- **我自己的同类错误（必须写出来）**：我第一版的"缓存命中 0.00 s"是拿**手写命名空间**的样本测的，
  不经过生产入口 ⇒ 与 E1 卡"同一条重尾里抽偶发样本"是**同一类**举证错误。返工后我用的是
  **生产入口 + 真实默认模型名 + 三态可观测**。

---

## 5. 优先级 3：就绪超时可配置且 ≥120 s

- `_WORKER_READY_TIMEOUT_DEFAULT = 120.0`；`_WORKER_READY_TIMEOUT = _resolve_worker_ready_timeout_from_env()`；
  `EmbeddingIndex._WORKER_STARTUP_TIMEOUT` 保持"同源别名"（`eval_route_conflict.py` 的类属性覆盖仍有效）。
- env：`AGENT_HYBRID_WORKER_READY_TIMEOUT`；非法/非正数 ⇒ 回退 120.0 并留 WARNING；**导入时读取一次**。
- 取值理由（已写入代码注释）：E1-F1 实测 30 s 只剩 **23.2/30 = 1.29×** 余量，而"过线"的后果是
  **静默降级**；120 s ≥ 实测就绪时间的 5×，且**只在预热线程生效、不在请求路径上** ⇒ 不增加查询时延。
  **它不负责兜住在线路径**（那是缓存优先的职责；抬超时已被 E1 证伪）。
- 佐证：§3.4 的在线回退实测 33.7 s —— 旧 30 s 必败、新 120 s 恰好够。

---

## 6. 优先级 4：worker 孤儿泄漏

### 6.1 pid 证据（探针 `orphan_probe.py`，子进程一律处于"长加载"状态）

| 场景 | 改前（HEAD） | 改后（现行） |
|---|---|---|
| 父进程**正常退出**（`sys.exit`） | 子进程在 t=1/3/6/12/20/30 s **全部存活 = 孤儿** | t=12 s 起 **已消失**（atexit `close()` 回收） |
| 父进程**被强杀**（`taskkill /F`） | 同上：**30 s 仍存活 = 孤儿** | **t=1 s 即已消失**（子进程看门狗 `os._exit(0)`） |

"改前"两次观测窗结束时仍存活的 worker（pid 6240 / 19160）由**探针自己 taskkill 清理**
（只杀本探针自己起的进程）；改后无需清理。

**python.exe 全进程 PID 快照**：开始前 `[10936]` → 结束后 `[10936]`（**零残留**）。

### 6.2 清理路径（代码里解决，不用 taskkill 兜底）

| 侧 | 机制 |
|---|---|
| 父进程侧 | `EmbeddingIndex.close()`（公开）+ `_LIVE_INDEXES`（`weakref.WeakSet`）+ `atexit` 钩子 `_shutdown_all_workers()`；`HybridRetriever.close()` 透传 |
| 子进程侧 | `_start_orphan_guard()`：Windows `OpenProcess(SYNCHRONIZE)` + `WaitForSingleObject(INFINITE)`（父进程一死**立即**返回）、POSIX/兜底轮询 `os.getppid()`；命中即 `os._exit(0)` |

### 6.3 残留窗口（诚实）

看门狗在 `main()` 第一行启动，覆盖"导入 + 加载"全程。**唯一**剩下的窗口是：子进程已经被
`Popen` 出来、但解释器还没跑到 `main()` 的**解释器启动阶段**（毫秒级）。实测"强杀父进程"
格子里 worker 在 **t=1 s** 就已消失。

### 6.4 顺带自查：`agent/tool_router_reranker.py`（**只读，未改**）

| 维度 | 结论 |
|---|---|
| 同病 ①「未就绪即 True」 | **有同形代码**（`:289-290` 仍是 `poll() is None ⇒ return True`），但**后果不同**：其 Popen 派在 `self._lock` 内完成整个握手，而唯一的管道写者 `_predict_scores` 也取 `self._lock`（`:379`）⇒ 早返回的调用者会在锁上排队，**不会**把 ready 行读成自己的响应。症状是"请求路径被阻塞到握手结束"，不是错位 |
| 同病 ② 在线解析 | **无**：其 worker 脚本已做本地缓存优先（`:166-175` 手扫 `~/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3/snapshots/*/config.json`），失败才回退 repo id。**但它不是 `snapshot_download(local_files_only=True)`，通用性弱于本卡新实现** |
| 同病 ③ 孤儿 | **有**（无 atexit、无父进程存活看门狗；其 worker 也只靠 stdin EOF）。它默认关闭（`AGENT_HYBRID_RERANKER != "1"` 即不启用），故本机现网未暴露 |
| 说明 | 该文件**不在本卡文件范围**，以上均为静态阅读结论（**未做差分探针**）⇒ 已在 §14 登记为跨卡请求 |

---

## 7. 优先级 5：`worker_health()["mode"]` 不再撒谎

- 旧口径：`"bm25_only" if _init_failed else "hybrid"` ⇒ spawn 后 6 s 采样得到
  `mode="hybrid"` 而 `available=false`（E1-F1 实测）。
- 新口径：`mode = "hybrid" if self.available else "bm25_only"`，**保持二值**（不新增第三态），
  于是任何按 `==` 比较的既有消费者都会自动得到诚实答案；启动中的事实由新增字段
  `worker_ready` / `load_cache_probe` 与既有 `worker_alive` / `available` 如实表达。
- 并把 `available` 的判据补全为「**真的能供数**」：`未判死 + 真的 ready + 进程活 + 有向量 + doc_ids 非空`。
  旧口径漏掉"当前 worker 是否 ready"，于是在**重启窗口**里会谎报 `available=True / degraded=False`
  （老向量还在、新 worker 还在加载，此刻一条查询都供不了）。这是**更严**的方向。
- 前后对照（生产入口）：

| 时刻 | 改前 | 改后 |
|---|---|---|
| `get_hybrid_retriever()` 刚返回（t=0） | `mode=hybrid, available=false` ← 撒谎 | `mode=bm25_only, worker_ready=false, available=false, retriever_degraded=true` |
| 就绪后 | `mode=hybrid, available=true`（向量腿未跑） | `mode=hybrid, worker_ready=true, available=true, load_cache_probe=hit_namespaced` |
| 加载窗口内（重启场景） | `mode=hybrid, available=true`（`worker_ready` 字段不存在） | `worker_ready=false` ⇒ `available=false` ⇒ `mode=bm25_only` |

---

## 8. ★`eval_route_conflict.py`：bm25_only vs hybrid（并列）

同一棵树、同一用例集（50 条）、同一脚本，唯一差别是向量腿开/关；两条命令都**离线、零 LLM**：

```
python scripts/eval_route_conflict.py --no-embedding        --json ...\eval_bm25_final.json
python scripts/eval_route_conflict.py --wait-embedding 150  --json ...\eval_hybrid_final.json
```

| 指标 | bm25_only | **hybrid** | 差 |
|---|---|---|---|
| 脚本自报 `mode` | `bm25_only` | `hybrid` | — |
| **决策层通过（主判据）** | **27/50（54.0%）** | **25/50（50.0%）** | **−2（hybrid 更差）** |
| 下发层 expected 命中 | 40/50 | **46/50** | +6 |
| forbid 在下发集里的用例数 | 25 | 29 | +4（更差） |
| top1 命中却未进下发集 | 0 | 0 | — |
| τ 标定 best F1 | 0.7937（τ=0.0462） | **0.9020（τ=0.1276）** | +0.108 |
| τ 通道 precision / recall | 0.6944 / 0.9259 | **0.8846 / 0.9200** | precision +0.19 |
| τ 通道 TP/FP/FN/TN | 25/11/2/12 | 23/**3**/2/22 | **假阳 11→3** |
| 执行率 | 72% | 52% | — |
| 退出码 | 0 | 0（`BASELINE["hybrid"]=None` ⇒ 下限 0） | — |

**如实结论（不粉饰）**：

1. **修好向量腿并没有让决策层变好，反而 −2 条**（27→25）。也就是说
   「hybrid 基线未验证」这个缺口填上之后，**现有 α=0.5 的融合排序不优于纯 BM25**。
   这与代码注释里早已声明的"余弦路锚点与 BM25 路锚点来源不同、不等权"一致 ——
   两路**并非同等置信**，等权融合会把 BM25 的好排序稀释掉。
2. 但 hybrid 在**另外两处明显更好**：下发层 40→46（召回更全）、
   **分差通道的判别力显著提升**（F1 0.79→0.90，假阳 11→3，precision 0.69→0.88）。
   这直接回应了 E1 卡"方案 3.4 主通道不可行、只剩检索分差"的结论：
   **分差通道在真 hybrid 下才第一次变得可用**。
3. 建议（不改本卡范围）：把"决策层排序"与"τ 门控通道"分开取值 ——
   前者可考虑上调 `alpha` 或直接用 BM25 序；后者应基于 hybrid 的分差。
   **这属于下一步的参数标定卡，不是本修复卡的范围。**

---

## 9. 回归与守卫

| 组 | 结果 |
|---|---|
| 既有 hybrid 组（改前基线 **133 passed**） | **154 passed**（含 `test_embedding_worker_crash_visibility` 的 1 条改写断言） |
| 本卡新增守卫 `tests/unit/test_tool_router_hybrid_e1f1a.py` | **22 passed** |
| 健康端点契约 `test_health_retrieval_endpoint.py` | 通过（其 mode 断言走**桩**，不受本卡影响） |
| 注册表零缺口（**主审计指定命令**）`python -m pytest tests/unit/test_settings_registry.py -q` | **56 passed**（= 基线，全绿） |

命令（可复现）：

```
python -m pytest tests/unit/test_tool_router_hybrid.py tests/unit/test_tool_router_hybrid_integration.py ^
  tests/unit/test_embedding_worker_crash_visibility.py tests/unit/test_worker_startup_timeout.py ^
  tests/unit/test_tool_router_hybrid_fusion_calibration.py tests/unit/test_tool_router_hybrid_real_index_l28.py ^
  tests/unit/test_tool_hybrid_lang_recall.py tests/unit/test_bm25_incremental_total_len.py ^
  tests/unit/test_tool_router_hybrid_e1f1a.py -q
python -m pytest tests/unit/test_settings_registry.py -q
```

### 9.1 我改写了一处既有断言（不是放宽）

`tests/unit/test_embedding_worker_crash_visibility.py::TestWorkerHealthContract::test_healthy_state_reports_hybrid`
原本是：

```python
health = mod.EmbeddingIndex().worker_health()
assert health["mode"] == "hybrid"      # <- 拿一个"从未启动 worker"的实例要求报 hybrid
```

它**编码的正是本卡要根除的那条谎报**（P5），故必然与本卡冲突。改写后
（`test_healthy_state_reports_mode_truthfully`）断言**更强**：
① 未启动 ⇒ `bm25_only + worker_ready=False + available=False` 三者一致；
② 受控桩（活进程 + 真置位 ready + 有向量）⇒ `hybrid`；
③ 就绪一熄 ⇒ mode 同步回落。
**没有删除任何既有断言**，只是把它从"断言缺陷"改成"断言正确性"。

### 9.2 ★一个与**本卡无关**的回归观察（供 SET-REG 卡）

在**合并多文件**运行时，`test_settings_registry.py` 有 **2 条**失败：

```
FAILED ...::TestConfigPathDrivesDisplayNotRuntime::test_no_declared_key_is_env_pinned_here
FAILED ...::TestConfigPathDrivesDisplayNotRuntime::test_every_declared_path_flips_default_to_config
E  AssertionError: ORCHESTRATOR_REJECT_ENABLED 在配置层缺位时来源不是 default：env
E  AssertionError: 本进程环境里已设置这些开关的 env：['ORCHESTRATOR_REJECT_ENABLED', 'SKILLS_FUSION_WEIGHT_BM25']
```

- 这两个键来自仓库 `.env`（`.env:221 ORCHESTRATOR_REJECT_ENABLED=false`、
  `.env:148 SKILLS_FUSION_WEIGHT_BM25=0.2`），**与本卡无关**
  （本卡既不设置也不读取它们；本卡新 env 在进程环境里为 `None`）。
- 证据：① `pytest tests/unit/test_settings_registry.py -q` **单跑 56 passed**；
  ② 把本卡新增测试文件从选择集里**去掉后仍然复现**（2 failed / 246 passed）；
  ③ 失败断言的消息里只有上面两个键。
- ⇒ 属**跨测试文件的环境污染**（某个文件把 `.env` 灌进了 `os.environ`），归 SET-REG 收口。

---

## 10. 新 env 的登记位置

`agent/settings/registry.py` 第 **2547 行**（`_REGISTRY_ROWS` 内，紧随原末行 F3-1 的
`YUNSHU_PROMPT_VOLATILE_TAIL` 之后追加；锚点当时存在，未改动他人任何行）：

```python
_a("AGENT_HYBRID_WORKER_READY_TIMEOUT", CAT_SKILLS, 120.0,
   "工具检索 embedding worker 的**就绪等待上限**（秒；代码级默认 120.0）。"
   "非法值/非正数一律回退 120.0 并留 WARNING（不静默接受垃圾值）。"
   "超时即降级为纯 BM25（向量腿整条失效）—— 故本值是「静默降级」这条路径的唯一旋钮"
   " [E1-F1-A；agent/tool_router_hybrid.py:_WORKER_READY_TIMEOUT]",
   owner="agent/tool_router_hybrid.py", needs_restart=True,
   validator=_range_validator(1.0, 3600.0, note="秒；<=0/非数字回退代码级默认 120.0")),
```

- **风险级 A**（纯数值超时；调大 = 等待更久、调小 = 降级更早，两个方向都不放宽防护面）；
- `needs_restart=True`（导入时读取一次，与同表 `BM25_K1` 同口径）；
- 用本文件既有的 `_a` 助手、六分类里的 `CAT_SKILLS`、既有 `_range_validator`，
  **未另发明任何结构**；登记后 `test_settings_registry.py` 零缺口守卫仍全绿。

---

## 11. 未验证项与残留风险（诚实列全）

1. **查询↔向量错位未能端到端复现**（§2.4）。已复现的是它的前提（未就绪即 True，改前 3/3）。
   我未能手动触发的那条残留路径（`_init_failed=True` 且 `_proc` 仍活）在**新代码里已被就绪标志封死**，
   但**没有**实测证据 ⇒ 记为"已消除但未复现"。
2. **在线回退路径**只在"桩 + 真网络"下验证过一次（33.7 s 成功）；**没有**验证"两个 HF 端点都不可达"
   时的真实重尾（零出网预算 + E1-F1 已证不可达是间歇的）。该分支的正确性主要靠**单元测试**
   （miss ⇒ `online:` + 用原始模型名）而非端到端。
3. **`hit_bare` 分支**只在探针里验证过（第二候选被用到）；生产默认模型名走的是 `hit_namespaced`。
4. **`load_failed` 分支**（快照在但加载失败）只有单元测试覆盖（注入假库），**没有**真实坏缓存的端到端验证。
5. **`AGENT_HYBRID_WORKER_READY_TIMEOUT` 的 env 覆盖路径**只有单元测试；生产".env 里设值 ⇒ 生效"
   未跑（需新进程 + 真加载）。**登记本身已落地**（§10）。
6. **孤儿清理的窗口**：解释器启动到 `main()` 之间（毫秒级）无看门狗；硬杀父进程的实测是 t=1 s 已回收。
7. **reranker 的同形缺陷未修**（§6.4）：不在本卡文件范围。默认关闭，但一旦
   `AGENT_HYBRID_RERANKER=1` 就同时继承"孤儿窗口"与"未就绪即 True 的阻塞式后果"。
8. **`alpha=0.5` 未调参**：§8 显示 hybrid 决策层更差 ⇒ 融合权重与两路锚点不匹配
   （代码注释早已声明二者不等权）。本卡**只让向量腿可用**，**没有**动排序参数。
9. **`available` 语义收紧的波及面**：`degraded` 在启动/重启窗口里现在会报 `True`（更诚实），
   下游 `app_server` 的 `/api/health/retrieval` 会因此在这些窗口显示 `degraded`
   （口径并集本就这样设计）。我**只验证了** hybrid 相关测试 + 健康端点契约测试，
   **没有**跑全仓测试套件。
10. **`worker_health()["mode"]` 二值化**：`test_health_retrieval_endpoint.py` 的 mode 断言基于桩，
    不受影响；但我**没有**扫全仓其它 mode 消费者。
11. **`_encode_pending_locked` 现在自持 `_lock`**：与 `rebuild()` 共用同一把 RLock，
    理论上 `rebuild` 持锁期间握手线程会等锁 —— 与改前一致（改前更坏：两个编码者可同时写管道）。
    **未做并发压测**。
12. **§9.2 的 2 条注册表失败**不是本卡引入，但会污染"全绿"口径，需 SET-REG 处理。

---

## 12. 回滚指令（**定向 revert 自己那几行**；禁止整文件 `git checkout`）

改动集中在 4 个文件、块与块互不重叠。**不要** `git checkout <file>` ——
这些文件都夹着其它 30 张卡的未提交改动。

1. `agent/tool_router_hybrid.py`
   - 删 `_resolve_worker_ready_timeout_from_env()` / `_WORKER_READY_TIMEOUT_DEFAULT`，
     把 `_WORKER_READY_TIMEOUT = ...` 还原为 `_WORKER_READY_TIMEOUT = 30.0`；
   - 删 `import atexit` / `import weakref`、`_LIVE_INDEXES`、`_shutdown_all_workers()`
     与 `atexit.register(...)`；
   - worker 脚本：删 `_start_orphan_guard()`（含调用）、`_cache_candidates()`、`_load_model()`；
     加载改回 `model = SentenceTransformer(model_name)`；`while True: line = _REQ_Q.get()` 改回
     `for line in sys.stdin:`；
   - `EmbeddingIndex`：删 `_worker_ready` / `_startup_lock` / `_load_cache_probe` / `close()`；
     `_ensure_worker` 恢复为"poll() ⇒ True"单段实现；`available` 去掉 `_worker_ready` 判据；
     `search()` 去掉锁内就绪判据；`worker_health` 的 `mode` 还原为 `_init_failed` 口径并去掉两个新字段；
     删 `HybridRetriever.close()`。
   - **最快的一条**：在 `git diff -- agent/tool_router_hybrid.py` 里**只挑本卡相关 hunk**
     （注释里带 `E1-F1-A` / `优先级 N` 的那些）反向应用；其它 hunk 属于别的卡，**不要碰**。
2. `agent/settings/registry.py`：删除第 2547 行起的 `AGENT_HYBRID_WORKER_READY_TIMEOUT` 登记块（含上方注释）。
3. `tests/unit/test_embedding_worker_crash_visibility.py`：把 `test_healthy_state_reports_mode_truthfully`
   换回原有的 `test_healthy_state_reports_hybrid`（3 行断言）。
4. 删除 `tests/unit/test_tool_router_hybrid_e1f1a.py` 与本报告（或改名保留为证据）。
5. 回滚后自检：`pytest tests/unit/test_settings_registry.py -q`（应仍 56 passed）、
   `pytest tests/unit/test_embedding_worker_crash_visibility.py -q`（应 12 passed）。

---

## 13. 进程与残留物自证

- **仓库内**：`git status --porcelain` 中属于本卡的条目**仅**：
  `M agent/tool_router_hybrid.py`、`M agent/settings/registry.py`、
  `M tests/unit/test_embedding_worker_crash_visibility.py`、
  `?? tests/unit/test_tool_router_hybrid_e1f1a.py`、`?? docs/audit_skill_governance/`（本报告）。
  **无 `undefined/` 之类失败写入残留**，无本卡引入的未知文件。
- **仓库外**（探针与临时产物，全部在 `C:\Users\Administrator\AppData\Local\Temp\e1f1a\`）：
  `trh_before.py`（HEAD 基线副本）、`fake_worker.py`、`probe_p1_diff.py`、`probe_ready.py`、
  `probe_miss.py`、`probe_hitbare_debug.py`、`diag_worker.py`、`repro_popen.py`、`matrix_worker.py`、
  `repro_stdin_thread.py`、`repro_pump_torch.py`、`repro_pump_kind.py`、`repro_guard_early.py`、
  `orphan_probe.py`、`worker_real.py`、`eval_*.json`、`regression.txt`、`marker_*.txt`。
- **进程**：本卡自己起的子进程在每次探针结束前都显式回收；孤儿探针里"改前"那两个**故意留下**的 worker
  由探针**自己** `taskkill`（只杀本探针的 pid）；driver 打印的全进程 PID 快照开始/结束一致
  （`[10936]` → `[10936]`）。**未对任何其它卡的 python 进程执行 taskkill。**
- **HF 缓存**：**未删除、未修改**（未命中一律用进程内 monkeypatch 模拟）。

---

## 14. 跨卡请求（不在本卡文件范围内，未改）

1. `agent/tool_router_reranker.py`：① `:289-290` 的"未就绪即 True"（当前被 `self._lock` 掩盖，
   但语义仍是谎报）；② 无父进程存活看门狗 / 无 atexit（孤儿窗口同形）；
   ③ 其本地缓存查找是**手写路径扫描**（`:166-175`），建议统一到本卡的
   `_cache_candidates + snapshot_download(local_files_only=True)` + 四态 `cache_probe` 口径。
2. `app_server.py`（**本卡未改**）：`/api/health/retrieval` 的 `degraded` 并集口径现在会正确反映
   "启动窗口 = degraded"；若要对齐本卡新增字段（`worker_ready` / `load_cache_probe`）需另一张卡。
3. **参数标定卡**：§8 的结论（hybrid 决策层 −2 但分差通道显著更好）提示 `alpha` 与两路锚点需重新标定；
   本卡只负责"向量腿可用"。
4. **SET-REG**：§9.2 的跨文件环境变量污染（`.env` 被灌进 `os.environ`）会让
   `TestConfigPathDrivesDisplayNotRuntime` 的两条对拍用例在合并运行时变红。


