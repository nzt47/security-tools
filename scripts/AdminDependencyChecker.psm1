<#
.SYNOPSIS
    AdminDependencyChecker 模块 - 静态扫描 PowerShell 脚本中的 admin-only 依赖

.DESCRIPTION
    用于在 CI 流程或本地环境中检测 PowerShell 脚本是否引入了管理员权限依赖.
    支持 11 种常见 admin-only 模式 (RunAsAdministrator / HKLM / 服务操作 / ACL 修改等).

    两种输出模式:
      - CI 模式 ($env:GITHUB_ACTIONS='true'): 输出 ::error::/::warning::/::notice:: workflow command
      - 本地模式: 颜色 + emoji 输出

.EXAMPLE
    Import-Module .\scripts\AdminDependencyChecker.psm1
    $result = Test-AdminDependency -Path .\scripts\rollback-protection.ps1
    if ($result.Violations.Count -gt 0) { exit 1 }

.EXAMPLE
    # 批量扫描多个文件
    Test-AdminDependency -Path @('scripts\a.ps1','scripts\b.ps1') -Verbose
#>

# 检测 CI 环境 (GitHub Actions 会设置 GITHUB_ACTIONS=true)
$script:isCI = $env:GITHUB_ACTIONS -eq 'true'

# ─── 内部辅助: 标准化输出 ──────────────────────────────────────
function Write-CiError {
    param([string]$Message, [string]$File = '', [int]$Line = 0)
    if ($script:isCI) {
        $loc = ''
        if ($File) {
            $loc = "file=$File"
            if ($Line -gt 0) { $loc += ",line=$Line" }
            $loc += '::'
        }
        Write-Host "::error $loc$Message"
    } else {
        Write-Host "  ❌ $Message" -ForegroundColor Red
    }
}

function Write-CiNotice {
    param([string]$Message)
    if ($script:isCI) {
        Write-Host "::notice::$Message"
    } else {
        Write-Host "  ℹ️  $Message" -ForegroundColor Cyan
    }
}

# ─── 默认 admin-only 模式表 ────────────────────────────────────
# 暴露为公共变量, 调用方可在 Import-Module 后追加自定义模式
$script:DefaultAdminPatterns = @(
    '#Requires\s+-\s*RunAsAdministrator',
    'WindowsBuiltInRole.*Administrator',
    'IsInRole.*Administrator',
    '-Verb\s+RunAs',
    'HKLM:\\|HKEY_LOCAL_MACHINE\\',
    'net\s+localgroup\s+administrators',
    'New-Service\b',
    'Set-Service\b',
    '\bicacls\b',
    '\btakeown\b',
    'Start-Process.*-Verb\s+RunAs'
)

<#
.SYNOPSIS
    扫描 PowerShell 脚本中的 admin-only 依赖

.DESCRIPTION
    逐行扫描目标文件, 检测是否匹配 admin-only 模式.
    自动跳过普通注释行 (# 开头但非 #Requires 指令).

.PARAMETER Path
    目标文件路径数组. 支持单文件字符串或字符串数组.

.PARAMETER Patterns
    可选: 自定义模式数组. 默认使用 $DefaultAdminPatterns.

.PARAMETER Quiet
    不输出进度信息, 仅返回结果对象.

.OUTPUTS
    [PSCustomObject] @{
        ScannedFiles  = [int]      # 实际扫描的文件数
        TotalFiles    = [int]      # 输入的文件总数 (含不存在的)
        Violations    = [array]    # 违规列表
        MissedFiles   = [array]    # 不存在被跳过的文件
        Passed        = [bool]      # 无违规时为 $true
    }
    Violations 元素结构:
      File    - 文件路径
      Line    - 行号
      Pattern - 匹配的模式
      Content - 行内容 (TrimStart 后)

.EXAMPLE
    Test-AdminDependency -Path .\scripts\rollback-protection.ps1

.EXAMPLE
    $r = Test-AdminDependency -Path (Get-ChildItem scripts\*.ps1).FullName
    if (-not $r.Passed) { $r.Violations | Format-Table }
#>
function Test-AdminDependency {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [ValidateNotNullOrEmpty()]
        [string[]]$Path,

        [string[]]$Patterns = $script:DefaultAdminPatterns,

        [switch]$Quiet
    )

    if (-not $Quiet) {
        Write-Host "=== 非管理员兼容性静态扫描 ===" -ForegroundColor Cyan
        Write-Host "扫描模式数: $($Patterns.Count) 种"
        Write-Host "目标文件数: $($Path.Count) 个"
        Write-Host ""
    }

    $violations = @()
    $missedFiles = @()
    $scannedCount = 0

    foreach ($file in $Path) {
        # 统一路径分隔符, 便于 ::error file=:: 定位
        $normalizedFile = ($file -replace '\\', '/').TrimStart('./')

        if (-not (Test-Path $file)) {
            if (-not $Quiet) {
                Write-Host "::warning file=$normalizedFile::文件不存在, 跳过"
            }
            $missedFiles += $file
            continue
        }

        $scannedCount++
        # [Fix] 用 @() 强制数组化: PowerShell 单行文件 Get-Content 返回 string 而非 string[],
        # 此时 $lines[0] 返回首字符, $lines.Count 误报为 1, 导致单行违规文件无法检测.
        # 这是 IsInRole/-Verb RunAs/New-Service 模式在单行测试中全部漏报的根因.
        $lines = @(Get-Content $file)
        if ($null -eq $lines) { $lines = @() }

        for ($i = 0; $i -lt $lines.Count; $i++) {
            $line = $lines[$i]
            foreach ($pat in $Patterns) {
                if ($line -match $pat) {
                    $trimmed = $line.TrimStart()
                    # 排除普通注释行 (# 开头但非 #Requires 指令)
                    if ($trimmed.StartsWith('#') -and $trimmed -notmatch '^#Requires') {
                        continue
                    }
                    $violations += [PSCustomObject]@{
                        File    = $normalizedFile
                        Line    = $i + 1
                        Pattern = $pat
                        Content = $trimmed
                    }
                }
            }
        }

        if (-not $Quiet) {
            Write-Host "  ✅ $normalizedFile 扫描完成"
        }
    }

    $result = [PSCustomObject]@{
        ScannedFiles = $scannedCount
        TotalFiles   = $Path.Count
        Violations   = $violations
        MissedFiles  = $missedFiles
        Passed       = ($violations.Count -eq 0)
    }

    if (-not $Quiet) {
        Write-Host ""
        Write-Host "扫描汇总: $scannedCount 扫描 / $($violations.Count) 违规 / $($missedFiles.Count) 跳过"
    }

    return $result
}

<#
.SYNOPSIS
    输出 Test-AdminDependency 结果的违规详情 (CI 模式自动用 ::error::)

.PARAMETER Result
    Test-AdminDependency 返回的结果对象

.EXAMPLE
    $r = Test-AdminDependency -Path .\scripts\foo.ps1 -Quiet
    Publish-AdminDependencyResult -Result $r
#>
function Publish-AdminDependencyResult {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        $Result
    )

    if ($Result.Passed) {
        Write-CiNotice "所有 $($Result.ScannedFiles) 个脚本均无 admin-only 依赖, 非管理员兼容性 OK"
        return
    }

    Write-Host ""
    Write-CiError "发现 $($Result.Violations.Count) 处 admin-only 依赖, 破坏非管理员兼容性"
    Write-Host ""
    Write-Host "违规详情:" -ForegroundColor Yellow
    foreach ($v in $Result.Violations) {
        Write-CiError -Message "匹配模式: $($v.Pattern) | 内容: $($v.Content)" -File $v.File -Line $v.Line
        Write-Host "  $($v.File):$($v.Line): $($v.Content)" -ForegroundColor Gray
    }
    Write-Host ""
    Write-Host "修复建议:" -ForegroundColor Cyan
    Write-Host "  1. 移除 #Requires -RunAsAdministrator 指令"
    Write-Host "  2. 用用户级替代方案 (env: / `$env:APPDATA / HKCU:)"
    Write-Host "  3. 避免修改系统级配置 (服务/注册表/ACL)"
}

Export-ModuleMember -Function Test-AdminDependency, Publish-AdminDependencyResult -Variable DefaultAdminPatterns
