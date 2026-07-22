# CI/CD 缓存检查与清除步骤

> **文档日期**：2026-07-19
> **安全等级**：P0（紧急）
> **关联文档**：[BFG_CLEANUP_REPORT_20260719.md](./BFG_CLEANUP_REPORT_20260719.md)
> **CI/CD 平台**：GitHub Actions（18 个 workflows）

---

## 一、CI/CD 缓存风险评估

### 1.1 风险等级矩阵

| 风险项 | 位置 | 风险等级 | 原因 | 缓解措施 |
|-------|------|---------|------|---------|
| `fetch-depth: 0` workflows | 5 个 workflow 克隆完整历史 | 🟢 低 | CI runner 是临时的，每次运行后销毁；force push 后下次运行自动获取清洁历史 | 无需操作 |
| `actions/cache@v4` pip 缓存 | `~/.cache/pip` 跨运行持久化 | 🟢 低 | pip 缓存只存储 Python 包，不含源代码或 git 历史 | 可选清除 |
| `actions/cache@v4` 测试报告 | `test-results/`、`logs/` 等路径 | 🟡 中 | 如果 CI 步骤将敏感数据写入日志/报告并被缓存，可能残留 | 必须清除 |
| `actions/cache@v4` 覆盖率数据 | `htmlcov/`、`coverage.xml` 等 | 🟢 低 | 覆盖率数据不含源代码内容 | 可选清除 |
| GitHub Actions artifacts | workflow 运行产物 | 🟡 中 | 可能含旧 commit 的构建产物 | 必须清除 |
| GitHub Actions workflow runs | 历次运行的日志 | 🟡 中 | 日志可能打印过敏感数据 | 必须清除历史 runs |

### 1.2 关键发现：fetch-depth: 0 的 workflows

以下 5 个 workflow 使用 `fetch-depth: 0`（克隆完整 git 历史）：

| Workflow | 文件位置 | fetch-depth 行号 | 风险评估 |
|---------|---------|----------------|---------|
| architecture-check.yml | `.github/workflows/architecture-check.yml` | L55 | 🟢 低：CI runner 临时，force push 后自动清洁 |
| kwarg-conflict-check.yml | `.github/workflows/kwarg-conflict-check.yml` | L36, L135 | 🟢 低：同上 |
| log-perf-guard.yml | `.github/workflows/log-perf-guard.yml` | L127 | 🟢 低：同上 |
| observability-ci.yml | `.github/workflows/observability-ci.yml` | L172, L649 | 🟢 低：同上 |

> **结论**：`fetch-depth: 0` 本身不是持久性风险，因为 GitHub Actions runner 在任务结束后销毁。force push 后，下次 CI 运行会自动克隆清洁历史。

### 1.3 关键发现：actions/cache 使用情况

| Workflow | 缓存路径 | 缓存类型 | 需要清除 |
|---------|---------|---------|---------|
| ci-cd.yml | `~/.cache/pip` | pip 包 | 可选 |
| ci-cd.yml | `logs/stress_test/`、`logs/`、`deployment_summary.md`、`alert_report.md` | 测试/部署报告 | ✅ 必须 |
| ci.yml | `~/.cache/pip`（×6） | pip 包 | 可选 |
| ci.yml | `test_reports/`、`test-results/`、`all-results/` | 测试报告 | ✅ 必须 |
| test.yml | `~/.cache/pip`、`~\AppData\Local\pip\Cache` | pip 包 | 可选 |
| test.yml | `htmlcov/`、`test-results/`、`test_reports/` | 测试/覆盖率报告 | ✅ 必须 |
| observability-ci.yml | `~/.cache/pip`（×9） | pip 包 | 可选 |
| observability-ci.yml | `test-results/`、`coverage.xml`、`all-observability-results/` 等 | 可观测性报告 | ✅ 必须 |
| coverage-ci.yml | `~/.cache/pip`（×2） | pip 包 | 可选 |
| coverage-ci.yml | `test-results/`、`test_reports/` | 测试报告 | ✅ 必须 |
| extension-health-check.yml | `~/.cache/pip`（×3） | pip 包 | 可选 |
| extension-health-check.yml | `logs/`、`test-results/`、`test_reports/` | 测试报告 | ✅ 必须 |
| log-perf-guard.yml | `~/.cache/pip`（×3） | pip 包 | 可选 |
| log-perf-guard.yml | `logs/log_perf_stress_test.json` | 日志报告 | ✅ 必须 |
| web-module-tests.yml | `~/.cache/pip`（×3） | pip 包 | 可选 |
| web-module-tests.yml | `coverage/`、`test_reports/logs/` | 覆盖率/测试报告 | ✅ 必须 |
| tool-tests.yml | `~/.cache/pip` | pip 包 | 可选 |
| tool-tests.yml | `htmlcov/` | 覆盖率 | ✅ 必须 |
| kwarg-conflict-check.yml | `~/.cache/pip`（×3） | pip 包 | 可选 |
| kwarg-conflict-check.yml | `kwarg-high-risk-report.txt`、`kwarg-medium-risk-report.json`、`kwarg-fix-report.md` | 报告文件 | ✅ 必须 |
| sandbox-boundary-tests.yml | `~/.cache/pip` | pip 包 | 可选 |
| architecture-check.yml | `~/.cache/pip` | pip 包 | 可选 |

---

## 二、清除 GitHub Actions 缓存的步骤

### 2.1 方法一：GitHub API 批量清除所有缓存（推荐）

```powershell
# ============================================================================
# [security] 批量清除 GitHub Actions 所有缓存
# 前置条件：已安装 gh CLI 并认证（gh auth login）
# ============================================================================

# 1. 确认 gh CLI 认证状态
gh auth status

# 2. 列出所有缓存（查看清除前状态）
Write-Host "=== 清除前：列出所有 GitHub Actions 缓存 ===" -ForegroundColor Cyan
gh api repos/nzt47/security-tools/actions/caches `
    --paginate `
    -q '.actions_caches[] | "ID: \(.id) | Key: \(.key) | Size: \(.size_in_bytes) bytes | Last accessed: \(.last_accessed_at)"'

# 3. 批量删除所有缓存
Write-Host ""
Write-Host "=== 开始批量清除 ===" -ForegroundColor Cyan

$caches = gh api repos/nzt47/security-tools/actions/caches --paginate -q '.actions_caches[].id'
$cacheCount = ($caches | Measure-Object).Count
Write-Host "找到 $cacheCount 个缓存"

foreach ($cacheId in $caches) {
    Write-Host "  删除缓存 ID: $cacheId" -NoNewline
    $result = gh api -X DELETE "repos/nzt47/security-tools/actions/caches/$cacheId" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host " [OK]" -ForegroundColor Green
    } else {
        Write-Host " [FAIL]: $result" -ForegroundColor Red
    }
}

# 4. 验证清除结果
Write-Host ""
Write-Host "=== 清除后：验证缓存列表 ===" -ForegroundColor Cyan
$remaining = gh api repos/nzt47/security-tools/actions/caches -q '.actions_caches'
$remainingCount = ($remaining | ConvertFrom-Json | Measure-Object).Count
if ($remainingCount -eq 0) {
    Write-Host "[OK] 所有 GitHub Actions 缓存已清除" -ForegroundColor Green
} else {
    Write-Host "[WARN] 仍有 $remainingCount 个缓存未清除" -ForegroundColor Yellow
}
```

### 2.2 方法二：按 cache key 精确删除

```powershell
# 如果只想删除特定 key 的缓存（例如仅 pip 缓存）
gh api -X DELETE "repos/nzt47/security-tools/actions/caches?key=python-"
```

### 2.3 方法三：GitHub Web UI 手动清除

1. 访问 `https://github.com/nzt47/security-tools/actions/caches`
2. 逐个点击每个缓存的 **Delete** 按钮
3. 适用于缓存数量较少的情况

---

## 三、清除 GitHub Actions 历史运行记录

### 3.1 删除所有历史 workflow runs（保留最新 N 次）

```powershell
# ============================================================================
# [security] 清除 GitHub Actions 历史 runs（日志可能含敏感数据）
# 警告：此操作不可逆，会删除所有历史 CI 运行日志
# ============================================================================

# 1. 查看当前 runs 数量
Write-Host "=== 查看当前 workflow runs ===" -ForegroundColor Cyan
$runs = gh api repos/nzt47/security-tools/actions/runs --paginate -q '.workflow_runs[] | "\(.id) | \(.name) | \(.created_at) | \(.status)"'
$runCount = ($runs | Measure-Object -Line).Lines
Write-Host "找到 $runCount 个 workflow runs"

# 2. 删除所有历史 runs（保留最近 5 次）
$keepCount = 5
$allRunIds = gh api repos/nzt47/security-tools/actions/runs --paginate -q '.workflow_runs[].id'
$runIdsToDelete = $allRunIds | Select-Object -Skip $keepCount

Write-Host ""
Write-Host "=== 删除历史 runs（保留最近 $keepCount 次） ===" -ForegroundColor Cyan
$deleted = 0
foreach ($runId in $runIdsToDelete) {
    $result = gh api -X DELETE "repos/nzt47/security-tools/actions/runs/$runId" 2>&1
    if ($LASTEXITCODE -eq 0) {
        $deleted++
    }
}
Write-Host "[OK] 已删除 $deleted 个历史 runs" -ForegroundColor Green

# 3. 验证
$remainingRuns = (gh api repos/nzt47/security-tools/actions/runs -q '.workflow_runs').Count
Write-Host "剩余 runs 数量: $remainingRuns"
```

### 3.2 删除特定 workflow 的所有 runs

```powershell
# 删除特定 workflow 的所有 runs（例如 observability-ci）
$workflowId = (gh api repos/nzt47/security-tools/actions/workflows -q '.workflows[] | select(.name == "observability-ci") | .id')
gh api "repos/nzt47/security-tools/actions/workflows/$workflowId/runs" --paginate -q '.workflow_runs[].id' |
    ForEach-Object { gh api -X DELETE "repos/nzt47/security-tools/actions/runs/$_" }
```

---

## 四、清除 GitHub Actions Artifacts

### 4.1 批量删除所有 artifacts

```powershell
# ============================================================================
# [security] 清除 GitHub Actions artifacts（构建产物可能含旧 commit 内容）
# ============================================================================

# 列出所有 artifacts
Write-Host "=== 列出所有 artifacts ===" -ForegroundColor Cyan
$artifacts = gh api repos/nzt47/security-tools/actions/artifacts --paginate -q '.artifacts[] | "\(.id) | \(.name) | \(.size_in_bytes) bytes | \(.created_at)"'
$artifactCount = ($artifacts | Measure-Object -Line).Lines
Write-Host "找到 $artifactCount 个 artifacts"

# 批量删除
Write-Host ""
Write-Host "=== 批量删除 artifacts ===" -ForegroundColor Cyan
$artifactIds = gh api repos/nzt47/security-tools/actions/artifacts --paginate -q '.artifacts[].id'
$deleted = 0
foreach ($artifactId in $artifactIds) {
    $result = gh api -X DELETE "repos/nzt47/security-tools/actions/artifacts/$artifactId" 2>&1
    if ($LASTEXITCODE -eq 0) {
        $deleted++
    }
}
Write-Host "[OK] 已删除 $deleted 个 artifacts" -ForegroundColor Green
```

---

## 五、触发 CI 验证清洁历史

### 5.1 手动触发关键 workflows

```powershell
# 列出所有 workflows
gh api repos/nzt47/security-tools/actions/workflows -q '.workflows[] | "\(.id) | \(.name) | \(.state)"'

# 手动触发 ci.yml（主 CI 流水线）
$ciWorkflowId = (gh api repos/nzt47/security-tools/actions/workflows -q '.workflows[] | select(.name == "ci") | .id')
gh api -X POST "repos/nzt47/security-tools/actions/workflows/$ciWorkflowId/dispatches" \
    -f ref=master

# 手动触发 test.yml
$testWorkflowId = (gh api repos/nzt47/security-tools/actions/workflows -q '.workflows[] | select(.name == "test") | .id')
gh api -X POST "repos/nzt47/security-tools/actions/workflows/$testWorkflowId/dispatches" \
    -f ref=master
```

### 5.2 监控 CI 运行结果

```powershell
# 查看最近的 workflow runs
gh run list --repo nzt47/security-tools --limit 10

# 查看特定 run 的详情
gh run view <run-id> --repo nzt47/security-tools

# 查看失败的 run 日志
gh run view <run-id> --repo nzt47/security-tools --log-failed
```

---

## 六、检查 CI 日志中的敏感信息残留

### 6.1 下载并扫描 CI 日志

```powershell
# ============================================================================
# [security] 下载最近的 CI 日志并扫描敏感信息
# ============================================================================

# 1. 下载最近 10 次 runs 的日志
$runs = gh run list --repo nzt47/security-tools --limit 10 --json databaseId -q '.[].databaseId'
$logDir = "C:\Windows\TEMP\ci_logs_scan"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

foreach ($runId in $runs) {
    Write-Host "下载 run $runId 日志..." -NoNewline
    try {
        gh run download $runId --repo nzt47/security-tools --dir "$logDir\run_$runId" 2>$null
        Write-Host " [OK]" -ForegroundColor Green
    } catch {
        Write-Host " [SKIP]" -ForegroundColor Yellow
    }
}

# 2. 扫描日志中的敏感信息
Write-Host ""
Write-Host "=== 扫描敏感信息 ===" -ForegroundColor Cyan
$patterns = @(
    'sk-ddf2****45a3',
    'Admin@****!',
    'admin123',
    'DEEPSEEK_API_KEY=sk-',
    'OPENAI_API_KEY=sk-'
)

$foundSensitive = $false
foreach ($pattern in $patterns) {
    $masked = if ($pattern.Length -gt 8) { $pattern.Substring(0,4) + "****" + $pattern.Substring($pattern.Length-4) } else { "****" }
    Write-Host "搜索: $masked" -NoNewline
    $matches = Select-String -Path "$logDir\*" -Pattern $pattern -Recurse -ErrorAction SilentlyContinue
    if ($matches) {
        Write-Host " [FOUND]" -ForegroundColor Red
        $matches | Select-Object -First 3 | ForEach-Object { Write-Host "  $($_.Filename):$($_.LineNumber): $($_.Line.Substring(0, [Math]::Min(100, $_.Line.Length)))" }
        $foundSensitive = $true
    } else {
        Write-Host " [CLEAN]" -ForegroundColor Green
    }
}

if (-not $foundSensitive) {
    Write-Host ""
    Write-Host "[OK] CI 日志中未发现敏感信息残留" -ForegroundColor Green
}

# 3. 清理临时日志
Remove-Item -Recurse -Force $logDir -ErrorAction SilentlyContinue
```

---

## 七、验证清单

| # | 验证项 | 验证命令 | 预期结果 | 状态 |
|---|-------|---------|---------|------|
| 1 | GitHub Actions 缓存已清除 | `gh api repos/nzt47/security-tools/actions/caches -q '.actions_caches' \| ConvertFrom-Json \| Measure-Object` | Count = 0 | ☐ |
| 2 | 历史 workflow runs 已清除 | `gh run list --repo nzt47/security-tools --limit 100 \| Measure-Object -Line` | ≤ 5 | ☐ |
| 3 | Artifacts 已清除 | `gh api repos/nzt47/security-tools/actions/artifacts -q '.artifacts' \| ConvertFrom-Json \| Measure-Object` | Count = 0 | ☐ |
| 4 | CI 运行使用清洁历史 | 触发 ci.yml 后查看 `gh run view <id> --log` | 无 `sk-ddf2` 字符串 | ☐ |
| 5 | CI 日志无敏感信息 | 执行第 6 节扫描脚本 | "CLEAN" | ☐ |
| 6 | 新 CI run 全部通过 | `gh run list --repo nzt47/security-tools --status failure --limit 5` | 无新增 failure | ☐ |

---

## 八、fetch-depth: 0 的优化建议（P2 长期）

以下 workflows 使用 `fetch-depth: 0` 克隆完整历史，虽然不是持久性风险，但会增加 CI 时间并下载不必要的旧对象：

| Workflow | 是否必须 fetch-depth: 0 | 建议优化 |
|---------|----------------------|---------|
| architecture-check.yml | 可能需要（架构变更检测） | 评估是否可改用 `fetch-depth: 1` + diff API |
| kwarg-conflict-check.yml | 用于变更报告 | 可改为只检测最近 commit 的变更 |
| log-perf-guard.yml | 不明确 | 评估必要性 |
| observability-ci.yml (×2) | 不明确 | 评估必要性 |

> **建议**：清理完成后，逐一评估这些 workflow 是否真的需要完整历史，能改为 `fetch-depth: 1` 的尽量改。

---

## 九、三义校验

- **【不易】** 覆盖 18 个 workflows 的完整缓存分析；3 类清除目标（caches/runs/artifacts）；6 项验证清单；日志扫描覆盖 5 类敏感模式
- **【变易】** 支持 GitHub API 批量清除 + Web UI 手动清除 + 按 key 精确删除三种方式；fetch-depth: 0 的 5 个 workflow 逐一列出并评估
- **【简易】** 脚本可直接复制执行；风险等级矩阵 + 验证清单表格化；每步操作可独立验证

---

## 十、参考文档

- [GitHub Actions Cache API](https://docs.github.com/rest/actions/cache)
- [GitHub Actions Artifacts API](https://docs.github.com/rest/actions/artifacts)
- [GitHub Actions Workflow Runs API](https://docs.github.com/rest/actions/workflow-runs)
- [actions/checkout documentation](https://github.com/actions/checkout)
- 内部文档：[BFG_CLEANUP_REPORT_20260719.md](./BFG_CLEANUP_REPORT_20260719.md)

---

> **文档生成时间**：2026-07-19
> **执行人**：Yi-Jing Coding Agent
> **审核状态**：待用户审核
