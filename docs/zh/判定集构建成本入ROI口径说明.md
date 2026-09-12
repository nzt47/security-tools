# 判定集构建成本入 ROI 口径说明（TASK-S7-06 R1）

> 归属：**TASK-S7-06 步骤 1**（收官审计 [`V72_全计划收官审计报告.md`](CloudPivot_v7.2重构计划/V72_全计划收官审计报告.md) §8.5 残留 **R1** / T1；§三 缺口 3）
> 上游：S3-03 内化条件③（`agent/digestion/internalize.py::ROIReport`）、S2-03 成本归一（`agent/observability/utc.py`）
> 基线：`master` / `531515e0`｜生成：2026-09-12
> 一句话：判定集构建成本**已可采集**（`stage=case_build` 成本事件）并**单列披露**，报告**同时给出不含/含两种 ROI**。

---

## 一、修的是什么（T1 残留的原文）

审计 T1 指出：判定集的**维护成本未建模**——"30–100 组谁写、谁抽检、漂移重生成成本未计"，
且 S3-03 的 `ROIReport` 只消费 `utc.utc_window()` 的**持续运营成本**。后果是：
条件③（`月省 > 自研一次性投入 ÷ 12`）在**只填了显性一次性投入**时，会把"判定集的白工"当成免费，
从而**高估内化收益**。

R1 的处置：**采集 + 单列披露**（不把两笔不同量纲的钱合成一个数）。

---

## 二、采集（埋点）

| 项 | 内容 |
|---|---|
| 埋点位置 | `agent/digestion/cases.py::CaseStore.save()` —— 判定集落库的**唯一漏斗**；写入**新版本**时记一条成本事件 |
| 事件类型 | `cost`（events.v1 `EV_COST`），载荷带 ``stage="case_build"`` / ``cost_class="case_build"`` |
| 事件流 | **独立目录**：`<判定集根>/_case_cost/`（可被 `CP_DIGESTION_CASE_COST_DIR` 覆盖） |
| 幂等键 | `case_build:<capability_id>:v<version>`（同版本重复落库**不重复计费**；漂移重生成 = 新版本 = 新成本） |
| 通道归因 | 由用例 `kind` 自动归因：`seed → seed_pack`、`trace → trace`、`llm → llm`、`manual → manual_review`，未知归 `other` |
| 可注入的实测项 | `tokens_in` / `tokens_out` / `model` / `cache_hit` / `manual_review_minutes` / `replay_cpu_ms` / `extra_cents`（缺省即 0，**不臆造**） |

### 为什么**不**写进 `data/events/`（关键设计）

上游单位成本是**持续运营成本**，判定集构建是**一次性投入**。两者混进同一条成本流会让
`UTC = cost_normalized_cents / 任务数` 被一次性投入污染 —— 而那正是条件③要比较的两侧。
因此成本事件写在**独立目录**，`utc.utc_window()` **看不到**它们，两个口径互不污染
（可用 `case_build_cost_window()["caveats"]` 中的说明逐字核对）。

> **测试隔离的顺带好处**：成本目录由**判定集根派生**，单测把 `CP_DIGESTION_CASE_DIR` 指到
> 临时目录即自动隔离（S3-02/S3-03 两次落盘污染的直接教训）。

---

## 三、ROI 公式（两种口径同时给出）

设：`月省 = (上游单位成本 − 自研单位成本) × 月样本数`；
`一次性投入 I`；`判定集构建成本 B`；摊销月数 `12`。

| 口径 | 摊销 | 净月收益 | ROI 为正 | 用途 |
|---|---|---|---|---|
| **不含**（条件③判定口径，与 S3-03 逐字一致） | `I / 12` | `月省 − I/12` | `月省 > I/12` | **判定**（`score_roi` 只用这一口径） |
| **含**（透明对照） | `(I + B) / 12` | `月省 − (I+B)/12` | `月省 > (I+B)/12` | **仅披露**（不参与条件③） |

对应字段（`ROIReport`，**兼容叠加，不改既有字段语义**）：

```
case_build_cost_cents                  # 点值（下界；不可得 ⇒ None，不以 0 冒充）
case_build_cost_low_cents              # 区间左端 = 点值
case_build_cost_high_cents             # 区间右端（单价未配置 ⇒ None = 不可估）
case_build_cost_samples                # 样本量（case_build 事件条数）
case_build_cost_method                 # 估计方法（逐项显式）
case_build_cost_source                 # 数据源标签
case_build_cost_channels               # 通道归因（用例数）
case_build_cost_lower_bound            # 是否为下界
case_build_cost_in_amortization        # 是否计入摊销（默认 False）
amortized_monthly_including_case_build_cents
net_monthly_including_case_build_cents
positive_including_case_build
positive_excluding_case_build          # = 既有 positive（同一判据）
formula_excluding_case_build / formula_including_case_build
```

### 为什么选"单列披露不参与摊销"（口径②）

判定集是**一次性资产**：它不随月度调用量增长，把它摊到月成本里会让
"月省 vs 摊销"的比较**偏向不内化**（摊销基数被人为抬高）。故默认
`case_build_in_amortization=False`；若某部署坚持口径①，显式传 `True` 即可，
**但两种数字始终同时出现在报告里**（透明可对比，不做单口径宣称）。

---

## 四、估计方法与样本（不得编造数字）

| 成本项 | 估计方法 | 未配置时 |
|---|---|---|
| LLM 调用 | 实测 token 数 × **S2-03 价格锚定系数**（`utc.normalize_cost`，与 UTC 口径同源） | 无调用即 0（实测值） |
| 人工复核工时 | 实测分钟数 × `CP_DIGESTION_CASE_BUILD_MANUAL_RATE_CENTS`（分/分钟） | **单价未配置 ⇒ 计 0**，点值成为**下界**，区间右端 `None`（**不臆造费率**）；工时未登记时另行标注"点值不含人工成本" |
| 回放算力 | 实测 CPU 秒 × `CP_DIGESTION_CASE_BUILD_REPLAY_RATE_CENTS`（分/CPU 秒） | 同上 |
| 显式金额 | 调用方 `extra_cents` | — |

**区间语义**：`[low, high] = [点值, 点值 + 未计价项按显式费率补估]`；
未配置费率 ⇒ `high_cents = None` 并标注"不可估"，**不用默认费率替调用方拍板**。

**样本标注**：`case_build_cost_samples` = 参与汇总的成本事件条数；`case_build_cost_method`
逐项说明每个金额的来源与计价方式。两者都进 ROI 报告（`to_dict()` 与 `markdown()`）。

---

## 五、复现（本地命令）

```powershell
# 1) 判定集构建一次（落库即产生 stage=case_build 成本事件；成本目录由判定集根派生）
python -c "from agent.digestion import cases as C; CAP='cp.builtin.read_file'; \
C.open_case_store().save(C.build_case_set(CAP, C.seed_cases_for(CAP)))"

# 2) 查看成本事件明细与汇总（逐条带 stage / 能力 / 版本 / 通道 / 金额 / 方法）
python -c "from agent.digestion import case_cost as CC; import json; \
print(json.dumps(CC.case_build_cost_ledger(), ensure_ascii=False, indent=1)); \
print(json.dumps(CC.case_build_cost_window(), ensure_ascii=False, indent=1))"

# 3) 单测（采集 + 双口径 + 估计标注 + 隔离）
python -m pytest tests/unit/test_s7_06_case_build_cost.py -q
```

---

## 六、影响面与兼容性

| 受影响面 | 处置 |
|---|---|
| S3-03 既有 ROI 断言 | **不改**既有字段语义：`net_monthly_cents` / `positive` / `amortized_monthly_cents` 仍是"不含"口径；条件③ 判据不变（有回归测试） |
| S6-01 ROI 面板 | **兼容叠加**：新增 `case_build_cost_*` / `*_including_case_build` / 区间与方法字段；旧字段原样保留 |
| 周报/UTC | **不受影响**：成本事件在独立目录，`utc_window()` 的分子分母都不变 |
| 判定集存储 | `CaseStore.save()` 新增可选参数（`cost_context` / `record_cost`）与可选 `cost_store` 注入；默认行为仅**多记一条 telemetry**，落库结果不变 |

---

## 七、与既有文档的关系

- 内化条件与 ROI 的原始口径：`docs/zh/CloudPivot_v7.2重构计划/TASK-S3-03_验收报告.md` §2；
- 成本归一与校准状态：`docs/zh/CloudPivot_v7.2重构计划/TASK-S5-03_验收报告.md`（`calibrated=False` 如实标注）与 [`PERF_BUDGET_REBASED.md`](../PERF_BUDGET_REBASED.md) §九；
- 本任务验收：`docs/zh/CloudPivot_v7.2重构计划/TASK-S7-06_验收报告.md`。
