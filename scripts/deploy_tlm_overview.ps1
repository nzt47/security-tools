<#
.SYNOPSIS
    部署 docs/TLM_OVERVIEW.md 到 GitHub Wiki 或 GitHub Pages

.DESCRIPTION
    将 TLM 架构总览文档部署到项目的 Wiki 或 Pages，便于团队查阅。
    支持三种模式：
    - Wiki: 推送到 GitHub Wiki 仓库（推荐，原生支持 Markdown）
    - Pages: 推送到 gh-pages 分支（需构建静态站点）
    - Preview: 本地预览（仅打开浏览器）

.PARAMETER Mode
    部署模式：wiki (默认) | pages | preview

.PARAMETER Repo
    GitHub 仓库（owner/repo 格式），默认从 git remote origin 推断

.EXAMPLE
    .\scripts\deploy_tlm_overview.ps1 -Mode preview
    .\scripts\deploy_tlm_overview.ps1 -Mode wiki
    .\scripts\deploy_tlm_overview.ps1 -Mode pages

.NOTES
    前置条件：
    - Wiki 模式：需先在 GitHub 仓库网页端创建一次 Wiki（任意页面）以初始化 Wiki 仓库
    - Pages 模式：需在仓库 Settings → Pages 启用 gh-pages 分支
#>
[CmdletBinding()]
param(
    [ValidateSet("wiki", "pages", "preview")]
    [string]$Mode = "preview",

    [Parameter()]
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
        Write-Error "[!] 无法从 git remote 推断仓库地址，请用 -Repo 指定（owner/repo 格式）"
        exit 1
    }
}

Write-Host "=== TLM Overview 部署 ===" -ForegroundColor Cyan
Write-Host "模式: $Mode"
Write-Host "仓库: $Repo"
Write-Host "源文档: $SourceDoc"
Write-Host ""

# ── preview 模式：本地浏览器打开 ──
if ($Mode -eq "preview") {
    Write-Host "[Preview] 在默认浏览器打开 TLM_OVERVIEW.md..." -ForegroundColor Yellow
    Start-Process $SourceDoc
    Write-Host "[OK] 已打开预览" -ForegroundColor Green
    exit 0
}

# 检查 gh CLI
$ghCmd = Get-Command gh -ErrorAction SilentlyContinue
if (-not $ghCmd) {
    Write-Error "[!] 未找到 gh CLI，请先安装：https://cli.github.com/"
    exit 1
}

# 检查 gh 登录状态
$authStatus = gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "[!] gh CLI 未登录，请执行 'gh auth login'"
    exit 1
}

# ── Wiki 模式：推送到 GitHub Wiki 仓库 ──
if ($Mode -eq "wiki") {
    Write-Host "[Wiki] 准备推送到 GitHub Wiki..." -ForegroundColor Yellow

    # [网络适配] Wiki 仓库 URL：使用 SSH 协议（HTTPS 443 可能被阻塞）
    $wikiUrl = "git@github.com:$Repo.wiki.git"
    $wikiCloneDir = Join-Path $env:TEMP "tlm_wiki_deploy_$(Get-Random)"

    try {
        Write-Host "  克隆 Wiki 仓库到临时目录..."
        git clone $wikiUrl $wikiCloneDir 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Error "[!] Wiki 克隆失败。请确认已在 GitHub 网页端创建一次 Wiki 以初始化 Wiki 仓库。"
            Write-Error "    访问：https://github.com/$Repo/wiki"
            exit 1
        }

        # 复制文档（重命名为 Home.md 作为 Wiki 首页，或保留原名）
        $targetFile = Join-Path $wikiCloneDir "TLM-Overview.md"
        Copy-Item -Path $SourceDoc -Destination $targetFile -Force

        # 如果 Wiki 没有 Home.md，创建一个索引页
        $homeFile = Join-Path $wikiCloneDir "Home.md"
        if (-not (Test-Path $homeFile)) {
            $homeContent = @"
# $($Repo.Split('/')[-1]) Wiki

## 文档索引

- [TLM 三层记忆架构总览](./TLM-Overview) - TLM 架构设计 + P3/P4 优化完整文档
"@
            Set-Content -Path $homeFile -Value $homeContent -Encoding utf8
        } else {
            # 在 Home.md 末尾追加链接（如果不存在）
            $homeContent = Get-Content $homeFile -Raw
            if ($homeContent -notmatch "TLM-Overview") {
                $append = "`n- [TLM 三层记忆架构总览](./TLM-Overview) - TLM 架构设计 + P3/P4 优化完整文档`n"
                Add-Content -Path $homeFile -Value $append -Encoding utf8
            }
        }

        # 提交并推送
        git -C $wikiCloneDir add .
        git -C $wikiCloneDir commit -m "docs(wiki): 部署 TLM Overview 总览文档" 2>&1 | Out-Null
        git -C $wikiCloneDir push origin master 2>&1

        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "[OK] Wiki 部署成功！" -ForegroundColor Green
            Write-Host "  访问：https://github.com/$Repo/wiki/TLM-Overview" -ForegroundColor Cyan
        } else {
            Write-Error "[!] Wiki 推送失败，请检查权限"
        }
    } finally {
        # 清理临时目录
        if (Test-Path $wikiCloneDir) {
            Remove-Item -Recurse -Force $wikiCloneDir -ErrorAction SilentlyContinue
        }
    }
}

# ── Pages 模式：推送到 gh-pages 分支 ──
if ($Mode -eq "pages") {
    Write-Host "[Pages] 准备推送到 gh-pages 分支..." -ForegroundColor Yellow

    $workDir = Join-Path $env:TEMP "tlm_pages_deploy_$(Get-Random)"

    try {
        # [安全修复] 无论 gh-pages 是否存在，都使用独立克隆目录操作
        # Why: 原脚本在主项目目录删除文件会造成代码丢失风险
        # [网络适配] 使用 SSH 协议（HTTPS 443 可能被阻塞，SSH 22 通常可用）
        $pagesUrl = "git@github.com:$Repo.git"
        Write-Host "  克隆仓库到独立临时目录（避免污染主工作区）..."

        # 检查 gh-pages 分支是否存在
        $branchCheck = git ls-remote --heads $pagesUrl gh-pages 2>&1
        $ghPagesExists = $branchCheck -match "gh-pages"

        if ($ghPagesExists) {
            # 分支存在：克隆时指定分支
            git clone --branch gh-pages $pagesUrl $workDir 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Error "[!] 克隆 gh-pages 分支失败"
                exit 1
            }
        } else {
            # 分支不存在：完整克隆后创建 orphan 分支（不含历史，干净起步）
            Write-Host "  gh-pages 分支不存在，创建 orphan 分支..."
            git clone $pagesUrl $workDir 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Error "[!] 克隆仓库失败"
                exit 1
            }
            # 清空工作区文件（仅在临时克隆中操作，不影响主项目）
            Get-ChildItem -Path $workDir -Force | Where-Object {
                $_.Name -notin @('.git')
            } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
            # 创建 orphan 分支
            git -C $workDir checkout --orphan gh-pages 2>&1 | Out-Null
        }

        # 准备 Pages 目录结构
        $pagesDir = $workDir
        $docsDir = Join-Path $pagesDir "docs"
        if (-not (Test-Path $docsDir)) {
            New-Item -ItemType Directory -Path $docsDir -Force | Out-Null
        }

        # 复制文档
        Copy-Item -Path $SourceDoc -Destination (Join-Path $docsDir "TLM_OVERVIEW.md") -Force

        # 创建 index.html 作为重定向页（Markdown 在 Pages 中需要 _config.yml 支持 Jekyll）
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
        Set-Content -Path (Join-Path $pagesDir "index.html") -Value $indexHtml -Encoding utf8

        # 创建 _config.yml 启用 Jekyll（GitHub Pages 原生渲染 Markdown）
        $configYml = @"
theme: jekyll-theme-markdown
markdown: kramdown
title: TLM 三层记忆架构总览
description: TLM 架构设计 + P3/P4 优化完整文档
"@
        Set-Content -Path (Join-Path $pagesDir "_config.yml") -Value $configYml -Encoding utf8

        # 提交并推送
        git -C $pagesDir add .
        git -C $pagesDir commit -m "docs(pages): 部署 TLM Overview 到 GitHub Pages" 2>&1 | Out-Null
        git -C $pagesDir push origin gh-pages 2>&1

        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "[OK] Pages 部署成功！" -ForegroundColor Green
            Write-Host "  访问：https://$($Repo.Split('/')[0]).github.io/$($Repo.Split('/')[-1])/" -ForegroundColor Cyan
            Write-Host "  注意：GitHub Pages 首次启用后需 1-2 分钟生效" -ForegroundColor Yellow
        } else {
            Write-Error "[!] Pages 推送失败，请检查 gh-pages 分支权限"
        }
    } finally {
        # [安全修复] 清理独立克隆目录（不使用 worktree，避免影响主项目）
        if ($workDir -and (Test-Path $workDir)) {
            Remove-Item -Recurse -Force $workDir -ErrorAction SilentlyContinue
        }
    }
}

Write-Host ""
Write-Host "=== 部署完成 ===" -ForegroundColor Cyan
