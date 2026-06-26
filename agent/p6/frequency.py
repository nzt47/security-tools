"""P6 快照频率控制模块

防止过于频繁的快照持久化操作。
"""

import time
import logging

logger = logging.getLogger(__name__)

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
