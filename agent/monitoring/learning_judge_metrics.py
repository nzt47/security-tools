"""LLM-as-Judge 双假设判别 Prometheus 指标（任务5 Judge dry-run 通道 · 观测层）

Judge 通道（agent/learning/judge_channel.py）每批 dry-run 评估完成后经
`sync_judge_gauges()` 把判别统计写入 Prometheus gauge，供监控面板与告警引用：

    yunshu_learning_judge_dryrun{metric="disagreement_rate"} 0.10
    yunshu_learning_judge_discrimination{conclusion="hypothesis_a_eval_insufficient"} 1

labels 语义（判别结论恒二值化 recommendation）:
    conclusion: hypothesis_a_eval_insufficient / hypothesis_b_candidate_quality /
                insufficient_data / inconclusive
    recommendation: not_introduce / evaluate_introduce
    值: 仅实际结论对应组合为 1，其余为 0
    → 告警表达式: `yunshu_learning_judge_discrimination{recommendation="evaluate_introduce"} == 1`
      （"支持引入 Judge"为远期决策信号，按报告 §3.3 流程评估，绝不自动启用）

【不易】gauge 同步纯观测：不改变任何提交/采纳/回滚决策；prometheus_client 不可用 /
        重复注册时安全降级（沿用 _safe_gauge 模式）；任何异常静默，不影响主链路。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agent.monitoring.prometheus import _safe_gauge

logger = logging.getLogger(__name__)

# 与 judge_channel 结论/建议对齐（避免循环导入，此处显式声明）
_CONCLUSIONS = (
    "hypothesis_a_eval_insufficient",   # 假设 A：评估不精细 → 支持引入
    "hypothesis_b_candidate_quality",   # 假设 B：候选质量差 → 不引入
    "insufficient_data",                # 样本不足 → 不启用（继续采集）
    "inconclusive",                     # 证据不足 → 不启用（继续采集/复核）
)
_RECOMMENDATIONS = ("not_introduce", "evaluate_introduce")

# 判别统计 metric 清单（gauge 名 = metric label 值）
_STAT_METRICS = (
    "candidates",
    "judged",
    "disagreements",
    "rule_adopted",
    "implied_adopted",
    "budget_blocked",
    "tokens_used",
    "disagreement_rate",
    "rule_adoption_rate",
    "judge_implied_adoption_rate",
    "adoption_rate_delta_pp",
)

yunshu_learning_judge_dryrun = _safe_gauge(
    'yunshu_learning_judge_dryrun',
    'LLM-as-Judge dry-run 双通道判别统计快照; '
    'labels: metric (candidates/judged/disagreement_rate/rule_adoption_rate/'
    'judge_implied_adoption_rate/adoption_rate_delta_pp/tokens_used/budget_blocked 等)',
    ['metric'],
)

yunshu_learning_judge_discrimination = _safe_gauge(
    'yunshu_learning_judge_discrimination',
    'LLM-as-Judge 双假设判别结论 (1=当前结论/建议); '
    'labels: conclusion (A/B/insufficient/inconclusive), '
    'recommendation (not_introduce/evaluate_introduce)',
    ['conclusion', 'recommendation'],
)


def sync_judge_gauges(stats: Dict[str, Any], discrimination: Dict[str, Any]) -> None:
    """把 evaluate_candidates() 的 stats + discrimination 同步到 gauge（幂等；异常静默）"""
    try:
        for metric in _STAT_METRICS:
            val = (stats or {}).get(metric)
            yunshu_learning_judge_dryrun.labels(metric=metric).set(
                float(val) if val is not None else -1.0)

        conclusion = str((discrimination or {}).get("conclusion") or "insufficient_data")
        if conclusion not in _CONCLUSIONS:
            conclusion = "insufficient_data"
        recommendation = str(
            (discrimination or {}).get("recommendation") or "not_introduce")
        if recommendation not in _RECOMMENDATIONS:
            recommendation = "not_introduce"
        for c in _CONCLUSIONS:
            for r in _RECOMMENDATIONS:
                val = 1.0 if (c == conclusion and r == recommendation) else 0.0
                yunshu_learning_judge_discrimination.labels(
                    conclusion=c, recommendation=r).set(val)
    except Exception:
        logger.debug("[Judge监控] gauge 同步失败（静默）")


__all__ = [
    "sync_judge_gauges",
    "yunshu_learning_judge_dryrun",
    "yunshu_learning_judge_discrimination",
]
