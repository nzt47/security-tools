"""E4-5 第四批：会话导入的**纯解析层**（外部导出 → 统一形状；不碰 Flask、零写入）

【不易·只认白名单格式】本模块只解析两种格式（FORMATS）：
  · `yunshu.session.v1` —— 云枢自己的会话导出形状（`{format, session, messages}`），用于往返；
  · `deepseek.export.v1` —— DeepSeek 会话导出（`[{id,title,messages:[{role,content}]}]`，
    也接受 `{conversations|chats|data|items: [...]}` 与"单会话"即顶层带 `messages`）。
    字段别名：title|name|chat_name、role|message_role（ai→assistant / human→user）、
    content|text、timestamp|created_at|inserted_at|create_time。
  其它形状一律 `ImportFormatError("unsupported_format")` —— **不猜**、不"尽力解析"：
  静默把别家的字段错读成空会话，比直接拒收更坏。

【变易·预览与写入分离】parse_upload / preview 都是纯函数、零写入；
只有 `routes_session_import` 的 apply 分支才建会话 + 写消息（另有写开关）。

【简易·上限是硬的】20MiB 上传、ZIP 内 20 个 .json、50 个会话、每会话 5000 条、
单条正文 100k 字符、标题 120 字符；越界**截断并计数**（stats.truncated_*），绝不悄悄丢。
role 不在白名单（system/tool/function）与空正文只计数后跳过 —— 导入的是"人看过的对话"，
不是把内部提示词与工具原始输出一起搬进来。
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Set, Tuple

#: 白名单格式（顺序即展示顺序；新增格式必须同批加解析 + 夹具 + 文档）
FORMATS: Tuple[str, ...] = ("yunshu.session.v1", "deepseek.export.v1")

#: 允许的上传后缀（只解析，不落盘）
ALLOWED_EXTS: Tuple[str, ...] = (".json", ".zip")

#: 单次上传字节上限（与附件上传同一量级，导入是低频操作）
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
#: ZIP 里最多读几个 .json
MAX_ZIP_ENTRIES = 20
#: 单次最多导入几个会话
MAX_SESSIONS_PER_IMPORT = 50
#: 单个会话最多导入几条消息
MAX_MESSAGES_PER_SESSION = 5000
#: 单条正文保留字符数
MAX_CONTENT_CHARS = 100_000
#: 标题保留字符数
MAX_TITLE_CHARS = 120
#: 会话时间戳保留字符数
MAX_TS_CHARS = 40
#: 只导入"人与人看过的对话"：系统提示词与工具原始输出不进消息流
ROLE_WHITELIST: Tuple[str, ...] = ("user", "assistant")
#: 旧导出里的角色别名
_ROLE_ALIASES = {"ai": "assistant", "human": "user", "bot": "assistant"}
#: 容器键（DeepSeek 导出外层可能是其中任何一个）
_CONTAINER_KEYS = ("conversations", "chats", "data", "items")
#: 裸 epoch 数字（秒/毫秒）
_EPOCH_RE = re.compile(r"^\d{10,13}$")
#: 形如 2026-08-01 / 2026-08-01 09:00:00 / 2026-08-01T09:00:00Z
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")


class ImportFormatError(Exception):
    """格式/体积类错误（`code` 是稳定机器码，`detail` 是可展示的中文短句）。"""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _new_stats() -> Dict[str, int]:
    return {
        "files": 0,
        "conversations": 0,
        "messages": 0,
        "dropped_roles": 0,
        "dropped_empty": 0,
        "truncated_messages": 0,
        "capped_sessions": 0,
        "truncated_sessions": 0,
        "duplicates_in_file": 0,
    }


def _as_text(value: Any) -> str:
    """把导出里的"正文"取成字符串：str 原样；列表拼文本段；dict 取 text/content。"""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        parts = [str(x) for x in value if isinstance(x, str)]
        return "\n".join(parts)
    if isinstance(value, dict):
        for key in ("text", "content"):
            inner = value.get(key)
            if isinstance(inner, str):
                return inner
    return ""


def _iso(value: Any) -> str:
    """时间戳归一：epoch 秒/毫秒 → ISO(UTC)，ISO/日期串原样保留，其它 → 空串。

    【为什么不填 `datetime.now()`】导入的是一条**历史**消息：拿"现在"冒充它的时间，
    会把会话时间轴整体压平。解析不出来就留空，由调用方决定（add_message 才用 now）。
    """
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e12:  # 毫秒
            number = number / 1000.0
        try:
            return datetime.fromtimestamp(number, timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return ""
    text = str(value or "").strip()
    if not text:
        return ""
    if _EPOCH_RE.match(text):
        return _iso(int(text))
    if _DATE_RE.match(text):
        return text[:MAX_TS_CHARS]
    return ""


def _title(raw: Dict[str, Any]) -> str:
    text = ""
    for key in ("title", "name", "chat_name"):
        text = _as_text(raw.get(key)).strip()
        if text:
            break
    text = " ".join(text.split())
    return text[:MAX_TITLE_CHARS] or "导入会话"


def _created_at(raw: Dict[str, Any]) -> str:
    for key in ("created_at", "inserted_at", "create_time", "updated_at", "update_time"):
        stamp = _iso(raw.get(key))
        if stamp:
            return stamp
    return ""


def _normalize_message(item: Dict[str, Any], stats: Dict[str, int]) -> Dict[str, Any] | None:
    role = _as_text(item.get("role") or item.get("message_role")).strip().lower()
    role = _ROLE_ALIASES.get(role, role)
    if role not in ROLE_WHITELIST:
        stats["dropped_roles"] += 1
        return None
    body = _as_text(item.get("content") if "content" in item else item.get("text"))
    if not body.strip():
        stats["dropped_empty"] += 1
        return None
    if len(body) > MAX_CONTENT_CHARS:
        body = body[:MAX_CONTENT_CHARS]
        stats["truncated_messages"] += 1
    stamp = ""
    for key in ("timestamp", "created_at", "inserted_at", "create_time"):
        stamp = _iso(item.get(key))
        if stamp:
            break
    return {"role": role, "content": body, "timestamp": stamp}


def _conversation(raw: Any, stats: Dict[str, int]) -> Dict[str, Any] | None:
    """把一个会话对象压成 {title, created_at, messages[]}；无可用消息 → None。"""
    if not isinstance(raw, dict):
        return None
    rows = raw.get("messages")
    if not isinstance(rows, list):
        rows = raw.get("chat_messages")
    if not isinstance(rows, list):
        rows = []
    messages: List[Dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        msg = _normalize_message(item, stats)
        if msg is None:
            continue
        messages.append(msg)
        if len(messages) >= MAX_MESSAGES_PER_SESSION:
            stats["capped_sessions"] += 1
            break
    if not messages:
        return None
    stats["conversations"] += 1
    stats["messages"] += len(messages)
    return {"title": _title(raw), "created_at": _created_at(raw), "messages": messages}


def _yunshu_conversation(obj: Dict[str, Any]) -> Dict[str, Any]:
    """yunshu.session.v1 的会话元数据在 `session` 子对象里（也容忍平铺）。"""
    meta = obj.get("session") if isinstance(obj.get("session"), dict) else {}
    merged: Dict[str, Any] = dict(meta)
    for key in ("title", "name", "created_at", "inserted_at"):
        if key not in merged and key in obj:
            merged[key] = obj.get(key)
    merged["messages"] = obj.get("messages")
    return merged


def _parse_json_obj(obj: Any, stats: Dict[str, int]) -> Tuple[str, List[Dict[str, Any]]]:
    """识别一份 JSON 属于哪种白名单格式，并抽出会话列表。"""
    if isinstance(obj, list):
        convs = [c for c in (_conversation(x, stats) for x in obj) if c]
        return "deepseek.export.v1", convs
    if not isinstance(obj, dict):
        raise ImportFormatError("unsupported_format", "只接受 JSON 对象或数组")
    fmt = str(obj.get("format") or "").strip()
    if fmt == "yunshu.session.v1":
        conv = _conversation(_yunshu_conversation(obj), stats)
        return fmt, ([conv] if conv else [])
    for key in _CONTAINER_KEYS:
        container = obj.get(key)
        if isinstance(container, list):
            convs = [c for c in (_conversation(x, stats) for x in container) if c]
            return "deepseek.export.v1", convs
    if isinstance(obj.get("messages"), list) or isinstance(obj.get("chat_messages"), list):
        conv = _conversation(obj, stats)
        return "deepseek.export.v1", ([conv] if conv else [])
    raise ImportFormatError(
        "unsupported_format",
        "无法识别导出格式（白名单：%s）" % " / ".join(FORMATS),
    )


def _read_zip(data: bytes, stats: Dict[str, int]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError):
        raise ImportFormatError("bad_zip", "ZIP 无法解析") from None
    with archive:
        infos = [i for i in archive.infolist()
                 if not i.is_dir() and i.filename.lower().endswith(".json")]
        if not infos:
            raise ImportFormatError("no_sessions", "ZIP 里没有 .json 会话文件")
        if len(infos) > MAX_ZIP_ENTRIES:
            raise ImportFormatError(
                "too_many_files",
                "ZIP 内 .json 超过 %d 个（请拆分后再导入）" % MAX_ZIP_ENTRIES,
            )
        declared = 0
        for info in infos:
            declared += int(getattr(info, "file_size", 0) or 0)
        if declared > MAX_UPLOAD_BYTES:
            raise ImportFormatError("too_large", "ZIP 解压后超过上限")
        out: List[Tuple[str, List[Dict[str, Any]]]] = []
        for info in infos:
            raw = archive.read(info)[:MAX_UPLOAD_BYTES]
            stats["files"] += 1
            name = os.path.basename(info.filename)
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ImportFormatError("bad_json", "ZIP 内的 %s 不是合法 JSON" % name) from None
            out.append(_parse_json_obj(obj, stats))
        return out


def _check_upload(data: bytes, filename: str) -> str:
    if not data:
        raise ImportFormatError("empty_file", "文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImportFormatError("too_large", "文件超过 %d MB 上限" % (MAX_UPLOAD_BYTES // (1024 * 1024)))
    ext = os.path.splitext(str(filename or ""))[1].lower()
    if ext not in ALLOWED_EXTS:
        raise ImportFormatError(
            "unsupported_format",
            "只接受 %s 文件" % " / ".join(ALLOWED_EXTS),
        )
    return ext


def fingerprint(conversation: Dict[str, Any]) -> str:
    """会话内容指纹（sha256 前 16 位）：**只看角色与正文**，不看标题。

    【为什么不含标题】导入后用户可能改名；改名不该让"同一份导出再导一次"变成重复数据。
    """
    import hashlib

    digest = hashlib.sha256()
    for msg in conversation.get("messages") or ():
        digest.update(str(msg.get("role") or "").encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(str(msg.get("content") or "").encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()[:16]


def parse_upload(data: bytes, filename: str) -> Dict[str, Any]:
    """解析上传内容（.json / .zip）→ `{format, sessions, stats}`（纯函数、零写入）。

    Raises:
        ImportFormatError: 体积/后缀/JSON/格式/空会话 各类稳定错误码。
    """
    ext = _check_upload(data, filename)
    stats = _new_stats()
    parsed: List[Tuple[str, List[Dict[str, Any]]]] = []
    if ext == ".zip":
        parsed = _read_zip(data, stats)
    else:
        stats["files"] += 1
        try:
            obj = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ImportFormatError("bad_json", "文件不是 UTF-8 JSON") from None
        parsed.append(_parse_json_obj(obj, stats))

    formats = {fmt for fmt, _ in parsed}
    fmt_out = formats.pop() if len(formats) == 1 else "mixed"
    sessions: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for _fmt, convs in parsed:
        for conv in convs:
            key = fingerprint(conv)
            if key in seen:
                stats["duplicates_in_file"] += 1
                continue
            seen.add(key)
            sessions.append(conv)
    if not sessions:
        raise ImportFormatError("no_sessions", "没有解析到可导入的会话（只导入 user/assistant 正文）")
    if len(sessions) > MAX_SESSIONS_PER_IMPORT:
        stats["truncated_sessions"] = len(sessions) - MAX_SESSIONS_PER_IMPORT
        sessions = sessions[:MAX_SESSIONS_PER_IMPORT]
    return {"format": fmt_out, "sessions": sessions, "stats": stats}


def preview(doc: Dict[str, Any], known: Sequence[str] | Set[str] = ()) -> Dict[str, Any]:
    """只读预览：每个会话只回标题/条数/字符数/时间范围/指纹与是否重复，**不回正文**。"""
    known_set = set(known or ())
    rows: List[Dict[str, Any]] = []
    duplicates = 0
    for conv in doc.get("sessions") or ():
        msgs = conv.get("messages") or []
        chars = sum(len(str(m.get("content") or "")) for m in msgs)
        stamps = [str(m.get("timestamp") or "") for m in msgs if m.get("timestamp")]
        fp = fingerprint(conv)
        duplicate = fp in known_set
        if duplicate:
            duplicates += 1
        rows.append({
            "title": conv.get("title") or "导入会话",
            "messages": len(msgs),
            "chars": chars,
            "first_ts": stamps[0] if stamps else "",
            "last_ts": stamps[-1] if stamps else "",
            "fingerprint": fp,
            "duplicate": duplicate,
        })
    return {
        "format": doc.get("format") or "",
        "sessions": rows,
        "session_count": len(rows),
        "duplicates": duplicates,
        "stats": dict(doc.get("stats") or {}),
        "allowed_formats": list(FORMATS),
        "limits": {
            "max_upload_bytes": MAX_UPLOAD_BYTES,
            "max_zip_entries": MAX_ZIP_ENTRIES,
            "max_sessions": MAX_SESSIONS_PER_IMPORT,
            "max_messages_per_session": MAX_MESSAGES_PER_SESSION,
            "max_content_chars": MAX_CONTENT_CHARS,
        },
    }


__all__ = [
    "ALLOWED_EXTS", "FORMATS", "ImportFormatError", "MAX_CONTENT_CHARS",
    "MAX_MESSAGES_PER_SESSION", "MAX_SESSIONS_PER_IMPORT", "MAX_TITLE_CHARS",
    "MAX_UPLOAD_BYTES", "MAX_ZIP_ENTRIES", "ROLE_WHITELIST", "fingerprint",
    "parse_upload", "preview",
]
