<#
.SYNOPSIS
    fix_broken_links.ps1 单元测试 — 覆盖三个 Bug 场景的回归守卫

.DESCRIPTION
    覆盖 2026-08-01 修复的三个 Bug，确保回归不再复发:
      Bug 1: 正则 [^\]]+/[^)]+ 匹配换行符，全角括号导致跨行贪婪匹配
      Bug 2: Get-Content 未指定 UTF8 编码，PS 5.x 默认 GBK 致中文路径乱码
      Bug 3: 单引号字符串中 `n 不转义，原样输出字面量

    每个 Bug 包含两层覆盖:
      - 行为验证: 构造触发场景，断言修复后的正确行为
      - 源码契约: 验证脚本源码包含修复关键字符（防止被改回 buggy 版本）

.NOTES
    运行方式:
      pwsh -Command "Invoke-Pester tests/unit/fix_broken_links.Tests.ps1 -Output Detailed"
    依赖: Pester 5.0+ (本地 6.0.1 验证通过)
    注: 与 AdminDependencyChecker.Tests.ps1 (Pester 4.10.1) 独立运行，互不影响
#>

BeforeAll {
    $script:projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $script:scriptPath = Join-Path $script:projectRoot 'scripts\dev\fix_broken_links.ps1'
    # 【不易】源码契约读取：用 -Encoding UTF8 避免中文注释乱码
    $script:scriptSource = Get-Content $script:scriptPath -Raw -Encoding UTF8

    # 保存原工作目录（dot-source 时脚本会 Set-Location 到 $ProjectRoot）
    $script:origLocation = Get-Location

    # 【不易】Mock Get-ChildItem 阻断主流程扫描 docs/ 的副作用
    # dot-source 会执行主流程，$File 默认 $null 触发 else 分支调用 Get-ChildItem
    Mock Get-ChildItem { }

    # dot-source 加载 Test-LinkBroken / Get-FixAction / Fix-File 函数定义
    . $script:scriptPath
}

AfterAll {
    Set-Location $script:origLocation
}

# ════════════════════════════════════════════════════════════════
# Bug 1: 正则跨行贪婪匹配
# 根因: [^\]]+ / [^)]+ 会匹配换行符，遇全角括号(如】)时一路匹配到
#       下一行半角 ]，把表格/标题/多链接吞成一个"链接"
# ════════════════════════════════════════════════════════════════
Describe "Bug 1: 正则跨行贪婪匹配修复" {
    Context "行为验证: 修复后正则不跨行匹配" {
        It "含全角括号的多行内容不产生跨行匹配" {
            # 模拟 docs/README.md 真实场景: 全角 】 后跟多行表格
            $content = @'
| [架构合规性审计】(superpowers/specs/report.md) | 审核 |

### 用户指南

| 文档 | 描述 |
|------|------|
| [使用指南](zh/使用指南.md)
'@
            # 修复后的正则（排除 \r\n 限制单行）
            $newPattern = '\[([^\]\r\n]+)\]\(([^)\r\n]+)\)'
            $matches = [regex]::Matches($content, $newPattern)

            # 只应匹配单行的 [使用指南](zh/使用指南.md)，不跨行吞内容
            $matches.Count | Should -Be 1
            $matches[0].Value | Should -Be '[使用指南](zh/使用指南.md)'
            $matches[0].Value | Should -Not -Match "[\r\n]"
        }

        It "旧正则(有 bug)会跨行匹配 — 证明 bug 确实存在" {
            $oldPattern = '\[([^\]]+)\]\(([^)]+)\)'
            $content = @'
| [架构合规性审计】(superpowers/specs/report.md) | 审核 |

### 用户指南

| [使用指南](zh/使用指南.md)
'@
            $oldMatches = [regex]::Matches($content, $oldPattern)

            # 旧正则匹配结果跨多行（验证 bug 根因）
            $oldMatches.Count | Should -Be 1
            $oldMatches[0].Value | Should -Match "[\r\n]"
            $oldMatches[0].Length | Should -BeGreaterThan 50
        }

        It "正常单行链接仍被正确匹配" {
            $content = '| [架构概述](architecture.md) | 系统架构总览 |'
            $newPattern = '\[([^\]\r\n]+)\]\(([^)\r\n]+)\)'
            $matches = [regex]::Matches($content, $newPattern)

            $matches.Count | Should -Be 1
            $matches[0].Groups[1].Value | Should -Be '架构概述'
            $matches[0].Groups[2].Value | Should -Be 'architecture.md'
        }
    }

    Context "源码契约: 脚本使用修复后的正则" {
        It "正则排除换行符 \r\n" {
            # 防止被改回 [^\]]+/[^)]+ 的跨行 bug 版本
            $script:scriptSource.Contains('[^\]\r\n]') | Should -BeTrue
            $script:scriptSource.Contains('[^)\r\n]') | Should -BeTrue
        }
    }
}

# ════════════════════════════════════════════════════════════════
# Bug 2: Get-Content 未指定 UTF8 编码
# 根因: PS 5.x 默认 GBK 读取无 BOM 的 UTF-8 文件，中文链接路径乱码
#       → Test-Path 检查乱码路径失败 → 误判有效链接为失效
# ════════════════════════════════════════════════════════════════
Describe "Bug 2: Get-Content UTF8 编码修复" {
    BeforeEach {
        # 跨平台临时目录 (Linux pwsh 无 $env:TEMP)
        $script:tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("FblTest_$(Get-Random)")
        $null = New-Item -ItemType Directory -Path $script:tempDir -Force
        $null = New-Item -ItemType Directory -Path (Join-Path $script:tempDir 'docs\zh') -Force
    }

    AfterEach {
        Remove-Item $script:tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Context "行为验证: Test-LinkBroken 正确识别中文链接" {
        It "无 BOM UTF-8 文件的中文链接被识别为有效(不失效)" {
            # 创建中文文件名目标文件 (无 BOM UTF-8)
            $targetFile = Join-Path $script:tempDir 'docs\zh\使用指南.md'
            $utf8NoBom = New-Object System.Text.UTF8Encoding $false
            [System.IO.File]::WriteAllText($targetFile, '# 使用指南', $utf8NoBom)

            # 创建含中文链接的 Markdown (无 BOM UTF-8)
            $mdFile = Join-Path $script:tempDir 'docs\README.md'
            [System.IO.File]::WriteAllText($mdFile, '[使用指南](zh/使用指南.md)', $utf8NoBom)

            # 切换到临时 docs/ 目录，使 Test-LinkBroken 的相对路径生效
            # 【不易】MdFile 用 docs/ 前缀: Split-Path 才能取到 Parent='docs',
            # 否则 'README.md' 的 Parent 为空字符串，Join-Path 拒绝空 Path 报错
            Push-Location $script:tempDir
            try {
                # 【不易】核心断言: 链接有效 → Test-LinkBroken 返回 $false
                # 修复前因编码乱码返回 $true (误判失效)
                $result = Test-LinkBroken -MdFile 'docs/README.md' -LinkPath 'zh/使用指南.md'
                $result | Should -BeFalse
            }
            finally {
                Pop-Location
            }
        }

        It "不存在的中文链接被正确识别为失效" {
            $mdFile = Join-Path $script:tempDir 'docs\README.md'
            $utf8NoBom = New-Object System.Text.UTF8Encoding $false
            [System.IO.File]::WriteAllText($mdFile, '[缺失](zh/不存在.md)', $utf8NoBom)

            Push-Location $script:tempDir
            try {
                $result = Test-LinkBroken -MdFile 'docs/README.md' -LinkPath 'zh/不存在.md'
                $result | Should -BeTrue
            }
            finally {
                Pop-Location
            }
        }
    }

    Context "行为验证: Get-Content -Encoding UTF8 正确读取中文" {
        It "无 BOM UTF-8 文件用 -Encoding UTF8 读取中文不乱码" {
            $targetFile = Join-Path $script:tempDir '使用指南.md'
            $utf8NoBom = New-Object System.Text.UTF8Encoding $false
            [System.IO.File]::WriteAllText($targetFile, '# 使用指南', $utf8NoBom)

            $content = Get-Content $targetFile -Raw -Encoding UTF8
            $content | Should -Match '使用指南'
        }
    }

    Context "源码契约: 脚本包含 -Encoding UTF8" {
        It "Get-Content 调用包含 -Encoding UTF8 参数" {
            # 防止被改回无编码的 buggy 版本
            $script:scriptSource.Contains('Get-Content $filePath -Raw -Encoding UTF8') | Should -BeTrue
        }
    }
}

# ════════════════════════════════════════════════════════════════
# Bug 3: 单引号字符串中 `n 不转义
# 根因: PowerShell 单引号 '...' 中 `n 是字面量两字符，非换行符
#       只有双引号 "..." 中 `n 才被解释为换行
# ════════════════════════════════════════════════════════════════
Describe "Bug 3: Write-Host 单引号转义修复" {
    Context "行为验证: 双引号中 `n 被解释为换行" {
        It "双引号字符串中 `n 产生换行符" {
            $output = "test`n"
            # 含换行符 (长度 = 4 + 1 = 5)
            $output.Length | Should -Be 5
            $output | Should -Match "`n"
        }

        It "单引号字符串中 `n 是字面量(bug 根因)" {
            $output = 'test`n'
            # 字面量 6 字符 (test=4 + `=1 + n=1)，无换行
            $output | Should -Be 'test`n'
            $output.Length | Should -Be 6
            $output | Should -Not -Match "`n"
        }
    }

    Context "源码契约: 扫描提示行使用双引号" {
        It "Write-Host '[1/2] 扫描...' 使用双引号而非单引号" {
            # 防止被改回单引号 buggy 版本
            $script:scriptSource.Contains('Write-Host "[1/2] 扫描失效链接...`n"') | Should -BeTrue
            $script:scriptSource.Contains("Write-Host '[1/2] 扫描失效链接...`n'") | Should -BeFalse
        }
    }
}

# ════════════════════════════════════════════════════════════════
# 附加: Set-Content 改 .NET WriteAllText 无 BOM 写入
# 根因: PS 5.x 的 Set-Content -Encoding utf8 会写入 BOM，
#       改变原文件编码状态；WriteAllText 保持无 BOM
# ════════════════════════════════════════════════════════════════
Describe "附加: Set-Content 无 BOM 写入修复" {
    Context "源码契约: 使用 .NET WriteAllText 而非 Set-Content -Encoding utf8" {
        It "脚本包含 WriteAllText 调用" {
            $script:scriptSource.Contains('[System.IO.File]::WriteAllText') | Should -BeTrue
        }

        It "脚本不再使用 Set-Content -Encoding utf8" {
            # 防止退回到会写 BOM 的 Set-Content -Encoding utf8
            $script:scriptSource.Contains('Set-Content -Path $filePath -Value $content -NoNewline -Encoding utf8') | Should -BeFalse
        }

        It "使用 UTF8NoBom 编码实例" {
            $script:scriptSource.Contains('New-Object System.Text.UTF8Encoding $false') | Should -BeTrue
        }
    }
}
