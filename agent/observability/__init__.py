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

__all__ = [
    "ACTOR_AUTO", "ACTOR_HUMAN", "ACTOR_SUB_AGENT",
    "SCHEMA_VERSION", "STATUS_BLOCKED", "STATUS_ERROR", "STATUS_SUCCESS",
    "Cost", "MissingWorkspaceError", "Request", "Response", "SideEffects",
    "Tenancy", "Timing", "TraceContext", "TraceFacade", "TraceValidationError",
    "UnifiedTrace", "UnifiedTraceStore",
    "capability_reference", "capability_reference_for_trace",
    "derive_workspace_id", "generate_trace_id", "hash_content",
    "load_runtime_descriptors", "redact", "redact_then_hash",
]
