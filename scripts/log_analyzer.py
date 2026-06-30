#!/usr/bin/env python3
"""
日志分析脚本
用于查询和分析云枢智能体的日志数据
"""

import argparse
import requests
import json
import time
from typing import Dict, List, Optional


class LogAnalyzer:
    def __init__(self, base_url: str = "http://localhost:5678"):
        self.base_url = base_url
    
    def get_logs(self, level: str = None, limit: int = 50) -> List:
        """获取日志列表"""
        url = f"{self.base_url}/api/diagnostics/logs"
        params = {"limit": limit}
        if level:
            params["level"] = level
        
        try:
            response = requests.get(url, params=params, timeout=10)
            return response.json()
        except requests.exceptions.RequestException as e:
            return [{"error": str(e)}]
    
    def get_observability_logs(self, level: str = None) -> List:
        """获取可观测性日志"""
        url = f"{self.base_url}/api/observability/logs"
        params = {}
        if level:
            params["level"] = level
        
        try:
            response = requests.get(url, params=params, timeout=10)
            return response.json()
        except requests.exceptions.RequestException as e:
            return [{"error": str(e)}]
    
    def analyze_logs(self, logs: List) -> Dict:
        """分析日志并生成报告"""
        report = {
            "timestamp": time.time(),
            "total_logs": len(logs),
            "level_distribution": {},
            "service_distribution": {},
            "error_count": 0,
            "warning_count": 0,
            "recent_errors": [],
            "common_errors": {}
        }
        
        for log in logs:
            if isinstance(log, dict):
                # 统计级别分布
                level = log.get("level", "UNKNOWN").upper()
                report["level_distribution"][level] = report["level_distribution"].get(level, 0) + 1
                
                # 统计服务分布
                service = log.get("service", log.get("module_name", "UNKNOWN"))
                report["service_distribution"][service] = report["service_distribution"].get(service, 0) + 1
                
                # 统计错误和警告
                if level == "ERROR":
                    report["error_count"] += 1
                    error_msg = log.get("message", "")
                    if error_msg not in report["common_errors"]:
                        report["common_errors"][error_msg] = 0
                    report["common_errors"][error_msg] += 1
                    
                    if len(report["recent_errors"]) < 5:
                        report["recent_errors"].append(log)
                elif level == "WARNING":
                    report["warning_count"] += 1
        
        return report
    
    def print_analysis_report(self, report: Dict):
        """打印分析报告"""
        print("\n" + "="*70)
        print("📝 云枢智能体日志分析报告")
        print("="*70)
        
        print(f"\n📊 日志统计:")
        print(f"   总日志数: {report['total_logs']}")
        print(f"   错误数: {report['error_count']}")
        print(f"   警告数: {report['warning_count']}")
        
        # 级别分布
        print("\n📈 级别分布:")
        for level, count in report["level_distribution"].items():
            percentage = (count / report["total_logs"]) * 100 if report["total_logs"] > 0 else 0
            print(f"   • {level}: {count} ({percentage:.1f}%)")
        
        # 服务分布
        print("\n🔧 服务分布:")
        for service, count in sorted(report["service_distribution"].items(), key=lambda x: -x[1]):
            print(f"   • {service}: {count}")
        
        # 常见错误
        if report["common_errors"]:
            print("\n🚨 常见错误:")
            for error_msg, count in sorted(report["common_errors"].items(), key=lambda x: -x[1])[:5]:
                print(f"   • [{count}次] {error_msg[:50]}..." if len(error_msg) > 50 else f"   • [{count}次] {error_msg}")
        
        # 最近错误详情
        if report["recent_errors"]:
            print("\n📋 最近错误详情:")
            for i, error in enumerate(report["recent_errors"], 1):
                timestamp = error.get("timestamp", "N/A")
                service = error.get("service", error.get("module_name", "N/A"))
                message = error.get("message", "N/A")
                print(f"\n   {i}. [{timestamp}] [{service}]")
                print(f"      {message}")
        
        # 健康评估
        if report["error_count"] == 0:
            print("\n🎉 日志分析完成，未发现错误")
        elif report["error_count"] < 5:
            print("\n⚠️ 发现少量错误，建议关注")
        else:
            print("\n🚨 发现较多错误，请及时排查")
        
        print("\n" + "="*70)
    
    def tail_logs(self, level: str = None, follow: bool = False):
        """实时日志监控"""
        print(f"\n📡 实时日志监控 (级别: {level or 'ALL'})")
        print("="*70)
        
        last_count = 0
        while True:
            logs = self.get_logs(level=level, limit=10)
            if len(logs) > last_count:
                new_logs = logs[:len(logs) - last_count]
                for log in reversed(new_logs):
                    if isinstance(log, dict):
                        timestamp = log.get("timestamp", "N/A")
                        level = log.get("level", "INFO").upper()
                        service = log.get("service", log.get("module_name", "UNKNOWN"))
                        message = log.get("message", "")
                        print(f"[{timestamp}] [{level}] [{service}] {message}")
                last_count = len(logs)
            
            if not follow:
                break
            time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description="云枢智能体日志分析")
    parser.add_argument("--url", default="http://localhost:5678", help="服务地址")
    parser.add_argument("--level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], 
                        help="按级别过滤")
    parser.add_argument("--limit", type=int, default=50, help="返回条数")
    parser.add_argument("--follow", action="store_true", help="实时监控")
    parser.add_argument("--json", action="store_true", help="输出JSON格式")
    args = parser.parse_args()
    
    analyzer = LogAnalyzer(args.url)
    
    if args.follow:
        analyzer.tail_logs(level=args.level, follow=True)
    else:
        logs = analyzer.get_logs(level=args.level, limit=args.limit)
        
        if args.json:
            print(json.dumps(logs, indent=2))
        else:
            report = analyzer.analyze_logs(logs)
            analyzer.print_analysis_report(report)


if __name__ == "__main__":
    main()