# 运维快速执行手册：Alertmanager → 139 邮箱 SMTP 告警链路

> 用途：值班运维**一页纸速查**。完整流程见 [最终部署执行SOP](./最终部署执行SOP_AlertmanagerSMTP_20260808.md)，详细步骤见 [生产操作手册](./生产操作手册_AlertmanagerSMTP_20260808.md)。
> 全流程预估 0.5-1 小时；每阶段**全绿才进下一步**，任一 FAIL 即停。

## 0. 五阶段流水线速查

| 阶段 | 动作 | 关键命令 | 门控 |
|---|---|---|---|
| P0 准备 | 拉代码/确认监控栈/取授权码 | `git pull`、`docker compose ps`、`python scripts/test_getpass_compat.py` | 全 PASS |
| P1 人工确认 | 防火墙 9 项 + 139 邮箱 8 项（对照[人工检查清单](./生产最终部署检查清单_人工确认_20260808.md)） | `bash scripts/preflight_prod_smtp.sh` 辅助预检 | 勾选+签字 |
| P2 授权码注入 | ⚠️ 交互式输入授权码（见下节） | `bash scripts/alertmanager_smtp_deploy.sh apply --interactive` | 退出码 0 |
| P3 链路验收 | 只读验收 + 真实测试邮件 | `verify` → `verify --send-test` | FAIL=0 + 邮件收到 |
| P4 收尾归档 | resolve 确认/快照/台账 | `curl localhost:9093/api/v2/alerts`、`python scripts/export_monitoring_config_snapshot.py` | 清单核对 |

> P0-P1 之间可加跑生产预检：`bash scripts/preflight_prod_smtp.sh`（587 连通性 + 防火墙策略，只读）。

## 1. ⚠️ 交互式输入授权码——安全注意事项（P2 必读）

**唯一推荐动作**：

```bash
bash scripts/alertmanager_smtp_deploy.sh apply --interactive
# 提示: 请输入 139 邮箱 SMTP 授权码（输入不回显）:
# 粘贴授权码 → 回车 → 全程屏幕无明文
```

**为什么安全（getpass 机制）**：

| 风险点 | getpass 表现 |
|---|---|
| 屏幕回显 | ✅ 不回显（Windows 走 `msvcrt`，Linux 走 `termios` 关闭 ECHO） |
| shell 历史 | ✅ 不进（授权码不在命令行中） |
| 进程列表 `ps` | ✅ 不可见 |
| 脚本日志 | ✅ 只打码（前 4 后 4），绝不输出明文 |

**必须遵守**：

1. ❌ **禁止** `apply --auth-code '明文'`——明文同时进 shell 历史 + 进程列表。
2. ⚠️ **禁止** `SMTP_AUTH_CODE='明文' bash ...`——整行仍写进 shell 历史（仅进程列表不可见）。
3. ⚠️ 在 **tmux 会话**执行（防断连），但**不要开窗格回放/日志**，避免授权码被录屏工具捕获。
4. ⚠️ 粘贴前确认终端**处于交互 TTY**（`test -t 0 && echo TTY`）；CI/管道环境 getpass 会退化回显，工具会拒绝交互——此时改用 `read -s`：

```bash
read -s -p "输入授权码: " SMTP_AUTH_CODE; echo
bash scripts/alertmanager_smtp_deploy.sh apply
```

5. ⚠️ 输入后若屏幕**出现了明文**（异常回显），立即按 Ctrl+C 终止，并到 139 设置页**作废重生成**。
6. ✅ 注入后复核：`git check-ignore deploy/monitoring/prometheus/alertmanager.yml` 应返回路径（已忽略）；`grep smtp_auth_password` 只应看到打码/非占位符。
7. ✅ 授权码泄露处置：139 设置页**立即作废** → 重新生成 → 重跑 P2。

## 2. 失败处置速查

| 现象 | 定位 | 动作 |
|---|---|---|
| `dial tcp ... connection refused/timeout` | 网络未放行 | 回 P1 修 A 段（安全组/防火墙），`preflight` 复测 |
| `535 ... authentication failed` | 授权码错误 | 重新生成 → 重跑 P2 |
| `454 ... TLS` | 协议 | 确认 587 + `smtp_require_tls: true` |
| `failed to create config` | 配置语法 | amtool 校验修复 |
| `[ERROR] 未提供授权码` | 非 TTY/无输入 | 改用 `read -s` 方式 |

## 3. 回滚速查

| 场景 | 动作 |
|---|---|
| 授权码泄露 | 设置页作废 → 重新生成 → 重跑 P2 |
| 配置错误致告警停发 | 备份含码版本 → `git checkout` 配置 → `docker exec yunshu-prod-alertmanager kill -HUP 1` |
| 容器异常 | `docker compose -f deploy/monitoring/docker-compose.yml restart alertmanager` |
| 全部失败 | 停流水线，升级组长，保留日志 |

## 4. 事后留痕

- P1 勾选清单 + P3 报告 + P4.3 快照归档至 `docs/zh/知识库重构计划/` 与 `backups/monitoring/`。
- 授权码 90 天轮换：重跑 P2 → P3 即可。
