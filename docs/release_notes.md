# Release Notes — 测试隔离污染修复（Task #112）

- **版本**：v1.2.0（release/v1.2.0）
- **提交**：`9e46d2e2` `fix(test-isolation): 强制中断 sandbox 残留线程 + 终止 visibility-refresh 后台刷新 + 清空共享编码器缓存`
- **变更规模**：4 文件，+97 / -5
- **关联补丁**：`docs/test_isolation_fix_9e46d2e2.patch`

---

## 1. 背景

CI 全量测试在随机序（`pytest-randomly`）下出现大量偶发失败、超时中断与收集不完整，根因是测试间**状态污染**：模块级单例、`sys.modules` 缓存、后台线程在测试间累积泄漏，导致下游测试读到脏状态。本修复定位并消除 5 类污染源，保证全量随机序下无超时、收集完整、无污染类新增失败。

## 2. 根因分析

| # | 污染源 | 位置 | 影响 |
|---|---|---|---|
| 1 | 模块级单例累积 | error_handler / metrics / state_manager / tracing 等 getter 单例 | 计数器、字典、注册表跨测试累积，断言读到陈旧值 |
| 2 | `patch.dict(sys.modules)` 破坏模块缓存 | `test_vector_store_fallback.py` 等 | `__exit__` 清空新导入键但父包属性残留旧模块对象，`sys.modules` 与包属性不一致 |
| 3 | sandbox mock 线程残留 | `tests/unit/conftest.py` `_FakeMPProcess` | `while True` 死循环线程无法 join，残留吃满 CPU，拖慢后续测试（`test_concurrent_recording` 60s 超时、`os.scandir` 超时） |
| 4 | visibility-refresh daemon 线程泄漏 | `scripts/visibility_report.py` `serve_metrics._refresh_loop` | 测试结束仍持续触发重量级 `generate_report`，拖慢全量 |
| 5 | 共享编码器单例缓存被 mock 污染 | `memory/vector_store/vector_store.py` `_shared_encoder_cache` | mock 编码器被缓存进进程级单例，后续 sqlite-vec 测试命中缓存得到 MagicMock → vec0 DDL 构造失败降级 json → `expected sqlite_vec, got json`（随机序 `TestVectorStoreSqliteVecIntegration` 8 ERROR + backend 1 FAILED 根因） |

第 5 类是本次新定位的污染源：`VectorStore._get_shared_encoder()`（模块级单例，`vector_store.py` L159）在 `sentence_transformers` 被 mock 的上下文中被实例化后缓存 mock 编码器；`_init_sqlite_vec()`（L494）与 `_init_chroma()`（L536）均读取该缓存，即使后续测试 patch 了 `SentenceTransformer` 仍返回 mock，`get_sentence_embedding_dimension()` 得到 MagicMock 导致失败。

## 3. 修复方案

### 3.1 `scripts/visibility_report.py`（+14/-2）
`serve_metrics` 新增可选参数 `stop_event: Optional[threading.Event] = None`；`_refresh_loop` 改用 `stop_event.wait(timeout=max(refresh_interval, 10))` 替代 `time.sleep`。测试环境可显式停止后台刷新线程，默认 `None` 时行为与旧版完全一致（向后兼容）。

### 3.2 `tests/conftest.py`（+26）
`reset_global_singletons` autouse fixture 新增两项清理：
- **#11 sqlite_vec**：删除 `sys.modules` 中所有 `sqlite_vec*` 键，强制后续测试全新导入，避免 C 扩展重复加载/引用残留。
- **#12 共享编码器缓存**：`_vstore._shared_encoder_cache.clear()`，清除被 mock 污染的进程级单例缓存。

### 3.3 `tests/unit/conftest.py`（+53/-2）
- 新增 `_async_raise_thread()`：通过 `ctypes.PyThreadState_SetAsyncExc` 向目标线程注入 `KeyboardInterrupt`，中断死循环线程（Python 无 `Thread.kill`）。
- `_FakeMPProcess`：线程命名 `sandbox-mock-{id}`；`terminate()`/`kill()` 由空实现改为真中断。
- `mock_sandbox_spawn` teardown：遍历中断并 `join(timeout=1)` 残留 sandbox 线程，超时记警告暴露。

### 3.4 `tests/unit/test_visibility_export.py`（+9）
两个 `serve_metrics` 集成测试传入 `stop_event`，结束时 `stop_event.set()` 停止 refresh 线程，避免 patch 退出后泄漏。

## 4. 测试结果

### 4.1 全量随机序验证（develop 分支，seed 20260808，DISABLE_NATIVE_EXT=1，timeout=120）

```
9 failed, 12670 passed, 139 skipped, 26 xfailed, 3 xpassed, 10 errors in 1616.71s
```

- **无超时**（修复前存在 Timeout 中断与收集卡死）
- **收集完整**（9700+ 测试全量执行）

### 4.2 残余失败分类（全部为既有失败或环境产物，无本次修复引入）

| 类别 | 数量 | 说明 |
|---|---|---|
| 工具检索（q19/q01/G4） | 3 | 既有失败，独立运行同样失败 |
| `trace_context_test` | 2 | 既有失败（TypeError/AssertionError），独立运行同样失败 |
| sqlite_vec FAILED + ERROR | 12 | `DISABLE_NATIVE_EXT=1` 环境产物（sentence_transformers 被封禁） |
| 收集 ERROR（同名 basename 冲突） | 2 | `test_model_router`/`test_visibility_report` 根级与 unit 级同名，单独运行 66 passed，仅全量随机收集顺序触发 |

### 4.3 A/B 对照验证（第 5 类污染源）

- 临时禁用 #12 清理 → `1 failed, 8 errors`（复现全量中的预期失败）
- 启用 #12 清理 → 全部通过

### 4.4 修复前后对比

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 全量执行 | 超时中断、收集不完整 | 完整执行，无超时 |
| sqlite_vec 污染类失败 | 8 ERROR + 1 FAILED（随机序） | 消失 |
| 后台线程泄漏 | visibility-refresh 持续触发 | 显式停止，无泄漏 |
| sandbox 线程残留 | 死循环线程吃满 CPU | 强制中断 + join 清理 |

## 5. 后续建议

- 全量最终绿灯（CI 固定 seed）确认后进入发布流程。
- 若 CI 偶发失败，优先检查是否出现 `sandbox-mock-` 残留线程警告或真实 `generate.start` 日志。
- 长期防护：将 `reset_global_singletons` 迁移为 session 级基线 + 逐测试校验，并推进测试文件 basename 去重（消除同名收集冲突）。

---
*生成时间：2026-08-08 · 基于 develop（release/v1.2.0 分支创建时的快照）*
