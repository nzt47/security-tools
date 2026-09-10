"""模型降级事件（TASK-S2-03 步骤 4 / v7.2 P7.1-18 第 9 事件 / §11.6.0 E_MODEL_DEGRADED）

v7.2 要求 `model.degraded` 作为 events.v1 的**第 9 个事件**在模型降级链或错误处理处
触发，载荷为 ``{from, to, reason}``。云枢现状（审计报告 §2.4 与 S2-02 盘点结论）：
**没有显式的模型降级链**——`memory/llm_service.LLMService` 只有「同模型重试」，
`agent/model_router` 只做「按复杂度选模型」，`graceful_degrade` 有通用组件降级但未归一
`E_MODEL_DEGRADED`。因此本模块：

1. **归一错误码**：定义 ``E_MODEL_DEGRADED``（§11.6.0 错误码归一化），并始终在
   主模型失败收口处 emit ``model.degraded {from, to, reason, error_code}``；
2. **提供可用的降级链**：``resolve_fallback_chain()``（env ``CP_MODEL_FALLBACK_CHAIN``
   > ``config.yaml:llm.fallback_chain`` > 文档化默认链）与 ``call_with_model_fallback()``，
   让 `to` 是**真实候选模型**而不是占位串；
3. **分级实施（P4）**：`model.degraded` 事件**默认始终发射**（纯观测，零行为变更）；
   「真的切到降级模型」由 ``CP_MODEL_FALLBACK_ENABLED``（**默认 0**）显式开启，
   避免在数据/可观测层任务里静默改变生产链路行为。

诚实口径：默认配置下 ``fallback_attempted=False``——事件如实标注「候选是谁、是否真的
切换过」，不把「候选」冒充「已降级」。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from agent.observability import events as ev
from agent.observability.events import (
    ACTOR_AUTO,
    EV_MODEL_DEGRADED,
    EventEnvelope,
    EventStore,
    emit,
)

logger = logging.getLogger("agent.observability.model_degrade")

#: §11.6.0 归一化错误码
E_MODEL_DEGRADED = "E_MODEL_DEGRADED"

ENV_FALLBACK_CHAIN = "CP_MODEL_FALLBACK_CHAIN"
ENV_FALLBACK_ENABLED = "CP_MODEL_FALLBACK_ENABLED"
ENV_PRIMARY_MODEL = "CP_UTC_ANCHOR_MODEL"

#: 文档化默认降级链（取自 `MODEL_COSTS` 已计价模型：主力失败 → 更便宜的可用模型）
DEFAULT_FALLBACK_CHAIN: Tuple[str, ...] = ("gpt-4o-mini", "gpt-3.5-turbo")

#: 无任何可用候选时的占位（显式标注，不臆造模型名）
FALLBACK_NONE = ""

_CHAIN_CACHE: Dict[str, Any] = {}


def fallback_enabled() -> bool:
    """是否允许**实际切换**到降级模型（`CP_MODEL_FALLBACK_ENABLED`，默认 0）"""
    return str(os.getenv(ENV_FALLBACK_ENABLED, "0")).strip().lower() in (
        "1", "true", "yes", "on")


def resolve_fallback_chain(chain: Optional[Iterable[str]] = None
                           ) -> Tuple[List[str], str]:
    """解析降级链 → ``(models, source)``；source ∈ env/config/default/explicit"""
    if chain is not None:
        return ([str(m).strip() for m in chain if str(m).strip()], "explicit")
    cached = _CHAIN_CACHE.get("chain")
    if cached:
        return (list(cached[0]), cached[1])
    raw = str(os.getenv(ENV_FALLBACK_CHAIN) or "").strip()
    if raw:
        resolved = ([m.strip() for m in raw.replace(";", ",").split(",") if m.strip()], "env")
    else:
        from_config, ok = _config_fallback_chain()
        resolved = (from_config, "config.yaml") if ok else (list(DEFAULT_FALLBACK_CHAIN),
                                                            "default")
    _CHAIN_CACHE["chain"] = (list(resolved[0]), resolved[1])
    return resolved


def _config_fallback_chain() -> Tuple[List[str], bool]:
    try:
        import yaml
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(root, "config.yaml")
        if not os.path.exists(path):
            return [], False
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        raw = (data.get("llm") or {}).get("fallback_chain")
        if isinstance(raw, (list, tuple)) and raw:
            return [str(m).strip() for m in raw if str(m).strip()], True
        if isinstance(raw, str) and raw.strip():
            return [m.strip() for m in raw.split(",") if m.strip()], True
    except Exception as e:  # noqa: BLE001 配置不可读 → 默认链
        logger.debug("config.yaml fallback_chain 读取失败（用默认链）: %s", e)
    return [], False


def reset_chain_cache() -> None:
    """清空解析缓存（**测试/配置热加载**用）"""
    _CHAIN_CACHE.clear()


def next_fallback_model(model: str, chain: Optional[Iterable[str]] = None) -> str:
    """给定当前模型 → 降级链中的下一个候选（无候选 → ``""``）"""
    models, _ = resolve_fallback_chain(chain)
    current = str(model or "").strip()
    for candidate in models:
        if candidate and candidate != current:
            return candidate
    return FALLBACK_NONE


def report_model_degraded(*, from_model: str, reason: str,
                          to_model: Optional[str] = None,
                          error_code: str = E_MODEL_DEGRADED,
                          actor: str = ACTOR_AUTO, task_id: str = "",
                          correlation_id: str = "",
                          fallback_source: str = "", fallback_attempted: bool = False,
                          fallback_error: str = "", provider: str = "",
                          chain: Optional[Iterable[str]] = None,
                          extra: Optional[Dict[str, Any]] = None,
                          store: Optional[EventStore] = None,
                          ts: Optional[str] = None) -> Optional[EventEnvelope]:
    """emit ``model.degraded {from, to, reason}``（P7.1-18 第 9 事件）

    ``to_model`` 语义：``None`` = 未指定（由 ``next_fallback_model`` 解析真实候选）；
    显式 ``""`` = 调用方明确「无后续候选」（链尾），此时**不**回填候选。
    - 幂等键 = ``model.degraded:{from}->{to}:{reason}``（同一降级原因重复上报折叠）。
    """
    if to_model is None:
        to_model = next_fallback_model(from_model, chain)
    if not fallback_source:
        fallback_source = resolve_fallback_chain(chain)[1]
    fields = ev.trace_fields()
    cid = correlation_id or fields.get("trace_id") or ""
    payload: Dict[str, Any] = {
        "from": str(from_model or ""),
        "to": str(to_model or ""),
        "reason": str(reason or "")[:400],
        "error_code": str(error_code or E_MODEL_DEGRADED),
        "fallback_source": str(fallback_source or ""),
        "fallback_available": bool(to_model),
        "fallback_attempted": bool(fallback_attempted),
        "fallback_error": str(fallback_error or "")[:200],
        "provider": str(provider or ""),
        "task_id": str(task_id or fields.get("task_id") or ""),
        "workspace_id": fields.get("workspace_id") or "",
        "subject_id": fields.get("subject_id") or "",
    }
    if extra:
        payload.update(extra)
    key = f"model.degraded:{from_model}->{to_model}:{reason}"
    return emit(EV_MODEL_DEGRADED, payload, actor=actor, correlation_id=cid,
                idempotency_key=key, ts=ts, store=store)


def handle_primary_failure(*, model: str, reason: str,
                           retry: Optional[Callable[[str], Any]] = None,
                           chain: Optional[Iterable[str]] = None,
                           allow_retry: Optional[bool] = None,
                           provider: str = "", task_id: str = "",
                           correlation_id: str = "",
                           actor: str = ACTOR_AUTO,
                           store: Optional[EventStore] = None,
                           ts: Optional[str] = None) -> Dict[str, Any]:
    """主模型失败收口：**总是** emit ``model.degraded``；按开关决定是否真的切换

    Args:
        model: 失败的主力模型。
        reason: 失败原因（异常类型 + 摘要）。
        retry: ``retry(candidate_model) -> result``；仅在允许切换且存在候选时调用。
        allow_retry: 覆盖 ``fallback_enabled()``（测试/显式调用用）。

    Returns:
        ``{"degraded", "from", "to", "attempted", "succeeded", "result", "error", "event"}``
    """
    models, source = resolve_fallback_chain(chain)
    candidate = next_fallback_model(model, models)
    may_retry = fallback_enabled() if allow_retry is None else bool(allow_retry)
    attempted = False
    result: Any = None
    error = ""
    if candidate and retry is not None and may_retry:
        attempted = True
        try:
            result = retry(candidate)
        except Exception as e:  # noqa: BLE001 降级候选同样失败 → 如实记录
            error = f"{type(e).__name__}: {e}"
            logger.warning("降级候选模型同样失败 %s → %s: %s", model, candidate, e)
    envelope = report_model_degraded(
        from_model=model, reason=reason, to_model=candidate, provider=provider,
        fallback_source=source, fallback_attempted=attempted,
        fallback_error=error, task_id=task_id, correlation_id=correlation_id,
        actor=actor, chain=models, store=store, ts=ts)
    return {
        "degraded": True,
        "from": str(model or ""),
        "to": str(candidate or ""),
        "attempted": attempted,
        "succeeded": bool(attempted and not error),
        "result": result,
        "error": error,
        "event": envelope,
        "chain_source": source,
    }


def call_with_model_fallback(call_fn: Callable[[str], Any],
                             models: Optional[Sequence[str]] = None, *,
                             provider: str = "", task_id: str = "",
                             correlation_id: str = "",
                             actor: str = ACTOR_AUTO,
                             store: Optional[EventStore] = None,
                             ts: Optional[str] = None) -> Any:
    """按降级链依次尝试 `call_fn(model)`；每次切换 emit ``model.degraded``

    全部候选失败时抛最后一个异常（不吞错）。供 §4.7 模型降级链 / S4-03 熔断复用。

    ``models`` 显式传入空列表 → 直接报错（不静默回退到默认链，避免"以为在用 A 链
    实际用了 B 链"）；``None`` 才走 `resolve_fallback_chain()`。
    """
    if models is not None:
        chain = [str(m).strip() for m in models if str(m).strip()]
    else:
        chain = [str(m) for m in resolve_fallback_chain()[0] if str(m)]
    if not chain:
        raise ValueError("降级链为空（models/CP_MODEL_FALLBACK_CHAIN/config 均未提供）")
    last_error: Optional[Exception] = None
    for index, model in enumerate(chain):
        try:
            return call_fn(model)
        except Exception as e:  # noqa: BLE001 逐个候选降级
            last_error = e
            nxt = chain[index + 1] if index + 1 < len(chain) else FALLBACK_NONE
            report_model_degraded(
                from_model=model, to_model=nxt,
                reason=f"{type(e).__name__}: {e}", provider=provider,
                fallback_source="chain", fallback_attempted=bool(nxt),
                task_id=task_id, correlation_id=correlation_id, actor=actor,
                chain=chain, store=store, ts=ts)
    assert last_error is not None
    raise last_error


def degrade_summary(*, day: Optional[str] = None, since: Optional[str] = None,
                    until: Optional[str] = None, directory: Optional[str] = None
                    ) -> Dict[str, Any]:
    """`model.degraded` 事件汇总（按 from→to 边计数，供面板「降级链拓扑」）"""
    rows = ev.iter_events(types=(EV_MODEL_DEGRADED,), day=day, since=since,
                          until=until, directory=directory)
    edges: Dict[str, int] = {}
    reasons: Dict[str, int] = {}
    attempted = 0
    succeeded = 0
    for env in rows:
        payload = env.payload or {}
        edge = f"{payload.get('from') or '?'} -> {payload.get('to') or '?'}"
        edges[edge] = edges.get(edge, 0) + 1
        reason = str(payload.get("reason") or "")
        head = reason.split(":")[0] if reason else ""
        reasons[head] = reasons.get(head, 0) + 1
        if payload.get("fallback_attempted"):
            attempted += 1
        if payload.get("fallback_attempted") and not payload.get("fallback_error"):
            succeeded += 1
    return {
        "total": len(rows),
        "edges": dict(sorted(edges.items())),
        "reasons": dict(sorted(reasons.items())),
        "fallback_attempted": attempted,
        "fallback_succeeded": succeeded,
        "error_code": E_MODEL_DEGRADED,
        "chain": {"models": resolve_fallback_chain()[0],
                  "source": resolve_fallback_chain()[1],
                  "enabled": fallback_enabled()},
    }


__all__ = [
    "E_MODEL_DEGRADED", "ENV_FALLBACK_CHAIN", "ENV_FALLBACK_ENABLED",
    "ENV_PRIMARY_MODEL", "DEFAULT_FALLBACK_CHAIN", "FALLBACK_NONE",
    "fallback_enabled", "resolve_fallback_chain", "next_fallback_model",
    "reset_chain_cache", "report_model_degraded", "handle_primary_failure",
    "call_with_model_fallback", "degrade_summary",
]
