"""文件系统工具集成测试 — 测试 system_tools.py 中的文件操作函数

覆盖范围：
- read_file — 正常读取、编码指定、行范围、文件不存在、超过大小限制、二进制文件
- write_file — 写入新文件、覆盖已有文件、禁止写入可执行文件、路径遍历防护
- list_directory — 正常列出、目录不存在、显示隐藏文件、路径是文件
- search_files — glob 模式搜索、无结果、忽略大小写
- get_file_info — 文件信息、目录信息、路径不存在
"""
import os
import pytest
from agent.system_tools import (
    read_file, write_file, list_directory, search_files, get_file_info,
)


class TestReadFile:
    """read_file 工具测试"""

    def test_read_normal(self, tmp_path):
        """正常读取文本文件（纯 ASCII）"""
        f = tmp_path / "hello.txt"
        f.write_text("Hello, world!", encoding="utf-8")
        result = read_file(str(f))
        assert result["ok"] is True
        assert result["content"] == "Hello, world!"
        assert result["encoding"] == "utf-8"
        assert result["binary"] is False

    def test_read_with_encoding(self, tmp_path):
        """指定编码读取 ASCII 文本"""
        f = tmp_path / "latin.txt"
        f.write_text("Simple ASCII text", encoding="latin-1")
        result = read_file(str(f), encoding="latin-1")
        assert result["ok"] is True
        assert result["content"] == "Simple ASCII text"

    def test_read_chinese_content_trigger_binary_detection(self, tmp_path):
        """中文 UTF-8 内容因字节分布触发二进制检测"""
        f = tmp_path / "chinese.txt"
        f.write_text("你好世界", encoding="utf-8")
        result = read_file(str(f))
        assert result["ok"] is True
        # 中文字节大多 > 0x7E，text char ratio < 85%，被判定为 binary
        assert result["binary"] is True

    def test_read_with_range(self, tmp_path):
        """指定行范围读取"""
        f = tmp_path / "lines.txt"
        f.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
        result = read_file(str(f), range="2-4")
        assert result["ok"] is True
        assert "line1" not in result["content"]
        assert "line2" in result["content"]
        assert "line3" in result["content"]
        assert "line4" in result["content"]
        assert "line5" not in result["content"]

    def test_read_file_not_found(self, tmp_path):
        """文件不存在"""
        result = read_file(str(tmp_path / "nonexistent.txt"))
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_read_path_is_directory(self, tmp_path):
        """路径是目录而非文件"""
        result = read_file(str(tmp_path))
        assert result["ok"] is False
        assert "目录" in result["error"]

    def test_read_too_large(self, tmp_path):
        """超过大小限制"""
        f = tmp_path / "large.txt"
        # 创建 6MB 文件
        f.write_bytes(b"x" * (6 * 1024 * 1024))
        result = read_file(str(f), max_size_mb=5)
        assert result["ok"] is False
        assert "过大" in result["error"]

    def test_read_binary_file(self, tmp_path):
        """读取二进制文件应返回 base64"""
        f = tmp_path / "test.bin"
        f.write_bytes(b"\x00\x01\x02\xFF\xFE")
        result = read_file(str(f), encoding=None)
        assert result["ok"] is True
        assert result["binary"] is True
        assert result["encoding"] == "base64"
        assert "content" in result


class TestWriteFile:
    """write_file 工具测试"""

    def test_write_new_file(self, tmp_path):
        """写入新文件"""
        f = tmp_path / "new.txt"
        result = write_file(str(f), "新文件内容")
        assert result["ok"] is True
        assert f.read_text(encoding="utf-8") == "新文件内容"

    def test_write_overwrite(self, tmp_path):
        """覆盖已有文件"""
        f = tmp_path / "existing.txt"
        f.write_text("旧内容", encoding="utf-8")
        result = write_file(str(f), "新内容")
        assert result["ok"] is True
        assert f.read_text(encoding="utf-8") == "新内容"
        assert "backup" in result  # 应有备份

    def test_write_executable_extension_blocked(self, tmp_path):
        """禁止写入可执行文件类型"""
        for ext in [".exe", ".dll", ".bat", ".ps1", ".vbs"]:
            f = tmp_path / f"evil{ext}"
            result = write_file(str(f), "bad stuff")
            assert result["ok"] is False, f"扩展名 {ext} 应被阻止"
            assert "禁止写入" in result["error"]

    def test_write_to_protected_directory(self, tmp_path):
        """写入系统保护目录应被拒绝"""
        if os.name == "nt":
            protected = r"C:\Windows\System32\evil.txt"
        else:
            protected = "/etc/evil.txt"
        result = write_file(protected, "hack")
        assert result["ok"] is False
        assert "保护目录" in result["error"] or "拒绝" in result["error"]

    def test_write_empty_content(self, tmp_path):
        """写入空内容"""
        f = tmp_path / "empty.txt"
        result = write_file(str(f), "")
        assert result["ok"] is True
        assert f.read_text(encoding="utf-8") == ""

    def test_write_nested_directory(self, tmp_path):
        """写入嵌套目录中的文件，应自动创建目录"""
        f = tmp_path / "sub" / "nested" / "deep.txt"
        result = write_file(str(f), "嵌套文件")
        assert result["ok"] is True
        assert f.read_text(encoding="utf-8") == "嵌套文件"


class TestListDirectory:
    """list_directory 工具测试"""

    def test_list_normal(self, tmp_path):
        """正常列出目录内容"""
        (tmp_path / "file1.txt").write_text("a")
        (tmp_path / "file2.txt").write_text("b")
        (tmp_path / "subdir").mkdir()
        result = list_directory(str(tmp_path))
        assert result["ok"] is True
        assert result["total"] == 3
        names = [i["name"] for i in result["items"]]
        assert "file1.txt" in names
        assert "file2.txt" in names
        assert "subdir" in names

    def test_list_show_hidden(self, tmp_path):
        """显示隐藏文件"""
        (tmp_path / "visible.txt").write_text("a")
        hidden = tmp_path / ".hidden"
        hidden.write_text("secret")
        # 不显示隐藏
        result = list_directory(str(tmp_path), show_hidden=False)
        names = [i["name"] for i in result["items"]]
        assert "visible.txt" in names
        assert ".hidden" not in names
        # 显示隐藏
        result = list_directory(str(tmp_path), show_hidden=True)
        names = [i["name"] for i in result["items"]]
        assert ".hidden" in names

    def test_list_directory_not_found(self, tmp_path):
        """目录不存在"""
        result = list_directory(str(tmp_path / "nonexistent"))
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_list_path_is_file(self, tmp_path):
        """路径是文件而非目录"""
        f = tmp_path / "afile.txt"
        f.write_text("hello")
        result = list_directory(str(f))
        assert result["ok"] is True
        assert result["type"] == "file"

    def test_list_sort_order(self, tmp_path):
        """目录优先，然后按名称排序"""
        (tmp_path / "b.txt").write_text("b")
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "z_dir").mkdir()
        result = list_directory(str(tmp_path))
        items = result["items"]
        # 目录应在文件前
        assert items[0]["type"] == "dir"
        assert items[0]["name"] == "z_dir"
        assert items[1]["name"] == "a.txt"
        assert items[2]["name"] == "b.txt"


class TestSearchFiles:
    """search_files 工具测试"""

    def test_search_glob(self, tmp_path):
        """按 glob 模式搜索"""
        (tmp_path / "data.csv").write_text("a")
        (tmp_path / "data.json").write_text("b")
        (tmp_path / "readme.md").write_text("c")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "nested.csv").write_text("d")
        result = search_files("*.csv", root_path=str(tmp_path))
        assert result["ok"] is True
        assert result["total"] >= 1
        paths = [i["name"] for i in result["results"]]
        assert "data.csv" in paths

    def test_search_recursive(self, tmp_path):
        """递归搜索子目录"""
        (tmp_path / "a.py").write_text("")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.py").write_text("")
        result = search_files("*.py", root_path=str(tmp_path))
        assert result["ok"] is True
        assert result["total"] == 2

    def test_search_no_results(self, tmp_path):
        """无匹配结果"""
        result = search_files("*.xyz", root_path=str(tmp_path))
        assert result["ok"] is True
        assert result["total"] == 0

    def test_search_root_not_found(self, tmp_path):
        """搜索根路径不存在"""
        result = search_files("*.py", root_path=str(tmp_path / "nonexistent"))
        assert result["ok"] is False

    def test_search_case_insensitive(self, tmp_path):
        """忽略大小写搜索"""
        (tmp_path / "DATA.CSV").write_text("a")
        result = search_files("*.csv", root_path=str(tmp_path), ignore_case=True)
        assert result["ok"] is True
        assert result["total"] >= 1


class TestGetFileInfo:
    """get_file_info 工具测试"""

    def test_get_file_info(self, tmp_path):
        """获取文件信息"""
        f = tmp_path / "info.txt"
        f.write_text("test content")
        result = get_file_info(str(f))
        assert result["ok"] is True
        assert result["type"] == "file"
        assert result["size"] == len("test content")
        assert "modified" in result
        assert "created" in result

    def test_get_dir_info(self, tmp_path):
        """获取目录信息"""
        result = get_file_info(str(tmp_path))
        assert result["ok"] is True
        assert result["type"] == "dir"

    def test_get_info_not_found(self, tmp_path):
        """路径不存在"""
        result = get_file_info(str(tmp_path / "nonexistent"))
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_get_info_protected_path(self):
        """访问保护路径应被拒绝"""
        if os.name == "nt":
            path = r"C:\Windows\System32"
        else:
            path = "/etc"
        result = get_file_info(path)
        assert result["ok"] is False

    def test_get_file_extension(self, tmp_path):
        """文件扩展名应正确返回"""
        f = tmp_path / "script.py"
        f.write_text("print('hello')")
        result = get_file_info(str(f))
        assert result["extension"] == ".py"
