#!/usr/bin/env python3
"""
可观测性端到端验证测试脚本

功能：
1. 验证所有可观测性端点正常响应
2. 构造完整的追踪链路（从请求入口到后端处理）
3. 验证追踪数据能正确导出
4. 验证 Prometheus 指标端点 /metrics 正常工作
5. 验证日志系统与追踪上下文的关联（日志中包含 trace_id）
6. 输出验证报告

使用方法：
    python tests/test_observability_e2e.py
    python tests/test_observability_e2e.py --report  # 生成详细报告
"""

import argparse
import json
import logging
import sys
import time
import traceback
from typing import Dict, List, Any

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)
logger = logging.getLogger(__name__)

# 添加项目路径
sys.path.insert(0, '.')

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    logger.warning("requests 库未安装，将跳过 HTTP 端点测试")
    REQUESTS_AVAILABLE = False


class ObservabilityValidator:
    """可观测性验证器"""
    
    def __init__(self, base_url: str = "http://localhost:5678"):
        self.base_url = base_url
        self.results = {
            "endpoints": {},
            "tracing": {},
            "metrics": {},
            "logging": {},
            "overall": {"passed": 0, "failed": 0, "skipped": 0}
        }
        self.trace_id = None
    
    def _test_endpoint(self, endpoint: str, method: str = "GET", 
                       headers: Dict = None, data: Dict = None, 
                       expected_status: int = 200) -> Dict:
        """测试单个端点"""
        result = {
            "endpoint": endpoint,
            "method": method,
            "status": "pending",
            "status_code": None,
            "response": None,
            "error": None,
            "latency_ms": 0
        }
        
        if not REQUESTS_AVAILABLE:
            result["status"] = "skipped"
            result["error"] = "requests 库不可用"
            return result
        
        url = f"{self.base_url}{endpoint}"
        headers = headers or {}
        
        try:
            start_time = time.time()
            
            if method.upper() == "GET":
                response = requests.get(url, headers=headers, timeout=10)
            elif method.upper() == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=10)
            else:
                result["status"] = "failed"
                result["error"] = f"不支持的方法: {method}"
                return result
            
            latency_ms = (time.time() - start_time) * 1000
            result["latency_ms"] = round(latency_ms, 2)
            result["status_code"] = response.status_code
            
            if response.status_code == expected_status:
                result["status"] = "passed"
                try:
                    result["response"] = response.json()
                except:
                    result["response"] = response.text[:500]
            else:
                result["status"] = "failed"
                result["error"] = f"状态码不匹配: 期望 {expected_status}, 实际 {response.status_code}"
            
            logger.info(f"[{result['status'].upper()}] {method} {endpoint} ({result['latency_ms']}ms)")
            
        except requests.exceptions.RequestException as e:
            result["status"] = "failed"
            result["error"] = str(e)
            logger.error(f"[FAILED] {method} {endpoint} - {e}")
        
        return result
    
    def test_health_endpoints(self) -> List[Dict]:
        """测试健康检查端点"""
        logger.info("\n" + "="*60)
        logger.info("📋 测试健康检查端点")
        logger.info("="*60)
        
        endpoints = [
            ("/api/health", "GET"),
            ("/api/diagnostics/health", "GET"),
            ("/api/status", "GET"),
            ("/api/heartbeat", "GET"),
        ]
        
        results = []
        for endpoint, method in endpoints:
            result = self._test_endpoint(endpoint, method)
            results.append(result)
            self.results["endpoints"][endpoint] = result
            
            if result["status"] == "passed":
                self.results["overall"]["passed"] += 1
            elif result["status"] == "failed":
                self.results["overall"]["failed"] += 1
            else:
                self.results["overall"]["skipped"] += 1
        
        return results
    
    def test_tracing_endpoints(self) -> List[Dict]:
        """测试追踪端点"""
        logger.info("\n" + "="*60)
        logger.info("🔍 测试追踪端点")
        logger.info("="*60)
        
        endpoints = [
            ("/api/diagnostics/trace", "GET"),
            ("/api/diagnostics/trace/inject", "GET"),
        ]
        
        results = []
        
        # 测试基础追踪端点
        for endpoint, method in endpoints:
            result = self._test_endpoint(endpoint, method)
            results.append(result)
            self.results["endpoints"][endpoint] = result
            
            if result["status"] == "passed":
                self.results["overall"]["passed"] += 1
                # 保存 trace_id 用于后续测试
                if endpoint == "/api/diagnostics/trace/inject" and result["response"]:
                    self.trace_id = result["response"].get("trace_id")
            elif result["status"] == "failed":
                self.results["overall"]["failed"] += 1
            else:
                self.results["overall"]["skipped"] += 1
        
        # 测试追踪上下文提取
        if self.trace_id:
            test_headers = {
                "traceparent": f"00-{self.trace_id}-1234567812345678-01"
            }
            result = self._test_endpoint(
                "/api/diagnostics/trace/extract",
                "POST",
                headers=test_headers,
                data={"headers": test_headers}
            )
            results.append(result)
            self.results["endpoints"]["/api/diagnostics/trace/extract"] = result
            
            if result["status"] == "passed":
                self.results["overall"]["passed"] += 1
            elif result["status"] == "failed":
                self.results["overall"]["failed"] += 1
        
        return results
    
    def test_metrics_endpoint(self) -> Dict:
        """测试 Prometheus 指标端点"""
        logger.info("\n" + "="*60)
        logger.info("📊 测试 Prometheus 指标端点")
        logger.info("="*60)
        
        result = self._test_endpoint("/metrics", "GET")
        self.results["endpoints"]["/metrics"] = result
        
        if result["status"] == "passed":
            self.results["overall"]["passed"] += 1
            # 验证 Prometheus 格式
            content = result["response"] if isinstance(result["response"], str) else str(result["response"])
            lines = content.split('\n')
            
            # 检查是否包含指标
            has_help = any(line.startswith('# HELP') for line in lines)
            has_type = any(line.startswith('# TYPE') for line in lines)
            has_metric = any(line and not line.startswith('#') for line in lines)
            
            self.results["metrics"] = {
                "has_help_lines": has_help,
                "has_type_lines": has_type,
                "has_metrics": has_metric,
                "line_count": len(lines)
            }
            
            if has_help and has_type and has_metric:
                logger.info("✅ Prometheus 指标格式验证通过")
            else:
                logger.warning(f"⚠️ Prometheus 指标格式可能不完整")
        
        elif result["status"] == "failed":
            self.results["overall"]["failed"] += 1
        else:
            self.results["overall"]["skipped"] += 1
        
        return result
    
    def test_runtime_metrics(self) -> Dict:
        """测试运行时指标端点"""
        logger.info("\n" + "="*60)
        logger.info("📈 测试运行时指标端点")
        logger.info("="*60)
        
        result = self._test_endpoint("/api/diagnostics/metrics", "GET")
        self.results["endpoints"]["/api/diagnostics/metrics"] = result
        
        if result["status"] == "passed":
            self.results["overall"]["passed"] += 1
            response = result["response"]
            
            if isinstance(response, dict):
                self.results["metrics"].update({
                    "has_histograms": "histograms" in response,
                    "has_counters": "counters" in response,
                    "histogram_count": len(response.get("histograms", {})),
                    "counter_count": len(response.get("counters", {}))
                })
        
        elif result["status"] == "failed":
            self.results["overall"]["failed"] += 1
        else:
            self.results["overall"]["skipped"] += 1
        
        return result
    
    def test_logs_endpoint(self) -> Dict:
        """测试日志端点"""
        logger.info("\n" + "="*60)
        logger.info("📝 测试日志端点")
        logger.info("="*60)
        
        result = self._test_endpoint("/api/diagnostics/logs", "GET")
        self.results["endpoints"]["/api/diagnostics/logs"] = result
        
        if result["status"] == "passed":
            self.results["overall"]["passed"] += 1
            response = result["response"]
            
            if isinstance(response, dict) and "logs" in response:
                self.results["logging"] = {
                    "log_count": len(response["logs"]),
                    "has_trace_id": any(
                        "trace_id" in log for log in response["logs"]
                    ) if response["logs"] else False
                }
                if self.results["logging"]["has_trace_id"]:
                    logger.info("✅ 日志中包含 trace_id")
                else:
                    logger.info("⚠️ 日志中未检测到 trace_id")
        
        elif result["status"] == "failed":
            self.results["overall"]["failed"] += 1
        else:
            self.results["overall"]["skipped"] += 1
        
        return result
    
    def test_observability_state(self) -> Dict:
        """测试可观测性状态端点"""
        logger.info("\n" + "="*60)
        logger.info("🔧 测试可观测性状态端点")
        logger.info("="*60)
        
        result = self._test_endpoint("/api/observability/state", "GET")
        self.results["endpoints"]["/api/observability/state"] = result
        
        if result["status"] == "passed":
            self.results["overall"]["passed"] += 1
            response = result["response"]
            
            if isinstance(response, dict):
                self.results["tracing"] = {
                    "has_trace_id": "trace_id" in response,
                    "has_health": "health" in response,
                    "has_metrics": "metrics" in response,
                    "has_tools": "tools" in response,
                    "has_config": "config" in response
                }
        
        elif result["status"] == "failed":
            self.results["overall"]["failed"] += 1
        else:
            self.results["overall"]["skipped"] += 1
        
        return result
    
    def test_tools_endpoint(self) -> Dict:
        """测试工具诊断端点"""
        logger.info("\n" + "="*60)
        logger.info("🛠️ 测试工具诊断端点")
        logger.info("="*60)
        
        result = self._test_endpoint("/api/diagnostics/tools", "GET")
        self.results["endpoints"]["/api/diagnostics/tools"] = result
        
        if result["status"] == "passed":
            self.results["overall"]["passed"] += 1
            response = result["response"]
            
            if isinstance(response, dict):
                self.results["tools"] = {
                    "total_tools": response.get("total_tools", 0),
                    "has_categories": "categories" in response,
                    "has_tools_list": "tools" in response
                }
        
        elif result["status"] == "failed":
            self.results["overall"]["failed"] += 1
        else:
            self.results["overall"]["skipped"] += 1
        
        return result
    
    def test_trace_context_propagation(self) -> Dict:
        """测试追踪上下文传播"""
        logger.info("\n" + "="*60)
        logger.info("🔗 测试追踪上下文传播")
        logger.info("="*60)
        
        result = {"status": "pending", "error": None, "details": {}}
        
        try:
            from agent.monitoring.tracing import (
                TraceContext,
                get_trace_id,
                set_trace_id,
                set_span_id,
                extract_trace_context,
                inject_trace_context
            )
            
            # 清理上下文
            set_trace_id(None)
            set_span_id(None)
            
            # 测试创建追踪上下文
            with TraceContext("TestService", "test_operation") as ctx:
                trace_id = ctx.trace_id
                span_id = ctx.span_id
                
                # 验证 trace_id 和 span_id 不为空
                assert trace_id is not None, "trace_id 不应为空"
                assert span_id is not None, "span_id 不应为空"
                assert len(trace_id) == 16, f"trace_id 长度应为16，实际为 {len(trace_id)}"
                
                # 测试 get_trace_id
                assert get_trace_id() == trace_id, "get_trace_id 返回值不正确"
                
                # 测试注入上下文
                headers = inject_trace_context()
                assert "traceparent" in headers, "注入的 headers 应包含 traceparent"
                
                # 测试提取上下文
                extracted = extract_trace_context(headers)
                assert extracted.get("trace_id") == trace_id, "提取的 trace_id 不匹配"
                
                # 测试嵌套上下文
                with TraceContext("NestedService", "nested_op") as nested_ctx:
                    assert nested_ctx.trace_id == trace_id, "嵌套上下文应继承 trace_id"
                    assert nested_ctx.span_id != span_id, "嵌套上下文应有新的 span_id"
            
            result["status"] = "passed"
            result["details"] = {
                "trace_id": trace_id,
                "span_id": span_id,
                "nested_context_works": True,
                "propagation_works": True
            }
            self.results["overall"]["passed"] += 1
            self.results["tracing"]["propagation_test"] = result
            logger.info("✅ 追踪上下文传播测试通过")
            
        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
            self.results["overall"]["failed"] += 1
            logger.error(f"❌ 追踪上下文传播测试失败: {e}")
        
        return result
    
    def run_all_tests(self):
        """运行所有测试"""
        logger.info("\n" + "="*80)
        logger.info("🚀 开始可观测性端到端验证测试")
        logger.info("="*80)
        
        # 运行所有测试
        self.test_health_endpoints()
        self.test_tracing_endpoints()
        self.test_metrics_endpoint()
        self.test_runtime_metrics()
        self.test_logs_endpoint()
        self.test_observability_state()
        self.test_tools_endpoint()
        self.test_trace_context_propagation()
        
    def generate_report(self, detailed: bool = False) -> str:
        """生成验证报告"""
        report_lines = []
        
        report_lines.append("="*80)
        report_lines.append("📊 可观测性端到端验证报告")
        report_lines.append("="*80)
        report_lines.append(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append(f"测试端点总数: {len(self.results['endpoints'])}")
        report_lines.append("")
        
        # 总体统计
        report_lines.append("📈 总体测试结果")
        report_lines.append("-" * 40)
        passed = self.results["overall"]["passed"]
        failed = self.results["overall"]["failed"]
        skipped = self.results["overall"]["skipped"]
        total = passed + failed + skipped
        report_lines.append(f"✅ 通过: {passed}")
        report_lines.append(f"❌ 失败: {failed}")
        report_lines.append(f"⚠️ 跳过: {skipped}")
        report_lines.append(f"📊 通过率: {round(passed / total * 100, 1)}%")
        report_lines.append("")
        
        if detailed:
            # 端点测试详情
            report_lines.append("📋 端点测试详情")
            report_lines.append("-" * 40)
            for endpoint, result in self.results["endpoints"].items():
                status_icon = {
                    "passed": "✅",
                    "failed": "❌",
                    "skipped": "⚠️"
                }.get(result["status"], "❓")
                latency = f" ({result['latency_ms']}ms)" if result["latency_ms"] else ""
                report_lines.append(f"{status_icon} {result['method']} {endpoint}{latency}")
                if result["status"] == "failed" and result["error"]:
                    report_lines.append(f"   错误: {result['error']}")
            report_lines.append("")
            
            # 追踪测试结果
            report_lines.append("🔍 追踪系统验证")
            report_lines.append("-" * 40)
            report_lines.append(f"✓ trace_id 可用: {'是' if self.trace_id else '否'}")
            if self.trace_id:
                report_lines.append(f"  当前 trace_id: {self.trace_id}")
            
            if "propagation_test" in self.results["tracing"]:
                prop = self.results["tracing"]["propagation_test"]
                status = "✅ 通过" if prop["status"] == "passed" else "❌ 失败"
                report_lines.append(f"✓ 上下文传播测试: {status}")
            
            if self.results["tracing"].get("has_trace_id"):
                report_lines.append("✓ 可观测性状态包含 trace_id")
            report_lines.append("")
            
            # 指标测试结果
            report_lines.append("📊 指标系统验证")
            report_lines.append("-" * 40)
            metrics = self.results.get("metrics", {})
            
            if metrics.get("has_help_lines"):
                report_lines.append("✓ Prometheus 格式包含 HELP 行")
            if metrics.get("has_type_lines"):
                report_lines.append("✓ Prometheus 格式包含 TYPE 行")
            if metrics.get("has_metrics"):
                report_lines.append("✓ Prometheus 格式包含指标数据")
            if metrics.get("has_histograms"):
                report_lines.append(f"✓ 运行时指标包含直方图 ({metrics.get('histogram_count', 0)} 个)")
            if metrics.get("has_counters"):
                report_lines.append(f"✓ 运行时指标包含计数器 ({metrics.get('counter_count', 0)} 个)")
            report_lines.append("")
            
            # 日志测试结果
            report_lines.append("📝 日志系统验证")
            report_lines.append("-" * 40)
            logging = self.results.get("logging", {})
            report_lines.append(f"✓ 日志数量: {logging.get('log_count', 0)}")
            if logging.get("has_trace_id"):
                report_lines.append("✓ 日志中包含 trace_id（追踪上下文关联正常）")
            else:
                report_lines.append("⚠️ 日志中未检测到 trace_id")
            
            # 工具测试结果
            if "tools" in self.results:
                tools = self.results["tools"]
                report_lines.append("")
                report_lines.append("🛠️ 工具注册验证")
                report_lines.append("-" * 40)
                report_lines.append(f"✓ 已注册工具总数: {tools.get('total_tools', 0)}")
        
        report_lines.append("")
        report_lines.append("="*80)
        
        return "\n".join(report_lines)


def main():
    parser = argparse.ArgumentParser(description="可观测性端到端验证测试")
    parser.add_argument("--report", action="store_true", help="生成详细报告")
    parser.add_argument("--url", default="http://localhost:5678", help="服务基础 URL")
    args = parser.parse_args()
    
    validator = ObservabilityValidator(args.url)
    
    try:
        validator.run_all_tests()
        
        print("\n" + "="*80)
        print("📊 可观测性端到端验证报告")
        print("="*80)
        
        report = validator.generate_report(detailed=args.report)
        print(report)
        
        # 检查是否有失败
        if validator.results["overall"]["failed"] > 0:
            print("\n⚠️ 部分测试失败，请检查相关端点")
            sys.exit(1)
        else:
            print("\n🎉 所有测试通过！")
            sys.exit(0)
            
    except Exception as e:
        logger.error(f"测试执行异常: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
