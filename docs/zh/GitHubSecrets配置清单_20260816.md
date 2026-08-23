# GitHub Secrets 配置清单 — 审计日志告警 CI（7 项）

配套：`.github/workflows/audit-alert.yml`（Job1 `audit-alert` 生产分析 + 告警）
配置入口：仓库 **Settings → Secrets and variables → Actions → New repository secret**
读取方式：workflow 用 `${{ secrets.<名称> }}` 注入环境变量，脚本 `scripts/analyze_audit_logs.py` 读 `os.environ`（与本地 `.env` 单一数据源约定一致）

## 一、密钥清单（名称必须与下表完全一致，含大小写）

| # | Secret 名称 | 必填 | 用途 | 示例格式 |
|---|---|---|---|---|
| 1 | `SMTP_HOST` | 是 | SMTP 服务器地址（发信服务器） | `smtp.exmail.qq.com` / `smtp.qq.com` |
| 2 | `SMTP_PORT` | 否 | 端口：465=SSL / 587=STARTTLS / 2525=本地捕获 | `465` |
| 3 | `SMTP_USER` | 是* | 发信账号（无认证 SMTP 可留空） | `alert@example.com` |
| 4 | `SMTP_PASS` | 是* | 应用专用密码（勿用登录密码；含空格按原样粘贴） | `abcd efgh ijkl mnop` |
| 5 | `SMTP_TO` | 是 | 收件人；多收件人英文逗号分隔 | `ops@example.com` / `a@x.com,b@x.com` |
| 6 | `SMTP_SSL` | 否 | `1`=SSL（默认）/ `0`=STARTTLS 或明文 | `1` |
| 7 | `AUDIT_ALERT_THRESHOLD` | 否 | 租户异常请求占比告警阈值（百分比），缺省 5 | `5` |

> \* `SMTP_USER`/`SMTP_PASS`：使用无需认证的本地 SMTP（如联调捕获服务器 `127.0.0.1:2525`）时可留空，脚本跳过 login。
> 未配置 `SMTP_*` 时 CI 不失败（脚本降级仅打印告警、不发信），但收不到告警邮件。

## 二、配置步骤（勾选确认）

- [ ] 打开仓库 **Settings → Secrets and variables → Actions**
- [ ] 依次点击 **New repository secret**，添加上表 7 项（名称完全一致）
- [ ] `SMTP_PASS` 使用邮箱提供的**应用专用密码**（QQ 邮箱：设置 → 账户 → 开启 SMTP 生成授权码）
- [ ] 在 **Actions → Audit Log Alert → Run workflow** 手动触发一次验证
- [ ] 触发后检查 Job1 日志：显示「已导入 N 条审计记录」与阈值行
- [ ] 收到告警邮件（或日志显示 `[WARN] SMTP 未配置` 降级提示）
- [ ] Job2 `audit-alert-selfcheck` 通过（5 项断言，无 Secrets 依赖）
- [ ] 确认无误后每日 02:30 UTC（北京 10:30）自动执行

## 三、注入方式速查

```yaml
# 方式一：step 级注入 $GITHUB_ENV（当前 audit-alert.yml 采用）
run: |
  echo "SMTP_HOST=${{ secrets.SMTP_HOST }}" >> $GITHUB_ENV
  echo "AUDIT_ALERT_THRESHOLD=${{ secrets.AUDIT_ALERT_THRESHOLD }}" >> $GITHUB_ENV

# 方式二：job 级 env 上下文
env:
  SMTP_HOST: ${{ secrets.SMTP_HOST }}
  SMTP_SSL: ${{ secrets.SMTP_SSL || '1' }}   # 未配置时兜底默认值
```

完整四种引用片段见 `docs/zh/GitHubSecrets告警配置指南_20260816.md` §五。
