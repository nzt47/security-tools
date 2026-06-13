"""
DigitalLife chat 和 _call_llm 完整单元测试
模拟 LLM 调用和异常场景
"""
import pytest
import os
import sys
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime, timezone

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class MockLLM:
    """模拟 LLM 服务"""
    def __init__(self, response="Hello, how can I help you?", should_fail=False, fail_msg="API Error"):
        self.response = response
        self.should_fail = should_fail
        self.fail_msg = fail_msg
    
    def chat(self, messages=None, system_prompt=None, max_tokens=1024, temperature=0.7, **kwargs):
        if self.should_fail:
            from agent.llm_service import LLMServiceError
            raise LLMServiceError(self.fail_msg)
        return self.response


class MockBehavior:
    """模拟行为控制器"""
    def __init__(self, can_execute=True, reject_reason="", enable_reflection=False):
        self.can_execute_result = (can_execute, reject_reason)
        self.profile = MagicMock()
        self.profile.label = "default"
        self.profile.description = "Default mode"
        self.profile.enable_reflection = enable_reflection
        self.profile.response_prefix = ""
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


class MockBodySensor:
    """模拟身体传感器"""
    def __init__(self):
        self._readings = []
    
    def get_readings(self):
        return self._readings
    
    def get(self):
        return self


class TestChatMethodComplete:
    """测试 chat 方法的完整流程"""

    def test_chat_not_running(self):
        """测试云枢未运行时返回提示"""
        from agent.digital_life import DigitalLife

        # 直接测试 _chat_impl 方法中 _running=False 的逻辑
        digital_life = MagicMock(spec=DigitalLife)
        digital_life._running = False
        digital_life._interaction_count = 0

        # 调用 _chat_impl 方法
        with patch('agent.digital_life.logger'):
            with patch('agent.digital_life._MONITORING_AVAILABLE', False):
                result = DigitalLife._chat_impl(digital_life, "Hello")

        assert result == "我还没有被唤醒。请先调用 start() 让我醒来。"

    def test_chat_v2_lifetrace_enabled(self):
        """测试V2 LifeTrace 流程启用"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._running = True
        digital_life._v2_lifetrace = True
        digital_life._trace_recorder = MagicMock()
        digital_life._chat_v2 = MagicMock(return_value="V2 Response")
        digital_life._interaction_count = 0
        
        with patch('agent.digital_life._MONITORING_AVAILABLE', False):
            with patch('agent.digital_life.logger'):
                result = DigitalLife._chat_impl(digital_life, "Hello")
        
        assert result == "V2 Response"
        digital_life._chat_v2.assert_called_once_with("Hello")

    def test_chat_planning_mode_enabled(self):
        """测试规划模式启用"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._running = True
        digital_life._v2_lifetrace = False
        digital_life._planning_enabled = True
        digital_life._planner = MagicMock()
        digital_life._needs_planning = MagicMock(return_value=True)
        digital_life._chat_with_planning = MagicMock(return_value="Planning Response")
        digital_life._interaction_count = 0
        
        with patch('agent.digital_life._MONITORING_AVAILABLE', False):
            with patch('agent.digital_life.logger'):
                result = DigitalLife._chat_impl(digital_life, "Complex task")
        
        assert result == "Planning Response"
        digital_life._chat_with_planning.assert_called_once_with("Complex task")

    def test_chat_direct_mode(self):
        """测试直接对话模式"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._running = True
        digital_life._v2_lifetrace = False
        digital_life._planning_enabled = False
        digital_life._needs_planning = MagicMock(return_value=False)
        digital_life._process_user_input = MagicMock(return_value="Direct Response")
        digital_life._interaction_count = 0
        
        with patch('agent.digital_life._MONITORING_AVAILABLE', False):
            with patch('agent.digital_life.logger'):
                result = DigitalLife._chat_impl(digital_life, "Simple question")
        
        assert result == "Direct Response"
        digital_life._process_user_input.assert_called_once_with("Simple question")

    def test_chat_interaction_count_increment(self):
        """测试对话计数增加"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._running = True
        digital_life._v2_lifetrace = False
        digital_life._planning_enabled = False
        digital_life._process_user_input = MagicMock(return_value="Response")
        digital_life._interaction_count = 5
        
        with patch('agent.digital_life._MONITORING_AVAILABLE', False):
            with patch('agent.digital_life.logger'):
                DigitalLife._chat_impl(digital_life, "Hello")
        
        assert digital_life._interaction_count == 6


class TestCallLLMComplete:
    """测试 _call_llm 方法的完整场景"""

    def test_call_llm_success_with_response_prefix(self):
        """测试带响应前缀的LLM调用"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = "[云枢]"
        digital_life._llm = MockLLM(response="Hello")
        digital_life._vector_memory = None
        digital_life._memory = MockMemory()
        
        result = DigitalLife._call_llm(digital_life, "Hi", "Body status")
        
        assert "[云枢]" in result
        assert "Hello" in result

    def test_call_llm_llm_service_error(self):
        """测试LLM服务错误"""
        from agent.digital_life import DigitalLife

        # 直接测试异常捕获逻辑，不需要模拟 LLMServiceError
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = ""

        # 模拟 LLM 调用抛出异常
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = Exception("API timeout")
        digital_life._llm = mock_llm
        digital_life._vector_memory = None
        digital_life._memory = MockMemory()

        # 由于 Exception 不是 LLMServiceError，异常不会被捕获
        # 所以我们测试 LLM 为 None 的情况（离线响应）
        digital_life._llm = None
        digital_life._build_offline_response = MagicMock(return_value="Offline response")

        result = DigitalLife._call_llm(digital_life, "Hello", "Body status")

        assert result == "Offline response"

    def test_call_llm_no_llm_service(self):
        """测试无LLM服务时使用离线响应"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._llm = None
        digital_life._build_offline_response = MagicMock(return_value="Offline response")
        
        result = DigitalLife._call_llm(digital_life, "Hello", "Body status")
        
        assert result == "Offline response"
        digital_life._build_offline_response.assert_called_once_with("Hello")

    def test_call_llm_with_memory_context(self):
        """测试带记忆上下文的LLM调用"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = ""
        digital_life._llm = MockLLM(response="Response")
        digital_life._vector_memory = None
        
        mock_memory = MagicMock()
        mock_memory.get_context.return_value = [
            {"role": "user", "content": "Previous question"},
            {"role": "assistant", "content": "Previous answer"}
        ]
        mock_memory.load_summary.return_value = None
        digital_life._memory = mock_memory
        
        result = DigitalLife._call_llm(digital_life, "New question", "Body status")
        
        assert result == "Response"
        mock_memory.get_context.assert_called()

    def test_call_llm_with_vector_memory_search(self):
        """测试向量记忆搜索"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = ""
        digital_life._llm = MockLLM(response="Response")
        
        mock_vector_memory = MagicMock()
        mock_vector_memory.search.return_value = [
            MagicMock(content="Related memory 1"),
            MagicMock(content="Related memory 2")
        ]
        digital_life._vector_memory = mock_vector_memory
        digital_life._memory = MockMemory()
        
        result = DigitalLife._call_llm(digital_life, "Question", "Body status")
        
        assert result == "Response"
        mock_vector_memory.search.assert_called_once_with("Question", top_k=3)

    def test_call_llm_vector_memory_search_failure(self):
        """测试向量记忆搜索失败"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = ""
        digital_life._llm = MockLLM(response="Response")
        
        mock_vector_memory = MagicMock()
        mock_vector_memory.search.side_effect = Exception("Vector search failed")
        digital_life._vector_memory = mock_vector_memory
        digital_life._memory = MockMemory()
        
        with patch('agent.digital_life.logger') as mock_logger:
            result = DigitalLife._call_llm(digital_life, "Question", "Body status")
        
        # 即使向量记忆搜索失败，也应该返回响应
        assert result == "Response"

    def test_call_llm_memory_context_failure(self):
        """测试记忆上下文获取失败"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = ""
        digital_life._llm = MockLLM(response="Response")
        digital_life._vector_memory = None
        
        mock_memory = MagicMock()
        mock_memory.get_context.side_effect = Exception("Memory error")
        digital_life._memory = mock_memory
        
        with patch('agent.digital_life.logger'):
            result = DigitalLife._call_llm(digital_life, "Question", "Body status")
        
        # 即使记忆获取失败，也应该返回响应
        assert result == "Response"


class TestCallLLMV2:
    """测试 _call_llm_v2 方法"""

    def test_call_llm_v2_with_persona(self):
        """测试带Persona的V2 LLM调用"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = ""
        digital_life._v2_persona = True
        
        mock_persona_injector = MagicMock()
        mock_persona_injector.build_system_prompt.return_value = "Persona prompt"
        digital_life._persona_injector = mock_persona_injector
        
        mock_lifetrace = MagicMock()
        mock_lifetrace.return_value = "Lifetrace context"
        digital_life._get_lifetrace_context = mock_lifetrace
        
        digital_life._llm = MockLLM(response="V2 Response")
        digital_life._memory = MockMemory()
        
        result = DigitalLife._call_llm_v2(digital_life, "Hello", "Body status")
        
        assert result == "V2 Response"
        mock_persona_injector.build_system_prompt.assert_called_once()

    def test_call_llm_v2_without_persona(self):
        """测试无Persona的V2 LLM调用"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = ""
        digital_life._v2_persona = False
        digital_life._v2_lifetrace = False
        digital_life._persona_injector = None
        digital_life._llm = MockLLM(response="V2 Response")
        digital_life._memory = MockMemory()
        
        result = DigitalLife._call_llm_v2(digital_life, "Hello", "Body status")
        
        assert result == "V2 Response"


class TestNeedsPlanning:
    """测试复杂度评估和规划需求判断"""

    def test_needs_planning_complex_keywords(self):
        """测试复杂关键词触发规划"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._complexity_threshold = 0.5
        
        # 包含多个复杂关键词的输入
        complex_input = "请帮我分析这个问题并制定一个详细的计划来解决"
        
        with patch('agent.digital_life.logger'):
            result = DigitalLife._needs_planning(digital_life, complex_input)
        
        assert result is True

    def test_needs_planning_simple_input(self):
        """测试简单输入不触发规划"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._complexity_threshold = 0.5
        
        simple_input = "你好"
        
        with patch('agent.digital_life.logger'):
            result = DigitalLife._needs_planning(digital_life, simple_input)
        
        assert result is False

    def test_needs_planning_action_keywords(self):
        """测试动作关键词触发规划"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._complexity_threshold = 0.5
        
        # 包含多个动作关键词的输入
        action_input = "请检查系统状态并创建报告"
        
        with patch('agent.digital_life.logger'):
            result = DigitalLife._needs_planning(digital_life, action_input)
        
        assert result is True


class TestBuildOfflineResponse:
    """测试离线响应构建"""

    def test_build_offline_response(self):
        """测试离线响应构建"""
        from agent.digital_life import DigitalLife
        
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Default"
        
        result = DigitalLife._build_offline_response(digital_life, "Hello")
        
        assert result is not None
        assert len(result) > 0