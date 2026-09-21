"""生态化扩展模块性能基准测试"""

import time
import json
import threading
from pathlib import Path
from typing import Dict, Any, List, Callable
from datetime import datetime

import pytest

from agent.extensions import manager as _ext_manager
from agent.extensions.manager import ExtensionManager
from agent.extensions.store import ExtensionStore
from agent.skills_mgmt.registry import SkillRegistry
from agent.skills_mgmt.service import SkillsMgmtService
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


# ════════════════════════════════════════════════════════════
#  【L14-b 回归】扩展用例的隔离夹具
# ════════════════════════════════════════════════════════════

#: 真实技能仓库中的目标目录 —— **只用于断言"未被触碰"**，用例绝不写它
_REAL_SKILL_DIR = (Path(__file__).resolve().parents[2]
                   / "data" / "skills_repo" / "memory_summary")


def _snapshot(path: Path):
    """目录快照（不存在 ⇒ None）。用于证明用例没碰真实数据。"""
    if not path.exists():
        return None
    return sorted(
        (str(p.relative_to(path)), p.stat().st_size, int(p.stat().st_mtime_ns))
        for p in path.rglob("*"))


@pytest.fixture
def isolated_extensions(tmp_path, monkeypatch):
    """把 ExtensionManager 的**技能面**整体隔离到 tmp_path。

    【为什么必须（L14-b 实测缺陷）】原用例直接构造 ExtensionManager，而
    SkillsInstaller.add_builtin_skill / remove_skill 内部构造的是
    SkillRegistry 再取 _svc()（agent/skills_mgmt/registry.py:43-47）——
    它会新建**默认路径**的 SkillsMgmtService ⇒ repo_path = 真实
    data/skills_repo；当主轨没有该 id 时，remove_skill 走 else 分支
    （agent/extensions/skills_installer.py:190）直接调 file_store.delete()
    → shutil.rmtree（agent/skills_mgmt/file_store.py:702）
    ⇒ **真实技能目录被整棵删除**：data/skills_repo/memory_summary/ 就是这样消失的。
    扩展元数据同样会写进真实 agent/data/extensions.json。

    【隔离手段：最小侵入，不改任何产品代码】
      ① SkillRegistry._svc → 注入 store_path / repo_path 都在 tmp 下的服务；
      ② ExtensionManager 构造用的 ExtensionStore → tmp 下的 JSON 文件。
    用例语义**不变**：仍然真跑 install（内置技能分支）与 uninstall
    （多轨删除 → file_store.delete 分支），只是把落点从生产数据换成 tmp。
    """
    svc = SkillsMgmtService(store_path=str(tmp_path / "skills_mgmt.json"),
                            repo_path=str(tmp_path / "skills_repo"))
    # 预置一个内置技能（等价于真实仓库里的 memory_summary/skill.md），
    # 保证 uninstall 真的走到 file_store.delete 分支而不是"技能不存在"
    svc.file_store.create(
        "memory_summary",
        meta={"id": "memory_summary", "name": "记忆摘要",
              "enabled": True, "status": "approved"},
        instruction="# 记忆摘要")

    monkeypatch.setattr(SkillRegistry, "_svc", lambda self: svc)
    monkeypatch.setattr(
        _ext_manager, "ExtensionStore",
        lambda *a, **k: ExtensionStore(data_file=str(tmp_path / "extensions.json")))

    return svc


class TestExtensionPerformance:
    """扩展模块性能测试"""
    
    @pytest.mark.performance
    def test_extension_manager_install_uninstall(self, isolated_extensions):
        """测试扩展安装卸载性能（**全程在隔离 repo 上**，见 isolated_extensions）

        【L14-b 回归】原实现直接落在生产数据上：install/uninstall 会把
        `data/skills_repo/<id>/` 整棵删掉、并写真实 agent/data/extensions.json。
        现在既隔离又**证明**没碰真实目录（见本用例末尾的 _snapshot 断言）。
        """
        real_before = _snapshot(_REAL_SKILL_DIR)
        manager = ExtensionManager()
        # 隔离落点在 CI 日志里可见（便于复核"没写生产数据"）
        print(f"\n[隔离] 技能 repo = {isolated_extensions.file_store.repo_path}")
        print(f"[隔离] 真实目标  = {_REAL_SKILL_DIR}")
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

        # ── 【L14-b 隔离回归】两道断言，缺一不可 ──────────────────────
        # ① 真实技能目录必须**一字未动**（隔离失效即红）
        assert _snapshot(_REAL_SKILL_DIR) == real_before, (
            "本用例触碰了真实 data/skills_repo（隔离失效）："
            f"{_REAL_SKILL_DIR}")
        # ② 覆盖性证明（**L20 起口径已变**）：memory_summary 属**文件轨独占**技能
        #    （主轨无记录，目录由技能仓库/内置技能提供），扩展 uninstall 必须
        #    **拒绝**删除；旧实现会经 file_store.delete → shutil.rmtree 把整棵
        #    技能目录（含 scripts/ 与 temp/）不可逆删掉。
        #    故此处断言「拒绝生效 + 目录仍在」——**与 L20 之前的断言方向相反**。
        res = manager.uninstall("skill", "memory_summary")
        assert isinstance(res, dict) and res.get("ok") is False, (
            f"L20 门禁失效：文件轨独占技能的 uninstall 未被拒绝，实际返回 {res!r}")
        assert "拒绝删除" in str(res.get("message", "")), (
            f"L20 拒绝原因不可读：{res!r}")
        assert (Path(isolated_extensions.file_store.repo_path)
                / "memory_summary").exists(), (
            "L20 门禁失效：文件轨独占技能目录被删除（不可逆）")
        assert isolated_extensions.store.get("memory_summary") is None, (
            "uninstall 意外写入了主轨（用例被弱化）")
    
    @pytest.mark.performance
    def test_extension_manager_list(self, isolated_extensions):
        """测试扩展列表查询性能（同样隔离，避免读真实扩展存储/技能轨）"""
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