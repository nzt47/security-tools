#!/usr/bin/env python3
"""任务 3 本地验证：模拟 Critic 服务不可用场景

验证点：
1. CriticEvaluator 评估链路抛异常时，降级路径返回的 EvaluationResult
   overall_score is None（诚实降级，不伪造分数）
2. 降级管理器 critic_evaluate_with_degrade 返回 {"degraded": True, "overall_score": None}
3. 降级埋点 degraded_fallbacks_used / degraded_calls_avoided 正确计数
4. 关键分支日志（critic_degrade_enter / critic_degrade_fallback）输出可见
5. 降级不计入 success_count

用法: python scripts/verify_task3_critic_degrade.py
"""

import logging
import sys
from pathlib import Path
from unittest.mock import patch

# 注入项目根（python scripts/x.py 时 sys.path[0] = scripts/）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.cognitive.critic import CriticEvaluator, CriticMode, EvaluationResult
from agent.graceful_degrade import get_degrade_manager

# 日志输出到控制台，展示关键分支日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("verify_task3")


def mock_critic_unavailable():
    """模拟 Critic 服务不可用：_evaluate_with_rules 抛异常"""
    evaluator = CriticEvaluator(mode=CriticMode.RULE_BASED)
    with patch.object(
        evaluator,
        "_evaluate_with_rules",
        side_effect=RuntimeError("critic service unavailable (mock)"),
    ):
        result = evaluator.evaluate(
            user_query="什么是人工智能？",
            response="人工智能是计算机科学的一个分支...",
            context={"knowledge_base": ["mock kb"]},
        )
    return result, evaluator


def main() -> int:
    # ── 场景 1：完整评估链路（CriticEvaluator.evaluate）──
    logger.info("== 场景 1：CriticEvaluator 评估链路抛异常 → 降级路径 ==")
    result, evaluator = mock_critic_unavailable()
    assert isinstance(result, EvaluationResult), f"期望 EvaluationResult，实际 {type(result)}"
    logger.info("EvaluationResult.overall_score = %r（期望 None）", result.overall_score)
    logger.info("EvaluationResult.explanation = %r", result.explanation)
    assert result.overall_score is None, f"overall_score 应为 None，实际 {result.overall_score}"
    assert "Critic 服务不可用" in result.explanation, result.explanation
    print("[PASS] 场景 1：EvaluationResult.overall_score is None 且 reason 正确")

    # ── 场景 2：降级管理器返回 dict 诚实标记 ──
    logger.info("== 场景 2：critic_evaluate_with_degrade 返回 dict ==")
    degrade_dict = evaluator._degrade_manager.critic_evaluate_with_degrade(
        "什么是人工智能？", "响应文本", {"kb": []}
    )
    logger.info("degrade dict = %r", degrade_dict)
    assert degrade_dict["degraded"] is True, degrade_dict
    assert degrade_dict["overall_score"] is None, degrade_dict
    print("[PASS] 场景 2：degraded=True 且 overall_score=None")

    # ── 场景 3：降级埋点计数（fallback 真发生）──
    metrics = get_degrade_manager().get_metrics()
    assert metrics.degraded_fallbacks_used >= 2, metrics   # 场景 1 + 场景 2
    assert metrics.degraded_calls_avoided >= 2, metrics
    critic_entries = [e for e in metrics.degrade_history if e["module"] == "critic"]
    assert critic_entries, "degrade_history 无 critic 条目"
    assert critic_entries[-1]["fallback_type"] == "stub", critic_entries[-1]
    print(f"[PASS] 场景 3：fallbacks_used={metrics.degraded_fallbacks_used} "
          f"calls_avoided={metrics.degraded_calls_avoided} fallback_type=stub")

    # ── 场景 4：降级不计入 success_count ──
    degrade_mgr = evaluator._degrade_manager
    before = degrade_mgr._get_module_state("critic")["success_count"]
    degrade_mgr.critic_evaluate_with_degrade("q", "r", {"c": 1})
    after = degrade_mgr._get_module_state("critic")["success_count"]
    assert after == before, f"降级不应计入 success_count: {before} -> {after}"
    print("[PASS] 场景 4：降级不计入 success_count")

    print("✓ 全部验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
