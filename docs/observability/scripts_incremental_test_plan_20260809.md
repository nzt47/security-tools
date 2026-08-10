# scripts/ 目录增量测试计划（2026-08-09）

> 目标：将 scripts 层覆盖率从 **6.9%** 提升至 **50%**（S2 红线），分 3 批增量推进。
> 背景：scripts/ 占全项目有效行 43.8%，是覆盖率缺口的**第一大来源**（3609/52175 行覆盖）。
> 关联：[coverage_gap_short_term_plan](../archive/coverage_gap_short_term_plan_20260809.md) / [scripts_gate_integration_plan](../archive/scripts_gate_integration_plan_20260809.md)

---

## 1. 现状与测量口径

| 项 | 值 |
|---|---|
| 当前 scripts 覆盖率 | **6.9%**（3609 行覆盖 / 52175 行有效） |
| 测量配置 | [.coveragerc_scripts](file:///c:/Users/Administrator/agent/.coveragerc_scripts)：`source=scripts`，omit 掉 tests/agent |
| 现有测试入口 | CI「运行优化脚本单元测试」仅测 3 个缓存相关测试文件（`test_visibility_report_cache.py` 等），覆盖率有限 |
| 门禁脚本 | `check_scripts_coverage.py`（红线 50%、warn-gap 5pp）已入库，未接入 workflow（过渡期以 warn-gap 100 仅告警） |

## 2. 分批策略（按 ROI 排序）

> ⚠️ **估算口径修正**（2026-08-09 复核）：scripts 总有效行 52175，当前覆盖 3609（6.9%）。
> 单批 pp 贡献 = 本批有效行 / 52175。三批合计仅 +8.35pp，远不足以到达 50% 红线
> （需新增 22478 行覆盖）。**因此本计划是"治理起步"而非"达标"计划**——真正的达标
> 需要覆盖绝大多数核心脚本（见 §2.4 现实路径）。

### 批次 1：门禁/治理类脚本（高价值、低行数、易测试）—— +1.2pp

| 脚本 | 行数 | 价值 | 建议测试文件 |
|------|------|------|-------------|
| `observability_quality_gate.py` | 351 | 质量门禁核心，覆盖率判定逻辑 | `tests/unit/test_scripts_quality_gate.py` |
| `check_scripts_coverage.py` | 37 | scripts 门禁自身，红线/告警分支 | `tests/unit/test_scripts_coverage_gate.py` |
| `config_snapshot.py` | 93 | 配置快照/校验 | `tests/unit/test_scripts_config_snapshot.py` |
| `csv_to_md_table.py` | 79 | 纯函数（CSV→Markdown），极易 100% | `tests/unit/test_scripts_csv_to_md.py` |
| `ci_guard_types.py` | 98 | 类型定义，低逻辑 | `tests/unit/test_scripts_ci_guard_types.py` |

**本批核心目标**：让门禁自身的判定逻辑（line-rate 解析、阈值比较、warn/error 分支）被真实测试覆盖，
避免「门禁没被测过就生效」的治理风险。**pp 贡献虽小（+1.2pp），但治理价值最高。**

### 批次 2：CI 验证/分析类脚本（大文件、核心分析逻辑）—— +2.8pp

| 脚本 | 行数 | 价值 | 建议测试文件 |
|------|------|------|-------------|
| `check_boundary_coverage.py` | 994 | 边界覆盖扫描（大型） | `tests/unit/test_scripts_boundary_coverage.py` |
| `validate_ci_config.py` | 190 | CI 配置校验 | `tests/unit/test_scripts_validate_ci.py` |
| `check_circular_deps.py` | 185 | 循环依赖检测 | `tests/unit/test_scripts_circular_deps.py` |
| `scan_sensitive_files.py` | 165 | 敏感文件扫描 | `tests/unit/test_scripts_scan_sensitive.py` |
| `run_ci_guard.py` | 115 | CI 守卫执行 | `tests/unit/test_scripts_ci_guard.py` |

**本批核心目标**：覆盖 CI 门禁/扫描链路的真实判定逻辑，重点测**发现违规**与**放行**两个分支。

### 批次 3：报告生成类脚本（大文件、已有部分覆盖）—— +4.3pp

| 脚本 | 行数 | 价值 | 建议测试文件 |
|------|------|------|-------------|
| `visibility_report.py` | 1235 | 可见性报告（已有 cache 测试） | 扩展现有 `test_visibility_report_cache.py` |
| `generate_visibility_trend.py` | 1226 | 趋势生成 | `tests/unit/test_scripts_visibility_trend.py` |

**本批核心目标**：在已有缓存测试基础上，补充报告**渲染/聚合**路径的覆盖。

### 2.4 达标 50% 的现实路径（需用户决策）

3 批合计 4358 行 ≈ +8.35pp，覆盖率仅到 ~15%。要到 50% 红线，需新增 **22478 行覆盖**，
意味着必须覆盖 scripts/ 下绝大多数核心脚本（约 4 万行有效代码）。三条路径：

| 路径 | 说明 | 成本 |
|------|------|------|
| A. 全量补测 | 覆盖所有非一次性脚本（排除 simulate_*/demo_*/archive/） | 高：需新增 40+ 测试文件 |
| B. 调整 omit | 将 simulate_*/demo_*/archive/ 一次性脚本从覆盖统计排除（与覆盖计划既有 omit 思路一致） | 中：可显著降低分母 |
| C. 门禁分阶段 | 保持 50% 红线但过渡期告警（warn-gap 100），随 A/B 推进逐步收紧 | 低：已在 transition plan 约定 |

> **建议**：先落地批次 1（治理正确性）+ 路径 B（口径修正），再视缺口动态决定 A 的规模。

## 3. 执行细则

### 3.1 测试写法规范

- 每个测试文件统一 `import pytest`，用 `tmp_path` fixture 生成临时 xml/config 输入
- 门禁判定类函数（返回 exit code 的）用 `capsys` 捕获 `::error::`/`::warning::` 输出
- 覆盖 `if __name__ == "__main__"` 外的核心函数；`argparse` 入口通过 `monkeypatch.setattr(sys, "argv", ...)` 测 main()
- 保持 `--cov-config=.coveragerc_scripts` 口径一致

### 3.2 CI 接入（复用现有 step）

```yaml
# 在「运行优化脚本单元测试」step 的 pytest 命令中追加新增测试文件
python -m pytest \
  tests/unit/test_visibility_report_cache.py \
  tests/unit/test_test_quality_assess_cache.py \
  tests/unit/test_impact_analysis_cache.py \
  tests/unit/test_scripts_*.py \        # ← 新增
  -v --tb=short \
  --cov=scripts --cov-config=.coveragerc_scripts \
  --cov-fail-under=0 --cov-report=term-missing \
  --cov-report=xml:coverage_scripts.xml  # ← 供门禁消费
```

### 3.3 门禁收紧节奏（阈值联动）

| 阶段 | 覆盖率 | 门禁参数 |
|------|--------|---------|
| 过渡期（当前） | < 45% | `--warn-gap 100`（仅告警） |
| 临门期 | ≥ 45% | `--warn-gap 5` |
| 达标期 | ≥ 50% | `--fail-under 50 --warn-gap 5` |
| 提升期（S3） | ≥ 60% | `--fail-under 60 --warn-gap 5` |

## 4. 验收标准

| 项 | 标准 |
|---|---|
| 本地验证 | `pytest tests/unit/test_scripts_*.py --cov=scripts --cov-config=.coveragerc_scripts` 通过 |
| 覆盖率提升 | 批次 1 完成后 scripts 覆盖率 ≥ 25%；全部完成后 ≥ 50% |
| 门禁生效 | `check_scripts_coverage.py` 对 `coverage_scripts.xml` 判定正确（<50% exit 1 / ≥50% exit 0） |
| CI 接入 | observability-ci「优化脚本单元测试」step 纳入新测试文件 |

## 5. 不做的事（防过度工程）

- **不**为一次性运维脚本（`simulate_*`、`demo_*`、`archive/`）补测——它们不构成业务价值
- **不**修改 `pyproject.toml` omit 来"美化"数字（与 coverage_gap_short_term_plan 决策一致）
- **不**触碰 `scripts/dev/*`（PS1 辅助脚本，非 Python 覆盖目标）

> 本计划为执行蓝图，批次 1 可在下一迭代直接开始；批次 2/3 视批次 1 后的覆盖率缺口动态调整优先级。

---

## 6. 批次 2/3 细化（2026-08-10 补充）

> 批次 1 已完成（见 [scripts_batch1_test_cases_20260810.md](scripts_batch1_test_cases_20260810.md)，78 passed + 1 xfailed，
> 4 个脚本 100% 覆盖）。本段为批次 2/3 的测试点级细化，供后续迭代直接执行。

### 6.1 批次 2：CI 验证/分析类

| 脚本 | 测试文件（建议） | 核心测试点 | 优先级 |
|------|-----------------|-----------|--------|
| `check_boundary_coverage.py` (994行) | `tests/unit/test_scripts_boundary_coverage.py` | ① 覆盖率/边界差异解析；② 违规清单发现与豁免清单合并；③ exit code 判定（有违规/无违规）；④ CLI 参数覆盖 | P0 |
| `run_ci_guard.py` (115行) | `tests/unit/test_scripts_ci_guard.py` | ① 契约报告生成（含 `--json` 输出结构，需与 `ci_guard_types.validate_report` 对齐）；② 各步骤 exit_code 聚合；③ 状态 pass/fail 判定 | P0 |
| `validate_ci_config.py` (190行) | `tests/unit/test_scripts_validate_ci.py` | ① 配置 schema 校验通过/失败；② 缺失字段错误消息；③ 多配置文件路径 | P1 |
| `check_circular_deps.py` (185行) | `tests/unit/test_scripts_circular_deps.py` | ① 无环依赖图通过；② 单环/多环检出；③ 输出格式（节点对列表） | P1 |
| `scan_sensitive_files.py` (165行) | `tests/unit/test_scripts_scan_sensitive.py` | ① 命中敏感模式→违规；② 白名单/豁免；③ 目录递归与文件类型过滤 | P1 |

**执行顺序建议**：`run_ci_guard.py`（与批次 1 的 `ci_guard_types.py` 同链路，可联动验证契约）→ `check_boundary_coverage.py` → 其余按优先级。

### 6.2 批次 3：报告生成类（大文件、已有部分覆盖）

| 脚本 | 测试文件（建议） | 核心测试点 | 优先级 |
|------|-----------------|-----------|--------|
| `visibility_report.py` (1235行) | 扩展 `tests/unit/test_visibility_report_cache.py` | ① 报告聚合/渲染路径（补齐 cache 测试外的逻辑）；② 覆盖率 line-rate 读取；③ 输出 markdown 结构 | P1 |
| `generate_visibility_trend.py` (1226行) | `tests/unit/test_scripts_visibility_trend.py` | ① 周/月趋势聚合；② 空数据降级；③ 报告生成幂等 | P2 |

> 批次 3 文件大、逻辑深，建议先以**函数级冒烟 + 关键分支**切入（目标单脚本 ≥60%），
> 而非追求 100%——其行数对 scripts 总量 pp 贡献大（见 §2），是覆盖率的**量**来源。

### 6.3 依赖与风险

- 批次 2 的 `run_ci_guard.py` 依赖 `ci_guard_types` 契约——批次 1 已修复/暴露的缺陷（非 dict 步骤崩溃）修复后可先行联动
- 批次 3 依赖 CI 现产出的 `coverage.xml` 样例作为 fixture 输入（可从任一 run 的 artifact 提取）
- 所有批次测试统一遵循 §3.1 写法规范，测量口径 `--cov-config=.coveragerc_scripts` 不变
