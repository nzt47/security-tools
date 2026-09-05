---
id: pd-brainstorming-697b717a-skill
name: brainstorming
description: You MUST use this before any creative work - creating features, building
  components, adding functionality, or modifying behavior. Explores user intent, requirements
  and design before implementation.。由 1 份素材蒸馏生成
content_type: markdown
category: custom
tags:
- brainstorming
- external
- distilled
- from_knowledge
author: process_distill
source: knowledge_distill
status: approved
enabled: true
---

# brainstorming

You MUST use this before any creative work - creating features, building components, adding functionality, or modifying behavior. Explores user intent, requirements and design before implementation.。由 1 份素材蒸馏生成

## 触发条件
- `brainstorming`

## 步骤清单
### 步骤 1: （纯指令）
- Explore project context — check files, docs, recent commits

### 步骤 2: （纯指令）
- Offer visual companion (if topic will involve visual questions) — this is its own message, not combined with a clarifying question. See the Visual Companion section below.

### 步骤 3: （纯指令）
- Ask clarifying questions — one at a time, understand purpose/constraints/success criteria

### 步骤 4: （纯指令）
- Propose 2-3 approaches — with trade-offs and your recommendation

### 步骤 5: （纯指令）
- Present design — in sections scaled to their complexity, get user approval after each section

### 步骤 6: （纯指令）
- Write design doc — save to docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md and commit

### 步骤 7: （纯指令）
- Spec self-review — quick inline check for placeholders, contradictions, ambiguity, scope (see below)

### 步骤 8: （纯指令）
- User reviews written spec — ask user to review the spec file before proceeding

### 步骤 9: （纯指令）
- Transition to implementation — invoke writing-plans skill to create implementation plan

### 步骤 10: （纯指令）
- Check out the current project state first (files, docs, recent commits)

### 步骤 11: （纯指令）
- Before asking detailed questions, assess scope: if the request describes multiple independent subsystems (e.g., "build a platform with chat, file storage, billing, and analytics"), flag this immediately. Don't spend questions refining details of a project that needs to be decomposed first.

### 步骤 12: （纯指令）
- If the project is too large for a single spec, help the user decompose into sub-projects: what are the independent pieces, how do they relate, what order should they be built? Then brainstorm the first sub-project through the normal design flow. Each sub-project gets its own spec → plan → implementa

### 步骤 13: （纯指令）
- For appropriately-scoped projects, ask questions one at a time to refine the idea

### 步骤 14: （纯指令）
- Prefer multiple choice questions when possible, but open-ended is fine too

### 步骤 15: （纯指令）
- Only one question per message - if a topic needs more exploration, break it into multiple questions

### 步骤 16: （纯指令）
- Focus on understanding: purpose, constraints, success criteria

### 步骤 17: （纯指令）
- Propose 2-3 different approaches with trade-offs

### 步骤 18: （纯指令）
- Present options conversationally with your recommendation and reasoning

### 步骤 19: （纯指令）
- Lead with your recommended option and explain why

### 步骤 20: （纯指令）
- Once you believe you understand what you're building, present the design

### 步骤 21: （纯指令）
- Scale each section to its complexity: a few sentences if straightforward, up to 200-300 words if nuanced

### 步骤 22: （纯指令）
- Ask after each section whether it looks right so far

### 步骤 23: （纯指令）
- Cover: architecture, components, data flow, error handling, testing

### 步骤 24: （纯指令）
- Be ready to go back and clarify if something doesn't make sense

### 步骤 25: （纯指令）
- Break the system into smaller units that each have one clear purpose, communicate through well-defined interfaces, and can be understood and tested independently

### 步骤 26: （纯指令）
- For each unit, you should be able to answer: what does it do, how do you use it, and what does it depend on?

### 步骤 27: （纯指令）
- Can someone understand what a unit does without reading its internals? Can you change the internals without breaking consumers? If not, the boundaries need work.

### 步骤 28: （纯指令）
- Smaller, well-bounded units are also easier for you to work with - you reason better about code you can hold in context at once, and your edits are more reliable when files are focused. When a file grows large, that's often a signal that it's doing too much.

### 步骤 29: （纯指令）
- Explore the current structure before proposing changes. Follow existing patterns.

### 步骤 30: （纯指令）
- Where existing code has problems that affect the work (e.g., a file that's grown too large, unclear boundaries, tangled responsibilities), include targeted improvements as part of the design - the way a good developer improves code they're working in.

## 来源
- brainstorming