"""统一任务规划器（D8 修复）

缺陷（P1）：agent/task_planner/planner.py 是另一套规则硬编码的任务规划器
（4 类关键词模式），与 planning/ 功能重复、职责边界不清。

修复：TaskPlanner 实现迁移至 planning 统一模块；agent/task_planner/planner.py
保留薄壳重导出（兼容既有调用路径）。DAG/TaskNode 为通用数据结构，继续共享
agent.task_planner.dag（无 planning 依赖，不产生循环导入）。
"""

import logging

from agent.task_planner.dag import DAG, TaskNode

logger = logging.getLogger(__name__)


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
