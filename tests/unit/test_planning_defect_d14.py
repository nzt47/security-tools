"""D14 复现测试：无风险管理

缺陷（P2）：任务失败即标记失败/高优先级即中断；无 Plan B、无降级链、无缓冲。
PlanExecutor.config 支持配置但从未实现"降级链"（degrade chain）。

预期失败：主工具失败时，应按配置的降级链尝试备份工具（Plan B）
→ 当前任务直接被标记失败、无任何备选方案尝试 → 断言失败即复现成功。
"""
import pytest

from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task


class TestDefectD14:
    """D14：任务失败时应按降级链尝试 Plan B"""

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="已知缺陷 D14：任务失败未走降级链（缺陷看门狗，修复后移除 xfail）", strict=False)
    async def test_task_failure_uses_degrade_chain(self):
        registry = ToolRegistry()

        def primary(**kwargs):
            raise RuntimeError("主工具故障")

        def backup(**kwargs):
            return "备份工具成功"

        registry.register("primary_tool", primary)
        registry.register("backup_tool", backup)

        executor = PlanExecutor(
            registry,
            max_retries=1,
            config={"degrade_chain": {"primary_tool": ["backup_tool"]}},
        )

        plan = Plan(original_task="降级链任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="primary_tool 执行"))
        plan.state = PlanState.READY

        result = await executor.execute_plan(plan)

        # 目标行为：主工具失败后应沿降级链执行备份工具，任务最终成功
        task_a = result.get_task("a")
        assert task_a is not None
        assert task_a.status.value == "completed"
        assert "备份工具成功" in str(task_a.result)

    # ── 修复后行为分支覆盖（D14 已实现，验证降级链各分支语义） ──────────

    @staticmethod
    def _build_executor(registry, config: dict) -> PlanExecutor:
        return PlanExecutor(registry, max_retries=1, config=config)

    @staticmethod
    def _single_task_plan() -> Plan:
        plan = Plan(original_task="降级链任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="primary_tool 执行"))
        plan.state = PlanState.READY
        return plan

    @pytest.mark.asyncio
    async def test_primary_success_skips_degrade_chain(self):
        """分支B：主工具成功 → 备份工具绝不被调用（降级链零触发）"""
        registry = ToolRegistry()

        def primary(**kwargs):
            return "主工具成功"

        def backup(**kwargs):
            raise AssertionError("备份工具不应被调用")

        registry.register("primary_tool", primary)
        registry.register("backup_tool", backup)

        executor = self._build_executor(
            registry, {"degrade_chain": {"primary_tool": ["backup_tool"]}}
        )
        result = await executor.execute_plan(self._single_task_plan())
        task_a = result.get_task("a")
        assert task_a.status.value == "completed"
        assert "主工具成功" in str(task_a.result)

    @pytest.mark.asyncio
    async def test_chain_tries_next_backup_after_failure(self):
        """分支C：多备份依次尝试——第一个备份失败不中断，继续尝试第二个"""
        registry = ToolRegistry()

        def primary(**kwargs):
            raise RuntimeError("主工具故障")

        def backup1(**kwargs):
            raise RuntimeError("备份1也故障")

        def backup2(**kwargs):
            return "备份2成功"

        registry.register("primary_tool", primary)
        registry.register("backup_tool1", backup1)
        registry.register("backup_tool2", backup2)

        executor = self._build_executor(
            registry, {"degrade_chain": {"primary_tool": ["backup_tool1", "backup_tool2"]}}
        )
        result = await executor.execute_plan(self._single_task_plan())
        task_a = result.get_task("a")
        assert task_a.status.value == "completed"
        assert "备份2成功" in str(task_a.result)

    @pytest.mark.asyncio
    async def test_all_backups_fail_keeps_primary_error(self):
        """分支D：全部备份失败 → 任务失败，错误信息保留主工具根因"""
        registry = ToolRegistry()

        def primary(**kwargs):
            raise RuntimeError("主工具故障")

        def backup1(**kwargs):
            raise RuntimeError("备份1故障")

        def backup2(**kwargs):
            raise RuntimeError("备份2故障")

        registry.register("primary_tool", primary)
        registry.register("backup_tool1", backup1)
        registry.register("backup_tool2", backup2)

        executor = self._build_executor(
            registry, {"degrade_chain": {"primary_tool": ["backup_tool1", "backup_tool2"]}}
        )
        result = await executor.execute_plan(self._single_task_plan())
        task_a = result.get_task("a")
        assert task_a.status.value == "failed"
        assert "主工具故障" in str(task_a.error)

    @pytest.mark.asyncio
    async def test_unknown_backup_tool_skipped(self):
        """分支E：备份工具未注册 → 跳过该备份项；主失败则任务失败"""
        registry = ToolRegistry()

        def primary(**kwargs):
            raise RuntimeError("主工具故障")

        registry.register("primary_tool", primary)
        # backup_missing 未注册，应从降级链中跳过（warning 日志，不抛错）

        executor = self._build_executor(
            registry, {"degrade_chain": {"primary_tool": ["backup_missing"]}}
        )
        result = await executor.execute_plan(self._single_task_plan())
        task_a = result.get_task("a")
        assert task_a.status.value == "failed"
        assert "主工具故障" in str(task_a.error)

    @pytest.mark.asyncio
    async def test_no_degrade_chain_config_behavior_unchanged(self):
        """分支F：无 degrade_chain 配置 → 行为与修复前一致（主失败直接任务失败，零回退）"""
        registry = ToolRegistry()

        def primary(**kwargs):
            raise RuntimeError("主工具故障")

        registry.register("primary_tool", primary)

        executor = self._build_executor(registry, {})  # 未配置降级链
        result = await executor.execute_plan(self._single_task_plan())
        task_a = result.get_task("a")
        assert task_a.status.value == "failed"
        assert "主工具故障" in str(task_a.error)
