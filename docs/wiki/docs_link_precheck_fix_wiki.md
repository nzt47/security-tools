# Docs 链接预检修复 Wiki

> 最后更新：2026-08-10
> 维护者：团队共享
> 关联提交：`09a3d81d`（修复 commit）、`363176d3`（归档 SVG/源文件，首次无 [skip ci] 触发全量 CI）
> CI 验证：run 31409491048（27 job 全 success，含文档链接预检）

---

## 概述

master 分支 CI 的 `docs-precheck-tests`（链接预检 + 锚点回归，阻塞阈值 0）在首次无 `[skip ci]` 的 push 中全量触发，暴露了 4 个历史失效链接——全部来自 `docs/troubleshooting/r1_r4_fix_summary_20260810.md` 引用的 3 个不存在文件。此前所有文档提交均带 `[skip ci]`，该检查从未真正生效。

| 失效链接 | 目标文件状态 | 处置 |
|----------|-------------|------|
| `shard_coverage_artifact_and_omit_rootcause_20260809.md`（×2） | 仅 develop 存在 | 从 develop 检出 |
| `shard56_log_assert_rootcause_archive_20260809.md` | 仅 develop 存在 | 从 develop 检出 |
| `r1_r4_fix_pr_and_impact_20260809.md` | 任何分支均不存在 | 删除引用 |

---

## Commit Message 记录

```text
docs(troubleshooting): 修复失效链接 - 检出 develop 归档文档并删除不存在引用

根因: r1_r4_fix_summary_20260810.md 引用 3 个不存在于 master 的文档
  (2 个仅 develop 存在, 1 个任何分支均不存在), 首次无 [skip ci] 全量 CI
  触发 docs-precheck-tests 链接预检(阻塞阈值 0)暴露。

修复:
- 从 develop 检出 2 个 troubleshooting 文档
- 按引用链递归确定 6 个 archive 闭合集文件并检出
  (scripts_gate_transition_plan → scripts_gate_integration_plan
   → scripts_coverage_governance_plan → path_a_acceptance_report
   → path_a_coverage_comparison_report / path_a_coverage_omit_report)
- 删除 r1_r4_fix_summary 中对不存在文档 r1_r4_fix_pr_and_impact_20260809.md 的引用

验证:
- 本地 precheck_docs.ps1 -BlockMode -AllowBroken 0 -SkipChart → 0 失效链接 PASS
- CI run 31409491048 全绿(27 job success, 含链接预检与锚点回归)
```

---

## 修复步骤

1. **引用链分析**：确定 2 个 troubleshooting 文档 → 6 个 archive 文档的最小闭合集（避免检出整个 develop 目录）
2. **检出文档**：`git checkout develop -- <path>` 逐个检出 8 个文件
3. **删除失效引用**：编辑 `r1_r4_fix_summary_20260810.md` 移除不存在文档的引用行
4. **本地验证**：`precheck_docs.ps1 -BlockMode -AllowBroken 0 -SkipChart`（对齐 CI 的 git_precommit_check.ps1 内部参数）
5. **提交推送**：commit `09a3d81d`，pre-commit hooks 全 Passed；远端并发 push 用 `git rebase origin/master` 处理
6. **CI 确认**：run 31409491048 全部 27 个 job success

---

## 教训

### 1. 跨分支检出目录的误删风险（本次险些事故）

`git checkout develop -- <dir>` 会检出该分支**整个目录**（覆盖 master 已有文件）。本次流程中：
- `git ls-tree -r --name-only master docs/archive/ | Select-String "20260809"` 输出为空 → 误判"master 无 archive 目录"（实际 master 有大量 archive 文件，只是无 20260809 命名）
- `Remove-Item` 删除非 keep 文件 → 误删 150+ master 已跟踪文件（git status 显示 150+ `D`）
- 恢复：`git checkout -- docs/archive/` 从 index 立即还原

**检查清单**：
- 判断目录是否存在：用 `git ls-files <path>`（工作区真实跟踪集），不要用 `ls-tree + 过滤管道`（过滤词不匹配会误判）
- 跨分支检出目录前：先 `git diff --stat master develop -- <dir>` 确认两分支文件集合差异
- 删除检出文件前：核对是否 HEAD 已跟踪文件

### 2. 提交遗漏

`git checkout develop -- <file>` 只 stage 检出文件，同一 commit 中手改的文件（如删除引用的 r1_r4_fix_summary）**不会自动 stage**。commit 后必须 `git status` 核对 staged 集合完整性（本次用 amend 补入，未推送前安全）。

### 3. precheck 参数必须与 CI 对齐

CI 的 git_precommit_check.ps1 内部调用 `precheck_docs.ps1 -SkipChart`（跳过图表生成/检查）。本地手动验证必须带 `-SkipChart`，否则脚本会重新生成图表文件（`git status` 出现 `M docs/perf-charts/*.md`）且误报图表链接 broken。

### 4. 并发 push 处理

远端 CI 健康度看板自动提交（`daf12078`）导致 push 被拒 → `git rebase origin/master` 后重推（本次零冲突，不同文件）。涉及 CI 自动提交的分支，push 前先 `git fetch` 检查。

---

## 诊断脚本使用指南（check_docs_broken_links.ps1）

> 新增：2026-08-10（commit `025feffa` 初版，`pwsh` 跨平台兼容）

### 功能

自动化本页修复流程的**诊断步骤**：运行链接预检 → 解析 `[BROKEN]` → 输出"本地 / develop 分支存在性"诊断表与修复建议。

| 场景 | 诊断输出 | 建议处置 |
|------|----------|----------|
| 无失效链接 | `[PASS] 0 失效`，exit 0 | 无需处理 |
| 目标本地不存在 + develop 存在 | 建议行 | `git checkout develop -- <path>` |
| 双方都不存在 | 建议行 | 删除 host 中的引用，或补建目标文档 |
| 目标不在仓库内（外部路径） | 建议行 | 检查引用是否应指向外部 URL/资源 |

### 用法

```powershell
# 默认当前目录为仓库根
pwsh -NoProfile -File scripts/dev/check_docs_broken_links.ps1

# 指定仓库根目录
pwsh -NoProfile -File scripts/dev/check_docs_broken_links.ps1 -TargetRepo C:\path\to\repo

# 调试：跳过 precheck 运行，仅解析上次输出
pwsh -NoProfile -File scripts/dev/check_docs_broken_links.ps1 -SkipCheck
```

退出码：`0` = 无失效链接；`1` = 存在失效链接（可用于 hook / CI 阻断）。

### 诊断逻辑

1. **运行链接预检**：调用 `precheck_docs.ps1 -BlockMode -AllowBroken 0 -SkipChart`（参数对齐 CI 的 `git_precommit_check.ps1` 内部调用；`-SkipChart` 跳过图表生成/检查，避免图表误报与工作区污染）
2. **解析 `[BROKEN]`**：兼容 `[ERROR] [BROKEN] host: target` 与 `[BROKEN] host: target` 两种格式
3. **逐条诊断**：
   - **host 定位**：precheck 输出的 host 是纯文件名（如 `tmp_diag_test.md`），脚本在全仓库递归定位真实目录（`Get-ChildItem -Recurse -Filter`），host 含路径分隔符时直接使用——避免按仓库根误解析
   - **目标绝对化**：`Path.GetFullPath(hostDir + target)`，与 precheck 的 `Path::Combine` 规则一致（支持 `../` 相对路径）
   - **develop 存在性**：`git cat-file -e develop:<repo-relative-path>`，目标不在仓库内（外部/绝对路径）时跳过
4. **汇总**：输出可检出 / 需删除补建的计数

### 集成位置

| 层 | 位置 | 行为 |
|----|------|------|
| 本地 pre-commit | `.pre-commit-config.yaml` → `docs-broken-links-diagnose`（commit 阶段） | 有失效链接阻断提交 |
| 远端 CI | `ci.yml` code-quality job → `docs 链接预检诊断` step | 第二道防线，防 `git commit --no-verify` 绕过 |

**跨平台说明**：ubuntu runner 无 `powershell.exe`，脚本内部优先用 `pwsh`（PowerShell 7），Windows PowerShell 5.1 环境回退 `powershell`。

---

## 关联文档

| 文档 | 说明 |
|------|------|
| [logging_leak_fix_review_20260810.md](../troubleshooting/logging_leak_fix_review_20260810.md) | logging.disable 泄漏治理复盘 |
| [logging_leak_governance_wiki_20260810.md](../troubleshooting/logging_leak_governance_wiki_20260810.md) | logging 治理 Wiki 整合版 |
| [logging_defense_mermaid_20260810.md](../troubleshooting/logging_defense_mermaid_20260810.md) | 三层防线全景图（Mermaid + SVG） |
