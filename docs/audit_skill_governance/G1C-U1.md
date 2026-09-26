# G1C-U1 · 生产 Layer-1 索引文本并入中文 description_zh（中文 query 可召回）

> 任务卡：**G1C-U1**（G1-C 登记的未验证项 **U1** 的专卡）
> 基线：`HEAD = 5c9ace10a4ca4bb96860db3a48debf9ddcf496bf`（开工/收尾各实测一次，未变）
> 环境：Python 3.12.0（系统解释器，**未用 venv**）；Windows；未启动任何常驻服务；
> 全程**无 `git add` / `git commit`**、**无整文件 `git checkout`**；零出网。
> 探针位置：`C:\Users\Administrator\AppData\Local\Temp\g1cu1\`（**全部在仓库外**）

---

## 0. 结论速览

| 项 | 判定 | 一行证据 |
|---|---|---|
| **改前基线（硬要求）** | ✅ 已复现 | 生产入口 `SkillLoader()`，中文 **2/8**、同语义英文 **8/8** |
| **改后中文召回** | ✅ 2/8 → **8/8** | 同一探针 `tag=FINAL_default` |
| **英文回归** | ✅ **8/8 → 8/8**，且**逐条技能分数 0 下降** | 1400 个打分点里「降 = 0」 |
| **eval_skill_retrieval.py 改前 vs 改后** | ⚠️ **P@3 / R@3 不变（0.4444 / 1.0），MRR 0.9778 → 0.9556（−0.0222）** | 不粉饰：确实扰动了排序 |
| **全部 28 条打分被扰动** | ⚠️ **属实**：1400 点中 91 点升（6.5%）、**0 点降**、88 点 0→>0；17/28 条技能受影响 | 11 条零扰动 |
| **误召代价** | ⚠️ **真实存在**：远邻负样本 0→1 次、近邻 1→6 次（score>0 全量口径） | 见 §5.4 |
| **逃生开关** | ✅ `CP_SKILL_META_INCLUDE_ZH`，默认新行为；置 0 ⇒ **逐用例 actual 与改前完全一致** | 见 §6 |
| **env 登记** | ✅ 已登记 `CAT_SKILLS` / **A 级**；`test_settings_registry.py` **56 passed**（= 基线） | registry.py:2511 |
| **非空转自证** | ✅ 删掉改动 ⇒ 新测试 **28 failed / 18 passed**（全删）、**14 failed / 32 passed**（只删行为） | 见 §7 |
| **既有回归** | ✅ 无新增红灯（**2 failed 是 G1-C 遗留的既有红灯**，同改前） | 367 → 469 passed |
| **残留物** | ✅ 无 | 见 §10.3 |

---

## 1. 第 1 步：改前基线（**原始输出，先复现再动手**）

### 1.1 生产入口（不是我自己拼的近似）

```
### G1C-U1 召回探针  tag=before
### 生产入口: agent.skills_mgmt.loader.SkillLoader(file_store=SkillFileStore())
### index_cache 挂载 = False
### 环境变量 CP_SKILL_META_INCLUDE_ZH = None
### load_metadata_index() 条数 = 28
### SkillLoader 模块文件 = C:\Users\Administrator\agent\agent\skills_mgmt\loader.py
```

> `SkillLoader(fs)` 的 `fs` 是生产默认 `SkillFileStore()`（`loader.py:355`），
> 与生产调用点 `capregistry/skillsearch.py:53`、`orchestrator.py:3916`、`context_injector.py:282` 同形；
> 探针里所有取数都走 `ld.match()` / `ld.fs.load_metadata_index()`，没有另拼元数据字典。

### 1.2 中文 query（8 条，贴近这 5 条技能的真实用途）—— 原始输出

```
── A. 中文 query（8 条）──
  q='写测试时要避免哪些反模式'                    exp=testing-anti-patterns    -> []  MISS
  q='给测试加 Mock 有什么坑'                      exp=testing-anti-patterns    -> ['testing-anti-patterns']  HIT
  q='生成后端接口时怎么加结构化日志和健康检查'      exp=code-observability       -> ['memory_summary', 'pd-requesting-code-review-ca5ae995-skill', 'pd-brainstorming-697b717a-skill', 'pd-test-driven-development-8562c8ad-skill']  MISS
  q='前后端状态不同步、有竞态该怎么防'             exp=frontend-state-sync      -> []  MISS
  q='乐观更新回滚和请求取消怎么写'                exp=frontend-state-sync      -> []  MISS
  q='做一个不用查文档就能看懂的自解释界面'          exp=self-explanatory-ui      -> ['pd-writing-skills-5da20e67-skill']  MISS
  q='界面设计时怎么把帮助信息集成进去'             exp=self-explanatory-ui      -> []  MISS
  q='代码交付前怎么做自测和审计报告'               exp=engineering-test-delivery -> ['engineering-test-delivery']  HIT
  中文命中 = 2/8
```

**2/8 逐条可归因**（不是"4/5 返回 []"那个粒度）：

| 改前命中的 2 条 | 为什么能中 |
|---|---|
| `给测试加 Mock 有什么坑` | query 里带了**英文词 Mock**，命中英文 description 里的 `mocks` |
| `代码交付前怎么做自测和审计报告` | 命中 `tags` 里的**中文** `代码与工程` 的 bigram `代码` |

⇒ **改前的中文命中全部来自"英文借词"或"tags 里的中文"，没有一条来自描述文本**。

### 1.3 英文对照（同语义 8 条）—— 原始输出

```
── B. 英文对照 query（同语义 8 条）──
  q='what anti-patterns to avoid when writing tests'       exp=testing-anti-patterns    -> ['testing-anti-patterns', ...]  HIT
  q='pitfalls of adding mocks in tests'                    exp=testing-anti-patterns    -> ['testing-anti-patterns', ...]  HIT
  q='structured logs and health check for backend api'     exp=code-observability       -> ['code-observability', ...]  HIT
  q='frontend backend state out of sync race condition'    exp=frontend-state-sync      -> ['frontend-state-sync', ...]  HIT
  q='optimistic update rollback and request cancellation'  exp=frontend-state-sync      -> ['frontend-state-sync', ...]  HIT
  q='self explanatory interface without documentation'     exp=self-explanatory-ui      -> ['self-explanatory-ui', ...]  HIT
  q='integrate help information into interface design'     exp=self-explanatory-ui      -> ['self-explanatory-ui', ...]  HIT
  q='self testing and audit report before code delivery'   exp=engineering-test-delivery -> ['engineering-test-delivery', ...]  HIT
  英文命中 = 8/8
```

### 1.4 改前负样本（证明"改前 0 误召"是真的 0，不是没测）

```
── C1. 负样本（远邻 8 条：与该 5 条技能毫无关系）──
  8 条全部 -> 召回 []        误召5条=[]   （含 '统计当前工作目录下有多少个文件'）
  C1 误召 5 条技能总次数 = 0 | 非空召回条数 = 0/8

── C2. 负样本（近邻 8 条：同属"写代码"主题、词汇有重叠，诉求不同）──
  q='帮我重构这段代码，把函数拆小一点'  -> ['engineering-test-delivery']   误召5条=['engineering-test-delivery']
  其余 7 条 -> 误召5条=[]
  C2 误召 5 条技能总次数 = 1 | 非空召回条数 = 2/8
```

### 1.5 一条必须先说的**测量学口径**（否则改前改后不可比）

`_tfidf_scan` 用 `set(candidate_hits.keys())` 定候选、之后 `sort(key=score, reverse=True)`
是稳定排序 ⇒ **同分并列的先后由 Python 字符串哈希随机化决定**。实测同一份代码、同一黄金集：

```
无 PYTHONHASHSEED ：MRR = 0.9519 / 0.9556 / 0.9778（三次不同）
PYTHONHASHSEED=0 ：MRR = 0.9778（两次相同，可复现）
```

⇒ 本卡所有对照测量都固定 `PYTHONHASHSEED=0`。它是**既有缺陷**（见 §9.2 R-6），不是本卡引入。

---

## 2. 第 2 步：定位结论

### ① 它到底拼了哪些字段

`agent/skills_mgmt/loader.py:69`（改前），由探针 `inspect.getsourcelines` 原样取出：

```python
def _meta_to_meta_text(meta: Dict[str, Any]) -> str:
    """将元数据字典转为用于匹配的文本（第一层）"""
    parts = [
        meta.get("name", ""),
        meta.get("description", ""),
        " ".join(meta.get("tags", []) or []),
        meta.get("category", ""),
    ]
    return " ".join(p for p in parts if p)
```

**四个字段：name / description / tags / category。**

上游调用链（探针逐行列出，**行号已复核**）：

```
loader.py:330  meta_text = _meta_to_meta_text(meta)   ← _get_inverted_index（建倒排索引 / 候选池）
loader.py:400  meta_text = _meta_to_meta_text(meta)   ← _tfidf_scan（精确打分）
loader.py:371  inverted = self._get_inverted_index(index)   ← match() 的 TF-IDF 单路
```

### ② 为什么 description_zh 没进去

字段普查（生产索引，逐条实测）：

```
  testing-anti-patterns      meta键=[author, category, content_type, description, description_zh, enabled, id, name, source, status, tags]
      meta_text 长度=454  其中 CJK 字符=8   -> CJK=1.8%
      description_zh 长度=74  CJK=58  已进 meta_text? False
  code-observability         meta_text CJK=0.0%   description_zh CJK=63  已进 meta_text? False
  engineering-test-delivery  meta_text CJK=1.6%   description_zh CJK=61  已进 meta_text? False
  frontend-state-sync        meta_text CJK=0.0%   description_zh CJK=71  已进 meta_text? False
  self-explanatory-ui        meta_text CJK=0.0%   description_zh CJK=70  已进 meta_text? False

  全索引 28 条中带 description_zh 的 = 20 条
```

**结论**：`description_zh` **在 meta 字典里**（`file_store.py:90` 的白名单，
`load_metadata_index()` 带得出来），**但检索侧从来没读过它**。
根因是 G1-B 建立的分工口径（`file_store.py:82-89` 的注释原文）：

```
description     = 英文，供检索 + 模型可见
description_zh  = 中文展示文案（UI 用）
```

⇒ 中文那一列**从建立那天起就只接了 UI 一个消费者**，检索侧是空的。G1-C 把 5 条迁进文件轨后，
"description 是英文"这条约束（H-1：改中文会让 `Use when …` 触发句式覆盖从 75.0% 掉到 57.1%）
立刻转化成了"**这 5 条对中文 query 不可召回**"。

### ③ 索引侧与查询侧的分词**是否同口径** —— 是同一个函数（**不是** G1-A/F11-C 那个坑）

```
  index 侧 tokenize 函数 = '_tokenize' (loader.py:95)，被 _get_inverted_index 调用
  query 侧 tokenize 函数 = 同一个函数对象 -> True
  _match_score 内部 tokenize = 同一函数（'_tokenize(meta_text)'）
  示例 query 分词 = ['写测','测试','试时','时要','要避','避免','免哪','哪些','些反','反模','模式']
  该技能 meta_text 分词样例 = ['1','add','adding','anti','apis','asserting','behavior','changing','code',...]
  query bigram 中命中 meta_text 的 = []
```

⇒ **口径一致，但一个都中不了**。所以这不是"两侧口径不一致 = 死特征"，而是
**"被索引的字段"一侧根本没有中文** —— query 侧再怎么分词，也切不出索引里不存在的 token。
这一条直接决定修法方向：**只能修索引侧并字段**。

### 修法选择（三选一，给理由）

| 方案 | 判断 | 理由 |
|---|---|---|
| A. 改中文 query 的分词 / 加权 | ❌ 无效 | 见 ③：索引里没有可中的 token，改查询侧是空转 |
| B. 把 `description` 改成中文 | ❌ 已被 H-1 否决 | 触发句式覆盖 75.0% → 57.1%（G1-C §2.6 实测） |
| **C. 索引侧并入 `description_zh`** | ✅ **采用** | 中文的唯一源就是它（G1-B 口径）；且**管理页搜索 `searcher._match_score` 早就是同一做法**（"英文描述 + 中文 description_zh 无条件并入"，`searcher.py:58-102`）⇒ 本条是把**生产 Layer-1** 拉到仓库既有口径，不是新发明 |

---

## 3. 第 3 步之一：改后中文召回（同那 8 条，原始输出）

```
### G1C-U1 召回探针  tag=FINAL_default
### 环境变量 CP_SKILL_META_INCLUDE_ZH = None     ← 默认 = 新行为
── A. 中文 query（8 条）──
  q='写测试时要避免哪些反模式'                    -> ['pd-verification-before-completion-...', 'pd-finishing-a-development-branch-...', 'pd-systematic-debugging-...', 'testing-anti-patterns']  HIT
  q='给测试加 Mock 有什么坑'                      -> ['testing-anti-patterns', ...]  HIT
  q='生成后端接口时怎么加结构化日志和健康检查'      -> ['code-observability', 'pd-finishing-...', 'frontend-state-sync', 'memory_summary']  HIT
  q='前后端状态不同步、有竞态该怎么防'             -> ['frontend-state-sync', 'code-observability', ...]  HIT
  q='乐观更新回滚和请求取消怎么写'                -> ['frontend-state-sync', 'pd-finishing-...']  HIT
  q='做一个不用查文档就能看懂的自解释界面'          -> ['self-explanatory-ui', ...]  HIT
  q='界面设计时怎么把帮助信息集成进去'             -> ['self-explanatory-ui', ...]  HIT
  q='代码交付前怎么做自测和审计报告'               -> ['engineering-test-delivery', ...]  HIT
  中文命中 = 8/8
```

**两条生产装配形态都跑了**（裸 loader = `capregistry/skillsearch.py` 那条；
服务形态 = `SkillsMgmtService` 那条）：

```
### index_cache 挂载 = True
### load_metadata_index() 条数 = 30
  中文命中 = 8/8
  英文命中 = 8/8
```

---

## 4. 第 3 步之二：英文是否被改坏

### 4.1 命中数（不降）

```
改前：中文 2/8  英文 8/8
改后：中文 8/8  英文 8/8      ← 英文命中数未下降
```

### 4.2 更强的不变量：**逐条技能的分数一个都没降**（1400 个打分点全量对拍）

探针 `score_shift_probe.py`：**28 条技能 × 50 条 query（黄金集 45 + 中文 5）= 1400 个打分点**，
逐点比对 `CP_SKILL_META_INCLUDE_ZH=0`（旧行为）与默认（新行为）：

```
=== 1. 开关对拍：flag=0 vs 默认(新行为) ===
  flag=0(旧行为) -> 默认(新行为) : 升=91 降=0 不变=1309 | 最大升幅=1.0000 @ ('self-explanatory-ui', '上下文')
      从 0 分变为 >0（= 新进候选池）的打分点 = 88

=== 2. 反空转：默认 两次调用（应为 0 升 0 降）===
  默认#1 -> 默认#2 : 升=0 降=0 不变=1400
```

**「降 = 0」不是巧合**：`_match_score = hits / len(query_tokens)`，并入中文只**增**
`meta_tokens`、分母不变 ⇒ 数学上单调不减。1400 点实测 0 例下降，与该不变量一致
（已固化为新测试 `test_english_scores_never_drop`）。

### 4.3 扰动的分布（**"会给全部 28 条改打分"的实测答案**）

```
  pd-using-superpowers-3aea3fc9-skill              被打分的 query 数=10/50   最大升幅=0.5000
  pd-verification-before-completion-af010352-skill 被打分的 query 数=10/50   最大升幅=0.1250
  self-explanatory-ui                              被打分的 query 数= 9/50   最大升幅=1.0000
  pd-frontend-design-77ea5c4e-skill                被打分的 query 数= 7/50   最大升幅=0.5000
  pd-brainstorming-697b717a-skill                  被打分的 query 数= 7/50   最大升幅=0.2500
  pd-receiving-code-review-8934157e-skill          被打分的 query 数= 7/50   最大升幅=0.1250
  pd-writing-plans-f846e3a2-skill                  被打分的 query 数= 6/50   最大升幅=0.1111
  pd-using-git-worktrees-d516703a-skill            被打分的 query 数= 5/50   最大升幅=0.1111
  pd-test-driven-development-8562c8ad-skill        被打分的 query 数= 5/50   最大升幅=0.1111
  pd-systematic-debugging-556faa20-skill           被打分的 query 数= 5/50   最大升幅=0.1111
  code-observability                               被打分的 query 数= 4/50   最大升幅=0.5263
  engineering-test-delivery                        被打分的 query 数= 4/50   最大升幅=0.1818
  frontend-state-sync                              被打分的 query 数= 3/50   最大升幅=0.3077
  pd-finishing-a-development-branch-e085de5a-skill 被打分的 query 数= 3/50   最大升幅=0.1053
  pd-dispatching-parallel-agents-b8065ccd-skill    被打分的 query 数= 3/50   最大升幅=0.0833
  testing-anti-patterns                            被打分的 query 数= 2/50   最大升幅=0.0909
  pd-requesting-code-review-ca5ae995-skill         被打分的 query 数= 1/50   最大升幅=0.0714
  零扰动的技能 = 11 条
  至少 1 个 query 被打分的技能 = 17 / 28
```

**读法**：G1-C 的警告"会给全部 28 条改打分"**方向上属实、量级上偏保守**。实测是
**17/28** 条技能在至少一个 query 上被打分（**15 条 pd-* 里 11 条被波及** —— 这是"15 条 pd-* 也带
description_zh"的必然结果）；11 条零扰动。**最大单点升幅 = 1.0000**
（`self-explanatory-ui` 对 query `'上下文'` 从 0 变满分）—— 这正是下面 MRR 下降的同一处。

---

## 5. 第 3 步之三：scripts/eval_skill_retrieval.py 改前 vs 改后

### 5.1 脚本可用性结论（先读它）

`scripts/eval_skill_retrieval.py`（452 行）**可用、口径合适**，因此**本卡没有改它**
（任务卡允许"如需小改"，实测不需要）。它给 `Precision@K / Recall@K / MRR`，
黄金集 `tests/eval/skill_retrieval_golden_set.json` 45 条，直连 `SkillLoader.match()`（生产入口）。
**本节数字来自该脚本原样输出，不是自建指标。**

### 5.2 四个配置的对照（全部 `PYTHONHASHSEED=0`）

```
eval_before_seed0_1        P@3=0.4444 R@3=1 MRR=0.9778 cases=45   ← 改前
eval_after_seed0           P@3=0.4444 R@3=1 MRR=0.9556 cases=45   ← 改后（新行为）
eval_after_seed0_b         P@3=0.4444 R@3=1 MRR=0.9556 cases=45   ← 改后复跑，确定性
eval_after_flag0_seed0     P@3=0.4444 R@3=1 MRR=0.9778 cases=45   ← 改后 + 逃生开关置 0
```

**诚实读法**：

1. **P@3 与 R@3 一分未变**（0.4444 / 1.0）—— 该召回的仍然都召回了，也没有多占 top-3 名额。
2. **MRR 下降 0.9778 → 0.9556（−0.0222）** —— **有代价**，而且能指到具体用例：

```
=== 改前 vs 改后：逐用例 actual 差异（变化 18 / 45 条；这里只列 MRR 掉的 2 条）===
  case_017  q='上下文'
      before: ['context_aware', 'proactive_suggestion']                        (P=0.33 MRR=1.00)
      after : ['self-explanatory-ui', 'context_aware', 'proactive_suggestion'] (P=0.33 MRR=0.50)
  case_020  q='根据之前的话题调整策略'
      before: ['context_aware', 'memory_summary', 'pd-writing-skills-...']     (P=0.33 MRR=1.00)
      after : ['memory_summary', 'context_aware', 'pd-brainstorming-...']      (P=0.33 MRR=0.50)
```

⇒ MRR 的全部跌幅来自**这 2 条**：`self-explanatory-ui` 的 description_zh 含「上下文帮助」，
于是 `'上下文'` 从 0 分变满分，把原本的 top1 `context_aware` 顶到第 2。
**这是中文词汇真实重叠造成的排序交换，不是 bug** —— 它同时是"中文终于进索引了"的证据与代价。
其余 16 条变化只发生在 rank 2/3（P 与 MRR 都不变）。

3. **`eval_after_flag0` 与 `eval_before` 三项指标完全相等**，且逐用例 `actual`
   **45/45 完全一致** —— 逃生开关是"逐字回滚"，不是"近似回滚"（见 §6.2）。

### 5.3 既有测试对同一指标的锁

`tests/unit/test_skills_mgmt.py::TestRetrievalEvaluation` 里已有一条 xfail
（"TF-IDF 基线 Precision@3=0.4444 < 0.6 阈值"）—— 改前改后都是同一条 xfail，**未变**。

### 5.4 误召代价（**"只报召回提升"最容易掩盖的一面**）

两类负样本，全部用 **`min_score=0.0` 全量扫描后 `score>0` 计数**
（不用 top_k 截断口径 —— 那会被同分并列的先后影响，跨进程不可复现）：

```
── 远邻 8 条（与该 5 条技能毫无关系）──
  改前：误召 0 次
  改后：误召 1 次
        '解释一下傅里叶变换的物理意义' -> [('self-explanatory-ui', 0.0769)]

── 近邻 8 条（同属"写代码"主题、词汇有重叠，诉求不同）──
  改前：误召 1 次
  改后：误召 6 次
        '帮我重构这段代码，把函数拆小一点'  -> frontend-state-sync / code-observability /
                                              engineering-test-delivery / testing-anti-patterns 各 0.0769
        '解释一下什么是闭包'               -> self-explanatory-ui 0.125
        '这个接口为什么返回 500，帮我看看'  -> code-observability 0.0833
        （'帮我优化一下这条 SQL 查询的性能' / 'git 怎么撤销上一次提交' /
          '帮我把这个网页的字体调大一点' / '帮我把变量名改成驼峰命名' /
          '写一个正则匹配邮箱地址' -> 仍不误召这 5 条）
```

**top_k=5（生产默认截断口径）的同一组**，便于与线上观感对齐：

```
           改前        改后
  远邻      0/8         1/8
  近邻      1/8         4/8
```

**代价的定性**：误召全部落在"**同主题但诉求不同**"的近邻上，且**分数都很低（0.0769~0.125）**——
它们靠 `min_score=0.01` 这条极低阈值才进候选，上层一旦按
`_BOUNDED_QUALITY_KEYS`（tfidf_score/vector_score/rerank_score）做质量门就会被滤掉。
但**远邻里出现了 1 例（傅里叶变换 → 自解释界面）**，这是真正"不该中"的一条，
**如实登记为已知代价**，并已用 `MEASURED_FAR_FP = 1` 钉死在新测试里。

---

## 6. 第 4 步：逃生开关

### 6.1 开关语义

`CP_SKILL_META_INCLUDE_ZH`（读取点 `agent/skills_mgmt/loader.py:88`）：

| 取值 | 行为 |
|---|---|
| 未设置 / 空串 / 1 / true / yes / on（大小写与空白不敏感） | **新行为**：索引文本 = name + description + tags + category + **description_zh** |
| 0 / false / no / off | **旧行为**：逐字回到四字段拼接 |

分级依据：**置 0 = 恢复改动前的旧行为**（不是拆掉某道防护）⇒ **A 级**（`_a(...)`）。

### 6.2 逃生开关证明（原始输出）

```
### G1C-U1 召回探针  tag=flag0_old_behavior
### 环境变量 CP_SKILL_META_INCLUDE_ZH = '0'
  中文命中 = 2/8          ← 与改前一致
  英文命中 = 8/8
```

**逐用例对拍（不是"指标看着一样"）**：

```
=== 逃生开关对拍：改后(flag=0) vs 改前 ===
  逐用例 actual 完全一致 = True
  overall before={'precision': 0.4444, 'recall': 1.0, 'mrr': 0.9778}
  overall flag0 ={'precision': 0.4444, 'recall': 1.0, 'mrr': 0.9778}

=== 1400 个打分点对拍 ===
  flag=0(旧行为) -> 默认(新行为) : 升=91 降=0 不变=1309
```
（反向读即：flag=0 相对默认是"0 升 0 降"，与改前逐点一致）

**误召也跟着回滚**：

```
  置 0 后：远邻误召 0 次、近邻误召 1 次（= 改前实测值）
```

### 6.3 一个容易漏的细节：倒排索引缓存键必须跟着开关走

`_get_inverted_index` 原本只用 `id(index)` 判缓存有效性。索引**文本**现在由开关决定
⇒ 只看 `id(index)` 会让"进程内翻转开关后仍命中旧倒排" = **逃生开关假生效**。
已把开关取值并入缓存键：

```python
    include_zh = _include_description_zh()
    if self._inverted_index is not None and \
       self._inverted_index_meta_id == id(index) and \
       self._inverted_index_zh_flag == include_zh:
        return self._inverted_index
```

实测（新测试 `test_flag_flip_rebuilds_inverted_index`）：翻转后 `inv_off is not inv_on`，
且指纹 token `'日志'`（旧索引里不存在）消失；翻回后重新出现。

### 6.4 登记状态

`agent/settings/registry.py:2511`：

```python
    _a("CP_SKILL_META_INCLUDE_ZH", CAT_SKILLS, True,
       "生产 Layer-1（SkillLoader 的 _meta_to_meta_text）索引文本是否并入**中文** "
       "description_zh（默认开 = 新行为）；置 0/false/no/off ⇒ 逐字回到只拼 "
       "name/description/tags/category 的旧行为（中文 query 召回回落、打分与倒排索引"
       "回到改动前）[G1C-U1；agent/skills_mgmt/loader.py:88,132,383]",
       owner="agent/skills_mgmt/loader.py"),
```

**零缺口护栏**（`scripts/scan_settings.py` AST 机械提取 vs 注册表）：

```
$ python -m pytest tests/unit/test_settings_registry.py -q
collected 56 items
tests\unit\test_settings_registry.py ................................... [ 62%]
.....................                                                    [100%]
============================= 56 passed in 40.50s =============================
```

**56 passed = 基线值**（改前实测同为 56 passed），零缺口与零重造两条守卫都绿。

---

## 7. 非空转自证（删掉改动 ⇒ 新测试必须变红）

新测试：`tests/unit/test_skill_meta_zh_recall.py`（326 行 / 46 条用例，**46 passed**）。

**做法**：把 loader.py 备份到仓库外 → 用 `edit` 逐处删除本卡改动 → 跑测试 → 从备份还原。
还原后 sha256 与备份**逐字节相同**：
`808220B753CD2A170565AB91994CA87E9C3EDE1E17FCC0D43B764049CEA82BB9`。

### 7.1 形态 A：**完全删除**本卡改动（helper + env 常量 + append + 缓存键）

```
======================== 28 failed, 18 passed in 3.07s ========================
FAILED ...::TestIndexSideFieldSelection::test_meta_text_includes_description_zh_for_all_dual_track
FAILED ...::TestIndexSideFieldSelection::test_the_five_targets_have_chinese_in_their_index_text
FAILED ...::TestIndexSideFieldSelection::test_inverted_index_contains_chinese_bigrams_of_targets
FAILED ...::TestProductionRecall::test_chinese_query_recalls_expected_skill[zh01]
FAILED ...::TestProductionRecall::test_chinese_query_recalls_expected_skill[zh03]
FAILED ...::TestProductionRecall::test_chinese_query_recalls_expected_skill[zh04]
FAILED ...::TestProductionRecall::test_chinese_query_recalls_expected_skill[zh05]
FAILED ...::TestProductionRecall::test_chinese_query_recalls_expected_skill[zh06]
FAILED ...::TestProductionRecall::test_chinese_query_recalls_expected_skill[zh07]
FAILED ...::TestProductionRecall::test_zh_and_en_hit_counts
FAILED ...::TestEscapeHatch::test_flag_values[12 组取值全红]
FAILED ...::TestEscapeHatch::test_flag_zero_makes_chinese_queries_that_newly_hit_miss_again
FAILED ...::TestEscapeHatch::test_flag_flip_rebuilds_inverted_index
FAILED ...::TestFalsePositiveBoundary::test_far_neighbour_false_positive_count_is_locked
FAILED ...::TestFalsePositiveBoundary::test_near_neighbour_false_positive_count_is_locked
FAILED ...::TestSwitchIsRegistered::test_read_site_is_the_loader
```

### 7.2 形态 B：**只删行为**（保留 `_include_description_zh()`，但不再 append）—— 隔离"行为"本身

```
======================== 14 failed, 32 passed in 2.91s ========================
FAILED ...::TestIndexSideFieldSelection::test_meta_text_includes_description_zh_for_all_dual_track
FAILED ...::TestIndexSideFieldSelection::test_the_five_targets_have_chinese_in_their_index_text
FAILED ...::TestIndexSideFieldSelection::test_inverted_index_contains_chinese_bigrams_of_targets
FAILED ...::TestProductionRecall::test_chinese_query_recalls_expected_skill[zh01/zh03/zh04/zh05/zh06/zh07]
FAILED ...::TestProductionRecall::test_zh_and_en_hit_counts
FAILED ...::TestEscapeHatch::test_flag_zero_makes_chinese_queries_that_newly_hit_miss_again
FAILED ...::TestEscapeHatch::test_flag_flip_rebuilds_inverted_index
FAILED ...::TestFalsePositiveBoundary::test_far_neighbour_false_positive_count_is_locked
FAILED ...::TestFalsePositiveBoundary::test_near_neighbour_false_positive_count_is_locked
```

**红路断言原文**（证明红的是"行为"，不是导入错误）：

```
E   AssertionError: 中文 query '写测试时要避免哪些反模式' 召回不到 testing-anti-patterns；实际 score>0 的有 []
E   assert 'testing-anti-patterns' in {}
E   AssertionError: 中文命中 2/8、英文命中 8/8
E   assert (2, 8) == (8, 8)
E   AssertionError: 远邻负样本误召 = 0，钉死值 = 1
E   assert 0 == 1
E   AssertionError: 近邻负样本误召 = 1，钉死值 = 6
E   assert 1 == 6
```

> **注意最后两条**：误召"锁"是**双向**的 —— 提高召回会撞上它（改前 0 / 1），
> 若有人把这个改动**再放宽**，它同样会红。它不是"只准涨"的单向断言。

还原后再跑：**46 passed**。

---

## 8. 既有回归

### 8.1 与技能检索/索引相关的 21 个测试文件（**改前 vs 改后**）

命令（两轮完全相同，`PYTHONHASHSEED=0 -p no:randomly`）：

```
改前（20 个文件，不含本卡新测试与 test_settings_registry.py）：
   ====== 2 failed, 367 passed, 2 skipped, 1 xfailed, 1 warning in 51.00s ======

改后（21 个文件，含 test_skill_meta_zh_recall.py 与 test_settings_registry.py）：
   == 2 failed, 469 passed, 2 skipped, 1 xfailed, 1 warning in 91.86s (0:01:31) ==
```

**算术核对**：`367 + 46（新测试）+ 56（settings_registry）= 469` ⇒
**新增全绿，零新增红灯**。2 个 skip 与 1 个 xfail 与改前逐条同名
（skip 均为 `--runslow` 门槛；xfail 是既有 TF-IDF 阈值项）。

文件清单：`test_skill_meta_zh_recall.py`（本卡新增）、`test_skill_registry.py`、
`test_skill_registry_audit.py`、`test_skill_description_single_source.py`（**32 passed = 基线**）、
`test_skills_mgmt.py`、`test_vector_skill_searcher.py`、`test_skill_index_cache.py`、
`test_skill_h3_migration.py`、`test_query_pattern.py`、`test_retrieval_silent_failures.py`、
`test_s10_03_retrieval_quality_gate.py`、`test_bm25_skill_searcher.py`、
`test_skill_search_description_source.py`、`test_skill_manager.py`、`test_skill_reranker.py`、
`test_verify_migrated_skills.py`、`test_update_meta_no_data_loss.py`、
`test_skill_create_no_data_loss.py`、`test_s2_gate_is_not_false_green.py`、
`test_skill_update_audit.py`、`test_settings_registry.py`

### 8.2 那 2 个红灯：**既有、与本卡无关**（不算到本卡头上，也不偷偷改绿）

```
FAILED tests/unit/test_s2_gate_is_not_false_green.py::test_pathA_bare_loader_cannot_recall_main_track_skills
FAILED tests/unit/test_s2_gate_is_not_false_green.py::test_drift_script_s2_fails_on_pathA_gap_now
E   AssertionError: 裸入口已能召回主轨技能 ['code-observability', 'engineering-test-delivery',
    'frontend-state-sync', 'self-explanatory-ui', 'testing-anti-patterns']；若属实，S2 pathA 门应已转绿，请更新本测试
```

**归因**：**改前那一轮（本卡未写一行代码时）就已经是这 2 条红**，输出逐字相同。
成因是 G1-C 把 `KNOWN_MAIN_TRACK_ONLY` 从 7 缩到 2 时，只更新了
`test_skill_description_single_source.py`，**漏掉了同一族的 `test_s2_gate_is_not_false_green.py`**
（该文件自己的断言消息就写着"若属实…请更新本测试"）。它读的是
`verify_index_drift._production_recall_sets` 返回的 **id 集合**（`load_metadata_index().keys()`），
**不经过 `_meta_to_meta_text`** ⇒ 本卡的打分改动**结构上不可能**影响它。

**处置**：**未修改该文件**（不在本卡文件范围；改写别人的守卫属越界）。
仅登记为**待 G1-C owner 或 CI owner 收口的既有红灯**。

### 8.3 因为 loader.py 是 tool-retrieval-ci.yml 的触发路径，额外跑了它的三个 job

```
$ python scripts/verify_bm25_proper_noun.py
== 专有名词匹配验证通过 ==     EXITCODE=0

$ python -m pytest tests/unit/test_tool_retrieval_quality.py -q
======================== 22 passed, 1 xfailed in 2.35s ========================

$ python -m pytest tests/unit/test_tool_negative_samples.py -q
======================= 26 passed, 13 xfailed in 3.24s ========================
```

**控制组**（排除"是不是我造成的 xfail 漂移"）：这 2 个文件**根本不 import skills_mgmt / loader**
（grep 命中 0 行），且 `CP_SKILL_META_INCLUDE_ZH=0` 与默认**结果逐字相同**
（`48 passed, 14 xfailed` ×2）⇒ 与本卡无关。**但如实指出一处既有漂移**：
`test_tool_negative_samples.py` 的 CI 摘要文字写"预期 27 passed + 12 xfailed"，
实测 **26 passed + 13 xfailed**（见 §9.2 R-5）。

---

## 9. 未验证项与残留风险（**不粉饰**）

### 9.1 未验证项

| # | 未验证 | 为什么 | 影响 |
|---|---|---|---|
| **U-A** | **向量腿 / BM25 腿 / RRF 融合**未按新索引文本重编码或重跑 | 本卡只改 `_meta_to_meta_text`（TF-IDF 单路的索引文本源）。向量库是**离线重编码**的派生数据，离线窗口无模型（G1-B 已记录 18 分钟挂起）；`bm25_searcher._skill_to_doc` 是**同形独立实现**，本次**未同步** | ⚠️ 走 `use_bm25=True` / `use_vector=True` 的路径**看不到本卡收益**（BM25 仍只吃英文 description）。生产默认 `match()` 是 TF-IDF 单路 ⇒ 默认链路已覆盖。**建议单开卡**同步 `_skill_to_doc` |
| **U-B** | 生产 `SkillsMgmtService` 的端到端未点（HTTP / UI） | 任务卡禁止启动常驻服务 | 替代证据：`SkillLoader` 的**两条装配形态**（裸 / `+SkillIndexCache`）都实测 8/8（§3） |
| **U-C** | 中文 query 的**排序质量**（不只命中率）未系统评估 | 本卡只构造了 8 条中文 query + 16 条负样本，不足以做 MRR 级评估；黄金集 45 条以中文为主但**不含这 5 条的期望技能** | 已知的具体损失见 §5.2 的 case_017 / case_020。**建议**给黄金集补这 5 条的中文 case（本卡未改黄金集：不在文件范围） |
| **U-D** | 15 条 pd-* 被波及后的**实际业务观感**未评估 | 需要真实会话样本 | §4.3 已量化到"15 条 pd-* 里 11 条、最多 10/50 个 query 被打分"，但"用户会不会因此被推错技能"未测 |

### 9.2 残留风险

| # | 风险 | 触发信号 | 归属 / 预案 |
|---|---|---|---|
| **R-1** | **误召上升**：远邻 0→1、近邻 1→6（全量口径） | 新测试 `test_*_false_positive_count_is_locked` 红 | 已钉死数字。若判定不可接受 ⇒ 置 `CP_SKILL_META_INCLUDE_ZH=0` 一行回滚 |
| **R-2** | **MRR 下降 0.0222 / 2 条中文 query 的 top1 被换掉** | `eval_skill_retrieval.py` 的 MRR 继续下降 | 已量化到具体 case。这是"中文进索引"的必然代价，不是可修的 bug |
| **R-3** | **全部 28 条打分被扰动（实测 17 条）**，含 11 条 pd-* | 任何依赖**绝对分数阈值**的下游若用 `min_score` 卡线 | 本卡只读生产默认 `min_score=0.01`（极低）；`_BOUNDED_QUALITY_KEYS` 口径未变。**建议**：任何按分数卡线的消费方在本改动上线后复核 |
| **R-4** | `tests/unit/test_skill_h3_migration.py:29-32` 的模块 docstring **已过期** | 它写"生产 Layer-1 只拼英文 description ⇒ 中文 query 对全部 28 条技能都召回弱" | **本卡未改该文件**（不在文件范围）。**建议**改成"G1C-U1 已并入 description_zh（CP_SKILL_META_INCLUDE_ZH）"。它是注释不是断言，**不会红** |
| **R-5** | `test_tool_negative_samples.py` 的 CI 摘要文字（"预期 27 passed + 12 xfailed"）与实际（**26 + 13**）不符 | 无断言红（控制组已证与本卡无关） | 既有漂移，**留待该 CI owner** |
| **R-6** | **`_tfidf_scan` 的同分并列顺序不可复现**（set 迭代 + 哈希随机化） | 不设 `PYTHONHASHSEED` 时 MRR 在 0.9519~0.9778 间跳 | **既有缺陷，非本卡引入**（改前改后都存在）。它使"改前 vs 改后"的 MRR 对比必须先固定 hash seed —— 本卡所有对照都已固定。**建议**给候选集加确定性排序（一行 sorted），但那会改变 tie 语义，属独立卡 |
| **R-7** | 主轨 description（中文历史副本）与文件轨 description_zh **同时**进了不同消费者的打分（前者进管理页搜索、后者进生产 Layer-1） | 若两者不一致（G1-B 的 R-c 残留） | 实测双轨 20 条满足 `主轨 description == 文件轨 description_zh`（G1-C §2.5），**当前无不一致** |
| **R-8** | 若将来有技能**只写 description_zh 不写英文 description**，索引文本会变成纯中文 | 英文 query 召回下降 | 与 G1-B 契约（description 英文为唯一权威）冲突，属数据纪律问题，非本卡 |

---

## 10. 交付物 / 改动清单 / 回滚 / 残留物

### 10.1 改了哪些文件

| 文件 | 改动量 | 内容 |
|---|---|---|
| `agent/skills_mgmt/loader.py` | **+73 行**（约 45 行是 Why 注释/文档字符串；有效逻辑 3 处） | ① `:79` 常量 `_ENV_META_INCLUDE_ZH`；② `:82-91` `_include_description_zh()`；③ `:132-136` `_meta_to_meta_text` 并入 description_zh；④ `:368` / `:390-393` / `:406-407` 倒排索引缓存键纳入开关 + 可观测日志 |
| `agent/settings/registry.py` | **+12 行**（仅本条登记；该文件另有**别的卡**的 SET-REG 块） | `:2511` `_a("CP_SKILL_META_INCLUDE_ZH", CAT_SKILLS, True, ...)` |
| `tests/unit/test_skill_meta_zh_recall.py` | **新增 326 行 / 46 条用例** | 字段选择 / 候选池 / 分词口径 / 中文召回 / 英文不退化 / 逃生开关 / 误召边界 / 登记状态 |
| `scripts/eval_skill_retrieval.py` | **未改**（读后确认可用、口径合适） | — |
| `agent/skills_mgmt/index_cache.py` | **未改** | — |

### 10.2 回滚指令

**热回滚（推荐：一行、不动代码、立即可逆）**：

```powershell
# 恢复旧行为（中文 query 召回回落到 2/8）
$env:CP_SKILL_META_INCLUDE_ZH = '0'
```

或在 `.env` / 主机环境写 `CP_SKILL_META_INCLUDE_ZH=0`
（`0/false/no/off` 任一），或经设置接口
`POST /api/cp/settings/CP_SKILL_META_INCLUDE_ZH/reset` 清除覆盖层。
**该开关每次调用都读 ⇒ 热生效；倒排索引会因缓存键变化自动重建，无需重启。**
实测：置 0 ⇒ 逐用例 actual 45/45 与改前完全一致、1400 个打分点 0 升 0 降、误召回到 (0, 1)。

**代码回滚（定向，不碰其他卡的 35 份未提交改动）**：按 §10.1 的四处逐处反向编辑
`loader.py` 与 `registry.py` 的那一条（本卡改动在代码里统一带
`【G1C-U1】` 标记，可 `grep -n "G1C-U1"` 定位）；新测试文件可直接删除。
**全程未用 `git checkout` / `git add` / `git commit`。**

### 10.3 残留物自证

```
仓库内新增未跟踪文件（本卡）= tests/unit/test_skill_meta_zh_recall.py   ← 唯一一个，刻意交付
仓库内新增探针脚本 = 0
    （grep 'probe|g1cu1' 命中的全是他卡既有文件：_ci_logs/ 、_scratch/ 、_t06_logs/ 、_tmp_rootcause_probe/）
探针落点 = C:\Users\Administrator\AppData\Local\Temp\g1cu1\   （19 个文件，全部在仓库外）
常驻服务 = 未启动
本卡起的进程 = 全部已结束（无存活 python 属于本卡）
未写 data/learned_workflows.json；未碰 data/audit/daily_roots.jsonl
未 taskkill 任何他卡进程
未改 agent/tool_router_hybrid.py / agent/audit/ / plugins/ / agent/workflow_learning/ /
     yunshu-ui/ / data/skills_repo/ / config.yaml / prompt 装配文件
HEAD = 5c9ace10a4ca4bb96860db3a48debf9ddcf496bf（收尾复核，未变）
```

---

## 11. 一句话总结

**改前**：这 5 条的"可召回"只对英文 query 成立（中文 **2/8**）。
**改后**：中文 **8/8**、英文仍 **8/8**、1400 个打分点 **0 下降**。
**代价**：MRR **−0.0222**（2 条中文 query 的 top1 被换）、远邻误召 **0→1**、近邻 **1→6**。
**可控**：`CP_SKILL_META_INCLUDE_ZH=0` 逐字回到改前（已证 45/45 用例 + 1400/1400 打分点）。
