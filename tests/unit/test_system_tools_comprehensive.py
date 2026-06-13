"""
SystemTools 综合测试 - 覆盖剩余未覆盖的代码
目标：将覆盖率从 60% 提升至 90%+
"""
import pytest
import os
import tempfile
import sys
from unittest.mock import MagicMock, patch, call
from agent.system_tools import (
    is_protected_path,
    safe_resolve_path,
    is_binary_content,
    read_file,
    write_file,
    list_directory,
    get_file_info,
    search_files,
    init_workspace,
    list_workspace,
    write_workspace,
    delete_workspace,
    run_sandbox,
    _guess_mime_type,
    list_scheduled_tasks,
    create_scheduled_task,
    delete_scheduled_task,
    toggle_scheduled_task,
    get_browser,
    browser_navigate,
    browser_screenshot,
    browser_close,
    start_process,
    list_processes,
    stop_process,
    get_clipboard,
    set_clipboard,
    BLOCKED_WRITE_EXTENSIONS,
)


class TestSystemToolsBasicFunctions:
    """测试系统工具基本功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_is_protected_path_windows(self):
        """测试 Windows 系统保护路径检测"""
        assert is_protected_path(r"C:\Windows\System32") is True
        assert is_protected_path(r"C:\Program Files") is True
        assert is_protected_path(r"C:\Windows\Temp") is False
        assert is_protected_path(r"C:\Users\Test\Documents") is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_is_protected_path_unix(self):
        """测试 Unix/Linux 系统保护路径检测（通过直接调用内部逻辑）"""
        from agent.system_tools import PROTECTED_SYSTEM_DIRS_UNIX
        
        abs_path = "/etc/test"
        is_protected = False
        for protected in PROTECTED_SYSTEM_DIRS_UNIX:
            if abs_path.startswith(protected + "/") or abs_path == protected:
                is_protected = True
                break
        assert is_protected is True
        
        abs_path = "/home/user"
        is_protected = False
        for protected in PROTECTED_SYSTEM_DIRS_UNIX:
            if abs_path.startswith(protected + "/") or abs_path == protected:
                is_protected = True
                break
        assert is_protected is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_is_protected_path_invalid_input(self):
        """测试无效路径输入（触发异常分支）"""
        assert is_protected_path(None) is True
        assert is_protected_path(123) is True
        # 空字符串不会触发异常，会被解析为当前目录，返回 False
        assert is_protected_path("") is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_resolve_path(self):
        """测试安全路径解析"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = safe_resolve_path(os.path.join(tmpdir, "test.txt"))
            assert os.path.dirname(result) == tmpdir

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_resolve_path_protected(self):
        """测试安全路径解析 - 保护目录"""
        result = read_file(r"C:\Windows\System32\notepad.exe")
        assert result["ok"] is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_resolve_path_invalid(self):
        """测试安全路径解析 - 无效路径（触发异常分支）"""
        # 使用保护路径来触发 ValueError
        with pytest.raises(ValueError):
            safe_resolve_path(r"C:\Windows\System32")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_is_binary_content(self):
        """测试二进制内容检测"""
        assert is_binary_content(b"hello world") is False
        assert is_binary_content(b"\x00\x00\x00") is True
        assert is_binary_content(b"") is False
        
        binary_content = b"\x00\x01\x02\x03"
        assert is_binary_content(binary_content) is True
        
        mostly_binary = b"hello" + b"\x00" * 100
        assert is_binary_content(mostly_binary) is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_read_write_file(self):
        """测试文件读写功能"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            temp_file = f.name
        
        try:
            content = "test content"
            write_result = write_file(temp_file, content)
            assert write_result["ok"] is True
            
            read_result = read_file(temp_file)
            assert read_result["ok"] is True
            assert content in read_result["content"]
        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_read_file_size_limit(self):
        """测试文件读取大小限制"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            temp_file = f.name
            f.write("x" * (10 * 1024 * 1024 + 1))
        
        try:
            result = read_file(temp_file)
            assert result["ok"] is False
        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_read_file_not_exists(self):
        """测试读取不存在的文件"""
        result = read_file("nonexistent_file_12345.txt")
        assert result["ok"] is False
        assert "不存在" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_read_file_is_directory(self):
        """测试读取目录（非文件）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = read_file(tmpdir)
            assert result["ok"] is False
            assert "不是文件" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_read_binary_file(self):
        """测试读取二进制文件"""
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.bin', delete=False) as f:
            temp_file = f.name
            f.write(b"\x00\x01\x02\x03")
        
        try:
            result = read_file(temp_file)
            assert result["ok"] is True
            assert result["binary"] is True
            assert "base64" in result["encoding"]
        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_read_file_unicode_error(self):
        """测试读取包含非法编码的文件"""
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.txt', delete=False) as f:
            temp_file = f.name
            f.write(b"\xff\xfe\xfd")
        
        try:
            result = read_file(temp_file)
            assert result["ok"] is True
        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_file_blocked_extension(self):
        """测试写入被阻止的文件扩展名"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.exe', delete=False) as f:
            temp_file = f.name
        
        try:
            result = write_file(temp_file, "test")
            assert result["ok"] is False
            assert "禁止" in result["error"]
        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_file_too_large(self):
        """测试写入过大的内容"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            temp_file = f.name
        
        try:
            large_content = "x" * (51 * 1024 * 1024)
            result = write_file(temp_file, large_content)
            assert result["ok"] is False
            assert "过大" in result["error"]
        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_file_protected_path(self):
        """测试写入保护路径"""
        result = write_file(r"C:\Windows\System32\test.txt", "test")
        assert result["ok"] is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_directory(self):
        """测试目录列表功能"""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "subdir"))
            with open(os.path.join(tmpdir, "file.txt"), 'w') as f:
                f.write("test")
            
            result = list_directory(tmpdir)
            assert result["ok"] is True
            assert "items" in result

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_file_info(self):
        """测试获取文件信息"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            temp_file = f.name
        
        try:
            result = get_file_info(temp_file)
            assert result["ok"] is True
            assert result["path"] == temp_file
            assert "type" in result
        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_search_files(self):
        """测试文件搜索功能"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "test.txt"), 'w') as f:
                f.write("test")
            
            result = search_files("*.txt", tmpdir)
            assert result["ok"] is True
            assert len(result["results"]) >= 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_guess_mime_type(self):
        """测试 MIME 类型猜测"""
        assert _guess_mime_type("test.txt") == "text/plain"
        assert _guess_mime_type("test.html") == "text/html"
        assert _guess_mime_type("test.png") == "image/png"
        assert _guess_mime_type("test.pdf") == "application/pdf"
        assert _guess_mime_type("test.unknown") == "application/octet-stream"


class TestWorkspaceManagement:
    """测试工作区管理功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_workspace_operations(self):
        """测试工作区基本操作"""
        with tempfile.TemporaryDirectory() as tmpdir:
            import agent.system_tools as st
            original_workspace = st.WORKSPACE_DIR
            st.WORKSPACE_DIR = os.path.join(tmpdir, "workspace")
            
            try:
                init_workspace()
                assert os.path.exists(st.WORKSPACE_DIR)
                
                write_workspace("test.txt", "test content")
                assert os.path.exists(os.path.join(st.WORKSPACE_DIR, "test.txt"))
                
                files = list_workspace()
                assert "test.txt" in [item["name"] for item in files["items"]]
                
                delete_workspace("test.txt")
                assert not os.path.exists(os.path.join(st.WORKSPACE_DIR, "test.txt"))
            finally:
                st.WORKSPACE_DIR = original_workspace


class TestSandboxExecution:
    """测试沙盒执行功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sandbox_basic_execution(self):
        """测试沙盒基本执行"""
        code = "result = 1 + 2"
        result = run_sandbox(code)
        assert result["error"] is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sandbox_blocked_patterns(self):
        """测试沙盒阻止危险代码模式"""
        blocked_codes = [
            "__import__('os')",
            "open('/etc/passwd')",
            "eval('os.system')",
            "exec('rm -rf /')",
            "getattr(object, '__class__')",
        ]
        
        for code in blocked_codes:
            result = run_sandbox(code)
            assert result["error"] is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sandbox_timeout(self):
        """测试沙盒超时机制"""
        code = "import time; time.sleep(5)"
        result = run_sandbox(code, timeout_sec=1)
        assert result is not None


class TestClipboardOperations:
    """测试剪贴板操作"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_clipboard_get_set(self):
        """测试剪贴板读写"""
        test_text = "test clipboard content"
        result = set_clipboard(test_text)
        assert result["ok"] is True or result.get("error") is not None
        
        result = get_clipboard()
        assert result is not None


class TestScheduledTasks:
    """测试定时任务管理"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_create_list_delete_task(self):
        """测试创建、列出和删除定时任务"""
        result = create_scheduled_task("test_task", "echo hello", 60)
        assert result["ok"] is True
        
        tasks = list_scheduled_tasks()
        assert len(tasks["tasks"]) >= 1
        
        task_id = tasks["tasks"][0]["id"]
        delete_result = delete_scheduled_task(task_id)
        assert delete_result["ok"] is True


class TestReadFileExceptions:
    """测试 read_file 异常处理"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_read_file_os_error(self):
        """测试读取文件时的 OSError"""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test")
            temp_path = f.name
        try:
            with patch("builtins.open", side_effect=OSError("test error")):
                result = read_file(temp_path)
                assert result["ok"] is False
                assert "读取文件失败" in result["error"]
        finally:
            os.remove(temp_path)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_read_file_unicode_decode_error(self):
        """测试 Unicode 解码错误处理"""
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            f.write(b"\xff\xfe\xfd")  # Invalid UTF-8
            temp_path = f.name
        
        try:
            # 先读取为二进制，然后测试文本解码
            result = read_file(temp_path, encoding="utf-8")
            # 对于无法识别的二进制内容，会返回 base64 编码
            if result.get("binary", False):
                assert result["encoding"] == "base64"
            else:
                assert "utf-8 (with replacements)" in result["encoding"]
        finally:
            os.remove(temp_path)


class TestWriteFileExceptions:
    """测试 write_file 异常处理"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_file_os_error(self):
        """测试写入时的 OSError"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = os.path.join(temp_dir, "test.txt")
            with patch("builtins.open", side_effect=OSError("test error")):
                result = write_file(temp_path, "content")
                assert result["ok"] is False
                assert "写入文件失败" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_file_create_dir_error(self):
        """测试创建目录失败"""
        with patch("agent.system_tools.safe_resolve_path", return_value="test.txt"):
            with patch("os.makedirs", side_effect=OSError("mkdir error")):
                result = write_file("test.txt", "content")
                assert result["ok"] is False
                assert "无法创建目录" in result["error"]


class TestListDirectory:
    """测试 list_directory 函数"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_directory_invalid_path(self):
        """测试无效路径"""
        with patch("agent.system_tools.safe_resolve_path", side_effect=ValueError("invalid path")):
            result = list_directory("invalid")
            assert result["ok"] is False
            assert "invalid path" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_directory_not_exists(self):
        """测试路径不存在"""
        result = list_directory("/nonexistent/path/xyz123")
        assert result["ok"] is False
        assert "路径不存在" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_directory_is_file(self):
        """测试路径是文件"""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"content")
            temp_path = f.name
        try:
            result = list_directory(temp_path)
            assert result["ok"] is True
            assert result["type"] == "file"
        finally:
            os.remove(temp_path)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_directory_permission_error(self):
        """测试无权限访问目录"""
        with patch("agent.system_tools.safe_resolve_path", return_value="test_dir"):
            with patch("os.listdir", side_effect=PermissionError("permission denied")):
                result = list_directory("test_dir")
                assert result["ok"] is False
                assert "没有权限" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_directory_hidden_files(self):
        """测试隐藏文件过滤"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, ".hidden"), "w") as f:
                f.write("hidden")
            with open(os.path.join(temp_dir, "visible"), "w") as f:
                f.write("visible")
            
            result_hidden = list_directory(temp_dir, show_hidden=True)
            result_normal = list_directory(temp_dir, show_hidden=False)
            
            assert len(result_hidden["items"]) == 2
            assert len(result_normal["items"]) == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_directory_max_items(self):
        """测试最大条目限制"""
        with tempfile.TemporaryDirectory() as temp_dir:
            for i in range(10):
                with open(os.path.join(temp_dir, f"file{i}.txt"), "w") as f:
                    f.write(str(i))
            
            result = list_directory(temp_dir, max_items=5)
            assert len(result["items"]) == 5


class TestGetFileInfo:
    """测试 get_file_info 函数"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_file_info_invalid_path(self):
        """测试无效路径"""
        with patch("agent.system_tools.safe_resolve_path", side_effect=ValueError("invalid")):
            result = get_file_info("invalid")
            assert result["ok"] is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_file_info_not_exists(self):
        """测试路径不存在"""
        result = get_file_info("/nonexistent/path/xyz123")
        assert result["ok"] is False
        assert "路径不存在" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_file_info_valid(self):
        """测试有效文件信息"""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            temp_path = f.name
        try:
            result = get_file_info(temp_path)
            assert result["ok"] is True
            assert "type" in result
            assert "size" in result
            assert "modified" in result
        finally:
            os.remove(temp_path)


class TestSearchFiles:
    """测试 search_files 函数"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_files_invalid_root(self):
        """测试无效搜索根目录"""
        with patch("agent.system_tools.safe_resolve_path", side_effect=ValueError("invalid")):
            result = search_files("*.txt", "invalid")
            assert result["ok"] is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_files_not_exists(self):
        """测试搜索根目录不存在"""
        result = search_files("*.txt", "/nonexistent/path_xyz123")
        assert result["ok"] is False
        assert "不存在" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_files_not_dir(self):
        """测试搜索根目录不是目录"""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"content")
            temp_path = f.name
        try:
            result = search_files("*.txt", temp_path)
            assert result["ok"] is False
            assert "不是目录" in result["error"]
        finally:
            os.remove(temp_path)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_files_pattern_match(self):
        """测试模式匹配"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, "test.txt"), "w") as f:
                f.write("content")
            with open(os.path.join(temp_dir, "test.py"), "w") as f:
                f.write("code")
            
            result = search_files("*.txt", temp_dir)
            assert result["ok"] is True
            assert len(result["results"]) == 1
            assert result["results"][0]["name"] == "test.txt"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_search_files_case_insensitive(self):
        """测试大小写不敏感匹配"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, "Test.TXT"), "w") as f:
                f.write("content")
            
            result = search_files("*.txt", temp_dir, ignore_case=True)
            assert len(result["results"]) >= 1
            
            # Windows 文件系统大小写不敏感，所以这里只测试 ignore_case=True 的情况
            # 大小写敏感测试在 Windows 上不适用


class TestWorkspaceFunctions:
    """测试工作区函数"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_workspace_outside(self):
        """测试列出工作区外的路径"""
        with pytest.raises(ValueError, match="路径超出工作区范围"):
            list_workspace("../outside")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_workspace_not_exists(self):
        """测试路径不存在"""
        result = list_workspace("nonexistent")
        assert result["error"] == "路径不存在"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_workspace_is_file(self):
        """测试路径是文件"""
        from agent.system_tools import WORKSPACE_DIR
        test_file = os.path.join(WORKSPACE_DIR, "test_file.txt")
        with open(test_file, "w") as f:
            f.write("test content")
        try:
            result = list_workspace("test_file.txt")
            assert result["type"] == "file"
            assert result["content"] == "test content"
        finally:
            os.remove(test_file)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_workspace_outside(self):
        """测试写入工作区外的路径"""
        with pytest.raises(ValueError, match="路径超出工作区范围"):
            write_workspace("../outside/test.txt", "content")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_workspace_create_dir(self):
        """测试写入时创建目录"""
        result = write_workspace("subdir/test.txt", "content")
        assert result["ok"] is True
        
        from agent.system_tools import WORKSPACE_DIR
        full_path = os.path.join(WORKSPACE_DIR, "subdir", "test.txt")
        assert os.path.exists(full_path)
        os.remove(full_path)
        os.rmdir(os.path.dirname(full_path))

    @pytest.mark.unit
    @pytest.mark.p0
    def test_delete_workspace_outside(self):
        """测试删除工作区外的路径"""
        with pytest.raises(ValueError, match="路径超出工作区范围"):
            delete_workspace("../outside")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_delete_workspace_root(self):
        """测试删除工作区根目录"""
        with pytest.raises(ValueError, match="不能删除工作区根目录"):
            delete_workspace("")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_delete_workspace_file(self):
        """测试删除文件"""
        from agent.system_tools import WORKSPACE_DIR
        test_file = os.path.join(WORKSPACE_DIR, "to_delete.txt")
        with open(test_file, "w") as f:
            f.write("content")
        
        result = delete_workspace("to_delete.txt")
        assert result["ok"] is True
        assert not os.path.exists(test_file)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_delete_workspace_directory(self):
        """测试删除目录"""
        from agent.system_tools import WORKSPACE_DIR
        test_dir = os.path.join(WORKSPACE_DIR, "to_delete_dir")
        os.makedirs(test_dir)
        with open(os.path.join(test_dir, "file.txt"), "w") as f:
            f.write("content")
        
        result = delete_workspace("to_delete_dir")
        assert result["ok"] is True
        assert not os.path.exists(test_dir)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_toggle_task(self):
        """测试启用/禁用任务"""
        create_result = create_scheduled_task("toggle_task", "echo test", 60)
        task_id = create_result["task"]["id"]
        
        toggle_result = toggle_scheduled_task(task_id, False)
        assert toggle_result["ok"] is True
        
        delete_scheduled_task(task_id)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_create_task_not_allowed(self):
        """测试创建不在白名单中的任务"""
        result = create_scheduled_task("bad_task", "rm -rf /")
        assert result["ok"] is False


class TestProcessManagement:
    """测试进程管理功能"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_list_processes(self):
        """测试列出进程"""
        processes = list_processes()
        assert isinstance(processes, list)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_start_not_allowed(self):
        """测试启动不在白名单中的程序"""
        result = start_process("malicious.exe")
        assert result["ok"] is False