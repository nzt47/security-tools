"""ModelAdapter 集成测试

覆盖 agent.model_router.adapters 模块：
- ModelAdapter ABC 基类
- OpenAIAdapter（generate/chat/is_available/cost）
- ClaudeAdapter（generate/chat/is_available/cost）
- GeminiAdapter（generate/chat/is_available/cost）
- ZhipuAdapter（generate/chat/is_available/cost）
- QwenAdapter（generate/chat/is_available/cost）
- ModelAdapterFactory 工厂模式
"""

from unittest.mock import MagicMock, patch

import pytest

from agent.model_router.adapters import (
    ModelAdapter,
    OpenAIAdapter,
    ClaudeAdapter,
    GeminiAdapter,
    ZhipuAdapter,
    QwenAdapter,
    ModelAdapterFactory,
)


# ============================================================================
# ModelAdapter ABC 测试
# ============================================================================

class TestModelAdapterABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            ModelAdapter()


# ============================================================================
# 辅助函数
# ============================================================================

def make_openai_response(content="hello", prompt_tokens=10, completion_tokens=5):
    """构造 OpenAI 风格响应"""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage.prompt_tokens = prompt_tokens
    resp.usage.completion_tokens = completion_tokens
    resp.usage.total_tokens = prompt_tokens + completion_tokens
    return resp


def make_claude_response(content="hello", input_tokens=10, output_tokens=5):
    """构造 Claude 风格响应"""
    resp = MagicMock()
    resp.content = [MagicMock()]
    resp.content[0].text = content
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    return resp


def make_gemini_response(content="hello", prompt=10, completion=5, total=15):
    """构造 Gemini 风格响应"""
    resp = MagicMock()
    resp.text = content
    resp.usage_metadata.prompt_token_count = prompt
    resp.usage_metadata.candidates_token_count = completion
    resp.usage_metadata.total_token_count = total
    return resp


def make_zhipu_response(content="hello", prompt_tokens=10, completion_tokens=5):
    """构造智谱风格响应（与 OpenAI 结构相同）"""
    return make_openai_response(content, prompt_tokens, completion_tokens)


def make_qwen_response(content="hello"):
    """构造通义千问风格响应"""
    resp = MagicMock()
    resp.body.output.choices = [MagicMock()]
    resp.body.output.choices[0].message.content = content
    return resp


# ============================================================================
# OpenAIAdapter 测试
# ============================================================================

class TestOpenAIAdapter:
    def test_init(self):
        adapter = OpenAIAdapter("gpt-4", api_key="key", base_url="http://test")
        assert adapter._model_name == "gpt-4"
        assert adapter._api_key == "key"
        assert adapter._base_url == "http://test"
        assert adapter._client is None

    def test_get_provider_name(self):
        assert OpenAIAdapter("gpt-4").get_provider_name() == "openai"

    def test_get_model_name(self):
        assert OpenAIAdapter("gpt-4").get_model_name() == "gpt-4"

    def test_cost_known_model(self):
        cost = OpenAIAdapter("gpt-4").get_cost_per_token()
        assert cost == {"prompt": 0.03, "completion": 0.06}

    def test_cost_gpt35(self):
        cost = OpenAIAdapter("gpt-3.5-turbo").get_cost_per_token()
        assert cost == {"prompt": 0.0015, "completion": 0.002}

    def test_cost_gpt4o(self):
        cost = OpenAIAdapter("gpt-4o").get_cost_per_token()
        assert cost == {"prompt": 0.005, "completion": 0.015}

    def test_cost_gpt4o_mini(self):
        cost = OpenAIAdapter("gpt-4o-mini").get_cost_per_token()
        assert cost == {"prompt": 0.0015, "completion": 0.006}

    def test_cost_unknown_model(self):
        cost = OpenAIAdapter("unknown").get_cost_per_token()
        assert cost == {"prompt": 0.0015, "completion": 0.002}

    def test_is_available_no_client(self):
        adapter = OpenAIAdapter("gpt-4")
        # 模拟 SDK 不可用
        with patch.object(adapter, "_get_client", return_value=None):
            assert adapter.is_available() is False

    def test_is_available_with_client(self):
        adapter = OpenAIAdapter("gpt-4")
        adapter._client = MagicMock()
        assert adapter.is_available() is True

    def test_generate_no_client(self):
        adapter = OpenAIAdapter("gpt-4")
        with patch.object(adapter, "_get_client", return_value=None):
            result = adapter.generate("hello")
        assert "error" in result
        assert "not available" in result["error"]

    def test_generate_success(self):
        adapter = OpenAIAdapter("gpt-4")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = make_openai_response("hi")
        adapter._client = mock_client
        result = adapter.generate("hello")
        assert result["success"] is True
        assert result["content"] == "hi"
        assert result["model"] == "gpt-4"
        assert result["provider"] == "openai"
        assert result["usage"]["total_tokens"] == 15

    def test_generate_filters_reserved_kwargs(self):
        adapter = OpenAIAdapter("gpt-4")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = make_openai_response()
        adapter._client = mock_client
        adapter.generate("hello", model="should-be-ignored", messages=["should-be-ignored"], temperature=0.7)
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4"
        assert call_kwargs["messages"] == [{"role": "user", "content": "hello"}]
        assert "temperature" in call_kwargs
        assert "model" not in {k for k, v in call_kwargs.items() if v == "should-be-ignored"}

    def test_generate_exception(self):
        adapter = OpenAIAdapter("gpt-4")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")
        adapter._client = mock_client
        result = adapter.generate("hello")
        assert result["success"] is False
        assert "API error" in result["error"]

    def test_chat_no_client(self):
        adapter = OpenAIAdapter("gpt-4")
        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert "error" in result

    def test_chat_success(self):
        adapter = OpenAIAdapter("gpt-4")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = make_openai_response("response")
        adapter._client = mock_client
        messages = [{"role": "user", "content": "hi"}]
        result = adapter.chat(messages)
        assert result["success"] is True
        assert result["content"] == "response"

    def test_chat_exception(self):
        adapter = OpenAIAdapter("gpt-4")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("chat error")
        adapter._client = mock_client
        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert result["success"] is False

    @patch("builtins.__import__", side_effect=ImportError("no openai"))
    def test_get_client_import_error(self, mock_import):
        adapter = OpenAIAdapter("gpt-4")
        client = adapter._get_client()
        assert client is None


# ============================================================================
# ClaudeAdapter 测试
# ============================================================================

class TestClaudeAdapter:
    def test_init(self):
        adapter = ClaudeAdapter("claude-3-sonnet", api_key="key")
        assert adapter._model_name == "claude-3-sonnet"
        assert adapter._api_key == "key"
        assert adapter._client is None

    def test_get_provider_name(self):
        assert ClaudeAdapter("claude-3").get_provider_name() == "claude"

    def test_get_model_name(self):
        assert ClaudeAdapter("claude-3").get_model_name() == "claude-3"

    def test_cost_known_models(self):
        assert ClaudeAdapter("claude-3-haiku").get_cost_per_token() == {"prompt": 0.00025, "completion": 0.00125}
        assert ClaudeAdapter("claude-3-sonnet").get_cost_per_token() == {"prompt": 0.00075, "completion": 0.003}
        assert ClaudeAdapter("claude-3-opus").get_cost_per_token() == {"prompt": 0.0015, "completion": 0.006}

    def test_cost_unknown_model(self):
        cost = ClaudeAdapter("unknown").get_cost_per_token()
        assert cost == {"prompt": 0.00075, "completion": 0.003}

    def test_is_available_no_client(self):
        adapter = ClaudeAdapter("claude-3")
        with patch.object(adapter, "_get_client", return_value=None):
            assert adapter.is_available() is False

    def test_is_available_with_client(self):
        adapter = ClaudeAdapter("claude-3")
        adapter._client = MagicMock()
        assert adapter.is_available() is True

    def test_generate_no_client(self):
        adapter = ClaudeAdapter("claude-3")
        with patch.object(adapter, "_get_client", return_value=None):
            result = adapter.generate("hello")
        assert "error" in result

    def test_generate_success(self):
        adapter = ClaudeAdapter("claude-3-sonnet")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = make_claude_response("hi")
        adapter._client = mock_client
        result = adapter.generate("hello", max_tokens=512)
        assert result["success"] is True
        assert result["content"] == "hi"
        assert result["model"] == "claude-3-sonnet"
        assert result["provider"] == "claude"
        assert result["usage"]["total_tokens"] == 15

    def test_generate_default_max_tokens(self):
        adapter = ClaudeAdapter("claude-3")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = make_claude_response()
        adapter._client = mock_client
        adapter.generate("hello")
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 1024

    def test_generate_exception(self):
        adapter = ClaudeAdapter("claude-3")
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("claude error")
        adapter._client = mock_client
        result = adapter.generate("hello")
        assert result["success"] is False

    def test_chat_no_client(self):
        result = ClaudeAdapter("claude-3").chat([{"role": "user", "content": "hi"}])
        assert "error" in result

    def test_chat_success(self):
        adapter = ClaudeAdapter("claude-3")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = make_claude_response("chat response")
        adapter._client = mock_client
        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert result["success"] is True
        assert result["content"] == "chat response"

    def test_chat_exception(self):
        adapter = ClaudeAdapter("claude-3")
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("chat fail")
        adapter._client = mock_client
        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert result["success"] is False


# ============================================================================
# GeminiAdapter 测试
# ============================================================================

class TestGeminiAdapter:
    def test_init(self):
        adapter = GeminiAdapter("gemini-1.5-pro", api_key="key")
        assert adapter._model_name == "gemini-1.5-pro"
        assert adapter._api_key == "key"

    def test_get_provider_name(self):
        assert GeminiAdapter("gemini").get_provider_name() == "gemini"

    def test_get_model_name(self):
        assert GeminiAdapter("gemini-1.5").get_model_name() == "gemini-1.5"

    def test_cost_known_models(self):
        assert GeminiAdapter("gemini-1.0-pro").get_cost_per_token() == {"prompt": 0.0015, "completion": 0.0015}
        assert GeminiAdapter("gemini-1.5-flash").get_cost_per_token() == {"prompt": 0.000125, "completion": 0.000375}
        assert GeminiAdapter("gemini-1.5-pro").get_cost_per_token() == {"prompt": 0.001, "completion": 0.003}

    def test_cost_unknown_model(self):
        cost = GeminiAdapter("unknown").get_cost_per_token()
        assert cost == {"prompt": 0.000125, "completion": 0.000375}

    def test_is_available_no_client(self):
        adapter = GeminiAdapter("gemini")
        with patch.object(adapter, "_get_client", return_value=None):
            assert adapter.is_available() is False

    def test_is_available_with_client(self):
        adapter = GeminiAdapter("gemini")
        adapter._client = MagicMock()
        assert adapter.is_available() is True

    def test_generate_no_client(self):
        adapter = GeminiAdapter("gemini")
        with patch.object(adapter, "_get_client", return_value=None):
            result = adapter.generate("hello")
        assert "error" in result

    def test_generate_success(self):
        adapter = GeminiAdapter("gemini-1.5-pro")
        mock_client = MagicMock()
        mock_client.generate_content.return_value = make_gemini_response("gemini response")
        adapter._client = mock_client
        result = adapter.generate("hello")
        assert result["success"] is True
        assert result["content"] == "gemini response"
        assert result["model"] == "gemini-1.5-pro"
        assert result["provider"] == "gemini"
        assert result["usage"]["total_tokens"] == 15

    def test_generate_exception(self):
        adapter = GeminiAdapter("gemini")
        mock_client = MagicMock()
        mock_client.generate_content.side_effect = Exception("gemini error")
        adapter._client = mock_client
        result = adapter.generate("hello")
        assert result["success"] is False

    def test_chat_no_client(self):
        result = GeminiAdapter("gemini").chat([{"role": "user", "content": "hi"}])
        assert "error" in result

    def test_chat_success(self):
        adapter = GeminiAdapter("gemini-1.5")
        mock_chat = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "chat reply"
        mock_chat.send_message.return_value = mock_response
        mock_client = MagicMock()
        mock_client.start_chat.return_value = mock_chat
        adapter._client = mock_client

        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "how are you"},
        ]
        result = adapter.chat(messages)
        assert result["success"] is True
        assert result["content"] == "chat reply"
        # 第一个消息作为历史，最后一个消息发送
        assert mock_chat.send_message.call_count == 2

    def test_chat_exception(self):
        adapter = GeminiAdapter("gemini")
        mock_client = MagicMock()
        mock_client.start_chat.side_effect = Exception("chat error")
        adapter._client = mock_client
        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert result["success"] is False


# ============================================================================
# ZhipuAdapter 测试
# ============================================================================

class TestZhipuAdapter:
    def test_init(self):
        adapter = ZhipuAdapter("glm-4", api_key="key")
        assert adapter._model_name == "glm-4"
        assert adapter._api_key == "key"

    def test_get_provider_name(self):
        assert ZhipuAdapter("glm-4").get_provider_name() == "zhipu"

    def test_get_model_name(self):
        assert ZhipuAdapter("glm-4").get_model_name() == "glm-4"

    def test_cost_known_models(self):
        assert ZhipuAdapter("glm-4").get_cost_per_token() == {"prompt": 0.002, "completion": 0.002}
        assert ZhipuAdapter("glm-4v").get_cost_per_token() == {"prompt": 0.002, "completion": 0.002}
        assert ZhipuAdapter("glm-3-turbo").get_cost_per_token() == {"prompt": 0.0005, "completion": 0.0005}

    def test_cost_unknown_model(self):
        cost = ZhipuAdapter("unknown").get_cost_per_token()
        assert cost == {"prompt": 0.002, "completion": 0.002}

    def test_is_available_no_client(self):
        adapter = ZhipuAdapter("glm-4")
        with patch.object(adapter, "_get_client", return_value=None):
            assert adapter.is_available() is False

    def test_is_available_with_client(self):
        adapter = ZhipuAdapter("glm-4")
        adapter._client = MagicMock()
        assert adapter.is_available() is True

    def test_generate_no_client(self):
        adapter = ZhipuAdapter("glm-4")
        with patch.object(adapter, "_get_client", return_value=None):
            result = adapter.generate("hello")
        assert "error" in result

    def test_generate_success(self):
        adapter = ZhipuAdapter("glm-4")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = make_zhipu_response("zhipu response")
        adapter._client = mock_client
        result = adapter.generate("hello")
        assert result["success"] is True
        assert result["content"] == "zhipu response"
        assert result["model"] == "glm-4"
        assert result["provider"] == "zhipu"

    def test_generate_filters_reserved_kwargs(self):
        adapter = ZhipuAdapter("glm-4")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = make_zhipu_response()
        adapter._client = mock_client
        adapter.generate("hello", model="ignored", messages="ignored", temperature=0.5)
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "glm-4"

    def test_generate_exception(self):
        adapter = ZhipuAdapter("glm-4")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("zhipu error")
        adapter._client = mock_client
        result = adapter.generate("hello")
        assert result["success"] is False

    def test_chat_no_client(self):
        result = ZhipuAdapter("glm-4").chat([{"role": "user", "content": "hi"}])
        assert "error" in result

    def test_chat_success(self):
        adapter = ZhipuAdapter("glm-4")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = make_zhipu_response("zhipu chat")
        adapter._client = mock_client
        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert result["success"] is True
        assert result["content"] == "zhipu chat"

    def test_chat_exception(self):
        adapter = ZhipuAdapter("glm-4")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("chat fail")
        adapter._client = mock_client
        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert result["success"] is False


# ============================================================================
# QwenAdapter 测试
# ============================================================================

class TestQwenAdapter:
    def test_init(self):
        adapter = QwenAdapter("qwen-turbo", api_key="key", api_secret="secret")
        assert adapter._model_name == "qwen-turbo"
        assert adapter._api_key == "key"
        assert adapter._api_secret == "secret"

    def test_get_provider_name(self):
        assert QwenAdapter("qwen-turbo").get_provider_name() == "qwen"

    def test_get_model_name(self):
        assert QwenAdapter("qwen-turbo").get_model_name() == "qwen-turbo"

    def test_cost_known_models(self):
        assert QwenAdapter("qwen-turbo").get_cost_per_token() == {"prompt": 0.0008, "completion": 0.0012}
        assert QwenAdapter("qwen-plus").get_cost_per_token() == {"prompt": 0.0015, "completion": 0.002}
        assert QwenAdapter("qwen-max").get_cost_per_token() == {"prompt": 0.003, "completion": 0.006}

    def test_cost_unknown_model(self):
        cost = QwenAdapter("unknown").get_cost_per_token()
        assert cost == {"prompt": 0.0008, "completion": 0.0012}

    def test_is_available_no_client(self):
        adapter = QwenAdapter("qwen-turbo")
        with patch.object(adapter, "_get_client", return_value=None):
            assert adapter.is_available() is False

    def test_is_available_with_client(self):
        adapter = QwenAdapter("qwen-turbo")
        adapter._client = MagicMock()
        assert adapter.is_available() is True

    def test_generate_no_client(self):
        adapter = QwenAdapter("qwen-turbo")
        with patch.object(adapter, "_get_client", return_value=None):
            result = adapter.generate("hello")
        assert "error" in result

    def test_generate_success(self):
        adapter = QwenAdapter("qwen-turbo")
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = make_qwen_response("qwen response")
        adapter._client = mock_client
        mock_models = MagicMock()
        with patch.dict("sys.modules", {"alibabacloud_dashscope_api20230714": MagicMock(), "alibabacloud_dashscope_api20230714.models": mock_models}):
            result = adapter.generate("hello")
            assert result["success"] is True
            assert result["content"] == "qwen response"
            assert result["model"] == "qwen-turbo"
            assert result["provider"] == "qwen"

    def test_generate_exception(self):
        adapter = QwenAdapter("qwen-turbo")
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = Exception("qwen error")
        adapter._client = mock_client
        mock_models = MagicMock()
        with patch.dict("sys.modules", {"alibabacloud_dashscope_api20230714": MagicMock(), "alibabacloud_dashscope_api20230714.models": mock_models}):
            result = adapter.generate("hello")
            assert result["success"] is False

    def test_chat_no_client(self):
        adapter = QwenAdapter("qwen-turbo")
        with patch.object(adapter, "_get_client", return_value=None):
            result = adapter.chat([{"role": "user", "content": "hi"}])
        assert "error" in result

    def test_chat_success(self):
        adapter = QwenAdapter("qwen-turbo")
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = make_qwen_response("qwen chat")
        adapter._client = mock_client
        mock_models = MagicMock()
        with patch.dict("sys.modules", {"alibabacloud_dashscope_api20230714": MagicMock(), "alibabacloud_dashscope_api20230714.models": mock_models}):
            result = adapter.chat([{"role": "user", "content": "hi"}])
            assert result["success"] is True
            assert result["content"] == "qwen chat"

    def test_chat_exception(self):
        adapter = QwenAdapter("qwen-turbo")
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = Exception("chat fail")
        adapter._client = mock_client
        mock_models = MagicMock()
        with patch.dict("sys.modules", {"alibabacloud_dashscope_api20230714": MagicMock(), "alibabacloud_dashscope_api20230714.models": mock_models}):
            result = adapter.chat([{"role": "user", "content": "hi"}])
            assert result["success"] is False


# ============================================================================
# ModelAdapterFactory 测试
# ============================================================================

class TestModelAdapterFactory:
    def test_create_openai(self):
        adapter = ModelAdapterFactory.create("openai", "gpt-4", api_key="key")
        assert isinstance(adapter, OpenAIAdapter)
        assert adapter._api_key == "key"

    def test_create_openai_case_insensitive(self):
        adapter = ModelAdapterFactory.create("OpenAI", "gpt-4")
        assert isinstance(adapter, OpenAIAdapter)

    def test_create_openai_with_base_url(self):
        adapter = ModelAdapterFactory.create("openai", "gpt-4", api_key="k", base_url="http://test")
        assert isinstance(adapter, OpenAIAdapter)
        assert adapter._base_url == "http://test"

    def test_create_claude(self):
        adapter = ModelAdapterFactory.create("claude", "claude-3", api_key="key")
        assert isinstance(adapter, ClaudeAdapter)
        assert adapter._api_key == "key"

    def test_create_gemini(self):
        adapter = ModelAdapterFactory.create("gemini", "gemini-1.5", api_key="key")
        assert isinstance(adapter, GeminiAdapter)

    def test_create_zhipu(self):
        adapter = ModelAdapterFactory.create("zhipu", "glm-4", api_key="key")
        assert isinstance(adapter, ZhipuAdapter)

    def test_create_qwen(self):
        adapter = ModelAdapterFactory.create("qwen", "qwen-turbo", api_key="key", api_secret="secret")
        assert isinstance(adapter, QwenAdapter)
        assert adapter._api_secret == "secret"

    def test_create_unknown_provider(self):
        adapter = ModelAdapterFactory.create("unknown", "model")
        assert adapter is None
