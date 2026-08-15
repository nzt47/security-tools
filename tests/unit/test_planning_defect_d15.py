"""D15 复现测试：无可解释性产品化

缺陷（P2）：只有日志；无规划过程可视化、无用户可读计划摘要。
Plan 模型只提供 to_dict()（开发向），没有任何用户可读的结构化摘要能力。

预期失败：Plan 应提供用户可读摘要方法（如 summarize()），包含目标、任务、状态
→ 当前 Plan 无此方法 → 断言失败即复现成功。
"""
import pytest

from planning.models import Plan, Task


class TestDefectD15:
    """D15：Plan 应提供用户可读的结构化摘要"""

    def test_plan_provides_human_readable_summary(self):
        plan = Plan(original_task="整理一份市场分析报告")
        plan.add_task(Task(id="t1", description="收集数据"))
        plan.add_task(Task(id="t2", description="撰写报告"))

        # 目标行为：Plan 应暴露 summarize() 生成用户可读计划摘要
        assert hasattr(plan, "summarize"), "目标: Plan 应提供用户可读摘要方法 summarize()"

        summary = plan.summarize()
        assert "整理一份市场分析报告" in summary
        assert "收集数据" in summary
        assert "撰写报告" in summary
