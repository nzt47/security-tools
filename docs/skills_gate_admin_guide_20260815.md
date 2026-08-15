# Skills Gate 管理员权限操作指南

> 适用仓库：nzt47/security-tools
> 日期：2026-08-15
> 关联：`docs/pr634_final_merge_summary_20260815.md` §2.3（Skills Gate 权限不足根因分析）

---

## 1. 问题背景与根因

**现象**：`Skills Check` workflow 的 `Skills Gate (汇总门禁)` job 失败：

```
HTTP 403 Resource not accessible by integration
```

**失败点**：job 末尾"检查 Branch Protection 状态"步骤调用
`scripts/rollback-protection.ps1 -Action status`，该脚本通过 GitHub REST API
查询分支保护配置：

```
GET repos/{owner}/{repo}/branches/{branch}/protection
```

**根因**：branch protection API（含查询）需要**仓库管理员权限**，而 CI 中传入的
`secrets.GITHUB_TOKEN`：

1. 由 GitHub Actions 自动生成，权限范围由 workflow 的 `permissions` 声明控制
   （本 workflow 为 `contents: read`），**不具备 admin 级 repository administration 权限**；
2. 在普通仓库中，GITHUB_TOKEN **永远无法**获得 admin 权限——这是 GitHub 的安全边界。

**影响**：该步骤为只读状态检查（`status` 动作，不修改任何配置），失败**不阻断合并**，
但 Skills Gate 汇总门禁长期显示失败，影响 CI 健康度观察与后续把该 job 设为
branch protection required check 的计划。

**结论**：这是 **CI 凭证配置问题，与业务代码无关**。需要仓库管理员介入配置。

## 2. 前置：两处 GH_TOKEN 使用点

`.github/workflows/skills-check.yml` 中 `GH_TOKEN` 共 **2 处**，均为
`env: GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}`：

| # | 位置 | 用途 |
|---|---|---|
| 1 | `skills-gate` job → `参数组合行为测试` 步骤 | 运行 `test-rollback-params.ps1`（验证 -WhatIf/-Confirm/-Force 优先级） |
| 2 | `skills-gate` job → `检查 Branch Protection 状态` 步骤 | 运行 `rollback-protection.ps1 -Action status`（查询保护配置） |

`rollback-protection.ps1` 内部通过 `Invoke-GhApi` 调用以下端点（均需 admin）：

```
GET    repos/{owner}/{repo}/branches/{branch}/protection
GET    repos/{owner}/{repo}/branches/{branch}/protection/required_status_checks
PUT    repos/{owner}/{repo}/branches/{branch}/protection/required_status_checks
```

> 注：工作流实际只执行 `status`（GET）；`enable/disable`（PUT）用于人工回滚演练，
> CI 不调用。

## 3. 方案 A（推荐）：管理员 PAT + `ADMIN_GH_PAT` Secret 替换

用仓库管理员的 Personal Access Token（PAT）替代 `GITHUB_TOKEN` 传入上述两处。

### 步骤 1：管理员创建 PAT

**推荐 fine-grained PAT**（权限最小化）：

1. 管理员登录 GitHub → `Settings → Developer settings → Fine-grained tokens → Generate new token`；
2. Repository access：仅选择 `security-tools`（或包含本仓库的 organization）；
3. Permissions：
   - **Administration** → **Read and write**（分支保护 API 所需）；
   - Contents → **Read**（如果脚本后续需要读代码）；
4. 生成并**立即复制保存**（只显示一次）。

> 备选 classic PAT：`Settings → Developer settings → Personal access tokens → Tokens (classic)`，
> 勾选 **repo** scope 即可（管理员身份下 repo 权限可访问分支保护 API）。

### 步骤 2：在仓库配置 Secret

1. 仓库 → `Settings → Secrets and variables → Actions → New repository secret`；
2. Name：`ADMIN_GH_PAT`
3. Value：粘贴步骤 1 的 PAT；
4. 保存。

### 步骤 3：修改 workflow 替换两处 GH_TOKEN

编辑 `.github/workflows/skills-check.yml`，将两处：

```yaml
GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

替换为：

```yaml
GH_TOKEN: ${{ secrets.ADMIN_GH_PAT }}
```

两处位置：`skills-gate` job 的 `参数组合行为测试` 与 `检查 Branch Protection 状态` 步骤。

### 步骤 4：验证

1. 触发 workflow（手动 `workflow_dispatch` 或对 `data/skills.json` 等路径 push）；
2. 观察 `Skills Gate (汇总门禁)` job：
   - ✅ 通过：`Branch Protection 状态检查` 步骤输出 Protection 配置详情（不再 403）；
   - ❌ 仍失败：检查 Secret 是否已生效（repo → Settings → Secrets 确认 `ADMIN_GH_PAT` 存在）、
     PAT 是否过期、权限是否已授权给本仓库。

## 4. 方案 B（备选）：移除 Branch Protection 状态检查

如果暂时不打算配置管理员 PAT，可删除 `skills-gate` job 中
`检查 Branch Protection 状态` 步骤（仅该步骤需要 admin）：

```yaml
- name: 检查 Branch Protection 状态
  shell: pwsh
  env:
    GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  run: |
    ...
```

保留"参数组合行为测试"步骤（不需要 admin，`GITHUB_TOKEN` 足够）。

> 权衡：移除后将失去"合并前 Protection 配置健康"的自动校验，不推荐作为长期方案。

## 5. 安全与维护要求

1. **PAT 权限最小化**：fine-grained PAT 仅授权 `security-tools` + Administration(Read/write)，
   不用仓库级全局 classic token；
2. **Secret 命名**：统一用 `ADMIN_GH_PAT`，与代码注释保持一致，便于后续维护；
3. **定期轮换**：PAT 应设置有效期（建议 ≤ 90 天）并在到期前轮换更新 Secret；
4. **禁止入库**：PAT 只存在于 GitHub Secrets，严禁写入仓库文件、提交记录或文档；
5. **审计**：如果 organization 有成员审核机制，PAT 归属应登记到管理员名下便于追溯。

## 6. 验收标准

- [ ] `Skills Gate (汇总门禁)` job 在 PR 合并前 **SUCCESS**（不再 403）
- [ ] `检查 Branch Protection 状态` 步骤输出实际保护配置（而非错误提示）
- [ ] 两处 `GH_TOKEN` 均指向 `ADMIN_GH_PAT`，仓库无明文 PAT 残留
