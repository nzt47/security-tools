---
id: pd-subagent-driven-development-8c375695-skill
name: subagent-driven-development
description: Use when executing implementation plans with independent tasks in the
  current session。由 1 份素材蒸馏生成
content_type: markdown
category: custom
tags:
- external
- development
- from_knowledge
- subagent
- distilled
- driven
author: process_distill
source: knowledge_distill
status: approved
enabled: true
---

# subagent-driven-development

Use when executing implementation plans with independent tasks in the current session。由 1 份素材蒸馏生成

## 触发条件
- `subagent`
- `driven`
- `development`

## 步骤清单
### 步骤 1: （纯指令）
- Same session (no context switch)

### 步骤 2: （纯指令）
- Fresh subagent per task (no context pollution)

### 步骤 3: （纯指令）
- Two-stage review after each task: spec compliance first, then code quality

### 步骤 4: （纯指令）
- Faster iteration (no human-in-loop between tasks)

### 步骤 5: （纯指令）
- Touches 1-2 files with a complete spec → cheap model

### 步骤 6: （纯指令）
- Touches multiple files with integration concerns → standard model

### 步骤 7: （纯指令）
- Requires design judgment or broad codebase understanding → most capable model

### 步骤 8: （纯指令）
- If it's a context problem, provide more context and re-dispatch with the same model

### 步骤 9: （纯指令）
- If the task requires more reasoning, re-dispatch with a more capable model

### 步骤 10: （纯指令）
- If the task is too large, break it into smaller pieces

### 步骤 11: （纯指令）
- If the plan itself is wrong, escalate to the human

### 步骤 12: （纯指令）
- ./implementer-prompt.md - Dispatch implementer subagent

### 步骤 13: （纯指令）
- ./spec-reviewer-prompt.md - Dispatch spec compliance reviewer subagent

### 步骤 14: （纯指令）
- ./code-quality-reviewer-prompt.md - Dispatch code quality reviewer subagent

### 步骤 15: （纯指令）
- Implemented install-hook command

### 步骤 16: （纯指令）
- Added tests, 5/5 passing

### 步骤 17: （纯指令）
- Self-review: Found I missed --force flag, added it

### 步骤 18: （纯指令）
- Committed

### 步骤 19: （纯指令）
- Added verify/repair modes

### 步骤 20: （纯指令）
- 8/8 tests passing

### 步骤 21: （纯指令）
- Self-review: All good

### 步骤 22: （纯指令）
- Committed

### 步骤 23: （纯指令）
- Missing: Progress reporting (spec says "report every 100 items")

### 步骤 24: （纯指令）
- Extra: Added --json flag (not requested)

### 步骤 25: （纯指令）
- Subagents follow TDD naturally

### 步骤 26: （纯指令）
- Fresh context per task (no confusion)

### 步骤 27: （纯指令）
- Parallel-safe (subagents don't interfere)

### 步骤 28: （纯指令）
- Subagent can ask questions (before AND during work)

### 步骤 29: （纯指令）
- Same session (no handoff)

### 步骤 30: （纯指令）
- Continuous progress (no waiting)

## 来源
- subagent-driven-development