"""Claude Code 本地记忆读取器（只读）。

三类素材（与只读盘点 SURVEY_20261024 的口径一致）：
1. 用户级 CLAUDE.md：按二级标题切段落 -> kind=preference（规则/偏好）；
2. history.jsonl：每行一条用户输入 -> kind=prompt；
3. projects/<project>/<session>.jsonl：会话转录 -> kind=message（只取 text 块）。

【为什么只取 text 块】实测 51.3 MB 转录里 60%（8.5 MB）是工具结果，
它们对"记住用户要什么"零贡献，却会把后续摘要与上下文撑爆；thinking 块同理。
工具结果/思考块**只计数不落记录**，让 preview 能如实报出"省了多少"。

【不易】一行一个 JSON，坏行很常见（实测 22,552 行里 1,084 行坏）：
坏行只计数不抛错；单行解析失败不应该让整次导入失败。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..models import (
    KIND_MESSAGE, KIND_PREFERENCE, KIND_PROMPT, ROLE_ASSISTANT, ROLE_NOTE,
    ROLE_USER, SOURCE_CLAUDE, MemoryRecord,
)

#: 单行上限（防止某一行的巨型工具结果把内存吃穿）
MAX_LINE_CHARS = 8 * 1024 * 1024
#: 单条消息正文上限（超长正文截断并记 meta；超长多半是日志堆栈）
MAX_MESSAGE_CHARS = 20000
#: 记录上限（防意外把整台机器的转录吃进内存）
MAX_RECORDS = 200000

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")


def default_claude_dir() -> Path:
    """Claude 配置目录（~/.claude）。

    【为什么不吃环境变量】读取位置已经由 CLI 的 --claude-dir / preview 的
    claude_dir 参数显式覆盖；再引一个 CLAUDE_CONFIG_DIR 会多出一个未登记的
    开关（云枢设置登记表的口径是"每个 env 读取点都要登记"），收益为零。
    """
    return Path.home() / ".claude"


def _iso(ts: Any) -> str:
    """时间戳归一：毫秒/秒数字、ISO 串都收；不可解析返回空串。"""
    if ts is None or ts == "":
        return ""
    if isinstance(ts, (int, float)):
        value = float(ts)
        if value > 1e11:  # 毫秒
            value /= 1000.0
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().replace(
                microsecond=0).isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    return str(ts)


def split_sections(text: str) -> List[Tuple[str, str]]:
    """按二级标题切段落 -> [(标题, 正文)]；无二级标题时整篇一节。"""
    sections: List[Tuple[str, str]] = []
    title = ""
    buf: List[str] = []
    for line in str(text or "").splitlines():
        match = _SECTION_RE.match(line)
        if match:
            body = "\n".join(buf).strip()
            if body:
                sections.append((title or "正文", body))
            title = match.group(1).strip()
            buf = []
        else:
            buf.append(line)
    body = "\n".join(buf).strip()
    if body:
        sections.append((title or "正文", body))
    return [(t, b) for t, b in sections if b]


def read_claude_md(path: Path) -> Tuple[List[MemoryRecord], Dict[str, Any]]:
    """CLAUDE.md -> 每节一条 preference 记录（跳过多余空节）。"""
    path = Path(path)
    report: Dict[str, Any] = {"path": str(path), "exists": path.is_file(),
                              "bytes": 0, "sections": [], "records": 0,
                              "warnings": []}
    if not path.is_file():
        report["warnings"].append("CLAUDE.md 不存在")
        return [], report
    raw = path.read_bytes()
    report["bytes"] = len(raw)
    text = raw.decode("utf-8", "replace")
    records: List[MemoryRecord] = []
    sections = split_sections(text)
    for index, (title, body) in enumerate(sections):
        if index == 0 and title == "正文":
            # 【口径】首个 H2 之前的引言：若只有一级标题，它只是文件名不是偏好
            first_line = body.splitlines()[0].strip() if body else ""
            if first_line.startswith("# ") and not first_line.startswith("## "):
                body = "\n".join(body.splitlines()[1:]).strip()
            title = "概述"
        if not body:
            continue
        report["sections"].append(title)
        records.append(MemoryRecord(
            source=SOURCE_CLAUDE, session_id="claude-md", ts="",
            role=ROLE_NOTE, text=f"## {title}\n{body}".strip(),
            kind=KIND_PREFERENCE, project="user",
            origin=str(path), meta={"section": title},
        ))
    report["records"] = len(records)
    return records, report


def read_history(path: Path, *, limit: int = 50000) -> Tuple[List[MemoryRecord], Dict[str, Any]]:
    """history.jsonl -> 用户输入历史（kind=prompt）。"""
    path = Path(path)
    report: Dict[str, Any] = {"path": str(path), "exists": path.is_file(),
                              "lines": 0, "records": 0, "bad_lines": 0,
                              "warnings": []}
    if not path.is_file():
        report["warnings"].append("history.jsonl 不存在")
        return [], report
    records: List[MemoryRecord] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            report["lines"] += 1
            if len(records) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                report["bad_lines"] += 1
                continue
            if not isinstance(obj, dict):
                report["bad_lines"] += 1
                continue
            text = str(obj.get("display") or obj.get("prompt") or "").strip()
            if not text:
                continue
            records.append(MemoryRecord(
                source=SOURCE_CLAUDE,
                session_id=str(obj.get("sessionId") or "claude-history"),
                ts=_iso(obj.get("timestamp")), role=ROLE_USER, text=text,
                kind=KIND_PROMPT, project=str(obj.get("project") or ""),
                origin=str(path),
            ))
    report["records"] = len(records)
    return records, report


def _blocks(message: Any) -> Tuple[str, Dict[str, int], int]:
    """消息 -> (text 拼接正文, 块类型计数, 工具块字符数)。"""
    kinds: Dict[str, int] = {}
    tool_chars = 0
    if isinstance(message, str):
        return message.strip(), kinds, tool_chars
    if not isinstance(message, dict):
        return "", kinds, tool_chars
    content = message.get("content")
    if isinstance(content, str):
        return content.strip(), kinds, tool_chars
    if not isinstance(content, list):
        return "", kinds, tool_chars
    texts: List[str] = []
    for block in content:
        if isinstance(block, str):
            texts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "")
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind == "text":
            piece = str(block.get("text") or "").strip()
            if piece:
                texts.append(piece)
        elif kind in ("tool_result", "tool_use"):
            tool_chars += len(json.dumps(block, ensure_ascii=False))
    return "\n\n".join(texts).strip(), kinds, tool_chars


@dataclass
class ClaudeReadResult:
    records: List[MemoryRecord] = field(default_factory=list)
    report: Dict[str, Any] = field(default_factory=dict)


def iter_transcript(path: Path, *, stats: Dict[str, Any],
                    max_records: int = MAX_RECORDS) -> Iterator[MemoryRecord]:
    """逐行读一份会话转录，产出 kind=message 记录（只取 text 块）。"""
    session_id = Path(path).stem
    project = Path(path).parent.name
    with Path(path).open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stats["lines"] = int(stats.get("lines", 0)) + 1
            if len(line) > MAX_LINE_CHARS:
                stats["bad_lines"] = int(stats.get("bad_lines", 0)) + 1
                continue
            if '"message"' not in line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                stats["bad_lines"] = int(stats.get("bad_lines", 0)) + 1
                continue
            if not isinstance(obj, dict):
                stats["bad_lines"] = int(stats.get("bad_lines", 0)) + 1
                continue
            kind = str(obj.get("type") or "")
            if kind not in ("user", "assistant"):
                continue
            message = obj.get("message")
            role = str((message or {}).get("role") if isinstance(message, dict) else kind) or kind
            # 【口径】"角色条目"与"有正文的消息"是两件事：前者含纯工具行，
            # 后者才是会被导入的量；两个数都给，避免把 16,942 行当成 16,942 条记忆
            stats["all_roles"][role] = stats["all_roles"].get(role, 0) + 1
            text, block_kinds, tool_chars = _blocks(message)
            for name, count in block_kinds.items():
                stats["block_kinds"][name] = stats["block_kinds"].get(name, 0) + count
            stats["tool_result_chars"] = int(stats.get("tool_result_chars", 0)) + tool_chars
            if not text:
                stats["empty_msgs"] = int(stats.get("empty_msgs", 0)) + 1
                continue
            stats["messages"] = int(stats.get("messages", 0)) + 1
            stats["roles"][role] = stats["roles"].get(role, 0) + 1
            truncated = False
            if len(text) > MAX_MESSAGE_CHARS:
                text = text[:MAX_MESSAGE_CHARS]
                truncated = True
            stats["chars"] = int(stats.get("chars", 0)) + len(text)
            ts = _iso(obj.get("timestamp"))
            if ts:
                if not stats.get("ts_first") or ts < stats["ts_first"]:
                    stats["ts_first"] = ts
                if not stats.get("ts_last") or ts > stats["ts_last"]:
                    stats["ts_last"] = ts
            yield MemoryRecord(
                source=SOURCE_CLAUDE, session_id=str(obj.get("sessionId") or session_id),
                ts=ts, role=ROLE_ASSISTANT if role == ROLE_ASSISTANT else ROLE_USER,
                text=text, kind=KIND_MESSAGE, project=project,
                origin=str(path),
                meta={"truncated": truncated, "cwd": str(obj.get("cwd") or "")},
            )
            if stats["messages"] >= max_records:
                stats["warnings"].append(f"达到记录上限 {max_records}")
                return


def read_claude(*, claude_dir: Optional[Path] = None, include_transcripts: bool = True,
                max_records: int = MAX_RECORDS) -> ClaudeReadResult:
    """读 Claude 全部本地记忆（CLAUDE.md + history + 会话转录）。"""
    root = Path(claude_dir) if claude_dir else default_claude_dir()
    report: Dict[str, Any] = {
        "root": str(root), "exists": root.is_dir(), "warnings": [],
        "claude_md": {}, "history": {},
        "sessions": {"files": 0, "lines": 0, "bad_lines": 0, "messages": 0,
                     "roles": {}, "all_roles": {}, "block_kinds": {}, "empty_msgs": 0,
                     "chars": 0, "tool_result_chars": 0, "ts_first": "",
                     "ts_last": "", "warnings": [], "projects": {}},
    }
    records: List[MemoryRecord] = []
    if not root.is_dir():
        report["warnings"].append(f"Claude 目录不存在: {root}")
        return ClaudeReadResult(records, report)

    md_records, md_report = read_claude_md(root / "CLAUDE.md")
    records.extend(md_records)
    report["claude_md"] = md_report

    hist_records, hist_report = read_history(root / "history.jsonl")
    records.extend(hist_records)
    report["history"] = hist_report

    if not include_transcripts:
        return ClaudeReadResult(records, report)

    sessions = report["sessions"]
    files = sorted(root.glob("projects/**/*.jsonl"))
    sessions["files"] = len(files)
    for path in files:
        project = path.parent.name
        sessions["projects"][project] = sessions["projects"].get(project, 0) + 1
        try:
            for record in iter_transcript(path, stats=sessions, max_records=max_records):
                records.append(record)
                if len(records) >= max_records:
                    break
        except OSError as exc:
            sessions["warnings"].append(f"读取失败（{path.name}）：{exc}")
        if len(records) >= max_records:
            sessions["warnings"].append(f"总记录达到上限 {max_records}，停止")
            break
    report["total_records"] = len(records)
    return ClaudeReadResult(records, report)


__all__ = [
    "ClaudeReadResult", "MAX_MESSAGE_CHARS", "default_claude_dir",
    "iter_transcript", "read_claude", "read_claude_md", "read_history",
    "split_sections",
]
