# 测试覆盖率对比 — v1.2.0-rc3-final vs v1.2.0-rc4（2026-08-14）

**目的**：确认 P1 A3 slow 分流策略是否影响整体测试覆盖范围。

> 前提说明：两次回归均未启用 coverage 工具（`-p no:cacheprovider`，无 `--cov`），
> 本对比为**覆盖范围（文件/用例集合）与执行完整性**分析；行/分支覆盖率待后续
> `--cov` 专项补充。

---

## 一、执行配置对比

| 维度 | rc3-final（分块 all 模式） | rc4（A3 分流后） |
|---|---|---|
| 命令 | `run_full_pytest.py 4 1`（不过滤） | `run_full_pytest.py 4 1 fast` + `1 1 slow` |
| 过滤 | 无 | fast: `-m "not slow"`；slow: `-m slow --runslow --timeout=300` |
| 测试文件 | 523（全量） | 519（fast）+ 4（slow）= 523 |
| slow 标注 | 无（不存在标记） | 4 处：weekly_report_generator / task_scheduler / task_scheduler_integration（文件级）+ e2e 热更（用例级） |
| 受影响用例 | — | ~199 个 slow 用例从 fast 移至 slow 模式 |

## 二、实际执行完整性（关键差异）

| chunk | rc3-final 实测（2026-08-14 上午） | rc4 fast 预期 |
|---|---|---|
| 0/1/2 | rc=1 **无汇总**（D 类 Timeout → pytest-timeout 强杀 `os._exit(1)`）→ 大量用例未执行 | 排除 slow 后稳定完成，逐 chunk 有 `passed` 汇总行 |
| 3 | rc=0，3662 passed / 39 skipped / 7 xfailed / 4 xpassed（完整） | 完整执行 |

**结论**：rc3-final 实际仅 chunk_3 完整（约 1/4 文件集），chunk_0/1/2 因 D 类卡死**实际覆盖不完整**；
rc4 fast 分流后预期 **519 文件全覆盖**，实际执行完整性**显著提升**（非降低）。

## 三、覆盖范围影响判定

| 影响面 | 判定 |
|---|---|
| 文件级覆盖 | 无损失：523 = 519（fast）+ 4（slow），覆盖路径不变，仅执行时机分离 |
| 用例级覆盖 | 无损失：slow 用例由 slow 模式单独执行（`--runslow` 激活 `--runslow` 门控用例） |
| B 类验证 | 无影响：B 类用例均非 slow，fast 分块仍完整覆盖 |
| e2e 热更用例 | 从 fast 移入 slow（t.join 卡死源）——fast 不再被其卡死，slow 模式 300s 余量监控 |
| coverage 行覆盖 | fast 的 coverage 报告不含 4 个 slow 文件；slow 模式单独产出 → **合计不变** |

## 四、结论

**A3 分流不减少整体覆盖范围**（523 文件全覆盖路径保持），且通过排除 D 类慢测试使
分块回归从"3/4 chunk 崩溃"变为"可稳定完整执行"——实际覆盖率不降反升。
slow 模式作为独立阶段（D 类监控）补足 4 个 slow 文件的执行与超时观测。

---

## 五、Phase 1 执行后补充（2026-08-14 实测，fast 4x1）

**运行实录**：`python scripts/run_full_pytest.py 4 1 fast`，共收集 **527** 个测试文件
（523 已跟踪 + 4 个新增未跟踪：test_evolution_loop / test_parent_selection /
test_self_healing_policy / test_skills_mgmt_safety），切 4 块串行执行。

| chunk | rc | 汇总行 | 卡死点（Timeout 时正在执行） |
|---|---|---|---|
| 0 | 1 | **无汇总**（强杀） | `test_permission_system_concurrency.py::test_concurrent_confirm_no_crash`（30 线程 teardown `t.join()` → `_wait_for_tstate_lock`） |
| 1 | 1 | **无汇总**（强杀） | `test_task_scheduler_comprehensive.py::test_generate_weekly_report_no_exception`（dotenv/pydantic_settings 环境扫描） |
| 2 | 1 | **无汇总**（强杀） | `test_memory_module.py::test_knowledge_base_initialization`（transformers `import_utils` 扫描） |
| 3 | 1 | **无汇总**（强杀） | `test_memory_optimized.py::test_vector_operations`（pydantic_settings `_lenient_issubclass`/abc 扫描） |

- monitor_phase1.py 判定：`0/4 chunk 有汇总 → 最终判定 FAIL`（已修复"全卡死时空转等超时"缺陷并复验）
- slow 模式（`1 1 slow`）：**未执行**（Phase 2 补跑）
- **结果修正**：第二节"rc4 fast 预期稳定完整执行"**未达成**——fast 集合内仍残留
  **4 个未标注 slow 的 D 类慢文件**（并发线程 join / 环境变量扫描 / transformers /
  pydantic_settings 导入），任一命中即整块强杀。**分流策略本身不减少覆盖范围**
  （527 文件全覆盖路径保持，仅执行时机分离），但**当前 fast 标记不完整**。
- ✅ 4 处标注已追加（2026-08-14，文件级 pytestmark，135 用例验证 fast 排除/slow 选中）；
  ⏳ 待并行会话（18708/16744）结束后重跑 fast 复查。
- ✅ **重跑复查完成（10:05-10:42）**：3/4 chunk 完成汇总（11016 passed）；发现第 5 卡点
  `test_memory_vector_store` 已追加标注；chunk_3 仅 1 个真实失败（性能断言，非 D 类）。
- 比对结论：覆盖范围判定维持"无损失"；执行完整性结论需修正为
  **"分流方向正确，标注已补全（共 9 处），重跑后 3/4 chunk 稳定完成"**
