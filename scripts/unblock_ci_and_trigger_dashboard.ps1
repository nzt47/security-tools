<#
.SYNOPSIS
    CI 卡死解除 + 看板自动化验证脚本

.DESCRIPTION
    场景: 2026-07-29 诊断发现 commit 9c53ae88 的 CI run (30377427290)
          unit-tests job 卡住 ~16 小时（pytest 挂起在 C 扩展阻塞调用），
          阻塞了 a78324d8 的新 run 创建，导致 update-ci-dashboard job
          无法执行、看板趋势行未追加。

    本脚本执行 5 步:
      1. 取消卡住的 9c53ae88 run（释放阻塞）
      2. 提交 timeout-minutes: 45 修改（治本，防止未来卡死）
      3. push 触发新 CI run
      4. 检查新 run 是否创建，未创建则空推送兜底
      5. 列出新 run 供后续监控

.NOTES
    前置条件:
      - gh CLI 已登录 (gh auth status)
      - git 已配置用户信息
      - 当前位于 master 分支且工作区干净（除 ci.yml 的 timeout 修改）
      - 远程 origin 指向 github.com:nzt47/security-tools.git

    风险提示:
      - Step 1 取消 run 会终止 9c53ae88 的 3 个 in_progress unit-tests job
        （step 级状态已通过 gh api 留存，关键日志不丢）
      - Step 3 的 push 会触发完整 CI（含 update-ci-dashboard job）

    退出码:
      0 = 全部成功
      1 = 致命错误（git/gh 命令失败）
#>

#Requires -Version 5.1
$ErrorActionPreference = "Continue"  # 不因非致命错误中断，由显式检查处理

# ============================================================================
# 配置
# ============================================================================
$TARGET_REPO = "nzt47/security-tools"
$STUCK_RUN_ID = "30377427290"       # 9c53ae88 卡住的 run
$WORKFLOW_FILE = "ci.yml"
$DASHBOARD_PATH = "docs/dashboards/ci_health_dashboard.md"

# ============================================================================
# 前置检查
# ============================================================================
Write-Host "`n========== 前置检查 ==========" -ForegroundColor Cyan

# 检查分支
$currentBranch = git branch --show-current
if ($currentBranch -ne "master") {
    Write-Host "[FATAL] 当前分支非 master: $currentBranch" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] 当前分支: $currentBranch" -ForegroundColor Green

# 检查 gh 登录
$ghAuth = gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FATAL] gh CLI 未登录，请先执行 gh auth login" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] gh CLI 已登录" -ForegroundColor Green

# 检查 ci.yml 是否有未提交的 timeout-minutes 修改
$ciDiff = git diff --stat .github/workflows/ci.yml
if (-not $ciDiff) {
    Write-Host "[WARN] ci.yml 无未提交修改，timeout-minutes 可能已提交" -ForegroundColor Yellow
    Write-Host "       若已提交，Step 2 会跳过 commit 直接 push" -ForegroundColor Yellow
}

# ============================================================================
# Step 1: 取消卡住的 9c53ae88 run
# ============================================================================
Write-Host "`n========== Step 1: 取消卡住的 run $STUCK_RUN_ID ==========" -ForegroundColor Cyan

# 先查 run 当前状态（可能已自行结束）
$runStatus = gh run view $STUCK_RUN_ID --json status,conclusion --jq '.status + " | " + (.conclusion // "—")' 2>&1
Write-Host "当前状态: $runStatus"

if ($runStatus -like "*completed*" -or $runStatus -like "*cancelled*") {
    Write-Host "[SKIP] run 已结束，无需取消" -ForegroundColor Yellow
} else {
    Write-Host "正在取消..."
    gh run cancel $STUCK_RUN_ID
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] 取消成功" -ForegroundColor Green
    } else {
        Write-Host "[WARN] 取消失败（可能权限不足或 run 已结束）" -ForegroundColor Yellow
        Write-Host "       继续执行后续步骤" -ForegroundColor Yellow
    }

    # 等待 run 状态更新
    Start-Sleep -Seconds 5
    $newStatus = gh run view $STUCK_RUN_ID --json status,conclusion --jq '.status + " | " + (.conclusion // "—")' 2>&1
    Write-Host "取消后状态: $newStatus"
}

# ============================================================================
# Step 2: 提交 timeout-minutes: 45 修改
# ============================================================================
Write-Host "`n========== Step 2: 提交 timeout-minutes 修改 ==========" -ForegroundColor Cyan

if ($ciDiff) {
    Write-Host "暂存 ci.yml..."
    git add .github/workflows/ci.yml
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FATAL] git add 失败" -ForegroundColor Red
        exit 1
    }

    Write-Host "提交..."
    $commitMsg = @"
ci(unit-tests): 添加 timeout-minutes: 45 防止 C 扩展挂起

2026-07-29 排查 9c53ae88 run 卡住 16 小时发现:
--timeout-method=signal 的 SIGALRM 无法中断 C 扩展调用
(sentence_transformers/chromadb/sqlite-vec 的阻塞 join)
timeout-minutes 由 GitHub runner 强制 kill，是最后防线。

详见 docs/troubleshooting/ci_dashboard_update_failure_runbook.md
"@
    git commit -m $commitMsg
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FATAL] git commit 失败" -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] 提交成功" -ForegroundColor Green
} else {
    Write-Host "[SKIP] ci.yml 无修改，跳过提交" -ForegroundColor Yellow
}

# ============================================================================
# Step 3: push 触发新 CI run
# ============================================================================
Write-Host "`n========== Step 3: push 触发新 CI ==========" -ForegroundColor Cyan

$beforePushSha = git rev-parse HEAD
Write-Host "推送前 HEAD: $beforePushSha"

git push origin master
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FATAL] git push 失败" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] push 成功" -ForegroundColor Green

# ============================================================================
# Step 4: 检查新 run 是否创建，未创建则空推送兜底
# ============================================================================
Write-Host "`n========== Step 4: 检查新 run 创建 ==========" -ForegroundColor Cyan

Write-Host "等待 GitHub Actions 创建 run（15 秒）..."
Start-Sleep -Seconds 15

$afterPushSha = git rev-parse HEAD
Write-Host "推送后 HEAD: $afterPushSha"

# 查询新 SHA 是否有 run
$runCount = gh api "repos/$TARGET_REPO/actions/runs?head_sha=$afterPushSha" --jq '.workflow_runs | length' 2>&1
Write-Host "新 SHA 的 run 数量: $runCount"

if ([int]$runCount -eq 0) {
    Write-Host "[WARN] 新 run 未创建，使用空推送兜底触发" -ForegroundColor Yellow

    git commit --allow-empty -m "ci: 触发看板自动化验证（空推送兜底）"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FATAL] 空提交失败" -ForegroundColor Red
        exit 1
    }

    git push origin master
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FATAL] 空推送失败" -ForegroundColor Red
        exit 1
    }

    Write-Host "等待 GitHub Actions 创建 run（15 秒）..."
    Start-Sleep -Seconds 15

    $afterEmptySha = git rev-parse HEAD
    $runCount2 = gh api "repos/$TARGET_REPO/actions/runs?head_sha=$afterEmptySha" --jq '.workflow_runs | length' 2>&1
    Write-Host "空推送后 run 数量: $runCount2"

    if ([int]$runCount2 -eq 0) {
        Write-Host "[FATAL] 空推送后仍未创建 run，请检查 GitHub Actions 服务状态" -ForegroundColor Red
        Write-Host "       手动排查: https://github.com/$TARGET_REPO/actions" -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] 空推送触发成功" -ForegroundColor Green
} else {
    Write-Host "[OK] 新 run 已创建" -ForegroundColor Green
}

# ============================================================================
# Step 5: 列出新 run 供监控
# ============================================================================
Write-Host "`n========== Step 5: 新 run 状态 ==========" -ForegroundColor Cyan

Write-Host "最近 3 个 CI run:"
gh run list --workflow=$WORKFLOW_FILE --limit 3

Write-Host "`n========== 全部步骤完成 ==========" -ForegroundColor Green

Write-Host @"
后续监控命令:
  # 实时监控新 run（替换 <run-id>）
  gh run watch <run-id>

  # 查看 update-ci-dashboard job 状态
  gh run view <run-id> --json jobs --jq '.jobs[] | select(.name == "更新 CI 健康度看板") | {status, conclusion}'

  # CI 完成后验证看板更新
  git pull origin master
  git diff HEAD~1 $DASHBOARD_PATH

  # 若看板未更新，查看 job 日志
  gh run view <run-id> --log --job=<update-dashboard-job-id>
"@ -ForegroundColor Cyan

Write-Host "`n排查文档: docs/troubleshooting/ci_dashboard_update_failure_runbook.md" -ForegroundColor Cyan
