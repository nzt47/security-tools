# phase2 → develop 合并差异预览报告

> 生成时间：2026-08-09，用于合并前评估影响范围

## 1. 基本信息

- develop HEAD: `4ccfbfd9` (`docs(zh): 知识模块优化 Pending 待办清单（Jira/Confluence 凭据项）`)
- phase2 HEAD: `3854a3b6` (`merge: 合并远程 phase2-visibility-convergence 的 3 个修复 commit`)
- merge-base: `a58e0372`
- phase2 独有 commit: 309 个
- develop 独有 commit: 1203 个

## 2. 文件变化总览（develop → phase2 方向）

```
 1894 files changed, 665181 insertions(+), 448414 deletions(-)
```

## 3. 按目录分类的文件变化

| 目录 | 新增 | 修改 | 删除 | 合计 |
|------|------|------|------|------|
| `docs` | 1 | 32 | 423 | 456 |
| `scripts` | 1 | 13 | 370 | 384 |
| `tests` | 2 | 70 | 181 | 253 |
| `agent` | 8 | 91 | 56 | 155 |
| `data` | 14 | 1 | 96 | 111 |
| `"docs` | 0 | 4 | 60 | 64 |
| `.file_backups` | 59 | 0 | 0 | 59 |
| `packages` | 0 | 8 | 43 | 51 |
| `deploy` | 0 | 3 | 47 | 50 |
| `yunshu-ui` | 0 | 9 | 41 | 50 |
| `.github` | 0 | 14 | 31 | 45 |
| `monitoring` | 0 | 6 | 14 | 20 |
| `docker` | 0 | 2 | 15 | 17 |
| `"backup` | 0 | 0 | 14 | 14 |
| `static` | 0 | 0 | 8 | 8 |
| `templates` | 3 | 2 | 3 | 8 |
| `backup` | 0 | 0 | 7 | 7 |
| `coverage_report` | 5 | 0 | 0 | 5 |
| `"data` | 0 | 0 | 4 | 4 |
| `release-readiness-action` | 0 | 0 | 4 | 4 |
| `Modules` | 0 | 0 | 3 | 3 |
| `knowledge` | 0 | 0 | 3 | 3 |
| `memory` | 0 | 2 | 1 | 3 |
| `test_reports` | 3 | 0 | 0 | 3 |
| `lifetrace` | 0 | 2 | 0 | 2 |
| `releases` | 0 | 0 | 2 | 2 |
| `"tests` | 0 | 0 | 2 | 2 |
| `.commit_msg_fix.md` | 0 | 0 | 1 | 1 |
| `.commit_msg_test.md` | 0 | 0 | 1 | 1 |
| `.coverage` | 1 | 0 | 0 | 1 |
| `.dockerignore` | 0 | 1 | 0 | 1 |
| `.env.example` | 0 | 1 | 0 | 1 |
| `.gitattributes` | 0 | 0 | 1 | 1 |
| `.gitignore` | 0 | 1 | 0 | 1 |
| `.gitlab-ci.yml` | 0 | 0 | 1 | 1 |
| `.importlinter` | 0 | 0 | 1 | 1 |
| `.pre-commit-config.yaml` | 0 | 1 | 0 | 1 |
| `.secure_config.json` | 1 | 0 | 0 | 1 |
| `ARCHITECTURE_EVOLUTION_README.md` | 0 | 0 | 1 | 1 |
| `CHANGELOG.md` | 0 | 1 | 0 | 1 |
| `CHANGELOG_L2_ASYNC_IO_REVERT_20260726.md` | 0 | 0 | 1 | 1 |
| `Dockerfile` | 0 | 1 | 0 | 1 |
| `Dockerfile.linux-test` | 0 | 0 | 1 | 1 |
| `Dockerfile.test` | 0 | 1 | 0 | 1 |
| `Dockerfile.tlm-migrate` | 0 | 0 | 1 | 1 |
| `LICENSE` | 0 | 0 | 1 | 1 |
| `README.md` | 0 | 1 | 0 | 1 |
| `RELEASE_NOTES.md` | 0 | 0 | 1 | 1 |
| `RELEASE_PROCESS_TEMPLATE.md` | 0 | 0 | 1 | 1 |
| `RELEASE_V150_FINAL_ARCHIVE.md` | 0 | 0 | 1 | 1 |
| `_add_chat_trace.py` | 1 | 0 | 0 | 1 |
| `_check_logs.py` | 1 | 0 | 0 | 1 |
| `_commit_msg_v4.txt` | 1 | 0 | 0 | 1 |
| `_fix_calls.py` | 1 | 0 | 0 | 1 |
| `_mock_log.txt` | 1 | 0 | 0 | 1 |
| `_test_full.txt` | 1 | 0 | 0 | 1 |
| `_test_junit.xml` | 1 | 0 | 0 | 1 |
| `_test_logging.txt` | 1 | 0 | 0 | 1 |
| `_test_results.txt` | 1 | 0 | 0 | 1 |
| `_test_upload_flow.py` | 1 | 0 | 0 | 1 |
| `app_server.py` | 0 | 1 | 0 | 1 |
| `app_server_output.log` | 1 | 0 | 0 | 1 |
| `app_startup.log` | 1 | 0 | 0 | 1 |
| `check_and_fix.ps1` | 1 | 0 | 0 | 1 |
| `check_prometheus_rules.ps1` | 1 | 0 | 0 | 1 |
| `check_provider.py` | 1 | 0 | 0 | 1 |
| `check_provider2.py` | 1 | 0 | 0 | 1 |
| `check_sampler.py` | 1 | 0 | 0 | 1 |
| `commit_msg_missed.txt` | 1 | 0 | 0 | 1 |
| `config.py` | 0 | 1 | 0 | 1 |
| `config.yaml` | 0 | 1 | 0 | 1 |
| `config` | 0 | 0 | 1 | 1 |
| `config_secure.py` | 1 | 0 | 0 | 1 |
| `coverage.xml` | 0 | 1 | 0 | 1 |
| `diagnose_sampler.py` | 1 | 0 | 0 | 1 |
| `docker-compose.linux-test.yml` | 0 | 0 | 1 | 1 |
| `docker-compose.monitoring.aliyun.yml` | 0 | 1 | 0 | 1 |
| `docker-compose.monitoring.yml` | 0 | 1 | 0 | 1 |
| `docker-compose.tlm-migrate.yml` | 0 | 0 | 1 | 1 |
| `docker-compose.yml` | 0 | 1 | 0 | 1 |
| `file_monitor.py` | 0 | 0 | 1 | 1 |
| `full_cov_run.log` | 1 | 0 | 0 | 1 |
| `full_cov_run2.log` | 1 | 0 | 0 | 1 |
| `full_cov_run3.log` | 1 | 0 | 0 | 1 |
| `full_cov_run4.log` | 1 | 0 | 0 | 1 |
| `full_test_run.log` | 1 | 0 | 0 | 1 |
| `full_test_run2.log` | 1 | 0 | 0 | 1 |
| `full_test_run3.log` | 1 | 0 | 0 | 1 |
| `gen_mock_data.py` | 0 | 0 | 1 | 1 |
| `generate_guard_json_example.py` | 0 | 0 | 1 | 1 |
| `main.py` | 0 | 1 | 0 | 1 |
| `mcp_services` | 0 | 1 | 0 | 1 |
| `p0_regression_verify.log` | 1 | 0 | 0 | 1 |
| `persona` | 0 | 1 | 0 | 1 |
| `phase3_end_to_end.log` | 1 | 0 | 0 | 1 |
| `phase3_step1_debug.log` | 1 | 0 | 0 | 1 |
| `prometheus_export.log` | 1 | 0 | 0 | 1 |
| `pyproject.toml` | 0 | 1 | 0 | 1 |
| `pytest.ini` | 0 | 1 | 0 | 1 |
| `requirements-dev.txt` | 0 | 1 | 0 | 1 |
| `requirements-monitor.txt` | 0 | 0 | 1 | 1 |
| `requirements-test.txt` | 0 | 1 | 0 | 1 |
| `requirements.txt` | 0 | 1 | 0 | 1 |
| `run_evolution_demo.py` | 0 | 0 | 1 | 1 |
| `server.log` | 1 | 0 | 0 | 1 |
| `stress-report.json` | 0 | 0 | 1 | 1 |
| `test.log` | 1 | 0 | 0 | 1 |
| `test_ab_mock.py` | 1 | 0 | 0 | 1 |
| `test_cost_log.jsonl` | 1 | 0 | 0 | 1 |
| `test_metrics.py` | 1 | 0 | 0 | 1 |
| `test_observability.py` | 1 | 0 | 0 | 1 |
| `test_output.log` | 1 | 0 | 0 | 1 |
| `test_result.txt` | 1 | 0 | 0 | 1 |
| `test_run_result.txt` | 1 | 0 | 0 | 1 |
| `test_tracing_debug.py` | 1 | 0 | 0 | 1 |
| `test_ui_skills_mgmt.py` | 1 | 0 | 0 | 1 |
| `verify_all.ps1` | 1 | 0 | 0 | 1 |
| `verify_and_pull.ps1` | 1 | 0 | 0 | 1 |
| `verify_gui_config.ps1` | 1 | 0 | 0 | 1 |
| `verify_sampler.py` | 1 | 0 | 0 | 1 |
| `verify_simple.ps1` | 1 | 0 | 0 | 1 |
| `vis_missing_stderr.log` | 1 | 0 | 0 | 1 |
| `vis_report_test.log` | 1 | 0 | 0 | 1 |
| `vis_verify_stderr.log` | 1 | 0 | 0 | 1 |
| `visibility_run_stderr.log` | 1 | 0 | 0 | 1 |
| `visibility_run_stdout.log` | 1 | 0 | 0 | 1 |
| `visibility_stderr.log` | 1 | 0 | 0 | 1 |

## 4. 核心业务文件变化（agent/ 目录）

```
D	.github/gitleaks-config.toml
D	.github/monitoring/develop_stability_counter.json
D	.github/scripts/develop_stability_monitor.py
D	.github/workflow-templates/README.md
D	.github/workflow-templates/examples/flask-auth-p0-security.yml
D	.github/workflow-templates/examples/nodejs-auth-p0-security.yml
D	.github/workflow-templates/p0-security.template.yml
M	.github/workflows/architecture-check.yml
M	.github/workflows/boundary-guard.yml
M	.github/workflows/ci-cd.yml
D	.github/workflows/ci-failure-notify.yml
D	.github/workflows/ci-guard-runner.yml
M	.github/workflows/ci.yml
M	.github/workflows/config-drift-guard.yml
D	.github/workflows/core-invariants-guard.yml
D	.github/workflows/coverage-ci.yml
D	.github/workflows/daily_regression.yml
D	.github/workflows/deploy-pages.yml
D	.github/workflows/develop-ci-stability-monitor.yml
M	.github/workflows/extension-health-check.yml
D	.github/workflows/guard-master-commit-origin.yml
D	.github/workflows/hardcoded-password-scan.yml
D	.github/workflows/hook-failsafe-e2e.yml
D	.github/workflows/import-linter.yml
D	.github/workflows/intent-layer-ratio-check.yml
M	.github/workflows/kwarg-conflict-check.yml
M	.github/workflows/kwarg-docker-scan.yml
D	.github/workflows/kwarg-sonarqube.yml
D	.github/workflows/l3-docker-tests.yml
M	.github/workflows/log-perf-guard.yml
M	.github/workflows/observability-ci.yml
M	.github/workflows/p0-security.yml
D	.github/workflows/publish-psgallery.yml
D	.github/workflows/release-auto.yml
D	.github/workflows/release-docs.yml
D	.github/workflows/release-precheck.yml
D	.github/workflows/reranker-timeout-guard.yml
D	.github/workflows/sandbox-boundary-tests.yml
D	.github/workflows/semantic-perf-regression.yml
D	.github/workflows/skills-check.yml
M	.github/workflows/test.yml
D	.github/workflows/tool-retrieval-ci.yml
M	.github/workflows/tool-tests.yml
M	.github/workflows/web-module-tests.yml
D	.github/workflows/yunshui-ui-tests.yml
M	agent/__init__.py
M	agent/ab_testing.py
M	agent/caching/multi_level_cache.py
M	agent/circuit_breaker.py
M	agent/cognitive/knowledge.py
D	agent/common/stop_mixin.py
D	agent/config/etcd_config_client.py
D	agent/config_validation.py
A	agent/data/api_access.log
A	agent/data/api_keys.json
A	agent/data/extensions.json
A	agent/data/network_config.json
A	agent/data/role_assignments.json
A	agent/data/tenants.json
A	agent/data/users.json
M	agent/digital_life_persona.py
M	agent/disaster_recovery.py
D	agent/env_config_manager.py
M	agent/error_handler.py
M	agent/error_reporting_config.py
M	agent/extensions/security_check_skill.py
M	agent/extensions/security_checker.py
M	agent/graceful_degrade.py
M	agent/guardrails/output_schema.py
D	agent/handoff/__init__.py
D	agent/handoff/handoff_generator.py
M	agent/human_in_the_loop/hitl.py
D	agent/knowledge/__init__.py
D	agent/knowledge/__main__.py
D	agent/knowledge/card.py
D	agent/knowledge/discuss.py
D	agent/knowledge/distill.py
D	agent/knowledge/index.py
D	agent/knowledge/ingest.py
D	agent/knowledge/lifecycle.py
... 共 584 个
```

## 5. phase2 关键功能文件差异

### `agent/skills_mgmt/`

```
M	agent/skills_mgmt/__init__.py
D	agent/skills_mgmt/bm25_searcher.py
D	agent/skills_mgmt/conflict_resolver.py
M	agent/skills_mgmt/context_injector.py
M	agent/skills_mgmt/creator.py
M	agent/skills_mgmt/exceptions.py
M	agent/skills_mgmt/executor.py
D	agent/skills_mgmt/few_shot_injector.py
M	agent/skills_mgmt/file_store.py
D	agent/skills_mgmt/git_sync.py
D	agent/skills_mgmt/index_cache.py
M	agent/skills_mgmt/loader.py
D	agent/skills_mgmt/mcp_adapter.py
M	agent/skills_mgmt/memory_abstractor.py
M	agent/skills_mgmt/models.py
D	agent/skills_mgmt/negative_intent_detector.py
M	agent/skills_mgmt/observability.py
D	agent/skills_mgmt/offline_evolver.py
D	agent/skills_mgmt/output_guard.py
D	agent/skills_mgmt/reranker.py
D	agent/skills_mgmt/reranker_utils.py
M	agent/skills_mgmt/reviewer.py
M	agent/skills_mgmt/service.py
D	agent/skills_mgmt/vector_adapter.py
```

### `agent/workflow_learning/`

```
M	agent/workflow_learning/executor.py
M	agent/workflow_learning/matcher.py
M	agent/workflow_learning/service.py
```

### `.github/workflows/p0-security.yml`

```
M	.github/workflows/p0-security.yml
```

### `docs/security/`

```
D	docs/security/CICD_CACHE_CLEANUP_20260719.md
D	docs/security/COLLABORATOR_NOTICE_EMAIL_20260719.md
D	docs/security/EMBEDDING_CRASH_ANALYSIS_20260725.md
D	docs/security/GH_ACTIONS_CLEANUP_REPORT_20260720.md
D	docs/security/KEY_REVOCATION_VERIFICATION_20260719.md
D	docs/security/P1_FINAL_DELIVERY_REPORT_20260723.md
D	docs/security/P1_HARDCODED_PASSWORD_FIX_PLAN_20260720.md
D	docs/security/P1_P6_TASK_COMPLETION_CHECKLIST_20260720.md
D	docs/security/P1_P8_FINAL_CLOSURE_REPORT_20260720.md
D	docs/security/SECURITY_AUDIT_FINAL_SUMMARY_P1_P3_20260720.md
D	docs/security/SECURITY_AUDIT_REPORT.md
D	docs/security/SECURITY_AUDIT_UPDATE_P2_COMPLETE_20260720.md
D	docs/security/SECURITY_AUDIT_UPDATE_P3_COMPLETE_20260720.md
M	docs/security/p0_workflow_rebuild_summary.md
```

## 6. 冲突风险评估

### 6.1 工作区未提交修改与 merge 重叠

- 工作区未提交修改：79 个文件
- 与 merge 涉及文件重叠：41 个

**重叠文件（merge 前必须处理，否则 merge 会失败）：**

- README.md
- agent/ab_testing.py
- agent/disaster_recovery.py
- agent/extensions/security_checker.py
- agent/knowledge/__main__.py
- agent/knowledge/card.py
- agent/knowledge/index.py
- agent/knowledge/links.py
- agent/lazy_loader/__init__.py
- agent/logging_utils.py
- agent/monitoring/chaos_injector.py
- agent/monitoring/error_reporter.py
- agent/monitoring/observability_optimizations.py
- agent/monitoring/performance_optimization.py
- agent/monitoring/resource_monitor.py
- agent/monitoring/search.py
- agent/monitoring/tracing.py
- agent/monitoring/tracing_cache.py
- agent/server_routes/routes_knowledge.py
- agent/state_manager.py
- agent/system_prompt_config.py
- agent/task_scheduler.py
- agent/tool_router_reranker.py
- app_server.py
- knowledge/index.md
- knowledge/log.md
- tests/conftest.py
- tests/integration/test_alert_notifier_integration.py
- tests/integration/test_chaos_injector_integration.py
- tests/integration/test_performance_optimization_integration.py
- tests/integration/test_routes_logging_integration.py
- tests/integration/test_self_healer_integration.py
- tests/integration/test_task_scheduler_integration.py
- tests/unit/test_knowledge_card.py
- tests/unit/test_knowledge_cli.py
- tests/unit/test_knowledge_links.py
- tests/unit/test_orchestrator_refactor.py
- tests/unit/test_performance_alert.py
- yunshu-ui/src/App.tsx
- yunshu-ui/src/lib/apiClient.ts
- yunshu-ui/src/utils/replayRecorder.ts

## 7. 结论

- 两个分支自 merge-base 分叉巨大（phase2 309 commit / develop 1203 commit），merge 影响面很大。
- 若工作区存在与 merge 重叠的未提交修改，merge 会被拒绝；需要先 stash 或提交。
- 建议：先在干净工作区执行 merge，或使用 `git stash` 暂存当前开发。
