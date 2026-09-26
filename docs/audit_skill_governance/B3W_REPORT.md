# B3-W 接线收尾卡报告（B3 六指标的"已就绪却无人调用"断点接线）

- **基线 HEAD**：\`5c9ace10a4ca4bb96860db3a48debf9ddcf496bf\`
- **卡号**：B3-W（B3 后续卡，只做**接线**，不改 B3 已定的指标语义）
- **环境**：Windows，Python **3.12.0 系统解释器**（未使用 \`venv/\`）
- **范围**：\`agent/tool_router_hybrid.py\`、\`agent/observability/tool_trace.py\`、
  \`agent/monitoring/prometheus.py\`（\`agent/orchestrator/routing_observability.py\` 只读引用）
- **禁止项遵守**：全程 **0 次** \`git add\` / \`git commit\`；**0 次** \`git checkout <file>\`；
  未触碰 \`agent/skills_mgmt/\`、\`plugins/skills.py\`、\`agent/lines/callability.py\`、
  \`yunshu-ui/\`、\`data/skills_repo/\`（G1-B 并发修改中）。

---

## 0. 结论速览

| 断点 | 判定 | 一行证据 |
|---|---|---|
| ① \`record_tool_retrieval\` 缺 \`trace_id\` | **已接通** | 改前"含注入 trace 的日志行数 = **1**"（只有 route decision）；改后 **2**：\`action=tool_retrieval trace_id_ctx='b3wprobed981d429'\` 与 \`action=orchestrator.process.route_decision trace_id_ctx='b3wprobed981d429'\` |
| ② 6 处静默零召回 | **按分类接通（2 计入 / 4 不计入 / 1 已非静默）** | 改前 7 条分支 + 对照全部 \`delta=0.0\`、\`事件: 无\`；改后 \`results_empty\` 与 \`sort_empty\` 各 \`delta=1.0\` 且产出 \`action=tool.zero_recall\`，其余 4 条 \`delta=0.0\` 但产出 \`action=tool.retrieval.early_exit\`（不再静默） |
| ③ \`llm_response_cache.hit_rate\` 无 \`/metrics\` 出口 | **已接通** | 改前 \`/metrics\` 上 \`llm_response_cache*\` 行数 = **0**；改后 = **12**，\`llm_response_cache_hit_ratio 0.5\`（= 真实缓存 \`total_hits 1 / total_misses 1\`） |

**最重要的一条语义结论**：\`zero_recall_total\` **只**记「本来该召回却没召回」。
7 条早退分支里**只有 2 条**属于该语义，其余 5 条若也计数，会让这个 B3 验收指标
被"索引没起来 / 白名单约束 / 异常"永久污染成噪声 —— 详见 §1。

---

## 1. 早退分支分类表（6+1）

判据（三条，逐分支套用）：

> **判据 A（能力可行性）**：该次早退发生时，系统**是否具备**召回能力？
> 不具备 ⇒ 谈不上"该召回"，**不得计数**。
> **判据 B（召回是否真的发生过）**：检索阶段是否**成功执行并返回了候选**？
> 没执行 / 执行失败 ⇒ 无法断言"该召回"，**不得计数**。
> **判据 C（零的成因在谁）**：零结果若由**调用方显式约束**或**异常**造成，
> 属回退/降级而非召回质量，**不得计数**。

| # | 行号 | 分支条件 | 归类 | 计入 \`zero_recall_total\` | reason 标签 | 理由 |
|---|---|---|---|---|---|---|
| 1 | \`:1656\` | \`if not _HELPER_AVAILABLE\` | **能力未就绪** | ❌ | \`helper_unavailable\` | \`agent.tool_router\` 的 \`_apply_alias_merge_and_priority_sort\`/\`TOOL_CATEGORIES\`/\`classify_user_input\` **没导入起来**（\`tool_router_hybrid.py:250-265\`），整条混合检索能力本就不存在 ⇒ 判据 A 不成立。这正是任务点名的"不该算零召回"的分支。 |
| 2 | \`:1660\` | \`retriever is None or not retriever.available\` | **能力未就绪** | ❌ | \`retriever_unavailable\` | \`available\` 的定义是 \`self._tools_loaded and self._bm25.size > 0\`（\`:1450-1452\`）：**索引没加载 / 条目为 0 / 单例构造失败**。检索器根本没建起来，一次查询都没发 ⇒ 判据 A、B 均不成立。 |
| 3 | \`:1698\` | \`if results is None\` | **异常降级** | ❌ | \`results_none\` | \`HybridRetriever.query()\` 的 docstring 明写 **"None 表示检索失败"**（\`:1466\`），而它的 \`None\` 只有两个来源：① 索引重建期**抢不到锁**（\`:1473-1475\`，\`acquire(blocking=False)\` 失败）；② \`_query_locked\` **内部抛异常**被吞（\`:1478-1480\`）。两者都是"检索没跑成"，**不是"跑了但没搜到"** ⇒ 判据 B 不成立。把索引重建期的抢锁失败记成零召回，会在每次重建窗口制造假的召回质量塌陷。 |
| 4 | \`:1700\` | \`if not results\`（空列表） | **✅ 零召回** | ✅ | \`results_empty\` | 检索器 **available、索引已加载、\`query()\` 成功返回了列表**，只是候选数为 0 ⇒ 三条判据全部满足：有能力、真的搜了、零的成因就在召回本身（语料/IDF/阈值）。**这就是该指标要测的东西。** |
| 5 | \`:1742\` | \`if not selected\`（白名单交集后为空） | **正常回退** | ❌ | \`whitelist_empty\` | 走到这里说明 \`results\` **非空**（检索成功召回了候选），是**调用方传入的 \`enabled_whitelist\`** 把它们全部过滤掉。零的成因在调用方约束，不在召回质量 ⇒ 判据 C。若计数，则任何"只给 2 个工具的白名单调用"都会稳定刷高零召回率（\`docs/audit_skill_governance/B1-T2.md\` 记录了这类收窄调用是生产常态）。 |
| 6 | \`:1757\` | \`if not result\`（排序/合并后为空） | **✅ 零召回** | ✅ | \`sort_empty\` | \`selected\` 此时**非空**（检索命中 ∪ 类别兜底都可能有），白名单也没把它清空，是漏斗末端 \`_apply_alias_merge_and_priority_sort\` 把候选**全丢了**。有能力、候选存在、最终却零下发 ⇒ "本来该召回却没召回"，且属**内部漏斗故障**，必须计数值班。 |
| 7 | \`:1764\` | \`except Exception\` 分支 | **异常降级** | ❌ | —（沿用既有 WARNING） | **明确判断：算降级，不算零召回。** 理由是判据 B/C：异常意味着检索**没走完**，我们无从知道"本来能不能召回"；且它与"搜了但没搜到"是**互斥的两类故障**，混进同一计数器会让 \`zero_recall_total\` 无法区分"召回质量差"和"代码在崩"。该分支**本来就不静默**（\`:1763\` 已有 \`logger.warning(...)\`，且 \`finally\` 仍会落一条 \`tool_retrieval\` 事件），故本次**未改动它**。 |

**为什么计入的只有 2 条**：这不是"少接了 4 条"，而是**判据要求的结果**。
把 7 条一律 \`+1\` 是最省事的做法，也是最容易做错的做法（本卡被点名的风险）。

> **一处诚实的判断分歧留档**：B3 原 \`record_zero_recall\` 的 docstring 把
> \`results_none\` 与其余 4 个 reason 并列，暗示它也算零召回。本卡依 \`query()\`
> 的**代码事实**（\`None\` 只可能来自抢锁失败/内部异常）判为**异常降级、不计数**。
> 若复核方认为应计入，改动是**一行**：把 \`ZERO_RECALL_REASONS\` 改成
> \`("results_empty", "sort_empty", "results_none")\`（\`tool_router_hybrid.py\`），
> 分类表与测试同步即可。**本卡按"不污染指标"一侧取值。**

### 1.1 实现方式（所有分支统一出口，不再静默）

\`agent/tool_router_hybrid.py\` 新增唯一出口 \`_note_retrieval_early_exit(reason)\`：

- \`reason in ZERO_RECALL_REASONS\` ⇒ 调 \`record_zero_recall(reason, trace_id=...)\`
  （一次调用完成 \`zero_recall_total +1\` + \`action=tool.zero_recall\` 事件），**然后 return**；
- 否则 ⇒ 输出 \`action=tool.retrieval.early_exit\` 的 \`DEBUG\` 结构化事件（带 \`reason\`、\`zero_recall\` 标志、\`trace_id_ctx\`）。

\`\`\`python
ZERO_RECALL_REASONS = ("results_empty", "sort_empty")   # 稳定短标签，禁止拼动态字符串
\`\`\`

⇒ **7 条分支没有一条是静默的了**：2 条进指标（INFO 事件），4 条留痕（DEBUG 事件），
第 7 条沿用既有 WARNING。同时计数器保持语义干净。

---

## 2. 断点① \`trace_id\` 关联

### 2.1 改动

- \`agent/observability/tool_trace.py\`：\`record_tool_retrieval(...)\` 增加
  \`trace_id: Optional[str] = None\` 参数，日志载荷新增 \`'trace_id_ctx': trace_id or ''\`。
- \`agent/tool_router_hybrid.py\`：新增 \`_retrieval_trace_id()\`（惰性取
  \`agent.orchestrator.routing_observability.current_trace_id\`，负结果缓存），
  在**唯一调用点** \`ToolTraceRecorder.instance().record_tool_retrieval(...)\` 传入。

### 2.2 为什么键名是 \`trace_id_ctx\` 而不是 \`trace_id\`（重要）

\`agent/logging_utils.py:140-174\` 的 \`log_dict()\` **会自动补一个 \`trace_id\` 键**，
其值是 \`_trace_id()\`：

\`\`\`python
# agent/logging_utils.py:108-110
def _trace_id():
    """生成 trace_id（结构化日志用）"""
    return uuid.uuid4().hex[:16]
\`\`\`

即 **每一次 \`log_dict()\` 调用现生成一个随机 id**，**每行都不同、不串联任何东西**。
所以"日志里已经有 trace_id 了"是假象。真正可串联的键是与
\`routing_observability.log_layer_result\` / \`emit_route_decision\` 同名的
**\`trace_id_ctx\`**（\`routing_observability.py:299\`、\`:345\`）。本卡接的就是它。

### 2.3 串联证据（同一 trace_id 同时出现在检索日志与 route decision）

**改后**（\`%TEMP%\b3w\b3w_probe_out_AFTER3.txt\` 原始片段）：

\`\`\`
── P1  trace_id 串联(检索决策日志 <-> route decision 记录) ──
注入 trace_id = b3wprobed981d429
hybrid_select_tools 返回 25 个工具: ['get_status', 'search_memory', 'remember', 'read_file', 'workspace_list']
含该 trace_id 的日志行数 = 2
  [JOIN-HIT] action=tool_retrieval trace_id_ctx='b3wprobed981d429'
  [JOIN-HIT] action=orchestrator.process.route_decision trace_id_ctx='b3wprobed981d429'
tool_retrieval 事件数=1  route_decision 事件数=1
  route_decision 原始行: {"module_name": "orchestrator", "action": "orchestrator.process.route_decision",
   "message": "[B3W probe] route decision", "trace_id_ctx": "b3wprobed981d429", "final_layer": "probe_layer",
   "decision": "hit", "layer_results": {}, "decision_basis": {}, "duration_ms": 11.7, "trace_id": "93233fd8b9dc476e"}
P1 结论: 检索日志与 route decision 用同一 trace_id 串联 = YES
\`\`\`

注意上面这一行里**同时存在两个 id**，正好证伪"用 \`trace_id\` 就能串"：
\`"trace_id_ctx": "b3wprobed981d429"\`（可串联）与 \`"trace_id": "93233fd8b9dc476e"\`（随机、不可串联）。

**零召回事件同样可归因到具体请求**（\`P2b\`）：

\`\`\`
── P2b 零召回事件与 route decision 的 trace 关联(请求上下文内) ──
注入 trace_id = b3wzerod981d468 ; zero_recall_total 2.0 -> 3.0 (delta=1.0)
  [JOIN-HIT] action=tool.zero_recall reason=results_empty trace_id_ctx='b3wzerod981d468'
  [JOIN-HIT] action=tool_retrieval reason=None trace_id_ctx='b3wzerod981d468'
  [JOIN-HIT] action=orchestrator.process.route_decision reason=None trace_id_ctx='b3wzerod981d468'
P2b 结论: 零召回事件与 route decision 同 trace = YES
\`\`\`

⇒ 同一次请求的三类记录（**检索决策 / 零召回 / 路由决策**）现在共用
\`trace_id_ctx = b3wzerod981d468\`，可以直接用 \`grep\` 或日志平台按 trace 串起来。

---

## 3. 断点③ \`hit_rate\` 出口

### 3.1 找到的真实来源（未臆造计数器）

- **实现**：\`agent/llm_response_cache.py:58 class LLMResponseCache\`，
  进程级单例 \`llm_cache = LLMResponseCache()\`（\`:384\`）。
- **既有统计来源**：\`LLMResponseCache.get_stats()\`（\`:228-245\`）返回
  \`total_hits / total_misses / total_puts / total_evictions / hit_rate / cache_size ...\`，
  计数在 \`get()\`（\`:148\`、\`:157\`、\`:163\`）与 \`put()\`（\`:214\`）里累加。
- **本卡无法改该文件**（不在允许文件集内）⇒ 采用 **prometheus_client 标准 pull 模式**：
  自定义 \`Collector\` 在**抓取时刻**读真值，无双写、无漂移。

### 3.2 新增的 4 个指标

| 指标名 | 类型 | 语义 |
|---|---|---|
| \`llm_response_cache_hit_ratio\` | gauge | 累计命中率 = \`total_hits/(total_hits+total_misses)\`；无查询时为 0 |
| \`llm_response_cache_hits_total\` | counter | 累计命中次数（源 \`total_hits\`，进程累计、单调） |
| \`llm_response_cache_misses_total\` | counter | 累计未命中次数（源 \`total_misses\`） |
| \`llm_response_cache_entries\` | gauge | 当前条目数（源 \`cache_size\`，底层 L1） |

### 3.3 口径与 Prometheus 语义不匹配的地方，及处理方式

1. **\`hit_rate\` 是字符串百分比，不是数值。**
   \`get_stats()['hit_rate']\` 的实际值是 \`"50.0%"\`（\`f"{hit_rate:.1f}%"\`，\`:241\`），
   且低命中率会被舍入成 \`"0.0%"\`（**丢失全部分辨率**）。
   ⇒ **不解析该字符串**；改由本模块从**整数计数器** \`total_hits/(total_hits+total_misses)\` 现算。
   实测对照：\`get_stats()\` 给出 \`"hit_rate": "50.0%"\`，指标给出 \`llm_response_cache_hit_ratio 0.5\` —— 一致但后者是数值。

2. **累计计数 vs 窗口命中率。**
   \`total_hits/total_misses\` 是**进程启动以来单调递增**的计数，天然是 **Counter** 而非 Gauge；
   "命中率"是由两个 Counter 派生出来的比值。⇒ 两者**都暴露**：\`*_total\` 保留 counter 语义，
   比值只作"**累计**命中率"读数。**窗口命中率（如 5m）无法在进程内无偏地算出**
   （\`get_stats()\` 只给累计值），请在 PromQL 侧算：
   \`\`\`promql
   rate(llm_response_cache_hits_total[5m])
     / (rate(llm_response_cache_hits_total[5m]) + rate(llm_response_cache_misses_total[5m]))
   \`\`\`

3. **命名易混淆，已显式隔离。**
   本组指标与 B3 既有的 \`cache_hit_ratio\` **不是一回事**：
   \`cache_hit_ratio\` 是**服务端前缀缓存**（DeepSeek 提示词缓存，来自 API \`usage\` 的
   \`prompt_cache_hit/miss_tokens\`）；\`llm_response_cache_*\` 是**本地 \`sha256(prompt)\` 全文哈希的 LRU 响应缓存**。
   分母、来源、语义都不同。已在代码注释与本表中写明，**看板/告警不得混用**。

### 3.4 \`/metrics\` 实测（生产入口 \`app_server.app.test_client()\`）

\`\`\`
/metrics status=200  llm_response_cache* 行数=12
  [METRIC] # HELP llm_response_cache_hit_ratio 本地 LLM 响应缓存累计命中率 = total_hits/(total_hits+total_misses)；进程累计值，无查询时为 0；窗口命中率请用 *_total 计数器在 PromQL 侧算
  [METRIC] # TYPE llm_response_cache_hit_ratio gauge
  [METRIC] llm_response_cache_hit_ratio 0.5
  [METRIC] # HELP llm_response_cache_entries 本地 LLM 响应缓存当前条目数（LLMResponseCache.cache_size，底层 L1）
  [METRIC] # TYPE llm_response_cache_entries gauge
  [METRIC] llm_response_cache_entries 1.0
  [METRIC] # HELP llm_response_cache_hits_total 本地 LLM 响应缓存累计命中次数（源：LLMResponseCache.total_hits，进程累计）
  [METRIC] # TYPE llm_response_cache_hits_total counter
  [METRIC] llm_response_cache_hits_total 1.0
  [METRIC] # HELP llm_response_cache_misses_total 本地 LLM 响应缓存累计未命中次数（源：LLMResponseCache.total_misses，进程累计）
  [METRIC] # TYPE llm_response_cache_misses_total counter
  [METRIC] llm_response_cache_misses_total 1.0
P3 结论: /metrics 上 llm_response_cache_hit_ratio = 0.5
\`\`\`

对照真实缓存自报值（同一次运行）：

\`\`\`
put+命中get(='b3w-probe-response')+未命中get(=None) 后 get_stats() =
{"total_hits": 1, "total_misses": 1, "total_puts": 1, "total_evictions": 0,
 "hit_rate": "50.0%", "cache_size": 1, "avg_hit_time_ms": "0.10", "hits_by_type": {"other": 1}}
\`\`\`

⇒ \`1/(1+1) = 0.5\` **逐位吻合**，指标确实读的是真实缓存，不是另起的计数器。

---

## 4. 改前/改后差分原始输出（证明测试非空转）

探针脚本（**同一份**，改前/改后各跑一次）：
\`%TEMP%\b3w\b3w_probe.py\`，原始输出留档 \`%TEMP%\b3w\b3w_probe_out_AFTER2.txt\` /
\`b3w_probe_out_AFTER3.txt\`。读数一律走**生产入口** \`app_server.app.test_client().get("/metrics")\`。

> **留档精度说明**：BEFORE 那次运行时探针脚本的"写原始输出文件"分支有 bug
> （`LINES` 未定义 ⇒ `RAW_OUT_FAIL`），故 `b3w_probe_out_BEFORE.txt` 是 **0 字节**。
> 下面 4.1 的内容是该次运行的 **stdout 原样**（由 PowerShell 捕获，逐字未改），
> 不是事后重跑。AFTER2/AFTER3 的 `.txt` 留档完整。

### 4.1 改前（BEFORE，改动前真实运行，2026-09-26 00:52）

\`\`\`
── P1  trace_id 串联(检索决策日志 <-> route decision 记录) ──
注入 trace_id = b3wprobed97b2f57
hybrid_select_tools 返回 25 个工具: ['get_status', 'search_memory', 'remember', 'read_file', 'workspace_list']
含该 trace_id 的日志行数 = 1
  [JOIN-HIT] action=orchestrator.process.route_decision trace_id_ctx='b3wprobed97b2f57'
tool_retrieval 事件数=1  route_decision 事件数=1
P1 结论: 检索日志与 route decision 用同一 trace_id 串联 = NO

── P2  6+1 条早退分支: zero_recall_total 增量(自己建基线:读初值->测增量) ──
真实 retriever=HybridRetriever available=True
[S1] :1656 not _HELPER_AVAILABLE 归类=? 返回=None
    zero_recall_total: 0.0 -> 0.0  delta=0.0
    事件: 无
[S2] :1660 retriever 不可用 归类=? 返回=None
    zero_recall_total: 0.0 -> 0.0  delta=0.0
    事件: 无
[S3] :1698 results is None 归类=? 返回=None
    zero_recall_total: 0.0 -> 0.0  delta=0.0
    事件: 无
[S4] :1700 results 空 归类=? 返回=None
    zero_recall_total: 0.0 -> 0.0  delta=0.0
    事件: 无
[S5] :1742 白名单过滤后空 归类=? 返回=None
    zero_recall_total: 0.0 -> 0.0  delta=0.0
    事件: 无
[S6] :1757 排序后空 归类=? 返回=None
    zero_recall_total: 0.0 -> 0.0  delta=0.0
    事件: 无
[S7] :1764 except 异常路径 归类=? 返回=None
    zero_recall_total: 0.0 -> 0.0  delta=0.0
    事件: 无
[S0] :正常命中路径(对照) 归类=? 返回=['get_status', 'search_memory', 'remember', 'read_file', 'wo
    zero_recall_total: 0.0 -> 0.0  delta=0.0
    事件: 无

── P3  LLM 响应缓存命中率 /metrics 出口 ──
改前 get_stats() = {"total_hits": 0, "total_misses": 0, "total_puts": 0, "total_evictions": 0, "hit_rate": "0.0%", "cache_size": 0, "avg_hit_time_ms": "0.00", "hits_by_type": {}}
put+命中get(='b3w-probe-response')+未命中get(=None) 后 get_stats() = {"total_hits": 1, "total_misses": 1, "total_puts": 1, "total_evictions": 0, "hit_rate": "50.0%", "cache_size": 1, "avg_hit_time_ms": "0.10", "hits_by_type": {"other": 1}}
/metrics status=200  llm_response_cache* 行数=0
P3 结论: /metrics 上 llm_response_cache_hit_ratio = None

── SUMMARY ──
P1_trace_join=NO
P3_hit_ratio_metric=None
zero_recall_total_final=0.0
DONE
\`\`\`

**改前的三个"不动"**：① 检索日志没有 trace_id（只 1 行命中）；② 7 条早退分支
**全部 delta=0.0 且零事件**（正是 21 张卡之前反复踩的"接线就绪、无人调用"）；
③ \`/metrics\` 上**连指标名都没有**。

### 4.2 改后（AFTER3，与 4.1 同一份脚本，2026-09-26 00:59）

\`\`\`
── P1  trace_id 串联(检索决策日志 <-> route decision 记录) ──
注入 trace_id = b3wprobed981d429
hybrid_select_tools 返回 25 个工具: ['get_status', 'search_memory', 'remember', 'read_file', 'workspace_list']
含该 trace_id 的日志行数 = 2
  [JOIN-HIT] action=tool_retrieval trace_id_ctx='b3wprobed981d429'
  [JOIN-HIT] action=orchestrator.process.route_decision trace_id_ctx='b3wprobed981d429'
tool_retrieval 事件数=1  route_decision 事件数=1
P1 结论: 检索日志与 route decision 用同一 trace_id 串联 = YES

── P2  6+1 条早退分支: zero_recall_total 增量(自己建基线:读初值->测增量) ──
真实 retriever=HybridRetriever available=True
[S1] :1656 not _HELPER_AVAILABLE 归类=? 返回=None
    zero_recall_total: 0.0 -> 0.0  delta=0.0
    事件: [{'action': 'tool.retrieval.early_exit', 'reason': 'helper_unavailable', 'category': None, 'trace_id_ctx': ''}]
[S2] :1660 retriever 不可用 归类=? 返回=None
    zero_recall_total: 0.0 -> 0.0  delta=0.0
    事件: [{'action': 'tool.retrieval.early_exit', 'reason': 'retriever_unavailable', 'category': None, 'trace_id_ctx': ''}]
[S3] :1698 results is None 归类=? 返回=None
    zero_recall_total: 0.0 -> 0.0  delta=0.0
    事件: [{'action': 'tool.retrieval.early_exit', 'reason': 'results_none', 'category': None, 'trace_id_ctx': ''}]
[S4] :1700 results 空 归类=? 返回=None
    zero_recall_total: 0.0 -> 1.0  delta=1.0
    事件: [{'action': 'tool.zero_recall', 'reason': 'results_empty', 'category': None, 'trace_id_ctx': ''}]
[S5] :1742 白名单过滤后空 归类=? 返回=None
    zero_recall_total: 1.0 -> 1.0  delta=0.0
    事件: [{'action': 'tool.retrieval.early_exit', 'reason': 'whitelist_empty', 'category': None, 'trace_id_ctx': ''}]
[S6] :1757 排序后空 归类=? 返回=None
    zero_recall_total: 1.0 -> 2.0  delta=1.0
    事件: [{'action': 'tool.zero_recall', 'reason': 'sort_empty', 'category': None, 'trace_id_ctx': ''}]
[S7] :1764 except 异常路径 归类=? 返回=None
    zero_recall_total: 2.0 -> 2.0  delta=0.0
    事件: 无
[S0] :正常命中路径(对照) 归类=? 返回=['get_status', 'search_memory', 'remember', 'read_file', 'wo
    zero_recall_total: 2.0 -> 2.0  delta=0.0
    事件: 无

── P2b 零召回事件与 route decision 的 trace 关联(请求上下文内) ──
注入 trace_id = b3wzerod981d468 ; zero_recall_total 2.0 -> 3.0 (delta=1.0)
  [JOIN-HIT] action=tool.zero_recall reason=results_empty trace_id_ctx='b3wzerod981d468'
  [JOIN-HIT] action=tool_retrieval reason=None trace_id_ctx='b3wzerod981d468'
  [JOIN-HIT] action=orchestrator.process.route_decision reason=None trace_id_ctx='b3wzerod981d468'
P2b 结论: 零召回事件与 route decision 同 trace = YES

── P3  LLM 响应缓存命中率 /metrics 出口 ──
（见 §3.4：llm_response_cache* 行数=12，hit_ratio=0.5，与 get_stats() 逐位吻合）

── SUMMARY ──
P1_trace_join=YES
P3_hit_ratio_metric=0.5
zero_recall_total_final=3.0
DONE
\`\`\`

**"改前不动、改后动"的对照成立**：同一脚本、同一生产入口，
\`P1 NO→YES\`、\`S4/S6 delta 0.0→1.0\`、\`P3 None→0.5\`，
且 S1/S2/S3/S5/S7 与对照 S0 **依然 delta=0.0**（分类没有被"顺手全接"污染）。

### 4.3 变异证据（把接线删掉 ⇒ 变红）

对三处接线各做一次**定向删除 + 跑测试 + 精确还原**，还原后用 SHA256 校验
工作区文件与变异前**逐位一致**。

| 变异 | 操作 | 测试结果 | 还原校验 |
|---|---|---|---|
| **M1** | 删掉调用点 \`trace_id=_retrieval_trace_id(),\` | \`1 failed, 16 passed\` —— \`TestTraceIdJoin::test_hybrid_select_tools调用点带上trace_id\` | SHA256 一致 ✅ |
| **M2** | 删掉 \`_note_retrieval_early_exit("results_empty")\` | \`5 failed, 45 passed\` —— \`test_结果为空_计入零召回\`、\`test_零召回事件可归因到具体请求\`、\`test_分类表显式且稳定\`、\`test_six_metrics.py::test_零召回计数器在空召回时真的动\`、\`test_six_metrics.py::test_零召回埋点已接线_B3W\` | SHA256 一致 ✅ |
| **M3** | 注释掉 \`register_llm_response_cache_collector()\` | \`4 failed, 13 passed\` —— \`test_指标名存在且类型正确\`、\`test_读数等于真实缓存自报值\`、\`test_命中后计数器真实增长\`、\`test_与前缀缓存指标口径分离\` | SHA256 一致 ✅ |

原始输出（节选）：

\`\`\`
=== 变异 M1（删掉调用点 trace_id=_retrieval_trace_id()）后 pytest ===
FAILED tests/unit/test_b3w_observability_wiring.py::TestTraceIdJoin::test_hybrid_select_tools调用点带上trace_id
======================== 1 failed, 16 passed in 2.74s =========================

=== 变异 M2（删掉 results_empty 的零召回接线）后 pytest ===
FAILED tests/unit/test_b3w_observability_wiring.py::TestZeroRecallWiring::test_结果为空_计入零召回
FAILED tests/unit/test_b3w_observability_wiring.py::TestZeroRecallWiring::test_零召回事件可归因到具体请求
FAILED tests/unit/test_b3w_observability_wiring.py::TestZeroRecallWiring::test_分类表显式且稳定
FAILED tests/unit/test_six_metrics.py::TestKnownGapGuard::test_零召回计数器在空召回时真的动
FAILED tests/unit/test_six_metrics.py::TestKnownGapGuard::test_零召回埋点已接线_B3W
======================== 5 failed, 45 passed in 4.55s =========================

=== 变异 M3（注释掉 collector 注册）后 pytest ===
FAILED tests/unit/test_b3w_observability_wiring.py::TestLLMResponseCacheMetrics::test_指标名存在且类型正确
FAILED tests/unit/test_b3w_observability_wiring.py::TestLLMResponseCacheMetrics::test_与前缀缓存指标口径分离
FAILED tests/unit/test_b3w_observability_wiring.py::TestLLMResponseCacheMetrics::test_命中后计数器真实增长
FAILED tests/unit/test_b3w_observability_wiring.py::TestLLMResponseCacheMetrics::test_读数等于真实缓存自报值
======================== 4 failed, 13 passed in 3.24s =========================
\`\`\`

### 4.4 测试与回归结果

\`\`\`
tests/unit/test_b3w_observability_wiring.py  .................  [100%]   17 passed in 2.18s
tests/unit/test_six_metrics.py               .................................  33 passed
（回归）test_tool_router_hybrid.py + test_tool_router_hybrid_integration.py
        + test_six_metrics.py + test_b3w_observability_wiring.py
        + test_llm_response_cache.py + test_caching_multi_level.py    192 passed in 44.14s
（回归）test_context_assembler / test_fallback_submetric_ratio_invariant / test_intent_layer_metrics
        / test_lost_update_race / test_prometheus_ratio_regression / test_lock_watchdog
        / test_import_smoke / test_tool_retrieval_quality      153 passed, 1 xfailed in 11.04s
\`\`\`

**并发污染防护**：\`_HELPER_AVAILABLE\`、\`trh._hybrid_instance\`、
\`_apply_alias_merge_and_priority_sort\`、\`llm_cache\` 累计计数、\`zero_recall_total\`(Counter 不可重置)
都是模块级状态。测试与探针**一律"读初值 → 断言增量"**（\`_zero_recall_delta\` / 前一后读数），
**没有硬编码任何绝对值**；假检索器经 fixture 注入并在 teardown 精确还原。

**B3 原"已知缺口守卫"已按指示翻转**：\`tests/unit/test_six_metrics.py\` 的
\`test_零召回埋点尚未接线\`（断言源码里**没有** \`record_zero_recall\`）按其自身 docstring
"若此断言失败…请把本测试改为断言 hybrid_select_tools 在空召回时确实调用 record_zero_recall"
翻转为 \`test_零召回埋点已接线_B3W\` + \`test_零召回计数器在空召回时真的动\`。
**这是一处跨卡改动**（该文件属 B3 卡），已在此显式登记。

---

## 5. 改动清单

| 文件 | 改动 | 行数 |
|---|---|---|
| \`agent/tool_router_hybrid.py\` | 新增 \`ZERO_RECALL_REASONS\`、\`_zero_recall_fn()\`、\`_retrieval_trace_id()\`、\`_note_retrieval_early_exit()\`；接入 5 条早退分支；调用点传 \`trace_id\` | **+115 / -0** |
| \`agent/monitoring/prometheus.py\` | 追加 \`_LLMResponseCacheCollector\` + \`_llm_response_cache_stats()\` + \`register_llm_response_cache_collector()\`（文件尾，第 1137-1282 行） | **+146 / -0** |
| \`agent/observability/tool_trace.py\` | \`record_tool_retrieval\` 增 \`trace_id\` 形参 + 载荷 \`trace_id_ctx\` + docstring | **+11 / -1** |
| \`tests/unit/test_b3w_observability_wiring.py\` | **新增**（17 个用例，含反例与分类守卫） | **+368（新文件）** |
| \`tests/unit/test_six_metrics.py\` | 翻转 B3 缺口守卫为接线守卫 + 端到端计数器用例（原 16 行 → 63 行） | **净 +47** |
| \`docs/audit_skill_governance/B3W_REPORT.md\` | **新增**（本文件） | 新文件 |

未触碰（G1-B 并发域）：\`agent/skills_mgmt/\`、\`plugins/skills.py\`、\`agent/lines/callability.py\`、
\`yunshu-ui/\`、\`data/skills_repo/\`。
\`agent/orchestrator/routing_observability.py\` **只读引用，未改一行**。

---

## 6. 未验证项与残留风险（诚实清单）

### 6.1 未验证项

1. **没有跑真实用户请求的端到端链路**。探针在**进程内**建立 \`RouteContext\` 后直接调用生产函数
   \`hybrid_select_tools\`，并用 \`app_server.app.test_client().get("/metrics")\` 读指标；
   **没有**走"HTTP 请求 → 编排器 → 工具路由 → 抓取 /metrics"的完整链路。
   *旁证*：单测调试中出现过一次**真实 HybridRetriever 的真实零召回**（\`text='单测'\`，
   \`BM25 召回: total=0\` → 计数器 +1 并落 \`reason=results_empty\` 事件），说明真实检索路径确实能触发；
   但这**不等于**端到端已验。
2. **\`llm_response_cache.hit_rate\` 没有从真实 LLM 调用链路过**。缓存是用它的公开 API
   （\`put\`/\`get\`）驱动后读的 \`/metrics\`；未验证真实对话是否命中该缓存
   （该缓存的调用方主要在 \`scripts/archive/\` 与文档示例里）。
3. **多进程/多 worker 未验证**。\`zero_recall_total\`（默认 REGISTRY 的 Counter）与
   \`llm_response_cache_*\`（读进程内单例）在 \`gunicorn\`/多 worker 下**各进程独立**，
   没有接 \`PROMETHEUS_MULTIPROC_DIR\`。当前部署是单进程 Flask，故未处理。
4. **\`sort_empty\` 分支在生产是否可达未证明**。探针是靠 monkeypatch
   \`_apply_alias_merge_and_priority_sort\` 返回 \`[]\` 触发的；未见到自然触发的真实样本。
5. **未跑全量 \`tests/unit\`**（只跑了 5+8 个相关文件共 345 用例 + 新增 50 用例）。仓库全量套件
   体量大且存在已知 flaky（\`tests/unit/conftest.py\` 自述 failures 在 6-67 间波动），本卡未据此下结论。

### 6.2 残留风险

1. **★ \`trace_id\` 只在编排器路径上非空**。全仓 \`RouteContext.init\` **只有 1 个调用点**：
   \`agent/orchestrator/orchestrator.py:663\`（\`process()\`），对应两个 hybrid 调用点
   \`_call_llm:3353\` / \`_call_llm_v2:4089\`。
   而**工作台 SSE 路径** \`plugins/chat.py:1201\` **没有任何 \`RouteContext\` 引用**
   ⇒ 该路径的 \`tool_retrieval\` 事件 \`trace_id_ctx\` 会是**空串**，**无法与 route decision 串联**。
   \`plugins/chat.py\` 不在本卡允许文件集内（且正被其他卡修改），**本卡未修**。
   若要补齐，需在 \`plugins/chat.py\` 的请求入口做 \`RouteContext.init(trace_id)\`。
   另：\`ContextVar\` **不跨线程**，若 \`_call_llm\`/\`_call_llm_v2\` 被丢到与 \`process()\` 不同的线程执行，
   trace 也会丢；本次**未验证线程同一性**。
2. **4 条"不计入"分支只有 DEBUG 留痕**。\`helper_unavailable\`/\`retriever_unavailable\`/
   \`results_none\`/\`whitelist_empty\` 发的是 \`logger.debug\`，生产 INFO 级别下**仍不可见**
   （设计取舍：这几条在"检索器没起来"状态下会**每请求一条**，用 INFO 会刷屏）。
   代价是：**"混合检索能力长期未就绪"这件事在 /metrics 上仍然没有计数**——本卡没有为它新造指标
   （避免臆造），属**已知盲区**。若要补，最省的做法是加一个
   \`tool_retrieval_early_exit_total{reason=...}\` 计数器（需新开卡、需改 \`record_zero_recall\` 同族函数）。
3. **\`results_none\` 的归类存在判断分歧**（见 §1 末）。本卡取"不污染指标"一侧；
   若复核方要求计入，改动为一行 \`ZERO_RECALL_REASONS\`。
4. **\`llm_response_cache_hit_ratio\` 是"进程累计"值，不是窗口值**。直接用它做阈值告警会
   因为"启动初期分母小"而抖动；应改用 \`*_total\` + \`rate()\`（§3.3-2）。
5. **\`degraded=True\` 长期为真**。探针多次输出 \`"degraded": true, "embed_candidates": 0\`
   （Embedding 一路未生效，只跑 BM25）。这是**既有状况，非本卡引入**，但它意味着
   \`results_empty\` 的成因里"Embedding 未参与"权重很高 —— 解读零召回率时需知悉。
6. **metric 名的可发现性**：若 \`agent.llm_response_cache\` 导入失败，
   \`collect()\` 不产出任何样本 ⇒ \`/metrics\` 上那 4 个名字**整体消失**（而非显示 0）。
   这是刻意的（不读真值就不报数），但会让"名字消失"与"值为 0"难区分。

---

## 7. 回滚指令

> ⚠️ **禁止** \`git checkout <file>\` —— 这 3 个文件都夹着其他 20 张卡的未提交改动。
> 回滚**只允许定向删除本卡新增的行**（下面的补丁均可逐个反向编辑）。

本卡改动是**纯增量**，三处互相独立，可分别回滚：

**(A) 断点② 零召回接线（\`agent/tool_router_hybrid.py\`，+115 行）**
删掉 \`reset_hybrid_retriever()\` 之后、\`def hybrid_select_tools\` 之前的
**整段 \`# 【B3-W】早退分支可观测接线\` 块**（\`ZERO_RECALL_REASONS\` ~ \`_note_retrieval_early_exit\`，
以 \`# ═══…\` 开头、以 \`logger.debug("[tool_router_hybrid] 早退埋点失败(忽略)"...\` 收尾），
并删掉函数体内 5 处 \`_note_retrieval_early_exit("...") \` 调用行（含其上方 B3-W 注释）。

**(B) 断点① trace_id（\`tool_router_hybrid.py\` + \`tool_trace.py\`）**
- \`tool_router_hybrid.py\`：删除 \`_retrieval_trace_id()\` 定义（含 \`_TRACE_ID_FN\`/\`_TRACE_ID_PROBED\` 与模块级常量）
  与调用点那 4 行注释 + \`trace_id=_retrieval_trace_id(),\`。
- \`tool_trace.py\`：删除形参 \`trace_id: Optional[str] = None,\`、
  docstring 里 \`trace_id:\`/\`【B3-W】日志字段说明\` 两段、
  以及载荷中的 \`, 'trace_id_ctx': trace_id or ''\`。

**(C) 断点③ hit_rate（\`agent/monitoring/prometheus.py\`，+146 行）**
删除文件**末尾第 1137-1282 行整块**：从 \`# ═══…【B3-W】B3 遗留项⑩\` 开始，
到最后一行的 \`register_llm_response_cache_collector()\` 为止。

**(D) 测试文件**
- 删除 \`tests/unit/test_b3w_observability_wiring.py\`（本卡新文件）。
- \`tests/unit/test_six_metrics.py\`：把 \`class TestKnownGapGuard\` 及其后新增的
  \`_FakeEmptyRetriever\` 还原为原 16 行版本（原文本见本卡 diff / 会话记录），
  即 \`test_零召回埋点尚未接线\`（断言 \`"record_zero_recall" not in src\`）。

**(E) 文档**：删除 \`docs/audit_skill_governance/B3W_REPORT.md\`。

回滚后自检：

\`\`\`powershell
$env:PYTHONUTF8="1"
python -m pytest tests/unit/test_six_metrics.py tests/unit/test_tool_router_hybrid.py -q
# 期望：全绿；且源码中不再出现 _note_retrieval_early_exit / llm_response_cache_hit_ratio
Select-String -Path agent/tool_router_hybrid.py -Pattern "_note_retrieval_early_exit"   # 期望 0 命中
Select-String -Path agent/monitoring/prometheus.py -Pattern "llm_response_cache"        # 期望 0 命中
\`\`\`

---

## 8. 复现方式

\`\`\`powershell
# 探针（改前/改后同一份；AFTER 标签可换）
$env:PYTHONUTF8="1"; $env:PYTHONIOENCODING="utf-8"
python "$env:TEMP\b3w\b3w_probe.py" AFTER
# 原始输出：$env:TEMP\b3w\b3w_probe_out_AFTER.txt

# 单测
$env:PYTHONUTF8="1"
python -m pytest tests/unit/test_b3w_observability_wiring.py tests/unit/test_six_metrics.py -q
\`\`\`

**残留物声明**：探针脚本与原始输出**只**存在于 \`%TEMP%\b3w\\\`（\`%TEMP%\` = \`C:\Windows\TEMP\`），
仓库内**无**本卡遗留的临时文件；本卡**未启动任何长期驻留服务**（探针只用进程内
\`test_client\`，不 bind 端口）。
