"""Compatibility 单元测试"""
import pytest
import sys

from agent.utils.compatibility import (
    get_python_version,
    get_python_version_string,
    get_platform,
    get_os_name,
    is_python_version_compatible,
    is_platform_supported,
    check_compatibility,
    get_compatibility_report,
    assert_python_version,
    assert_platform,
)


class TestCompatibilityFunctions:
    """测试兼容性函数"""

    def test_get_python_version(self):
        """测试获取 Python 版本"""
        version = get_python_version()
        
        assert isinstance(version, tuple)
        assert len(version) == 3
        assert version[0] == sys.version_info.major
        assert version[1] == sys.version_info.minor

    def test_get_python_version_string(self):
        """测试获取 Python 版本字符串"""
        version_str = get_python_version_string()
        
        assert isinstance(version_str, str)
        assert version_str == f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    def test_get_platform(self):
        """测试获取平台信息"""
        platform = get_platform()
        
        assert platform in ["Windows", "Linux", "Darwin"]

    def test_get_os_name(self):
        """测试获取 OS 名称"""
        os_name = get_os_name()
        
        assert os_name in ["nt", "posix"]

    def test_is_python_version_compatible(self):
        """测试 Python 版本兼容性"""
        result = is_python_version_compatible()
        
        assert isinstance(result, bool)

    def test_is_platform_supported(self):
        """测试平台支持性"""
        result = is_platform_supported()
        
        assert isinstance(result, bool)

    def test_check_compatibility(self):
        """测试兼容性检查"""
        result = check_compatibility()
        
        assert "python_version" in result
        assert "platform" in result
        assert "python_compatible" in result
        assert "platform_supported" in result
        assert "known_issues" in result
        assert isinstance(result["python_compatible"], bool)
        assert isinstance(result["platform_supported"], bool)

    def test_get_compatibility_report(self):
        """测试获取兼容性报告"""
        report = get_compatibility_report()
        
        assert isinstance(report, str)
        assert "云枢系统兼容性检查报告" in report
        assert "Python" in report
        assert "操作系统" in report

    def test_assert_python_version(self):
        """测试 Python 版本断言"""
        try:
            assert_python_version()
        except RuntimeError:
            pytest.skip("Python version not compatible")

    def test_assert_platform(self):
        """测试平台断言"""
        try:
            assert_platform()
        except RuntimeError:
            pytest.skip("Platform not supported")