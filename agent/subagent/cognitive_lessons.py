"""cognitive_loop 跨 workspace / 跨实例共享教训池（二期：提升闸门）。

设计：`docs/proposals/cognitive_loop_cross_workspace_learning.md`（2026-10-10，草稿）。
一期（方案 A：共享文件系统 + 隔离键 + 双上限 + erase + 审计）已落地；本模块二期补上
**提升闸门（I2）**：candidate → shared 需 N 个**不同 instance_id** 的独立确认，或人工
`promote()` 批准。

【安全不变量】
  I1 隔离：记录带 (tenant, subject)；检索只返回**当前作用域完全匹配**的记录；无作用域 ⇒ 不读不写。
  I2 提升闸门：进池默认 `candidate`（同主体可见，低风险）；`shared` 需 N 个不同 instance_id
     的独立确认，或人工批准。同一 instance 反复记录**不**自证（按 instance_id 去重）。
  I3 注入即数据：记录固定 taint=upstream，回读后由调用方当"历史教训（数据）"拼入，绝不当指令。
  I4 fail-closed：目录未配置 / 文件损坏 / 写失败 ⇒ 退化为空（不注入、不假装成功），且不影响本地流水。
  I5 可遗忘：erase(lesson_id) 在**当前作用域内**原子重写，擦除后不再注入。

【instance_id 口径（二期自行确定，2026-10-12）】
  含义：**云枢母体部署实例**的稳定标识，**只用于"独立确认"去重，不参与可见性**
  （可见性仍由 tenant/subject 决定，见 I1）。解析顺序：
    1) 显式 `CP_SUBAGENT_INSTANCE_ID`（生产多实例部署应显式设置，跨容器重启身份稳定）；
    2) 兜底 = `host-<sha256(hostname|仓库根)[:16]>`（稳定、不落盘；同机同仓 = 同一实例，
       跨主机/跨仓自然区分）。兜底不保证跨容器重启稳定 ⇒ 生产请用 (1)。

【本次落地的默认假设（方案 §12 待决策项）】
  1) 存储：方案 A（共享文件系统），非母体服务；
  2) subject/tenant：直接取母体 `DelegationContext.tenant_id / subject_id`，不另行派生；
  3) 提升闸门：默认 `N = DEFAULT_PROMOTION_N`（3）个不同 instance_id 自动 candidate→shared；
     也可 `promote()` 人工批准；`N = 0` 表示只走人工批准；
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
    "ENV_SHARED", "ENV_DIR", "ENV_INSTANCE_ID", "ENV_PROMOTION_N",
    "LESSONS_FILENAME", "SCHEMA_VERSION", "TAINT",
    "STATE_LOCAL", "STATE_CANDIDATE", "STATE_SHARED",
    "DEFAULT_TTL_DAYS", "POOL_ENTRY_CAP", "ADVICE_CHAR_CAP",
    "RECALL_COUNT_CAP", "RECALL_CHAR_CAP",
    "DEFAULT_PROMOTION_N", "CONFIRMED_BY_CAP", "MIN_RECALL_CONFIDENCE",
    "shared_enabled", "lessons_dir", "promotion_n", "instance_id",
    "scope_context", "current_scope", "current_workspace_id",
    "lesson_id", "record_lesson", "recall_shared", "promote", "erase",
]

#: 开关与目录（注册于 agent/settings/registry.py）
ENV_SHARED = "CP_SUBAGENT_COGNITIVE_SHARED_LESSONS"
ENV_DIR = "CP_SUBAGENT_COGNITIVE_LESSONS_DIR"
#: 部署实例身份（二期；只用于"独立确认"去重，不参与可见性）
ENV_INSTANCE_ID = "CP_SUBAGENT_INSTANCE_ID"
#: candidate→shared 所需的"不同 instance_id 独立确认数"（0 = 只走人工批准）
ENV_PROMOTION_N = "CP_SUBAGENT_COGNITIVE_PROMOTION_N"

LESSONS_FILENAME = "lessons.jsonl"
SCHEMA_VERSION = 2
#: 进池文本固定标 upstream（注入时保持）
TAINT = "upstream"

#: 提升状态机（方案 §7）
STATE_LOCAL = "local"          # 仅来源 workspace/实例可见
STATE_CANDIDATE = "candidate"  # 同 (tenant, subject) 可见（新建默认）
STATE_SHARED = "shared"        # 已达闸门 / 人工批准

DEFAULT_TTL_DAYS = 90
POOL_ENTRY_CAP = 2000
ADVICE_CHAR_CAP = 500
RECALL_COUNT_CAP = 5
RECALL_CHAR_CAP = 2000
#: 提升闸门：N 个不同 instance_id 的独立确认（方案 §7 建议 3，可配）
DEFAULT_PROMOTION_N = 3
#: confirmed_by 去重列表硬上限（防单条记录无界增长）
CONFIRMED_BY_CAP = 50
#: 回读置信度下限：低于它不注入（默认 0.0 = 不启用该闸）
MIN_RECALL_CONFIDENCE = 0.0

#: 当前委派作用域（由母体 executor 从 DelegationContext 设定；ContextVar 保证并发委派互不串）
_SCOPE: ContextVar[Optional[Tuple[str, str]]] = ContextVar("cognitive_lessons_scope", default=None)
#: 当前 workspace 标识（只作来源标注，不参与可见性/闸门）
_WORKSPACE: ContextVar[str] = ContextVar("cognitive_lessons_workspace", default="")
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
def scope_context(tenant: str, subject: str, *, workspace_id: str = "") -> Iterator[None]:
    """在 with 块内设定当前 (tenant, subject) 作用域；缺失任一 ⇒ 置空（fail-closed）。

    workspace_id 只作**来源标注**（不参与可见性/闸门），可空。

    【为什么用 ContextVar】同一个 executor 会被并发委派共享；实例级字段会把 A 委派的
    作用域串到 B 委派。ContextVar 随调用线程/任务隔离，且不改变 ChannelInvocation 的
    "不带域标识" 约定。
    """
    t = str(tenant or "").strip()
    s = str(subject or "").strip()
    token = _SCOPE.set((t, s) if (t and s) else None)
    wtoken = _WORKSPACE.set(str(workspace_id or "").strip())
    try:
        yield
    finally:
        _WORKSPACE.reset(wtoken)
        _SCOPE.reset(token)


def current_scope() -> Optional[Tuple[str, str]]:
    """当前作用域；无 / 不完整 ⇒ None"""
    return _SCOPE.get()


def current_workspace_id() -> str:
    """当前 workspace 标识（来源标注；无 ⇒ ""）"""
    return _WORKSPACE.get()


def _env_value(key: str, environ: Optional[Mapping[str, str]] = None) -> str:
    raw = os.environ.get(key, "") if environ is None else environ.get(key, "")
    return str(raw or "").strip()


def promotion_n(environ: Optional[Mapping[str, str]] = None) -> int:
    """candidate→shared 所需的独立确认数（0 = 只走人工批准；非法值回退默认）"""
    raw = _env_value(ENV_PROMOTION_N, environ)
    if not raw:
        return DEFAULT_PROMOTION_N
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PROMOTION_N
    if n < 0:
        return DEFAULT_PROMOTION_N
    return n


def instance_id(environ: Optional[Mapping[str, str]] = None) -> str:
    """当前**部署实例**的稳定标识（只用于独立确认去重，不参与可见性）。

    优先显式 `CP_SUBAGENT_INSTANCE_ID`；否则用 `host-<sha256(hostname|仓库根)[:16]>`
    兜底（同机同仓 = 同一实例；不落盘、可复算）。生产多实例部署应显式设置环境变量，
    以保证跨容器重启身份稳定。
    """
    explicit = _env_value(ENV_INSTANCE_ID, environ)
    if explicit:
        return explicit[:128]
    host = ""
    try:
        import socket
        host = socket.gethostname() or ""
    except Exception:  # noqa: BLE001 拿不到主机名也不能阻断
        host = ""
    try:
        root = str(Path(__file__).resolve().parents[2])
    except (OSError, IndexError):
        root = ""
    digest = hashlib.sha256(("%s|%s" % (host, root)).encode("utf-8")).hexdigest()[:16]
    return "host-%s" % digest


def _normalize_row(row: Mapping[str, Any]) -> dict:
    """把任意版本的行规整为二期视图（旧行按 candidate + 单实例保守解释）。

    一期行没有 state/confirmed_by/instance_id：其 `confirmations` 是"被记录次数"而非
    "独立实例数"，直接沿用会**虚高**闸门 ⇒ 保守视为 1 个来源（"legacy"）。
    """
    inst = str(row.get("instance_id") or "")
    raw_cby = row.get("confirmed_by")
    if isinstance(raw_cby, list):
        cby = [str(x).strip() for x in raw_cby if str(x).strip()]
    else:
        cby = [inst] if inst else []
    if not cby:
        cby = [inst or "legacy"]
    state = str(row.get("state") or "").strip().lower()
    if state not in (STATE_LOCAL, STATE_CANDIDATE, STATE_SHARED):
        state = STATE_CANDIDATE
    return {
        "instance_id": inst or cby[0],
        "workspace_id": str(row.get("workspace_id") or ""),
        "confirmed_by": cby[:CONFIRMED_BY_CAP],
        "confirmations": len(cby[:CONFIRMED_BY_CAP]),
        "state": state,
    }


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
    inst = instance_id(environ)
    workspace = current_workspace_id()
    n = promotion_n(environ)
    with _write_lock:
        rows = _load(path)
        if rows is None:
            return None
        for row in rows:
            if row.get("lesson_id") != lid:
                continue
            view = _normalize_row(row)
            cby = list(view["confirmed_by"])
            # I2：同一 instance 反复记录**不自证**；只有新的 instance_id 才 +1
            if inst and inst not in cby and len(cby) < CONFIRMED_BY_CAP:
                cby.append(inst)
            state = view["state"]
            if state != STATE_SHARED and n > 0 and len(cby) >= n:
                state = STATE_SHARED
            row.update({
                "schema_version": SCHEMA_VERSION,
                "instance_id": view["instance_id"],
                "workspace_id": view["workspace_id"] or workspace,
                "confirmed_by": cby,
                "confirmations": len(cby),
                "state": state,
                "last_seen_at": stamp,
                "confidence": max(float(row.get("confidence") or 0.0), conf),
            })
            if state == STATE_SHARED and not row.get("promoted_at"):
                row["promoted_at"] = stamp
            return lid if _write(path, rows) else None
        rows.append({
            "schema_version": SCHEMA_VERSION,
            "lesson_id": lid,
            "tenant": tenant,
            "subject": subject,
            "workspace_id": workspace,
            "instance_id": inst,
            "signature": sig,
            "advice": text,
            "evidence": str(evidence or "")[:200],
            "confidence": conf,
            "confirmed_by": [inst],
            "confirmations": 1,
            "state": STATE_CANDIDATE,
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
    me = instance_id(environ)
    valid: List[Tuple[dict, dict]] = []
    for row in rows:
        if row.get("tenant") != tenant or row.get("subject") != subject:
            continue
        if sig and row.get("signature") != sig:
            continue
        view = _normalize_row(row)
        # local 只对来源实例可见（本模块不主动创建 local；为状态机完整性/人工降级保留）
        if view["state"] == STATE_LOCAL and view["instance_id"] != me:
            continue
        try:
            if float(row.get("confidence") or 0.0) < MIN_RECALL_CONFIDENCE:
                continue
        except (TypeError, ValueError):
            pass
        created = _parse_ts(row.get("created_at"))
        try:
            ttl = int(row.get("ttl_days"))
        except (TypeError, ValueError):
            ttl = DEFAULT_TTL_DAYS
        if created + timedelta(days=ttl) < moment:
            continue
        valid.append((row, view))
    # shared 优先，其次独立确认数，再置信度 / 新近度
    valid.sort(key=lambda rv: (
        1 if rv[1]["state"] == STATE_SHARED else 0,
        int(rv[1]["confirmations"]),
        float(rv[0].get("confidence") or 0.0),
        str(rv[0].get("last_seen_at") or ""),
    ), reverse=True)
    out: List[dict] = []
    budget = max(0, int(char_budget))
    for row, view in valid:
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
            "confirmations": view["confirmations"],
            "state": view["state"],
            "taint": TAINT,
        })
    return out


def promote(lesson_id_value: str, *, approver: str = "",
            environ: Optional[Mapping[str, str]] = None,
            now: Optional[datetime] = None) -> bool:
    """人工批准：把当前作用域内的指定 lesson 提升为 ``shared``（I2 的"或人工批准"）。

    与 erase 同口径：**只在当前 (tenant, subject) 作用域内**生效（防跨主体越权）。
    返回是否改了至少一行；目录/作用域/行缺失一律 False（fail-closed，绝不新建）。
    """
    lid = str(lesson_id_value or "").strip()
    if not lid:
        return False
    scope = current_scope()
    if scope is None:
        return False
    tenant, subject = scope
    path = _pool_path(environ)
    if path is None or not path.exists():
        return False
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    with _write_lock:
        rows = _load(path)
        if not rows:
            return False
        changed = False
        for row in rows:
            if (row.get("lesson_id") == lid
                    and row.get("tenant") == tenant and row.get("subject") == subject):
                view = _normalize_row(row)
                row.update({
                    "schema_version": SCHEMA_VERSION,
                    "instance_id": view["instance_id"],
                    "workspace_id": view["workspace_id"],
                    "confirmed_by": view["confirmed_by"],
                    "confirmations": view["confirmations"],
                    "state": STATE_SHARED,
                    "approved_by": str(approver or "")[:120],
                    "promoted_at": stamp,
                })
                changed = True
        if not changed:
            return False
        return _write(path, rows)


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
