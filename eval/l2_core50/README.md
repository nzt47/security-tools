# L2 Core-50 —— UTC 基线唯一依据（TASK-S5-02 / v7.2 §6.5）

> 契约与判定口径见 [`agent/eval/cases.py`](../../agent/eval/cases.py) 与 [`eval/README.md`](../README.md)；
> 本目录只放**数据**：`cases.json`（用例集）+ `reference.json`（参考解）。

## 一、规模与场景分布（冻结口径）

| 项 | 值 |
|---|---|
| 条数 | **50**（契约值，`LAYER_SIZES["L2"] = 50`） |
| 场景 | `S1_fix_bug` **20** ／ `S2_codebase_qa` **15** ／ `S3_commit` **15**（三类**种子场景**） |
| 判定条目 | **212** 条（每条用例 1–8 条，全部通过才算 pass） |
| 判定口径 | `mechanical` **48** ＋ `proxy` **2**（词表 rubric，已在 `notes` 披露） |
| 用例集哈希 | `c42870ea6d4514d22d9afa6fce7252c1b1187a3a3701b7530ad8e5d4a8e7a8d8` |
| 参考解 | 覆盖 50/50；`--solver reference` → 50/50 pass；`--solver mutant` → 0/50 pass |

判定器使用分布：`python_probes` 20 · `not_contains` 48 · `path_exists` 45 ·
`length_between` 27 · `symbols_exist` 15 · `contains_all` 15 · `commit_message` 15 ·
`paths_exist` 15 · `all_items` 9 · `rubric_keywords` 2 · `contains_any` 1。

## 二、三类种子场景的构造原则

### S1 修 bug（20 条）——`python_probes` 机械执行

每条给出**一段有缺陷的纯 Python 函数**（只用内置函数，不 import），要求候选产出修好的
完整源码 `{"code": …}`；判定在**受限命名空间**内执行该源码并跑 ≥3 个探针
（含边界/负例，必要时断言 `raises`）。20 条覆盖 20 类不同缺陷：

> 切片 off-by-one ／ 空输入守卫 ／ `True` 与 `1` 混淆 ／ 可变默认参数 ／ 除零语义 ／
> 字符串切片边界 ／ 排序稳定性 ／ `setdefault` 未回写 ／ 递归基线 ／ 浮点舍入 ／
> 布尔算术 ／ 异常吞掉 ／ 闭区间写成开区间 ／ 去重不保序 ／ 负数取模 ／ 最小值初始化 ／
> 大小写敏感 ／ 整除代替真除 ／ 提前返回 ／ 迭代器被消费两次

### S2 懂代码库（15 条）——**反幻觉**机械判定

答案工件形如 `{"answer", "files", "symbols", "facts"}`；判定要求：
给出的**文件路径真实存在**（`path_exists` + 可选 `symbol`）、提到的**符号真实存在**
（`symbols_exist`）、**关键论断逐项相等**（`json_subset` / `list_ordered` / `contains_all`）。
问的都是本仓库**真实定义**：ACR 七项权重、`EvolutionStage` 七态、`events.v1` 信封、
`p99_wall_*` 的两处定义、`eval.cases.v1` 契约、判定器登记表、执行器四态、
解算器四角色、L0 锚三机制、验收门四条件、descriptor 合并阈值、事件层上限常量等。

> 其中 **2 条为 `proxy`**（`L2-S2-10` / `L2-S2-13`）：其"解释覆盖度"用词表 rubric 判定
> （**代理口径，非 LLM 裁判**），且各自保留 `path_exists` / `contains_all` 等**机械佐证**；
> 披露见用例 `notes` 与基线快照的 `verdict_counts`。

### S3 提交（15 条）——结构机械校验

给出变更摘要与真实存在的文件清单，要求产出 `{"message", "files"}`；判定用
`commit_message`（`type(scope): subject` 形态、type/scope 白名单、subject 长度、
正文必需片段）、`paths_exist`（路径必须真实）、`not_contains`（禁止 `--force` / `rm -rf` 等）。

## 三、与 UTC 基线的关系（为什么是"唯一依据"）

`agent/eval/baseline.py::build_l2_baseline()` 把本数据集与三路真实数据源装配为一份可复核快照：

| 基线分片 | 数据源 | 口径要点 |
|---|---|---|
| `caseset` | **本目录 `cases.json`** | 条数 / 场景分布 / 哈希（换版即基线失效） |
| `performance` | `agent.eval.runner` | 用例耗时 p99，**clock = wall_clock(perf_counter)** |
| `shadow_inputs` | `ShadowLedger.rows()` / `daily_average()`（S3-03） | **真实墙钟** p99 与能力级样本量趋势 |
| `trace_inputs` | `UnifiedTraceStore.snapshot_stats()`（S2-01） | 能力级同类轨迹数（≥20 门槛） |
| `cost_inputs` | `utc.utc_window()`（S2-03） | UTC = 归一成本 / 任务数（直接透传，不重算） |
| `sample_adequacy` / `calibration_trigger` | 上述之和 | 样本缺口清单 + **Owner 裁定 C 触发条件状态** |

```powershell
python scripts/run_eval.py --layer L2 --write-l2-baseline data/eval/l2_baseline.json
python scripts/run_eval.py --layer L2 --record-baseline       # 固化判定器自检基线
```

## 四、诚实边界

* 本目录的 `reference.json` 是**参考解**：只用于**判定器自检**与**变异解区分度对照**，
  **不代表任何模型能力**。真实能力基线须由被测解算器产出答案工件后另测：
  `python scripts/run_eval.py --layer L2 --solver file:answers.json`。
* 缺省解算器为 `null`（无凭证时的诚实回落）：50 条全部 `unassessed`，
  **不计入通过率分母**（`pass_rate = n/a`），而不是记 0 分。
* "L2 就绪"≠"成本系数校准已完成"：触发条件解锁后，校准仍需真实成本样本 ≥20/模型。
