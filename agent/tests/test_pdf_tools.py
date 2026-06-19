"""PDF 处理工具测试 — 测试 pdf_tools.py 中的函数

覆盖范围：
- read_pdf_text — 读取文本、页码范围、文件不存在、缺少依赖
- merge_pdfs — 合并多个 PDF、输出路径、空列表
- split_pdf — 按范围拆分、每页拆为一个文件
- get_pdf_info — 元信息读取、文件不存在

策略：Mock pypdf 库，避免真实 PDF 文件依赖。
"""
import os
import pytest
from unittest.mock import patch, MagicMock, mock_open


# ── 模拟 pypdf 对象 ──

@pytest.fixture
def mock_pypdf():
    """全局 patch pypdf，模拟 PdfReader 和 PdfWriter"""
    with patch("agent.pdf_tools._check_library") as mock_check:
        # 让 _check_library 返回模拟的 pypdf 模块
        mock_pypdf_mod = MagicMock()
        mock_pypdf_mod.__name__ = "pypdf"

        # 模拟 PdfReader
        mock_reader_cls = MagicMock()
        mock_reader = MagicMock()
        mock_page_1 = MagicMock()
        mock_page_1.extract_text.return_value = "第1页内容"
        mock_page_2 = MagicMock()
        mock_page_2.extract_text.return_value = "第2页内容"
        mock_reader.pages = [mock_page_1, mock_page_2]
        mock_reader_cls.return_value = mock_reader
        mock_pypdf_mod.PdfReader = mock_reader_cls

        # 模拟 PdfWriter
        mock_writer = MagicMock()
        mock_writer_cls = MagicMock(return_value=mock_writer)
        mock_pypdf_mod.PdfWriter = mock_writer_cls

        mock_check.return_value = (mock_pypdf_mod, None)

        # 需要延迟导入以保证 patch 生效
        from agent.pdf_tools import (
            read_pdf_text, merge_pdfs, split_pdf, get_pdf_info,
        )
        yield {
            "read_pdf_text": read_pdf_text,
            "merge_pdfs": merge_pdfs,
            "split_pdf": split_pdf,
            "get_pdf_info": get_pdf_info,
            "mock_pypdf_mod": mock_pypdf_mod,
            "mock_reader": mock_reader,
            "mock_writer": mock_writer,
        }


# ════════════════════════════════════════════════════════════════════════════════
#  read_pdf_text 测试
# ════════════════════════════════════════════════════════════════════════════════

class TestReadPdf:
    """read_pdf_text 测试"""

    def test_read_all_pages(self, mock_pypdf, tmp_path):
        """读取全部页面"""
        pdf_path = str(tmp_path / "test.pdf")
        # 创建空文件让 os.path.exists 通过
        (tmp_path / "test.pdf").write_text("dummy")

        func = mock_pypdf["read_pdf_text"]
        result = func(pdf_path)

        assert result["ok"] is True
        assert result["pages"] == 2
        assert result["extracted_pages"] == 2
        assert "第1页内容" in result["text"]
        assert "第2页内容" in result["text"]

    def test_read_specific_pages(self, mock_pypdf, tmp_path):
        """读取指定页码"""
        pdf_path = str(tmp_path / "test.pdf")
        (tmp_path / "test.pdf").write_text("dummy")

        func = mock_pypdf["read_pdf_text"]
        result = func(pdf_path, pages=[1])  # 仅第1页

        assert result["ok"] is True
        assert result["extracted_pages"] == 1
        assert "第1页内容" in result["text"]
        assert "第2页内容" not in result["text"]

    def test_read_file_not_found(self, mock_pypdf):
        """文件不存在"""
        func = mock_pypdf["read_pdf_text"]
        result = func("/nonexistent/file.pdf")
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_read_dependency_missing(self):
        """缺少 pypdf 依赖"""
        with patch("agent.pdf_tools._check_library") as mock_check:
            mock_check.return_value = (None, "缺少依赖库 'pypdf'")
            from agent.pdf_tools import read_pdf_text
            result = read_pdf_text("/dummy.pdf")
            assert result["ok"] is False
            assert "pypdf" in result["error"]


# ════════════════════════════════════════════════════════════════════════════════
#  merge_pdfs 测试
# ════════════════════════════════════════════════════════════════════════════════

class TestMergePdfs:
    """merge_pdfs 测试"""

    def test_merge_success(self, mock_pypdf, tmp_path):
        """成功合并多个 PDF"""
        src1 = tmp_path / "a.pdf"
        src2 = tmp_path / "b.pdf"
        src1.write_text("dummy")
        src2.write_text("dummy")
        out = tmp_path / "merged.pdf"

        func = mock_pypdf["merge_pdfs"]
        result = func([str(src1), str(src2)], str(out))

        assert result["ok"] is True
        assert result["merged_files"] == 2

    def test_merge_empty_list(self, mock_pypdf, tmp_path):
        """空文件列表"""
        func = mock_pypdf["merge_pdfs"]
        result = func([], str(tmp_path / "out.pdf"))
        assert result["ok"] is False
        assert "空" in result["error"]

    def test_merge_missing_file(self, mock_pypdf, tmp_path):
        """某个源文件不存在"""
        func = mock_pypdf["merge_pdfs"]
        result = func([str(tmp_path / "nonexistent.pdf")], str(tmp_path / "out.pdf"))
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_merge_dependency_missing(self):
        """缺少 pypdf"""
        with patch("agent.pdf_tools._check_library") as mock_check:
            mock_check.return_value = (None, "缺少依赖库 'pypdf'")
            from agent.pdf_tools import merge_pdfs
            result = merge_pdfs(["a.pdf"], "out.pdf")
            assert result["ok"] is False
            assert "pypdf" in result["error"]


# ════════════════════════════════════════════════════════════════════════════════
#  split_pdf 测试
# ════════════════════════════════════════════════════════════════════════════════

class TestSplitPdf:
    """split_pdf 测试"""

    def test_split_all_pages(self, mock_pypdf, tmp_path):
        """每页拆为一个文件"""
        src = tmp_path / "source.pdf"
        src.write_text("dummy")
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        func = mock_pypdf["split_pdf"]
        result = func(str(src), str(out_dir))

        assert result["ok"] is True
        assert result["total_outputs"] == 2

    def test_split_with_ranges(self, mock_pypdf, tmp_path):
        """按指定范围拆分"""
        src = tmp_path / "source.pdf"
        src.write_text("dummy")
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        func = mock_pypdf["split_pdf"]
        result = func(str(src), str(out_dir), ranges=[[1, 1], [2, 2]])

        assert result["ok"] is True
        assert result["total_outputs"] == 2

    def test_split_file_not_found(self, mock_pypdf):
        """源文件不存在"""
        func = mock_pypdf["split_pdf"]
        result = func("/nonexistent.pdf", "/tmp/out")
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_split_dependency_missing(self):
        """缺少 pypdf"""
        with patch("agent.pdf_tools._check_library") as mock_check:
            mock_check.return_value = (None, "缺少依赖库 'pypdf'")
            from agent.pdf_tools import split_pdf
            result = split_pdf("/dummy.pdf", "/tmp/out")
            assert result["ok"] is False
            assert "pypdf" in result["error"]


# ════════════════════════════════════════════════════════════════════════════════
#  get_pdf_info 测试
# ════════════════════════════════════════════════════════════════════════════════

class TestGetPdfInfo:
    """get_pdf_info 测试"""

    def test_get_info_success(self, mock_pypdf, tmp_path):
        """获取 PDF 元信息"""
        pdf_path = str(tmp_path / "test.pdf")
        (tmp_path / "test.pdf").write_text("dummy")

        # 设置元数据
        mock_reader = mock_pypdf["mock_reader"]
        mock_reader.metadata = MagicMock()
        mock_reader.metadata.title = "测试文档"
        mock_reader.metadata.author = "作者"
        mock_reader.metadata.subject = "主题"
        mock_reader.metadata.creator = "创建者"

        func = mock_pypdf["get_pdf_info"]
        result = func(pdf_path)

        assert result["ok"] is True
        assert result["info"]["pages"] == 2
        assert result["info"]["title"] == "测试文档"
        assert result["info"]["author"] == "作者"

    def test_get_info_no_metadata(self, mock_pypdf, tmp_path):
        """PDF 无元数据"""
        pdf_path = str(tmp_path / "test.pdf")
        (tmp_path / "test.pdf").write_text("dummy")

        mock_reader = mock_pypdf["mock_reader"]
        mock_reader.metadata = None

        func = mock_pypdf["get_pdf_info"]
        result = func(pdf_path)

        assert result["ok"] is True
        assert result["info"]["pages"] == 2

    def test_get_info_file_not_found(self, mock_pypdf):
        """文件不存在"""
        func = mock_pypdf["get_pdf_info"]
        result = func("/nonexistent.pdf")
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_get_info_dependency_missing(self):
        """缺少 pypdf"""
        with patch("agent.pdf_tools._check_library") as mock_check:
            mock_check.return_value = (None, "缺少依赖库 'pypdf'")
            from agent.pdf_tools import get_pdf_info
            result = get_pdf_info("/dummy.pdf")
            assert result["ok"] is False
            assert "pypdf" in result["error"]
