"""LLM API 抽象层 — 专为对话摘要场景设计"""

import logging
import time

logger = logging.getLogger(__name__)


class LLMServiceError(Exception):
    """LLM 服务异常"""
    pass


class LLMService:
    """轻量级 LLM 抽象，专注摘要场景

    支持 OpenAI 和 Anthropic 双后端，通过配置切换。

    不提供通用对话能力，只暴露 summarize() 和 count_tokens() 两个方法。
    """

    OPENAI_COMPAT = {
        "deepseek": "https://api.deepseek.com",
    }

    MIN_API_KEY_LENGTH = 10
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_DELAY = 1.0
    MAX_RETRY_DELAY = 30.0

    def __init__(self, provider: str = "openai", api_key: str = "",
                 model: str = "gpt-4", timeout: int = 30, base_url: str = "",
                 max_retries: int = DEFAULT_MAX_RETRIES, retry_delay: float = DEFAULT_RETRY_DELAY):
        self._validate_api_key(api_key)
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._base_url = base_url or self.OPENAI_COMPAT.get(provider, "")
        self._client = None
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def _validate_api_key(self, api_key: str):
        """验证 API Key 是否有效

        Args:
            api_key: API Key 字符串

        Raises:
            LLMServiceError: API Key 为空或格式不正确
        """
        if not api_key:
            raise LLMServiceError("API Key 不能为空，请检查配置")
        if not api_key.strip():
            raise LLMServiceError("API Key 不能仅包含空白字符")
        if len(api_key) < self.MIN_API_KEY_LENGTH:
            raise LLMServiceError(f"API Key 格式不正确，长度至少需要 {self.MIN_API_KEY_LENGTH} 个字符")

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
        """调用 LLM 生成对话摘要（带重试机制）

        Args:
            messages: 对话消息列表，格式 [{"role": "...", "content": "..."}]
            max_tokens: 摘要最大 Token 数

        Returns:
            摘要文本。空输入返回空字符串。

        Raises:
            LLMServiceError: 所有重试次数耗尽后抛出
        """
        if not messages:
            return ""

        system_prompt = "请将以下对话总结为核心要点，保留关键决策、问题和结论。要求简洁准确。"
        last_exception = None

        for attempt in range(self.max_retries):
            try:
                logger.info("┌─────────────────────────────────────────────")
                logger.info("│ 🔄 [LLM摘要] 第 %d/%d 次尝试", attempt + 1, self.max_retries)
                logger.info("└─────────────────────────────────────────────")

                client = self._get_client()

                if self._is_openai_compat():
                    logger.info("├─ Provider: %s | Model: %s", self.provider, self.model)
                    response = client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            *messages
                        ],
                        max_tokens=max_tokens
                    )
                    result = response.choices[0].message.content.strip()
                    logger.info("│ ✓ 摘要生成成功，长度: %d 字符", len(result))
                    return result

                elif self.provider == "anthropic":
                    logger.info("├─ Provider: %s | Model: %s", self.provider, self.model)
                    response = client.messages.create(
                        model=self.model,
                        system=system_prompt,
                        messages=messages,
                        max_tokens=max_tokens
                    )
                    result = response.content[0].text.strip()
                    logger.info("│ ✓ 摘要生成成功，长度: %d 字符", len(result))
                    return result

            except Exception as e:
                last_exception = e
                logger.warning("├─ 第 %d 次尝试失败: %s", attempt + 1, e)

                if attempt < self.max_retries - 1:
                    delay = min(self.retry_delay * (2 ** attempt), self.MAX_RETRY_DELAY)
                    logger.warning("├─ %.1f 秒后进行第 %d 次尝试（指数退避）", delay, attempt + 2)
                    time.sleep(delay)
                else:
                    logger.error("└─────────────────────────────────────────────")
                    logger.error("│ ✗ [LLM摘要] 所有 %d 次尝试均失败", self.max_retries)
                    logger.error("├─────────────────────────────────────────────")
                    logger.error("│   Provider: %s", self.provider)
                    logger.error("│   Model: %s", self.model)
                    logger.error("│   Timeout: %s 秒", self.timeout)
                    logger.error("│   消息数量: %d 条", len(messages))
                    logger.error("│   最大Token: %d", max_tokens)
                    logger.error("│   最后错误: %s", e)
                    logger.error("└─────────────────────────────────────────────")
                    raise LLMServiceError(f"摘要生成失败（已重试 {self.max_retries} 次）: {e}") from e

        raise LLMServiceError(f"摘要生成失败（已重试 {self.max_retries} 次）: {last_exception}")

    def chat(self, messages: list[dict], system_prompt: str = "",
             max_tokens: int = 1024, temperature: float = 0.7) -> str:
        """调用 LLM 生成对话响应（带重试机制）

        Args:
            messages: 对话历史，格式 [{"role": "user"/"assistant", "content": "..."}]
            system_prompt: 系统提示词（可选）
            max_tokens: 最大生成 Token 数
            temperature: 生成温度

        Returns:
            模型生成的文本内容

        Raises:
            LLMServiceError: 所有重试次数耗尽后抛出
        """
        if not messages:
            return ""

        last_exception = None

        for attempt in range(self.max_retries):
            try:
                logger.info("┌─────────────────────────────────────────────")
                logger.info("│ 🔄 [LLM对话] 第 %d/%d 次尝试", attempt + 1, self.max_retries)
                logger.info("└─────────────────────────────────────────────")

                client = self._get_client()

                if self._is_openai_compat():
                    full_messages = []
                    if system_prompt:
                        full_messages.append({"role": "system", "content": system_prompt})
                    full_messages.extend(messages)
                    logger.info("├─ Provider: %s | Model: %s", self.provider, self.model)
                    response = client.chat.completions.create(
                        model=self.model,
                        messages=full_messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    result = response.choices[0].message.content.strip()
                    logger.info("│ ✓ 对话生成成功，长度: %d 字符", len(result))
                    return result

                elif self.provider == "anthropic":
                    kwargs = {}
                    if system_prompt:
                        kwargs["system"] = system_prompt
                    logger.info("├─ Provider: %s | Model: %s", self.provider, self.model)
                    response = client.messages.create(
                        model=self.model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        **kwargs,
                    )
                    result = response.content[0].text.strip()
                    logger.info("│ ✓ 对话生成成功，长度: %d 字符", len(result))
                    return result

            except Exception as e:
                last_exception = e
                logger.warning("├─ 第 %d 次尝试失败: %s", attempt + 1, e)

                if attempt < self.max_retries - 1:
                    delay = min(self.retry_delay * (2 ** attempt), self.MAX_RETRY_DELAY)
                    logger.warning("├─ %.1f 秒后进行第 %d 次尝试（指数退避）", delay, attempt + 2)
                    time.sleep(delay)
                else:
                    logger.error("└─────────────────────────────────────────────")
                    logger.error("│ ✗ [LLM对话] 所有 %d 次尝试均失败", self.max_retries)
                    logger.error("├─────────────────────────────────────────────")
                    logger.error("│   Provider: %s", self.provider)
                    logger.error("│   Model: %s", self.model)
                    logger.error("│   Timeout: %s 秒", self.timeout)
                    logger.error("│   Temperature: %.2f", temperature)
                    logger.error("│   消息数量: %d 条", len(messages))
                    logger.error("│   最大Token: %d", max_tokens)
                    logger.error("│   最后错误: %s", e)
                    logger.error("└─────────────────────────────────────────────")
                    raise LLMServiceError(f"对话生成失败（已重试 {self.max_retries} 次）: {e}") from e

        raise LLMServiceError(f"对话生成失败（已重试 {self.max_retries} 次）: {last_exception}")

    def count_tokens(self, text: str) -> int:
        """使用 tiktoken 估算文本 Token 数（不依赖 LLM API）"""
        try:
            import tiktoken
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except ImportError:
            # 降级估算
            return len(text) // 4
