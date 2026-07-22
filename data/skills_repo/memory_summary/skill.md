---
id: memory_summary
name: memory_summary
description: "记忆摘要技能 — 对长对话或历史记忆做结构化压缩，保留关键事实与决策。适用于总结对话历史、压缩记忆、梳理历史、归纳之前的内容等场景"
category: custom
tags: [memory_summary, summary, compression, memory, 记忆摘要, 总结, 压缩, 梳理历史, 对话历史]
version: 0.1.0
enabled: true
status: approved
author: unknown
source: legacy_migration
content_type: markdown
---

# memory_summary

记忆摘要技能 — 对长对话或历史记忆做结构化压缩，保留关键事实与决策。

## 适用场景

- 单轮对话超过上下文窗口阈值时，触发前文压缩
- 跨会话恢复时，加载历史摘要替代全量历史
- 长期记忆检索前，按主题归并相似条目

## 触发条件

- 上下文 token 用量超过阈值（默认 80%）
- 用户主动请求"总结之前的内容 / 梳理历史"
- 跨会话恢复且历史超过 N 轮（默认 20）

## 摘要结构

每条摘要包含以下字段：

- `topic`: 主题（一句话）
- `facts`: 关键事实列表（不可省略数字、人名、时间）
- `decisions`: 已达成的决策与决策依据
- `open_questions`: 未解决的问题
- `timestamp`: 摘要生成时间
- `source_refs`: 原始消息定位（会话ID + 消息序号区间）

## 使用方式

1. 按时间窗或主题切分原始记忆
2. 抽取每段的事实与决策，丢弃寒暄与冗余
3. 合并同主题条目，跨段时间线保持顺序
4. 写入摘要时附带 `source_refs` 以便回溯

## 不变量

- 数字、人名、时间必须原样保留，不得改写
- 决策依据不可省略
- 摘要必须可追溯到原始消息，不可凭印象生成
