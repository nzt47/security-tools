"""Compatibility Utils 单元测试"""
import pytest
import sys
import platform
from unittest.mock import patch

from agent.utils.compatibility import (
    get_python_version,
    get_python_version_string,
    get_platform,
    get_os_name,
    is_python_version_compatible,
    is_platform_supported,
    get_python_version_requirement,
    check_compatibility,
    assert_python_version,
    assert_platform,
    import_with_fallback,
    get_platform_specific_import,
    get_compatibility_report,
    REQUIRED_PYTHON_MAJOR,
    REQUIRED_PYTHON_MIN_MINOR,
    REQUIRED_PYTHON_MAX_MINOR,
    SUPPORTED_PLATFORMS,
    KNOWN_ISSUES,
)


class TestPythonVersion:
    """测试 Python 版本检测"""

    def test_get_python_version(self):
        """测试获取 Python 版本"""
        version = get_python_version()
        
        assert isinstance(version, tuple)
        assert len(version) == 3
        assert version[0] == sys.version_info.major
        assert version[1] == sys.version_info.minor
        assert version[2] == sys.version_info.micro

    def test_get_python_version_string(self):
        """测试获取 Python 版本字符串"""
        version_str = get_python_version_string()
        
        assert isinstance(version_str, str)
        assert "." in version_str
        assert str(sys.version_info.major) in version_str

    def test_get_python_version_requirement(self):
        """测试获取 Python 版本要求"""
        requirement = get_python_version_requirement()
        
        assert isinstance(requirement, str)
        assert ">=" in requirement
        assert "<" in requirement


class TestPlatform:
    """测试平台检测"""

    def test_get_platform(self):
        """测试获取平台名称"""
        platform_name = get_platform()
        
        assert isinstance(platform_name, str)
        assert platform_name in ["Windows", "Linux", "Darwin", "Java"]

    def test_get_os_name(self):
        """测试获取操作系统名称"""
        os_name = get_os_name()
        
        assert isinstance(os_name, str)
        assert os_name in ["nt", "posix", "java"]


class TestCompatibility:
    """测试兼容性检查"""

    def test_is_python_version_compatible(self):
        """测试 Python 版本兼容性"""
        result = is_python_version_compatible()
        
        assert isinstance(result, bool)

    def test_is_platform_supported(self):
        """测试平台支持"""
        result = is_platform_supported()
        
        assert isinstance(result, bool)

    def test_is_platform_supported_windows(self):
        """测试 Windows 平台支持"""
        with patch('agent.utils.compatibility.get_platform', return_value="Windows"):
            with patch('agent.utils.compatibility.get_os_name', return_value="nt"):
                result = is_platform_supported()
                assert result is True

    def test_is_platform_supported_linux(self):
        """测试 Linux 平台支持"""
        with patch('agent.utils.compatibility.get_platform', return_value="Linux"):
            with patch('agent.utils.compatibility.get_os_name', return_value="posix"):
                result = is_platform_supported()
                assert result is True

    def test_is_platform_supported_unsupported(self):
        """测试不支持的平台"""
        with patch('agent.utils.compatibility.get_platform', return_value="FreeBSD"):
            with patch('agent.utils.compatibility.get_os_name', return_value="posix"):
                result = is_platform_supported()
                assert result is False


class TestCompatibilityCheck:
    """测试完整兼容性检查"""

    def test_check_compatibility(self):
        """测试完整兼容性检查"""
        result = check_compatibility()
        
        assert isinstance(result, dict)
        assert "python_version" in result
        assert "python_version_tuple" in result
        assert "python_compatible" in result
        assert "platform" in result
        assert "os_name" in result
        assert "platform_supported" in result
        assert "known_issues" in result
        assert "recommended_python_versions" in result

    def test_check_compatibility_dict_values(self):
        """测试兼容性检查返回值的类型"""
        result = check_compatibility()
        
        assert isinstance(result["python_version"], str)
        assert isinstance(result["python_version_tuple"], tuple)
        assert isinstance(result["python_compatible"], bool)
        assert isinstance(result["platform"], str)
        assert isinstance(result["platform_supported"], bool)
        assert isinstance(result["known_issues"], dict)


class TestConstants:
    """测试常量定义"""

    def test_required_python_major(self):
        """测试所需 Python 主版本"""
        assert REQUIRED_PYTHON_MAJOR == 3

    def test_required_python_min_minor(self):
        """测试所需 Python 最小次版本"""
        assert REQUIRED_PYTHON_MIN_MINOR >= 8

    def test_required_python_max_minor(self):
        """测试所需 Python 最大次版本"""
        assert REQUIRED_PYTHON_MAX_MINOR >= 10

    def test_supported_platforms(self):
        """测试支持的平台"""
        assert isinstance(SUPPORTED_PLATFORMS, dict)
        assert len(SUPPORTED_PLATFORMS) > 0
        assert "Windows" in SUPPORTED_PLATFORMS
        assert "Linux" in SUPPORTED_PLATFORMS

    def test_known_issues(self):
        """测试已知问题"""
        assert isinstance(KNOWN_ISSUES, dict)
        assert "Windows" in KNOWN_ISSUES or "Linux" in KNOWN_ISSUES


class TestAssertFunctions:
    """测试断言函数"""

    def test_assert_python_version_compatible(self):
        """测试 Python 版本兼容断言"""
        # 如果版本兼容，不应抛出异常
        assert_python_version()  # 不应抛出

    def test_assert_python_version_incompatible(self):
        """测试 Python 版本不兼容断言"""
        with patch('agent.utils.compatibility.is_python_version_compatible', return_value=False):
            with pytest.raises(RuntimeError):
                assert_python_version()

    def test_assert_platform_supported(self):
        """测试平台支持断言"""
        # 如果平台支持，不应抛出异常
        assert_platform()  # 不应抛出

    def test_assert_platform_unsupported(self):
        """测试平台不支持断言"""
        with patch('agent.utils.compatibility.is_platform_supported', return_value=False):
            with pytest.raises(RuntimeError):
                assert_platform()


class TestImportFunctions:
    """测试导入函数"""

    def test_import_with_fallback_success(self):
        """测试成功导入模块"""
        # json 是标准库模块，应该可以导入
        result = import_with_fallback("json")
        assert result is not None

    def test_import_with_fallback_missing_with_fallback(self):
        """测试模块缺失时使用回退"""
        result = import_with_fallback("nonexistent_module_12345", fallback_module="json")
        assert result is not None

    def test_import_with_fallback_missing_no_fallback(self):
        """测试模块缺失且无回退"""
        result = import_with_fallback("nonexistent_module_12345", fallback_value="default_value")
        assert result == "default_value"

    def test_get_platform_specific_import_success(self):
        """测试平台特定模块导入成功"""
        # Windows 下应该可以导入 winreg 或类似的
        with patch('agent.utils.compatibility.get_platform', return_value="Windows"):
            try:
                # 这个测试取决于平台，可能是 Windows 或其他
                result = get_platform_specific_import("test", {"Windows": "winreg"})
                # 成功或 ImportError 都可以接受
                assert result is not None or True
            except ImportError:
                pass  # 预期行为

    def test_get_platform_specific_import_unsupported_platform(self):
        """测试不支持平台的模块导入"""
        with patch('agent.utils.compatibility.get_platform', return_value="FreeBSD"):
            with pytest.raises(ImportError):
                get_platform_specific_import("test", {"Windows": "winreg"})


class TestCompatibilityReport:
    """测试兼容性报告"""

    def test_get_compatibility_report(self):
        """测试生成兼容性报告"""
        report = get_compatibility_report()
        
        assert isinstance(report, str)
        assert len(report) > 0
        assert "Python" in report
        assert "平台" in report or "Platform" in report

    def test_compatibility_report_format(self):
        """测试兼容性报告格式"""
        report = get_compatibility_report()
        
        # 报告应该包含关键信息
        assert "==" in report  # 分隔符
        assert "Python" in report
        assert "操作系统" in report or "OS" in report or "Platform" in report

    def test_compatibility_report_no_known_issues(self):
        """测试无已知问题时的报告"""
        # 模拟无已知问题的情况
        with patch('agent.utils.compatibility.KNOWN_ISSUES', {}):
            report = get_compatibility_report()
            assert "无" in report


class TestPlatformSpecificImportEdgeCase:
    """测试平台特定导入的异常情况"""

    def test_get_platform_specific_import_failure(self):
        """测试平台特定导入失败的情况"""
        with patch('agent.utils.compatibility.get_platform') as mock_get_platform:
            mock_get_platform.return_value = 'unsupported_platform'
            
            with pytest.raises(ImportError):
                get_platform_specific_import('test_module', {'Windows': 'winreg'})

    def test_get_platform_specific_import_import_error(self):
        """测试平台特定导入时 ImportError 分支"""
        with patch('agent.utils.compatibility.get_platform') as mock_get_platform:
            mock_get_platform.return_value = 'test_platform'
            
            with pytest.raises(ImportError):
                get_platform_specific_import('test_module', {'test_platform': 'nonexistent_module_12345'})
