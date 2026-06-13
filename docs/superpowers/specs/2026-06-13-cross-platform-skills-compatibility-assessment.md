# 跨平台技能兼容性与冲突评估报告

> 评估日期：2026-06-13
> 评估范围：Claude Code / OpenClaw / Hermes Agent 三套技能系统与云枢（Yunshu）原生系统的兼容性

---

## 1. 三套技能系统概览

### 1.1 Claude Code 技能

| 属性 | 值 |
|------|-----|
| 版本 | 2.1.167 |
| 技能总数 | 约 120+（含插件子技能） |
| 技能格式 | `SKILL.md`（YAML frontmatter + Markdown 指令） |
| 安装位置 | `~/.claude/skills/` + `~/.claude/plugins/cache/` |
| 执行机制 | LLM 根据 description 自动择机加载 SKILL.md 内容 |
| 技能来源 | skills.sh 市场、GitHub 仓库、本地文件、官方插件 |

### 1.2 OpenClaw 技能

| 属性 | 值 |
|------|-----|
| 版本 | 2026.5.6 |
| 技能总数 | 66（23 ready + 41 disabled + 2 needs_setup） |
| 技能格式 | `SKILL.md`（YAML frontmatter + Markdown + 可选脚本目录） |
| 安装位置 | `~/.openclaw/workspace/skills/` + 内置 `openclaw/skills/` |
| 执行机制 | 三级渐进加载：元数据→主体→资源 |
| 技能来源 | ClawHub 市场、GitHub、本地、openclaw-bundled、插件 |

### 1.3 Hermes Agent 技能

| 属性 | 值 |
|------|-----|
| 版本 | 最新 |
| 技能总数 | 60（全部启用） |
| 技能格式 | `SKILL.md`（YAML frontmatter + Markdown + 可选模板/脚本/引用） |
| 安装位置 | `~/.hermes/skills/` |
| 执行机制 | 启动时扫描→平台过滤→禁用检查→立即可用搜索→按需加载 |
| 技能来源 | builtin / official / local / github / URL / skills.sh / well-known |
| 特有系统 | Curator（生命周期管理）、Guard（安全扫描）、Sync（自动更新） |

---

## 2. 云枢现有系统能力基线

### 2.1 已注册工具（digital_life.py）

| 类别 | 工具名称 | 功能描述 |
|------|----------|----------|
| **自检** | `check_health` | 检查智能体身体状态 |
| | `get_status` | 获取完整状态 |
| | `search_memory` | 搜索对话记忆 |
| | `get_sensor_summary` | 查看传感器状态 |
| | `search_lifetrace` | 使用 LifeTrace 搜索记忆 |
| **文件系统** | `read_file` | 读取本地文件 |
| | `write_file` | 写入本地文件（含权限检查） |
| | `list_directory` | 列出目录内容 |
| | `get_file_info` | 获取文件详细信息 |
| | `search_files` | 按模式搜索文件 |
| **互联网** | `web_get` | HTTP GET 请求 |
| | `web_post` | HTTP POST 请求 |
| | `web_xpath` | XPath 提取 |
| | `web_css` | CSS 选择器提取 |
| | `web_search` | 互联网搜索（多引擎） |
| | `web_clean_data` | 清洗网页数据 |
| | `web_download` | 下载文件 |
| | `web_batch` | 批量请求 |
| **进程管理** | `run_program` | 运行白名单程序 |
| | `list_processes` | 列出运行进程 |
| | `stop_process` | 终止进程 |
| **Shell** | `shell_execute` | 执行 shell 命令 |
| **扩展管理** | `ext_install/uninstall/list/toggle/discover/configure/send_channel` | 安装/管理扩展 |

### 2.2 系统基础设施（system_tools.py）

| 系统 | 功能 |
|------|------|
| 文件安全 | 保护系统目录、阻止可执行扩展名、路径解析 |
| Python 沙盒 | 受限环境，阻止逃逸模式 |
| 定时任务 | Task Scheduler 集成，白名单命令 |
| 无头浏览器 | Selenium Chrome，URL 限制 |
| 进程白名单 | 默认 + 自定义白名单 |
| 工作区管理 | 受保护的沙盒目录 |
| 剪贴板 | pyperclip + PowerShell 回退 |
| 配置 | 工具启用/禁用状态持久化 |

### 2.3 扩展系统（agent/extensions/）

| 扩展类型 | 功能 |
|----------|------|
| Skills | 安装/卸载/启用/禁用/发现技能 |
| MCP Servers | 安装/配置/管理 MCP 服务器 |
| Channels | 通信通道（消息平台） |
| Plugins | 通用插件管理 |
| 安全 | PermissionSystem + SafetyGuard 双检查 |

---

## 3. 功能重叠矩阵

### 3.1 云枢 vs. 三套通用技能的全面对照

| 功能领域 | 云枢已有 | Claude Code 技能 | OpenClaw 技能 | Hermes 技能 | 重叠程度 |
|----------|---------|-----------------|---------------|-------------|----------|
| **文件读写** | `read_file`/`write_file` | 内置 Read/Write 工具 | 无独立技能 | 无独立技能 | 完全重叠 |
| **文件搜索** | `search_files`(glob) | 内置 Glob/Grep | 无独立技能 | smart-explore(AST搜索) | 完全重叠 |
| **文件元数据** | `get_file_info` | file-operations(wc/stat) | 无 | 无 | 完全重叠 |
| **代码审查** | 无独立工具 | review, code-review(多个版本) | code-review | simplify-code | **高重叠**——三方都有 |
| **代码重构** | 无独立工具 | code-refactor(多个版本) | code-refactor | 无 | **高重叠**——两方有 |
| **Shell 执行** | `shell_execute`(新增) | 内置 Bash 工具 | 无 | 内置 sheel/terminal 工具集 | 完全重叠 |
| **进程管理** | `run_program` | 内置 Bash | 无 | 内置 process 工具集 | 完全重叠 |
| **网页抓取** | `web_get`/`web_css`/`web_xpath` | WebFetch(内置) | 无 | 无 | 完全重叠 |
| **网页搜索** | `web_search`(多引擎) | WebSearch(内置) | multi-search-engine(17引擎) | 无 | **高重叠**——两方有 |
| **浏览器控制** | 无头浏览器(Selenium) | gstack:browse(CDP) | 无 | 无 | 部分重叠 |
| **记忆系统** | search_memory, lifetrace, MemoryManager | claude-mem(gstack/learn) | self-improvement | 无 | **中重叠**——机制不同 |
| **定时任务** | 定时任务(Task Scheduler) | 无 | taskflow(多步✓) | cron(hermes cron ✓) | **中重叠**——实现不同 |
| **PDF 处理** | 无 | gstack:make-pdf | pdf, nano-pdf | nano-pdf | **需补充**——云枢全无 |
| **架构图** | 无 | 无 | 无 | architecture-diagram | **需补充**——三方有 |
| **设计规范** | 无 | frontend-design | 无 | design-md | **需补充**——两方有 |
| **工作流编排** | 无 | ralph-loop, planning-with-files | flow, taskflow | development-workflows | **高重叠**——三方都有 |
| **多代理编排** | 无 | subagent-driven, dispatching-agents | coding-agent | ai-coding-agents | **高重叠**——三方都有 |
| **API 文档** | 无 | api-docs-generator | 无 | 无 | **需补充** |
| **提示词优化** | 无 | prompt-optimizer | 无 | 无 | **需补充** |
| **自我改进** | 无 | gstack:learn | self-improvement | 无 | **中重叠**——两方有 |
| **Obsidian 集成** | 无 | 无 | obsidian ✓ | obsidian ✓ | **需补充** ——两方有 |
| **Notion 集成** | 无 | 无 | notion ✓ | notion ✓ | **需补充**——两方有 |
| **飞书(Lark)集成** | 无 | lark*(27个✓) | 无 | lark*(26个✓) | **需补充**——两方有 |
| **天气查询** | 无 | 无 | weather(wttr.in ✓) | 无 | **需补充** |
| **多媒体** | 无 | 无 | 无 | ascii-art/video, gif-search, music, youtube, spotify | **需补充**——Hermes 独有 |
| **MLOps** | 无 | 无 | 无 | huggingface-hub, llama-cpp, dspy, weights-and-biases | **需补充**——Hermes 独有 |

### 3.2 详细领域分析

#### 3.2.1 代码审查（三方竞争）

| 属性 | Claude: code-review | OpenClaw: code-review | Hermes: simplify-code |
|------|--------------------|---------------------|---------------------|
| 覆盖维度 | 正确性+安全+性能+清洁度 | 安全+性能+可维护性+正确性+测试 | 简化重构 |
| 审查深度 | 低/中/高三级 | 严重级别+结构化反馈 | 仅简化 |
| 交互方式 | 被动审查 | 审查+反模式指南 | 重构 |
| 与云枢冲突 | 高——如果云枢也做代码审查会重复 | 高——同上 | 中——专注简化，较少冲突 |

**结论**：云枢目前没有独立代码审查工具。如果安装，三方代码审查技能相互重叠度高，但各有侧重。建议最多安装一套。

#### 3.2.2 工作流编排（三方竞争）

| 属性 | Claude: planning-with-files | OpenClaw: flow/taskflow | Hermes: development-workflows |
|------|---------------------------|------------------------|-------------------------------|
| 模式 | 基于文件(tasks.md/findings.md) | 自然语言→工作流编译 | 开发阶段工作流 |
| 持久化 | files + hooks 完整性校验 | 注册表+安全扫描 | 会话内 |
| 适合场景 | 开发计划管理 | 通用自动化 | 开发工作流 |

**结论**：三者模式差异大。planning-with-files 以文件为核心，flow 是编译式，development-workflows 是阶段式。兼容性中等，视场景选择。

#### 3.2.3 多引擎搜索（云枢 vs. OpenClaw）

| 属性 | 云枢 web_search | OpenClaw: multi-search-engine |
|------|----------------|------------------------------|
| 引擎数 | 4（DuckDuckGo/Tavily/Bing/Google） | 17（8中文 + 9全球） |
| 中文支持 | 一般 | 强（百度/搜狗/360/头条/维基中文等） |
| API Key 需求 | Tavily/Bing/Google 需要密钥 | 无需 API Key |
| 站内搜索 | 不支持 | 支持 site: 运算符 |
| WolframAlpha | 不支持 | 支持 |

**结论**：OpenClaw 的多引擎搜索覆盖更广，特别是中文搜索和无需密钥方面是明显优势。如需增强云枢搜索能力，这是优先候选。

#### 3.2.4 记忆系统（云枢 vs. Claude: claude-mem）

| 属性 | 云枢 MemoryManager | Claude: claude-mem |
|------|-------------------|--------------------|
| 存储方式 | JSON + LLMService | MCP 工具（数据库） |
| 检索方式 | 关键词搜索 + 向量(opt) | 3层：Search→Timeline→Fetch |
| 跨会话 | ✓ | ✓（基于 MCP） |
| 知识构建 | 手动 search | 自动构建 corpus（knowledge-agent） |
| 代码库学习 | 无 | learn-codebase（全面文件读取） |

**结论**：云枢的记忆系统自建，claude-mem 是通过 MCP 的外部系统。两者可共存，但功能重叠。知识库构建方面 claude-mem 更强。

---

## 4. 冲突分析

### 4.1 文件系统冲突

| 冲突风险 | 说明 | 严重程度 |
|----------|------|----------|
| **SKILL.md 格式不兼容** | 三者都用 SKILL.md 但 frontmatter 字段不一（platforms/tags/setup/来源等），云枢扩展系统用自有格式 | **高**——直接安装无法被云枢识别 |
| **安装路径竞争** | Claude 用 `~/.claude/skills/`，Hermes 用 `~/.hermes/skills/`，OpenClaw 用 `~/.openclaw/skills/`，云枢用 `agent/extensions/` | **低**——安装在各自目录 |
| **配置文件竞争** | 各系统都有自己的 config.json / config.yaml，互不读写对方配置 | **低**——隔离 |

### 4.2 API 与端口冲突

| 冲突风险 | 说明 | 严重程度 |
|----------|------|----------|
| **搜索引擎竞争** | 云枢 web_search + OpenClaw multi-search-engine 可能同时调用同一搜索 API | **中**——如 Tavily/Bing API key 配额共用需注意 |
| **LLM API Key** | 各系统可能使用不同 LLM 提供商或同一提供商的同一密钥 | **中**——需确认各工具的 .env/config 配置 |
| **端口占用** | OpenClaw 默认网关端口 19001，Hermes 有 gateway 服务，云枢 Flask 5000 | **低**——端口不同 |

### 4.3 资源竞争

| 资源 | 竞争方 | 说明 |
|------|--------|------|
| **CPU** | 三方 + 云枢同时运行时 | 低——非持续计算 |
| **磁盘** | 技能文件存储 | 低——总 < 100MB |
| **网络** | 搜索/抓取/API 调用 | 低——非持续 |
| **进程** | subprocess 子进程（bash/claude/hermes/openclaw） | **中**——嵌套调用有风险 |
| **工作目录** | 各系统都有 workspace 概念 | 低——目录不同 |

### 4.4 嵌套调用风险

```
云枢
 └─ shell_execute("claude /code-review ...")
     └─ Claude Code 启动子进程
         └─ 调用 Bash 工具
             └─ 触发更多子进程
```

这是最大的风险：**云枢通过 shell_execute 调用 claude/hermes/openclaw CLI，然后这些 CLI 工具又启动 bash/子进程，形成嵌套进程树**。可能导致：
- 资源耗尽（进程数爆炸）
- 权限混淆（哪个系统的安全策略生效？）
- 难以清理的孤儿进程

**严重程度：高** —— 建议云枢不要直接通过 shell_execute 递归调用其他 agent CLI 的交互式模式。

---

## 5. 资源消耗评估

### 5.1 磁盘占用

| 系统 | 技能目录大小 | 备注 |
|------|------------|------|
| Claude Code skills | ~5MB | 纯文本 SKILL.md |
| Claude Code plugins | ~15MB | 含缓存的多版本 |
| OpenClaw skills | ~8MB | SKILL.md + 引用 + 脚本 |
| Hermes skills | ~10MB | SKILL.md + 模板 + 引用 |
| **总计** | **~40MB** | 可忽略 |

### 5.2 运行时开销

| 系统 | 启动时间 | 内存占用 |
|------|---------|---------|
| Claude Code | ~2s | ~100MB |
| OpenClaw | ~1s | ~80MB |
| Hermes | ~1.5s | ~90MB |

**注意**：这些是各 CLI 工具自身的开销，不是技能的开销。技能只有被加载时才会消耗 token 上下文。

---

## 6. 兼容性等级分类

### A 级：可无缝集成（格式兼容、功能互补）

| 技能 | 来源 | 理由 |
|------|------|------|
| `multi-search-engine` | OpenClaw | 17引擎 + 无需API Key，可增量部署作为云枢搜索的补充 |
| `weather` | OpenClaw | 轻量级，独立功能，零冲突 |
| `nano-pdf` | OpenClaw/Hermes | 云枢无 PDF 能力，填补空白 |
| `gif-search` | Hermes | 独立功能，零冲突 |
| `music-and-audio` | Hermes | 独立功能 |
| `architecture-diagram` | Hermes | 云枢无架构图能力 |
| `humanizer-zh` | OpenClaw | 中文优化，云枢目前无类似功能 |
| `ascii-art`/`ascii-video` | Hermes | 独立创意功能 |

### B 级：有条件集成（部分重叠，需配置）

| 技能 | 来源 | 重叠项 | 处理方式 |
|------|------|--------|----------|
| `code-review` | Claude/OpenClaw | 与 `verify` 部分重叠 | 二选一或根据场景使用不同审查维度 |
| `code-refactor` | Claude/OpenClaw | 与 `refactor` 重叠 | 二选一 |
| `smart-explore`(AST) | Hermes(claude-mem) | 与 `search_files`/`glob` 重叠 | 作为互补（AST 搜索 VS 路径搜索） |
| `claude-code` | Hermes | 与 `shell_execute("claude ...")` 重叠 | 统一通过 shell_execute 调用 |
| `architecture-diagram` | Hermes | HTML+SVG 生成 vs 云枢绘图能力 | 互补 |

### C 级：不兼容或高风险（格式冲突、功能冗余严重、嵌套调用危险）

| 技能 | 来源 | 原因 |
|------|------|------|
| 三方 `SKILL.md` 直接安装到云枢扩展 | 全部 | **格式不兼容**，SKILL.md 的 frontmatter 字段各异，云枢扩展系统无法解析 |
| `flow` | OpenClaw | 自身是工作流引擎，与云枢的扩展系统竞争 |
| `adaptive-skill-stack` | OpenClaw | 元技能，意图覆盖其他所有决策，与云枢原生决策逻辑冲突 |
| `coding-agent` | OpenClaw | 委派给其他 agent CLI → 嵌套调用风险 |
| `ai-coding-agents` | Hermes | 同上，编排三套 agent → 嵌套风险 |
| `hermes-agent`(技能) | Hermes | 完整的 agent 配置指南 → 与云枢自身身份冲突 |

---

## 7. 结论与建议

### 7.1 云枢真正缺失、值得补充的能力

按优先顺序排列：

| 优先级 | 能力 | 来源 | 集成方式 |
|--------|------|------|----------|
| ⭐⭐⭐ | **搜索引擎增强**(multi-search-engine) | OpenClaw | 云枢 web_search 已有多引擎基础，可将 multi-search-engine 的 17 引擎作为后端扩展 |
| ⭐⭐⭐ | **PDF 处理**(nano-pdf/pdf) | OpenClaw/Hermes | 云枢无 PDF 能力，新增独立工具 |
| ⭐⭐⭐ | **架构图生成**(architecture-diagram) | Hermes | 独立纯 HTML+SVG 生成，无外部依赖 |
| ⭐⭐ | **代码审查**(code-review) | Claude/OpenClaw | 云枢有 run/simplify 但无正式审查流程 |
| ⭐⭐ | **中文优化**(humanizer-zh) | OpenClaw | 中文去 AI 痕迹，实用 |
| ⭐⭐ | **LlamaCpp / HuggingFace 集成** | Hermes | 本地模型运行能力 |
| ⭐ | **天气查询**(weather) | OpenClaw | 轻量实用 |
| ⭐ | **创意生成**(ascii/design-md/architecture) | Hermes | 锦上添花 |

### 7.2 不建议安装的内容

1. **元技能**（adaptive-skill-stack、hermes-agent 技能）—— 它们是"技能的技能"，与云枢自身的决策逻辑冲突
2. **另有 agent CLI 委派的技能**（coding-agent、ai-coding-agents）—— 嵌套调用风险高
3. **工作流引擎冲突**（flow vs planning-with-files vs development-workflows）—— 选一套即可
4. **格式不兼容的 SKILL.md 直接注册**—— 需转换格式后才能纳入云枢扩展系统

### 7.3 推荐的集成架构

```
┌──────────────────────────────────────────────────────┐
│                   云枢 (Yunshu)                        │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ 原生工具     │  │ 扩展系统     │  │ shell_execute │ │
│  │ (36个)      │  │ (Skills/MCP) │  │ (bash桥接)    │ │
│  └─────────────┘  └──────┬───────┘  └──────┬───────┘ │
│                          │                  │          │
└──────────────────────────┼──────────────────┼──────────┘
                           │                  │
              ┌────────────┴─────┐   ┌────────┴────────┐
              │ MCP 协议桥接      │   │ CLI 子进程调用    │
              │ (持久连接)        │   │ (一次性任务)      │
              └──────────┬───────┘   └────────┬────────┘
                         │                    │
              ┌──────────┴────────┐  ┌────────┴────────┐
              │ Hermes 技能/工具  │  │ claude / openclaw│
              │ OpenClaw MCP     │  │ hermes CLI 命令  │
              └───────────────────┘  └─────────────────┘
```

- **适合 MCP 桥接**：持续会话、工具集共享（如记忆系统、搜索 API）
- **适合 CLI 子进程**：一次性任务（如 PDF 处理、代码审查、搜索查询）
- **不适合任何形式**：元技能、agent 委派、交互式长时间运行

---

*报告完毕。如需对特定技能进行更深入的技术可行性分析，可以继续。*
