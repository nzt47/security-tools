# CI run 未创建场景排查清单

> **触发场景**：`unblock_ci_and_trigger_dashboard.ps1` 脚本 Step 4 空推送兜底后，
> `gh api .../runs?head_sha=<sha>` 仍返回 0 个 run
> **来源**：2026-07-29 看板自动化验证过程中，a78324d8 推送后未触发 CI run
> **前置文档**：[`ci_dashboard_update_failure_runbook.md`](file:///c:/Users/Administrator/agent/docs/troubleshooting/ci_dashboard_update_failure_runbook.md)

---

## 一、快速诊断（按顺序执行，3 分钟内定位）

### 1.1 验证 push 确实到达远程

```bash
# 本地 HEAD
git rev-parse HEAD
# 远程 master HEAD（应与本地一致）
git ls-remote origin master
```

**判定**：
- ✅ SHA 一致 → push 成功，问题在 GitHub 侧
- ❌ SHA 不一致 → push 未到达，检查 `git remote -v` + 网络连接

### 1.2 检查 workflow 启用状态

```bash
# 列出所有 workflows 及状态
gh api "repos/nzt47/security-tools/actions/workflows" \
  --jq '.workflows[] | "\(.id) | \(.name) | \(.state)"'
```

**判定**：
- ✅ `云枢系统测试流程` state = `active` → workflow 已启用
- ❌ state = `disabled_manually` / `disabled_inactivity` → 手动启用：
  ```bash
  gh workflow enable <workflow-id>
  ```

### 1.3 检查 ci.yml 在推送的 commit 中是否有效

```bash
# 检查远程 master 的 ci.yml 语法
gh api "repos/nzt47/security-tools/contents/.github/workflows/ci.yml?ref=master" \
  --jq '.content' | base64 -d | python -c "import yaml,sys; yaml.safe_load(sys.stdin); print('YAML OK')"
```

**判定**：
- ✅ `YAML OK` → 语法正确
- ❌ 报错 → ci.yml 有语法错误，GitHub 拒绝触发。本地修复：
  ```bash
  python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml', encoding='utf-8'))"
  ```

### 1.4 检查触发条件匹配

```bash
# 查看推送 commit 中的 ci.yml on.push.branches 配置
gh api "repos/nzt47/security-tools/contents/.github/workflows/ci.yml?ref=master" \
  --jq '.content' | base64 -d | grep -A 10 "^on:"
```

**判定**：
- ✅ `branches:` 列表含 `master` → 触发条件匹配
- ❌ 不含 `master` → 修正 ci.yml 的 `on.push.branches`

---

## 二、深度排查（快速诊断未解决时）

### 2.1 检查 GitHub Actions 配额

```bash
# 查看当前用户的 Actions 使用情况（需浏览器）
echo "打开: https://github.com/settings/billing/summary"
echo "检查: Actions 行的 used/included 数值"
```

**判定**：
- ✅ used < included → 配额充足
- ❌ used ≥ included → 配额耗尽，等待月初重置或升级 plan

**注意**：公开仓库 Actions minutes 通常 unlimited，但私有仓库有配额。确认仓库可见性：
```bash
gh repo view nzt47/security-tools --json visibility --jq '.visibility'
```

### 2.2 检查仓库 Actions 权限

```bash
# 查看仓库 Actions 设置
gh api "repos/nzt47/security-tools/actions/permissions" --jq '.'
```

**判定**：
- ✅ `enabled: true` → Actions 已启用
- ❌ `enabled: false` → 在 GitHub UI 开启：
  - Settings → Actions → General → Allow all actions
- ⚠️ `allowed_actions: selected` → 检查白名单是否限制

### 2.3 检查同 workflow 同分支的 in_progress run 阻塞

```bash
# 查询同分支所有 in_progress 的 run
gh api "repos/nzt47/security-tools/actions/runs?branch=master&status=in_progress" \
  --jq '.workflow_runs[] | "\(.id) | \(.head_sha[0:7]) | \(.name) | \(.created_at)"'
```

**判定**：
- ✅ 返回空 → 无阻塞 run
- ❌ 返回非空 → 存在 in_progress run 可能阻塞。取消它们：
  ```bash
  # 逐个取消（替换 <run-id>）
  gh run cancel <run-id>
  ```

**这是 2026-07-29 的实际根因**：9c53ae88 的 in_progress run 阻塞了 a78324d8 触发。

### 2.4 检查 GitHub 服务状态

```bash
# 查看 GitHub Status 页面
echo "打开: https://www.githubstatus.com/"
echo "检查: Actions 行是否为 Operational"
```

**判定**：
- ✅ Operational → 服务正常
- ❌ Degraded Performance / Outage → 等待 GitHub 修复

### 2.5 检查 push 事件是否被 GitHub 接收

```bash
# 查看 push 事件（GitHub 记录的最近事件）
gh api "repos/nzt47/security-tools/events?per_page=10" \
  --jq '.[] | select(.type == "PushEvent") | "\(.id) | \(.payload.head[0:7]) | \(.created_at)"'
```

**判定**：
- ✅ 包含你的 push SHA → GitHub 接收了事件，但未触发 workflow
- ❌ 不包含 → push 事件丢失，重新 push

---

## 三、手动触发（绕过 push 触发）

如果以上排查均未解决，尝试手动触发：

### 3.1 workflow_dispatch（需 ci.yml 配置触发器）

```bash
# 检查 ci.yml 是否有 workflow_dispatch 触发器
gh api "repos/nzt47/security-tools/contents/.github/workflows/ci.yml?ref=master" \
  --jq '.content' | base64 -d | grep "workflow_dispatch"
```

**若有 workflow_dispatch**：
```bash
gh workflow run ci.yml --ref master
```

**若无 workflow_dispatch**（当前 ci.yml 未配置）：
在 ci.yml 的 `on:` 下添加：
```yaml
on:
  push:
    branches: [main, master, develop, 'release/**']
  pull_request:
    branches: [main, master, develop]
  schedule:
    - cron: '0 2 * * *'
  workflow_dispatch:  # 新增：支持手动触发
```
提交推送后即可用 `gh workflow run`。

### 3.2 重新推送（强制刷新）

```bash
# 方法 A: 修改空格触发（amend 触发新 push）
git commit --amend --no-edit
git push origin master --force-with-lease

# 方法 B: 删除远程分支重建（极端情况，谨慎）
# 不推荐，会丢失分支保护设置
```

---

## 四、根因记录模板

定位根因后，填写本表追加到 `docs/troubleshooting/ci_dashboard_update_failure_runbook.md` 第七节：

```markdown
| 日期 | commit | 根因 | 解决方案 | 验证 |
|------|--------|------|----------|------|
| 2026-07-29 | a78324d8 | <填入根因> | <填入方案> | <填入验证结果> |
```

---

## 五、诊断命令速查

```bash
# 一键诊断（复制执行）
echo "=== 1. push 验证 ==="
git rev-parse HEAD
git ls-remote origin master

echo "=== 2. workflow 状态 ==="
gh api "repos/nzt47/security-tools/actions/workflows" --jq '.workflows[] | "\(.id) | \(.name) | \(.state)"'

echo "=== 3. ci.yml 语法 ==="
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml', encoding='utf-8')); print('YAML OK')"

echo "=== 4. in_progress run 阻塞检查 ==="
gh api "repos/nzt47/security-tools/actions/runs?branch=master&status=in_progress" --jq '.workflow_runs | length'

echo "=== 5. push 事件记录 ==="
gh api "repos/nzt47/security-tools/events?per_page=5" --jq '.[] | select(.type=="PushEvent") | "\(.payload.head[0:7]) | \(.created_at)"'

echo "=== 6. 仓库可见性 + Actions 权限 ==="
gh repo view nzt47/security-tools --json visibility --jq '.visibility'
gh api "repos/nzt47/security-tools/actions/permissions" --jq '.enabled'
```

---

## 六、相关文档

- 看板更新失败排查：[`ci_dashboard_update_failure_runbook.md`](file:///c:/Users/Administrator/agent/docs/troubleshooting/ci_dashboard_update_failure_runbook.md)
- 解除阻塞脚本：[`scripts/unblock_ci_and_trigger_dashboard.ps1`](file:///c:/Users/Administrator/agent/scripts/unblock_ci_and_trigger_dashboard.ps1)
- 看板模板：[`docs/dashboards/ci_health_dashboard.md`](file:///c:/Users/Administrator/agent/docs/dashboards/ci_health_dashboard.md)
