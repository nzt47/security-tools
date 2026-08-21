# 任务 T5：网络请求与 SSE 增强

> 所属计划：[14_前端封装理想设计_审计与重构总览.md](14_前端封装理想设计_审计与重构总览.md)
> 任务 ID：EVO-FE-T5 | 依赖：T1 | 工作量：中

---

## 一、目标描述

统一网络层与 SSE 流式的错误处理口径、补请求取消与重试能力、沉淀 API 层与 mock 切换契约文档，使 `src/lib/sse.ts` 与 `src/utils/request.ts` 的语义对齐（这是参考文档"网络请求封装"维度在云枢的核心落地）。

**【不易】边界**：`request` 函数与 axios 拦截器行为（401 登出 / 解包 / perf 日志）不变；`createChatStream` 的对外契约不变——仍为 `AsyncGenerator<StreamEvent>`，入参 `(question, signal?)`，事件类型 `StreamEvent` 不变；后端接口 URL 与事件格式不变；`toast` 提示语义不变（防重复弹窗逻辑不动）。

## 二、前置依赖

T1（`storage` / `isAbortError` / `abortable` 工具）。

## 三、执行步骤

1. **SSE 错误口径统一**：`createChatStream` 抛错信息与 request.ts 语义对齐：
   - HTTP 非 2xx：附 status 与响应体摘要（现已有，保留），并补充"网络层错误统一经 `notify()` 提示"的可选回调参数 `onError?: (e: Error) => void`（默认行为不变，调用方 useLayoutStore 维持现状）；
   - 明确区分三类终止：服务端 done 事件（正常）、AbortError（主动停止，调用方判定）、HTTP/解析错误（抛异常）；
   - 解析失败事件（`parseEventBlock` 返回 null 时的 `console.error`）保留日志，不做弹窗。
2. **请求取消 API**：在 request.ts 导出 `cancelRequest(source)` 辅助（基于 axios `CancelToken` 或 `AbortController`，二选一保持与 axios 1.19 兼容），并接入页面在"切换/卸载时取消在途列表请求"（若 T3 已完成，`useTablePage` 消费此能力；未完成则由本任务单独提供 demo 用法即可）。
3. **重试（可选，默认关闭）**：新增 `requestWithRetry(config, { retries = 2, retryDelayMs = 500, shouldRetry? })`，仅对幂等请求（GET）生效，401/4xx 业务错误不重试；由 `VITE_REQUEST_RETRY_ENABLED` 环境变量控制，默认关闭（对齐"自动化默认关闭"安全底线）。
4. **API 层与 mock 契约文档**：
   - 新增 `src/api/README.md`：登记每个 api 模块的接口 URL / 入参 / 返回类型 / 后端实现状态（真实 or devMock / exportMock），切换条件（如 role.ts 注明"真实后端未实现，devMock 提供同构 mock，后端就绪后对齐字段与 URL 即可无缝切换"）；
   - 在 README 明确"mock 与真实后端的判定依据"（`.env` 的 `VITE_USE_MOCK` 或 dev server 中间件）与"切换检查单"（字段对齐 / URL 对齐 / 状态码语义）。
5. **测试**：
   - `sse.test.ts` 补充：HTTP 非 2xx 抛错、AbortError 传播、`onError` 回调触发；
   - `request.test.ts` 补充：`requestWithRetry` 重试次数与不重试 4xx、`cancelRequest` 取消在途请求；
   - 既有 useLayoutStore 流式测试全绿（sendMessage 行为不变）。
6. **回归验证**：`npm run check` / `npm run lint` / `npm test`；Electron 模式（或 mockElectron）手工验证 SSE 流式对话、停止生成、断网重连提示。

## 四、预期成果（交付物）

- `src/lib/sse.ts` 增强（onError 可选回调 + 错误语义注释，契约不变）；
- `src/utils/request.ts` 新增 `cancelRequest` 与 `requestWithRetry`（既有导出不变）；
- `.env` / `.env.example` 新增 `VITE_REQUEST_RETRY_ENABLED`（默认 0/false）；
- `src/api/README.md`（接口与 mock 契约）；
- 更新/新增测试。

## 五、评估标准（验收条件）

1. `createChatStream` 契约不变：类型、入参、事件结构、默认行为与既有测试全绿；
2. 三类终止（done / AbortError / 异常）在测试中有明确用例覆盖；
3. `requestWithRetry`：GET 重试生效、401 不重试、重试上限生效；默认关闭（无 env 时不重试）；
4. `cancelRequest` 能取消在途请求且不触发 toast 噪音（对齐 Toaster 防重复弹窗）；
5. `src/api/README.md` 覆盖全部 api 模块，mock 切换判定依据明确；
6. `npm run check` 无类型错误、`npm run lint` 无新增告警、`npm test` 全部通过；
7. 未修改后端接口 URL / 事件格式 / `toast` 语义。

## 六、涉及模块

`yunshu-ui/src/lib/sse.ts`、`yunshu-ui/src/utils/request.ts`、`yunshu-ui/src/stores/useLayoutStore.ts`（仅验证不修改）、`yunshu-ui/.env.example`、`yunshu-ui/src/api/README.md`（新增）、`yunshu-ui/src/lib/sse.test.ts`、`yunshu-ui/src/utils/request.test.ts`。
