"""分身记忆 brokered 档 —— 母体代管的**只读、限定域**记忆上下文

【任务定位】
    P3「分身记忆 brokered 档」：分身侧**仍然没有任何记忆工具**（§5.7 机制 3 硬禁不变，
    见 agent/security/actor_matrix.py / agent/subagent/toolset.py）。母体（云枢）
    按 tenancy 从既有只读入口 LayeredMemoryStore.recall 取一段**限定域**的记忆，
    渲染成**一条**带来源/域标注的约束行，注入到委派契约的 **②约束**（正文）；
    记忆 id 与 tenancy 标识只进 ctx.metadata。

【不易（三条边界）】
    1. **只读**：本模块只调 recall，绝不写/存/删 —— 分身不产生记忆副作用。
    2. **只进 ②约束**：记忆正文只出现在 ②约束（进而出现在 task_file 的 constraints），
       **绝不进** system prompt，也**绝不进** ③已有成果（那是"引用"不是正文）。
    3. **fail-soft**：取不到 / 无事件循环 / recall 抛错 / 域越界 ⇒ 一律返回
       degraded 且 text=''，**绝不抛异常、绝不阻断委派**。

【同步包装】
    LayeredMemoryStore.recall 是 async；执行器在同步栈里被调用（HTTP 同步请求 /
    线程池 worker）。default_recall_fn() 用 asyncio.run 包一层；若当前线程已有
    运行中的事件循环（或依赖导入失败），如实降级而不阻断。

【域越界守卫】
    配置里的 scope.workspace_id 与委派上下文携带的 workspace 标识不一致 ⇒
    degraded='scope_workspace_mismatch' 且不注入（宁可少注入，不可串租户）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_CONSTRAINT_CHARS",
    "PER_ENTRY_CHARS",
    "SCOPE_KEYS",
    "BrokeredContext",
    "assemble_brokered_context",
    "default_recall_fn",
    "render_constraint",
]

#: 默认最多取几条记忆（注入的是"一段"上下文，不是整库）
DEFAULT_LIMIT = 5

#: 约束行正文总上限（防止一段记忆把 ②约束 撑爆）
MAX_CONSTRAINT_CHARS = 1200

#: 单条记忆正文上限
PER_ENTRY_CHARS = 400

#: scope 里允许出现的键（其余键视为配置写错 —— 不静默忽略）
SCOPE_KEYS: Tuple[str, ...] = (
    "tenant_id", "workspace_id", "subject_id", "workspace_root",
    "memory_types", "limit",
)


@dataclass(frozen=True)
class BrokeredContext:
    """一次 brokered 记忆取用的结果

    Attributes:
        text: 渲染进 ②约束 的**记忆正文**（空 = 未取到 / 降级，调用方不得注入）。
        memory_ids: 命中条目的 id（**只进 metadata**，不进约束正文）。
        tenancy: 归一化租户上下文（**只进 metadata**）。
        provider: 记忆来源标识（如 layered）。
        degraded: 降级原因（空 = 正常；非空 ⇒ text 必为空）。
    """

    text: str = ""
    memory_ids: Tuple[str, ...] = ()
    tenancy: Mapping[str, str] = field(default_factory=dict)
    provider: str = ""
    degraded: str = ""

    @property
    def ok(self) -> bool:
        """是否真的取到可注入的正文"""
        return bool(str(self.text or "").strip())

    def to_dict(self) -> Dict[str, Any]:
        """**不含正文**的项目（审计/回投用）"""
        return {
            "provider": self.provider,
            "memory_count": len(self.memory_ids),
            "degraded": self.degraded,
        }


def default_recall_fn() -> Callable[..., List[Any]]:
    """构造默认 recall 可调用：把 async 的 LayeredMemoryStore.recall 同步化

    返回的 callable 签名（与 assemble_brokered_context 约定一致）::

        recall_fn(query, *, tenant_id, workspace_id, subject_id,
                  workspace_root, limit, memory_types) -> list[MemoryEntry]

    【为什么在函数内导入】agent.memory.layered_store 会拉 SQLite/身份盐表等，
    本模块被执行器惰性引用；把重依赖压到函数体内，避免 import 期副作用。
    """

    def _recall(
        query: str = "",
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        subject_id: Optional[str] = None,
        workspace_root: str = "",
        limit: int = DEFAULT_LIMIT,
        memory_types: Optional[Sequence[Any]] = None,
    ) -> List[Any]:
        import asyncio

        # 当前线程已有运行中的事件循环 ⇒ asyncio.run 会抛；提前给出可读原因
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "brokered 记忆取用需要同步执行，但当前线程已有运行中的事件循环")

        from agent.memory.layered_store import LayeredMemoryStore

        store = LayeredMemoryStore()
        return asyncio.run(store.recall(
            query, tenant_id=tenant_id, workspace_id=workspace_id,
            subject_id=subject_id, workspace_root=workspace_root,
            limit=int(limit), memory_types=memory_types))

    return _recall


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _entry_text(entry: Any) -> str:
    for attr in ("content_redacted", "content"):
        value = getattr(entry, attr, None)
        if value:
            return str(value).strip()
    return ""


def _degraded(reason: str, *, provider: str = "", tenancy: Optional[Mapping[str, str]] = None,
              memory_ids: Sequence[str] = ()) -> BrokeredContext:
    return BrokeredContext(text="", memory_ids=tuple(memory_ids),
                           tenancy=dict(tenancy or {}), provider=provider,
                           degraded=str(reason or "degraded"))


def assemble_brokered_context(
    query: str = "",
    *,
    scope: Optional[Mapping[str, Any]] = None,
    recall_fn: Optional[Callable[..., List[Any]]] = None,
    limit: Optional[int] = None,
    provider: str = "layered",
    memory_types: Optional[Sequence[Any]] = None,
    expected_tenant_id: str = "",
    expected_workspace_id: str = "",
    expected_subject_id: str = "",
) -> BrokeredContext:
    """按 scope 取一段只读、限定域的记忆上下文（**同步、永不抛**）

    Args:
        query: 召回查询（缺省空串 = 列举最近条目）。
        scope: 配置里的 memory_scope（可选键见 SCOPE_KEYS）。非 Mapping / 未知键
            ⇒ 降级（不注入），不抛异常。
        recall_fn: 注入的同步 recall（测试用）；缺省 default_recall_fn()。
        limit: 覆盖条数上限。
        provider: 记忆来源标识（只进 metadata）。
        memory_types: 限定记忆层（也可写在 scope.memory_types）。
        expected_tenant_id / expected_workspace_id / expected_subject_id:
            委派上下文自带的 tenancy 标识，用于**域越界**守卫与缺省补齐。

    Returns:
        BrokeredContext；任何异常路径都收敛为 degraded + 空正文。
    """
    provider_label = _clean(provider) or "layered"
    if scope is not None and not isinstance(scope, Mapping):
        return _degraded("invalid_scope_type", provider=provider_label)
    scope_map: Mapping[str, Any] = scope or {}
    unknown = [k for k in scope_map.keys() if k not in SCOPE_KEYS]
    if unknown:
        return _degraded("unknown_scope_key:%s" % ",".join(sorted(str(k) for k in unknown)),
                         provider=provider_label)

    scope_ws = _clean(scope_map.get("workspace_id"))
    expected_ws = _clean(expected_workspace_id)
    # ── 域越界守卫：配置声明的 workspace 与委派上下文不一致 ⇒ 拒绝注入 ──
    if scope_ws and expected_ws and scope_ws != expected_ws:
        logger.warning(
            "[MemoryBroker] scope.workspace_id=%s 与委派 workspace=%s 不一致，"
            "拒绝注入（P7.2-08 不串租户）", scope_ws, expected_ws)
        return _degraded("scope_workspace_mismatch", provider=provider_label)

    # ── tenancy 归一化：显式 scope 优先，缺项由委派上下文补齐 ──
    try:
        from agent.memory.tenancy import resolve_tenancy

        tctx = resolve_tenancy(
            tenant_id=_clean(scope_map.get("tenant_id")) or _clean(expected_tenant_id) or None,
            workspace_id=scope_ws or expected_ws or None,
            subject_id=_clean(scope_map.get("subject_id")) or _clean(expected_subject_id) or None,
            workspace_root=_clean(scope_map.get("workspace_root")),
            use_current_context=False,
        )
    except Exception as e:  # noqa: BLE001 tenancy 不可用 ⇒ 降级，不阻断委派
        logger.warning("[MemoryBroker] tenancy 解析失败: %s", e)
        return _degraded("tenancy_unavailable", provider=provider_label)

    tenancy = {k: str(v) for k, v in tctx.as_dict().items()}
    if not (tctx.has_tenant or tctx.has_workspace or tctx.has_subject):
        # 无任何限定域 ⇒ 不敢取（取到什么都不确定是"谁的"）
        return _degraded("empty_scope", provider=provider_label, tenancy=tenancy)

    if limit is None:
        try:
            limit = int(scope_map.get("limit"))
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
    limit = max(1, min(50, int(limit)))
    if memory_types is None:
        memory_types = scope_map.get("memory_types")

    try:
        fn = recall_fn or default_recall_fn()
        entries = list(fn(
            query, tenant_id=tctx.tenant_id or None, workspace_id=tctx.workspace_id or None,
            subject_id=tctx.subject_id or None,
            workspace_root=_clean(scope_map.get("workspace_root")),
            limit=limit, memory_types=memory_types) or [])
    except Exception as e:  # noqa: BLE001 fail-soft：取不到就如实降级，绝不阻断委派
        logger.warning("[MemoryBroker] 记忆召回失败（降级，不阻断委派）: %s", e)
        return _degraded("recall_failed:%s" % type(e).__name__,
                         provider=provider_label, tenancy=tenancy)

    parts: List[str] = []
    ids: List[str] = []
    for entry in entries[:limit]:
        entry_id = _clean(getattr(entry, "id", ""))
        if entry_id:
            ids.append(entry_id)
        text = _entry_text(entry)
        if text:
            parts.append(text[:PER_ENTRY_CHARS])
    text = "；".join(parts)[:MAX_CONSTRAINT_CHARS].strip()
    if not text:
        return _degraded("empty_recall", provider=provider_label,
                         tenancy=tenancy, memory_ids=ids)
    return BrokeredContext(text=text, memory_ids=tuple(ids), tenancy=tenancy,
                           provider=provider_label, degraded="")


def render_constraint(bc: Optional[BrokeredContext]) -> Optional[str]:
    """把取到的记忆渲染成**一条**带来源/域标注的 ②约束 行

    形如 [记忆 域=<tenant>/<workspace>] <正文>。未取到正文 ⇒ None（调用方不得注入）。
    memory_ids 与 tenancy **不在这里**出现 —— 它们只进 metadata。
    """
    text = str(getattr(bc, "text", "") or "").strip()
    if not text:
        return None
    tenancy = dict(getattr(bc, "tenancy", None) or {})
    tenant = _clean(tenancy.get("tenant_id"))
    workspace = _clean(tenancy.get("workspace_id"))
    subject = _clean(tenancy.get("subject_id"))
    if tenant or workspace:
        domain = "%s/%s" % (tenant or "-", workspace or "-")
    else:
        domain = subject or "-"
    return "[记忆 域=%s] %s" % (domain, text)
