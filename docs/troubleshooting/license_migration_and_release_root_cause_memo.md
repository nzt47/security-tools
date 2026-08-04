# License 迁移与 GitHub Release 根因分析技术备忘录

> **创建日期**: 2026-08-05  
> **适用范围**: tlm-hook-failsafe PSGallery 发布工作流（publish-psgallery.yml）  
> **关联版本**: v1.1.5 → v1.1.8  
> **关联 CI Runs**: #30919434635（起点）/ #30928367863（v1.1.5）/ #30933929583（v1.1.7）/ #30934961843（v1.1.8 终点）  
> **关联文档**: [node20_deprecation_action_upgrade_memo.md](./node20_deprecation_action_upgrade_memo.md)

---

## 一、背景

v1.1.5 发布到 PSGallery 后，CI 日志出现两类警告：

1. **PSGallery license 警告**: `WARNING: All published packages should have license information specified`
2. **GitHub Release 未创建**: publish job 的 "创建 GitHub Release" step 状态为 skipped

v1.1.6 → v1.1.8 三次迭代修复，本备忘录记录完整根因分析与修复路径。

---

## 二、License 警告修复历程

### 2.1 问题现象（v1.1.5）

CI 日志：
```
WARNING: All published packages should have license information specified.
Learn more: https://aka.ms/deprecateLicenseUrl.
```

PSGallery 验证：`Find-Module tlm-hook-failsafe` 返回 LicenseUri 为空。

### 2.2 三阶段修复路径

#### 阶段 1: 启用 .psd1 LicenseUri（v1.1.5）

修改 `tlm-hook-failsafe.psd1`，取消注释 LicenseUri：
```powershell
LicenseUri = 'https://github.com/nzt47/security-tools/blob/master/packages/tlm-hook-failsafe/LICENSE'
```

**结果**: 警告仍在。根因：.psd1 的 LicenseUri 不会自动写入 .nupkg 的 nuspec。

#### 阶段 2: .nuspec 模板添加 `<licenseUrl>`（v1.1.6）

修改 `publish-to-psgallery.ps1` 的 .nuspec 模板：
```xml
<licenseUrl>$licenseUri</licenseUrl>
```

**遭遇两个坑**:

**坑 1: `<licenseUrl>` 与 `<license>` 不能共存**  
```
The licenseUrl and license elements cannot be used together.
```
NuGet 不允许同时使用两个 license 元素。修复：只保留 `<licenseUrl>`。

**坑 2: ReleaseNotes 含尖括号破坏 XML 结构**  
```
The 'licenseUrl' start tag on line 10 position 68 does not match the end tag of 'releaseNotes'.
```
ReleaseNotes 文本 `"adding <licenseUrl> to nuspec template"` 中的 `<licenseUrl>` 被 XML 解析器误认为标签。修复：添加 XML 转义 + ReleaseNotes 去尖括号。

```powershell
# XML 转义防护
$releaseNotes  = $releaseNotes -replace '&', '&amp;' -replace '<', '&lt;' -replace '>', '&gt;'
$description   = $description  -replace '&', '&amp;' -replace '<', '&lt;' -replace '>', '&gt;'
```

**结果**: v1.1.6 发布成功，"license information specified" 警告消除 ✅。但出现新警告：
```
WARNING: NU5125: The 'licenseUrl' element will be deprecated.
Consider using the 'license' element instead.
```

#### 阶段 3: 迁移到 `<license type="expression">MIT</license>`（v1.1.7）

NuGet 4.9.2+ 推荐用 `<license>` 元素替代 `<licenseUrl>`：

```xml
<!-- 旧（v1.1.6，有 NU5125 警告）-->
<licenseUrl>$licenseUri</licenseUrl>

<!-- 新（v1.1.7+，无警告）-->
<license type="expression">MIT</license>
```

**三种 license 方案对比**:

| 方案 | 兼容性 | 警告 | 风险 |
|------|--------|------|------|
| `<licenseUrl>` | 全兼容 | NU5125 deprecation | 低 |
| `<license type="expression">MIT` | NuGet 4.9.2+（2018+） | 无 | 中（需验证 PSGallery UI） |
| `<license type="file">LICENSE` | NuGet 4.9.2+ | 无 | 高（需 `<file src="LICENSE">` 配合，曾导致 `entryName` 错误） |

**最终选择**: `<license type="expression">MIT</license>`（SPDX 表达式，无需嵌入文件）

**验证**: v1.1.7/v1.1.8 CI 日志无 NU5125 警告 ✅，PSGallery 接受 ✅

### 2.3 关键经验

1. **.psd1 LicenseUri 与 .nuspec 是独立的**：.psd1 的 LicenseUri 仅供 PSGallery UI 显示，不自动写入 .nupkg
2. **`<license>` 与 `<licenseUrl>` 互斥**：NuGet 硬约束，不可共存
3. **ReleaseNotes 必须做 XML 转义**：任何含 `<` `>` `&` 的文本都会破坏 .nuspec XML 结构
4. **`<license type="expression">` 优于 `<license type="file">`**：SPDX 表达式无需嵌入文件，避免 entryName 错误

---

## 三、GitHub Release 未创建根因分析

### 3.1 问题现象（v1.1.5）

publish job 的 "创建 GitHub Release" step 状态为 **skipped**，GitHub Release 不存在。

### 3.2 两层根因

#### 根因 1: if 条件不覆盖 auto-tag 触发路径（v1.1.7 修复）

原 if 条件：
```yaml
if: startsWith(github.ref, 'refs/tags/v')
```

**问题**: auto-tag 在同工作流内创建 tag，但 `github.ref` 仍是 `refs/heads/master`，不满足条件。

**触发场景对比**:

| 触发方式 | `github.ref` | 满足原条件？ | Release 创建？ |
|----------|-------------|--------------|---------------|
| 手动打 tag push | `refs/tags/v1.1.5` | ✅ 是 | ✅ 创建 |
| auto-tag 同工作流 | `refs/heads/master` | ❌ 否 | ❌ 跳过 |
| workflow_dispatch | `refs/heads/master` | ❌ 否 | ❌ 跳过 |

**修复**（v1.1.7）: 扩展 if 条件覆盖三种触发路径
```yaml
if: |
  success() &&
  (
    startsWith(github.ref, 'refs/tags/v') ||
    needs.auto-tag.outputs.tagged == 'true' ||
    (github.event_name == 'workflow_dispatch' && github.event.inputs.force_publish == 'true')
  )
```

同时显式指定 `tag_name`（不依赖 `github.ref_name`）：
```yaml
tag_name: v${{ steps.version.outputs.version }}
```

**结果**: v1.1.7 CI 中 Release step **仍 skipped** ❌ → 存在第二层根因

#### 根因 2: GitHub Actions needs 传递依赖 outputs 不可访问（v1.1.8 修复）

**这是最隐蔽的根因**。

原 publish job 配置：
```yaml
publish-to-psgallery:
  needs: dry-run-validate   # ← 只声明依赖 dry-run-validate
```

依赖链：`publish → dry-run-validate → auto-tag`（传递依赖）

**问题**: GitHub Actions 中 `needs.<job>.outputs.*` **只在直接 needs 关系中可访问**。传递依赖的 outputs 在下游 job 中求值为空字符串。

验证（API 查询）:
```
gh api repos/.../actions/jobs/<auto-tag-job-id>
→ outputs: null   ← API 端点不返回，但 workflow 内部可用（如果直接 needs）
```

publish job 的 if 条件 `needs.auto-tag.outputs.tagged == 'true'` 求值为 `'' == 'true'` = false。但 publish job 仍能运行，因为：
- master push 路径：第三项 `needs.auto-tag.outputs.tagged == 'true'` 求值为 false
- 但 publish job 的 if 是 job 级别，`needs: dry-run-validate` 满足后 job 运行
- Release step 的 if 中再次引用 `needs.auto-tag.outputs.tagged`，**在 step 级别同样不可访问**（传递依赖）

**修复**（v1.1.8）: publish job 显式声明 `needs: [auto-tag, dry-run-validate]`
```yaml
publish-to-psgallery:
  needs: [auto-tag, dry-run-validate]   # ← 显式声明 auto-tag 直接依赖
```

**验证**: v1.1.8 CI 中 Release step **success** ✅，GitHub Release v1.1.8 创建成功 ✅

### 3.3 GitHub Actions outputs 传递规则（关键知识点）

| 场景 | `needs.A.outputs.x` 可访问？ |
|------|------------------------------|
| `job B: needs: [A]` | ✅ 是（直接依赖） |
| `job C: needs: [B]`, `job B: needs: [A]` | ❌ 否（传递依赖，C 不能访问 A 的 outputs） |
| `job C: needs: [A, B]` | ✅ 是（A 和 B 都是直接依赖） |

**推论**: 任何需要访问上游 job outputs 的 job，必须在 `needs` 中显式声明该上游 job，不能依赖传递关系。

### 3.4 调试方法

1. **查 job outputs（API 端点）**:
   ```powershell
   gh api repos/$owner/$repo/actions/jobs/$jobId --jq '.outputs'
   ```
   注意：API 返回 null 是设计行为，不代表 outputs 未设置。workflow 内部仍可访问。

2. **查 step 是否设置 output**:
   ```powershell
   gh run view $runId --log --job=$jobId | Select-String "tagged=|GITHUB_OUTPUT"
   ```

3. **查 step 级 if 求值**:
   GitHub Actions 不直接打印 if 求值结果，需通过 step 的 conclusion（success/skipped）反推。

---

## 四、完整修复时间线

| 版本 | 修改 | CI Run | 结果 |
|------|------|--------|------|
| v1.1.5 | 启用 .psd1 LicenseUri | #30928367863 | license 警告仍在 |
| v1.1.6 | .nuspec 添加 `<licenseUrl>` + XML 转义 | #30930640173 | license 警告消除，出现 NU5125 |
| v1.1.7 | 迁移到 `<license type="expression">MIT` + 扩展 Release if 条件 | #30933929583 | NU5125 消除 ✅，Release 仍 skipped |
| v1.1.8 | publish job 显式 `needs: [auto-tag, dry-run-validate]` | #30934961843 | **Release step success** ✅ |

---

## 五、最终验证状态（v1.1.8）

### 5.1 publish job 所有 step 状态

```
Set up job                            success
检出代码                              success
验证 PSGALLERY_API_KEY secret 已配置  success
设置 PowerShell 环境                  success
Sync 源码到包                         success
读取发布版本号                        success
发布到 PSGallery                      success
创建 GitHub Release（publish 成功后）  success  ← 关键修复点
发布总结                              success
Post 检出代码                         success
Complete job                         success
```

### 5.2 警告检查

| 警告类型 | v1.1.5 | v1.1.6 | v1.1.7 | v1.1.8 |
|----------|--------|--------|--------|--------|
| license information specified | ❌ | ✅ 消除 | ✅ | ✅ |
| NU5125 licenseUrl deprecated | - | ❌ 出现 | ✅ 消除 | ✅ |
| Node 20 deprecation (action-gh-release@v2) | - | - | - | ❌ 残留 |

### 5.3 遗留项（非阻断）

- **action-gh-release@v2 Node 20 警告**: v1.1.8 CI 发现 `softprops/action-gh-release@v2` 仍基于 Node 20（之前 Release step 被 skipped 未触发）。已升级到 `@v3`（Node 24 native），待下一个版本验证。

---

## 六、关键代码变更

### 6.1 publish-to-psgallery.ps1

```powershell
# 不易：使用 <license type="expression">MIT</license> 替代 <licenseUrl>（NuGet 4.9.2+ 推荐）
#       两者不能共存（NuGet 报 licenseUrl and license elements cannot be used together）
$licenseUri = $manifest.PrivateData.PSData.LicenseUri
if (-not $licenseUri) {
    Write-Host "  [WARN] .psd1 LicenseUri is empty; PSGallery UI license link will be missing"
}
# 不易：XML 转义 releaseNotes/description
$releaseNotes = $releaseNotes -replace '&', '&amp;' -replace '<', '&lt;' -replace '>', '&gt;'
$description  = $description  -replace '&', '&amp;' -replace '<', '&lt;' -replace '>', '&gt;'

$nuspec = @"
<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://schemas.microsoft.com/packaging/2011/08/nuspec.xsd">
  <metadata>
    ...
    <license type="expression">MIT</license>
    ...
  </metadata>
</package>
"@
```

### 6.2 publish-psgallery.yml

```yaml
publish-to-psgallery:
  # 不易：显式声明 needs auto-tag（不只是传递依赖 dry-run-validate）
  #       GitHub Actions 中 needs.auto-tag.outputs.* 只在直接 needs 关系中可访问
  needs: [auto-tag, dry-run-validate]
  ...
  steps:
    ...
    - name: 创建 GitHub Release（publish 成功后）
      if: |
        success() &&
        (
          startsWith(github.ref, 'refs/tags/v') ||
          needs.auto-tag.outputs.tagged == 'true' ||
          (github.event_name == 'workflow_dispatch' && github.event.inputs.force_publish == 'true')
        )
      uses: softprops/action-gh-release@v3   # v3 = Node 24 native
      with:
        tag_name: v${{ steps.version.outputs.version }}   # 显式构造，不依赖 github.ref_name
```

---

## 七、参考链接

- [NuGet License 文档](https://learn.microsoft.com/en-us/nuget/reference/nuspec#license)
- [NuGet 4.9.2 release notes](https://github.com/NuGet/Announcements/issues/32)（license 元素引入）
- [GitHub Actions needs 依赖文档](https://docs.github.com/en/actions/using-jobs/using-jobs-in-a-workflow#defining-prerequisite-jobs)
- [softprops/action-gh-release v3 release notes](https://github.com/softprops/action-gh-release/releases/tag/v3.0.0)
- [Node 20 弃用备忘录](./node20_deprecation_action_upgrade_memo.md)
- [v1.1.4 发布报告](../releases/release-note-tlm-hook-failsafe-v1.1.4-20260804.md)
