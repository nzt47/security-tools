"""结构化计划摘要（阶段 4 / D15）

【不易】build_plan_summary 输出键结构稳定（goal/strategy/tasks/budget/
  failure_reasons/reflection），供 UI/日志/追踪消费；Plan 既有字段只读不改。
【变易】reflector 可选注入：未注入时反思结论取空（不影响主流程）。
【简易】纯函数薄模块：读 Plan/Task 既有字段聚合输出，不持有状态。
"""

import logging
from typing import Any, Dict, List, Optional

from .models import Plan, Task, TaskStatus, PlanState

logger = logging.getLogger(__name__)


def _task_duration_ms(task: Task) -> Optional[int]:
    """任务耗时（毫秒）：completed_at - started_at，缺时间戳返回 None"""
    if not task.started_at or not task.completed_at:
        return None
    return int((task.completed_at - task.started_at).total_seconds() * 1000)


def _task_entries(plan: Plan) -> List[Dict]:
    """任务列表（含依赖/状态/耗时/失败原因）"""
    entries = []
    for task in plan.tasks:
        entry = {
            "id": task.id,
            "description": task.description,
            "status": task.status.value,
            "dependencies": list(task.dependencies),
            "duration_ms": _task_duration_ms(task),
            "error": task.error,
        }
        failure_reason = (task.metadata or {}).get("failure_reason")
        if failure_reason:
            entry["failure_reason"] = failure_reason
        entries.append(entry)
    return entries


def _failure_reasons(plan: Plan) -> List[Dict]:
    """失败原因汇总（阶段 3 失败归因，task.metadata.failure_reason）"""
    reasons = []
    for task in plan.tasks:
        reason = (task.metadata or {}).get("failure_reason")
        if reason:
            reasons.append({"task_id": task.id, "reason": reason})
        elif task.status in (TaskStatus.FAILED, TaskStatus.SKIPPED):
            reasons.append({"task_id": task.id, "reason": "未分类"})
    return reasons


def _strategy(plan: Plan) -> str:
    """策略描述：并行组声明优先，否则按执行拓扑推断"""
    parallel_groups = (plan.metadata or {}).get("parallel_groups") or []
    if parallel_groups:
        return f"并行执行（{len(parallel_groups)} 组）"
    return "串行执行"


def build_plan_summary(plan: Plan, reflector=None) -> Dict[str, Any]:
    """构建结构化计划摘要

    Args:
        plan: 计划对象
        reflector: 可选 Reflector（取最近一条计划级反思结论；未注入/无记录时为空）

    Returns:
        dict: goal/strategy/tasks/budget/failure_reasons/reflection
    """
    budget = (plan.metadata or {}).get("budget") or {}
    reflection = None
    if reflector is not None:
        try:
            for entry in reversed(getattr(reflector, "reflection_history", []) or []):
                if entry.get("type") == "plan":
                    reflection = entry.get("reflection")
                    break
        except Exception as e:
            logger.warning(f"[D15] 读取反思结论失败: {e}")

    return {
        "goal": plan.original_task,
        "state": plan.state.value,
        "strategy": _strategy(plan),
        "progress": f"{plan.progress():.1%}",
        "tasks": _task_entries(plan),
        "budget": {
            "steps": budget.get("steps"),
            "iterations": budget.get("iterations"),
            "elapsed_seconds": budget.get("elapsed_seconds"),
            "tokens": budget.get("tokens"),
            "cost": budget.get("cost"),
            "status": budget.get("status"),
        },
        "failure_reasons": _failure_reasons(plan),
        "reflection": reflection,
    }


def build_plan_summary_markdown(summary: Dict[str, Any]) -> str:
    """将结构化计划摘要转为 markdown 文本版（供响应追加）"""
    lines = [
        f"**计划目标**: {summary.get('goal') or '无'}",
        f"**状态**: {summary.get('state')} | 进度 {summary.get('progress')} | 策略: {summary.get('strategy')}",
        "",
        "**任务清单**:",
    ]
    for task in summary.get("tasks", []):
        duration = f" | 耗时 {task['duration_ms']}ms" if task.get("duration_ms") is not None else ""
        error = f" | 失败: {task['error']}" if task.get("error") else ""
        lines.append(f"- [{task['status']}] {task['description']}{duration}{error}")

    budget = summary.get("budget") or {}
    budget_parts = []
    for key, label in (("steps", "步数"), ("iterations", "迭代"), ("elapsed_seconds", "耗时(s)"),
                       ("tokens", "tokens"), ("cost", "成本$")):
        if budget.get(key) is not None:
            budget_parts.append(f"{label}={budget[key]}")
    if budget_parts:
        lines.append("")
        lines.append(f"**预算消耗**: {' | '.join(budget_parts)}")

    failures = summary.get("failure_reasons") or []
    if failures:
        lines.append("")
        lines.append("> **[失败原因]** " + "; ".join(
            f"{f['task_id']}({f['reason']})" for f in failures
        ))

    reflection = summary.get("reflection")
    if reflection:
        lessons = reflection.get("lessons") if isinstance(reflection, dict) else None
        if lessons:
            lines.append("")
            lines.append("> **[反思结论]** " + "; ".join(str(l) for l in lessons))

    return "\n".join(lines)


def build_react_summary(message: str, react_result) -> Dict[str, Any]:
    """ReAct 聊天路径的结构化执行摘要（无 Plan 对象时的等价物）

    Args:
        message: 用户任务描述（作为 goal）
        react_result: ReActResult

    Returns:
        dict: goal/state/tasks(简化)/budget(react 侧预算快照)/失败原因/反思
    """
    final_state = getattr(react_result, "final_state", None) or {}
    budget = final_state.get("budget") or {}
    error = getattr(react_result, "error", None)
    return {
        "goal": message,
        "state": "completed" if react_result.success else "failed",
        "strategy": "ReAct 自由循环",
        "progress": "100%" if react_result.success else "部分完成",
        "tasks": [
            {
                "id": f"react_{step.iteration}",
                "description": step.action or step.thought,
                "status": "completed" if step.success else "failed",
                "dependencies": [],
                "duration_ms": getattr(step, "duration_ms", None),
                "error": None if step.success else step.observation,
            }
            for step in getattr(react_result, "steps", []) or []
        ],
        "budget": {
            "steps": budget.get("steps"),
            "iterations": budget.get("iterations"),
            "elapsed_seconds": budget.get("elapsed_seconds"),
            "tokens": budget.get("tokens"),
            "cost": budget.get("cost"),
            "status": budget.get("status"),
        },
        "failure_reasons": (
            [{"task_id": "react_loop", "reason": "执行失败"}] if not react_result.success else []
        ),
        "reflection": None,
    }


def build_react_summary_markdown(summary: Dict[str, Any]) -> str:
    """ReAct 摘要 markdown 版（短版：目标/结果/步数/耗时/失败原因/反思结论）"""
    lines = [
        f"**计划目标**: {summary.get('goal') or '无'}",
        f"**状态**: {'成功' if summary.get('state') == 'completed' else '未完成'}",
    ]
    tasks = summary.get("tasks") or []
    if tasks:
        lines.append(f"**执行步骤**: {len(tasks)} 步")
        failed = [t for t in tasks if t.get("status") == "failed"]
        if failed:
            lines.append(f"**失败步骤**: {len(failed)} 步（{failed[-1].get('description', '')[:60]}）")
    budget = summary.get("budget") or {}
    if budget.get("cost") is not None or budget.get("tokens") is not None:
        lines.append(
            f"**预算**: tokens={budget.get('tokens')} cost=${budget.get('cost')}"
        )
    failures = summary.get("failure_reasons") or []
    if failures:
        lines.append("")
        lines.append("> **[失败原因]** " + "; ".join(
            f"{f['task_id']}({f['reason']})" for f in failures
        ))
    reflection = summary.get("reflection")
    if reflection:
        lessons = reflection.get("lessons") if isinstance(reflection, dict) else None
        if lessons:
            lines.append("")
            lines.append("> **[反思结论]** " + "; ".join(str(l) for l in lessons))
    return "\n".join(lines)
