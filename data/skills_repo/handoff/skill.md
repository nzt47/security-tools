---
id: handoff
name: handoff
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
description_zh: 将当前对话压缩成一份交接文档，供其他代理人员接手。
---

## When to Use

- Compact the current conversation into a handoff document for another agent to pick up

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Save to the temporary directory of the user's OS - not the current workspace.

Include a "suggested skills" section in the document, which suggests skills that the agent should invoke.

Do not duplicate content already captured in other artifacts (PRDs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

Redact any sensitive information, such as API keys, passwords, or personally identifiable information.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.
