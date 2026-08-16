"""D6 复现测试：复杂度判定粗糙且配置悬空

缺陷（P1）：_needs_planning 靠关键词计数；complexity_threshold(0.5) 配置从未被使用。

预期失败：complexity_threshold 调高后判定应变严
→ 当前阈值被忽略、判定不变 → 断言失败即复现成功。
"""
import tempfile
import pytest

from planning.core import PlanningCore


class TestDefectD6:
    """D6：complexity_threshold 应参与复杂度判定"""

    def test_complexity_threshold_affects_needs_planning(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            # 阈值设为极高：只有极复杂任务才需要规划
            core = PlanningCore(config={
                "complexity_threshold": 10.0,
                "reflector": {"persist_dir": tmp_dir},
            })

            # 目标行为：高阈值下普通"报告"类任务不应触发规划
            assert core._needs_planning("帮我完成一个报告") is False
