# 生产操作手册：Alertmanager → 139 邮箱 SMTP 告警链路

> 适用范围：生产服务器上完成 Alertmanager 邮件告警链路的**授权码安全注入**与**链路验收**。
> 工具：[一键部署工具 README](./README_AlertmanagerSMTP一键部署.md) / [生产部署检查清单](./生产部署检查清单_AlertmanagerSMTP_20260808.md) / [验证报告模板](./验证报告模板_SMTP端到端.md)
> 部署形态：docker compose（`deploy/monitoring/docker-compose.yml`），Alertmanager 容器 `yunshu-prod-alertmanager`。

## 0. 前置条件（开始前逐项确认）

| 项 | 要求 | 验证命令 |
|---|---|---|
| 监控栈运行 | 5 服务 Up (healthy) | `docker compose -f deploy/monitoring/docker-compose.yml ps` |
| 出站 587 放行 | 主机可连 `smtp.139.com:587` | `nc -vz smtp.139.com 587` |
| 授权码已取得 | 139 邮箱设置页生成（**非登录密码**） | — |
| 收件邮箱可用 | `13539371839@139.com` 可正常登录 | — |

> 若 587 不通，先按 [检查清单](./生产部署检查清单_AlertmanagerSMTP_20260808.md) 第 1-2 节放行防火墙/安全组后再继续，否则验收必然 FAIL。

## 1. 安全注入 SMTP 授权码（第 1 步）

### 1.1 推荐方式：交互式输入（授权码不进 shell 历史 / 进程列表）

```bash
# 方式 A（推荐，最安全）：工具内置交互输入，不回显、不进 shell 历史
bash scripts/alertmanager_smtp_deploy.sh apply --interactive

# 方式 B：先 read -s 读入变量再执行（输入不回显，命令本身不含明文）
read -s -p "输入 139 邮箱 SMTP 授权码: " SMTP_AUTH_CODE; echo
bash scripts/alertmanager_smtp_deploy.sh apply

# 方式 C（⚠️ 整行仍进 shell 历史，仅避免进程列表可见）：临时环境变量
SMTP_AUTH_CODE='<你的139邮箱授权码>' \
  bash scripts/alertmanager_smtp_deploy.sh apply
```

### 1.2 注意事项（【不易】授权码属敏感凭证）

- ✅ **推荐 `--interactive` 交互输入** → 不回显、不进 shell 历史、不进进程列表。
- ⚠️ 环境变量 `SMTP_AUTH_CODE=xxx bash ...` **整行仍会写入 shell 历史**，只是 `ps` 进程列表不可见；`read -s` 方式安全。
- ❌ 不要 `--auth-code '明文'` 直接写在命令行（明文进 shell 历史 + 进程列表）。
- ✅ 版本库防护：`deploy/monitoring/prometheus/alertmanager.yml` 已加入 `.gitignore`，`git add .` 不会误提交；提交前可用 `git check-ignore deploy/monitoring/prometheus/alertmanager.yml` 复核。
- ✅ 泄露处置：立即到 139 邮箱设置页**作废并重新生成**授权码。

### 1.3 验证注入结果

`apply` 完成后应看到：

```
[1] 读取配置: .../alertmanager.yml
    当前 smtp_auth_password: REPL****CODE (占位符=True)
[2] 替换占位符 → 真实授权码
[3] 验证 SMTP 端口连通性: smtp.139.com:587
    smtp.139.com:587 连通成功（TCP 握手完成）
[4] 容器侧校验 + 热加载
    amtool 校验通过 + SIGHUP 热加载已触发
[结果] 授权码已替换，端口连通性与配置加载均正常 ✓
```

> 若第 [3] 步显示 `connection refused`/超时：授权码已写入但网络未放行，**必须**先按检查清单修网络。

## 2. 执行链路验收（第 2 步）

### 2.1 只读验收（不含邮件测试）

```bash
bash scripts/alertmanager_smtp_deploy.sh verify
```

输出 8 段检查的 PASS/FAIL/WARN，最后汇总。**看到 `关键链路全部通过` + 退出码 0** 才算网络/配置侧达标。

### 2.2 含邮件发送测试（端到端最终确认）

```bash
bash scripts/alertmanager_smtp_deploy.sh verify --send-test
```

测试邮件流程：注入唯一测试告警 → 等待 group_wait(30s)+发送(40s) → 检视 `Notify for alerts completed`。

**成功标志**：

- 控制台：`[PASS] 邮件发送成功（Notify for alerts completed）`
- 邮箱：`13539371839@139.com` 收到标题含 `[FIRING:1] ProdSmtpVerify` 的邮件（**含垃圾箱**）

**失败定位**（日志错误关键字 → 处理）：

| 日志关键字 | 含义 | 处理 |
|---|---|---|
| `535 ... authentication failed` | 授权码错误 | 重新生成授权码 → 重新 `apply` |
| `454 ... TLS not available` / STARTTLS | TLS 协商异常 | 确认 587 端口 + `smtp_require_tls: true` |
| `dial tcp ... connection refused / timeout` | 网络未放行 | 检查清单第 1-2 节 |

## 3. 端到端模拟测试（第 3 步，可选）

生产网络受限/未部署时，可用本地模拟验证逻辑链路：

```bash
bash scripts/alertmanager_smtp_deploy.sh simulate --local-mock \
    --auth-code mock --report-out smtp_e2e_report.md
```

- 本地内置极简 SMTP 服务器（`127.0.0.1:1025`）接收 Alertmanager 发出的邮件。
- `587 外发`与`真实外发 139` 两步骤如实标记 `BLOCKED`（不假装通过）。
- 结束后**自动还原占位符并 reload**（生产配置零残留）。

## 4. 一键串联（推荐最终交付动作）

```bash
bash scripts/alertmanager_smtp_deploy.sh full \
    --auth-code '<你的139邮箱授权码>' --send-test
```

等价于：`apply`（注入+端口验证）→ 成功 → `verify --send-test`（含真实邮件测试）。任一环节失败即中断（非零退出码）。

## 5. 验收后收尾

1. **确认测试告警已 resolve**：`verify --send-test` 与 `simulate` 均会自动 resolve；若手工注入过告警，用 `curl -X POST` 补 resolve 或等 `repeat_interval` 自然消退。
2. **确认配置最终状态**：

   ```bash
   grep -E 'smtp_smarthost|smtp_auth_password|smtp_require_tls' \
     deploy/monitoring/prometheus/alertmanager.yml
   ```

   期望：`smtp.139.com:587` + 真实授权码 + `true`（`simulate` 后若未 `--keep-code` 则为占位符，属预期）。
3. **归档验证报告**：将 `simulate --report-out` 生成的报告归档到 `docs/zh/知识库重构计划/`，或使用 [验证报告模板](./验证报告模板_SMTP端到端.md) 手工填写。
4. **（可选）导出配置快照**：`python scripts/export_monitoring_config_snapshot.py` 归档当前监控配置。

## 6. 常见问题（FAQ）

### Q1：`verify` 中 `OUTPUT 链存在 DROP/REJECT` 但没看到 587 规则

说明 iptables 有白名单策略但未放行 587 出站 → 按检查清单第 2.2 节追加 `iptables -A OUTPUT -p tcp --dport 587 -j ACCEPT` 后重测。

### Q2：`simulate` 收到 4 封邮件而不是 1 封？

本地模拟无去重干扰时可出现多条（含 resolve 恢复通知）；链路判定只看"收到且头完整"，数量非关键。

### Q3：授权码是 16 位数字字母串，直接粘贴会触发 shell 转义吗？

建议用**环境变量方式**注入（第 1.1 节），完全规避转义/历史问题；`apply` 内部对值做了单引号包裹，普通字符集（字母+数字）无风险。

### Q4：`full` 在 Windows 上能跑吗？

统一入口依赖 bash；Windows 可用 Git Bash / WSL 运行，`apply`/`simulate` 可跑（本地网络受限时 587 会 FAIL/BLOCKED，属预期）；`verify` 需 Docker 容器在线。

## 7. 操作时序总览

```text
① 取得 139 邮箱 SMTP 授权码（设置页生成）
        ↓
② SMTP_AUTH_CODE 环境变量注入 → apply（注入 + 587 验证 + reload）
        ↓
③ verify（只读 8 段检查）
        ↓
④ verify --send-test（真实测试邮件 → 收件确认）
        ↓
⑤ simulate --local-mock --report-out（可选：逻辑链路模拟 + 报告归档）
        ↓
⑥ 收尾：resolve 确认 / 配置状态确认 / 报告归档 / 快照导出
```
