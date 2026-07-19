"""技能管理系统可观测性

遵循项目硬约束:
    - 结构化日志: JSON 格式，必含 trace_id / module_name / action / duration_ms
    - 埋点预留: 关键交互点调用 trackEvent()
    - 业务指标: 复用 agent.monitoring.business_metrics.BusinessMetricsCollector
      指标命名: yunshu_skill_<动作>
"""

from __future__ import annotations
import json
import time
import uuid
import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger("agent.skills_mgmt")

# 尝试引入业务指标收集器（按硬约束要求）；失败则降级为 no-op
try:
    from agent.monitoring.business_metrics import BusinessMetricsCollector
    _metrics = BusinessMetricsCollector()
    _METRICS_AVAILABLE = True
except Exception:  # noqa: BLE001
    _metrics = None
    _METRICS_AVAILABLE = False

# [不易] 可观测性字段防御性约束：单条 trace 的 retrieved_chunks 最多记录 50 项，
# 超出自动截断并标记，防止单条日志过大拖垮日志管道。
_MAX_RETRIEVED_CHUNKS = 50


def _sanitize_observability_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """[变易] 防御性清洗可观测性 payload：截断过大的 retrieved_chunks 字段。

    超过 _MAX_RETRIEVED_CHUNKS 项的 retrieved_chunks 自动截断，
    并追加 retrieved_chunks_truncated=True 标记（任务要求 truncated 标记，
    此处用更具体的字段名避免与业务返回值中的 truncated 字段冲突，守不易）。

    缺失或非 list 类型时原样返回，不报错（防御性）。
    """
    if not isinstance(payload, dict):
        return payload
    chunks = payload.get("retrieved_chunks")
    if isinstance(chunks, list) and len(chunks) > _MAX_RETRIEVED_CHUNKS:
        # [Observability:fill] 截断触发：记录原始数量与上限，便于排查"是否打标"
        logger.info(
            "[Observability:fill] stage=sanitize.truncate | original_count=%d | "
            "max=%d | truncated=true",
            len(chunks), _MAX_RETRIEVED_CHUNKS,
        )
        return {
            **payload,
            "retrieved_chunks": chunks[:_MAX_RETRIEVED_CHUNKS],
            "retrieved_chunks_truncated": True,
            "retrieved_chunks_original_count": len(chunks),
        }
    return payload


def _emit_structured_log(action: str, *, trace_id: Optional[str] = None,
                         duration_ms: float = 0.0, level: str = "info",
                         **payload: Any) -> None:
    """输出结构化 JSON 日志 (遵循可观测性强制约束)"""
    record = {
        "trace_id": trace_id or str(uuid.uuid4()),
        "module_name": "skills_mgmt",
        "action": action,
        "duration_ms": round(duration_ms, 2),
        "ts": time.time(),
        **payload,
    }
    getattr(logger, level, logger.info)(json.dumps(record, ensure_ascii=False, default=str))


@contextmanager
def traced_action(action: str, *, trace_id: Optional[str] = None,
                  **payload: Any) -> Iterator[Dict[str, Any]]:
    """追踪一个业务动作的耗时与结果

    用法:
        with traced_action("skill_create", skill_id="foo") as ctx:
            ...
            ctx["result"] = "ok"
    """
    tid = trace_id or str(uuid.uuid4())
    ctx: Dict[str, Any] = {"trace_id": tid, "payload": payload}
    t0 = time.time()
    # 保留键：与 _emit_structured_log 的显式参数同名，展开前必须过滤
    _RESERVED = {"action", "trace_id", "duration_ms", "level", "module_name",
                 "status", "error", "error_type"}
    safe = {k: v for k, v in payload.items() if k not in _RESERVED}
    # [变易] 入口即清洗：防止 retrieved_chunks 过大拖垮 start 日志
    safe = _sanitize_observability_payload(safe)
    try:
        _emit_structured_log(f"{action}.start", trace_id=tid, duration_ms=0.0,
                             **safe)
        # [Observability:fill] traced_action 入口：retrieved_chunks 填充时机
        _rc = safe.get("retrieved_chunks")
        _rc_brief = (f"count={len(_rc)}" if isinstance(_rc, list)
                     else f"type={type(_rc).__name__}" if _rc is not None
                     else "absent")
        logger.info(
            "[Observability:fill] stage=traced_action.start | trace_id=%s | "
            "action=%s | payload_keys=%s | retrieved_chunks=%s",
            tid, action, list(safe.keys()), _rc_brief,
        )
        yield ctx
        elapsed = (time.time() - t0) * 1000
        # 合并 payload 与 ctx，过滤保留键（safe_merged 命名标记已过滤）
        safe_merged = {**safe}
        for k, v in ctx.items():
            if k in _RESERVED or k in ("payload",):
                continue
            safe_merged[k] = v
        # [变易] end 日志同样清洗：ctx 中可能新设置 retrieved_chunks
        safe_merged = _sanitize_observability_payload(safe_merged)
        # [Observability:fill] traced_action 出口：retrieved_chunks 填充时机
        _rc_end = safe_merged.get("retrieved_chunks")
        _rc_end_brief = (f"count={len(_rc_end)}" if isinstance(_rc_end, list)
                         else f"type={type(_rc_end).__name__}" if _rc_end is not None
                         else "absent")
        logger.info(
            "[Observability:fill] stage=traced_action.end | trace_id=%s | "
            "action=%s | merged_keys=%s | retrieved_chunks=%s",
            tid, action, list(safe_merged.keys()), _rc_end_brief,
        )
        _emit_structured_log(f"{action}.end", trace_id=tid, duration_ms=elapsed,
                             status="ok", **safe_merged)
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        _emit_structured_log(f"{action}.error", trace_id=tid, duration_ms=elapsed,
                             status="error", error=str(e), error_type=type(e).__name__,
                             level="error", **safe)
        raise


def track_event(event_name: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """前端埋点占位符 (前后端通用)

    后端调用时仅记录日志；前端通过同名函数上传到分析系统。
    失败不影响主流程（按硬约束要求）。
    """
    try:
        _emit_structured_log("track_event", event_name=event_name,
                             payload=payload or {})
    except Exception:  # noqa: BLE001
        logger.debug("track_event 失败，已忽略", exc_info=True)


def emit_metric(name: str, *, value: float = 1.0, labels: Optional[Dict[str, str]] = None,
                kind: str = "counter") -> None:
    """发射业务指标

    Args:
        name: 指标名，遵循 yunshu_skill_<动作> 命名
        value: 计数或耗时
        labels: 必须含 success/failure 标签（按硬约束）
        kind: counter / histogram / gauge
    """
    if not _METRICS_AVAILABLE:
        return
    try:
        labels = labels or {}
        if "success" not in labels and "failure" not in labels:
            labels = {**labels, "success": "true"}
        if kind == "counter" and hasattr(_metrics, "inc_counter"):
            _metrics.inc_counter(name, labels=labels, value=value)
        elif kind == "histogram" and hasattr(_metrics, "observe_histogram"):
            _metrics.observe_histogram(name, value=value, labels=labels)
        elif kind == "gauge" and hasattr(_metrics, "set_gauge"):
            _metrics.set_gauge(name, value=value, labels=labels)
    except Exception:  # noqa: BLE001  埋点失败不影响主流程
        logger.debug("emit_metric 失败: %s", name, exc_info=True)


# ════════════════════════════════════════════════════════════
#  全链路可观测性扩展字段（retrieved_chunks / eval_score 等）
#  [不易] 接口签名保持可选，缺失不报错；metrics 发射失败不影响主流程
# ════════════════════════════════════════════════════════════

def persist_observability_span(*, trace_id: Optional[str] = None,
                               **fields: Any) -> None:
    """[变易] 将可观测性扩展字段持久化到 trace 存储（span attributes）。

    优先调用 agent.monitoring.tracing.record_span_attributes 写入结构化日志；
    失败时降级为本地结构化日志，绝不抛错（守"metrics 发射失败不影响主流程"）。
    """
    try:
        from agent.monitoring.tracing import record_span_attributes
        record_span_attributes(trace_id=trace_id, **fields)
        # [Observability] INFO 级别：span 持久化成功，打印字段概要（不含 chunks 全量，避免噪音）
        chunks_val = fields.get("retrieved_chunks")
        chunks_brief = (
            f"count={len(chunks_val)}" if isinstance(chunks_val, list)
            else f"type={type(chunks_val).__name__}" if chunks_val is not None
            else "absent"
        )
        logger.info(
            "[Observability] span persisted | trace_id=%s | fields=%s | "
            "retrieved_chunks=%s",
            trace_id, list(fields.keys()), chunks_brief,
        )
    except Exception:  # noqa: BLE001
        try:
            _emit_structured_log(
                "observability_span.fallback",
                trace_id=trace_id,
                span_fields=_sanitize_observability_payload(dict(fields)),
            )
        except Exception:  # noqa: BLE001
            logger.debug("persist_observability_span 失败", exc_info=True)


def emit_retrieval_precision_metric(*, k: int, hits: int,
                                    precision: float,
                                    trace_id: Optional[str] = None) -> None:
    """[变易] 发射 Precision@K 指标（histogram，labels={k}）。

    Args:
        k: Top-K 阈值
        hits: 命中数
        precision: 精确率（hits/k）
        trace_id: 关联追踪ID
    """
    try:
        emit_metric("yunshu_skill_retrieval_precision_at_k",
                    value=precision, kind="histogram",
                    labels={"k": str(k)})
        _emit_structured_log(
            "retrieval_precision_at_k",
            trace_id=trace_id,
            retrieval_precision_at_k={"k": k, "hits": hits, "precision": precision},
        )
    except Exception:  # noqa: BLE001
        logger.debug("emit_retrieval_precision_metric 失败", exc_info=True)


def emit_eval_score_metric(skill_id: str, eval_score: Dict[str, Any],
                           *, trace_id: Optional[str] = None) -> None:
    """[变易] 发射端到端评估得分指标并持久化到 span。

    eval_score 期望结构: {task_success, instruction_followed,
                          hallucination_detected, score}
    所有字段缺失不报错（防御性）。

    发射的指标:
        - yunshu_skill_eval_score (histogram, labels={skill_id, task_success})
        - yunshu_skill_hallucination_total (counter, labels={skill_id})
    """
    if not isinstance(eval_score, dict):
        return
    try:
        # [Observability:fill] emit_eval_score_metric 入口：eval_score 填充时机
        logger.info(
            "[Observability:fill] stage=emit_eval_score_metric.enter | "
            "skill_id=%s | trace_id=%s | eval_score_keys=%s | task_success=%s | "
            "hallucination_detected=%s | score=%s",
            skill_id, trace_id, list(eval_score.keys()),
            eval_score.get("task_success"),
            eval_score.get("hallucination_detected"),
            eval_score.get("score"),
        )
        task_success = bool(eval_score.get("task_success", False))
        hallucination_detected = bool(eval_score.get("hallucination_detected", False))
        score = float(eval_score.get("score", 0.0))

        emit_metric("yunshu_skill_eval_score",
                    value=score, kind="histogram",
                    labels={"skill_id": skill_id,
                            "task_success": str(task_success).lower()})
        if hallucination_detected:
            emit_metric("yunshu_skill_hallucination_total",
                        value=1, kind="counter",
                        labels={"skill_id": skill_id})

        # 同步持久化到 trace span，供后续 Precision@K/幻觉率分析
        persist_observability_span(
            trace_id=trace_id,
            skill_id=skill_id,
            eval_score=eval_score,
            task_success=task_success,
            hallucination_detected=hallucination_detected,
        )
        _emit_structured_log(
            "eval_score.recorded",
            trace_id=trace_id,
            skill_id=skill_id,
            eval_score=eval_score,
        )
    except Exception:  # noqa: BLE001
        logger.debug("emit_eval_score_metric 失败: skill_id=%s", skill_id, exc_info=True)


def report_retrieval_observability(
    retrieved_chunks: List[Dict[str, Any]],
    *,
    trace_id: Optional[str] = None,
    precision_at_k: Optional[Dict[str, Any]] = None,
) -> None:
    """[简易] 一站式上报检索召回可观测性：持久化 span + 可选 Precision@K 指标。

    Args:
        retrieved_chunks: 检索召回分块列表，每项含 {skill_id, score, layer, tokens}
        trace_id: 关联追踪ID
        precision_at_k: 可选 {k, hits, precision}
    """
    try:
        # [Observability:fill] report_retrieval_observability 入口：retrieved_chunks 填充时机
        _rc_in_count = len(retrieved_chunks) if isinstance(retrieved_chunks, list) else -1
        logger.info(
            "[Observability:fill] stage=report_retrieval_observability.enter | "
            "trace_id=%s | retrieved_chunks_count=%d | has_precision_at_k=%s",
            trace_id, _rc_in_count, precision_at_k is not None,
        )
        # [变易] 防御性清洗：截断过大的 retrieved_chunks，避免 span 日志膨胀
        # 与 traced_action 上下文走同一 _sanitize_observability_payload 路径（守不易：统一截断契约）
        sanitized = _sanitize_observability_payload({
            "retrieved_chunks": retrieved_chunks,
        })
        persist_observability_span(
            trace_id=trace_id,
            retrieved_chunks=sanitized["retrieved_chunks"],
            retrieved_chunks_truncated=sanitized.get(
                "retrieved_chunks_truncated", False
            ),
            retrieved_chunks_original_count=sanitized.get(
                "retrieved_chunks_original_count", len(retrieved_chunks)
            ),
            retrieval_precision_at_k=precision_at_k,
        )
        if isinstance(precision_at_k, dict):
            emit_retrieval_precision_metric(
                k=int(precision_at_k.get("k", 0)),
                hits=int(precision_at_k.get("hits", 0)),
                precision=float(precision_at_k.get("precision", 0.0)),
                trace_id=trace_id,
            )
    except Exception:  # noqa: BLE001
        logger.debug("report_retrieval_observability 失败", exc_info=True)
