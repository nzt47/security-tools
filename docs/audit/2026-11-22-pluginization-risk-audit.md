# 云枢「一切皆插件」风险审计（2026-11-22）

> 审计对象：`C:/Users/Administrator/agent`（master `a118efe31`，部署三处一致）。
> 结论先说：云枢**不是**「一切皆插件」，而是「模块化单体 + 有限的扩展面」；
> 文章的五类代价里，**安全边界**与**版本契约**是真实短板，性能/调试/产品一致性大部分被现有机制压住；
> 横切体验有一处真实接缝。本文记录审计结论与 2026-11-22 P0/P1 第一批落地（见 §10）。

## 0. 结论摘要

1. **核心 UI 没有文章所说的「两个设置、两套布局」**：4 个桶 / 25 个叶子，导航与页头由宿主集中管理，
   旧深链走 `LEGACY_NAV_KEY_MAP`；页面合并（账号、实例、消息、日志审计、调度开关）已经落地。
2. **真正的高危在第三方插件包**：`/api/extensions/install` 与 `/toggle` 只校验共享令牌、不经人工审批；
   装进来的 Python 代码在主进程 `import`，与宿主同权限；依赖直接 `pip install` 进宿主环境。
3. **次高危是「假沙箱 + 版本契约装饰化」**：`PluginSandbox` 是进程内直调、资源上限从未执行；
   插件 `version` 没有 API 版本/兼容矩阵；工具 `result_schema` 只有 15/101。
4. **建议先做 P0**（默认关闭第三方插件包 + 安装/启用接审批 + 依赖隔离 + 沙箱诚实化），
   再做 P1（API 版本、健康、bulkhead、横切 e2e）。**不建议**现在做「插件化 UI / 进程化内核」的大改。

## 1. 审计方法

| 手段 | 内容 |
|---|---|
| 读文 | 22 行原文逐条拆成「五类结构性代价 + 治理 + 横切 + 边界判断」 |
| 读码 | `plugins/`、`agent/extensions/`、`agent/tools/`、`agent/timeout_budget.py`、`app_server.py`、`yunshu-ui/src/plugins/`、`pages/hub/plugins/`、`tools/`、`engine/` |
| 实测 | 运行中 `/api/plugins`、`/api/tools/config`、进程指标、启动日志、审计卡 |
| 测试 | 插件清单/装饰器/鉴权基线/通道桥 52 passed；当前日志 0 超时 / 0 Traceback |

## 2. 文章论点拆解（可检验化）

文章的五类代价：性能、调试、兼容性、安全、一致性；另加治理（版本/供应链/生态）与横切（功能跨插件无 Owner）。
其中「63%」「12%」等数字**没有出处**，本文不拿它们当证据，只检验结构与机制。
Java 的 Metaspace/Full GC 也不可直接类比 Python；对应的真实问题是内存、GIL、线程与子进程隔离。

## 3. 云枢的真实形态（不是「一切皆插件」）

| 扩展面 | 实测数量 | 加载方式 | 第三方？ | 隔离边界 |
|---|---|---|---|---|
| 后端 HTTP 插件 | 14 个 / 226 条路由（占 518 条路由的 44%） | 启动时扫描 `plugins/*.py`，`import` 后注册 Blueprint | 目前全是内置 | 主进程内 |
| 工具 | 101 个 | `data/tool_definitions/*.yaml` + `@_tools.register` | 内置 + 少量运行时登记 | 主进程内，`call()` 统一闸门 |
| 技能 | 55 个目录 / 52 条文件轨 | 文件轨 + `SKILL.md`；脚本走 `SkillExecutor` 子进程 | 有外部入轨 | 指令型为主，脚本子进程 |
| 扩展包/MCP/通道 | 4 MCP / 2 通道 / **0 插件包** | `ExtensionManager` 运行时安装 | 可第三方 | 插件包主进程内；MCP 多为外部进程 |
| 前端功能 | 构建期 `src/features/*` 静态发现 | `import.meta.glob(eager)` | 否 | 与主包同构建、同守卫 |

⇒ 云枢的**核心循环、可靠性、持久化、UI/导航**都是一体化的，只有工具/技能/接入面开放扩展。
这正是文章最后推荐的「分层插拔」折中，而不是 Eclipse/OSGi 式的全插件内核。

## 4. 逐维审计

### 4.1 性能与资源隔离：**部分有（潜在风险，当前未发作）**

**事实**
- 14 个插件、226 条路由、101 个工具全部在同一个 Python 进程；工作线程是 waitress `threads=16`（`app_server.py:2978`）。
- 有全局并发闸门（8 个同时在跑，`app_server.py:409`），但**没有按插件分的线程池/并发配额**；
  一条慢插件路由可以占住 waitress 线程；8 条慢请求占满并发闸门（waitress 共 16 线程）就会让新请求排队。
- 工具 handler 的 1800s 兜底是 **daemon 线程 + 只解除等待、不杀执行体**（`agent/timeout_budget.py:477-538`，注释已如实声明）。
- `PluginSandbox` 有 `ResourceLimits`（CPU/内存/磁盘/30s）但**从未被执行**；`execute_in_sandbox` 直接 `func(*args)`，
  环境变量还是 `os.environ.copy()`（`agent/extensions/sandbox.py:108-168`）。全仓没有生产调用方，等于死代码。
- 当前进程：私有内存 2.1 GB / 工作集 974 MB / 65 线程 / 2008 句柄；当前日志 0 次工具超时。

**判定**：不是文章里的 Java 插件泄漏，但「进程内 + 无配额 + 超时不可杀」三件事同时成立；
当前 0 个第三方插件包，所以没有现场故障，属**余量为零**的结构性风险。

### 4.2 调试与故障隔离：**轻微有（大部分已被现有机制抵消）**

**做得好的**
- 工具调用有 `trace_id`、健康计数、审计链、TLM 追踪；交接卡 87,810 条审计 / 0 断点。
- 单插件 `import` 失败被 `except` 隔离，不阻断启动（`plugins/loader.py:99-108`）；单条路由异常由 Flask 隔离为 500。
- 鉴权覆盖基线只剩 1 条（`POST /api/auth/login`，见 `reports/auth_coverage_baseline.json`），不是「插件裸奔一片」。

**缺口**
- **加载失败只 `print`**（`plugins/loader.py:106-107`），没有结构化日志/审计/健康位；
  `/api/plugins` 的 manifest 只有 name/version/description/schema/routes/client_slot，**没有 status/load_error**（`plugins/plugin_api.py:168-184`）。
- 插件页「刷新」成功只回「插件清单已刷新」（`plugin-manage.tsx:240-243`），但 Flask 已注册的 Blueprint 不能热加/热删，
  新插件路由要重启才生效（`plugins/loader.py:16-18`）；UI 没有「需重启」提示，容易出现「刷新成功但路由是空的」。
- 没有插件级熔断/自动摘除：某个插件路由持续失败，仍会继续接流量；只有全局健康页。

### 4.3 版本与兼容：**明显有（最值得补的「契约」）**

**事实**
- `Plugin.version` 只是展示字段（`plugins/plugin_api.py:25`）；全仓 14 个插件是 1.0.0 / 0.6.0，`register_plugin` 不校验宿主版本、不校验 API 版本。
- 插件直接 import 宿主内部模块：`agent.network_config`、`agent.config_validation`、`agent.api_envelope`、`agent.ingestion.models`、`agent.build_info` 等；
  宿主重构即破坏插件，且失败只 `print`。
- `DependencyManager` 是**死代码**（全仓无调用方）；它只解析 `==`（`dependency_manager.py:103-110`），
  版本检查异常时 `return True`（fail-open，`:207-209`）。真正安装走 `InstallEngine.install_pip_package`。
- `install_pip_package` 不检查 `returncode`，永远 `return True`（`agent/extensions/installer.py:249-261`）；
  依赖失败只在 `plugins_installer.py:197-202` 记 warning，插件仍被记为安装成功。
- 一律 `pip install` 进**宿主 Python 环境**（`installer.py:254-256`），没有 venv/锁/哈希/兼容矩阵；
- GitHub 安装优先 `gh`/`git clone` 默认分支，失败再拉 `zipball/main`（`installer.py:83-167`），都不固定 commit；URL 的 zip/tar 直接 `extractall`（`:187-202`），没有成员路径校验。
- 工具 `result_schema` 只有 15/101；其余 86 个没有输出契约。工具 YAML 的 `version` 100/101 是 1.0.0（另一个 1.1.0），属装饰。

**判定**：文章说的「插件 A 依赖 B v1.2、宿主同时有 v1.1/v1.3」在云枢的对应形态是
「插件依赖宿主内部符号 + 依赖全局 pip 环境」；一旦装第三方包，确实可能把宿主依赖降级/冲突，且失败不可见。

### 4.4 安全与供应链：**严重有（本文最高优先级）**

**一条可复现的攻击链（当前代码即可走）**
1. 在插件市场搜到一个 GitHub 仓库（市场按 `topic:yunshu-plugin` 搜索，`agent/extensions/market.py`），点「安装」；
   UI 直接 `POST /api/extensions/install {type:plugin, source:github:...}`（`plugin-manage.tsx:330-349`）。
2. 该端点只要求 `@_require_token`（`plugins/skills.py:590-609`），**不经过 `tool_gate` 的 L3 审批**；
   对比：同一个能力走工具 `ext_install` 是 `needs_approval`（L3，人工审批）。
3. 安装器把仓库复制到 `agent/data/extensions_packages/plugins/`，读取 `plugin.json` 的 dependencies，
   直接 `pip install` 进宿主环境（`plugins_installer.py:93-224`）；恶意包的 build backend 在这里就能执行。
4. 用户在「已安装插件包」点「启用」（`installed-extensions.tsx:94-102`，同样只校验令牌），
   `load_plugin` 把插件目录 `sys.path.insert(0, ...)` 后 `importlib.import_module`（`plugins_installer.py:226-260`）——
   **第三方 Python 代码在宿主主进程执行，与宿主同权限**。
5. 该代码可以直接 `open(".env")`、读 `data/`、发网络请求；宿主给 `read_file` 加的凭据边界（`.env`/`.pem`/`.ssh`，
   `agent/tools/file_tools.py:158-219`）对它**完全无效**——它根本不走工具层。

**其他安全事实**
- 全仓安装链路没有对插件包/技能包做签名、哈希、来源白名单校验；市场也不做人工复核。
- 插件 UI：`client_slot` 用动态 `import()` 加载 `/static/plugins/*.js`（`extensionHost.ts:44,60-63`），
  在宿主页面同源执行；API 令牌存在 `localStorage.yunshu_api_token`（`apiToken.ts:18-31`），插件 JS 可读。
  当前只有 demo 声明 client_slot，且该机制目前只服务仓库内 `plugins/`，但「同源任意 JS」这个口子已经存在。
- CSP 只下发 `Content-Security-Policy-Report-Only`（`app_server.py:697-698`），不阻断；`X-Frame-Options: DENY` 已下发。
- `PluginSandbox` 是**假沙箱**：进程内直调、`os.environ.copy()`、路径判断只是前缀黑名单（`sandbox.py:281-291`）；
  这比没有沙箱更危险，因为它会让人以为有隔离。

**判定**：文章说「插件与宿主同进程同权限，可摸 Shell/文件/环境变量/密钥」——云枢的**第三方插件包**完全符合；
区别只是当前装了 0 个，且入口藏在「插件与扩展」页。

### 4.5 产品一致性：**核心无，插件面有（当前仅 demo）**

**核心 UI 的防线**
- 导航是单一事实源 `HUB_NAV`（`hubNav.tsx:124-225`），4 桶 25 叶；旧 key 全进 `LEGACY_NAV_KEY_MAP`（`:344`），有测试。
- 页头/字号/按钮/颜色有 `ui:check` 守卫；量具基线 `raw_heading 70`、`css_hardcoded_color 0`；
- 已合并的同源页面：账号与权限、实例与网络、消息与通道、日志与审计、调度与开关；没有文章里的「两个设置」。

**插件面的法外之地**
- `ui:check` 的量具只扫 `yunshu-ui/src/**`（`scripts/audit/ui_primitive_convergence.py:24,39`），**不扫 `public/plugins/*.js`**。
- demo 插件 UI 就用了内联样式、`fontSize: 15`、`color: var(--text-muted, #666)`（`public/plugins/demo-ui.js:20-24`），
  与全站 token/原语规范不一致；它被直接 `createElement` 进宿主 React 树（`plugin-manage.tsx:526`）。
- 概念同名不同契约：后端 `plugins/plugin_api.Plugin`、扩展包 `Plugin`、前端 `src/plugins` 都叫「插件」，
  用户在「插件与扩展」页看到的是两套生命周期（manifest 插件 vs 已安装插件包）。

**判定**：核心产品一致性比文章案例好；插件 UI 是唯一的「法外之地」，当前只有 demo，属于**结构性风险**而非现网问题。

### 4.6 治理与生态：**部分有**

- **没有公开技能市场**：55 个技能是本地文件轨，双轨 52/54、skill.md 0 error / 3 warning；文章说的「Skill Hub 几十万垃圾上传」不成立。
- **但有一个小型插件市场**：GitHub `topic:yunshu-plugin` 搜索 + 一键安装，无签名/无复核/无 quarantine；
  这与 NPM 的供应链问题同族。
- 工具新增有 5 处连锁（tool_index / capability_manifest / BM25 / 路由基线 / 冲突 τ），但 `generate_tool(persist=True)`
  只写 `agent/tools/custom/*.py`（`tool_generator.py:250-321`），不写 `data/tool_definitions/*.yaml`，
  除非另走 `ingestion/register_draft` 全链——这是「动态生成」绕过能力清单的一个旁路。

### 4.7 横切体验：**有一处真实接缝**

文章的例子是「后台任务完成 → 推送手机」跨 Jobs/Session/通知/Workflow。云枢的对应链路：
- `task_scheduler` 完成/失败会 `dispatch_event` 到 `channel_bridge`（`agent/task_scheduler.py:564-599`）；
- 告警到通道也有 `alarms/notify`；`notify` 工具写本地台账；这条主链是通的。

**但成功通知有三处割裂**：
1. 任务要写 `notify_on_complete: true` 才会在成功时外发（`task_scheduler.py:585`）；
2. 调度 UI 的新建表单只发 `name/action/interval/cron`（`scheduler.tsx:104`），**没有这个开关**；
3. 消息与通道页只有「定时任务完成」事件总开关（`messages-channels.tsx:147`），仍以任务字段为前置门。

更典型的是**同一个「新建调度任务」有两个入口、两种诚实度**：
- LLM 工具 `schedule_task` 在 action 未实现时**直接拒绝**（`code_tools.py:209-230`）；
- UI 的 `POST /api/schedules` 不做这个判断，照常 201（`plugins/mcp_scheduler.py:378-405`）；
  实际执行时 `agent/scheduling.py:488-520` 把 `success` 记为 False 并写「已触发但动作未执行」，但 `log_execution` 的 record 只有 `success/result`（`:607-620`）——注释声称的 `executed` 字段**并没有写**，消费者只能读文案。
用户先看到「创建成功」，之后才发现是空跑——这正是文章说的「完整体验没有 Owner，只有接缝」。

## 5. 评分矩阵

| 维度 | 文章病灶 | 云枢现状 | 严重度 | 当前可触发？ |
|---|---|---|---|---|
| 性能 | 进程内泄漏/GC/CPU 占满 | 进程内 + 无配额 + 超时不可杀；但 14 插件 / 0 第三方包 | 中 | 需要坏插件或慢路由 |
| 调试 | 调用链跨 N 插件/日志分散/单插件拖垮 | trace/审计/TLM 强；加载失败只 print、无健康位 | 低-中 | 掉一个坏插件即触发 |
| 兼容 | 版本冲突/依赖地狱 | version 装饰化、无 API version、全局 pip、失败被吞 | 高 | 装第三方包即可能 |
| 安全 | 同进程同权限/摸密钥 | 第三方插件包主进程 import；安装/启用仅令牌；无签名；假沙箱 | **严重** | 一键安装 + 一键启用 |
| 一致性 | 双设置/布局争抢/弹窗关不掉 | 核心 UI 集中式且已合并；插件 UI 绕过设计系统 | 低（核心）/ 中（插件面） | 仅 demo |
| 治理 | 市场垃圾/供应链投毒 | 无公开技能市场；插件市场无复核；动态工具旁路清单 | 中 | 市场安装 |
| 横切 | 功能跨插件无 Owner | 调度→通道已通；成功通知/任务创建 UI 割裂 | 中 | 用户建任务即遇到 |

## 6. 改正方案

### P0（建议本周做，1–2 天量；都可回滚）

**P0-1 默认关闭第三方插件包入口，或把安装/启用接入同一审批门**
- 加 `CP_PLUGIN_PACKAGES_ENABLED`（默认 0）进 `agent/settings/registry.py`；
  `ExtensionManager.install/uninstall/toggle/configure` 对 `ExtensionType.PLUGIN` 直接返回 403/`PLUGIN_PACKAGES_DISABLED`；
  UI 的「从市场安装」「启用」置灰并说明「第三方插件包隔离未完成，暂不开放」。
- 若 Owner 要保留能力：改为调用 `agent.tool_approval.request_approval`（与 `ext_install` 同门），
  人工批准后单次放行；不要只靠 `FLASK_API_TOKEN`。
- 验收：默认配置下 `POST /api/extensions/install {type:plugin}` → 403 + 审计；0 个已装包，业务零影响。
- 回滚：环境变量置 1。

**P0-2 依赖安装与宿主解耦、失败必须可见**
- `install_pip_package` 检查 `returncode`，失败返回 False；`install_dependencies` 失败 ⇒ 插件安装返回失败 / 状态 ERROR，不再 warning 后继续。
- 短期禁止向宿主环境 `pip install`：用 `--target <plugin_dir>/.deps` 或要求依赖已预装；长期每插件独立 venv。
- 验收：模拟 pip 失败，安装返回 `ok=false`；安装前后 `pip freeze` 不变；插件状态 ERROR 可查。

**P0-3 沙箱诚实化**
- 短期：删除/标注 `PluginSandbox` 为「未接线实验」，在文档和 UI 里不再暗示有隔离；加一条测试断言无生产调用方。
- 正解：`load_plugin` 不再在主进程 `import`，改为子进程 + JSON-RPC（stdin/stdout），子进程带超时、资源上限、最小环境变量；
  插件工具调用通过 RPC 回传。工程量 1–2 天，但这是唯一真正的边界。

**P0-4 插件 UI 先停用或 iframe 隔离**
- 加 `CP_PLUGIN_CLIENT_UI_ENABLED`（默认 0），关闭 `registerPluginModule` 的自动挂载；
- 或改成 `<iframe sandbox="allow-scripts">`（**不加 allow-same-origin**）+ `postMessage`，令牌不进 iframe；
- 把 `public/plugins` 纳入 `ui:check` 或单独守卫，禁止内联样式/硬编码颜色。

### P1（1–2 周）

1. **插件 API 版本与契约**：`Plugin.api_version` + 宿主 `PLUGIN_API_VERSION`；`plugin.json` 校验；不兼容拒绝加载并在 manifest 里显示；
   给高风险工具补 `result_schema`（至少 L2/L3 的 20 个）。
2. **插件健康与可观测**：manifest 增加 `status/load_error/load_ms/last_error/route_count`；
   加载失败进审计 + 告警；插件级 trace tag；连续失败自动停用/熔断。
3. **Bulkhead**：每插件独立线程池/并发配额 + 路由超时；不可信工具 handler 改子进程硬超时，避免 daemon 线程堆积。
4. **横切 e2e**：调度 UI 增加 `notify_on_complete` 勾选；`/api/schedules` 与 `schedule_task` 共用「action 是否实现」判断；
   一条 e2e 用例「建任务 → 成功 → 通道收到」用假通道验证。
5. **市场准入**：固定 commit/tag、sha256、签名/allowlist、安装前展示权限与依赖 diff；禁止 `main` 直装。

### P2（长期，按需）

- 插件进程化/能力令牌（文件、网络、环境变量白名单）；每插件 venv；
- 插件 API 稳定版 + 弃用策略 + 兼容矩阵；
- 市场 quarantine/签名/下架机制；
- 若保留插件 UI：宿主 design tokens + 原语白名单，或 iframe 沙箱。

## 7. 不建议做的

- **不要把核心 UI/导航也插件化**：云枢现在的集中式 IA 正是文章案例里「两个设置」的解药；
- 不要为 14 个第一方插件引入 OSGi/ClassLoader 式内核；
- 不要在隔离/签名完成前扩大插件市场；
- 不要把「插件数量」当目标——文章最后一句是对的：插件是止痛药，不是主食。

## 8. 验收命令（落地 P0 后）

~~~text
python -X utf8 -m pytest tests/unit/test_plugin_manifest_derivation.py tests/unit/test_plugin_decorator_unification.py \
  tests/unit/test_auth_coverage_baseline.py tests/unit/test_channel_bridge.py -q
# 新增：test_plugin_package_gate.py / test_plugin_dependency_install.py / test_plugin_ui_isolation.py
cd yunshu-ui && npm run ui:check
# 默认配置下：
#   POST /api/extensions/install {type:plugin,source:github:x/y} -> 403 PLUGIN_PACKAGES_DISABLED
#   安装前后 pip freeze 不变；/api/plugins manifest 带 status/load_error
~~~

## 9. 本轮实测数据（2026-11-22 20:53）

| 项 | 读数 |
|---|---|
| 部署 | master `a118efe31`；backend/frontend 均 `a118efe31`；bundle `index-PNfGxVFf.js`；pid 15140 |
| 插件 | 14 个 / 226 条路由；1 个 client_slot（demo）；版本 1.0.0 / 0.6.0 |
| 工具 | 101 个；risk critical 6 / high 15 / medium 28 / low 52；needs_approval 13；停用 6；result_schema 15 |
| 路由 | 518 条（UI 457 / CLI 15 / runtime 12 / unreferenced 34） |
| 技能/扩展 | 55 个技能目录；扩展台账 4 MCP / 2 通道 / 3 技能 / 0 插件包 |
| 进程 | 私有 2.1 GB / 工作集 974 MB / 65 线程 / 2008 句柄；启动 19:35:31 |
| 日志 | 当前 spawn 日志 0 超时 / 0 Traceback / 358 WARNING |
| 治理 | 审计 87,810 条 / 0 断点；鉴权基线仅 1 条（login）；`CP_API_AUTH_MODE=enforce_all` |
| 安全面 | code scanning 0 / dependabot 0 / open PR 0；CSP 仅 Report-Only |

## 10. 2026-11-22 落地记录（P0/P1 第一批）

> 本节是本轮实际改动的验收记录；第 6 节的方案保留，便于对照“做了什么、没做什么”。

| 项 | 实现 | 验收 |
|---|---|---|
| P0-1 插件包默认关闭 | `CP_PLUGIN_PACKAGES_ENABLED=0`；HTTP `install/toggle` 对 plugin 返回 403；installer 内部第二道门 | `test_plugin_package_gate` / `test_extensions_api` 403 用例 |
| P0-2 依赖隔离/失败可见 | `pip --target <plugin>/.deps`；检查 returncode；依赖失败即安装失败；单文件插件拒绝依赖 | `test_plugin_dependency_install` |
| P0-3 沙箱诚实化 | `PluginSandbox` 标注“未接线实验，不是安全边界”；守卫禁止生产 import | `test_plugin_sandbox_not_wired` |
| P0-4 插件 UI 默认关闭 | `CP_PLUGIN_CLIENT_UI_ENABLED=0`；manifest 下发 `plugin_client_ui_enabled`；UI 不自动 import | plugin-manage “安全默认”用例 |
| P1-1 API 版本 | `Plugin.api_version`（宿主 `PLUGIN_API_VERSION=1`）；不匹配拒绝注册 | `test_plugin_manifest_health` |
| P1-2 健康可见 | manifest 增加 `status/load_error/load_ms` + `load_failures`；loader 结构化日志/记录；UI 顶部提示 | `test_plugin_loader` / `test_plugin_manifest_health` |
| P1-3 每插件 bulkhead | `CP_PLUGIN_MAX_CONCURRENT`（默认 4）；`app_server` 按 blueprint 非阻塞配额，满即 429 | `test_plugin_bulkhead` |
| P1-4 调度诚实度 | `/api/schedules` 对未实现 action 返回 400 NOT_IMPLEMENTED（与工具同口径）；history 增加 `executed`；UI 提示 | `test_scheduler_honesty` |
| P1-5 安装白名单 | `CP_PLUGIN_INSTALL_ALLOWLIST` 精确匹配；空 = 拒绝；`pip:` 直装一律拒绝 | `test_plugin_policy` / `test_plugin_package_gate` |
| P1-4 横切通知 | 系统命令任务支持 `notify_on_complete` 并持久化/重启保留 | `test_scheduler_notify_plumbing` |
| P2-A 进程隔离（第一批） | 插件包改独立子进程 + JSON-RPC；超时硬杀；最小环境变量白名单；`CP_PLUGIN_PROCESS_ISOLATION=1` / `CP_PLUGIN_PROCESS_TIMEOUT_SEC=30` | `test_plugin_process_isolation` |
| P2-B 来源固定 + L3 契约 | `github:owner/repo@ref` 固定分支/标签/commit（白名单仍按 base 仓库）；10 个 tracked L3 工具补 `result_schema`（require `ok`） | `test_plugin_source_pin` / `test_l3_result_schema` |

**实测（2026-11-22 22:20，master 87e8c29bc / bundle index-hPBimolB.js / pid 20320）**

- 三处一致：backend/frontend 均 `87e8c29bc`；`/api/plugins` host 显示
  `plugin_packages_enabled=false`、`plugin_client_ui_enabled=false`、`plugin_api_version=1`。
- `POST /api/extensions/install {type:plugin}` → **403 PLUGIN_PACKAGES_DISABLED**；
  `POST /api/extensions/toggle {type:plugin,enabled:true}` → 同样 403。
- `POST /api/schedules {action:run}` → **400 NOT_IMPLEMENTED**。
- 真机（1440×900，`.local/probe_plugin_safe_default_1223.py`）：插件包横幅可见、
  市场按钮 disabled、demo 插件界面显示“已停用”；截图 `.ui_shots/PLUGIN_SAFE_DEFAULT_1440.png`。
- 测试：前端 **200 files · 1534 tests**；指定 pytest + 新增套件 **374 passed**；`npm run ui:check` exit 0。
- 【实测发现】本机 `data/ui_settings.json` 里 `CP_SCHEDULER_ACTION_EXECUTION=true`（历史误开）。
  仅靠环境开关会让工具/API 继续空跑；本轮加 `ACTION_EXECUTION_IMPLEMENTED=False` 的代码事实，
  开关在代码接线前**不生效**，实机已回到 400。这条也说明“开关中心里的值”不能替代“代码确实实现了”。

**明确未做（不是漏，是不该现在做）**

- **进程边界已做，OS 级沙箱未做**：插件包现在跑独立子进程（超时硬杀 + 最小 env），但仍是当前用户身份，能读当前用户能读的文件；文件系统/网络沙箱（AppContainer/seccomp/容器）是 P3。
- **签名/commit pin**：需要密钥管理与发布流程；本轮先做“默认关 + 精确白名单”，不假装有签名。
- **工具 `result_schema` 扩面**：86/101 没有输出契约；猜 schema 会产生新的“假契约”，比没有更坏。建议按高风险工具逐个补。
- **插件路由硬超时**：waitress 无每请求超时；本轮只做并发 bulkhead（防吃光线程）。硬超时需子进程/网关层，放 P2。
- **插件 UI iframe**：本轮默认关闭；iframe 隔离与 postMessage 契约放 P2。

> 本报告第 10 节是本轮实际落地项；未落地项（进程隔离、签名、result_schema 扩面、路由硬超时、iframe）仍按 P2 管理。第三方插件包入口现在是「默认关闭 + 白名单」，不等于已完成隔离。
>
> **2026-11-23 续**：P2 剩余五项的优先级对比与四份方案（签名/密钥、AppContainer/Docker、路由硬超时、schema L2）见
> `docs/audit/2026-11-23-plugin-p2-decision.md`。Owner 已拍板并落地：
> **签名已完整接线**（`plugin_signature.py` + `scripts/sign_plugin_package.py` + 安装暂存/加载复验 +
> `CP_PLUGIN_SIGNATURE_MODE`，受信表为空，包总开关仍默认关）；**AppContainer 已做可行性 PoC**
> （文件/网络/env 三层隔住，未接生产）；**插件路由 p99 已实测**（无持续慢路由，bulkhead 保留）。
> AppContainer 生产适配已做（`appcontainer.py` + `CP_PLUGIN_SANDBOX=appcontainer`，默认 off；
> 不可用 fail-closed）。仍未做：Job Object 资源上限/卸载清理、226 条内置路由硬超时、
> result_schema L2、插件 UI iframe。
