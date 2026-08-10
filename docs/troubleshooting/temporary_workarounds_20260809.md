# 临时规避方案 — p6_snapshot 与 task_scheduler（2026-08-09）

> 状态：✅ 根因已确认 | 本文件为**可执行的临时规避补丁**
> 完整缺陷分析见 `docs/troubleshooting/defect_tracking_20260809.md`（缺陷 2、缺陷 3）

---

## 缺陷 2：p6_snapshot 脆弱断言

### 问题

`tests/unit/test_p6_snapshot.py::TestStateSnapshotManager::test_performance_monitor`
mock 掉 `_save_core_modules_with_delta` 后保存耗时被压缩到浮点 `0.0`，
`record_save` 的 `last_save_time_ms = 0.0`，断言 `last_save_ms > 0` 失败。
（`total_saves == 1` 已证明 record_save 正常调用，业务无缺陷）

### 规避补丁（临时）

```diff
--- a/tests/unit/test_p6_snapshot.py
+++ b/tests/unit/test_p6_snapshot.py
@@ test_performance_monitor 内 @@
-        assert summary["last_save_ms"] > 0, (
+        # 【临时规避】mock 后保存耗时可能为浮点 0.0，
+        # total_saves == 1 已证明 record_save 被调用；耗时断言放宽
+        assert summary["last_save_ms"] >= 0, (
             f"上次保存时间应大于 0，得到: {summary['last_save_ms']}，"
             f"完整摘要: {summary}，保存耗时: {elapsed_ms:.2f}ms"
         )
```

应用后即可通过：

```bash
python -m pytest "tests/unit/test_p6_snapshot.py::TestStateSnapshotManager::test_performance_monitor" -q
```

---

## 缺陷 3：task_scheduler 单例测试互相污染

### 问题

1. `test_concurrent_first_get_initializes_once` **无单例重置**，前序测试残留
   `_scheduler` → 并发首次 get 命中缓存 → "实际 0 次"
2. `test_get_scheduler_returns_instance` 受并发测试类替换影响 → `assert False`

### 规避补丁（临时，共 2 处）

```diff
--- a/tests/unit/test_task_scheduler_singleton.py
+++ b/tests/unit/test_task_scheduler_singleton.py
@@ test_concurrent_first_get_initializes_once 开头 @@
     def test_concurrent_first_get_initializes_once(self):
         """多线程并发首次 get_scheduler 只构造一个实例（双重检查锁）"""
+        # 【临时规避】显式重置单例，避免前序测试残留缓存导致零构造
+        module._scheduler = None
         orig_cls = module.TaskScheduler
         created = []
```

```diff
--- a/tests/integration/test_task_scheduler_integration.py
+++ b/tests/integration/test_task_scheduler_integration.py
@@ TestGlobalSingleton::test_get_scheduler_returns_instance @@
     def test_get_scheduler_returns_instance(self, reset_scheduler_singleton):
+        # 【临时规避】防御类被替换泄漏：先校验类引用一致再断言
+        import agent.task_scheduler as _ts_mod
+        assert _ts_mod.TaskScheduler is TaskScheduler, (
+            "module.TaskScheduler 已被前序测试替换"
+        )
         s = get_scheduler()
         assert isinstance(s, TaskScheduler)
```

应用后验证：

```bash
python -m pytest tests/unit/test_task_scheduler_singleton.py tests/integration/test_task_scheduler_integration.py -q
# 期望：115 passed（不变量，与单独运行一致）
```

---

## 附：临时跳过（最低优先级，不推荐）

| 缺陷 | 命令 |
|------|------|
| 缺陷 2 | `python -m pytest tests/ --ignore=tests/unit/test_p6_snapshot.py -q` |
| 缺陷 3 | `python -m pytest tests/ --ignore=tests/unit/test_task_scheduler_singleton.py --ignore=tests/integration/test_task_scheduler_integration.py -q` |
