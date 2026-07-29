<#
.SYNOPSIS
    多仓库协作 + post-commit 自动同步端到端验证

.DESCRIPTION
    三义校验：
    - 不易：源 psm1 修改可回滚（git restore）；临时仓库/模块目录 try/finally 清理
    - 变易：模拟新仓库从 LocalPSRepo 安装、升级的完整生命周期
    - 简易：10 步线性流程，无嵌套抽象

    前置条件：
    1. LocalPSRepo 已注册且 tlm-hook-failsafe 已发布（运行过 publish-to-local-repo.ps1）
    2. .git/hooks/post-commit 已安装
    3. 当前仓库工作树干净（git status 无未提交改动）

.EXAMPLE
    .\tests\test_multi_repo_sync.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$repoRoot      = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$sourcePsm1    = Join-Path $repoRoot "scripts\dev\hook_fail_safe.psm1"
$packagePsm1   = Join-Path $repoRoot "packages\tlm-hook-failsafe\tlm-hook-failsafe.psm1"
$publishScript = Join-Path $PSScriptRoot "..\publish-to-local-repo.ps1"
$publishScript = (Resolve-Path $publishScript).Path

# 测试隔离目录
$testRepoDir   = "D:\code\test-multi-repo-$(Get-Random)"
$testModuleDir = Join-Path $env:TEMP "tlm-hfs-test-$(Get-Random)"
$testMarker    = "# TEST_MARKER_$(Get-Random)_$(Get-Date -Format 'yyyyMMddHHmmss')"

# 不易：记录初始状态用于回滚
$initialPsm1Hash = (Get-FileHash $sourcePsm1).Hash
$initialPkgHash  = (Get-FileHash $packagePsm1).Hash
$initialHead     = git rev-parse HEAD
$initialManifestVersion = $null

$results = @()

function Show-Step {
    param([int]$Index, [int]$Total, [string]$Message)
    Write-Host "`n[$Index/$Total] $Message" -ForegroundColor Yellow
}

function Show-Pass {
    param([string]$Message)
    Write-Host "  [PASS] $Message" -ForegroundColor Green
    $script:results += @{ Status = 'PASS'; Message = $Message }
}

function Show-Fail {
    param([string]$Message)
    Write-Host "  [FAIL] $Message" -ForegroundColor Red
    $script:results += @{ Status = 'FAIL'; Message = $Message }
}

Write-Host "=== 多仓库协作 + post-commit 自动同步验证 ===" -ForegroundColor Cyan
Write-Host "  源仓库:       $repoRoot"
Write-Host "  临时仓库:     $testRepoDir"
Write-Host "  临时模块目录: $testModuleDir"
Write-Host "  测试 marker:  $testMarker"
Write-Host "  初始 HEAD:    $initialHead"

# 不易：try/finally 保证清理
try {
    # ── [1/10] 创建临时仓库 ──
    Show-Step 1 10 "创建临时仓库..."
    New-Item -ItemType Directory -Path $testRepoDir -Force | Out-Null
    & git -C $testRepoDir init 2>&1 | Out-Null
    & git -C $testRepoDir config user.email "test@test.local"
    & git -C $testRepoDir config user.name "test"
    if (Test-Path "$testRepoDir\.git") {
        Show-Pass "临时仓库已创建: $testRepoDir"
    } else {
        Show-Fail "临时仓库创建失败"
        throw "git init failed"
    }

    # ── [2/10] 从 LocalPSRepo 安装到临时模块目录 ──
    Show-Step 2 10 "从 LocalPSRepo 安装模块..."
    New-Item -ItemType Directory -Path $testModuleDir -Force | Out-Null
    try {
        Save-Module -Name tlm-hook-failsafe -Repository LocalPSRepo -Path $testModuleDir -Force -ErrorAction Stop
        $installedModuleDir = Get-ChildItem -Path (Join-Path $testModuleDir "tlm-hook-failsafe") -Directory |
            Sort-Object Name -Descending | Select-Object -First 1
        Show-Pass "已安装到: $($installedModuleDir.FullName)"
    } catch {
        Show-Fail "Save-Module 失败: $($_.Exception.Message)"
        throw
    }

    # ── [3/10] 验证 Import-Module + 12 函数导出 ──
    Show-Step 3 10 "验证 12 函数导出..."
    try {
        # Invariant: Import-Module requires .psd1/.psm1 file path or module name, not directory
        $psd1File = Join-Path $installedModuleDir.FullName "tlm-hook-failsafe.psd1"
        Import-Module $psd1File -Force -ErrorAction Stop
        $exported = @(Get-Command -Module tlm-hook-failsafe -ErrorAction SilentlyContinue)
        if ($exported.Count -eq 12) {
            Show-Pass "12 函数导出"
        } else {
            Show-Fail "导出函数数=$($exported.Count), 预期 12"
        }
    } catch {
        Show-Fail "Import-Module 失败: $($_.Exception.Message)"
    }

    # 记录初始版本（用于发布后比对）
    $initialManifestVersion = (Test-ModuleManifest -Path (Join-Path $installedModuleDir.FullName "tlm-hook-failsafe.psd1")).Version
    Write-Host "  [INFO] 初始版本: $initialManifestVersion" -ForegroundColor Cyan

    # ── [4/10] 修改源 psm1（添加注释行，可回滚） ──
    Show-Step 4 10 "修改源 psm1（添加 marker 注释）..."
    # 不易：注释行加在文件末尾，不影响导出
    $content = [System.IO.File]::ReadAllText($sourcePsm1, [System.Text.Encoding]::UTF8)
    # 在 Export-ModuleMember 之前插入 marker
    if ($content -match 'Export-ModuleMember') {
        $newContent = $content -replace '(Export-ModuleMember)', "$testMarker`r`n`$1"
    } else {
        # 若无 Export-ModuleMember，加到文件末尾
        $newContent = $content.TrimEnd() + "`r`n" + $testMarker + "`r`n"
    }
    $utf8Bom = New-Object System.Text.UTF8Encoding $true
    [System.IO.File]::WriteAllText($sourcePsm1, $newContent, $utf8Bom)
    Show-Pass "已添加 marker: $testMarker"

    # ── [5/10] git add + commit（触发 post-commit 自动同步） ──
    Show-Step 5 10 "提交变更（触发 post-commit）..."
    & git -C $repoRoot add scripts/dev/hook_fail_safe.psm1 2>&1 | Out-Null
    $commitOutput = & git -C $repoRoot commit -m "test: 临时 marker 用于多仓库同步验证 [skip ci]" 2>&1
    Write-Host "  git commit 输出:" -ForegroundColor Gray
    $commitOutput | ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
    Show-Pass "提交完成"

    # ── [6/10] 验证包内 .psm1 哈希已更新 ──
    Show-Step 6 10 "验证包内 .psm1 已同步..."
    $packageContent = [System.IO.File]::ReadAllText($packagePsm1, [System.Text.Encoding]::UTF8)
    if ($packageContent -match [regex]::Escape($testMarker)) {
        $srcHash = (Get-FileHash $sourcePsm1).Hash
        $pkgHash = (Get-FileHash $packagePsm1).Hash
        if ($srcHash -eq $pkgHash) {
            Show-Pass "包内已同步，哈希一致: $pkgHash"
        } else {
            Show-Fail "哈希不一致: source=$srcHash package=$pkgHash"
        }
    } else {
        Show-Fail "包内 .psm1 未同步 marker"
    }

    # ── [7/10] 发布新版本（-BumpVersion） ──
    Show-Step 7 10 "发布新版本到 LocalPSRepo..."
    & powershell -ExecutionPolicy Bypass -File $publishScript -BumpVersion 2>&1 |
        ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
    if ($LASTEXITCODE -eq 0) {
        Show-Pass "新版本已发布"
    } else {
        Show-Fail "发布失败 (exit $LASTEXITCODE)"
    }

    # ── [8/10] 临时仓库重新拉取新版本 ──
    Show-Step 8 10 "临时仓库重新拉取新版本..."
    Remove-Module tlm-hook-failsafe -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force (Join-Path $testModuleDir "tlm-hook-failsafe") -ErrorAction SilentlyContinue
    try {
        Save-Module -Name tlm-hook-failsafe -Repository LocalPSRepo -Path $testModuleDir -Force -ErrorAction Stop
        $newModuleDir = Get-ChildItem -Path (Join-Path $testModuleDir "tlm-hook-failsafe") -Directory |
            Sort-Object Name -Descending | Select-Object -First 1
        # Invariant: import via .psd1 file path, not directory
        $newPsd1 = Join-Path $newModuleDir.FullName "tlm-hook-failsafe.psd1"
        Import-Module $newPsd1 -Force -ErrorAction Stop
        $newVersion = (Test-ModuleManifest -Path $newPsd1).Version
        Show-Pass "新版本: $newVersion"
    } catch {
        Show-Fail "重新拉取失败: $($_.Exception.Message)"
    }

    # ── [9/10] 验证新版本含 marker ──
    Show-Step 9 10 "验证新版本含 marker..."
    $newPsm1Content = [System.IO.File]::ReadAllText((Join-Path $newModuleDir.FullName "tlm-hook-failsafe.psm1"), [System.Text.Encoding]::UTF8)
    if ($newPsm1Content -match [regex]::Escape($testMarker)) {
        Show-Pass "新版本已含 marker"
    } else {
        Show-Fail "新版本未含 marker"
    }

    # ── [10/10] 汇总 ──
    Show-Step 10 10 "汇总"
    $passCount = ($results | Where-Object { $_.Status -eq 'PASS' }).Count
    $failCount = ($results | Where-Object { $_.Status -eq 'FAIL' }).Count
    Write-Host ""
    Write-Host "=== Summary ===" -ForegroundColor Cyan
    $results | ForEach-Object {
        $color = if ($_.Status -eq 'PASS') { 'Green' } else { 'Red' }
        Write-Host "  [$($_.Status)] $($_.Message)" -ForegroundColor $color
    }
    Write-Host ""
    Write-Host "  Total: $($results.Count) | PASS: $passCount | FAIL: $failCount" -ForegroundColor $(if ($failCount -eq 0) { 'Green' } else { 'Red' })

} finally {
    Write-Host "`n=== 清理 ===" -ForegroundColor Cyan

    # 不易：回滚源 psm1 修改
    try {
        # 回滚测试提交（保留 reflog 可恢复）
        & git -C $repoRoot reset --soft HEAD~1 2>&1 | Out-Null
        # 撤销暂存
        & git -C $repoRoot restore --staged scripts/dev/hook_fail_safe.psm1 2>&1 | Out-Null
        # restore 工作树到 HEAD
        & git -C $repoRoot restore scripts/dev/hook_fail_safe.psm1 2>&1 | Out-Null
        Write-Host "  [OK] 源 psm1 已回滚" -ForegroundColor Green

        # 哈希校验确认回滚成功
        $finalHash = (Get-FileHash $sourcePsm1).Hash
        if ($finalHash -ne $initialPsm1Hash) {
            Write-Host "  [WARN] 哈希不匹配: $finalHash != $initialPsm1Hash" -ForegroundColor Yellow
        } else {
            Write-Host "  [OK] 哈希一致: $finalHash" -ForegroundColor Green
        }
    } catch {
        Write-Host "  [WARN] 回滚失败: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    # 变易：删除临时仓库
    if (Test-Path $testRepoDir) {
        Remove-Item -Recurse -Force $testRepoDir -ErrorAction SilentlyContinue
        Write-Host "  [OK] 删除临时仓库: $testRepoDir" -ForegroundColor Green
    }

    # 变易：删除临时模块目录
    if (Test-Path $testModuleDir) {
        Remove-Item -Recurse -Force $testModuleDir -ErrorAction SilentlyContinue
        Write-Host "  [OK] 删除临时模块目录: $testModuleDir" -ForegroundColor Green
    }

    # 变易：删除测试版本的 .nupkg（保留 initialManifestVersion 及以下）
    try {
        $repoPkgs = Get-ChildItem -Path "C:\PSRepo" -Filter "tlm-hook-failsafe.*.nupkg" -ErrorAction SilentlyContinue
        foreach ($pkg in $repoPkgs) {
            # 从文件名解析版本：tlm-hook-failsafe.1.0.1.nupkg -> 1.0.1
            if ($pkg.BaseName -match "tlm-hook-failsafe\.(\d+\.\d+\.\d+)") {
                $pkgVersion = [version]$matches[1]
                if ($initialManifestVersion -and $pkgVersion -gt $initialManifestVersion) {
                    Remove-Item $pkg.FullName -Force -ErrorAction SilentlyContinue
                    Write-Host "  [OK] 删除测试版本包: $($pkg.Name)" -ForegroundColor Green
                }
            }
        }
    } catch {
        Write-Host "  [WARN] 清理 .nupkg 失败: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    # 不易：回滚 .psd1 版本号（若 -BumpVersion 修改了）
    try {
        $psd1Path = Join-Path $repoRoot "packages\tlm-hook-failsafe\tlm-hook-failsafe.psd1"
        $currentManifest = Test-ModuleManifest -Path $psd1Path
        if ($initialManifestVersion -and $currentManifest.Version -gt $initialManifestVersion) {
            Update-ModuleManifest -Path $psd1Path -ModuleVersion $initialManifestVersion
            # 修复 BOM
            $bytes = [System.IO.File]::ReadAllBytes($psd1Path)
            $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
            if (-not $hasBom) {
                $content = [System.IO.File]::ReadAllText($psd1Path, [System.Text.Encoding]::UTF8)
                $utf8Bom = New-Object System.Text.UTF8Encoding $true
                [System.IO.File]::WriteAllText($psd1Path, $content, $utf8Bom)
            }
            Write-Host "  [OK] .psd1 版本回滚到 $initialManifestVersion" -ForegroundColor Green
        }
    } catch {
        Write-Host "  [WARN] .psd1 版本回滚失败: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    Write-Host "[DONE] 清理完成" -ForegroundColor Green
}

# 退出码
$failCount = ($results | Where-Object { $_.Status -eq 'FAIL' }).Count
if ($failCount -gt 0) { exit 1 }
exit 0
