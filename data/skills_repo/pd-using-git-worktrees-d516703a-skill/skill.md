---
id: pd-using-git-worktrees-d516703a-skill
name: using-git-worktrees
description: Use when starting feature work that needs isolation from current workspace
  or before executing implementation plans - ensures an isolated workspace exists
  via native tools or git worktree fallback。由 1 份素材蒸馏生成
content_type: markdown
category: custom
tags:
- external
- worktrees
- from_knowledge
- using
- git
- distilled
author: process_distill
source: knowledge_distill
status: approved
enabled: true
---

# using-git-worktrees

Use when starting feature work that needs isolation from current workspace or before executing implementation plans - ensures an isolated workspace exists via native tools or git worktree fallback。由 1 份素材蒸馏生成

## 触发条件
- `using`
- `git`
- `worktrees`

## 步骤清单
### 步骤 1: （纯指令）
- On a branch: "Already in isolated workspace at <path> on branch <name>."

### 步骤 2: （纯指令）
- Detached HEAD: "Already in isolated workspace at <path> (detached HEAD, externally managed). Branch creation needed at finish time."

### 步骤 3: （纯指令）
- Check your instructions for a declared worktree directory preference. If the user has already specified one, use it without asking.

### 步骤 4: （纯指令）
- Check for an existing project-local worktree directory:

### 步骤 5: （纯指令）
- Check for an existing global directory:

### 步骤 6: （纯指令）
- If there is no other guidance available, default to .worktrees/ at the project root.

### 步骤 7: （纯指令）
- Problem: Using git worktree add when the platform already provides isolation

### 步骤 8: （纯指令）
- Fix: Step 0 detects existing isolation. Step 1a defers to native tools.

### 步骤 9: （纯指令）
- Problem: Creating a nested worktree inside an existing one

### 步骤 10: （纯指令）
- Fix: Always run Step 0 before creating anything

### 步骤 11: （纯指令）
- Problem: Worktree contents get tracked, pollute git status

### 步骤 12: （纯指令）
- Fix: Always use git check-ignore before creating project-local worktree

### 步骤 13: （纯指令）
- Problem: Creates inconsistency, violates project conventions

### 步骤 14: （纯指令）
- Fix: Follow priority: existing > global legacy > instruction file > default

### 步骤 15: （纯指令）
- Problem: Can't distinguish new bugs from pre-existing issues

### 步骤 16: （纯指令）
- Fix: Report failures, get explicit permission to proceed

### 步骤 17: （纯指令）
- Create a worktree when Step 0 detects existing isolation

### 步骤 18: （纯指令）
- Use git worktree add when you have a native worktree tool (e.g., EnterWorktree). This is the 1 mistake — if you have it, use it.

### 步骤 19: （纯指令）
- Skip Step 1a by jumping straight to Step 1b's git commands

### 步骤 20: （纯指令）
- Create worktree without verifying it's ignored (project-local)

### 步骤 21: （纯指令）
- Skip baseline test verification

### 步骤 22: （纯指令）
- Proceed with failing tests without asking

### 步骤 23: （纯指令）
- Run Step 0 detection first

### 步骤 24: （纯指令）
- Prefer native tools over git fallback

### 步骤 25: （纯指令）
- Follow directory priority: existing > global legacy > instruction file > default

### 步骤 26: （纯指令）
- Verify directory is ignored for project-local

### 步骤 27: （纯指令）
- Auto-detect and run project setup

### 步骤 28: （纯指令）
- Verify clean test baseline

## 来源
- using-git-worktrees