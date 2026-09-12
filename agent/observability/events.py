"""events.v1 统一事件信封与统一事件出口（TASK-S2-03 / v7.2 §3.6 + §6.6）

云枢既有事件是**分散 JSONL**（`skills_assessment_events.jsonl` /
`approval_records.jsonl` / `task_history.jsonl` / `cost_log.jsonl` …），没有统一信封、
没有幂等键、也没有统一出口。本模块提供 v7.2 §3.6 的 **events.v1 信封**与**统一事件出口**，
作为后续治理机制（审批衰减率、断食/刹车、评测、六面板）的**统一数据源**。

设计要点（对齐任务书与 v7.2 §3.6）：

- **信封字段逐字对齐 §3.6**：``{v, event_id, ts, correlation_id, actor, type, payload}``
  （`EventEnvelope.to_dict()` 的键集合即该 7 个字段，无冗余键）。
- **event_id 幂等**：`event_id` 由「type + actor + correlation_id + 幂等键/载荷」确定性
  派生（`build_event_id`），写入端按 id 去重（重复发射不重复落盘），读取端同样按 id 去重
  （**重放/重复摄取不重复计数**）。
- **逐步收敛而非一次性替换**：本任务只落「新事件出口 + 核心事件接入」，存量事件文件
  **保持只读**（归档轨由 S2-02 `agent.audit.migration` 处理），不改存量写入方语义。
- **单写者纪律（§5.5，S2-02 同款）**：同一进程内每个事件文件路径只允许一个 writer
  （重复构造抛 `SingleWriterViolationError`），后台/只读方用 `EventStore.reader()`。
- **存档逻辑复用 `log_archiver.py`**：跨日首次写入时调用
  `agent.skills_mgmt.log_archiver.archive_daily_file()`，把历史行按日移入
  `events-YYYY-MM-DD.jsonl` 分片（老分片不冻结、不删除）。
- **载荷纪律（S2-02 裁定 D6 的教训）**：事件载荷**只放标识/标签/计数**，
  不放原始用户文本与密钥；`sanitize_payload()` 强制丢弃敏感键并截断超长字符串，
  且**不对摘要类 hex 做脱敏启发式**（S2-02 实测脱敏会把 24 位 hex 部分掩码，
  导致关联键无法对齐）。

事件类型（§3.6 八事件 + P7.1-18 第 9 事件 + §6.6 ACR/UTC 埋点清单）：见 `EventType`。
"""

from __future__ import annotations

import enum as _enum
import hashlib
import json
import logging
import os
import re as _re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from agent.utils.cross_process_lock import (
    CrossProcessLock,
    LockError,
    lock_path_for,
)

logger = logging.getLogger("agent.observability.events")

# ════════════════════════════════════════════════════════════
#  路径 / 开关 / 常量
# ════════════════════════════════════════════════════════════

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_DIR = os.path.join(_PROJECT_ROOT, "data", "events")

#: 信封 schema 名与版本（§3.6：events.v1；`v` = 该版本号）
SCHEMA_NAME = "events.v1"
SCHEMA_VERSION = 1
ENVELOPE_FIELDS = ("v", "event_id", "ts", "correlation_id", "actor", "type", "payload")

ACTIVE_FILENAME = "events.jsonl"

#: 幂等索引容量上限（进程内；超出后仅保留最近 N 个 id，读取端仍逐条去重）
MAX_IDEMPOTENCY_INDEX = 200_000
#: 单条事件字符串载荷截断上限（防长文本撑爆事件流）
MAX_STR_LEN = 400
#: 事件文件读取时单行上限（防异常大行拖垮解析）
MAX_LINE_BYTES = 1 << 20

#: 载荷中禁止出现的键（密钥/凭据类；命中即丢弃该叶子）
#: 精确命中表（含多段名，如 api_key）
_FORBIDDEN_KEYS_EXACT = frozenset({
    "token", "api_key", "apikey", "api_secret", "apisecret", "access_key",
    "accesskey", "secret_key", "secretkey", "private_key", "privatekey",
    "password", "passwd", "pwd", "secret", "authorization", "cookie",
    "credential", "credentials", "auth_token", "access_token", "refresh_token",
})
#: 分段命中表（键名按非字母数字切分后，任一段命中即视为敏感）
_FORBIDDEN_PARTS = frozenset({
    "password", "passwd", "pwd", "secret", "credential", "credentials",
    "authorization", "cookie",
})
#: 密钥形态的**末段**（access_token / refresh_token / api_key / signing_key）
_FORBIDDEN_TAIL_PARTS = frozenset({
    "token", "apikey", "secretkey", "privatekey", "accesskey",
})
#: 度量字段白名单（**永不**被敏感键启发式误伤；保护 §6.6 cost 埋点 schema）
_METRIC_SAFE_KEYS = frozenset({
    "tokens_in", "tokens_out", "tokens_total", "token_count", "request_tokens",
    "response_tokens", "billable_tokens_in", "billable_tokens_out",
    "billable_tokens_total", "cache_hit", "cache_hits", "retries",
    "cost_raw_cents", "cost_normalized_cents", "shadow_overhead_ms",
    "shadow_overhead_cents", "coefficient_in", "coefficient_out",
    "duration_ms", "latency_ms", "weight", "total_weight", "count", "total",
    "cost_cents", "cost_usd", "input_tokens", "output_tokens",
})

_KEY_SPLIT_RE = _re.compile(r"[^a-z0-9]+")

#: actor 口径（与 S2-01 §3.4 actor 对齐 + 治理侧扩展）
ACTOR_HUMAN = "human"
ACTOR_AUTO = "auto"
ACTOR_SUB_AGENT = "sub_agent"
ACTOR_SYSTEM = "system"
ACTOR_UI = "ui"

ENV_ENABLED = "CP_EVENTS_ENABLED"
ENV_DIR = "CP_EVENTS_DIR"
ENV_AUDIT_MIRROR = "CP_EVENTS_AUDIT_MIRROR"
ENV_ARCHIVE = "CP_EVENTS_ARCHIVE"

# ── 跨进程写锁与降级（TASK-S8-02；**默认保守**：开锁、有限等待、不丢数据）──
# 【为什么直读 env 而不进 observability_config】该模块由别的任务拥有；
# 本模块沿用既有 CP_EVENTS_* 直读风格，避免新的模块耦合边。
#: 写锁总开关（"0" ⇒ 退回无锁追加，**仅供测试/应急**：Windows 上多进程会撕行）
ENV_LOCK_ENABLED = "CP_EVENTS_LOCK_ENABLED"
#: 取锁有限等待上限（秒）。**有限**是硬要求：事件写入在主路径上，绝不能无限阻塞。
ENV_LOCK_TIMEOUT_SEC = "CP_EVENTS_LOCK_TIMEOUT_SEC"
#: 降级缓冲上限（行）。超出 ⇒ 显式失败（计数 + 告警），绝不静默丢。
ENV_PENDING_MAX = "CP_EVENTS_PENDING_MAX"
#: 坏行告警的限流间隔（每 N 条告警一次；计数则**每次**都记）
ENV_CORRUPT_LOG_EVERY = "CP_EVENTS_CORRUPT_LOG_EVERY"

DEFAULT_LOCK_TIMEOUT_SEC = 2.0
DEFAULT_PENDING_MAX = 10_000
DEFAULT_CORRUPT_LOG_EVERY = 100


class EventError(Exception):
    """事件层基类异常"""


class EventEnvelopeError(EventError):
    """信封字段非法（缺 type / 非法 type 形态 / 非 dict payload）"""


class EventTypeError(EventEnvelopeError):
    """未知事件类型（仅 strict 模式下抛出；默认仅计数告警）"""


class SingleWriterViolationError(EventError):
    """同一事件文件路径出现第二个 writer（§5.5 单写者纪律）"""


class ReadOnlyEventStoreError(EventError):
    """只读 store 上调用 append"""


# ════════════════════════════════════════════════════════════
#  事件类型枚举（§3.6 八事件 + P7.1-18 第 9 事件 + §6.6 埋点）
# ════════════════════════════════════════════════════════════

# ── §3.6 八事件 ──
EV_TOOL_CALLED = "tool.called"
EV_DIGEST_STAGE = "digest.stage"
EV_SKILL_GENERATED = "skill.generated"
EV_APPROVAL_REQUIRED = "approval.required"
EV_HEALING_TRIGGERED = "healing.triggered"
EV_METRICS_DELTA = "metrics.delta"
EV_POLICY_DENIED = "policy.denied"
EV_BACKUP_HEALTH = "backup.health"
# ── P7.1-18 第 9 事件 ──
EV_MODEL_DEGRADED = "model.degraded"
# ── §6.6 ACR / UTC 埋点清单 ──
EV_TASK_CLOSED = "task.closed"
EV_TASK_ABANDONED = "task.abandoned"
EV_APPROVAL = "approval"
EV_ESCAPE = "escape"
EV_INTERVENTION = "intervention"
EV_COST = "cost"
# ── S8-01 数据生命周期治理埋点 ──
EV_RETENTION_RUN = "retention.run"

#: §3.6 八事件（第 9 个 model.degraded 为 P7.1-18 补丁）
CORE_EVENT_TYPES = (
    EV_TOOL_CALLED, EV_DIGEST_STAGE, EV_SKILL_GENERATED, EV_APPROVAL_REQUIRED,
    EV_HEALING_TRIGGERED, EV_METRICS_DELTA, EV_POLICY_DENIED, EV_BACKUP_HEALTH,
)
#: 全量九事件（§3.6 八 + P7.1-18 第 9）
NINE_EVENT_TYPES = CORE_EVENT_TYPES + (EV_MODEL_DEGRADED,)
#: §6.6 ACR/UTC 埋点事件
METRIC_EVENT_TYPES = (EV_TASK_CLOSED, EV_TASK_ABANDONED, EV_APPROVAL,
                      EV_ESCAPE, EV_INTERVENTION, EV_COST)
#: S8-01 数据生命周期治理埋点（保留策略每次执行一条；属**新增分组**，
#: 不改动上面三组的语义 —— 上面三组是 §3.6/P7.1-18/§6.6 的冻结清单）
GOVERNANCE_EVENT_TYPES = (EV_RETENTION_RUN,)
ALL_EVENT_TYPES = NINE_EVENT_TYPES + METRIC_EVENT_TYPES + GOVERNANCE_EVENT_TYPES

#: 需要同步镜像入链式审计（S2-02）的治理类事件（§13.3 联动表：事件名 ↔ 审计写入）
#:
#: **不含 `approval.required`**：审批状态机 `ApprovalFlow` 已把
#: `approval.submit/approved/rejected/merged/archived` 直接写入链式台账（S2-02），
#: 再镜像一次会产生语义重复的链上噪声（S2-02 单测 `test_approval_lifecycle_recorded`
#: 以链上动作序列断言「一动作一记录」，实测该镜像会破坏该不变量）。
AUDIT_MIRROR_TYPES = frozenset({
    EV_POLICY_DENIED, EV_HEALING_TRIGGERED, EV_MODEL_DEGRADED, EV_ESCAPE,
})


def normalize_type(value: Any) -> str:
    """事件类型归一：`EventType` 成员 / 字符串 / None → 点分小写字符串

    （`str, Enum` 混入下 `str(member)` 会得到 `"EventType.X"`，故必须先取 `.value`。）
    """
    if isinstance(value, _enum.Enum):
        return str(value.value)
    return str(value or "")


class EventType(str, _enum.Enum):
    """事件类型枚举（§3.6 八事件 + P7.1-18 第 9 + §6.6 ACR/UTC 埋点）

    以 `str` 混入实现，可直接与存量字符串事件名比较/落盘（`EventType.COST == "cost"`）。
    分组清单见模块常量 `CORE_EVENT_TYPES` / `NINE_EVENT_TYPES` / `METRIC_EVENT_TYPES`
    / `GOVERNANCE_EVENT_TYPES` / `ALL_EVENT_TYPES`（不放进枚举体，避免被 Enum 当作成员）。
    """

    # §3.6 八事件
    TOOL_CALLED = EV_TOOL_CALLED
    DIGEST_STAGE = EV_DIGEST_STAGE
    SKILL_GENERATED = EV_SKILL_GENERATED
    APPROVAL_REQUIRED = EV_APPROVAL_REQUIRED
    HEALING_TRIGGERED = EV_HEALING_TRIGGERED
    METRICS_DELTA = EV_METRICS_DELTA
    POLICY_DENIED = EV_POLICY_DENIED
    BACKUP_HEALTH = EV_BACKUP_HEALTH
    # P7.1-18 第 9 事件
    MODEL_DEGRADED = EV_MODEL_DEGRADED
    # §6.6 ACR / UTC 埋点
    TASK_CLOSED = EV_TASK_CLOSED
    TASK_ABANDONED = EV_TASK_ABANDONED
    APPROVAL = EV_APPROVAL
    ESCAPE = EV_ESCAPE
    INTERVENTION = EV_INTERVENTION
    COST = EV_COST
    # S8-01 数据生命周期治理埋点
    RETENTION_RUN = EV_RETENTION_RUN

    @classmethod
    def isin(cls, value: Any) -> bool:
        """是否为本模块登记的事件类型"""
        return normalize_type(value) in ALL_EVENT_TYPES

    @classmethod
    def values(cls) -> tuple:
        return ALL_EVENT_TYPES


# ════════════════════════════════════════════════════════════
#  时间
# ════════════════════════════════════════════════════════════


def now_ts() -> str:
    """当前时刻的 ISO-8601 字符串（**带本地时区偏移 + 毫秒**）

    形式：``2026-09-10T20:15:33.123+08:00``。

    取「带偏移」而非 UTC `Z`：① 时间点无歧义（含偏移）；② `ts[:10]` 即
    **本地日历日**，与云枢存量事件文件（`approval_records.jsonl` /
    `task_history.jsonl` 的本地 naive ISO）及 ACR/UTC 的「按日/按周」口径一致。
    """
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def event_day(ts: Any) -> str:
    """取事件所属日历日（`YYYY-MM-DD`）；无法解析返回 "" """
    text = str(ts or "")
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return ""


def shift_day(day: str, days: int) -> str:
    """`YYYY-MM-DD` 加减天数（非法输入返回原值）"""
    try:
        base = datetime.strptime(str(day)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return str(day or "")
    return (base + timedelta(days=int(days))).isoformat()


# ════════════════════════════════════════════════════════════
#  信封
# ════════════════════════════════════════════════════════════


def canonical_payload(payload: Any) -> str:
    """载荷 → 稳定 JSON（sort_keys，保证同构载荷同哈希）"""
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 不可序列化 → 退化 repr（仅用于哈希/幂等键）
        return str(payload)


def build_event_id(event_type: str, actor: str, correlation_id: str,
                   payload: Any = None, idempotency_key: str = "") -> str:
    """确定性派生 `event_id`（幂等键）

    - 提供 `idempotency_key` 时：**只**由 `type|actor|correlation_id|key` 派生
      —— 同一逻辑事件重放（载荷微差）仍得到同一 id → 不重复计数；
    - 未提供时：由 `type|actor|correlation_id|canonical(payload)` 派生
      —— 内容相同即视为同一次事件（重放安全）；确有「同内容但确属两次」的场景，
      调用方必须显式给 `idempotency_key`（如 LLM 交互 id / 审批 record_id）。
    """
    material = "|".join([
        str(event_type or ""), str(actor or ""), str(correlation_id or ""),
        str(idempotency_key) if idempotency_key else canonical_payload(payload),
    ])
    return "ev_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _is_sensitive_key(key: str) -> bool:
    """键名是否属于密钥/凭据类（**精确/分段匹配，不做子串匹配**）

    子串匹配会误伤度量字段（`tokens_in` 含 `token`）——那正是 §6.6 cost 埋点的核心
    字段，被吞掉会让 UTC 归零（本任务实现期实测到的真实缺陷，已加回归测试）。
    """
    low = str(key or "").strip().lower()
    if not low:
        return False
    if low in _METRIC_SAFE_KEYS:
        return False
    if low in _FORBIDDEN_KEYS_EXACT:
        return True
    parts = [p for p in _KEY_SPLIT_RE.split(low) if p]
    if not parts:
        return False
    if any(part in _FORBIDDEN_PARTS for part in parts):
        return True
    if parts[-1] in _FORBIDDEN_TAIL_PARTS:
        return True
    # api_key_value / private_key_path 一类：含 key 且含限定词
    if "key" in parts and any(part in ("api", "secret", "private", "access", "signing")
                              for part in parts):
        return True
    return False


def sanitize_payload(payload: Any, *, _depth: int = 0) -> Any:
    """载荷净化：丢弃敏感键、截断超长字符串、保证 JSON 可序列化

    **不做脱敏启发式掩码**：S2-02 裁定 D6 实测「脱敏会把 24 位随机 hex 部分掩码」，
    而事件载荷中的 `event_id`/摘要/关联键正是内部生成的 hex，掩码后将破坏关联能力。
    因此本函数只做「键名黑名单 + 长度上限」，不做值形态掩码；**载荷纪律由调用方守**：
    事件载荷只放标识/标签/计数，不放原始用户文本与密钥。
    """
    if _depth > 8:
        return "[depth-limit]"
    if payload is None or isinstance(payload, (bool, int, float)):
        return payload
    if isinstance(payload, str):
        return payload if len(payload) <= MAX_STR_LEN else payload[:MAX_STR_LEN] + "…"
    if isinstance(payload, dict):
        out: Dict[str, Any] = {}
        for key, value in payload.items():
            name = str(key)
            if _is_sensitive_key(name):
                out[name] = "[已丢弃:敏感键]"
                continue
            out[name] = sanitize_payload(value, _depth=_depth + 1)
        return out
    if isinstance(payload, (list, tuple, set)):
        return [sanitize_payload(v, _depth=_depth + 1) for v in payload]
    # 其它类型（Path / datetime / 自定义对象）→ 字符串化后截断
    text = str(payload)
    return text if len(text) <= MAX_STR_LEN else text[:MAX_STR_LEN] + "…"


@dataclass
class EventEnvelope:
    """events.v1 信封（字段逐字对齐 §3.6，顺序即 `ENVELOPE_FIELDS`）

    Attributes:
        v: 信封版本（`SCHEMA_VERSION` = 1 ⇔ schema 名 `events.v1`）。
        event_id: 幂等键（缺省由 `build_event_id` 确定性派生）。
        ts: 事件时间（ISO-8601，带本地时区偏移 + 毫秒）。
        correlation_id: 关联 id（任务 trace_id / task_id；无上下文时 `"unlinked"`）。
        actor: 触发方（human / auto / sub_agent / system / ui）。
        type: 事件类型（见 `EventType`）。
        payload: 事件载荷（**只放标识/标签/计数**，经 `sanitize_payload`）。
    """

    v: int = SCHEMA_VERSION
    event_id: str = ""
    ts: str = ""
    correlation_id: str = ""
    actor: str = ""
    type: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.v = int(self.v or SCHEMA_VERSION)
        self.type = normalize_type(self.type).strip()
        if not self.type:
            raise EventEnvelopeError("EventEnvelope.type 不能为空")
        if not _valid_type_shape(self.type):
            raise EventEnvelopeError(
                f"EventEnvelope.type 形态非法（应为点分小写，如 tool.called）: {self.type!r}")
        if not isinstance(self.payload, dict):
            raise EventEnvelopeError("EventEnvelope.payload 必须为 dict")
        self.payload = sanitize_payload(self.payload)
        self.ts = str(self.ts or now_ts())
        self.actor = str(self.actor or ACTOR_SYSTEM)
        self.correlation_id = str(self.correlation_id or "")
        # 无上下文时显式标注 "unlinked"（不臆造关联键，但保留可查询性）
        if not self.correlation_id:
            self.correlation_id = "unlinked"
        if not self.event_id:
            self.event_id = build_event_id(
                self.type, self.actor, self.correlation_id, self.payload)

    # ── 序列化 ──

    def to_dict(self) -> Dict[str, Any]:
        """§3.6 七字段（键集合 == `ENVELOPE_FIELDS`，无冗余键）"""
        return {
            "v": self.v,
            "event_id": self.event_id,
            "ts": self.ts,
            "correlation_id": self.correlation_id,
            "actor": self.actor,
            "type": self.type,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    @property
    def day(self) -> str:
        return event_day(self.ts)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventEnvelope":
        """从 dict 还原（容忍缺字段；非法行由调用方跳过）"""
        if not isinstance(data, dict):
            raise EventEnvelopeError("EventEnvelope.from_dict 需要 dict")
        return cls(
            v=data.get("v", SCHEMA_VERSION),
            event_id=str(data.get("event_id") or ""),
            ts=str(data.get("ts") or ""),
            correlation_id=str(data.get("correlation_id") or ""),
            actor=str(data.get("actor") or ""),
            type=str(data.get("type") or ""),
            payload=data.get("payload") if isinstance(data.get("payload"), dict) else {},
        )


def _valid_type_shape(event_type: str) -> bool:
    """事件名形态校验：小写点分（`tool.called`）或单段小写（`approval`/`escape`/`cost`）

    §3.6 的八事件是点分两段（`tool.called`），§6.6 埋点清单的事件名是单段
    （`approval` / `escape` / `cost`），两者都必须合法。
    """
    text = str(event_type or "")
    if not text:
        return False
    parts = text.split(".")
    if not parts:
        return False
    for part in parts:
        if not part:
            return False
        if not all(ch.islower() or ch.isdigit() or ch == "_" for ch in part):
            return False
    return True


# ════════════════════════════════════════════════════════════
#  单写者纪律（§5.5，与 S2-02 audit chain 同款约束）
# ════════════════════════════════════════════════════════════

_WRITER_REGISTRY: Dict[str, str] = {}
_WRITER_LOCK = threading.Lock()


def _norm_path(path: Any) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def register_writer(path: Any, owner: str) -> None:
    """登记事件文件属主；同路径第二个 writer 抛 `SingleWriterViolationError`"""
    key = _norm_path(path)
    with _WRITER_LOCK:
        holder = _WRITER_REGISTRY.get(key)
        if holder is not None and holder != owner:
            raise SingleWriterViolationError(
                f"事件文件已有 writer（§5.5 单写者纪律）: {path} → {holder}")
        _WRITER_REGISTRY[key] = owner


def release_writer(path: Any, owner: str) -> None:
    key = _norm_path(path)
    with _WRITER_LOCK:
        if _WRITER_REGISTRY.get(key) == owner:
            _WRITER_REGISTRY.pop(key, None)


def active_writers() -> Dict[str, str]:
    """当前登记的 writer（路径 → 属主 token）"""
    with _WRITER_LOCK:
        return dict(_WRITER_REGISTRY)


def reset_event_stores() -> None:
    """关闭并清除进程单例 + writer 登记（**测试专用**）"""
    global _STORE, _STORE_READER
    store, reader = _STORE, _STORE_READER
    _STORE = None
    _STORE_READER = None
    for item in (store, reader):
        if item is not None:
            try:
                item.close()
            except Exception as e:  # noqa: BLE001 收尾失败不影响测试断言
                logger.debug("事件 store 关闭失败: %s", e)


# ════════════════════════════════════════════════════════════
#  出口（EventStore）
# ════════════════════════════════════════════════════════════


def default_events_dir() -> str:
    return os.getenv(ENV_DIR) or _DEFAULT_DIR


def active_events_path(directory: Optional[str] = None) -> str:
    return os.path.join(directory or default_events_dir(), ACTIVE_FILENAME)


def _env_flag(name: str, default: str = "1") -> bool:
    return str(os.getenv(name, default)).strip().lower() not in ("0", "false", "no", "off", "")


def _env_float(name: str, default: float) -> float:
    """环境变量浮点（非法/缺省 → 默认；**不抛**：坏配置不该打断事件写入）"""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("%s 非法（%r），回落默认 %s", name, raw, default)
        return float(default)


def _env_int(name: str, default: int) -> int:
    """环境变量整数（非法/缺省 → 默认）"""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return int(default)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("%s 非法（%r），回落默认 %s", name, raw, default)
        return int(default)


# ════════════════════════════════════════════════════════════
#  读侧腐坏可见化（TASK-S8-02 要求 4：坏行不得静默跳过）
# ════════════════════════════════════════════════════════════
#
# 【为什么必须有】读侧一直"跳过解析不了的行"——这是**静默**的：
# 多进程撕行造成的坏行、磁盘半写、人工误编辑，在面板上看起来都只是"事件变少了"。
# 计数器（进 `EventStore.stats()`）+ 限流告警让腐坏**可见**；不做限流会在大面积
# 腐坏时把日志冲垮，反而掩盖真相（与锁冲突留痕的冷却口径一致：计数不冷却，
# 输出有窗口）。

_READ_STATS_LOCK = threading.Lock()
_READ_STATS: Dict[str, int] = {
    "skipped_line_count": 0,            # 解析失败 / 非 dict 的行
    "skipped_oversized_line_count": 0,  # 超长（> MAX_LINE_BYTES）被跳过的行
    "unreadable_file_count": 0,         # 整文件不可读（OSError）
    "corruption_warnings": 0,           # 实际发出的告警条数（限流后）
}


def read_stats() -> Dict[str, int]:
    """读侧计数快照（**进程内**；`EventStore.stats()` 会并入其中三项）"""
    with _READ_STATS_LOCK:
        return dict(_READ_STATS)


def reset_read_stats() -> None:
    """清零读侧计数（**测试专用**：断言增量而非绝对值）"""
    with _READ_STATS_LOCK:
        for key in _READ_STATS:
            _READ_STATS[key] = 0


def _bump_unreadable_file() -> None:
    """记一次"整文件不可读"（OSError：权限/被占用/半写目录）"""
    with _READ_STATS_LOCK:
        _READ_STATS["unreadable_file_count"] += 1


def _note_skipped_line(path: str, line_no: int, *, oversized: bool = False,
                       reason: str = "") -> None:
    """记一次"被跳过的坏行"：计数 + **限流**告警（腐坏必须可见）

    限流口径：第 1 条必报，其后每 `CP_EVENTS_CORRUPT_LOG_EVERY`（默认 100）条报一次。
    计数**不**限流（每条坏行都进 `read_stats()`），因此"报警少了"仍可从计数看出来。
    """
    key = "skipped_oversized_line_count" if oversized else "skipped_line_count"
    with _READ_STATS_LOCK:
        _READ_STATS[key] += 1
        total = _READ_STATS["skipped_line_count"] + _READ_STATS["skipped_oversized_line_count"]
        every = max(1, _env_int(ENV_CORRUPT_LOG_EVERY, DEFAULT_CORRUPT_LOG_EVERY))
        should_log = total == 1 or (total % every == 0)
        if should_log:
            _READ_STATS["corruption_warnings"] += 1
    if should_log:
        logger.warning(
            "事件文件存在不可解析行（已跳过并计数）%s:%d（累计 %d 条，%s）",
            path, int(line_no), total, reason or ("超长行" if oversized else "非法 JSON"))


def _append_line_bytes(path: str, data: bytes, *, ensure_dir: bool = True) -> None:
    """把整行**一次** `os.write` 追加到 JSONL（O_APPEND）

    【为什么不用 `open(path, "a")` + `write`】文本层带缓冲，一行可能被拆成多次
    系统调用；POSIX 上 `O_APPEND` 的**短写原子性**只在"单次 write"下成立。
    显式 fd + `os.write` 让"取不到锁时的降级路径"也不会在 POSIX 上撕行
    （Windows 无此保证，故仍以跨进程锁为主防线）。

    与 `agent.skills_mgmt.log_archiver` 的同名底层写法**刻意保持一致**
    （两处的锁文件派生规则由用例锁死同值，见
    `tests/unit/test_events_write_hardening.py::test_lock_path_rule_matches_archiver`）。

    建目录用 `pathlib.Path.mkdir`（与 `log_archiver` 同风格）而非 `os.makedirs`：
    后者是**进程级全局函数**，会被既有单测 `patch('…os.makedirs')` 命中并污染其
    调用计数断言（实现期实测 `test_save_tasks_creates_directory` 由此失败）。

    Args:
        ensure_dir: 是否先确保父目录存在。热路径上的调用方**缓存"目录已就绪"**，
            只在首次（或目录被外部清掉后）传 True —— 实测依据见
            `EventStore._write_line`。目录被外部删除时由下面的 FileNotFoundError
            分支自愈（缓存过期不是数据风险）。
    """
    parent = os.path.dirname(path)
    if parent and ensure_dir:
        Path(parent).mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    except FileNotFoundError:
        if not parent:
            raise
        # "目录已就绪"缓存过期（目录被外部清理 / 多进程首次竞态）⇒ 重建后重试一次
        Path(parent).mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:  # pragma: no cover - 理论不可达
                raise OSError(f"os.write 返回 {written}（未写入任何字节）")
            view = view[written:]
    finally:
        os.close(fd)


def event_files(directory: Optional[str] = None) -> List[str]:
    """事件文件清单（活动文件 + 按日归档分片，按名升序；仅返回存在的文件）"""
    base = Path(directory or default_events_dir())
    if not base.exists():
        return []
    found: List[str] = []
    active = base / ACTIVE_FILENAME
    if active.exists():
        found.append(str(active))
    # 归档分片：events-YYYY-MM-DD.jsonl
    for path in sorted(base.glob("events-*.jsonl")):
        found.append(str(path))
    return found


class EventStore:
    """统一事件出口（追加写 JSONL + 幂等去重 + 跨日归档 + 审计镜像）

    Args:
        path: 活动事件文件路径（None → `CP_EVENTS_DIR`/默认 `data/events/events.jsonl`）。
        enabled: 总开关（None → `CP_EVENTS_ENABLED`，默认开）。
        audit_mirror: 治理类事件是否镜像入链式审计（None → `CP_EVENTS_AUDIT_MIRROR`）。
        archive: 跨日是否调用 `log_archiver` 归档（None → `CP_EVENTS_ARCHIVE`）。
        strict: True 时 `append` 异常向上抛（默认 False = best-effort 不阻断主路径）。
        reader: True 时构造只读 store（不占 writer 登记、append 抛错）。

    【TASK-S8-02 加固：多进程安全的追加写】
        每次 `append` 的落盘现在是「**持 `<path>.lock` 跨进程锁 → 单次 `os.write`
        追加**」（锁原语见 `agent.utils.cross_process_lock`；锁文件与被写文件
        分离，避免原子写 `os.replace` 把锁落到旧 inode 上）。取锁**有限等待**
        `CP_EVENTS_LOCK_TIMEOUT_SEC`（默认 2.0s）；等不到则走**排队降级**
        （见 `_write_line` 的降级设计注释），绝不静默丢事件。

    单写者纪律（§5.5）仍是**进程内**约束；跨进程互斥由上述文件锁提供。
    """

    def __init__(self, path: Optional[str] = None, *, enabled: Optional[bool] = None,
                 audit_mirror: Optional[bool] = None, archive: Optional[bool] = None,
                 strict: bool = False, reader: bool = False):
        self._path = str(path or active_events_path())
        self._enabled = _env_flag(ENV_ENABLED) if enabled is None else bool(enabled)
        self._audit_mirror = (_env_flag(ENV_AUDIT_MIRROR) if audit_mirror is None
                              else bool(audit_mirror))
        self._archive = _env_flag(ENV_ARCHIVE) if archive is None else bool(archive)
        self._strict = bool(strict)
        self._reader = bool(reader)
        self._lock = threading.RLock()
        self._ids: Set[str] = set()
        self._id_order: List[str] = []
        self._kinds: Dict[str, str] = {}          # event_id → type
        self._pending_interventions: Dict[str, List[str]] = {}
        self._closed = False
        self._write_count = 0
        self._durable_write_count = 0
        self._lock_write_count = 0
        self._lock_bypass_count = 0
        self._dropped_count = 0
        self._duplicate_count = 0
        self._failure_count = 0
        self._unknown_type_count = 0
        self._last_error = ""
        self._archived_total = 0
        self._last_archive_day = ""
        # ── 跨进程写锁 / 降级缓冲（TASK-S8-02）──
        self._lock_enabled = _env_flag(ENV_LOCK_ENABLED)
        self._lock_timeout = max(0.0, _env_float(ENV_LOCK_TIMEOUT_SEC,
                                                 DEFAULT_LOCK_TIMEOUT_SEC))
        self._pending_max = max(1, _env_int(ENV_PENDING_MAX, DEFAULT_PENDING_MAX))
        #: 降级缓冲：**尚未落盘**的整行字节（按写入先后顺序，排空时原序补齐）
        self._pending_lines: List[bytes] = []
        #: 父目录是否已确保存在（热路径优化：`Path.mkdir(parents=True, exist_ok=True)`
        #: 每次追加要走 os.mkdir（必失败）+ is_dir 两次系统调用；本机实测单次
        #: ~200us（Windows 实时防护/慢盘），而事件写入是热路径。缓存后只在首次付
        #: 这个钱；目录若被外部清理，`_append_line_bytes` 会捕获 FileNotFoundError
        #: 重建并重试一次（**自愈**，不引入"缓存过期 ⇒ 静默丢行"的风险）。
        self._dir_ready = False
        #: 锁文件路径：与被写文件分离；`Path.resolve()` 与 log_archiver 同规则
        #: （两侧必须派生到**同一个**锁文件，否则互斥静默失效——由用例锁死）
        self._lock_path = lock_path_for(Path(self._path).resolve())
        self._write_guard: Optional[CrossProcessLock] = None
        if self._lock_enabled:
            self._write_guard = CrossProcessLock(
                self._lock_path, name=f"events:{os.path.basename(self._path)}",
                holder_info={"owner": f"pid:{os.getpid()}"})
        self._owner = f"{os.getpid()}:{id(self)}"
        self._registered = False
        if self._reader:
            self._load_known_ids()
        else:
            register_writer(self._path, self._owner)
            self._registered = True
            self._load_known_ids()
            if self._archive:
                self._maybe_archive("")

    # ── 工厂 ──

    @classmethod
    def reader(cls, path: Optional[str] = None) -> "EventStore":
        """只读 store（不占 writer 登记、不归档；`append` 抛 `ReadOnlyEventStoreError`）"""
        return cls(path, reader=True, archive=False)

    # ── 属性 ──

    @property
    def path(self) -> str:
        return self._path

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def known_event_ids(self) -> Set[str]:
        with self._lock:
            return set(self._ids)

    # ── 写入 ──

    def append(self, envelope: EventEnvelope) -> bool:
        """写入一条事件；**返回是否真正落盘**（幂等重复 → False）

        Raises:
            ReadOnlyEventStoreError: 只读 store。
            EventTypeError: strict 模式下未知事件类型。
        """
        if self._reader:
            raise ReadOnlyEventStoreError("只读 EventStore 不支持 append")
        if self._closed:
            raise EventError("EventStore 已关闭")
        if not self._enabled:
            return False
        if not isinstance(envelope, EventEnvelope):
            raise EventEnvelopeError("append 需要 EventEnvelope 实例")
        if self._strict and not EventType.isin(envelope.type):
            raise EventTypeError(f"未知事件类型: {envelope.type}")
        try:
            with self._lock:
                if envelope.event_id in self._ids:
                    self._duplicate_count += 1
                    logger.debug("事件幂等去重（重复 event_id）: %s", envelope.event_id)
                    return False
                self._write_line(envelope.to_json())
                self._remember(envelope)
                self._write_count += 1
                if not EventType.isin(envelope.type):
                    self._unknown_type_count += 1
                if envelope.type == EV_INTERVENTION:
                    kind = str((envelope.payload or {}).get("kind") or "")
                    if kind:
                        self._pending_interventions.setdefault(
                            envelope.correlation_id, []).append(kind)
            self._maybe_archive(envelope.day)
            self._mirror_to_audit(envelope)
            return True
        except EventTypeError:
            raise
        except Exception as e:  # noqa: BLE001 事件写入 best-effort：绝不阻断主路径
            with self._lock:
                self._failure_count += 1
                self._last_error = f"{type(e).__name__}: {e}"
            logger.warning("事件写入失败（已计数，不影响主路径）: %s", e)
            if self._strict:
                raise
            return False

    def emit(self, event_type: str, payload: Optional[Dict[str, Any]] = None, *,
             actor: str = ACTOR_AUTO, correlation_id: str = "",
             event_id: Optional[str] = None, idempotency_key: str = "",
             ts: Optional[str] = None) -> Optional[EventEnvelope]:
        """构造信封并写入；返回信封（未启用/写失败 → None）"""
        if not self._enabled:
            return None
        try:
            event_type = normalize_type(event_type)
            cid = correlation_id or default_correlation_id()
            env = EventEnvelope(
                event_id=event_id or build_event_id(
                    event_type, actor, cid, payload, idempotency_key),
                ts=ts or now_ts(), correlation_id=cid, actor=actor,
                type=event_type, payload=dict(payload or {}))
            written = self.append(env)
            return env if written else None
        except Exception as e:  # noqa: BLE001 best-effort
            with self._lock:
                self._failure_count += 1
                self._last_error = f"{type(e).__name__}: {e}"
            logger.warning("事件发射失败（不影响主路径）: %s", e)
            if self._strict:
                raise
            return None

    # ── 读取 ──

    def read(self, **kwargs: Any) -> List[EventEnvelope]:
        """读取事件（默认本目录全部活动 + 归档分片；见 `iter_events`）"""
        return iter_events(directory=os.path.dirname(self._path), **kwargs)

    def tail(self, limit: int = 50, **kwargs: Any) -> List[EventEnvelope]:
        """最近 limit 条（时间序）"""
        rows = self.read(**kwargs)
        return rows[-int(limit):] if limit else rows

    def interventions_for(self, correlation_id: str) -> List[str]:
        """本进程内某关联 id 已发生的介入 kind 列表（供 `task.closed` 汇总）"""
        with self._lock:
            return list(self._pending_interventions.get(str(correlation_id or ""), []))

    def clear_interventions(self, correlation_id: str) -> List[str]:
        """取出并清空某关联 id 的介入 kind 列表"""
        with self._lock:
            return list(self._pending_interventions.pop(str(correlation_id or ""), []))

    # ── 收尾/状态 ──

    def flush(self) -> bool:
        """把**降级缓冲**的积压行补写落盘；返回是否已无积压（True = 全部落盘）

        【为什么这里成了有意义的操作】正常路径逐条直写（无异步队列），本方法是
        历史接口的保留；但 S8-02 之后多了一条"取不到跨进程锁 ⇒ 先排队、
        下一次成功取锁时排空"的降级路径，调用方需要一个显式的"现在就排空"入口。

        【锁序（防死锁）】始终**先** `self._lock`（进程内）**后**跨进程锁：
        `append()` 也是这个顺序。反过来会在两个线程间形成环形等待。
        """
        with self._lock:
            if not self._pending_lines:
                return True
            if self._reader or not self._enabled:
                return False
            return self._drain_locked()

    def close(self) -> None:
        """关闭 store：**先尽力排空降级缓冲**，再释放 writer 登记"""
        try:
            if self._pending_lines and not self._reader:
                with self._lock:
                    self._drain_locked()
        except Exception as e:  # noqa: BLE001 关闭路径绝不因排空失败而抛
            logger.warning("关闭前排空事件降级缓冲失败（已计数，行仍在内存缓冲）: %s", e)
        if self._registered:
            release_writer(self._path, self._owner)
            self._registered = False
        self._closed = True

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            reads = read_stats()
            return {
                "path": self._path,
                "enabled": self._enabled,
                "audit_mirror": self._audit_mirror,
                "archive": self._archive,
                "schema": SCHEMA_NAME,
                "schema_version": SCHEMA_VERSION,
                "write_count": self._write_count,
                "duplicate_count": self._duplicate_count,
                "failure_count": self._failure_count,
                "unknown_type_count": self._unknown_type_count,
                "idempotency_index_size": len(self._ids),
                "archived_total": self._archived_total,
                "last_error": self._last_error,
                # ── TASK-S8-02 加固后的可观测面 ──
                "lock_enabled": self._lock_enabled,
                "lock_path": self._lock_path,
                "lock_timeout_sec": self._lock_timeout,
                "lock_write_count": self._lock_write_count,
                "lock_bypass_count": self._lock_bypass_count,
                "durable_write_count": self._durable_write_count,
                "pending_unsaved": len(self._pending_lines),
                "dropped_count": self._dropped_count,
                # 读侧腐坏（进程内累计；坏行不再静默）
                "skipped_line_count": reads["skipped_line_count"],
                "skipped_oversized_line_count": reads["skipped_oversized_line_count"],
                "unreadable_file_count": reads["unreadable_file_count"],
            }

    # ── 内部 ──

    def _write_line(self, line: str) -> None:
        """跨进程安全追加一行（**持锁 → 单次 `os.write`**；取锁失败走排队降级）

        【为什么必须加锁（TASK-S8-02）】原实现是 `open(path,"a")` + `write`：
        POSIX 上 `O_APPEND` 的短行写入恰好原子，但 **Windows 无此保证**，而且
        完全没有跨进程互斥——两个进程并发追加会**交错/撕行**；读侧又会静默跳过
        解析不了的行，于是腐坏是**看不见的**。

        【降级设计：有限等待 + 内存排队 + 排空（选项 b，配 (c) 作终端态）】
        取锁用**有限等待**（`CP_EVENTS_LOCK_TIMEOUT_SEC`，默认 2.0s），等不到时
        比较三种可选降级：

        - **(a) 直接无锁追加**：写入"当前"文件——但归档方正在做
          read-modify-write（`os.replace`）时，这一行会落在**即将被替换掉的旧
          inode** 上，`os.replace` 后被**静默吞掉**。即"不丢"只是错觉。
        - **(b) 内存排队 + 下次成功取锁时排空（本实现）**：把整行放进
          `_pending_lines`，下一次取锁成功时**按原序**补写。既不撕行，也不会
          撞上归档的替换窗口；`flush()` / `close()` 也各给一次排空机会。
        - **(c) 显式失败（丢弃并计数）**：只在 (b) 的缓冲**打满**时作为终端态
          使用——抛 `EventError`（由 `append()` 计入 `failure_count` 并告警，
          `emit()` 返回 None），**不是静默**。

        【为什么不用无条件阻塞长等待】本方法在热路径上（`emit()` 是主路径的
        旁路），无限等待会把"另一个进程在归档"变成"本进程卡住"。

        【降级必须可观测】`stats()` 里的 `lock_bypass_count` / `pending_unsaved`
        / `dropped_count` 三者 + 限流告警 + 锁原语自身的冲突/超时留痕
        （审计链 + 事件流），保证"降级"永远不是静默点。

        【为什么缓存"父目录已就绪"（延迟实测结论）】原实现每次追加都调
        `Path.mkdir(parents=True, exist_ok=True)`；本机实测该调用 p50 ≈ 200us
        （os.mkdir 必失败 + is_dir 两次系统调用，Windows 实时防护下更慢），
        而整条追加路径的裸 `os.open/write/close` 才 ~150us —— 即"每次建目录"
        比"写文件"还贵。改为**首次付一次**、之后跳过；目录被外部清理时由
        `_append_line_bytes` 的 FileNotFoundError 分支重建重试（自愈）。
        """
        with self._lock:
            data = (line + "\n").encode("utf-8")
            ensure_dir = not self._dir_ready
            guard = self._write_guard
            if guard is None:
                # 锁被显式关闭（`CP_EVENTS_LOCK_ENABLED=0`，测试/应急）：
                # 仍用**单次 os.write**，POSIX 上短行仍不撕
                _append_line_bytes(self._path, data, ensure_dir=ensure_dir)
                self._dir_ready = True
                self._durable_write_count += 1
                return
            try:
                guard.acquire(self._lock_timeout)
            except LockError as exc:
                self._note_lock_degraded(exc)
                self._buffer_pending(data)
                return
            try:
                drained = self._drain_pending_locked()
                _append_line_bytes(self._path, data, ensure_dir=ensure_dir)
            except OSError:
                # 补写失败：把积压与本次都留在缓冲（**一行都不丢**），异常照常上抛
                # 由 `append()` 计入 failure_count 并告警（best-effort 不阻断主路径）
                self._pending_lines.append(data)
                raise
            finally:
                guard.release()
            self._dir_ready = True
            self._durable_write_count += 1 + drained
            self._lock_write_count += 1

    def _note_lock_degraded(self, exc: BaseException) -> None:
        """取锁失败 → 计数 + 限流告警（锁原语已另行留痕到审计链/事件流）"""
        self._lock_bypass_count += 1
        self._last_error = f"lock:{type(exc).__name__}: {exc}"
        total = self._lock_bypass_count
        if total == 1 or total % 100 == 0:
            logger.warning(
                "事件写入未取得跨进程锁（第 %d 次，已转入排队降级，不丢数据）%s: %s",
                total, self._path, exc)

    def _buffer_pending(self, data: bytes) -> None:
        """降级缓冲入队；缓冲**打满**时显式失败（绝不静默丢）"""
        if len(self._pending_lines) >= self._pending_max:
            self._dropped_count += 1
            raise EventError(
                f"事件降级缓冲已满（{self._pending_max} 行未落盘，锁持续不可用）"
                f"，本条显式失败：{self._path}")
        self._pending_lines.append(data)

    def _drain_pending_locked(self) -> int:
        """排空降级缓冲（**须已持跨进程锁**；返回本次补写行数）

        顺序保证：补齐顺序 == 原写入顺序（FIFO）。逐行 `os.write` 而不是把多行
        拼成一个块：拼接会放大单次写的大小，且中途失败时"已写/未写"的边界
        更难界定（这里用"写一行、弹一行"）。
        """
        drained = 0
        ensure_dir = not self._dir_ready
        try:
            for head in self._pending_lines:
                _append_line_bytes(self._path, head, ensure_dir=ensure_dir)
                ensure_dir = False
                drained += 1
        finally:
            # 失败时只把**已成功落盘**的部分移出缓冲（剩下的留待下次，一行不丢）
            if drained:
                del self._pending_lines[:drained]
                self._dir_ready = True
        return drained

    def _drain_locked(self) -> bool:
        """在已有的进程内锁下尝试取跨进程锁并排空（`flush()`/`close()` 用）"""
        guard = self._write_guard
        if guard is None:
            drained = self._drain_pending_locked()
            self._durable_write_count += drained
            return not self._pending_lines
        try:
            guard.acquire(self._lock_timeout)
        except LockError as exc:
            self._note_lock_degraded(exc)
            return False
        try:
            drained = self._drain_pending_locked()
        finally:
            guard.release()
        self._durable_write_count += drained
        return not self._pending_lines

    def _remember(self, envelope: EventEnvelope) -> None:
        self._ids.add(envelope.event_id)
        self._id_order.append(envelope.event_id)
        self._kinds[envelope.event_id] = envelope.type
        if len(self._id_order) > MAX_IDEMPOTENCY_INDEX:
            drop = self._id_order[:len(self._id_order) - MAX_IDEMPOTENCY_INDEX]
            del self._id_order[:len(self._id_order) - MAX_IDEMPOTENCY_INDEX]
            for eid in drop:
                self._ids.discard(eid)
                self._kinds.pop(eid, None)

    def _load_known_ids(self) -> None:
        """从活动文件加载已知 event_id（幂等索引冷启动）

        坏行**不再静默**：计入 `read_stats()`（并入 `stats()['skipped_line_count']`）
        并限流告警——冷启动阶段恰是"上次崩溃/撕行"最可能被发现的地方。
        """
        try:
            if not os.path.exists(self._path):
                return
            with open(self._path, "r", encoding="utf-8", errors="ignore") as fh:
                for line_no, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    if len(line) > MAX_LINE_BYTES:
                        _note_skipped_line(self._path, line_no, oversized=True)
                        continue
                    try:
                        data = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        _note_skipped_line(self._path, line_no, reason="非法 JSON")
                        continue
                    if not isinstance(data, dict):
                        _note_skipped_line(self._path, line_no, reason="非 JSON 对象")
                        continue
                    eid = str(data.get("event_id") or "")
                    if eid:
                        self._ids.add(eid)
                        self._id_order.append(eid)
                        self._kinds[eid] = str(data.get("type") or "")
            if len(self._id_order) > MAX_IDEMPOTENCY_INDEX:
                self._id_order = self._id_order[-MAX_IDEMPOTENCY_INDEX:]
                self._ids = set(self._id_order)
        except OSError as e:
            logger.debug("事件幂等索引加载失败（按空处理）: %s", e)

    def _maybe_archive(self, event_day_value: str = "") -> None:
        """跨日（或写入历史行）→ 调用 log_archiver 归档历史行（复用既有存档逻辑）

        触发规则（避免每条事件都重写文件）：
        - 写入的事件属于**历史日**（补写/回放）→ 立即归档；
        - 否则每个进程每个自然日最多归档一次（跨日后首条事件触发）。
        """
        if not self._archive:
            return
        today = datetime.now().date().isoformat()
        day = str(event_day_value or today)[:10]
        with self._lock:
            if day >= today and self._last_archive_day == today:
                return
            self._last_archive_day = today
        try:
            from agent.skills_mgmt.log_archiver import archive_daily_file
            result = archive_daily_file(self._path)
            self._archived_total += int(result.get("archived") or 0)
        except Exception as e:  # noqa: BLE001 归档失败不影响事件写入
            logger.debug("事件按日归档失败（不影响写入）: %s", e)

    def _mirror_to_audit(self, envelope: EventEnvelope) -> None:
        """治理类事件镜像入链式审计（S2-02 平权台账；best-effort）

        依赖倒置：本模块**不在导入期**拉 `agent.audit`（避免与
        `agent.audit.logger → agent.observability.tracer` 形成环），仅在需要时惰性导入。
        """
        if not self._audit_mirror or envelope.type not in AUDIT_MIRROR_TYPES:
            return
        try:
            from agent.audit import audit as audit_facade
            payload = dict(envelope.payload or {})
            payload["event_id"] = envelope.event_id
            payload["events_schema"] = SCHEMA_NAME
            subject = (payload.get("capability_id") or payload.get("task_id")
                       or payload.get("record_id") or payload.get("path") or "")
            audit_facade.record(
                envelope.type, actor=envelope.actor,
                subject=str(subject), payload=payload, source="agent",
                extra={"event_id": envelope.event_id, "event_ts": envelope.ts})
        except Exception as e:  # noqa: BLE001 镜像失败不影响事件流
            logger.debug("事件审计镜像失败 type=%s: %s", envelope.type, e)


# ════════════════════════════════════════════════════════════
#  进程单例 + 模块级 emit
# ════════════════════════════════════════════════════════════

_STORE: Optional[EventStore] = None
_STORE_READER: Optional[EventStore] = None
_STORE_LOCK = threading.Lock()


def get_event_store(reload: bool = False) -> EventStore:
    """进程级统一事件出口（懒加载单例）"""
    global _STORE
    if _STORE is None or reload:
        with _STORE_LOCK:
            if _STORE is None or reload:
                _STORE = EventStore()
    return _STORE


def get_event_reader() -> EventStore:
    """进程级只读出口（后台/聚合方使用，不占 writer 登记）"""
    global _STORE_READER
    if _STORE_READER is None:
        with _STORE_LOCK:
            if _STORE_READER is None:
                _STORE_READER = EventStore.reader()
    return _STORE_READER


def default_correlation_id() -> str:
    """当前上下文的关联 id（TraceContext.trace_id → task_id → ""）"""
    try:
        from agent.observability.trace_v2 import TraceContext
        ctx = TraceContext.current()
        if ctx is not None:
            return str(ctx.trace_id or ctx.task_id or "")
    except Exception:  # noqa: BLE001 无上下文不是错误
        pass
    return ""


def trace_fields() -> Dict[str, str]:
    """当前 TraceContext 的叶子字段（task_id/workspace_id/subject_id/trace_id）

    只读叶子，不搬运 live 对象（任务书纪律）。
    """
    try:
        from agent.observability.trace_v2 import TraceContext
        ctx = TraceContext.current()
    except Exception:  # noqa: BLE001
        ctx = None
    if ctx is None:
        return {"task_id": "", "workspace_id": "", "subject_id": "", "trace_id": ""}
    return {
        "task_id": str(ctx.task_id or ""),
        "workspace_id": str(ctx.workspace_id or ""),
        "subject_id": str(ctx.subject_id or ""),
        "trace_id": str(ctx.trace_id or ""),
    }


def emit(event_type: str, payload: Optional[Dict[str, Any]] = None, *,
         actor: str = ACTOR_AUTO, correlation_id: str = "",
         event_id: Optional[str] = None, idempotency_key: str = "",
         ts: Optional[str] = None, store: Optional[EventStore] = None
         ) -> Optional[EventEnvelope]:
    """统一事件出口：``emit(type, payload, *, actor, correlation_id)``

    幂等键（`event_id`）由 `build_event_id` 确定性派生；写入 `data/events/events.jsonl`。
    **best-effort**：任何失败都被吞掉（返回 None），绝不阻断调用方主路径。
    """
    target = store if store is not None else get_event_store()
    return target.emit(event_type, payload, actor=actor, correlation_id=correlation_id,
                       event_id=event_id, idempotency_key=idempotency_key, ts=ts)


# ════════════════════════════════════════════════════════════
#  读取 / 聚合基元
# ════════════════════════════════════════════════════════════


def iter_events(*, since: Optional[str] = None, until: Optional[str] = None,
                types: Optional[Iterable[str]] = None, day: Optional[str] = None,
                correlation_id: str = "", directory: Optional[str] = None,
                dedupe: bool = True, limit: Optional[int] = None,
                files: Optional[Sequence[str]] = None) -> List[EventEnvelope]:
    """读取事件信封（活动文件 + 按日归档分片；跨分片按 `event_id` 去重）

    Args:
        since/until: 时间窗（ISO 前缀比较：`ts >= since` / `ts <= until`）。
        types: 事件类型白名单。
        day: 精确到某日（等价 since/until 收窄到该日）。
        correlation_id: 关联 id 精确过滤。
        dedupe: 按 `event_id` 去重（**重放不重复计数**的关键保证）。
        limit: 最多返回条数（时间序尾部）。
    """
    if day:
        # 日窗：`ts` 以 `YYYY-MM-DD` 开头 → 前缀比较即可锁定当日全部记录
        since = str(day)
        until = f"{day}\uffff"
    wanted = set(types) if types else None
    paths = list(files) if files else event_files(directory)
    seen: Set[str] = set()
    out: List[EventEnvelope] = []
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line_no, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    if len(line) > MAX_LINE_BYTES:
                        _note_skipped_line(path, line_no, oversized=True)
                        continue
                    try:
                        data = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        _note_skipped_line(path, line_no, reason="非法 JSON")
                        continue
                    if not isinstance(data, dict):
                        _note_skipped_line(path, line_no, reason="非 JSON 对象")
                        continue
                    eid = str(data.get("event_id") or "")
                    if dedupe and eid and eid in seen:
                        continue
                    etype = str(data.get("type") or "")
                    if wanted is not None and etype not in wanted:
                        continue
                    ts = str(data.get("ts") or "")
                    if since and ts and ts < str(since):
                        continue
                    if until and ts and ts > str(until):
                        continue
                    if correlation_id and str(data.get("correlation_id") or "") != correlation_id:
                        continue
                    try:
                        env = EventEnvelope.from_dict(data)
                    except EventEnvelopeError:
                        _note_skipped_line(path, line_no, reason="信封字段非法")
                        continue
                    if dedupe and env.event_id:
                        seen.add(env.event_id)
                    out.append(env)
        except OSError as e:
            _bump_unreadable_file()
            logger.warning("事件文件读取失败 %s: %s", path, e)
    out.sort(key=lambda e: (e.ts, e.event_id))
    if limit:
        out = out[-int(limit):]
    return out


def read_events(**kwargs: Any) -> List[EventEnvelope]:
    """`iter_events` 的语义别名（读侧接口统一命名）"""
    return iter_events(**kwargs)


def group_by_day(events: Iterable[EventEnvelope]) -> Dict[str, List[EventEnvelope]]:
    """按日历日分桶（`ts[:10]`）"""
    buckets: Dict[str, List[EventEnvelope]] = {}
    for env in events:
        buckets.setdefault(env.day, []).append(env)
    return buckets


def filter_types(events: Iterable[EventEnvelope],
                 types: Iterable[str]) -> List[EventEnvelope]:
    wanted = set(types)
    return [e for e in events if e.type in wanted]


__all__ = [
    "SCHEMA_NAME", "SCHEMA_VERSION", "ENVELOPE_FIELDS", "ACTIVE_FILENAME",
    "MAX_IDEMPOTENCY_INDEX", "AUDIT_MIRROR_TYPES",
    "ACTOR_HUMAN", "ACTOR_AUTO", "ACTOR_SUB_AGENT", "ACTOR_SYSTEM", "ACTOR_UI",
    "ENV_ENABLED", "ENV_DIR", "ENV_AUDIT_MIRROR", "ENV_ARCHIVE",
    "ENV_LOCK_ENABLED", "ENV_LOCK_TIMEOUT_SEC", "ENV_PENDING_MAX",
    "ENV_CORRUPT_LOG_EVERY",
    "DEFAULT_LOCK_TIMEOUT_SEC", "DEFAULT_PENDING_MAX", "DEFAULT_CORRUPT_LOG_EVERY",
    "read_stats", "reset_read_stats",
    "EV_TOOL_CALLED", "EV_DIGEST_STAGE", "EV_SKILL_GENERATED", "EV_APPROVAL_REQUIRED",
    "EV_HEALING_TRIGGERED", "EV_METRICS_DELTA", "EV_POLICY_DENIED", "EV_BACKUP_HEALTH",
    "EV_MODEL_DEGRADED", "EV_TASK_CLOSED", "EV_TASK_ABANDONED", "EV_APPROVAL",
    "EV_ESCAPE", "EV_INTERVENTION", "EV_COST", "EV_RETENTION_RUN",
    "CORE_EVENT_TYPES", "NINE_EVENT_TYPES", "METRIC_EVENT_TYPES",
    "GOVERNANCE_EVENT_TYPES", "ALL_EVENT_TYPES",
    "EventType", "EventEnvelope", "EventStore", "EventError", "EventEnvelopeError",
    "EventTypeError", "SingleWriterViolationError", "ReadOnlyEventStoreError",
    "normalize_type",
    "build_event_id", "canonical_payload", "sanitize_payload", "now_ts", "event_day",
    "shift_day", "default_events_dir", "active_events_path", "event_files",
    "register_writer", "release_writer", "active_writers", "reset_event_stores",
    "get_event_store", "get_event_reader", "default_correlation_id", "trace_fields",
    "emit", "iter_events", "read_events", "group_by_day", "filter_types",
]
