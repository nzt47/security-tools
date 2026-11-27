# E4-3 设计：MCP resources / prompts 暴露（2026-11-27，**方案稿**）

> 状态：**设计稿（未实现）**。本文件只回答「怎么把 MCP 的 resources/prompts 暴露给模型」，
> 不改现有 tools/list·tools/call 链路。实现时按口径 §3：新工具 = 6 处计数契约 +
> 分类默认表 + `PLANE_MAP` + 主线 boost + 干净 worktree 派生；新 env 登记 registry.py。

## 0. 现状事实（可复核）

| 事实 | 位置 |
|---|---|
| MCP 客户端只实现三个方法：`initialize` / `tools/list` / `tools/call` | `agent/mcp_executor.py:190 / :226 / :250` |
| 连接管理（连接/断开/列举活跃连接）走工具面 `connect_mcp` / `disconnect_mcp` / `list_mcp_connections` | `agent/tools/mcp_connector.py` |
| 能力协商在 `initialize` 里发 `capabilities`，但**没有读回**服务端能力 | 同上 :190 |
| 安装/信任走 `ext_install` + 签名/白名单（治理平面，L3） | `agent/extensions/mcp_installer.py` |

缺口：MCP 服务器的 `resources`（可读数据，如日志/文档/DB 行）与 `prompts`（参数化提示词模板）
在云枢里**完全不可见** —— 模型只能调 tools，看不到服务器提供的数据面与模板面。

## 1. 目标与边界

**做**：
1. `resources/list` / `resources/read`：列出并读取服务器声明的资源（URI + mimeType）。
2. `prompts/list` / `prompts/get`：列出并取回提示词模板（含参数）。
3. 全部**只读**：不 subscribe、不写资源、不把 prompt 自动注入系统提示词。

**不做**（明确写死）：
- `resources/subscribe` 与变更通知（需要长连接与事件泵，单独立项）；
- 把 resource 内容落盘（只在结果里回，走 spill 阈值）；
- 把 `prompts/get` 的结果**自动**拼进系统提示词（提示注入面：模板内容由第三方服务器提供，
  只能作为**数据**回给模型，由模型显式使用）；
- 绕过 `ext_install` 的签名/白名单：本设计只作用于**已连接**的服务器。

## 2. 表面：一个工具 + action 枚举（与 terminal 同一处置）

```
mcp_resource(action = list_resources | read_resource | list_prompts | get_prompt,
             connection = <list_mcp_connections 返回的 id>,
             uri = <read_resource 必填>, name = <get_prompt 必填>,
             arguments = <get_prompt 的参数对象>)
```

理由（与 E3-1 同一取舍）：四个动作是**同一个连接对象的四种读**，拆四个工具会让工具索引/清单/
BM25 半饱和点/路由 τ 连着改四次；schema 用 action 枚举表达，错误能就地说清缺哪个参数。

计数连锁（实现时必须同批）：`test_capability_spec`（YAML 数/清单数/可见集）、`test_capregistry_core`
（总数/kind/local+remote/索引 primary/探针 total）、`test_confirm_level`（分布）、
`test_tool_router_hybrid_real_index_l28`（真实工具数/BM25 篇数）、`test_tool_pruning`（受保护数/上限）、
`test_det2_stable_sort_determinism`、`_DEFAULT_TOOL_CATEGORIES`、`scripts/backfill_tool_planes.PLANE_MAP`、
`engineering.yaml` 的 boost/max_tools、以及 `_BM25_HALF_SATURATION` / S0 分位数 / v1 τ 的重标定。

## 3. 协议与失败码

| 场景 | 行为 |
|---|---|
| 服务器 `initialize` 未声明 `resources`（或 `prompts`） | 回稳定码 `not_supported`（附服务端声明的能力清单，便于运维判断） |
| connection 不存在 / 已断开 | `no_connection` |
| resource 超过 `CP_MCP_RESOURCE_MAX_BYTES`（默认 256KB） | `too_large`（**不**落盘；提示改用分页 URI 或让服务器暴露子资源） |
| `resources/read` 返回非 text（blob） | `not_text`（二进制不入上下文；与图片 offload 同口径） |
| 未知 action / 缺参 | `bad_request`（就地说明缺哪个参数） |

**能力协商必须读回**：`initialize` 的响应里 `capabilities` 要存进连接对象（今天没存），
否则"服务器不支持 resources"会被误报成"空列表"（两者对运维的含义完全不同）。

## 4. 安全（CodeQL 三条铁律 + 注入面）

1. **资源内容是数据，不是指令**：结果里原样回，不拼进系统提示词；结果长度受 spill 阈值约束。
2. **不进日志**：只记 `connection` + `uri` 的 sha256 前 8 位 + 字节数（URI 可能含租户/凭据片段）。
3. **只读**：任何 action 都不携带写语义；服务器侧 `resources/read` 本身是只读方法。
4. **不新增 HTTP 面**：一期只做工具面（模型可见），UI 只在「扩展 → MCP」页显示连接的
   resources/prompts 计数（若做，路由同批有前端引用）。

## 5. 判据（实现完成时逐条验）

1. 假服务器（进程内 stdio，实现 `initialize/resources/list/read/prompts/list/get`）：
   四个 action 全通，`uri`/`name` 原样回；不支持的服务器回 `not_supported`。
2. 大资源（> 上限）回 `too_large` 且**不落盘**。
3. 能力协商：服务器声明缺 `resources` ⇒ `list_resources` 回 `not_supported`（不是空列表）。
4. 审计：每次读写一条 `mcp_resource_access`（connection 摘要 / action / uri 摘要 / 字节数 / 结果码）。
5. 计数契约全绿（同上 7 处）+ `scan_settings --check` 零缺口（新 env `CP_MCP_RESOURCE_MAX_BYTES`）。
6. 干净 worktree 全量 pytest 绿；派生（tool_index / manifest / BM25 / τ）同步。

## 6. 分期

- **一期**：list/read resources + list/get prompts（只读，默认 `enabled: false` 观察）。
- **二期（不在本期）**：resources 变更通知（subscribe + 事件泵）、prompts 一键注入（需 Owner 决定，
  且必须走"显式选择 + 显示来源"的提示词角色层）。
