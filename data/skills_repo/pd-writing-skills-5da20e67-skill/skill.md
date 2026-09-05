---
id: pd-writing-skills-5da20e67-skill
name: writing-skills
description: '适用于创建、编辑或验证 agent 技能（SKILL.md）之前或过程中，核心是将 TDD 应用于流程文档编写。。由 1 份素材蒸馏生成。预期产出:
  一份经 RED-GREEN-REFACTOR 验证、无已知漏洞且可被其他 agent 正确触发和使用的 SKILL.md 文档。'
content_type: markdown
category: custom
tags:
- 创建技能
- external
- 验证技能
- 编辑技能
- from_knowledge
- distilled
author: process_distill
source: knowledge_distill
status: approved
enabled: true
---

# writing-skills

适用于创建、编辑或验证 agent 技能（SKILL.md）之前或过程中，核心是将 TDD 应用于流程文档编写。。由 1 份素材蒸馏生成。预期产出: 一份经 RED-GREEN-REFACTOR 验证、无已知漏洞且可被其他 agent 正确触发和使用的 SKILL.md 文档。

## 触发条件
- `创建技能`
- `编辑技能`
- `验证技能`
- `SKILL.md`

## 步骤清单
### 步骤 1: （纯指令）
- 运行无技能时的基线压力场景，观察 agent 在未使用技能时的违规行为
- 边界: RED 阶段，先用子代理执行压力场景，记录其自然行为与合理化借口

### 步骤 2: （纯指令）
- 记录 agent 在基线测试中使用的具体合理化理由
- 边界: 逐字记录，用于后续在技能中显式封堵

### 步骤 3: （纯指令）
- 编写最小化技能文档 SKILL.md，仅针对基线测试中观察到的违规行为
- 边界: GREEN 阶段，不添加假设性内容；遵循 SKILL.md 格式与黄金法则

### 步骤 4: （纯指令）
- 用相同压力场景重新运行测试，确认 agent 在技能存在时遵守规则
- 边界: 验证通过则进入 REFACTOR；否则继续补充技能

### 步骤 5: （纯指令）
- 识别 agent 新找出的漏洞或合理化方式，在技能中显式添加反制条款
- 边界: REFACTOR 阶段，每次发现新漏洞即补充并重新测试

### 步骤 6: （纯指令）
- 反复运行测试和补充技能，直到技能在各种压力场景下保持有效
- 边界: 涵盖纪律型、技法型、模式型、参考型技能的不同测试方法

### 步骤 7: （纯指令）
- 执行部署前验证，确保当前技能已通过测试后再进入下一个技能
- 边界: 禁止批量创建未经测试的技能；必须完成测试与修补后停止并交付

## 来源
- writing-skills