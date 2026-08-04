# BOM 修复与失效链接清理总结报告（2026-08-03）

> 范围：develop 分支 PowerShell 脚本 BOM 污染修复 + 文档失效链接清理
> 关联：Issue 模板 [issue-template-broken-links-20260803.md](../issues/issue-template-broken-links-20260803.md)

---

## 一、背景

develop 分支提交历史中，一批 PowerShell 脚本（.ps1/.psm1/.psd1）首行被叠加多个 BOM 字符（U+FEFF）。PowerShell 5.1 解析多 BOM 前缀报 `ParserError`，导致：

- 本地 pre-commit hook 体系（tlm-hook-failsafe）无法运行
- CI 的 hook-failsafe E2E（PS 5.1/7 矩阵）契约测试失败
- 云枢测试流程依赖安装失败（另见 pythoncom 依赖问题）

## 二、BOM 修复明细

### 2.1 第一轮：12 个 .ps1（commit 4db85572）

全量扫描 94 个 .ps1 定位，修复多 BOM 前缀：

- `packages/tlm-hook-failsafe/`：install.ps1、sync-from-source.ps1、tests/test_exit_code_resolution.ps1、tests/test_install.ps1、tests/test_multi_repo_sync.ps1
- `scripts/dev/`：simulate_batch_sync.ps1、simulate_real_deploy.ps1、sync_precommit_hook.ps1、test_hook_chinese_path.ps1、test_hook_chinese_path_boundary.ps1、_test_permission.ps1
- `scripts/rollback_cicd_metrics.ps1`

### 2.2 第二轮：2 个 .psm1/.psd1（commit 81778b0a）

首轮只扫描 `.ps1`，遗漏 `.psm1/.psd1` 扩展名。tlm-hook-failsafe E2E 暴露：

- `scripts/dev/hook_fail_safe.psm1`（**7 重 BOM**）→ sync 后模块导入失败 → 15 函数契约 missing
- `packages/tlm-hook-failsafe/tlm-hook-failsafe.psd1`（7 重 BOM）

**教训**：BOM 全量扫描必须覆盖 .ps1/.psm1/.psd1 全部 PowerShell 扩展名，否则契约测试在 CI 兜底暴露。

## 三、失效链接清理

| 分支 | 位置 | 处理 |
|------|------|------|
| master（3 处，随 a16fa4fb 工作区保留） | semantic_monitoring_runbook.md 锚点 | 移除失效锚点，改纯文件链接 |
| master（2 处，同上） | incident_report_template.md 占位符 | 改反引号代码形式 |
| develop（4 处，commit 950a41c5） | DEVELOPMENT_STANDARDS_K8S_SCRIPTS.md ×2、HPA_CHANGELOG.md、MIGRATION_PORT_FORWARD_TO_IN_CLUSTER.md | 目标文件不存在，改反引号标注「待补档」 |

## 四、CI 状态快照（2026-08-03 17:40）

| Workflow | 修复前 | 修复后（待验证） |
|----------|--------|-----------------|
| tlm-hook-failsafe E2E（BOM/PS 契约） | failure（15 函数 missing） | 81778b0a 已修，E2E 已重新触发 |
| 云枢系统测试流程 | failure（pythoncom 依赖） | 9c208b9a 已修，已重新触发 |
| 硬编码密码扫描（全分支） | success | — |
| 关键字参数冲突扫描（非 Docker） | success | — |
| 关键字参数冲突扫描（Docker） | failure（52 HIGH + PermissionError） | ✅ 已修复（0055a3f8, run 30882099762 success） |

## 五、遗留问题

1. **Docker kwarg 扫描 52 个 HIGH 风险项**：既有 agent/ 代码，非本次变更引入；且扫描器写报告路径权限不足（PermissionError），需修复扫描配置
2. **post-commit sync-from-source WARN**（exit 1）：worktree 环境同步失败，提交不受影响，待排查
3. **73 个 .ps1 无 BOM**：UTF-8 无 BOM 在 PowerShell 5.1 下中文注释按 ANSI 解析，属既有编码设计（非多 BOM 污染），未纳入本次范围
4. master 上 3 处失效链接修复未随 a16fa4fb 提交（工作区保留），需随下次 master 提交合入

## 六、验证方式

- 本地：PowerShell AST 解析 12/12 通过；15 函数契约模拟导出 15/15 通过；pyproject TOML 解析通过
- CI：以 develop 最新 Run 结果为准（等待 81778b0a / 9c208b9a 触发的新 Run）
