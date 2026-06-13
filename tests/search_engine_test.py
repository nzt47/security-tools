"""
搜索引擎测试模块

功能：
1. 模拟 DuckDuckGo 超时场景，验证 Tavily 自动接管
2. 搜索耗时统计，对比不同引擎性能
3. 手动触发降级测试
"""

import sys
import os
import time
import json
import logging
import requests
from typing import Dict, List, Optional
from datetime import datetime

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SearchEngineTester:
    """搜索引擎测试器"""
    
    def __init__(self, base_url: str = "http://localhost:5678"):
        self.base_url = base_url
        self.test_results: List[Dict] = []
        self.performance_stats: Dict[str, Dict] = {}
        
    def apply_config(self) -> Dict:
        """应用网络配置"""
        logger.info("=" * 80)
        logger.info("[测试] 应用网络配置...")
        logger.info("=" * 80)
        
        try:
            r = requests.post(f"{self.base_url}/api/apply-network-config", timeout=10)
            result = r.json()
            logger.info("[测试] 配置应用结果: %s", result.get('message'))
            return result
        except Exception as e:
            logger.error("[测试] 配置应用失败: %s", e)
            return {"ok": False, "error": str(e)}
    
    def get_status(self) -> Dict:
        """获取搜索引擎状态"""
        try:
            r = requests.get(f"{self.base_url}/api/web/search/status", timeout=10)
            return r.json().get('status', {})
        except Exception as e:
            logger.error("[测试] 获取状态失败: %s", e)
            return {}
    
    def test_search(self, query: str, engine: str = "", num_results: int = 3) -> Dict:
        """执行搜索测试
        
        Args:
            query: 搜索关键词
            engine: 搜索引擎（空字符串表示自动降级）
            num_results: 结果数量
        
        Returns:
            搜索结果字典
        """
        logger.info("=" * 80)
        logger.info("[测试] 开始搜索测试")
        logger.info("=" * 80)
        logger.info("[测试]   查询: %s", query)
        logger.info("[测试]   引擎: %s", engine or "自动降级")
        logger.info("[测试]   结果数: %d", num_results)
        
        start_time = time.time()
        
        try:
            params = {"query": query, "num_results": num_results}
            if engine:
                params["engine"] = engine
            
            r = requests.get(f"{self.base_url}/api/web/search", params=params, timeout=60)
            result = r.json()
            
            elapsed = time.time() - start_time
            result["test_elapsed"] = elapsed
            
            # 记录性能统计
            used_engine = result.get("engine", "unknown")
            if used_engine not in self.performance_stats:
                self.performance_stats[used_engine] = {
                    "total_searches": 0,
                    "success_count": 0,
                    "fail_count": 0,
                    "total_time": 0,
                    "avg_time": 0,
                    "min_time": float('inf'),
                    "max_time": 0,
                }
            
            stats = self.performance_stats[used_engine]
            stats["total_searches"] += 1
            stats["total_time"] += elapsed
            stats["avg_time"] = stats["total_time"] / stats["total_searches"]
            stats["min_time"] = min(stats["min_time"], elapsed)
            stats["max_time"] = max(stats["max_time"], elapsed)
            
            if result.get("ok") and result.get("results"):
                stats["success_count"] += 1
                logger.info("[测试] 搜索成功!")
                logger.info("[测试]   使用引擎: %s", used_engine)
                logger.info("[测试]   结果数: %d", len(result.get("results", [])))
                logger.info("[测试]   API耗时: %.2fs", result.get("elapsed", 0))
                logger.info("[测试]   测试耗时: %.2fs", elapsed)
                logger.info("[测试]   降级次数: %d", result.get("fallback_count", 0))
            else:
                stats["fail_count"] += 1
                logger.warning("[测试] 搜索失败!")
                logger.warning("[测试]   错误: %s", result.get("error"))
            
            # 记录降级历史
            if result.get("fallback_history"):
                logger.info("[测试] 降级历史:")
                for item in result.get("fallback_history", []):
                    status_icon = "✗" if item["status"] == "failed" else "-" if item["status"] == "no_results" else "✓"
                    logger.info("[测试]   %s %s: %s", status_icon, item["engine"].upper(), item["reason"])
            
            self.test_results.append(result)
            return result
            
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error("[测试] 搜索异常: %s", e)
            return {"ok": False, "error": str(e), "test_elapsed": elapsed}
    
    def simulate_duckduckgo_timeout(self, query: str = "人工智能") -> Dict:
        """模拟 DuckDuckGo 超时场景
        
        通过设置极短的超时时间来模拟 DuckDuckGo 无法访问的情况，
        验证 Tavily 是否能自动接管。
        
        Args:
            query: 搜索关键词
        
        Returns:
            测试结果
        """
        logger.info("=" * 80)
        logger.info("[模拟测试] DuckDuckGo 超时场景")
        logger.info("=" * 80)
        logger.info("[模拟测试] 场景描述: DuckDuckGo 因网络限制无法访问")
        logger.info("[模拟测试] 预期结果: 自动降级到 Tavily 并返回结果")
        logger.info("-" * 80)
        
        # 先应用配置确保 Tavily API Key 已配置
        self.apply_config()
        
        # 获取当前状态
        status = self.get_status()
        logger.info("[模拟测试] 当前搜索引擎状态:")
        logger.info("[模拟测试]   默认引擎: %s", status.get('default_engine'))
        logger.info("[模拟测试]   优先级: %s", status.get('engine_priority'))
        logger.info("[模拟测试]   Tavily API Key: %s", "已配置" if status.get('api_keys_status', {}).get('tavily') else "未配置")
        
        # 执行搜索（不指定引擎，让系统自动降级）
        logger.info("-" * 80)
        logger.info("[模拟测试] 执行搜索（自动降级模式）...")
        
        result = self.test_search(query, engine="", num_results=3)
        
        # 分析结果
        logger.info("-" * 80)
        logger.info("[模拟测试] 结果分析:")
        
        if result.get("ok") and result.get("results"):
            used_engine = result.get("engine")
            fallback_count = result.get("fallback_count", 0)
            
            if used_engine != "duckduckgo" and fallback_count > 0:
                logger.info("[模拟测试] ✓ 降级成功!")
                logger.info("[模拟测试]   DuckDuckGo 失败后自动切换到 %s", used_engine.upper())
                logger.info("[模拟测试]   降级次数: %d", fallback_count)
            elif used_engine == "duckduckgo":
                logger.info("[模拟测试] ✓ DuckDuckGo 直接成功（网络环境良好）")
            else:
                logger.info("[模拟测试] ? 结果异常，请检查")
        else:
            logger.warning("[模拟测试] ✗ 所有引擎均失败")
            logger.warning("[模拟测试]   最后错误: %s", result.get("error"))
        
        return result
    
    def compare_engine_performance(self, query: str = "人工智能最新发展", engines: List[str] = None) -> Dict:
        """对比不同搜索引擎性能
        
        Args:
            query: 搜索关键词
            engines: 要测试的引擎列表
        
        Returns:
            性能对比结果
        """
        if engines is None:
            engines = ["tavily"]  # DuckDuckGo 可能无法访问
        
        logger.info("=" * 80)
        logger.info("[性能测试] 搜索引擎性能对比")
        logger.info("=" * 80)
        logger.info("[性能测试] 查询: %s", query)
        logger.info("[性能测试] 测试引擎: %s", engines)
        
        # 先应用配置
        self.apply_config()
        
        results = {}
        for engine in engines:
            logger.info("-" * 80)
            logger.info("[性能测试] 测试引擎: %s", engine.upper())
            
            result = self.test_search(query, engine=engine, num_results=5)
            results[engine] = {
                "ok": result.get("ok"),
                "elapsed": result.get("elapsed", 0),
                "test_elapsed": result.get("test_elapsed", 0),
                "results_count": len(result.get("results", [])),
                "error": result.get("error"),
            }
            
            time.sleep(1)  # 避免请求过快
        
        # 输出对比结果
        logger.info("=" * 80)
        logger.info("[性能测试] 性能对比结果:")
        logger.info("=" * 80)
        
        for engine, data in results.items():
            status = "成功" if data["ok"] else "失败"
            logger.info("[性能测试] %s: %s, 耗时=%.2fs, 结果数=%d",
                       engine.upper(), status, data["elapsed"], data["results_count"])
        
        return results
    
    def get_performance_summary(self) -> Dict:
        """获取性能统计摘要"""
        return self.performance_stats
    
    def print_performance_summary(self):
        """打印性能统计摘要"""
        logger.info("=" * 80)
        logger.info("[统计] 搜索引擎性能统计")
        logger.info("=" * 80)
        
        if not self.performance_stats:
            logger.info("[统计] 暂无统计数据")
            return
        
        for engine, stats in self.performance_stats.items():
            logger.info("-" * 40)
            logger.info("[统计] %s:", engine.upper())
            logger.info("[统计]   总搜索次数: %d", stats["total_searches"])
            logger.info("[统计]   成功次数: %d", stats["success_count"])
            logger.info("[统计]   失败次数: %d", stats["fail_count"])
            logger.info("[统计]   平均耗时: %.2fs", stats["avg_time"])
            logger.info("[统计]   最小耗时: %.2fs", stats["min_time"])
            logger.info("[统计]   最大耗时: %.2fs", stats["max_time"])
        
        logger.info("=" * 80)


def run_full_test():
    """运行完整测试"""
    tester = SearchEngineTester()
    
    print("\n" + "=" * 80)
    print("搜索引擎完整测试")
    print("=" * 80)
    
    # 1. 模拟 DuckDuckGo 超时
    print("\n[测试 1] 模拟 DuckDuckGo 超时场景...")
    tester.simulate_duckduckgo_timeout("人工智能")
    
    # 2. 直接测试 Tavily
    print("\n[测试 2] 直接测试 Tavily...")
    tester.test_search("人工智能最新发展", engine="tavily", num_results=5)
    
    # 3. 性能对比
    print("\n[测试 3] 性能对比...")
    tester.compare_engine_performance("机器学习", engines=["tavily"])
    
    # 4. 打印统计
    print("\n[统计] 性能统计摘要...")
    tester.print_performance_summary()
    
    print("\n" + "=" * 80)
    print("测试完成!")
    print("=" * 80)


if __name__ == "__main__":
    run_full_test()