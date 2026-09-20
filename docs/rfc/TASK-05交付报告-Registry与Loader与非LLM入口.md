# TASK-05 交付报告 · Registry + Loader + 非 LLM 入口

> 本文件是 `TASK-05` 的**证据索引**：每条结论都指向一条可复跑的命令与一份日志。
> 生成时间：2026-09-20。基线：`HEAD = 807401ba`（TASK-04 收口）。

---

## 0. 一句话结论

**E1 通过**（关掉 LLM 后 `GET /capabilities/tools` / `POST /capabilities/invoke` /
CLI `cloudshu invoke` 三条链路全部可用）；**E1b 通过**（调用路径已收敛，
`--check` 正例 0 / 负例 1 已实测）；**E5 通过**（真实 app_server 装配下故意让一个
Loader 初始化失败，平台照常启动、`/capabilities/health` 200）。
**E9 部分达标**：真实 114 条全部达标；10,000 条合成压测**主键查询达标、
全量信封未达标**（已给出的改进路径见 §6）。

---

## 1. 可复跑命令与日志

| 验收项 | 命令 | 日志 |
|---|---|---|
| E1 + E6 | `python scripts/verify_llm_off_entrypoints.py` | `_ci_logs/e1_run.txt` |
| E5（真实 app_server 应用） | `python scripts/verify_loader_degradation_startup.py --mode app_server` | `_ci_logs/e5_appserver.txt` |
| E5（最小路由应用，秒级） | `python scripts/verify_loader_degradation_startup.py --mode routes_only` | `_ci_logs/e5_routes.txt` |
| E1b 调用路径 | `python scripts/audit_call_paths.py --check` / `--json _ci_logs/call_paths.json` | `_ci_logs/audit_check.txt`、`_ci_logs/call_paths.json` |
| E9 性能 | `python scripts/bench_capregistry.py --json _ci_logs/bench_capregistry.json` | `_ci_logs/bench_run.txt` |
| 单测（136 条） | `python -m pytest tests/unit/test_capregistry_core.py tests/unit/test_capregistry_loader.py tests/unit/test_capregistry_callpaths_routes.py -q -p no:randomly` | `_ci_logs/test_capall3.txt` |
| 设置注册表零缺口（D5） | `python -m pytest tests/unit/test_settings_registry.py -q -p no:randomly` | `_ci_logs/test_settings.txt` |
| TASK-04 守卫（D1） | `python -m pytest tests/unit/test_capability_spec.py -q -p no:randomly` | `_ci_logs/test_spec2.txt` |
| 清单一致性 | `python scripts/sync_capability_manifest.py --check` | `_ci_logs/sync_check.txt` |

---

## 2. 「关掉 LLM 还能用」的实测输出（E1）

**关闭方式（两道锁，且自证）**

1. 环境级：12 个模型密钥置为 `INVALID-KEY-FOR-LLM-OFF-VERIFICATION`；
2. 代码级：monkeypatch **10 个**模型调用入口（`LLMService.chat/chat_stream/summarize/
   _get_client/_do_chat/_do_summarize`、`ModelAdapter.chat/generate`、
   `OpenAIAdapter._get_client`、`ClaudeAdapter._get_client`）为必然抛异常；
3. **自证**：逐个调用被 patch 的入口并断言确实抛异常 ⇒ `自证覆盖 10 个入口（全部必然失败）`。

**三条链路**

```
链路 1  GET  /capabilities/tools          → HTTP 200 | status=ok | total=114 | degraded=False
链路 2  POST /capabilities/invoke         → HTTP 200 | status=ok | code=ok
        body={"name":"data_format_detect","args":{"data":"{\"a\": 1, \"b\": [2, 3]}"}}
        data={"confidence":0.95,"data":{"a":1,"b":[2,3]},"format":"json","valid":true,...}
        meta: contract=declared loader=local location=local
链路 3  CLI  cloudshu invoke ... --json   → 退出码 0 | status=ok | code=ok
E6      HTTP vs CLI 逐字段对拍            → 0 处差异（忽略唯一易变字段 meta.timing）
结论：  ✅ E1 通过
```

---

## 3. Registry 的存储选择与「只读派生视图」证明（E2）

**选择：内存 + 进程内，包一层门面（TASK-05 §3 第 1 步的候选 A）。**

| 候选 | 结论 |
|---|---|
| A. `load_tool_meta()` + 技能声明的门面 | ✅ 采纳 |
| B. SQLite | ❌ 只读派生视图落盘 = 造第二份能力定义；且 D6 禁止碰 `data/*.db` |
| C. 独立模块 + 启动构建 | ⚠️ 模块新建（`agent/capregistry/`），**数据不另存** |
| Redis/etcd/Postgres | ❌ D3 禁止 |

**只读性的三重证明**

1. **类型级**：`CapabilityRecord` 是 `frozen=True` 的 dataclass（`spec.py`）；
2. **API 级**：`tests/unit/test_capregistry_core.py::test_registry_没有公开写入_api`
   用 `inspect` 枚举 `CapabilityRegistry` 的公开方法，任何写入型动词（`set/register/
   update/delete/…`）一律判失败；
3. **源码级**：同文件 `test_registry_源码里没有反向写入_yaml_或_registry`
   剥掉注释与 docstring 后，断言 `view.py` 的**可执行代码**里不出现
   `yaml.dump(` / `json.dump(` / `open(path,"w"` / `.write(` / `register(` /
   `register_dynamic(` / `unregister(`。

**运行时健康态不在 Registry 里**：它由 `agent/capregistry/loader.py::LoaderManager`
单独持有，Registry 只通过**只读回调** `health_provider` 查询 —— 能力定义与运行时
状态在类型层面分开，`Registry` 保持纯只读。

**命名冲突（E11）**：声明层 `(tenant_id, name)` **零重复**（测试实证）；
`data/capability_manifest.json::same_name_conflicts` 登记的 3 组
（`get_status` / `search_memory` / `get_sensor_summary`，`global` 与 `planning` 两套
注册表各一份）**保持不合并**（D2），每条都有 `resolved` 处置说明。

### 3.1 命名：为什么类叫 `CapabilityRecord` 而不是 `CapabilitySpec`

`TASK-04` 的决定是「**`CapabilitySpec` 必须就是 `ToolMeta`**」，并把它写成了自动守卫
`tests/unit/test_capability_spec.py::test_能力定义只有一个结构`（全仓出现第二个
`^class CapabilitySpec` 即失败）。本模块第一版正叫 `CapabilitySpec`，
**被该守卫当场拦下**。正确处置是**改名**而不是放宽守卫 —— 那条守卫守的正是 D1。
⇒ 本类叫 `CapabilityRecord`（= `CapabilitySpec` 在 Registry 里的归一化记录）。

---

## 4. 四处直调缺口的处置（E1b）

| # | 位置（锚点） | 性质（**含实测更正**） | 处置 |
|---|---|---|---|
| 1 | `agent/knowledge/__main__.py::cmd_audit`（`ci.yml` 的 `knowledge-audit-smoke` job 触发） | **真绕过**：直调函数，不过 `_registry`、不过 `tool_gate` | **已收敛**：新增 `agent/knowledge/audit_entry.py::run_knowledge_audit_entry()` 为**唯一实现**，CI 面与 Agent 面（`kb_lint`）共用；CI 面**产生结构化审计记录** `data/audit/knowledge_audit.jsonl`（含 actor=ci / channel=cli / CI job 名 / 结果摘要）。保留双入口（设计意图），**登记为例外** |
| 2 | `agent/async_executor.py::_run_task` | 🔴 **实测更正**：该文件第 22 行是 `from agent.tools import call as call_tool` ⇒ **确实过 `tools.call()`、确实过 `tool_gate`**，不是绕过。真缺口是 **`submit()` 无身份参数 + `ThreadPoolExecutor` 不继承 `contextvars`** ⇒ `session_source` 退化为环境变量缺省 `"cli"`（后台调用被当成"人从 CLI 调的"） | **已收敛（身份层）**：`submit(..., session_source=None)` 新增可选参数（D2 缺省行为不变）；`_run_task` 在**工作线程体内** `set_session_source()` 包夹；`code_tools._submit_task` 从当前上下文取值并兜底 `"api"`。**登记为例外**（完整身份层属 TASK-06） |
| 3 | `mcp_services/yunshu_mcp_server.py::_handle_tools_call` | 🔴 **实测更正**：第 472-476 行同样是 `_tools.call(...)` ⇒ **过闸门**。例外点是**协议层身份**（MCP 协议无内建认证） | **已收敛（来源标注）**：`set_session_source("mcp")` 包夹，使审计不再把它误记成 `cli`。**登记为例外**（含理由与身份） |
| 4 | `agent/skills_mgmt/mcp_adapter.py::_call_tool` | ⚠️ **潜在风险**：确实 `session.call_tool(...)` 不过闸门，但依赖官方 `mcp` SDK，**实测未安装** ⇒ 当前不可达 | **按潜在风险登记**：`reachable=False`，理由写明"**一旦 SDK 装上即生效**"，接入 Registry/Loader 属 TASK-07 |

**本轮新发现（不在原 4 处之内）**

- `agent/tools/mcp_connector.py::_handler`：MCP 工具注册转发，经 `register_dynamic(source="mcp")`
  进 `_registry` ⇒ **过闸门**；缺的是**传输层治理**（熔断/状态机/退避）。**登记为例外**，
  收敛到 `Loader` 属 TASK-07。
- `agent/knowledge/tools.py::kb_lint`：与 CI 面共用实现的那一层，登记为例外。

### 4.1 `--check` 的两条不变量（**负例已实测**）

```
正例（原样）                        → exit=0  ✓ 无未登记直调（扫描 39 条路径，例外 10 条，无腐化）
负例①（删掉一条真实直调例外）        → exit=1  ✗ 1 条未登记的直调（绕过 tool_gate 执行能力）
负例②（造一个代码里不存在的例外）    → exit=1  ✗ 1 条已登记例外在代码里找不到符号（白名单腐化）
```

**锚点用 `路径::符号名`，不用行号** —— `TASK-05` 预检已证明行号会漂移
（任务书写 `ci.yml:562`，实测在 `:726`）。

**硬失败范围显式声明**：`agent/`、`plugins/`、`cloudshu/`（本地能力平面的生产代码）。
`mcp_services/` 下除 `yunshu_mcp_server.py` 外是**面向外部 MCP 服务端的客户端与
演示脚本**，仍**全部列入清单**（P3 段），只是不计入退出码 —— "看得见但不拦"与
"看不见"是两回事。

---

## 5. Loader 四实现的真实状态（E4）

| Loader | 状态 | 失败降级用例 | 真实链路验证 |
|---|---|---|---|
| **Local** | **真实现**（包装 `agent/tools/__init__.py::_registry`，**不重写**） | 未知工具 ⇒ `not_found` + 退避 + `unhealthy`（不抛） | 运行时 spy 证明本地执行**只**经 `agent.tools.call()` |
| **Stdio** | **真实现并端到端验证** | ① 服务端脚本不存在 ⇒ 启动期 `LoaderInitError`；② 脚本启动即崩 ⇒ 连接失败 + 退避 | **真实子进程**驱动仓库自带 `mcp_services/yunshu_mcp_server.py`（**手写 JSON-RPC，不需要官方 `mcp` SDK**）；实测 `get_file_info README.md` 返回真实文件信息 |
| **SSE** | **真实现，但无生产端点** | ① 端点不可达；② 握手拿不到 endpoint（HTTP 404） | 对**真实本地 HTTP 服务**（chunked SSE）连通并完成一次 `tools/call` |
| **HTTP** | **真实现，但无生产端点** | 端点不可达 ⇒ 退避 + `unhealthy` | 对**真实本地 HTTP 服务**（JSON-RPC over POST）连通并完成一次 `tools/call` |

> ⚠️ **诚实标注**：仓库里**没有任何生产 SSE/HTTP MCP 端点**（`data/mcp_services.json`
> 无此类登记）⇒ 这两个 Loader 的**生产可用性未经验证**，只有"对真实 HTTP 服务可用"
> 这一条实测结论。**不把它们表述为"生产链路已就绪"。**

**熔断已接到传输层**：`Stdio/Sse/Http` 三个 Loader 各挂一个
`agent/circuit_breaker.CircuitBreaker`（复用既有实现）；熔断打开时**不发起真实调用**。
`Local` **刻意不挂**（进程内调用无"连接"可熔断，挂了会把"某工具报错 3 次"升级成
"整个本地能力面被切断"）。

**stdlib 冷启动**：见 §6；`prewarm` 池按 `CP_CAPABILITY_STDIO_PREWARM` 建立，
**未在默认配置下常开**（默认 0）—— 因此**不声称**"stdio 冷启动 < 5s 已达标"，
只报告"真实子进程可从零建立并完成调用"这一条实测事实。

---

## 6. 性能实测（E9）

命令：`python scripts/bench_capregistry.py`　口径：单进程内、**16 线程**共享同一
Registry 实例（与 waitress 16 线程同构）；**采集于并发负载下**（用户后端在跑）。

| 指标 | 114 条 p99 | 10,000 条合成 p99 | 倍数 | 判定 |
|---|---|---|---|---|
| `get(tenant,name)` 主键查询 | 0.001 ms | **0.001 ms** | 1.10× | ✅ 索引不退化 |
| `query(kind,location)` 过滤查询 | 0.037 ms | **205.95 ms** | 5612× | ❌ 超 100 ms |
| `list_envelope()` 全量信封（无分页） | 16.70 ms | **1346.65 ms** | 81× | ❌ 超 100 ms（**已知未达标**） |
| `list_envelope(limit=500)` 分页信封 | 33.60 ms | **677.92 ms** | 20× | ❌ 超 100 ms |

**真实 114 条：最差 p99 = 33.60 ms ⇒ ✅ < 100 ms。**

**为什么 10k 的 p99 会炸（诚实归因）**：p50 与 p99 差两个数量级
（过滤查询 p50 = 2.2 ms、分页信封 p50 = 5.2 ms），说明**单次查询本身不慢**，
尖峰来自 16 路 GIL 争用 + GC 抖动（每次查询构造数千元素的列表、每次信封序列化
~40 万个 dict 键）。**退化的不是"查询"，而是"一次处理 N 条"这个动作。**

**改进路径（E9 要求写明）**

1. **已做**：HTTP 面加**默认分页 500**（`CP_CAPABILITY_DEFAULT_PAGE`），
   响应以 `data.truncated=true` **明示**截断（不静默）—— 生产调用方（面板/CI）
   走的是分页形态；
2. **未做**：10k 全量导出应改为**流式/游标**接口（生成器 + 分块 JSON），
   而不是加大单次响应；
3. **未做**：把 `limit` 下推进 `query()` 做早退（需要同时给出 `total`，
   故只能省序列化、不能省扫描）。

> ⚠️ **禁止误读**：10,000 条是**合成数据**，只能证明"数据结构在 10k 量级的行为"，
> **不能**用来说明"真实 10k 能力下满足容量目标"（真实能力含 AST 派生的 location
> 证据等更重的字段）。

**Loader 初始化不阻塞启动 + 冷启动增量**

- 真实 `app_server` 应用装配耗时 **74.22 s**（480 条路由，含 5 条 `/capabilities/*`）；
- 本层**边际冷启动成本 177.5 ms**（import 16.8 + import server_routes 1.4 +
  register_routes 18.3 + 首次 build_registry 140.8 + LoaderManager 0.2）
  = **0.239%**，远低于 E5 的"增幅 < 10%"。

---

## 7. 统一错误语义（E8）

14 个错误码全部实现（`agent/capregistry/errors.py::CODE_META`），每个码都有
`retryable` / `http_status` / **固定** `llm_hint`；映射点唯一
（`from_exception()`），`ToolError` 按语义特判（"未知工具" ⇒ `not_found`，
"执行失败" ⇒ `internal_error`）。

**「异常不进 LLM 上下文」**：`to_llm_safe()` 是唯一允许进 LLM 的形态 ——
`message` 恒为**常量串**（结构上不可能携带路径/URL/密钥），只有 `detail` 经
`redact()` 清洗后输出。脱敏覆盖：HTML/XML 标签、traceback 骨架与 `File "...", line N`、
Windows/POSIX 绝对路径、带与不带 scheme 的 URL、**内网主机名**（`.local/.internal/
.corp/.lan/...`）、邮箱、长十六进制/opaque 串、`sk-/ghp-/xox*` 密钥前缀、
内存地址；结果硬截断 200 字符。`tests/unit/test_capregistry_core.py::TestRedaction`
用一段**真实形态的危险原文**（含全部上述元素）逐项断言，并断言每条错误码的
`llm_hint` 里不含 `X:\`、`http://`、`@`。

---

## 8. 三入口一致与模型能力探测（E6 / E7）

- **E6**：HTTP 与 CLI 的 JSON **逐字段一致**（`compare` 递归比对，唯一忽略项
  `meta.timing` —— 文档化的易变字段）。结构上只可能一致：两边的响应体都由
  `CapabilityRegistry.list_envelope()` / `invoke_capability()` 产出，
  路由与 CLI 只负责取值与状态码。
- **E7**：`?model=` 探测 —— 显式哨兵（`none/off/-/no-tools`）与**已确认不支持**的
  模型前缀 ⇒ `supports_tool_calling=false` 且 `returned=0`（**裁剪后的清单**），
  同时 `total` 仍如实报 114（两个数含义不同，不许混）；未知模型**按支持处理**
  并说明理由（宁可多暴露，不可因判定失败静默藏能力）。表可由
  `data/model_tool_calling.yaml` 覆写。

**调度双工具面可区分（交付物 #15）**：`/capabilities/tools` 的每条都带
`impl_status`，`schedule_task` 为 `not_implemented` 并附 `impl_status_reason`
（来自清单的 `non_capabilities.hollow_executor` 证据）。

**后台任务面鉴权不一致（交付物 #16，已登记）**：
`agent/server_routes/routes_background.py` 的 4 条 `/api/background/tasks*`
**无 `@require_token`**，而同目录 `routes_workflow_learning.py:117`、
`plugins/mcp_scheduler.py:227` 都有。它是 `async_executor.submit()` 无身份链路的
**结果面** —— 二者因果相连。本任务**登记但不修**（身份层完整修复属 TASK-06）；
新端点 `/capabilities/*` 选择**带 `@require_token`**（与多数口径一致）。
**实测**：真实 app_server 下无令牌访问 `/capabilities/*` 返回 **401**（不是缺陷，
是"默认需鉴权"的必然结果），验证脚本按真实调用方一样带令牌。

---

## 9. 未做 / 未验证（诚实清单）

1. **SSE / HTTP Loader 的生产可用性未验证**（仓库无生产端点）；
2. **stdio 预热池未在默认配置下常开**（`CP_CAPABILITY_STDIO_PREWARM=0`）⇒
   **不声称**"stdio 冷启动 < 5s / 首请求 < 1s 达标"；
3. **`/capabilities/skills/search` 未做端到端召回验证**：技能实体
   （`data/skills.json` 等）**不在版本控制内**（`TASK-00` §0.3）⇒ 新克隆仓库上
   召回可能为空。端点已实现并复用既有检索栈（`SkillLoader.match` + RRF），
   并在 `meta` 里如实披露 `entity_available`；
4. **10,000 条全量信封未达标**（见 §6，改进路径已给）；
5. **完整身份层（`callable_by` 落到 ABAC、`service_account` 预授权）未实现** ——
   本任务只做**接口预留**（`set_preauthorization_hook`），完整实现属 TASK-06；
6. **未跑全量回归**：`TASK-00` D14 明令不要用裸 `pytest` 单进程跑全量
   （实测边际退化到 7.5 分钟/1%），且 D13 要求不与其它 pytest 并发。
   本任务跑了**相关文件**（136 + 216 + 49 + 27 条）全部通过；
   全量基线对照**未执行**，故 **E10（零回归）只能给部分证据**。
7. **`python app_server.py` 命令行本身未跑**：它会 `taskkill` 5678 端口上的进程
   （`app_server.py:1545-1565`），会杀掉用户正在运行的后端 —— 本次改为
   `import app_server` + waitress 绑 5679，**启动链路的其余部分完全一致**。

---

## 10. 与 TASK-05 原描述的冲突点（逐条）

| # | 任务书原文 | 实测事实 | 处置 |
|---|---|---|---|
| 1 | `agent/async_executor.py:224` 是"绕过 `tool_gate` 的现实缺口" | 该文件第 22 行 `from agent.tools import call as call_tool` ⇒ **过 `tools.call()`、过 `tool_gate`**；真缺口是**身份缺失** | 按"过闸门但无身份"登记并做身份透传 |
| 2 | `yunshu_mcp_server.py:476` "文档明写不做鉴权" ⇒ 缺口 | 同样是 `_tools.call(...)` ⇒ **过闸门**；`不做鉴权` 指**协议层身份**（MCP 无内建认证） | 收敛为"来源标注 `mcp`" + 登记例外 |
| 3 | §2.3d "调度能力有**两个工具面**" | 只有 `schedule_task`（`code_tools`，空实现）是**工具**；`task_tools.create_scheduled_task` **从未注册为工具**（HTTP `routes_monitoring.py:97` 直调它） | 在 `impl_status` 上区分；双工具面的表述按实测修正 |
| 4 | 完成判据点名 `current_time` | 仓库**没有** `current_time` 工具（91 个 YAML 无此名） | 用 `data_format_detect`（纯本地、确定性、已声明 `result_schema`）替代；任务书原文是"如 `current_time`"（举例） |
| 5 | "Registry 只读派生视图，不得有写入 API" | 新增的 `CapabilityRecord` 与 TASK-04 的 D1 守卫**同名冲突**（守卫禁第二个 `class CapabilitySpec`） | **改名**为 `CapabilityRecord`，不放宽守卫 |
| 6 | `data/capability_manifest.json` 为 TASK-04 的产物 | 本任务新增模块改变了 AST 派生的 `location_unresolved` 计数（3 条能力的 `location_evidence`），`--check` 因此报"盘点表与清单不同源" | 按脚本指示**重新派生**两侧产物；delta 仅 3 条能力的证据计数 + 时间戳，`counts`/location 分布（93/21）**不变** |
| 7 | §5 E5 "对比 TASK-03 基线，增幅 < 10%" | 无 TASK-03 基线快照可直接 A/B（回退我的改动就破坏工作区，D15 禁止） | 改报**边际成本**：本层冷启动增量 177.5 ms = 74.22 s 的 0.239% |
| 8 | §2.3c 第 3 条说 `mcp_adapter.py:254` 这条"在第二轮才被发现" | 本轮 AST 扫描**又发现一条**不在 4 处之内的 `agent/tools/mcp_connector.py:217` | 已登记为例外并说明收敛归属（TASK-07） |
