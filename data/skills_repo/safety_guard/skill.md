---
id: safety_guard
name: 安全守护
description: 在生成回应与执行工具调用前检测潜在风险内容，对敏感操作执行拦截、告警或要求二次确认，保障对话与操作安全
category: custom
tags: []
version: 0.1.0
enabled: true
status: approved
author: unknown
source: legacy_migration
content_type: markdown
---

# 安全守护

在生成回应与执行工具调用前检测潜在风险内容，对敏感操作执行拦截、告警或要求二次确认，保障对话与操作安全。

## 适用场景

- 工具调用前校验参数路径（防目录穿越、防系统文件写入）
- 回应中检测隐私信息泄露（手机号、身份证、密钥、token）
- 用户请求涉及危险操作（删除、格式化、批量修改）时强制二次确认

## 触发条件

- 每次工具调用前自动运行（前置门控）
- 回应生成后、输出前自动运行（后置审查）
- 命中风险关键词或正则模式时触发拦截

## 使用方式

1. 维护风险规则库：路径黑名单、敏感信息正则、危险操作动词清单
2. 工具调用前：校验参数路径与操作类型，命中黑名单则拦截
3. 回应输出前：扫描隐私信息与危险指令，命中则替换或告警
4. 高风险操作（删除/格式化/批量执行）：不直接执行，返回"需二次确认"
5. 所有拦截与告警记录结构化日志，含 trace_id 与命中规则

## 输出特征

- `action`: allowed / blocked / confirm_required
- `matched_rules`: 命中的规则 ID 列表
- `sanitized_content`: 脱敏后的内容（如有）
- `severity`: info / warning / critical

## 不变量

- 任何拦截不得静默，必须返回明确的 action 与 matched_rules
- 敏感信息脱敏必须彻底，不得部分残留
- 二次确认操作必须等待用户显式同意后才执行，不得绕过
- 风险规则库变更必须留审计日志，禁止运行时静默修改
