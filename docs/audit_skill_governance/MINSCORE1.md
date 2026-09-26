# MINSCORE-1 · 生产 min_score=0.3 把技能检索的中文命中打到 4/8 —— 独立复核

> 卡：MINSCORE1（独立工程卡）
> 仓库：C:\Users\Administrator\agent ｜ 分支 audit/skill-governance-v1.0 ｜ Python 3.12.0
> 基线 loader.py sha256 = 7C8723AE787983AB737F4F37E580A7363FD2CC5B06A0DAE11C81EF992E8E84B0
> （与 GATE-1 报告 §8.1 记录的终态**逐字节相同**；本卡结束时仍是这个值）
> 结论：**本卡不修，只交设计与数据。** 三条硬理由见 §5。
> 所有数字都出自**生产入口** SkillLoader.match()（或被明确标注为"源码 / 生产日志 / 生产静态方法"的诊断），没有自造口径。

---

## 0. 结论速览

| 项 | 结果 | 证据 |
|---|---|---|
| **0.3 的来源** | 引入于 47e7f6be（2026-07-31，nzt47），是 os.environ.get(..., "0.3") 的**硬编码默认值**；**commit message 与注释里都没有任何数据支撑或理由** | §1 |
| **根因** | 判据比较的「有界相似度」= _match_score = **H / N**（query token 命中率），**不是相似度**：与文档长度完全无关、与 query 长度严格反比。固定阈值 0.3 ≡ 「命中 token 数 H ≥ 0.3·N」⇒ 中文 bigram 下**长 query 必然被拒**。且这个阈值被**同时**用作 TF-IDF 腿与向量腿的**候选过滤器**，两条腿一起被清空，闸的第二判据（要求有有界证据）随之失效 | §2 + §3 |
| **复现** | ✅ 复现：生产配置（向量腿不可用）min_score=0.3 ⇒ 中文 **4/8**、英文 8/8；min_score=0.01 ⇒ 中文 **8/8**、英文 8/8。向量腿在线时两档都是 **8/8** | §3 |
| **候选修法（实测 4 条）** | C1 调用方降阈值 → 中文 8/8 但**误召 5/31→8/31 且重开 S10-03 假阳**；C2 腿级地板解耦 → 中文 8/8 但 **S10-03 锚红 + 误召 5→7 + 护栏 1 failed**；C3 解耦 + 裕度门槛抬到 2.0 → 中文 **7/8**、锚绿、误召不升，但**护栏仍 1 failed** | §4 |
| **改没改代码** | ❌ **没有**。loader.py 最终 sha256 = 基线，git diff 为空。新增 1 个测试文件 + 2 个数据集 + 本报告 | §6 |
| **跨文件阻塞（新发现）** | 即使 loader 修好，编排层 _semantic_layer_match 第二道闸用**同一个 0.3** 比**同一个 H/N**，同样 4 条被拦 ⇒ 端到端**零收益** | §4.5 |

---

## 1. 0.3 的来源：**查不到理由，是无据的魔数**

### 1.1 引入点

```
$ git log --oneline --all -S "ORCHESTRATOR_SEMANTIC_MIN_SCORE" -- agent/orchestrator/orchestrator.py
1f87a77b feat: restore reranker worktree and fix ci-cd config
47e7f6be fix(orchestrator): 修复 response_workflows 导入被静默吞异常，补齐模板语义层
```

47e7f6be 是**最早**出现处（1f87a77b 是把它搬进 config.yaml 的那次）。该 commit 的**原始 diff（新增行）**：

```
+        【变易】RRF 启用开关与命中阈值通过环境变量配置：
+               ORCHESTRATOR_SEMANTIC_LAYER_ENABLED (默认 true)
+               ORCHESTRATOR_SEMANTIC_MIN_SCORE (默认 0.3)
...
+        min_score = float(_os.environ.get("ORCHESTRATOR_SEMANTIC_MIN_SCORE", "0.3"))
+        result = svc.loader.match(user_input, top_k=5, enabled_only=True,
+                                  min_score=min_score, use_vector=True, use_bm25=True,
+                                  use_reranker=False, fusion_mode="rrf")
```

该 commit 的 message（全文）：

```
fix(orchestrator): 修复 response_workflows 导入被静默吞异常，补齐模板语义层
- orchestrator.py: except ImportError 从 pass 改为 logger.warning，避免模板语义层失效被静默吞掉（违三层漏斗架构）
- agent/response_workflows.py: 新增 IntentRouter/ResponseTemplates/Confidence 三组件，纯函数零 LLM/IO
- tests/unit/test_response_workflows.py: 29 个单元测试
- IntentRouter.classify 补充 3 分支 debug 日志（empty_input/hit/miss）
验收: 107 集成测试 + 29 单元测试全部通过
```

⇒ **message 里一个字都没提 min_score**；docstring 只声明"默认 0.3"。没有标定集、没有负样本对照、没有与任何相似度分布挂钩。

### 1.2 后来搬进 config.yaml（同样没有理由）

```
$ git show 1f87a77b -- config.yaml | Select-String -Pattern "min_score" -Context 6,6
+orchestrator:
+  semantic_layer:
+    # 总开关 - 关闭后语义层跳过，直接降级 LLM
+    # 环境变量 ORCHESTRATOR_SEMANTIC_LAYER_ENABLED 覆盖此值
+    enabled: true
+    # top1 最低匹配分阈值（< 此值视为未命中，降级 LLM）
+    # 环境变量 ORCHESTRATOR_SEMANTIC_MIN_SCORE 覆盖此值
+    min_score: 0.3
```

1f87a77b 的 commit body **是空的**（git show -s --format="%B" 只输出 subject）。
git blame -L 1775,1785 agent/orchestrator/orchestrator.py 确认 _SEM_DEFAULTS["min_score"] = 0.3 同样来自 1f87a77b（nzt47 2026-08-01）。

### 1.3 注意：这**不是** loader 里那个有数据支撑的 0.3

- loader._RRF_QUALITY_MIN = 0.3（loader.py:984）**有**数据来源，注释原文给了 FakeModel + 100 技能标定（负样本 0.1429 / 正样本 0.7143），引入于 69ee92ca（2026-07-30，v6.5 Reranker 集成），后由 0a58589b（S10-03）收紧口径。
- orchestrator.semantic_layer.min_score = 0.3 与它**谱系无关**（引入者 / 日期 / commit 都不同），**没有任何标定记录**。
- 但编排层注释（orchestrator.py:3206）却声称"semantic_layer.min_score 的语义是**相似度阈值**" —— 这是**把两个独立的 0.3 当成同一个东西**用了。

**结论：0.3 是一个无据的魔数，且被当成"相似度阈值"消费。**

---

## 2. 判据为什么在中文短 query 上必然失效

### 2.1 判据的真身：_match_score = H / N（实测逐字确认）

loader.py:194-202：

```
def _match_score(meta_text: str, query_tokens: List[str]) -> float:
    if not query_tokens: return 0.0
    meta_tokens = _tokenize(meta_text)
    if not meta_tokens: return 0.0
    hits = sum(1 for t in query_tokens if t in meta_tokens)
    return hits / len(query_tokens)   # 命中率
```

**实测 1（定义）**：对真库上每一条真实候选，score 逐字等于 round(H/N, 4)（H 用生产分词器 _tokenize(_meta_to_meta_text(meta)) 现算）：
新增测试 test_minscore2_chinese_recall.py::TestCriterionIsQueryCoverageNotSimilarity::test_bounded_score_equals_hit_count_over_query_token_count **通过**（无一条例外）。

### 2.2 「有界相似度 = H/N」这个假设：**证实**，并顺手证伪了"它是相似度"

**实测 2（与文档长度无关）**：同一个 query、同一组命中 token，文档长度 8 倍 vs 200 倍填充 ⇒ 分数**完全相同**：

```
### 2. 文档长度无关性：同一命中集合、文档长度 5/50/500 token，分数是否变化
  len-short  ...  score=0.272700
  len-mid    ...  score=0.272700
  len-long   ...  score=0.272700
  结论：同一 query 下，三个文档长度差 100 倍，得分完全相同 ⇒ 该分数与文档长度无关
```

（输出取自诊断探针 probe_b；同一条断言以 0 倍 / 200 倍填充写进了新测试 test_score_does_not_depend_on_document_length 并**通过**。）

⇒ 没有任何文档长度归一化 ⇒ 它**不可能是余弦 / 相似度**（余弦与 BM25 都含文档长度项）。

**实测 3（与 query 长度严格反比）**：同一 base query 追加真实补充说明：

```
=== 3. 同一 base query 追加补充说明（query 变长），命中分是否 1/N 下降 ===
  pad=0  字符= 12  tokens= 11  len-short score=0.272700
  pad=1  字符= 26  tokens= 25  len-short score=0.120000
  pad=2  字符= 40  tokens= 39  len-short score=0.076900
  pad=3  字符= 54  tokens= 53  len-short score=0.056600
  pad=4  字符= 68  tokens= 67  len-short score=0.044800
```

score × N = 3.0 / 3.0 / 2.999 / 2.9998 / 3.0016（差异全部是 SkillMatch.score 的 round(.,4) 量化误差）。
⇒ **score = H / N 严格成立**，H 只取决于"这个技能与这条意图能对上几个 token"，与 query 长短无关。

### 2.3 于是固定阈值 0.3 等价于「H ≥ 0.3·N」

中文走 bigram 分词（_tokenize，loader.py:174-191），tokens ≈ 汉字数。所以：

> **误拒起点 ≈ 3.33 · H 个汉字。**

- H=1（只对上 1 个 bigram）⇒ 约 3~4 个 token 就开始被拒；
- H=2 ⇒ 约 7 个 token；H=4 ⇒ 约 13 个 token。

这与"这条 query 和这个技能到底像不像"**完全无关** —— 只与"用户话说得多长"有关。
**中文用户的长 query（12~20 字）天然落在被拒区**，这就是"中文 4/8 而英文 8/8"的机制：
英文按整词切，query 的 token 数少、命中词占比高，覆盖率天然 ≥ 0.3。

### 2.4 阈值被用在两处，两条腿一起被清空

min_score 在生产 RRF 路径上**只**用于**腿级候选过滤**，不是最终闸：

| 位置 | 代码 | 后果 |
|---|---|---|
| TF-IDF 腿 | loader.py:1616 传 min_score=min_score 进 _tfidf_scan，过滤实现 :525 | 候选被清空 |
| 向量腿 | loader.py:1646 adapter.search(..., min_score=min_score) | 候选被清空 |
| 融合后 | loader.py:1798 注释：**"融合后不再二次过滤 min_score"** | 不再过滤 |
| 质量闸 | loader.py:1817 起用 _RRF_QUALITY_MIN（另一个 0.3）+ 第二判据 bounded_score is not None（:1855） | 没有有界证据 ⇒ **整单 reject**（:1865-1884） |

⇒ 0.3 把两条腿都清空 ⇒ 闸看到 bounded_similarity=None ⇒ 即使 BM25 把正解顶到 rank1 且裕度 8.09，也**整单返回 []**。

---

## 3. 生产四格（原始输出）

### 3.1 向量腿不可用（= config.yaml 声明的降级模式 / 新进程冷启动形态）

命令（生产入口 SkillLoader.match()，真库 28 条技能，真 BM25，PYTHONHASHSEED=0）：

```
python C:\Users\Administrator\AppData\Local\Temp\minscore1\probe_c.py      # MINSCORE1_CANDIDATE=C0
```

```
### indexed skills = 28
--- A. fusion_mode=none, use_vector=False (pure TF-IDF single path) ---
### tfidf-only  min_score=0.3   zh=4/8  en=8/8
### tfidf-only  min_score=0.01  zh=8/8  en=8/8
--- B. use_bm25=True (auto RRF; vector leg unavailable -> tfidf+bm25) ---
### tfidf+bm25  min_score=0.3   zh=4/8  en=8/8
### tfidf+bm25  min_score=0.01  zh=8/8  en=8/8
```

逐条（min_score=0.3，tfidf+bm25 路径）：

```
[P1 rrf+bm25] min_score=0.30  zh=4/8 en=8/8
     写测试时要避免哪些反模式                   MISS rrf []
     给测试加 Mock 有什么坑                 MISS rrf []
     生成后端接口时怎么加结构化日志和健康检查           HIT  rrf ['code-observability', 'memory_summary']
     前后端状态不同步、有竞态该怎么防               HIT  rrf ['frontend-state-sync', 'code-observability']
     乐观更新回滚和请求取消怎么写                 HIT  rrf ['frontend-state-sync', 'pd-finishing-...']
     做一个不用查文档就能看懂的自解释界面             MISS rrf []
     界面设计时怎么把帮助信息集成进去               HIT  rrf ['self-explanatory-ui', 'pd-frontend-design-...']
     代码交付前怎么做自测和审计报告                MISS rrf []
[P1 rrf+bm25] min_score=0.01  zh=8/8 en=8/8
     写测试时要避免哪些反模式                   HIT  rrf ['testing-anti-patterns', ...]
     给测试加 Mock 有什么坑                 HIT  rrf ['testing-anti-patterns', ...]
     做一个不用查文档就能看懂的自解释界面             HIT  rrf ['self-explanatory-ui', ...]
     代码交付前怎么做自测和审计报告                HIT  rrf ['engineering-test-delivery', ...]
```

**与主审计 / 前序卡的数字逐字一致 ⇒ ✅ 复现。**

### 3.2 向量腿在线（真 BGE-m3，本机 HF 离线缓存）

```
$ python C:\Users\Administrator\AppData\Local\Temp\minscore1\probe_v.py
### backend=st_backend indexed=28 load=87.7s  skills=28
=== 生产配置（vector+bm25+rrf）min_score=0.30 ===
### zh=8/8  en=8/8
### S10-03 噪声 query: matches=[]          ← 锚绿
### S10-03 真命中: ids=['self_reflection', ...]
=== 生产配置（vector+bm25+rrf）min_score=0.01 ===
### zh=8/8  en=8/8
### S10-03 噪声 query: matches=[('self_reflection', {tfidf_score:0.1, vector_score:0.3528,
      bm25_score:4.6614, rrf_normalized:0.9626}), ...]   ← 假阳被重开
```

⇒ **故障范围 = 向量腿不可用（降级模式）**。向量腿在线时 8/8 且 S10-03 锚绿。
⇒ 顺带**独立复核了 GATE-1 §B.4**：min_score=0.01 会把 S10-03 的噪声查询放行 —— 在 C0 基线上就复现，不是某个候选修法引入的。

### 3.3 生产日志里的闸字段（不是自造探针）

```
### 生产日志字段（min_score=0.01，向量腿不可用 = 与 GATE-1 B 卡同口径）
query                               N      H      cov   bm25_t1   bm25_t2    ratio     legN
写测试时要避免哪些反模式                       11 1     0.0909    2.5441    1.9044   1.3359        4
给测试加 Mock 有什么坑                      7 2     0.2857    6.1838    1.9044   3.2471        4
生成后端接口时怎么加结构化日志和健康检查              19 10    0.5263   21.5611    4.4117   4.8873       10
前后端状态不同步、有竞态该怎么防                  13 4     0.3077    9.6211    ...      4.5212        4
乐观更新回滚和请求取消怎么写                    13 8     0.6154   21.5043    ...     10.9148        2
做一个不用查文档就能看懂的自解释界面                17 4     0.2353   11.2095    4.6197   2.4265        4
界面设计时怎么把帮助信息集成进去                  15 7     0.4667   18.7354    3.6919   5.0747        4
代码交付前怎么做自测和审计报告                   14 2     0.1429    2.8096    0.3474   8.0875       10
2 加 3 等于多少？只回答数字                   10 1     0.1000    4.6614    3.0204   1.5433        2
      decision=pass bm25_rank1_evidence=True
```

（摘录自 action=rrf.quality_gate.check / rrf.paths_before_fuse 的字段；cov = bounded_similarity，legN = tfidf_candidate_count。）

---

## 4. 候选修法（4 条，全部走生产入口 match() 实测）

### 4.0 候选定义

| 编号 | 做法 | 改动面 |
|---|---|---|
| **C0** | 现状（基线） | 无 |
| **C1** | 调用方把 min_score 从 0.3 降到 0.01（GATE-1 的 **F3**） | config.yaml / orchestrator._SEM_DEFAULTS（**不在本卡文件归属内**） |
| **C2** | 腿级地板与调用方阈值**解耦**：_try_rrf_match 的两条腿改用 _RRF_LEG_MIN_SCORE=0.01 过滤，闸不变（GATE-1 的 **F1**） | loader.py |
| **C3** | C2 + 把 _RRF_QUALITY_BM25_DECISION_RATIO 从 1.2 抬到 2.0（**本卡新增方案**，用来把 S10-03 的裕度 1.5433 挡在外面） | loader.py |

### 4.1 数字总表

| 候选 | 中文(生产配置 0.3) | 英文(0.3) | S10-03 噪声 @0.3 | S10-03 噪声 @0.01 | 负样本非空 @0.3（rrf / 单向量路） | 指定 4 文件护栏 |
|---|---|---|---|---|---|---|
| **C0 基线** | **4/8** | 8/8 | [] ✅锚绿 | ['self_reflection','pd-dispatching-…'] ❌假阳 | **5/31** / 5/31 | **42 passed** ✅ |
| **C1** 降阈值到 0.01 | **8/8** | 8/8 | 测试仍绿（它自己写死 0.3）**但它保护的真机形态被重开** | 同上，假阳 | **7/31** / **8/31** ↑ | 绿（无代码改动） |
| **C2** 腿级地板解耦 | **8/8** | 8/8 | ['self_reflection','pd-dispatching-…'] ❌**锚红** | 同上 | **7/31** ↑ / 5/31 | **1 failed** ❌ |
| **C3** 解耦 + 裕度≥2.0 | **7/8** | 8/8 | [] ✅**锚绿** | [] ✅（顺带修好 GATE-1 §B.4 的假阳） | 5/31（不升）/ 5/31 | **1 failed** ❌ |

C2 / C3 的护栏失败原文（同一条）：

```
FAILED tests/unit/test_ret1r_bm25_quality_gate.py::TestBm25AloneIsNotEnough::test_bm25_only_evidence_is_not_enough
E   assert 0.1818 is None
```

它失败在**前置断言**上，而这条前置断言把"min_score 与腿级过滤的耦联"写成了**契约**（该文件 docstring 原文：
「min_score=0.3 ⇒ TF-IDF 腿的候选（覆盖率 0.18/0.09）全被自己的阈值挡掉」，断言 assert gate["bounded_similarity"] is None）。
⇒ **C2 / C3 不是"顺手改坏"，而是明确改动了一条既有契约**；而该测试文件不在本卡文件归属内（属别的卡），
按卡面规则「不能靠放宽断言 / 加 skip 变绿」⇒ C2 / C3 **不合格**。

### 4.2 C3 是"行为上最接近可用"的一条（但仍被否）

C3 在**行为面**上几乎无瑕：中文 4/8→7/8、英文不回退、S10-03 锚绿（连 0.01 形态的假阳都一起修好）、
负样本非空**没有上升**（5/31 持平）。唯一没救回来的是 zh01「写测试时要避免哪些反模式」（裕度 1.3359 < 2.0）
—— 而它**恰恰与 S10-03 的噪声查询不可区分**（见 §4.3）。

### 4.3 为什么"再想一条阈值"也不行：Pareto 支配（本卡新证据）

把 zh01（正样本）与 S10-03 噪声查询并排放（同一配置：min_score=0.01、向量腿不可用，数据来自 §3.3）：

| 特征 | **zh01** 正样本 | **S10-03 噪声** | 谁更大 |
|---|---|---|---|
| cov = H/N（闸比的那个量） | 0.0909 | **0.1000** | 噪声 |
| H（命中 token 数） | 1 | 1 | 平 |
| bm25_top1（绝对分） | 2.5441 | **4.6614** | 噪声 |
| bm25_ratio（裕度） | 1.3359 | **1.5433** | 噪声 |
| idf_cov（IDF 加权覆盖率，诊断量） | 0.0594 | **0.0869** | 噪声 |
| N | 11 | 10 | 噪声更短 |

⇒ **噪声查询在每一个坐标上都不劣于 zh01（五项严格更优、一项持平）。**
⇒ **任何对这组特征单调非降的判据，要么同时收下、要么同时拒绝。**

GATE-1 的结论是"ratio 区间重叠（1.3359 < 1.5433 < 3.2471）"；本卡的结论更强：**是支配，不是重叠。**

唯一 zh01 占优的坐标是 TF-IDF 腿候选数（4 vs 2）—— 但"候选越多越像正样本"没有质量含义，
且这个坐标系上只有 1 个样本，**据此定阈值就是过拟合**，本卡明确拒绝。

### 4.4 GATE-1 的 F2（唯一候选拿不到裕度）

_bm25_decision_ratio 在候选 < 2 条时返回 None（loader.py:1077 起）。zh01 与噪声都恰好有 2 条 BM25 候选，
所以 F2 对本卡的 4/8 **不适用**；它针对的是"稀有标识符"形态（GATE-1 §B.3），本卡未复测（不在本卡验收集里）。

### 4.5 **新发现（跨文件阻塞）：只在 loader.py 里修 = 端到端零收益**

把腿救活（= C2 / C3 会交给上层的候选）之后，用**生产静态方法** Orchestrator._bounded_relevance 复核：

```
### 同上，但用 min_score=0.01 模拟「腿被救活」后的候选（= C2/C3 之后 loader 会交出的东西）
   写测试时要避免哪些反模式       top1=testing-anti-patterns    bounded=0.0909  编排器判 拒(低相关) 降级LLM
   给测试加 Mock 有什么坑      top1=testing-anti-patterns    bounded=0.2857  编排器判 拒(低相关) 降级LLM
   生成后端接口时怎么加结构化日志和健康检查 top1=code-observability  bounded=0.5263  编排器判 放行
   前后端状态不同步、有竞态该怎么防     top1=frontend-state-sync  bounded=0.3077  编排器判 放行
   乐观更新回滚和请求取消怎么写       top1=frontend-state-sync  bounded=0.6154  编排器判 放行
   做一个不用查文档就能看懂的自解释界面   top1=self-explanatory-ui  bounded=0.2353  编排器判 拒(低相关) 降级LLM
   界面设计时怎么把帮助信息集成进去     top1=self-explanatory-ui  bounded=0.4667  编排器判 放行
   代码交付前怎么做自测和审计报告      top1=engineering-test-delivery bounded=0.1429 编排器判 拒(低相关) 降级LLM
```

orchestrator.py:2423-2432：_relevance = self._bounded_relevance(top1)；if _relevance is not None and _relevance < min_score: reject("low_bounded_relevance")。
min_score 就是同一个 0.3，_bounded_relevance 取的就是同一个 tfidf_score（= H/N）。

⇒ **被 loader 救回的 3 条（0.2857 / 0.2353 / 0.1429）在编排层被同样拦下。**
⇒ 只改 loader.py（本卡唯一能动的生产文件）= **e2e 中文仍然是 4/8**。

---

## 5. 本卡裁决：**不修**（三条硬理由）

1. **唯一行为上可用的修法 C3 会撞红一条既有护栏**，而那条护栏恰好把"min_score 与腿级过滤的耦联"写成了契约
   （test_ret1r_bm25_quality_gate.py::TestBm25AloneIsNotEnough）。该文件不在本卡文件归属内；
   为了让它变绿只能改它的前置断言 = **放宽既有守卫**，卡面明确禁止。
2. **只改 loader.py 收益为零**（§4.5 实测）：编排层用同一个 0.3 比同一个 H/N，同样拦下。真正的修法必须同时动
   agent/orchestrator/orchestrator.py（第二道闸）与 config.yaml（阈值语义分层），这两处都不在本卡归属内。
3. **在当前验收集上不存在"零回归"的判据**：zh01 被 S10-03 噪声 **Pareto 支配**（§4.3）。
   要么重做判据（需要能分开这两者的**新证据**，当前 28 技能 / 26 query 的验收集给不出），
   要么接受 7/8 并改动既有契约 —— 两者都超出本卡范围。

### 5.1 移交给后续卡的具体建议（不是"随便调个阈值"）

1. **阈值语义分层**（同时改 loader + orchestrator + config.yaml）：
   - 腿级候选过滤不再用调用方的 min_score，改用一个**腿级地板**常量（C2 已实测可行，8/8）；
   - 调用方 min_score 只作为**最终闸**，且必须在**两个消费点**同步改：loader._RRF_QUALITY_MIN 与
     orchestrator._bounded_relevance < min_score；
   - 必须同步重写 test_ret1r_bm25_quality_gate.py::TestBm25AloneIsNotEnough 的前置断言（它锁的是耦联）。
2. **先补"能分开 zh01 与 S10-03 噪声"的独立证据**（当前无解，见 §4.3）。
   本卡唯一看到的方向是**引入第二条独立腿**（向量腿），而这正好指向第三条。
3. **最便宜的止血（0 行代码）**：不要让向量腿静默降级。实测向量腿在线时 min_score=0.3 下中文 **8/8**、
   英文 8/8、S10-03 锚绿（§3.2）。而源码层面（**仅静态阅读，未端到端实测**）：
   SkillsMgmtService.__init__ 里 self.loader = SkillLoader(self.file_store)（service.py:82）**不注入也不预热**向量适配器，
   而 _try_rrf_match（loader.py:1636-1640）与 _try_vector_match（loader.py:805-808）在
   _st_backend / _native_chroma 均为 None 时**都会跳过向量腿** ⇒ 新进程冷启动时，RRF 路径很可能一直跑在
   tfidf+bm25 的降级形态上（与 config.yaml 注释"sqlite-vec 不可用时自动降级"同形）。
   **这条我只有源码证据，没有端到端实测**（见 §8-U2）。

---

## 6. 本次改动与残留物自证

### 6.1 仓库内（本卡只写 4 个文件，**没改任何生产代码**）

| 文件 | 动作 |
|---|---|
| tests/unit/test_minscore2_chinese_recall.py | **新增**（11 passed）—— 特征化锁：H/N 不变量 + 生产四格 + 根因 + 跨文件阻塞 |
| data/eval/minscore1_query_set.v1.jsonl | **新增**（16 行 = 8 中文 + 8 英文，含 expected_skill） |
| data/eval/minscore1_negative_set.v1.jsonl | **新增**（31 行：tests/eval/negative_samples_extended.json 的 25 条 + RET-1R 文档点名的 5 条 + S10-03 噪声） |
| docs/audit_skill_governance/MINSCORE1.md | **新增**（本报告） |

**agent/skills_mgmt/loader.py 未改** —— 为测 C2 / C3 临时打过补丁，测完逐字还原：

```
$ Get-FileHash agent\skills_mgmt\loader.py -Algorithm SHA256
7C8723AE787983AB737F4F37E580A7363FD2CC5B06A0DAE11C81EF992E8E84B0   ← 与基线 / GATE-1 记录值相同
$ git diff --stat      （对 agent/ 与 tests/ 无输出）
```

### 6.2 非本卡产生的变化（如实登记，我未触碰）

git status --short 里还有：

```
 M scripts/detect_dynamic_loads.py                                  ← 并发卡所改，不是本卡
 M agent/descriptors/registry.py                                    ← 并发卡所改，不是本卡
?? tests/unit/test_descriptor_registry_concurrent_load.py           ← 并发卡新增，不是本卡
?? tests/unit/test_dynamic_loads_high_exemption.py                  ← 并发卡新增，不是本卡
```

（这些文件在本卡执行期间被其它并发卡改动；本卡**逐字未碰**。本卡自己新增的只有 §6.1 那 4 个。）

### 6.3 本卡起的进程与临时文件

探针全部在 %TEMP%\minscore1\（仓库外，可整目录删除），进程全部退出；未写任何生产台账
（data/audit/**、data/agent_lines/_active.json、data/skills_mgmt.json、data/skills_repo/** 均未改；
SkillFileStore.load_metadata_index 是只读缓存，不落盘）。
唯一一次"写入生产目录"是新增 data/eval/*.jsonl（卡面明确允许）。

---

## 7. 可复现命令

```
# 0) 基线
python -V                                    # Python 3.12.0
git status --short
Get-FileHash agent\skills_mgmt\loader.py -Algorithm SHA256
#   → 7C8723AE787983AB737F4F37E580A7363FD2CC5B06A0DAE11C81EF992E8E84B0

# 1) 0.3 的来源（只读 git）
git log --oneline --all -S "ORCHESTRATOR_SEMANTIC_MIN_SCORE" -- agent/orchestrator/orchestrator.py
git show 47e7f6be -- agent/orchestrator/orchestrator.py | Select-String -Pattern "min_score" -Context 8,8
git show -s --format="%H%n%an%n%ad%n%n%B" 47e7f6be
git show 1f87a77b -- config.yaml | Select-String -Pattern "min_score" -Context 6,6
git blame -L 1775,1785 --date=short -- agent/orchestrator/orchestrator.py

# 2) 生产四格（向量腿不可用）
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONHASHSEED='0'
python C:\Users\Administrator\AppData\Local\Temp\minscore1\probe_c.py          # C0 基线

# 3) 真向量腿在线（BGE-m3 本机离线缓存，加载约 88s）
$env:HF_HUB_OFFLINE='1'
python C:\Users\Administrator\AppData\Local\Temp\minscore1\probe_v.py

# 4) 判据本质（H/N 的两条证伪）
python C:\Users\Administrator\AppData\Local\Temp\minscore1\probe_b.py
python C:\Users\Administrator\AppData\Local\Temp\minscore1\probe_d.py          # 生产日志字段
python C:\Users\Administrator\AppData\Local\Temp\minscore1\probe_e.py          # 编排层第二道闸

# 5) 卡面指定的护栏（本卡终态，全绿）
python -m pytest tests/unit/test_gate1_single_vector_quality_gate.py tests/unit/test_s2_gate_is_not_false_green.py tests/unit/test_ret1r_bm25_quality_gate.py tests/unit/test_ret1r_negative_query_scope.py -q -p no:randomly --timeout=60
#   实测：42 passed in 6.01s

# 6) 本卡新增的特征化锁
python -m pytest tests/unit/test_minscore2_chinese_recall.py -q -p no:randomly --timeout=60
#   实测：11 passed in 2.13s

# 7) 候选 C2 / C3 的复现（需临时打补丁：_try_rrf_match 的两处 min_score=min_score
#    → min_score=self._RRF_LEG_MIN_SCORE (=0.01)；C3 另把 _RRF_QUALITY_BM25_DECISION_RATIO 改 2.0）
$env:MINSCORE1_CANDIDATE='C2'; python ...\probe_c.py     # zh=8/8 但锚红、护栏 1 failed
$env:MINSCORE1_CANDIDATE='C3'; python ...\probe_c.py     # zh=7/8、锚绿、护栏仍 1 failed
```

（探针脚本不是交付物，位于 %TEMP%\minscore1\：probe_a / b / c / d / e / v.py。
关键口径都已固化进 tests/unit/test_minscore2_chinese_recall.py 与 data/eval/*.jsonl，可脱离探针重跑。）

---

## 8. 我没能确认的部分（不许粉饰）

| # | 未确认项 | 状态与原因 |
|---|---|---|
| U1 | **GATE-1 的 23 条负样本无法逐条对拍** | GATE-1 的探针在 %TEMP%，仓库里**只有报告数字**（12/23），没有查询清单。本卡自建 31 条负样本集（§6.1），得到的 5/31、8/31、7/31 **不能**与 GATE-1 的 12/23 直接比较；我报告的是"本卡集上的变化方向"。 |
| U2 | **生产是否真的跑在"向量腿不可用"的降级模式** | 只有**源码**证据（service.py:82 不注入 / 不预热 → loader.py:1636 / 805 会跳过向量腿）。我**没有**端到端跑 get_skills_mgmt_service()：构造生产 SkillsMgmtService 可能触碰生产台账，卡面禁止。是否由别处（skill 写入触发的 upsert 钩子 → ensure_indexed）预热，未验证。 |
| U3 | **编排层第二道闸的实际拦截** | 用的是生产静态方法 Orchestrator._bounded_relevance + 真实 SkillMatch 对象（§4.5），**没有**跑完整 _semantic_layer_match（需要真实 service + LLM 上下文）。判定为"等价"的依据是 orchestrator.py:2424 的一行比较式，属**静态阅读 + 局部实测**，不是全链路实测。 |
| U4 | **向量腿在线数字的稳定性** | 单次实测（本机 BGE-m3、HF_HUB_OFFLINE=1、28 条技能、load 87.7s）。未多轮取平均、未换机型 / 在线加载复核。 |
| U5 | **0.3 是否有仓库外的设计依据** | 我只查了 git 历史（引入 commit + blame + -S）。若存在设计文档 / Wiki / 需求单，未做全历史文档扫描。 |
| U6 | **全量 tests/unit** | 未跑（卡面要求：几十分钟，不得前台跑）。本卡改动只新增测试与数据文件、未改生产代码，回归面为空；已跑卡面指定文件 + 本卡新文件（42 + 11 passed）。 |
| U7 | **C3 的 7/8 是否可接受** | 本卡不做取舍裁决：zh01 与 S10-03 噪声不可区分（§4.3），7/8 意味着**永久放弃 zh01 这类查询**。这需要 Owner 决定，本卡只给数字。 |

---

## 附录 A · 数据集

data/eval/minscore1_query_set.v1.jsonl（16 行，摘录）：

```
{"id":"zh01","lang":"zh","query":"写测试时要避免哪些反模式","expected_skill":"testing-anti-patterns","en_counterpart":"what anti-patterns to avoid when writing tests"}
{"id":"zh02","lang":"zh","query":"给测试加 Mock 有什么坑","expected_skill":"testing-anti-patterns","en_counterpart":"pitfalls of adding mocks in tests"}
{"id":"zh03","lang":"zh","query":"生成后端接口时怎么加结构化日志和健康检查","expected_skill":"code-observability", ...}
{"id":"zh04","lang":"zh","query":"前后端状态不同步、有竞态该怎么防","expected_skill":"frontend-state-sync", ...}
{"id":"zh05","lang":"zh","query":"乐观更新回滚和请求取消怎么写","expected_skill":"frontend-state-sync", ...}
{"id":"zh06","lang":"zh","query":"做一个不用查文档就能看懂的自解释界面","expected_skill":"self-explanatory-ui", ...}
{"id":"zh07","lang":"zh","query":"界面设计时怎么把帮助信息集成进去","expected_skill":"self-explanatory-ui", ...}
{"id":"zh08","lang":"zh","query":"代码交付前怎么做自测和审计报告","expected_skill":"engineering-test-delivery", ...}
{"id":"en01".."en08", ...}   与 zh01..zh08 一一对应的英文原句
```

来源：与 docs/audit_skill_governance/G1C-UA.md §1.2 / §1.3、RET1.md §3.2 逐字相同，
并与 tests/unit/test_skill_meta_zh_recall.py::PAIRS 逐字相同（便于对拍）。

data/eval/minscore1_negative_set.v1.jsonl：31 行 = tests/eval/negative_samples_extended.json 的 25 条
（case_101..case_125，原样保留 category）+ 5 条 RET-1R 文档点名的（best pizza recipe / testing /
observability / reflection / systematic debugging）+ S10-03 噪声查询（ext_06）。

## 附录 B · 关键常量与源码行（供复核）

| 常量 / 位置 | 值 | 文件:行 |
|---|---|---|
| SkillLoader.match(min_score=...) 默认 | 0.01 | loader.py:550 |
| 生产调用方传入 | 0.3 | orchestrator.py:1778（_SEM_DEFAULTS）/ config.yaml orchestrator.semantic_layer.min_score |
| 腿级过滤（TF-IDF） | min_score | loader.py:1616（过滤实现 :525） |
| 腿级过滤（向量） | min_score | loader.py:1646 |
| _RRF_QUALITY_MIN | 0.3 | loader.py:984 |
| _RRF_QUALITY_BM25_DECISION_RATIO | 1.2 | loader.py:1009 |
| _SINGLE_PATH_MIN_TOP1（GATE-1） | 0.45 | loader.py:1040（:864 与 :1730 共用） |
| 融合后不再过滤 min_score | — | loader.py:1798 |
| 编排层第二道闸 | _bounded_relevance(top1) < min_score | orchestrator.py:2423-2432 / :3200-3236 |
