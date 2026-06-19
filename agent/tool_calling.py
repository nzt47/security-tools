"""ToolCallingService — LLM 工具调用编排引擎

接收 LLM 返回的 tool_calls，执行对应工具，将结果回注给 LLM，
在 LLM 返回纯文本时终止循环。
"""

import json
import logging
import re
import threading
import time
from typing import Any


def _clean_for_json(obj, _seen=None):
    """递归清理对象，将 bytes 转为字符串，确保 JSON 可序列化

    工具执行结果中可能包含 bytes（如 HTTP 原始响应体），
    json.dumps 无法序列化 bytes，需要提前转换。

    处理类型：bytes, dict, list, tuple, set, frozenset, datetime, date, time, range, complex
    """
    # 处理循环引用
    if _seen is None:
        _seen = set()
    obj_id = id(obj)
    if obj_id in _seen:
        logger.debug("[_clean_for_json] 检测到循环引用，跳过")
        return "<循环引用>"
    _seen.add(obj_id)

    try:
        if isinstance(obj, bytes):
            result = obj.decode("utf-8", errors="replace")
            logger.debug("[_clean_for_json] bytes -> str (长度: %d)", len(result))
            return result
        if isinstance(obj, dict):
            logger.debug("[_clean_for_json] dict -> 递归处理 (%d 个key)", len(obj))
            return {str(k) if k is not None else None: _clean_for_json(v, _seen)
                    for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            logger.debug("[_clean_for_json] list/tuple -> 递归处理 (%d 个元素)", len(obj))
            return [_clean_for_json(v, _seen) for v in obj]
        if isinstance(obj, set):
            logger.debug("[_clean_for_json] set -> 转为list (%d 个元素)", len(obj))
            return [_clean_for_json(v, _seen) for v in obj]
        if isinstance(obj, frozenset):
            logger.debug("[_clean_for_json] frozenset -> 转为list (%d 个元素)", len(obj))
            return [_clean_for_json(v, _seen) for v in obj]
        # 处理常见不可序列化类型
        if isinstance(obj, (int, float, str, bool, type(None))):
            logger.debug("[_clean_for_json] 基本类型直接返回: %s", type(obj).__name__)
            return obj
        # datetime 系列
        if hasattr(obj, 'isoformat'):
            result = obj.isoformat()
            logger.debug("[_clean_for_json] datetime -> str: %s", result)
            return result
        if hasattr(obj, '__str__'):
            result = str(obj)
            logger.debug("[_clean_for_json] 其他类型 -> str: %s", type(obj).__name__)
            return result
        logger.warning("[_clean_for_json] 未知类型，返回类型名称: %s", type(obj).__name__)
        return str(type(obj).__name__)
    except Exception as e:
        logger.error("[_clean_for_json] 异常: %s, 对象类型: %s", e, type(obj).__name__)
        return str(obj)

from agent import tools

logger = logging.getLogger(__name__)


class ToolCallError(Exception):
    """工具调用异常"""
    pass


class ToolCallingService:
    """LLM 工具调用编排引擎（支持多模型路由）"""

    def __init__(self, llm_service, max_rounds: int = None, tool_timeout: int = None,
                 task_timeout: int = None, model_router=None):
        self._primary_llm = llm_service
        self._upgrade_llm = None  # 升级后的 LLM
        self._model_router = model_router  # 模型路由器（可选）
        self._model_upgraded = False  # 是否已升级过模型

        # 从配置系统读取 max_rounds、tool_timeout 和 task_timeout
        from config import Config
        try:
            global_config = Config()
            if max_rounds is None:
                max_rounds = global_config.get("tool_calling", "max_rounds", default=20)
            if tool_timeout is None:
                tool_timeout = global_config.get("behavior", "tool_timeout", default=120)
            if task_timeout is None:
                task_timeout = global_config.get("tool_calling", "task_timeout", default=600)
        except Exception:
            if max_rounds is None:
                max_rounds = 20
            if tool_timeout is None:
                tool_timeout = 120
            if task_timeout is None:
                task_timeout = 600
            logger.warning("[工具调用] 无法从配置系统获取设置，使用默认值 max_rounds=%d, tool_timeout=%d",
                           max_rounds, tool_timeout)
        self._max_rounds = max_rounds
        self._tool_timeout = tool_timeout
        self._task_timeout = task_timeout

        self.last_steps: list[dict] = []
        self._abort_event = threading.Event()
        self._timeout_event = threading.Event()

    @property
    def _current_llm(self):
        """获取当前活跃的 LLM（如已升级则返回升级后的）"""
        return self._upgrade_llm or self._primary_llm

    def _try_upgrade_model(self, steps: list[dict]) -> bool:
        """尝试升级到更强模型（支持多轮 tool calling）

        当当前模型只支持单轮 tool calling，且已经成功执行过工具后，
        自动升级到支持多轮的模型继续处理。

        Returns:
            True 如果升级成功
        """
        if self._model_upgraded:
            return False

        if not self._model_router:
            logger.warning("[ToolCalling] ⬆ 跳过升级: model_router 为空 (primary=%s)",
                          self._primary_llm.model if hasattr(self, '_primary_llm') else 'N/A')
            return False

        has_done_tools = any(
            s.get("type") == "tool_result" and s.get("status") == "success"
            for s in steps
        )
        if not has_done_tools:
            step_types = [s.get("type") for s in steps[-5:]]
            step_statuses = [s.get("status") for s in steps[-5:]]
            logger.info("[ToolCalling] ⬆ 跳过升级: 无成功工具结果 (steps=%s, statuses=%s)",
                       step_types, step_statuses)
            return False

        # 查找当前模型名称在路由器中的注册名
        current_name = None
        for cfg_name, cfg in self._model_router._models.items():
            if cfg.model == self._primary_llm.model:
                current_name = cfg_name
                break

        if not current_name:
            logger.warning("[ToolCalling] ⬆ 无法匹配当前模型 %s 到路由器",
                          self._primary_llm.model)
            return False

        upgrade_cfg = self._model_router.get_upgrade(current_name)
        if not upgrade_cfg:
            # 当前已是多轮模型或没有可升级的目标
            return False

        logger.info("[ToolCalling] ⬆ 升级: %s → %s", self._primary_llm.model, upgrade_cfg.model)
        try:
            from memory.llm_service import LLMService
            new_llm = LLMService(**upgrade_cfg.to_llm_kwargs())
            new_llm._get_client()
            self._upgrade_llm = new_llm
            self._model_upgraded = True
            logger.info("[ToolCalling] ⬆ 升级成功: %s", upgrade_cfg.model)
            return True
        except Exception as e:
            logger.error("[ToolCalling] ⬆ 升级失败: %s", e)
            return False

    def abort(self):
        """手动中止当前正在进行的工具调用循环"""
        self._abort_event.set()
        logger.info("[ToolCalling] ⏹ 手动中止已触发")

    def chat(self, messages: list[dict], system_prompt: str = "",
             max_tokens: int = 8192, temperature: float = 0.7,
             tools_whitelist: list[str] | None = None) -> str:
        """带工具调用的对话（返回纯文本，向后兼容）"""
        result = self.chat_with_steps(
            messages, system_prompt, max_tokens, temperature, tools_whitelist
        )
        return result["text"]

    def chat_with_steps(self, messages: list[dict], system_prompt: str = "",
                        max_tokens: int = 8192, temperature: float = 0.7,
                        tools_whitelist: list[str] | None = None,
                        on_step: callable = None) -> dict:
        """带工具调用的对话，返回文本和步骤

        Args:
            on_step: 可选回调，每步调用时触发 on_step(step_dict)，用于实时流式推送

        Returns:
            {"text": str, "steps": [{"type": str, ...}]}
        """
        tool_defs = tools.get_tool_defs(whitelist=tools_whitelist)
        steps = []
        self.last_steps = steps
        logger.info("[ToolCalling] chat() 开始，工具定义数: %d, 消息数: %d",
                     len(tool_defs), len(messages))
        working_messages = list(messages)

        # 连续失败检测：记录每个工具连续返回错误的次数
        _consecutive_failures: dict[str, int] = {}

        # 重置中止事件（每次新对话开始时清除之前的中止信号）
        self._abort_event.clear()
        self._timeout_event.clear()

        # 启动任务级超时定时器（默认 600s = 10 分钟）
        _timeout_timer = None
        if self._task_timeout > 0:
            _timeout_timer = threading.Timer(self._task_timeout, self._timeout_event.set)
            _timeout_timer.daemon = True
            _timeout_timer.start()
            logger.info("[ToolCalling] 任务超时保护已启用: %d 秒", self._task_timeout)

        response = None  # 安全初始值
        try:
            for round_idx in range(self._max_rounds + 1):
                # 检查手动中止信号
                if self._abort_event.is_set():
                    logger.info("[ToolCalling] ⏹ 检测到中止信号，终止工具循环")
                    steps.append({"type": "aborted", "summary": "⏹ 用户手动中止"})
                    if on_step: on_step(steps[-1])
                    result = {"text": self._get_last_assistant_text(working_messages) or "（已中止）", "steps": steps}
                    return result

                # 检查任务超时
                if self._timeout_event.is_set():
                    logger.warning("[ToolCalling] ⏰ 任务执行超时（%d 秒），终止工具循环", self._task_timeout)
                    steps.append({"type": "timed_out", "summary": f"⏰ 任务执行超时（{self._task_timeout} 秒）"})
                    if on_step: on_step(steps[-1])
                    result = {"text": self._get_last_assistant_text(working_messages) or "（任务超时）", "steps": steps}
                    return result

                need_tools = tool_defs if round_idx < self._max_rounds else None

                # LLM 调用增加指数退避重试，应对瞬时网络波动
                llm_last_exc = None
                for retry_attempt in range(3):
                    if self._abort_event.is_set():
                        break
                    try:
                        has_tools = need_tools is not None and len(need_tools) > 0
                        logger.info("[ToolCalling] 第 %d 轮 LLM 调用（尝试 %d/3），%s工具",
                                    round_idx, retry_attempt + 1,
                                    f"带 {len(need_tools)} 个" if has_tools else "无")
                        response = self._call_llm_with_tools(
                            working_messages, system_prompt,
                            max_tokens, temperature, need_tools
                        )
                        llm_last_exc = None  # 成功，清除异常
                        break
                    except Exception as e:
                        llm_last_exc = e
                        if retry_attempt < 2:  # 前两次失败才重试
                            delay = 2 ** retry_attempt  # 指数退避: 1s, 2s
                            logger.warning("[ToolCalling] LLM 调用失败（第 %d 轮，尝试 %d/3）: %s，%.1fs 后重试",
                                           round_idx, retry_attempt + 1, e, delay)
                            time.sleep(delay)
                        else:
                            logger.error("[ToolCalling] LLM 调用失败（第 %d 轮，3 次尝试均失败）: %s",
                                         round_idx, e)

                if llm_last_exc is not None:
                    logger.error("LLM 调用失败（第 %d 轮，已重试 3 次）: %s", round_idx, llm_last_exc)
                    if round_idx == 0:
                        logger.warning("[ToolCalling] 首轮失败，降级为纯文本 LLM 调用")
                        try:
                            text = self._current_llm.chat(
                                messages, system_prompt=system_prompt,
                                max_tokens=max_tokens, temperature=temperature
                            )
                            steps.append({"type": "text", "content": "（使用基础对话模式）"})
                            if on_step: on_step(steps[-1])
                            return {"text": text, "steps": steps}
                        except Exception as fallback_e:
                            raise ToolCallError(
                                f"LLM 调用失败（已降级）: {fallback_e}"
                            ) from fallback_e
                    return {"text": self._get_last_assistant_text(working_messages), "steps": steps}

                tool_calls = self._extract_tool_calls(response)
                text_preview = self._extract_text(response)
                reasoning = self._extract_reasoning(response)

                if not tool_calls:
                    logger.info("[ToolCalling] LLM 返回纯文本")

                    # ⬆ XML 格式工具调用检测（DeepSeek 模型有时输出 XML 格式而非 JSON tool_calls）
                    _xml_tools = self._extract_xml_tool_calls(text_preview) if text_preview else []
                    if _xml_tools:
                        logger.info("[ToolCalling] 检测到 XML 格式工具调用: %d 个", len(_xml_tools))
                        # 执行 XML 工具，然后用结果摘要直接回复（不返回模型循环，防止死循环）
                        _summaries = []
                        for _xc in _xml_tools:
                            _fn = _xc["function"]["name"]
                            _fa = json.loads(_xc["function"]["arguments"])
                            _xr = self._execute_safe(_fn, _fa)
                            _xok = _xr.get("ok", False)
                            _sum = _summarize_tool_result(_fn, _xr)
                            if on_step:
                                on_step({"type": "tool_call", "tool": _fn, "args": _fa, "status": "running"})
                                on_step({"type": "tool_result", "tool": _fn, "status": "success" if _xok else "error", "summary": _sum})
                            steps.append({"type": "tool_call", "tool": _fn, "args": _fa, "status": "running"})
                            steps.append({"type": "tool_result", "tool": _fn, "status": "success" if _xok else "error", "summary": _sum})
                            if _sum:
                                _summaries.append(_sum)
                        final_text = "已执行操作：\n" + "\n".join(f"• {s}" for s in _summaries) if _summaries else "操作已完成。"
                        result = {"text": final_text, "steps": steps}
                        if reasoning:
                            result["reasoning"] = reasoning
                        return result
                    else:
                        # ⬆ 模型升级检测
                        logger.info("[UPGRADE] notool round=%d router=%s upgraded=%s", round_idx, self._model_router is not None, self._model_upgraded)
                        if not self._model_upgraded and self._model_router:
                            _has = any(s.get("type")=="tool_result" and s.get("status")=="success" for s in steps)
                            logger.info("[UPGRADE] has_tools=%s", _has)
                            if _has:
                                _cn = next((cn for cn,cc in self._model_router._models.items()
                                            if cc.model == self._primary_llm.model), None)
                                _uc = self._model_router.get_upgrade(_cn) if _cn else None
                                logger.info("[UPGRADE] cn=%s uc=%s", _cn, _uc.model if _uc else None)
                                if _uc:
                                    try:
                                        from memory.llm_service import LLMService
                                        _nu = LLMService(**_uc.to_llm_kwargs())
                                        _nu._get_client()
                                        self._upgrade_llm = _nu
                                        self._model_upgraded = True
                                        logger.info("[ToolCalling] ⬆ 已切换到更强模型，继续执行")
                                        continue
                                    except Exception as _e:
                                        logger.warning("[UPGRADE] ❌ 失败: %s", _e)

                    # 最终文本降级链：模型文本 > reasoning > 历史助手消息 > 工具步骤摘要
                    final_text = text_preview
                    if not final_text and reasoning:
                        final_text = reasoning
                    if not final_text:
                        final_text = self._get_last_assistant_text(working_messages)
                    if not final_text:
                        tool_summary = self._summarize_tool_steps(steps)
                        final_text = tool_summary or "（无法生成回复）"
                    result = {"text": final_text, "steps": steps}
                    if reasoning:
                        result["reasoning"] = reasoning
                    return result

                logger.info("[ToolCalling] LLM 返回 %d 个工具调用: %s",
                            len(tool_calls),
                            [tc.get("function", {}).get("name", "") for tc in tool_calls])

                assistant_msg = {"role": "assistant", "content": None, "tool_calls": []}
                tool_results = []
                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    func_name = tc.get("function", {}).get("name", "")
                    raw_args = tc.get("function", {}).get("arguments", "{}")
                    if isinstance(raw_args, str):
                        try:
                            raw_args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            logger.warning("[ToolCalling] %s 参数 JSON 解析失败，原始参数: %s",
                                           func_name, raw_args[:200])
                            # 尝试修复 LLM 常见的 JSON 格式错误
                            fixed = raw_args.strip()
                            # 单引号 → 双引号（LLM 经常用单引号代替双引号）
                            fixed = fixed.replace("'", '"')
                            # 未引号的 key → 加双引号: {key: → {"key":
                            fixed = re.sub(r'([\{,])\s*([a-zA-Z_]\w*)\s*:', r'\1"\2":', fixed)
                            # Python 布尔值/None → JSON 小写
                            fixed = fixed.replace("True", "true").replace("False", "false").replace("None", "null")
                            # 尾随逗号
                            fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
                            try:
                                raw_args = json.loads(fixed)
                                logger.info("[ToolCalling] %s 参数已通过修复解析成功: %s", func_name, raw_args)
                            except json.JSONDecodeError:
                                logger.error("[ToolCalling] %s 参数修复后仍然无法解析: %s",
                                             func_name, fixed[:200])
                                raw_args = {}

                    logger.info("[ToolCalling] LLM 调用工具: %s, 参数: %s", func_name, raw_args)

                    tc_entry = {
                        "id": tc_id,
                        "type": "function",
                        "function": {"name": func_name, "arguments": json.dumps(raw_args)},
                    }
                    assistant_msg["tool_calls"].append(tc_entry)

                    # 记录步骤：开始调用工具
                    steps.append({
                        "type": "tool_call",
                        "tool": func_name,
                        "args": raw_args,
                        "status": "running",
                    })
                    if on_step: on_step(steps[-1])

                    result = self._execute_safe(func_name, raw_args)

                    # 连续失败检测：同一工具连续出错则递增计数
                    result_ok = result.get("ok", False)
                    if result_ok or result.get("blocked"):
                        _consecutive_failures[func_name] = 0
                    elif not raw_args or not any(raw_args.values()):
                        # 工具被调用但参数为空或全空 → 可能是 LLM 工具调用缺陷
                        _consecutive_failures[func_name] = _consecutive_failures.get(func_name, 0) + 1
                        if _consecutive_failures[func_name] >= 2:
                            logger.warning("[ToolCalling] %s 连续 %d 次空参数调用失败，终止工具循环",
                                           func_name, _consecutive_failures[func_name])

                    # 记录步骤：工具返回结果
                    result_ok = result.get("ok", False)
                    result_summary = _summarize_tool_result(func_name, result)
                    steps.append({
                        "type": "tool_result",
                        "tool": func_name,
                        "status": "success" if result_ok else "error",
                        "summary": result_summary,
                    })
                    if on_step: on_step(steps[-1])

                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        # 限制工具结果大小，防止撑爆上下文限制（超过 8000 字符截断）
                        "content": self._truncate_tool_content(
                            json.dumps(_clean_for_json(result), ensure_ascii=False)
                        ),
                    })

                # 先添加 assistant 消息（含 tool_calls），再添加工具结果（OpenAI API 要求顺序）
                working_messages.append(assistant_msg)
                working_messages.extend(tool_results)

                # ⬆ 在多轮调用工具后升级模型（模型每轮返回 JSON tool_calls，不会触发
                # if not tool_calls 分支，所以必须在此处检查）
                if not self._model_upgraded and self._model_router:
                    _has = any(s.get("type")=="tool_result" and s.get("status")=="success" for s in steps)
                    logger.info("[UPGRADE] upgraded=%s has=%s router_models=%d", self._model_upgraded, _has, len(self._model_router._models))
                    if _has:
                        _cn = next((cn for cn,cc in self._model_router._models.items()
                                    if cc.model == self._primary_llm.model), None)
                        _uc = self._model_router.get_upgrade(_cn) if _cn else None
                        logger.info("[UPGRADE] cn=%s uc=%s", _cn, _uc.model if _uc else None)
                        if _uc:
                            try:
                                from memory.llm_service import LLMService
                                _nu = LLMService(**_uc.to_llm_kwargs())
                                _nu._get_client()
                                self._upgrade_llm = _nu
                                self._model_upgraded = True
                                logger.info("[UPGRADE] ✅ 升级: %s -> %s", self._primary_llm.model, _uc.model)
                            except Exception as _e:
                                logger.warning("[UPGRADE] ❌ 失败: %s", _e)

                # 检测连续空参数失败：某个工具连续 2+ 次被 LLM 以空参数调用
                # 不再直接终止循环，而是清除计数让 LLM 重新尝试，避免一次失败就放弃
                stuck_tools = [name for name, count in _consecutive_failures.items() if count >= 2]
                if stuck_tools:
                    logger.warning("[ToolCalling] 以下工具连续空参数调用失败: %s，清除计数继续重试", stuck_tools)
                    # 记录提示步骤但不终止循环
                    steps.append({
                        "type": "tool_stuck",
                        "tools": stuck_tools,
                        "summary": f"工具 {stuck_tools} 空参数调用失败，已重置计数继续尝试",
                    })
                    if on_step: on_step(steps[-1])
                    # 清除失败计数，给 LLM 再次尝试的机会
                    for name in stuck_tools:
                        _consecutive_failures[name] = 0

            steps.append({"type": "text", "content": "（达到最大工具调用轮次）"})
            if on_step: on_step(steps[-1])
            final_text = self._get_last_assistant_text(working_messages)
            if not final_text:
                tool_summary = self._summarize_tool_steps(steps)
                final_text = tool_summary or "（无法生成回复）"
            result = {"text": final_text, "steps": steps}
            # 尝试从最后响应中提取 reasoning
            if response and hasattr(response, "reasoning_content") and response.reasoning_content:
                result["reasoning"] = response.reasoning_content
            return result
        finally:
            # 取消超时定时器（防止定时器在任务完成后触发）
            if _timeout_timer:
                _timeout_timer.cancel()

    def _call_llm_with_tools(self, messages, system_prompt,
                              max_tokens, temperature, tool_defs):
        """调用 LLM（带工具定义）"""
        client = self._current_llm._get_client()

        if self._current_llm._is_openai_compat():
            return self._call_llm_openai(client, messages, system_prompt,
                                          max_tokens, temperature, tool_defs)
        else:
            return self._call_llm_anthropic(client, messages, system_prompt,
                                             max_tokens, temperature, tool_defs)

    def _call_llm_openai(self, client, messages, system_prompt,
                          max_tokens, temperature, tool_defs):
        """OpenAI 兼容格式的 LLM 调用"""
        api_messages = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        api_messages.extend(messages)

        kwargs = {
            "model": self._current_llm.model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tool_defs:
            kwargs["tools"] = tool_defs

        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message

    def _call_llm_anthropic(self, client, messages, system_prompt,
                             max_tokens, temperature, tool_defs):
        """Anthropic Messages API 调用（含工具）"""
        # 1. 转换工具定义：OpenAI → Anthropic
        anthropic_tools = []
        for td in (tool_defs or []):
            fn = td.get("function", td)
            anthropic_tools.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })

        # 2. 转换消息格式
        anthropic_messages = []
        merged_system = system_prompt

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")

            if role == "system":
                merged_system = (merged_system or "") + "\n" + (content or "")
                continue
            elif role == "tool":
                tc_id = msg.get("tool_call_id", "")
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tc_id,
                        "content": content or "",
                    }]
                })
            elif role == "assistant" and tool_calls:
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    func = tc.get("function", {})
                    raw_args = func.get("arguments", "{}")
                    if isinstance(raw_args, str):
                        try:
                            raw_args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            raw_args = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc_id,
                        "name": func.get("name", ""),
                        "input": raw_args,
                    })
                anthropic_messages.append({"role": "assistant", "content": blocks})
            else:
                anthropic_messages.append({"role": role, "content": content})

        # 3. 调用 API
        kwargs = {
            "model": self._current_llm.model,
            "messages": anthropic_messages,
            "max_tokens": max(2048, max_tokens),
            "temperature": temperature,
        }
        if merged_system:
            kwargs["system"] = merged_system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        response = client.messages.create(**kwargs)

        # 4. 转换响应回统一格式
        return self._anthropic_to_openai(response)

    def _anthropic_to_openai(self, response):
        """将 Anthropic Message 响应包装为 OpenAI 兼容格式

        Anthropic 的 content 包含 text + tool_use 块，
        转为 OpenAI message 的 tool_calls 对象列表格式。
        """
        from types import SimpleNamespace

        text_parts = []
        tool_call_objs = []

        for block in getattr(response, "content", []):
            btype = getattr(block, "type", "")
            if btype == "text":
                text_parts.append(getattr(block, "text", ""))
            elif btype == "tool_use":
                # 构造与 OpenAI tool_call 对象兼容的结构
                fn = SimpleNamespace()
                fn.name = getattr(block, "name", "")
                fn.arguments = json.dumps(getattr(block, "input", {}))
                tc = SimpleNamespace()
                tc.id = getattr(block, "id", "")
                tc.type = "function"
                tc.function = fn
                tool_call_objs.append(tc)

        wrapper = SimpleNamespace()
        wrapper.content = "".join(text_parts)
        wrapper.tool_calls = tool_call_objs if tool_call_objs else None
        wrapper.role = "assistant"
        return wrapper

    def _extract_tool_calls(self, response) -> list[dict]:
        """从 LLM 响应中提取 tool_calls"""
        if hasattr(response, "tool_calls"):
            calls = response.tool_calls
            if calls:
                return [
                    {
                        "id": c.id,
                        "function": {
                            "name": c.function.name,
                            "arguments": c.function.arguments,
                        },
                    }
                    for c in calls
                ]
        if isinstance(response, dict):
            return response.get("tool_calls") or []
        return []

    def _extract_reasoning(self, response) -> str | None:
        """从 LLM 响应中提取推理过程（reasoning_content），如 DeepSeek-R1 等模型提供"""
        if hasattr(response, "reasoning_content"):
            return response.reasoning_content or None
        if isinstance(response, dict):
            return response.get("reasoning_content") or response.get("reasoning") or None
        return None

    def _extract_text(self, response) -> str:
        """从 LLM 响应中提取文本内容"""
        if hasattr(response, "content"):
            return response.content or ""
        if isinstance(response, dict):
            return response.get("content", "") or response.get("text", "")
        return str(response)

    def _execute_safe(self, func_name: str, args: dict) -> dict:
        """安全执行工具（集成错误恢复 + 结果后处理）"""
        # 尝试使用 ErrorRecovery 工作流
        try:
            from agent.response_workflows import ErrorRecovery, ToolResultProcessor
            has_workflow = True
        except ImportError:
            has_workflow = False

        for attempt in range(3):
            try:
                result = tools.call(func_name, **args)
                if not isinstance(result, dict):
                    result = {"ok": True, "result": str(result)}
                if "ok" not in result:
                    result["ok"] = True

                # 后处理：格式化 + 压缩
                if has_workflow and result.get("ok"):
                    try:
                        ToolResultProcessor.compress_verbose(result)
                    except Exception:
                        pass

                return result
            except tools.ToolError as e:
                error_msg = str(e)
                # 用错误恢复工作流决定是否重试
                if has_workflow:
                    plan = ErrorRecovery.get_recovery_plan(error_msg, attempt)
                    if plan["should_retry"]:
                        logger.warning("[恢复] %s (尝试 %d/3, %.1fs 后重试)",
                                       plan["message"], attempt + 1, plan["delay"])
                        import time
                        time.sleep(plan["delay"])
                        continue
                return {"ok": False, "error": error_msg}
            except Exception as e:
                logger.error("工具 %s 执行异常: %s", func_name, e)
                if has_workflow:
                    plan = ErrorRecovery.get_recovery_plan(str(e), attempt)
                    if plan["should_retry"]:
                        import time
                        time.sleep(plan["delay"])
                        continue
                return {"ok": False, "error": f"工具执行异常: {e}"}

        return {"ok": False, "error": f"工具 {func_name} 执行失败（已重试 3 次）"}

    def _get_last_assistant_text(self, messages: list) -> str:
        """从消息列表中获取最后一条助手文本"""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return ""

    @staticmethod
    def _summarize_tool_steps(steps: list[dict]) -> str:
        """从工具执行步骤中生成摘要（当模型未返回文本时的备用回复）"""
        tool_calls_done = [s for s in steps if s.get("type") == "tool_result" and s.get("status") == "success"]
        if not tool_calls_done:
            return ""
        lines = ["以下是执行结果："]
        for s in tool_calls_done:
            tool_name = s.get("tool", "")
            summary = s.get("summary", "")
            if summary:
                lines.append(f"  • {tool_name}: {summary}")
        return "\n".join(lines)

    @staticmethod
    def _extract_xml_tool_calls(text: str) -> list[dict]:
        """从文本中提取 XML 格式的工具调用（DeepSeek 模型有时输出此格式）

        解析格式（支持命名空间前缀）：
          <tool_calls> 或 <dsml:tool_calls>
          <invoke name="tool_name"> 或 <dsml:invoke name="tool_name">
          <parameter name="param1">value1</parameter>
          <parameter name="param2">value2</parameter>
          </invoke>
          </tool_calls>

        Returns:
            list[dict]: 与 JSON tool_calls 兼容的格式
                        [{"id": "xml_0", "function": {"name": "...", "arguments": "{}"}}]
        """
        if not text:
            return []

        # 支持可选命名空间前缀: <prefix:tool_calls> 或 <tool_calls>
        import re as _re
        if not _re.search(r'<(?:\w+:)?tool_calls[\s>]', text):
            return []

        results = []
        # 匹配带可选命名空间的 <invoke name="xxx"> ... </invoke>
        pattern = r'<(?:\w+:)?invoke\s+name=["\']([^"\']+)["\']>(.*?)</(?:\w+:)?invoke>'
        for idx, (name, body) in enumerate(_re.findall(pattern, text, _re.DOTALL)):
            params = {}
            # 匹配带可选命名空间的 <parameter name="xxx" ...>value</parameter>
            for pname, pvalue in _re.findall(
                r'<(?:\w+:)?parameter\s+name=["\']([^"\']+)["\'][^>]*>(.*?)</(?:\w+:)?parameter>',
                body, _re.DOTALL
            ):
                params[pname.strip()] = pvalue.strip()
            results.append({
                "id": f"xml_{idx}",
                "function": {
                    "name": name.strip(),
                    "arguments": json.dumps(params, ensure_ascii=False),
                }
            })
        return results

    @staticmethod
    def _truncate_tool_content(content: str, max_chars: int = 3000) -> str:
        """截断过长的工具结果内容，防止撑爆 LLM 上下文窗口"""
        if len(content) <= max_chars:
            return content
        truncated = content[:max_chars]
        truncated += f'\n\n...（结果过长，已截断至 {max_chars} 字符）'
        return truncated


def _summarize_tool_result(tool_name: str, result) -> str:
    """生成工具执行结果的简短摘要"""
    # 防御：工具可能返回字符串而非 dict（如 _remember 校验失败）
    if not isinstance(result, dict):
        return str(result)[:200]
    if result.get("ok") is False:
        # 支持 error 和 message 两种键名（ExtensionManager 等使用 message）
        err_msg = result.get("error") or result.get("message") or ""
        if not err_msg:
            # 没有 error 信息时，尝试从 exit_code 推断
            exit_code = result.get("exit_code", result.get("code"))
            if exit_code is not None:
                err_msg = f"命令退出 (exit_code={exit_code})"
            else:
                err_msg = "未知错误"
        return f"执行失败: {err_msg}"
    if tool_name == "web_search":
        results_list = result.get("results", [])
        if results_list:
            items = []
            for r in results_list[:3]:  # 最多取3条
                title = r.get("title", "")
                snippet = r.get("snippet", "") or r.get("body", "") or ""
                item_parts = []
                if title:
                    item_parts.append(title[:60])
                if snippet:
                    item_parts.append(snippet[:120])
                if item_parts:
                    items.append(" · ".join(item_parts))
            if items:
                return f"找到 {len(results_list)} 条结果:\n" + "\n---\n".join(items)
            return f"找到 {len(results_list)} 条结果"
        return "未找到相关结果"
    if tool_name == "web_get":
        text = result.get("text", "")
        title = result.get("title") or result.get("parsed", {}).get("title", "")
        if title:
            return f"已获取页面: {title[:60]}"
        return f"已获取页面 ({len(text)} 字符)"
    if tool_name == "read_file":
        content = result.get("content", "") or result.get("result", "")
        return f"已读取文件 ({len(str(content))} 字符)"
    if result.get("result"):
        return str(result["result"])[:60]
    return f"执行成功"
