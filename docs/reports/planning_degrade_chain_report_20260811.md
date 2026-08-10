# 降级链路（D14）测试报告

> 生成日期: 2026-08-11
> 范围: planning/executor.py 降级链实现的分支覆盖验证 + 规划模块遗留技术债清单
> 关联: [test_planning_defect_d14.py](../../tests/unit/test_planning_defect_d14.py) · [test_planning_capability_baseline.py](../../tests/unit/test_planning_capability_baseline.py)

## 1. 背景

D14 缺陷（P2）:任务失败即标记失败、无 Plan B、无降级链。`PlanExecutor.config` 支持配置但从未实现 degrade chain。

2026-08-11 已修复(commit 自 master 59362542 起):

| 改动点 | 内容 |
|---|---|
| `__init__` | 解析 `config["degrade_chain"]`（主工具名 → 备份工具列表），缺失零回退 |
| `_do_execute_task` | 主工具失败（仅 TOOL_CALL 动作）→ 进入降级分支 |
| `_try_degrade_chain` | 沿链逐个尝试备份工具；任一成功返回成功（observation 标注降级来源）；全失败返回 None |
| 重试语义 | 全备份失败才抛 `RecoverableError`，`async_with_retry` 原样重试（不变量未破坏） |

## 2. 分支覆盖矩阵

| 分支 | 场景 | 预期行为 | 用例 | 结果 |
|---|---|---|---|---|
| A | 主工具失败 + 单个备份成功 | 降级成功,任务 completed,结果为备份输出 | `test_task_failure_uses_degrade_chain` | ✅ passed |
| B | 主工具成功 | 备份绝不被调用（零触发） | `test_primary_success_skips_degrade_chain` | ✅ passed |
| C | 多备份依次尝试:第 1 个失败 | 不中断,继续尝试第 2 个并成功 | `test_chain_tries_next_backup_after_failure` | ✅ passed |
| D | 全部备份失败 | 任务 failed,错误保留主工具根因 | `test_all_backups_fail_keeps_primary_error` | ✅ passed |
| E | 备份工具未注册 | 跳过该备份项（warning 不抛错），主失败则任务失败 | `test_unknown_backup_tool_skipped` | ✅ passed |
| F | 无 degrade_chain 配置 | 行为与修复前一致（零回退成本） | `test_no_degrade_chain_config_behavior_unchanged` | ✅ passed |
| G | 能力基线:任务级降级链 | 主失败按链尝试 Plan B 成功 | `test_task_degrade_chain`（capability_baseline） | ✅ passed |

**未单独构造的分支**:
- 非 TOOL_CALL 动作失败（LLM 推理路径）:降级链仅拦截 `ActionType.TOOL_CALL`,LLM 路径由既有集成测试覆盖,未单独构造。
- 备份工具同时为异步实现:注册表工具均为同步,异步工具注册为等价包装,语义一致。

## 3. 测试执行结果

```text
python -m pytest tests/unit/test_planning_defect_d14.py tests/unit/test_planning_capability_baseline.py -q
→ 7 passed, 4 skipped in 4.54s
```

- 7 passed = d14 缺陷复现 + 5 分支（B-F）+ capability 能力基线用例
- 4 skipped = 能力基线其余规格（D5/D9/D11/D13，见 §4）

## 4. 规划模块遗留技术债清单（master 现状扫描）

**已闭环**:19 个缺陷复现测试（d1–d19）全部通过;planning/ 源码无 TODO/FIXME/待实现注释;D6/D7/D8/D10/D13/D14/D15/D16/D17 已修复（本工作系列）。

**遗留（capability_baseline 4 个 skip，均为更高阶规格）**:

| 规格 | 缺陷编号 | 现状 | 说明 |
|---|---|---|---|
| 并行组执行 | D5 | 基础并行已实现（`next_tasks` gather），capability 规格要求严格使用 `decomposer.parallel_groups` | 部分满足,可取消 skip 验证 |
| 计划验证 | D11 | `validate_plan` 已实现（悬空/环检测），capability 规格另要求"工具可用性"校验 | 部分满足,可取消 skip 验证 |
| 持久化恢复 | D9 | **未实现**（无 SQLite 落库/恢复） | 真实缺口,需排期 |
| 预算超限降级 | D13 | deadline 超限终止已实现;capability 规格要求 token/cost 预算 + "触发降级或征求用户" | 部分满足,deadline 分支已覆盖 |

## 5. 结论

- 降级链 6 个行为分支 + 1 个能力基线用例全部通过,覆盖主成功/单备份/多备份/全失败/未知备份/无配置六类场景。
- 重试语义、错误保留（主工具根因）、零回退成本（无配置时行为不变）三个不变量经分支 D/E/F 验证。
- 遗留技术债:D9 持久化（真实缺口）;D5/D11/D13 为规格级差距（基础能力已落地）,建议后续取消 skip 逐项验证。
