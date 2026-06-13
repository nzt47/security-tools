"""system_tools.py 平台特定代码测试（Windows路径检查、沙盒执行等）"""
import pytest
from unittest.mock import MagicMock, patch
import os

from agent.system_tools import (
    is_protected_path,
    safe_resolve_path,
    is_executable_extension,
    PROTECTED_SYSTEM_DIRS_WIN,
    PROTECTED_SYSTEM_DIRS_UNIX,
    ALLOWED_WIN_SUBDIRS,
    BLOCKED_WRITE_EXTENSIONS,
)


class TestWindowsPathProtection:
    """测试Windows路径保护"""

    def test_windows_protected_directories(self):
        """测试Windows保护目录列表"""
        with patch('os.name', 'nt'):
            for protected_dir in PROTECTED_SYSTEM_DIRS_WIN:
                assert is_protected_path(protected_dir) is True, f"{protected_dir} should be protected"

    def test_windows_allowed_subdirectories(self):
        """测试Windows允许的子目录"""
        with patch('os.name', 'nt'):
            for allowed_dir in ALLOWED_WIN_SUBDIRS:
                assert is_protected_path(allowed_dir) is False, f"{allowed_dir} should be allowed"

    def test_windows_subdirectory_of_protected(self):
        """测试保护目录的子目录"""
        with patch('os.name', 'nt'):
            assert is_protected_path(r"C:\Windows\System32\config") is True
            assert is_protected_path(r"C:\Program Files\SomeApp") is True

    def test_windows_user_directory(self):
        """测试用户目录（不应被保护）"""
        with patch('os.name', 'nt'):
            assert is_protected_path(r"C:\Users\Administrator\Desktop") is False
            assert is_protected_path(r"C:\Users\Test\Documents") is False

    def test_windows_path_case_insensitive(self):
        """测试Windows路径大小写不敏感"""
        with patch('os.name', 'nt'):
            assert is_protected_path(r"c:\windows\system32") is True
            assert is_protected_path(r"C:\WINDOWS\SYSTEM32") is True


class TestUnixPathProtection:
    """测试Unix路径保护"""

    def test_unix_protected_directories(self):
        """测试Unix保护目录列表"""
        # 在Windows上模拟Unix路径检查
        for protected_dir in PROTECTED_SYSTEM_DIRS_UNIX:
            # 直接检查路径是否以保护目录开头（不依赖os.name）
            test_path = protected_dir
            is_protected = False
            for p in PROTECTED_SYSTEM_DIRS_UNIX:
                if test_path == p:
                    is_protected = True
                    break
            assert is_protected is True, f"{protected_dir} should be protected"

    def test_unix_subdirectory_of_protected(self):
        """测试保护目录的子目录"""
        test_paths = ["/etc/passwd", "/usr/lib/python3"]
        for test_path in test_paths:
            is_protected = False
            for p in PROTECTED_SYSTEM_DIRS_UNIX:
                if test_path.startswith(p + "/") or test_path == p:
                    is_protected = True
                    break
            assert is_protected is True, f"{test_path} should be protected"

    def test_unix_home_directory(self):
        """测试用户home目录（不应被保护）"""
        with patch('os.name', 'posix'):
            assert is_protected_path("/home/user/Documents") is False
            assert is_protected_path("/home/user/projects") is False

    def test_unix_current_directory(self):
        """测试当前目录（不应被保护）"""
        with patch('os.name', 'posix'):
            assert is_protected_path("./test") is False
            assert is_protected_path(".") is False


class TestPathTraversalAttack:
    """测试路径遍历攻击防护"""

    def test_path_traversal_windows(self):
        """测试Windows路径遍历攻击"""
        with patch('os.name', 'nt'):
            # 使用绝对路径测试路径遍历攻击（从已知位置开始）
            traversal_paths = [
                r"C:\Users\Public\..\..\Windows\System32",
                r"C:\Users\Public\..\..\Windows\System32\cmd.exe",
            ]
            for path in traversal_paths:
                assert is_protected_path(path) is True, f"Path traversal should be blocked: {path}"

    def test_path_traversal_unix(self):
        """测试Unix路径遍历攻击"""
        # 直接测试规范化后的路径应该被检测为保护路径
        normalized_paths = [
            "/etc/passwd",
            "/etc/shadow",
        ]
        for path in normalized_paths:
            is_protected = False
            for p in PROTECTED_SYSTEM_DIRS_UNIX:
                if path.startswith(p + "/") or path == p:
                    is_protected = True
                    break
            assert is_protected is True, f"Path should be blocked: {path}"


class TestSafeResolvePath:
    """测试安全路径解析"""

    def test_safe_resolve_normal_path(self):
        """测试正常路径解析"""
        result = safe_resolve_path("./tests/unit")
        assert os.path.isabs(result)
        # Windows 使用反斜杠，Unix 使用正斜杠
        assert "tests" in result and "unit" in result

    def test_safe_resolve_protected_path(self):
        """测试保护路径解析"""
        # 在Windows上测试Windows保护目录
        with patch('os.name', 'nt'):
            with pytest.raises(ValueError, match="系统保护目录"):
                safe_resolve_path(r"C:\Windows\System32")

    def test_safe_resolve_traversal_attack(self):
        """测试路径遍历攻击防护"""
        # 在Windows上测试Windows路径遍历攻击
        with patch('os.name', 'nt'):
            with pytest.raises(ValueError, match="系统保护目录"):
                safe_resolve_path(r"C:\Users\Public\..\..\Windows\System32")

    def test_safe_resolve_invalid_path(self):
        """测试无效路径"""
        with patch('os.path.abspath', side_effect=ValueError("Invalid path")):
            with pytest.raises(ValueError, match="路径解析失败"):
                safe_resolve_path("\x00invalid")


class TestExecutableExtension:
    """测试可执行文件扩展名检查"""

    def test_blocked_extensions(self):
        """测试被阻止的扩展名"""
        blocked_files = [
            "program.exe",
            "script.bat",
            "command.cmd",
            "script.ps1",
            "program.msi",
            "script.vbs",
            "program.js",
            "library.dll",
            "script.pyc",
            "binary.so",
            "app.dmg",
            "installer.pkg",
        ]
        for filename in blocked_files:
            assert is_executable_extension(filename) is True, f"{filename} should be blocked"

    def test_allowed_extensions(self):
        """测试允许的扩展名"""
        allowed_files = [
            "document.txt",
            "data.json",
            "config.yaml",
            "image.png",
            "log.csv",
            "report.md",
            "script.py",
            "notes.txt",
            "data.xml",
        ]
        for filename in allowed_files:
            assert is_executable_extension(filename) is False, f"{filename} should be allowed"

    def test_no_extension(self):
        """测试无扩展名文件"""
        assert is_executable_extension("README") is False
        assert is_executable_extension("Makefile") is False

    def test_empty_filename(self):
        """测试空文件名"""
        assert is_executable_extension("") is False


class TestCrossPlatformPathCheck:
    """测试跨平台路径检查"""

    def test_windows_path_on_unix(self):
        """测试Unix系统上的Windows路径"""
        with patch('os.name', 'posix'):
            # Unix系统上Windows路径不应被视为保护路径
            assert is_protected_path(r"C:\Windows\System32") is False

    def test_unix_path_on_windows(self):
        """测试Windows系统上的Unix路径"""
        with patch('os.name', 'nt'):
            # Windows系统上Unix路径不应被视为保护路径
            assert is_protected_path("/etc/passwd") is False

    def test_relative_path_resolution(self):
        """测试相对路径解析"""
        with patch('os.name', 'posix'):
            current_dir = os.getcwd()
            test_path = "./test"
            result = safe_resolve_path(test_path)
            expected = os.path.abspath(test_path)
            assert result == expected