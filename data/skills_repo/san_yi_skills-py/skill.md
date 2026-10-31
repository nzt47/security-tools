---
id: san_yi_skills-py
name: 易经三义编程技能工具集
description: 易经三义编程工具集：用「不易 / 变易 / 简易」三义做结构化输出（BuYiResult / BianYiResult / JianYiResult）与校验。
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
description_zh: 用于按不易、变易、简易三义评估编程方案：识别不可变约束与安全边界，评估适配性及过度/不足设计风险，生成推荐模式与增量步骤；在代码变更、架构设计或重构决策时触发。
---

san_yi_skills.py - 易经三义编程技能工具集
from typing import Literal
from pydantic import BaseModel, Field


# ========== Output Models ==========
class BuYiResult(BaseModel)
    invariants list[str] = Field(description=不可变约束清单)
    risk_level Literal[low, medium, high, critical]
    blocked_changes list[str] = Field(default_factory=list)
    safe_boundary str


class BianYiResult(BaseModel)
    adaptability_score int = Field(ge=1, le=10)
    over_design_risks list[str]
    under_design_risks list[str]
    recommended_pattern str
    incremental_steps list[str]


class JianYiResult(BaseModel)
    complexity_issues list[dict]
    simplified_version str
    deletion_candidates list[str] = Field(default_factory=list)
    readability_verified bool


# ========== Skill Tools ==========
SAN_YI_SYSTEM_PROMPT = 你是易经三义软件工程专家。严格按指定技能协议输出JSON，不附加任何解释文字。


async def analyze_bu_yi(task_context str, existing_artifacts str = ) - BuYiResult
    [不易分析器] 识别系统不变量与安全变更边界。在重构API变更Schema修改前调用。
    prompt = f执行 bu_yi_analyzer 技能
## 任务 {task_context}
## 现有资产 {existing_artifacts or '未提供'}
## 要求
1. 列出所有不可变约束(业务规则API契约Schema核心字段安全边界测试行为)
2. 评估风险等级
3. 标出当前请求中触碰不变量的危险操作
4. 定义可安全修改的范围
输出严格符合 BuYiResult JSON Schema。
    # TODO 替换为实际 LLM 调用
    # response = await llm.chat(system=SAN_YI_SYSTEM_PROMPT, user=prompt, response_model=BuYiResult)
    # return response
    raise NotImplementedError(接入您的 LLM Provider)


async def advise_bian_yi(
    feature_request str,
    project_stage Literal[prototype, mvp, production, legacy],
    current_architecture str = 
) - BianYiResult
    [变易顾问] 评估扩展性并给出渐进式设计建议。在架构设计技术选型新功能实现时调用。
    prompt = f执行 bian_yi_advisor 技能
## 需求 {feature_request}
## 项目阶段 {project_stage}
## 当前架构 {current_architecture or '未提供'}
## 要求
1. 评分适应性(1-10)
2. 分别列出过度设计与设计不足的风险
3. 推荐恰到好处的模式扩展点
4. 拆解为可独立验证回滚的渐进步骤
5. ⚠️ prototypemvp 阶段禁止推荐抽象层2的方案
输出严格符合 BianYiResult JSON Schema。
    raise NotImplementedError(接入您的 LLM Provider)


async def simplify_jian_yi(
    code_or_plan str,
    readability_target Literal[junior, mid, senior] = junior
) - JianYiResult
    [简易简化器] 执行奥卡姆剃刀审查，输出最简等价实现。在Code Review代码生成后调用。
    prompt = f执行 jian_yi_simplifier 技能
## 待审查内容 {code_or_plan}
## 可读性目标 {readability_target}级工程师30秒内可理解
## 要求
1. 逐条列出复杂度问题及修复方式
2. 输出简化后的完整版本(行数必须=原版)
3. 标出可安全删除的代码段
4. 验证是否达到可读性目标
输出严格符合 JianYiResult JSON Schema。
    raise NotImplementedError(接入您的 LLM Provider)

## 示例

> 以下示例摘自上文正文（逐字引用，未新增内容）。

```
from typing import Literal
from pydantic import BaseModel, Field


# ========== Output Models ==========
class BuYiResult(BaseModel)
    invariants list[str] = Field(description=不可变约束清单)
    risk_level Literal[low, medium, high, critical]
    blocked_changes list[str] = Field(default_factory=list)
    safe_boundary str
```
