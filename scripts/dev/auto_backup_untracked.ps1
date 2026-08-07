# 清理临时文件前的自动化保险：检测并备份所有未追踪文件
#
# 背景: 2026-08-06 误删 docs/zh/知识库重构计划/ 下 5 个 HEAD 已跟踪文件
#   (目录内含"已跟踪+未跟踪"混合文件), 靠备份 + git checkout 补救。
#   教训: 删除前必须先核对并备份。本脚本将"检测+备份"固化为清理前置步骤。
#
# 设计(三义):
# - 不易: 只检测/备份 untracked(git status --porcelain, 单一事实源),
#        绝不触碰已跟踪文件(M/D 状态); 备份保留相对路径结构, 可完整还原;
#        默认排除 backup/ 自身与脚本所在目录, 防自嵌套。
# - 变易: -DryRun 预览 / -Targets 仅备份指定项 / -NoExclude 取消默认排除 /
#        -LogDir 日志目录(默认 backup/logs, 也可用环境变量 BACKUP_LOG_DIR)。
# - 简易: 无删除逻辑(纯备份), 与 cleanup_parallel_session_tmp.ps1 配合使用:
#        先跑本脚本备份 → 再跑清理脚本删除。
#        每次运行自动追加一行结构化日志到 当日 日志文件(审计/复盘用):
#        backup/logs/backup_untracked_YYYYMMDD.log
#
# 用法:
#   powershell -File scripts/dev/auto_backup_untracked.ps1              # 备份全部 untracked
#   powershell -File scripts/dev/auto_backup_untracked.ps1 -DryRun      # 仅预览(不备份, 记 DRY_RUN 日志)
#   powershell -File scripts/dev/auto_backup_untracked.ps1 -Targets "guard.env","notes.md"
#   powershell -File scripts/dev/auto_backup_untracked.ps1 -LogDir "D:\backup_logs"

[CmdletBinding()]
param(
    [switch]$DryRun,
    [string[]]$Targets,
    [switch]$NoExclude,
    [string]$LogDir = $env:BACKUP_LOG_DIR
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# ── 0. 日志基础设施 ──
if (-not $LogDir) { $LogDir = Join-Path $repoRoot "backup\logs" }
$logFile = Join-Path $LogDir ("backup_untracked_" + (Get-Date -Format "yyyyMMdd") + ".log")
$script:startTime = Get-Date

function Write-BackupLog {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [string]$BackupDir = "",
        [int]$Count = 0
    )
    $elapsed = [math]::Round(((Get-Date) - $script:startTime).TotalSeconds, 1)
    $line = "[{0}] status={1} backup={2} count={3} elapsed={4}s" -f `
        (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Status, $BackupDir, $Count, $elapsed
    try {
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
        Add-Content -Path $logFile -Value $line -Encoding UTF8
    } catch {
        Write-Warning "日志写入失败: $($_.Exception.Message)"
    }
}

# ── 1. 检测 untracked 文件(逐文件列出) ──
# Why quotepath=false: git 默认对非 ASCII 路径输出"引号+八进制转义"(如
# "\351\242\204..."), 会导致后续前缀排除匹配失败(引号成为路径首字符)。
# 2026-08-07 实测: 未关闭时 backup/ 前缀过滤失效。故显式关闭。
# Why --untracked-files=all: git ls-files --others 默认把 untracked 目录折叠成
# "dir/" 单条目(实测 .tmp-script-fix/ 折叠后内容全丢)。git status --porcelain
# 配合 --untracked-files=all 逐文件列出, 备份粒度最细且避免目录复制陷阱。
# 2026-08-07 实测: .tmp-script-fix/ 内含 Edge profile 数十文件, 折叠模式备份为空壳。
$raw = @(& git -C $repoRoot -c core.quotepath=false status --porcelain --untracked-files=all)
$untracked = $raw | Where-Object { $_ -like "?? *" } | ForEach-Object { $_.Substring(3) }
if (-not $untracked) {
    Write-Host "[OK] 工作区无 untracked 文件, 无需备份" -ForegroundColor Green
    Write-BackupLog -Status "SKIP_NONE"
    exit 0
}

# ── 2. 过滤: 默认排除 backup/ 与脚本自身(防自嵌套) ──
if (-not $NoExclude) {
    $excludes = @("backup/", "scripts/dev/auto_backup_untracked.ps1")
    $untracked = $untracked | Where-Object {
        $p = $_ -replace "/", [System.IO.Path]::DirectorySeparatorChar
        -not ($excludes | Where-Object { $p -like ($_ -replace "/", [System.IO.Path]::DirectorySeparatorChar) + "*" })
    }
}

# ── 3. 按 Targets 过滤(可选) ──
if ($Targets) {
    $untracked = $untracked | Where-Object {
        $p = $_ -replace "/", [System.IO.Path]::DirectorySeparatorChar
        $hit = $Targets | Where-Object { $p -like ($_ -replace "/", [System.IO.Path]::DirectorySeparatorChar) + "*" -or $p -eq $_ }
        [bool]$hit
    }
}
if (-not $untracked) {
    Write-Host "[OK] 过滤后无待备份项" -ForegroundColor Yellow
    Write-BackupLog -Status "SKIP_FILTERED"
    exit 0
}

Write-Host "=== 检测到 $($untracked.Count) 个 untracked 文件 ===" -ForegroundColor Cyan
$untracked | ForEach-Object { Write-Host "  - $_" }

# ── 4. 备份到 backup/untracked_backup_YYYYMMDD_HHMMSS/ (保留相对路径) ──
$backupDir = Join-Path $repoRoot ("backup/untracked_backup_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
if ($DryRun) {
    Write-Host "[DRY-RUN] 将备份到: $backupDir (未执行)" -ForegroundColor Cyan
    Write-BackupLog -Status "DRY_RUN" -BackupDir $backupDir -Count $untracked.Count
    exit 0
}

$count = 0
foreach ($f in $untracked) {
    $src = Join-Path $repoRoot $f
    $dest = Join-Path $backupDir $f
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    # Why -Recurse: git status 对"嵌套 git 仓库"目录(如 .tmp-script-fix/ 含 .git)
    # 永远折叠为单条目, 无法展开; 无 -Recurse 时 Copy-Item 对目录只复制空壳。
    # 2026-08-07 实测: .tmp-script-fix/ (Edge profile + 完整仓库副本) 折叠模式备份丢失。
    if ((Test-Path $src) -and (Get-Item $src).PSIsContainer) {
        Copy-Item -Force -Recurse $src $dest
    } else {
        Copy-Item -Force $src $dest
    }
    $count++
}
Write-Host "[BACKUP] 已备份 $count 个文件 -> $backupDir" -ForegroundColor Green
Write-Host "[NEXT] 确认无误后可运行 cleanup_parallel_session_tmp.ps1 执行清理" -ForegroundColor Yellow
Write-BackupLog -Status "OK" -BackupDir $backupDir -Count $count
