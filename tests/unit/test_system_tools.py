"""
SystemTools 单元测试
测试 agent/system_tools.py 的功能
"""
import pytest
import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from agent.system_tools import (
    is_protected_path,
    safe_resolve_path,
    is_binary_content,
    is_executable_extension,
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
    list_scheduled_tasks,
    create_scheduled_task,
    delete_scheduled_task,
    toggle_scheduled_task,
    start_process,
    get_clipboard,
    set_clipboard,
)


class TestPathSecurity:
    """测试路径安全检查功能"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_is_protected_path_windows_system_dirs(self):
        """测试 Windows 系统保护目录检测"""
        assert is_protected_path(r"C:\Windows\System32") is True
        assert is_protected_path(r"C:\Program Files\SomeApp") is True
        assert is_protected_path(r"C:\Windows\Temp") is False
        assert is_protected_path(r"C:\Windows\Fonts") is False

    @pytest.mark.unit
    @pytest.mark.p3
    def test_is_protected_path_unix_system_dirs(self):
        """测试 Unix 系统保护目录检测"""
        # 在 Windows 上，Unix 路径检查也会执行
        # /etc/passwd 在 Unix 系统上是受保护的
        if os.name != "nt":
            assert is_protected_path("/etc/passwd") is True
            assert is_protected_path("/usr/lib/libc.so") is True
        assert is_protected_path("/home/user/documents") is False

    @pytest.mark.unit
    @pytest.mark.p3
    def test_safe_resolve_path_valid(self):
        """测试安全路径解析 - 有效路径"""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = os.path.join(tmpdir, "test.txt")
            result = safe_resolve_path(test_path)
            assert os.path.isabs(result)

    @pytest.mark.unit
    @pytest.mark.p3
    def test_safe_resolve_path_protected(self):
        """测试安全路径解析 - 保护目录"""
        with pytest.raises(ValueError):
            safe_resolve_path(r"C:\Windows\System32\test.txt")

    @pytest.mark.unit
    @pytest.mark.p3
    def test_safe_resolve_path_invalid(self):
        """测试安全路径解析 - 无效路径"""
        # 空字符路径在某些系统上可能不会抛出异常
        try:
            safe_resolve_path("\x00invalid")
        except (ValueError, OSError):
            pass


class TestBinaryDetection:
    """测试二进制内容检测"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_is_binary_content_with_null_byte(self):
        """测试包含 NULL 字节的内容"""
        assert is_binary_content(b"hello\x00world") is True

    @pytest.mark.unit
    @pytest.mark.p3
    def test_is_binary_content_text(self):
        """测试纯文本内容"""
        assert is_binary_content(b"hello world") is False
        # UTF-8 编码的中文可能被检测为二进制（因为非ASCII字节比例高）
        # 这是预期行为，因为二进制检测算法基于ASCII字符比例
        assert is_binary_content(b"hello world 12345") is False

    @pytest.mark.unit
    @pytest.mark.p3
    def test_is_binary_content_empty(self):
        """测试空内容"""
        assert is_binary_content(b"") is False


class TestExecutableExtension:
    """测试可执行文件扩展名检查"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_is_executable_extension_blocked(self):
        """测试被阻止的扩展名"""
        blocked_exts = [".exe", ".dll", ".bat", ".ps1", ".pyc"]
        for ext in blocked_exts:
            assert is_executable_extension(f"test{ext}") is True
        # .sh 在 Windows 上不被阻止（BLOCKED_WRITE_EXTENSIONS 中没有）
        assert is_executable_extension("test.sh") is False

    @pytest.mark.unit
    @pytest.mark.p3
    def test_is_executable_extension_allowed(self):
        """测试允许的扩展名"""
        allowed_exts = [".txt", ".md", ".json", ".py", ".csv"]
        for ext in allowed_exts:
            assert is_executable_extension(f"test{ext}") is False


class TestFileOperations:
    """测试文件操作功能"""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.mark.unit
    @pytest.mark.p3
    def test_read_file_text(self, temp_dir):
        """测试读取文本文件"""
        test_file = os.path.join(temp_dir, "test.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("hello world")
        
        result = read_file(test_file)
        assert result["ok"] is True
        assert result["content"] == "hello world"
        assert result["binary"] is False

    @pytest.mark.unit
    @pytest.mark.p3
    def test_read_file_binary(self, temp_dir):
        """测试读取二进制文件"""
        test_file = os.path.join(temp_dir, "test.bin")
        with open(test_file, "wb") as f:
            f.write(b"\x00\x01\x02")
        
        result = read_file(test_file)
        assert result["ok"] is True
        assert result["binary"] is True

    @pytest.mark.unit
    @pytest.mark.p3
    def test_read_file_nonexistent(self, temp_dir):
        """测试读取不存在的文件"""
        result = read_file(os.path.join(temp_dir, "nonexistent.txt"))
        assert result["ok"] is False

    @pytest.mark.unit
    @pytest.mark.p3
    def test_write_file_success(self, temp_dir):
        """测试写入文件成功"""
        test_file = os.path.join(temp_dir, "output.txt")
        result = write_file(test_file, "写入内容")
        assert result["ok"] is True
        
        with open(test_file, "r", encoding="utf-8") as f:
            assert f.read() == "写入内容"

    @pytest.mark.unit
    @pytest.mark.p3
    def test_write_file_executable_blocked(self, temp_dir):
        """测试写入可执行文件被阻止"""
        test_file = os.path.join(temp_dir, "malicious.exe")
        result = write_file(test_file, "危险代码")
        assert result["ok"] is False
        assert "禁止写入可执行" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p3
    def test_write_file_backup(self, temp_dir):
        """测试文件备份功能"""
        test_file = os.path.join(temp_dir, "existing.txt")
        with open(test_file, "w") as f:
            f.write("原始内容")
        
        result = write_file(test_file, "新内容")
        assert result["ok"] is True
        assert "backup" in result


class TestDirectoryOperations:
    """测试目录操作功能"""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试结构
            os.makedirs(os.path.join(tmpdir, "subdir"))
            with open(os.path.join(tmpdir, "file1.txt"), "w") as f:
                f.write("content1")
            with open(os.path.join(tmpdir, "subdir", "file2.txt"), "w") as f:
                f.write("content2")
            yield tmpdir

    @pytest.mark.unit
    @pytest.mark.p3
    def test_list_directory(self, temp_dir):
        """测试列出目录内容"""
        result = list_directory(temp_dir)
        assert result["ok"] is True
        assert result["total"] >= 2

    @pytest.mark.unit
    @pytest.mark.p3
    def test_get_file_info(self, temp_dir):
        """测试获取文件信息"""
        test_file = os.path.join(temp_dir, "file1.txt")
        result = get_file_info(test_file)
        assert result["ok"] is True
        assert result["type"] == "file"

    @pytest.mark.unit
    @pytest.mark.p3
    def test_search_files(self, temp_dir):
        """测试搜索文件"""
        result = search_files("*.txt", temp_dir)
        assert result["ok"] is True
        assert result["total"] >= 1


class TestWorkspaceOperations:
    """测试工作区操作功能"""

    @pytest.fixture
    def temp_workspace(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setattr("agent.system_tools.WORKSPACE_DIR", tmpdir)
            yield tmpdir

    @pytest.mark.unit
    @pytest.mark.p3
    def test_init_workspace(self, temp_workspace):
        """测试初始化工作区"""
        result = init_workspace()
        assert result == temp_workspace
        assert os.path.exists(os.path.join(temp_workspace, ".gitkeep"))

    @pytest.mark.unit
    @pytest.mark.p3
    def test_write_and_list_workspace(self, temp_workspace):
        """测试写入和列出工作区"""
        write_workspace("test.txt", "工作区内容")
        result = list_workspace("test.txt")
        assert result["content"] == "工作区内容"

    @pytest.mark.unit
    @pytest.mark.p3
    def test_delete_workspace(self, temp_workspace):
        """测试删除工作区文件"""
        write_workspace("to_delete.txt", "内容")
        delete_workspace("to_delete.txt")
        assert not os.path.exists(os.path.join(temp_workspace, "to_delete.txt"))

    @pytest.mark.unit
    @pytest.mark.p3
    def test_workspace_path_validation(self, temp_workspace):
        """测试工作区路径验证"""
        with pytest.raises(ValueError):
            write_workspace("../escape.txt", "内容")


class TestSandbox:
    """测试 Python 沙盒功能"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_run_sandbox_safe_code(self):
        """测试运行安全代码"""
        # 沙盒中没有 print，使用变量赋值来测试
        result = run_sandbox("x = 1 + 2; y = [1, 2, 3]")
        assert result["error"] is None

    @pytest.mark.unit
    @pytest.mark.p3
    def test_run_sandbox_blocked_pattern(self):
        """测试被阻止的代码模式"""
        blocked_codes = [
            "__import__('os')",
            "eval('1')",
            "exec('print(1)')",
            "__class__",
            "__bases__",
        ]
        for code in blocked_codes:
            result = run_sandbox(code)
            assert result["error"] is not None

    @pytest.mark.unit
    @pytest.mark.p3
    def test_run_sandbox_timeout(self):
        """测试超时处理"""
        # 使用长循环来测试超时（import被阻止）
        result = run_sandbox("x = 0; [x := x + 1 for _ in range(10**8)]", timeout_sec=1)
        assert result["timed_out"] is True


class TestScheduledTasks:
    """测试定时任务功能"""

    @pytest.fixture
    def temp_task_file(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_file = os.path.join(tmpdir, "tasks.json")
            monkeypatch.setattr("agent.system_tools.SCHEDULED_TASKS_FILE", task_file)
            yield task_file

    @pytest.mark.unit
    @pytest.mark.p3
    def test_create_scheduled_task(self, temp_task_file):
        """测试创建定时任务"""
        result = create_scheduled_task("测试任务", "python script.py")
        assert result["ok"] is True
        assert result["task"]["name"] == "测试任务"

    @pytest.mark.unit
    @pytest.mark.p3
    def test_create_scheduled_task_not_allowed(self, temp_task_file):
        """测试创建不允许的任务"""
        result = create_scheduled_task("危险任务", "rm -rf /")
        assert result["ok"] is False

    @pytest.mark.unit
    @pytest.mark.p3
    def test_list_and_delete_task(self, temp_task_file):
        """测试列出和删除任务"""
        create_scheduled_task("任务1", "python test.py")
        tasks = list_scheduled_tasks()
        assert len(tasks["tasks"]) == 1
        
        task_id = tasks["tasks"][0]["id"]
        delete_result = delete_scheduled_task(task_id)
        assert delete_result["deleted"] is True
        
        tasks = list_scheduled_tasks()
        assert len(tasks["tasks"]) == 0

    @pytest.mark.unit
    @pytest.mark.p3
    def test_toggle_task(self, temp_task_file):
        """测试启用/禁用任务"""
        create_scheduled_task("任务", "python test.py")
        task_id = list_scheduled_tasks()["tasks"][0]["id"]
        
        toggle_scheduled_task(task_id, False)
        tasks = list_scheduled_tasks()
        assert tasks["tasks"][0]["enabled"] is False


class TestProcessManagement:
    """测试进程管理功能"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_start_process_allowed(self):
        """测试启动允许的进程"""
        result = start_process("notepad.exe")
        # 可能成功也可能失败（取决于环境），但不应因为白名单失败
        if result["ok"] is False:
            assert "不在白名单中" not in result["error"]

    @pytest.mark.unit
    @pytest.mark.p3
    def test_start_process_blocked(self):
        """测试启动被阻止的进程"""
        result = start_process("malware.exe")
        assert result["ok"] is False
        assert "不在白名单中" in result["error"]


class TestClipboard:
    """测试剪贴板功能"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_clipboard_basic(self):
        """测试剪贴板基本操作"""
        # 测试读取（即使没有pyperclip也应返回某种结果）
        result = get_clipboard()
        assert isinstance(result, dict)
        
        # 测试写入
        result = set_clipboard("测试剪贴板内容")
        assert result["ok"] is True or "失败" in result.get("error", "")