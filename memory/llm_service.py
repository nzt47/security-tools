"""LLM API 抽象层 — 专为对话摘要场景设计"""

import logging
import os
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
        # ── base_url 解析优先级：显式入参 > 环境变量 > provider 内置默认 ──
        # 【不易·2026-09-19 实测缺陷】此前只有"显式入参 > 内置默认"，**环境变量被跳过**。
        #   后果是**同一份 `.env` 在不同链路生效不同**：`plugins/chat.py:987` 与
        #   `agent/orchestrator/lifecycle_manager.py:1193` 显式读了 env，而
        #   `memory/memory_manager.py:251-256` 构造时**未传 base_url** ⇒ 静默回退到
        #   硬编码的 `https://api.deepseek.com`（`OPENAI_COMPAT`）。
        #   换网关 / 自建代理时，改 `.env` 对**流式链路生效、对编排器与记忆摘要不生效** ——
        #   这是最容易被误判成"模型坏了"的一类排障陷阱（实测由端到端验证发现）。
        #
        # 为什么把回退放在**本构造函数**而不是逐个调用点补参数：
        #   全仓有 10+ 个 `LLMService(...)` 构造点（`memory/memory_manager.py`、
        #   `plugins/admin.py`、`agent/tool_calling.py`×3、
        #   `agent/orchestrator/task_dispatcher.py`、`agent/process_distill/service.py` …），
        #   逐个补是治标；本构造函数是**唯一收口**，在此统一回退才能根治。
        #
        # 为什么用这两个变量名：`agent/settings/registry.py:1420,1423` 已登记
        #   `LLM_BASE_URL` 与 `DEEPSEEK_BASE_URL` ⇒ **不引入新环境变量**（不制造 D5 零缺口），
        #   且与 `plugins/chat.py:987` 的既有读法逐字一致（`LLM_BASE_URL` 优先）。
        #
        # 为什么不在这里 import `agent.settings`：本模块位于 `memory/`，被大量低层模块导入；
        #   引入 `agent.settings` 会拉进注册表依赖并可能形成环。`os.environ` 是本文件既有的、
        #   唯一配置来源（模块顶部原本只有 logging/time，本次为读取它而显式引入 os）。
        _env_base = (os.environ.get("LLM_BASE_URL", "")
                     or os.environ.get("DEEPSEEK_BASE_URL", "")).strip()
        self._base_url = (base_url or _env_base
                          or self.OPENAI_COMPAT.get(self.provider, "")).rstrip("/")
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
        """实际的对话生成逻辑（不含重试）

        【DSML 根因防线】本方法**按定义永远不下发 `tools`**（没有该形参），
        因此传入的 `system_prompt` 里任何"你有工具"的宣传都是**虚假宣传**。
        实测（`_baseline/dsml-evidence/cond4_P1`）：提示词宣传工具 + 请求无 tools
        ⇒ 上游 DeepSeek 退回 DSML 文本协议，标记被当正文返回给用户。
        真实触发路径之一：`agent/tool_calling.py::chat_with_steps` 首轮 LLM 调用
        连续失败后降级到 `_current_llm.chat(...)`（那里同样不带 tools）。
        """
        try:
            from agent.tools_prompt_guard import align_system_prompt_with_tools
            system_prompt, _ = align_system_prompt_with_tools(
                system_prompt, False, site="llm_service._do_chat")
        except Exception as _g_e:  # noqa: BLE001 守卫异常不得击穿对话主链路
            logger.debug("[LLM][tools_prompt_guard] 一致性守卫异常（按原样继续）: %s", _g_e)

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
                    on_tool_call=None, tools: list | None = None,
                    on_reasoning=None):
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
            on_reasoning: 可选回调 fn(reasoning_delta)——DeepSeek thinking 模式下逐段
                产出推理内容（思考过程）。**additive**：不传时行为与既有完全一致
                （推理内容仍只用于工具调用回传）。

        Yields:
            str: 每次产出的文本增量（可为空串）
        """
        if not messages:
            return
        client = self._get_client()

        # 【DSML 根因防线】流式路径的**最后一道收口**。
        # 本条链路上游有多个调用方会各自决定是否传 `tools`
        # （`plugins/chat.py` 的工具循环、编排器、`chat_with_steps`），
        # 任何一处把 tools 丢掉而提示词仍宣传工具，都会在这里被中和并记
        # `event=tools_prompt_mismatch`（实测根因见 agent/tools_prompt_guard.py）。
        try:
            from agent.tools_prompt_guard import align_system_prompt_with_tools
            system_prompt, _ = align_system_prompt_with_tools(
                system_prompt, bool(tools), site="llm_service.chat_stream",
                tools_count=len(tools or []))
        except Exception as _g_e:  # noqa: BLE001 守卫异常不得打断流
            logger.debug("[Stream][tools_prompt_guard] 一致性守卫异常（按原样继续）: %s", _g_e)

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
            # ── 文本协议（DSML）流式守卫（TASK-01 修复 A） ──
            # 实测流式响应有数百个 chunk，标记会被任意切断（例如切在标记前缀中间）。
            # 逐 chunk 判正则既漏检、又会把正文切碎 ⇒ 必须在这里攒齐。
            # 守卫本身**有界**（体积 + 时间上限），不会因上游吐半个标记就挂住整条流。
            _dsml_guard = None
            try:
                from agent.dsml_adapter import DSMLStreamGuard
                _dsml_guard = DSMLStreamGuard()
            except Exception as _guard_e:  # noqa: BLE001
                logger.debug("DSML 流式守卫不可用（退化为逐片直通）: %s", _guard_e)
            _stream_started = time.time()
            _last_finish = ""
            _text_chars = 0
            # 把上游 finish_reason 挂到实例上，供上层（plugins/chat.py 的 SSE 兜底）
            # 记进 event=llm_empty_response —— 否则那一层的 finish_reason 只能是空串，
            # 而它恰恰是判断"空 choices / tool_calls 却没带 tool_calls / 超时降级"的关键字段。
            self._last_stream_finish_reason = ""
            for chunk in stream:
                if not chunk.choices:
                    continue
                _fr = getattr(chunk.choices[0], "finish_reason", None)
                if _fr:
                    _last_finish = _fr
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
                    if _dsml_guard is None:
                        _text_chars += len(delta.content)
                        yield delta.content
                    else:
                        for _safe_piece in _dsml_guard.feed(delta.content):
                            # 计数口径 = **真正外发给用户的字符数**（不含被守卫吞掉的标记），
                            # 否则"只吐了一个解析失败的标记"会被误判成"有内容、非空返回"。
                            _text_chars += len(_safe_piece)
                            yield _safe_piece
                # 收集 reasoning_content（DeepSeek 推理，回传时需带上）
                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    reasoning_parts.append(rc)
                    # 思考过程实时外发（additive：未传回调时这一支不生效）
                    if on_reasoning is not None:
                        try:
                            on_reasoning(rc)
                        except Exception as _re:  # noqa: BLE001 回调异常不影响主流程
                            logger.debug("on_reasoning 回调异常: %s", _re)

            # ── 守卫收尾：放行残留、上报攒到的文本协议工具调用 ──
            if _dsml_guard is not None:
                for _safe_piece in _dsml_guard.flush():
                    _text_chars += len(_safe_piece)
                    yield _safe_piece
                _g_res = _dsml_guard.take_result()
                if _g_res.errors or _g_res.tool_calls:
                    logger.warning("[Stream] 文本协议(DSML)解析结果: %s", _g_res.log_fields())
                if _g_res.tool_calls and on_tool_call is not None:
                    for _tc in _g_res.tool_calls:
                        _fn = _tc.get("function", {})
                        on_tool_call(_fn.get("name", ""), _fn.get("arguments", ""),
                                     "".join(reasoning_parts))

            # 流结束后上报已聚合的工具调用（只上报完整有名字的）
            if on_tool_call is not None:
                for idx in sorted(tool_accum):
                    acc = tool_accum[idx]
                    if acc["name"]:
                        on_tool_call(acc["name"], acc["args"], "".join(reasoning_parts))

            # ── 空返回的统一判定 + 结构化日志（TASK-01 修复 B） ──
            # 修复前这里**什么都不记**：只有 plugins/chat.py 一句兜底文案，
            # 线上无从判断是上游空 choices、finish_reason=tool_calls 却没带
            # tool_calls、还是内容被吞。口径集中在 agent.llm_response_guard。
            try:
                from agent.llm_response_guard import is_valid_response, log_empty_response
                if not is_valid_response(
                        "x" * _text_chars, list(tool_accum.values()),
                        "".join(reasoning_parts)):
                    log_empty_response(
                        source="memory/llm_service.py::chat_stream",
                        provider=self.provider, model=self.model,
                        finish_reason=_last_finish,
                        has_tool_calls=bool(tool_accum),
                        elapsed_ms=(time.time() - _stream_started) * 1000.0,
                        raw_prefix="")
            except Exception as _empty_e:  # noqa: BLE001 观测失败不得影响主链路
                logger.debug("[Stream] 空返回判定失败: %s", _empty_e)
            finally:
                self._last_stream_finish_reason = _last_finish

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
