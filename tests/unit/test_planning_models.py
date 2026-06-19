"""规划模块数据模型单元测试"""

import pytest
from datetime import datetime
from planning.models import (
    Task, TaskType, TaskStatus,
    Plan, PlanState,
    Action, ActionType, ActionResult,
    ExecutionRecord,
    ReActStep, ReActResult, ThoughtResult
)


class TestTask:
    """任务模型单元测试"""

    def test_task_creation(self):
        task = Task(
            id="test_task",
            description="测试任务",
            task_type=TaskType.ATOMIC,
            priority=3,
            dependencies=["dep1", "dep2"]
        )
        assert task.id == "test_task"
        assert task.description == "测试任务"
        assert task.task_type == TaskType.ATOMIC
        assert task.priority == 3
        assert task.dependencies == ["dep1", "dep2"]
        assert task.status == TaskStatus.PENDING

    def test_task_mark_running(self):
        task = Task()
        task.mark_running()
        assert task.status == TaskStatus.RUNNING
        assert task.started_at is not None

    def test_task_mark_completed(self):
        task = Task()
        task.mark_completed(result="成功")
        assert task.status == TaskStatus.COMPLETED
        assert task.result == "成功"
        assert task.completed_at is not None

    def test_task_mark_failed(self):
        task = Task()
        task.mark_failed(error="失败原因")
        assert task.status == TaskStatus.FAILED
        assert task.error == "失败原因"
        assert task.completed_at is not None

    def test_task_mark_skipped(self):
        task = Task()
        task.mark_skipped()
        assert task.status == TaskStatus.SKIPPED
        assert task.completed_at is not None

    def test_task_can_execute(self):
        task1 = Task(id="task1", dependencies=[])
        task2 = Task(id="task2", dependencies=["task1"])
        
        completed = {"task1"}
        assert task1.can_execute(completed) is True
        assert task2.can_execute(completed) is True
        
        task1.status = TaskStatus.COMPLETED
        assert task1.can_execute(completed) is False

    def test_task_to_dict(self):
        task = Task(
            id="test_task",
            description="测试任务",
            task_type=TaskType.SEQUENTIAL,
            priority=2
        )
        task.mark_completed("完成结果")
        
        result = task.to_dict()
        assert result["id"] == "test_task"
        assert result["description"] == "测试任务"
        assert result["task_type"] == "sequential"
        assert result["status"] == "completed"
        assert result["result"] == "完成结果"


class TestPlan:
    """计划模型单元测试"""

    def test_plan_creation(self):
        plan = Plan(original_task="原始任务")
        assert plan.id is not None
        assert plan.original_task == "原始任务"
        assert plan.state == PlanState.INIT
        assert plan.tasks == []

    def test_plan_add_task(self):
        plan = Plan()
        task = Task(id="task1", description="任务1")
        plan.add_task(task)
        
        assert len(plan.tasks) == 1
        assert plan.tasks[0].id == "task1"
        assert "task1" in plan.execution_graph

    def test_plan_get_task(self):
        plan = Plan()
        task = Task(id="task1", description="任务1")
        plan.add_task(task)
        
        found = plan.get_task("task1")
        assert found is not None
        assert found.id == "task1"
        
        not_found = plan.get_task("nonexistent")
        assert not_found is None

    def test_plan_get_next_executable_tasks(self):
        task1 = Task(id="task1", dependencies=[], priority=2)
        task2 = Task(id="task2", dependencies=["task1"], priority=3)
        task3 = Task(id="task3", dependencies=["task1", "task2"], priority=1)
        
        plan = Plan(tasks=[task1, task2, task3])
        
        # 初始状态只有 task1 可执行
        executable = plan.get_next_executable_tasks()
        assert len(executable) == 1
        assert executable[0].id == "task1"
        
        # 完成 task1 后，task2 可执行（优先级更高）
        task1.mark_completed()
        executable = plan.get_next_executable_tasks()
        assert len(executable) == 1
        assert executable[0].id == "task2"

    def test_plan_is_complete(self):
        """测试计划是否完成"""
        plan = Plan(state=PlanState.READY)
        task1 = Task(id="task1")
        task2 = Task(id="task2")
        plan.add_task(task1)
        plan.add_task(task2)
        
        assert plan.is_complete() is False
        
        task1.mark_completed()
        assert plan.is_complete() is False
        
        task2.mark_completed()
        assert plan.is_complete() is True

    def test_plan_is_success(self):
        plan = Plan(state=PlanState.COMPLETED)
        task1 = Task(id="task1")
        task2 = Task(id="task2")
        plan.add_task(task1)
        plan.add_task(task2)
        
        task1.mark_completed()
        task2.mark_completed()
        assert plan.is_success() is True
        
        task2.mark_failed("失败")
        plan.state = PlanState.COMPLETED
        assert plan.is_success() is False

    def test_plan_progress(self):
        plan = Plan()
        assert plan.progress() == 0.0
        
        task1 = Task(id="task1")
        task2 = Task(id="task2")
        plan.add_task(task1)
        plan.add_task(task2)
        
        assert plan.progress() == 0.0
        
        task1.mark_completed()
        assert plan.progress() == 0.5
        
        task2.mark_completed()
        assert plan.progress() == 1.0

    def test_plan_to_dict(self):
        plan = Plan(original_task="测试计划")
        task = Task(id="task1", description="任务1")
        plan.add_task(task)
        task.mark_completed()
        
        result = plan.to_dict()
        assert result["id"] is not None
        assert result["original_task"] == "测试计划"
        assert result["state"] == "init"
        assert len(result["tasks"]) == 1


class TestAction:
    """动作模型单元测试"""

    def test_action_tool_action(self):
        action = Action.tool_action(
            tool_name="search",
            params={"query": "test"},
            description="搜索测试"
        )
        assert action.action_type == ActionType.TOOL_CALL
        assert action.tool_name == "search"
        assert action.tool_params == {"query": "test"}
        assert action.description == "搜索测试"

    def test_action_llm_action(self):
        action = Action.llm_action(
            prompt="思考问题",
            description="LLM推理"
        )
        assert action.action_type == ActionType.LLM_REASONING
        assert action.tool_params == {"prompt": "思考问题"}

    def test_action_response_action(self):
        action = Action.response_action("直接响应")
        assert action.action_type == ActionType.RESPONSE
        assert action.tool_params == {"response": "直接响应"}


class TestActionResult:
    """动作结果单元测试"""

    def test_success_result(self):
        result = ActionResult.success_result(
            output="成功输出",
            observation="观察结果"
        )
        assert result.success is True
        assert result.output == "成功输出"
        assert result.observation == "观察结果"
        assert result.error is None

    def test_failure_result(self):
        result = ActionResult.failure_result("失败原因")
        assert result.success is False
        assert result.error == "失败原因"
        assert result.output is None


class TestReActStep:
    """ReAct步骤单元测试"""

    def test_step_creation(self):
        step = ReActStep(
            iteration=0,
            thought="思考内容",
            action="执行动作",
            observation="观察结果",
            success=True
        )
        assert step.iteration == 0
        assert step.thought == "思考内容"
        assert step.action == "执行动作"
        assert step.success is True


class TestReActResult:
    """ReAct结果单元测试"""

    def test_result_creation(self):
        """测试结果创建"""
        steps = [ReActStep(iteration=0, thought="步骤1", action="动作1", observation="观察1", success=True)]
        result = ReActResult(
            success=True,
            result="最终结果",
            steps=steps,
            iterations=1,
            total_duration_ms=1000
        )
        assert result.success is True
        assert result.result == "最终结果"
        assert len(result.steps) == 1
        assert result.iterations == 1
        assert result.total_duration_ms == 1000


class TestThoughtResult:
    """思考结果单元测试"""

    def test_thought_creation(self):
        thought = ThoughtResult(
            reasoning="推理过程",
            action_type="tool_call",
            confidence=0.8,
            result="结果"
        )
        assert thought.reasoning == "推理过程"
        assert thought.action_type == "tool_call"
        assert thought.confidence == 0.8
        assert thought.result == "结果"
