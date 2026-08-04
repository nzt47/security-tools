# Git Diff 摘要：L2 性能优化决策（代码评审用）

**评审范围**: 5 个 commit（`4f7bbbeb` → `cd20ef04`）
**主题**: L2 冷数据加载性能优化——回退异步 IO，确认同步串行最优
**生成日期**: 2026-07-26

---

## Commit 链

| Commit | 类型 | 标题 | 文件数 | 行变更 |
|--------|------|------|--------|--------|
| `4f7bbbeb` | test | 添加 L2 冷数据加载性能回归测试护栏 | 2 | +255 |
| `2ab9c84d` | perf | 添加 L2 极限压测脚本与超时断言护栏 | 2 | +585 -1 |
| `2ff228eb` | revert | 回退场景 E 异步 IO 代码并补充分析文档 | 2 | +159 -126 |
| `497cc0ff` | docs | 添加 L2 加载性能测试最佳实践 README | 1 | +65 |
| `cd20ef04` | docs | 同步最佳实践到根 README 并记录异步 IO 回退决策 | 3 | +146 |
| **合计** | — | — | **8 个独立文件** | **+1210 -127** |

## 文件变更矩阵

| 文件 | 4f7bbbeb | 2ab9c84d | 2ff228eb | 497cc0ff | cd20ef04 | 合计 |
|------|----------|----------|----------|----------|----------|------|
| `tests/performance/test_l2_perf_regression.py` | +247 | — | — | — | — | **+247** |
| `scripts/bench_l2_stress.py` | — | +578 | -126 | — | — | **+452** |
| `scripts/verify_tlm_three_layers.py` | — | +7 -1 | — | — | — | **+7 -1** |
| `docs/perf-async-io-analysis.md` | — | — | +159 | — | — | **+159** |
| `tests/performance/README.md` | — | — | — | +65 | — | **+65** |
| `CHANGELOG_L2_ASYNC_IO_REVERT_20260726.md` | — | — | — | — | +119 | **+119** |
| `.github/workflows/test.yml` | +8 | — | — | — | +3 | **+11** |
| `README.md` | — | — | — | — | +24 | **+24** |

## 变更分类

### 1. 测试代码（+247 行）

| 文件 | 说明 |
|------|------|
| [test_l2_perf_regression.py](../../tests/performance/test_l2_perf_regression.py) | 4 个 CI 性能护栏测试（冷启动/热启动/并发/缓存有效性） |

### 2. 压测脚本（+452 行净增）

| 文件 | 说明 |
|------|------|
| [bench_l2_stress.py](../../scripts/bench_l2_stress.py) | 极限压测脚本：4 场景（A/B/C/D）+ LockStatsWrapper 锁统计。原 578 行，回退场景 E 后 -126 行 |
| [verify_tlm_three_layers.py](../../scripts/verify_tlm_three_layers.py) | 新增 L2 加载耗时断言（≤1s 性能护栏）+ L2 耗时打印 |

### 3. 文档（+367 行）

| 文件 | 说明 |
|------|------|
| [perf-async-io-analysis.md](../perf-async-io-analysis.md) | 异步 IO 不适用场景分析（8 章节，含实测数据和根因） |
| [tests/performance/README.md](../../tests/performance/README.md) | L2 性能测试最佳实践详解 |
| [CHANGELOG_L2_ASYNC_IO_REVERT_20260726.md](../../CHANGELOG_L2_ASYNC_IO_REVERT_20260726.md) | 决策 Changelog（完整决策过程） |

### 4. CI 配置（+11 行）

| 文件 | 说明 |
|------|------|
| [.github/workflows/test.yml](../../.github/workflows/test.yml) | L2 性能回归测试步骤 + echo 方案标记（同步串行方案） |

### 5. 项目文档（+24 行）

| 文件 | 说明 |
|------|------|
| [README.md](../../README.md) | 根 README 追加 L2 性能测试最佳实践章节 |

## 关键变更说明

### 决策核心：回退异步 IO（commit `2ff228eb`）

**变更**: 从 `bench_l2_stress.py` 删除场景 E（`_async_build_l2` + `scenario_e_async_io`），-126 行

**依据**: 实测异步 IO P50 变慢 21 倍（16.81ms → 370.64ms），同步串行 + 路径缓存是最优方案

**删除内容**:
- `import types`（仅用于 monkey-patch）
- `_async_build_l2` 函数（异步版 _build_l2）
- `scenario_e_async_io` 函数（异步 IO 压测场景）
- main 中场景 E 调用块 + 同步 vs 异步对比输出

### 新增：CI 性能护栏（commit `4f7bbbeb`）

**变更**: 新增 4 个 pytest 性能回归测试，+247 行

**护栏阈值**:
- 冷启动 P99 < 2s
- 热启动 P99 < 1s
- 并发(10) P99 < 2s
- 缓存有效性：热启动 ≤ 2×冷启动

## 代码评审要点

### ✅ 需确认

1. **测试覆盖率**: 4 个性能护栏测试是否覆盖关键退化场景？
2. **阈值合理性**: CI 环境（共享 runner）的 P99 阈值（2s/1s）是否过宽或过严？
3. **降级安全**: sqlite-vec 不可用时测试自动 skip，是否影响 CI 通过率？
4. **echo 标记**: CI 日志的 echo 标记是否满足快速识别需求？

### 🔍 关注点

1. **bench_l2_stress.py 净增 452 行**: 大量压测代码，确认非测试场景不引入生产依赖
2. **monkey-patch 已移除**: 场景 E 的 monkey-patch（`types.MethodType`）已随回退删除，无残留
3. **文档一致性**: 5 份文档（分析文档/README/Changelog/简报/最佳实践）的结论是否一致？

### ⚠️ 风险

1. **CI 执行时间**: 4 个性能测试约 8s（本地），CI 环境可能 15-20s，确认不超时（--timeout=120）
2. **环境依赖**: 性能测试依赖 sqlite-vec，CI 环境需确认安装（`pip install -e .`）

## 评审命令

```bash
# 查看完整 diff
git diff 4f7bbbeb^..cd20ef04

# 查看单个 commit
git show 2ff228eb  # 回退场景 E

# 运行性能测试验证
pytest tests/performance/test_l2_perf_regression.py -m performance -v

# 运行极限压测
python scripts/bench_l2_stress.py --cold-count 200 --concurrency 10
```

---

*本摘要用于代码评审，完整决策过程见 [CHANGELOG_L2_ASYNC_IO_REVERT_20260726.md](../../CHANGELOG_L2_ASYNC_IO_REVERT_20260726.md)。*
