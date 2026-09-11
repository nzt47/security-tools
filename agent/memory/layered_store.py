"""记忆四层分片存储（复用既有 ``LongTermMemory`` 引擎与检索，不另建第二套记忆库）

设计要点
--------
1. **复用而非新建**：每个分片就是一个 ``LongTermMemory`` 实例（既有 SQLite 引擎、
   既有 keyword/vec 检索、既有锁与连接池），仅换 ``db_path``。分层字段落在既有的
   ``metadata`` JSON 列里 —— **不改既有表结构、不改既有方法签名与默认行为**。
2. **物理分库即隔离**（§4.3「物理分库」+ P7.2-08）：租户分片目录由 tenant_id 决定，
   偏好分片目录由 subject_id 决定 —— 跨租户不可见是**结构性保证**，不依赖 WHERE 子句。
3. **检索复用**：召回走既有 ``LongTermMemory.search()``（keyword）+ ``list_recent()``，
   再叠加 §4.3 的可见性过滤与优先级排序；既有记忆检索的公开行为零改动
   （``_LayeredLongTermMemory`` 只在返回的 ``MemoryResult.metadata`` 里**追加**
   ``layer`` 叶子字段，既有键一个不动）。

分片布局::

    <root>/
      org/strategy.db                      # 企业策略记忆（org 级只读下发）
      tenants/<slug(tenant)>/working.db    # 工作记忆
      tenants/<slug(tenant)>/fact.db       # 事实记忆（按租户隔离）
      tenants/<slug(tenant)>/strategy.db   # 租户内策略（SYSTEM 通道回填）
      subjects/<slug(subject)>/preference.db   # 偏好记忆（随 subject 跨租户携带）
      degraded/unscoped.db                 # 缺租户字段的**显式降级**隔离区（默认召回不含）
      identity/subject_salts.json          # 审计伪名盐表（匿名化=销毁盐）

环境变量
--------
- ``MEMORY_LAYERS_ROOT``: 分片根目录（默认 ``./data/memory/layers``）
- ``MEMORY_IDENTITY_ROOT``: 盐表目录（默认 ``<root>/identity``）
- ``MEMORY_LAYERS_AUDIT``: ``0`` 时关闭记忆审计写入（默认开启）
"""

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from agent.logging_utils import log_dict
from agent.memory.identity import SubjectPseudonymizer, record_memory_audit
from agent.memory.long_term_memory import LongTermMemory
from agent.memory.taxonomy import (
    GLOBAL_SCOPE,
    LAYER_POLICY,
    MemoryEntry,
    MemoryEntryError,
    MemoryType,
    coerce_memory_type,
    expiry_for,
    new_memory_id,
    scope_kind,
)
from agent.memory.tenancy import (
    DEGRADED_TENANT,
    ORG_TENANT,
    MemoryWriteRejected,
    TenancyContext,
    TenancyPolicy,
    WriteChannel,
    WriteDecision,
    WriteDisposition,
    get_tenancy_policy,
    resolve_tenancy,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_LAYERS_ROOT",
    "ENV_LAYERS_ROOT",
    "LAYER_TAG_PREFIX",
    "load_layer_metadata",
    "redact_content",
    "WriteResult",
    "LayeredMemoryStore",
]

#: 云枢 local-first 保险库根（v7.2 §8：D2 记忆归档直写 ``~/.cloudpivot/vault/``）
DEFAULT_VAULT_ROOT = os.path.join(os.path.expanduser("~"), ".cloudpivot", "vault")

#: 分片根目录默认落点（可用 ``MEMORY_LAYERS_ROOT`` 覆盖）
#:
#: 刻意落在**项目树之外**：§8 P7.2-18 要求备份/归档不得位于项目树内，
#: 同时避免记忆存储被误提交进仓库（并行会话 index 卫生）。
DEFAULT_LAYERS_ROOT = os.path.join(DEFAULT_VAULT_ROOT, "memory", "layers")

ENV_LAYERS_ROOT = "MEMORY_LAYERS_ROOT"
ENV_IDENTITY_ROOT = "MEMORY_IDENTITY_ROOT"
ENV_LAYERS_AUDIT = "MEMORY_LAYERS_AUDIT"

#: 分层条目的标签前缀（供既有 LIKE 检索筛选与诊断，避免与业务标签碰撞）
LAYER_TAG_PREFIX = "cp-layer"

_REDACT_FILTER: Any = None


# ════════════════════════════════════════════════════════════
#  既有引擎的兼容叠加
# ════════════════════════════════════════════════════════════


def load_layer_metadata(raw: Any) -> Optional[MemoryEntry]:
    """从既有 ``metadata`` 列还原分层条目（非分层条目返回 None）

    容忍两种形态：dict（``list_recent``/``get`` 的解析结果不保证）与 JSON TEXT
    （既有 ``LongTermMemoryEntry.from_dict`` 直接透传字符串列值）。
    """
    if raw is None:
        return None
    data: Any = raw
    if isinstance(raw, str):
        if not str(raw).strip():
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    if not isinstance(data, dict):
        return None
    if not data.get("id") or not data.get("type"):
        return None
    try:
        return MemoryEntry.from_dict(data)
    except (MemoryEntryError, TypeError, ValueError):
        return None


class _LayeredLongTermMemory(LongTermMemory):
    """``LongTermMemory`` 的**只增不改**叠加：让检索结果携带分层 metadata

    守不易：不重写任何既有方法；``_row_to_memory_result`` 只 在 ``MemoryResult.metadata``
    中**追加** ``layer`` 键（既有 key/importance/tags/sensitive/verified 一个不动）。
    """

    def _row_to_memory_result(self, row: Any):  # type: ignore[override]
        result = super()._row_to_memory_result(row)
        try:
            entry = load_layer_metadata(row.get("metadata") if hasattr(row, "get") else None)
        except Exception:  # noqa: BLE001 叠加失败不得影响既有检索
            entry = None
        if entry is not None:
            result.metadata["layer"] = entry.to_dict()
        return result


# ════════════════════════════════════════════════════════════
#  内容脱敏（§3.4：脱敏发生在哈希之前）
# ════════════════════════════════════════════════════════════


def _get_redact_filter() -> Any:
    global _REDACT_FILTER
    if _REDACT_FILTER is None:
        from agent.utils.sensitive_data_filter import SensitiveDataFilter

        _REDACT_FILTER = SensitiveDataFilter()
    return _REDACT_FILTER


def redact_content(content: Any) -> str:
    """记忆内容脱敏 → ``content_redacted``（§3.11）

    主路径复用既有 ``SensitiveDataFilter.detect_and_sanitize``（与 memory/filter.py 同源）；
    该模块不可用时回退 S2-01 的 ``trace_v2.redact``；两者皆不可用时返回去空白文本。
    """
    if isinstance(content, str):
        text = content
    else:
        try:
            text = json.dumps(content, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(content)
    try:
        _changed, sanitized, _matches = _get_redact_filter().detect_and_sanitize(text)
        return str(sanitized)
    except Exception:  # noqa: BLE001 脱敏失败回退，不阻断写入
        try:
            from agent.observability.trace_v2 import redact as _redact

            return str(_redact(text))
        except Exception:  # noqa: BLE001
            return text


# ════════════════════════════════════════════════════════════
#  工具
# ════════════════════════════════════════════════════════════


def _slug(value: str) -> str:
    """分片目录名：可读前缀 + 短哈希（确定性、文件系统安全、无碰撞歧义）"""
    raw = str(value or "")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", raw)[:32].strip("._-")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return "%s-%s" % (safe or "x", digest)


def _importance_for(confidence: float) -> int:
    """置信度 → 既有引擎的 importance（1..4）

    刻意**不产生 5**：既有 ``delete(force=False)`` 对 importance>=5 需审查，
    分层记忆的删除走受控的遗忘通道（显式 force），不与既有保护语义纠缠。
    """
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.5
    conf = min(1.0, max(0.0, conf))
    return int(min(4, max(1, round(conf * 3) + 1)))


def _tags_for(entry: MemoryEntry) -> List[str]:
    """分层标签（供既有 LIKE 检索筛选与诊断；不含任何主体原始标识符）"""
    tags = [
        "%s:%s" % (LAYER_TAG_PREFIX, entry.type.value),
        "cp-scope:%s" % scope_kind(entry.scope),
        "cp-tenant:%s" % (entry.tenant_id or "-"),
    ]
    if entry.is_org_level:
        tags.append("cp-org:1")
    if entry.degraded:
        tags.append("cp-degraded:1")
    return tags


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


@dataclass
class WriteResult:
    """写入结果（条目 + 判定 + 落点，供调用方与验收报告取证）"""

    entry: MemoryEntry
    decision: WriteDecision
    persisted: bool = False
    shard_path: str = ""

    @property
    def degraded(self) -> bool:
        return self.decision.degraded

    def as_dict(self) -> Dict[str, Any]:
        return {
            "entry": self.entry.to_dict(),
            "decision": self.decision.as_dict(),
            "persisted": self.persisted,
            "shard_path": self.shard_path,
        }


# ════════════════════════════════════════════════════════════
#  分片存储
# ════════════════════════════════════════════════════════════


class LayeredMemoryStore:
    """四层记忆分片存储（P7.2-08 隔离 / §4.3 召回优先级 / §3.11 条目模型）

    用法::

        store = LayeredMemoryStore(root=str(tmp_path))
        await store.write("使用 tabs 缩进", memory_type="preference",
                          workspace_root="/repo/a", subject_id="alice")
        hits = await store.recall("缩进", workspace_root="/repo/a", subject_id="alice")

    Attributes:
        root: 分片根目录
        policy: 租户隔离策略（``TenancyPolicy``）
        pseudonymizer: 审计伪名化器（擦除=销毁盐）
    """

    def __init__(
        self,
        root: Optional[str] = None,
        *,
        tenant_policy: Optional[TenancyPolicy] = None,
        pseudonymizer: Optional[SubjectPseudonymizer] = None,
        clock: Optional[Callable[[], float]] = None,
        store_factory: Optional[Callable[..., LongTermMemory]] = None,
        audit: Optional[bool] = None,
    ) -> None:
        self.root = os.path.abspath(
            root or os.environ.get(ENV_LAYERS_ROOT) or DEFAULT_LAYERS_ROOT
        )
        self.policy = tenant_policy or get_tenancy_policy()
        self.pseudonymizer = pseudonymizer or SubjectPseudonymizer(
            root=os.path.join(self.root, "identity")
        )
        self._clock: Callable[[], float] = clock or time.time
        self._store_factory = store_factory or _LayeredLongTermMemory
        self._stores: Dict[str, LongTermMemory] = {}
        self._audit = _env_flag(ENV_LAYERS_AUDIT, True) if audit is None else bool(audit)

    # ── 时钟 ──

    def now(self) -> float:
        """当前时刻（可注入时钟，避免真实时钟边界，START 坑 3）"""
        try:
            return float(self._clock())
        except Exception:  # noqa: BLE001 注入时钟异常 → 回退墙钟
            return time.time()

    # ── 分片路径 ──

    def shard_path(
        self,
        memory_type: Any,
        *,
        tenant_id: str = "",
        subject_id: str = "",
        org_level: bool = False,
        degraded: bool = False,
    ) -> str:
        """按（层 / 租户 / 主体 / org 级）解析分片文件路径（隔离的物理落点）"""
        mt = coerce_memory_type(memory_type)
        if degraded:
            return os.path.join(self.root, "degraded", "unscoped.db")
        if org_level and mt is MemoryType.STRATEGY:
            return os.path.join(self.root, "org", "strategy.db")
        if mt is MemoryType.PREFERENCE:
            return os.path.join(
                self.root, "subjects", _slug(subject_id or "-"), "preference.db")
        return os.path.join(
            self.root, "tenants", _slug(tenant_id or "-"), "%s.db" % mt.value)

    def shard_paths(self, ctx: TenancyContext, *, include_degraded: bool = False) -> Dict[str, str]:
        """当前上下文可达的全部分片路径（诊断与单测用）"""
        paths: Dict[str, str] = {
            "org_strategy": self.shard_path(MemoryType.STRATEGY, org_level=True),
            "tenant_working": self.shard_path(MemoryType.WORKING, tenant_id=ctx.tenant_id),
            "tenant_fact": self.shard_path(MemoryType.FACT, tenant_id=ctx.tenant_id),
            "tenant_strategy": self.shard_path(MemoryType.STRATEGY, tenant_id=ctx.tenant_id),
            "subject_preference": self.shard_path(
                MemoryType.PREFERENCE, subject_id=ctx.subject_id),
        }
        if include_degraded:
            paths["degraded"] = self.shard_path(MemoryType.FACT, degraded=True)
        return paths

    def owner_shard_path(self, entry: MemoryEntry) -> str:
        """条目自身所属分片（写入/更新/删除用）"""
        return self.shard_path(
            entry.type,
            tenant_id=entry.tenant_id,
            subject_id=entry.subject_id,
            org_level=entry.is_org_level,
            degraded=entry.degraded,
        )

    def _store_for_path(self, path: str) -> LongTermMemory:
        """按路径取（并缓存）既有引擎实例 —— 一个分片一个 `LongTermMemory`"""
        store = self._stores.get(path)
        if store is None:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            store = self._store_factory(db_path=path)
            self._stores[path] = store
        return store

    def _readable_shard_paths(
        self, ctx: TenancyContext, *, include_degraded: bool = False
    ) -> List[str]:
        """召回侧候选分片（**只读已存在的文件**，绝不因读操作新建空库）"""
        candidates: List[str] = []
        if ctx.has_subject:
            candidates.append(self.shard_path(MemoryType.PREFERENCE, subject_id=ctx.subject_id))
        if ctx.has_tenant:
            for mt in (MemoryType.WORKING, MemoryType.FACT, MemoryType.STRATEGY):
                candidates.append(self.shard_path(mt, tenant_id=ctx.tenant_id))
        candidates.append(self.shard_path(MemoryType.STRATEGY, org_level=True))
        if include_degraded:
            candidates.append(self.shard_path(MemoryType.FACT, degraded=True))
        return [p for p in candidates if os.path.exists(p)]

    def iter_shard_files(self) -> List[str]:
        """遍历根目录下全部分片文件（遗忘扫描 / 删除权跨租户执行用）"""
        found: List[str] = []
        identity_dir = os.path.abspath(os.path.join(self.root, "identity"))
        for dirpath, _dirnames, filenames in os.walk(self.root):
            if os.path.abspath(dirpath).startswith(identity_dir):
                continue
            for name in filenames:
                if name.endswith(".db"):
                    found.append(os.path.join(dirpath, name))
        return sorted(found)

    # ── 上下文 ──

    def _ctx(
        self,
        ctx: Any = None,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        subject_id: Optional[str] = None,
        workspace_root: str = "",
    ) -> TenancyContext:
        """归一化租户上下文（显式参数 > 传入 ``TenancyContext``/``TraceContext`` > 当前上下文）"""
        if isinstance(ctx, TenancyContext) and not any(
            x is not None for x in (tenant_id, workspace_id)
        ) and not workspace_root and subject_id is None:
            return ctx
        context = None if isinstance(ctx, TenancyContext) else ctx
        base = ctx if isinstance(ctx, TenancyContext) else None
        return resolve_tenancy(
            tenant_id=tenant_id if tenant_id is not None else (base.tenant_id if base else None),
            workspace_id=workspace_id if workspace_id is not None else (base.workspace_id if base else None),
            subject_id=subject_id if subject_id is not None else (base.subject_id if base else None),
            workspace_root=workspace_root,
            trace_id=(base.trace_id if base else ""),
            task_id=(base.task_id if base else ""),
            context=context,
        )

    # ── 审计 ──

    def _audit_event(
        self, action: str, entry: Optional[MemoryEntry], extra: Optional[Dict[str, Any]] = None,
        subject_id: str = "",
    ) -> None:
        if not self._audit:
            return
        payload = dict(entry.to_audit_leaf()) if entry is not None else {}
        if extra:
            payload.update({str(k): v for k, v in extra.items()})
        record_memory_audit(
            action,
            memory_id=(entry.id if entry is not None else ""),
            subject_id=(subject_id or (entry.subject_id if entry is not None else "")),
            payload=payload,
            pseudonymizer=self.pseudonymizer,
        )

    # ── 写入 ──

    async def write(
        self,
        content: Any,
        *,
        memory_type: Any,
        ctx: Any = None,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        subject_id: Optional[str] = None,
        workspace_root: str = "",
        scope: str = "",
        confidence: float = 0.5,
        source_task_id: str = "",
        source_capability_id: str = "",
        source_trace_id: str = "",
        ttl_seconds: Optional[float] = None,
        channel: Any = WriteChannel.USER,
        org_level: Optional[bool] = None,
        entry_id: str = "",
        extra: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> WriteResult:
        """写入一条分层记忆（**强制 tenancy**：缺租户字段默认拒绝，绝不静默落全局）

        Raises:
            MemoryWriteRejected: 隔离策略拒绝（缺租户字段 / 个人写策略层 / scope 越界）
        """
        mt = coerce_memory_type(memory_type)
        tctx = self._ctx(
            ctx, tenant_id=tenant_id, workspace_id=workspace_id,
            subject_id=subject_id, workspace_root=workspace_root,
        )
        decision = self.policy.decide_write(
            mt, tctx, channel=channel, org_level=org_level, scope=scope)
        if not decision.allowed:
            logger.warning(log_dict({
                "module_name": "memory.layered_store",
                "action": "write.rejected",
                "msg": "[layered_store] 写入被拒: type=%s reason=%s" % (mt.value, decision.reason),
            }))
            if self._audit:
                record_memory_audit(
                    "memory.write.rejected",
                    subject_id=(subject_id or tctx.subject_id),
                    payload={
                        "type": mt.value,
                        "reason": decision.reason,
                        "tenant_id": decision.tenant_id,
                    },
                    pseudonymizer=self.pseudonymizer,
                )
            raise MemoryWriteRejected(decision)

        ts = self.now() if now is None else float(now)
        text = redact_content(content)
        entry = MemoryEntry(
            id=str(entry_id or "").strip() or new_memory_id(),
            tenant_id=decision.tenant_id,
            subject_id=decision.subject_id,
            type=mt,
            content_redacted=text,
            content_hash="",
            scope=decision.scope or GLOBAL_SCOPE,
            source_task_id=source_task_id,
            confidence=confidence,
            created_at=ts,
            ttl_expires_at=expiry_for(mt, ttl_seconds=ttl_seconds, created_at=ts),
            source_capability_id=source_capability_id,
            source_trace_id=source_trace_id or tctx.trace_id,
            org_level=decision.org_level,
            degraded=decision.degraded,
            degradation_reason=("; ".join(decision.warnings) if decision.degraded else ""),
            extra=dict(extra or {}),
        )
        persisted = await self._persist(entry)
        if self._audit:
            action = "memory.write.degraded" if decision.degraded else "memory.write"
            self._audit_event(action, entry, extra={
                "type": mt.value,
                "scope": entry.scope,
                "reason": decision.reason,
                "persisted": bool(persisted),
            })
        if decision.warnings:
            for warning in decision.warnings:
                logger.warning(log_dict({
                    "module_name": "memory.layered_store",
                    "action": "write.warning",
                    "msg": "[layered_store] " + warning,
                }))
        return WriteResult(
            entry=entry,
            decision=decision,
            persisted=bool(persisted),
            shard_path=self.owner_shard_path(entry),
        )

    async def _persist(self, entry: MemoryEntry) -> bool:
        """把条目写进其所属分片（upsert；既有引擎保证 key 幂等）"""
        path = self.owner_shard_path(entry)
        store = self._store_for_path(path)
        return await store.save(
            key=entry.id,
            content=entry.content_redacted,
            importance=_importance_for(entry.confidence),
            tags=_tags_for(entry),
            sensitive=False,
            metadata=entry.to_persist_metadata(),
        )

    async def update_entry(self, entry: MemoryEntry, *, channel: Any = WriteChannel.SYSTEM) -> bool:
        """就地更新条目（`forget_candidate` / `last_hit_at` 等标记回写）

        Raises:
            MemoryWriteRejected: 目标为 org 级只读下发条目且通道非 ORG 时
        """
        path = self.owner_shard_path(entry)
        tctx = TenancyContext(
            tenant_id=entry.tenant_id, workspace_id=entry.scope_workspace_id or "",
            subject_id=entry.subject_id)
        self.policy.require_writable(entry, tctx, channel=channel)
        return await self._persist(entry)

    async def delete_entry(self, entry: MemoryEntry, *, force: bool = True) -> bool:
        """**物理删除**条目（遗忘执行用；``force=True`` 绕过既有"高重要性需审查"保护）

        遗忘是受控通道（三触发 / 删除权，均先经快照），故此处显式 force。
        """
        path = self.owner_shard_path(entry)
        if not os.path.exists(path):
            return False
        return await self._store_for_path(path).delete(entry.id, force=force)

    # ── 读取 ──

    async def _collect(
        self,
        tctx: TenancyContext,
        *,
        query: str = "",
        memory_types: Optional[Sequence[Any]] = None,
        candidate_limit: int = 200,
        include_degraded: bool = False,
    ) -> List[MemoryEntry]:
        """从候选分片收集候选条目（**不做可见性过滤**，由策略层统一判定）"""
        wanted: Optional[set] = None
        if memory_types:
            wanted = {coerce_memory_type(m).value for m in memory_types}
        collected: Dict[str, MemoryEntry] = {}
        for path in self._readable_shard_paths(tctx, include_degraded=include_degraded):
            store = self._store_for_path(path)
            entries: List[MemoryEntry] = []
            if str(query or "").strip():
                try:
                    results = await store.search(query, top_k=candidate_limit)
                except Exception as exc:  # noqa: BLE001 单个分片检索失败不影响其他分片
                    logger.warning(log_dict({
                        "module_name": "memory.layered_store",
                        "action": "recall.shard.failed",
                        "msg": "[layered_store] 分片检索失败 path=%s: %s" % (path, exc),
                    }))
                    results = []
                for result in results:
                    entry = load_layer_metadata((result.metadata or {}).get("layer"))
                    if entry is not None:
                        entries.append(entry)
            else:
                try:
                    rows = store.list_recent(limit=candidate_limit)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(log_dict({
                        "module_name": "memory.layered_store",
                        "action": "recall.shard.failed",
                        "msg": "[layered_store] 分片列举失败 path=%s: %s" % (path, exc),
                    }))
                    rows = []
                for row in rows:
                    entry = load_layer_metadata(getattr(row, "metadata", None))
                    if entry is not None:
                        entries.append(entry)
            for entry in entries:
                if wanted is not None and entry.type.value not in wanted:
                    continue
                collected.setdefault(entry.id, entry)
        return list(collected.values())

    async def recall(
        self,
        query: str = "",
        *,
        ctx: Any = None,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        subject_id: Optional[str] = None,
        workspace_root: str = "",
        limit: int = 10,
        memory_types: Optional[Sequence[Any]] = None,
        include_expired: bool = False,
        include_forget_candidates: bool = True,
        include_degraded: Optional[bool] = None,
        candidate_limit: int = 200,
        record_hits: bool = False,
    ) -> List[MemoryEntry]:
        """召回（既有检索 → 隔离可见性过滤 → §4.3 优先级排序 → 截断）

        §4.3：``project 事实 > global 偏好，同级新者胜``；P7.2-10 层序
        ``策略 > 事实 > 偏好``；P7.2-08：跨租户不可见 / 偏好随 subject 携带。
        """
        tctx = self._ctx(
            ctx, tenant_id=tenant_id, workspace_id=workspace_id,
            subject_id=subject_id, workspace_root=workspace_root,
        )
        allow_degraded = (
            self.policy.include_degraded_by_default
            if include_degraded is None else bool(include_degraded)
        )
        entries = await self._collect(
            tctx, query=query, memory_types=memory_types,
            candidate_limit=candidate_limit, include_degraded=allow_degraded,
        )
        visible = self.policy.recall(
            entries, tctx, limit=limit,
            include_expired=include_expired,
            include_forget_candidates=include_forget_candidates,
            include_degraded=allow_degraded,
            now=self.now(),
        )
        if record_hits:
            for entry in visible:
                entry.record_hit(now=self.now())
                await self.update_entry(entry)
        return visible

    async def get(
        self,
        entry_id: str,
        *,
        ctx: Any = None,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        subject_id: Optional[str] = None,
        workspace_root: str = "",
        include_expired: bool = True,
        include_forget_candidates: bool = True,
        include_degraded: bool = True,
        lookup_extra_shards: bool = True,
    ) -> Optional[MemoryEntry]:
        """按 id 取条目（可见性判定后返回；不可见等同不存在）"""
        target = str(entry_id or "").strip()
        if not target:
            return None
        tctx = self._ctx(
            ctx, tenant_id=tenant_id, workspace_id=workspace_id,
            subject_id=subject_id, workspace_root=workspace_root,
        )
        paths = self._readable_shard_paths(tctx, include_degraded=include_degraded)
        if lookup_extra_shards:
            for path in self.iter_shard_files():
                if path not in paths:
                    paths.append(path)
        for path in paths:
            try:
                row = await self._store_for_path(path).get(target)
            except Exception:  # noqa: BLE001
                continue
            if row is None:
                continue
            entry = load_layer_metadata(getattr(row, "metadata", None))
            if entry is None:
                continue
            if self.policy.is_visible(
                entry, tctx, include_expired=include_expired,
                include_forget_candidates=include_forget_candidates,
                include_degraded=include_degraded, now=self.now(),
            ):
                return entry
            return None
        return None

    async def list_entries(
        self,
        *,
        ctx: Any = None,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        subject_id: Optional[str] = None,
        workspace_root: str = "",
        memory_types: Optional[Sequence[Any]] = None,
        include_expired: bool = True,
        include_forget_candidates: bool = True,
        include_degraded: Optional[bool] = None,
        limit: Optional[int] = None,
        candidate_limit: int = 500,
    ) -> List[MemoryEntry]:
        """列出当前上下文可见的分层条目（遗忘扫描 / 删除权的候选集）"""
        tctx = self._ctx(
            ctx, tenant_id=tenant_id, workspace_id=workspace_id,
            subject_id=subject_id, workspace_root=workspace_root,
        )
        allow_degraded = (
            self.policy.include_degraded_by_default
            if include_degraded is None else bool(include_degraded)
        )
        entries = await self._collect(
            tctx, query="", memory_types=memory_types,
            candidate_limit=candidate_limit, include_degraded=allow_degraded,
        )
        visible = self.policy.recall(
            entries, tctx,
            include_expired=include_expired,
            include_forget_candidates=include_forget_candidates,
            include_degraded=allow_degraded,
            now=self.now(),
        )
        return visible[:limit] if limit is not None else visible

    async def all_entries(self, *, memory_types: Optional[Sequence[Any]] = None) -> List[MemoryEntry]:
        """枚举**全部分片**的分层条目（不做可见性过滤；遗忘/删除权跨租户扫描用）

        Note:
            这是内部治理通道（三触发扫描、被遗忘权执行），调用方必须自行按主体/租户
            约束处置范围。
        """
        wanted: Optional[set] = None
        if memory_types:
            wanted = {coerce_memory_type(m).value for m in memory_types}
        collected: Dict[str, MemoryEntry] = {}
        for path in self.iter_shard_files():
            store = self._store_for_path(path)
            try:
                rows = store.list_recent(limit=2000)
            except Exception:  # noqa: BLE001
                continue
            for row in rows:
                entry = load_layer_metadata(getattr(row, "metadata", None))
                if entry is None:
                    continue
                if wanted is not None and entry.type.value not in wanted:
                    continue
                collected.setdefault(entry.id, entry)
        return list(collected.values())

    # ── 遗忘支撑 ──

    async def mark_forget_candidate(
        self, entry: MemoryEntry, reason: str, *, persist: bool = True
    ) -> bool:
        """标记遗忘候选（§3.11 forget_candidate；不删除）"""
        entry.mark_forget_candidate(reason)
        if self._audit:
            self._audit_event("memory.forget_candidate", entry, extra={"reason": reason})
        return await self.update_entry(entry) if persist else True

    async def clear_forget_candidate(self, entry: MemoryEntry, *, persist: bool = True) -> bool:
        """撤销遗忘候选标记（来源恢复 / 成功率回升）"""
        entry.clear_forget_candidate()
        return await self.update_entry(entry) if persist else True

    def stats(self) -> Dict[str, Any]:
        """存储统计（分片数 / 条目数 / 盐表；供报告与面板复用）"""
        shards = self.iter_shard_files()
        total = 0
        for path in shards:
            try:
                total += self._store_for_path(path).get_stats().get("total_entries", 0)
            except Exception:  # noqa: BLE001
                continue
        return {
            "root": self.root,
            "shard_count": len(shards),
            "entry_count": total,
            "org_tenant": ORG_TENANT,
            "degraded_tenant": DEGRADED_TENANT,
            "audit_enabled": bool(self._audit),
            "identity": self.pseudonymizer.stats(),
        }
