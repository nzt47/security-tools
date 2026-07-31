<#
.SYNOPSIS
    三路融合 BM25 权重配置变更脚本（.env 驱动）

.DESCRIPTION
    将 .env 中的 SKILLS_FUSION_WEIGHT_BM25 设置为指定值（默认 0.5）。
    符合项目硬约束"所有配置修改必须改到 .env 文件"。
    loader.py:_get_default_weights() 读取此环境变量，无需改代码即可调整权重。

    验证依据（scripts/verify_three_path_fusion_real.py）：
      - 默认 bm25=0.2 命中率 6/7=86%（k8s 失败：TF-IDF 并列 + BM25 权重低）
      - bm25=0.5 命中率 7/7=100%（BM25 精确字面匹配打破 TF-IDF 并列）

.PARAMETER Weight
    BM25 权重值（默认 0.5）。权重不必和为 1，内部自动归一化。
    常用值：
      0.2 — 默认值（回归旧版行为）
      0.5 — 推荐值（专有名词场景验证通过）
      0.8 — BM25 主导（极端字面匹配场景）

.PARAMETER Restore
    从最近一次备份恢复 .env

.PARAMETER BackupOnly
    仅备份不修改（用于变更前手动备份）

.EXAMPLE
    .\scripts\set_bm25_weight.ps1
    # 应用默认变更：BM25 权重 → 0.5

.EXAMPLE
    .\scripts\set_bm25_weight.ps1 -Weight 0.2
    # 回滚到默认权重 0.2（不恢复备份，仅改值）

.EXAMPLE
    .\scripts\set_bm25_weight.ps1 -Restore
    # 从备份恢复 .env

.NOTES
    变更记录写入 logs/config_audit.jsonl（由 EnvConfigManager 审计）
#>
[CmdletBinding()]
param(
    [ValidateRange(0.0, 1.0)]
    [float]$Weight = 0.5,
    [switch]$Restore,
    [switch]$BackupOnly
)

$ErrorActionPreference = "Stop"

# 项目根目录（脚本位于 scripts/ 下，根目录是上一级）
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$EnvFile = Join-Path $ProjectRoot ".env"
$BackupDir = Join-Path $ProjectRoot ".env.backups"

if (-not (Test-Path $EnvFile)) {
    Write-Error "未找到 .env 文件: $EnvFile"
    exit 1
}

# ── 恢复模式 ──
if ($Restore) {
    $LatestBackup = Get-ChildItem -Path $BackupDir -Filter "env.bak.*" -ErrorAction SilentlyContinue |
                    Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $LatestBackup) {
        Write-Error "未找到备份文件，无法恢复。备份目录: $BackupDir"
        exit 1
    }
    Copy-Item $LatestBackup.FullName $EnvFile -Force
    Write-Host "✓ 已从备份恢复 .env" -ForegroundColor Green
    Write-Host "  备份来源: $($LatestBackup.FullName)" -ForegroundColor Gray
    Write-Host "  备份时间: $($LatestBackup.LastWriteTime)" -ForegroundColor Gray
    Write-Host ""
    Write-Host "提示: 恢复后需重启服务或重新加载 .env 才能生效" -ForegroundColor Yellow
    exit 0
}

# ── 备份 .env ──
if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}
$Timestamp = Get-Date -Format "yyyyMMddHHmmss"
$BackupFile = Join-Path $BackupDir "env.bak.$Timestamp"
Copy-Item $EnvFile $BackupFile -Force
Write-Host "✓ 已备份 .env → $BackupFile" -ForegroundColor Green

if ($BackupOnly) {
    Write-Host "（仅备份模式，未修改 .env）" -ForegroundColor Yellow
    exit 0
}

# ── 读取 .env 内容 ──
$Content = Get-Content $EnvFile -Raw -Encoding UTF8
$WeightStr = $Weight.ToString("0.0", [System.Globalization.CultureInfo]::InvariantCulture)
# 处理 0.5 vs .5 格式问题（确保小数点前有数字）
if ($WeightStr -match "^\.") { $WeightStr = "0" + $WeightStr }

# ── 检查是否已存在 SKILLS_FUSION_WEIGHT_BM25 ──
$Pattern = "(?m)^(#?\s*SKILLS_FUSION_WEIGHT_BM25\s*=\s*).*$"
if ($Content -match $Pattern) {
    # 更新现有行（包括被注释掉的行，取消注释并设新值）
    $NewContent = [regex]::Replace($Content, $Pattern, "SKILLS_FUSION_WEIGHT_BM25=$WeightStr")
    Write-Host "✓ 更新现有 SKILLS_FUSION_WEIGHT_BM25 = $WeightStr" -ForegroundColor Cyan
} else {
    # 在 SKILL_RERANKER_MIN_SCORE 行后插入新区块
    $InsertBlock = @"

# ========================================
# 三路融合权重配置（agent/skills_mgmt/loader.py:_get_default_weights）
# ========================================
# 由 scripts/set_bm25_weight.ps1 于 $Timestamp 添加
# 验证: scripts/verify_three_path_fusion_real.py
SKILLS_FUSION_WEIGHT_BM25=$WeightStr
"@
    # 找到 SKILL_RERANKER_MIN_SCORE 行后插入
    if ($Content -match "(?m)^(SKILL_RERANKER_MIN_SCORE\s*=\s*.*)$") {
        $NewContent = $Content -replace "(?m)^(SKILL_RERANKER_MIN_SCORE\s*=\s*.*)$", "`$1`n$InsertBlock"
        Write-Host "✓ 新增 SKILLS_FUSION_WEIGHT_BM25 = $WeightStr（在 SKILL_RERANKER 区块后）" -ForegroundColor Cyan
    } else {
        # 找不到锚点，追加到文件末尾
        $NewContent = $Content.TrimEnd() + "`n" + $InsertBlock + "`n"
        Write-Host "✓ 新增 SKILLS_FUSION_WEIGHT_BM25 = $WeightStr（追加到文件末尾）" -ForegroundColor Cyan
    }
}

# ── 写入 .env ──
# 注意：用 UTF8 无 BOM 编码（.env 文件标准）
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($EnvFile, $NewContent, $Utf8NoBom)

Write-Host ""
Write-Host "【变更摘要】" -ForegroundColor Yellow
Write-Host "  BM25 权重: 0.2 → $WeightStr" -ForegroundColor Yellow
Write-Host "  归一化后:  tfidf=0.2/$(0.2+0.6+$Weight)=$( [math]::Round(0.2/(0.2+0.6+$Weight), 4) )  vector=0.6/$(0.2+0.6+$Weight)=$( [math]::Round(0.6/(0.2+0.6+$Weight), 4) )  bm25=$WeightStr/$(0.2+0.6+$Weight)=$( [math]::Round($Weight/(0.2+0.6+$Weight), 4) )" -ForegroundColor Gray
Write-Host ""
Write-Host "【回滚方式】" -ForegroundColor Yellow
Write-Host "  方式1（恢复备份）: .\scripts\set_bm25_weight.ps1 -Restore" -ForegroundColor Gray
Write-Host "  方式2（改回默认）: .\scripts\set_bm25_weight.ps1 -Weight 0.2" -ForegroundColor Gray
Write-Host ""
Write-Host "【验证命令】" -ForegroundColor Yellow
Write-Host "  python scripts/verify_env_weights.py        # 确认 .env 读取生效" -ForegroundColor Gray
Write-Host "  python scripts/verify_three_path_fusion_real.py  # 端到端命中率验证" -ForegroundColor Gray
Write-Host ""
Write-Host "提示: 需重启服务或重新加载 .env 才能生效" -ForegroundColor Yellow
