"""
DigitalLife 核心方法测试
覆盖 _process_user_input、_call_llm 等核心逻辑
"""
import pytest
import os
import sys
from unittest.mock import patch, MagicMock, PropertyMock

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.digital_life import DigitalLife


class MockBehavior:
    """模拟行为控制器"""
    def __init__(self, can_execute=True, reject_reason=""):
        self.can_execute_result = (can_execute, reject_reason)
        self.profile = MagicMock()
        self.profile.label = "default"
        self.profile.description = "Default mode"
        self.profile.enable_reflection = False
        self._reasons = []
    
    def can_execute(self, user_input):
        return self.can_execute_result


class MockMemory:
    """模拟记忆系统"""
    def __init__(self):
        self.messages = []
    
    def save_log(self, log_type, data):
        pass
    
    def add_message(self, role, content):
        self.messages.append({"role": role, "content": content})
    
    def get_context(self, token_limit=None):
        return self.messages
    
    def load_summary(self):
        return None


class MockVectorMemory:
    """模拟向量记忆系统"""
    def __init__(self):
        self.data = []
    
    def add(self, content, metadata=None):
        self.data.append({"content": content, "metadata": metadata})
        return f"memory_{len(self.data)}"
    
    def search(self, query, top_k=3):
        return []


class MockLLM:
    """模拟LLM服务"""
    def __init__(self, response="Hello, how can I help you?"):
        self.response = response
    
    def chat(self, messages=None, system_prompt=None, **kwargs):
        return self.response


class TestProcessUserInput:
    """测试 _process_user_input 方法"""

    def test_process_user_input_success(self):
        """测试正常处理用户输入"""
        digital_life = MagicMock(spec=DigitalLife)
        
        # 设置模拟
        digital_life.check_health.return_value = []
        digital_life._build_body_status.return_value = "Body status"
        digital_life._behavior = MockBehavior(can_execute=True)
        
        mock_memory = MagicMock()
        digital_life._memory = mock_memory
        
        digital_life._vector_memory = None
        digital_life._call_llm.return_value = "LLM response"
        digital_life._interaction_count = 1
        
        # 直接调用 _process_user_input 方法
        from agent.digital_life import DigitalLife as RealDigitalLife
        result = RealDigitalLife._process_user_input(digital_life, "Hello")
        
        assert result == "LLM response"
        digital_life._call_llm.assert_called_once_with("Hello", "Body status")
        mock_memory.add_message.assert_called()

    def test_process_user_input_rejected(self):
        """测试用户输入被拒绝"""
        digital_life = MagicMock()
        
        digital_life.check_health.return_value = []
        digital_life._build_body_status.return_value = "Body status"
        digital_life._behavior = MockBehavior(can_execute=False, reject_reason="Content rejected")
        digital_life._build_reject_response.return_value = "Request rejected"
        
        mock_memory = MagicMock()
        digital_life._memory = mock_memory
        
        mock_current_mode = MagicMock()
        mock_current_mode.value = "test_mode"
        digital_life._current_mode = mock_current_mode
        
        from agent.digital_life import DigitalLife as RealDigitalLife
        result = RealDigitalLife._process_user_input(digital_life, "Malicious input")
        
        assert result == "Request rejected"
        mock_memory.save_log.assert_called_once_with(
            "task_rejected",
            {
                "reason": "Content rejected",
                "mode": "test_mode",
                "input_preview": "Malicious input"[:100]
            }
        )

    def test_process_user_input_with_vector_memory(self):
        """测试向量记忆保存"""
        digital_life = MagicMock(spec=DigitalLife)
        
        digital_life.check_health.return_value = []
        digital_life._build_body_status.return_value = "Body status"
        digital_life._behavior = MockBehavior(can_execute=True)
        digital_life._memory = MockMemory()
        
        mock_vector_memory = MockVectorMemory()
        digital_life._vector_memory = mock_vector_memory
        
        digital_life._call_llm.return_value = "Response"
        digital_life._interaction_count = 1
        
        from agent.digital_life import DigitalLife as RealDigitalLife
        result = RealDigitalLife._process_user_input(digital_life, "Test input")
        
        assert result == "Response"
        assert len(mock_vector_memory.data) == 1
        assert "用户: Test input" in mock_vector_memory.data[0]["content"]

    def test_process_user_input_vector_memory_failure(self):
        """测试向量记忆保存失败"""
        digital_life = MagicMock(spec=DigitalLife)
        
        digital_life.check_health.return_value = []
        digital_life._build_body_status.return_value = "Body status"
        digital_life._behavior = MockBehavior(can_execute=True)
        digital_life._memory = MockMemory()
        
        mock_vector_memory = MagicMock()
        mock_vector_memory.add.side_effect = Exception("Memory save failed")
        digital_life._vector_memory = mock_vector_memory
        
        digital_life._call_llm.return_value = "Response"
        digital_life._interaction_count = 1
        
        from agent.digital_life import DigitalLife as RealDigitalLife
        result = RealDigitalLife._process_user_input(digital_life, "Test input")
        
        # 即使向量记忆保存失败，也应该返回响应
        assert result == "Response"

    def test_process_user_input_with_reflection(self):
        """测试启用反思功能"""
        digital_life = MagicMock(spec=DigitalLife)
        
        digital_life.check_health.return_value = []
        digital_life._build_body_status.return_value = "Body status"
        
        behavior = MockBehavior(can_execute=True)
        behavior.profile.enable_reflection = True
        digital_life._behavior = behavior
        
        digital_life._memory = MockMemory()
        digital_life._vector_memory = None
        digital_life._call_llm.return_value = "Response"
        digital_life.self_reflect = MagicMock()
        digital_life._interaction_count = 1
        
        from agent.digital_life import DigitalLife as RealDigitalLife
        result = RealDigitalLife._process_user_input(digital_life, "Test input")
        
        assert result == "Response"
        digital_life.self_reflect.assert_called_once_with("Test input", "Response")


class TestBuildBodyStatus:
    """测试 _build_body_status 方法"""

    def test_build_body_status_empty_readings(self):
        """测试空读数时返回默认状态"""
        digital_life = MagicMock(spec=DigitalLife)
        
        from agent.digital_life import DigitalLife as RealDigitalLife
        result = RealDigitalLife._build_body_status(digital_life, [])
        
        assert result == "我感觉很好，一切正常。"

    def test_build_body_status_with_readings(self):
        """测试有读数时构建状态"""
        digital_life = MagicMock(spec=DigitalLife)
        
        mock_reading = MagicMock()
        mock_reading.to_dict.return_value = {"type": "test", "value": 100}
        
        digital_life._injector = MagicMock()
        digital_life._injector.inject.return_value = "Injected status"
        
        behavior = MagicMock()
        behavior.profile.label = "Test Mode"
        behavior.profile.description = "Testing"
        behavior._reasons = []
        digital_life._behavior = behavior
        
        from agent.digital_life import DigitalLife as RealDigitalLife
        result = RealDigitalLife._build_body_status(digital_life, [mock_reading])
        
        assert "Injected status" in result
        assert "当前行为模式：Test Mode — Testing" in result

    def test_build_body_status_with_reasons(self):
        """测试带有触发原因的状态"""
        digital_life = MagicMock(spec=DigitalLife)
        
        mock_reading = MagicMock()
        mock_reading.to_dict.return_value = {"type": "test", "value": 100}
        
        digital_life._injector = MagicMock()
        digital_life._injector.inject.return_value = "Status"
        
        behavior = MagicMock()
        behavior.profile.label = "Mode"
        behavior.profile.description = "Desc"
        behavior._reasons = ["reason1", "reason2"]
        digital_life._behavior = behavior
        
        from agent.digital_life import DigitalLife as RealDigitalLife
        result = RealDigitalLife._build_body_status(digital_life, [mock_reading])
        
        assert "触发原因：reason1；reason2" in result


class TestCallLLM:
    """测试 _call_llm 方法"""

    def test_call_llm_basic(self):
        """测试基本LLM调用"""
        digital_life = MagicMock()
        
        digital_life._current_mode = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test description"
        digital_life._behavior.profile.response_prefix = ""
        digital_life._llm = MockLLM(response="LLM response")
        digital_life._vector_memory = None
        digital_life._memory = MockMemory()
        
        from agent.digital_life import DigitalLife as RealDigitalLife
        result = RealDigitalLife._call_llm(digital_life, "Hello", "Body status")
        
        assert result == "LLM response"

    def test_call_llm_with_vector_memory(self):
        """测试带向量记忆的LLM调用"""
        digital_life = MagicMock()
        
        digital_life._current_mode = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test description"
        digital_life._behavior.profile.response_prefix = ""
        digital_life._llm = MockLLM(response="Response")
        
        mock_vector_memory = MagicMock()
        mock_vector_memory.search.return_value = [MagicMock(content="Related memory")]
        digital_life._vector_memory = mock_vector_memory
        
        digital_life._memory = MockMemory()
        
        from agent.digital_life import DigitalLife as RealDigitalLife
        result = RealDigitalLife._call_llm(digital_life, "Hello", "Body status")
        
        assert result == "Response"
        mock_vector_memory.search.assert_called_once_with("Hello", top_k=3)

    def test_call_llm_vector_memory_failure(self):
        """测试向量记忆搜索失败"""
        digital_life = MagicMock()
        
        digital_life._current_mode = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test description"
        digital_life._behavior.profile.response_prefix = ""
        digital_life._llm = MockLLM(response="Response")
        
        mock_vector_memory = MagicMock()
        mock_vector_memory.search.side_effect = Exception("Search failed")
        digital_life._vector_memory = mock_vector_memory
        
        digital_life._memory = MockMemory()
        
        from agent.digital_life import DigitalLife as RealDigitalLife
        result = RealDigitalLife._call_llm(digital_life, "Hello", "Body status")
        
        # 即使向量记忆失败，也应该返回响应
        assert result == "Response"

    def test_call_llm_with_context_messages(self):
        """测试带上下文消息的LLM调用"""
        digital_life = MagicMock()
        
        digital_life._current_mode = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test description"
        digital_life._behavior.profile.response_prefix = ""
        digital_life._llm = MockLLM(response="Response")
        digital_life._vector_memory = None
        
        mock_memory = MagicMock()
        mock_memory.get_context.return_value = [
            {"role": "user", "content": "Previous message"}
        ]
        mock_memory.load_summary.return_value = None
        digital_life._memory = mock_memory
        
        from agent.digital_life import DigitalLife as RealDigitalLife
        result = RealDigitalLife._call_llm(digital_life, "Hello", "Body status")
        
        assert result == "Response"
        # get_context 在 _call_llm 中可能被调用多次
        assert mock_memory.get_context.call_count >= 1


class TestChatFlow:
    """测试完整对话流程"""

    def test_chat_basic_flow(self):
        """测试基本对话流程"""
        digital_life = MagicMock()
        
        mock_chat_impl = MagicMock(return_value="Response")
        digital_life._chat_impl = mock_chat_impl
        
        with patch('agent.digital_life._MONITORING_AVAILABLE', False):
            from agent.digital_life import DigitalLife as RealDigitalLife
            result = RealDigitalLife.chat(digital_life, "Hello")
        
        assert result == "Response"
        mock_chat_impl.assert_called_once_with("Hello")

    def test_chat_exception_handling(self):
        """测试对话异常处理"""
        digital_life = MagicMock()
        
        # 模拟 _chat_impl 返回异常处理后的消息
        mock_chat_impl = MagicMock(return_value="抱歉，处理您的请求时遇到了问题：Test error")
        digital_life._chat_impl = mock_chat_impl
        
        with patch('agent.digital_life._MONITORING_AVAILABLE', False):
            from agent.digital_life import DigitalLife as RealDigitalLife
            result = RealDigitalLife.chat(digital_life, "Hello")
        
        assert "抱歉，处理您的请求时遇到了问题" in str(result)
        assert "Test error" in str(result)


class TestChatV2Flow:
    """测试V2对话流程"""

    def test_chat_v2_basic(self):
        """测试V2对话流程"""
        digital_life = MagicMock()
        
        digital_life.check_health.return_value = []
        digital_life._trace_recorder = MagicMock()
        digital_life._build_body_status.return_value = "Body status"
        digital_life._v2_distillation = False
        
        digital_life._behavior = MagicMock()
        digital_life._behavior.can_execute.return_value = (True, "")
        
        digital_life._v2_persona = False
        digital_life._call_llm_v2.return_value = "Response"
        
        with patch('agent.digital_life.logger'):
            from agent.digital_life import DigitalLife as RealDigitalLife
            result = RealDigitalLife._chat_v2(digital_life, "Hello")
        
        assert result == "Response"
        digital_life._trace_recorder.record_chat.assert_called()

    def test_chat_v2_rejected(self):
        """测试V2对话被拒绝"""
        digital_life = MagicMock()
        
        digital_life.check_health.return_value = []
        digital_life._trace_recorder = MagicMock()
        digital_life._build_body_status.return_value = "Body status"
        digital_life._v2_distillation = False
        
        digital_life._behavior = MagicMock()
        digital_life._behavior.can_execute.return_value = (False, "Rejected")
        
        digital_life._v2_persona = False
        digital_life._build_reject_response.return_value = "Rejected response"
        
        with patch('agent.digital_life.logger'):
            from agent.digital_life import DigitalLife as RealDigitalLife
            result = RealDigitalLife._chat_v2(digital_life, "Hello")
        
        assert result == "Rejected response"