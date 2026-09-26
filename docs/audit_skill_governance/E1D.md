# E1-D · 下发集跨进程非确定性：复现 / 定位 / 修复，兼 AGENT_HYBRID_EMBEDDING 登记收口

| 项 | 值 |
|---|---|
| 基线 HEAD | `5c9ace10a4ca4bb96860db3a48debf9ddcf496bf` |
| 本卡改动文件 | `agent/tool_router_hybrid.py`(+161 行)、`agent/settings/registry.py`(+15 行)、`tests/unit/test_tool_router_hybrid_e1d_determinism.py`(新)、`tests/unit/test_settings_registry_e1d.py`(新) |
| 探针位置（仓库外） | `C:\Users\Administrator\AppData\Local\Temp\e1d\` |
| 出网 | 零（全程只用本地索引 + 本地缓存模型） |
| 结论 | **是——生产上同一查询跨进程可能下发不同的工具集**（成员不同，不只顺序） |

---

## 0. 结论（先给答案）

1. **是。** 同一查询、同一索引、同一份代码，不同进程下发的**工具集成员**可能不同。
   在 `AGENT_HYBRID_EMBEDDING=0`（向量 worker 从未拉起、`degraded=True`、无子进程）下
   **同样复现** ⇒ **与向量腿无关**：成因是融合入口用 `set` 汇合候选
   （`all_candidates`）＋ 下发阶把 `set` 交给 helper，而两处的排序都是**稳定排序**
   ⇒ 分数/优先级**并列**的先后直接等于 **set 迭代序 = 字符串哈希随机化**
   ⇒ 截断点落在并列块内部时，**成员**随进程变。
2. 在真实用例集（50 条 × 种子 0/1/2，纯 BM25）上：**成员抖动只出现在 rc-007 / rc-046**
   （各差 2 条），**顺序抖动 50/50**，**top1 抖动 0/50**。这与 E1-C 的原始读数
   （"两次扫描决策层/τ 全同，但下发层差 1~3 条；rc-007/rc-046 的下发集成员不同"）
   **逐条吻合** —— E1-C 没有看错，只是把成因归给了向量腿。
3. E1-C 的另一条怀疑（**向量腿就绪/预热时序**）**不是**这条抖动的成因，但它**是另一件
   真实的事**：冷进程（预热未完成）与热进程的候选池是 **3 vs 40**、**top1 都不同**
   （§1.4）。两件事必须分开记账：前者是**缺陷（已修）**，后者是**状态差异**（未修，
   见 §5 残留风险）。

---

## 1. 第 1 步：跨种子测量（先测量，不先改）

### 1.1 装置（仓库外，全部走**生产入口**）

| 文件 | 作用 |
|---|---|
| `%TEMP%\e1d\payload_probe.py` | 子进程：给定 `PYTHONHASHSEED` 跑一次生产入口，打印一行 JSON |
| `%TEMP%\e1d\run_seeds.py` | 同一查询 × 多个种子，逐种子一行 + 一致性判定 |
| `%TEMP%\e1d\run_matrix.py` | 50 条用例 × 3 种子 → `matrix_{before,after}.json` |
| `%TEMP%\e1d\make_tie_index.py` | 合成**必然并列**索引（真实 90 工具 + 40 个描述逐字相同的探针工具） |
| `%TEMP%\e1d\setorder_probe.py` | 机制直证：同一批字符串的 `set` 迭代序随种子变 |

生产入口（**没有重写一份截断逻辑**）：
* 下发层 = `agent.tool_router_hybrid.hybrid_select_tools(query, None, max_tools=25, top_k=40)`
* 决策层 = `HybridRetriever.query(query, top_k=40)`
* 每个种子 = **新解释器进程**（`PYTHONHASHSEED` 必须在解释器启动前设好）

### 1.2 证据 A：必然并列的合成索引（40 个描述逐字相同的工具）

「描述逐字相同 ⇒ 词频/文档长度完全一致 ⇒ BM25 raw 分**严格相等**」；
40 > 候选池截断点 25 ⇒ 截断点必然落在并列块内部。
命令（向量腿 **关闭**）：

```
python %TEMP%\e1d\run_seeds.py --index %TEMP%\e1d\synth_tie_index.json \
  --queries "tieprobezeta 并列探针" --seeds 0,1,2,random,random \
  --top-k 40 --max-tools 25 --off
```

**原始输出（改前，`%TEMP%\e1d\tie_before.txt`）**：

```
  seed=0       n=25 degraded=True  top1=tieprobe06       payload=["get_status", "search_memory", "remember", "tieprobe06", "tieprobe04", "tieprobe16", "tieprobe24", "tieprobe19", "tieprobe29", "tieprobe31", "tieprobe38", "tieprobe15", "tieprobe13", "tieprobe05", "tieprobe27", "get_sensor_summary", "todo_write", "tieprobe10", "tieprobe33", "tieprobe17", "tieprobe39", "tieprobe18", "tieprobe35", "tieprobe02", "tieprobe30"]
  seed=1       n=25 degraded=True  top1=tieprobe30       payload=["get_status", "search_memory", "remember", "tieprobe30", "tieprobe05", "tieprobe13", "tieprobe31", "tieprobe12", "tieprobe17", "tieprobe34", "tieprobe35", "tieprobe33", "tieprobe37", "tieprobe40", "tieprobe24", "get_sensor_summary", "todo_write", "tieprobe11", "tieprobe20", "tieprobe10", "tieprobe14", "tieprobe25", "tieprobe39", "tieprobe06", "tieprobe27"]
  seed=2       n=25 degraded=True  top1=tieprobe22       payload=["get_status", "search_memory", "remember", "tieprobe22", "tieprobe32", "tieprobe29", "tieprobe11", "tieprobe25", "tieprobe20", "tieprobe33", "tieprobe05", "tieprobe09", "tieprobe02", "tieprobe26", "tieprobe39", "get_sensor_summary", "todo_write", "tieprobe34", "tieprobe27", "tieprobe08", "tieprobe23", "tieprobe16", "tieprobe01", "tieprobe17", "tieprobe18"]
  seed=random  n=25 degraded=True  top1=tieprobe02       payload=["get_status", "search_memory", "remember", "tieprobe02", "tieprobe31", "tieprobe11", "tieprobe23", "tieprobe37", "tieprobe20", "tieprobe16", "tieprobe18", "tieprobe35", "tieprobe09", "tieprobe04", "tieprobe01", "todo_write", "get_sensor_summary", "tieprobe34", "tieprobe12", "tieprobe36", "tieprobe05", "tieprobe17", "tieprobe08", "tieprobe22", "tieprobe28"]
  seed=random  n=25 degraded=True  top1=tieprobe23       payload=["get_status", "search_memory", "remember", "tieprobe23", "tieprobe37", "tieprobe21", "tieprobe15", "tieprobe03", "tieprobe12", "tieprobe04", "tieprobe14", "tieprobe31", "tieprobe34", "tieprobe32", "tieprobe17", "todo_write", "get_sensor_summary", "tieprobe18", "tieprobe11", "tieprobe02", "tieprobe33", "tieprobe13", "tieprobe06", "tieprobe36", "tieprobe25"]
  => 跨种子: 集合不一致(5种) | 序列不一致(5种) | top1不一致(5种)
     与 seed=1 的集合对称差(16): ['tieprobe02', 'tieprobe04', 'tieprobe11', 'tieprobe12', 'tieprobe14', 'tieprobe15', 'tieprobe16', 'tieprobe18', 'tieprobe19', 'tieprobe20', 'tieprobe25', 'tieprobe29', 'tieprobe34', 'tieprobe37', 'tieprobe38', 'tieprobe40']
     与 seed=2 的集合对称差(22): [...22 条...]
     与 seed=random 的集合对称差(24): [...24 条...]
     与 seed=random 的集合对称差(22): [...22 条...]
```

**读法**：**决策层 top1 本身就随种子变**（tieprobe06/30/22/02/23），
下发集对称差最大 **24/25**。这不是"顺序不同"，是**成员不同**。

### 1.3 证据 B：真实用例集（复现 E1-C 的读数）

命令：`run_matrix.py --tag before --seeds 0,1,2 --off`（50 条 rc-*，向量腿关闭）。
汇总原始输出（`%TEMP%\e1d\before.log` 末尾）：

```
== tag=before cases=50 成员跨种子抖动=2 ['rc-007', 'rc-046']
== tag=before 顺序跨种子抖动=50
== tag=before top1 跨种子抖动=0 []
```

逐种子原始行（`%TEMP%\e1d\evidence_real.txt`，节选）：

```
### 改前 rc-007 | query=用 subagent 模式执行这个实现计划，产物写成 report.md | AGENT_HYBRID_EMBEDDING=0
seed=0      degraded=True  n_ranked=3   n=26 set#e540dd66 seq#e4239f1d top1=search_files
   ranked_top10=["search_files", "delegate", "distill_process_from_knowledge"]
seed=1      degraded=True  n_ranked=3   n=26 set#fb281f0e seq#00a8f727 top1=search_files
   ranked_top10=["search_files", "delegate", "distill_process_from_knowledge"]
seed=2      degraded=True  n_ranked=3   n=26 set#50a64119 seq#13582cd1 top1=search_files
   ranked_top10=["search_files", "delegate", "distill_process_from_knowledge"]
### 改前 rc-046 | query=把大文件处理放到后台，别卡住对话 | AGENT_HYBRID_EMBEDDING=0
seed=0      degraded=True  n_ranked=3   n=25 set#7e5a48ae seq#b1669ced top1=submit_task
seed=1      degraded=True  n_ranked=3   n=25 set#e4670fcf seq#f0b38fad top1=submit_task
seed=2      degraded=True  n_ranked=3   n=25 set#e4670fcf seq#dcf093a3 top1=submit_task
```

**关键对照**：rc-007/rc-046 的**决策层**（`ranked_top10` 与 `n_ranked`）三种子**逐位相同**，
而**下发集成员**不同（对称差各 2 条：rc-007 在 {run_tests, git, data_format_detect} 之间换位；
rc-046 在 {cancel_task, list_async_tasks} 之间换位）—— 这正是 E1-C 的那句话。
⇒ 成因不在融合排序，而在**下发阶**（候选集合 → 优先级并列 → 截断）。

### 1.4 证据 D：向量腿假设的检验（冷/热对照，同一种子序列）

| 运行 | `degraded_at_query` | `n_ranked` | `embed_candidates` | top1 | 下发集 set# |
|---|---|---|---|---|---|
| 冷（构造后**立刻**查询，预热未完成） | `True` | 3 | 0 | `search_files` | `e540dd66` / `fb281f0e`（两种子已不同） |
| 热（`--wait-embedding 240`，`waited_sec=20.01`） | `False` | 40 | 65 | `distill_process_from_knowledge` | `ebe7a00e`（两种子相同，序列仍不同：`e88dab84` vs `3dd8aae8`） |

原始行见 `%TEMP%\e1d\embed_cold.txt` / `embed_warm.txt`。三点结论：

1. **生产默认态（env 未设）在冷进程里就是纯 BM25**：预热是 daemon 线程，模型要 ~20 s，
   而第一问在几毫秒内就发生了 ⇒ `degraded=True`。这与 E1-C 记的"向量腿现在真的会加载模型"
   不矛盾，但意味着**"向量腿在不在"取决于这一问离启动有多近**。
2. 冷/热之间**不只是成员不同，top1 都不同**（`search_files` → `distill_process_from_knowledge`）
   —— 这解释了 E1-C 看到的"1~3 条"量级抖动：两次扫描落在不同就绪态时，差异远大于哈希抖动。
3. 但**在固定就绪态下**（关掉向量腿、纯 BM25）**依然**跨种子抖动（§1.2/§1.3）
   ⇒ 哈希序这条**是独立的缺陷**，不能被"时序"解释掉。

### 1.5 机制直证（不是推断）

```
$ foreach ($s in 0,1,2,random) { $env:PYTHONHASHSEED=$s; python %TEMP%\e1d\setorder_probe.py }
seed=0 set_head=['tieprobe06', 'tieprobe04', 'tieprobe16', 'tieprobe24', 'tieprobe19', 'tieprobe29', 'tieprobe31', 'tieprobe38']
seed=1 set_head=['tieprobe30', 'tieprobe05', 'tieprobe13', 'tieprobe31', 'tieprobe12', 'tieprobe17', 'tieprobe34', 'tieprobe35']
seed=2 set_head=['tieprobe22', 'tieprobe32', 'tieprobe29', 'tieprobe11', 'tieprobe25', 'tieprobe20', 'tieprobe33', 'tieprobe05']
seed=random set_head=['tieprobe21', 'tieprobe17', 'tieprobe02', 'tieprobe03', 'tieprobe24', 'tieprobe29', 'tieprobe31', 'tieprobe37']
```

`set(["tieprobe01".."tieprobe40"])` 的迭代序**逐位等于** §1.2 里各种子下发集的并列块先后
（seed0 → `tieprobe06`、seed1 → `tieprobe30`、seed2 → `tieprobe22` …）。
⇒ 因果链闭合：**set 迭代序 → 稳定排序的并列先后 → 截断点上的成员**。

### 1.6 第 1 步结论

> **同一个查询、同一份索引，跨进程是可能下发不同工具集的**（成员差异，非仅顺序）。
> 三条独立证据：(a) 合成并列索引上 top1 与 24/25 个成员随种子变；(b) 真实 50 条用例上
> rc-007/rc-046 成员随种子变而决策层逐位相同；(c) `set` 迭代序随种子变且与下发集并列块
> 顺序逐位一致。全部在 `AGENT_HYBRID_EMBEDDING=0`（无 worker、无子进程）下取得。
> ⇒ 这是**生产缺陷**，进入第 2 步修复。（E1-C 的"向量腿时序"另有其事，见 §1.4 与 §5。）

---

## 2. 第 2 步：修复（让下发集在给定输入下确定）

### 2.1 改了什么（本卡专属 diff）

⚠️ 仓库里那两份文件还叠着**别的卡的未提交改动**，直接 `git diff` 会把别人的行算进来。
故本卡的 diff 用「HEAD 同区域原文替换掉本卡改动区域」的方式生成，只含本卡的行：
`%TEMP%\e1d\my_tool_router.diff`（+161）、`%TEMP%\e1d\my_registry.diff`（+15）。
生成脚本：`%TEMP%\e1d\gen_e1d_diff.py`（反向副本 = `*.pre_e1d`，可直接复核）。

三处改动（**都没有改 `agent/tool_router.py` 一行** —— 它不在本卡文件范围）：

1. `HybridRetriever._query_locked` 融合入口：候选汇合由 `set` 改为**确定序列**
   （BM25 路序 → Embedding 路序，两路各自稳定），成员判定仍用集合；
2. 同一处排序：**主键不变**（分数降序），补**确定的次级键**（候选汇合序位置）：
   `fused.sort(key=lambda x: (-x[1], _cand_pos[x[0]]))`；
3. `hybrid_select_tools`：交给 helper 的第一个实参由 `set` 改为**有序候选序列**
   （相关度序 → 类别声明序 → 兜底字典序），类别集合同样改为有序表。

### 2.2 为什么次级键取"候选汇合序"而不是 "(-score, tool_name)"

「工具名字典序」也能消除非确定性，但它会在**分数并列处重排 BM25 本路**，而
"降级路上融合顺序必须与 raw BM25 顺序**逐位一致**"是既有回归的**明文契约**
（`tests/unit/test_tool_router_hybrid_fusion_calibration.py::test_degraded_path_order_matches_raw_bm25`）。
那等于把"修非确定性"做成"改本路排序"。候选汇合序**既确定、又与契约逐位相容**：
并列时谁在 BM25 路里靠前谁就靠前。回归已证该用例仍绿（§4）。

### 2.3 顺序 / 成员变化统计（改前 vs 改后，全部 50 条用例）

| 指标 | 改前（种子 0/1/2） | 改后（种子 0/1/2） |
|---|---|---|
| 成员跨种子抖动（同查询不同下发集） | **2 / 50**（rc-007、rc-046） | **0 / 50** |
| 顺序跨种子抖动 | **50 / 50** | **0 / 50** |
| top1 跨种子抖动 | 0 / 50 | 0 / 50 |
| 改前→改后**下发集顺序**变化（对改前 seed=0 基线） | — | **42 / 50** |
| 改前→改后**下发集成员**变化（同上基线） | — | **2 / 50** |

* 成员变化明细：`rc-007` 改后新增 `run_tests`、移除 `run_lint`；`rc-046` 改后新增 `list_async_tasks`。
  （"改前"没有唯一答案，故以 seed=0 那次采样作基线对比；它只是无数种哈希序里的一种。）
* 逐位完全相同：8 / 50。
* **顺序变化本身也要报告**（下游若按顺序消费工具表，模型看到的列表顺序会变）：
  42 条用例的顺序变了，其中 40 条是"并列块内部重排"，2 条同时伴随成员变化。
* 端到端判据未退化：`python scripts/eval_route_conflict.py --no-embedding` ⇒
  **决策层通过 27/50、下发层 expected 命中 40/50、退出码 0**（= `BASELINE[bm25_only]`，未降）。

### 2.4 非空转自证（把修复去掉 ⇒ 新测试必须变红）

脚本 `%TEMP%\e1d\mutation_selfproof.py`：备份 → 施加变异（去掉次级键、退回 `set` 迭代与
`set` 候选）→ 跑新测试 → **还原并校验字节相同** → 再跑一次。

```
已施加变异（去掉次级键 + 有序候选），跑新测试：
### 变异后（期望红）
E   AssertionError: 并列块顺序不是确定的候选汇合序 —— 回到了 set 迭代序（哈希随机化）
E     At index 0 diff: 'tieprobe10' != 'tieprobe01'
E   AssertionError: 同一查询 / 同一索引 / 同一份代码，跨进程下发了**不同的工具集**：
E       seeds=['0'] -> [..., 'tieprobe06', 'tieprobe04', ...]
E       seeds=['1'] -> [..., 'tieprobe30', 'tieprobe05', ...]
E       seeds=['2'] -> [..., 'tieprobe22', 'tieprobe32', ...]
E       seeds=['random'] -> [...]
E       seeds=['random'] -> [...]
E   assert 5 == 1
E   AssertionError: 候选又以 set 形式交给 helper 了 —— 同优先级并列的先后会退回哈希序…
FAILED ...::TestFusedOrderIsDeterministic::test_tie_block_keeps_candidate_order
FAILED ...::TestPayloadIsCrossProcessDeterministic::test_payload_is_identical_across_hash_seeds
FAILED ...::TestPayloadStageCandidateFeedIsOrdered::test_helper_receives_an_ordered_sequence
============================== 3 failed in 4.82s ==============================
已还原（字节级相同）
### 还原后（期望绿）
============================== 3 passed in 4.30s ==============================
变异后 pytest rc=1（非 0 = 新测试确实变红 ⇒ 非空转）
还原后 pytest rc=0（0 = 修复在位上）
```

还原的**字节级自证**：`live=53B3A41BB098F9AA4E7ADC86C83C8E97F779DC3C0B092746CFDDAC036A9E5A61` ==
`bak =53B3A41BB098F9AA4E7ADC86C83C8E97F779DC3C0B092746CFDDAC036A9E5A61` ⇒ `RESTORED-OK`。
（变异实验的完整输出：`%TEMP%\e1d\mutation.txt`、`mutation_变异后.txt`、`mutation_还原后.txt`。）

---

## 3. 残留 2：`AGENT_HYBRID_EMBEDDING` 登记收口

### 3.1 登记改动（本卡专属 diff，`%TEMP%\e1d\my_registry.diff`）

```diff
     _a("AGENT_HYBRID_RERANKER", CAT_SKILLS, False,
        "工具路由是否启用混合重排",
        owner="agent/tool_router_reranker.py"),
+    # 【E1-D】原登记：「工具路由混合检索的向量模型」/ 默认 "" —— **与代码事实相反**。
+    #   代码把它当**布尔开关**读（唯一判据 _resolve_embedding_env_override：
+    #   0/false/no/off ⇒ 关，1/true/yes/on ⇒ 开，其余 ⇒ 未表态），
+    #   向量模型名另有固定常量（_DEFAULT_MODEL），本 env **不参与选型**。
+    #   默认值取代码事实：env 缺席时 HybridRetriever.__init__ 照常预热 ⇒ 启用；
+    #   风险级保持 A（…不是拆除任何防护闸门，与同族 AGENT_HYBRID_RERANKER 同级）。
+    _a("AGENT_HYBRID_EMBEDDING", CAT_SKILLS, True,
+       "混合检索**向量腿开关**（不是模型名；向量模型是固定常量，本环境变量不选型）。"
+       "默认启用。关法：置 0/false/no/off —— 关到「抑制预热」这一层："
+       "HybridRetriever 构造时不再启动 EmbeddingIndex.preheat 子进程，本次进程不拉模型"
+       "（省约 18s/450MB），检索退化为纯 BM25（degraded=True，下发集相应变小）；"
+       "但**不是硬禁用**：若另有调用方直接触发 EmbeddingIndex.search/preheat"
+       "（内部会 _ensure_worker），向量腿仍会被拉起。置 1/true/yes/on 与默认同为启用。",
+       owner="agent/tool_router_hybrid.py"),
-    _a("AGENT_HYBRID_EMBEDDING", CAT_SKILLS, "",
-       "工具路由混合检索的向量模型",
-       owner="agent/tool_router_hybrid.py"),
```

三者与代码事实对齐：**类型** `str → bool`（默认值由 `""` 变 `True` 后 `_infer_type` 判为 bool）、
**默认值** `"" → True`（env 缺席时生产确实预热）、**描述**改为开关语义 + 关法 + 关闭程度。

### 3.2 死分支去向：**接上调用点**（不是删掉）

原状：这个 env 有**两处各自解析**——`_ensure_st_checked` 里的"0=禁用/1=启用"分支
（**全仓无生产调用点**，Q3 §11 / E1-F1 已证）与 `HybridRetriever.__init__` 里的预热 gate。
于是"能关向量腿"这条**权威声明挂在了一段没人调用的代码上**。

处置：抽出**唯一解析口** `_resolve_embedding_env_override() -> Optional[bool]`，
**生产路径（`HybridRetriever.__init__`）调用它**，`_ensure_st_checked` **复用同一个它**
（不再自己解析）。⇒ "0 = 关"这条语义从此在生产路径上**有实现、有调用点**，
登记表再也不会基于一段死代码写说明。

**为什么不干脆删掉整段探针链**：探针链（文件缓存 + 子进程探测）**有意**保持不接生产 ——
`data/.embedding_probe` 里躺着一条 **2026-07-23 的陈旧 `available=false`**（本卡复核：
该文件修改时间仍是 `2026-07-23 0:20:15`，未被我触碰），把它接上等于让一条过期读数
决定向量腿生死（E1-F1 的根因形态）。这一点已写进 `_ensure_st_checked` 的 docstring，
**不是**沉默保留。守卫：「test_env_name_is_parsed_in_exactly_one_place」把
"归属模块里该 env 名只能被解析一次"钉死，任何"第二份解析"回潮都会变红。

### 3.3 真实的关法（写给运维，= 登记描述与 docstring 的同源口径）

* **怎么关**：`AGENT_HYBRID_EMBEDDING=0`（或 `false/no/off`，大小写不敏感、允许两侧空白）。
* **关到什么程度**：**抑制预热**。「HybridRetriever` 构造时不再启动
  `EmbeddingIndex.preheat` daemon 线程 ⇒ **本进程内向量 worker 从不被拉起**
  ⇒ `retriever.degraded == True`、检索退化为纯 BM25（下发集相应变小）。
  E1-C 实测与本次复核一致：无子进程、无模型加载、省约 18 s / 450 MB。
* **不是硬禁用**：`EmbeddingIndex.search/preheat` 内部会 `_ensure_worker()`，
  任何**直接**调用它们的路径仍会把向量腿拉起来。生产检索路径不这么做
  （`_query_locked` 先看 `available`，而 `available` 要求 worker 已 ready），
  但这条边界必须写明，别让运维以为它是「保险丝」。
* **默认**：不设 = **启用**（与登记默认值 `True` 一致）。
* 行为守卫：`TestProductionGateHonoursTheSwitch` 直接对**生产 gate** 下判据
  （env=0 时预热不被启动；env 缺席时预热必须被启动），不是只测那个探针函数。

### 3.4 风险级

**保持 A**。它降级的是**能力**（退化为纯 BM25——一条既有、有回归覆盖的路径），
**不是**"关闭即拆除防护"的总开关；与同族 `AGENT_HYBRID_RERANKER`（A）同级。
本卡未改任何既有风险级（B 级计数不受影响）。

---

## 4. 回归结果

命令与结果（原始输出 `%TEMP%\e1d\regress.txt`），**未放宽任何断言**：

```
tests/unit/test_tool_router_hybrid.py ......................  46 passed
tests/unit/test_tool_router_hybrid_real_index_l28.py .......  16 passed
tests/unit/test_tool_router_hybrid_integration.py ..........  16 passed
tests/unit/test_tool_router_hybrid_fusion_calibration.py ...  18 passed   ← 含降级路"融合顺序 == raw BM25 顺序"契约
tests/unit/test_tool_router_hybrid_e1f1a.py ................  22 passed
tests/unit/test_tool_router_hybrid_e1d_determinism.py .....   3 passed   ← 本卡新增
tests/unit/test_route_conflict_cases.py ....................  31 passed
tests/unit/test_settings_registry.py .......................  56 passed   ← 基线 56 passed，未变
tests/unit/test_settings_registry_e1d.py ...................  11 passed   ← 本卡新增
================== 219 passed, 1 warning in 72.01s ==================
```

另：多轮跨种子实验（前后共 300+ 次子进程）后改后矩阵三种子**逐位一致**
（`after.log`: 成员抖动 0、顺序抖动 0、top1 抖动 0）。
`python scripts/eval_route_conflict.py --no-embedding` 退出码 0，读数与基线一致（§2.3）。

---

## 5. 未验证项与残留风险

1. **关键词路由（`agent/tool_router.get_tools_for_input`）仍传 `set`** 给同一个 helper
   ⇒ 那条路径上"同优先级并列"的先后**仍受哈希序影响**。本卡只修了 hybrid 侧的调用点
   （`tool_router.py` 不在本卡文件范围）。**建议另立卡**：helper 内显式次级键
   （如 `key=(priority, name)` 或接受有序序列并保持稳定排序）。
2. **冷/热进程的下发集差异仍在**（§1.4）：候选池 3 vs 40、top1 可能不同。
   这不是哈希缺陷而是**状态差异**，但后果更大（用户看到的下发集在冷启动窗口与稳定期不同）。
   现状有 `degraded` 字段落 trace 可解释，但**没有**任何"窗口内固定策略"。
   本卡**未**改这一点（改动会牵动启动时序/首问延迟，超出本卡范围），登记为残留风险。
3. **未实测**：向量腿就绪态下的 50 条用例 × 多种子全量矩阵（成本 ≈ 50×5×20 s ≈ 1.4 h，
   且要反复拉起 450 MB 模型）。本次只做了 rc-007 的冷/热 × 2 种子对照。
4. **未实测**：「余弦分完全相等」时 `np.argsort(-sims)` 的并列行为（argsort 非稳定）。
   理论上它对**同一数组**是确定的（不引入跨进程差异），但本卡**没有**构造该场景验证；
   记为未验证项。
5. **未实测**：注册表 UI/API 层的布尔写回（未启动服务，未走 `/api/cp/settings`）。
   已验证的是：解析口对 `"False"`/`"0"` 等字符串的**行为**（测试覆盖），
   以及 registry 的 default **不会**被写进 `os.environ`（既有用例守护）。
6. **未验证**：仓库外注入方（`deploy/ansible/templates/env.j2`）实际写入的取值格式；
   只按"字符串枚举"做了防御（`strip().lower()` + 两个枚举表），未跑部署链路。
7. 本卡**未**处理 E1-C 建议的 α/τ 事项，也未改 `scripts/eval_route_conflict.py`
   （它在范围内但无需改动）。

---

## 6. 回滚指令（定向，**不要**整文件 checkout）

本卡 4 处代码改动都有**唯一锚点**，逐条反向替换即可（我的反向替换脚本
`%TEMP%\e1d\gen_e1d_diff.py` + `*.pre_e1d` 副本可直接拿来 diff 复核）：

1. `agent/tool_router_hybrid.py`
   * 删除 `def _resolve_embedding_env_override()` 整段，并把 `_ensure_st_checked` 里
     `_override = _resolve_embedding_env_override()` 两分支换回原来的
     `env_val = os.environ.get("AGENT_HYBRID_EMBEDDING", ...)` 两分支；
   * `HybridRetriever.__init__`：`_embed_override/_embed_disabled` 两行换回
     `_embed_env = ...` / `_embed_disabled = _embed_env in (...)`；
   * `_query_locked`：`candidate_order` 归并段换回 `all_candidates: set[str] = set()` 两行；
     `for doc_id in candidate_order:` 换回 `for doc_id in all_candidates:`；
     `fused.sort(key=lambda x: (-x[1], _cand_pos[x[0]]))` 换回 `fused.sort(key=lambda x: x[1], reverse=True)`；
   * `hybrid_select_tools`：删除 `ordered_candidates` 构造段，把第一个实参换回 `selected`，
     `categories = [c for c in TOOL_CATEGORIES if c in _cat_set]` 换回 `_cat_set`。
2. `agent/settings/registry.py`：把 `AGENT_HYBRID_EMBEDDING` 条目换回
   `_a(..., CAT_SKILLS, "", "工具路由混合检索的向量模型", owner=...)`。
3. 两个新测试文件可直接删除（它们只守护本卡的行为）。

⚠️ 禁止 `git checkout -- <整文件>`：这两个文件里叠着别的卡的未提交改动。
若要从 HEAD 取原文，只取上面点名的行（HEAD 原文可用
`git show HEAD:agent/tool_router_hybrid.py` 按同名锚点定位）。

**回滚后的预期**：跨种子下发集抖动**立即复现**（rc-007/rc-046 成员各差 2 条、
50/50 顺序抖动、合成索引 5 种子 5 种下发集），新测试 3 条变红，登记表回到"向量模型"的错语义。

---

## 7. 残留物自证

* **无 python 残留进程**：实验结束后 `@(Get-Process python).Count == 0`（未 taskkill 任何别人的进程）。
* **探针全部在仓库外**：`C:\Users\Administrator\AppData\Local\Temp\e1d\`
  （`payload_probe.py`/`run_seeds.py`/`run_matrix.py`/`make_tie_index.py`/`setorder_probe.py`/
  `mutation_selfproof.py`/`gen_e1d_diff.py`/`synth_tie_index.json`/`matrix_before.json`/
  `matrix_after.json`/ 各 `*.txt`/`*.diff`/`*.pre_e1d`）。仓库内**零**临时文件。
* **未触碰禁区**：`agent/skills_mgmt/`、`agent/audit/`、`plugins/`、`agent/workflow_learning/`、
  `yunshu-ui/`、`data/skills_repo/`、`config.yaml`、prompt 装配文件 —— 一行未改
  （`git status` 里这些路径的改动属于**别的卡**）。
* **未动 `data/audit/daily_roots.jsonl`**，也未写 `data/` 下任何文件：
  `data/.embedding_probe` 的修改时间仍是 `2026-07-23 0:20:15`（陈旧缓存未被写入/未被使用）。
* **无 git 写操作**：未 `git add` / 未 `git commit` / 未整文件 checkout。
* 模型缓存：为了 §1.4 的热进程对照，向量 worker 正常加载过一次本地缓存模型（~20 s），
  进程已 `close()` 回收，无残留子进程。
