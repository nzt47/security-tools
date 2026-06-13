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

    def is_success(self) -> bool:
        """检查计划是否成功"""
        if self.state != PlanState.COMPLETED:
            return False
        return all(t.status == TaskStatus.COMPLETED for t in self.tasks)

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
            "tasks": [t.to_dict() for t in self.tasks],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
