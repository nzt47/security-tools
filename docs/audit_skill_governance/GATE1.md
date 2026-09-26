# GATE-1 · 检索健壮性卡：单向量路质量闸（RET-1R 残留 R2-1）+ 三条残留的实测裁决

> 任务卡：**GATE-1**（检索健壮性；处理 RET-1R 如实登记的三条残留中**可做**的那几条）
> 承接：`docs/audit_skill_governance/RET1.md` §6（R2-1 / R1-2 / R1-3）；压测项来自 RUNBOOK-1 §⑥-4
> 环境：Python 3.12.0（**系统解释器**，未用 venv）、Windows；**零出网**（`HF_HUB_OFFLINE=1`，模型走本地 HF cache）
> 探针全部在仓库外：`C:\Users\Administrator\AppData\Local\Temp\gate1\`
> 全程 **0 次 `git add` / `git commit` / `git checkout` / `git stash`**、**未 `taskkill` 任何进程**、未起常驻服务、未跑全量 `tests/unit`。
> **未触碰**：`.github/workflows/`、`tests/unit/test_date_shift_blindspots_guard.py`、`tests/conftest.py`、`agent/tool_router*.py`、`agent/audit/`、`plugins/`、`yunshu-ui/`、`config.yaml`、`data/` 下任何运行期文件（含 `data/descriptors.json`，**只读复制**）、prompt 装配四件套。

---

## 0. 结论速览

| 项 | 判定 | 一行证据 |
|---|---|---|
| **A** 复现 RET-1R 的 R2-1 | ✅ **复现** | `match(use_vector=True)` 负样本非空 **22/23**（与 RET-1R §1.4 逐字一致） |
| **A** 方案选择 | **方案 X**（加闸），**不选 Y** | Y 的证据不成立：单向量路是 `fusion_mode` 这个**运行时可热更**的配置键可达的公开检索模式，不是"调用方自知风险"的裸 API（§A.2） |
| **A** 闸的口径 | **既有的 `SINGLE_PATH_MIN_TOP1 = 0.45`**（上提为类常量 `_SINGLE_PATH_MIN_TOP1`，融合路与单向量路**共用同一个**） | 没有另发明阈值；数值一字未改（§A.3） |
| **A** 四格（改前→改后） | 误召 **22/23 → 12/23**；中文召回 **8/8 → 8/8**；英文召回 **8/8 → 8/8**；对照路 10/23 → 10/23 | §A.4 原始输出 + 逐条 |
| **A** 诚实残留 | 向量腿自身仍有 **5/23** 高于 0.45；RET-1R 净增的 3 条里**修好 2 条**，`def print_hello_world function`（0.4654）**仍在** | §A.4 / §⑥-G1 |
| **B** 「只有 BM25 命中一律拒绝」 | ✅ **构造出来了，且实测被误拒**（**两条独立机制**） | 真库 8 条中文 query：`min_score=0.01` 时 **8/8**，**生产 `min_score=0.3` 时 4/8**（BM25 腿判得果断且**答案正确**）→ §B.2 |
| **B** 附带发现 | RET-1R 的 R-1 判据在 `min_score=0.01` 下**重新放行了 S10-03 的噪声查询**（它自己的护栏只覆盖 0.3） | §B.4：`bounded=0.1 / ratio=1.5433 / decision=pass`，返回 2 条候选 |
| **C** 长度相关性 | ✅ **量化出来了，而且是精确的**：有界相似度 = **H / N**（H = 该技能命中的 query token 数，恒定；N = query token 数）⇒ 严格 ∝ 1/长度 | 5 个 base 全部满足（H = 2/2/4/4/10），§C.1 |
| **C** 多长开始误拒 | 由 H 决定：**token 数 > H/0.3 即开始**（H=2 ⇒ 约 **7 字**；H=4 ⇒ 约 13 字；H=10 ⇒ 约 33 字）。实测首次误拒 **14 / 15 / 18 / 22 / 40 字符**（向量腿不可用时）；**生产配置到 51 字符都没被误拒** | §C.2 |
| **C** 值不值得修 | **不值得在本卡修，只登记**（给代价数据） | §C.4 |
| **D** 多进程并发重建同一台账 | ❌ **证实有害**：**30 次并发里 1 次台账损坏 + 半成品台账（30/32，丢 2 条）**；**丢失更新 9/9 全中**；另有**审计链 seq 冲突** | §D.3 / §D.4 |
| **D** 生产台账 | ✅ **未被触碰**：实验前后 sha256 逐字节相同 `325AC2D1…` | §D.5 |
| 新增 env | ✅ **0 个** ⇒ `agent/settings/registry.py` **未改**；`test_settings_registry.py` **56 passed** = 基线 | §⑤ |
| 非空转自证 | ✅ 摘掉修复 ⇒ 新测试 **1 failed / 2 passed**（行为断言原文见 §A.6）；还原后 sha256 逐字节相同 | §A.6 |
| 回归 | ✅ **168 passed**（卡面 6 个文件 162 + 本卡新文件 6）+ **176 passed / 1 xfailed**（5 个相邻文件）；**0 failed，未放宽任何断言** | §⑤ |
| 残留物 | ✅ 仓库内只多 **1 个测试文件 + 本报告**；改了 **1 个源文件**；探针全在 Temp；我起的进程**全部退出** | §⑧ |

---

## A（主）· 单向量路没有质量闸（RET-1R 的 R2-1）

### A.1 复现：**证实**（改前原始输出）

生产入口与取数口：`agent.skills_mgmt.loader.SkillLoader.match(q, top_k=5, use_vector=True)`
（= `fusion_mode` 默认 `"none"` ⇒ 走 `_try_vector_match`，**单向量路**）。
探针 `Temp\gate1\probe_a.py`，真库 28 条技能，BGE-m3（`st_backend`），`PYTHONHASHSEED=0`，进程内把 `_SEARCH_TIMEOUT_SECONDS` 临时抬到 60s 以排除 CPU 争用导致的超时混淆（RET-1R §6-R2-5 同口径，本次实测**超时 0 次**）。

```
### backend=st_backend indexed=28 load=69.0s skills=28          ← 改前（= RET-1R 修复后的工作区状态）
### zh e2e_vec recall=8/8   e2e_rrf(bm25) recall=8/8
### en e2e_vec recall=8/8   e2e_rrf(bm25) recall=8/8
### neg e2e_vec nonempty=22/23   e2e_rrf(bm25) nonempty=10/23
```

**逐条核对 RET-1R §1.4 登记的 3 条净增误召**（改前向量腿 top1 余弦 → 端到端）：

| query | 向量腿 top1 | 改前端到端 |
|---|---|---|
| `best pizza recipe` | 0.3865 | 非空（向量腿 5 条） |
| `asdfghjkl` | 0.3382 | 非空（向量腿 5 条） |
| `def print_hello_world function` | **0.4654** | 非空（向量腿 5 条） |
| `12345`（档 1 早退） | 无候选 | 空（防线未丢） |

⇒ **复现成立**，且与 RET-1R 报的数字**逐字一致**（22/23）。根因确认：`_try_vector_match` 里**只有 `min_score` 过滤**（默认 0.01），它**不会**拒绝任何低相似度候选；融合路上的两条闸（`_RRF_QUALITY_MIN=0.3` 与"单路兜底阈值 0.45"）都只存在于 `_try_rrf_match` 内。

**负样本/正样本的向量 top1 分布（决定闸能否存在的关键数据）**：

```
负样本 23 条：0.3186 … 0.5042   （最大 = "跑步前要做什么热身" 0.5042）
正样本 16 条：0.5195 … 0.6876   （最小 = "optimistic update rollback and request cancellation" 0.5195）
```

⇒ 两簇**不重叠**，既有的 0.45 落在负样本簇内、正样本簇之下 ⇒ 用**既有阈值**即可在零召回损失下挡住大部分误召。

### A.2 方案选择：**X（加闸）**，以及为什么**不是** Y

**方案 Y 的主张**是「单向量路本就该没有闸：它是显式 API，调用方自己知道语义」。**我按卡面要求去找证据，结论是这条主张在本仓不成立**：

| 证据 | 内容 |
|---|---|
| 谁在调它 | `match(..., use_vector=True)` 的**生产调用点只有一处**：`agent/orchestrator/orchestrator.py:2374`，入参来自 `_load_semantic_layer_config()` |
| 调用方是否"自知风险" | ❌ **不成立**。`use_vector` / `fusion_mode` 不是代码里写死的字面量，而是 **`config.yaml orchestrator.semantic_layer` 的键**，**且可被三层覆盖**：env（`ORCHESTRATOR_SEMANTIC_*`）> config.yaml > **API 热更（`_SEM_API_OVERRIDE`，落 SQLite，重启仍在）**。运维把 `fusion_mode` 改成 `none`（或 `use_bm25: false`）就**直接落到这条无闸路径**，编排器对"这条腿没有闸"**没有任何感知**（`MatchResult` 里也没有"该腿未经质量判定"的标记） |
| 是否有"明知故犯"的先例口径 | ❌ `scripts/eval_rrf_fusion.py` 的 docstring 把 `vector: use_vector=True, fusion_mode="none"` 明确叫作「**单路向量**」对比臂 ⇒ 这是**被官方脚本当作正规检索模式**的路径，不是调试后门 |
| 不修的实际代价（本卡实测） | 22/23 负样本被召回 = **96%** 的垃圾 query 都能拿到 5 条技能；其中 `asdfghjkl` / `def print_hello_world function` 是 RET-1R 自己判定的"确实应该拒" |
| 修的实际代价（本卡实测） | 中英召回 **8/8 + 8/8 不变**（0 损失）；误召 22/23 → 12/23 |

⇒ **选 X**。同时**不采用**"它是显式 API 所以不修"的论证：本仓里"显式"只意味着**可配置**，不意味着**调用方承担了风险**；而代价侧为零（召回零损失）恰好让"不修"失去理由。

### A.3 闸的口径：复用**既有的**单路兜底阈值（不另发明）

`_try_rrf_match` 里**本来就有一条**针对"只有向量路有候选"的闸：

```
if not tfidf_matches and vector_matches and not bm25_matches:
    vec_top1_score = vector_matches[0].score
    if vec_top1_score < SINGLE_PATH_MIN_TOP1:      # 原为就地局部变量 = 0.45
        logger.info(... 'rrf.single_path_low_score_rejected' ...); return None
```
它的数据支撑写的正是"向量 top1 的**有界余弦**"，与负样本场景逐字对应（case_042「帮我订一张机票」0.4414 应拒 / case_043「请帮我反思」0.6030 应留）。

**触发条件在两条路径上结构等价**：那条闸的条件是「TF-IDF 路无候选 + 只有向量路有 + BM25 无候选」；而 `fusion_mode="none" + use_vector=True` 的单向量路**根本不跑 TF-IDF 腿和 BM25 腿**（`use_bm25=True` 会在 `match()` 里把 `fusion_mode` 自动升为 `"rrf"` ⇒ 二者不可能共存）⇒ **恒满足**该条件。

**改动（行为行 +5 / −2；含注释文档 +51 / −11）**：

| # | 位置 | 改动 | 性质 |
|---|---|---|---|
| 1 | `SkillLoader` 类常量区 | 新增 `_SINGLE_PATH_MIN_TOP1 = 0.45`（把原就地变量**上提**，原数据支撑注释一并搬来） | 唯一真相源 |
| 2 | `_try_rrf_match` 单路兜底检查 | 就地 `SINGLE_PATH_MIN_TOP1 = 0.45` **删除**，比较与日志改引用 `self._SINGLE_PATH_MIN_TOP1` | **数值/语义一字未改** |
| 3 | `_try_vector_match`（`min_score` 过滤之后、构造 `MatchResult` 之前） | 新增 3 行闸：`if matches[0].score < self._SINGLE_PATH_MIN_TOP1: log('vector.single_path_low_score_rejected') ; return None` | 与既有闸**同处置**（`return None` ⇒ 调用方降级 TF-IDF） |

**为什么处置用 `return None`（走 TF-IDF 兜底）而不是返回空结果**：这正是既有那条闸的处置（"口径一致"的直接含义）；`_try_vector_match` 的契约本身就是"返回 None ⇒ 外层 `fallback_used=True` 降级 TF-IDF"。
**我同时测了另一种处置**（返回空 `MatchResult`、不兜底）：误召会降到 **5/23**（= 向量腿自身残余），召回同样 8/8+8/8。**没有采用的理由**：那会把"这条腿**质量不足**"改写成"这条腿**失败**"，越过 `_try_vector_match` 的降级契约、并顺带替 TF-IDF 腿做决定 —— 那是 `_RRF_QUALITY_MIN` 那条闸的语义（它敢不兜底，是因为它**已经看过** TF-IDF 腿的有界分；单向量路**从不计算** TF-IDF 分）。⇒ 列为可选后续项（§⑥-G6），数字已给全。

### A.4 四格数字（改前 vs 改后，原始输出）

```
### backend=st_backend indexed=28 load=72.9s skills=28          ← 改后
### zh e2e_vec recall=8/8   e2e_rrf(bm25) recall=8/8
### en e2e_vec recall=8/8   e2e_rrf(bm25) recall=8/8
### neg e2e_vec nonempty=12/23   e2e_rrf(bm25) nonempty=10/23
```

| 口径（真库 28 条技能，23 条负样本 / 8 中文 / 8 英文） | 改前 | 改后 | 判定 |
|---|---|---|---|
| `match(use_vector=True)` 负样本**非空** | **22/23** | **12/23** | ✅ 回落（−10） |
| ├ 由**向量腿**返回 | 22 | **5** | 闸挡下 17 条 |
| ├ 由 **TF-IDF 兜底**返回 | 0 | 7 | 见下"诚实说明" |
| └ 返回空 | 1 | 11 | |
| **中文**正样本召回 | **8/8** | **8/8** | ✅ **不降** |
| **英文**正样本召回 | **8/8** | **8/8** | ✅ **不降** |
| 对照：`match(use_vector=True, use_bm25=True, fusion_mode="rrf")` 负样本非空 | 10/23 | 10/23 | 未受影响（不在本闸路径上） |
| 对照：向量腿自身（`adapter.search`，腿级）非空 | 23/23（`12345` 除外） | 23/23（同） | 本卡**没有**动腿级行为，闸在 loader 侧 |

**改后仍非空的 12 条（逐条，含通路）**：

```
1+1 等于几                v_top1=0.3947  → tfidf 兜底
跑步前要做什么热身          v_top1=0.5042  → 向量腿（≥0.45）
book a flight to tokyo    v_top1=0.3853  → tfidf 兜底
what is the weather today v_top1=0.3678  → tfidf 兜底
recommend a good song     v_top1=0.4049  → tfidf 兜底
how much is bitcoin now   v_top1=0.3646  → tfidf 兜底
who won the world cup     v_top1=0.3186  → tfidf 兜底
tell me a joke            v_top1=0.4247  → tfidf 兜底
def print_hello_world function v_top1=0.4654 → 向量腿（≥0.45，**没挡住**）
帮我删除文件               v_top1=0.5025  → 向量腿
重启服务器                 v_top1=0.4703  → 向量腿
现在几点了                 v_top1=0.474   → 向量腿
```

**诚实说明（不许只报好消息）**：

1. **7 条走 TF-IDF 兜底的不是本卡引入的**：它们**改前就非空**（RET-1R §1.4 记录的"6 条由 TF-IDF 兜底"是同一批）。我的闸把它们**还原成改前的通路**；TF-IDF 腿在 `min_score=0.01` 下对这些 query 本来就有候选（实测腿级非空 **7/23**），这是**另一条腿的既有行为**，不在本闸的裁决范围内。
2. **RET-1R 净增的 3 条里只修好 2 条**：`best pizza recipe`（0.3865）与 `asdfghjkl`（0.3382）被挡回空；**`def print_hello_world function`（0.4654 ≥ 0.45）仍在**。
3. **向量腿自身仍误召 5/23** —— 0.45 是**既有**常量，负样本簇上界（0.5042）比它高。抬到 0.51 可在本 23 条上做到 0 误召且不伤召回，但那是**重新标定/另发明阈值**（且在这 23 条上过拟合），卡面明确禁止，**本卡不做**（数据放在这里供决策）。

### A.5 护栏

新文件 `tests/unit/test_gate1_single_vector_quality_gate.py`（**6 个用例**）：

| 用例 | 守什么 |
|---|---|
| `test_low_top1_is_not_returned_as_the_answer` | **行为**：假模型造出 top1=0.3536（<0.45）的候选 ⇒ 单向量路**不得**把它当答案（`retrieval_method != "vector"` 且 `fallback_used is True`）。**改前红** |
| `test_top1_at_or_above_threshold_still_goes_vector` | **行为（防假绿）**：达标候选（余弦 1.0）必须照常走向量路，并真的返回期望技能 |
| `test_gate_uses_bounded_cosine_not_raw_score` | 闸比的是**有界余弦**（前置断言它等于 1/sqrt(8)），不是名次分/无界分 |
| `test_threshold_value_is_the_pre_existing_one` | **口径**：`_SINGLE_PATH_MIN_TOP1 == 0.45`（本卡没调阈值） |
| `test_both_call_sites_reference_the_same_constant` | **口径（源码级）**：融合路与单向量路都引用**同一个类常量**；融合路里不得再出现就地定义的 `SINGLE_PATH_MIN_TOP1` |
| `test_gate_only_guards_the_single_vector_path` | **结构**：闸在 `_try_vector_match` 内、且以 `return None` 交回降级语义 |

假模型用**关键词袋**把余弦做成可控量（余弦 = 共享关键词数 / sqrt(|q|·|d|)），因此"低于阈值/达到阈值"两侧都能被精确构造；真库四格数字由 `probe_a.py` 提供（不依赖 CI 上的 BGE-m3）。

### A.6 非空转自证（**去掉修复 ⇒ 新测试变红**；断言原文）

手段：`Temp\gate1\reverse_patch.py`（读 `gate1_patch.json` 里 3 组**逐字**替换，每处断言 `count == 1`，可重复执行）。

```
$ python reverse_patch.py drop
drop     file_sha256=515B23B4FB12FFAA9DE293519E86DE6449F9B753F1EF55472A17EC8CE1FCC5D3

$ python -m pytest tests/unit/test_gate1_single_vector_quality_gate.py::TestSingleVectorQualityGate -q --tb=long
>       assert result.retrieval_method != "vector", (
E       AssertionError: 向量腿 top1（0.3536）低于单路兜底阈值（0.45）时，单向量路不得把这条腿的
E       结果当成答案交出去（RET-1R 残留 R2-1：这条路径上原本没有任何质量闸）；实际
E       retrieval_method=vector，matches=['wide-vocabulary-skill']
E       assert 'vector' != 'vector'
tests\unit\test_gate1_single_vector_quality_gate.py:155: AssertionError
FAILED ...::TestSingleVectorQualityGate::test_low_top1_is_not_returned_as_the_answer
========================= 1 failed, 2 passed in 1.98s =========================
```

**红的是行为断言**（不是 `AttributeError`/`KeyError` 这类 API 面）：为此我在测试里刻意用模块常量 `_PREEXISTING_THRESHOLD = 0.45` 写**前置条件**，不引用类常量 —— 否则摘掉修复时会先红在"常量不存在"上，掩盖真正的行为回归。另外两条口径/结构用例在摘掉时自然红（`AttributeError: type object 'SkillLoader' has no attribute '_SINGLE_PATH_MIN_TOP1'`、`assert 'self._SINGLE_PATH_MIN_TOP1' in ...`），共 **5 failed / 1 passed**（`test_top1_at_or_above_threshold_still_goes_vector` 是防假绿的反向用例，摘掉修复后**本来就该绿**）。

```
$ python reverse_patch.py restore
restore  file_sha256=7C8723AE787983AB737F4F37E580A7363FD2CC5B06A0DAE11C81EF992E8E84B0
EXPECT 7C8723AE787983AB737F4F37E580A7363FD2CC5B06A0DAE11C81EF992E8E84B0
ACTUAL 7C8723AE787983AB737F4F37E580A7363FD2CC5B06A0DAE11C81EF992E8E84B0   ← 逐字节相同
$ python -m pytest tests/unit/test_gate1_single_vector_quality_gate.py -q
6 passed in 1.47s
```

**交叉校验（本卡的"改前"到底是不是 RET-1R 的终态）**：`drop` 得到的 sha256 `515B23B4…` 与 **RET-1R §4.3 自己记录的 loader.py 终态 sha 逐字相同** ⇒ 本卡基线 = RET-1R 修复后的字节级状态，探针没有踩到别的卡的改动。

**本卡对 `loader.py` 的逐行 diff**（同一次 drop 的副本 vs 改后副本，`git diff --no-index`）：

```
 1 file changed, 51 insertions(+), 11 deletions(-)
```
其中**行为行**只有：`+ _SINGLE_PATH_MIN_TOP1 = 0.45`、`+ if matches[0].score < self._SINGLE_PATH_MIN_TOP1:`、`+ logger.info(...'vector.single_path_low_score_rejected'...)`、`+ return None`（4 行新增），以及 2 行改引用（融合路的比较行与日志行 `SINGLE_PATH_MIN_TOP1` → `self._SINGLE_PATH_MIN_TOP1`）；**其余 47 行新增/9 行删除全是注释**（把原有数据支撑注释搬进类常量）。

---

## B · 「只有 BM25 命中」是否造成假阴（RET-1R 的 R1-2）

### B.1 构造思路

R1-2 的判据是：质量闸的第二条判据要求 **`bounded_score is not None`**（"至少有一条有界腿真的给出过相似度"）。RET-1R 加这条是为了挡住 S10-03 的噪声查询（`min_score=0.3` 时 tfidf 腿被清空 ⇒ 只剩无界 BM25 分）。**它自己说这条未验证。**
关键洞察：**`bounded_score` 是"腿里有没有值"，而腿有没有值是调用方 `min_score` 决定的** —— 只要把 `min_score` 提到生产值 0.3，**任何**覆盖度低于 0.3 的中文 query 都会退化成"只有 BM25 命中"。

配置：**向量腿不可用**（新进程未 `ensure_indexed` ⇒ `adapter._st_backend is None` ⇒ loader 的 fast-exit `BM25 fallback is not real vector search`），这正是 `config.yaml` 写的降级模式（"sqlite-vec 不可用时 SkillLoader 内部自动降级 TF-IDF+BM25"）与 CI 的 `SKILLS_OFFLINE=1` 形态，也是 RET-1R 测 R-1 时用的配置。

### B.2 实测：**被误拒**（真库，8 条中文 query，BM25 腿判得果断且答案正确）

```
### bm25_available=True  adapter(before)=None     （向量腿 skipped=True，逐条已核对）

--- min_score = 0.01（= RET-1R R-1 的测量口径）---
### B1 zh min_score=0.01  recall=8/8
      写测试时要避免哪些反模式      bounded=0.0909 ev=True ratio=1.3359 dec=pass  tfidfN=4
      给测试加 Mock 有什么坑        bounded=0.2857 ev=True ratio=3.2471 dec=pass  tfidfN=4
      生成后端接口时怎么加结构化日志…  bounded=0.5263 ev=True ratio=4.8873 dec=pass  tfidfN=10
      前后端状态不同步、有竞态该怎么防 bounded=0.3077 ev=True ratio=4.5212 dec=pass  tfidfN=4
      乐观更新回滚和请求取消怎么写     bounded=0.6154 ev=True ratio=10.9148 dec=pass tfidfN=2
      做一个不用查文档就能看懂的自解释界面 bounded=0.2353 ev=True ratio=2.4265 dec=pass tfidfN=4
      界面设计时怎么把帮助信息集成进去   bounded=0.4667 ev=True ratio=5.0747 dec=pass  tfidfN=4
      代码交付前怎么做自测和审计报告    bounded=0.1429 ev=True ratio=8.0875 dec=pass  tfidfN=10

--- min_score = 0.3（= config.yaml orchestrator.semantic_layer.min_score，**生产值**）---
### B1 zh min_score=0.3   recall=4/8          ← 4 条真命中被整单拒绝
      写测试时要避免哪些反模式      bounded=None ev=False ratio=1.3359 dec=reject tfidfN=0
      给测试加 Mock 有什么坑        bounded=None ev=False ratio=3.2471 dec=reject tfidfN=0
      生成后端接口时怎么加结构化日志…  bounded=0.5263 ev=True ratio=4.8873 dec=pass  tfidfN=1
      前后端状态不同步、有竞态该怎么防 bounded=0.3077 ev=True ratio=4.5212 dec=pass  tfidfN=1
      乐观更新回滚和请求取消怎么写     bounded=0.6154 ev=True ratio=10.9148 dec=pass tfidfN=1
      做一个不用查文档就能看懂的自解释界面 bounded=None ev=False ratio=2.4265 dec=reject tfidfN=0
      界面设计时怎么把帮助信息集成进去   bounded=0.4667 ev=True ratio=5.0747 dec=pass  tfidfN=1
      代码交付前怎么做自测和审计报告    bounded=None ev=False ratio=8.0875 dec=reject tfidfN=0
```
（英文 8 条两种 `min_score` 下都是 8/8：英文 query 的 token 命中率高，覆盖度天然 ≥0.3。）

**结论：构造出来了，且实测被误拒。** 4 条被拒 query 全部满足：`tfidf_candidate_count=0`（被 `min_score=0.3` 清空）、`vector_candidate_count=0`（向量腿跳过）、**BM25 top1 就是正确答案、裕度 1.34 / 3.25 / 2.43 / 8.09（远高于 1.2 的"果断"门槛）** —— 却因为 `bounded_score is None` 被**整单 reject、返回 `[]`**。
⇒ **RET-1R 的 R-1 修复在生产 `min_score` 下不生效**：它的 e2e 8/8 是在 `min_score=0.01` 下测的。

### B.3 第二种机制：**唯一正确命中被拒，恰恰因为它唯一**（合成语料）

合成语料：`zxq-4471-gateway`（描述只含稀有标识符 `ZXQ-4471`）+ 3 条干扰技能；query 是**真实形态的报错排查请求**：

```
--- min_score=0.01 ---
q=请帮我看一下线上网关报错 ZXQ-4471 这个故障该怎么排查处理  hit=True  bm25leg=[('zxq-4471-gateway',2.7531),('deploy-notes',2.2928)]
   bounded=0.087  ratio=1.2008  ev=True  dec=pass
q=麻烦查一下 ZXQ-4471 是什么错误码，生产环境一直在报          hit=False bm25leg=[('zxq-4471-gateway',2.7531)]
   bounded=0.1111 ratio=None    ev=False dec=reject          ← BM25 腿只有 1 条候选 ⇒ 裕度算不出来
--- min_score=0.3 ---
q=请帮我看一下线上网关报错 ZXQ-4471 这个故障该怎么排查处理  hit=False bm25leg=[(…,2.7531),(…,2.2928)]
   bounded=None   ratio=1.2008  ev=False dec=reject
q=麻烦查一下 ZXQ-4471 是什么错误码，生产环境一直在报          hit=False bm25leg=[('zxq-4471-gateway',2.7531)]
   bounded=None   ratio=None    ev=False dec=reject
```

两条**互相独立**的假阴机制：
1. **`min_score` 决定 `bounded_score` 是否为 None**（B.2）⇒ 生产 `min_score=0.3` + 向量腿不可用时，**长中文 query 一律被拒**；
2. **BM25 腿只有 1 条候选时 `_bm25_decision_ratio` 返回 `None`**（源码：`if not matches or len(matches) < 2: return None`）⇒ **"唯一正确命中"恰恰拿不到"果断"证据**（稀有错误码/专有名词查询的典型形态）。这条**与 `min_score` 无关**，两个值下都被拒。

### B.4 附带发现：同一条判据在 `min_score=0.01` 下**重新放行了 S10-03 的噪声查询**

```
q=2 加 3 等于多少？只回答数字    min_score=0.01 → ids=['self_reflection', 'pd-dispatching-parallel-agents-b8065ccd-skill']
   gate={'bounded_similarity': 0.1, 'bm25_raw_unbounded': 4.6614, 'bm25_decision_ratio': 1.5433,
         'bm25_rank1_evidence': true, 'decision': 'pass'}          ← **放行**
q=2 加 3 等于多少？只回答数字    min_score=0.3  → ids=[]  decision='reject'（bounded=None）  ← 拦下
q=自我反思一下你的回答          min_score=0.01 / 0.3 → ids=['self_reflection']（真命中，两边都对）
```
（真库 + 真 BM25，向量腿跳过。）

⇒ **RET-1R 的 R-1 判据在 `min_score=0.01` 下把 TASK-S10-03 修掉的假阳重新放了进来**：`bounded_similarity=0.1`（不是 None ⇒ 条件②满足）+ `ratio=1.5433 ≥ 1.2` ⇒ 过闸、返回 2 条候选。它没被测试抓住，是因为**它的真库锚 `test_噪声查询_不得有候选` 用的是 `min_score=0.3`**，而 0.01 形态只在合成用例里出现，那条合成用例的 BM25 只有 1 条候选 ⇒ 恰好绕过了。
**这是本卡发现的新缺陷（不是 RET-1R 的登记项），已如实登记（§⑥-G2），本卡未修。**

### B.5 修法 / 取舍（卡面要求：给修法，或明确说"有意取舍"并给代价）

**根因一句话**：判据②用的是「**腿里有没有值**」，而"腿里有没有值"被**调用方的 `min_score`** 决定 ⇒ 同一判据在两个方向同时失灵：`min_score` 大 ⇒ 误拒真命中（B.2/B.3-1）；小 ⇒ 放行噪声（B.4）。这是**耦联**，不是阈值问题。

| 修法 | 做法 | 代价 / 风险 |
|---|---|---|
| **F1**（正确方向）**解耦**：闸的"有界证据"不从被 `min_score` 截断后的腿取，改用**腿级独立阈值** | 判据②变成"该腿产出过候选且能算出有界分" | ⚠️ **会重新打开 S10-03 假阳**（B.4 就是现状）；必须先找到能把"噪声 0.1 + ratio 1.5433"与"真命中 0.0909~0.2857 + ratio 1.34~8.09"分开的新证据 —— 而 RET-1R §2.2(c) 已实测**没有任何有界标量能分开**，我复核了 ratio 区间**重叠**（1.3359 < 1.5433 < 3.2471）⇒ **需要重新设计判据，不是改一个数** |
| **F2** 只修机制 2（唯一候选） | 允许 `len(bm25_matches) == 1` 时用替代证据（如绝对分分位数） | ⚠️ 又回到**无界分**比较（TASK-S10-03 的量纲混用陷阱）；且 S10-03 噪声在 `min_score=0.01` 下也只有 2 条候选，区间同样重叠 |
| **F3** 不动判据，改调用方口径 | 语义层把 `min_score` 从 0.3 降到 0.01 | ⚠️ `min_score` 在编排层是"top1 是否足够像"的**最终门槛**，降它会放松下游一切；且 RRF 归一化 top1 恒 ≈1.0，`min_score` 对融合结果本就不起筛选作用，改动语义不清 |
| **F4** 明确取舍：登记为**有意取舍** | 保持现状（宁可拒真命中，也不放噪声） | 代价已量化：**向量腿不可用 + `min_score=0.3` 时中文端到端 8/8 → 4/8**；稀有标识符查询（唯一候选）**无论 `min_score` 都被拒** |

**本卡裁决**：F1 是正确方向但要**重新设计判据并单独定标**（需同时满足 B.2 召回与 B.4/S10-03 拒噪），超出"处理残留"范围；F3 会改编排层语义；F2 踩已知量纲陷阱。⇒ **如实登记 + 给修法方向 + 给代价数字**（本节），不在本卡实施。**这条残留的严重度高于 RET-1R 登记时的估计**（它说"本卡数据集里没有这种样本，未验证"；实测**本仓真库上就有 4 条**）。

---

## C · 有界相似度与 query 长度强相关（RET-1R 的 R1-3）

### C.1 相关性是**精确**的：有界相似度 = H / N

有界相似度（= `_match_score` = `score_breakdown['tfidf_score']`）的定义就是**命中率** `hits / len(query_tokens)`。
构造：取 5 条真中文意图，逐档追加**真实的补充说明**（"想请教一下 / 大家平时都怎么处理 / 我最近在重构 / 一个老项目 / 很多依赖 / 都需要隔离 / 而且测试跑得很慢 …"），6 档（14→51 字符）：

| base 技能 | 命中 token 数 H（= cov × tokens） | 实测（字符 → tokens → 有界相似度） |
|---|---|---|
| `testing-anti-patterns` | **2** | 14→7→**0.2857** ｜ 20→11→**0.1818** ｜ 29→20→**0.1000** ｜ 34→25→**0.0800** ｜ 40→31→**0.0645** ｜ 45→36→**0.0556** |
| `code-observability` | **10** | 20→19→**0.5263** ｜ 26→23→**0.4348** ｜ 35→32→**0.3125** ｜ 40→37→**0.2703** ｜ 46→43→**0.2326** ｜ 51→48→**0.2083** |
| `frontend-state-sync` | **4** | 16→13→**0.3077** ｜ 22→17→**0.2353** ｜ 31→26→**0.1538** ｜ 36→31→**0.1290** ｜ 42→37→**0.1081** ｜ 47→42→**0.0952** |
| `self-explanatory-ui` | **4** | 18→17→**0.2353** ｜ 24→21→**0.1905** ｜ 33→30→**0.1333** ｜ 38→35→**0.1143** ｜ 44→41→**0.0976** ｜ 49→46→**0.0870** |
| `engineering-test-delivery` | **2** | 15→14→**0.1429** ｜ 21→18→**0.1111** ｜ 30→27→**0.0741** ｜ 35→32→**0.0625** ｜ 41→38→**0.0526** ｜ 46→43→**0.0465** |

⇒ 每条 base 的 **H 恒定**（分子只取决于"这个技能与这条意图能对上几个 token"，与加长无关），**分母 = query token 数 N 随长度线性增长** ⇒ `cov = H/N` **严格反比于长度**（中文 bigram 下 tokens ≈ 字符数）。这就是 R1-3"强相关"的**精确形式**。
**副作用同样精确**：`cov ≥ 0.3` 等价于 `N ≤ H/0.3` ⇒ **误拒起点 = H/0.3 个 token ≈ 3.33·H 个汉字**。

### C.2 多长开始被误拒：取决于**哪条腿在**（三套配置实测）

数据来源：P1/P2/P3 列 = `probe_c.py`（真库 + BGE-m3；P2/P3 用 `_st_backend=None` 触发 loader 的 fast-exit 模拟"向量腿不可用"）；**P4 列 = `probe_c2.py`**（不需要模型：纯 TF-IDF 单路，`match(q, min_score=0.3)`，即编排器语义层口径）：

| base | **P1 生产**：vector+bm25+tfidf, `min_score=0.3` | **P2 向量腿不可用**, 0.3 | **P3 向量腿不可用**, 0.01 | **P4 纯 TF-IDF**（语义层口径 0.3） |
|---|---|---|---|---|
| `testing-anti-patterns` | 到 45 字符**全命中** | **14 字符起就被拒**（cov 0.2857） | 到 45 字符全命中（BM25 裕度 3.25→1.80） | **14 字符起就被拒** |
| `code-observability` | 到 51 字符**全命中** | 35 字符内通过，**40 字符起被拒**（0.2703） | 到 51 字符全命中（裕度 4.89） | 35 字符内通过，**40 字符起被拒** |
| `frontend-state-sync` | 到 47 字符**全命中** | **22 字符起被拒**（0.2353） | 到 47 字符全命中（裕度 4.52→1.89） | **22 字符起被拒** |
| `self-explanatory-ui` | 到 49 字符**全命中** | **18 字符起被拒**（0.2353） | 到 49 字符全命中（裕度 2.43→2.16） | **18 字符起被拒** |
| `engineering-test-delivery` | 到 46 字符**全命中** | **15 字符起就被拒**（0.1429） | 21 字符内通过，**30 字符起被拒**（裕度塌到 **1.075 < 1.2**） | **15 字符起就被拒** |

**回答"多长开始误拒"**：
- **生产配置（向量腿在线）**：本卡 6 档（≤51 字符）**没有被误拒** —— 因为闸取 `max(tfidf_score, vector_score, …)`，而向量腿余弦对长度**几乎不敏感**（实测 0.6009~0.6982，全区间都在 0.3 以上）。
- **向量腿不可用（`min_score=0.3`）或纯 TF-IDF（0.3）**：**误拒起点 = H/0.3 个 token**，实测 **14 / 15 / 18 / 22 / 40 字符**（H=2 的两个 base **连基线 query 都过不了**）。
- **向量腿不可用 + `min_score=0.01`**：BM25 的第二条判据能救，但**它也随长度退化**：`engineering-test-delivery` 的裕度从 8.09 塌到 **1.075**，**30 字符起被拒** —— 即"绕"的那个办法**自己也与长度相关**（新数据点）。

### C.3 与长度无关的量（用于判断"该不该改"）

| 量 | 随长度 | 说明 |
|---|---|---|
| `tfidf_score`（有界相似度） | **∝ 1/N（强相关）** | 就是 R1-3 说的那条 |
| `vector_score`（余弦） | **基本不变**（0.60~0.70） | 所以生产配置下没有观察到长度误拒 |
| `bm25_decision_ratio` | **弱相关但会塌**（8.09→1.075） | 长 query 的补充词会稀释 BM25 的区分度 |
| `rrf_normalized`（top1） | 恒 ≈1.0 | 排名分，本就不含绝对质量信息 |

### C.4 结论：**不值得在本卡修，只登记**（代价数据如下）

1. **口径是全局共享的**：`tfidf_score` 这个键同时被 ① RRF 质量闸（`_RRF_QUALITY_MIN=0.3`）② TASK-S10-03 的真库锚 ③ 融合路"单路兜底"语义 ④ 编排层 `_bounded_relevance`（源码注释写明"同键名、同语义"）消费。把 `hits/N` 改成任何长度无关形式（`hits/√N`、`hits/|技能token|`、饱和函数）等于**同时改四处语义**，每处的 0.3 都要重标。
2. **验收集太小且结论已知**：RET-1R §2.2(c) 实测**没有任何有界标量**能把"被误拒的 4 条真命中（0.0909~0.2857）"与"已通过的负样本（0.4/0.5）"分开 —— 长度归一化只会**同时抬高或同时压低两边**，无法在 28 条技能 / 26 条 query 上验收"确实变好"。
3. **收益面很窄**：只对**向量腿不可用**的部署模式有效（生产配置下本卡 51 字符内零误拒）；该模式下真正的救命绳是 BM25 的第二条判据（P3 列：4/5 个 base 全长度存活）。
4. **有更便宜的替代**：B 卡说明这条路径的真正毛病是 **`min_score` 与闸耦联**（§B.5），比"改相似度定义"小得多。

⇒ **裁决：只登记，不修**。登记内容 = §C.1 的精确关系 `cov = H/N` + §C.2 的三套配置实测起点 + §C.3 的"哪个量随长度变"。**不修不是因为它不真实，而是因为"修"要动全局口径而收益只覆盖一个降级模式**（这就是卡面要的代价数据）。

---

## D（独立小项）· 多进程并发重建同一台账（RUNBOOK-1 登记未压测）

### D.1 方法与铁律

- **绝不碰生产 `data/descriptors.json`**：每个用例把生产台账**只读复制**成 `%TEMP%\gate1\d2\<case>\ledger.json`，所有写入落在副本上（生产 sha256 见 §D.5）。
- 并发主体 = RUNBOOK-1 的重建入口同一条函数链：`plan_backfill(main_path=…)` + `run_backfill(planned, registry_path=<副本>, ingest_stages=…)`，**N 个独立进程同时启动**（`subprocess.Popen`，stdout/stderr 落文件，不用管道）。
- 矩阵：`same`（同输入）N=2/4/8 **各 10 轮**；`diverged`（每个进程看到的主轨**各多一条自己的技能** —— 模拟"多张卡各自加技能后重建"）N=2/4/8 各 3 轮；`ingest`（RUNBOOK-1 的自动收口路径）N=4/8 各 3 轮；另加**读者风暴**（1 写 + 8 读者 × 400 次读）。
- 审计落点用 `AUDIT_DB_PATH`/`AUDIT_ROOTS_PATH` 指向 tmp，**不碰生产审计链**。

### D.2 基线（单进程串行，2 次）

```
### baseline_single_sha ['325AC2D14A692007C92C3F99E3B4A8FA75641E318AD181EEB727DAA3022FDEDE',
                          '325AC2D14A692007C92C3F99E3B4A8FA75641E318AD181EEB727DAA3022FDEDE']
```
⇒ 单进程是**不动点**（重建对已是终态的台账是逐字节 no-op），与 RUNBOOK-1 §5.2 一致。

### D.3 同输入并发：**出现损坏 + 半成品台账**（30 轮里 1 轮；D1 另 1 轮）

```
[same n=2 rep=0..9]  final_sha=325AC2D14A692007 desc=32 corr=[]                              ← 10/10 正常
[same n=4 rep=0..9]  final_sha=325AC2D14A692007 desc=32 corr=[]                              ← 10/10 正常
                     （D1 的同参数 n=4 另有一轮 corr=['ledger.corrupted.json']，见下）
[same n=8 rep=0..9]  rep=7: final_sha=BA095A5DF306F1E1 desc=30 corr=['ledger.corrupted.json']  ← ★ 异常
                     其余 9 轮 final_sha=325AC2D14A692007 desc=32 corr=[]
```

**异常轮的原始证据（全部保留在 `Temp\gate1\d2\same_n8_rep7\`）**：

```
worker w1 捕获到的**产品自己的告警**：
  [DescriptorRegistry] 存储损坏已备份到 …ledger.corrupted.json:
    [Errno 13] Permission denied: '…ledger.json'

ledger.corrupted.json  sha = 325AC2D14A692007C92C3F99E3B4A8FA75641E318AD181EEB727DAA3022FDEDE  (32 条 = 原件)
ledger.json（最终）    sha = BA095A5DF306F1E12F55C64860C6EC91986F326663B70660E89FE08799CCEF7B  (30 条, 128342 bytes)
missing from final: ['cp.builtin.read_file', 'cp.builtin.write_file']
```

**机制（链路完整）**：
1. 多进程各做 `load() → 内存改 → 整体 os.replace 落盘`；Windows 上 `os.replace` 与别的进程正在 `open()` 读同一文件时会产生**共享冲突（`PermissionError [Errno 13]`）**；
2. `DescriptorRegistry.load()` 的 `except (json.JSONDecodeError, ValueError, OSError)` **把 `OSError` 也当成"存储损坏"** ⇒ 执行 `self._path.rename(backup)` —— **把瞬时读失败升级成"把台账搬到 `.corrupted.json`"**，然后从**空注册表**继续；
3. 该进程把计划里 30 条资产**全部重新 register**（`register: 30`），写出**只含 30 条的半成品台账**；
4. 全量快照覆盖写 ⇒ **最后落盘的那份（半成品）成为最终台账**，原 32 条只留在 `.corrupted.json` 里（**产品不会自动恢复**）。
⇒ 卡面问的四件事一次性全中：**交错（同一轮里两个进程看到不同台账：w1 空/30 条 vs w5 32 条）✓ 损坏（corrupted 改名）✓ sha 不确定（同输入同参数得 325AC2D1 vs BA095A5D）✓ 半成品台账（30/32）✓**。

**另一轮的进程级异常（诚实归属）**：`same n=4 rep=4` 有 1 个 worker **exit code 1**，日志是 `PermissionError: [Errno 13] Permission denied: …ledger.json` —— 但**抛在探针自己的 `sha()` 辅助函数里**（探针在 `open()` 上踩到同一个 OS 级共享冲突），**不是产品代码崩的**；它同时反证了"`Errno 13` 在并发下真实可复现"。**这条算探针的坑，不算产品缺陷。**

### D.4 不同输入并发：**丢失更新 9/9 全中** + 审计链 seq 冲突

```
[diverged n=2 rep=0..2] final_desc=33  surviving_probe_ids=['cp.skill.gate1-probe-1']                 （期望 2 条）
[diverged n=4 rep=0..2] final_desc=33  surviving_probe_ids=['cp.skill.gate1-probe-3']                 （期望 4 条）
[diverged n=8 rep=0..2] final_desc=33  surviving_probe_ids=['cp.skill.gate1-probe-6' / '-5' / '-4']   （期望 8 条）
```
⇒ **N 个进程各新增 1 条，最终只剩 1 条**（32→33 而不是 32+N）：每个进程持有**全量内存快照**并整体覆盖写 ⇒ **后写者覆盖先写者，N−1 条静默丢失**（9/9 轮，无一例外；存活者随调度变化）。这正是"多张卡并行重建同一台账"的真实形态，**没有锁、没有冲突检测、也不报错**（所有进程 exit 0、`batches_ok=[True]`）。

另有两轮（`diverged n=8` rep0/rep2）捕获到**产品告警**：
```
审计链 seq 冲突，本进程分发未生效：UNIQUE constraint failed: audit_chain
```
⇒ 审计链序号分配**没有跨进程互斥**，冲突时**该进程的审计分发被静默丢弃**（best-effort）。

### D.5 结论与生产台账自证

| 用例 | 轮数 | 结论 |
|---|---|---|
| 同输入 N=2 / N=4 | 10 + 10 | ✅ 未见异常（但 D1 的同参数 n=4 出现过 1 次损坏） |
| 同输入 N=8 | 10 | ⚠️ **1 次损坏 + 半成品台账（30/32）** |
| `ingest_stages=True` N=4 / N=8 | 3 + 3 | ✅ 6 轮全部落在不动点（`325AC2D1`，desc=32） |
| 不同输入 N=2/4/8 | 3+3+3 | ❌ **丢失更新 9/9**；2 轮伴审计链 seq 冲突 |
| 读者风暴（1 写 + 8 读 × 400） | 3200 次读 | ✅ 0 次解析错/IO 错、只有 1 个 sha ⇒ **纯读者不会看到撕裂内容**（问题不在 `os.replace` 的原子性，而在 §D.3-2 的异常处置与 §D.4 的全量覆盖写） |

**卡面要求"结论开放：证实或证伪都算交付" ⇒ 本条是证实**：RUNBOOK-1 登记的"多进程并发重建同一台账未压测"**是真实风险**，且比"未压测"更严重 —— 它不只是"可能有竞态"，而是**可复现的数据丢失/台账改名**。

**建议修法（本卡 D 只压测，未改代码）**：
1. `load()` **只对真正的解析失败**（`json.JSONDecodeError`）走"备份 + 重置"；`OSError`（尤其 `EACCES/EINVAL`）必须**重试**（短退避）而不是改名 —— 现在这一步把"瞬时读失败"变成了"台账被搬走"；
2. 重建的 `load→save` 全程加**跨进程锁**（或写前 sha/mtime 校验 + 冲突时拒绝/合并），别让全量快照覆盖写；
3. 审计链 `seq` 分配放进同一把锁（或改成数据库自增/事务）。

**生产台账未变的原始自证**：
```
prod_ledger_sha_before = 325AC2D14A692007C92C3F99E3B4A8FA75641E318AD181EEB727DAA3022FDEDE
prod_ledger_sha_after  = 325AC2D14A692007C92C3F99E3B4A8FA75641E318AD181EEB727DAA3022FDEDE   ← 逐字节相同
### prod ledger unchanged: True
```

---

## ⑤ 回归结果（全部原始计数，**未放宽任何断言**）

| 测试文件 | 结果 | 基线 |
|---|---|---|
| `test_ret1r_negative_query_scope.py` | **22 passed** | 22 |
| `test_ret1r_bm25_quality_gate.py` | **10 passed** | 10 |
| `test_s10_03_retrieval_quality_gate.py`（含真库噪声锚，`min_score=0.3`） | **13 passed** | 13 |
| `test_three_legs_meta_zh_parity.py` | **15 passed** | 15 |
| `test_skill_meta_zh_recall.py` | **46 passed** | 46 |
| `test_settings_registry.py` | **56 passed** | **56**（本卡新增 env = 0） |
| 本卡新文件 `test_gate1_single_vector_quality_gate.py` | **6 passed** | — |
| **卡面 6 个文件 + 本卡新文件合跑** | **168 passed in 54.74s** | 162 + 6 |
| `test_vector_skill_searcher.py` / `test_bm25_skill_searcher.py` / `test_skills_mgmt.py` / `test_skill_description_single_source.py` / `test_retrieval_silent_failures.py`（**本卡自己加跑**，覆盖被改函数的直接调用方） | **176 passed, 1 xfailed** | 同（1 xfail 是既有 `Precision@3=0.4444 < 0.6`，与 RET-1R §3.3 同一既有缺陷） |
| **0 failed** | ✅ | |
| 是否跑全量 `tests/unit` | ❌ **未跑**（卡面要求；47–62 分钟） | 见 §⑥-G4 |

**本卡没有放宽/删除任何断言**，也没有新增 env ⇒ `agent/settings/registry.py` **零改动**。

---

## ⑥ 未验证项与残留风险（不许粉饰）

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| **G1** | 单向量路仍误召 **5/23**；RET-1R 净增 3 条里仍有 1 条（`def print_hello_world function`，0.4654） | ⚠️ 残留 | 0.45 是**既有**常量，负样本簇上界 0.5042 高于它。抬到 **0.51** 可在本 23 条上 0 误召且不伤召回（正样本最小 0.5195），但那是**重新标定 + 23 条上过拟合**，卡面禁止，只登记数据 |
| **G2** | **RET-1R 的 R-1 判据在 `min_score=0.01` 下重新放行 S10-03 噪声查询**（§B.4） | ❌ **新发现，未修** | 真库实测 `bounded=0.1 / ratio=1.5433 / decision=pass` ⇒ 返回 2 条候选；它的护栏只覆盖 0.3。修它需重新设计判据（§B.5-F1） |
| **G3** | **`min_score` 与质量闸判据②耦联**（§B.2/§B.5） | ❌ 新发现，未修 | 双向失灵：大 ⇒ 真命中被拒（真库中文 8/8→4/8）；小 ⇒ 噪声过闸（G2）。已给修法方向与代价 |
| **G4** | 未跑全量 `tests/unit` | ⚠️ 卡面要求 | 只跑了 12 个文件（**344 passed / 1 xfailed / 0 failed**）。`loader.py` 被很多模块 import，全量无新红建议由发起方复跑确认 |
| **G5** | 单向量路数字是**单次实测**（本机 BGE-m3，`HF_HUB_OFFLINE=1`，本地 HF cache） | ⚠️ 口径 | 与 RET-1R §6-R2-4 同口径；未多轮取平均、未验证与在线加载/其它机型差异。0.45 的分离性需在新模型上复核 |
| **G6** | 闸的**处置选择**（`return None` → TF-IDF 兜底）**保留 7 条 TF-IDF 兜底误召** | ⚠️ 有意选择 | 若改为"返回空结果不兜底"，误召为 **5/23**（数据已给），但会越过 `_try_vector_match` 的降级契约并替 TF-IDF 腿做决定。改与不改都能自洽，需一张更大的卡定调；本卡选"与既有闸逐字一致" |
| **G7** | **D 卡暴露的并发缺陷未修**（台账损坏改名 / 丢失更新 / 审计链 seq 冲突） | ❌ 未修（D 只压测） | 修法见 §D.5。生产当前是**单写者**（重建入口只有 CLI）⇒ 今天不会自己炸；但"多卡并行重建"一旦发生就是**静默丢数据** |
| **G8** | 并发失败率是**小样本估计** | ⚠️ 口径 | 同输入 N=8：10 轮 1 次异常（D1 的 N=4 另 1 次）⇒ 量级 **3%~10%**；这是**时序敏感**竞态，真实概率随负载/杀软/文件系统变化，本卡不给"精确故障率" |
| **G9** | **未**验证单向量路闸在 `use_vector=True` 且 `use_reranker=True` 时的行为 | ⚠️ 未验证 | `use_reranker=True` 且 `fusion_mode="none"` 时精排本就不生效（`match()` 只在 rrf 模式传 `use_reranker`），路径与本次一致，但没有专门造用例跑 |
| **G10** | **HEAD 在本卡执行期间被别的卡提交了** | ⚠️ 环境事实 | 会话开始时 `HEAD=5c9ace10`；期间出现提交 `f74dce16`（"…48 卡批次"，227 files，2026-09-27 00:03:43）。**不是本卡所为**（本卡 0 次 `git add/commit`，`git diff --cached` 里没有本卡文件）；已核对 `git show HEAD:agent/skills_mgmt/loader.py` **不含** `_SINGLE_PATH_MIN_TOP1` ⇒ 本卡改动**仍在工作区、未提交** |

---

## ⑦ 回滚

**只改了 1 个源文件**（`agent/skills_mgmt/loader.py`），回滚不需要 `git checkout`：

方案 1（精确、推荐）：反向补丁（每处断言 `count==1`，可重复执行）
```powershell
python C:\Users\Administrator\AppData\Local\Temp\gate1\reverse_patch.py drop
#   预期：loader.py sha 7C8723AE…（本卡改后）→ 515B23B4…（= RET-1R 终态，逐字节）
#   验证后再还原： python …\reverse_patch.py restore
```
方案 2（手工）：删这 3 处 ——
a) 类常量区：删 `_SINGLE_PATH_MIN_TOP1 = 0.45` 及其上方 GATE-1 注释块；
b) `_try_rrf_match`：把 `self._SINGLE_PATH_MIN_TOP1` 改回就地 `SINGLE_PATH_MIN_TOP1 = 0.45`（比较行与日志行各 1 处），并恢复原注释；
c) `_try_vector_match`：删掉"单路质量闸"那 3 行行为代码 + 注释块。

方案 3（整卡）：删 `tests/unit/test_gate1_single_vector_quality_gate.py` 与 `docs/audit_skill_governance/GATE1.md`。

**运行时**：本卡**没有**改动任何 `data/` 下文件 ⇒ **无需运行时回滚**。
**回滚判定标准**：`loader.py` sha256 回到 `515B23B4FB12FFAA9DE293519E86DE6449F9B753F1EF55472A17EC8CE1FCC5D3`（= RET-1R §4.3 记录值），且 `test_ret1r_*.py` 仍全绿；本卡新测试文件此时应红（**有意的告警**，见 §A.6）。

---

## ⑧ 残留物自证

### 8.1 仓库内：本卡只写了 3 个文件

| 文件 | 性质 | sha256 / 规模 |
|---|---|---|
| `agent/skills_mgmt/loader.py` | **改**（行为 +5/−2；含注释 +51/−11） | 改后 `7C8723AE787983AB737F4F37E580A7363FD2CC5B06A0DAE11C81EF992E8E84B0`；改前 `515B23B4FB12FFAA9DE293519E86DE6449F9B753F1EF55472A17EC8CE1FCC5D3` |
| `tests/unit/test_gate1_single_vector_quality_gate.py` | **新增**（6 例，12130 bytes） | 本卡护栏 |
| `docs/audit_skill_governance/GATE1.md` | **新增** | 本报告 |

`git status --porcelain` 对本卡相关路径的原始输出：
```
 M agent/skills_mgmt/loader.py
?? tests/unit/test_gate1_single_vector_quality_gate.py
```
**未改**：`agent/settings/registry.py`（0 个新 env）、`agent/descriptors/**`、`agent/skills_mgmt/vector_adapter.py`、`bm25_searcher.py`、`searcher.py`、`agent/tool_router*.py`、`agent/audit/**`、`plugins/**`、`yunshu-ui/**`、`.github/workflows/**`、`tests/conftest.py`、`tests/unit/test_date_shift_blindspots_guard.py`、`config.yaml`、`data/**`。

### 8.2 运行期文件 sha256（**本卡未变**）

| 文件 | sha256（前 32） | mtime | 判定 |
|---|---|---|---|
| `data/descriptors.json` | `325AC2D14A692007C92C3F99E3B4A8FA…` | 2026-09-26 15:22:34 | **未变 ✓**（并发压测前/后逐字节相同） |
| `data/audit/audit_chain.db` | `16272B29AB728FCFB8138E7714401034…` | 2026-09-26 15:22:34 | **未变 ✓**（mtime 早于本卡；并发用例审计落点走 `AUDIT_DB_PATH` 指向 tmp） |
| `data/audit/daily_roots.jsonl` | `04991152919985FC21EC3BECD877A9D0…` | 2026-09-26 10:07:00 | **未变 ✓** |
| `data/learned_workflows.json` | `CDF9BC8BD0651A6FCF71B12113531D11…` | 2026-09-27 00:03:44 | **不是本卡**（时间 = 别的卡批量提交时刻；本卡从未读写它） |

### 8.3 仓库外探针（可整目录删除）

`C:\Users\Administrator\AppData\Local\Temp\gate1\`：
`probe_a.py` / `probe_b.py` / `probe_b3.py` / `probe_c.py` / `probe_c2.py` / `probe_d.py` / `probe_d2.py` /
`reverse_patch.py` + `gate1_patch.json`（反向补丁）/ `loader_pre_copy.py` / `loader_fixed_copy.py`（逐行 diff 用） /
`tab*.py` / `probe_a_before.json` / `probe_a_after.json` / `probe_b.json` / `probe_b3.json` / `probe_c.json` / `probe_c2.json` /
`d\`（D1）/ **`d2\`（D2：异常物证 `same_n8_rep7\ledger.corrupted.json` 等全在）** / `synthB\`（B 卡合成语料）。

### 8.4 禁止项自检

- **0 次** `git add` / `git commit` / `git checkout` / `git stash` / `git reset`（只用只读 `git status` / `diff` / `show` / `rev-parse` / `reflog`）。
- **未 `taskkill` 任何进程**。收尾实测只剩**别的卡**的 2 个 pytest 进程（起始 00:09:27/28，命令行带 `-p t2probe` / `-p ci1_dumpmods` 插件 ⇒ **不是本卡**）；**本卡起的 5 次向量探针进程 + D1/D2 的约 150 个子进程全部已退出**。
- **未启动任何常驻服务**；未出网（`HF_HUB_OFFLINE=1`，模型从本地 cache 加载）。
- 未使用 `venv/`（系统解释器 Python 3.12.0）。
- 本卡**只读**了 `data/descriptors.json` / `data/skills_mgmt.json` / `data/skills_repo/**`（D 的台账副本从前两者复制）。

---

## ⑨ 一张图

```
A（单向量路没有质量闸，RET-1R 的 R2-1）:
  match(use_vector=True) → _try_vector_match → 只有 min_score 过滤 ⇒ 负样本 22/23
  修法 = 复用**既有**单路兜底阈值：if matches[0].score < _SINGLE_PATH_MIN_TOP1(0.45): return None
         （与 _try_rrf_match 的同名闸共用同一个类常量：口径一致，数值未改）
  四格：误召 22/23 → 12/23（向量腿自身 22 → 5；另 7 条落 TF-IDF 兜底，改前就在）
        中文召回 8/8 → 8/8    英文召回 8/8 → 8/8    对照路 10/23 → 10/23

B（"只有 BM25 命中一律拒绝"）: 构造成功，**被误拒**
  判据②用"腿里有没有值"⇒ 被调用方 min_score 左右 ⇒ 双向失灵：
    真库中文 query：min_score=0.01 → 8/8；**生产 0.3 → 4/8**（BM25 裕度 1.34~8.09 且答案正确）
    合成稀有标识符：BM25 只有 1 条候选 ⇒ 裕度 None ⇒ **两种 min_score 都被拒**
    反向：min_score=0.01 时 S10-03 噪声查询 bounded=0.1/ratio=1.5433 ⇒ **decision=pass**（G2）

C（有界相似度 ∝ 1/长度）: cov = H/N 精确成立（H=2/2/4/4/10 恒定）
  误拒起点 = H/0.3 个 token（实测 14/15/18/22/40 字符）；生产配置（向量腿在线）≤51 字符零误拒
  ⇒ **不值得修（只登记）**：改它 = 改全局有界相似度语义（RRF 闸/S10-03 锚/编排层同键），
     且 RET-1R 已实测"没有有界标量能分离正负样本" ⇒ 无法在本数据集验收

D（多进程并发重建同一台账）: **证实有害**（30 轮 N∈{2,4,8} 里 1 轮 + D1 的 1 轮）
  ① 读时 Errno 13 ⇒ registry 把"瞬时读失败"当"存储损坏" ⇒ **把台账改名成 .corrupted.json**
     并从空注册表继续 ⇒ 写出 **30/32 的半成品台账** ⇒ 最后落盘者胜
  ② 无跨进程锁 + 全量快照覆盖写 ⇒ **丢失更新 9/9**（N 条新增只剩 1 条）
  ③ 审计链 seq 冲突 ⇒ "本进程分发未生效"（静默丢审计）
  生产 data/descriptors.json 前后 sha256 逐字节相同 ✓
```
