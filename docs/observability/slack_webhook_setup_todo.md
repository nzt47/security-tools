# 待办清单：配置 SLACK_WEBHOOK_URL（M6）

> 来源：`docs/observability/README_maintenance_check.md` M6 检查项
> 维护者：nzt47 ｜ 优先级：低（不影响现有守卫功能，dry-run 模式安全）
> 创建：2026-08-05

## 一、现状

| 项 | 状态 |
|----|------|
| guard workflow Slack 通知步骤 | 已就绪（`.github/workflows/guard-master-commit-origin.yml` 第 152-171 行） |
| 步骤触发条件 | `failure() && steps.precheck.outputs.skip != 'true' && env.SLACK_WEBHOOK_URL != ''` |
| `SLACK_WEBHOOK_URL` secret | **未配置** → 步骤自动跳过（不产生噪音失败） |
| `slack_notify.py` 脚本链路 | 已验证正常：dry-run 实测输出 `ok:true, dry_run:true` |
| 模拟 BLOCK 报告 `%TEMP%\mock_guard_report.json` | 已不存在（2026-08-05 清理临时产物时移除，需验证时重建） |
| 巡检脚本 M6 判定 | WARN（提示人工核对，不阻断） |

## 二、待办步骤

### 步骤 1：创建 Slack Incoming Webhook（人工，约 5 分钟）

1. 打开 Slack → 目标工作区（如 `nzt47-security`）→ 管理后台
2. 选择接收 CI 告警的频道（建议建专用频道 `#ci-alerts`）
3. 创建 Incoming Webhook 应用，复制 Webhook URL（形如 `https://hooks.slack.com/services/T000/B000/XXXX`）

> 安全提示：Webhook URL 等同凭据，**不得**写入代码、README 或 commit message。

### 步骤 2：配置仓库 Secret（二选一）

```powershell
# 方式 A：GitHub CLI
gh secret set SLACK_WEBHOOK_URL --repo nzt47/security-tools --body "https://hooks.slack.com/services/..."

# 方式 B：网页操作
# GitHub → nzt47/security-tools → Settings → Secrets and variables → Actions
# → New repository secret → 名称 SLACK_WEBHOOK_URL → 值粘贴 Webhook URL
```

### 步骤 3：本地真实发送验证（可选但推荐）

构造一份 BLOCK 报告并真实发送，确认即时渠道可达：

```powershell
# 1. 重建模拟 BLOCK 报告（内容构造示例，按 verify_commit_origin.py 报告格式）
#    items 中放一条 status=BLOCK 的记录即可触发阻塞文案渲染

# 2. 真实发送（从环境变量读 webhook，不落盘）
$env:SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/..."
python scripts/slack_notify.py --json-file %TEMP%\mock_guard_report.json `
    --title "commit-origin-guard 阻断(验证)" `
    --repo "nzt47/security-tools"

# 预期：exit 0 且频道收到消息；若 exit 2 表示 webhook 缺失/格式错误
```

### 步骤 4：确认 M6 变 pass

```powershell
python scripts/maintenance_check.py --quiet
# 预期输出: PASS 通过 7/7 (BLOCK 0 / WARN 0)   ← M6 不再是 WARN
```

### 步骤 5：后续演进（灰度阶段关联）

- 目前 `GUARD_MODE=dry-run`，Slack 通知仅在 enforce 模式的实际阻断时触发
- 切 `enforce` 前建议先完成步骤 1-3，确保阻断发生时能即时告警

## 三、验收标准

- [ ] `gh secret list --repo nzt47/security-tools` 可见 `SLACK_WEBHOOK_URL`
- [ ] 步骤 3 真实发送成功（频道收到测试消息）
- [ ] `maintenance_check.py` M6 项变 `pass`
- [ ] 模拟 enforce 阻断场景，workflow 失败时 Slack 收到通知（可选，需构造真实 BLOCK push）

## 四、相关文件与命令

| 文件/命令 | 说明 |
|-----------|------|
| `.github/workflows/guard-master-commit-origin.yml` | 通知步骤定义（152-171 行） |
| `scripts/slack_notify.py` | 发送脚本（`--dry-run` 可本地模拟） |
| `gh secret list --repo nzt47/security-tools` | 查看已配置 secrets |
| `python scripts/maintenance_check.py --quiet` | M6 状态复查 |

## 五、回滚 / 移除方式

- 误配置后移除：`gh secret delete SLACK_WEBHOOK_URL --repo nzt47/security-tools`
- 通知步骤在无 secret 时自动跳过，移除后不产生任何失败噪音
