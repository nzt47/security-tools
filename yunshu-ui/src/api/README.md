# src/api 接口契约与 Mock 说明

> 统一经 `@/utils/request`（axios 封装：Token 注入 / 401 登出 / 解包 / 错误提示）。所有 URL 相对 `/api` 前缀（`VITE_API_BASE` 为空时拼接 `/api`）。

## 模块清单

| 模块 | 接口（相对 /api） | 后端实现状态 |
|---|---|---|
| `user.ts` | `POST /auth/login`、`GET /user/info`、`GET /auth/menus`、`GET /user/list`、`POST /user`、`PUT /user/:id`、`DELETE /user/:id` | 真实后端已实现（auth/menus 按角色过滤下发） |
| `role.ts` | `GET /role/list`、`POST /role`、`PUT /role/:id`、`DELETE /role/:id`、`GET /permissions`、`PUT /role/:id/permissions` | ⚠️ 真实后端未实现，devMock 提供同构 mock |
| `menu.ts` | `GET /menu/tree`、`POST /menu`、`PUT /menu/:id`、`DELETE /menu/:id`、`PUT /role/:id/data-scope` | ⚠️ 真实后端未实现，devMock 提供同构 mock |
| `audit.ts` | `GET /audit/logs`（分页 + 筛选） | ✅ 已实现（后端 `agent/audit/logger.py` 提供查询） |
| `notification.ts` | `GET /notification/list`、`GET /notification/unread-count`、`POST /notification/:id/read`、`POST /notification/read-all` | ⚠️ 真实后端未实现，devMock 提供完整实现 |
| `demo.ts` | `GET /demo/validate-email` | ⚠️ 仅 mock（演示网络异常场景，生产无此路由，404 属预期） |

## Mock / 真实后端切换判定

- **开关**：`.env` 的 `VITE_MOCK_API`（true → devMock 拦截；false → 走真实后端）。
- **判定依据**：Vite dev 中间件按 `VITE_MOCK_API` 决定是否拦截 `/api/*`；生产构建（Electron 桌面）无 mock，必须真实后端。
- **切换检查单**（后端就绪时）：
  1. 字段对齐：mock 返回结构与 api 模块内 `interface` 逐一比对（list/total 分页结构、枚举取值）；
  2. URL 对齐：与上表路由一致；
  3. 状态码语义：`code !== 200` 视为业务错误（拦截器统一 toast + reject）；
  4. 数据范围：`updateRoleDataScope` 的 `dataScope` 取值 `all/dept/self`。
- **无缝切换示例**：role.ts 头注释标注"后端就绪后对齐字段与 URL 即可"，API 层调用方（页面）零改动。

## 传输层语义

- `request`（axios）：解包 `response.data.data`，业务错误 `code !== 200` → toast + reject。
- `createChatStream`（SSE，`src/lib/sse.ts`）：POST `/api/chat/stream`，三类终止——done 正常 / AbortError 主动停止 / HTTP 异常抛错（可选 `onError` 回调）。
- `createRequestAbort`：AbortController 封装，页面卸载取消在途请求。
- `requestWithRetry`：默认关闭（`VITE_REQUEST_RETRY_ENABLED`），仅幂等 GET 在 5xx/网络错误/超时下重试，4xx 不重试。
