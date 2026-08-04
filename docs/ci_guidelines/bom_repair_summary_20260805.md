# BOM 修复与 Hook 集成最终总结报告

> **报告目的**：汇总 2026-08-05 批次 BOM 修复与 pre-commit 钩子集成工作，
> 供团队存档与后续维护参考。
> **适用范围**：本仓库及所有使用 `sync_precommit_hook.ps1` 部署 Hook 的仓库。
> **文档版本**：v1.0 | **更新日期**：2026-08-05
> **关联文档**：[Git Hook 与 BOM 排查避坑指南](git_hook_bom_guide.md) | [新成员上手指南](new_member_onboarding.md)

---

## 一、背景与目标

- **背景**：其他进程/工具批量写入 `.ps1`/`.psm1` 时反复引入叠加 BOM（EF BB BF 连续 ≥2 次），
  且仓库历史遗留 42 个无 BOM 的 PS 文件（PS 5.1 中文系统按 GBK 解码会乱码/解析失败）。
- **目标**：全仓库 PS 文件达到「恰好 1 个 BOM」的编码契约；pre-commit 在提交前
  自动检测 BOM 异常并阻止提交。
- **成果**：BLOCK 0 / WARN 0（全仓库符合契约）；hook 新增 BOMFIX 段，拦截验证通过。

---

## 二、修复文件清单（42 个，补 BOM）

### 2.1 scripts/ 下（38 个）

| 文件 | 修复动作 |
|------|---------|
| `AdminDependencyChecker.psm1` | 补 BOM |
| `bfg_force_push.ps1` | 补 BOM |
| `cleanup_io_cache.ps1` | 补 BOM |
| `cleanup_temp_files.ps1` | 补 BOM |
| `Copy-AdminModule.ps1` | 补 BOM |
| `deploy_pages_fast.ps1` | 补 BOM |
| `deploy_tlm_overview.ps1` | 补 BOM |
| `dev/apply_nodejs_ci_optimization.ps1` | 补 BOM |
| `dev/verify_config_regression.ps1` | 补 BOM |
| `fix_prometheus_remote_write.ps1` | 补 BOM |
| `install_kind.ps1` | 补 BOM |
| `monitor_dashboard_update.ps1` | 补 BOM |
| `reload_prometheus_rules.ps1` | 补 BOM |
| `rollback-protection.ps1` | 补 BOM |
| `rollback.ps1` | 补 BOM |
| `rollback_prometheus_rules.ps1` | 补 BOM |
| `rollback_reranker.ps1` | 补 BOM |
| `rotate_grafana_password.ps1` | 补 BOM |
| `run_chaos_regression.ps1` | 补 BOM |
| `run_full_loadtest.ps1` | 补 BOM |
| `run_hpa_scale_test.ps1` | 补 BOM |
| `run_l3_regression_tests.ps1` | 补 BOM |
| `run_tests_local.ps1` | 补 BOM |
| `schedule_backup_cleanup.ps1` | 补 BOM |
| `set_bm25_weight.ps1` | 补 BOM |
| `setup_scheduled_task.ps1` | 补 BOM |
| `simulate-ci-admin-check.ps1` | 补 BOM |
| `simulate-ci-rollback-test.ps1` | 补 BOM |
| `start_k8s_cluster.ps1` | 补 BOM |
| `stop_mock_service.ps1` | 补 BOM |
| `test-rollback-params.ps1` | 补 BOM |
| `test_networkpolicy_kind.ps1` | 补 BOM |
| `trigger_workflow_dispatch.ps1` | 补 BOM |
| `unblock_ci_and_trigger_dashboard.ps1` | 补 BOM |
| `upgrade_v1.2.0_to_v1.3.0.ps1` | 补 BOM |
| `verify_k8s_v1.2.ps1` | 补 BOM |
| `verify_key_revocation.ps1` | 补 BOM |
| `verify_monitoring_setup.ps1` | 补 BOM |

### 2.2 packages/ 下（4 个）

| 文件 | 修复动作 |
|------|---------|
| `l2_p99_monitor/scripts/release.ps1` | 补 BOM |
| `tlm-hook-failsafe/publish-to-local-repo.ps1` | 补 BOM |
| `tlm-hook-failsafe/publish-to-psgallery.ps1` | 补 BOM |
| `tlm-hook-failsafe/tests/test_team_integration_e2e.ps1` | 补 BOM |

> 另修复 1 个叠加 BOM 文件：`rollback_cicd_metrics.ps1`（叠加 BOM x4 → x1，2026-08-04 已提交）。

---

## 三、Hook 配置细节

### 3.1 pre-commit 钩子段结构（bash，部署于 `.git/hooks/pre-commit`）

| 段 | 调用 | 职责 | 豁免开关 |
|----|------|------|---------|
| 链接预检 | `git_precommit_check.ps1` | Markdown 链接 + 锚点回归（阻塞） | `--no-verify` |
| ENCODING_CHECK | `check_ps1_encoding.py --quiet --repo-root …` | 非法 UTF-8 / 叠加 BOM / 关键文件缺 BOM → BLOCK | `SKIP_ENCODING_CHECK=1` |
| BOMFIX | `fix_ps_bom.py --check --quiet --repo-root …` | 叠加 BOM / 关键文件缺 BOM → 阻止提交 | `SKIP_BOM_FIX_CHECK=1` |
| CI_GUARD | `simulate_ci_guard_failure.py --assert-allowed` | CI 判定链复判 | `SKIP_CI_GUARD=1` |
| INVARIANT | `verify_core_invariants.py --quiet --repo-root …` | 12 项核心不变量静态校验 | `SKIP_INVARIANT=1` |

- **顺序**：链接预检 → ENCODING_CHECK → BOMFIX → CI_GUARD → INVARIANT。
- **跨仓库安全**：所有段均为「脚本存在才执行」，脚本缺失时静默跳过。
- **pre-push 钩子**：仅 INVARIANT 段（push 前校验核心不变量）。

### 3.2 关键契约文件（缺 BOM → BLOCK）

```text
scripts/dev/hook_fail_safe.psm1
```

可用 `check_ps1_encoding.py --require-bom <path>` 或 `fix_ps_bom.py` 的
`REQUIRE_BOM_DEFAULT` 追加。

### 3.3 部署与同步

```powershell
# 部署 / 批量同步 / 查看状态 / 预览
.\scripts\dev\sync_precommit_hook.ps1 -Install .
.\scripts\dev\sync_precommit_hook.ps1 -Sync -ScanRoot D:\code
.\scripts\dev\sync_precommit_hook.ps1 -Status
.\scripts\dev\sync_precommit_hook.ps1 -DryRun
```

- 每次部署自动备份 `.git/hooks/pre-commit.bak.<时间戳>`。
- post-commit 自动执行 `sync-from-source`：把 `scripts/dev/hook_fail_safe.psm1`
  同步到 `packages/tlm-hook-failsafe/` 并校验 16 个导出函数 hash。

---

## 四、后续维护建议

1. **每次批量写入后扫描**：其他进程/工具改完 PS 文件后，
   `python scripts/check_ps1_encoding.py --repo-root . --quiet`
   （BLOCK/WARN 分级；异常时 `fix_ps_bom.py --apply` 一键修复）。
2. **新增 PS 文件规范**：`.ps1`/`.psm1` 必须恰好 1 个 BOM（UTF-8 with BOM 保存）；
   新增关键契约文件时同步更新 `--require-bom` 或 `REQUIRE_BOM_DEFAULT`。
3. **编辑 psm1 后复查 BOM**：IDE/Edit 保存可能剥 BOM，改后验证
   `head -c3 file | xxd` 为 `ef bb bf`（见避坑指南 3.1 扫描命令）。
4. **防无痕回滚**：`verify_core_invariants.py` 12 项不变量为静态锁；
   发现回滚先查提交历史，再按 `docs/observability/rollback_recovery_report.md` 恢复。
5. **CI 同源**：CI `docs-precheck-tests` 与本地 hook 共用同一判定链
   （`-BomDiag -JsonOutput`），BOM 边缘问题在 PR 阶段即暴露。
6. **演示回归**：`docs/ci_guidelines/assets/bomdiag_pr_demo.gif` 与
   `tests/integration/test_core_invariants.py`（14 用例）可在改动后回归验证。
