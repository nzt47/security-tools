<#
.SYNOPSIS
    [security] DeepSeek + OpenAI 密钥撤销验证脚本
.DESCRIPTION
    验证旧 API key 已在平台撤销（返回 401）+ 新 key 已配置且可用。
    本脚本不含任何硬编码密钥，旧 key 从临时文件或环境变量读取。
.NOTES
    关联文档：docs/security/KEY_REVOCATION_VERIFICATION_20260719.md
    安全设计：脚本本身可安全提交到 git，不含任何敏感信息
#>

param(
    [string]$OldKeyFile = "$env:TEMP\deepseek_old_key.txt",
    [string]$EnvFile = "c:\Users\Administrator\agent\.env",
    [string]$DeepSeekBaseUrl = "https://api.deepseek.com/chat/completions",
    [string]$OpenAiBaseUrl = "https://api.openai.com/v1/chat/completions",
    [switch]$SkipOldKeyTest,    # 跳过旧 key 测试（如果已知撤销）
    [switch]$SkipNewKeyTest,    # 跳过新 key 测试（如果未配置新 key）
    [switch]$SkipAppTest        # 跳过应用端测试
)

$ErrorActionPreference = "Continue"
$resultFile = "$env:TEMP\key_revocation_report_$(Get-Date -Format 'yyyyMMddHHmmss').txt"
$passCount = 0
$failCount = 0
$skipCount = 0

function Write-Section([string]$msg) {
    Write-Host "`n========== $msg ==========" -ForegroundColor Cyan
    Add-Content -Path $resultFile -Value "`n========== $msg =========="
}

function Write-Pass([string]$msg) {
    Write-Host "[PASS] $msg" -ForegroundColor Green
    Add-Content -Path $resultFile -Value "[PASS] $msg"
    $script:passCount++
}

function Write-Fail([string]$msg) {
    Write-Host "[FAIL] $msg" -ForegroundColor Red
    Add-Content -Path $resultFile -Value "[FAIL] $msg"
    $script:failCount++
}

function Write-Skip([string]$msg) {
    Write-Host "[SKIP] $msg" -ForegroundColor Yellow
    Add-Content -Path $resultFile -Value "[SKIP] $msg"
    $script:skipCount++
}

function Get-MaskedKey([string]$key) {
    if ($key -and $key.Length -gt 8) {
        return $key.Substring(0, 4) + "****" + $key.Substring($key.Length - 4)
    }
    return "****"
}

# ============================================================================
# 初始化报告文件
# ============================================================================
"=== DeepSeek + OpenAI 密钥撤销验证报告 ===" | Out-File $resultFile -Encoding UTF8
"时间戳: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Add-Content -Path $resultFile
"执行人: $env:USERNAME" | Add-Content -Path $resultFile

Write-Host "=== DeepSeek + OpenAI 密钥撤销验证 ===" -ForegroundColor Cyan
Write-Host "报告文件: $resultFile"
Write-Host ""

# ============================================================================
# 阶段 1：读取旧 key（用于验证已撤销）
# ============================================================================

Write-Section "阶段 1：读取旧 key"

$oldKey = $null
if (Test-Path $OldKeyFile) {
    $oldKey = (Get-Content $OldKeyFile -Raw -Encoding UTF8).Trim()
    Write-Pass "从 $OldKeyFile 读取到旧 key: $(Get-MaskedKey $oldKey)"
} elseif ($env:OLD_DEEPSEEK_KEY) {
    $oldKey = $env:OLD_DEEPSEEK_KEY
    Write-Pass "从环境变量 OLD_DEEPSEEK_KEY 读取到旧 key: $(Get-MaskedKey $oldKey)"
} else {
    Write-Skip "未找到旧 key 文件（$OldKeyFile）或环境变量 OLD_DEEPSEEK_KEY"
    Write-Host "  如需测试旧 key 是否已撤销，请执行：" -ForegroundColor Yellow
    Write-Host "    1. 将旧 key 保存到文件: 'sk-...' | Out-File $OldKeyFile -Encoding UTF8 -NoNewline" -ForegroundColor Yellow
    Write-Host "    2. 或设置环境变量: `$env:OLD_DEEPSEEK_KEY = 'sk-...'" -ForegroundColor Yellow
    Write-Host "    3. 重新运行本脚本" -ForegroundColor Yellow
}

# ============================================================================
# 阶段 2：从 .env 读取新 key
# ============================================================================

Write-Section "阶段 2：从 .env 读取新 key 配置"

$newDeepSeekKey = $null
$newOpenAiKey = $null
$newLlmKey = $null

if (Test-Path $EnvFile) {
    Write-Pass ".env 文件存在: $EnvFile"
    $envContent = Get-Content $EnvFile -Encoding UTF8

    # 读取 DeepSeek key
    $dsLine = $envContent | Where-Object { $_ -match "^DEEPSEEK_API_KEY=" } | Select-Object -First 1
    if ($dsLine) {
        $newDeepSeekKey = ($dsLine -split "=", 2)[1].Trim()
        if ($newDeepSeekKey -and $newDeepSeekKey -notmatch "^sk-your" -and $newDeepSeekKey -ne "") {
            Write-Pass ".env 中 DEEPSEEK_API_KEY 已配置: $(Get-MaskedKey $newDeepSeekKey)"
        } else {
            Write-Fail ".env 中 DEEPSEEK_API_KEY 仍为占位符: $newDeepSeekKey"
        }
    } else {
        Write-Fail ".env 中未找到 DEEPSEEK_API_KEY 配置"
    }

    # 读取 OpenAI key
    $openaiLine = $envContent | Where-Object { $_ -match "^OPENAI_API_KEY=" } | Select-Object -First 1
    if ($openaiLine) {
        $newOpenAiKey = ($openaiLine -split "=", 2)[1].Trim()
        if ($newOpenAiKey -and $newOpenAiKey -notmatch "^sk-your" -and $newOpenAiKey -ne "") {
            Write-Pass ".env 中 OPENAI_API_KEY 已配置: $(Get-MaskedKey $newOpenAiKey)"
        } else {
            Write-Fail ".env 中 OPENAI_API_KEY 仍为占位符: $newOpenAiKey"
        }
    } else {
        Write-Fail ".env 中未找到 OPENAI_API_KEY 配置"
    }

    # 读取 LLM key（可能复用 OpenAI key）
    $llmLine = $envContent | Where-Object { $_ -match "^LLM_API_KEY=" } | Select-Object -First 1
    if ($llmLine) {
        $newLlmKey = ($llmLine -split "=", 2)[1].Trim()
        if ($newLlmKey -and $newLlmKey -notmatch "^sk-your" -and $newLlmKey -ne "") {
            Write-Pass ".env 中 LLM_API_KEY 已配置: $(Get-MaskedKey $newLlmKey)"
        } else {
            Write-Fail ".env 中 LLM_API_KEY 仍为占位符: $newLlmKey"
        }
    }
} else {
    Write-Fail ".env 文件不存在: $EnvFile"
}

# ============================================================================
# 阶段 3：验证旧 key 已撤销（应返回 401）
# ============================================================================

Write-Section "阶段 3：验证旧 key 已撤销"

if ($SkipOldKeyTest) {
    Write-Skip "已跳过旧 key 测试（-SkipOldKeyTest）"
} elseif ($oldKey) {
    $testBody = @{
        model = "deepseek-chat"
        messages = @(@{ role = "user"; content = "ping" })
        max_tokens = 5
    } | ConvertTo-Json -Depth 5

    try {
        Write-Host "  测试旧 DeepSeek key: $(Get-MaskedKey $oldKey) ..." -NoNewline
        $response = Invoke-RestMethod -Uri $DeepSeekBaseUrl -Method Post `
            -Headers @{ "Authorization" = "Bearer $oldKey"; "Content-Type" = "application/json" } `
            -Body $testBody -ErrorAction Stop
        Write-Fail "旧 key 仍然有效（未撤销）！返回了正常响应"
    } catch {
        $statusCode = $_.Exception.Response.StatusCode.value__
        if ($statusCode -eq 401) {
            Write-Pass "旧 DeepSeek key 已正确撤销（返回 401 Unauthorized）"
        } elseif ($statusCode -eq 403) {
            Write-Pass "旧 DeepSeek key 已正确撤销（返回 403 Forbidden）"
        } else {
            Write-Fail "旧 key 返回异常状态码: $statusCode - $($_.Exception.Message)"
        }
    }

    # 测试旧 key 对 OpenAI 端点（同一 key 复用场景）
    try {
        Write-Host "  测试旧 key 对 OpenAI 端点..." -NoNewline
        $openaiBody = @{
            model = "gpt-4o-mini"
            messages = @(@{ role = "user"; content = "ping" })
            max_tokens = 5
        } | ConvertTo-Json -Depth 5
        $response = Invoke-RestMethod -Uri $OpenAiBaseUrl -Method Post `
            -Headers @{ "Authorization" = "Bearer $oldKey"; "Content-Type" = "application/json" } `
            -Body $openaiBody -ErrorAction Stop
        Write-Fail "旧 key 在 OpenAI 端点仍然有效（未撤销）"
    } catch {
        $statusCode = $_.Exception.Response.StatusCode.value__
        if ($statusCode -eq 401 -or $statusCode -eq 403) {
            Write-Pass "旧 key 在 OpenAI 端点已正确撤销（返回 $statusCode）"
        } else {
            Write-Skip "OpenAI 端点返回状态码 $statusCode（可能是 key 不适用于 OpenAI）"
        }
    }
} else {
    Write-Skip "无旧 key 可测试（未提供旧 key 文件或环境变量）"
}

# ============================================================================
# 阶段 4：验证新 key 可用
# ============================================================================

Write-Section "阶段 4：验证新 key 可用"

if ($SkipNewKeyTest) {
    Write-Skip "已跳过新 key 测试（-SkipNewKeyTest）"
} else {
    # 测试新 DeepSeek key
    if ($newDeepSeekKey -and $newDeepSeekKey -notmatch "^sk-your") {
        $testBody = @{
            model = "deepseek-chat"
            messages = @(@{ role = "user"; content = "ping" })
            max_tokens = 5
        } | ConvertTo-Json -Depth 5

        try {
            Write-Host "  测试新 DeepSeek key: $(Get-MaskedKey $newDeepSeekKey) ..." -NoNewline
            $response = Invoke-RestMethod -Uri $DeepSeekBaseUrl -Method Post `
                -Headers @{ "Authorization" = "Bearer $newDeepSeekKey"; "Content-Type" = "application/json" } `
                -Body $testBody -ErrorAction Stop
            Write-Pass "新 DeepSeek key 有效（返回: $($response.choices[0].message.content))"
        } catch {
            $statusCode = $_.Exception.Response.StatusCode.value__
            Write-Fail "新 DeepSeek key 测试失败（状态码: $statusCode）- $($_.Exception.Message)"
        }
    } else {
        Write-Skip "新 DeepSeek key 未配置或为占位符"
    }

    # 测试新 OpenAI key
    if ($newOpenAiKey -and $newOpenAiKey -notmatch "^sk-your") {
        $openaiBody = @{
            model = "gpt-4o-mini"
            messages = @(@{ role = "user"; content = "ping" })
            max_tokens = 5
        } | ConvertTo-Json -Depth 5

        try {
            Write-Host "  测试新 OpenAI key: $(Get-MaskedKey $newOpenAiKey) ..." -NoNewline
            $response = Invoke-RestMethod -Uri $OpenAiBaseUrl -Method Post `
                -Headers @{ "Authorization" = "Bearer $newOpenAiKey"; "Content-Type" = "application/json" } `
                -Body $openaiBody -ErrorAction Stop
            Write-Pass "新 OpenAI key 有效（返回: $($response.choices[0].message.content))"
        } catch {
            $statusCode = $_.Exception.Response.StatusCode.value__
            Write-Fail "新 OpenAI key 测试失败（状态码: $statusCode）- $($_.Exception.Message)"
        }
    } else {
        Write-Skip "新 OpenAI key 未配置或为占位符"
    }
}

# ============================================================================
# 阶段 5：验证应用端（/api/news 接口）
# ============================================================================

Write-Section "阶段 5：验证应用端"

if ($SkipAppTest) {
    Write-Skip "已跳过应用端测试（-SkipAppTest）"
} else {
    # 检查应用是否在运行
    try {
        $healthCheck = Invoke-RestMethod -Uri "http://localhost:5678/api/health" -Method Get -TimeoutSec 5 -ErrorAction Stop
        Write-Pass "应用运行中（/api/health 返回正常）"
        $appRunning = $true
    } catch {
        Write-Skip "应用未运行（无法测试 /api/news 接口）"
        Write-Host "  如需测试，请启动应用: python app_server.py" -ForegroundColor Yellow
        $appRunning = $false
    }

    if ($appRunning) {
        # 测试 /api/news 接口（依赖 DeepSeek key）
        try {
            Write-Host "  测试 /api/news 接口..." -NoNewline
            $newsResponse = Invoke-RestMethod -Uri "http://localhost:5678/api/news?topic=test&max=1" -Method Get -TimeoutSec 30 -ErrorAction Stop
            if ($newsResponse) {
                Write-Pass "/api/news 接口正常响应"
            } else {
                Write-Fail "/api/news 接口返回空响应"
            }
        } catch {
            Write-Fail "/api/news 接口测试失败: $($_.Exception.Message)"
        }
    }
}

# ============================================================================
# 阶段 6：验证 git 历史无旧 key 残留
# ============================================================================

Write-Section "阶段 6：验证 git 历史无旧 key 残留"

$repoPath = Split-Path $EnvFile -Parent
if (Test-Path "$repoPath\.git") {
    Push-Location $repoPath

    # 检查完整 key 是否在历史中（BFG 清理后应为 0）
    # 复用阶段 1 读取的 $oldKey，避免硬编码完整 key（安全设计）
    $fullKey = $oldKey
    if (-not $fullKey) {
        Write-Skip "无旧 key 可用于 git 历史检查（未提供旧 key 文件或环境变量）"
    } else {
        $keyCommits = git log -S $fullKey --oneline --all 2>$null
        $keyCount = ($keyCommits | Measure-Object -Line).Lines
        if ($keyCount -eq 0) {
            Write-Pass "git 历史中无完整 API key 残留（0 commits）"
        } else {
            Write-Fail "git 历史中仍有 $keyCount 个 commits 含完整 key"
            $keyCommits | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
        }
    }

    # 检查工作区是否含完整 key
    $grepResult = git grep -n $fullKey 2>$null
    if (-not $grepResult) {
        Write-Pass "工作区文件中无完整 API key"
    } else {
        Write-Fail "工作区文件中仍有完整 API key:"
        $grepResult | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
    }

    Pop-Location
} else {
    Write-Skip "未找到 git 仓库（$repoPath\.git）"
}

# ============================================================================
# 汇总报告
# ============================================================================

Write-Section "验证汇总"

$summary = @"
=== 验证汇总 ===
时间戳: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
通过: $passCount
失败: $failCount
跳过: $skipCount
总计: $($passCount + $failCount + $skipCount)

结论: $(if ($failCount -eq 0) { '✅ 全部通过' } else { '❌ 有失败项需要处理' })
"@

Write-Host $summary
Add-Content -Path $resultFile -Value $summary

Write-Host ""
Write-Host "报告已保存到: $resultFile" -ForegroundColor Cyan

if ($failCount -gt 0) {
    Write-Host "❌ 有 $failCount 项验证失败，请检查上述输出" -ForegroundColor Red
    exit 1
} else {
    Write-Host "✅ 所有验证通过" -ForegroundColor Green
    exit 0
}
