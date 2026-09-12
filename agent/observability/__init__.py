# P9 可观测性 — Trace 订阅接口
"""Observability — 性能观测与链路追踪"""

# 统一 Trace（TASK-S2-01 / v7.2 §3.4 + §2.7）：公共 Trace 门面 + TraceContext 透传
from .trace_v2 import (  # noqa: F401
    ACTOR_AUTO,
    ACTOR_HUMAN,
    ACTOR_SUB_AGENT,
    SCHEMA_VERSION,
    STATUS_BLOCKED,
    STATUS_ERROR,
    STATUS_SUCCESS,
    Cost,
    MissingWorkspaceError,
    Request,
    Response,
    SideEffects,
    Tenancy,
    Timing,
    TraceContext,
    TraceFacade,
    TraceValidationError,
    UnifiedTrace,
    UnifiedTraceStore,
    capability_reference,
    capability_reference_for_trace,
    derive_workspace_id,
    generate_trace_id,
    hash_content,
    load_runtime_descriptors,
    redact,
    redact_then_hash,
)

# events.v1 信封 + 统一事件出口（TASK-S2-03 / v7.2 §3.6）
from .events import (  # noqa: F401
    ALL_EVENT_TYPES,
    CORE_EVENT_TYPES,
    GOVERNANCE_EVENT_TYPES,
    METRIC_EVENT_TYPES,
    NINE_EVENT_TYPES,
    SCHEMA_NAME as EVENTS_SCHEMA_NAME,
    EventEnvelope,
    EventStore,
    EventType,
    build_event_id,
    emit,
    emit as emit_event,
    event_day,
    get_event_store,
    iter_events,
    read_events,
    sanitize_payload,
)

# ACR / UTC 埋点（TASK-S2-03 / v7.2 §6.1–6.6）
from .acr import (  # noqa: F401
    INTERVENTION_WEIGHTS,
    acr_daily,
    acr_snapshot,
    acr_summary,
    acr_weekly,
    classify_difficulty,
    classify_intent,
    record_approval as record_approval_metrics,
    record_escape as record_escape_metrics,
    record_intervention,
    record_task_closed,
)
from .utc import (  # noqa: F401
    coefficient_table,
    normalize_cost,
    reconcile_pricing,
    record_cost,
    resolve_anchor_model,
    utc_daily,
    utc_snapshot,
    utc_weekly,
)
from .escape import (  # noqa: F401
    detect_escapes,
    governed_write,
    record_governed_write,
)
from .model_degrade import (  # noqa: F401
    E_MODEL_DEGRADED,
    call_with_model_fallback,
    report_model_degraded,
    resolve_fallback_chain,
)

__all__ = [
    "ACTOR_AUTO", "ACTOR_HUMAN", "ACTOR_SUB_AGENT",
    "SCHEMA_VERSION", "STATUS_BLOCKED", "STATUS_ERROR", "STATUS_SUCCESS",
    "Cost", "MissingWorkspaceError", "Request", "Response", "SideEffects",
    "Tenancy", "Timing", "TraceContext", "TraceFacade", "TraceValidationError",
    "UnifiedTrace", "UnifiedTraceStore",
    "capability_reference", "capability_reference_for_trace",
    "derive_workspace_id", "generate_trace_id", "hash_content",
    "load_runtime_descriptors", "redact", "redact_then_hash",
    # events.v1
    "ALL_EVENT_TYPES", "CORE_EVENT_TYPES", "GOVERNANCE_EVENT_TYPES",
    "METRIC_EVENT_TYPES", "NINE_EVENT_TYPES",
    "EVENTS_SCHEMA_NAME", "EventEnvelope", "EventStore", "EventType",
    "build_event_id", "emit", "emit_event", "event_day", "get_event_store",
    "iter_events", "read_events", "sanitize_payload",
    # ACR / UTC / escape / degrade
    "INTERVENTION_WEIGHTS", "acr_daily", "acr_snapshot", "acr_summary", "acr_weekly",
    "classify_difficulty", "classify_intent", "record_approval_metrics",
    "record_escape_metrics", "record_intervention", "record_task_closed",
    "coefficient_table", "normalize_cost", "reconcile_pricing", "record_cost",
    "resolve_anchor_model", "utc_daily", "utc_snapshot", "utc_weekly",
    "detect_escapes", "governed_write", "record_governed_write",
    "E_MODEL_DEGRADED", "call_with_model_fallback", "report_model_degraded",
    "resolve_fallback_chain",
]
