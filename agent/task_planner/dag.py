
import logging
import json
import uuid

logger = logging.getLogger(__name__)


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]

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


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "dag",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
