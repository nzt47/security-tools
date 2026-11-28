#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""云枢 ACP server（E4-5 第五批）：**stdio-only** 的 Agent Client Protocol 最小实现。

【为什么是 stdio、不是 HTTP】
  · 信任边界 = OS 进程：客户端（编辑器）自己拉起本脚本，stdin/stdout 就是它的管道；
  · 不新开监听面、不引入第二套令牌 —— 后端仍是既有 REST（同 E4-5 第二批 CLI），
    令牌只从 YUNSHU_API_TOKEN / 仓库 .env 的 FLASK_API_TOKEN 读，**永不打印**；
  · 回滚 = 不启动它（默认关：YUNSHU_ACP_ENABLED 不为真时脚本直接退出码 2）。

【协议】ACP v1 最小集（够编辑器完成一轮对话）：
  · initialize          → protocolVersion / agentCapabilities / agentInfo / authMethods=[]
  · session/new         → 在云枢建一个会话（标题带 cwd 目录名），回 {sessionId}
  · session/prompt      → 取 prompt 里的 text 块，调 POST /api/chat，先发 session/update
                          （agent_message_chunk）再回 {stopReason:"end_turn"}
  · session/cancel      → 标记取消；本轮结束返回 {stopReason:"cancelled"}
  未知方法回 -32601；坏 JSON 回 -32700（连接继续，不因一条坏消息退出）。

【不做】不做 session/load（无历史回放）、不做工具调用/权限请求（云枢自己审批）、
不做 MCP 转发（mcpServers 只接受并忽略）、不流式（/api/chat 是同步整轮）。

用法::

    set YUNSHU_ACP_ENABLED=1
    python scripts/yunshu_acp.py        # 客户端通过 stdin/stdout 说 JSON-RPC

退出码：0 正常结束（EOF）；2 未启用 / 参数错误。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

#: 我们实现的 ACP 主版本（单整数）
PROTOCOL_VERSION = 1
#: agentInfo 里的名字与版本（客户端展示用）
AGENT_NAME = "yunshu"
AGENT_TITLE = "云枢"
SERVER_VERSION = "1.0.0"
#: 单条 agent_message_chunk 的最大字符数（不把整段回答塞进一个通知）
MAX_CHUNK_CHARS = 4000
#: 默认一轮对话的等待秒数（LLM 可能很慢；只影响客户端超时）
DEFAULT_TURN_TIMEOUT = 600.0
#: 开关（默认关；只影响本脚本，不改服务端行为）
ENABLED_ENV = "YUNSHU_ACP_ENABLED"

#: JSON-RPC 错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_TRUE_WORDS = ("1", "true", "yes", "on")


def _enabled() -> bool:
    return str(os.environ.get(ENABLED_ENV, "") or "").strip().lower() in _TRUE_WORDS


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_cli():
    """复用第二批 CLI 的令牌/BaseURL/请求实现（按路径加载，scripts 不是包）。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yunshu_cli.py")
    spec = importlib.util.spec_from_file_location("yunshu_cli_shared", path)
    if spec is None or spec.loader is None:  # pragma: no cover 打包缺失时才会发生
        raise RuntimeError("找不到 yunshu_cli.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RestTransport:
    """默认传输层：走既有 REST（/api/sessions、/api/chat）。"""

    def __init__(self, timeout: float = DEFAULT_TURN_TIMEOUT):
        self.timeout = float(timeout)
        self._cli = None

    def _module(self):
        if self._cli is None:
            self._cli = _load_cli()
        return self._cli

    def _token(self) -> str:
        return self._module().token_from_env()

    def create_session(self, title: str) -> str:
        cli = self._module()
        data = cli.request_json("POST", "/api/sessions", payload={"title": title},
                                token=self._token(), timeout=30.0)
        session_id = str((data or {}).get("id") or "") if isinstance(data, dict) else ""
        if not session_id:
            raise RuntimeError("创建会话失败：响应里没有 id")
        return session_id

    def chat(self, session_id: str, text: str) -> str:
        cli = self._module()
        payload: Dict[str, Any] = {"message": text}
        if session_id:
            payload["session_id"] = session_id
        data = cli.request_json("POST", "/api/chat", payload=payload,
                                token=self._token(), timeout=self.timeout)
        if not isinstance(data, dict):
            raise RuntimeError("对话响应不是 JSON 对象")
        answer = data.get("Yunshu")
        if answer is None:
            answer = data.get("response")
        return str(answer or "")


def _write(outstream, payload: Dict[str, Any]) -> None:
    outstream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    try:
        outstream.flush()
    except (AttributeError, ValueError):  # pragma: no cover StringIO 没有 flush 也不该崩
        pass


def _error(outstream, msg_id: Any, code: int, message: str) -> None:
    _write(outstream, {"jsonrpc": "2.0", "id": msg_id,
                       "error": {"code": int(code), "message": str(message)}})


def _notify(outstream, method: str, params: Dict[str, Any]) -> None:
    _write(outstream, {"jsonrpc": "2.0", "method": method, "params": params})


def _chunks(text: str, size: int = MAX_CHUNK_CHARS) -> List[str]:
    if not text:
        return [""]
    return [text[i:i + size] for i in range(0, len(text), size)]


def _prompt_text(blocks: Any) -> str:
    """只取 text 块（initialize 里声明了 image/audio/embeddedContext 均不支持）。"""
    if not isinstance(blocks, (list, tuple)):
        return ""
    parts: List[str] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(p for p in parts if p).strip()


class Agent:
    """ACP 请求处理（与传输解耦，便于用假传输做契约测试）。"""

    def __init__(self, transport: Any, outstream: Any):
        self.transport = transport
        self.outstream = outstream
        self.sessions: Dict[str, str] = {}   # sessionId -> cwd
        self.cancelled: set = set()
        self._message_seq = 0

    # ── 请求 ──────────────────────────────────────────────────
    def handle_request(self, method: str, params: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[int, str]]]:
        if method == "initialize":
            return self._initialize(params), None
        if method == "session/new":
            return self._session_new(params), None
        if method == "session/prompt":
            return self._session_prompt(params)
        return None, (METHOD_NOT_FOUND, "未知方法：%s" % method)

    def handle_notification(self, method: str, params: Dict[str, Any]) -> None:
        if method == "session/cancel":
            session_id = str((params or {}).get("sessionId") or "")
            if session_id:
                self.cancelled.add(session_id)
        # 其它通知（客户端能力通知等）静默忽略：ACP 里通知不要求响应

    # ── 方法实现 ──────────────────────────────────────────────
    def _initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # 客户端请求的版本我们只支持 1：不支持时按 ACP 约定回「我支持的最新版本」
        version = PROTOCOL_VERSION
        return {
            "protocolVersion": version,
            "agentCapabilities": {
                "loadSession": False,
                "promptCapabilities": {"image": False, "audio": False, "embeddedContext": False},
                "mcpCapabilities": {"http": False, "sse": False},
            },
            "agentInfo": {"name": AGENT_NAME, "title": AGENT_TITLE, "version": SERVER_VERSION},
            "authMethods": [],
        }

    def _session_new(self, params: Dict[str, Any]) -> Dict[str, Any]:
        cwd = str(params.get("cwd") or "").strip()
        title = "ACP · " + (os.path.basename(cwd) or "会话")
        session_id = str(self.transport.create_session(title[:120]) or "")
        if not session_id:
            raise RuntimeError("创建会话失败")
        self.sessions[session_id] = cwd
        return {"sessionId": session_id}

    def _session_prompt(self, params: Dict[str, Any]):
        session_id = str(params.get("sessionId") or "")
        text = _prompt_text(params.get("prompt"))
        if not session_id:
            return None, (INVALID_PARAMS, "缺少 sessionId")
        if not text:
            return None, (INVALID_PARAMS, "prompt 里没有 text 内容")
        if session_id in self.cancelled:
            self.cancelled.discard(session_id)
            return {"stopReason": "cancelled"}, None
        try:
            answer = str(self.transport.chat(session_id, text) or "")
        except RuntimeError as exc:
            # 传输层已把异常脱敏成一句话；这里再兜一层，绝不回 traceback
            return None, (INTERNAL_ERROR, "云枢后端请求失败：%s" % str(exc)[:200])
        self._message_seq += 1
        message_id = "msg_%d" % self._message_seq
        for piece in _chunks(answer):
            _notify(self.outstream, "session/update", {
                "sessionId": session_id,
                "update": {"sessionUpdate": "agent_message_chunk",
                           "messageId": message_id,
                           "content": {"type": "text", "text": piece}},
            })
        if session_id in self.cancelled:
            self.cancelled.discard(session_id)
            return {"stopReason": "cancelled"}, None
        return {"stopReason": "end_turn"}, None


def serve(instream, outstream, transport: Any = None) -> int:
    """stdio 主循环：一行一条 JSON-RPC；EOF 正常退出。"""
    agent = Agent(transport if transport is not None else RestTransport(), outstream)
    for raw in instream:
        line = str(raw or "").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _error(outstream, None, PARSE_ERROR, "JSON 解析失败")
            continue
        if not isinstance(message, dict):
            _error(outstream, None, INVALID_REQUEST, "请求必须是 JSON 对象")
            continue
        method = message.get("method")
        if not method:
            # 客户端对我们请求的响应（本实现不发请求）→ 忽略
            continue
        params = message.get("params")
        if not isinstance(params, dict):
            params = {}
        has_id = "id" in message
        msg_id = message.get("id")
        if not has_id:
            agent.handle_notification(str(method), params)
            continue
        try:
            result, failure = agent.handle_request(str(method), params)
        except RuntimeError as exc:
            _error(outstream, msg_id, INTERNAL_ERROR, str(exc)[:200])
            continue
        except Exception:  # noqa: BLE001 兜底：单条请求失败不该带走整个连接
            _error(outstream, msg_id, INTERNAL_ERROR, "内部错误")
            continue
        if failure is not None:
            _error(outstream, msg_id, failure[0], failure[1])
            continue
        _write(outstream, {"jsonrpc": "2.0", "id": msg_id, "result": result})
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print("python scripts/yunshu_acp.py  # ACP v1 over stdio（需 YUNSHU_ACP_ENABLED=1）")
        return 0
    if not _enabled():
        print("ACP 未启用（%s=0）；设置后由客户端拉起本进程。" % ENABLED_ENV, file=sys.stderr)
        return 2
    stdin = sys.stdin
    stdout = sys.stdout
    try:
        if hasattr(stdin, "reconfigure"):
            stdin.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(stdout, "reconfigure"):
            stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover 某些宿主的标准流不可重配置
        pass
    return serve(stdin, stdout)


if __name__ == "__main__":
    sys.exit(main())
