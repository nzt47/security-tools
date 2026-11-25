"""会话智能的派生视图（E1-4c）：**轮次大纲 / 每轮统计 / 导出**

【为什么单独一层】大纲、统计、导出三件事都只依赖 `messages.jsonl` 这一份事实
（会话存储），不新增采集、不落第二份真相；路由只做"取消息 → 调这里 → 序列化"。
纯函数集中在本模块，便于契约测试（不碰 Flask、不碰磁盘）。

口径：
- **轮**：一条 `role=user` 开始一轮，直到下一条 user 之前都算这一轮
  （工具调用与助手正文都归属该轮）；会话不以 user 开头时补一个空轮；
- **工具名**：从助手消息的 `steps`（thinking 事件序列，title 形如「工具调用：goal」）、
  legacy `tool_steps` 与 `tool_calls` 里提取，去重保序；
- **token**：复用 `LLMMonitor.estimate_tokens`（同一口径；tiktoken 缺失时按 4 字符/token）；
- **上界**：只回最近 `MAX_ROUNDS` 轮；导出时单条正文截到 `MAX_EXPORT_CHARS` 并写
  「…（已截断）」，不静默丢；
- **文件名**：`session_<sha256(session_id)[:12]>.<ext>`（会话 ID 来自客户端，不直接进文件名）。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
import zipfile
from typing import Any, Dict, List, Optional

#: 大纲/统计最多回多少轮（更早的轮次对"快速回看"没价值，也会拖慢响应）
MAX_ROUNDS = 200
#: 单条正文在导出里最多保留多少字符
MAX_EXPORT_CHARS = 4_000
#: 大纲里每轮的「用户说了什么」「结论」各留多少字符
MAX_OUTLINE_CHARS = 160
#: 单轮最多列多少个工具名（去重后）
MAX_TOOLS_PER_ROUND = 20

_TOOL_TITLE_RE = re.compile(r"工具调用[：:]\s*([A-Za-z0-9_\-\.]+)")


def _plain(value: Any) -> str:
    """压平空白（换行/多空格 → 单空格）。"""
    return " ".join(str(value or "").split())


def tool_names(message: Dict[str, Any]) -> List[str]:
    """从一条助手消息里提取工具名（去重保序，纯函数）。"""
    names: List[str] = []
    for step in message.get("steps") or []:
        if not isinstance(step, dict):
            continue
        match = _TOOL_TITLE_RE.search(str(step.get("title") or ""))
        if match:
            names.append(match.group(1))
    for step in message.get("tool_steps") or []:
        if not isinstance(step, dict):
            continue
        name = step.get("tool") or step.get("name") or (step.get("tool_call") or {}).get("name")
        if name:
            names.append(str(name))
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict):
            name = call.get("name") or (call.get("function") or {}).get("name")
            if name:
                names.append(str(name))
    seen, out = set(), []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
        if len(out) >= MAX_TOOLS_PER_ROUND:
            break
    return out


def rounds(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按「user 开始一轮」切分，返回最近 MAX_ROUNDS 轮（含**全文**，调用方自行裁剪）。"""
    out: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    def _new_round(ts: str = "") -> Dict[str, Any]:
        return {"round": len(out) + 1, "user": "", "assistant": "",
                "tools": [], "ts": ts, "messages": 0,
                "user_chars": 0, "assistant_chars": 0}

    for msg in messages or []:
        role = str(msg.get("role") or "")
        text = str(msg.get("content") or "")
        if role == "user":
            if current is not None:
                out.append(current)
            current = _new_round(str(msg.get("timestamp") or ""))
            current["user"] = text
            current["user_chars"] = len(text)
            current["messages"] = 1
            continue
        if current is None:
            current = _new_round(str(msg.get("timestamp") or ""))
        current["messages"] = int(current["messages"]) + 1
        if role == "assistant":
            current["assistant_chars"] = int(current["assistant_chars"]) + len(text)
            if text.strip():
                current["assistant"] = (str(current["assistant"]) + "\n\n" + text).strip()
            for name in tool_names(msg):
                if name not in current["tools"]:
                    current["tools"].append(name)
    if current is not None:
        out.append(current)
    # 重新编号，保证截断后仍然连续
    kept = out[-MAX_ROUNDS:]
    for index, row in enumerate(kept, start=1):
        row["round"] = index
    return kept


def _tokens(text: str) -> int:
    try:
        from agent.llm_monitor import LLMMonitor

        return int(LLMMonitor.estimate_tokens(text))
    except Exception:  # noqa: BLE001 估算器不可用就按 4 字符/token（与 LLMMonitor 兜底一致）
        return len(text or "") // 4


def outline(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """轮次大纲：每轮的用户问题 / 工具名 / 结论一句。"""
    rows = rounds(messages)
    items = [{
        "round": row["round"],
        "user": _plain(row["user"])[:MAX_OUTLINE_CHARS],
        "tools": list(row["tools"]),
        "conclusion": _plain(row["assistant"])[:MAX_OUTLINE_CHARS],
        "ts": row["ts"],
        "messages": int(row["messages"]),
    } for row in rows]
    return {"rounds": items, "round_count": len(items),
            "truncated": len(messages or []) and len(rows) >= MAX_ROUNDS}


def stats(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """每轮统计 + 汇总（token 与 LLMMonitor 同口径）。"""
    rows = rounds(messages)
    per_round: List[Dict[str, Any]] = []
    tokens_in = tokens_out = tools_total = 0
    for row in rows:
        tin = _tokens(row["user"])
        tout = _tokens(row["assistant"])
        tokens_in += tin
        tokens_out += tout
        tools_total += len(row["tools"])
        per_round.append({
            "round": row["round"],
            "user_chars": int(row["user_chars"]),
            "assistant_chars": int(row["assistant_chars"]),
            "tools": len(row["tools"]),
            "tool_names": list(row["tools"]),
            "ts": row["ts"],
            "tokens_in": tin,
            "tokens_out": tout,
        })
    return {
        "round_count": len(per_round),
        "rounds": per_round,
        "totals": {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tools": tools_total,
            "rounds": len(per_round),
        },
        "truncated": len(messages or []) and len(rows) >= MAX_ROUNDS,
    }


def export_filename(session_id: str, ext: str = "md") -> str:
    """导出文件名（sha256 派生，不含会话原始字符）。"""
    digest = hashlib.sha256(str(session_id or "").encode("utf-8")).hexdigest()[:12]
    return "session_%s.%s" % (digest, str(ext or "md").lstrip("."))


def _clip(text: str, limit: int = MAX_EXPORT_CHARS) -> str:
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "\n\n…（已截断）"


def to_markdown(meta: Dict[str, Any], messages: List[Dict[str, Any]]) -> str:
    """整会话导出成 Markdown（按轮；超长正文截断并标注）。"""
    title = _plain(meta.get("title") or meta.get("id") or "会话")
    lines: List[str] = ["# %s" % title, ""]
    lines.append("- 会话 ID：`%s`" % (meta.get("id") or ""))
    for key, label in (("created_at", "创建时间"), ("updated_at", "最近更新"),
                       ("agent", "智能体"), ("project_id", "项目")):
        if meta.get(key):
            lines.append("- %s：%s" % (label, meta.get(key)))
    lines.append("- 导出时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("")

    for row in rounds(messages):
        lines.append("## 第 %d 轮" % row["round"])
        lines.append("")
        if row["ts"]:
            lines.append("_时间：%s_" % row["ts"])
            lines.append("")
        if str(row["user"]).strip():
            lines.append("**用户**：")
            lines.append("")
            lines.append(_clip(row["user"]))
            lines.append("")
        if row["tools"]:
            lines.append("**工具**：%s" % "、".join(row["tools"]))
            lines.append("")
        if str(row["assistant"]).strip():
            lines.append("**助手**：")
            lines.append("")
            lines.append(_clip(row["assistant"]))
            lines.append("")
    return "\n".join(lines)


def to_zip(meta: Dict[str, Any], messages: List[Dict[str, Any]]) -> bytes:
    """整会话导出成 ZIP：manifest.json + transcript.md + messages.jsonl（原始）。"""
    manifest = {
        "schema": "yunshu/session-export/v1",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session": {k: meta.get(k) for k in
                    ("id", "title", "title_source", "created_at", "updated_at",
                     "agent", "project_id", "message_count")},
        "stats": stats(messages)["totals"],
        "messages": len(messages or []),
    }
    raw = "\n".join(json.dumps(m, ensure_ascii=False) for m in (messages or []))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        zf.writestr("transcript.md", to_markdown(meta, messages))
        zf.writestr("messages.jsonl", raw)
    return buf.getvalue()


__all__ = ["MAX_ROUNDS", "MAX_EXPORT_CHARS", "MAX_OUTLINE_CHARS", "tool_names", "rounds",
           "outline", "stats", "export_filename", "to_markdown", "to_zip"]
