"""计划状态机单元测试"""

import pytest
from planning.state_machine import PlanStateMachine, InvalidStateTransitionError
from planning.models import Plan, PlanState


class TestPlanStateMachine:
    """计划状态机单元测试"""

    def test_state_machine_initialization(self):
        """测试状态机初始化"""
        sm = PlanStateMachine()
        assert sm is not None
        assert len(sm._transition_history) == 0

    def test_valid_transition_init_to_decomposing(self):
        """测试从INIT到DECOMPOSING的有效转换"""
        sm = PlanStateMachine()
        plan = Plan(state=PlanState.INIT)
        
        result = sm.transition(plan, PlanState.DECOMPOSING, "开始分解")
        assert result is True
        assert plan.state == PlanState.DECOMPOSING

    def test_valid_transition_decomposing_to_ready(self):
        """测试从DECOMPOSING到READY的有效转换"""
        sm = PlanStateMachine()
        plan = Plan(state=PlanState.DECOMPOSING)
        
        result = sm.transition(plan, PlanState.READY, "分解完成")
        assert result is True
        assert plan.state == PlanState.READY

    def test_valid_transition_ready_to_executing(self):
        """测试从READY到EXECUTING的有效转换"""
        sm = PlanStateMachine()
        plan = Plan(state=PlanState.READY)
        
        result = sm.transition(plan, PlanState.EXECUTING, "开始执行")
        assert result is True
        assert plan.state == PlanState.EXECUTING

    def test_valid_transition_executing_to_completed(self):
        """测试从EXECUTING到COMPLETED的有效转换"""
        sm = PlanStateMachine()
        plan = Plan(state=PlanState.EXECUTING)
        
        result = sm.transition(plan, PlanState.COMPLETED, "执行成功")
        assert result is True
        assert plan.state == PlanState.COMPLETED

    def test_valid_transition_executing_to_failed(self):
        """测试从EXECUTING到FAILED的有效转换"""
        sm = PlanStateMachine()
        plan = Plan(state=PlanState.EXECUTING)
        
        result = sm.transition(plan, PlanState.FAILED, "执行失败")
        assert result is True
        assert plan.state == PlanState.FAILED

    def test_valid_transition_executing_to_paused(self):
        """测试从EXECUTING到PAUSED的有效转换"""
        sm = PlanStateMachine()
        plan = Plan(state=PlanState.EXECUTING)
        
        result = sm.transition(plan, PlanState.PAUSED, "暂停执行")
        assert result is True
        assert plan.state == PlanState.PAUSED

    def test_valid_transition_paused_to_executing(self):
        """测试从PAUSED到EXECUTING的有效转换"""
        sm = PlanStateMachine()
        plan = Plan(state=PlanState.PAUSED)
        
        result = sm.transition(plan, PlanState.EXECUTING, "继续执行")
        assert result is True
        assert plan.state == PlanState.EXECUTING

    def test_invalid_transition_init_to_executing(self):
        """测试从INIT到EXECUTING的无效转换"""
        sm = PlanStateMachine()
        plan = Plan(state=PlanState.INIT)
        
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(plan, PlanState.EXECUTING)

    def test_invalid_transition_completed_to_executing(self):
        """测试从COMPLETED到EXECUTING的无效转换"""
        sm = PlanStateMachine()
        plan = Plan(state=PlanState.COMPLETED)
        
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(plan, PlanState.EXECUTING)

    def test_invalid_transition_failed_to_ready(self):
        """测试从FAILED到READY的无效转换"""
        sm = PlanStateMachine()
        plan = Plan(state=PlanState.FAILED)
        
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(plan, PlanState.READY)

    def test_can_transition_valid(self):
        """测试can_transition方法对有效转换的判断"""
        sm = PlanStateMachine()
        
        assert sm.can_transition(PlanState.INIT, PlanState.DECOMPOSING) is True
        assert sm.can_transition(PlanState.DECOMPOSING, PlanState.READY) is True
        assert sm.can_transition(PlanState.READY, PlanState.EXECUTING) is True
        assert sm.can_transition(PlanState.EXECUTING, PlanState.COMPLETED) is True

    def test_can_transition_invalid(self):
        """测试can_transition方法对无效转换的判断"""
        sm = PlanStateMachine()
        
        assert sm.can_transition(PlanState.INIT, PlanState.EXECUTING) is False
        assert sm.can_transition(PlanState.COMPLETED, PlanState.EXECUTING) is False
        assert sm.can_transition(PlanState.FAILED, PlanState.READY) is False

    def test_transition_history(self):
        """测试状态转换历史记录"""
        sm = PlanStateMachine()
        plan = Plan(id="test_plan", state=PlanState.INIT)
        
        sm.transition(plan, PlanState.DECOMPOSING, "开始分解")
        sm.transition(plan, PlanState.READY, "分解完成")
        sm.transition(plan, PlanState.EXECUTING, "开始执行")
        
        history = sm.get_transition_history("test_plan")
        assert len(history) == 3
        assert history[0]["from_state"] == "init"
        assert history[0]["to_state"] == "decomposing"
        assert history[1]["from_state"] == "decomposing"
        assert history[1]["to_state"] == "ready"
        assert history[2]["from_state"] == "ready"
        assert history[2]["to_state"] == "executing"

    def test_get_state_description(self):
        """测试获取状态描述"""
        sm = PlanStateMachine()
        
        assert sm.get_state_description(PlanState.INIT) == "初始化"
        assert sm.get_state_description(PlanState.DECOMPOSING) == "正在分解任务"
        assert sm.get_state_description(PlanState.READY) == "计划就绪"
        assert sm.get_state_description(PlanState.EXECUTING) == "执行中"
        assert sm.get_state_description(PlanState.COMPLETED) == "已完成"
        assert sm.get_state_description(PlanState.FAILED) == "执行失败"

    def test_register_and_trigger_hook(self):
        """测试注册和触发状态转换钩子"""
        sm = PlanStateMachine()
        hook_called = []
        
        def callback(plan):
            hook_called.append(plan.id)
        
        plan = Plan(id="hook_test", state=PlanState.INIT)
        sm.register_hook(PlanState.INIT, PlanState.DECOMPOSING, callback)
        
        sm.transition(plan, PlanState.DECOMPOSING)
        
        assert len(hook_called) == 1
        assert hook_called[0] == "hook_test"

    def test_cancel_from_various_states(self):
        """测试从不同状态取消计划"""
        sm = PlanStateMachine()
        
        plan1 = Plan(state=PlanState.INIT)
        sm.transition(plan1, PlanState.CANCELLED, "用户取消")
        assert plan1.state == PlanState.CANCELLED
        
        plan2 = Plan(state=PlanState.DECOMPOSING)
        sm.transition(plan2, PlanState.CANCELLED, "用户取消")
        assert plan2.state == PlanState.CANCELLED
        
        plan3 = Plan(state=PlanState.READY)
        sm.transition(plan3, PlanState.CANCELLED, "用户取消")
        assert plan3.state == PlanState.CANCELLED
        
        plan4 = Plan(state=PlanState.EXECUTING)
        sm.transition(plan4, PlanState.CANCELLED, "用户取消")
        assert plan4.state == PlanState.CANCELLED
        
        plan5 = Plan(state=PlanState.PAUSED)
        sm.transition(plan5, PlanState.CANCELLED, "用户取消")
        assert plan5.state == PlanState.CANCELLED

    def test_cannot_cancel_terminal_states(self):
        """测试不能从终态继续转换"""
        sm = PlanStateMachine()
        
        plan1 = Plan(state=PlanState.COMPLETED)
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(plan1, PlanState.CANCELLED)
        
        plan2 = Plan(state=PlanState.FAILED)
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(plan2, PlanState.CANCELLED)
        
        plan3 = Plan(state=PlanState.CANCELLED)
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(plan3, PlanState.EXECUTING)
