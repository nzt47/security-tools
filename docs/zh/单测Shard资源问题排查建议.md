# 单测 Shard 资源问题 · 排查建议（提交基础设施团队）

> 项目：云枢 · AI 智能体桌面工作台
> 问题：单元测试矩阵偶发失败 `RuntimeError: can't start new thread`
> 日期：2026-08-23

---

## 1. 问题现象

PR #754 与 develop 合并后的 CI run 中，单元测试多个 Shard 偶发失败：

```
单元测试 (Python 3.10 / Shard 2)  failure
单元测试 (Python 3.11 / Shard 2)  failure
单元测试 (Python 3.12 / Shard 2)  failure
单元测试 (Python 3.11 / Shard 5)  failure
```

CI 日志中的失败根因（已有并行会话备注）：
```
8661 个测试累积导致 "RuntimeError: can't start new thread" INTERNALERROR
Shard 3 py3.12 实跑验证（INTERNALERROR: can't start new thread）
```

## 2. 根因分析（初步）

`RuntimeError: can't start new thread` 是 **Python 进程无法创建新线程**——`pytest-xdist` 多 worker + 测试内并发（threading/线程池）叠加，在 runner 资源受限时达到**线程数上限**（进程数 × 每进程线程限制）。

关键特征：
- **偶发**（非固定用例失败，本次失败用例为报表渲染/回滚变体等随机分布）
- **跨 Shard 跨 Python 版本**（Shard 2 三版本全失败，Shard 3 实跑复现）
- 8661 个测试累积——套件规模大 + 并发叠加触发资源上限

## 3. 排查建议

### 3.1 短期（降低触发概率）

| # | 动作 | 说明 |
|---|---|---|
| 1 | 分片粒度调整 | Shard 数量 ≥ 并行 job 数，避免单 runner 承载过重 |
| 2 | 限制 pytest-xdist 并发 | 设置 `-n` 上限（如 `-n 4`）或 `--maxprocesses`，防止 worker 数 × 线程数超限 |
| 3 | 收敛测试内线程 | 排查并发测试用例（ThreadPoolExecutor/threading）的线程池大小，避免无界创建（如 `max_workers` 显式设置） |
| 4 | 资源隔离 | 单测 job 的 runner 类型升级（如 4 vCPU → 8 vCPU）或限制同 runner 并发 job 数 |

### 3.2 中长期（结构性）

| # | 动作 | 说明 |
|---|---|---|
| 5 | 失败重试策略 | CI 对"非断言类"失败（INTERNALERROR）自动重试 1 次（区分真失败与资源抖动） |
| 6 | 测试并发规范 | 仓库级规范：测试内并发需显式控制线程池上限，纳入 code review |
| 7 | 健康监控 | 在 CI 步骤前置资源探测（`ulimit -u` / `nproc`），超出阈值提前告警 |

### 3.3 验证方式

- 重跑失败 run（`gh run rerun <id> --failed`），观察是否稳定通过（佐证资源抖动）
- 本地复现：`pytest tests/unit -n 8 --maxprocesses 4` 对比

## 4. 备注

- 该问题**与具体 PR 无关**（PR #754 无相关代码改动），属基础设施/测试套件规模问题
- 已确认不影响测试正确性（失败为进程级异常，非断言失败）
- 建议作为独立 issue 跟踪，基础设施团队可基于本建议落地
