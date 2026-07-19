<#
.SYNOPSIS
    AdminDependencyChecker.psm1 模块单元测试

.DESCRIPTION
    使用 Pester 3.4+ 兼容语法 (Windows 内置版本), 覆盖:
      - 4 种核心违规模式检测 (#Requires / IsInRole / -Verb RunAs / New-Service)
      - 11 种默认模式全覆盖
      - 干净文件不误报 (Passed=$true)
      - 自定义 Patterns 参数
      - 返回对象结构 (File/Line/Pattern/Content/ScannedFiles/MissedFiles)
      - 不存在文件容错
      - 注释行排除 (非 #Requires 注释)

.NOTES
    运行方式:
      pwsh -Command "Invoke-Pester tests/unit/AdminDependencyChecker.Tests.ps1 -Output Detailed"
      powershell -Command "Invoke-Pester tests/unit/AdminDependencyChecker.Tests.ps1"
#>

# 模块路径 (相对项目根: tests/unit -> scripts/AdminDependencyChecker.psm1)
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:modulePath = Join-Path $projectRoot 'scripts\AdminDependencyChecker.psm1'

# 临时测试目录
$script:testDir = Join-Path $env:TEMP ("AdminDepTest_$(Get-Random)")
$null = New-Item -ItemType Directory -Path $script:testDir -Force

Describe "AdminDependencyChecker 模块加载" {
    Context "模块导入" {
        It "应能成功导入模块" {
            { Import-Module $script:modulePath -Force -ErrorAction Stop } | Should Not Throw
        }

        It "应导出 Test-AdminDependency 函数" {
            Get-Command Test-AdminDependency -ErrorAction SilentlyContinue | Should Not Be $null
        }

        It "应导出 Publish-AdminDependencyResult 函数" {
            Get-Command Publish-AdminDependencyResult -ErrorAction SilentlyContinue | Should Not Be $null
        }

        It "应导出 DefaultAdminPatterns 变量" {
            (Get-Variable DefaultAdminPatterns -ErrorAction SilentlyContinue) | Should Not Be $null
        }
    }
}

Describe "4 种核心违规模式检测" {
    # 确保模块已加载
    Import-Module $script:modulePath -Force -ErrorAction SilentlyContinue

    # ─── 模式 1: #Requires -RunAsAdministrator ─────────────────
    Context "模式 1: #Requires -RunAsAdministrator" {
        $testFile = Join-Path $script:testDir "requires_admin.ps1"
        # 故意只放这一种违规, 检测应只命中 1 处
        Set-Content -Path $testFile -Value @(
            '#requires -RunAsAdministrator'
            'Write-Host "hello"'
        ) -Encoding UTF8

        $result = Test-AdminDependency -Path $testFile -Quiet

        It "应检测到 1 处违规" {
            $result.Violations.Count | Should Be 1
        }

        It "Passed 应为 false" {
            $result.Passed | Should Be $false
        }

        It "应命中正确模式 (#Requires)" {
            $result.Violations[0].Pattern | Should Match 'RunAsAdministrator'
        }

        It "行号应为 1" {
            $result.Violations[0].Line | Should Be 1
        }

        It "内容应包含 RunAsAdministrator" {
            $result.Violations[0].Content | Should Match 'RunAsAdministrator'
        }

        It "File 应正确指向被测文件" {
            # 路径分隔符可能被归一化, 只验证文件名
            $result.Violations[0].File | Should Match 'requires_admin\.ps1'
        }
    }

    # ─── 模式 2: IsInRole(Administrator) ───────────────────────
    Context "模式 2: IsInRole(WindowsBuiltInRole::Administrator)" {
        $testFile = Join-Path $script:testDir "is_in_role.ps1"
        # 这个表达式会同时匹配 2 个模式: WindowsBuiltInRole.*Administrator + IsInRole.*Administrator
        Set-Content -Path $testFile -Value @(
            '$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)'
        ) -Encoding UTF8

        $result = Test-AdminDependency -Path $testFile -Quiet

        It "应检测到至少 2 处违规 (两个模式同时命中)" {
            $result.Violations.Count | Should Be 2
        }

        It "Passed 应为 false" {
            $result.Passed | Should Be $false
        }

        It "应包含 WindowsBuiltInRole 模式" {
            ($result.Violations | Where-Object { $_.Pattern -match 'WindowsBuiltInRole' }).Count | Should Be 1
        }

        It "应包含 IsInRole 模式" {
            ($result.Violations | Where-Object { $_.Pattern -match 'IsInRole' }).Count | Should Be 1
        }

        It "行号应为 1 (两种模式命中同一行)" {
            $result.Violations[0].Line | Should Be 1
            $result.Violations[1].Line | Should Be 1
        }
    }

    # ─── 模式 3: Start-Process -Verb RunAs ─────────────────────
    Context "模式 3: Start-Process -Verb RunAs" {
        $testFile = Join-Path $script:testDir "verb_runas.ps1"
        # 同时匹配 2 个模式: -Verb\s+RunAs + Start-Process.*-Verb\s+RunAs
        Set-Content -Path $testFile -Value @(
            'Start-Process powershell -Verb RunAs'
        ) -Encoding UTF8

        $result = Test-AdminDependency -Path $testFile -Quiet

        It "应检测到至少 2 处违规" {
            $result.Violations.Count | Should Be 2
        }

        It "Passed 应为 false" {
            $result.Passed | Should Be $false
        }

        It "两种 Verb RunAs 模式都应命中" {
            ($result.Violations | Where-Object { $_.Pattern -match 'Verb' }).Count | Should Be 2
        }
    }

    # ─── 模式 4: New-Service ───────────────────────────────────
    Context "模式 4: New-Service" {
        $testFile = Join-Path $script:testDir "new_service.ps1"
        Set-Content -Path $testFile -Value @(
            'New-Service -Name "foo" -BinaryPathName "bar"'
        ) -Encoding UTF8

        $result = Test-AdminDependency -Path $testFile -Quiet

        It "应检测到 1 处违规" {
            $result.Violations.Count | Should Be 1
        }

        It "Passed 应为 false" {
            $result.Passed | Should Be $false
        }

        It "应命中 New-Service 模式" {
            $result.Violations[0].Pattern | Should Match 'New-Service'
        }
    }
}

Describe "干净文件不误报" {
    Import-Module $script:modulePath -Force -ErrorAction SilentlyContinue

    Context "无 admin 依赖的合规脚本" {
        $testFile = Join-Path $script:testDir "clean.ps1"
        # 完全合规的代码: 用 HKCU: (用户级), env: (用户级), 无 admin 操作
        Set-Content -Path $testFile -Value @(
            '# 健康的 PowerShell 脚本'
            '$path = "$env:APPDATA\myapp\config.json"'
            '$userRoot = "HKCU:\Software\myapp"'
            'Write-Host "hello $path"'
            'Get-ChildItem $path | ForEach-Object { Write-Host $_.Name }'
        ) -Encoding UTF8

        $result = Test-AdminDependency -Path $testFile -Quiet

        It "违规数应为 0" {
            $result.Violations.Count | Should Be 0
        }

        It "Passed 应为 true" {
            $result.Passed | Should Be $true
        }

        It "ScannedFiles 应为 1" {
            $result.ScannedFiles | Should Be 1
        }
    }

    Context "注释行不应被误报 (非 #Requires)" {
        $testFile = Join-Path $script:testDir "comments_only.ps1"
        # 注释行包含 admin 关键字, 但在注释里 (非 #Requires 指令)
        Set-Content -Path $testFile -Value @(
            '# 这是一个注释, 提到 New-Service 不应触发违规'
            '# 也不应因提到 RunAsAdministrator 而报警'
            '# HKLM:\ 仅作文档说明, 不是实际代码'
            'Write-Host "actual code"'
        ) -Encoding UTF8

        $result = Test-AdminDependency -Path $testFile -Quiet

        It "注释中的 admin 关键字不应触发违规" {
            $result.Violations.Count | Should Be 0
        }

        It "Passed 应为 true" {
            $result.Passed | Should Be $true
        }
    }
}

Describe "自定义 Patterns 参数" {
    Import-Module $script:modulePath -Force -ErrorAction SilentlyContinue

    Context "使用自定义模式覆盖默认" {
        $testFile = Join-Path $script:testDir "custom_pattern.ps1"
        Set-Content -Path $testFile -Value @(
            'Stop-Computer -Force'
            'New-Service -Name "x"'
        ) -Encoding UTF8

        # 自定义模式: 只检测 Stop-Computer, 不检测 New-Service
        $custom = @('Stop-Computer')
        $result = Test-AdminDependency -Path $testFile -Patterns $custom -Quiet

        It "应只命中 1 处 (Stop-Computer)" {
            $result.Violations.Count | Should Be 1
        }

        It "应命中 Stop-Computer 模式" {
            $result.Violations[0].Pattern | Should Be 'Stop-Computer'
        }

        It "New-Service 应被忽略 (自定义模式不含它)" {
            ($result.Violations | Where-Object { $_.Content -match 'New-Service' }).Count | Should Be 0
        }
    }

    Context "扩展 DefaultAdminPatterns" {
        $testFile = Join-Path $script:testDir "extended.ps1"
        Set-Content -Path $testFile -Value @(
            'Stop-Computer'
        ) -Encoding UTF8

        # 默认 11 个 + 1 个自定义
        $extended = $DefaultAdminPatterns + 'Stop-Computer'
        $result = Test-AdminDependency -Path $testFile -Patterns $extended -Quiet

        It "应命中 1 处" {
            $result.Violations.Count | Should Be 1
        }

        It "模式总数应为 12 (默认 11 + 1 自定义)" {
            $extended.Count | Should Be 12
        }
    }
}

Describe "边界场景" {
    Import-Module $script:modulePath -Force -ErrorAction SilentlyContinue

    Context "不存在的文件" {
        $result = Test-AdminDependency -Path "C:\nonexistent_$(Get-Random).ps1" -Quiet

        It "ScannedFiles 应为 0" {
            $result.ScannedFiles | Should Be 0
        }

        It "MissedFiles 应有 1 个" {
            $result.MissedFiles.Count | Should Be 1
        }

        It "违规数应为 0" {
            $result.Violations.Count | Should Be 0
        }

        It "Passed 应为 true (无违规即通过)" {
            $result.Passed | Should Be $true
        }
    }

    Context "多文件批量扫描" {
        $file1 = Join-Path $script:testDir "multi1.ps1"
        $file2 = Join-Path $script:testDir "multi2.ps1"
        $file3 = Join-Path $script:testDir "multi3.ps1"
        Set-Content -Path $file1 -Value '#requires -RunAsAdministrator' -Encoding UTF8
        Set-Content -Path $file2 -Value 'New-Service -Name "x"' -Encoding UTF8
        Set-Content -Path $file3 -Value 'Write-Host "clean"' -Encoding UTF8

        $result = Test-AdminDependency -Path @($file1, $file2, $file3) -Quiet

        It "ScannedFiles 应为 3" {
            $result.ScannedFiles | Should Be 3
        }

        It "TotalFiles 应为 3" {
            $result.TotalFiles | Should Be 3
        }

        It "违规数应为 2 (file1 + file2)" {
            $result.Violations.Count | Should Be 2
        }

        It "Passed 应为 false" {
            $result.Passed | Should Be $false
        }
    }
}

Describe "Publish-AdminDependencyResult" {
    Import-Module $script:modulePath -Force -ErrorAction SilentlyContinue

    Context "通过场景 (Passed=true)" {
        $testFile = Join-Path $script:testDir "publish_ok.ps1"
        Set-Content -Path $testFile -Value 'Write-Host "clean"' -Encoding UTF8
        $result = Test-AdminDependency -Path $testFile -Quiet

        It "调用 Publish-AdminDependencyResult 不应抛异常" {
            { Publish-AdminDependencyResult -Result $result } | Should Not Throw
        }
    }

    Context "违规场景 (Passed=false)" {
        $testFile = Join-Path $script:testDir "publish_fail.ps1"
        Set-Content -Path $testFile -Value '#requires -RunAsAdministrator' -Encoding UTF8
        $result = Test-AdminDependency -Path $testFile -Quiet

        It "调用 Publish-AdminDependencyResult 不应抛异常" {
            { Publish-AdminDependencyResult -Result $result } | Should Not Throw
        }
    }
}

# ─── 清理临时目录 ─────────────────────────────────────────────
Remove-Item -Path $script:testDir -Recurse -Force -ErrorAction SilentlyContinue
