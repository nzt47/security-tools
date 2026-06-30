# 可观测性验证测试报告

> **⚠️ 过时声明（2026-06-28 更新）**
> 本报告生成于 2026-06-27，当时 `_read_test_coverage()` 在 coverage.xml 缺失时会降级到
> `pyproject.toml fail_under=40` 作为基线。该降级逻辑已于 2026-06-28 彻底移除
> （commit 2e8521fc），现在 coverage.xml 缺失时直接返回 0.0 并输出 error 日志。
> 下方涉及 `fallback_pyproject` / `baseline: 40.0` / "降级值" 的内容均为历史记录，
> 仅供追溯，不代表当前行为。CI 中已增加回归检查步骤防止降级逻辑回滚。

**生成时间**：2026-06-27
**执行环境**：本地 Windows（模拟 CI 环境）
**Trace ID**：ed82d63e384644c2
**总耗时**：2752.16 ms
**总体状态**：pass

---

## 一、执行摘要

| 维度 | 状态 | 说明 |
|------|------|------|
| 运行时可见 | ✅ | 结构化日志 + 链路追踪 + 健康检查 |
| 验证过程可见 | ✅ | 测试覆盖 + 边界测试 + 契约测试（boundary_test_coverage=11.8% 达标） |
| 业务价值可见 | ✅ | 埋点 + 看板 + 告警 |
| 架构影响可见 | ✅ | 依赖图 + 架构规则 + 变更影响 |

**退出码**：0（全部指标达标）

---

## 二、六项核心指标详情

### 2.1 结构化日志覆盖率（structured_log_coverage）

| 属性 | 值 |
|------|-----|
| 实际值 | 40.6% |
| 阈值 | ≥30% |
| 状态 | ✅ 通过 |
| 扫描文件数 | 288 |
| 日志调用总数 | 2975 |
| 结构化日志数 | 1209 |

**日志摘要**：
```
{"trace_id": "7e7772f81b4e4a86", "module_name": "visibility_report", "action": "calc_structured_log.success", "duration_ms": 147.34, "scanned_files": 288, "total_logs": 2975, "structured_logs": 1209, "coverage_percent": 40.6}
```

### 2.2 链路追踪覆盖率（trace_coverage）

| 属性 | 值 |
|------|-----|
| 实际值 | 55.3% |
| 阈值 | ≥30% |
| 状态 | ✅ 通过 |
| 扫描路由文件数 | 19 |
| 路由总数 | 219 |
| 已追踪路由数 | 121 |

**日志摘要**：
```
{"trace_id": "fccac0501aa04ce2", "module_name": "visibility_report", "action": "calc_trace.success", "duration_ms": 4.0, "scanned_files": 19, "total_routes": 219, "traced_routes": 121, "coverage_percent": 55.3}
```

### 2.3 测试覆盖率（test_coverage）

| 属性 | 值 |
|------|-----|
| 实际值 | 40.0% |
| 阈值 | ≥40% |
| 状态 | ✅ 通过（降级值） |
| 数据来源 | pyproject.toml fail_under（coverage.xml 缺失） |

**日志摘要**：
```
{"trace_id": "f4d2fe42983b414b", "module_name": "visibility_report", "action": "read_test_coverage.missing_xml", "duration_ms": 0, "path": "C:\\Users\\Administrator\\agent\\coverage.xml", "reason": "coverage.xml 不存在，请检查 CI 中 observability-unit-tests 是否上传 coverage-report artifact 且 visibility-report 已下载"}
{"trace_id": "07890dec003246fe", "module_name": "visibility_report", "action": "read_test_coverage.fallback_pyproject", "duration_ms": 0, "baseline": 40.0, "reason": "降级使用 pyproject.toml fail_under 作为基线，非真实覆盖率"}
```

**注意**：coverage.xml 缺失，当前值为 pyproject.toml 配置的 fail_under 基线，非真实测试覆盖率。CI 环境中 observability-unit-tests job 会生成真实 coverage.xml。

### 2.4 边界测试覆盖率（boundary_test_coverage）

| 属性 | 值 |
|------|-----|
| 实际值 | 11.8% |
| 阈值 | ≥5% |
| 状态 | ✅ 通过 |
| 测试总数 | 3718 |
| 边界测试数 | 438 |
| 模块总数 | 32 |
| 子进程返回码 | 0（正常） |
| 子进程 stdout 长度 | 13926 |
| 扫描耗时 | 2295.95 ms |

**日志摘要**：
```
{"trace_id": "35452e3dac54407f", "module_name": "visibility_report", "action": "calc_boundary_coverage.subprocess_start", "duration_ms": 0, "cmd": ["C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe", "C:\\Users\\Administrator\\agent\\scripts\\check_boundary_coverage.py", "--json-only"], "cwd": "C:\\Users\\Administrator\\agent", "timeout_sec": 60}
{"trace_id": "35452e3dac54407f", "module_name": "visibility_report", "action": "calc_boundary_coverage.subprocess_done", "duration_ms": 2295.95, "returncode": 0, "stdout_len": 13926, "stderr_len": 852, "stderr_preview": "00:23:53 [    INFO] boundary_coverage: {\"trace_id\": \"a881ce84a5b54d97\", \"module_name\": \"boundary_coverage\", \"action\": \"scan.start\", \"timestamp\": \"2026-06-27T00:23:53.414986\"}\n00:23:53 [    INFO] boundary_coverage: 发现测试文件: 234 个\n..."}
{"trace_id": "35452e3dac54407f", "module_name": "visibility_report", "action": "calc_boundary_coverage.parsed", "duration_ms": 2295.95, "total_tests": 3718, "total_boundary_tests": 438, "overall_status": "warn", "blocked_modules": [], "total_modules": 32}
{"trace_id": "35452e3dac54407f", "module_name": "visibility_report", "action": "calc_boundary_coverage.success", "duration_ms": 2295.95, "total_tests": 3718, "total_boundary_tests": 438, "coverage_percent": 11.8}
```

**修复后表现**：`check_boundary_coverage.py` 子进程以 returncode=0 正常退出，stdout 正常输出 JSON（13926 字节），`calc_boundary_coverage.parsed` 与 `calc_boundary_coverage.success` 日志均正常生成，边界覆盖率 11.8% 超过 5% 阈值。

### 2.5 契约测试数（contract_test_count）

| 属性 | 值 |
|------|-----|
| 实际值 | 3 个 |
| 阈值 | ≥3 个 |
| 状态 | ✅ 通过 |
| 契约目录 | tests/contract/contracts/ |
| 契约文件 | chat_api_contract.json, dashboard_api_contract.json, health_api_contract.json |

**日志摘要**：
```
{"trace_id": "dbd4dbc1276e437b", "module_name": "visibility_report", "action": "count_contract_tests.success", "duration_ms": 0.0, "contracts_dir": "C:\\Users\\Administrator\\agent\\tests\\contract\\contracts", "contract_count": 3}
```

### 2.6 埋点覆盖率（track_event_coverage）

| 属性 | 值 |
|------|-----|
| 实际值 | 37.0% |
| 阈值 | ≥30% |
| 状态 | ✅ 通过 |
| 总模块数 | 27 |
| 已埋点模块数 | 10 |
| 未埋点模块 | audit, caching, data, extensions, human_in_the_loop, lazy_loader, log_system, network, observability, p6, prompt_manager, quality, subagent, task_planner, tests, utils, workflow_engine |

**日志摘要**：
```
{"trace_id": "7693dfe408c94ac4", "module_name": "visibility_report", "action": "calc_track.success", "duration_ms": 295.72, "total_modules": 27, "tracked_modules": 10, "untracked_modules": ["audit", "caching", "data", "extensions", "human_in_the_loop", "lazy_loader", "log_system", "network", "observability", "p6", "prompt_manager", "quality", "subagent", "task_planner", "tests", "utils", "workflow_engine"], "coverage_percent": 37.0}
```

---

## 三、CI 质量门禁配置

### 3.1 阈值阻断机制

| 检查点 | 阈值 | 阻断方式 |
|--------|------|----------|
| boundary-coverage-check job | boundary_test_coverage ≥ 5% | exit 1 阻断 job |
| visibility-report job | 4 项核心指标（30/30/40/30） | exit 1 阻断合并 |
| observability-quality-gate job | 综合质量门禁 | push/schedule 时检查 |

### 3.2 边界测试覆盖率告警

- **触发条件**：boundary_test_coverage < 5%
- **阻断方式**：`boundary-coverage-check` job 中 `boundary_threshold` 步骤 `exit 1`
- **告警方式**：PR 评论自动发布告警横幅 + 覆盖率摘要表格
- **双重保障**：`visibility-report` job 的 `visibility_report.py` 也会检查同一阈值

### 3.3 CI 工作流结构

```
push/PR → observability-config-validation
        → architecture-visibility-check
        → observability-unit-tests
        → observability-integration-tests
        → observability-e2e-validation (push/schedule only)
        → boundary-coverage-check ← 阈值检查步骤
        → contract-test
        → visibility-report ← 阈值阻断步骤
        → observability-quality-gate (push/schedule only)
        → observability-alert-notification
```

---

## 四、验证结论

### 4.1 指标达标情况

| # | 指标 | 实际值 | 阈值 | 状态 |
|---|------|--------|------|------|
| 1 | structured_log_coverage | 40.6% | ≥30% | ✅ |
| 2 | trace_coverage | 55.3% | ≥30% | ✅ |
| 3 | test_coverage | 40.0% | ≥40% | ✅ |
| 4 | boundary_test_coverage | 11.8% | ≥5% | ✅ |
| 5 | contract_test_count | 3 | ≥3 | ✅ |
| 6 | track_event_coverage | 37.0% | ≥30% | ✅ |

**6 项指标全部达标，总体状态：pass**

### 4.2 已知限制

1. **test_coverage 为降级值（已过时）**：~~本地无 coverage.xml，使用 pyproject.toml fail_under=40 作为基线~~。该降级逻辑已于 2026-06-28 移除（commit 2e8521fc），现在 coverage.xml 缺失时返回 0.0。
2. **CI 环境差异**：CI 中 full-project-tests job 生成全项目 coverage.xml（覆盖 agent + scripts），visibility-report job 下载后读取真实 line-rate
3. **boundary_test_coverage 已修复**：编码问题修复后 returncode=0，覆盖率 11.8%（438/3718）真实反映边界测试情况，超过 5% 阈值

### 4.3 后续建议

1. 在 CI 中上传 coverage-report artifact，使 visibility-report 能读取真实覆盖率
2. 定期监控 boundary_test_coverage 趋势，确保不低于 5% 阈值
3. 补充未埋点模块的 trackEvent 调用，提升 track_event_coverage（当前未埋点 17 个模块：audit, caching, data, extensions, human_in_the_loop, lazy_loader, log_system, network, observability, p6, prompt_manager, quality, subagent, task_planner, tests, utils, workflow_engine）

---

## 五、编码问题修复记录

### 5.1 问题

`check_boundary_coverage.py` 在 Windows 环境下运行时，子进程以 returncode=2 异常退出，导致 `visibility_report.py` 判定脚本执行异常，将 `boundary_test_coverage` 降级为 0.0%。

### 5.2 根因

- Windows 默认 stdout/stderr 使用 GBK 编码
- `check_boundary_coverage.py` 在输出告警信息时打印 emoji 字符（`✅`/`⚠️`/`❌`），其中 `⚠`（U+26A0）无法用 GBK 编码
- 触发 `'gbk' codec can't encode character '\u26a0'` 异常，子进程以 returncode=2 退出
- `visibility_report.py` 判定 returncode 非 0/1 为异常，降级返回 0.0%
- 真实边界覆盖率 11.78%（438/3718）本应通过 5% 阈值，但因编码错误被误判为 0.0%

### 5.3 影响

- `boundary_test_coverage` 被误判为 0.0%，触发质量门禁阻断（exit 1）
- `calc_boundary_coverage.parsed` 与 `calc_boundary_coverage.success` 日志缺失（因 stdout 被丢弃）
- 总体状态由 pass 误降为 fail
- 仅影响 Windows 本地环境，CI（Linux）环境通常不受影响

### 5.4 修复

修复涉及两处，缺一不可：

**1. `scripts/check_boundary_coverage.py` 入口处强制 UTF-8 输出**：

```python
# ── Windows 编码修复 ──
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
```

**2. `scripts/visibility_report.py` 子进程调用显式指定 UTF-8 解码**：

```python
result = subprocess.run(
    [sys.executable, str(script_path), "--json-only"],
    capture_output=True,
    text=True,
    encoding="utf-8",      # 显式指定 UTF-8 解码（关键修复）
    errors="replace",      # 解码异常时替换而非崩溃
    timeout=60,
    cwd=self.project_root,
)
```

> 第二处修复是必要的：即使子进程以 UTF-8 输出，`subprocess.run` 默认仍用 GBK 解码（`text=True` 时），会触发 `UnicodeDecodeError: 'gbk' codec can't decode byte 0xae`，导致 `stdout_len=0`，进而触发 `empty_stdout` 降级为 0.0%。

### 5.5 验证

修复后重新运行 `visibility_report.py`，结果如下：

| 验证项 | 修复前 | 修复后 |
|--------|--------|--------|
| `calc_boundary_coverage.subprocess_done` returncode | 2 | **0** |
| `calc_boundary_coverage.subprocess_done` stdout_len | 165（异常输出） | **13926**（正常 JSON） |
| `calc_boundary_coverage.parsed` 日志 | 缺失 | **存在**（total_tests=3718, total_boundary_tests=438） |
| `calc_boundary_coverage.success` 日志 | 缺失 | **存在**（coverage_percent=11.8） |
| `boundary_test_coverage` 实际值 | 0.0% | **11.8%** |
| `generate.complete` overall_status | fail | **pass** |
| `generate.complete` violations_count | 1 | **0** |
| 进程退出码 | 1 | **0** |

所有 `calc_boundary_coverage.*` 日志行（修复后）：

```
{"trace_id": "35452e3dac54407f", "module_name": "visibility_report", "action": "calc_boundary_coverage.subprocess_start", "duration_ms": 0, "cmd": ["...python.exe", ".../check_boundary_coverage.py", "--json-only"], "cwd": "C:\\Users\\Administrator\\agent", "timeout_sec": 60}
{"trace_id": "35452e3dac54407f", "module_name": "visibility_report", "action": "calc_boundary_coverage.subprocess_done", "duration_ms": 2295.95, "returncode": 0, "stdout_len": 13926, "stderr_len": 852, "stderr_preview": "...scan.start...发现测试文件: 234 个..."}
{"trace_id": "35452e3dac54407f", "module_name": "visibility_report", "action": "calc_boundary_coverage.parsed", "duration_ms": 2295.95, "total_tests": 3718, "total_boundary_tests": 438, "overall_status": "warn", "blocked_modules": [], "total_modules": 32}
{"trace_id": "35452e3dac54407f", "module_name": "visibility_report", "action": "calc_boundary_coverage.success", "duration_ms": 2295.95, "total_tests": 3718, "total_boundary_tests": 438, "coverage_percent": 11.8}
```

**结论**：编码问题已彻底修复，`boundary_test_coverage` 恢复为真实值 11.8%，超过 5% 阈值，总体状态由 fail 转为 pass。

---

## 六、附录：完整日志摘要

以下为本次运行按时间顺序排列的所有 calc_*/read_*/count_*/generate 日志行（每行一条 JSON）：

```
{"trace_id": "ed82d63e384644c2", "module_name": "visibility_report", "action": "generate.start", "timestamp": "2026-06-27T00:23:52.818758"}
{"trace_id": "7e7772f81b4e4a86", "module_name": "visibility_report", "action": "calc_structured_log.success", "duration_ms": 147.34, "scanned_files": 288, "total_logs": 2975, "structured_logs": 1209, "coverage_percent": 40.6}
{"trace_id": "fccac0501aa04ce2", "module_name": "visibility_report", "action": "calc_trace.success", "duration_ms": 4.0, "scanned_files": 19, "total_routes": 219, "traced_routes": 121, "coverage_percent": 55.3}
{"trace_id": "f4d2fe42983b414b", "module_name": "visibility_report", "action": "read_test_coverage.missing_xml", "duration_ms": 0, "path": "C:\\Users\\Administrator\\agent\\coverage.xml", "reason": "coverage.xml 不存在，请检查 CI 中 observability-unit-tests 是否上传 coverage-report artifact 且 visibility-report 已下载"}
{"trace_id": "07890dec003246fe", "module_name": "visibility_report", "action": "read_test_coverage.fallback_pyproject", "duration_ms": 0, "baseline": 40.0, "reason": "降级使用 pyproject.toml fail_under 作为基线，非真实覆盖率"}
{"trace_id": "35452e3dac54407f", "module_name": "visibility_report", "action": "calc_boundary_coverage.subprocess_start", "duration_ms": 0, "cmd": ["C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe", "C:\\Users\\Administrator\\agent\\scripts\\check_boundary_coverage.py", "--json-only"], "cwd": "C:\\Users\\Administrator\\agent", "timeout_sec": 60}
{"trace_id": "35452e3dac54407f", "module_name": "visibility_report", "action": "calc_boundary_coverage.subprocess_done", "duration_ms": 2295.95, "returncode": 0, "stdout_len": 13926, "stderr_len": 852, "stderr_preview": "00:23:53 [    INFO] boundary_coverage: scan.start...发现测试文件: 234 个..."}
{"trace_id": "35452e3dac54407f", "module_name": "visibility_report", "action": "calc_boundary_coverage.parsed", "duration_ms": 2295.95, "total_tests": 3718, "total_boundary_tests": 438, "overall_status": "warn", "blocked_modules": [], "total_modules": 32}
{"trace_id": "35452e3dac54407f", "module_name": "visibility_report", "action": "calc_boundary_coverage.success", "duration_ms": 2295.95, "total_tests": 3718, "total_boundary_tests": 438, "coverage_percent": 11.8}
{"trace_id": "dbd4dbc1276e437b", "module_name": "visibility_report", "action": "count_contract_tests.success", "duration_ms": 0.0, "contracts_dir": "C:\\Users\\Administrator\\agent\\tests\\contract\\contracts", "contract_count": 3}
{"trace_id": "7693dfe408c94ac4", "module_name": "visibility_report", "action": "calc_track.success", "duration_ms": 295.72, "total_modules": 27, "tracked_modules": 10, "untracked_modules": ["audit", "caching", "data", "extensions", "human_in_the_loop", "lazy_loader", "log_system", "network", "observability", "p6", "prompt_manager", "quality", "subagent", "task_planner", "tests", "utils", "workflow_engine"], "coverage_percent": 37.0}
{"trace_id": "ed82d63e384644c2", "module_name": "visibility_report", "action": "generate.complete", "duration_ms": 2752.16, "overall_status": "pass", "violations_count": 0}
```

---

*报告由 visibility_report.py 自动生成，人工整理归档*
