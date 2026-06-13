"""
兼容性模块测试
"""

import pytest

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
)


class TestVersionFunctions:
    """测试版本相关函数"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_python_version(self):
        """测试获取Python版本"""
        version = get_python_version()
        assert isinstance(version, tuple)
        assert len(version) == 3
        assert all(isinstance(v, int) for v in version)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_python_version_string(self):
        """测试获取Python版本字符串"""
        version_str = get_python_version_string()
        assert isinstance(version_str, str)
        parts = version_str.split(".")
        assert len(parts) == 3

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_python_version_requirement(self):
        """测试获取Python版本要求"""
        requirement = get_python_version_requirement()
        assert isinstance(requirement, str)
        assert "3." in requirement


class TestPlatformFunctions:
    """测试平台相关函数"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_platform(self):
        """测试获取平台名称"""
        platform = get_platform()
        assert platform in ["Windows", "Linux", "Darwin"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_os_name(self):
        """测试获取操作系统名称"""
        os_name = get_os_name()
        assert os_name in ["nt", "posix"]


class TestCompatibilityChecks:
    """测试兼容性检查"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_is_python_version_compatible(self):
        """测试Python版本兼容性检查"""
        result = is_python_version_compatible()
        assert isinstance(result, bool)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_is_platform_supported(self):
        """测试平台支持检查"""
        result = is_platform_supported()
        assert isinstance(result, bool)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_check_compatibility(self):
        """测试完整兼容性检查"""
        result = check_compatibility()
        assert isinstance(result, dict)
        assert "python_version" in result
        assert "python_compatible" in result
        assert "platform" in result
        assert "platform_supported" in result
        assert "known_issues" in result
        assert "supported_platforms" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_compatibility_report(self):
        """测试获取兼容性报告"""
        report = get_compatibility_report()
        assert isinstance(report, str)
        assert "兼容性检查报告" in report
        assert "Python" in report


class TestAssertions:
    """测试断言函数"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_assert_python_version_success(self):
        """测试Python版本断言成功"""
        # 当前环境应该满足版本要求
        try:
            assert_python_version()
        except RuntimeError:
            pytest.fail("Python版本断言不应该失败")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_assert_platform_success(self):
        """测试平台断言成功"""
        try:
            assert_platform()
        except RuntimeError:
            pytest.fail("平台断言不应该失败")


class TestImportFunctions:
    """测试导入辅助函数"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_import_with_fallback_success(self):
        """测试成功导入模块"""
        os_module = import_with_fallback("os")
        assert os_module is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_import_with_fallback_fallback_module(self):
        """测试使用备用模块"""
        # 使用存在的模块作为fallback
        result = import_with_fallback("nonexistent_module_xyz123", "os")
        assert result is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_import_with_fallback_fallback_value(self):
        """测试使用备用值"""
        result = import_with_fallback("nonexistent_module_xyz123", fallback_value="fallback")
        assert result == "fallback"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_platform_specific_import_success(self):
        """测试平台特定导入成功"""
        module_map = {get_platform(): "os"}
        result = get_platform_specific_import("test", module_map)
        assert result is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_platform_specific_import_failure(self):
        """测试平台特定导入失败"""
        module_map = {"UnknownPlatform": "nonexistent_module"}
        with pytest.raises(ImportError):
            get_platform_specific_import("test", module_map)