"""生态化扩展模块性能基准测试"""

import time
import json
import threading
from typing import Dict, Any, List, Callable
from datetime import datetime

import pytest

from agent.extensions.manager import ExtensionManager
from agent.extensions.sandbox import SandboxManager, PluginSandbox, SandboxPermission, ResourceLimits
from agent.api_gateway import ApiGateway, ApiKeyManager, QuotaManager
from agent.multi_tenant import TenantManager, BillingManager
from agent.model_router.router import ModelRouter, ModelSelector
from agent.model_router.adapters import ModelAdapterFactory


class PerformanceTestResult:
    """性能测试结果"""
    
    def __init__(self, name: str):
        self.name = name
        self.latencies: List[float] = []
        self.throughput = 0.0
        self.avg_latency = 0.0
        self.p95_latency = 0.0
        self.p99_latency = 0.0
    
    def record_latency(self, latency_ms: float):
        """记录延迟"""
        self.latencies.append(latency_ms)
    
    def calculate_stats(self, duration_ms: float):
        """计算统计信息"""
        if not self.latencies:
            return
        
        self.latencies.sort()
        n = len(self.latencies)
        
        self.avg_latency = sum(self.latencies) / n
        self.p95_latency = self.latencies[int(n * 0.95)] if n > 0 else 0
        self.p99_latency = self.latencies[int(n * 0.99)] if n > 0 else 0
        self.throughput = n / (duration_ms / 1000) if duration_ms > 0 else 0


class TestExtensionPerformance:
    """扩展模块性能测试"""
    
    @pytest.mark.performance
    def test_extension_manager_install_uninstall(self):
        """测试扩展安装卸载性能"""
        manager = ExtensionManager()
        result = PerformanceTestResult("Extension Install/Uninstall")
        
        iterations = 100
        start_time = time.perf_counter()
        
        for _ in range(iterations):
            op_start = time.perf_counter()
            manager.install("skill", "memory_summary")
            manager.uninstall("skill", "memory_summary")
            latency_ms = (time.perf_counter() - op_start) * 1000
            result.record_latency(latency_ms)
        
        total_ms = (time.perf_counter() - start_time) * 1000
        result.calculate_stats(total_ms)
        
        print(f"\n[*] Extension Install/Uninstall Performance Test:")
        print(f"   Iterations: {iterations}")
        print(f"   Avg Latency: {result.avg_latency:.4f} ms")
        print(f"   P95 Latency: {result.p95_latency:.4f} ms")
        print(f"   P99 Latency: {result.p99_latency:.4f} ms")
        print(f"   Throughput: {result.throughput:.2f} ops/s")
        
        assert result.avg_latency < 50, f"扩展安装卸载平均延迟过高: {result.avg_latency}ms"
    
    @pytest.mark.performance
    def test_extension_manager_list(self):
        """测试扩展列表查询性能"""
        manager = ExtensionManager()
        result = PerformanceTestResult("Extension List Query")
        
        iterations = 1000
        start_time = time.perf_counter()
        
        for _ in range(iterations):
            op_start = time.perf_counter()
            manager.list_all("skill")
            latency_ms = (time.perf_counter() - op_start) * 1000
            result.record_latency(latency_ms)
        
        total_ms = (time.perf_counter() - start_time) * 1000
        result.calculate_stats(total_ms)
        
        print(f"\n📊 扩展列表查询性能测试:")
        print(f"   迭代次数: {iterations}")
        print(f"   平均延迟: {result.avg_latency:.4f} ms")
        print(f"   P95延迟: {result.p95_latency:.4f} ms")
        print(f"   P99延迟: {result.p99_latency:.4f} ms")
        print(f"   吞吐量: {result.throughput:.2f} ops/s")
        
        assert result.avg_latency < 10, f"扩展列表查询平均延迟过高: {result.avg_latency}ms"
    
    @pytest.mark.performance
    def test_sandbox_creation(self):
        """测试沙箱创建性能"""
        sandbox_manager = SandboxManager()
        result = PerformanceTestResult("Sandbox Creation")
        
        iterations = 50
        start_time = time.perf_counter()
        
        for i in range(iterations):
            op_start = time.perf_counter()
            sandbox = sandbox_manager.get_sandbox(f"test_plugin_{i}")
            sandbox.create_sandbox(
                f"test_plugin_{i}",
                [SandboxPermission.READ_FILES.value],
                ResourceLimits(max_memory_mb=256)
            )
            latency_ms = (time.perf_counter() - op_start) * 1000
            result.record_latency(latency_ms)
        
        total_ms = (time.perf_counter() - start_time) * 1000
        result.calculate_stats(total_ms)
        
        print(f"\n[*] Sandbox Creation Performance Test:")
        print(f"   Iterations: {iterations}")
        print(f"   Avg Latency: {result.avg_latency:.4f} ms")
        print(f"   P95 Latency: {result.p95_latency:.4f} ms")
        print(f"   P99 Latency: {result.p99_latency:.4f} ms")
        print(f"   Throughput: {result.throughput:.2f} ops/s")
        
        # 清理
        sandbox_manager.destroy_all()
        
        assert result.avg_latency < 100, f"沙箱创建平均延迟过高: {result.avg_latency}ms"


class TestApiGatewayPerformance:
    """API网关性能测试"""
    
    @pytest.mark.performance
    def test_api_key_validation(self):
        """测试API Key验证性能"""
        key_manager = ApiKeyManager()
        result = PerformanceTestResult("API Key Validation")
        
        # 创建测试key
        key_info = key_manager.create_key("test_user", "test_key")
        test_key = key_info["key"]
        
        iterations = 1000
        start_time = time.perf_counter()
        
        for _ in range(iterations):
            op_start = time.perf_counter()
            key_manager.validate_key(test_key)
            latency_ms = (time.perf_counter() - op_start) * 1000
            result.record_latency(latency_ms)
        
        total_ms = (time.perf_counter() - start_time) * 1000
        result.calculate_stats(total_ms)
        
        print(f"\n[*] API Key Validation Performance Test:")
        print(f"   Iterations: {iterations}")
        print(f"   Avg Latency: {result.avg_latency:.4f} ms")
        print(f"   P95 Latency: {result.p95_latency:.4f} ms")
        print(f"   P99 Latency: {result.p99_latency:.4f} ms")
        print(f"   Throughput: {result.throughput:.2f} ops/s")
        
        assert result.avg_latency < 1, f"API Key验证平均延迟过高: {result.avg_latency}ms"
    
    @pytest.mark.performance
    def test_quota_check(self):
        """测试配额检查性能"""
        quota_manager = QuotaManager()
        quota_manager.set_quota("test_user", "api_calls", 10000)
        result = PerformanceTestResult("Quota Check")
        
        iterations = 10000
        start_time = time.perf_counter()
        
        for _ in range(iterations):
            op_start = time.perf_counter()
            quota_manager.check_quota("test_user", "api_calls")
            latency_ms = (time.perf_counter() - op_start) * 1000
            result.record_latency(latency_ms)
        
        total_ms = (time.perf_counter() - start_time) * 1000
        result.calculate_stats(total_ms)
        
        print(f"\n[*] Quota Check Performance Test:")
        print(f"   Iterations: {iterations}")
        print(f"   Avg Latency: {result.avg_latency:.4f} ms")
        print(f"   P95 Latency: {result.p95_latency:.4f} ms")
        print(f"   P99 Latency: {result.p99_latency:.4f} ms")
        print(f"   Throughput: {result.throughput:.2f} ops/s")
        
        assert result.avg_latency < 0.5, f"配额检查平均延迟过高: {result.avg_latency}ms"
    
    @pytest.mark.performance
    def test_gateway_request_handling(self):
        """测试网关请求处理性能"""
        gateway = ApiGateway()
        
        def handler(request):
            return {"success": True, "status_code": 200}
        
        gateway.register_endpoint("/test", "GET", handler, auth_required=False)
        
        class MockRequest:
            def __init__(self):
                self.path = "/test"
                self.method = "GET"
                self.headers = {}
        
        result = PerformanceTestResult("Gateway Request Handling")
        
        iterations = 1000
        start_time = time.perf_counter()
        
        for _ in range(iterations):
            op_start = time.perf_counter()
            gateway.handle_request(MockRequest())
            latency_ms = (time.perf_counter() - op_start) * 1000
            result.record_latency(latency_ms)
        
        total_ms = (time.perf_counter() - start_time) * 1000
        result.calculate_stats(total_ms)
        
        print(f"\n📊 网关请求处理性能测试:")
        print(f"   迭代次数: {iterations}")
        print(f"   平均延迟: {result.avg_latency:.4f} ms")
        print(f"   P95延迟: {result.p95_latency:.4f} ms")
        print(f"   P99延迟: {result.p99_latency:.4f} ms")
        print(f"   吞吐量: {result.throughput:.2f} ops/s")
        
        assert result.avg_latency < 5, f"网关请求处理平均延迟过高: {result.avg_latency}ms"


class TestMultiTenantPerformance:
    """多租户模块性能测试"""
    
    @pytest.mark.performance
    def test_user_creation(self):
        """测试用户创建性能"""
        manager = TenantManager()
        result = PerformanceTestResult("User Creation")
        
        iterations = 100
        start_time = time.perf_counter()
        
        for i in range(iterations):
            op_start = time.perf_counter()
            manager.create_user(f"test{i}@example.com", f"Test User {i}")
            latency_ms = (time.perf_counter() - op_start) * 1000
            result.record_latency(latency_ms)
        
        total_ms = (time.perf_counter() - start_time) * 1000
        result.calculate_stats(total_ms)
        
        print(f"\n[*] User Creation Performance Test:")
        print(f"   Iterations: {iterations}")
        print(f"   Avg Latency: {result.avg_latency:.4f} ms")
        print(f"   P95 Latency: {result.p95_latency:.4f} ms")
        print(f"   P99 Latency: {result.p99_latency:.4f} ms")
        print(f"   Throughput: {result.throughput:.2f} ops/s")
        
        assert result.avg_latency < 20, f"用户创建平均延迟过高: {result.avg_latency}ms"
    
    @pytest.mark.performance
    def test_permission_check(self):
        """测试权限检查性能"""
        manager = TenantManager()
        user = manager.create_user("test@example.com", "Test User")
        org = manager.create_organization("Test Org", user.id)
        manager.assign_role(user.id, org.id, "admin")
        
        result = PerformanceTestResult("Permission Check")
        
        iterations = 10000
        start_time = time.perf_counter()
        
        for _ in range(iterations):
            op_start = time.perf_counter()
            manager.has_permission(user.id, org.id, "read")
            latency_ms = (time.perf_counter() - op_start) * 1000
            result.record_latency(latency_ms)
        
        total_ms = (time.perf_counter() - start_time) * 1000
        result.calculate_stats(total_ms)
        
        print(f"\n📊 权限检查性能测试:")
        print(f"   迭代次数: {iterations}")
        print(f"   平均延迟: {result.avg_latency:.4f} ms")
        print(f"   P95延迟: {result.p95_latency:.4f} ms")
        print(f"   P99延迟: {result.p99_latency:.4f} ms")
        print(f"   吞吐量: {result.throughput:.2f} ops/s")
        
        assert result.avg_latency < 0.5, f"权限检查平均延迟过高: {result.avg_latency}ms"
    
    @pytest.mark.performance
    def test_billing_record(self):
        """测试计费记录性能"""
        billing = BillingManager()
        result = PerformanceTestResult("Billing Record")
        
        iterations = 1000
        start_time = time.perf_counter()
        
        for _ in range(iterations):
            op_start = time.perf_counter()
            billing.record_usage("tenant1", "api_calls", 1)
            latency_ms = (time.perf_counter() - op_start) * 1000
            result.record_latency(latency_ms)
        
        total_ms = (time.perf_counter() - start_time) * 1000
        result.calculate_stats(total_ms)
        
        print(f"\n📊 计费记录性能测试:")
        print(f"   迭代次数: {iterations}")
        print(f"   平均延迟: {result.avg_latency:.4f} ms")
        print(f"   P95延迟: {result.p95_latency:.4f} ms")
        print(f"   P99延迟: {result.p99_latency:.4f} ms")
        print(f"   吞吐量: {result.throughput:.2f} ops/s")
        
        assert result.avg_latency < 1, f"计费记录平均延迟过高: {result.avg_latency}ms"


class TestModelRouterPerformance:
    """模型路由器性能测试"""
    
    @pytest.mark.performance
    def test_task_analysis(self):
        """测试任务分析性能"""
        selector = ModelSelector()
        result = PerformanceTestResult("Task Analysis")
        
        test_tasks = [
            "你好", "分析这个问题", "写一首诗", 
            "总结这段文本", "翻译中文到英文", "编写Python代码"
        ]
        
        iterations = 1000
        start_time = time.perf_counter()
        
        for i in range(iterations):
            op_start = time.perf_counter()
            selector.analyze_task(test_tasks[i % len(test_tasks)])
            latency_ms = (time.perf_counter() - op_start) * 1000
            result.record_latency(latency_ms)
        
        total_ms = (time.perf_counter() - start_time) * 1000
        result.calculate_stats(total_ms)
        
        print(f"\n[*] Task Analysis Performance Test:")
        print(f"   Iterations: {iterations}")
        print(f"   Avg Latency: {result.avg_latency:.4f} ms")
        print(f"   P95 Latency: {result.p95_latency:.4f} ms")
        print(f"   P99 Latency: {result.p99_latency:.4f} ms")
        print(f"   Throughput: {result.throughput:.2f} ops/s")
        
        assert result.avg_latency < 5, f"任务分析平均延迟过高: {result.avg_latency}ms"
    
    @pytest.mark.performance
    def test_model_selection(self):
        """测试模型选择性能"""
        selector = ModelSelector()
        result = PerformanceTestResult("Model Selection")
        
        iterations = 1000
        start_time = time.perf_counter()
        
        for _ in range(iterations):
            op_start = time.perf_counter()
            selector.select_model("normal")
            latency_ms = (time.perf_counter() - op_start) * 1000
            result.record_latency(latency_ms)
        
        total_ms = (time.perf_counter() - start_time) * 1000
        result.calculate_stats(total_ms)
        
        print(f"\n[*] Model Selection Performance Test:")
        print(f"   Iterations: {iterations}")
        print(f"   Avg Latency: {result.avg_latency:.4f} ms")
        print(f"   P95 Latency: {result.p95_latency:.4f} ms")
        print(f"   P99 Latency: {result.p99_latency:.4f} ms")
        print(f"   Throughput: {result.throughput:.2f} ops/s")
        
        assert result.avg_latency < 10, f"模型选择平均延迟过高: {result.avg_latency}ms"
    
    @pytest.mark.performance
    def test_adapter_creation(self):
        """测试适配器创建性能"""
        result = PerformanceTestResult("Adapter Creation")
        
        iterations = 100
        start_time = time.perf_counter()
        
        for _ in range(iterations):
            op_start = time.perf_counter()
            ModelAdapterFactory.create("openai", "gpt-3.5-turbo")
            latency_ms = (time.perf_counter() - op_start) * 1000
            result.record_latency(latency_ms)
        
        total_ms = (time.perf_counter() - start_time) * 1000
        result.calculate_stats(total_ms)
        
        print(f"\n[*] Adapter Creation Performance Test:")
        print(f"   Iterations: {iterations}")
        print(f"   Avg Latency: {result.avg_latency:.4f} ms")
        print(f"   P95 Latency: {result.p95_latency:.4f} ms")
        print(f"   P99 Latency: {result.p99_latency:.4f} ms")
        print(f"   Throughput: {result.throughput:.2f} ops/s")
        
        assert result.avg_latency < 50, f"适配器创建平均延迟过高: {result.avg_latency}ms"


if __name__ == "__main__":
    print("=" * 70)
    print("Starting Ecosystem Performance Benchmark Tests")
    print("=" * 70)
    
    # 运行扩展模块测试
    ext_test = TestExtensionPerformance()
    ext_test.test_extension_manager_install_uninstall()
    ext_test.test_extension_manager_list()
    ext_test.test_sandbox_creation()
    
    # 运行API网关测试
    api_test = TestApiGatewayPerformance()
    api_test.test_api_key_validation()
    api_test.test_quota_check()
    api_test.test_gateway_request_handling()
    
    # 运行多租户测试
    tenant_test = TestMultiTenantPerformance()
    tenant_test.test_user_creation()
    tenant_test.test_permission_check()
    tenant_test.test_billing_record()
    
    # 运行模型路由器测试
    model_test = TestModelRouterPerformance()
    model_test.test_task_analysis()
    model_test.test_model_selection()
    model_test.test_adapter_creation()
    
    print("\n" + "=" * 70)
    print("[OK] All performance benchmark tests completed successfully")
    print("=" * 70)