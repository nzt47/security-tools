"""Trace ID 管理 — 适配层，桥接到 monitoring.tracing

提供 generate_trace_id / get_trace_id / set_trace_id 供 audit / log_system 使用
"""
from agent.monitoring.tracing import get_trace_id, set_trace_id, TraceContext
import uuid


def generate_trace_id() -> str:
    """生成 16 位十六进制 Trace ID"""
    return uuid.uuid4().hex[:16]


__all__ = ["generate_trace_id", "get_trace_id", "set_trace_id", "TraceContext"]
