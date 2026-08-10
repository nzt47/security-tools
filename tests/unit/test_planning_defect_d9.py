"""D9 复现测试：无计划持久化（重启即失）

缺陷（P1）：_active_plans 纯内存，重启即失；无检查点/恢复；
反思经验库（./data/reflection）与系统记忆/知识库无关联。

预期失败：新 PlanningCore 实例（模拟进程重启）应恢复未完成计划
→ 当前恢复列表为空 → 断言失败即复现成功。
"""
import os
import tempfile
import pytest

from planning.core import PlanningCore
from planning.models import PlanState


class TestDefectD9:
    """D9：重启后应恢复未完成计划"""

    @pytest.mark.asyncio
    async def test_unfinished_plan_recovered_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            # planning.persist_dir 指向独立临时目录，避免默认 data/plans 被历史残留污染
            cfg = {
                "reflector": {"persist_dir": tmp_dir},
                "planning": {"persist_dir": tmp_dir},
            }

            core1 = PlanningCore(config=cfg)
            plan = await core1.plan("首先打开文件然后保存")
            plan.state = PlanState.EXECUTING  # 模拟未完成计划
            core1.save_plan_checkpoint(plan)

            # D9 规格：SQLite 落库文件存在
            db_path = os.path.join(tmp_dir, "plans.db")
            assert os.path.exists(db_path)

            # 模拟进程重启：全新实例
            core2 = PlanningCore(config=cfg)

            # 目标行为：重启后应恢复未完成计划
            assert plan.id in core2._active_plans
