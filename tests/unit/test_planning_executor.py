"""计划执行器单元测试"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task, TaskStatus, Action, ActionType, ActionResult


class TestToolRegistry:
    """工具注册表单元测试"""

    def test_tool_registry_initialization(self):
        """测试工具注册表初始化"""
        registry = ToolRegistry()
        assert registry is not None
        assert registry.list_tools() == []

    def test_register_tool(self):
        """测试注册工具"""
        registry = ToolRegistry()
        
        def test_tool():
            return "result"
        
        registry.register("test_tool", test_tool, {"description": "测试工具"})
        
        assert registry.has("test_tool") is True
        assert registry.get("test_tool") == test_tool
        assert registry.get_schema("test_tool") == {"description": "测试工具"}
        assert "test_tool" in registry.list_tools()

    def test_get_nonexistent_tool(self):
        """测试获取不存在的工具"""
        registry = ToolRegistry()
        
        assert registry.get("nonexistent") is None
        assert registry.has("nonexistent") is False

    def test_find_tool(self):
        """测试根据描述查找工具"""
        registry = ToolRegistry()
        
        def search_tool():
            pass
        
        def analyze_tool():
            pass
        
        registry.register("search", search_tool)
        registry.register("analyze", analyze_tool)
        
        assert registry.find_tool("使用search工具") == "search"
        assert registry.find_tool("调用analyze") == "analyze"
        assert registry.find_tool("未知操作") is None


class TestPlanExecutor:
    """计划执行器单元测试"""

    def test_executor_initialization(self):
        """测试执行器初始化"""
        registry = ToolRegistry()
        executor = PlanExecutor(registry)
        
        assert executor is not None
        assert executor.tool_registry == registry
        assert executor.max_retries == 3
        assert executor.execution_history == []

    def test_register_callback(self):
        """测试注册事件回调"""
        registry = ToolRegistry()
        executor = PlanExecutor(registry)
        
        callback_called = []
        
        def test_callback(*args):
            callback_called.append(args)
        
        executor.register_callback("on_task_complete", test_callback)
        
        assert len(executor._callbacks["on_task_complete"]) == 1

    def test_determine_action_tool(self):
        """测试确定动作 - 工具调用"""
        registry = ToolRegistry()
        registry.register("search", lambda: "result")
        
        executor = PlanExecutor(registry)
        task = Task(description="使用search搜索")
        
        action = executor._determine_action(task)
        
        assert action.action_type == ActionType.TOOL_CALL
        assert action.tool_name == "search"

    def test_determine_action_llm(self):
        """测试确定动作 - LLM推理"""
        registry = ToolRegistry()
        mock_llm = MagicMock()
        
        executor = PlanExecutor(registry, llm_service=mock_llm)
        task = Task(description="思考一个问题")
        
        action = executor._determine_action(task)
        
        assert action.action_type == ActionType.LLM_REASONING

    def test_determine_action_response(self):
        """测试确定动作 - 直接响应"""
        registry = ToolRegistry()
        
        executor = PlanExecutor(registry)
        task = Task(description="无法执行的任务")
        
        action = executor._determine_action(task)
        
        assert action.action_type == ActionType.RESPONSE

    @pytest.mark.asyncio
    async def test_execute_tool_call(self):
        """测试执行工具调用"""
        registry = ToolRegistry()
        registry.register("test_tool", lambda x: f"result_{x}")
        
        executor = PlanExecutor(registry)
        action = Action.tool_action("test_tool", {"x": "test"}, "测试工具调用")
        
        result = await executor._execute_action(action)
        
        assert result.success is True
        assert result.output == "result_test"

    @pytest.mark.asyncio
    async def test_execute_tool_call_async(self):
        """测试执行异步工具调用"""
        registry = ToolRegistry()
        
        async def async_tool(x):
            return f"async_result_{x}"
        
        registry.register("async_tool", async_tool)
        
        executor = PlanExecutor(registry)
        action = Action.tool_action("async_tool", {"x": "test"}, "异步测试")
        
        result = await executor._execute_action(action)
        
        assert result.success is True
        assert result.output == "async_result_test"

    @pytest.mark.asyncio
    async def test_execute_tool_call_failure(self):
        """测试工具调用失败"""
        registry = ToolRegistry()
        registry.register("bad_tool", lambda: 1/0)
        
        executor = PlanExecutor(registry)
        action = Action.tool_action("bad_tool", {}, "失败工具")
        
        result = await executor._execute_action(action)
        
        assert result.success is False
        assert "division by zero" in result.error

    @pytest.mark.asyncio
    async def test_execute_llm_reasoning(self):
        """测试执行LLM推理"""
        registry = ToolRegistry()
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = "LLM响应"
        
        executor = PlanExecutor(registry, llm_service=mock_llm)
        action = Action.llm_action("思考问题", "LLM推理")
        
        result = await executor._execute_action(action)
        
        assert result.success is True
        assert result.output == "LLM响应"

    @pytest.mark.asyncio
    async def test_execute_plan_success(self):
        """测试执行计划成功"""
        registry = ToolRegistry()
        registry.register("search", lambda: "搜索结果")
        
        executor = PlanExecutor(registry)
        
        plan = Plan(original_task="测试计划")
        plan.add_task(Task(id="task1", description="搜索信息"))
        plan.state = PlanState.READY
        
        result = await executor.execute_plan(plan)
        
        assert result.state == PlanState.COMPLETED
        assert result.is_success() is True
        assert len(result.tasks) == 1
        assert result.tasks[0].status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_plan_failure(self):
        """测试执行计划失败"""
        registry = ToolRegistry()
        registry.register("bad_tool", lambda: 1/0)
        
        executor = PlanExecutor(registry)
        
        plan = Plan(original_task="测试计划")
        plan.add_task(Task(id="task1", description="使用bad_tool", priority=4))
        plan.state = PlanState.READY
        
        result = await executor.execute_plan(plan)
        
        assert result.is_success() is False
        assert result.tasks[0].status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_execute_plan_with_dependencies(self):
        """测试执行有依赖关系的计划"""
        registry = ToolRegistry()
        registry.register("step1", lambda: "result1")
        registry.register("step2", lambda: "result2")
        
        executor = PlanExecutor(registry)
        
        plan = Plan(original_task="测试计划")
        plan.add_task(Task(id="task1", description="step1", dependencies=[]))
        plan.add_task(Task(id="task2", description="step2", dependencies=["task1"]))
        plan.state = PlanState.READY
        
        result = await executor.execute_plan(plan)
        
        assert result.state == PlanState.COMPLETED
        assert result.tasks[0].status == TaskStatus.COMPLETED
        assert result.tasks[1].status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_plan_callback(self):
        """测试执行计划时触发回调"""
        registry = ToolRegistry()
        registry.register("test_tool", lambda: "result")
        
        executor = PlanExecutor(registry)
        callbacks = []
        
        def on_task_complete(task, result):
            callbacks.append(("task_complete", task.id))
        
        executor.register_callback("on_task_complete", on_task_complete)
        
        plan = Plan(original_task="测试计划")
        plan.add_task(Task(id="task1", description="test_tool"))
        plan.state = PlanState.READY
        
        await executor.execute_plan(plan)
        
        assert len(callbacks) == 1
        assert callbacks[0] == ("task_complete", "task1")

    @pytest.mark.asyncio
    async def test_cancel_plan(self):
        """测试取消计划"""
        registry = ToolRegistry()
        executor = PlanExecutor(registry)
        
        plan = Plan(original_task="测试计划")
        plan.state = PlanState.EXECUTING
        
        result = await executor.cancel_plan(plan)
        
        assert result.state == PlanState.CANCELLED

    def test_get_history(self):
        """测试获取执行历史"""
        registry = ToolRegistry()
        executor = PlanExecutor(registry)
        
        assert len(executor.get_history()) == 0
