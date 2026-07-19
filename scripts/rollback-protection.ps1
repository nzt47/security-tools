<#
.SYNOPSIS
    Skills Check Branch Protection 紧急回滚脚本

.DESCRIPTION
    用于紧急情况下临时关闭/恢复 GitHub Branch Protection 的 required_status_checks。
    基于 gh CLI 调用 GitHub API, 无需手动配置 token。

    三种动作:
      status   - 查看当前 Branch Protection 状态
      disable  - 备份并临时关闭 required_status_checks (保留规则本身)
      enable   - 从备份恢复 required_status_checks

    典型紧急回滚流程:
      1. .\scripts\rollback-protection.ps1 -Action disable
      2. 合并紧急修复 PR
      3. .\scripts\rollback-protection.ps1 -Action enable
      4. .\scripts\rollback-protection.ps1 -Action status  # 确认恢复

.PARAMETER Action
    动作: status (默认) / disable / enable

.PARAMETER Branch
    分支名, 默认 master (按需改为 main)

.PARAMETER Force
    跳过 disable/enable 的确认提示

.EXAMPLE
    .\scripts\rollback-protection.ps1
    .\scripts\rollback-protection.ps1 -Action disable
    .\scripts\rollback-protection.ps1 -Action enable
    .\scripts\rollback-protection.ps1 -Action disable -Force

.NOTES
    前置条件:
      1. gh CLI 已安装且已登录 (gh auth login)
      2. 对仓库有 Admin 权限
      3. PowerShell 7+ (兼容 Windows PowerShell 5.1)
    备份位置: 系统临时目录 (由 Get-BackupPath 动态生成, 含 owner/repo/branch)
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [ValidateSet('status', 'disable', 'enable')]
    [string]$Action = 'status',

    [string]$Branch = 'master',

    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# ─── 前置检查 ─────────────────────────────────────────────────
function Test-Prerequisites {
    Write-Host "[1/3] 检查 gh CLI..." -ForegroundColor Cyan
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $gh) {
        Write-Host "  ❌ gh CLI 未安装, 请先安装: https://cli.github.com/" -ForegroundColor Red
        Write-Host "     或运行: winget install GitHub.cli" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "  ✅ gh CLI 已安装: $($gh.Source)" -ForegroundColor Green

    Write-Host "[2/3] 检查 gh 登录状态..." -ForegroundColor Cyan
    $auth = gh auth status 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ❌ gh 未登录, 请运行: gh auth login" -ForegroundColor Red
        Write-Host $auth
        exit 1
    }
    $account = ($auth | Select-String "Logged in to github.com account (\S+)").Matches.Groups[1].Value
    Write-Host "  ✅ 已登录账户: $account" -ForegroundColor Green

    Write-Host "[3/3] 解析当前仓库..." -ForegroundColor Cyan
    $remoteUrl = git remote get-url origin 2>$null
    if (-not $remoteUrl) {
        Write-Host "  ❌ 当前目录无 git remote origin" -ForegroundColor Red
        exit 1
    }
    Write-Verbose "[verbose] git remote origin 原始 URL: $remoteUrl"
    # 解析 owner/repo, 支持 HTTPS 和 SSH
    $script:Owner, $script:Repo = switch -Wildcard ($remoteUrl) {
        'https://github.com/*/*' {
            $path = $remoteUrl -replace '^https://github.com/', '' -replace '\.git$', ''
            $parts = $path -split '/'
            $parts[0], $parts[1]
            break
        }
        'git@github.com:*/*' {
            $path = $remoteUrl -replace '^git@github.com:', '' -replace '\.git$', ''
            $parts = $path -split '/'
            $parts[0], $parts[1]
            break
        }
        default {
            Write-Host "  ❌ 无法解析 remote URL: $remoteUrl" -ForegroundColor Red
            exit 1
        }
    }
    Write-Verbose "[verbose] 解析结果: Owner=$script:Owner Repo=$script:Repo"
    Write-Host "  ✅ 仓库: $script:Owner/$script:Repo (branch=$Branch)" -ForegroundColor Green
}

# ─── 调用 GitHub API ──────────────────────────────────────────
function Invoke-GhApi {
    param(
        [Parameter(Mandatory)]
        [string]$Method,
        [Parameter(Mandatory)]
        [string]$Endpoint,
        [string]$Body
    )
    Write-Verbose "[verbose] gh api 调用: $Method $Endpoint"
    if ($Body) {
        Write-Verbose "[verbose] 请求 Body: $Body"
        # PowerShell 用管道传 stdin, 不用 bash 的 <<< 语法
        $result = $Body | gh api --method $Method $Endpoint --input - 2>&1
    } else {
        $result = gh api --method $Method $Endpoint 2>&1
    }
    if ($LASTEXITCODE -ne 0) {
        throw "gh api 调用失败: $result"
    }
    Write-Verbose "[verbose] 响应长度: $($result.Length) 字符"
    return $result
}

# ─── status 动作 ──────────────────────────────────────────────
function Show-Status {
    Write-Host "`n=== Branch Protection 状态 ===" -ForegroundColor Cyan

    try {
        $protection = Invoke-GhApi -Method GET -Endpoint "repos/$script:Owner/$script:Repo/branches/$Branch/protection"
        $data = $protection | ConvertFrom-Json
    } catch {
        if ($_.Exception.Message -match 'Branch not protected') {
            Write-Host "  ℹ️  分支 '$Branch' 未配置 Branch Protection" -ForegroundColor Yellow
            return
        }
        throw
    }

    Write-Verbose "[verbose] Protection API 响应 (前 500 字符): $($protection.Substring(0, [Math]::Min(500, $protection.Length)))"

    Write-Host "  分支: $Branch" -ForegroundColor White
    Write-Host "  强制 PR: $($data.required_pull_request_reviews.enabled)" -ForegroundColor White

    $rsc = $data.required_status_checks
    Write-Host "  Required Status Checks:" -ForegroundColor White
    Write-Host "    enabled: $($rsc.enabled)" -ForegroundColor White
    Write-Host "    strict (up-to-date): $($rsc.strict)" -ForegroundColor White
    if ($rsc.contexts) {
        Write-Host "    contexts:" -ForegroundColor White
        foreach ($ctx in $rsc.contexts) {
            $mark = if ($ctx -like '*Skills*') { ' ⭐' } else { '' }
            Write-Host "      - $ctx$mark" -ForegroundColor White
        }
    } else {
        Write-Host "    contexts: (空)" -ForegroundColor Yellow
    }

    # 检查备份是否存在
    $backupPath = Get-BackupPath
    Write-Verbose "[verbose] 备份文件路径: $backupPath"
    if (Test-Path $backupPath) {
        Write-Host "`n  备份文件存在: $backupPath" -ForegroundColor Green
        $backup = Get-Content $backupPath -Raw | ConvertFrom-Json
        Write-Host "    备份时间: $($backup.backup_time)" -ForegroundColor White
        Write-Host "    备份 contexts: $($backup.required_status_checks.contexts -join ', ')" -ForegroundColor White
    } else {
        Write-Host "`n  备份文件不存在 (未执行过 disable)" -ForegroundColor Gray
    }
}

# ─── disable 动作 ─────────────────────────────────────────────
function Disable-Protection {
    Write-Host "`n=== 禁用 required_status_checks ===" -ForegroundColor Cyan

    # 危险操作: 用 ShouldProcess 统一确认 (-Confirm:$false 或 -Force 跳过)
    $target = "branch/$Branch required_status_checks"
    $operation = "Disable (清空 contexts)"
    if (-not $PSCmdlet.ShouldProcess($target, $operation)) {
        Write-Host "  已取消 (用户拒绝或 -Confirm:`$false)" -ForegroundColor Gray
        return
    }
    # -Force 也跳过 ShouldProcess
    if ($Force) {
        Write-Host "  [Force] 跳过确认" -ForegroundColor Yellow
    }

    # 1. 获取当前 protection 配置
    Write-Host "`n[1/3] 获取当前 Branch Protection 配置..." -ForegroundColor Cyan
    try {
        $protection = Invoke-GhApi -Method GET -Endpoint "repos/$script:Owner/$script:Repo/branches/$Branch/protection"
        $data = $protection | ConvertFrom-Json
    } catch {
        if ($_.Exception.Message -match 'Branch not protected') {
            Write-Host "  ℹ️  分支 '$Branch' 未配置 Protection, 无需 disable" -ForegroundColor Yellow
            return
        }
        throw
    }

    $rsc = $data.required_status_checks
    if (-not $rsc.enabled) {
        Write-Host "  ℹ️  required_status_checks 已是禁用状态, 无需重复 disable" -ForegroundColor Yellow
        return
    }

    # 2. 备份到本地
    Write-Host "[2/3] 备份当前配置到系统临时目录..." -ForegroundColor Cyan
    $backupPath = Get-BackupPath
    Write-Verbose "[verbose] 备份文件完整路径: $backupPath"
    $backupDir = Split-Path $backupPath -Parent
    if (-not (Test-Path $backupDir)) {
        Write-Verbose "[verbose] 备份目录不存在, 创建: $backupDir"
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    $backup = @{
        backup_time    = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        owner          = $script:Owner
        repo           = $script:Repo
        branch         = $Branch
        required_status_checks = @{
            enabled  = $rsc.enabled
            strict   = $rsc.strict
            contexts = @($rsc.contexts)
        }
    }
    $backup | ConvertTo-Json -Depth 5 | Out-File -FilePath $backupPath -Encoding utf8
    Write-Verbose "[verbose] 备份内容 (contexts 变更前): $($rsc.contexts -join ', ')"
    Write-Host "  ✅ 已备份 contexts: $($rsc.contexts -join ', ')" -ForegroundColor Green

    # 3. 清空 contexts 禁用
    Write-Host "[3/3] 清空 required_status_checks.contexts..." -ForegroundColor Cyan
    $payload = @{
        strict   = $rsc.strict
        contexts = @()
    } | ConvertTo-Json -Compress
    Write-Verbose "[verbose] PATCH 请求将 contexts 从 [$($rsc.contexts -join ', ')] 变更为 []"

    Invoke-GhApi -Method PATCH `
        -Endpoint "repos/$script:Owner/$script:Repo/branches/$Branch/protection/required_status_checks" `
        -Body $payload | Out-Null

    Write-Verbose "[verbose] contexts 变更完成: $($rsc.contexts.Count) 个 → 0 个"
    Write-Host "  ✅ required_status_checks 已临时关闭" -ForegroundColor Green
    Write-Host "`n  💡 完成紧急合并后请立即执行:" -ForegroundColor Yellow
    Write-Host "     .\scripts\rollback-protection.ps1 -Action enable" -ForegroundColor Yellow
}

# ─── enable 动作 ──────────────────────────────────────────────
function Enable-Protection {
    Write-Host "`n=== 恢复 required_status_checks ===" -ForegroundColor Cyan

    # 危险操作: 用 ShouldProcess 统一确认 (-WhatIf 在此模拟, 不被前置检查阻塞)
    $target = "branch/$Branch required_status_checks"
    $operation = "Enable (恢复 contexts)"
    if (-not $PSCmdlet.ShouldProcess($target, $operation)) {
        Write-Host "  已取消 (用户拒绝或 -Confirm:`$false)" -ForegroundColor Gray
        return
    }
    if ($Force) {
        Write-Host "  [Force] 跳过确认" -ForegroundColor Yellow
    }

    $backupPath = Get-BackupPath
    Write-Verbose "[verbose] 备份文件完整路径: $backupPath"
    if (-not (Test-Path $backupPath)) {
        Write-Host "  ❌ 备份文件不存在: $backupPath" -ForegroundColor Red
        Write-Host "     无法自动恢复, 请手动到 GitHub 网页配置 required checks" -ForegroundColor Yellow
        exit 1
    }

    Write-Host "`n[1/2] 读取备份..." -ForegroundColor Cyan
    $backup = Get-Content $backupPath -Raw | ConvertFrom-Json
    Write-Verbose "[verbose] 备份文件内容: $($backup | ConvertTo-Json -Compress)"
    Write-Host "  ✅ 备份时间: $($backup.backup_time)" -ForegroundColor Green

    Write-Host "[2/2] 恢复 required_status_checks..." -ForegroundColor Cyan
    $payload = @{
        strict   = $backup.required_status_checks.strict
        contexts = @($backup.required_status_checks.contexts)
    } | ConvertTo-Json -Compress
    Write-Verbose "[verbose] PATCH 请求将 contexts 从 [] 恢复为 [$($backup.required_status_checks.contexts -join ', ')]"

    Invoke-GhApi -Method PATCH `
        -Endpoint "repos/$script:Owner/$script:Repo/branches/$Branch/protection/required_status_checks" `
        -Body $payload | Out-Null

    Write-Verbose "[verbose] contexts 恢复完成: 0 个 → $($backup.required_status_checks.contexts.Count) 个"
    Write-Host "  ✅ required_status_checks 已恢复" -ForegroundColor Green
    Write-Host "     contexts: $($backup.required_status_checks.contexts -join ', ')" -ForegroundColor White

    # 询问是否删除备份
    if ($Force -or (Read-Host "`n  是否删除备份文件? (y/N)") -eq 'y') {
        Remove-Item $backupPath -Force
        Write-Host "  ✅ 备份文件已删除" -ForegroundColor Gray
    }
}

# ─── 辅助函数 ─────────────────────────────────────────────────
function Get-BackupPath {
    # 【不易】备份文件放在系统临时目录，避免泄露 branch protection 配置到仓库
    # 文件名包含 owner/repo/branch，避免多仓库冲突
    $tempDir = [System.IO.Path]::GetTempPath()
    $owner = if ($script:Owner) { $script:Owner } else { 'unknown' }
    $repo = if ($script:Repo) { $script:Repo } else { 'unknown' }
    $fileName = "branch_protection_backup_${owner}_${repo}_$Branch.json"
    return (Join-Path $tempDir $fileName)
}

# ─── 主入口 ───────────────────────────────────────────────────
Write-Host @"
╔══════════════════════════════════════════════════════════╗
║   Skills Check Branch Protection 紧急回滚工具            ║
╚══════════════════════════════════════════════════════════╝
"@ -ForegroundColor Cyan

Write-Host "Action: $Action | Branch: $Branch | Force: $Force`n" -ForegroundColor White

Write-Verbose "[verbose] 参数解析: Action=$Action Branch=$Branch Force=$Force Verbose=$VerbosePreference WhatIfPreference=$WhatIfPreference ConfirmPreference=$ConfirmPreference"

Test-Prerequisites

switch ($Action) {
    'status'  { Show-Status }
    'disable' { Disable-Protection }
    'enable'  { Enable-Protection }
}

Write-Host ""
