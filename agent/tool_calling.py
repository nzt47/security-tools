"""ToolCallingService — LLM 工具调用编排引擎

接收 LLM 返回的 tool_calls，执行对应工具，将结果回注给 LLM，
在 LLM 返回纯文本时终止循环。
"""

import json
import logging
from typing import Any

from agent import tools

logger = logging.getLogger(__name__)


class ToolCallError(Exception):
    """工具调用异常"""
    pass


class ToolCallingService:
    """LLM 工具调用编排引擎"""

    def __init__(self, llm_service, max_rounds: int = 5, tool_timeout: int = 60):
        self._llm = llm_service
        self._max_rounds = max_rounds
        self._tool_timeout = tool_timeout
        self.last_steps: list[dict] = []

    def chat(self, messages: list[dict], system_prompt: str = "",
             max_tokens: int = 1024, temperature: float = 0.7,
             tools_whitelist: list[str] | None = None) -> str:
        """带工具调用的对话（返回纯文本，向后兼容）"""
        result = self.chat_with_steps(
            messages, system_prompt, max_tokens, temperature, tools_whitelist
        )
        return result["text"]

    def chat_with_steps(self, messages: list[dict], system_prompt: str = "",
                        max_tokens: int = 1024, temperature: float = 0.7,
                        tools_whitelist: list[str] | None = None) -> dict:
        """带工具调用的对话，返回文本和步骤

        Returns:
            {"text": str, "steps": [{"type": str, ...}]}
        """
        tool_defs = tools.get_tool_defs(whitelist=tools_whitelist)
        steps = []
        self.last_steps = steps
        logger.info("[ToolCalling] chat() 开始，工具定义数: %d, 消息数: %d",
                     len(tool_defs), len(messages))
        working_messages = list(messages)

        for round_idx in range(self._max_rounds + 1):
            need_tools = tool_defs if round_idx < self._max_rounds else None

            try:
                has_tools = need_tools is not None and len(need_tools) > 0
                logger.info("[ToolCalling] 第 %d 轮 LLM 调用，%s工具",
                            round_idx, f"带 {len(need_tools)} 个" if has_tools else "无")
                response = self._call_llm_with_tools(
                    working_messages, system_prompt,
                    max_tokens, temperature, need_tools
                )
            except Exception as e:
                logger.error("LLM 调用失败（第 %d 轮）: %s", round_idx, e)
                if round_idx == 0:
                    logger.warning("[ToolCalling] 首轮失败，降级为纯文本 LLM 调用")
                    try:
                        text = self._llm.chat(
                            messages, system_prompt=system_prompt,
                            max_tokens=max_tokens, temperature=temperature
                        )
                        steps.append({"type": "text", "content": "（使用基础对话模式）"})
                        return {"text": text, "steps": steps}
                    except Exception as fallback_e:
                        raise ToolCallError(
                            f"LLM 调用失败（已降级）: {fallback_e}"
                        ) from fallback_e
                return {"text": self._get_last_assistant_text(working_messages), "steps": steps}

            tool_calls = self._extract_tool_calls(response)
            text_preview = self._extract_text(response)

            if not tool_calls:
                logger.info("[ToolCalling] LLM 返回纯文本")
                if text_preview:
                    working_messages.append({"role": "assistant", "content": text_preview})
                return {"text": text_preview or self._get_last_assistant_text(working_messages), "steps": steps}

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
                        raw_args = {}

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

                result = self._execute_safe(func_name, raw_args)

                # 记录步骤：工具返回结果
                result_ok = result.get("ok", False)
                result_summary = _summarize_tool_result(func_name, result)
                steps.append({
                    "type": "tool_result",
                    "tool": func_name,
                    "status": "success" if result_ok else "error",
                    "summary": result_summary,
                })

                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

            # 先添加 assistant 消息（含 tool_calls），再添加工具结果（OpenAI API 要求顺序）
            working_messages.append(assistant_msg)
            working_messages.extend(tool_results)

        steps.append({"type": "text", "content": "（达到最大工具调用轮次）"})
        return {"text": self._get_last_assistant_text(working_messages), "steps": steps}

    def _call_llm_with_tools(self, messages, system_prompt,
                              max_tokens, temperature, tool_defs):
        """调用 LLM（带工具定义）"""
        client = self._llm._get_client()

        if self._llm._is_openai_compat():
            api_messages = []
            if system_prompt:
                api_messages.append({"role": "system", "content": system_prompt})
            api_messages.extend(messages)

            kwargs = {
                "model": self._llm.model,
                "messages": api_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if tool_defs:
                kwargs["tools"] = tool_defs

            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message
        else:
            raise NotImplementedError(
                "Anthropic tool calling 尚未实现。请使用 OpenAI 兼容的提供商。"
            )

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

    def _extract_text(self, response) -> str:
        """从 LLM 响应中提取文本内容"""
        if hasattr(response, "content"):
            return response.content or ""
        if isinstance(response, dict):
            return response.get("content", "") or response.get("text", "")
        return str(response)

    def _execute_safe(self, func_name: str, args: dict) -> dict:
        """安全执行工具"""
        try:
            result = tools.call(func_name, **args)
            if not isinstance(result, dict):
                return {"ok": True, "result": str(result)}
            return result
        except tools.ToolError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.error("工具 %s 执行异常: %s", func_name, e)
            return {"ok": False, "error": f"工具执行异常: {e}"}


def _summarize_tool_result(tool_name: str, result: dict) -> str:
    """生成工具执行结果的简短摘要"""
    if not result.get("ok", False):
        return f"执行失败: {result.get('error', '未知错误')}"
    if tool_name == "web_search":
        results_list = result.get("results", [])
        if results_list:
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

    def _get_last_assistant_text(self, messages: list) -> str:
        """从消息列表中获取最后一条助手文本"""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return "（无法生成回复）"
