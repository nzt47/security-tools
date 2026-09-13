# TASK-S10-01 验收报告 — 工作流学习层准入与存量脏工作流退役

- 日期：2026-09-13
- 基线：`master` = `9c736dad`（父提交 `bbc5d821`）
- 交付提交：`66a67888`（feat(S10-01)，分支 `s10/main`；双远端 SHA 见文末）
- 未触碰：`agent/digestion/`、`agent/orchestrator/orchestrator.py`（他人地盘）

---

## 一、交付物

| 类型 | 文件 | 说明 |
|---|---|---|
| 新增 | `agent/workflow_learning/admission.py` | 准入判定**单一源**：`MIN_STEPS=2` / `MIN_TRIGGER_CHARS=2` / `MIN_CROSS_SESSION_SUPPORT=2` + 机器可读拒绝码 |
| 新增 | `agent/workflow_learning/retirement.py` | 存量退役：只标记不删除 + 追加式台账 |
| 新增 | `scripts/retire_dirty_workflows.py` | 退役 CLI（默认 dry-run，`--apply` 写入） |
| 新增 | `scripts/audit_wf_admission.py` | 准入读数 + 全库匹配面实测（可复算） |
| 新增 | `tests/unit/test_workflow_learning_admission.py` | 31 个机器可读用例（含回归锚） |
| 新增 | `data/learned_workflows_retired.jsonl` | 退役审计台账（4 行，append-only） |
| 改 | `agent/workflow_learning/matcher.py` | 准入否决不入索引；match 侧复核；单字触发词不进索引特征；拒绝计数 |
| 改 | `agent/workflow_learning/learner.py` | 触发词只收有区分度的；不达标定档 `draft` + 拒绝码写进 description |
| 改 | `agent/workflow_learning/skill_converter.py` | 结构性门控（`force` 不可越过）；统计门控抽成 `quality_gate_reasons` |
| 改 | `agent/workflow_learning/service.py` | 自动升格门槛（结构+统计+跨会话样本数）；`admission_report()`；`retire_dirty_workflows()`；health 读数 |
| 改 | `agent/workflow_learning/repository.py` | `count_distinct_sessions()` / `signatures()`（跨会话样本数，不新增持久化字段） |
| 改 | `agent/workflow_learning/__init__.py` | 模块文档与导出（v1.2.0） |
| 改 | `data/learned_workflows.json` | 4 条 active→archived（条目未删） |
| 改 | `scripts/simulate_workflow_closed_loop.py` | 重新基线 + 新增 R9 准入隔离轮 |
| 改 | `tests/unit/{test_workflow_to_skill,test_matcher_concurrency,test_routes_workflow_learning}.py` | fixture 适配新前置条件（未放宽任何断言，另补 3 条收紧断言） |

---

## 二、现象固化（先测后改）

### 2.1 存量条数与命中条件（可复算）

```
$ python scripts/retire_dirty_workflows.py            # 试运行
条目总数: 5
命中脏条件(结构性否决)条数: 4
  - python-eb17ed25 | steps=3 | triggers=['统','计','项','目','里'] | NO_DISCRIMINATIVE_TRIGGER
  - wf-c7499f27     | steps=1 | triggers=['列','出','当','前','工'] | STEPS_TOO_FEW + NO_DISCRIMINATIVE_TRIGGER
  - wf-f19dc52c     | steps=1 | triggers=['列','出','当','前','工'] | STEPS_TOO_FEW + NO_DISCRIMINATIVE_TRIGGER | skill=wf-f19dc52c-skill
  - zip-d2968c59    | steps=2 | triggers=['包','代','码','仓','库'] | NO_DISCRIMINATIVE_TRIGGER | skill=zip-d2968c59-skill
```

脏条件（机器可判定，判定源 `agent/workflow_learning/admission.py`）：
1. `len(steps) < MIN_STEPS(2)` —— 1 步 == 单次工具调用；
2. `effective_trigger_patterns(trigger_patterns) == []` —— 全部触发词无区分度
   （有效字符 < 2；正则元字符不计）。

### 2.2 "大面积误匹配"是否成立：**部分复现**（如实登记，不夸大）

初测（逐条单独注册，N=1，16 条探针）**未复现**：无关请求相似度 ≈0.001，0 命中；
任务书点名的 `1+1 等于几` 亦为 0 命中。原因：索引文本含整句
`source_user_input`，且 `_idf` 对 doc 中不存在的 query 词给最大权重
（df=0 → log(2/1)=0.693）而给共有词极小权重（N=1、df=1 → 下界 0.001），
只有"问句词集≈文档词集"才可能高分——**长问句反而被压到 0**。

改测（**全库注册 N=5**，21 条探针：4 条来源输入 + 12 条通用 + 5 条**短探针**），
用 HEAD 代码 + HEAD 数据实测，复现了跨任务命中：

```
== 退役/修复前（HEAD 代码 + HEAD 数据），探针=21 ==
命中查询数: 9   候选对: 12
   HIT '帮我列出当前工作目录下的文件'          -> wf-c7499f27   combined=0.8158
   HIT '帮我列出当前工作目录下的文件'          -> wf-f19dc52c   combined=0.4108   # 同一任务的重复资产
   HIT '把代码仓库打包成 zip 压缩包'           -> zip-d2968c59  combined=0.6459
   HIT '统计项目里所有 Python 文件的行数并保存报告' -> python-eb17ed25 combined=0.4823
   HIT '读取 JSON 配置文件并转换为 YAML 格式'   -> json-30a189b6  combined=0.3626
   HIT '请列出当前的状态'                      -> wf-c7499f27   combined=0.3072   # 跨任务
   HIT '请列出当前的状态'                      -> wf-f19dc52c   combined=0.1547   # 跨任务
   HIT '列出文件'                              -> wf-c7499f27   combined=0.5679   # 跨任务（≥0.3）
   HIT '列出文件'                              -> wf-f19dc52c   combined=0.2859   # 跨任务
   HIT '统计一下'                              -> python-eb17ed25 combined=0.2129  # 跨任务
   HIT '打包代码'                              -> zip-d2968c59  combined=0.5244   # 跨任务（≥0.3）
   HIT '读取配置'                              -> json-30a189b6  combined=0.2967   # 跨任务
```

结论（可复算）：**跨任务候选对 6 个**，其中 3 个 ≥ 0.3（executor 默认
`min_score=0.3`）→ 会被拦截层直接执行且跳过 LLM。短问句（"列出文件"/"打包代码"）
是实际热区；`1+1 等于几` 类长无关问句**不命中**——故"任何含出/当/前的输入都可能命中"
在**当前索引构造下不成立**，本任务据此把触发词规则定位为**结构性前置条件**
（阈值取严），而不是宣称实测误匹配率。

### 2.3 回归锚（修复前必须失败）

```
$ git show 9c736dad:agent/workflow_learning/matcher.py > agent/workflow_learning/_prefix_matcher_tmp.py
$ python -c "... pre.WorkflowMatcher().register(wf-f19dc52c 存量条目) ..."
pre-fix register() -> None
pre-fix match('帮我列出当前工作目录下的文件') -> [('wf-f19dc52c', 0.3938)]
ANCHOR_FAILS_BEFORE_FIX=True
```

⇒ 修复前该 1 步/单字触发词条目**是匹配候选**（combined 0.3938 ≥ 0.3），
用例 `tests/unit/test_workflow_learning_admission.py::TestSingleCharTriggerNeverCandidate`
在修复前失败（断言 `register() is False` 与 `match() == []`）。

### 2.4 已完成的历史转换（证据链）

| 证据 | 内容 |
|---|---|
| `data/learned_workflows.json` | `wf-f19dc52c`：`steps=[list_directory]`、`trigger_patterns=["列","出","当","前","工"]`、`source_session_id=sess_20260907_220445_39c3ebf7`、`converted_to_skill_id=wf-f19dc52c-skill` |
| 运行期技能库 `data/skills_mgmt.json`（gitignore） | `wf-f19dc52c-skill` 的 `来源` 块记录 `success_count: 5 / confidence: 0.75`，正文"触发条件"列 `列/出/当/前/工` |
| 折算 | `record_execution` 步长公式 `0.1·e^{-(n-1)/5}` 从 0.4 起算：0.5→0.582→0.649→0.704→**0.749**，与技能快照的 0.75 吻合 ⇒ **转换时该条目确实"跑满"了统计门控**（`success_count=5 ≥ 5`、`conf=0.749 ≥ 0.7`） |

⇒ 历史路径**不是**"force 硬闯"单一原因：统计门控本身可被"重复执行 1 步工作流"
刷过（这也是任务书提示"不得用脚本造任务来刷数据"的原因）。触发路径（人工 force
还是 300s 周期的自动升格）**运行期无审计记录，无法判定**，故两条路径**都**关闭。

---

## 三、根因（代码行级，基线 `9c736dad`）

1. **触发词零区分度**：`agent/workflow_learning/learner.py:37`
   `_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]+|[\u4e00-\u9fff]")`
   对中文**按字**切分；`_extract_keywords`（`:40`，词频全为 1 时按插入序取
   top-5）⇒ `learner.py:111` 产出的 `triggers` 实为"用户那句话的前 5 个字"，
   `learner.py:121` 直接写成 `trigger_patterns`。无任何长度/区分度过滤。
2. **优先级倒挂**：`generator.py:102-103` `if n_steps <= 3: base += 20`
   ⇒ 1 步工作流拿到 `priority=80`（最高档），信息量最小者匹配权重最强
   （存量 `wf-f19dc52c` priority=80 实测）。
3. **匹配无准入门**：`matcher.py:144 def register` 无条件 `:163 self._index.add(...)`；
   `match` 只按 `similarity/confidence/enabled` 过滤（`:199`）⇒ 脏条目天然进候选。
4. **转换门控只看统计量**：`skill_converter.py:163 _check_quality_gate` 仅
   `status/enabled/success_count(≥5)/confidence(≥0.7)/priority(≥50)`；且
   `:120 if not force:` ⇒ `force=True` 连这几条也一并绕过（无任何结构性下限）。
5. **自动升格同款门控**：`service.py:260 list_convertible_workflows` 内联同一组
   统计条件（`:270-272`），由 `agent/orchestrator/lifecycle_manager.py:477`
   （默认 300s）→ `:773` → `:793` → `:799` 周期调用 ⇒ 达统计量即自动沉淀为 Skill。
6. **学习侧无定档**：`learner.learn` 一律产出 `status=ACTIVE`（`models.py` 默认），
   1 步/单字触发词条目没有任何"草稿态"中间态。

---

## 四、实现（为什么是这些阈值）

| 阈值 | 取值 | 依据（写进 `admission.py` docstring） |
|---|---|---|
| `MIN_STEPS` | **2** | 1 步 == 单次工具调用，无编排价值；且 `generator` 给 ≤3 步 +20 使它拿到最高优先级（倒挂）；统计门控可被"重复执行"刷过（§2.4 实测 5 次即 0.749），**结构刷不过去** |
| `MIN_TRIGGER_CHARS` | **2**（有效字符，正则元字符不计） | 中文按字切分下，单字触发词 == 一句话的头几个字，不携带区分信息；2 字是中文最小成词单位；`"*"`/`"a*"` 判为无区分度（**拿不准取更严**） |
| `MIN_CROSS_SESSION_SUPPORT` | **2** | "单轮来源"的机器可判定形式：同一 `task_signature` 只在 1 个会话出现 = 1 个样本，无从判断是否复现；自动**沉淀资产**要求跨会话复现，人工 `force` 不受此限 |
| `force` 语义 | 只越过统计门控 | 结构性不达标不是"还没养熟"，而是"根本不是资产"——没有任何人工判断能让一次 `list_directory` 变成可复用工作流 |

落地要点：
- 单字触发词一律**丢弃**（保留在 workflow 里的原样不动，只不进 `trigger_patterns`/索引）；
- 不达标条目 `status=draft`，仍可按 ID 人工执行（`execute_by_id` 不经匹配器）；
- 存量退役**只标记不删除**：`status=archived`，`converted_to_skill_id` 等历史原样保留；
- 退役只走**显式**入口（CLI/服务方法），**不在服务构造时写盘**——开发中实测
  "构造全局服务 → 改写真实 `data/learned_workflows.json`"确实发生过一次
  （pytest 触发），该风险与 P0「测试不再覆盖真实 .env」同类，故加反回归用例
  `test_service_construction_does_not_touch_repo`。

---

## 五、验收逐条

### ① 准入门槛有机器可读用例

```
$ python -m pytest tests/unit/test_workflow_learning_admission.py -q -p no:randomly
31 passed in 1.87s
```

覆盖：阈值即契约、触发词区分度参数化（9 例）、单步/单字拒绝码、
`force=True` 结构性拒绝、单会话不得自动升格（跨会话后放行）、
单步交互→草稿、草稿仍可执行、混入单字只丢单字不整条否决、
存量仓库"永不进候选 + 达标条目仍在池中"不变量。

### ② 单字/单轮来源的工作流不再能自动转 skill

```
$ python -m pytest tests/unit -q -k "workflow_learning" -p no:randomly
74 passed, 1 skipped（既有 skip：test_query_pattern.py 前提已失效）
```
其中新增的路由级用例：
`test_structural_gate_returns_400_even_with_force`（force 也 400）、
`test_dirty_workflow_not_listed_as_convertible`（自动升格候选为空）。

转换器/服务侧断言：`force=True` 抛
`WorkflowConvertError(code=QUALITY_GATE_FAILED, codes=['STEPS_TOO_FEW'])`。

### ③ 存量退役有清单与前后对比数字（可复算）

```
$ python scripts/retire_dirty_workflows.py --apply
条目总数: 5
命中脏条件(结构性否决)条数: 4   → 退役后仍为脏且未归档的条数: 0
本轮实际迁移(active→archived): 4   台账追加行数: 4
$ git show 66a67888:data/learned_workflows.json | python -c "...统计 status..."
  python-eb17ed25 | archived | converted_to_skill_id=-
  zip-d2968c59    | archived | converted_to_skill_id=zip-d2968c59-skill   ← 历史保留
  json-30a189b6   | active   | converted_to_skill_id=-
  wf-f19dc52c     | archived | converted_to_skill_id=wf-f19dc52c-skill    ← 历史保留
  wf-c7499f27     | archived | converted_to_skill_id=-
 条目总数 = 5                                  ← 未删除
$ (Get-Content data/learned_workflows_retired.jsonl | Measure-Object -Line).Lines
4
```

退役前后（同一判定源 `scripts/audit_wf_admission.py` 复算）：

| 指标 | 退役前（HEAD 数据） | 退役后 |
|---|---|---|
| 条目总数 | 5 | **5**（未删） |
| 匹配候选 | 1（脏条目被代码门槛挡住） | 1 |
| 单字触发词条目 | 5 | 5（历史如实保留） |
| 拒绝码 `STEPS_TOO_FEW` | 2 | 2 |
| 拒绝码 `NOT_ACTIVE` | 0 | 4（新增：退役标记生效） |
| status=archived | 0 | 4 |
| 可自动升格 | 0 | 0 |

匹配面（全库注册，21 探针，同一电池）：

| | 命中查询数 | 候选对 | 跨任务候选对 | ≥0.3 的跨任务对 |
|---|---|---|---|---|
| 修复前（HEAD 代码+数据） | 9 | 12 | 6 | 3（0.568 / 0.524 / 0.307） |
| 修复后（本次提交） | 2 | 2 | **0** | **0** |

修复后残留的 2 个命中均来自**达标**条目 `json-30a189b6`（其来源请求 0.3472、
"读取配置" 0.2309）。

关于台账与 pre-commit `clean-runtime-noise`：**未靠钩子放行**。
`status` 不在 `ACCOUNTING_PATHS` 的统计字段集内，属"真实内容改动"，钩子打印
`还原 1 条统计漂移，保留 4 条真实改动`；被还原的仅是 `updated_at`（统计字段），
提交对象里 `status=archived` 四处在位（已用 `git show 66a67888:data/...` 复核）。

### ④ 邻接回归全过

```
$ python -m pytest tests/unit -q -k "workflow_learning" -p no:randomly
74 passed, 1 skipped（既有）
$ python -m pytest tests/unit/test_workflow_to_skill.py tests/unit/test_workflow_mode.py \
    tests/unit/test_workflow_hybrid.py tests/unit/test_matcher_concurrency.py \
    tests/integration/test_skills_workflow_flow.py tests/integration/test_skill_converter_integration.py -q -p no:randomly
80 passed
$ python -m pytest tests/unit -q -p no:randomly -k "workflow or matcher or admission or convert"
546 passed, 1 skipped, 2 xfailed（均为既有 skip/xfail）
$ python scripts/simulate_workflow_closed_loop.py     # 全绿（含 R9 准入隔离轮）
```

**正常工作流不受影响**（正向回归，非空断言）：
- `tests/unit/test_workflow_learning_admission.py::TestLearnerAdmission::test_admitted_interaction_stays_active_and_matchable`
  —— 2 步 + 有区分度触发词 → `active`，原样复述即 `search` 命中、`try_execute` 成功；
- 存量 `json-30a189b6` 退役后仍 `active` 且仍命中其来源请求（0.3472）；
- `test_committed_stock_repo_never_yields_dirty_candidates` 显式断言
  `admitted_ids` 非空（防"对象从报告里消失"式假绿）。

### ⑤ 未动他人地盘

```
$ git diff --name-only 9c736dad 66a67888 | Select-String "digestion|orchestrator/orchestrator.py"
（无输出）
```

---

## 六、门禁四条

| 门禁 | 命令 | 结果 |
|---|---|---|
| 相关套件 | 见 §五①②④ | 74 / 80 / 546 全过 |
| 参数冲突扫描 | `python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH` | **HIGH 0 处**（总计 0） |
| 参数冲突扫描 | `python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH` | **HIGH 0 处**（总计 0） |
| mypy（改动模块） | `python -m mypy agent/workflow_learning/{admission,retirement,matcher,learner,generator,repository,service,skill_converter}.py` | 与 `9c736dad` 基线**诊断集完全一致**（各 12 条，新增 0；基线 12 条为既有 pydantic call-arg / no-any-return 等） |
| 分层契约 | `lint-imports --config .importlinter` | **2 kept, 0 broken** |
| 产物漂移 | `git status --porcelain` | 提交后仅剩临时提交信息文件（已删）；数据文件工作区 == 提交对象 |

**纪律记录**
- 未放宽任何既有断言以让用例通过。三处改的是 **fixture**（`test_workflow_to_skill`
  加 `task_signature/source_session_id` 参数并补同签名第二会话样本；
  `test_matcher_concurrency` 的并发 fixture 补 2 个步骤；`test_routes_workflow_learning`
  的样例任务带 ASCII 词），断言原样保留，并新增 3 条收紧用例。
- `scripts/simulate_workflow_closed_loop.py` 在本次修改前**已是红的**（R1 期望
  conf=0.30 而代码早改为 0.40；R2 期望"新工作流不命中"而冷启动死锁修复后初值即
  等门槛）。已按当前语义重新基线并补 R9 准入隔离轮，脚本头部如实登记此事，
  未删除任何原有断言（R1/R2/R6/R7/R8 均为严格断言）。
- 未用脚本造任务刷数据；未改删台账；`data/learned_workflows.json` 的改动仅为
  `status` 迁移，`success_count/confidence/converted_to_skill_id` 一律原样。

---

## 七、遗留（带归属）

| # | 事项 | 证据 | 归属 |
|---|---|---|---|
| 1 | **运行期生效需重启**（无审批/触发审计）。主工作区 `data/learned_workflows.json` 仍是 5 条 active；在跑服务持有旧 cache，重启后才会读到 `archived`，或先跑 `python scripts/retire_dirty_workflows.py --apply`（幂等）。代码级隔离（不进候选）**无需重启即已生效于新代码**，但 5678 服务仍是旧进程 | `Get-NetTCPConnection -LocalPort 5678`；`C:\Users\Administrator\agent\data\learned_workflows.json` | 运维/工具链（本次任务不含重启授权） |
| 2 | 已生成的两个技能产物 `wf-f19dc52c-skill`、`zip-d2968c59-skill` 仍在运行期技能库（`data/skills_mgmt.json`，gitignore，非本仓可追踪文件）。本任务口径是"退役工作流"，技能侧下线应走技能管理（`deprecate`）而非删档 | `data/skills_mgmt.json` 内两键 | 技能层（skills_mgmt）/ 真用期人工决策 |
| 3 | `learner._WORD_RE` 的中文**按字切分**未改（改它会把"无有区分度触发词"这一判定条件本身消解掉）。若后续要提升中文触发词质量，需引入真正的分词/词表，属**学习质量专项** | `learner.py:37` | 工作流学习层后续专项（S10 邻接） |
| 4 | `_idf` 的 df=0 分支给最大权重，导致"问句里出现文档没有的罕见词"时相似度骤降到 ≈0（实测 0.001），即当前 matcher 偏保守、长问句几乎不可能命中 | §2.2 初测；`matcher.py::_idf` | 检索/匹配层专项（与 S9-03 的 RRF 归一化同族，非本任务范围） |
| 5 | `generator._compute_priority` 对 ≤3 步给 +20（优先级倒挂）未改：1 步条目已进不了候选，倒挂不再有实际影响，但语义仍反直觉 | `generator.py:102-103` | 工作流学习层调优（低优先） |
| 6 | `scripts/seed_demo_workflows.py` 直连**生产仓库** `WorkflowLearningService()` 并以 `demo-seed` 造 3 条演示数据——正是存量 3 条 `demo-seed` 条目的来源；本次未运行它（避免造数据），但它仍是"脚本刷真实数据"的风险点 | 脚本第 60/68 行 | 工具链（建议后续加 `--dry-run`/显式 `--repo` 必填） |
| 7 | 工作区 `data/learned_workflows.json` 与主工作区该文件存在双份（worktree 各自持有一份运行期数据）。本次只在 worktree 内退役，主工作区未动 | worktree 结构 | 工具链（同 S9-01 记的 `.env` worktree 问题） |

---

## 八、SHA 记录

| 项 | SHA |
|---|---|
| 基线 `master` | `9c736dad`（父 `bbc5d821`） |
| 交付提交（`s10/main`） | `66a67888`（feat(S10-01)） |
| 合并提交（`master`） | 见交接回复（`git merge s10/main --no-edit`） |
| 远端 | `origin/master`、`gitee/master` 同 SHA（见交接回复） |
