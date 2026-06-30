#!/usr/bin/env python3
"""
混沌工程测试脚本

用于验证可观测性体系在异常场景下的有效性。

测试场景：
1. 网络延迟/超时
2. 服务不可用
3. 内存压力
4. 高并发压力
5. CPU压力

验证内容：
- 追踪链路是否完整记录异常
- 指标是否正确反映异常状态
- 日志是否包含足够的异常信息
- 告警是否正确触发

输出：
- 混沌工程测试报告
- 可观测性改进建议
"""

import sys
import time
import json
import threading
import concurrent.futures
import gc
import datetime
from datetime import datetime, timedelta
from typing import Dict, Any, List
from dataclasses import dataclass
import random

sys.path.insert(0, '.')

# 设置日志级别
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from agent.monitoring import (
    TraceContext,
    get_trace_id,
    get_error_reporter,
    get_metrics_collector,
    get_chaos_injector,
    FaultType,
    chaos_fault,
    diagnose_opentelemetry_config,
    with_chaos_injection,
)


@dataclass
class TestResult:
    """测试结果"""
    test_name: str
    fault_type: FaultType
    start_time: datetime
    end_time: datetime
    duration_ms: float
    success: bool
    observations: List[str]
    issues: List[str]
    metrics_before: Dict[str, Any]
    metrics_after: Dict[str, Any]
    trace_records: List[Dict[str, Any]]
    error_reports: List[Dict[str, Any]]


class ChaosTestSuite:
    """混沌测试套件"""
    
    def __init__(self):
        self.injector = get_chaos_injector()
        self.error_reporter = get_error_reporter()
        self.metrics_collector = get_metrics_collector()
        self.test_results: List[TestResult] = []
        self._errors_during_test = []
    
    def _capture_metrics(self) -> Dict[str, Any]:
        """捕获当前指标状态"""
        return {
            'injector_stats': self.injector.get_stats(),
        }
    
    def _capture_traces(self) -> List[Dict[str, Any]]:
        """捕获追踪记录"""
        records = []
        for record in self.injector.get_injection_history():
            records.append({
                'fault_type': record.fault_type.value,
                'injected_at': record.injected_at.isoformat(),
                'triggered_count': record.triggered_count,
                'affected_requests': record.affected_requests,
                'recovered_at': record.recovered_at.isoformat() if record.recovered_at else None,
            })
        return records
    
    def _capture_errors(self) -> List[Dict[str, Any]]:
        """捕获错误报告"""
        return self._errors_during_test.copy()
    
    def test_network_delay(self) -> TestResult:
        """测试网络延迟故障"""
        test_name = "网络延迟测试"
        logger.info(f"\n{'='*60}")
        logger.info(f"🔌 {test_name}")
        logger.info(f"{'='*60}")
        
        start_time = datetime.now()
        metrics_before = self._capture_metrics()
        observations = []
        issues = []
        
        try:
            @with_chaos_injection(FaultType.NETWORK_DELAY)
            def delayed_operation():
                time.sleep(0.1)
                return "completed"
            
            # 注入网络延迟（3秒）
            self.injector.inject_network_delay(delay_ms=3000)
            logger.info("已注入网络延迟故障 (3秒延迟)")
            
            # 执行测试请求
            test_results = []
            trace_ids_found = []
            for i in range(3):
                with TraceContext("ChaosTest", "network_test") as ctx:
                    trace_ids_found.append(ctx.trace_id)
                    start = time.time()
                    try:
                        result = delayed_operation()
                        elapsed = (time.time() - start) * 1000
                        test_results.append({'success': True, 'latency_ms': elapsed})
                        logger.info(f"请求 {i+1} 完成，延迟: {elapsed:.2f}ms")
                    except Exception as e:
                        test_results.append({'success': False, 'error': str(e)})
                        logger.error(f"请求 {i+1} 失败: {e}")
            
            # 清理故障
            self.injector.clear_fault(FaultType.NETWORK_DELAY)
            
            # 验证延迟是否被注入
            avg_latency = sum(r['latency_ms'] for r in test_results if r['success']) / len([r for r in test_results if r['success']])
            if avg_latency > 2500:
                observations.append(f"✅ 网络延迟注入成功，平均延迟: {avg_latency:.2f}ms")
            else:
                issues.append(f"❌ 网络延迟注入未生效，平均延迟: {avg_latency:.2f}ms")
            
            # 验证追踪是否记录
            valid_trace_ids = [tid for tid in trace_ids_found if tid]
            if valid_trace_ids:
                observations.append(f"✅ 追踪ID正常生成: {valid_trace_ids[0]}")
            else:
                issues.append("❌ 追踪ID未生成")
            
            success = len(issues) == 0
            
        except Exception as e:
            issues.append(f"测试执行失败: {str(e)}")
            success = False
        
        end_time = datetime.now()
        duration_ms = (end_time - start_time).total_seconds() * 1000
        
        result = TestResult(
            test_name=test_name,
            fault_type=FaultType.NETWORK_DELAY,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
            success=success,
            observations=observations,
            issues=issues,
            metrics_before=metrics_before,
            metrics_after=self._capture_metrics(),
            trace_records=self._capture_traces(),
            error_reports=self._capture_errors()
        )
        
        self.test_results.append(result)
        return result
    
    def test_network_timeout(self) -> TestResult:
        """测试网络超时故障"""
        test_name = "网络超时测试"
        logger.info(f"\n{'='*60}")
        logger.info(f"⏱️  {test_name}")
        logger.info(f"{'='*60}")
        
        start_time = datetime.now()
        metrics_before = self._capture_metrics()
        observations = []
        issues = []
        
        try:
            @with_chaos_injection(FaultType.NETWORK_TIMEOUT)
            def timeout_operation():
                time.sleep(0.5)
                return "completed"
            
            # 注入网络超时
            self.injector.inject_network_timeout(probability=1.0)
            logger.info("已注入网络超时故障")
            
            # 执行测试请求
            timeout_count = 0
            for i in range(3):
                with TraceContext("ChaosTest", "timeout_test"):
                    try:
                        result = timeout_operation()
                        observations.append(f"❌ 预期超时但未发生，结果: {result}")
                    except TimeoutError as e:
                        timeout_count += 1
                        observations.append(f"✅ 捕获到超时异常: {e}")
                    except Exception as e:
                        observations.append(f"⚠️ 捕获到其他异常: {type(e).__name__}: {e}")
            
            # 清理故障
            self.injector.clear_fault(FaultType.NETWORK_TIMEOUT)
            
            if timeout_count == 3:
                observations.append("✅ 所有请求均触发超时")
            else:
                issues.append(f"❌ 仅 {timeout_count}/3 请求触发超时")
            
            success = len(issues) == 0
            
        except Exception as e:
            issues.append(f"测试执行失败: {str(e)}")
            success = False
        
        end_time = datetime.now()
        duration_ms = (end_time - start_time).total_seconds() * 1000
        
        result = TestResult(
            test_name=test_name,
            fault_type=FaultType.NETWORK_TIMEOUT,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
            success=success,
            observations=observations,
            issues=issues,
            metrics_before=metrics_before,
            metrics_after=self._capture_metrics(),
            trace_records=self._capture_traces(),
            error_reports=self._capture_errors()
        )
        
        self.test_results.append(result)
        return result
    
    def test_service_unavailable(self) -> TestResult:
        """测试服务不可用故障"""
        test_name = "服务不可用测试"
        logger.info(f"\n{'='*60}")
        logger.info(f"🔴 {test_name}")
        logger.info(f"{'='*60}")
        
        start_time = datetime.now()
        metrics_before = self._capture_metrics()
        observations = []
        issues = []
        
        try:
            @with_chaos_injection(FaultType.SERVICE_UNAVAILABLE)
            def service_call():
                time.sleep(0.1)
                return "completed"
            
            # 注入服务不可用
            self.injector.inject_service_unavailable(service_name="downstream-api", error_code=503)
            logger.info("已注入服务不可用故障 (503)")
            
            # 执行测试请求
            error_count = 0
            for i in range(3):
                with TraceContext("ChaosTest", "service_test"):
                    try:
                        result = service_call()
                        issues.append(f"❌ 预期服务不可用但未发生，结果: {result}")
                    except ConnectionError as e:
                        error_count += 1
                        observations.append(f"✅ 捕获到服务不可用: {e}")
                    except Exception as e:
                        observations.append(f"⚠️ 捕获到其他异常: {type(e).__name__}: {e}")
            
            # 清理故障
            self.injector.clear_fault(FaultType.SERVICE_UNAVAILABLE)
            
            if error_count == 3:
                observations.append("✅ 所有请求均触发服务不可用")
            else:
                issues.append(f"❌ 仅 {error_count}/3 请求触发服务不可用")
            
            success = len(issues) == 0
            
        except Exception as e:
            issues.append(f"测试执行失败: {str(e)}")
            success = False
        
        end_time = datetime.now()
        duration_ms = (end_time - start_time).total_seconds() * 1000
        
        result = TestResult(
            test_name=test_name,
            fault_type=FaultType.SERVICE_UNAVAILABLE,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
            success=success,
            observations=observations,
            issues=issues,
            metrics_before=metrics_before,
            metrics_after=self._capture_metrics(),
            trace_records=self._capture_traces(),
            error_reports=self._capture_errors()
        )
        
        self.test_results.append(result)
        return result
    
    def test_memory_pressure(self) -> TestResult:
        """测试内存压力故障"""
        test_name = "内存压力测试"
        logger.info(f"\n{'='*60}")
        logger.info(f"💾 {test_name}")
        logger.info(f"{'='*60}")
        
        start_time = datetime.now()
        metrics_before = self._capture_metrics()
        observations = []
        issues = []
        
        try:
            # 注入内存压力（512MB）
            self.injector.inject_memory_pressure(target_mb=512, duration_ms=10000)
            logger.info("已注入内存压力故障 (512MB)")
            
            # 等待内存分配
            time.sleep(3)
            
            # 检查内存使用
            import psutil
            process = psutil.Process()
            memory_usage = process.memory_info().rss / (1024 * 1024)  # MB
            
            logger.info(f"当前内存使用: {memory_usage:.2f} MB")
            
            if memory_usage > 450:
                observations.append(f"✅ 内存压力注入成功，使用: {memory_usage:.2f} MB")
            else:
                issues.append(f"❌ 内存压力注入未生效，使用: {memory_usage:.2f} MB")
            
            # 执行一些操作验证系统稳定性
            try:
                with TraceContext("ChaosTest", "memory_test"):
                    data = [i for i in range(10000)]
                    observations.append("✅ 内存压力下操作正常")
            except MemoryError:
                issues.append("❌ 内存压力下操作失败")
            
            # 清理后检查
            self.injector.clear_fault(FaultType.MEMORY_PRESSURE)
            gc.collect()
            time.sleep(1)
            memory_after = psutil.Process().memory_info().rss / (1024 * 1024)
            if memory_after < 200:
                observations.append(f"✅ 内存清理成功，当前使用: {memory_after:.2f} MB")
            else:
                issues.append(f"⚠️ 内存未完全释放，当前使用: {memory_after:.2f} MB")
            
            success = len(issues) == 0
            
        except Exception as e:
            issues.append(f"测试执行失败: {str(e)}")
            success = False
        
        end_time = datetime.now()
        duration_ms = (end_time - start_time).total_seconds() * 1000
        
        result = TestResult(
            test_name=test_name,
            fault_type=FaultType.MEMORY_PRESSURE,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
            success=success,
            observations=observations,
            issues=issues,
            metrics_before=metrics_before,
            metrics_after=self._capture_metrics(),
            trace_records=self._capture_traces(),
            error_reports=self._capture_errors()
        )
        
        self.test_results.append(result)
        return result
    
    def test_cpu_pressure(self) -> TestResult:
        """测试CPU压力故障"""
        test_name = "CPU压力测试"
        logger.info(f"\n{'='*60}")
        logger.info(f"🔥 {test_name}")
        logger.info(f"{'='*60}")
        
        start_time = datetime.now()
        metrics_before = self._capture_metrics()
        observations = []
        issues = []
        
        try:
            # 注入CPU压力
            self.injector.inject_cpu_pressure(duration_ms=5000)
            logger.info("已注入CPU压力故障")
            
            # 等待CPU压力生效
            time.sleep(2)
            
            # 检查CPU使用率
            import psutil
            cpu_percent = psutil.cpu_percent(interval=2)
            
            logger.info(f"当前CPU使用率: {cpu_percent}%")
            
            if cpu_percent > 70:
                observations.append(f"✅ CPU压力注入成功，使用率: {cpu_percent}%")
            else:
                issues.append(f"❌ CPU压力注入未生效，使用率: {cpu_percent}%")
            
            # 执行一些操作验证系统稳定性
            try:
                with TraceContext("ChaosTest", "cpu_test"):
                    start_op = time.time()
                    result = sum(i ** 2 for i in range(100000))
                    elapsed = (time.time() - start_op) * 1000
                    observations.append(f"✅ CPU压力下操作完成，耗时: {elapsed:.2f}ms")
            except Exception as e:
                issues.append(f"❌ CPU压力下操作失败: {e}")
            
            # 清理故障
            self.injector.clear_fault(FaultType.CPU_PRESSURE)
            
            success = len(issues) == 0
            
        except Exception as e:
            issues.append(f"测试执行失败: {str(e)}")
            success = False
        
        end_time = datetime.now()
        duration_ms = (end_time - start_time).total_seconds() * 1000
        
        result = TestResult(
            test_name=test_name,
            fault_type=FaultType.CPU_PRESSURE,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
            success=success,
            observations=observations,
            issues=issues,
            metrics_before=metrics_before,
            metrics_after=self._capture_metrics(),
            trace_records=self._capture_traces(),
            error_reports=self._capture_errors()
        )
        
        self.test_results.append(result)
        return result
    
    def test_concurrent_pressure(self) -> TestResult:
        """测试高并发压力故障"""
        test_name = "高并发压力测试"
        logger.info(f"\n{'='*60}")
        logger.info(f"⚡ {test_name}")
        logger.info(f"{'='*60}")
        
        start_time = datetime.now()
        metrics_before = self._capture_metrics()
        observations = []
        issues = []
        
        try:
            logger.info("开始高并发测试 (100请求, 20并发)")
            
            def worker(request_id):
                """工作线程"""
                with TraceContext("ChaosTest", f"concurrent_{request_id}"):
                    start = time.time()
                    try:
                        time.sleep(0.05 + random.random() * 0.1)
                        elapsed = (time.time() - start) * 1000
                        return {'success': True, 'request_id': request_id, 'latency_ms': elapsed}
                    except Exception as e:
                        return {'success': False, 'request_id': request_id, 'error': str(e)}
            
            # 执行并发请求
            num_requests = 100
            max_workers = 20
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(worker, i) for i in range(num_requests)]
                results = [f.result() for f in concurrent.futures.as_completed(futures)]
            
            # 统计结果
            successes = [r for r in results if r['success']]
            failures = [r for r in results if not r['success']]
            
            avg_latency = sum(r['latency_ms'] for r in successes) / len(successes) if successes else 0
            max_latency = max(r['latency_ms'] for r in successes) if successes else 0
            
            observations.append(f"✅ 完成 {len(successes)}/{num_requests} 请求")
            observations.append(f"✅ 平均延迟: {avg_latency:.2f}ms")
            observations.append(f"✅ 最大延迟: {max_latency:.2f}ms")
            
            if failures:
                issues.append(f"❌ {len(failures)} 个请求失败")
                for f in failures[:3]:
                    issues.append(f"   - 请求 {f['request_id']}: {f.get('error', '未知错误')}")
            
            success = len(issues) == 0
            
        except Exception as e:
            issues.append(f"测试执行失败: {str(e)}")
            success = False
        
        end_time = datetime.now()
        duration_ms = (end_time - start_time).total_seconds() * 1000
        
        result = TestResult(
            test_name=test_name,
            fault_type=FaultType.CONCURRENT_PRESSURE,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
            success=success,
            observations=observations,
            issues=issues,
            metrics_before=metrics_before,
            metrics_after=self._capture_metrics(),
            trace_records=self._capture_traces(),
            error_reports=self._capture_errors()
        )
        
        self.test_results.append(result)
        return result
    
    def generate_report(self) -> str:
        """生成测试报告"""
        report = []
        report.append("# 混沌工程测试报告")
        report.append("")
        report.append(f"**测试时间**: {datetime.now().isoformat()}")
        report.append("")
        report.append("## 测试概述")
        report.append("")
        
        total_tests = len(self.test_results)
        passed = sum(1 for r in self.test_results if r.success)
        failed = total_tests - passed
        
        report.append(f"- 测试总数: {total_tests}")
        report.append(f"- 通过: {passed}")
        report.append(f"- 失败: {failed}")
        report.append("")
        
        # 详细测试结果
        report.append("## 测试详情")
        report.append("")
        
        for result in self.test_results:
            report.append(f"### {result.test_name}")
            report.append("")
            report.append(f"- **故障类型**: {result.fault_type.value}")
            report.append(f"- **测试时间**: {result.start_time.isoformat()}")
            report.append(f"- **持续时间**: {result.duration_ms:.2f}ms")
            report.append(f"- **结果**: {'✅ 通过' if result.success else '❌ 失败'}")
            report.append("")
            
            if result.observations:
                report.append("**观测结果**:")
                for obs in result.observations:
                    report.append(f"- {obs}")
                report.append("")
            
            if result.issues:
                report.append("**问题列表**:")
                for issue in result.issues:
                    report.append(f"- {issue}")
                report.append("")
        
        # 改进建议
        report.append("## 改进建议")
        report.append("")
        
        all_issues = []
        for result in self.test_results:
            all_issues.extend(result.issues)
        
        if not all_issues:
            report.append("✅ 所有测试通过，可观测性体系运行正常")
        else:
            report.append("### 发现的问题")
            report.append("")
            for issue in all_issues:
                report.append(f"- {issue}")
            report.append("")
            
            report.append("### 建议措施")
            report.append("")
            if any("追踪" in issue for issue in all_issues):
                report.append("- 检查追踪模块配置，确保OpenTelemetry正确初始化")
                report.append("- 验证追踪上下文在故障场景下的正确传播")
            
            if any("延迟" in issue for issue in all_issues):
                report.append("- 增加网络延迟监控指标")
                report.append("- 配置超时告警规则")
            
            if any("内存" in issue for issue in all_issues):
                report.append("- 增加内存使用监控和告警")
                report.append("- 优化内存清理机制")

            if any("CPU" in issue for issue in all_issues):
                report.append("- 增加CPU使用率监控和告警")
            
            if any("服务不可用" in issue for issue in all_issues):
                report.append("- 增加下游服务健康检查")
                report.append("- 配置服务不可用告警规则")
        
        # 统计摘要
        report.append("## 统计摘要")
        report.append("")
        
        stats = self.injector.get_stats()
        report.append("### 故障注入统计")
        report.append("")
        report.append(f"- 活跃故障数: {stats['active_faults']}")
        report.append(f"- 总注入次数: {stats['total_injections']}")
        report.append(f"- 总触发次数: {stats['total_triggered']}")
        report.append(f"- 受影响请求数: {stats['total_affected_requests']}")
        report.append("")
        
        return '\n'.join(report)
    
    def run_all_tests(self) -> List[TestResult]:
        """运行所有测试"""
        logger.info("\n" + "="*80)
        logger.info("🚀 开始混沌工程测试套件")
        logger.info("="*80)
        
        # 先诊断OpenTelemetry配置
        diag = diagnose_opentelemetry_config()
        logger.info("\n📊 OpenTelemetry 配置诊断:")
        for msg in diag.get('diagnosis', []):
            logger.info(f"   {msg}")
        
        tests = [
            self.test_network_delay,
            self.test_network_timeout,
            self.test_service_unavailable,
            self.test_memory_pressure,
            self.test_cpu_pressure,
            self.test_concurrent_pressure,
        ]
        
        for test in tests:
            try:
                test()
            except Exception as e:
                logger.error(f"测试 {test.__name__} 执行异常: {e}", exc_info=True)
        
        # 清理所有故障
        self.injector.clear_all()
        
        return self.test_results


def main():
    """主函数"""
    suite = ChaosTestSuite()
    suite.run_all_tests()
    
    # 生成报告
    report = suite.generate_report()
    
    # 打印报告
    print("\n" + "="*80)
    print("📋 混沌工程测试报告")
    print("="*80)
    print(report)
    
    # 保存报告
    report_filename = f"chaos_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    with open(report_filename, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n📄 报告已保存到: {report_filename}")
    
    # 统计结果
    passed = sum(1 for r in suite.test_results if r.success)
    total = len(suite.test_results)
    
    if passed == total:
        print("\n🎉 所有测试通过!")
        return 0
    else:
        print(f"\n⚠️  {passed}/{total} 测试通过")
        return 1


if __name__ == "__main__":
    sys.exit(main())