"""会话标题自动生成（E1-4a）

【解决什么】会话列表里全是「会话 11-26 00:12」这类占位标题，历史一多就认不出来。
本模块在**一轮结束后**（不阻塞对话）用 LLM 起一个 ≤24 字的短标题。

【口径】
- **旁路**：在守护线程里跑，8s 超时；任何失败都只 WARN，对话不受影响；
- **不覆盖人工标题**：`title_source == "manual"` 永不被改写；已 `auto` 的不重复生成；
  只有 `placeholder`（创建占位）/`fallback`（上次没拿到模型）才允许再试；
- **可回退**：LLM 不可用时用**首条用户消息**压平成标题，来源记 `fallback`（下轮还会重试）；
- **可关闭**：`CP_SESSION_TITLE_ENABLED=0` ⇒ 一条都不生成（只保留占位/人工标题）。
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ENABLED_ENV = "CP_SESSION_TITLE_ENABLED"

#: 至少几条用户消息（= 几轮）才开始起标题：1 轮时信息太少，容易起出"你好"这种标题
MIN_USER_MESSAGES = 2
#: 标题上限（字符）：会话列表一行放得下
MAX_TITLE_CHARS = 24
#: 送给模型的上下文上限：取最近若干条消息、总字符截断
MAX_CONTEXT_MESSAGES = 6
MAX_INPUT_CHARS = 1200
#: 模型回复上限与超时（标题很短，32 token 足够）
MAX_TITLE_TOKENS = 32
TIMEOUT_SEC = 8

#: 允许自动改写的来源（manual = 人工锁定，auto = 已生成）
GENERATABLE_SOURCES = frozenset({"placeholder", "fallback", ""})

_STRIP_PREFIXES = ("标题：", "标题:", "会话标题：", "会话标题:", "title:", "Title:", "TITLE:")
_STRIP_CHARS = "\"'“”‘’《》「」『』\[\]()（） \t\r\n"

SYSTEM_PROMPT = (
    "你给对话起标题。只输出标题本身：不超过 12 个汉字（或 24 个字符），"
    "不要引号、不要句号、不要「标题：」前缀，不要解释。"
)


def enabled() -> bool:
    raw = str(os.environ.get("CP_SESSION_TITLE_ENABLED", "1") or "").strip().lower()
    return raw not in ("0", "false", "no", "off", "disable", "disabled")


def _plain(text: str) -> str:
    """压平空白（换行/多空格 → 单空格）"""
    return " ".join(str(text or "").split())


def sanitize_title(raw: str, limit: int = 0) -> str:
    """把模型输出收拾成可展示的标题（纯函数）。

    - 只留第一行，压平空白；
    - 去首尾引号/括号/空白（**先剥**，否则「会话标题：…」被引号包住时前缀剥不到）；
    - 去掉「标题：」这类前缀（剥完再检查一次）；
    - 截断到 limit（默认 MAX_TITLE_CHARS）。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    text = _plain(text.splitlines()[0])
    text = text.strip(_STRIP_CHARS)
    for _ in range(2):  # 允许「「标题：xxx」」这种两层包裹
        changed = False
        for prefix in _STRIP_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):].strip(_STRIP_CHARS)
                changed = True
                break
        if not changed:
            break
    cap = int(limit or MAX_TITLE_CHARS)
    if len(text) > cap:
        text = text[:cap].rstrip(_STRIP_CHARS)
    return text


def fallback_title(messages: List[Dict[str, Any]]) -> str:
    """LLM 不可用时的标题：首条用户消息压平截断（纯函数）。"""
    for msg in messages or []:
        if str(msg.get("role") or "") != "user":
            continue
        text = sanitize_title(msg.get("content") or "")
        if text:
            return text
    return ""


def should_generate(meta: Optional[Dict[str, Any]], messages: List[Dict[str, Any]]) -> bool:
    """是否该（重新）生成标题（纯函数）。"""
    if not enabled():
        return False
    source = str((meta or {}).get("title_source") or "").strip().lower()
    if source not in GENERATABLE_SOURCES:
        return False
    users = [m for m in (messages or []) if str(m.get("role") or "") == "user"]
    return len(users) >= MIN_USER_MESSAGES


def build_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """把会话消息裁成"给标题模型看的一段"（纯函数）。"""
    picked = [m for m in (messages or [])
              if str(m.get("role") or "") in ("user", "assistant")]
    picked = picked[-MAX_CONTEXT_MESSAGES:]
    rows: List[Dict[str, str]] = []
    used = 0
    for msg in picked:
        role = str(msg.get("role") or "user")
        text = _plain(msg.get("content") or "")
        if not text:
            continue
        remain = MAX_INPUT_CHARS - used
        if remain <= 0:
            break
        text = text[:remain]
        used += len(text)
        rows.append({"role": role, "content": text})
    if not rows:
        return []
    rows.append({"role": "user", "content": "请给上面的对话起一个标题（只输出标题）。"})
    return rows


def _build_llm(timeout: int = 0):
    """按 .env 构建 LLMService（与 skills_mgmt / process_distill 同口径）。"""
    try:
        from memory.llm_service import LLMService

        key = (os.environ.get("LLM_API_KEY", "")
               or os.environ.get("DEEPSEEK_API_KEY", "")).strip()
        if not key:
            try:
                from agent.env_config_manager import get_env_config_manager

                get_env_config_manager().reload()
            except Exception:  # noqa: BLE001 .env 加载失败不阻断
                pass
            key = (os.environ.get("LLM_API_KEY", "")
                   or os.environ.get("DEEPSEEK_API_KEY", "")).strip()
        if not key:
            return None
        kwargs: Dict[str, Any] = {}
        if timeout:
            kwargs["timeout"] = timeout
        return LLMService(
            provider=os.environ.get("LLM_PROVIDER", "deepseek").strip().lower(),
            api_key=key,
            model=os.environ.get("LLM_MODEL", "deepseek-v4-flash"),
            base_url=(os.environ.get("LLM_BASE_URL", "")
                      or os.environ.get("DEEPSEEK_BASE_URL", "")),
            **kwargs,
        )
    except Exception as exc:  # noqa: BLE001 构建失败 ⇒ 走回退标题
        logger.warning("[session-title] LLM 构建失败: %s", type(exc).__name__)
        return None


def _ask_llm(llm, messages: List[Dict[str, Any]]) -> str:
    """让模型给一个标题；失败返回空串（调用方走回退）。"""
    rows = build_messages(messages)
    if not rows or llm is None:
        return ""
    try:
        reply = llm.chat(
            rows,
            system_prompt=SYSTEM_PROMPT,
            max_tokens=MAX_TITLE_TOKENS,
            temperature=0.3,
            # 需要**正文**的调用必须关 thinking（口径 §6）：否则推理会吃掉全部预算
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as exc:  # noqa: BLE001 标题是旁路，失败不抛出
        logger.warning("[session-title] 生成失败: %s", type(exc).__name__)
        return ""
    return sanitize_title(reply)


def generate(session_id: str, mgr: Any = None, llm: Any = None) -> Dict[str, Any]:
    """同步生成一次标题并落盘（线程内/测试调用）。

    Returns:
        {"title": str, "source": "auto"|"fallback"}；不该生成/失败时返回 {}。
    """
    if not enabled() or not session_id:
        return {}
    manager = mgr if mgr is not None else _default_manager()
    if manager is None:
        return {}
    try:
        messages = manager.get_messages(session_id, limit=MAX_CONTEXT_MESSAGES * 4)
        meta = manager.get_session_metadata(session_id) or {}
    except Exception as exc:  # noqa: BLE001 读会话失败 ⇒ 放弃本次
        logger.warning("[session-title] 读会话失败: %s", type(exc).__name__)
        return {}
    if not should_generate(meta, messages):
        return {}

    title = _ask_llm(llm if llm is not None else _build_llm(TIMEOUT_SEC), messages)
    source = "auto"
    if not title:
        title = fallback_title(messages)
        source = "fallback"
    if not title:
        return {}
    try:
        ok = bool(manager.set_title(session_id, title, source=source))
    except Exception as exc:  # noqa: BLE001 落盘失败不影响对话
        logger.warning("[session-title] 写标题失败: %s", type(exc).__name__)
        return {}
    if not ok:
        return {}
    logger.info("[session-title] %s → %s（%s）", session_id, title, source)
    return {"title": title, "source": source}


def maybe_schedule(session_id: str, mgr: Any = None) -> bool:
    """一轮结束后调用：满足条件则**后台**生成标题（不阻塞对话）。返回是否已排入。"""
    if not enabled() or not session_id:
        return False
    manager = mgr if mgr is not None else _default_manager()
    if manager is None:
        return False
    try:
        messages = manager.get_messages(session_id, limit=MAX_CONTEXT_MESSAGES * 4)
        meta = manager.get_session_metadata(session_id) or {}
    except Exception:  # noqa: BLE001 读不到就不生成
        return False
    if not should_generate(meta, messages):
        return False

    def _run() -> None:
        try:
            generate(session_id, mgr=manager)
        except Exception as exc:  # noqa: BLE001 标题是旁路
            logger.warning("[session-title] 后台生成异常: %s", type(exc).__name__)

    threading.Thread(target=_run, name="session-title", daemon=True).start()
    return True


def _default_manager():
    """取进程内的会话管理器（app_server 注入的那个）；独立脚本里没有就返回 None。"""
    try:
        from app_server import _session_mgr  # 延迟导入：避免测试/脚本侧的循环依赖

        return _session_mgr
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "ENABLED_ENV", "MIN_USER_MESSAGES", "MAX_TITLE_CHARS", "GENERATABLE_SOURCES",
    "enabled", "sanitize_title", "fallback_title", "should_generate", "build_messages",
    "generate", "maybe_schedule",
]
