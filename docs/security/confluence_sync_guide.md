# Confluence 知识库同步操作指南

> **同步目标：** 将 P0 安全修复复盘报告同步到团队 Confluence 知识库
> **复盘报告路径：** `docs/security/p0_security_retrospective.md`

---

## 一、前置准备

### 1.1 获取 Confluence API Token

1. 访问 https://id.atlassian.com/manage-profile/security/api-tokens
2. 点击 "Create API token"
3. 命名为 "P0-Security-Sync"
4. 复制生成的 Token（仅显示一次）

### 1.2 安装依赖

```bash
pip install requests
```

### 1.3 设置环境变量

**Linux / macOS:**
```bash
export CONFLUENCE_BASE_URL="https://your-team.atlassian.net/wiki"
export CONFLUENCE_USER="your-email@team.com"
export CONFLUENCE_TOKEN="your-api-token"
```

**Windows PowerShell:**
```powershell
$env:CONFLUENCE_BASE_URL="https://your-team.atlassian.net/wiki"
$env:CONFLUENCE_USER="your-email@team.com"
$env:CONFLUENCE_TOKEN="your-api-token"
```

---

## 二、自动同步（推荐）

### 2.1 创建新页面

```bash
python scripts/sync_to_confluence.py --space "SEC" --title "P0 安全修复复盘报告"
```

参数说明：
- `--space`：Confluence Space Key（如 SEC、DEV、OPS）
- `--title`：页面标题
- `--report-path`：报告文件路径（默认 `docs/security/p0_security_retrospective.md`）

### 2.2 更新已有页面

```bash
python scripts/sync_to_confluence.py --space "SEC" --title "P0 安全修复复盘报告" --page-id 123456789
```

参数说明：
- `--page-id`：已有页面的 ID（从 URL 获取：`.../pages/123456789` 中的数字）

### 2.3 同步脚本功能

脚本会自动完成以下工作：
1. 读取 `docs/security/p0_security_retrospective.md`
2. 将 Markdown 转换为 Confluence Storage Format（代码块转为 Confluence 代码宏）
3. 检查页面是否已存在（避免重复创建）
4. 创建新页面或更新已有页面
5. 输出页面访问 URL

---

## 三、手动同步（备选方案）

如果自动脚本无法使用，可手动粘贴：

### 3.1 复制报告内容

```bash
cat docs/security/p0_security_retrospective.md
```

### 3.2 在 Confluence 中粘贴

1. 在 Confluence 中创建新页面
2. 标题填入：`P0 安全修复复盘报告`
3. 正文粘贴 Markdown 内容
4. Confluence 会自动识别 Markdown 格式
5. 点击 "Publish" 发布

### 3.3 优化建议

- 粘贴后检查代码块格式（Confluence 可能需要手动设置语言）
- 检查表格是否正确渲染
- 检查文件链接是否需要调整为 Confluence 附件

---

## 四、同步内容清单

以下文件内容应同步到 Confluence：

| 文件 | Confluence 位置 | 说明 |
|------|----------------|------|
| `docs/security/p0_security_retrospective.md` | SEC Space / 安全复盘 | 完整复盘报告 |
| `docs/security/security_coding_checklist.md` | SEC Space / 编码规范 | 安全编码检查清单 |
| `docs/wiki/security_config_wiki.md` | SEC Space / 安全配置 | 安全配置 Wiki（含 P0 修复记录章节） |
| `docs/handover/KNOWLEDGE_CHECKLIST.md` | DEV Space / 交接文档 | 团队交接知识清单 |

### 批量同步命令

```bash
# 同步复盘报告
python scripts/sync_to_confluence.py --space "SEC" --title "P0 安全修复复盘报告"

# 同步安全编码规范
python scripts/sync_to_confluence.py --space "SEC" --title "安全编码规范检查清单" --report-path "docs/security/security_coding_checklist.md"

# 同步安全配置 Wiki
python scripts/sync_to_confluence.py --space "SEC" --title "安全配置 Wiki" --report-path "docs/wiki/security_config_wiki.md"
```

---

## 五、验证清单

同步完成后，在 Confluence 中确认以下内容完整：

- [ ] 复盘报告 8 个章节全部渲染（事件概要、根因分析、修复方案、测试覆盖、预防机制、经验教训、改进追踪、附录）
- [ ] 代码块格式正确（语法高亮）
- [ ] 表格渲染正确（产出文件清单、验证命令等）
- [ ] 文件链接可点击（或已转为 Confluence 附件）
- [ ] 页面位于正确的 Space 中
- [ ] 页面权限设置正确（团队成员可读）

---

## 六、常见问题

### Q1: API Token 权限不足

**错误：** `HTTP 403 Forbidden`

**解决：** 确保账号对目标 Space 有 "Add Page" 权限，联系 Confluence 管理员。

### Q2: 页面标题冲突

**错误：** `HTTP 500` 或 "页面标题已存在"

**解决：** 使用 `--page-id` 参数更新已有页面，或修改 `--title` 参数。

### Q3: Markdown 格式渲染异常

**解决：** Confluence 对 Markdown 的支持有限，建议手动粘贴后调整代码块格式，或使用 Confluence Editor 手动排版。
