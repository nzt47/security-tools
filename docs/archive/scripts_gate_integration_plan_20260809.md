# scripts 门禁并入 observability-ci 实施步骤规划（2026-08-09）

> 目标：将 `check_scripts_coverage.py`（红线 50%，缺口≥5pp 阻断）正式并入 observability-ci，
> 复用现有「优化脚本单元测试」step 的 `--cov=scripts --cov-config=.coveragerc_scripts` 链路。
> 关联：[scripts_coverage_governance_plan_20260809.md](scripts_coverage_governance_plan_20260809.md)

---

## 实施步骤（共 5 步，均改 observability-ci.yml）

### 步骤 1：现有 step 追加 XML 报告输出

定位「优化脚本单元测试」step（observability-ci.yml L426-441），pytest 命令追加：

```bash
--cov-report=xml:coverage_scripts.xml
```

> 【不易】`--cov-config=.coveragerc_scripts` 保持不动——XML 统计口径与门禁一致。

### 步骤 2：新增门禁调用 step（紧接其后）

```yaml
- name: scripts 层覆盖率门禁（红线 50%）
  if: matrix.python-version == '3.11'   # 单版本执行即可，避免 3 版本重复判定
  run: |
    python scripts/check_scripts_coverage.py --xml coverage_scripts.xml
```

> 【变易】S3 上调红线时仅改 `--fail-under` 参数，脚本零改动。

### 步骤 3：上传 XML artifact（供趋势回溯）

```yaml
- name: 上传 scripts 覆盖率报告
  if: always()
  uses: actions/upload-artifact@v7
  with:
    name: scripts-coverage-report
    path: coverage_scripts.xml
    retention-days: 30
```

### 步骤 4：覆盖率数字写入 step summary

```yaml
- name: 汇总 scripts 层覆盖率
  if: always()
  run: |
    python - <<'EOF'
    import xml.etree.ElementTree as ET
    try:
        rate = float(ET.parse('coverage_scripts.xml').getroot().attrib['line-rate']) * 100
    except Exception:
        rate = float('nan')
    print(f"**scripts 层覆盖率: {rate:.2f}%** (红线 50%)" >> "$GITHUB_STEP_SUMMARY")
    EOF
```

### 步骤 5：触发 paths 确认

`scripts/check_scripts_coverage.py` 已在 paths 内吗？未含则追加：

```yaml
- 'scripts/check_scripts_coverage.py'
```

## 关键决策点

| 项 | 建议 | 理由 |
|---|---|---|
| 判定版本 | 仅 py3.11 | 单版本判定，避免矩阵重复执行同门禁 |
| continue-on-error | **保持默认（false）** | 门禁必须真实阻断；S2 告警期可临时设 true |
| S2 过渡策略 | 当前 scripts 覆盖率仅 6.9%，缺口 43.1pp ≥ 5pp 会**立即阻断** | 建议先补测到 45%+ 再启用硬门禁，或临时 `--fail-under 50 --warn-gap 50` 降为告警 |

## 验证方式

- 本地：`python scripts/check_scripts_coverage.py --xml <ci 产出的 xml>`（已验：6.9%→exit1 / 55%→exit0 / 缺文件→exit1）
- CI：push 触发 observability-ci → 观察新增 step 结论 + 覆盖率汇总

> 本规划仅输出修改步骤，待用户确认 S2 过渡策略（立即硬门禁 / 先告警）后实施。
