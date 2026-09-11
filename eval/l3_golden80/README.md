# L3 Golden-80（框架占位）

本目录是 **L3 Golden-80** 的**框架占位**：定义目录、运行器接口、运行周期、从 L2 Core-50 的晋升规则
与哈希锚定复用方式；**用例内容为空**（`cases: []`），实际扩充入 **M7+**（设计文档 v7.2 §6.5 明确 L3 延后）。

> 口径纪律：本目录不产生任何"通过率"。空用例集下运行器的 `pass_rate` 为 `null`（报告显示 `n/a`），
> 绝不虚报 0% 或 100%。框架就绪 ≠ 评测就绪。

## 1. 目录

```
eval/l3_golden80/
├── README.md      # 本文件：框架说明（目录 / 接口 / 周期 / 晋升 / 锚定）
└── cases.json     # eval.cases.v1 用例集；当前为框架占位（cases 为空数组）
```

- `cases.json` 根结构：`{"schema": "eval.cases.v1", "layer": "L3", "frozen": false, "meta": {...}, "cases": [...]}`。
- `meta.status = "framework_only"`、`meta.target_size = 80`，与
  `agent/eval/runner.py::L3_FRAMEWORK` 保持同一份口径（运行器会把该声明原样挂到报告上）。
- 用例扩充时的硬约束（由 `agent.eval.cases.validate_case_set` 强制）：
  - `id` 唯一且前缀必须为 `L3-`；`scenario` ∈ `SCENARIOS`（S1 修 bug / S2 懂代码库 / S3 提交 /
    Router 决策 / 审批拦截 / 回滚）；
  - 每条至少 1 条判定条目 `{"checker","path","args","why"}`；判定器只能取自
    `agent.eval.checkers`（机械可验优先）；
  - `verdict_kind="mechanical"` 的用例**不得**引用代理判定器（`rubric_keywords`）——引用即必须降级为
    `proxy` 并在 `notes` 里写清回落口径；无法判定的如实标 `unsupported`（不计入通过率分母）。
  - L3 不设固定条数（`LAYER_SIZES["L3"] is None`），因此 `target_size = 80` 是**目标**而非结构约束。

## 2. 运行器接口

两条等价入口（同一个 `run_layer` 内核，报告结构完全一致）：

| 入口 | 说明 |
|---|---|
| `agent.eval.runner.run_l3()` | Python API：`run_l3(case_set=..., solver=..., solver_name=..., compare_baseline=...)` → `EvalReport` |
| `python scripts/run_eval.py --layer L3` | 命令行入口（`runner.L3_FRAMEWORK["cli"]` 声明的接口名） |

要点：

- `run_l3()` 默认读 `eval/l3_golden80/cases.json`（`runner.LAYER_CASESET_PATHS["L3"]`）。
- **文件缺失**时：直接返回框架声明（`total=0`）并附披露文本「L3 Golden-80 为**框架占位**（§6.5：W15+/M7+
  扩充）」；**文件存在**（当前状态）时：走 `run_layer(C.LAYER_L3)`，`total=0`、`pass_rate=null`、
  `framework` 字段仍为 `L3_FRAMEWORK` 全量声明。
- 被测答案来源与其它层一致（`agent.eval.solvers`）：`reference`（判定器自检）/ `static` / `mutant`
  （区分度对照）/ `null`（无凭证时如实标注"未评测"，不计入通过率分母）/ `file:<answers.json>`。
- 自检片段：

```python
from agent.eval import cases as C, runner as R
cs = C.load_case_set(r"eval/l3_golden80/cases.json")   # 空用例集合法：L3 不设固定条数
rep = R.run_l3()
print(rep.layer, rep.total, rep.framework.get("status"), rep.disclosures)
```

- 与 L1/L2 的关系：L1 最小集（`eval/l1_min/cases.json`，10 条，全 mechanical）是**每次提交**都能跑的
  快路径回归；L2 Core-50（`eval/l2_core50/cases.json`，50 条）是 UTC 基线的唯一依据；L3 是**发布前/每月**
  的深度黄金集，用于止损长尾退化，不参与日常快路径。

## 3. 运行周期

- **阶段**：W15+ / M7+（设计文档 §6.5：L3 Golden-80 延后实施）。
- **触发**：**发布前** + **每月一次**（与 §6.6 `baseline_remeasure` 的 weekly 口径同源扩展）。
- **事件**：`baseline_remeasure {weekly, core50_cost, golden_set_pass_rate}`——
  L3 的 `golden_set_pass_rate` 与 L2 的成本指标同批上报，避免"两套口径两套仪表"。
- **耗时预期**：L3 允许远高于 L1 的单条耗时（含端到端/多轮场景），因此**不进**日常 CI 快路径门禁；
  基线对比沿用 `runner.compare_to_baseline()`：用例集哈希变化时只披露 `anchor_changed`，不把
  "改了用例的对比"冒充实测回归证据。

## 4. 从 L2 Core-50 晋升到 L3

**晋升规则（`promotion_rule`）**：L2 Core-50 **连续 2 个窗口全绿**且样本量达标 → 由 L2 抽取代表用例升入 L3，
并**保留 L2 原始证据链**（`case_id` 不变，层前缀升级）。

操作要点：

1. **全绿判定**：两个连续窗口内，L2 Core-50 的全部用例状态均为 `pass`（`fail`/`error`/`unassessed`
   任一条即不满足；`unassessed` 是"未评测"，不算绿）。
2. **样本量达标**：该用例在窗口内的评测次数达到约定下限（未达标的用例只作候选，不晋升）。
3. **代表用例抽取**：按场景与判定器覆盖度抽取，而非按"最容易通过"抽取；每个场景的覆盖率不得因晋升而下降。
4. **身份与证据链**：`case_id` 语义不变（便于把 L3 的失败回溯到 L2 的历史状态），层前缀升级为 `L3-`；
   L3 用例的 `notes` 里注明来源 L2 用例 id 与原窗口。
5. **不得静默放宽**：晋升到 L3 只允许**加强或保持不变**的判定口径；若因环境差异必须回落到代理口径，
   必须把 `verdict_kind` 降级为 `proxy` 并在 `notes` 写清披露内容。

## 5. 哈希锚定复用（`agent.eval.anchor`）

L3 复用 L0 的哈希锚定机制，不新造第二套：用例集在 `eval/` 根下（**独立于系统数据目录** `data/`），
`freeze_anchor` 可直接对 `eval/l3_golden80` 执行。

- **冻结（人工 + 显式能力，唯一被许可的写路径）**：

```python
from agent.eval import cases as C, anchor as A
case_set = C.load_case_set(r"eval/l3_golden80/cases.json")   # 冻结前先走结构/规模/覆盖校验
A.freeze_anchor(
    case_set=case_set,
    reference=answers,                 # {case_id: 答案工件}，必须覆盖用例集内每一条
    frozen_by="<人工署名>",             # 空值直接拒绝：自动化流程无权冻结
    review_note="<为何冻结 / 依据>",
    root=r"eval/l3_golden80",          # 锚目录 = 本目录（写入 cases.json/reference.json/manifest.json）
    allow_write=True,                  # 显式写入能力：只由人工冻结脚本提供
)
```

- **完整性校验（fail-closed）**：`A.verify_anchor(root=r"eval/l3_golden80")` 重算逐条哈希 + 用例集整体
  哈希 + 参考解哈希，并对齐 manifest；任一不一致/缺失/多余即 `ok=False`。
  `A.AnchorStore(r"eval/l3_golden80").load(verify=True)` 在校验失败时抛 `AnchorIntegrityError`，
  **拒绝在锚被改动后给出评测结论**。
- **位置独立性**：`A.assert_independent_of_system_data()` 双向校验锚目录不在系统数据目录内（反之亦然）；
  `A.guard_write(path)` 供任何落盘路径调用，拒绝对锚目录的写入。
- **哈希口径**：`cases.caseset_sha256` = 逐条用例规范化 JSON 哈希**排序后**再哈希（与文件缩进/键序无关），
  因此"格式漂移"不会被误判为锚变更，"内容漂移"不会被漏判；L3 一旦冻结，任何字节级语义改动都会让
  `run_l3()` 的基线对照进入 `anchor_changed` 状态。

## 6. 未决事项（如实记录）

- `python scripts/run_eval.py --layer L3` 是 `runner.L3_FRAMEWORK["cli"]` 声明的命令行接口；
  截至本框架占位交付时，**本仓库树中尚无 `scripts/run_eval.py`**（全局仅 `agent/eval/runner.py`
  引用该路径字符串）。CLI 接线补齐前，请以 `run_l3()` 作为等价入口；两者共用同一个 `run_layer` 内核。
- L3 的 80 条用例、其参考解与冻结锚（`manifest.json`）均**不在**本次交付范围内（M7+）。
