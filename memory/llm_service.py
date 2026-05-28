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

    def __init__(self, provider: str = "openai", api_key: str = "",
                 model: str = "gpt-4", timeout: int = 30):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = None

    def _get_client(self):
        """惰性初始化 API 客户端"""
        if self._client is not None:
            return self._client

        if self.provider == "openai":
            import openai
            self._client = openai.OpenAI(api_key=self.api_key, timeout=self.timeout)
            return self._client
        elif self.provider == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)
            return self._client
        else:
            raise LLMServiceError(f"不支持的 provider: {self.provider}")

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

            if self.provider == "openai":
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
                import anthropic
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

    def count_tokens(self, text: str) -> int:
        """使用 tiktoken 估算文本 Token 数（不依赖 LLM API）"""
        try:
            import tiktoken
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except ImportError:
            # 降级估算
            return len(text) // 4
