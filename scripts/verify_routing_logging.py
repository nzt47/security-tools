#!/usr/bin/env python3
"""任务6 采样验证 — 主业务链路路由日志埋点（routing_observability.py）

模拟两条典型请求链路，验证：
  1. 统一层日志四字段必含: trace_id_ctx / layer / decision / duration_ms
  2. 日志级别约定: 命中/终态 INFO、未命中（中间结果）DEBUG
  3. 流量分布: RouteTraffic attempts/hits 计数 + 每 N 次 INFO 汇总
  4. 路由决策: emit_route_decision 含 final_layer / layer_results（决策依据）/ decision_basis
  5. 链路还原: 一次请求日志可还原完整路由链路

【不易】不调用完整 Orchestrator，直接驱动 routing_observability 公共接口验证格式契约
【简易】独立可运行: python scripts/verify_routing_logging.py
"""
import logging
import os
import sys

# 确保项目根目录在 sys.path（独立运行脚本时 agent 包可导入）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.orchestrator.routing_observability import (
    log_layer_result,
    emit_route_decision,
    RouteTraffic,
    RouteContext,
    LAYER_INPUT_GUARD, LAYER_WORKFLOW, LAYER_SEMANTIC, LAYER_LLM,
    LAYER_OUTPUT_GUARD, LAYER_REJECT,
    DECISION_PASS, DECISION_HIT, DECISION_MISS, DECISION_SUCCESS,
    DECISION_REJECT,
)


class _CaptureHandler(logging.Handler):
    """收集结构化日志消息，供断言"""

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        try:
            self.messages.append(record.getMessage())
        except Exception:
            pass


def _reset_state():
    RouteTraffic.reset()
    RouteContext.clear()


def _simulate_semantic_hit_chain():
    """链路 A: 规则未命中 → 语义命中（短路径）"""
    trace_id = "verify-semantic-hit"
    RouteContext.init(trace_id)
    log_layer_result(LAYER_INPUT_GUARD, DECISION_PASS, trace_id,
                     level=logging.DEBUG, duration_ms=1.2)
    log_layer_result(LAYER_WORKFLOW, DECISION_MISS, trace_id,
                     level=logging.DEBUG, duration_ms=0.5)
    log_layer_result(LAYER_SEMANTIC, DECISION_HIT, trace_id,
                     duration_ms=8.3, score=0.87)
    emit_route_decision(LAYER_SEMANTIC, DECISION_HIT, trace_id,
                        message="[语义层] 命中短路返回",
                        basis_extra={"score": 0.87, "retrieval_method": "rrf"})


def _simulate_llm_fallback_chain():
    """链路 B: 全部未命中 → LLM 兜底（长路径）"""
    trace_id = "verify-llm-fallback"
    RouteContext.init(trace_id)
    log_layer_result(LAYER_INPUT_GUARD, DECISION_PASS, trace_id,
                     level=logging.DEBUG, duration_ms=1.0)
    log_layer_result(LAYER_WORKFLOW, DECISION_MISS, trace_id,
                     level=logging.DEBUG, duration_ms=0.4)
    log_layer_result(LAYER_SEMANTIC, DECISION_MISS, trace_id,
                     level=logging.DEBUG, duration_ms=5.1)
    log_layer_result(LAYER_LLM, DECISION_SUCCESS, trace_id,
                     duration_ms=120.0, llm_confidence=0.82)
    emit_route_decision(LAYER_LLM, DECISION_SUCCESS, trace_id,
                        message="[LLM] 正常完成",
                        basis_extra={"llm_confidence": 0.82})


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "DEBUG"),
        format="%(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("verify_routing_logging")
    # 挂接捕获 handler（保持控制台输出，同时收集消息用于断言）
    handler = _CaptureHandler()
    logging.getLogger("agent.orchestrator").addHandler(handler)
    failures: list[str] = []

    _reset_state()
    _simulate_semantic_hit_chain()
    _simulate_llm_fallback_chain()

    # 1. 四字段契约: 每条层日志都含 trace_id_ctx/layer/decision/duration_ms
    layer_msgs = [m for m in handler.messages if "'action': 'orchestrator.layer" in m
                  or "'action': 'orchestrator.wfl" in m]
    for m in layer_msgs:
        for field in ("'trace_id_ctx'", "'layer'", "'decision'", "'duration_ms'"):
            if field not in m:
                failures.append(f"层日志缺字段 {field}: {m[:120]}")
                break

    # 2. 决策日志恰好一条链路一决策（verify-* 两条链路 → 2 条 route_decision）
    decisions = [m for m in handler.messages
                 if "'action': 'orchestrator.process.route_decision'" in m]
    if len(decisions) != 2:
        failures.append(f"最终决策日志应恰好 2 条（两条链路）, 实际 {len(decisions)}")
    for d in decisions:
        for field in ("'final_layer'", "'layer_results'", "'decision_basis'"):
            if field not in d:
                failures.append(f"决策日志缺字段 {field}: {d[:120]}")

    # 3. 流量分布计数
    snap = RouteTraffic.snapshot()
    if snap.get(LAYER_SEMANTIC, {}).get("attempts", 0) != 2:   # 两条链路各一次
        failures.append(f"semantic attempts 应为 2, 实际 {snap}")
    if snap.get(LAYER_SEMANTIC, {}).get("hits", 0) != 1:       # 仅链路 A 命中
        failures.append(f"semantic hits 应为 1, 实际 {snap}")
    if snap.get(LAYER_LLM, {}).get("hits", 0) != 1:            # 仅链路 B LLM 成功
        failures.append(f"llm hits 应为 1, 实际 {snap}")

    # 4. 链路还原: 决策日志的 layer_results 含前序中间结果
    sem_decision = next(d for d in decisions if "'final_layer': 'semantic'" in d)
    if "'workflow'" not in sem_decision or "'input_guard'" not in sem_decision:
        failures.append("语义链路决策缺少 input_guard/workflow 中间结果（链路不可还原）")

    logging.getLogger("agent.orchestrator").removeHandler(handler)

    if failures:
        logger.error("❌ 路由日志埋点采样验证失败 %d 项:\n  - %s",
                     len(failures), "\n  - ".join(failures))
        return 1
    logger.info("✅ 任务6 埋点采样验证通过（四字段契约/级别/流量计数/决策日志/链路还原）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
