# CI 邮件通知配置说明

> 关联 workflow：[`.github/workflows/log-perf-guard.yml`](../.github/workflows/log-perf-guard.yml)
> 作用：当日志性能守护 CI 失败时（每日定时构建或 push 到 main），自动发送邮件通知

## 一、需要配置的 Secrets 和 Variables

在 GitHub 仓库 → Settings → Secrets and variables → Actions 中配置：

### 1.1 Secrets（敏感信息）

| Secret 名称 | 说明 | 示例值 |
|-------------|------|--------|
| `MAIL_SERVER_ADDRESS` | SMTP 服务器地址 | `smtp.gmail.com` |
| `MAIL_SERVER_PORT` | SMTP 服务器端口 | `587` |
| `MAIL_USERNAME` | SMTP 用户名（发件人邮箱） | `ci-bot@example.com` |
| `MAIL_PASSWORD` | SMTP 密码或应用专用密码 | `****` |

### 1.2 Variables（非敏感信息）

| Variable 名称 | 说明 | 示例值 |
|---------------|------|--------|
| `MAIL_NOTIFY_RECIPIENTS` | 收件人邮箱列表（逗号分隔） | `dev-team@example.com,ops@example.com` |

## 二、常见 SMTP 服务商配置

### 2.1 Gmail（推荐使用应用专用密码）

```
MAIL_SERVER_ADDRESS = smtp.gmail.com
MAIL_SERVER_PORT = 587
MAIL_USERNAME = your-bot@gmail.com
MAIL_PASSWORD = <应用专用密码（16位）>
```

**注意**：Gmail 不支持使用账户密码登录，需在 Google 账户设置中生成"应用专用密码"。

### 2.2 QQ 邮箱

```
MAIL_SERVER_ADDRESS = smtp.qq.com
MAIL_SERVER_PORT = 465
MAIL_USERNAME = your-bot@qq.com
MAIL_PASSWORD = <授权码（16位）>
```

### 2.3 企业微信邮箱

```
MAIL_SERVER_ADDRESS = smtp.exmail.qq.com
MAIL_SERVER_PORT = 465
MAIL_USERNAME = ci-bot@your-company.com
MAIL_PASSWORD = <邮箱密码>
```

### 2.4 自建 SMTP 服务器

```
MAIL_SERVER_ADDRESS = mail.your-company.com
MAIL_SERVER_PORT = 587
MAIL_USERNAME = ci-bot@your-company.com
MAIL_PASSWORD = <密码>
```

## 三、通知触发条件

邮件通知 job（`notify-on-failure`）仅在以下条件**同时满足**时发送：

1. **质量门禁失败**：`log-perf-quality-gate` job 状态为 `failure`
2. **触发事件为以下之一**：
   - `schedule`（每日定时构建）
   - `push`（推送到 main/master/develop/release/**）

**PR 提交时不发送邮件**（避免开发阶段噪声），PR 失败会通过 PR 评论通知。

## 四、邮件内容模板

```
主题：⚠️ 日志性能守护 CI 失败 - <仓库名>

正文：
日志性能守护 CI 流水线检测到失败。

仓库: <owner>/<repo>
分支: <branch>
触发事件: <event>
提交: <commit-sha>
提交者: <actor>

任务结果:
- 日志压力测试: <success/failure>
- 双重序列化守护: <success/failure>
- 依赖注入单元测试: <success/failure>
- 质量门禁: <failure>

查看详情: <CI run URL>

此邮件由 CI 自动发送，请勿回复。
```

## 五、验证配置

配置完 secrets 和 variables 后，可手动触发 workflow 验证：

1. 进入 GitHub 仓库 → Actions 页面
2. 选择 "日志性能守护" workflow
3. 点击 "Run workflow"
4. 选择 `mode: both`，运行
5. 查看 `notify-on-failure` job 的输出，确认邮件是否发送成功

如果邮件发送失败，检查：
- SMTP secrets 是否正确配置
- 收件人邮箱地址是否正确
- SMTP 服务器是否允许 GitHub Actions IP 访问

## 六、禁用邮件通知

如需临时禁用邮件通知，在 `.github/workflows/log-perf-guard.yml` 中将 `notify-on-failure` job 的 `if` 条件改为 `if: false`：

```yaml
notify-on-failure:
  name: 失败邮件通知
  runs-on: ubuntu-latest
  needs: [...]
  if: false  # ← 临时禁用
  steps:
    ...
```

## 七、相关文档

- [CI 流水线文档](./ci_log_perf_guard.md)
- [依赖注入重构与 Bug 修复报告](./di_refactor_and_bugfix_report.md)
