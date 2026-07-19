# TLM 熔断机制与 thread-local 缓存架构说明

> 本文档简要说明 HolographicAdapter 的两项优化：thread-local 连接缓存、熔断机制（含 busy_timeout）。
> 详细 PR 描述见 [PR_TLM_REFACTOR.md](./PR_TLM_REFACTOR.md) §9。

## 1. 概述

针对 sqlite-vec 在生产环境运行时可能出现的两类问题：

| 问题 | 优化 | 代码位置 |
|------|------|----------|
| 每次操作重复 `connect` + `load_extension` 开销大 | thread-local 连接缓存 | `_get_conn()` |
| 多线程并发时 `SQLITE_BUSY` 直接抛异常 | `PRAGMA busy_timeout=5000` | `_get_conn()` |
| sqlite-vec 运行时持续不可用，每次操作都触发无意义重试 | 熔断器（阈值 5 连续失败自动降级） | `_record_vec_failure()` / `_reset_vec_circuit()` |

## 2. 配置参数

### 2.1 熔断器参数

| 参数 | 默认值 | 作用 | 可配置 |
|------|--------|------|--------|
| `_vec_fail_threshold` | `5` | 连续失败阈值，达此值自动熔断 | 是，`adapter._vec_fail_threshold = N` |
| `_vec_fail_count` | `0` | 当前连续失败计数（运行时状态） | 否，由 `_record_vec_failure` 递增 |
| `_vec_available` | `False`（初始化）/ `True`（加载成功后） | 向量层可用标志，`False` 时所有向量操作降级 | 通过 `_reset_vec_circuit` 恢复为 `True` |

### 2.2 thread-local 缓存参数

| 参数 | 作用 |
|------|------|
| `_conn_local` | `threading.local()` 实例，按线程隔离连接状态 |
| `_conn_local.conn` | 当前线程的 SQLite Connection（懒加载，首次访问时创建） |
| `_conn_local.vec_loaded` | 当前线程连接是否已加载 sqlite-vec 扩展（避免重复 `load_extension`） |

### 2.3 busy_timeout

| 参数 | 默认值 | 作用 |
|------|--------|------|
| `PRAGMA busy_timeout` | `5000`（ms） | SQLite 内部排队等待锁 5s，超时才抛 `SQLITE_BUSY`，避免短时锁竞争直接失败 |

## 3. 架构流程

### 3.1 thread-local 连接获取流程

```
_get_conn()
    │
    ├─ 检查 _conn_local.conn 是否存在？
    │   ├─ 否 → sqlite3.connect() + PRAGMA busy_timeout=5000 → 缓存到 _conn_local.conn
    │   └─ 是 → 复用缓存连接
    │
    ├─ _vec_available=True 且 vec_loaded=False？
    │   ├─ 是 → sqlite_vec.load(conn) → 标记 vec_loaded=True
    │   │       └─ 失败 → logger.debug，调用方 try-except 兜底降级
    │   └─ 否 → 跳过扩展加载
    │
    └─ 返回 conn
```

**Why（缓存复用安全）**：
- SQLite `Connection` 的 `with` 语句仅触发 `commit/rollback`，**不会 `close`**，缓存复用安全
- `check_same_thread=False` 允许跨线程使用（已有 `self._lock` 保护写入操作）
- 扩展加载状态用 thread-local `vec_loaded` 标志，每个线程的连接只加载一次

### 3.2 熔断器状态机

```
            ┌────────────────────────────────────┐
            │   正常状态: _vec_available=True    │
            │   _vec_fail_count=0                │
            └────────────────┬───────────────────┘
                             │
                   search_vector / _retry_vec_write 失败
                             │
                             ▼
                     _record_vec_failure()
                             │
                     _vec_fail_count += 1
                             │
                     _vec_fail_count >= threshold(5)?
                     ├─ 否 → 返回（等待下次失败）
                     └─ 是 → _vec_available=False（熔断）
                             │
                             ▼
            ┌────────────────────────────────────┐
            │   熔断状态: _vec_available=False   │
            │   - search_vector 直接返回 []      │
            │   - save_with_embedding 跳过向量层 │
            │   - 不再调用 _get_conn（避免重试） │
            └────────────────┬───────────────────┘
                             │
                   后台探活成功 → _reset_vec_circuit()
                             │
                             ▼
            ┌────────────────────────────────────┐
            │   恢复状态: _vec_available=True    │
            │   _vec_fail_count=0                │
            │   （回到正常状态，可再次熔断）     │
            └────────────────────────────────────┘
```

### 3.3 三者协同关系

三项优化**正交**，互不依赖，各司其职：

| 优化 | 解决的问题 | 触发位置 |
|------|-----------|----------|
| thread-local 缓存 | 性能（避免重复 `load_extension`） | `_get_conn()` 内部 |
| busy_timeout | 并发锁竞争（`SQLITE_BUSY` 排队） | `_get_conn()` 内部 |
| 熔断机制 | 持续故障（避免无意义重试） | `search_vector` / `_retry_vec_write` 的 except 路径 |

**协同流程**：
1. 正常时：`_get_conn` 用 thread-local 缓存返回已加载扩展的连接，`busy_timeout` 处理短时锁竞争
2. 故障时：`search_vector` 失败 → `_record_vec_failure` 计数 → 达阈值熔断
3. 熔断后：`search_vector` 直接返回 `[]`，**不再调用 `_get_conn`**（熔断器短路）
4. 恢复时：`_reset_vec_circuit` 重置状态，`_get_conn` 恢复正常工作

## 4. 关键代码位置

| 功能 | 文件 | 方法 |
|------|------|------|
| thread-local 缓存 | `agent/memory/adapters/holographic_adapter.py` | `_get_conn()` |
| busy_timeout | 同上 | `_get_conn()` 中 `conn.execute("PRAGMA busy_timeout=5000")` |
| 熔断计数 | 同上 | `_record_vec_failure()` |
| 熔断恢复 | 同上 | `_reset_vec_circuit()` |
| 熔断触发点（读路径） | 同上 | `search_vector` except 块 |
| 熔断触发点（写路径） | 同上 | `_retry_vec_write` 重试耗尽处 |

## 5. 测试覆盖

| 测试类 | 用例数 | 覆盖点 |
|--------|--------|--------|
| `TestCircuitBreaker` | 5 | 阈值触发 / 未触发 / 重置 / 阈值可配 / 搜索触发 |
| `TestCircuitBreakerRecovery` | 5 | 频繁超时 / 熔断后跳过 `_get_conn` / 恢复检索 / 再熔断 / 降级写入 |

**测试结果**：27/27 PASSED in 4.91s（含原 17 + 熔断 5 + 恢复 5）

## 6. 不变量约束

- 【不易】熔断只置位 `_vec_available=False`，**不删除已有向量数据**
- 【不易】`_reset_vec_circuit` 重置后状态完全恢复（`fail_count=0`，可再次熔断）
- 【不易】thread-local 缓存的 `with conn` 仅 `commit/rollback` 不 `close`，复用安全
- 【不易】主表 + FTS 仍同事务；`save`/`search` 接口签名未变
- 【不易】持锁操作不含 I/O 回调（thread-local 缓存为内存状态读取）
- 【变易】熔断阈值可配置，`_reset_vec_circuit` 供后台探活调用
- 【简易】熔断器状态机简单（三态：正常→熔断→恢复），无复杂状态转换
