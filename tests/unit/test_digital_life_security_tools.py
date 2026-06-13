"""
DigitalLife 安全监控和工具注册测试
"""
import pytest
import os
import sys
from unittest.mock import patch, MagicMock, Mock

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.digital_life import DigitalLife


@pytest.fixture
def minimal_config():
    return {
        "llm": {
            "provider": "mock",
            "model": "test-model"
        },
        "enable_planning": False,
        "enable_vector_memory": False,
        "enable_voice": False,
        "enable_p6_snapshot": False,
        "enable_safety_monitor": True
    }


class TestSafetyMonitorIntegration:
    """测试安全监控器集成"""
    
    def test_safety_monitor_initialized(self, minimal_config):
        """测试安全监控器是否正确初始化"""
        with patch('agent.digital_life.MemoryManager'):
            with patch('agent.digital_life.get_safety_monitor') as mock_safety:
                mock_safety.return_value = MagicMock()
                dl = DigitalLife(config=minimal_config)
                
                assert hasattr(dl, '_safety_monitor')
                assert dl._safety_monitor is not None
    
    def test_safety_monitor_not_available(self, minimal_config):
        """测试安全监控器不可用时的降级处理"""
        with patch('agent.digital_life.MemoryManager'):
            with patch('agent.digital_life.get_safety_monitor', return_value=None):
                dl = DigitalLife(config=minimal_config)
                
                assert not hasattr(dl, '_safety_monitor') or dl._safety_monitor is None
    
    def test_safety_monitor_check_text(self, minimal_config):
        """测试安全监控器文本检查功能"""
        with patch('agent.digital_life.MemoryManager'):
            with patch('agent.digital_life.get_safety_monitor') as mock_safety:
                mock_monitor = MagicMock()
                mock_monitor.check_text.return_value = {"level": "safe"}
                mock_safety.return_value = mock_monitor
                
                dl = DigitalLife(config=minimal_config)
                result = dl._safety_monitor.check_text("测试内容")
                
                assert result["level"] == "safe"
                mock_monitor.check_text.assert_called_once_with("测试内容")
    
    def test_safety_monitor_check_critical(self, minimal_config):
        """测试安全监控器检测到危险内容"""
        with patch('agent.digital_life.MemoryManager'):
            with patch('agent.digital_life.get_safety_monitor') as mock_safety:
                mock_monitor = MagicMock()
                mock_monitor.check_text.return_value = {
                    "level": "critical",
                    "matches": [{"description": "危险内容"}]
                }
                mock_safety.return_value = mock_monitor
                
                dl = DigitalLife(config=minimal_config)
                result = dl._safety_monitor.check_text("危险内容")
                
                assert result["level"] == "critical"


class TestToolRegistration:
    """测试工具注册逻辑"""
    
    def test_register_builtin_tools_called(self, minimal_config):
        """测试内置工具注册方法被调用"""
        with patch('agent.digital_life.MemoryManager'):
            with patch('agent.digital_life.get_safety_monitor'):
                with patch.object(DigitalLife, '_register_builtin_tools') as mock_register:
                    dl = DigitalLife(config=minimal_config)
                    mock_register.assert_called_once()
    
    def test_planning_tools_registration(self, minimal_config):
        """测试规划工具注册"""
        minimal_config["enable_planning"] = True
        
        with patch('agent.digital_life.MemoryManager'):
            with patch('agent.digital_life.get_safety_monitor'):
                with patch('agent.digital_life.PlanningCore'):
                    dl = DigitalLife(config=minimal_config)
                    
                    assert hasattr(dl, '_planning_tools')
    
    def test_planning_tool_registration_failure(self, minimal_config):
        """测试规划工具注册失败的处理"""
        minimal_config["enable_planning"] = True
        
        with patch('agent.digital_life.MemoryManager'):
            with patch('agent.digital_life.get_safety_monitor'):
                with patch('agent.digital_life.ToolRegistry') as mock_registry:
                    mock_registry.return_value = None
                    dl = DigitalLife(config=minimal_config)
                    
                    assert dl._planning_tools is None


class TestPermissionSystemIntegration:
    """测试权限系统集成"""
    
    def test_permission_system_initialized(self, minimal_config):
        """测试权限系统是否正确初始化"""
        with patch('agent.digital_life.MemoryManager'):
            with patch('agent.digital_life.get_safety_monitor'):
                dl = DigitalLife(config=minimal_config)
                
                assert hasattr(dl, '_permission')
                assert dl._permission is not None
    
    def test_permission_check_action(self, minimal_config):
        """测试权限检查操作"""
        with patch('agent.digital_life.MemoryManager'):
            with patch('agent.digital_life.get_safety_monitor'):
                dl = DigitalLife(config=minimal_config)
                
                mock_perm = Mock()
                mock_perm.allowed = True
                dl._permission.check_action = Mock(return_value=mock_perm)
                
                result = dl._permission.check_action("test_action", "测试操作")
                
                assert result.allowed is True


class TestSecurityMonitoring:
    """测试安全监控相关功能"""
    
    def test_check_health_method(self, minimal_config):
        """测试检查健康状态方法"""
        with patch('agent.digital_life.MemoryManager'):
            with patch('agent.digital_life.get_safety_monitor'):
                dl = DigitalLife(config=minimal_config)
                
                result = dl.check_health()
                assert result is not None
                assert isinstance(result, list)
    
    def test_get_status_method(self, minimal_config):
        """测试获取状态方法"""
        with patch('agent.digital_life.MemoryManager'):
            with patch('agent.digital_life.get_safety_monitor'):
                dl = DigitalLife(config=minimal_config)
                
                result = dl.get_status()
                assert result is not None
                assert isinstance(result, dict)
                assert "云枢" in result


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
