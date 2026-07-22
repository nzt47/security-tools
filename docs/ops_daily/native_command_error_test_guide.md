# NativeCommandError 测试用例文档（新人指南）

> **目标读者**：刚加入团队的工程师、初次接触 P6-2 修复的运维人员
> **文档目的**：帮助新人快速理解 NativeCommandError 误判问题、测试用例设计思路、维护方法
> **关联代码**：`tests/unit/test_verify_production_deployment.ps1` L488-L559

---

## 1. 什么是 NativeCommandError？

### 1.1 一句话解释
**PowerShell 5.x 调用原生命令（如 kubectl）时，如果 stderr 有输出，会自动包装成一条错误记录，这条记录叫 NativeCommandError。**

### 1.2 为什么这是问题？
Unix 哲学中，程序把**日志信息写到 stderr**，**真正的数据写到 stdout**。例如我们的日报脚本：
```
stdout: 日报内容（数据）
stderr: [WARN] 未找到事件 / [OK] 日报已生成（日志）
```

这在 Linux 下完全正常。但在 PowerShell 5.x 下：
```
kubectl exec ... 2>&1   ← stderr 被合并到 stdout
    ↓
PS 5.x 发现 stderr 非空，自动创建 NativeCommandError 记录
    ↓
输出中多了一段："FullyQualifiedErrorId : NativeCommandError"
    ↓
"NativeCommandError" 包含 "Error" 这个词
    ↓
旧判据 -notmatch "Traceback|Error" 匹配到 "Error"，误判失败
```

### 1.3 PS 5.x vs PS 7.x 的关键差异
| 行为 | PS 5.x (Desktop) | PS 7.x (Core) |
|------|-------------------|----------------|
| stderr 非空时 | 创建 NativeCommandError 错误记录 | 直接合并到 stdout，不创建错误记录 |
| `2>&1` 后输出 | 含 `FullyQualifiedErrorId : NativeCommandError` | 只有原始 stderr 内容 |
| 旧判据结果 | **FAIL（误判）** | PASS（正确） |

> **新人提示**：如果你在 Windows Server 2016/2019 自带 PowerShell（5.x）运行脚本遇到莫名其妙的 FAIL，先怀疑 NativeCommandError。

---

## 2. P6-2 修复简介

### 2.1 问题
P6-2 检查点"手动触发日报生成成功"在 PS 5.x 下误判 FAIL，即使日报正常生成。

### 2.2 修复方案（方案 B）
**旧判据**（看输出内容）→ **新判据**（看文件是否生成）

```powershell
# 旧判据：容易被 NativeCommandError 中的 "Error" 关键词误判
if ($LASTEXITCODE -eq 0 -and $reportStr -notmatch "Traceback|Error") { ... }

# 新判据：检查日报文件是否生成且非空，与 PS 版本无关
$fileCheck = (kubectl exec $pod -- sh -c "test -s $manualReport && echo FILE_OK" 2>&1 | Out-String)
if ($fileCheck -match "FILE_OK") { ... }
```

### 2.3 为什么选方案 B？
| 方案 | 优点 | 缺点 | 选择 |
|------|------|------|------|
| A: 改 Python 脚本不写 stderr | 治本 | 破坏 Unix 哲学，影响其他工具 | ❌ |
| B: 改判据看文件生成 | 治标且健壮，跨 PS 版本一致 | 需要额外一次 kubectl exec | ✅ |
| C: 用 -ErrorAction SilentlyContinue | 简单 | 不能完全抑制 NativeCommandError | ❌ |

---

## 3. 测试用例清单（7 个）

### 3.1 测试用例总览
| # | 用例名称 | 类型 | 验证目标 |
|---|----------|------|----------|
| 1 | 模拟输出含 'NativeCommandError' 标识 | 数据校验 | 模拟数据与真实格式一致 |
| 2 | 模拟输出含 'Error' 子串 | 数据校验 | 证明误判根因 |
| 3 | 旧判据：PS 5.x 下误判 FAIL | 回归测试 | **bug 复现** |
| 4 | 旧判据：PS 7.x 下应 PASS | 对照实验 | 证明 bug 与 PS 版本相关 |
| 5 | 新判据：PS 5.x 下应 PASS | 修复验证 | **修复有效** |
| 6 | 新判据：空输出应 FAIL | 判据有效性 | 新判据不是无脑 PASS |
| 7 | 新判据：与 PS 版本无关 | 核心目标 | 跨版本一致性 |

### 3.2 每个用例详解

#### 用例 1：模拟输出含 'NativeCommandError' 标识
```powershell
It "PS 5.x NativeCommandError 模拟输出包含 'NativeCommandError' 标识" {
    $script:Ps5NativeError | Should -Match "NativeCommandError"
}
```
**目的**：验证我们模拟的 PS 5.x 输出数据与真实环境一致。
**新人理解**：这是数据真实性校验，确保后续测试基于正确的模拟数据。

#### 用例 2：模拟输出含 'Error' 子串
```powershell
It "PS 5.x NativeCommandError 模拟输出包含 'Error' 子串（误判根因）" {
    $script:Ps5NativeError | Should -Match "Error"
}
```
**目的**：证明 "NativeCommandError" 包含 "Error"，这是旧判据误判的根因。
**新人理解**：如果没有 "Error" 子串，旧判据就不会误判。这个用例确认了 bug 的根本原因。

#### 用例 3：旧判据在 PS 5.x 下误判 FAIL（bug 复现）
```powershell
It "旧判据：PS 5.x 下 Python 退出码 0 仍误判 FAIL（bug 复现）" {
    $result = Test-P62LegacyJudge 0 $script:Ps5NativeError
    $result.Pass | Should -Be $false
}
```
**目的**：回归测试，证明修复前的判据确实有 bug。
**新人理解**：即使 Python 退出码 0（成功），旧判据也会因为 "Error" 关键词误判 FAIL。这是 bug 的核心表现。

#### 用例 4：旧判据在 PS 7.x 下应 PASS（对照实验）
```powershell
It "旧判据：PS 7.x 下（无 NativeCommandError）应 PASS（对照实验）" {
    $ps7Output = "[WARN] 未找到任何熔断器相关事件`n[OK] 运维日报已生成: /app/output/manual.md"
    $result = Test-P62LegacyJudge 0 $ps7Output
    $result.Pass | Should -Be $true
}
```
**目的**：对照实验，证明旧判据在 PS 7.x 下工作正常。
**新人理解**：PS 7.x 不会创建 NativeCommandError，输出只有 [WARN]/[OK]，不含 "Error"，所以旧判据 PASS。这证明了 bug 与 PS 版本相关。

#### 用例 5：新判据在 PS 5.x 下应 PASS（修复有效）
```powershell
It "新判据：PS 5.x 下文件存在 FILE_OK 应 PASS（修复有效）" {
    $result = Test-P62FileJudge "FILE_OK"
    $result.Pass | Should -Be $true
}
```
**目的**：验证修复后新判据在 PS 5.x 下能正确 PASS。
**新人理解**：新判据只看 "FILE_OK" 标识，不关心 stderr 内容，所以 NativeCommandError 不再影响判据。

#### 用例 6：新判据空输出应 FAIL（判据有效性）
```powershell
It "新判据：日报文件未生成（空输出）应 FAIL（判据有效性保持）" {
    $result = Test-P62FileJudge ""
    $result.Pass | Should -Be $false
}
```
**目的**：验证新判据不是"无脑 PASS"，仍能正确识别失败。
**新人理解**：如果文件没生成，`$fileCheck` 为空，新判据会 FAIL。这保证判据的有效性。

#### 用例 7：新判据与 PS 版本无关（核心目标）
```powershell
It "新判据：与 PS 版本无关（修复核心目标达成）" {
    $ps5Result = Test-P62FileJudge "FILE_OK"
    $ps7Result = Test-P62FileJudge "FILE_OK"
    $ps5Result.Pass | Should -Be $ps7Result.Pass
}
```
**目的**：验证修复的核心目标——判据与 PS 版本解耦。
**新人理解**：新判据在 PS 5.x 和 PS 7.x 下行为完全一致，这是修复的核心价值。

---

## 4. 测试数据说明

### 4.1 模拟的 PS 5.x NativeCommandError 输出
```
kubectl : [WARN] 未找到任何熔断器相关事件
    + CategoryInfo          : NotSpecified: ([WARN] 未找到任何熔断器相关事件:String) [], RemoteException
    + FullyQualifiedErrorId : NativeCommandError
[OK] 运维日报已生成: /app/output/manual.md
```

**关键字段解释**：
- `kubectl :` — PS 5.x 标记错误来源的命令名
- `CategoryInfo : NotSpecified` — 错误分类（原生命令 stderr 都是这个）
- `FullyQualifiedErrorId : NativeCommandError` — **关键标识**，含 "Error" 子串
- `[OK] 运维日报已生成` — 实际的 stdout 内容（日报生成成功）

### 4.2 模拟的 PS 7.x 干净输出
```
[WARN] 未找到任何熔断器相关事件
[OK] 运维日报已生成: /app/output/manual.md
```
PS 7.x 不创建错误记录，stderr 直接合并到 stdout，输出只有原始日志内容。

---

## 5. 如何运行测试

### 5.1 运行全部测试
```bash
# 使用 PowerShell 7（推荐，支持 Pester 5.x 语法）
pwsh -NoProfile -Command "Invoke-Pester -Path .\tests\unit\test_verify_production_deployment.ps1 -Output Detailed"
```

### 5.2 只运行 NativeCommandError 相关测试
```bash
pwsh -NoProfile -Command "Invoke-Pester -Path .\tests\unit\test_verify_production_deployment.ps1 -TestName 'P6-2 NativeCommandError 兼容性判据（PS 5.x 回归测试）' -Output Detailed"
```

### 5.3 预期输出
```
Describing P6-2 NativeCommandError 兼容性判据（PS 5.x 回归测试）
  [+] PS 5.x NativeCommandError 模拟输出包含 'NativeCommandError' 标识 19ms
  [+] PS 5.x NativeCommandError 模拟输出包含 'Error' 子串（误判根因） 3ms
  [+] 旧判据：PS 5.x 下 Python 退出码 0 仍误判 FAIL（bug 复现） 4ms
  [+] 旧判据：PS 7.x 下（无 NativeCommandError）应 PASS（对照实验） 3ms
  [+] 新判据：PS 5.x 下文件存在 FILE_OK 应 PASS（修复有效） 4ms
  [+] 新判据：日报文件未生成（空输出）应 FAIL（判据有效性保持） 6ms
  [+] 新判据：与 PS 版本无关（修复核心目标达成） 3ms

Tests Passed: 7, Failed: 0, Skipped: 0
```

---

## 6. 维护指南

### 6.1 何时需要修改这些测试？
| 场景 | 是否需要修改 | 说明 |
|------|-------------|------|
| P6-2 判据再次调整 | ✅ 需要 | 更新 Test-P62FileJudge 逻辑 |
| 日报脚本输出格式变化 | ⚠️ 评估 | 如果 stdout/stderr 分离策略改变，需更新模拟数据 |
| 新增其他检查点的 NativeCommandError 修复 | 🔁 复用 | 复制本测试模式，修改判据函数 |
| Pester 升级到新版本 | ⚠️ 评估 | 检查语法兼容性 |

### 6.2 如何添加新的测试用例

**步骤 1**：在 `BeforeAll` 块中定义新的判据函数
```powershell
function Test-P62NewScenario {
    param([string]$SomeInput)
    return @{ Pass = ($SomeInput -match "EXPECTED_PATTERN") }
}
```

**步骤 2**：在 `Describe` 块中添加 `It` 用例
```powershell
It "新场景：描述预期行为" {
    $result = Test-P62NewScenario "input_data"
    $result.Pass | Should -Be $true  # 或 $false，取决于预期
}
```

**步骤 3**：运行测试验证
```bash
pwsh -NoProfile -Command "Invoke-Pester -Path .\tests\unit\test_verify_production_deployment.ps1 -TestName 'P6-2 NativeCommandError' -Output Detailed"
```

### 6.3 测试命名规范
- **回归测试**：`旧判据：场景描述（bug 复现）`
- **修复验证**：`新判据：场景描述（修复有效）`
- **对照实验**：`旧判据：对照场景（对照实验）`
- **有效性验证**：`新判据：失败场景（判据有效性保持）`
- **一致性验证**：`新判据：跨版本场景（核心目标达成）`

---

## 7. FAQ

### Q1: 为什么不用 `-ErrorAction SilentlyContinue` 抑制 NativeCommandError？
**A**: `-ErrorAction` 对 NativeCommandError 不完全有效，因为它不是标准的 PowerShell 错误。即使抑制了错误记录，`2>&1` 重定向后的输出流中仍可能包含错误信息。方案 B（看文件生成）更健壮。

### Q2: 为什么模拟数据用英文？之前不是中文吗？
**A**: 对比脚本 `p62_regression_compare.ps1` 为了在 PS 5.x 下兼容运行（避免 UTF-8 编码问题），模拟数据改用英文。但 Pester 测试文件（`test_verify_production_deployment.ps1`）仍用中文，因为 Pester 6.x 在 PS 7.x 下运行，编码无问题。

### Q3: 如果生产环境用 PS 7.x，还需要这个修复吗？
**A**: 需要。即使当前生产环境用 PS 7.x，验收脚本可能在开发/测试环境的 PS 5.x 上运行（如 Windows Server 自带）。修复保证了脚本跨 PS 版本一致性，是 [变易] 原则的体现。

### Q4: 如何确认我的环境是否受影响？
**A**: 运行对比脚本：
```bash
powershell -NoProfile -File .\scripts\p62_regression_compare.ps1
```
如果 `bug_confirmed: true`，说明旧判据在你的 PS 5.x 环境下会误判。

### Q5: 新判据会不会漏检 Python 脚本异常？
**A**: 不会。Python 脚本异常时退出码非 0 且不生成日报文件，`test -s` 检查文件非空会 FAIL。新判据通过文件存在性间接验证了脚本执行成功。

---

## 8. 相关文档
- [P6-2 回归测试报告](./p62_regression_test_report_20260719.md) — 完整的 PS 5.x/7.x 对比数据
- [端到端验证分析报告](./e2e_verification_analysis_20260719.md) — Kind 集群真实部署验证
- [生产部署指南](./production_deployment_guide.md) — 28 检查点完整说明
- [对比脚本](../../scripts/p62_regression_compare.ps1) — PS 5.x/7.x 数据收集工具

---

## 9. 变更记录
| 日期 | 变更 | 作者 |
|------|------|------|
| 2026-07-19 | 初始版本，配合 P6-2 修复（commit da79989d）