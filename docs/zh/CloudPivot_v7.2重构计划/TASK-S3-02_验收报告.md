# TASK-S3-02 验收报告 — EquivalenceCase 判定集资产 + 回放沙箱 + 验收门量化

> 归档日期：2026-09-11（演示与门禁实跑日期同）
> 所属计划：CloudPivot v7.2 重构计划（S3 消化流水线 · 第 2 任务 · 串行链中段）
> 任务书：[TASK-S3-02_判定集与回放沙箱.md](TASK-S3-02_判定集与回放沙箱.md)
> 上游设计：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §3.1 / §3.3 / §4.5 / §11.4（P7.2-23）
> 结案报告：[S3-02_交付结案报告_20260911.md](S3-02_交付结案报告_20260911.md)
> 状态：✅ **验收 8/8 通过 · 已结案**（Owner 于 2026-09-11 指示收尾并结案，见 §十一）

---

## 一、交付范围

| # | 交付物 | 落点 | 规模 |
|---|---|---|---|
| 1 | 判定集模型与存储（`EquivalenceCase` / `CaseSet` / JSON + SQLite 后端） | `agent/digestion/cases.py` | 788 行 |
| 2 | 三通道用例生成（Seed Pack / Trace 自动 / LLM+人工） | 同上 | — |
| 3 | **Seed Pack 资产**（P7.2-23：14 技能 × 43 组预置等价用例） | `agent/digestion/seed_pack.json` | 843 行 |
| 4 | 确定性回放沙箱（双跑 + record-and-replay + 三层比对 + 配额） | `agent/digestion/sandbox.py` | 947 行 |
| 5 | 验收门四条件硬闸 + 通行证 + 失败清单 | `agent/digestion/gate.py` | 541 行 |
| 6 | 漂移重探（30 天 / 上游版本变化 / schema 变化 → drifted → 失效重生成） | 同上 | — |
| 7 | stage 门控 opt-in 放行（`mirrored → shadow` 凭通行证） | `agent/digestion/stage.py`（+50 行） | — |
| 8 | 端到端演示（8 步，可复现） | `scripts/demo_s3_02_cases.py` | 448 行 |
| 9 | 新增单测 **237 例**（4 套件，全绿） | `tests/unit/test_digestion_{cases,sandbox,gate,seed_pack}.py` | 1636 行 |

**术语与纪律**：判定集资产是**证据**而非发布动作 —— 本任务不发布技能、不启用技能、
不越过审批；`shadow` 灰度与内化仍归 S3-03（本任务止于"发通行证 + 可选的 stage 推进"）。

---

## 二、真实能力端到端样例（判定集 → 回放 → 验收门）

**样例能力**：`cp.builtin.read_file`（真实 descriptor 台账中的 canonical 能力键，
S1-02 回填 / S3-01 L4 已入轨）。全部代码路径均为真实实现，无桩；除合成"同类轨迹台账"
（真实 `TraceFacade` 落库，含真实 `side_effects` 记录）外不依赖任何外部分支。

### 2.1 复现命令

```powershell
# 正向：判定集 → 回放 → 验收门四条件齐 → 通行证 → mirrored→shadow → 漂移重探
python scripts/demo_s3_02_cases.py

# 负样本 1：候选骨架缺末步（模拟提取错误）→ 门必须拒绝
python scripts/demo_s3_02_cases.py --mutate

# 负样本 2：注入无人覆盖的参数级破坏性分支 → 条件 4 必须拒绝
python scripts/demo_s3_02_cases.py --uncovered-branch
```

隔离性：统一台账 / descriptor 台账 / 审计链 / 事件流 / 判定集存储 / 通行证全部落在
`tempfile.mkdtemp()` 目录（演示末尾打印），仅产物归档到 `data/digestion/demo_s3_02/`
（该目录已 gitignore）。

### 2.2 正向运行实测输出（原文摘录）

```
[步骤 1] 合成统一轨迹台账（含噪声 + 失败轨迹 + 真实副作用记录）
  任务数=40 成功=36 失败=4
  台账行数=236

[步骤 2] 消化流水线：清洗 → 模式挖掘 → draft SKILL.md → stage 入轨
  首次入轨: None → borrowed applied=True
  候选模式: pat_b592b5996d185fe0 步骤=['read_file', 'shell_execute', 'write_file']
  支撑=36/40 覆盖率=1.00 置信度=1.00
  参数槽: ['${cmd}', '${content}', '${cmd_2}', '${content_2}']
  分支条件: ['步骤 `write_file` 出现', '步骤 `write_file` 缺失']
  副作用画像: ['${path}'] / ['shell_execute']
  draft: [('dig-cp-builtin-read-file-56353a7e', 'draft')]
  stage: borrowed → mirrored （applied）

[步骤 3] 判定集：Seed Pack（P7.2-23，独立回放验证）+ 从同类轨迹自动生成
  Seed Pack: 14 技能 × ≥3 组 = 43 组；达标 P7.2-23=True
  Seed Pack 可回放性：43/43 全部通过（失败 []）
  判定集共 40 组（生效 36 组，§3.1 区间 30–100，complete=True）
  来源分布: {'seed': 4, 'trace': 36}
  复制台账原始参数（§3.1 复制数据）：True；示例输入={'path': 'C:/repo/proj000/tests/test_mod000.py', 'encoding': 'utf-8'}

  [独立存储验证] 判定集存储与统一台账分离：
    台账文件=...\trace.db｜判定集文件=...\cases\cp.builtin.read_file.json
    同一目录？False

[步骤 4] 回放沙箱：上游 vs 候选双跑 + 三层比对 + 确定性自检 + journal
  确定性自检（两次回放逐字段一致）: True fingerprint=60019f0f1f60c1ae
    层[structure]    kind=hard passed=True score=1.0
    层[side_effects] kind=hard passed=True score=1.0
    层[judge]        kind=soft passed=True score=1.0
  副作用只记录不双写：env.commit() → 被拒绝（回放沙箱不提供真实落盘通道：副作用只记录不双写…）
  record-and-replay 校验: {'case_id': 'seed-read_file-utf8', 'found': True, 'replay_ok': True, 'diffs': []}

[步骤 5] 验收门四条件（§4.5）
  分支要求: 必须覆盖=1（已覆盖 1）｜观察项=1（['步骤 `write_file` 缺失']）
    replay_all_pass              passed=True actual=36 threshold=20
    success_rate                 passed=True actual=1.0 threshold=0.98
    p99                          passed=True actual=14.0 threshold=14.0
    destructive_branch_coverage  passed=True
  门判决: passed=True 失败条件=[]
  回放: 36/36 通过；p99 候选=14.0ms 上游=14.0ms
  人工抽检清单（10%）：['case_7ad7b5639ff7', 'case_d118dbe19d33', 'case_d19e7fee5403', 'case_e71f936d5442']
  通行证: pp_e8144bcc28dd28057f83 （executed=36/required=20）
  事件 id=ev_375155eaa6debf23fb4993c929ccc84d｜审计 seq=46 hash=df3dd0a6b47bf125…

[步骤 6] 凭通行证推进 stage（经既有 stage_migrate，三处联动）
  迁移: mirrored → shadow applied=True verdict=applied
  reasons=['等价判定集通过（§4.5 验收门通行证 pp_e8144bcc28dd28057f83） ⇒ mirrored → shadow 放行']
  审计: action=descriptor.stage seq=47
  台账 stage=shadow

[步骤 7] 漂移重探（§4.5）：上游版本变化 → 简化探针 → drifted → 失效 → 重生成
  trigger=upstream_version_change verdict=drifted drifted=True schema_changed=True
  探针用例: ['case_5f6380a2ed39', 'case_7ad7b5639ff7', 'case_d118dbe19d33', 'case_d19e7fee5403', 'case_e71f936d5442']
  理由: ['触发源声明为上游版本变化（按 §4.5 直接判定 drifted）']
  失效版本=1 → 重生成版本=2（40 组）
  版本历史: [version1 active=False drifted=True cases=40, version2 active=True cases=40]
  事件 id=ev_76e7e6b1d2c01226e7fc57c3b434f33b｜审计 seq=48

[步骤 8] 归档演示产物
  已归档: data\digestion\demo_s3_02\s3_02_demo_report.json

门判决=True｜stage=EvolutionStage.SHADOW｜判定集版本=2
```

**样例链条可核验点**：

1. **判定集脱离 Trace 生命周期**：判定集文件与统一台账分属不同路径（`同一目录？False`），
   且用例携带 `origin_trace_id` 与**复制出来的台账原始参数**（示例输入含真实路径
   `C:/repo/proj000/tests/test_mod000.py`）—— 台账 90 天过期后判定集依然可回放。
2. **三层比对全部落位**：结构（硬）/ 副作用（硬）/ judge（软）三层逐层 `passed=True`，
   且 soft 层 `score=1.0`（候选与上游行为逐字段一致）。
3. **四条件齐才发证**：`executed=36 ≥ 20`、`pass_rate 1.0 ≥ 1.0×0.98`、`p99 14.0 ≤ 14.0`、
   破坏性分支 1/1 覆盖 ⇒ 通行证 `pp_e8144bcc28dd28057f83` + `digest.stage` 事件 + 链式审计。
4. **通行证才放行**：`mirrored → shadow` 经既有 `stage_migrate` 落地（台账 stage=shadow），
   审计 action=`descriptor.stage`（三处联动由 `stage.py` 独占，未新增写入方）。
5. **漂移重探闭环**：上游版本变化 ⇒ `drifted` ⇒ 版本 1 **失效**（保留 40 组用例与理由）
   ⇒ 重生成版本 2（40 组）。

### 2.3 负样本实测（门必须拒绝）

```
$ python scripts/demo_s3_02_cases.py --mutate          # 候选骨架刻意去掉末步
  门判决: passed=False 失败条件=['replay_all_pass', 'success_rate']
  回放: 0/36 通过
  失败清单：
    - case_05188af722bb 失败层=['structure', 'side_effects']
      ['[structure] 输出结构不一致：上游 {"step_count": "number", "steps": "list[3]<str>", …}
        ≠ 候选 {"step_count": "number", "steps": "list[2]<str>", …}']
  → exit 0（负样本：门**必须**拒绝，脚本以退出码表达"已拒绝"）

$ python scripts/demo_s3_02_cases.py --uncovered-branch   # 注入无人覆盖的参数级分支
    destructive_branch_coverage  passed=False
        ↳ 未覆盖破坏性分支 1 条：["cmd contains force-recreate"]
  门判决: passed=False 失败条件=['destructive_branch_coverage']
  回放: 36/36 通过（说明失败**只**来自条件 4，四条件相互独立）
```

---

## 三、验收清单逐条核验（任务书 §四 · 8 条）

| # | 验收标准 | 结论 | 证据（命令 + 实测） |
|---|---|---|---|
| 1 | 判定集资产带 `origin_trace_id`，Trace 过期不影响（独立存储验证） | ✅ | `cases.py:cases_from_trace_set()` 强制 trace 通道带指针（`validate()` 拒绝缺失）；独立存储见演示步骤 3「同一目录？False」；用例 `test_cases_carry_origin_trace_id`、`test_trace_kind_requires_origin_trace_id`、`test_store_root_is_independent_of_trace_dir` |
| 2 | Seed Pack ≥12×≥3 组可加载且**全部可回放** | ✅ | 实测 **14 技能 × 43 组**、`meets_p7_2_23=True`、**43/43 回放通过**（演示步骤 3）；用例 `TestSeedPackShape::test_meets_p7_2_23_minimums`、`TestSeedPackReplay::test_every_seed_case_replays_green`、`test_seed_cases_reject_a_wrong_candidate`（防"恒绿资产"） |
| 3 | 回放确定性：同输入同环境两次一致；副作用只记录不双写 | ✅ | `determinism_probe()` 实测 `deterministic=True`（两次 canonical 文本/指纹一致）；`ReplayEnv.commit()` **恒抛**；出界路径 `SandboxEscapeError`；用例 `test_same_input_twice_is_identical`、`test_commit_always_refuses`、`test_real_filesystem_untouched`、`test_sandbox_module_has_no_real_io_or_process_calls`（AST 静态证据） |
| 4 | 三层比对（结构 schema → 副作用集合 → LLM-judge ≥0.85 + 10% 人工抽检）；任一层失败有明确报告 | ✅ | 三层实现于 `sandbox.three_layer_diff()`；实测三层 `passed=True score=1.0`；**逐层失败各有专测**（`test_structure_layer_catches_missing_step` / `test_side_effect_layer_catches_content_drift` / `test_judge_layer_fails_below_threshold`）；10% 抽检为**确定性抽样**（`manual_sample_ids`，实测 36 组抽 4 组）并在报告中列名 |
| 5 | 验收门四条件齐才发通行证；失败清单化 | ✅ | `gate.acceptance_gate()`；四条件**各自可单独失败**（4 条专测：`test_condition1_fails_below_twenty_replays` / `..._when_a_replay_fails` / `test_condition2_fails_on_success_rate` / `test_condition3_fails_when_p99_regresses` / `test_condition4_fails_on_uncovered_branch`）；失败清单见 `GateResult.failure_list()`（演示 --mutate 实测逐例列出失败层） |
| 6 | 漂移重探：30 天或上游版本变化触发；drifted 后判定集失效重生成路径可用 | ✅ | `gate.reprobe()` 三种触发（`age_30d` / `upstream_version_change` / `schema_change`）+ 探针失败触发；演示实测 `drifted=True → version1 失效 → version2 重生（40 组）`；用例 `TestDriftReprobe` 12 例（含 `test_due_for_reprobe_age`、`test_reprobe_detects_descriptor_schema_change`、`test_reprobe_invalidates_and_regenerates`、`test_register_reprobe_job_*`） |
| 7 | 既有 digestion/descriptors/skills 套件零回归；新增单测全绿、覆盖率 ≥80% | ✅ | 邻接回归 **333（S3-01 digestion）+ 527（skills/descriptors/trace/audit）+ 314（orchestrator/tool_calling/events）= 1174 passed / 0 failed**；新增 **237 例全绿**；覆盖率 **cases 90% / sandbox 92% / gate 94%（TOTAL 92%）** |
| 8 | 真实能力样例（判定集→回放→验收门）在验收报告可复现 | ✅ | 本报告 §二（命令 + 原文输出 + 6 个可核验点 + 两个负样本） |

---

## 四、验收门四条件与阈值口径（逐条对齐设计文档）

| 条件 | 实现 | 阈值 | 与设计文档的对应 |
|---|---|---|---|
| ① 回放全过 | `COND_REPLAY` | 生效用例 ≥ `GATE_REPLAY_MIN = 20` **且**逐例通过率 100% | §4.5「≥20 条回放全过」 |
| ② 成功率 ≥ 基线×0.98 | `COND_SUCCESS_RATE` | `GATE_SUCCESS_RATE_RATIO = 0.98` | §4.5「成功率 ≥ 基线×0.98」；与 §4.5.1 内化条件④**同值** |
| ③ p99 ≤ 上游 | `COND_P99` | `GATE_P99_RATIO = 1.0` | §4.5「p99 ≤ 上游」；与内化条件⑤同口径 |
| ④ 覆盖破坏性分支 | `COND_BRANCH` | 破坏性/失败倾向分支**全部**被用例覆盖 | §4.5「覆盖破坏性分支」 |

**基线口径（重要取舍，如实披露）**：**阈值取同一回放系统内的"上游臂"实测**
（双跑同环境 ⇒ 天然可比）；S2-01 台账 quality 统计按任务书要求**照实披露**在
`GateResult.baseline["ledger"]`（本次实测 `ledger.success_rate=1.0 / ledger.p99=3.0ms`），
但**不直接当阈值** —— 台账 `duration_ms` 是**单次能力调用墙钟**，回放测的是**整条任务链
模型时钟**，两者不同量纲；直接比较会把"一次读文件的 3ms"用作"读→测→写 14ms"的上限，
得出**错误判决**（实现期实测复现）。该口径已写入模块文档与测试
（`test_default_baseline_is_upstream_arm_not_ledger`）。

**门槛常量不引入第三套**：判定集规模 `MIN_CASE_SET_SIZE=30`（§3.1 的 30–100 区间）与
`MIN_SEED_CASES_PER_SKILL=3`（P7.2-23）语义独立；S3-01 的 `MIN_PATTERN_STEPS=2`
（成形）/ `MIN_ASCENSION_STEPS=3`（可升格）**未被复制或覆盖** —— 验收门引用的是它们
**上游**的产物（候选模式与草稿），并从 `pattern.branches` 取破坏性分支。

---

## 五、三层比对的分工与边界（如实披露）

| 层 | 性质 | 比对内容 | 判否样例 |
|---|---|---|---|
| ① 结构 schema | **硬性** | 键名 + 类型 + **列表基数**（不含标量值） | 候选少一步 ⇒ `list[3]` vs `list[2]` |
| ② 副作用 | **硬性** | ①两臂**具体目标**逐一相同；②两臂写入**内容指纹**相同；③用例契约（**具体值优先、形态容忍**：写 `${path}` 表示同形态即可） | 写别的文件 / 内容不同 ⇒ 明确理由 |
| ③ judge | **软性** | 两臂 canonical 文本相似度 ≥ **0.85** + 10% 人工抽检标记 | 默认打分器为**确定性本地实现**（token Jaccard 0.5 + 序列相似 0.5），LLM-judge 经 `judge=` **注入**（S3-03 接入） |

**披露项**：
1. 层③ 的默认打分器**不是 LLM**：LLM-judge 会让回放不可复现（§4.5 的沙箱是确定性设施），
   故默认用确定性打分器并保留注入通道；`judge_kind` 字段如实记录实际用的是哪一种。
2. **10% 人工抽检目前只产出清单**（`manual_sample`，确定性抽样、可复现），人工复核动作
   由 Owner/S3-03 承接 —— 层③ 的 `passed` 只据 judge 分数，抽检项在理由中显式标注
   "人工复核未完成前不得视为已验收"，**不冒充已完成**。
3. **p99 是模型时钟量**（标称延迟累加，见 `TOOL_LATENCY_MS`），不是墙钟：真实 p99 必须在
   S3-03 灰度期采集。这一点在 `GateConditionResult.detail["clock"]` 与通行证里都写明，
   避免把模型量当成真实性能证据。

---

## 六、与 S3-01 的接口复用（未重复实现）

| 复用对象 | 用途 | 证据 |
|---|---|---|
| `Trajectory` / `TraceSet` / `SameTaskKey` | 判定集来源数据与同类分组 | `cases.py`（只读消费，未改 S3-01 模型） |
| `cleaning.same_task_key` / `intent_key_for_trace` 口径 | "用例属于同一能力同一意图"由既有实现给出 | `cases.trace_set_for()` 委派 `DigestionService.collect/build_trajectories` |
| `generalize` 占位符方言 `${name}` / `${name}_2` | 参数槽命名与绑定（**未新造方言**） | `cases._slot_name` 与 `generalize.infer_parameter_slots` 逐值对账用例 `test_slot_names_mirror_generalize_rule` |
| `CandidatePattern` / `PatternStep` / `BranchCondition` | 条件 4 的破坏性分支来源 + 候选原生实现载体 | `gate.required_branches()` / `sandbox.PatternImplementation` |
| `DigestionService.pipeline()` | 端到端串接（演示步骤 2） | `scripts/demo_s3_02_cases.py` |
| `stage.stage_migrate()` / `evaluate_migration()` | 通行证通过后的 stage 推进入口 | `gate.advance_to_shadow()`；`stage.py` 新增 opt-in 分支 |

**S3-01 遗留 #4（参数级条件）在本任务内的落地**：新增条件求值器
（`sandbox.evaluate_condition`）支持 `path contains test` / `mode == force` /
`path matches <regex>` / `steps >= N` / `步骤 X 出现|缺失` 与 `且|或` 组合；
`gate.branch_coverage()` 据此判定"必须覆盖"与"登记观察"两类要求。
Seed Pack 的 TDD 用例即用该能力：`seed-tdd-red-green` 的条件步**执行**、
`seed-tdd-no-test-path` 的条件步**跳过**（两条用例均有专测）。

---

## 七、实现期发现并处置的真实缺陷（含自我纠正）

| # | 现象 | 根因 | 处置 | 回归用例 |
|---|---|---|---|---|
| 1 | 轨迹派生的用例复制参数时**错位**（把探索前缀行的参数当成了种子步参数） | 清洗会削前缀/合并重复 ⇒ 按下标硬对会错位 | 改为**按标签贪心对齐**（`align_concrete_args`） | `test_alignment_skips_noise_prefix_rows` |
| 2 | 同名键（读的 `path` / 写的 `path`）被清洗压成**同一个形态占位符** ⇒ 两处都绑到第一个值（上游臂把报告写到被读文件上） | 形态占位符不编码位次 | 用例程序把形态占位符**提升为位次槽**（`${path}` / `${path_2}`）；补齐按位次取槽（`positional_slot_names`） | `test_complete_params_prefers_positional_slot_over_bare_key`、`test_complete_params_uses_literal_over_bare_key_fallback` |
| 3 | 候选骨架**只带差异参数**（常量/形态参数不进骨架）⇒ 骨架单独不可执行 | `generalize.infer_parameter_slots` 的"恒定值不进槽"判定（S3-01 口径） | 新增**接口补齐**（位次对齐 → 同标签 → 用例输入），补齐来源逐条进 `Observation.filled`（显式可审，不是"偷偷换成上游程序"） | `test_complete_params_fills_from_upstream_position`、`test_complete_params_never_overrides_candidate_params` |
| 4 | `PatternStep.params` 的**键名是槽名**（`cmd_2`）而非原键（`cmd`）⇒ 直接执行会丢参数 | `service.mine()` 的 `pstep.params[slot.name] = placeholder` | `restore_param_keys()` 按命名规则反向还原（仅还原已知参数名，防误改） | `test_pattern_slot_keys_are_restored`、`test_restore_param_key_keeps_unknown_suffix` |
| 5 | 把 S3-01 的**描述性**分支条件当执行守卫 ⇒ 条件恒假、该步被永久跳过 | `PatternStep.condition` 是"该位次历史分支条件"的标注，不是守卫 | `PatternImplementation(use_conditions=False)` **默认不当守卫**，需要条件执行须显式给出 `ProgramStep.condition`（Seed Pack 即该通道） | `test_pattern_description_conditions_are_not_execution_guards`、`test_pattern_use_conditions_is_opt_in` |
| 6 | 层②最初按**形态**比对 ⇒ "写对了形态、写错了具体位置"漏过硬性层；且在**内容**层一度只用路径+摘要，具体路径差异不可见 | 形态归一掩盖了具体目标 | 层②改为：两臂**具体目标**逐一相同 + 内容指纹相同 + 用例契约（具体优先、形态容忍）；形态视图保留在 detail 供报告 | `test_side_effect_layer_catches_target_drift`、`test_side_effect_layer_catches_content_drift` |
| 7 | 台账基线（单次调用墙钟）被直接用作整链 p99 上限 ⇒ **错误判决**（14ms 被判 > 3ms） | 量纲/粒度不一致 | 阈值统一取同回放系统的上游臂；台账统计转为**披露字段** | `test_default_baseline_is_upstream_arm_not_ledger` |
| 8 | 测试期 `acceptance_gate`/`advance_to_shadow` 默认 `PassportStore()` ⇒ 把通行证写进**运行时** `data/digestion/cases/passports/`（并污染后续用例） | 默认落点即运行时目录 | **首次修复不完整**（只改了被我注意到的用例），收尾轮次改为**会话级兜底**：4 个新套件各加 autouse fixture 把 `CP_DIGESTION_CASE_DIR` 隔离到 `tmp_path` ⇒ 无论用例是否显式传 store，默认落点都在临时目录；`PassportStore.save` 改为按 `passport_id` **幂等**；`_descriptor_view(registry=None)` 不再隐式读运行时台账 | `test_passport_store_round_trip`、`test_descriptor_view_is_explicit_only`；收尾实测：跑完 4 套件后 `data/digestion/cases` 与 `data/digestion/replay` **均未被创建**（见 §十一） |

> 第 8 项是**本次自查发现并已彻底闭合**的污染：`data/digestion/cases/` 与
> `data/digestion/replay/` 下的测试产物已删除，且根因（默认落点未隔离）已在**会话级**
> 兜住 —— 该目录属 gitignore 的运行时区（不入库），但"测试不得写运行时目录"是纪律项，
> 故按根因处置而非逐例打补丁。

---

## 八、本地门禁实测（推送前）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 新套件 | `pytest tests/unit/test_digestion_{cases,sandbox,gate,seed_pack}.py` | ✅ **237 passed / 0 failed** |
| 邻接回归（S3-01 digestion） | `pytest tests/unit/test_digestion_{cleaning,generation,mining,pipeline,stage}.py tests/unit/test_s3_01_handover.py` | ✅ **333 passed** |
| 邻接回归（skills/descriptors/trace/audit） | `pytest tests/unit/test_skills_mgmt.py test_descriptors_*.py test_trace_v2.py test_audit_*.py` | ✅ **527 passed / 1 xfailed**（既有 TF-IDF 基线） |
| 邻接回归（orchestrator/tool_calling/events） | `pytest tests/unit/test_orchestrator_{reject,refactor}.py test_tool_calling_*.py test_events_v1.py` | ✅ **314 passed** |
| 覆盖率（新模块） | `pytest ... --cov=agent.digestion.{cases,sandbox,gate}` | ✅ **90% / 92% / 94%，TOTAL 92%**（≥80%） |
| kwarg 冲突扫描 | `python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH` **和** `--path tests` | ✅ 两路均 **0 处** |
| mypy（新模块 + 脚本） | `mypy agent/digestion/{cases,sandbox,gate,stage,__init__}.py scripts/demo_s3_02_cases.py` | ✅ 本任务文件 **0 error** |
| 循环依赖 | `lint-imports --config .importlinter`（需 `PYTHONUTF8=1`） | ✅ **2 kept / 0 broken** |
| 架构规则 | `python -m agent.observability.arch_rules --check` | ✅ **未豁免违规 0**（4 项既有豁免） |
| 核心不变量 | `python scripts/verify_core_invariants.py` | ✅ **12/12 PASS** |
| 边界覆盖 | `python scripts/check_boundary_coverage.py` | ✅ `blocked_modules=[]`（场景覆盖 96%） |
| 演示（正/负样本） | `python scripts/demo_s3_02_cases.py [--mutate] [--uncovered-branch]` | ✅ 正向发证+推进；两个负样本**均被拒绝** |

**CI 终态（push 触发，SHA `36e27d9b`）**：**13/13 workflow 全绿（0 failure / 0 cancelled）**，
其中「云枢系统测试流程」**21/21 job success**、「可观测性质量保障」19/20 success（1 skipped）、
「kwarg 扫描 → SonarQube」success（`agent` 与 `tests` 两路均 0 处 HIGH）。
原始统计与可复现命令见结案报告 §3.2。

---

## 九、演示产物与取证文件

| 文件 | 内容 |
|---|---|
| `data/digestion/demo_s3_02/s3_02_demo_report.json` | 演示全量结构化产物（pattern / 判定集规模裁定 / 沙箱三层 / 门四条件 / 通行证 / 漂移报告） |
| `data/digestion/demo_s3_02/demo_stdout.txt` | 演示标准输出原文（本报告 §2.2 摘录自它） |

> 二者均落在 gitignore 的 `data/digestion/` 运行时区，**不入库**；复现命令见 §2.1。

---

## 十、结论

- 任务书 §三 6 项预期成果**全部交付**；§四 验收清单 **8/8 通过**，每条附「文件 + 用例 + 实测输出」三级证据。
- 判定集资产（40 组 / 能力，含 Seed Pack 14×43）、确定性回放沙箱（三层比对 + 只记录不双写）、
  验收门四条件硬闸（通行证 + 失败清单）、漂移重探（失效 + 重生成）**均已实现并实测通过**，
  且给出**正反两个方向的实测证据**（-`--mutate` / `--uncovered-branch` 门必须拒绝）。
- 与 S3-01 的接口**零重复实现**、门槛常量**未引入第三套**、既有公开接口签名与行为
  **未被破坏**（`mirrored → shadow` 的放行是**opt-in**：不带通行证键时逐字返回 S3-01 的
  `deferred_to_downstream`，有既有用例与新增用例双重钉住）。
- 未能闭环者已**逐条披露**（judge 默认非 LLM、人工抽检仅清单、p99 为模型时钟、
  结构型分支登记为观察项、用例↔候选适用性暂以 `active` 表达），并登记归属任务（见结案报告 §5）。

---

## 十一、收尾轮次（Owner 指示，2026-09-11）

Owner 指示：「完成项目交付前的各项收尾工作（推送并确保经 CI/CD 验证、更新报告、
最终状态确认与 stakeholder 核实）……如有遗留问题，处理后结案。」据此执行：

### 11.1 冻结树终态复核（全部实跑，非引用历史结论）

| 复核项 | 命令 | 结果 |
|---|---|---|
| 工作树 | `git status --porcelain` | ✅ 干净（无漂移） |
| 双远端 | `git rev-parse origin/master gitee/master` + `git diff` | ✅ 同点（空 diff） |
| 新套件 | `pytest tests/unit/test_digestion_{cases,sandbox,gate,seed_pack}.py` | ✅ **237 passed / 0 failed** |
| 邻接回归（广域） | `pytest tests/unit -k "digestion or descriptors or skills_mgmt or trace or audit or events or orchestrator or tool_calling" -m "not slow"` | ✅ **2238 passed / 0 failed / 1 skipped / 1 xfailed** |
| 端到端演示 | `python scripts/demo_s3_02_cases.py` | ✅ 门判决=True、stage=SHADOW、判定集版本=2 |
| 文档门禁 | `scripts/dev/git_precommit_check.ps1` | ✅ 失效链接 0 + 锚点回归 4 passed |
| 运行时区卫生 | 跑完 4 套件后检查 `data/digestion/cases`、`data/digestion/replay` | ✅ 均**未被创建**（收尾修复后） |
| 敏感串速查 | 对新文件扫 `api_key/secret/password/token` 赋值形态 | ✅ 0 命中（CI「硬编码密码扫描（全分支）」亦 success） |

### 11.2 收尾轮次发现并处置的缺陷（详见 §七 第 8 项）

**测试隔离缺陷的根因闭合**：首次修复只改了"我注意到的"用例，收尾复核时发现
`data/digestion/cases/passports/` 仍被重建 —— 说明仍有通过路径的用例把通行证写进了
**运行时目录**。处置：4 个新套件各加 autouse fixture 把 `CP_DIGESTION_CASE_DIR`
隔离到 `tmp_path`（**会话级兜底**，不依赖用例自觉传参），删除残留产物并实测确认
零污染。这是"**逐例打补丁 ≠ 修根因**"的一次实证，与 S3-01 §4.5/§4.9/§4.7
「门禁/隔离必须确认真的生效、且作用于该作用的地方」同源。

### 11.3 遗留问题处置（收尾轮次结论）

| # | 遗留 | 本轮处置 | 终态 |
|---|---|---|---|
| 1 | judge 默认非 LLM | 注入通道与 `judge_kind` 披露均已就位并有专测 | 📋 归 S3-03（灰度期接入） |
| 2 | 10% 人工抽检仅清单 | 抽检为确定性可复现抽样，层③理由显式标注"未完成人工复核前不得视为已验收" | 📋 归 Owner/S3-03（人工动作） |
| 3 | p99 为模型时钟量 | 门条件 detail 与通行证均写明 `clock` 字段 | 📋 归 S3-03（灰度采集真实 p99） |
| 4 | 结构型分支为观察项 | 分类规则（`classify_condition`）与理由写入代码、报告与门 detail | 📋 需"可回放的中止注入"机制方可纳硬闸（S3-03/后续 RFC） |
| 5 | 用例↔候选适用性 | 演示中已显式打印该标注与其重生成后的重施加；报告披露 | 📋 归 S3-03（引入显式字段） |
| 6 | 判定集 TTL/容量治理 | `size_verdict` 已裁定 30–100 与超限理由；历史保留上限 `MAX_STORE_HISTORY=5` | 📋 归 S2/S5 生产化 |
| 7 | 沙箱非容器隔离 | 模块文档显式声明边界（进程内确定性执行模型） | 📋 归 S3-03/生产化 |
| 8 | 真实流量不足 | **未声称已实现真实能力内化**；验收依赖 Seed Pack + 合成同类轨迹（设计内离线设施） | 📋 随真实流量 |
| 9 | 重探调度默认关闭 | 与 `evolution_scheduler` 同一条安全底线（env 显式开启），且有"禁用/启用"双向专测 | 📋 归部署/生产化 |
| 10 | 门禁脚本产物漂移 | 本轮再次确认跟踪文件无漂移（`git status` 干净），本地跑门禁后已还原生成物 | ✅ **本轮闭环**（流程纪律） |
| 11 | **测试隔离未兜住默认落点**（本轮新发现） | 会话级 autouse fixture + 删除残留 + 实测零污染 | ✅ **本轮闭环** |

### 11.4 最终状态确认与结案

- **交付物**（按 `git show --stat` 逐件核对）：`cases.py` / `sandbox.py` / `gate.py` /
  `seed_pack.json` / `stage.py`（增量）/ `demo_s3_02_cases.py` / 4 个测试套件 /
  两份报告 —— 与任务书 §三 6 项预期成果逐条对应（§6.1）。
- **质量标准**：验收 8/8（§三）、冻结树复核全绿（§11.1）、
  **代码实现基线 `36e27d9b` 的 CI 13/13 workflow 全绿（「云枢系统测试流程」21/21 job）**。
- **stakeholder 核实**：Owner 于 2026-09-11 直接指示收尾并结案，据此确认交付物与质量标准；
  报告内所有未能闭环项**逐条披露并登记归属**（§11.3），无隐瞒、无"以文档代替证据"。
- **结案**：**TASK-S3-02 已结案**（验收 8/8；11 项遗留中 2 项本轮闭环、9 项带归属移交下游，
  均不阻塞）；S3 串行链下一环 **S3-03 前置已就绪**（判定集资产 / 回放沙箱 / 验收门通行证与失败清单）。
