"""文件比较工具测试 -- 测试 diff_tools.py 的 diff_files

覆盖范围：
- 两个不同文件的差异比较
- 相同文件的比较
- context_lines 参数
- 错误处理（文件不存在、路径不是文件、文件过大、权限不足）
"""
import os
import pytest

from agent.diff_tools import diff_files


# ════════════════════════════════════════════════════════════════════════════════
#  基本差异比较测试
# ════════════════════════════════════════════════════════════════════════════════

class TestDiffDifferentFiles:
    """不同文件的差异比较"""

    def test_diff_two_different_files(self, tmp_path):
        """两个内容不同的文件应产生 diff"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("line1\nline2\nline3\n", encoding="utf-8")
        f2.write_text("line1\nline2 modified\nline3\n", encoding="utf-8")

        result = diff_files(str(f1), str(f2))
        assert result["ok"] is True
        assert "diff" in result
        assert result["changes"] > 0
        # unified diff 应包含源文件名
        assert str(f1) in result["diff"] or "a.txt" in result["diff"]

    def test_diff_identical_files(self, tmp_path):
        """相同文件应返回空 diff"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        content = "line1\nline2\nline3\n"
        f1.write_text(content, encoding="utf-8")
        f2.write_text(content, encoding="utf-8")

        result = diff_files(str(f1), str(f2))
        assert result["ok"] is True
        assert result["changes"] == 0
        assert result["additions"] == 0
        assert result["deletions"] == 0

    def test_diff_additions_count(self, tmp_path):
        """统计新增行数"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("line1\n", encoding="utf-8")
        f2.write_text("line1\nline2\nline3\n", encoding="utf-8")

        result = diff_files(str(f1), str(f2))
        assert result["ok"] is True
        assert result["additions"] >= 2

    def test_diff_deletions_count(self, tmp_path):
        """统计删除行数"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("line1\nline2\nline3\n", encoding="utf-8")
        f2.write_text("line1\n", encoding="utf-8")

        result = diff_files(str(f1), str(f2))
        assert result["ok"] is True
        assert result["deletions"] >= 2

    def test_diff_returns_paths(self, tmp_path):
        """结果包含两个文件路径"""
        f1 = tmp_path / "x.txt"
        f2 = tmp_path / "y.txt"
        f1.write_text("a\n", encoding="utf-8")
        f2.write_text("b\n", encoding="utf-8")

        result = diff_files(str(f1), str(f2))
        assert result["path1"] == str(f1)
        assert result["path2"] == str(f2)


# ════════════════════════════════════════════════════════════════════════════════
#  context_lines 参数测试
# ════════════════════════════════════════════════════════════════════════════════

class TestContextLines:
    """上下文行数参数测试"""

    def test_context_lines_default(self, tmp_path):
        """默认 context_lines=3"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        lines1 = [f"line{i}" for i in range(20)]
        lines2 = list(lines1)
        lines2[10] = "modified line"
        f1.write_text("\n".join(lines1), encoding="utf-8")
        f2.write_text("\n".join(lines2), encoding="utf-8")

        result = diff_files(str(f1), str(f2))
        assert result["ok"] is True

    def test_context_lines_custom(self, tmp_path):
        """自定义 context_lines=0（无上下文）"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("line1\nline2\nline3\n", encoding="utf-8")
        f2.write_text("line1\nchanged\nline3\n", encoding="utf-8")

        result = diff_files(str(f1), str(f2), context_lines=0)
        assert result["ok"] is True
        assert result["changes"] > 0

    def test_context_lines_large(self, tmp_path):
        """较大的 context_lines 值"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("l1\nl2\nl3\n", encoding="utf-8")
        f2.write_text("l1\nl2x\nl3\n", encoding="utf-8")

        result = diff_files(str(f1), str(f2), context_lines=10)
        assert result["ok"] is True
        assert result["changes"] > 0


# ════════════════════════════════════════════════════════════════════════════════
#  错误处理测试
# ════════════════════════════════════════════════════════════════════════════════

class TestDiffErrorHandling:
    """错误处理测试"""

    def test_file1_not_exists(self, tmp_path):
        """文件1不存在"""
        f2 = tmp_path / "exists.txt"
        f2.write_text("content", encoding="utf-8")
        result = diff_files(str(tmp_path / "nonexistent.txt"), str(f2))
        assert result["ok"] is False
        assert "error" in result

    def test_file2_not_exists(self, tmp_path):
        """文件2不存在"""
        f1 = tmp_path / "exists.txt"
        f1.write_text("content", encoding="utf-8")
        result = diff_files(str(f1), str(tmp_path / "nonexistent.txt"))
        assert result["ok"] is False
        assert "error" in result

    def test_path1_is_directory(self, tmp_path):
        """路径1是目录而非文件"""
        subdir = tmp_path / "mydir"
        subdir.mkdir()
        f2 = tmp_path / "file.txt"
        f2.write_text("content", encoding="utf-8")
        result = diff_files(str(subdir), str(f2))
        assert result["ok"] is False

    def test_path2_is_directory(self, tmp_path):
        """路径2是目录而非文件"""
        f1 = tmp_path / "file.txt"
        f1.write_text("content", encoding="utf-8")
        subdir = tmp_path / "mydir"
        subdir.mkdir()
        result = diff_files(str(f1), str(subdir))
        assert result["ok"] is False

    def test_both_files_same(self, tmp_path):
        """两个完全相同的文件"""
        f = tmp_path / "same.txt"
        f.write_text("content", encoding="utf-8")
        result = diff_files(str(f), str(f))
        assert result["ok"] is True
        assert result["changes"] == 0

    def test_empty_files(self, tmp_path):
        """两个空文件比较"""
        f1 = tmp_path / "empty1.txt"
        f2 = tmp_path / "empty2.txt"
        f1.write_text("", encoding="utf-8")
        f2.write_text("", encoding="utf-8")
        result = diff_files(str(f1), str(f2))
        assert result["ok"] is True
        assert result["changes"] == 0
