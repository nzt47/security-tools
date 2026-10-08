"""scoped 档真实记忆域 —— 按 provider 选真实后端，写路径带配额/域/审计守卫

【任务定位（S3 收口）】
    P3 已交付 scoped 档的**能力面**（actor_matrix / toolset / assembly /
    capability_exposure 四处判定）与配额组件（agent/subagent/memory_quota.py 的
    MemoryQuotaGuard），但真实记忆读写后端一直没接：guard 没有真实调用点，
    memory_provider 填了也不生效。本模块把这条链打通：

        memory_provider --select_store()--> 真实后端
                                             |- holographic -> LayeredMemoryStore（本地 SQLite/FTS5）
                                             |- mem0        -> Mem0Adapter（可选依赖 + 内置降级）
        写回经 ScopedMemoryDomain.write，**严格按序**：
            1) MemoryQuotaGuard.admit(bytes)      超限/熔断 => 拒绝，绝不触达底层 write
            2) tenancy 域校验                     越域 => 明确拒绝（复用 scope_workspace_mismatch）
            3) 底层 write
            4) guard.record_write(actual_bytes)   按实际字节对账
            5) 审计（persist / reject / degraded 各留痕）

【不易（三条硬边界）】
    1. **未知 provider 不静默回退**：select_store 对词表外取值显式抛
       UnknownMemoryProviderError（回退默认后端 = 你以为在用 mem0、其实写进了本地库）。
    2. **同步桥只有一条**：复用 agent/memory/broker.py 的 run_sync（asyncio.run +
       运行中事件循环显式抛错），不再造第二套。运行中循环 / 依赖缺失 / 抛错 =>
       明确 degraded，**不伪造成功**（底层 save 返回 False 也算失败）。
    3. **密钥形态不落库**：写回内容先过 agent/subagent/credentials.py 的密钥闸门
       （find_manifest_secrets）；命中即拒绝并留 degraded 审计，绝不把 sk-/ghp- 一类
       凭据写进记忆域。

【与 tenancy 的关系】
    LayeredMemoryStore.write 自身还有一层 tenancy 拒绝；本模块的域校验是**前置**
    守卫，保证 scoped 声明的域（memory_scope）与条目声明的域一致 —— 越域请求在
    触达存储前就被拒，且计入连续拒绝熔断。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from agent.memory.broker import run_sync
from agent.memory.taxonomy import coerce_memory_type, new_memory_id, project_scope
from agent.memory.tenancy import (
    TenancyPolicy,
    WriteChannel,
    get_tenancy_policy,
    resolve_tenancy,
)
from agent.subagent.memory_broker import (
    SUPPORTED_MEMORY_PROVIDERS,
    normalize_memory_provider as _normalize_memory_provider,
)
from agent.subagent.memory_quota import (
    AUDIT_SCOPED_DEGRADED,
    AUDIT_SCOPED_PERSIST,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SUPPORTED_MEMORY_PROVIDERS",
    "PROVIDER_STORES",
    "E_MEMORY_PROVIDER_UNKNOWN",
    "E_MEMORY_SCOPE_MISMATCH",
    "E_MEMORY_DEGRADED",
    "E_MEMORY_SECRET_BLOCKED",
    "E_MEMORY_EMPTY",
    "MAX_SCOPED_WRITE_CHARS",
    "UnknownMemoryProviderError",
    "ScopedMemoryError",
    "ScopedMemoryScope",
    "ScopedMemoryEntry",
    "ScopedWriteOutcome",
    "ScopedReadOutcome",
    "ScopedMemoryDomain",
    "normalize_provider",
    "provider_runtime_view",
    "scoped_domain_from_scope",
]

#: scoped 档**已接线**的 provider 词表（唯一权威在 agent/subagent/memory_broker.py：
#: 配置校验与 select_store 共用同一份，不另立第二套）。此处为 re-export 便于同域引用。

#: provider -> 承载后端的稳定标签（回显 / 报告用；不是实现细节的承诺）
PROVIDER_STORES: Dict[str, str] = {
    "holographic": "layered_sqlite_fts5",
    "mem0": "mem0_adapter",
}

#: 错误码
E_MEMORY_PROVIDER_UNKNOWN = "E_MEMORY_PROVIDER_UNKNOWN"
E_MEMORY_SCOPE_MISMATCH = "E_MEMORY_SCOPE_MISMATCH"
E_MEMORY_DEGRADED = "E_MEMORY_DEGRADED"
E_MEMORY_SECRET_BLOCKED = "E_MEMORY_SECRET_BLOCKED"
E_MEMORY_EMPTY = "E_MEMORY_EMPTY"

#: 单次写回正文上限（结构性事实摘要，不是整段正文搬运）
MAX_SCOPED_WRITE_CHARS = 800


class ScopedMemoryError(Exception):
    """scoped 记忆域异常基类"""


class UnknownMemoryProviderError(ScopedMemoryError):
    """provider 不在已接线词表内（**不静默回退默认后端**）"""

    code = E_MEMORY_PROVIDER_UNKNOWN

    def __init__(self, provider: Any) -> None:
        super().__init__(
            "未知 scoped memory_provider %r；已接线: %s（不静默回退默认后端）"
            % (provider, " / ".join(SUPPORTED_MEMORY_PROVIDERS)))
        self.provider = str(provider or "")


class _BackendWriteFailed(ScopedMemoryError):
    """底层后端明确报告失败（如 Mem0Adapter.save 返回 False）"""


def normalize_provider(provider: Any) -> str:
    """provider 别名归一化；词表外取值 => UnknownMemoryProviderError（不猜）

    别名表与词表复用 memory_broker 的同一份口径，避免两处漂移。
    """
    canonical = _normalize_memory_provider(provider)
    if canonical not in SUPPORTED_MEMORY_PROVIDERS:
        raise UnknownMemoryProviderError(provider)
    return canonical


def provider_runtime_view(provider: Any) -> Dict[str, Any]:
    """provider 的运行时承载视图（回显 store / 离线可用性）

    - holographic：本地 SQLite/FTS5，离线可用；
    - mem0：可选依赖；未安装时 Mem0Adapter 走内置 JSON 降级 —— 如实标注 degraded，
      **不把降级说成"语义去重已生效"**。
    """
    canonical = normalize_provider(provider)
    view: Dict[str, Any] = {
        "provider": canonical,
        "store": PROVIDER_STORES.get(canonical, canonical),
        "available": True,
        "degraded": "",
    }
    if canonical == "mem0":
        try:
            import importlib.util

            has_mem0 = importlib.util.find_spec("mem0") is not None
        except Exception:  # noqa: BLE001 探测失败按未安装处理（保守）
            has_mem0 = False
        view["degraded"] = "" if has_mem0 else "mem0_not_installed"
    return view


@dataclass(frozen=True)
class ScopedMemoryScope:
    """scoped 记忆域（来自 SubagentConfig.memory_scope；只放标识，不放正文）"""

    tenant_id: str = ""
    workspace_id: str = ""
    subject_id: str = ""
    workspace_root: str = ""
    memory_types: Tuple[str, ...] = ()
    limit: int = 5

    @classmethod
    def from_mapping(cls, scope: Any) -> "ScopedMemoryScope":
        if isinstance(scope, ScopedMemoryScope):
            return scope
        data: Mapping[str, Any] = scope if isinstance(scope, Mapping) else {}
        raw_types = data.get("memory_types") or ()
        if isinstance(raw_types, str):
            raw_types = (raw_types,)
        try:
            limit = int(data.get("limit") or 5)
        except (TypeError, ValueError):
            limit = 5
        return cls(
            tenant_id=str(data.get("tenant_id") or "").strip(),
            workspace_id=str(data.get("workspace_id") or "").strip(),
            subject_id=str(data.get("subject_id") or "").strip(),
            workspace_root=str(data.get("workspace_root") or "").strip(),
            memory_types=tuple(str(t) for t in raw_types if str(t)),
            limit=max(1, limit),
        )

    @property
    def has_domain(self) -> bool:
        return bool(self.tenant_id or self.workspace_id or self.subject_id)

    def as_dict(self) -> Dict[str, str]:
        return {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
        }


@dataclass(frozen=True)
class ScopedMemoryEntry:
    """一条待写回 scoped 私人记忆域的结构化事实"""

    content: str
    memory_type: str = "fact"
    tenant_id: str = ""
    workspace_id: str = ""
    subject_id: str = ""
    scope: str = ""
    key: str = ""
    confidence: float = 0.5
    source_task_id: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, entry: Any) -> "ScopedMemoryEntry":
        if isinstance(entry, ScopedMemoryEntry):
            return entry
        data: Mapping[str, Any] = entry if isinstance(entry, Mapping) else {}
        return cls(
            content=str(data.get("content") or data.get("text") or ""),
            memory_type=str(data.get("memory_type") or data.get("type") or "fact"),
            tenant_id=str(data.get("tenant_id") or "").strip(),
            workspace_id=str(data.get("workspace_id") or "").strip(),
            subject_id=str(data.get("subject_id") or "").strip(),
            scope=str(data.get("scope") or "").strip(),
            key=str(data.get("key") or "").strip(),
            confidence=float(data.get("confidence") or 0.5),
            source_task_id=str(data.get("source_task_id") or "").strip(),
            extra=dict(data.get("extra") or {}),
        )


def _coerce_entry(entry: Any) -> ScopedMemoryEntry:
    return entry if isinstance(entry, ScopedMemoryEntry) else ScopedMemoryEntry.from_mapping(entry)


@dataclass(frozen=True)
class ScopedWriteOutcome:
    """一次 scoped 写回结果（**不含记忆正文**，可直接投影/审计）"""

    ok: bool = False
    error_code: str = ""
    degraded: str = ""
    bytes: int = 0
    entries: int = 0
    decision_reason: str = ""
    shard_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "error_code": self.error_code,
            "degraded": self.degraded,
            "bytes": int(self.bytes),
            "entries": int(self.entries),
            "decision_reason": self.decision_reason,
            "shard_path": self.shard_path,
        }


@dataclass(frozen=True)
class ScopedReadOutcome:
    """一次 scoped 召回结果（entries 为后端原始条目；degraded 时为空）"""

    ok: bool = False
    error_code: str = ""
    degraded: str = ""
    entries: Tuple[Any, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "error_code": self.error_code,
            "degraded": self.degraded,
            "count": len(self.entries),
        }


# ============================================================
#  后端包装（统一 async write / read 签名；重依赖在函数内导入）
# ============================================================


class _LayeredBackend:
    """holographic 后端：本地四层分片存储（复用既有 LayeredMemoryStore）"""

    provider = "holographic"

    def __init__(self, store: Any) -> None:
        self._store = store

    async def write(self, entry: ScopedMemoryEntry, scope: ScopedMemoryScope) -> Any:
        return await self._store.write(
            entry.content,
            memory_type=entry.memory_type,
            tenant_id=scope.tenant_id or None,
            workspace_id=scope.workspace_id or None,
            subject_id=scope.subject_id or None,
            workspace_root=scope.workspace_root,
            scope=entry.scope,
            confidence=entry.confidence,
            source_task_id=entry.source_task_id,
            entry_id=entry.key,
            extra=dict(entry.extra),
        )

    async def read(self, query: str, scope: ScopedMemoryScope, *,
                   limit: int, memory_types: Optional[Sequence[Any]]) -> Sequence[Any]:
        return await self._store.recall(
            query,
            tenant_id=scope.tenant_id or None,
            workspace_id=scope.workspace_id or None,
            subject_id=scope.subject_id or None,
            workspace_root=scope.workspace_root,
            limit=int(limit),
            memory_types=memory_types or (scope.memory_types or None),
        )


class _Mem0Backend:
    """mem0 后端：复用既有 Mem0Adapter（可选依赖；未装时内置 JSON 降级）"""

    provider = "mem0"

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    async def write(self, entry: ScopedMemoryEntry, scope: ScopedMemoryScope) -> Any:
        key = entry.key or new_memory_id()
        metadata = {
            "tenant_id": scope.tenant_id,
            "workspace_id": scope.workspace_id,
            "subject_id": scope.subject_id,
            "memory_type": entry.memory_type,
        }
        metadata.update({str(k): v for k, v in dict(entry.extra).items()})
        ok = await self._adapter.save(key, entry.content, metadata)
        if not ok:
            raise _BackendWriteFailed("Mem0Adapter.save 明确返回 False")
        return {"key": key, "shard_path": ""}

    async def read(self, query: str, scope: ScopedMemoryScope, *,
                   limit: int, memory_types: Optional[Sequence[Any]]) -> Sequence[Any]:
        return await self._adapter.search(query, top_k=int(limit))


# ============================================================
#  scoped 记忆域
# ============================================================


class ScopedMemoryDomain:
    """一个 scoped 分身的私人记忆域（provider 选择 + 受守卫的同步读写）

    用法::

        domain = ScopedMemoryDomain("holographic", scope={"tenant_id": ..., ...})
        out = domain.write(ScopedMemoryEntry(content="项目使用 tabs 缩进"))
        hits = domain.read("缩进")

    Args:
        provider: memory_provider（词表见 SUPPORTED_MEMORY_PROVIDERS）。
        scope: memory_scope（Mapping 或 ScopedMemoryScope）。
        quota: 归一化后的配额（None => 默认配额）。
        guard: 注入的 MemoryQuotaGuard（测试 / 复用容器级熔断）。
        audit: 审计 sink（与 DelegationExecutor 同款 record(action, ...)）。
        store: 注入的后端（测试用；需实现 async write/read）。
        store_factory: provider -> 后端工厂（测试用；优先于内置选择）。
        policy: 注入的 TenancyPolicy（缺省 get_tenancy_policy()）。
        root: 分片根目录（holographic 用）。
        clock: 熔断时钟（测试确定性）。
        max_chars: 单次写回正文上限。
    """

    def __init__(
        self,
        provider: Any,
        *,
        scope: Any = None,
        quota: Optional[Mapping[str, Any]] = None,
        guard: Any = None,
        audit: Any = None,
        store: Any = None,
        store_factory: Optional[Callable[[str], Any]] = None,
        policy: Optional[TenancyPolicy] = None,
        root: str = "",
        clock: Optional[Callable[[], float]] = None,
        max_chars: int = MAX_SCOPED_WRITE_CHARS,
    ) -> None:
        self.provider = normalize_provider(provider)
        self.scope = ScopedMemoryScope.from_mapping(scope)
        self._audit = audit
        self._store = store
        self._store_factory = store_factory
        self._policy = policy
        self._root = str(root or "")
        self._clock = clock
        self.max_chars = max(1, int(max_chars))
        if guard is None:
            from agent.subagent.memory_quota import guard_from_quota

            guard = guard_from_quota(
                quota, audit=audit, actor="sub_agent",
                subject="scoped:%s" % (self.scope.subject_id or "-"),
                clock=clock)
        self.guard = guard
        self._backend: Any = None

    # -- 后端选择 --

    @property
    def policy(self) -> TenancyPolicy:
        return self._policy or get_tenancy_policy()

    def select_store(self, provider: Any = None) -> Any:
        """按 provider 取真实后端；未知 provider => 显式异常（不静默回退）"""
        canonical = normalize_provider(provider if provider is not None else self.provider)
        if self._store is not None:
            return self._store
        if self._store_factory is not None:
            return self._store_factory(canonical)
        if self._backend is None:
            self._backend = self._build_backend(canonical)
        return self._backend

    def _build_backend(self, canonical: str) -> Any:
        if canonical == "holographic":
            from agent.memory.layered_store import LayeredMemoryStore

            store = (LayeredMemoryStore(root=self._root) if self._root
                     else LayeredMemoryStore())
            return _LayeredBackend(store)
        if canonical == "mem0":
            from agent.memory.adapters.mem0_adapter import Mem0Adapter

            storage_path = os.path.join(
                self._root or os.path.join(".", "data", "memory"),
                "mem0", "scoped_facts.json")
            return _Mem0Backend(Mem0Adapter(storage_path=storage_path))
        # 理论不可达：canonical 已过 normalize_provider
        raise UnknownMemoryProviderError(canonical)

    # -- 审计 --

    def _emit(self, action: str, *, status: str = "",
              payload: Optional[Mapping[str, Any]] = None) -> bool:
        from agent.subagent.memory_quota import emit_scoped_audit

        return emit_scoped_audit(
            self._audit, action, actor="sub_agent",
            subject="scoped:%s" % (self.scope.subject_id or "-"),
            payload=dict(payload or {}), status=status)

    # -- 域校验（复用 tenancy 的 scope_workspace_mismatch）--

    def check_domain(self, entry: ScopedMemoryEntry) -> str:
        """条目声明的域必须在 scope 内；越域返回原因（空 = 通过）

        顺序：先比 tenant/subject/workspace 标识，再让 TenancyPolicy.decide_write
        判定显式 scope（这样 scope=project:<别的 workspace> 也收敛到
        scope_workspace_mismatch，与 tenancy 侧同一个词）。
        """
        scope = self.scope
        if entry.tenant_id and entry.tenant_id != scope.tenant_id:
            return "scope_tenant_mismatch"
        if entry.subject_id and entry.subject_id != scope.subject_id:
            return "scope_subject_mismatch"
        if entry.workspace_id and entry.workspace_id != scope.workspace_id:
            return "scope_workspace_mismatch"
        try:
            tctx = resolve_tenancy(
                tenant_id=scope.tenant_id or None,
                workspace_id=scope.workspace_id or None,
                subject_id=scope.subject_id or None,
                workspace_root=scope.workspace_root,
                use_current_context=False,
            )
            memory_type = coerce_memory_type(entry.memory_type)
            explicit = entry.scope or (
                project_scope(entry.workspace_id) if entry.workspace_id else "")
            decision = self.policy.decide_write(
                memory_type, tctx, channel=WriteChannel.USER, scope=explicit)
        except Exception as e:  # noqa: BLE001 tenancy 不可用 => 明确拒绝，绝不放行
            logger.warning("[ScopedMemory] tenancy 不可用（拒绝写入）: %s", e)
            return "tenancy_unavailable:%s" % type(e).__name__
        if not decision.allowed:
            return decision.reason or "memory_write_rejected"
        return ""

    # -- 写 --

    def write(self, entry: Any, *, task_id: str = "", trace_id: str = "") -> ScopedWriteOutcome:
        """同步写回（严格按序：admit -> 域校验 -> 底层 write -> record_write -> 审计）

        绝不抛异常（fail-soft）：任何降级都如实返回 ok=False + 原因。
        """
        item = _coerce_entry(entry)
        content = str(item.content or "").strip()
        if not content:
            self._emit(AUDIT_SCOPED_DEGRADED, status="degraded",
                       payload={"reason": "empty_content", "provider": self.provider})
            return ScopedWriteOutcome(ok=False, error_code=E_MEMORY_EMPTY,
                                      degraded="empty_content")
        truncated = len(content) > self.max_chars
        if truncated:
            content = content[: self.max_chars]
        item = replace(item, content=content)

        # 0) 密钥形态闸门（在占用配额之前拦下；绝不把凭据写进记忆域）
        offenders = _find_secret_forms(content)
        if offenders:
            self._emit(AUDIT_SCOPED_DEGRADED, status="degraded",
                       payload={"reason": "secret_detected", "provider": self.provider,
                                "offenders": list(offenders)[:5]})
            return ScopedWriteOutcome(ok=False, error_code=E_MEMORY_SECRET_BLOCKED,
                                      degraded="secret_detected")

        size = len(content.encode("utf-8"))

        # 1) 配额准入：拒绝 => 绝不触达底层 write（record_reject 已由 admit 内部统一调用）
        decision = self.guard.admit(size_bytes=size, action="write")
        if not decision.allowed:
            return ScopedWriteOutcome(
                ok=False, error_code=decision.code,
                degraded=("breaker_open" if decision.breaker_open else "quota_exceeded"),
                bytes=int(decision.bytes), entries=int(decision.entries),
                decision_reason=decision.reason)

        # 2) tenancy 域校验
        reason = self.check_domain(item)
        if reason:
            # 域拒绝不是一次落库：先释放 admit 占位，再计连续拒绝/审计
            self.guard.rollback_admission()
            self.guard.record_reject(E_MEMORY_SCOPE_MISMATCH, reason,
                                     payload={"provider": self.provider, "reason": reason})
            self._emit(AUDIT_SCOPED_DEGRADED, status="rejected",
                       payload={"reason": reason, "provider": self.provider,
                                "error_code": E_MEMORY_SCOPE_MISMATCH})
            status = self.guard.status()
            return ScopedWriteOutcome(
                ok=False, error_code=E_MEMORY_SCOPE_MISMATCH, degraded=reason,
                bytes=int(status["bytes"]), entries=int(status["entries"]),
                decision_reason=reason)

        # 3) 底层 write（同步桥；运行中循环 / 依赖缺失 / 抛错 => degraded）
        try:
            backend = self.select_store()
            result = run_sync(lambda: backend.write(item, self.scope),
                              need="scoped 记忆写入")
            if result is False:
                raise _BackendWriteFailed("底层后端返回 False")
        except Exception as e:  # noqa: BLE001 fail-soft
            self.guard.rollback_admission()
            degraded = "write_failed:%s" % type(e).__name__
            self._emit(AUDIT_SCOPED_DEGRADED, status="degraded",
                       payload={"reason": degraded, "provider": self.provider})
            return ScopedWriteOutcome(ok=False, error_code=E_MEMORY_DEGRADED,
                                      degraded=degraded)

        # 4) 按实际字节对账 + 5) 审计
        self.guard.record_write(size)
        status = self.guard.status()
        self._emit(AUDIT_SCOPED_PERSIST, status="written",
                   payload={"provider": self.provider, "bytes": size,
                            "entries": int(status["entries"]), "truncated": truncated,
                            "task_id": str(task_id or ""), "trace_id": str(trace_id or "")})
        return ScopedWriteOutcome(
            ok=True, bytes=size, entries=int(status["entries"]),
            decision_reason="persisted",
            shard_path=str(getattr(result, "shard_path", "") or ""))

    # -- 读 --

    def read(self, query: str = "", *, limit: Optional[int] = None,
             memory_types: Optional[Sequence[Any]] = None) -> ScopedReadOutcome:
        """同步召回（同一同步桥；失败 => 明确 degraded，返回空）"""
        try:
            backend = self.select_store()
            rows = run_sync(
                lambda: backend.read(query, self.scope,
                                     limit=int(limit or self.scope.limit),
                                     memory_types=memory_types),
                need="scoped 记忆读取")
        except Exception as e:  # noqa: BLE001 fail-soft
            degraded = "read_failed:%s" % type(e).__name__
            self._emit(AUDIT_SCOPED_DEGRADED, status="degraded",
                       payload={"reason": degraded, "provider": self.provider,
                                "op": "read"})
            return ScopedReadOutcome(ok=False, error_code=E_MEMORY_DEGRADED,
                                     degraded=degraded)
        return ScopedReadOutcome(ok=True, entries=tuple(rows or ()))

    # -- 视图 --

    def view(self) -> Dict[str, Any]:
        """只读投影（**不含正文**）：provider/store/degraded/配额与熔断状态"""
        data = provider_runtime_view(self.provider)
        data["scope"] = self.scope.as_dict()
        data["quota"] = self.guard.status()
        return data


def _find_secret_forms(content: str) -> Sequence[str]:
    """密钥形态扫描（复用 credentials 的闸门；闸门不可用 => fail-closed）"""
    try:
        from agent.subagent.credentials import find_manifest_secrets

        return find_manifest_secrets({"content": str(content or "")})
    except Exception as e:  # noqa: BLE001 闸门不可用 => 拒绝落库（保守，不赌）
        logger.warning("[ScopedMemory] 密钥闸门不可用（拒绝落库）: %s", e)
        return ("<credentials_gate_unavailable>",)


def scoped_domain_from_scope(
    provider: Any,
    scope: Any,
    *,
    quota: Optional[Mapping[str, Any]] = None,
    audit: Any = None,
    root: str = "",
    store: Any = None,
    store_factory: Optional[Callable[[str], Any]] = None,
) -> ScopedMemoryDomain:
    """便捷构造：从 memory_scope + provider + quota 建域（executor / container 共用）"""
    return ScopedMemoryDomain(
        provider, scope=scope, quota=quota, audit=audit, root=root,
        store=store, store_factory=store_factory)
