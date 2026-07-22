#Requires -Version 5.1
<#
.SYNOPSIS
    TEMP 目录白名单清理脚本（安全删除预定义模式的临时文件）
.DESCRIPTION
    基于白名单机制，仅删除匹配预定义模式的临时文件，避免误删。
    支持 -DryRun 预览 + -KeepUseful 保留近期文件。
    【P1 重建 2026-07-22】基于设计说明恢复，未入 git 历史。
.PARAMETER Path
    目标目录，默认 $env:TEMP
.PARAMETER DryRun
    仅显示将删除的文件，不实际删除
.PARAMETER KeepUseful
    保留最近 N 天内修改的文件（默认 7 天）
.PARAMETER KeepDays
    -KeepUseful 模式下保留天数，默认 7
.EXAMPLE
    .\cleanup_temp_files.ps1 -DryRun
    预览将删除的文件
.EXAMPLE
    .\cleanup_temp_files.ps1 -KeepUseful -KeepDays 3
    正式清理，保留 3 天内修改的文件
#>
[CmdletBinding()]
param(
    [string]$Path = $env:TEMP,
    [switch]$DryRun,
    [switch]$KeepUseful,
    [int]$KeepDays = 7
)

# ── 白名单：17 个文件模式（仅删除匹配项，其余保留）──────────────
# Why: 白名单 > 黑名单，避免误删未知文件（不易守约）
$WhitelistPatterns = @(
    '*.tmp',           # 通用临时文件
    '*.temp',          # 通用临时文件
    '*.bak',           # 备份文件
    '*.old',           # 旧版本文件
    '*.swp',           # Vim 交换文件
    '*.swo',           # Vim 交换文件
    '*~',              # 编辑器备份
    '.DS_Store',       # macOS 目录元数据
    'Thumbs.db',       # Windows 缩略图缓存
    '~$*',             # Office 临时锁文件
    '*.log',           # 临时日志（TEMP 目录内）
    '*.dmp',           # 崩溃转储
    '*.err',           # 错误输出
    '*.cache',         # 缓存文件
    '*.pid',           # 进程 ID 文件
    '*.pyc',           # Python 字节码缓存
    '*.pyo'            # Python 优化字节码
)

# ── 目录白名单（递归清理）────────────────────────────────────────
$WhitelistDirs = @(
    '__pycache__',     # Python 字节码目录
    '.pytest_cache'    # pytest 缓存目录
)

$ErrorActionPreference = 'Stop'

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "──[ $Title ]" -ForegroundColor Cyan
}

function Write-Info {
    param([string]$Msg)
    Write-Host "[INFO] $Msg" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Msg)
    Write-Host "[WARN] $Msg" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Msg)
    Write-Host "[ERR ] $Msg" -ForegroundColor Red
}

# ── Stage 0: 预检查 ─────────────────────────────────────────────
Write-Section "Stage 0: 预检查"

if (-not (Test-Path $Path)) {
    Write-Err "目标目录不存在: $Path"
    exit 1
}

$resolvedPath = (Resolve-Path $Path).Path
Write-Info "目标目录: $resolvedPath"
Write-Info "模式: $(if ($DryRun) { 'DryRun（预览）' } else { '正式删除' })"
if ($KeepUseful) {
    Write-Info "保留策略: 最近 $KeepDays 天内修改的文件保留"
}

$cutoffTime = $null
if ($KeepUseful) {
    $cutoffTime = (Get-Date).AddDays(-$KeepDays)
}

# ── Stage 1: 扫描匹配文件 ──────────────────────────────────────
Write-Section "Stage 1: 扫描白名单匹配文件"

$matchedFiles = New-Object System.Collections.Generic.List[System.IO.FileInfo]
$matchedDirs = New-Object System.Collections.Generic.List[string]

# 扫描文件模式
foreach ($pattern in $WhitelistPatterns) {
    try {
        $files = Get-ChildItem -Path $resolvedPath -Filter $pattern -File -Recurse -ErrorAction SilentlyContinue
        foreach ($f in $files) {
            $matchedFiles.Add($f)
        }
    } catch {
        Write-Warn "扫描 $pattern 失败: $($_.Exception.Message)"
    }
}

# 扫描目录模式
foreach ($dirName in $WhitelistDirs) {
    try {
        $dirs = Get-ChildItem -Path $resolvedPath -Filter $dirName -Directory -Recurse -ErrorAction SilentlyContinue
        foreach ($d in $dirs) {
            $matchedDirs.Add($d.FullName)
        }
    } catch {
        Write-Warn "扫描目录 $dirName 失败: $($_.Exception.Message)"
    }
}

# 去重（同一文件可能被多个模式匹配）
$uniqueFiles = $matchedFiles | Sort-Object FullName -Unique
Write-Info "匹配文件数: $($uniqueFiles.Count)（去重后）"
Write-Info "匹配目录数: $($matchedDirs.Count)"

# ── Stage 2: 应用保留策略 ──────────────────────────────────────
Write-Section "Stage 2: 应用保留策略"

$toDelete = New-Object System.Collections.Generic.List[System.IO.FileInfo]

foreach ($f in $uniqueFiles) {
    $shouldDelete = $true
    if ($KeepUseful -and $f.LastWriteTime -gt $cutoffTime) {
        $shouldDelete = $false
    }
    if ($shouldDelete) {
        $toDelete.Add($f)
    }
}

$keptCount = $uniqueFiles.Count - $toDelete.Count
Write-Info "将删除: $($toDelete.Count) 个文件"
if ($KeepUseful) {
    Write-Info "保留: $keptCount 个文件（$KeepDays 天内修改）"
}

# ── Stage 3: 执行删除 ──────────────────────────────────────────
Write-Section "Stage 3: 执行删除"

$totalSize = 0L
$deletedCount = 0
$failedCount = 0

foreach ($f in $toDelete) {
    $size = $f.Length
    if ($DryRun) {
        Write-Host "[DRY] $($f.FullName) ($('{0:N2}' -f ($size/1KB)) KB)" -ForegroundColor DarkGray
        $totalSize += $size
        $deletedCount++
    } else {
        try {
            Remove-Item -Path $f.FullName -Force -ErrorAction Stop
            $totalSize += $size
            $deletedCount++
        } catch {
            Write-Warn "删除失败: $($f.FullName) - $($_.Exception.Message)"
            $failedCount++
        }
    }
}

# 删除目录（仅当为空或仅含白名单文件时）
foreach ($dir in $matchedDirs) {
    if ($DryRun) {
        Write-Host "[DRY] 目录: $dir" -ForegroundColor DarkGray
    } else {
        try {
            # 仅当目录内无非白名单文件时才删除
            $remaining = Get-ChildItem -Path $dir -Recurse -File -ErrorAction SilentlyContinue
            if (-not $remaining) {
                Remove-Item -Path $dir -Recurse -Force -ErrorAction Stop
                Write-Info "已删除目录: $dir"
            }
        } catch {
            Write-Warn "目录删除失败: $dir - $($_.Exception.Message)"
        }
    }
}

# ── Stage 4: 汇总报告 ──────────────────────────────────────────
Write-Section "Stage 4: 汇总报告"

$sizeMB = $totalSize / 1MB
Write-Info "已处理文件: $deletedCount"
Write-Info "释放空间: $('{0:N2}' -f $sizeMB) MB"
if ($failedCount -gt 0) {
    Write-Warn "失败文件: $failedCount"
}

if ($DryRun) {
    Write-Warn "DryRun 模式：未实际删除，移除 -DryRun 参数执行正式清理"
} else {
    Write-Info "清理完成"
}

Write-Host ""
exit 0
