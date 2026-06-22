"""TaskPlanner DAG 测试"""
from agent.task_planner.dag import DAG, TaskNode
from agent.task_planner.planner import TaskPlanner

class TestDAG:
    def setup_method(self):
        self.dag = DAG()
        self.dag.add_task(TaskNode("a", "A"))
        self.dag.add_task(TaskNode("b", "B", depends_on=["a"]))
        self.dag.add_task(TaskNode("c", "C", depends_on=["a"]))

    def test_topological_sort(self):
        order = self.dag.topological_sort()
        assert order[0] == "a"

    def test_ready_tasks(self):
        assert len(self.dag.get_ready_tasks()) == 1

    def test_complete_detection(self):
        self.dag._nodes["a"].status = "done"
        self.dag._nodes["b"].status = "done"
        self.dag._nodes["c"].status = "done"
        assert self.dag.is_complete()

class TestTaskPlanner:
    def test_plan_code(self):
        dag = TaskPlanner().plan("写一个Python爬虫")
        assert len(dag.topological_sort()) >= 3
