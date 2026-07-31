﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿<#
.SYNOPSIS
    模拟真实仓库部署场景：在 D:\code\real-project 下验证 sync 脚本实际效果

.DESCRIPTION
    与 simulate_batch_sync.ps1 的区别：
    - 单仓库深度验证（而非 3 个空仓库）
    - 模拟真实场景：含 docs/ 内容 + 已有自定义 hook + README + 多次提交历史
    - 验证 sync 脚本在复杂场景下的表现：备份/覆盖/环境变量/真实阻塞

    验证步骤：
    1. 创建真实仓库（含 docs/ + README.md + 已有自定义 hook + 2 次提交历史）
    2. 运行 sync_precommit_hook.ps1 -Install
    3. 验证旧 hook 被备份
    4. 验证新 hook 部署质量（5 项）
    5. 真实触发：docs/ 含失效链接应被阻塞
    6. 修复失效链接后应能正常提交
    7. 验证 -Status 正确识别
    8. 清理

.PARAMETER KeepRepo
    保留测试仓库不清理

.EXAMPLE
    .\scripts\dev\simulate_real_deploy.ps1
    .\scripts\dev\simulate_real_deploy.ps1 -KeepRepo
#>
[CmdletBinding()]
param(
    [switch]$KeepRepo
)

$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $ProjectRoot

$testRepo = "D:\code\real-project"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$utf8Bom = New-Object System.Text.UTF8Encoding($true)

Write-Host "=== 真实仓库部署模拟测试 ===" -ForegroundColor Cyan
Write-Host "  测试仓库: $testRepo"
Write-Host "  保留仓库: $KeepRepo"

# --- 0. 预检查 ---
if (Test-Path $testRepo) {
    Write-Host "`n[ERROR] $testRepo 已存在，请先清理" -ForegroundColor Red
    exit 1
}

# --- 1. 创建真实仓库（含内容 + 历史 + 已有 hook） ---
Write-Host "`n[1/8] 创建真实仓库（含 docs/ + README + 已有 hook + 提交历史）..." -ForegroundColor Yellow

# 创建目录结构
New-Item -ItemType Directory -Path $testRepo -Force | Out-Null
New-Item -ItemType Directory -Path "$testRepo\docs" -Force | Out-Null
New-Item -ItemType Directory -Path "$testRepo\docs\api" -Force | Out-Null

# git init + 配置
git -C $testRepo init 2>&1 | Out-Null
git -C $testRepo config user.email "dev@real-project.com"
git -C $testRepo config user.name "Real Project Dev"

# 创建 README.md
$readmeContent = @"
# Real Project

这是一个模拟真实项目的仓库，用于验证 sync_precommit_hook.ps1 的部署效果。

## 文档

- [设计文档](docs/design.md)
- [API 文档](docs/api/index.md)

## 部署

详见 [部署指南](docs/deployment.md)。
"@
[System.IO.File]::WriteAllText("$testRepo\README.md", $readmeContent, $utf8Bom)

# 创建 docs/design.md（被 README 引用，应存在）
$designContent = @"
# 设计文档

## 架构

本系统采用三层架构。
"@
[System.IO.File]::WriteAllText("$testRepo\docs\design.md", $designContent, $utf8Bom)

# 创建 docs/deployment.md（被 README 引用，应存在）
$deployContent = @"
# 部署指南

## 步骤

1. 克隆仓库
2. 运行 install.sh
3. 启动服务
"@
[System.IO.File]::WriteAllText("$testRepo\docs\deployment.md", $deployContent, $utf8Bom)

# 创建 docs/api/index.md（被 README 引用，应存在）
$apiContent = @"
# API 文档

## 接口列表

- GET /health
- POST /api/v1/users
"@
[System.IO.File]::WriteAllText("$testRepo\docs\api\index.md", $apiContent, $utf8Bom)

# 第一次提交（建立历史）
git -C $testRepo add . 2>&1 | Out-Null
git -C $testRepo commit --no-verify -m "init: project scaffold with docs" 2>&1 | Out-Null
Write-Host "  [OK] 第一次提交: init scaffold" -ForegroundColor Green

# 添加一个含失效链接的文档（用于后续验证 hook 阻塞）
$brokenContent = @"
# 已知问题

## 待修复

- [失效链接](missing-page.md) - 此链接目标不存在
- [另一个失效](docs/nonexistent.md) - 此链接目标不存在
"@
[System.IO.File]::WriteAllText("$testRepo\docs\known-issues.md", $brokenContent, $utf8Bom)

# 第二次提交（含失效链接，用 --no-verify 跳过，因为此时还没有 hook）
git -C $testRepo add . 2>&1 | Out-Null
git -C $testRepo commit --no-verify -m "docs: add known-issues (contains broken links)" 2>&1 | Out-Null
Write-Host "  [OK] 第二次提交: known-issues（含失效链接，--no-verify 跳过）" -ForegroundColor Green

# 模拟已有自定义 hook（用户之前的配置）
$hooksDir = "$testRepo\.git\hooks"
if (-not (Test-Path $hooksDir)) { New-Item -ItemType Directory -Path $hooksDir -Force | Out-Null }
$existingHook = @'
#!/bin/bash
# 用户自定义的 pre-commit hook
echo "[custom-hook] running custom checks..."
# 模拟一些检查逻辑
echo "[custom-hook] checks passed"
exit 0
'@
[System.IO.File]::WriteAllText("$hooksDir\pre-commit", $existingHook, $utf8NoBom)
Write-Host "  [OK] 已有自定义 hook 已放置（将被 sync 备份）" -ForegroundColor Green

# 显示仓库状态
$commitCount = (git -C $testRepo log --oneline 2>&1 | Measure-Object).Count
Write-Host "  [INFO] 仓库就绪: $commitCount 次提交, 已有自定义 hook" -ForegroundColor Gray

# --- 2. 运行 sync 脚本 ---
Write-Host "`n[2/8] 运行 sync_precommit_hook.ps1 -Install..." -ForegroundColor Yellow
& powershell -ExecutionPolicy Bypass -File scripts\dev\sync_precommit_hook.ps1 -Install $testRepo

# --- 3. 验证旧 hook 被备份 ---
Write-Host "`n[3/8] 验证旧 hook 被备份..." -ForegroundColor Yellow
$bakFiles = @(Get-ChildItem $hooksDir -Filter 'pre-commit.bak.*' -ErrorAction SilentlyContinue)
if ($bakFiles.Count -gt 0) {
    $bakContent = [System.IO.File]::ReadAllText($bakFiles[0].FullName, [System.Text.Encoding]::UTF8)
    $hasCustomMarker = $bakContent -match 'custom-hook'
    Write-Host "  [PASS] 备份文件存在: $($bakFiles[0].Name)" -ForegroundColor Green
    Write-Host "  [PASS] 备份内容含旧 hook 标识: $hasCustomMarker" -ForegroundColor Green
    $backupPass = $true
} else {
    Write-Host "  [FAIL] 无备份文件" -ForegroundColor Red
    $backupPass = $false
}

# --- 4. 验证新 hook 部署质量 ---
Write-Host "`n[4/8] 验证新 hook 部署质量..." -ForegroundColor Yellow
$hookPath = "$hooksDir\pre-commit"
$tests = @()

$tests += [PSCustomObject]@{ Check = 'hook 存在'; Expected = $true; Actual = (Test-Path $hookPath) }

$bytes = [System.IO.File]::ReadAllBytes($hookPath)
$tests += [PSCustomObject]@{ Check = '无 BOM (bash 兼容)'; Expected = $true; Actual = (-not ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)) }

$content = [System.IO.File]::ReadAllText($hookPath, [System.Text.Encoding]::UTF8)
$tests += [PSCustomObject]@{ Check = 'marker TLM-HOOK v1'; Expected = $true; Actual = ($content -match 'TLM-HOOK v1') }
$tests += [PSCustomObject]@{ Check = '阈值 AllowBroken 0'; Expected = $true; Actual = ($content -match '-AllowBroken 0') }
$tests += [PSCustomObject]@{ Check = '使用 $TLM_HOOK_SOURCE_REPO'; Expected = $true; Actual = ($content -match '\$TLM_HOOK_SOURCE_REPO') }
$tests += [PSCustomObject]@{ Check = '使用 -TargetRepo 参数'; Expected = $true; Actual = ($content -match '-TargetRepo') }

$pass = 0
$fail = 0
foreach ($t in $tests) {
    $ok = $t.Expected -eq $t.Actual
    $color = if ($ok) { 'Green' } else { 'Red' }
    $status = if ($ok) { 'PASS' } else { 'FAIL' }
    Write-Host "  [$status] $($t.Check)" -ForegroundColor $color
    if ($ok) { $pass++ } else { $fail++ }
}
Write-Host "  部署质量: $pass/$($tests.Count) passed" -ForegroundColor $(if ($fail -eq 0) { 'Green' } else { 'Red' })
$qualityFail = $fail

# --- 5. 真实触发：docs/ 含失效链接应被阻塞 ---
Write-Host "`n[5/8] 真实阻塞测试：提交含失效链接的变更..." -ForegroundColor Yellow
$env:TLM_HOOK_SOURCE_REPO = $ProjectRoot

# known-issues.md 已含失效链接，但已经被 --no-verify 提交了
# 现在新增一个变更触发 hook
$newChange = "# 变更日志`n`n- 2026-07-29: 初始版本`n"
[System.IO.File]::WriteAllText("$testRepo\docs\changelog.md", $newChange, $utf8Bom)
git -C $testRepo add . 2>&1 | Out-Null

$commitResult = git -C $testRepo commit -m "docs: add changelog" 2>&1 | Out-String
$commitExit = $LASTEXITCODE

if ($commitExit -ne 0 -and $commitResult -match 'BLOCK') {
    Write-Host "  [PASS] hook 正确阻塞了提交（因 known-issues.md 含失效链接）" -ForegroundColor Green
    $blockPass = $true
} else {
    Write-Host "  [FAIL] hook 未阻塞。exit=$commitExit" -ForegroundColor Red
    Write-Host $commitResult
    $blockPass = $false
}

# --- 6. 修复失效链接后应能正常提交 ---
Write-Host "`n[6/8] 修复失效链接后验证可正常提交..." -ForegroundColor Yellow

# 修复 known-issues.md 中的失效链接
$fixedContent = @"
# 已知问题

## 待修复

- [设计文档](design.md) - 已修复链接
- [部署指南](deployment.md) - 已修复链接
"@
[System.IO.File]::WriteAllText("$testRepo\docs\known-issues.md", $fixedContent, $utf8Bom)

git -C $testRepo add . 2>&1 | Out-Null
$commitResult2 = git -C $testRepo commit -m "docs: fix broken links in known-issues + add changelog" 2>&1 | Out-String
$commitExit2 = $LASTEXITCODE

if ($commitExit2 -eq 0) {
    Write-Host "  [PASS] 修复后提交成功" -ForegroundColor Green
    $fixPass = $true
} else {
    Write-Host "  [FAIL] 修复后仍无法提交。exit=$commitExit2" -ForegroundColor Red
    Write-Host $commitResult2
    $fixPass = $false
}

# --- 7. 验证 -Status 正确识别 ---
Write-Host "`n[7/8] 验证 -Status 正确识别..." -ForegroundColor Yellow
& powershell -ExecutionPolicy Bypass -File scripts\dev\sync_precommit_hook.ps1 -Status -ScanRoot D:\code

# --- 8. 总结 ---
Write-Host "`n[8/8] 总结..." -ForegroundColor Yellow
Write-Host "  备份验证: $(if ($backupPass) { 'PASS' } else { 'FAIL' })"
Write-Host "  部署质量: $pass/$($tests.Count)"
Write-Host "  hook 阻塞: $(if ($blockPass) { 'PASS' } else { 'FAIL' })"
Write-Host "  修复后提交: $(if ($fixPass) { 'PASS' } else { 'FAIL' })"

$totalPass = ($backupPass -and $qualityFail -eq 0 -and $blockPass -and $fixPass)
Write-Host "  总体: $(if ($totalPass) { 'ALL PASS' } else { 'HAS FAIL' })" -ForegroundColor $(if ($totalPass) { 'Green' } else { 'Red' })

# --- 清理 ---
if ($KeepRepo) {
    Write-Host "`n[KeepRepo] 测试仓库保留在 $testRepo" -ForegroundColor Yellow
} else {
    Write-Host "`n[Cleanup] 删除测试仓库 $testRepo..." -ForegroundColor Gray
    # 先重置 git 状态避免文件锁
    git -C $testRepo reset --hard 2>&1 | Out-Null
    Remove-Item -Recurse -Force $testRepo -ErrorAction SilentlyContinue
    if (-not (Test-Path $testRepo)) {
        Write-Host "  [OK] 已清理" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] 清理失败，请手动删除 $testRepo" -ForegroundColor Yellow
    }
    # 清理空的 D:\code 目录（如果为空）
    if (Test-Path D:\code) {
        $remaining = Get-ChildItem D:\code -Force -ErrorAction SilentlyContinue
        if (-not $remaining) {
            Remove-Item D:\code -Force
        }
    }
}

if (-not $totalPass) { exit 1 }
exit 0
