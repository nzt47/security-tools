#!/usr/bin/env python3
"""
指标查询脚本
用于查询和分析云枢智能体的监控指标
"""

import argparse
import requests
import json
import time
from typing import Dict, List


class MetricsQuerier:
    def __init__(self, base_url: str = "http://localhost:5678"):
        self.base_url = base_url
    
    def get_metrics(self) -> Dict:
        """获取JSON格式的运行时指标"""
        url = f"{self.base_url}/api/diagnostics/metrics"
        try:
            response = requests.get(url, timeout=10)
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}
    
    def get_prometheus_metrics(self) -> str:
        """获取Prometheus格式的指标"""
        url = f"{self.base_url}/metrics"
        try:
            response = requests.get(url, timeout=10)
            return response.text
        except requests.exceptions.RequestException as e:
            return f"Error: {e}"
    
    def parse_prometheus_metrics(self, text: str) -> Dict:
        """解析Prometheus格式指标"""
        metrics = {}
        lines = text.strip().split("\n")
        current_metric = None
        
        for line in lines:
            line = line.strip()
            if line.startswith("# HELP"):
                parts = line.split(" ", 2)
                if len(parts) >= 3:
                    current_metric = parts[2]
                    metrics[current_metric] = []
            elif line.startswith("# TYPE"):
                continue
            elif line and not line.startswith("#") and current_metric:
                metrics[current_metric].append(line)
        
        return metrics
    
    def analyze_metrics(self) -> Dict:
        """分析指标并生成报告"""
        data = self.get_metrics()
        report = {"timestamp": time.time(), "analysis": {}}
        
        # 分析直方图
        histograms = data.get("histograms", {})
        if histograms:
            report["analysis"]["latency_analysis"] = {}
            for name, stats in histograms.items():
                report["analysis"]["latency_analysis"][name] = {
                    "count": stats.get("count", 0),
                    "avg_ms": round(stats.get("avg", 0) * 1000, 2),
                    "p50_ms": round(stats.get("p50", 0) * 1000, 2),
                    "p95_ms": round(stats.get("p95", 0) * 1000, 2),
                    "p99_ms": round(stats.get("p99", 0) * 1000, 2),
                    "max_ms": round(stats.get("max", 0) * 1000, 2)
                }
        
        # 分析计数器
        counters = data.get("counters", {})
        if counters:
            report["analysis"]["counters"] = counters
        
        return report
    
    def print_analysis_report(self, report: Dict):
        """打印分析报告"""
        print("\n" + "="*70)
        print("📊 云枢智能体指标分析报告")
        print("="*70)
        
        # 延迟分析
        latency = report["analysis"].get("latency_analysis", {})
        if latency:
            print("\n⏱️ 延迟分析:")
            for name, stats in latency.items():
                print(f"\n   📈 {name}:")
                print(f"      调用次数: {stats['count']}")
                print(f"      平均延迟: {stats['avg_ms']}ms")
                print(f"      P50延迟: {stats['p50_ms']}ms")
                print(f"      P95延迟: {stats['p95_ms']}ms")
                print(f"      P99延迟: {stats['p99_ms']}ms")
                print(f"      最大延迟: {stats['max_ms']}ms")
                
                # 性能警告
                if stats["p95_ms"] > 1000:
                    print("      ⚠️ P95延迟超过1秒，建议优化")
        
        # 计数器
        counters = report["analysis"].get("counters", {})
        if counters:
            print("\n📊 计数器统计:")
            for name, value in counters.items():
                print(f"   • {name}: {value}")
        
        print("\n" + "="*70)


def main():
    parser = argparse.ArgumentParser(description="云枢智能体指标查询")
    parser.add_argument("--url", default="http://localhost:5678", help="服务地址")
    parser.add_argument("--format", choices=["json", "prometheus", "analysis"], 
                        default="analysis", help="输出格式")
    args = parser.parse_args()
    
    querier = MetricsQuerier(args.url)
    
    if args.format == "json":
        metrics = querier.get_metrics()
        print(json.dumps(metrics, indent=2))
    elif args.format == "prometheus":
        metrics = querier.get_prometheus_metrics()
        print(metrics)
    elif args.format == "analysis":
        report = querier.analyze_metrics()
        querier.print_analysis_report(report)


if __name__ == "__main__":
    main()