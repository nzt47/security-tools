"""计划状态机

管理计划的生命周期状态转换
"""

import logging
from enum import Enum
from typing import Callable, Dict, List, Optional
from datetime import datetime

from .models import Plan, PlanState

logger = logging.getLogger(__name__)


class InvalidStateTransitionError(Exception):
    """无效状态转换异常"""
    pass


class PlanStateMachine:
    """计划状态机

    管理计划的生命周期状态转换
    """

    VALID_TRANSITIONS = {
        PlanState.INIT: {PlanState.DECOMPOSING, PlanState.CANCELLED},
        PlanState.DECOMPOSING: {PlanState.READY, PlanState.FAILED, PlanState.CANCELLED},
        PlanState.READY: {PlanState.EXECUTING, PlanState.CANCELLED},
        PlanState.EXECUTING: {
            PlanState.PAUSED,
            PlanState.COMPLETED,
            PlanState.FAILED,
            PlanState.CANCELLED
        },
        PlanState.PAUSED: {PlanState.EXECUTING, PlanState.CANCELLED},
        PlanState.COMPLETED: set(),
        PlanState.FAILED: set(),
        PlanState.CANCELLED: set(),
    }

    STATE_DESCRIPTIONS = {
        PlanState.INIT: "初始化",
        PlanState.DECOMPOSING: "正在分解任务",
        PlanState.READY: "计划就绪",
        PlanState.EXECUTING: "执行中",
        PlanState.PAUSED: "已暂停",
        PlanState.COMPLETED: "已完成",
        PlanState.FAILED: "执行失败",
        PlanState.CANCELLED: "已取消",
    }

    def __init__(self):
        self._transition_history: List[Dict] = []
        self._hooks: Dict[tuple, List[Callable]] = {
            (PlanState.INIT, PlanState.DECOMPOSING): [],
            (PlanState.DECOMPOSING, PlanState.READY): [],
            (PlanState.DECOMPOSING, PlanState.FAILED): [],
            (PlanState.READY, PlanState.EXECUTING): [],
            (PlanState.EXECUTING, PlanState.COMPLETED): [],
            (PlanState.EXECUTING, PlanState.FAILED): [],
            (PlanState.EXECUTING, PlanState.PAUSED): [],
            (PlanState.EXECUTING, PlanState.CANCELLED): [],
            (PlanState.PAUSED, PlanState.EXECUTING): [],
            (PlanState.PAUSED, PlanState.CANCELLED): [],
        }

    def can_transition(self, current_state: PlanState, target_state: PlanState) -> bool:
        """检查是否可以转换"""
        return target_state in self.VALID_TRANSITIONS.get(current_state, set())

    def transition(self, plan: Plan, new_state: PlanState, reason: str = None) -> bool:
        """
        执行状态转换

        Args:
            plan: 计划对象
            new_state: 目标状态
            reason: 转换原因

        Returns:
            是否转换成功

        Raises:
            InvalidStateTransitionError: 如果转换无效
        """
        if not self.can_transition(plan.state, new_state):
            raise InvalidStateTransitionError(
                f"无法从 {plan.state.value} 转换到 {new_state.value}"
            )

        old_state = plan.state
        plan.state = new_state
        plan.updated_at = datetime.now()

        self._record_transition(plan, old_state, new_state, reason)
        self._trigger_hooks(plan, old_state, new_state)

        logger.info(f"计划状态转换: {old_state.value} → {new_state.value}")
        return True

    def register_hook(self, from_state: PlanState, to_state: PlanState, callback: Callable):
        """注册状态转换钩子"""
        key = (from_state, to_state)
        if key in self._hooks:
            self._hooks[key].append(callback)

    def _record_transition(self, plan: Plan, old_state: PlanState, new_state: PlanState, reason: str = None):
        """记录状态转换"""
        entry = {
            "plan_id": plan.id,
            "from_state": old_state.value,
            "to_state": new_state.value,
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        }
        self._transition_history.append(entry)

    def _trigger_hooks(self, plan: Plan, old_state: PlanState, new_state: PlanState):
        """触发状态转换钩子"""
        key = (old_state, new_state)
        callbacks = self._hooks.get(key, [])

        for callback in callbacks:
            try:
                if hasattr(callback, "__await__"):
                    import asyncio
                    asyncio.create_task(callback(plan))
                else:
                    callback(plan)
            except Exception as e:
                logger.error(f"状态钩子执行失败: {e}")

    def get_transition_history(self, plan_id: str = None, limit: int = 50) -> List[Dict]:
        """获取状态转换历史"""
        history = self._transition_history

        if plan_id:
            history = [h for h in history if h["plan_id"] == plan_id]

        return history[-limit:]

    def get_state_description(self, state: PlanState) -> str:
        """获取状态描述"""
        return self.STATE_DESCRIPTIONS.get(state, "未知状态")
