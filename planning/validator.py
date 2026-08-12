"""计划验证器（阶段 2 / D11 升级）

将校验逻辑从 executor 抽取为独立公共模块，供 PlanningCore.plan() 与
execute_plan() 入口复用，使坏计划在执行前被拦截（标记 FAILED + 结构化错误），
而不是执行期卡死。

【不易】PlanValidationError 类名/语义与既有 executor.validate_plan 完全一致
（executor 改为委托本模块），对外契约不变；校验失败仍抛 PlanValidationError。
【变易】validate_plan 返回结构化错误列表（code/message/task_id），上层可据
错误码定制处理；新增"任务描述非空"检查（④）补齐规格缺口。
【简易】纯函数 + 轻量数据类，零第三方依赖；DFS 环检测与既有实现同算法。
"""

import logging
from typing import Dict, List, Optional

from .models import Plan

logger = logging.getLogger(__name__)


class PlanValidationError(Exception):
    """计划验证失败（D11 修复，保持既有类名向后兼容）"""
    pass


class PlanValidationIssue:
    """结构化验证错误项"""

    __slots__ = ("code", "message", "task_id")

    def __init__(self, code: str, message: str, task_id: Optional[str] = None):
        self.code = code
        self.message = message
        self.task_id = task_id

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {"code": self.code, "message": self.message, "task_id": self.task_id}

    def __repr__(self) -> str:
        return f"PlanValidationIssue({self.code}: {self.message})"


def validate_plan(plan: Plan, tool_registry=None, llm=None) -> List[PlanValidationIssue]:
    """校验计划结构，返回结构化错误列表（空列表 = 通过）。

    检查项：
      ① 依赖完整性：任务引用的依赖 ID 必须存在于计划内（防悬空依赖卡死）
      ② 循环依赖：DFS 检测（防环导致永不可执行）
      ③ 工具可用性：无 LLM 纯工具路径下，每任务 find_tool 必须可解析；
         有 LLM 时跳过（推理可灵活完成，防误拦截）
      ④ 任务描述非空（防无意义空任务）

    Args:
        plan: 待校验计划
        tool_registry: 工具注册表（可选，无 LLM 时用于工具可用性预检）
        llm: LLM 服务（可选，存在时跳过工具可用性预检）

    Returns:
        结构化错误列表；空列表表示通过。
    """
    issues: List[PlanValidationIssue] = []
    task_ids = {t.id for t in plan.tasks}
    task_map = {t.id: t for t in plan.tasks}

    # ④ 任务描述非空 + ① 依赖完整性
    for task in plan.tasks:
        if not task.description or not str(task.description).strip():
            issues.append(PlanValidationIssue(
                code="empty_description",
                message=f"任务 '{task.id}' 描述为空",
                task_id=task.id,
            ))
        for dep in task.dependencies:
            if dep not in task_ids:
                issues.append(PlanValidationIssue(
                    code="dangling_dependency",
                    message=f"任务 '{task.id}' 依赖不存在的任务 '{dep}'（依赖不存在）",
                    task_id=task.id,
                ))

    # ② 循环依赖（DFS 三色标记）
    visiting, visited = set(), set()

    def _dfs(tid: str) -> None:
        if tid in visiting:
            issues.append(PlanValidationIssue(
                code="circular_dependency",
                message=f"检测到循环依赖（涉及任务 '{tid}'）",
                task_id=tid,
            ))
            return
        if tid in visited or tid not in task_map:
            return
        visiting.add(tid)
        for dep in task_map[tid].dependencies:
            _dfs(dep)
        visiting.discard(tid)
        visited.add(tid)

    for t in plan.tasks:
        _dfs(t.id)

    # ③ 工具可用性预检（仅无 LLM 纯工具路径）
    if llm is None and tool_registry is not None:
        for task in plan.tasks:
            if tool_registry.find_tool(task.description) is None:
                issues.append(PlanValidationIssue(
                    code="tool_unavailable",
                    message=f"任务 '{task.id}' 引用的工具不可用"
                            f"（描述 '{task.description}' 无法解析到已注册工具）",
                    task_id=task.id,
                ))

    return issues


def validate_plan_or_raise(plan: Plan, tool_registry=None, llm=None) -> None:
    """校验计划；失败抛 PlanValidationError（消息拼接全部错误，便于定位）。

    Raises:
        PlanValidationError: 计划结构非法（消息含全部错误项）
    """
    issues = validate_plan(plan, tool_registry, llm)
    if issues:
        raise PlanValidationError("；".join(i.message for i in issues))
