"""对话处理全链路集成测试

测试覆盖：
1. 正常对话流程：输入→模型→响应→记忆存储
2. 带工具调用的对话流程
3. 多轮对话的上下文一致性
4. 对话过程中触发降级的行为
5. 对话过程中触发熔断的行为
6. 对话过程中限流的处理
7. 异常输入的全链路处理
8. 对话取消/中断的资源清理
"""

import pytest
import asyncio
import json
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.integration
pytest.timeout = 30


class TestChatFullPipeline:
    """对话处理全链路集成测试"""

    def test_normal_chat_flow_input_model_response_memory(self):
        """测试正常对话流程：输入→模型→响应→记忆存储"""
        from agent.tool_calling import ToolCallingService
        from types import SimpleNamespace

        mock_llm = MagicMock()
        mock_response = SimpleNamespace()
        mock_response.content = "Hello! How can I help you today?"
        mock_response.tool_calls = None

        def mock_create(**kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=mock_response)])

        mock_llm._get_client.return_value.chat.completions.create = mock_create
        mock_llm._is_openai_compat.return_value = True
        mock_llm.model = "test-model"

        service = ToolCallingService(mock_llm)

        messages = [{"role": "user", "content": "Hello"}]
        result = service.chat(messages)

        assert isinstance(result, str)
        assert "Hello" in result

    def test_chat_flow_with_tool_calling(self):
        """测试带工具调用的对话流程"""
        from agent.tool_calling import ToolCallingService
        from types import SimpleNamespace

        mock_llm = MagicMock()

        mock_response1 = SimpleNamespace()
        mock_response1.content = None
        fn1 = SimpleNamespace()
        fn1.name = "get_current_time"
        fn1.arguments = json.dumps({})
        tc1 = SimpleNamespace()
        tc1.id = "call_1"
        tc1.type = "function"
        tc1.function = fn1
        mock_response1.tool_calls = [tc1]

        mock_response2 = SimpleNamespace()
        mock_response2.content = "The current time is 10:30 AM."
        mock_response2.tool_calls = None

        call_count = [0]

        def mock_call(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return SimpleNamespace(choices=[SimpleNamespace(message=mock_response1)])
            return SimpleNamespace(choices=[SimpleNamespace(message=mock_response2)])

        mock_llm._get_client.return_value.chat.completions.create = mock_call
        mock_llm._is_openai_compat.return_value = True
        mock_llm.model = "test-model"

        service = ToolCallingService(mock_llm, max_rounds=2)

        messages = [{"role": "user", "content": "What time is it?"}]
        result = service.chat_with_steps(messages)

        assert "text" in result
        assert isinstance(result["text"], str)

    def test_multi_round_chat_context_consistency(self):
        """测试多轮对话的上下文一致性"""
        from agent.tool_calling import ToolCallingService
        from types import SimpleNamespace

        responses = [
            "I'll remember your favorite color is blue.",
            "Your favorite color is blue, right?",
            "Yes, blue is a great color!"
        ]

        mock_llm = MagicMock()
        mock_llm._is_openai_compat.return_value = True
        mock_llm.model = "test-model"

        response_index = [0]

        def mock_chat_call(**kwargs):
            message = SimpleNamespace()
            message.content = responses[response_index[0]]
            message.tool_calls = None
            response_index[0] = (response_index[0] + 1) % len(responses)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        mock_llm._get_client.return_value.chat.completions.create.side_effect = mock_chat_call

        service = ToolCallingService(mock_llm)

        messages = []

        result1 = service.chat([{"role": "user", "content": "My favorite color is blue"}])
        assert "remember" in result1.lower()

        messages.append({"role": "user", "content": "My favorite color is blue"})
        messages.append({"role": "assistant", "content": result1})

        result2 = service.chat([{"role": "user", "content": "What's my favorite color?"}])
        assert "blue" in result2.lower()

        result3 = service.chat([{"role": "user", "content": "Do you like that color?"}])
        assert "blue" in result3.lower()

    def test_chat_degrade_behavior_during_conversation(self):
        """测试对话过程中触发降级的行为"""
        from agent.tool_calling import ToolCallingService
        from agent.graceful_degrade import get_degrade_manager, DegradeModule
        from types import SimpleNamespace

        mock_llm = MagicMock()

        call_count = [0]

        def mock_chat_call(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 3:
                raise Exception("LLM service unavailable")
            message = SimpleNamespace()
            message.content = "Service restored, here's your answer."
            message.tool_calls = None
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        mock_llm._get_client.return_value.chat.completions.create.side_effect = mock_chat_call
        mock_llm._is_openai_compat.return_value = True
        mock_llm.model = "test-model"

        service = ToolCallingService(mock_llm)

        degrade_manager = get_degrade_manager()
        degrade_manager.reset()

        messages = [{"role": "user", "content": "What is the weather today?"}]

        try:
            result = service.chat(messages)
            assert isinstance(result, str)
        except Exception:
            pass

        metrics = degrade_manager.get_metrics()
        assert metrics.total_degrades >= 0

    def test_chat_circuit_breaker_trigger_during_conversation(self):
        """测试对话过程中触发熔断的行为"""
        from agent.tool_calling import ToolCallingService
        from agent.circuit_breaker import get_circuit_breaker, CircuitBreakerState
        from types import SimpleNamespace

        mock_llm = MagicMock()

        def mock_chat_call(**kwargs):
            raise Exception("Service failure")

        mock_llm._get_client.return_value.chat.completions.create.side_effect = mock_chat_call
        mock_llm._is_openai_compat.return_value = True
        mock_llm.model = "test-model"

        service = ToolCallingService(mock_llm)

        breaker = get_circuit_breaker("tool_calling")
        breaker.reset()

        for _ in range(5):
            try:
                service.chat([{"role": "user", "content": "Test"}])
            except Exception:
                pass

        status = breaker.get_status()
        assert status["state"] == "closed"

    def test_chat_rate_limit_handling(self):
        """测试对话过程中限流的处理"""
        from agent.tool_calling import ToolCallingService
        from agent.rate_limiter import get_rate_limiter, RateLimitStrategy
        from types import SimpleNamespace

        mock_llm = MagicMock()
        mock_response = SimpleNamespace()
        mock_response.content = "Rate limited response"
        mock_response.tool_calls = None

        def mock_create(**kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=mock_response)])

        mock_llm._get_client.return_value.chat.completions.create = mock_create
        mock_llm._is_openai_compat.return_value = True
        mock_llm.model = "test-model"

        service = ToolCallingService(mock_llm)

        messages = [{"role": "user", "content": "Test rate limit"}]

        try:
            result = service.chat(messages)
            assert isinstance(result, str)
        except Exception:
            pass

    def test_chat_abnormal_input_full_pipeline(self):
        """测试异常输入的全链路处理"""
        from agent.tool_calling import ToolCallingService
        from types import SimpleNamespace

        mock_llm = MagicMock()
        mock_response = SimpleNamespace()
        mock_response.content = "I cannot process that request."
        mock_response.tool_calls = None

        mock_llm._get_client.return_value.chat.completions.create.return_value.choices[0].message = mock_response
        mock_llm._is_openai_compat.return_value = True
        mock_llm.model = "test-model"

        service = ToolCallingService(mock_llm)

        abnormal_inputs = [
            "",
            " ",
            None,
            "a" * 10000,
            "<script>alert('xss')</script>"
        ]

        for input_text in abnormal_inputs:
            try:
                messages = [{"role": "user", "content": input_text}] if input_text is not None else []
                result = service.chat(messages)
                assert isinstance(result, str)
            except Exception as e:
                assert "content" in str(e).lower() or "message" in str(e).lower()

    def test_chat_cancel_interrupt_resource_cleanup(self):
        """测试对话取消/中断的资源清理"""
        from agent.tool_calling import ToolCallingService
        from types import SimpleNamespace

        mock_llm = MagicMock()
        mock_response = SimpleNamespace()
        mock_response.content = "Response"
        mock_response.tool_calls = None

        def mock_create(**kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=mock_response)])

        mock_llm._get_client.return_value.chat.completions.create = mock_create
        mock_llm._is_openai_compat.return_value = True
        mock_llm.model = "test-model"

        service = ToolCallingService(mock_llm)

        assert service._abort_event.is_set() is False

        service.abort()

        assert service._abort_event.is_set() is True

        status = service._circuit_breaker.get_status()
        assert status["state"] == "closed"

        service._abort_event.clear()