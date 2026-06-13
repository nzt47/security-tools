"""DigitalLife 核心方法测试：process_input、_think、_act"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

class TestProcessInput:
    """测试 _process_user_input 方法"""

    def test_process_input_normal_flow(self):
        """测试正常处理流程"""
        from agent.digital_life import DigitalLife

        digital_life = MagicMock(spec=DigitalLife)
        digital_life._running = True
        digital_life._behavior = MagicMock()
        digital_life._behavior.can_execute.return_value = (True, None)
        digital_life._call_llm.return_value = "LLM response"
        digital_life._behavior.profile.enable_reflection = False
        digital_life._memory = MagicMock()
        digital_life._vector_memory = None
        digital_life.check_health.return_value = []
        digital_life._build_body_status.return_value = "Body status"

        result = DigitalLife._process_user_input(digital_life, "Hello")

        assert result == "LLM response"
        digital_life._call_llm.assert_called_once_with("Hello", "Body status")
        digital_life._memory.add_message.assert_any_call("user", "Hello")
        digital_life._memory.add_message.assert_any_call("assistant", "LLM response")

    def test_process_input_rejected(self):
        """测试输入被拒绝的情况"""
        from agent.digital_life import DigitalLife

        digital_life = MagicMock(spec=DigitalLife)
        digital_life._running = True
        digital_life._behavior = MagicMock()
        digital_life._behavior.can_execute.return_value = (False, "Content blocked")
        digital_life._build_reject_response.return_value = "Rejected response"
        digital_life.check_health.return_value = []
        digital_life._memory = MagicMock()
        # 添加 _current_mode 属性
        digital_life._current_mode = MagicMock()
        digital_life._current_mode.value = "test_mode"

        result = DigitalLife._process_user_input(digital_life, "Malicious input")

        assert result == "Rejected response"
        digital_life._memory.save_log.assert_called_once_with(
            "task_rejected",
            {
                "reason": "Content blocked",
                "mode": "test_mode",
                "input_preview": "Malicious input"[:100],
            }
        )

    def test_process_input_with_vector_memory(self):
        """测试带向量记忆的处理流程"""
        from agent.digital_life import DigitalLife

        digital_life = MagicMock(spec=DigitalLife)
        digital_life._running = True
        digital_life._behavior = MagicMock()
        digital_life._behavior.can_execute.return_value = (True, None)
        digital_life._call_llm.return_value = "LLM response"
        digital_life._behavior.profile.enable_reflection = False
        digital_life._memory = MagicMock()
        digital_life._vector_memory = MagicMock()
        digital_life._vector_memory.add.return_value = "memory_item_id"
        digital_life.check_health.return_value = []
        digital_life._build_body_status.return_value = "Body status"
        digital_life._interaction_count = 42

        result = DigitalLife._process_user_input(digital_life, "Hello")

        assert result == "LLM response"
        digital_life._vector_memory.add.assert_called_once()

    def test_process_input_vector_memory_failure(self):
        """测试向量记忆保存失败"""
        from agent.digital_life import DigitalLife

        digital_life = MagicMock(spec=DigitalLife)
        digital_life._running = True
        digital_life._behavior = MagicMock()
        digital_life._behavior.can_execute.return_value = (True, None)
        digital_life._call_llm.return_value = "LLM response"
        digital_life._behavior.profile.enable_reflection = False
        digital_life._memory = MagicMock()
        digital_life._vector_memory = MagicMock()
        digital_life._vector_memory.add.side_effect = Exception("Connection failed")
        digital_life.check_health.return_value = []
        digital_life._build_body_status.return_value = "Body status"

        with patch("agent.digital_life.logger"):
            result = DigitalLife._process_user_input(digital_life, "Hello")

        assert result == "LLM response"

    def test_process_input_with_reflection(self):
        """测试带自我反思的处理流程"""
        from agent.digital_life import DigitalLife

        digital_life = MagicMock(spec=DigitalLife)
        digital_life._running = True
        digital_life._behavior = MagicMock()
        digital_life._behavior.can_execute.return_value = (True, None)
        digital_life._call_llm.return_value = "LLM response"
        digital_life._behavior.profile.enable_reflection = True
        digital_life.self_reflect = MagicMock()
        digital_life._memory = MagicMock()
        digital_life._vector_memory = None
        digital_life.check_health.return_value = []
        digital_life._build_body_status.return_value = "Body status"

        result = DigitalLife._process_user_input(digital_life, "Hello")

        assert result == "LLM response"
        digital_life.self_reflect.assert_called_once_with("Hello", "LLM response")


class TestChatImpl:
    """测试 _chat_impl 方法"""

    def test_chat_impl_not_running(self):
        """测试未运行状态"""
        from agent.digital_life import DigitalLife

        digital_life = MagicMock(spec=DigitalLife)
        digital_life._running = False
        digital_life._interaction_count = 0

        with patch("agent.digital_life.logger"):
            with patch("agent.digital_life._MONITORING_AVAILABLE", False):
                result = DigitalLife._chat_impl(digital_life, "Hello")

        assert result == "我还没有被唤醒。请先调用 start() 让我醒来。"

    def test_chat_impl_empty_input(self):
        """测试空输入"""
        from agent.digital_life import DigitalLife

        digital_life = MagicMock(spec=DigitalLife)
        digital_life._running = True
        digital_life._interaction_count = 0
        digital_life._behavior = MagicMock()
        digital_life._v2_lifetrace = None
        digital_life._trace_recorder = None
        digital_life._planning_enabled = False
        digital_life._planner = None
        digital_life._process_user_input = MagicMock(return_value="")

        with patch("agent.digital_life.logger"):
            with patch("agent.digital_life._MONITORING_AVAILABLE", False):
                result = DigitalLife._chat_impl(digital_life, "")

        assert result == ""

    def test_chat_impl_normal_flow(self):
        """测试正常对话流程"""
        from agent.digital_life import DigitalLife

        digital_life = MagicMock(spec=DigitalLife)
        digital_life._running = True
        digital_life._interaction_count = 0
        digital_life._behavior = MagicMock()
        digital_life._v2_lifetrace = None
        digital_life._trace_recorder = None
        digital_life._planning_enabled = False  # 添加规划相关属性
        digital_life._planner = None
        digital_life._process_user_input = MagicMock(return_value="Response")

        with patch("agent.digital_life.logger"):
            with patch("agent.digital_life._MONITORING_AVAILABLE", False):
                result = DigitalLife._chat_impl(digital_life, "Hello")

        assert result == "Response"
        digital_life._process_user_input.assert_called_once_with("Hello")