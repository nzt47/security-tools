# BFG Repo-Cleaner 清除 Git 历史敏感信息操作指南

> **文档日期**：2026-07-19
> **操作类型**：破坏性（历史重写 + force push）
> **前置条件**：必须先完成 P0 操作（revoke 所有泄露的凭证）

---

## 一、敏感信息扫描结果

### 1.1 真实敏感信息清单（必须清除）

| # | 类型 | 位置 | 值（掩码） | git 跟踪 | 严重程度 |
|---|---|---|---|---|---|
| 1 | API Key（DeepSeek/OpenAI） | `app_server.py` line 961 | `sk-ddf2****45a3` | 跟踪 | 高 |
| 2 | API Key（同上） | `agent/data/network_config.json` line 5,45 | `sk-ddf2****45a3` | 已 gitignore（历史有） | 高 |
| 3 | 加密密钥文件 | `.encryption_key`（二进制） | 二进制内容 | 跟踪 | 高 |
| 4 | API Key 引用 | `docs/security/SECURITY_NOTICE_20260719_api_key_leak.md` line 29,106,138,204 | `sk-ddf2****45a3` | 跟踪 | 中 |
| 5 | GlitchTip 管理员密码 | `docker/glitchtip/orm_setup_inline.py` line 52 | `***REMOVED_GLITCHTIP_PWD***` | 跟踪 | 中 |
| 6 | Grafana 密码 | `scripts/_import_dashboards.py` line 8 | `admin123` | 跟踪 | 中 |

### 1.2 历史 commit 中 API key 出现记录（11 个）

```
0be54682 docs(security): 添加 OpenAI API key 泄露事件团队安全通知
669d66f4 feat(skills_mgmt): 重建记忆→技能自动抽象器
fadc48f6 fix(observability): 修复 3 个预存测试失败用例
b84da9ed fix(observability): 修复 prometheus _safe_gauge NameError
188d32b3 feat: 动态工具注册表 + 插件自动注册钩子
16f7fccb fix: 工具返回格式统一为 {ok, data/error}
a07332b2 fix: 优先级 Tavily>Firecrawl>DuckDuckGo>搜狗>360
e4c9da43 refactor: 仅保留 DuckDuckGo/搜狗/360
2d4ef586 refactor: 移除所有内置搜索引擎
cf3b1901 feat: 云枢计划任务与心跳系统完整集成
d249e64f security(chore): 脱敏 network_config.json（删除操作本身含 key）
```

### 1.3 已正确处理的文件（无需 BFG）

| 文件 | 状态 |
|---|---|
| `.env` | 本地存在，未跟踪，内容为空模板 |
| `.env.example` | 跟踪但仅含占位符 |
| `agent/data/network_config.json` | 已 gitignore（但历史 commit 仍含 key，需 BFG） |

### 1.4 测试文件中的样例凭证（非真实，无需处理）

- `memory/tests/test_llm_service.py` — `sk-ant-test`
- `memory/tests/test_risk_fixes.py` — `sk-valid-test-key-12345`
- `scripts/quick_test.py` — `password="MyPassword123"`（测试文本）
- `tests/unit/test_security_utils.py` — `BEGIN PRIVATE KEY`（测试样例）
- `tests/unit/test_code_review_additional.py` — `BEGIN PRIVATE KEY`（测试样例）
- `tests/integration/test_observability_security.py` — `AKIAIOSFODNN7EXAMPLE`（AWS 官方示例）

---

## 二、P0 前置操作（必须先完成）

BFG 只能清除 git 历史，无法撤销已发生的泄露。必须先在所有平台 revoke 凭证：

### 2.1 OpenAI 平台
1. 登录 https://platform.openai.com/api-keys
2. 找到以 `sk-ddf2****45a3` 开头的 key
3. 点击 Revoke 撤销
4. 生成新 key，更新到 `.env` 和 `app_server.py`

### 2.2 DeepSeek 平台
1. 登录 https://platform.deepseek.com/api_keys
2. 撤销相同 key（`_DS_KEY` 变量表明此 key 也用于 DeepSeek）
3. 生成新 key

### 2.3 GlitchTip（如使用）
1. 修改管理员密码（当前 `***REMOVED_GLITCHTIP_PWD***`）

### 2.4 Grafana（如使用）
1. 修改默认密码（当前 `admin123`）

---

## 三、BFG Repo-Cleaner 操作步骤

### 3.1 准备工作

```powershell
# 1. 安装 Java（BFG 依赖 Java 8+）
java -version

# 2. 下载 BFG Repo-Cleaner
#    官方地址: https://rtyley.github.io/bfg-repo-cleaner/
#    下载 bfg-x.y.z.jar 到 C:\Tools\bfg.jar
$bfgJar = "C:\Tools\bfg.jar"
if (-not (Test-Path $bfgJar)) {
    Write-Output "请先下载 bfg.jar 到 $bfgJar"
    exit 1
}

# 3. 通知所有协作者暂停推送（BFG 会重写历史，旧 clone 全部失效）
```

### 3.2 备份仓库（关键！）

```powershell
# 1. 完整备份当前仓库（含所有分支和 tag）
cd C:\Users\Administrator
Compress-Archive -Path .\agent -DestinationPath .\agent_backup_20260719_pre_bfg.zip -Force

# 2. 记录当前 HEAD commit（用于回滚）
git -C .\agent rev-parse HEAD
git -C .\agent log --oneline -5

# 3. 记录所有远程仓库地址
git -C .\agent remote -v
```

### 3.3 创建敏感信息替换文件

```powershell
# 创建 bfg-replacements.txt，每行一个替换规则
$rules = @'
# 完整 API key 替换为掩码
sk-ddf2****45a3==>sk-ddf2****45a3

# GlitchTip 密码
***REMOVED_GLITCHTIP_PWD***==>***REDACTED***

# Grafana 密码
"admin123"==>"***REDACTED***"
'@
[System.IO.File]::WriteAllText("C:\Users\Administrator\bfg-replacements.txt", $rules, [System.Text.UTF8Encoding]::new($false))
Write-Output "替换规则文件已创建: C:\Users\Administrator\bfg-replacements.txt"
```

### 3.4 执行 BFG 清理

```powershell
# 1. 镜像克隆仓库（BFG 要求 bare 仓库）
cd C:\Users\Administrator
git clone --mirror agent agent-mirror.git
cd agent-mirror.git

# 2. 用 BFG 替换文本内容
java -jar C:\Tools\bfg.jar --replace-text C:\Users\Administrator\bfg-replacements.txt

# 3. 用 BFG 删除 .encryption_key 文件（从所有历史 commit）
java -jar C:\Tools\bfg.jar --delete-files .encryption_key

# 4. 清理 git reflog 和 GC（BFG 不自动清理，必须手动执行）
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# 5. 验证清理结果（应无输出）
git log --all -p | Select-String "sk-ddf2****45a3"
git log --all -p | Select-String "***REMOVED_GLITCHTIP_PWD***"
git log --all -p | Select-String "admin123"
git log --all -- .encryption_key
```

### 3.5 强制推送到远程

```powershell
# 警告：此操作会覆盖远程历史，所有协作者必须重新 clone
# 备份完成后再执行！

cd C:\Users\Administrator\agent-mirror.git

# 推送到所有远程（origin / gitee / 其他）
git push --force --all
git push --force --tags

# 验证远程是否更新
git log --all -p | Select-String "sk-ddf2****45a3"
```

### 3.6 重新 clone 并验证

```powershell
# 1. 备份旧工作目录
Move-Item C:\Users\Administrator\agent C:\Users\Administrator\agent_old_pre_bfg

# 2. 重新 clone（替换 YOUR_REMOTE_URL 为实际地址）
cd C:\Users\Administrator
git clone YOUR_REMOTE_URL agent

# 3. 验证历史中无敏感信息
cd agent
git log --all -p | Select-String "sk-ddf2****45a3"  # 应无输出
git log --all -p | Select-String "***REMOVED_GLITCHTIP_PWD***"                          # 应无输出
git log --all -p | Select-String "admin123"                             # 应无输出
git log --all -- .encryption_key                                         # 应无输出

# 4. 恢复本地配置（从备份）
Copy-Item ..\agent_old_pre_bfg\.env .\.env
Copy-Item ..\agent_old_pre_bfg\agent\data\network_config.json .\agent\data\
Copy-Item ..\agent_old_pre_bfg\data\* .\data\ -Recurse -Force
```

---

## 四、BFG 后的代码修复（必做）

BFG 只清理历史，当前代码中的硬编码必须手动修复：

### 4.1 `app_server.py` line 961

```python
# 修复前（硬编码）:
_DS_KEY = "sk-ddf2****45a3"
_DS_URL = "https://api.deepseek.com/chat/completions"

# 修复后（从环境变量读取）:
import os
_DS_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
_DS_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions")
if not _DS_KEY:
    logger.warning("DEEPSEEK_API_KEY 未设置，DeepSeek 功能不可用")
```

### 4.2 `.encryption_key` 从 git 跟踪移除

```powershell
# 加入 .gitignore
Add-Content .gitignore "`n.encryption_key"

# 从 git 跟踪移除（本地保留）
git rm --cached .encryption_key
git commit -m "security: 取消跟踪 .encryption_key（加入 .gitignore）"
```

### 4.3 `docs/security/SECURITY_NOTICE_20260719_api_key_leak.md` 脱敏

```powershell
# 替换完整 key 为掩码
(Get-Content docs\security\SECURITY_NOTICE_20260719_api_key_leak.md -Raw) `
    -replace 'sk-ddf2****45a3', 'sk-ddf2****45a3' `
    | Set-Content docs\security\SECURITY_NOTICE_20260719_api_key_leak.md -NoNewline

git add docs/security/SECURITY_NOTICE_20260719_api_key_leak.md
git commit -m "security: 脱敏安全通知文档中的 API key 引用"
```

### 4.4 `docker/glitchtip/orm_setup_inline.py` line 52

```python
# 修复前:
password = "***REMOVED_GLITCHTIP_PWD***"

# 修复后:
import os
password = os.environ.get("GLITCHTIP_ADMIN_PASSWORD", "")
```

### 4.5 `scripts/_import_dashboards.py` line 8

```python
# 修复前:
GRAFANA_PASSWORD = "admin123"

# 修复后:
import os
GRAFANA_PASSWORD = os.environ.get("GRAFANA_PASSWORD", "")
```

### 4.6 提交所有修复

```powershell
git add app_server.py .gitignore docker/glitchtip/orm_setup_inline.py scripts/_import_dashboards.py
git commit -m "security: 移除硬编码凭证，改用环境变量

- app_server.py: _DS_KEY 改为读取 DEEPSEEK_API_KEY 环境变量
- .encryption_key: 加入 .gitignore，取消跟踪
- orm_setup_inline.py: GlitchTip 密码改为环境变量
- _import_dashboards.py: Grafana 密码改为环境变量"
```

---

## 五、验证清单

### 5.1 历史清理验证

```powershell
# 以下命令应全部无输出
git log --all -p | Select-String "sk-ddf2****45a3"
git log --all -p | Select-String "***REMOVED_GLITCHTIP_PWD***"
git log --all -p | Select-String '"admin123"'
git log --all -- .encryption_key
git log --all -- agent/data/network_config.json | Select-String "sk-"
```

### 5.2 当前代码验证

```powershell
# 以下命令应全部无输出（排除测试文件）
git ls-files | Where-Object { $_ -notmatch "^tests/|\.example$|\.md$" } | ForEach-Object {
    Select-String -Path "c:\Users\Administrator\agent\$_" -Pattern "sk-ddf2****45a3|***REMOVED_GLITCHTIP_PWD***" -ErrorAction SilentlyContinue
}
```

### 5.3 协作者通知模板

```
【紧急】仓库历史已重写，请重新 clone

由于清理了历史 commit 中的敏感信息（API key/密码），所有旧的 clone 已失效。

请执行：
1. 备份你的本地修改（如有）
2. 删除旧的工作目录: rm -rf agent
3. 重新 clone: git clone YOUR_REMOTE_URL agent
4. 恢复本地配置: cp .env.example .env (并填入新凭证)

原因: 历史 commit 中泄露了 OpenAI/DeepSeek API key 和管理员密码
影响: 旧 clone 中的 git 历史仍含敏感信息，必须删除
```

---

## 六、风险评估与回滚

### 6.1 风险

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| Force push 覆盖远程 | 所有协作者必须重新 clone | 提前通知，提供重新 clone 指南 |
| 历史重写失败 | 仓库损坏 | 完整备份（步骤 3.2） |
| BFG 漏清 | 敏感信息残留 | 步骤 5.1 验证清单 |
| CI/CD 失效 | 构建中断 | 更新 CI 配置中的 checkout 步骤 |

### 6.2 回滚方案

```powershell
# 如果 BFG 失败或验证不通过，从备份恢复
cd C:\Users\Administrator
Remove-Item agent -Recurse -Force
Expand-Archive -Path .\agent_backup_20260719_pre_bfg.zip -DestinationPath .

# 强制推送旧历史到远程（撤销 BFG）
cd agent
git push --force --all
git push --force --tags
```

---

## 七、时间线建议

| 步骤 | 耗时 | 负责人 | 状态 |
|---|---|---|---|
| P0: Revoke 所有泄露凭证 | 30 分钟 | 用户 | 待执行 |
| P1: 备份仓库 | 10 分钟 | 用户/运维 | 待执行 |
| P1: 通知协作者 | 即时 | 用户 | 待执行 |
| P1: 执行 BFG 清理 | 30 分钟 | 用户/运维 | 待执行 |
| P1: Force push 到远程 | 10 分钟 | 用户/运维 | 待执行 |
| P2: 修复当前代码硬编码 | 30 分钟 | 开发 | 待执行 |
| P2: 验证清理结果 | 15 分钟 | 用户/运维 | 待执行 |
| P2: 通知协作者重新 clone | 即时 | 用户 | 待执行 |

**总耗时**：约 2 小时（含验证）

---

## 八、不变量【不易】守护

1. BFG 操作前必须完成 P0（revoke 凭证）
2. 必须完整备份仓库
3. 必须通知所有协作者
4. BFG 后必须验证历史无残留
5. 当前代码硬编码必须同步修复（BFG 只清历史，不改当前文件）
6. `.encryption_key` 必须从 git 跟踪移除并加入 .gitignore

---

## 九、参考文档

- BFG Repo-Cleaner 官方: https://rtyley.github.io/bfg-repo-cleaner/
- GitHub 关于敏感数据清除: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository
- OpenAI API key 管理: https://platform.openai.com/api-keys
- DeepSeek API key 管理: https://platform.deepseek.com/api_keys
