# Shard 6 flaky 根因分析与总结报告（2026-08-10）

> 范围：CI run 31355530446「全项目测试覆盖率 (Shard 6/6)」中 `test_knowledge_observability.py` 4 例失败的根因诊断与修复。
> 状态：**根因确认并修复**（autouse fixture），本地复现/修复验证双通过；CI 验证待 run 31359130239。

---

## 1. 问题现象

| 失败测试 | 失败断言 |
|----------|----------|
| `test_emit_produces_parseable_json_line` | `JSONDecodeError: Expecting value: line 1 column 1 (char 0)` |
| `test_knowledge_trace_shares_id_in_chain` | `assert 0 == 3` |
| `test_knowledge_trace_explicit_param_priority` | `IndexError: list index out of range` |
| `test_knowledge_trace_concurrent_isolation` | `assert 0 == 10` |

**统一模式**：4 例全部是"捕获日志的 buffer 为空"——`json.loads` 解析空串、`len(rows)==0`。观测代码的 ContextVar / emit 逻辑根本没有机会输出。

## 2. 诊断过程（证据链）

| 步骤 | 实验 | 结论 |
|------|------|------|
| 1 | 本地单独跑 `test_knowledge_observability.py` | **8/8 通过**（含 4 个"失败"测试）→ 非确定性失败 |
| 2 | 对比 CI 命令：无固定 `--randomly-seed` | pytest-randomly 每 run 随机顺序 → 顺序依赖嫌疑 |
| 3 | 拉取 CI shard 6 失败堆栈 | 4 例 buffer 全空 → 日志未进入测试 handler |
| 4 | 搜索 tests 全局日志开关 | 定位 `tests/performance/test_knowledge_link_perf.py:28` 模块顶层 `logging.disable(logging.CRITICAL)` |
| 5 | **决定性复现**：同进程跑 perf + observability | **4 failed，模式与 CI 完全一致**（修复前） |

## 3. 根因

`test_knowledge_link_perf.py` 模块顶层执行 `logging.disable(logging.CRITICAL)`（为屏蔽断链 warning 计时干扰），**无任何恢复**：

- `logging.disable()` 是 **进程级全局开关**（模块锁存器），一旦设置屏蔽所有 < CRITICAL 日志，直到显式 `logging.disable(logging.NOTSET)`
- 模块**导入时**（collection 阶段）即生效，与 pytestmark 无关——`-m performance` 跳过用例也无法避免污染
- conftest 中 `root.setLevel(_GOLDEN_LEVEL)` 的恢复机制**不作用于** `logging.disable()`，无法自动兜底
- **为何此前未暴露**：4a2fd3d1 新增 `test_scripts_*` 后分片分配变化，perf 与 observability 首次落入同一 shard 进程；此前分片不同进程（进程隔离）而侥幸通过

## 4. 修复（da5f83ac）

```python
@pytest.fixture(autouse=True)
def _silence_knowledge_link_warnings():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)
```

- 移除模块顶层全局副作用，改为 autouse fixture：每个测试期间屏蔽、**结束后恢复**
- 污染不越出本模块，任何后续测试不再受影响
- 断言可读性：fixture 无显式断言，污染与否由 observability 测试自然验证

## 5. 验证结果

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| 同进程 perf + observability（确定性顺序） | **4 failed, 12 passed** | ✅ 16/16 passed |
| 同进程 perf + observability（`--randomly-seed=999`） | — | ✅ 16/16 passed |
| CI run 31359130239（da5f83ac，待出） | — | 待验证 |

## 6. 遗留与关联风险（预提交护栏 WARN）

1. **分片脚本未将 performance/stress 纳入串行段**：本次 flaky 正是 performance 测试混入并行 shard 矩阵的后果。建议后续在 `split_unit_tests.py` 中将 performance/stress 排除出并行分片（或独立 job），从分配层杜绝同类问题。
2. **6 处模块顶层副作用**（collection 阶段 import 即生效）：`test_behavior_controller.py:7`、`test_behavior_controller_debug.py:9`、`test_memory_manager.py:6`、`test_permission_system.py:5`（等）——与本次 `logging.disable` 同模式隐患（虽不全是日志开关），建议逐一审计是否需收敛为 fixture。
3. **其余存量 CI 非绿项**（与本次无关）：可观测性质量门禁覆盖率 22.40% < 60%（scripts 治理进行中）；knowledge 循环依赖已在 d354b4d0 根因消除。

## 7. 关键产物

| 类型 | 文件 |
|------|------|
| 修复 | [test_knowledge_link_perf.py](../tests/performance/test_knowledge_link_perf.py#L29-L41)（autouse fixture） |
| 触发 commit | `da5f83ac`（fix(test)） |
| 相关前置 | [knowledge_circular_dependency_fix_summary_20260810.md](../architecture/knowledge_circular_dependency_fix_summary_20260810.md) |

## 8. 结论

Shard 6 flaky **根因是性能测试的进程级日志污染，非观测链路代码缺陷**。已通过 autouse fixture 修复并在本地双场景验证通过；观察 CI run 31359130239 确认转绿后即可闭环。
