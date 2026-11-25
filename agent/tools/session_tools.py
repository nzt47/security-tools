"""E1-4d 会话检索工具：`session_search`（找回以前说过什么）与 `session_trace`（某轮工具轨迹）。

设计页 `docs/plan/2026-11-26-e1-4-session-intelligence-design.md` §4：
  · 两者都是**任务工具**（进检索索引），不是 META 交互原语（ask/present 那类）；
  · 只读本机会话索引/消息，`effect=read` ⇒ `permission_level=public`；
  · 返回**摘要**而不是全文：search 只给片段（≤200 字），trace 每项也只给 200 字
    —— 否则一句话就能把上下文窗口灌满。
"""
from __future__ import annotations

import logging
import re

import agent.tools as _tools
from agent.context import session_bind

logger = logging.getLogger(__name__)

#: search 的 limit 上限（模型侧；服务端 session_index 另有 50 的硬上限）
MAX_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT_DEFAULT = 5
#: 查询串上限（与 session_index.MAX_QUERY_CHARS 同量级）
MAX_QUERY_CHARS = 200
#: 每条命中片段 / 每个工具步骤摘要的字符上限
MAX_SNIPPET_CHARS = 200
#: trace 默认回最近几轮
DEFAULT_TRACE_ROUNDS = 3
#: trace 每轮最多列多少步骤
MAX_TRACE_STEPS = 20

_SNIPPET_MARK_RE = re.compile(r"[\[\]]")


def resolve_session() -> str:
    try:
        return session_bind.current()
    except Exception:  # noqa: BLE001 绑定层故障不得击穿工具链
        return ""


def _clip(text: str, limit: int = MAX_SNIPPET_CHARS) -> str:
    raw = " ".join(str(text or "").split())
    return raw if len(raw) <= limit else raw[:limit] + "…"


def run_session_search(query, limit=MAX_SEARCH_LIMIT_DEFAULT) -> dict:
    """按关键词检索历史会话消息（只返回片段与定位，不返回全文）。"""
    from agent.context import session_index as _idx

    text = _idx.normalize_query(query)
    if not text:
        return {"ok": False, "code": "empty_query", "error": "query 不能为空"}
    try:
        capped = max(1, min(MAX_SEARCH_LIMIT, int(limit or MAX_SEARCH_LIMIT_DEFAULT)))
    except (TypeError, ValueError):
        capped = MAX_SEARCH_LIMIT_DEFAULT
    doc = _idx.search(text, limit=capped)
    if not doc.get("ok"):
        return {"ok": False, "code": "index_unavailable",
                "error": doc.get("error") or "会话索引不可用"}
    hits = []
    for row in doc.get("hits") or []:
        hits.append({
            "session_id": row.get("session_id", ""),
            "seq": row.get("seq", 0),
            "role": row.get("role", ""),
            # snippet 里的 [] 是**高亮标记**，给模型看要剥掉
            "snippet": _clip(_SNIPPET_MARK_RE.sub("", str(row.get("snippet") or ""))),
        })
    return {"ok": True, "query": text, "mode": doc.get("mode", ""),
            "hits": hits, "count": len(hits), "took_ms": doc.get("took_ms", 0.0)}


def _manager():
    try:
        from app_server import _session_mgr

        return _session_mgr
    except Exception:  # noqa: BLE001 独立脚本/测试里没有 app_server
        return None


def run_session_trace(session_id="", round=0) -> dict:
    """某一轮（或最近几轮）的工具调用轨迹；每项摘要 ≤200 字。"""
    from agent.context import session_insight as _insight

    sid = str(session_id or "").strip() or resolve_session()
    if not sid:
        return {"ok": False, "code": "no_session",
                "error": "拿不到会话：请在会话里调用，或显式传 session_id"}
    mgr = _manager()
    if mgr is None:
        return {"ok": False, "code": "no_session_manager", "error": "会话管理器不可用"}
    try:
        messages = mgr.get_messages(sid, limit=_insight.MAX_ROUNDS * 8)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[session-trace] 读会话失败: %s", type(exc).__name__)
        return {"ok": False, "code": "read_failed", "error": "读取会话失败（详见服务日志）"}
    if not messages:
        return {"ok": False, "code": "not_found", "error": "会话不存在或还没有消息"}

    rows = _insight.rounds(messages)
    try:
        wanted = int(round or 0)
    except (TypeError, ValueError):
        wanted = 0
    if wanted > 0:
        picked = [r for r in rows if int(r.get("round") or 0) == wanted]
        if not picked:
            return {"ok": False, "code": "round_not_found",
                    "error": "第 %d 轮不存在（该会话共 %d 轮）" % (wanted, len(rows))}
    else:
        picked = rows[-DEFAULT_TRACE_ROUNDS:]

    out = []
    for row in picked:
        steps = []
        # 工具轨迹来自该轮的助手消息 steps（title 形如「工具调用：x」）
        for msg in messages:
            if str(msg.get("role") or "") != "assistant":
                continue
            for step in msg.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                title = str(step.get("title") or "")
                if "工具调用" not in title:
                    continue
                steps.append({"step": _clip(title, 60), "detail": _clip(step.get("detail"))})
                if len(steps) >= MAX_TRACE_STEPS:
                    break
            if len(steps) >= MAX_TRACE_STEPS:
                break
        out.append({
            "round": row.get("round"),
            "user": _clip(row.get("user")),
            "tools": list(row.get("tools") or []),
            "steps": steps,
            "rounds_back": len(rows) - int(row.get("round") or 0),
        })
    return {"ok": True, "session_id": sid, "round_count": len(rows),
            "returned": len(out), "rounds": out}


def register_all(dl=None) -> None:  # noqa: ARG001 与 ask/goal 同签名（生命周期管理器会传 self）
    @_tools.register(
        "session_search",
        "在**历史会话**里按关键词检索（本机已建索引的全部会话消息）。只返回命中片段与会话/轮次定位，"
        "不返回全文（避免把上下文灌爆）；limit 最多 10。用于「我上次说过/做过什么」的回忆。"
        "Search your past sessions by keyword; returns snippets only.",
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "关键词（≥3 字走全文索引；<3 字回落模糊匹配）"},
                "limit": {"type": "integer",
                          "description": "最多返回几条命中（默认 5，上限 10）"},
            },
            "required": ["query"],
        },
    )
    def _session_search_tool(**kwargs):
        return run_session_search(kwargs.get("query"), kwargs.get("limit"))

    @_tools.register(
        "session_trace",
        "查看**某一轮**的工具调用轨迹（工具名/参数摘要/结果摘要各取前 200 字）。给 round 就取该轮，"
        "不给就取最近 3 轮；session_id 缺省为当前会话。用于复盘「上一轮到底调了什么」。"
        "Show the tool-call trace of a round in a session.",
        schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string",
                               "description": "会话 id（缺省 = 当前会话）"},
                "round": {"type": "integer",
                          "description": "第几轮（从 1 开始；缺省 = 最近 3 轮）"},
            },
            "required": [],
        },
    )
    def _session_trace_tool(**kwargs):
        return run_session_trace(kwargs.get("session_id"), kwargs.get("round"))


__all__ = ["MAX_SEARCH_LIMIT", "MAX_SNIPPET_CHARS", "resolve_session",
           "run_session_search", "run_session_trace", "register_all"]
