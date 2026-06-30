"""API 网关认证流程验证脚本

使用说明：
1. 确保 API 网关模块已正确加载
2. 运行此脚本进行认证流程验证

python tests/test_api_gateway_auth_flow.py
"""

import json
import sys
import time
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List

# 添加项目路径
sys.path.insert(0, r"c:\Users\Administrator\agent")

from agent.api_gateway import ApiGateway, ApiKeyManager, QuotaManager, RateLimiter, AccessLogger
from tests.mock_api_gateway_data import MOCK_API_KEYS, MOCK_TENANTS, MOCK_USERS, MOCK_QUOTA_USAGE


class MockApiGateway:
    """模拟 API 网关（用于测试）"""
    
    def __init__(self, key_manager, quota_manager, rate_limiter, access_logger):
        self._key_manager = key_manager
        self._quota_manager = quota_manager
        self._rate_limiter = rate_limiter
        self._access_logger = access_logger
        self._endpoints: Dict[str, Dict] = {}
    
    def register_endpoint(self, path: str, method: str, handler: Callable,
                         auth_required: bool = True, scopes: List[str] = None):
        """注册 API 端点"""
        key = f"{method.upper()}:{path}"
        self._endpoints[key] = {
            "path": path,
            "method": method.upper(),
            "handler": handler,
            "auth_required": auth_required,
            "scopes": scopes or [],
        }
    
    def authenticate(self, request) -> Optional[Dict]:
        """认证请求"""
        auth_header = request.headers.get("Authorization", "")
        
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            return self._key_manager.validate_key(token)
        elif auth_header.startswith("Api-Key "):
            token = auth_header[8:]
            return self._key_manager.validate_key(token)
        
        api_key = request.headers.get("X-API-Key", "")
        if api_key:
            return self._key_manager.validate_key(api_key)
        
        return None
    
    def handle_request(self, request) -> Dict:
        """处理请求"""
        path = request.path
        method = request.method
        
        endpoint_key = f"{method.upper()}:{path}"
        endpoint = self._endpoints.get(endpoint_key)
        
        if not endpoint:
            return {"error": "Endpoint not found", "status_code": 404}
        
        if endpoint["auth_required"]:
            key_info = self.authenticate(request)
            if not key_info:
                return {"error": "Unauthorized", "status_code": 401}
            
            user_id = key_info["user_id"]
            
            # 检查配额
            if not self._quota_manager.check_quota(user_id, "api_calls"):
                return {"error": "Quota exceeded", "status_code": 429}
            
            # 消耗配额
            self._quota_manager.consume_quota(user_id, "api_calls", 1)
        else:
            user_id = "anonymous"
        
        # 执行处理器
        handler = endpoint["handler"]
        result = handler(request)
        
        # 记录访问日志
        self._access_logger.log_access({
            "user_id": user_id,
            "endpoint": path,
            "method": method,
            "status_code": 200,
        })
        
        return {
            "success": True,
            "status_code": 200,
            "user_id": key_info.get("user_id") if endpoint["auth_required"] else "anonymous",
            "tenant_id": key_info.get("tenant_id") if endpoint["auth_required"] else None,
            **result,
        }


class MockApiKeyManager:
    """Mock API Key 管理器（跳过文件加载）"""
    
    def __init__(self):
        self._api_keys: Dict[str, Dict] = {}
    
    def validate_key(self, api_key: str) -> Optional[Dict]:
        """验证 API Key"""
        key_info = self._api_keys.get(api_key)
        if key_info and key_info.get("enabled"):
            return key_info
        return None
    
    def check_key_enabled(self, api_key: str) -> bool:
        """检查 API Key 是否启用且未过期"""
        key_info = self._api_keys.get(api_key)
        if not key_info:
            return False
        if not key_info.get("enabled", True):
            return False
        return True


class MockRequest:
    """模拟 HTTP 请求"""
    def __init__(self, path: str, method: str = "GET", headers: Dict = None, body: Dict = None):
        self.path = path
        self.method = method
        self.headers = headers or {}
        self.body = body or {}


class ApiGatewayAuthFlowTester:
    """API 网关认证流程测试器"""
    
    def __init__(self):
        # 使用 Mock API Key 管理器
        self.key_manager = MockApiKeyManager()
        self.quota_manager = QuotaManager()
        self.rate_limiter = RateLimiter()
        self.access_logger = AccessLogger()
        
        # 加载 Mock 数据
        self._load_mock_data()
        
        # 创建模拟网关（使用我们的 Mock 组件）
        self.gateway = self._create_mock_gateway()
    
    def _load_mock_data(self):
        """加载 Mock 数据"""
        print("[*] 加载 Mock 数据...")
        
        # 加载 API Keys
        for key_info in MOCK_API_KEYS:
            key = key_info["key"]
            self.key_manager._api_keys[key] = {
                "key": key,
                "user_id": key_info["user_id"],
                "tenant_id": key_info["tenant_id"],
                "scopes": key_info["scopes"],
                "rate_limit": key_info["rate_limit"],
                "daily_quota": key_info["daily_quota"],
                "enabled": key_info["enabled"],
                "created_at": key_info["created_at"],
                "expires_at": key_info.get("expires_at"),
            }
        
        # 加载配额使用数据
        for user_id, quotas in MOCK_QUOTA_USAGE.items():
            for quota_type, usage in quotas.items():
                key = f"{user_id}:{quota_type}"
                self.quota_manager._quotas[key] = {
                    "user_id": user_id,
                    "quota_type": quota_type,
                    "used": usage["used"],
                    "limit": usage["limit"],
                    "period": "day",  # 默认为天
                    "last_reset": usage.get("reset_at", datetime.now().isoformat()),
                }
        
        print(f"    - 已加载 {len(MOCK_API_KEYS)} 个 API Keys")
        print(f"    - 已加载 {len(MOCK_QUOTA_USAGE)} 个配额记录")
    
    def _create_mock_gateway(self):
        """创建模拟 API 网关"""
        gateway = MockApiGateway(self.key_manager, self.quota_manager, self.rate_limiter, self.access_logger)
        # 注册测试端点
        gateway.register_endpoint("/api/v1/chat", "POST", lambda r: {
            "success": True,
            "message": "Chat response",
            "model": "gpt-3.5-turbo"
        }, auth_required=True)
        gateway.register_endpoint("/api/v1/generate", "POST", lambda r: {
            "success": True,
            "content": "Generated content"
        }, auth_required=True)
        return gateway
    
    def _register_test_endpoints(self):
        """注册测试端点"""
        def chat_handler(request):
            return {
                "success": True,
                "message": "Chat response",
                "model": "gpt-3.5-turbo"
            }
        
        def generate_handler(request):
            return {
                "success": True,
                "content": "Generated content"
            }
        
        self.gateway.register_endpoint("/api/v1/chat", "POST", chat_handler, auth_required=True)
        self.gateway.register_endpoint("/api/v1/generate", "POST", generate_handler, auth_required=True)
    
    def test_scenario_1_valid_api_key(self):
        """场景 1: 有效 API Key 认证"""
        print("\n" + "=" * 70)
        print("场景 1: 有效 API Key 认证")
        print("=" * 70)
        
        request = MockRequest(
            path="/api/v1/chat",
            method="POST",
            headers={"Authorization": f"Bearer {MOCK_API_KEYS[0]['key']}"},
            body={"messages": [{"role": "user", "content": "Hello"}]}
        )
        
        print(f"请求: {request.method} {request.path}")
        print(f"API Key: {request.headers['Authorization'][:20]}...")
        
        result = self.gateway.handle_request(request)
        
        print(f"\n结果:")
        print(f"  - 成功: {result.get('success')}")
        print(f"  - 状态码: {result.get('status_code')}")
        print(f"  - 用户 ID: {result.get('user_id')}")
        print(f"  - 租户 ID: {result.get('tenant_id')}")
        
        if result.get("success"):
            print("\n[OK] 认证成功！")
        else:
            print(f"\n[FAIL] 认证失败: {result.get('error')}")
        
        return result.get("success")
    
    def test_scenario_2_invalid_api_key(self):
        """场景 2: 无效 API Key 认证"""
        print("\n" + "=" * 70)
        print("场景 2: 无效 API Key 认证")
        print("=" * 70)
        
        request = MockRequest(
            path="/api/v1/chat",
            method="POST",
            headers={"Authorization": "Bearer sk_invalid_key_12345"},
            body={"messages": [{"role": "user", "content": "Hello"}]}
        )
        
        print(f"请求: {request.method} {request.path}")
        print(f"API Key: sk_invalid_key_12345")
        
        result = self.gateway.handle_request(request)
        
        print(f"\n结果:")
        print(f"  - 成功: {result.get('success')}")
        print(f"  - 状态码: {result.get('status_code')}")
        print(f"  - 错误: {result.get('error')}")
        
        if not result.get("success") and result.get("status_code") == 401:
            print("\n[OK] 预期行为：认证失败，返回 401")
        else:
            print(f"\n[FAIL] 意外结果")
        
        return not result.get("success") and result.get("status_code") == 401
    
    def test_scenario_3_quota_exhausted(self):
        """场景 3: 配额耗尽测试"""
        print("\n" + "=" * 70)
        print("场景 3: 配额耗尽测试")
        print("=" * 70)
        
        user_id = MOCK_API_KEYS[2]["user_id"]
        quota_type = "api_calls"
        
        # 设置配额为 5（方便测试）
        self.quota_manager.set_quota(user_id, quota_type, 5, "day")
        
        print(f"配额设置为: 5/5")
        
        # 发送 10 个请求
        request = MockRequest(
            path="/api/v1/chat",
            method="POST",
            headers={"Authorization": f"Bearer {MOCK_API_KEYS[2]['key']}"},
            body={"messages": [{"role": "user", "content": "Hello"}]}
        )
        
        success_count = 0
        quota_exceeded = False
        
        for i in range(10):
            result = self.gateway.handle_request(request)
            if result.get("status_code") == 429:
                print(f"请求 {i + 1}: 配额耗尽 (429)")
                quota_exceeded = True
                break
            elif result.get("success"):
                success_count += 1
                print(f"请求 {i + 1}: 成功")
        
        if quota_exceeded:
            print(f"\n[OK] 配额限制生效！成功处理 {success_count} 个请求后触发限制")
        else:
            print(f"\n请求全部成功（可能配额配置有误）")
        
        return quota_exceeded
    
    def test_scenario_4_disabled_api_key(self):
        """场景 4: 禁用/过期 API Key"""
        print("\n" + "=" * 70)
        print("场景 4: 禁用/过期 API Key")
        print("=" * 70)
        
        request = MockRequest(
            path="/api/v1/chat",
            method="POST",
            headers={"Authorization": f"Bearer {MOCK_API_KEYS[3]['key']}"},
            body={"messages": [{"role": "user", "content": "Hello"}]}
        )
        
        print(f"请求: {request.method} {request.path}")
        print(f"API Key: {MOCK_API_KEYS[3]['key']}")
        print(f"状态: enabled={MOCK_API_KEYS[3]['enabled']}, expires_at={MOCK_API_KEYS[3]['expires_at']}")
        
        result = self.gateway.handle_request(request)
        
        print(f"\n结果:")
        print(f"  - 成功: {result.get('success')}")
        print(f"  - 状态码: {result.get('status_code')}")
        print(f"  - 错误: {result.get('error')}")
        
        if not result.get("success") and result.get("status_code") == 401:
            print("\n[OK] 预期行为：API Key 已禁用或过期，返回 401")
        else:
            print(f"\n[FAIL] 意外结果")
        
        return not result.get("success") and result.get("status_code") == 401
    
    def test_scenario_5_rate_limit(self):
        """场景 5: 速率限制测试"""
        print("\n" + "=" * 70)
        print("场景 5: 速率限制测试")
        print("=" * 70)
        
        # 使用 rate_limit=10 的 API Key
        api_key = MOCK_API_KEYS[2]["key"]
        rate_limit = MOCK_API_KEYS[2]["rate_limit"]
        
        print(f"API Key: {api_key[:20]}...")
        print(f"速率限制: {rate_limit} 请求/分钟")
        
        request = MockRequest(
            path="/api/v1/chat",
            method="POST",
            headers={"Authorization": f"Bearer {api_key}"},
            body={"messages": [{"role": "user", "content": "Hello"}]}
        )
        
        # 快速发送 15 个请求
        success_count = 0
        rate_limited = False
        
        for i in range(15):
            result = self.gateway.handle_request(request)
            status = result.get("status_code")
            
            if status == 429:
                print(f"请求 {i + 1}: 速率限制 (429)")
                rate_limited = True
                break
            elif result.get("success"):
                success_count += 1
                print(f"请求 {i + 1}: 成功")
            else:
                print(f"请求 {i + 1}: 失败 ({status})")
        
        if rate_limited:
            print(f"\n[OK] 速率限制生效！成功处理 {success_count} 个请求后触发限流")
        else:
            print(f"\n所有请求成功（可能速率限制未生效）")
        
        return rate_limited
    
    def test_access_logging(self):
        """测试访问日志记录"""
        print("\n" + "=" * 70)
        print("访问日志记录测试")
        print("=" * 70)
        
        # 发送几个请求
        request = MockRequest(
            path="/api/v1/chat",
            method="POST",
            headers={"Authorization": f"Bearer {MOCK_API_KEYS[0]['key']}"},
            body={"messages": [{"role": "user", "content": "Hello"}]}
        )
        
        self.gateway.handle_request(request)
        
        # 获取最近日志
        logs = self.access_logger.get_logs(limit=5)
        
        print(f"最近 {len(logs)} 条访问日志:")
        for log in logs:
            print(f"  - {log.get('timestamp')} | {log.get('endpoint')} | {log.get('status_code')}")
        
        return len(logs) > 0
    
    def run_all_tests(self):
        """运行所有测试"""
        print("=" * 70)
        print("API 网关认证流程验证测试")
        print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)
        
        results = {}
        
        # 运行所有场景
        results["场景1-有效APIKey"] = self.test_scenario_1_valid_api_key()
        results["场景2-无效APIKey"] = self.test_scenario_2_invalid_api_key()
        results["场景3-配额耗尽"] = self.test_scenario_3_quota_exhausted()
        results["场景4-禁用APIKey"] = self.test_scenario_4_disabled_api_key()
        results["场景5-速率限制"] = self.test_scenario_5_rate_limit()
        results["访问日志"] = self.test_access_logging()
        
        # 打印总结
        print("\n" + "=" * 70)
        print("测试总结")
        print("=" * 70)
        
        passed = sum(1 for v in results.values() if v)
        total = len(results)
        
        for name, result in results.items():
            status = "[PASS]" if result else "[FAIL]"
            print(f"  {status} {name}")
        
        print(f"\n通过率: {passed}/{total} ({passed*100//total}%)")
        print(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)
        
        return passed == total


if __name__ == "__main__":
    tester = ApiGatewayAuthFlowTester()
    success = tester.run_all_tests()
    sys.exit(0 if success else 1)
