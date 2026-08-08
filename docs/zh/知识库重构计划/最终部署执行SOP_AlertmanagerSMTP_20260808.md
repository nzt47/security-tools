# 最终部署执行 SOP：Alertmanager → 139 邮箱 SMTP 告警链路

> 适用对象：运维团队。目标：将[人工检查清单](./生产最终部署检查清单_人工确认_20260808.md)与[一键部署工具](./README_AlertmanagerSMTP一键部署.md)串联成一条**可执行、可回滚、可审计**的部署流水线。
> 全流程预估：0.5-1 小时（不含安全组审批等待）。
> 前置文档：[生产部署检查清单](./生产部署检查清单_AlertmanagerSMTP_20260808.md) / [生产操作手册](./生产操作手册_AlertmanagerSMTP_20260808.md) / [验证报告模板](./验证报告模板_SMTP端到端.md)

---

## 0. 角色与职责

| 角色 | 职责 |
|---|---|
| 执行人（值班运维） | 逐阶段执行、记录输出、归档报告 |
| 复核人（运维组长） | 核对 A/B 段人工确认项、审批上线 |
| 告警接收人 | 确认收到测试邮件（收件箱+垃圾箱） |

## 1. 流水线总览

```text
P0 准备    →  P1 人工确认  →  P2 授权码注入  →  P3 链路验收  →  P4 收尾归档
  15min       30min            2min             5min            5min
  [门控]      [门控]           [门控]           [门控]
```

**门控规则**：每阶段所有检查项通过（绿灯）才进入下一阶段；任一 FAIL → 停止流水线，修复后从该阶段重跑。

---

## P0 准备（执行人）

**目标**：环境就绪、工具可用、授权码在手。

| # | 操作 | 命令/方法 | 期望结果 | 通过 |
|---|---|---|---|---|
| P0.1 | 拉取最新代码 | `cd /opt/yunshu && git pull` | 工作区最新 | ☐ |
| P0.2 | 确认监控栈运行 | `docker compose -f deploy/monitoring/docker-compose.yml ps` | alertmanager `Up (healthy)` | ☐ |
| P0.3 | 确认工具存在 | `ls scripts/alertmanager_smtp_deploy.sh scripts/verify_prod_alertmanager.sh` | 两文件存在 | ☐ |
| P0.4 | getpass 兼容性预检（首次/换终端时） | `python scripts/test_getpass_compat.py` | 平台检测通过、输入不回显 | ☐ |
| P0.5 | 取得授权码 | 139 邮箱设置页生成 | 已取得，**不落任何明文载体** | ☐ |

**门控**：P0 全绿 → P1。

---

## P1 人工确认（执行人 + 复核人）

**目标**：脚本无法自动探测的组织级/服务端配置，逐项人工核实。

1. 执行 `[生产最终部署检查清单_人工确认]` 的 **A 段（防火墙/网络 9 项）**：
   - 安全组出站 587 已申请并生效（A1-A2）
   - 宿主机 firewalld/iptables/ufw 已放行或确认无拦截（A3-A5）
   - `nc -vz smtp.139.com 587` 手动复测（A6-A8）
2. 执行 **B 段（139 邮箱服务端 8 项）**：SMTP 服务开启、587+STARTTLS、授权码有效、白名单等。
3. 执行 **E 段（组织级 4 项）**：值班通知、SLA、轮换计划、审计台账。

**产出**：勾选完成的检查清单（或截图留痕）。

**门控**：A/B/E 段全部勾选，复核人签字 → P2。

---

## P2 授权码注入（执行人）

**目标**：安全注入授权码并热加载，全程授权码不落 shell 历史/版本库。

```bash
# 在服务器终端（建议 tmux 会话，防断连）执行：
bash scripts/alertmanager_smtp_deploy.sh apply --interactive
```

**交互提示**：`请输入 139 邮箱 SMTP 授权码（输入不回显）:` —— 粘贴授权码后回车。

**期望输出**（关键行）：

```text
[2] 替换占位符 → 真实授权码
[3] 验证 SMTP 端口连通性: smtp.139.com:587
    smtp.139.com:587 连通成功（TCP 握手完成）
[4] 容器侧校验 + 热加载
    amtool 校验通过 + SIGHUP 热加载已触发
[结果] 授权码已替换，端口连通性与配置加载均正常 ✓
```

**失败处置**：
- `[3] connection refused/超时` → 网络未放行，回 P1 修 A 段；授权码已写入（可留待修复后复验）
- `[ERROR] 未提供授权码` → 终端不支持交互（如 CI），改用 `read -s` 方式（见操作手册 1.1-B）
- amtool 校验失败 → 检查 yml 缩进，修复后重跑 P2

**安全复核（执行后）**：

```bash
grep smtp_auth_password deploy/monitoring/prometheus/alertmanager.yml   # 只应看到打码/非占位符
git check-ignore deploy/monitoring/prometheus/alertmanager.yml          # 应返回文件路径（已忽略）
```

**门控**：apply 退出码 0 + 安全复核通过 → P3。

---

## P3 链路验收（执行人 + 告警接收人）

**目标**：验证真实外发成功、邮件到达。

```bash
# ① 只读 8 段检查（快速回归）
bash scripts/alertmanager_smtp_deploy.sh verify

# ② 端到端含真实邮件（关键动作）
bash scripts/alertmanager_smtp_deploy.sh verify --send-test
```

**期望结果**：
- verify 汇总 `PASS≥8  FAIL=0`，退出码 0
- `[PASS] 邮件发送成功（Notify for alerts completed）`
- 告警接收人在 **收件箱或垃圾箱** 收到 `[FIRING:1] ProdSmtpVerify` 邮件

**失败处置**（错误关键字 → 动作）：

| 日志关键字 | 定位 | 处置 |
|---|---|---|
| `dial tcp ... connection refused/timeout` | 网络 | 回 P1 修 A 段 |
| `535 ... authentication failed` | 授权码 | 重新生成 → 重跑 P2 |
| `454 ... TLS` | 协议 | 确认 587 + `smtp_require_tls: true` |
| `failed to create config` | 配置 | amtool 校验修复 |

**门控**：FAIL=0 + 邮件确认收到 → P4。

---

## P4 收尾归档（执行人）

| # | 操作 | 命令/方法 | 期望 |
|---|---|---|---|
| P4.1 | 生成端到端报告 | `bash scripts/alertmanager_smtp_deploy.sh simulate --auth-code <码> --report-out docs/zh/知识库重构计划/验证报告_正式归档_<日期>.md`（或按模板手工填写） | 报告落盘 |
| P4.2 | 确认测试告警已清理 | `curl -s localhost:9093/api/v2/alerts` | 无 `ProdSmtpVerify`/`SmtpE2ESim` 残留 |
| P4.3 | 导出配置快照 | `python scripts/export_monitoring_config_snapshot.py` | 快照归档 backups/ |
| P4.4 | 更新运维台账 | 记录变更单号、执行人、结论 | 台账留痕 |

> ⚠️ P4.1 的 `simulate --auth-code <码>` 会把明文放命令行——**仅限离线归档场景**；否则直接运行后立刻清理 shell 历史（`history -d <行号>`），或改用 `--interactive`（simulate 暂不支持时走 read -s 透传）。

## 2. 回滚预案

| 场景 | 回滚动作 |
|---|---|
| 授权码泄露 | 139 设置页立即作废 → 重新生成 → P2 重跑 |
| 配置错误导致告警停发 | `git checkout deploy/monitoring/prometheus/alertmanager.yml`（注意先备份含码版本）→ `docker exec yunshu-prod-alertmanager kill -HUP 1` |
| 容器异常 | `docker compose -f deploy/monitoring/docker-compose.yml restart alertmanager` |
| 全部失败 | 停止流水线，升级运维组长 + 保留全部日志/报告备查 |

## 3. 交接与审计

- 本 SOP 每次执行后，将 P1 勾选清单 + P3 报告 + P4.3 快照一并归档至 `docs/zh/知识库重构计划/` 与 `backups/monitoring/`。
- 授权码轮换（默认 90 天）：重新执行 P2 → P3 即可，无需重复 P0/P1。
