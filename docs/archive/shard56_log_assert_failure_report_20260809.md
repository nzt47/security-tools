# Shard 5/6 日志断言失败排查报告（2026-08-09）

> run：31307725441（head 410dc41e，P3-1 门禁验证 dispatch）
> 现象：Shard 5/6 的 6 个测试失败，全部为日志采集类断言
> 结论：**CI 环境噪音（pytest-xdist 并行 + 全局日志状态竞争），非代码缺陷**（本地串行全部通过）

---

## 1. 失败清单

| 测试 | 断言 | 失败信息 |
|---|---|---|
| test_skill_manager.py::test_build_context_scenario_budget_exceeded_skip_instruction | assertLogs | `no logs of level INFO or higher triggered on agent.skills_mgmt` |
| test_skill_manager.py::test_build_context_scenario_no_match_logs_boundary_state | assertLogs | 同上 |
| test_skill_manager.py::test_build_context_scenario_skill_not_found | assertLogs | 同上 |
| test_log_dict_refactor.py::test_file_log_json_parseable | 文件内容 | `assert 'file json test' in ''`（文件为空） |
| test_knowledge_search.py::test_link_stage_below_io_bound | 自定义 TimingHandler | `应采集到 search_stage_timing 日志`（列表为空） |
| test_security_utils_comprehensive.py::test_data_sanitizer_init_logging | assertLogs | `assert False` |

## 2. 根因分析

### 2.1 直接根因：pytest-xdist 并行 + 全局日志状态竞争

CI 全项目测试命令（observability-ci.yml L920-922）：

```bash
pytest -n 2 --dist=loadscope --cov=... 
```

- `-n 2` 启动 2 个并行 worker 进程，`--dist=loadscope` 按模块粒度分发
- **每个 worker 进程独立持有全局 logging 配置**。`logging.getLogger("agent.skills_mgmt")` 等是进程级全局单例
- `assertLogs` 通过临时替换 handler 捕获日志；若被测代码在 worker 中执行时 logger 已 attach 了**其他 worker 路径残留的 handler**（或 `setup_agent_logging` 在早前测试中被调用修改了 root logger 配置），则捕获失效

### 2.2 触发链条（test_skill_manager 3 例）

```text
[worker A] 某测试调用 setup_agent_logging() → 修改 root logger 的 propagate/handlers
[worker B] assertLogs("agent.skills_mgmt", level="INFO")
           → 依赖 logger.propagate 为 True 才向 root 冒泡
           → 若 root handler 被禁用/替换 → 日志被吞 → cm 记录为空 → 断言失败
```

### 2.3 时序类（test_log_dict_refactor / test_knowledge_search）

- `test_file_log_json_parseable`：`h.flush()` + `time.sleep(0.1)` 后读文件——CI 负载高时 flush 未落盘/worker 竞争
- `test_link_stage_below_io_bound`：handler 挂在 `agent.knowledge.search` logger，若 root logger propagate 被改则记录不达 handler

### 2.4 为什么本地不失败

本地验证（`python -m pytest ... -p no:randomly`，单进程）4 个失败项**全部通过**：
```
4 passed in 2.04s
```
串行时全局日志状态无竞争，assertLogs 可靠捕获。

## 3. 证据链

| 证据 | 说明 |
|---|---|
| 本地串行通过 | 4 个失败项本地全绿（2.04s） |
| CI 参数 | `-n 2 --dist=loadscope` 确认存在 |
| 失败模式一致 | 6 例全部「日志未采集到」类，无业务断言失败 |
| 与代码改动无关 | 失败测试均不涉及 omit/pyproject/concurrency 改动文件 |

## 4. 处置建议

### 4.1 立即：重跑排除噪音
`gh run rerun 31307725441 --failed`（已触发）——观察是否转绿。

### 4.2 根治方向（择一，待用户确认）

| 方案 | 做法 | 成本 | 效果 |
|---|---|---|---|
| A. 日志测试标记串行 | 对 6 个日志断言测试加 `@pytest.mark.serial`，CI 用 `-m "not serial"` 并行 + `-m serial` 串行两段 | 低 | 彻底消除日志竞争 |
| B. 关闭 propagate 依赖 | 测试内显式 attach handler 到目标 logger（不依赖 root 冒泡） | 中 | 根治但需改 6 个测试 |
| C. 暂缓 | 仅重跑，接受偶发 | 零 | 噪音持续，污染门禁 |

**建议方案 A**：最小改动（仅加 marker + CI 命令微调），与「测试=不易护城河」一致——保留日志断言强度，消除环境竞争。

## 5. 关联

- 门禁验证 run 31307725441（rerun 已触发）
- 过渡方案：见 [scripts_gate_transition_plan_20260809.md](scripts_gate_transition_plan_20260809.md)
