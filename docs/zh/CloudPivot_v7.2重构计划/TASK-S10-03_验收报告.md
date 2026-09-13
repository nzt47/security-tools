# TASK-S10-03 验收报告 — 上下文预算告警口径 + 检索层融合分归一化

- 日期：2026-09-13
- 基线：`master` = `bbc5d821`（派发时点名）／实际起分支时 `master` = `87858dc3`（S10-01 已并入）
- 分支：`s1003/main`（worktree `.worktrees/s1003`）
- feat 提交：`0a58589b`
- 未触碰：`agent/orchestrator/judge*`、`plugins/chat.py`（D4 显示公式，任务书明确划出本任务范围）、
  `agent/workflow_learning/**`（S10-01 地盘）、`agent/digestion/**`

---

## 一、交付物

| 类型 | 文件 | 说明 |
|---|---|---|
| 改 | `agent/orchestrator/orchestrator.py` | 告警**口径**修正（成因分列 + 披露分母与来源）+ 告警改走结构化字段 `metadata.context_notice`，**不再拼进 `response`** |
| 改 | `agent/orchestrator/lifecycle_manager.py` | 上下文窗口上限的**单一事实源 + 来源披露**（`_memory_token_limit_source`） |
| 改 | `agent/skills_mgmt/loader.py` | 质量门只认**有界相似度**（新增 `_BOUNDED_QUALITY_KEYS` / `_bounded_quality_score`），BM25 无界原始分不再参与阈值比较 |
| 新增 | `tests/unit/test_s10_03_context_budget_notice.py` | 现象A 回归锚 7 例（修复前 6 例红） |
| 新增 | `tests/unit/test_s10_03_retrieval_quality_gate.py` | 现象B 回归锚 13 例（修复前 10 例红） |
| 新增 | `scripts/dev/s1003_context_notice_repro.py` | 现象A 进程内复现/对照探针（服务进程外，不碰 127.0.0.1:5678） |
| 新增 | `scripts/dev/s1003_retrieval_gate_probe.py` | 现象B 同输入前后对照探针（真技能库 + 真 BM25 + 真 TF-IDF） |

**未新增任何 env 读取点** ⇒ `agent/settings/registry.py` 无需新增行（见 §七.5 的既有红项说明）。

---

## 二、现象固化（先测后改，回归锚先行）

### 2.1 现象A：告警文本确实被追加进 `response`（修复前原始输出）

探针：`python scripts/dev/s1003_context_notice_repro.py`（复现真机形态
`compress_rounds=5` + 真机观测到的 27% / 37%）。修复前输出：

```
[pct=27%] _check_context_usage() →
  level            = 'critical'
  reason           = None
  pct              = 27.0
  used_tokens      = None
  limit_tokens     = None
  message          = '已压缩 5 次，摘要退化明显（当前使用 27%），建议创建新会话继续对话'
  [response]       = '5\n\n---\n💡 **当前会话上下文即将耗尽**（已使用 27%）。\n点击下方「创建新会话」按钮，我会携带之前的记忆继续对话。'
  [context_notice] = None
  [verdict] 正式回答含告警文本? True
  [verdict] 告警仍可见(结构化)? False
[pct=37%]
  [response]       = '5\n\n---\n💡 **当前会话上下文即将耗尽**（已使用 37%）。\n点击下方「创建新会话」按钮，我会携带之前的记忆继续对话。'
  [verdict] 正式回答含告警文本? True
结论：response 未被污染 且 告警仍可见 = False
```

与真机原文逐字一致（`docs/zh/CloudPivot_v7.2重构计划/TASK-S9-01_验收报告.md` §5.1
`[response] "5\n\n---\n💡 **当前会话上下文即将耗尽**（已使用 37%）。..."`）。

**回归锚（新增用例，修复前逐条红）**：

```
$ python -m pytest tests/unit/test_s10_03_context_budget_notice.py -q
FAILED ...::Test告警不得污染正式回答::test_压缩退化告警_不得拼进response尾部
FAILED ...::Test告警不得污染正式回答::test_告警仍可见_但走结构化字段
FAILED ...::Test告警不得污染正式回答::test_LLM回答里恰好含有告警字样时_不得改写回答
FAILED ...::Test告警口径自洽::test_压缩退化触发_不得声称即将耗尽
FAILED ...::Test告警口径自洽::test_真实高占用触发_才可声称即将耗尽
FAILED ...::Test告警口径自洽::test_告警披露真实上限与来源
6 failed, 1 passed
```

### 2.2 现象B：噪声级候选靠无界 BM25 分过闸（修复前原始输出）

真机复现（真技能库 `data/skills_repo` 23 个技能 + 真 BM25 + 真 TF-IDF，
`min_score=0.3` = 编排器语义层配置）：

```
Q=2 加 3 等于多少？只回答数字
  retrieval_method=rrf  fallback_used=False  candidates=2
  #1 self_reflection        score=1.0      tfidf=None     vec=None   bm25=3.5184   rrf_norm=1.0      max_bounded=None     max_raw_all=3.5184
  #2 pd-dispatching-parallel-agents-b8065ccd-skill score=0.9839   tfidf=None     vec=None   bm25=2.9697   rrf_norm=0.9839   max_bounded=None     max_raw_all=2.9697
```

`min_score=0.01`（S9-01 真机探针所用）时的形态，与真机报告 §3.2 逐字一致：

```
  #1 self_reflection  score=0.9954  tfidf=0.1  vec=None  bm25=3.5184  rrf_norm=0.9954  max_bounded=0.1  max_raw_all=3.5184
```

即：**有界相似度 0.1（噪声级）**，但 `max_raw_all=3.5184`（无界 BM25）≥ 阈值 0.3 ⇒ 过闸。

**回归锚（新增用例，修复前 10 例红）**：

```
$ python -m pytest tests/unit/test_s10_03_retrieval_quality_gate.py -q
FAILED ...::Test质量门只认有界相似度::test_真机形态_1_无界BM25撑起max_raw_必须被挡
FAILED ...::Test质量门只认有界相似度::test_真机形态_2_仅BM25命中_有界路全空_必须被挡
FAILED ...::Test质量门只认有界相似度::test_有界相似度低_即使BM25很高_仍被挡
FAILED ...::Test真库同输入对照::test_噪声查询_不得有候选
FAILED ...::Test有界相似度提取口径::（6 例：_bounded_quality_score 尚不存在）
10 failed, 3 passed
```

---

## 三、根因（代码行级）

### 3.1 现象A 根因链（三条，缺一不可）

| # | 位置（修复前） | 问题 |
|---|---|---|
| A1 | `agent/orchestrator/orchestrator.py:1628-1647` | 只要 `_last_context_warning["level"] == "critical"` 就 `response += "…💡 **当前会话上下文即将耗尽**（已使用 {pct:.0f}%）…"` —— **文案与成因脱钩**，且**拼进正式回答** |
| A2 | `agent/orchestrator/orchestrator.py:2891-2917`（修复前行号） | `critical` 有**两个互相独立**的成因：`compress_rounds >= 5`（摘要退化，`2891-2900`）与 `pct >= 95`（真实高占用，`2911-2917`）。真机 27%/37% 走的是**第一支**，与占用百分比无关 ⇒ 「即将耗尽（已使用 27%）」是错话 |
| A3 | `agent/orchestrator/lifecycle_manager.py:276`（修复前行号）`self._memory_token_limit = memory_cfg.get("token_limit", 131072)` | 分母的来源**完全不可见**：`config.yaml:memory` 段没有 `token_limit` 键 ⇒ 恒为**内置默认值 131072**；而 `plugins/chat.py:306` 的 `context.percentage` 用的是**另一个**硬编码 4096（D4）。两个分母混读必然得出互相矛盾的结论 |

**放大效应（实测可推）**：`memory/token_manager`→`memory_manager.py:286` 的压缩阈值
`self._token_limit = config.get("token_limit", 4096)`（× `compress_threshold=0.8` ⇒ 3276 tokens）
与编排器的 131072 **同键不同默认值**，于是「压缩 5 次」被轻易触发 ⇒
`critical` 常态化 ⇒ 每轮回答都被追加告警文本。

**回流污染**：`plugins/chat.py:267-272` 把接口返回的 `response` 原样写入会话历史
⇒ 这段非模型答案的文本进入后续上下文（自我污染）。

### 3.2 现象B 根因（量纲混用）

| # | 位置（修复前） | 问题 |
|---|---|---|
| B1 | `agent/skills_mgmt/loader.py:1516-1522` | `max_raw_score = max(各路 *_score)`：把**有界**余弦相似度（`tfidf_score` / `vector_score`）与**无界** `bm25_score` 放进同一个 `max()` |
| B2 | `agent/skills_mgmt/loader.py:820` `_RRF_QUALITY_MIN = 0.3` | 该阈值的语义是「**有界相似度**阈值」（数据支撑注释里正样本 0.5+/负样本 0.14 全是余弦量级），却与无界分比大小 |
| B3 | `agent/skills_mgmt/loader.py:1231` `normalized_score = rrf_score / max_possible` | RRF 按 rank-1 归一化 ⇒ top1 恒 ≈1.0，**排名分**同样不可当相似度（S9-01 已在编排层用 `_bounded_relevance` 挡住，检索层本次收敛同口径） |

实测 BM25 原始分跨度（同一技能库，本报告 §五）：**1.29 ~ 21.04** —— 无上界，恒赢 0.3。

---

## 四、修复口径

### 4.1 现象A

1. **成因分列**：`_check_context_usage()` 返回结构化告警，新增 `reason`
   （`summary_degraded` / `summary_warning` / `usage_high` / `usage_info`）。
   只有 `usage_high`（真实占用高）才允许出现「即将耗尽」措辞；
   `summary_degraded` 说的是「摘要已压缩 N 次，摘要退化明显」。
2. **基于真实上限 + 披露来源**：分母仍是**真正用于组装上下文的那个上限**
   （`_memory_token_limit`，即 `get_context(token_limit=...)` 的实参），
   并由 `LifecycleManager` 显式记录来源：
   `config.yaml:memory.token_limit`（配了就用）/ `builtin_default(131072)`（未配）。
   文案尾巴固定给出 `（窗口占用 X%：used/limit tokens，上限来源 …）`。
   **明确不用** `plugins/chat.py` 的显示公式分母 4096（D4，本任务范围外）。
3. **不污染正式回答**：告警不再拼进 `response`；改为
   `metadata.context_notice = {kind: "system_notice", level, reason, pct,
   used_tokens, limit_tokens, limit_source, compress_rounds, message, summary}`，
   并同时写 `WARNING` 日志。`last_context_warning` 属性契约不变（`summary` 仍被填充）。
   这样处理而非「在回答里加标注」的理由：`response` 会被持久化进会话历史并回灌上下文，
   在回答里保留任何自带文本都会形成自我污染回路。

### 4.2 现象B

1. 新增模块常量 `_BOUNDED_QUALITY_KEYS = ("tfidf_score", "vector_score", "rerank_score")`
   （`loader.py:89`），与编排层 `_BOUNDED_RELEVANCE_KEYS` **同键名同语义**。
2. 新增 `SkillLoader._bounded_quality_score()`（`loader.py:833`）：
   只取有界键（`bool` 排除，避免 `True` 被当 1.0 放行全量），取不到返回 `None`。
3. 质量门（`loader.py:1547+`）改为：
   - **有界路已声明** ⇒ 阈值只与 `max(有界相似度)` 比较；
     `bm25_score` 只记录不参与（日志字段由 `max_raw_score` 改为
     `bounded_similarity` / `bm25_raw_unbounded` / `effective_score` 三列分列）；
   - **有界路已声明但全部未命中**（只有 BM25 命中）⇒ 判为不可信，拒绝
     （与编排层 `_bounded_relevance` 已上线的口径一致）；
   - **未声明任何有界路**（自定义 loader / mock breakdown）⇒ 沿用旧口径，向后兼容不新增拒召回。
4. **保守优先**：仍然**只看 top1**（「挡低质」而非「重排高分」），不新增任何分数重排、
   不改 `_RRF_K` / 权重 / 各路 `min_score`。

---

## 五、改前/改后同输入对比（同一批 query，可复算）

命令（`PYTHONHASHSEED=0` 固定候选并列时的顺序，消除 `set` 迭代的哈希随机性）：

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'; $env:PYTHONHASHSEED='0'
$env:S1003_MIN_SCORE='0.3'            # 编排器语义层配置
$env:S1003_OUT='.p6_snapshots/s1003_after_seed0.json'
python scripts/dev/s1003_retrieval_gate_probe.py
```

改前（`git stash` 掉三处源码改动后同命令）与改后逐条对照：

| query | 改前候选（skill_id / tfidf_score / bm25_score） | 改后候选 |
|---|---|---|
| `2 加 3 等于多少？只回答数字`（真机负样本） | **2 个**：self_reflection(None/3.5184)、pd-dispatching-parallel-agents(None/2.9697) | **0 个（质量门拦截）** |
| `帮我订一张机票` | 0 | 0 |
| `今天天气真好` | 0 | 0 |
| `1+1 等于几` | 5 个：pd-systematic-debugging(0.5/1.4764)、pd-subagent-driven-development(0.5/1.4586)、pd-requesting-code-review(0.5/1.4079)、pd-writing-plans(0.5/1.4244)、pd-brainstorming(0.5/1.3162) | **完全相同**（5 个，逐条同 id 同分） |
| `写一首关于春天的诗` | 0 | 0 |
| `自我反思一下你的回答`（真机正样本） | 1 个：self_reflection(**0.4444**/15.1856) | **完全相同** |
| `帮我解析PDF文件` | 0 | 0 |
| `总结一下之前的对话记忆` | 5 个：memory_summary(0.5/12.5124)、context_aware(None/3.2159)、emotion_expression(None/1.6479)、pd-writing-skills(None/1.5508)、proactive_suggestion(None/1.2865) | **完全相同**（逐条同 id 同分） |
| `请帮我梳理历史记忆并压缩` | 2 个：memory_summary(0.5455/21.0366)、context_aware(None/2.0565) | **完全相同** |
| `费马小定理证明` | 0 | 0 |
| `PDF解析` | 0 | 0 |

**判读（哪些是真变好 / 真变坏）**：

- **真变好 1 条**：唯一变化就是真机点名的那条噪声 query（`2 加 3 等于多少？只回答数字`）
  由「2 个候选」变「0 个候选」⇒ 挡住的就是噪声，符合任务书「优先挡低质」。
- **其余 10 条 query 逐条逐分完全不变** ⇒ **没有重排任何高分**，无新增误伤。
- 真机正样本（`自我反思一下你的回答`，有界相似度 0.4444）与两条记忆类正样本
  （0.5 / 0.5455）全部保留 ⇒ 正向契约不变。
- **本次未观察到的真变坏**：见 §八 遗留 R1（BM25-only 召回面收窄，附实测边界）。

> **注（如实登记一个测量陷阱）**：`1+1 等于几` 这类「多候选同分并列（tfidf 全为 0.5）」的
> query，其候选**集合与顺序**会随进程哈希种子变化（`_tfidf_scan` 的倒排候选集是 `set`）。
> 在**同一份改后代码**上换种子实测：
>
> ```
> PYTHONHASHSEED=0 → pd-systematic-debugging, pd-subagent-driven-development, pd-requesting-code-review, pd-writing-plans, pd-brainstorming
> PYTHONHASHSEED=1 → pd-subagent-driven-development, pd-systematic-debugging, pd-test-driven-development, pd-writing-plans, pd-executing-plans
> PYTHONHASHSEED=7 → pd-subagent-driven-development, pd-systematic-debugging, pd-test-driven-development, pd-writing-plans, pd-dispatching-parallel-agents
> ```
>
> 故**只有固定种子下的同种子对照**才可比（§五 用 `PYTHONHASHSEED=0` 前后对照）；
> 首次未固定种子跑出的「顺序/集合不同」表象已按此排除，**不计入改动效果**。
> 同时，被修的那条噪声 query 在 seed 0/1/7 下**候选数恒为 0** ⇒ 修复效果对种子不敏感。

---

## 六、验收逐条

| # | 验收项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 告警基于**真实上限** | ✅ | 分母 = `_memory_token_limit`（即 `get_context(token_limit=...)` 的实参），并披露来源（`builtin_default(131072)` / `config.yaml:memory.token_limit`）。`test_告警披露真实上限与来源` 断言 `limit_tokens == 131072` 且 `!= 4096`（显式排除 D4 的显示公式分母） |
| 2 | 措辞准确 | ✅ | `reason=summary_degraded` 时文案为「上下文摘要已压缩 5 次，摘要退化明显…」且**不含「即将耗尽」**；只有 `reason=usage_high`（pct≥95）才出现「即将耗尽」。见 §二.1 前后输出与 `test_压缩退化触发_不得声称即将耗尽` / `test_真实高占用触发_才可声称即将耗尽` |
| 3 | 不污染正式回答 | ✅ | `process()` 返回的 `data` 原样 == LLM 输出 `"5"`；告警走 `metadata.context_notice`（`kind=system_notice`）。见 §二.1 改后输出与 `test_压缩退化告警_不得拼进response尾部`、`test_告警仍可见_但走结构化字段`（**正向断言**：移出 response ≠ 丢掉告警，防假绿） |
| 4 | 质量门挡住实测低分候选 | ✅ | `2 加 3 等于多少？只回答数字` 由 2 候选 → 0 候选（§五）；`test_真库同输入对照::test_噪声查询_不得有候选` 用真技能库 + 真 BM25 钉死 |
| 5 | 检索类既有断言**未被放宽** | ✅ | **未修改任何既有测试文件**（`git diff --stat` 仅 3 个源文件 + 4 个新增文件）。已知 `xfail` 仍为 `xfail`：`test_skill_retrieval_precision_above_threshold XFAIL … Precision@3=0.4444 < 0.6`，且 `test_baseline_precision_recorded` 仍 PASSED ⇒ TF-IDF 路分数未被改动 |
| 6 | 门禁四条齐全 | ✅（+1 项既有红，见 §七.5） | 见 §七 |

**「被检查的对象没从报告里消失」自查（防假绿灯）**：
- 现象A：不是「删掉告警」而是「换通道」——`test_告警仍可见_但走结构化字段` 断言
  `metadata.context_notice` 存在且 `level=="critical"`；探针改后输出已列出完整 notice。
- 现象B：不是「一刀切拒全部」——`test_真机正样本_有界相似度达标_契约不变`、
  `test_无有界路声明_保持既有行为_向后兼容`、§五 的 10 条不变 query 共同证明
  拒召回只发生在低质 top1 上。

---

## 七、质量证据（门禁四条 + 邻接回归）

### 7.1 相关套件（邻接回归）

任务书给的命令在 pytest 8 下是非法表达式（`-k` 需显式 `and`/`or`），按**意图**等价的 `or` 形式执行：

```
$ python -m pytest tests/unit -q -k "context or prompt or loader or retrieval or skills_mgmt"
843 passed, 13 skipped, 17470 deselected, 13 xfailed, 10 warnings in 84.03s
```

（原命令 `-k "context prompt loader retrieval skills_mgmt"` 报
`ERROR: Wrong expression passed to '-k' … expected end of input; got identifier`。）

补充定向套件：

```
$ python -m pytest tests/unit/test_bm25_skill_searcher.py tests/unit/test_vector_skill_searcher.py \
    tests/unit/test_skills_mgmt.py tests/unit/test_orchestrator_turn_state_isolation.py \
    tests/unit/test_orchestrator_refactor.py tests/unit/test_context_assembler.py \
    tests/unit/test_prompt_builder.py tests/unit/test_prompt_cache_order.py -q
238 passed, 6 skipped, 1 xfailed in 15.64s

$ python -m pytest tests/integration/test_orchestrator三层路由_e2e.py \
    tests/integration/test_digital_life_integration.py -q
56 passed, 2 skipped, 3 warnings in 21.30s
```

### 7.2 `scan_kwarg_conflicts --min-risk HIGH`（各 0 处）

```
$ python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH
总发现数: 0        HIGH: 0 处  MEDIUM: 0 处  LOW: 0 处     exit=0

$ python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH
总发现数: 0        HIGH: 0 处  MEDIUM: 0 处  LOW: 0 处     exit=0
```

### 7.3 `mypy` 改动模块（零新增错误）

```
$ python -m mypy --no-incremental agent/skills_mgmt/loader.py \
      agent/orchestrator/orchestrator.py agent/orchestrator/lifecycle_manager.py
改后：Found 1236 errors in 169 files (checked 3 source files)
改前（git stash 同一命令）：Found 1236 errors in 169 files (checked 3 source files)
```

两版错误**逐条消息多重集相同**，差异只有 `no-redef` 类消息里携带的行号随插入代码平移；
无任何错误指向本次新增标识符（`_bounded_quality_score` / `_context_limit_source` /
`_memory_token_limit_source` / `context_notice` / `_BOUNDED_QUALITY_KEYS`，grep 为空）。
⇒ 该仓库此三模块本来就不是 mypy-clean（1236 条存量），本次**零新增**。

### 7.4 `lint-imports --config .importlinter`

```
$ lint-imports --config .importlinter
Analyzed 574 files, 1698 dependencies.
error_handler 不得导入 monitoring.decorators（核心循环依赖防护） KEPT
error_handler 不得在模块级导入 monitoring 子模块 KEPT
Contracts: 2 kept, 0 broken.        exit=0
```

### 7.5 产物漂移检查

```
$ git status --short
 M agent/orchestrator/lifecycle_manager.py
 M agent/orchestrator/orchestrator.py
 M agent/skills_mgmt/loader.py
?? .p6_snapshots/…（本地证据 JSON/日志，不入库）
?? scripts/dev/s1003_context_notice_repro.py
?? scripts/dev/s1003_retrieval_gate_probe.py
?? tests/unit/test_s10_03_context_budget_notice.py
?? tests/unit/test_s10_03_retrieval_quality_gate.py
```

除上述源码改动外**无额外漂移**（未生成/改写任何运行时台账、`data/**` 无变化）。
`.p6_snapshots/s1003_*.{json,log}`、`mypy_*.txt` 为本报告的可复算原始证据，保留在本地不入库。

**既有红项（不是本任务引入，如实登记）**：`agent/settings/registry.py` 的零缺口硬守卫
**在基线上就是红的**：

```
$ python -m pytest tests/unit/test_settings_registry.py -q
FAILED TestMechanicalZeroGap::test_zero_gap_between_scan_and_registry
E  AssertionError: 代码读到但注册表未覆盖：['CP_ENV_FILE']
FAILED TestMechanicalZeroGap::test_extracted_scale_is_disclosed
E  AssertionError: assert 373 == 374
```

在**把本任务三处源码改动 stash 掉**后跑同一条命令，**同样红**（已实测），
⇒ 与 S10-03 无关。来源：`bbc5d821 fix(P0): 测试不再覆盖真实 .env` 引入
`agent/env_config_manager.py:77 ENV_FILE_OVERRIDE_VAR = "CP_ENV_FILE"`，
以及 `80133139 feat(s7-03)` 的 `scripts/calibrate_cost_coefficients.py:113`
`os.getenv("CP_ENV_FILE")`，两处都未登记。详见 §八 R2。

---

## 八、遗留（带归属）

| # | 遗留 | 归属 | 现状与依据 |
|---|---|---|---|
| **R1** | **BM25-only 候选的召回面收窄**：改为「只认有界相似度」后，若某 query 的有界路（TF-IDF/向量）全部未命中而只有 BM25 命中，质量门现判为不可信并拒绝（此前会放行）。 | 检索层专项（S10-03 邻接） | 实测边界：同一批 11 条 query 中，**没有一条真命中的 top1 是 BM25-only**（真命中 top1 的有界相似度分别 0.4444 / 0.5 / 0.5455）；BM25-only 只出现在**噪声 query 的 top1** 与正样本 query 的 **#2 及以后**（名单见 §五）。且编排层（`orchestrator._bounded_relevance`）早已按同一口径拒绝 BM25-only，本次只是让检索层与之一致。若后续需要恢复纯 BM25 专有名词召回，应**为 BM25 单独标定一个归一化映射与阈值**（不得直接拿 0.3 比无界分）——本次不发明这个常量。 |
| **R2** | `CP_ENV_FILE` 未登记 ⇒ `test_settings_registry.py` 零缺口硬守卫红（基线即红，见 §七.5） | `agent/env_config_manager.py`（P0 提交 `bbc5d821`）/ `scripts/calibrate_cost_coefficients.py`（S7-03 `80133139`） | 本任务**未新增任何 env 读取点**，故未登记、也未替他人补登（避免与在跑的会话撞同一行）。补登方式已有先例（同文件 `_REGISTRY_ROWS` 的「【跨任务补登】」段落），按已登记口径应为 **C 级只读脱敏（路径类）**：`_c("CP_ENV_FILE", CAT_EXTERNAL 或 CAT_OBSERVABILITY, "", "…", owner="agent/env_config_manager.py", validator=Validator("path"))`。**建议由该 env 的归属任务补登**，不要只改级别/不改依据。 |
| **R3** | `plugins/chat.py:298-330` 的 `context.percentage` 仍是「**全局会话累计 ÷ 硬编码 4096**」的显示公式，且 `session_total_tokens` 取 `_get_current_session_id()`（非请求指定的会话）。 | D4 → 读数可信性专项（任务书明确划在本任务范围外） | 本任务**未触碰** `plugins/chat.py`。但请注意：修完 A 之后，同一响应里会同时存在**两个不同分母**的读数——`context.percentage`（÷4096，显示公式）与 `metadata.context_notice.limit_tokens`（÷真实窗口上限）。建议 D4 专项把 `context` 块也切到 `_memory_token_limit` 并回显实际 `session_id`。 |
| **R4** | 三处 token 上限仍三套默认值：`MemoryManager` 压缩阈值 4096（`memory/memory_manager.py:286`）、编排窗口 131072（`lifecycle_manager.py:288`）、`plugins/chat.py` 显示 4096。 | 记忆层/上下文预算专项 | 本次只让**告警**口径统一到「真实窗口上限 + 来源披露」；**未改压缩策略**（属策略决定：把 4096→131072 会显著减少压缩次数，需 Owner 裁定）。这也是真机「压缩 5 次」轻易触发的直接来源。 |
| **R5** | 告警外发通道变了：`response` 里不再有那段文字。若前端此前**依赖正文尾部的这段话**渲染「创建新会话」按钮，需改读 `metadata.context_notice`。 | 前端 / `plugins/chat.py` | 已在仓库内检索：`tests/**` 与 `scripts/**` 均无对该段文案的断言或解析（`grep "即将耗尽"` 仅命中 orchestrator 自身与文档）；`plugins/chat.py` 目前**不转发** `metadata`，故 Web 端短期会「看不到」该提示（但也不再被污染）。这符合任务书「不得混进正式回答」的硬要求，前端接线归后续。 |

---

## 九、SHA 记录

| 项 | SHA |
|---|---|
| 基线 `master` | `bbc5d821`（派发时点名）→ 实际分支基点 `87858dc3`（S10-01 已并入） |
| 交付提交（`s1003/main`） | `0a58589b`（feat(S10-03)）+ 本报告（docs(S10-03)） |
| 合并提交（`master`） | 见交接回复（`git merge s1003/main --no-edit`） |
| 远端 | `origin/master`、`gitee/master` 同 SHA（见交接回复） |
