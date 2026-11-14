---
title: claude 偏好与规则（外部记忆导入）
slug: claude-偏好与规则-外部记忆导入
status: current
type: concepts
source: import:claude:static
date: '2026-10-24'
tags: [claude, knowledge_card, user, static]
links: []
topic: general
contradictions: []
insight: 使用中文进行所有对话和注释
scope: ''
metadata: {import_batch: mb_d01efa678ada, records: 4, source: claude}
---

# claude user static（外部记忆导入）

> 共 4 条；本段由规则汇总，可直接删除单条后再提交。

## 语言偏好
- 使用中文进行所有对话和注释
- 代码注释使用中文
## 工作流程
1. 理解需求后，先规划实现方案
2. 直接执行，无需请求确认（包括工具调用、代码修改等）
3. 完成后进行基本的语法检查
4. 如有疑问，及时询问确认
## 工具使用
- 优先使用 Read、Edit、Glob、Grep 工具
- 避免不必要的 bash 命令
- 执行工具调用时直接执行，无需请求确认
- 破坏性操作（删除大量文件、force push 等）谨慎处理但无需弹窗确认
## 开发规范
- 遵循项目现有的代码风格
- 保持代码简洁、可读
- 编写必要的注释说明复杂逻辑
