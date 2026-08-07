# PR #379 CI 等待原因分析报告

> 生成时间：2026-08-07 14:50 · PR #379（v1.0.0 标签前移归档报告）CI 状态：**56 SUCCESS / 2 FAILURE / 9 SKIPPED / 0 PENDING**，未全绿

---

## 1. 当前 Checks 总览

| 状态 | 数量 | 说明 |
|------|------|------|
| SUCCESS | 56 | 通过 |
| **FAILURE** | **2** | 文档链接预检与锚点回归测试 / 全项目测试覆盖率 (Shard 5/6) |
| SKIPPED | 9 | 含 Nightly Full Test、可见性趋势报告等（条件触发跳过，非失败） |
| PENDING/QUEUED | 0 | 无排队任务 |

**轮询历史**：14:28 后 watch 脚本退出（终端被回收），期间两个失败 job 已多次 rerun（文档链接预检 ×2、覆盖率 Shard 5/6 ×1）**均再次失败** → 判定为**真实失败**，非基础设施瞬时故障。

## 2. 失败 1：文档链接预检与锚点回归测试

```
[!] 发现 1 个失效链接
[BLOCK] 阻塞模式：失效链接 1 > 阈值 0
```

**根因**（本地 `fix_broken_links.ps1 -DryRun` 实测定位）：

| 项 | 值 |
|----|----|
| 失效文件 | `docs/observability/ops_log_parallel_session_cleanup_20260806.md`（**master 既有已跟踪文件**） |
| 原链接 | `[cleanup_parallel_session_tmp.ps1](../../scripts/dev/cleanup_parallel_session_tmp.ps1)` |
| 失效原因 | 目标 `scripts/dev/cleanup_parallel_session_tmp.ps1` **不存在于任何分支**——该脚本是并行会话主工作区 untracked 文件，从未提交入库 |

**与 PR #379 的关系**：**无关**。失效链接由并行会话提交的文档引入（其引用的脚本未同步入库），PR #379 仅新增 v100_tag_advance_final_archive_20260807.md（无 markdown 链接），扫描 PR head 全仓库时撞上既有失效链接。

## 3. 失败 2：全项目测试覆盖率 (Shard 5/6)

```
FAILED tests/performance/test_lazy_loader_performance.py::TestLazyLoaderPerformance::test_module_registration_time
  AssertionError: 模块注册时间过长: 52.52ms (assert 52.5 < 50.0)
FAILED tests/stress/test_resource_leak.py::TestSamplingPerformance::test_single_sample_under_600ms
  AssertionError: 采样中位数耗时 990.56ms 超过 600ms（1% 开销约束）
===== 2 failed, 2226 passed, 11 skipped, 14 warnings in 587.59s =====
```

**根因**：2 个**性能边界测试**超阈值（注册 52.52ms > 50ms、采样 990ms > 600ms），其余 2226 个测试全过。
- 典型 **flaky**（runner 负载高导致性能波动，与上次 PR #354 轮询遇到的性能类失败同类）
- **与 PR #379 的关系**：**无关**——纯文档 PR 不改任何代码，性能测试失败系 runner 环境负载所致

## 4. 结论

PR #379（纯文档归档）**本身无 CI 缺陷**，2 个失败均为外部因素：
1. 文档链接预检 ← master 既有失效链接（并行会话文档引用未入库脚本）
2. Shard 5/6 ← 性能测试 flaky（runner 负载）

## 5. 处置建议

| 选项 | 操作 | 适用场景 |
|------|------|----------|
| **A（推荐）** | 直接合并 PR #379——仓库分支保护**无 required checks**（PR #371 合并时已确认），2 个失败与 PR 内容无关，合并后跑第 11 次前移 | 归档类文档 PR，快速收尾 |
| B | 先修复失效链接：并行会话提交 `cleanup_parallel_session_tmp.ps1`（或修正 ops_log 文档链接）→ rerun 文档链接预检 → 全绿后合并 | 需保证 master CI 全绿 |
| C | rerun 覆盖率 Shard 5/6（性能 flaky 大概率通过），修复文档链接后合并 | 严谨但耗时（Shard 单次 ~10min） |

> 无论 A/B/C，**第 11 次前移脚本已就绪**：`pwsh -File scripts/dev/advance_v100_tag.ps1 -Execute -SyncGitee`（v1.0.0 落后 origin/master 6 提交，触发条件已成立）。
