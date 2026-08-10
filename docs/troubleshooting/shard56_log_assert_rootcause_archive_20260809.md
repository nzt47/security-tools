# Shard 5/6 日志断言失败根因分析 — 归档文档（2026-08-09）

> 归档性质：P3 治理闭环归档，供后续复盘与同类问题速查
> 关联 run：31307725441（P3-1 门禁验证 dispatch，rerun 后仍失败）
> 根治提交：ad27fb1e（serial marker 串行隔离）
> 状态：根因已定位 / 根治已实施 / 本地验证通过 / CI 排队中（runner 瓶颈）

---

## 1. 事件概要

| 项 | 值 |
|---|---|
| 时间 | 2026-08-09 |
| 影响范围 | observability-ci「全项目测试覆盖率 (Shard 5/6)」job |
| 失败数 | 6 个测试（全部为日志采集类断言） |
| 失败模式 | `no logs of level INFO or higher triggered on ...` / 日志文件为空 / 日志列表为空 |
| 直接结论 | **CI 环境噪音（pytest-xdist 并行 + 全局 logging 状态竞争），非代码缺陷** |

## 2. 失败清单（6 例）

| 测试文件 | 测试名 | 断言类型 |
|---|---|---|
| test_skill_manager.py | test_build_context_scenario_budget_exceeded_skip_instruction | assertLogs |
| test_skill_manager.py | test_build_context_scenario_no_match_logs_boundary_state | assertLogs |
| test_skill_manager.py | test_build_context_scenario_skill_not_found | assertLogs |
| test_log_dict_refactor.py | test_file_log_json_parseable | 文件内容断言（为空） |
| test_knowledge_search.py | test_link_stage_below_io_bound | 自定义 TimingHandler 收集列表（为空） |
| test_security_utils_comprehensive.py | test_data_sanitizer_init_logging | assertLogs |

## 3. 根因分析

### 3.1 直接根因：pytest-xdist 并行 + 全局 logging 状态竞争

- CI 命令：`pytest -n 2 --dist=loadscope`（observability-ci.yml）
- `-n 2` 启动 2 个并行 worker 进程，**每进程独立持有全局 logging 单例**
- `assertLogs` 通过临时替换 handler 捕获日志，依赖 logger 向 root 冒泡
- 并行时某 worker 早前测试调用 `setup_agent_logging()` 修改 root logger 的 handlers/propagate → 其他 worker 的日志被吞 → 捕获为空 → 断言失败

### 3.2 复现证据链

| 证据 | 结果 |
|---|---|
| 本地串行（-p no:randomly） | 4 个代表项 **全部通过**（2.04s） |
| CI 并行（-n 2） | 6 例确定性失败（rerun 后仍失败） |
| 失败模式 | 全部「日志未采集到」，无业务断言失败 |
| 与改动关系 | 失败测试均不涉及 omit/pyproject/concurrency 改动文件 |

## 4. 根治方案：serial marker 串行隔离（ad27fb1e）

### 4.1 原理

【不易】保留断言强度，不降级测试；仅将依赖全局 logging 状态的测试隔离到串行段。

### 4.2 改动清单

| 文件 | 改动 |
|---|---|
| pytest.ini | 注册 `serial` marker |
| test_skill_manager.py | +`import pytest`；3 个 assertLogs 测试加 `@pytest.mark.serial` |
| test_log_dict_refactor.py | test_file_log_json_parseable 加 `@pytest.mark.serial` |
| test_knowledge_search.py | test_link_stage_below_io_bound 加 `@pytest.mark.serial` |
| test_security_utils_comprehensive.py | test_data_sanitizer_init_logging 加 `@pytest.mark.serial` |
| observability-ci.yml | 测试拆两段：并行段 `-m "not slow and not skip_ci and not serial"`（-n 2）；串行段 `-m "... and serial"`（单进程 + `--cov-append` 追加覆盖率） |

### 4.3 本地验证

```
python -m pytest ... -m "serial" --collect-only  → 6/267 tests collected（恰好 6 个，261 deselected）
python -m pytest ... -m "serial" -q              → 6 passed, 261 deselected in 2.21s
```

### 4.4 CI 验证状态（截至 2026-08-09）

- 提交 `ad27fb1e` push 后触发 8 个 workflow run（run 31315288819 ~ 31315288868）
- 全部 `status=queued`（等待 runner 分配，与既知 runner 容量瓶颈同源）
- 结论待 Shard 完成后再确认门禁行为

## 5. 验收标准

| 项 | 标准 |
|---|---|
| 并行段 | 261 个非 serial 测试全绿，无日志噪音 |
| 串行段 | 6 个 serial 测试全绿 |
| 覆盖率合并 | 两段 --cov-append 后合并数据完整（Shard 5/6 覆盖率不被截断） |
| 门禁 | 覆盖率 ≥ 40% 时门禁转绿 |

> 验收结论：待 CI 执行完成（当前排队中）。本地侧已验证 marker 收集与串行段执行均正常。

## 6. 经验教训（供复盘）

1. **测试≠纯业务**：日志断言依赖全局状态，需与并发框架（xdist）隔离设计
2. **环境噪音判定标准**：本地串行全绿 + CI 并行确定性失败 + 失败模式同质 → 判定环境问题
3. **根治优先于降级**：宁可拆串行段（保留断言）也不 `xfail`/跳过（丢护城河）
4. **CI 命令注释先行**：加 marker 时同步在 CI 命令处写 Why，避免后人误删两段结构

## 7. 关联文档

- [scripts_gate_transition_plan_20260809.md](../archive/scripts_gate_transition_plan_20260809.md)（过渡方案）
- [scripts_coverage_governance_plan_20260809.md](../archive/scripts_coverage_governance_plan_20260809.md)（长期治理）
- 根治提交：ad27fb1e
- 验证 run：31315288819 ~ 31315288868（head ad27fb1e）
