<#
.SYNOPSIS
  kind (Kubernetes in Docker) 手动安装脚本

.DESCRIPTION
  多镜像源尝试下载 kind 二进制，支持 Windows/macOS/Linux。
  网络恢复后直接运行即可安装，无需手动查找镜像源。

  三义原则：
  - [不易] kind 官方 release 二进制，不修改不打包
  - [变易] 多镜像源 + 跨平台 + 版本参数化，应对网络不稳
  - [简易] 单脚本全流程：检测平台 → 多源下载 → 配置 PATH → 验证

.PARAMETER Version
  kind 版本号，默认 v0.23.0（查看全部版本: https://github.com/kubernetes-sigs/kind/releases）

.PARAMETER InstallDir
  安装目录，默认:
    Windows: C:\Users\Administrator\bin
    macOS/Linux: /usr/local/bin

.PARAMETER Sources
  自定义下载源（JSON 数组），默认内置 5 个镜像源按优先级尝试

.EXAMPLE
  .\install_kind.ps1
  .\install_kind.ps1 -Version v0.24.0
  .\install_kind.ps1 -InstallDir D:\tools
#>

param(
    [string]$Version = "v0.23.0",
    [string]$InstallDir = "",
    [string[]]$CustomSources = @()
)

$ErrorActionPreference = "Stop"

# ===== 平台检测 =====
function Get-PlatformInfo {
    $os = "windows"
    $arch = "amd64"

    if ($IsMacOS) {
        $os = "darwin"
    } elseif ($IsLinux) {
        $os = "linux"
    }

    # 架构检测（兼容 Windows PowerShell 5.1 与 PowerShell 7+）
    try {
        $procArch = [System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture.ToString()
        if ($procArch -match "ARM|Arm64") { $arch = "arm64" }
    } catch {
        if ($env:PROCESSOR_ARCHITECTURE -match "ARM") { $arch = "arm64" }
    }

    return @{ OS = $os; Arch = $arch }
}

# ===== 默认安装路径 =====
function Get-DefaultInstallDir {
    param([string]$os)
    if ($os -eq "windows") {
        return "C:\Users\Administrator\bin"
    } else {
        return "/usr/local/bin"
    }
}

# ===== 镜像源列表 =====
function Get-MirrorSources {
    param([string]$version, [string]$os, [string]$arch)

    $binary = "kind-$os-$arch"
    if ($os -eq "windows") { $binary += ".exe" }

    return @(
        # [不易] 官方源（优先级最高，网络好时首选）
        "https://kind.sigs.k8s.io/dl/$version/$binary"
        # GitHub releases 直链
        "https://github.com/kubernetes-sigs/kind/releases/download/$version/$binary"
        # [变易] 国内镜像源（网络差时兜底）
        "https://mirror.ghproxy.com/https://github.com/kubernetes-sigs/kind/releases/download/$version/$binary"
        "https://gh-proxy.com/https://github.com/kubernetes-sigs/kind/releases/download/$version/$binary"
        "https://ghproxy.net/https://github.com/kubernetes-sigs/kind/releases/download/$version/$binary"
    )
}

# ===== 主逻辑 =====
Write-Host "`n=== kind 安装脚本 ===" -ForegroundColor Cyan

# 1. 平台检测
$platform = Get-PlatformInfo
$os = $platform.OS
$arch = $platform.Arch
Write-Host "[INFO] 平台: $os / $arch" -ForegroundColor Gray

# 2. 安装路径
if (-not $InstallDir) {
    $InstallDir = Get-DefaultInstallDir -os $os
}
Write-Host "[INFO] 安装目录: $InstallDir" -ForegroundColor Gray

# 创建目录
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Write-Host "[INFO] 已创建目录: $InstallDir" -ForegroundColor Gray
}

# 3. 检查是否已安装
$kindPath = Join-Path $InstallDir "kind$(if ($os -eq 'windows') { '.exe' } else { '' })"
if (Test-Path $kindPath) {
    $existingVer = & $kindPath version 2>&1 | Select-Object -First 1
    Write-Host "[WARN] kind 已存在: $existingVer" -ForegroundColor Yellow
    $overwrite = Read-Host "  是否覆盖? (y/N)"
    if ($overwrite -ne "y") {
        Write-Host "[INFO] 跳过安装" -ForegroundColor Gray
        exit 0
    }
}

# 4. 镜像源
$sources = if ($CustomSources.Count -gt 0) { $CustomSources } else { Get-MirrorSources -version $Version -os $os -arch $arch }
Write-Host "[INFO] 将尝试 $($sources.Count) 个镜像源" -ForegroundColor Gray

# 5. 多源下载
$binaryName = "kind-$os-$arch$(if ($os -eq 'windows') { '.exe' } else { '' })"
$downloaded = $false

foreach ($i in 0..($sources.Count - 1)) {
    $url = $sources[$i]
    Write-Host "`n[尝试 $(( $i + 1 ))/$($sources.Count)] $url" -ForegroundColor Cyan

    try {
        # 下载（带超时）
        $tempFile = Join-Path $env:TEMP $binaryName
        Invoke-WebRequest -Uri $url -OutFile $tempFile -UseBasicParsing -TimeoutSec 120

        # 验证文件大小（kind 二进制 > 5MB）
        $fileSize = (Get-Item $tempFile).Length
        if ($fileSize -lt 1MB) {
            Write-Host "  [FAIL] 文件过小 ($fileSize bytes)，可能为错误页面" -ForegroundColor Red
            Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
            continue
        }

        # 移动到安装目录
        Move-Item -Path $tempFile -Destination $kindPath -Force

        # 设置可执行权限（Unix）
        if ($os -ne "windows") {
            chmod +x $kindPath 2>$null
        }

        Write-Host "  [PASS] 下载成功: $fileSize bytes" -ForegroundColor Green
        $downloaded = $true
        break

    } catch {
        $err = $_.Exception.Message
        Write-Host "  [FAIL] $err" -ForegroundColor Red
        continue
    }
}

if (-not $downloaded) {
    Write-Host "`n[ERROR] 所有镜像源均下载失败" -ForegroundColor Red
    Write-Host "`n手动下载方案:" -ForegroundColor Yellow
    Write-Host "  1. 用浏览器（可能需 VPN）访问:" -ForegroundColor Gray
    Write-Host "     https://github.com/kubernetes-sigs/kind/releases/download/$Version/$binaryName"
    Write-Host "  2. 下载后放到: $InstallDir"
    Write-Host "  3. 重命名/确保文件名: kind$(if ($os -eq 'windows') { '.exe' } else { '' })"
    Write-Host "  4. (Unix) chmod +x $kindPath"
    exit 1
}

# 6. 配置 PATH
$currentPath = $env:PATH -split ";|:"
$inPath = $currentPath -contains $InstallDir

if (-not $inPath) {
    Write-Host "`n[INFO] 配置 PATH..." -ForegroundColor Gray
    if ($os -eq "windows") {
        # Windows: 永久添加到用户 PATH
        $userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
        if ($userPath -notmatch [regex]::Escape($InstallDir)) {
            [Environment]::SetEnvironmentVariable("PATH", "$userPath;$InstallDir", "User")
            Write-Host "  [PASS] 已添加到用户 PATH（永久）" -ForegroundColor Green
        }
        # 当前会话也生效
        $env:PATH = "$InstallDir;$env:PATH"
    } else {
        Write-Host "  [WARN] 请手动将 $InstallDir 添加到 PATH" -ForegroundColor Yellow
        Write-Host "    echo 'export PATH=`$PATH:$InstallDir' >> ~/.bashrc" -ForegroundColor Gray
    }
} else {
    Write-Host "[INFO] $InstallDir 已在 PATH 中" -ForegroundColor Gray
}

# 7. 验证安装
Write-Host "`n=== 验证安装 ===" -ForegroundColor Cyan
try {
    $version = & $kindPath version 2>&1 | Select-Object -First 1
    Write-Host "[PASS] kind 安装成功!" -ForegroundColor Green
    Write-Host "  版本: $version" -ForegroundColor Gray
    Write-Host "  路径: $kindPath" -ForegroundColor Gray

    Write-Host "`n下一步:" -ForegroundColor Cyan
    Write-Host "  创建集群:  kind create cluster --name tlm-np-test"
    Write-Host "  运行测试:  .\scripts\test_networkpolicy_kind.ps1"
    Write-Host "  清理集群:  kind delete cluster --name tlm-np-test"

} catch {
    Write-Host "[FAIL] kind 验证失败: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  请检查文件是否完整或权限是否正确" -ForegroundColor Gray
    exit 1
}

Write-Host "`n=== 安装结束 ===" -ForegroundColor Cyan
