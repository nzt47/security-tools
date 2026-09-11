# `eval/` —— v7.2 评测分层数据（L0–L3）与运行手册（TASK-S5-02）

> 权威规格：[`TASK-S5-02_评测锚与基线.md`](../docs/zh/CloudPivot_v7.2重构计划/TASK-S5-02_评测锚与基线.md)（§6.5 评测分层 / §6.7 指标字典）
> 本目录**只放数据**（用例集 / 锚清单 / 参考解 / 自检基线）；执行代码在 `agent/eval/`。

```
eval/
├── l0_anchor/          # L0 锚：20 条人工冻结用例（**系统不可写**）
│   ├── cases.json      #   用例集（哈希锚定对象）
│   ├── reference.json  #   参考解（仅用于判定器自检 + 变异对照）
│   └── manifest.json   #   逐条哈希 + 用例集哈希 + 参考解哈希 + 冻结署名/时间
├── l1_min/             # L1 最小集：10 条快路径回归（全机械可验）
├── l2_core50/          # L2 Core-50：UTC 基线唯一依据（三类种子场景）
├── l3_golden80/        # L3 Golden-80：**框架占位**（M7+ 扩充）
└── baselines/          # 各层**判定器自检基线**（solver=reference；随代码版本走）
```

## 分层契约（§6.5）

| 层 | 条数 | 判定口径 | 用途 | 运行 |
|---|---|---|---|---|
| **L0 锚** | 20（人工冻结） | 全部机械可验；6 类场景各 ≥2 | 打破"自验收循环"的客观标尺 | `python scripts/run_eval.py --layer L0` |
| **L1 最小集** | 10 | 全部机械可验；快路径（秒级） | 常规回归 | `python scripts/run_eval.py --layer L1` |
| **L2 Core-50** | 50（48 机械 + 2 代理披露） | 三类种子场景 S1 修 bug / S2 懂代码库 / S3 提交 | **UTC 基线唯一依据**（对接 S5-03） | `python scripts/run_eval.py --layer L2` |
| **L3 Golden-80** | 0（框架） | 待 M7+ 扩充至 80 | 发布前 + 每月 | `python scripts/run_eval.py --layer L3` |

### L0 为什么"系统不可写"

L0 是评测体系的**信任根**：如果自动化流程能改它，"自验收循环"就只是换了个地方发生。
因此本任务用三层机制实现不可写（**逐层可测**，见 `tests/unit/test_eval_anchor.py`）：

1. **位置独立**：`eval/l0_anchor/` 不在任何系统数据目录下（`data/`、`data/events/`、
   `data/digestion/`、`data/reflection/`、`data/feedback/`、`data/eval/`）；
   `anchor.assert_independent_of_system_data()` 在**每次加载时**机器校验该不变量（双向）。
2. **无写 API + 写入守门**：`AnchorStore` 只有读方法；`write_cases()/update_case()/
   delete_case()/write_reference()/write_manifest()` **一律抛 `AnchorReadOnlyError`**；
   `anchor.guard_write(path)` 供任何落盘路径调用以拒绝写入锚目录
   （基线/拟合件/周报写出前都会过这道门）。
3. **哈希锚定 + fail-closed**：`manifest.json` 记录逐条哈希与用例集整体哈希；
   加载时重算比对，**任一不一致/缺失/多余 → `AnchorIntegrityError`，拒绝输出评测结论**。

> OS 级只读（`chmod 444`）是**可选硬化**（`freeze_eval_anchor.py --mark-readonly`），
> 仓库工作区默认不启用 —— git 需要能改写工作区；发布包可启用。

### 冻结流程（人工）

```powershell
# 只校验（默认；不写任何文件）
python scripts/freeze_eval_anchor.py --verify-only

# 人工冻结：必须署名 + 显式 --confirm-freeze（唯一提供写能力的开关）
python scripts/freeze_eval_anchor.py --confirm-freeze --reviewer <人名> --note "<变更依据>"

# 把锚哈希锚定入发布清单 release/eval_anchor_manifest.json
python scripts/freeze_eval_anchor.py --write-release-manifest
```

## 判定口径

* **机械可验优先**：判定器只做相等/包含/正则/集合/顺序/长度/路径存在/符号存在/
  代码可执行/断言通过 这类可复现判断（`agent/eval/checkers.py::MECHANICAL_CHECKERS`）。
* **代理口径必须降级并披露**：`rubric_keywords`（词表代理）只允许出现在
  `verdict_kind="proxy"` 的用例上，且必须在 `notes` 写清"代理口径 ≠ 语义正确"。
* **未评测不许冒充通过**：解算器返回 `None` → 状态 `unassessed`，**不计入通过率分母**。
* **反幻觉**：`path_exists` / `symbols_exist` 要求答案里的文件路径与符号真实存在于仓库。
* **受限执行**：`python_probes` 在只给白名单内置函数的命名空间内执行被测代码
  （无 `import`/`open`/`eval`），因此判定**不触网、不调模型**。

## 自检与门禁

```powershell
python scripts/check_eval_datasets.py          # 四层契约 + 锚完整性 + 参考解全过 + 逐条判定区分度
python scripts/run_eval.py --layer L2 --record-baseline   # 固化基线（eval/baselines/）
python -m pytest tests/unit/test_eval_*.py -q             # 新增套件
```

自检门的三条不变量：**① 参考解全过**（判定器与用例自洽）；**② 每条判定条目单独被破坏后
该用例必须判负**（判定器真的在干活）；**③ 变异解全负**（无幸存用例）。

## 参考解的诚实边界

`reference.json` 是**参考解**，仅用于：① 判定器自检（管道通不通）；② 生成变异解证明区分度。
它**不代表任何模型能力**；真实能力评测必须由被测解算器（真实 LLM / 被测系统）产出答案工件：

```powershell
python scripts/run_eval.py --layer L2 --solver file:answers.json --print-md
```

无凭证环境下如实标注"未评测"（`--solver null`，缺省行为），**绝不用参考解冒充模型成绩**。
