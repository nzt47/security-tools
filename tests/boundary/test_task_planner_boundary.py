"""BT-005 task_planner 模块边界测试

【生成日志摘要】
- 生成时间：2026-07-01
- 内容：BT-005 task_planner 边界测试（v1.0）
- 模型：GLM-5.2
- 关键状态：覆盖 TaskPlanner.plan / DAG 的 7 类边界场景
- 状态同步机制：纯函数式测试，无外部依赖

覆盖范围：
- 空值边界: None goal / 空 goal / 空步骤列表
- 极值边界: 单步 DAG / 多步 DAG / 远过去 week_offset
- 类型边界: None goal 抛 TypeError
- 异常分支: 循环依赖 / 重复 id / 不存在的依赖

源代码限制记录：
- plan(None) 抛 TypeError（keyword in None）
- DAG.add_task(None) 抛 AttributeError
- topological_sort() 循环依赖不报错但可能递归过深
"""
import json
from pathlib import Path

import pytest

from agent.task_planner.planner import TaskPlanner
from agent.task_planner.dag import DAG, TaskNode


_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _load_mock_data() -> dict:
    """加载 mock 数据"""
    with open(_FIXTURES_DIR / "mock_bt005_modules.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════
#  TaskPlanner.plan 边界测试
# ═══════════════════════════════════════════════════════════════


class TestPlannerNullBoundary:
    """plan() 空值边界测试"""

    def test_null_None作为goal抛出TypeError(self):
        """None 作为 goal 抛出 TypeError

        源代码限制: plan() 第 14 行 `keyword in goal` 未做 None 校验
        """
        planner = TaskPlanner()
        with pytest.raises(TypeError):
            planner.plan(None)  # type: ignore

    def test_empty_空字符串goal返回默认DAG(self):
        """空字符串 goal 返回默认 3 步 DAG"""
        planner = TaskPlanner()
        dag = planner.plan("")
        assert isinstance(dag, DAG)
        # 默认步骤: ["理解需求", "执行", "验证结果"]
        ready = dag.get_ready_tasks()
        assert len(ready) == 1  # 只有第一个步骤无依赖

    def test_empty_无匹配关键词返回默认DAG(self):
        """无匹配关键词返回默认 DAG"""
        planner = TaskPlanner()
        dag = planner.plan("随便做点什么")
        ready = dag.get_ready_tasks()
        assert len(ready) == 1

    def test_empty_Unicode目标正常处理(self):
        """Unicode 目标正常处理"""
        planner = TaskPlanner()
        dag = planner.plan("你好世界")
        assert isinstance(dag, DAG)


class TestPlannerKeywordBoundary:
    """plan() 关键词匹配边界测试"""

    def test_boundary_代码关键词匹配(self):
        """包含"代码"关键词匹配代码模板"""
        planner = TaskPlanner()
        dag = planner.plan("开发一个代码项目")
        # 代码模板: ["需求分析", "设计", "实现", "测试", "部署"]
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].description == "需求分析"

    def test_boundary_文章关键词匹配(self):
        """包含"文章"关键词匹配文章模板"""
        planner = TaskPlanner()
        dag = planner.plan("写一篇文章")
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].description == "大纲"

    def test_boundary_分析关键词匹配(self):
        """包含"分析"关键词匹配分析模板"""
        planner = TaskPlanner()
        dag = planner.plan("进行数据分析")
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].description == "数据收集"

    def test_boundary_项目关键词匹配(self):
        """包含"项目"关键词匹配项目模板"""
        planner = TaskPlanner()
        dag = planner.plan("管理项目")
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].description == "需求"

    def test_boundary_多关键词匹配取第一个(self):
        """多关键词同时命中取 PATTERNS 中第一个匹配项

        Python 3.7+ 字典保持插入顺序，"代码"在"分析"之前
        "代码分析项目" 同时命中"代码"和"分析"，取"代码"
        """
        planner = TaskPlanner()
        dag = planner.plan("代码分析项目")
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].description == "需求分析"  # 代码模板第一步


class TestPlannerExtremeBoundary:
    """plan() 极值边界测试"""

    def test_extreme_超长goal字符串正常处理(self):
        """超长 goal 字符串正常处理"""
        planner = TaskPlanner()
        long_goal = "代码" * 1000 + "项目"
        dag = planner.plan(long_goal)
        assert isinstance(dag, DAG)

    def test_extreme_重复关键词正常处理(self):
        """重复关键词正常处理"""
        planner = TaskPlanner()
        dag = planner.plan("代码代码代码")
        assert isinstance(dag, DAG)


# ═══════════════════════════════════════════════════════════════
#  DAG.add_task 边界测试
# ═══════════════════════════════════════════════════════════════


class TestDAGAddTaskBoundary:
    """DAG.add_task 边界测试"""

    def test_null_None作为node抛出AttributeError(self):
        """None 作为 node 抛出 AttributeError

        源代码限制: add_task() 第 18 行 `node.id` 未做 None 校验
        """
        dag = DAG()
        with pytest.raises(AttributeError):
            dag.add_task(None)  # type: ignore

    def test_empty_空DAG添加任务(self):
        """空 DAG 添加任务正常"""
        dag = DAG()
        node = TaskNode(id="task_1", description="测试任务")
        dag.add_task(node)
        assert len(dag.get_ready_tasks()) == 1

    def test_boundary_重复id覆盖旧节点(self):
        """重复 id 覆盖旧节点（字典赋值语义）"""
        dag = DAG()
        node1 = TaskNode(id="task_1", description="原始任务")
        node2 = TaskNode(id="task_1", description="覆盖任务")
        dag.add_task(node1)
        dag.add_task(node2)
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].description == "覆盖任务"

    def test_empty_空字符串id正常添加(self):
        """空字符串 id 正常添加"""
        dag = DAG()
        node = TaskNode(id="", description="空 id 任务")
        dag.add_task(node)
        assert len(dag.get_ready_tasks()) == 1

    def test_extreme_超长id正常添加(self):
        """超长 id 正常添加"""
        dag = DAG()
        long_id = "task_" + "x" * 1000
        node = TaskNode(id=long_id, description="长 id 任务")
        dag.add_task(node)
        assert len(dag.get_ready_tasks()) == 1


# ═══════════════════════════════════════════════════════════════
#  DAG.get_ready_tasks 边界测试
# ═══════════════════════════════════════════════════════════════


class TestDAGReadyTasksBoundary:
    """DAG.get_ready_tasks 边界测试"""

    def test_empty_空DAG返回空列表(self):
        """空 DAG 返回空列表"""
        dag = DAG()
        assert dag.get_ready_tasks() == []

    def test_boundary_无依赖任务全部就绪(self):
        """无依赖任务全部就绪"""
        dag = DAG()
        dag.add_task(TaskNode(id="t1", description="任务1"))
        dag.add_task(TaskNode(id="t2", description="任务2"))
        ready = dag.get_ready_tasks()
        assert len(ready) == 2

    def test_boundary_依赖未完成不就绪(self):
        """依赖未完成的任务不就绪"""
        dag = DAG()
        dag.add_task(TaskNode(id="t1", description="任务1"))
        dag.add_task(TaskNode(id="t2", description="任务2", depends_on=["t1"]))
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "t1"

    def test_boundary_依赖完成后任务就绪(self):
        """依赖完成后任务就绪"""
        dag = DAG()
        node1 = TaskNode(id="t1", description="任务1")
        node2 = TaskNode(id="t2", description="任务2", depends_on=["t1"])
        dag.add_task(node1)
        dag.add_task(node2)
        # 完成 t1
        node1.status = "done"
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "t2"

    def test_invalid_不存在的依赖被忽略(self):
        """不存在的依赖被忽略（不影响就绪判断）"""
        dag = DAG()
        dag.add_task(TaskNode(id="t1", description="任务1", depends_on=["nonexistent"]))
        ready = dag.get_ready_tasks()
        # 依赖 nonexistent 不在 _nodes 中，被 all() 忽略
        assert len(ready) == 1

    def test_boundary_已完成任务不再就绪(self):
        """已完成任务不再出现在就绪列表"""
        dag = DAG()
        node = TaskNode(id="t1", description="任务1")
        dag.add_task(node)
        node.status = "done"
        assert dag.get_ready_tasks() == []

    def test_invalid_失败状态任务不再就绪(self):
        """失败状态任务不再出现在就绪列表"""
        dag = DAG()
        node = TaskNode(id="t1", description="任务1")
        dag.add_task(node)
        node.status = "failed"
        assert dag.get_ready_tasks() == []


# ═══════════════════════════════════════════════════════════════
#  DAG.is_complete / has_failed 边界测试
# ═══════════════════════════════════════════════════════════════


class TestDAGStatusBoundary:
    """DAG.is_complete / has_failed 边界测试"""

    def test_empty_空DAG_is_complete返回True(self):
        """空 DAG is_complete() 返回 True（all 空迭代为 True）"""
        dag = DAG()
        assert dag.is_complete() is True

    def test_empty_空DAG_has_failed返回False(self):
        """空 DAG has_failed() 返回 False"""
        dag = DAG()
        assert dag.has_failed() is False

    def test_boundary_全部完成is_complete返回True(self):
        """全部任务完成 is_complete() 返回 True"""
        dag = DAG()
        node = TaskNode(id="t1", description="任务1")
        dag.add_task(node)
        node.status = "done"
        assert dag.is_complete() is True

    def test_boundary_部分完成is_complete返回False(self):
        """部分任务完成 is_complete() 返回 False"""
        dag = DAG()
        n1 = TaskNode(id="t1", description="任务1")
        n2 = TaskNode(id="t2", description="任务2")
        dag.add_task(n1)
        dag.add_task(n2)
        n1.status = "done"
        assert dag.is_complete() is False

    def test_boundary_有失败任务has_failed返回True(self):
        """有失败任务 has_failed() 返回 True"""
        dag = DAG()
        n1 = TaskNode(id="t1", description="任务1")
        n2 = TaskNode(id="t2", description="任务2")
        dag.add_task(n1)
        dag.add_task(n2)
        n1.status = "failed"
        assert dag.has_failed() is True

    def test_boundary_无失败任务has_failed返回False(self):
        """无失败任务 has_failed() 返回 False"""
        dag = DAG()
        dag.add_task(TaskNode(id="t1", description="任务1"))
        assert dag.has_failed() is False


# ═══════════════════════════════════════════════════════════════
#  DAG.topological_sort 边界测试
# ═══════════════════════════════════════════════════════════════


class TestDAGTopologicalSortBoundary:
    """DAG.topological_sort 边界测试"""

    def test_empty_空DAG返回空列表(self):
        """空 DAG topological_sort() 返回空列表"""
        dag = DAG()
        assert dag.topological_sort() == []

    def test_boundary_单节点排序(self):
        """单节点 DAG 排序"""
        dag = DAG()
        dag.add_task(TaskNode(id="t1", description="任务1"))
        result = dag.topological_sort()
        assert result == ["t1"]

    def test_boundary_线性依赖排序(self):
        """线性依赖 DAG 排序（t3→t2→t1）"""
        dag = DAG()
        dag.add_task(TaskNode(id="t1", description="任务1"))
        dag.add_task(TaskNode(id="t2", description="任务2", depends_on=["t1"]))
        dag.add_task(TaskNode(id="t3", description="任务3", depends_on=["t2"]))
        result = dag.topological_sort()
        # t1 应在 t2 之前，t2 应在 t3 之前
        assert result.index("t1") < result.index("t2")
        assert result.index("t2") < result.index("t3")

    def test_invalid_循环依赖不报错(self):
        """循环依赖不报错（源代码限制：DFS 有 visited 守卫不会无限递归）"""
        dag = DAG()
        dag.add_task(TaskNode(id="t1", description="任务1", depends_on=["t2"]))
        dag.add_task(TaskNode(id="t2", description="任务2", depends_on=["t1"]))
        # 不应抛异常，结果包含两个节点
        result = dag.topological_sort()
        assert len(result) == 2

    def test_extreme_多节点DAG排序(self):
        """多节点 DAG 排序"""
        dag = DAG()
        for i in range(10):
            deps = [f"t{i-1}"] if i > 0 else []
            dag.add_task(TaskNode(id=f"t{i}", description=f"任务{i}", depends_on=deps))
        result = dag.topological_sort()
        assert len(result) == 10
        # 验证依赖顺序
        for i in range(1, 10):
            assert result.index(f"t{i-1}") < result.index(f"t{i}")
