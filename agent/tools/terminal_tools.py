"""terminal 工具（E3-1）：持久 PTY 会话的模型侧入口。

【为什么是**一个**工具 + action 枚举】路线图写的是 6 个工具（open/send/read/signal/close/list）。
做成 6 个会让工具索引 / 能力清单 / BM25 半饱和点 / 路由冲突 τ 连着改 6 次，而它们本来就是
"同一个会话对象的六个动作"——对模型也是一套语义。收成一个工具后连锁只发生 1 次；
schema 用 action 枚举表达，错误文案能就地说清"缺哪个参数"。

【默认关 + 受限档拒绝】真正判定在 agent/pty_session.py（唯一事实源）；工具层只做分派，
不再写第二份阈值/开关判断（否则改一处必漂移）。
"""
from __future__ import annotations

from typing import Any, Dict

from agent import tools as _tools


def register_all(*_args, **_kwargs) -> int:
    """注册 terminal 工具（与其它 *_tools.register_all 同形；返回注册个数）"""

    @_tools.register(
        "terminal",
        "持久终端（PTY）会话：把交互式程序当成长跑会话操作。action=open 开会话（默认当前平台 shell）、"
        "send 写 stdin（默认补换行）、read 取输出（offset/limit 以字符计）、signal 发 Ctrl-C 或终止、"
        "close 关闭、list 列出。需要 CP_TERMINAL_ENABLED=1，且权限预设必须是 full-access。"
        "Manage persistent PTY terminal sessions",
        schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["open", "send", "read", "signal", "close", "list"],
                    "description": "要做的动作",
                },
                "command": {
                    "type": "string",
                    "description": "action=open 时要启动的命令（缺省 cmd/bash）；按空格拆分，不支持管道与重定向",
                },
                "cwd": {"type": "string", "description": "action=open 时的工作目录（缺省当前目录）"},
                "rows": {"type": "integer", "description": "PTY 行数（默认 30）"},
                "cols": {"type": "integer", "description": "PTY 列数（默认 120）"},
                "id": {"type": "string", "description": "会话 id（open 返回；其余动作必填）"},
                "text": {"type": "string", "description": "action=send 时写入 stdin 的文本"},
                "newline": {
                    "type": "boolean",
                    "description": "action=send 时是否补一个换行（默认 true；向 REPL 发代码通常要）",
                },
                "offset": {
                    "type": "integer",
                    "description": "action=read 的起始字符偏移（用上次的 next_offset）",
                },
                "limit": {
                    "type": "integer",
                    "description": "action=read 本次最多返回的字符数（默认 20000，上限 60000）",
                },
                "signal": {
                    "type": "string",
                    "enum": ["interrupt", "terminate"],
                    "description": "action=signal 的动作：interrupt=Ctrl-C，terminate=结束进程树",
                },
            },
            "required": ["action"],
        })
    def _terminal(**kwargs) -> Dict[str, Any]:
        from agent import pty_session

        action = str(kwargs.get("action") or "").strip().lower()
        if action == "open":
            try:
                rows = int(kwargs.get("rows") or 30)
                cols = int(kwargs.get("cols") or 120)
            except (TypeError, ValueError):
                return {"ok": False, "error": "rows/cols 必须是整数"}
            return pty_session.open_session(
                command=str(kwargs.get("command") or ""),
                cwd=str(kwargs.get("cwd") or ""),
                rows=rows, cols=cols)
        if action == "list":
            return pty_session.list_sessions()

        session_id = str(kwargs.get("id") or "").strip()
        if not session_id:
            return {"ok": False,
                    "error": "action=%s 需要 id（open 返回值里的那个）" % (action or "?")}

        if action == "send":
            text = str(kwargs.get("text") or "")
            if kwargs.get("newline") is not False:
                # 【为什么是 \r 而不是 \n】PTY 里"回车"才是 Enter：Windows ConPTY 收 \n
                # 不认为一行结束（实测：子进程的 stdin 迭代拿不到整行）；POSIX 的
                # cooked 模式会把 \r 经 ICRNL 转成 \n。一条口径，两端都对。
                text += "\r"
            return pty_session.send(session_id, text)
        if action == "read":
            return pty_session.read(session_id,
                                    offset=kwargs.get("offset"),
                                    limit=kwargs.get("limit"))
        if action == "signal":
            return pty_session.signal(session_id, str(kwargs.get("signal") or "interrupt"))
        if action == "close":
            return pty_session.close(session_id)
        return {"ok": False,
                "error": "不支持的 action（可选 open/send/read/signal/close/list）"}

    return 1


__all__ = ["register_all"]
