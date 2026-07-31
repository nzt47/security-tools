﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿<#
.SYNOPSIS
    批量修复 docs/ 中的失效 Markdown 链接

.DESCRIPTION
    分析并修复 precheck_docs.ps1 扫描出的失效链接，按策略处理：
    1. 路径纠正（自动修复）
    2. 已删除文件标记
    3. 特殊字符处理

.PARAMETER DryRun
    仅显示修复方案，不实际修改文件

.PARAMETER File
    仅修复指定文件（默认修复所有）

.EXAMPLE
    .\scripts\dev\fix_broken_links.ps1 -DryRun
    .\scripts\dev\fix_broken_links.ps1
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$File
)

$ErrorActionPreference = 'Continue'
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $ProjectRoot

Write-Host '=== 失效链接批量修复 ===' -ForegroundColor Cyan
if ($DryRun) {
    Write-Host '[DryRun] 仅显示修复方案，不修改文件' -ForegroundColor Yellow
} else {
    Write-Host '[EXEC] 将实际修改文件' -ForegroundColor Yellow
}
Write-Host ''

# 已知被 BFG/历史清理删除的文件
$script:cleanedFiles = @(
    'BFG_CLEANUP_REPORT_20260719.md',
    'DEEPSEEK_KEY_REVOKE_GUIDE.md'
)

# 修复统计
$stats = @{
    totalBroken    = 0
    fixedPath      = 0
    markedCleaned  = 0
    markedPrivate  = 0
    markedMissing  = 0
    markedSpecial  = 0
    skipped        = 0
    filesModified  = @()
}

function Test-LinkBroken {
    param([string]$MdFile, [string]$LinkPath)
    if ($LinkPath -match '^(https?|mailto:|file:///|#|/)') { return $false }
    $basePath = Split-Path $MdFile -Parent
    $fullPath = Join-Path $basePath $LinkPath
    return -not (Test-Path $fullPath)
}

function Get-FixAction {
    param([string]$MdFile, [string]$LinkPath, [string]$LinkText)

    # 类型 1: .claude/plans/ 私人目录
    if ($LinkPath -match '\.claude/plans/') {
        return @{
            Action = 'MarkPrivate'
            Reason = '引用 .claude/ 私人目录'
            NewText = "~~$LinkText~~ " + [char]0x1F512 + " (内部计划文档，不入库)"
        }
    }

    # 类型 2: BFG 清理已删除文件
    if ($LinkPath -match 'BFG_CLEANUP_REPORT' -or $LinkPath -match 'DEEPSEEK_KEY_REVOKE_GUIDE') {
        $fileName = Split-Path $LinkPath -Leaf
        return @{
            Action = 'MarkCleaned'
            Reason = "文件 $fileName 已被 BFG 历史清理删除"
            NewText = "~~$LinkText~~ " + [char]0x1F512 + " (历史已清理)"
        }
    }

    # 类型 3: 特殊字符链接（含 ?! 或 |）
    if ($LinkPath -match '[\?\!]\!|^\?!|\|') {
        return @{
            Action = 'MarkSpecial'
            Reason = '链接含特殊字符，应为代码引用'
            NewText = '``' + $LinkPath + '``'
        }
    }

    # 类型 4: 在 docs/ 内引用 ../docs/xxx（冗余路径）
    if ($MdFile -match '^docs/' -and $LinkPath -match '^\.\./docs/') {
        $newPath = $LinkPath -replace '^\.\./docs/', './'
        $basePath = Split-Path $MdFile -Parent
        $fullPath = Join-Path $basePath $newPath
        if (Test-Path $fullPath) {
            return @{
                Action = 'FixPath'
                Reason = '../docs/ 路径冗余'
                NewText = "[$LinkText]($newPath)"
            }
        }
    }

    # 类型 5: 在 docs/ 子目录引用 docs/xxx
    if ($MdFile -match '^docs/' -and $LinkPath -match '^docs/') {
        $newPath = $LinkPath -replace '^docs/', './'
        $basePath = Split-Path $MdFile -Parent
        $fullPath = Join-Path $basePath $newPath
        if (Test-Path $fullPath) {
            return @{
                Action = 'FixPath'
                Reason = 'docs/ 前缀冗余'
                NewText = "[$LinkText]($newPath)"
            }
        }
    }

    # 类型 6: monitoring/ 已迁移到 deploy/monitoring/
    if ($LinkPath -match '^monitoring/') {
        $fileName = Split-Path $LinkPath -Leaf
        $newPath = "../deploy/monitoring/$fileName"
        $basePath = Split-Path $MdFile -Parent
        $fullPath = Join-Path $basePath $newPath
        if (Test-Path $fullPath) {
            return @{
                Action = 'FixPath'
                Reason = 'monitoring/ 已迁移到 deploy/monitoring/'
                NewText = "[$LinkText]($newPath)"
            }
        }
    }

    # 类型 7: 引用代码文件（不存在）
    if ($LinkPath -match '^\.\./(tests|scripts|agent|deploy|docker|monitoring)/') {
        $fileName = Split-Path $LinkPath -Leaf
        return @{
            Action = 'MarkMissing'
            Reason = "代码文件不存在: $fileName"
            NewText = "~~$LinkText~~ " + [char]0x26A0 + " (待确认: $fileName)"
        }
    }

    # 类型 8: kubeconfig 文件
    if ($LinkPath -match '^(setup-kubeconfig|test-kubeconfig|kubeconfig)') {
        return @{
            Action = 'MarkMissing'
            Reason = 'kubeconfig 脚本已删除'
            NewText = "~~$LinkText~~ " + [char]0x26A0 + " (已删除)"
        }
    }

    # 类型 9: 含锚点的链接，检查文件部分
    if ($LinkPath -match '^(.+?)#(.+)$') {
        $filePart = $Matches[1]
        if ($filePart) {
            $basePath = Split-Path $MdFile -Parent
            $fullPath = Join-Path $basePath $filePart
            if (-not (Test-Path $fullPath)) {
                return @{
                    Action = 'MarkMissing'
                    Reason = '锚点链接的目标文件不存在'
                    NewText = "~~$LinkText~~ " + [char]0x26A0 + " (文件缺失)"
                }
            }
        }
    }

    # 类型 10: 默认标记
    return @{
        Action = 'MarkMissing'
        Reason = '目标文件不存在'
        NewText = "~~$LinkText~~ " + [char]0x26A0 + " (待确认)"
    }
}

function Fix-File {
    param([string]$MdFile)

    $filePath = Join-Path $ProjectRoot $MdFile
    if (-not (Test-Path $filePath)) { return }

    $content = Get-Content $filePath -Raw
    $originalContent = $content
    $fileFixed = 0

    $matches = [regex]::Matches($content, '\[([^\]]+)\]\(([^)]+)\)')
    $sortedMatches = $matches | Sort-Object -Property Index -Descending

    foreach ($match in $sortedMatches) {
        $fullMatch = $match.Value
        $linkText = $match.Groups[1].Value
        $linkPath = $match.Groups[2].Value

        if ($linkPath -match '^(https?|mailto:|file:///|#|/)') { continue }
        if (-not (Test-LinkBroken -MdFile $MdFile -LinkPath $linkPath)) { continue }

        $stats.totalBroken++

        $fix = Get-FixAction -MdFile $MdFile -LinkPath $linkPath -LinkText $linkText

        $color = switch ($fix.Action) {
            'FixPath' { 'Green' }
            'MarkCleaned' { 'DarkYellow' }
            'MarkPrivate' { 'DarkYellow' }
            'MarkMissing' { 'Yellow' }
            'MarkSpecial' { 'Yellow' }
            default { 'Gray' }
        }

        Write-Host "  [$($fix.Action)] $MdFile" -ForegroundColor $color
        Write-Host "    原链接: $fullMatch"
        Write-Host "    修复为: $($fix.NewText)"
        Write-Host "    原因: $($fix.Reason)`n"

        if (-not $DryRun) {
            $content = $content.Remove($match.Index, $match.Length).Insert($match.Index, $fix.NewText)
            $fileFixed++
        }

        switch ($fix.Action) {
            'FixPath' { $stats.fixedPath++ }
            'MarkCleaned' { $stats.markedCleaned++ }
            'MarkPrivate' { $stats.markedPrivate++ }
            'MarkMissing' { $stats.markedMissing++ }
            'MarkSpecial' { $stats.markedSpecial++ }
            default { $stats.skipped++ }
        }
    }

    if (-not $DryRun -and $content -ne $originalContent -and $fileFixed -gt 0) {
        Set-Content -Path $filePath -Value $content -NoNewline -Encoding utf8
        $stats.filesModified += $MdFile
        Write-Host "  [SAVED] $MdFile (修复 $fileFixed 处)" -ForegroundColor Green
    }
}

# ── 主流程 ──
Write-Host '[1/2] 扫描失效链接...`n' -ForegroundColor Yellow

if ($File) {
    Fix-File -MdFile $File
} else {
    Get-ChildItem -Path docs -Filter '*.md' -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        $relPath = (Resolve-Path $_.FullName -Relative) -replace '^\.\\', '' -replace '\\', '/'
        Fix-File -MdFile $relPath
    }
}

# ── 统计报告 ──
Write-Host ''
Write-Host '=== 修复统计 ===' -ForegroundColor Cyan
Write-Host "  扫描到失效链接总数: $($stats.totalBroken)"
Write-Host "  路径纠正 (绿色): $($stats.fixedPath)" -ForegroundColor Green
Write-Host "  标记已清理 (历史): $($stats.markedCleaned)" -ForegroundColor DarkYellow
Write-Host "  标记私人目录: $($stats.markedPrivate)" -ForegroundColor DarkYellow
Write-Host "  标记待确认 (黄色): $($stats.markedMissing)" -ForegroundColor Yellow
Write-Host "  特殊字符处理: $($stats.markedSpecial)" -ForegroundColor Yellow
Write-Host "  跳过 (未处理): $($stats.skipped)" -ForegroundColor Gray
Write-Host ''
if (-not $DryRun) {
    Write-Host "  修改的文件 ($($stats.filesModified.Count)):" -ForegroundColor Green
    $stats.filesModified | ForEach-Object { Write-Host "    $_" -ForegroundColor Green }
} else {
    Write-Host '  [DryRun] 未修改任何文件' -ForegroundColor Yellow
    Write-Host '  实际修复: 移除 -DryRun 参数' -ForegroundColor Yellow
}
Write-Host ''
Write-Host '=== 完成 ===' -ForegroundColor Cyan
Write-Host '  下一步: 运行 precheck 验证修复效果'
Write-Host '    .\scripts\dev\precheck_docs.ps1 -SkipChart'
Write-Host ''
Write-Host "  提交修复: git add docs/ && git commit -m 'docs: 批量修复失效链接'"
