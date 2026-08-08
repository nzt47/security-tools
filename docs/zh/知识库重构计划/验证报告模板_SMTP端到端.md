# SMTP 端到端告警测试验证报告

> 说明：本模板用于归档"Alertmanager → 139 邮箱 SMTP 邮件告警链路"的端到端测试结果。
> 推荐执行工具：`scripts/simulate_prod_smtp_e2e.py --auth-code <真实授权码> --report-out <本报告路径>`
> （脚本自动填充下方表格；若手工测试，请按提示填写 `【填写】` 标记处）

## 1. 元数据

| 字段 | 值 |
|---|---|
| 测试日期 | 【填写】 |
| 生产服务器 | 【填写】主机名 / IP |
| 测试人 | 【填写】 |
| 验证模式 | ☐ 本地模拟　☐ 生产实测 |
| 测试告警 instance | `e2e-【时间戳】` |
| Alertmanager 容器 | `yunshu-prod-alertmanager`（v0.27.0） |
| SMTP 目标 | `smtp.139.com:587`（STARTTLS） |
| 收件邮箱 | `13539371839@139.com` |

## 2. 前置状态核对

| 检查项 | 期望 | 实际 | 状态 |
|---|---|---|---|
| 容器运行 | Up (healthy) | 【填写】 | ☐ 通过　☐ 失败 |
| `smtp_smarthost` | `smtp.139.com:587`（非 465） | 【填写】 | ☐ 通过　☐ 失败 |
| `smtp_require_tls` | `true` | 【填写】 | ☐ 通过　☐ 失败 |
| `smtp_auth_password` | 非占位符 `REPLACE_WITH_SMTP_AUTH_CODE` | 【填写】 | ☐ 通过　☐ 失败 |
| 授权码来源 | 139 邮箱设置页生成（非登录密码） | 【填写】 | ☐ 通过　☐ 失败 |
| **防火墙/安全组：主机出站 587** | `nc -vz smtp.139.com 587` 成功 | 【填写】 | ☐ 通过　☐ 失败　☐ 未测 |
| **防火墙/安全组：容器内出站 587** | 容器内 `smtp.139.com:587` 可达 | 【填写】 | ☐ 通过　☐ 失败　☐ 未测 |
| **防火墙/安全组：OUTPUT 链策略** | 无 DROP/REJECT，或已放行 587 | 【填写】 | ☐ 通过　☐ 失败　☐ 未测 |

## 3. 端到端步骤结果

| 步骤 | 状态 | 说明 | 证据（日志/输出片段） |
|---|---|---|---|
| S1 前置检查（容器 + 配置） | 【PASS/FAIL/SKIP】 | 【填写】 | 【填写】 |
| S2 填入授权码 + reload | 【PASS/FAIL/SKIP】 | `smtp_auth_password → 前4****后4` | `kill -HUP 1` 后日志 `Completed loading` |
| S3 SMTP 587 端口连通性 | 【PASS/FAIL/BLOCKED】 | 【填写】 | `dial tcp ...` / `Connection succeeded` |
| S4 注入测试告警 | 【PASS/FAIL】 | `POST /api/v2/alerts → HTTP 200` | 【填写】 |
| S5 邮件发送 | 【PASS/FAIL/BLOCKED】 | `Notify for alerts completed` | 【填写】 |
| S6 resolve 清理 | 【PASS/WARN】 | resolve → HTTP 200 | 【填写】 |

## 4. 关键日志证据（粘贴关键行）

```text
【粘贴：docker logs yunshu-prod-alertmanager --since 5m 中含 Notify / smtp / dial tcp 的行】
```

## 5. SMTP 超时与重试机制说明

### 5.1 通知失败重试（Alertmanager 内建行为）

- Alertmanager 对**发送失败的通知会自动重试**：失败后按 `group_interval`（当前配置 5m）间隔周期性重发，直到发送成功或告警被 resolve。
- 因此日志中 `Notify attempt failed` **反复出现属于正常重试**，不代表死循环；重点看错误关键字定位根因：
  - `dial tcp ... connection refused / i/o timeout` → **网络层**问题（防火墙/安全组未放行 587 出站）
  - `535 ... authentication failed` → **凭证**问题（授权码错误）
  - `454 ... TLS not available` / STARTTLS 相关 → **协议**问题（端口/`smtp_require_tls` 配置）
- 成功标志：出现 `Notify for alerts completed`。
- 影响：网络/凭证未修复前，同一告警每 `group_interval`（5m）重试一次，日志量可控；修复后下个周期自动成功，无需重启。

### 5.2 SMTP 连接超时

- 139 邮箱 587 端口连接超时：底层表现为 `dial tcp 120.232.169.x:587: i/o timeout`（丢包/黑洞）或 `connection refused`（明确拒绝）。
- 排查顺序建议：**先网络（检查清单第 1-2 节）→ 再凭证 → 再协议**，避免在错误根因上反复重试浪费时间。

### 5.3 本测试的预期表现

| 场景 | 日志预期 | 判定 |
|---|---|---|
| 网络放行 + 授权码正确 | `Notify for alerts completed`，邮箱收到邮件 | 通过 |
| 网络未放行 | 反复 `Notify attempt failed ... connection refused` | 失败（先修网络） |
| 授权码错误 | `Notify attempt failed ... 535` | 失败（重新生成授权码） |

## 6. 收件确认

- ☐ 收件箱已收到标题含 `[FIRING:1] SmtpE2ESim` 的邮件
- ☐ 垃圾箱确认过（若未在收件箱）
- ☐ From = `13539371839@139.com`，To = `13539371839@139.com`
- ☐ 告警 resolve 后收到恢复邮件（`send_resolved: true`）

## 7. 结论

- 本地逻辑链路验证（receiver / email_configs / SMTP 会话）：☐ 通过　☐ 未通过　☐ 未执行
- 生产真实外发：☐ 通过　☐ 未通过　☐ 待生产实测（BLOCKED，网络受限）
- 防火墙/安全组 587 放行：☐ 通过　☐ 未通过　☐ 未测
- 整体判定：☐ 通过　☐ 未通过　☐ 待生产实测

## 8. 遗留问题与后续动作

| 问题 | 严重度 | 处理人 | 状态 |
|---|---|---|---|
| 【填写，如：授权码仍为占位符】 | 【高/中/低】 | 【填写】 | ☐ 待办　☐ 已解决 |
| 【填写，如：生产 587 出站被防火墙阻断】 | 【高/中/低】 | 【填写】 | ☐ 待办　☐ 已解决 |
| 【填写，如：OUTPUT 链 DROP 策略未放行 587】 | 【高/中/低】 | 【填写】 | ☐ 待办　☐ 已解决 |
| 【填写，如：授权码过期/错误导致 535 重试中】 | 【高/中/低】 | 【填写】 | ☐ 待办　☐ 已解决 |

---

*模板说明：本文件为归档模板，字段使用 `【填写】` 标注；脚本自动生成时会被实际值替换。*
