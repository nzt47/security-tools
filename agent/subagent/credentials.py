"""子智能体临时凭据（v7.2 §5.9）

【不易（§5.9 逐字）】
    「子智能体凭据 TTL ≤ 任务时长，任务结束即销毁；每来源独立凭据；manifest 禁存
    长期密钥；第三方 MCP Server 默认隔离容器（无宿主网络 / 无 SSH agent / 无 $HOME）。」

    本模块落第 1、2、3 条（第 4 条在 ``sandbox.py`` 的隔离默认值）：

    1. **TTL ≤ 任务时长**：``issue()`` 强制 ``ttl_seconds <= task_timeout_seconds``，
       超出的申请**直接拒绝**（不是悄悄截断）——静默截断会让调用方以为拿到了更长的
       凭据窗口。
    2. **任务结束即销毁**：``credential_scope()`` 在 ``finally`` 中销毁，异常/超时/
       取消路径同样销毁；``destroy()`` 幂等。销毁会**擦除明文**并保留指纹（供审计
       关联），销毁后读取 ``value`` 抛 ``CredentialDestroyed``——「隐藏失败会长期
       留存凭据」，故把「读取失败」做成可断言事实，而不是靠日志自证。
    3. **每来源独立凭据**：凭据按 ``(source, name)`` 发号，同一逻辑名在不同来源下
       是两条独立凭据（独立 TTL / 独立环境变量 / 独立销毁），避免一个来源的凭据被
       复用到另一个来源。

【manifest 禁存长期密钥】
    ``assert_manifest_secret_free()`` 是落盘前的最后一道闸：既查**已知密钥值**是否
    出现在 manifest 里（精确比对），也查可疑**键名**（``api_key`` / ``token`` /
    ``secret`` / ``password`` 一类）是否带了非空值。违反抛 ``ManifestSecretLeak``。

【依赖纪律】
    仅标准库（hashlib/os/time/uuid/contextlib），零 agent 内部依赖。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: 临时凭据注入环境时的变量名前缀（便于审计区分「临时」与「长期」）
ENV_PREFIX = "CP_TEMP_"

#: 临时凭据的 TTL 上界（秒）——`.env` 可调 ``CP_SUBAGENT_CRED_TTL_MAX``
DEFAULT_MAX_TTL_SECONDS = 3600.0

#: 错误码
E_CREDENTIAL_TTL_TOO_LONG = "E_CREDENTIAL_TTL_TOO_LONG"
E_CREDENTIAL_DESTROYED = "E_CREDENTIAL_DESTROYED"
E_MANIFEST_SECRET_LEAK = "E_MANIFEST_SECRET_LEAK"

#: 可疑密钥**键名**（含即视为长期密钥槽位；值非空则违规）
_SUSPICIOUS_KEY_RE = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|credential|private[_-]?key|"
    r"access[_-]?key|client[_-]?secret)",
    re.IGNORECASE,
)

#: 值形态上的疑似长期密钥（长度 ≥ 24 的连续高熵串，或已知前缀）
_SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)

_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9]+")


def _env_float(name: str, default: float) -> float:
    """读取 ``.env`` 浮点配置（非法值回退默认，不抛异常）"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("[Credentials] %s 非法值 %r，回退默认 %s", name, raw, default)
        return default
    return value if value > 0 else default


def fingerprint(value: str) -> str:
    """凭据指纹（sha256 前 16 位）——销毁后仍可关联审计，且不可还原明文"""
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def env_var_name(source: str, name: str) -> str:
    """凭据环境变量名：``CP_TEMP_<SOURCE>_<NAME>``（大写 + 非字母数字折叠为 ``_``）"""
    src = _NAME_SAFE_RE.sub("_", str(source or "unknown")).strip("_").upper()
    nm = _NAME_SAFE_RE.sub("_", str(name or "cred")).strip("_").upper()
    return f"{ENV_PREFIX}{src or 'UNKNOWN'}_{nm or 'CRED'}"


# ════════════════════════════════════════════════════════════
#  异常
# ════════════════════════════════════════════════════════════


class CredentialError(Exception):
    """凭据相关异常基类"""


class CredentialTTLTooLong(CredentialError):
    """申请的 TTL 超过任务时长（§5.9：TTL ≤ 任务时长）"""

    code = E_CREDENTIAL_TTL_TOO_LONG

    def __init__(self, ttl_seconds: float, task_timeout_seconds: float) -> None:
        self.ttl_seconds = ttl_seconds
        self.task_timeout_seconds = task_timeout_seconds
        super().__init__(
            f"{E_CREDENTIAL_TTL_TOO_LONG}: 凭据 TTL={ttl_seconds}s 超过任务时长 "
            f"{task_timeout_seconds}s（§5.9 要求 TTL ≤ 任务时长）")


class CredentialDestroyed(CredentialError):
    """凭据已销毁后读取明文（**这是期望行为**：销毁即为不可用）"""

    code = E_CREDENTIAL_DESTROYED

    def __init__(self, credential_id: str, name: str = "") -> None:
        self.credential_id = credential_id
        self.name = name
        super().__init__(
            f"{E_CREDENTIAL_DESTROYED}: 凭据 {name or credential_id} 已销毁，明文不可读")


class ManifestSecretLeak(CredentialError):
    """manifest 中检出长期密钥（§2.3 硬约束 / §5.9）"""

    code = E_MANIFEST_SECRET_LEAK

    def __init__(self, offenders: Sequence[str]) -> None:
        self.offenders = tuple(offenders)
        super().__init__(
            f"{E_MANIFEST_SECRET_LEAK}: manifest 检出 {len(self.offenders)} 处疑似"
            f"长期密钥：{', '.join(self.offenders)}（§2.3 硬约束：manifest 禁存长期密钥）")


# ════════════════════════════════════════════════════════════
#  凭据
# ════════════════════════════════════════════════════════════


@dataclass
class TemporaryCredential:
    """一次性临时凭据

    明文只在内存中存活于 ``_value``；``destroy()`` 后 ``_value`` 被清空且
    ``value`` 属性抛 ``CredentialDestroyed``。

    Attributes:
        credential_id: 凭据标识（``cred-<hex>``）。
        name: 逻辑名（如 ``GITHUB_TOKEN``）。
        source: 来源标识（如 ``mcp:github``）——**每来源独立凭据**的键之一。
        env_var: 注入子代理进程的变量名。
        ttl_seconds: 存活时长（≤ 任务时长）。
        issued_at / expires_at: 签发与到期时间戳。
        value_fingerprint: 明文的 sha256 前 16 位（销毁后仍可审计关联）。
        destroyed / destroyed_at / destroy_reason: 销毁状态。
    """

    name: str
    source: str
    ttl_seconds: float
    value_fingerprint: str
    credential_id: str = ""
    env_var: str = ""
    issued_at: float = 0.0
    expires_at: float = 0.0
    destroyed: bool = False
    destroyed_at: float = 0.0
    destroy_reason: str = ""
    _value: str = field(default="", repr=False)
    #: 时钟函数（与签发它的管理器**同一个**时钟）——签发与到期必须同源，
    #: 否则注入时钟的调用方会拿到互相矛盾的 issued_at / is_expired。
    _clock: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._clock is None:
            self._clock = time.time
        if not self.credential_id:
            self.credential_id = f"cred-{uuid.uuid4().hex[:12]}"
        if not self.env_var:
            self.env_var = env_var_name(self.source, self.name)
        if not self.issued_at:
            self.issued_at = self._clock()
        if not self.expires_at:
            self.expires_at = self.issued_at + float(self.ttl_seconds)

    # ── 明文访问（销毁即不可读）──

    @property
    def value(self) -> str:
        """明文（**销毁后抛 ``CredentialDestroyed``**，绝不返回空串冒充）"""
        if self.destroyed:
            raise CredentialDestroyed(self.credential_id, self.name)
        return self._value

    @property
    def is_active(self) -> bool:
        return not self.destroyed

    @property
    def is_expired(self) -> bool:
        return (not self.destroyed) and self._clock() >= self.expires_at

    @property
    def age_seconds(self) -> float:
        return max(0.0, float(self._clock()) - float(self.issued_at))

    @property
    def wipe_verified(self) -> bool:
        """销毁后明文确实为空（销毁的**可断言证据**）"""
        return bool(self.destroyed and not self._value)

    # ── 销毁 ──

    def destroy(self, reason: str = "task_end") -> bool:
        """销毁凭据（幂等）；返回是否为**本次**销毁

        先擦明文再落状态：即便中途异常，明文也已被清空。
        """
        if self.destroyed:
            return False
        self._value = ""
        self.destroyed = True
        self.destroyed_at = self._clock()
        self.destroy_reason = str(reason or "task_end")
        return True

    def to_dict(self) -> Dict[str, Any]:
        """审计视图——**绝不含明文**"""
        return {
            "credential_id": self.credential_id,
            "name": self.name,
            "source": self.source,
            "env_var": self.env_var,
            "ttl_seconds": round(float(self.ttl_seconds), 3),
            "issued_at": round(float(self.issued_at), 3),
            "expires_at": round(float(self.expires_at), 3),
            "value_fingerprint": self.value_fingerprint,
            "destroyed": bool(self.destroyed),
            "destroyed_at": round(float(self.destroyed_at), 3),
            "destroy_reason": self.destroy_reason,
            "wipe_verified": self.wipe_verified,
        }


# ════════════════════════════════════════════════════════════
#  凭据管理器
# ════════════════════════════════════════════════════════════


class TemporaryCredentialManager:
    """临时凭据管理器（签发 / 注入 / 销毁 / 审计）

    线程安全：所有状态变更在 ``RLock`` 内完成（执行器用线程池并行委派，凭据
    管理器是共享资源）。
    """

    def __init__(self, *, max_ttl_seconds: Optional[float] = None,
                 clock: Any = None) -> None:
        """
        Args:
            max_ttl_seconds: TTL 上界（缺省读 ``CP_SUBAGENT_CRED_TTL_MAX``，
                再缺省 3600s）。
            clock: 时钟函数（测试用；缺省 ``time.time``）。
        """
        self._lock = threading.RLock()
        self._credentials: Dict[str, TemporaryCredential] = {}
        self._by_source: Dict[str, List[str]] = {}
        self._max_ttl = (float(max_ttl_seconds) if max_ttl_seconds is not None
                         else _env_float("CP_SUBAGENT_CRED_TTL_MAX",
                                         DEFAULT_MAX_TTL_SECONDS))
        self._clock = clock or time.time
        self._total_issued = 0
        self._total_destroyed = 0

    # ── 签发 ──

    def issue(self, name: str, value: str, *, source: str,
              task_timeout_seconds: float,
              ttl_seconds: Optional[float] = None) -> TemporaryCredential:
        """签发一条临时凭据

        Args:
            name: 逻辑名（如 ``GITHUB_TOKEN``）。
            value: 明文（**只在内存中**）。
            source: 来源标识（每来源独立）。
            task_timeout_seconds: 任务时长（§5.9 的 TTL 上界来源）。
            ttl_seconds: 期望 TTL；缺省 = 任务时长。

        Raises:
            CredentialTTLTooLong: ``ttl > task_timeout`` 或 ``ttl > max_ttl``。
            CredentialError: 参数非法（空名/空来源/空值/非正任务时长）。
        """
        ttl = float(task_timeout_seconds if ttl_seconds is None else ttl_seconds)
        timeout = float(task_timeout_seconds)
        if timeout <= 0:
            raise CredentialError(f"任务时长必须为正数：{task_timeout_seconds!r}")
        if ttl <= 0:
            raise CredentialError(f"凭据 TTL 必须为正数：{ttl!r}")
        if ttl > timeout:
            raise CredentialTTLTooLong(ttl, timeout)
        if ttl > self._max_ttl:
            raise CredentialTTLTooLong(ttl, self._max_ttl)
        if not str(name or "").strip():
            raise CredentialError("凭据名不得为空")
        if not str(source or "").strip():
            raise CredentialError("凭据来源不得为空（§5.9：每来源独立凭据）")
        if not str(value or "").strip():
            raise CredentialError("凭据明文不得为空")

        now = self._clock()
        cred = TemporaryCredential(
            name=str(name).strip(),
            source=str(source).strip(),
            ttl_seconds=ttl,
            value_fingerprint=fingerprint(value),
            issued_at=now,
            expires_at=now + ttl,
            _value=str(value),
            _clock=self._clock,
        )
        with self._lock:
            self._credentials[cred.credential_id] = cred
            self._by_source.setdefault(cred.source, []).append(cred.credential_id)
            self._total_issued += 1
        logger.debug("[Credentials] 签发 %s source=%s ttl=%.1fs fp=%s",
                     cred.name, cred.source, ttl, cred.value_fingerprint)
        return cred

    def issue_for_sources(self, name: str,
                          per_source: Mapping[str, str], *,
                          task_timeout_seconds: float,
                          ttl_seconds: Optional[float] = None,
                          ) -> List[TemporaryCredential]:
        """为多个来源各签发一条**独立**凭据（§5.9「每来源独立凭据」）"""
        return [
            self.issue(name, value, source=source,
                       task_timeout_seconds=task_timeout_seconds,
                       ttl_seconds=ttl_seconds)
            for source, value in per_source.items()
        ]

    # ── 销毁 ──

    def destroy(self, credential_id: str, reason: str = "task_end") -> bool:
        """按 id 销毁（幂等）；返回是否为本次销毁"""
        with self._lock:
            cred = self._credentials.get(credential_id)
            if cred is None:
                return False
            changed = cred.destroy(reason)
            if changed:
                self._total_destroyed += 1
            return changed

    def destroy_credential(self, cred: TemporaryCredential,
                           reason: str = "task_end") -> bool:
        """按对象销毁（``destroy`` 的便捷形式）"""
        return self.destroy(cred.credential_id, reason)

    def destroy_source(self, source: str, reason: str = "task_end") -> int:
        """销毁某来源的全部凭据"""
        with self._lock:
            ids = list(self._by_source.get(str(source).strip(), ()))
        return sum(1 for cid in ids if self.destroy(cid, reason))

    def destroy_all(self, reason: str = "task_end") -> int:
        """销毁全部凭据（进程退出/测试隔离用）"""
        with self._lock:
            ids = list(self._credentials.keys())
        return sum(1 for cid in ids if self.destroy(cid, reason))

    def sweep_expired(self, reason: str = "ttl_expired") -> int:
        """销毁已到期凭据（TTL 的兜底路径：即便调用方忘记销毁）"""
        with self._lock:
            expired = [cid for cid, c in self._credentials.items() if c.is_expired]
        return sum(1 for cid in expired if self.destroy(cid, reason))

    # ── 查询 ──

    def get(self, credential_id: str) -> Optional[TemporaryCredential]:
        with self._lock:
            return self._credentials.get(credential_id)

    def active(self) -> List[TemporaryCredential]:
        with self._lock:
            return [c for c in self._credentials.values() if c.is_active]

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for c in self._credentials.values() if c.is_active)

    def for_source(self, source: str) -> List[TemporaryCredential]:
        with self._lock:
            return [self._credentials[cid] for cid in self._by_source.get(str(source).strip(), ())
                    if cid in self._credentials]

    def assert_all_destroyed(self) -> None:
        """断言无存活凭据（任务收尾的守门；有存活则抛 ``CredentialError``）"""
        live = self.active()
        if live:
            names = ", ".join(f"{c.name}@{c.source}" for c in live)
            raise CredentialError(
                f"仍有 {len(live)} 条临时凭据未销毁：{names}"
                "（§5.9 要求任务结束即销毁）")

    # ── 环境注入 ──

    def env_for(self, credentials: Optional[Iterable[TemporaryCredential]] = None) -> Dict[str, str]:
        """构造注入子代理进程的环境变量（仅临时凭据键值）"""
        creds = list(credentials) if credentials is not None else self.active()
        env: Dict[str, str] = {}
        for cred in creds:
            if cred.is_active:
                env[cred.env_var] = cred.value
        return env

    # ── 审计视图 ──

    def snapshot(self) -> Dict[str, Any]:
        """统计快照（**不含明文**）"""
        with self._lock:
            creds = list(self._credentials.values())
            return {
                "issued": self._total_issued,
                "destroyed": self._total_destroyed,
                "active": sum(1 for c in creds if c.is_active),
                "sources": sorted({c.source for c in creds}),
                "max_ttl_seconds": self._max_ttl,
                "credentials": [c.to_dict() for c in creds],
            }


# ════════════════════════════════════════════════════════════
#  作用域（finally 销毁）
# ════════════════════════════════════════════════════════════


@contextmanager
def credential_scope(
    manager: TemporaryCredentialManager,
    specs: Sequence[Mapping[str, Any]],
    *,
    task_timeout_seconds: float,
    reason: str = "task_end",
) -> Iterator[List[TemporaryCredential]]:
    """凭据作用域：进入时签发，``finally`` **无条件销毁**

    ``specs`` 每项为 ``{"name": ..., "value": ..., "source": ..., "ttl_seconds": 可选}``。

    为什么用 ``finally`` 而不是 ``except``：超时/取消/``KeyboardInterrupt`` 都不
    经过 ``except Exception``，只有 ``finally`` 能保证销毁。
    """
    issued: List[TemporaryCredential] = []
    try:
        for spec in specs:
            issued.append(manager.issue(
                str(spec.get("name") or ""),
                str(spec.get("value") or ""),
                source=str(spec.get("source") or ""),
                task_timeout_seconds=task_timeout_seconds,
                ttl_seconds=spec.get("ttl_seconds"),
            ))
        yield issued
    finally:
        for cred in issued:
            try:
                manager.destroy(cred.credential_id, reason)
            except Exception as e:  # noqa: BLE001  销毁失败不得掩盖原始异常
                logger.error("[Credentials] 销毁失败 %s: %s", cred.credential_id, e)


# ════════════════════════════════════════════════════════════
#  manifest 密钥闸门（§2.3 硬约束 / §5.9）
# ════════════════════════════════════════════════════════════


def find_manifest_secrets(payload: Any, *,
                          known_secrets: Iterable[str] = ()) -> List[str]:
    """扫描 manifest，返回违规路径列表（``a.b[0].c`` 形式）

    两类命中：
      1. **已知密钥值**出现在任意值中（精确子串比对）；
      2. 可疑**键名**（api_key/token/secret/password/…）带非空值；
      3. 值形态像长期密钥（``sk-`` / ``ghp_`` / ``AKIA`` / PEM 头）。
    """
    known = [str(s) for s in known_secrets if str(s)]
    offenders: List[str] = []

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                key_str = str(key)
                child = f"{path}.{key_str}" if path else key_str
                if _SUSPICIOUS_KEY_RE.search(key_str) and isinstance(value, str) and value.strip():
                    offenders.append(child)
                _walk(value, child)
            return
        if isinstance(node, (list, tuple)):
            for idx, item in enumerate(node):
                _walk(item, f"{path}[{idx}]")
            return
        if isinstance(node, str):
            if any(secret and secret in node for secret in known):
                offenders.append(path or "<root>")
                return
            if _SECRET_VALUE_RE.search(node):
                offenders.append(path or "<root>")

    _walk(payload, "")
    # 去重保序
    seen: List[str] = []
    for item in offenders:
        if item not in seen:
            seen.append(item)
    return seen


def assert_manifest_secret_free(payload: Any, *,
                                known_secrets: Iterable[str] = ()) -> None:
    """manifest 落盘前的密钥闸门（违规抛 ``ManifestSecretLeak``）

    Raises:
        ManifestSecretLeak: 检出长期密钥。
    """
    offenders = find_manifest_secrets(payload, known_secrets=known_secrets)
    if offenders:
        raise ManifestSecretLeak(offenders)


__all__ = [
    "ENV_PREFIX", "DEFAULT_MAX_TTL_SECONDS",
    "E_CREDENTIAL_TTL_TOO_LONG", "E_CREDENTIAL_DESTROYED", "E_MANIFEST_SECRET_LEAK",
    "CredentialError", "CredentialTTLTooLong", "CredentialDestroyed", "ManifestSecretLeak",
    "TemporaryCredential", "TemporaryCredentialManager",
    "credential_scope",
    "fingerprint", "env_var_name",
    "find_manifest_secrets", "assert_manifest_secret_free",
]
