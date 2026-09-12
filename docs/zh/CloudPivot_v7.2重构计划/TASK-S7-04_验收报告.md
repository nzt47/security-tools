# TASK-S7-04 验收报告 —— 验证与探活补全（性能补测 + native 期探活设施）

> 任务书：[`TASK-S7-04_验证与探活补全.md`](TASK-S7-04_验证与探活补全.md)｜批次：[`PARALLEL_S7批次总表.md`](PARALLEL_S7批次总表.md)
> 基线：`master` / `8f22974f`（批次总表记 `531515e0`；该 commit 之后 master 只前进了一个**纯文档**提交，本任务落点未受影响）｜worktree id：`s704`
> 交付日期：2026-09-12｜预估：4–6 人日
> 来源：收官审计 #4（4 项性能无实测）+ #3（native 期探活未定义）

---

## 零、结论先行

| 子项 | 结论 | 一句话 |
|---|---|---|
| **A 性能补测** | ✅ **完成**（唯一遗留：Watchdog「恢复」侧如实未测） | `PERF_BUDGET_REBASED.md` **§二 表的 ❓ 已清零**（19 个指标行一个没删）；剩余两项在受控环境实测出**原始毫秒级采样**，未测的一项写明测法与环境要求 |
| **B native 期探活设施** | ✅ **完成** | `agent/digestion/probe.py` 落地 `LivenessProbe` / `evaluate_liveness` / `suggest_stage_rollback` / `register_liveness_job`；四项退化条件**各自可触发**；退化**只出建议、不自动改 stage**；事故卡**六要素齐备**；调度**默认关闭**；无 native 能力时**如实返回空** |

**一句话总括**：本任务把"**没验证的验证掉**"（子项 A）与"**未来的闸门先建好**"（子项 B）
都推进到了**可机器验证**的状态，且**没有编造任何一个数字**——包括"测不出"这件事本身，
也是如实登记 + 给出测法，而不是拿估算值填坑。

---

## 一、交付物

| # | 交付物 | 路径 |
|---|---|---|
| 1 | 性能复测脚本（可复跑、输出原始采样） | `scripts/measure_perf_budget.py` |
| 2 | 性能预算文档（实测值 + 采样方法 + 环境说明） | `docs/PERF_BUDGET_REBASED.md`（新增 §九；§一/§二/§五/§六 同步） |
| 3 | 探活设施 | `agent/digestion/probe.py` |
| 4 | 探活设施导出 | `agent/digestion/__init__.py`（新增 26 个公开符号） |
| 5 | 探活**演示**脚本（产出演示记录，可复跑） | `scripts/demo_s7_04_probe.py` |
| 6 | 新增单测（子项 A） | `tests/unit/test_perf_budget_measure.py`（26 例） |
| 7 | 新增单测（子项 B） | `tests/unit/test_digestion_probe.py`（46 例） |
| 8 | 原始采样与证据 | `reports/s7_04/perf_budget_probe.json`、`reports/s7_04/probe_demo.json`、`reports/s7_04/probe_demo.txt`、`reports/s7_04/cov_probe.json`、`reports/s7_04/kwarg_agent.json`、`reports/s7_04/kwarg_tests.json` |
| 9 | 本验收报告 | `TASK-S7-04_验收报告.md` |
| 10 | 交付结案报告 | [`S7-04_交付结案报告_20260912.md`](S7-04_交付结案报告_20260912.md) |
| 11 | 总览状态行 | [`00_总览_审计结论与重构总计划.md`](00_总览_审计结论与重构总计划.md) §4.3（新增「批 6（S7）」进展行） |

---

## 二、子项 A 验收（§四 子项 A 清单逐条）

### A-1 ✅ Watchdog 感知时间有**实测值 + 采样方法 + 环境说明**

**两个口径分别实测、分别披露**（**不合并成一个数字** —— 合并会掩盖"释放点判定"这一结构事实）：

| 口径 | 采样方法 | p50 | p95 | max | 预算 10 s |
|---|---|---|---|---|---|
| **W-1 持锁超时感知**（§五 指定方法） | 受控注入 `WatchedLock` 持锁 **3 s**（> `LOCK_WATCHDOG_HOLD_MS=2000`），测"持锁开始 → 告警回调触发" | **3000.9487 ms** | **3002.3624 ms** | 3002.3624 ms | ✅ 余量 ≥3.3× |
| W-1 的**纯判定开销** | "释放锁 → 告警回调触发"（把持锁时长与判定成本分开） | 0.3869 ms | 1.3261 ms | 1.3261 ms | — |
| **W-2 主进程失联感知** | 本脚本自起**桩进程**持 `WatchdogSingleton` 锁 → `terminate(桩)` → 100 ms 周期探测 `is_stale()`，测"失联 → 判陈旧" | **6.0266 ms** | **9.9044 ms** | 9.9044 ms | ✅ 余量 ≈1009× |

- **采样方法**（可复跑）：`python scripts/measure_perf_budget.py --repeat 20 --out reports/s7_04/perf_budget_probe.json`
- **环境说明**：Windows-10-10.0.19045-SP0｜Python 3.12.0｜12 逻辑核｜**`time.perf_counter()` 单调墙钟**｜进程内受控环境（临时目录 + 桩实现，**不 kill 生产进程、不读写运行时台账**）｜n=20
- **原始采样**（非估算、非引用历史）：`reports/s7_04/perf_budget_probe.json` 的 `samples_ms` 全量数组；`docs/PERF_BUDGET_REBASED.md` §九 如实抄录

**【如实标注的缺口】Watchdog「恢复」侧未实测**（不是"忘了测"）：

§11.2 的硬性指标是"主进程失联 → **感知 / 恢复**时间"，本任务实测的是**感知**。
"拉起/恢复"（L3 `kill → 重启`）**需要真实服务进程**，在单机受控环境下无法安全注入
（任务书硬约束："**不得 kill 真实生产进程**"）。故：

- **不编造**该数字；
- 在 §二 #9 状态栏标 `⚠️ 恢复未测`，在 §五 保留该指标项并写明**测法与环境要求**（5 步：隔离环境部署可重启服务 → 注入 SIGKILL → 记 `t_kill → l1_process 恢复` → n≥20 取分位 → 回填）。

### A-2 ✅ 熔断「达阈值 → 生效」时延有实测

**走真实调用入口** `CircuitBreaker.call()`（不是直接改状态字段）：以进程内抛错桩连续失败达阈值
（`failure_threshold=0.3`、`min_calls=5`），再经同一入口发起下一次 outbound：

| 口径 | p50 | p95 | max | 预算 3 s |
|---|---|---|---|---|---|
| 阈值达成 → `state == OPEN`（状态跃迁） | **0.0015 ms** | **0.0035 ms** | 0.0035 ms | ✅ |
| **阈值达成 → 下一次 outbound 被实际阻断**（主口径） | **0.0176 ms** | **0.0418 ms** | 0.0418 ms | ✅ 余量 ≈7×10⁴ |
| 每次采样：失败次数至熔断 / 是否真的被阻断 | 全部 = **5** / 全部 = **true**（20/20） | — | — | 与阈值语义一致 |

**口径边界如实披露**：跨进程/跨节点的**配置下发**时延需集群环境、**单机测不出** ⇒
单独标注，**不并入**本数字（§九 9.2 边界 1）。

### A-3 ✅ 复测脚本可重复运行，输出**原始采样**（非估算 / 非引用历史）

- 单跑：`python scripts/measure_perf_budget.py --items watchdog --repeat 20`（**无需外部依赖**）；
- JSON 产物含**每个口径的 `samples_ms` 全量数组**（不是只有汇总值）；
- **可重复性证据**：本任务连续跑了两轮完整测量（smoke `--repeat 3` 与正式 `--repeat 20`），
  分位值稳定（正式轮 W-1 p50 3000.9487 ms，与第一轮 3000.9912 ms 相差 < 0.05%）；
- **单测覆盖"可跑 + 结构 + 单位"**：`tests/unit/test_perf_budget_measure.py`（26 例）断言
  `samples_ms` 原文保留、`clock` 口径字段、`min/p50/p95/max` 单位（ms）、未知项显式报错。

### A-4 ✅ 未删除任何既有指标项（只允许"填实测"或"标测法"）

机器核验（`git show master:docs/PERF_BUDGET_REBASED.md` vs 现状）：

```
master 指标行 19  现状 19
缺失的指标项: []
❓ 残留: []
```

→ **§二 表的 19 个指标行（#1–#18 含 #7b）一个没删**，原有 4 项 ❓ 全部按"填实测"处理，
**唯一仍未实测的是 Watchdog「恢复」侧**——它**保留在表内**并标注 `⚠️ 恢复未测` + 写明测法，
**不是删除、也不是拿估算值填**。

### A-5 ✅ 未编造数字；无法测的明确写明原因

- 文档中每一个数字都能追到 `reports/s7_04/perf_budget_probe.json`：
  自查脚本 13 项比对**全部 OK**（分位值 + 原始采样首段逐值匹配，见 §四 质量证据）；
- 脚本层面对"测不出"的**机制化**处理：注入量未达阈值时不产生数字
  （`status="unmeasured"`、`min/p50/p95/max` 全 `None`），并有单测断言
  （`test_hold_below_threshold_yields_unmeasured_not_a_number`）—— **不以 0 冒充**（S5-02/S6-01 口径纪律）；
- Watchdog 恢复侧、熔断集群下发时延：**明确写明"单机测不出" + 测法与环境要求**。

---

## 三、子项 B 验收（§四 子项 B 清单逐条）

### B-1 ✅ `LivenessProbe.run()` 对 `internalized`/`native` 能力跑判定集子集，产出通过率 / 真实墙钟 p99 / 成本增量

- **作用范围**：`PROBE_STAGES = ("internalized", "native")`；`scan_targets()` 经
  `DescriptorRegistry.list_by_stage()` 枚举，**其他 stage 一律 `not_applicable` 且不回放**；
- **判定集子集**：复用 `gate.probe_sample_ids()`（**与 30 天漂移重探同一抽样口径**，不自建第二套），
  默认抽样 **5** 组（`CP_DIGESTION_LIVENESS_PROBE_SIZE` 可配）；
- **真实墙钟 p99**：`ReplaySandbox(measure_wall=True)` 的 `perf_counter` 口径
  （与 S3-03 内化条件⑤同口径），报告 `clock` 字段显式标注；
- **成本增量**：`cost_provider` 注入点（缺省 `None` ⇒ 如实 `(None, "unavailable")`，
  条件③记为"证据缺位"），也为 S7-03 成本校准留出上游接口；
- 驱动证据：`tests/unit/test_digestion_probe.py::TestHealthyProbe::test_run_reports_pass_rate_wall_p99_and_cost`。

### B-2 ✅ 四项退化条件**各自有触发用例**；命中即 `degraded`

| 条件 | 阈值（可配） | 触发用例 |
|---|---|---|
| ① 通过率 < 基线×0.98 | `LIVENESS_PASS_RATE_RATIO=0.98`（与 §4.5.1 条件④同值） | `test_condition_pass_rate_triggered_below_baseline_ratio`（含"恰好等于阈值**不**判退化"的边界用例） |
| ② p99 回归超阈 | `LIVENESS_P99_REGRESSION_RATIO=1.5`（**自己的历史基线**，非上游比对口径） | `test_condition_p99_regression_triggered` |
| ③ 成本显著上升 | `LIVENESS_COST_INCREASE_RATIO=1.3`（与成本刹车断食阈值同值） | `test_condition_cost_increase_triggered` |
| ④ 超周期未成功探活 | `LIVENESS_PERIOD_DAYS=7`（每周） | `test_condition_overdue_triggered_by_injected_clock`（**注入时钟**，不依赖真实墙钟） |

- 四项**同时命中**亦有用例（`test_all_four_conditions_can_trigger_together`）；
- **缺证据的项 `applicable=False`**：既不判退化（不编造），也不冒充通过
  （`unmeasured_conditions` / `missing_evidence_conditions` 如实列出）—— **首次探活不会误报退化**（`test_first_probe_has_no_applicable_conditions`）；
- 阈值全部可配（`test_thresholds_are_configurable`）。

### B-3 ✅ 退化产出 stage 回退建议（`native → borrowed`）且**不自动执行**（用例断言）

- `suggest_stage_rollback()` 是**纯函数**：`executed` **恒为 `False`**（无任何执行路径）；
- `apply_hint` 只给出"**若要执行**该调哪一行"（`stage.stage_migrate(...)`），**不调用**它；
- `requires_approval=True`（需人工或既有审批门）；
- **用例断言**：
  - `test_degraded_probe_recommends_rollback_without_executing`：断言
    `rollback.recommended is True`、`executed is False`、`from_stage="native"`、`to_stage="borrowed"`，
    且 **`registry.set_stage_calls == []`、`registry.stages[CAP] == "native"`**（台账未被触碰）；
  - `test_suggest_stage_rollback_is_pure_and_never_executes`：纯函数语义 + 未退化时
    `recommended=False`（"建议了但不该执行"与"根本没建议"可区分）。

### B-4 ✅ 退化升级为事故卡（`raise_incident`），六要素齐备

- **复用** `agent/self_healing/levels.py::raise_incident`（落盘 + 审计 + `healing.triggered` 三件套，
  **不重复留痕**），再补齐该函数签名**未覆盖**的三个要素并回写（同一 `incident_id` / 同一路径 /
  **同一 writer**，避免 `SingleWriterViolationError`）：

| 要素 | 来源（真实、可回溯） |
|---|---|
| `root_cause` | 逐条命中的条件 + 实测值/阈值（本模块实算） |
| `fatal_change` | 显式传入 > `descriptor.meta.version`；**都无则如实留空** |
| `evasion_rule` | 本模块产出的回退规则 id `stage_rollback:<cap>.<from>-><to>` |
| `in_strategy_memory` | 探活基线台账记录 `liveness:<cap>`（本模块实际写入的记录） |
| `regression_case_added` | 命中失败的**判定集用例** id（判定集即回归资产，**不新建第二套回归集**） |
| `trace_ids` | 命中用例的真实 `origin_trace_id`；为空时退回探活 run id（可在基线台账 `history` 反查） |

- **级别映射（对齐 S4-03 §4.4 语义）**：`internalized → L2`（劣化自动 downgrade）、
  `native → L3`（kill→revert→重启→负面样本，因自研实现退化需回退代码）；两条都有用例
  （`test_incident_card_has_all_six_elements` 断言 L3、`test_internalized_degradation_maps_to_L2` 断言 L2）；
- **不编造的守卫**：拿不到"致命变更 commit"时**不编造**，卡片**如实保留缺失项、不可 resolved**
  （`test_missing_fatal_change_is_reported_honestly` 断言 `missing_elements == {fatal_change, regression_case_added}` 且 `is_resolvable() is False`）；
  有 `descriptor.meta.version` 时回退取它并标注来源（`test_fatal_change_falls_back_to_descriptor_version`）。

### B-5 ✅ `register_liveness_job()` 默认关闭，需显式环境变量才注册

- `CP_DIGESTION_LIVENESS_ENABLED=true` 才注册（与 `register_reprobe_job` /
  `register_internalize_job` / shadow **同一条安全底线**）；
- 非法值（`"maybe"`）**保持关闭**（`test_illegal_enabled_value_keeps_it_off`）；
- 周期 = **每周**（`LIVENESS_INTERVAL_SECONDS = 7×86400`，`test_interval_is_weekly`）；
- **开启后有预算上限**：单周期最多 `CP_DIGESTION_LIVENESS_MAX_TARGETS`（默认 20）个能力 ×
  每个能力 `PROBE_SIZE`（默认 5）组用例；非法值回退默认（`test_illegal_max_targets_env_falls_back`）；
- **与成本刹车的处置刻意不同并写明理由**：内化是"新增资产"（断食期冻结），
  探活是**已上线实现的健康监控**（§6.7「保执行」）⇒ 断食期**不停**探活，只把抽样规模减半 ——
  避免监控被成本治理自身熄火。

### B-6 ✅ 无 `internalized`/`native` 能力时安全返回（不报错、不编造）

**真实台账证据**（`scripts/demo_s7_04_probe.py` 第一段，**只读**）：

```
台账能力总数：32
stage 分布  ：{'borrowed': 32}
可探活目标  ：[]（['internalized', 'native'] 共 0 个）
run_all()   ：status=no_capability
说明        ：无 ['internalized', 'native'] 能力 ⇒ 无可探活能力（不报错、不编造、不拿别的 stage 凑数）
```

- 台账未注入 / 读失败 ⇒ `scan_targets()` 返回 `[]`（**不隐式读运行时台账**，与 `gate._descriptor_view` 同纪律）；
- 无判定集 ⇒ `verdict="no_cases"`；内部异常 ⇒ 收敛为 `verdict="error"`，**永不抛出**
  （`test_run_never_raises_on_internal_failure`）—— 探活不得成为新的故障源。

### B-7 ✅ 报告中明确区分"探活（自身退化）"与"漂移重探（上游契约）"

- `probe.py` 模块 docstring 有**对照表**（回答的问题 / 比较基准 / 触发周期 / 命中后动作 / 失败含义）；
- **`LivenessReport.to_dict()["note"]` 逐份报告都带这句话**：
  "探活判「自身实现退化」；上游契约漂移由 gate.reprobe（30 天）负责，两者口径不通用"
  （`test_report_distinguishes_liveness_from_drift_reprobe` 断言）；
- **口径不被互相冒充的机器证据**：条件②的基线是**自己的历史 p99**（不是同轮上游臂 p99），
  `test_liveness_uses_its_own_baseline_not_upstream_arm` 用"基线设为极小值 ⇒ 必然触发"证明
  它没有偷偷改用"候选/上游比值"这一漂移口径；
- 两者**共用**同一判定集与同一回放沙箱（复用 `cases` / `sandbox`），
  但**基线来源、判定条件、动作完全不同**；`test_module_does_not_create_second_case_or_sandbox_implementation`
  断言没有自建第二套判定集/沙箱/抽样。

### B-8 ✅ 邻接套件零回归；新增单测全绿、覆盖率 ≥80%

见 §四 质量证据。**1119 例全部通过、0 失败**；`agent/digestion/probe.py` 覆盖率 **87%**。

---

## 四、质量证据

### 4.1 单测与回归

| 套件 | 例数 | 结果 |
|---|---|---|
| **新增**：`tests/unit/test_digestion_probe.py`（子项 B） | 46 | ✅ 全绿 |
| **新增**：`tests/unit/test_perf_budget_measure.py`（子项 A） | 26 | ✅ 全绿 |
| **邻接回归**：`digestion` 12 个套件（cases/sandbox/gate/shadow/internalize/stage/applicability/seed_pack/pipeline/cleaning/generation/mining） | 749 | ✅ 全绿 |
| **邻接回归**：`self_healing` / `cost_brake` / `lock_watchdog` / `watchdog_singleton` / `circuit_breaker` | 298 | ✅ 全绿 |
| **合计（本条命令）** | **1119** | **✅ 0 失败 / 0 回归** |

```powershell
python -m pytest tests/unit/test_digestion_probe.py tests/unit/test_perf_budget_measure.py `
  tests/unit/test_digestion_*.py tests/unit/test_self_healing_*.py tests/unit/test_s5_03_cost_brake.py `
  tests/unit/test_lock_watchdog.py tests/unit/test_watchdog_singleton.py `
  tests/unit/test_circuit_breaker_three_level.py tests/unit/test_circuit_breaker_boundary.py -q
# => 1119 passed in 75.99s
```

### 4.2 覆盖率

| 模块 | 语句 | 覆盖率 |
|---|---|---|
| `agent/digestion/probe.py` | 641 | **87%**（≥80% ✅） |

```powershell
python -m pytest tests/unit/test_digestion_probe.py -q --cov=agent.digestion.probe --cov-report=term-missing
# => agent\digestion\probe.py  641  81  87%
```

### 4.3 静态检查 / 门禁

| 门禁 | 结果 |
|---|---|
| kwarg 冲突扫描 `--path agent` | 530 文件 / 90 发现 / **HIGH = 0** / exit 0 ✅ |
| kwarg 冲突扫描 `--path tests` | 733 文件 / 32 发现 / **HIGH = 0** / exit 0 ✅ |
| `mypy agent/digestion/probe.py`（新模块） | **0 错误** ✅ |
| `mypy agent/digestion/__init__.py`（新导出） | **0 错误** ✅ |
| `mypy scripts/measure_perf_budget.py`（新脚本） | **0 错误** ✅ |
| `mypy tests/unit/test_digestion_probe.py tests/unit/test_perf_budget_measure.py` | **0 错误** ✅ |
| `lint-imports`（importlinter） | **2 kept / 0 broken** ✅ |
| 真实提交场景 pre-commit | 见交付结案报告（随提交执行） |

**kwarg 扫描中属于本任务新增文件的 3 条（均已逐条处置，非放过）**：

| 位置 | 风险 | 处置 |
|---|---|---|
| `probe.py:run_all` → `self.run(..., **kwargs)`（MEDIUM） | 启发式：担心 `**kwargs` 与显式实参冲突 | `now` / `max_targets` 均为**命名参数** ⇒ 不可能出现在 `kwargs` 中；已在代码内写明该推导（复核者无需重推） |
| `probe.py:establish` → `self.run(..., **kwargs)`（MEDIUM） | 同上 | 已**显式 `pop("emit_incident")` / `pop("rebaseline")`** ⇒ 调用方重复传入也不会 `TypeError`；同样写明 |
| `test_digestion_probe.py:164`（LOW） | "外部函数签名未知" | 被测对象是本仓库模块，非外部函数；LOW 且不影响门禁 |

### 4.4 数字可追溯性自查（**防"文档数字与实测脱节"**）

```
OK   watchdog hold p50 = 3000.9487
OK   watchdog hold p95 = 3002.3624
OK   watchdog overhead p95 = 1.3261
OK   watchdog liveness p50 = 6.0266
OK   watchdog liveness p95 = 9.9044
OK   cb open p50 = 0.0015      OK   cb open p95 = 0.0035
OK   cb block p50 = 0.0176     OK   cb block p95 = 0.0418
OK   hold/detect/block 原始采样首段逐值匹配
OK   §二 表无 ❓ 状态格
ALL 13 CHECKS OK
```

→ `docs/PERF_BUDGET_REBASED.md` §九 的每个数字都能在
`reports/s7_04/perf_budget_probe.json` 中逐值对上。

### 4.5 既有指标项未被删除（机器核验）

```
master 指标行 19   现状 19
缺失的指标项: []
❓ 残留: []
```

---

## 五、探活演示记录（**含"退化建议但不自动执行"的证据**）

复跑命令（`--ledger` 指向**只读**的运行时台账以取证）：

```powershell
python scripts/demo_s7_04_probe.py --ledger <repo>\data\descriptors.json
```

### 5.1 第一段：真实台账的可探活范围（**只读**）

```
台账能力总数：32（台账=默认路径）
stage 分布  ：{'borrowed': 32}
可探活目标  ：[]（['internalized', 'native'] 共 0 个）
run_all()   ：status=no_capability
说明        ：无 ['internalized', 'native'] 能力 ⇒ 无可探活能力（不报错、不编造、不拿别的 stage 凑数）
```

⇒ **B-6 的直接证据**：当前仓库**确实**没有 native 能力（32 个能力全在 `borrowed`），
设施如实返回空 —— 这正是 §3.3 所说"**设施可以先建，能力到达即自动生效**"的状态。

### 5.2 第二段：受控演示（临时目录；native 能力 → 健康探活 → 退化探活）

```
① 首次探活：verdict=ok  基线已建立=True  通过率=1.0  真实墙钟 p99=0.071ms
   未判定项（首次无基线，**不误报退化**）：['pass_rate', 'p99_regression', 'cost_increase', 'overdue']
② 健康复探：verdict=ok  触发条件=[]  建议回退=False
③ 退化探活：verdict=degraded  degraded=True  命中条件=['pass_rate']
     [① 通过率 < 基线×0.98] 通过率 0.0 < 基线 1.0 × 0.98 = 0.98（质量下滑）
   回退建议：native → borrowed  recommended=True  **executed=False**  requires_approval=True
   建议规则 id：stage_rollback:cp.builtin.read_file.native->borrowed
   若需执行的调用形状（本脚本**不执行**）：
     from agent.digestion.stage import stage_migrate; stage_migrate('cp.builtin.read_file', 'borrowed',
     evidence={'liveness_rollback': True, 'reason': ...}, actor='<approver>', reason='探活退化回退（TASK-S7-04）')
   事故卡：inc-f0841228eaa0  缺失要素=[]  可 resolved=True
台账 stage：native → native  set_stage 调用=[]  **未自动执行=True**
```

**事故卡全文**（六要素齐备；`detail.rollback_executed=false`）：

```json
{
  "id": "inc-f0841228eaa0", "severity": "L3", "status": "open",
  "root_cause": "cp.builtin.read_file（stage=native）自身实现退化：① 通过率 < 基线×0.98——pass_rate
                 （探活 run=live_a0ce21c08786；样本 5 组，通过率 0.0，真实墙钟 p99 0.04ms）",
  "fatal_change": "demo-commit-abc123",
  "evasion_rule": "stage_rollback:cp.builtin.read_file.native->borrowed",
  "in_strategy_memory": {"yes": true, "id": "liveness:cp.builtin.read_file"},
  "regression_case_added": {"yes": true, "case_id": "s704-case-000"},
  "trace_ids": ["tr-s704-000", "tr-s704-002", "tr-s704-004", "tr-s704-005", "tr-s704-007"],
  "missing_elements": [],
  "detail": {"rollback_executed": false, "rollback_to_stage": "borrowed", "stage": "native",
             "probe_run_id": "live_a0ce21c08786", "clock": "wall_clock(perf_counter; per-arm real elapsed)"}
}
```

**"退化建议但不自动执行"的三重证据**：

1. `rollback.executed == False` 且 `requires_approval == True`（建议对象本身的性质）；
2. **`set_stage 调用 == []`、`stage: native → native`**（台账侧**无任何写入**，
   与 `test_degraded_probe_recommends_rollback_without_executing` 的断言同源）；
3. 事故卡 `detail.rollback_executed = false`（证据链上同一条事实）。

原始记录：`reports/s7_04/probe_demo.json`（JSON）与 `reports/s7_04/probe_demo.txt`（可读）。

---

## 六、实现期发现与处置

| # | 发现 | 处置 |
|---|---|---|
| 1 | `WatchdogSingleton._os_lock_acquirable()` 清理路径在 Windows 稳定报 `锁文件解锁失败（句柄关闭时 OS 会释放）: [Errno 13] Permission denied`（`msvcrt.locking` 字节区间解锁失败） | **不影响**判定结论（`_try_lock` 已返回 True；句柄关闭即由 OS 释放锁）⇒ 实测值有效；该噪声归 **S5-02/S4-04 侧**，本任务**不改其代码**，仅在 §九 与本报告登记为遗留 |
| 2 | `raise_incident()` 签名**不覆盖**事故卡六要素中的三项（`evasion_rule` / `in_strategy_memory` / `regression_case_added`） | 不修改 `levels.py`（S4-03 领地）；在 `probe.py` 内**补齐这三项**并回写（同 incident_id / 同路径 / **同 writer**），并写明为何必须同名（否则 `SingleWriterViolationError`） |
| 3 | "基线若每轮自动抬高，慢性退化永远不触发"（温水煮青蛙） | 设计成：探活**不自动抬基线**，只在首轮 / **显式 `establish()`** 时确定；健康探活只刷**心跳** `last_success_at`；两条都有用例断言 |
| 4 | "首次探活无基线"若按退化处理会**立即误报** | 四项条件在无基线时 `applicable=False`（**不判定**，也不冒充通过），并用 `evidence_missing` 把"首次正常"与"证据缺位"分开 |

---

## 七、遗留（带归属与阻塞性）

| # | 遗留 | 归属 | 阻塞性 |
|---|---|---|---|
| L1 | **Watchdog「恢复」侧未实测**（需可重启真实服务的环境） | 生产化批次 / 部署侧 | **非阻塞**；§五 已给测法与 5 步环境要求，指标项**保留未删** |
| L2 | 熔断**集群配置下发**时延未测（需集群环境） | 生产化批次 | 非阻塞；已在 §九 边界说明 |
| L3 | `watchdog_singleton._os_lock_acquirable()` 的 Windows 解锁噪声 | S5-02 / S4-04 | 非阻塞（不影响正确性，仅日志噪声） |
| L4 | 探活设施**尚无真实 `native` 能力可跑**（当前 32 个能力全在 `borrowed`） | 待 S3 全链产出真实内化（S7-05 会推进） | **非阻塞**；设施已就绪，能力到达即自动生效（本任务设计目标） |
| L5 | 真实 LLM-judge 用于探活软性层 | 部署侧配 `CP_DIGESTION_JUDGE_PROVIDER/MODEL` | 非阻塞；缺省走确定性本地打分器（探活主判定是硬性层 + 基线比较，不依赖 judge） |
| L6 | 成本增量（条件③）缺真实成本源时记为"证据缺位" | S7-03 成本校准产出后接 `cost_provider` | 非阻塞；接口已留好，缺证据时**不编造** |

---

## 八、Start 壳「开工自查清单」逐条对照

- [x] 已读任务书，确认子项 A/B 分步计划 → §二 / §三
- [x] `--base master`、id=`s704`；worktree 内工作 → 交付物 §一
- [x] 性能实测有原始采样 + 环境说明；测不出的保留 ❓ 并给测法 → §二 A-1/A-3/A-5
      （注：❓ 已清零；"恢复"侧以 `⚠️ 恢复未测` + 测法保留，**未删除指标项**）
- [x] 探活对 internalized/native 生效；无能力时安全返回 → §三 B-1 / B-6 + §五 5.1
- [x] 四项退化条件各自有触发用例 → §三 B-2
- [x] 退化只出建议，**不自动改 stage**（断言） → §三 B-3 + §五 5.2（三重证据）
- [x] 调度默认关闭；事故卡六要素齐备 → §三 B-4 / B-5
- [x] 未编造数字；邻接套件零回归 → §二 A-5 + §四 4.1/4.4
- [x] 双远端同点推送 → 见交付结案报告

---

## 九、与上游设计文档的对应

| 设计文档章节 | 本任务落点 |
|---|---|
| §3.3 `native = 30 天零回退 + 每周探活` | `LivenessProbe` + `register_liveness_job()`（每周；默认关闭）；**探活内容已定义**（判定集子集重放 + 通过率/p99/成本基线比较），补齐审计 T4 |
| §4.4 L1–L5 自愈 | 退化 → `HealLevel.L2`（internalized）/ `L3`（native）+ 事故卡（复用 `raise_incident`）；六要素齐备语义不变 |
| §4.5 漂移重探 | **与探活明确区分**（见 §三 B-7 对照表）；共用判定集与沙箱，**不自建第二套** |
| §11.2 性能预算 | Watchdog / 熔断「达阈值→生效」补测并回填 `PERF_BUDGET_REBASED.md` §九 |
| §11.10 混沌清单（故障注入） | 子项 A 的受控注入（持锁 3 s / 抛错桩 / 桩进程 terminate）全部在**受控环境**内，不触碰生产进程与运行时台账 |

---

## 十、复跑清单（一页命令）

```powershell
# 子项 A：性能补测（原始采样）
python scripts/measure_perf_budget.py --repeat 20 --out reports/s7_04/perf_budget_probe.json
python scripts/measure_perf_budget.py --items watchdog --repeat 5      # 只测 Watchdog（快速）

# 子项 B：探活演示记录
python scripts/demo_s7_04_probe.py --ledger <repo>\data\descriptors.json

# 单测与回归
python -m pytest tests/unit/test_digestion_probe.py tests/unit/test_perf_budget_measure.py -q
python -m pytest tests/unit/test_digestion_*.py tests/unit/test_self_healing_*.py `
  tests/unit/test_s5_03_cost_brake.py tests/unit/test_lock_watchdog.py `
  tests/unit/test_watchdog_singleton.py -q

# 门禁
python scripts/scan_kwarg_conflicts.py --path agent
python scripts/scan_kwarg_conflicts.py --path tests
python -m mypy agent/digestion/probe.py
lint-imports
```
