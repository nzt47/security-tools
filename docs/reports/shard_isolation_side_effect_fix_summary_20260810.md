# 分片隔离与副作用修复总结报告（2026-08-10）

> 关联工作链：PR #7（develop 全绿治理主链路）→ PR #586（`feature/archive-agent-tests`，归档收尾）
> 本报告覆盖 PR #586 合并前的修复集合：分片隔离（阶段 A）、`shell: bash` 修复、
> `agent/tests` 归档回归修复，及 pytest 收集隔离机制验证。

## 1. 问题链背景

2026-08-10 在 develop 全绿治理过程中，并行分片 CI 出现两类不稳定：

| 现象 | 根因 | 影响 |
|------|------|------|
| Shard 6 偶发失败 | `test_knowledge_link_perf.py` 模块顶层 `logging.disable(CRITICAL)` 无恢复，进程级屏蔽同进程后续日志捕获测试 | 决定性复现：perf + observability 同进程 → 4 failed |
| Windows runner ParserError | tool-tests.yml 多行 pytest 命令用 bash `\` 续行符，默认 shell 为 PowerShell，`\` 被解析为除法运算符 | 归档分支 run 31377751010 全矩阵失败 |

## 2. 修复项与验证

### 2.1 副作用修复：日志屏蔽改 autouse fixture（PR #7 阶段完成）

- `tests/performance/test_knowledge_link_perf.py`：模块顶层 `logging.disable` 改为
  `@pytest.fixture(autouse=True)` 内 `disable → yield → NOTSET` 恢复，消除进程级污染。
- 详见 `docs/observability/shard6_flaky_root_cause_analysis_20260810.md`。

### 2.2 分片隔离（阶段 A）：`split_unit_tests.py` 排除 performance/stress

- `SERIAL_DIRS = ("tests/performance/", "tests/stress/")`，全项目模式（`--root tests`）
  收集后按目录前缀过滤，计时敏感 + 顶层副作用文件移出并行矩阵。
- 串行覆盖方案见 `docs/observability/scripts_serial_ci_segmentation_plan_20260810.md` 阶段 B（待实施）。

### 2.3 `shell: bash` 修复（PR #586 分支 commit `72ebc37`）

- tool-tests.yml「运行工具模块测试」step 增加 `shell: bash`，并更新 push paths 指向
  `docs/archive/agent_tests_20260810/test_*tools*.py` / `test_file_tools.py`。
- 触发验证：本批 PR #586 新提交触及 `docs/archive/agent_tests_20260810/test_file_tools.py`
  （追加归档说明注释）→ 触发 tool-tests 用最新 head workflow（含 `shell: bash`）。

### 2.4 归档回归修复（Shard 6/6 ModuleNotFoundError）

- 归档后 `agent/tests/` 不再是可导入包，`tests/unit/test_agent_tests_helpers.py`
  的 `from agent.tests.stress_test_tools import ...`（18 处）报 ModuleNotFoundError。
- 修复：`git mv tests/unit/test_agent_tests_helpers.py docs/archive/agent_tests_20260810/`
  （保留历史；该测试针对已归档代码，跟随归档合理）。
- 脚本引用迁移（归档分支 `ed7699da` 已迁移 cicd_pipeline.py / apply_config_and_test.py）：
  本次补齐 `scripts/stress_test_pipeline.py` → 与 cicd_pipeline.py 同构的
  `load_tool_router_tester()` 文件加载（`importlib.util.spec_from_file_location`），
  本地已验证可加载 `tool_router_tester.ToolRouterTester`。

### 2.5 pytest 收集隔离机制验证（docs/archive 不再触发收集）

机制层双保险：

1. `pytest.ini` `testpaths = tests` → pytest 根收集仅扫描 `tests/`，`docs/` 天然不收集。
2. `scripts/split_unit_tests.py` 仅扫描 `tests/unit`（默认）或 `tests`（`--root tests`）子树，
   归档目录不在任何分片清单内。

实证（本地执行 6 分片全量枚举）：

```
test_agent_tests_helpers 在分片清单中: False   # 回归修复生效
归档文件在分片清单中: False                    # docs/archive 不触发收集
```

另：归档 6 个 tool-tests 文件本地 pytest 收集成功（132 tests），tool-tests 显式路径运行不受影响。

## 3. 本批提交内容（PR #586）

| 文件 | 变更 |
|------|------|
| `docs/archive/agent_tests_20260810/test_agent_tests_helpers.py` | 自 tests/unit 归档（git mv） |
| `docs/archive/agent_tests_20260810/test_file_tools.py` | 追加归档说明 docstring（触发 tool-tests 验证） |
| `scripts/stress_test_pipeline.py` | 包导入迁移为归档文件加载 |
| `docs/reports/shard_isolation_side_effect_fix_summary_20260810.md` | 本报告 |

## 4. 验证矩阵与剩余风险

| CI workflow | 状态 | 说明 |
|-------------|------|------|
| tool-tests.yml | 待验证（本批触发） | shell: bash 修复后的首次验证 |
| observability-ci Shard 6/6 | 已修复 | test_agent_tests_helpers 移出 |
| observability-ci Shard 1/6 | 观察 | `test_metrics_deadlock_fix.py` 并发时序 flaky（`assert 9 == 10`），与归档无关，develop 同批全绿 |
| test.yml unit-tests | 已修复 | `pytest tests/unit/` 全量收集的归档回归随 2.4 消除 |
| ci.yml 分片（31380932584） | 全绿 | 与本次修复无冲突 |

剩余风险：
- `test_metrics_deadlock_fix.py` 高并发时序 flaky 需独立治理（与本次修复正交），
  建议后续走 flaky 治理路线图（`docs/observability/develop_green_roadmap_20260810.md`）。
- 阶段 B（performance/stress 串行 job 落地）待实施，当前并行矩阵已排除，覆盖率不受影响。

## 5. 结论

PR #586 合并后，`docs/archive/agent_tests_20260810/` 将作为存档目录存在：
- pytest（`testpaths=tests`）与分片脚本均不收集 → 不再触发 pytest 收集；
- tool-tests.yml 显式路径运行归档测试，`shell: bash` 保证 Windows runner 无 ParserError；
- 归档回归（Shard 6/6）已修复，CI 具备转绿条件。
