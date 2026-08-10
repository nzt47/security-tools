# 批次 1 测试 CI 接入变更方案（2026-08-10）

> 目标：将批次 1 的 5 个门禁类测试文件接入 observability-ci.yml，使门禁/治理脚本测试在 CI 真实执行。
> 前置：测试已本地验证 **79 passed, 0 failed, 0 xfailed**（[scripts_batch1_test_cases_20260810.md](scripts_batch1_test_cases_20260810.md)）。
> 关联：[scripts_incremental_test_plan_20260809.md](scripts_incremental_test_plan_20260809.md) §3.2

---

## 1. 变更位置

**唯一变更点**：`.github/workflows/observability-ci.yml` → `observability-unit-tests` job →
「运行优化脚本单元测试」step（当前 L427-441）。

该 step 现状（仅 Python 3.11 matrix 运行，避免矩阵重复）：

```yaml
- name: 运行优化脚本单元测试
  if: matrix.python-version == '3.11'
  run: |
    echo "=== 运行优化脚本单元测试（缓存优化逻辑验证）==="
    python -m pytest \
      tests/unit/test_visibility_report_cache.py \
      tests/unit/test_test_quality_assess_cache.py \
      tests/unit/test_impact_analysis_cache.py \
      -v \
      --tb=short \
      --cov=scripts \
      --cov-config=.coveragerc_scripts \
      --cov-fail-under=0 \
      --cov-report=term-missing \
      --timeout=300
```

## 2. 变更内容（方案 A：显式追加，推荐）

在 `test_impact_analysis_cache.py` 之后追加 5 个文件（与现有显式风格一致，避免通配误纳未来文件）：

```diff
     python -m pytest \
       tests/unit/test_visibility_report_cache.py \
       tests/unit/test_test_quality_assess_cache.py \
       tests/unit/test_impact_analysis_cache.py \
+      tests/unit/test_scripts_quality_gate.py \
+      tests/unit/test_scripts_coverage_gate.py \
+      tests/unit/test_scripts_ci_guard_types.py \
+      tests/unit/test_scripts_config_snapshot.py \
+      tests/unit/test_scripts_csv_to_md.py \
       -v \
```

**不变项**（【不易】）：
- `if: matrix.python-version == '3.11'` 保持——单版本执行，避免矩阵重复
- `--cov-config=.coveragerc_scripts` 保持——统计口径与门禁一致
- `--cov-fail-under=0` 保持——本 step 覆盖率不阻断，全项目覆盖率由质量门禁 job 判定
- `--timeout=300` 保持

**备选方案 B（通配）**：
```diff
       tests/unit/test_impact_analysis_cache.py \
+      tests/unit/test_scripts_*.py \
```
> 不推荐：`tests/unit/` 下未来新增的 `test_scripts_*` 文件会被静默纳入，绕过显式评审。

## 3. 影响评估

| 项 | 评估 |
|---|---|
| 用例增量 | +79 个用例（本地耗时 ~15s），CI 该 step 总用例从 82 → 161 |
| 覆盖率影响 | 5 个目标脚本：4 个 100%、quality_gate 90%，scripts 总量 +~5.6pp 覆盖行（391 行/52175 ≈ 0.75pp） |
| 触发条件 | 本次变更涉及 `.github/workflows/observability-ci.yml`（已在 paths 内）→ push 即触发 |
| 风险 | 低：测试全部本地通过，无外部依赖（tmp_path/纯函数） |

## 4. 验收标准

1. CI run 中「可观测性单元测试 (3.11)」job 该 step 显示 `161 passed`（82 既有 + 79 新增）
2. `coverage_scripts.xml`（若随后接入门禁）中 5 个目标脚本覆盖率 ≥90%
3. 3.10/3.12 matrix 不受影响（该 step 仅 3.11 运行）

## 5. 实施步骤

1. 编辑 `.github/workflows/observability-ci.yml` L434 后追加 5 行（见 §2 diff）
2. 本地复核：`python -m pytest tests/unit/test_scripts_*.py -q` → 79 passed
3. commit + push 触发 observability-ci
4. 观察 run：单元测试 (3.11) job 通过、无 xfail

> 本方案仅描述变更，**尚未修改 workflow**；确认后由用户授权实施（CI 变更影响面大，需显式确认）。
