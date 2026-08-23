<# 
  monitor-test-shard.ps1 — 单测 Shard 资源问题自动化监控脚本
  用途：持续观察 CI 单元测试 Shard 的"资源耗尽"类失败（RuntimeError: can't start new thread / INTERNALERROR），
        定位是否为 pytest-xdist 线程/资源问题，输出报告并可接告警。
  依赖：gh CLI（已认证）
  用法：
    .\monitor-test-shard.ps1 -Branch develop -Days 7
    .\monitor-test-shard.ps1 -Branch develop -Days 14 -FailThreshold 3   # 超阈值退出码 1（告警）
  说明：只分析单元测试 Shard 相关 job，忽略其它 workflow。
#>
param(
    [string]$Repo = "nzt47/security-tools",
    [string]$Branch = "develop",
    [int]$Days = 7,
    [int]$FailThreshold = 0
)

$ErrorActionPreference = "Stop"
$since = (Get-Date).AddDays(-$Days).ToString("yyyy-MM-ddTHH:mm:ssZ")

Write-Host "===== 单测 Shard 资源问题监控 =====" -ForegroundColor Cyan
Write-Host "仓库: $Repo  分支: $Branch  窗口: ${Days} 天" 

# 1) 拉取窗口内 ci.yml 的 run 列表（仅 completed）
$runs = gh api "repos/$Repo/actions/workflows/ci.yml/runs?branch=$Branch&per_page=100" --jq ".workflow_runs[] | select(.created_at >= \"$since\") | select(.status == \"completed\") | {id: .id, conclusion: .conclusion, head: (.head_sha[0:8])}" 2>$null
if (-not $runs) { Write-Host "窗口内无已完成 run"; exit 0 }
$runs = $runs | ConvertFrom-Json
Write-Host "窗口内已完成 run 数: $($runs.Count)（失败: $(($runs | Where-Object conclusion -eq 'failure').Count)）"

# 2) 对每个失败 run，检查单元测试 Shard job 的失败原因
$hits = @()
foreach ($r in ($runs | Where-Object { $_.conclusion -eq "failure" })) {
    $jobs = gh api "repos/$Repo/actions/runs/$($r.id)/jobs?per_page=100" --jq ".jobs[] | select((.name | startswith(\"单元测试\")) and .conclusion == \"failure\") | {name: .name, id: .database_id}" 2>$null
    if (-not $jobs) { continue }
    foreach ($j in ($jobs | ConvertFrom-Json)) {
        # 拉取失败 job 日志，检测资源耗尽特征
        $log = gh api "repos/$Repo/actions/jobs/$($j.id)/logs" 2>$null
        $isResource = $log -match "can't start new thread|INTERNALERROR|Resource temporarily unavailable"
        $isAssert = $log -match "FAILED|AssertionError"
        $hits += [PSCustomObject]@{
            RunId   = $r.id
            Head    = $r.head
            Job     = $j.name
            Resource= $isResource
            Assert  = $isAssert
        }
    }
}

# 3) 输出报告
Write-Host "`n===== 检测结果 =====" -ForegroundColor Cyan
if ($hits.Count -eq 0) {
    Write-Host "✅ 无单元测试 Shard 失败（窗口内）"
    exit 0
}
$hits | Format-Table -AutoSize
$resCount = ($hits | Where-Object Resource).Count
$assertCount = ($hits | Where-Object { -not $_.Resource }).Count
Write-Host "资源类失败: $resCount  |  断言类失败: $assertCount"
Write-Host "资源类失败占比: $(if ($hits.Count) { '{0:P0}' -f ($resCount / $hits.Count) } else { 'N/A' })"

# 4) 阈值告警（仅资源类计阈值）
if ($FailThreshold -gt 0 -and $resCount -ge $FailThreshold) {
    Write-Host "⚠️ 资源类失败($resCount) 达到阈值($FailThreshold) —— 触发告警" -ForegroundColor Red
    exit 1
}
exit 0
