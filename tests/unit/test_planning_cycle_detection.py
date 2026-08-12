"""循环依赖检测 DFS 专项单元测试（阶段 2 / D11 验证器）

针对 validator.find_circular_dependencies 独立验证 DFS 三色标记正确性：
线性/分叉无环、自环、二元/三元环、带支线的环、悬空依赖安全、多环、空计划。
"""

from planning.models import Task
from planning.validator import find_circular_dependencies


def _tasks(specs):
    """按 (id, dependencies) 列表构造 Task 集合"""
    return [Task(id=tid, description=f"任务{tid}", dependencies=list(deps))
            for tid, deps in specs]


def _cycle_ids(specs):
    return find_circular_dependencies(_tasks(specs))


class TestFindCircularDependencies:
    def test_empty_plan_no_cycle(self):
        assert find_circular_dependencies([]) == []

    def test_single_task_no_cycle(self):
        assert _cycle_ids([("a", [])]) == []

    def test_linear_chain_no_cycle(self):
        # a -> b -> c（单向依赖，无环）
        assert _cycle_ids([("a", ["b"]), ("b", ["c"]), ("c", [])]) == []

    def test_diamond_no_cycle(self):
        # a -> b, a -> c, b -> d, c -> d（分叉汇聚，无环）
        assert _cycle_ids([
            ("a", ["b", "c"]), ("b", ["d"]), ("c", ["d"]), ("d", []),
        ]) == []

    def test_self_cycle_detected(self):
        # a 依赖自身
        assert _cycle_ids([("a", ["a"])]) == ["a"]

    def test_two_node_cycle_detected(self):
        assert _cycle_ids([("a", ["b"]), ("b", ["a"])]) == ["a"]

    def test_three_node_cycle_detected(self):
        # a -> b -> c -> a
        assert _cycle_ids([("a", ["b"]), ("b", ["c"]), ("c", ["a"])]) == ["a"]

    def test_cycle_with_tail_not_reported(self):
        # 支线 x -> a 不在环内；环为 a -> b -> a
        ids = _cycle_ids([
            ("x", ["a"]), ("a", ["b"]), ("b", ["a"]),
        ])
        assert ids == ["a"]
        assert "x" not in ids

    def test_dangling_dependency_does_not_crash(self):
        # 引用不存在的 id：DFS 安全跳过（悬空交由依赖完整性校验），不报环
        assert _cycle_ids([("a", ["ghost"]), ("b", ["ghost"])]) == []

    def test_multiple_disjoint_cycles(self):
        # 两个不相连环：a <-> b 与 c <-> d
        ids = _cycle_ids([
            ("a", ["b"]), ("b", ["a"]), ("c", ["d"]), ("d", ["c"]),
        ])
        assert set(ids) == {"a", "c"}

    def test_chain_entering_cycle_detects_cycle_only(self):
        # 支线 x -> a 进入环 a <-> b，仅报环内节点
        ids = _cycle_ids([
            ("x", ["a"]), ("a", ["b"]), ("b", ["a"]), ("y", ["z"]), ("z", []),
        ])
        assert ids == ["a"]
        assert "x" not in ids and "y" not in ids


class TestValidatorIntegration:
    def test_circular_issue_via_validate_plan(self):
        """抽离后 validate_plan 仍产生 circular_dependency issue（行为不变）"""
        from planning.models import Plan, PlanState
        from planning.validator import validate_plan

        plan = Plan(original_task="环", state=PlanState.READY)
        plan.add_task(Task(id="a", description="x", dependencies=["b"]))
        plan.add_task(Task(id="b", description="x", dependencies=["a"]))
        issues = validate_plan(plan)
        circular = [i for i in issues if i.code == "circular_dependency"]
        assert circular, "环计划必须被检测"
        assert circular[0].task_id == "a"
