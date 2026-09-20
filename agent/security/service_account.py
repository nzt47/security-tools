"""服务账号（`service_account`）—— **第四类主体的凭据与预授权机制**（TASK-06 §3 第 1 步）

## 存在的理由

TASK-06 证实的四个真实缺陷之一：

> **非交互（cron/CI）调用高危工具会"挂空单"永远拿不到结果** ⇒ 建立
> `service_account` 身份 + 预授权机制。

在 `service_account` 之前，仓库只有 `human` / `auto` / `sub_agent` 三类主体
（`agent/security/actor_matrix.py`），而**唯一一把共享令牌** `FLASK_API_TOKEN`
既不区分使用者、也没有 TTL/轮换/吊销。于是"CI 要调一个 L2 工具"这件事无解：
非交互来源下没人能批准，调用只能挂空单。

## 边界（**本模块做什么、不做什么**）

| 做 | 不做 |
|---|---|
| 签发/校验/吊销带签名与 `scope` 的 SA 令牌 | 不引入 Vault / KMS / 外部 IdP（D3） |
| 进程内注册表 + **加密**凭据落盘（`cryptography` 已在依赖里） | 不接入 `agent/multi_tenant.py`（越界，见 TASK-06 §5） |
| 把 SA 的 `scope` 交给**闸门**做预授权判定 | **不绕过** `tool_gate`（预授权是闸门内的一条判定） |

## 三条不可让步的铁律

1. **SA 不继承创建者权限**（v1.4 §10.1）。
   `create_service_account(created_by="admin", ...)` 里的 `created_by` **只进审计**，
   **不参与任何授权判定** —— 权限**全部**来自 `scope` 参数。
   `tests/unit/test_service_account.py::TestNoPermissionInheritance` 是这条的负例证明：
   以 admin 身份创建一个 scope 为空的 SA，用该 SA 调 admin 专属能力**必须被拒**。
2. **`sub` 带前缀**。令牌的 `sub` 一律 `sa:<name>`，便于与 `user:` / `system:` /
   `llm:` 区分（v1.4 §10.1），也便于 `actor_matrix.infer_actor_type` 由 actor 名
   反推主体类型（`_SERVICE_ACCOUNT_NAME_PREFIXES`）。
3. **吊销必须秒级生效**。`jti` 黑名单在**进程内**（`_REVOKED_JTIS`）先行判定，
   落盘只是持久化 ⇒ `revoke()` 之后的下一次校验立刻失败，**不等文件 IO**。

## 签名方案（**为什么用 HMAC-SHA256 而不是 ed25519**）

TASK-06 §3 第 1 步第 3 项允许"HMAC 或 ed25519（ed25519 已有先例）"。选 **HMAC**：

- **对称密钥就够**：SA 令牌的签发方与校验方**都是本进程**（单机单用户部署，
  见 TASK-00 §0.2），不存在"第三方需要独立验证签名"的场景 —— 那才是非对称签名的价值。
- **不引入新依赖**：`hmac`/`hashlib` 是标准库；ed25519 需要 `cryptography` 的
  `Ed25519PrivateKey`（可用，但签发/校验/密钥序列化的代码量明显更大）。
  仓库里 ed25519 的先例（`agent/audit/chain.py::RootsSigner`）服务的是
  **跨进程长期可验证**的审计根，与"进程内会话期凭据"的威胁模型不同。
- **可换**：`_sign()` / `_verify_sig()` 是本模块仅有的两处签名原语，
  将来真要换 ed25519 只需改这两处（`alg` 字段已在 payload 里预留）。

## 与闸门的接线（**预授权不是旁路**）

`preauthorize(capability, identity, args, level)` 注册进
`agent/tool_gate.py::set_preauthorization_hook()`。闸门在 L1/L2/L3 分支上调用它；
返回 True 才放行，并且**仍然**走闸门、**仍然**落审计（`decision=preauthorized`）。
`install_gate_hook()` 是显式的安装入口（**不**在 import 时自动安装：导入期做副作用
会让"只想读个常量"的调用方也改动全局状态）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "SA_SUB_PREFIX", "SA_TOKEN_PREFIX", "SA_ISSUER", "SA_AUDIENCE",
    "SA_DEFAULT_TTL_SEC", "SA_MAX_TTL_SEC",
    "ServiceAccountError", "SATokenError", "SARevokedError", "SAScopeError",
    "ServiceAccount", "SATokenClaims", "VerifiedSA",
    "create_service_account", "issue_token", "verify_token", "revoke",
    "is_revoked", "list_service_accounts", "get_service_account",
    "reset_registry", "preauthorize", "install_gate_hook",
    "enter_service_account", "current_service_account",
    "scope_allows", "ACCOUNTS_PATH_ENV", "KEY_ENV",
]

#: `sub` 前缀（v1.4 §10.1：区分 `sa:` / `user:` / `system:` / `llm:`）
SA_SUB_PREFIX = "sa:"
#: 令牌字面量前缀（便于人眼识别与日志脱敏识别）
SA_TOKEN_PREFIX = "sa1"
#: 签发者 / 受众（写进 payload，供将来多环境共存时区分）
SA_ISSUER = "yunshu"
SA_AUDIENCE = "yunshu.service_account"

#: 默认有效期：90 天轮换（v1.4 §10.1）
SA_DEFAULT_TTL_SEC = 90 * 24 * 3600
#: 有效期上限（**防止签发时误传一个几年的时间戳**，使吊销成为唯一出口）
SA_MAX_TTL_SEC = 365 * 24 * 3600

#: 凭据文件路径开关（D5：已登记 `agent/settings/registry.py`）
ACCOUNTS_PATH_ENV = "CP_SERVICE_ACCOUNTS_PATH"
#: HMAC 签名密钥的环境变量名（**沿用仓库既有的 `Yunshu_ENCRYPT_KEY` 家族**：
#: 不新增第二个"根密钥"概念 —— 密钥越多，越容易有一个没人轮换）
KEY_ENV = "CP_SERVICE_ACCOUNT_KEY"
_DEFAULT_ACCOUNTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "service_accounts.json")


# ════════════════════════════════════════════════════════════
#  异常
# ════════════════════════════════════════════════════════════

class ServiceAccountError(Exception):
    """服务账号域的错误基类"""


class SATokenError(ServiceAccountError):
    """令牌不可用（格式错 / 签名不符 / 过期 / 受众不符）"""


class SARevokedError(SATokenError):
    """令牌已被**吊销**（与"过期"区分：过期是自然到期，吊销是人工即时失效）"""


class SAScopeError(ServiceAccountError):
    """scope 声明非法（例如 max_confirm_level 不在 L0–L3 内）"""


# ════════════════════════════════════════════════════════════
#  取值域
# ════════════════════════════════════════════════════════════

#: 允许的最高确认级别（与 `agent/lines/models.py::CONFIRM_LEVELS` 同值域）
CONFIRM_LEVELS: Tuple[str, ...] = ("L0", "L1", "L2", "L3")


def _norm_level(value: Any) -> str:
    """规范化确认级别（非法值 ⇒ 抛错，**不静默降级**）

    【为什么非法值抛错而不是回落到最宽】scope 里的 `max_confirm_level` 是
    **授权声明**。写错了却回落到 L0（最宽）会让一次笔误变成"预授权全部高危动作"。
    fail-closed 的方向在这里是"拒绝签发"，不是"给个默认值"。
    """
    text = str(value or "").strip().upper()
    if text not in CONFIRM_LEVELS:
        raise SAScopeError(
            f"max_confirm_level 非法: {value!r}（允许: {', '.join(CONFIRM_LEVELS)}）")
    return text


# ════════════════════════════════════════════════════════════
#  scope
# ════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SAScope:
    """SA 的授权范围（**权限的唯一来源**，见模块 docstring 铁律 1）

    Attributes:
        capabilities: 允许调用的能力名集合。支持两种写法：
            · 精确名（`write_file`）—— **L3 只接受这一种**（见 `scope_allows`）；
            · 命名空间通配（`yunshu:*`、`*`）—— 仅对 L0–L2 有效。
        max_confirm_level: 允许通过的**最高**确认级别（L0–L3）。
        resource_filter: 资源过滤声明（如 `{"path_prefix": "/tmp"}`）。
            本任务只**声明并留痕**，不做资源级强制（仓库当前无资源级策略引擎）——
            如实标注而不是假装它已生效。
        namespaces: 允许的命名空间集合（缺省 = 从 `capabilities` 的通配里推）。
    """
    capabilities: FrozenSet[str] = frozenset()
    max_confirm_level: str = "L0"
    resource_filter: Dict[str, Any] = field(default_factory=dict)
    namespaces: FrozenSet[str] = frozenset()

    def __post_init__(self) -> None:
        # 校验放在构造期：非法 scope 不该等到"用的时候"才被发现
        _norm_level(self.max_confirm_level)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capabilities": sorted(self.capabilities),
            "max_confirm_level": self.max_confirm_level,
            "resource_filter": dict(self.resource_filter),
            "namespaces": sorted(self.namespaces),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "SAScope":
        if not isinstance(raw, dict):
            return cls()
        caps = raw.get("capabilities") or ()
        if isinstance(caps, str):
            caps = [caps]
        nss = raw.get("namespaces") or ()
        if isinstance(nss, str):
            nss = [nss]
        return cls(
            capabilities=frozenset(str(c).strip() for c in caps if str(c).strip()),
            max_confirm_level=str(raw.get("max_confirm_level") or "L0").strip().upper(),
            resource_filter=dict(raw.get("resource_filter") or {}),
            namespaces=frozenset(str(n).strip() for n in nss if str(n).strip()),
        )


def scope_allows(scope: SAScope, capability: str, level: str) -> bool:
    """`scope` 是否允许以 `level` 执行 `capability`（**预授权的核心判定**）

    判定两条**都**要满足：

    1. **级别**：`level <= scope.max_confirm_level`（序号比较）。
       这是 TASK-06 §3 第 3 步第 2 项"SA 的 token 里带 `scope`，声明允许的能力集合
       与**最高 confirm_level**"的直接落地。
    2. **能力**：命中 `scope.capabilities`。命中规则对 **L3 与非 L3 不同**：
       · `level != "L3"` ⇒ 精确名**或**命名空间通配（`yunshu:*` / `*`）均可；
       · `level == "L3"` ⇒ **只接受精确名**。

    【为什么 L3 不接受通配】L3 = 会改变云枢自身能力集或造成不可逆后果
    （`shell_execute` / `generate_tool` / `ext_install` …）。若允许 `*` 一次性
    覆盖它们，那么"预授权"就退化成"给 SA 一把万能钥匙"—— 那与"L3 须**显式**
    预授权"直接矛盾。要求逐条点名，代价是一行配置，收益是爆炸半径可控。

    【为什么不做资源级强制】`resource_filter` 已声明但本任务无资源级策略引擎，
    故如实返回"This 声明未强制"由调用方在审计里留痕，而不是假装拦住了。
    """
    if str(level or "").strip().upper() not in CONFIRM_LEVELS:
        return False
    want = CONFIRM_LEVELS.index(str(level).strip().upper())
    have = CONFIRM_LEVELS.index(_norm_level(scope.max_confirm_level))
    if want > have:
        return False

    name = str(capability or "").strip()
    if not name:
        return False
    if name in scope.capabilities:
        return True
    if str(level).strip().upper() == "L3":
        return False                     # L3 只认精确名（见上）
    # 命名空间通配：`yunshu:*` 与 `*`
    for pattern in scope.capabilities:
        if pattern == "*":
            return True
        if pattern.endswith(":*") and pattern[:-1]:      # `yunshu:*` → 前缀 `yunshu:`
            prefix = pattern[:-1]
            if name.startswith(prefix) or prefix.rstrip(":") in scope.namespaces:
                return True
    return False


# ════════════════════════════════════════════════════════════
#  账号与令牌
# ════════════════════════════════════════════════════════════

@dataclass
class ServiceAccount:
    """一个服务账号（**不含任何权限**；权限全在 `scope`）"""
    name: str
    scope: SAScope = field(default_factory=SAScope)
    tenant_id: str = "default"
    #: 创建者 —— **只进审计，不参与授权**（铁律 1）
    created_by: str = ""
    created_at: float = 0.0
    enabled: bool = True
    description: str = ""

    @property
    def sub(self) -> str:
        """令牌主体标识（带 `sa:` 前缀）"""
        return f"{SA_SUB_PREFIX}{self.name}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "sub": self.sub,
            "scope": self.scope.to_dict(), "tenant_id": self.tenant_id,
            "created_by": self.created_by, "created_at": self.created_at,
            "enabled": bool(self.enabled), "description": self.description,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ServiceAccount":
        return cls(
            name=str(raw.get("name") or ""),
            scope=SAScope.from_dict(raw.get("scope")),
            tenant_id=str(raw.get("tenant_id") or "default"),
            created_by=str(raw.get("created_by") or ""),
            created_at=float(raw.get("created_at") or 0.0),
            enabled=bool(raw.get("enabled", True)),
            description=str(raw.get("description") or ""),
        )


@dataclass(frozen=True)
class SATokenClaims:
    """已验证的令牌载荷（v1.4 §10.1 的字段纪律，一字不缺）

    `sub` / `tenant_id` / `scope` / `resource_filter` / `jti` / `exp` / `iat` /
    `aud` / `iss` —— 九个字段全部落在 :meth:`to_dict` 的叶子上（**只放标识，
    不放原文**，与 `agent/security/identity.py::to_audit_fields` 同一纪律）。
    """
    sub: str
    tenant_id: str
    scope: SAScope
    resource_filter: Dict[str, Any]
    jti: str
    exp: float
    iat: float
    aud: str
    iss: str
    alg: str = "HS256"
    account: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sub": self.sub, "tenant_id": self.tenant_id,
            "scope": self.scope.to_dict(), "resource_filter": dict(self.resource_filter),
            "jti": self.jti, "exp": self.exp, "iat": self.iat,
            "aud": self.aud, "iss": self.iss, "alg": self.alg,
            "account": self.account,
        }

    def to_audit_fields(self) -> Dict[str, Any]:
        """进审计链的叶子字段（**不含**令牌原文与签名）"""
        return {
            "sub": self.sub, "jti": self.jti, "tenant_id": self.tenant_id,
            "max_confirm_level": self.scope.max_confirm_level,
            "iss": self.iss, "aud": self.aud,
        }


@dataclass(frozen=True)
class VerifiedSA:
    """一次成功校验的结果（供 `enter_service_account` 的调用方取用）"""
    claims: SATokenClaims
    identity: str = "service_account"

    @property
    def actor(self) -> str:
        return self.claims.sub

    @property
    def scope(self) -> SAScope:
        return self.claims.scope


# ════════════════════════════════════════════════════════════
#  签名密钥
# ════════════════════════════════════════════════════════════

_KEY_LOCK = threading.Lock()
_SIGN_KEY: Optional[bytes] = None


def _sign_key() -> bytes:
    """取 HMAC 签名密钥（环境变量优先；缺省按"进程内生成 + 明确告警"处理）

    【为什么缺省不是"拒绝签发"】与 `agent/server_auth.py:96-98` 的
    "未配置令牌直接放行"是**不同**的情形：那里缺配置等于"谁都能进"（我们把那条
    缺陷的处置另行登记，见 `docs/rfc/鉴权迁移.md`）；这里缺密钥只是"本次进程内
    签发的令牌在重启后失效"，**不放宽任何权限**。故按可用性优先处理，并留下
    WARNING（不静默）。
    """
    global _SIGN_KEY
    if _SIGN_KEY is not None:
        return _SIGN_KEY
    with _KEY_LOCK:
        if _SIGN_KEY is not None:
            return _SIGN_KEY
        raw = os.environ.get(KEY_ENV) or ""
        if raw.strip():
            _SIGN_KEY = str(raw).strip().encode("utf-8")
        else:
            _SIGN_KEY = secrets.token_bytes(32)
            logger.warning(
                "[service_account] 未配置 %s ⇒ 已为**本进程**生成临时签名密钥："
                "本次签发的 SA 令牌在进程重启后一律失效（不影响任何权限判定）。"
                "生产使用请显式配置该变量。", KEY_ENV)
        return _SIGN_KEY


def _reset_sign_key() -> None:
    """清空签名密钥缓存（**仅测试用**：验证"密钥不同 ⇒ 签名校验失败"）"""
    global _SIGN_KEY
    with _KEY_LOCK:
        _SIGN_KEY = None


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _canonical(payload: Dict[str, Any]) -> bytes:
    """规范化 JSON（`sort_keys` + 紧凑分隔符）—— 签名必须对**同一串字节**计算"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _sign(body: bytes) -> str:
    return _b64e(hmac.new(_sign_key(), body, hashlib.sha256).digest())


def _verify_sig(body: bytes, sig: str) -> bool:
    """常量时间比对（`compare_digest`，与 `server_auth.py:88` 同纪律）"""
    try:
        return hmac.compare_digest(_sign(body), str(sig or ""))
    except Exception:  # noqa: BLE001  签名不可计算 ⇒ 一律不通过
        return False


# ════════════════════════════════════════════════════════════
#  注册表（进程内 + 加密落盘）
# ════════════════════════════════════════════════════════════

_REG_LOCK = threading.RLock()
_ACCOUNTS: Dict[str, ServiceAccount] = {}
#: 已吊销的 `jti`（**进程内先行判定** ⇒ 吊销秒级生效，不等文件 IO）
_REVOKED_JTIS: set = set()
#: 签发过的 jti → (account, exp)（用于"按账号吊销全部令牌"与盘点）
_ISSUED: Dict[str, Tuple[str, float]] = {}
_LOADED = False


def _accounts_path() -> str:
    return str(os.environ.get(ACCOUNTS_PATH_ENV) or "").strip() or _DEFAULT_ACCOUNTS_PATH


def _load_accounts() -> None:
    """惰性加载账号（**只读一次**；文件不存在 = 正常初始状态，不报错）

    【D6】本函数**只读**；落盘只在 `create_service_account` / `revoke` 时发生，
    且路径由 `CP_SERVICE_ACCOUNTS_PATH` 控制（测试用临时目录）。

    【不易·信封格式与 `_save_accounts` 必须严格对称】`_save_accounts` 写的是
    `{"encrypted": bool, "payload": <密文串 | 对象>}`。第一版这里直接
    `doc.get("accounts")` —— 于是**加密落盘之后读回来永远是空注册表**
    （键名对不上，而 `FileNotFoundError` 分支掩盖了它：不报错、只是什么都读不到）。
    实测抓到（往返测试一条不过）。故读取必须走同一个信封。
    """
    global _LOADED
    if _LOADED:
        return
    with _REG_LOCK:
        if _LOADED:
            return
        path = _accounts_path()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                envelope = json.load(fh)
            doc = _decode_envelope(envelope)
            for raw in (doc.get("accounts") or []):
                acct = ServiceAccount.from_dict(raw)
                if acct.name:
                    _ACCOUNTS[acct.name] = acct
            for jti in (doc.get("revoked_jtis") or []):
                _REVOKED_JTIS.add(str(jti))
            for entry in (doc.get("issued") or []):
                if isinstance(entry, list) and len(entry) >= 2:
                    _ISSUED[str(entry[0])] = (str(entry[1]), float(entry[2] or 0.0))
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001  损坏的凭据文件不得让平台起不来（D4）
            logger.error("[service_account] 凭据文件读取失败（按空注册表继续，"
                         "即 SA 预授权全部失效 —— 这是**更严**的一侧）: %s: %s",
                         type(e).__name__, e)
        _LOADED = True


def _decode_envelope(envelope: Any) -> Dict[str, Any]:
    """解出凭据信封里的明文对象

    兼容三种形态，便于人工排查与历史文件：
      1. `{"encrypted": true, "payload": "<Fernet 密文>"}` —— 正常加密落盘；
      2. `{"encrypted": false, "payload": {...}}` —— 加密器不可用时的明文退化；
      3. `{"accounts": [...]}` —— **裸对象**（更早的手写/调试文件）。
    """
    if not isinstance(envelope, dict):
        raise ServiceAccountError("凭据文件顶层必须是 JSON 对象")
    if "accounts" in envelope and "payload" not in envelope:
        return envelope
    payload = envelope.get("payload")
    if not envelope.get("encrypted"):
        return payload if isinstance(payload, dict) else {}
    cipher = _encryptor()
    if cipher is None:
        raise ServiceAccountError(
            "凭据文件是加密的，但当前进程没有可用的加密器（Fernet 不可用）")
    text = cipher.decrypt_string(str(payload))
    doc = json.loads(text)
    if not isinstance(doc, dict):
        raise ServiceAccountError("凭据文件解密后不是 JSON 对象")
    return doc


def _save_accounts() -> None:
    """落盘（**加密**：凭据里的 `scope` 与 `created_by` 属敏感信息）

    加密用 `cryptography` 的 Fernet（`agent/security_utils.py:21-24` 已是既有依赖，
    D3 不引入新依赖）。密钥沿用 `LogEncryptor` 的 `Yunshu_ENCRYPT_KEY` 家族 ——
    不再引入第二个"根密钥"概念。
    """
    path = _accounts_path()
    payload = {
        "version": 1,
        "accounts": [a.to_dict() for a in sorted(_ACCOUNTS.values(),
                                                key=lambda x: x.name)],
        "revoked_jtis": sorted(_REVOKED_JTIS),
        "issued": [[j, v[0], v[1]] for j, v in sorted(_ISSUED.items())],
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    cipher = _encryptor()
    if cipher is not None:
        envelope: Dict[str, Any] = {"encrypted": True,
                                   "payload": cipher.encrypt_string(text)}
    else:
        envelope = {"encrypted": False, "payload": payload}
    body = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001  落盘失败不得让调用方崩（内存注册表仍有效）
        logger.error("[service_account] 凭据落盘失败（内存注册表仍生效）: %s: %s",
                     type(e).__name__, e)


#: 凭据文件**加密**密钥环境变量
#
# 【不易·为什么不用仓库既有的 `Yunshu_ENCRYPT_KEY`】两点实测原因：
#   ① `scripts/scan_settings.py` 的 `_DYNAMIC_NAME_PATTERN` 是 `^[A-Z][A-Z0-9_]*$`，
#      而 `Yunshu_ENCRYPT_KEY` 含小写（`unshu`）⇒ 常量无法被解析 ⇒ 该读取点会变成
#      一个 `<unresolved>` 动态家族 ⇒ `test_settings_registry.py` 的**零缺口守卫变红**
#      （实测：25 passed / 2 failed，diff 里正是 `<unresolved>`）。
#   ② `LogEncryptor` 对它的解析是 `Fernet(urlsafe_b64decode(值))`，即要求放
#      **"32 字节再 base64 一次"** 的值；用户按标准用法放 `Fernet.generate_key()`
#      的产物会静默退化成明文落盘（见 `_FileCipher` 的 docstring）。
#   ⇒ 本模块自持一个 `CP_` 前缀、全大写的密钥变量，并把它登记进设置注册表（D5）。
ENC_KEY_ENV = "CP_SERVICE_ACCOUNT_ENCRYPT_KEY"


class _FileCipher:
    """凭据文件的加解密（Fernet；**只在本模块用**，且密钥解析容错）

    【不易·为什么不直接用 `agent/security_utils.py::LogEncryptor`】实测：
    `LogEncryptor._load_or_generate_key` 做的是
    `Fernet(base64.urlsafe_b64decode(env))`，即它要求环境变量里放
    **"32 字节再 base64 一次"** 的值。而 `Fernet.generate_key()` 的产物
    （标准用法、也是本模块与用户最可能放进去的值）会被解成 32 个**原始字节**
    ⇒ `Fernet(raw)` 抛 `ValueError: Fernet key must be 32 url-safe base64-encoded bytes`
    ⇒ `LogEncryptor._cipher` 为 None ⇒ **静默退化成明文落盘**。
    实测抓到（本模块第一版即如此：`encrypted: false`，而日志只有一行 warning）。

    这里同时接受两种写法（标准 Fernet key 优先，再退化到 LogEncryptor 的双重编码），
    于是"用户按标准用法配置"与"沿用仓库旧约定配置"**都能真的加密**。
    """
    __slots__ = ("_fernet",)

    def __init__(self, key: bytes) -> None:
        from cryptography.fernet import Fernet  # noqa: PLC0415 惰性：只在写盘时用
        self._fernet = Fernet(key)

    def encrypt_string(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt_string(self, ciphertext: str) -> str:
        return self._fernet.decrypt(str(ciphertext).encode("ascii")).decode("utf-8")


def _parse_enc_key(raw: str) -> bytes:
    """把环境变量里的密钥解析成 `Fernet` 接受的 44 字节 url-safe base64

    接受两种写法：
      ① 标准 `Fernet.generate_key()` 的值（44 字符，本身已是合法 Fernet key）；
      ② 仓库 `LogEncryptor` 旧约定：其值 = `urlsafe_b64encode(①)`（双重编码）。
    """
    text = str(raw or "").strip()
    if not text:
        raise ValueError("空密钥")
    body = text.encode("ascii")
    # ① 先按标准 Fernet key 试（长度为 44 且能被 Fernet 接受）
    if len(body) == 44:
        return body
    # ② 再按"双重编码"解一层
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            decoded = decoder(text)
        except Exception:  # noqa: BLE001
            continue
        if len(decoded) == 44:
            return decoded
    raise ValueError("无法识别的加密密钥格式（既非标准 Fernet key，也非其 base64）")


def _encryptor() -> Any:
    """取凭据文件加密器（不可用 ⇒ 返回 ``None``，落盘退化为明文并**明确标记**）

    【为什么密钥缺失时退化而不是拒绝】凭据文件是**可重建**的（`create_service_account`
    能重新签发），故"没配密钥"的代价是"不能持久化"，不是"权限被放宽"。
    但**绝不静默**：退化路径会打 WARNING，且落盘信封里 `encrypted=false` 是显式标记
    （人工排查时一眼可见，不必猜）。
    """
    try:
        raw = os.environ.get(ENC_KEY_ENV) or ""
        if not raw.strip():
            logger.warning("[service_account] 未配置 %s ⇒ 凭据将以**明文**落盘"
                           "（不影响任何权限判定；配置该变量即启用加密）", ENC_KEY_ENV)
            return None
        return _FileCipher(_parse_enc_key(raw))
    except Exception as e:  # noqa: BLE001  cryptography 不可用/密钥非法 ⇒ 明文退化
        logger.warning("[service_account] 加密器不可用，凭据将明文落盘: %s: %s",
                       type(e).__name__, e)
        return None


# ════════════════════════════════════════════════════════════
#  签发
# ════════════════════════════════════════════════════════════

def create_service_account(name: str, *, scope: Optional[SAScope] = None,
                           tenant_id: str = "default", created_by: str = "",
                           description: str = "") -> ServiceAccount:
    """创建（或幂等更新）一个服务账号

    【铁律 1 —— SA 不继承创建者权限】
      `created_by` **只写进审计字段**，本函数**不做任何**"按创建者权限推导 scope"
      的逻辑。`scope` 缺省是一个**空能力集合 + L0** 的 scope（即什么都调不了）。
      这是刻意的：要让"继承创建者权限"成为**不可能**，而不是"要注意别写"。
      `tests/unit/test_service_account.py::TestNoPermissionInheritance` 用
      "admin 创建的 SA 调 admin 能力被拒"证明这条。
    """
    key = str(name or "").strip()
    if not key:
        raise ServiceAccountError("服务账号名不能为空")
    _load_accounts()
    acct = ServiceAccount(
        name=key, scope=scope if scope is not None else SAScope(),
        tenant_id=str(tenant_id or "default"), created_by=str(created_by or ""),
        created_at=time.time(), enabled=True, description=str(description or ""))
    with _REG_LOCK:
        _ACCOUNTS[key] = acct
        _save_accounts()
    logger.info("[service_account] 已创建服务账号 %s（scope 能力数=%d，"
                "最高级别=%s；创建者 %r **不参与授权**）",
                key, len(acct.scope.capabilities), acct.scope.max_confirm_level,
                acct.created_by or "<未声明>")
    return acct


def get_service_account(name: str) -> Optional[ServiceAccount]:
    _load_accounts()
    with _REG_LOCK:
        return _ACCOUNTS.get(str(name or "").strip())


def list_service_accounts() -> List[Dict[str, Any]]:
    """盘点（**不含**任何令牌原文；只给 scope 与元数据）"""
    _load_accounts()
    with _REG_LOCK:
        return [a.to_dict() for a in sorted(_ACCOUNTS.values(), key=lambda x: x.name)]


def issue_token(name: str, *, ttl_sec: Optional[float] = None,
                now: Optional[float] = None) -> str:
    """为服务账号签发一个令牌

    Returns:
        令牌字面量 ``sa1.<b64(payload)>.<b64(hmac)>``（**只在此处返回一次**；
        仓库不存原文，`revoked_jtis` 与 `issued` 只记 `jti`）。
    """
    _load_accounts()
    key = str(name or "").strip()
    with _REG_LOCK:
        acct = _ACCOUNTS.get(key)
    if acct is None:
        raise ServiceAccountError(f"服务账号不存在: {key!r}")
    if not acct.enabled:
        raise ServiceAccountError(f"服务账号已停用: {key!r}")

    t0 = float(now if now is not None else time.time())
    ttl = float(ttl_sec if ttl_sec is not None else SA_DEFAULT_TTL_SEC)
    if ttl <= 0:
        raise SATokenError(f"ttl_sec 必须为正数，收到 {ttl!r}")
    if ttl > SA_MAX_TTL_SEC:
        raise SATokenError(
            f"ttl_sec 超过上限 {SA_MAX_TTL_SEC}s（约 365 天）："
            "过长的有效期会让「吊销」成为唯一出口，与 90 天轮换纪律冲突")
    payload = {
        "sub": acct.sub,
        "tenant_id": acct.tenant_id,
        "scope": acct.scope.to_dict(),
        "resource_filter": dict(acct.scope.resource_filter),
        "jti": secrets.token_hex(16),
        "exp": t0 + ttl,
        "iat": t0,
        "aud": SA_AUDIENCE,
        "iss": SA_ISSUER,
        "alg": "HS256",
        "account": acct.name,
    }
    body = _canonical(payload)
    token = f"{SA_TOKEN_PREFIX}.{_b64e(body)}.{_sign(body)}"
    with _REG_LOCK:
        _ISSUED[str(payload["jti"])] = (acct.name, float(payload["exp"]))
        _save_accounts()
    return token


# ════════════════════════════════════════════════════════════
#  校验与吊销
# ════════════════════════════════════════════════════════════

def verify_token(token: str, *, now: Optional[float] = None,
                 audience: str = SA_AUDIENCE) -> SATokenClaims:
    """校验令牌；**不通过一律抛异常**（不返回 None，避免调用方漏判）

    Raises:
        SATokenError: 格式/签名/受众/签发者/时间不合法，或账号不存在/已停用。
        SARevokedError: `jti` 在黑名单里（**秒级生效**：先查进程内集合）。
    """
    raw = str(token or "").strip()
    parts = raw.split(".")
    if len(parts) != 3 or parts[0] != SA_TOKEN_PREFIX:
        raise SATokenError("令牌格式非法（期望 sa1.<payload>.<sig>）")
    try:
        body = _b64d(parts[1])
    except Exception as e:  # noqa: BLE001
        raise SATokenError(f"令牌载荷不是合法 base64: {type(e).__name__}") from e
    if not _verify_sig(body, parts[2]):
        raise SATokenError("令牌签名不符（令牌被篡改或签名密钥已更换）")
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise SATokenError(f"令牌载荷不是合法 JSON: {type(e).__name__}") from e
    if not isinstance(payload, dict):
        raise SATokenError("令牌载荷必须是 JSON 对象")

    _load_accounts()
    jti = str(payload.get("jti") or "")
    # ── 吊销：**先查进程内集合**（秒级生效，不等任何 IO）──
    with _REG_LOCK:
        revoked = jti in _REVOKED_JTIS
    if revoked:
        raise SARevokedError(f"令牌已被吊销（jti={jti}）")

    t0 = float(now if now is not None else time.time())
    try:
        exp = float(payload.get("exp") or 0.0)
        iat = float(payload.get("iat") or 0.0)
    except (TypeError, ValueError) as e:
        raise SATokenError(f"令牌 exp/iat 非法: {type(e).__name__}") from e
    if exp <= t0:
        raise SATokenError(f"令牌已过期（exp={exp:.0f} < now={t0:.0f}）")
    if iat > t0 + 60:
        raise SATokenError("令牌 iat 在未来（时钟异常或伪造）")
    if str(payload.get("aud") or "") != str(audience or ""):
        raise SATokenError(f"令牌受众不符（aud={payload.get('aud')!r}）")
    if str(payload.get("iss") or "") != SA_ISSUER:
        raise SATokenError(f"令牌签发者不符（iss={payload.get('iss')!r}）")

    sub = str(payload.get("sub") or "")
    if not sub.startswith(SA_SUB_PREFIX):
        raise SATokenError(f"令牌 sub 缺少 {SA_SUB_PREFIX!r} 前缀（v1.4 §10.1 字段纪律）")
    account = str(payload.get("account") or sub[len(SA_SUB_PREFIX):])
    with _REG_LOCK:
        acct = _ACCOUNTS.get(account)
    if acct is None:
        raise SATokenError(f"令牌对应的服务账号不存在: {account!r}")
    if not acct.enabled:
        raise SATokenError(f"令牌对应的服务账号已停用: {account!r}")

    return SATokenClaims(
        sub=sub, tenant_id=str(payload.get("tenant_id") or acct.tenant_id),
        scope=SAScope.from_dict(payload.get("scope")),
        resource_filter=dict(payload.get("resource_filter") or {}),
        jti=jti, exp=exp, iat=iat, aud=str(payload.get("aud") or ""),
        iss=str(payload.get("iss") or ""), alg=str(payload.get("alg") or "HS256"),
        account=account)


def revoke(jti: str = "", *, name: str = "") -> int:
    """吊销令牌：给 `jti` 或给**账号名**（吊销该账号的全部已签发令牌）

    Returns:
        被吊销的 `jti` 条数（0 = 无事发生，调用方可据此判"吊销是否真的命中"）。

    【为什么必须秒级生效】`_REVOKED_JTIS` 是**进程内**集合，`verify_token` 第一件事
    就是查它（在任何文件 IO 之前）；`_save_accounts()` 只是持久化，其耗时
    （Fernet + 文件写）**不在**吊销生效路径上。
    """
    _load_accounts()
    targets: List[str] = []
    with _REG_LOCK:
        if jti:
            targets.append(str(jti))
        if name:
            key = str(name).strip()
            targets.extend(j for j, v in _ISSUED.items() if v[0] == key)
        added = 0
        for j in targets:
            if j and j not in _REVOKED_JTIS:
                _REVOKED_JTIS.add(j)
                added += 1
        if added:
            _save_accounts()
    if added:
        logger.warning("[service_account] 已吊销 %d 个令牌（jti/账号=%s）",
                       added, jti or name)
    return added


def is_revoked(jti: str) -> bool:
    """该 `jti` 是否已吊销（**只读**；进程内集合优先）"""
    _load_accounts()
    with _REG_LOCK:
        return str(jti or "") in _REVOKED_JTIS


def reset_registry() -> None:
    """清空进程内状态（**仅测试用**：保证用例互不污染）"""
    global _LOADED, _SIGN_KEY
    with _REG_LOCK:
        _ACCOUNTS.clear()
        _REVOKED_JTIS.clear()
        _ISSUED.clear()
        _LOADED = False
    with _KEY_LOCK:
        _SIGN_KEY = None


# ════════════════════════════════════════════════════════════
#  与闸门的接线（预授权 —— **不是旁路**）
# ════════════════════════════════════════════════════════════

#: 当前执行上下文的已验证 SA（contextvar；空 = 本次调用不是 SA）
_SA_VAR: Any = None


def _sa_var() -> Any:
    """惰性创建 SA 的 contextvar（**避免导入期副作用**，与 `tool_gate` 同风格）"""
    global _SA_VAR
    if _SA_VAR is None:
        import contextvars  # noqa: PLC0415 标准库，惰性只为与既有风格一致
        _SA_VAR = contextvars.ContextVar("cp_service_account", default=None)
    return _SA_VAR


def current_service_account() -> Optional[VerifiedSA]:
    """读当前执行上下文的已验证 SA（``None`` = 本次调用不是 SA 身份）"""
    try:
        return _sa_var().get()
    except Exception:  # noqa: BLE001  上下文不可读 ⇒ 按"非 SA"处理
        return None


class _SAHandle:
    """``enter_service_account()`` 的返回值（可用于 ``with``，也可手动 ``reset()``）"""

    __slots__ = ("_sa_token", "_id_token")

    def __init__(self, sa_token: Any, id_token: Any) -> None:
        self._sa_token = sa_token
        self._id_token = id_token

    def reset(self) -> None:
        if self._sa_token is not None:
            try:
                _sa_var().reset(self._sa_token)
            except Exception:  # noqa: BLE001
                pass
            self._sa_token = None
        if self._id_token is not None:
            try:
                from agent.tool_gate import reset_execution_identity  # noqa: PLC0415
                reset_execution_identity(self._id_token)
            except Exception:  # noqa: BLE001
                pass
            self._id_token = None

    def __enter__(self) -> "_SAHandle":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        self.reset()
        return False


def enter_service_account(token: str, *, now: Optional[float] = None) -> _SAHandle:
    """校验令牌并**进入 SA 上下文**（把身份同时写进两层）

    两层都要写，理由：
      · `service_account` 的 contextvar ⇒ 让 `preauthorize()` 能读到 scope；
      · `tool_gate.set_execution_identity("service_account")` ⇒ 让闸门的
        "无身份 ⇒ 拒绝 / 非交互 ⇒ 拒绝（不挂单）"两条判定能**如实**看到身份
        （只看 source 是不够的，`"api"` 同时承载模型调用与 SA 调用）。

    校验失败会抛异常（`SATokenError`/`SARevokedError`），**不进入**任何上下文。
    """
    claims = verify_token(token, now=now)
    sa_tok = _sa_var().set(VerifiedSA(claims=claims))
    id_tok = None
    try:
        from agent.tool_gate import set_execution_identity  # noqa: PLC0415
        id_tok = set_execution_identity("service_account")
    except Exception as e:  # noqa: BLE001  闸门不可用 ⇒ 身份标注降级（不影响校验结果）
        logger.warning("[service_account] tool_gate 身份标注不可用: %s: %s",
                       type(e).__name__, e)
        id_tok = None
    return _SAHandle(sa_tok, id_tok)


def preauthorize(capability: str, identity: str,
                 args: Optional[Dict[str, Any]] = None,
                 level: str = "") -> bool:
    """**闸门调用的预授权判定**（`agent/tool_gate.py::set_preauthorization_hook` 的契约）

    Returns:
        True 仅当：本次上下文的身份是 `service_account`、且该 SA 的 `scope`
        同时满足"级别上限"与"能力集合"两条（见 :func:`scope_allows`）。

    【为什么身份参数也要核】钩子的签名里有 `identity`，若只信上下文而忽略它，
    一个"忘了设 identity 却设了 SA 上下文"的调用点会意外获得预授权。
    两条都核 = 上下文与显式声明**一致**才放行。

    【为什么 args 参与判定但当前不用】`resource_filter` 的资源级强制需要资源级
    策略引擎（当前不存在）。参数留在签名里是**接口预留**（与 v1.4 §1.3 "预留接口"
    同一做法）；将来接上资源级判定时无需改闸门侧的调用点。
    """
    _ = args  # 预留：资源级过滤（见 docstring）
    if str(identity or "").strip().lower() != "service_account":
        return False
    sa = current_service_account()
    if sa is None:
        return False
    return scope_allows(sa.scope, capability, level)


def install_gate_hook() -> bool:
    """把 :func:`preauthorize` 注册进 `agent/tool_gate.py` 的预授权钩子

    Returns:
        True = 安装成功；False = 闸门不可用（**不抛异常**：调用方通常是启动路径，
        而"预授权钩子没装上"的后果是"SA 无法凭 scope 通过 L2"，即**更严**而非更宽）。
    """
    try:
        from agent.tool_gate import set_preauthorization_hook  # noqa: PLC0415
        set_preauthorization_hook(preauthorize)
        logger.info("[service_account] 预授权钩子已装入 tool_gate（预授权是闸门内的判定）")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[service_account] 预授权钩子安装失败（SA 将无法凭 scope 通过 "
                       "L2/L3，属更严的一侧）: %s: %s", type(e).__name__, e)
        return False
