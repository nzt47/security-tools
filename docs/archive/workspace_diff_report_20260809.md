# 工作区代码差异报告（2026-08-09）

> 用途：手动比对当前 develop 工作区修改与已提交基线，确认是否丢失 phase2 分支关键功能。

## 1. 基线信息

- 当前分支: `develop`
- HEAD: `4ccfbfd9` (`docs(zh): 知识模块优化 Pending 待办清单（Jira/Confluence 凭据项）`)
- 恢复的 phase2 分支: `phase2-visibility-convergence` @ `3854a3b6`

## 2. 工作区修改统计

```
.gitignore                                         |   3 +
 .vscode/settings.json                              |  37 ++-
 README.md                                          |  10 +
 agent/ab_testing.py                                |  26 +-
 agent/api_gateway.py                               |  26 +-
 agent/async_executor.py                            |  64 ++++-
 agent/auto_tuner.py                                |  26 +-
 agent/cognitive/failure_analysis.py                |  27 +-
 agent/cognitive/failure_collector.py               |  27 +-
 agent/extensions/security_checker.py               |  24 +-
 agent/feedback.py                                  |  26 +-
 agent/knowledge/__main__.py                        | 115 +++++++-
 agent/knowledge/card.py                            |  83 +++++-
 agent/knowledge/index.py                           | 214 ++++++++++++++-
 agent/knowledge/links.py                           |  87 +++++-
 agent/lazy_loader/__init__.py                      |  24 +-
 agent/lazy_loader_async.py                         |  24 +-
 agent/log_system/optimized_storage.py              |  23 +-
 agent/log_system/safe_logger.py                    |  61 ++++-
 agent/logging_utils.py                             |  53 +++-
 agent/monitoring/alert_evaluator.py                |  49 +++-
 agent/monitoring/alert_manager.py                  |  53 +++-
 agent/monitoring/alert_notifier.py                 |  43 ++-
 agent/monitoring/chaos_injector.py                 |  36 ++-
 agent/monitoring/error_reporter.py                 |  26 +-
 agent/monitoring/loki.py                           |  23 +-
 agent/monitoring/observability_config.py           |  24 +-
 agent/monitoring/observability_optimizations.py    |  24 +-
 agent/monitoring/optimized_metrics.py              |  33 ++-
 agent/monitoring/performance.py                    |  52 +++-
 agent/monitoring/performance_optimization.py       |  41 ++-
 agent/monitoring/replay_storage.py                 |  49 +++-
 agent/monitoring/resource_monitor.py               |  32 ++-
 agent/monitoring/search.py                         |  44 ++-
 agent/monitoring/self_healer.py                    |  49 +++-
 agent/monitoring/sensitive_data_filter.py          |  23 +-
 agent/monitoring/tracing.py                        |  33 ++-
 agent/monitoring/tracing_cache.py                  |  24 +-
 agent/monitoring/tracing_sampling.py               |  24 +-
 agent/prompt_manager/deployment.py                 |  26 +-
 agent/prompt_manager/registry.py                   |  24 +-
 agent/prompt_manager/storage.py                    |  27 +-
 agent/prompt_manager/version_control.py            |  26 +-
 agent/safety_guard.py                              |  32 ++-
 agent/server_routes/routes_health.py               |  23 +-
 agent/server_routes/routes_knowledge.py            | 299 ++++++++++++++++++++-
 agent/server_routes/routes_logging.py              |  43 ++-
 agent/state_manager.py                             |  24 +-
 agent/system_prompt_config.py                      |  38 ++-
 agent/task_scheduler.py                            |  84 ++++--
 agent/tool_router_reranker.py                      |  32 ++-
 agent/utils/index_manager.py                       |  24 +-
 app_server.py                                      |  22 ++
 knowledge/index.md                                 |   4 +-
 knowledge/log.md                                   |  30 +++
 tests/conftest.py                                  |   4 +-
 tests/contract/contracts/chat_api_contract.json    |   2 +-
 tests/contract/contracts/chat_api_pact.json        |   2 +-
 .../contract/contracts/dashboard_api_contract.json |   2 +-
 tests/contract/contracts/dashboard_api_pact.json   |   2 +-
 tests/contract/contracts/health_api_contract.json  |   2 +-
 tests/contract/contracts/health_api_pact.json      |   2 +-
 .../integration/test_alert_notifier_integration.py |  11 +-
 .../integration/test_chaos_injector_integration.py |   7 +-
 .../test_performance_optimization_integration.py   |   7 +-
 .../integration/test_routes_logging_integration.py |   2 +-
 tests/integration/test_self_healer_integration.py  |   7 +-
 .../integration/test_task_scheduler_integration.py |   5 +-
 tests/unit/test_knowledge_card.py                  |  24 ++
 tests/unit/test_knowledge_cli.py                   | 130 +++++++++
 tests/unit/test_knowledge_links.py                 |  54 ++++
 tests/unit/test_misc_modules_comprehensive.py      |   5 +-
 tests/unit/test_orchestrator_refactor.py           |   6 +-
 tests/unit/test_performance_alert.py               |   2 +-
 yunshu-ui/src/App.tsx                              |  70 +++--
 yunshu-ui/src/utils/replayRecorder.ts              |   2 +-
 76 files changed, 2542 insertions(+), 226 deletions(-)
```

## 3. 已跟踪文件修改清单

### 已修改（未暂存）：76 个

- .vscode/settings.json
- README.md
- agent/ab_testing.py
- agent/api_gateway.py
- agent/async_executor.py
- agent/auto_tuner.py
- agent/cognitive/failure_analysis.py
- agent/cognitive/failure_collector.py
- agent/extensions/security_checker.py
- agent/feedback.py
- agent/knowledge/__main__.py
- agent/knowledge/card.py
- agent/knowledge/index.py
- agent/knowledge/links.py
- agent/lazy_loader/__init__.py
- agent/lazy_loader_async.py
- agent/log_system/optimized_storage.py
- agent/log_system/safe_logger.py
- agent/logging_utils.py
- agent/monitoring/alert_evaluator.py
- agent/monitoring/alert_manager.py
- agent/monitoring/alert_notifier.py
- agent/monitoring/chaos_injector.py
- agent/monitoring/error_reporter.py
- agent/monitoring/loki.py
- agent/monitoring/observability_config.py
- agent/monitoring/observability_optimizations.py
- agent/monitoring/optimized_metrics.py
- agent/monitoring/performance.py
- agent/monitoring/performance_optimization.py
- agent/monitoring/replay_storage.py
- agent/monitoring/resource_monitor.py
- agent/monitoring/search.py
- agent/monitoring/self_healer.py
- agent/monitoring/sensitive_data_filter.py
- agent/monitoring/tracing.py
- agent/monitoring/tracing_cache.py
- agent/monitoring/tracing_sampling.py
- agent/prompt_manager/deployment.py
- agent/prompt_manager/registry.py
- agent/prompt_manager/storage.py
- agent/prompt_manager/version_control.py
- agent/safety_guard.py
- agent/server_routes/routes_health.py
- agent/server_routes/routes_knowledge.py
- agent/server_routes/routes_logging.py
- agent/state_manager.py
- agent/system_prompt_config.py
- agent/task_scheduler.py
- agent/tool_router_reranker.py
- agent/tools/browser_tools.py
- agent/utils/index_manager.py
- app_server.py
- knowledge/index.md
- knowledge/log.md
- tests/conftest.py
- tests/contract/contracts/chat_api_contract.json
- tests/contract/contracts/chat_api_pact.json
- tests/contract/contracts/dashboard_api_contract.json
- tests/contract/contracts/dashboard_api_pact.json
- tests/contract/contracts/health_api_contract.json
- tests/contract/contracts/health_api_pact.json
- tests/integration/test_alert_notifier_integration.py
- tests/integration/test_chaos_injector_integration.py
- tests/integration/test_performance_optimization_integration.py
- tests/integration/test_routes_logging_integration.py
- tests/integration/test_self_healer_integration.py
- tests/integration/test_task_scheduler_integration.py
- tests/unit/test_knowledge_card.py
- tests/unit/test_knowledge_cli.py
- tests/unit/test_knowledge_links.py
- tests/unit/test_misc_modules_comprehensive.py
- tests/unit/test_orchestrator_refactor.py
- tests/unit/test_performance_alert.py
- yunshu-ui/src/App.tsx
- yunshu-ui/src/utils/replayRecorder.ts

### 已暂存：1 个

- gitignore

### 未跟踪：53 个

- "docs/zh/\347\237\245\350\257\206\345\272\223\351\207\215\346\236\204\350\256\241\345\210\222/\347\237\245\350\257\206\345\272\223API\346\216\245\345\217\243\346\226\207\346\241\243_openapi.yaml"
- agent/knowledge/audit_job.py
- agent/knowledge/conflict.py
- agent/knowledge/lint.py
- agent/knowledge/reporting.py
- agent/utils/singleton_manager.py
- data/_demo_broken_massive.py
- data/knowledge/
- docs/SingletonManager_Migration_Checklist.md
- docs/SingletonManager_Migration_Completion_Report.md
- docs/SingletonManager_Migration_Guide.md
- docs/SingletonManager_Migration_PPT_Outline.md
- docs/SingletonManager_Migration_Plan.md
- docs/SingletonManager_Migration_Priority_Report.md
- docs/SingletonManager_Migration_Progress_Report.md
- docs/SingletonManager_Performance_Report.md
- docs/troubleshooting/ci_cd_optimization_progress_20260808.md
- docs/troubleshooting/ci_runner_queue_diagnosis_20260808.md
- docs/troubleshooting/master_governance_retrospective_20260808.md
- docs/troubleshooting/p1_governance_summary_20260808.md
- docs/troubleshooting/workspace_diff_report_20260809.md
- kb_blank_state.png
- kb_check_no_entry.png
- kb_diag_page.png
- knowledge/index_links.md
- scripts/.tmp/
- scripts/bench_knowledge_links.py
- scripts/dev/kb_blank_check.py
- scripts/dev/kb_crud_smoke.py
- scripts/dev/kb_diag.py
- scripts/gen_workspace_diff_report.py
- tests/unit/test_alert_evaluator_singleton.py
- tests/unit/test_alert_manager_singleton.py
- tests/unit/test_alert_notifier_singleton.py
- tests/unit/test_audit_safety_logging_singleton.py
- tests/unit/test_knowledge_conflict.py
- tests/unit/test_knowledge_html_report.py
- tests/unit/test_knowledge_incremental.py
- tests/unit/test_knowledge_lint.py
- tests/unit/test_performance_alert_manager_singleton.py
- tests/unit/test_routes_knowledge.py
- tests/unit/test_search_performance_monitor_singleton.py
- tests/unit/test_self_healer_singleton.py
- tests/unit/test_singleton_manager.py
- tests/unit/test_singleton_performance.py
- tests/unit/test_system_prompt_config_singleton.py
- tests/unit/test_task_scheduler_singleton.py
- yunshu-ui/src/api/
- yunshu-ui/src/components/Knowledge/
- yunshu-ui/src/hooks/usePolling.ts
- yunshu-ui/src/pages/Knowledge.css
- yunshu-ui/src/pages/Knowledge.tsx
- yunshu-ui/src/test/knowledge.test.tsx

## 4. 可疑文件标记

- ⚠️ data/_demo_broken_massive.py
- ⚠️ scripts/.tmp/

## 5. 调试代码扫描（diff 中的 print/debugger/TODO）

- `print("=" * 70)`
- `+        print(f"健康报告邮件: {'已发送 ✓' if ok else '未发送（SMTP 未配置或失败，详见日志）'}")`
- `+    print(`
- `+        print(f"HTML 报告: {'已在浏览器打开 ✓' if opened else '打开失败'} {html_path}")`
- `+        print(`
- `+    print(`
- `+    print("[启动] 知识库 CardStore 已接线: knowledge/wiki")`
- `+    print(f"[启动] 知识库 CardStore 接线失败: {_kb_e}")`
- `+    print("[启动] 知识库 API 路由已注册: /api/knowledge/*")`
- `+    print(f"[启动] 知识库 API 路由注册失败: {_kb_r}")`
- `print("[启动] 开始加载网络配置...")`

## 6. phase2 分支关键功能对照（确认未丢失）

| 文件 | phase2 分支存在 | 当前 develop 工作区存在 | develop vs phase2 内容 |
|------|----------------|------------------------|----------------------|
| `agent/skills_mgmt/dependency_validator.py` | ✅ | ✅ | 🟢 一致 |
| `agent/skills_mgmt/memory_abstractor.py` | ✅ | ✅ | 🔴 有差异 |
| `agent/skills_mgmt/signal_scorer.py` | ✅ | ✅ | 🟢 一致 |
| `agent/skills_mgmt/store.py` | ✅ | ✅ | 🟢 一致 |
| `agent/skills_mgmt/enhancer.py` | ✅ | ✅ | 🟢 一致 |
| `agent/skills_mgmt/service.py` | ✅ | ✅ | 🔴 有差异 |
| `agent/workflow_learning/service.py` | ✅ | ✅ | 🔴 有差异 |
| `scripts/rebuild_p0_workflow.py` | ✅ | ✅ | 🟢 一致 |
| `tests/unit/test_rebuild_p0_workflow.py` | ✅ | ✅ | 🟢 一致 |
| `tests/unit/test_memory_skill_abstractor.py` | ✅ | ✅ | 🟢 一致 |
| `tests/unit/test_memory_abstractor_extreme_edge_cases.py` | ✅ | ✅ | 🟢 一致 |
| `tests/unit/test_abstract_from_memory_route.py` | ✅ | ✅ | 🟢 一致 |
| `tests/unit/test_skill_merge.py` | ✅ | ✅ | 🟢 一致 |
| `.github/workflows/p0-security.yml` | ✅ | ✅ | 🔴 有差异 |
| `docs/security/p0_incident_retrospective.md` | ✅ | ✅ | 🟢 一致 |

## 6.1 phase2 与 develop 关键差异

- **`agent/skills_mgmt/memory_abstractor.py`** 🔴: phase2 使用 LongTermMemory/TLM-L4 增强版；develop 使用 MemoryManager
- **`agent/skills_mgmt/service.py`** 🔴: phase2 含 SkillIndexCache / MCP adapter / SkillOutputGuard；develop 无
- **`agent/workflow_learning/service.py`** 🔴: phase2 的 try_execute 支持 min_score 覆盖参数；develop 无
- **`.github/workflows/p0-security.yml`** 🔴: phase2 含 master 分支触发 + checkout@v6/setup-python@v6；develop 为 checkout@v3

```diff
diff --git a/agent/skills_mgmt/memory_abstractor.py b/agent/skills_mgmt/memory_abstractor.py
index 58d13ba1..cba38ba2 100644
--- a/agent/skills_mgmt/memory_abstractor.py
+++ b/agent/skills_mgmt/memory_abstractor.py
@@ -286,7 +286,7 @@ class MemorySkillAbstractor:
                 "passed": sum(1 for r in results if r["quality_gate_passed"]),
                 "registered": sum(1 for r in results if r.get("registered")),
             }
-            logger.info("[MemAbstract] 抽象完成 | %s", str(summary))
+            logger.info("[MemAbstract] 抽象完成 | %s", summary)
             emit_metric("yunshu_memory_abstract_total",
                         value=len(results), kind="counter",
                         labels={"auto_register": str(auto_register)})
@@ -430,36 +430,26 @@ class MemorySkillAbstractor:
     def _load_long_term_memories(self, *, days: int) -> List[MemoryEntry]:
         """从长期记忆库加载"""
         try:
-            from agent.memory.long_term_memory import LongTermMemory
-            from datetime import datetime, timezone
+            from agent.memory_optimized import MemoryManager
         except Exception:
             return []
-        ltm = LongTermMemory()
+        mgr = MemoryManager()
         cutoff = self._cutoff_ts(days)
         entries: List[MemoryEntry] = []
-        # [TLM-L4] 改用 list_recent（替代 list_unverified + list_sensitive，避免重复）
-        raw_entries = ltm.list_recent(limit=200)
-        for entry in raw_entries:
-            if entry.created_at and entry.created_at < cutoff:
+        for mem in mgr.list_recent(limit=200):
+            ts = mem.get("timestamp", "")
+            if ts and self._parse_ts(ts) < cutoff:
                 continue
-            content = entry.content
-            if isinstance(content, dict):
-                task_text = content.get("content") or content.get("summary", "")
-            else:
-                task_text = str(content) if content else ""
-            meta = entry.metadata or {}
             entries.append(MemoryEntry(
                 source="long_term_memory",
-                source_id=str(entry.key),
-                task_text=task_text,
-                success=bool(meta.get("success", True)),
-                tool_calls=meta.get("tool_calls", []),
-                params=meta.get("params", {}),
-                tags=list(entry.tags or []),
-                timestamp=datetime.fromtimestamp(
-                    entry.created_at, tz=timezone.utc
-                ).isoformat() if entry.crea
```

## 7. 结论

- phase2 分支已通过 gitee 远程完整恢复（458 个 commit，含全部 skills_mgmt / workflow_learning / P0 相关文件）。
- 当前 develop 工作区修改为 SingletonManager 迁移 + 知识库重构，属正常开发，与 phase2 分支功能无冲突。
- phase2 分支大部分关键文件与 develop 一致；仅 4 个文件存在实质差异（均为 phase2 独有的增强功能，见 6.1）。
- 如需将 phase2 增强合并回 develop：`git checkout develop && git merge phase2-visibility-convergence`
