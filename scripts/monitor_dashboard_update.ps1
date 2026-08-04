<#
.SYNOPSIS
    监控 CI run 的 update-ci-dashboard job 直到看板更新完成

.DESCRIPTION
    针对 run 30426327728 (commit c9f89218 "fix(types): 修复 env_config_manager.py
    的 6 个 mypy 类型错误") 的轮询监控脚本。

    执行阶段 C: 每 60s 查询 update-ci-dashboard job 状态，直到 completed。
    然后验证看板趋势行是否追加。

.NOTES
    run-id: 30426327728
    commit: c9f89218
    触发时间: 2026-07-29T05:51:50Z
    预期: unit-tests 约 30-45min + update-ci-dashboard 约 1min
    最大监控时长: 60 分钟（60 次 × 60s）
#>

#Requires -Version 5.1
$ErrorActionPreference = "Continue"

# ============================================================================
# 配置
# ============================================================================
$RUN_ID = "30426327728"
$TARGET_REPO = "nzt47/security-tools"
$DASHBOARD_PATH = "docs/dashboards/ci_health_dashboard.md"
$EXPECTED_SHA7 = "c9f8921"  # commit c9f89218 前 7 位，用于验证趋势行
$POLL_INTERVAL = 60  # 秒
$MAX_POLLS = 60      # 最大轮询次数（60min）

# ============================================================================
# 主流程
# ============================================================================
Write-Host "`n========== CI 看板更新监控 ==========" -ForegroundColor Cyan
Write-Host "Run ID: $RUN_ID"
Write-Host "Commit: $EXPECTED_SHA7"
Write-Host "监控目标: update-ci-dashboard job 完成 + 看板趋势行追加"
Write-Host "最大监控时长: $MAX_POLLS 分钟"
Write-Host ""

# Step 1: 轮询 update-ci-dashboard job
Write-Host "========== Step 1: 轮询 update-ci-dashboard job ==========" -ForegroundColor Cyan

$jobCompleted = $false
$jobConclusion = ""
$finalStatus = ""

for ($i = 1; $i -le $MAX_POLLS; $i++) {
    $timestamp = Get-Date -Format "HH:mm:ss"

    # 查询 update-ci-dashboard job 状态
    $jobInfo = gh run view $RUN_ID --json jobs --jq '.jobs[] | select(.name == "更新 CI 健康度看板") | .status + "|" + (.conclusion // "—")' 2>&1

    if ($LASTEXITCODE -ne 0) {
        Write-Host "[$timestamp] [$i/$MAX_POLLS] 查询失败: $jobInfo" -ForegroundColor Yellow
        Start-Sleep -Seconds $POLL_INTERVAL
        continue
    }

    # 解析状态
    $parts = $jobInfo -split '\|'
    $status = $parts[0]
    $conclusion = $parts[1]

    # 同时查 unit-tests 状态（看进度）
    $unitStatus = gh run view $RUN_ID --json jobs --jq '.jobs[] | select(.name | contains("3.10")) | .status + "|" + (.conclusion // "—")' 2>&1
    $unitParts = $unitStatus -split '\|'
    $unitState = $unitParts[0]
    $unitConcl = $unitParts[1]

    Write-Host "[$timestamp] [$i/$MAX_POLLS] unit-tests(3.10): $unitState/$unitConcl | dashboard: $status/$conclusion"

    if ($status -eq "completed") {
        $jobCompleted = $true
        $jobConclusion = $conclusion
        $finalStatus = $status
        Write-Host "[OK] update-ci-dashboard job 已完成: $conclusion" -ForegroundColor Green
        break
    }

    # 检查整个 run 是否已结束（可能 job 被 skipped）
    $runStatus = gh run view $RUN_ID --json status,conclusion --jq '.status + "|" + (.conclusion // "—")' 2>&1
    $runParts = $runStatus -split '\|'
    if ($runParts[0] -eq "completed") {
        $jobCompleted = $true
        $jobConclusion = $conclusion
        Write-Host "[INFO] 整个 run 已结束: $($runParts[0])/$($runParts[1])" -ForegroundColor Yellow
        Write-Host "       update-ci-dashboard job 最终状态: $conclusion"
        break
    }

    Start-Sleep -Seconds $POLL_INTERVAL
}

if (-not $jobCompleted) {
    Write-Host "`n[FATAL] 监控超时（$MAX_POLLS 分钟），job 未完成" -ForegroundColor Red
    Write-Host "       请手动检查: gh run view $RUN_ID"
    exit 1
}

# Step 2: 根据 job 结果决定下一步
Write-Host "`n========== Step 2: 验证看板更新 ==========" -ForegroundColor Cyan

if ($jobConclusion -eq "success") {
    Write-Host "[OK] update-ci-dashboard job 成功，验证看板趋势行..." -ForegroundColor Green

    # 拉取最新代码（job 会自动 commit + push 看板更新）
    Write-Host "拉取最新代码..."
    git pull origin master 2>&1

    # 验证趋势行
    Write-Host "验证看板是否包含 commit $EXPECTED_SHA7 的趋势行..."
    $trendLine = Select-String -Path $DASHBOARD_PATH -Pattern "\| $EXPECTED_SHA7 \|" | Select-Object -First 1

    if ($trendLine) {
        Write-Host "[OK] 看板已更新！趋势行:" -ForegroundColor Green
        Write-Host "  $($trendLine.Line)"

        # 显示看板最新 commit
        Write-Host "`n看板最近 commit:"
        git log --oneline -3 -- $DASHBOARD_PATH

        Write-Host "`n========== 监控成功完成 ==========" -ForegroundColor Green
        Write-Host "看板已自动追加趋势行，闭环验证通过"
        exit 0
    } else {
        Write-Host "[WARN] job 成功但看板未找到趋势行" -ForegroundColor Yellow
        Write-Host "       可能原因:"
        Write-Host "       1. 脚本占位行被误删（检查 docs/dashboards/ci_health_dashboard.md）"
        Write-Host "       2. junit.xml 解析失败（查 job 日志）"
        Write-Host "       3. git push 失败（查 job 日志的 git push step）"
        Write-Host "`n排查命令:"
        Write-Host "  gh run view $RUN_ID --log --job=<update-dashboard-job-id>"
        exit 2
    }

} elseif ($jobConclusion -eq "skipped") {
    Write-Host "[WARN] update-ci-dashboard job 被 skipped" -ForegroundColor Yellow
    Write-Host "       原因: needs: unit-tests 未成功（unit-tests failed/timeout）"
    Write-Host "`n排查 unit-tests 失败原因:"
    Write-Host "  gh run view $RUN_ID --json jobs --jq '.jobs[] | select(.name | contains(\"单元测试\")) | {name, conclusion}'"
    Write-Host "  gh run view $RUN_ID --log-failed"

    # 显示各 job 结论
    Write-Host "`n各 job 最终状态:"
    gh run view $RUN_ID --json jobs --jq '.jobs[] | "\(.name) | \(.conclusion // "—")"' 2>&1

    exit 3

} else {
    Write-Host "[FATAL] update-ci-dashboard job 失败: $jobConclusion" -ForegroundColor Red
    Write-Host "`n查看失败日志:"
    Write-Host "  gh run view $RUN_ID --log-failed"
    Write-Host "  gh run view $RUN_ID --json jobs --jq '.jobs[] | select(.name == \"更新 CI 健康度看板\") | .databaseId'"

    exit 4
}
