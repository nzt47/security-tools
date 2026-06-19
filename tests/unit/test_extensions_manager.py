"""Extension Manager 单元测试"""
import pytest
from unittest.mock import patch, MagicMock

from agent.extensions.base import ExtensionMetadata, ExtensionType, ExtensionStatus
from agent.extensions.store import ExtensionStore
from agent.extensions.manager import ExtensionManager


class TestExtensionManager:
    """测试扩展管理器"""

    def test_extension_manager_init(self, tmp_path):
        """测试初始化"""
        manager = ExtensionManager()
        
        assert manager._store is not None
        assert isinstance(manager._store, ExtensionStore)

    def test_extension_manager_install_skill(self):
        """测试安装技能扩展"""
        manager = ExtensionManager()
        
        result = manager.install("skill", "self_reflection")
        
        assert result["ok"] is True

    def test_extension_manager_install_invalid_type(self):
        """测试安装无效类型"""
        manager = ExtensionManager()
        
        result = manager.install("invalid_type", "test")
        
        assert result["ok"] is False
        assert "未知扩展类型" in result["message"]

    def test_extension_manager_install_mcp(self):
        """测试安装 MCP 扩展"""
        manager = ExtensionManager()
        
        result = manager.install("mcp", "filesystem")
        
        assert isinstance(result, dict)
        assert "ok" in result

    def test_extension_manager_list_all(self):
        """测试列出所有扩展"""
        manager = ExtensionManager()
        
        extensions = manager.list_all()
        
        assert isinstance(extensions, list)

    def test_extension_manager_list_by_type(self):
        """测试按类型列出扩展"""
        manager = ExtensionManager()
        
        skills = manager.list_all(ext_type="skill")
        
        assert isinstance(skills, list)

    def test_extension_manager_set_network_config(self):
        """测试设置网络配置管理器"""
        manager = ExtensionManager()
        mock_config_mgr = MagicMock()
        
        manager.set_network_config_mgr(mock_config_mgr)
        
        assert manager