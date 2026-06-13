"""
DigitalLifeV2 性能监控模块
用于追踪和记录各模块的初始化耗时
"""
import time
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ModuleInitRecord:
    """模块初始化记录"""
    name: str
    start_time: float
    end_time: float = 0.0
    duration_ms: float = 0.0
    success: bool = True
    error: str = ""

    def finish(self):
        """标记模块初始化完成"""
        self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000

    def __str__(self):
        status = "✅" if self.success else "❌"
        return f"{status} {self.name}: {self.duration_ms:.2f}ms"


class InitPerformanceTracker:
    """初始化性能追踪器"""

    def __init__(self):
        self.records: Dict[str, ModuleInitRecord] = {}
        self.start_time = time.time()

    def start_module(self, module_name: str):
        """开始追踪某个模块的初始化"""
        self.records[module_name] = ModuleInitRecord(
            name=module_name,
            start_time=time.time()
        )
        logger.info(f"[性能追踪] 开始初始化模块: {module_name}")

    def finish_module(self, module_name: str, success: bool = True, error: str = ""):
        """完成某个模块的初始化追踪"""
        if module_name in self.records:
            record = self.records[module_name]
            record.finish()
            record.success = success
            record.error = error

            status = "成功" if success else f"失败: {error}"
            logger.info(f"[性能追踪] 模块 {module_name} 初始化完成: {record.duration_ms:.2f}ms ({status})")

    def get_total_time(self) -> float:
        """获取总初始化时间（毫秒）"""
        total = time.time() - self.start_time
        return total * 1000

    def get_summary(self) -> str:
        """生成性能总结报告"""
        lines = []
        lines.append("=" * 60)
        lines.append("初始化性能总结")
        lines.append("=" * 60)

        # 按耗时排序
        sorted_records = sorted(
            self.records.values(),
            key=lambda r: r.duration_ms,
            reverse=True
        )

        total_ms = self.get_total_time()
        lines.append(f"\n总初始化时间: {total_ms:.2f}ms")
        lines.append("\n各模块耗时（按耗时排序）:")

        for record in sorted_records:
            percentage = (record.duration_ms / total_ms * 100) if total_ms > 0 else 0
            lines.append(f"  {record}")
            lines.append(f"    占比: {percentage:.1f}%")

        lines.append("\n关键指标:")
        lines.append(f"  模块总数: {len(self.records)}")
        lines.append(f"  成功数: {sum(1 for r in self.records.values() if r.success)}")
        lines.append(f"  失败数: {sum(1 for r in self.records.values() if not r.success)}")

        # 找出瓶颈
        if sorted_records:
            bottleneck = sorted_records[0]
            lines.append(f"\n主要瓶颈: {bottleneck.name} ({bottleneck.duration_ms:.2f}ms, {bottleneck.duration_ms/total_ms*100:.1f}%)")

        lines.append("=" * 60)
        return "\n".join(lines)

    def print_summary(self):
        """打印性能总结"""
        print(self.get_summary())

    def get_bottlenecks(self, threshold_ms: float = 50.0) -> List[ModuleInitRecord]:
        """获取耗时超过阈值的瓶颈模块"""
        return [
            r for r in self.records.values()
            if r.duration_ms > threshold_ms
        ]

    def get_timeline(self) -> List[Dict]:
        """获取初始化时间线"""
        timeline = []
        for name, record in sorted(self.records.items(), key=lambda x: x[1].start_time):
            timeline.append({
                "module": name,
                "start": record.start_time,
                "end": record.end_time,
                "duration_ms": record.duration_ms,
                "success": record.success
            })
        return timeline


class Timer:
    """简单的计时器类"""
    
    def __init__(self, name: str = ""):
        self.name = name
        self.start_time = time.time()
        self.end_time = None
        self.elapsed = 0.0
    
    def stop(self):
        """停止计时器"""
        self.end_time = time.time()
        self.elapsed = self.end_time - self.start_time
        return self.elapsed
    
    def __enter__(self):
        """上下文管理器入口"""
        return self
    
    def __exit__(self, *args):
        """上下文管理器出口"""
        self.stop()
        if self.name:
            logger.debug(f"[Timer] {self.name} 耗时: {self.elapsed:.3f}s")


def log_module_load_time(module_name: str, elapsed_time: float):
    """记录模块加载时间"""
    logger.info(f"[性能] 模块 {module_name} 加载完成，耗时: {elapsed_time:.3f}s")


def get_performance_recorder():
    """获取性能记录器实例"""
    return InitPerformanceTracker()
