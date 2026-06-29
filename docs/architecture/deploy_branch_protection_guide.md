# 分支保护规则与通知机制部署指南

## 前置条件

1. GitHub 仓库的管理员权限
2. GitHub Personal Access Token（需要 `repo` 权限）
3. （可选）Slack 或 Microsoft Teams Webhook URL

## 步骤 1：创建 GitHub Personal Access Token

1. 打开 https://github.com/settings/tokens
2. 点击 **Generate new token (classic)**
3. 勾选 `repo` 权限（Full control of private repositories）
4. 生成并复制 Token

## 步骤 2：配置分支保护规则

```bash
# 设置 Token 环境变量
set GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# 自动检测仓库并配置分支保护
python scripts/configure_branch_protection.py

# 或指定仓库名
python scripts/configure_branch_protection.py --repo nzt47/security-tools

# 自定义配置（2 人审批 + 额外检查项）
python scripts/configure_branch_protection.py --review-count 2 --extra-checks "ci/build"
```

配置的规则：
- 必需状态检查：`architecture-check`
- 强制管理员遵守：是
- 要求 PR 审批：1 人
- 禁止 force push / 删除分支
- 要求线性历史

## 步骤 3：设置 GitHub Secrets（用于通知机制）

在 GitHub 仓库页面设置 Secrets，供 CI 工作流使用：

1. 打开 `https://github.com/nzt47/security-tools/settings/secrets/actions`
2. 点击 **New repository secret**
3. 添加以下 Secrets：

| Secret 名称 | 值 | 用途 |
|------------|-----|------|
| `GITHUB_TOKEN` | （自动提供，无需手动设置） | PR 评论 |
| `ARCH_WEBHOOK_URL` | `https://hooks.slack.com/services/xxx` | Webhook 通知（可选） |

> `GITHUB_TOKEN` 在 GitHub Actions 中自动可用，无需手动设置。
> `ARCH_WEBHOOK_URL` 仅在需要 Slack/Teams 通知时设置。

## 步骤 4：验证配置

### 4.1 验证分支保护规则

```bash
# 查看当前配置
python scripts/configure_branch_protection.py --dry-run
```

或通过 GitHub Web UI 验证：
1. 打开 `https://github.com/nzt47/security-tools/settings/branches`
2. 确认 `main` 分支有保护规则
3. 确认 `architecture-check` 在必需状态检查列表中

### 4.2 验证 CI 工作流

创建一个测试 PR 验证 CI 流程：

```bash
# 创建测试分支
git checkout -b test/arch-check-verify

# 做一个小改动
echo "# test" >> agent/__init__.py

git add . && git commit -m "test: 验证架构检查 CI"
git push origin test/arch-check-verify

# 在 GitHub 上创建 PR，观察：
# 1. architecture-check 工作流自动触发
# 2. PR 评论显示校验结果
# 3. Merge 按钮状态
```

### 4.3 验证通知机制

```bash
# 无违规场景（正常 PR）
python scripts/notify_arch_violation.py --no-pr-comment --no-webhook
# 预期输出：✅ 无未豁免违规，无需发送通知

# 有违规场景（模拟）
python scripts/notify_arch_violation.py \
  --report docs/architecture/arch_rules_report_simulated.json \
  --no-pr-comment --no-webhook
# 预期输出：🚨 检测到 N 个未豁免架构违规 + 归档 + 指标
```

## 配置后的 CI 流程

```
开发者提交 PR
  │
  ├── architecture-check 工作流自动触发
  │     ├── 步骤 1: 生成模块依赖图
  │     ├── 步骤 2: 架构规则校验（阻断合并）
  │     ├── 步骤 2.5: 违规通知与监控记录 ← 新增
  │     │     ├── 无违规 → 仅更新 Prometheus 指标
  │     │     └── 有违规 → 归档 + 指标 + Webhook + PR评论
  │     ├── 步骤 3: 变更影响分析
  │     ├── 步骤 4: 合规性报告
  │     ├── PR 评论校验结果
  │     └── Artifact 上传报告
  │
  ├── ❌ 有未豁免违规
  │     → PR 红色 ❌，Merge 按钮灰色
  │     → Slack/Teams 收到告警通知
  │     → PR 评论显示违规详情+修复建议
  │     → 违规事件归档到 violation_events/
  │     → Prometheus 指标更新
  │
  └── ✅ 无违规或全部豁免
        → PR 绿色 ✅，满足条件后可合并
```

## 故障排查

| 问题 | 解决方案 |
|------|---------|
| `architecture-check` 搜不到 | 工作流需先成功运行一次，创建一个小 PR 触发 |
| Token 权限不足 | 确保 Token 有 `repo` 权限 |
| Webhook 通知未发送 | 检查 `ARCH_WEBHOOK_URL` Secret 是否设置 |
| PR 评论未发送 | 检查 `GITHUB_TOKEN` 是否可用（Actions 自动提供） |
| 分支保护规则不生效 | 确认 `enforce_admins` 为 true |
