"""人机边界词 —— 注入防御机制 5（TASK-S4-03 步骤 4 / v7.2 §5.7 + §7）

【机制原文（§5.7 表第 5 行 + §7 末段）】
    5  人机边界词｜转账/发布/删库/改权限/push --force → UI 显式确认，
       **不接受任何文本形式的"已批准"**
    §7：永不自动化五类：不可逆且超 workspace（发布/转账/删库/改权限/push --force），
       UI 显式确认**绑定单次 action + 60s 时效**。

【三条硬约束（对应验收项）】
    1. **不可自动化**：五类边界操作**永不**由 agent/自动流程执行——只能由人在 UI 确认后
       落地。`is_never_automated()` 是声明式判定；`guard_execution()` 是执行前置闸门。
    2. **绑定单次 action**：一个确认凭据只对**它被签发时的那一个 action** 有效。
       换一个 action（哪怕同类）→ 凭据无效。绑定通过 action 摘要（`action_digest`）实现，
       **不是**靠调用方自觉传对参数。
    3. **60s 时效**：签发后 60 秒内有效，**且不可延长**（
       `MAX_CONFIRMATION_TTL_SECONDS = 60` 是硬上限；`request_ttl` 调大只会被截断并告警）。
       一次性：核销即作废（`used_at` 置位），重放失败。

【"不接受任何文本形式的已批准"怎么落地】
    `confirm()` 的签名里**没有**"文本批准"这个入参——它只接受 `token`。
    想绕过的人只能伪造 token，而 token 由 `ConfirmationStore` 随机签发并只在
    UI 通道下发（见 `issue_to_ui`）。此外 `detect_text_approval()` 提供**审计用**的
    识别（用于把"注入文本试图声称已批准"记进审计与告警），但它**从不**返回"已批准"。

【与 S4-01 审批面的关系】
    S4-01 的 `agent/security/approval_session.py` 已经有成熟的一次性 token + TTL + CSRF +
    二次认证机制（审批面安全，§5.7⑦）。本模块**不重造**那套：边界词确认是**更窄**的一层
    ——它只回答"这次执行是不是五类之一、有没有对应的单次确认"，
    真正的"人是谁、有没有二次认证"仍由 S4-01 的审批流负责（`approval_record_id` 可从
    S4-01 的审批记录带过来，本模块只把它当**证据引用**留痕）。

【不易】TTL 硬上限 60s；单次性靠核销；判定默认开启（无边界词命中时零影响）。
【变易】`BOUNDARY_PATTERNS` 是数据：新增边界词加一条（正则 + 类别 + 中文名）。
【简易】纯标准库；凭据账有容量上限与过期清理。
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("agent.guardrails.boundary_words")

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

ENV_ENABLED = "CP_GUARDRAILS_BOUNDARY_WORDS"
ENV_TTL_SECONDS = "CP_GUARDRAILS_BOUNDARY_TTL_SECONDS"

#: 确认时效（§7 逐字：**60s 时效**）——默认值与硬上限同为 60s
DEFAULT_CONFIRMATION_TTL_SECONDS = 60.0
MAX_CONFIRMATION_TTL_SECONDS = 60.0

#: 凭据账容量上限
DEFAULT_MAX_TOKENS = 500

#: 判定结论
VERDICT_ALLOW = "allow"
VERDICT_NEED_CONFIRM = "need_confirmation"
VERDICT_REJECT = "reject"


class BoundaryWord(str, enum.Enum):
    """永不自动化五类（§7；"不可逆且超 workspace"）"""

    TRANSFER = "transfer"                 # 转账
    PUBLISH = "publish"                   # 发布
    DROP_DATABASE = "drop_database"       # 删库
    PERMISSION_CHANGE = "permission_change"  # 改权限
    FORCE_PUSH = "force_push"             # push --force


#: 类别 → 中文名（错误文案/UI 用）
BOUNDARY_LABELS: Dict[str, str] = {
    BoundaryWord.TRANSFER.value: "转账",
    BoundaryWord.PUBLISH.value: "发布",
    BoundaryWord.DROP_DATABASE.value: "删库",
    BoundaryWord.PERMISSION_CHANGE.value: "改权限",
    BoundaryWord.FORCE_PUSH.value: "push --force",
}

#: §7 逐字的五类（顺序即文档顺序；验收核对用）
NEVER_AUTOMATED: Tuple[str, ...] = (
    BoundaryWord.TRANSFER.value, BoundaryWord.PUBLISH.value,
    BoundaryWord.DROP_DATABASE.value, BoundaryWord.PERMISSION_CHANGE.value,
    BoundaryWord.FORCE_PUSH.value,
)

#: 边界词识别规则（**数据**：新增边界词只需加一条）
#: 每项 = (类别, 中文标签, 编译后的正则)
BOUNDARY_PATTERNS: Tuple[Tuple[str, str, "re.Pattern[str]"], ...] = (
    # ── push --force（先于 publish：`git push --force` 同时含 "push"，需更具体的先判） ──
    (BoundaryWord.FORCE_PUSH.value, "push --force", re.compile(
        r"(?i)(?:"
        r"git\s+push\b[^\n]{0,80}?(?:--force\b|--force-with-lease\b|\s-f\b)"
        r"|push\s+--force"
        r"|force[-\s]?push"
        r"|强制推送"
        r")")),
    # ── 删库 ──
    (BoundaryWord.DROP_DATABASE.value, "删库", re.compile(
        r"(?i)(?:"
        r"drop\s+(?:database|schema|table)\b"
        r"|truncate\s+table\b"
        r"|delete\s+from\s+\w+\s*(?:;|$)"
        r"|rm\s+-rf?\s+(?:/|\*|~|\.\.)"
        r"|shutil\.rmtree\s*\("
        r"|删库|删表|清库|删除数据库|销毁数据"
        r")")),
    # ── 改权限 ──
    (BoundaryWord.PERMISSION_CHANGE.value, "改权限", re.compile(
        r"(?i)(?:"
        r"\bchmod\s+(?:-R\s+)?[0-7]{3,4}\b"
        r"|\bchown\s+(?:-R\s+)?\S+"
        r"|\b(?:grant|revoke)\s+\S+\s+on\b"
        r"|setfacl\b"
        r"|(?:put|attach|create)[-_ ]?(?:user|role)[-_ ]?policy\b"
        r"|iam\s+policy"
        r"|改权限|提权|授予权限|撤销权限"
        r")")),
    # ── 转账 ──
    (BoundaryWord.TRANSFER.value, "转账", re.compile(
        r"(?i)(?:"
        r"\b(?:transfer|remit|disburse|payout)\b[^\n]{0,60}?(?:funds|money|amount|usd|cny|¥|\$)"
        r"|\b(?:amount|sum)\b[^\n]{0,30}?\b(?:transfer|payment)\b"
        r"|转账|付款|支付|汇款|打款"
        r")")),
    # ── 发布 ──
    (BoundaryWord.PUBLISH.value, "发布", re.compile(
        r"(?i)(?:"
        r"\b(?:publish|release|deploy)\b[^\n]{0,60}?(?:to\s+)?(?:prod|production|pypi|npm|marketplace|app\s?store|公开|线上)"
        r"|\b(?:npm\s+publish|twine\s+upload|cargo\s+publish)\b"
        r"|正式发布|上线发布|发版|对外发布|推送生产"
        r")")),
)

#: "文本形式已批准"的识别（**仅用于审计/告警**，从不返回"已批准"）
_TEXT_APPROVAL_RE = re.compile(
    r"(?i)(?:"
    r"(?:已|我)(?:经)?(?:批准|同意|授权|确认)(?:了)?"
    r"|approved?\s+by\s+(?:the\s+)?user"
    r"|user\s+has\s+approved"
    r"|authorized\s*[:：]\s*(?:yes|true)"
    r"|批准\s*[:：]\s*(?:是|yes|true)"
    r")"
)


class BoundaryError(Exception):
    """边界词层基类异常"""


class ConfirmationRequiredError(BoundaryError):
    """边界操作缺少有效的单次人工确认——**拒绝执行**

    Attributes:
        category: 命中的边界类别。
        token_state: 凭据状态（missing/expired/used/mismatch/invalid）。
    """

    def __init__(self, message: str, *, category: str = "",
                 token_state: str = "") -> None:
        self.category = str(category or "")
        self.token_state = str(token_state or "")
        super().__init__(message)


# ════════════════════════════════════════════════════════════
#  识别
# ════════════════════════════════════════════════════════════


def _enabled() -> bool:
    """总开关（默认开：**开启不等于收紧**，无边界词命中时零影响）"""
    return str(os.environ.get(ENV_ENABLED, "1")).strip().lower() \
        not in ("0", "false", "no", "off")


def ttl_seconds() -> float:
    """确认时效（**硬上限 60s**；配置调大只会被截断并告警）"""
    try:
        raw = float(os.environ.get(ENV_TTL_SECONDS, str(DEFAULT_CONFIRMATION_TTL_SECONDS)))
    except (TypeError, ValueError):
        raw = DEFAULT_CONFIRMATION_TTL_SECONDS
    if raw <= 0:
        raw = DEFAULT_CONFIRMATION_TTL_SECONDS
    if raw > MAX_CONFIRMATION_TTL_SECONDS:
        logger.warning("%s=%s 超过硬上限 %.0fs，已截断（§7：绑定单次 action + 60s 时效）",
                       ENV_TTL_SECONDS, raw, MAX_CONFIRMATION_TTL_SECONDS)
        raw = MAX_CONFIRMATION_TTL_SECONDS
    return float(raw)


#: `ConfirmationStore.issue()` 的 `ttl_seconds` **参数**会遮蔽同名模块函数，
#: 故默认值解析走这个别名（实现期实测踩到的真实缺陷，已加回归用例）。
_default_ttl_seconds = ttl_seconds


@dataclass
class BoundaryHit:
    """边界词命中结果"""

    category: str
    label: str
    matched: str

    def to_dict(self) -> Dict[str, Any]:
        return {"category": self.category, "label": self.label,
                "matched": self.matched}


def detect_boundary_words(text: Any) -> List[BoundaryHit]:
    """识别文本中的边界操作（§7 五类；**顺序即优先级**，靠前更具体）

    Returns:
        命中的 `BoundaryHit` 列表（同类只保留首个命中；无命中返回 []）。
    """
    value = str(text or "")
    if not value.strip():
        return []
    hits: List[BoundaryHit] = []
    seen: set = set()
    for category, label, pattern in BOUNDARY_PATTERNS:
        if category in seen:
            continue
        match = pattern.search(value)
        if match:
            hits.append(BoundaryHit(category=category, label=label,
                                    matched=match.group()[:120]))
            seen.add(category)
    return hits


def is_never_automated(text: Any) -> bool:
    """文本是否属于"永不自动化五类"（§7）"""
    return bool(detect_boundary_words(text))


def detect_text_approval(text: Any) -> bool:
    """识别"文本形式的已批准"（**仅用于审计/告警**）

    【纪律】本函数**从不**用于放行——`confirm()` 只认 token。它存在是为了把
    "注入文本试图声称已获批准"这件事**记进审计与告警**（否则这类尝试会静默消失）。
    """
    return bool(_TEXT_APPROVAL_RE.search(str(text or "")))


# ════════════════════════════════════════════════════════════
#  单次确认凭据
# ════════════════════════════════════════════════════════════


def action_digest(action: Any, *, extra: Any = None) -> str:
    """action 绑定摘要（**绑定单次 action** 的实现）

    摘要覆盖 action 的**全部可判定内容**（操作名 + 目标 + 参数），故：
    "批准了 A 的转账"不能拿去执行 "B 的转账"。
    """
    material = json.dumps({"action": action, "extra": extra},
                          ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


#: 凭据状态
TOKEN_OK = "ok"
TOKEN_MISSING = "missing"
TOKEN_UNKNOWN = "unknown_token"
TOKEN_EXPIRED = "expired"
TOKEN_USED = "used"
TOKEN_MISMATCH = "action_mismatch"


@dataclass
class BoundaryConfirmation:
    """一次边界操作的人工确认凭据（**单次 + 60s**）"""

    action_digest: str
    category: str = ""
    issued_at: float = field(default_factory=time.time)
    ttl_seconds: float = DEFAULT_CONFIRMATION_TTL_SECONDS
    token: str = field(default_factory=lambda: "bwc-" + uuid.uuid4().hex[:24])
    used_at: float = 0.0
    issued_by: str = ""              # 签发通道（应为 "ui"）
    approval_record_id: str = ""     # S4-01 审批记录引用（证据留痕）
    note: str = ""

    @property
    def expires_at(self) -> float:
        return self.issued_at + max(0.0, float(self.ttl_seconds))

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at

    @property
    def used(self) -> bool:
        return self.used_at > 0.0

    def remaining_seconds(self, now: Optional[float] = None) -> float:
        return max(0.0, self.expires_at - (now if now is not None else time.time()))

    def to_public(self, now: Optional[float] = None) -> Dict[str, Any]:
        """对外形态（**不含 token 原文**——token 只在签发时回给 UI 一次）"""
        current = now if now is not None else time.time()
        return {
            "category": self.category,
            "action_digest": self.action_digest,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "remaining_seconds": round(self.remaining_seconds(current), 3),
            "ttl_seconds": self.ttl_seconds,
            "used": self.used,
            "expired": self.is_expired(current),
            "issued_by": self.issued_by,
            "approval_record_id": self.approval_record_id,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {**self.to_public(), "token": self.token, "used_at": self.used_at,
                "note": self.note}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BoundaryConfirmation":
        return cls(
            action_digest=str(data.get("action_digest") or ""),
            category=str(data.get("category") or ""),
            issued_at=float(data.get("issued_at") or 0.0),
            ttl_seconds=float(data.get("ttl_seconds") or DEFAULT_CONFIRMATION_TTL_SECONDS),
            token=str(data.get("token") or ""),
            used_at=float(data.get("used_at") or 0.0),
            issued_by=str(data.get("issued_by") or ""),
            approval_record_id=str(data.get("approval_record_id") or ""),
            note=str(data.get("note") or ""),
        )


@dataclass
class ConfirmationCheck:
    """凭据校验结果"""

    state: str
    confirmation: Optional[BoundaryConfirmation] = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == TOKEN_OK

    def to_dict(self) -> Dict[str, Any]:
        return {"state": self.state, "ok": self.ok, "detail": self.detail,
                "confirmation": self.confirmation.to_public() if self.confirmation else None}


class ConfirmationStore:
    """单次确认凭据账（进程内；一次性 + 60s 时效）

    【不易】核销与校验在同一把锁内完成——"检查后使用"（TOCTOU）在这里必须是原子的，
    否则两次并发执行可以共用同一张凭据，单次性就破了。
    """

    def __init__(self, *, max_tokens: Optional[int] = None) -> None:
        self._tokens: Dict[str, BoundaryConfirmation] = {}
        self._max_tokens = int(max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS)
        self._lock = threading.RLock()
        self._issued = 0
        self._redeemed = 0

    # ── 签发 ──

    def issue(
        self,
        action: Any,
        *,
        category: str = "",
        issued_by: str = "ui",
        ttl_seconds: Optional[float] = None,
        approval_record_id: str = "",
        note: str = "",
        extra: Any = None,
    ) -> BoundaryConfirmation:
        """签发一张绑定到**单次 action** 的确认凭据

        Args:
            action: 该次操作的可判定内容（操作名 + 目标 + 参数）。
            category: 边界类别（`BoundaryWord` 值或中文标签）。
            issued_by: 签发通道（应为 `"ui"`——**文本形式不被接受**）。
            ttl_seconds: 覆盖时效（**受 60s 硬上限约束**）。
            approval_record_id: S4-01 审批记录引用（证据留痕）。

        Returns:
            `BoundaryConfirmation`（含 token；调用方须只在 UI 通道回给此人一次）。
        """
        ttl = float(ttl_seconds) if ttl_seconds is not None else _default_ttl_seconds()
        if ttl > MAX_CONFIRMATION_TTL_SECONDS:
            logger.warning("请求时效 %.1fs 超过硬上限，截断为 %.1fs", ttl,
                           MAX_CONFIRMATION_TTL_SECONDS)
            ttl = MAX_CONFIRMATION_TTL_SECONDS
        confirmation = BoundaryConfirmation(
            action_digest=action_digest(action, extra=extra),
            category=str(category or ""),
            ttl_seconds=ttl,
            issued_by=str(issued_by or ""),
            approval_record_id=str(approval_record_id or ""),
            note=str(note or ""),
        )
        with self._lock:
            self._purge_locked()
            self._tokens[confirmation.token] = confirmation
            self._issued += 1
            if len(self._tokens) > self._max_tokens:
                self._evict_oldest_locked(len(self._tokens) - self._max_tokens)
        _audit("guardrails.boundary_confirmation_issued", confirmation)
        return confirmation

    def issue_to_ui(self, action: Any, *, category: str = "",
                    approval_record_id: str = "", extra: Any = None,
                    note: str = "",
                    ttl_seconds: Optional[float] = None) -> BoundaryConfirmation:
        """签发到 UI 通道（`issued_by="ui"` 的语义化入口）

        `ttl_seconds` 同 `issue()`：**受 60s 硬上限约束**（§7），传更大会被截断。
        """
        return self.issue(action, category=category, issued_by="ui",
                          approval_record_id=approval_record_id, extra=extra,
                          note=note, ttl_seconds=ttl_seconds)

    # ── 校验与核销 ──

    def check(self, token: Any, action: Any, *, extra: Any = None) -> ConfirmationCheck:
        """校验凭据（**不核销**；只读，供展示/预检）"""
        with self._lock:
            return self._check_locked(token, action, extra=extra)

    def confirm(self, token: Any, action: Any, *, extra: Any = None,
                consume: bool = True) -> ConfirmationCheck:
        """核销凭据（**原子的一次性校验 + 作废**）

        Args:
            token: 凭据 token（**唯一**的批准形式；不存在"文本批准"入参）。
            action: 本次要执行的 action（必须与签发时的 action 摘要一致）。
            consume: True（默认）→ 校验通过即置 `used_at`（单次性）。

        Returns:
            `ConfirmationCheck`（`ok=False` 时 `state` 说明原因）。
        """
        with self._lock:
            result = self._check_locked(token, action, extra=extra)
            if result.ok and consume and result.confirmation is not None:
                result.confirmation.used_at = time.time()
                self._redeemed += 1
                _audit("guardrails.boundary_confirmation_redeemed",
                       result.confirmation)
            return result

    def _check_locked(self, token: Any, action: Any,
                      *, extra: Any = None) -> ConfirmationCheck:
        text = str(token or "").strip()
        if not text:
            return ConfirmationCheck(state=TOKEN_MISSING,
                                     detail="未提供确认凭据（§5.7 机制 5：必须 UI 显式确认）")
        confirmation = self._tokens.get(text)
        if confirmation is None:
            return ConfirmationCheck(state=TOKEN_UNKNOWN,
                                     detail="确认凭据不存在（伪造或已超出保留窗口）")
        if confirmation.used:
            return ConfirmationCheck(
                state=TOKEN_USED, confirmation=confirmation,
                detail="确认凭据已被使用（§7：绑定**单次** action，不可复用旧确认）")
        if confirmation.is_expired():
            return ConfirmationCheck(
                state=TOKEN_EXPIRED, confirmation=confirmation,
                detail=(f"确认凭据已过期（时效 {confirmation.ttl_seconds:.0f}s，"
                        f"§7：60s 时效）"))
        if confirmation.action_digest != action_digest(action, extra=extra):
            return ConfirmationCheck(
                state=TOKEN_MISMATCH, confirmation=confirmation,
                detail="确认凭据与本次 action 不匹配（绑定的是另一个 action）")
        return ConfirmationCheck(state=TOKEN_OK, confirmation=confirmation)

    # ── 维护 ──

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            self._purge_locked()
            return {"token_count": len(self._tokens), "issued": self._issued,
                    "redeemed": self._redeemed, "max_tokens": self._max_tokens,
                    "ttl_seconds": ttl_seconds(),
                    "max_ttl_seconds": MAX_CONFIRMATION_TTL_SECONDS}

    def reset(self) -> None:
        """清空凭据账（用例隔离）"""
        with self._lock:
            self._tokens.clear()

    def _purge_locked(self) -> int:
        """清理已过期凭据（已核销的也一并清——它已无用途）"""
        now = time.time()
        stale = [t for t, c in self._tokens.items()
                 if c.is_expired(now) or c.used]
        for token in stale:
            self._tokens.pop(token, None)
        return len(stale)

    def _evict_oldest_locked(self, count: int) -> None:
        if count <= 0:
            return
        ordered = sorted(self._tokens.items(), key=lambda kv: kv[1].issued_at)
        for token, _ in ordered[:count]:
            self._tokens.pop(token, None)


_STORE_LOCK = threading.RLock()
_STORE: Optional[ConfirmationStore] = None


def get_confirmation_store() -> ConfirmationStore:
    """进程级凭据账（惰性创建）"""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = ConfirmationStore()
        return _STORE


def set_confirmation_store(store: Optional[ConfirmationStore]) -> Optional[ConfirmationStore]:
    """替换进程级凭据账（用例注入；返回旧账）"""
    global _STORE
    with _STORE_LOCK:
        old, _STORE = _STORE, store
        return old


def reset_confirmation_store() -> None:
    """清空并丢弃进程级凭据账（用例隔离）"""
    global _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            _STORE.reset()
        _STORE = None


# ════════════════════════════════════════════════════════════
#  执行前置闸门
# ════════════════════════════════════════════════════════════


@dataclass
class BoundaryVerdict:
    """边界闸门判定结果"""

    verdict: str
    hits: List[BoundaryHit] = field(default_factory=list)
    reason: str = ""
    token_state: str = ""
    text_approval_claimed: bool = False
    confirmation: Optional[BoundaryConfirmation] = None

    @property
    def allowed(self) -> bool:
        return self.verdict == VERDICT_ALLOW

    @property
    def needs_confirmation(self) -> bool:
        return self.verdict == VERDICT_NEED_CONFIRM

    def to_dict(self) -> Dict[str, Any]:
        return {"verdict": self.verdict, "allowed": self.allowed,
                "needs_confirmation": self.needs_confirmation,
                "reason": self.reason, "token_state": self.token_state,
                "text_approval_claimed": self.text_approval_claimed,
                "hits": [h.to_dict() for h in self.hits],
                "confirmation": (self.confirmation.to_public()
                                 if self.confirmation else None)}


def guard_execution(
    action: Any,
    *,
    action_text: str = "",
    token: Any = None,
    store: Optional[ConfirmationStore] = None,
    extra: Any = None,
    consume: bool = True,
    enforce: bool = False,
    actor: str = "auto",
) -> BoundaryVerdict:
    """**机制 5 落点**：边界操作执行前置闸门（接线到工具执行前）

    逻辑：
        1. 识别 `action_text`（缺省用 `action`）中的边界词；
        2. 无命中 → `allow`（**零影响**：非边界操作完全按既有路径走）；
        3. 有命中 → 凭据必须通过 `confirm()`（单次 + 60s + action 绑定）：
           - 通过 → `allow`（凭据已核销）；
           - 不通过 → `need_confirmation`（`enforce=True` 时抛异常）。
        4. 无论何种结论，若文本里声称"已批准"→ 记审计 + 告警（**从不因此放行**）。

    Args:
        action: 本次操作的可判定内容（用于 action 摘要绑定）。
        action_text: 用于边界词识别的文本（缺省 `str(action)`）。
        token: UI 确认凭据（**唯一**的批准形式）。
        consume: 通过时是否核销（默认核销——**单次**）。
        enforce: True → 未通过时抛 `ConfirmationRequiredError`。

    Returns:
        `BoundaryVerdict`。
    """
    probe = str(action_text or action or "")
    hits = detect_boundary_words(probe) if _enabled() else []
    text_claim = detect_text_approval(probe)
    if text_claim:
        _audit_text_approval(probe, actor=actor, hits=hits)

    if not hits:
        return BoundaryVerdict(verdict=VERDICT_ALLOW, hits=[],
                               text_approval_claimed=text_claim,
                               reason="非边界操作（§7 五类未命中）")

    labels = "、".join(h.label for h in hits)
    active = store or get_confirmation_store()
    check = active.confirm(token, action, extra=extra, consume=consume)
    if check.ok:
        return BoundaryVerdict(
            verdict=VERDICT_ALLOW, hits=hits, token_state=check.state,
            text_approval_claimed=text_claim, confirmation=check.confirmation,
            reason=(f"边界操作（{labels}）已获 UI 单次确认"
                    f"（剩余 {check.confirmation.remaining_seconds():.1f}s 内有效）"
                    if check.confirmation else f"边界操作（{labels}）已获确认"),
        )
    reason = (
        f"边界操作（{labels}）需 UI 显式确认（§5.7 机制 5 / §7 永不自动化五类）："
        f"{check.detail}"
    )
    verdict = BoundaryVerdict(verdict=VERDICT_NEED_CONFIRM, hits=hits,
                              reason=reason, token_state=check.state,
                              text_approval_claimed=text_claim)
    _audit_blocked(verdict, actor=actor)
    if enforce:
        raise ConfirmationRequiredError(reason, category=hits[0].category,
                                        token_state=check.state)
    return verdict


def boundary_state(store: Optional[ConfirmationStore] = None) -> Dict[str, Any]:
    """机制 5 状态快照（诊断/验收报告）"""
    active = store or get_confirmation_store()
    return {
        "enabled": _enabled(),
        "never_automated": list(NEVER_AUTOMATED),
        "labels": dict(BOUNDARY_LABELS),
        "ttl_seconds": ttl_seconds(),
        "max_ttl_seconds": MAX_CONFIRMATION_TTL_SECONDS,
        "single_action_bound": True,
        "accepts_text_approval": False,
        "store": active.stats(),
    }


def _audit(action: str, confirmation: BoundaryConfirmation) -> None:
    """凭据签发/核销入审计（best-effort；不落 token 原文）"""
    try:
        from agent.audit.facade import audit
        audit.record(action, actor="guardrails.boundary_words",
                     subject=f"boundary:{confirmation.category or '-'}",
                     payload={**confirmation.to_public(),
                              "action_digest": confirmation.action_digest})
    except Exception as exc:  # noqa: BLE001
        logger.debug("边界凭据审计写入失败: %s", exc)


def _audit_blocked(verdict: BoundaryVerdict, *, actor: str) -> None:
    """边界拦截入审计 + 事件（best-effort）"""
    categories = [str(h.category) for h in verdict.hits]
    payload: Dict[str, Any] = {
        "categories": categories,
        "token_state": verdict.token_state,
        "actor": str(actor or "auto"),
        "text_approval_claimed": verdict.text_approval_claimed,
        "enforced": False,
    }
    try:
        from agent.audit.facade import audit
        audit.record("guardrails.boundary_blocked",
                     actor="guardrails.boundary_words",
                     subject=f"boundary:{','.join(categories) or '-'}",
                     payload=payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("边界拦截审计写入失败: %s", exc)


def _audit_text_approval(text: str, *, actor: str, hits: Sequence[BoundaryHit]) -> None:
    """记录"文本形式声称已批准"（**从不放行**，只留痕 + 告警）"""
    categories = [h.category for h in hits]
    logger.warning("检测到文本形式的「已批准」声明（不予采信，§5.7 机制 5）：actor=%s "
                   "categories=%s", actor, categories or "-")
    try:
        from agent.audit.facade import audit
        audit.record("guardrails.text_approval_rejected",
                     actor="guardrails.boundary_words",
                     subject=f"boundary:{','.join(categories) or 'unspecified'}",
                     payload={"actor": str(actor or "auto"), "categories": categories,
                              "accepted": False,
                              "reason": "§5.7 机制 5：不接受任何文本形式的「已批准」"})
    except Exception as exc:  # noqa: BLE001
        logger.debug("文本批准拒绝审计写入失败: %s", exc)


__all__ = [
    # 常量
    "ENV_ENABLED", "ENV_TTL_SECONDS", "DEFAULT_CONFIRMATION_TTL_SECONDS",
    "MAX_CONFIRMATION_TTL_SECONDS", "DEFAULT_MAX_TOKENS", "NEVER_AUTOMATED",
    "BOUNDARY_LABELS", "BOUNDARY_PATTERNS",
    "VERDICT_ALLOW", "VERDICT_NEED_CONFIRM", "VERDICT_REJECT",
    "TOKEN_OK", "TOKEN_MISSING", "TOKEN_UNKNOWN", "TOKEN_EXPIRED",
    "TOKEN_USED", "TOKEN_MISMATCH",
    # 异常与结果
    "BoundaryError", "ConfirmationRequiredError",
    "BoundaryHit", "BoundaryVerdict", "ConfirmationCheck",
    # 识别
    "BoundaryWord", "detect_boundary_words", "is_never_automated",
    "detect_text_approval",
    # 凭据
    "action_digest", "BoundaryConfirmation", "ConfirmationStore",
    "get_confirmation_store", "set_confirmation_store", "reset_confirmation_store",
    # 闸门
    "guard_execution", "boundary_state",
]
