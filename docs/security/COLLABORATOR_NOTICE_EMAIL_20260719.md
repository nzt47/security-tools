# 协作者重新克隆仓库通知邮件 + 本地缓存清理指南

> **文档日期**：2026-07-19
> **安全等级**：P0（紧急）
> **关联文档**：[BFG_CLEANUP_REPORT_20260719.md](./BFG_CLEANUP_REPORT_20260719.md)

---

## 一、邮件模板（中文版）

### 1.1 邮件元信息

| 字段 | 内容 |
|------|------|
| **收件人** | 所有仓库协作者（git log --format='%ae' \| sort -u 提取） |
| **抄送** | 项目负责人 / 安全负责人 |
| **主题** | 【P0 安全紧急】security-tools 仓库历史已重写，请立即重新克隆 |
| **优先级** | 高 |
| **截止时间** | 收到邮件后 24 小时内完成 |

### 1.2 邮件正文（中文）

```text
各位协作者：

我们于 2026-07-19 对 security-tools 仓库（git@github.com:nzt47/security-tools.git）
执行了 git 历史重写（BFG 清理），原因是历史中存在敏感信息泄露：

  1. DeepSeek/OpenAI API key（sk-ddf2****45a3）— 11 个 commits
  2. GlitchTip 管理员密码（Admin@****!）— 3 个 commits
  3. .encryption_key 加密密钥文件 — 10 个 commits
  4. SECURITY_NOTICE 安全通知文档（含完整 key）— 22 个 commits

清理操作已完成并强制推送到 origin（GitHub）+ gitee（Gitee）双远程仓库。
所有 commit hash 已变更，旧 clone 中的历史仍含敏感数据。

【必须执行的操作】（24 小时内）

  步骤 1：备份当前工作区变更（如有未提交的工作）

    cd <你的仓库路径>
    git stash push -u -m "pre-reclone-backup-20260719"
    # 或导出 patch 文件
    git diff > ~/pre-reclone-changes.patch
    git status --short > ~/pre-reclone-filelist.txt

  步骤 2：删除旧仓库目录（含敏感历史的 .git）

    cd ..
    # Windows
    Remove-Item -Recurse -Force <你的仓库路径>
    # macOS/Linux
    rm -rf <你的仓库路径>

  步骤 3：重新克隆清洁版本

    git clone git@github.com:nzt47/security-tools.git
    # 或使用 gitee 镜像
    git clone git@gitee.com:nzt47/security-tools.git

  步骤 4：恢复工作区变更（如有备份）

    cd security-tools
    git stash pop  # 如果之前用了 stash
    # 或
    git apply ~/pre-reclone-changes.patch

  步骤 5：清理本地缓存（防止旧缓存被复用）

    # Python 缓存
    pip cache purge
    # Windows
    Remove-Item -Recurse -Force ~\AppData\Local\pip\Cache -ErrorAction SilentlyContinue
    # macOS/Linux
    rm -rf ~/.cache/pip

    # 虚拟环境（重新创建）
    Remove-Item -Recurse -Force .venv venv -ErrorAction SilentlyContinue
    python -m venv .venv
    # Windows
    .\.venv\Scripts\Activate.ps1
    # macOS/Linux
    source .venv/bin/activate
    pip install -r requirements.txt

  步骤 6：验证新克隆无敏感信息

    # 应无输出
    git log -S "sk-ddf2****45a3" --oneline --all
    git log -S "Admin@****!" --oneline --all
    git log --all --diff-filter=A -- '.encryption_key' --oneline

【已知影响】

  - 所有 commit hash 已变更，引用旧 hash 的文档/脚本需更新
  - 77 个旧 stashes 已被清除（仅影响本地仓库所有者）
  - master 分支保护已恢复，无法直接 force push

【不需要执行的操作】

  - 不需要重新申请 API key（key 已在平台撤销，新 key 通过 .env 配置）
  - 不需要修改代码（已改为环境变量读取）

【如有问题】

  - 查看完整清理报告：docs/BFG_CLEANUP_REPORT_20260719.md
  - 查看 key 撤销指南：docs/DEEPSEEK_KEY_REVOKE_GUIDE.md
  - 联系人：<项目负责人邮箱>

请确认完成后回复本邮件。

安全团队
2026-07-19
```

---

## 二、邮件模板（英文版）

### 2.1 Email Metadata

| Field | Value |
|-------|-------|
| **To** | All repository collaborators |
| **CC** | Project owner / Security team |
| **Subject** | [P0 SECURITY] security-tools repo history rewritten — re-clone required immediately |
| **Priority** | High |
| **Deadline** | Within 24 hours of receipt |

### 2.2 Email Body (English)

```text
Dear collaborators,

On 2026-07-19, we performed a git history rewrite (BFG cleanup) on the
security-tools repository (git@github.com:nzt47/security-tools.git) due to
sensitive data exposure in the commit history:

  1. DeepSeek/OpenAI API key (sk-ddf2****45a3) — 11 commits
  2. GlitchTip admin password (Admin@****!) — 3 commits
  3. .encryption_key file — 10 commits
  4. SECURITY_NOTICE document (contained full key) — 22 commits

The cleanup is complete and force-pushed to both origin (GitHub) and
gitee (Gitee). All commit hashes have changed. Your existing local clone
still contains the sensitive data in its history.

[REQUIRED ACTIONS] (within 24 hours)

  Step 1: Backup uncommitted changes (if any)

    cd <your-repo-path>
    git stash push -u -m "pre-reclone-backup-20260719"
    # OR export a patch file
    git diff > ~/pre-reclone-changes.patch
    git status --short > ~/pre-reclone-filelist.txt

  Step 2: Delete the old repository directory

    cd ..
    # Windows
    Remove-Item -Recurse -Force <your-repo-path>
    # macOS/Linux
    rm -rf <your-repo-path>

  Step 3: Re-clone the clean version

    git clone git@github.com:nzt47/security-tools.git
    # OR use the gitee mirror
    git clone git@gitee.com:nzt47/security-tools.git

  Step 4: Restore your changes (if backed up)

    cd security-tools
    git stash pop  # if you used stash
    # OR
    git apply ~/pre-reclone-changes.patch

  Step 5: Clear local caches

    # Python cache
    pip cache purge
    # Windows
    Remove-Item -Recurse -Force ~\AppData\Local\pip\Cache -ErrorAction SilentlyContinue
    # macOS/Linux
    rm -rf ~/.cache/pip

    # Recreate virtual environment
    Remove-Item -Recurse -Force .venv venv -ErrorAction SilentlyContinue
    python -m venv .venv
    # Windows
    .\.venv\Scripts\Activate.ps1
    # macOS/Linux
    source .venv/bin/activate
    pip install -r requirements.txt

  Step 6: Verify the new clone has no sensitive data

    # Should return nothing
    git log -S "sk-ddf2****45a3" --oneline --all
    git log -S "Admin@****!" --oneline --all
    git log --all --diff-filter=A -- '.encryption_key' --oneline

[KNOWN IMPACTS]

  - All commit hashes have changed; docs/scripts referencing old hashes need updating
  - 77 old stashes were dropped (affects only the repo owner)
  - master branch protection is restored; direct force-push is blocked

[NOT REQUIRED]

  - No need to re-apply for API keys (old keys revoked; new keys via .env)
  - No code changes needed (already switched to env var reading)

[IF YOU HAVE QUESTIONS]

  - Full cleanup report: docs/BFG_CLEANUP_REPORT_20260719.md
  - Key revocation guide: docs/DEEPSEEK_KEY_REVOKE_GUIDE.md
  - Contact: <project-owner-email>

Please reply to confirm once completed.

Security Team
2026-07-19
```

---

## 三、本地缓存清理命令（完整版）

### 3.1 Windows (PowerShell)

```powershell
# ============================================================================
# [security] 本地缓存清理脚本 — BFG 清理后执行
# 目的：清除可能含旧敏感数据的本地缓存
# ============================================================================

# 1. Python pip 缓存
Write-Host "[1/8] Clearing pip cache..." -ForegroundColor Cyan
pip cache purge 2>$null
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\pip\Cache" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$env:USERPROFILE\.cache\pip" -ErrorAction SilentlyContinue
Write-Host "[OK] pip cache cleared" -ForegroundColor Green

# 2. Python __pycache__ 目录
Write-Host "[2/8] Clearing __pycache__ directories..." -ForegroundColor Cyan
Get-ChildItem -Path . -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "[OK] __pycache__ cleared" -ForegroundColor Green

# 3. pytest 缓存
Write-Host "[3/8] Clearing pytest cache..." -ForegroundColor Cyan
Remove-Item -Recurse -Force ".pytest_cache" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force ".mypy_cache" -ErrorAction SilentlyContinue
Write-Host "[OK] pytest/mypy cache cleared" -ForegroundColor Green

# 4. 虚拟环境（重新创建）
Write-Host "[4/8] Removing virtual environments..." -ForegroundColor Cyan
Remove-Item -Recurse -Force ".venv" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "venv" -ErrorAction SilentlyContinue
Write-Host "[OK] Virtual environments removed (recreate with: python -m venv .venv)" -ForegroundColor Green

# 5. node_modules（如存在）
Write-Host "[5/8] Clearing node_modules..." -ForegroundColor Cyan
Remove-Item -Recurse -Force "node_modules" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "static\node_modules" -ErrorAction SilentlyContinue
Write-Host "[OK] node_modules cleared (recreate with: npm install)" -ForegroundColor Green

# 6. npm 缓存
Write-Host "[6/8] Clearing npm cache..." -ForegroundColor Cyan
npm cache clean --force 2>$null
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\npm-cache" -ErrorAction SilentlyContinue
Write-Host "[OK] npm cache cleared" -ForegroundColor Green

# 7. Docker 构建缓存（如使用 Docker）
Write-Host "[7/8] Clearing Docker build cache..." -ForegroundColor Cyan
docker builder prune -f 2>$null
Write-Host "[OK] Docker build cache cleared" -ForegroundColor Green

# 8. Git reflog + gc（清除本地旧对象）
Write-Host "[8/8] Cleaning git reflog and gc..." -ForegroundColor Cyan
git reflog expire --expire=now --all
git gc --prune=now
Write-Host "[OK] Git reflog cleared and gc completed" -ForegroundColor Green

Write-Host ""
Write-Host "=== All local caches cleared ===" -ForegroundColor Cyan
Write-Host "Next steps:"
Write-Host "  1. Recreate virtual env: python -m venv .venv; .\.venv\Scripts\Activate.ps1"
Write-Host "  2. Install deps: pip install -r requirements.txt"
Write-Host "  3. Verify clean history: git log -S 'sk-ddf2****45a3' --oneline --all"
```

### 3.2 macOS / Linux (Bash)

```bash
#!/bin/bash
# ============================================================================
# [security] Local cache cleanup script — post BFG cleanup
# ============================================================================

set -e

# 1. Python pip cache
echo "[1/8] Clearing pip cache..."
pip cache purge 2>/dev/null || true
rm -rf ~/.cache/pip
echo "[OK] pip cache cleared"

# 2. Python __pycache__ directories
echo "[2/8] Clearing __pycache__ directories..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
echo "[OK] __pycache__ cleared"

# 3. pytest cache
echo "[3/8] Clearing pytest cache..."
rm -rf .pytest_cache .mypy_cache
echo "[OK] pytest/mypy cache cleared"

# 4. Virtual environments
echo "[4/8] Removing virtual environments..."
rm -rf .venv venv
echo "[OK] Virtual environments removed (recreate with: python -m venv .venv)"

# 5. node_modules
echo "[5/8] Clearing node_modules..."
rm -rf node_modules static/node_modules
echo "[OK] node_modules cleared (recreate with: npm install)"

# 6. npm cache
echo "[6/8] Clearing npm cache..."
npm cache clean --force 2>/dev/null || true
rm -rf ~/.npm
echo "[OK] npm cache cleared"

# 7. Docker build cache
echo "[7/8] Clearing Docker build cache..."
docker builder prune -f 2>/dev/null || true
echo "[OK] Docker build cache cleared"

# 8. Git reflog + gc
echo "[8/8] Cleaning git reflog and gc..."
git reflog expire --expire=now --all
git gc --prune=now
echo "[OK] Git reflog cleared and gc completed"

echo ""
echo "=== All local caches cleared ==="
echo "Next steps:"
echo "  1. Recreate virtual env: python -m venv .venv && source .venv/bin/activate"
echo "  2. Install deps: pip install -r requirements.txt"
echo "  3. Verify clean history: git log -S 'sk-ddf2****45a3' --oneline --all"
```

---

## 四、协作者清单（用于跟踪）

| # | 协作者 | 邮箱 | 通知时间 | 确认重新克隆 | 确认缓存清理 | 确认验证通过 |
|---|--------|------|---------|------------|------------|------------|
| 1 | | | | ☐ | ☐ | ☐ |
| 2 | | | | ☐ | ☐ | ☐ |
| 3 | | | | ☐ | ☐ | ☐ |

> **填写说明**：从 `git log --format='%aN <%ae>' | sort -u` 提取协作者列表后填入。

---

## 五、提取协作者邮箱列表的命令

```powershell
# 提取所有曾经提交过代码的协作者邮箱
cd c:\Users\Administrator\agent
git log --all --format='%aN <%ae>' | Sort-Object -Unique

# 仅提取邮箱地址（用于邮件群发）
git log --all --format='%ae' | Sort-Object -Unique
```

```bash
# macOS/Linux 版本
git log --all --format='%aN <%ae>' | sort -u
git log --all --format='%ae' | sort -u
```

---

## 六、三义校验

- **【不易】** 邮件明确 P0 安全等级 + 24 小时截止时间 + 6 步必须执行操作 + 验证命令；本地缓存覆盖 8 类（pip/pycache/pytest/venv/npm/Docker/git reflog）
- **【变易】** 中英双语邮件模板适配国际化团队；Windows PowerShell + macOS/Linux Bash 双脚本；协作者清单可扩展
- **【简易】** 命令可直接复制执行；6 步操作线性无依赖；协作者清单表格化跟踪

---

> **文档生成时间**：2026-07-19
> **关联操作**：BFG 历史清理（详见 [BFG_CLEANUP_REPORT_20260719.md](./BFG_CLEANUP_REPORT_20260719.md)）
