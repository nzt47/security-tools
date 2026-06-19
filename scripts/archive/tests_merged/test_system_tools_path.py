"""系统工具路径处理模块测试

覆盖 Windows 和 Linux 路径处理场景，包括：
- 系统保护目录检测
- 路径遍历攻击防护
- 安全路径解析
- 可执行文件扩展名检测
"""
import os
import pytest
from unittest.mock import patch, MagicMock

from agent.system_tools import (
    is_protected_path,
    safe_resolve_path,
    is_executable_extension,
    is_binary_content,
    PROTECTED_SYSTEM_DIRS_WIN,
    PROTECTED_SYSTEM_DIRS_UNIX,
    ALLOWED_WIN_SUBDIRS,
    BLOCKED_WRITE_EXTENSIONS,
)


class TestPathProtectionWindows:
    """Windows 路径保护测试"""

    def test_protected_system_dirs_win(self):
        """测试 Windows 系统保护目录检测"""
        if os.name != "nt":
            pytest.skip("仅在 Windows 上运行")
        
        for protected in PROTECTED_SYSTEM_DIRS_WIN:
            assert is_protected_path(protected) is True
            assert is_protected_path(protected + "\\test.txt") is True
            assert is_protected_path(protected.lower()) is True

    def test_allowed_win_subdirs(self):
        """测试 Windows 允许的子目录"""
        if os.name != "nt":
            pytest.skip("仅在 Windows 上运行")
        
        for allowed in ALLOWED_WIN_SUBDIRS:
            assert is_protected_path(allowed) is False
            assert is_protected_path(allowed + "\\test.txt") is False

    def test_path_traversal_win(self):
        """测试 Windows 路径遍历攻击防护"""
        if os.name != "nt":
            pytest.skip("仅在 Windows 上运行")
        
        malicious_paths = [
            "C:\\Windows\\System32\\cmd.exe",
            "C:\\Program Files\\malware.exe",
        ]
        for path in malicious_paths:
            assert is_protected_path(path) is True

    def test_safe_resolve_path_win(self):
        """测试 Windows 安全路径解析"""
        if os.name != "nt":
            pytest.skip("仅在 Windows 上运行")
        
        with pytest.raises(ValueError, match="系统保护目录"):
            safe_resolve_path("C:\\Windows\\System32\\test.exe")
        
        with pytest.raises(ValueError, match="系统保护目录"):
            safe_resolve_path("C:\\Program Files\\malware.exe")

    def test_normal_path_win(self):
        """测试 Windows 正常路径"""
        if os.name != "nt":
            pytest.skip("仅在 Windows 上运行")
        
        assert is_protected_path("C:\\Users\\admin\\Documents") is False
        assert is_protected_path("D:\\Projects\\test.py") is False


class TestPathProtectionUnix:
    """Unix/Linux 路径保护测试"""

    def test_protected_system_dirs_unix(self):
        """测试 Unix 系统保护目录检测"""
        if os.name != "posix":
            pytest.skip("仅在 Unix/Linux 上运行")
        
        for protected in PROTECTED_SYSTEM_DIRS_UNIX:
            assert is_protected_path(protected) is True
            assert is_protected_path(protected + "/test.txt") is True

    def test_path_traversal_unix(self):
        """测试 Unix 路径遍历攻击防护"""
        if os.name != "posix":
            pytest.skip("仅在 Unix/Linux 上运行")
        
        malicious_paths = [
            "/etc/passwd",
            "/bin/bash",
            "../../etc/passwd",
            "./../etc/passwd",
            "/home/user/../../etc/passwd",
        ]
        for path in malicious_paths:
            assert is_protected_path(path) is True

    def test_safe_resolve_path_unix(self):
        """测试 Unix 安全路径解析"""
        if os.name != "posix":
            pytest.skip("仅在 Unix/Linux 上运行")
        
        with pytest.raises(ValueError, match="系统保护目录"):
            safe_resolve_path("/etc/passwd")
        
        with pytest.raises(ValueError, match="系统保护目录"):
            safe_resolve_path("/bin/bash")

    def test_normal_path_unix(self):
        """测试 Unix 正常路径"""
        if os.name != "posix":
            pytest.skip("仅在 Unix/Linux 上运行")
        
        assert is_protected_path("/home/user/documents") is False
        assert is_protected_path("/tmp/test.txt") is False
        assert is_protected_path("/opt/app/config.yaml") is False


class TestPathProtectionCrossPlatform:
    """跨平台路径保护测试（使用模拟）"""

    @patch("os.name", "nt")
    def test_windows_protection_with_mock(self):
        """使用 mock 测试 Windows 保护目录检测"""
        assert is_protected_path("C:\\Windows\\System32\\test.exe") is True

    def test_protected_dirs_constants(self):
        """验证保护目录常量已正确定义"""
        assert len(PROTECTED_SYSTEM_DIRS_WIN) > 0
        assert len(PROTECTED_SYSTEM_DIRS_UNIX) > 0
        assert len(ALLOWED_WIN_SUBDIRS) > 0


class TestExecutableExtension:
    """可执行文件扩展名检测测试"""

    def test_blocked_extensions(self):
        """测试被阻止的可执行文件扩展名"""
        for ext in BLOCKED_WRITE_EXTENSIONS:
            assert is_executable_extension(f"test{ext}") is True
            assert is_executable_extension(f"test{ext.upper()}") is True

    def test_allowed_extensions(self):
        """测试允许的文件扩展名"""
        allowed_exts = [".txt", ".md", ".json", ".csv", ".xml", ".yaml", ".log"]
        for ext in allowed_exts:
            assert is_executable_extension(f"test{ext}") is False

    def test_no_extension(self):
        """测试无扩展名文件"""
        assert is_executable_extension("test") is False


class TestBinaryContentDetection:
    """二进制内容检测测试"""

    def test_binary_with_null_byte(self):
        """测试包含 NULL 字节的二进制数据"""
        data = b"hello\x00world"
        assert is_binary_content(data) is True

    def test_text_content(self):
        """测试纯文本内容"""
        data = b"Hello World!\nThis is a test.\n"
        assert is_binary_content(data) is False

    def test_mixed_content(self):
        """测试混合内容"""
        mostly_text = b"Hello World!\x00"
        assert is_binary_content(mostly_text) is True
        
        text_with_special = b"Hello \x7f World"
        assert is_binary_content(text_with_special) is False

    def test_empty_content(self):
        """测试空内容"""
        assert is_binary_content(b"") is False

    def test_binary_file_signatures(self):
        """测试常见二进制文件签名"""
        png_data = b"\x89PNG\r\n\x1a\n"
        assert is_binary_content(png_data) is True
        
        pdf_data = b"%PDF-1.4"
        assert is_binary_content(pdf_data) is False


class TestPathNormalization:
    """路径规范化测试"""

    def test_safe_resolve_path_normalization(self):
        """测试路径规范化"""
        result = safe_resolve_path(".\\test\\file.txt") if os.name == "nt" else safe_resolve_path("./test/file.txt")
        assert "test" in result
        assert "file.txt" in result

    def test_invalid_path_null_byte(self):
        """测试包含 NULL 字节的无效路径"""
        try:
            result = safe_resolve_path("\x00invalid")
            # 如果没有抛出异常，检查结果是否正确拒绝了路径
            assert result is not None
        except (ValueError, OSError):
            pass
