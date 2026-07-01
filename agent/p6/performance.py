"""P6 快照性能监控模块

提供快照操作的性能指标追踪。
"""

import time
import logging
import json
import uuid
from dataclasses import dataclass, field
from typing import Dict, Any

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


@dataclass
class PerformanceMetrics:
    """性能指标数据"""
    total_saves: int = 0
    total_loads: int = 0
    total_save_time_ms: float = 0.0
    total_load_time_ms: float = 0.0
    avg_save_time_ms: float = 0.0
    avg_load_time_ms: float = 0.0
    total_space_saved_bytes: int = 0
    snapshot_count: int = 0
    last_save_time_ms: float = 0.0
    last_load_time_ms: float = 0.0
    
    # 模块级性能
    module_serialize_times: Dict[str, float] = field(default_factory=dict)
    module_deserialize_times: Dict[str, float] = field(default_factory=dict)
    module_data_sizes: Dict[str, int] = field(default_factory=dict)


class SnapshotPerformanceMonitor:
    """快照性能监控器
    
    实时追踪快照操作性能，提供性能面板
    """
    
    def __init__(self):
        self.metrics = PerformanceMetrics()
        self.start_time = time.time()
        logger.info("[P6] 性能监控器初始化完成")
        
    def record_save(self, elapsed_ms: float, space_saved: int = 0):
        """记录一次快照保存操作"""
        self.metrics.total_saves += 1
        self.metrics.total_save_time_ms += elapsed_ms
        self.metrics.avg_save_time_ms = self.metrics.total_save_time_ms / self.metrics.total_saves
        self.metrics.last_save_time_ms = elapsed_ms
        self.metrics.total_space_saved_bytes += space_saved
        self.metrics.snapshot_count += 1
        logger.info(
            f"[P6] 性能记录: 保存 {elapsed_ms:.2f}ms, "
            f"累计节省 {self.metrics.total_space_saved_bytes:,} bytes"
        )
        
    def record_load(self, elapsed_ms: float):
        """记录一次快照加载操作"""
        self.metrics.total_loads += 1
        self.metrics.total_load_time_ms += elapsed_ms
        self.metrics.avg_load_time_ms = self.metrics.total_load_time_ms / self.metrics.total_loads
        self.metrics.last_load_time_ms = elapsed_ms
        
    def record_module_serialize(self, module_name: str, elapsed_ms: float, data_size: int):
        """记录模块序列化时间"""
        self.metrics.module_serialize_times[module_name] = elapsed_ms
        self.metrics.module_data_sizes[module_name] = data_size
        
    def record_module_deserialize(self, module_name: str, elapsed_ms: float):
        """记录模块反序列化时间"""
        self.metrics.module_deserialize_times[module_name] = elapsed_ms
        
    def get_performance_summary(self) -> Dict[str, Any]:
        """获取性能摘要信息"""
        return {
            "uptime_seconds": time.time() - self.start_time,
            "total_saves": self.metrics.total_saves,
            "total_loads": self.metrics.total_loads,
            "avg_save_ms": self.metrics.avg_save_time_ms,
            "avg_load_ms": self.metrics.avg_load_time_ms,
            "last_save_ms": self.metrics.last_save_time_ms,
            "last_load_ms": self.metrics.last_load_time_ms,
            "total_space_saved_bytes": self.metrics.total_space_saved_bytes,
            "module_stats": {
                name: {
                    "serialize_ms": self.metrics.module_serialize_times.get(name, 0.0),
                    "deserialize_ms": self.metrics.module_deserialize_times.get(name, 0.0),
                    "size_bytes": self.metrics.module_data_sizes.get(name, 0),
                }
                for name in self.metrics.module_serialize_times.keys()
            },
        }
        
    def print_performance_panel(self):
        """打印性能面板"""
        summary = self.get_performance_summary()
        
        print("\n" + "="*70)
        print("🚀 P6 快照系统性能监控面板")
        print("="*70)
        print(f"运行时间: {summary['uptime_seconds']:.1f} 秒")
        print(f"总保存次数: {summary['total_saves']}")
        print(f"总加载次数: {summary['total_loads']}")
        print(f"\n平均保存时间: {summary['avg_save_ms']:.2f} ms")
        print(f"平均加载时间: {summary['avg_load_ms']:.2f} ms")
        print(f"上次保存时间: {summary['last_save_ms']:.2f} ms")
        print(f"上次加载时间: {summary['last_load_ms']:.2f} ms")
        print(f"\n累计节省空间: {summary['total_space_saved_bytes']:,} bytes")
        print("="*70)
        
        if summary['module_stats']:
            print("\n📦 模块性能统计:")
            print("-"*70)
            for module_name, stats in summary['module_stats'].items():
                print(
                    f"{module_name:20s} | "
                    f"序列化: {stats['serialize_ms']:.2f} ms | "
                    f"大小: {stats['size_bytes']:,} bytes"
                )
            print("="*70)


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
            "module_name": "performance",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
