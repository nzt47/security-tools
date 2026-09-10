"""统一 Trace 规格 + TraceContext 全链透传（TASK-S2-01 / v7.2 §3.4 / §2.7）

在既有 ``tool_trace.py``（工具级 SQLite 轨迹）**之上**扩展统一 schema，提供向上兼容的
公共 Trace 门面，供消化流水线（S3）、审计（S2-02）、评测（S5）共用统一轨迹台账。
**不删除任何既有写入方**（tool_trace / subscriber / orchestrator 零破坏）。

设计要点（对齐任务书）：

- **统一而不另起炉灶**：内部复用既有 SQLite 批量异步写入模式，写入**同一个**数据库文件
  ``agent/data/tool_trace.db``（双表同库：既有的 ``tool_traces`` 表 + 新增的
  ``unified_traces`` 表），不新增第二存储介质/第二存储轨。
- **字段对齐 §3.4**：trace_id / task_id / capability_id / actor(human|auto|sub_agent)
  / tenancy(tenant/workspace) / request(args_redacted + args_hash + idempotency_key)
  / response(status + output_redacted + output_hash + error_code) / timing / cost
  / side_effects / parent_trace_id / schema_version。
- **脱敏先于哈希**：``redact → hash → 持久化`` 顺序硬编码在 ``_redact_then_hash``，
  原文（args/output）绝不入账，仅存 redacted + hash（复用既有 ``sensitive_data_filter``）。
- **TraceContext 穿透**：``trace_id/task_id/tenant_id/workspace_id/subject_id/policy_version``
  随调用链透传（结构化 ContextVar）；``child()`` 自动串联 ``parent_trace_id``。
- **append-only 台账**：``unified_traces`` 只 INSERT（``clear()`` 为测试专用显式动作）；
  无 UPDATE/DELETE 写路径。
- **workspace_id 不变量（P7.1-19）**：缺 workspace_id 的持久化记录默认**显式降级**
  （入 ring buffer + 告警，不阻断主路径）；``strict=True`` 时抛 ``MissingWorkspaceError``。

公开 API：
    UnifiedTrace / Tenancy / Request / Response / Timing / Cost / SideEffects
    TraceContext / TraceFacade / UnifiedTraceStore
    redact / hash_content / redact_then_hash / generate_trace_id / derive_workspace_id
    TraceValidationError / MissingWorkspaceError
    ACTOR_HUMAN / ACTOR_AUTO / ACTOR_SUB_AGENT
    STATUS_SUCCESS / STATUS_ERROR / STATUS_BLOCKED
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import queue as queue_module
import sqlite3
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agent.observability.trace_v2")

# ════════════════════════════════════════════════════════════
#  路径与配置
# ════════════════════════════════════════════════════════════

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 与 tool_trace.py 同库（双表同库，不新增第二存储轨）
_DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, "agent", "data", "tool_trace.db")

SCHEMA_VERSION = 1
RING_BUFFER_MAXLEN = 1000
WRITER_BATCH_SIZE = 100
WRITER_POLL_INTERVAL = 1.0

# actor 取值（§3.4）
ACTOR_HUMAN = "human"
ACTOR_AUTO = "auto"
ACTOR_SUB_AGENT = "sub_agent"
_ACTORS = frozenset({ACTOR_HUMAN, ACTOR_AUTO, ACTOR_SUB_AGENT})

# response.status 取值
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_BLOCKED = "blocked"
_STATUSES = frozenset({STATUS_SUCCESS, STATUS_ERROR, STATUS_BLOCKED})

# 结构化 TraceContext ContextVar（扩展 tool_trace 的 _trace_id_var 为结构化对象）
_trace_context_var: contextvars.ContextVar = contextvars.ContextVar(
    "unified_trace_context", default=None
)


# ════════════════════════════════════════════════════════════
#  基础工具
# ════════════════════════════════════════════════════════════


def generate_trace_id() -> str:
    """生成 16 位十六进制 Trace ID（与 tracer.py / tool_trace.py 同格式）"""
    return uuid.uuid4().hex[:16]


def _normalize_for_hash(data: Any) -> str:
    """任意对象 → 稳定可哈希字符串（sort_keys 保证同构 dict 同哈希）"""
    try:
        return json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)
    except Exception:
        return str(data)


def hash_content(data: Any) -> str:
    """SHA256 前 16 位哈希（不存原文）"""
    return hashlib.sha256(_normalize_for_hash(data).encode("utf-8")).hexdigest()[:16]


def derive_workspace_id(workspace_root: str) -> str:
    """P7.1-19：会话工作区根目录哈希 → workspace_id（workspace-hash 默认）

    确定性：同路径（大小写不敏感、规范化为绝对路径）恒产生同一 workspace_id。
    """
    root = (workspace_root or "").strip()
    if not root:
        return ""
    norm = os.path.normcase(os.path.normpath(os.path.abspath(root)))
    return "ws_" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


# 敏感字段名模式（脱敏兜底用，主路径复用 sensitive_data_filter）
_SENSITIVE_KEY_RE_PATTERNS = (
    "password", "passwd", "pwd", "secret", "api_key", "apikey", "token",
    "auth", "credential", "private_key", "privatekey", "access_token",
    "refresh_token", "authorization", "client_secret",
)

_REDACTED = "********"


def _fallback_redact(data: Any) -> Any:
    """脱敏兜底：sensitive_data_filter 不可用时，按字段名/文本做最小掩码"""
    if isinstance(data, dict):
        out: Dict[str, Any] = {}
        for k, v in data.items():
            key = str(k)
            if key.lower() in _SENSITIVE_KEY_RE_PATTERNS or any(
                    p in key.lower() for p in _SENSITIVE_KEY_RE_PATTERNS):
                out[k] = _REDACTED
            elif isinstance(v, (dict, list)):
                out[k] = _fallback_redact(v)
            elif isinstance(v, str):
                out[k] = _fallback_redact_text(v)
            else:
                out[k] = v
        return out
    if isinstance(data, list):
        return [_fallback_redact(it) for it in data]
    if isinstance(data, str):
        return _fallback_redact_text(data)
    return data


def _fallback_redact_text(text: str) -> str:
    """文本级兜底脱敏：sk-/ghp_/AKIA/eyJ(JWT)/Bearer 等关键密钥形态"""
    import re
    rules = (
        (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), _REDACTED),
        (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), _REDACTED),
        (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _REDACTED),
        (re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), _REDACTED),
        (re.compile(r"(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer " + _REDACTED),
    )
    result = text
    for pattern, repl in rules:
        result = pattern.sub(repl, result)
    return result


def redact(data: Any) -> Any:
    """脱敏任意数据（复用既有 sensitive_data_filter；不可用时兜底掩码）。

    返回**脱敏后**内容（args_redacted/output_redacted 的来源），原文丢弃。
    """
    try:
        from agent.utils.sensitive_data_filter import filter_sensitive_data
        return filter_sensitive_data(data)
    except Exception:  # noqa: BLE001  过滤器不可用时兜底，绝不抛异常
        return _fallback_redact(data)


def redact_then_hash(data: Any) -> tuple:
    """脱敏 → 哈希 顺序硬编码：返回 (redacted, hash(redacted))。

    Why 顺序关键：若先哈希再脱敏，哈希将绑定原文（可被字典攻击还原）；
    先脱敏再哈希使哈希只绑定脱敏后内容，原文不可恢复（§3.4 / 验收标准）。
    """
    redacted = redact(data)
    return redacted, hash_content(redacted)


# ════════════════════════════════════════════════════════════
#  错误
# ════════════════════════════════════════════════════════════


class TraceValidationError(Exception):
    """统一 Trace 校验失败"""

    def __init__(self, message: str, *, code: str = "TRACE_VALIDATION"):
        super().__init__(message)
        self.code = code


class MissingWorkspaceError(TraceValidationError):
    """缺 workspace_id（P7.1-19：持久化写入失败/显式降级的不变量落点）"""

    def __init__(self, trace_id: str = ""):
        super().__init__(
            f"trace {trace_id!r} 缺 workspace_id（P7.1-19 不变量）",
            code="MISSING_WORKSPACE",
        )


# ════════════════════════════════════════════════════════════
#  TraceContext（结构化 ContextVar，§2.7）
# ════════════════════════════════════════════════════════════


@dataclass
class TraceContext:
    """结构化追踪上下文（§2.7）：trace_id/task_id/tenant_id/workspace_id/subject_id/
    policy_version；``parent_trace_id`` 由 ``child()`` 自动串联（云枢裁定扩展字段）。

    ``started_at`` 为内部计时字段（非 §2.7 六字段，供 task 级 Trace 计时），不参与
    持久化 schema。
    """

    trace_id: str = ""
    task_id: str = ""
    tenant_id: str = "default"
    workspace_id: str = ""
    subject_id: str = ""
    policy_version: str = ""
    parent_trace_id: str = ""
    started_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.trace_id:
            self.trace_id = generate_trace_id()
        if not self.task_id:
            self.task_id = self.trace_id
        if not self.started_at:
            self.started_at = time.time()

    # ── ContextVar 接入 ──

    @classmethod
    def current(cls) -> Optional["TraceContext"]:
        """读取当前线程/协程的结构化追踪上下文（无则 None）"""
        return _trace_context_var.get()

    def enter(self) -> contextvars.Token:
        """设置当前上下文，返回 Token（配合 ``exit()`` 精确恢复）"""
        return _trace_context_var.set(self)

    @classmethod
    def exit(cls, token: contextvars.Token) -> None:
        """恢复进入前的上下文（幂等：跨 context 时降级为 None，不抛异常）"""
        try:
            _trace_context_var.reset(token)
        except Exception:  # noqa: BLE001  跨 context reset 抛 ValueError → 降级
            try:
                _trace_context_var.set(None)
            except Exception:  # noqa: BLE001
                pass

    def child(self) -> "TraceContext":
        """派生子 Trace 上下文：新 trace_id，``parent_trace_id`` 自动指向父 trace_id。

        task_id / tenant_id / workspace_id / subject_id / policy_version 继承。
        """
        return TraceContext(
            trace_id=generate_trace_id(),
            task_id=self.task_id,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            subject_id=self.subject_id,
            policy_version=self.policy_version,
            parent_trace_id=self.trace_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "policy_version": self.policy_version,
            "parent_trace_id": self.parent_trace_id,
        }


# ════════════════════════════════════════════════════════════
#  统一 Trace 数据类（§3.4 字段组）
# ════════════════════════════════════════════════════════════


@dataclass
class Tenancy:
    """tenancy(tenant/workspace)：workspace_id 为 P7.1-19 不变量（必填非空）"""

    tenant_id: str = "default"
    workspace_id: str = ""


@dataclass
class Request:
    """request(args_redacted + args_hash + idempotency_key)"""

    args_redacted: Any = None
    args_hash: str = ""
    idempotency_key: str = ""


@dataclass
class Response:
    """response(status + output_redacted + output_hash + error_code)"""

    status: str = STATUS_SUCCESS
    output_redacted: Any = None
    output_hash: str = ""
    error_code: str = ""


@dataclass
class Timing:
    """timing（墙钟秒 + 毫秒时长）"""

    started_at: float = 0.0
    finished_at: Optional[float] = None
    duration_ms: Optional[float] = None


@dataclass
class Cost:
    """cost（token + 美元）"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class SideEffects:
    """side_effects（文件/外部调用等副作用清单，供审计与回滚）"""

    files_written: List[str] = field(default_factory=list)
    files_deleted: List[str] = field(default_factory=list)
    external_calls: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class UnifiedTrace:
    """统一 Trace 记录（§3.4）

    字段组：meta(trace_id/task_id/capability_id/schema_version) · actor ·
    tenancy(tenant/workspace) · request(args_redacted+args_hash+idempotency_key) ·
    response(status+output_redacted+output_hash+error_code) · timing · cost ·
    side_effects · parent(parent_trace_id)。
    """

    trace_id: str
    task_id: str
    capability_id: str
    actor: str = ACTOR_AUTO
    tenancy: Tenancy = field(default_factory=Tenancy)
    request: Request = field(default_factory=Request)
    response: Response = field(default_factory=Response)
    timing: Timing = field(default_factory=Timing)
    cost: Cost = field(default_factory=Cost)
    side_effects: SideEffects = field(default_factory=SideEffects)
    parent_trace_id: str = ""
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> List[str]:
        """域级校验（返回问题清单；workspace_id 缺失为第一条）"""
        issues: List[str] = []
        if not self.trace_id:
            issues.append("trace_id 缺失")
        if not self.task_id:
            issues.append("task_id 缺失")
        if self.actor not in _ACTORS:
            issues.append(f"actor 非法: {self.actor!r}（{sorted(_ACTORS)}）")
        if not self.tenancy.workspace_id:
            issues.append("workspace_id 缺失（P7.1-19 不变量）")
        if self.response.status not in _STATUSES:
            issues.append(f"status 非法: {self.response.status!r}")
        return issues

    def to_dict(self) -> Dict[str, Any]:
        """扁平化 JSON 字典（枚举全为字符串；redacted 内容原样保留）"""
        return {
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "capability_id": self.capability_id,
            "actor": self.actor,
            "tenant_id": self.tenancy.tenant_id,
            "workspace_id": self.tenancy.workspace_id,
            "args_redacted": self.request.args_redacted,
            "args_hash": self.request.args_hash,
            "idempotency_key": self.request.idempotency_key,
            "status": self.response.status,
            "output_redacted": self.response.output_redacted,
            "output_hash": self.response.output_hash,
            "error_code": self.response.error_code,
            "started_at": self.timing.started_at,
            "finished_at": self.timing.finished_at,
            "duration_ms": self.timing.duration_ms,
            "input_tokens": self.cost.input_tokens,
            "output_tokens": self.cost.output_tokens,
            "total_tokens": self.cost.total_tokens,
            "cost_usd": self.cost.cost_usd,
            "files_written": list(self.side_effects.files_written),
            "files_deleted": list(self.side_effects.files_deleted),
            "external_calls": list(self.side_effects.external_calls),
            "notes": list(self.side_effects.notes),
            "parent_trace_id": self.parent_trace_id,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UnifiedTrace":
        d = dict(data)
        return cls(
            trace_id=str(d.get("trace_id") or ""),
            task_id=str(d.get("task_id") or ""),
            capability_id=str(d.get("capability_id") or ""),
            actor=str(d.get("actor") or ACTOR_AUTO),
            tenancy=Tenancy(
                tenant_id=str(d.get("tenant_id") or "default"),
                workspace_id=str(d.get("workspace_id") or ""),
            ),
            request=Request(
                args_redacted=d.get("args_redacted"),
                args_hash=str(d.get("args_hash") or ""),
                idempotency_key=str(d.get("idempotency_key") or ""),
            ),
            response=Response(
                status=str(d.get("status") or STATUS_SUCCESS),
                output_redacted=d.get("output_redacted"),
                output_hash=str(d.get("output_hash") or ""),
                error_code=str(d.get("error_code") or ""),
            ),
            timing=Timing(
                started_at=float(d.get("started_at") or 0.0),
                finished_at=d.get("finished_at"),
                duration_ms=d.get("duration_ms"),
            ),
            cost=Cost(
                input_tokens=int(d.get("input_tokens") or 0),
                output_tokens=int(d.get("output_tokens") or 0),
                total_tokens=int(d.get("total_tokens") or 0),
                cost_usd=float(d.get("cost_usd") or 0.0),
            ),
            side_effects=SideEffects(
                files_written=list(d.get("files_written") or []),
                files_deleted=list(d.get("files_deleted") or []),
                external_calls=list(d.get("external_calls") or []),
                notes=list(d.get("notes") or []),
            ),
            parent_trace_id=str(d.get("parent_trace_id") or ""),
            schema_version=int(d.get("schema_version") or SCHEMA_VERSION),
        )


# ════════════════════════════════════════════════════════════
#  UnifiedTraceStore（append-only SQLite + 批量异步 writer，同库）
# ════════════════════════════════════════════════════════════


class UnifiedTraceStore:
    """统一 Trace 存储（append-only，双表同库 tool_trace.db）

    - 批量异步 writer：``record()`` 仅入队，后台 daemon 线程批量写 SQLite（与
      tool_trace.py 同一模式）。
    - 降级：SQLite 失败 → 内存 ring buffer，不抛异常（守【不易】）。
    - append-only：仅 INSERT；``clear()`` 为测试专用显式动作（DELETE）。
    - workspace_id 不变量：缺失时默认降级（ring buffer + 告警），``strict=True`` 抛
      ``MissingWorkspaceError``。
    """

    _COLUMNS = (
        "trace_id", "task_id", "capability_id", "parent_trace_id", "workspace_id",
        "tenant_id", "actor", "status", "error_code", "started_at", "finished_at",
        "duration_ms", "input_tokens", "output_tokens", "total_tokens", "cost_usd",
        "schema_version", "payload",
    )

    def __init__(self, db_path: Optional[str] = None, *, strict: bool = False):
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._strict = strict
        self._queue: "queue_module.Queue[UnifiedTrace]" = queue_module.Queue()
        self._fallback_ring_buffer: deque = deque(maxlen=RING_BUFFER_MAXLEN)
        self._degraded: bool = False
        self._stopped: bool = False
        self._write_lock = threading.Lock()
        self._local = threading.local()
        # 写入完成计数（flush 竞态修复，对齐 tool_trace 的 _enqueue/_commit 机制）
        self._enqueue_count = 0
        self._commit_count = 0
        self._count_lock = threading.Lock()
        # 缺 workspace_id 降级计数（供快照/告警）
        self._workspace_degraded_count = 0
        self._workspace_lock = threading.Lock()

        if self._db_path == ":memory:":
            self._init_db()
        else:
            try:
                self._init_db()
            except Exception as e:  # noqa: BLE001
                logger.warning("统一 Trace SQLite 初始化失败，降级到 ring buffer: %s", e)
                self._degraded = True

        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="unified-trace-writer", daemon=True)
        self._writer_thread.start()

    # ── 持久化生命周期 ────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """线程本地连接（同库双表：本模块表与 tool_traces 共用一个 DB 文件）

        busy_timeout 显式设为 5s：与 tool_trace writer 线程同库并发写时等待文件锁，
        而非立即抛 "database is locked"（超时仍失败则由 record/降级路径兜底）。
        """
        if not hasattr(self._local, "conn") or self._local.conn is None:
            if self._db_path == ":memory:":
                # 共享缓存内存库：跨线程（writer 线程 vs 主线程）共用同一张表
                self._local.conn = sqlite3.connect(
                    "file:unified_trace_mem?mode=memory&cache=shared",
                    uri=True, check_same_thread=False)
            else:
                os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
                self._local.conn = sqlite3.connect(
                    self._db_path, check_same_thread=False, timeout=5.0)
            self._local.conn.row_factory = sqlite3.Row
            try:
                self._local.conn.execute("PRAGMA busy_timeout = 5000")
            except Exception:  # noqa: BLE001  非致命：默认 timeout 已覆盖
                pass
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        with self._write_lock:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS unified_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    parent_trace_id TEXT NOT NULL DEFAULT '',
                    workspace_id TEXT NOT NULL DEFAULT '',
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    actor TEXT NOT NULL DEFAULT 'auto',
                    status TEXT NOT NULL DEFAULT 'success',
                    error_code TEXT NOT NULL DEFAULT '',
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    duration_ms REAL,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    cost_usd REAL DEFAULT 0,
                    schema_version INTEGER DEFAULT 1,
                    payload TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ut_cap_time "
                "ON unified_traces(capability_id, started_at)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ut_task_time "
                "ON unified_traces(task_id, started_at)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ut_parent "
                "ON unified_traces(parent_trace_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ut_workspace "
                "ON unified_traces(workspace_id, started_at)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ut_actor "
                "ON unified_traces(actor, started_at)")
            conn.commit()

    def record(self, trace: UnifiedTrace, *, strict: Optional[bool] = None) -> bool:
        """入队一条统一 Trace（append-only）。

        Args:
            trace: 统一 Trace。
            strict: None→用构造时默认；True→缺 workspace_id 抛 MissingWorkspaceError；
                    False→缺 workspace_id 显式降级（ring buffer + 告警）。

        Returns:
            True=正常入队（将异步落 SQLite）；False=显式降级（ring buffer）。
        """
        want_strict = self._strict if strict is None else strict
        if not trace.tenancy.workspace_id:
            if want_strict:
                raise MissingWorkspaceError(trace.trace_id)
            # 显式降级：记入 ring buffer + 告警，不阻断主路径（P7.1-19）
            self._fallback_ring_buffer.append(trace)
            with self._workspace_lock:
                self._workspace_degraded_count += 1
            logger.warning(
                "统一 Trace %s 缺 workspace_id，已显式降级（P7.1-19 不变量）",
                trace.trace_id)
            return False
        try:
            self._queue.put_nowait(trace)
            with self._count_lock:
                self._enqueue_count += 1
            return True
        except Exception:  # noqa: BLE001
            self._fallback_ring_buffer.append(trace)
            return False

    def _row_values(self, t: UnifiedTrace) -> tuple:
        return (
            t.trace_id, t.task_id, t.capability_id, t.parent_trace_id,
            t.tenancy.workspace_id, t.tenancy.tenant_id, t.actor,
            t.response.status, t.response.error_code,
            t.timing.started_at, t.timing.finished_at, t.timing.duration_ms,
            t.cost.input_tokens, t.cost.output_tokens, t.cost.total_tokens,
            t.cost.cost_usd, t.schema_version,
            json.dumps(t.to_dict(), ensure_ascii=False, default=str),
        )

    def _write_to_db(self, records: List[UnifiedTrace]) -> None:
        if not records:
            return
        if self._degraded:
            for r in records:
                self._fallback_ring_buffer.append(r)
            self._mark_committed(len(records))
            return
        try:
            conn = self._get_conn()
            placeholders = ",".join(["?"] * len(self._COLUMNS))
            sql = f"INSERT INTO unified_traces ({','.join(self._COLUMNS)}) VALUES ({placeholders})"
            with self._write_lock:
                conn.executemany(sql, [self._row_values(r) for r in records])
                conn.commit()
            self._mark_committed(len(records))
        except Exception as e:  # noqa: BLE001
            logger.warning("统一 Trace SQLite 批量写入失败，降级到 ring buffer: %s", e)
            self._degraded = True
            for r in records:
                self._fallback_ring_buffer.append(r)
            self._mark_committed(len(records))

    def _mark_committed(self, n: int) -> None:
        with self._count_lock:
            self._commit_count += n

    def _writer_loop(self) -> None:
        while not self._stopped:
            batch: List[UnifiedTrace] = []
            try:
                first = self._queue.get(timeout=WRITER_POLL_INTERVAL)
                if first is None:
                    continue
                batch.append(first)
                while len(batch) < WRITER_BATCH_SIZE:
                    try:
                        item = self._queue.get_nowait()
                        if item is None:
                            continue
                        batch.append(item)
                    except queue_module.Empty:
                        break
            except queue_module.Empty:
                continue
            except Exception as e:  # noqa: BLE001
                logger.debug("unified writer 取队异常: %s", e)
                continue
            if batch:
                self._write_to_db(batch)

    def flush(self, timeout: float = 2.0) -> bool:
        """等待已入队记录全部持久化（测试用；对齐 tool_trace 的 commit 追平机制）"""
        with self._count_lock:
            target = self._enqueue_count
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._count_lock:
                committed = self._commit_count
            if committed >= target:
                return True
            time.sleep(0.01)
        return False

    def stop(self, timeout: float = 5.0) -> bool:
        """优雅停止 writer 线程 + flush 残留（幂等）"""
        if self._stopped:
            return True
        self._stopped = True
        try:
            self._queue.put(None, timeout=1.0)
        except Exception:  # noqa: BLE001
            pass
        if self._writer_thread.is_alive():
            self._writer_thread.join(timeout=timeout)
        residual: List[UnifiedTrace] = []
        while True:
            try:
                item = self._queue.get_nowait()
                if item is None:
                    continue
                residual.append(item)
            except queue_module.Empty:
                break
        if residual:
            self._write_to_db(residual)
        return not self._writer_thread.is_alive()

    def clear(self) -> None:
        """清空表 + ring buffer（**测试专用**显式动作，非运行时写路径）"""
        self._queue.queue.clear()
        self._fallback_ring_buffer.clear()
        if not self._degraded:
            try:
                conn = self._get_conn()
                with self._write_lock:
                    conn.execute("DELETE FROM unified_traces")
                    conn.commit()
            except Exception as e:  # noqa: BLE001
                logger.warning("清空 unified_traces 失败: %s", e)

    # ── 读取接口 ─────────────────────────────────────────────

    def _iter_persisted(self) -> List[UnifiedTrace]:
        # 合并 DB 与 ring buffer（workspace 降级/写失败降级的记录始终可见）
        traces: List[UnifiedTrace] = list(self._fallback_ring_buffer)
        if not self._degraded:
            try:
                conn = self._get_conn()
                rows = conn.execute(
                    "SELECT payload FROM unified_traces ORDER BY started_at ASC").fetchall()
                for row in rows:
                    try:
                        traces.append(UnifiedTrace.from_dict(json.loads(row["payload"])))
                    except Exception:  # noqa: BLE001
                        continue
            except Exception as e:  # noqa: BLE001
                logger.warning("查询统一 Trace 失败: %s", e)
        return traces

    def query(
        self,
        *,
        capability_id: Optional[str] = None,
        task_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        since: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> List[UnifiedTrace]:
        """按 capability/task/parent/since 过滤（返回 UnifiedTrace 列表）"""
        result = []
        for t in self._iter_persisted():
            if capability_id and t.capability_id != capability_id:
                continue
            if task_id and t.task_id != task_id:
                continue
            if parent_id is not None and t.parent_trace_id != parent_id:
                continue
            if since is not None and (t.timing.started_at or 0) < since:
                continue
            result.append(t)
        result.sort(key=lambda t: t.timing.started_at or 0)
        if limit is not None:
            result = result[-limit:]
        return result

    def list_by_capability(self, capability_id: str, limit: int = 100) -> List[UnifiedTrace]:
        """S3 模式挖掘入口：某能力最近 limit 条同类轨迹（≥20 条门槛的数据源）"""
        return self.query(capability_id=capability_id, limit=limit)

    def chain(self, trace_id: str) -> List[UnifiedTrace]:
        """返回以 trace_id 为根的父→子链（含根与全部后代，BFS 按 started_at 排序）。

        parent_trace_id 由 TraceContext.child() 自动串联，故链可经 parent 关系还原。
        """
        all_traces = self._iter_persisted()
        children_map: Dict[str, List[UnifiedTrace]] = {}
        for t in all_traces:
            children_map.setdefault(t.parent_trace_id, []).append(t)
        result: List[UnifiedTrace] = []
        seen = set()
        frontier = [trace_id]
        while frontier:
            cur = frontier.pop(0)
            if cur in seen:
                continue
            seen.add(cur)
            # 根节点自身
            matches = [t for t in all_traces if t.trace_id == cur]
            result.extend(matches)
            for child in children_map.get(cur, []):
                if child.trace_id not in seen:
                    frontier.append(child.trace_id)
        result.sort(key=lambda t: t.timing.started_at or 0)
        return result

    def task_summary(self, task_id: str) -> Dict[str, Any]:
        """任务聚合：步数 / 成本 / 成功率（供 ACR 成本与 S6 面板）

        步＝能力级行（``capability_id`` 非空）；任务级主 Trace（capability_id 空）
        仅提供任务级状态/计时，不计入步数。
        """
        traces = self.query(task_id=task_id)
        steps = [t for t in traces if t.capability_id]
        successes = [t for t in steps if t.response.status == STATUS_SUCCESS]
        total_cost = sum(t.cost.cost_usd for t in steps)
        total_tokens = sum(t.cost.total_tokens for t in steps)
        return {
            "task_id": task_id,
            "step_count": len(steps),
            "success_count": len(successes),
            "failed_count": len(steps) - len(successes),
            "success_rate": round(len(successes) / len(steps), 4) if steps else 0.0,
            "total_cost_usd": round(total_cost, 6),
            "total_tokens": total_tokens,
            "capabilities": sorted({t.capability_id for t in steps if t.capability_id}),
            "workspace_id": (steps[0].tenancy.workspace_id if steps else ""),
        }

    def count(self) -> int:
        return len(self._iter_persisted())

    def snapshot_stats(self) -> Dict[str, Any]:
        traces = self._iter_persisted()
        by_cap: Dict[str, int] = {}
        by_actor: Dict[str, int] = {}
        success = 0
        for t in traces:
            if t.capability_id:
                by_cap[t.capability_id] = by_cap.get(t.capability_id, 0) + 1
            by_actor[t.actor] = by_actor.get(t.actor, 0) + 1
            if t.response.status == STATUS_SUCCESS:
                success += 1
        return {
            "total": len(traces),
            "success_count": success,
            "success_rate": round(success / len(traces), 4) if traces else 0.0,
            "by_capability": by_cap,
            "by_actor": by_actor,
            "workspace_degraded": self._workspace_degraded_count,
            "schema_version": SCHEMA_VERSION,
            "append_only": True,
        }


# ════════════════════════════════════════════════════════════
#  TraceFacade（公共门面，向上兼容）
# ════════════════════════════════════════════════════════════


class TraceFacade:
    """统一 Trace 门面：start / record / finish / query + 读取接口。

    用法::

        facade = TraceFacade.instance()
        tid = facade.start(task_id="t1", workspace_id="ws_x")
        # ... 每个能力调用：
        facade.record("cp.builtin.read_file", args={"path": "..."}, output={"ok": True})
        facade.finish(status="success")

    内部仍走既有 SQLite 批量异步 writer（``UnifiedTraceStore``，双表同库 tool_trace.db），
    不新增第二存储轨。
    """

    _instance: Optional["TraceFacade"] = None
    _instance_lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None, *, strict: bool = False):
        self._store = UnifiedTraceStore(db_path, strict=strict)
        # trace_id -> (TraceContext, started_mono, contextvar_token)
        self._active: Dict[str, tuple] = {}
        self._active_lock = threading.Lock()

    @classmethod
    def instance(cls, *, db_path: Optional[str] = None) -> "TraceFacade":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(db_path)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（测试用）：优雅停止 writer 线程并置空"""
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance._store.stop(timeout=2.0)
                cls._instance = None

    # ── 生命周期 ─────────────────────────────────────────────

    def start(
        self,
        *,
        task_id: str = "",
        tenant_id: str = "default",
        workspace_id: str = "",
        subject_id: str = "",
        policy_version: str = "",
        trace_id: str = "",
    ) -> str:
        """开始一个任务级 Trace：构建 TraceContext、注入 ContextVar，返回 trace_id。

        task_id 缺省等于 trace_id（任务级 Trace 自指）；workspace_id 缺省时由调用方
        经 ``derive_workspace_id``/session_manager 提供（P7.1-19）。
        """
        ctx = TraceContext(
            trace_id=trace_id or generate_trace_id(),
            task_id=task_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            subject_id=subject_id,
            policy_version=policy_version,
        )
        if not ctx.task_id:
            ctx.task_id = ctx.trace_id
        token = ctx.enter()
        with self._active_lock:
            self._active[ctx.trace_id] = (ctx, time.perf_counter(), token)
        return ctx.trace_id

    def record(
        self,
        capability_id: str,
        args: Any = None,
        output: Any = None,
        *,
        actor: str = ACTOR_AUTO,
        status: Optional[str] = None,
        error_code: str = "",
        idempotency_key: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        side_effects: Optional[SideEffects] = None,
        duration_ms: Optional[float] = None,
        started_at: Optional[float] = None,
        finished_at: Optional[float] = None,
        trace_id: Optional[str] = None,
        task_ctx: Optional[TraceContext] = None,
        strict: Optional[bool] = None,
    ) -> UnifiedTrace:
        """记录一次能力调用 Trace（redact→hash→持久化），返回 UnifiedTrace。

        parent 串联：未显式传 trace_id 时，作为当前任务 Trace 的**子 Trace**
        （parent_trace_id = 当前 TraceContext.trace_id）。
        """
        ctx = task_ctx if task_ctx is not None else TraceContext.current()
        args_redacted, args_hash = redact_then_hash(args)
        out_redacted, out_hash = redact_then_hash(output)

        if status is None:
            status = STATUS_SUCCESS
            if isinstance(output, dict) and not bool(output.get("ok", True)):
                status = STATUS_ERROR
                if not error_code:
                    error_code = output.get("error_code") or output.get("error") \
                        or output.get("type") or "ToolError"

        # 子 Trace 串联（parent_trace_id 自动指向父）；无上下文时不臆造父链
        parent_trace_id = ""
        if trace_id is None:
            trace_id = generate_trace_id()
            if ctx is not None and ctx.trace_id:
                parent_trace_id = ctx.trace_id
        task_id = (ctx.task_id if ctx is not None and ctx.task_id else trace_id)
        tenant_id = ctx.tenant_id if ctx is not None else "default"
        workspace_id = ctx.workspace_id if ctx is not None else ""

        now = time.time()
        started = started_at if started_at is not None else now
        finished = finished_at if finished_at is not None else (
            started + (duration_ms / 1000.0) if duration_ms is not None else now)
        dur = duration_ms if duration_ms is not None else (finished - started) * 1000.0

        total_tokens = input_tokens + output_tokens
        trace = UnifiedTrace(
            trace_id=trace_id,
            task_id=task_id,
            capability_id=capability_id,
            actor=actor,
            tenancy=Tenancy(tenant_id=tenant_id, workspace_id=workspace_id),
            request=Request(
                args_redacted=args_redacted,
                args_hash=args_hash,
                idempotency_key=idempotency_key,
            ),
            response=Response(
                status=status,
                output_redacted=out_redacted,
                output_hash=out_hash,
                error_code=error_code,
            ),
            timing=Timing(started_at=started, finished_at=finished, duration_ms=dur),
            cost=Cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cost_usd=cost_usd,
            ),
            side_effects=side_effects or SideEffects(),
            parent_trace_id=parent_trace_id,
            schema_version=SCHEMA_VERSION,
        )
        self._store.record(trace, strict=strict)
        return trace

    def finish(
        self,
        *,
        trace_id: Optional[str] = None,
        status: str = STATUS_SUCCESS,
        error_code: str = "",
        output: Any = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        side_effects: Optional[SideEffects] = None,
    ) -> Optional[UnifiedTrace]:
        """结束任务级 Trace：把任务级主 Trace 一次性落账（append-only，写一次）。

        主 Trace 的 trace_id 取自当前 ContextVar 或显式 trace_id；capability_id 置空
        表示任务级聚合行（子能力 Trace 以 parent_trace_id 链接到它）。
        """
        ctx = TraceContext.current()
        tid = trace_id or (ctx.trace_id if ctx else None)
        if not tid:
            return None
        with self._active_lock:
            active = self._active.pop(tid, None)
        active_ctx, started_mono, token = (active if active is not None
                                           else (ctx, None, None))
        now = time.time()
        started_at = active_ctx.started_at if active_ctx is not None else now
        if started_mono is not None:
            duration_ms = (time.perf_counter() - started_mono) * 1000.0
        else:
            duration_ms = 0.0

        out_redacted, out_hash = redact_then_hash(output)
        total_tokens = input_tokens + output_tokens
        task_id = (active_ctx.task_id if active_ctx is not None and active_ctx.task_id
                   else tid)
        trace = UnifiedTrace(
            trace_id=tid,
            task_id=task_id,
            capability_id="",
            actor=ACTOR_AUTO,
            tenancy=Tenancy(
                tenant_id=active_ctx.tenant_id if active_ctx else "default",
                workspace_id=active_ctx.workspace_id if active_ctx else "",
            ),
            request=Request(args_redacted=None, args_hash="", idempotency_key=""),
            response=Response(
                status=status, output_redacted=out_redacted,
                output_hash=out_hash, error_code=error_code,
            ),
            timing=Timing(started_at=started_at, finished_at=now, duration_ms=duration_ms),
            cost=Cost(
                input_tokens=input_tokens, output_tokens=output_tokens,
                total_tokens=total_tokens, cost_usd=cost_usd,
            ),
            side_effects=side_effects or SideEffects(),
            parent_trace_id="",
            schema_version=SCHEMA_VERSION,
        )
        self._store.record(trace)
        # 清理 ContextVar（精确恢复 start() 之前的状态，防泄漏到下一次任务）
        if token is not None:
            TraceContext.exit(token)
        else:
            try:
                _trace_context_var.set(None)
            except Exception:  # noqa: BLE001
                pass
        return trace

    # ── 读取接口（S3/S5/S6 消费） ─────────────────────────────

    def query(self, *, capability_id=None, task_id=None, parent_id=None, since=None,
              limit=None) -> List[UnifiedTrace]:
        return self._store.query(capability_id=capability_id, task_id=task_id,
                                 parent_id=parent_id, since=since, limit=limit)

    def list_by_capability(self, capability_id: str, limit: int = 100) -> List[UnifiedTrace]:
        return self._store.list_by_capability(capability_id, limit=limit)

    def chain(self, trace_id: str) -> List[UnifiedTrace]:
        return self._store.chain(trace_id)

    def task_summary(self, task_id: str) -> Dict[str, Any]:
        return self._store.task_summary(task_id)

    def snapshot_stats(self) -> Dict[str, Any]:
        return self._store.snapshot_stats()

    def write_stats(self, path: Optional[str] = None) -> Dict[str, Any]:
        """输出 data/trace_stats.json 摘要（S3 判定集与 S5 评测的数据源声明）"""
        stats = self.snapshot_stats()
        target = path or os.path.join(_PROJECT_ROOT, "data", "trace_stats.json")
        try:
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001  best-effort 摘要写出，绝不阻断主路径
            logger.warning("写出 trace_stats.json 失败: %s", e)
        return stats

    def flush(self, timeout: float = 2.0) -> bool:
        return self._store.flush(timeout=timeout)


# ════════════════════════════════════════════════════════════
#  S1-01 遗留 #3：内置/MCP 运行时工具 → DescriptorRegistry 接线
# ════════════════════════════════════════════════════════════


def _list_builtin_entries() -> List[Dict[str, Any]]:
    """best-effort 读取 agent.tools 已注册内置工具（无 schema，仅 name/description）"""
    try:
        from agent import tools
        return list(tools.list_tools() or [])
    except Exception:  # noqa: BLE001
        return []


def load_runtime_descriptors(
    registry: Optional[Any] = None,
    *,
    builtin_entries: Optional[List[Dict[str, Any]]] = None,
    mcp_tools: Optional[List[Dict[str, Any]]] = None,
    mcp_server: str = "runtime",
) -> tuple:
    """内置/MCP 运行时工具真实装载进 DescriptorRegistry（S1-01 遗留 #3 收口）。

    只写 Descriptor 层（advisory），失败仅计 error 不阻断。返回 (registry, summary)。
    """
    from agent.descriptors.bridge import register_bridge_view
    from agent.descriptors.registry import DescriptorRegistry

    reg = registry if registry is not None else DescriptorRegistry()
    summary: Dict[str, Any] = {}
    if builtin_entries is None:
        builtin_entries = _list_builtin_entries()
    if builtin_entries:
        summary["builtin"] = register_bridge_view(
            reg, "builtin", builtin_entries).as_dict()
    if mcp_tools:
        summary["mcp"] = register_bridge_view(
            reg, "mcp", mcp_tools, server=mcp_server).as_dict()
    return reg, summary


def capability_reference(capability_id: str, registry: Optional[Any] = None) -> Dict[str, Any]:
    """§3.4：`trace.capability_id` → descriptor 引用（含 origin/provenance）。

    borrowed/opaque 类能力（外来导入 / MCP / 未内化）必须带 **origin/provenance 引用**
    ——本地代码即证据（builtin=verified）、外来无签名（unknown/declared）在引用中显式
    暴露，并携带 `trace_policy`（borrowed 必记完整轨迹，S1-01 evolution.trace_policy）。

    未登记能力返回 `{"joined": False, ...}`（**诚实口径**：不伪造 provenance）。
    registry 缺省时懒加载 DescriptorRegistry 读取 `data/descriptors.json` 台账。

    Returns:
        {"capability_id", "joined", "source_type", "source_id", "provenance",
         "stage", "trace_policy", "risk_level", "data_class", "external_endpoint"}
    """
    base: Dict[str, Any] = {
        "capability_id": capability_id,
        "joined": False,
        "source_type": None,
        "source_id": None,
        "provenance": None,
        "stage": None,
        "trace_policy": "",
        "risk_level": None,
        "data_class": None,
        "external_endpoint": False,
    }
    if not capability_id:
        return base
    try:
        reg = registry
        if reg is None:
            from agent.descriptors.registry import DescriptorRegistry
            reg = DescriptorRegistry()
        desc = reg.get(capability_id)
    except Exception:  # noqa: BLE001  引用解析失败不抛（advisory）
        return base
    if desc is None:
        return base
    base.update({
        "joined": True,
        "source_type": desc.origin.source_type.value,
        "source_id": desc.origin.source_id,
        "provenance": desc.origin.provenance.value,
        "stage": desc.evolution.stage.value if desc.evolution.stage else None,
        "trace_policy": desc.evolution.trace_policy or "",
        "risk_level": desc.trust.risk_level.value if desc.trust.risk_level else None,
        "data_class": desc.trust.data_class.value if desc.trust.data_class else None,
        "external_endpoint": bool(desc.origin.external_endpoint),
    })
    return base


def capability_reference_for_trace(
    trace: UnifiedTrace, registry: Optional[Any] = None,
) -> Dict[str, Any]:
    """把一条 Trace 的 capability_id 解析为 §3.4 引用（borrowed/opaque 带 provenance）"""
    return capability_reference(trace.capability_id, registry)


__all__ = [
    # schema
    "UnifiedTrace", "Tenancy", "Request", "Response", "Timing", "Cost", "SideEffects",
    # context
    "TraceContext",
    # store + facade
    "UnifiedTraceStore", "TraceFacade",
    # helpers
    "redact", "hash_content", "redact_then_hash", "generate_trace_id",
    "derive_workspace_id", "load_runtime_descriptors",
    "capability_reference", "capability_reference_for_trace",
    # errors
    "TraceValidationError", "MissingWorkspaceError",
    # constants
    "ACTOR_HUMAN", "ACTOR_AUTO", "ACTOR_SUB_AGENT",
    "STATUS_SUCCESS", "STATUS_ERROR", "STATUS_BLOCKED",
    "SCHEMA_VERSION",
]
