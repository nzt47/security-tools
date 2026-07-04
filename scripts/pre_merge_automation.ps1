# PR 合并前自动化验证脚本 - 确保所有阻塞项已解决
# 基于 docs/observability/pr_pre_merge_checklist.md 生成，覆盖：
# 1. 环境预检查（分支、gh CLI、认证、网络）
# 2. 工作区清洁度检查（未跟踪文件、待推送提交）
# 3. 自动化测试运行（113 个专项测试）
# 4. PR 描述文件完整性检查（4 个 mermaid 图表）
# 5. 语法检查（py_compile）
# 6. 推送与 PR 创建（可选，需 -Execute 开关）
#
# 使用方式：
#   干运行（仅检查，不推送/创建 PR）：
#       .\scripts\pre_merge_automation.ps1
#   完整执行（推送 + 创建 PR）：
#       .\scripts\pre_merge_automation.ps1 -Execute
#   跳过测试（快速检查）：
#       .\scripts\pre_merge_automation.ps1 -SkipTests

[CmdletBinding()]
param(
    [switch]$Execute,
    [switch]$SkipTests,
    [string]$BaseBranch = "master",
    [string]$HeadBranch = "phase2-visibility-convergence",
    [string]$PrTitle = "log_dict 重构 — 消除双重序列化，提升日志系统性能",
    [string]$PrBodyFile = "docs/observability/pr_description_phase2_log_dict.md"
)

$ErrorActionPreference = "Continue"
$script:Checks = @()
$script:Blockers = @()
$script:Warnings = @()

function Add-Check {
    param([string]$Name, [string]$Status, [string]$Detail = "", [bool]$IsBlocker = $false)
    $script:Checks += [PSCustomObject]@{
        Name = $Name
        Status = $Status
        Detail = $Detail
        IsBlocker = $IsBlocker
    }
    if ($IsBlocker -and $Status -ne "PASS") {
        $script:Blockers += "$Name : $Detail"
    }
    if ($Status -eq "WARN") {
        $script:Warnings += "$Name : $Detail"
    }
}

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}

function Invoke-Command {
    param([scriptblock]$Block)
    & $Block 2>&1
}

# ============================================================
# 步骤 1：环境预检查
# ============================================================
Write-Section "步骤 1：环境预检查"

# 1.1 当前分支
$currentBranch = git branch --show-current
if ($currentBranch -eq $HeadBranch) {
    Add-Check "当前分支" "PASS" "当前在 $HeadBranch 分支"
} else {
    Add-Check "当前分支" "FAIL" "当前在 $currentBranch，应为 $HeadBranch" $true
}

# 1.2 base 分支存在
$baseExists = git rev-parse --verify $BaseBranch 2>$null
if ($LASTEXITCODE -eq 0) {
    Add-Check "Base 分支" "PASS" "$BaseBranch 存在"
} else {
    Add-Check "Base 分支" "FAIL" "$BaseBranch 不存在" $true
}

# 1.3 gh CLI 安装
$ghCmd = Get-Command gh -ErrorAction SilentlyContinue
if ($ghCmd) {
    $ghVersion = (gh --version | Select-Object -First 1)
    Add-Check "gh CLI" "PASS" $ghVersion
} else {
    Add-Check "gh CLI" "FAIL" "gh 未安装" $true
}

# 1.4 gh 认证状态
if ($ghCmd) {
    $authStatus = gh auth status 2>&1
    # 注意：不能用 -match "Logged in"，因为 "not logged into" 也包含 "logged in"
    if ($authStatus -notmatch "not logged into") {
        Add-Check "gh 认证" "PASS" "已登录"
    } else {
        Add-Check "gh 认证" "FAIL" "未登录（需运行 gh auth login）" $true
    }
} else {
    Add-Check "gh 认证" "SKIP" "gh 未安装，跳过"
}

# 1.5 网络连通性（GitHub）
$networkOK = Test-NetConnection -ComputerName "github.com" -Port 443 -WarningAction SilentlyContinue
if ($networkOK.TcpTestSucceeded) {
    Add-Check "网络连通" "PASS" "github.com:443 可达"
} else {
    Add-Check "网络连通" "FAIL" "github.com:443 不可达" $true
}

# ============================================================
# 步骤 2：工作区清洁度检查
# ============================================================
Write-Section "步骤 2：工作区清洁度检查"

# 2.1 待推送提交数
$localHead = git rev-parse HEAD
$remoteHead = git rev-parse "origin/$HeadBranch" 2>$null
if ($localHead -eq $remoteHead) {
    Add-Check "推送状态" "PASS" "本地与远程同步"
    $pendingPush = 0
} else {
    $pendingPush = (git log --oneline "origin/$HeadBranch..HEAD" | Measure-Object -Line).Lines
    Add-Check "推送状态" "WARN" "待推送 $pendingPush 个提交（本地 $localHead → 远程 $remoteHead）"
}

# 2.2 未跟踪文件数（log_dict 相关）
$untrackedLogDict = git ls-files --others --exclude-standard |
    Select-String -Pattern "log_dict|perf_monitor|migrate_to_log_dict|test_log_dict_performance|test_memory_comparison|log_alert_rules|pr_description_phase2|phase2_branch_leftover|log_dict_refactoring_summary|log-perf-guard" |
    Measure-Object -Line |
    Select-Object -ExpandProperty Lines

if ($untrackedLogDict -eq 0) {
    Add-Check "log_dict 相关未跟踪文件" "PASS" "无未提交的 log_dict 相关文件"
} else {
    Add-Check "log_dict 相关未跟踪文件" "WARN" "发现 $untrackedLogDict 个未跟踪的 log_dict 相关文件"
}

# 2.3 .gitignore 关键模式
$gitignoreContent = Get-Content .gitignore -Raw -ErrorAction SilentlyContinue
$requiredPatterns = @("scripts/_\*\.py", "unit_run\.log", "\.file_backups/", "tests/unit/temp/")
$missingPatterns = @()
foreach ($pattern in $requiredPatterns) {
    if ($gitignoreContent -notmatch $pattern) {
        $missingPatterns += $pattern
    }
}
if ($missingPatterns.Count -eq 0) {
    Add-Check ".gitignore 模式" "PASS" "所有关键模式已配置"
} else {
    Add-Check ".gitignore 模式" "WARN" "缺失模式: $($missingPatterns -join ', ')"
}

# 2.4 领先提交数
$commitsAhead = (git log --oneline "$BaseBranch..HEAD" | Measure-Object -Line).Lines
Add-Check "领先提交数" "INFO" "分支领先 $BaseBranch 共 $commitsAhead 个提交"

# ============================================================
# 步骤 3：PR 描述文件完整性检查
# ============================================================
Write-Section "步骤 3：PR 描述文件完整性检查"

if (Test-Path $PrBodyFile) {
    $bodyContent = Get-Content $PrBodyFile -Raw
    $bodySize = (Get-Item $PrBodyFile).Length

    # 检查 mermaid 图表数
    $mermaidCount = ([regex]::Matches($bodyContent, '^```mermaid', [System.Text.RegularExpressions.RegexOptions]::Multiline)).Count
    if ($mermaidCount -ge 4) {
        Add-Check "PR 描述 mermaid 图表" "PASS" "发现 $mermaidCount 个 mermaid 图表（要求 ≥ 4）"
    } else {
        Add-Check "PR 描述 mermaid 图表" "WARN" "仅发现 $mermaidCount 个 mermaid 图表（要求 ≥ 4）"
    }

    # 检查关键性能数据
    $hasSpeedup = $bodyContent -match "3\.93x"
    $hasThroughput = $bodyContent -match "74\.71%"
    $hasP99 = $bodyContent -match "47\.66%"
    if ($hasSpeedup -and $hasThroughput -and $hasP99) {
        Add-Check "PR 描述性能数据" "PASS" "关键性能数据齐全（3.93x / 74.71% / -47.66%）"
    } else {
        Add-Check "PR 描述性能数据" "WARN" "性能数据不完整"
    }

    # 检查已知问题数据更新
    if ($bodyContent -match "40 个.*非本 PR 引入") {
        Add-Check "PR 描述失败数更新" "PASS" "预存失败数已更新为 40"
    } else {
        Add-Check "PR 描述失败数更新" "WARN" "预存失败数可能未更新"
    }

    Add-Check "PR 描述文件" "PASS" "文件存在，大小 $bodySize 字节"
} else {
    Add-Check "PR 描述文件" "FAIL" "文件不存在: $PrBodyFile" $true
}

# ============================================================
# 步骤 4：语法检查
# ============================================================
Write-Section "步骤 4：语法检查（py_compile）"

$coreFiles = @(
    "agent/logging_utils.py",
    "agent/utils/perf_monitor.py",
    "agent/utils/sensitive_data_filter.py",
    "scripts/fix_config_secure_tests.py"
)

$syntaxOK = $true
foreach ($file in $coreFiles) {
    if (Test-Path $file) {
        python -m py_compile $file 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Add-Check "py_compile $file" "PASS" "语法正确"
        } else {
            Add-Check "py_compile $file" "FAIL" "语法错误" $true
            $syntaxOK = $false
        }
    } else {
        Add-Check "py_compile $file" "WARN" "文件不存在"
    }
}

# ============================================================
# 步骤 5：自动化测试
# ============================================================
if (-not $SkipTests) {
    Write-Section "步骤 5：自动化测试（113 个专项测试）"

    $testFiles = @(
        "tests/unit/test_log_dict_performance.py",
        "tests/unit/test_memory_comparison.py",
        "tests/unit/test_log_system_safe_logger.py",
        "tests/unit/test_config_secure.py",
        "tests/unit/test_perf_monitor.py"
    )

    $allTestFilesExist = $true
    foreach ($file in $testFiles) {
        if (-not (Test-Path $file)) {
            Add-Check "测试文件 $file" "FAIL" "文件不存在" $true
            $allTestFilesExist = $false
        }
    }

    if ($allTestFilesExist) {
        Write-Host "运行 pytest..." -ForegroundColor Yellow
        $testOutput = python -m pytest $testFiles --tb=no -q --no-header 2>&1
        $testExitCode = $LASTEXITCODE

        # 解析测试结果
        $passedMatch = [regex]::Match($testOutput, '(\d+) passed')
        $skippedMatch = [regex]::Match($testOutput, '(\d+) skipped')
        $failedMatch = [regex]::Match($testOutput, '(\d+) failed')

        $passed = if ($passedMatch.Success) { [int]$passedMatch.Groups[1].Value } else { 0 }
        $skipped = if ($skippedMatch.Success) { [int]$skippedMatch.Groups[1].Value } else { 0 }
        $failed = if ($failedMatch.Success) { [int]$failedMatch.Groups[1].Value } else { 0 }

        if ($failed -eq 0 -and $passed -ge 100) {
            Add-Check "自动化测试" "PASS" "$passed passed, $skipped skipped, $failed failed"
        } elseif ($failed -eq 0) {
            Add-Check "自动化测试" "WARN" "$passed passed, $skipped skipped, $failed failed（通过数偏低）"
        } else {
            Add-Check "自动化测试" "FAIL" "$passed passed, $skipped skipped, $failed failed" $true
        }

        Write-Host "  通过: $passed / 跳过: $skipped / 失败: $failed" -ForegroundColor $(if ($failed -eq 0) { 'Green' } else { 'Red' })
    }
} else {
    Write-Section "步骤 5：自动化测试（已跳过）"
    Add-Check "自动化测试" "SKIP" "已通过 -SkipTests 跳过"
}

# ============================================================
# 步骤 6：检查结果汇总
# ============================================================
Write-Section "检查结果汇总"

$passCount = ($script:Checks | Where-Object Status -eq "PASS").Count
$warnCount = ($script:Checks | Where-Object Status -eq "WARN").Count
$failCount = ($script:Checks | Where-Object Status -eq "FAIL").Count
$skipCount = ($script:Checks | Where-Object Status -eq "SKIP").Count
$infoCount = ($script:Checks | Where-Object Status -eq "INFO").Count

Write-Host ""
Write-Host "  PASS: $passCount  WARN: $warnCount  FAIL: $failCount  SKIP: $skipCount  INFO: $infoCount" -ForegroundColor Yellow
Write-Host ""

# 输出详细检查结果
$script:Checks | Format-Table -AutoSize -Property Name, Status, Detail

if ($script:Blockers.Count -gt 0) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Red
    Write-Host "  阻塞项（$($script:Blockers.Count) 个）" -ForegroundColor Red
    Write-Host "========================================" -ForegroundColor Red
    $script:Blockers | ForEach-Object { Write-Host "  ❌ $_" -ForegroundColor Red }
}

if ($script:Warnings.Count -gt 0) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host "  警告项（$($script:Warnings.Count) 个）" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Yellow
    $script:Warnings | ForEach-Object { Write-Host "  ⚠️  $_" -ForegroundColor Yellow }
}

# ============================================================
# 步骤 7：执行推送与 PR 创建（仅 -Execute 模式）
# ============================================================
if ($Execute) {
    Write-Section "步骤 7：执行推送与 PR 创建"

    if ($script:Blockers.Count -gt 0) {
        Write-Host "存在阻塞项，无法执行推送与 PR 创建。请先解决阻塞项。" -ForegroundColor Red
        Write-Host ""
        Write-Host "常见阻塞项解决方案：" -ForegroundColor Yellow
        Write-Host "  1. gh 未认证: 运行 gh auth login"
        Write-Host "  2. 网络不通: 检查网络连接或代理设置"
        Write-Host "  3. 当前分支错误: git checkout $HeadBranch"
        exit 1
    }

    # 7.1 推送
    if ($pendingPush -gt 0) {
        Write-Host "[7.1] 推送 $pendingPush 个提交到 origin/$HeadBranch..." -ForegroundColor Cyan
        git push origin $HeadBranch
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  ✅ 推送成功" -ForegroundColor Green
        } else {
            Write-Host "  ❌ 推送失败" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "[7.1] 本地与远程已同步，无需推送" -ForegroundColor Green
    }

    # 7.2 创建 PR
    Write-Host "[7.2] 创建 PR..." -ForegroundColor Cyan
    Write-Host "  --base $BaseBranch" -ForegroundColor Gray
    Write-Host "  --head $HeadBranch" -ForegroundColor Gray
    Write-Host "  --title `"$PrTitle`"" -ForegroundColor Gray
    Write-Host "  --body-file $PrBodyFile" -ForegroundColor Gray
    Write-Host ""

    $prResult = gh pr create --base $BaseBranch --head $HeadBranch --title $PrTitle --body-file $PrBodyFile 2>&1

    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✅ PR 创建成功" -ForegroundColor Green
        Write-Host "  PR URL: $prResult" -ForegroundColor Green
        Write-Host ""
        Write-Host "  后续操作：" -ForegroundColor Yellow
        Write-Host "    1. 添加 reviewer"
        Write-Host "    2. 添加 label（如: observability, performance, refactor）"
        Write-Host "    3. 等待 CI 全绿后合并"
    } else {
        Write-Host "  ❌ PR 创建失败" -ForegroundColor Red
        Write-Host "  错误: $prResult" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Section "步骤 7：执行推送与 PR 创建（dry-run 模式）"
    Write-Host "  当前为 dry-run 模式，未实际执行推送与 PR 创建。" -ForegroundColor Gray
    Write-Host "  如需执行，请运行:" -ForegroundColor Yellow
    Write-Host "    .\scripts\pre_merge_automation.ps1 -Execute" -ForegroundColor Cyan
}

# ============================================================
# 最终结论
# ============================================================
Write-Section "最终结论"

if ($script:Blockers.Count -eq 0) {
    if ($script:Warnings.Count -eq 0) {
        Write-Host "  ✅ 所有检查通过，无阻塞项，无警告项" -ForegroundColor Green
        Write-Host "  建议: 运行 .\scripts\pre_merge_automation.ps1 -Execute 执行推送与 PR 创建" -ForegroundColor Green
    } else {
        Write-Host "  ✅ 所有检查通过，无阻塞项（但有 $($script:Warnings.Count) 个警告项）" -ForegroundColor Green
        Write-Host "  警告项不阻塞合并，但建议关注" -ForegroundColor Yellow
        Write-Host "  建议: 运行 .\scripts\pre_merge_automation.ps1 -Execute 执行推送与 PR 创建" -ForegroundColor Green
    }
    exit 0
} else {
    Write-Host "  ❌ 存在 $($script:Blockers.Count) 个阻塞项，需先解决才能合并" -ForegroundColor Red
    exit 1
}
