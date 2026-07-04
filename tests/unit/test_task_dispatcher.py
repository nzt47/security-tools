"""TaskDispatcher 任务调度器补充测试

API 契约对齐说明：
TaskDispatcher 的统一入口为 dispatch_task(user_input: str) -> dict，
返回任务处理策略 {path, model, tools_whitelist, needs_planning}。
原测试用 self.dispatcher.dispatch("unknown_task", {}) 是假阳性——
dispatch 方法不存在导致 AttributeError 被 pytest.raises(Exception) 捕获，
测试通过但原因错误。现已改为调用真实 dispatch_task 并断言返回结构。

TaskDispatcher 是 mixin，依赖宿主类提供以下属性/方法：
- _llm: 待命 LLM 服务（需有 model 属性）
- _set_thinking_mode(mode=None): 设置思考模式
- _get_enabled_tools_whitelist() -> list: 启用的工具白名单
其余属性（_llm_pro / _model_router / _planner / _planning_enabled）
通过 getattr 安全读取，缺省为 None / False，无需 mock。
"""
from unittest.mock import MagicMock

from agent.orchestrator.task_dispatcher import TaskDispatcher


class TestTaskDispatcher:
    """TaskDispatcher 基本功能测试"""

    def setup_method(self):
        """构建最小可运行的 dispatcher 实例"""
        self.dispatcher = TaskDispatcher()
        mock_llm = MagicMock()
        mock_llm.model = "standby-model"
        self.dispatcher._llm = mock_llm
        self.dispatcher._set_thinking_mode = MagicMock()
        self.dispatcher._get_enabled_tools_whitelist = MagicMock(return_value=[])

    def test_create_dispatcher(self):
        """验证实例创建成功"""
        assert self.dispatcher is not None

    def test_dispatch_simple_task_returns_strategy(self):
        """简单任务应返回 direct 路径策略字典"""
        result = self.dispatcher.dispatch_task("你好")

        assert isinstance(result, dict)
        assert result["path"] == "direct"
        assert result["needs_planning"] is False
        assert result["model"] == "standby-model"
        assert "tools_whitelist" in result

    def test_dispatch_complex_task_triggers_planning(self):
        """复杂任务在启用规划时应返回 planning 路径"""
        self.dispatcher._planning_enabled = True
        self.dispatcher._planner = MagicMock()

        # "帮我完成" 命中 complex_indicators
        result = self.dispatcher.dispatch_task("帮我完成数据分析流程")

        assert result["path"] == "planning"
        assert result["needs_planning"] is True
