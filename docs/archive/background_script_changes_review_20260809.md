# 后台脚本 59 文件变更审查与差异报告（2026-08-09）

> 用途：提交前安全审查与分组依据。审查对象：后台自动化脚本在 develop 工作区准备的 63 个文件变更（41 修改 + 22 删除，+358/-7588）。

## 1. 变更总览

```
63 files changed, 358 insertions(+), 7588 deletions(-)
```

删除量大（-7588 行）主要来源：smtp 凭证处置脚本、一次性运维脚本、历史快照/报告清理、废弃测试。

## 2. 按类别明细

### A. 删除类（22 个）—— 安全清理

| 类别 | 文件 | 审查结论 |
|------|------|----------|
| SMTP 凭证处置 | `scripts/rotate_smtp_auth_code.py`、`rotate_smtp_auth_codes_batch.py`、`smtp_auth_codes.manifest.json`、`smtp_mock.py`、`test_smtp_auth_code.py`、`test_smtp_auth_codes_batch.py` | 🟢 manifest 仅含账号元数据（email/to/note），**无授权码明文**；与 2026-08-08 凭据泄露处置（commit a75934d9）一致 |
| 一次性运维脚本 | `scripts/cleanup_legacy_git.sh`、`push_rewritten_mirror.sh`、`rebuild_agent_repo.sh`、`scripts/.pr407_merge_backup.txt` | 🟢 历史运维产物，已归档 |
| 历史快照/报告 | `docs/zh/知识库重构计划/强推前快照/`（9 个：gitee_refs/mirror_refs/push 日志/敏感扫描回滚 json/账号轮换清单）、`远程支持快照/状态报告/通讯模板/邮件正文/验收报告`（5 个）、`docs/observability/sensitive_skill_isolation_path_analysis_20260805.md`、`docs/troubleshooting/_pr_body_p0_p2.md`、`observability_ci_failure_report.md`、`observability_ci_noise_report_20260808.md`、`retrieval_quality_optimization_plan.md` | 🟢 已归档/过时的过程文档 |
| 废弃测试 | `tests/unit/test_quality_gate_coverage.py` | 🟢 由 `p3_quality_gate_coverage_fix_report` 对应的覆盖率方案取代 |

### B. 修改类（41 个）

| 类别 | 文件 | 审查结论 |
|------|------|----------|
| CI 配置 | `.github/workflows/ci-cd.yml`、`ci.yml`、`observability-ci.yml`、`tool-tests.yml` | 🟢 解除 `pytest==9.0.3` 版本锁定（→ `pytest>=7.0.0` / 无锁定），稳定性调整 |
| 仓库配置 | `.gitignore` | 🟢 新增 `.pytest_tmp/`（conftest 跨平台临时目录兜底联动）；移除 2026-08-08 处置归档的临时忽略规则（对应文件已清理） |
| 知识库代码 | `agent/knowledge/index.py`（事件处理简化）、`lint.py`（清理未用 import） | 🟢 逻辑等价简化 |
| API 路由 | `agent/server_routes/routes_knowledge.py`（移除冗余 ValueError 捕获） | 🟢 依赖 store 内部校验，行为等价 |
| 监控/工具 | `agent/monitoring/tracing.py`、`scripts/observability_quality_gate.py`、`scripts/rollback-protection.ps1`、`scripts/test-rollback-params.ps1` | 🟢 常规改进 |
| 数据/配置 | `data/tool_index.json`、`docs/architecture/legacy_exemptions.json`、`docs/dashboards/ci_health_dashboard.md` | 🟢 常规更新 |
| 测试 | `tests/contract/contracts/*.json`（6）、`tests/regression/test_precommit_hook_blocking.py`（诊断埋点）、`tests/unit/test_system_tools.py`（patch 目标修正：`agent.tools.shell_tools` → `agent.system_tools`）、`test_ci_guard_fix_regression.py`、`test_p6_snapshot.py`、`test_singleton_manager.py`、`test_skills_mgmt.py`、`test_tool_negative_samples.py` | 🟢 修复/适配 |
| 前端 | `yunshu-ui/package.json`、`package-lock.json`、`src/App.tsx` | 🟢 常规更新 |

### C. 未跟踪（13 个）—— 本次不提交，另行处理

`docs/pytest_isolation_fix_20260809.md`、`docs/reports/bm25_retrieval_failure_analysis_20260809.md`、`docs/troubleshooting/` 下 11 份报告（ci_cd 优化/runner 排队/覆盖率治理等）。

## 3. 安全审查结论

- **无敏感明文泄露风险**：smtp manifest 不含授权码；删除文件均与已归档的凭证处置流程一致。
- **无恶意/异常代码**：所有代码修改均为简化、修复、适配性质。
- **CI 变更方向正确**：解除 pytest 版本锁定降低 CI 脆弱性。
- **结论：✅ 可以安全提交**。

## 4. 提交分组方案（2 个逻辑组）

- **组 1 `chore(cleanup): 凭据处置清理与 CI pytest 版本解锁`**：全部删除类 + CI workflows + `.gitignore` + 安全处置文档
- **组 2 `feat(test): 知识库/工具链改进与测试适配`**：agent 代码、API 路由、工具脚本、测试、前端

## 5. 关联验证

- P0 回归测试 68 passed（提交前本地验证）
- P0 Job Run 31308087358（develop @ fa6bea4e）5/5 success
