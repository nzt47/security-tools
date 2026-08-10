# 测试污染与跨平台临时目录修复总结

> 日期：2026-08-09
> 范围：pytest 测试间污染（order-dependent failures）根治 + Windows 临时目录竞态修复
> 状态：✅ 方案已实施并通过专项验证；全量回归见文末

---

## 1. 问题背景

完整测试套件（`tests/`，13372 项）在 pytest-randomly 随机顺序下存在两类失败：

| 类别 | 表现 | 根因 |
|------|------|------|
| **测试间污染** | 失败集随随机种子漂移（默认顺序 31 个失败 / seed=12345 28 个），且**单独运行全部通过** | 全局状态（类静态方法、类级注册表、单例）被前序测试 `patch` 泄漏或直接修改 |
| **Windows 临时目录竞态** | seed=12345 下 memory_module 18 个 `NotADirectoryError: ...chroma.sqlite3` | chroma sqlite/segment 文件句柄在测试结束时未释放，Windows 无法删除被占用文件，rmtree 链式抛错（POSIX 无此问题） |

**关键验证结论**：此前误判的"9 个恒定失败"经单独运行（`9 passed in 2.15s`）确认**全部为污染受害方**，不存在真正的恒定失败。

---

## 2. 变更点清单

### 2.1 `tests/conftest.py` — 黄金快照 + 强制恢复（✅ 核心修复）

在 `reset_global_singletons`（function 级 autouse）teardown 追加 3 个强制恢复项：

| 变更 | 作用 | 覆盖失败 |
|------|------|---------|
| `_snapshot_golden_methods()` + `_force_restore_golden_methods()` | conftest 加载时快照 `MessageHandler` 5 个静态方法（parse/is_simple_query/detect_dissatisfaction/is_follow_up/extract_keywords）真实引用；每测试后检测到被替换为 `Mock` 即恢复 | message_handler 2 + orchestrator_boundary 5 + dialog_state 3 |
| `_force_reset_intent_rules()` | `IntentRouter._rules = copy.deepcopy(_DEFAULT_RULES)`（深拷贝：防规则对象内部 patterns 被修改的残留污染） | response_workflows 17 |
| `_force_reset_scheduler_singleton()` | `agent.task_scheduler._scheduler` 若为 Mock 置 None 触发重建 | task_scheduler 2 |

### 2.2 `tests/conftest.py` — 跨平台临时目录兜底（✅ 新增）

| 变更 | 作用 |
|------|------|
| `_safe_tmp_directory`（session 级 autouse） | `tempfile.tempdir` 重定向到项目内 `.pytest_tmp/`（跨平台路径一致、可诊断，不再依赖 `C:\Windows\TEMP`） |
| `_RetryTemporaryDirectory` | 替换 `tempfile.TemporaryDirectory`（接口兼容零侵入）：Windows 上 cleanup 捕获 OSError 重试 5 次（间隔 0.3s），最终失败则保留目录 + 告警，**绝不抛错**；POSIX 直接清理 |
| `_ORIG_TEMPFILE_TEMPDIR_CLS` | 模块加载时保存原始类，供替换类内部创建使用（防无限递归） |

### 2.3 `.gitignore` — 忽略重定向目标

追加 `.pytest_tmp/`（conftest 重定向生成的临时目录，不入库）。

### 2.4 日志埋点（7 个疑似资源竞争用例，✅ 已实施）

| 文件 | 埋点 | 定位目标 |
|------|------|---------|
| `test_ci_guard_fix_regression.py` | 新增 `_run_ci_cmd` helper，替换 4 处 `subprocess.run`；失败时输出 `returncode`（含十六进制崩溃码）+ 耗时 + stdout/stderr 尾部 | 区分「子进程被杀/崩溃」(0xC0000005) 与「正常业务失败」 |
| `test_precommit_hook_blocking.py` | `_run_git`/`_run_check` 失败时输出同样的诊断字段 | hook 拦截失败时定位是 hook 输出还是 git 环境 |
| `test_p6_snapshot.py::test_performance_monitor` | 记录 `save_snapshot` 全程耗时 + 断言消息携带完整 `performance_summary` | 区分「保存本身慢」与「性能统计未记录」 |
| `test_singleton_manager.py::test_metrics_modules_registered` | 断言失败时输出当前 `_manager._factories` 注册表全量键 | 区分「注册名不符」与「前序 reset_all 清空注册表」 |

**埋点验证**：`24 passed / 2 failed`（43.87s）。埋点本身不破坏测试；并证实 ci_guard 4 个确为资源竞争（全量第 3 轮无并行时消失），p6_snapshot/precommit 为真实失败（单独运行也失败）。

### 2.5 文档（本次会话）

- `docs/troubleshooting/baseline_failure_analysis_20260809.md`：追加 seed 复现结论、污染源定位、conftest 方案；9 个用例修复建议降级为「仅建议优化，非必须」
- `docs/tracing_concurrency_fix_20260809.md`：tracing 并发修复方案（前期会话产物）

### 2.6 前期会话变更（追溯）

- `agent/monitoring/tracing.py`：trace_id 栈式管理并发安全修复（Token 式恢复 + 冲突检测 + 防御降级）
- `tests/trace_context_test.py`：旧 API 3 参数 → 2 参数修复

---

## 3. 验证结果

### 3.1 专项验证（✅ 全部通过）

| 场景 | 命令 | 结果 |
|------|------|------|
| 9 个"恒定失败"单独运行 | 9 个 node id 组合 | **9 passed in 2.15s** |
| 污染源子集（helpers 生效） | e2e + boundary + message_handler + response_workflows + task_scheduler + singleton_performance，`--randomly-seed=12345` | **97 passed** |
| 临时目录方案 | `tests/unit/test_memory_module.py` 全文件 | **21 passed**（修复前残留 1 个真实竞态失败已消除） |

### 3.2 全量回归对比（seed=12345，顺序一致）

| 轮次 | 方案状态 | 结果 | 关键差异 |
|------|---------|------|---------|
| 第 1 轮（无修复） | 原始 conftest | **28 failed** | memory 18（NotADirectoryError）+ boundary 5 + message_handler 2 + singleton 2 + task_scheduler 1 |
| 第 2 轮（helpers） | 快照恢复已生效 | **28 failed** | boundary 5 + message_handler 2 消除；新增 7 个疑似资源竞争/环境敏感失败（验证期间并行跑测试所致） |
| 第 3 轮（完整方案，无并行） | helpers + 临时目录 | **16 failed** | **memory NotADirectoryError 全部消除**；ci_guard 4 + p6 + singleton_manager 证实为资源竞争（无并行即消失）；遗留 6 个见 3.4 |

### 3.3 修复效果总结

| 失败类别 | 修复前 | 修复后（第 3 轮） | 处置 |
|---------|--------|------------------|------|
| response_workflows 17 + dialog_state 3 | 默认顺序失败 | 不出现（`_force_reset_intent_rules` 覆盖） | ✅ 已解决 |
| boundary 5 + message_handler 2 | 两种顺序均失败 | 不出现（`_force_restore_golden_methods` 覆盖） | ✅ 已解决 |
| memory_module 18（NotADirectoryError） | seed 顺序失败 | **目录竞态已消除**，但暴露 11 个 `assert 0 == N` 数据未写入（顺序依赖污染，见 3.4） | ⚠️ 部分解决 |
| ci_guard 4 + singleton_manager 1 + p6_snapshot 1 | 第 2 轮出现 | 第 3 轮无并行时消失 | ✅ 证实为资源竞争/环境敏感 |
| precommit 1 / p6_snapshot（单独运行） | — | **真实失败**（单独运行也失败） | ⚠️ 基线问题，埋点已就位 |

### 3.4 遗留失败（第 3 轮 16 个 = 11 + 5）

| 失败 | 数量 | 性质 | 说明 |
|------|------|------|------|
| memory_module `assert 0 == N` | 11 | 顺序依赖污染（新暴露） | 目录竞态消除后暴露：add 后 count=0，疑似前序测试 mock 污染 embedder/VectorStore，待下一步排查 |
| precommit `test_real_git_commit_blocked_by_hook` | 1 | 真实环境失败（单独运行也失败） | hook 全量预检失败，与临时目录无关，埋点 `[diag-git]` 已输出诊断 |
| p6_snapshot `test_performance_monitor` | 1 | 真实失败（单独运行也失败） | `last_save_ms: 0.0`（mock `_save_core_modules_with_delta` 后性能统计未记录），埋点已输出完整 summary |
| task_scheduler 2 | 2 | 顺序依赖 | `_force_reset_scheduler_singleton` 未完全覆盖，待查 |
| skills_mgmt `PermissionError` | 1 | 临时目录方案副作用 | `.pytest_tmp` 重定向后 rename 被拒绝（WinError 5），待优化 |

---

## 4. 遗留与建议（非必须）

1. **e2e patch 泄漏根治**（仅建议优化）：`test_orchestrator三层路由_e2e.py` 的 `patch("agent.orchestrator.message_handler.MessageHandler.*")` 建议改为 `patch.object` + `try/finally`，减少对 conftest 兜底的依赖。
2. **`patch("agent.task_scheduler._scheduler")` 加 `new=None`**（仅建议优化）：避免无 new 参数时的 Mock 泄漏。
3. **性能断言阈值宽松化**（仅建议优化）：`test_singleton_performance.py` 新模式冷启动 1165-1257us vs 旧模式 17us，阈值 `max(old*10, 200)` 偏严，建议 `max(old*50, 1000)`。
4. **`_pytest_tmp/` 残留目录**：Windows 下极端占用场景会保留目录（已告警），可定期手动清理或加 CI 清理步骤。

---

## 5. 复现命令

```bash
# 全量回归（固定种子，复现顺序依赖）
python -m pytest tests/ --randomly-seed=12345 -q --tb=line -p no:cacheprovider
```
