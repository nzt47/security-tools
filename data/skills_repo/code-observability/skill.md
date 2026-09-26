---
id: code-observability
name: code-observability
description: 'Use when generating feature-module code or backend APIs. Enforces the
  "exists means observable" principle: structured JSON logs with trace_id/module_name/action/duration_ms,
  explicit error boundaries with business error codes, reserved analytics hooks and
  a /health or /status endpoint — without adding expensive paid third-party dependencies.'
content_type: markdown
category: custom
tags:
- external
- imported
- markdown
author: unknown
source: external_agent
status: published
enabled: true
description_zh: 生成功能模块代码或后端 API 时使用。遵循"存在即可见"原则，强制输出结构化日志、显性错误边界、埋点预留与健康检查接口，且不引入昂贵的第三方付费依赖。
---

# 可观测性强制约束

## 描述
在生成任何功能模块代码时，遵循"存在即可见"原则，确保代码具备结构化日志、显性错误边界、关键埋点与健康检查能力，且不引入昂贵的第三方付费依赖。

## 使用场景
- 生成核心业务逻辑模块代码时。
- 生成后端 API 接口时。
- 涉及网络请求、数据校验等可能失败分支的代码时。
- 涉及关键用户交互点（提交、筛选、支付等）的代码时。

## 指令

1. **结构化日志**：所有核心业务逻辑节点，必须输出 JSON 格式的 `console.log`，必须包含 `trace_id`、`module_name`、`action`、`duration_ms` 字段。
2. **边界显性化**：对于可能失败的分支（如网络超时、数据校验失败），必须抛出带有明确业务错误码的 Error，而不是静默返回 `null`。
3. **埋点预留**：在关键用户交互点（如提交、筛选、支付），预留 `trackEvent('event_name', {payload})` 的函数调用占位符。
4. **健康检查**：如果生成的是后端 API，必须附带一个 `/health` 或 `/status` 接口，返回该模块依赖的数据库/缓存的连接状态。