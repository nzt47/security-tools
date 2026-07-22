<#
.SYNOPSIS
    AdminDependencyChecker 模块清单 (Module Manifest)

.DESCRIPTION
    声明 AdminDependencyChecker 模块的元数据, 让用户能通过:
        Import-Module .\scripts\AdminDependencyChecker.psd1
    或复制整个 scripts 目录到目标项目后:
        Import-Module AdminDependencyChecker
    来加载模块.

    与 .psm1 同目录放置, RootModule 用相对路径引用 .psm1.

.NOTES
    兼容性: PowerShell 5.1+ / PowerShell 7.x
    生成方式: 手工编写 (遵循 New-ModuleManifest 规范字段)
#>
@{

# ─── 模块身份 ──────────────────────────────────────────────────
RootModule        = 'AdminDependencyChecker.psm1'
ModuleVersion      = '1.0.0'
GUID              = '7e3a2c5f-9b4d-4e8a-b1c6-2d5e7f8a9b0c'
Author            = 'agent-team'
CompanyName       = 'agent-team'
Copyright         = '(c) agent-team. MIT license.'
Description       = '静态扫描 PowerShell 脚本中的 admin-only 依赖, 支持 11 种常见模式, 用于 CI/本地非管理员兼容性检测.'

# ─── 版本兼容性 ────────────────────────────────────────────────
# PowerShell 5.1 (Windows 内置) + PowerShell 7.x (跨平台)
PowerShellVersion = '5.1'
CompatiblePSEditions = @('Desktop', 'Core')

# ─── 导出 API (最小暴露面, 不变量) ────────────────────────────
# 函数: Test-AdminDependency (核心扫描) / Publish-AdminDependencyResult (输出)
# 变量: $DefaultAdminPatterns (11 种默认模式, 调用方可扩展)
FunctionsToExport = @('Test-AdminDependency', 'Publish-AdminDependencyResult')
VariablesToExport = @('DefaultAdminPatterns')
CmdletsToExport   = @()
AliasesToExport   = @()

# ─── 元数据 (PSGallery 风格, 必须放在 PrivateData.PSData) ────
# 顶层不允许 Tags/ProjectUri/LicenseUri/ReleaseNotes (PS 5.1+ 严格规范)

# ─── 依赖 ──────────────────────────────────────────────────────
# 无外部依赖, 纯 PowerShell 实现 (Get-Content / -match 正则)
RequiredModules = @()
RequiredAssemblies = @()
ScriptsToProcess = @()

# ─── 模块成员格式化规则 ────────────────────────────────────────
FormatsToProcess = @()
TypesToProcess   = @()
NestedModules    = @()
HelpInfoURI      = ''

# ─── 私有数据 (PSGallery 元数据 + 模块自描述信息) ─────────────
PrivateData = @{
    PSData = @{
        # 模块自带的单元测试路径 (相对模块根)
        Tests = 'tests/unit/AdminDependencyChecker.Tests.ps1'
        # 自测脚本路径
        SelfTest = 'scripts/simulate-ci-admin-check.ps1'
        # 默认模式数
        DefaultPatternsCount = 11

        # ─── PSGallery 元数据 (Tags/Uri/ReleaseNotes 必须在此处) ────
        Tags          = @('admin', 'security', 'ci', 'static-analysis', 'powershell', 'non-admin', 'compliance')
        ProjectUri    = ''
        LicenseUri    = ''
        ReleaseNotes  = 'v1.0.0: 初始版本, 11 种 admin-only 模式, 支持 CI/本地双模式输出, Pester 单元测试 41 例全过.'
    }
}

}
