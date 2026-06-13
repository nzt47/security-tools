"""SystemTools 平台特定代码 Mock 测试方案"""
import pytest
from unittest.mock import MagicMock, patch, mock_open
import os


class TestSystemToolsPathProtection:
    """测试路径保护功能"""

    def test_windows_path_protection_allowed(self):
        """测试 Windows 允许的路径"""
        with patch('os.name', 'nt'):
            from agent.system_tools import is_protected_path
            
            result = is_protected_path("C:\\Users\\test\\file.txt")
            
            assert result is False

    def test_windows_path_protection_denied(self):
        """测试 Windows 被保护的路径"""
        with patch('os.name', 'nt'):
            from agent.system_tools import is_protected_path
            
            result = is_protected_path("C:\\Windows\\System32\\")
            
            assert result is True

    def test_unix_path_protection_allowed(self):
        """测试 Unix 允许的路径"""
        with patch('os.name', 'posix'):
            from agent.system_tools import is_protected_path
            
            result = is_protected_path("/home/user/file.txt")
            
            assert result is False


class TestSystemToolsWindowsSpecific:
    """测试 Windows 特定功能"""

    def test_windows_executable_extensions(self):
        """测试 Windows 可执行文件扩展名识别"""
        from agent.system_tools import is_executable_extension
        
        assert is_executable_extension("script.exe") is True
        assert is_executable_extension("program.bat") is True
        assert is_executable_extension("file.txt") is False
        assert is_executable_extension("script.py") is False

    def test_windows_temp_path_allowed(self):
        """测试 Windows Temp 目录允许访问"""
        with patch('os.name', 'nt'):
            from agent.system_tools import is_protected_path
            
            # Temp 目录应该被允许
            assert is_protected_path("C:\\Windows\\Temp\\test.txt") is False


class TestSystemToolsCrossPlatform:
    """测试跨平台功能"""

    def test_safe_resolve_path(self):
        """测试安全路径解析"""
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = "/safe/path/file.txt"
            with patch('agent.system_tools.is_protected_path') as mock_safe:
                mock_safe.return_value = False
                
                from agent.system_tools import safe_resolve_path
                
                result = safe_resolve_path("relative/path")
                
                assert result == "/safe/path/file.txt"

    def test_safe_resolve_path_unsafe(self):
        """测试不安全路径解析"""
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = "/etc/passwd"
            with patch('agent.system_tools.is_protected_path') as mock_safe:
                mock_safe.return_value = True
                
                from agent.system_tools import safe_resolve_path
                
                with pytest.raises(ValueError):
                    safe_resolve_path("../../../etc/passwd")


class TestSystemToolsBinaryDetection:
    """测试二进制内容检测"""

    def test_binary_content_detection(self):
        """测试二进制内容检测"""
        from agent.system_tools import is_binary_content
        
        # 纯文本
        assert is_binary_content(b"hello world") is False
        # 包含 NULL 字节
        assert is_binary_content(b"hello\x00world") is True
        # 空数据
        assert is_binary_content(b"") is False


class TestSystemToolsFileOperations:
    """测试文件操作"""

    def test_read_file_not_found(self):
        """测试读取不存在的文件"""
        with patch('agent.system_tools.safe_resolve_path') as mock_resolve:
            mock_resolve.return_value = "/safe/path/file.txt"
        with patch('os.path.exists') as mock_exists:
            mock_exists.return_value = False
            
            from agent.system_tools import read_file
            
            result = read_file("/safe/path/file.txt")
            
            assert result["ok"] is False
            assert "文件不存在" in result["error"]

    def test_write_file_executable(self):
        """测试写入可执行文件被拒绝"""
        with patch('agent.system_tools.safe_resolve_path') as mock_resolve:
            mock_resolve.return_value = "/safe/path/script.exe"
        with patch('agent.system_tools.is_executable_extension') as mock_exec:
            mock_exec.return_value = True
            
            from agent.system_tools import write_file
            
            result = write_file("/safe/path/script.exe", "test content")
            
            assert result["ok"] is False
            assert "禁止写入可执行" in result["error"]


class TestSystemToolsHighRiskScenarios:
    """测试高风险场景"""

    def test_path_traversal_attempt(self):
        """测试路径遍历攻击"""
        with patch('os.name', 'nt'):
            from agent.system_tools import safe_resolve_path
            
            with patch('os.path.abspath') as mock_abspath:
                mock_abspath.return_value = "C:\\Windows\\System32"
                with patch('agent.system_tools.is_protected_path') as mock_protected:
                    mock_protected.return_value = True
                    
                    with pytest.raises(ValueError):
                        safe_resolve_path("../../../Windows/System32")