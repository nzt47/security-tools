# P3-1 质量门禁覆盖率口径修复报告（2026-08-09）

> 范围：observability-ci 质量门禁（可观测性质量保障）覆盖率误判修复——读取口径 + 阈值对齐
> 提交：`6bebc43d`（master）
> 关联：master_governance_retrospective_20260808.md（治理复盘）、ci_runner_queue_diagnosis_20260808.md（排队诊断）

---

## 1. 问题根因

observability-ci 的「可观测性质量门禁」job（`observability-quality-gate`）两次误判失败（3703bd7d / 34f42cb6），均报 `覆盖率 22.80% 低于阈值 60.0%`。经日志与代码分析，根因有二：

### 1.1 读取口径错误（主因）

[observability_quality_gate.py](scripts/observability_quality_gate.py) 的 `check_coverage` 遍历结果目录，**匹配第一个路径含 "coverage" 的 JSON 报告**：

- 匹配到的实际是 `observability-unit-test-results-py3.x` 上传的**可观测性子模块局部覆盖率**（仅 7 个测试文件、覆盖 3 个子模块，≈22.8%）
- 真正的全项目覆盖率 `full-coverage-report/coverage.xml`（6 个 shard 合并）是 **XML 格式**，而脚本只收集 `.json` 文件——**全项目覆盖率从未被门禁读取过**

### 1.2 阈值脱节（次因）

门禁传参 `--min-coverage 60`，但真实全项目覆盖率仅 **37.96%**，且 visibility-report 契约阈值为 **40%**（`verification.test_coverage` 默认值）。60% 阈值与真实水平脱节。

---

## 2. 修复改动（3 文件，+159/-28）

| 文件 | 改动 |
|---|---|
| `scripts/observability_quality_gate.py` | 新增 `_parse_coverage_xml()`：优先读取 `full-coverage-report/coverage.xml`，`line-rate`(0~1) 转百分比；`check_coverage` 改为 **XML 优先 → JSON 回退**；默认阈值 60→40 |
| `.github/workflows/observability-ci.yml` | 质量门禁 job 传参 `--min-coverage 60` → `40`（对齐 visibility-report 契约） |
| `tests/unit/test_quality_gate_coverage.py`（新增） | 5 个口径回归测试 |

**修复设计（三义）**：
- 【不易】门禁覆盖率唯一可信口径 = coverage-combine 合并 6 shard 的 `full-coverage-report/coverage.xml`（与 visibility-report 同契约）
- 【变易】XML 缺失时回退 JSON 提取，兼容历史场景
- 【简易】`_parse_coverage_xml` 独立方法，逻辑单一路径可读

---

## 3. 验收数据

### 3.1 单元测试（5 passed / 0 failed）

| 用例 | 场景 | 预期 | 结果 |
|---|---|---|---|
| test_full_coverage_report_xml_high_rate_passes | 全项目 XML 75% + 局部 JSON 22.8% 共存 | 读 XML → passed | ✅ |
| test_full_coverage_report_xml_low_rate_fails | 全项目 XML 40% + 局部 JSON 95% 共存 | 读 XML → failed（局部 JSON 不再误过） | ✅ |
| test_no_xml_fallback_to_json | 无 XML 有 JSON | 回退 JSON 提取 | ✅ |
| test_local_xml_not_mistaken_for_full | 仅局部 XML 22.8% | 读取任意 XML → failed（真实反映） | ✅ |
| test_no_reports_skipped | 无任何覆盖率报告 | skip 不失败（保持原语义） | ✅ |

### 3.2 真实数据模拟验证

用 run 31263942975 下载的 `full-coverage-report/coverage.xml` 模拟完整门禁：

| 场景 | 修复前行为 | 修复后行为 |
|---|---|---|
| 读取源 | observability-unit-test 局部覆盖率 | **full-coverage-report 全项目** |
| 覆盖率数值 | 22.80%（错误口径） | **37.96%（真实全项目）** |
| 阈值 40% 判定 | — | `覆盖率 37.96% 低于阈值 40.0%` → failed（诚实反映缺口） |

> 说明：修复后门禁仍 failed，是因为真实覆盖率 37.96% < 40% 阈值——这是**如实暴露**，而非误判。选择对齐 40% 意味着门禁反映真实覆盖率缺口，待后续提高覆盖率后转绿。

---

## 4. 关键决策记录

| 决策点 | 选项 | 选择 | 理由 |
|---|---|---|---|
| 覆盖率来源 | 局部 JSON / 全项目 coverage.xml | **全项目 coverage.xml** | 唯一可信口径；局部 22.8% 是两次误判根因 |
| 阈值 | 60% / 40% / 35% | **40%** | 对齐 visibility-report 契约；诚实反映 37.96% 缺口 |
| 回退策略 | 无回退 / JSON 回退 | **JSON 回退** | 兼容无 XML 的历史场景，保持 skip 语义 |

---

## 5. 遗留项

| 项 | 说明 |
|---|---|
| 覆盖率 37.96% < 40% | 门禁继续 failed 属预期；需提升全项目测试覆盖率至 40%+ 后转绿（P3 后续项） |
| CI 验证中 | head `6bebc43d` 推送后已触发新 run（31295085380 等），待 Shard 完成确认门禁行为 |
| P3-2 L3 Docker | `agent-test-sqlite-vec` pull 失败待修复（独立项） |
