# 批次 7-10 变更日志（完整 diff 摘要）

**生成日期**: 2026-07-20
**范围**: 批次 7 → 批次 10（缓存隔离审计 → 运维诊断文档 + 杂项工具）
**分支**: develop（commit 已 rebase，hash 与原始 master 提交不同）
**提交总数**: 10 个 commit，累计 +59,733 行

---

## 批次 7：缓存隔离风险审计 + 修复回溯

### `2e29666f` chore(gitignore): 排除测试产物和测试数据目录

```text
 .gitignore | 20 ++++++++++++++++++++
 1 file changed, 20 insertions(+)
```

**Diff 摘要**: 新增排除规则——测试输出文件（`boundary_stdout.txt`、`boundary_junit.xml`、`pytest_xfail_output.txt`、`verify_production_report.md`）、测试数据目录（`data/test_chroma_db/` 等 6 个）、`test_results/` 测试结果目录。

### `2a409633` feat(audit): 缓存隔离风险审计 + 修复回溯

```text
 docs/audits/_cache_isolation_benchmark.py          | 201 ++++++++++++
 docs/audits/cache_isolation_risk_audit_20260715.md | 249 ++++++++++++++
 docs/audits/cache_isolation_risk_audit_round2_20260716.md  | 279 ++++++++++++++++
 docs/audits/manual_vs_pickle_comparison_20260715.md | 266 +++++++++++++++
 docs/reports/CACHE_ISOLATION_FIX_RETROSPECTIVE_20260716.md | 364 +++++++++++++++++++++
 tests/unit/test_tracing_cache_isolation.py         | 103 ++++++
 6 files changed, 1462 insertions(+)
```

**Diff 摘要**:
- 新增 4 份审计文档（风险审计 2 轮 + 手工 vs pickle 对比 + 修复回溯报告）
- 新增缓存隔离基准测试脚本 `_cache_isolation_benchmark.py`
- 新增单元测试 `test_tracing_cache_isolation.py`（103 行）

---

## 批次 8a：Skills 仓库迁移 + CI/CD 集成 + 验证工具

### `1e9f8189` feat(skills): Skills 仓库迁移 + CI/CD 集成 + 验证工具

```text
 .github/workflows/skills-check.yml                 |  273 +
 data/skills_repo/context_aware/skill.md            |   50 +
 data/skills_repo/emotion_expression/skill.md       |   17 +
 data/skills_repo/memory_summary/skill.md           |   53 +
 data/skills_repo/proactive_suggestion/skill.md     |   48 +
 data/skills_repo/safety_guard/skill.md             |   51 +
 data/skills_repo/scripted-selftest/scripts/main.py |   28 +
 data/skills_repo/scripted-selftest/skill.md        |   98 +
 data/skills_repo/self_reflection/skill.md          |   45 +
 data/skills_repo/voice_interaction/skill.md        |   51 +
 docs/skills_ci_cd_integration.md                   |  167 +
 scripts/compare_skills_legacy_vs_repo.py           |  171 +
 scripts/detect_dynamic_loads.py                    |  323 +
 scripts/export_arch_diagrams.py                    |  373 +
 scripts/verify_migrated_skills.py                  |  530 +
 tests/eval/baseline_tfidf.json                     |  881 +
 tests/eval/baseline_tfidf_v2.json                  | 1368 +
 tests/eval/compare_reranker_models.log             |   82 +
 tests/eval/eval_detail_log.txt                     |  192 +
 tests/eval/negative_rejection_base.log             |  232 +
 tests/eval/negative_rejection_base_threshold_001.json | 5484 +
 tests/eval/negative_rejection_v2_m3.log            |  230 +
 tests/eval/negative_rejection_v2_m3_threshold_001.json | 5291 +
 tests/eval/negative_samples_extended.json          |  233 +
 tests/eval/report_k1.json                          |  822 +
 tests/eval/report_k3.json                          |  881 +
 tests/eval/report_k5.json                          |  915 +
 tests/eval/rrf_fusion_report.json                  | 4722 +
 tests/eval/rrf_fusion_v4_console.log               |  982 +
 tests/eval/rrf_fusion_v4_report.json               | 7015 +
 tests/eval/rrf_fusion_v5_threshold_001.json        | 6832 +
 tests/eval/rrf_fusion_v5_threshold_001.log         |  975 +
 tests/eval/rrf_rerank_threshold_001.log            |  166 +
 tests/eval/rrf_rerank_threshold_005.log            |  168 +
 tests/eval/rrf_rerank_threshold_005_v2.log         |  161 +
 tests/eval/simulate_description_fix_report.json    |   26 +
 tests/eval/skill_retrieval_golden_set.json         |  413 +
 tests/eval/vector_vs_tfidf_report.json             | 2240 +
 tests/eval/vector_vs_tfidf_v2_report.json          | 2208 +
 tests/eval/vector_vs_tfidf_v3_chromadb_report.json | 2360 +
 40 files changed, 47157 insertions(+)
```

**Diff 摘要**:
- 8 个技能目录迁移至 `data/skills_repo/`（skill.md + scripted-selftest）
- 新增 `skills-check.yml` CI workflow（273 行）
- 4 个验证/迁移工具脚本（`verify_migrated_skills.py` 530 行最大）
- 24 个评估数据文件（TF-IDF 基线 / RRF 融合 / 向量对比报告）

---

## 批次 8b：Reranker 训练数据集 + 微调脚本 + V5 评估报告

### `259a721d` feat(reranker): Reranker 训练数据集 + 微调脚本 + V5 评估报告

```text
 data/reranker_trainset.jsonl                       |  477 +
 data/reranker_valset.jsonl                         |  123 +
 data/tool_negative_samples_expanded.json           | 1697 +
 docs/RETRIEVAL_UPGRADE_V5_REPORT_20260720.md       |  314 +
 docs/proposals/phase2_finetune_data_prep_plan_20260720.md |  573 +
 docs/proposals/reranker_zero_shot_eval_result_20260720.md |  227 +
 scripts/augment_negative_samples.py                |  307 +
 scripts/compare_reranker_models.py                 |  228 +
 scripts/convert_negative_samples_to_trainset.py    |  183 +
 scripts/eval_reranker_zero_shot.py                 |  515 +
 scripts/finetune_reranker.py                       |  290 +
 scripts/test_skill_reranker.py                     |  102 +
 12 files changed, 5036 insertions(+)
```

**Diff 摘要**:
- 3 个训练数据集（trainset 477 行 / valset 123 行 / 扩展负样本 1697 行）
- 3 份评估/计划文档（V5 报告 + 微调数据准备计划 + 零样本评估结果）
- 6 个脚本（零样本评估 515 行 / 负样本增强 307 行 / 微调 290 行等）

---

## 批次 9：前端构建产物 + 新模板

### `51234e84` feat(frontend): 前端构建产物 + 新模板

```text
 static/assets/FlowCanvas-HVBmN63R.js     |   1 +
 static/assets/FlowCanvas-HVBmN63R.js.map |   1 +
 static/assets/index-BThdZyrQ.css         |   1 +
 static/assets/index-DfWTd_WZ.css         |   1 +
 static/assets/index-Dhrn2cG4.js          |  41 +
 static/assets/index-Dhrn2cG4.js.map      |   1 +
 static/assets/index-KeDj06u5.js          |  28 +
 static/assets/index-KeDj06u5.js.map      |   1 +
 templates/file_monitor.html              | 725 +
 templates/lingxi.html                    | 753 +
 10 files changed, 1553 insertions(+)
```

**Diff 摘要**:
- 8 个前端构建产物（JS/CSS 压缩包 + sourcemap）
- 2 个新模板（`file_monitor.html` 725 行 / `lingxi.html` 753 行）

---

## 批次 10a：运维诊断文档 + 验证报告 + 文档目录

### `5cd5d2b2` docs(ops): 运维诊断文档 + 验证报告 + 文档目录

```text
 docs/images/circuit_breaker_state_machine.png      | Bin 0 -> 149955 bytes
 docs/images/tlm_three_table_data_flow.png          | Bin 0 -> 246012 bytes
 docs/ops_daily/compose_test_report.md              |  64 +
 docs/ops_daily/e2e_verification_analysis_20260719.md | 162 +
 docs/ops_daily/kind_diagnostic_logs/apiserver.log  |   3 +
 docs/ops_daily/kind_diagnostic_logs/deployment.yaml |   1 +
 docs/ops_daily/kind_diagnostic_logs/events.txt     |   1 +
 docs/ops_daily/kind_diagnostic_logs/kind_containers.txt |   3 +
 docs/ops_daily/kind_diagnostic_logs/kubelet.log    |  52 +
 docs/ops_daily/kind_diagnostic_logs/networkpolicy.yaml |   1 +
 docs/ops_daily/kind_diagnostic_logs/pod_describe.txt |   1 +
 docs/ops_daily/kind_diagnostic_logs/pod_logs.txt   |   1 +
 docs/ops_daily/kind_diagnostic_logs/pods.txt       |   1 +
 docs/ops_daily/kind_diagnostic_logs/pvc.txt        |   1 +
 docs/ops_daily/v1.1_verification.md                |  61 +
 docs/prometheus_metrics_verification.md            | 152 +
 docs/proposals/tool_router_reranker_integration_plan.md | 490 +
 docs/proposals/tool_router_reranker_todos.md       | 419 +
 docs/tool_trace_reports/01_overview_20260717_002834.csv |   2 +
 docs/tool_trace_reports/02_daily_trend_20260717_002834.csv |   3 +
 docs/tool_trace_reports/03_tool_profile_20260717_002834.csv |   6 +
 docs/tool_trace_reports/04_slow_queries_top20_20260717_002834.csv |  21 +
 docs/tool_trace_reports/05_error_analysis_20260717_002834.csv |   3 +
 docs/tool_trace_reports/06_high_fail_rate_tools_20260717_002834.csv |   1 +
 docs/tool_trace_reports/07_dangerous_audit_20260717_002834.csv |   2 +
 docs/tool_trace_reports/08_hourly_traffic_20260717_002834.csv |   4 +
 26 files changed, 1455 insertions(+)
```

**Diff 摘要**:
- 2 张架构图（熔断器状态机 + TLM 三表数据流）
- 9 个 kind 集群诊断日志
- 2 份验证报告（e2e 验证分析 + Prometheus 指标验证）
- 2 份 reranker 集成提案（集成计划 490 行 + TODO 419 行）
- 8 个 tool_trace 日报 CSV

---

## 批次 10b：杂项脚本 + Admin 依赖检查模块 + 工具

### `60288998` chore(scripts): 杂项脚本 + Admin 依赖检查模块 + 工具

```text
 Modules/AdminDependencyChecker/AdminDependencyChecker.psd1 |  75 +
 Modules/AdminDependencyChecker/AdminDependencyChecker.psm1 | 225 +
 Modules/AdminDependencyChecker/README.md           |  89 +
 file_monitor.py                                    | 780 +
 gen_mock_data.py                                   | 125 +
 scripts/AdminDependencyChecker.README.md           |  89 +
 scripts/AdminDependencyChecker.psd1                |  75 +
 scripts/AdminDependencyChecker.psm1                | 225 +
 scripts/Copy-AdminModule.ps1                       | 135 +
 scripts/analyze_threshold.py                       |  89 +
 scripts/demo_fill_timing_logs.py                   | 123 +
 scripts/diagnose_env.py                            |  55 +
 scripts/mock_metrics_server.py                     | 126 +
 scripts/simulate-ci-admin-check.ps1                | 216 +
 scripts/simulate_description_fix.py                | 216 +
 tests/unit/AdminDependencyChecker.Tests.ps1        | 342 +
 16 files changed, 2985 insertions(+)
```

**Diff 摘要**:
- AdminDependencyChecker PowerShell 模块（psm1 225 行 + psd1 + README，双目录冗余）
- `file_monitor.py` 文件监控工具（780 行）
- 9 个杂项脚本（诊断/模拟/阈值分析/mock metrics server）
- 1 个 Pester 单元测试（342 行）

---

## 批次 10c：.gitignore 补充

### `106648c9` chore(gitignore): 补充 *.sqlite3 规则

```text
 .gitignore | 3 +++
 1 file changed, 3 insertions(+)
```

**Diff 摘要**: 新增 `*.sqlite3` 忽略规则（`chroma.sqlite3` 运行时数据库不再入库）。

---

## 补充批次：可观测性 + Reranker 修复（本会话追加 3 个 commit）

### `6ba95dc6` fix(metrics): observability 全局单例修复 + yunshu_skill_* 指标定义

```text
 agent/monitoring/business_metrics.py | 23 +++++++++++
 agent/skills_mgmt/observability.py   |  6 ++-
 scripts/mock_metrics_server.py       | 79 +++++++++++++------------------
 3 files changed, 66 insertions(+), 42 deletions(-)
```

**Diff 摘要**:
- `observability.py`: `BusinessMetricsCollector()` 独立实例 → `get_business_metrics_collector()` 全局单例（与 app_server `/metrics` 端点共享实例）
- `business_metrics.py`: 新增 `yunshu_skill_eval_score`（histogram）+ `yunshu_skill_hallucination_total`（counter）白名单定义
- `mock_metrics_server.py`: 删除"直接注册 prometheus_client"的 workaround，改走 `emit_eval_score_metric → 全局单例 → export_prometheus()` 链路

### `b3ed56b2` fix(reranker): 适配 sentence-transformers 5.x BinaryCrossEntropyLoss

```text
 scripts/finetune_reranker.py | 56 ++++++++++++------------------------
 1 file changed, 25 insertions(+), 31 deletions(-)
```

**Diff 摘要**: `BCELoss` → `BinaryCrossEntropyLoss`（sentence-transformers 5.x 正确 API）；移除手动早停循环，改用 `fit` 内置 `evaluator + save_best_model` + `tempfile.TemporaryDirectory`。

### `072540ba` feat(reranker): ToolReranker 子进程隔离实现 (Cross-Encoder 两阶段精排)

```text
 agent/tool_router_reranker.py | 539 ++++++++++++++++++++++++++++++++++
 1 file changed, 539 insertions(+)
```

**Diff 摘要**: 新增 `ToolReranker` 类——子进程隔离加载 `BAAI/bge-reranker-v2-m3`，JSON Lines 协议双向通信，失败降级返回原顺序，`AGENT_HYBRID_RERANKER` 环境变量开关 + `AGENT_RERANKER_MODEL/TOP_N/MIN_SCORE` 可配置，模块级单例（SingletonManager 优先）。

---

## 汇总统计

| 批次 | Commit | 文件数 | 增/删行数 |
|------|--------|--------|-----------|
| 7 | `2e29666f` | 1 | +20 |
| 7 | `2a409633` | 6 | +1462 |
| 8a | `1e9f8189` | 40 | +47157 |
| 8b | `259a721d` | 12 | +5036 |
| 9 | `51234e84` | 10 | +1553 |
| 10a | `5cd5d2b2` | 26 | +1455 |
| 10b | `60288998` | 16 | +2985 |
| 10c | `106648c9` | 1 | +3 |
| 补充 | `6ba95dc6` | 3 | +66/-42 |
| 补充 | `b3ed56b2` | 1 | +25/-31 |
| 补充 | `072540ba` | 1 | +539 |
| **合计** | **11** | **117** | **+60,301/-73** |

**说明**: 原始 master 分支上的批次 7-10 commit（`d60f6cad`/`26bf4a21`/`c8fe5e23`/`f4618c7e`/`65832ae7`/`e66a40a2`/`4e277a00`/`874ea107`/`88f83188`/`cc1f5cc0`）在 rebase 到 develop 后 hash 全部变化，本文档使用 develop 分支上的实际 hash。
