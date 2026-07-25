# L2 冷数据加载性能测试

> 同步串行 + 路径缓存是当前最优方案。异步 IO（`asyncio.to_thread`）实测 P50 变慢 21 倍，不建议使用。

## 最佳实践

| 实践 | 说明 |
|------|------|
| **同步串行 read_fragment** | `_build_l2` 中 for 循环串行调用，无线程池开销 |
| **路径缓存（O(1) 命中）** | `_fragment_path_cache` 缓存 key→filepath，避免重复 glob |
| **限量读取** | `f.read(max_chars*4)` 只读前 N 字节，避免读全文 |
| **锁等待监控** | `_cache_lock` 实测等待占比 0.0%，非瓶颈 |

## 性能护栏阈值（CI 回归测试）

| 测试 | 阈值 | 文件 |
|------|------|------|
| 冷启动 P99 | < 2s | `test_l2_cold_start_p99_under_threshold` |
| 热启动 P99 | < 1s | `test_l2_warm_start_p99_under_threshold` |
| 并发(10) P99 | < 2s | `test_l2_concurrent_p99_under_threshold` |
| 缓存有效性 | 热启动 ≤ 2×冷启动 | `test_l2_cache_effectiveness` |

运行 CI 性能回归测试：

```bash
pytest tests/performance/test_l2_perf_regression.py -m performance -v --timeout=120
```

## 极限压测

```bash
# 默认规模（200 条 × 20 子目录 × 10 并发）
python scripts/bench_l2_stress.py

# 千条极限压测（验证 P99 是否逼近 1s 阈值）
python scripts/bench_l2_stress.py --cold-count 1000 --category-count 50 --concurrency 20

# 大 fragment 验证限量读取效果
python scripts/bench_l2_stress.py --fragment-size large
```

## 不建议：异步 IO

异步 IO（`asyncio.to_thread` 包装 `read_fragment`）在路径缓存已优化的场景下**反而更慢**：

| 指标 | 同步串行 | 异步 IO | 变化 |
|------|---------|---------|------|
| P50 | 16.81ms | 370.64ms | 变慢 21 倍 |
| P99 | 99.75ms | 541.54ms | 变慢 5 倍 |

根因：路径缓存命中后单次 `read_fragment` 仅 0.8ms，线程池调度开销（1-2ms/次）超过操作本身。详见 [异步 IO 不适用场景分析](../../docs/perf-async-io-analysis.md)。

## 相关文件

| 文件 | 说明 |
|------|------|
| [test_l2_perf_regression.py](./test_l2_perf_regression.py) | CI 性能回归测试（4 个护栏） |
| [bench_l2_stress.py](../../scripts/bench_l2_stress.py) | 极限压测脚本（场景 A/B/C/D + 锁统计） |
| [perf-async-io-analysis.md](../../docs/perf-async-io-analysis.md) | 异步 IO 不适用场景分析文档 |
| [context_assembler.py](../../agent/memory/context_assembler.py) | `_build_l2` 同步串行调用 |
| [markdown_syncer.py](../../agent/memory/markdown_syncer.py) | `read_fragment` 路径缓存 + 限量读取 |

---

*维护日期：2026-07-26*
