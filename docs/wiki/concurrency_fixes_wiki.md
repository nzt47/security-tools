# 并发缺陷修复 Wiki

> 最后更新：2026-07-11
> 维护者：团队共享
> 关联提交：`3cd54031`（死锁修复）、`0714d792`（multiprocessing 重构）

---

## 概述

本页面记录了项目近期修复的三个并发缺陷，以及团队在并发编程中应遵循的检查清单。

| 缺陷 | 类型 | 影响 | 修复提交 |
|------|------|------|---------|
| metrics.py 不可重入锁嵌套 | 确定性死锁 | `get_all_metrics()` 永久挂起 | `3cd54031` |
| DiskCache 持锁 I/O | 性能骤降 | 5 线程并发 set/get 超时 | `3cd54031` |
| run_sandbox threading 泄漏 | GIL 竞争 + 线程泄漏 | 全量测试 17+ 个误报失败 | `0714d792` |

**核心教训：** 这三个问题表现相似（测试超时/挂起），但根因完全不同。排查时必须先确认是死锁、性能问题、还是 GIL 竞争，再设计修复方案。

---

## 缺陷 1：metrics.py 不可重入锁嵌套

### 根因

`MetricsCollector` 使用 `threading.Lock`（不可重入锁）。`get_all_metrics()` 持有锁后调用 `get_stats()`，而 `get_stats()` 也尝试获取同一把锁，导致同一线程第二次 `acquire()` 永久阻塞。

```python
# ❌ 死锁代码
def get_all_metrics(self):
    with self._lock:                              # ① 获取锁
        histograms = {
            name: self.get_stats(name)            # ② get_stats 内部再次获取锁 → 死锁
            for name in self._histograms.keys()
        }

def get_stats(self, metric_name):
    with self._lock:                              # ③ 不可重入锁第二次 acquire → 阻塞
        values = list(self._histograms.get(metric_name, []))
```

### 修复

锁内只复制数据快照，锁释放后再调用 `get_stats()`：

```python
# ✅ 修复后
def get_all_metrics(self):
    with self._lock:
        histogram_names = list(self._histograms.keys())   # 锁内：复制名称
        counters = dict(self._counters)                   # 锁内：复制计数器

    histograms = {
        name: self.get_stats(name)                        # 锁外：调用 get_stats
        for name in histogram_names
    }
```

### 为什么不直接改用 RLock？

`RLock` 虽然能解决死锁，但会**掩盖设计缺陷**——"持锁调用持锁方法"本身就是错误模式。改用 `RLock` 等于治标不治本，后续维护者不会意识到这个陷阱。

---

## 缺陷 2：DiskCache 持锁 I/O 性能骤降

### 根因

`DiskCache.set()` 在持有 `RLock` 时执行磁盘 I/O（目录遍历 + JSON 写入），多线程并发时持锁时间过长，导致性能退化。

```python
# ❌ 持锁做 I/O
def set(self, key, value, ttl_seconds=3600):
    with self._lock:
        if self._get_total_size() > self.max_size_bytes:
            self._evict_oldest()                # 遍历目录 + 读取所有 JSON
        with open(file_path, 'w') as f:
            json.dump(data, f)                   # 磁盘 I/O
```

### 修复

锁内只做必须互斥的目录检查，文件写入移到锁外（每个 key 哈希到唯一文件，无冲突）：

```python
# ✅ I/O 移到锁外
def set(self, key, value, ttl_seconds=3600):
    file_path = self._get_file_path(key)

    with self._lock:
        if self._get_total_size() > self.max_size_bytes:
            self._evict_oldest()                # 锁内：互斥的目录操作

    with open(file_path, 'w') as f:              # 锁外：文件写入
        json.dump(data, f)
```

### 性能提升

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| 单次 set（无淘汰） | 1-5ms 持锁 | ~0.01ms 持锁 |
| 5 线程 50 次 set | 串行 ~15s | 并行 ~3s |

---

## 缺陷 3：run_sandbox threading 线程泄漏

### 根因

旧版 `run_sandbox` 使用 `threading.Thread` 执行用户代码。超时后 `thread.join(timeout)` 返回，但 **Python 没有提供终止线程的 API**，线程会继续运行直到进程退出。pytest 不退出进程，所以：

1. `test_run_sandbox_timeout` 启动的 `while True: pass` 线程永久运行
2. 该线程持续占用 GIL，导致后续并发测试受 GIL 竞争影响
3. 全量测试中出现 17 个 skills_mgmt `TypeError` 误报（`traced_action` 捕获了被 GIL 竞争扭曲的异常）

### 修复

迁移到 `multiprocessing.Process` + `spawn` context：

- 超时后 `process.terminate()` 强制终止子进程，立即释放 CPU
- 子进程的 `sys.stdout` 修改不影响主进程（进程级隔离）
- `spawn` 方式避免 `fork` 继承父进程锁状态

### 验证结果

修复后 729 个受影响测试全部通过（26.35s），0 失败，0 误报。

---

## 并发测试检查清单

### 编写并发测试时

- [ ] **设置 join 超时**：`thread.join(timeout=10)`，防止死锁导致测试永久挂起
- [ ] **验证线程退出**：`assert all(not t.is_alive() for t in threads)`，检测线程泄漏
- [ ] **设置 pytest-timeout**：`@pytest.mark.timeout(30)` 或 `--timeout=30`，兜底防护
- [ ] **验证数据正确性**：并发操作后检查数据状态，不仅是"没崩溃"
- [ ] **考虑 CI 环境差异**：CI 机器可能更慢，超时阈值留足余量

### 审查并发代码时

- [ ] **锁类型是否正确？**
  - `threading.Lock`：不可重入，同一线程不能两次 acquire
  - `threading.RLock`：可重入，但有额外开销，且会掩盖嵌套设计缺陷
- [ ] **持锁时是否调用了其他持锁方法？**
  - 如果是 → 改为锁内复制快照，锁外调用
- [ ] **持锁时是否做了 I/O？**
  - 磁盘读写、网络请求、数据库操作都不应在持锁时进行
  - 改为锁内只做内存操作，I/O 移到锁外
- [ ] **持锁时是否调用了外部回调？**
  - 回调可能获取其他锁，导致跨锁死锁
- [ ] **多锁场景下获取顺序是否一致？**
  - 所有代码路径必须按相同顺序获取多把锁，避免循环等待
- [ ] **是否用 `with` 语句？**
  - 禁止手动 `acquire/release`，`with` 语句保证异常时锁释放
- [ ] **锁保护的数据范围是否正确？**
  - 不能多保护（降低并发度），也不能少保护（数据竞争）

### 排查并发问题时

按以下顺序诊断：

1. **看堆栈位置**：卡在 `acquire` → 死锁；卡在 I/O → 性能问题；卡在 `exec` → GIL 竞争
2. **看锁类型**：`Lock` → 检查嵌套；`RLock` → 检查持锁时长
3. **看是否有后台线程**：`pytest --timeout` 报 Timeout 时检查是否有 `Thread-N` 未退出
4. **设计修复方案**：
   - 死锁 → 移到锁外 / 缩小临界区
   - 性能 → I/O 移到锁外 / 降低锁粒度
   - GIL 竞争 → 用 `multiprocessing` 替代 `threading`

---

## Python 锁使用四条铁律

### 铁律 1：默认用 Lock，明确需要重入时才用 RLock

```python
# ✅ 推荐
self._lock = threading.Lock()

# ⚠️ 仅当确实需要同一线程重入时
self._lock = threading.RLock()

# ❌ 避免：无脑用 RLock 掩盖设计问题
```

### 铁律 2：持锁时不调用其他持锁方法

```python
# ❌ 危险
def get_all(self):
    with self._lock:
        return {name: self.get_stats(name) for name in self._names}

# ✅ 正确
def get_all(self):
    with self._lock:
        names = list(self._names)
    return {name: self.get_stats(name) for name in names}
```

### 铁律 3：持锁时不做 I/O

```python
# ❌ 危险
def set(self, key, value):
    with self._lock:
        json.dump(value, open(self._path(key), 'w'))

# ✅ 正确
def set(self, key, value):
    with self._lock:
        path = self._path(key)
    json.dump(value, open(path, 'w'))
```

### 铁律 4：线程无法强制终止，需要超时强杀时用进程

```python
# ❌ 线程超时后仍在运行
thread = threading.Thread(target=func, daemon=True)
thread.start()
thread.join(timeout=5)  # 超时后线程继续运行

# ✅ 进程可以被 terminate
process = multiprocessing.Process(target=func, daemon=True)
process.start()
process.join(timeout=5)
if process.is_alive():
    process.terminate()  # 强制终止
```

---

## 并发测试模板

```python
import threading
import pytest

class TestConcurrentCache:
    """并发测试模板：正确性 + 死锁检测 + 性能验证"""

    @pytest.mark.timeout(30)  # 兜底超时
    def test_concurrent_set_get(self, tmp_path):
        cache = MultiLevelCache(
            l1_max_size=100, l2_enabled=True,
            l2_dir=str(tmp_path / "cache")
        )

        def worker():
            for i in range(50):
                cache.set(f"key_{i}", f"value_{i}")
                cache.get(f"key_{i}")

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)  # join 超时防死锁

        # 验证线程全部退出（检测死锁/泄漏）
        assert all(not t.is_alive() for t in threads), "线程未退出，疑似死锁"

        # 验证数据正确性
        stats = cache.get_stats()
        assert stats["total_hits"] > 0
        assert stats["total_puts"] > 0
```

---

## 相关文档

- [死锁修复技术文档（完整版）](file:///c:/Users/Administrator/agent/docs/fixes/deadlock_fix_technical_review.md)
- [run_sandbox multiprocessing 重构方案](file:///c:/Users/Administrator/agent/docs/fixes/run_sandbox_multiprocessing_refactor.md)
- [全量测试失败用例分类修复优先级清单](file:///c:/Users/Administrator/agent/docs/fixes/test_failures_priority_list.md)
- [workflow_result 签名修复方案](file:///c:/Users/Administrator/agent/docs/fixes/workflow_result_signature_fix_plan.md)

## 修改文件清单

| 文件 | 修改内容 | 提交 |
|------|---------|------|
| `agent/monitoring/metrics.py` | `get_all_metrics()` 锁内复制快照，锁外调用 `get_stats()` | `3cd54031` |
| `agent/caching/multi_level_cache.py` | `DiskCache.set()` 磁盘 I/O 移到锁外 | `3cd54031` |
| `agent/system_tools.py` | `run_sandbox` 从 threading 迁移到 multiprocessing | `0714d792` |
