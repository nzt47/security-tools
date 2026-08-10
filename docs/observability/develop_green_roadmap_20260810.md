# develop 分支非全绿修复路线图（2026-08-10）

> 目标：消除 develop 分支的 2 个持续 CI 失败项，恢复全绿。
> 背景：[verification_status_pr7_20260810.md](verification_status_pr7_20260810.md) 确认失败项与已合并代码无关，
> 但会阻塞后续每次 push 的 CI 结论。本路线图为治理执行蓝图。

---

## 1. 问题清单

| # | 失败项 | 根因 | 阻塞级别 | 所属方 |
|---|--------|------|---------|--------|
| P1 | 架构影响可见性检查 | `agent.knowledge.links_index → card` 循环依赖（links_index.py:103），1 个未豁免违规 | **高**（每次 push 失败） | knowledge 重构负责人 |
| P2 | 可观测性质量门禁 | 全项目覆盖率 22.40% < 60% 阈值；scripts/（43.8% 有效行）覆盖 6.9% | 中（存量，治理中） | 可观测性治理 |

## 2. 阶段划分（按依赖顺序，非时间承诺）

### 阶段 1：P1 架构违规清零（最高优先）

| 步骤 | 动作 | 验收 |
|------|------|------|
| 1.1 | knowledge 重构负责人修复 `links_index.py:103` 循环依赖（依赖倒置 / lazy_loader），或经评审纳入 `docs/architecture/legacy_exemptions.json` 豁免 | 架构检查 job 转绿 |
| 1.2 | 本地验证：`python -m agent.observability.arch_rules --check --root agent --exemptions docs/architecture/legacy_exemptions.json --config config.yaml` | 0 未豁免违规 |

> 决策点：**修复 vs 豁免**——修复为正解（根因治理）；豁免仅当重构成本高且影响面小。
> 注意：该文件改动来自其他进程（工作树存在 knowledge 未提交改动），需其负责人协同。

### 阶段 2：P2 第一步——批次 1 测试 CI 接入（方案已就绪）

| 步骤 | 动作 | 验收 |
|------|------|------|
| 2.1 | 按 [scripts_ci_integration_change_plan_20260810.md](scripts_ci_integration_change_plan_20260810.md) 将 5 个测试文件追加到「运行优化脚本单元测试」step | 单元测试 (3.11) step 显示 161 passed |
| 2.2 | 观察全项目覆盖率 shard（已自动收集 test_scripts_*，无需配置） | coverage.xml 中 scripts 覆盖行数提升 |

> 说明：4 个新测试文件已提交（f8d18622），全项目 shard 已自动纳入；本阶段仅需改 workflow。

### 阶段 3：P2 第二步——批次 2 补测（覆盖量的主要来源）

| 步骤 | 动作 | 目标 |
|------|------|------|
| 3.1 | 批次 2 P0：`run_ci_guard.py`（与 ci_guard_types 同链路）+ `check_boundary_coverage.py`（994 行） | 两脚本 ≥60% |
| 3.2 | 批次 2 P1：`validate_ci_config.py` / `check_circular_deps.py` / `scan_sensitive_files.py` | 各 ≥60% |
| 3.3 | 累计效果：scripts 覆盖率 6.9% → ~15%（+8.35pp 理论值，见计划 §2） | 门禁报告数字可观测提升 |

### 阶段 4：覆盖率口径决策（关键决策点）

> 路径 B（调整 omit）可在阶段 3 之前单独决策，见效最快。三条路径见计划 §2.4：

| 路径 | 内容 | 效果 | 决策时机 |
|------|------|------|---------|
| A | 全量补测（40+ 文件） | scripts ≥50% | 长期，成本高 |
| B | omit 排除 simulate_*/demo_*/archive 一次性脚本 | 分母显著下降，覆盖率跳升 | **可立即决策** |
| C | 门禁分阶段收紧（warn-gap 过渡） | 门禁不阻断但可见 | 已约定（transition plan） |

> 建议：阶段 2/3 推进的同时**决策路径 B**（口径修正），使门禁数字尽早反映真实业务覆盖率；
> 若 B 落地后覆盖率仍 <60%，再决定门禁阈值是否阶段性下调（需评审，非默认动作）。

### 阶段 5：红线达标（S2 目标）

- 依据 [scripts_incremental_test_plan](scripts_incremental_test_plan_20260809.md) 批次 3（visibility_report / generate_visibility_trend）补齐报告类脚本
- 红线 50% 达标后，按 transition plan 将门禁从 `warn-gap 100` 收紧到 `warn-gap 5`
- 长期（S3）：60% 红线渐进

## 3. 依赖与风险

| 项 | 说明 |
|----|------|
| 他人改动 | P1 依赖 knowledge 重构负责人；期间其未提交改动不要被误提交 |
| workflow 变更 | 阶段 2 需改 observability-ci.yml（已授权方案，实施时单步可回滚） |
| 门禁阈值调整 | 任何阈值下调须记录在案（【不易】收紧不可逆原则） |

## 4. 验收标准（全部满足即全绿）

- [ ] 架构影响可见性检查：success
- [ ] 可观测性质量门禁：success（覆盖率 ≥60%，或口径调整后达标）
- [ ] 单元测试 (3.11)：含 test_scripts_* 共 161+ passed
- [ ] 其余 job 保持现行通过状态

> 本路线图为执行蓝图，不含时间承诺；各阶段可并行推进（P1 独立于 P2-P5）。
