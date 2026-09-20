# CapabilitySpec 规范（TASK-04 · 能力规格正式化）

> **状态**：已落地（2026-09-20）
> **权威声明（D1 单一真相源）**：工具 → `data/tool_definitions/*.yaml`；技能 → `data/skill_callability.yaml`
> **派生视图（禁止手改）**：`data/capability_manifest.json` + `docs/rfc/云枢能力清单盘点表.md`
> **事实判定器**：`agent/lines/location.py`（`location`）+ `agent/lines/callability.py`（可调用性三层）
> **字段定义单点**：`agent/lines/models.py::ToolMeta`（`CapabilitySpec` = `ToolMeta` 的完整版，**不新建第二个类**）

---

## 1. 为什么把 `ToolMeta` 扩展成 `CapabilitySpec`，而不是新建一个类

`ToolMeta` 已被 `agent/lines/`、`agent/hitl/`、`agent/tool_gate.py`、`agent/rate_limiter.py`、
`agent/subagent/toolset.py` 消费。新建 `CapabilitySpec` 会让"能力定义"出现**两处**，
违反 D1（单一真相源），也是 TASK-04「不通过」清单里的第一条。

⇒ **做法**：扩展 `ToolMeta`，让它成为 v1.4 §5.1 意义上的完整 `CapabilitySpec`。
新增字段**全部可选、全部有默认值**（D2），`to_dict()` 只增不减。

**E1 证明（不存在第二个能力定义结构）**：

```powershell
# 全仓只有一个 dataclass 承载能力定义
git grep -n "class ToolMeta\|class CapabilitySpec" -- agent
# → agent/lines/models.py:56:@dataclass(frozen=True) / :57:class ToolMeta
#   无第二处
```

---

## 2. 字段表（v1.4 §5.1 对齐）

| 字段 | 类型 | 必填 | 来源 | 进 prompt | 与 v1.4 §5.1 的关系 |
|---|---|---|---|---|---|
| `name` | str | ✅ | YAML `name` | 否（`description` 进） | = `name`（v1.4 无此字段，用 `capability_id`） |
| `capability_id` | str（派生属性） | ✅（派生） | 派生：`tenant_id:namespace:name@version` | 否 | = v1.4 `capability_id`；**`tool_name` 保留为别名**（D2） |
| `tool_name` | str | ✅ | 清单字段（= `name`） | 否 | **别名/短键**：`agent/lines`、`routes_agent_lines.py`、UI 都在用，不能删 |
| `kind` | enum（派生属性） | ✅（派生） | 派生：`api→tool`、`script→skill`，其余同 `tool_type` | 否 | = v1.4 `kind: tool \| skill`（归并规则见 §3） |
| `tool_type` | enum | ✅ | YAML `tool_type` | 否 | 仓库既有四形态（`tool/skill/api/script`），实测 **91/91 全是 `tool`** |
| `location` | enum `local\|remote` | ✅ | **事实判定**（`agent/lines/location.py`）；YAML `location` 为**钉住值** | 否 | = v1.4 `location`（**本任务核心新增**） |
| `location_source` | enum | ✅（派生） | 派生：`declaration` / `executor_boundary` / `registry_source` / `skill_chain` / `default` | 否 | v1.4 无；本仓库为"判定可追溯"新增 |
| `location_evidence` | list[str] | ✅（派生） | 派生：逐条可核的事实说明 | 否 | v1.4 无；**不接受黑箱结论** |
| `location_confidence` | `high\|low` | ✅（派生） | 派生：`remote` 恒 high；`local` 且有 unresolved 调用点为 low | 否 | v1.4 无；**诚实标注判定强度** |
| `owner` | enum | ✅ | YAML `owner`，缺省 `builtin` | 否 | = v1.4 `owner`（`builtin/local-installed/tenant-installed/marketplace`） |
| `version` | str | ✅ | YAML `version`，缺省 `1.0.0` | 否 | = v1.4 `version`（实测 91/91 都有值，但**治理未启动**：90 个 `1.0.0`） |
| `tenant_id` | str | ✅ | **派生**（服务端 workspace-hash）；清单里占位 `default` | 否 | = v1.4 `tenant_id`；见 §6 |
| `namespace` | str | ✅ | YAML `namespace`，缺省 `yunshu` | 否 | = v1.4 `namespace` |
| `registry_source` | enum `global\|planning` | ✅ | 派生：全局注册表 / 第二套 `planning.ToolRegistry` | 否 | v1.4 无；用于让"同名两来源"**可见**（§2.7） |
| `input_schema` | dict\|null | 可选 | YAML `schema`（**别名，不改名**） | 是（转成 tool defs） | = v1.4 `input_schema` |
| `output_schema` | dict\|null | 可选 | YAML `output_schema` / `result_schema` | 否 | = v1.4 `output_schema`；`result_schema` 是同一件的别名 |
| `manifest_version` | int | 可选（默认 1） | YAML `manifest_version` | 否 | = v1.4 `manifest_version`（首期恒 1） |
| `signature` / `source_trust` / `compatibility` / `semver_policy` | str | 可选（默认空） | YAML 同名键 | 否 | = v1.4 同名；**首期全部可选**（v1.4 §1.3 自述"不强制 SR 签名与 SBOM，但预留接口"） |
| `health` | str | 可选（默认空） | 预留 | 否 | = v1.4 `health: HealthState`；**核实结论：当前不可被 Registry 消费，需 TASK-05 实现**（`agent/health/` 存在，但产出的是探针文本/日志，无结构化 `HealthState` 供消费） |
| `plane` / `effect` / `risk` | enum | ✅ | YAML 同名 | 否 | 已有，同名同值域，**无需改** |
| `permission_level` | enum | ✅ | YAML `permission_level`（缺省由 plane/effect/risk 派生） | 否 | 已有，同名同值域 |
| `callable_mode` | enum `auto\|required\|manual` | ✅ | YAML `callable_mode` | 否 | = v1.4 `llm_callable_mode` |
| `llm_callable` | bool（**派生**） | ✅（派生） | 派生：`llm_visible && llm_invokable` 的既有等价物（`agent/lines/callability.py::judge`） | 否 | v1.4 拆成 `llm_visible`/`llm_invokable`；本仓库做**别名映射**（§4） |
| `trigger` | enum `model\|system\|human\|none` | ✅（派生） | 派生（`judge`） | 否 | ≈ v1.4 `callable_by`（缺 `service_account`，见 §3 差距） |
| `internal` | bool | ✅ | YAML `internal` | 否 | v1.4 无；仓库既有（内部执行体不进可见集） |
| `sandbox_allowed` | bool | ✅ | YAML `sandbox_allowed` | 否 | v1.4 无（TASK-07 才有消费方） |
| `deprecated` | bool | ✅ | YAML `deprecated`（实测全 false） | 否 | = v1.4 `deprecation`（**淘汰机制未启动**） |
| `aliases` | list[str] | 可选（默认空） | YAML `aliases` + 注册期冲突记录 | 否 | v1.4 无；让"静默改名"变成可见别名 |

### 与 v1.4 §5.1 的差异清单（逐条说明为何不同）

| # | v1.4 有而本仓库没有/不同 | 为什么不照搬 |
|---|---|---|
| 1 | `llm_visible` / `llm_invokable` **两个独立字段** | 仓库现状是 `internal`（可见性）+ 派生 `llm_callable`（可调用性）。**保留既有口径**并做别名：`llm_visible = not internal and reachable`，`llm_invokable = llm_callable`。新增两个真字段会与 `get_tool_defs` 的隐藏逻辑形成第二真相源（D1）。 |
| 2 | `callable_by: llm/human/system/service_account` | 仓库是 `trigger: model/system/human/none`。**缺 `service_account`**（当前无服务账号主体）。映射：`model→llm`、`system→system`、`human→human`、`none→∅`；`service_account` 留待 TASK-06。 |
| 3 | `confirm_level: L0–L3` | **不在本任务范围**（TASK-06）。工具侧当前只有"是否挂单"二值；已确认 v1.4 只映射了 `critical`，**13 个 `high` 不触发确认**是缺陷，须由 TASK-06 修。 |
| 4 | `health: HealthState` 为必填 | `agent/health/` 的产物是探针文本/日志，**无可消费的结构化状态** ⇒ 改为**可选预留字段**，接线归 TASK-05。 |
| 5 | `signature` / `source_trust` / `compatibility` / `semver_policy` 必填 | v1.4 §1.3 自述"首期不强制 SR 签名与 SBOM，但预留接口" ⇒ 按自述落为**可选**。 |
| 6 | `output_schema` / `result_schema` 两个字段 | 仓库 YAML 只有 `schema`（即输入契约）。两个名字指向同一份"输出/结果契约"，做**别名**而非造两个空字段（避免"看起来有两个契约"的误读）。 |
| 7 | `toolset_hash` / session 重建 | 属 Registry 范围（TASK-05），本任务**不引入**（越界即"第二真相源"）。 |

---

## 3. 新增字段的判定规则

### 3.1 `location`（**本任务最易出错的部分**）

**铁律**（v1.4 §2.5）：这次执行**跨出本进程**（含本机 stdio 子进程、本机 localhost HTTP、
Unix domain socket、浏览器/IDE 等外部宿主）⇒ `remote`；只在**同进程内**加载（含 FFI/动态库/WASM）
⇒ `local`。

**判定器**：`agent/lines/location.py::judge_executor_location(host_executor, declared=, registry_source=)`。
依据**事实**（有界调用链 AST 分析，`_MAX_DEPTH=6` / `_MAX_NODES=400`）：

| 规则 | 内容 | 覆盖的实例 |
|---|---|---|
| 直接原语 | 链路调用点命中 `subprocess.*` / `asyncio.create_subprocess_*` / `socket` / `urllib.request` / `requests.Session` / `selenium` / `wmi` … | `shell_execute`、`git`、`run_tests`、`web_post`、`get_weather` |
| 属性→类还原 | `dl._web_http.post` → 赋值的类 `HttpClient` → `agent.web.http_client` | `web_get`/`web_post`/`web_batch`/`web_download`/`web_extract` |
| 返回值类型还原 | `self._session = self._build_session()`，而 `_build_session` 返回 `requests.Session()` | `agent.web.http_client` 全链路 |
| `getattr` 字面量分发 | `getattr(discovery, "list_mcp_connections")` | `list_mcp_connections` |
| 客户端类兜底 | 链路触达的**类**（如 `McpConnector`）内部任一方法直接含边界原语 | `disconnect_mcp`、`list_mcp_connections` |
| 注册来源佐证 | `source=mcp` / `source=mcp_admin` 时按 `remote` 佐证（**不单独定案**，v1.4 §5.1 明确 `source`≠执行边界） | 4 个 MCP 管理面工具 |
| 缺省 | `host_executor` 为空 ⇒ 保守 `remote`（需要超时/熔断的一侧） | 实测 **0 条**走这条 |

**边缘情形表（逐条核对 v1.4 §2.5 与仓库现实）**：

| v1.4 §2.5 情形 | 归类 | 仓库里是否有实例 | 具体是哪个 |
|---|---|---|---|
| 本地 subprocess 调 CLI（stdin/stdout） | remote | ✅ | `shell_execute`→`agent.tools.shell_tools:execute_shell`；`run_program`/`stop_process`/`list_processes`→`agent.tools.process_tools`；`git`；`run_tests`；`run_lint` |
| 本机 stdio MCP | **remote**（铁律明确本机 stdio 也算） | ✅ | `connect_mcp`→`McpConnector.connect_stdio`→`mcp_services.mcp_client.MCPClient`（`asyncio.create_subprocess_exec`） |
| gRPC / HTTP 连 localhost | remote | ✅ | `agent/web/http_client.py`（`requests.Session`）；`get_weather`（`urllib.request`） |
| Unix domain socket | remote | ❌ **仓库无此情形**（全仓 socket 调用点只有 `agent/utils/cross_process_lock.py` 的本地锁，未挂在任何工具的链路上） | — |
| 本地数据库驱动直连 | local（同进程）/ remote（走网络） | ✅ 部分 | `sqlite_query`→`orchestrator_config.db` = **local**；Postgres/5432 = **仓库无此实例**（无 `psycopg2` 依赖） |
| 浏览器扩展、IDE 插件 | remote | ✅ | `browser_navigate`/`browser_screenshot`/`browser_close`（`agent/tools/browser_tools.py`，selenium webdriver 起独立驱动进程） |
| FFI / 动态库 / WASM 同进程加载 | local | ✅ | `pywin32`/`comtypes`/`wmi` 是 `pyproject.toml:92-95` 的 Windows 依赖；**当前无任何工具的调用链触达它们**（判定器已保留 `_LOCAL_FFI` 常量与备注通道） |
| **出进程的 COM/DCOM、WMI** | **remote**（不在 §2.5 的"同进程 FFI"行） | ❌ 当前无实例 | `wmi.WMI()` / `win32com.client.Dispatch` 走跨进程 RPC ⇒ 判定器把它归 `remote`（`_REMOTE_FFI`），与 §2.5 的 FFI 行**有意区分** |
| prompt-only skill | §2.5 说"不注册为 skill" | ✅ **22/23 全部如此** | 与 v1.4 **正面冲突**，见 §7 缺陷上报第 1 条 |
| 单步 skill | skill | ✅ | `scripted-selftest`（带脚本技能，`SkillExecutor` 以 `subprocess.run` 执行 ⇒ remote） |
| cron/CI/Webhook | 不入主分类，只入 `callable_by` | ✅ | `agent/scheduling.py` 的 cron 循环（**但不执行动作**，见 `schedule_task` 假能力）；`agent/task_scheduler.py` 的命令任务 |

**三处与 v1.4 直接冲突的仓库现实（已处理）**：

1. **FFI 类能力的归类**：`pywin32`/`comtypes`/`wmi` 按铁律是同进程 ⇒ `local`；但它们**作用于操作系统**
   （注册表 / WMI / 窗口控制），副作用极强。**处置**：归 `local`（遵从铁律），但把
   「出进程的 COM/DCOM/WMI」单独列为 `remote`（那不是同进程 FFI），并在 `location_ffi`
   字段里保留"同进程 FFI"证据供安全审查。⇒ 见 §7 缺陷上报第 2 条。
2. **22 条纯提示词技能**：v1.4 §2.5 说"不注册为 skill"，但仓库里它们**已经作为 skill 存在于清单**。
   **处置**：保留 `kind: skill` + `callable_mode: manual`（如实标注"由 ContextInjector 注入，
   非模型发起"），**上报 v1.4 §2.5 需修订**（不应因"prompt-only"就删掉能力面）。
3. **4 个 MCP 管理面工具**：`register()` 原先不记录 `source` ⇒ 导出时被兜底成 `builtin`。
   **处置（修事实源，而不是在派生层打补丁）**：给 `register()` 增加 `source` 参数，
   4 个工具显式标 `source=SOURCE_MCP_ADMIN`（**不是** `SOURCE_MCP`：那会让 MCP 断连时的
   `unregister_by_source("mcp")` 误注销管理面工具，实测 `tests/test_dynamic_tools.py:143,488`
   正是无 `source_id` 的调用）。`location` 仍以**执行链事实为主证据**，`source` 只作佐证。

### 3.2 `owner`

仓库此前**没有**这个概念（`declared_in` 只是文件路径）。判定依据（按优先级）：

1. 声明了 `owner` ⇒ 用声明值（可选，便于租户/市场安装场景覆盖）。
2. 默认 `builtin`：能力定义落在 `data/tool_definitions/*.yaml` 或 `data/skill_callability.yaml`
   —— 这两处都在版本控制内、随仓库发布。
3. `local-installed`：运行时经 `register_dynamic(source=mcp)`（MCP 服务）或
   `source=plugin` / `source=market`（扩展）注册的**动态工具**（它们不在 91 个 YAML 里，
   当前不进清单口径）。
4. `tenant-installed` / `marketplace`：**当前恒为空**（单机单用户，无租户安装面）。

实测分布：`builtin 114 / local-installed 0 / tenant-installed 0 / marketplace 0`。

### 3.3 `capability_id`

构造规则：`"{tenant_id}:{namespace}:{name}@{version}"`（如 `default:yunshu:shell_execute@1.0.0`）。

- **为什么顺序是这样**：v1.4 §5.1 要求所有 Registry / Router / 缓存 / 审计 / 配额键**都带 `tenant_id`**。
  当前它恒为 `default`，但**键形状必须现在就正确**，否则将来一开多租户就是全量键重写。
- **`tool_name` 保留为别名**（D2）：`agent/lines/`、`agent/tool_gate.py`、`routes_agent_lines.py`
  与前端都在用 `tool_name`。清单里两个字段并存，`_diff` 与匹配逻辑仍以 `tool_name` 为键。

### 3.4 字段纪律（对齐 v1.4 §5.1）

- `llm_callable = llm_visible && llm_invokable`：**只作派生展示，禁止单独维护**。
  仓库里它由 `agent/lines/callability.py::judge()` 单点派生（声明层 + 事实层 + 派生层）。
- `callable_by`（本仓库 `trigger`）是**身份白名单**，**不表示执行是否依赖 LLM**。
- `execution_requires_llm` 与"是否允许 LLM 调用"**正交**：本仓库无该字段（当前无此判定需求），
  若将来引入，必须与 `llm_callable` 分列。
- 所有 Registry / Router / 缓存 / 审计 / 配额键**必须带 `tenant_id`**。

---

## 4. `TASK-00` §0.4 术语映射表的全部行与处置

| v1.4 术语 | 仓库现有等价物 | 本任务处置 | 落地位置 |
|---|---|---|---|
| `CapabilitySpec` | `ToolMeta` + YAML 全量字段 | **扩展** `ToolMeta`（不新建类） | `agent/lines/models.py` |
| `kind: tool \| skill` | `tool_type: tool\|skill\|api\|script` | **归并**：`api→tool`+`location=remote`；`script→skill`；技能侧 `kind` 来自 `skill_callability.yaml` | `models.ToolMeta.kind` |
| `location: local \| remote` | **不存在** | **新增**（事实判定器 + YAML 钉住值 + `--check` 对拍） | `agent/lines/location.py` |
| `plane` | 已有同名 | 无需改 | — |
| `effect` | 已有同名 | 无需改 | — |
| `risk` | 已有同名 | 无需改（`confirm_level` 映射归 TASK-06） | — |
| `confirm_level: L0–L3` | 工具侧只有"是否挂单"二值 | **不在本任务**；已记入缺陷上报（13 个 `high` 不触发确认） | TASK-06 |
| `llm_visible` / `llm_invokable` | `internal` / 派生 `llm_callable` | **做别名**，不新增真字段（避免第二真相源） | 本文 §2 差异表 #1 |
| `llm_callable_mode` | `callable_mode`（同值域） | 无需改（名字保留） | — |
| `callable_by` | `trigger` + `permission_level` | **映射**（缺 `service_account`，已上报） | `callability.TRIGGERS` |
| `permission_level` | 已有同名同值域 | 无需改 | — |
| `owner` | **不存在** | **新增**（判定依据见 §3.2） | `models.OWNERS` |
| `capability_id` | `tool_name`（唯一主键） | **新增派生**，`tool_name` 保留为别名 | `models.ToolMeta.capability_id` |
| `tenant_id` | 两套互不相通；活的 = workspace-hash，且**可由客户端指定** | **服务端派生**（复用 workspace-hash）；**修掉客户端可指定** | `routes_ui_panels.py::_server_tenant_id` |
| `input_schema` | YAML `schema` | **别名**（不改名） | `models.ToolMeta.schema` |
| `output_schema` / `result_schema` | **不存在** | **新增可选**；两个名字互为别名 | `models.ToolMeta.output_schema/result_schema` |
| `signature`/`source_trust`/`manifest_version`/`compatibility`/`semver_policy` | **不存在** | **新增，首期可选** | `models.ToolMeta` |
| `health: HealthState` | `agent/health/` | **核实：不可直接消费** ⇒ 可选预留，接线归 TASK-05 | 本文 §2 差异表 #4 |
| `Registry` | 启动扫描 YAML + 内存 `_registry` | **不在本任务**（TASK-05） | — |
| `Loader`（Local/Stdio/SSE/HTTP） | 无统一抽象；MCP 侧 `mcp_executor.py` 是 mock | **不在本任务**；但已核实 MCP 客户端真实链路 | 见盘点表 §假能力 |
| `Router` | `agent/tool_router.py` 等 4 个模块 | 不在本任务（TASK-08） | — |
| `toolset_hash` / session 重建 | **不存在** | **不在本任务**（TASK-05） | — |

---

## 5. 三层校验：声明 → 事实 → 派生

```
声明层（人可写、L1 权威）      事实层（机器可证）                  派生层（产物）
data/tool_definitions/*.yaml   注册点 AST 扫描（执行器）          data/capability_manifest.json
data/skill_callability.yaml    有界调用链分析（location）         docs/rfc/云枢能力清单盘点表.md
                               registry_facts()（source/schema）
        └─────────── --check 逐条对拍，不一致即非零退出 ───────────┘
```

三层各守一条：

1. **声明 → 事实**：YAML 的 `location` 与判定器结论不一致 ⇒
   * `scripts/backfill_capability_spec.py --check` 非零退出
   * `scripts/sync_capability_manifest.py --check` 非零退出（`validate()` 的 ⑤ 条）
2. **事实 → 派生**：手改 `data/capability_manifest.json` 或盘点表 ⇒ `--check` 非零退出
3. **假能力拦截**：`callability.FAKE_CAPABILITIES` 的 8 个案例，任何"可用能力"口径都不得收录

**`llm_invokable` 的三项事实口径**（`TASK-00` §2.3(b) 的核心贡献）：

> `llm_invokable` 由「**有执行器 + 有调用方 + 有实体**」三项事实共同判定，**不能只读声明**。

- 有执行器：`static_executors()`（AST 注册点扫描）/ `runtime_executors()`（运行时注入）
- 有实体：工具 = YAML 定义 + 注册点；技能 = `skills_repo/<id>/skill.md` 或台账内联内容
- **有调用方**：新增 `callability.has_caller(name)`（静态扫 `agent/`、`plugins/`、`scripts/`、`app_server.py`
  的字符串字面量引用，**测试目录不计**）⇒ 专门拦 `register_knowledge_audit_job` 这类"只有测试在调"的孤儿

---

## 6. `tenant_id` 的占位与预留

1. **不引入真实多租户改造**（已拍板：单机优先 + 预留接入点）。
2. `CapabilitySpec.tenant_id` 默认 `"default"`，来源是**派生**（服务端），**不是请求参数**。
3. **已修的真实缺陷**：`agent/server_routes/routes_ui_panels.py` 原先允许客户端在
   GET query（`…/memory/skills`）与 POST body（整包回滚）里指定 `tenant_id` ⇒ 改为
   `_server_tenant_id()`（复用 `agent.observability.trace_v2.derive_workspace_id`，与
   `orchestrator.py:211-228` 的"workspace(repository) = 逻辑租户"同源）；
   客户端值经 `_tenant_id_with_declaration()` 只登记为**待校验声明**并留痕告警。
4. `agent/multi_tenant.py`（435 行、零生产 import 的孤岛）**处置建议：保留但明确标注"未接入"**；
   本任务**不接入**（越界）。
5. 盘点表的 `tenant_id` 列**一律填 `default`**（预留占位，非真实隔离）。

---

## 7. 方案缺陷上报单（≥3 条）

| # | 缺陷 | 证据 | 建议处置 |
|---|---|---|---|
| 1 | **v1.4 §2.5「prompt-only skill 不注册为 skill」与存量资产正面冲突** | 仓库 23 条技能里 **22 条**是纯提示词技能（由 `ContextInjector` 注入），且**已**在清单中作为 `kind: skill` 存在 | **修订 v1.4 §2.5**：不因"prompt-only"删掉能力面；改为 `kind: skill` + `callable_mode: manual`（模型不发起）+ 明确"注入式触发" |
| 2 | **FFI 类能力无归类** | `pywin32`/`comtypes`/`wmi` 是同进程 FFI（⇒ local）但作用于操作系统；且**出进程**的 COM/WMI 走跨进程 RPC（⇒ 应 remote）。v1.4 §2.5 只有"FFI ⇒ local"一行 | v1.4 增补一行：**同进程 FFI = local（但需标注"高危副作用"）**；**出进程 COM/DCOM/WMI = remote**。本仓库已按此实现（`_LOCAL_FFI` / `_REMOTE_FFI`） |
| 3 | **性能列（`avg_ms`/`p99_ms`/`dpm`）无数据来源** | 仓库唯一真实压测是一次 HTTP p50=15.84s；`data/tracing_performance_report_*.json` 的 p99 是**假分位数**（每项 `count: 1`）；进程内计时冒充压测 | v1.4 应给出采集口径与采样规范；本任务**留空并标注"未采集"，严禁估算**（见盘点表 §10） |
| 4 | **技能实体不在版本控制内 ⇒ 清单可审计、实体不可复现** | `.gitignore:140` 忽略 `data/skills.json`；`:205` 忽略 `data/skills_mgmt.json`；只有 `data/skill_callability.yaml` 入库 | 二选一：① 把技能实体入库；② 在契约中声明"技能实体由租户/运行时提供"，并在 Registry 里给出降级行为（TASK-05） |
| 5 | **`callable_by` 缺 `service_account`** | 仓库 `TRIGGERS` 只有 `model/system/human/none`，无服务账号主体 | 明确"当前无服务账号主体"或由 TASK-06 引入；在此之前不得声称映射完整 |
| 6 | **`location` 静态判定的精度上限（本任务自曝）** | 本仓库大量 `dl._xxx.method(...)` 动态分发，判定器在 `_MAX_DEPTH=6` 内**能证明 remote，但不能证明 local**。实测 91 个工具里 74 条 `location_confidence=low`（链路含无法静态解析的调用点） | 在 `location_confidence=low` 的条目上**优先人工复核**；根治方案是 TASK-05 的 Registry 在执行器注册点直接记录 transport 事实（运行时事实而非静态推测） |

---

## 8. 回滚方案

- `agent/lines/models.py` 的扩展：新增字段全带默认值 ⇒ 回滚 = `git revert`，**无需数据迁移**。
- **批量改 91 个 YAML**（本任务风险最高的操作）：
  1. 迁移脚本 `scripts/backfill_capability_spec.py` 支持 `--dry-run` / `--check`；
  2. 改动前自动把 `data/tool_definitions/` 整体备份到
     `data/backups/tool_definitions_<timestamp>/`（实测已生成）；
  3. 脚本只做**行级插入/替换**，不改动其它行 ⇒ diff 可逐条评审；
  4. 回滚 = 把备份目录拷回 `data/tool_definitions/`。
- `data/capability_manifest.json` 与盘点表是生成物，回滚 = 重新生成。
- 特性开关：本次新增字段全部**只增不减且默认值不影响既有行为**（实测
  `agent/lines/` 既有消费者零修改）；如需关闭 `location` 校验，可临时回退
  `sync_capability_manifest.py` 的 validate 第 ⑤ 条（不建议：那正是本任务的价值）。
