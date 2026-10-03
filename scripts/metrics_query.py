#!/usr/bin/env python3
"""
指标查询脚本
用于查询和分析云枢智能体的监控指标
"""

import argparse
import os
import requests
import json
import time
from typing import Dict, List


class MetricsQuerier:
    def __init__(self, base_url: str = "http://localhost:5678", token: str = ""):
        self.base_url = base_url
        # 【2026-10-03 修：本脚本此前**必然静默返回空报告**】
        #   /api/diagnostics/metrics 带 @require_token（routes_logging.py:828），
        #   而本脚本从不带令牌 ⇒ 恒 401。更坏的是原实现直接 `response.json()`：
        #   401 的响应体恰好是合法 JSON（{"error": "未授权：..."}），于是**不抛异常**，
        #   上层读不到 histograms/counters，最终打印一份"零指标"的体检报告 ——
        #   看起来像"服务很干净"，实际是"根本没读到"。
        #   注：该端点从未真正免鉴权（CP_API_AUTH_ALLOW 里那条被装饰器遮蔽，
        #   属"影子豁免"，见 app_server.audit_shadowed_exemptions）。本修复不依赖它。
        self.token = token or os.environ.get("CP_API_TOKEN") or os.environ.get("FLASK_API_TOKEN") or ""

    def _headers(self) -> Dict[str, str]:
        if not self.token:
            return {}
        # 两个头都给：Authorization 是规范路径，X-API-Token 走闸门的回退分支。
        return {"Authorization": f"Bearer {self.token}", "X-API-Token": self.token}

    def get_metrics(self) -> Dict:
        """获取JSON格式的运行时指标

        【失败姿态：响亮，不静默】非 200 一律返回带 error 的字典（由调用方打印），
        不把错误响应体当成指标数据继续往下算。
        """
        url = f"{self.base_url}/api/diagnostics/metrics"
        try:
            response = requests.get(url, timeout=10, headers=self._headers())
        except requests.exceptions.RequestException as e:
            return {"error": f"请求失败: {e}"}
        if response.status_code != 200:
            hint = ("（本端点需要 API 令牌：设 CP_API_TOKEN 环境变量或用 --token 传入）"
                    if response.status_code == 401 else "")
            return {"error": f"HTTP {response.status_code}{hint}: {response.text[:200]}"}
        try:
            return response.json()
        except ValueError as e:
            return {"error": f"响应不是 JSON: {e}"}
    
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

        # 【取数失败必须显式传播】原实现吞掉它 → 打印零指标报告（见 get_metrics 注释）。
        if "error" in data and "histograms" not in data:
            report["analysis"]["_fetch_error"] = str(data["error"])
            return report

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

        # 【取数失败要**说出来**】只给退出码 2 而不打印原因，运维只看到"报告是空的"，
        #   与"服务确实没有指标"依旧难以区分 —— 那正是本次要根治的静默失败。
        if report["analysis"].get("_fetch_error"):
            print("\n❌ 指标取数失败：" + str(report["analysis"]["_fetch_error"]))
            print("   （本次未读到任何指标，报告为空**不代表**服务健康）")
            print("\n" + "="*70)
            return

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
    parser.add_argument("--token", default="", help="API 令牌（默认取 CP_API_TOKEN / FLASK_API_TOKEN）")
    parser.add_argument("--format", choices=["json", "prometheus", "analysis"], 
                        default="analysis", help="输出格式")
    args = parser.parse_args()
    
    querier = MetricsQuerier(args.url, token=args.token)
    
    if args.format == "json":
        metrics = querier.get_metrics()
        print(json.dumps(metrics, indent=2))
    elif args.format == "prometheus":
        metrics = querier.get_prometheus_metrics()
        print(metrics)
    elif args.format == "analysis":
        report = querier.analyze_metrics()
        querier.print_analysis_report(report)
        # 【非零退出：让"读不到指标"在脚本层就是失败】
        #   否则 CI/运维把一份零指标报告当成"服务健康"。取数失败与"真的没有指标"
        #   必须在退出码上可区分 —— 本仓 A4' 对 pytest 的同类要求（rc 二义）即此理。
        if report["analysis"].get("_fetch_error"):
            raise SystemExit(2)


if __name__ == "__main__":
    main()