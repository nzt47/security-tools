"""规划器——将目标分解为子任务 DAG"""
from .dag import DAG, TaskNode

class TaskPlanner:
    PATTERNS = {
        "代码": ["需求分析", "设计", "实现", "测试", "部署"],
        "文章": ["大纲", "初稿", "修改", "终稿"],
        "分析": ["数据收集", "数据清洗", "分析", "报告"],
        "项目": ["需求", "设计", "开发", "测试", "上线"],
    }

    def plan(self, goal: str) -> DAG:
        dag = DAG()
        for keyword, steps in self.PATTERNS.items():
            if keyword in goal:
                return self._build_dag(steps)
        return self._build_dag(["理解需求", "执行", "验证结果"])

    def _build_dag(self, steps: list[str]) -> DAG:
        dag = DAG()
        prev = None
        for i, step in enumerate(steps):
            node = TaskNode(id=f"step_{i}", description=step,
                          depends_on=[f"step_{i-1}"] if prev else [])
            dag.add_task(node)
            prev = step
        return dag
