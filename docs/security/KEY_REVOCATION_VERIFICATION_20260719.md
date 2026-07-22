# DeepSeek + OpenAI 密钥撤销验证检查清单

> **文档日期**：2026-07-19
> **安全等级**：P0（紧急）
> **关联文档**：
> - [DEEPSEEK_KEY_REVOKE_GUIDE.md](./DEEPSEEK_KEY_REVOKE_GUIDE.md)
> - [BFG_CLEANUP_REPORT_20260719.md](./BFG_CLEANUP_REPORT_20260719.md)
> - 自动化脚本：[scripts/verify_key_revocation.ps1](../../scripts/verify_key_revocation.ps1)

---

## 一、验证流程总览

```
┌─────────────────────────────────────────────────────────────┐
│  阶段 1：准备旧 key（用于验证已撤销）                          │
│  ├─ 从备份/密码管理器获取旧 key                              │
│  └─ 保存到 $env:TEMP\deepseek_old_key.txt                  │
├─────────────────────────────────────────────────────────────┤
│  阶段 2：平台手动验证（DeepSeek + OpenAI 控制台）              │
│  ├─ DeepSeek 平台：旧 key 不在列表中                         │
│  └─ OpenAI 平台：旧 key 不在列表中                           │
├─────────────────────────────────────────────────────────────┤
│  阶段 3：自动化脚本验证（API 直测）                           │
│  ├─ 旧 key → 应返回 401 Unauthorized                       │
│  ├─ 新 key → 应返回正常响应                                 │
│  └─ .env 配置 → 已配置且非占位符                            │
├─────────────────────────────────────────────────────────────┤
│  阶段 4：应用端验证                                           │
│  ├─ /api/health 返回正常                                    │
│  └─ /api/news 返回翻译内容（无 401）                        │
├─────────────────────────────────────────────────────────────┤
│  阶段 5：git 历史验证                                         │
│  ├─ git log -S 完整 key → 0 commits                        │
│  └─ git grep 完整 key → 无输出                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、自动化脚本使用指南

### 2.1 脚本位置

```
c:\Users\Administrator\agent\scripts\verify_key_revocation.ps1
```

### 2.2 准备工作

```powershell
# 1. 将旧 key 保存到临时文件（用于验证已撤销）
# ⚠️ 警告：此文件在 $env:TEMP 中，不会进入 git
"sk-ddf2****45a3" | Out-File -FilePath "$env:TEMP\deepseek_old_key.txt" -Encoding UTF8 -NoNewline

# 2. 确认 .env 文件已配置新 key
notepad c:\Users\Administrator\agent\.env
# 应包含：
#   DEEPSEEK_API_KEY=sk-<新key>
#   OPENAI_API_KEY=sk-<新key>
#   LLM_API_KEY=sk-<新key>
```

### 2.3 执行完整验证

```powershell
# 完整验证（6 个阶段全部执行）
powershell -ExecutionPolicy Bypass -File c:\Users\Administrator\agent\scripts\verify_key_revocation.ps1
```

### 2.4 部分验证（按需跳过）

```powershell
# 仅验证旧 key 已撤销（跳过新 key 和应用端测试）
powershell -ExecutionPolicy Bypass -File c:\Users\Administrator\agent\scripts\verify_key_revocation.ps1 -SkipNewKeyTest -SkipAppTest

# 仅验证 .env 配置和 git 历史（不调用 API）
powershell -ExecutionPolicy Bypass -File c:\Users\Administrator\agent\scripts\verify_key_revocation.ps1 -SkipOldKeyTest -SkipNewKeyTest -SkipAppTest
```

### 2.5 脚本输出示例

```
========== 阶段 3：验证旧 key 已撤销 ==========
  测试旧 DeepSeek key: sk-d****45a3 ...[PASS] 旧 DeepSeek key 已正确撤销（返回 401 Unauthorized）
  测试旧 key 对 OpenAI 端点...[PASS] 旧 key 在 OpenAI 端点已正确撤销（返回 401）

========== 阶段 4：验证新 key 可用 ==========
  测试新 DeepSeek key: sk-a****8b3c ...[PASS] 新 DeepSeek key 有效（返回: Hello）
  测试新 OpenAI key: sk-p****9d2e ...[PASS] 新 OpenAI key 有效（返回: Hi）

=== 验证汇总 ===
通过: 12
失败: 0
跳过: 0
结论: ✅ 全部通过
```

---

## 三、手动验证检查清单

### 3.1 DeepSeek 平台验证

| # | 验证项 | 操作步骤 | 预期结果 | 状态 |
|---|-------|---------|---------|------|
| 1 | 登录 DeepSeek 控制台 | 访问 https://platform.deepseek.com/api_keys | 成功登录 | ☐ |
| 2 | 旧 key 不在列表中 | 在 API Keys 页面查找 `sk-ddf2****45a3` | 列表中无此 key | ☐ |
| 3 | 新 key 已创建 | 确认有新创建的 key（描述含 `security-tools-prod-20260719`） | 新 key 存在 | ☐ |
| 4 | 旧 key 直测返回 401 | 用旧 key 调用 API（见 3.3 节命令） | 返回 401 Unauthorized | ☐ |
| 5 | 新 key 直测成功 | 用新 key 调用 API（见 3.4 节命令） | 返回正常响应 | ☐ |

### 3.2 OpenAI 平台验证

| # | 验证项 | 操作步骤 | 预期结果 | 状态 |
|---|-------|---------|---------|------|
| 1 | 登录 OpenAI 控制台 | 访问 https://platform.openai.com/api-keys | 成功登录 | ☐ |
| 2 | 旧 key 不在列表中 | 在 API Keys 页面查找 `sk-ddf2****45a3` | 列表中无此 key | ☐ |
| 3 | 新 key 已创建 | 确认有新创建的 key（命名含 `security-tools-openai-20260719`） | 新 key 存在 | ☐ |
| 4 | 旧 key 直测返回 401 | 用旧 key 调用 OpenAI API | 返回 401 Unauthorized | ☐ |
| 5 | 新 key 直测成功 | 用新 key 调用 OpenAI API | 返回正常响应 | ☐ |

### 3.3 旧 key 直测命令（应返回 401）

```powershell
# DeepSeek 旧 key 测试（应返回 401）
$oldKey = Get-Content "$env:TEMP\deepseek_old_key.txt" -Encoding UTF8
$body = @{
    model = "deepseek-chat"
    messages = @(@{ role = "user"; content = "ping" })
    max_tokens = 5
} | ConvertTo-Json -Depth 5

try {
    $response = Invoke-RestMethod -Uri "https://api.deepseek.com/chat/completions" `
        -Method Post `
        -Headers @{ "Authorization" = "Bearer $oldKey"; "Content-Type" = "application/json" } `
        -Body $body
    Write-Host "❌ 旧 key 仍然有效（未撤销！）" -ForegroundColor Red
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -eq 401) {
        Write-Host "✅ 旧 key 已正确撤销（401 Unauthorized）" -ForegroundColor Green
    } else {
        Write-Host "⚠️ 返回状态码: $statusCode" -ForegroundColor Yellow
    }
}
```

### 3.4 新 key 直测命令（应返回正常）

```powershell
# DeepSeek 新 key 测试
$newKey = (Select-String -Path c:\Users\Administrator\agent\.env -Pattern "^DEEPSEEK_API_KEY=" | ForEach-Object { $_.Line -split "=", 2 } | Select-Object -Last 1).Trim()
$body = @{
    model = "deepseek-chat"
    messages = @(@{ role = "user"; content = "ping" })
    max_tokens = 5
} | ConvertTo-Json -Depth 5

try {
    $response = Invoke-RestMethod -Uri "https://api.deepseek.com/chat/completions" `
        -Method Post `
        -Headers @{ "Authorization" = "Bearer $newKey"; "Content-Type" = "application/json" } `
        -Body $body
    Write-Host "✅ 新 key 有效，返回: $($response.choices[0].message.content)" -ForegroundColor Green
} catch {
    Write-Host "❌ 新 key 测试失败: $($_.Exception.Message)" -ForegroundColor Red
}
```

### 3.5 OpenAI 新旧 key 测试

```powershell
# OpenAI 旧 key 测试（应返回 401）
$oldKey = Get-Content "$env:TEMP\deepseek_old_key.txt" -Encoding UTF8
$openaiBody = @{
    model = "gpt-4o-mini"
    messages = @(@{ role = "user"; content = "ping" })
    max_tokens = 5
} | ConvertTo-Json -Depth 5

try {
    $response = Invoke-RestMethod -Uri "https://api.openai.com/v1/chat/completions" `
        -Method Post `
        -Headers @{ "Authorization" = "Bearer $oldKey"; "Content-Type" = "application/json" } `
        -Body $openaiBody
    Write-Host "❌ 旧 key 在 OpenAI 仍然有效（未撤销！）" -ForegroundColor Red
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -eq 401) {
        Write-Host "✅ 旧 key 在 OpenAI 已正确撤销（401）" -ForegroundColor Green
    }
}

# OpenAI 新 key 测试
$newOpenAiKey = (Select-String -Path c:\Users\Administrator\agent\.env -Pattern "^OPENAI_API_KEY=" | ForEach-Object { $_.Line -split "=", 2 } | Select-Object -Last 1).Trim()
try {
    $response = Invoke-RestMethod -Uri "https://api.openai.com/v1/chat/completions" `
        -Method Post `
        -Headers @{ "Authorization" = "Bearer $newOpenAiKey"; "Content-Type" = "application/json" } `
        -Body $openaiBody
    Write-Host "✅ 新 OpenAI key 有效，返回: $($response.choices[0].message.content)" -ForegroundColor Green
} catch {
    Write-Host "❌ 新 OpenAI key 测试失败: $($_.Exception.Message)" -ForegroundColor Red
}
```

---

## 四、应用端验证

### 4.1 启动应用

```powershell
cd c:\Users\Administrator\agent
python app_server.py
```

### 4.2 验证 .env 加载

```powershell
# 验证环境变量已正确加载
python -c "import os; from dotenv import load_dotenv; load_dotenv(); k = os.environ.get('DEEPSEEK_API_KEY', ''); print(f'DeepSeek: {k[:8]}...{k[-4:]}' if k else 'DeepSeek: NOT loaded'); k2 = os.environ.get('OPENAI_API_KEY', ''); print(f'OpenAI: {k2[:8]}...{k2[-4:]}' if k2 else 'OpenAI: NOT loaded')"
```

### 4.3 验证 API 接口

```powershell
# 1. 健康检查（应返回 200）
Invoke-RestMethod -Uri "http://localhost:5678/api/health" -Method Get

# 2. /api/news 接口（依赖 DeepSeek key，应返回翻译内容）
Invoke-RestMethod -Uri "http://localhost:5678/api/news?topic=test&max=1" -Method Get

# 3. /api/skills-mgmt/health（不依赖 key，应返回正常）
Invoke-RestMethod -Uri "http://localhost:5678/api/skills-mgmt/health" -Method Get
```

### 4.4 验证日志无 key 泄露

```powershell
# 检查应用日志中是否打印了完整 key（应无输出）
Select-String -Path "c:\Users\Administrator\agent\logs\*.log" -Pattern "sk-ddf2****45a3" -ErrorAction SilentlyContinue
```

---

## 五、git 历史验证

### 5.1 验证远程历史已清洁

```powershell
cd c:\Users\Administrator\agent

# 1. 拉取最新远程历史
git fetch origin

# 2. 检查远程历史中是否还有完整 key（应返回 0）
git log -S "sk-ddf2****45a3" --oneline origin

# 3. 检查远程历史中是否还有 GlitchTip 密码（应返回 0）
git log -S "Admin@****!" --oneline origin

# 4. 检查 .encryption_key 文件是否在远程历史中（应返回 0）
git log --all --diff-filter=A -- '.encryption_key' --oneline origin
```

### 5.2 验证工作区文件无硬编码

```powershell
# 检查工作区文件中是否还有完整 key（应无输出）
git grep -n "sk-ddf2****45a3"

# 检查 GlitchTip 密码是否仍在工作区文件中
git grep -n "Admin@****!"
# 预期：docker/glitchtip/orm_setup_inline.py 可能仍有（P1 待修复）
```

---

## 六、完整验证脚本一键执行

```powershell
# ============================================================================
# 一键执行完整验证流程
# ============================================================================

# 1. 准备旧 key（仅首次执行需要）
if (-not (Test-Path "$env:TEMP\deepseek_old_key.txt")) {
    Write-Host "请输入旧 DeepSeek key（用于验证已撤销）：" -ForegroundColor Yellow
    $oldKey = Read-Host
    $oldKey | Out-File -FilePath "$env:TEMP\deepseek_old_key.txt" -Encoding UTF8 -NoNewline
    Write-Host "[OK] 旧 key 已保存到 $env:TEMP\deepseek_old_key.txt" -ForegroundColor Green
}

# 2. 执行验证脚本
powershell -ExecutionPolicy Bypass -File c:\Users\Administrator\agent\scripts\verify_key_revocation.ps1

# 3. 查看报告
$latestReport = Get-ChildItem "$env:TEMP\key_revocation_report_*.txt" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host "`n最新报告: $($latestReport.FullName)" -ForegroundColor Cyan
notepad $latestReport.FullName
```

---

## 七、验证结果汇总表

| # | 验证类别 | 验证项 | 预期 | 实际 | 状态 |
|---|---------|-------|------|------|------|
| 1 | DeepSeek 平台 | 旧 key 已删除 | 列表中无 | | ☐ |
| 2 | DeepSeek 平台 | 新 key 已创建 | 列表中有 | | ☐ |
| 3 | OpenAI 平台 | 旧 key 已删除 | 列表中无 | | ☐ |
| 4 | OpenAI 平台 | 新 key 已创建 | 列表中有 | | ☐ |
| 5 | API 直测 | 旧 DeepSeek key 返回 401 | 401 | | ☐ |
| 6 | API 直测 | 旧 OpenAI key 返回 401 | 401 | | ☐ |
| 7 | API 直测 | 新 DeepSeek key 有效 | 200 + 响应 | | ☐ |
| 8 | API 直测 | 新 OpenAI key 有效 | 200 + 响应 | | ☐ |
| 9 | .env 配置 | DEEPSEEK_API_KEY 已配置 | 非占位符 | | ☐ |
| 10 | .env 配置 | OPENAI_API_KEY 已配置 | 非占位符 | | ☐ |
| 11 | 应用端 | /api/health 正常 | 200 | | ☐ |
| 12 | 应用端 | /api/news 正常 | 200 + 翻译 | | ☐ |
| 13 | git 历史 | 远程无完整 key | 0 commits | | ☐ |
| 14 | git 历史 | 工作区无完整 key | 无输出 | | ☐ |
| 15 | 日志 | 应用日志无完整 key | 无输出 | | ☐ |

---

## 八、失败处理

### 8.1 旧 key 仍然有效（未撤销）

| 情况 | 处理措施 |
|------|---------|
| DeepSeek 平台旧 key 仍可调用 | 立即登录 https://platform.deepseek.com/api_keys 删除该 key |
| OpenAI 平台旧 key 仍可调用 | 立即登录 https://platform.openai.com/api-keys 撤销该 key |

### 8.2 新 key 测试失败

| 情况 | 处理措施 |
|------|---------|
| 新 key 返回 401 | key 可能输入错误，重新从平台复制 |
| 新 key 返回 429 | 请求频率超限，等待后重试 |
| 新 key 返回 403 | 账户可能未开通相应模型权限 |

### 8.3 应用端测试失败

| 情况 | 处理措施 |
|------|---------|
| /api/news 返回 500 | 检查 .env 是否正确加载，检查应用日志 |
| /api/news 返回降级内容 | DeepSeek key 未配置或无效，检查 .env |
| 应用启动失败 | 检查 .env 格式，确保无 BOM 头 |

---

## 九、脚本安全设计说明

### 9.1 为什么脚本不含硬编码 key？

| 设计决策 | 原因 |
|---------|------|
| 旧 key 从临时文件读取 | 避免将旧 key 写入脚本文件（会被 git 跟踪） |
| 新 key 从 .env 读取 | .env 已被 .gitignore 忽略，安全 |
| 脚本可安全提交 | 不含任何敏感信息，可进入版本控制 |
| 临时文件在 `$env:TEMP` | 不会被 git 跟踪，重启后自动清理 |

### 9.2 脚本参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-OldKeyFile` | `$env:TEMP\deepseek_old_key.txt` | 旧 key 文件路径 |
| `-EnvFile` | `c:\Users\Administrator\agent\.env` | .env 文件路径 |
| `-DeepSeekBaseUrl` | `https://api.deepseek.com/chat/completions` | DeepSeek API 端点 |
| `-OpenAiBaseUrl` | `https://api.openai.com/v1/chat/completions` | OpenAI API 端点 |
| `-SkipOldKeyTest` | false | 跳过旧 key 撤销测试 |
| `-SkipNewKeyTest` | false | 跳过新 key 可用性测试 |
| `-SkipAppTest` | false | 跳过应用端测试 |

---

## 十、三义校验

- **【不易】** 覆盖 15 项验证（平台 4 + API 直测 4 + .env 2 + 应用端 2 + git 历史 2 + 日志 1）；脚本不含硬编码 key，可安全提交；旧 key 从临时文件读取避免二次泄露
- **【变易】** 支持完整验证 + 部分验证（3 个 Skip 参数）；支持自动化脚本 + 手动检查清单双路径；支持 DeepSeek + OpenAI 双平台
- **【简易】** 一键执行命令；验证结果表格化跟踪；失败处理表格化指导

---

## 十一、参考文档

- [DeepSeek API 官方文档](https://platform.deepseek.com/api-docs/)
- [OpenAI API Keys 管理](https://platform.openai.com/api-keys)
- 内部文档：[DEEPSEEK_KEY_REVOKE_GUIDE.md](./DEEPSEEK_KEY_REVOKE_GUIDE.md)
- 内部文档：[BFG_CLEANUP_REPORT_20260719.md](./BFG_CLEANUP_REPORT_20260719.md)
- 自动化脚本：[scripts/verify_key_revocation.ps1](../../scripts/verify_key_revocation.ps1)

---

> **文档生成时间**：2026-07-19
> **执行人**：Yi-Jing Coding Agent
> **审核状态**：待用户审核
