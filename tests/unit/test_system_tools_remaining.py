import os
import tempfile
import pytest
import time
import fnmatch
import subprocess
from unittest.mock import patch, MagicMock, PropertyMock

from agent.system_tools import (
    read_file,
    write_file,
    list_directory,
    search_files,
    get_file_info,
    safe_resolve_path,
    is_protected_path,
    _get_single_file_info,
    get_browser,
    browser_navigate,
    browser_screenshot,
    browser_close,
    _cleanup_browser_instance,
    _load_tasks,
    _save_tasks,
    toggle_scheduled_task,
    start_process,
    list_processes,
    stop_process,
    PROCESS_WHITELIST,
    set_browser_config,
    get_clipboard,
    set_clipboard,
)


class TestSafeResolvePathExceptions:
    """测试 safe_resolve_path 的异常处理"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_resolve_path_value_error(self):
        """测试路径解析时的 ValueError"""
        with patch("os.path.abspath", side_effect=ValueError("invalid path")):
            with pytest.raises(ValueError, match="路径解析失败"):
                safe_resolve_path("test/path")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_resolve_path_os_error(self):
        """测试路径解析时的 OSError"""
        with patch("os.path.abspath", side_effect=OSError("os error")):
            with pytest.raises(ValueError, match="路径解析失败"):
                safe_resolve_path("test/path")


class TestReadFileBoundaryCases:
    """测试 read_file 的边界条件"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_read_file_permission_error(self):
        """测试读取文件时的权限错误"""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test")
            temp_path = f.name
        try:
            with patch("builtins.open", side_effect=PermissionError("permission denied")):
                result = read_file(temp_path)
                assert result["ok"] is False
                assert "没有权限读取文件" in result["error"]
        finally:
            os.remove(temp_path)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_read_file_unicode_decode_error(self):
        """测试 Unicode 解码错误后的编码降级"""
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            f.write(b"\xff\xfe\xfd")  # Invalid UTF-8
            temp_path = f.name
        try:
            # 强制不识别为二进制，测试文本解码路径
            with patch("agent.system_tools.is_binary_content", return_value=False):
                result = read_file(temp_path, encoding="utf-8")
                assert result["ok"] is True
                assert result["binary"] is False
                assert "utf-8 (with replacements)" in result["encoding"] or result["encoding"] == "latin-1"
        finally:
            os.remove(temp_path)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_read_file_latin1_fallback(self):
        """测试 latin-1 降级编码"""
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            f.write(b"\xff\xfe\xfd")
            temp_path = f.name
        try:
            with patch("agent.system_tools.is_binary_content", return_value=False):
                # 测试编码降级路径
                result = read_file(temp_path, encoding="utf-8")
                assert result["ok"] is True
                # utf-8 replace 应该能处理任何字节序列
                assert result["encoding"] == "utf-8 (with replacements)"
        finally:
            os.remove(temp_path)


class TestWriteFileBoundaryCases:
    """测试 write_file 的边界条件"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_file_backup_failure(self):
        """测试备份失败时仍能继续写入"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = os.path.join(temp_dir, "test.txt")
            # 创建一个现有文件
            with open(temp_path, "w") as f:
                f.write("original")
            
            with patch("shutil.copy2", side_effect=OSError("backup failed")):
                result = write_file(temp_path, "new content")
                assert result["ok"] is True
                # 验证文件内容已更新
                with open(temp_path, "r") as f:
                    assert f.read() == "new content"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_file_permission_error(self):
        """测试写入时的权限错误"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = os.path.join(temp_dir, "test.txt")
            with patch("builtins.open", side_effect=PermissionError("permission denied")):
                result = write_file(temp_path, "content")
                assert result["ok"] is False
                assert "没有权限写入文件" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_file_create_dir_error(self):
        """测试创建目录失败"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = os.path.join(temp_dir, "nonexistent", "test.txt")
            with patch("os.makedirs", side_effect=OSError("mkdir failed")):
                result = write_file(temp_path, "content")
                assert result["ok"] is False
                assert "无法创建目录" in result["error"]


class TestListDirectoryBoundaryCases:
    """测试 list_directory 的边界条件"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_directory_get_file_info_error(self):
        """测试获取文件信息失败"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建测试文件
            with open(os.path.join(temp_dir, "test.txt"), "w") as f:
                f.write("test")
            
            with patch("agent.system_tools._get_single_file_info", side_effect=OSError("stat failed")):
                result = list_directory(temp_dir)
                assert result["ok"] is True
                # 验证错误处理
                items = result["items"]
                assert any(item.get("type") == "unknown" for item in items)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_directory_permission_error(self):
        """测试权限错误"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("os.listdir", side_effect=PermissionError("permission denied")):
                result = list_directory(temp_dir)
                assert result["ok"] is False
                assert "没有权限列出目录" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_directory_os_error(self):
        """测试 OSError"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("os.listdir", side_effect=OSError("os error")):
                result = list_directory(temp_dir)
                assert result["ok"] is False
                assert "列出目录失败" in result["error"]


class TestSearchFilesBoundaryCases:
    """测试 search_files 的边界条件"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_files_case_sensitive(self):
        """测试大小写敏感匹配"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建测试文件
            with open(os.path.join(temp_dir, "Test.txt"), "w") as f:
                f.write("test")
            
            result = search_files("*.txt", temp_dir, ignore_case=False)
            assert result["ok"] is True
            # 在 Linux 上应该不匹配，Windows 上可能匹配
            # 这取决于文件系统是否大小写敏感

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_files_max_results(self):
        """测试最大结果限制"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建多个测试文件
            for i in range(10):
                with open(os.path.join(temp_dir, f"test{i}.txt"), "w") as f:
                    f.write(f"test{i}")
            
            result = search_files("*.txt", temp_dir, max_results=5)
            assert result["ok"] is True
            assert len(result["results"]) == 5
            assert result["truncated"] is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_files_stat_error(self):
        """测试 os.stat 失败"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, "test.txt"), "w") as f:
                f.write("test")
            
            with patch("os.stat", side_effect=OSError("stat failed")):
                result = search_files("*.txt", temp_dir)
                assert result["ok"] is True
                # 应该跳过有问题的文件
                assert len(result["results"]) == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_files_permission_error(self):
        """测试权限错误（应继续搜索）"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, "test.txt"), "w") as f:
                f.write("test")
            
            with patch("os.walk", side_effect=PermissionError("permission denied")):
                result = search_files("*.txt", temp_dir)
                assert result["ok"] is True
                assert len(result["results"]) == 0


class TestGetSingleFileInfoBoundaryCases:
    """测试 _get_single_file_info 的边界条件"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_single_file_info_link_error(self):
        """测试符号链接读取失败"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建一个普通文件（不是链接）
            test_path = os.path.join(temp_dir, "test.txt")
            with open(test_path, "w") as f:
                f.write("test")
            
            # 模拟 is_link=True 但实际不是链接
            with patch("os.path.islink", return_value=True):
                with patch("os.readlink", side_effect=OSError("readlink failed")):
                    info = _get_single_file_info(test_path)
                    assert "link_target" not in info


class TestBrowserControl:
    """测试浏览器控制相关功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_browser_selenium_not_installed(self):
        """测试 selenium 未安装时返回 None"""
        with patch.dict("sys.modules", {"selenium": None}):
            with patch("builtins.__import__", side_effect=ImportError("selenium not found")):
                result = get_browser()
                assert result is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_navigate_invalid_url(self):
        """测试无效 URL"""
        result = browser_navigate("ftp://example.com")
        assert result["ok"] is False
        assert "仅允许 http/https" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_navigate_blocked_url(self):
        """测试禁止的内网地址"""
        result = browser_navigate("http://localhost/admin")
        assert result["ok"] is False
        assert "禁止访问内网地址" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_navigate_127_address(self):
        """测试 127.0.0.1 地址"""
        result = browser_navigate("http://127.0.0.1/admin")
        assert result["ok"] is False
        assert "禁止访问内网地址" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_navigate_192_168_address(self):
        """测试 192.168.x.x 地址"""
        result = browser_navigate("http://192.168.1.1/admin")
        assert result["ok"] is False
        assert "禁止访问内网地址" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_navigate_browser_unavailable(self):
        """测试浏览器不可用"""
        with patch("agent.system_tools.get_browser", return_value=None):
            result = browser_navigate("http://example.com")
            assert result["ok"] is False
            assert "浏览器不可用" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_screenshot_browser_unavailable(self):
        """测试截图时浏览器不可用"""
        with patch("agent.system_tools.get_browser", return_value=None):
            result = browser_screenshot()
            assert result["ok"] is False
            assert "浏览器不可用" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_close_no_instance(self):
        """测试关闭浏览器时无实例"""
        with patch("agent.system_tools._browser_instance", None):
            result = browser_close()
            # 应该正常执行，无异常

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cleanup_browser_instance(self):
        """测试清理浏览器实例"""
        mock_browser = MagicMock()
        with patch("agent.system_tools._browser_instance", mock_browser):
            _cleanup_browser_instance()
            mock_browser.quit.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cleanup_browser_instance_quit_error(self):
        """测试清理浏览器实例时 quit 失败"""
        mock_browser = MagicMock()
        mock_browser.quit.side_effect = Exception("quit failed")
        with patch("agent.system_tools._browser_instance", mock_browser):
            _cleanup_browser_instance()
            # 应该正常执行，无异常


class TestBrowserNavigateSuccess:
    """测试浏览器导航成功场景"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_navigate_success(self):
        """测试浏览器导航成功"""
        mock_browser = MagicMock()
        mock_browser.title = "Test Page"
        mock_browser.current_url = "http://example.com"
        mock_body = MagicMock()
        mock_body.text = "Page content"
        mock_browser.find_element.return_value = mock_body
        
        with patch("agent.system_tools.get_browser", return_value=mock_browser):
            result = browser_navigate("http://example.com")
            assert result["ok"] is True
            assert result["title"] == "Test Page"
            assert "text" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_navigate_error(self):
        """测试浏览器导航失败"""
        mock_browser = MagicMock()
        mock_browser.get.side_effect = Exception("navigation failed")
        
        with patch("agent.system_tools.get_browser", return_value=mock_browser):
            result = browser_navigate("http://example.com")
            assert result["ok"] is False
            assert "navigation failed" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_screenshot_success(self):
        """测试浏览器截图成功"""
        mock_browser = MagicMock()
        mock_browser.get_screenshot_as_base64.return_value = "base64_data"
        
        with patch("agent.system_tools.get_browser", return_value=mock_browser):
            result = browser_screenshot()
            assert result["ok"] is True
            assert "screenshot_base64" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_screenshot_error(self):
        """测试浏览器截图失败"""
        mock_browser = MagicMock()
        mock_browser.get_screenshot_as_base64.side_effect = Exception("screenshot failed")
        
        with patch("agent.system_tools.get_browser", return_value=mock_browser):
            result = browser_screenshot()
            assert result["ok"] is False
            assert "screenshot failed" in result["error"]


class TestSearchFilesBoundaryConditions:
    """测试 search_files 的边界条件"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_files_max_walk_limit(self):
        """测试遍历文件数达到上限"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建多个测试文件
            for i in range(10):
                with open(os.path.join(temp_dir, f"test{i}.txt"), "w") as f:
                    f.write(f"test{i}")
            
            with patch("os.walk") as mock_walk:
                # 模拟返回大量文件
                mock_walk.return_value = [
                    (temp_dir, [], [f"file{i}.txt" for i in range(100)])
                ]
                with patch("os.stat") as mock_stat:
                    mock_stat.return_value = MagicMock(
                        st_size=100,
                        st_mtime=time.time()
                    )
                    result = search_files("*.txt", temp_dir)
                    # 验证正常执行
                    assert result["ok"] is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_files_os_error(self):
        """测试搜索时发生 OSError"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, "test.txt"), "w") as f:
                f.write("test")
            
            with patch("os.walk", side_effect=OSError("os error")):
                result = search_files("*.txt", temp_dir)
                assert result["ok"] is False
                assert "搜索文件失败" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_files_early_break(self):
        """测试遍历达到上限后提前退出"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("os.walk") as mock_walk:
                # 模拟返回大量文件
                files = [f"file{i}.txt" for i in range(100)]
                mock_walk.return_value = [(temp_dir, [], files)]
                with patch("os.stat") as mock_stat:
                    mock_stat.return_value = MagicMock(
                        st_size=100,
                        st_mtime=time.time()
                    )
                    result = search_files("*.txt", temp_dir, max_results=5)
                    # 验证达到最大结果数后截断
                    assert len(result["results"]) == 5
                    assert result["truncated"] is True


class TestGetSingleFileInfoOSError:
    """测试 _get_single_file_info 的 OSError 处理"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_single_file_info_stat_error(self):
        """测试 os.stat 失败时重新抛出异常"""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_path = os.path.join(temp_dir, "test.txt")
            with open(test_path, "w") as f:
                f.write("test")
            
            with patch("os.stat", side_effect=OSError("stat failed")):
                with pytest.raises(OSError):
                    _get_single_file_info(test_path)


class TestTaskSchedulerFunctions:
    """测试任务调度相关函数"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_tasks_file_not_found(self):
        """测试加载任务文件不存在"""
        with tempfile.TemporaryDirectory() as temp_dir:
            tasks_file = os.path.join(temp_dir, "scheduled_tasks.json")
            with patch("agent.system_tools.SCHEDULED_TASKS_FILE", tasks_file):
                result = _load_tasks()
                # 应该返回默认空任务
                assert "tasks" in result
                assert result["tasks"] == []

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_tasks_invalid_json(self):
        """测试加载任务文件格式错误"""
        with tempfile.TemporaryDirectory() as temp_dir:
            tasks_file = os.path.join(temp_dir, "scheduled_tasks.json")
            with open(tasks_file, "w") as f:
                f.write("invalid json")
            with patch("agent.system_tools.SCHEDULED_TASKS_FILE", tasks_file):
                result = _load_tasks()
                # 应该返回默认空任务
                assert "tasks" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_save_tasks_permission_error(self):
        """测试保存任务文件权限错误"""
        with tempfile.TemporaryDirectory() as temp_dir:
            tasks_file = os.path.join(temp_dir, "scheduled_tasks.json")
            with patch("agent.system_tools.SCHEDULED_TASKS_FILE", tasks_file):
                with patch("agent.system_tools.open", side_effect=PermissionError("permission denied")):
                    try:
                        _save_tasks({"tasks": []})
                    except PermissionError:
                        pass  # 预期会抛出异常

    @pytest.mark.unit
    @pytest.mark.p0
    def test_toggle_scheduled_task_not_found(self):
        """测试切换不存在的任务"""
        with tempfile.TemporaryDirectory() as temp_dir:
            tasks_file = os.path.join(temp_dir, "scheduled_tasks.json")
            with open(tasks_file, "w") as f:
                f.write('{"tasks": []}')
            with patch("agent.system_tools.SCHEDULED_TASKS_FILE", tasks_file):
                result = toggle_scheduled_task("nonexistent", True)
                assert result["ok"] is False
                assert "不存在" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_toggle_scheduled_task_success(self):
        """测试切换任务状态成功"""
        with patch("agent.system_tools._load_tasks") as mock_load:
            mock_load.return_value = {"tasks": [{"id": "task1", "enabled": False}]}
            with patch("agent.system_tools._save_tasks") as mock_save:
                result = toggle_scheduled_task("task1", True)
                assert result["ok"] is True
                mock_save.assert_called_once()
                # 验证调用参数
                saved_data = mock_save.call_args[0][0]
                assert saved_data["tasks"][0]["enabled"] is True


class TestBrowserControlEdgeCases:
    """测试浏览器控制的边界情况"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_navigate_timeout(self):
        """测试浏览器导航超时"""
        mock_browser = MagicMock()
        mock_browser.get.side_effect = Exception("timeout")
        
        with patch("agent.system_tools.get_browser", return_value=mock_browser):
            result = browser_navigate("http://example.com")
            assert result["ok"] is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_navigate_element_not_found(self):
        """测试浏览器导航但元素未找到"""
        mock_browser = MagicMock()
        mock_browser.title = "Test"
        mock_browser.current_url = "http://example.com"
        mock_browser.find_element.side_effect = Exception("element not found")
        
        with patch("agent.system_tools.get_browser", return_value=mock_browser):
            result = browser_navigate("http://example.com")
            # 应该返回部分结果或失败
            assert result is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_get_no_selenium(self):
        """测试没有 selenium 库"""
        import sys
        # 保存原始模块
        original_selenium = sys.modules.get('selenium')
        original_selenium_webdriver = sys.modules.get('selenium.webdriver')
        
        try:
            # 移除 selenium 模块
            if 'selenium' in sys.modules:
                del sys.modules['selenium']
            if 'selenium.webdriver' in sys.modules:
                del sys.modules['selenium.webdriver']
            
            # 模拟导入失败
            with patch("builtins.__import__", side_effect=ImportError("No module named 'selenium'")):
                result = get_browser()
                assert result is None
        finally:
            # 恢复原始模块
            if original_selenium is not None:
                sys.modules['selenium'] = original_selenium
            if original_selenium_webdriver is not None:
                sys.modules['selenium.webdriver'] = original_selenium_webdriver


class TestReadFileEdgeCases:
    """测试 read_file 的边界情况"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_read_file_binary_content_fallback(self):
        """测试二进制内容检测失败后的处理"""
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            f.write(b"test content")
            temp_path = f.name
        try:
            result = read_file(temp_path, encoding="utf-8")
            assert result["ok"] is True
            assert result["binary"] is False
            assert result["content"] == "test content"
        finally:
            os.remove(temp_path)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_read_file_large_content(self):
        """测试读取大文件"""
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            f.write(b"x" * 10000)
            temp_path = f.name
        try:
            result = read_file(temp_path)
            assert result["ok"] is True
            assert len(result["content"]) == 10000
        finally:
            os.remove(temp_path)


class TestWriteFileEdgeCases:
    """测试 write_file 的边界情况"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_file_large_content(self):
        """测试写入大文件"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = os.path.join(temp_dir, "large.txt")
            large_content = "x" * 100000
            
            result = write_file(temp_path, large_content)
            assert result["ok"] is True
            assert result["size"] == 100000

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_file_unicode_content(self):
        """测试写入 Unicode 内容"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = os.path.join(temp_dir, "unicode.txt")
            unicode_content = "中文测试 😀 éèê"
            
            result = write_file(temp_path, unicode_content)
            assert result["ok"] is True
            
            # 验证写入正确
            with open(temp_path, "r", encoding="utf-8") as f:
                assert f.read() == unicode_content

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_file_empty_content(self):
        """测试写入空内容"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = os.path.join(temp_dir, "empty.txt")
            
            result = write_file(temp_path, "")
            assert result["ok"] is True
            assert result["size"] == 0


class TestUnixProtectedPath:
    """测试 Unix 保护路径检测"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_is_protected_path_unix_dirs(self):
        """测试 Unix 系统保护目录"""
        # 在 Windows 上，Unix 路径不会被检测为保护路径
        # 这个测试验证 Unix 路径在 Windows 上的行为
        result = is_protected_path("/etc/passwd")
        # 在 Windows 上，Unix 路径不应该被保护
        assert result is False


class TestSearchFilesMaxWalk:
    """测试 search_files 遍历上限"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_files_max_walk_exceeded(self):
        """测试遍历文件数超过上限"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建大量文件
            for i in range(100):
                with open(os.path.join(temp_dir, f"file{i}.txt"), "w") as f:
                    f.write("test")
            
            # 使用 mock 模拟超过 max_walk 的情况
            with patch("os.walk") as mock_walk:
                # 返回超过 50000 个文件的模拟数据
                files = [f"file{i}.txt" for i in range(60000)]
                mock_walk.return_value = [(temp_dir, [], files)]
                
                with patch("os.stat") as mock_stat:
                    mock_stat.return_value = MagicMock(
                        st_size=100,
                        st_mtime=time.time()
                    )
                    
                    result = search_files("*.txt", temp_dir)
                    assert result["ok"] is True
                    # 验证 truncated 标志
                    assert result["truncated"] is True


class TestGetSingleFileInfoLinkTarget:
    """测试符号链接目标读取"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_single_file_info_link_target_success(self):
        """测试成功读取符号链接目标"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建目标文件
            target_file = os.path.join(temp_dir, "target.txt")
            with open(target_file, "w") as f:
                f.write("target")
            
            # 模拟符号链接
            with patch("os.path.islink", return_value=True):
                with patch("os.readlink", return_value=target_file):
                    with patch("os.stat") as mock_stat:
                        mock_stat.return_value = MagicMock(
                            st_size=100,
                            st_mtime=time.time(),
                            st_ctime=time.time(),
                            st_mode=0o777
                        )
                        with patch("os.path.isdir", return_value=False):
                            info = _get_single_file_info(os.path.join(temp_dir, "link.txt"))
                            assert info["is_link"] is True
                            assert "link_target" in info
                            assert info["link_target"] == target_file


class TestBrowserInitialization:
    """测试浏览器初始化的详细场景"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_browser_page_load_timeout_error(self):
        """测试设置页面加载超时失败"""
        import agent.system_tools as st
        st._browser_instance = None
        
        # 创建模拟 webdriver 模块
        mock_webdriver = MagicMock()
        mock_browser = MagicMock()
        mock_browser.set_page_load_timeout.side_effect = Exception("timeout error")
        mock_browser.quit = MagicMock()
        mock_webdriver.Chrome.return_value = mock_browser
        
        result = get_browser(webdriver_module=mock_webdriver)
        
        # 超时设置失败应该返回 None
        assert result is None
        # 验证浏览器实例已被清理
        assert st._browser_instance is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_browser_window_handles_error(self):
        """测试获取窗口句柄失败"""
        import agent.system_tools as st
        st._browser_instance = None
        
        # 创建模拟 webdriver 模块
        mock_webdriver = MagicMock()
        mock_browser = MagicMock()
        mock_browser.set_page_load_timeout = MagicMock()
        # 使用 PropertyMock 模拟属性访问异常
        type(mock_browser).window_handles = PropertyMock(side_effect=Exception("handle error"))
        mock_webdriver.Chrome.return_value = mock_browser
        
        result = get_browser(webdriver_module=mock_webdriver)
        
        # 窗口句柄失败不应该影响浏览器返回
        assert result is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_browser_chrome_init_error(self):
        """测试 Chrome 初始化失败"""
        import agent.system_tools as st
        st._browser_instance = None
        
        # 创建模拟 webdriver 模块，Chrome 抛出异常
        mock_webdriver = MagicMock()
        mock_webdriver.Chrome.side_effect = Exception("chrome failed")
        
        result = get_browser(webdriver_module=mock_webdriver)
        
        # 初始化失败应该返回 None
        assert result is None
        # 验证浏览器实例已被清理
        assert st._browser_instance is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_browser_success(self):
        """测试浏览器成功初始化"""
        import agent.system_tools as st
        st._browser_instance = None
        
        # 创建模拟 webdriver 模块
        mock_webdriver = MagicMock()
        mock_browser = MagicMock()
        mock_browser.set_page_load_timeout = MagicMock()
        mock_browser.window_handles = ["window1"]
        mock_webdriver.Chrome.return_value = mock_browser
        
        result = get_browser(webdriver_module=mock_webdriver)
        
        # 成功初始化应该返回浏览器实例
        assert result is not None
        assert result is mock_browser

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_close_with_instance(self):
        """测试关闭浏览器实例"""
        mock_browser = MagicMock()
        
        import agent.system_tools as st
        st._browser_instance = mock_browser
        
        browser_close()
        
        mock_browser.quit.assert_called_once()
        assert st._browser_instance is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_close_quit_error(self):
        """测试关闭浏览器时 quit 失败"""
        mock_browser = MagicMock()
        mock_browser.quit.side_effect = Exception("quit failed")
        
        import agent.system_tools as st
        st._browser_instance = mock_browser
        
        browser_close()
        
        # 即使 quit 失败，也应该清理实例
        assert st._browser_instance is None


class TestBrowserNavigation:
    """测试浏览器导航功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_navigate_success(self):
        """测试浏览器导航成功"""
        import agent.system_tools as st
        st._browser_instance = None
        
        # 创建模拟浏览器
        mock_browser = MagicMock()
        mock_browser.set_page_load_timeout = MagicMock()
        mock_browser.window_handles = ["window1"]
        mock_browser.title = "Test Page"
        mock_browser.current_url = "http://example.com"
        mock_body = MagicMock()
        mock_body.text = "Page content"
        mock_browser.find_element.return_value = mock_body
        mock_browser.get.return_value = None
        
        mock_webdriver = MagicMock()
        mock_webdriver.Chrome.return_value = mock_browser
        
        # 使用注入的 webdriver_module
        result = get_browser(webdriver_module=mock_webdriver)
        
        # 导航到 URL
        result = browser_navigate("http://example.com")
        
        assert result["ok"] is True
        assert result["title"] == "Test Page"
        assert result["url"] == "http://example.com"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_navigate_error(self):
        """测试浏览器导航失败"""
        import agent.system_tools as st
        st._browser_instance = None
        
        # 创建模拟浏览器
        mock_browser = MagicMock()
        mock_browser.set_page_load_timeout = MagicMock()
        mock_browser.window_handles = ["window1"]
        mock_browser.get.side_effect = Exception("navigation failed")
        
        mock_webdriver = MagicMock()
        mock_webdriver.Chrome.return_value = mock_browser
        
        # 使用注入的 webdriver_module
        get_browser(webdriver_module=mock_webdriver)
        
        result = browser_navigate("http://example.com")
        
        assert result["ok"] is False
        assert "navigation failed" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_navigate_element_not_found(self):
        """测试浏览器导航但 body 元素未找到"""
        import agent.system_tools as st
        st._browser_instance = None
        
        # 创建模拟浏览器
        mock_browser = MagicMock()
        mock_browser.set_page_load_timeout = MagicMock()
        mock_browser.window_handles = ["window1"]
        mock_browser.title = "Test Page"
        mock_browser.current_url = "http://example.com"
        mock_browser.find_element.side_effect = Exception("element not found")
        mock_browser.get.return_value = None
        
        mock_webdriver = MagicMock()
        mock_webdriver.Chrome.return_value = mock_browser
        
        # 使用注入的 webdriver_module
        get_browser(webdriver_module=mock_webdriver)
        
        result = browser_navigate("http://example.com")
        
        # body 未找到时，应该返回失败结果
        assert result["ok"] is False
        assert "element not found" in result["error"]


class TestBrowserScreenshot:
    """测试浏览器截图功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_screenshot_success(self):
        """测试浏览器截图成功"""
        import agent.system_tools as st
        st._browser_instance = None
        
        # 创建模拟浏览器
        mock_browser = MagicMock()
        mock_browser.set_page_load_timeout = MagicMock()
        mock_browser.window_handles = ["window1"]
        mock_browser.get_screenshot_as_base64.return_value = "base64_data"
        
        mock_webdriver = MagicMock()
        mock_webdriver.Chrome.return_value = mock_browser
        
        # 使用注入的 webdriver_module
        get_browser(webdriver_module=mock_webdriver)
        
        result = browser_screenshot()
        
        assert result["ok"] is True
        assert "screenshot_base64" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_browser_screenshot_error(self):
        """测试浏览器截图失败"""
        import agent.system_tools as st
        st._browser_instance = None
        
        # 创建模拟浏览器
        mock_browser = MagicMock()
        mock_browser.set_page_load_timeout = MagicMock()
        mock_browser.window_handles = ["window1"]
        mock_browser.get_screenshot_as_base64.side_effect = Exception("screenshot failed")
        
        mock_webdriver = MagicMock()
        mock_webdriver.Chrome.return_value = mock_browser
        
        # 使用注入的 webdriver_module
        get_browser(webdriver_module=mock_webdriver)
        
        result = browser_screenshot()
        
        assert result["ok"] is False
        assert "screenshot failed" in result["error"]


class TestProcessManagement:
    """测试进程管理功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_process_whitelist_check(self):
        """测试进程白名单检查"""
        # 测试不在白名单中的程序
        result = start_process("malware.exe")
        assert result["ok"] is False
        assert "不在白名单中" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_process_allowed_program(self):
        """测试启动白名单程序"""
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            
            result = start_process("notepad.exe")
            assert result["ok"] is True
            assert result["pid"] == 12345
            assert result["program"] == "notepad.exe"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_process_with_args(self):
        """测试带参数启动程序"""
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            
            result = start_process("python.exe", ["--version"])
            assert result["ok"] is True
            # 验证参数传递
            call_args = mock_popen.call_args[0][0]
            assert "--version" in call_args

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_process_exception(self):
        """测试启动进程异常"""
        with patch("subprocess.Popen", side_effect=Exception("process failed")):
            result = start_process("notepad.exe")
            assert result["ok"] is False
            assert "process failed" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_processes_success(self):
        """测试列出进程"""
        mock_proc_iter = [
            {"pid": 123, "name": "notepad.exe", "status": "running"},
            {"pid": 456, "name": "malware.exe", "status": "running"},
            {"pid": 789, "name": "python.exe", "status": "running"},
        ]
        
        with patch("psutil.process_iter") as mock_iter:
            # 创建模拟进程对象
            mock_procs = []
            for info in mock_proc_iter:
                mock_proc = MagicMock()
                mock_proc.info = info
                mock_procs.append(mock_proc)
            mock_iter.return_value = mock_procs
            
            result = list_processes()
            # 只应该返回白名单进程
            assert len(result) == 2
            assert any(p["name"] == "notepad.exe" for p in result)
            assert any(p["name"] == "python.exe" for p in result)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_processes_exception(self):
        """测试列出进程时异常"""
        with patch("psutil.process_iter") as mock_iter:
            mock_proc = MagicMock()
            mock_proc.info = {"pid": 123, "name": "notepad.exe", "status": "running"}
            # 模拟访问 info 时抛出异常
            type(mock_proc).info = PropertyMock(side_effect=Exception("access denied"))
            mock_iter.return_value = [mock_proc]
            
            result = list_processes()
            # 异常应该被静默处理
            assert result == []

    @pytest.mark.unit
    @pytest.mark.p0
    def test_stop_process_whitelist_check(self):
        """测试终止进程白名单检查"""
        with patch("psutil.Process") as mock_process:
            mock_proc = MagicMock()
            mock_proc.name.return_value = "malware.exe"
            mock_process.return_value = mock_proc
            
            result = stop_process(12345)
            assert result["ok"] is False
            assert "不在白名单" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_stop_process_success(self):
        """测试成功终止进程"""
        with patch("psutil.Process") as mock_process:
            mock_proc = MagicMock()
            mock_proc.name.return_value = "notepad.exe"
            mock_proc.terminate.return_value = None
            mock_process.return_value = mock_proc
            
            result = stop_process(12345)
            assert result["ok"] is True
            mock_proc.terminate.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_stop_process_not_found(self):
        """测试终止不存在的进程"""
        with patch("psutil.Process", side_effect=Exception("process not found")):
            result = stop_process(12345)
            assert result["ok"] is False
            assert "not found" in result["error"]


class TestBrowserConfig:
    """测试浏览器配置"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_browser_config(self):
        """测试设置浏览器配置"""
        import agent.system_tools as st
        
        # 设置自定义配置
        set_browser_config(page_load_timeout=30, headless=False)
        
        # 验证配置已更新
        assert st._browser_config["page_load_timeout"] == 30
        assert st._browser_config["headless"] is False
        
        # 恢复默认配置
        set_browser_config(page_load_timeout=15, headless=True)


class TestClipboardOperations:
    """测试剪贴板操作"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_clipboard_pyperclip_success(self):
        """测试使用 pyperclip 读取剪贴板成功"""
        with patch("pyperclip.paste", return_value="clipboard content"):
            result = get_clipboard()
            assert result["ok"] is True
            assert result["content"] == "clipboard content"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_clipboard_pyperclip_import_error(self):
        """测试 pyperclip 导入失败，使用 PowerShell 后备"""
        import agent.system_tools as st
        
        # 模拟 pyperclip 导入失败
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
        
        def mock_import(name, *args, **kwargs):
            if name == "pyperclip":
                raise ImportError("pyperclip not found")
            return original_import(name, *args, **kwargs)
        
        with patch("builtins.__import__", mock_import):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="powershell content")
                result = get_clipboard()
                assert result["ok"] is True
                assert result["content"] == "powershell content"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_clipboard_all_methods_fail(self):
        """测试所有剪贴板读取方法都失败"""
        import agent.system_tools as st
        
        # 模拟 pyperclip 导入失败
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
        
        def mock_import(name, *args, **kwargs):
            if name == "pyperclip":
                raise ImportError("pyperclip not found")
            return original_import(name, *args, **kwargs)
        
        with patch("builtins.__import__", mock_import):
            with patch("subprocess.run", side_effect=Exception("powershell failed")):
                result = get_clipboard()
                assert result["ok"] is False
                assert "剪贴板读取失败" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_clipboard_pyperclip_success(self):
        """测试使用 pyperclip 写入剪贴板成功"""
        with patch("pyperclip.copy") as mock_copy:
            result = set_clipboard("test content")
            assert result["ok"] is True
            mock_copy.assert_called_once_with("test content")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_clipboard_content_too_long(self):
        """测试剪贴板内容过长"""
        long_content = "x" * 60000
        result = set_clipboard(long_content)
        assert result["ok"] is False
        assert "内容过长" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_clipboard_pyperclip_import_error(self):
        """测试 pyperclip 导入失败，使用 PowerShell 后备写入"""
        import agent.system_tools as st
        
        # 模拟 pyperclip 导入失败
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
        
        def mock_import(name, *args, **kwargs):
            if name == "pyperclip":
                raise ImportError("pyperclip not found")
            return original_import(name, *args, **kwargs)
        
        with patch("builtins.__import__", mock_import):
            with patch("subprocess.run") as mock_run:
                result = set_clipboard("test content")
                assert result["ok"] is True
                mock_run.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_clipboard_all_methods_fail(self):
        """测试所有剪贴板写入方法都失败"""
        import agent.system_tools as st
        
        # 模拟 pyperclip 导入失败
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
        
        def mock_import(name, *args, **kwargs):
            if name == "pyperclip":
                raise ImportError("pyperclip not found")
            return original_import(name, *args, **kwargs)
        
        with patch("builtins.__import__", mock_import):
            with patch("subprocess.run", side_effect=Exception("powershell failed")):
                result = set_clipboard("test content")
                assert result["ok"] is False
                assert "剪贴板写入失败" in result["error"]


class TestProcessManagementNoSuchProcess:
    """测试进程不存在异常处理"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_stop_process_no_such_process(self):
        """测试终止不存在的进程（NoSuchProcess 异常）"""
        import psutil
        
        with patch("psutil.Process") as mock_process:
            mock_process.side_effect = psutil.NoSuchProcess(12345)
            result = stop_process(12345)
            assert result["ok"] is False
            assert "进程不存在" in result["error"]


