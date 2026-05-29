"""LLM API 抽象层 — 专为对话摘要场景设计"""

import logging

logger = logging.getLogger(__name__)


class LLMServiceError(Exception):
    """LLM 服务异常"""
    pass


class LLMService:
    """轻量级 LLM 抽象，专注摘要场景

    支持 OpenAI 和 Anthropic 双后端，通过配置切换。

    不提供通用对话能力，只暴露 summarize() 和 count_tokens() 两个方法。
    """

    # OpenAI 兼容供应商的 base_url 映射
    OPENAI_COMPAT = {
        "deepseek": "https://api.deepseek.com",
    }

    def __init__(self, provider: str = "openai", api_key: str = "",
                 model: str = "gpt-4", timeout: int = 30, base_url: str = ""):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._base_url = base_url or self.OPENAI_COMPAT.get(provider, "")
        self._client = None

    def _get_client(self):
        """惰性初始化 API 客户端"""
        if self._client is not None:
            return self._client

        if self.provider in ("openai", *self.OPENAI_COMPAT.keys()):
            import openai
            kwargs = {"api_key": self.api_key, "timeout": self.timeout}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = openai.OpenAI(**kwargs)
            return self._client
        elif self.provider == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)
            return self._client
        else:
            raise LLMServiceError(f"不支持的 provider: {self.provider}")

    def _is_openai_compat(self) -> bool:
        """判断当前提供商是否兼容 OpenAI API 格式"""
        return self.provider in ("openai", *self.OPENAI_COMPAT.keys())

    def summarize(self, messages: list[dict], max_tokens: int = 500) -> str:
        """调用 LLM 生成对话摘要

        Args:
            messages: 对话消息列表，格式 [{"role": "...", "content": "..."}]
            max_tokens: 摘要最大 Token 数

        Returns:
            摘要文本。空输入返回空字符串。
        """
        if not messages:
            return ""

        system_prompt = "请将以下对话总结为核心要点，保留关键决策、问题和结论。要求简洁准确。"

        try:
            client = self._get_client()

            if self._is_openai_compat():
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        *messages
                    ],
                    max_tokens=max_tokens
                )
                return response.choices[0].message.content.strip()

            elif self.provider == "anthropic":
                response = client.messages.create(
                    model=self.model,
                    system=system_prompt,
                    messages=messages,
                    max_tokens=max_tokens
                )
                return response.content[0].text.strip()

        except Exception as e:
            logger.error("LLM API 调用失败: %s", e)
            raise LLMServiceError(f"摘要生成失败: {e}") from e

    def chat(self, messages: list[dict], system_prompt: str = "",
             max_tokens: int = 1024, temperature: float = 0.7) -> str:
        """调用 LLM 生成对话响应（通用对话接口）

        Args:
            messages: 对话历史，格式 [{"role": "user"/"assistant", "content": "..."}]
            system_prompt: 系统提示词（可选）
            max_tokens: 最大生成 Token 数
            temperature: 生成温度

        Returns:
            模型生成的文本内容
        """
        if not messages:
            return ""

        try:
            client = self._get_client()

            if self._is_openai_compat():
                full_messages = []
                if system_prompt:
                    full_messages.append({"role": "system", "content": system_prompt})
                full_messages.extend(messages)
                response = client.chat.completions.create(
                    model=self.model,
                    messages=full_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return response.choices[0].message.content.strip()

            elif self.provider == "anthropic":
                kwargs = {}
                if system_prompt:
                    kwargs["system"] = system_prompt
                response = client.messages.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                )
                return response.content[0].text.strip()

        except Exception as e:
            logger.error("LLM 对话调用失败: %s", e)
            raise LLMServiceError(f"对话生成失败: {e}") from e

    def count_tokens(self, text: str) -> int:
        """使用 tiktoken 估算文本 Token 数（不依赖 LLM API）"""
        try:
            import tiktoken
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except ImportError:
            # 降级估算
            return len(text) // 4
