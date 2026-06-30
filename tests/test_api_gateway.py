"""API 网关单元测试"""

import unittest
import tempfile
from unittest.mock import Mock, MagicMock

from agent.api_gateway import ApiGateway, ApiKeyManager, AccessLogger, QuotaManager


class MockRequest:
    """模拟请求对象"""
    def __init__(self, path, method, headers=None):
        self.path = path
        self.method = method
        self.headers = headers or {}


class TestApiKeyManager(unittest.TestCase):
    """测试 API Key 管理器"""
    
    def test_create_key(self):
        """测试创建 API Key"""
        key_manager = ApiKeyManager()
        result = key_manager.create_key("user123", "Test key")
        
        self.assertIn("key", result)
        self.assertEqual(result["user_id"], "user123")
        self.assertEqual(result["description"], "Test key")
        self.assertTrue(result["enabled"])
    
    def test_validate_key(self):
        """测试验证 API Key"""
        key_manager = ApiKeyManager()
        key_info = key_manager.create_key("user123")
        
        validated = key_manager.validate_key(key_info["key"])
        self.assertIsNotNone(validated)
        self.assertEqual(validated["user_id"], "user123")
        
        invalid = key_manager.validate_key("invalid_key")
        self.assertIsNone(invalid)
    
    def test_increment_usage(self):
        """测试增加使用计数"""
        key_manager = ApiKeyManager()
        key_info = key_manager.create_key("user123")
        
        initial_count = key_info["usage_count"]
        key_manager.increment_usage(key_info["key"])
        
        updated = key_manager.get_key_info(key_info["key"])
        self.assertEqual(updated["usage_count"], initial_count + 1)


class TestQuotaManager(unittest.TestCase):
    """测试配额管理器"""
    
    def test_set_quota(self):
        """测试设置配额"""
        quota_manager = QuotaManager()
        quota_manager.set_quota("user123", "api_calls", 1000)
        
        status = quota_manager.get_quota_status("user123", "api_calls")
        self.assertEqual(status["limit"], 1000)
        self.assertEqual(status["used"], 0)
    
    def test_check_quota(self):
        """测试检查配额"""
        quota_manager = QuotaManager()
        quota_manager.set_quota("user123", "api_calls", 5)
        
        self.assertTrue(quota_manager.check_quota("user123", "api_calls"))
        
        for _ in range(5):
            quota_manager.consume_quota("user123", "api_calls")
        
        self.assertFalse(quota_manager.check_quota("user123", "api_calls"))
    
    def test_consume_quota(self):
        """测试消耗配额"""
        quota_manager = QuotaManager()
        quota_manager.set_quota("user123", "api_calls", 10)
        
        result = quota_manager.consume_quota("user123", "api_calls", 3)
        self.assertTrue(result)
        
        status = quota_manager.get_quota_status("user123", "api_calls")
        self.assertEqual(status["used"], 3)


class TestApiGateway(unittest.TestCase):
    """测试 API 网关"""
    
    def test_register_endpoint(self):
        """测试注册端点"""
        gateway = ApiGateway()
        
        def handler(request):
            return {"success": True}
        
        gateway.register_endpoint("/test", "GET", handler, auth_required=False)
        
        stats = gateway.get_stats()
        self.assertEqual(stats["endpoints"], 1)
    
    def test_handle_request(self):
        """测试处理请求"""
        gateway = ApiGateway()
        
        def handler(request):
            return {"success": True, "status_code": 200}
        
        gateway.register_endpoint("/test", "GET", handler, auth_required=False)
        
        request = MockRequest("/test", "GET", {})
        result = gateway.handle_request(request)
        
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("status_code"), 200)
    
    def test_authentication(self):
        """测试认证"""
        gateway = ApiGateway()
        key_info = gateway._api_key_manager.create_key("user123")
        
        def handler(request):
            return {"success": True, "status_code": 200}
        
        gateway.register_endpoint("/secure", "GET", handler, auth_required=True)
        
        # 无认证头
        request = MockRequest("/secure", "GET", {})
        result = gateway.handle_request(request)
        self.assertEqual(result.get("status_code"), 401)
        
        # 有效认证头
        request = MockRequest("/secure", "GET", {"X-API-Key": key_info["key"]})
        result = gateway.handle_request(request)
        self.assertEqual(result.get("status_code"), 200)
    
    def test_generate_swagger_doc(self):
        """测试生成 Swagger 文档"""
        gateway = ApiGateway()
        
        def handler(request):
            return {"success": True}
        
        gateway.register_endpoint("/test", "GET", handler, auth_required=False, 
                                summary="Test endpoint", description="A test endpoint")
        
        swagger = gateway.generate_swagger_doc()
        
        self.assertIn("openapi", swagger)
        self.assertIn("/test", swagger["paths"])


class TestAccessLogger(unittest.TestCase):
    """测试访问日志记录器"""
    
    def test_log_access(self):
        """测试记录访问日志"""
        logger = AccessLogger()
        
        log_entry = {
            "endpoint": "/test",
            "method": "GET",
            "status_code": 200,
            "user_id": "user123",
        }
        
        logger.log_access(log_entry)
        
        logs = logger.get_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["endpoint"], "/test")
    
    def test_get_stats(self):
        """测试获取统计信息"""
        logger = AccessLogger()
        
        for i in range(10):
            logger.log_access({
                "endpoint": "/test",
                "method": "GET",
                "status_code": 200 if i % 2 == 0 else 500,
                "user_id": f"user{i}",
            })
        
        stats = logger.get_stats("24h")
        self.assertEqual(stats["total_requests"], 10)


if __name__ == "__main__":
    unittest.main()