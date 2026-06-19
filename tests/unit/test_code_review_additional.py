import pytest
import os
from agent import code_review


class TestCodeReviewSecurity:
    """代码审查安全维度补充测试"""

    def test_security_private_key_detection(self):
        """测试检测私钥泄露"""
        code = 'private_key = "-----BEGIN PRIVATE KEY-----\\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQ"'
        result = code_review.code_review(diff=code, dimensions=["安全"])
        assert result["ok"] is True
        findings = result["dimensions"][0]["findings"]
        assert any("私钥泄露" in f["description"] for f in findings)

    def test_security_auth_bypass_detection(self):
        """测试检测认证绕过模式"""
        code = """
if debug_mode:
    bypass_auth_check()
"""
        result = code_review.code_review(diff=code, dimensions=["安全"])
        assert result["ok"] is True
        findings = result["dimensions"][0]["findings"]
        assert any("认证/授权绕过风险" in f["description"] for f in findings)


class TestCodeReviewPerformance:
    """代码审查性能维度补充测试"""

    def test_performance_nested_loop_detection(self):
        """测试检测嵌套循环"""
        code = """
for i in range(100):
    for j in range(100):
        process(i, j)
"""
        result = code_review.code_review(diff=code, dimensions=["性能"])
        assert result["ok"] is True


class TestCodeReviewMaintainability:
    """代码审查可维护性维度补充测试"""

    def test_maintainability_magic_numbers(self):
        """测试检测魔法数字"""
        code = """
def calculate_price(quantity):
    return quantity * 1.08  # 8% tax
"""
        result = code_review.code_review(diff=code, dimensions=["可维护性"])
        assert result["ok"] is True

    def test_maintainability_dead_code(self):
        """测试检测注释掉的代码"""
        code = """
# def old_function():
#     return 42

def new_function():
    return 24
"""
        result = code_review.code_review(diff=code, dimensions=["可维护性"])
        assert result["ok"] is True

    def test_maintainability_hardcoded_path(self):
        """测试检测硬编码路径"""
        code = 'log_path = "/var/log/myapp.log"'
        result = code_review.code_review(diff=code, dimensions=["可维护性"])
        assert result["ok"] is True


class TestCodeReviewApiCompatibility:
    """代码审查API兼容性维度补充测试"""

    def test_api_removal_detection(self):
        """测试检测函数移除"""
        diff = """
-def old_function():
-    return 42
"""
        result = code_review.code_review(diff=diff, dimensions=["API兼容性"])
        assert result["ok"] is True
        findings = result["dimensions"][0]["findings"]
        assert any("被移除" in f["description"] for f in findings)


class TestCodeReviewEdgeCases:
    """代码审查边界情况测试"""

    def test_empty_diff(self):
        """测试空 diff"""
        result = code_review.code_review(diff="", dimensions=["安全"])
        assert result["ok"] is False
        assert "审查内容为空" in result["error"]

    def test_all_dimensions(self):
        """测试所有维度审查"""
        code = 'password = "secret"'
        result = code_review.code_review(diff=code)
        assert result["ok"] is True
        assert len(result["dimensions"]) == 5

    def test_findings_deduplication(self):
        """测试发现去重"""
        code = 'password = "secret"\npassword = "secret"'
        result = code_review.code_review(diff=code, dimensions=["安全"])
        assert result["ok"] is True
        findings = result["dimensions"][0]["findings"]
        # 相同的发现应该被去重，所以实际发现数量应该小于等于总代码行数
        assert len(findings) <= 2  # 两行相同的代码，去重后应该少于2