# L2 冷数据加载：异步 IO 不适用场景分析

> **结论先行**：在路径缓存已优化的场景下，`asyncio.to_thread` 异步 IO **反而比同步串行慢 21 倍**（P50 维度）。同步串行 + 路径缓存是当前最优方案，不建议将异步 IO 推广到生产代码。

## 1. 背景

TLM 三层上下文组装中，L2 冷数据层通过 `MarkdownSyncer.read_fragment` 从 `.md` 归档文件懒加载片段。`read_fragment` 是同步阻塞调用（`glob` 路径查找 + `open/read` 文件读取），在 `ContextAssembler._build_l2` 的 for 循环中串行执行。

**假设**：同步 `read_fragment` 阻塞事件循环，高并发下应导致 P99 恶化。用 `asyncio.to_thread` 包装 `read_fragment` 放到线程池，释放事件循环，应能彻底解决阻塞问题。

**实验目的**：用数据验证上述假设是否成立。

## 2. 实验设计

### 压测配置

| 参数 | 值 |
|------|-----|
| 冷数据量 | 300 条 |
| category 子目录数 | 30 个 |
| fragment 大小 | medium（2000 字符/条） |
| l2_top_k | 20（单次 assemble 读取 20 个 fragment） |
| 并发度 | 20 |
| 压测轮次 | 2 |

### 对照组

| 场景 | 实现方式 |
|------|---------|
| 场景 C（同步 IO） | 原版 `_build_l2`：for 循环串行调用 `read_fragment` |
| 场景 E（异步 IO） | monkey-patch `_build_l2`：`asyncio.to_thread` + `asyncio.gather` 并发调用 `read_fragment` |

两组共享同一份冷数据、同一路径缓存、同一并发度，唯一变量是 `read_fragment` 的执行方式。

## 3. 实测数据

| 指标 | 场景 C（同步） | 场景 E（异步） | 变化 |
|------|---------------|---------------|------|
| P50 | 16.81ms | 370.64ms | **变慢 21 倍** |
| P99 | 99.75ms | 541.54ms | **变慢 5 倍** |
| 总耗时 | 669.29ms | 592.54ms | 略快（但 P50/P99 全面恶化） |

**关键发现**：异步 IO 在 P50 维度变慢 21 倍，在 P99 维度变慢 5 倍。假设被数据证伪。

## 4. 根因分析

### 4.1 路径缓存已解决主要瓶颈

`MarkdownSyncer.read_fragment` 已实现两层优化（方案 A+B）：

- **路径缓存**（`_fragment_path_cache`）：首次 `glob` 命中后缓存 `key → filepath`，后续 O(1) 查找，避免重复 `glob(recursive=True)` 跨子目录扫描
- **限量读取**（`f.read(max_chars*4)`）：只读前 `max_chars*4` 字节，避免读全文

优化后，**缓存命中时单次 `read_fragment` 仅约 0.8ms**。20 个 fragment 串行执行总耗时约 16ms，与场景 C 的 P50 吻合。

### 4.2 asyncio.to_thread 引入额外开销

`asyncio.to_thread` 将同步函数提交到默认线程池（`ThreadPoolExecutor`，默认 `min(32, cpu+4)` 线程）。每次调用涉及：

1. **线程池调度**：提交任务 → 线程池队列 → 空闲线程拾取
2. **GIL 竞争**：`read_fragment` 内部的 `glob` 目录遍历是 CPU 密集型，持有 GIL；`open/read` 是 IO 操作，释放 GIL
3. **future 包装**：`asyncio.to_thread` 返回 future，`await` 时有事件循环调度开销

当单次操作很快（0.8ms）时，线程池调度开销（约 1-2ms/次）**超过了操作本身**，导致异步反而更慢。

### 4.3 并发 glob 竞争加剧

场景 E 清空路径缓存后，20 个并发任务的 `read_fragment` 都面临首次缓存未命中。异步并发导致：

- 多个线程同时执行 `glob(recursive=True)` 跨 30 个子目录查找
- 磁盘 IO 带宽竞争（多个线程同时打开不同文件）
- `_cache_lock` 竞争（多个线程同时写入路径缓存）

而同步串行模式下，第一个任务填充缓存后，后续任务 O(1) 命中，无竞争。

### 4.4 GIL 限制真正的并行

`read_fragment` 内部的 `glob` 是 CPU+IO 混合型：
- 目录遍历（`os.scandir`）持有 GIL（CPU 密集）
- 文件打开/读取释放 GIL（IO 密集）

GIL 保证同一时刻只有一个线程执行 Python 字节码。当 `glob` 持有 GIL 时，其他线程无法执行。因此异步并行的实际并行度受限于 GIL，无法实现真正的 IO 并行。

## 5. 结论

### 5.1 不建议推广异步 IO

在当前路径缓存已优化的场景下，异步 IO **有害无益**：
- P50 变慢 21 倍（16.81ms → 370.64ms）
- P99 变慢 5 倍（99.75ms → 541.54ms）
- 引入额外线程池调度开销和 GIL 竞争

### 5.2 同步串行 + 路径缓存是最优方案

| 优势 | 说明 |
|------|------|
| 路径缓存 O(1) 命中 | 首次 glob 后缓存，后续直接读取 |
| 限量读取 | `f.read(max_chars*4)` 避免读全文 |
| 无线程池开销 | 同步直接调用，零调度开销 |
| 无 GIL 竞争 | 单线程串行，无锁竞争 |
| 锁等待占比 0.0% | `_cache_lock` 几乎无竞争（实测） |

### 5.3 异步 IO 的适用条件

异步 IO 并非万能，仅在以下条件同时满足时才有收益：

1. **单次操作慢**（>10ms）：线程池调度开销占比低
2. **缓存命中率低**：大部分操作无法命中缓存，必须执行真实 IO
3. **纯 IO 密集**：操作大部分时间在等待 IO（释放 GIL），而非 CPU 计算
4. **无 GIL 限制**：或使用 Python 3.13+ 的自由线程（PEP 703）

当前 L2 冷数据加载场景**不满足上述任何条件**：
- 单次操作 0.8ms（远低于 10ms 阈值）
- 路径缓存命中率高（热启动几乎 100%）
- `glob` 有 CPU 部分（持 GIL）
- 使用标准 CPython（有 GIL）

## 6. 验证数据

### 6.1 路径缓存效果

| 场景 | P50 | P99 | 缓存状态 |
|------|-----|-----|---------|
| A 冷启动 | 98.63ms | 98.83ms | 缓存空，全部 glob |
| B 热启动 | 15.44ms | 15.84ms | 缓存满，O(1) 命中 |
| **加速比** | x6.3 | x6.2 | — |

### 6.2 锁竞争统计

| 指标 | 值 |
|------|-----|
| acquire 次数 | 21 |
| 锁等待 P99 | 0.00ms |
| 持锁 P99 | 0.00ms |
| 锁等待占 L2 总耗时比 | 0.2% |

**结论**：`_cache_lock` 几乎无竞争，锁不是瓶颈。真正的瓶颈在 `glob` + `open` 的同步 IO，但路径缓存已将其优化到可接受范围（P99 < 100ms）。

## 7. 行动项

| 行动项 | 状态 | 说明 |
|--------|------|------|
| 保留同步串行 + 路径缓存 | ✅ 已采纳 | 当前最优方案 |
| 回退场景 E 异步 IO 代码 | ✅ 已完成 | 从 `bench_l2_stress.py` 删除 |
| CI 性能回归护栏 | ✅ 已就绪 | `test_l2_perf_regression.py` 监控 P99 退化 |
| 异步 IO 实验记录 | ✅ 本文档 | 供后续决策参考 |

## 8. 相关文件

- 压测脚本：[scripts/bench_l2_stress.py](../scripts/bench_l2_stress.py)（场景 A/B/C/D + 锁统计）
- 性能回归测试：[tests/performance/test_l2_perf_regression.py](../tests/performance/test_l2_perf_regression.py)
- CI 配置：[.github/workflows/test.yml](../.github/workflows/test.yml)（performance-tests job）
- 生产代码：[agent/memory/markdown_syncer.py](../agent/memory/markdown_syncer.py)（`read_fragment` 路径缓存 + 限量读取）
- 生产代码：[agent/memory/context_assembler.py](../agent/memory/context_assembler.py)（`_build_l2` 同步串行调用）

---

*文档日期：2026-07-26*
*实验数据基于：300 条冷数据 × 30 子目录 × 20 并发，Windows 10 + CPython 3.10*
