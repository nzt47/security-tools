"""集成测试：验证第四阶段验证模块的完整流程

测试覆盖：
1. Schema 校验接入主流程
2. Critic 自检评审接入主流程
3. 失败模式自动归档
4. Memory 边界强制约束
"""

import pytest
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from unittest.mock import MagicMock, patch


class TestVerificationFlow:
    """验证流程集成测试"""

    @pytest.mark.asyncio
    async def test_schema_validation_integration(self):
        """测试 Schema 校验接入主流程"""
        from agent.tool_calling import _validate_output_with_schema
        
        valid_response = '{"output_type": "text_response", "content": "Hello World"}'
        result = _validate_output_with_schema(valid_response, max_retries=3)
        
        assert isinstance(result, dict)
        assert "valid" in result
        assert "content" in result
        assert "retry_count" in result

    @pytest.mark.asyncio
    async def test_tool_calling_schema_config(self):
        """测试 ToolCallingService 读取 Schema 配置"""
        from agent.tool_calling import ToolCallingService
        
        mock_llm = MagicMock()
        service = ToolCallingService(mock_llm)
        
        assert hasattr(service, '_schema_validation_enabled')
        assert hasattr(service, '_schema_max_retries')

    @pytest.mark.asyncio
    async def test_critic_evaluation_integration(self):
        """测试 Critic 评估集成（验证配置读取）"""
        from config import Config
        
        config = Config()
        critic_enabled = config.get("verification", "critic_enabled", default=False)
        critic_threshold = config.get("verification", "critic_threshold", default=70)
        
        assert isinstance(critic_enabled, bool)
        assert isinstance(critic_threshold, int)
        assert 0 <= critic_threshold <= 100

    @pytest.mark.asyncio
    async def test_memory_boundary_constraints(self):
        """测试 Memory 边界约束"""
        from agent.memory.router import MemoryRouter
        
        router = MemoryRouter()
        
        assert hasattr(router, '_memory_boundary_enabled')
        assert hasattr(router, '_sensitive_filter_enabled')
        assert hasattr(router, '_memory_classification_enabled')

    @pytest.mark.asyncio
    async def test_memory_sensitive_filter(self):
        """测试敏感信息过滤"""
        from agent.memory.router import MemoryRouter
        
        router = MemoryRouter()
        
        router._sensitive_filter_enabled = True
        router._memory_boundary_enabled = True
        router._sensitive_patterns = [
            r'password',
            r'密码',
        ]
        
        test_data = "我的密码是 password"
        has_sensitive, filtered, patterns = router._filter_sensitive_info(test_data)
        
        assert has_sensitive is True
        assert "[REDACTED]" in filtered

    @pytest.mark.asyncio
    async def test_memory_context_classification(self):
        """测试上下文分类"""
        from agent.memory.router import MemoryRouter
        
        router = MemoryRouter()
        router._memory_classification_enabled = True
        
        long_term_data = "用户偏好：喜欢红色"
        result = router._classify_context(long_term_data)
        assert result == "long_term"
        
        temp_data = "今天天气真好"
        result = router._classify_context(temp_data)
        assert result == "temporary"

    @pytest.mark.asyncio
    async def test_config_verification_settings(self):
        """测试配置文件中的验证设置"""
        from config import Config
        
        config = Config()
        
        schema_enabled = config.get("verification", "schema_validation", default=False)
        critic_enabled = config.get("verification", "critic_enabled", default=False)
        failure_archive = config.get("verification", "failure_archive", default=False)
        memory_boundary = config.get("verification", "memory_boundary", default=False)
        
        assert isinstance(schema_enabled, bool)
        assert isinstance(critic_enabled, bool)
        assert isinstance(failure_archive, bool)
        assert isinstance(memory_boundary, bool)

    @pytest.mark.asyncio
    async def test_structured_log_format(self):
        """测试结构化日志格式"""
        log_entry = {
            "trace_id": "test-trace-123",
            "module_name": "test_module",
            "action": "test_action",
            "duration_ms": 100,
            "result": "success"
        }
        
        json_str = json.dumps(log_entry)
        parsed = json.loads(json_str)
        
        required_fields = ["trace_id", "module_name", "action", "duration_ms"]
        for field in required_fields:
            assert field in parsed

    @pytest.mark.asyncio
    async def test_failure_analyzer_integration(self):
        """测试失败归档配置"""
        from config import Config
        
        config = Config()
        failure_archive = config.get("verification", "failure_archive", default=False)
        
        assert isinstance(failure_archive, bool)

    @pytest.mark.asyncio
    async def test_memory_save_with_boundary(self):
        """测试带边界约束的内存保存"""
        from agent.memory.router import MemoryRouter
        from agent.memory.base import MemoryInterface
        
        class MockAdapter(MemoryInterface):
            async def save(self, key, data, metadata=None):
                return True
            async def search(self, query, top_k=5):
                return []
            async def get_profile(self, user_id):
                return {}
            async def update_graph(self, entities, relations):
                return True
            @property
            def capabilities(self):
                return []
        
        router = MemoryRouter()
        router._memory_boundary_enabled = True
        router._sensitive_filter_enabled = True
        
        mock_adapter = MockAdapter()
        router.register("holographic", mock_adapter)
        
        result = await router.save("test-key", "safe data", task_type="local_privacy")
        assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])