---
id: handoff
name: 会话交接文档生成器
description: Compact the current conversation into a handoff document for another
  agent to pick up.
version: 1.0.0
author: ''
category: claude
source: claude-skills
tags:
- claude-skills
content_type: markdown
enabled: true
description_zh: 用于将当前对话压缩整理为交接文档，供新代理继续工作；当需要转交任务、切换代理或让其他代理接手时触发。文档保存至系统临时目录，包含建议技能，并引用已有产物路径或链接，同时隐去敏感信息。
---

## When to Use

- Compact the current conversation into a handoff document for another agent to pick up

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Save to the temporary directory of the user's OS - not the current workspace.

Include a "suggested skills" section in the document, which suggests skills that the agent should invoke.

Do not duplicate content already captured in other artifacts (PRDs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

Redact any sensitive information, such as API keys, passwords, or personally identifiable information.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.

## 示例

> 以下示例摘自上文正文（逐字引用，未新增内容）。

```
- Compact the current conversation into a handoff document for another agent to pick up
```
