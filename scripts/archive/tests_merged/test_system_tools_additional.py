import pytest
import os
from unittest.mock import patch, MagicMock
from agent.system_tools import (
    is_protected_path,
    is_binary_content,
    is_executable_extension,
    init_workspace,
    list_workspace,
    write_workspace,
    delete_workspace,
    get_file_info,
    get_process_whitelist,
)


class TestSystemToolsPathSecurity:
    """系统工具路径安全测试"""

    def test_is_protected_path_windows(self):
        """测试 Windows 系统保护路径"""
        assert is_protected_path('C:\\Windows\\System32') is True
        assert is_protected_path('C:\\Windows\\SysWOW64') is True
        assert is_protected_path('C:\\Users\\Administrator\\Documents') is False

    def test_is_binary_content(self):
        """测试二进制内容检测"""
        pdf_header = b'%PDF-1.7'
        assert is_binary_content(pdf_header) is False
        
        text_content = b'hello world'
        assert is_binary_content(text_content) is False
        
        binary_with_null = b'\x00\x01\x02'
        assert is_binary_content(binary_with_null) is True
        
        mostly_non_text = b'\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09hello'
        assert is_binary_content(mostly_non_text) is True

    def test_is_executable_extension(self):
        """测试可执行文件扩展名检测"""
        assert is_executable_extension('document.txt') is False
        assert is_executable_extension('image.png') is False


class TestSystemToolsWorkspace:
    """系统工具工作区操作测试"""

    def test_init_workspace(self, tmp_path):
        """测试初始化工作区"""
        with patch('agent.system_tools.WORKSPACE_DIR', str(tmp_path / 'workspace')):
            result = init_workspace()
            assert result == str(tmp_path / 'workspace')
            assert os.path.exists(str(tmp_path / 'workspace'))

    def test_list_workspace(self, tmp_path):
        """测试列出工作区内容"""
        workspace_dir = tmp_path / 'workspace'
        workspace_dir.mkdir()
        (workspace_dir / 'file1.txt').write_text('content1')
        (workspace_dir / 'file2.txt').write_text('content2')
        
        with patch('agent.system_tools.WORKSPACE_DIR', str(workspace_dir)):
            result = list_workspace()
            assert result['path'] == ''
            assert result['type'] == 'dir'
            assert len(result['items']) == 2

    def test_write_workspace(self, tmp_path):
        """测试写入工作区文件"""
        workspace_dir = tmp_path / 'workspace'
        workspace_dir.mkdir()
        
        with patch('agent.system_tools.WORKSPACE_DIR', str(workspace_dir)):
            result = write_workspace('test.txt', 'hello world')
            assert result['ok'] is True
            assert result['path'] == 'test.txt'
            assert (workspace_dir / 'test.txt').read_text() == 'hello world'

    def test_delete_workspace(self, tmp_path):
        """测试删除工作区文件"""
        workspace_dir = tmp_path / 'workspace'
        workspace_dir.mkdir()
        (workspace_dir / 'test.txt').write_text('content')
        
        with patch('agent.system_tools.WORKSPACE_DIR', str(workspace_dir)):
            result = delete_workspace('test.txt')
            assert result['ok'] is True
            assert not (workspace_dir / 'test.txt').exists()


class TestSystemToolsFileInfo:
    """系统工具文件信息测试"""

    def test_get_file_info_exists(self, tmp_path):
        """测试获取存在文件的信息"""
        test_file = tmp_path / 'test.txt'
        test_file.write_text('hello world')
        
        result = get_file_info(str(test_file))
        assert result is not None
        assert result.get('ok') is True
        assert result.get('type') == 'file'

    def test_get_file_info_not_exists(self):
        """测试获取不存在文件的信息"""
        result = get_file_info('/nonexistent/path/file.txt')
        assert result is not None


class TestSystemToolsWhitelist:
    """系统工具进程白名单测试"""

    def test_get_process_whitelist(self):
        """测试获取进程白名单"""
        whitelist = get_process_whitelist()
        assert isinstance(whitelist, list)