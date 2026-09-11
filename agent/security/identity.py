"""身份层解析（S2-02 遗留 #1 / S2-03 遗留 #13 收口）

【Owner 裁定（2026-09-11，方案 A3「令牌 → 用户映射」）】
    1. 每个使用者分配独立令牌，配置表映射 token → actor 名；
    2. actor 解析顺序：**会话/映射表 → 头 → 降级 `ui:<addr>`**；
    3. 命中映射表 → ``identity_source=token_map``；未命中 → 仍降级并**标注 degraded**；
    4. **不做**完整 session 登录体系（A1）、**不信任**反代注入头（A2）；
    5. **actor 解析层须保持可替换**，以便企业侧（P5）平滑升级为 A1/A2。

【为什么要有这个模块】
    S2-02 的身份解析落在 `agent/audit/ui_middleware.py::resolve_ui_actor`（UI 写路由
    审计用），S2-03 的埋点沿用了同一降级口径。同一份「actor 是谁」的判定散落两处
    必然漂移。本模块是**唯一解析入口**：审计（S2-02）与埋点（S2-03）与审批（S4-01）
    都调它，`identity_source` 因而逐字一致（验收项 §四【S2-03 #13】）。

【配置（.env / 环境变量）】
    CP_UI_TOKENS         令牌映射表，`<token>:<name>` 以逗号分隔；支持
                         `sha256:<hex>:<name>` 指纹写法（配置不必存原文）。
                         例：`CP_UI_TOKENS=tokA:alice,tokB:bob`
    CP_UI_TOKENS_FILE    映射表文件路径（每行一条，`#` 开头为注释）；
                         环境变量表为空时读取。生产建议用文件 + 600 权限。
    CP_IDENTITY_RESOLVER 解析器实现名（默认 `token_map`；P5 可注册 A1/A2 实现）

【不易（空表回退）】
    映射表为空 / 全部非法 → **完全回退既有行为**（头 → Cookie → 令牌指纹 →
    `ui:<remote_addr>`）。新机制绝不导致后台无法审批（分发壳硬约束 4）。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from agent.security.actor_matrix import (
    ACTOR_HUMAN,
    infer_actor_type,
    normalize_actor_type,
)

logger = logging.getLogger("agent.security.identity")

# ════════════════════════════════════════════════════════════
#  身份来源口径（identity_source 的唯一取值来源）
# ════════════════════════════════════════════════════════════

#: 映射表命中（A3 权威来源；每使用者独立令牌）
SRC_TOKEN_MAP = "token_map"
#: 调用方显式声明的 actor（路由自身已知操作者）
SRC_EXPLICIT = "explicit"
#: 会话上下文（A3 会话绑定；审批面用）
SRC_SESSION = "session"
#: 反代/客户端注入头（A2 未采用：**不作为权威来源**，仅降级记录）
SRC_HEADER_PREFIX = "header:"
#: Cookie（既有降级链第 3 级）
SRC_COOKIE_PREFIX = "cookie:"
#: Bearer 令牌指纹（不落令牌原文）
SRC_BEARER_FINGERPRINT = "bearer_token_fingerprint"
#: 降级：`ui:<remote_addr>`（既有降级链末端）
SRC_REMOTE_ADDR = "remote_addr"
#: 降级：无任何身份线索
SRC_NO_IDENTITY = "degraded_no_identity"

#: **权威**来源（可直接用于 human 专属判定）；其余一律 degraded
AUTHORITATIVE_SOURCES: Tuple[str, ...] = (SRC_EXPLICIT, SRC_SESSION, SRC_TOKEN_MAP)

#: 权威度分级（审计/埋点统一披露口径）
AUTHORITY_AUTHORITATIVE = "authoritative"
AUTHORITY_DEGRADED = "degraded"

#: 链上字段名 —— **刻意不用 `identity_authority`**：
#: 统一审计门面的脱敏器按**键名**做敏感键启发式（键含 `auth` 即判为凭据类），
#: 实测 `identity_authority` 的值会被部分掩码成 `au****ve`，破坏可查询性。
#: 故链上用 `identity_tier`，而 Python 侧属性仍叫 `authority`（语义直观）。
FIELD_IDENTITY_SOURCE = "identity_source"
FIELD_IDENTITY_TIER = "identity_tier"
FIELD_IDENTITY_DEGRADED = "identity_degraded"
FIELD_ACTOR_TYPE = "actor_type"

_ENV_TOKENS = "CP_UI_TOKENS"
_ENV_TOKENS_FILE = "CP_UI_TOKENS_FILE"
#: 令牌指纹写法前缀（`sha256:<hex>:<name>`）
_FINGERPRINT_PREFIX = "sha256:"

#: 既有（S2-02）身份头与 Cookie 键——顺序即降级链顺序，**不得更改**（零回归）
ACTOR_HEADERS: Tuple[str, ...] = ("X-Audit-Actor", "X-User", "X-Username", "X-Operator")
COOKIE_KEYS: Tuple[str, ...] = ("user", "username", "login_user", "ui_user")


# ════════════════════════════════════════════════════════════
#  解析结果
# ════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ResolvedIdentity:
    """一次身份解析的完整结果（只含叶子字段，可 JSON 序列化）

    Attributes:
        actor: 操作者标识（映射表命中时是配置的 actor 名，绝不臆造）。
        identity_source: 来源口径（**与 S2-02/S2-03 逐字一致**）。
        actor_type: 由来源与 actor 名推断的执行体类型（human/auto/sub_agent）。
        scope: 该身份绑定的 scope（映射表可声明，缺省空）。
        session_id: 会话标识（审批面安全用）。
        degraded: 是否降级路径（非权威来源）。
        authority: `authoritative` / `degraded`（冗余但便于查询过滤）。
        token_fingerprint: 令牌指纹（**永不**含令牌原文）。
    """
    actor: str = ""
    identity_source: str = SRC_NO_IDENTITY
    actor_type: str = ACTOR_HUMAN
    scope: str = ""
    session_id: str = ""
    degraded: bool = True
    authority: str = AUTHORITY_DEGRADED
    token_fingerprint: str = ""

    def to_audit_fields(self) -> Dict[str, Any]:
        """审计/事件载荷叶子字段（S2-02 与 S2-03 共用，口径一致）

        键名见 `FIELD_*` 常量（`identity_tier` 而非 `identity_authority`，
        避免统一脱敏器的敏感键启发式误伤）。
        """
        return {
            FIELD_IDENTITY_SOURCE: self.identity_source,
            FIELD_IDENTITY_TIER: self.authority,
            FIELD_IDENTITY_DEGRADED: bool(self.degraded),
            FIELD_ACTOR_TYPE: self.actor_type,
        }


# ════════════════════════════════════════════════════════════
#  令牌指纹（不落原文）
# ════════════════════════════════════════════════════════════

def token_fingerprint(token: str) -> str:
    """令牌指纹（sha256 前 12 位；与 S2-02 `ui_middleware` 同形，可跨模块关联）"""
    return "tok_" + hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:12]


def _hash_token(token: str) -> str:
    """映射表指纹写法用的完整 sha256 hex（比对用，不落原文）"""
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


# ════════════════════════════════════════════════════════════
#  令牌映射表（A3）
# ════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TokenMapEntry:
    """一条 token → actor 映射

    Attributes:
        token: 令牌原文（配置表以 `sha256:` 写法给出时为 None，只有 fingerprint）。
        fingerprint: 令牌 sha256 hex（指纹写法时来自配置，原文写法时计算得到）。
        actor: actor 名（**唯一可信来源**，不做任何推断）。
        scope: 可选 scope 声明。
        actor_type: 可选执行体类型声明（缺省按 actor 名推断）。
    """
    actor: str
    fingerprint: str
    token: Optional[str] = None
    scope: str = ""
    actor_type: str = ""


class TokenMap:
    """令牌 → 用户映射表（A3 的配置载体）

    解析 `CP_UI_TOKENS`（一行式）与 `CP_UI_TOKENS_FILE`（每行一条）。
    非法行**跳过并计数**（不抛异常、不阻断服务启动）——配置写错不应导致审批
    面整体不可用（空表即回退既有行为）。
    """

    def __init__(self, raw: str = "", *, source: str = "") -> None:
        self.source = source or _ENV_TOKENS
        self.entries: List[TokenMapEntry] = []
        self.invalid_lines = 0
        for line in _split_entries(raw):
            entry = _parse_entry(line)
            if entry is None:
                self.invalid_lines += 1
                logger.warning("[Identity] 令牌映射行非法（已跳过）: %s", _mask_line(line))
                continue
            self.entries.append(entry)

    @property
    def empty(self) -> bool:
        return not self.entries

    def __len__(self) -> int:
        return len(self.entries)

    def lookup(self, token: str) -> Optional[TokenMapEntry]:
        """按令牌查 actor（常量时间比对指纹；未命中 → None）"""
        if not token or not self.entries:
            return None
        digest = _hash_token(token)
        for entry in self.entries:
            if secrets.compare_digest(digest, entry.fingerprint):
                return entry
        return None

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "TokenMap":
        """从环境变量/文件构建映射表（表为空 → 空 TokenMap）"""
        env = env if env is not None else os.environ
        raw = str(env.get(_ENV_TOKENS, "") or "").strip()
        if raw:
            return cls(raw, source=_ENV_TOKENS)
        path = str(env.get(_ENV_TOKENS_FILE, "") or "").strip()
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return cls(fh.read(), source=_ENV_TOKENS_FILE)
            except OSError as e:  # 读不到 → 回退既有行为（不阻断）
                logger.warning("[Identity] 映射表文件读取失败（回退既有行为）: %s", e)
        return cls("", source=_ENV_TOKENS)


def _split_entries(raw: str) -> List[str]:
    """拆分映射表条目（逗号或换行分隔；忽略 `#` 注释与空行）"""
    out: List[str] = []
    for chunk in str(raw or "").replace("\r", "\n").split("\n"):
        for part in chunk.split(","):
            item = part.strip()
            if not item or item.startswith("#"):
                continue
            out.append(item)
    return out


def _parse_entry(line: str) -> Optional[TokenMapEntry]:
    """解析一条映射（`<token>:<name>` 或 `sha256:<hex>:<name>`）

    扩展写法：`<token>:<name>[:<scope>[:<actor_type>]]`（后两段可选）。

    **格式约束**：令牌与 actor 名中**不得含 `:`**（`:` 是字段分隔符）——非法行会被
    跳过并计入 `invalid_lines`（跳过而非抛异常：配置写错不得阻断服务启动）。
    """
    item = str(line or "").strip()
    if not item:
        return None
    if item.lower().startswith(_FINGERPRINT_PREFIX):
        rest = item[len(_FINGERPRINT_PREFIX):]
        parts = rest.split(":")
        if len(parts) < 2:
            return None
        digest, actor = parts[0].strip(), parts[1].strip()
        if len(digest) != 64 or not all(c in "0123456789abcdefABCDEF" for c in digest):
            return None
        scope = parts[2].strip() if len(parts) > 2 else ""
        actor_type = parts[3].strip() if len(parts) > 3 else ""
        if not actor:
            return None
        return TokenMapEntry(actor=actor, fingerprint=digest.lower(), token=None,
                             scope=scope, actor_type=actor_type)
    parts = item.split(":")
    if len(parts) < 2:
        return None
    token, actor = parts[0].strip(), parts[1].strip()
    if not token or not actor:
        return None
    scope = parts[2].strip() if len(parts) > 2 else ""
    actor_type = parts[3].strip() if len(parts) > 3 else ""
    return TokenMapEntry(actor=actor, fingerprint=_hash_token(token), token=token,
                         scope=scope, actor_type=actor_type)


def _mask_line(line: str) -> str:
    """日志安全的映射行（令牌形态一律打码，绝不落原文）"""
    item = str(line or "")
    if item.lower().startswith(_FINGERPRINT_PREFIX):
        return item
    parts = item.split(":")
    if len(parts) >= 2:
        return f"{token_fingerprint(parts[0])}:{':'.join(parts[1:])}"
    return "***"


# ════════════════════════════════════════════════════════════
#  解析器（可替换：P5 → A1/A2）
# ════════════════════════════════════════════════════════════

#: 解析器签名：(TokenMap, 关键字参数) -> ResolvedIdentity
ResolverFn = Callable[..., ResolvedIdentity]


class IdentityResolver:
    """可替换的身份解析器（A3 默认实现）

    替换方式（P5 企业侧升级 A1 会话体系 / A2 可信反代头）：
        `set_resolver(MyResolver())` —— 只需实现 `resolve(**kwargs)` 并返回
        `ResolvedIdentity`，上层（审计 / 埋点 / 审批）零改动。
    """

    name = SRC_TOKEN_MAP

    def resolve(self, *, actor: str = "", token: str = "",
                headers: Optional[Mapping[str, str]] = None,
                cookies: Optional[Mapping[str, str]] = None,
                remote_addr: str = "", session_id: str = "",
                token_map: Optional[TokenMap] = None) -> ResolvedIdentity:
        """解析顺序：会话/映射表 → 头 → Cookie → 令牌指纹 → `ui:<addr>`"""
        tm = token_map if token_map is not None else current_token_map()
        hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        # 令牌取自显式入参或请求头（`Authorization: Bearer` / `X-API-Token`）
        raw_token = str(token or "").strip() or extract_bearer_token(hdrs)

        # ① 映射表（A3 权威）：命中即定 actor，来源 token_map
        if raw_token and tm is not None:
            entry = tm.lookup(raw_token)
            if entry is not None:
                at = _resolve_declared_type(entry.actor_type, entry.actor)
                return _make(actor=entry.actor, source=SRC_TOKEN_MAP, actor_type=at,
                             scope=entry.scope, session_id=session_id,
                             token_fingerprint=token_fingerprint(raw_token))
        # ② 显式 actor（调用方已知操作者）
        if actor:
            return _make(actor=str(actor), source=SRC_EXPLICIT,
                         actor_type=infer_actor_type(actor), session_id=session_id,
                         token_fingerprint=token_fingerprint(raw_token) if raw_token else "")
        # ③ 身份头（A2 未采用：仅降级记录，不作为权威来源）
        for name in ACTOR_HEADERS:
            val = hdrs.get(name.lower(), "").strip()
            if val:
                return _make(actor=val, source=f"{SRC_HEADER_PREFIX}{name}",
                             actor_type=infer_actor_type(val),
                             session_id=session_id)
        # ④ Cookie
        ck = cookies or {}
        for key in COOKIE_KEYS:
            val = str(ck.get(key) or "").strip()
            if val:
                return _make(actor=val, source=f"{SRC_COOKIE_PREFIX}{key}",
                             actor_type=infer_actor_type(val),
                             session_id=session_id)
        # ⑤ Bearer 令牌指纹（不落原文）
        auth = hdrs.get("authorization", "")
        if auth.lower().startswith("bearer "):
            bare = auth[7:].strip()
            if bare:
                return _make(actor=token_fingerprint(bare),
                             source=SRC_BEARER_FINGERPRINT, actor_type=ACTOR_HUMAN,
                             session_id=session_id,
                             token_fingerprint=token_fingerprint(bare))
        # ⑤' X-API-Token（无 Bearer 前缀时同口径降级为指纹）
        if raw_token:
            return _make(actor=token_fingerprint(raw_token),
                         source=SRC_BEARER_FINGERPRINT, actor_type=ACTOR_HUMAN,
                         session_id=session_id,
                         token_fingerprint=token_fingerprint(raw_token))
        # ⑥ 降级：ui:<remote_addr>
        if remote_addr:
            return _make(actor=f"ui:{remote_addr}", source=SRC_REMOTE_ADDR,
                         actor_type=ACTOR_HUMAN, session_id=session_id)
        # ⑦ 无任何线索
        return _make(actor="ui:unknown", source=SRC_NO_IDENTITY,
                     actor_type=ACTOR_HUMAN, session_id=session_id)


def _resolve_declared_type(declared: str, actor: str) -> str:
    """映射表声明的执行体类型（缺省按 actor 名推断）"""
    if declared:
        try:
            return normalize_actor_type(declared)
        except ValueError:
            logger.warning("[Identity] 映射表声明了未知执行体类型（按 actor 名推断）")
    return infer_actor_type(actor)


def _make(*, actor: str, source: str, actor_type: str, scope: str = "",
          session_id: str = "", token_fingerprint: str = "") -> ResolvedIdentity:
    degraded = source not in AUTHORITATIVE_SOURCES
    return ResolvedIdentity(
        actor=str(actor or ""), identity_source=source,
        actor_type=str(actor_type or ACTOR_HUMAN), scope=str(scope or ""),
        session_id=str(session_id or ""), degraded=bool(degraded),
        authority=AUTHORITY_DEGRADED if degraded else AUTHORITY_AUTHORITATIVE,
        token_fingerprint=str(token_fingerprint or ""))


# ── 进程级解析器与映射表（可注入、可复位） ──

_lock = threading.RLock()
_resolver: Optional[IdentityResolver] = None
_token_map: Optional[TokenMap] = None
_token_map_loaded = False


def set_resolver(resolver: Optional[IdentityResolver]) -> Optional[IdentityResolver]:
    """替换身份解析器（返回旧实现；None → 恢复 A3 默认实现）"""
    global _resolver
    with _lock:
        previous = _resolver
        _resolver = resolver
    return previous


def current_resolver() -> IdentityResolver:
    global _resolver
    with _lock:
        if _resolver is None:
            _resolver = IdentityResolver()
        return _resolver


def current_token_map() -> TokenMap:
    """进程级映射表（首次访问从环境加载；可经 `set_token_map` 注入/复位）"""
    global _token_map, _token_map_loaded
    with _lock:
        if not _token_map_loaded or _token_map is None:
            loaded = TokenMap.from_env()
            _token_map = loaded
            _token_map_loaded = True
            if loaded.empty:
                logger.info("[Identity] 令牌映射表为空 → 回退既有身份解析行为")
            else:
                logger.info("[Identity] 令牌映射表已加载 entries=%s source=%s",
                            len(loaded), loaded.source)
        return _token_map


def set_token_map(token_map: Optional[TokenMap]) -> None:
    """注入映射表（测试隔离 / 配置热更新）；传 None 使下次访问重新加载"""
    global _token_map, _token_map_loaded
    with _lock:
        _token_map = token_map
        _token_map_loaded = token_map is not None


def reset_identity() -> None:
    """复位解析器与映射表（测试隔离）"""
    global _resolver, _token_map, _token_map_loaded
    with _lock:
        _resolver = None
        _token_map = None
        _token_map_loaded = False


def resolve_identity(*, actor: str = "", token: str = "",
                     headers: Optional[Mapping[str, str]] = None,
                     cookies: Optional[Mapping[str, str]] = None,
                     remote_addr: str = "", session_id: str = "",
                     token_map: Optional[TokenMap] = None) -> ResolvedIdentity:
    """**唯一身份解析入口**（审计 / 埋点 / 审批共用，口径必然一致）"""
    try:
        return current_resolver().resolve(
            actor=actor, token=token, headers=headers, cookies=cookies,
            remote_addr=remote_addr, session_id=session_id, token_map=token_map)
    except Exception as e:  # noqa: BLE001 身份解析失败绝不阻断主流程 → 降级
        logger.warning("[Identity] 解析失败（降级为无身份）: %s", e)
        return _make(actor="ui:unknown", source=SRC_NO_IDENTITY, actor_type=ACTOR_HUMAN)


def extract_bearer_token(headers: Optional[Mapping[str, str]]) -> str:
    """从请求头取 Bearer 令牌原文（仅用于映射表比对，**不得落盘/落日志**）"""
    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    auth = hdrs.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return hdrs.get("x-api-token", "").strip()


__all__ = [
    "SRC_TOKEN_MAP", "SRC_EXPLICIT", "SRC_SESSION", "SRC_HEADER_PREFIX",
    "SRC_COOKIE_PREFIX", "SRC_BEARER_FINGERPRINT", "SRC_REMOTE_ADDR",
    "SRC_NO_IDENTITY", "AUTHORITATIVE_SOURCES", "AUTHORITY_AUTHORITATIVE",
    "AUTHORITY_DEGRADED", "ACTOR_HEADERS", "COOKIE_KEYS",
    "ResolvedIdentity", "TokenMap", "TokenMapEntry", "IdentityResolver",
    "resolve_identity", "token_fingerprint", "extract_bearer_token",
    "set_resolver", "current_resolver", "current_token_map", "set_token_map",
    "reset_identity",
]
