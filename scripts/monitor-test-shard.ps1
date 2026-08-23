<# 
  monitor-test-shard.ps1 — 单测 Shard 资源问题自动化监控脚本
  用途：持续观察 CI 单元测试 Shard 的"资源耗尽"类失败（RuntimeError: can't start new thread / INTERNALERROR），
        定位是否为 pytest-xdist 线程/资源问题，输出报告并可接告警。
  依赖：gh CLI（已认证）
  用法：
    .\monitor-test-shard.ps1 -Branch develop -Days 7
    .\monitor-test-shard.ps1 -Branch develop -Days 14 -FailThreshold 3   # 资源类失败≥3 时退出码 1（告警）
    .\monitor-test-shard.ps1 -Branch develop -ReportPath logs\monitor-report.txt   # 报告写入文件
  说明：只分析单元测试 Shard 相关 job，忽略其它 workflow。
#>
param(
    [string]$Repo = "nzt47/security-tools",
    [string]$Branch = "develop",
    [int]$Days = 7,
    [int]$FailThreshold = 3,
    [string]$ReportPath = ""
)

$ErrorActionPreference = "Stop"
# gh 输出为 UTF-8，显式设置控制台/管道编码，避免中文 job 名乱码
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$OutputEncoding = [Console]::OutputEncoding
# created_at 为 UTC 时间，必须用 UTC 计算起点，避免本地时区偏移导致过滤过严
$since = (Get-Date).ToUniversalTime().AddDays(-$Days).ToString("yyyy-MM-ddTHH:mm:ssZ")

Write-Host "===== 单测 Shard 资源问题监控 =====" -ForegroundColor Cyan
Write-Host "仓库: $Repo  分支: $Branch  窗口: ${Days} 天" 

# 1) 拉取窗口内 ci.yml 的 run 列表（仅 completed）
$jqRuns = '.workflow_runs[] | select(.created_at >= "' + $since + '") | select(.status == "completed") | {id: .id, created: .created_at, conclusion: .conclusion, head: (.head_sha[0:8])}'
$runs = gh api "repos/$Repo/actions/workflows/ci.yml/runs?branch=$Branch&per_page=100" --jq $jqRuns 2>$null
if (-not $runs) { Write-Host "窗口内无已完成 run"; exit 0 }
$runs = $runs | ConvertFrom-Json
Write-Host "窗口内已完成 run 数: $($runs.Count)（失败: $(($runs | Where-Object conclusion -eq 'failure').Count)）"

# 2) 对每个失败 run，检查单元测试 Shard job 的失败原因
$hits = @()
foreach ($r in ($runs | Where-Object { $_.conclusion -eq "failure" })) {
    $jqJobs = '.jobs[] | select((.name | startswith("单元测试")) and .conclusion == "failure") | {name: .name, id: .id}'
    $jobs = gh api "repos/$Repo/actions/runs/$($r.id)/jobs?per_page=100" --jq $jqJobs 2>$null
    if (-not $jobs) { continue }
    foreach ($j in ($jobs | ConvertFrom-Json)) {
        # 下载 job 日志检测失败原因（curl -L 跟随重定向；GitHub 日志下载限速，耗时较长属正常）
        try {
            $token = gh auth token
            $log = curl.exe -sL -H "Authorization: Bearer $token" "https://api.github.com/repos/$Repo/actions/jobs/$($j.id)/logs" 2>$null
            $log = $log -join "`n"
        } catch {
            $log = ""
        }
        $isResource = $log -match "can't start new thread|INTERNALERROR|Resource temporarily unavailable"
        $isAssert = $log -match "FAILED|AssertionError"
        $hits += [PSCustomObject]@{
            RunId   = $r.id
            Created = ($r.created -replace "T", " " -replace "Z", " UTC")
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
$report = @"
===== 单测 Shard 资源监控报告 =====
生成时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
仓库: $Repo  分支: $Branch  窗口: ${Days} 天  阈值: $FailThreshold
窗口内已完成 run: $($runs.Count)（失败: $(($runs | Where-Object conclusion -eq 'failure').Count)）
单测 Shard 失败命中: $($hits.Count)  资源类: $resCount  断言类: $assertCount
资源类占比: $(if ($hits.Count) { '{0:P0}' -f ($resCount / $hits.Count) } else { 'N/A' })
$($hits | Format-Table -AutoSize | Out-String)
"@

# 写入报告文件（固定路径便于定时任务归档/检索）
if ($ReportPath) {
    $report | Out-File -FilePath $ReportPath -Encoding utf8
    Write-Host "报告已写入: $ReportPath"
}

if ($FailThreshold -gt 0 -and $resCount -ge $FailThreshold) {
    Write-Host "⚠️ 资源类失败($resCount) 达到阈值($FailThreshold) —— 触发告警" -ForegroundColor Red
    exit 1
}
exit 0
