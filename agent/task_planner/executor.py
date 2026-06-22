"""DAG 执行器——按拓扑序执行子任务"""
import logging
from .dag import DAG

logger = logging.getLogger(__name__)

class DAGExecutor:
    async def execute(self, dag: DAG, orchestrator) -> dict:
        results = {}
        while not dag.is_complete() and not dag.has_failed():
            for task in dag.get_ready_tasks():
                task.status = "running"
                result = await orchestrator.process(task.description)
                task.status = "done" if result.get("success", True) else "failed"
                task.result = result.get("response", "")
                results[task.id] = task.result
                logger.info(f"[Planner] {task.id}: {task.status}")
        return results
