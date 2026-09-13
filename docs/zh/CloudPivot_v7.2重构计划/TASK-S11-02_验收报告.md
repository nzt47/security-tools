# TASK-S11-02 验收报告 — 工作流触发词政策收紧 + seed 脚本加固

- 日期：2026-09-14
- 基线：`master` = `4e1f5b67`（本 worktree 起点实为 `506aeb60`，见文末说明）
- 交付提交：`0363221d`（分支 `s11-02/main`）
- worktree：`.worktrees/s11-02`
- 未触碰：`agent/workflow_learning/matcher.py` 的匹配算法、`learner._WORD_RE`
  （任务陷阱明确禁止）、`agent/digestion/`、`agent/orchestrator/orchestrator.py`

---

## 一、交付物

| 类型 | 文件 | 说明 |
|---|---|---|
| 改 | `agent/workflow_learning/admission.py` | 新增**纯净度**门槛：新拒绝码 `CODE_IMPURE_TRIGGER_LIST`；`check_structure` 改为"全无有效触发词"与"混入无区分度触发词"**互斥**两分支；模块 docstring 新增 §4（政策依据 a~f）与 ⚠️ 残留缺口声明 |
| 改 | `tests/unit/test_workflow_learning_admission.py` | 2 条旧政策断言改写为新政策（**未放宽**，另**新增** 1 条正向收紧用例）；覆盖契约 docstring 增第 8 条 |
| 改 | `scripts/seed_demo_workflows.py` | `--repo` 必填、默认 dry-run（写临时仓库）、写盘须 `--apply` |
| 新增 | `scripts/dev/probe_trigger_policy.py` | 政策探针：旧/新规则显式求值对照 + 真实匹配候选读数 + 残留缺口检验 + 优先级代价（可复算） |
| 改 | `data/learned_workflows.json` | 2 条 `status` → `archived`（条目未删、字段未改）；仅 2 行 diff |
| 改 | `data/learned_workflows_retired.jsonl` | 追加 2 行退役台账（append-only） |

---

## 二、审核漏洞的确认与修正（先查清再改）

### 2.1 matcher 语义 —— 审核假设的「任一触发词命中」**不存在**

**结论：`WorkflowMatcher` 不做触发词命中判定，命中 = TF-IDF 余弦相似度 ≥ `min_similarity`。**
触发词只是被拼进索引文本的一个字段，不存在"任一/全部触发词命中"这一判定环节，
故任务书里"若为'任一'则单字会大面积误触发"的前提**不成立**。

代码级证据（`agent/workflow_learning/matcher.py`）：

| 行 | 内容 | 说明 |
|---|---|---|
| 92-126 | `TfidfIndex.query()` | 返回 `[(wf_id, cosine_similarity)]`，只有余弦相似度 |
| 116-122 | `sim += w * vec[t]` | 归一化向量点积 = 余弦；**无任何触发词布尔判定** |
| 186-193 | 索引文本 = `name + description + task_signature + 有效触发词 + tags + 步骤工具名 + source_user_input` | 触发词**只是**索引文本的一个拼装字段 |
| 252 | `if sim < self.min_similarity: continue` | 唯一的"命中"门槛 |

Skills 侧同构：`agent/skills_mgmt/loader.py:409 match()` 走 `_match_score`（同文件 `:115`）
TF-IDF；`skill_converter._compile_skill_content:326-329` 把触发词渲染进 SKILL.md 的
「触发条件」只是**给 LLM 看的 markdown 叙述**，无机器匹配。
全仓库检索确认**不存在**任何"触发词任一命中即通过"的闸门。

### 2.2 但审核指出的风险**确实存在**，通道不是「任一命中」而是另三条

改前实测（`python scripts/dev/probe_trigger_policy.py`，`data/learned_workflows.json`）：

```
注册入候选池: ['json-30a189b6']
被准入否决  : ['python-eb17ed25', 'wf-28f2775d', 'wf-c7499f27', 'wf-f19dc52c', 'zip-d2968c59']
  '读取 JSON 配置文件并转换为 YAML 格式'  -> json-30a189b6:0.3472
  '读取配置'                            -> json-30a189b6:0.2309
  '读'                                  -> json-30a189b6:0.1443   ← 单字输入成为候选
  '取'                                  -> json-30a189b6:0.1443   ← 单字输入成为候选
  '配置'                                -> json-30a189b6:0.1225   ← 2 字输入成为候选
```

（打印值为 `combined = sim × conf_factor × priority_factor`；该条 `conf=0.5`、
`success_count=1` ⇒ `conf_factor=0.5`，`priority=60` ⇒ `priority_factor=0.8`，
合计因子 0.4，故 `sim = combined / 0.4`。换算：`读/取` sim **0.361**、`配置` sim **0.306**，
均 **≥ `min_similarity=0.3`** ⇒ **1 个汉字就能触发一条 4 步工具链的自动执行**。
换算为本文档依据打印值的算术推导，非另测。）

即：**"单字误触发"是真的，但根因不在"任一触发词命中"，而在单字回流索引文本。**
单字回流的具体通道（改前）：

1. **`tags` 后门（代码缺陷，行级）**：`learner.py:137`
   `tags=["learned"] + triggers[:3]` 把触发词**镜像**进 `tags`；S10-01 之前的版本写入的是
   **未过滤**的原始单字（存量数据可见 `tags: ["learned","读","取","json"]`）。而
   `matcher.py:190` 拼索引文本时 `" ".join(wf.tags)` **不经过** `effective_trigger_patterns`
   —— 紧邻的 `matcher.py:188` 对 `trigger_patterns` 做了过滤，**被 190 行绕过**。
2. **`task_signature`**：`json-30a189b6` 的 `json|件|取|并|换|文|置|读|转|配`，按字切分。
3. **`source_user_input`**（S10-01 冷启动修复**有意**加入）：中文按字切分。

### 2.3 污染触发词的可量化代价（第 4 段实测）

```
json-30a189b6 触发词=['读','取','json','配','置']（有效 1 个 / 混入单字 4 个）
  _compute_priority(原样)   = 60   ← 5 个触发词命中 '>=3' 加成
  _compute_priority(纯净后) = 50   ← 仅 1 个有效触发词
  仓库中实测 priority 字段  = 60（与 _compute_priority(原样) 一致: True）
  污染代价 = +10 优先级（priority_factor 0.800 → 0.750）
```

`generator.py:109` 的 `if len(wf.trigger_patterns) >= 3: base += 10` 把 4 个单字
**也算进长度** ⇒ 演示数据骗到 +10 优先级，`priority_factor` 虚高 6.7%。

---

## 三、政策变更（策略变更声明）

### 3.1 变更点与影响面

| 项 | 改前（S10-01） | 改后（S11-02） |
|---|---|---|
| 判定 | `len(effective_trigger_patterns(tp)) >= 1`（**存在** 1 个有效触发词即放行） | 在上条基础上**追加纯净度**：`single_char_triggers(tp)` 必须为空 |
| 单字触发词 | 从索引特征中**丢弃**，但**留在声明列表**里 | 同上丢弃；且**声明列表不纯净 → 整条判 dirty** |
| 拒绝码 | `NO_DISCRIMINATIVE_TRIGGER` | 新增 `IMPURE_TRIGGER_LIST`，与前者**互斥**（全坏 vs 混坏，不重复计数） |
| 阈值数字 | `MIN_STEPS=2`、`MIN_TRIGGER_CHARS=2` | **不变**，未新增任何数字 |
| 影响面 | — | 匹配候选准入、转技能准入（`force` 不可越过）、存量退役判定源，三处同源同步生效 |

### 3.2 为什么是这个阈值（不新增数字 + 整条否决）

1. **零收益**：单字触发词本就被 `matcher.py:188` 过滤，**不进索引特征**，
   留在声明里对匹配没有任何正贡献 —— "严格一点总会丢东西"的顾虑不成立。
2. **实测负收益一（优先级倒挂）**：`generator.py:109` 按长度给 +10，单字算数（见 §2.3，60 vs 50）。
3. **实测负收益二（tags 回流索引）**：`matcher.py:190` 不过滤 `tags`。同条数据换成纯净触发词后，
   单字查询 `读` 的 sim 由 **0.361 降到 0.303**（§2.2 与 §3.3 C 段），即污染确实抬高了误触发分。
4. **实测负收益三（导出契约污染）**：`skill_converter.py:326-329` 把原始列表逐项渲染进
   SKILL.md「触发条件」，5 行里 4 行是无用单字，作为"LLM 参考"的适用性声明交给模型。
5. **为什么不取比例阈值（如"有效占比 ≥50%"）**：比例阈值要引入一个**没有锚点**的新数字
   （为什么 50%？为什么不是 2 个？），而纯净度**不引入新数字** —— 复用 §2 既有的
   `MIN_TRIGGER_CHARS=2`，政策里始终只有**一个**阈值；且"纯净列表"正是写入方
   `learner.py:117`（先过滤再落库）**本来就产出**的形态，政策与写入方契约一致、可机械核对。
6. **为什么"整条否决"不过严**：新学习路径落库前已过滤单字，**新条目按构造即纯净**，
   本门槛对新路径是 **no-op**；它只在存量/demo/手工改写条目上触发，正是本次审核的漏洞面。
   退役 ≠ 失效：`archived` 条目仍可按 ID 人工执行（`retirement.py` docstring 已声明该语义）。

### 3.3 改前 / 改后 同输入对比

**A. 政策判定对照（同一批 6 条存量，两条规则在探针内显式求值）**

| workflow_id | steps | 混入的单字 | 旧规则 | 新规则 |
|---|---|---|---|---|
| `json-30a189b6` | 4 | `['读','取','配','置']` | **通过** | **否决** |
| `python-eb17ed25` | 3 | `['统','计','项','目','里']` | 否决 | 否决 |
| `wf-28f2775d` | 1 | `[]` | 否决 | 否决 |
| `wf-c7499f27` | 1 | `['列','出','当','前','工']` | 否决 | 否决 |
| `wf-f19dc52c` | 1 | `['列','出','当','前','工']` | 否决 | 否决 |
| `zip-d2968c59` | 2 | `['包','代','码','仓','库']` | 否决 | 否决 |

⇒ 政策差异**只命中 1 条**（`json-30a189b6`），其余 5 条两规则结论一致。

**B. 真实匹配候选（同一批查询集，真实代码路径 `WorkflowMatcher`，`min_similarity=0.3`）**

| 查询 | 改前 | 改后 |
|---|---|---|
| `读取 JSON 配置文件并转换为 YAML 格式` | `json-30a189b6:0.3472` | (无候选) |
| `读取配置` | `json-30a189b6:0.2309` | (无候选) |
| `读` | `json-30a189b6:0.1443` | (无候选) |
| `取` | `json-30a189b6:0.1443` | (无候选) |
| `配置` | `json-30a189b6:0.1225` | (无候选) |
| `帮我列出当前工作目录下的文件` | (无候选) | (无候选) |
| `把代码仓库打包成 zip 压缩包` | (无候选) | (无候选) |
| `统计项目里所有 Python 文件的行数并保存报告` | (无候选) | (无候选) |
| **候选池** | `['json-30a189b6']` | **`[]`** |

**C. 残留缺口检验（诚实披露，非"全绿"）**
把 `json-30a189b6` 的触发词换成**纯净**的 `["json"]`（其余字段原样、强制 `active`）后：

```
  '读'    -> wf-pure-probe:0.1213    (sim = 0.303，仍 ≥ 0.3 ⇒ 仍会命中)
  '取'    -> wf-pure-probe:0.1213
  '配置'  -> wf-pure-probe:0.1287    (sim = 0.322，仍会命中)
  '读取'  -> wf-pure-probe:0.1715
  '读取配置'-> wf-pure-probe:0.2123
```

⇒ **触发词政策只能消除 tags/声明这条通道，不能消除"单字输入触发多步自动执行"这一现象**：
根因在索引文本构成（`source_user_input` / `task_signature` 对中文按字切分）与
`min_similarity=0.3` 阈值本身。本任务**未**改这两者（改 `source_user_input` 会
回退 S10-01 的冷启动匹配修复；改阈值属匹配质量策略，需单独裁定）—— 已登记为遗留 #1。

---

## 四、存量复核（dry-run → `--apply`）

### 4.1 dry-run

```
$ python scripts/retire_dirty_workflows.py
判定阈值: MIN_STEPS=2  MIN_TRIGGER_CHARS=2
模式: DRY-RUN(未写入)
条目总数: 6
命中脏条件(结构性否决)条数: 6
退役后仍为脏且未归档的条数: 6
本轮实际迁移(active→archived): 2
已是 archived（幂等跳过）: 4 ['python-eb17ed25','wf-c7499f27','wf-f19dc52c','zip-d2968c59']
台账追加行数: 0
退役清单:
  - json-30a189b6 | steps=4 | triggers=['读','取','json','配','置'] | codes=['IMPURE_TRIGGER_LIST'] | session=demo-seed
  - wf-28f2775d   | steps=1 | triggers=[] | codes=['STEPS_TOO_FEW','NO_DISCRIMINATIVE_TRIGGER'] | session=audit-4a3b4826
```

### 4.2 **新增**被判 dirty 的条目（可复算）

用 `git show master:data/learned_workflows.json` 取改前数据，对**同一份改前输入**分别求值
两条规则：

```
旧规则 dirty: ['python-eb17ed25','wf-28f2775d','wf-c7499f27','wf-f19dc52c','zip-d2968c59']
新规则 dirty: ['json-30a189b6','python-eb17ed25','wf-28f2775d','wf-c7499f27','wf-f19dc52c','zip-d2968c59']
*** 新增 dirty: ['json-30a189b6']
*** 由 dirty 变 clean: []
```

⇒ **新增 dirty 恰好 1 条：`json-30a189b6`**（审核点名的那条）；反向 0 条（无政策回退）。

⚠️ **口径区分（dry-run 的"2 条迁移"≠"2 条新增 dirty"）**：`wf-28f2775d` 在**改前**
就已被判 dirty（`STEPS_TOO_FEW` + `NO_DISCRIMINATIVE_TRIGGER`），只是状态停留在 `draft`
而**从未归档** —— 这是 S10-01 退役轮次的执行缺口，与本次政策变更无关。
两者不可混为一谈。

### 4.3 `--apply`

```
$ python scripts/retire_dirty_workflows.py --apply
模式: APPLY(已写入)
命中脏条件(结构性否决)条数: 6
退役后仍为脏且未归档的条数: 0
本轮实际迁移(active→archived): 2
台账追加行数: 2
```

退役后 6 条状态：全部 `archived`（条目未删、`trigger_patterns`/`converted_to_skill_id`
等字段原样保留）。台账追加 2 行，`deleted: false`。

`git diff` 实际只有 **2 行**（`json-30a189b6` active→archived、`wf-28f2775d` draft→archived）：

```
 data/learned_workflows.json | 4 ++--
```

**运行期数据噪音政策复核**（任务陷阱要求）：执行 `python scripts/clean_runtime_noise.py`：

```
[clean-runtime-noise] data/learned_workflows.json: 还原 4 条统计漂移，保留 2 条真实改动
```

⇒ `status` 不在会计字段集 `{success_count, failure_count, confidence, updated_at, last_used_at}`
（`clean_runtime_noise.py:40-41`）内，故**真实改动被保留、不会被还原**（已复验：apply 后 6 条仍全为 `archived`）。
副作用：`updated_at` 属统计字段，按仓库既有政策被还原为 HEAD 值 —— 故 `archived` 状态的
**权威时间戳以台账** `data/learned_workflows_retired.jsonl` 的 `retired_at` 为准，属预期口径。

---

## 五、`seed_demo_workflows.py` 加固（S10-01 遗留 #6）

### 5.1 改动

| 项 | 改前 | 改后 |
|---|---|---|
| 目标仓库 | `WorkflowLearningService()` 无参 ⇒ **隐式生产仓库** | `--repo` **必填**，无默认值 |
| 默认行为 | 直接写盘 | **默认 dry-run**（写**临时目录**仓库，走完全一致的真实学习路径） |
| 写盘条件 | 无条件 | 必须**显式** `--apply` |
| `--dry-run` | 无 | 新增（与默认同义，供脚本化显式表达意图） |

### 5.2 尝试命令与输出（生产仓库 SHA256 全程不变）

| # | 命令 | 退出码 | 生产仓库 SHA256 |
|---|---|---|---|
| 1 | `python scripts/seed_demo_workflows.py` | **2** | 不变 |
| 2 | `... --repo data/learned_workflows.json`（默认 dry-run） | 0 | 不变 |
| 3 | `... --repo data/learned_workflows.json --dry-run` | 0 | 不变 |
| 4 | `... --repo data/learned_workflows.json --apply --dry-run` | **2**（互斥） | 不变 |

```
=== 1) 省略 --repo ===
usage: seed_demo_workflows.py [-h] --repo PATH [--apply | --dry-run]
seed_demo_workflows.py: error: the following arguments are required: --repo
[exit code: 2]

=== 2) 显式 --repo、不加 --apply ===
模式: DRY-RUN（不写入目标仓库）
目标仓库(本次未被写入): ...\data\learned_workflows.json
临时仓库: C:\Windows\TEMP\seed_demo_wf_0j1yz2li\learned_workflows.json
沉淀前 workflow 数: 0
[沉淀] python-eb17ed25: ... status=WorkflowStatus.DRAFT
[沉淀] zip-d2968c59: ... status=WorkflowStatus.DRAFT
[沉淀] json-30a189b6: 自动学习: json steps=['read_file','json_query','json_to_yaml','write_file'] conf=0.4 status=WorkflowStatus.ACTIVE
[DRY-RUN] 未写入 ...\data\learned_workflows.json；确认无误后加 --apply 才会真正沉淀
[exit code: 0]

生产仓库 SHA256 变化:
  基线      275996AC044C78D115F39B3473BE167F9353C041B3D53F3739FB64556039F8F1
  步骤1后   same=True     步骤2后 same=True     步骤3后 same=True     步骤4后 same=True
```

**副产品证据（顺带确认了存量条目的来源）**：dry-run 在**空仓库**上重放，确定性复现出
**同一个 id `json-30a189b6`**，且其触发词在新学习者路径下为**纯净的 `['json']`**
（`status=ACTIVE`），另两条因单步被打成 `DRAFT`。这既证实"现存 3 条 demo-seed 确由本脚本
产生"，也证实**新政策对新学习路径是 no-op**（§3.2 第 6 点）。

---

## 六、质量证据（门禁四条）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 相关套件 | `python -m pytest tests/unit -q -k "workflow_learning"` | **75 passed / 0 failed / 1 skipped**（skip 为 `test_query_pattern.py` 既有失效前提，与本任务无关） |
| 邻接回归① | `pytest tests/unit/test_workflow_learning.py tests/unit/test_workflow_learning_admission.py tests/unit/test_workflow_to_skill.py tests/unit/test_matcher_concurrency.py tests/unit/test_orchestrator_workflow_learning_layer.py tests/unit/test_process_distill.py tests/integration/test_skill_converter_integration.py -q` | **135 passed / 0 failed** |
| 邻接回归② | `pytest tests/unit/test_workflow_hybrid.py tests/unit/test_workflow_mode.py tests/unit/test_routes_workflow_learning.py tests/unit/test_shared_blackboard.py tests/integration/test_skills_workflow_flow.py tests/unit/test_digestion_generation.py -q` | **136 passed / 0 failed** |
| kwarg 冲突（agent） | `python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH` | **HIGH 0 处**（总计 0 处） |
| kwarg 冲突（tests） | `python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH` | **HIGH 0 处**（总计 0 处） |
| mypy | `python -m mypy agent/workflow_learning/admission.py scripts/seed_demo_workflows.py scripts/dev/probe_trigger_policy.py tests/unit/test_workflow_learning_admission.py --follow-imports=skip` | **Success: no issues found in 4 source files** |
| 架构 import | `lint-imports --config .importlinter` | **2 kept / 0 broken** |
| 闭环仿真 | `python scripts/simulate_workflow_closed_loop.py` | **✅ 工作流自动闭环全部符合预期**（退出码 0） |
| 准入审计工具 | `python scripts/audit_wf_admission.py --repo data/learned_workflows.json` | 匹配候选 **0**；拒绝码分布含 `IMPURE_TRIGGER_LIST: 1`；21 条探针命中 0 条 |
| 产物漂移 | `git status --short` | 工作区 clean（运行期噪音已按政策清理） |

**断言变更纪律说明**（原测试写的是**旧政策**意图，非"放宽"）：
- `test_single_char_triggers_never_become_index_features` → 重写为
  `test_mixed_trigger_list_rejects_whole_entry`：断言由 `register() is True` 改为 `is False`，
  并**新增** `d.codes == (IMPURE_TRIGGER_LIST,)` 的互斥性断言、"不得留在索引"断言。
- 新增 `test_pure_multi_trigger_list_still_admitted`：**正向收紧**，防止新门槛退化成
  "触发词多就否决"（`['python','统计','报告','行数']` 必须通过）。
- `test_committed_stock_repo_never_yields_dirty_candidates`：原"达标条目仍在池中"的
  防假绿断言在**新政策下存量 6 条全部不达标**，故改用**合成达标条目**作正向对照，
  并**新增**"逐条判定与该条自身数据独立复算一致"的断言 —— 断言仍有牙齿，
  不会因"全被拒"或"对象从报告里消失"而假绿。

---

## 七、遗留（带归属）

| # | 遗留 | 归属 | 依据 |
|---|---|---|---|
| 1 | **单字输入仍可触发多步自动执行**（`读` sim 0.303 / `配置` 0.322 ≥ `min_similarity=0.3`）：根因是索引文本含 `source_user_input` + `task_signature`，二者对中文按字切分；触发词政策只堵住 tags 通道 | 工作流**匹配质量**专项（非 S11-02 范围） | §3.3 C 段实测；候选方案：查询侧最小信息量闸门 / `min_similarity` 随查询 token 数自适应；**不宜**直接去掉 `source_user_input`（会回退 S10-01 冷启动修复） |
| 2 | `matcher.py:190` 的 `" ".join(wf.tags)` **不过滤**无区分度 token，与相邻 `:188` 的过滤语义不一致（"单字不进索引特征"的注释在 `:181-183` 因此**不成立**） | `agent/workflow_learning/matcher.py` | §2.2 通道 1。本次**未改**：单字仍会经 `source_user_input`/`task_signature` 进入索引，单独过滤 `tags` 无法建立该不变量，属遗留 #1 的同族问题，宜一并解决 |
| 3 | `generator.py:109` 的 `len(wf.trigger_patterns) >= 3 → +10` 用的是**原始长度**而非**有效**长度；纯净列表下语义正常，但若将来再有脏数据回流会重演优先级倒挂 | `agent/workflow_learning/generator.py` | §2.3 实测 60 vs 50。本次未改（纯净度门槛已使脏条目不进候选，无实际暴露面） |
| 4 | `wf-28f2775d` 在 S10-01 退役轮次中**已被判 dirty 却从未归档**（状态停在 `draft`）—— 反映退役脚本当时未覆盖 `draft` 态条目 | S10-01 执行缺口（本轮已顺带归档） | §4.2 口径区分 |
| 5 | 中文分词按字切分（`learner._WORD_RE`）导致的触发词无区分度问题；任务陷阱明确**禁止本次改动** | 学习质量专项 | 任务书【陷阱】第 2 条 |
| 6 | **改了 `agent/**` ⇒ 需重启云枢服务（127.0.0.1:5678）新政策才在运行期生效**；同时 `data/learned_workflows.json` 已落盘为 `archived`，但运行中进程的**内存索引**仍是旧态（`json-30a189b6` 可能仍在候选池内），**必须重启**才能收敛 | 运维（主工作区） | 环境事实约定 |

---

## 八、双远端 SHA

| 远端 | SHA |
|---|---|
| `origin/master` | 见文末交付说明（推送后回填） |
| `gitee/master` | 见文末交付说明（推送后回填） |
| 分支提交 | `0363221d`（`s11-02/main`） |

**基线说明**：任务书写 `master = 4e1f5b67`，但创建 worktree 时 `master` 已推进到
`506aeb60`（`docs(流程): 建「通用约定」唯一正本…`）。本 worktree 以 **`506aeb60`** 为基点
（`--base master` 的实际取值），故交付内容建立在 `506aeb60` 之上；`4e1f5b67` 上的
`CP_ENV_FILE` 补登等改动已包含在 `506aeb60` 的历史中。**口径差异已显式声明**。
