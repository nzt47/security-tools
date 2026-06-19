import pytest
import os
from unittest.mock import patch, MagicMock
from agent import pdf_tools


class TestPDFTools:
    """PDF 工具测试"""

    def test_read_pdf_text_file_not_exists(self):
        """测试读取不存在的 PDF 文件"""
        result = pdf_tools.read_pdf_text("/nonexistent/file.pdf")
        assert result["ok"] is False
        assert "文件不存在" in result["error"]

    def test_read_pdf_text_missing_library(self):
        """测试缺少 pypdf 库时的错误处理"""
        with patch("agent.pdf_tools._check_library", return_value=(None, "缺少依赖库")):
            result = pdf_tools.read_pdf_text("test.pdf")
            assert result["ok"] is False
            assert "缺少依赖库" in result["error"]

    def test_read_pdf_tables_file_not_exists(self):
        """测试提取不存在 PDF 的表格"""
        result = pdf_tools.read_pdf_tables("/nonexistent/file.pdf")
        assert result["ok"] is False
        assert "文件不存在" in result["error"]

    def test_read_pdf_tables_missing_library(self):
        """测试缺少 pdfplumber 库时的错误处理"""
        with patch("agent.pdf_tools._check_library", return_value=(None, "缺少依赖库")):
            result = pdf_tools.read_pdf_tables("test.pdf")
            assert result["ok"] is False
            assert "缺少依赖库" in result["error"]

    def test_merge_pdfs_empty_list(self):
        """测试合并空列表"""
        result = pdf_tools.merge_pdfs([], "output.pdf")
        assert result["ok"] is False
        assert "paths 列表为空" in result["error"]

    def test_merge_pdfs_missing_files(self):
        """测试合并不存在的文件"""
        result = pdf_tools.merge_pdfs(["/nonexistent/file1.pdf", "/nonexistent/file2.pdf"], "output.pdf")
        assert result["ok"] is False
        assert "文件不存在" in result["error"]

    def test_split_pdf_file_not_exists(self):
        """测试拆分不存在的 PDF"""
        result = pdf_tools.split_pdf("/nonexistent/file.pdf", "/tmp/output")
        assert result["ok"] is False
        assert "文件不存在" in result["error"]

    def test_split_pdf_default_ranges(self, tmp_path):
        """测试拆分 PDF（默认每页一个文件）"""
        mock_reader = MagicMock()
        mock_reader.pages = [MagicMock(), MagicMock(), MagicMock()]
        
        with patch("pypdf.PdfReader", return_value=mock_reader):
            result = pdf_tools.split_pdf("test.pdf", str(tmp_path))
            assert result["ok"] is True
            assert result["total_outputs"] == 3

    def test_get_pdf_info_file_not_exists(self):
        """测试获取不存在 PDF 的信息"""
        result = pdf_tools.get_pdf_info("/nonexistent/file.pdf")
        assert result["ok"] is False
        assert "文件不存在" in result["error"]

    def test_normalize_pages(self):
        """测试页码规范化"""
        # 正常情况
        assert pdf_tools._normalize_pages(10, [1, 5, 10]) == [0, 4, 9]
        
        # None 应返回所有页
        assert pdf_tools._normalize_pages(3, None) == [0, 1, 2]
        
        # 超出范围的页码应被过滤
        assert pdf_tools._normalize_pages(5, [0, 1, 6, 10]) == [0, 4]

    @patch("pypdf.PdfReader")
    def test_read_pdf_text_with_pages(self, mock_reader_class):
        """测试按指定页码读取 PDF 文本"""
        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Page 1 content"
        mock_page3 = MagicMock()
        mock_page3.extract_text.return_value = "Page 3 content"
        
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page1, MagicMock(), mock_page3]
        mock_reader_class.return_value = mock_reader
        
        result = pdf_tools.read_pdf_text("test.pdf", pages=[1, 3])
        assert result["ok"] is True
        assert "Page 1 content" in result["text"]
        assert "Page 3 content" in result["text"]
        assert result["pages"] == 3
        assert result["extracted_pages"] == 2

    @patch("pypdf.PdfWriter")
    @patch("pypdf.PdfReader")
    def test_merge_pdfs_success(self, mock_reader_class, mock_writer_class):
        """测试成功合并 PDF"""
        mock_page = MagicMock()
        mock_reader1 = MagicMock()
        mock_reader1.pages = [mock_page]
        mock_reader2 = MagicMock()
        mock_reader2.pages = [mock_page]
        
        mock_reader_class.side_effect = [mock_reader1, mock_reader2]
        mock_writer = MagicMock()
        mock_writer_class.return_value = mock_writer
        
        result = pdf_tools.merge_pdfs(["file1.pdf", "file2.pdf"], "output.pdf")
        assert result["ok"] is True
        assert result["total_pages"] == 2
        assert result["merged_files"] == 2
        mock_writer.write.assert_called_once()

    def test_check_library_installed(self):
        """测试检查已安装的库"""
        module, error = pdf_tools._check_library("os")
        assert module is not None
        assert error is None

    def test_check_library_not_installed(self):
        """测试检查未安装的库"""
        module, error = pdf_tools._check_library("nonexistent_library_xyz123")
        assert module is None
        assert error is not None
        assert "缺少依赖库" in error