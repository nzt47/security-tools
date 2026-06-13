"""任务数据模型

定义Task、TaskType、TaskStatus等核心数据结构
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid


class TaskType(Enum):
    """任务类型枚举"""
    ATOMIC = "atomic"
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"
    LOOP = "loop"


class TaskStatus(Enum):
    """任务状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


@dataclass
class Task:
    """任务单元

    代表一个可执行的最小工作单元
    """
    id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    description: str = ""
    task_type: TaskType = TaskType.ATOMIC
    priority: int = 3
    dependencies: List[str] = field(default_factory=list)
    estimated_steps: int = 1
    constraints: List[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    retry_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def mark_running(self):
        """标记为运行中"""
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.now()

    def mark_completed(self, result: Any = None):
        """标记为完成"""
        self.status = TaskStatus.COMPLETED
        if result is not None:
            self.result = result
        self.completed_at = datetime.now()

    def mark_failed(self, error: str):
        """标记为失败"""
        self.status = TaskStatus.FAILED
        self.error = error
        self.completed_at = datetime.now()

    def mark_skipped(self):
        """标记为跳过"""
        self.status = TaskStatus.SKIPPED
        self.completed_at = datetime.now()

    def can_execute(self, completed_tasks: set) -> bool:
        """检查是否可以执行(依赖是否满足)"""
        if self.status != TaskStatus.PENDING:
            return False
        return all(dep_id in completed_tasks for dep_id in self.dependencies)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "description": self.description,
            "task_type": self.task_type.value,
            "priority": self.priority,
            "status": self.status.value,
            "result": str(self.result) if self.result else None,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
