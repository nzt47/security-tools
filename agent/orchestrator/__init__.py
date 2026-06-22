"""
orchestrator — 云枢主编排层

职责划分:
- Orchestrator: 消息路由、工具调用协调、结果聚合
- LifecycleManager: 系统初始化、组件组装、生命周期管理
- TaskDispatcher: 任务调度与超时控制

设计原则: 主 Agent 轻量化，专注理解用户意图、任务拆解和结果整合。
"""

from .lifecycle_manager import LifecycleManager
from .task_dispatcher import TaskDispatcher
from .orchestrator import Orchestrator

__all__ = [
    "LifecycleManager",
    "TaskDispatcher",
    "Orchestrator",
]
