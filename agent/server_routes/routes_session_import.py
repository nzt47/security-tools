"""E4-5 第四批：会话导入（`POST /api/sessions/import`；默认关 + 写路径二次闸）

| 模式 | 语义 | 写不写 |
|---|---|---|
| `mode=preview`（默认） | 解析上传的 .json/.zip，回会话清单/计数/重复数（**不回正文**） | 不写 |
| `mode=apply` | 必须 `confirm=1` **且**写开关打开，才建会话 + 写消息 | 写 |

两个开关都是 A 级、默认关（登记在 `agent/settings/registry.py`）：
  · `CP_SESSION_IMPORT_ENABLED` 关 ⇒ 直接回 `disabled`，**连文件都不读**；
  · `CP_SESSION_IMPORT_WRITE_ENABLED` 关 ⇒ 预览照常，apply 回 403 `write_disabled`。

【为什么写路径单独一道闸】导入 = 往会话库里**追加**会话与消息（删已导入的只能靠会话删除）。
预览是只读的、随时可试；真写要人再点一次"确认导入"，并且运维还能整体关掉写路径。

【去重台账】`data/session_import/ledger.json`（运行期、gitignore）：键是内容指纹
（角色+正文的 sha256 前 16 位，**不含标题**），所以改过名的会话再导一次也不会重复。
台账只存计数与 id/标题，不存正文。

【边界】不落盘上传文件（内存解析）、不接受客户端路径、argv 全为常量、异常原文不回响应体。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from flask import jsonify, request

from agent import session_import as _import
from agent.server_auth import require_token
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)

#: 功能总开关（关 ⇒ 预览与导入都不可用；连文件都不读）
ENABLED_ENV = "CP_SESSION_IMPORT_ENABLED"
#: 写路径开关（关 ⇒ 只能预览；apply 回 403 write_disabled）
WRITE_ENV = "CP_SESSION_IMPORT_WRITE_ENABLED"

_TRUE_WORDS = ("1", "true", "yes", "on")
#: 台账最多保留多少条指纹（超了按时间淘汰最旧的）
LEDGER_MAX_ITEMS = 500
#: 文件名清洗：只留字母数字与少量安全符号（进 metadata 与会话标题前置展示用）
_BAD_NAME_RE = re.compile(r"[^0-9A-Za-z._\u4e00-\u9fff-]")
#: 台账文件里允许的键（读侧白名单：坏文件/旧版本不会把任意字段带进响应）
_LEDGER_ITEM_KEYS = ("at", "session_id", "title", "file")

#: 同一时刻只允许一次真导入（建会话 + 写消息是低频重操作）
_RUN_LOCK = threading.Lock()


def _flag(env_name: str) -> bool:
    return str(os.environ.get(env_name, "") or "").strip().lower() in _TRUE_WORDS


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "session_import"


def _ledger_path() -> Path:
    return _data_dir() / "ledger.json"


def _read_ledger() -> Dict[str, Any]:
    """读去重台账（坏文件/缺文件 ⇒ 空台账；读失败不阻塞导入，只记日志）。"""
    path = _ledger_path()
    if not path.exists():
        return {"version": 1, "items": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("[session-import] 去重台账不可读，本次按空台账处理")
        return {"version": 1, "items": {}}
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, dict):
        return {"version": 1, "items": {}}
    clean: Dict[str, Dict[str, Any]] = {}
    for key, row in items.items():
        if not isinstance(key, str) or not isinstance(row, dict):
            continue
        clean[key[:32]] = {k: row.get(k) for k in _LEDGER_ITEM_KEYS if k in row}
    return {"version": 1, "items": clean}


def _write_ledger(items: Dict[str, Dict[str, Any]]) -> None:
    """写台账（tmp + replace；只保留最近 LEDGER_MAX_ITEMS 条）。失败只记日志。"""
    trimmed = sorted(items.items(), key=lambda kv: str(kv[1].get("at") or ""), reverse=True)
    payload = {"version": 1, "items": dict(trimmed[:LEDGER_MAX_ITEMS])}
    path = _ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logger.warning("[session-import] 去重台账写入失败（不影响已导入的会话）")


def _safe_name(raw: str) -> str:
    base = os.path.basename(str(raw or "").replace("\\", "/"))
    return _BAD_NAME_RE.sub("_", base)[:80].lstrip(".") or "upload"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mgr():
    from app_server import _session_mgr

    return _session_mgr


def _still_exists(mgr, session_id: Any) -> bool:
    """台账记录的那个会话还在不在（读不到就按"还在"处理：宁可少导一次，也不重复导）。"""
    if not session_id:
        return False
    try:
        return mgr.get_session(str(session_id)) is not None
    except Exception:  # noqa: BLE001 会话库读失败不该让去重失效
        return True


def _live_fingerprints(fingerprints, known_map: Dict[str, Dict[str, Any]], mgr) -> set:
    """台账里**对应会话仍然存在**的指纹（用户删了导入的会话 ⇒ 允许重新导入同一份导出）。"""
    live = set()
    for fingerprint in fingerprints:
        row = known_map.get(fingerprint)
        if not isinstance(row, dict):
            continue
        if _still_exists(mgr, row.get("session_id")):
            live.add(fingerprint)
    return live


def _error(code: str, detail: str, status: int = 400):
    return jsonify({"ok": False, "code": code, "error": detail}), status


def _apply(doc: Dict[str, Any], safe_name: str, mgr) -> Dict[str, Any]:
    """把解析结果写成会话（调用方已持锁、已过两道开关 + confirm 校验）。"""
    ledger = _read_ledger()
    # 【去重要"活"】台账只拦**会话仍在**的指纹：用户删掉导入的会话后，同一份导出应能再导一次
    doc_fingerprints = [_import.fingerprint(conv) for conv in (doc.get("sessions") or ())]
    known = _live_fingerprints(doc_fingerprints, ledger["items"], mgr)
    created: List[Dict[str, Any]] = []
    skipped = 0
    previous_current = None
    try:
        previous_current = mgr.get_current_id()
    except Exception:  # noqa: BLE001 取当前会话失败不该挡住导入
        previous_current = None
    try:
        for conv in doc.get("sessions") or ():
            fingerprint = _import.fingerprint(conv)
            if fingerprint in known:
                skipped += 1
                continue
            info = mgr.create_session(title=conv.get("title") or "导入会话")
            session_id = str(info.get("id") or "")
            if not session_id:
                raise RuntimeError("create_session 未返回会话 id")
            for msg in conv.get("messages") or ():
                mgr.add_message(session_id, msg.get("role") or "user",
                                msg.get("content") or "",
                                timestamp=msg.get("timestamp") or None)
            mgr.update_session_metadata(
                session_id,
                imported={
                    "format": doc.get("format") or "",
                    "file": safe_name,
                    "at": _now_iso(),
                    "fingerprint": fingerprint,
                    "messages": len(conv.get("messages") or ()),
                },
            )
            ledger["items"][fingerprint] = {
                "at": _now_iso(), "session_id": session_id,
                "title": str(conv.get("title") or "")[: _import.MAX_TITLE_CHARS],
                "file": safe_name,
            }
            known.add(fingerprint)
            created.append({"id": session_id,
                            "title": conv.get("title") or "导入会话",
                            "messages": len(conv.get("messages") or ())})
    finally:
        if previous_current:
            try:
                mgr.set_current(previous_current)
            except Exception:  # noqa: BLE001 恢复"当前会话"失败不影响导入结果
                logger.warning("[session-import] 恢复当前会话失败")
        _write_ledger(ledger["items"])
    return {"created": created, "created_count": len(created),
            "skipped_duplicates": skipped,
            "stats": dict(doc.get("stats") or {}),
            "format": doc.get("format") or ""}


def register_routes(app, state=None):
    @app.route("/api/sessions/import", methods=["POST"])
    @trace_route("SessionImport")
    @require_token
    def api_session_import():
        """导入外部会话导出：默认只预览；真写要 confirm=1 且写开关打开"""
        if not _flag(ENABLED_ENV):
            return jsonify({"ok": False, "code": "disabled",
                            "error": "会话导入未启用（CP_SESSION_IMPORT_ENABLED=0）",
                            "write_enabled": False})
        upload = request.files.get("file")
        if upload is None or not str(upload.filename or "").strip():
            return _error("missing_file", "缺少文件（form 字段名 file）")
        safe_name = _safe_name(str(upload.filename))
        mode = str(request.form.get("mode") or "preview").strip().lower()
        if mode not in ("preview", "apply"):
            return _error("bad_mode", "mode 只支持 preview / apply")
        data = upload.stream.read(_import.MAX_UPLOAD_BYTES + 1)
        if len(data) > _import.MAX_UPLOAD_BYTES:
            return _error("too_large",
                          "文件超过 %d MB 上限" % (_import.MAX_UPLOAD_BYTES // (1024 * 1024)),
                          413)
        try:
            doc = _import.parse_upload(data, safe_name)
        except _import.ImportFormatError as exc:
            # 【安全】只回稳定 code 与固定中文短句：解析异常原文可能带文件名/片段
            return _error(exc.code, exc.detail, 413 if exc.code == "too_large" else 400)
        except Exception:  # noqa: BLE001 兜底不把异常原文回给客户端
            logger.exception("[session-import] 解析失败")
            return _error("parse_failed", "解析时出错，详见服务日志", 500)
        write_enabled = _flag(WRITE_ENV)
        if mode == "preview":
            ledger_items = _read_ledger()["items"]
            fingerprints = [_import.fingerprint(conv) for conv in (doc.get("sessions") or ())]
            known = _live_fingerprints(fingerprints, ledger_items, _mgr())
            body = _import.preview(doc, known=known)
            return jsonify({"ok": True, "write_enabled": write_enabled,
                            "filename": safe_name, **body})
        if not write_enabled:
            return _error("write_disabled",
                          "写路径未开启（CP_SESSION_IMPORT_WRITE_ENABLED=0），当前只能预览", 403)
        if str(request.form.get("confirm") or "").strip().lower() not in _TRUE_WORDS:
            return _error("confirmation_required", "真导入必须传 confirm=1（建议先预览）")
        if not _RUN_LOCK.acquire(blocking=False):
            return _error("busy", "已有导入任务在执行，请稍后重试", 409)
        try:
            result = _apply(doc, safe_name, _mgr())
        except Exception:  # noqa: BLE001 写失败如实报，但不回异常原文；已写入的不回滚
            logger.exception("[session-import] 导入失败")
            return _error("import_failed", "导入时出错，详见服务日志；已写入的会话不会回滚", 500)
        finally:
            _RUN_LOCK.release()
        return jsonify({"ok": True, "write_enabled": True, "filename": safe_name, **result})


__all__ = ["ENABLED_ENV", "WRITE_ENV", "register_routes"]
