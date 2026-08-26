"""工作流模式分类器 — DAG vs Agent 判断

依据: docs/workflow_dag_vs_agent.md §2 判定规则 (按优先级)
    1. 分支数 > 3      → agent   (条件节点组合爆炸, DAG 失去可维护性)
    2. 步骤数 > 10     → agent   (单 DAG 过长可读性差)
    3. 串联无分支      → dag     (线性流水线, 黑板数据流清晰)
    4. 有条件分支 ≤ 3  → dag_conditional (用 WorkflowStep.condition 表达分支谓词)

不变量 (【不易】):
    - 纯函数, 无副作用, 无 I/O —— 可在任何上下文安全调用 (含锁内)
    - 不修改 steps, 不依赖外部状态
    - 返回值仅三种: "dag" / "dag_conditional" / "agent"

架构层级: [TLM-L1] 模式判断 - 决策层
"""
from __future__ import annotations

from typing import Iterable, List

from .models import WorkflowStep


# 返回值类型 (用字符串字面量, 避免引入 enum 增加耦合 — 【简易】)
WorkflowMode = str  # "dag" | "dag_conditional" | "agent"

# 判断阈值 (与 docs/workflow_dag_vs_agent.md §2 判定规则一致)
# 公开常量 — 供 executor 日志引用, 保持单一数据源 (DRY)
AGENT_BRANCH_THRESHOLD = 3       # 分支数 > 3 → Agent
AGENT_STEP_THRESHOLD = 10        # 步骤数 > 10 → Agent
_DAG_LINEAR_STEP_LIMIT = 5       # 串联 ≤ 5 步 (仅用于文档对齐, 不影响判断)


def count_branches(steps: Iterable[WorkflowStep]) -> int:
    """统计条件分支数

    分支数 = 带 condition 的步骤数 (每个条件节点产生一个分支路径)。
    与文档 §2 "分支数 ≤ 3 → DAG + 条件节点" 语义一致。

    Note:
        不做扇出 (fan-out) 计算 — 当前 WorkflowStep 无显式下游指针,
        condition 字段已是分支谓词的充分信号。若未来引入显式 DAG 邻接表,
        可扩展为 max fan-out, 但须保持本函数签名不变 (【不易】)。
    """
    return sum(1 for s in steps if s.condition)


def classify_workflow_mode(steps: Iterable[WorkflowStep]) -> WorkflowMode:
    """根据步骤列表判断工作流执行模式

    Args:
        steps: WorkflowStep 列表 (LearnedWorkflow.steps)

    Returns:
        "dag"             — 线性串联 (无条件分支)
        "dag_conditional" — DAG + 条件节点 (分支数 ≤ 3)
        "agent"           — 自由 Agent (分支数 > 3 或步骤数 > 10)

    判断优先级 (与 docs/workflow_dag_vs_agent.md §2 决策规则一致):
        1. 分支数 > 3  → agent
        2. 步骤数 > 10 → agent
        3. 无条件分支 → dag
        4. 其他        → dag_conditional

    Examples:
        >>> from agent.workflow_learning.models import WorkflowStep
        >>> linear = [WorkflowStep(step_id=f"s{i}", tool_name="t") for i in range(3)]
        >>> classify_workflow_mode(linear)
        'dag'
        >>> branching = [WorkflowStep(step_id=f"s{i}", tool_name="t",
        ...                           condition="x" if i > 0 else None) for i in range(5)]
        >>> classify_workflow_mode(branching)
        'agent'
    """
    steps_list: List[WorkflowStep] = list(steps)
    n_steps = len(steps_list)
    n_branches = count_branches(steps_list)

    # 规则 1: 分支数 > 3 → Agent (最高优先级, 组合爆炸)
    if n_branches > AGENT_BRANCH_THRESHOLD:
        return "agent"
    # 规则 2: 步骤数 > 10 → Agent (单 DAG 过长)
    if n_steps > AGENT_STEP_THRESHOLD:
        return "agent"
    # 规则 3: 无条件分支 → 线性 DAG
    if n_branches == 0:
        return "dag"
    # 规则 4: 有条件分支 但 ≤ 3 → DAG + 条件节点
    return "dag_conditional"
