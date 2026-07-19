# DeepSeek API Key 撤销与轮换操作指南

> **文档日期**：2026-07-19
> **事件等级**：P0（紧急）
> **涉及 Key**：`sk-ddf2****45a3`（完整值见 `agent/data/network_config.json` line 5）
> **泄露范围**：git 历史 11 个 commit + `app_server.py` 硬编码 + `network_config.json` + `SECURITY_NOTICE` 文档
> **关联文档**：
> - [BFG_CLEANUP_GUIDE_20260719.md](./BFG_CLEANUP_GUIDE_20260719.md) — git 历史清理操作指南
> - [SUMMARY_TEST_FIX_20260719.md](./SUMMARY_TEST_FIX_20260719.md) — 敏感信息扫描结果

---

## 一、不变量【不易】守护

1. **撤销优先于清理**：必须先在 DeepSeek 平台撤销 key，再清理 git 历史。撤销后即使历史中残留 key 也已失效。
2. **轮换与撤销分离**：撤销旧 key 与申请新 key 是两个独立操作，禁止在同一会话中混淆。
3. **新 key 仅落地 `.env`**：根据用户配置管理约束，所有敏感配置必须存入 `.env`，其他文件通过环境变量引用。
4. **本地配置文件 `network_config.json` 同步更新**：撤销后该文件中的旧 key 必须替换为新 key，否则运行时调用会失败。

---

## 二、撤销前准备（5 分钟）

### 2.1 确认泄露范围

```powershell
# 在仓库根目录执行
cd c:\Users\Administrator\agent

# 列出所有含旧 key 的历史 commit
git log -S "sk-ddf2****45a3" --oneline --all

# 检查工作区是否还有残留
git grep -n "sk-ddf2****45a3" -- ':!docs/security/SECURITY_NOTICE*'
```

预期输出：11 个 commit（详见 SUMMARY 报告 4.5 节）。

### 2.2 评估当前 key 使用情况

```powershell
# 确认 app_server.py 已改为环境变量读取（应输出空，表示无硬编码）
git grep -n "sk-ddf2" app_server.py

# 检查 .env 是否已有新 key
if (Test-Path .env) {
    Select-String -Path .env -Pattern "^DEEPSEEK_API_KEY=" -Quiet
}
```

- 第 1 条命令应无输出（已修复）
- 第 2 条命令应返回 `False`（尚未配置新 key）

### 2.3 备份当前 key（用于核对）

```powershell
# 将完整 key 记录到临时文件（仅本地，不提交）
"sk-ddf2****45a3" | Out-File -FilePath $env:TEMP\deepseek_old_key.txt -Encoding UTF8 -NoNewline
Write-Host "旧 key 已备份到 $env:TEMP\deepseek_old_key.txt（用于撤销时核对）"
```

---

## 三、DeepSeek 平台撤销步骤（P0，立即执行）

### 3.1 登录 DeepSeek 控制台

1. 访问 https://platform.deepseek.com/api_keys
2. 使用注册账号登录
3. 进入 **API Keys** 管理页面

### 3.2 定位并撤销旧 Key

1. 在 API Keys 列表中查找以 `sk-ddf2` 开头、以 `45a3` 结尾的 key
2. 点击该 key 右侧的 **Delete**（删除）按钮
3. 在确认弹窗中点击 **Delete** 确认
4. 验证：列表中该 key 已消失

> ⚠️ **警告**：DeepSeek 平台的"删除"操作等价于"撤销"（revoke），删除后该 key 立即失效，无法恢复。所有使用该 key 的请求将返回 `401 Unauthorized`。

### 3.3 申请新 Key

1. 在同一页面点击 **Create API Key** 按钮
2. 填写描述（建议：`security-tools-prod-20260719`）
3. 点击 **Create**
4. **立即复制**新 key（页面关闭后无法再次查看）
5. 将新 key 保存到安全位置（如密码管理器）

---

## 四、新 Key 落地配置（撤销后立即执行）

### 4.1 写入 `.env` 文件

```powershell
# 切换到仓库根目录
cd c:\Users\Administrator\agent

# 追加 DeepSeek 配置到 .env（若已存在 DEEPSEEK_API_KEY 行，需先删除）
if (Test-Path .env) {
    # 移除旧的 DEEPSEEK_API_KEY 行（避免重复）
    $existing = Get-Content .env -Encoding UTF8 | Where-Object { $_ -notmatch "^DEEPSEEK_API_KEY=" -and $_ -notmatch "^DEEPSEEK_BASE_URL=" }
    $existing | Set-Content .env -Encoding UTF8
}

# 追加新 key（请替换 sk-NEW_KEY_HERE 为实际新 key）
$newConfig = @"

# DeepSeek API 配置（2026-07-19 轮换后）
DEEPSEEK_API_KEY=sk-NEW_KEY_HERE
DEEPSEEK_BASE_URL=https://api.deepseek.com/chat/completions
"@
Add-Content -Path .env -Value $newConfig -Encoding UTF8

Write-Host "=== .env 已更新 DeepSeek 配置 ==="
Write-Host "请手动编辑 .env，将 sk-NEW_KEY_HERE 替换为实际新 key"
notepad .env
```

### 4.2 同步更新本地 `network_config.json`

```powershell
# 备份当前配置
Copy-Item agent\data\network_config.json agent\data\network_config.json.bak

# 替换旧 key 为新 key（请将 sk-NEW_KEY_HERE 替换为实际新 key）
$path = "agent\data\network_config.json"
$content = Get-Content $path -Raw -Encoding UTF8
$newContent = $content -replace "sk-ddf2****45a3", "sk-NEW_KEY_HERE"
$newContent | Set-Content $path -Encoding UTF8 -NoNewline

Write-Host "=== network_config.json 已更新 ==="
Write-Host "请手动核对：notepad agent\data\network_config.json"
```

> **注意**：`network_config.json` 已被 `.gitignore`（行 69），修改不会进入 git 历史。

### 4.3 验证新 Key 生效

```powershell
# 1. 验证环境变量已加载
cd c:\Users\Administrator\agent
python -c "import os; from dotenv import load_dotenv; load_dotenv(); k = os.environ.get('DEEPSEEK_API_KEY', ''); print(f'Key loaded: {k[:8]}...{k[-4:]}' if k else 'Key NOT loaded')"

# 2. 启动应用并测试 /api/news 接口
# 启动后访问：http://localhost:5678/api/news?topic=test&max=1
# 预期：返回翻译后的新闻内容，无 401 错误
```

---

## 五、DeepSeek API 调用验证（命令行直测）

```powershell
# 直接调用 DeepSeek API 验证新 key（请替换 sk-NEW_KEY_HERE）
$newKey = "sk-NEW_KEY_HERE"

$body = @{
    model = "deepseek-chat"
    messages = @(
        @{ role = "user"; content = "ping" }
    )
    max_tokens = 5
} | ConvertTo-Json -Depth 5

try {
    $response = Invoke-RestMethod -Uri "https://api.deepseek.com/chat/completions" `
        -Method Post `
        -Headers @{ "Authorization" = "Bearer $newKey"; "Content-Type" = "application/json" } `
        -Body $body
    Write-Host "✅ 新 key 验证成功" -ForegroundColor Green
    Write-Host "Response: $($response.choices[0].message.content)"
} catch {
    Write-Host "❌ 新 key 验证失败：$($_.Exception.Message)" -ForegroundColor Red
}
```

---

## 六、OpenAI 平台同步撤销（同一 key 复用场景）

> **背景**：`SECURITY_NOTICE` 文档显示该 key 同时在 OpenAI 控制台使用，需同步撤销。

### 6.1 登录 OpenAI 控制台

1. 访问 https://platform.openai.com/api-keys
2. 登录后进入 **API Keys** 页面

### 6.2 撤销旧 Key

1. 查找以 `sk-ddf2` 开头的 key
2. 点击该 key 右侧的 **Delete** 按钮
3. 确认删除

### 6.3 申请新 OpenAI Key（如需）

1. 点击 **Create new secret key**
2. 命名：`security-tools-openai-20260719`
3. 复制新 key 到 `.env` 的 `OPENAI_API_KEY` 和 `LLM_API_KEY` 字段

```powershell
# 更新 .env 中的 OpenAI 配置（请替换 sk-NEW_OPENAI_KEY）
$path = ".env"
$content = Get-Content $path -Raw -Encoding UTF8
$content = $content -replace "sk-your-openai-key", "sk-NEW_OPENAI_KEY"
$content = $content -replace "sk-your-api-key", "sk-NEW_OPENAI_KEY"
$content | Set-Content $path -Encoding UTF8 -NoNewline
Write-Host "=== .env OpenAI 配置已更新 ==="
```

---

## 七、撤销后核对清单

| # | 核对项 | 验证方法 | 状态 |
|---|---|---|---|
| 1 | DeepSeek 控制台旧 key 已删除 | 控制台列表无 `sk-ddf2****45a3` | ☐ |
| 2 | OpenAI 控制台旧 key 已删除 | 控制台列表无 `sk-ddf2****45a3` | ☐ |
| 3 | `.env` 中已配置新 DeepSeek key | `Select-String -Path .env -Pattern "^DEEPSEEK_API_KEY=sk-"` | ☐ |
| 4 | `network_config.json` 已更新新 key | JSON 中无 `sk-ddf2` 字符串 | ☐ |
| 5 | `app_server.py` line 961 已改为环境变量 | `git grep -n "sk-ddf2" app_server.py` 无输出 | ☐ ✅ |
| 6 | 应用启动后 `/api/news` 正常响应 | HTTP 200 + 翻译内容 | ☐ |
| 7 | 旧 key 直测返回 401 | `Invoke-RestMethod` 抛 401 异常 | ☐ |
| 8 | Git 历史清理（BFG）已完成 | 见 [BFG_CLEANUP_GUIDE](./BFG_CLEANUP_GUIDE_20260719.md) | ☐ |

---

## 八、风险评估与影响

### 8.1 撤销后受影响的功能

| 功能 | 影响 | 恢复条件 |
|---|---|---|
| `/api/news` 接口的 DeepSeek 翻译 | 翻译失败，接口返回降级内容 | `.env` 配置新 key 后恢复 |
| OpenAI 兼容调用（LLM 服务） | LLM 调用失败 | `.env` 配置新 OpenAI key 后恢复 |

### 8.2 撤销后不受影响的功能

- 所有非 LLM/翻译接口（`/api/skills-mgmt/*`、`/api/health` 等）
- 本地数据存储（SQLite、JSON 文件）
- 已运行的其他 LLM 实例（使用不同 key）

### 8.3 紧急回滚方案

若新 key 申请失败或应用无法启动：

```powershell
# 临时回滚到旧 key（仅限 DeepSeek 平台未真正删除前）
# ⚠️ 警告：此操作仅用于紧急回滚，DeepSeek 平台一旦删除 key 即无法恢复

# 1. 临时在 app_server.py 中硬编码（仅本地测试，禁止提交）
# _DS_KEY = "sk-ddf2****45a3"  # 临时回滚，禁止提交

# 2. 或使用环境变量临时设置
$env:DEEPSEEK_API_KEY = "sk-ddf2****45a3"
python app_server.py
```

> **注意**：一旦 DeepSeek 平台完成删除，旧 key 即永久失效，回滚无效。必须申请新 key。

---

## 九、时间线建议

| 时间 | 操作 | 责任人 |
|---|---|---|
| T+0 | 阅读本指南，确认泄露范围 | 用户 |
| T+5min | DeepSeek 平台撤销旧 key | 用户 |
| T+10min | DeepSeek 平台申请新 key | 用户 |
| T+15min | OpenAI 平台撤销旧 key | 用户 |
| T+20min | 申请新 OpenAI key（如需） | 用户 |
| T+25min | 更新 `.env` 和 `network_config.json` | 用户 |
| T+30min | 启动应用验证 `/api/news` | 用户 |
| T+40min | 执行 BFG 历史清理 | 见 [BFG 指南](./BFG_CLEANUP_GUIDE_20260719.md) |

---

## 十、三义校验

- **【不易】** 撤销优先于清理 / 新 key 仅落地 `.env` / `app_server.py` 已改为环境变量读取 —— 三类不变量全部守护
- **【变易】** 适配 DeepSeek + OpenAI 双平台 / 适配 `.env` + `network_config.json` 双配置点 / 支持回滚方案
- **【简易】** 按时间线线性执行，每步可独立验证，初级工程师 30s 可读

---

## 十一、参考文档

- [DeepSeek API 官方文档](https://platform.deepseek.com/api-docs/)
- [OpenAI API Keys 管理](https://platform.openai.com/api-keys)
- [BFG Repo-Cleaner](https://rtyley.github.io/bfg-repo-cleaner/)
- 内部文档：[BFG_CLEANUP_GUIDE_20260719.md](./BFG_CLEANUP_GUIDE_20260719.md)
- 内部文档：[SUMMARY_TEST_FIX_20260719.md](./SUMMARY_TEST_FIX_20260719.md)
- 内部文档：[CHANGELOG_TEST_FIX_AND_FILE_RECOVERY.md](./CHANGELOG_TEST_FIX_AND_FILE_RECOVERY.md)
