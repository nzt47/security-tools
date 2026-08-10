"""系统工具集成测试 — 测试 system_tools.py 中 shell/进程/天气等功能

覆盖范围：
- execute_shell — 正常命令、被阻止的命令、超时、shell 自动检测
- start_process — 白名单程序、不在白名单
- list_processes — 正常列出
- stop_process — 正常终止、PID 无效
- get_weather — 指定城市、格式参数

策略：Mock subprocess/psutil/urllib 调用
"""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock


# ════════════════════════════════════════════════════════════════════════════════
#  Shell 执行测试
# ════════════════════════════════════════════════════════════════════════════════

class TestShellExecute:
    """execute_shell 测试"""

    def test_shell_success(self):
        """正常执行命令"""
        from agent.system_tools import execute_shell
        with patch("agent.system_tools.subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = b"hello world"
            mock_proc.stderr = b""
            mock_run.return_value = mock_proc

            result = execute_shell("echo hello", shell="bash")

        assert result["ok"] is True
        assert result["stdout"] == "hello world"
        assert result["exit_code"] == 0

    def test_shell_failure(self):
        """命令执行失败"""
        from agent.system_tools import execute_shell
        with patch("agent.system_tools.subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_proc.stdout = b""
            mock_proc.stderr = b"error: not found"
            mock_run.return_value = mock_proc

            result = execute_shell("nonexistent_command", shell="bash")

        assert result["ok"] is False
        assert result["exit_code"] == 1

    def test_shell_timeout(self):
        """命令执行超时"""
        from agent.system_tools import execute_shell
        from subprocess import TimeoutExpired

        with patch("agent.system_tools.subprocess.run",
                   side_effect=TimeoutExpired("cmd", 5)):
            result = execute_shell("sleep 100", shell="bash", timeout=5)

        assert result["ok"] is False
        assert "超时" in result.get("error", "")

    def test_shell_empty_command(self):
        """空命令"""
        from agent.system_tools import execute_shell
        result = execute_shell("", shell="bash")
        assert result["ok"] is False
        assert "不能为空" in result.get("error", "")

    def test_shell_invalid_shell_type(self):
        """无效的 shell 类型"""
        from agent.system_tools import execute_shell
        result = execute_shell("echo hello", shell="invalid_shell")
        assert result["ok"] is False
        assert "不支持" in result.get("error", "")

    def test_shell_auto_detect(self):
        """shell 类型自动检测"""
        from agent.system_tools import execute_shell
        with patch("agent.system_tools.subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = b"ok"
            mock_proc.stderr = b""
            mock_run.return_value = mock_proc

            result = execute_shell("echo test", shell="auto")

        assert result["ok"] is True

    def test_shell_output_truncation(self):
        """长输出截断"""
        from agent.system_tools import execute_shell
        long_output = b"x" * 200000  # ~200KB

        with patch("agent.system_tools.subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = long_output
            mock_proc.stderr = b""
            mock_run.return_value = mock_proc

            result = execute_shell("cat large_file", shell="bash")

        assert result["ok"] is True
        # 输出应被截断到约 100KB
        assert len(result.get("stdout", "")) <= 110000


# ════════════════════════════════════════════════════════════════════════════════
#  进程管理测试
# ════════════════════════════════════════════════════════════════════════════════

class TestProcessManagement:
    """start_process / list_processes / stop_process 测试"""

    def test_start_process_whitelisted(self):
        """启动白名单程序"""
        from agent.system_tools import start_process
        with patch("agent.system_tools.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            result = start_process("notepad.exe")

        assert result["ok"] is True
        assert result["pid"] == 12345

    def test_start_process_not_whitelisted(self):
        """启动不在白名单的程序"""
        from agent.system_tools import start_process
        result = start_process("hack.exe")
        assert result["ok"] is False
        assert "白名单" in result.get("error", "")

    def test_list_processes(self):
        """列出进程"""
        from agent.system_tools import list_processes
        mock_proc_1 = MagicMock()
        mock_proc_1.info = {"pid": 100, "name": "notepad.exe", "status": "running"}
        mock_proc_2 = MagicMock()
        mock_proc_2.info = {"pid": 200, "name": "calc.exe", "status": "running"}

        with patch("psutil.process_iter",
                   return_value=[mock_proc_1, mock_proc_2]):
            result = list_processes()

        assert len(result) >= 2
        pids = [p["pid"] for p in result]
        assert 100 in pids
        assert 200 in pids

    def test_list_processes_empty(self):
        """无白名单进程运行"""
        from agent.system_tools import list_processes
        with patch("psutil.process_iter", return_value=[]):
            result = list_processes()
        assert len(result) == 0

    def test_stop_process_success(self):
        """终止进程"""
        from agent.system_tools import stop_process
        mock_proc = MagicMock()
        mock_proc.name.return_value = "notepad.exe"

        with patch("psutil.Process", return_value=mock_proc):
            result = stop_process(12345)

        assert result["ok"] is True
        assert result["pid"] == 12345
        mock_proc.terminate.assert_called_once()

    def test_stop_process_not_found(self):
        """终止不存在的进程"""
        from agent.system_tools import stop_process
        import psutil
        with patch("psutil.Process",
                   side_effect=psutil.NoSuchProcess(99999)):
            result = stop_process(99999)
        assert result["ok"] is False
        assert "不存在" in result.get("error", "")

    def test_stop_process_not_whitelisted(self):
        """终止不在白名单的进程"""
        from agent.system_tools import stop_process
        mock_proc = MagicMock()
        mock_proc.name.return_value = "unknown.exe"

        with patch("psutil.Process", return_value=mock_proc):
            result = stop_process(12345)
        assert result["ok"] is False
        assert "白名单" in result.get("error", "")


# ════════════════════════════════════════════════════════════════════════════════
#  白名单管理测试
# ════════════════════════════════════════════════════════════════════════════════

class TestWhitelist:
    """进程白名单管理测试"""

    def test_default_whitelist_contains_common_programs(self):
        """默认白名单包含常见程序"""
        from agent.system_tools import get_process_whitelist
        wl = get_process_whitelist()
        assert "notepad.exe" in wl
        assert "calc.exe" in wl
        assert "python.exe" in wl

    def test_add_custom_whitelist_entry(self):
        """添加自定义白名单条目"""
        from agent.system_tools import add_whitelist_entry
        with patch("agent.system_tools._save_custom_whitelist") as mock_save:
            result = add_whitelist_entry("myapp.exe")
        assert result["ok"] is True

    def test_add_duplicate_default_entry(self):
        """添加已在默认白名单的条目"""
        from agent.system_tools import add_whitelist_entry
        result = add_whitelist_entry("notepad.exe")
        assert result["ok"] is False
        assert "已在默认" in result.get("error", "")


# ════════════════════════════════════════════════════════════════════════════════
#  天气查询测试
# ════════════════════════════════════════════════════════════════════════════════

class FakeResponse:
    """模拟 urllib.response — 支持上下文管理器协议"""
    def __init__(self, data):
        self._data = data if isinstance(data, bytes) else data.encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return self._data


class TestWeather:

    def test_get_weather_text_format(self):
        """文本格式查询天气"""
        from agent.system_tools import get_weather

        data = b"Beijing: \xe2\x98\x80\xef\xb8\x8f +25\xc2\xb0C"
        with patch("urllib.request.urlopen", return_value=FakeResponse(data)):
            result = get_weather("Beijing", format="text")

        assert result["ok"] is True
        assert result["city"] == "Beijing"
        assert result["format"] == "text"

    def test_get_weather_json_format(self):
        """JSON 格式查询天气"""
        from agent.system_tools import get_weather
        import json

        weather_data = {"current_condition": [{"temp_C": "25", "humidity": "60"}]}
        json_bytes = json.dumps(weather_data).encode("utf-8")

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return json_bytes

        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            result = get_weather("Shanghai", format="json")

        assert result["ok"] is True
        assert result["format"] == "json"
        assert isinstance(result["data"], dict)
        assert result["data"]["current_condition"][0]["temp_C"] == "25"

    def test_get_weather_full_format(self):
        """完整文本预报格式"""
        from agent.system_tools import get_weather

        data = b"Weather forecast for Tokyo..."
        with patch("urllib.request.urlopen", return_value=FakeResponse(data)):
            result = get_weather("Tokyo", format="full")

        assert result["ok"] is True
        assert result["format"] == "full"

    def test_get_weather_auto_city(self):
        """自动 IP 定位城市"""
        from agent.system_tools import get_weather

        with patch("urllib.request.urlopen", return_value=FakeResponse(b"Auto City: +20\xc2\xb0C")):
            result = get_weather(format="text")

        assert result["ok"] is True
        assert result["city"] == "" or result["city"] == "auto"

    def test_get_weather_network_error(self):
        """网络错误处理"""
        from agent.system_tools import get_weather
        import urllib.error

        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("Name or service not known")):
            result = get_weather("UnknownCity")

        assert result["ok"] is False
        assert "网络" in result.get("error", "") or "失败" in result.get("error", "")
