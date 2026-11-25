"""E1-3a 结构化提问：待答问题 + 答案台账（**不做文件 IO 之外的重活**）。

【存储】``data/ask/<sha256(session)[:32]>/``
  - ``pending.json``：当前未答问题（同会话只保留最近 1 条；新问题把旧问题标 superseded）；
  - ``answers.jsonl``：append-only 答案台账（回答/替代/过期都留痕，供复盘与审计）。

【为什么不做阻塞等待】设计页 §2：与既有"审批边界"同一模式——工具返回
``USER_INPUT_REQUIRED`` → SSE ``ask`` 事件 → 本轮结束 → 用户回答 → 下一条普通用户消息
继续。不占流式线程、不绕开"停止生成"/权限 contextvar/超时预算。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

_ROOT_OVERRIDE: Optional[str] = None
_LOCK = threading.RLock()

_KEY_RE = re.compile(r"^(?P<key>[0-9a-f]{32})$")

#: 未答问题的存活期（读取时惰性清理）
PENDING_TTL = timedelta(hours=24)

#: 上限（与设计页 §3 一致）
MAX_QUESTION_CHARS = 1000
MAX_OPTIONS = 8
MAX_OPTION_LABEL = 100
MAX_CUSTOM_CHARS = 2000


class AskError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def set_root(path: Optional[str]) -> None:
    global _ROOT_OVERRIDE
    _ROOT_OVERRIDE = str(path) if path else None


def get_root() -> str:
    if _ROOT_OVERRIDE:
        return _ROOT_OVERRIDE
    return os.path.join(_repo_root(), "data", "ask")


def session_key(session_id: str) -> str:
    sid = str(session_id or "").strip()
    if not sid:
        raise AskError("invalid_session", "session_id 不能为空")
    digest = hashlib.sha256(sid.encode("utf-8")).hexdigest()[:32]
    match = _KEY_RE.match(digest)
    if not match:  # pragma: no cover
        raise AskError("invalid_key", "会话键派生失败")
    return match.group("key")


def _dir(key: str) -> str:
    return os.path.join(get_root(), key)


def _pending_path(key: str) -> str:
    return os.path.join(_dir(key), "pending.json")


def _answers_path(key: str) -> str:
    return os.path.join(_dir(key), "answers.jsonl")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:  # noqa: BLE001
            pass
    os.replace(tmp, path)


def _append_event(key: str, event: dict) -> None:
    path = _answers_path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:  # noqa: BLE001
            pass


def _load_pending(key: str) -> Optional[dict]:
    path = _pending_path(key)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _normalize_options(options) -> list:
    if options is None:
        return []
    if not isinstance(options, (list, tuple)):
        raise AskError("invalid_options", "options 必须是数组")
    if len(options) > MAX_OPTIONS:
        raise AskError("too_many_options", "options 最多 %d 个" % MAX_OPTIONS)
    out = []
    for i, raw in enumerate(options):
        if isinstance(raw, str):
            label = raw.strip()
            oid = "o%d" % (i + 1)
        elif isinstance(raw, dict):
            oid = str(raw.get("id") or "o%d" % (i + 1)).strip()[:40]
            label = str(raw.get("label") or "").strip()
        else:
            raise AskError("invalid_option", "第 %d 个选项格式不合法" % (i + 1))
        if not label:
            raise AskError("empty_option", "第 %d 个选项缺少 label" % (i + 1))
        item = {"id": oid, "label": label[:MAX_OPTION_LABEL]}
        if isinstance(raw, dict) and raw.get("description"):
            item["description"] = str(raw["description"])[:200]
        out.append(item)
    return out


def create_question(session_id: str, question: str, options=None,
                    allow_multiple: bool = False, allow_custom: bool = True,
                    context: str = "") -> dict:
    """创建一个待答问题（同会话只保留最近 1 条；新问题把旧问题标 superseded）"""
    text = str(question or "").strip()
    if not text:
        raise AskError("empty_question", "问题不能为空")
    if len(text) > MAX_QUESTION_CHARS:
        raise AskError("question_too_long", "问题最多 %d 字符" % MAX_QUESTION_CHARS)
    opts = _normalize_options(options)
    key = session_key(session_id)
    now = _now()
    qid = "q-" + uuid.uuid4().hex[:12]
    with _LOCK:
        old = _load_pending(key)
        if old and old.get("question_id"):
            _append_event(key, {"type": "superseded", "question_id": old["question_id"],
                                "at": _iso(now), "by": qid})
        doc = {
            "question_id": qid,
            "question": text,
            "options": opts,
            "allow_multiple": bool(allow_multiple),
            "allow_custom": bool(allow_custom),
            "context": str(context or "").strip()[:500],
            "created_at": _iso(now),
        }
        _atomic_write(_pending_path(key), json.dumps(doc, ensure_ascii=False, indent=1))
    return doc


def get_pending(session_id: str) -> Optional[dict]:
    """读未答问题；超过 TTL 懒清理并返回 None"""
    key = session_key(session_id)
    with _LOCK:
        doc = _load_pending(key)
        if not doc:
            return None
        try:
            created = datetime.fromisoformat(str(doc.get("created_at") or ""))
        except (TypeError, ValueError):
            created = _now()
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if _now() - created > PENDING_TTL:
            try:
                os.remove(_pending_path(key))
            except OSError:
                pass
            _append_event(key, {"type": "expired", "question_id": doc.get("question_id"),
                                "at": _iso(_now())})
            return None
        return doc


def answer(session_id: str, question_id: str, answers=None, custom: str = "") -> dict:
    """回答问题：校验 id/选项 → 落台账 → 清 pending。

    Returns:
        {"question_id", "answers", "custom", "question"}（供路由拼 next_message）
    """
    key = session_key(session_id)
    qid = str(question_id or "").strip()
    picked = []
    if answers is not None:
        if not isinstance(answers, (list, tuple)):
            raise AskError("invalid_answers", "answers 必须是数组")
        picked = [str(a).strip() for a in answers if str(a).strip()]
    text = str(custom or "").strip()
    if len(text) > MAX_CUSTOM_CHARS:
        raise AskError("custom_too_long", "自定义回答最多 %d 字符" % MAX_CUSTOM_CHARS)
    with _LOCK:
        doc = _load_pending(key)
        if not doc:
            raise AskError("no_pending", "当前没有待回答的问题（可能已过期或被替代）")
        if qid and qid != doc.get("question_id"):
            raise AskError("stale_question", "问题已被新提问替代，请回答最新问题")
        valid_ids = {o.get("id") for o in doc.get("options") or []}
        unknown = [a for a in picked if valid_ids and a not in valid_ids]
        if unknown:
            raise AskError("invalid_option_id", "未知选项：%s" % ",".join(unknown[:3]))
        if not picked and not text:
            raise AskError("empty_answer", "请至少选择一个选项或填写自定义回答")
        if not doc.get("allow_multiple") and len(picked) > 1:
            raise AskError("single_choice_only", "该问题只能选一个选项")
        # allow_custom=false 时只接受选项（文本仍允许作为备注？设计：不接受自定义）
        if text and not doc.get("allow_custom", True):
            raise AskError("custom_not_allowed", "该问题不接受自定义回答")
        record = {
            "type": "answer", "question_id": doc.get("question_id"),
            "answers": picked, "custom": text, "at": _iso(_now()),
        }
        _append_event(key, record)
        _atomic_write(_pending_path(key), json.dumps({}, ensure_ascii=False))
        return {"question_id": doc.get("question_id"), "answers": picked,
                "custom": text, "question": doc.get("question", "")}


def delete_ask(session_id: str) -> bool:
    """删除会话的提问文件（会话删除时调用；失败返回 False）"""
    try:
        key = session_key(session_id)
    except AskError:
        return False
    path = _dir(key)
    if not os.path.isdir(path):
        return False
    with _LOCK:
        try:
            for name in ("pending.json", "pending.json.tmp", "answers.jsonl",
                         "answers.jsonl.tmp"):
                try:
                    os.remove(os.path.join(path, name))
                except OSError:
                    pass
            try:
                os.rmdir(path)
            except OSError:
                pass
            return True
        except OSError:
            return False


__all__ = [
    "AskError", "MAX_CUSTOM_CHARS", "MAX_OPTIONS", "MAX_QUESTION_CHARS", "PENDING_TTL",
    "answer", "create_question", "delete_ask", "get_pending", "get_root",
    "session_key", "set_root",
]
