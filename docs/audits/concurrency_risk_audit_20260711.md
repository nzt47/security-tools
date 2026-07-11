# 并发风险审计报告

> 审计日期：2026-07-11
> 审计范围：`agent/` 目录下所有使用 `threading.Lock` / `threading.RLock` 的文件
> 审计依据：[并发代码审查检查清单](file:///c:/Users/Administrator/agent/docs/templates/concurrency_code_review_checklist.md)
> 审计人：自动化扫描 + 人工复核

---

## 审计概述

对项目中 **100 个锁使用点**（跨 60+ 文件）进行了系统性扫描，重点检查三类风险：

| 风险类型 | 检查方法 | 发现数 |
|---------|---------|--------|
| 锁嵌套（持锁调用持锁方法） | multiline regex + 人工复核 | 3 处 |
| 持锁 I/O（锁内文件/网络操作） | multiline regex + 人工复核 | 5 处 |
| 多锁循环等待 | 人工审查多锁文件 | 0 处 |

---

## 风险清单

### 高风险：锁嵌套 + 持锁 I/O 组合

#### 风险 1：`disaster_recovery.py` — `auto_recover_on_startup()`

| 属性 | 值 |
|------|-----|
| 文件 | [disaster_recovery.py](file:///c:/Users/Administrator/agent/agent/disaster_recovery.py#L858-L868) |
| 行号 | 858-868 |
| 锁类型 | `RLock` |
| 风险等级 | **高** |

**问题**：`auto_recover_on_startup()` 持有 `self._lock` 后，在锁内调用 `self.get_backup_list()` 和 `self.restore_from_backup()`。`get_backup_list()` 也获取 `self._lock`（RLock 可重入，不会死锁），但它在锁内做目录遍历 + 多文件 JSON 读取。`restore_from_backup()` 同样在锁内做文件 I/O。

```python
# ❌ 当前代码（第 858-868 行）
def auto_recover_on_startup(self) -> bool:
    with self._lock:
        backups = self.get_backup_list()          # 锁嵌套 + 持锁 I/O
        if not backups:
            return False
        latest = backups[0]
        return self.restore_from_backup(latest.backup_id)  # 锁嵌套 + 持锁 I/O
```

**影响**：恢复操作期间锁被长时间持有，阻塞所有其他需要锁的操作。备份文件多时可能持锁数秒。

**修复建议**：锁内只做状态检查，I/O 和恢复操作移到锁外：

```python
# ✅ 修复方案
def auto_recover_on_startup(self) -> bool:
    with self._lock:
        enabled = self._config.enabled
    if not enabled:
        return False

    backups = self.get_backup_list()              # 锁外调用（get_backup_list 自己加锁）
    if not backups:
        return False

    latest = backups[0]
    return self.restore_from_backup(latest.backup_id)  # 锁外调用
```

---

#### 风险 2：`disaster_recovery.py` — `get_status()`

| 属性 | 值 |
|------|-----|
| 文件 | [disaster_recovery.py](file:///c:/Users/Administrator/agent/agent/disaster_recovery.py#L990-L991) |
| 行号 | 990-1004 |
| 锁类型 | `RLock` |
| 风险等级 | **高** |

**问题**：`get_status()` 持有 `self._lock` 后调用 `self.get_backup_list()`，导致持锁做目录遍历 + 多文件读取。

```python
# ❌ 当前代码（第 990-991 行）
def get_status(self) -> dict:
    with self._lock:
        backups = self.get_backup_list()          # 锁嵌套 + 持锁 I/O
        latest = backups[0] if backups else None
        return { ... }
```

**修复建议**：`get_backup_list()` 自己会加锁，不需要在外层加锁：

```python
# ✅ 修复方案
def get_status(self) -> dict:
    backups = self.get_backup_list()              # 锁外调用
    latest = backups[0] if backups else None

    with self._lock:                              # 锁内只取配置快照
        config_snapshot = {
            "enabled": self._config.enabled,
            "backup_dir": self._config.backup_dir,
            ...
        }
        providers = list(self._backup_providers.keys())

    return { **config_snapshot, "backup_providers": providers, ... }
```

---

### 中风险：持锁 I/O

#### 风险 3：`multi_level_cache.py` — `DiskCache.get()`

| 属性 | 值 |
|------|-----|
| 文件 | [multi_level_cache.py](file:///c:/Users/Administrator/agent/agent/caching/multi_level_cache.py#L334-L353) |
| 行号 | 334-353 |
| 锁类型 | `RLock` |
| 风险等级 | **中** |

**问题**：`DiskCache.get()` 持有 `self._lock` 时做文件存在检查、文件读取、文件删除。我们之前修复了 `set()` 的持锁 I/O，但 `get()` 遗漏了。

```python
# ❌ 当前代码（第 338-353 行）
def get(self, key: str) -> Optional[Any]:
    file_path = self._get_file_path(key)
    with self._lock:
        if not file_path.exists():                # 持锁 I/O
            return None
        try:
            with open(file_path, 'r') as f:       # 持锁 I/O
                data = json.load(f)               # 持锁 I/O
            if self._is_expired(...):
                file_path.unlink()                # 持锁 I/O
                return None
            return data['value']
```

**修复建议**：文件读取移到锁外。每个 key 哈希到唯一文件，无并发冲突：

```python
# ✅ 修复方案
def get(self, key: str) -> Optional[Any]:
    file_path = self._get_file_path(key)
    if not file_path.exists():                    # 锁外检查
        return None
    try:
        with open(file_path, 'r') as f:           # 锁外读取
            data = json.load(f)
        if self._is_expired(data['timestamp'], data['ttl_seconds']):
            file_path.unlink()                    # 锁外删除
            return None
        return data['value']
    except Exception as e:
        logger.error(...)
        return None
```

**注意**：移除 `with self._lock` 后，`DiskCache.get()` 不再需要锁——文件路径由 key 哈希唯一确定，无共享状态需要保护。

---

#### 风险 4：`disaster_recovery.py` — `get_backup_list()`

| 属性 | 值 |
|------|-----|
| 文件 | [disaster_recovery.py](file:///c:/Users/Administrator/agent/agent/disaster_recovery.py#L703-L740) |
| 行号 | 703-740 |
| 锁类型 | `RLock` |
| 风险等级 | **中** |

**问题**：`get_backup_list()` 持有 `self._lock` 时遍历目录 + 读取所有 `_meta.json` 文件。备份文件多时持锁时间可达数秒。

**修复建议**：目录遍历和文件读取移到锁外，锁内只保护内存状态（如果有的话）。`get_backup_list()` 实际只读取磁盘数据，不访问共享内存状态，可以完全移除锁。

---

#### 风险 5：`disaster_recovery.py` — `restore_from_backup()`

| 属性 | 值 |
|------|-----|
| 文件 | [disaster_recovery.py](file:///c:/Users/Administrator/agent/agent/disaster_recovery.py#L678-L700) |
| 行号 | 678-700 |
| 锁类型 | `RLock` |
| 风险等级 | **中** |

**问题**：`restore_from_backup()` 持有 `self._lock` 时读取 backup 文件。恢复操作可能很耗时（解压、数据库恢复等）。

**修复建议**：锁内只检查 backup 是否存在并记录恢复状态，实际恢复操作移到锁外。

---

### 低风险：RLock 锁嵌套（无 I/O）

#### 风险 6：`chaos_injector.py` — `clear_all()`

| 属性 | 值 |
|------|-----|
| 文件 | [chaos_injector.py](file:///c:/Users/Administrator/agent/agent/chaos_injector.py#L598-L611) |
| 行号 | 598-611 |
| 锁类型 | `RLock` |
| 风险等级 | **低** |

**问题**：`clear_all()` 持有 `self._lock` 后调用 `self.clear_fault()`（也获取 `self._lock`），并在锁内做线程 join（`self._memory_pressure_thread.join(timeout=...)`）。

```python
# ⚠️ 当前代码
def clear_all(self):
    with self._lock:
        for fault_type in FaultType:
            self.clear_fault(fault_type)          # RLock 重入，不会死锁
        self._memory_pressure_thread.join(timeout=...)  # 持锁等待线程退出
```

**影响**：RLock 可重入所以不会死锁，但 `thread.join(timeout)` 在锁内等待，延长持锁时间。

**修复建议**：`clear_fault` 调用可以保留（RLock 可重入），但线程 join 应移到锁外：

```python
# ✅ 修复方案
def clear_all(self):
    with self._lock:
        for fault_type in FaultType:
            self.clear_fault(fault_type)
        self._memory_pressure_stop_event.set()
        thread_to_join = self._memory_pressure_thread
        self._memory_pressure_thread = None
        self._memory_hold_list.clear()

    if thread_to_join and thread_to_join.is_alive():  # 锁外 join
        thread_to_join.join(timeout=self._thread_join_timeout)
    gc.collect()
```

---

## 修复优先级

| 优先级 | 风险编号 | 文件 | 风险类型 | 修复成本 | 建议时限 |
|--------|---------|------|---------|---------|---------|
| **P0** | 3 | multi_level_cache.py `DiskCache.get()` | 持锁 I/O | 低 | 立即 |
| **P1** | 1, 2 | disaster_recovery.py `auto_recover` + `get_status` | 锁嵌套 + I/O | 中 | 本周 |
| **P1** | 4, 5 | disaster_recovery.py `get_backup_list` + `restore` | 持锁 I/O | 中 | 本周 |
| **P2** | 6 | chaos_injector.py `clear_all()` | 锁内线程 join | 低 | 下周 |

---

## 无风险确认

以下文件经审查后确认无锁嵌套、持锁 I/O 或多锁循环等待风险：

| 文件 | 锁数量 | 审查结论 |
|------|--------|---------|
| circuit_breaker.py | 3 | 锁内只做状态变更，无嵌套 |
| rate_limiter.py | 4 | 锁内只做计数器操作，无 I/O |
| graceful_degrade.py | 2 | 锁内只做状态变更，无嵌套 |
| performance_optimization.py | 4 | 各锁职责独立，无嵌套 |
| replay_storage.py | 2 | 锁内只做内存操作，无 I/O |
| optimized_storage.py | 4 | 锁内只做队列/缓存操作，无嵌套 |
| error_handler.py | 2 | 锁内只做状态变更 |
| scheduling.py | 1 | I/O 在锁外（正确） |
| metrics.py | 1 | 已修复（commit 3cd54031） |
| multi_level_cache.py `set()` | 3 | 已修复（commit 3cd54031） |

---

## 审计方法

1. **锁使用点扫描**：`grep -rn "threading\.(Lock|RLock)()" agent/` → 100 个匹配
2. **锁嵌套检测**：multiline regex 匹配 `with self._lock:` 后 200 字符内调用 `self.(get|set|update|...)` 的模式
3. **持锁 I/O 检测**：multiline regex 匹配 `with self._lock:` 后 300 字符内出现 `open(|json.|pickle.|shutil.` 的模式
4. **人工复核**：对每个匹配点读取上下文，确认是否为真实风险

**局限性**：本次审计为静态分析，未进行运行时死锁检测。建议后续引入 `threading.get_ident()` 插桩或 `import-linter` 契约检查。
