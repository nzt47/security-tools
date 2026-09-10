"""逃逸检测（TASK-S2-03 / v7.2 §3.8 任务生命周期「逃逸」/ §6.6 escape 埋点）

**逃逸（escape）定义**：绕过治理路径的行为——典型为**用户手工编辑受管任务文件**
（`data/scheduled_tasks.json`），而不是经云枢受控写入能力
（`create_scheduled_task` / `delete_scheduled_task` / `toggle_scheduled_task`）。

检测机制（可复现、无猜测）：

1. **受控写台账**（`data/events/governed_writes.jsonl`，追加写）：云枢每次经受控
   路径写受管文件后登记一次指纹（sha256 + 大小 + 时间 + 写入方）。
2. **检测**：比对文件当前 sha256 与台账中**最后一次受控写**的 sha256；
   不一致 ⇒ 该变更**未经受控路径** ⇒ 判定逃逸并 emit `escape`
   （`{task_id, reason, capability_id}`）+ `intervention{kind=escape, weight=1}`。
3. **首次见到（台账无记录）**：只登记基线，**不判逃逸**（避免对存量文件误报），
   判定结论如实记录为 `baseline`。
4. **不改写逃逸内容**：检测是**只读**的，绝不回滚/覆盖用户改动（守【不易】：
   用户对本地文件的最终处置权在用户；本模块只做留痕与计数）。

诚实口径：本机制是**单机降级**的完整性比对（无文件系统级审计钩子/无签名内核），
只覆盖「已登记受控写的受管文件」；未纳管文件、以及在受控写登记之前发生的改动
不可检出（已在验收报告遗留清单登记）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from agent.observability import events as ev
from agent.observability.acr import KIND_ESCAPE, record_escape
from agent.observability.events import ACTOR_HUMAN, EventStore

logger = logging.getLogger("agent.observability.escape")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 受控写台账文件名（置于事件目录内，跟随 `CP_EVENTS_DIR` 隔离）
LEDGER_FILENAME = "governed_writes.jsonl"

ENV_WATCH = "CP_ESCAPE_WATCH"
ENV_GUARD = "CP_ESCAPE_GUARD"

#: 默认纳管的受管任务文件（相对仓库根）
DEFAULT_WATCHED = (os.path.join("data", "scheduled_tasks.json"),)

#: 受管文件 ↔ 本应使用的受控写入能力（capability_id，供 §6.6 escape.capability_id）
GOVERNED_CAPABILITY: Dict[str, str] = {
    "data/scheduled_tasks.json": "cp.governed.scheduled_task.write",
    "scheduled_tasks.json": "cp.governed.scheduled_task.write",
}

#: 逃逸原因（机器 token）
REASON_UNTRACKED_CHANGE = "untracked_content_change"
REASON_MISSING = "watched_file_missing"

#: 逃逸原因的中文说明（人读）
REASON_DETAIL = {
    REASON_UNTRACKED_CHANGE: "受管任务文件内容变更未经受控写入路径（疑似用户手工编辑）",
    REASON_MISSING: "受管任务文件消失（疑似被手工删除/移走）",
}

_LEDGER_LOCK = threading.Lock()


def _env_flag(name: str, default: str = "1") -> bool:
    return str(os.getenv(name, default)).strip().lower() not in ("0", "false", "no", "off", "")


def guard_enabled() -> bool:
    """逃逸守卫总开关（`CP_ESCAPE_GUARD=0` 关闭；默认开）"""
    return _env_flag(ENV_GUARD)


def ledger_path() -> str:
    return os.path.join(ev.default_events_dir(), LEDGER_FILENAME)


def _norm(path: Any) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _relative(path: Any) -> str:
    """仓库根相对路径（台账与事件的稳定键；跨机可读）"""
    full = os.path.abspath(str(path))
    try:
        rel = os.path.relpath(full, _PROJECT_ROOT)
    except ValueError:  # 跨盘符
        return full
    return rel.replace("\\", "/")


def watched_files(paths: Optional[Sequence[str]] = None) -> List[str]:
    """纳管的受管文件清单（显式入参 > `CP_ESCAPE_WATCH` > 默认）"""
    if paths:
        items = [str(p) for p in paths]
    else:
        raw = str(os.getenv(ENV_WATCH) or "").strip()
        if raw:
            items = [p.strip() for p in raw.replace(";", os.pathsep).split(os.pathsep) if p.strip()]
        else:
            items = [os.path.join(_PROJECT_ROOT, rel) for rel in DEFAULT_WATCHED]
    out: List[str] = []
    for item in items:
        out.append(item if os.path.isabs(item) else os.path.join(_PROJECT_ROOT, item))
    return out


def file_digest(path: Any) -> Dict[str, Any]:
    """文件指纹：``{exists, sha256, size}``（不可读 → exists=False/空摘要）"""
    full = str(path)
    if not os.path.isfile(full):
        return {"exists": False, "sha256": "", "size": 0}
    try:
        digest = hashlib.sha256()
        size = 0
        with open(full, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
        return {"exists": True, "sha256": digest.hexdigest(), "size": size}
    except OSError as e:
        logger.debug("受管文件指纹读取失败 %s: %s", full, e)
        return {"exists": False, "sha256": "", "size": 0}


# ════════════════════════════════════════════════════════════
#  受控写台账
# ════════════════════════════════════════════════════════════


class GovernedWriteLedger:
    """受控写台账（追加 JSONL；单进程串行写，锁内无 IO 之外的重活）"""

    def __init__(self, path: Optional[str] = None):
        self._path = str(path or ledger_path())

    @property
    def path(self) -> str:
        return self._path

    def record(self, path: Any, *, writer: str = "", digest: Optional[Dict[str, Any]] = None,
               extra: Optional[Dict[str, Any]] = None,
               ts: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """登记一次**受控写**（内容哈希为写入后的文件指纹）

        ``extra`` 可携带治理侧上下文（如受管文件当时的 ``task_ids``），用于后续
        逃逸判定时定位「被改的是哪个任务」。
        """
        if not guard_enabled():
            return None
        info = digest if digest is not None else file_digest(path)
        entry = {
            "path": _relative(path),
            "abs_path": _norm(path),
            "sha256": info.get("sha256") or "",
            "size": int(info.get("size") or 0),
            "writer": str(writer or ""),
            "ts": ts or ev.now_ts(),
        }
        if extra:
            entry.update(extra)
        try:
            with _LEDGER_LOCK:
                parent = os.path.dirname(self._path)
                if parent:
                    # pathlib 而非 os.makedirs：避免命中既有单测对全局 os.makedirs 的
                    # patch（同 events._write_line 的理由）
                    Path(parent).mkdir(parents=True, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return entry
        except OSError as e:  # noqa: BLE001 台账写失败不阻断受控写入
            logger.warning("受控写台账写入失败（不影响受控流程）: %s", e)
            return None

    def entries(self, path: Optional[Any] = None) -> List[Dict[str, Any]]:
        """台账全量（可按文件过滤），时间序"""
        if not os.path.exists(self._path):
            return []
        wanted = _norm(path) if path is not None else ""
        out: List[Dict[str, Any]] = []
        try:
            with open(self._path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(rec, dict):
                        continue
                    if wanted and str(rec.get("abs_path") or "") != wanted:
                        continue
                    out.append(rec)
        except OSError as e:
            logger.warning("受控写台账读取失败: %s", e)
        return out

    def last(self, path: Any) -> Optional[Dict[str, Any]]:
        """某文件最近一次受控写登记（无 → None）"""
        rows = self.entries(path)
        return rows[-1] if rows else None

    def baseline(self, path: Any) -> Optional[Dict[str, Any]]:
        """首次见到的存量文件：登记基线（`writer="baseline"`），不判逃逸"""
        return self.record(path, writer="baseline")


def get_ledger() -> GovernedWriteLedger:
    return GovernedWriteLedger()


def governed_capability(path: Any) -> str:
    """受管文件本应使用的受控写入能力 id（§6.6 escape.capability_id）

    显式映射优先；未登记路径按文件名派生 ``cp.governed.<stem>.write``
    （**确定性派生，不臆造具体能力**：它标明「本该走哪一类受控写入」）。
    """
    rel = _relative(path)
    name = os.path.basename(str(path))
    for key in (rel, name, str(path)):
        if key in GOVERNED_CAPABILITY:
            return GOVERNED_CAPABILITY[key]
    stem = os.path.splitext(name)[0].replace("-", "_") or "unknown"
    return f"cp.governed.{stem}.write"


def record_governed_write(path: Any, *, writer: str = "",
                          extra: Optional[Dict[str, Any]] = None,
                          ledger: Optional[GovernedWriteLedger] = None
                          ) -> Optional[Dict[str, Any]]:
    """登记一次受控写（供受控写入函数/上下文管理器调用；best-effort）"""
    try:
        return (ledger or get_ledger()).record(path, writer=writer, extra=extra)
    except Exception as e:  # noqa: BLE001 绝不阻断受控写入
        logger.debug("受控写登记失败 %s: %s", path, e)
        return None


@contextmanager
def governed_write(path: Any, *, writer: str = "",
                   extra: Optional[Dict[str, Any]] = None) -> Iterator[None]:
    """受控写上下文：块内成功完成后自动登记指纹

    用法::

        with governed_write(SCHEDULED_TASKS_FILE, writer="create_scheduled_task",
                            extra={"task_ids": ids}):
            _save_tasks(data)
    """
    try:
        yield
    finally:
        record_governed_write(path, writer=writer, extra=extra)


# ════════════════════════════════════════════════════════════
#  逃逸检测
# ════════════════════════════════════════════════════════════


def detect_escapes(paths: Optional[Sequence[str]] = None, *,
                   ledger: Optional[GovernedWriteLedger] = None,
                   store: Optional[EventStore] = None,
                   actor: str = ACTOR_HUMAN,
                   reason: str = REASON_UNTRACKED_CHANGE,
                   task_id: str = "",
                   detect_missing: bool = False,
                   ts: Optional[str] = None) -> List[Dict[str, Any]]:
    """检测受管任务文件的逃逸变更并 emit 事件

    Returns:
        逐文件结论列表（``status`` ∈ ``clean`` / ``baseline`` / ``escape`` / ``missing``）。
        **返回值仅供调用方观测**；任何异常都会退化为空列表（不阻断调用方主路径）。
    """
    if not guard_enabled():
        return []
    try:
        book = ledger or get_ledger()
        results: List[Dict[str, Any]] = []
        for path in watched_files(paths):
            rel = _relative(path)
            digest = file_digest(path)
            last = book.last(path)
            if not digest["exists"]:
                if detect_missing and last is not None:
                    results.append(_emit_escape(
                        path=rel, task_id=task_id, reason=REASON_MISSING,
                        capability_id=governed_capability(path),
                        digest_before=str(last.get("sha256") or ""),
                        digest_after="", actor=actor, store=store, ts=ts))
                    continue
                results.append({"path": rel, "status": "missing", "emitted": False})
                continue
            if last is None:
                book.baseline(path)
                results.append({"path": rel, "status": "baseline",
                                "sha256": digest["sha256"], "emitted": False})
                continue
            if str(last.get("sha256") or "") == digest["sha256"]:
                results.append({"path": rel, "status": "clean",
                                "sha256": digest["sha256"], "emitted": False})
                continue
            changed = _changed_task_ids(path, last.get("task_ids"))
            results.append(_emit_escape(
                path=rel, task_id=task_id or ",".join(changed) or f"file:{rel}",
                reason=reason, capability_id=governed_capability(path),
                digest_before=str(last.get("sha256") or ""),
                digest_after=digest["sha256"], changed_task_ids=changed,
                actor=actor, store=store, ts=ts))
        return results
    except Exception as e:  # noqa: BLE001 逃逸检测 best-effort
        logger.warning("逃逸检测失败（不影响主路径）: %s", e)
        return []


def _changed_task_ids(path: Any, baseline_ids: Any) -> List[str]:
    """对比受控写时登记的 ``task_ids`` 与当前文件内容 → 变更任务 id 列表

    仅适用于「受管文件是含 ``tasks`` 数组的 JSON」这一形态（云枢
    `scheduled_tasks.json` 即此形态）；无法解析 → 空列表（不臆造）。
    """
    if not isinstance(baseline_ids, (list, tuple)):
        return []
    try:
        with open(str(path), "r", encoding="utf-8", errors="ignore") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        return []
    current = {str(t.get("id") or "") for t in tasks if isinstance(t, dict)}
    baseline = {str(i) for i in baseline_ids}
    changed = sorted((current - baseline) | (baseline - current))
    return changed


def _emit_escape(*, path: str, task_id: str, reason: str, capability_id: str,
                 digest_before: str, digest_after: str, actor: str,
                 store: Optional[EventStore], ts: Optional[str],
                 changed_task_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """emit escape + intervention（幂等：同一 (path, 新摘要) 只计一次）"""
    envelopes = record_escape(
        task_id=task_id or path, reason=reason, capability_id=capability_id,
        path=path, digest_before=digest_before, digest_after=digest_after,
        actor=actor, store=store, ts=ts,
        extra={"changed_task_ids": list(changed_task_ids or [])})
    emitted = any(env is not None for env in envelopes)
    return {
        "path": path,
        "status": "escape",
        "reason": reason,
        "reason_detail": REASON_DETAIL.get(reason, ""),
        "capability_id": capability_id,
        "digest_before": digest_before,
        "digest_after": digest_after,
        "changed_task_ids": list(changed_task_ids or []),
        "emitted": emitted,
        "intervention_kind": KIND_ESCAPE,
    }


def escape_summary(*, since: Optional[str] = None, until: Optional[str] = None,
                   day: Optional[str] = None, directory: Optional[str] = None
                   ) -> Dict[str, Any]:
    """逃逸事件汇总（按文件 / 按原因计数）"""
    rows = ev.iter_events(types=(ev.EV_ESCAPE,), since=since, until=until, day=day,
                          directory=directory)
    by_path: Dict[str, int] = {}
    by_reason: Dict[str, int] = {}
    for env in rows:
        payload = env.payload or {}
        path = str(payload.get("path") or "")
        reason = str(payload.get("reason") or "")
        by_path[path] = by_path.get(path, 0) + 1
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {"total": len(rows), "by_path": dict(sorted(by_path.items())),
            "by_reason": dict(sorted(by_reason.items())),
            "ledger": get_ledger().path,
            "watched": [_relative(p) for p in watched_files()]}


__all__ = [
    "LEDGER_FILENAME", "ENV_WATCH", "ENV_GUARD", "DEFAULT_WATCHED",
    "GOVERNED_CAPABILITY", "REASON_UNTRACKED_CHANGE", "REASON_MISSING",
    "REASON_DETAIL", "guard_enabled", "ledger_path", "watched_files", "file_digest",
    "GovernedWriteLedger", "get_ledger", "governed_capability",
    "record_governed_write", "governed_write",
    "detect_escapes", "escape_summary",
]
