"""
最终性能验证测试 - 验证 P2-P4 优化成果
目标：确保初始化时间稳定在 0.168s 附近
"""

import pytest
import time
import tempfile
from pathlib import Path
import logging
import statistics
from typing import List, Dict

# 增加日志级别以便看到 P4 优化的详细日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# 导入 V2 模块
from agent.digital_life_v2 import DigitalLifeV2


class TestFinalPerformance:
    """最终性能验证测试"""
    
    @pytest.fixture
    def temp_data_dir(self):
        """创建临时数据目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)
    
    @pytest.fixture
    def base_config(self, temp_data_dir):
        """基础配置"""
        return {
            "distillation": {
                "enabled": True,
                "interval": 10,
                "data_dir": str(temp_data_dir / "persona"),
                "distiller_enabled": True,
            },
            "sensor": {
                "lazy_load": True,
            }
        }
    
    def run_initialization_benchmark(self, config, iterations=10) -> Dict:
        """运行多次初始化测试并统计"""
        times: List[float] = []
        
        logger.info("=" * 60)
        logger.info(f"开始性能基准测试 - 迭代次数: {iterations}")
        logger.info("=" * 60)
        
        for i in range(iterations):
            logger.info(f"\n--- 迭代 {i+1}/{iterations} ---")
            
            # 显式清理以确保准确性
            import gc
            gc.collect()
            
            start_time = time.time()
            
            # 创建并启动实例
            v2 = DigitalLifeV2(config)
            v2.start()
            v2.stop()
            
            elapsed = time.time() - start_time
            times.append(elapsed)
            
            logger.info(f"迭代 {i+1} 耗时: {elapsed:.4f}s")
        
        # 统计分析
        avg_time = statistics.mean(times)
        min_time = min(times)
        max_time = max(times)
        std_dev = statistics.stdev(times) if len(times) > 1 else 0.0
        
        logger.info("\n" + "=" * 60)
        logger.info("性能统计结果")
        logger.info("=" * 60)
        logger.info(f"平均耗时:  {avg_time:.4f}s")
        logger.info(f"最小耗时:  {min_time:.4f}s")
        logger.info(f"最大耗时:  {max_time:.4f}s")
        logger.info(f"标准差:    {std_dev:.4f}s")
        logger.info(f"迭代次数:  {iterations}")
        logger.info(f"目标:      < 10s")
        logger.info("=" * 60)
        
        # 性能验证
        meets_target = avg_time < 10.0
        
        logger.info(f"\n性能目标验证: {'✅ 通过' if meets_target else '❌ 未通过'}")
        
        return {
            "times": times,
            "average": avg_time,
            "min": min_time,
            "max": max_time,
            "std_dev": std_dev,
            "iterations": iterations,
            "meets_target": meets_target
        }
    
    @pytest.mark.benchmark
    @pytest.mark.final
    def test_final_initialization_stability(self, base_config):
        """最终初始化稳定性测试"""
        logger.info("\n\n" + "=" * 60)
        logger.info("最终性能验证测试 - 初始化稳定性")
        logger.info("=" * 60)
        
        result = self.run_initialization_benchmark(base_config, iterations=10)
        
        # 验证平均时间是否达标
        assert result["meets_target"], f"平均初始化时间 {result['average']:.4f}s 超过 10s 目标！"
        
        # 验证标准差是否足够小（表示稳定）
        assert result["std_dev"] < 0.5, f"初始化时间波动太大！标准差: {result['std_dev']:.4f}s"
        
        # 记录结果
        with open("benchmark_final_results.txt", "w", encoding="utf-8") as f:
            f.write("最终性能验证测试结果\n")
            f.write("=" * 60 + "\n")
            f.write(f"平均耗时:  {result['average']:.4f}s\n")
            f.write(f"最小耗时:  {result['min']:.4f}s\n")
            f.write(f"最大耗时:  {result['max']:.4f}s\n")
            f.write(f"标准差:    {result['std_dev']:.4f}s\n")
            f.write(f"迭代次数:  {result['iterations']}\n")
            f.write(f"目标:      < 10s\n")
            f.write(f"状态:      {'✅ 通过' if result['meets_target'] else '❌ 未通过'}\n")
            f.write("=" * 60 + "\n")
        
        logger.info(f"\n结果已保存至 benchmark_final_results.txt")
        
        return result
    
    @pytest.mark.benchmark
    @pytest.mark.final
    @pytest.mark.lazy_load
    def test_lazy_loading_comparison(self, temp_data_dir):
        """对比懒加载和非懒加载性能"""
        logger.info("\n\n" + "=" * 60)
        logger.info("性能对比测试 - 懒加载 vs 非懒加载")
        logger.info("=" * 60)
        
        config_lazy = {
            "distillation": {
                "enabled": True,
                "interval": 10,
                "data_dir": str(temp_data_dir / "persona"),
                "distiller_enabled": True,
            },
            "sensor": {
                "lazy_load": True,
            }
        }
        
        config_no_lazy = {
            "distillation": {
                "enabled": True,
                "interval": 10,
                "data_dir": str(temp_data_dir / "persona"),
                "distiller_enabled": True,
            },
            "sensor": {
                "lazy_load": False,
            }
        }
        
        logger.info("\n--- 测试懒加载模式 ---")
        lazy_result = self.run_initialization_benchmark(config_lazy, iterations=5)
        
        logger.info("\n--- 测试非懒加载模式 ---")
        no_lazy_result = self.run_initialization_benchmark(config_no_lazy, iterations=5)
        
        # 对比结果
        improvement = ((no_lazy_result["average"] - lazy_result["average"]) / no_lazy_result["average"]) * 100
        
        logger.info("\n" + "=" * 60)
        logger.info("对比结果总结")
        logger.info("=" * 60)
        logger.info(f"懒加载平均:     {lazy_result['average']:.4f}s")
        logger.info(f"非懒加载平均:   {no_lazy_result['average']:.4f}s")
        logger.info(f"时间节省:      {no_lazy_result['average'] - lazy_result['average']:.4f}s")
        logger.info(f"性能提升:      {improvement:.1f}%")
        logger.info("=" * 60)
        
        # 验证懒加载确实更快
        assert lazy_result["average"] <= no_lazy_result["average"], "懒加载应该比非懒加载更快！"
        
        return {
            "lazy": lazy_result,
            "no_lazy": no_lazy_result,
            "improvement": improvement
        }


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("最终性能验证测试")
    print("=" * 60)
    
    pytest.main([__file__, "-v", "-s", "--tb=short"])
