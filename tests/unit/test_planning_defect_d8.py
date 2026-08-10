"""D8 复现测试：双规划器并存（职责边界不清）

缺陷（P1）：agent/task_planner/（planner.py / enhanced_planner.py / dag.py）是另一套
规则硬编码的任务规划器（4 类关键词模式），与 planning/ 功能重复、职责边界不清。

预期失败：重复能力收口后，task_planner 应转发到 planning 统一入口
（TaskPlanner.plan 实现应属于 planning 模块）→ 当前为独立实现 → 断言失败即复现成功。
"""
import pytest

from agent.task_planner.planner import TaskPlanner


class TestDefectD8:
    """D8：task_planner 应收敛为 planning 统一入口的薄壳"""

    @pytest.mark.xfail(reason="已知缺陷 D8：任务规划器未委托规划内核（缺陷看门狗，修复后移除 xfail）", strict=False)
    def test_task_planner_delegates_to_planning(self):
        # 目标行为：重复能力收口后，task_planner 的 plan 实现转发到 planning 模块
        assert TaskPlanner.plan.__module__.startswith("planning")
