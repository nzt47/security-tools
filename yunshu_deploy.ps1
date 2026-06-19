#Requires -Version 5.1
<#
.SYNOPSIS
    云枢一键部署工具 - 整合沙盒修复、启动、测试功能
.DESCRIPTION
    自动化部署云枢环境，包括：
    - 修复沙盒配置问题
    - 设置环境变量
    - 启动服务
    - 验证功能
.PARAMETER Mode
    full: 完整部署（修复+启动+测试）
    fix: 仅修复配置
    start: 仅启动服务
    test: 仅运行测试
    clean: 清理测试文件
.PARAMETER VerboseLog
    启用详细日志输出（包含堆栈信息）
.EXAMPLE
    .\yunshu_deploy.ps1 full
    .\yunshu_deploy.ps1 fix
    .\yunshu_deploy.ps1 start -Sandbox enable
    .\yunshu_deploy.ps1 test -BatchCount 20
    .\yunshu_deploy.ps1 full -VerboseLog
#>

param(
    [Parameter(Position=0)]
    [ValidateSet("full", "fix", "start", "test", "clean")]
    [string]$Mode = "full",
    
    [ValidateSet("enable", "disable")]
    [string]$Sandbox = "disable",
    
    [int]$BatchCount = 10,
    
    [switch]$SkipTest,
    
    [switch]$SkipLLM,
    
    [switch]$VerboseLog,
    
    [switch]$AutoRestart
)

# 设置输出编码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 全局变量：执行统计
$Global:DeployStats = @{
    StartTime = Get-Date
    StepsCompleted = 0
    Errors = @()
    Warnings = @()
}

# 日志文件路径
$Global:LogFile = Join-Path $PWD.Path "logs\deploy_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

# 日志函数（增强版）
function Write-Log {
    param(
        [string]$Message,
        [string]$Level = "INFO",
        [string]$Detail = ""
    )
    
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    $colors = @{
        "INFO" = "Cyan"
        "SUCCESS" = "Green"
        "WARNING" = "Yellow"
        "ERROR" = "Red"
        "STEP" = "Magenta"
        "DEBUG" = "Gray"
        "DETAIL" = "DarkGray"
    }
    # PowerShell 5.1 不支持 ?: 运算符
    if ($colors.ContainsKey($Level)) {
        $color = $colors[$Level]
    } else {
        $color = "White"
    }
    
    $prefix = @{
        "STEP" = ">>>"
        "INFO" = "---"
        "SUCCESS" = "[+]"
        "WARNING" = "[!]"
        "ERROR" = "[X]"
        "DEBUG" = "[D]"
        "DETAIL" = "   "
    }
    if ($prefix.ContainsKey($Level)) {
        $p = $prefix[$Level]
    } else {
        $p = "---"
    }
    
    # 输出到控制台
    Write-Host "$p [$timestamp] $Message" -ForegroundColor $color
    
    # 详细信息（仅在 VerboseLog 模式下显示）
    if ($Detail -and $VerboseLog) {
        Write-Host "   $Detail" -ForegroundColor $colors["DETAIL"]
    }
    
    # 写入日志文件
    $logDir = Join-Path $PWD.Path "logs"
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    $logLine = "$p [$timestamp] [$Level] $Message"
    if ($Detail) { $logLine += " | $Detail" }
    $logLine | Out-File -FilePath $Global:LogFile -Encoding UTF8 -Append
    
    # 记录错误和警告
    if ($Level -eq "ERROR") {
        $Global:DeployStats.Errors += $Message
    }
    if ($Level -eq "WARNING") {
        $Global:DeployStats.Warnings += $Message
    }
}

# 步骤分隔符
function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Log "========== $Message ==========" "STEP"
    $Global:DeployStats.StepsCompleted++
}

# 详细日志函数
function Write-Detail {
    param([string]$Message)
    if ($VerboseLog) {
        Write-Log $Message "DETAIL"
    }
}

# 错误处理函数（带堆栈）
function Write-ErrorDetail {
    param(
        [string]$Message,
        [System.Management.Automation.ErrorRecord]$ErrorRecord
    )
    Write-Log $Message "ERROR"
    if ($VerboseLog -and $ErrorRecord) {
        Write-Log "  异常类型: $($ErrorRecord.Exception.GetType().FullName)" "DETAIL"
        Write-Log "  异常消息: $($ErrorRecord.Exception.Message)" "DETAIL"
        Write-Log "  堆栈跟踪: $($ErrorRecord.ScriptStackTrace)" "DETAIL"
    }
}

# 环境检查函数
function Check-Environment {
    Write-Step "步骤 0: 环境检查"
    
    # 检查 Python
    Write-Log "检查 Python 环境..." "INFO"
    try {
        $pythonVersion = python --version 2>&1
        if ($pythonVersion -match "Python (\d+\.\d+\.\d+)") {
            $ver = $matches[1]
            Write-Log "Python 版本: $ver" "SUCCESS"
            Write-Detail "Python 路径: $(Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)"
            
            # 检查 Python 版本是否 >= 3.8
            $verParts = $ver.Split('.')
            if ([int]$verParts[0] -lt 3 -or ([int]$verParts[0] -eq 3 -and [int]$verParts[1] -lt 8)) {
                Write-Log "Python 版本过低，建议使用 3.8+" "WARNING"
            }
        } else {
            Write-Log "无法获取 Python 版本" "WARNING"
        }
    } catch {
        Write-ErrorDetail "Python 未安装或不在 PATH 中" $_
        return $false
    }
    
    # 检查关键 Python 包
    Write-Log "检查 Python 依赖包..." "INFO"
    $requiredPackages = @("flask", "requests", "pyyaml")
    foreach ($pkg in $requiredPackages) {
        try {
            $pkgVersion = python -c "import $pkg; print($pkg.__version__)" 2>&1
            if ($pkgVersion -notmatch "Error|Traceback") {
                Write-Log "  ${pkg}: ${pkgVersion}" "SUCCESS"
                if ($VerboseLog) { Write-Log "    已安装" "DETAIL" }
            } else {
                Write-Log "  ${pkg}: 未安装" "WARNING"
            }
        } catch {
            Write-Log "  ${pkg}: 检查失败" "WARNING"
        }
    }
    
    # 检查 PowerShell 版本
    Write-Log "检查 PowerShell 环境..." "INFO"
    $psVersion = $PSVersionTable.PSVersion.ToString()
    Write-Log "PowerShell 版本: $psVersion" "SUCCESS"
    Write-Detail "PS Edition: $($PSVersionTable.PSEdition)"
    
    # 检查关键文件
    Write-Log "检查关键文件..." "INFO"
    $criticalFiles = @("config.py", "app_server.py", "requirements.txt")
    foreach ($file in $criticalFiles) {
        $filePath = Join-Path $PWD.Path $file
        if (Test-Path $filePath) {
            $fileSize = (Get-Item $filePath).Length
            Write-Log "  ${file}: 存在 (${fileSize} 字节)" "SUCCESS"
        } else {
            Write-Log "  ${file}: 不存在" "ERROR"
        }
    }
    
    # 检查 workspace 目录
    $workspaceDir = Join-Path $PWD.Path "workspace"
    if (Test-Path $workspaceDir) {
        Write-Log "workspace 目录: 存在" "SUCCESS"
    } else {
        Write-Log "workspace 目录: 不存在，将自动创建" "WARNING"
        New-Item -ItemType Directory -Path $workspaceDir -Force | Out-Null
        Write-Log "workspace 目录: 已创建" "SUCCESS"
    }
    
    # 检查端口状态
    Write-Log "检查端口状态..." "INFO"
    $ports = @(5678, 8123)
    foreach ($port in $ports) {
        $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($conn) {
            $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
            Write-Log "  端口 $port: 已占用 ($($proc.ProcessName), PID: $($conn.OwningProcess))" "WARNING"
        } else {
            Write-Log "  端口 $port: 可用" "SUCCESS"
        }
    }
    
    return $true
}

# 获取脚本目录
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = $PWD.Path }
Set-Location $ScriptDir

Write-Host @"

╔══════════════════════════════════════════════════════════════╗
║           云枢一键部署工具 v2.0 (2026-06-15)                 ║
╠══════════════════════════════════════════════════════════════╣
║  Mode: $Mode`n`t`t`t`t   Sandbox: $Sandbox                      ║
║  VerboseLog: $VerboseLog                                      ║
║  日志文件: $Global:LogFile                                    ║
╚══════════════════════════════════════════════════════════════╝

"@ -ForegroundColor Cyan

# ============================================================
# 步骤 0: 环境检查（所有模式都执行）
# ============================================================
if ($Mode -ne "clean") {
    Check-Environment
}

# ============================================================
# 步骤 1: 修复配置
# ============================================================
if ($Mode -eq "full" -or $Mode -eq "fix") {
    Write-Step "步骤 1: 检查并修复配置"
    
    # 检查 config.py 是否有环境变量处理
    $configFile = Join-Path $ScriptDir "config.py"
    $configContent = Get-Content $configFile -Raw -ErrorAction SilentlyContinue
    
    if ($configContent -notmatch "YUNSHU_FEATURE_SANDBOX") {
        Write-Log "config.py 缺少 YUNSHU_FEATURE_SANDBOX 环境变量处理" "WARNING"
        Write-Log "正在添加修复代码..." "INFO"
        
        # 查找 _load_from_env 方法
        if ($configContent -match "(def _load_from_env\(\):.*?(?=\n    def |\nclass |\Z))") {
            $methodContent = $matches[1]
            
            # 检查是否已有功能开关处理
            if ($methodContent -notmatch "# 功能开关") {
                # 添加功能开关处理
                $addCode = @"

        # 功能开关
        sandbox_env = os.getenv("YUNSHU_FEATURE_SANDBOX", "").strip().lower()
        if sandbox_env in ("true", "1", "yes", "on"):
            self._data["features"]["sandbox"] = True
        elif sandbox_env in ("false", "0", "no", "off"):
            self._data["features"]["sandbox"] = False
"@
                $configContent = $configContent -replace "(def _load_from_env\(\):.*?os\.getenv\(""LLM_MODEL""\)\s*\n(.*?)(?=\n    def )", "`$1os.getenv(`"LLM_MODEL`")`n$2$addCode`n"
                
                $configContent | Set-Content -Path $configFile -Encoding UTF8
                Write-Log "已添加 YUNSHU_FEATURE_SANDBOX 环境变量处理" "SUCCESS"
            }
        }
    } else {
        Write-Log "config.py 已包含 YUNSHU_FEATURE_SANDBOX 处理" "SUCCESS"
    }
    
    # 检查 app_server.py 是否有启动时加载
    $appServerFile = Join-Path $ScriptDir "app_server.py"
    $appServerContent = Get-Content $appServerFile -Raw -ErrorAction SilentlyContinue
    
    if ($appServerContent -notmatch "已从 network_config\.json 加载网络配置") {
        Write-Log "app_server.py 缺少启动时加载 network_config.json" "WARNING"
        Write-Log "跳过修复（需要手动修改）" "WARNING"
    } else {
        Write-Log "app_server.py 启动加载已就绪" "SUCCESS"
    }
}

# ============================================================
# 步骤 2: 设置环境变量
# ============================================================
if ($Mode -eq "full" -or $Mode -eq "start") {
    Write-Step "步骤 2: 设置环境变量"
    
    # 沙盒开关
    $env:YUNSHU_FEATURE_SANDBOX = if ($Sandbox -eq "enable") { 'true' } else { 'false' }
    Write-Log "YUNSHU_FEATURE_SANDBOX = $($env:YUNSHU_FEATURE_SANDBOX)" "SUCCESS"
    
    # LLM 配置（从 .env 文件读取）
    if (-not $SkipLLM) {
        $envFile = Join-Path $ScriptDir ".env"
        if (Test-Path $envFile) {
            $envVars = @{}
            Get-Content $envFile | ForEach-Object {
                if ($_ -match '^([^=]+)=(.*)$') {
                    $envVars[$matches[1].Trim()] = $matches[2].Trim().Trim('"').Trim("'")
                }
            }
            
            if ($envVars['LLM_API_KEY']) {
                $env:LLM_API_KEY = $envVars['LLM_API_KEY']
                Write-Log "LLM_API_KEY = **** (已从 .env 加载)" "SUCCESS"
            }
            if ($envVars['LLM_PROVIDER']) {
                $env:LLM_PROVIDER = $envVars['LLM_PROVIDER']
                Write-Log "LLM_PROVIDER = $($env:LLM_PROVIDER)" "SUCCESS"
            }
            if ($envVars['LLM_MODEL']) {
                $env:LLM_MODEL = $envVars['LLM_MODEL']
                Write-Log "LLM_MODEL = $($env:LLM_MODEL)" "SUCCESS"
            }
        } else {
            Write-Log ".env 文件不存在，跳过 LLM 配置" "WARNING"
        }
    }
}

# ============================================================
# 步骤 3: 启动服务
# ============================================================
if ($Mode -eq "full" -or $Mode -eq "start") {
    Write-Step "步骤 3: 启动云枢服务"
    
    # 检查端口占用
    $port = 5678
    $proc = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($proc) {
        $procInfo = Get-Process -Id $proc.OwningProcess -ErrorAction SilentlyContinue
        $procName = $procInfo.ProcessName
        $procId = $proc.OwningProcess
        Write-Log "端口 $port 已被占用: ${procName} (PID: ${procId})" "WARNING"
        
        # 自动处理或手动确认
        if ($AutoRestart) {
            Write-Log "自动重启模式: 停止现有进程..." "INFO"
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
            
            # 验证进程已停止
            $verifyProc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if (-not $verifyProc) {
                Write-Log "进程 ${procId} 已停止" "SUCCESS"
            } else {
                Write-Log "进程停止失败，尝试其他方法..." "WARNING"
                # 尝试通过任务kill
                try {
                    taskkill /PID $procId /F /T 2>&1 | Out-Null
                    Start-Sleep -Seconds 1
                    Write-Log "已使用 taskkill 强制终止" "SUCCESS"
                } catch {
                    Write-Log "无法停止进程，请手动处理" "ERROR"
                    $SkipTest = $true
                }
            }
        } else {
            # 非自动模式，尝试自动停止
            Write-Log "正在尝试停止现有进程..." "INFO"
            try {
                Stop-Process -Id $procId -Force -ErrorAction Stop
                Start-Sleep -Seconds 2
                Write-Log "进程已自动停止" "SUCCESS"
            } catch {
                Write-Log "自动停止失败，询问用户..." "WARNING"
                $response = Read-Host "是否强制重启现有服务? (y/n)"
                if ($response -eq 'y') {
                    Write-Log "停止现有进程..." "INFO"
                    taskkill /PID $procId /F /T 2>&1 | Out-Null
                    Start-Sleep -Seconds 2
                    Write-Log "进程已停止" "SUCCESS"
                } else {
                    Write-Log "跳过启动" "WARNING"
                    $SkipTest = $true
                }
            }
        }
    }
    
    if (-not $SkipTest) {
        # 再次检查端口
        $checkPort = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($checkPort) {
            Write-Log "端口 $port 仍被占用，跳过启动" "ERROR"
            $SkipTest = $true
        } else {
            Write-Log "启动 python app_server.py ..." "INFO"
            Write-Host ""
            python app_server.py
        }
    }
}

# ============================================================
# 步骤 4: 运行测试
# ============================================================
if (($Mode -eq "full" -or $Mode -eq "test") -and -not $SkipTest) {
    Write-Step "步骤 4: 运行功能测试"
    
    # 等待服务启动
    Write-Log "等待服务启动..." "INFO"
    Start-Sleep -Seconds 3
    
    # 测试 1: 健康检查
    Write-Log "测试 1: 健康检查" "INFO"
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:5678/api/health" -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) {
            Write-Log "  健康检查: PASS" "SUCCESS"
        } else {
            Write-Log "  健康检查: FAIL (Status: $($r.StatusCode))" "ERROR"
        }
    } catch {
        Write-Log "  健康检查: FAIL ($($_.Exception.Message))" "ERROR"
    }
    
    # 测试 2: 沙盒功能
    Write-Log "测试 2: 沙盒功能" "INFO"
    try {
        $body = @{code = "sum(range(10))"} | ConvertTo-Json
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:5678/api/sandbox/run" -Method Post -Body $body -ContentType "application/json" -UseBasicParsing -TimeoutSec 10
        $result = $r.Content | ConvertFrom-Json
        if ($result.error -eq $null) {
            Write-Log "  沙盒执行: PASS (Status: $($r.StatusCode))" "SUCCESS"
        } elseif ($result.blocked) {
            Write-Log "  沙盒状态: 关闭 (沙盒被阻止)" "WARNING"
            Write-Log "    提示: 设置 Sandbox=enable 并重启" "INFO"
        } else {
            Write-Log "  沙盒执行: FAIL" "ERROR"
        }
    } catch {
        Write-Log "  沙盒执行: FAIL ($($_.Exception.Message))" "ERROR"
    }
    
    # 测试 3: Web API 文件写入
    Write-Log "测试 3: Web API 文件写入" "INFO"
    $testContent = "云枢部署工具测试 - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    $body = @{path = "deploy_test.txt"; content = $testContent} | ConvertTo-Json
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:5678/api/workspace/write" -Method Post -Body $body -ContentType "application/json" -UseBasicParsing -TimeoutSec 5
        $result = $r.Content | ConvertFrom-Json
        if ($result.ok) {
            Write-Log "  API 写入: PASS (文件大小: $($result.size) 字节)" "SUCCESS"
            
            # 验证文件
            $filePath = Join-Path $ScriptDir "workspace\deploy_test.txt"
            if (Test-Path $filePath) {
                Write-Log "  文件验证: PASS" "SUCCESS"
                Remove-Item $filePath -Force -ErrorAction SilentlyContinue
                Write-Log "  清理完成" "INFO"
            } else {
                Write-Log "  文件验证: FAIL (文件不存在)" "ERROR"
            }
        } else {
            Write-Log "  API 写入: FAIL ($($result.error))" "ERROR"
        }
    } catch {
        Write-Log "  API 写入: FAIL ($($_.Exception.Message))" "ERROR"
    }
    
    # 测试 4: 批量写入
    Write-Log "测试 4: 批量写入 ($BatchCount 个文件)" "INFO"
    $pythonScript = @"
import requests
success = 0
failed = 0
for i in range(1, $BatchCount + 1):
    try:
        r = requests.post('http://127.0.0.1:5678/api/workspace/write',
                         json={'path': f'batch_test_{i}.txt', 'content': f'Test {i}'},
                         timeout=5)
        if r.status_code == 200:
            success += 1
        else:
            failed += 1
    except:
        failed += 1
print(f'{success}/{failed}')
"@
    try {
        $output = python -c $pythonScript 2>&1
        if ($output -match '(\d+)/(\d+)') {
            $s = $matches[1]
            $f = $matches[2]
            Write-Log "  批量写入: $s 成功, $f 失败" "SUCCESS"
        }
    } catch {
        Write-Log "  批量写入: FAIL" "ERROR"
    }
}

# ============================================================
# 步骤 5: 清理
# ============================================================
if ($Mode -eq "clean") {
    Write-Step "清理测试文件"
    
    $workspaceDir = Join-Path $ScriptDir "workspace"
    
    # 清理批量测试文件
    $batchDir = Join-Path $workspaceDir "云枢记忆\batch_test"
    if (Test-Path $batchDir) {
        $files = Get-ChildItem $batchDir -Filter "batch_test_*.txt" -ErrorAction SilentlyContinue
        $count = $files.Count
        $files | Remove-Item -Force -ErrorAction SilentlyContinue
        Write-Log "已清理 batch_test 文件: $count 个" "SUCCESS"
    }
    
    # 清理 deploy_test.txt
    $deployTest = Join-Path $workspaceDir "deploy_test.txt"
    if (Test-Path $deployTest) {
        Remove-Item $deployTest -Force -ErrorAction SilentlyContinue
        Write-Log "已清理 deploy_test.txt" "SUCCESS"
    }
}

# ============================================================
# 完成
# ============================================================
$Global:DeployStats.EndTime = Get-Date
$elapsed = $Global:DeployStats.EndTime - $Global:DeployStats.StartTime

Write-Host ""
Write-Log "========================================" "STEP"
Write-Log "  部署工具执行完成" "SUCCESS"
Write-Log "========================================" "STEP"
Write-Host ""

# 执行统计
Write-Host "执行统计:" -ForegroundColor Cyan
Write-Host "  总耗时: $($elapsed.TotalSeconds.ToString('F2')) 秒" -ForegroundColor White
Write-Host "  完成步骤: $($Global:DeployStats.StepsCompleted)" -ForegroundColor White
Write-Host "  错误数: $($Global:DeployStats.Errors.Count)" -ForegroundColor $(if ($Global:DeployStats.Errors.Count -gt 0) { "Red" } else { "Green" })
Write-Host "  警告数: $($Global:DeployStats.Warnings.Count)" -ForegroundColor $(if ($Global:DeployStats.Warnings.Count -gt 0) { "Yellow" } else { "Green" })
Write-Host "  日志文件: $Global:LogFile" -ForegroundColor Gray

# 错误详情
if ($Global:DeployStats.Errors.Count -gt 0) {
    Write-Host ""
    Write-Host "错误详情:" -ForegroundColor Red
    foreach ($err in $Global:DeployStats.Errors) {
        Write-Host "  - $err" -ForegroundColor Red
    }
}

# 警告详情
if ($Global:DeployStats.Warnings.Count -gt 0 -and $VerboseLog) {
    Write-Host ""
    Write-Host "警告详情:" -ForegroundColor Yellow
    foreach ($warn in $Global:DeployStats.Warnings) {
        Write-Host "  - $warn" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "常用命令:" -ForegroundColor Cyan
Write-Host "  $($PSCommandPath) full        # 完整部署" -ForegroundColor White
Write-Host "  $($PSCommandPath) start      # 启动服务" -ForegroundColor White
Write-Host "  $($PSCommandPath) start -Sandbox enable  # 启用沙盒启动" -ForegroundColor White
Write-Host "  $($PSCommandPath) test       # 运行测试" -ForegroundColor White
Write-Host "  $($PSCommandPath) clean      # 清理测试文件" -ForegroundColor White
Write-Host "  $($PSCommandPath) full -VerboseLog  # 详细日志模式" -ForegroundColor White
Write-Host ""

# 返回退出码
if ($Global:DeployStats.Errors.Count -gt 0) {
    exit 1
} else {
    exit 0
}
