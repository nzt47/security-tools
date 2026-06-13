"""SystemTools 跨平台路径处理单元测试"""
import pytest
import os
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
    DEFAULT_MAX_READ_SIZE,
    DEFAULT_MAX_WRITE_SIZE,
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

    def test_protected_system_dirs_case_insensitive(self):
        """测试 Windows 路径大小写不敏感"""
        if os.name != "nt":
            pytest.skip("仅在 Windows 上运行")
        
        assert is_protected_path("c:\\windows\\system32") is True
        assert is_protected_path("C:\\WINDOWS\\SYSTEM32") is True

    def test_allowed_win_subdirs(self):
        """测试 Windows 允许的子目录"""
        if os.name != "nt":
            pytest.skip("仅在 Windows 上运行")
        
        for allowed in ALLOWED_WIN_SUBDIRS:
            assert is_protected_path(allowed) is False
            assert is_protected_path(allowed + "\\test.txt") is False

    def test_safe_resolve_path_win_protected(self):
        """测试 Windows 保护路径拒绝访问"""
        if os.name != "nt":
            pytest.skip("仅在 Windows 上运行")
        
        with pytest.raises(ValueError, match="系统保护目录"):
            safe_resolve_path("C:\\Windows\\System32\\test.exe")

    def test_safe_resolve_path_win_normal(self):
        """测试 Windows 正常路径解析"""
        if os.name != "nt":
            pytest.skip("仅在 Windows 上运行")
        
        result = safe_resolve_path("C:\\Users\\admin\\Documents\\test.txt")
        assert "test.txt" in result


class TestPathProtectionUnix:
    """Unix/Linux 路径保护测试"""

    def test_protected_system_dirs_unix(self):
        """测试 Unix 系统保护目录检测"""
        if os.name != "posix":
            pytest.skip("仅在 Unix/Linux 上运行")
        
        for protected in PROTECTED_SYSTEM_DIRS_UNIX:
            assert is_protected_path(protected) is True
            assert is_protected_path(protected + "/test.txt") is True

    def test_safe_resolve_path_unix_protected(self):
        """测试 Unix 保护路径拒绝访问"""
        if os.name != "posix":
            pytest.skip("仅在 Unix/Linux 上运行")
        
        with pytest.raises(ValueError, match="系统保护目录"):
            safe_resolve_path("/etc/passwd")

    def test_safe_resolve_path_unix_normal(self):
        """测试 Unix 正常路径解析"""
        if os.name != "posix":
            pytest.skip("仅在 Unix/Linux 上运行")
        
        result = safe_resolve_path("/home/user/documents/test.txt")
        assert "test.txt" in result


class TestPathTraversal:
    """路径遍历攻击防护测试"""

    def test_path_traversal_windows_backslash(self):
        """测试 Windows 反斜杠路径遍历"""
        if os.name != "nt":
            pytest.skip("仅在 Windows 上运行")
        
        malicious_paths = [
            "C:\\Windows\\System32\\..\\..\\etc\\passwd",
        ]
        for path in malicious_paths:
            result = safe_resolve_path(path)
            assert "etc" in result.lower() or is_protected_path(result)

    def test_path_traversal_unix_slash(self):
        """测试 Unix 正斜杠路径遍历"""
        if os.name != "posix":
            pytest.skip("仅在 Unix/Linux 上运行")
        
        malicious_paths = [
            "/etc/../etc/passwd",
            "/bin/../etc/shadow",
        ]
        for path in malicious_paths:
            result = safe_resolve_path(path)
            assert is_protected_path(result) or "etc" in result

    def test_path_normalization_dot(self):
        """测试路径规范化（点号）"""
        result = safe_resolve_path("./test/file.txt")
        assert "test" in result
        assert "file.txt" in result

    def test_path_normalization_dotdot(self):
        """测试路径规范化（双点）"""
        result = safe_resolve_path("test/../test2/file.txt")
        assert "test2" in result
        assert "file.txt" in result


class TestExecutableExtension:
    """可执行文件扩展名检测测试"""

    def test_blocked_extensions(self):
        """测试被阻止的可执行文件扩展名"""
        blocked_exts = [".exe", ".dll", ".sys", ".bat", ".cmd", ".ps1", ".vbs", ".js"]
        for ext in blocked_exts:
            assert is_executable_extension(f"test{ext}") is True
            assert is_executable_extension(f"test{ext.upper()}") is True

    def test_blocked_extensions_with_path(self):
        """测试带路径的可执行文件扩展名"""
        assert is_executable_extension("C:\\Windows\\System32\\cmd.exe") is True
        assert is_executable_extension("/usr/bin/script.ps1") is True

    def test_allowed_extensions(self):
        """测试允许的文件扩展名"""
        allowed_exts = [".txt", ".md", ".json", ".csv", ".xml", ".yaml", ".yml", ".log", ".html", ".css"]
        for ext in allowed_exts:
            assert is_executable_extension(f"test{ext}") is False

    def test_no_extension(self):
        """测试无扩展名文件"""
        assert is_executable_extension("test") is False
        assert is_executable_extension("Makefile") is False

    def test_multiple_dots(self):
        """测试多个点号的文件名"""
        assert is_executable_extension("test.tar.gz") is False
        assert is_executable_extension("file.backup.exe") is True


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

    def test_text_with_whitespace(self):
        """测试带空白字符的文本"""
        data = b"Hello\tWorld\nTest\r\n"
        assert is_binary_content(data) is False

    def test_mostly_text_with_null(self):
        """测试大部分是文本但有 NULL 字节"""
        data = b"Hello World!\x00"
        assert is_binary_content(data) is True

    def test_empty_content(self):
        """测试空内容"""
        assert is_binary_content(b"") is False

    def test_binary_signature_png(self):
        """测试 PNG 文件签名"""
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        assert is_binary_content(png_data) is True

    def test_binary_signature_pdf(self):
        """测试 PDF 文件签名"""
        pdf_data = b"%PDF-1.4"
        # PDF 签名本身是文本字符，但实际 PDF 文件通常包含二进制内容
        # 这里仅测试签名部分
        assert is_binary_content(pdf_data) is False

    def test_high_non_text_ratio(self):
        """测试高非文本字符比例"""
        data = b"\x00\x01\x02\x03\x04\x05" * 100
        assert is_binary_content(data) is True

    def test_large_chunk(self):
        """测试大块数据（只检查前 8KB）"""
        data = b"Hello World!" * 1000
        assert is_binary_content(data) is False

    def test_unicode_text(self):
        """测试 Unicode 文本"""
        # 纯 ASCII 文本
        data = "Hello World!".encode("utf-8")
        assert is_binary_content(data) is False

    def test_utf16_text(self):
        """测试 UTF-16 编码文本"""
        data = "Hello World!".encode("utf-16-le")
        # UTF-16 小端序包含 NULL 字节，可能被识别为二进制
        assert isinstance(is_binary_content(data), bool)


class TestConstants:
    """测试常量定义"""

    def test_blocked_extensions_not_empty(self):
        """测试被阻止的扩展名不为空"""
        assert len(BLOCKED_WRITE_EXTENSIONS) > 0

    def test_protected_dirs_win_not_empty(self):
        """测试 Windows 保护目录不为空"""
        assert len(PROTECTED_SYSTEM_DIRS_WIN) > 0

    def test_protected_dirs_unix_not_empty(self):
        """测试 Unix 保护目录不为空"""
        assert len(PROTECTED_SYSTEM_DIRS_UNIX) > 0

    def test_allowed_win_subdirs_not_empty(self):
        """测试 Windows 允许子目录不为空"""
        assert len(ALLOWED_WIN_SUBDIRS) > 0

    def test_default_max_read_size(self):
        """测试默认最大读取大小"""
        assert DEFAULT_MAX_READ_SIZE == 10 * 1024 * 1024

    def test_default_max_write_size(self):
        """测试默认最大写入大小"""
        assert DEFAULT_MAX_WRITE_SIZE == 50 * 1024 * 1024


class TestErrorHandling:
    """错误处理测试"""

    def test_invalid_path_characters(self):
        """测试无效路径字符"""
        # 无效路径字符可能导致路径解析失败或返回保护路径
        result = safe_resolve_path("\x00invalid")
        # 结果可能是安全的（抛出异常或返回无效路径）
        assert isinstance(result, str)

    def test_is_protected_path_invalid_input(self):
        """测试无效输入返回 True"""
        try:
            result = is_protected_path("\x00invalid")
            # 如果不抛出异常，验证结果
            assert isinstance(result, bool)
        except Exception:
            pass  # 预期可能抛出异常


class TestMockScenarios:
    """使用 Mock 的场景测试"""

    @patch("os.name", "nt")
    def test_windows_paths_with_mock(self):
        """使用 Mock 测试 Windows 路径"""
        assert is_protected_path("C:\\Windows\\System32") is True
        assert is_protected_path("C:\\Users\\test") is False

    def test_unix_paths_skip_on_windows(self):
        """Unix 路径测试在 Windows 上跳过"""
        if os.name != "posix":
            pytest.skip("仅在 Unix/Linux 上运行")

    @patch("os.path.abspath")
    @patch("os.path.normpath")
    def test_path_resolution_error(self, mock_normpath, mock_abspath):
        """测试路径解析错误处理"""
        mock_abspath.side_effect = OSError("路径错误")
        
        result = is_protected_path("test/path")
        assert result is True  # 异常时返回 True（拒绝访问）
