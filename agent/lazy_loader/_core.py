"""懒加载器核心共享组件 — 预加载器基类

供 lazy_loader/__init__.py 和 lazy_loader_async.py 共用。
"""

import logging
import json
import uuid
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Any, Dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class _BaseParallelPreloader:
    """并行预加载器基类 — 提供公共初始化和关闭逻辑

    子类只需实现 preload() 方法，继承 __init__、_load_module、shutdown。
    """

    def __init__(self, max_workers: int = 4, name: str = "Preloader"):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.results: Dict[str, Any] = {}
        self._name = name
        logger.info(f"[{name}] 初始化完成: max_workers={max_workers}")

    def _load_module(self, name: str, loader: Callable) -> tuple[str, Any]:
        """加载单个模块（返回名称+实例元组）"""
        start_time = time.perf_counter()
        instance = loader()
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.debug(f"[{self._name}] 模块 {name} 加载完成: {elapsed_ms:.2f}ms")
        return name, instance

    def shutdown(self):
        """关闭预加载器"""
        self.executor.shutdown(wait=True)
        logger.info(f"[{self._name}] 已关闭")


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
            "module_name": "_core",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
