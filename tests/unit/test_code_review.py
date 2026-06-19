import pytest
import os
import logging
from agent import code_review

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class TestCodeReview:
    """代码审查工具测试"""

    def test_code_review_no_input(self):
        """测试无输入时的提示信息"""
        logger.info("="*60)
        logger.info("开始测试: test_code_review_no_input")
        
        logger.info("调用 code_review() 无参数...")
        result = code_review.code_review()
        logger.info(f"返回结果: ok={result.get('ok')}, available_dimensions={result.get('available_dimensions')}")
        
        assert result["ok"] is True
        assert "available_dimensions" in result
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_code_review_empty_content(self):
        """测试空内容应报错"""
        logger.info("="*60)
        logger.info("开始测试: test_code_review_empty_content")
        
        logger.info("调用 code_review() 空内容...")
        result = code_review.code_review(path="", diff="", dimensions=["安全"])
        logger.info(f"返回结果: ok={result.get('ok')}, error={result.get('error')}")
        
        assert result["ok"] is False
        assert "审查内容为空" in result["error"]
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_code_review_security_dimension(self):
        """测试安全维度审查"""
        logger.info("="*60)
        logger.info("开始测试: test_code_review_security_dimension")
        
        code_with_secret = 'password = "secret123"\napi_key = "sk-abcdefghijklmnopqrstuvwxyz"'
        logger.info(f"测试代码:\n{code_with_secret}")
        
        logger.info("调用安全维度审查...")
        result = code_review.code_review(diff=code_with_secret, dimensions=["安全"])
        logger.info(f"返回结果: ok={result.get('ok')}")
        logger.info(f"维度数量: {len(result.get('dimensions', []))}")
        
        assert result["ok"] is True
        assert len(result["dimensions"]) == 1
        
        security_result = result["dimensions"][0]
        logger.info(f"维度: {security_result.get('dimension')}, 严重程度: {security_result.get('severity')}")
        logger.info(f"发现数量: {len(security_result.get('findings', []))}")
        
        assert security_result["dimension"] == "安全"
        assert security_result["severity"] == "CRITICAL"
        assert len(security_result["findings"]) > 0
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_code_review_sql_injection_pattern(self):
        """测试 SQL 注入模式检测"""
        logger.info("="*60)
        logger.info("开始测试: test_code_review_sql_injection_pattern")
        
        code = 'query = f"SELECT * FROM users WHERE id={user_id}"'
        logger.info(f"测试代码: {code}")
        
        logger.info("调用安全维度审查检测 SQL 注入...")
        result = code_review.code_review(diff=code, dimensions=["安全"])
        
        assert result["ok"] is True
        findings = result["dimensions"][0]["findings"]
        logger.info(f"发现数量: {len(findings)}")
        for i, finding in enumerate(findings):
            logger.info(f"  发现 {i+1}: {finding.get('description')}")
        
        assert any("SQL注入" in f["description"] for f in findings), "未检测到 SQL 注入模式"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_code_review_xss_pattern(self):
        """测试 XSS 模式检测"""
        logger.info("="*60)
        logger.info("开始测试: test_code_review_xss_pattern")
        
        code = 'element.innerHTML = "<div>" + user_input + "</div>"'
        logger.info(f"测试代码: {code}")
        
        logger.info("调用安全维度审查检测 XSS...")
        result = code_review.code_review(diff=code, dimensions=["安全"])
        
        assert result["ok"] is True
        findings = result["dimensions"][0]["findings"]
        logger.info(f"发现数量: {len(findings)}")
        for i, finding in enumerate(findings):
            logger.info(f"  发现 {i+1}: {finding.get('description')}")
        
        assert any("XSS" in f["description"] for f in findings), "未检测到 XSS 模式"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_code_review_command_injection(self):
        """测试命令注入模式检测"""
        logger.info("="*60)
        logger.info("开始测试: test_code_review_command_injection")
        
        code = 'subprocess.run("ls " + path, shell=True)'
        logger.info(f"测试代码: {code}")
        
        logger.info("调用安全维度审查检测命令注入...")
        result = code_review.code_review(diff=code, dimensions=["安全"])
        
        assert result["ok"] is True
        findings = result["dimensions"][0]["findings"]
        logger.info(f"发现数量: {len(findings)}")
        for i, finding in enumerate(findings):
            logger.info(f"  发现 {i+1}: {finding.get('description')}")
        
        assert any("命令注入" in f["description"] for f in findings), "未检测到命令注入模式"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_code_review_performance_dimension(self):
        """测试性能维度审查"""
        logger.info("="*60)
        logger.info("开始测试: test_code_review_performance_dimension")
        
        code = """
async def slow_function():
    time.sleep(5)
    requests.get("http://example.com")
"""
        logger.info(f"测试代码:\n{code}")
        
        logger.info("调用性能维度审查...")
        result = code_review.code_review(diff=code, dimensions=["性能"])
        
        assert result["ok"] is True
        assert len(result["dimensions"]) == 1
        
        perf_result = result["dimensions"][0]
        logger.info(f"维度: {perf_result.get('dimension')}")
        
        assert perf_result["dimension"] == "性能"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_code_review_maintainability_dimension(self):
        """测试可维护性维度审查"""
        logger.info("="*60)
        logger.info("开始测试: test_code_review_maintainability_dimension")
        
        code = """
# This is commented code
# def old_function():
#     return 42

MAGIC_NUMBER = 42
"""
        logger.info(f"测试代码:\n{code}")
        
        logger.info("调用可维护性维度审查...")
        result = code_review.code_review(diff=code, dimensions=["可维护性"])
        
        assert result["ok"] is True
        assert len(result["dimensions"]) == 1
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_code_review_api_compatibility(self):
        """测试 API 兼容性维度审查"""
        logger.info("="*60)
        logger.info("开始测试: test_code_review_api_compatibility")
        
        diff = """
-def old_function():
+def new_function():
    pass
"""
        logger.info(f"测试 diff:\n{diff}")
        
        logger.info("调用 API 兼容性维度审查...")
        result = code_review.code_review(diff=diff, dimensions=["API兼容性"])
        
        assert result["ok"] is True
        findings = result["dimensions"][0]["findings"]
        logger.info(f"发现数量: {len(findings)}")
        for i, finding in enumerate(findings):
            logger.info(f"  发现 {i+1}: {finding.get('description')}")
        
        assert any("破坏性变更" in f["description"] for f in findings), "未检测到破坏性变更"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_code_review_testing_dimension(self):
        """测试测试维度审查"""
        logger.info("="*60)
        logger.info("开始测试: test_code_review_testing_dimension")
        
        test_code = """
def test_something():
    assert 1 == 1
    assert 2 == 2
"""
        logger.info(f"测试代码:\n{test_code}")
        
        logger.info("调用测试维度审查...")
        result = code_review.code_review(path="test_example.py", diff=test_code, dimensions=["测试"])
        
        assert result["ok"] is True
        assert len(result["dimensions"]) == 1
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_code_review_multiple_dimensions(self):
        """测试多维度审查"""
        logger.info("="*60)
        logger.info("开始测试: test_code_review_multiple_dimensions")
        
        code = 'password = "secret"'
        logger.info(f"测试代码: {code}")
        
        logger.info("调用多维度审查: 安全、性能、可维护性...")
        result = code_review.code_review(diff=code, dimensions=["安全", "性能", "可维护性"])
        
        assert result["ok"] is True
        logger.info(f"维度数量: {len(result.get('dimensions', []))}")
        for dim in result["dimensions"]:
            logger.info(f"  维度: {dim.get('dimension')}, 发现数: {len(dim.get('findings', []))}")
        
        assert len(result["dimensions"]) == 3
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_code_review_unknown_dimension(self):
        """测试未知维度"""
        logger.info("="*60)
        logger.info("开始测试: test_code_review_unknown_dimension")
        
        logger.info("调用未知维度审查...")
        result = code_review.code_review(diff="x=1", dimensions=["未知维度"])
        
        assert result["ok"] is True
        dim_result = result["dimensions"][0]
        logger.info(f"维度: {dim_result.get('dimension')}, 严重程度: {dim_result.get('severity')}")
        
        assert dim_result["dimension"] == "未知维度"
        assert dim_result["severity"] == "UNKNOWN"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_read_file_content_not_exists(self):
        """测试读取不存在的文件"""
        logger.info("="*60)
        logger.info("开始测试: test_read_file_content_not_exists")
        
        logger.info("读取不存在的文件: /nonexistent/path/to/file.py")
        content = code_review._read_file_content("/nonexistent/path/to/file.py")
        logger.info(f"返回内容: '{content}'")
        
        assert content == ""
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_read_file_content_exists(self, tmp_path):
        """测试读取存在的文件"""
        logger.info("="*60)
        logger.info("开始测试: test_read_file_content_exists")
        
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        logger.info(f"创建测试文件: {test_file}")
        
        content = code_review._read_file_content(str(test_file))
        logger.info(f"读取内容: '{content}'")
        
        assert content == "print('hello')"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_iter_lines(self):
        """测试行迭代"""
        logger.info("="*60)
        logger.info("开始测试: test_iter_lines")
        
        content = "line1\nline2\nline3"
        logger.info(f"测试内容: '{content}'")
        
        lines = code_review._iter_lines(content)
        logger.info(f"行列表: {lines}")
        
        assert len(lines) == 3
        assert lines[0] == (1, "line1")
        assert lines[1] == (2, "line2")
        assert lines[2] == (3, "line3")
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_make_finding(self):
        """测试创建审查发现"""
        logger.info("="*60)
        logger.info("开始测试: test_make_finding")
        
        logger.info("创建发现: line=42, description='描述', suggestion='建议'")
        finding = code_review._make_finding(42, "描述", "建议")
        logger.info(f"发现: {finding}")
        
        assert finding["line"] == 42
        assert finding["description"] == "描述"
        assert finding["suggestion"] == "建议"
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_parse_python_ast_valid(self):
        """测试解析有效的 Python AST"""
        logger.info("="*60)
        logger.info("开始测试: test_parse_python_ast_valid")
        
        logger.info("解析有效代码: 'x = 1'")
        ast = code_review._parse_python_ast("x = 1")
        logger.info(f"AST: {ast}")
        
        assert ast is not None
        
        logger.info("测试通过!")
        logger.info("="*60)

    def test_parse_python_ast_invalid(self):
        """测试解析无效的 Python 代码"""
        logger.info("="*60)
        logger.info("开始测试: test_parse_python_ast_invalid")
        
        logger.info("解析无效代码: 'def foo('")
        ast = code_review._parse_python_ast("def foo(")
        logger.info(f"AST: {ast}")
        
        assert ast is None
        
        logger.info("测试通过!")
        logger.info("="*60)