<#
.SYNOPSIS
    AdminDependencyChecker 模块打包说明

.DESCRIPTION
    模块结构 (位于 scripts/ 目录):
        scripts/AdminDependencyChecker.psd1   - 模块清单 (manifest)
        scripts/AdminDependencyChecker.psm1   - 模块实现
        scripts/simulate-ci-admin-check.ps1   - 自测脚本 (含 -SelfTest 回归模式)
        tests/unit/AdminDependencyChecker.Tests.ps1 - Pester 单元测试 (41 例)

    三种使用方式:
        方式 1: 原地加载 (推荐用于本项目)
        方式 2: 复制到目标项目 (推荐用于跨项目复用)
        方式 3: 加入 $PSModulePath 全局可用

# ─── 方式 1: 原地加载 ──────────────────────────────────────────
    # 在本项目内
    Import-Module .\scripts\AdminDependencyChecker.psd1

    $r = Test-AdminDependency -Path .\scripts\rollback-protection.ps1
    if (-not $r.Passed) {
        Publish-AdminDependencyResult -Result $r
        exit 1
    }

# ─── 方式 2: 复制到目标项目 ────────────────────────────────────
    # 一键复制 (推荐): 在目标项目根目录运行
    $src = 'c:\path\to\this\agent\scripts'
    .\scripts\Copy-AdminModule.ps1 -Destination .\Modules

    # 之后在目标项目内:
    Import-Module .\Modules\AdminDependencyChecker\AdminDependencyChecker.psd1

    # 模块目录结构 (复制后):
    #   Modules/AdminDependencyChecker/
    #     ├── AdminDependencyChecker.psd1
    #     ├── AdminDependencyChecker.psm1
    #     └── README.md (本文件副本)
    # 文件名 = 目录名 = 模块名 (PowerShell 模块解析规则)

# ─── 方式 3: 全局可用 (放入 $PSModulePath) ─────────────────────
    # 把方式 2 复制好的 Modules/AdminDependencyChecker 目录
    # 移动到 $PSModulePath 中任意一个路径下, 例如:
    #   C:\Users\<user>\Documents\PowerShell\Modules\AdminDependencyChecker\
    # 之后任何 PowerShell 会话都能:
    Import-Module AdminDependencyChecker   # 无需路径

# ─── 自测 / 单元测试 ──────────────────────────────────────────
    # 模块功能回归测试 (Pester 41 例)
    pwsh -Command "Invoke-Pester tests/unit/AdminDependencyChecker.Tests.ps1 -Output Detailed"

    # CI 自测 (注入 4 种违规验证检测能力)
    .\scripts\simulate-ci-admin-check.ps1 -SelfTest

# ─── 公共 API ──────────────────────────────────────────────────
    Test-AdminDependency
        -Path <string[]>              # 目标文件路径数组
        -Patterns <string[]>          # 可选: 自定义模式 (默认 11 种)
        -Quiet                        # 不输出进度信息
        返回: @{ ScannedFiles; TotalFiles; Violations; MissedFiles; Passed }

    Publish-AdminDependencyResult
        -Result <object>              # Test-AdminDependency 返回值
        自动识别 CI 环境 (GITHUB_ACTIONS=true) 输出 ::error:: workflow command

    $DefaultAdminPatterns             # 11 种默认模式数组, 可扩展:
        $DefaultAdminPatterns += 'Stop-Computer'

# ─── 11 种 admin-only 模式 (v1.0.0) ────────────────────────────
    1. #Requires -RunAsAdministrator
    2. WindowsBuiltInRole...Administrator
    3. IsInRole...Administrator
    4. -Verb RunAs
    5. HKLM:\ 或 HKEY_LOCAL_MACHINE\
    6. net localgroup administrators
    7. New-Service
    8. Set-Service
    9. icacls
    10. takeown
    11. Start-Process ... -Verb RunAs

# ─── 版本 ──────────────────────────────────────────────────────
    v1.0.0  初始版本
            - 41 例 Pester 单元测试全过
            - 4 种核心违规模式 + 7 种扩展模式
            - CI/本地双输出模式
            - -SelfTest 回归测试通道
#>
