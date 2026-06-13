# -*- coding: utf-8 -*-
"""
system_tools.py 极端边界条件测试 - Bug 修复 + 异常分支覆盖

本测试文件覆盖以下内容:
1. _browser_instance 状态泄漏 Bug 修复验证
2. process_management 异常分支 (start_process/list_processes/stop_process)
3. pyperclip 缺失回退分支 (get_clipboard/set_clipboard)
"""
import os
import sys
import time
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from agent import system_tools
from agent.system_tools import (
    get_browser,
    browser_close,
    start_process,
    list_processes,
    stop_process,
    get_clipboard,
    set_clipboard,
    _cleanup_browser_instance,
)


class TestBrowserInstanceStateLeakFix:
    """测试 _browser_instance 状态泄漏 Bug 修复 (修复 set_page_load_timeout 失败时的清理)"""

    @pytest.fixture(autouse=True)
    def reset_browser_instance(self):
        """每个测试前重置 _browser_instance"""
        with patch.object(system_tools, '_browser_instance', None):
            yield

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_page_load_timeout_failure_cleans_instance(self):
        """核心 Bug 修复: set_page_load_timeout 失败时 _browser_instance 应被清理为 None"""
        mock_selenium = MagicMock()
        mock_options = MagicMock()
        mock_options.add_argument.return_value = None
        mock_options_module = MagicMock()
        mock_options_module.Options.return_value = mock_options

        mock_chrome_instance = MagicMock()
        # 启动成功, 但 set_page_load_timeout 失败
        mock_chrome_instance.set_page_load_timeout.side_effect = Exception("timeout failed")
        mock_selenium.webdriver.Chrome.return_value = mock_chrome_instance
        mock_selenium.webdriver.chrome = MagicMock()
        mock_selenium.webdriver.chrome.options = mock_options_module

        with patch.dict(sys.modules, {
            'selenium': mock_selenium,
            'selenium.webdriver': mock_selenium.webdriver,
            'selenium.webdriver.chrome': mock_selenium.webdriver.chrome,
            'selenium.webdriver.chrome.options': mock_options_module,
        }):
            with patch('agent.system_tools.logger'):
                result = get_browser()
                # 修复后: 返回 None 且 _browser_instance 已被清理
                assert result is None
                assert system_tools._browser_instance is None, \
                    "Bug: set_page_load_timeout 失败后 _browser_instance 未被清理"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_page_load_timeout_failure_calls_quit(self):
        """测试 set_page_load_timeout 失败时调用 quit 释放资源"""
        mock_selenium = MagicMock()
        mock_options = MagicMock()
        mock_options.add_argument.return_value = None
        mock_options_module = MagicMock()
        mock_options_module.Options.return_value = mock_options

        mock_chrome_instance = MagicMock()
        mock_chrome_instance.set_page_load_timeout.side_effect = Exception("timeout failed")
        mock_selenium.webdriver.Chrome.return_value = mock_chrome_instance
        mock_selenium.webdriver.chrome = MagicMock()
        mock_selenium.webdriver.chrome.options = mock_options_module

        with patch.dict(sys.modules, {
            'selenium': mock_selenium,
            'selenium.webdriver': mock_selenium.webdriver,
            'selenium.webdriver.chrome': mock_selenium.webdriver.chrome,
            'selenium.webdriver.chrome.options': mock_options_module,
        }):
            with patch('agent.system_tools.logger'):
                get_browser()
                # quit 应被调用以释放浏览器资源
                mock_chrome_instance.quit.assert_called()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_page_load_timeout_failure_quit_also_fails(self):
        """测试 set_page_load_timeout 失败且 quit 也失败时仍能清理"""
        mock_selenium = MagicMock()
        mock_options = MagicMock()
        mock_options.add_argument.return_value = None
        mock_options_module = MagicMock()
        mock_options_module.Options.return_value = mock_options

        mock_chrome_instance = MagicMock()
        mock_chrome_instance.set_page_load_timeout.side_effect = Exception("timeout failed")
        mock_chrome_instance.quit.side_effect = Exception("quit also failed")
        mock_selenium.webdriver.Chrome.return_value = mock_chrome_instance
        mock_selenium.webdriver.chrome = MagicMock()
        mock_selenium.webdriver.chrome.options = mock_options_module

        with patch.dict(sys.modules, {
            'selenium': mock_selenium,
            'selenium.webdriver': mock_selenium.webdriver,
            'selenium.webdriver.chrome': mock_selenium.webdriver.chrome,
            'selenium.webdriver.chrome.options': mock_options_module,
        }):
            with patch('agent.system_tools.logger'):
                result = get_browser()
                # 即使 quit 失败, _browser_instance 仍应被清理为 None
                assert result is None
                assert system_tools._browser_instance is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_browser_after_partial_init_creates_new(self):
        """测试部分初始化失败后, 下次 get_browser 调用会创建新实例而非返回损坏实例"""
        mock_selenium = MagicMock()
        mock_options = MagicMock()
        mock_options.add_argument.return_value = None
        mock_options_module = MagicMock()
        mock_options_module.Options.return_value = mock_options

        # 第一次: 启动成功但 set_page_load_timeout 失败
        mock_chrome_first = MagicMock()
        mock_chrome_first.set_page_load_timeout.side_effect = Exception("timeout failed")

        # 第二次: 启动成功且 set_page_load_timeout 成功
        mock_chrome_second = MagicMock()
        mock_chrome_second.set_page_load_timeout.return_value = None
        mock_chrome_second.window_handles = ["h1"]

        # 两次 webdriver.Chrome 调用返回不同实例
        mock_selenium.webdriver.Chrome.side_effect = [mock_chrome_first, mock_chrome_second]
        mock_selenium.webdriver.chrome = MagicMock()
        mock_selenium.webdriver.chrome.options = mock_options_module

        with patch.dict(sys.modules, {
            'selenium': mock_selenium,
            'selenium.webdriver': mock_selenium.webdriver,
            'selenium.webdriver.chrome': mock_selenium.webdriver.chrome,
            'selenium.webdriver.chrome.options': mock_options_module,
        }):
            with patch('agent.system_tools.logger'):
                # 第一次调用: 失败, _browser_instance 应被清理
                result1 = get_browser()
                assert result1 is None
                assert system_tools._browser_instance is None

                # 第二次调用: 修复后会创建新实例, 而非返回已损坏的 mock_chrome_first
                result2 = get_browser()
                assert result2 is mock_chrome_second
                assert result2 is not mock_chrome_first

    @pytest.mark.unit
    @pytest.mark.p0
    def test_general_startup_failure_also_cleans_instance(self):
        """测试 webdriver.Chrome 自身抛异常时也清理 _browser_instance"""
        mock_selenium = MagicMock()
        mock_options = MagicMock()
        mock_options.add_argument.return_value = None
        mock_options_module = MagicMock()
        mock_options_module.Options.return_value = mock_options

        # webdriver.Chrome 直接抛异常
        mock_selenium.webdriver.Chrome.side_effect = Exception("Chrome failed to start")
        mock_selenium.webdriver.chrome = MagicMock()
        mock_selenium.webdriver.chrome.options = mock_options_module

        with patch.dict(sys.modules, {
            'selenium': mock_selenium,
            'selenium.webdriver': mock_selenium.webdriver,
            'selenium.webdriver.chrome': mock_selenium.webdriver.chrome,
            'selenium.webdriver.chrome.options': mock_options_module,
        }):
            with patch('agent.system_tools.logger'):
                result = get_browser()
                assert result is None
                assert system_tools._browser_instance is None


class TestCleanupBrowserInstance:
    """测试 _cleanup_browser_instance 辅助函数"""

    @pytest.fixture(autouse=True)
    def reset_browser_instance(self):
        with patch.object(system_tools, '_browser_instance', None):
            yield

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cleanup_with_none_instance(self):
        """测试清理 None 实例（无操作）"""
        _cleanup_browser_instance()
        assert system_tools._browser_instance is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cleanup_with_valid_instance(self):
        """测试清理有效实例"""
        mock_browser = MagicMock()
        with patch.object(system_tools, '_browser_instance', mock_browser):
            _cleanup_browser_instance()
            mock_browser.quit.assert_called_once()
            assert system_tools._browser_instance is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cleanup_with_quit_exception(self):
        """测试 quit 抛异常时仍清理"""
        mock_browser = MagicMock()
        mock_browser.quit.side_effect = Exception("quit failed")
        with patch.object(system_tools, '_browser_instance', mock_browser):
            _cleanup_browser_instance()
            # 不应抛错, _browser_instance 仍被清理
            assert system_tools._browser_instance is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cleanup_with_no_quit_method(self):
        """测试没有 quit 方法的实例"""
        mock_browser = MagicMock(spec=[])  # 没有 quit 方法
        with patch.object(system_tools, '_browser_instance', mock_browser):
            _cleanup_browser_instance()
            # 不应抛 AttributeError
            assert system_tools._browser_instance is None


class TestStartProcessExceptions:
    """测试 start_process 异常分支 (行 923-924)"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_process_subprocess_popen_exception(self):
        """测试 subprocess.Popen 抛异常"""
        with patch('subprocess.Popen', side_effect=OSError("无法启动进程")):
            result = start_process("notepad.exe")
            assert result["ok"] is False
            assert "无法启动进程" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_process_filenotfound(self):
        """测试程序文件不存在"""
        with patch('subprocess.Popen', side_effect=FileNotFoundError("系统找不到指定的文件")):
            result = start_process("notepad.exe")
            assert result["ok"] is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_process_permission_denied(self):
        """测试权限被拒绝"""
        with patch('subprocess.Popen', side_effect=PermissionError("权限不足")):
            result = start_process("notepad.exe")
            assert result["ok"] is False
            assert "权限" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_process_with_args(self):
        """测试带参数的进程启动"""
        mock_proc = MagicMock()
        mock_proc.pid = 9999
        with patch('subprocess.Popen', return_value=mock_proc) as mock_popen:
            result = start_process("notepad.exe", args=["test.txt"])
            assert result["ok"] is True
            assert result["pid"] == 9999
            # 验证命令包含参数
            call_args = mock_popen.call_args[0][0]
            assert "notepad.exe" in call_args
            assert "test.txt" in call_args

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_process_with_cwd(self):
        """测试自定义工作目录"""
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        with patch('subprocess.Popen', return_value=mock_proc) as mock_popen:
            result = start_process("notepad.exe", cwd="C:\\Windows")
            assert result["ok"] is True
            # 验证 cwd 参数
            assert mock_popen.call_args.kwargs.get("cwd") == "C:\\Windows"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_process_args_none(self):
        """测试 args=None 时不附加参数"""
        mock_proc = MagicMock()
        mock_proc.pid = 5555
        with patch('subprocess.Popen', return_value=mock_proc) as mock_popen:
            result = start_process("notepad.exe", args=None)
            assert result["ok"] is True
            # 命令应只有程序名
            call_args = mock_popen.call_args[0][0]
            assert call_args == ["notepad.exe"]


class TestListProcessesExceptions:
    """测试 list_processes 异常分支 (行 940-941)"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_processes_with_psutil_exception_in_iteration(self):
        """测试 psutil.process_iter 内部每个 proc 抛异常（被内部 except 捕获）"""
        # 构造一个 proc.info 抛异常的迭代器
        mock_proc1 = MagicMock()
        type(mock_proc1).info = PropertyMock(side_effect=Exception("psutil error"))

        mock_proc2 = MagicMock()
        mock_proc2.info = {"pid": 2, "name": "notepad.exe",
                           "create_time": time.time(), "status": "running"}

        with patch('psutil.process_iter', return_value=[mock_proc1, mock_proc2]):
            result = list_processes()
            # 异常的 proc 被跳过, 正常的 proc 被收集
            assert isinstance(result, list)
            assert len(result) == 1
            assert result[0]["name"] == "notepad.exe"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_processes_with_proc_info_exception(self):
        """测试 proc.info 访问抛异常"""
        # 构造一个 proc.info 抛异常的迭代器
        mock_proc1 = MagicMock()
        type(mock_proc1).info = PropertyMock(side_effect=Exception("info error"))

        mock_proc2 = MagicMock()
        mock_proc2.info = {"pid": 2, "name": "notepad.exe",
                           "create_time": time.time(), "status": "running"}

        with patch('psutil.process_iter', return_value=[mock_proc1, mock_proc2]):
            result = list_processes()
            # 异常的 proc 被跳过, 正常的 proc 被收集
            assert len(result) == 1
            assert result[0]["name"] == "notepad.exe"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_processes_with_none_name(self):
        """测试进程名称为 None（被转换为小写字符串）"""
        mock_proc = MagicMock()
        mock_proc.info = {"pid": 3, "name": None,
                          "create_time": time.time(), "status": "running"}
        with patch('psutil.process_iter', return_value=[mock_proc]):
            result = list_processes()
            # name=None 不在白名单中, 不会进入结果
            assert len(result) == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_processes_non_whitelisted_filtered(self):
        """测试非白名单进程被过滤"""
        mock_proc1 = MagicMock()
        mock_proc1.info = {"pid": 1, "name": "notepad.exe",
                           "create_time": time.time(), "status": "running"}
        mock_proc2 = MagicMock()
        mock_proc2.info = {"pid": 2, "name": "malware.exe",
                           "create_time": time.time(), "status": "running"}
        with patch('psutil.process_iter', return_value=[mock_proc1, mock_proc2]):
            result = list_processes()
            # 仅白名单进程返回
            assert len(result) == 1
            assert result[0]["name"] == "notepad.exe"


class TestStopProcessExceptions:
    """测试 stop_process 异常分支 (行 940-941)"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_stop_process_access_denied(self):
        """测试权限拒绝终止进程"""
        import psutil
        mock_proc = MagicMock()
        mock_proc.name.return_value = "notepad.exe"
        mock_proc.terminate.side_effect = psutil.AccessDenied("权限不足")

        with patch('psutil.Process', return_value=mock_proc):
            result = stop_process(1234)
            assert result["ok"] is False
            assert "权限" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_stop_process_zombie_process(self):
        """测试僵尸进程（ZombieProcess 是 NoSuchProcess 的子类, 会被前面捕获）"""
        import psutil
        mock_proc = MagicMock()
        mock_proc.name.return_value = "notepad.exe"
        mock_proc.terminate.side_effect = psutil.ZombieProcess(1234)

        with patch('psutil.Process', return_value=mock_proc):
            result = stop_process(1234)
            # ZombieProcess 是 NoSuchProcess 的子类, 会被 except NoSuchProcess 捕获
            assert result["ok"] is False
            assert "不存在" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_stop_process_timeout(self):
        """测试进程终止超时"""
        import psutil
        mock_proc = MagicMock()
        mock_proc.name.return_value = "notepad.exe"
        mock_proc.terminate.side_effect = psutil.TimeoutExpired("timeout")

        with patch('psutil.Process', return_value=mock_proc):
            result = stop_process(1234)
            assert result["ok"] is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_stop_process_name_none(self):
        """测试进程名称为 None"""
        import psutil
        mock_proc = MagicMock()
        mock_proc.name.return_value = None
        mock_proc.terminate.return_value = None

        with patch('psutil.Process', return_value=mock_proc):
            result = stop_process(1234)
            # name=None 不在白名单中, 拒绝终止
            assert result["ok"] is False
            assert "白名单" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_stop_process_general_exception(self):
        """测试通用异常"""
        import psutil
        with patch('psutil.Process', side_effect=Exception("unknown error")):
            result = stop_process(1234)
            assert result["ok"] is False


class TestGetClipboardPyperclipMissing:
    """测试 get_clipboard 在 pyperclip 缺失时的回退分支 (行 954-988)"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_clipboard_pyperclip_success(self):
        """测试 pyperclip 正常情况"""
        mock_pyperclip = MagicMock()
        mock_pyperclip.paste.return_value = "剪贴板内容"
        with patch.dict(sys.modules, {'pyperclip': mock_pyperclip}):
            with patch('builtins.__import__', side_effect=lambda name, *a, **kw:
                       mock_pyperclip if name == 'pyperclip' else __import__(name, *a, **kw)):
                result = get_clipboard()
                assert result["ok"] is True
                assert result["content"] == "剪贴板内容"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_clipboard_pyperclip_missing_falls_back_to_powershell(self):
        """测试 pyperclip 缺失时回退到 PowerShell"""
        # 模拟 pyperclip ImportError
        original_import = __import__

        def mock_import(name, *args, **kwargs):
            if name == 'pyperclip':
                raise ImportError("No module named 'pyperclip'")
            return original_import(name, *args, **kwargs)

        mock_result = MagicMock()
        mock_result.stdout = "PowerShell clipboard content"
        mock_result.returncode = 0

        with patch('builtins.__import__', side_effect=mock_import):
            with patch('subprocess.run', return_value=mock_result) as mock_run:
                result = get_clipboard()
                # 验证 PowerShell 被调用
                mock_run.assert_called_once()
                assert mock_run.call_args[0][0][0] == "powershell"
                assert result["ok"] is True
                assert "PowerShell" in result["content"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_clipboard_pyperclip_missing_powershell_timeout(self):
        """测试 pyperclip 缺失且 PowerShell 超时"""
        original_import = __import__

        def mock_import(name, *args, **kwargs):
            if name == 'pyperclip':
                raise ImportError("No module named 'pyperclip'")
            return original_import(name, *args, **kwargs)

        import subprocess as sp
        with patch('builtins.__import__', side_effect=mock_import):
            with patch('subprocess.run', side_effect=sp.TimeoutExpired("powershell", 3)):
                result = get_clipboard()
                assert result["ok"] is False
                assert "失败" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_clipboard_pyperclip_missing_powershell_not_found(self):
        """测试 pyperclip 缺失且 powershell 不存在"""
        original_import = __import__

        def mock_import(name, *args, **kwargs):
            if name == 'pyperclip':
                raise ImportError("No module named 'pyperclip'")
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=mock_import):
            with patch('subprocess.run', side_effect=FileNotFoundError("powershell not found")):
                result = get_clipboard()
                assert result["ok"] is False
                assert "失败" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_clipboard_content_truncation(self):
        """测试剪贴板内容截断到 10000 字符"""
        mock_pyperclip = MagicMock()
        long_content = "x" * 15000
        mock_pyperclip.paste.return_value = long_content
        with patch.dict(sys.modules, {'pyperclip': mock_pyperclip}):
            with patch('builtins.__import__', side_effect=lambda name, *a, **kw:
                       mock_pyperclip if name == 'pyperclip' else __import__(name, *a, **kw)):
                result = get_clipboard()
                assert result["ok"] is True
                assert len(result["content"]) == 10000


class TestSetClipboardPyperclipMissing:
    """测试 set_clipboard 在 pyperclip 缺失时的回退分支 (行 1003-1008)"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_clipboard_pyperclip_success(self):
        """测试 pyperclip 正常情况"""
        mock_pyperclip = MagicMock()
        with patch.dict(sys.modules, {'pyperclip': mock_pyperclip}):
            with patch('builtins.__import__', side_effect=lambda name, *a, **kw:
                       mock_pyperclip if name == 'pyperclip' else __import__(name, *a, **kw)):
                result = set_clipboard("hello world")
                assert result["ok"] is True
                mock_pyperclip.copy.assert_called_once_with("hello world")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_clipboard_pyperclip_missing_falls_back_to_powershell(self):
        """测试 pyperclip 缺失时回退到 PowerShell"""
        original_import = __import__

        def mock_import(name, *args, **kwargs):
            if name == 'pyperclip':
                raise ImportError("No module named 'pyperclip'")
            return original_import(name, *args, **kwargs)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch('builtins.__import__', side_effect=mock_import):
            with patch('subprocess.run', return_value=mock_result) as mock_run:
                result = set_clipboard("test content")
                mock_run.assert_called_once()
                # 验证 PowerShell 命令
                call_args = mock_run.call_args[0][0]
                assert call_args[0] == "powershell"
                # 验证 -Value 参数
                cmd_str = " ".join(call_args[2:])
                assert "Set-Clipboard" in cmd_str
                assert "test content" in cmd_str
                assert result["ok"] is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_clipboard_pyperclip_missing_powershell_timeout(self):
        """测试 pyperclip 缺失且 PowerShell 超时"""
        original_import = __import__

        def mock_import(name, *args, **kwargs):
            if name == 'pyperclip':
                raise ImportError("No module named 'pyperclip'")
            return original_import(name, *args, **kwargs)

        import subprocess as sp
        with patch('builtins.__import__', side_effect=mock_import):
            with patch('subprocess.run', side_effect=sp.TimeoutExpired("powershell", 3)):
                result = set_clipboard("hello")
                assert result["ok"] is False
                assert "失败" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_clipboard_too_long(self):
        """测试内容过长被拒绝"""
        long_text = "x" * 60000
        result = set_clipboard(long_text)
        assert result["ok"] is False
        assert "过长" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_clipboard_pyperclip_missing_truncation(self):
        """测试 pyperclip 缺失时 PowerShell 回退截断到 5000 字符"""
        original_import = __import__

        def mock_import(name, *args, **kwargs):
            if name == 'pyperclip':
                raise ImportError("No module named 'pyperclip'")
            return original_import(name, *args, **kwargs)

        mock_result = MagicMock()
        mock_result.returncode = 0

        # 内容长度大于 5000, 应被截断
        long_text = "A" * 10000
        with patch('builtins.__import__', side_effect=mock_import):
            with patch('subprocess.run', return_value=mock_result) as mock_run:
                result = set_clipboard(long_text)
                assert result["ok"] is True
                # 验证命令中只包含 5000 字符
                call_args = mock_run.call_args[0][0]
                cmd_str = " ".join(call_args[2:])
                # 5000 A 字符
                assert cmd_str.count("A") == 5000

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_clipboard_pyperclip_missing_powershell_not_found(self):
        """测试 pyperclip 缺失且 powershell 不存在"""
        original_import = __import__

        def mock_import(name, *args, **kwargs):
            if name == 'pyperclip':
                raise ImportError("No module named 'pyperclip'")
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=mock_import):
            with patch('subprocess.run', side_effect=FileNotFoundError("powershell not found")):
                result = set_clipboard("hello")
                assert result["ok"] is False
                assert "失败" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_clipboard_pyperclip_missing_general_exception(self):
        """测试 pyperclip 缺失时通用异常"""
        original_import = __import__

        def mock_import(name, *args, **kwargs):
            if name == 'pyperclip':
                raise ImportError("No module named 'pyperclip'")
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=mock_import):
            with patch('subprocess.run', side_effect=Exception("unknown error")):
                result = set_clipboard("hello")
                assert result["ok"] is False


class TestClipboardCrossPlatform:
    """测试剪贴板的跨平台行为"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_clipboard_truncation_at_10000(self):
        """测试截断 10000 字符边界"""
        mock_pyperclip = MagicMock()
        # 精确测试 10001 字符 -> 应截断为 10000
        content = "x" * 10001
        mock_pyperclip.paste.return_value = content
        with patch.dict(sys.modules, {'pyperclip': mock_pyperclip}):
            with patch('builtins.__import__', side_effect=lambda name, *a, **kw:
                       mock_pyperclip if name == 'pyperclip' else __import__(name, *a, **kw)):
                result = get_clipboard()
                assert result["ok"] is True
                assert len(result["content"]) == 10000
