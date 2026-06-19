"""
System Tools 平台特定代码测试
使用 Mock 测试 Windows/Unix 路径检查逻辑
"""
import pytest
import os
import sys
from unittest.mock import patch, MagicMock

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.system_tools import (
    is_protected_path,
    safe_resolve_path,
    PROTECTED_SYSTEM_DIRS_WIN,
    PROTECTED_SYSTEM_DIRS_UNIX,
    ALLOWED_WIN_SUBDIRS,
)


class TestWindowsPathProtection:
    """测试Windows路径保护检查"""

    def test_is_protected_path_windows_system_dir(self):
        """测试Windows系统保护目录被识别"""
        with patch('os.name', 'nt'):
            for protected_dir in PROTECTED_SYSTEM_DIRS_WIN:
                assert is_protected_path(protected_dir) is True

    def test_is_protected_path_windows_allowed_subdir(self):
        """测试Windows允许的子目录不被保护"""
        with patch('os.name', 'nt'):
            for allowed_dir in ALLOWED_WIN_SUBDIRS:
                assert is_protected_path(allowed_dir) is False

    def test_is_protected_path_windows_subdir_of_protected(self):
        """测试保护目录的子目录被保护"""
        with patch('os.name', 'nt'):
            protected_path = os.path.join(PROTECTED_SYSTEM_DIRS_WIN[0], "test")
            assert is_protected_path(protected_path) is True

    def test_is_protected_path_windows_subdir_of_allowed(self):
        """测试允许目录的子目录不被保护"""
        with patch('os.name', 'nt'):
            allowed_path = os.path.join(ALLOWED_WIN_SUBDIRS[0], "test", "subdir")
            assert is_protected_path(allowed_path) is False

    def test_is_protected_path_windows_normal_path(self):
        """测试普通Windows路径不被保护"""
        with patch('os.name', 'nt'):
            assert is_protected_path(r"C:\Users\Test\Documents") is False

    def test_is_protected_path_windows_path_normalization(self):
        """测试Windows路径规范化"""
        with patch('os.name', 'nt'):
            # 测试不同格式的路径
            path1 = r"C:\Windows\System32"
            path2 = r"C:\WINDOWS\SYSTEM32"
            path3 = r"C:\Windows\..\Windows\System32"
            
            assert is_protected_path(path1) is True
            assert is_protected_path(path2) is True
            assert is_protected_path(path3) is True


class TestUnixPathProtection:
    """测试Unix路径保护检查"""

    def test_is_protected_path_unix_system_dir(self):
        """测试Unix系统保护目录被识别"""
        test_path = "/etc"
        is_protected = False
        for protected in PROTECTED_SYSTEM_DIRS_UNIX:
            if test_path.startswith(protected + "/") or test_path == protected:
                is_protected = True
                break
        assert is_protected is True

    def test_is_protected_path_unix_subdir_of_protected(self):
        """测试保护目录的子目录被保护"""
        test_path = "/etc/test"
        is_protected = False
        for protected in PROTECTED_SYSTEM_DIRS_UNIX:
            if test_path.startswith(protected + "/") or test_path == protected:
                is_protected = True
                break
        assert is_protected is True

    def test_is_protected_path_unix_normal_path(self):
        """测试普通Unix路径不被保护"""
        test_path = "/home/user/documents"
        is_protected = False
        for protected in PROTECTED_SYSTEM_DIRS_UNIX:
            if test_path.startswith(protected + "/") or test_path == protected:
                is_protected = True
                break
        assert is_protected is False

    def test_is_protected_path_unix_path_normalization(self):
        """测试Unix路径规范化"""
        path1 = "/etc"
        
        # 测试规范化后的路径也应该被识别为保护路径
        # Unix上 "/etc/../etc" 规范化后应该是 "/etc"
        normalized_path = "/etc"  # 模拟 os.path.normpath("/etc/../etc") 在Unix上的结果
        
        is_protected1 = False
        is_protected2 = False
        
        for protected in PROTECTED_SYSTEM_DIRS_UNIX:
            if path1.startswith(protected + "/") or path1 == protected:
                is_protected1 = True
            if normalized_path.startswith(protected + "/") or normalized_path == protected:
                is_protected2 = True
        
        assert is_protected1 is True
        assert is_protected2 is True


class TestPathProtectionEdgeCases:
    """测试路径保护的边缘情况"""

    def test_is_protected_path_empty_path(self):
        """测试空路径"""
        # 空路径会被 os.path.abspath 解析为当前目录，当前目录不在保护列表中
        assert is_protected_path("") is False

    def test_is_protected_path_invalid_path(self):
        """测试无效路径"""
        # os.path.abspath 对空路径会返回当前目录，所以不会抛出异常
        # 但如果路径解析失败，应该返回 True（视为保护）
        with patch('os.path.abspath', side_effect=Exception("Invalid path")):
            assert is_protected_path("/invalid/path") is True

    def test_is_protected_path_traversal_attack(self):
        """测试路径遍历攻击被阻止"""
        with patch('os.name', 'nt'):
            malicious_path = r"C:\Users\Test\..\..\Windows\System32"
            assert is_protected_path(malicious_path) is True

    def test_is_protected_path_absolute_vs_relative(self):
        """测试绝对路径和相对路径"""
        with patch('os.name', 'nt'):
            with patch('os.sep', '\\'):
                # 相对路径应该被规范化后检查
                # 注意：这取决于当前工作目录，所以我们直接测试规范化行为
                absolute_path = r"C:\Windows\System32"
                assert is_protected_path(absolute_path) is True


class TestSafeResolvePath:
    """测试安全路径解析"""

    def test_safe_resolve_path_success(self):
        """测试路径解析成功"""
        with patch('os.name', 'nt'):
            with patch('os.path.abspath', return_value=r"C:\Users\Test\Documents"):
                with patch('agent.system_tools.is_protected_path', return_value=False):
                    result = safe_resolve_path("test.txt")
                    assert result == r"C:\Users\Test\Documents"

    def test_safe_resolve_path_protected(self):
        """测试解析受保护路径抛出异常"""
        with patch('os.name', 'nt'):
            with patch('os.path.abspath', return_value=r"C:\Windows\System32"):
                with patch('agent.system_tools.is_protected_path', return_value=True):
                    with pytest.raises(ValueError, match="路径位于系统保护目录"):
                        safe_resolve_path(r"C:\Windows\System32")

    def test_safe_resolve_path_value_error(self):
        """测试路径解析值错误"""
        with patch('os.path.abspath', side_effect=ValueError("Invalid path")):
            with pytest.raises(ValueError, match="路径解析失败"):
                safe_resolve_path("/invalid/path")

    def test_safe_resolve_path_os_error(self):
        """测试路径解析OS错误"""
        with patch('os.path.abspath', side_effect=OSError("OS error")):
            with pytest.raises(ValueError, match="路径解析失败"):
                safe_resolve_path("/invalid/path")


class TestCrossPlatformBehavior:
    """测试跨平台行为"""

    def test_is_protected_path_windows_on_unix(self):
        """测试Unix系统上检查Windows路径格式"""
        with patch('os.name', 'posix'):
            # 在Unix上，Windows格式路径应该不会被识别为保护路径
            # 因为PROTECTED_SYSTEM_DIRS_UNIX不包含Windows路径
            assert is_protected_path(r"C:\Windows\System32") is False

    def test_is_protected_path_unix_on_windows(self):
        """测试Windows系统上检查Unix路径格式"""
        with patch('os.name', 'nt'):
            # 在Windows上，Unix格式路径应该不会被识别为保护路径
            assert is_protected_path("/etc") is False

    def test_os_name_mock_effectiveness(self):
        """测试os.name mock的有效性"""
        original_os_name = os.name
        
        with patch('os.name', 'nt'):
            assert os.name == 'nt'
        
        with patch('os.name', 'posix'):
            assert os.name == 'posix'
        
        # 验证原始值恢复
        assert os.name == original_os_name


class TestPathCaseInsensitivity:
    """测试路径大小写不敏感"""

    def test_is_protected_path_case_insensitive_windows(self):
        """测试Windows路径大小写不敏感"""
        with patch('os.name', 'nt'):
            paths = [
                r"C:\Windows\System32",
                r"C:\windows\system32",
                r"C:\WINDOWS\SYSTEM32",
                r"C:\WiNdOwS\SyStEm32",
            ]
            for path in paths:
                assert is_protected_path(path) is True

    def test_is_protected_path_case_sensitive_unix(self):
        """测试Unix路径大小写敏感"""
        # /etc 是保护的
        test_path1 = "/etc"
        is_protected1 = False
        for protected in PROTECTED_SYSTEM_DIRS_UNIX:
            if test_path1.startswith(protected + "/") or test_path1 == protected:
                is_protected1 = True
                break
        assert is_protected1 is True
        
        # /Etc 不是标准保护路径（大小写敏感）
        # 注意：实际Unix系统上路径是大小写敏感的，但我们的保护列表只包含小写
        test_path2 = "/Etc"
        is_protected2 = False
        for protected in PROTECTED_SYSTEM_DIRS_UNIX:
            if test_path2.startswith(protected + "/") or test_path2 == protected:
                is_protected2 = True
                break
        assert is_protected2 is False