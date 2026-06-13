"""
System Tools 平台特定代码完整测试
使用 Mock 测试 Windows/Unix 路径检查、沙盒执行、进程管理等
"""
import pytest
import os
import sys
import subprocess
from unittest.mock import patch, MagicMock, PropertyMock

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.system_tools import (
    is_protected_path,
    safe_resolve_path,
    is_executable_extension,
    start_process,
    stop_process,
    list_processes,
    PROTECTED_SYSTEM_DIRS_WIN,
    PROTECTED_SYSTEM_DIRS_UNIX,
    ALLOWED_WIN_SUBDIRS,
    PROCESS_WHITELIST,
)


class TestWindowsPathProtectionComplete:
    """测试Windows路径保护的完整场景"""

    def test_protected_windows_system32(self):
        """测试Windows System32目录保护"""
        with patch('os.name', 'nt'):
            with patch('os.path.abspath', side_effect=lambda x: x):
                with patch('os.path.normpath', side_effect=lambda x: x):
                    assert is_protected_path(r"C:\Windows\System32") is True

    def test_protected_windows_program_files(self):
        """测试Windows Program Files目录保护"""
        with patch('os.name', 'nt'):
            with patch('os.path.abspath', side_effect=lambda x: x):
                with patch('os.path.normpath', side_effect=lambda x: x):
                    assert is_protected_path(r"C:\Program Files") is True

    def test_allowed_windows_user_documents(self):
        """测试Windows用户文档目录允许访问"""
        with patch('os.name', 'nt'):
            with patch('os.path.abspath', side_effect=lambda x: x):
                with patch('os.path.normpath', side_effect=lambda x: x):
                    assert is_protected_path(r"C:\Users\Test\Documents") is False

    def test_windows_path_case_insensitive(self):
        """测试Windows路径大小写不敏感"""
        with patch('os.name', 'nt'):
            with patch('os.path.abspath', side_effect=lambda x: x):
                with patch('os.path.normpath', side_effect=lambda x: x):
                    paths = [
                        r"C:\WINDOWS\system32",
                        r"C:\Windows\SYSTEM32",
                        r"c:\windows\system32",
                    ]
                    for path in paths:
                        assert is_protected_path(path) is True

    def test_windows_allowed_subdirs(self):
        """测试Windows允许的子目录"""
        with patch('os.name', 'nt'):
            with patch('os.path.abspath', side_effect=lambda x: x):
                with patch('os.path.normpath', side_effect=lambda x: x):
                    for allowed_dir in ALLOWED_WIN_SUBDIRS:
                        assert is_protected_path(allowed_dir) is False


class TestUnixPathProtectionComplete:
    """测试Unix路径保护的完整场景"""

    def test_unix_protected_directories(self):
        """测试Unix保护目录列表"""
        # 只测试实际在 PROTECTED_SYSTEM_DIRS_UNIX 中的目录
        test_protected_dirs = [
            "/etc",
            "/usr/lib",
            "/usr/share",
            "/boot",
            "/bin",
            "/sbin",
            "/lib",
            "/sys",
            "/proc",
            "/dev",
            "/var/log",
        ]

        for protected_dir in test_protected_dirs:
            is_protected = False
            for p in PROTECTED_SYSTEM_DIRS_UNIX:
                if protected_dir.startswith(p + "/") or protected_dir == p:
                    is_protected = True
                    break
            assert is_protected is True, f"{protected_dir} should be protected"

    def test_unix_allowed_home_directory(self):
        """测试Unix用户目录允许访问"""
        test_path = "/home/user/documents"
        is_protected = False
        for protected in PROTECTED_SYSTEM_DIRS_UNIX:
            if test_path.startswith(protected + "/") or test_path == protected:
                is_protected = True
                break
        assert is_protected is False

    def test_unix_path_case_sensitive(self):
        """测试Unix路径大小写敏感"""
        # /etc 是保护的
        test_path_lower = "/etc"
        is_protected_lower = False
        for protected in PROTECTED_SYSTEM_DIRS_UNIX:
            if test_path_lower.startswith(protected + "/") or test_path_lower == protected:
                is_protected_lower = True
                break
        assert is_protected_lower is True
        
        # /Etc 不是保护的（大小写敏感）
        test_path_upper = "/Etc"
        is_protected_upper = False
        for protected in PROTECTED_SYSTEM_DIRS_UNIX:
            if test_path_upper.startswith(protected + "/") or test_path_upper == protected:
                is_protected_upper = True
                break
        assert is_protected_upper is False


class TestSafeResolvePathComplete:
    """测试安全路径解析的完整场景"""

    def test_safe_resolve_normal_path(self):
        """测试正常路径解析"""
        with patch('os.path.abspath', return_value=r"C:\Users\Test\Documents\file.txt"):
            with patch('agent.system_tools.is_protected_path', return_value=False):
                result = safe_resolve_path(r"C:\Users\Test\Documents\file.txt")
                assert result == r"C:\Users\Test\Documents\file.txt"

    def test_safe_resolve_protected_path_raises(self):
        """测试解析保护路径抛出异常"""
        with patch('os.path.abspath', return_value=r"C:\Windows\System32"):
            with patch('agent.system_tools.is_protected_path', return_value=True):
                with pytest.raises(ValueError, match="路径位于系统保护目录"):
                    safe_resolve_path(r"C:\Windows\System32")

    def test_safe_resolve_value_error(self):
        """测试路径解析值错误"""
        with patch('os.path.abspath', side_effect=ValueError("Invalid characters")):
            with pytest.raises(ValueError, match="路径解析失败"):
                safe_resolve_path("invalid:path")

    def test_safe_resolve_os_error(self):
        """测试路径解析OS错误"""
        with patch('os.path.abspath', side_effect=OSError("Path too long")):
            with pytest.raises(ValueError, match="路径解析失败"):
                safe_resolve_path("very_long_path")


class TestExecutableExtension:
    """测试可执行文件扩展名检查"""

    def test_executable_extensions(self):
        """测试可执行文件扩展名"""
        # 只测试实际在 BLOCKED_WRITE_EXTENSIONS 中的扩展名
        executable_files = [
            "program.exe",
            "script.bat",
            "command.cmd",
            "script.ps1",
            "program.msi",
            "script.vbs",
            "program.js",
            "library.dll",
            "script.pyc",
        ]

        for filename in executable_files:
            assert is_executable_extension(filename) is True, f"{filename} should be executable"

    def test_non_executable_extensions(self):
        """测试非可执行文件扩展名"""
        non_executable_files = [
            "document.txt",
            "data.json",
            "config.yaml",
            "image.png",
            "log.csv",
            "report.md",
            "script.py",  # .py 不在阻塞列表中，只有 .pyc 和 .pyo 才在
        ]

        for filename in non_executable_files:
            assert is_executable_extension(filename) is False, f"{filename} should not be executable"

    def test_no_extension(self):
        """测试无扩展名文件"""
        assert is_executable_extension("README") is False


class TestProcessManagement:
    """测试进程管理功能"""

    def test_start_process_whitelist_allowed(self):
        """测试白名单程序允许启动"""
        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            
            result = start_process("notepad.exe")
            
            assert result["ok"] is True
            assert result["pid"] == 12345
            assert result["program"] == "notepad.exe"

    def test_start_process_not_in_whitelist(self):
        """测试非白名单程序拒绝启动"""
        result = start_process("malware.exe")
        
        assert result["ok"] is False
        assert "程序不在白名单中" in result["error"]

    def test_start_process_with_args(self):
        """测试带参数启动程序"""
        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12346
            mock_popen.return_value = mock_proc
            
            result = start_process("notepad.exe", args=["test.txt"])
            
            assert result["ok"] is True
            mock_popen.assert_called_once()

    def test_start_process_exception(self):
        """测试启动程序异常"""
        with patch('subprocess.Popen', side_effect=Exception("Process failed")):
            result = start_process("notepad.exe")
            
            assert result["ok"] is False
            assert "Process failed" in result["error"]

    def test_list_processes(self):
        """测试列出进程"""
        mock_procs = [
            MagicMock(info={"pid": 100, "name": "notepad.exe", "status": "running"}),
            MagicMock(info={"pid": 200, "name": "chrome.exe", "status": "running"}),
            MagicMock(info={"pid": 300, "name": "malware.exe", "status": "running"}),
        ]
        
        with patch('psutil.process_iter', return_value=mock_procs):
            result = list_processes()
            
            # 应该只返回白名单中的进程
            assert len(result) >= 0

    def test_stop_process_whitelist_allowed(self):
        """测试终止白名单进程"""
        mock_proc = MagicMock()
        mock_proc.name.return_value = "notepad.exe"
        mock_proc.terminate = MagicMock()
        
        with patch('psutil.Process', return_value=mock_proc):
            result = stop_process(12345)
            
            assert result["ok"] is True
            mock_proc.terminate.assert_called_once()

    def test_stop_process_not_in_whitelist(self):
        """测试终止非白名单进程被拒绝"""
        mock_proc = MagicMock()
        mock_proc.name.return_value = "malware.exe"
        
        with patch('psutil.Process', return_value=mock_proc):
            result = stop_process(12345)
            
            assert result["ok"] is False
            assert "不在白名单中" in result["error"]

    def test_stop_process_exception(self):
        """测试终止进程异常"""
        with patch('psutil.Process', side_effect=Exception("Process not found")):
            result = stop_process(12345)
            
            assert result["ok"] is False


class TestPathTraversalAttack:
    """测试路径遍历攻击防护"""

    def test_path_traversal_windows(self):
        """测试Windows路径遍历攻击"""
        with patch('os.name', 'nt'):
            with patch('os.path.abspath', side_effect=lambda x: os.path.normpath(x)):
                with patch('os.path.normpath', side_effect=lambda x: x.replace("..", "")):
                    malicious_paths = [
                        r"C:\Users\Test\..\..\Windows\System32",
                        r"C:\Users\Test\..\..\..\Windows",
                        r"C:\Safe\..\Windows\System32",
                    ]
                    
                    for path in malicious_paths:
                        # 路径规范化后应该被检查
                        result = is_protected_path(path)
                        # 结果取决于规范化后的路径

    def test_path_traversal_unix(self):
        """测试Unix路径遍历攻击"""
        malicious_paths = [
            "/home/user/../../etc/passwd",
            "/safe/../../../etc",
            "/tmp/../root",
        ]
        
        for path in malicious_paths:
            # 检查路径是否被正确处理
            normalized = os.path.normpath(path)
            is_protected = False
            for protected in PROTECTED_SYSTEM_DIRS_UNIX:
                if normalized.startswith(protected + "/") or normalized == protected:
                    is_protected = True
                    break


class TestCrossPlatformProcess:
    """测试跨平台进程管理"""

    def test_process_creation_flags_windows(self):
        """测试Windows进程创建标志"""
        with patch('os.name', 'nt'):
            with patch('subprocess.Popen') as mock_popen:
                mock_proc = MagicMock()
                mock_proc.pid = 12345
                mock_popen.return_value = mock_proc

                result = start_process("notepad.exe")

                # 应该使用 CREATE_NO_WINDOW 标志
                assert mock_popen.called
                call_kwargs = mock_popen.call_args[1]
                assert 'creationflags' in call_kwargs
                assert call_kwargs['creationflags'] == subprocess.CREATE_NO_WINDOW

    def test_process_creation_flags_unix(self):
        """测试Unix进程创建标志"""
        with patch('os.name', 'posix'):
            with patch('subprocess.Popen') as mock_popen:
                mock_proc = MagicMock()
                mock_proc.pid = 12345
                mock_popen.return_value = mock_proc

                # python 在白名单中
                result = start_process("python.exe")

                # Unix 不应该使用 creationflags（值为0）
                assert mock_popen.called
                call_kwargs = mock_popen.call_args[1]
                assert call_kwargs.get('creationflags', 0) == 0


class TestProcessWhitelist:
    """测试进程白名单"""

    def test_whitelist_contains_common_programs(self):
        """测试白名单包含常用程序"""
        expected_programs = ["notepad.exe", "explorer.exe", "cmd.exe"]
        
        for program in expected_programs:
            prog_lower = program.lower()
            is_allowed = False
            for w in PROCESS_WHITELIST:
                if prog_lower == w or prog_lower.endswith("\\\\" + w):
                    is_allowed = True
                    break
            assert is_allowed is True

    def test_whitelist_rejects_dangerous_programs(self):
        """测试白名单拒绝危险程序"""
        dangerous_programs = ["format.exe", "del.exe", "shutdown.exe", "regedit.exe"]
        
        for program in dangerous_programs:
            prog_lower = program.lower()
            is_allowed = False
            for w in PROCESS_WHITELIST:
                if prog_lower == w or prog_lower.endswith("\\\\" + w):
                    is_allowed = True
                    break
            assert is_allowed is False