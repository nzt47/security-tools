"""子代理侧工具循环（S5-3）—— 放开 §5.7 收口：分身**自己执行**工具，母体只审计

【为什么需要它】§5.7 机制 3 原先的做法是"分身**声明**记忆读写、母体（DelegationExecutor
+ agent/subagent/scoped_memory_gate.py）**代执行**"——根因是**分身没有工具执行循环**。
本模块把这条循环补上：给定一个**支持工具调用**的 LLM（`chat_stream(tools=, on_tool_call=)`）
与一张**授权工具白名单**，循环执行"模型出 tool_calls → 本地执行 → 回填结果"，直到模型
不再调用工具或到达轮次上限。于是 §5.7 的"代执行"不再是必要条件：母体可以把**已授权**
的工具定义交给分身，由分身自己跑，母体只对结果与审计负责。

【边界（不假装，四条）】
  1. **白名单是硬边界**：只有 `allowed` 内的工具会被执行；白名单外一律拒绝并记
     `E_TOOL_NOT_ALLOWED`，**绝不触达后端**。白名单为空 = 一个都不执行（fail-closed）。
  2. **不支持工具调用就如实失败**：LLM 无 `tools`/`on_tool_call` 形参 ⇒ 返回
     `E_TOOL_LOOP_UNSUPPORTED`，**不静默退化成单轮**（由调用方决定是否回退到旧路径）。
  3. **绝不抛**：任何异常收敛成 `ok=False + error_code`，与分身主路径的 fail-soft 一致。
  4. **审计只记标识**：每个工具写一条 `(tool, ok, error_code, duration_ms)`，
     **不含参数与结果正文**（正文可能含记忆内容）。

【与母体的分工】母体（DelegationExecutor）决定"给哪些工具"（授权子集 + automation 档位），
把它写进 task_file 的 `tool_defs` / `allowed_tools`；分身侧只负责**执行**与**如实回报**
`tool_calls`。谁执行并不改变"越权不可执行"：白名单外仍拒。
"""
from __future__ import annotations

import inspect
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "E_TOOL_LOOP_UNSUPPORTED",
    "E_TOOL_NOT_ALLOWED",
    "E_TOOL_LOOP_LLM_FAILED",
    "MAX_TURNS_HARD",
    "ToolLoopOutcome",
    "llm_supports_tools",
    "run_tool_loop",
]

#: LLM 适配器不支持工具调用（缺 tools / on_tool_call 形参）
E_TOOL_LOOP_UNSUPPORTED = "E_TOOL_LOOP_UNSUPPORTED"
#: 模型请求了授权白名单之外的工具（拒绝执行）
E_TOOL_NOT_ALLOWED = "E_TOOL_NOT_ALLOWED"
#: LLM 调用本身失败
E_TOOL_LOOP_LLM_FAILED = "E_TOOL_LOOP_LLM_FAILED"

#: 轮次硬上限（防止"模型无限调用工具"；调用方的 max_turns 只可更小）
MAX_TURNS_HARD = 12


@dataclass
class ToolLoopOutcome:
    """工具循环的可判定结果（`to_dict` 里**不含正文**，供审计/回执）"""

    ok: bool
    text: str = ""
    turns: int = 0
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    error_code: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "turns": self.turns,
            "tool_calls": [dict(x) for x in self.tool_calls],
            "error_code": self.error_code,
            "error": self.error,
            "text_chars": len(self.text or ""),
        }


def llm_supports_tools(llm: Any) -> bool:
    """LLM 适配器是否支持工具调用（`chat_stream` 带 `tools` 或 `on_tool_call` 形参）

    【为什么用形参判定而不是 try-call】本地 Ollama 适配器只有
    `chat_stream(messages, system_prompt)` —— 真去调用只会得到一个**静默忽略 tools**
    的普通回答，看起来"模型没调用工具"，掩盖了"这条路根本不通"。形参判定把"不支持"
    变成**可提前判定的事实**，调用方据此回退到旧路径。
    """
    stream = getattr(llm, "chat_stream", None)
    if not callable(stream):
        return False
    try:
        params = inspect.signature(stream).parameters
    except (TypeError, ValueError):
        return False
    return "tools" in params or "on_tool_call" in params


def _default_execute(name: str, args: Dict[str, Any]) -> Any:
    """默认执行器：走工具分发唯一汇聚点 `agent.tools.call`（惰性导入）"""
    from agent.tools import call as _call

    return _call(name, **args)


def _emit_audit(audit: Optional[Callable[..., None]], tool: str, ok: bool,
                error_code: str, duration_ms: float) -> None:
    """写一条工具执行审计（调用方缺失/异常均静默，不影响循环）"""
    if audit is None:
        return
    try:
        audit(tool, ok, error_code, duration_ms)
    except Exception as e:  # noqa: BLE001 审计失败不阻断执行（fail-soft）
        logger.debug("[tool_loop] 审计写入失败: %s", e)


def _parse_args(args_json: Any) -> Dict[str, Any]:
    """模型给的 `arguments` 字符串 → dict；非对象/坏 JSON ⇒ 空 dict（不猜）"""
    if isinstance(args_json, Mapping):
        return dict(args_json)
    text = args_json if isinstance(args_json, str) else ""
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}
    if isinstance(parsed, Mapping):
        return dict(parsed)
    return {"args": parsed}


def run_tool_loop(
    llm: Any,
    *,
    messages: List[Dict[str, Any]],
    system_prompt: str = "",
    tool_defs: Any = None,
    allowed: Optional[Sequence[str]] = None,
    execute: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    max_turns: int = 4,
    audit: Optional[Callable[..., None]] = None,
) -> ToolLoopOutcome:
    """执行"模型出 tool_calls → 本地执行 → 回填"循环（**绝不抛**）

    Args:
        llm: 支持工具调用的适配器（`chat_stream(messages, system_prompt, tools,
            on_tool_call)`）。
        messages: 初始消息（**就地追加** assistant/tool 消息，供多轮）。
        system_prompt: 系统提示（每次调用都带）。
        tool_defs: 下发给模型的工具定义（OpenAI 风格）。
        allowed: 允许执行的工具名集合；**空/None ⇒ 一个都不执行**（fail-closed）。
        execute: 工具执行器（默认 `agent.tools.call`）；便于测试注入。
        max_turns: 轮次上限（内部再被 `MAX_TURNS_HARD` 夹住）。
        audit: `(tool, ok, error_code, duration_ms) -> None` 审计回调。

    Returns:
        `ToolLoopOutcome`。`ok=False` 只在"不支持工具调用/LLM 失败"时；工具本身失败
        不影响 `ok`（如实记在 `tool_calls` 里）。
    """
    if not llm_supports_tools(llm):
        return ToolLoopOutcome(
            ok=False, error_code=E_TOOL_LOOP_UNSUPPORTED,
            error="LLM 适配器不支持工具调用（chat_stream 缺 tools/on_tool_call 形参）")

    allow = {str(x).strip() for x in (allowed or ()) if str(x or "").strip()}
    cap = max(1, min(int(max_turns), MAX_TURNS_HARD))
    execute = execute or _default_execute
    recorded: List[Dict[str, Any]] = []
    turns = 0
    text = ""

    while turns < cap:
        turns += 1
        collected: List[Any] = []
        parts: List[str] = []

        def _on_tool_call(name: Any, args_json: Any, reasoning: Any = "") -> None:
            collected.append((str(name or ""), args_json, reasoning))

        try:
            for piece in llm.chat_stream(
                    messages=messages, system_prompt=system_prompt,
                    tools=tool_defs, on_tool_call=_on_tool_call):
                if piece:
                    parts.append(str(piece))
        except Exception as e:  # noqa: BLE001 LLM 失败 ⇒ 如实失败，绝不抛
            return ToolLoopOutcome(
                ok=False, text="".join(parts), turns=turns, tool_calls=recorded,
                error_code=E_TOOL_LOOP_LLM_FAILED,
                error="%s: %s" % (type(e).__name__, e))

        text = "".join(parts)
        if not collected:
            # 模型不再调用工具 ⇒ 正常收尾（本轮文本即最终回答）
            return ToolLoopOutcome(ok=True, text=text, turns=turns,
                                   tool_calls=recorded)

        # 组装 assistant 消息（含 tool_calls），并逐个执行 + 回填
        calls_msg: List[Dict[str, Any]] = []
        for idx, (name, args_json, _reasoning) in enumerate(collected):
            calls_msg.append({
                "id": "call_%d_%s" % (idx, name or "unknown"),
                "type": "function",
                "function": {"name": name, "arguments":
                             args_json if isinstance(args_json, str) else json.dumps(
                                 args_json or {}, ensure_ascii=False)},
            })
        messages.append({"role": "assistant", "content": "", "tool_calls": calls_msg})

        for call, (name, args_json, _reasoning) in zip(calls_msg, collected):
            started = time.time()
            if not allow or name not in allow:
                result: Dict[str, Any] = {
                    "ok": False, "error_code": E_TOOL_NOT_ALLOWED,
                    "error": "工具 %s 不在分身授权白名单内（已拒绝，未触达后端）" % name,
                }
                _emit_audit(audit, name, False, E_TOOL_NOT_ALLOWED,
                            (time.time() - started) * 1000.0)
            else:
                try:
                    raw = execute(name, _parse_args(args_json))
                except Exception as e:  # noqa: BLE001 工具异常如实回填
                    result = {"ok": False, "error_code": type(e).__name__,
                              "error": "%s: %s" % (type(e).__name__, e)}
                else:
                    if isinstance(raw, Mapping):
                        result = dict(raw)
                    else:
                        result = {"ok": True, "result": raw}
                ok = bool(result.get("ok", True))
                _emit_audit(audit, name, ok, str(result.get("error_code") or ""),
                            (time.time() - started) * 1000.0)
            recorded.append({"name": name, "ok": bool(result.get("ok", True)),
                             "error_code": str(result.get("error_code") or "")})
            messages.append({
                "role": "tool", "tool_call_id": call["id"],
                "content": json.dumps(result, ensure_ascii=False, default=str)[:4000],
            })

    # 轮次耗尽：如实返回（不是失败——模型还在调工具，调用方按 text 决定）
    return ToolLoopOutcome(ok=True, text=text, turns=turns, tool_calls=recorded)
