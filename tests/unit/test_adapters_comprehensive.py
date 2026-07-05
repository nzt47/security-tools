"""ModelAdapter 综合单元测试

覆盖模块: agent/model_router/adapters.py
测试维度: 抽象基类 / OpenAI / Claude / Gemini / Zhipu 适配器
设计原则: AAA (Arrange-Act-Assert), Mock 外部 SDK, 不实际调用 API
"""

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from agent.model_router.adapters import (
    ModelAdapter,
    OpenAIAdapter,
    ClaudeAdapter,
    GeminiAdapter,
    ZhipuAdapter,
    _trace_id,
)


# ═══════════════════════════════════════════════════════════════
# _trace_id 辅助函数
# ═══════════════════════════════════════════════════════════════


class TestTraceId:
    """_trace_id 函数测试"""

    def test_returns_string(self):
        tid = _trace_id()
        assert isinstance(tid, str)
        assert len(tid) > 0

    def test_returns_hex(self):
        tid = _trace_id()
        assert all(c in "0123456789abcdef" for c in tid)

    def test_unique(self):
        ids = {_trace_id() for _ in range(20)}
        assert len(ids) == 20


# ═══════════════════════════════════════════════════════════════
# ModelAdapter 抽象基类
# ═══════════════════════════════════════════════════════════════


class TestModelAdapterABC:
    """ModelAdapter 抽象基类测试"""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            ModelAdapter()

    def test_has_abstract_methods(self):
        # 应该有 6 个抽象方法
        abstract_methods = {
            "get_provider_name", "get_model_name", "get_cost_per_token",
            "generate", "chat", "is_available"
        }
        assert abstract_methods.issubset(ModelAdapter.__abstractmethods__)


# ═══════════════════════════════════════════════════════════════
# OpenAIAdapter
# ═══════════════════════════════════════════════════════════════


class TestOpenAIAdapterInit:
    """OpenAIAdapter 初始化测试"""

    def test_init_basic(self):
        adapter = OpenAIAdapter("gpt-4")
        assert adapter._model_name == "gpt-4"
        assert adapter._api_key is None
        assert adapter._base_url is None
        assert adapter._client is None

    def test_init_with_api_key(self):
        adapter = OpenAIAdapter("gpt-4", api_key="sk-test")
        assert adapter._api_key == "sk-test"

    def test_init_with_base_url(self):
        adapter = OpenAIAdapter("gpt-4", base_url="https://api.example.com")
        assert adapter._base_url == "https://api.example.com"


class TestOpenAIAdapterGetters:
    """OpenAIAdapter getter 方法测试"""

    def test_get_provider_name(self):
        adapter = OpenAIAdapter("gpt-4")
        assert adapter.get_provider_name() == "openai"

    def test_get_model_name(self):
        adapter = OpenAIAdapter("gpt-4")
        assert adapter.get_model_name() == "gpt-4"

    def test_get_cost_known_model(self):
        adapter = OpenAIAdapter("gpt-4")
        costs = adapter.get_cost_per_token()
        assert "prompt" in costs
        assert "completion" in costs
        assert costs["prompt"] == 0.03

    def test_get_cost_unknown_model(self):
        adapter = OpenAIAdapter("unknown-model")
        costs = adapter.get_cost_per_token()
        # 未知模型应返回默认值
        assert costs["prompt"] == 0.0015
        assert costs["completion"] == 0.002

    def test_get_cost_gpt_3_5(self):
        adapter = OpenAIAdapter("gpt-3.5-turbo")
        costs = adapter.get_cost_per_token()
        assert costs["prompt"] == 0.0015
        assert costs["completion"] == 0.002

    def test_get_cost_gpt_4o_mini(self):
        adapter = OpenAIAdapter("gpt-4o-mini")
        costs = adapter.get_cost_per_token()
        assert costs["prompt"] == 0.0015
        assert costs["completion"] == 0.006


class TestOpenAIAdapterGetClient:
    """OpenAIAdapter._get_client 测试"""

    def test_get_client_returns_none_when_no_lib(self):
        adapter = OpenAIAdapter("gpt-4")
        # openai 库未安装或导入失败时返回 None
        with mock.patch("builtins.__import__", side_effect=ImportError("no openai")):
            client = adapter._get_client()
            assert client is None

    def test_get_client_caches(self):
        adapter = OpenAIAdapter("gpt-4")
        # 模拟 openai 库
        mock_openai = mock.MagicMock()
        mock_client = mock.MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        with mock.patch("builtins.__import__") as mock_import:
            mock_import.return_value = mock_openai
            client1 = adapter._get_client()
            client2 = adapter._get_client()
            # 第二次应使用缓存
            assert client1 is client2


class TestOpenAIAdapterGenerate:
    """OpenAIAdapter.generate 测试"""

    def test_generate_no_client(self):
        adapter = OpenAIAdapter("gpt-4")
        # 强制 client 为 None
        adapter._client = None
        with mock.patch.object(adapter, "_get_client", return_value=None):
            result = adapter.generate("hello")
            assert "error" in result
            assert "not available" in result["error"]

    def test_generate_success(self):
        adapter = OpenAIAdapter("gpt-4")
        # 构造 mock 响应
        mock_response = SimpleNamespace()
        mock_response.choices = [
            SimpleNamespace(message=SimpleNamespace(content="Hello back"))
        ]
        mock_response.usage = SimpleNamespace(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        )
        mock_client = mock.MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        adapter._client = mock_client

        result = adapter.generate("hello")
        assert result["success"] is True
        assert result["content"] == "Hello back"
        assert result["usage"]["total_tokens"] == 15
        assert result["model"] == "gpt-4"
        assert result["provider"] == "openai"

    def test_generate_api_error(self):
        adapter = OpenAIAdapter("gpt-4")
        mock_client = mock.MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")
        adapter._client = mock_client

        result = adapter.generate("hello")
        assert result["success"] is False
        assert "API error" in result["error"]

    def test_generate_filters_reserved_kwargs(self):
        adapter = OpenAIAdapter("gpt-4")
        mock_response = SimpleNamespace()
        mock_response.choices = [
            SimpleNamespace(message=SimpleNamespace(content="ok"))
        ]
        mock_response.usage = SimpleNamespace(
            prompt_tokens=5, completion_tokens=2, total_tokens=7
        )
        mock_client = mock.MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        adapter._client = mock_client

        # 传入 model 和 messages（应被过滤掉）
        adapter.generate("hello", model="wrong", messages=[])
        # 验证调用参数中不包含 reserved
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        # model 和 messages 应被显式设置，不应在额外 kwargs 中
        assert call_kwargs["model"] == "gpt-4"


class TestOpenAIAdapterChat:
    """OpenAIAdapter.chat 测试"""

    def test_chat_no_client(self):
        adapter = OpenAIAdapter("gpt-4")
        with mock.patch.object(adapter, "_get_client", return_value=None):
            result = adapter.chat([{"role": "user", "content": "hi"}])
            assert "error" in result

    def test_chat_success(self):
        adapter = OpenAIAdapter("gpt-4")
        mock_response = SimpleNamespace()
        mock_response.choices = [
            SimpleNamespace(message=SimpleNamespace(content="chat response"))
        ]
        mock_response.usage = SimpleNamespace(
            prompt_tokens=8, completion_tokens=3, total_tokens=11
        )
        mock_client = mock.MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        adapter._client = mock_client

        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hi"},
        ]
        result = adapter.chat(messages)
        assert result["success"] is True
        assert result["content"] == "chat response"

    def test_chat_api_error(self):
        adapter = OpenAIAdapter("gpt-4")
        mock_client = mock.MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("chat error")
        adapter._client = mock_client

        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert result["success"] is False


class TestOpenAIAdapterIsAvailable:
    """OpenAIAdapter.is_available 测试"""

    def test_available_with_client(self):
        adapter = OpenAIAdapter("gpt-4")
        adapter._client = mock.MagicMock()
        assert adapter.is_available() is True

    def test_not_available_without_client(self):
        adapter = OpenAIAdapter("gpt-4")
        with mock.patch.object(adapter, "_get_client", return_value=None):
            assert adapter.is_available() is False

    def test_available_handles_exception(self):
        adapter = OpenAIAdapter("gpt-4")
        with mock.patch.object(adapter, "_get_client", side_effect=Exception("err")):
            assert adapter.is_available() is False


# ═══════════════════════════════════════════════════════════════
# ClaudeAdapter
# ═══════════════════════════════════════════════════════════════


class TestClaudeAdapterInit:
    """ClaudeAdapter 初始化测试"""

    def test_init_basic(self):
        adapter = ClaudeAdapter("claude-3-sonnet")
        assert adapter._model_name == "claude-3-sonnet"
        assert adapter._api_key is None
        assert adapter._client is None

    def test_init_with_api_key(self):
        adapter = ClaudeAdapter("claude-3-sonnet", api_key="sk-ant-test")
        assert adapter._api_key == "sk-ant-test"


class TestClaudeAdapterGetters:
    """ClaudeAdapter getter 方法测试"""

    def test_get_provider_name(self):
        adapter = ClaudeAdapter("claude-3-sonnet")
        assert adapter.get_provider_name() == "claude"

    def test_get_model_name(self):
        adapter = ClaudeAdapter("claude-3-sonnet")
        assert adapter.get_model_name() == "claude-3-sonnet"

    def test_get_cost_known_model(self):
        adapter = ClaudeAdapter("claude-3-opus")
        costs = adapter.get_cost_per_token()
        assert costs["prompt"] == 0.0015
        assert costs["completion"] == 0.006

    def test_get_cost_unknown_model(self):
        adapter = ClaudeAdapter("unknown-claude")
        costs = adapter.get_cost_per_token()
        assert costs["prompt"] == 0.00075
        assert costs["completion"] == 0.003

    def test_get_cost_haiku(self):
        adapter = ClaudeAdapter("claude-3-haiku")
        costs = adapter.get_cost_per_token()
        assert costs["prompt"] == 0.00025
        assert costs["completion"] == 0.00125


class TestClaudeAdapterGetClient:
    """ClaudeAdapter._get_client 测试"""

    def test_get_client_returns_none_when_no_lib(self):
        adapter = ClaudeAdapter("claude-3-sonnet")
        with mock.patch("builtins.__import__", side_effect=ImportError("no anthropic")):
            client = adapter._get_client()
            assert client is None

    def test_get_client_caches(self):
        adapter = ClaudeAdapter("claude-3-sonnet")
        mock_anthropic = mock.MagicMock()
        mock_client = mock.MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        with mock.patch("builtins.__import__") as mock_import:
            mock_import.return_value = mock_anthropic
            client1 = adapter._get_client()
            client2 = adapter._get_client()
            assert client1 is client2


class TestClaudeAdapterGenerate:
    """ClaudeAdapter.generate 测试"""

    def test_generate_no_client(self):
        adapter = ClaudeAdapter("claude-3-sonnet")
        with mock.patch.object(adapter, "_get_client", return_value=None):
            result = adapter.generate("hello")
            assert "error" in result

    def test_generate_success(self):
        adapter = ClaudeAdapter("claude-3-sonnet")
        # 构造 mock 响应
        mock_response = SimpleNamespace()
        mock_response.content = [SimpleNamespace(text="Claude response")]
        mock_response.usage = SimpleNamespace(
            input_tokens=10, output_tokens=5
        )
        mock_client = mock.MagicMock()
        mock_client.messages.create.return_value = mock_response
        adapter._client = mock_client

        result = adapter.generate("hello")
        assert result["success"] is True
        assert result["content"] == "Claude response"
        assert result["usage"]["total_tokens"] == 15
        assert result["provider"] == "claude"

    def test_generate_api_error(self):
        adapter = ClaudeAdapter("claude-3-sonnet")
        mock_client = mock.MagicMock()
        mock_client.messages.create.side_effect = Exception("Claude error")
        adapter._client = mock_client

        result = adapter.generate("hello")
        assert result["success"] is False


class TestClaudeAdapterChat:
    """ClaudeAdapter.chat 测试"""

    def test_chat_no_client(self):
        adapter = ClaudeAdapter("claude-3-sonnet")
        with mock.patch.object(adapter, "_get_client", return_value=None):
            result = adapter.chat([{"role": "user", "content": "hi"}])
            assert "error" in result

    def test_chat_success(self):
        adapter = ClaudeAdapter("claude-3-sonnet")
        mock_response = SimpleNamespace()
        mock_response.content = [SimpleNamespace(text="chat reply")]
        mock_response.usage = SimpleNamespace(input_tokens=5, output_tokens=3)
        mock_client = mock.MagicMock()
        mock_client.messages.create.return_value = mock_response
        adapter._client = mock_client

        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert result["success"] is True
        assert result["content"] == "chat reply"


class TestClaudeAdapterIsAvailable:
    """ClaudeAdapter.is_available 测试"""

    def test_available_with_client(self):
        adapter = ClaudeAdapter("claude-3-sonnet")
        adapter._client = mock.MagicMock()
        assert adapter.is_available() is True

    def test_not_available_without_client(self):
        adapter = ClaudeAdapter("claude-3-sonnet")
        with mock.patch.object(adapter, "_get_client", return_value=None):
            assert adapter.is_available() is False


# ═══════════════════════════════════════════════════════════════
# GeminiAdapter
# ═══════════════════════════════════════════════════════════════


class TestGeminiAdapterInit:
    """GeminiAdapter 初始化测试"""

    def test_init_basic(self):
        adapter = GeminiAdapter("gemini-1.5-pro")
        assert adapter._model_name == "gemini-1.5-pro"
        assert adapter._api_key is None
        assert adapter._client is None


class TestGeminiAdapterGetters:
    """GeminiAdapter getter 方法测试"""

    def test_get_provider_name(self):
        adapter = GeminiAdapter("gemini-1.5-pro")
        assert adapter.get_provider_name() == "gemini"

    def test_get_model_name(self):
        adapter = GeminiAdapter("gemini-1.5-pro")
        assert adapter.get_model_name() == "gemini-1.5-pro"

    def test_get_cost_known_model(self):
        adapter = GeminiAdapter("gemini-1.5-pro")
        costs = adapter.get_cost_per_token()
        assert costs["prompt"] == 0.001

    def test_get_cost_unknown_model(self):
        adapter = GeminiAdapter("unknown-gemini")
        costs = adapter.get_cost_per_token()
        assert costs["prompt"] == 0.000125


class TestGeminiAdapterGenerate:
    """GeminiAdapter.generate 测试"""

    def test_generate_no_client(self):
        adapter = GeminiAdapter("gemini-1.5-pro")
        with mock.patch.object(adapter, "_get_client", return_value=None):
            result = adapter.generate("hello")
            assert "error" in result

    def test_generate_success(self):
        adapter = GeminiAdapter("gemini-1.5-pro")
        mock_response = SimpleNamespace()
        mock_response.text = "Gemini response"
        mock_response.usage_metadata = SimpleNamespace(
            prompt_token_count=8,
            candidates_token_count=4,
            total_token_count=12,
        )
        mock_client = mock.MagicMock()
        mock_client.generate_content.return_value = mock_response
        adapter._client = mock_client

        result = adapter.generate("hello")
        assert result["success"] is True
        assert result["content"] == "Gemini response"
        assert result["usage"]["total_tokens"] == 12

    def test_generate_api_error(self):
        adapter = GeminiAdapter("gemini-1.5-pro")
        mock_client = mock.MagicMock()
        mock_client.generate_content.side_effect = Exception("Gemini error")
        adapter._client = mock_client

        result = adapter.generate("hello")
        assert result["success"] is False


class TestGeminiAdapterChat:
    """GeminiAdapter.chat 测试"""

    def test_chat_no_client(self):
        adapter = GeminiAdapter("gemini-1.5-pro")
        with mock.patch.object(adapter, "_get_client", return_value=None):
            result = adapter.chat([{"role": "user", "content": "hi"}])
            assert "error" in result

    def test_chat_success(self):
        adapter = GeminiAdapter("gemini-1.5-pro")
        mock_chat = mock.MagicMock()
        mock_response = SimpleNamespace()
        mock_response.text = "chat reply"
        mock_chat.send_message.return_value = mock_response
        mock_client = mock.MagicMock()
        mock_client.start_chat.return_value = mock_chat
        adapter._client = mock_client

        messages = [{"role": "user", "content": "hi"}]
        result = adapter.chat(messages)
        assert result["success"] is True
        assert result["content"] == "chat reply"


class TestGeminiAdapterIsAvailable:
    """GeminiAdapter.is_available 测试"""

    def test_available_with_client(self):
        adapter = GeminiAdapter("gemini-1.5-pro")
        adapter._client = mock.MagicMock()
        assert adapter.is_available() is True

    def test_not_available_without_client(self):
        adapter = GeminiAdapter("gemini-1.5-pro")
        with mock.patch.object(adapter, "_get_client", return_value=None):
            assert adapter.is_available() is False


# ═══════════════════════════════════════════════════════════════
# ZhipuAdapter
# ═══════════════════════════════════════════════════════════════


class TestZhipuAdapterInit:
    """ZhipuAdapter 初始化测试"""

    def test_init_basic(self):
        adapter = ZhipuAdapter("glm-4")
        assert adapter._model_name == "glm-4"
        assert adapter._api_key is None
        assert adapter._client is None


class TestZhipuAdapterGetters:
    """ZhipuAdapter getter 方法测试"""

    def test_get_provider_name(self):
        adapter = ZhipuAdapter("glm-4")
        assert adapter.get_provider_name() == "zhipu"

    def test_get_model_name(self):
        adapter = ZhipuAdapter("glm-4")
        assert adapter.get_model_name() == "glm-4"

    def test_get_cost_known_model(self):
        adapter = ZhipuAdapter("glm-4")
        costs = adapter.get_cost_per_token()
        assert costs["prompt"] == 0.002

    def test_get_cost_unknown_model(self):
        adapter = ZhipuAdapter("unknown-glm")
        costs = adapter.get_cost_per_token()
        assert costs["prompt"] == 0.002

    def test_get_cost_glm_3_turbo(self):
        adapter = ZhipuAdapter("glm-3-turbo")
        costs = adapter.get_cost_per_token()
        assert costs["prompt"] == 0.0005


class TestZhipuAdapterGetClient:
    """ZhipuAdapter._get_client 测试"""

    def test_get_client_returns_none_when_no_lib(self):
        adapter = ZhipuAdapter("glm-4")
        with mock.patch("builtins.__import__", side_effect=ImportError("no zhipuai")):
            client = adapter._get_client()
            assert client is None


class TestZhipuAdapterGenerate:
    """ZhipuAdapter.generate 测试"""

    def test_generate_no_client(self):
        adapter = ZhipuAdapter("glm-4")
        with mock.patch.object(adapter, "_get_client", return_value=None):
            result = adapter.generate("hello")
            assert "error" in result

    def test_generate_success(self):
        adapter = ZhipuAdapter("glm-4")
        mock_response = SimpleNamespace()
        mock_response.choices = [
            SimpleNamespace(message=SimpleNamespace(content="GLM response"))
        ]
        mock_response.usage = SimpleNamespace(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        )
        mock_client = mock.MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        adapter._client = mock_client

        result = adapter.generate("hello")
        assert result["success"] is True
        assert result["content"] == "GLM response"

    def test_generate_api_error(self):
        adapter = ZhipuAdapter("glm-4")
        mock_client = mock.MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("GLM error")
        adapter._client = mock_client

        result = adapter.generate("hello")
        assert result["success"] is False


class TestZhipuAdapterChat:
    """ZhipuAdapter.chat 测试"""

    def test_chat_no_client(self):
        adapter = ZhipuAdapter("glm-4")
        with mock.patch.object(adapter, "_get_client", return_value=None):
            result = adapter.chat([{"role": "user", "content": "hi"}])
            assert "error" in result

    def test_chat_success(self):
        adapter = ZhipuAdapter("glm-4")
        mock_response = SimpleNamespace()
        mock_response.choices = [
            SimpleNamespace(message=SimpleNamespace(content="chat reply"))
        ]
        mock_response.usage = SimpleNamespace(
            prompt_tokens=5, completion_tokens=3, total_tokens=8
        )
        mock_client = mock.MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        adapter._client = mock_client

        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert result["success"] is True
        assert result["content"] == "chat reply"


class TestZhipuAdapterIsAvailable:
    """ZhipuAdapter.is_available 测试"""

    def test_available_with_client(self):
        adapter = ZhipuAdapter("glm-4")
        adapter._client = mock.MagicMock()
        assert adapter.is_available() is True

    def test_not_available_without_client(self):
        adapter = ZhipuAdapter("glm-4")
        with mock.patch.object(adapter, "_get_client", return_value=None):
            assert adapter.is_available() is False


# ═══════════════════════════════════════════════════════════════
# 跨适配器一致性测试
# ═══════════════════════════════════════════════════════════════


class TestAdapterConsistency:
    """所有适配器接口一致性测试"""

    @pytest.mark.parametrize("adapter_class,model_name", [
        (OpenAIAdapter, "gpt-4"),
        (ClaudeAdapter, "claude-3-sonnet"),
        (GeminiAdapter, "gemini-1.5-pro"),
        (ZhipuAdapter, "glm-4"),
    ])
    def test_all_adapters_have_same_interface(self, adapter_class, model_name):
        adapter = adapter_class(model_name)
        # 所有适配器应实现所有抽象方法
        assert callable(adapter.get_provider_name)
        assert callable(adapter.get_model_name)
        assert callable(adapter.get_cost_per_token)
        assert callable(adapter.generate)
        assert callable(adapter.chat)
        assert callable(adapter.is_available)

    @pytest.mark.parametrize("adapter_class,model_name", [
        (OpenAIAdapter, "gpt-4"),
        (ClaudeAdapter, "claude-3-sonnet"),
        (GeminiAdapter, "gemini-1.5-pro"),
        (ZhipuAdapter, "glm-4"),
    ])
    def test_all_adapters_return_dict(self, adapter_class, model_name):
        adapter = adapter_class(model_name)
        # 强制 client 为 None
        with mock.patch.object(adapter, "_get_client", return_value=None):
            gen_result = adapter.generate("test")
            chat_result = adapter.chat([{"role": "user", "content": "test"}])
            assert isinstance(gen_result, dict)
            assert isinstance(chat_result, dict)

    @pytest.mark.parametrize("adapter_class,model_name", [
        (OpenAIAdapter, "gpt-4"),
        (ClaudeAdapter, "claude-3-sonnet"),
        (GeminiAdapter, "gemini-1.5-pro"),
        (ZhipuAdapter, "glm-4"),
    ])
    def test_provider_name_is_string(self, adapter_class, model_name):
        adapter = adapter_class(model_name)
        name = adapter.get_provider_name()
        assert isinstance(name, str)
        assert len(name) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
