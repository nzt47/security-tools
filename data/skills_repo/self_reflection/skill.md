---
id: self_reflection
name: self_reflection
description: "自我反思技能 — 让模型回顾自身推理与回答过程，识别可能的疏漏并改进。适用于复查、核对、自检、反思、检查回答逻辑漏洞等场景"
category: custom
tags: [self_reflection, reflection, review, self_check, 自我反思, 反思, 复查, 核对, 自检]
version: 0.1.0
enabled: true
status: approved
author: unknown
source: legacy_migration
content_type: markdown
---

# self_reflection

自我反思技能 — 让模型回顾自身推理与回答过程，识别可能的疏漏并改进。

## 适用场景

- 多步推理完成后，回放关键步骤查找逻辑漏洞
- 长回答输出前，自检事实准确性、逻辑一致性与完整性
- 用户质疑回答时，复盘上一轮回答定位偏差来源

## 触发条件

- 任务包含"复查 / 核对 / 自检 / 反思"等语义
- 输出超过 3 段或涉及多个事实断言时默认启用

## 使用方式

1. 模型完成初稿后，调用本技能对核心论点逐一回放
2. 标记存疑项并附原始依据，不可凭印象补全
3. 仅修正确证的错误，未确证的保留原判断并注明

## 输出特征

- 错误项列表（含原文位置 + 修正建议）
- 未确证项列表（含待核实的依据）
- 整体置信度评分（0-1）

## 不变量

- 不得编造依据；引用必须可追溯
- 修正动作必须显式列出，禁止静默改写
