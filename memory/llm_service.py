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
        # 规范化 provider 大小写（如 .env 中的 'DeepSeek' → 'deepseek'），
        # 兼容 OPENAI_COMPAT 映射表的大小写敏感匹配。
        self.provider = (provider or "").strip().lower() or "openai"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._base_url = (base_url or self.OPENAI_COMPAT.get(self.provider, "")).rstrip("/")
        self._client = None
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        # 延迟导入避免循环依赖
        from agent.error_handler import (
            with_retry,
            TemporaryNetworkError,
            ExternalServiceError
        )
        
        self._summarize_with_retry = with_retry(
            max_retries=self.max_retries,
            initial_delay=self.retry_delay,
            max_delay=self.MAX_RETRY_DELAY,
            backoff_factor=2.0,
            strategy="exponential",
            jitter_factor=0.1,
            retryable_exceptions=(TemporaryNetworkError, ExternalServiceError),
            error_counter="llm.summarize",
            on_retry=self._on_summarize_retry
        )(self._do_summarize)
        
        self._chat_with_retry = with_retry(
            max_retries=self.max_retries,
            initial_delay=self.retry_delay,
            max_delay=self.MAX_RETRY_DELAY,
            backoff_factor=2.0,
            strategy="exponential",
            jitter_factor=0.1,
            retryable_exceptions=(TemporaryNetworkError, ExternalServiceError),
            error_counter="llm.chat",
            on_retry=self._on_chat_retry
        )(self._do_chat)
    
    def _on_summarize_retry(self, attempt: int, error: Exception) -> None:
        logger.info("┌─────────────────────────────────────────────")
        logger.info("│ 🔄 [LLM摘要] 第 %d/%d 次尝试", attempt, self.max_retries)
        logger.info("└─────────────────────────────────────────────")
        logger.warning("├─ 第 %d 次尝试失败: %s", attempt, error)
    
    def _on_chat_retry(self, attempt: int, error: Exception) -> None:
        logger.info("┌─────────────────────────────────────────────")
        logger.info("│ 🔄 [LLM对话] 第 %d/%d 次尝试", attempt, self.max_retries)
        logger.info("└─────────────────────────────────────────────")
        logger.warning("├─ 第 %d 次尝试失败: %s", attempt, error)

    # ── TASK-S2-03：模型降级链（P7.1-18 第 9 事件 model.degraded） ──

    def _shadow_service(self, model: str) -> "LLMService":
        """构造「仅模型名不同」的影子实例（并发安全：不改 self.model）

        影子复用同一 provider/api_key/base_url 与已建客户端；重试包装器按当前配置
        重新绑定到影子的 ``_do_chat`` / ``_do_summarize``（既有实例零改动）。
        """
        import copy

        from agent.error_handler import (
            ExternalServiceError,
            TemporaryNetworkError,
            with_retry,
        )
        shadow = copy.copy(self)
        shadow.model = model
        shadow._summarize_with_retry = with_retry(
            max_retries=self.max_retries, initial_delay=self.retry_delay,
            max_delay=self.MAX_RETRY_DELAY, backoff_factor=2.0,
            strategy="exponential", jitter_factor=0.1,
            retryable_exceptions=(TemporaryNetworkError, ExternalServiceError),
            error_counter="llm.summarize.fallback",
            on_retry=self._on_summarize_retry)(shadow._do_summarize)
        shadow._chat_with_retry = with_retry(
            max_retries=self.max_retries, initial_delay=self.retry_delay,
            max_delay=self.MAX_RETRY_DELAY, backoff_factor=2.0,
            strategy="exponential", jitter_factor=0.1,
            retryable_exceptions=(TemporaryNetworkError, ExternalServiceError),
            error_counter="llm.chat.fallback",
            on_retry=self._on_chat_retry)(shadow._do_chat)
        return shadow

    def _fallback_after_failure(self, kind: str, error: Exception,
                                invoke) -> dict:
        """主模型失败收口：**总是** emit `model.degraded`；按开关决定是否真切换

        `CP_MODEL_FALLBACK_ENABLED` 默认 0 → 只发事件不改行为（P4 分级实施）；
        置 1 且链上有候选时，用 `invoke(影子实例)` 真正降级重试。
        """
        try:
            from agent.observability.model_degrade import handle_primary_failure
            return handle_primary_failure(
                model=self.model,
                reason=f"{kind}: {type(error).__name__}: {error}",
                retry=lambda candidate: invoke(self._shadow_service(candidate)),
                provider=self.provider)
        except Exception as inner:  # noqa: BLE001 埋点/降级不得掩盖原始错误
            logger.debug("[LLM] 降级处理失败（保留原始异常）: %s", inner)
            return {}

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

    def _do_summarize(self, messages: list[dict], max_tokens: int = 500, system_prompt: str = "") -> str:
        """实际的摘要生成逻辑（不含重试）"""
        system_prompt = system_prompt or "请将以下对话总结为核心要点，保留关键决策、问题和结论。要求简洁准确。"
        
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
    
    def summarize(self, messages: list[dict], max_tokens: int = 500, system_prompt: str = "") -> str:
        """调用 LLM 生成对话摘要（带重试机制）

        Args:
            messages: 对话消息列表，格式 [{"role": "...", "content": "..."}]
            max_tokens: 摘要最大 Token 数
            system_prompt: 自定义系统提示词（可选）

        Returns:
            摘要文本。空输入返回空字符串。

        Raises:
            LLMServiceError: 所有重试次数耗尽后抛出
        """
        if not messages:
            return ""
        
        try:
            return self._summarize_with_retry(messages, max_tokens, system_prompt)
        except Exception as e:
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
            # TASK-S2-03：主模型失败 → emit model.degraded（可选真实降级重试）
            outcome = self._fallback_after_failure(
                "summarize", e,
                lambda shadow: shadow._summarize_with_retry(
                    messages, max_tokens, system_prompt))
            if outcome.get("succeeded"):
                logger.warning("│ ↩ 已降级到 %s 完成摘要（%s）",
                               outcome.get("to"), outcome.get("chain_source"))
                return str(outcome["result"])
            raise LLMServiceError(f"摘要生成失败（已重试 {self.max_retries} 次）: {e}") from e
    
    def _do_chat(self, messages: list[dict], system_prompt: str = "",
                 max_tokens: int = 1024, temperature: float = 0.7) -> str:
        """实际的对话生成逻辑（不含重试）"""
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
        
        try:
            return self._chat_with_retry(messages, system_prompt, max_tokens, temperature)
        except Exception as e:
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
            # TASK-S2-03：主模型失败 → emit model.degraded（可选真实降级重试）
            outcome = self._fallback_after_failure(
                "chat", e,
                lambda shadow: shadow._chat_with_retry(
                    messages, system_prompt, max_tokens, temperature))
            if outcome.get("succeeded"):
                logger.warning("│ ↩ 已降级到 %s 完成对话（%s）",
                               outcome.get("to"), outcome.get("chain_source"))
                return str(outcome["result"])
            raise LLMServiceError(f"对话生成失败（已重试 {self.max_retries} 次）: {e}") from e

    def chat_stream(self, messages: list[dict], system_prompt: str = "",
                    max_tokens: int = 1024, temperature: float = 0.7,
                    on_tool_call=None, tools: list | None = None):
        """流式对话生成（生成器，逐 chunk 产出文本增量）

        用于 SSE 流式输出场景：前端逐块渲染。
        支持 OpenAI 兼容（含 DeepSeek）与 Anthropic。

        Args:
            messages: 对话历史，格式 [{"role": "user"/"assistant", "content": "..."}]
            system_prompt: 系统提示词（可选）
            max_tokens: 最大生成 Token 数
            temperature: 生成温度
            on_tool_call: 可选回调 fn(tool_name, args_json, reasoning_content)——流式检测到模型
                工具调用时触发（OpenAI 兼容流式 tool_calls 增量聚合；reasoning_content
                为 DeepSeek thinking 模式推理内容，回传消息时需附带）。
            tools: 可选 OpenAI 格式工具定义列表（[{type:function,function:{...}}]），
                传入后模型可请求工具调用。

        Yields:
            str: 每次产出的文本增量（可为空串）
        """
        if not messages:
            return
        client = self._get_client()

        if self._is_openai_compat():
            full_messages = []
            if system_prompt:
                full_messages.append({"role": "system", "content": system_prompt})
            full_messages.extend(messages)
            logger.info("├─ [Stream] Provider: %s | Model: %s | tools=%s",
                        self.provider, self.model, bool(tools))
            create_kwargs = dict(
                model=self.model,
                messages=full_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
            if tools:
                create_kwargs["tools"] = tools
            stream = client.chat.completions.create(**create_kwargs)
            # 工具调用增量聚合：流式 tool_calls 按 index 分片，需跨 chunk 拼接
            tool_accum: dict[int, dict] = {}
            # DeepSeek thinking 模式：reasoning_content 需在回传消息时附上
            reasoning_parts: list[str] = []
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                # 提取工具调用增量（函数名 + 参数 JSON 分片）
                if delta and delta.tool_calls and on_tool_call is not None:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        acc = tool_accum.setdefault(idx, {"name": "", "args": ""})
                        if tc.function:
                            if tc.function.name:
                                acc["name"] += tc.function.name
                            if tc.function.arguments:
                                acc["args"] += tc.function.arguments
                if delta and delta.content:
                    yield delta.content
                # 收集 reasoning_content（DeepSeek 推理，回传时需带上）
                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    reasoning_parts.append(rc)
            # 流结束后上报已聚合的工具调用（只上报完整有名字的）
            if on_tool_call is not None:
                for idx in sorted(tool_accum):
                    acc = tool_accum[idx]
                    if acc["name"]:
                        on_tool_call(acc["name"], acc["args"], "".join(reasoning_parts))

        elif self.provider == "anthropic":
            kwargs = {}
            if system_prompt:
                kwargs["system"] = system_prompt
            logger.info("├─ [Stream] Provider: %s | Model: %s", self.provider, self.model)
            with client.messages.stream(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            ) as stream:
                for text in stream.text_stream:
                    yield text

    def count_tokens(self, text: str) -> int:
        """使用 tiktoken 估算文本 Token 数（不依赖 LLM API）"""
        try:
            import tiktoken
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except ImportError:
            # 降级估算
            return len(text) // 4
