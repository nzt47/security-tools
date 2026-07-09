"""handoff_generator — 会话交接文档生成器

把当前会话用 LLM 压缩成结构化 Markdown 交接文档，保存到 OS 临时目录。
文档包含：会话摘要 / 关键上下文 / Suggested Skills / 脱敏声明。

设计原则:
    - 会话只读: 不修改 messages.jsonl
    - 单次 LLM 调用: 用 LLMService.chat()，不经过 DigitalLife.chat() 完整流程，不污染对话历史
    - 三处脱敏: 送入 LLM 前 / LLM 输出后 / 提取的上下文文本
    - 降级链: llm.chat → llm.summarize → 手动规则提取
"""

from __future__ import annotations

import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.utils.token_redactor import redact_recursive, redact_sensitive_tokens

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是交接文档生成器。基于以下对话历史，输出 Markdown，严格按此结构：\n"
    "## 目标\n（用户想完成什么）\n"
    "## 已完成\n（已达成的事项，用 bullet）\n"
    "## 待办\n（接下来要做的事，用 bullet）\n"
    "## 阻塞\n（当前卡点，无则写'无'）\n"
    "只基于给定对话，不编造。引用文件路径/commit 时保留原文。"
)

_MAX_MESSAGES_FOR_LLM = 500
_SUMMARY_PREVIEW_LEN = 500


def generate_handoff(state, session_id: Optional[str] = None,
                     intent: Optional[str] = None) -> Dict[str, Any]:
    """生成交接文档并写入 OS 临时目录。

    Args:
        state: ServerState 实例，需有 session_mgr 和 Yunshu 属性
        session_id: 目标会话 ID，None 取当前会话
        intent: 下一 session 用途描述，用于 skill 推荐；None 用摘要文本推断

    Returns:
        dict — {file_path, session_id, message_count, generated_at,
                summary_preview, skills_count, fallback_used}

    Raises:
        ValueError: 会话不存在或无消息
        RuntimeError: 文件写入失败
    """
    session_mgr = state.session_mgr
    if session_mgr is None:
        raise ValueError("session_mgr 未初始化")

    messages, meta = _gather_messages(session_mgr, session_id)
    sid = session_id or session_mgr.get_current_id()

    # 脱敏点 1: 送入 LLM 前对整个消息列表递归脱敏
    redacted_messages = redact_recursive(messages)
    llm_messages = [{"role": m.get("role", "user"), "content": m.get("content", "")}
                    for m in redacted_messages]

    llm = getattr(state.Yunshu, "_llm", None) if state.Yunshu else None
    summary, fallback_used = _llm_summarize(llm, llm_messages)

    # 脱敏点 2: LLM 输出后二次清洗
    summary = redact_sensitive_tokens(summary)

    context = _extract_context(messages)
    # 脱敏点 3: 提取的上下文文本清洗
    context = redact_sensitive_tokens(context)

    skills = _recommend_skills(intent, summary)

    generated_at = datetime.now(timezone.utc).isoformat()
    markdown = _render_markdown(meta, sid, len(messages), generated_at,
                                summary, context, skills)
    file_path = _write_temp_file(markdown, sid)

    return {
        "file_path": file_path,
        "session_id": sid,
        "message_count": len(messages),
        "generated_at": generated_at,
        "summary_preview": summary[:_SUMMARY_PREVIEW_LEN],
        "skills_count": len(skills),
        "fallback_used": fallback_used,
    }


def _gather_messages(session_mgr, session_id: Optional[str]
                     ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """取消息 + 会话 meta，空则抛 ValueError。"""
    sid = session_id or session_mgr.get_current_id()
    if not sid:
        raise ValueError("无当前会话，请指定 session_id 或先创建会话")

    meta = session_mgr.get_session(sid) or {}
    messages = session_mgr.get_messages(sid, limit=_MAX_MESSAGES_FOR_LLM)
    if not messages:
        raise ValueError(f"会话 {sid} 无消息，无法生成交接文档")
    return messages, meta


def _llm_summarize(llm, messages: List[Dict[str, str]]) -> Tuple[str, Optional[str]]:
    """调用 LLM 压缩会话，返回 (摘要文本, fallback标记)。

    降级链: llm.chat → llm.summarize → 手动规则提取
    """
    if llm is None:
        logger.warning("LLM 未配置，走手动规则提取降级")
        return _manual_extract(messages), "manual"

    # 首选: chat() with 结构化 system_prompt
    try:
        summary = llm.chat(
            messages=messages,
            system_prompt=_SYSTEM_PROMPT,
            max_tokens=800,
            temperature=0.3,
        )
        if summary and summary.strip():
            return summary.strip(), None
    except Exception as e:
        logger.warning("LLM.chat 结构化摘要失败: %s，尝试 summarize 降级", e)

    # 降级 1: summarize()
    try:
        summary = llm.summarize(messages, max_tokens=500)
        if summary and summary.strip():
            return summary.strip(), "summarize"
    except Exception as e:
        logger.warning("LLM.summarize 降级失败: %s，走手动规则提取", e)

    # 降级 2: 手动规则提取
    return _manual_extract(messages), "manual"


def _manual_extract(messages: List[Dict[str, str]]) -> str:
    """手动规则提取: 取前 2 条 user 消息 + 后 3 条消息拼接。"""
    user_msgs = [m for m in messages if m.get("role") == "user"]
    head = user_msgs[:2]
    tail = messages[-3:]

    lines = ["## 会话摘录（LLM 不可用，手动提取）", ""]
    for m in head:
        lines.append(f"- **用户**: {m.get('content', '')[:200]}")
    lines.append("")
    lines.append("**最近消息:**")
    for m in tail:
        role = m.get("role", "unknown")
        content = m.get("content", "")[:200]
        lines.append(f"- **{role}**: {content}")
    return "\n".join(lines)


def _extract_context(messages: List[Dict[str, Any]]) -> str:
    """正则提取文件路径/URL/commit hash/API 端点，去重。"""
    patterns = {
        "URL": re.compile(r"https?://[^\s)\"'\]>]+"),
        "文件路径": re.compile(r"(?:[a-zA-Z]:[\\/]|agent/|tests/|docs/)[\w\\/.-]+\.\w+"),
        "commit": re.compile(r"(?i)\bcommit\s+([0-9a-f]{7,40})\b"),
        "API 端点": re.compile(r"/api/[\w/-]+"),
    }

    found: Dict[str, set] = {k: set() for k in patterns}
    for m in messages:
        text = m.get("content", "")
        if not isinstance(text, str):
            continue
        for label, pat in patterns.items():
            for match in pat.findall(text):
                found[label].add(match)

    lines = []
    for label in ["URL", "文件路径", "commit", "API 端点"]:
        items = sorted(found[label])
        if items:
            lines.append(f"**{label}:**")
            for item in items:
                lines.append(f"- `{item}`")
            lines.append("")
    return "\n".join(lines).strip()


def _recommend_skills(intent: Optional[str], summary: str) -> List[Dict[str, Any]]:
    """调用 match_skills 推荐 top 3，异常时返回空列表。"""
    try:
        from agent.state_manager import get_skills_mgmt_service
        svc = get_skills_mgmt_service()
        query = intent or summary[:200] or "通用"
        result = svc.match_skills(query, top_k=3, enabled_only=True)
        return [
            {
                "skill_id": m.skill_id,
                "name": m.name,
                "description": m.description,
                "score": m.score,
                "category": m.category,
            }
            for m in result.matches
        ]
    except Exception as e:
        logger.warning("skill 推荐失败，跳过该章节: %s", e)
        return []


def _render_markdown(meta: Dict[str, Any], session_id: str, message_count: int,
                     generated_at: str, summary: str, context: str,
                     skills: List[Dict[str, Any]]) -> str:
    """拼接最终 Markdown。"""
    title = meta.get("title", "（无标题）")
    lines = [
        "# 会话交接文档",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        f"| session_id | `{session_id}` |",
        f"| 标题 | {title} |",
        f"| 消息数 | {message_count} |",
        f"| 生成时间 | {generated_at} |",
        "",
        "## 会话摘要",
        "",
        summary,
        "",
    ]

    if context:
        lines += ["## 关键上下文", "", context, ""]
    else:
        lines += ["## 关键上下文", "", "（未提取到文件路径/URL/commit/API 端点）", ""]

    if skills:
        lines += ["## Suggested Skills", ""]
        for s in skills:
            lines.append(f"- **{s['name']}** (`{s['skill_id']}`): {s['description']}")
        lines.append("")
    else:
        lines += ["## Suggested Skills", "", "（无可用推荐）", ""]

    lines += [
        "## 脱敏声明",
        "",
        "本文档已对 API keys / passwords / tokens 等敏感信息脱敏为 [REDACTED]。",
        f"如需原始信息，请查阅会话 `data/sessions/{session_id}/messages.jsonl`。",
        "",
        "---",
        "*由云枢 handoff 功能自动生成*",
    ]
    return "\n".join(lines)


def _write_temp_file(content: str, session_id: str) -> str:
    """写入 OS 临时目录，返回绝对路径。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_sid = re.sub(r"[^\w-]", "_", session_id)
    filename = f"yunshu_handoff_{safe_sid}_{ts}.md"
    file_path = Path(tempfile.gettempdir()) / filename
    try:
        file_path.write_text(content, encoding="utf-8")
        logger.info("handoff 文档已写入: %s", file_path)
        return str(file_path)
    except OSError as e:
        raise RuntimeError(f"写入临时文件失败: {e}") from e
