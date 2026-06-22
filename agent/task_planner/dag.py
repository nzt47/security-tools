"""DAG 引擎——管理任务依赖和执行顺序"""
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class TaskNode:
    id: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    result: Optional[str] = None

class DAG:
    def __init__(self):
        self._nodes: dict[str, TaskNode] = {}

    def add_task(self, node: TaskNode):
        self._nodes[node.id] = node

    def get_ready_tasks(self) -> list[TaskNode]:
        ready = []
        for node in self._nodes.values():
            if node.status != "pending":
                continue
            deps_done = all(self._nodes[d].status == "done" for d in node.depends_on if d in self._nodes)
            if deps_done:
                ready.append(node)
        return ready

    def is_complete(self) -> bool:
        return all(n.status == "done" for n in self._nodes.values())

    def has_failed(self) -> bool:
        return any(n.status == "failed" for n in self._nodes.values())

    def topological_sort(self) -> list[str]:
        visited, result = set(), []
        def dfs(nid):
            if nid in visited:
                return
            visited.add(nid)
            node = self._nodes[nid]
            for dep in node.depends_on:
                if dep in self._nodes:
                    dfs(dep)
            result.append(nid)
        for nid in self._nodes:
            dfs(nid)
        return result
