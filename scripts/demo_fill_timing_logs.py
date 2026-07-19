"""填充时机日志演示脚本

专门展示 observability.py 中 5 个 [Observability:fill] 日志点的输出。
配置 logging 输出到 stderr，让用户直观看到 retrieved_chunks / eval_score 的填充时机。

用法:
    python scripts/demo_fill_timing_logs.py
"""
from __future__ import annotations

import logging
import os
import sys

# 配置 logging 输出到 stderr（INFO 级别，只看 message）
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stderr,
)

# 确保项目根在 sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.skills_mgmt.observability import (  # noqa: E402
    _sanitize_observability_payload,
    _MAX_RETRIEVED_CHUNKS,
    emit_eval_score_metric,
    report_retrieval_observability,
    traced_action,
)


def banner(title: str) -> None:
    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"  {title}", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)


def main() -> int:
    banner("填充时机日志演示 — 5 个 [Observability:fill] 日志点")

    # ── 日志点 5: report_retrieval_observability.enter ──
    banner("[日志点 5] report_retrieval_observability 入口")
    print("触发: 调用 report_retrieval_observability(retrieved_chunks=[...])", file=sys.stderr)
    report_retrieval_observability(
        retrieved_chunks=[
            {"skill_id": "demo-skill", "score": 0.95, "layer": 1, "tokens": 100},
        ],
        trace_id="demo-trace-001",
    )

    # ── 日志点 1: sanitize.truncate ──
    banner("[日志点 1] _sanitize_observability_payload 截断（51 项触发）")
    print(f"触发: 构造 {_MAX_RETRIEVED_CHUNKS + 1} 项 retrieved_chunks", file=sys.stderr)
    big_chunks = [
        {"skill_id": f"skill-{i}", "score": 0.1 * i, "layer": 1, "tokens": 10}
        for i in range(_MAX_RETRIEVED_CHUNKS + 1)
    ]
    sanitized = _sanitize_observability_payload({"retrieved_chunks": big_chunks})
    print(
        f"截断后: count={len(sanitized['retrieved_chunks'])}, "
        f"truncated={sanitized.get('retrieved_chunks_truncated')}, "
        f"original_count={sanitized.get('retrieved_chunks_original_count')}",
        file=sys.stderr,
    )

    # ── 日志点 4: emit_eval_score_metric.enter ──
    banner("[日志点 4] emit_eval_score_metric 入口")
    print("触发: 调用 emit_eval_score_metric(skill_id, eval_score)", file=sys.stderr)
    emit_eval_score_metric(
        skill_id="demo-skill",
        eval_score={
            "task_success": True,
            "instruction_followed": True,
            "hallucination_detected": False,
            "score": 0.92,
        },
        trace_id="demo-trace-002",
    )

    # ── 日志点 4 再来一次：幻觉场景 ──
    banner("[日志点 4] emit_eval_score_metric 入口（幻觉场景）")
    print("触发: hallucination_detected=True", file=sys.stderr)
    emit_eval_score_metric(
        skill_id="demo-skill-hallucination",
        eval_score={
            "task_success": False,
            "instruction_followed": False,
            "hallucination_detected": True,
            "score": 0.3,
        },
        trace_id="demo-trace-003",
    )

    # ── 日志点 2 + 3: traced_action.start / end ──
    banner("[日志点 2 + 3] traced_action 入口/出口")
    print("触发: with traced_action('demo_action', retrieved_chunks=[...])", file=sys.stderr)
    with traced_action(
        "demo_action",
        trace_id="demo-trace-004",
        retrieved_chunks=[
            {"skill_id": "ctx-skill", "score": 0.8, "layer": 1, "tokens": 50},
        ],
    ) as ctx:
        # 模拟业务逻辑：在 ctx 中追加结果
        ctx["result"] = "ok"
        ctx["match_count"] = 1

    banner("演示完成 — 5 个填充时机日志点均已触发")
    print("关键观察:", file=sys.stderr)
    print("  - report_retrieval_observability.enter: retrieved_chunks 进入 observability 层", file=sys.stderr)
    print("  - sanitize.truncate: 超过 50 项时截断并打标", file=sys.stderr)
    print("  - emit_eval_score_metric.enter: eval_score 进入 observability 层", file=sys.stderr)
    print("  - traced_action.start: payload 中 retrieved_chunks 填充时机", file=sys.stderr)
    print("  - traced_action.end: merged ctx 中 retrieved_chunks 填充时机", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
