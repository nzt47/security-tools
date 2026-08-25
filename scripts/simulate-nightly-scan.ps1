<#
.SYNOPSIS
    模拟 Skills Check "定期全量扫描" job 的 workflow_dispatch 触发场景,
    验证优化后的稳定性行为 (对照 .github/workflows/skills-check.yml 的
    nightly-full-scan job 步骤):

      1. 一致性验证    — compare + verify (legacy 缺失时自动 SKIP)
      2. 动态加载风险扫描 — detect --json, continue-on-error: true 语义
                       (detect exit 1 不阻断 job)
      3. 报告生成校验   — dynamic_loads_report.json 存在且 JSON 可解析
                       (if: always() 语义: 报告总是保留)
      4. 模拟上传      — 报告字段完整即视为上传成功

.NOTES
    运行: pwsh scripts/simulate-nightly-scan.ps1
    期望结果: 即使 detect 因 HIGH 退出码 1, job 仍继续、报告照常生成。
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root

function Step([string]$name) { Write-Host "`n=== $name ===" -ForegroundColor Cyan }

# ─── Step 1: 一致性验证 (对应 job 的"一致性验证"步骤) ─────────
Step "1/4 一致性验证 (compare + verify)"
python scripts/compare_skills_legacy_vs_repo.py
python scripts/verify_migrated_skills.py
Write-Host "  一致性验证完成 (legacy 缺失时自动 SKIP)" -ForegroundColor Green

# ─── Step 2: detect --json (对应"动态加载风险扫描"步骤) ───────
# [变易] 与 workflow 的 continue-on-error: true 一致: detect 退出码非 0
# 不阻断后续步骤, 报告仍继续生成.
Step "2/4 动态加载风险扫描 (JSON 报告)"
python scripts/detect_dynamic_loads.py --json > dynamic_loads_report.json 2>$null
$detectExit = $LASTEXITCODE
Write-Host "  detect exit code: $detectExit (continue-on-error 语义: 不阻断 job)" -ForegroundColor Yellow

# ─── Step 3: 报告生成校验 (对应"上传扫描报告"步骤的 if: always()) ─
Step "3/4 报告生成校验"
if (-not (Test-Path dynamic_loads_report.json)) {
    Write-Host "  ❌ 报告未生成" -ForegroundColor Red
    Pop-Location
    exit 1
}
try {
    $report = Get-Content dynamic_loads_report.json -Raw | ConvertFrom-Json
} catch {
    Write-Host "  ❌ 报告 JSON 不可解析: $($_.Exception.Message)" -ForegroundColor Red
    Pop-Location
    exit 1
}
Write-Host "  ✅ 报告已生成: scanned=$($report.scanned_files) high=$($report.high_risk_count) med=$($report.medium_risk_count) low=$($report.low_risk_count)" -ForegroundColor Green

# ─── Step 4: 模拟上传 (报告存在 + 字段完整即视为上传成功) ──────
Step "4/4 模拟 artifact 上传"
$uploadName = "dynamic-load-scan-$($env:GITHUB_RUN_ID)"
if (-not $uploadName -or $uploadName -match '/') { $uploadName = "dynamic-load-scan-local" }
if ($report.scanned_files -gt 0 -and $report.PSObject.Properties.Name -contains 'high_risk_count') {
    Write-Host "  ✅ 模拟上传成功: artifact '$uploadName'" -ForegroundColor Green
} else {
    Write-Host "  ❌ 报告内容无效 (scanned_files=0 或缺少字段)" -ForegroundColor Red
    Pop-Location
    exit 1
}

Write-Host "`n✅ 模拟 workflow_dispatch 验证通过: job 不失败 + 报告已生成" -ForegroundColor Green

# 清理模拟产物 (报告仅用于本地验证, 不入库; CI 上报告上传 artifact 后由 runner 自动回收)
Remove-Item dynamic_loads_report.json -Force -ErrorAction SilentlyContinue
Pop-Location
exit 0
