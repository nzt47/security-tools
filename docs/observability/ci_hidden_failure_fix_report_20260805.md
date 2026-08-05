# CI 必挂隐患修复记录（2026-08-05）

> 范围：`ci-guard-runner` 全链路（run_ci_guard → detect/rollback_sim/guard_verify）的
> "未入库依赖 / .pyc 缓存陷阱 / stdout 污染"三类必挂隐患排查与修复。
> 状态：✅ 本地完整 CI 流水线模拟 ALL PASS（3 个 workflow 全绿）

---

## 一、问题背景

`run_ci_guard.py` 作为统一 CI 守卫入口，在 CI 中暴露了两类"本地假绿、CI 必挂"的隐患：

1. **未入库依赖**：`run_ci_guard.py` import 的 `simulate_pr_merge_guard` / `safe_git_revert`
   两个模块从未 `git add/commit`，仅残留 `.pyc` 编译缓存 → 本地能跑、CI 检出后必现
   `ModuleNotFoundError`。
2. **stdout 污染**：`safe_git_revert` 的 dry-run 日志直接 `print` 到 stdout，
   `run_ci_guard.py --json` 的输出流被日志行污染 → CI 中 `json.load(guard_report.json)` 解析失败。

用户要求：横向排查其他脚本是否同类问题，并出具修复记录、本地完整模拟 CI。

---

## 二、根因分析

### 2.1 未入库依赖（已修复：commit `a422a64f`）

| 模块 | 状态 | 根因 |
|------|------|------|
| `scripts/simulate_pr_merge_guard.py` | 曾缺→已入库 | 从未 `git add`，仅 `.pyc` 幸存 |
| `scripts/safe_git_revert.py` | 曾缺→已入库 | 同上 |
| `scripts/run_ci_guard.py` | 已入库 | `--validate` 分支还依赖 `ci_guard_types`（见 §2.3） |

### 2.2 stdout 污染（已修复：本次修改 `scripts/safe_git_revert.py`）

- **现象**：`python scripts/run_ci_guard.py --json` 的 stdout 首行是
  `[safe_git_revert][dry-run] 目标 commit: ...`，而非 JSON 的 `{`。
- **影响**：ci-guard-runner.yml 中 `run_ci_guard.py --json > guard_report.json` 写入的文件
  无法被 `json.load()` 解析 → CI 必挂。
- **根因**：`safe_revert(dry_run=True)` 用 `print()` 直出日志，未区分 stdout/stderr。
  `run_ci_guard.py` 依赖 stdout 纯净，但未约束子模块的打印行为（契约脆弱点）。
- **修复**：dry-run 日志改走 `sys.stderr`，stdout 保持纯净。
  验证：`redirect_stdout` 捕获为空 + `--json | json.load` 解析成功。

### 2.3 遗留依赖缺口（`ci_guard_types.py`，未修复）

- `scripts/ci_guard_types.py` 从未入库（`git log --all` 无记录），仅存在
  `scripts/__pycache__/ci_guard_types.cpython-312.pyc`（缓存陷阱）。
- `run_ci_guard.py` L92-93 的 `--validate` 分支 `from ci_guard_types import validate_report`
  实测必挂：`ModuleNotFoundError`。
- **影响评估**：当前所有 workflow（ci-guard-runner.yml 等）均未调用 `--validate`，
  **不影响现有 CI**；但任何新增 `--validate` 调用或手动执行都会失败。
- **建议**：二选一——(a) 重建 `ci_guard_types.py`（契约校验，按 run_ci_guard 输出结构实现
  `validate_report(report) -> list[str]`）；(b) 若 `--validate` 无使用方，移除该分支。
  **待团队决策，未擅自处置**。

### 2.4 .pyc 缓存陷阱横向扫描（19 项 LOST，均不影响现有 CI）

对全仓库 `__pycache__/*.pyc` 与已跟踪 `.py` 对比，发现 19 个"仅有 .pyc、无 .py 源文件"条目：

| 分类 | 文件 | 说明 |
|------|------|------|
| 包整体丢失 | `agent/knowledge/{__init__,lifecycle,schema}.py` | 包源码从未入库，`from agent.knowledge` 必挂 |
| 守卫契约 | `scripts/ci_guard_types.py` | 见 §2.3 |
| 运维脚本 | `scripts/{elk_import_logs,generate_orchestrator_demo_log,perf_regression_monitor,scan_template_miss_stats,send_semantic_logs}.py` | 从未入库 |
| 测试 | `tests/unit/{test_knowledge_lifecycle,test_knowledge_schema,test_safe_git_revert,test_security_scanner_malicious_skill,test_zzz_chromadb_import_probe}.py` | 从未入库 |
| 集成测试 | `tests/integration/{test_core_invariants,test_precommit_hook_ci_guard,test_safe_git_revert_integration}.py` | 从未入库 |
| 历史删除 | `tests/unit/test_scan_sensitive_data.py` | `LOST[HISTORY]`：曾有历史被删，非本次范围 |

> 注意：这些 .pyc 是本地历史遗留编译缓存，**未被任何现有 workflow 引用**，
> 不会导致当前 CI 挂；但 `agent/knowledge/` 若未来被 import 则必挂。
> `security-tools/` 是独立嵌套仓库副本（.gitignore L190-191 明确忽略），**不处理**。

---

## 三、修复与验证记录

### 3.1 修复动作

| 动作 | 内容 |
|------|------|
| commit `a422a64f` | 重建并入库 `simulate_pr_merge_guard.py` / `safe_git_revert.py` |
| 本次修改（未提交） | `safe_git_revert.py` dry-run 日志 stdout→stderr |
| 临时诊断脚本 | `scripts/_scan_missing_deps.py` / `_simulate_ci_pipeline.py`（已转正：`scan_missing_deps.py` / `simulate_ci_guard_pipeline.py`） |

### 3.2 本地完整 CI 流水线模拟（3 个 workflow）

| workflow | 步骤 | 结果 |
|----------|------|------|
| ci-guard-runner | `run_ci_guard.py --json`（detect/rollback_sim/guard_verify） | ✅ exit=0，JSON 纯净可解析 |
| reranker-timeout-guard | verify 6 场景 + pytest 9 用例 | ✅ exit=0 ×2 |
| core-invariants-guard | `verify_core_invariants.py --json` | ✅ exit=0（12 项不变量全 PASS） |

复现命令：

```bash
python scripts/simulate_ci_guard_pipeline.py   # 汇总 ALL PASS
python scripts/run_ci_guard.py --json 2>$null | python -c "import json,sys;d=json.load(sys.stdin);print(d['overall'])"
```

---

## 四、预防建议

1. **stdout 纯净契约**：所有供 `--json` 消费的库函数，日志一律走 `sys.stderr`；
   CI 侧增加自检：`python scripts/run_ci_guard.py --json | python -c "import json,sys;json.load(sys.stdin)"`。
2. **未入库依赖守卫**：pre-commit / CI 增加"workflow 引用的 `.py` 全集必须入库"检查
   （可用 `git ls-tree HEAD` 对比 workflow `paths:`/`run:` 引用）。
3. **.pyc 缓存陷阱巡检**：定期用 `scripts/_scan_missing_deps.py`（可转正入库）扫描
   `__pycache__` 中无对应 `.py` 的模块，发现即补充入库或清理缓存。
4. **CI 环境差异**：本地 PowerShell `>` 重定向为 UTF-16，与 CI(bash) 的 UTF-8 不同，
   本地验证 JSON 输出必须用 Python 内部写文件或直接管道，勿用 PS 重定向判真伪。
5. **决策遗留项**：`ci_guard_types.py` 重建或移除 `--validate` 分支（§2.3），
   `agent/knowledge/` 包源码补齐或确认废弃。

---

## 五、遗留清单（待决策）

- [x] `scripts/ci_guard_types.py`：已按 run_ci_guard 输出契约重建并入库（`--validate` 分支已验证通过）
- [ ] `agent/knowledge/` 三个源文件：恢复 / 确认废弃
- [ ] 其余 13 项 LOST 脚本与测试：恢复 / 清理 .pyc 归档
- [x] `scripts/_scan_missing_deps.py` / `_simulate_ci_pipeline.py`：已转正为 `scan_missing_deps.py` / `simulate_ci_guard_pipeline.py` 入库
- [x] safe_git_revert stdout 修复：已提交（commit `e859f22e`）
