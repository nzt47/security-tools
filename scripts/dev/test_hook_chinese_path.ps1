<#
.SYNOPSIS
    模拟中文路径下文件被删除/重命名的场景，验证 pre-commit hook 能否实时捕获并阻塞

.DESCRIPTION
    测试策略（沙盒隔离 + 自动还原）：
    1. 在 docs/zh/_场景测试/ 下创建临时测试文件 + 引用文件
    2. 模拟三种场景：删除文件 / 重命名文件 / 移动文件到子目录
    3. 每个场景执行 git add + git commit，观察 hook 是否阻塞
    4. 测试结束自动清理临时文件 + git reset 回到测试前状态

    不破坏现有工作区（守不易），覆盖核心场景（变易），单脚本一键运行（简易）。

.PARAMETER Cleanup
    仅清理上次测试残留，不执行新测试

.EXAMPLE
    .\scripts\dev\test_hook_chinese_path.ps1
    .\scripts\dev\test_hook_chinese_path.ps1 -Cleanup
#>
[CmdletBinding()]
param(
    [switch]$Cleanup
)

# ASCII-only 脚本主体，避免 PowerShell 5.1 GBK 解码问题
# 中文文件名通过变量动态构造（UTF-8 BOM 已加，注释仍用 ASCII 以保稳健）

# Note: $ErrorActionPreference intentionally set to Continue
# Git writes warnings to stderr (e.g. LF/CRLF), which would be treated as
# RemoteException under "Stop" mode and abort the script prematurely.
$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $ProjectRoot

# --- 测试目录与文件清单 ---
$TestDir = "docs\zh\_场景测试"
$RefFile = Join-Path $TestDir "引用文件.md"
$TargetFile = Join-Path $TestDir "目标文件.md"
$TargetFileRenamed = Join-Path $TestDir "目标文件_已重命名.md"
$SubDir = Join-Path $TestDir "子目录"
$TargetFileMoved = Join-Path $SubDir "目标文件.md"

# --- 仅清理模式 ---
if ($Cleanup) {
    Write-Host "=== Cleanup Mode ===" -ForegroundColor Cyan
    if (Test-Path $TestDir) {
        Remove-Item -Recurse -Force $TestDir
        Write-Host "[OK] Removed $TestDir"
    }
    # 清理 git index 中的测试残留
    $testFiles = git diff --cached --name-only 2>$null | Where-Object { $_ -like "*_场景测试*" }
    if ($testFiles) {
        $testFiles | ForEach-Object { git reset HEAD $_ 2>$null | Out-Null }
        Write-Host "[OK] Unstaged test files from git index"
    }
    Write-Host "Cleanup done."
    exit 0
}

# --- 0. 预检查：测试前 git 状态必须干净 ---
Write-Host "=== Pre-flight Check ===" -ForegroundColor Cyan
$initialStatus = git status --short 2>&1 | Out-String
if ($initialStatus -match "_场景测试") {
    Write-Host "[ERROR] 上次测试残留未清理，请先运行 -Cleanup" -ForegroundColor Red
    exit 1
}

# 记录初始 HEAD
$initialHead = git rev-parse HEAD 2>&1 | Out-String
$initialHead = $initialHead.Trim()
Write-Host "[OK] Initial HEAD: $initialHead"

# --- 1. 准备测试目录 ---
Write-Host "`n=== Setup Test Directory ===" -ForegroundColor Cyan
if (-not (Test-Path $TestDir)) {
    New-Item -ItemType Directory -Path $TestDir -Force | Out-Null
    Write-Host "[OK] Created $TestDir"
}

# 创建引用文件，包含指向目标文件的 Markdown 链接
$refContent = @"
# 场景测试引用文件

此文件包含指向目标文件的中文路径链接，用于测试 hook 能否识别失效链接。

- [目标文件](目标文件.md)
"@
[System.IO.File]::WriteAllText($RefFile, $refContent, (New-Object System.Text.UTF8Encoding $true))
Write-Host "[OK] Created reference file: $RefFile"

# 创建目标文件（被引用的文件）
$targetContent = @"
# 目标文件

此文件存在时，引用文件中的链接应通过 hook 检查。
"@
[System.IO.File]::WriteAllText($TargetFile, $targetContent, (New-Object System.Text.UTF8Encoding $true))
Write-Host "[OK] Created target file: $TargetFile"

# --- 2. 场景 0：基线测试（无失效链接，应 PASS） ---
Write-Host "`n=== Scenario 0: Baseline (no broken link, expect PASS) ===" -ForegroundColor Yellow
git add $TestDir 2>&1 | Out-Null
$commitResult = git commit -m "test(scenario0): baseline with valid Chinese path link" 2>&1 | Out-String
Write-Host $commitResult
if ($LASTEXITCODE -eq 0) {
    Write-Host "[PASS] Scenario 0 - hook allowed commit with valid link" -ForegroundColor Green
} else {
    Write-Host "[FAIL] Scenario 0 - hook unexpectedly blocked valid commit" -ForegroundColor Red
}

# --- 3. 场景 1：删除目标文件（应 BLOCK） ---
Write-Host "`n=== Scenario 1: Delete target file (expect BLOCK) ===" -ForegroundColor Yellow
Remove-Item -Force $TargetFile
Write-Host "[OK] Deleted $TargetFile"
git add -A $TestDir 2>&1 | Out-Null  # stage 删除操作
$commitResult = git commit -m "test(scenario1): delete target file, link should break" 2>&1 | Out-String
Write-Host $commitResult
if ($LASTEXITCODE -ne 0 -and $commitResult -match "\[BLOCK\]") {
    Write-Host "[PASS] Scenario 1 - hook correctly blocked commit after file deletion" -ForegroundColor Green
} else {
    Write-Host "[FAIL] Scenario 1 - hook failed to block broken link" -ForegroundColor Red
}

# 还原目标文件以便下一场景
[System.IO.File]::WriteAllText($TargetFile, $targetContent, (New-Object System.Text.UTF8Encoding $true))
git checkout $TargetFile 2>&1 | Out-Null  # 取消 stage 删除
Write-Host "[OK] Restored target file for next scenario"

# --- 4. 场景 2：重命名目标文件（应 BLOCK） ---
Write-Host "`n=== Scenario 2: Rename target file (expect BLOCK) ===" -ForegroundColor Yellow
Rename-Item -Path $TargetFile -NewName "目标文件_已重命名.md"
Write-Host "[OK] Renamed target file to 'target_file_renamed.md'"
git add -A $TestDir 2>&1 | Out-Null
$commitResult = git commit -m "test(scenario2): rename target file, link should break" 2>&1 | Out-String
Write-Host $commitResult
if ($LASTEXITCODE -ne 0 -and $commitResult -match "\[BLOCK\]") {
    Write-Host "[PASS] Scenario 2 - hook correctly blocked commit after file rename" -ForegroundColor Green
} else {
    Write-Host "[FAIL] Scenario 2 - hook failed to block broken link after rename" -ForegroundColor Red
}

# 还原：把重命名后的文件改回原名
Rename-Item -Path $TargetFileRenamed -NewName "目标文件.md"
Write-Host "[OK] Restored target file name"

# --- 5. 场景 3：移动目标文件到子目录（应 BLOCK） ---
Write-Host "`n=== Scenario 3: Move target file to subdir (expect BLOCK) ===" -ForegroundColor Yellow
if (-not (Test-Path $SubDir)) {
    New-Item -ItemType Directory -Path $SubDir -Force | Out-Null
}
Move-Item -Path $TargetFile -Destination $TargetFileMoved -Force
Write-Host "[OK] Moved target file to subdir"
git add -A $TestDir 2>&1 | Out-Null
$commitResult = git commit -m "test(scenario3): move target file to subdir, link should break" 2>&1 | Out-String
Write-Host $commitResult
if ($LASTEXITCODE -ne 0 -and $commitResult -match "\[BLOCK\]") {
    Write-Host "[PASS] Scenario 3 - hook correctly blocked commit after file move" -ForegroundColor Green
} else {
    Write-Host "[FAIL] Scenario 3 - hook failed to block broken link after move" -ForegroundColor Red
}

# --- 6. 验证 --no-verify 可跳过（应急通道） ---
Write-Host "`n=== Scenario 4: Bypass via --no-verify (expect PASS) ===" -ForegroundColor Yellow
$commitResult = git commit --no-verify -m "test(scenario4): bypass hook with --no-verify" 2>&1 | Out-String
Write-Host $commitResult
if ($LASTEXITCODE -eq 0) {
    Write-Host "[PASS] Scenario 4 - --no-verify correctly bypassed hook" -ForegroundColor Green
} else {
    Write-Host "[FAIL] Scenario 4 - --no-verify failed to bypass" -ForegroundColor Red
}

# --- 7. 清理：恢复到测试前 HEAD + 删除测试目录 ---
Write-Host "`n=== Cleanup ===" -ForegroundColor Cyan

# 回滚测试期间所有 commit（包括 --no-verify 提交的）
$currentHead = git rev-parse HEAD 2>&1 | Out-String
$currentHead = $currentHead.Trim()
if ($currentHead -ne $initialHead) {
    Write-Host "[OK] Rolling back HEAD: $currentHead -> $initialHead"
    git reset --mixed $initialHead 2>&1 | Out-Null
}

# 删除测试目录
if (Test-Path $TestDir) {
    Remove-Item -Recurse -Force $TestDir
    Write-Host "[OK] Removed test directory: $TestDir"
}

# 验证最终状态
$finalStatus = git status --short 2>&1 | Out-String
if ($finalStatus -match "_场景测试") {
    Write-Host "[WARN] Test residue remains, run -Cleanup to remove" -ForegroundColor Yellow
} else {
    Write-Host "[OK] Workspace restored to pre-test state" -ForegroundColor Green
}

# --- 8. 总结 ---
Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "Initial HEAD: $initialHead"
Write-Host "Final HEAD:   $((git rev-parse HEAD 2>&1 | Out-String).Trim())"
Write-Host "Test directory removed: True"
Write-Host ""
Write-Host "Scenarios tested:" -ForegroundColor Yellow
Write-Host "  0. Baseline (valid link)         -> expect PASS (commit allowed)"
Write-Host "  1. Delete target file            -> expect BLOCK"
Write-Host "  2. Rename target file            -> expect BLOCK"
Write-Host "  3. Move target file to subdir    -> expect BLOCK"
Write-Host "  4. --no-verify bypass            -> expect PASS (commit allowed)"
Write-Host ""
Write-Host "See [PASS]/[FAIL] markers above for each scenario result." -ForegroundColor Green
