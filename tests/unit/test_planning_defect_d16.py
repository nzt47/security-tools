"""D16 复现测试：无监控埋点

缺陷（P2）：未接入系统 Prometheus/metrics/tracing；无法统计规划成功率、迭代数、成本。
PlanningCore.get_stats() 只暴露活跃计划数/执行历史/学习统计/工具列表，
没有任何规划成功率、迭代数、成本等可观测指标。

预期失败：get_stats() 应暴露规划成功率、迭代数、成本等指标
→ 当前缺少这些键 → 断言失败即复现成功。
"""
import pytest

from planning.core import PlanningCore


class TestDefectD16:
    """D16：PlanningCore 应暴露规划可观测指标"""

    def test_get_stats_exposes_planning_metrics(self):
        core = PlanningCore()

        stats = core.get_stats()

        # 目标行为：可统计规划成功率、迭代数、成本
        for metric in ("total_plans", "success_count", "success_rate", "total_iterations", "total_cost"):
            assert metric in stats, f"目标: get_stats() 应包含指标 {metric}"
