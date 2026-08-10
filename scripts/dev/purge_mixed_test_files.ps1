<#
清理 d815bcbb 中混入的 2 个测试文件改动（备份 + filter-branch 改写 + force push）

背景：并行会话在提交前向 staged 区混入了 2 个测试文件改动：
  - tests/unit/test_singleton_performance.py
  - tests/unit/test_tool_retrieval_quality.py
本脚本将其从 git 历史中移除（恢复到 BaseCommit 版本），并 force-with-lease 推送。

【不易】安全护栏：
  - 改动先备份为 patch（可恢复）
  - fetch 后若远端在目标提交之后有新提交（除预期下游外），默认中止（-Force 可跳过）
  - 校验通过才推送；推送用 --force-with-lease（拒绝覆盖未知远端提交）

用法（仓库根目录执行）：
  .\scripts\dev\purge_mixed_test_files.ps1             # 完整执行（改写+推送）
  .\scripts\dev\purge_mixed_test_files.ps1 -SkipPush   # 只改写不推送，人工检查
#>
param(
    [string]$TargetCommit = "d815bcbb",
    [string]$BaseCommit = "d354b4d0",
    [string[]]$Files = @("tests/unit/test_singleton_performance.py", "tests/unit/test_tool_retrieval_quality.py"),
    [switch]$SkipPush,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Backup = "mixed_files_backup_$(Get-Date -Format yyyyMMdd_HHmmss).patch"

# ── 0) 前置校验 ──────────────────────────────────────────────
if (-not (git rev-parse --is-inside-work-tree 2>$null)) { throw "必须在 git 仓库内执行" }
git rev-parse --verify "$TargetCommit" *> $null; if ($LASTEXITCODE -ne 0) { throw "目标提交不存在: $TargetCommit" }
git rev-parse --verify "$BaseCommit" *> $null; if ($LASTEXITCODE -ne 0) { throw "基线提交不存在: $BaseCommit" }
foreach ($f in $Files) {
    git cat-file -e "$TargetCommit`:$f" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "目标提交中不存在文件: $f" }
}
if (-not (git diff --quiet "$BaseCommit" "$TargetCommit" -- $Files)) {
    Write-Host "     目标提交中的改动摘要:"
    git diff --stat "$BaseCommit" "$TargetCommit" -- $Files | ForEach-Object { Write-Host "     $_" }
} else {
    Write-Host "⚠️ 目标提交相对基线无改动（可能已清理过），继续执行确认一致性"
}

# ── 1) 备份改动 ─────────────────────────────────────────────
Write-Host "[1/5] 备份混入改动 -> $Backup"
git diff "$BaseCommit" "$TargetCommit" -- $Files | Out-File -Encoding utf8 $Backup
if ((Get-Item $Backup).Length -eq 0) { throw "备份为空，中止（可能无改动）" }
Write-Host "     备份完成: $((Get-Item $Backup).Length) bytes"

# ── 2) fetch 并检查远端（防 force 覆盖未知提交）─────────────
Write-Host "[2/5] fetch 远端确认状态"
git fetch origin
if ($LASTEXITCODE -ne 0) { throw "fetch 失败" }
$newRemote = git log --oneline "$TargetCommit..origin/develop"
if ($newRemote) {
    Write-Host "⚠️ 远端在目标提交之后存在新提交："
    $newRemote | ForEach-Object { Write-Host "    $_" }
    if (-not $Force) { throw "远端有新提交，默认中止以避免 force 覆盖。确认无影响后加 -Force 重跑" }
    Write-Host "   -Force 已指定，继续"
}

# ── 3) filter-branch 改写历史 ───────────────────────────────
Write-Host "[3/5] filter-branch 改写历史 (范围: $BaseCommit..HEAD)"
$fileArgs = ($Files -join " ")
git filter-branch -f --index-filter "git restore --source=$BaseCommit --staged -- $fileArgs" -- $BaseCommit..HEAD 2>&1 | Select-Object -Last 8
if ($LASTEXITCODE -ne 0) { throw "filter-branch 失败" }

# ── 4) 校验 ─────────────────────────────────────────────────
Write-Host "[4/5] 校验改写结果"
$diff = git diff "$BaseCommit" HEAD -- $Files
if ($diff) { throw "校验失败：2 文件与基线仍有差异，请人工检查，勿推送" }
Write-Host "     校验通过：2 文件已恢复为基线版本，历史中无残留改动"
git log --oneline "$BaseCommit..HEAD" | Select-Object -First 5

# ── 5) 推送 ─────────────────────────────────────────────────
if ($SkipPush) {
    Write-Host "[-] SkipPush 已指定，跳过推送。人工检查后手动执行:"
    Write-Host "    git push --force-with-lease origin develop"
} else {
    Write-Host "[5/5] force-with-lease 推送"
    git push --force-with-lease origin develop
    if ($LASTEXITCODE -ne 0) { throw "推送失败（远端可能有新提交，请重新 fetch 检查）" }
    Write-Host "     推送成功，历史已清理"
}

Write-Host ""
Write-Host "完成。备份文件: $Backup"
Write-Host "提醒：下游提交 hash 已变化，请转告并行会话重新 fetch + rebase。"
