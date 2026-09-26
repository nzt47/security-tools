---
id: frontend-state-sync
name: frontend-state-sync
description: 'Use when generating web code that involves frontend/backend interaction,
  state updates, async requests or data display. Keeps UI and server data what-you-see-is-what-you-get:
  race-condition defense via request ids, AbortController cancellation, optimistic-update
  rollback, debounce/throttle, WebSocket resilience, framework alignment (nextTick
  / useEffect) and double-click protection.'
content_type: markdown
category: custom
tags:
- external
- imported
- markdown
author: unknown
source: external_agent
status: approved
enabled: true
description_zh: 生成涉及前后端交互、状态更新、异步请求或数据展示的 Web 代码时使用。确保 UI 与后端数据"所见即所得"，覆盖竞态防御、请求取消、乐观更新回滚、防抖节流、WebSocket
  健壮性、框架对齐与防连点。
---

# 前后端状态绝对同步规范

## 描述
在生成任何涉及前后端交互、状态更新或数据展示的 Web 代码时，严格遵循"前后端状态绝对同步"原则，确保 UI 呈现"所见即所得"，彻底杜绝因网络延迟、异步时序错乱或并发操作导致的 UI 与后台数据割裂。

## 使用场景
- 生成列表加载、搜索联想、多 Tab 切换、表单提交等涉及异步请求的前端代码。
- 采用乐观更新（Optimistic UI）的交互场景。
- 输入框触发搜索/过滤的高频交互场景。
- 生成 WebSocket 实时通信相关代码。
- React/Vue 框架下的状态管理与副作用处理。

## 指令

### 1. 异步时序与竞态防御
- **禁止盲目信任最后返回的请求**：在生成列表加载、搜索联想、多 Tab 切换等代码时，必须内置请求序号或版本号校验机制。只有当返回的 ID 等于当前最新请求 ID 时，才允许更新 UI。
- **强制取消废弃请求**：在 React/Vue 等框架中生成异步请求代码时，必须默认使用 `AbortController`。当组件卸载、依赖项变更或用户连续触发时，自动取消前一个未完成的请求。

### 2. 状态更新与回滚机制
- **乐观更新必须配对回滚**：当采用 Optimistic UI 时，必须显式实现 `try/catch` 结构，并在 `catch` 块中利用闭包缓存的旧状态进行精准回滚，同时提供友好的错误提示。
- **后端权威原则**：禁止在前端自行推导关键业务状态。写操作（POST/PUT/DELETE）成功后，若业务允许，优先通过重新拉取或后端推送的权威数据刷新本地状态，而非仅依赖前端数组的本地增删。

### 3. 实时通信与防抖节流
- **输入防抖**：生成任何输入框触发搜索/过滤的代码时，必须默认集成 `debounce` 或 `throttle` 逻辑，防止高频请求打乱状态。
- **WebSocket 健壮性**：若生成 WS 相关代码，必须包含断线指数退避重连机制、心跳检测、以及重连后的全量快照补齐逻辑，防止断线期间的消息丢失。

### 4. 框架底层对齐
- **Vue 环境**：在数据变更后读取 DOM 或执行依赖 DOM 尺寸的逻辑前，必须使用 `await nextTick()`。
- **React 环境**：严禁在 `setState` 后立即读取状态或 DOM，必须使用 `useEffect` 监听状态变化；避免在 `useEffect` 中产生未清理的异步副作用，必须返回清理函数。

### 5. 幂等性与防连点
- 生成按钮点击事件时，必须默认包含 `loading` 状态控制或按钮禁用逻辑，直到请求完成，从 UI 层面阻断用户的重复提交。

### 6. 输出要求
在每次生成涉及数据交互的代码后，必须在注释中简要说明使用了哪种机制（如：AbortController、Request ID、Optimistic Rollback）来保证状态同步。