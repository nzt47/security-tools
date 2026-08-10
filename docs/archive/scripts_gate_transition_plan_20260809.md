# scripts 门禁临时过渡方案（警告模式，2026-08-09）

> 背景：scripts 层覆盖率当前 6.9%，若直接以 fail-under=50/warn-gap=5 启用硬门禁会**立即阻断 CI**。
> 目标：门禁先行上线（防「退出门禁=无人看管」），但以**警告模式**运行，待覆盖率提升后再收紧。
> 关联：[scripts_gate_integration_plan_20260809.md](scripts_gate_integration_plan_20260809.md)

---

## 1. 方案选择：`--warn-gap` 拉高，仅告警不阻断

check_scripts_coverage.py 已支持 `--warn-gap`：缺口 ≥ warn-gap 才 `exit 1`，否则仅 `::warning::` 输出并 `exit 0`。

**过渡期调用参数**：

```yaml
- name: scripts 层覆盖率门禁（过渡：仅告警）
  if: matrix.python-version == '3.11'
  run: |
    python scripts/check_scripts_coverage.py \
      --xml coverage_scripts.xml \
      --fail-under 50 \
      --warn-gap 100   # 缺口永远 <100pp → 永不阻断，仅告警
```

**效果**：当前 6.9% → 缺口 43.1pp < 100pp → 输出 `::warning::scripts 覆盖率低于红线`，CI 继续。

## 2. 收紧时间表（覆盖率阈值联动）

| 阶段 | 触发条件 | 参数调整 | 行为 |
|---|---|---|---|
| 过渡期（当前） | scripts 覆盖率 < 45% | `--warn-gap 100` | 仅告警，不阻断 |
| 临门期 | 覆盖率 ≥ 45% | `--warn-gap 5` | 缺口 ≥5pp 阻断（已接近红线） |
| 达标期（S2 目标） | 覆盖率 ≥ 50% | `--fail-under 50 --warn-gap 5` | 标准硬门禁 |
| 提升期（S3） | 覆盖率 ≥ 60% | `--fail-under 60 --warn-gap 5` | 渐进上调 |

## 3. 防回退护栏（【不易】）

- 警告必须**可见**：`::warning::` 会在 CI 日志与 summary 显著呈现，覆盖率缺口随时可查
- **门禁脚本持续生效**：即使警告模式，每次 run 仍产出 coverage_scripts.xml + 覆盖率数字——趋势不中断
- **收紧不可逆**：阈值只上调不下调（记录在案），防止「告警疲劳」后无人追赶

## 4. 实施变更（并入 scripts_gate_integration_plan 步骤 2）

将步骤 2 的调用参数替换为过渡参数（--warn-gap 100），其余步骤（xml 输出、artifact、summary）不变。
待覆盖率 ≥45% 时，仅需把 `--warn-gap 100` 改回 `--warn-gap 5`——单行变更，无脚本改动。

## 5. 验收标准

| 项 | 标准 |
|---|---|
| CI 不阻断 | 过渡参数下 run 结论 success（即使覆盖率 6.9%） |
| 警告可见 | step summary 显示 `scripts 层覆盖率: 6.90% (红线 50%)` |
| 数据产出 | artifact 含 coverage_scripts.xml，趋势可回溯 |

> 本方案为临时过渡，红线目标仍为 50%（S2 硬门禁），过渡只为避免「立即阻断 → 门禁被绕开」的最坏情况。
