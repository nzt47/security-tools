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

TASK-S8-02 写路径加固（``UnifiedTraceStore`` 是本模块**最后一条**未加固的写路径）：

- **跨进程写序列化**：``<tool_trace.db>.lock``（锁原语唯一实现 =
  ``agent.utils.cross_process_lock``；锁文件与被锁 DB **分离**），只管住
  ``executemany + commit``。**不夸大**：SQLite 自身即串行化写者，本锁买的是
  "排队确定 + 降级可见"，不是正确性必要条件（详见类文档）。
- **WAL + ``synchronous=FULL``**：与 ``agent/audit/chain.py::_init_db`` 同口径，
  best-effort（网络盘/只读目录上会失败，退回 rollback journal 仍正确）。
- **有界队列 + 显式满溢行为**：队列满**不丢**，转 ring buffer 并**计数 + 限流告警**
  （原实现的 ``except Exception`` 因队列无界而是死代码）。
- **ring buffer 丢弃不再静默**：显式驱逐最旧 + ``ring_buffer_dropped`` 计数 +
  限流告警 + 事件留痕（原 ``deque(maxlen=1000)`` 是**无声**数据丢失）。
- **降级可恢复**：成功提交即清除 ``_degraded`` 并计 ``degraded_recoveries``；
  ``consecutive_failures`` 区分"抖动"与"真死"（原实现一次失败终身降级）。
- **``flush()`` 不说谎**：``True`` ＝ "在途清零"（提交**或**显式降级），真正的
  落盘口径看 ``stats()['durable']`` / ``flush_durable()``。
- **读侧腐坏可见**：坏行计 ``skipped_row_count`` + 限流告警；``verify_integrity()``
  提供**显式 opt-in** 的哈希一致性抽查（默认**不**在读路径上跑）。

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

# 【唯一锁实现（硬约束）】跨进程写序列化**只**用 agent.utils.cross_process_lock：
# 本仓库历史上同一套 OS 原语被手抄过三份（各自长出不同的超时/重入语义），
# TASK-S8-02 明令禁止出现第 N 份实现。本模块只**使用**该工具，不复制任何平台分支的
# 锁代码（验收口径：全仓 ``agent/`` 下与平台锁 API 同名的符号只允许出现在该工具里）。
from agent.utils.cross_process_lock import (
    CrossProcessLock,
    LockError,
    lock_path_for,
)

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

# ════════════════════════════════════════════════════════════
#  TASK-S8-02 写路径加固配置（**默认保守**：开锁、有限等待、不静默丢）
# ════════════════════════════════════════════════════════════
#
# 【为什么直读 env 而不进 observability_config】该模块由别的任务拥有（本任务硬约束
# 禁止修改）；本模块沿用既有 ``AUDIT_TRACE_EVENTS`` 的直读风格，避免新增模块耦合边。
#
#: 跨进程写锁总开关（"0" ⇒ 退回只靠 SQLite 的 busy_timeout；**仅供测试/应急**）
ENV_LOCK_ENABLED = "CP_TRACE_LOCK_ENABLED"
#: 取锁**有限等待**上限（秒）。有限是硬要求：取不到锁**不丢数据**（仍照写），
#: 但绝不允许无限阻塞把 writer 线程钉死。
ENV_LOCK_TIMEOUT_SEC = "CP_TRACE_LOCK_TIMEOUT_SEC"
#: 有界队列容量（条）。满 ⇒ 转 ring buffer + 计数 + 告警（**不丢**）。
ENV_QUEUE_MAXSIZE = "CP_TRACE_QUEUE_MAXSIZE"
#: ring buffer 容量（条）。满 ⇒ 驱逐最旧 + ``ring_buffer_dropped`` 计数（**不静默**）。
ENV_RING_BUFFER_MAXLEN = "CP_TRACE_RING_BUFFER_MAXLEN"
#: 降级态的**重试退避**（秒）。0 = 每批都重试（用例/应急）。
ENV_DEGRADED_RETRY_SEC = "CP_TRACE_DEGRADED_RETRY_SEC"
#: WAL 自动 checkpoint 阈值（页）。0 = 关闭自动 checkpoint（仅排障）。
ENV_WAL_AUTOCHECKPOINT = "CP_TRACE_WAL_AUTOCHECKPOINT"
#: 告警限流间隔：第 1 次必报，其后每 N 次报一次（**计数不限流**）
ENV_CORRUPT_LOG_EVERY = "CP_TRACE_CORRUPT_LOG_EVERY"

DEFAULT_LOCK_TIMEOUT_SEC = 2.0
DEFAULT_QUEUE_MAXSIZE = 20000
DEFAULT_DEGRADED_RETRY_SEC = 2.0
#: SQLite 默认值（约 4MB WAL）。见 ``_apply_durability_pragmas`` 的实测说明。
DEFAULT_WAL_AUTOCHECKPOINT = 1000
DEFAULT_CORRUPT_LOG_EVERY = 100

#: 写路径降级/溢出的事件类型（事件层结构化留痕；计数只在进程内存里，进程一退就没了）
EVENT_TRACE_QUEUE_FULL = "trace.store.queue_full"
EVENT_TRACE_OVERFLOW = "trace.store.overflow"
EVENT_TRACE_DEGRADED = "trace.store.degraded"

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

    S2-02（S2-01 遗留 #3）：脱敏**确实发生**时把该动作写入链式审计
    （`trace.redact`，只记字段数与字段名，**绝不记被脱敏的值**）。
    """
    redacted = redact(data)
    _audit_redaction_event(data, redacted)
    return redacted, hash_content(redacted)


def _safe_changed(original: Any, redacted: Any) -> bool:
    """安全比较（对象可能不支持 == / 返回数组等）——异常视为未变更"""
    try:
        return bool(original != redacted)
    except Exception:  # noqa: BLE001 不可比较对象 → 不判定为变更
        return False


def _redacted_kinds(original: Any, redacted: Any) -> tuple:
    """统计脱敏变更 → (变更计数, 顶层字段名清单)

    只暴露**字段名**，不暴露任何值（避免把被脱敏的内容写进审计链）。
    """
    if isinstance(original, dict) and isinstance(redacted, dict):
        kinds = [str(k) for k in original
                 if k in redacted and _safe_changed(original.get(k), redacted.get(k))]
        return len(kinds), kinds
    if isinstance(original, list) and isinstance(redacted, list):
        changed = sum(1 for a, b in zip(original, redacted) if _safe_changed(a, b))
        if len(original) != len(redacted):
            changed = max(changed, 1)
        return changed, ["[list]"] if changed else []
    if _safe_changed(original, redacted) and original not in (None, "", {}, []):
        return 1, ["[text]"]
    return 0, []


def _audit_redaction_event(original: Any, redacted: Any) -> None:
    """脱敏动作入链（best-effort；无变更则不产生审计噪声）"""
    try:
        if not _env_audit_trace_events():
            return
        count, kinds = _redacted_kinds(original, redacted)
        if count <= 0:
            return
        from agent.audit.facade import audit as _audit_facade
        _audit_facade.record_redact_event(field_count=count, kinds=kinds)
    except Exception:  # noqa: BLE001 审计失败绝不影响 trace 写入
        pass


def _env_audit_trace_events() -> bool:
    """Trace 关键事件是否入链（环境开关 AUDIT_TRACE_EVENTS，默认 1）"""
    return os.getenv("AUDIT_TRACE_EVENTS", "1").strip().lower() not in (
        "0", "false", "no", "off")


def _env_flag(name: str, default: str = "1") -> bool:
    """布尔环境开关（缺省/空值 → 默认；口径与 ``events.py::_env_flag`` 一致）"""
    return str(os.getenv(name, default)).strip().lower() not in (
        "0", "false", "no", "off")


def _env_float(name: str, default: float) -> float:
    """浮点环境变量（非法/缺省 → 默认；**绝不抛**：坏配置不该打断 trace 写入）"""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("%s 非法（%r），回落默认 %s", name, raw, default)
        return float(default)


def _env_int(name: str, default: int) -> int:
    """整型环境变量（非法/缺省 → 默认；**绝不抛**）"""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return int(default)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("%s 非法（%r），回落默认 %s", name, raw, default)
        return int(default)


def _audit_trace_closed(trace: "UnifiedTrace") -> None:
    """任务级 Trace 收尾入链（`trace.closed`，best-effort）"""
    try:
        if not _env_audit_trace_events():
            return
        from agent.audit.facade import audit as _audit_facade
        _audit_facade.record_trace_event(trace, event="trace.closed")
    except Exception:  # noqa: BLE001
        pass


def _audit_trace_failure(trace: "UnifiedTrace") -> None:
    """能力级 Trace 失败/阻断入链（`trace.tool.failed`/`trace.tool.blocked`）

    成功调用不逐条入链（避免审计噪声），仅治理相关结果留痕。
    """
    try:
        if not _env_audit_trace_events():
            return
        status = str(getattr(trace.response, "status", "") or "")
        if status not in (STATUS_ERROR, STATUS_BLOCKED):
            return
        from agent.audit.facade import audit as _audit_facade
        _audit_facade.record_trace_event(
            trace, event=f"trace.tool.{status}",
            extra={"error_code": str(getattr(trace.response, "error_code", "") or "")})
    except Exception:  # noqa: BLE001
        pass


def _audit_trace_context_leaf() -> dict:
    """供审计门面读取的 TraceContext 叶子字段（依赖倒置注入用）

    只回传叶子字段（trace_id/subject_id/workspace_id），不搬运 live 对象。
    """
    ctx = TraceContext.current()
    if ctx is None:
        return {}
    return {
        "trace_id": str(getattr(ctx, "trace_id", "") or ""),
        "subject_id": str(getattr(ctx, "subject_id", "") or ""),
        "workspace_id": str(getattr(ctx, "workspace_id", "") or ""),
    }


def _register_audit_hooks() -> bool:
    """把本模块的「脱敏器 + TraceContext 提供者」注入审计包（依赖倒置）

    方向 `agent.observability.trace_v2 → agent.audit.facade` 单向：审计包不得反向
    导入本模块（否则构成 CI 架构规则禁止的循环依赖）。审计包不可用时静默跳过。
    """
    try:
        from agent.audit.facade import set_payload_sanitizer, set_trace_context_provider
        set_payload_sanitizer(redact)
        set_trace_context_provider(_audit_trace_context_leaf)
        return True
    except Exception:  # noqa: BLE001 审计包不可用 → 审计侧用内置兜底脱敏
        return False


_AUDIT_HOOKS_REGISTERED = _register_audit_hooks()


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

    【TASK-S8-02：本类是模块内最后一条未加固的写路径】
    加固后的写路径 = 「**有界队列**（``CP_TRACE_QUEUE_MAXSIZE``）→ 后台 writer →
    **持 ``<db>.lock`` 跨进程锁**（``agent.utils.cross_process_lock``）→ WAL 事务提交」。
    四条硬口径：

    1. **绝不静默丢观测**：队列满 / ring buffer 满 / 行损坏 / 取锁失败，全部
       **计数 + 限流告警**（计数每次都记，日志按窗口节流），drop 不可避免时必须可数。
    2. **降级可恢复**：``_degraded`` 不再是"终身标记"——成功提交会清除它并计入
       ``degraded_recoveries``；``consecutive_failures`` 用来区分"抖动一次"与"真死"。
    3. **``flush()`` 不说谎**：``True`` 只表示"已入队记录都被处理完"（提交**或**
       **显式**降级），**不**表示落盘；落盘口径看 ``stats()['durable']`` /
       ``flush_durable()``。理由见 ``flush()`` 文档。
    4. **跨进程锁买到了什么（不夸大）**：SQLite 自身就会串行化写者，所以本锁
       **不是**正确性的必要条件。它买的是 (a) 把"多进程争 ``busy_timeout``"变成
       显式排队、避免长事务/锁序病理把某进程拖到超时；(b) 让"没能拿到锁"成为
       **可计数的显式事件**（``lock_bypass_count``）而不是静默等待；(c) 让"交接"
       （后一批写者开始写）是确定性的。**取不到锁不丢数据**：仍照写（WAL +
       ``busy_timeout`` 兜底），只计数并留痕。
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
        self._is_memory = (self._db_path == ":memory:")

        # ── 配置（读 env；**默认保守**）──
        self._lock_enabled = _env_flag(ENV_LOCK_ENABLED)
        self._lock_timeout = max(
            0.0, _env_float(ENV_LOCK_TIMEOUT_SEC, DEFAULT_LOCK_TIMEOUT_SEC))
        self._queue_maxsize = max(
            1, _env_int(ENV_QUEUE_MAXSIZE, DEFAULT_QUEUE_MAXSIZE))
        self._ring_maxlen = max(
            1, _env_int(ENV_RING_BUFFER_MAXLEN, RING_BUFFER_MAXLEN))
        self._degraded_retry_sec = max(
            0.0, _env_float(ENV_DEGRADED_RETRY_SEC, DEFAULT_DEGRADED_RETRY_SEC))
        self._wal_autocheckpoint = max(
            0, _env_int(ENV_WAL_AUTOCHECKPOINT, DEFAULT_WAL_AUTOCHECKPOINT))
        self._corrupt_log_every = max(
            1, _env_int(ENV_CORRUPT_LOG_EVERY, DEFAULT_CORRUPT_LOG_EVERY))

        # ── 有界队列（缺陷 #1）──
        # 【为什么必须给 maxsize】原实现 ``queue.Queue()`` 无界 ⇒ ``put_nowait``
        # **永不**抛 ``queue.Full`` ⇒ ``record()`` 里的 ``except Exception`` 是死代码，
        # 而"真正的容量上限"落在了 ring buffer 上（见下一条）。有界 + 显式满溢处理
        # 才能把"容量到了"变成一个**可观测**事件。
        self._queue: "queue_module.Queue[UnifiedTrace]" = queue_module.Queue(
            maxsize=self._queue_maxsize)
        # ── ring buffer（缺陷 #2）──
        # 【为什么**不用** ``deque(maxlen=N)``】``maxlen`` 满了以后 ``append`` 会
        # **静默丢弃最旧**元素——观测存储里的无声数据丢失。这里改为无 maxlen 的
        # deque，由 ``_ring_append()`` 显式驱逐 + 计数 + 留痕：行为完全一致
        # （丢最旧、保最新），区别只在于"丢了多少"是**可知**的。
        self._fallback_ring_buffer: deque = deque()
        self._ring_lock = threading.Lock()
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

        # ── TASK-S8-02 可观测计数（进程内；每一个"没写成功"的口子都要有数字）──
        self._lock_write_count = 0        # 持跨进程锁完成的批次数
        self._lock_bypass_count = 0       # 取锁失败**仍照写**的次数（不丢数据但要可见）
        self._queue_overflow_count = 0    # 队列满 ⇒ 转 ring buffer
        self._ring_buffer_dropped = 0     # ring buffer 满 ⇒ 驱逐最旧（**显式**）
        self._durable_count = 0           # 真正提交进 SQLite 的记录数
        self._uncommitted_count = 0       # 因**写路径**降级而未落盘的记录数
        self._batch_failure_count = 0     # 批量写失败的批次数（累计）
        self._consecutive_failures = 0    # 连续失败批次数（成功即归零）
        self._degraded_enter_count = 0    # 进入降级态的次数
        self._degraded_recoveries = 0     # 由失败态**恢复**的次数
        self._degraded_retry_skips = 0    # 退避窗口内跳过的重试批次数
        self._skipped_row_count = 0       # 读侧不可解析行（缺陷 #8）
        self._read_failure_count = 0      # 读侧整体查询失败
        self._integrity_checked = 0       # 显式完整性抽查：已校验哈希数
        self._integrity_mismatch = 0      # 显式完整性抽查：不一致数
        self._last_error = ""
        self._degraded_reason = ""
        self._last_failure_mono = 0.0
        self._last_recovery_ts = 0.0
        self._journal_mode = ""
        # 限流用（按 key 记"已报次数"）；计数**不**走这里，见 _rate_limited_warning
        self._warn_counts: Dict[str, int] = {}
        self._warn_lock = threading.Lock()

        # ── 连接登记（缺陷 #5：原实现连接永不关闭，Windows 上一直占住库文件）──
        self._conns: Dict[int, sqlite3.Connection] = {}
        self._conn_lock = threading.Lock()

        # ── 跨进程写锁（TASK-S8-02 要求 1）──
        # 【为什么锁**独立**文件】锁 DB 本体时，一旦有人用 rename 替换该文件
        # （原子写惯用法），锁就落到被淘汰的 inode 上、互斥**静默失效**；故始终锁
        # ``<db>.lock``（见 cross_process_lock.lock_path_for 的既有结论）。
        # 【为什么内存库不建锁】``:memory:`` 没有跨进程语义，建锁只会凭空产生一个
        # 磁盘文件，反而让"测试不写 data/"的隔离更难保证。
        self._lock_path = "" if self._is_memory else lock_path_for(
            os.path.abspath(self._db_path))
        self._write_guard: Optional[CrossProcessLock] = None
        if self._lock_enabled and not self._is_memory:
            self._write_guard = CrossProcessLock(
                self._lock_path,
                name=f"trace:{os.path.basename(self._db_path)}",
                holder_info={"db_path": os.path.abspath(self._db_path)})

        if self._is_memory:
            self._init_db()
        else:
            try:
                self._init_db()
            except Exception as e:  # noqa: BLE001
                logger.warning("统一 Trace SQLite 初始化失败，降级到 ring buffer: %s", e)
                self._enter_degraded_state(
                    f"init_db: {type(e).__name__}: {e}")

        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="unified-trace-writer", daemon=True)
        self._writer_thread.start()

    # ── 持久化生命周期 ────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """线程本地连接（同库双表：本模块表与 tool_traces 共用一个 DB 文件）

        - ``busy_timeout`` 显式 5s：与 tool_trace writer 线程同库并发写时**等待**
          文件锁，而非立即抛 "database is locked"（超时仍失败则由降级路径兜底）。
        - ``journal_mode = WAL`` + ``synchronous = FULL``：见 ``_apply_durability_pragmas``。
        - 连接**登记在册**（``_conns``），``stop()`` 时统一关闭（缺陷 #5：原实现连接
          永不关闭，Windows 上会一直占住 ``db`` / ``-wal`` / ``-shm``，临时目录也删不掉）。
          ``_get_conn`` 用"``self._local.conn`` 是否仍是登记本线程的那条连接"作为
          存活判据：连接被关闭（从登记表移除）后下一句自动重开，不会把已关闭的连接
          交给调用方。
        """
        ident = threading.get_ident()
        with self._conn_lock:
            registered: Optional[sqlite3.Connection] = self._conns.get(ident)
        cached: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if cached is not None and cached is registered:
            return cached
        conn: sqlite3.Connection
        if self._is_memory:
            # 共享缓存内存库：跨线程（writer 线程 vs 主线程）共用同一张表
            conn = sqlite3.connect(
                "file:unified_trace_mem?mode=memory&cache=shared",
                uri=True, check_same_thread=False)
        else:
            parent = os.path.dirname(self._db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            conn = sqlite3.connect(
                self._db_path, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
        except Exception:  # noqa: BLE001  非致命：默认 timeout 已覆盖
            pass
        if not self._is_memory:
            self._apply_durability_pragmas(conn)
        self._local.conn = conn
        with self._conn_lock:
            self._conns[ident] = conn
        return conn

    def _apply_durability_pragmas(self, conn: sqlite3.Connection) -> None:
        """WAL + ``synchronous``（**best-effort**：平台差异不得阻断写入）

        【为什么要 WAL（缺陷 #4）】原实现从不设 ``journal_mode``，即 rollback journal：
        多个写者对整库文件互斥，只能靠 ``busy_timeout`` 硬等；WAL 下写者不阻塞读者、
        "写-写"仍由 SQLite 自己串行化。写法与 ``agent/audit/chain.py::_init_db`` 对齐
        （同样 best-effort + 吞异常）。
        【为什么不因失败上抛】``journal_mode=WAL`` 在网络盘/NFS、只读目录、只读库上
        会失败；退回 rollback journal 仍然**正确**（只是更慢），不该让 trace 写入
        整体不可用。
        【为什么 ``synchronous=FULL``（保守）】WAL 下 NORMAL 只在 checkpoint 时同步，
        掉电/OS 崩溃可能丢已提交尾部；本存储的契约是"不静默丢观测"，故取 FULL
        （代价 = 每批一次 fsync，批量提交把它摊销掉）。
        """
        try:
            row = conn.execute("PRAGMA journal_mode = WAL").fetchone()
            mode = str(row[0]).strip().lower() if row is not None else ""
            if mode:
                self._journal_mode = mode
        except Exception as e:  # noqa: BLE001 平台/文件系统差异，退回 rollback journal
            logger.debug("journal_mode=WAL 未生效（退回 rollback journal）: %s", e)
        try:
            conn.execute("PRAGMA synchronous = FULL")
        except Exception as e:  # noqa: BLE001
            logger.debug("synchronous=FULL 未生效: %s", e)
        try:
            conn.execute(f"PRAGMA wal_autocheckpoint = {int(self._wal_autocheckpoint)}")
        except Exception as e:  # noqa: BLE001
            logger.debug("wal_autocheckpoint 未生效: %s", e)

    #: 【实测：WAL 自动 checkpoint 会给**同进程其它线程**带来一次多毫秒停顿】
    #:
    #: 单进程 6000 条（payload ≈ 700B）本机实测 ``record()`` 延迟：
    #:
    #: ==========================  =========  =========  =========  ==========
    #: 配置                        p50        p99        max        mean
    #: ==========================  =========  =========  =========  ==========
    #: 加固后（默认 1000 页）      1.5µs      8.5µs      **19.4ms**  5.8µs
    #: 加固后（关自动 checkpoint） 1.5µs      6.2µs      **0.31ms**  2.1µs
    #: 加固前（HEAD，rollback）    1.3µs      3.6µs      0.03ms     1.5µs
    #: ==========================  =========  =========  =========  ==========
    #:
    #: 即：**尾部**那一两次多毫秒停顿来自 checkpoint（把 WAL 并回主库 + fsync），
    #: 而 pysqlite 在 sqlite3 API 调用期间**持 GIL**，停顿会传导到同进程其它线程。
    #: 默认仍取 SQLite 的 1000 页（磁盘占用可控、停顿频次低，约每 4MB WAL 一次）；
    #: 延迟敏感部署可调大 ``CP_TRACE_WAL_AUTOCHECKPOINT``（停顿更少但单次更长、
    #: WAL 更大），调 0 = 关闭自动 checkpoint（仅排障用，WAL 会无界增长）。

    def _init_db(self) -> None:
        conn = self._get_conn()
        with self._write_lock:
            # 【为什么 DDL 也纳入跨进程串行化】4 个进程同时构造 store 会并发执行
            # CREATE TABLE/INDEX：SQLite 会用 busy_timeout 串行化，但"谁先建表"
            # 不确定，失败一方会**整体降级**（本模块最贵的失败模式）。用同一把
            # ``<db>.lock`` 把建表段串起来，让"交接"确定化。取不到锁仍照做
            # （DDL 本身幂等且 SQLite 会兜底），只计数。
            guard = self._acquire_guard()
            try:
                if not self._is_memory:
                    self._apply_durability_pragmas(conn)
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
            finally:
                self._release_guard(guard)

    # ── 加固辅助（TASK-S8-02）─────────────────────────────────

    def _enter_degraded_state(self, reason: str) -> None:
        """进入降级态（幂等：已在降级态则不重复计"进入"次数）"""
        with self._count_lock:
            if not self._degraded:
                self._degraded_enter_count += 1
        self._degraded = True
        self._degraded_reason = reason
        self._last_error = reason
        self._last_failure_mono = time.monotonic()

    def _rate_limited_warning(self, key: str, message: str, *args: Any) -> bool:
        """限流告警：第 1 次必报，其后每 ``CP_TRACE_CORRUPT_LOG_EVERY`` 次报一次

        【为什么计数不限流、日志才限流】计数（``stats()``）是**事实**，每一次都要记；
        日志是**信号**，热循环里"每条一条"会把日志冲垮、反而掩盖真相（与
        ``events.py`` 坏行告警同一口径：计数不冷却，输出有窗口）。

        Returns:
            本次是否真的打了日志（调用方据此决定是否**同频**发事件留痕）。
        """
        with self._warn_lock:
            seen = self._warn_counts.get(key, 0) + 1
            self._warn_counts[key] = seen
        if seen == 1 or (seen % self._corrupt_log_every == 0):
            logger.warning("[第 %d 次] " + message, seen, *args)
            return True
        return False

    def _emit_store_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """把写路径降级/溢出事件发给事件层（best-effort，**绝不抛出**）

        【为什么必须有】计数只活在**本进程内存**里，进程一退就没了；事件层是落盘的
        结构化留痕，能回答"什么时候、哪个库、丢了多少"。
        【为什么调用点一律在临界区外】本方法会取事件文件的锁；放进 Trace 写锁/
        跨进程锁的临界区就会形成"Trace 锁 → 事件锁"的锁序，别的模块一旦反向持有
        即死锁——这正是本任务要消除的锁序病理。
        """
        try:
            from agent.observability.events import emit
            emit(event_type, payload, actor="system")
        except Exception as e:  # noqa: BLE001 事件层不可用不影响 trace 写入
            logger.debug("Trace 写路径事件留痕失败（best-effort）: %s", e)

    def _degrade_payload(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """降级/溢出事件的**叶子字段**载荷（只回传标量，绝不搬运 live 对象）"""
        with self._count_lock:
            payload: Dict[str, Any] = {
                "db_path": self._db_path,
                "degraded": bool(self._degraded),
                "degraded_reason": self._degraded_reason,
                "consecutive_failures": self._consecutive_failures,
                "batch_failure_count": self._batch_failure_count,
                "uncommitted_count": self._uncommitted_count,
                "ring_buffer_pending": len(self._fallback_ring_buffer),
                "ring_buffer_dropped": self._ring_buffer_dropped,
                "queue_overflow_count": self._queue_overflow_count,
                "durable_count": self._durable_count,
                "pid": os.getpid(),
            }
        payload.update(extra or {})
        return payload

    def _ring_append(self, trace: UnifiedTrace, *, reason: str = "",
                     durable_overflow: bool = True) -> None:
        """把一条记录放进 ring buffer（**满时显式驱逐最旧 + 计数 + 留痕**）

        【为什么不用 ``deque(maxlen=N)``（缺陷 #2）】``maxlen`` 满后 ``append`` 会
        **静默丢弃最旧**元素；观测存储里的静默丢弃等价于"观测凭空消失"。本方法
        显式判长 + ``popleft`` + 计数 + 限流告警 + 事件留痕：行为一致（丢最旧保最新），
        但"丢了多少"从此**可知**。

        Args:
            reason: 降级原因（诊断用）。
            durable_overflow: 是否计入 ``uncommitted_count``（**写路径**降级为 True；
                缺 workspace_id 属于**策略性**降级，另有 ``workspace_degraded`` 计数，
                不计入"未落盘"口径，避免两类语义互相污染）。
        """
        dropped = 0
        with self._ring_lock:
            while len(self._fallback_ring_buffer) >= self._ring_maxlen:
                self._fallback_ring_buffer.popleft()
                dropped += 1
            self._fallback_ring_buffer.append(trace)
        if durable_overflow:
            with self._count_lock:
                self._uncommitted_count += 1
        if dropped:
            with self._count_lock:
                self._ring_buffer_dropped += dropped
                total = self._ring_buffer_dropped
            logged = self._rate_limited_warning(
                "ring_dropped",
                "统一 Trace ring buffer 已满（maxlen=%d）⇒ 丢弃最旧 %d 条"
                "（reason=%s，累计丢弃 %d 条；已计数 + 留痕，**不是静默丢弃**）",
                self._ring_maxlen, dropped, reason or "?", total)
            if logged:
                self._emit_store_event(EVENT_TRACE_OVERFLOW, self._degrade_payload(
                    {"reason": reason, "dropped": dropped}))

    def _should_attempt_db(self) -> bool:
        """当前是否该（重新）尝试写 SQLite

        【为什么要有退避（缺陷 #3 的连带修复）】降级态下若每批都重试，每次都要付
        ``busy_timeout``（默认 5s）等待 + 一次异常构造；一个"库真的坏了"的进程会
        因此把 writer 线程钉死。``CP_TRACE_DEGRADED_RETRY_SEC``（默认 2s）让重试
        成为**有节拍**的而不是狂热的；设 0 = 每批都重试（用例/应急）。
        """
        if not self._degraded:
            return True
        if self._degraded_retry_sec <= 0:
            return True
        return (time.monotonic() - self._last_failure_mono) >= self._degraded_retry_sec

    def _acquire_guard(self) -> Optional[CrossProcessLock]:
        """尝试取跨进程写锁（**绝不抛**；拿不到返回 None 并计数）

        【不夸大】SQLite 自身即串行化写者，所以"没拿到锁"**不是**数据风险：返回
        None 后调用方**照常写**，由 WAL + ``busy_timeout`` 兜底。锁买的是"排队
        确定性 + 降级可见性"（见类文档第 4 条）。锁原语自身的冲突/超时留痕
        （审计链 + 事件流）由 ``cross_process_lock`` 负责。
        """
        guard = self._write_guard
        if guard is None:
            return None
        try:
            guard.acquire(self._lock_timeout)
        except LockError as exc:
            with self._count_lock:
                self._lock_bypass_count += 1
                bypass = self._lock_bypass_count
            self._rate_limited_warning(
                "lock_bypass",
                "未能取得 Trace 跨进程写锁（%s），**仍照写**（WAL + busy_timeout 兜底，"
                "不丢数据）；累计 %d 次: %s",
                self._lock_path, bypass, exc)
            return None
        with self._count_lock:
            self._lock_write_count += 1
        return guard

    def _release_guard(self, guard: Optional[CrossProcessLock]) -> None:
        """释放跨进程写锁（失败只告警：OS 在句柄关闭时也会释放）"""
        if guard is None:
            return
        try:
            guard.release()
        except Exception as e:  # noqa: BLE001 释放失败不该推翻已完成的提交
            logger.debug("释放 Trace 跨进程写锁异常: %s", e)

    def record(self, trace: UnifiedTrace, *, strict: Optional[bool] = None) -> bool:
        """入队一条统一 Trace（append-only）。

        Args:
            trace: 统一 Trace。
            strict: None→用构造时默认；True→缺 workspace_id 抛 MissingWorkspaceError；
                    False→缺 workspace_id 显式降级（ring buffer + 告警）。

        Returns:
            True=正常入队（将异步落 SQLite）；False=**显式降级**（已转 ring buffer 且
            已计数）：缺 workspace_id（P7.1-19 不变量）或队列已满。
        """
        want_strict = self._strict if strict is None else strict
        if not trace.tenancy.workspace_id:
            if want_strict:
                raise MissingWorkspaceError(trace.trace_id)
            # 显式降级：记入 ring buffer + 告警，不阻断主路径（P7.1-19）
            self._ring_append(trace, reason="missing_workspace",
                              durable_overflow=False)
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
        except queue_module.Full:
            # 【缺陷 #1 修复】队列**有界**后这条分支才真正可达：满 ⇒ 不丢，
            # 转 ring buffer，且两个口子分别计数（队列溢出 + ring 溢出）。
            with self._count_lock:
                self._queue_overflow_count += 1
                overflow = self._queue_overflow_count
            logged = self._rate_limited_warning(
                "queue_full",
                "统一 Trace 队列已满（maxsize=%d）⇒ 转入 ring buffer（**不丢数据**，"
                "累计 %d 次；请检查 writer 是否被慢库/长事务拖住）",
                self._queue_maxsize, overflow)
            self._ring_append(trace, reason="queue_full")
            if logged:
                self._emit_store_event(EVENT_TRACE_QUEUE_FULL, self._degrade_payload(
                    {"queue_maxsize": self._queue_maxsize}))
            return False
        except Exception as e:  # noqa: BLE001 其余入队异常同样**显式降级**，绝不静默丢
            with self._count_lock:
                self._queue_overflow_count += 1
            logger.warning("统一 Trace 入队异常，显式降级到 ring buffer: %s", e)
            self._ring_append(trace, reason="enqueue_error")
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
        """批量提交到 SQLite（**持跨进程锁**；失败则显式降级 + 计数）

        【锁的边界】只包住 ``executemany + commit``（真正动库的部分），**不**包住
        ring buffer 追加与限流告警/事件留痕——把跨模块调用放进临界区会人为拉长
        持锁时间，制造本任务正要消除的锁序病理。
        【append-only】本方法里只有 INSERT；没有任何改写/删除语句（硬不变量，
        ``test_trace_v2.py`` 按**源码文本**锁死本方法体，故 SQL 字面量必须留在
        这里、且注释里也不能出现那两个 SQL 动词）。
        """
        if not records:
            return
        total = len(records)
        committed = 0
        entered_degraded = False
        degrade_reason = "batch_write_failed"
        with self._write_lock:
            guard = self._acquire_guard()
            try:
                if not self._should_attempt_db():
                    # 降级态 + 退避窗口内：本轮不碰库（只记"跳过一次重试"）
                    degrade_reason = "degraded_backoff"
                    with self._count_lock:
                        self._degraded_retry_skips += 1
                else:
                    conn = None
                    try:
                        conn = self._get_conn()
                        placeholders = ",".join(["?"] * len(self._COLUMNS))
                        sql = (f"INSERT INTO unified_traces "
                               f"({','.join(self._COLUMNS)}) VALUES ({placeholders})")
                        conn.executemany(sql, [self._row_values(r) for r in records])
                        conn.commit()
                    except Exception as e:  # noqa: BLE001 写失败 ⇒ 显式降级
                        # 【为什么要显式 rollback】``executemany`` 成功但 ``commit`` 失败
                        # 时，连接上会残留一个**未提交事务**；不显式回滚就继续复用该连接，
                        # 后续读取会看到脏数据、下一批写入还会落进同一个事务（"以为提交了
                        # 其实没有"）。故失败路径先 best-effort 回滚，再把整批转 ring
                        # buffer——宁可重放在内存里，也不要**半提交**。
                        if conn is not None:
                            try:
                                conn.rollback()
                            except Exception:  # noqa: BLE001 回滚失败也照走降级
                                pass
                        entered_degraded = self._note_db_failure(e, total)
                    else:
                        self._note_db_success(total)
                        committed = total
            finally:
                self._release_guard(guard)
        try:
            if committed < total:
                for r in records[committed:]:
                    self._ring_append(r, reason=degrade_reason)
        finally:
            # 【口径】无论提交还是降级，被 writer 接手的记录都算"已处理"——
            # 这正是 flush() 的 True 所保证的东西（见 flush 文档）。
            self._mark_committed(total)
        if entered_degraded:
            # 只在**一次降级事件的开头**留痕（后续同一次降级只计数），避免风暴
            self._emit_store_event(EVENT_TRACE_DEGRADED, self._degrade_payload(
                {"records": total}))

    def _note_db_failure(self, exc: BaseException, n: int) -> bool:
        """记录一次批量写失败（计数 + 状态迁移 + 限流告警）；返回是否**新进入**降级

        【缺陷 #3 修复：降级必须可恢复】原实现一次失败即置 ``_degraded`` 且**永不复位**
        ——一次瞬时抖动（另一进程持锁、磁盘瞬时满、连接被关）就让本进程**余生**只写
        内存：观测丢失且没有任何信号。现在：

        - ``_degraded`` 仍置位（保持既有语义："最近一次尝试失败了"）；
        - 每次批量写都会**再试**（受 ``CP_TRACE_DEGRADED_RETRY_SEC`` 退避约束），
          成功即清位并计入 ``degraded_recoveries``；
        - ``consecutive_failures`` 用来区分"抖一次就恢复"与"连续 N 次都失败（真死）"。
        """
        with self._count_lock:
            was_degraded = bool(self._degraded)
            self._batch_failure_count += 1
            self._consecutive_failures += 1
            consecutive = self._consecutive_failures
            failures = self._batch_failure_count
        self._enter_degraded_state(f"{type(exc).__name__}: {exc}")
        self._rate_limited_warning(
            "db_failure",
            "统一 Trace SQLite 批量写入失败（%d 条转 ring buffer；连续失败 %d 次，"
            "累计 %d 次）: %s",
            n, consecutive, failures, exc)
        return not was_degraded

    def _note_db_success(self, n: int) -> None:
        """记录一次成功提交（**清除降级态** + 计恢复）"""
        with self._count_lock:
            self._durable_count += n
            was_degraded = bool(self._degraded)
            failures_before = self._consecutive_failures
            self._consecutive_failures = 0
            if was_degraded:
                self._degraded_recoveries += 1
                recoveries = self._degraded_recoveries
        self._degraded = False
        self._degraded_reason = ""
        if was_degraded:
            self._last_recovery_ts = time.time()
            logger.info(
                "统一 Trace 写路径**已从降级恢复**（此前连续失败 %d 次，"
                "累计恢复 %d 次）: %s", failures_before, recoveries, self._db_path)

    def _mark_committed(self, n: int) -> None:
        """记录 n 条记录**已被 writer 处理**（提交 或 显式降级，二者都算）

        注意口径：本计数是 ``flush()`` 的依据，**不**代表落盘——落盘见
        ``_note_db_success`` 的 ``_durable_count``。
        """
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
        """等待已入队记录被 writer **处理完**（**不是**"等落盘"，口径见下）

        【返回值口径（TASK-S8-02 要求 6：``flush()`` 不许说谎）】
        ``True`` ⇔ 调用瞬间 ``handled >= enqueued``，其中 handled ＝ 该记录要么
        **提交进 SQLite**、要么被**显式降级**（转 ring buffer 且已计数/告警）。
        也就是说 ``True`` 保证的是"**没有在途记录**"，**不**保证"都已落盘"。
        【为什么不干脆改成"全部落盘才 True"】降级态（库不可用）下那将意味着
        ``flush()`` **永远**返回 False，调用方（S3/S5/用例）无法把它与"真的还
        没写完"区分开——把"处理完了但降级了"伪装成"没处理完"，**同样是说谎**。
        故契约拆成两个都不说谎的入口：

        - ``flush()``            → "在途清零"（本方法；保持既有签名与语义）；
        - ``flush_durable()``    → "在途清零 **且** 没有写路径降级未落盘的记录"；
        - ``stats()['durable']`` → 随时可查的同一判据（ops/面板/用例）。

        注：原实现的 ring buffer 路径也调用 ``_mark_committed``，因此"``flush() is True``
        但一条都没落盘"确实可能发生——这一点现在由上面的第二个口径**显式暴露**，
        而不是靠把 ``flush()`` 改成永久 False 来掩盖。
        """
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

    def _durability_ok(self) -> bool:
        """当前是否"没有因写路径降级而未落盘的记录"（**不读库**，只看计数）"""
        with self._count_lock:
            uncommitted = self._uncommitted_count
        return uncommitted == 0 and not self._degraded

    def flush_durable(self, timeout: float = 2.0) -> bool:
        """等待并判定"**真正落盘**"（在途清零 **且** 无未落盘记录）

        与 ``flush()`` 的唯一区别就是这一条：降级态下 ``flush()`` 可为 True 而本方法
        为 False——这正是"诚实"的落点（``stats()['durable']`` 是同一判据的查询入口）。
        """
        if not self.flush(timeout=timeout):
            return False
        return self._durability_ok()

    def stop(self, timeout: float = 5.0) -> bool:
        """优雅停止 writer 线程 + flush 残留 + 关闭连接（幂等；**终态**）

        【终态语义】停止后 writer **不会**重启（``_stopped`` 只置位不复位）：停止后
        再 ``record()`` 只会把记录堆在队列里直到 ``stop()`` 的排空路径处理它们。
        用例必须显式调用本方法（或接受"进程退出时 daemon 线程被丢弃、队列残留丢失"）。
        【连接收尾（缺陷 #5）】关闭全部登记在册的 SQLite 连接（WAL 下关闭最后一个
        连接会顺带 checkpoint，把 ``-wal`` 并回主库）。``:memory:`` 库**不关**——
        共享缓存内存库在最后一个连接关闭时会被销毁，关掉等于把数据抹了。
        """
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
        if not self._is_memory:
            self._close_connections()
        return not self._writer_thread.is_alive()

    def _close_connections(self) -> int:
        """关闭本 store 打开过的全部 SQLite 连接（返回关闭条数；幂等）

        关闭后再次 ``_get_conn()``/读接口会**自动重开**（``_get_conn`` 以"是否仍是
        登记在册的那条连接"为存活判据），所以读接口在 ``stop()`` 之后仍然可用。
        """
        with self._conn_lock:
            conns = list(self._conns.values())
            self._conns.clear()
        closed = 0
        for conn in conns:
            try:
                conn.close()
                closed += 1
            except Exception as e:  # noqa: BLE001 收尾失败不影响 stop 的返回值
                logger.debug("关闭统一 Trace 连接失败: %s", e)
        # 本线程的 thread-local 必须清掉：否则会拿到刚被关闭的连接
        self._local.conn = None
        return closed

    def clear(self) -> None:
        """清空表 + ring buffer（**测试专用**显式动作，非运行时写路径）

        与既有实现的差异：不再用 ``_degraded`` 短路（降级可恢复后，"降级"不代表
        库里没有数据；测试专用清理应当真的清干净）。计数**不**清零——它们是
        进程生命周期内的累计事实，清零会让"丢了多少"这一断言失去意义。
        """
        self._queue.queue.clear()
        with self._ring_lock:
            self._fallback_ring_buffer.clear()
        try:
            conn = self._get_conn()
            with self._write_lock:
                conn.execute("DELETE FROM unified_traces")
                conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("清空 unified_traces 失败: %s", e)

    # ── 读取接口 ─────────────────────────────────────────────

    def _iter_persisted(self) -> List[UnifiedTrace]:
        """合并 DB 与 ring buffer（workspace 降级/写失败的记录始终可见）

        【为什么不再用 ``_degraded`` 短路 DB 读取（缺陷 #3 的连带修复）】原实现一旦
        降级就**再也不读库**：即使后续恢复、或别的进程已经写进去了，本进程的读侧也
        看不见，而且没有任何信号。现在无论降级与否都尝试读库，失败则计数 + 限流告警。
        【坏行不再静默（缺陷 #8）】解析失败的 payload 计入 ``skipped_row_count``
        并限流告警——否则"行坏了"在面板上只表现为"记录变少了"。
        """
        with self._ring_lock:
            traces: List[UnifiedTrace] = list(self._fallback_ring_buffer)
        try:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT payload FROM unified_traces ORDER BY started_at ASC").fetchall()
        except Exception as e:  # noqa: BLE001
            with self._count_lock:
                self._read_failure_count += 1
                failures = self._read_failure_count
            self._rate_limited_warning(
                "read_failure",
                "查询统一 Trace 失败（累计 %d 次；ring buffer 中的 %d 条仍然可见）: %s",
                failures, len(traces), e)
            return traces
        skipped = 0
        for row in rows:
            try:
                traces.append(UnifiedTrace.from_dict(json.loads(row["payload"])))
            except Exception:  # noqa: BLE001 坏行：跳过但**计数 + 告警**
                skipped += 1
        if skipped:
            with self._count_lock:
                self._skipped_row_count += skipped
                total = self._skipped_row_count
            self._rate_limited_warning(
                "corrupt_row",
                "统一 Trace 存在**不可解析行**（本次跳过 %d 条；累计 %d 条）——"
                "已计数，请核对磁盘/并发写/人工编辑",
                skipped, total)
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

    def verify_integrity(self, *, limit: Optional[int] = None,
                         sample: int = 0) -> Dict[str, Any]:
        """抽查持久化行的哈希一致性（**显式 opt-in**，默认不在读路径上跑）

        【为什么默认不跑（TASK-S8-02 要求 8 的取舍）】``_iter_persisted()`` 是
        ``count()/query()/chain()`` 的公共底座；每次读都重算 SHA256 会把读成本成倍
        放大，而"篡改/半写"是**低频**事件。本任务给了"显式入口"或"廉价抽样"两条路，
        这里选**显式入口**（可运维按需调用、可被用例锁死），不引入默认读放大。

        【口径与已知边界（不夸大检测力）】只校验 ``args_hash``/``output_hash``
        **非空**的记录（``TraceFacade.finish()`` 的任务级主 Trace 显式留空 hash，
        空 = 未哈希，不能当损坏）。哈希绑定的是**脱敏后**内容（``redact_then_hash``），
        读侧重算 ``hash_content(redacted)``；若原始 args 含非 JSON 可序列化对象，
        写侧的 ``default=str`` 兜底会改变落盘形态，这类记录会被判为"不一致"——
        因此本方法的产物是**可疑清单 + 计数**，不是"篡改定论"。

        Args:
            limit: 只校验最近 N 行（按 ``started_at``）；None = 全部。
            sample: >0 时随机抽 N 行（在 ``limit`` 之后生效）。

        Returns:
            {"checked", "unhashed", "mismatch", "skipped_rows", "suspicious": [...]}
        """
        try:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT trace_id, payload FROM unified_traces "
                "ORDER BY started_at ASC").fetchall()
        except Exception as e:  # noqa: BLE001 读失败如实上报，不抛
            return {"checked": 0, "unhashed": 0, "mismatch": 0, "skipped_rows": 0,
                    "suspicious": [], "error": f"{type(e).__name__}: {e}"}
        if limit is not None and limit >= 0:
            rows = rows[-limit:] if limit else []
        if sample and sample > 0 and len(rows) > sample:
            import random
            rows = random.sample(list(rows), int(sample))

        checked = unhashed = mismatch = skipped = 0
        suspicious: List[Dict[str, Any]] = []
        for row in rows:
            try:
                parsed = UnifiedTrace.from_dict(json.loads(row["payload"]))
            except Exception:  # noqa: BLE001 坏行单独计数（与读路径同口径）
                skipped += 1
                continue
            for label, content, expected in (
                    ("args", parsed.request.args_redacted, parsed.request.args_hash),
                    ("output", parsed.response.output_redacted,
                     parsed.response.output_hash)):
                if not expected:
                    unhashed += 1
                    continue
                checked += 1
                if hash_content(content) != expected:
                    mismatch += 1
                    suspicious.append({
                        "trace_id": str(row["trace_id"]),
                        "field": label,
                        "expected_hash": expected,
                    })
        with self._count_lock:
            self._integrity_checked += checked
            self._integrity_mismatch += mismatch
        if mismatch:
            self._rate_limited_warning(
                "integrity",
                "统一 Trace 完整性抽查发现**哈希不一致** %d 处"
                "（本次校验 %d 处；累计不一致 %d 处）",
                mismatch, checked, self._integrity_mismatch)
        return {"checked": checked, "unhashed": unhashed, "mismatch": mismatch,
                "skipped_rows": skipped, "suspicious": suspicious}

    def stats(self) -> Dict[str, Any]:
        """统计快照（``snapshot_stats()`` 的别名；TASK-S8-02 要求的统一查询入口）

        含 **``durable``** 这一诚实口径：True ⇔ 没有因写路径降级而未落盘的记录，
        且当前不在降级态。``flush()`` 的 True 与之**不等价**（见 ``flush()`` 文档）。
        """
        return self.snapshot_stats()

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
        with self._count_lock:
            write_stats = {
                "enqueue_count": self._enqueue_count,
                "handled_count": self._commit_count,
                "durable_count": self._durable_count,
                "uncommitted_count": self._uncommitted_count,
                "queue_maxsize": self._queue_maxsize,
                "queue_overflow_count": self._queue_overflow_count,
                "ring_buffer_maxlen": self._ring_maxlen,
                "ring_buffer_pending": len(self._fallback_ring_buffer),
                "ring_buffer_dropped": self._ring_buffer_dropped,
                "degraded": bool(self._degraded),
                "degraded_reason": self._degraded_reason,
                "degraded_enter_count": self._degraded_enter_count,
                "degraded_recoveries": self._degraded_recoveries,
                "degraded_retry_skips": self._degraded_retry_skips,
                "consecutive_failures": self._consecutive_failures,
                "batch_failure_count": self._batch_failure_count,
                "last_error": self._last_error,
                "last_recovery_ts": self._last_recovery_ts,
                "lock_enabled": bool(self._lock_enabled),
                "lock_path": self._lock_path,
                "lock_timeout_sec": self._lock_timeout,
                "lock_write_count": self._lock_write_count,
                "lock_bypass_count": self._lock_bypass_count,
                "skipped_row_count": self._skipped_row_count,
                "read_failure_count": self._read_failure_count,
                "integrity_checked": self._integrity_checked,
                "integrity_mismatch": self._integrity_mismatch,
                "journal_mode": self._journal_mode,
                "durable": (self._uncommitted_count == 0 and not self._degraded),
            }
        return {
            "total": len(traces),
            "success_count": success,
            "success_rate": round(success / len(traces), 4) if traces else 0.0,
            "by_capability": by_cap,
            "by_actor": by_actor,
            "workspace_degraded": self._workspace_degraded_count,
            "schema_version": SCHEMA_VERSION,
            "append_only": True,
            **write_stats,
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
        # S2-02：失败/阻断的能力级 Trace 入链（成功调用不逐条入链，避免审计噪声）
        _audit_trace_failure(trace)
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
        # S2-02：任务级 Trace 收尾入链（`trace.closed`；节点级失败另有 trace.tool.*）
        _audit_trace_closed(trace)
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

    def flush_durable(self, timeout: float = 2.0) -> bool:
        """等待并判定"**真正落盘**"（口径见 ``UnifiedTraceStore.flush_durable``）

        与 ``flush()`` 的差别只有一条：降级态下 ``flush()`` 可能为 True（在途清零，
        但记录只在内存 ring buffer 里），本方法此时返回 False——不给调用方看假象。
        """
        return self._store.flush_durable(timeout=timeout)


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
