# RET-1R · 功能修复卡：向量腿英文召回 1/8（R-2）+「开了 BM25 反而更差」（R-1）

> 任务卡：**RET-1R**（RET-1 的重派；RET-1 **零足迹**，本卡**从零开始**，未寻找其残留）
> 基线：`HEAD = 5c9ace10a4ca4bb96860db3a48debf9ddcf496bf`（分支未动；工作区含 45 张卡的未提交改动）
> 环境：Python 3.12.0（**系统解释器**，未用 venv）、Windows；**零出网**（`HF_HUB_OFFLINE=1`，模型走本地 HF cache）；
> 全程 **0 次 `git add` / `git commit`**、**无整文件 `git checkout`**、**未 `taskkill` 任何进程**、未启动常驻服务。
> 探针全部在仓库外：`C:\Users\Administrator\AppData\Local\Temp\ret1r\`
> 并行卡 **TESTHYG-1** 在跑 CPU 负载进程：**未触碰** `tests/unit/conftest.py` 与它点名的测试文件（本卡新增的 2 个测试文件与它无交集）。

---

## 0. 结论速览

| 项 | 判定 | 一行证据 |
|---|---|---|
| **R-2** 向量腿英文召回 | ✅ **1/8 → 8/8**（中文 8/8 → 8/8） | §1.1 / §1.3 原始输出 |
| **R-2** 修法 | 让负样本正则**只作用于它声称的后端**（字面匹配兜底） | §1.2 |
| **R-2** 误召代价（诚实报） | ⚠️ 单向量路负样本非空 **19/23 → 22/23**（+3 条，逐条列出） | §1.4 |
| **R-1** `match(use_bm25=True)` 中文召回 | ✅ **4/8 → 8/8**（英文 8/8 → 8/8） | §2.1 / §2.5 |
| **R-1** 修法 | 质量闸新增**并列的第二条判据**：BM25 腿 top1/top2 的**无量纲裕度** | §2.3 |
| **R-1** 误召代价（诚实报） | ✅ **零新增**：改前改后被召回的负样本是**同一批 4 条**（逐条比对） | §2.5 |
| 有没有「把阈值调小」 | ❌ 没有：`_RRF_QUALITY_MIN` 与 `_BOUNDED_QUALITY_KEYS` **逐字未动**，有护栏测试钉死 | §2.3 / §4 |
| 新增 env | ✅ **0 个** ⇒ `agent/settings/registry.py` 未改；`test_settings_registry.py` **56 passed** = 基线 | §5 |
| 非空转自证 | ✅ 去掉修复 ⇒ 新测试 **6 failed**（3 条 R-2 + 3 条 R-1，断言原文见 §4.3） | §4.3 |
| 还原后 | ✅ 两个源文件 sha256 **逐字节相同**，45 passed | §4.3 |
| 回归 | ✅ **338 passed / 1 xfailed / 0 failed**（11 个文件，含 7 个卡面指定 + S10-03 质量闸 + 静默失败） | §5 |
| `eval_skill_retrieval.py` | ⚪ 改前/改后**逐字节相同**（它走 TF-IDF 单路，两条修复都不在它路径上） | §3.3 |
| 残留物 | ✅ 仓库内只多 2 个测试文件 + 本报告；探针全在 Temp；无残留在跑的进程 | §8 |

---

## 1. R-2（先做；**已完整交付**）

### 1.1 复现：**证实**（生产入口 + 改前原始输出）

生产入口：`agent.skills_mgmt.loader.SkillLoader(file_store=SkillFileStore())._get_vector_adapter().search()`；
「绕开点」用同一条链路上**唯一**的区别 —— `adapter._search_with_timeout()`（与 `search()` 的唯一差异就是**跳过负样本启发式**），
与 G1C-UA §1.7「绕开它直接比余弦」同口径。

```
### backend=st_backend indexed=28 load=54.2s          ← 改前（修复前源码）
### zh=8/8 (bypass 8/8)  en=1/8 (bypass 8/8)
### neg nonempty=13/27 (bypass 27/27)
### filtered zh=0 en=7 neg=14                          ← 8 条英文 query 里 7 条被启发式整条过滤
```

**结论：证实。** 与 G1C-UA §7 R-2 的登记完全一致：向量腿英文 1/8，绕开启发式后 **8/8**
⇒ 余弦本身没问题，是**静态过滤**把 7 条合法英文 query 当成了负样本。

命中那条正则的正是 `_NEGATIVE_PATTERNS[1] = ^[a-zA-Z_][a-zA-Z0-9_ ]*$`：
英文 query 只要由「字母/数字/下划线/空格」组成就被判为负样本（8 条里 7 条符合，
唯一漏网的是含连字符的 `what anti-patterns to avoid when writing tests`）。

**与 docstring 不符**（两处都写了「只在 BM25 fallback 模式下生效」）：
`search()` 的「负样本防御（针对 BM25 fallback 模式）」与
`_is_negative_query` 的「此规则只在 BM25 fallback 模式下生效兜底作用；ChromaDB 真实向量模式下不影响」，
而实现是**无条件**对所有后端生效。

### 1.2 怎么修的（二选一：选了「让正则只作用于它声称的后端」）

**判据**：把启发式**分两档**，档 2（会误伤合法英文 query 的那部分）**只在「字面匹配兜底后端」生效**；
该后端判据与 loader 侧判定同一个后端的判据**逐字一致**（`loader._try_vector_match` / `_try_rrf_match` 的
`_st_backend is None and _native_chroma is None` ⇒ 日志 reason `BM25 fallback is not real vector search`）：

| 档 | 规则 | 作用域 | 位置 |
|---|---|---|---|
| 档 1 | 空 / 单字符 / 纯数字符号（`^[\d\s\W]+$`，含中文豁免） | **与后端无关**（任何后端下过滤都不损失召回） | 懒构建**之前**（垃圾 query 不拉起 BGE-m3） |
| 档 2 | 纯 ASCII 单词/词组 `^[a-zA-Z_][a-zA-Z0-9_ ]*$`、全编程关键字 | **只在** `_negative_filter_applies()` 为真时（VectorStore 倒排 BM25 / 字符匹配兜底） | 懒构建**之后**（此时后端才判得出来） |

为什么档 2 必须放在懒构建**之后**：构建前 `_st_backend`/`_native_chroma` 都还是 `None`，
判不出后端 ⇒ 同一条 query 会「第一次返回空、建完索引后又返回结果」（顺序依赖，无法写护栏）。

**为什么不选另一条路（论证它不该全局生效）**：
① 它防的是「**字面匹配**把标识符误命中技能」，真向量后端由语义负责区分；
② 全局生效的代价是**实测**的：英文 1/8（7 条合法 query 被整条丢弃）；
③ 它连自己的 docstring 都不是这个语义（规则 3 原文是「纯英文标识符**且无空格分隔**（看起来像变量名）」，
   而正则 `[a-zA-Z0-9_ ]*` **允许空格** —— 连「无空格分隔」这一条都对不上）；
④ 只把正则收紧成「不含空格」也**不够**：单词英文 query（`testing` / `observability` / `reflection`）
   仍会被判为负样本，那是半个修复（本卡探针里这三条是单独测的）。
⇒ 因此选「作用域」这条，并把 docstring 改成与实现一致的精确表述（两处 docstring 都改了）。

**改动（行为行数，反向补丁实测）**：
`agent/skills_mgmt/vector_adapter.py` **+17 / −7**（另加注释/文档约 +228 / −12）；
改动点 6 处：模块级两条正则常量 + `_is_empty_or_symbol_only()` helper、类内两档正则常量、
`_negative_filter_applies()`、`_is_negative_query` 文档与实现、`search()` 两档调用点与文档。
**未新增任何 env**；`bm25_searcher.py` / `searcher.py` 未改。

### 1.3 改后（原始输出）

```
### backend=st_backend indexed=28 load=56.5s          ← 改后
### zh=8/8 (bypass 8/8)  en=8/8 (bypass 8/8)
### neg nonempty=26/27 (bypass 27/27)
```

### 1.4 误召代价（**不许只报好消息**）

**(a) 腿级（向量腿本身）**：负样本 query 非空 **13/27 → 26/27**。
这**不是**「多召回了 13 条技能」，而是「向量腿不再把整条 query 静态丢弃」：向量腿对任何 query 都会返回 top-5
最近邻（`min_score=0.01`），改前被静态过滤掉的那些 query 是**返回空**的。真正的用户可见口径见 (b)。

**(b) 端到端 `match(use_vector=True)`（单向量路，无 RRF 闸）** —— 这是**真实的误召上升**：

```
改前: 非空 19/23（13 条走向量路 + 6 条落 TF-IDF 兜底）
改后: 非空 22/23（22 条走向量路）
```

逐条比对的**净变化（由空变非空，+3）**：

| query | 改前 | 改后 |
|---|---|---|
| `best pizza recipe` | 空 | 返回 5 条（向量） |
| `asdfghjkl` | 空 | 返回 5 条（向量） |
| `def print_hello_world function` | 空 | 返回 5 条（向量） |

另有 6 条（`book a flight to tokyo` / `what is the weather today` / `recommend a good song` /
`how much is bitcoin now` / `who won the world cup` / `tell me a joke`）**改前就已经非空**，
只是通路从「TF-IDF 兜底」变成「向量腿」（结果集合相同）。
`12345`（档 1）**仍然返回空** —— 垃圾数字 query 的静态防线没有丢。

**如实说**：`asdfghjkl` 与 `def print_hello_world function` 属于「确实应该拒」的垃圾 query，
本卡把它们放进了语义腿（这是「作用域改成 docstring 说的那样」的直接后果）。
在 `use_bm25=True` 的 RRF 路径上它们仍被质量闸挡住（§2.5：负样本非空 4/18 不变）；
**只有单向量路（`use_vector=True, use_bm25=False`）没有质量闸** —— 这是**既有**缺陷（不是本卡引入），
已登记为残留风险 §6-R2-1。若评审认为不值得，回滚方式见 §7。

### 1.5 护栏（R-2）

新文件 `tests/unit/test_ret1r_negative_query_scope.py`（**22 个用例**，300 行）：
作用域判据 5 条、真实检索结果 3 条、垃圾 query 不空转 13 条（参数化）、调用点结构守卫 1 条。
关键断言（原文）：

- `assert adapter._negative_filter_applies() is False` —— 真向量后端下启发式**不**适用；
- `assert ids, "真向量后端下合法英文 query 不得被负样本启发式过滤（改前返回 [] ⇒ 英文 1/8）"` —— **真实检索结果**；
  前置还有 `assert _ASCII_IDENTIFIER_RE.match(EN_QUERY)`（证明那条正则确实匹配该 query，变的是作用域不是规则）；
- `assert store.queries == [], "字面匹配后端不该被查询（过滤要发生在后端调用之前）"` —— 作用域的另一半；
- `assert model.encode_calls == before` —— 档 1 必须在编码之前早退；
- `assert "_negative_filter_applies()" in line`（对 `inspect.getsource(SkillVectorAdapter.search)`）—— **源码级**：
  `_is_negative_query` 的调用只允许出现在作用域守卫之下。

---

## 2. R-1（已完成）

### 2.1 复现：**证实**（改前原始输出）

```
（改前）e2e_bm25_zh  recall=4/8   nonempty=4/8
        e2e_bm25_en  recall=8/8   nonempty=8/8
        e2e_bm25_neg recall=0/18  nonempty=4/18
        e2e_tfidf_zh recall=8/8   ← 只用 TF-IDF 是 8/8
```

⇒ **打开 BM25 反而更差**（8/8 → 4/8）。被闸误拒的 4 条中文 query 的原始记录：

| query | 融合 top1 | bounded_similarity | bm25 raw | bm25_rank | rrf_normalized | 闸 |
|---|---|---|---|---|---|---|
| 写测试时要避免哪些反模式 | testing-anti-patterns | 0.0909 | 2.5441 | 1 | 0.9866 | **reject** |
| 给测试加 Mock 有什么坑 | testing-anti-patterns | 0.2857 | 6.1838 | 1 | 1.0 | **reject** |
| 做一个不用查文档就能看懂的自解释界面 | self-explanatory-ui | 0.2353 | 11.2095 | 1 | 1.0 | **reject** |
| 代码交付前怎么做自测和审计报告 | engineering-test-delivery | 0.1429 | 2.8096 | 1 | 1.0 | **reject** |

原始日志（与 G1C-UA §7 R-1 逐字一致）：

```
{'action': 'rrf.quality_gate.check', 'top1_skill_id': 'testing-anti-patterns', 'top1_rrf_normalized': 0.9866,
 'bounded_similarity': 0.0909, 'bounded_keys_declared': ['tfidf_score', 'vector_score'],
 'bm25_raw_unbounded': 2.5441, 'effective_score': 0.0909, 'threshold': 0.3, 'use_bm25': True, 'decision': 'reject'}
{'action': 'rrf.quality_gate.rejected', 'reason': 'bounded_similarity_below_threshold', 'fused_count': 4}
```

根因确认：闸只拿 `max(tfidf_score, vector_score, rerank_score)`（= `_match_score` 的
**query token 命中率**）与 0.3 比；BM25 的 `bm25_rank=1` / `rrf_normalized=0.9866` **完全不参与** ⇒ 整单 reject、返回 `[]`。

### 2.2 判据设计：先证伪三个方向，再定方向（都要有数据）

**(a) 不能「调小 0.3」**：同一批负样本的有界相似度（0.4 / 0.5）比被误拒的正样本（0.09~0.29）**还高**
⇒ 调小阈值会**先放走负样本**（正是卡面禁止的「把假阴换成假阳」）。

**(b) 不能用 BM25 原始分**：BM25Okapi 原始分**无上界**（同库实测 1.2~21.0），与有界阈值 0.3 比大小
就是 TASK-S10-03 记录过的**量纲混用**（无界分恒赢 ⇒ 噪声候选过闸）。

**(c) 实测否决的两个「看起来合理」的有界量**（探针 `probe_cov.py`，26 条 query 全量）：

| 候选有界量 | 被误拒正样本 | 已通过的负样本 | 结论 |
|---|---|---|---|
| 字面覆盖率（= 现有 tfidf_score） | 0.0909~0.2857 | **0.4 / 0.5** | ❌ 反相关 |
| IDF 覆盖率 `Σidf(命中)/Σidf(query)` | 0.66~1.0 | **0.82~1.0** | ❌ 完全不分离 |
| BM25 top1 绝对分 | 2.54 / 2.81 / 6.18 / 11.21 | 2.17 / 3.44 / 3.63 / 5.37 | ❌ 区间重叠 |

⇒ **实测结论：没有任何「有界标量」能把「被误拒的 4 条正样本」与「已通过的 4 条负样本」分开。**
唯一能分开的是 **BM25 腿自己的判决形状**：被误拒的 4 条正样本裕度 = 1.336 / 3.247 / 2.426 / 8.088，
而**被闸正确拒绝**的 3 条负样本裕度 = 1.028 / 1.030 / 1.028（「这条腿是平的」）。

### 2.3 修法（方向：让 BM25 的证据参与判定，而不是调阈值）

在质量闸里加一条**并列**判据（原判据一字未改）：

```
通过 ⇔ bounded_similarity >= 0.3
     或 ( use_bm25=True
          且 至少有一条有界腿真的给出了相似度（bounded_score is not None）
          且 融合 top1 就是 BM25 腿的 rank1
          且 BM25 腿 top1/top2 >= 1.2 )
```

**为什么「比值」是合法的、不是换个马甲调阈值**：
`top1/top2` 是**同一量纲两个分数之比** ⇒ **无量纲**、与语料规模/分词无关，
衡量的是「这条腿的判决有多果断」，不是「分数有多高」；它与有界相似度阈值**并列**成第二条独立判据，
`_RRF_QUALITY_MIN = 0.3` 与 `_BOUNDED_QUALITY_KEYS` 都**逐字未动**（有护栏测试钉死）。
护栏 `test_ratio_is_scale_invariant` 直接证明量纲无关：`[10,5]` 与 `[2000,1000]` 都得 2.0。

**常量 1.2 的标定与敏感性**（数据集：28 条技能语料 / 8 中文 + 8 英文正样本 / 18 条负样本）：

```
M=1.05 -> zh=8/8 en=8/8 neg_nonempty=4/18      ← 安全带 [1.05, 1.30]
M=1.30 -> zh=8/8 en=8/8 neg_nonempty=4/18
M=1.4  -> zh=7/8 en=8/8 neg_nonempty=4/18
M=2.0  -> zh=7/8 en=8/8 neg_nonempty=4/18
M=2.5  -> zh=6/8 en=8/8 neg_nonempty=4/18
M=3.0  -> zh=6/8 en=8/8 neg_nonempty=4/18      ← 等价于把新判据收紧回原样
```

1.2 取安全带中点（被拒负样本最大裕度 1.030 与最小正样本裕度 1.336 的几何中值 ≈ 1.17）。
**这不是「精调出来的幸运数字」**：整个 [1.05, 1.30] 区间结果完全相同（8/8 且零新增误召），
常量在这个 25% 宽的区间里怎么取都一样。语料/分词变化后需重新标定（残留风险 §6-R1-1）。

### 2.4 【必须显式报告】回归里踩出来的真问题：新判据一度放行了 S10-03 的负样本

第一版判据（只有 rank1 + 裕度两条）**打红了一个既有护栏测试**：

```
tests/unit/test_s10_03_retrieval_quality_gate.py::Test真库同输入对照::test_噪声查询_不得有候选
E   AssertionError: 真机噪声查询必须零候选（修复前 top1=self_reflection，tfidf_score=0.1）；
    实际=[('self_reflection', {... 'bm25_rank': 1, 'bm25_score': 4.6614 ...}),
          ('pd-dispatching-parallel-agents-b8065ccd-skill', {... 'bm25_score': 3.0204 ...})]
```

该噪声 query（`2 加 3 等于多少？只回答数字`）在真库上 **tfidf/vector 全空**，
BM25 只有两条候选且裕度 = 4.6614/3.0204 = **1.543 ≥ 1.2** ⇒ 第一版把它放行了，
**正是 TASK-S10-03 修掉的量纲混用陷阱复发**（只剩无界 BM25 分时，「果断」没有量纲可比）。
处理方式（**不是放宽断言**，那条断言的严格性一字未改）：给新判据加**第 ② 条必要条件下界**：

> **BM25 的「果断」只能作为有界证据的佐证，不能单独成立** —— 必须 `bounded_score is not None`。

加条件 ② 后：`test_s10_03_retrieval_quality_gate.py` **13 passed**（含该真库锚），
本卡 4 条中文正样本全部仍被救回（它们的有界相似度是 0.0909~0.2857，**是有值的**，只是低于阈值）。
并在本卡自己的测试文件里加了同形态的**合成**护栏 `TestBm25AloneIsNotEnough`
（`min_score=0.3` 让有界腿全空 ⇒ 仍必须拒绝），避免将来只有「真库存在时才跑」的那条测试兜底。

### 2.5 改后量化 + 误召代价

```
（改后）e2e_bm25_zh  recall=8/8   nonempty=8/8      ← 4/8 → 8/8
        e2e_bm25_en  recall=8/8   nonempty=8/8
        e2e_bm25_neg recall=0/18  nonempty=4/18     ← 与改前**逐条相同**
```

**误召代价（逐条比对，不是只比数字）**：

```
=== e2e_bm25_neg: before nonempty=4/18, after nonempty=4/18
  nonempty before: ['1+1 等于几', 'book a flight to tokyo', 'what is the weather today', 'how much is bitcoin now']
  nonempty after : ['1+1 等于几', 'book a flight to tokyo', 'what is the weather today', 'how much is bitcoin now']
=== e2e_tfidf_neg: before nonempty=7/18, after nonempty=7/18   （逐条相同）
```

⇒ **R-1 的放宽没有换来任何一条新的误召**：被召回的负样本**是同一批 4 条**（这 4 条改前就已经被放行，
原因是它们的有界相似度 0.4/0.5 **本来就高于 0.3**，与新判据无关）。

### 2.6 护栏（R-1）

新文件 `tests/unit/test_ret1r_bm25_quality_gate.py`（**10 个用例**，256 行）。
**全部用真实检索结果断言**（卡面要求「不要只断言配置」）：

- `test_result_is_not_empty_when_bm25_leg_is_decisive`：合成语料上 `match(q, use_bm25=True)`
  必须**真的返回**期望技能（改前返回 `[]`）；同用例的前置从**生产日志** `rrf.quality_gate.check` 断言
  `bounded_similarity < 0.3`、`bm25_rank1_evidence is True`、`decision == "pass"`；
- `test_without_the_escape_the_rejection_returns` / `test_removing_the_ratio_evidence_restores_the_rejection`：
  **进程内**把常量抬到 ∞ / 把裕度打桩为 `None` ⇒ 立刻退回 `[]`（证明结果是「BM25 证据」救的）；
- `TestFlatBm25LegIsStillRejected`：BM25 腿裕度 1.0（平局簇）的负样本**仍然**整单拒绝，
  且对照断言「TF-IDF 单路对它有候选」⇒ 那个 `[]` 是闸挡的，不是没候选；
- `TestBm25AloneIsNotEnough`：有界腿全空 + BM25 果断 ⇒ 仍拒绝（§2.4 的合成版）；
- `test_bounded_threshold_and_keys_are_unchanged`：次级守卫 —— `_RRF_QUALITY_MIN == 0.3`、
  `_BOUNDED_QUALITY_KEYS` 未变。

---

## 3. 四格量化表 + eval 脚本对照

### 3.1 中/英 × 三条腿（top-5 命中，生产取数口，`PYTHONHASHSEED=0`）

| 腿 | 取数口 | 中文 改前 | 中文 改后 | 英文 改前 | 英文 改后 |
|---|---|---|---|---|---|
| **TF-IDF** | `loader.match(use_vector=False, use_bm25=False)` | 8/8 | 8/8 | 8/8 | 8/8 |
| **BM25** | `loader._get_bm25_searcher().search()` | 8/8 | 8/8 | 8/8 | 8/8 |
| **向量** | `loader._get_vector_adapter().search()` | 8/8 | 8/8 | **1/8** | **8/8** |

| 端到端 | 改前 | 改后 |
|---|---|---|
| `match(use_bm25=True)` 中文 | **4/8** | **8/8** |
| `match(use_bm25=True)` 英文 | 8/8 | 8/8 |
| `match(use_bm25=True)` 负样本非空 | 4/18 | 4/18（同一条集合） |
| `match(use_vector=True)` 负样本非空 | 19/23 | 22/23（+3，逐条见 §1.4） |
| `match(use_vector=True)` 走向量路 | 13/23 | 22/23 |

**说不好的那一条**：本卡只修「**该被命中却没被命中**」（假阴），代价是 R-2 让单向量路放行了 3 条垃圾 query（§1.4），
以及 **BM25 / 向量两条腿的中文召回本来就是 8/8、没有提升**（不是本卡的收益）。

### 3.2 正/负样本集合口径

正样本：8 条中文 + 8 条英文，与 `docs/audit_skill_governance/G1C-UA.md` §1.2/§1.3 **逐字相同**（便于对拍）。
负样本：18 条无关 query（中文日常 / 英文日常 / 纯数字 `12345`、`asdfghjkl` / 编程关键字）
+ R-2 探针里扩到 23~27 条（含单词英文 query `testing` / `observability` / `reflection` / `systematic debugging`）。

### 3.3 `scripts/eval_skill_retrieval.py` 改前 vs 改后（固定 `PYTHONHASHSEED=0`）

```
before B1101C6E5D983E0358E664E6...
after  B1101C6E5D983E0358E664E6...
BYTES IDENTICAL: True
overall before: {"precision": 0.4444, "recall": 1.0, "mrr": 0.9889}
❌ CI 守卫失败: Precision@3=0.4444 < 阈值 0.6   ← 改前改后同样失败（既有问题，与本卡无关）
```

⇒ **如实说明**：该脚本调用的是 `ld.match(query, top_k, enabled_only)`（**不传 `use_vector` / `use_bm25`）
⇒ 走 TF-IDF 单路，**两条修复都不在它的执行路径上**，所以改前改后**逐字节相同**。
它对本卡的价值是**回归证据**（证明没有把别的东西改坏），**不是**修复效果的证据。
它的 Precision@3=0.4444 与 `test_skills_mgmt.py` 里那条既有 `xfail` 是同一个既有缺陷
（技能 description 为空的 TF-IDF 召回），本卡未触碰。

---

## 4. 护栏断言 + 非空转自证

### 4.1 R-2 护栏（作用域 = 它声称的后端）
见 §1.5；核心结构断言：
`assert "_negative_filter_applies()" in line`
（对 `inspect.getsource(SkillVectorAdapter.search)` 里每一行含 `_is_negative_query(` 的代码行）。

### 4.2 R-1 护栏（启用 BM25 腿时 BM25 的证据必须能影响最终结果）
见 §2.6；核心行为断言：
`assert ids, "启用 BM25 腿且该腿判得果断时，BM25 的证据必须能影响最终结果（不得整单 reject）"`。

### 4.3 非空转自证（**去掉修复 ⇒ 新测试变红**；断言原文）

做法：`Temp\ret1r\reverse_patch.py` 对两个源文件做**行为级反向补丁**
（每处替换断言 `count == 1`，可从 `fixed_backup\` 重复恢复），然后跑新测试。

**① 反向补丁（= 去掉修复）**：

```
  reversed: R-2 tier2 removed
  reversed: R-2 tier1 -> unconditional call
  reversed: R-1 escape block removed
  reversed: R-1 gate condition -> bounded-only
  reversed: R-1 gate log restored
  reversed: R-1 reject log restored
```

**② 新测试变红（6 failed / 39 passed）—— 断言原文**：

```
FAILED tests/unit/test_ret1r_negative_query_scope.py::TestEnglishRecallOnSemanticBackend::test_english_query_is_not_treated_as_negative
E   AssertionError: 真向量后端下合法英文 query 不得被负样本启发式过滤（改前返回 [] ⇒ 英文 1/8）
    assert []

FAILED tests/unit/test_ret1r_negative_query_scope.py::TestEnglishRecallOnSemanticBackend::test_loader_sees_the_semantic_leg
E   AssertionError: assert 'tfidf' == 'vector'

FAILED tests/unit/test_ret1r_negative_query_scope.py::TestCallSiteIsGuarded::test_is_negative_query_is_only_called_under_the_scope_guard
E   AssertionError: 负样本启发式的调用必须被作用域守卫包住（作用域 = 它声称的后端）：if self._is_negative_query(query):
    assert '_negative_filter_applies()' in 'if self._is_negative_query(query):'

FAILED tests/unit/test_ret1r_bm25_quality_gate.py::TestBm25EvidenceReachesTheGate::test_precondition_scenario_is_reproduced
E   KeyError: 'bm25_rank1_evidence'

FAILED tests/unit/test_ret1r_bm25_quality_gate.py::TestBm25EvidenceReachesTheGate::test_result_is_not_empty_when_bm25_leg_is_decisive
E   AssertionError: 启用 BM25 腿且该腿判得果断时，BM25 的证据必须能影响最终结果（不得整单 reject）
    assert []

FAILED tests/unit/test_ret1r_bm25_quality_gate.py::TestBm25AloneIsNotEnough::test_bm25_only_evidence_is_not_enough
E   KeyError: 'bm25_decision_ratio'

======================== 6 failed, 39 passed in 2.99s =========================
```

（同一次反向补丁下 `test_s10_03_retrieval_quality_gate.py` 13 条**全绿** —— 说明那条测试是「假阳守卫」，
不是本卡的「修复探测器」，两者互补。）

**③ 还原后：sha256 逐字节相同 + 全绿**：

```
### 还原后 vs 实验前 逐字节比对（实验前 sha256 于反向补丁**之前**记录）
vector_adapter.py   IDENTICAL  AF693B3B9DCBF1BE3C1251CEFC149CC95DAA0DE7F50451160D24580C86A285A6
loader.py           IDENTICAL  515B23B4FB12FFAA9DE293519E86DE6449F9B753F1EF55472A17EC8CE1FCC5D3
test_ret1r_negative_query_scope.py    EFCFCBA5C4EC4CC08EE27845AE302ABB114ABE1B69C427E194B0C2469D12AA04
test_ret1r_bm25_quality_gate.py       991AD91863F89B3C7FFBB371008DD3A5013491FDF2A7870784A6D9987FC278C9

$ python -m pytest tests/unit/test_ret1r_negative_query_scope.py tests/unit/test_ret1r_bm25_quality_gate.py tests/unit/test_s10_03_retrieval_quality_gate.py -q
45 passed in 3.16s
```

---

## 5. 回归结果（全部原始计数）

| 测试文件 | 结果 | 基线 |
|---|---|---|
| `test_vector_skill_searcher.py` | **9 passed** | 9 |
| `test_bm25_skill_searcher.py` | **26 passed** | 26 |
| `test_skill_meta_zh_recall.py` | **46 passed** | 46 |
| `test_three_legs_meta_zh_parity.py` | **15 passed** | **15**（基线一致） |
| `test_skill_description_single_source.py` | **33 passed** | 33 |
| `test_settings_registry.py` | **56 passed** | **56**（基线一致；本卡新增 env = 0） |
| `test_skills_mgmt.py` | **75 passed, 1 xfailed** | 同 |
| **7 个卡面指定文件一起跑** | **260 passed, 1 xfailed, 0 failed** | 与 G1C-UA §6 一致 |
| `test_s10_03_retrieval_quality_gate.py`（质量闸前卡，**本卡自己加跑**） | **13 passed** | 13 |
| `test_retrieval_silent_failures.py`（**本卡自己加跑**） | **33 passed** | 33 |
| 本卡 2 个新文件 | **32 passed** | — |
| **11 个文件一起跑** | **338 passed, 1 xfailed, 0 failed** | — |

**没有任何断言被放宽**，没有删除任何既有用例。
唯一被本卡触碰的既有断言是「发现它红了」（§2.4 的 S10-03 真库锚）—— 处理方式是**让实现满足它**（加必要条件下界），
**不是**改断言。

---

## 6. 未验证项与残留风险（不许粉饰）

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| **R2-1** | 单向量路（`use_vector=True, use_bm25=False`）**没有质量闸** | ⚠️ 既有缺陷，本卡未修（**但被本卡放大**） | 改前静态过滤替它挡了 7 条英文 query；改后这 7 条走向量检索，其中 3 条（`best pizza recipe` / `asdfghjkl` / `def print_hello_world function`）由「空」变「有结果」。真正的修法是给该路补一个与 RRF 同口径的质量闸（**超出本卡文件范围**，且会与「英文召回」直接对冲，需要单独定标）。 |
| **R2-2** | 档 2 的启发式在**默认部署下事实上休眠** | ⚠️ 已登记 | sentence-transformers（BGE-m3）可用时 `_negative_filter_applies()` 恒为 False ⇒ 「纯 ASCII 标识符 / 编程关键字」的静态防线在真向量后端下不再生效。这是「作用域 = docstring 声称的后端」的**必然结果**；若希望真向量后端也有静态防线，正确做法是**新增一条与语义证据挂钩的判据**（如「top1 相似度低于某有界阈值 且 query 无实义 token」），而不是把这条正则再变成全局。 |
| **R2-3** | 垃圾 query 首调用代价上升 | ⚠️ 已量化 | 档 2 在懒构建**之后**求值 ⇒ 形如 `asdfghjkl` 的 query 在**新进程**里会触发一次 BGE-m3 懒构建（本机实测 **54.2s / 56.5s**，含模型加载），而改前是**立即返回空**。`12345` 一类（档 1）仍走早退，不受影响。 |
| **R2-4** | 向量腿召回数字来自**本机单次实测** | ⚠️ 口径 | BGE-m3 在 `HF_HUB_OFFLINE=1` 下从本地 HF cache 加载（391 个权重分片），未验证与在线加载的差异；未做多轮取平均。 |
| **R2-5** | 探针一侧曾出现**超时混淆**（已排除并如实记录） | ⚠️ 已排除 | TESTHYG-1 并行跑 CPU 负载，实测一次模型加载被拖到 **255.8s**，此时 `_SEARCH_TIMEOUT_SECONDS=2.0` 被打爆 ⇒ 每次向量检索超时降级 ⇒ e2e 全落 TF-IDF（第一版 e2e 探针测到的是「超时降级」而不是「误召」）。定稿探针在**进程内**把该预算临时设为 60s（不改源码）并逐条记录 `method / fallback_used / degrade_reason`；定稿数据 `超时=0`。**该 2s 预算覆盖不到懒构建，是既有残留（G1C-UA §7 R-5），本卡未改**。 |
| **R1-1** | 新判据常量 `1.2` 是**单语料标定** | ⚠️ 需重标 | 标定集 = 28 条技能 + 26 条 query；安全带 [1.05, 1.30] 内结果完全一致，但**语料规模 / 分词方式变化后必须重标**。标定数据与敏感性表已写进源码注释与该常量的 docstring。 |
| **R1-2** | 新判据要求「至少有一条有界腿给出过相似度」 | ⚠️ 保守取舍 | 这让「只有 BM25 命中」的场景**一律拒绝**（§2.4 的 S10-03 形态）。代价：如果将来出现「技能只有专有名词能被 BM25 命中、TF-IDF/向量都为空」的真命中，它仍会被拒（假阴）。本卡数据集里没有这种样本，**未验证**。 |
| **R1-3** | 有界相似度（命中率）**与 query 长度强相关** | ⚠️ 既有缺陷，本卡只绕不修 | 长中文 query 的命中率天然低（11 个 bigram 只命中 1~3 个 ⇒ 0.09~0.29），与固定阈值 0.3 比是**尺度问题**。改它等于改阈值语义，本卡只加了第二条并列判据；中文 query 继续变长后仍可能被误拒。 |
| **R1-4** | 向量腿在本卡的 RRF 端到端测量里**始终未激活** | ⚠️ 事实登记 | `match(use_bm25=True)`（`use_vector=False`）时 loader 的 fast-exit 会跳过向量腿（`_st_backend is None` ⇒ `rrf.vector.skipped_bm25_fallback`），与 G1C-UA §7 R-1 原始日志一致（`vector_candidate_count: 0`）。三路同时激活（`use_vector=True, use_bm25=True` 且向量腿已建索引）的端到端**未在本卡量化**。 |
| **R1-5** | 未跑全量 `tests/unit` | ⚠️ 卡面要求 | 按卡面「不要跑全量（124 分钟）」执行；已额外加跑 `test_s10_03_retrieval_quality_gate.py`（质量闸前卡）与 `test_retrieval_silent_failures.py`（检索静默失败），两者全绿。**其它未被点名的文件未测**。 |

---

## 7. 回滚

### 7.1 只回滚 R-2（无 env 可关，改 3 处代码）

把 `search()` 里的两档调用点改回「无条件 `self._is_negative_query(query)` 早退」：
档 1 的 `if _is_empty_or_symbol_only(query):` 换成 `if self._is_negative_query(query):`，并删掉档 2 的 4 行。
⇒ 英文召回**立刻回到 1/8**（= 改前行为的定义）。
· 最省事的等价做法：把档 2 守卫改成恒真（`self._negative_filter_applies() or True`）—— **但会让护栏红**（那是有意的告警）。

### 7.2 只回滚 R-1（一行）

把 `gate_passed = (effective_score >= self._RRF_QUALITY_MIN or bm25_rank1_evidence)`
改成 `gate_passed = effective_score >= self._RRF_QUALITY_MIN`
（或把 `_RRF_QUALITY_BM25_DECISION_RATIO` 设为 `float("inf")`）⇒ 中文端到端**立刻回到 4/8**。
· `_RRF_QUALITY_BM25_DECISION_RATIO` 是**类常量**，刻意**没有** env 入口（避免「多一个开关就多一处不一致」）。

### 7.3 全卡回滚

删除 `tests/unit/test_ret1r_negative_query_scope.py`、`tests/unit/test_ret1r_bm25_quality_gate.py`、本报告，
再对两个源文件执行 `python reverse_patch.py revert`（`Temp\ret1r\reverse_patch.py`，可重复执行，每处断言 `count == 1`）。
备份：`Temp\ret1r\fixed_backup\`。

---

## 8. 残留物自证

· **仓库内新增文件**：`tests/unit/test_ret1r_negative_query_scope.py`、`tests/unit/test_ret1r_bm25_quality_gate.py`、
  `docs/audit_skill_governance/RET1.md`（本报告）。**没有别的**
  （eval 报告用 `--output` 写到 Temp，未落进 `tests/eval/`）。
· **修改的文件**：`agent/skills_mgmt/vector_adapter.py`（行为 **+17/−7**）、`agent/skills_mgmt/loader.py`（行为 **+27/−3**）；
  含注释/文档的 hunk 口径为 `+245/−19`、`+90/−4`（该口径来自 `git diff` 中带 RET-1R 标记的 hunk，
  可能含同区域的其它卡改动，故只作参考）。
  **未改**：`bm25_searcher.py`、`searcher.py`、`agent/settings/registry.py`（无新增 env）、
  `agent/tool_router*.py`、`agent/audit/`、`agent/descriptors/`、`plugins/`、`yunshu-ui/`、`config.yaml`、
  `data/` 下任何运行期文件、prompt 装配四件套，以及 `tests/unit/conftest.py` 与 TESTHYG-1 点名的测试文件（本卡未对它们执行任何写操作）。
· **探针全部在仓库外**：`C:\Users\Administrator\AppData\Local\Temp\ret1r\`
  （`probe_r1.py` / `probe_r2.py` / `probe_r2_e2e.py` / `probe_cov.py` / `reverse_patch.py` /
  `dbg1.py` / `dbg2.py` / `margin.py` / `dump*.py` / `table.py` / `diff_neg.py` /
  `diffsplit.py` / `gitstat.py` / `*.json` / `*.txt` / `fixed_backup\` / `revwork\` / `synth*\`）。
· **临时还原痕迹已清零**：两个源文件还原后 sha256 与实验前**逐字节相同**（§4.3）。
· **无新增 env** ⇒ `registry.py` 零改动。
· **未提交**：全程 `git add` / `git commit` **0 次**；`HEAD` 仍为 `5c9ace10a4ca4bb96860db3a48debf9ddcf496bf`，分支未动。
· **未 `taskkill` 任何进程**；本卡启动的 4 次向量探针进程**全部已退出**
  （收尾实测 `Get-CimInstance Win32_Process -Filter "Name='python.exe'"` **无输出**；未触碰 TESTHYG-1 的负载进程）。
· **未启动任何常驻服务**；未出网（`HF_HUB_OFFLINE=1`，模型从本地 cache 加载）。

---

## 9. 一张图

```
R-2（向量腿英文 1/8）:
  search() ── 无条件 _is_negative_query()   ← 实现与 docstring 不符
      ↓ 改成两档
  档1（空 / 单字符 / 纯数字符号）      与后端无关；懒构建前早退（垃圾 query 不拉模型）
  档2（纯 ASCII 词 / 编程关键字）      只在「字面匹配兜底后端」生效（= loader 同口径判据）
      ⇒ 英文 1/8 → 8/8；代价：单向量路负样本非空 19/23 → 22/23（+3 条，逐条列出）

R-1（use_bm25=True 中文 4/8）:
  质量闸 = bounded(tfidf/vector) >= 0.3          ← 原判据一字未改
       或 BM25 腿 rank1 且 top1/top2 >= 1.2      ← 新增并列判据（无量纲比值）
          前提：至少一条有界腿给出过相似度（否则只剩无界分 = 量纲混用陷阱）
      ⇒ 中文 4/8 → 8/8；负样本被召回的**还是同一批 4 条**（零新增误召）
```
