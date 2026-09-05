---
id: pd-dispatching-parallel-agents-b8065ccd-skill
name: dispatching-parallel-agents
description: Use when facing 2+ independent tasks that can be worked on without shared
  state or sequential dependencies。由 1 份素材蒸馏生成
content_type: markdown
category: custom
tags:
- parallel
- external
- dispatching
- from_knowledge
- agents
- distilled
author: process_distill
source: knowledge_distill
status: approved
enabled: true
---

# dispatching-parallel-agents

Use when facing 2+ independent tasks that can be worked on without shared state or sequential dependencies。由 1 份素材蒸馏生成

## 触发条件
- `dispatching`
- `parallel`
- `agents`

## 步骤清单
### 步骤 1: （纯指令）
- 3+ test files failing with different root causes

### 步骤 2: （纯指令）
- Multiple subsystems broken independently

### 步骤 3: （纯指令）
- Each problem can be understood without context from others

### 步骤 4: （纯指令）
- No shared state between investigations

### 步骤 5: （纯指令）
- Failures are related (fix one might fix others)

### 步骤 6: （纯指令）
- Need to understand full system state

### 步骤 7: （纯指令）
- Agents would interfere with each other

### 步骤 8: （纯指令）
- File A tests: Tool approval flow

### 步骤 9: （纯指令）
- File B tests: Batch completion behavior

### 步骤 10: （纯指令）
- File C tests: Abort functionality

### 步骤 11: （纯指令）
- Specific scope: One test file or subsystem

### 步骤 12: （纯指令）
- Clear goal: Make these tests pass

### 步骤 13: （纯指令）
- Constraints: Don't change other code

### 步骤 14: （纯指令）
- Expected output: Summary of what you found and fixed

### 步骤 15: （纯指令）
- Read each summary

### 步骤 16: （纯指令）
- Verify fixes don't conflict

### 步骤 17: （纯指令）
- Run full test suite

### 步骤 18: （纯指令）
- Integrate all changes

### 步骤 19: （纯指令）
- Focused - One clear problem domain

### 步骤 20: （纯指令）
- Self-contained - All context needed to understand the problem

### 步骤 21: （纯指令）
- Specific about output - What should the agent return?

### 步骤 22: （纯指令）
- "should abort tool with partial output capture" - expects 'interrupted at' in message

### 步骤 23: （纯指令）
- "should handle mixed completed and aborted tools" - fast tool aborted instead of completed

### 步骤 24: （纯指令）
- "should properly track pendingToolCount" - expects 3 results but gets 0

### 步骤 25: （纯指令）
- Read the test file and understand what each test verifies

### 步骤 26: （纯指令）
- Identify root cause - timing issues or actual bugs?

### 步骤 27: （纯指令）
- Fix by:

### 步骤 28: （纯指令）
- Replacing arbitrary timeouts with event-based waiting

### 步骤 29: （纯指令）
- Fixing bugs in abort implementation if found

### 步骤 30: （纯指令）
- Adjusting test expectations if testing changed behavior

## 来源
- dispatching-parallel-agents