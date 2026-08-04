"""主业务链路路由可观测性埋点（横切关注点）

任务6: 主业务链路系统性日志埋点
- 各层耗时: InputGuard / WorkflowEngine / 语义层 / LLM / OutputGuard
- 流量分布: 每层命中/未命中 + 每 N 次请求 INFO 汇总占比
- 路由决策: 决策依据（各层分数）+ 中间结果 + 最终选择

三义设计:
- 【不易】埋点不阻断主链路: 任何异常静默降级为 DEBUG 日志,不向上传播
- 【变易】汇总间隔 N 可配置: ORCHESTRATOR_TRAFFIC_REPORT_INTERVAL（默认 50）;
        各层 start/end 用 time.perf_counter() 配对计时
- 【简易】统一 log_dict 格式: trace_id_ctx + layer + decision + duration_ms 四字段必含;
        一层一入口（log_layer_result），避免各层重复造轮子

约定:
- 层名（layer）: input_guard / workflow / template / semantic / llm / output_guard / reject / behavior
- 决策（decision）: hit / miss / block / pass / modified / success / fallback / error / reject
- 日志级别: 命中/拦截/终态（决策点）→ INFO（WARNING 用于拦截告警）;
           未命中（中间结果，继续下沉漏斗）→ DEBUG; 汇总占比 → INFO
"""

from __future__ import annotations

import logging
import os
import threading
import time
import contextvars
from typing import Any, Dict, Optional

from agent.logging_utils import log_dict

logger = logging.getLogger("agent.orchestrator")

# 流量分布汇总间隔（每 N 次请求输出一次占比日志, 运维可配置）
_TRAFFIC_REPORT_INTERVAL = int(
    os.environ.get("ORCHESTRATOR_TRAFFIC_REPORT_INTERVAL", "50")
)

# 层名常量（与 process() 各步骤一一对应）
LAYER_INPUT_GUARD = "input_guard"
LAYER_WORKFLOW = "workflow"
LAYER_TEMPLATE = "template"
LAYER_WORKFLOW_LEARNING = "workflow_learning"
LAYER_SEMANTIC = "semantic"
LAYER_LLM = "llm"
LAYER_OUTPUT_GUARD = "output_guard"
LAYER_REJECT = "reject"
LAYER_BEHAVIOR = "behavior"

# 决策值常量
DECISION_HIT = "hit"
DECISION_MISS = "miss"
DECISION_BLOCK = "block"
DECISION_PASS = "pass"
DECISION_MODIFIED = "modified"
DECISION_SUCCESS = "success"
DECISION_FALLBACK = "fallback"
DECISION_ERROR = "error"
DECISION_REJECT = "reject"


# ════════════════════════════════════════════════════════════════
#  RouteTraffic: 流量分布计数（每层 attempts/hits + 每 N 次请求汇总占比）
# ════════════════════════════════════════════════════════════════

class RouteTraffic:
    """流量分布计数（线程安全）

    计数:
    - attempts: 该层被尝试次数（每次 log_layer_result 调用 +1）
    - hits:     该层命中/拦截/终态次数（hit/block/modified/success/reject +1）
    - requests: 请求数（RouteContext.init 每请求调用一次）

    每 _TRAFFIC_REPORT_INTERVAL 次请求输出一次 INFO 汇总占比日志。
    【不易】计数失败不影响主链路；reset() 仅供测试隔离。
    """

    _lock = threading.Lock()
    _attempts: Dict[str, int] = {}
    _hits: Dict[str, int] = {}
    _requests: int = 0

    @classmethod
    def request(cls) -> None:
        """请求计数（由 RouteContext.init 调用, 每请求恰好一次）"""
        try:
            with cls._lock:
                cls._requests += 1
                if cls._requests % _TRAFFIC_REPORT_INTERVAL == 0:
                    cls._report_locked()
        except Exception:
            logger.debug("RouteTraffic.request 失败", exc_info=True)

    @classmethod
    def record(cls, layer: str, decision: str) -> None:
        """层流量计数（每次 log_layer_result 调用一次）"""
        try:
            with cls._lock:
                cls._attempts[layer] = cls._attempts.get(layer, 0) + 1
                if decision in (
                    DECISION_HIT, DECISION_BLOCK,
                    DECISION_MODIFIED, DECISION_SUCCESS, DECISION_REJECT,
                ):
                    cls._hits[layer] = cls._hits.get(layer, 0) + 1
        except Exception:
            logger.debug("RouteTraffic.record 失败", exc_info=True)

    @classmethod
    def _report_locked(cls) -> None:
        """每 N 次请求输出流量占比汇总（调用方持锁）"""
        layers = sorted(set(cls._attempts) | set(cls._hits))
        summary = {}
        for layer in layers:
            a = cls._attempts.get(layer, 0)
            h = cls._hits.get(layer, 0)
            summary[layer] = {
                "attempts": a,
                "hits": h,
                "hit_rate": round(h / a, 4) if a else 0.0,
            }
        logger.info(log_dict({
            "module_name": "orchestrator",
            "action": "orchestrator.traffic.summary",
            "message": "路由流量分布汇总（最近 %d 次请求）" % _TRAFFIC_REPORT_INTERVAL,
            "traffic_summary": summary,
        }))

    @classmethod
    def snapshot(cls) -> Dict[str, Dict[str, int]]:
        """当前计数快照（测试/巡检用）"""
        with cls._lock:
            layers = sorted(set(cls._attempts) | set(cls._hits))
            return {
                layer: {
                    "attempts": cls._attempts.get(layer, 0),
                    "hits": cls._hits.get(layer, 0),
                }
                for layer in layers
            }

    @classmethod
    def reset(cls) -> None:
        """清零（测试隔离用）"""
        with cls._lock:
            cls._attempts.clear()
            cls._hits.clear()
            cls._requests = 0


# ════════════════════════════════════════════════════════════════
#  RouteContext: 单次请求路由上下文（ContextVar, 累积各层中间结果）
# ════════════════════════════════════════════════════════════════

class RouteContext:
    """单次请求路由上下文

    用途:
    - process() 入口调用 RouteContext.init(trace_id) 初始化
    - log_layer_result 自动 add_layer 累积中间结果
    - emit_route_decision 读取 layer_results 输出最终决策

    【不易】ContextVar 隔离并发请求；请求结束（emit_route_decision 后）
            由调用方调用 clear() 清理，避免泄漏到下一次请求。
    """

    _var: contextvars.ContextVar = contextvars.ContextVar(
        "route_context", default=None
    )

    def __init__(self, trace_id: str):
        self.trace_id = trace_id or ""
        self.started_at = time.perf_counter()
        self.layers: Dict[str, Dict[str, Any]] = {}

    # ── 生命周期 ──────────────────────────────────────────────

    @classmethod
    def init(cls, trace_id: str) -> "RouteContext":
        """初始化单次请求上下文（每请求恰好一次, 同时计一次流量请求）"""
        ctx = cls(trace_id or "")
        cls._var.set(ctx)
        RouteTraffic.request()
        return ctx

    @classmethod
    def get(cls) -> Optional["RouteContext"]:
        """获取当前请求上下文（无则 None）"""
        return cls._var.get()

    @classmethod
    def clear(cls) -> None:
        """清理当前请求上下文（请求结束调用）"""
        cls._var.set(None)

    # ── 累积 ──────────────────────────────────────────────────

    def add_layer(self, layer: str, decision: str,
                  duration_ms: Optional[float] = None,
                  score: Optional[float] = None,
                  **fields) -> None:
        """累积一层中间结果（供最终决策日志引用）"""
        entry: Dict[str, Any] = {
            "outcome": decision,
            "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
        }
        if score is not None:
            entry["score"] = round(score, 4)
        entry.update(fields)
        self.layers[layer] = entry

    @property
    def duration_ms(self) -> float:
        """请求总耗时（自 init 起, 毫秒）"""
        return (time.perf_counter() - self.started_at) * 1000


# ════════════════════════════════════════════════════════════════
#  统一层日志入口 + 最终路由决策
# ════════════════════════════════════════════════════════════════

def log_layer_result(layer: str, decision: str, trace_id: str, *,
                     level: int = logging.INFO,
                     action: Optional[str] = None,
                     message: Optional[str] = None,
                     duration_ms: Optional[float] = None,
                     score: Optional[float] = None,
                     **fields) -> None:
    """统一层日志入口（一次调用完成三件事）

    1. 结构化日志: log_dict 组装并输出（四字段契约 trace_id_ctx/layer/decision/duration_ms）
    2. 流量计数: RouteTraffic.record() — 层尝试 +1，终态/命中 +1
    3. 路由上下文累积: RouteContext.add_layer() — 供最终决策日志引用

    【不易】任一失败静默降级为 DEBUG，不阻断主链路。
    """
    try:
        payload: Dict[str, Any] = {
            "module_name": "orchestrator",
            "action": action or f"orchestrator.layer.{layer}.{decision}",
            "message": message or f"[路由层] {layer}: {decision}",
            "trace_id_ctx": trace_id or "",
            "layer": layer,
            "decision": decision,
            "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
        }
        if score is not None:
            payload["score"] = round(score, 4)
        payload.update(fields)
        logger.log(level, log_dict(payload))

        RouteTraffic.record(layer, decision)
        ctx = RouteContext.get()
        if ctx is not None:
            ctx.add_layer(layer, decision, duration_ms=duration_ms,
                          score=score, **fields)
    except Exception:
        # 【不易】埋点失败静默降级，绝不影响主链路
        logger.debug("routing_observability.log_layer_result 失败: layer=%s decision=%s",
                     layer, decision, exc_info=True)


def emit_route_decision(final_layer: str, decision: str, trace_id: str, *,
                        message: Optional[str] = None,
                        basis_extra: Optional[Dict[str, Any]] = None) -> None:
    """最终路由决策日志（每次请求恰好一条, INFO）

    字段:
    - final_layer: 最终处理层（workflow/template/semantic/llm/reject）
    - layer_results: 各层中间结果（来自 RouteContext, 可还原完整路由链路）
    - decision_basis: 决策依据（规则命中名/语义 top1 score/LLM 置信度等）
    - duration_ms: 请求总耗时（自 RouteContext.init 起）

    【不易】任一失败静默降级为 DEBUG，不阻断主链路。
    """
    try:
        ctx = RouteContext.get()
        payload: Dict[str, Any] = {
            "module_name": "orchestrator",
            "action": "orchestrator.process.route_decision",
            "message": message or f"[路由决策] final_layer={final_layer} decision={decision}",
            "trace_id_ctx": trace_id or "",
            "final_layer": final_layer,
            "decision": decision,
            "layer_results": dict(ctx.layers) if ctx is not None else {},
            "decision_basis": dict(basis_extra or {}),
            "duration_ms": round(ctx.duration_ms, 2) if ctx is not None else None,
        }
        logger.info(log_dict(payload))
    except Exception:
        # 【不易】埋点失败静默降级，绝不影响主链路
        logger.debug("routing_observability.emit_route_decision 失败: final_layer=%s decision=%s",
                     final_layer, decision, exc_info=True)
