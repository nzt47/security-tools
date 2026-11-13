"""DeepSeek Harness（DSH）本地会话读取器（只读）。

DSH 把每个会话存成一份 zstd 压缩的 JSONL 事件流：
    ~/.dsh/sessions/<project-slug>/<session-id>/session.jsonl.zstd

它不是"一问一答两行"，而是**事件流**（reasoning-chunks / tool-call-chunks /
assistant/chunk / tool/call / tool/result / user/message / assistant/message ...）。
本读取器只取两类事件，其余全部跳过：

- user/message      -> 且 data.source.kind == "user"（**真人输入**；
                        plugin / skill-catalog / notice / relay 都是系统注入）
- assistant/message -> 只取 content 里 type == "text" 的块（tool-call / thinking 丢弃）

【为什么必须过滤 source.kind】实测 60 份会话里真人输入 60 条，而系统注入的
user/message 有 90+ 条（system prompt 快照、技能目录、后台作业通知、子代理回传）。
不过滤就会把"系统提示词"当成用户说的话写进云枢。

【口径】只读源文件：不移动、不改写、不建索引文件、不打开写句柄；
坏行只计数；单条正文按 MAX_MESSAGE_CHARS 截断并标记。
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import zstandard

from ..models import (
    ROLE_ASSISTANT, ROLE_USER, SOURCE_DSH, TranscriptMessage, TranscriptSession,
)

#: 单行上限（防御异常大的 reasoning 行把内存吃穿）
MAX_LINE_CHARS = 8 * 1024 * 1024
#: 单条正文上限（与 claude 读取器同口径）
MAX_MESSAGE_CHARS = 20000
#: 单会话消息上限（正常会话远小于此；防"一个会话几万步"）
MAX_MESSAGES = 100000
#: 只读前多少行做来源探测（include_subagents=False 时需要先知道 origin）
PEEK_LINES = 40

#: 只解析这几类事件；其余事件行连 json.loads 都不做（实测能省掉 80% 解析）。
#: 【不易】不能写成 '"type":"user/message"'：DSH 真实文件是紧凑格式，
#: 但测试/其他写入方可能用带空格的 json.dumps 默认格式 —— 按事件名匹配两种都认。
_EVENT_MARKERS = (
    "user/message", "assistant/message", "session/title",
    '"type":"session"', '"type": "session"',
)


def default_dsh_root() -> Path:
    """DSH 会话根目录（~/.dsh/sessions）。"""
    return Path.home() / ".dsh" / "sessions"


def iter_session_files(root: Optional[Path] = None) -> List[Path]:
    """列出全部 session.jsonl.zstd（按修改时间新 -> 旧）。"""
    base = Path(root) if root else default_dsh_root()
    if not base.is_dir():
        return []
    files = list(base.glob("*/*/session.jsonl.zstd"))
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
    return files


def _iso_ms(value: Any) -> str:
    """事件 time（毫秒 epoch）-> 本地 ISO 秒级；不可解析返回空串。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    if number > 1e11:  # 毫秒
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).astimezone().replace(
            microsecond=0).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _text_blocks(content: Any) -> str:
    """content（字符串或块列表）-> 只拼 text 块，块间双换行。"""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    texts: List[str] = []
    for block in content:
        if isinstance(block, str):
            piece = block.strip()
        elif isinstance(block, dict) and str(block.get("type") or "") == "text":
            piece = str(block.get("text") or "").strip()
        else:
            piece = ""
        if piece:
            texts.append(piece)
    return "\n\n".join(texts).strip()


def peek_origin(path: Path) -> str:
    """只读前若干行取 origin（top / subagent）；读不到返回空串（不猜）。"""
    try:
        with Path(path).open("rb") as fh:
            reader = zstandard.ZstdDecompressor().stream_reader(fh)
            text = io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
            for index, line in enumerate(text):
                if index >= PEEK_LINES:
                    break
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("type") == "session":
                    return str(obj.get("origin") or "top")
    except (OSError, ValueError, TypeError):
        return ""
    return ""


def read_session(path: Path, *, max_messages: int = MAX_MESSAGES) -> Tuple[TranscriptSession, Dict[str, int]]:
    """流式读一份会话 -> (TranscriptSession, 统计)。只读，不写任何文件。"""
    path = Path(path)
    stats: Dict[str, int] = {
        "lines": 0, "bad_lines": 0, "injected_user_messages": 0,
        "empty_messages": 0, "truncated_messages": 0, "capped": 0,
        "user_messages": 0, "assistant_messages": 0,
    }
    session_id = path.parent.name
    project = path.parent.parent.name
    title = ""
    origin = ""
    meta: Dict[str, Any] = {}
    messages: List[TranscriptMessage] = []
    index = 0

    with path.open("rb") as fh:
        reader = zstandard.ZstdDecompressor().stream_reader(fh)
        text_stream = io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
        for line in text_stream:
            stats["lines"] += 1
            if len(line) > MAX_LINE_CHARS:
                stats["bad_lines"] += 1
                continue
            if not any(marker in line for marker in _EVENT_MARKERS):
                continue
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                stats["bad_lines"] += 1
                continue
            if not isinstance(obj, dict):
                stats["bad_lines"] += 1
                continue
            kind = str(obj.get("type") or "")
            if kind == "session":
                session_id = str(obj.get("id") or session_id)
                origin = str(obj.get("origin") or "top")
                meta = {
                    "cwd": str(obj.get("cwd") or ""),
                    "agent_preset": str(obj.get("agentPreset") or ""),
                    "parent_session": str(obj.get("parentSession") or ""),
                    "created_at_ms": obj.get("createdAt") or "",
                }
                continue
            if kind == "session/title":
                data = obj.get("data") or {}
                if isinstance(data, dict):
                    title = str(data.get("title") or title)
                continue
            data = obj.get("data")
            if not isinstance(data, dict):
                stats["bad_lines"] += 1
                continue
            if kind == "user/message":
                source = data.get("source") or {}
                if not isinstance(source, dict) or str(source.get("kind") or "") != "user":
                    stats["injected_user_messages"] += 1
                    continue
                body = _text_blocks(data.get("content"))
                role = ROLE_USER
            else:  # assistant/message
                message = data.get("message") or {}
                if not isinstance(message, dict):
                    stats["bad_lines"] += 1
                    continue
                body = _text_blocks(message.get("content"))
                role = ROLE_ASSISTANT
            if not body:
                stats["empty_messages"] += 1
                continue
            truncated = False
            if len(body) > MAX_MESSAGE_CHARS:
                body = body[:MAX_MESSAGE_CHARS]
                truncated = True
                stats["truncated_messages"] += 1
            index += 1
            messages.append(TranscriptMessage(
                role=role, content=body, ts=_iso_ms(obj.get("time")),
                truncated=truncated, index=index,
            ))
            stats["user_messages" if role == ROLE_USER else "assistant_messages"] += 1
            if len(messages) >= max_messages:
                stats["capped"] += 1
                break

    session = TranscriptSession(
        source=SOURCE_DSH, session_id=session_id, project=project,
        title=title, origin=origin or "top", messages=messages, meta=meta,
    )
    return session, stats


__all__ = [
    "MAX_LINE_CHARS", "MAX_MESSAGE_CHARS", "PEEK_LINES",
    "default_dsh_root", "iter_session_files", "peek_origin", "read_session",
]
