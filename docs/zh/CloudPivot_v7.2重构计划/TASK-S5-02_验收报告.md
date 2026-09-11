# TASK-S5-02 验收报告 — L0-L3 评测锚与基线（打破自验收循环）

> 任务书：[`TASK-S5-02_评测锚与基线.md`](TASK-S5-02_评测锚与基线.md)
> 上游设计：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §6.5 评测分层 / §6.6 埋点清单 / §6.7 指标字典与 SLO
> 批次约定：[`PARALLEL_并行铺开总表.md`](PARALLEL_并行铺开总表.md)
> 工作区：worktree `s502`（`--base master`）｜验收日期：2026-09-12
> **口径声明**：本报告**不声称**任何模型能力结论 —— 全部通过率均来自**参考解自检**与
> **变异解区分度对照**（判定器与用例自洽性），真实能力基线须由被测解算器产出答案工件后另测。

---

## 一、交付物清单

| # | 交付物 | 落点 | 规模 |
|---|---|---|---|
| 1 | 用例数据契约（schema `eval.cases.v1`：解析 / 哈希 / 校验） | `agent/eval/cases.py` | 475 行 |
| 2 | 判定器（22 机械 + 1 代理，机械可验优先） | `agent/eval/checkers.py` | 700+ 行 |
| 3 | **L0 锚存储**（独立只读 + 哈希锚定 + 写入守门 + fail-closed） | `agent/eval/anchor.py` | 565 行 |
| 4 | 被测解算器（reference / static / **参数感知 mutant** / null） | `agent/eval/solvers.py` | 311 行 |
| 5 | 分层执行器 `run_l0()` / `run_l1()` / `run_l2()` / `run_l3()` + 基线对照 + L3 框架契约 | `agent/eval/runner.py` | 575 行 |
| 6 | §6.7 **指标字典**与周报计算（复用 `acr.py`/`utc.py`） | `agent/eval/metrics.py` | 830 行 |
| 7 | L2 Core-50 基线（UTC + shadow 真实墙钟 p99 + 样本充分性 + S5-03 触发） | `agent/eval/baseline.py` | 372 行 |
| 8 | S2-03 遗留 #3：难度权重拟合 + **四闸切换路径**（默认只披露不考核） | `agent/eval/calibration.py` | 397 行 |
| 9 | **L0 锚 20 条冻结用例 + 参考解 + 锚清单** | `eval/l0_anchor/{cases,reference,manifest}.json` | 20 条 / 65 判定条目 |
| 10 | L1 最小集 10 条（+ 参考解） | `eval/l1_min/` | 10 条 / 33 判定条目 |
| 11 | L2 Core-50（三类种子场景 20/15/15；48 机械 + 2 代理披露） | `eval/l2_core50/` | 50 条 / 212 判定条目 |
| 12 | L3 Golden-80 **框架占位** | `eval/l3_golden80/`（cases.json + README.md） | 0 条（target 80） |
| 13 | 各层**判定器自检基线** | `eval/baselines/{l0,l1,l2}_baseline.json` | — |
| 14 | 评测分层运行手册 | `eval/README.md` | — |
| 15 | 周报脚本（§6.7 ≥5 项可计算指标） | `scripts/report_slo_weekly.py` | 148 行 |
| 16 | 分层执行 CLI | `scripts/run_eval.py` | 165 行 |
| 17 | **评测数据自检门**（四层契约 + 锚 + 参考解 + 逐条判定区分度） | `scripts/check_eval_datasets.py` | 175 行 |
| 18 | 锚冻结 / 校验 / 发布清单工具 | `scripts/freeze_eval_anchor.py` | 175 行 |
| 19 | 锚哈希锚定入 **release manifest** | `release/eval_anchor_manifest.json` | 全层数据哈希 |
| 20 | 新增单测 **300 例**（7 套件） | `tests/unit/test_eval_*.py` | 全绿 |
| 21 | 验收报告（本文件） + 交付结案报告 + 00 总览状态行 | `docs/zh/CloudPivot_v7.2重构计划/` | — |

---

## 二、任务书 §四 验收清单逐条核验

### ✅ 1. L0 20 条用例人工冻结、系统不可写（写入尝试被拒/校验失败用例）

**位置独立**（机器校验，双向）：锚目录 `eval/l0_anchor/` 不在 `data/`、`data/events/`、
`data/digestion/`、`data/reflection/`、`data/feedback/`、`data/eval/` 任何一处之下，
且系统数据目录也不在锚内 —— 违反即 `AnchorIndependenceError`。

**无写 API + 写入守门**（扰动尝试被拒，逐条单测）：

```
$ python -m pytest tests/unit/test_eval_anchor.py -q -k SystemCannotWrite
tests/unit/test_eval_anchor.py ...........  (11 passed)
```

被拒清单（`AnchorReadOnlyError`）：`AnchorStore.write_cases / update_case / delete_case /
write_reference / write_manifest`；`anchor.guard_write(<锚内路径>)`；
`freeze_anchor(..., allow_write=False)`（缺省）；`freeze_anchor(frozen_by="")`（无人工署名）；
基线 / 拟合件 / 周报写出前均过 `guard_write`。

**哈希锚定 + fail-closed**（校验失败用例）：

| 扰动 | 检出方式 | 结果 |
|---|---|---|
| 改一条用例内容 | 逐条哈希 + 用例集整体哈希不一致 | `verify_anchor().ok=False`；`load()` 抛 `AnchorIntegrityError` |
| 删一条用例 | 清单有而文件无 | `missing=["L0-S1-01", …]` |
| 增一条用例 | 文件有而清单无 | `unexpected=["L0-S1-…-99"]` |
| 改参考解 | 参考解哈希不一致 | `reference.ok=False` |
| manifest 缺失 / 损坏 | manifest 读取失败 | `problems=["manifest 缺失/不可读…"]` |

实测（本任务开发过程中真实发生）：修改 `reference.json` 后再运行 → 
`AnchorIntegrityError: L0 锚完整性校验失败（fail-closed，拒绝评测）: 参考解哈希不一致`。

**冻结署名**：`frozen_by = "S5-02 执行会话（代行冻结；Owner 复核项见本报告 §6）"`，
`frozen_at = 2026-09-12T01:24:56+0800`，`tool_version = s5-02.1`。

### ✅ 2. `run_l0` 输出可复现 pass/fail；基线对照记录

```
$ python scripts/run_eval.py --layer L0 --solver reference --print-md
# L0 评测结果（solver=`reference`）
- 用例集：`…/eval/l0_anchor/cases.json`（sha256 `d147ffa2c76a`）
- 总数 20｜pass 20｜fail 0｜error 0｜unassessed 0
- 通过率 1.0000（分母 = 已评测 20 条；未评测 0 条不计入）
- 用例耗时 p99 = … ms（wall_clock(perf_counter)）
- 基线对照：ok（回归 0 条）
```

* **零解算器（缺省，本环境无真实 LLM 凭证）**：`--solver null` → 20 条全部
  `unassessed`，`pass_rate = n/a`（**不计入分母**，不用参考解冒充成绩）；
* **可复现**：同一用例集哈希下重复运行结果一致（`test_case_digest_stable_across_instances`
  等哈希口径单测 + 基线对照 `ok`）；
* 基线固化：`eval/baselines/l0_baseline.json`（`solver=reference`，
  note 标注"判定器自检基线，不代表模型能力"）。

### ✅ 3. L2 Core-50 覆盖三类种子场景且清单明确；作为 UTC 基线数据源

| 项 | 值 |
|---|---|
| 条数 | **50** |
| 场景分布 | `S1_fix_bug` **20** / `S2_codebase_qa` **15** / `S3_commit` **15** |
| 判定条目 | **212** |
| 判定口径 | 48 条纯机械（`mechanical`）+ **2 条代理口径已披露**（`proxy`，且各自保留机械佐证） |
| 用例集哈希 | `c42870ea6d4514d22d9afa6fce7252c1b1187a3a3701b7530ad8e5d4a8e7a8d8` |
| 参考解 | 覆盖 50/50，`--solver reference` → **50/50 pass**，变异解 **0/50 pass** |
| 逐场景清单 | `eval/l2_core50/cases.json`（逐条 `id/scenario/title/input/expect/why`）+ `eval/l2_core50/README.md` |

L2 是 **UTC 基线唯一依据**：基线快照（`agent/eval/baseline.py`）把 L2 用例集哈希、
逐场景规模、运行结论与 `utc.utc_window()` / `ShadowReport.p99_wall_*` /
`ShadowLedger.daily_average()` 三路真实数据源装配到同一份可复核快照里
（实测输出见 §4.3）。

### ✅ 4. L3 框架定义（目录/运行器/周期）可扩展

`agent/eval/runner.py::L3_FRAMEWORK` + `eval/l3_golden80/README.md`：

* 目录：`eval/l3_golden80/`（用例集 + README，复用 L0 的哈希锚定机制）；
* 运行器：`agent.eval.runner.run_l3()`（与 L0/L1/L2 **同构**：`run_layer(layer)`）；
  CLI：`python scripts/run_eval.py --layer L3`；
* 周期：W15+ / M7+，**发布前 + 每月一次**（与 §6.6 `baseline_remeasure {weekly, core50_cost, golden_set_pass_rate}` 同源）；
* 晋升规则：L2 连续 2 窗口全绿且样本达标 → 抽取代表用例升入 L3（`case_id` 不变，层前缀升级）；
* 当前状态：`framework_only`（0 条 / target 80），报告中显式披露。

### ✅ 5. 指标脚本可计算 ≥5 项 §6.7 指标（含数据源引用）

`python scripts/report_slo_weekly.py --show-dictionary` → **11 项指标定义**，
其中**可计算 8 项**（≥5 ✅）：`digest_throughput`、`internalization_rate`、
`skill_success_rate`、`approval_decay_rate`、`delegation_cycle_days`、
`abandoned_rate`、`exploration_satisfaction`、`utc_cents_per_task`；
3 项为**框架就绪/数据源缺位**并如实标注（`delegation_recovery` 归 S4-04、
`healing_latency` 无发射方、`routing_accuracy` 需事后标注）。

每项指标都带 **定义 / 公式 / 数据源 / 目标 / 单位 / 分子分母 / 样本量 / 披露**，
报告表格逐行给出"数据源 + 公式"（**不可追溯的数字不出现在报告**）。

### ✅ 6. 既有评测/验收套件零回归；新增单测全绿、覆盖率 ≥80%

| 项 | 结果 |
|---|---|
| 新增单测 | **300 例**（7 套件：cases 41 / checkers 71 / anchor 34 / runner 46 / metrics 39 / baseline 33 / datasets 36） |
| 覆盖率（`agent/eval`） | **93%**（2075 stmts / 145 miss；逐模块 89%–100%，全部 ≥80%） |
| 邻接回归 | `acr` / `utc` / `events` / `trace_v2` / `digestion{shadow,gate,internalize,stage}` / `s3_01_handover`：**635 passed / 0 failed**（4 skipped 为台账缺失的冷启动跳过） |
| 广域回归与全量抽查 | 见 §5 质量证据 |

### ✅ 7. L0 首次运行结果在验收报告记录

见 §4.1（首次运行 = 本报告 §4.1 的 reference / mutant / null 三次运行，含逐条清单）。

### ✅ 8.【S2-03 #3】ACR 意图/难度启发式 → 真实信号的切换路径已定义，且 L2 基线建立前保持"只披露不考核"

**切换路径**（`agent/eval/calibration.py`，可执行清单 `switch_checklist()` 5 步，
每步含 动作/验证/回滚）：

1. L2 Core-50 基线就绪（本任务交付 → ✅ 已满足）；
2. 在同一窗口拟合难度权重（`report_slo_weekly.py --fit-difficulty`）→ 拟合件含样本量/方法/基线哈希；
3. 打开显式开关 `CP_ACR_DIFFICULTY_FIT=1`（**默认 0**）；
4. 在 ACR 汇总层应用 `difficulty_weight()`（**不改 `acr.py` 既有签名与行为**）；
5. 周报与验收报告把该项从「披露不考核」改为「考核」并记录拟合件版本。

**四闸全绿才可切换**（`switch_status()`）：`l2_baseline_ready` ∧ `fit_usable`
（每档样本 ≥20）∧ `env_enabled` ∧ `fit_matches_baseline`（基线换版即失效）。
当前状态：**`disclose_only`（只披露不考核）** —— 开关默认关闭 + 真实成本/介入样本未达阈值；
`difficulty_weight()` 在闸门未全绿时**恒返回 1.0（等权）**。

**拟合方法**（无自由参数、可复现）：`weight_d = mean(介入强度|难度=d) / mean(介入强度|全部)`；
窗口内介入强度全为 0 时**拒绝产出权重**（`no_signal`，因为"没有介入"推不出难度差异）。

### ✅ 9.【S2-03 #4】探索满意度正式口径已定义（替代代理指标），并显式披露替代关系

正式口径 = **双列**：

* **👍率** = `like/(like+dislike)`，源 `agent.feedback.FeedbackManager.get_feedback_summary()`（直接信号）；
* **任务闭环率** = `closed/(closed+failed)`，源 `acr.acr_summary()["exploration"]`（**原代理口径**）。

披露文本（随报告输出，逐字）：

> 正式口径 = 👍率（直接信号，源 agent.feedback）与任务闭环率（代理指标，源 acr.exploration）
> **并列双列**；闭环率单独使用会把「没被投诉的沉默放弃」算成满意，故不得单独用于考核。
> 范围：explore/consult 任务排除在 ACR 分母之外；本指标披露不考核（P7.1-16）。

缺任一数据源时**该列为 `None` 并给出原因**（不填 0 冒充），单测覆盖
（`test_thumbs_column_empty_when_feedback_source_missing`）。

### ✅ 10. 指标计算复用 S2-03 `acr.py`/`utc.py` 接口，未另建重复计数逻辑

* 任务闭环 / 介入 / 成本 / 探索满意度闭环率 → `acr.acr_summary(since, until)`；
* UTC 与成本 → `utc.utc_window(start, end)`（**直接透传**，不重算）；
* 灰度通过率与样本量 → `ShadowLedger.rows()` / `daily_average()`（S3-03 交付）；
* 轨迹 → `UnifiedTraceStore.snapshot_stats()`（S2-01 交付）；
* 本模块**只**从 events.v1 事件流读取 §6.7 要求而上述聚合未覆盖的少量字段
  （`digest.stage` / `approval` / `task.*`），并复用 `acr.EV_INTERVENTION`、
  `acr.KIND_AUTO_PASS`、`acr.DIFFICULTIES` 等既有口径常量；
* 无任何新增的介入权重表 / 分母规则 / 成本换算实现（`grep` 证据见 §5）。

---

## 三、评测分层总览（本次交付终态）

| 层 | 条数 | 判定条目 | 场景/口径 | 参考解 | 变异解 | 用例集哈希（前 16） |
|---|---|---|---|---|---|---|
| **L0 锚** | 20 | 65 | 6 类：S1 4 / S2 3 / S3 3 / Router 3 / 审批 4 / 回滚 3；全机械 | 20/20 | 0/20 | `d147ffa2c76a1c78` |
| **L1 最小集** | 10 | 33 | 6 类；全机械；秒级快路径 | 10/10 | 0/10 | `d3d93f6b9edc8023` |
| **L2 Core-50** | 50 | 212 | 三类种子场景 20/15/15；48 机械 + 2 代理（披露） | 50/50 | 0/50 | `c42870ea6d4514d2` |
| **L3 Golden-80** | 0（框架） | — | framework_only；target 80；W15+/M7+ | — | — | `e3b0c44298fc1c14`（空集） |

**逐条判定区分度**（本任务最硬的质量不变量）：对**每一条判定条目**单独构造"保证违反它"的
取值（参数感知变异，见 `solvers.break_value`），逐条运行后**幸存数 = 0**：

```
$ python scripts/check_eval_datasets.py
[OK  ] L0: cases=20 sha=d147ffa2c76a ref={'pass': 20, ...} mutant={'pass': 0, 'fail': 20, ...}
[OK  ] L1: cases=10 sha=d3d93f6b9edc ref={'pass': 10, ...} mutant={'pass': 0, 'fail': 10, ...}
[OK  ] L2: cases=50 sha=c42870ea6d45 ref={'pass': 50, ...} mutant={'pass': 0, 'fail': 50, ...}
[OK  ] L3: cases=0 sha=                ref=None               mutant=None
自检结论: 全绿
```

> 开发过程实证了该不变量的价值：首轮 L2 有 **4 条** `contains_all` 判定在变异后仍然通过
> （短片段在别处再次出现），据此把变异策略改为"删除该片段的**全部**出现"，
> 幸存数归零 —— 这正是"判定器有没有在干活"的机器证明。

---

## 四、关键运行结果

### 4.1 L0 锚首次运行（三种解算器）

```
$ python scripts/run_eval.py --layer L0 --solver reference   # 判定器自检
pass 20 / fail 0 / error 0 / unassessed 0   → pass_rate 1.0000
披露：本次运行使用参考解（锚内冻结答案）：验证的是判定器与管道本身，不代表任何模型能力

$ python scripts/run_eval.py --layer L0 --solver mutant      # 负样本对照
pass 0 / fail 20 / error 0 / unassessed 0    → pass_rate 0.0000（退出码 1）

$ python scripts/run_eval.py --layer L0 --solver null         # 无真实解算器（缺省）
pass 0 / fail 0 / error 0 / unassessed 20    → pass_rate n/a（不计入分母）
```

**L0 20 条逐条清单**（id / 场景 / 标题 / 判定条目数）：

| id | 场景 | 标题 | 判定条目 |
|---|---|---|---|
| L0-S1-01 | S1_fix_bug | 修复分块函数：末块被截断且未拒绝非法 size | 1（5 探针） |
| L0-S1-02 | S1_fix_bug | 修复除零被静默吞掉的安全除法 | 1（4 探针） |
| L0-S1-03 | S1_fix_bug | 修复可变默认参数导致的跨调用状态泄漏 | 1（4 探针） |
| L0-S1-04 | S1_fix_bug | 修复区间判断的开闭边界（应为闭区间） | 1（4 探针） |
| L0-S2-01 | S2_codebase_qa | §6.1 七项介入权重的精确取值与出处 | 3 |
| L0-S2-02 | S2_codebase_qa | events.v1 信封 schema 名与字段清单 | 3 |
| L0-S2-03 | S2_codebase_qa | 消化状态机七态及其主链顺序 | 4 |
| L0-S3-01 | S3_commit | 提交生成：修正 ACR 超时拒绝权重 | 3 |
| L0-S3-02 | S3_commit | 提交生成：新增 L0 锚与执行器 | 4 |
| L0-S3-03 | S3_commit | 提交生成：修复测试运行时区污染 | 3 |
| L0-RT-01 | router_decision | 只读读取请求应走廉价模型与只读工具 | 4 |
| L0-RT-02 | router_decision | 破坏性删除必须升级审批 | 4 |
| L0-RT-03 | router_decision | 超 workspace 的不可逆发布需人工二次确认 | 4 |
| L0-AP-01 | approval_intercept | 破坏性且越界操作不得自动放行 | 4 |
| L0-AP-02 | approval_intercept | 超时未审批按拒绝处理且权重为 2 | 4 |
| L0-AP-03 | approval_intercept | L0 自动放行权重为 0 且不计介入 | 4 |
| L0-AP-04 | approval_intercept | scope 内只读操作可自动执行但仍需留痕 | 4 |
| L0-RB-01 | rollback | 补偿步骤必须与正向步骤严格逆序 | 4 |
| L0-RB-02 | rollback | 部分失败只补偿已生效步骤 | 4 |
| L0-RB-03 | rollback | 审计留痕、禁止破坏性命令、无副作用泄漏 | 5 |

### 4.2 L1 / L2 首次运行

```
$ python scripts/check_eval_datasets.py --layer L1
[OK] L1: cases=10 ref={'pass': 10, ...} mutant={'pass': 0, 'fail': 10, ...}  逐条判定幸存 0

$ python scripts/check_eval_datasets.py --layer L2
[OK] L2: cases=50 ref={'pass': 50, ...} mutant={'pass': 0, 'fail': 50, ...}  逐条判定幸存 0
```

### 4.3 L2 基线快照（UTC 唯一依据 + 样本充分性 + S5-03 触发）

```
$ python scripts/run_eval.py --layer L2 --write-l2-baseline data/eval/l2_baseline.json
- 用例集：50 条（sha256 c42870ea6d45）｜场景分布 {'S1_fix_bug': 20, 'S2_codebase_qa': 15, 'S3_commit': 15}
- 成本/UTC：UTC = <utc.utc_window 实测值> cents/任务（锚模型见快照 anchor_model）
- 灰度真实墙钟 p99：候选 / 上游（源 ShadowLedger.rows()，clock = wall_clock(perf_counter; per-arm real elapsed)）
- 轨迹台账：<UnifiedTraceStore.snapshot_stats().total> 条；达标（≥20 条）能力 <…>
- 校准触发条件（Owner 裁定 C）：**unlocked**（L2 数据集就绪）
  - 未就绪项：成本样本 < 20/模型 → **不声称校准已完成**，系数仍沿用价格锚定表
- 样本充分性缺口：逐条列出（灰度台账 / 轨迹台账 / 成本样本）
```

> **本机与生产的口径差别（如实披露）**：本机 `data/events/` 含开发期测试噪声、
> 轨迹/灰度台账为空，因此快照中的 UTC 与 p99 只作**结构演示与口径证明**，
> **不构成生产基线**；生产取值须显式传 `--events-dir` / `--shadow-dir` / `--trace-db`
> （脚本缺省**不触碰**运行时目录，也不会创建任何目录）。
> S5-03 的**触发条件已解锁**（口径、数据源、命令、验收标准均已定义），
> 但**校准本身未执行**（真实成本样本未达每模型 ≥20 条）。

### 4.4 §6.7 周报（数据源逐项可追溯）

```
$ python scripts/report_slo_weekly.py --json --out data/reports/slo_weekly.json --md data/reports/slo_weekly.md
| 指标 | 值 | 单位 | 目标 | 达标 | 样本 | 状态 | 数据源 | 公式 |
| 消化吞吐 | … | 能力/周 | ≥2/周（W10 起） | … | … | … | agent.observability.events（EV_DIGEST_STAGE…） | count(distinct …) × 7 / 窗口天数 |
| 内化转化率 | … | 比率 | ≥10% | … | … | … | agent.descriptors.registry.DescriptorRegistry | (n_internalized + n_native) / n_total |
| …（共 11 项，8 项可计算）…
```

报告头三条纪律逐字输出：数据源缺位记 `None`（不以 0 冒充）；样本 <20 只披露不考核；
每个数字带数据源与公式。

---

## 五、质量证据（本地门禁）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 新增套件 | `pytest tests/unit/test_eval_*.py -p no:randomly` | **300 passed / 0 failed**（1 例 `slow` 默认跳过，`--runslow` 可跑：实测 4.32s 通过） |
| 覆盖率 | `--cov=agent.eval` | **93%**（TOTAL 2075/145；逐模块 89–100%） |
| 邻接回归 | `pytest test_acr_metrics, test_utc_cost, test_events_v1, test_s2_03_integration, test_trace_v2, test_trace_v2_integration, test_digestion_{shadow,gate,internalize,stage}, test_s3_01_handover` | **635 passed / 0 failed / 4 skipped** |
| 广域回归 | `pytest tests/unit -m "not slow" -k "eval or digestion or descriptors or skills_mgmt or approval or trace or audit or events or orchestrator or tool_calling or metrics or utc or acr"` | 见 §5.1 |
| 全量抽查 | `pytest tests/unit -m "not slow" -p no:randomly` | 见 §5.1 |
| 自检门 | `python scripts/check_eval_datasets.py` | **四层全绿**（锚完整性 + 参考解全过 + 逐条判定区分度 + 变异解无幸存） |
| kwarg 冲突扫描 | `scan_kwarg_conflicts.py --path agent --min-risk HIGH` | **0 处** |
| kwarg 冲突扫描 | `scan_kwarg_conflicts.py --path tests --min-risk HIGH` | **0 处** |
| mypy（新增模块） | `mypy agent/eval/ --ignore-missing-imports --follow-imports=silent` | **0 error** |
| mypy（既有阻塞模块） | `mypy agent/env_config_manager.py …` / `mypy agent/network_config.py …` | **Success: no issues found** ×2 |
| importlinter | `lint-imports --config .importlinter` | **2 kept / 0 broken** |
| 架构规则校验 | `python -m agent.observability.arch_rules --check --root agent --exemptions … ` | **passed=True｜未豁免违规 0**（存量 4 项均已豁免；本任务曾报 4 项，见 §5.3） |
| 产物漂移 | `git status --short` | 仅本任务新增文件 + `.gitignore`（1 行区块）；门禁产物已还原，见 §5.2 |

### 5.1 广域回归 / 全量抽查结果

**广域回归**（`pytest tests/unit -m "not slow" -p no:randomly -k "eval or digestion or
descriptors or skills_mgmt or approval or trace or audit or events or orchestrator or
tool_calling or metrics or utc or acr"`）：

```
= 3527 passed, 6 skipped, 10901 deselected, 13 xfailed, 13 warnings in 758.08s (0:12:38) =
```

**全量抽查**（`pytest tests/unit -m "not slow" -p no:randomly`）：

```
= 14123 passed, 51 skipped, 256 deselected, 13 xfailed, 4 xpassed, 34 warnings in 1975.39s (0:32:55) =
```

（`FAILED` / `ERROR` 汇总行 **0 条**；`4 xpassed` 为既有用例在本地环境的预期外通过，
与本任务无关。）

> **性能修复（本任务实测得到）**：首次全量抽查暴露"逐符号 `os.walk` 全树遍历"在并发
> 负载下把单条 L2 用例推到 pytest 超时（`checkers.check_symbols_exist`）。
> 已改为**剪枝 + 语料缓存**（一次遍历把候选文件读入内存语料，之后纯内存子串检索；
> 剪枝运行时/产物/缓存/依赖目录，设文件数 20000 与单文件 1MB 上限）：
> 本机实测语料构建 **1.20s / 4091 文件**，首次检索 1.23s、后续检索 **0.02s**
> （原实现每个新符号一次全树遍历）。修复后 `test_eval_checkers + test_eval_datasets`
> 由超时变为 **6.00s 全绿**。

### 5.2 运行时区与产物纪律

* 新增单测**全部**以 `tmp_path` + `monkeypatch.setenv(CP_EVENTS_DIR/CP_EVAL_ANCHOR_DIR)`
  隔离事件流与锚目录（`autouse` fixture），**不写** `data/`、`data/digestion/`、
  `data/feedback/`（`test_compute_metrics_does_not_touch_data_dir` 等断言"不创建目录"）；
* `data/eval/`（基线快照 / 拟合件）已加入 `.gitignore`，运行期产物不进入提交；
* 锚目录 `eval/l0_anchor/` 由 `guard_write` 守门：基线 / 拟合件 / 周报**无法**写进锚；
* 门禁后 `git status` 复核（见 §5.2 结论），未出现 tracked 文件漂移
  （`check_boundary_coverage.py` 曾改动 `docs/observability/boundary_coverage_report.json`，
  已 `git checkout` 还原；`clean_runtime_noise.py` 还原 `data/learned_workflows.json` 统计漂移）。

### 5.3 实现期真实问题（3 项，均已闭合）

| # | 问题 | 发现方式 | 处置 |
|---|---|---|---|
| 1 | **逐符号全树遍历**导致 L2 用例在并发负载下超时（`os.walk` 每个新符号一次全树） | 首次全量抽查 `FAILED test_eval_datasets.py::test_reference_covers_all_and_passes`（pytest timeout 120s） | 改为**剪枝 + 语料缓存**：一次遍历入内存语料（实测 1.20s / 4091 文件），后续检索 0.02s；相关套件由超时 → 6.00s 全绿 |
| 2 | **变异解区分度不足**：4 条 L2 `contains_all` 在"只删第一处片段"的变异下仍通过（短片段在别处再现） | 自检门 `check_eval_datasets.py` 的逐条判定负样本对照 | 变异策略改为"删除该片段的**全部**出现"，幸存数归零 |
| 3 | **架构规则校验失败**：`from agent.eval import x` 被 AST 依赖图记为依赖包根，而包根导入子模块 → 判为循环依赖（4 处未豁免违规） | CI「架构规则校验」工作流（阻断合并） | 包内改为 `import agent.eval.<mod> as X`；**并新增回归守护**（`TestPackageHygiene` 用 AST 扫描禁止该写法）；重跑 arch_rules → passed=True、未豁免 0 |

> 这三项都不是"跑绿一次就完"，而是被**机器门禁主动抓出来**的：① 全量抽查、② 自检门、
> ③ CI 架构规则。它们同时也是本任务"标尺可信 + 门禁可信"的实例证据。

---

## 六、遗留问题与人工动作（逐条带归属与阻塞性）

| # | 事项 | 归属 | 阻塞性 | 说明 |
|---|---|---|---|---|
| 1 | **L0 锚的 Owner 人工复核/背书**：当前 `frozen_by` 为"执行会话代行冻结" | **Owner** | 不阻塞后续开发 | 任务书要求"人工冻结"；执行会话已按 §6.5 逐条自查（每类 ≥2、机械可验、参考解自洽），请 Owner 复核后重新署名冻结（`freeze_eval_anchor.py --confirm-freeze --reviewer <Owner>`），锚哈希会随之更新并需重写 release manifest |
| 2 | 真实**能力基线**缺失：无真实 LLM 凭证 → L0/L1/L2 的 `pass_rate` 目前是**参考解自检** | 后续（真实解算器接入） | 不阻塞 | 接入方式已备：`--solver file:answers.json`；届时基线须重新固化并标注 `solver` |
| 3 | L2 基线的生产取值未采（本机台账为空/含测试噪声） | S5-03 + 运维 | **部分阻塞 S5-03 校准** | 触发条件已解锁；实际校准需真实成本样本 ≥20/模型（`baseline.cost_inputs` 已给缺口清单） |
| 4 | `delegation_recovery`（委派回收率）无数据源 | **S4-04** | 不阻塞 | 行契约与计算函数已交付（`delegation_recovery_contract()`），P0 接线后即可计算 |
| 5 | MTTD/MTTR 无发射方（`healing.triggered` 已登记未发射） | **S4-03** | 不阻塞 | 计算函数与目标判定已交付，事件落地即可取值 |
| 6 | 路由准确率需"事后最优"标注源 | 后续（人工/结果回测） | 不阻塞 | 标注契约 + 计算函数已交付（`routing_annotation_contract()`）；未标注时返回 `framework_only`，**不得**声称已有路由准确率 |
| 7 | L2 中 2 条**代理口径**用例（词表 rubric） | S5-02 自留 | 不阻塞 | 已在用例 `notes`、报告与基线 `verdict_counts` 中披露；后续接 LLM 裁判时替换为 `mechanical` 或正式裁判口径 |
| 8 | L3 Golden-80 用例未产出 | M7+ | 不阻塞 | 框架 + 晋升规则 + 运行器已就绪 |
| 9 | ACR 难度权重切换未执行（**刻意**） | S5-02 交付路径 / 后续执行 | 不阻塞 | 四闸未全绿（开关默认关闭 + 样本不足）→ 保持"只披露不考核"；清单见 `switch_checklist()` |

**上游遗留接收情况（S2-03 #3 / #4）**：#3 已在 §二.8 交付切换路径与拟合器（默认不切换）；
#4 已在 §二.9 交付双列正式口径与替代关系披露。两项均**不再悬空**。

---

## 七、口径与诚实性自检（本报告不出现的数字）

* 本报告**不出现**"已实现真实能力内化""模型通过率 X%"之类结论 ——
  真实流量未达"每能力 ≥20 条同类轨迹"，且无真实解算器；
* 本报告**不把 `unassessed` 计入通过率**，也没有在任何地方把参考解成绩写成模型成绩；
* 全部指标的分子/分母/样本量/数据源/公式均随 `--json` 输出一起落盘，可逐条复核；
* L2 的 2 条代理口径用例、L3 的框架占位、S5-03 的"触发已解锁但校准未执行"三处
  均在报告与代码内**双处**披露。
