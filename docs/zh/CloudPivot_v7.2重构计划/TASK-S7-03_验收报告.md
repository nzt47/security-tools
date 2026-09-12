# TASK-S7-03 验收报告 —— 成本系数校准（把"价格锚定"升级为"实测校准"）

> 任务书：[TASK-S7-03_成本系数校准.md](TASK-S7-03_成本系数校准.md)｜分发壳：[START-S7-03_成本系数校准.md](START-S7-03_成本系数校准.md)
> 批次总表：[PARALLEL_S7批次总表.md](PARALLEL_S7批次总表.md)｜基线 `master` / `531515e0`｜worktree `s703`
> 上游设计：v7.2 §6.2 UTC 刹车 / §6.6 成本埋点 / §6.7 指标字典 / §6.8 模型矩阵
> 提交：`s703/main` → **`80133139` → `f1d3c75c` → `ac2e9eac` → `84da91cc` → `2e1d4a33`**｜合并提交 **`1dfefd57`**｜双远端终态 **`324ac202`**｜验收日期：**2026-09-12**

---

## 〇、一句话结论（先说清楚"完成到什么程度"）

**校准管线已完整交付并机器可验；但"把口径升级为实测校准"这一步在本机
未完成——因为既无可用多模型凭证（路径 A 无法执行），历史成本样本（12 条）
也远低于每模型 20 条的门槛。** 因此按口径纪律：**只披露不结论**，
系数**仍为价格锚定**（`calibration_version=price_anchor.v1`、`calibrated=False`），
并在所有输出中**显式声明"未完成完整实测校准，结论置信度受限"**。

这不是"没做完就交差"：任务书 §五 已明确"或如实报告未完成"是**允许的出口**，
且本任务的价值（可复跑的校准设施 + 来源优先级 + 偏差表机制 + 诚实标注）
已全部落地并被 51 例单测锁死。

---

## 一、交付物清单

| # | 交付物 | 落点 | 规模 |
|---|---|---|---|
| 1 | 校准方案（实验设计先行） | `docs/zh/成本系数校准方案.md`（新建） | 样本/变量/指标/对照/复校周期五要素齐备 |
| 2 | 实测校准核心模块 | `agent/observability/cost_calibration.py`（新建） | **678 行** |
| 3 | 校准脚本（路径 A / 路径 B） | `scripts/calibrate_cost_coefficients.py`（新建） | **922 行** |
| 4 | 偏差分析报告（含逐数字溯源） | `docs/zh/成本系数偏差分析报告.md`（新建） | 含偏差表 + 置信度 + 溯源清单 |
| 5 | 来源标注与版本升级 | `agent/observability/utc.py`（改）+ `agent/monitoring/cost_brake.py`（改：口径标签实效透传） | `coefficient_detail()` / `coefficient_table()` / `calibration_block()` / `effective_calibration_labels()` |
| 6 | 新增单测 | `tests/unit/test_s7_03_cost_calibration.py`（新建） | **542 行 / 51 例** |
| 7 | 成本口径文档更新 | `docs/PERF_BUDGET_REBASED.md` §九（改） | 逐模型标注"已实测/仍是价格锚定" |
| 8 | 运行时产物不入库 | `.gitignore`（改） | +5 行（校准件/偏差表/报告中间件） |

---

## 二、任务书 §四 验收清单逐条

### ① 校准方案先行落文档（样本/变量/指标/对照/复校周期齐全）

| 要素 | 方案中的落点 | 值 |
|---|---|---|
| 样本 | §二 | L2 Core-50（50 条用例 / **212 判定条目**，哈希 `c42870ea6d45…`）；48 机械 + 2 proxy（后者只计数、不作能力结论） |
| 变量 | §三 | 锚模型（`resolve_anchor_model()`，本机 `gpt-4`，`anchor_source=config.yaml`）+ **2–4 个**参与模型（主力/备选/本地）；凭证前置检查**必须实探端点**，占位符不算凭证 |
| 指标 | §四 | M1–M8：token(in/out)、单位任务 token、重试、成功率、缓存命中、正常化成本、单位任务正常化成本、用例通过率 |
| 对照 | §五 | `price_coef_effective`（二元组压在**真实 token 构成**上的标量）vs 实测系数 vs 偏差率；**并显式处理"用待校准系数反推"的反身性问题**（校准一律取 `cost_raw_cents`） |
| 复校周期 | §九 | **季度** + 4 条提前触发（单价变更／锚变更／用例集哈希变更／滚动偏差 > 20%） |

**证据**：`docs/zh/成本系数校准方案.md`（本文档与脚本均以其为准绳）。

### ② 有凭证时完成实跑校准；无凭证时走降级路径并**显式声明"非完整实测"**

| 项 | 结果 | 证据 |
|---|---|---|
| 凭证前置检查 | **无可用多模型凭证** | `python scripts/calibrate_cost_coefficients.py --probe-credentials` → `usable_providers: []`、`multi_model_usable: false`；`LLM_API_KEY` 判为 **placeholder**（`sk-t…cdef(len=24)`，命中 `sk-test`）；端点 `GET https://api.deepseek.com/v1/models` 实测 **HTTP 401** |
| 路径切换 | 自动走 **B（降级）** | `--path auto` → `路径=b`（`requested_path` 与 `used_path` 同时记录） |
| 显式声明 | **已在三处出现** | ① 报告首段 `## 〇、**必读：未完成完整实测校准，结论置信度受限**`；② 校准件 `version=measured.partial.v1` + `calibrated=False`；③ `calibration_block()["calibration_note"]` 含"未完成完整实测校准，结论置信度受限" |
| 不得含糊 | 退出码与措辞都可机器验证 | 退出码 **3**（样本全不足）；工件**默认不写**（`--write` 才写，且无达标模型时拒绝写） |

**反证（不得用价格系数冒充实测）**：
`CC.MEASURED_PARTIAL_VERSION != CC.MEASURED_VERSION`，
且 `CostCalibration.calibrated` 仅在 `version == measured.v1` **且存在达标的参与模型**时为真
（单测 `test_partial_artifact_is_not_claimed_as_measured`、`test_degraded_build_is_partial_version`）。

### ③ 偏差表给出每模型：价格系数 / 实测系数 / 偏差率 / 样本量 / 置信度

实测输出（`docs/zh/成本系数偏差分析报告.md` §一）：

| 模型 | 价格系数(有效标量) | 实测系数 | 偏差率 | 样本量 | 任务数 | 置信度 |
|---|---|---|---|---|---|---|
| `gpt-4`（锚） | 1.0 | 1.0 | +0.00% | 6 | 6 | `insufficient` |
| `m` | 0.333333333 | —（样本不足） | — | 6 | 6 | `insufficient` |

**证据命令**：

```powershell
python scripts/calibrate_cost_coefficients.py --path auto --models gpt-4,m `
    --events-dir data/events --report docs/zh/成本系数偏差分析报告.md `
    --json-out data/calibration_table.json
```

### ④ 样本 <20 → 只披露不结论（复用 S5-02 口径）

| 项 | 实现 | 断言 |
|---|---|---|
| 门槛值 | `MIN_COST_SAMPLES_PER_MODEL = 20`（**与 `agent/eval/baseline` 同值**） | `test_confidence_is_mechanical` |
| 不给系数 | `insufficient` 行的 `measured_coefficient` / `deviation` **一律 `None`** | `test_insufficient_samples_disclosed_not_concluded` |
| 不进工件 | `insufficient_models` 只披露，`models` 为空 | 同上 |
| 分母也管 | 锚模型样本 <20 → **所有**实测系数不可用 | `test_anchor_itself_insufficient_blocks_everything`／`test_anchor_absent_from_sample_is_disclosed` |
| 不点名 | §1.1 点名规则要求"达标 **且** |偏差| ≥30%"；本批达标数为 0 → 明确写"**无**（不因样本不足给结论）" | 报告 §1.1 |

### ⑤ `coefficient_table()` 来源标注与优先级生效；`calibration` 版本正确

| 项 | 实现 |
|---|---|
| 来源枚举 | `source ∈ {override, measured, price_ratio}` |
| 优先级 | **`CP_UTC_COEFFICIENTS`（override）> 实测件（measured）> 价格锚定（price_ratio 回落）**，且**逐模型**判定 |
| 生效依据 | 每行带 `origin`（`env:CP_UTC_COEFFICIENTS` / `artifact:<路径>` / `price_anchor:MODEL_COSTS`） |
| 对照列 | 每行带 `price_coefficient`（同模型价格锚定值），便于逐项比对偏差 |
| 全局汇总 | `coefficient_sources`（按模型）+ `measured_models` |
| `calibration` 块 | `calibrated` / `calibration_version` / `measured_models` / `calibrated_at` / `calibration_method` / `calibration_partial` / `coefficient_sources` / `stale_reason` |

**本机实测（升级后仍与升级前同口径）**：

```
coefficient_sources.by_model = {"gpt-3.5-turbo": "price_ratio",
                                "gpt-4": "price_ratio",
                                "gpt-4o-mini": "price_ratio"}
calibration_version = "price_anchor.v1"｜calibrated = False｜stale_reason = "no_artifact"
```

**断言**：`test_default_is_price_ratio_and_matches_legacy`、`test_measured_artifact_takes_effect`、
`test_override_beats_measured`、`test_unmeasured_model_falls_back_per_model`、
`test_table_carries_price_side_for_comparison`、`test_measured_artifact_raises_version`。

### ⑥ **历史口径不追溯**（旧数据仍按其原版本可查，用例断言）

| 机制 | 说明 |
|---|---|
| 事件自带口径 | 每条 `cost` 事件写入时就带 `coefficient_in/out`、`coefficient_source`、`anchor_model`、`cost_normalized_cents` |
| 聚合不重算 | `utc_daily/utc_window/utc_weekly/utc_snapshot` 均为"读事件字段求和"，**没有任何按当前系数重算历史**的代码路径 |
| 模块不回写 | 校准模块不修改任何历史事件文件 |

**三条断言**：
`test_historical_event_keeps_its_own_coefficient`（改系数后历史聚合值**分毫不变**）、
`test_new_events_use_new_calibration`（新系数只影响**生效之后**的写入：旧 0.015 + 新 3.0 = 3.015）、
`test_module_never_rewrites_event_files`（事件文件字节级不变）。

### ⑦ 未编造任何数字（每个数字可溯源到事件流或导入 CSV）

| 机制 | 说明 |
|---|---|
| 唯一入口 | 校准只吃 `SampleRow`（带 `source_path` + `source_line`），不联网、不读盘、不插值 |
| 溯源输出 | 报告 §2.1 列事件文件与字节数；指标表 `数据来源` 列给出**首个** `文件:行号` |
| 无样本即"—" | 缺任务 → 单位任务成本 `None`；缺偏差 → `—`；**不以 0 或估计值顶替** |
| CSV 纪律 | 行级解析失败 → 跳过并计数（原因分布进报告），**不补零、不插值** |
| 自证 | 报告 §3.1 明确"全部数字由 `cost_calibration.py` 从样本行导出，无任何人工填写或估计值" |

**断言**：`test_replay_reads_only_cost_events_with_provenance`（非 `cost` 事件与重复 `event_id` 均排除）、
`test_csv_import_skips_bad_rows_and_counts`、`test_deviation_math_and_none_guards`
（`None` 一律传播为 `None`，**不以 0 顶替**）。

### ⑧ `utc`/`acr`/`cost_brake` 邻接套件零回归；新增单测全绿、覆盖率 ≥80%

| 套件 | 结果 |
|---|---|
| `tests/unit/test_s7_03_cost_calibration.py`（新增） | **51 passed** |
| `tests/unit/test_utc_cost.py` | **37 passed** |
| `tests/unit/test_acr_metrics.py` + `tests/unit/test_s5_03_cost_brake.py` | **158 passed** |
| `tests/unit/test_eval_baseline.py` + `tests/unit/test_eval_datasets.py` | 并入下方合计 |
| **合计（六个相关/邻接套件）** | **316 passed / 1 skipped / 0 failed** |
| 覆盖率 `agent/observability/cost_calibration.py` | **91%**（≥80% 达标） |
| 覆盖率 `agent/observability/utc.py` | **85%**（≥80% 达标） |

---

## 三、质量证据（门禁）

| 门禁 | 命令 | 结果 |
|---|---|---|
| kwarg 扫描 #1 | `python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH` | **0 处**（HIGH/MEDIUM/LOW 全 0），exit 0 |
| kwarg 扫描 #2 | `python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH` | **0 处**，exit 0 |
| mypy（改动模块） | `python -m mypy --follow-imports=silent agent/observability/utc.py agent/observability/cost_calibration.py` | **Success: no issues found in 2 source files**（且**顺手清掉 `utc.py` 的 3 个既有报错**：master 上 3 error → 本分支 0 error） |
| importlinter | `python -m importlinter.cli lint-imports` | exit 0（2 kept / 0 broken） |
| 架构规则 | `python -m agent.observability.arch_rules --check` | exit 0；4 处 `no_circular_dependency` **均为既有豁免**，本任务新增 0 处违规 |
| pre-commit（真实提交场景） | `git commit`（钩子自动跑） | ✅ 提交成功；钩子同时执行 `clean-runtime-noise` → `data/learned_workflows.json: 3 条统计漂移，已还原` |
| 产物漂移 | 提交后 `git status --short` | **空**（无 `??`、无 `M`）——校准运行时产物已入 `.gitignore` |

---

## 四、实现期间发现并处置的问题（9 项，不隐瞒）

| # | 问题 | 处置 |
|---|---|---|
| 1 | **偏差表把锚模型标成 `+100.00%`**（把"自比偏差"误填为 1.0） | 锚行 `deviation` 固定 `0.0`，并注明"不参与点名"；单测锁定 |
| 2 | **锚模型样本不足时仍被列进 `models`**（会自称"已生效"） | 锚也走同一置信度判定；不足则只进 `insufficient_models` |
| 3 | **锚不在样本里时"分母不存在"未被披露**（偏差表会缺一行） | 显式登记 `samples=0` 的锚行 + 披露"分母不存在"；单测锁定 |
| 4 | **分母不达标但分子达标时仍算出系数**（最危险的一类静默失真） | 增加 `anchor_ok` 判据（分子/分母/比值三者全绿才给系数）；单测锁定 |
| 5 | **过期校准件被报告成"已校准"**（文件写着 `calibrated=true` 却没生效） | `_artifact_active()` 统一裁决：过期 → 一律按"回落价格锚定"报告，并给出 `stale_reason` |
| 6 | **`--path a` 的提示渲染拿不到用例输入**（`EvalCase` 的字段是 `id`/`input`，脚本初版按 `case_id` 取值） | 改为按契约字段渲染并兼容别名（已用真实 L2 用例验证） |
| 7 | **凭证探测看不见 `.env`**（项目不自动加载 `.env`，会误判"没有端点/没有密钥"） | 增加只读 `.env` 解析（`CP_ENV_FILE` > worktree > **主工作区**），且**绝不注入进程环境** |
| 8 | **报告泄露绝对路径**（可读性与可移植性差） | 溯源统一转仓库相对路径（优先主工作区根） |
| 9 | **S5-03 侧口径标签写死**：`cost_brake.py` 把 `calibration_version/note` 固定为 `price_anchor.v1`——一旦有生效实测件，同一指标会同时出现"**系数按实测算**"与"**口径标注说价格锚定**"两种说法（正是本任务要消灭的静默失真） | 新增 `cost_brake.effective_calibration_labels()` 从 `utc.calibration_block()` **实效透传**（本模块仍不做系数计算，口径唯一源头保持 `utc`）；四处输出改用实效值；`status()` 每次读取刷新（进程存活期间换版也如实上报）；`cost_brake` 107 例零回归 |

---

## 五、遗留与阻塞

| # | 项 | 归属 | 阻塞性 |
|---|---|---|---|
| 1 | **完整实测校准未执行**：需 ≥2 个参与模型的可用凭证 | Owner / 运维（提供凭证）→ 执行方复跑 `--path a` | **不阻塞本任务验收**（任务书允许"如实报告未完成"），但**阻塞"口径升级为实测"**；复跑命令见报告 §四 |
| 2 | 真实成本样本量：需每模型 **≥20** 条 L2 用例级 `cost` 记录 | 与 #1 同批产生（路径 A 一次跑完即达标） | 同上 |
| 3 | 历史 `cost` 事件全为**开发/测试流量**（8、7 个 input token、无 output、`m` 全报错） | 运维（生产流量接入后自然改善） | 不阻塞；已在本报告与偏差报告显式标注"不代表真实单位任务成本" |
| 4 | 本地模型成本（机时/能耗折算）**未落地**（本机无可跑本地模型） | 后续（有本地模型时按校准方案 §六 单列） | 不阻塞 |
| 5 | L2 用例集哈希 → 校准件失效联动，依赖 `data/eval/l2_baseline.json` 存在 | 运维（跑 `run_eval.py --write-l2-baseline` 后可全链生效） | 不阻塞（读不到时**跳过**哈希判定，不误判过期） |

---

## 六、验收签署（待 Owner 确认）

| 验收项 | 结论 |
|---|---|
| 任务书 §四 八条 | **8/8 满足**（其中"完成实跑校准"一条按任务书许可走"如实报告未完成 + 显式声明"出口） |
| 口径纪律 | 未编造任何数字；样本不足只披露不结论；历史口径不追溯；降级声明明确 |
| 邻接零回归 | ✅ 316 passed / 0 failed（六个套件）；`tests/unit` 全量 **16473 passed / 11 failed**，11 项失败**全部与未改动 `master` 逐例同现或可环境解释** ⇒ **0 回归**（逐例比对见交付结案报告 §六） |
| 双远端 | 见 `S7-03_交付结案报告_20260912.md` |

**结论：本任务**技术交付完整、口径诚实**；"实测校准"本身因外部条件（凭证 + 样本）
未完成，已在全部输出中如实标注，并有可一键复跑的完整路径。**
