"""P3 scoped 档配额与熔断 —— 纯逻辑 + 可注入时钟 + fail-soft 审计

【任务定位】
    scoped 档放开了分身对**自身私人记忆域**的读写（§5.7 机制 3 的受控例外）。放开
    必须有界：本模块提供 `MemoryQuotaGuard`（条数 / 字节配额 + 连续拒绝熔断），
    超限一律给出**明确错误码**拒绝，绝不静默丢弃（静默丢 = 用户以为写成功了）。

【不易】
    · **纯逻辑**：不碰真实记忆库、不依赖执行器；时钟可注入（测试确定性）。
    · **默认不启用**：本模块只在显式 scoped 档下被调用，默认档（none/brokered）
      绝不触达（不改变任何既有行为）。
    · **审计 fail-soft**：审计写入失败不得阻断配额判定本身（但结果可查）。

【错误码】
    · `E_MEMORY_QUOTA_EXCEEDED` —— 条数或字节配额超限；
    · `E_MEMORY_BREAKER_OPEN`    —— 连续拒绝达阈值，熔断打开，后续一律拒绝。

【审计事件（`subagent.memory.scoped.*`）】
    · `subagent.memory.scoped.enabled`      显式开启 scoped 档（安全姿态变更）；
    · `subagent.memory.scoped.write`        一次成功写入；
    · `subagent.memory.scoped.reject`       一次被拒（含配额/域越界等）；
    · `subagent.memory.scoped.breaker_open` 熔断打开。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "E_MEMORY_QUOTA_EXCEEDED",
    "E_MEMORY_BREAKER_OPEN",
    "AUDIT_SCOPED_ENABLED",
    "AUDIT_SCOPED_WRITE",
    "AUDIT_SCOPED_REJECT",
    "AUDIT_SCOPED_BREAKER_OPEN",
    "MEMORY_QUOTA_KEYS",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_CONSECUTIVE_REJECT_LIMIT",
    "MemoryQuotaConfigError",
    "MemoryQuotaDecision",
    "MemoryQuotaGuard",
    "normalize_memory_quota",
    "emit_scoped_audit",
    "guard_from_quota",
]

#: 配额超限（条数 / 字节）
E_MEMORY_QUOTA_EXCEEDED = "E_MEMORY_QUOTA_EXCEEDED"
#: 连续拒绝达阈值 ⇒ 熔断打开
E_MEMORY_BREAKER_OPEN = "E_MEMORY_BREAKER_OPEN"

#: 显式开启 scoped 档的审计事件名
AUDIT_SCOPED_ENABLED = "subagent.memory.scoped.enabled"
AUDIT_SCOPED_WRITE = "subagent.memory.scoped.write"
AUDIT_SCOPED_REJECT = "subagent.memory.scoped.reject"
AUDIT_SCOPED_BREAKER_OPEN = "subagent.memory.scoped.breaker_open"

#: memory_quota 允许的键（唯一词表；未知键 ⇒ 配置错误，不静默忽略）
MEMORY_QUOTA_KEYS = ("max_entries", "max_bytes", "consecutive_reject_limit")

DEFAULT_MAX_ENTRIES = 1000
DEFAULT_MAX_BYTES = 1048576
DEFAULT_CONSECUTIVE_REJECT_LIMIT = 5


class MemoryQuotaConfigError(ValueError):
    """配额配置非法（端点据此转 400 E_MEMORY_CONFIG）"""


def normalize_memory_quota(raw: Any) -> Dict[str, int]:
    """归一化并校验 memory_quota（**不静默夹取**：非法即抛）

    · None / 空 dict ⇒ {}（用默认配额）；
    · 必须是键值表，未知键 ⇒ 报错；
    · 值必须是正整数（bool 不算），否则报错。
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise MemoryQuotaConfigError(
            "memory_quota 必须是对象（键值表），收到: %s" % type(raw).__name__)
    out: Dict[str, int] = {}
    unknown = [k for k in raw.keys() if k not in MEMORY_QUOTA_KEYS]
    if unknown:
        raise MemoryQuotaConfigError(
            "memory_quota 含未知键 %s；允许: %s"
            % (sorted(str(k) for k in unknown), " / ".join(MEMORY_QUOTA_KEYS)))
    for key in MEMORY_QUOTA_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, bool):
            raise MemoryQuotaConfigError("memory_quota.%s 必须是正整数，收到 bool" % key)
        try:
            ivalue = int(value)
        except (TypeError, ValueError) as e:
            raise MemoryQuotaConfigError(
                "memory_quota.%s 必须是正整数，收到: %r" % (key, value)) from e
        if ivalue <= 0:
            raise MemoryQuotaConfigError(
                "memory_quota.%s 必须 > 0，收到: %r" % (key, value))
        out[key] = ivalue
    return out


def emit_scoped_audit(audit: Any, action: str, *, actor: str = "sub_agent",
                      subject: str = "", payload: Optional[Mapping[str, Any]] = None,
                      status: str = "") -> bool:
    """写一条 scoped 审计（**fail-soft**：审计失败不阻断主路径，返回是否写入）

    审计接口与 `DelegationExecutor` 同款：`record(action, actor=, subject=, payload=,
    status=)`。audit=None ⇒ 不写（返回 False），不报错。
    """
    if audit is None:
        return False
    try:
        audit.record(action, actor=str(actor or "sub_agent"), subject=str(subject or ""),
                     payload=dict(payload or {}), status=str(status or ""))
        return True
    except Exception as e:  # noqa: BLE001 审计失败绝不阻断配额判定
        logger.warning("[MemoryQuota] 审计写入失败 %s: %s", action, e)
        return False


@dataclass(frozen=True)
class MemoryQuotaDecision:
    """一次写入准入判定的结果（可直接入审计/HTTP 回显）"""

    allowed: bool
    code: str = ""
    reason: str = ""
    entries: int = 0
    bytes: int = 0
    max_entries: int = 0
    max_bytes: int = 0
    consecutive_rejects: int = 0
    breaker_open: bool = False
    recorded: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": bool(self.allowed),
            "code": self.code,
            "reason": self.reason,
            "entries": int(self.entries),
            "bytes": int(self.bytes),
            "max_entries": int(self.max_entries),
            "max_bytes": int(self.max_bytes),
            "consecutive_rejects": int(self.consecutive_rejects),
            "breaker_open": bool(self.breaker_open),
            "recorded": bool(self.recorded),
        }


class MemoryQuotaGuard:
    """条数 / 字节配额 + 连续拒绝熔断（**纯逻辑**，时钟与审计可注入）

    Args:
        max_entries / max_bytes: 配额上限（条数 / 累计字节）；
        consecutive_reject_limit: 连续拒绝达此数 ⇒ 熔断打开；
        clock: 时钟（缺省 `time.monotonic`），用于记录熔断打开时刻；
        audit: 审计注入点（缺省 None）；
        actor / subject: 审计主体标识（只放标识，不放正文）。

    语义：
        · 每次 `admit()` 先看熔断；熔断打开 ⇒ `E_MEMORY_BREAKER_OPEN`；
        · 配额超限 ⇒ `E_MEMORY_QUOTA_EXCEEDED`，并累计连续拒绝；
        · 连续拒绝达阈值的那一次 ⇒ 就地打开熔断并返回 `E_MEMORY_BREAKER_OPEN`；
        · 成功写入清零连续拒绝计数（熔断不可自动恢复，需重建 guard / reset）。
    """

    def __init__(self, *, max_entries: int = DEFAULT_MAX_ENTRIES,
                 max_bytes: int = DEFAULT_MAX_BYTES,
                 consecutive_reject_limit: int = DEFAULT_CONSECUTIVE_REJECT_LIMIT,
                 clock: Optional[Callable[[], float]] = None,
                 audit: Any = None, actor: str = "", subject: str = "") -> None:
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self.consecutive_reject_limit = max(1, int(consecutive_reject_limit))
        self._clock = clock or time.monotonic
        self._audit = audit
        self._actor = str(actor or "sub_agent")
        self._subject = str(subject or "")
        self._entries = 0
        self._bytes = 0
        self._consecutive_rejects = 0
        self._breaker_open = False
        self._opened_at = 0.0

    # ── 视图 ──

    @property
    def breaker_open(self) -> bool:
        return self._breaker_open

    @property
    def consecutive_rejects(self) -> int:
        return self._consecutive_rejects

    def status(self) -> Dict[str, Any]:
        """当前配额/熔断状态（**只含标识与计数，不含记忆正文**）"""
        return {
            "entries": self._entries,
            "bytes": self._bytes,
            "max_entries": self.max_entries,
            "max_bytes": self.max_bytes,
            "consecutive_rejects": self._consecutive_rejects,
            "consecutive_reject_limit": self.consecutive_reject_limit,
            "breaker_open": self._breaker_open,
            "opened_at": self._opened_at,
        }

    def reset(self) -> None:
        """清空计数与熔断（测试隔离 / 显式人工恢复用）"""
        self._entries = 0
        self._bytes = 0
        self._consecutive_rejects = 0
        self._breaker_open = False
        self._opened_at = 0.0

    # ── 准入 ──

    def _decision(self, allowed: bool, code: str, reason: str,
                  recorded: bool = False) -> MemoryQuotaDecision:
        return MemoryQuotaDecision(
            allowed=allowed, code=code, reason=reason,
            entries=self._entries, bytes=self._bytes,
            max_entries=self.max_entries, max_bytes=self.max_bytes,
            consecutive_rejects=self._consecutive_rejects,
            breaker_open=self._breaker_open, recorded=recorded)

    def admit(self, *, size_bytes: int = 0, action: str = "write") -> MemoryQuotaDecision:
        """请求一次写入准入（配额/熔断判定；不真正落库）

        Args:
            size_bytes: 本次写入（记忆正文）的字节数；
            action: 审计载荷里的动作名（缺省 write）。

        Returns:
            `MemoryQuotaDecision`；`allowed=False` 时 `code` 为上面两个错误码之一。
        """
        size = max(0, int(size_bytes or 0))
        payload = {"action": str(action or "write"), "size_bytes": size,
                   "entries": self._entries, "bytes": self._bytes,
                   "max_entries": self.max_entries, "max_bytes": self.max_bytes,
                   "consecutive_rejects": self._consecutive_rejects}
        if self._breaker_open:
            recorded = emit_scoped_audit(
                self._audit, AUDIT_SCOPED_REJECT, actor=self._actor,
                subject=self._subject,
                payload={**payload, "code": E_MEMORY_BREAKER_OPEN}, status="rejected")
            return self._decision(
                False, E_MEMORY_BREAKER_OPEN,
                "scoped 记忆写入熔断已打开（连续拒绝达阈值）：需重建/显式恢复",
                recorded=recorded)

        over_entries = (self._entries + 1) > self.max_entries
        over_bytes = (self._bytes + size) > self.max_bytes
        if over_entries or over_bytes:
            self._consecutive_rejects += 1
            quota_reason = (
                "scoped 记忆配额超限：条数 %d/%d" % (self._entries + 1, self.max_entries)
                if over_entries else
                "scoped 记忆配额超限：字节 %d/%d" % (self._bytes + size, self.max_bytes))
            if self._consecutive_rejects >= self.consecutive_reject_limit:
                self._breaker_open = True
                self._opened_at = float(self._clock())
                recorded = emit_scoped_audit(
                    self._audit, AUDIT_SCOPED_BREAKER_OPEN, actor=self._actor,
                    subject=self._subject,
                    payload={**payload, "code": E_MEMORY_BREAKER_OPEN,
                             "consecutive_rejects": self._consecutive_rejects},
                    status="open")
                return self._decision(
                    False, E_MEMORY_BREAKER_OPEN,
                    "scoped 记忆写入连续被拒达阈值，熔断打开：%s" % quota_reason,
                    recorded=recorded)
            recorded = emit_scoped_audit(
                self._audit, AUDIT_SCOPED_REJECT, actor=self._actor,
                subject=self._subject,
                payload={**payload, "code": E_MEMORY_QUOTA_EXCEEDED,
                         "consecutive_rejects": self._consecutive_rejects},
                status="rejected")
            return self._decision(False, E_MEMORY_QUOTA_EXCEEDED, quota_reason,
                                  recorded=recorded)

        self._entries += 1
        self._bytes += size
        self._consecutive_rejects = 0
        recorded = emit_scoped_audit(
            self._audit, AUDIT_SCOPED_WRITE, actor=self._actor,
            subject=self._subject, payload={**payload, "entries": self._entries,
                                            "bytes": self._bytes}, status="written")
        return self._decision(True, "", "scoped 记忆写入准入通过", recorded=recorded)

    def record_reject(self, code: str, reason: str, *,
                      payload: Optional[Mapping[str, Any]] = None) -> MemoryQuotaDecision:
        """记录一次**外部原因**的拒绝（如域越界），并累计连续拒绝

        供调用方在 tenancy/域校验拒绝时统一走熔断计数与审计；语义与 `admit()` 的
        配额拒绝一致：连续拒绝达阈值 ⇒ 熔断打开。
        """
        self._consecutive_rejects += 1
        if self._consecutive_rejects >= self.consecutive_reject_limit:
            self._breaker_open = True
            self._opened_at = float(self._clock())
            recorded = emit_scoped_audit(
                self._audit, AUDIT_SCOPED_BREAKER_OPEN, actor=self._actor,
                subject=self._subject,
                payload=dict(payload or {}, code=E_MEMORY_BREAKER_OPEN,
                             consecutive_rejects=self._consecutive_rejects),
                status="open")
            return self._decision(False, E_MEMORY_BREAKER_OPEN,
                                  "连续拒绝达阈值，熔断打开：%s" % reason,
                                  recorded=recorded)
        recorded = emit_scoped_audit(
            self._audit, AUDIT_SCOPED_REJECT, actor=self._actor,
            subject=self._subject,
            payload=dict(payload or {}, code=str(code or E_MEMORY_QUOTA_EXCEEDED),
                         consecutive_rejects=self._consecutive_rejects),
            status="rejected")
        return self._decision(False, str(code or E_MEMORY_QUOTA_EXCEEDED),
                              str(reason or ""), recorded=recorded)

    def audit_enabled(self, *, payload: Optional[Mapping[str, Any]] = None) -> bool:
        """写一条 scoped 开启审计（显式开启是安全姿态变更，必须留痕）"""
        enabled_payload = dict(payload or {})
        enabled_payload.setdefault("max_entries", self.max_entries)
        enabled_payload.setdefault("max_bytes", self.max_bytes)
        enabled_payload.setdefault("consecutive_reject_limit",
                                   self.consecutive_reject_limit)
        return emit_scoped_audit(self._audit, AUDIT_SCOPED_ENABLED, actor=self._actor,
                                 subject=self._subject, payload=enabled_payload,
                                 status="enabled")


def guard_from_quota(quota: Optional[Mapping[str, Any]], *,
                     audit: Any = None, actor: str = "", subject: str = "",
                     clock: Optional[Callable[[], float]] = None) -> MemoryQuotaGuard:
    """由已归一化的 memory_quota 构造 guard（缺省用默认配额）"""
    data = dict(quota or {})
    return MemoryQuotaGuard(
        max_entries=int(data.get("max_entries", DEFAULT_MAX_ENTRIES)),
        max_bytes=int(data.get("max_bytes", DEFAULT_MAX_BYTES)),
        consecutive_reject_limit=int(
            data.get("consecutive_reject_limit", DEFAULT_CONSECUTIVE_REJECT_LIMIT)),
        clock=clock, audit=audit, actor=actor, subject=subject)
