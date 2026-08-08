# Alertmanager SMTP 一键部署工具 README

> 适用范围：Alertmanager → 139 邮箱 SMTP（`smtp.139.com:587` + STARTTLS）邮件告警链路的部署、授权码注入与链路验收。
> 对应文档：[生产部署检查清单](./生产部署检查清单_AlertmanagerSMTP_20260808.md) / [生产操作手册](./生产操作手册_AlertmanagerSMTP_20260808.md)

## 1. 工具组成

| 文件 | 角色 | 运行位置 |
|---|---|---|
| `scripts/alertmanager_smtp_deploy.sh` | **一键统一入口**（本工具） | 生产服务器（Linux） |
| `scripts/apply_smtp_auth_code.py` | 子命令 `apply`：注入授权码 + 587 端口验证 | 任意（有 python3） |
| `scripts/simulate_prod_smtp_e2e.py` | 子命令 `simulate`：端到端模拟测试 | 任意（有 python3 + docker） |
| `scripts/verify_prod_alertmanager.sh` | 子命令 `verify`：生产一键验收（防火墙 + 邮件测试） | 生产服务器（Linux + docker） |

## 2. 环境要求

- **Python 3.10+**（本地 Windows 与生产 Linux 均可；仅标准库，无第三方依赖）
- **Docker**（Alertmanager 容器 `yunshu-prod-alertmanager` 运行中）
- **bash**（`verify` 子命令需要；Windows 可用 Git Bash / WSL）
- 生产服务器出站 TCP **587** 已放行（未放行时 `apply`/`verify` 会给出明确 FAIL 提示）

## 3. 快速开始

```bash
# ① 注入真实授权码并验证 587 端口（授权码经环境变量传入，不进 shell 历史）
SMTP_AUTH_CODE='你的139邮箱授权码' bash scripts/alertmanager_smtp_deploy.sh apply

# ② 生产一键验收（含防火墙检查 + 邮件发送测试）
bash scripts/alertmanager_smtp_deploy.sh verify --send-test

# ③ 本地模拟端到端（网络受限时验证逻辑链路，生成报告）
bash scripts/alertmanager_smtp_deploy.sh simulate --local-mock --auth-code mock \
    --report-out smtp_e2e_report.md
```

**一键串联**（注入授权码 → 自动验收）：

```bash
bash scripts/alertmanager_smtp_deploy.sh full --auth-code '你的139邮箱授权码' --send-test
```

## 4. 子命令详解

### 4.1 `apply` — 注入授权码 + 587 端口验证

```bash
# 方式 A：交互式输入（推荐，不回显、不进 shell 历史、不进进程列表）
bash scripts/alertmanager_smtp_deploy.sh apply --interactive

# 方式 B：环境变量（⚠️ 整行仍会留在 shell 历史，仅避免进程列表可见）
SMTP_AUTH_CODE='xxxx' bash scripts/alertmanager_smtp_deploy.sh apply

# 方式 C：命令行参数（⚠️ 明文会留在 shell 历史，不推荐）
bash scripts/alertmanager_smtp_deploy.sh apply --auth-code 'xxxx'

# 附加选项
bash scripts/alertmanager_smtp_deploy.sh apply --interactive \
    --skip-port-check \                # 跳过 587 连通性验证（仅替换配置）
    --config /path/to/alertmanager.yml # 指定配置文件（默认 deploy/monitoring/prometheus/alertmanager.yml）
```

行为：替换占位符 `REPLACE_WITH_SMTP_AUTH_CODE` → TCP 探测 `smtp.139.com:587` → amtool 校验 → SIGHUP 热加载。

### 4.2 `simulate` — 端到端模拟测试

```bash
# 生产模式（真实执行：注入 → reload → 触发告警 → 检视日志 → resolve）
python scripts/simulate_prod_smtp_e2e.py --auth-code 'xxxx' --report-out report.md

# 本地模拟模式（内置极简 SMTP 服务器验证逻辑链路；真实外发 139 标记 BLOCKED）
bash scripts/alertmanager_smtp_deploy.sh simulate --local-mock --auth-code mock \
    --report-out report.md
```

行为：S1 前置检查 → S2 注入授权码+reload → S3 587 连通性 → S4 注入唯一测试告警 → S5 等待 40s 检视日志 → S6 resolve 清理。结束后**默认还原占位符并 reload**（`--keep-code` 可保留）。

### 4.3 `verify` — 生产一键验收

```bash
bash scripts/alertmanager_smtp_deploy.sh verify          # 只读检查
bash scripts/alertmanager_smtp_deploy.sh verify --send-test  # 额外触发测试邮件
```

覆盖 8 段：出站 587 连通性 / DNS / 防火墙（firewalld/iptables/ufw）/ 容器状态 / 容器内连通性 / amtool 校验 / 占位符检查 / 最近发送日志（+ 可选邮件测试）。退出码：0=全过，1=存在 FAIL。

### 4.4 `full` — 一键串联

```bash
bash scripts/alertmanager_smtp_deploy.sh full --interactive --send-test
bash scripts/alertmanager_smtp_deploy.sh full --auth-code 'xxxx' --send-test  # ⚠️ 明文进 shell 历史
```

`apply` 成功后自动执行 `verify`；`apply` 失败即终止（exit 1）。

## 5. 安全注意事项

1. **授权码是敏感凭证**：优先用 `--interactive` 交互输入（不回显、不进 shell 历史、不进进程列表）。
2. **shell 历史风险**：`--auth-code` 参数与 `SMTP_AUTH_CODE=xxx bash ...` 环境变量赋值**整行都会留在 shell 历史**；环境变量仅避免进程列表（ps）可见。彻底规避必须用 `--interactive`。
3. **版本库风险**：`deploy/monitoring/prometheus/alertmanager.yml` 已加入 `.gitignore`（含真实授权码后禁止 `git add .` 误提交）；`apply` 前可用 `git check-ignore` 复核。
4. 脚本日志只显示**打码版本**（前 4 后 4），绝不输出明文。
5. `simulate` 结束后默认还原占位符，避免生产配置残留明文授权码（`--keep-code` 慎用，仅调试期）。
6. 139 邮箱授权码需在邮箱**设置页**生成（非登录密码）；泄露后请立即在设置页作废并重新生成。

## 6. 退出码约定

| 退出码 | 含义 |
|---|---|
| 0 | 全部通过 |
| 1 | 存在 FAIL 项（apply/verify/full） |
| 2 | 用法/前置错误（缺授权码、无 python/bash、未知子命令） |

## 7. 故障排查速查

| 现象 | 定位 |
|---|---|
| `dial tcp ... connection refused` | 出站 587 被防火墙/安全组阻断 → 见检查清单第 1-2 节 |
| `535 ... authentication failed` | 授权码错误 → 重新生成后 `apply` |
| `454 ... TLS not available` | TLS 协商异常 → 确认端口 587 + `smtp_require_tls: true` |
| `Notify attempt failed` 反复出现 | 网络/认证问题未解决 → 按错误关键字定位 |
| `amtool check-config` 失败 | 配置语法错误 → 检查 yml 缩进/引号 |

## 8. 关联文档

- [生产部署检查清单](./生产部署检查清单_AlertmanagerSMTP_20260808.md) — 端口/防火墙逐项检查
- [生产操作手册](./生产操作手册_AlertmanagerSMTP_20260808.md) — 安全注入 + 验收详细步骤
- [验证报告模板](./验证报告模板_SMTP端到端.md) — 端到端测试归档模板
