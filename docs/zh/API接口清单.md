# 云枢 API 接口清单

> 生成方式：代码直扫（app_server.py `@app.route` / blueprint 路由 / T8 网关注册点），以源码为准。
> 生成日期：2026-08-16

## 总览

| 类别 | 规模 | 认证方式 |
|---|---|---|
| A. 内部 API（app_server.py 原生路由） | 165 个 `/api/*` | `FLASK_API_TOKEN`（require_token，双轨） |
| B. Blueprint 路由（已挂载 4 个 bp） | 19 个端点 | 同上 |
| C. 开放 API（T8 网关 `/api/open/*` + 灰度开放） | 5 个网关端点 + 8 个只读端点 | API Key + RBAC + 限流配额 |
| D. 页面/静态/测试路由 | 12 个 | 页面渲染 / 错误注入测试 |

---

## A. 内部 API（165 个，按域分组）

### A1 状态与基础（14）
| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/voice/listen` | POST | 语音输入 |
| `/api/voice/status` | GET | 语音状态 |
| `/api/health` | GET | 服务健康 |
| `/api/sensors` | GET | 传感器状态 |
| `/api/status` | GET | 运行状态 |
| `/api/mode` | GET | 运行模式 |
| `/api/planning/toggle` | POST | 规划开关 |
| `/api/cognitive/status` | GET | 认知状态 |
| `/api/heartbeat` | GET | 心跳 |
| `/api/heartbeat/history` | GET | 心跳历史 |
| `/api/heartbeat/status` | GET | 心跳状态 |
| `/api/safety/check` | POST | 安全检测 |
| `/api/safety/alerts` | GET | 安全告警列表 |
| `/api/safety/keywords` | GET/POST | 安全关键词 |

### A2 对话与新闻（3）
| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/chat` | POST | 对话（主入口） |
| `/api/chat/stream` | POST | 流式对话 |
| `/api/news` | GET | 新闻（T8.4 开放，Key+read） |

### A3 会话与历史（10）
| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/sessions` | GET/POST | 会话列表/创建 |
| `/api/sessions/<session_id>` | DELETE | 删除会话 |
| `/api/sessions/<id>/rename` | PUT | 重命名 |
| `/api/sessions/current` | POST | 切换当前会话 |
| `/api/sessions/<id>/messages` | GET | 会话消息 |
| `/api/history` | GET | 历史记录 |
| `/api/history/search` | GET | 历史搜索 |
| `/api/history/<int:index>` | DELETE | 删除历史条目 |
| `/api/clear` | POST | 清空历史 |
| `/api/auth/token-check` | GET | Token 校验 |

### A4 配置与上下文（8）
| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/config` | GET/POST | 全局配置读写 |
| `/api/config/logs` | GET | 配置日志 |
| `/api/context/status` | GET | 上下文状态 |
| `/api/context/config` | POST | 上下文配置 |
| `/api/context/compress` | POST | 上下文压缩 |
| `/api/panorama` | GET | 全景视图 |
| `/api/system-prompt` | GET/POST | 系统提示词读写 |
| `/api/system-prompt/reset` | POST | 重置系统提示词 |

### A5 人格与个性化（4）
`/api/personality` GET、`/api/personality/params` POST、`/api/personality/profile` POST、`/api/personality/reset` POST

### A6 技能（5）
`/api/skills` GET（T8.4 开放）、`/api/skills/toggle` POST、`/api/skills/params` POST、`/api/skills/add` POST、`/api/skills/delete` POST

### A7 扩展（13）
`/api/extensions/list` GET、`/installed` GET、`/install` POST、`/uninstall` POST、`/toggle` POST、`/configure` POST、`/discover` GET、`/market/search` GET、`/market/recommend` GET、`/market/refresh` POST、`/channels/send` POST

### A8 网络配置（5）
`/api/network-config` GET/POST、`/network-config/reset` POST、`/export` GET、`/import` POST、`/api/apply-network-config` POST

### A9 LLM 实例（6）
`/api/llm/instances` GET/POST、`/api/llm/instances/<id>` GET/PUT/DELETE、`/<id>/default` POST、`/<id>/test` POST

### A10 搜索实例（6，同 A9 结构）
`/api/search/instances` GET/POST、`/<id>` PUT/DELETE、`/<id>/default` POST、`/<id>/test` POST

### A11 MCP 服务（5）
`/api/mcp/services` GET/POST、`/<id>` GET/PUT/DELETE、`/api/mcp/enable` POST

### A12 工具（7）
`/api/tools/config` GET、`/toggle` POST、`/categories` GET、`/keywords` POST/DELETE、`/keywords/update` POST、`/keywords/reset` POST、`/health` GET、`/status-batch` GET

### A13 记忆、向量与隐私（19）
`/api/memory/overview` GET、`/manual` POST、`/compress` POST、`/<int:index>` DELETE、`/clear-summary` POST、`/summary` PUT、`/api/vector/search` POST、`/api/memory/windows/events` GET、`/windows/stats` GET、`/windows/current` GET、`/windows/config` GET/POST、`/windows/clear` POST、`/api/privacy/info` GET、`/api/window/consent` POST

### A14 权限（6）
`/api/permission/status` GET、`/log` GET、`/stats` GET、`/access-log` GET、`/emergency` POST、`/toggle` POST

### A15 工作区与文件系统（9）
`/api/workspace` GET、`/workspace/write` POST、`/workspace/delete` POST、`/workspace/info` GET、`/api/filesystem/read` POST、`/write` POST、`/list` GET、`/info` GET、`/search` GET

### A16 沙盒（1）
`/api/sandbox/run` POST — Python 沙盒执行

### A17 调度、计划与任务（15）
`/api/scheduler/tasks` GET、`/create` POST、`/delete` POST、`/toggle` POST、`/execute-now` POST、`/history` GET、`/api/schedules` GET（T8.4 开放）/POST、`/api/schedules/<task_id>` DELETE、`/<id>/pause` POST、`/<id>/resume` POST、`/api/schedules/history` GET、`/api/tasks` GET（T8.4 开放）、`/api/tasks/<task_id>` GET、`/<id>/cancel` POST

### A18 搜索性能（6）
`/api/search-performance/status` GET（T8.4 开放）、`/start` POST、`/stop` POST、`/check` POST、`/history` GET（T8.4 开放）、`/summary` GET（T8.4 开放）

### A19 浏览器、进程与剪贴板（10）
`/api/browser/navigate` POST、`/screenshot` GET、`/close` POST、`/api/process/list` GET、`/whitelist` GET、`/whitelist/add` POST、`/whitelist/remove` POST、`/start` POST、`/stop` POST、`/api/clipboard` GET/POST

### A20 Web 抓取（9）
`/api/web/get` POST、`/post` POST、`/xpath` POST、`/css` POST、`/search` GET、`/clean` POST、`/download` POST、`/stats` GET、`/search/status` GET

### A21 审计（1）
| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/audit/logs` | GET | 审计日志查询（T8.4 开放，Key+read，租户隔离） |

### A22 测试端点（3，非生产）
`/api/test/error`、`/api/test/null`、`/api/test/division` — 错误注入

---

## B. Blueprint 路由（已挂载 4 个 bp，19 端点）

| 端点 | 方法 | 功能 | 挂载方式 |
|---|---|---|---|
| `/api/health/dashboard` | GET | 健康看板 | `register_blueprint(health_bp)` |
| `/api/health/probe-trend` | GET | 探活趋势 | 同上 |
| `/api/learning/metrics` | GET | 学习度量 KPI | `register_blueprint(learning_metrics_bp)` |
| `/api/modules/topology` | GET | 六域模块树+实时状态+指标 | `register_modules_api(app)` → 内部 `register_blueprint(modules_bp)` |
| `/api/modules/<module_id>/detail` | GET | 节点详情（状态/动作/审计） | 同上 |
| `/api/modules/<module_id>/actions` | POST | 统一干预入口（高危动作需 reason） | 同上 |
| `/logs/dashboard` | GET | 日志仪表盘页面 | `register_log_system(app)` → 内部 `register_blueprint(log_system_bp)` |
| `/logs/dashboard/data` | GET | 仪表盘实时数据 | 同上 |
| `/logs/api/stats` | GET | 日志统计 | 同上 |
| `/logs/api/query` | GET | 日志查询 | 同上 |
| `/logs/api/errors` | GET | 错误查询 | 同上 |
| `/logs/api/insights` | GET | 内省洞察 | 同上 |
| `/logs/api/actions` | GET | 行动建议 | 同上 |
| `/logs/api/knowledge` | GET | 知识条目 | 同上 |
| `/logs/api/trends` | GET | 趋势 | 同上 |
| `/logs/api/introspection/status` | GET | 内省状态 | 同上 |
| `/logs/api/introspection/run` | POST | 触发内省 | 同上 |

> 注：modules_bp / log_system_bp 通过封装函数注册（非直接 `app.register_blueprint`），启动日志可见
> 「模块聚合 API 路由已注册」「日志系统仪表盘与 API 路由已注册」。

---

## C. 开放 API（T8 网关）

### C1 网关管理端点（无需 API Key）
| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/open/echo` | GET | 网关探活（auth_required=False） |
| `/api/open/keys` | POST | 创建 API Key（明文仅返回一次；可绑 tenant_id+role） |
| `/api/open/keys` | GET | 列出 API Key（脱敏） |
| `/api/open/stats` | GET | 网关运行统计 |
| `/api/docs` | GET | Swagger 全量文档（内部+开放） |

### C2 需 API Key 鉴权的开放接口（T8.4 灰度，GET + scope=read）
| 端点 | 说明 |
|---|---|
| `/api/news` | 新闻 |
| `/api/audit/logs` | 审计日志（租户隔离） |
| `/api/schedules` | 计划列表 |
| `/api/skills` | 技能列表 |
| `/api/tasks` | 任务列表 |
| `/api/search-performance/status` | 搜索性能状态 |
| `/api/search-performance/history` | 搜索性能历史 |
| `/api/search-performance/summary` | 搜索性能汇总 |

鉴权方式：`X-API-Key: <key>` 或 `Authorization: Bearer <key>`；无 Key → 401，无 scope → 403，超配额/限流 → 429。
调用示例见 `scripts/examples/open_api_client.py`。

---

## D. 页面/静态/测试路由（12，非 API）

`/`、`/chat`、`/legacy`、`/static/<path>`、`/mascot-test`、`/network-test`、`/search-status`、`/network-config-debug`、`/replay-viewer`

---

## 备注

- 认证双轨：内部 API 走 `FLASK_API_TOKEN`（require_token）；开放 API 走网关 API Key（X-API-Key / Bearer）。
- 审计发现的历史未挂载路由模块（188 个候选）不在上表——表中 B/C 类均已在 app_server 接线生效。
- 路由挂载自检可用 `python scripts/check_mounted_routes.py`。
