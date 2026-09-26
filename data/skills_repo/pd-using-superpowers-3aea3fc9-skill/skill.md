---
id: pd-using-superpowers-3aea3fc9-skill
name: using-superpowers
description: Use when starting any conversation - establishes how to find and use
  skills, requiring Skill tool invocation before ANY response including clarifying
  questions。由 1 份素材蒸馏生成
content_type: markdown
category: custom
tags:
- external
- superpowers
- from_knowledge
- using
- distilled
author: process_distill
source: knowledge_distill
status: approved
enabled: true
description_zh: 在每次开启对话时使用——用于确立查找和使用技能的方式，要求在做出任何回应（包括澄清性问题）之前，都必须先调用技能工具（Skill tool）。由
  1 份素材蒸馏生成
---

# using-superpowers

Use when starting any conversation - establishes how to find and use skills, requiring Skill tool invocation before ANY response including clarifying questions。由 1 份素材蒸馏生成

## 触发条件
- `using`
- `superpowers`

## 步骤清单
### 步骤 1: （纯指令）
- User's explicit instructions (CLAUDE.md, GEMINI.md, AGENTS.md, direct requests) — highest priority

### 步骤 2: （纯指令）
- Superpowers skills — override default system behavior where they conflict

### 步骤 3: （纯指令）
- Default system prompt — lowest priority

### 步骤 4: （纯指令）
- Process skills first (brainstorming, debugging) - these determine HOW to approach the task

## 来源
- using-superpowers
