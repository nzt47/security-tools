---
id: pd-writing-plans-f846e3a2-skill
name: writing-plans
description: Use when you have a spec or requirements for a multi-step task, before
  touching code。由 1 份素材蒸馏生成
content_type: markdown
category: custom
tags:
- external
- plans
- from_knowledge
- writing
- distilled
author: process_distill
source: knowledge_distill
status: approved
enabled: true
---

# writing-plans

Use when you have a spec or requirements for a multi-step task, before touching code。由 1 份素材蒸馏生成

## 触发条件
- `writing`
- `plans`

## 步骤清单
### 步骤 1: （纯指令）
- Design units with clear boundaries and well-defined interfaces. Each file should have one clear responsibility.

### 步骤 2: （纯指令）
- You reason best about code you can hold in context at once, and your edits are more reliable when files are focused. Prefer smaller, focused files over large ones that do too much.

### 步骤 3: （纯指令）
- Files that change together should live together. Split by responsibility, not by technical layer.

### 步骤 4: （纯指令）
- In existing codebases, follow established patterns. If the codebase uses large files, don't unilaterally restructure - but if a file you're modifying has grown unwieldy, including a split in the plan is reasonable.

### 步骤 5: （纯指令）
- "Write the failing test" - step

### 步骤 6: （纯指令）
- "Run it to make sure it fails" - step

### 步骤 7: （纯指令）
- "Implement the minimal code to make the test pass" - step

### 步骤 8: （纯指令）
- "Run the tests and make sure they pass" - step

### 步骤 9: （纯指令）
- "Commit" - step

### 步骤 10: （纯指令）
- Create: exact/path/to/file.py

### 步骤 11: （纯指令）
- Modify: exact/path/to/existing.py:123-145

### 步骤 12: （纯指令）
- Test: tests/exact/path/to/test.py

### 步骤 13: （纯指令）
- "TBD", "TODO", "implement later", "fill in details"

### 步骤 14: （纯指令）
- "Add appropriate error handling" / "add validation" / "handle edge cases"

### 步骤 15: （纯指令）
- "Write tests for the above" (without actual test code)

### 步骤 16: （纯指令）
- "Similar to Task N" (repeat the code — the engineer may be reading tasks out of order)

### 步骤 17: （纯指令）
- Steps that describe what to do without showing how (code blocks required for code steps)

### 步骤 18: （纯指令）
- References to types, functions, or methods not defined in any task

### 步骤 19: （纯指令）
- Exact file paths always

### 步骤 20: （纯指令）
- Complete code in every step — if a step changes code, show the code

### 步骤 21: （纯指令）
- Exact commands with expected output

### 步骤 22: （纯指令）
- DRY, YAGNI, TDD, frequent commits

### 步骤 23: （纯指令）
- REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

### 步骤 24: （纯指令）
- Fresh subagent per task + two-stage review

### 步骤 25: （纯指令）
- REQUIRED SUB-SKILL: Use superpowers:executing-plans

### 步骤 26: （纯指令）
- Batch execution with checkpoints for review

## 来源
- writing-plans