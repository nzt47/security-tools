﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿<#
.SYNOPSIS
    Pre-commit hook 中文路径边界测试（10 场景自动化）

.DESCRIPTION
    设计原则（三义校验）：
    - 不易：不污染 git 历史/工作区，每个场景独立隔离 + 测后清理
    - 变易：10 个边界场景覆盖纯中文/混合/特殊字符/深度路径/BOM 等
    - 简易：直接调用 precheck_docs.ps1 检测逻辑，不触发 git commit

    测试流程：
    1. 在 docs/zh/_边界测试/ 下创建 10 个独立的测试 markdown 文件
    2. 每个文件含 1 个特定场景的中文路径链接
    3. 调用 precheck_docs.ps1 检测，捕获 [BROKEN] 输出
    4. 比对预期结果，记录 PASS/FAIL
    5. 输出 Markdown 日志到 scripts/dev/logs/，并打印汇总表

.PARAMETER OutputDir
    日志输出目录（默认 scripts/dev/logs）

.PARAMETER KeepTestFiles
    保留测试文件（默认删除，便于调试时查看）

.EXAMPLE
    .\scripts\dev\test_hook_chinese_path_boundary.ps1
    .\scripts\dev\test_hook_chinese_path_boundary.ps1 -KeepTestFiles
#>
[CmdletBinding()]
param(
    [string]$OutputDir = "scripts\dev\logs",
    [switch]$KeepTestFiles
)

# Continue mode: avoid aborting on git stderr warnings
$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $ProjectRoot

# --- 测试目录与日志目录 ---
$TestDir = "docs\zh\_边界测试"
$LogDir = $OutputDir
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $LogDir "hook_chinese_path_test_$timestamp.md"

# --- 10 个边界场景定义 ---
# 每项：name, linkContent (引用文件内容), expectBroken (是否预期失效)
$scenarios = @(
    @{
        Name = "S1_pure_chinese_valid"
        Desc = "纯中文文件名 - 文件存在"
        RefContent = "- [目标](纯中文有效文件.md)"
        TargetFile = "纯中文有效文件.md"
        TargetContent = "# 有效"
        ExpectBroken = $false
    },
    @{
        Name = "S2_pure_chinese_missing"
        Desc = "纯中文文件名 - 文件不存在"
        RefContent = "- [目标](纯中文不存在文件.md)"
        TargetFile = $null
        TargetContent = $null
        ExpectBroken = $true
    },
    @{
        Name = "S3_chinese_english_mix"
        Desc = "中文+英文混合路径"
        RefContent = "- [目标](中文english混合.md)"
        TargetFile = "中文english混合.md"
        TargetContent = "# mixed"
        ExpectBroken = $false
    },
    @{
        Name = "S4_chinese_digits"
        Desc = "中文+数字路径"
        RefContent = "- [目标](中文123数字.md)"
        TargetFile = "中文123数字.md"
        TargetContent = "# 123"
        ExpectBroken = $false
    },
    @{
        Name = "S5_chinese_with_space"
        Desc = "中文路径含空格"
        RefContent = "- [目标](中文 路径 含空格.md)"
        TargetFile = "中文 路径 含空格.md"
        TargetContent = "# space"
        ExpectBroken = $false
    },
    @{
        Name = "S6_chinese_fullwidth_paren"
        Desc = "中文+全角括号（避开 Markdown 语法冲突）"
        RefContent = "- [目标](中文（说明）注释.md)"
        TargetFile = "中文（说明）注释.md"
        TargetContent = "# fullwidth"
        ExpectBroken = $false
    },
    @{
        Name = "S7_deep_chinese_path"
        Desc = "深度中文路径（3 层）"
        RefContent = "- [目标](一级目录/二级目录/三级文件.md)"
        TargetFile = "一级目录/二级目录/三级文件.md"
        TargetContent = "# deep"
        ExpectBroken = $false
    },
    @{
        Name = "S8_chinese_with_dot"
        Desc = "中文路径含多个点"
        RefContent = "- [目标](中文.说明.注释.md)"
        TargetFile = "中文.说明.注释.md"
        TargetContent = "# dots"
        ExpectBroken = $false
    },
    @{
        Name = "S9_chinese_renamed"
        Desc = "中文文件名重命名后失效"
        RefContent = "- [目标](原名.md)"
        TargetFile = "原名.md"
        TargetContent = "# original"
        RenameTo = "新名.md"
        ExpectBroken = $true
    },
    @{
        Name = "S10_rare_unicode"
        Desc = "生僻 Unicode 字符（CJK Ext）"
        RefContent = "- [目标](生僻字𠀀𠀁测试.md)"
        TargetFile = "生僻字𠀀𠀁测试.md"
        TargetContent = "# rare"
        ExpectBroken = $false
    },
    # ── S11-S18: 扩展场景（半角括号 / 特殊符号 / URL 编码） ──
    # 预期基于 precheck_docs.ps1 实际行为（非理想行为）：
    # - 正则 \[([^\]]+)\]\(([^)]+)\) 遇第一个 ) 截断
    # - 跳过 ^(https?|mailto:|file:///|#|/) 开头（# 在中间不跳过）
    # - [System.IO.File]::Exists 不做 URL 解码、不剥离 # 锚点、不识别 + 为空格
    @{
        Name = "S11_halfwidth_paren_basic"
        Desc = "半角括号失效 - 正则在第一个 ) 截断"
        RefContent = "- [目标](文件(1).md)"
        TargetFile = "文件(1).md"
        TargetContent = "# exists"
        ExpectBroken = $true
    },
    @{
        Name = "S12_halfwidth_paren_nested"
        Desc = "半角括号嵌套 - 多层括号同样截断"
        RefContent = "- [目标](a(b)c.md)"
        TargetFile = "a(b)c.md"
        TargetContent = "# nested"
        ExpectBroken = $true
    },
    @{
        Name = "S13_chinese_space_ampersand"
        Desc = "中文+空格+& 组合（& 在文件名合法，应通过）"
        RefContent = "- [目标](中文 & 英文.md)"
        TargetFile = "中文 & 英文.md"
        TargetContent = "# ampersand"
        ExpectBroken = $false
    },
    @{
        Name = "S14_hash_anchor_conflict"
        Desc = "# 锚点冲突 - 脚本不剥离锚点，整体当路径查找"
        RefContent = "- [目标](目标文件.md#章节)"
        TargetFile = "目标文件.md"
        TargetContent = "# anchored"
        ExpectBroken = $true
    },
    @{
        Name = "S15_percent_literal"
        Desc = "% 符号 - 文件名含 % 字面量（应通过）"
        RefContent = "- [目标](100%.md)"
        TargetFile = "100%.md"
        TargetContent = "# percent"
        ExpectBroken = $false
    },
    @{
        Name = "S16_plus_literal"
        Desc = "+ 符号 - 文件名含 + 字面量（应通过，验证 + 不被解释为空格）"
        RefContent = "- [目标](a+b.md)"
        TargetFile = "a+b.md"
        TargetContent = "# plus"
        ExpectBroken = $false
    },
    @{
        Name = "S17_url_encoded_literal_exists"
        Desc = "URL 编码路径 - 字面量文件存在（脚本不解码，按字面量查找应通过）"
        RefContent = "- [目标](%E4%B8%AD%E6%96%87.md)"
        TargetFile = "%E4%B8%AD%E6%96%87.md"
        TargetContent = "# literal"
        ExpectBroken = $false
    },
    @{
        Name = "S18_url_encoded_decoded_only"
        Desc = "URL 编码路径 - 仅解码后文件存在（字面量不存在，应 BROKEN）"
        RefContent = "- [目标](%E4%B8%AD%E6%96%87.md)"
        TargetFile = "中文.md"
        TargetContent = "# decoded"
        ExpectBroken = $true
    }
)

# --- 准备测试目录 ---
Write-Host "=== Pre-flight ===" -ForegroundColor Cyan
if (Test-Path $TestDir) {
    Remove-Item -Recurse -Force $TestDir
}
New-Item -ItemType Directory -Path $TestDir -Force | Out-Null
Write-Host "[OK] Test directory ready: $TestDir"

$utf8Bom = New-Object System.Text.UTF8Encoding $true
$results = @()

# --- 逐场景测试 ---
for ($i = 0; $i -lt $scenarios.Count; $i++) {
    $s = $scenarios[$i]
    $scenarioDir = Join-Path $TestDir $s.Name
    New-Item -ItemType Directory -Path $scenarioDir -Force | Out-Null

    $refPath = Join-Path $scenarioDir "ref.md"
    [System.IO.File]::WriteAllText($refPath, $s.RefContent + "`n", $utf8Bom)

    # 创建目标文件（如果场景需要）
    if ($s.TargetFile) {
        $targetFull = Join-Path $scenarioDir $s.TargetFile
        $targetDir = Split-Path $targetFull -Parent
        if (-not (Test-Path $targetDir)) {
            New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
        }
        [System.IO.File]::WriteAllText($targetFull, $s.TargetContent + "`n", $utf8Bom)
    }

    # 场景 S9：创建后重命名
    if ($s.RenameTo) {
        $original = Join-Path $scenarioDir $s.TargetFile
        $renamed = Join-Path $scenarioDir $s.RenameTo
        Rename-Item -Path $original -NewName $s.RenameTo
    }

    Write-Host "`n--- $($s.Name): $($s.Desc) ---" -ForegroundColor Yellow

    # 调用 precheck_docs.ps1 检测，捕获输出
    $output = & powershell -ExecutionPolicy Bypass -File scripts\dev\precheck_docs.ps1 -SkipChart -BlockMode -AllowBroken 0 2>&1 | Out-String

    # 解析输出：找当前 ref.md 的 BROKEN 行
    $refBasename = Split-Path $refPath -Leaf
    $isBroken = $output -match "BROKEN.*$refBasename"

    # 也检查是否包含其他文件的 BROKEN（理论上不应有）
    $allBroken = ([regex]::Matches($output, '\[BROKEN\] [^\r\n]+')).Count

    $passed = $false
    $actualBroken = $isBroken -or ($allBroken -gt 0)
    if ($s.ExpectBroken) {
        $passed = $actualBroken
    } else {
        $passed = -not $actualBroken
    }

    $status = if ($passed) { "PASS" } else { "FAIL" }
    $color = if ($passed) { "Green" } else { "Red" }
    Write-Host "  Expected broken: $($s.ExpectBroken) | Actual broken: $actualBroken | $status" -ForegroundColor $color

    $results += [PSCustomObject]@{
        Index = $i + 1
        Name = $s.Name
        Desc = $s.Desc
        ExpectBroken = $s.ExpectBroken
        ActualBroken = $actualBroken
        Status = $status
    }

    # 清理当前场景目录（避免下个场景受影响）
    if (-not $KeepTestFiles) {
        Remove-Item -Recurse -Force $scenarioDir
    }
}

# --- 最终清理 ---
if (-not $KeepTestFiles -and (Test-Path $TestDir)) {
    # 删除空测试目录
    $remaining = Get-ChildItem $TestDir -Recurse -Force
    if (-not $remaining) {
        Remove-Item -Force $TestDir
    }
}

# --- 输出汇总表 ---
Write-Host "`n=== Summary ===" -ForegroundColor Cyan
$passCount = ($results | Where-Object Status -eq "PASS").Count
$failCount = ($results | Where-Object Status -eq "FAIL").Count
Write-Host "Total: $($results.Count) | PASS: $passCount | FAIL: $failCount" -ForegroundColor $(if ($failCount -eq 0) { "Green" } else { "Red" })

Write-Host ""
Write-Host "| # | Scenario | Description | Expected | Actual | Status |" -ForegroundColor Cyan
Write-Host "|---|----------|-------------|----------|--------|--------|" -ForegroundColor Cyan
foreach ($r in $results) {
    $line = "| $($r.Index) | $($r.Name) | $($r.Desc) | $($r.ExpectBroken) | $($r.ActualBroken) | $($r.Status) |"
    $color = if ($r.Status -eq "PASS") { "Green" } else { "Red" }
    Write-Host $line -ForegroundColor $color
}

# --- 写入 Markdown 日志 ---
$logContent = @"
# Pre-commit Hook 中文路径边界测试报告

- **时间**: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
- **测试脚本**: scripts/dev/test_hook_chinese_path_boundary.ps1
- **测试目录**: $TestDir
- **保留测试文件**: $KeepTestFiles

## 汇总

| 指标 | 值 |
|------|-----|
| 总场景数 | $($results.Count) |
| PASS | $passCount |
| FAIL | $failCount |
| 通过率 | $([math]::Round($passCount / $results.Count * 100, 1))% |

## 详细结果

| # | 场景名 | 描述 | 预期失效 | 实际失效 | 状态 |
|---|--------|------|---------|---------|------|
$(
    $results | ForEach-Object {
        "| $($_.Index) | $($_.Name) | $($_.Desc) | $($_.ExpectBroken) | $($_.ActualBroken) | $($_.Status) |"
    } | ForEach-Object { "  $_" }
)

## 场景说明

$(
    $results | ForEach-Object {
        $i = $_.Index
        $s = $scenarios[$i - 1]
        "### $i. $($s.Name)`n`n- **描述**: $($s.Desc)`n- **链接内容**: \`$($s.RefContent)\``n- **目标文件**: $(if ($s.TargetFile) { $s.TargetFile } else { 'N/A' })`n- **重命名到**: $(if ($s.RenameTo) { $s.RenameTo } else { 'N/A' })`n- **预期失效**: $($s.ExpectBroken)`n- **实际失效**: $($_.ActualBroken)`n- **状态**: $($_.Status)`n"
    }
)

## 环境信息

- **OS**: $(if ($env:OS) { $env:OS } else { 'Windows' })
- **PowerShell**: $($PSVersionTable.PSVersion.ToString())
- **Pre-commit hook 阈值**: 0
- **被测脚本**: scripts/dev/precheck_docs.ps1
- **HEAD commit**: $(git rev-parse HEAD 2>&1 | Out-String).Trim()
"@

[System.IO.File]::WriteAllText($logFile, $logContent, $utf8Bom)
Write-Host "`n[OK] Log saved: $logFile" -ForegroundColor Green

# 退出码：所有场景通过则 0，否则 1
if ($failCount -gt 0) {
    exit 1
}
exit 0
