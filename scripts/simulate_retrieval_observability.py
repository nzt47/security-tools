"""模拟检索召回数据 — 验证 retrieved_chunks 截断与标记效果

使用方法:
    python scripts/simulate_retrieval_observability.py

效果:
    1. 构造 60 项 retrieved_chunks（超过 _MAX_RETRIEVED_CHUNKS=50 阈值）
    2. 调用 _sanitize_observability_payload 演示截断标记
    3. 调用 report_retrieval_observability 上报结构化日志
    4. 调用 emit_eval_score_metric 演示 eval_score 持久化
    5. 所有结构化日志输出到 stdout 与 agent.skills_mgmt logger

【不易】仅调用 observability 公开接口，不修改任何源文件
【变易】支持 --chunks N 参数自定义数量
【简易】单文件脚本，无第三方依赖
"""
from __future__ import annotations
import argparse
import json
import logging
import sys
import random
from typing import List, Dict, Any

# 配置 logger 输出到 stdout，便于本地观察
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    stream=sys.stdout,
)

from agent.skills_mgmt.observability import (
    _sanitize_observability_payload,
    _MAX_RETRIEVED_CHUNKS,
    report_retrieval_observability,
    emit_eval_score_metric,
    emit_retrieval_precision_metric,
    persist_observability_span,
)
from agent.monitoring.tracing import TraceContext, get_trace_id


def _build_chunks(n: int) -> List[Dict[str, Any]]:
    """构造 n 项模拟 retrieved_chunks（每项含 skill_id/score/layer/tokens）"""
    skill_ids = [
        "email-helper", "code-reviewer", "data-fetcher", "summary-writer",
        "translation", "safety-guard", "memory-summary", "self-reflection",
        "proactive-suggestion", "voice-interaction", "context-aware",
        "emotion-expression",
    ]
    chunks = []
    for i in range(n):
        chunks.append({
            "skill_id": f"{skill_ids[i % len(skill_ids)]}-{i:03d}",
            "score": round(random.uniform(0.3, 0.95), 4),
            "layer": random.choice([1, 2, 3]),
            "tokens": random.randint(50, 500),
        })
    return chunks


def demo_truncation(n: int) -> None:
    """演示 1: _sanitize_observability_payload 截断效果"""
    print("\n" + "=" * 60)
    print(f"演示 1: 截断效果（构造 {n} 项 chunks，阈值 {_MAX_RETRIEVED_CHUNKS}）")
    print("=" * 60)

    chunks = _build_chunks(n)
    payload = {
        "retrieved_chunks": chunks,
        "action": "simulate_demo",
        "trace_id": "demo-trace-001",
    }
    sanitized = _sanitize_observability_payload(payload)

    print(f"原始 chunks 数量: {len(chunks)}")
    print(f"截断后 chunks 数量: {len(sanitized['retrieved_chunks'])}")
    print(f"截断标记 truncated: {sanitized.get('retrieved_chunks_truncated')}")
    print(f"原始数量标记 original_count: {sanitized.get('retrieved_chunks_original_count')}")
    print(f"其他字段保留 (action): {sanitized.get('action')}")

    if n > _MAX_RETRIEVED_CHUNKS:
        assert sanitized["retrieved_chunks_truncated"] is True
        assert sanitized["retrieved_chunks_original_count"] == n
        assert len(sanitized["retrieved_chunks"]) == _MAX_RETRIEVED_CHUNKS
        print(f"\n✅ 截断验证通过: {n} → {_MAX_RETRIEVED_CHUNKS} 项")
    else:
        assert sanitized.get("retrieved_chunks_truncated") is None
        print(f"\n✅ 未触发截断（{n} ≤ {_MAX_RETRIEVED_CHUNKS}）")


def demo_report_observability(n: int) -> None:
    """演示 2: report_retrieval_observability 结构化日志上报"""
    print("\n" + "=" * 60)
    print(f"演示 2: report_retrieval_observability 结构化日志（{n} 项 chunks）")
    print("=" * 60)
    print("观察下方 agent.skills_mgmt logger 输出的结构化 JSON 日志：\n")

    chunks = _build_chunks(n)
    # 模拟 precision@k 指标
    precision_at_k = {3: 0.6667, 5: 0.6, 10: 0.5}

    with TraceContext("SimulateApp", "demo_retrieval"):
        tid = get_trace_id()
        print(f"[trace_id={tid}] 开始上报检索可观测性...")
        report_retrieval_observability(
            chunks,
            trace_id=tid,
            precision_at_k=precision_at_k,
        )
        # 单独发射 precision histogram
        for k, precision in precision_at_k.items():
            emit_retrieval_precision_metric(
                k=k, hits=int(precision * k), precision=precision,
                trace_id=tid,
            )
        print(f"[trace_id={tid}] 检索可观测性上报完成")


def demo_eval_score() -> None:
    """演示 3: emit_eval_score_metric eval_score 持久化"""
    print("\n" + "=" * 60)
    print("演示 3: emit_eval_score_metric eval_score 持久化")
    print("=" * 60)
    print("观察下方 eval_score.recorded 结构化日志：\n")

    test_cases = [
        ("skill-demo-success", {"task_success": True, "score": 0.92,
                                 "hallucination_detected": False,
                                 "instruction_followed": True}),
        ("skill-demo-halluc", {"task_success": True, "score": 0.65,
                                "hallucination_detected": True,
                                "instruction_followed": False}),
        ("skill-demo-fail", {"task_success": False, "score": 0.3,
                              "hallucination_detected": False,
                              "instruction_followed": False}),
    ]

    with TraceContext("SimulateApp", "demo_eval"):
        tid = get_trace_id()
        for skill_id, eval_score in test_cases:
            print(f"[trace_id={tid}] 上报 eval_score: skill={skill_id} score={eval_score['score']}")
            emit_eval_score_metric(skill_id, eval_score, trace_id=tid)
        print(f"[trace_id={tid}] eval_score 持久化完成")


def demo_span_attributes() -> None:
    """演示 4: persist_observability_span span 属性持久化"""
    print("\n" + "=" * 60)
    print("演示 4: persist_observability_span span 属性持久化")
    print("=" * 60)
    print("观察下方 span_attributes 结构化日志：\n")

    with TraceContext("SimulateApp", "demo_span"):
        tid = get_trace_id()
        persist_observability_span(
            trace_id=tid,
            action="simulate_demo_span",
            retrieved_chunks_count=42,
            eval_score=0.88,
            user_feedback="positive",
        )
        print(f"[trace_id={tid}] span 属性持久化完成")


def main():
    parser = argparse.ArgumentParser(
        description="模拟检索召回数据，验证截断与标记效果"
    )
    parser.add_argument(
        "--chunks", type=int, default=60,
        help=f"构造的 chunks 数量（默认 60，超过 {_MAX_RETRIEVED_CHUNKS} 触发截断）",
    )
    args = parser.parse_args()

    print(f"\n🔍 模拟检索可观测性数据生成（chunks={args.chunks}）")
    print(f"   截断阈值: {_MAX_RETRIEVED_CHUNKS}")
    print(f"   日志 logger: agent.skills_mgmt / agent.monitoring.tracing")

    random.seed(42)  # 可复现

    demo_truncation(args.chunks)
    demo_report_observability(args.chunks)
    demo_eval_score()
    demo_span_attributes()

    print("\n" + "=" * 60)
    print("✅ 全部演示完成")
    print("=" * 60)
    print("请在本地日志中检查以下结构化日志关键字：")
    print("  - retrieved_chunks（含截断后的 50 项）")
    print("  - retrieved_chunks_truncated: true")
    print("  - retrieved_chunks_original_count: 60")
    print("  - retrieval_precision_at_k")
    print("  - eval_score.recorded")
    print("  - hallucination_detected")
    print("  - span_attributes")


if __name__ == "__main__":
    main()