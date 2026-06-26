"""DAG 引擎（增强版）— 管理任务依赖和执行顺序

增强功能：
- 任务节点状态机（pending -> planning -> confirmed -> running -> done/failed）
- 执行计划预览
- 依赖冲突检测
- 回退机制支持
"""

from dataclasses import dataclass, field
from typing import Optional, Callable, Any
from enum import Enum
import time

from agent.task_planner.dag import DAG, TaskNode


class PlanStatus(Enum):
    """计划状态"""
    DRAFT = "draft"              # 草稿（待确认）
    CONFIRMED = "confirmed"      # 已确认（可执行）
    RUNNING = "running"          # 执行中
    COMPLETED = "completed"      # 已完成
    FAILED = "failed"            # 失败
    ROLLED_BACK = "rolled_back"     # 已回退
    CANCELLED = "cancelled"      # 已取消


@dataclass
class EnhancedTaskNode:
    """增强任务节点

    新增属性：
    - status: 任务状态
    - estimated_duration: 预估执行时间（秒）
    - actual_duration: 实际执行时间（秒）
    - error_message: 错误信息（如有）
    - rollback_action: 回滚动作
    - requires_confirmation: 是否需要人工确认
    - confirmed_by: 确认人
    - confirmed_at: 确认时间
    """
    id: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    result: Optional[str] = None
    # 增强属性
    estimated_duration: float = 0.0
    actual_duration: float = 0.0
    error_message: Optional[str] = None
    rollback_action: Optional[str] = None
    requires_confirmation: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[float] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "description": self.description,
            "depends_on": self.depends_on,
            "status": self.status,
            "result": self.result,
            "estimated_duration": self.estimated_duration,
            "actual_duration": self.actual_duration,
            "error_message": self.error_message,
            "requires_confirmation": self.requires_confirmation,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class EnhancedDAG:
    """增强 DAG 引擎

    增强功能：
    - 任务状态机管理
    - 计划确认流程
    - 循环依赖检测
    - 执行路径预览
    - 回退计划生成
    """

    def __init__(self):
        self._nodes: dict[str, EnhancedTaskNode] = {}
        self._plan_id: str = ""
        self._created_at: float = time.time()
        self._status: PlanStatus = PlanStatus.DRAFT

    @property
    def plan_id(self) -> str:
        return self._plan_id

    @plan_id.setter
    def plan_id(self, value: str):
        self._plan_id = value

    @property
    def status(self) -> PlanStatus:
        return self._status

    @status.setter
    def status(self, value: PlanStatus):
        self._status = value

    def add_task(
        self,
        node: EnhancedTaskNode,
        estimated_duration: float = 0.0,
        requires_confirmation: bool = False,
        rollback_action: Optional[str] = None,
    ):
        """添加任务节点

        Args:
            node: 任务节点
            estimated_duration: 预估执行时间
            requires_confirmation: 是否需要人工确认
            rollback_action: 回滚动作描述
        """
        node.estimated_duration = estimated_duration
        node.requires_confirmation = requires_confirmation
        node.rollback_action = rollback_action
        self._nodes[node.id] = node

    def get_task(self, task_id: str) -> Optional[EnhancedTaskNode]:
        """获取任务节点"""
        return self._nodes.get(task_id)

    def get_ready_tasks(self, include_unconfirmed: bool = False) -> list[EnhancedTaskNode]:
        """获取就绪任务

        Args:
            include_unconfirmed: 是否包含未确认的任务
        """
        ready = []
        for node in self._nodes.values():
            if node.status not in ("pending", "planning"):
                continue

            # 检查依赖是否完成
            deps_done = all(
                self._nodes[d].status == "done"
                for d in node.depends_on
                if d in self._nodes
            )

            if not deps_done:
                continue

            # 检查是否需要确认
            if node.requires_confirmation and not include_unconfirmed:
                if node.status != "confirmed":
                    continue

            ready.append(node)

        return ready

    def is_complete(self) -> bool:
        return all(n.status == "done" for n in self._nodes.values())

    def has_failed(self) -> bool:
        return any(n.status == "failed" for n in self._nodes.values())

    def has_unconfirmed(self) -> bool:
        """检查是否有未确认的任务"""
        return any(
            n.requires_confirmation and n.status != "confirmed"
            for n in self._nodes.values()
        )

    def get_execution_path(self) -> list[str]:
        """获取执行路径（拓扑排序）"""
        visited, result = set(), []

        def dfs(nid):
            if nid in visited:
                return
            visited.add(nid)
            node = self._nodes[nid]
            for dep in node.depends_on:
                if dep in self._nodes:
                    dfs(dep)
            result.append(nid)

        for nid in self._nodes:
            dfs(nid)

        return result

    def get_unconfirmed_tasks(self) -> list[EnhancedTaskNode]:
        """获取需要确认的任务"""
        return [
            n for n in self._nodes.values()
            if n.requires_confirmation and n.status != "confirmed"
        ]

    def confirm_task(self, task_id: str, confirmed_by: str = "system") -> bool:
        """确认任务

        Args:
            task_id: 任务 ID
            confirmed_by: 确认人

        Returns:
            True 表示确认成功
        """
        node = self._nodes.get(task_id)
        if not node:
            return False

        # 无论是否需要确认，都记录确认人
        node.status = "confirmed"
        node.confirmed_by = confirmed_by
        node.confirmed_at = time.time()
        return True

    def confirm_all(self, confirmed_by: str = "system") -> int:
        """确认所有任务

        Returns:
            确认的任务数量
        """
        count = 0
        for node in self._nodes.values():
            if node.requires_confirmation and node.status != "confirmed":
                node.status = "confirmed"
                node.confirmed_by = confirmed_by
                node.confirmed_at = time.time()
                count += 1
            elif not node.requires_confirmation:
                node.status = "confirmed"
                node.confirmed_by = "system"
                node.confirmed_at = time.time()
                count += 1

        if count > 0:
            self._status = PlanStatus.CONFIRMED

        return count

    def mark_running(self, task_id: str):
        """标记任务开始执行"""
        node = self._nodes.get(task_id)
        if node:
            node.status = "running"
            node.started_at = time.time()
            self._status = PlanStatus.RUNNING

    def mark_done(self, task_id: str, result: str = ""):
        """标记任务完成"""
        node = self._nodes.get(task_id)
        if node:
            node.status = "done"
            node.result = result
            node.completed_at = time.time()
            if node.started_at:
                node.actual_duration = node.completed_at - node.started_at

    def mark_failed(self, task_id: str, error: str):
        """标记任务失败"""
        node = self._nodes.get(task_id)
        if node:
            node.status = "failed"
            node.error_message = error
            node.completed_at = time.time()
            if node.started_at:
                node.actual_duration = node.completed_at - node.started_at

    def rollback_task(self, task_id: str) -> Optional[str]:
        """回滚单个任务

        Returns:
            回滚动作描述
        """
        node = self._nodes.get(task_id)
        if not node:
            return None

        node.status = "rolled_back"
        node.completed_at = time.time()
        return node.rollback_action

    def get_rollback_path(self, failed_task_id: str) -> list[str]:
        """获取回滚路径

        从失败任务开始，回溯所有它依赖的已完成任务。
        这些前序任务的输出可能是导致失败的原因，需要回退。

        返回顺序：从失败任务的直接前置任务开始，向前回溯。
        """
        rollback_path = []
        visited = set()

        def dfs(task_id):
            if task_id in visited:
                return
            visited.add(task_id)

            node = self._nodes.get(task_id)
            if not node:
                return

            # 回溯所有依赖的任务
            for dep_id in node.depends_on:
                dep_node = self._nodes.get(dep_id)
                if dep_node and dep_node.status == "done":
                    rollback_path.append(dep_id)
                    dfs(dep_id)  # 继续回溯

        dfs(failed_task_id)

        return rollback_path

    def detect_cycles(self) -> list[list[str]]:
        """检测循环依赖

        Returns:
            循环路径列表
        """
        cycles = []
        visited = set()
        rec_stack = set()

        def dfs(nid, path):
            visited.add(nid)
            rec_stack.add(nid)
            path.append(nid)

            for dep in self._nodes[nid].depends_on:
                if dep not in self._nodes:
                    continue
                if dep not in visited:
                    if dfs(dep, path):
                        return True
                elif dep in rec_stack:
                    # 发现循环
                    cycle_start = path.index(dep)
                    cycles.append(path[cycle_start:] + [dep])

            path.pop()
            rec_stack.remove(nid)
            return False

        for nid in self._nodes:
            if nid not in visited:
                dfs(nid, [])

        return cycles

    def get_plan_summary(self) -> dict:
        """获取计划摘要"""
        status_counts = {}
        for node in self._nodes.values():
            status_counts[node.status] = status_counts.get(node.status, 0) + 1

        total_estimated = sum(n.estimated_duration for n in self._nodes.values())
        total_actual = sum(n.actual_duration for n in self._nodes.values())

        return {
            "plan_id": self._plan_id,
            "status": self._status.value,
            "total_tasks": len(self._nodes),
            "status_counts": status_counts,
            "total_estimated_duration": total_estimated,
            "total_actual_duration": total_actual,
            "has_unconfirmed": self.has_unconfirmed(),
            "unconfirmed_count": len(self.get_unconfirmed_tasks()),
            "has_cycles": len(self.detect_cycles()) > 0,
        }

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "plan_id": self._plan_id,
            "status": self._status.value,
            "created_at": self._created_at,
            "nodes": {
                nid: node.to_dict()
                for nid, node in self._nodes.items()
            },
            "execution_path": self.get_execution_path(),
            "summary": self.get_plan_summary(),
        }
