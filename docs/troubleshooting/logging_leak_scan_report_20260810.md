# logging 资源泄漏风险 — 全项目详细扫描报告（2026-08-10）

> 性质：全项目 logging 全局状态泄漏风险盘点（配合 `scripts/check_logging_disable_leak.py`）
> 范围：仓库全部 `.py` 文件（1535 个可解析 + 1 个解析失败单独核查）
> 判定基线：`logging.disable` 修改进程级 `manager.disable`，泄漏会静默同进程后续 caplog/assertLogs（🔴）；`basicConfig(force=True)` 强制重置 root handlers（🟠）；无 force 的 `basicConfig` 在 root 已有 handler 时是 no-op（🟢）

---

## 1. 结论速览（TL;DR）

| 类别 | 命中 | 风险 | 需修复 |
|---|---|---|---|
| `logging.disable` 未受 try/finally 保护（测试代码） | **0** | — | 无 |
| `logging.disable` 模块顶层（scripts/ 独立基准脚本） | 4 | 🟢 低 | 否（刻意设计） |
| `logging.disable`（测试代码，已保护） | 2 | — | 已修复 |
| `basicConfig(force=True)` | 1 | 🟢 低 | 否（生产入口） |
| 模块级 `basicConfig`（无 force，进入 pytest 收集） | 1 | 🟢 低 | 否（no-op + 防线兜底） |
| `captureWarnings` | 0 | — | — |
| root `setLevel`（测试代码） | 2 | — | 均为恢复函数内（安全） |

**核心结论：测试代码（pytest 收集范围）内不存在 logging 资源泄漏风险。** 现有防线三层：测试内 try/finally 自恢复 → tests/conftest `manager.disable` 快照兜底 → CI/pre-commit 扫描器强制。

---

## 2. A 类：`logging.disable`（进程级，最危险）

由 `check_logging_disable_leak.py` 全项目扫描（1535 文件，`scripts/webhook_server.py` 因语法无法解析单独核查）：

| 文件:行 | 位置 | 状态 | 说明 |
|---|---|---|---|
| tests/integration/test_orchestrator三层路由_e2e.py:769 | 函数内 | ✅ 受保护 | try/finally 配对恢复（6ada3dc1） |
| tests/performance/test_knowledge_link_perf.py:39 | autouse fixture | ✅ 受保护 | try/finally 恢复（305282cf） |
| scripts/bench_knowledge_links.py:31 | 模块顶层 | ⚪ 豁免 | 独立基准脚本：刻意关闭日志避免 I/O 干扰计时，进程短生命周期，不进 pytest collection |
| scripts/bench_list_cache_compare.py:31 | 模块顶层 | ⚪ 豁免 | 同上 |
| scripts/probe_list_100k_perf.py:33 | 模块顶层 | ⚪ 豁免 | 同上 |
| scripts/run_p4_benchmark.py:17 | 模块顶层 | ⚪ 豁免 | 同上 |
| scripts/webhook_server.py | — | ⚪ 安全 | AST 解析失败（bytes 字面量语法），人工 grep 确认无任何 logging 全局状态调用 |

---

## 3. B 类：`basicConfig(force=True)`（强制重置 root handlers）

| 文件:行 | 位置 | 风险 | 说明 |
|---|---|---|---|
| app_server.py:74 | 生产入口 | 🟢 低 | 独立进程入口，`force=True` 重置 root 为该进程初始化的一部分，无测试泄漏语义。**tests/ 下 0 处 force=True** |

---

## 4. C 类：模块级 `basicConfig`（无 force，弱全局）

> 判定依据：pytest.ini `python_files = test_*.py *_test.py`、`testpaths = tests`；Python logging 语义——root 已有 handler 时 `basicConfig` 是 no-op；tests/conftest.py L100 已在 collection 早期安装 FileHandler+StreamHandler。

### 4.1 进入 pytest 收集范围（tests/ 下，匹配收集规则）

| 文件:行 | 收集 | 风险 | 说明 |
|---|---|---|---|
| tests/search_engine_test.py:20 | ✅（`_test.py` 后缀） | 🟢 低 | 无 force → root 已有 handler → no-op；即使生效也被 conftest 恢复 |
| tests/stress/test_tracing_stress.py:35 | ✅ 规则匹配但被分片排除 | 🟢 低 | `tests/stress/` 在 split 脚本 EXCLUDED，不进全项目分片 |

### 4.2 不被 pytest 收集（非 test_/_test 前缀或运行器）

| 文件:行 | 说明 |
|---|---|
| tests/run_tests.py:14 | 运行器脚本（run_ 前缀） |
| tests/contract/verify_provider.py:45 | verify_ 前缀 |
| tests/benchmark/benchmark_core.py:10 | benchmark_ 前缀 |
| tests/test_error_handling.py:459 | 函数内调用（非模块级） |

### 4.3 分片外测试目录（独立运行路径）

| 目录 | 命中 | 风险 | 说明 |
|---|---|---|---|
| agent/tests/ | 6 个文件（test_safety_guard、test_planning、test_permission_system、test_memory_manager、test_behavior_controller(_debug)） | 🟢 低 | 不在 observability-ci 分片（root=tests）；无 force。若由其他 workflow 独立跑，建议确认各自 conftest 防护（见 §7 遗留） |
| memory/tests/ | test_llm_stress.py:16 | 🟢 低 | 同上 |
| mcp_services/ | test_mcp_integration.py:12（模块级）、test_mcp_windows.py:426（函数内） | 🟢 低 | 同上 |

### 4.4 生产/工具脚本（80 处中的主体）

app_server.py、file_monitor.py、sensor_server.py、health_check.py、agent/knowledge/\_\_main\_\_.py、scripts/*.py、scripts/dev/*.py、agent/extensions/*.py、mcp_services/multi_search_engine.py 等 —— 均为**独立进程入口或工具**，`basicConfig` 是进程初始化的一部分，无泄漏语义。

---

## 5. D/E 类：`captureWarnings` 与 root `setLevel`

| 项 | 结果 |
|---|---|
| `captureWarnings` | 全项目 **0 处** |
| tests/conftest.py:398-404 | `manager.disable` 快照/恢复（本次治理补丁，最终兜底）✅ |
| tests/unit/conftest.py:107 | `_restore_golden()` 恢复函数内 `root.setLevel(_GOLDEN_LEVEL)` ✅（unit 目录专属更强防线：handlers/level/filters/formatter + `_isolate_test_loggers()` 子 logger 隔离） |

---

## 6. 防线全景（三层）

```
┌─ 第 1 层：测试内自恢复 ─────────────────────────────┐
│  logging.disable 必须 try/finally 或 autouse fixture │
│  （test_orchestrator / test_knowledge_link_perf 已修复）│
├─ 第 2 层：conftest 兜底 ────────────────────────────┤
│  reset_global_singletons：root handlers/level 快照   │
│  + manager.disable 快照（2026-08-10 补丁，最终兜底）   │
│  tests/unit/conftest：黄金状态 + 子 logger 隔离       │
├─ 第 3 层：自动化强制 ───────────────────────────────┤
│  pre-commit hook logging-disable-leak-scan           │
│  ci.yml code-quality「logging.disable 泄漏扫描」step  │
│  扫描器 --only-under tests --exit-nonzero-on-risk     │
└──────────────────────────────────────────────────────┘
```

---

## 7. 遗留风险与建议

| 项 | 风险 | 建议 | 优先级 |
|---|---|---|---|
| agent/tests/、memory/tests/、mcp_services/ 的模块级 basicConfig | 🟢 低（无 force） | 若这些目录由独立 workflow 跑 pytest，建议确认各自 conftest 有 root 状态防护（不在 observability-ci 分片覆盖内） | 🟢 低 |
| scripts/webhook_server.py 无法 AST 解析 | 🟢 低 | 该文件含非 ASCII bytes 字面量（疑似编码问题），建议后续修复后纳入扫描覆盖 | 🟢 低 |
| tests/search_engine_test.py:20 模块级 basicConfig | 🟢 低 | 无 force + conftest 兜底，当前安全；如未来改造为 force=True 会被扫描器/防线拦截 | 🟢 低 |

---

## 8. 附：CI 阻断验证（模拟提交）

`scripts/dev/verify_logging_leak_ci_block.py`：生成故意漏写 finally 的泄漏文件 → 以 ci.yml step 逐字相同的命令（`--root . --only-under tests --exit-nonzero-on-risk`）运行扫描 → 断言退出码非 0 → 清理。

实测结果：检出 `test_tmp_ci_block_probe.py:11 未受保护`，退出码 1 → **CI 的 logging.disable 泄漏扫描会正确报错阻断该类提交**。
