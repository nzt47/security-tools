# 云枢工具系统全面修复计划

> 基于 2026-06-19 工具评估报告制定的系统性修复方案
> 评估范围：40+ 工具，10 大类别

---

## 目录

1. [问题总览与优先级矩阵](#1-问题总览与优先级矩阵)
2. [修复阶段详细计划](#2-修复阶段详细计划)
3. [工作量评估](#3-工作量评估)
4. [资源需求分析](#4-资源需求分析)
5. [会话拆分方案](#5-会话拆分方案)
6. [独立会话提示词](#6-独立会话提示词)
7. [风险与应对](#7-风险与应对)
8. [验收标准](#8-验收标准)

---

## 1. 问题总览与优先级矩阵

| ID | 问题 | 类型 | 影响范围 | 优先级 | 修复难度 |
|----|------|------|----------|--------|----------|
| U1 | 返回格式不统一（字串/dict/混合） | 可用性 | 全局：所有工具消费者 | **P0** | ★☆☆ |
| U2 | web_search 结果硬限制 3 条 | 可用性 | 搜索工具用户 | **P0** | ★☆☆ |
| U3 | ext_list 中乱码注释 | 可用性 | 代码可维护性 | P2 | ★☆☆ |
| U4 | search_files 缺少路径安全校验 | 可用性/安全 | 文件系统工具用户 | **P0** | ★☆☆ |
| U5 | 错误信息中英文混杂 | 可用性 | 用户体验 | P2 | ★☆☆ |
| S1 | 具体工具集成测试覆盖率 0% | 稳定性 | 全局：所有工具 | **P0** | ★★☆ |
| S2 | 网络工具无 Mock 测试 | 稳定性 | 6 个 web_* 工具 | **P0** | ★★☆ |
| S3 | 技能开关同步问题（3 文件互覆盖） | 稳定性 | 扩展管理 | **P0** | ★★★ |
| S4 | 扩展管理器多重实例化 | 稳定性 | 扩展管理 | P1 | ★☆☆ |
| S5 | 条件注册工具对 LLM 不透明 | 稳定性 | Persona/偏好工具 | P1 | ★☆☆ |
| S6 | web_search 引擎初始化强依赖 | 稳定性 | 搜索工具 | P1 | ★★☆ |
| F1 | 无异步/流式工具执行 | 功能强度 | 大文件/长时间任务 | P1 | ★★★ |
| F2 | 无定时调度工具 | 功能强度 | 自动化任务 | P2 | ★★★ |
| F3 | 无图像处理/压缩/对比工具 | 功能强度 | 文件操作场景 | P2 | ★★☆ |
| F4 | 搜索结果深度不足且无去重合并 | 功能强度 | 搜索质量 | P1 | ★★☆ |
| F5 | 无工具调用限流/依赖管理 | 功能强度 | 系统稳定性 | P2 | ★★★ |

### 优先级定义

- **P0** — 阻塞性：直接影响系统可用性和稳定性，必须立即修复
- **P1** — 重要：影响核心功能质量，应在当前迭代修复
- **P2** — 增强：提升体验和可维护性，可在后续迭代完成

---

## 2. 修复阶段详细计划

### Phase 1：紧急修复 — Quick Wins（工作日 1-2）

| 步骤 | 任务 | 负责人 | 工时 | 产出物 | 依赖 |
|------|------|--------|------|--------|------|
| 1.1 | 统一所有工具返回格式为 `{"ok": bool, "error": str, "data": ...}` | 开发者 | 4h | 修改后的 digital_life.py | 无 |
| 1.2 | 放宽 web_search 结果限制（3→8条, 150→300字） | 开发者 | 1h | 修改后的 digital_life.py | 无 |
| 1.3 | 修复 ext_list 乱码注释（line 2735） | 开发者 | 0.5h | 代码清理 | 无 |
| 1.4 | 为 search_files 添加路径安全校验（normpath + 黑名单） | 开发者 | 1h | 修改后的 digital_life.py | 无 |
| 1.5 | 统一所有错误信息为中文 | 开发者 | 2h | 所有工具的错误文本 | 1.1 |
| 1.6 | 条件注册工具改为始终注册 + 运行时提示不可用 | 开发者 | 1.5h | 修改后的 digital_life.py | 无 |
| 1.7 | 修复 ext_list 数据源同步：统一 skills.json 为唯一数据源 | 开发者 | 4h | 修改后的 ext_list + ExtensionManager | 无 |

**Phase 1 合计：14 工时（约 2 个工作日）**

### Phase 2：测试体系建设（工作日 3-5）

| 步骤 | 任务 | 负责人 | 工时 | 产出物 | 依赖 |
|------|------|--------|------|--------|------|
| 2.1 | 为文件系统工具（read/write/list/search/get_info）编写单元测试 | 开发者 | 4h | test_file_tools.py | Phase 1 |
| 2.2 | 为网络工具（web_get/post/search/xpath/css/batch）编写 Mock 测试 | 开发者 | 6h | test_web_tools.py | Phase 1 |
| 2.3 | 为扩展管理工具（ext_*）编写集成测试 | 开发者 | 6h | test_ext_tools.py | 1.7 |
| 2.4 | 为 PDF 工具编写测试 | 开发者 | 2h | test_pdf_tools.py | Phase 1 |
| 2.5 | 为软件管理工具编写测试 | 开发者 | 3h | test_software_tools.py | Phase 1 |
| 2.6 | 为 shell_execute + 进程管理工具编写测试 | 开发者 | 3h | test_system_tools.py | Phase 1 |
| 2.7 | 运行全量测试并修复发现的回归问题 | 开发者 | 4h | 测试报告 | 2.1-2.6 |
| 2.8 | 配置 CI（如果尚未配置）自动运行测试 | 开发者 | 2h | CI 配置 | 2.7 |

**Phase 2 合计：30 工时（约 4 个工作日）**

### Phase 3：功能增强（工作日 6-10）

| 步骤 | 任务 | 负责人 | 工时 | 产出物 | 依赖 |
|------|------|--------|------|--------|------|
| 3.1 | 增加搜索结果多引擎去重合并 | 开发者 | 6h | search_aggregator 模块 | Phase 1 |
| 3.2 | 增加文件压缩/解压工具（zip/tar.gz） | 开发者 | 4h | compress_tools.py | Phase 1 |
| 3.3 | 增加 JSON/YAML 结构化数据处理工具 | 开发者 | 4h | data_process_tools.py | Phase 1 |
| 3.4 | 增加文件比较/diff 工具 | 开发者 | 3h | diff_tools.py | Phase 1 |
| 3.5 | 增加定时调度工具（基于 schedule 库） | 开发者 | 8h | scheduling.py + API 端点 | Phase 1 |
| 3.6 | 增加异步工具执行框架（支持长时间任务） | 开发者 | 12h | async_executor.py | Phase 1 |
| 3.7 | 为新增工具编写测试 | 开发者 | 6h | test_new_tools.py | 3.1-3.6 |
| 3.8 | 更新 API 文档 | 开发者 | 4h | 工具 API 文档 | 3.1-3.6 |

**Phase 3 合计：47 工时（约 6 个工作日）**

### Phase 4：架构优化（工作日 11-13）

| 步骤 | 任务 | 负责人 | 工时 | 产出物 | 依赖 |
|------|------|--------|------|--------|------|
| 4.1 | 重构 web_search 引擎初始化：延迟初始化 + 降级策略 | 开发者 | 4h | 修改后的 digital_life.py | Phase 1 |
| 4.2 | 扩展管理器单例化，避免重复实例化 | 开发者 | 3h | extension_manager.py | Phase 1 |
| 4.3 | 增加工具调用限流器（基于令牌桶） | 开发者 | 5h | rate_limiter.py | Phase 1 |
| 4.4 | 为异步执行框架添加进度回调/WebSocket 通知 | 开发者 | 6h | async_executor.py + 路由 | 3.6 |
| 4.5 | 重构 digital_life.py 工具注册代码为独立模块（按类别拆分） | 开发者 | 8h | agent/tools/*.py | Phase 1-3 |

**Phase 4 合计：26 工时（约 4 个工作日）**

### Phase 5：稳定性和性能（工作日 14-15）

| 步骤 | 任务 | 负责人 | 工时 | 产出物 | 依赖 |
|------|------|--------|------|--------|------|
| 5.1 | 工具调用链路追踪（trace_id 传播） | 开发者 | 4h | trace_context.py | Phase 4 |
| 5.2 | 长时间运行压力测试（并发调用 50+ 轮） | 开发者 | 4h | 压力测试报告 | Phase 2 |
| 5.3 | 优化工具注册表查找性能（dict → LRU cache） | 开发者 | 2h | tools/__init__.py | 无 |
| 5.4 | 运行时工具健康检查端点 | 开发者 | 3h | /api/tools/health | Phase 1 |
| 5.5 | 最终回归测试+合入主分支 | 开发者 | 4h | 合并 PR | 全部 |

**Phase 5 合计：17 工时（约 3 个工作日）**

---

## 3. 工作量评估

### 总工时汇总

| 阶段 | 工时 | 工作日 | 日历天数（单人） | 日历天数（双人） |
|------|------|--------|-----------------|-----------------|
| Phase 1: 紧急修复 | 14h | 2 | 2 | 1 |
| Phase 2: 测试体系 | 30h | 4 | 4 | 2 |
| Phase 3: 功能增强 | 47h | 6 | 6 | 3.5 |
| Phase 4: 架构优化 | 26h | 4 | 4 | 2 |
| Phase 5: 稳定性 | 17h | 3 | 3 | 1.5 |
| **合计** | **134h** | **19** | **19** | **10** |

### 人员配置方案

| 方案 | 人数 | 总日历天数 | 并行度 | 适合场景 |
|------|------|-----------|--------|----------|
| **单人全栈** | 1 | 19 天 | 无 | 预算有限，可接受较长周期 |
| **双人并行** | 2 | 10 天 | Phase 2/3 可并行 | 标准配置，推荐 |
| **三人小组** | 3 | 7 天 | 全部阶段可并行 | 需要快速交付 |

### 按角色的人力需求

| 角色 | 技能要求 | 投入 | 关键阶段 |
|------|----------|------|----------|
| Python 后端工程师 | 熟悉异步编程、pytest、API 设计 | 134h | Phase 1-5 |
| QA/测试工程师 | 熟悉 pytest、Mock、集成测试 | 30h | Phase 2 |
| DevOps（可选） | 熟悉 CI 配置 | 2h | Phase 2.8 |

---

## 4. 资源需求分析

### 技术资源

| 资源 | 用途 | 是否已有 | 备注 |
|------|------|---------|------|
| 开发环境（Python 3.10+） | 代码开发和测试 | ✅ 已有 | 当前项目环境 |
| pytest + pytest-mock | 单元测试和 Mock 测试 | ✅ 已有 | pytest 已安装 |
| Git 分支隔离 | 各会话独立开发互不影响 | ✅ 已有 | 使用 git 分支 |
| schedule 库 | 定时调度工具（Phase 3.5） | ❌ 需安装 | `pip install schedule` |
| aiohttp / asyncio | 异步执行框架（Phase 3.6） | ⚠️ 需确认 | 检查当前 web 框架 |
| WebSocket 支持 | 异步任务进度推送（Phase 4.4） | ⚠️ 需确认 | 取决于当前 server 框架 |
| CI 服务（如 GitHub Actions） | 自动运行测试（Phase 2.8） | ❌ 需配置 | 代码托管平台决定 |

### 工具依赖

| 库 | 版本 | 用途 | 阶段 |
|----|------|------|------|
| pytest | >=7.0 | 测试框架 | Phase 2 |
| pytest-mock | >=3.10 | Mock 测试 | Phase 2 |
| schedule | >=1.2 | 定时任务 | Phase 3.5 |
| zipfile/pathlib | stdlib | 压缩工具 | Phase 3.2 |
| json/yaml | stdlib/pyyaml | 数据处理 | Phase 3.3 |
| difflib | stdlib | 文件对比 | Phase 3.4 |

---

## 5. 会话拆分方案

### 5.1 是否需要拆分？

**结论：需要拆分为 3 个独立会话。**

理由：
1. **互不阻塞** — Phase 1 是 Phase 2-5 的前置条件，但 Phase 2/3/4 之间无严格依赖
2. **关注点分离** — 修复（Phase 1）→ 测试（Phase 2）→ 增强（Phase 3-5）是不同认知模式
3. **上下文窗口限制** — 全部 5 个阶段的细节无法在单个会话上下文窗口中完整容纳
4. **可分阶段验收** — 每个会话结束后可独立验收交付物

### 5.2 拆分方案

```
会话 A（Phase 1）：紧急修复 Quick Wins
  ├── 1.1 统一返回格式
  ├── 1.2 web_search 放宽限制
  ├── 1.3 修复乱码注释
  ├── 1.4 search_files 路径安全校验
  ├── 1.5 错误信息统一中文
  ├── 1.6 条件注册工具改造
  └── 1.7 ext_list 数据源同步 ← 此步骤依赖后端模块知识最深

会话 B（Phase 2）：测试体系建设
  ├── 2.1-2.6 各模块测试
  ├── 2.7 全量回归
  └── 2.8 CI 配置
  前置条件：Phase 1 所有修改已合并到 master

会话 C（Phase 3-5）：功能增强 + 架构优化 + 稳定性
  ├── 3.1-3.8 功能增强
  ├── 4.1-4.5 架构优化
  └── 5.1-5.5 稳定性和合入
  前置条件：Phase 1 已完成，Phase 2 测试框架就绪
```

### 5.3 执行顺序

```
时间线 →
会话 A ──────────────────────▸ 完成
        ↘
         会话 B ──────────────▸ 完成
                  ↘
                   会话 C ────▸ 完成
```

- 会话 A 和会话 B 可以**部分并行**：B 可以在 A 完成 1.1-1.6 后立即启动（1.7 单独处理）
- 会话 C 必须在 A 和 B 之后启动

---

## 6. 独立会话提示词

### 会话 A：紧急修复（Quick Wins）

> ⚠️ **使用方法**：将会话 A、B、C 的提示词分别复制到新的 Claude Code 会话中执行

---

<session-break>
## 会话 A — 提示词开始
</session-break>

```markdown
## 目标
对云枢工具系统执行紧急修复，解决 7 个 P0 优先级问题。所有修改基于 master 分支。

## 背景
云枢 DigitalLife 系统在 `c:\Users\Administrator\agent` 目录下，工具注册在 `agent/digital_life.py` 的 `_register_builtin_tools()` 方法中（约 1912-3179 行）。工具注册框架在 `agent/tools/__init__.py`。

## 需要完成的任务

### 任务 1：统一工具返回格式（4h）
**问题**：部分工具返回纯字符串（search_memory、remember、search_lifetrace），部分返回 dict（web_*），部分返回混合格式（planning 工具）。

**要求**：
1. 所有工具统一返回 `{"ok": bool, "error": str, "data": ...}` 格式
2. 当前返回字符串的工具改为返回 dict，原有内容放在 `data` 字段
3. 测试 `test_tools_registry.py` 需要更新以匹配新的返回格式
4. 检查 `test_tool_calling.py` 中 `_execute_safe` 方法是否需要对 dict 结果的特殊处理

**涉及工具**：search_memory, remember, search_lifetrace, get_persona_info, get_preferences, check_health（planning tools）

### 任务 2：放宽 web_search 结果限制（1h）
**问题**：`digital_life.py:2356` 中搜索结果硬限制为最多 3 条，snippet 截断 150 字。

**要求**：
1. 将 `results[:3]` 改为 `results[:8]`
2. 将 snippet 截断从 150 放宽到 300 字
3. 确保结果总量控制通过 token 计算而非固定数字
4. schema 中的 `num_results` 描述同步更新

### 任务 3：修复 ext_list 乱码注释（0.5h）
**问题**：`digital_life.py:2735` 行存在中文编码损坏的注释。

**要求**：
1. 将行 `# �� skill �����߳���` 替换为 `# 非 skill 类型走扩展管理器`

### 任务 4：search_files 添加路径安全校验（1h）
**问题**：`digital_life.py:2155-2160` 中 pattern 直接传给 glob，可能被路径遍历攻击利用。

**要求**：
1. 对 `root_path` 使用 `os.path.normpath()` 规范化
2. 检查 pattern 是否包含 `..` 路径穿越符
3. 禁止访问 `root_path` 目录之外的路径（使用 `Path(root_path).resolve()`）
4. 返回安全的错误提示

### 任务 5：统一错误信息为中文（2h）
**问题**：错误信息中英文混杂。

**要求**：
1. 扫描所有工具的错误信息，将英文提示改为中文
2. 统一使用"请提供…"、"未找到…"、"执行失败…"等中文格式
3. 保持技术术语（URL、API、PID 等）保留英文

**涉及范围**：digital_life.py 中 `_register_builtin_tools()` 内的所有工具

### 任务 6：条件注册工具改为始终注册 + 运行时提示（1.5h）
**问题**：`digital_life.py:1986-2065` 中 search_lifetrace、get_persona_info、get_preferences、trigger_distillation 依赖 `_v2_lifetrace` 标志位条件注册。条件不满足时这些工具不注册，但 LLM 不知道它们不可用。

**要求**：
1. 移除条件注册逻辑，始终注册这些工具
2. 在工具处理函数中检查对应模块是否可用
3. 如果模块不可用，返回 `{"ok": False, "error": "LifeTrace 系统未启用，此工具不可用", "available": False}`
4. LLM 能通过返回结果判断工具状态

### 任务 7：修复 ext_list 数据源同步（4h）
**问题**：`digital_life.py:2703-2734` 中 ext_list 同时从 `data/skills.json` 读取技能列表，又从 `_make_ext_mgr()` 读取其他扩展类型。UI 开关和 API 查询可能不同步。

**要求**：
1. 统一技能配置的唯一数据源为 ExtensionManager
2. 移除 ext_list 中直接读 JSON 文件的部分
3. 确保 `ext_toggle` 的结果通过 ExtensionManager 持久化
4. 验证 UI 开关和 API 查询的结果一致性
5. 需要阅读 `agent/extensions/manager.py` 理解现有数据流

## 工作流要求
1. 每个任务完成时运行 `pytest` 确保已有测试通过
2. 创建一个新分支 `fix/tool-system-quickwins` 进行所有修改
3. 所有任务完成后创建 Merge Request 到 master
```

---

<session-break>
## 会话 B — 提示词开始
</session-break>

```markdown
## 目标
为云枢工具系统建立完整的测试体系，确保所有工具都有自动化测试覆盖。

## 前置条件
- 会话 A（紧急修复）已合并到 master
- 工作目录：`c:\Users\Administrator\agent`
- 创建分支：`fix/tool-system-testing`
- 基础测试框架：pytest + pytest-mock

## 背景
当前只有注册表框架测试（test_tools_registry.py）和 ToolCallingService 测试（test_tool_calling.py），具体工具的集成测试覆盖率为 0%。需要为 6 大工具类别编写测试。

## 测试文件和覆盖范围

### 1. test_file_tools.py（4h）
为以下工具编写测试：
- `read_file` — 正常读取、编码指定、行范围、文件不存在、超过大小限制
- `write_file` — 写入新文件、覆盖、权限拒绝（Mock PermissionSystem）
- `list_directory` — 正常列出、目录不存在、显示隐藏文件
- `search_files` — glob 模式搜索、根路径限定、无结果
- `get_file_info` — 文件信息、目录信息、路径不存在

**要求**：
- 使用 `tmp_path` fixture 创建临时文件
- 隔离 PermissionSystem 依赖（Mock）

### 2. test_web_tools.py（6h）
为以下工具编写测试：
- `web_get` — 正常返回、超时、404、自定义 headers（Mock HttpClient）
- `web_post` — 表单数据、JSON 数据、空参数
- `web_search` — 正常搜索、多引擎切换、无结果、降级策略（Mock SearchEngine）
- `web_xpath` — XPath 匹配、无匹配、传 HTML 跳过请求
- `web_css` — CSS 选择器、属性提取、传 HTML 跳过请求
- `web_batch` — 批量请求、并发限制

**要求**：
- 使用 unittest.mock 或 pytest-mock 对外部 HTTP 调用做 Mock
- 测试搜索引擎的自动故障转移逻辑
- 测试结果截断逻辑

### 3. test_ext_tools.py（6h）
为以下工具编写测试：
- `ext_install` — 安装 skill/mcp/channel/plugin、无效类型、无效来源
- `ext_uninstall` — 成功卸载、扩展不存在
- `ext_list` — 列出全部、按类型筛选、空列表
- `ext_toggle` — 启用/禁用、切换状态
- `ext_discover` — 搜索、按类型搜索
- `ext_configure` — 配置更新、无效 ID
- `ext_send_channel` — 发送消息、通道不存在

**要求**：
- Mock ExtensionManager 的所有方法
- 测试数据源一致性（skills.json vs ExtensionManager）

### 4. test_pdf_tools.py（2h）
为以下工具编写测试：
- `read_pdf` — 读取文本、页码范围、文件不存在
- `merge_pdf` — 合并多个 PDF、输出路径权限拒绝
- `split_pdf` — 按范围拆分、每页拆为一个文件
- `get_pdf_info` — 元信息读取、文件不存在

**要求**：
- 使用库中的 PDF 测试样本或生成小型测试 PDF
- 测试文件不存在等异常路径

### 5. test_software_tools.py（3h）
为以下工具编写测试：
- `software_search` — 所有后端搜索、指定后端、无结果
- `software_install` — 白名单内安装、白名单外需 confirm、权限拒绝
- `software_list` — 已安装列表、空列表
- `software_uninstall` — 卸载、权限拒绝

**要求**：
- Mock 所有后端（Chocolatey/pip/npm/GitHub/web_download）
- 测试 confirm 流程的逻辑分支

### 6. test_system_tools.py（3h）
为以下工具编写测试：
- `shell_execute` — 正常命令、命令被安全系统阻止、危险命令需确认、超时
- `run_program` — 正常启动、程序不在白名单
- `list_processes` — 正常列出
- `stop_process` — 正常终止、PID 无效、权限拒绝
- `get_weather` — 指定城市、自动 IP、格式参数

**要求**：
- Mock subprocess/psutil 调用
- 测试安全检查的逻辑分支

### 7. 全量回归（4h）
- 运行 `pytest agent/tests/ -v` 确保所有测试通过
- 修复发现的回归问题
- 生成测试覆盖率报告：`pytest --cov=agent.tools --cov-report=html`

### 8. CI 配置（2h）
- 如果代码托管在 GitHub，配置 `.github/workflows/test.yml`
- 在 push/pull_request 时自动运行测试
- 测试矩阵：Python 3.10 / 3.11

## 验收标准
1. 测试总量 >= 100 个测试用例
2. 核心工具覆盖率 >= 80%
3. 所有测试通过（pytest exit code 0）
4. CI 配置完毕并触发成功
```

---

<session-break>
## 会话 C — 提示词开始
</session-break>

```markdown
## 目标
在紧急修复和测试体系就绪的基础上，对云枢工具系统进行功能增强、架构优化和稳定性提升。

## 前置条件
- 会话 A（紧急修复）和会话 B（测试体系）已合并到 master
- 工作目录：`c:\Users\Administrator\agent`
- 创建分支：`fix/tool-system-enhancement`
- 当前测试套件全部通过

## 阶段 1：功能增强

### 任务 1.1：多引擎搜索结果聚合去重（6h）
**问题**：web_search 单次只调用一个引擎，结果质量受限于单一来源。

**要求**：
1. 在 `agent/web.py` 或新文件 `agent/search_aggregator.py` 中实现聚合搜索模块
2. 同时调用 2-3 个搜索引擎（主引擎+备选引擎）
3. 对结果进行去重（基于 URL 归一化）、评分（来源权重+关键词匹配度）
4. 按评分排序后返回 Top N 条
5. 添加 `aggregate` 参数（bool）让 LLM 选择是否启用聚合模式
6. 聚合搜索的超时控制：单个引擎超时不阻塞整体
7. 编写测试（包含在 3.7）

### 任务 1.2：文件压缩/解压工具（4h）
在 `agent/system_tools.py` 或新文件添加：
- `compress` — 压缩文件/目录为 zip 或 tar.gz。参数：`source_path`, `output_path`, `format`（zip/tar.gz）
- `decompress` — 解压压缩文件。参数：`file_path`, `output_dir`

**要求**：
- 基于 zipfile 和 tarfile（标准库）
- 支持大文件流式处理
- 路径安全检查（防止 zip slip 攻击）
- 进度回调支持（为后续异步框架预留）

### 任务 1.3：JSON/YAML 数据处理工具（4h）
在 `agent/data_process_tools.py` 或新文件添加：
- `json_query` — 使用 JSONPath 查询 JSON 数据
- `json_to_yaml` — JSON → YAML 转换
- `yaml_to_json` — YAML → JSON 转换
- `json_validate` — 验证 JSON 格式正确性
- `data_format_detect` — 自动检测数据格式（JSON/YAML/CSV/XML）

### 任务 1.4：文件比较工具（3h）
在 `agent/diff_tools.py` 添加：
- `diff_files` — 比较两个文件的差异。参数：`path1`, `path2`, `context_lines`（上下文行数）
- 基于 difflib 实现
- 返回统一格式的差异行（类似 git diff 格式）

### 任务 1.5：定时调度工具（8h）
**这是本次增强中最复杂的任务。**

新文件：`agent/scheduling.py` + API 路由 `agent/server_routes/routes_scheduling.py`

**工具：**
- `schedule_task` — 创建定时任务。参数：`name`, `cron_expr`（cron 表达式）或 `interval_minutes`, `action`（要执行的操作描述）, `params`
- `list_scheduled_tasks` — 列出所有定时任务
- `cancel_scheduled_task` — 取消指定任务
- `pause_scheduled_task` / `resume_scheduled_task` — 暂停/恢复

**要求：**
- 使用 `schedule` 库（需安装 `pip install schedule`）
- 任务持久化到 `data/schedules.json`
- 任务执行结果记录到 `data/schedule_history.jsonl`
- 服务器重启后自动恢复定时任务
- 注意线程安全（schedule 在主线程或独立线程运行）

**API 端点（用于前端管理）：**
- `GET /api/schedules` — 获取所有定时任务
- `POST /api/schedules` — 创建定时任务
- `DELETE /api/schedules/:id` — 删除定时任务
- `GET /api/schedules/history` — 获取执行历史

### 任务 1.6：异步工具执行框架（12h）
**本次增强中最具技术挑战性的任务。**

新文件：`agent/async_executor.py`

**核心机制：**
1. 任务提交 + 轮询模式：`submit_task`（返回 task_id）→ `get_task_status`（轮询）→ `get_task_result`（获取结果）
2. 支持长时间运行的工具（大文件下载、批量搜索等）
3. 任务状态：pending → running → completed / failed
4. 任务结果缓存（TTL 配置化）
5. 可选 WebSocket 推送进度

**要求：**
- 基于 `threading` 或 `concurrent.futures.ThreadPoolExecutor`（不引入 asyncio 以免与现有同步框架冲突）
- 最大并发数可配置（默认 3）
- 任务超时控制
- 数据库/文件系统持久化任务记录

### 任务 1.7：为新增工具编写测试（6h）
- 为 1.1-1.6 的所有新工具编写 pytest 测试
- 测试覆盖正常路径、异常路径、边界条件
- 集成到现有测试套件

### 任务 1.8：API 文档更新（4h）
- 更新所有新增工具的 Schema 和描述
- 确保 LLM 能正确理解和使用新工具

## 阶段 2：架构优化

### 任务 2.1：web_search 引擎初始化重构（4h）
**问题**：当前在 `_register_builtin_tools` 中一次性初始化所有搜索引擎，如果有引擎不可用则整体降级。

**要求**：
1. 改为延迟初始化：在第一次搜索时再按需初始化各个引擎
2. 失败引擎的自动隔离：单个引擎初始化失败不影响其他引擎
3. 定期重试失败的引擎（后台任务，间隔 5 分钟）
4. 在 `get_status` 中加入引擎健康状态

### 任务 2.2：扩展管理器单例化（3h）
**问题**：`_make_ext_mgr()` 在 `ext_install` 中被重复调用，每次创建新实例。

**要求**：
1. 将扩展管理器实例保存为 `self._ext_manager` 实例变量
2. 使用懒加载模式（首次访问时创建）
3. 移除 `_make_ext_mgr()` 函数，改为 `self._get_ext_manager()`
4. 确保线程安全（`threading.Lock`）

### 任务 2.3：工具调用限流器（5h）
新文件：`agent/rate_limiter.py`

**实现令牌桶算法：**
- 每个工具有独立的速率限制配置（`tools/__init__.py` 中注册时指定）
- 默认限制：普通工具 10次/秒，网络工具 5次/秒，Shell 工具 2次/秒
- 超出限制时返回 `{"ok": False, "error": "调用频率过高，请稍后重试", "retry_after": 0.5}`

### 任务 2.4：异步框架进度回调（6h）
在任务 1.6 的基础上，添加 WebSocket 通知和 REST API 端点：
- `GET /api/tasks/:id` — 获取任务状态
- `GET /api/tasks` — 获取所有任务列表
- `GET /api/tasks/:id/cancel` — 取消任务
- WebSocket 端点 `/ws/tasks/:id` — 实时推送任务进度

### 任务 2.5：工具注册代码模块化拆分（8h）
**问题**：`digital_life.py` 中的 `_register_builtin_tools()` 超过 1200 行，所有工具注册在一个方法中。

**要求**：
1. 将工具按类别拆分为独立模块：
   - `agent/tools/core_tools.py` — get_status, search_memory, remember 等
   - `agent/tools/file_tools.py` — read/write/list/search/get_file_info
   - `agent/tools/web_tools.py` — web_get/post/search/xpath/css/batch/download
   - `agent/tools/ext_tools.py` — ext_install/uninstall/list/toggle/discover/configure/send_channel
   - `agent/tools/pdf_tools.py` — read/merge/split/get_pdf_info
   - `agent/tools/software_tools.py` — search/install/list/uninstall
   - `agent/tools/system_tools.py` — shell_execute, run_program, processes, weather
   - `agent/tools/code_tools.py` — code_review, arch_diagram, humanize_zh
2. 每个模块导出 `register_all(digital_life_instance)` 函数
3. `_register_builtin_tools()` 简化为依次调用各模块的 register_all

## 阶段 3：稳定性提升

### 任务 3.1：工具调用链路追踪（4h）
**要求**：
1. 在 `agent/tools/__init__.py` 的 `call()` 函数中生成唯一的 `trace_id`
2. trace_id 通过日志上下文传递（使用 `logging.LoggerAdapter` 或 `contextvars`）
3. 在每条日志中输出 `trace_id`，便于问题排查
4. 关键工具（web_search, shell_execute）添加额外追踪信息

### 任务 3.2：压力测试（4h）
**要求**：
1. 编写压力测试脚本 `agent/tests/stress_test_tools.py`
2. 模拟 50 个并发工具调用（使用 `concurrent.futures.ThreadPoolExecutor`）
3. 测试场景：文件操作并发、网络请求并发、混合负载
4. 记录成功率、平均响应时间、P95/P99 延迟
5. 输出压力测试报告

### 任务 3.3：优化注册表查找性能（2h）
**要求**：
1. `_registry` 当前是普通 dict，查找 O(1) 已足够
2. 添加 `@lru_cache` 装饰 `get_tool_defs()` 和 `list_tools()`（需要手动失效机制）
3. 或者添加缓存版本号，工具注册/注销时递增

### 任务 3.4：工具健康检查端点（3h）
**要求**：
1. 添加 `GET /api/tools/health` 端点
2. 返回每个工具的最后调用状态（成功/失败/耗时）
3. 返回引擎健康状态（搜索引擎各引擎是否可用）
4. 返回整体健康评分

### 任务 3.5：最终回归和合入（4h）
1. 运行完整测试套件：`pytest agent/tests/ -v --tb=short`
2. 修复所有失败测试
3. 代码审查：检查新代码的质量和一致性
4. 创建合并 PR 到 master
5. 更新进度文档

## 验收标准
1. 所有测试通过，核心工具覆盖率 >= 75%
2. 新增工具注册到系统并能被 LLM 调用
3. 异步执行框架可正确运行长时间任务
4. 工具调用限流器生效
5. 架构模块化拆分完成，digital_life.py 工具注册部分 <= 200 行
6. 压力测试报告显示成功率 >= 99%，P95 延迟 <= 3s
```

---

## 7. 风险与应对

| 风险 | 概率 | 影响 | 应对措施 |
|------|------|------|----------|
| 统一返回格式破坏现有客户端 | 中 | 高 | 先扫描所有调用方（前端、API 消费者），更新后再部署 |
| ext_list 数据源同步修复涉及多文件 | 高 | 高 | 需要仔细阅读 ExtensionManager 的完整数据流再动手 |
| 异步框架与现有同步架构冲突 | 中 | 高 | 使用线程池而非 asyncio，保持现有接口不变 |
| 定时调度任务在服务器重启后丢失 | 低 | 中 | 使用文件持久化 + 启动时自动恢复机制 |
| 新工具测试不充分导致回归 | 中 | 中 | 在 Phase 2 之后才进入 Phase 3，测试框架先行 |
| 模块化拆分引入 import 循环依赖 | 中 | 中 | 提前规划模块依赖图，使用延迟导入 |

## 8. 验收标准

### 阶段验收门禁

| 阶段 | 验收标准 | 验证方式 |
|------|----------|----------|
| Phase 1 | 所有 7 个 Quick Wins 完成，pytest 通过 | `pytest agent/tests/ -v` |
| Phase 2 | 测试用例 >= 100，核心工具覆盖率 >= 80% | `pytest --cov=agent.tools` |
| Phase 3 | 所有新工具注册成功，可被 `tools.list_tools()` 列出 | 手动验证 |
| Phase 4 | digital_life.py 工具注册部分 <= 200 行 | `wc -l` |
| Phase 5 | 压力测试成功率 >= 99%，P95 < 3s | `stress_test_tools.py` |

### 最终验收清单

- [ ] 所有测试通过
- [ ] 测试覆盖率 >= 75%
- [ ] 修复所有 P0 问题
- [ ] 新增 8+ 个功能工具
- [ ] 异步执行框架可用
- [ ] 定时调度系统可用
- [ ] 工具调用限流器生效
- [ ] 代码模块化拆分完成
- [ ] CI 自动化测试配置完毕
- [ ] API 文档已更新

---

## 附录：文件修改清单

| 文件 | 修改类型 | 涉及阶段 |
|------|---------|---------|
| `agent/digital_life.py` | 修改 | Phase 1, 4 |
| `agent/tools/__init__.py` | 修改 | Phase 4, 5 |
| `agent/tools/core_tools.py` | **新建** | Phase 4 |
| `agent/tools/file_tools.py` | **新建** | Phase 4 |
| `agent/tools/web_tools.py` | **新建** | Phase 4 |
| `agent/tools/ext_tools.py` | **新建** | Phase 4 |
| `agent/tools/pdf_tools.py` | **新建** | Phase 4 |
| `agent/tools/software_tools.py` | **新建** | Phase 4 |
| `agent/tools/system_tools.py` | **修改** | Phase 3, 4 |
| `agent/tools/code_tools.py` | **新建** | Phase 4 |
| `agent/search_aggregator.py` | **新建** | Phase 3 |
| `agent/async_executor.py` | **新建** | Phase 3 |
| `agent/scheduling.py` | **新建** | Phase 3 |
| `agent/rate_limiter.py` | **新建** | Phase 4 |
| `agent/diff_tools.py` | **新建** | Phase 3 |
| `agent/data_process_tools.py` | **新建** | Phase 3 |
| `agent/extensions/manager.py` | 修改 | Phase 1, 4 |
| `agent/tests/test_file_tools.py` | **新建** | Phase 2 |
| `agent/tests/test_web_tools.py` | **新建** | Phase 2 |
| `agent/tests/test_ext_tools.py` | **新建** | Phase 2 |
| `agent/tests/test_pdf_tools.py` | **新建** | Phase 2 |
| `agent/tests/test_software_tools.py` | **新建** | Phase 2 |
| `agent/tests/test_system_tools.py` | **新建** | Phase 2 |
| `agent/tests/stress_test_tools.py` | **新建** | Phase 5 |
| `agent/server_routes/routes_scheduling.py` | **新建** | Phase 3 |
| `.github/workflows/test.yml` | **新建** | Phase 2 |
| `config_secure.py` | 无修改（仅参考） | - |

---

*文档版本：v1.0 | 最后更新：2026-06-19 | 状态：待执行*
