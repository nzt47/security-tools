"""P6 快照频率控制模块

防止过于频繁的快照持久化操作。
"""

import time
import logging
import json
import uuid

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


class SnapshotFrequencyController:
    """快照频率控制器 - 增强系统安全性
    
    防止过于频繁的快照持久化操作，减少磁盘I/O和安全风险
    """
    
    def __init__(
        self,
        min_interval_seconds: float = 300.0,  # 最小间隔5分钟
        max_snapshots: int = 5,  # 最多保留5个快照
    ):
        self.min_interval_seconds = min_interval_seconds
        self.max_snapshots = max_snapshots
        self.last_save_time: float = 0.0
        self.save_count: int = 0
        
    def can_save(self, force: bool = False) -> bool:
        """检查是否可以保存快照"""
        if force:
            return True
            
        current_time = time.time()
        elapsed = current_time - self.last_save_time
        
        if elapsed >= self.min_interval_seconds:
            return True
            
        logger.debug(
            f"[P6] 快照保存过于频繁，上次保存于 {elapsed:.1f}秒前，"
            f"最小间隔 {self.min_interval_seconds}秒"
        )
        return False
        
    def on_save_success(self):
        """保存成功后的回调"""
        self.last_save_time = time.time()
        self.save_count += 1


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
            "module_name": "frequency",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
