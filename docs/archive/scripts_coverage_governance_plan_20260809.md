# scripts/ 目录独立跟踪门禁长期治理方案（2026-08-09）

> 背景：路径 A（f52c0e11 + 410dc41e）将 `scripts/*` 移出全项目覆盖率分母（37.96% → 67.92%），
> 脚本层质量需由独立跟踪体系把关，避免「退出门禁 = 无人看管」。
> 关联：[path_a_acceptance_report_20260809.md](path_a_acceptance_report_20260809.md)、[path_a_coverage_omit_report_20260809.md](path_a_coverage_omit_report_20260809.md)

---

## 1. 现状盘点（基线）

| 项 | 现状 | 依据 |
|---|---|---|
| 脚本层测试覆盖 | **6.9%**（3609/52175 行），387 文件仅 28 个有测试覆盖 | full-coverage-report 合并数据 |
| 独立配置 | `.coveragerc_scripts`：`source=scripts` + `omit=tests/*,agent/*,__pycache__/*` | 文件已存在（2026-06-27 方案 A） |
| 消费方 1 | observability-ci「优化脚本单元测试」step：`--cov=scripts --cov-config=.coveragerc_scripts`（3 个缓存相关测试文件，**continue-on-error: true 临时不阻断**） | observability-ci.yml L426-441 |
| 消费方 2 | observability-ci「架构影响可见性检查」job：`impact_analysis.py` 追踪 scripts 变更影响 | observability-ci.yml L171-257 |
| 触发路径 | observability-ci push paths **含** `.coveragerc_scripts`、`scripts/verify_*.py`、`scripts/observability_*.py`、`scripts/impact_analysis.py` 等 | observability-ci.yml L58-64 |
| 缺失项 | **无 scripts 层独立的覆盖率门禁**（fail-under 阈值、趋势看板、回归统计） | 本次治理对象 |

> 关键事实：`pyproject.toml` 已由 410dc41e 纳入 observability-ci push paths（修复 f52c0e11 未触发门禁的缺口），
> 但 `.coveragerc_scripts` 与 scripts 关键文件本就在 paths 内——脚本层跟踪已有独立触发路径，仅缺门禁闭环。

## 2. 治理目标（阶段化）

| 阶段 | 目标 | 验收标准 |
|---|---|---|
| S1（立即） | 建立 scripts 覆盖率**趋势基线**：每 run 产出脚本层覆盖率数字并写入报告 | observability-ci 报告含 `scripts_coverage` 指标 |
| S2（1 迭代内） | 明确 scripts 层**覆盖红线**（建议 fail-under=50%），覆盖缺口形成可见债务 | 门禁按红线判定，缺口 ≥5pp 时告警 |
| S3（持续） | 脚本层覆盖率随迭代**渐进提升**，红线按阶段上调（50% → 60% → 70%） | 每个里程碑调阈一次，附数据回看 |

## 3. 实施方案（三步，均走 agent-b2 worktree）

### 步骤 1：复用现有消费方，产出可统计指标（S1）

现有「优化脚本单元测试」step 已用 `--cov=scripts --cov-config=.coveragerc_scripts`，
只需把覆盖率结果落到 artifact 并输出数字：

```yaml
# observability-ci.yml「优化脚本单元测试」step 后追加
- name: 提取脚本层覆盖率指标
  if: matrix.python-version == '3.11'
  run: |
    python - <<'EOF'
    import xml.etree.ElementTree as ET
    # coverage.xml 由上一 step 的 --cov-report=xml 生成（需在 pytest 参数追加 --cov-report=xml:coverage_scripts.xml）
    r = ET.parse('coverage_scripts.xml').getroot()
    rate = float(r.attrib['line-rate']) * 100
    print(f"scripts 层覆盖率: {rate:.2f}%")
    EOF
```

**前置**：pytest 命令追加 `--cov-report=xml:coverage_scripts.xml`；上传 `coverage_scripts.xml` 为 artifact。

### 步骤 2：建立门禁脚本 + 告警阈值（S2）

新增 `scripts/check_scripts_coverage.py`（可被 observability-ci 调用）：

```python
"""scripts/ 层覆盖率门禁：读取 coverage_scripts.xml，低于红线时失败/告警。"""
import argparse, sys, xml.etree.ElementTree as ET

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", required=True)
    ap.add_argument("--fail-under", type=float, default=50.0)
    ap.add_argument("--warn-gap", type=float, default=5.0)  # 缺口≥5pp 告警
    args = ap.parse_args()
    rate = float(ET.parse(args.xml).getroot().attrib["line-rate"]) * 100
    print(f"scripts 层覆盖率: {rate:.2f}% (红线 {args.fail_under:.0f}%)")
    if rate < args.fail_under - args.warn_gap:
        print(f"::error::scripts 覆盖率缺口 {args.fail_under-rate:.1f}pp ≥ {args.warn_gap:.0f}pp")
        sys.exit(1)
    if rate < args.fail_under:
        print(f"::warning::scripts 覆盖率低于红线 {args.fail_under:.0f}%")
    sys.exit(0)  # 缺口<红线告警不阻断；≥红线+gap 才失败

if __name__ == "__main__":
    main()
```

**决策原则（三义）**：
- 【不易】红线 = 脚本层质量底线，S2 定 50%（从 6.9% 需 3-5 倍提升，务实起步），S3 阶段上调
- 【变易】`--warn-gap` 让「低于红线但差距小」先告警不阻断，避免刚起步就频繁失败
- 【简易】单文件单函数，xml 读取与质量门禁同模式（复用 observability_quality_gate 的解析范式）

### 步骤 3：纳入 CI 门禁 + 报告（S2→S3）

- observability-ci「优化脚本单元测试」step 后调用 `check_scripts_coverage.py --xml coverage_scripts.xml`
- 脚本层覆盖率写入 visibility-report 或 step summary（沿用现有汇总格式）
- 覆盖率缺口与红线变更记录进 `docs/troubleshooting/`（本方案作为 S1 记录）

## 4. 预期效果

| 指标 | 当前 | S2 目标 | S3 目标 |
|---|---|---|---|
| scripts 层覆盖率 | 6.9% | 50% | 70% |
| 全项目覆盖率（omit 后） | 67.92% | ≥60%（业务层红线 40% 之上） | ≥65% |
| 脚本层质量可见性 | 无门禁、无趋势 | 门禁 + 每 run 数字 | 门禁 + 趋势看板 |

## 5. 风险与注意

- **红线起步不宜过高**：6.9% → 50% 需补测约 2 万行，S2 阶段若直接 fail-under=50 会长期失败；
  建议 S2 先用 `warn-gap` 模式（告警不阻断），待补测到 45%+ 后收紧为 fail-under=50 硬门禁
- **与路径 A 的边界**：scripts/ 退出全项目门禁（分母）+ 进入独立门禁（分子），两层解耦互不干扰
- **不重复造轮子**：`.coveragerc_scripts` 与消费 step 已存在，仅补指标提取与门禁判定两环

## 6. 待用户确认项

| 项 | 建议 | 备选 |
|---|---|---|
| S2 红线值 | 50%（warn-gap 告警起步） | 40%（更保守）/ 60%（激进） |
| 硬门禁时机 | 覆盖率 ≥45% 后收紧 | 立即硬门禁（可能长期失败） |
| 门禁归属 | 并入 observability-ci（已有消费方） | 独立 workflow（多余触发面） |

> 本方案仅建议 S1 已具备实现条件（复用现有 step）；S2/S3 是否落地、红线取值待你确认后再实施。
