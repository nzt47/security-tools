# 云枢插件化改造方案（路线 A：务实自研）

> 目标：把云枢（Yunshu）从「单体 Flask + 大 App 组件」改造为「插件化 + 插槽化 + Schema 驱动自解释 UI」的架构，
> 借鉴 DSH（DeepSeek Harness）的 Cordis 插件化设计思想，但采用**轻量自研**实现，不引入 JS 微内核。
> 本文档只描述方案与任务，**不包含任何代码改动**。

---

## 1. 现状盘点（2026-08 实测）

### 后端（Python / Flask）

| 项 | 值 |
|---|---|
| 主服务文件 | `app_server.py`（213KB） |
| 路由规模 | **约 177 处 `@app.route`**，全部直接挂在大 `app` 上 |
| Blueprint | 仅 2 个（`health_bp`、`learning_metrics_bp`） |
| 入口 | `main.py`（CLI/REPL 主循环，本次改造不动） |
| 已有基础 | 感知/认知/记忆/行动四层架构、统一单例管理（SingletonManager）、权限/安全/MCP/调度等子系统 |

### 前端（React 18 + TS + Vite + Tailwind + Zustand）

| 项 | 值 |
|---|---|
| 根组件 | `yunshu-ui/src/App.tsx`（381 行大组件） |
| 面板切换 | `useState` 布尔开关（`skillMgmtOpen` / `knowledgeOpen`） |
| 页面 | ChatPage / Knowledge / PromptLab / Home |
| 已有组件 | Chat、Mascot、Status、SkillsMgmt（含 SkillCreator）、DevConsole、VisualEditor、Observability 等 |
| 打包 | Vite + Electron（`dist-electron`），Flask 静态托管（`build:flask`） |

### 差距矩阵（对照 DSH 式架构）

| DSH 特性 | 云枢现状 | 差距 | 本方案阶段 |
|---|---|---|---|
| 前后端物理分离 | ✅ Flask JSON API + SPA | 已具备 | — |
| 插件化 / 模块化 | ❌ 177 路由挤在一个文件 | 大 | 阶段 1 |
| Slot 插槽机制 | ❌ 布尔开关切面板 | 大 | 阶段 2 |
| Profile 配置驱动 | ❌ 无 | 大 | 阶段 2 |
| Schema 自解释 UI | 🟡 SkillCreator 有雏形，未通用化 | 中 | 阶段 3 |
| 动态插件装载 | ❌ 无 | 大 | 阶段 4（可选） |

---

## 2. 改造路线（四阶段，逐级增量）

| 阶段 | 内容 | 产出 | 工作量（单人） |
|---|---|---|---|
| 阶段 1 | 后端插件化：注册表 + 按域拆分 Blueprint | `plugins/` 目录、`/api/plugins` 端点、路由路径不变 | 约 1–2 周 |
| 阶段 2 | 前端插槽化：slot registry + profile.json | 界面由配置组装，App.tsx 瘦身 | 约 1 周 |
| 阶段 3 | Schema 驱动自解释 UI | 通用 SchemaRenderer、插件面板 | 约 1 周 |
| 阶段 4 | 动态装载（可选） | 插件目录扫描 + 前端运行时刷新 | 约 3–5 天 |

**核心原则（路线 A）：**

1. **增量迁移，不重写**：每次任务只搬一部分路由/组件，路由路径与前端行为 100% 不变。
2. **回归优先**：后端每个任务跑 pytest（仓库现存 12714 项用例）；前端每个任务跑 vitest + `tsc`。
3. **不做大爆炸**：`app_server.py` 在阶段 1 结束时才瘦身，过程中保持可运行。
4. **共享依赖用「懒加载桥」**：插件模块顶层不 `import app_server`（避免循环导入），视图函数内部延迟 import，或逐步收口到 `plugins/services.py`（风格与现有 SingletonManager 一致）。

---

## 3. 任务总表（每个任务 = 一个独立可执行的提示词文件）

> 任务提示词位于 `tasks/` 目录，每个文件可直接粘贴到「新建任务」（Trae / Cursor / Claude Code 等）中执行。
> 依赖关系：同一阶段内大部分任务可并行，但标注「前置」的任务必须在前。

### 阶段 1：后端插件化

| 任务 | 文件 | 内容 | 前置 |
|---|---|---|---|
| T1.1 | `tasks/T1.1-plugin-registry.md` | 插件注册表 + `/api/plugins` 端点 + 装配器骨架 | — |
| T1.2 | `tasks/T1.2-chat-plugin.md` | 拆 chat / sessions / history / voice / news | T1.1 |
| T1.3 | `tasks/T1.3-memory-plugin.md` | 拆 context / memory / vector / windows | T1.1 |
| T1.4 | `tasks/T1.4-status-plugin.md` | 拆 health / sensors / status / heartbeat / personality / panorama | T1.1 |
| T1.5 | `tasks/T1.5-skills-plugin.md` | 拆 skills / extensions / tools | T1.1 |
| T1.6 | `tasks/T1.6-admin-plugin.md` | 拆 config / auth / audit / network-config / system-prompt / llm / search | T1.1 |
| T1.7 | `tasks/T1.7-safety-plugin.md` | 拆 safety / permission / privacy / window-consent | T1.1 |
| T1.8 | `tasks/T1.8-mcp-scheduler-plugin.md` | 拆 mcp / scheduler / schedules / tasks | T1.1 |
| T1.9 | `tasks/T1.9-system-tools-plugin.md` | 拆 workspace / filesystem / sandbox / browser / process / clipboard / web | T1.1 |
| T1.10 | `tasks/T1.10-assembler-finalize.md` | 装配器收尾、清理、全量回归、manifest 完整化 | T1.2–T1.9 |

### 阶段 2：前端插槽化

| 任务 | 文件 | 内容 | 前置 |
|---|---|---|---|
| T2.1 | `tasks/T2.1-slot-registry-core.md` | slotRegistry 核心 + profile 加载 + 单测 | — |
| T2.2 | `tasks/T2.2-app-shell-slots.md` | App.tsx 外壳改插槽：topbar / sidebar / main | T2.1 |
| T2.3 | `tasks/T2.3-panels-as-slots.md` | SkillManagement / Knowledge / DevConsole 面板插槽化 | T2.2 |
| T2.4 | `tasks/T2.4-profile-driven.md` | profile.json 驱动顺序/显隐 + 回退默认值 | T2.3 |

### 阶段 3：Schema 自解释 UI

| 任务 | 文件 | 内容 | 前置 |
|---|---|---|---|
| T3.1 | `tasks/T3.1-schema-protocol.md` | 后端插件 Schema 协议 + manifest 扩展 | T1.10 |
| T3.2 | `tasks/T3.2-schema-renderer.md` | 前端通用 SchemaRenderer + 单测 | T2.1 |
| T3.3 | `tasks/T3.3-plugin-panel.md` | 插件面板接入 `/api/plugins` + 演示插件验证 | T3.1, T3.2 |

### 阶段 4：动态装载（可选）

| 任务 | 文件 | 内容 | 前置 |
|---|---|---|---|
| T4.1 | `tasks/T4.1-backend-scan.md` | 后端插件目录扫描自动加载 + 刷新端点 | T1.10 |
| T4.2 | `tasks/T4.2-frontend-dynamic.md` | 前端运行时拉取 manifest 动态挂载 | T2.4, T4.1 |

---

## 4. 使用说明

1. **执行顺序**：按阶段顺序执行。阶段 1 内 T1.2–T1.9 相互独立（都以 T1.1 为前置），可并行；强烈建议每完成一个任务就提交一次 git（功能分支）。
2. **新建任务**：把对应 `tasks/T*.md` 的**全文**粘贴到新建任务窗口即可。每个提示词自包含：目标、设计契约、步骤、验收标准、回归要求。
3. **回归命令**：
   - 后端：`python -m pytest tests/ -x -q`（或按任务指定的子集）
   - 前端：`cd yunshu-ui && npx tsc -b --noEmit && npx vitest run`
   - 构建：`cd yunshu-ui && npm run build`
4. **冒烟测试**：启动 `app_server.py`，逐一请求 `/api/health`、`/api/sensors`、`/api/chat` 等已迁移路由，确认响应与迁移前一致。
5. **失败回滚**：任何任务若破坏现有行为，优先回滚该任务的 git 提交，再重试，不要带伤前进。

---

## 5. 最终目标形态（阶段 4 完成后）

```
后端：
  app_server.py          → 装配器（创建 app + 注册全部插件 + /api/plugins）
  plugins/
    plugin_api.py        → Plugin 协议（name/version/description/schema/blueprint）
    services.py          → 共享服务懒加载桥（可选演进）
    chat.py / memory.py  / status.py / skills.py / admin.py / safety.py /
    mcp_scheduler.py / system_tools.py
  /api/plugins           → 返回全部插件 manifest（自解释的数据源）

前端：
  src/plugins/
    slotRegistry.ts      → registerSlot / mountToSlot / SlotHost / profile
    profile.json         → { slots: { sidebar: [...], main: [...] } }
    SchemaRenderer.tsx   → 根据 JSON Schema 自动渲染表单/面板
    pluginDiscovery.ts   → fetchPlugins / reloadPlugins / 动态装载 clientSlot 模块
    public/plugins/*.js  → 插件客户端模块（register(registry) 约定，动态 import 装载）
  App.tsx                → 瘦身为「SlotHost 集合 + 少量全局状态」
```

新增功能（阶段 4 后）= 后端放一个 `plugins/xxx.py`（声明 Schema，可选 `client_slot`）
+ 前端可选放一个 `public/plugins/xxx-ui.js`（`register` 约定），点「刷新」即发现、
点「加载 UI」即挂入插槽，UI 由 Schema 自动渲染，**无需手写页面、无需发版**。

---

## 进度跟踪

> ⚠️ **2026-10-03 复核修订**：本表此前把 T2.1–T4.2 **全部**标为「✅ 完成」，而其中
> **前端侧（阶段 2 全部 + T3.2/T3.3 的宿主 + T4.2）的产物已在 `9b4aa8f5`
> （chore(cleanup): 移除 legacy 死代码链与孤儿文件）被删除**，且**没有任何消费方**。
> 也就是本仓被反复点名的第三种失败形态 —— **「文档说完成、代码已删除」**。
> 本次逐条实测（方法见下方「复核方法与证据」）后按事实重写。
> **纪律**：本表的每一行都必须能被一条命令复现；不得以交付报告的存在代替代码的存在。

### 后端（阶段 1 / T3.1 / T4.1）—— 交付且**在用**

| 任务 | 状态 | 完成日期 | 提交 | 2026-10-03 复核证据 |
|------|------|----------|------|------|
| T1.1 插件注册表 + 装配器骨架 | ✅ 完成 | 2026-08-30 | `205a478d`（代码）+ `e5231633`（文档归档） | `plugins/plugin_api.py`（Plugin / register_plugin / manifest）+ `GET /api/plugins`（app_server.py:1402） |
| T1.2–T1.9 域拆分（8 插件） | ✅ 完成 | 2026-08-30 | `af157655`（chat+admin）、`092e1d68`（skills）、`2b9cc881`（mcp/scheduler）、`2c3562f5`（system_tools）、`3fc9c7f2`（memory）、`02db10b1`（status）、`7dd427db`（safety） | `plugins/` 现存 10 个域模块：admin / admin_api / chat / experience / mcp_scheduler / memory / safety / skills / status / system_tools |
| T1.10 装配器收尾 + 全量回归 | ✅ 完成 | 2026-08-30 | `3cfb4fe4` | `python scripts/audit/contract_diff.py` ⇒ manifest 声明 199 条、漂移 0 |
| 排除项修复（前端构建 / 测试顺序污染） | ✅ 完成 | 2026-08-30 | `97c8e50f` | — |
| T3.1 后端插件 Schema 协议 + manifest 扩展 | ✅ 完成 | 2026-08-31 | `6c6269cb` | `Plugin.schema` + `_validate_schema`（plugin_api.py:8-34）+ manifest 输出 `schema` 字段；`tests/test_plugin_schema.py` 守约 |
| T4.1 后端目录扫描 + reload 端点 | ✅ 完成 | 2026-08-31 | `5e980b4f` | `plugins/loader.py`；消费方 `app_server.py:476`（启动装配）与 `:1423`（`POST /api/plugins/reload` → `loader.refresh_manifest()`） |

### 前端（阶段 2 全部 / T3.2–T3.3 / T4.2）—— **大部分已退役**

| 任务 | 原状态 | 2026-10-03 实际状态 | 依据 |
|------|--------|--------------------|------|
| T2.1 slotRegistry 核心 + profile 加载 | ✅ 完成 | ❌ **已删除** | `yunshu-ui/src/plugins/slotRegistry.ts` 与 `profile.json` 不存在；由 `9b4aa8f5` 删除 |
| T2.2 App 外壳插槽化（topbar / sidebar / main） | ✅ 完成 | ❌ **已删除** | `yunshu-ui/src/App.tsx` 不存在（当前外壳为 `WorkbenchApp.tsx` / `DetachedChatApp.tsx`） |
| T2.3 面板插槽化 + PanelSwitcher | ✅ 完成 | ❌ **已删除** | 同上（随 App.tsx 一并退役） |
| T2.4 profile 驱动组装 + 回退默认值 | ✅ 完成 | ❌ **已删除** | `profile.json` 与 `PROFILE.md` 均不存在 |
| T3.2 前端通用 SchemaRenderer + 单测 | ✅ 完成 | ⚠️ **存留，但只剩 1 个消费方** | `yunshu-ui/src/plugins/schema/SchemaRenderer.tsx` 仍在；唯一消费方是 `pages/hub/plugin-manage.tsx`（原宿主 `plugins/PluginPanel.tsx` 已随 `9b4aa8f5` 删除）。**去留属未决决策**（审计 §7 第 7 项：补第二个真实消费方，或承认其为演示件并冻结） |
| T3.3 插件面板接入 `/api/plugins` + 演示验证 | ✅ 完成 | ❌ **原宿主已删除** | `yunshu-ui/src/plugins/PluginPanel.tsx`（+ `PluginPanel.test.tsx`）由 `9b4aa8f5` 删除；能力由 `pages/hub/plugin-manage.tsx` 承接（它 import `@/plugins/schema` 的 SchemaRenderer） |
| T4.2 前端运行时发现 + 刷新（+ 动态装载进阶） | ✅ 完成 | ❌ **前端侧已删除**（后端侧仍在） | `yunshu-ui/src/plugins/pluginDiscovery.ts` 不存在；`static/plugins/demo-ui.js` 仍在，但没有前端发现机制去挂载它 —— 即"后端能扫、前端不再动态挂" |

> **结论（修订）**：**阶段 1 / T3.1 / T4.1 已交付且在用**；
> **阶段 2 全部、T3.2–T3.3 的宿主、T4.2 的前端侧已退役**。
> 因此本文件早先那句「阶段 1、2、3、4 已全部完成（四阶段改造路线全部交付）」**不成立**，
> 本次删除。
>
> **为什么会这样（审计 §4.4 第 7 条的实测结论）**：本方案默认「路线 A（插槽/配置驱动）
> 可持续、路线 B（目录扫描 + 运行时挂载）会变僵尸」。实测**相反** ——
> A 路线的落地成果变成僵尸并被整体删除，而 B 路线的产物（`plugins/demo_plugin.py` +
> `static/plugins/demo-ui.js` + 后端目录扫描）反而仍在。
> ⇒ 决定产物存亡的是**保鲜机制**（有没有消费方、有没有 CI 断言），不是 A/B 路线选择。
> 这也正是重构计划把「契约事实源唯一化 + CI 门禁」置于「代码生成」之前的理由。
>
> 阶段 1 交付说明见 `docs/DELIVERY_CLOSEOUT_REPORT_PHASE1_20260830.md`（T1.1 单独记录见
> `docs/DELIVERY_CLOSEOUT_REPORT_20260830.md`）。阶段 2/3/4 的交付说明文件仍在仓库中，
> 但它们描述的**前端产物已不存在** —— 阅读时请以上表为准。

### 复核方法与证据（可复现）

```powershell
# 1) 前端产物是否还在（预期：前 5 项 False，SchemaRenderer 与 plugin-manage True）
Test-Path yunshu-ui/src/plugins/slotRegistry.ts        # False
Test-Path yunshu-ui/src/plugins/profile.json           # False
Test-Path yunshu-ui/src/plugins/pluginDiscovery.ts     # False
Test-Path yunshu-ui/src/plugins/PluginPanel.tsx        # False
Test-Path yunshu-ui/src/App.tsx                        # False
Test-Path yunshu-ui/src/plugins/schema/SchemaRenderer.tsx   # True
Test-Path yunshu-ui/src/pages/hub/plugin-manage.tsx         # True

# 2) 是谁删的（预期：三条都指向 9b4aa8f5）
git log --oneline -1 --diff-filter=D -- yunshu-ui/src/plugins/slotRegistry.ts
git log --oneline -1 --diff-filter=D -- yunshu-ui/src/plugins/pluginDiscovery.ts
git log --all --oneline -- "*PluginPanel*"

# 3) 是否还有消费方
#    预期：slotRegistry / pluginDiscovery 无输出；profile.json 只命中 1 处**注释**
#    （pages/hub/plugin-manage.tsx:7 写着"不迁移 SlotHost/profile.json 插槽宿主体系"）
#    —— 注释不算消费方，但如实列出，免得下一个人以为"还有引用"。
git grep -n "slotRegistry"     -- yunshu-ui/src   # 无输出
git grep -n "pluginDiscovery"  -- yunshu-ui/src   # 无输出
git grep -n "profile.json"     -- yunshu-ui/src   # 仅 plugin-manage.tsx:7 注释

# 4) 后端侧是否在用（预期：有命中）
git grep -n "plugins import loader\|refresh_manifest" -- app_server.py
```

> **配套机制**：`tests/unit/test_legacy_surface_inventory.py` 把遗留 SSR 页面清单做成
> **只允许收缩**的守卫；`plugins/plugin_api.py` 的 `Plugin.routes` 亦有对拍工具
> `scripts/audit/contract_diff.py` 守着。**"完成"必须由代码/CI 断言证明，而不是本表的一个勾。**
