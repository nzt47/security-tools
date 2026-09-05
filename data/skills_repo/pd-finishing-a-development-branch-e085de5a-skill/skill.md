---
id: pd-finishing-a-development-branch-e085de5a-skill
name: finishing-a-development-branch
description: Use when implementation is complete, all tests pass, and you need to
  decide how to integrate the work - guides completion of development work by presenting
  structured options for merge, PR, or cleanup。由 1 份素材蒸馏生成
content_type: markdown
category: custom
tags:
- branch
- external
- finishing
- development
- from_knowledge
- distilled
author: process_distill
source: knowledge_distill
status: approved
enabled: true
---

# finishing-a-development-branch

Use when implementation is complete, all tests pass, and you need to decide how to integrate the work - guides completion of development work by presenting structured options for merge, PR, or cleanup。由 1 份素材蒸馏生成

## 触发条件
- `finishing`
- `development`
- `branch`

## 步骤清单
### 步骤 1: （纯指令）
- Merge back to <base-branch> locally

### 步骤 2: （纯指令）
- Push and create a Pull Request

### 步骤 3: （纯指令）
- Keep the branch as-is (I'll handle it later)

### 步骤 4: （纯指令）
- Discard this work

### 步骤 5: （纯指令）
- Push as new branch and create a Pull Request

### 步骤 6: （纯指令）
- Keep as-is (I'll handle it later)

### 步骤 7: （纯指令）
- Discard this work

### 步骤 8: （纯指令）
- <verification steps>

### 步骤 9: （纯指令）
- Branch <name>

### 步骤 10: （纯指令）
- All commits: <commit-list>

### 步骤 11: （纯指令）
- Worktree at <path>

### 步骤 12: （纯指令）
- Problem: Merge broken code, create failing PR

### 步骤 13: （纯指令）
- Fix: Always verify tests before offering options

### 步骤 14: （纯指令）
- Problem: "What should I do next?" is ambiguous

### 步骤 15: （纯指令）
- Fix: Present exactly 4 structured options (or 3 for detached HEAD)

### 步骤 16: （纯指令）
- Problem: Remove worktree user needs for PR iteration

### 步骤 17: （纯指令）
- Fix: Only cleanup for Options 1 and 4

### 步骤 18: （纯指令）
- Problem: git branch -d fails because worktree still references the branch

### 步骤 19: （纯指令）
- Fix: Merge first, remove worktree, then delete branch

### 步骤 20: （纯指令）
- Problem: Command fails silently when CWD is inside the worktree being removed

### 步骤 21: （纯指令）
- Fix: Always cd to main repo root before git worktree remove

### 步骤 22: （纯指令）
- Problem: Removing a worktree the harness created causes phantom state

### 步骤 23: （纯指令）
- Fix: Only clean up worktrees under .worktrees/, worktrees/, or ~/.config/superpowers/worktrees/

### 步骤 24: （纯指令）
- Problem: Accidentally delete work

### 步骤 25: （纯指令）
- Fix: Require typed "discard" confirmation

### 步骤 26: （纯指令）
- Proceed with failing tests

### 步骤 27: （纯指令）
- Merge without verifying tests on result

### 步骤 28: （纯指令）
- Delete work without confirmation

### 步骤 29: （纯指令）
- Force-push without explicit request

### 步骤 30: （纯指令）
- Remove a worktree before confirming merge success

## 来源
- finishing-a-development-branch