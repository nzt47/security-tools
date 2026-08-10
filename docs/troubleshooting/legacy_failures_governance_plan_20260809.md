# 遗留失败治理方案 — Shard 1/2/5（2026-08-09）

> 性质：R1-R4 修复后遗留的 3 类失败治理规划
> 关联 run：31318623868（门禁已转绿，但 Shard 1/2/5 仍有测试失败）
> 状态：根因假设已建立 / 验证步骤与修复选项待确认后实施
> 优先级判定：三处均**不阻塞覆盖率门禁**（已转绿），属质量收尾，非 P0

---

## 0. 背景与失败矩阵

R1-R4 修复已达成核心目标：Shard 3/6 转绿、6/6 artifact 上传、门禁 success（覆盖率 68.67%）。
但 3 个 Shard 仍有测试失败，与本次修复无耦合，属独立治理项：

| Shard | 失败 | 数量 | 性质 | 门禁影响 |
|---|---|---|---|---|
| 1/6 | `test_singleton_manager.py::test_metrics_modules_registered` | 1 | **确定性**（断言未实现的迁移） | 无（门禁已绿） |
| 2/6 | `test_perf_monitor.py::TestStressTestDependencyInjection` | 7 | **环境相关**（本地 21/21 通过） | 无 |
| 5/6 | `test_task_scheduler_singleton.py::test_concurrent_first_get_initializes_once` | 1 | **顺序敏感 flake**（上轮通过本轮失败） | 无 |

---

## 1. Shard 1：test_metrics_modules_registered（确定性失败）

### 1.1 根因假设（证据充分，可直接确认）

- 失败点：`test_singleton_manager.py:218 assert is_registered("auto_tuner")` → False
- **全代码库无任何 `register_singleton("auto_tuner"...)` / `error_reporter` / `optimized_metrics` / `trace_cache` 注册调用**（Grep 全仓 0 命中）
- 四个目标模块仍为**手写全局单例**：`_global_reporter`（error_reporter.py:771）、`_global_trace_cache`（tracing_cache.py:452）、`_global_optimized_collector`（optimized_metrics.py:474）、`get_auto_tuner` 用 `_global_auto_tuner`（auto_tuner.py:968）
- **结论：测试断言了"已迁移到 SingletonManager"，但迁移从未完成** —— 测试先行 / 迁移遗留

### 1.2 修复选项

| 选项 | 内容 | 成本 | 风险 |
|---|---|---|---|
| A（推荐） | **完成迁移**：给 4 个模块补 `register_singleton` 模块级注册，getter 改走 SingletonManager（对齐 task_scheduler 模式） | 中 | 中（涉及运行时单例行为变更，需回归） |
| B（最小） | **修正测试**：将断言改为验证各模块 getter 幂等（`get_x() is get_x()`），不要求注册 | 低 | 低（放弃迁移验收） |
| C（折中） | 迁移 auto_tuner/error_reporter（高频模块），其余保持手写 + 测试按实际调整 | 中 | 中 |

> 【不易】约束：若迁移从未完成是事实，则测试当前断言**永远无法通过**——不存在"修复测试就掩盖问题"的风险，B 是诚实修正；A 是完成预期迁移。建议先确认迁移意图（查 git log 相关提交），再选 A/B。

### 1.3 验证步骤

1. `git log --oneline --all -- agent/utils/singleton_manager.py | head` 查迁移节奏
2. `git log -S 'register_singleton("auto_tuner"' --oneline` 确认是否曾有注册后移除
3. 若确认从未迁移 → 按 A 或 B 实施

---

## 2. Shard 2：TestStressTestDependencyInjection（7 个 filter 测试）

### 2.1 根因假设（环境相关，需 CI 侧证据闭环）

- 失败模式：`custom filter 应被调用` / `filter 调用次数 0 应等于 total_ops`（call_count=0）
- **本地复现：同一测试类 21/21 全部通过**（Windows + pytest 9.1.1 单进程）
- 代码逻辑：`stress_test` 用独立 logger（`stress_test_{id}`，propagate=False）+ `_DiscardHandler` + 注入 filter（perf_monitor.py:475-487）——逻辑本身正确
- 候选根因（按可能性排序）：
  1. **CI 并行环境（-n 2）logging 状态竞争**：与 Shard5/6 已根治的 assertLogs 问题同源 —— 并行 worker 中某测试调用 `setup_agent_logging()` 改全局 logging，导致 `stress_logger.info()` 未触发 handler.filter
  2. **CI 与本地 Python 版本差异**（CI 3.11 vs 本地 3.12）：logging 内部行为差异
  3. **测试顺序**：同 worker 内前序测试污染 `logging.disable()` / handler 状态

### 2.2 修复选项

| 选项 | 内容 | 成本 | 风险 |
|---|---|---|---|
| A（推荐） | 效仿 serial 根治：该 7 个依赖 filter 调用计数的测试加 `@pytest.mark.serial`（隔离到串行段） | 低 | 低（复用已验证模式） |
| B | 断言宽松：`call_count > 0` 替代 `== total_ops`（去掉精确相等） | 低 | 中（减弱断言强度） |
| C | 测试内显式 `logging.disable(logging.NOTSET)` 复位全局状态 | 低 | 低（治标） |
| D | 深入 CI 日志定位确切污染源后根治 | 高 | 低 |

> 建议：先跑一次 CI 复现 + 抓 `test_perf_monitor` 前后序测试，确认是顺序还是并行竞争；若与 Shard5/6 同源则直接 A（serial marker）。

### 2.3 验证步骤

1. 抓 CI Shard 2 完整日志：确认 `test_custom_filter_invoked_per_record` 失败时同 worker 前序执行了哪些测试
2. 本地模拟：`pytest tests/unit/test_perf_monitor.py tests/unit/test_singleton_manager.py -n 2` 看是否复现
3. 确认后按 A 实施（serial）并回归

---

## 3. Shard 5：test_concurrent_first_get_initializes_once（顺序敏感 flake）

### 3.1 根因假设

- 失败模式：`应只构造一次，实际 0 次`（created 列表为空，而非 >1）
- **created == 0 意味着 factory 从未被调用** → `get_scheduler()` 返回了 SingletonManager 中**已缓存的旧实例**（工厂路径未走）
- 测试用 `module.TaskScheduler = CountingScheduler` 替换类统计构造次数，但若实例已缓存，替换类不生效
- 候选根因：
  1. **测试隔离不足**：autouse fixture `_cleanup_singleton` 调用 `module.reset_scheduler()`，但若 `_SINGLETON_AVAILABLE` 为 False 时走 fallback 分支（task_scheduler.py:466-471），reset 只置 `_scheduler=None` 不清 SingletonManager 缓存 → 前序测试（同 worker）创建的实例残留
  2. **xdist 顺序 shuffle**：上轮该测试在 Shard 5 通过、本轮失败 → 与同 worker 前序测试序列相关

### 3.2 修复选项

| 选项 | 内容 | 成本 | 风险 |
|---|---|---|---|
| A（推荐） | 检查 `_SINGLETON_AVAILABLE` 判定与 reset 语义：确保 fixture 与 fallback 分支都彻底清空；必要时测试内显式 `reset_singleton` + 断言前置 `is_initialized` 为 False | 低 | 低 |
| B | 该并发测试加 `@pytest.mark.serial`（与 Shard5/6 同模式） | 低 | 低 |
| C | 断言增强：先 `assert not is_initialized("task_scheduler")` 再并发，失败即暴露隔离问题而非"构造 0 次" | 低 | 低（诊断优先） |

> 建议：A+C 组合 —— 先修 reset 语义（根），再补前置断言（诊断增强）。若时间紧可先 B 止血。

### 3.3 验证步骤

1. 读 `agent/task_scheduler.py` 的 `_SINGLETON_AVAILABLE` 定义与 import 兜底逻辑（L31-36）
2. 本地连续多次运行：`pytest tests/unit/test_task_scheduler_singleton.py -n 2 --count=5`（或与整 shard 文件列表联跑）复现顺序敏感性
3. 修复后本地 + CI 双验证

---

## 4. 治理优先级与路线

| 阶段 | 项 | 理由 |
|---|---|---|
| P1（先做，低风险） | Shard 5 隔离修复（选项 A+C） | 单文件、根因清晰、成本低 |
| P2（并行推进） | Shard 2 serial 化（选项 A） | 复用已验证模式，若同源立即生效 |
| P3（需决策） | Shard 1 迁移意图确认 → A 或 B | 涉及运行时单例行为，需用户拍板 |

## 5. 验收标准

| 项 | 标准 |
|---|---|
| Shard 1 | 测试通过（迁移完成 或 测试修正为与实现一致） |
| Shard 2 | 7 个 filter 测试在 CI 并行下稳定通过（serial 后） |
| Shard 5 | 并发测试连续 5 轮通过，无顺序依赖 |
| 门禁 | 维持 success（覆盖率 68.67% 不受影响） |
| 覆盖率 | 6/6 artifact 继续上传，omit 生效不回归 |

## 6. 关联文档

- [shard_coverage_artifact_and_omit_rootcause_20260809.md](shard_coverage_artifact_and_omit_rootcause_20260809.md)（R1-R4 排查报告）
- [r1_r4_fix_pr_and_impact_20260809.md](r1_r4_fix_pr_and_impact_20260809.md)（PR 与影响评估）
- [shard56_log_assert_rootcause_archive_20260809.md](shard56_log_assert_rootcause_archive_20260809.md)（serial 根治模式参考）
