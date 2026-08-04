<#
.SYNOPSIS
    快速部署 TLM Overview 到 GitHub Pages（无需克隆，使用本地 worktree）

.DESCRIPTION
    高效方案：在本地 git worktree 创建 orphan gh-pages 分支，
    复制必要文件后推送。避免完整克隆仓库（仓库大时极慢）。

.PARAMETER Repo
    GitHub 仓库（owner/repo 格式），默认从 git remote origin 推断
#>
[CmdletBinding()]
param(
    [string]$Repo
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$SourceDoc = Join-Path $ProjectRoot "docs\TLM_OVERVIEW.md"

if (-not (Test-Path $SourceDoc)) {
    Write-Error "[!] 源文档不存在: $SourceDoc"
    exit 1
}

# 推断仓库地址
if (-not $Repo) {
    $remoteUrl = git -C $ProjectRoot remote get-url origin 2>$null
    if ($remoteUrl -match "github\.com[/:](.+?)(?:\.git)?$") {
        $Repo = $Matches[1]
    } else {
        Write-Error "[!] 无法推断仓库地址"
        exit 1
    }
}

Write-Host "=== TLM Overview 快速部署到 GitHub Pages ===" -ForegroundColor Cyan
Write-Host "仓库: $Repo"
Write-Host ""

# 使用临时 worktree 创建 orphan 分支
$worktreeDir = Join-Path $env:TEMP "gh-pages-deploy-$(Get-Random)"
$currentBranch = git -C $ProjectRoot branch --show-current

try {
    Write-Host "[1/6] 创建临时 worktree（orphan gh-pages 分支）..." -ForegroundColor Yellow
    # 在临时目录创建 orphan 分支（不含任何历史）
    git -C $ProjectRoot worktree add --detach $worktreeDir 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[!] worktree 创建失败"
        exit 1
    }

    # 在 worktree 中创建 orphan 分支
    git -C $worktreeDir checkout --orphan gh-pages 2>&1 | Out-Null
    # 清空 worktree 中的文件（orphan 分支初始状态）
    Get-ChildItem -Path $worktreeDir -Force | Where-Object {
        $_.Name -notin @('.git')
    } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    Write-Host "[2/6] 准备 Pages 目录结构..." -ForegroundColor Yellow
    $docsDir = Join-Path $worktreeDir "docs"
    New-Item -ItemType Directory -Path $docsDir -Force | Out-Null

    Write-Host "[3/6] 复制 TLM_OVERVIEW.md..." -ForegroundColor Yellow
    Copy-Item -Path $SourceDoc -Destination (Join-Path $docsDir "TLM_OVERVIEW.md") -Force

    # 复制性能图表（可选）
    $chartSrc = Join-Path $ProjectRoot "docs\perf-charts"
    if (Test-Path $chartSrc) {
        $chartDst = Join-Path $docsDir "perf-charts"
        New-Item -ItemType Directory -Path $chartDst -Force | Out-Null
        Copy-Item -Path "$chartSrc\*" -Destination $chartDst -Recurse -Force
        Write-Host "  [OK] 复制性能图表"
    }

    Write-Host "[4/6] 生成 index.html + _config.yml..." -ForegroundColor Yellow
    $indexHtml = @"
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="0; url=docs/TLM_OVERVIEW.md">
    <title>TLM 三层记忆架构总览</title>
</head>
<body>
    <p>正在跳转到 <a href="docs/TLM_OVERVIEW.md">TLM Overview</a>...</p>
</body>
</html>
"@
    Set-Content -Path (Join-Path $worktreeDir "index.html") -Value $indexHtml -Encoding utf8

    $configYml = @"
theme: jekyll-theme-markdown
markdown: kramdown
title: TLM 三层记忆架构总览
description: TLM 架构设计 + P3/P4 优化完整文档
"@
    Set-Content -Path (Join-Path $worktreeDir "_config.yml") -Value $configYml -Encoding utf8

    Write-Host "[5/6] 提交并强制推送 gh-pages 分支..." -ForegroundColor Yellow
    git -C $worktreeDir add . 2>&1 | Out-Null
    git -C $worktreeDir commit -m "docs(pages): 部署 TLM Overview 到 GitHub Pages" 2>&1 | Out-Null

    # 强制推送（orphan 分支需要 force）
    git -C $worktreeDir push origin gh-pages:gh-pages --force 2>&1

    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "[6/6] 部署成功！" -ForegroundColor Green
        $pagesUrl = "https://$($Repo.Split('/')[0]).github.io/$($Repo.Split('/')[-1])/"
        Write-Host "  访问: $pagesUrl" -ForegroundColor Cyan
        Write-Host "  注意: GitHub Pages 首次启用需在 Settings → Pages 选择 gh-pages 分支" -ForegroundColor Yellow
        Write-Host "  生效时间: 1-2 分钟" -ForegroundColor Yellow
    } else {
        Write-Error "[!] 推送失败，请检查权限"
    }
} finally {
    Write-Host ""
    Write-Host "[清理] 移除临时 worktree..." -ForegroundColor Gray
    git -C $ProjectRoot worktree remove $worktreeDir --force 2>&1 | Out-Null
    if (Test-Path $worktreeDir) {
        Remove-Item -Recurse -Force $worktreeDir -ErrorAction SilentlyContinue
    }
    # 确保切回原分支
    git -C $ProjectRoot checkout $currentBranch 2>&1 | Out-Null
}

Write-Host ""
Write-Host "=== 部署完成 ===" -ForegroundColor Cyan
