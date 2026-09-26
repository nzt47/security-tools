# Q5 — 三层漏斗基线与命中率可得性审计

> 审计范围：`agent/`（622 个 .py / ~25.4 万行）+ `logs/` + `data/` + `app_server.py`
> 审计方式：**只读**静态阅读 + 只读统计（PowerShell Select-String / python sqlite3 只读 URI）
> 所有数字均来自实测；与注释/文档声称值不符处已显式标注。
> 标注约定：无代码证据的推论一律加 **【推测】**；注释声称值与实测不符标注 **【注释≠实测】**。

---

## 0. 结论速览（TL;DR）

| # | 结论 | 关键证据 |
|---|---|---|
| 1 | 代码里**没有**「规则 → 模板 → BM25/Embedding」这一条三层漏斗。实际存在的是**两条互不相交的漏斗**：①**意图漏斗**（规则→模板→工作流学习→语义→拒识→规划→LLM，`orchestrator.process()`）；②**工具漏斗**（关键词类别→BM25+Embedding 融合，只在 `_call_llm`_v2 内部执行）。BM25/Embedding **不是**意图漏斗的第 3 层。 | `agent/orchestrator/orchestrator.py:727,880,1004,2324,1152,1312`；`agent/orchestrator/orchestrator.py:3452,4103` |
| 2 | 融合公式**确实是 alpha 加权**，且 alpha **可配**（显式参数 > `AGENT_HYBRID_ALPHA` > 硬编码 0.5），归一化**已不再是 min-max**，改为查询无关的单调校准。 | `agent/tool_router_hybrid.py:156-179, 1282-1324, 1552-1564`；`.env:247` |
| 3 | 「模板(0-Token)」的 0-Token 语义 = **零 LLM 调用**，**不是缓存**。模板层是纯 `if/else` 查表，**没有任何 cache hit/miss 计数器**。 | `agent/response_workflows.py:278-341`；全文无 `cache_hit` 命中 |
| 4 | 三层**都打了点**（`_record_intent_layer` + `log_layer_result`），字段齐全，**但没有任何持久化 sink**：web 服务的路由日志只进 stderr，全仓库磁盘上仅残留 **114 条** `intent_layer` 记录、**110 条** `route_decision` 记录，且 **`template` 层与 `reject` 层各 0 条**。 | `app_server.py:128`；`agent/logging_utils.py:441,479,500-513`；实测见 §8.2 |
| 5 | **现在出不了「规则命中率/模板命中率/检索命中率/零召回率/澄清率」**——分子能取，分母不可靠（`planning` 与 `llm` 对同一请求双重计数），且无持久化。 | `orchestrator.py:1312` 先于 `:1506-1517` 执行 |
| 6 | **完全没有澄清（clarify）机制**。零召回的出口是「拒识」→ 而拒识在本部署 **被显式关闭**，实际全部硬落到 LLM。 | 全仓库 `clarif` 0 命中；`config.yaml:625`；`.env:221` |
| 7 | 拒识的「兜底文案」= `_FALLBACK_MSG`（LLM 低置信度路径），是唯一近似「请用户澄清」的出口，但它是**事后**兜底，不是路由期澄清。 | `orchestrator.py:413, 1448-1472` |

---

## 1. 实测漏斗拓扑

`Orchestrator.process()`（`agent/orchestrator/orchestrator.py:614`）实际执行顺序：

| 序 | 层 | 代码位置（函数/调用） | 埋点调用 | 层名常量 | "0 Token"？ |
|---|---|---|---|---|---|
| L0 | InputGuard | `orchestrator.py:700-721`（`emit_route_decision(LAYER_INPUT_GUARD, DECISION_BLOCK)`） | 仅 block 走 `emit_route_decision` | `input_guard` | 是 |
| **L1** | **规则层** WorkflowEngine | `orchestrator.py:727` `self._workflow_engine.try_match(user_input)`；实现 `agent/workflow_engine/engine.py:114`(`class WorkflowEngine`)`/120`(`def try_match`) | `orchestrator.py:751` `_record_intent_layer("rule")`；命中 `731`，未命中 `758` | `workflow` | 是 |
| **L2** | **模板层** IntentRouter+ResponseTemplates | `orchestrator.py:880` `IntentRouter.classify(routing_input)`；`orchestrator.py:923` `ResponseTemplates.for_intent(...)`；实现 `agent/response_workflows.py:184`(`class IntentRouter`)`/278`(`class ResponseTemplates`)`/286`(`for_intent`) | `orchestrator.py:971` `_record_intent_layer("template")`；命中 `929`，未命中 `975-985`(DEBUG) | `template` | 是（零 LLM，非缓存） |
| **L2.5** | **工作流学习层**（自动闭环 v1，文档未列） | `orchestrator.py:1004` `self._workflow_learning_layer_match(routing_input, trace_id)`；def `orchestrator.py:1953` | `orchestrator.py:1080` `_record_intent_layer("workflow_learning")` | `workflow_learning` | 是（零 LLM） |
| **L3** | **语义层** SkillLoader RRF 三路 | `orchestrator.py:2324` `def _semantic_layer_match`；`orchestrator.py:2374-2383` `svc.loader.match(...)` | `orchestrator.py:2550` `_record_intent_layer("semantic")`；失败 `2607` `semantic_failed` | `semantic` | 否（但零 LLM） |
| L4 | 拒识层 | `orchestrator.py:1134-1178`；判定 `orchestrator.py:2834` `def _should_reject`，调用 `:1152` | `orchestrator.py:1179` `_record_intent_layer("reject")` | `reject` | 是 |
| L5 | 规划引擎 | wire 路 `orchestrator.py:1290`；旧路 `orchestrator.py:1516` | `_record_intent_layer("planning")` ×2 处 | `planning` | 是 |
| L6 | LLM（终态） | `orchestrator.py:1311-1312`；`_call_llm` `:3258` / `_call_llm_v2` `:4010` | `orchestrator.py:1312` `llm`；`:1345` `llm_error`；`:1457` `llm_low_confidence_fallback` | `llm` | 否 |

**层名常量**定义在 `agent/orchestrator/routing_observability.py:41-49`（`LAYER_INPUT_GUARD/WORKFLOW/TEMPLATE/WORKFLOW_LEARNING/SEMANTIC/LLM/OUTPUT_GUARD/REJECT/BEHAVIOR`）；`prometheus.py:715,746-752` 的注释只声明了 `rule/template/semantic/llm/reject`，**注释里没有 `workflow_learning`/`planning`/子指标 `semantic_failed`/`llm_error`/`llm_low_confidence_fallback`**——即代码实际会写 8 种层值，注释只列了 5 种 **【注释≠实测】**。

### 1.1 「BM25/Embedding 混合」的真实位置

| 事实 | 证据 |
|---|---|
| 只在 `_call_llm`/`_call_llm_v2` 内部被调用，即**已经决定走 LLM 之后** | `orchestrator.py:3450-3455`（在 `def _call_llm` `:3258` 内）、`orchestrator.py:4101-4108`（在 `def _call_llm_v2` `:4010` 内） |
| 另有 `task_dispatcher.py` 第三条调用点 | `agent/orchestrator/task_dispatcher.py:48-50` |
| 开关默认 **False**，但本部署配置为 **True** | 默认：`task_dispatcher.py:152-160` `is_section_enabled("smart_tool_selection", default=False)`；实测配置：`agent/data/system_prompt_config.json:4` `"enabled": true` |
| 调用是「二选一兜底」语义，异常/空结果即回退关键词分类 | `tool_router_hybrid.py:1641-1643, 1700-1701, 1743, 1757-1758, 1762-1764` |

⇒ `hybrid_select_tools` 的返回值是 **检索命中 ∪ 类别召回** 的并集（`tool_router_hybrid.py:1703-1717`），因此**单看返回值无法区分「检索命中」与「类别兜底」**，这直接堵死了「检索命中率」的离线计算。

---

## 2. L1 规则层实现位置

| 项 | 位置 |
|---|---|
| 入口调用 | `agent/orchestrator/orchestrator.py:727` `workflow_result = self._workflow_engine.try_match(user_input)` |
| 引擎实现 | `agent/workflow_engine/engine.py:114` `class WorkflowEngine`；`:120` `def try_match` |
| 结果模型 | `agent/workflow_engine/engine.py:103` `class WorkflowResult` |
| 内置规则 | `agent/workflow_engine/builtin_rules.py:58` `def register_builtin_rules(registry)` |
| 匹配器 | `agent/workflow_engine/matcher.py:55` `class RuleMatcher` |
| 单例注入 | `agent/orchestrator/lifecycle_manager.py:427` `self._workflow_engine = WorkflowEngine()` |
| 埋点 | `orchestrator.py:751` `_record_intent_layer("rule")`；层日志 hit `731-742` / miss `758-761` |

**规则层打点可得性：可得**（命中 → `yunshu_intent_layer_total{layer="rule"}` + `intent_layer.metric_recorded` 日志；未命中 → `log_layer_result(LAYER_WORKFLOW, DECISION_MISS)` DEBUG 日志，默认 INFO 级别下**不落盘**）。

---

## 3. L2 模板层（声称「0-Token 缓存命中」）

| 项 | 位置 / 事实 |
|---|---|
| 意图分类 | `agent/response_workflows.py:184` `class IntentRouter`；`:221` 按 priority 排序规则；`:260` `register_intent` |
| 模板查表 | `agent/response_workflows.py:278` `class ResponseTemplates`；`:286` `@staticmethod for_intent(intent, confidence, hour)` |
| 模板体 | `agent/response_workflows.py:316-341` —— 纯 `if intent == INTENT_*` 分支 + 常量字符串，**无 dict/无 LRU/无 TTL/无 IO** |
| 调用点 | `orchestrator.py:880`（classify）、`orchestrator.py:923`（for_intent） |
| 埋点 | `orchestrator.py:971` `_record_intent_layer("template")`；层日志 hit `:929-938`，miss `:975-985` |

### 「0-Token 缓存命中」的说法核查结论

* **0-Token 成立**：模板命中即 `return ResponseBuilder.success(response)`（`orchestrator.py:972`），全程无 LLM 调用；注释亦自述「零 LLM 消耗」（`orchestrator.py:870`）。
* **「缓存」不成立**：`ResponseTemplates.for_intent` 每次都是**重新求值**的纯函数（`response_workflows.py:281` 注释自述「for_intent 为纯函数，无副作用」）。**没有第二次调用会被"命中缓存"这回事。**
* **无缓存指标**：`response_workflows.py` 全文 **0 处** `cache_hit`/`cache_miss`/`hit_rate`。全仓库 `cache_hit` 相关实现只在 `agent/caching/multi_level_cache.py:141-152`、`agent/llm_response_cache.py:231`、`agent/health/health_score.py:458`（且 `routes_health.py:513` 处该值是**硬编码 0.5** 占位）。

⇒ **「模板(0-Token) 缓存命中率」这个指标在当前代码里不存在，也没有可提取的原始计数。** 能算的只有「模板层命中率」（命中次数 / 进入模板层的请求数）。

---

## 4. 语义层（L3）与技能侧 RRF

| 项 | 位置 |
|---|---|
| 语义层入口 | `agent/orchestrator/orchestrator.py:2324` `def _semantic_layer_match` |
| 调用 SkillLoader | `orchestrator.py:2374-2383`（`top_k / enabled_only / min_score / use_vector / use_bm25 / use_reranker / fusion_mode`） |
| 配置默认（硬编码兜底） | `orchestrator.py:1776-1784`：`enabled=True, min_score=0.3, top_k=5, use_vector=True, use_bm25=True, use_reranker=False, fusion_mode="rrf"` |
| 配置（config.yaml） | `config.yaml:581-597`（与默认一致） |
| 二次阈值门控 | `orchestrator.py:2423-2461`（`_bounded_relevance` / `no_bounded_evidence` / `low_bounded_relevance`） |
| 有界相似度键 | `orchestrator.py:75` `_BOUNDED_RELEVANCE_KEYS = ("tfidf_score", "vector_score", "rerank_score")` |
| 命中后短路 | `orchestrator.py:2594-2602`（返回 instruction，跳过 LLM） |
| 埋点 | hit `:2550`；miss 五处 `:2460, 2478, 2505, 2521, 2405`；异常 `:2607` `semantic_failed` |

### 技能侧三路检索实现

| 项 | 位置 |
|---|---|
| RRF 平滑常数 k=60 | `agent/skills_mgmt/loader.py:816` `_RRF_K = 60` |
| 无权重双路 RRF | `loader.py:866` `def _rrf_fuse`（`contrib = 1.0/(k+rank)`，`loader.py:903, 919`；`max_possible = 2.0/(k+1)`，`:950`） |
| 加权 N 路 RRF | `loader.py:1175` `def _rrf_fuse_weighted`（`loader.py:1235` 归一化权重、`:1237` `contrib = normalized_weight/(k+rank)`、`:1270` `max_possible = 1.0/(k+1)`） |
| 默认权重（硬编码） | `loader.py:987-991` `{"tfidf":0.2,"vector":0.6,"bm25":0.2}` |
| 默认权重（config.yaml） | `config.yaml:697-701` `{tfidf:0.2, vector:0.6, bm25:0.5}` |
| 默认权重（.env） | `.env:148` `SKILLS_FUSION_WEIGHT_BM25=0.2`（另两路被注释掉 `.env:146-147`） |
| 权重优先级实现 | `loader.py:1143-1173` `_get_default_weights`（层0 硬编码 < 层1 config.yaml < 层2 .env） |
| 负样本质量门 | `loader.py:830` `_RRF_QUALITY_MIN = 0.3` |
| BM25 技能检索器 | `agent/skills_mgmt/bm25_searcher.py:135` `class BM25SkillSearcher`；`:250` `def search` |
| 向量适配 | `agent/skills_mgmt/vector_adapter.py`（52KB，`:638` `encode_query` 供 NegativeIntentDetector） |
| TF-IDF 页内搜索 | `agent/skills_mgmt/searcher.py:65` `class SkillSearcher`（**注意：这是技能管理页的列表搜索，不是路由链路**） |

**实测生效权重**（`.env` 优先级最高）：`tfidf=0.2, vector=0.6, bm25=0.2` → 三路权重和恰好 = 1.0（不触发重分配）。
**【注释≠实测】**`config.yaml:701` 注释写「bm25: 0.5 ... 专有名词命中率 86%→100%」，但 `.env:148` 已把 bm25 覆盖回 0.2，**该注释描述的不是本部署的生效值**。

### 技能检索层打点

| 项 | 位置 |
|---|---|
| 度量函数 | `loader.py:126` `def _record_skill_match_prometheus(layer, method, success, elapsed_ms)`；`:137-144` 异常静默 |
| Prometheus 出口 | `agent/monitoring/prometheus.py:663` `skill_match_latency_ms`（Histogram，labels `layer/method/success`）、`:672` `skill_match_count_total`（Counter，同 labels） |
| 候选详情日志 | `orchestrator.py:2389-2391`（DEBUG 级，含 `skill_id=score` top5） |

---

## 5. L3「BM25+Embedding 混合」融合公式（代码原文）

### 5.1 融合主循环

`agent/tool_router_hybrid.py:1552-1575`：

```python
1552:        bm25_map = dict(bm25_norm)
1553:        embed_map = dict(embed_norm)
1554:
1555:        fused: list[tuple[str, float]] = []
1556:        for doc_id in all_candidates:
1557:            bm25_score = bm25_map.get(doc_id, 0.0)
1558:            embed_score = embed_map.get(doc_id, 0.0)
1559:            # 若 Embedding 不可用,只用 BM25(alpha=1.0 等效)
1560:            if not self._embedding.available or not embed_norm:
1561:                final = bm25_score
1562:            else:
1563:                final = self._alpha * bm25_score + (1 - self._alpha) * embed_score
1564:            fused.append((doc_id, final))
1565:
1566:        fused.sort(key=lambda x: x[1], reverse=True)
```

**判定：是真正的 alpha 加权线性融合**（`final = α·BM25_calibrated + (1-α)·cosine_calibrated`），但有两个限定：
1. 缺失项按 **0.0** 补齐（`1557-1558`），不是按「剩余路重分配」，所以**单路命中的文档其融合分被 (1-α) 或 α 系统性压低**；
2. Embedding 不可用时退化为**纯 BM25、不做 α 缩放**（`1560-1561`），即降级路与融合路**不同量纲**（注释 `1559` 自称「alpha=1.0 等效」，实际是「跳过加权」而非「α=1.0 代入公式」，两者只在 BM25 项上等价）。

### 5.2 归一化方式（**已不是 min-max**）

`agent/tool_router_hybrid.py:1282-1301`（BM25 路）：

```python
1295:    for doc_id, raw in scores:
1296:        if raw <= 0.0:
1297:            # 非正分不是"证据"：映射为 0 而不是被 min/max 拉成 0.5 之类的中间值
1298:            out.append((doc_id, 0.0))
1299:        else:
1300:            out.append((doc_id, raw / (raw + _BM25_HALF_SATURATION)))
```

`agent/tool_router_hybrid.py:1304-1324`（余弦路）：

```python
1318:    span = 1.0 - _COSINE_CUTOFF
1319:    out: list[tuple[str, float]] = []
1320:    for doc_id, cos in scores:
1321:        v = (cos - _COSINE_CUTOFF) / span if span > 1e-9 else 0.0
1322:        # 截断到 [0,1]：余弦可 >1（浮点误差）或 <cutoff（调用方未剪枝时）
1323:        out.append((doc_id, min(1.0, max(0.0, v))))
```

旧的 min-max 函数仍保留但**已不参与融合**：`agent/tool_router_hybrid.py:1327` `def _min_max_normalize`，`:1333-1335` 注释自述「W5/L27 起不再用于融合」。

### 5.3 alpha 的来源与默认值

| 项 | 值 / 位置 |
|---|---|
| 硬编码默认 | `agent/tool_router_hybrid.py:59` `_DEFAULT_ALPHA = 0.5` |
| 环境变量解析 | `agent/tool_router_hybrid.py:156-179` `def _resolve_alpha_from_env()`：读 `AGENT_HYBRID_ALPHA`，非数字/越界 `[0,1]` 回退 0.5 并 WARNING |
| 优先级 | `agent/tool_router_hybrid.py:1361-1362`：显式参数 > env > 0.5；`:1682-1684` 调用方覆盖 |
| 本部署实际值 | `.env:247` `AGENT_HYBRID_ALPHA=0.5` |
| 写入 event 的字段 | `tool_router_hybrid.py:1543` `"alpha": self._alpha`；`:1777` `alpha=effective_alpha` |
| **alpha 跨请求可变性** | `tool_router_hybrid.py:1682-1683` 直接写 `retriever._alpha = alpha`（**实例级可变状态，非线程安全**：并发请求会互相覆盖 alpha，然后 `:1684` 读回被覆盖的值写进 event） |

⇒ **alpha 值 = 0.5，可配，非写死**（但当前部署生效值就是 .env 与默认值重合的 0.5，因此从 event 里看不出是「配的」还是「默认的」——`tool_retrieval` 事件只记数值不记来源）。

### 5.4 融合前后的其它常数（实测）

| 常数 | 值 | 位置 |
|---|---|---|
| `_COSINE_CUTOFF` | 0.2 | `tool_router_hybrid.py:67`（注释注明它同时是余弦路校准跨度参数） |
| `_BM25_HALF_SATURATION` | 5.5375 | `tool_router_hybrid.py:96` |
| `_MIN_IDF_COVERAGE` | 0.2 | `tool_router_hybrid.py:115`（`:107-113` 注明 "0.0 == 单点关闭护栏"） |
| BM25 k1 / b | 1.5 / 0.75 | `tool_router_hybrid.py:474` `def __init__(self, k1=1.5, b=0.75)` |
| 候选池 `top_k` 默认 | `_DEFAULT_TOP_K`，且 `pool = max(top_k, max_tools)` | `tool_router_hybrid.py:1690-1695` |

### 5.5 第四级（Cross-Encoder 精排）是死代码

| 事实 | 证据 |
|---|---|
| `agent/tool_router_reranker.py`（26KB）公开入口 `get_tool_reranker` / `ToolReranker` **无任何生产调用者** | `agent/tool_router_reranker.py:209`(`class ToolReranker`)`/533``/538`(`get_tool_reranker`)`/557`；全仓库 `record/import` 检索只命中 `scripts/detect_reranker_changes.py:5,29`（CI 变更侦测，不调用） |
| 开关本来就是关的 | `.env:100` `AGENT_HYBRID_RERANKER=0`；`agent/tool_router_reranker.py:544` `if os.environ.get("AGENT_HYBRID_RERANKER","0") != "1": return None` |
| 技能侧 reranker 同样关闭 | `.env:119` `SKILL_RERANKER_ENABLED=false`；`config.yaml:691-693` `reranker.enabled: false`；`orchestrator.py:1782` `"use_reranker": False` |

⇒ Q5 关注的「L3」实际只有 **BM25 + Embedding 两路**，不存在第三级融合。

---

## 6. 命中率统计可得性

### 6.1 打点字段清单（实测）

**A. 层命中计数（Prometheus，进程内）**

| 指标名 | 类型 | labels | 定义位置 | 调用点 |
|---|---|---|---|---|
| `yunshu_intent_layer_total` | Counter | `layer` | `agent/monitoring/prometheus.py:718-722` | `prometheus.py:754`（由 `record_intent_layer` 调用） |
| `yunshu_intent_layer_ratio` | Gauge | `layer` | `prometheus.py:724-728` | `prometheus.py:763`（模块级 dict 相对计数） |

统一入口：`agent/orchestrator/orchestrator.py:368` `def _record_intent_layer(layer)` → 动态 import `record_intent_layer`（`:380`）→ 成功写 INFO 日志（`:383-390`，`action='orchestrator.intent_layer.metric_recorded'`，字段 `layer`/`metric`/`trace_id_ctx`），失败写 WARNING（`:393-397`）。

**B. 层日志字段契约**（`agent/orchestrator/routing_observability.py:222-266`）

`log_layer_result(layer, decision, trace_id, level, action, message, duration_ms, score, **fields)` 输出 4 个必含字段 + 可选字段：

```python
238:        payload: Dict[str, Any] = {
239:            "module_name": "orchestrator",
240:            "action": action or f"orchestrator.layer.{layer}.{decision}",
241:            "message": message or f"[路由层] {layer}: {decision}",
242:            "trace_id_ctx": trace_id or "",
243:            "layer": layer,
244:            "decision": decision,
245:            "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
246:        }
247:        if score is not None:
248:            payload["score"] = round(score, 4)
249:        payload.update(fields)
```

`decision` 取值常量：`routing_observability.py:52-60` `hit/miss/block/pass/modified/success/fallback/error/reject`。

**C. 最终路由决策**（`routing_observability.py:269-299` `emit_route_decision`）

字段：`final_layer` / `decision` / `layer_results`（`RouteContext` 累积的各层中间结果 dict）/ `decision_basis` / `duration_ms`（请求总耗时，`:293` 取 `ctx.duration_ms`）。**每次请求恰好一条 INFO**（`:272` 注释声称）。

**D. 流量占比**（`routing_observability.py:67-148` `class RouteTraffic`）

```python
110:    def _report_locked(cls) -> None:
117:            summary[layer] = {
118:                "attempts": a,
119:                "hits": h,
120:                "hit_rate": round(h / a, 4) if a else 0.0,
121:            }
122:        logger.info(log_dict({
123:            "module_name": "orchestrator",
124:            "action": "orchestrator.traffic.summary",
...
```

每 `ORCHESTRATOR_TRAFFIC_REPORT_INTERVAL`（默认 50，`routing_observability.py:36-38`）次请求输出一条 **含逐层 hit_rate 的 INFO 日志**。
**实测：全仓库 `logs/` 下 `traffic.summary` 命中数 = 0**（见 §8.2）。`RouteTraffic` 是**类变量内存计数**（`:79-82`），既不落盘也不进 Prometheus。

**E. 工具检索**（`agent/observability/tool_trace.py:524-559` `record_tool_retrieval`）

事件 `action='tool_retrieval'`，字段：`query_hash / top_k / latency_ms / bm25_candidates / embed_candidates / fused_candidates / alpha / degraded / tools_preview / raw_bm25_top5 / raw_embed_top5 / bm25_half_saturation / cosine_floor / bm25_filtered_by_min_coverage / bm25_considered / min_idf_coverage / bm25_filtered_preview`。
调用点唯一：`agent/tool_router_hybrid.py:1770-1790`（在 `finally` 中，异常静默 `:1791-1792`）。

**F. 工具关键词选择**（`tool_trace.py:481-497` `record_tool_selection`）

事件 `action='tool_selection'`，字段 `user_input_hash / categories / tools_count / tools_preview`。调用点唯一：`agent/tool_router.py:641-645`。

**G. 学习 KPI（内存，非持久）**（`agent/learning_metrics.py`）

`get_snapshot()`（`:236-327`）输出 7 项 KPI，其中与本问题相关的：
`skill_hit_rate = skill_hits / semantic_queries`（`:290-294`）、`workflow_hit_rate = workflow_hits / total_interactions`（`:295-299`）。
计数字段全在 `__init__` 里初始化为 0（`:69-89`）——**纯进程内内存，无落盘**（全文无 `json.dump`/`sqlite`）。
埋点接线：`record_interaction` → `orchestrator.py:649`；`record_semantic_query` → `orchestrator.py:2405,2460,2478,2505,2521,2566`；`record_llm_tokens` → `orchestrator.py:4186`。

### 6.2 磁盘上到底有什么（实测）

| 位置 | 实测内容 | 能否出路由数 |
|---|---|---|
| `data/logs/` | 59 个 `*.jsonl`（32.9 MB）+ `yunshu_logs.db`(4096B) + `raw/`（空）。抽 `2026-09-19.jsonl`、`2026-09-21.jsonl` 首行：`{"timestamp":…,"labels":{"app":"agent-config","event":"change"},"message":"{\"config_path\":…}"}` | **不能**。`route_decision` 出现 **0** 次、`intent_layer` 出现 **0** 次（实测 Select-String 计数均为 0）。这是**配置变更专用** sink |
| `logs/`（仓库根） | 大量 `*.log` 的 stdout/stderr 捕获。**有** `route_decision`/`intent_layer`/`tool_retrieval` 记录 | **勉强能**，见 §6.3：全仓库合计仅 114 / 110 条 |
| `data/events/` | `events.jsonl` + 逐日分片，schema `{v,event_id,ts,correlation_id,actor,type,payload}`。抽样 `events.jsonl` 前 3 行 `type="intervention"`（任务调度 rerun） | **不能**。无路由事件类型 |
| `data/health/` | `history-YYYY-MM-DD.jsonl`（34 文件 12.3MB）。抽 `2026-09-23` 末两行：`{"overall":0.818,"dimensions":{"l1_process":0.8,"l2_dependency":1.0,"l3_llm_tool":null,"l4_business":null,"l5_semantic":0.5},"issues":["l3_llm_tool 无数据","l4_business 无数据"]}` | **不能**（自身即声明无数据） |
| `data/sessions/` | `sessions.json`(4.6KB) / `groups.json` / `workspaces.json` | **不能**，只有会话索引 |
| `data/traces/` | 仅 4 个文件，全部 `2026-07-06` 的测试残留（`trace_20260624_demo_001.json` 等） | **不能** |
| `agent/data/tool_trace.db` | 5.37 MB，最后写 `2026-09-23 20:09`。表：`tool_traces`(**1072 行**；列 `id,trace_id,tool_name,input_hash,output_hash,latency_ms,success,error_type,session_id,user_role,timestamp,permission_decision`)；`unified_traces`(**3716 行**；列含 `status,duration_ms,input_tokens,output_tokens,total_tokens,cost_usd,payload`) | **不能**（无 routed_layer / final_layer / l1_hit / l2_hit 之类字段；`cost_usd` 与 tokens **全为 0**，实测 `SUM(cost_usd)=0.0, SUM(total_tokens)=0`） |
| `agent/data/tool_router_test_report.json` | 1528 B，`2026-09-03`。**注意：任务书给出的 `data/tool_router_test_report.json` 在本仓库不存在**（只有 `agent/data/` 下这一份） | **不能**。它是 15 项功能测试的 pass/fail 汇总（`summary.total=15, passed=15, success_rate=100.0`），`config_info.total_tools=64, total_categories=11, total_alias_rules=6`，**与命中率无关** |

### 6.3 全仓库日志扫描（实测数字）

模式 `intent_layer 指标已记录: layer=(\w+)`，范围 `logs/**/*.log`：

```
ci_final_shard2.log        14 条
ci_job_105665700634.log    44 条
ci_job_105665700652.log    56 条
合计                      114 条
```

按层分布（**全部 114 条**）：

| layer | 条数 | 占比 |
|---|---:|---:|
| `llm` | 78 | 68.4% |
| `llm_error` | 12 | 10.5% |
| `planning` | 10 | 8.8% |
| `semantic` | 4 | 3.5% |
| `llm_low_confidence_fallback` | 4 | 3.5% |
| `rule` | 2 | 1.8% |
| `semantic_failed` | 2 | 1.8% |
| `workflow_learning` | 2 | 1.8% |
| **`template`** | **0** | **0%** |
| **`reject`** | **0** | **0%** |

对照扫描：`logs/**/*.log` 中 `layer=template` 与 `layer=reject` **0 命中**。
另：`action='orchestrator.process.route_decision'` 全仓库共 **110 条**（`_run12.log` 1 / `app_server_restart_20260919_072118.err.log` 1 / `ci_final_shard2.log` 18 / `ci_job_105665700634.log` 46 / `ci_job_105665700652.log` 42 / `server_restart_20260919_224516.log` 2）；`action='traffic.summary'` 共 **0 条**。

真实 `route_decision` 原文（`logs/ci_job_105665700634.log`，字段完整、无截断）：

```json
{'module_name': 'orchestrator', 'action': 'orchestrator.process.route_decision', 'message': '[LLM] 正常完成',
 'trace_id_ctx': '', 'final_layer': 'llm', 'decision': 'success',
 'layer_results': {'input_guard': {'outcome': 'pass', 'duration_ms': 0.01},
                   'workflow': {'outcome': 'miss', 'duration_ms': 0.0},
                   'llm': {'outcome': 'success', 'duration_ms': 4.81, 'llm_confidence': 'high', 'low_reason': 'normal', 'response_length': 8},
                   'output_guard': {'outcome': 'pass', 'duration_ms': 0.12}},
 'decision_basis': {'llm_duration_ms': 4.81, 'output_guard_modified': False, 'redacted_fields': []},
 'duration_ms': 452.72, 'trace_id': '55c2b51d85d6477a'}
```

⇒ 字段齐全，**可解析**；但 `layer_results` 里**没有 `template`/`semantic`/`reject` 条目**（未走到就不写），且 `llm` 的 `duration_ms` 被 `routing_observability.py:258-260` 的保留键过滤规则约束，仅 `emit_route_decision` 的总 `duration_ms` 是端到端。

### 6.4 日志为什么不落盘（根因）

| 事实 | 证据 |
|---|---|
| Web 服务入口只调用 `logging.basicConfig`，默认只有一个 StreamHandler（stderr） | `app_server.py:128` `logging.basicConfig(level=logging.INFO, encoding="utf-8", force=True)` |
| 带轮转的文件 handler 只在 `setup_agent_logging(enable_file=True)` 时创建，且 `enable_file` 默认 **False** | `agent/logging_utils.py:441`（签名 `enable_file: bool = False`）、`:500-513`（`if enable_file and log_file:` 才 `addHandler(file_handler)`） |
| `setup_agent_logging` 的生产调用者只有 CLI 入口 `main.py`，且不带参数（即 console-only） | `main.py:25-28` `from agent import setup_agent_logging` / `setup_agent_logging()`；`main.py:95` `setup_agent_logging(debug_mode=…)`（仍未传 `enable_file`） |
| 独立日志系统 `agent/log_system/` 的默认落盘路径存在但**没有路由事件写入者** | `agent/log_system/storage.py:50-51` `DEFAULT_DB_PATH=data/logs/yunshu_logs.db`、`DEFAULT_RAW_DIR=data/logs/raw`；实测该 db 仅 4096 B 且 `raw/` 为空 |
| `.env` 确实被加载进 `os.environ`（故 `AGENT_HYBRID_ALPHA`/`ORCHESTRATOR_REJECT_ENABLED` 生效） | `app_server.py:48-52` `get_env_config_manager().reload()` → `agent/env_config_manager.py:367 def reload` / `:387 os.environ[k] = v` |
| **审计时刻无服务在跑** | `Get-NetTCPConnection -LocalPort 5678` 空；`Get-Process python*` 空 ⇒ 无法现取 `/metrics` |

### 6.5 明确结论：现在能不能出数？

| 目标指标 | 能否出数 | 缺什么 |
|---|---|---|
| 规则命中率 | **不能（不可靠）** | 分子 `rule` 有埋点；分母需「本层被尝试次数」。当前分母只能靠 `RouteTraffic.attempts`（内存，且实测 0 条 summary 落盘）或自己拿 `llm` 计数反推。**没有持久化 sink** |
| 模板命中率 | **不能** | 分子在磁盘上为 **0 条**（114 条里 template=0）。埋点存在但从未触发/从未落盘 |
| 检索命中率 | **不能** | ① hybrid 返回的是「检索 ∪ 类别兜底」并集（`tool_router_hybrid.py:1709-1717`），返回值里无命中来源标签；② `tool_retrieval` 事件只有 `tools_preview`（top10 工具名）+ 三个候选计数，**没有 gold 标注**，无法算准确率/召回率；③ 事件仅 10 个日志文件有，且 app 不落盘 |
| 零召回率 | **不能** | 「零召回」在代码里没有独立事件。最接近的是 `tool_router_hybrid.py:1700-1701`（`if not results: return None`，**无日志**）与 `:1743`/`:1757-1758`（同样 **无日志**）⇒ 零召回/白名单空/截断空三者**静默不可观测** |
| 澄清率 | **不能** | 澄清机制不存在（见 §7），无事件、无计数器 |
| 层占比（rule/template/semantic/llm/reject） | **能算但样本极小** | 有 `yunshu_intent_layer_total` + `intent_layer.metric_recorded` 日志；实测全盘仅 114 条，且 68% 是 `llm` |

**核心缺口（按修复优先级）**：
1. **持久化 sink 缺失**（P0）：web 服务路由日志只进 stderr，无轮转文件、无 jsonl/DB 落表。修复点 `app_server.py:128` 或让 `RouteTraffic.snapshot()` 周期性落盘。
2. **分母不闭合**（P0）：`planning` 与 `llm` **对同一请求双重计数**（`orchestrator.py:1312` 在 `:1506-1517` 之前无条件执行；只有 wire 路的 `:1311 if not _wire_planning_used:` 做了互斥），破坏 `prometheus.py:756` 注释声称的「ratio 总和始终 = 1.0」。
3. **零召回/空结果静默**（P0）：`tool_router_hybrid.py:1698-1701, 1743, 1757-1758` 四条 return 路径无任何日志或事件。
4. **`tool_retrieval` 无「命中来源」标签**（P1）：无法拆「检索命中 vs 类别兜底」。
5. **`tool_retrieval` 无请求级关联**（P1）：只记 `query_hash`，不记 `trace_id`，无法与 `route_decision`/`llm` 关联成一条链路。

---

## 7. 澄清机制与零召回出口

### 7.1 澄清（clarify）机制：**不存在**

* 全仓库 `clarif|Clarif` 模式命中 **0 处**（含 tests/、docs/、scripts/）。
* 中文「澄清」在 `agent/**/*.py` 命中 **0 处**；全仓库 19 处全在 `docs/` 与 `scripts/` 的说明文本里，**无一是运行时逻辑**。
* 路由链路没有任何「请求用户补充信息后重新路由」的分支：所有 `return` 点都是 `ResponseBuilder.success/workflow_result/guard_blocked` 或 `return None`（下沉）。

### 7.2 零召回的出口：拒识 → 而拒识已被关闭

| 环 | 位置 | 实测状态 |
|---|---|---|
| 零召回判定(a) 输入过短 | `orchestrator.py:1140` `ORCHESTRATOR_REJECT_MIN_LENGTH`（默认 3）；`:1144` `_len_reject = (not _is_ellipsis and len(user_input.strip()) < _reject_min_len)` | **生效**（不受 reject 总开关影响，见 `config.yaml:620` 注释） |
| 零召回判定(b) 规则+语义双未命中 | `orchestrator.py:1149-1152` → `_should_reject`（`orchestrator.py:2834`） | **关闭** |
| 关闭开关 | `orchestrator.py:2863-2867` `if not cfg["enabled"]: return False, "reject_disabled"` | 配置：`config.yaml:625` `reject.enabled: false`；`.env:221` `ORCHESTRATOR_REJECT_ENABLED=false` |
| 拒识响应 | `orchestrator.py:1179-1210`（`_record_intent_layer("reject")` + WARNING 日志 + `log_layer_result(LAYER_REJECT, DECISION_REJECT)`） | 实测 `reject` 层记录 **0 条** |
| 指代句豁免 | `orchestrator.py:1141, 1154-1155` | 生效 |

⇒ **当前部署的实际行为是「硬猜/下沉 LLM」**：规则层 miss → 模板层 miss → 工作流学习层 miss → 语义层 miss（或分低于 `min_score=0.3`）→ **直接调用 LLM**（`orchestrator.py:1312`），且因为 `reject.enabled=false`，**没有任何"我不知道，请你说清楚"的出口**。

### 7.3 唯一的近似替代：`_FALLBACK_MSG`

| 项 | 位置 |
|---|---|
| 常量定义 | `agent/orchestrator/orchestrator.py:413` `_FALLBACK_MSG = (...)` |
| 触发 | `orchestrator.py:1448` `if not _wire_planning_used and _llm_confidence == "low":` |
| 埋点 | `orchestrator.py:1457` `_record_intent_layer("llm_low_confidence_fallback")` |
| 实测样本量 | 全仓库 **4 条** |

⇒ 这是**LLM 已经答完之后的低置信度兜底**（`orchestrator.py:1444-1446` 注释自述「返回统一文案 + 转人工建议」），不是路由期澄清。

### 7.4 其它「零召回」静默点（需要新增埋点）

| 位置 | 行为 | 是否有日志 |
|---|---|---|
| `agent/tool_router_hybrid.py:1698-1699` | `if results is None: return None`（检索失败） | ❌ 无 |
| `agent/tool_router_hybrid.py:1700-1701` | `if not results: return None`（**真零召回**） | ❌ 无 |
| `agent/tool_router_hybrid.py:1742-1743` | 白名单过滤后空 | ❌ 无 |
| `agent/tool_router_hybrid.py:1757-1758` | 排序/截断后空 | ❌ 无 |
| `agent/tool_router_hybrid.py:1660-1661` | 检索器不可用（`retriever is None or not retriever.available`） | ❌ 无 |
| `agent/orchestrator/orchestrator.py:2393-2406` | 语义层零匹配 → `DECISION_MISS`（DEBUG 级，`:2397`） | ⚠ 仅 DEBUG，INFO 级别下不输出 |

---

## 8. P0 六指标可算性矩阵

| # | 指标 | 现在能否算 | 数据来源（实测） | 必须新增的埋点 |
|---|---|---|---|---|
| 1 | **路由准确率** | ❌ **不能** | 无 gold 标注；`tool_router_test_report.json` 只是功能测试 pass/fail（15/15），不含路由正确性 | ① 每请求的 `final_layer` + 选中工具集（已有 `route_decision.layer_results`，需持久化）；② 人工/自动 gold 集；③ 检索 vs 类别兜底的来源标签 |
| 2 | **误召率** | ❌ **不能** | 无「召回了但不该召回」的判定数据；`record_tool_selection` 只记 `tools_preview`（`tool_trace.py:497`）无正确性 | 同 #1，且需「每轮实际被 LLM 采纳的工具」回写（当前 `tool_traces` 表有 `tool_name`，但**不区分该工具是否由路由选中**） |
| 3 | **漏召率** | ❌ **不能** | 零召回路径**静默**（§7.4 六处无日志） | 在 `tool_router_hybrid.py:1698/1700/1742/1757/1660` 五处补 `action='tool_retrieval.empty'` + `reason` 事件 |
| 4 | **平均路由深度** | ⚠ **勉强能**（样本 114） | `route_decision.layer_results` 的**键数**即深度（`routing_observability.py:291`）。实测样本见 §6.3 的原文：一层请求 `layer_results` 有 4 个键。但只有 110 条记录且不落盘 | ① 持久化；② 显式输出 `route_depth` 字段 |
| 5 | **P95 端到端延迟** | ⚠ **部分能** | ① `route_decision.duration_ms` = 请求总耗时（`routing_observability.py:293`，`ctx.duration_ms` 自 `RouteContext.init` 起算 `:173-173`）——**仅 110 条样本**；② HTTP 层：`PrometheusMetrics(app, defaults_prefix='yunshu', group_by='endpoint')`（`app_server.py:243-247`）会导出 `yunshu_http_request_duration_seconds`（Histogram，labels `method/endpoint/status`，命名规则见 site-packages `prometheus_flask_exporter/__init__.py:410,430`）——**可算 P95 但仅进程内、重启归零**；③ `agent/data/tool_trace.db:unified_traces.duration_ms` 实测 p50=29.57ms / p95=2729.96ms / max=10646.66ms（3716 行）——**但这是工具/任务级 span，不是端到端请求** | ① 路由层延迟直方图（当前 `log_layer_result` 的 `duration_ms` 只进日志，**没有任何 Histogram**）；② 端到端 Histogram 落 Prometheus |
| 6 | **每日成本 + 缓存命中率** | ❌ **不能** | ① `unified_traces`：`SUM(cost_usd)=0.0`、`SUM(input_tokens)=SUM(output_tokens)=SUM(total_tokens)=0`（3716 行**全零**）⇒ 该表**从不回填成本**；② `agent/llm_monitor.py:340-344` 有 `estimated_cost_usd`，但那是**进程内 records 列表的即时统计**（`:318-345`），无落盘；③ 缓存命中率：`agent/llm_response_cache.py:231` 有 `hit_rate`，但 `llm_monitor` 的 `cache_hit` 字段（`:78`）**无 Prometheus 指标**；④ `config_audit.jsonl`(12.4MB) 只记配置访问/修改 | ① `llm_monitor` records 落 jsonl；② `yunshu_llm_tokens_total` / `yunshu_llm_cost_usd_total` Counter + `cache_hit` label；③ `skill_match_count_total` 已带 `method` label，可复用作「检索方法命中」但不能代表 LLM 缓存 |

---

## 9. Prometheus exporter 位置与现有指标名清单

### 9.1 Exporter 位置

| 组件 | 位置 | 说明 |
|---|---|---|
| **Web 服务主 exporter** | `app_server.py:241-247` `PrometheusMetrics(app, defaults_prefix='yunshu', group_by='endpoint')`（来自 `prometheus_flask_exporter`，`app_server.py:100` 导入） | 自动注册 `/metrics`（endpoint 名 `prometheus_metrics`），用**默认 REGISTRY**（`app_server.py:249-250`，注释见 `:1591-1594`） |
| **独立 exporter 类** | `agent/monitoring/prometheus.py:39` `class PrometheusMetricsExporter`；`:406` `start_http_server(self.port)`；工厂 `:476` `create_exporter_from_digital_life` | 默认 `namespace="Yunshu"`、`port=8000`（`:46`）。**生产路由链路未实例化它**（唯一 `port` 用法在 `agent/server_routes/routes_logging.py:93`，`port=0`） |
| **健康 Gauge 动态注册** | `agent/server_routes/routes_health.py:44-59` `_get_health_gauge` | 按名动态建 Gauge |
| **业务指标（非 prometheus_client）** | `agent/monitoring/metrics.py:44` `class MetricsCollector`；`:249` `_global_collector` | 自管理字典，**不支持 histogram_quantile**（注释 `prometheus.py:130` 自述） |
| **学习 KPI 只读 API** | `agent/learning_metrics_api.py:27` `GET /api/learning/metrics` | 消费 `LearningMetrics.get_snapshot()` |

**实测：审计时刻 5678 端口无监听、无 python 进程** ⇒ 无法现取 `/metrics`，下述清单来自**静态代码枚举**。

### 9.2 现有指标名清单（代码枚举，非 /metrics 实测）

**A. `agent/monitoring/prometheus.py` 模块级（10 个）**

| 指标名 | 类型 | labels | 定义行 |
|---|---|---|---|
| `yunshu_safe_file_reader_errors_total` | Counter | 有 | `:588` |
| `yunshu_safe_file_reader_encoding_fallbacks_total` | Counter | 有 | `:594` |
| `yunshu_safe_file_reader_read_duration_seconds` | Histogram | 有 | `:600` |
| `yunshu_safe_file_reader_loaded_history_count` | Gauge | 有 | `:617` |
| `yunshu_safe_file_reader_invalid_ratio` | Gauge | 有 | `:623` |
| `skill_match_latency_ms` | Histogram | `layer, method, success` | `:663` |
| `skill_match_count_total` | Counter | `layer, method, success` | `:672` |
| `yunshu_intent_layer_total` | Counter | `layer` | `:718` |
| `yunshu_intent_layer_ratio` | Gauge | `layer` | `:724` |
| `context_assembler_injected_total` | Counter | — | `:786` |
| `context_assembler_degraded_total` | Counter | — | `:792` |
| `context_assembler_duration_ms` | Histogram | — | `:798` |
| `context_assembler_injected_tokens` | Gauge | — | `:806` |

**B. `PrometheusMetricsExporter` 实例级（namespace 默认 `Yunshu`，前缀 `Yunshu_`）**

`Yunshu_v2_module_load_duration_seconds`(`:79`)、`Yunshu_v2_module_load_total`(`:86`)、`Yunshu_v2_module_enabled`(`:92`)、`Yunshu_interaction_total`(`:98`)、**`Yunshu_interaction_duration_duration_seconds`**(`:103`，⚠ 名字里 `duration` 重复，疑为命名缺陷)、`Yunshu_conversations_total`(`:113`)、`Yunshu_active_connections`(`:121`)、`Yunshu_memory_count`(`:126`)、`Yunshu_alert_total`(`:131`)、`Yunshu_error_total`(`:139`)、`Yunshu_error_retry_total`(`:144`)、`Yunshu_circuit_breaker_state`(`:149`)、`Yunshu_ci_pipeline_duration_seconds`(`:157`)、`Yunshu_ci_test_coverage_percent`(`:161`)、`Yunshu_ci_test_failures_total`(`:165`)、`Yunshu_ci_build_failures_total`(`:169`)、`Yunshu_ci_pipeline_runs_total`(`:173`)、`Yunshu_deployment_status`(`:181`)、`Yunshu_deployment_duration`(`:186`)、`Yunshu_deployment_failures`(`:191`)、`Yunshu_deployment_total`(`:195`)、`Yunshu_rollback_total`(`:200`)。

**C. `app_server.py` 自定义（`defaults_prefix='yunshu'` 上下文）**

`yunshu_security_blocks_total`(`:254`)、`yunshu_llm_calls_total`(`:261`)、`yunshu_user_logins_total`(`:268`)、`yunshu_api_calls_total`、`yunshu_conversations_total`、`yunshu_tool_calls_total`、`yunshu_cpu_usage_percent`、`yunshu_memory_usage_percent`(`:301`)、`yunshu_active_connections`(`:307`)（`:269-310` 区间）。
**HTTP 默认集**（由库自动生成，实测命名规则 `prometheus_flask_exporter/__init__.py:410` `prefix = prefix + "_"`，`:430`/`:448`/`:455`）：
`yunshu_exporter_info`、`yunshu_http_request_total`(labels `method,status`)、`yunshu_http_request_duration_seconds`(Histogram, labels `method,endpoint,status`)、`yunshu_http_request_exceptions_total`。

**D. `agent/server_routes/routes_chat.py`（重复注册同名的 `yunshu_security_blocks_total` / `yunshu_llm_calls_total`，用 try/except 吞重复注册）**

`yunshu_security_blocks_total`(`:113`)、`yunshu_llm_calls_total`(`:118`)、`yunshu_voice_entry_unassigned_total`(`:125`)。

**E. 其它模块**

`agent/log_system/dashboard.py:278-280`：`yunshu_log_total`、`yunshu_log_errors_total`、`yunshu_log_insights_pending`。
`agent/utils/perf_monitor.py:762-782`：`log_dict_call_duration_seconds`、`log_dict_calls_total`、`log_dict_speedup_ratio`、`log_dict_improvement_pct`。

### 9.3 **没有的**指标（Q5 需新增）

* 无任何 `routed_layer` / `route_depth` / `template_hit` / `rule_hit` / `zero_recall` / `clarify` 指标。
* 层级计数只有 `yunshu_intent_layer_total{layer}` 一个（Counter，无对应 Histogram 记层耗时）。
* 工具检索只有日志事件 `tool_retrieval`，**无 Prometheus 指标**。
* 无 token / cost 的 Prometheus 指标（`yunshu_llm_calls_total` 只有 `provider/model/status`）。

---

## 10. 附录：证据索引（文件:行号）

```
agent/orchestrator/orchestrator.py:75       _BOUNDED_RELEVANCE_KEYS
agent/orchestrator/orchestrator.py:313      _emit_learning_metric
agent/orchestrator/orchestrator.py:368      _record_intent_layer (统一埋点入口)
agent/orchestrator/orchestrator.py:413      _FALLBACK_MSG
agent/orchestrator/orchestrator.py:614      def process
agent/orchestrator/orchestrator.py:649      _emit_learning_metric("record_interaction")
agent/orchestrator/orchestrator.py:727      WorkflowEngine.try_match  ← L1
agent/orchestrator/orchestrator.py:751      _record_intent_layer("rule")
agent/orchestrator/orchestrator.py:870-880  IntentRouter.classify   ← L2
agent/orchestrator/orchestrator.py:923      ResponseTemplates.for_intent
agent/orchestrator/orchestrator.py:971      _record_intent_layer("template")
agent/orchestrator/orchestrator.py:1004     _workflow_learning_layer_match ← L2.5
agent/orchestrator/orchestrator.py:1080     _record_intent_layer("workflow_learning")
agent/orchestrator/orchestrator.py:1134-1178 拒识检查              ← L4
agent/orchestrator/orchestrator.py:1179     _record_intent_layer("reject")
agent/orchestrator/orchestrator.py:1290     _record_intent_layer("planning")  (wire 路)
agent/orchestrator/orchestrator.py:1311-1312 _record_intent_layer("llm")
agent/orchestrator/orchestrator.py:1345     _record_intent_layer("llm_error")
agent/orchestrator/orchestrator.py:1448-1457 llm_low_confidence_fallback
agent/orchestrator/orchestrator.py:1516     _record_intent_layer("planning")  (旧路, 双重计数点)
agent/orchestrator/orchestrator.py:1776-1784 _SEM_DEFAULTS
agent/orchestrator/orchestrator.py:1953     def _workflow_learning_layer_match
agent/orchestrator/orchestrator.py:2324     def _semantic_layer_match         ← L3
agent/orchestrator/orchestrator.py:2374-2383 svc.loader.match(...)
agent/orchestrator/orchestrator.py:2550     _record_intent_layer("semantic")
agent/orchestrator/orchestrator.py:2607     _record_intent_layer("semantic_failed")
agent/orchestrator/orchestrator.py:2834     def _should_reject
agent/orchestrator/orchestrator.py:2863-2867 reject.enabled 短路
agent/orchestrator/orchestrator.py:3258     def _call_llm
agent/orchestrator/orchestrator.py:3452     hybrid_select_tools(...) or get_tools_for_input(...)
agent/orchestrator/orchestrator.py:4010     def _call_llm_v2
agent/orchestrator/orchestrator.py:4103     hybrid_select_tools(...) (V2 路)
agent/orchestrator/orchestrator.py:4186     _emit_learning_metric("record_llm_tokens")

agent/orchestrator/routing_observability.py:36-38   ORCHESTRATOR_TRAFFIC_REPORT_INTERVAL
agent/orchestrator/routing_observability.py:41-49   LAYER_* 常量
agent/orchestrator/routing_observability.py:52-60   DECISION_* 常量
agent/orchestrator/routing_observability.py:110-127 RouteTraffic._report_locked (hit_rate)
agent/orchestrator/routing_observability.py:222-266 log_layer_result (字段契约)
agent/orchestrator/routing_observability.py:269-299 emit_route_decision

agent/orchestrator/task_dispatcher.py:48-50 第三条 hybrid_select_tools 调用点
agent/orchestrator/task_dispatcher.py:152-160 _is_smart_tool_selection_enabled (default=False)
agent/orchestrator/lifecycle_manager.py:427  WorkflowEngine() 单例

agent/workflow_engine/engine.py:103,114,120  WorkflowResult / WorkflowEngine / try_match
agent/workflow_engine/builtin_rules.py:58    register_builtin_rules

agent/response_workflows.py:184,278,286,316-341 IntentRouter / ResponseTemplates / for_intent 本体

agent/tool_router.py:548  classify_user_input
agent/tool_router.py:569  get_tools_for_input
agent/tool_router.py:641-645 record_tool_selection 调用
agent/tool_router.py:702  _apply_alias_merge_and_priority_sort

agent/tool_router_hybrid.py:59    _DEFAULT_ALPHA = 0.5
agent/tool_router_hybrid.py:67    _COSINE_CUTOFF = 0.2
agent/tool_router_hybrid.py:96    _BM25_HALF_SATURATION = 5.5375
agent/tool_router_hybrid.py:115   _MIN_IDF_COVERAGE = 0.2
agent/tool_router_hybrid.py:156-179 _resolve_alpha_from_env
agent/tool_router_hybrid.py:474   BM25Index.__init__(k1=1.5,b=0.75)
agent/tool_router_hybrid.py:54?(见1296) idf 覆盖率护栏
agent/tool_router_hybrid.py:1282  _calibrate_bm25_scores
agent/tool_router_hybrid.py:1304  _calibrate_cosine_scores
agent/tool_router_hybrid.py:1327  _min_max_normalize (已弃用于融合)
agent/tool_router_hybrid.py:1361-1362 alpha 优先级
agent/tool_router_hybrid.py:1532-1550 _last_query_stats (中间统计)
agent/tool_router_hybrid.py:1552-1575 融合主循环 ★公式
agent/tool_router_hybrid.py:1632  hybrid_select_tools
agent/tool_router_hybrid.py:1690-1695 候选池放大
agent/tool_router_hybrid.py:1698-1701 零召回静默返回
agent/tool_router_hybrid.py:1709-1717 检索 ∪ 类别兜底并集
agent/tool_router_hybrid.py:1742-1743, 1757-1758 空结果静默返回
agent/tool_router_hybrid.py:1770-1790 record_tool_retrieval 调用

agent/tool_router_reranker.py:209,533,538,544,557  ToolReranker (无生产调用者)

agent/observability/tool_trace.py:481-497 record_tool_selection
agent/observability/tool_trace.py:524-559 record_tool_retrieval
agent/observability/tool_trace.py:48      _DEFAULT_DB_PATH = agent/data/tool_trace.db

agent/skills_mgmt/loader.py:126-144 _record_skill_match_prometheus
agent/skills_mgmt/loader.py:816     _RRF_K = 60
agent/skills_mgmt/loader.py:830     _RRF_QUALITY_MIN = 0.3
agent/skills_mgmt/loader.py:866-982 _rrf_fuse (双路)
agent/skills_mgmt/loader.py:987-991 _DEFAULT_RETRIEVAL_WEIGHTS
agent/skills_mgmt/loader.py:1143-1173 _get_default_weights (env > yaml > 硬编码)
agent/skills_mgmt/loader.py:1175-1300 _rrf_fuse_weighted (三路加权)
agent/skills_mgmt/loader.py:1507-1522 融合分支选择
agent/skills_mgmt/bm25_searcher.py:135,250
agent/skills_mgmt/vector_adapter.py:638
agent/skills_mgmt/searcher.py:65     (技能管理页搜索，非路由链路)

agent/learning_metrics.py:95,128,145 record_interaction / record_semantic_query / record_llm_tokens
agent/learning_metrics.py:236-327   get_snapshot (7 KPI)
agent/learning_metrics.py:69-89     全内存计数器
agent/learning_metrics_api.py:27    GET /api/learning/metrics

agent/monitoring/prometheus.py:39,406,476 PrometheusMetricsExporter / start_http_server
agent/monitoring/prometheus.py:663,672    skill_match_latency_ms / skill_match_count_total
agent/monitoring/prometheus.py:718,724    yunshu_intent_layer_total / _ratio
agent/monitoring/prometheus.py:737-775    record_intent_layer / reset_intent_layer_counts
agent/monitoring/prometheus.py:756        "ratio 总和始终 = 1.0" (被 planning 双计破坏)
agent/monitoring/metrics.py:44,249        MetricsCollector
agent/server_routes/routes_health.py:44-59, 513
agent/server_routes/routes_logging.py:91-93
agent/utils/perf_monitor.py:762-782
agent/log_system/storage.py:50-51         data/logs/yunshu_logs.db / data/logs/raw

agent/logging_utils.py:441,479-513        setup_agent_logging(enable_file=False 默认)
app_server.py:48-52                       .env → os.environ
app_server.py:100,128,241-247,249-250,254,261,268,301,307,313,1591-1594
main.py:25-28,95                          setup_agent_logging 唯一生产调用者
start_yunshu.bat:28                       python app_server.py

config.yaml:581-597   orchestrator.semantic_layer
config.yaml:606-612   orchestrator.workflow_learning_layer
config.yaml:623-629   orchestrator.reject (enabled: false)
config.yaml:684-701   skills_mgmt.retrieval (+ fusion.weights)
.env:100,119,148,221,247,200
agent/data/system_prompt_config.json:3-4  smart_tool_selection.enabled = true
agent/env_config_manager.py:367,387       reload → os.environ
agent/data/tool_router_test_report.json   15/15 功能测试（非命中率）

实测数据（本次审计生成）
  logs/**/*.log  中 intent_layer 记录 = 114 条（llm 78 / llm_error 12 / planning 10 /
                 semantic 4 / llm_low_confidence_fallback 4 / rule 2 /
                 semantic_failed 2 / workflow_learning 2 / template 0 / reject 0）
  logs/**/*.log  中 route_decision = 110 条；traffic.summary = 0 条
  data/logs/2026-09-19.jsonl 中 route_decision = 0、intent_layer = 0
  agent/data/tool_trace.db: tool_traces=1072 行 / unified_traces=3716 行 /
                 SUM(cost_usd)=0.0 / SUM(total_tokens)=0 /
                 duration_ms p50=29.57 p95=2729.96 max=10646.66
  5678 端口无监听、无 python 进程（审计时刻）
```
