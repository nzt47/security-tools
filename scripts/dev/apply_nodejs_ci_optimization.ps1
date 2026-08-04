<#
.SYNOPSIS
    自动化扫描 Node.js 子项目，识别 jest/vitest，在 CI workflow 中应用优化参数

.DESCRIPTION
    基于 P0 安全验证模板的性能优化实践，自动将以下优化参数应用到仓库中的
    Node.js CI 配置:
      1. npm ci --prefer-offline     (离线优先安装)
      2. --maxWorkers=2              (jest worker 限制)
      3. --poolOptions.threads.maxThreads=2  (vitest 线程限制)

    扫描流程:
      1. 找出仓库中所有 package.json（排除 node_modules）
      2. 识别每个项目用的测试运行器（jest 或 vitest）
      3. 扫描 .github/workflows/ 下的 yml 文件
      4. 在含 npx jest / npx vitest 的命令中添加 worker 限制参数
      5. 在含 npm ci 的命令中添加 --prefer-offline

.PARAMETER DryRun
    仅预览将要修改的文件和内容，不实际修改（推荐先运行确认）

.PARAMETER Apply
    实际执行修改

.EXAMPLE
    .\scripts\dev\apply_nodejs_ci_optimization.ps1 -DryRun
    .\scripts\dev\apply_nodejs_ci_optimization.ps1 -Apply
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Apply
)

$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $ProjectRoot

if (-not $DryRun -and -not $Apply) {
    Write-Host "[ERROR] 请指定 -DryRun 或 -Apply 参数" -ForegroundColor Red
    Write-Host "  预览: .\scripts\dev\apply_nodejs_ci_optimization.ps1 -DryRun"
    Write-Host "  执行: .\scripts\dev\apply_nodejs_ci_optimization.ps1 -Apply"
    exit 1
}

$mode = if ($DryRun) { "[DryRun] 预览模式" } else { "[Apply] 执行模式" }
Write-Host "=== Node.js CI 优化参数自动化应用 ===" -ForegroundColor Cyan
Write-Host "模式: $mode" -ForegroundColor Yellow
Write-Host ""

# ── 步骤 1: 扫描 package.json，识别测试运行器 ──
Write-Host "[1/3] 扫描 Node.js 子项目..." -ForegroundColor Yellow

$nodeProjects = @()
$packageJsons = Get-ChildItem -Path . -Filter "package.json" -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch "node_modules" -and $_.FullName -notmatch "\.tmp" }

foreach ($pkg in $packageJsons) {
    $content = Get-Content $pkg.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    $testRunner = "unknown"
    $allDeps = @()
    if ($content.devDependencies) { $allDeps += $content.devDependencies.PSObject.Properties.Name }
    if ($content.dependencies) { $allDeps += $content.dependencies.PSObject.Properties.Name }

    if ($allDeps -contains "vitest") { $testRunner = "vitest" }
    elseif ($allDeps -contains "jest") { $testRunner = "jest" }

    $relPath = $pkg.FullName.Replace($ProjectRoot, "").TrimStart("\", "/")
    $nodeProjects += [PSCustomObject]@{
        Path = $relPath
        TestRunner = $testRunner
        Name = $content.name
    }
    Write-Host "  Found: $relPath (runner: $testRunner)" -ForegroundColor Green
}

if ($nodeProjects.Count -eq 0) {
    Write-Host "  [INFO] 未找到 Node.js 项目" -ForegroundColor Gray
    exit 0
}

# ── 步骤 2: 扫描 CI workflow 文件 ──
Write-Host "`n[2/3] 扫描 CI workflow 文件..." -ForegroundColor Yellow

$workflows = Get-ChildItem -Path ".github/workflows" -Filter "*.yml" -ErrorAction SilentlyContinue
$modifiedFiles = @()

foreach ($wf in $workflows) {
    $content = Get-Content $wf.FullName -Raw -Encoding UTF8
    $original = $content
    $changes = @()

    # 检测是否含 npm ci（未加 --prefer-offline 的）
    $npmCiCount = ([regex]::Matches($content, 'npm ci(?!\s+--prefer-offline)')).Count
    if ($npmCiCount -gt 0) {
        $content = $content -replace 'npm ci(?!\s+--prefer-offline)', 'npm ci --prefer-offline'
        $changes += "npm ci → npm ci --prefer-offline ($npmCiCount 处)"
    }

    # 检测是否含 npx jest（未加 --maxWorkers 的）
    $jestCount = ([regex]::Matches($content, 'npx jest(?!\s+.*--maxWorkers)')).Count
    if ($jestCount -gt 0) {
        # 在 npx jest 行的续行末尾添加 --maxWorkers=2
        # 简化处理：在 npx jest 命令的最后一个参数后添加
        $content = $content -replace '(npx jest[^\n]*?\\)\s*\n(\s*)([^\n\\]+)\s*\n', "`$1`n`$2`$3 --maxWorkers=2`n"
        $changes += "npx jest → 添加 --maxWorkers=2 ($jestCount 处)"
    }

    # 检测是否含 npx vitest（未加 --poolOptions 的）
    $vitestCount = ([regex]::Matches($content, 'npx vitest(?!\s+.*--poolOptions)')).Count
    if ($vitestCount -gt 0) {
        $content = $content -replace '(npx vitest[^\n]*?\\)\s*\n(\s*)([^\n\\]+)\s*\n', "`$1`n`$2`$3 --poolOptions.threads.maxThreads=2`n"
        $changes += "npx vitest → 添加 --poolOptions.threads.maxThreads=2 ($vitestCount 处)"
    }

    if ($content -ne $original) {
        $relPath = $wf.FullName.Replace($ProjectRoot, "").TrimStart("\", "/")
        $modifiedFiles += [PSCustomObject]@{
            File = $relPath
            Changes = $changes -join "; "
        }
        Write-Host "  [需更新] $relPath" -ForegroundColor Yellow
        foreach ($c in $changes) { Write-Host "    - $c" -ForegroundColor Gray }

        if ($Apply) {
            [System.IO.File]::WriteAllText($wf.FullName, $content, [System.Text.UTF8Encoding]::new($false))
            Write-Host "    [OK] 已更新" -ForegroundColor Green
        }
    }
}

# ── 步骤 3: 汇总报告 ──
Write-Host "`n[3/3] 汇总报告..." -ForegroundColor Yellow
Write-Host "  Node.js 项目数: $($nodeProjects.Count)"
Write-Host "  需更新的 workflow: $($modifiedFiles.Count)"

if ($modifiedFiles.Count -eq 0) {
    Write-Host "  [OK] 所有 CI workflow 已是最新优化状态，无需更新" -ForegroundColor Green
} elseif ($DryRun) {
    Write-Host "`n  以上为预览结果，执行 -Apply 参数实际修改" -ForegroundColor Cyan
} else {
    Write-Host "`n  [OK] 已完成 $($modifiedFiles.Count) 个 workflow 的优化参数应用" -ForegroundColor Green
}

# 输出项目清单
Write-Host "`n=== Node.js 项目清单 ===" -ForegroundColor Cyan
$nodeProjects | Format-Table Path, TestRunner, Name -AutoSize

Write-Host "`n=== 优化参数对照 ===" -ForegroundColor Cyan
Write-Host "  npm ci --prefer-offline                    (所有 Node.js 项目)"
Write-Host "  npx jest --maxWorkers=2                    (jest 项目)"
Write-Host "  npx vitest --poolOptions.threads.maxThreads=2  (vitest 项目)"
Write-Host ""
Write-Host "  预期优化效果: 内存降 ~43%, 时间降 ~36%"
