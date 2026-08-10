# 测试日志埋点指南（7 处）

> 日期：2026-08-09
> 目的：为「疑似资源竞争 / 环境敏感」的偶发失败用例添加诊断埋点，
> 下次复现时可直接从 pytest 输出定位失败根因，无需再次静态分析。
> 全部埋点仅在**失败时输出**（走 stderr，pytest 自动捕获展示），不干扰正常通过。

---

## 1. 埋点总览

| # | 文件 | 测试用例 | 埋点形式 | 定位目标 |
|---|------|---------|---------|---------|
| 1 | `tests/unit/test_ci_guard_fix_regression.py` | 4 个（run_ci_guard --json / --skip-detect / --validate / simulate pipeline） | `_run_ci_cmd` helper | 子进程被杀死 vs 业务失败 |
| 2 | `tests/regression/test_precommit_hook_blocking.py` | `test_real_git_commit_blocked_by_hook` | `_run_git` / `_run_check` helper | hook 拦截失败定位 |
| 3 | `tests/unit/test_p6_snapshot.py` | `test_performance_monitor` | 保存耗时 + 完整 summary 进断言 | 保存慢 vs 统计未记录 |
| 4 | `tests/unit/test_singleton_manager.py` | `test_metrics_modules_registered` | 失败时输出注册表全量键 | 注册名不符 vs 被清空 |

---

## 2. 子进程类埋点（#1 #2）—— 输出格式与解读

### 2.1 输出格式（统一）

```
[diag-ci]  run_ci_guard.py returncode=1 (0x00000001) elapsed=12.34s stdout_tail='...' stderr_tail='...'
[diag-git] commit returncode=1 (0x00000001) elapsed=0.50s stdout_tail='...' stderr_tail='...'
[diag-hook] returncode=1 (0x00000001) elapsed=180.00s stdout_tail='...' stderr_tail='...'
```

字段说明：
- `returncode`：十进制退出码
- `(0xXXXXXXXX)`：十六进制退出码，**Windows 崩溃码判定关键**：
  - `0x00000001` / `0x00000002` → 正常业务失败（脚本自身 sys.exit）
  - `0xC0000005` → ACCESS_VIOLATION（C 扩展崩溃，如 pyarrow/onnxruntime）
  - `0xC0000409` → STACK_BUFFER_OVERRUN（堆栈溢出/终止）
  - `0xC0000135` → DLL 加载失败
- `elapsed`：子进程耗时，**接近 timeout 上限 → 超时/挂起嫌疑**
- `stdout_tail` / `stderr_tail`：各取尾部 500 字符

### 2.2 解读规则

| 现象 | 判定 |
|------|------|
| returncode=1 且 stderr 为空 | 进程被外部终止或 `sys.exit(1)` 无输出 → 查 elapsed 是否接近 timeout；若测试并行期间发生 → 资源竞争 |
| returncode=0xC0000005 | C 扩展崩溃（内存/并发） |
| stderr 非空 | 脚本自身业务错误，直接读 stderr |
| elapsed 接近 timeout | 脚本挂起，子进程内等待（网络/锁） |

### 2.3 复现命令

```bash
# 单独复现（无并行干扰）
python -m pytest tests/unit/test_ci_guard_fix_regression.py -q --tb=long -s
# 全量复现（观察是否并行时失败）
python -m pytest tests/ --randomly-seed=12345 -q --tb=line
```

---

## 3. 状态类埋点（#3 #4）—— 输出格式与解读

### 3.1 test_performance_monitor（test_p6_snapshot.py）

埋点内容（断言消息 + 日志）：
```
INFO  保存结果: ...
INFO  性能摘要: {total_saves, last_save_ms, ...}
INFO  保存耗时: X.XXms
AssertionError: 上次保存时间应大于 0，得到: 0.0，完整摘要: {...}，保存耗时: X.XXms
```

解读：
- `保存耗时 ≈ 0ms` + `last_save_ms: 0.0` → 性能统计分支未触发（mock 了 `_save_core_modules_with_delta` 时的实现缺陷，**确定性失败**，非环境问题）
- `保存耗时 > 500ms` + `last_save_ms: 0.0` → 保存本身慢，统计未记录
- `last_save_ms > 0` 但断言仍失败 → 阈值/其他断言问题

### 3.2 test_metrics_modules_registered（test_singleton_manager.py）

埋点内容（断言消息）：
```
AssertionError: 单例未注册: auto_tuner；当前注册表: ['auto_tuner', 'error_reporter', ...]
```

解读：
- 注册表含该键 → `is_registered` 逻辑/大小写问题
- 注册表为空或缺键 → 前序测试 `reset_all_singletons` 清空了注册表（测试间污染）

---

## 4. 埋点代码位置速查

| 埋点 | 文件:行 |
|------|---------|
| `_run_ci_cmd` | `tests/unit/test_ci_guard_fix_regression.py` 模块级 helper |
| `_run_git` | `tests/regression/test_precommit_hook_blocking.py` 模块级 |
| `_run_check` | `tests/regression/test_precommit_hook_blocking.py` 模块级 |
| 性能埋点 | `tests/unit/test_p6_snapshot.py::test_performance_monitor` 测试体内 |
| 注册表埋点 | `tests/unit/test_singleton_manager.py::test_metrics_modules_registered` 测试体内 |

---

## 5. 已确认结论（埋点产出）

| 用例 | 结论 |
|------|------|
| ci_guard 4 个 | **资源竞争**（第 3 轮无并行时全部通过）；stderr 空 + returncode=1 特征 |
| precommit 1 个 | **真实环境失败**（单独运行也失败）：hook 全量预检失败，`[diag-git]` 已捕获输出 |
| p6_snapshot 1 个 | **真实失败**（单独运行也失败）：`last_save_ms: 0.0` + 保存耗时 0.00ms |
| singleton_manager 1 个 | 资源竞争/顺序相关（第 3 轮无并行时通过），埋点可区分两类根因 |
