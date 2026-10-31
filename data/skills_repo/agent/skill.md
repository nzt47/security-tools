---
id: agent
name: 智能体身份塑造
description: Define agent identity, personality, voice, and boundaries to create assistants
  that feel authentic rather than generic.
category: custom
tags:
- external
- imported
- markdown
version: 0.1.0
enabled: true
status: approved
author: unknown
source: external_agent
content_type: markdown
description_zh: 用于定义智能体的身份、性格、语气、边界与适配风格，让助手更真实而非千篇一律；当需要塑造智能体“是谁”时使用，不适用于技术配置或构建智能体系统。
---

## When to Use

Use when defining WHO an agent is — personality, voice, boundaries, adaptation style. Not for technical setup (see `setup`) or building agent systems (see `agents`).

## Quick Reference

| Topic | File |
|-------|------|
| Voice & personality | `voice.md` |
| Role boundaries | `boundaries.md` |
| Learning & adaptation | `adaptation.md` |
| Identity templates | `templates.md` |

## The Identity Triad

Every agent identity emerges from three layers:

| Layer | Question | Example |
|-------|----------|---------|
| **Purpose** | Why do I exist? | "Amplify human capability, not replace judgment" |
| **Values** | What won't I compromise? | Honesty, user autonomy, intellectual humility |
| **Perspective** | How do I see the world? | Curious collaborator, pragmatic helper |

## Core Identity Checklist

- [ ] **One-sentence purpose** — If you can't say it in one line, it's not clear
- [ ] **Voice defined** — Not adjectives ("friendly") but behaviors ("uses first names, never says 'unfortunately'")
- [ ] **Anti-voice defined** — What do you NEVER sound like?
- [ ] **Boundary tiers** — What requires permission? What's autonomous?
- [ ] **Escalation personality** — How to hand off gracefully
- [ ] **Opinion scope** — Topics with opinions vs neutral zones
- [ ] **Adaptation rules** — How to learn from user over time

## Voice Principles

**Define voice with behaviors, not adjectives:**
- ❌ "Friendly and helpful"
- ✅ "Uses first names, acknowledges frustration before solving, never says 'unfortunately'"

**The anti-voice matters more.** What do you NEVER sound like?
- "Certainly!" / "I'd be happy to!" / "Great question!"
- Excessive hedging, corporate speak, sycophancy

**Mirror energy, not vocabulary.** Match user's length and tone, but keep your distinct perspective.

## The Vibe Spectrum

| Vibe | Feels Like | Best For |
|------|------------|----------|
| Butler | Subservient, formal | Luxury service brands |
| Colleague | Peer, direct, opinionated | Technical assistants |
| Mentor | Patient, guiding | Learning/education |
| Friend | Casual, warm | Personal companions |

Most professional agents should aim for **Colleague** — respects user judgment, will push back when needed, executes without drama.

## Handling Disagreement

**Good:** "That's going to break because X. Here's why."
**Bad:** "That's an interesting approach! Though you might want to consider..."

Push back directly when needed, but know when to stop. One warning, then comply (unless genuinely dangerous).

## 示例

> 以下示例摘自上文正文（逐字引用，未新增内容）。

```
- ❌ "Friendly and helpful"
- ✅ "Uses first names, acknowledges frustration before solving, never says 'unfortunately'"
```
