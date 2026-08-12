"""计划数据模型

定义Plan、PlanState等核心数据结构
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid

from .task import Task, TaskStatus


class PlanState(Enum):
    """计划状态"""
    INIT = "init"
    DECOMPOSING = "decomposing"
    READY = "ready"
    EXECUTING = "executing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Plan:
    """执行计划

    包含完整任务执行所需的所有信息
    """
    id: str = field(default_factory=lambda: f"plan_{uuid.uuid4().hex[:8]}")
    original_task: str = ""
    tasks: List[Task] = field(default_factory=list)
    execution_graph: Dict[str, List[str]] = field(default_factory=dict)
    current_step: int = 0
    max_steps: int = 50
    state: PlanState = PlanState.INIT
    context: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def get_task(self, task_id: str) -> Optional[Task]:
        """根据ID获取任务"""
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def get_next_executable_tasks(self) -> List[Task]:
        """获取所有可执行的任务"""
        completed = {t.id for t in self.tasks if t.status == TaskStatus.COMPLETED}
        executable = []

        for task in self.tasks:
            if task.can_execute(completed):
                executable.append(task)

        executable.sort(key=lambda t: t.priority, reverse=True)
        return executable

    def is_complete(self) -> bool:
        """检查计划是否完成"""
        if self.state not in (PlanState.EXECUTING, PlanState.READY):
            return False

        terminal_statuses = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED}
        return all(t.status in terminal_statuses for t in self.tasks)

    def is_success(self, consider_state: bool = True) -> bool:
        """检查计划是否成功

        Args:
            consider_state: 是否要求计划状态为 COMPLETED。默认 True（对外语义不变）；
                执行收尾判定时传 False，仅依据任务状态判定（D1 修复：避免计划仍处于
                EXECUTING 时被 state == COMPLETED 短路，导致"全部成功"分支永不触发）。

        Returns:
            计划是否成功（需至少存在一个任务且全部 COMPLETED）。
        """
        if consider_state and self.state != PlanState.COMPLETED:
            return False
        return len(self.tasks) > 0 and all(t.status == TaskStatus.COMPLETED for t in self.tasks)

    def progress(self) -> float:
        """计算完成进度"""
        if not self.tasks:
            return 0.0
        completed = sum(1 for t in self.tasks if t.status == TaskStatus.COMPLETED)
        return completed / len(self.tasks)

    def add_task(self, task: Task):
        """添加任务"""
        self.tasks.append(task)
        self._update_graph()

    def _update_graph(self):
        """更新依赖图"""
        self.execution_graph = {}
        for task in self.tasks:
            self.execution_graph[task.id] = task.dependencies.copy()

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "original_task": self.original_task,
            "state": self.state.value,
            "progress": f"{self.progress():.1%}",
            "current_step": self.current_step,
            "max_steps": self.max_steps,
            "result": str(self.result) if self.result else None,
            "error": self.error,
            "context": dict(self.context),
            "tasks": [t.to_dict() for t in self.tasks],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def summarize(self) -> str:
        """生成用户可读的结构化计划摘要（D15 修复）

        与 to_dict()（开发向字典）互补：供 UI/日志面向用户展示
        目标、任务清单与各自状态。
        """
        lines = [
            f"计划目标: {self.original_task}",
            f"状态: {self.state.value} | 进度: {self.progress():.1%}",
            "任务清单:",
        ]
        for t in self.tasks:
            lines.append(f"  - [{t.status.value}] {t.description}")
        return "\n".join(lines)

    @classmethod
    def from_dict(cls, data: dict) -> "Plan":
        """从字典还原计划（与 to_dict 对称，D9 修复：持久化恢复）"""
        plan = cls(
            id=data["id"],
            original_task=data.get("original_task", ""),
            state=PlanState(data.get("state", "init")),
            current_step=data.get("current_step", 0),
            max_steps=data.get("max_steps", 50),
            context=data.get("context", {}),
            result=data.get("result"),
            error=data.get("error"),
        )
        for task_data in data.get("tasks", []):
            plan.add_task(Task.from_dict(task_data))
        return plan
