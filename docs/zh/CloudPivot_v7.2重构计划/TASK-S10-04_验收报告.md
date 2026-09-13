# TASK-S10-04 验收报告 — judge `verdict` 口径与 v7.2 §4.5 对齐

- 日期：2026-09-13
- 会话 worktree：`s10-04`（分支 `s10-04/main`）
- 基线：`master` = `717d530e`（任务书写的是 `bbc5d821`，实际开工时 master 已推进到 `717d530e`，
  二者对本任务涉及的判据**无差异**；`bbc5d821` 见下方遗留 L1）
- 交付前先与已推进的 master（`aab04ca4`，含 S10-02 / S10-06）合并复跑，合并提交 `38c0b760`（**零冲突**，ort 自动合并）
- 规格来源：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §4.5（第 219–228 行，关键第 **225** 行）
- Owner 裁定：**口径 A —— 结论前置 + 0.85 仍作用于 confidence**（本报告 §四 记录裁定与理由）
- 未触碰：`agent/skills_mgmt/`、`agent/orchestrator/`、`agent/digestion/{stage,internalize,probe,gate}.py`
  （同域但非 verdict 语义）；`agent/settings/registry.py`（见遗留 L1）

---

## 一、交付物

| 类型 | 文件 | 说明 |
|---|---|---|
| 改 | `agent/digestion/shadow.py` | `parse_judge_verdict` 结论前置；`LLMJudge.score` 层③得分跟结论；`_record_judge_verdict` 判定来源一致性；提示词写明 confidence 语义；文档字符串同步 |
| 改 | `agent/digestion/judge_runtime.py` | `JudgeVerdictStore.record` 并列留痕 `model_verdict`/`threshold_verdict`；`summary()` 新增 `conflicted`；新增 `_row_conflicted`；`judge_consistency` 新增 `conflicted` 与分歧条目两分量 |
| 改 | `agent/digestion/sandbox.py` | `diff_judge` 文档字符串：层③门槛量的口径（纯浮点门槛，量的定义归判定器） |
| 改 | `tests/unit/test_judge_llm_runtime.py` | 改口径用例 1 例（原断言按旧口径写死）+ 新增 11 例（口径表 / 层③承重 / legacy 不漂移 / 反向组合披露） |
| 改 | `tests/unit/test_judge_cost_guardrail.py` | 新增 `TestVerdictSemantics` 4 例（反向样本落负例 / 对照组不变 / `conflicted` 计数 / 批内回落不写不实来源） |
| 新增 | `scripts/dev/s1004_judge_verdict_repro.py` | 改前/改后**同输入对比**可复跑脚本（五段：字段层 / 层③门槛 / 阈值边界 / 全链路灰度 / 一致率） |
| 新增 | `docs/zh/CloudPivot_v7.2重构计划/TASK-S10-04_验收报告.md` | 本报告 |

---

## 二、规格 ↔ 实现 逐字段对照表（含证据行号）

### 2.1 规格原文（逐字）

`CloudPivot_v7.2_final_合并归档(智谱审核版).md`：

```
219  4.5 SkillFactory 流水线
223  验收门硬闸：≥20 条回放全过 / 成功率 ≥ 基线×0.98 / p99 ≤ 上游 / 覆盖破坏性分支。
225  Shadow 抽样：每日预算 = min(日均×15%, 50 次)，trace_id 哈希确定性抽样；副作用
     record-and-replay 绝不双写；非确定性目标三层比对（结构 schema 硬性→副作用集合硬性
     →LLM-judge ≥0.85 软性+10% 人工）。
234  ④ 质量不下滑：success_rate(自研) ≥ 上游 × 0.98
```

**关键事实：§4.5 第 225 行只写「LLM-judge ≥0.85 软性」，没有指名 0.85 作用于哪个量**
（"相似度"？"置信度"？）。这是本任务"规格级"的根源，也是 §四 需要 Owner 裁定的部分。

### 2.2 对照表

| # | 契约字段 | 规格要求（出处） | 改前实现 | 改后实现 | 判定 |
|---|---|---|---|---|---|
| 1 | `verdict`（判定结论） | §4.5:225「LLM-judge … 软性」+ §4.5:223「三层比对」= 层③的**通过/不通过结论** | `shadow.py:728-729`（改前）`confidence >= threshold ⇒ pass`，**完全不看模型结论** ⇒ `different + 1.0` 判 `pass` | `shadow.py:749-754`：结论前置 —— `model_verdict == fail ⇒ verdict = fail`（不受 confidence 影响）；否则按 0.85 折算 | ❌ **反向（已修）** |
| 2 | `confidence`（模型自报置信度） | 提示词问的是"结论**与**置信度"（`shadow.py:614-622`）⇒ confidence = 对该结论的把握 | `shadow.py:728`（改前）被**直接当相似度**用于门槛 | 仅作披露 + 与阈值折算 `threshold_verdict`；**不再单独决定 pass/fail** | ❌ 混用（已修） |
| 3 | `score`（层③得分，喂给 `sandbox.diff_judge`） | `diff_judge` 是**纯浮点门槛**：`passed = score >= threshold`（`sandbox.py:1451`） | `shadow.py:1002`（改前）`score = confidence` | `shadow.py:1015-1054`：结论为"不等价" ⇒ `0.0`；其余如实回报测量值（低分不压成 0） | ❌ **反向（已修）** |
| 4 | `model_verdict`（模型措辞归一） | 无规格字段，属实现自加的**如实披露** | `shadow.py:713-715`（改前）归一化并对中文否定词"先否后肯"（`_VERDICT_FALSE` 先扫，`shadow.py:625-629`） | `shadow.py:734`，**不变**（保留） | ✅ |
| 5 | `threshold_verdict`（仅按阈值的折算） | — | 不存在 ⇒ 读者看不到"只按阈值会判什么" | 新增（`shadow.py:749-750`、`:764`），并列存盘与并列披露 | ➕ 新增 |
| 6 | `conflict` | 无规格字段；S9-02 把它当"下游兜底判据"，但**下游只看 verdict 时会读反** | `shadow.py:733`（改前）`model_verdict != verdict` —— 修好后该式恒假 ⇒ 披露通道会**变哑** | `shadow.py:755`：`model_verdict != threshold_verdict`（结论与阈值相左），`note` 明写"**采纳模型结论**" | 🔁 重定义（保披露） |
| 7 | `format` (`structured`/`legacy_score`) | — | `shadow.py:708,724-725`（改前） | `shadow.py:729,746`，**不变** | ✅ |
| 8 | `JUDGE_THRESHOLD = 0.85` | §4.5:225「≥0.85」 | `sandbox.py:73` | **不变**（单点定义未动） | ✅ |
| 9 | 落盘行 `judge_verdicts.jsonl` | S8-04 步骤 4（一致率的数据源） | `judge_runtime.py:817-825`（改前）无 `model_verdict`/`threshold_verdict` | `judge_runtime.py:814-841`：并列新增两字段（additive，旧台账读侧空串） | ➕ 新增 |
| 10 | `JudgeVerdictStore.summary().by_verdict` | — | `judge_runtime.py:877-886`（改前） | `judge_runtime.py:882-893`：增加 `conflicted`；`note` 写明 verdict 口径 | ➕ 新增 |
| 11 | `judge_consistency._manual_verdict_to_judge` | 人工结论 → 可比口径（`uncertain` 不进分母） | `judge_runtime.py:889-896`（改前） | `judge_runtime.py:904-911`，**不变** | ✅ |
| 12 | `judge_consistency.agreement_rate` | S8-04 步骤 4 | `judge_runtime.py:926-944`（改前） | `judge_runtime.py:914-1030`：语义不变，**输入已修正**（judge 侧 verdict 不再反向）；新增 `conflicted` 计数与分歧条目 `model_verdict`/`threshold_verdict` | 🔁 输入修正 |
| 13 | `ShadowRunner._record_judge_verdict` 的来源 | 「judge 判定与人工判定**并列**存档」+ 诚信底线（`judge_kind` 如实） | `shadow.py:2972-2974`（改前）**无条件**读 `last_structured` ⇒ 批内回落时把上一个 LLM 样本的结论写成自己的 | `shadow.py:2991-3023`（`is_llm_kind` 闸在 `:3009-3010`）：仅当本样本 `judge_kind` 属 LLM 族才采信 | ❌ 不实来源（已修） |
| 14 | `ShadowReport.pass_rate` / `negative`（下游） | §4.5:223「成功率 ≥ 基线×0.98」；§4.5.1:234 条件④ | 经层③ `passed` 派生 ⇒ 被 #1/#3 污染 | 同上修正后自动跟随（`shadow.py:2152-2215`） | 🔁 传导修正 |
| 15 | `judge_kind` | M1：真实 LLM-judge 与确定性打分器**可区分** | `shadow.py:156-209` | **不变** | ✅ |
| 16 | `reason` | 结构化三件套之一 | `shadow.py:711-712`（改前） | `shadow.py:732`，**不变** | ✅ |

### 2.3 反向项的证据（改前，原始输出）

```
$ python scripts/dev/s1004_judge_verdict_repro.py       # 在 master(aab04ca4) 的 judge 实现上运行
[A] 字段层：`LLMJudge.score()`
输入                      verdict  model_verdict  conflict  score
different + conf=1.00   pass     fail           True      1.0      ← 模型说"不同且 100% 确定"，却判通过
不等价  + conf=0.95        pass     fail           True      0.95
equivalent + conf=0.95  pass     pass           False     0.95
[B] 层③门槛：`diff_judge` 用真实 `LLMJudge`
different + conf=1.00    layer3.passed=True    score=1.0      reasons=[]   ← 层③也放行
[D] 全链路灰度
different + conf=1.00    judge_kind=llm:probe:gpt-4o-mini  pass_rate=1.0 negative=0 total=6
                         判定存档 by_verdict={'pass': 6}                  ← 负例整批消失
```

**根因（代码行级）**

| 位置 | 代码（改前） | 机理 |
|---|---|---|
| `agent/digestion/shadow.py:728-729` | `verdict = PASS if float(confidence) >= float(threshold) else FAIL` | 把"对该结论的把握"当成了"两臂有多像"；模型给出 `different` 时该式仍可能为真 |
| `agent/digestion/shadow.py:1002` | `"score": float(verdict["confidence"])` | `score` 是层③唯一被消费的量（`sandbox.py:1451`），于是**层③门槛**也被同一处误用放行 |
| `agent/digestion/shadow.py:2945-2947` | `structured.get("verdict") or (…sample.judge_score >= JUDGE_THRESHOLD…)` | 落盘 verdict 复用同一误用结果 ⇒ 一致率统计的分母也被污染 |
| 引入提交 | `S8-04`（`docs/zh/.../TASK-S8-04_LLM-judge接入.md:28`「**阈值**：`confidence ≥ 0.85` 视为软性通过（与 §4.5 一致）」） | S3-02/S3-03 的旧提示词要的是 `{"score": 相似度}`（`shadow.py:569-576`），0.85 当时是**有界相似度**门槛；S8-04 把输出换成 `{verdict, confidence}` 后，**把 confidence 填进了原本属于相似度的槽位** ⇒ 口径漂移 |

同时，仓库自身对层③的描述与实现相矛盾（改前）：
`sandbox.py:1411`「judge ≥0.85 **语义等价比对**」、`shadow.py:1996`「judge **相似度** ≥0.85」——
两处都写"相似度"，而实现用的是 confidence。（改后：`sandbox.py:1411-1419` 写明"纯浮点门槛，
量的定义归判定器"；`shadow.py:2008` 改为"judge 判定通过（**结论为等价 ∧ 置信度 ≥0.85**）"。）

---

## 三、消费方清单（改口径会影响谁）

判定依据：全仓 grep `verdict` / `judge_verdicts` / `verdict_store` / `by_verdict` /
`judge_consistency` / `last_structured` / `pass_rate` / `judge_kind`（范围 `agent/`、`tests/`、
`scripts/`、`yunshu-ui/src/`、`plugins/`、`docs/zh/`）。

**A 类：直接消费 judge `verdict`（本次同步）**

| 消费方 | 位置 | 受影响方式 |
|---|---|---|
| judge 判定存档 | `agent/digestion/shadow.py::ShadowRunner._record_judge_verdict:2991-3023` | 落盘 `verdict` 由反向改为结论前置；新增 `model_verdict`/`threshold_verdict` |
| 存档汇总 | `agent/digestion/judge_runtime.py::JudgeVerdictStore.summary:882-893` | `by_verdict` 计数由 `{pass}` 变 `{fail}`（反向样本）；新增 `conflicted` |
| 一致率统计 | `agent/digestion/judge_runtime.py::judge_consistency:914-1030` | `agreement_rate` / `disagreements` 的**输入**被修正；新增 `conflicted` 与两分量 |
| judge 运行时对外字典 | `agent/digestion/judge_runtime.py::JudgeRuntime.to_dict:1080-1085`（`payload["verdict_store"]`） | 自动带上 `conflicted`（additive） |
| 凭证自检 CLI | `scripts/verify_judge_real_credentials.py:136`（`judge.last_structured`） | 打印的结构化判定随口径变（`verdict` 语义变、新增两字段） |
| 单测 | `tests/unit/test_judge_llm_runtime.py`、`tests/unit/test_judge_cost_guardrail.py` | 已同步（§六） |

**B 类：消费层③得分 / 灰度结果（本次**不需要**改代码，但读数会变）**

| 消费方 | 位置 | 受影响方式 |
|---|---|---|
| 层③门槛 | `agent/digestion/sandbox.py::diff_judge:1451` | 只认浮点，**代码未动**；喂进去的量在"结论为不等价"时变成 0.0 ⇒ 由放行改拦截 |
| 灰度报告 | `agent/digestion/shadow.py::ShadowReport.pass_rate/negative/judge_score:2180-2215` | 反向样本由 `pass`/0 变 `fail`/N；`judge_score` 由 confidence 变 0.0（结论为不等价时） |
| 劣化判定 | `agent/digestion/shadow.py::assess_degradation:2085-2135` | 负例回来后才会触发（**这正是修复的目的**） |
| 内化条件④ | `agent/digestion/internalize.py:1132-1138`（`candidate = shadow_report.pass_rate`） | 候选成功率读数修正 |
| 探活 | `agent/digestion/probe.py:1012`（`report.pass_rate = replay.pass_rate`） | 同上 |
| 面板卡片 | `agent/ui_panels/data.py::_shadow_card:275-308`；`yunshu-ui/src/pages/hub/governance/index.tsx:285,299` | 只展示 `pass_rate`/`judge_kind`，**契约未变**、数值随判定变 |
| 真实链路脚本 | `scripts/s705_real_digestion_chain.py:313`、`scripts/demo_s3_03_internalize.py:389` | 打印口径不变，数值随判定变 |
| 门面 | `agent/digestion/shadow.py::shadow_quality` | 透传，无需改 |

**C 类：同名但**不同域**的 `verdict`（本次**不涉及**，仅列明以免误改）**

`agent/digestion/probe.py`（探活 `ok/degraded/no_cases/error`）、`agent/digestion/stage.py`
（迁移 `applied/illegal_transition/…`）、`agent/digestion/internalize.py`（`promote/veto_blocked/…`）、
`agent/digestion/gate.py`（`passport_granted/drifted/probe_ok`）、`agent/skills_mgmt`（`review_verdict`）、
`agent/guardrails/*`、`agent/verification/output_validator.py`、`agent/eval/*`、
`agent/self_healing/*`、`agent/knowledge/*`、`yunshu-ui` 的 `manual_review.by_verdict`（**人工**抽检台账，
与 judge 存档是**两本账**，`judge_runtime.py:792-798` 明写不互写）。

---

## 四、口径裁定与变更点声明

### 4.1 为什么需要裁定

§4.5:225 未指名 0.85 作用于哪个量，两种读法都"有道理"：

| 读法 | 定义 | 与 §4.5 文字的关系 | 与仓库既有文档的关系 |
|---|---|---|---|
| **A. 0.85 = confidence**（Owner 选定） | `pass ⟺ 模型结论为等价 ∧ confidence ≥ 0.85` | 字面"≥0.85"保留在 confidence 上（S8-04 的读法） | 与 `CompareVerdict`/`diff_judge` 文档写"相似度"**不一致** |
| **C. 0.85 = 相似度** | 提示词改求 `similarity`；`pass ⟺ similarity ≥ 0.85`；confidence 仅披露 | 与"三层**比对**"的语境和"≥0.85"最贴 | 与既有文档一致，但**改变模型输出契约** |

### 4.2 但有一件事**两种读法下都是错的**（这才是"实现写反了"）

无论 0.85 指什么，**层③都不能在模型明确判"不等价"时判通过**——
"我 100% 确定它们不同" ⇒ "软性通过"不是任何读法的合理推论，
且与本层"检测行为漂移"的功能直接相反（§4.5:228 漂移重探、§3.3:136-138 转移条件）。

### 4.3 Owner 裁定（本次会话内确认）

> **口径 A**：结论前置 + 0.85 仍作用于 confidence。
> —— 只把"模型说不同却高置信"这一类由 `pass` 改成 `fail`（**纯收紧**），
> 不改提示词契约、不改 §4.5 文字、不重新解释 0.85。

### 4.4 变更点声明（**改变既有语义**，逐条列明）

| # | 变更点 | 改前 | 改后 | 影响面 |
|---|---|---|---|---|
| 1 | 模型结论为"不等价"时的 `verdict` | `confidence≥0.85 ⇒ pass` | **一律 `fail`** | A 类全部 + B 类全部 |
| 2 | 结论为"不等价"时的层③ `score` | `= confidence`（可 ≥0.85） | `= 0.0` | 层③放行→拦截；`judge_score` 列数值变 |
| 3 | `conflict` 的定义 | `model_verdict != verdict`（修好后恒假） | `model_verdict != threshold_verdict` | 仅 `shadow.py` 内部与用例（无外部消费者） |
| 4 | 存档/统计新增字段 | — | `model_verdict`/`threshold_verdict`/`conflicted` | additive；旧台账读侧空串 |
| 5 | 批内回落样本的存档来源 | 读 LLM 的 `last_structured`（**会串上一个样本的结论**） | 只在本样本属 LLM 族时读 | 一致率统计不再混入不实来源 |

**未变更（逐字保持）**：`JUDGE_THRESHOLD=0.85`；结论为"等价"时 0.84/0.85/0.86 的边界；
旧式只给分数（`{"score": …}`）时的纯阈值折算；`format` 取值与语义；
`judge_kind` 标注体系；`REVIEW_VERDICTS` 词表；`_manual_verdict_to_judge`；
`uncertain` 不进一致率分母；小样本（<20）只披露不结论。

---

## 五、改前 / 改后 同输入对比（原始输出）

```
$ python scripts/dev/s1004_judge_verdict_repro.py
```

| 输入（judge 回复） | 改前 verdict | 改后 verdict | 改前 层③ | 改后 层③ | 改前 score | 改后 score |
|---|---|---|---|---|---|---|
| `different` + conf=1.00 | **pass** | **fail** | **True** | **False** | 1.0 | 0.0 |
| `不等价` + conf=0.95 | **pass** | **fail** | **True** | **False** | 0.95 | 0.0 |
| `different` + conf=0.20 | fail | fail | False | False | 0.2 | 0.0 |
| `equivalent` + conf=0.95 | pass | pass | True | True | 0.95 | 0.95 |
| `equivalent` + conf=0.50 | fail | fail | False | False | 0.5 | 0.5 |
| `{"score":0.90}`（旧式） | pass | pass | True | True | 0.9 | 0.9 |

全链路灰度（`ShadowRunner`，24 条用例集抽 6 条样本，`judge_kind=llm:probe:gpt-4o-mini`）：

```
                                改前                                    改后
judge 回复 different+1.00  pass_rate=1.0 negative=0  by_verdict={pass:6}   pass_rate=0.0 negative=6  by_verdict={fail:6}
judge 回复 equivalent+0.95 pass_rate=1.0 negative=0  by_verdict={pass:6}   pass_rate=1.0 negative=0  by_verdict={pass:6}
一致率（judge vs 人工真值） agreement_rate=0.5                             agreement_rate=1.0  conflicted=1
```

**"被检查的对象没从报告里消失"核验**：改后 `len(report.samples)=6`、`verdicts.summary()["stored"]=6`
（与改前同为 6）—— 反向样本是**落成负例**，不是被过滤掉。

---

## 六、用例更新与承重反证

**改口径的既有断言 1 处**（原断言按旧口径写死，按**意图**改并补正向断言收紧）：

| 用例 | 改前断言 | 改后断言 |
|---|---|---|
| `test_judge_llm_runtime.py::TestStructuredVerdict::test_model_verdict_word_is_disclosed_when_it_conflicts` → 重命名 `test_model_conclusion_wins_over_high_confidence` | `result["verdict"] == "pass"` | `verdict == "fail"` + `model_verdict == "fail"` + `threshold_verdict == "pass"` + `conflict is True` + `score == 0.0` + `note` 含"采纳模型结论"（6 条收紧断言） |

**新增 15 例**（全部机器可读）：

| 文件 | 用例 | 锁住什么 |
|---|---|---|
| `test_judge_llm_runtime.py` | `test_low_confidence_on_equivalent_conclusion_is_disclosed_and_fails` | 反向组合（结论等价但把握不足）也如实披露 |
| 〃 | `test_judge_verdict_conclusion_first_table`（6 参数） | 口径表逐行；并断言 **`passed ⟺ score ≥ 0.85` 与 verdict 自洽**（不得出现分裂读法） |
| 〃 | `test_judge_verdict_legacy_score_still_folds_on_threshold` | 旧式只给分数时**纯阈值折算不漂移**；低分如实回报不被压成 0 |
| 〃 | `test_layer_three_gate_follows_the_judge_conclusion`（5 参数） | **承重**：层③真实放行/拦截（只看浮点的那一层） |
| `test_judge_cost_guardrail.py` | `test_inverted_judge_verdict_becomes_a_negative_sample` | S9-02 原始反向样本 ⇒ `pass_rate=0/negative=total`、存档 `{fail:total}`、**样本与存档条数仍为 total** |
| 〃 | `test_equivalent_conclusion_keeps_the_old_pass_behaviour` | 对照组：结论为等价时与原口径逐字一致 |
| 〃 | `test_conflicted_rows_are_counted_in_store_and_consistency` | `conflicted` 在存档汇总与一致率报告里都数得出；分歧条目带两分量 |
| 〃 | `test_batch_fallback_does_not_store_a_stale_llm_verdict` | 批内超预算回落后**不得**把上一个 LLM 样本的结论写成自己的 |

**承重反证（把三个源文件回退到 master 版本、只留用例）**：

```
$ git checkout aab04ca4 -- agent/digestion/{shadow,judge_runtime,sandbox}.py
$ python -m pytest tests/unit/test_judge_cost_guardrail.py::TestVerdictSemantics \
                 tests/unit/test_judge_llm_runtime.py::TestStructuredVerdict -q -p no:randomly
FAILED ...test_inverted_judge_verdict_becomes_a_negative_sample
FAILED ...test_equivalent_conclusion_keeps_the_old_pass_behaviour
FAILED ...test_conflicted_rows_are_counted_in_store_and_consistency
FAILED ...test_batch_fallback_does_not_store_a_stale_llm_verdict
FAILED ...test_model_conclusion_wins_over_high_confidence
FAILED ...test_low_confidence_on_equivalent_conclusion_is_disclosed_and_fails
FAILED ...test_judge_verdict_conclusion_first_table[different-1.0-fail]
FAILED ...test_judge_verdict_conclusion_first_table[不等价-0.95-fail]
FAILED ...test_layer_three_gate_follows_the_judge_conclusion[different-1.0-False]
FAILED ...test_layer_three_gate_follows_the_judge_conclusion[different-0.99-False]
FAILED ...test_layer_three_gate_follows_the_judge_conclusion[不等价-0.95-False]
======== 11 failed, 33 passed in 4.51s ========
```

（回退态下 `layer3.passed=True, score=1.0` —— 与 §2.3 现场一致；随后已 `git checkout HEAD --` 复原并复跑全绿。）

---

## 七、质量证据（门禁四条 + 邻接回归）

均在**合并 master（`aab04ca4`）之后**的 `38c0b760` 上复跑：

```
$ python -m pytest tests/unit -q -p no:randomly -k "judge or verdict"
276 passed, 1 skipped, 18112 deselected, 7 warnings in 41.52s
```

> ⚠️ 任务书写的 `-k "judge verdict"` 在本机 pytest 9.1.1 上**不是合法表达式**，原始输出为：
> `ERROR: Wrong expression passed to '-k': judge verdict: at column 7: expected end of input; got identifier`
> ⇒ 按等价意图改用 `-k "judge or verdict"`（并额外跑邻接套件，见下）。

```
$ python -m pytest tests/unit -q -p no:randomly \
    -k "digestion or shadow or judge or internalize or probe or verdict or panel or sandbox or takeover or worktree_env"
1663 passed, 2 skipped, 16724 deselected in 107.85s

$ python -m pytest tests/unit/test_s10_02_judge_wiring.py -q -p no:randomly     # 联合验证（S10-02 地盘）
20 passed in 3.28s

$ python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH
  HIGH: 0 处 / MEDIUM: 0 处 / LOW: 0 处 / 总计: 0 处
$ python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH
  HIGH: 0 处 / MEDIUM: 0 处 / LOW: 0 处 / 总计: 0 处

$ python -m mypy agent/digestion/shadow.py agent/digestion/judge_runtime.py agent/digestion/sandbox.py
改动模块错误 0（过滤 `digestion.(shadow|judge_runtime|sandbox).py` 计数 = 0）
总错误 546 项 / 92 文件 —— 与回退到同一基线后复跑**逐数相同**（0 新错）

$ lint-imports --config .importlinter
Contracts: 2 kept, 0 broken.

$ git status --short        # 提交后产物漂移
（空）
```

其它：`tests/integration -k "digestion or shadow or judge or internalize or panel"` → `3 passed`（该
关键词在 integration 层只有 3 例）。

---

## 八、遗留（如实登记、带归属）

| # | 遗留 | 归属 | 说明 |
|---|---|---|---|
| L1 | **零缺口硬守卫在基线即为红**：`CP_ENV_FILE` 代码读到但 `agent/settings/registry.py` 未登记（`scripts/scan_settings.py --check` → `缺口（未注册）: 1`，`agent/env_config_manager.py:101`） | **S10-06 / 批次出口**（引入提交 = `bbc5d821`，即任务书声明的 master） | **非本任务引入**：把本任务的全部改动 `git stash` 掉后，在 pristine 基线上复跑 `tests/unit/test_settings_registry.py::TestMechanicalZeroGap` 仍 **2 failed**（守卫红与改动无关）；`bbc5d821` 的 `registry.py` 里同样没有该名字，`master`(`aab04ca4`) 与各会话分支（`s10-02/s10-06/s1003/s1005`）**均未登记**。本任务**未改** `agent/settings/registry.py`：① 非本任务引入；② S10-06 正活跃于"worktree 自动供给 `.env`"这一**同一域**，重复登记/合并冲突风险高于收益。**建议**：由 S10-06 或批次出口补一条 `_c("CP_ENV_FILE", …, validator=Validator("path"))`（路径项 ⇒ `_c`），使该守卫恢复常绿。 |
| L2 | **未与 S10-02 会话本人联合验证** | S10-02 / 批次出口 | 已做的是"与其**已合并提交**在同一基线上联合复跑"：master 已含 S10-02（`agent/digestion/{shadow,judge_runtime}.py` 各 +57/+36 行），本分支已 `git merge master`（零冲突，`38c0b760`），S10-02 自带套件 `test_s10_02_judge_wiring.py` **20 passed**、邻接 1663 passed。**未做**的是两个会话的交叉评审与"同一真机场景下 S10-02 注入 + S10-04 口径"的端到端真跑。 |
| L3 | **真实凭证下未验证** | 沿用 S8-04 W1-L3 / 运营期 | 本机无可用模型凭证（`../真用前置_模型凭证核查_20260913.md`），本任务全部判定均为**桩通道**（`invoke=` 注入）。修复本身不依赖凭证，但"真实模型在 `different` 时是否确实给出高 confidence"只有真凭证才能测。 |
| L4 | §4.5 的 0.85 究竟作用于"相似度"还是"置信度"**仍属规格未言明** | **Owner** | 本次按裁定 **A** 落地（0.85 留在 confidence 上）。若后续改判 **C**（0.85 = 相似度），需改 `_JUDGE_STRUCTURED_PROMPT` 求 `similarity` 字段并重跑本报告 §五/§六 全部用例；改动点集中在 `shadow.py::parse_judge_verdict` + 提示词，用例已参数化（`test_judge_verdict_conclusion_first_table`）。**未改规格文档**。 |
| L5 | LLM 路径的 `judge_score`（等价性得分）与确定性打分器的 `judge_score`（有界相似度）**不是同一量** | S10-04 自留 / 运营期 | `diff_judge` 只做浮点门槛，两者可混跑但**分布不可直接比较**；同一批样本内回落时（`judge_kind` 逐样本不同）尤其如此。已在文档字符串写明，未做归一（归一需要新规格）。 |
| L6 | judge 真实判定样本仍只有 3 条，一致率无结论 | 运营期（S9-02 W1-L2） | `CONSISTENCY_MIN_SAMPLES=20` 口径不变；本次只把统计**输入**修正，未新增真实样本。 |
| L7 | 新增字段未在 UI 面板透出 | 运营期 / 面板域 | `conflicted` 目前只在存档汇总、一致率报告与 `JudgeRuntime.to_dict()` 里可读；`agent/ui_panels/data.py::_shadow_card` 仍是 `passed/negative/pass_rate/judge_kind`。未扩面板（跨域）。 |
| L8 | **运行期生效需重启服务** | 批次出口 / 值班 | 在跑的 `127.0.0.1:5678` 服务（PID **9092**，`python.exe`，启动于 2026-09-13 19:07:33）加载的是**改动前**的 `agent/**`（与 S10-03 §遗留 R6 同源）；本任务**未**重启该服务（多会话共用，避免打断他人）。影响有限：judge 真实通道**默认关闭**（`CP_DIGESTION_JUDGE_ENABLED=false` ⇒ `judge_kind=deterministic_local(disabled)`），故口径变更在重启并显式开启前**不改变线上判定**。 |

---

## 九、复现入口

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
python scripts/dev/s1004_judge_verdict_repro.py                    # 五段同输入对比
python -m pytest tests/unit -q -p no:randomly -k "judge or verdict"
python -m pytest tests/unit/test_judge_cost_guardrail.py::TestVerdictSemantics -q
```

---

## 十、提交与双远端 SHA

| 项 | SHA |
|---|---|
| 基线（开工时 master） | `717d530e`（任务书写的是 `bbc5d821`；两者对本任务判据无差异，见 §八 L1） |
| 本任务代码提交（`s10-04/main`） | `1e7632a8` |
| 合并 master（含 S10-02 / S10-06，零冲突） | `38c0b760` |
| 交付报告 + 对照脚本提交 | `8fe5a469` |
| SHA 定稿（本行回填） | `__FINAL_SHA__` |
| **双远端终态** | `origin/master` = `gitee/master` = `8fe5a469f7e9702b8f453c032f383d58e500a1b9` |

合并方式：`git -C <主工作区> merge s10-04/main --no-edit` → **Fast-forward**（`aab04ca4..8fe5a469`），
无合并提交、无冲突、无产物漂移（`git status --short agent/digestion tests/unit scripts/dev docs/...` 为空）。
