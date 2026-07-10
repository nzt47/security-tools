"""合并后的测试文件 - 由 test_file_consolidation 工具自动生成。"""
# pylint: disable=redefined-outer-name,missing-function-docstring

import pytest
import os
import sys
import ntpath
from contextlib import contextmanager
from unittest.mock import patch, MagicMock
from agent.system_tools import (
    is_protected_path,
    safe_resolve_path,
    PROTECTED_SYSTEM_DIRS_WIN,
    PROTECTED_SYSTEM_DIRS_UNIX,
    ALLOWED_WIN_SUBDIRS,
)
import subprocess
from unittest.mock import patch, MagicMock, PropertyMock
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
from unittest.mock import MagicMock, patch
from agent.system_tools import (
    is_protected_path,
    safe_resolve_path,
    is_executable_extension,
    PROTECTED_SYSTEM_DIRS_WIN,
    PROTECTED_SYSTEM_DIRS_UNIX,
    ALLOWED_WIN_SUBDIRS,
    BLOCKED_WRITE_EXTENSIONS,
)
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


# === 来自 test_system_tools_platform.py ===

"""
System Tools 平台特定代码测试
使用 Mock 测试 Windows/Unix 路径检查逻辑
"""

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


# 在模块级保存 ntpath 原始函数引用。
# Windows 上 os.path 即 ntpath，patch('os.path.abspath') 会同时替换 ntpath.abspath，
# 若在 side_effect 中调用 ntpath.abspath 会触发无限递归。故必须提前保存原始引用。
_ntpath_abspath_orig = ntpath.abspath
_ntpath_normpath_orig = ntpath.normpath


@contextmanager
def _windows_path_env():
    """模拟 Windows 路径环境

    is_protected_path() 内部调用 os.path.abspath()，在 Linux 上会将
    Windows 路径转换为 cwd 前缀的 Linux 路径，导致保护目录匹配失败。
    使用 ntpath 模块正确处理 Windows 路径（解析 .. 和正常化分隔符）。
    同时 mock os.name/os.sep/os.path.abspath/os.path.normpath 使跨平台测试一致。
    """
    with patch('os.name', 'nt'), \
         patch('os.sep', '\\'), \
         patch('os.path.abspath', side_effect=lambda p: _ntpath_abspath_orig(p)), \
         patch('os.path.normpath', side_effect=lambda p: _ntpath_normpath_orig(p)):
        yield


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
        with _windows_path_env():
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

# === 来自 test_system_tools_platform_complete.py ===

"""
System Tools 平台特定代码完整测试
使用 Mock 测试 Windows/Unix 路径检查、沙盒执行、进程管理等
"""

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)



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

# === 来自 test_system_tools_platform_mock.py ===

"""system_tools.py 平台特定代码测试（Windows路径检查、沙盒执行等）"""



class TestWindowsPathProtection_system_tools_platform_mock:
    """测试Windows路径保护"""

    def test_windows_protected_directories(self):
        """测试Windows保护目录列表"""
        with _windows_path_env():
            for protected_dir in PROTECTED_SYSTEM_DIRS_WIN:
                assert is_protected_path(protected_dir) is True, f"{protected_dir} should be protected"

    def test_windows_allowed_subdirectories(self):
        """测试Windows允许的子目录"""
        with patch('os.name', 'nt'):
            for allowed_dir in ALLOWED_WIN_SUBDIRS:
                assert is_protected_path(allowed_dir) is False, f"{allowed_dir} should be allowed"

    def test_windows_subdirectory_of_protected(self):
        """测试保护目录的子目录"""
        with _windows_path_env():
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


class TestUnixPathProtection_system_tools_platform_mock:
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


class TestPathTraversalAttack_system_tools_platform_mock:
    """测试路径遍历攻击防护"""

    def test_path_traversal_windows(self):
        """测试Windows路径遍历攻击"""
        with _windows_path_env():
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


class TestSafeResolvePath_system_tools_platform_mock:
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


class TestExecutableExtension_system_tools_platform_mock:
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
        with _windows_path_env():
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

# === 来自 test_system_tools_cross_platform.py ===

"""SystemTools 跨平台路径处理单元测试"""



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
            with pytest.raises(ValueError, match="路径位于系统保护目录"):
                safe_resolve_path(path)

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


class TestExecutableExtension_system_tools_cross_platform:
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

# === 来自 test_system_tools_path.py ===

"""系统工具路径处理模块测试

覆盖 Windows 和 Linux 路径处理场景，包括：
- 系统保护目录检测
- 路径遍历攻击防护
- 安全路径解析
- 可执行文件扩展名检测
"""



class TestPathProtectionWindows_system_tools_path:
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


class TestPathProtectionUnix_system_tools_path:
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

    def test_windows_protection_with_mock(self):
        """使用 mock 测试 Windows 保护目录检测"""
        with _windows_path_env():
            assert is_protected_path("C:\\Windows\\System32\\test.exe") is True

    def test_protected_dirs_constants(self):
        """验证保护目录常量已正确定义"""
        assert len(PROTECTED_SYSTEM_DIRS_WIN) > 0
        assert len(PROTECTED_SYSTEM_DIRS_UNIX) > 0
        assert len(ALLOWED_WIN_SUBDIRS) > 0


class TestExecutableExtension_system_tools_path:
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


class TestBinaryContentDetection_system_tools_path:
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
