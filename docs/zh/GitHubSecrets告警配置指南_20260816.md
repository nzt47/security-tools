# GitHub Secrets 配置指南 — 审计日志告警 CI

配套：`.github/workflows/audit-alert.yml`（每日 02:30 UTC cron + 手动触发）
脚本：`scripts/analyze_audit_logs.py`（`load_smtp_config` 从环境变量读取）

## 一、需要配置的 Secrets（7 项）

| Secret 名 | 必填 | 格式 / 示例 | 说明 |
|---|---|---|---|
| `SMTP_HOST` | 是 | `smtp.exmail.qq.com` | SMTP 服务器地址 |
| `SMTP_PORT` | 否 | `465`（默认）/ `587` / `2525` | 端口；SSL 465 / STARTTLS 587 |
| `SMTP_USER` | 是 | `alert@example.com` | 发信账号（无认证 SMTP 可留空） |
| `SMTP_PASS` | 是 | `abcd efgh ijkl mnop` | 应用专用密码（勿用登录密码；空格含时按原样粘贴） |
| `SMTP_TO` | 是 | `ops@example.com` | 收件人；多收件人逗号分隔：`a@x.com,b@x.com` |
| `SMTP_SSL` | 否 | `1`（默认）/ `0` | 1=SSL，0=STARTTLS/明文 |
| `AUDIT_ALERT_THRESHOLD` | 否 | `5` | 租户异常占比告警阈值（百分比）；缺省 5 |

> 未配置 `SMTP_*` 时 CI 不失败（脚本降级仅打印告警），但不会真正发信；`SMTP_HOST`+`SMTP_TO` 缺失即不发信。

## 二、配置步骤

1. 打开仓库 **Settings → Secrets and variables → Actions**
2. 点击 **New repository secret**，依次添加上表 7 项（Name 与 Secret 名完全一致）
3. 建议先添加 `workflow_dispatch` 手动触发验证：仓库 **Actions → Audit Log Alert → Run workflow**
4. 确认后，每日 02:30 UTC（北京 10:30）自动执行

## 三、本地联调（与 CI 同一套键，走 .env）

```bash
# 复制 .env.example 的「审计日志告警配置」段到 .env 并填真实值
# 本地 SMTP 联调（无真实服务器）：
#   1) 启动捕获服务器: python scripts/dev/smtp_capture_server.py --port 2525
#   2) .env 设 SMTP_HOST=127.0.0.1 SMTP_PORT=2525 SMTP_SSL=0 SMTP_TO=test@local
#   3) 构造超阈值数据集并运行:
#      python scripts/analyze_audit_logs.py --audit-dir <含异常数据目录>
```

## 四、验证清单

- [ ] 7 项 Secret 已添加（名称与上表完全一致）
- [ ] Actions 页可手动 Run workflow（workflow_dispatch）
- [ ] 触发后 job 日志显示「已导入 N 条审计记录」与阈值行
- [ ] 收到告警邮件（或 job 日志显示 SMTP 未配置降级 WARN）

## 五、YAML 配置片段（如何在 workflow 中引用 Secrets）

### 5.1 方式一：注入 `$GITHUB_ENV`（当前 audit-alert.yml 采用，脚本读环境变量）

```yaml
jobs:
  audit-alert:
    runs-on: ubuntu-latest
    steps:
      - name: 注入 SMTP / 阈值配置（Secrets）
        run: |
          echo "SMTP_HOST=${{ secrets.SMTP_HOST }}" >> $GITHUB_ENV
          echo "SMTP_PORT=${{ secrets.SMTP_PORT }}" >> $GITHUB_ENV
          echo "SMTP_USER=${{ secrets.SMTP_USER }}" >> $GITHUB_ENV
          echo "SMTP_PASS=${{ secrets.SMTP_PASS }}" >> $GITHUB_ENV
          echo "SMTP_TO=${{ secrets.SMTP_TO }}" >> $GITHUB_ENV
          echo "SMTP_SSL=${{ secrets.SMTP_SSL }}" >> $GITHUB_ENV
          echo "AUDIT_ALERT_THRESHOLD=${{ secrets.AUDIT_ALERT_THRESHOLD }}" >> $GITHUB_ENV

      - name: 运行审计分析 + 告警
        run: python scripts/analyze_audit_logs.py   # 脚本读环境变量（等价本地 .env 约定）
```

### 5.2 方式二：job 级 `env` 上下文（整个 job 可见，适合脚本内直接 `os.environ`）

```yaml
jobs:
  audit-alert:
    runs-on: ubuntu-latest
    env:
      SMTP_HOST: ${{ secrets.SMTP_HOST }}
      SMTP_PORT: ${{ secrets.SMTP_PORT }}
      SMTP_USER: ${{ secrets.SMTP_USER }}
      SMTP_PASS: ${{ secrets.SMTP_PASS }}
      SMTP_TO: ${{ secrets.SMTP_TO }}
      SMTP_SSL: ${{ secrets.SMTP_SSL }}
      AUDIT_ALERT_THRESHOLD: ${{ secrets.AUDIT_ALERT_THRESHOLD }}
    steps:
      - run: python scripts/analyze_audit_logs.py
```

### 5.3 带默认值 / 可选 Secret（未配置时兜底）

```yaml
env:
  # SMTP_SSL 未配置时默认 '1'（SSL）
  SMTP_SSL: ${{ secrets.SMTP_SSL || '1' }}
  # 阈值未配置时默认 5
  AUDIT_ALERT_THRESHOLD: ${{ secrets.AUDIT_ALERT_THRESHOLD || '5' }}
  # 多收件人逗号分隔（示例：a@x.com,b@x.com）
  SMTP_TO: ${{ secrets.SMTP_TO }}
```

### 5.4 自检 job（每日验证告警链路，无 Secrets 依赖）

```yaml
jobs:
  audit-alert-selfcheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - name: 告警流程自检（构造 >5% 数据集 → mock 邮件 → 断言）
        run: python -X utf8 scripts/test_audit_alert_flow.py
```

> 完整示例见 `.github/workflows/audit-alert.yml`（含 Job 1 告警 + Job 2 自检）。
> 注意：`secrets.*` 仅在 workflow 定义的 `on:` 事件下可用；`pull_request` 从 fork 触发时 Secrets 不注入（安全设计）。
