"""外部会话「全话」迁移（2026-11-14）。

把 DeepSeek Harness / Claude Code / Trae 的**完整转录**幂等写进云枢消息流，
与 agent/memory_import 既有的「卡片/向量」导入是两条路：

| 路径 | 输入 | 落点 | 是否改写 |
|---|---|---|---|
| preview/commit（既有） | 提炼后的记录 | 知识卡片 / 向量记忆 | 去噪、去重、摘要 |
| 本模块（新） | 整段会话 | data/messages.jsonl（追加） | 只截断 + 脱敏，**不摘要不改写** |

口径（写代码前已钉住）：
- **只读源文件**：不移动、不改写 ~/.dsh、~/.claude、Trae 的存储；不删云枢既有消息。
- **零 token**：不调 LLM；导入的是原文。
- **凭据不进日志/报告**：先过 redact.redact_text；报告只放计数与来源，不放正文。
- **幂等**：每行带 import:<source>:<session_id>:<n>，重跑只补新增。
- **冻结池**：每行必带 session_id（非空），legacy_pool.legacy_messages 不变。
- **不改既有行为**：只追加；排序交给读方（按 timestamp 参与排序）。

状态文件（运行期，gitignored）：
- data/memory/import_sessions/ledger.json  已导入会话的指纹（避免重扫 2 GB 源）
- data/memory/import_sessions/reports/*.json  每次 dry-run / execute 的报告（只有计数）
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    KIND_MESSAGE, KIND_PROMPT, SESSION_SOURCES, SOURCE_CLAUDE, SOURCE_DSH,
    SOURCE_TRAE, ConfirmationRequired, MemoryImportError, TranscriptMessage,
    TranscriptSession, import_key,
)
from .redact import redact_text

#: 消息流落点（运行期状态，.gitignore 已忽略；只追加）
DEFAULT_MESSAGES = Path(__file__).resolve().parents[2] / "data" / "messages.jsonl"
#: 台账与报告根目录
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "memory" / "import_sessions"
LEDGER_FILENAME = "ledger.json"
REPORT_DIRNAME = "reports"

#: 单条正文上限（与 dsh / claude 读取器同口径）
MAX_MESSAGE_CHARS = 20000
#: 单次运行的护栏（防止一次把整台机器吃满）
MAX_SESSIONS_PER_RUN = 2000
MAX_MESSAGES_PER_SESSION = 4000
MAX_MESSAGES_PER_RUN = 60000
MAX_RUNTIME_SECONDS = 180.0
#: 报告保留份数（每源）
MAX_REPORTS = 40
#: 失败样例最多记几条（只记会话 id + 异常类名）
MAX_ERROR_SAMPLES = 5
#: 写文件时一次 flush 的行数
FLUSH_EVERY = 200

_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _fingerprint(path: Path) -> str:
    """文件指纹：mtime_ns + size（源文件只读，不改它）。"""
    try:
        st = Path(path).stat()
    except OSError:
        return ""
    return f"{int(st.st_mtime_ns)}:{int(st.st_size)}"


def _dir_fingerprint(path: Path) -> str:
    try:
        st = Path(path).stat()
    except OSError:
        return ""
    return f"{int(st.st_mtime_ns)}"


@dataclass
class SessionRef:
    """待扫描的一份会话（只放路径/指纹，不读正文）。"""

    source: str
    session_id: str
    project: str = ""
    fingerprint: str = ""
    path: Optional[Path] = None
    origin: str = ""
    title: str = ""


@dataclass
class ScanStats:
    """读取器统计（会并进报告；只有计数）。"""

    lines: int = 0
    bad_lines: int = 0
    injected_user_messages: int = 0
    empty_messages: int = 0
    truncated_messages: int = 0
    capped: int = 0
    warnings: List[str] = field(default_factory=list)

    def merge(self, raw: Dict[str, Any]) -> None:
        self.lines += int(raw.get("lines") or 0)
        self.bad_lines += int(raw.get("bad_lines") or 0)
        self.injected_user_messages += int(raw.get("injected_user_messages") or 0)
        self.empty_messages += int(raw.get("empty_messages") or 0)
        self.truncated_messages += int(raw.get("truncated_messages") or 0)
        self.capped += int(raw.get("capped") or 0)
        for item in raw.get("warnings") or []:
            if len(self.warnings) < MAX_ERROR_SAMPLES:
                self.warnings.append(str(item))


def _load_ledger(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("sessions"), dict):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {"version": 1, "sessions": {}}


def _save_ledger(path: Path, ledger: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger["version"] = 1
    ledger["updated_at"] = _now_iso()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _existing_keys(path: Path) -> set:
    """消息流里已存在的 import_key 集合（坏行跳过；只读）。"""
    keys: set = set()
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"import_key"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(row, dict) and row.get("import_key"):
                    keys.add(str(row["import_key"]))
    except OSError:
        return keys
    return keys


# ════════════════════════════════════════════════════════════
#  来源枚举（不解析正文；Trae 例外，见 _trae_sessions）
# ════════════════════════════════════════════════════════════


def _dsh_refs(root: Optional[Path], *, include_subagents: bool) -> List[SessionRef]:
    from .readers import dsh as dsh_reader

    refs: List[SessionRef] = []
    for path in dsh_reader.iter_session_files(root):
        origin = dsh_reader.peek_origin(path) if not include_subagents else ""
        if not include_subagents and origin == "subagent":
            continue
        refs.append(SessionRef(
            source=SOURCE_DSH, session_id=path.parent.name,
            project=path.parent.parent.name, fingerprint=_fingerprint(path),
            path=path, origin=origin or "",
        ))
    return refs


def _claude_refs(root: Optional[Path]) -> List[SessionRef]:
    from .readers import claude as claude_reader

    base = Path(root) if root else claude_reader.default_claude_dir()
    if not base.is_dir():
        return []
    files = sorted(base.glob("projects/*/*.jsonl"),
                   key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
                   reverse=True)
    return [SessionRef(
        source=SOURCE_CLAUDE, session_id=path.stem, project=path.parent.name,
        fingerprint=_fingerprint(path), path=path, origin="claude_code",
    ) for path in files]


def _trae_roots(appdata: Optional[Path]) -> List[Path]:
    from .readers import trae as trae_reader

    return trae_reader.find_trae_roots(appdata)


def _trae_fingerprint(roots: List[Path]) -> str:
    parts: List[str] = []
    for root in roots:
        vscdb = root / "User" / "globalStorage" / "state.vscdb"
        parts.append("vscdb:" + _fingerprint(vscdb))
        for sub in ("IndexedDB", "Local Storage", "Session Storage"):
            parts.append(sub + ":" + _dir_fingerprint(root / sub))
    return "|".join(parts)


def _trae_sessions(appdata: Optional[Path], stats: ScanStats) -> Tuple[List[SessionRef], Dict[str, TranscriptSession]]:
    """把 Trae 的本地记录按 session_id 归并（本地没有会话边界，按日期归）。"""
    from .readers import trae as trae_reader

    result = trae_reader.read_trae(appdata=appdata, max_records=MAX_MESSAGES_PER_SESSION)
    report = result.report or {}
    stats.warnings.extend(str(w) for w in (report.get("warnings") or [])[:MAX_ERROR_SAMPLES])
    groups: Dict[str, TranscriptSession] = {}
    for record in result.records:
        if record.kind not in (KIND_PROMPT, KIND_MESSAGE):
            continue
        text = str(record.text or "")
        if not text:
            continue
        session = groups.get(record.session_id)
        if session is None:
            session = TranscriptSession(
                source=SOURCE_TRAE, session_id=record.session_id,
                project=record.project, origin="trae-local",
            )
            groups[record.session_id] = session
        if len(session.messages) >= MAX_MESSAGES_PER_SESSION:
            stats.capped += 1
            continue
        truncated = len(text) > MAX_MESSAGE_CHARS
        session.messages.append(TranscriptMessage(
            role=record.role, content=text[:MAX_MESSAGE_CHARS],
            ts=record.ts, truncated=truncated, index=len(session.messages) + 1,
        ))
        if truncated:
            stats.truncated_messages += 1
    fp = _trae_fingerprint(_trae_roots(appdata))
    refs = [SessionRef(source=SOURCE_TRAE, session_id=s.session_id,
                       project=s.project, fingerprint=fp, origin="trae-local",
                       title=s.title) for s in groups.values()]
    return refs, groups


# ════════════════════════════════════════════════════════════
#  扫描
# ════════════════════════════════════════════════════════════


def _scan_ref(ref: SessionRef, stats: ScanStats) -> TranscriptSession:
    if ref.source == SOURCE_DSH:
        from .readers import dsh as dsh_reader

        session, raw = dsh_reader.read_session(ref.path, max_messages=MAX_MESSAGES_PER_SESSION)
        stats.merge(raw)
        return session
    if ref.source == SOURCE_CLAUDE:
        from .readers import claude as claude_reader

        raw = {"lines": 0, "bad_lines": 0, "messages": 0, "chars": 0, "roles": {},
               "all_roles": {}, "block_kinds": {}, "empty_msgs": 0,
               "tool_result_chars": 0, "warnings": []}
        messages: List[TranscriptMessage] = []
        for record in claude_reader.iter_transcript(
                ref.path, stats=raw, max_records=MAX_MESSAGES_PER_SESSION):
            text = str(record.text or "")
            if not text:
                continue
            truncated = len(text) > MAX_MESSAGE_CHARS
            messages.append(TranscriptMessage(
                role=record.role, content=text[:MAX_MESSAGE_CHARS],
                ts=record.ts, truncated=truncated, index=len(messages) + 1,
            ))
            if truncated:
                stats.truncated_messages += 1
        stats.lines += int(raw.get("lines") or 0)
        stats.bad_lines += int(raw.get("bad_lines") or 0)
        stats.empty_messages += int(raw.get("empty_msgs") or 0)
        return TranscriptSession(
            source=SOURCE_CLAUDE, session_id=ref.session_id,
            project=ref.project, title=ref.title, origin="claude_code",
            messages=messages, meta={"file": Path(ref.path).name},
        )
    raise MemoryImportError(f"来源 {ref.source} 没有单文件扫描器", status=400,
                            code="unsupported-source", title="不支持的来源")


# ════════════════════════════════════════════════════════════
#  报告
# ════════════════════════════════════════════════════════════


def _write_report(root: Path, source: str, dry_run: bool, payload: Dict[str, Any]) -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    directory = root / REPORT_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{source}-{stamp}-{'dry' if dry_run else 'exec'}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    reports = sorted(directory.glob(f"{source}-*.json"))
    for old in reports[:-MAX_REPORTS]:
        try:
            old.unlink()
        except OSError:
            pass
    return str(path)


def latest_report(source: str, *, root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """读某来源最近一份报告（只读；没有就回 None，不伪造）。"""
    directory = Path(root or DEFAULT_ROOT) / REPORT_DIRNAME
    reports = sorted(directory.glob(f"{source}-*.json"))
    if not reports:
        return None
    try:
        data = json.loads(reports[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    data["report_path"] = str(reports[-1])
    return data


def source_availability(*, dsh_root: Optional[Path] = None,
                        claude_dir: Optional[Path] = None,
                        appdata: Optional[Path] = None) -> List[Dict[str, Any]]:
    """本机来源探测（只读；不做完整扫描）。"""
    from .readers import claude as claude_reader
    from .readers import dsh as dsh_reader

    dsh_base = Path(dsh_root) if dsh_root else dsh_reader.default_dsh_root()
    claude_base = Path(claude_dir) if claude_dir else claude_reader.default_claude_dir()
    trae_roots = _trae_roots(appdata)
    return [
        {"id": SOURCE_DSH, "label": "DeepSeek Harness",
         "available": dsh_base.is_dir(),
         "detail": str(dsh_base),
         "sessions": len(dsh_reader.iter_session_files(dsh_base))},
        {"id": SOURCE_CLAUDE, "label": "Claude Code",
         "available": claude_base.is_dir(),
         "detail": str(claude_base),
         "sessions": len(list(claude_base.glob("projects/*/*.jsonl"))) if claude_base.is_dir() else 0},
        {"id": SOURCE_TRAE, "label": "Trae",
         "available": bool(trae_roots),
         "detail": ", ".join(str(p) for p in trae_roots),
         "sessions": 0},
    ]


def _audit(source: str, payload: Dict[str, Any], messages_path: Path) -> Dict[str, Any]:
    """写一条审计（只有计数）；审计不可用不阻断已完成的写入。"""
    try:
        from agent.audit.facade import audit

        entry = audit.record(
            "memory_import.sessions", actor="agent.memory_import.transcript",
            subject=f"source:{source}",
            payload={"source": source, "sessions": payload.get("sessions", {}),
                     "messages": payload.get("messages", {}),
                     "chars": payload.get("chars", {}),
                     "redactions": payload.get("redactions", {}),
                     "messages_path": str(messages_path)},
            source="agent",
        )
        chain = getattr(audit, "chain", None)
        if chain is not None and hasattr(chain, "flush"):
            chain.flush()
        if entry is None:
            return {"seq": 0, "self_hash": ""}
        return {"seq": int(getattr(entry, "seq", 0) or 0),
                "self_hash": str(getattr(entry, "self_hash", "") or "")}
    except Exception:  # noqa: BLE001 审计失败不掩盖业务结果
        return {"seq": 0, "self_hash": ""}


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════


def import_sessions(*, source: str, dry_run: bool = True, confirm: bool = False,
                    limit_sessions: int = 0, include_subagents: bool = True,
                    messages_path: Optional[Path] = None,
                    ledger_path: Optional[Path] = None,
                    report_dir: Optional[Path] = None,
                    dsh_root: Optional[Path] = None,
                    claude_dir: Optional[Path] = None,
                    appdata: Optional[Path] = None,
                    max_runtime_seconds: float = MAX_RUNTIME_SECONDS) -> Dict[str, Any]:
    """把一个来源的会话整段写进 data/messages.jsonl（幂等，只追加）。"""
    source = str(source or "").strip().lower()
    if source not in SESSION_SOURCES:
        raise MemoryImportError("不支持的会话来源",
                                detail="source 只能取 dsh / claude / trae",
                                status=400, code="unsupported-source")
    if not dry_run and not confirm:
        raise ConfirmationRequired(
            "真写入必须显式确认",
            detail="先 dry_run 看计数；确认后传 confirm=true 且 dry_run=false",
        )

    messages_path = Path(messages_path or DEFAULT_MESSAGES)
    root = Path(report_dir) if report_dir else DEFAULT_ROOT
    ledger_file = Path(ledger_path) if ledger_path else (root / LEDGER_FILENAME)
    ledger = _load_ledger(ledger_file)

    started = time.monotonic()
    started_at = _now_iso()
    partial = False
    scan = ScanStats()
    redactions: Dict[str, int] = {}
    errors: List[Dict[str, str]] = []

    sessions = {"seen": 0, "scanned": 0, "imported": 0, "skipped_unchanged": 0,
                "empty": 0, "failed": 0, "capped": 0}
    messages = {"seen": 0, "candidates": 0, "imported": 0, "skipped_existing": 0,
                "skipped_redacted": 0, "skipped_empty": 0, "skipped_capped": 0,
                "truncated": 0}
    chars = {"seen": 0, "imported": 0}
    bytes_written = 0

    # ── 1) 枚举（Trae 例外：需要先解出 session 组） ──
    trae_aggregate = ledger.get("trae", {}) if isinstance(ledger.get("trae"), dict) else {}
    trae_fp = ""
    trae_all_skipped = False
    trae_map: Dict[str, TranscriptSession] = {}
    if source == SOURCE_TRAE:
        roots = _trae_roots(appdata)
        trae_fp = _trae_fingerprint(roots)
        if trae_aggregate.get("fingerprint") == trae_fp and trae_aggregate.get("sessions", 0) > 0:
            trae_all_skipped = True
            sessions["skipped_unchanged"] = int(trae_aggregate.get("sessions") or 0)
            messages["skipped_existing"] = int(trae_aggregate.get("messages") or 0)
            refs: List[SessionRef] = []
        else:
            refs, trae_map = _trae_sessions(appdata, scan)
    elif source == SOURCE_DSH:
        refs = _dsh_refs(dsh_root, include_subagents=include_subagents)
    else:
        refs = _claude_refs(claude_dir)

    # 小样本：取最近 N 个（按指纹里的 mtime 已倒序；Trae 按 session_id 排序）
    if source == SOURCE_TRAE:
        refs.sort(key=lambda r: r.session_id)
    total_refs = len(refs)
    wanted = total_refs
    if limit_sessions and wanted > int(limit_sessions):
        wanted = int(limit_sessions)
    # 硬上限：即使调用方传 0（全部），也不让一次请求枚举超过护栏值
    if wanted > MAX_SESSIONS_PER_RUN:
        wanted = MAX_SESSIONS_PER_RUN
    if wanted < total_refs:
        refs = refs[:wanted]
    sessions["seen"] = total_refs
    has_more = total_refs > len(refs)

    existing = _existing_keys(messages_path) if not trae_all_skipped else set()

    # ── 2) 扫描 + 写入 ──
    fh = None
    if not dry_run and not trae_all_skipped:
        messages_path.parent.mkdir(parents=True, exist_ok=True)
        fh = messages_path.open("a", encoding="utf-8", newline="\n")
    imported_at = _now_iso()
    pending_flush = 0
    try:
        for ref in refs:
            if time.monotonic() - started > float(max_runtime_seconds):
                partial = True
                break
            key = f"{ref.source}:{ref.session_id}"
            entry = ledger.get("sessions", {}).get(key) if isinstance(ledger.get("sessions"), dict) else None
            if isinstance(entry, dict) and entry.get("fingerprint") == ref.fingerprint:
                sessions["skipped_unchanged"] += 1
                messages["skipped_existing"] += int(entry.get("messages") or 0)
                chars["seen"] += int(entry.get("chars") or 0)
                continue
            sessions["scanned"] += 1
            try:
                session = trae_map[ref.session_id] if source == SOURCE_TRAE else _scan_ref(ref, scan)
            except Exception as exc:  # noqa: BLE001 单会话失败不拖垮整次导入
                sessions["failed"] += 1
                if len(errors) < MAX_ERROR_SAMPLES:
                    errors.append({"session_id": ref.session_id,
                                   "error": type(exc).__name__})
                continue
            if not session.messages:
                sessions["empty"] += 1
            session_imported = 0
            for message in session.messages:
                messages["seen"] += 1
                chars["seen"] += message.chars
                if message.truncated:
                    messages["truncated"] += 1
                body = str(message.content or "")
                if not body.strip():
                    messages["skipped_empty"] += 1
                    continue
                outcome = redact_text(body)
                for name, count in (outcome.hits or {}).items():
                    redactions[name] = redactions.get(name, 0) + int(count)
                if outcome.blocked:
                    messages["skipped_redacted"] += 1
                    messages["skipped_redacted_gate"] = messages.get("skipped_redacted_gate", 0) + 1
                    continue
                body = outcome.text
                messages["candidates"] += 1
                msg_key = import_key(ref.source, session.session_id, message.index)
                if msg_key in existing:
                    messages["skipped_existing"] += 1
                    session_imported += 1
                    continue
                if dry_run:
                    messages["imported"] += 1
                    chars["imported"] += len(body)
                    session_imported += 1
                    continue
                row = {
                    "role": message.role, "content": body,
                    "timestamp": message.ts or imported_at,
                    "session_id": session.session_id, "source": ref.source,
                    "imported_at": imported_at, "import_key": msg_key,
                    "project": ref.project or session.project,
                }
                line = json.dumps(row, ensure_ascii=False) + "\n"
                fh.write(line)
                bytes_written += len(line.encode("utf-8"))
                existing.add(msg_key)
                messages["imported"] += 1
                chars["imported"] += len(body)
                session_imported += 1
                pending_flush += 1
                if pending_flush >= FLUSH_EVERY:
                    fh.flush()
                    pending_flush = 0
            if session_imported:
                sessions["imported"] += 1
            if not dry_run:
                if not isinstance(ledger.get("sessions"), dict):
                    ledger["sessions"] = {}
                ledger["sessions"][key] = {
                    "fingerprint": ref.fingerprint,
                    "session_id": session.session_id,
                    "source": ref.source,
                    "project": ref.project or session.project,
                    "origin": ref.origin or session.origin,
                    "messages": len(session.messages),
                    "imported": session_imported,
                    "chars": session.chars,
                    "imported_at": imported_at,
                }
            if messages["imported"] >= MAX_MESSAGES_PER_RUN:
                partial = True
                break
    finally:
        if fh is not None:
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
            fh.close()

    if scan.capped:
        sessions["capped"] = scan.capped
        messages["skipped_capped"] = scan.capped

    if not dry_run:
        if source == SOURCE_TRAE and trae_fp:
            ledger["trae"] = {"fingerprint": trae_fp,
                              "sessions": sessions["imported"] + sessions["skipped_unchanged"],
                              "messages": messages["imported"] + messages["skipped_existing"],
                              "updated_at": imported_at}
        _save_ledger(ledger_file, ledger)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    payload: Dict[str, Any] = {
        "source": source, "dry_run": bool(dry_run), "started_at": started_at,
        "finished_at": _now_iso(), "duration_ms": elapsed_ms,
        "sessions": sessions, "messages": messages, "chars": chars,
        "bytes_written": bytes_written, "redactions": redactions,
        "truncated_messages": messages["truncated"],
        "read": {"lines": scan.lines, "bad_lines": scan.bad_lines,
                 "injected_user_messages": scan.injected_user_messages,
                 "empty_messages": scan.empty_messages,
                 "truncated_messages": scan.truncated_messages},
        "errors": errors, "has_more": has_more, "partial": partial,
        "messages_path": str(messages_path), "ledger_path": str(ledger_file),
        "warnings": scan.warnings[:MAX_ERROR_SAMPLES],
    }
    if not dry_run:
        payload["audit"] = _audit(source, payload, messages_path)
    payload["report_path"] = _write_report(root, source, dry_run, payload)
    return payload


__all__ = [
    "DEFAULT_MESSAGES", "DEFAULT_ROOT", "LEDGER_FILENAME", "MAX_MESSAGES_PER_RUN",
    "MAX_MESSAGES_PER_SESSION", "MAX_RUNTIME_SECONDS", "MAX_SESSIONS_PER_RUN",
    "ScanStats", "SessionRef", "import_sessions", "latest_report",
    "source_availability",
]
