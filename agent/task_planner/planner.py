
import logging
import json
import uuid

logger = logging.getLogger(__name__)


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]

"""规划器——将目标分解为子任务 DAG

【D8 修复】TaskPlanner 实现已迁移至 planning/task_planner.py（统一规划模块），
本模块保留薄壳重导出以兼容既有调用路径（builtin_plans / boundary / integration）。
"""
from .dag import DAG, TaskNode  # noqa: F401 保留重导出，兼容既有引用
from planning.task_planner import TaskPlanner  # noqa: F401 重导出（实现归属 planning 模块）


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
            "module_name": "planner",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
