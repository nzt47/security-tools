"""cognitive_loop 跨 workspace / 跨实例共享教训池（一期 MVP，默认关）。

设计：`docs/proposals/cognitive_loop_cross_workspace_learning.md`（2026-10-10，草稿）。
本模块只做**一期**（方案 A：共享文件系统 + 隔离键 + 双上限 + erase + 审计），
提升闸门（candidate→shared，I2）留二期。

【安全不变量】
  I1 隔离：记录带 (tenant, subject)；检索只返回**当前作用域完全匹配**的记录；无作用域 ⇒ 不读不写。
  I3 注入即数据：记录固定 taint=upstream，回读后由调用方当"历史教训（数据）"拼入，绝不当指令。
  I4 fail-closed：目录未配置 / 文件损坏 / 写失败 ⇒ 退化为空（不注入、不假装成功），且不影响本地流水。
  I5 可遗忘：erase(lesson_id) 在**当前作用域内**原子重写，擦除后不再注入。

【本次落地的默认假设（方案 §12 待决策项）】
  1) 存储：方案 A（共享文件系统），非母体服务；
  2) subject/tenant：直接取母体 `DelegationContext.tenant_id / subject_id`，不另行派生；
  3) 提升闸门：一期不做，进池即可见；
  4) TTL/容量：TTL `DEFAULT_TTL_DAYS` 天 / 单池 `POOL_ENTRY_CAP` 条 / 单条 advice 截断
     `ADVICE_CHAR_CAP` 字符 / 回读 `RECALL_COUNT_CAP` 条且 `RECALL_CHAR_CAP` 字符。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "ENV_SHARED", "ENV_DIR", "LESSONS_FILENAME", "SCHEMA_VERSION", "TAINT",
    "DEFAULT_TTL_DAYS", "POOL_ENTRY_CAP", "ADVICE_CHAR_CAP",
    "RECALL_COUNT_CAP", "RECALL_CHAR_CAP",
    "shared_enabled", "lessons_dir", "scope_context", "current_scope",
    "lesson_id", "record_lesson", "recall_shared", "erase",
]

#: 开关与目录（注册于 agent/settings/registry.py）
ENV_SHARED = "CP_SUBAGENT_COGNITIVE_SHARED_LESSONS"
ENV_DIR = "CP_SUBAGENT_COGNITIVE_LESSONS_DIR"

LESSONS_FILENAME = "lessons.jsonl"
SCHEMA_VERSION = 1
#: 进池文本固定标 upstream（注入时保持）
TAINT = "upstream"

DEFAULT_TTL_DAYS = 90
POOL_ENTRY_CAP = 2000
ADVICE_CHAR_CAP = 500
RECALL_COUNT_CAP = 5
RECALL_CHAR_CAP = 2000

#: 当前委派作用域（由母体 executor 从 DelegationContext 设定；ContextVar 保证并发委派互不串）
_SCOPE: ContextVar[Optional[Tuple[str, str]]] = ContextVar("cognitive_lessons_scope", default=None)
_write_lock = threading.Lock()


def shared_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """共享教训池是否开启（默认关；只认 1/true/yes/on）"""
    raw = (os.environ.get(ENV_SHARED, "") if environ is None
           else environ.get(ENV_SHARED, ""))
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def lessons_dir(environ: Optional[Mapping[str, str]] = None) -> str:
    """共享教训池目录（空 = 未配置 ⇒ 不读不写）"""
    raw = (os.environ.get(ENV_DIR, "") if environ is None
           else environ.get(ENV_DIR, ""))
    return str(raw).strip()


@contextmanager
def scope_context(tenant: str, subject: str) -> Iterator[None]:
    """在 with 块内设定当前 (tenant, subject) 作用域；缺失任一 ⇒ 置空（fail-closed）。

    【为什么用 ContextVar】同一个 executor 会被并发委派共享；实例级字段会把 A 委派的
    作用域串到 B 委派。ContextVar 随调用线程/任务隔离，且不改变 ChannelInvocation 的
    "不带域标识" 约定。
    """
    t = str(tenant or "").strip()
    s = str(subject or "").strip()
    token = _SCOPE.set((t, s) if (t and s) else None)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def current_scope() -> Optional[Tuple[str, str]]:
    """当前作用域；无 / 不完整 ⇒ None"""
    return _SCOPE.get()


def lesson_id(tenant: str, subject: str, signature: str, advice: str) -> str:
    """规范化内容的哈希 —— 幂等去重的唯一键（同内容重复确认不产生第二条）"""
    raw = json.dumps(
        {"tenant": str(tenant or ""), "subject": str(subject or ""),
         "signature": str(signature or ""), "advice": str(advice or "")},
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _pool_path(environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    d = lessons_dir(environ)
    if not d:
        return None
    return Path(d) / LESSONS_FILENAME


def _load(path: Path) -> Optional[List[dict]]:
    """读全部行；文件不存在 ⇒ []；损坏/不可读 ⇒ None（区分"空"与"坏"，坏不覆盖）"""
    if not path.exists():
        return []
    try:
        rows: List[dict] = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
        return rows
    except (OSError, ValueError) as exc:
        logger.debug("[CognitiveLessons] 读取失败 %s: %s", path, exc)
        return None


def _write(path: Path, rows: List[dict]) -> bool:
    """原子替换写入（先写临时文件再 os.replace）"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except OSError as exc:
        logger.debug("[CognitiveLessons] 写入失败 %s: %s", path, exc)
        return False


def _parse_ts(value: Any) -> datetime:
    try:
        ts = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def record_lesson(signature: str, advice: str, *, evidence: str = "",
                  confidence: float = 0.5, ttl_days: Optional[int] = None,
                  environ: Optional[Mapping[str, str]] = None,
                  now: Optional[datetime] = None) -> Optional[str]:
    """落一条共享教训；返回 lesson_id，未落盘（关/无作用域/无目录/写失败）返回 None。

    - 同 lesson_id 只更新 confirmations/last_seen_at（幂等，不产生重复行）；
    - advice 截断到 ADVICE_CHAR_CAP；signature/advice 任一为空 ⇒ 拒收；
    - 池超过 POOL_ENTRY_CAP ⇒ 保留最近 POOL_ENTRY_CAP 条。
    """
    if not shared_enabled(environ):
        return None
    scope = current_scope()
    if scope is None:
        return None
    tenant, subject = scope
    sig = str(signature or "").strip()
    text = str(advice or "").strip()
    if not sig or not text:
        return None
    text = text[:ADVICE_CHAR_CAP]
    try:
        conf = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        conf = 0.0
    try:
        ttl = int(ttl_days) if ttl_days is not None else DEFAULT_TTL_DAYS
    except (TypeError, ValueError):
        ttl = DEFAULT_TTL_DAYS
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    lid = lesson_id(tenant, subject, sig, text)
    path = _pool_path(environ)
    if path is None:
        return None
    with _write_lock:
        rows = _load(path)
        if rows is None:
            return None
        for row in rows:
            if row.get("lesson_id") == lid:
                row["confirmations"] = int(row.get("confirmations") or 0) + 1
                row["last_seen_at"] = stamp
                row["confidence"] = max(float(row.get("confidence") or 0.0), conf)
                return lid if _write(path, rows) else None
        rows.append({
            "schema_version": SCHEMA_VERSION,
            "lesson_id": lid,
            "tenant": tenant,
            "subject": subject,
            "signature": sig,
            "advice": text,
            "evidence": str(evidence or "")[:200],
            "confidence": conf,
            "confirmations": 1,
            "created_at": stamp,
            "last_seen_at": stamp,
            "ttl_days": ttl,
            "taint": TAINT,
        })
        if len(rows) > POOL_ENTRY_CAP:
            rows = rows[-POOL_ENTRY_CAP:]
        return lid if _write(path, rows) else None


def recall_shared(signature: str = "", *, count: int = RECALL_COUNT_CAP,
                  char_budget: int = RECALL_CHAR_CAP,
                  environ: Optional[Mapping[str, str]] = None,
                  now: Optional[datetime] = None) -> List[dict]:
    """回读当前作用域下匹配 signature 的教训（按 confidence 降序，再受条数/字符双上限）。

    signature 为空 = 不限签名（同作用域全部）。过期（created_at + ttl_days < now）跳过。
    """
    if not shared_enabled(environ):
        return []
    scope = current_scope()
    if scope is None:
        return []
    tenant, subject = scope
    path = _pool_path(environ)
    if path is None:
        return []
    rows = _load(path)
    if not rows:
        return []
    sig = str(signature or "").strip()
    moment = now or datetime.now(timezone.utc)
    valid: List[dict] = []
    for row in rows:
        if row.get("tenant") != tenant or row.get("subject") != subject:
            continue
        if sig and row.get("signature") != sig:
            continue
        created = _parse_ts(row.get("created_at"))
        try:
            ttl = int(row.get("ttl_days"))
        except (TypeError, ValueError):
            ttl = DEFAULT_TTL_DAYS
        if created + timedelta(days=ttl) < moment:
            continue
        valid.append(row)
    valid.sort(key=lambda r: (float(r.get("confidence") or 0.0), str(r.get("last_seen_at") or "")),
               reverse=True)
    out: List[dict] = []
    budget = max(0, int(char_budget))
    for row in valid:
        if len(out) >= max(0, int(count)):
            break
        text = str(row.get("advice") or "")
        if len(text) + 1 > budget:
            break
        budget -= len(text) + 1
        out.append({
            "lesson_id": row.get("lesson_id"),
            "signature": row.get("signature"),
            "advice": text,
            "confidence": row.get("confidence"),
            "confirmations": row.get("confirmations"),
            "taint": TAINT,
        })
    return out


def erase(lesson_id_value: str, *, environ: Optional[Mapping[str, str]] = None) -> int:
    """在当前作用域内擦除指定 lesson（原子重写）；返回删除条数。

    【为什么要求作用域】防止用 A 主体的 id 擦掉 B 主体的记录（I1 的擦除面同样适用）。
    """
    lid = str(lesson_id_value or "").strip()
    if not lid:
        return 0
    scope = current_scope()
    if scope is None:
        return 0
    tenant, subject = scope
    path = _pool_path(environ)
    if path is None or not path.exists():
        return 0
    with _write_lock:
        rows = _load(path)
        if not rows:
            return 0
        kept = [r for r in rows
                if not (r.get("lesson_id") == lid
                        and r.get("tenant") == tenant and r.get("subject") == subject)]
        removed = len(rows) - len(kept)
        if removed and not _write(path, kept):
            return 0
        return removed
