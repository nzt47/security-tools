# 变更说明（简版）：L2 异步 IO 回退与同步方案确认

**日期**: 2026-07-26
**类型**: 性能决策（Performance Decision）
**状态**: 已完成
**详细文档**: [CHANGELOG_L2_ASYNC_IO_REVERT_20260726.md](../../CHANGELOG_L2_ASYNC_IO_REVERT_20260726.md)

## 一句话决策

回退 L2 冷数据加载的异步 IO（`asyncio.to_thread`）实验代码，**确认同步串行 + 路径缓存为最优方案**。

## 实测数据（300 条 × 30 子目录 × 20 并发）

| 指标 | 同步串行（场景 C） | 异步 IO（场景 E） | 变化 |
|------|-------------------|------------------|------|
| P50  | 16.81ms           | 370.64ms         | **变慢 21 倍** |
| P99  | 99.75ms           | 541.54ms         | **变慢 5 倍**  |

## 根因（3 条）

1. **路径缓存已消除主要瓶颈**：热启动 `key→filepath` 缓存 O(1) 命中，单次 `read_fragment` 仅 0.8ms，无需异步化
2. **线程池调度开销反超操作本身**：`asyncio.to_thread` 调度开销 1-2ms/次，超过 0.8ms 的实际 IO
3. **GIL 限制 + 并发 glob 竞争**：`glob` 目录遍历持 GIL 无法真正并行，多线程同时 glob 反而加剧磁盘竞争

## 决策依据

- 假设「同步阻塞事件循环 → 异步 IO 应更优」被实测数据**证伪**
- `_cache_lock` 等待占比 0.0%，排除锁竞争假设
- 异步 IO 适用条件（单次操作 >10ms / 缓存命中率低 / 纯 IO / 无 GIL）当前**均不满足**

## 影响文件

| 文件 | 变更 |
|------|------|
| `scripts/bench_l2_stress.py` | 回退场景 E 异步 IO 代码（-126 行） |
| `.github/workflows/test.yml` | L2 step 新增 `L2_SCHEME=sync-serial-path-cache` 标记 + 解析图表 step + artifact 上传 |
| `scripts/parse_ci_l2_report.py` | 新增 CI 日志解析与可视化脚本 |
| `CHANGELOG_L2_ASYNC_IO_REVERT_20260726.md` | 详细决策记录 |
| `scripts/simulate_l2_async_switch.py` | 新增 dry-run 切换模拟脚本，支持 `--bench-log` 性能对比日志与 `--check` 一致性校验 |
| `scripts/l2_async_experiment_branch.ps1` | 新增临时分支 git 操作指令（6 动作：create/verify/status/merge/abort/cleanup） |
| `docs/changelogs/l2-async-switch-checklist.md` | 新增异步方案切换操作检查清单（7 Phase + 回滚预案 + 9 项验收标准） |

## CI 标记

CI 日志中通过 `SCHEME=sync-serial-path-cache` 环境变量明确标识同步串行方案，便于机器解析与方案区分。

## 经验教训

> **不要盲目异步化**：异步 IO 不是万能药。在缓存命中率高、单次操作快的场景下，线程池调度开销会反超收益。用对照实验数据驱动决策，而非凭直觉。

---

*本文档为简版变更说明，供团队快速同步决策结论。完整决策过程见详细 CHANGELOG。*
