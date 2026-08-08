# 生产环境部署检查清单：Alertmanager → 139 邮箱 SMTP 告警链路

> 适用范围：生产服务器上 Alertmanager（`yunshu-prod-alertmanager`）→ `smtp.139.com:587`（STARTTLS）邮件告警链路的端口与防火墙放行检查。
> 背景：本地已修复 SMTP 配置（465 隐式 TLS → 587 STARTTLS），并用本地模拟 SMTP 服务器验证了通知逻辑（`scripts/demo_smtp_chain_check.py`，exit=0）。但本地实测外发 `smtp.139.com:587` 时 `dial tcp 120.232.169.42:587: connection refused`，**证明本地网络被阻断**——生产服务器可能同样受限，部署后必须逐项核实本清单。

## 0. 结论速览（先看这几条）

| # | 关键结论 | 依据 |
|---|---|---|
| 1 | 端口必须用 **587**，不可用 465 | 139 邮箱 465 为隐式 TLS(SMTPS)，Alertmanager Go SMTP 客户端仅支持 STARTTLS |
| 2 | `smtp_require_tls: true` 保持开启 | 587 端口协商 STARTTLS，需显式声明 |
| 3 | `smtp_auth_password` 必须为**真实 SMTP 授权码**，禁止保留占位符 | 授权码 ≠ 登录密码，需在 139 邮箱设置页生成 |
| 4 | 本地 `connection refused` 是网络阻断，非配置问题 | `dial tcp 120.232.169.42:587: connection refused`（2026-08-07 实测） |

---

## 1. SMTP 587 端口出站连通性（最高优先级）

**在目标生产服务器上执行**（不是本机，本机已确认不通）：

```bash
# ① 基础 TCP 连通性（netcat）
nc -vz smtp.139.com 587

# ② 用 curl 做真实 SMTP 握手（发 EHLO，看 220 欢迎行）
curl -v smtp://smtp.139.com:587 --connect-timeout 5 2>&1 | head -20

# ③ DNS 解析确认（应解析到 139 邮箱 SMTP 网关，如 120.232.169.x）
getent hosts smtp.139.com
```

**预期结果**：

- `nc` 输出 `Connection to smtp.139.com 587 port [tcp/smtp] succeeded!`
- `curl` 输出中包含 `220` 欢迎行（SMTP 服务就绪）

**失败信号对照表**（逐字对照排查）：

| 报错形态 | 含义 | 处理 |
|---|---|---|
| `connection refused` | 出站 587 被防火墙/安全组/ISP 阻断 | 见第 2 节放行后重测 |
| `timed out` / `No route to host` | 中间路由黑洞或安全组未放行 | 检查云安全组出站规则 + 路由 |
| `getent hosts` 无结果 | DNS 故障 | 检查 /etc/resolv.conf 与内网 DNS |

> 【不易】只测 587。不要用 `telnet smtp.139.com 465` 验证——465 是隐式 TLS 端口，Alertmanager 不采用，测试结果无意义。

---

## 2. 防火墙 / 安全组策略放行

139 邮箱 SMTP 是**纯出站**需求（本服务器 → 139 邮箱），**不需要任何入站规则**。

### 2.1 云安全组（阿里云 / 腾讯云 / AWS）

- 默认出站全放行，但若自定义过出站规则（只放行 80/443 等），需追加：
  - 出站 TCP 目标端口 **587**
- 常见坑：出站按目标 IP 段限制时，需确认 `smtp.139.com` 解析出的 IP 段在放行范围内。

### 2.2 主机防火墙（firewalld / iptables / ufw）

```bash
# firewalld（CentOS/RHEL 8+）—— 查看现状
firewall-cmd --list-all
# 若 OUTPUT 有 DROP/REJECT 策略，放行 587 出站
firewall-cmd --permanent --add-port=587/tcp && firewall-cmd --reload

# iptables —— 查看 OUTPUT 链
iptables -L OUTPUT -n | grep -E 'smtp|587|DROP|REJECT'
# 放行出站 587
iptables -A OUTPUT -p tcp --dport 587 -j ACCEPT

# ufw（Ubuntu）
ufw status verbose
ufw allow out to any port 587 proto tcp
```

**验证放行**：重跑第 1 节 `nc -vz smtp.139.com 587`。

---

## 3. Alertmanager 容器侧验证

配置本身是 compose 挂载进容器的，但容器网络（`monitoring-net` 桥接 → 宿主机 NAT 出网）需独立验证：

```bash
# ① DNS 解析（在容器内）
docker exec yunshu-prod-alertmanager getent hosts smtp.139.com

# ② TCP 连通性（容器内 netcat 不存在时，用 bash /dev/tcp 探测）
docker exec yunshu-prod-alertmanager sh -c \
  'timeout 5 bash -c "exec 3<>/dev/tcp/smtp.139.com/587 && echo TCP-OK" || echo TCP-FAIL'

# ③ 直接看最近一次发送失败的报错形态（最有价值）
docker logs yunshu-prod-alertmanager --since 1h | grep -E 'Notify attempt failed' | tail -3
```

**日志报错对照**：

| 日志关键字 | 含义 | 定位 |
|---|---|---|
| `Notify attempt failed ... dial tcp ...: connection refused` | 出站网络阻断（与本地一致） | 第 1/2 节 |
| `... 535 ... authentication failed` | 授权码错误 | 换真实授权码 |
| `... 454 ... TLS not available` / `STARTTLS` 相关 | TLS 协商异常 | 确认 `smtp_require_tls: true` 且端口为 587 |
| `Notify for alerts completed` | 发送成功 | ✅ 链路通 |

---

## 4. 配置核对（alertmanager.yml）

文件：`deploy/monitoring/prometheus/alertmanager.yml`（compose 以只读挂载进容器）

| 检查项 | 期望值 | 当前状态 |
|---|---|---|
| `smtp_smarthost` | `'smtp.139.com:587'`（**不是 465**） | ✅ 已修复 |
| `smtp_require_tls` | `true`（587 走 STARTTLS） | ✅ 已修复 |
| `smtp_from` | `'13539371839@139.com'` | ✅ |
| `smtp_auth_username` | `'13539371839@139.com'` | ✅ |
| `smtp_auth_password` | **真实授权码，非占位符** | ⚠️ 待用户填入（当前为 `REPLACE_WITH_SMTP_AUTH_CODE`） |
| `email_configs.to` | `'13539371839@139.com'` | ✅ |
| `send_resolved` | `true`（恢复也通知） | ✅ |

**授权码获取**：登录 139 邮箱网页端 → 设置 → 客户端设置 / SMTP 服务 → 开启并生成授权码。**授权码是一次性生成的独立凭证，不等于邮箱登录密码**。填入后立即 reload（第 5 节）。

---

## 5. 配置校验 + 热加载

```bash
# ① amtool 校验配置语法
docker exec yunshu-prod-alertmanager amtool check-config /etc/alertmanager/alertmanager.yml

# ② 热加载（SIGHUP，零停机）
docker exec yunshu-prod-alertmanager kill -HUP 1

# ③ 确认加载成功
docker logs yunshu-prod-alertmanager --since 1m | grep 'Completed loading'
```

---

## 6. 端到端验证（填完授权码后执行）

```bash
# ① 触发一条测试告警（脚本会注入唯一 instance 标签，避开去重）
python scripts/demo_send_test_alert.py --smtp-auth-code <真实授权码>

# ② 观察 Alertmanager 日志
docker logs yunshu-prod-alertmanager --since 5m | grep -E 'Notify attempt failed|Notify for alerts completed'
```

成功标准：

- 日志出现 `Notify for alerts completed`
- 邮箱 `13539371839@139.com` 收到标题含 `[FIRING:1]` 的告警邮件
- 查看告警恢复邮件（`send_resolved`）可等告警自动 resolve 后复核

> 若生产仍 `connection refused`，先按第 1/2 节逐层排查；`demo_smtp_chain_check.py` 仅用于本地验证**通知逻辑**（已通过），不能替代生产外网连通性验证。

---

## 7. 收件端与整体链路自检

- 邮件收件箱 / **垃圾箱**均需确认（首次告警邮件易被误判）。
- 建议将 `13539371839@139.com` 加入白名单。
- Prometheus → Alertmanager 段（非本清单范围，已修复）：`http://<服务器>:9091/-/status` 的 Alertmanagers 区应显示 `yunshu-prod-alertmanager:9093` 为 up。

---

## 8. 验收命令汇总（一键执行）

```bash
set -e
echo "── 1. 主机出站 587 连通性 ──"
nc -vz smtp.139.com 587
echo "── 2. 容器内连通性 ──"
docker exec yunshu-prod-alertmanager sh -c 'timeout 5 bash -c "exec 3<>/dev/tcp/smtp.139.com/587 && echo TCP-OK" || echo TCP-FAIL'
echo "── 3. 配置校验 ──"
docker exec yunshu-prod-alertmanager amtool check-config /etc/alertmanager/alertmanager.yml
echo "── 4. 授权码是否已填（不应出现 REPLACE_WITH）──"
grep smtp_auth_password deploy/monitoring/prometheus/alertmanager.yml
echo "── 5. 最近发送结果 ──"
docker logs yunshu-prod-alertmanager --since 1h | grep -E 'Notify attempt failed|Notify for alerts completed' | tail -5
```

---

## 附：本次修复时间线（防回归）

| 时间 | 事件 | 结论 |
|---|---|---|
| 2026-08-07 | 发现 alertmanager.yml 路径为空目录 → 容器宕机 11 天 | 已重建配置 + 纳入 compose |
| 2026-08-07 | 配置 465 端口实测 `does not advertise the STARTTLS extension` | 改 587 + `smtp_require_tls: true` |
| 2026-08-07 | 本地实测 587 外发 `connection refused` | 本地网络阻断，非配置问题 |
| 2026-08-07 | `demo_smtp_chain_check.py` 本地模拟 SMTP 验证 | 通知逻辑通过（exit=0），配置已恢复 |
| 部署后 | **生产服务器执行本清单第 1-6 节** | 待办 |
