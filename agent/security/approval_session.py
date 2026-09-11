"""审批面安全（v7.2 §5.7⑦）

【任务定位】
    §5.7⑦ 的六条要求：**会话绑定**（审批 token 与登录会话强绑定，禁分享式链接）、
    **CSRF 保护**、**链接时效 ≤15 分钟**、**越权尝试 → 告警 + 审计**、
    **destructive 强制二次认证**、**审批按钮区 DOM 隔离**（前端，见
    `templates/approval_console.html` + `static/js/approval_console.js`）。

    前五条在本模块；越权的「告警 + 审计」由
    `agent/security/approval_guard.py::report_denial` 承担（本模块只**判定**）。

【三项机制（不易）】
    1. **会话绑定**：审批链接 token ≠ 凭据。token 服务端登记，绑定
       `(session_id, record_id, actor)`；兑换时必须携带**同一会话**，否则
       `session_mismatch` —— 把链接转发给别人（分享式链接）**不可用**。
    2. **时效**：默认 900s，**硬上限 900s**（配置写大也钳到 900，并告警）；
       过期 → `expired`，提示重新发起。token **一次性**（兑换即作废）。
    3. **二次认证**：destructive 审批必须凭**一次性确认码**（由服务端向
       **同一会话**签发）或配置口令（`CP_APPROVAL_SECOND_FACTOR_CODE`）通过
       校验，否则判定不通过（`second_factor_ok=False`）。

【重启即失效（fail-closed）】
    会话/链接/确认码全部**只在进程内**，不落盘：进程重启 ⇒ 链接全部失效，
    需重新发起。治理面宁可多一次人工发起，不可让陈旧链接继续有效。

【配置（.env / 环境变量）】
    CP_APPROVAL_LINK_TTL_SECONDS        链接时效秒，默认 900（硬上限 900）
    CP_APPROVAL_CSRF_ENABLED            CSRF 校验开关，默认 1
    CP_APPROVAL_SESSION_TTL_SECONDS     会话时效秒，默认 1800
    CP_APPROVAL_SECOND_FACTOR_CODE      二次认证口令（可选；缺省用一次性确认码）
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from agent.security import pii as pii_mod

logger = logging.getLogger("agent.security.approval_session")

# ── 结果码（稳定口径；路由与单测据此断言） ──
CHECK_OK = "ok"
CHECK_UNKNOWN = "unknown_token"
CHECK_EXPIRED = "expired"
CHECK_SESSION_MISMATCH = "session_mismatch"
CHECK_RECORD_MISMATCH = "record_mismatch"
CHECK_ALREADY_USED = "already_used"
CHECK_SESSION_EXPIRED = "session_expired"
CHECK_SESSION_UNKNOWN = "unknown_session"
CHECK_CSRF_MISMATCH = "csrf_mismatch"
CHECK_SECOND_FACTOR_REQUIRED = "second_factor_required"
CHECK_SECOND_FACTOR_INVALID = "second_factor_invalid"

#: 会话 Cookie 名（路由与前端共用）
SESSION_COOKIE_NAME = "cp_approval_session"
#: CSRF 头名（前端必须携带；双重提交 Cookie 模式）
CSRF_HEADER_NAME = "X-CSRF-Token"

#: ≤15 分钟（§5.7⑦ 的硬约束；**契约常量**，不随配置突破）
MAX_LINK_TTL_SECONDS = 900.0
DEFAULT_LINK_TTL_SECONDS = 900.0
DEFAULT_SESSION_TTL_SECONDS = 1800.0

_ENV_LINK_TTL = "CP_APPROVAL_LINK_TTL_SECONDS"
_ENV_SESSION_TTL = "CP_APPROVAL_SESSION_TTL_SECONDS"
_ENV_CSRF = "CP_APPROVAL_CSRF_ENABLED"
_ENV_SECOND_FACTOR_CODE = "CP_APPROVAL_SECOND_FACTOR_CODE"


def _env_flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("[ApprovalSession] %s 非法值（回退默认 %s）", name, default)
        return default


def link_ttl_seconds() -> float:
    """审批链接时效（秒）——**钳制到 ≤900**；非法/非正 → 默认 900"""
    raw = _env_float(_ENV_LINK_TTL, DEFAULT_LINK_TTL_SECONDS)
    if raw <= 0:
        logger.warning("[ApprovalSession] %s=%s 非正（回退默认 %s）",
                       _ENV_LINK_TTL, raw, DEFAULT_LINK_TTL_SECONDS)
        raw = DEFAULT_LINK_TTL_SECONDS
    if raw > MAX_LINK_TTL_SECONDS:
        logger.warning("[ApprovalSession] %s=%s 超过 §5.7⑦ 上限，已钳制为 %s 秒",
                       _ENV_LINK_TTL, raw, MAX_LINK_TTL_SECONDS)
        raw = MAX_LINK_TTL_SECONDS
    return float(raw)


def session_ttl_seconds() -> float:
    raw = _env_float(_ENV_SESSION_TTL, DEFAULT_SESSION_TTL_SECONDS)
    return float(raw) if raw > 0 else DEFAULT_SESSION_TTL_SECONDS


def csrf_enabled() -> bool:
    return _env_flag(_ENV_CSRF, "1")


def second_factor_passphrase() -> str:
    return str(os.getenv(_ENV_SECOND_FACTOR_CODE, "") or "").strip()


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════

@dataclass
class ApprovalSession:
    """审批会话（token 会话绑定的锚点；**进程内**，不落盘）

    Attributes:
        session_id: 会话标识（随机 32 hex；写 Cookie）。
        actor / actor_type / identity_source: 身份事实（来自身份层，不可由前端声明）。
        scope: 身份 scope。
        csrf_token: 双重提交 CSRF 令牌（服务端持有 + 下发 Cookie 各一份）。
        created_at / expires_at: 秒级墙钟。
        actor_ip: 来源 IP（**只在内存**，仅用于派生掩码/HMAC 入链）。
        authorized_capabilities: 授权子集（sub_agent 场景透传）。
    """
    session_id: str
    actor: str
    actor_type: str = "human"
    identity_source: str = ""
    scope: str = ""
    csrf_token: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    actor_ip: str = ""
    authorized_capabilities: frozenset = field(default_factory=frozenset)

    def expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at

    def ip_fields(self) -> Dict[str, Any]:
        return pii_mod.ip_pii_fields(self.actor_ip) if self.actor_ip else {}

    def to_public(self, now: Optional[float] = None) -> Dict[str, Any]:
        """对外可见字段（**不含** csrf_token / actor_ip / 内部时间戳精度）"""
        current = now if now is not None else time.time()
        return {
            "session_id": self.session_id,
            "actor": self.actor,
            "actor_type": self.actor_type,
            "identity_source": self.identity_source,
            "scope": self.scope,
            "expires_in": max(0, int(self.expires_at - current)),
        }


@dataclass
class ApprovalLink:
    """审批链接 token（绑定会话 + 记录；一次性）

    Attributes:
        token: 链接令牌（随机 43 字符；**只回给发起会话一次**）。
        session_id: 绑定会话（分享给他人即 `session_mismatch`）。
        record_id: 绑定审批记录。
        actor: 绑定操作者。
        risk: 对象风险等级（destructive ⇒ 需二次认证）。
        created_at / expires_at: 时效（≤900s）。
        used_at: 首次兑换时间（0 = 未使用）。
    """
    token: str
    session_id: str
    record_id: str
    actor: str
    risk: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    used_at: float = 0.0

    @property
    def destructive(self) -> bool:
        return str(self.risk or "").strip().lower() == "destructive"

    def to_public(self, now: Optional[float] = None) -> Dict[str, Any]:
        current = now if now is not None else time.time()
        return {
            "token": self.token,
            "record_id": self.record_id,
            "session_id": self.session_id,
            "risk": self.risk,
            "destructive": bool(self.destructive),
            "requires_second_factor": bool(self.destructive),
            "expires_in": max(0, int(self.expires_at - current)),
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class LinkCheck:
    """链接/会话/CSRF/二次认证的校验结果"""
    ok: bool
    code: str
    message: str = ""
    requires_second_factor: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": bool(self.ok), "code": self.code,
                "message": self.message,
                "requires_second_factor": bool(self.requires_second_factor)}


def _ok() -> LinkCheck:
    return LinkCheck(ok=True, code=CHECK_OK, message="ok")


def _fail(code: str, message: str, *, sf: bool = False) -> LinkCheck:
    return LinkCheck(ok=False, code=code, message=message,
                     requires_second_factor=sf)


# ════════════════════════════════════════════════════════════
#  会话 / 链接 / 确认码 存储
# ════════════════════════════════════════════════════════════

class ApprovalSessionStore:
    """审批会话与链接的进程内存储（线程安全；**可注入时钟**便于单测）

    Args:
        clock: 返回当前秒级墙钟的可调用；默认 `time.time`。
    """

    def __init__(self, clock: Optional[Any] = None) -> None:
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._sessions: Dict[str, ApprovalSession] = {}
        self._links: Dict[str, ApprovalLink] = {}
        self._challenges: Dict[str, Dict[str, Any]] = {}
        # 二次认证确认码的进程级密钥（重启即失效）
        self._secret = secrets.token_bytes(32)

    # ── 会话 ──

    def open_session(self, *, actor: str, actor_type: str = "human",
                     identity_source: str = "", scope: str = "",
                     actor_ip: str = "",
                     authorized_capabilities: frozenset = frozenset(),
                     ttl_seconds: Optional[float] = None,
                     session_id: str = "") -> ApprovalSession:
        """开启审批会话（返回含 csrf_token 的会话对象）"""
        now = float(self._clock())
        ttl = float(ttl_seconds) if ttl_seconds else session_ttl_seconds()
        session = ApprovalSession(
            session_id=session_id or secrets.token_hex(16),
            actor=str(actor or ""), actor_type=str(actor_type or "human"),
            identity_source=str(identity_source or ""), scope=str(scope or ""),
            csrf_token=secrets.token_urlsafe(24),
            created_at=now, expires_at=now + ttl, actor_ip=str(actor_ip or ""),
            authorized_capabilities=authorized_capabilities or frozenset())
        with self._lock:
            self._sessions[session.session_id] = session
            self._purge_locked(now)
        logger.info("[ApprovalSession] 会话已开启 actor=%s source=%s ttl=%ss",
                    session.actor, session.identity_source, int(ttl))
        return session

    def get_session(self, session_id: str, *,
                    now: Optional[float] = None) -> Optional[ApprovalSession]:
        with self._lock:
            session = self._sessions.get(str(session_id or ""))
        if session is None:
            return None
        if session.expired(now if now is not None else float(self._clock())):
            return None
        return session

    def close_session(self, session_id: str) -> bool:
        """关闭会话并作废其全部链接（登出/换会话 → 分享式链接失效）"""
        with self._lock:
            existed = self._sessions.pop(str(session_id or ""), None) is not None
            for token in [t for t, l in self._links.items()
                          if l.session_id == str(session_id or "")]:
                self._links.pop(token, None)
            for key in [k for k in self._challenges
                        if self._challenges[k].get("session_id") == str(session_id or "")]:
                self._challenges.pop(key, None)
        if existed:
            logger.info("[ApprovalSession] 会话已关闭（其审批链接全部作废）")
        return existed

    # ── 链接 ──

    def issue_link(self, *, session_id: str, record_id: str, actor: str = "",
                   risk: str = "", ttl_seconds: Optional[float] = None
                   ) -> ApprovalLink:
        """签发审批链接（token 一次性、绑定会话与记录、时效 ≤900s）"""
        now = float(self._clock())
        ttl = (link_ttl_seconds() if ttl_seconds is None
               else max(0.0, min(float(ttl_seconds), MAX_LINK_TTL_SECONDS)))
        link = ApprovalLink(
            token=secrets.token_urlsafe(32), session_id=str(session_id or ""),
            record_id=str(record_id or ""), actor=str(actor or ""),
            risk=str(risk or ""), created_at=now, expires_at=now + ttl)
        with self._lock:
            self._links[link.token] = link
            self._purge_locked(now)
        logger.info("[ApprovalSession] 审批链接已签发 record=%s ttl=%ss risk=%s",
                    link.record_id, int(ttl), link.risk or "-")
        return link

    def check_link(self, token: str, *, session_id: str, record_id: str = "",
                   now: Optional[float] = None) -> LinkCheck:
        """校验链接：会话绑定 → 时效 → 绑定记录 → 一次性

        **不消费** token（兑换与校验分离，便于「展示待确认页 → 二次认证 → 提交」
        的两段式流程）。真正消费在 `redeem_link`。
        """
        current = float(now if now is not None else self._clock())
        with self._lock:
            link = self._links.get(str(token or ""))
        if link is None:
            return _fail(CHECK_UNKNOWN, "审批链接无效或已失效（进程重启/已消费）")
        if link.used_at:
            return _fail(CHECK_ALREADY_USED, "审批链接已使用（一次性 token）")
        if link.session_id != str(session_id or ""):
            # 分享式链接：token 有效但会话不符 → 明确拒绝（§5.7⑦ 核心用例）
            return _fail(CHECK_SESSION_MISMATCH,
                         "审批链接与发起会话不匹配（禁止分享式链接）")
        if current >= link.expires_at:
            return _fail(CHECK_EXPIRED,
                         f"审批链接已过期（时效 {int(link.expires_at - link.created_at)}s，"
                         f"请重新发起）")
        if record_id and link.record_id != str(record_id):
            return _fail(CHECK_RECORD_MISMATCH, "审批链接与目标记录不匹配")
        session = self._sessions.get(link.session_id)
        if session is None or session.expired(current):
            return _fail(CHECK_SESSION_EXPIRED, "审批会话已过期（请重新登录/发起）")
        return LinkCheck(ok=True, code=CHECK_OK, message="ok",
                         requires_second_factor=bool(link.destructive))

    def redeem_link(self, token: str, *, session_id: str, record_id: str = "",
                    now: Optional[float] = None) -> LinkCheck:
        """兑换链接（校验通过后**立即作废**，防止重放）"""
        check = self.check_link(token, session_id=session_id,
                                record_id=record_id, now=now)
        if not check.ok:
            return check
        current = float(now if now is not None else self._clock())
        with self._lock:
            link = self._links.get(str(token or ""))
            if link is None or link.used_at:
                return _fail(CHECK_ALREADY_USED, "审批链接已使用（一次性 token）")
            link.used_at = current
        return check

    # ── CSRF ──

    def verify_csrf(self, session_id: str, token: str,
                    *, now: Optional[float] = None) -> LinkCheck:
        """双重提交 CSRF 校验（会话 Cookie + 请求头各自携带同一令牌）"""
        if not csrf_enabled():
            return _ok()
        session = self.get_session(session_id, now=now)
        if session is None:
            return _fail(CHECK_SESSION_UNKNOWN, "审批会话不存在或已过期")
        presented = str(token or "")
        if not presented or not secrets.compare_digest(presented, session.csrf_token):
            return _fail(CHECK_CSRF_MISMATCH, "CSRF 令牌缺失或不匹配")
        return _ok()

    # ── 二次认证（destructive 强制） ──

    def issue_second_factor(self, *, session_id: str, record_id: str,
                            now: Optional[float] = None) -> str:
        """向**同一会话**签发一次性确认码（destructive 审批专用）

        返回 6 位数字码；调用方（路由）只能回给该会话的持有者。
        """
        current = float(now if now is not None else self._clock())
        code = f"{secrets.randbelow(1_000_000):06d}"
        key = self._challenge_key(session_id, record_id)
        with self._lock:
            self._challenges[key] = {
                "code": code, "session_id": str(session_id or ""),
                "record_id": str(record_id or ""), "issued_at": current,
                "consumed": False,
            }
        logger.info("[ApprovalSession] 已签发二次认证确认码 record=%s", record_id)
        return code

    def verify_second_factor(self, *, session_id: str, record_id: str,
                             code: str, now: Optional[float] = None) -> LinkCheck:
        """校验二次认证（一次性确认码 **或** 配置口令）"""
        presented = str(code or "").strip()
        if not presented:
            return _fail(CHECK_SECOND_FACTOR_REQUIRED, "destructive 审批需二次认证",
                         sf=True)
        passphrase = second_factor_passphrase()
        if passphrase and secrets.compare_digest(presented, passphrase):
            return _ok()
        current = float(now if now is not None else self._clock())
        key = self._challenge_key(session_id, record_id)
        with self._lock:
            entry = self._challenges.get(key)
            if entry is None:
                return _fail(CHECK_SECOND_FACTOR_INVALID,
                             "二次认证确认码无效或未签发", sf=True)
            if entry.get("consumed"):
                return _fail(CHECK_SECOND_FACTOR_INVALID,
                             "二次认证确认码已使用（一次性）", sf=True)
            if current >= float(entry.get("issued_at", 0)) + MAX_LINK_TTL_SECONDS:
                return _fail(CHECK_EXPIRED, "二次认证确认码已过期", sf=True)
            if not secrets.compare_digest(presented, str(entry.get("code", ""))):
                return _fail(CHECK_SECOND_FACTOR_INVALID,
                             "二次认证确认码不匹配", sf=True)
            entry["consumed"] = True
        return _ok()

    # ── 内部 ──

    def _challenge_key(self, session_id: str, record_id: str) -> str:
        material = f"{session_id}|{record_id}".encode("utf-8")
        return hmac.new(self._secret, material, hashlib.sha256).hexdigest()

    def _purge_locked(self, now: float) -> None:
        """清理过期会话/链接/确认码（有界内存；调用方持锁）"""
        for key in [s for s, v in self._sessions.items() if v.expired(now)]:
            self._sessions.pop(key, None)
        for key in [t for t, l in self._links.items() if now >= l.expires_at]:
            self._links.pop(key, None)
        for key, entry in list(self._challenges.items()):
            if now >= float(entry.get("issued_at", 0)) + MAX_LINK_TTL_SECONDS:
                self._challenges.pop(key, None)

    def stats(self) -> Dict[str, Any]:
        now = float(self._clock())
        with self._lock:
            self._purge_locked(now)
            return {
                "sessions": len(self._sessions),
                "links": len(self._links),
                "pending_challenges": len(self._challenges),
                "link_ttl_seconds": link_ttl_seconds(),
                "max_link_ttl_seconds": MAX_LINK_TTL_SECONDS,
                "session_ttl_seconds": session_ttl_seconds(),
                "csrf_enabled": csrf_enabled(),
                "second_factor_passphrase_set": bool(second_factor_passphrase()),
            }

    def reset(self) -> None:
        """清空全部状态（测试隔离 / 进程收尾）"""
        with self._lock:
            self._sessions.clear()
            self._links.clear()
            self._challenges.clear()


# ── 进程级单例（路由用；测试经 `reset_approval_sessions()` 复位） ──

_store: Optional[ApprovalSessionStore] = None
_store_lock = threading.RLock()


def get_session_store() -> ApprovalSessionStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = ApprovalSessionStore()
        return _store


def set_session_store(store: Optional[ApprovalSessionStore]) -> Optional[ApprovalSessionStore]:
    global _store
    with _store_lock:
        previous = _store
        _store = store
    return previous


def reset_approval_sessions() -> None:
    global _store
    with _store_lock:
        if _store is not None:
            _store.reset()
        _store = None


__all__ = [
    "CHECK_OK", "CHECK_UNKNOWN", "CHECK_EXPIRED", "CHECK_SESSION_MISMATCH",
    "CHECK_RECORD_MISMATCH", "CHECK_ALREADY_USED", "CHECK_SESSION_EXPIRED",
    "CHECK_SESSION_UNKNOWN", "CHECK_CSRF_MISMATCH", "CHECK_SECOND_FACTOR_REQUIRED",
    "CHECK_SECOND_FACTOR_INVALID", "SESSION_COOKIE_NAME", "CSRF_HEADER_NAME",
    "MAX_LINK_TTL_SECONDS", "DEFAULT_LINK_TTL_SECONDS",
    "DEFAULT_SESSION_TTL_SECONDS",
    "ApprovalSession", "ApprovalLink", "LinkCheck", "ApprovalSessionStore",
    "link_ttl_seconds", "session_ttl_seconds", "csrf_enabled",
    "second_factor_passphrase", "get_session_store", "set_session_store",
    "reset_approval_sessions",
]
