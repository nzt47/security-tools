"""
性能基准测试框架
Phase 3 实施
"""
import os
import time
import logging
import json
from typing import Dict, Any, List, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """基准测试结果"""
    name: str
    iterations: int
    total_time: float
    mean_time: float
    median_time: float
    min_time: float
    max_time: float
    timestamp: str
    metadata: Dict[str, Any] = None
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "iterations": self.iterations,
            "total_time": self.total_time,
            "mean_time": self.mean_time,
            "median_time": self.median_time,
            "min_time": self.min_time,
            "max_time": self.max_time,
            "timestamp": self.timestamp,
            "metadata": self.metadata
        }


class BenchmarkSuite:
    """基准测试套件"""
    
    def __init__(self, name: str, output_dir: str = "./data/benchmarks"):
        self.name = name
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._benchmarks: List[Callable] = []
        self._results: List[BenchmarkResult] = []
        
    def benchmark(self, name: str = None, iterations: int = 100, **metadata):
        """基准测试装饰器
        
        Args:
            name: 测试名称
            iterations: 迭代次数
            metadata: 额外元数据
        """
        def decorator(func: Callable):
            test_name = name or func.__name__
            
            def wrapper():
                logger.info(f"Running benchmark: {test_name} ({iterations} iterations)")
                
                times = []
                for i in range(iterations):
                    start = time.perf_counter()
                    func()
                    end = time.perf_counter()
                    times.append(end - start)
                
                # 计算统计信息
                total = sum(times)
                mean = total / len(times)
                times_sorted = sorted(times)
                median = times_sorted[len(times_sorted) // 2]
                min_time = times_sorted[0]
                max_time = times_sorted[-1]
                
                result = BenchmarkResult(
                    name=test_name,
                    iterations=iterations,
                    total_time=total,
                    mean_time=mean,
                    median_time=median,
                    min_time=min_time,
                    max_time=max_time,
                    timestamp=datetime.now().isoformat(),
                    metadata=metadata
                )
                
                self._results.append(result)
                logger.info(f"  ✓ {test_name}")
                logger.info(f"    - Mean: {mean*1000:.2f}ms")
                logger.info(f"    - Median: {median*1000:.2f}ms")
                logger.info(f"    - Min: {min_time*1000:.2f}ms")
                logger.info(f"    - Max: {max_time*1000:.2f}ms")
                return result
            
            self._benchmarks.append(wrapper)
            return func
        return decorator
    
    def run_all(self) -> List[BenchmarkResult]:
        """运行所有基准测试"""
        logger.info("="*70)
        logger.info(f"Starting Benchmark Suite: {self.name}")
        logger.info("="*70)
        
        results = []
        for benchmark in self._benchmarks:
            try:
                result = benchmark()
                results.append(result)
            except Exception as e:
                logger.error(f"Benchmark failed: {e}")
                import traceback
                logger.error(traceback.format_exc())
        
        logger.info(f"Completed {len(results)}/{len(self._benchmarks)} benchmarks")
        return results
    
    def save_results(self, filename: str = None):
        """保存基准测试结果"""
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.name}_{timestamp}.json"
        
        filepath = self.output_dir / filename
        
        data = {
            "suite": self.name,
            "timestamp": datetime.now().isoformat(),
            "results": [r.to_dict() for r in self._results]
        }
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Results saved to: {filepath}")
        return filepath
    
    def generate_report(self, filepath: str = None):
        """生成Markdown报告"""
        if not filepath:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = self.output_dir / f"{self.name}_report_{timestamp}.md"
        
        lines = [
            f"# {self.name} 基准测试报告",
            "",
            f"生成时间: {datetime.now().isoformat()}",
            "",
            "## 结果概览",
            ""
        ]
        
        if self._results:
            lines.append("| 测试名称 | 迭代次数 | 平均时间 | 中位数时间 | 最小时间 | 最大时间 |")
            lines.append("|---------|---------|---------|----------|---------|---------|")
            
            for r in self._results:
                lines.append(
                    f"| {r.name} | {r.iterations} | {r.mean_time*1000:.2f}ms | {r.median_time*1000:.2f}ms | {r.min_time*1000:.2f}ms | {r.max_time*1000:.2f}ms |"
                )
            
            lines.append("")
            lines.append("## 详细结果")
            for r in self._results:
                lines.append(f"### {r.name}")
                lines.append(f"- 迭代次数: {r.iterations}")
                lines.append(f"- 总耗时: {r.total_time:.2f}s")
                lines.append(f"- 平均: {r.mean_time*1000:.2f}ms")
                lines.append(f"- 中位数: {r.median_time*1000:.2f}ms")
                lines.append(f"- 最小: {r.min_time*1000:.2f}ms")
                lines.append(f"- 最大: {r.max_time*1000:.2f}ms")
                if r.metadata:
                    lines.append(f"- 元数据: {json.dumps(r.metadata, ensure_ascii=False)}")
                lines.append("")
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        
        logger.info(f"Report saved to: {filepath}")
        return filepath
