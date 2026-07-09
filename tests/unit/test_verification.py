"""校验工程模块测试

测试覆盖三个核心模块：
1. output_schema - 结构化输出约束
2. critic - LLM-as-Judge 自检评审
3. hitl - 高危操作人工确认流程
"""

import pytest
import json
import time
from unittest.mock import MagicMock, patch

# ==================== Output Schema 测试 ====================
from agent.guardrails.output_schema import (
    OutputSchemaValidator,
    OutputSchemaBuilder,
    OutputType,
    TextResponse,
    ToolCallOutput,
    ErrorMessage,
    SummaryReport,
    SchemaValidationError,
    ToolCall,
    ErrorDetail,
    SummarySection
)

# ==================== Critic 测试 ====================
from agent.cognitive.critic import (
    CriticEvaluator,
    EvaluationDimension,
    EvaluationResult,
    CriticMode
)

# ==================== HITL 测试 ====================
from agent.human_in_the_loop.hitl import (
    HITLManager,
    RiskLevel,
    ApprovalStatus,
    ConfirmationMode
)


class TestOutputSchema:
    """输出 Schema 模块测试"""
    
    def test_validate_text_response(self):
        """测试验证文本响应"""
        validator = OutputSchemaValidator()
        output = {
            "output_type": "text_response",
            "trace_id": "abc123",
            "timestamp": 1234567890,
            "version": "1.0",
            "content": "Hello World"
        }
        assert validator.validate(output) is True
    
    def test_validate_tool_call(self):
        """测试验证工具调用"""
        validator = OutputSchemaValidator()
        output = {
            "output_type": "tool_call",
            "trace_id": "abc123",
            "timestamp": 1234567890,
            "version": "1.0",
            "tool_calls": [{
                "tool_name": "search",
                "tool_params": {"query": "test"}
            }]
        }
        assert validator.validate(output) is True
    
    def test_validate_error_message(self):
        """测试验证错误消息"""
        validator = OutputSchemaValidator()
        output = {
            "output_type": "error_message",
            "trace_id": "abc123",
            "timestamp": 1234567890,
            "version": "1.0",
            "error": {
                "error_code": "TEST_ERROR",
                "message": "Test error"
            }
        }
        assert validator.validate(output) is True
    
    def test_validate_summary_report(self):
        """测试验证总结报告"""
        validator = OutputSchemaValidator()
        output = {
            "output_type": "summary_report",
            "trace_id": "abc123",
            "timestamp": 1234567890,
            "version": "1.0",
            "title": "Test Report",
            "sections": [{
                "title": "Section 1",
                "content": "Content"
            }]
        }
        assert validator.validate(output) is True
    
    def test_invalid_output_type(self):
        """测试无效输出类型"""
        validator = OutputSchemaValidator()
        output = {
            "output_type": "invalid_type",
            "trace_id": "abc123",
            "timestamp": 1234567890,
            "version": "1.0"
        }
        assert validator.validate(output) is False
    
    def test_missing_required_fields(self):
        """测试缺少必需字段"""
        validator = OutputSchemaValidator()
        output = {
            "output_type": "text_response",
            "trace_id": "abc123"
        }
        assert validator.validate(output) is False
    
    def test_parse_and_validate_success(self):
        """测试解析并验证成功"""
        validator = OutputSchemaValidator()
        output = json.dumps({
            "output_type": "text_response",
            "trace_id": "abc123",
            "timestamp": 1234567890,
            "version": "1.0",
            "content": "Hello"
        })
        result = validator.parse_and_validate(output)
        assert isinstance(result, TextResponse)
        assert result.content == "Hello"
    
    def test_parse_and_validate_failure(self):
        """测试解析并验证失败 — 无效 JSON 触发降级路径，返回降级 TextResponse"""
        validator = OutputSchemaValidator()
        output = "invalid json"
        result = validator.parse_and_validate(output)
        # 无效 JSON 经重试后触发降级，返回 degraded TextResponse 而非 ErrorMessage
        assert isinstance(result, TextResponse)
        assert result.source.startswith("degraded_")
    
    def test_builder_text(self):
        """测试构建器 - 文本响应"""
        result = OutputSchemaBuilder.text("Hello", confidence=0.9, source="test")
        assert isinstance(result, TextResponse)
        assert result.content == "Hello"
        assert result.confidence == 0.9
    
    def test_builder_tool_call(self):
        """测试构建器 - 工具调用"""
        result = OutputSchemaBuilder.tool_call("search", {"query": "test"}, "thinking")
        assert isinstance(result, ToolCallOutput)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "search"
    
    def test_builder_error(self):
        """测试构建器 - 错误消息"""
        result = OutputSchemaBuilder.error("CODE", "Message", "Suggestion")
        assert isinstance(result, ErrorMessage)
        assert result.error.error_code == "CODE"
    
    def test_builder_summary(self):
        """测试构建器 - 总结报告"""
        sections = [{"title": "Section 1", "content": "Content"}]
        result = OutputSchemaBuilder.summary("Title", sections, "Conclusion", ["Action 1"])
        assert isinstance(result, SummaryReport)
        assert result.title == "Title"


class TestCritic:
    """Critic 评估器测试"""
    
    def test_evaluate_passes_threshold(self):
        """测试评估通过阈值"""
        evaluator = CriticEvaluator(threshold=70)
        result = evaluator.evaluate(
            user_query="人工智能",
            response="人工智能（Artificial Intelligence，简称AI）是计算机科学的一个分支，致力于研究、开发用于模拟、延伸和扩展人的智能的理论、方法、技术及应用系统。人工智能领域的研究包括机器人、语言识别、图像识别、自然语言处理和专家系统等。人工智能是一门极富挑战性的科学，从事这项工作的人必须懂得计算机知识、心理学和哲学。"
        )
        assert result.passed is True
        assert result.overall_score >= 70
    
    def test_evaluate_fails_threshold(self):
        """测试评估未通过阈值"""
        evaluator = CriticEvaluator(threshold=70)
        result = evaluator.evaluate(
            user_query="什么是人工智能？",
            response="不知道"
        )
        assert result.passed is False
        assert result.overall_score < 70
    
    def test_evaluate_factual_accuracy(self):
        """测试事实性评估"""
        evaluator = CriticEvaluator()
        # 包含错误事实的响应
        result = evaluator.evaluate(
            user_query="地球是什么形状的？",
            response="地球是平的"
        )
        assert result.dimension_scores["factual_accuracy"] < 100
    
    def test_evaluate_relevance(self):
        """测试相关性评估"""
        evaluator = CriticEvaluator()
        # 无关响应
        result = evaluator.evaluate(
            user_query="什么是人工智能？",
            response="今天天气很好"
        )
        assert result.dimension_scores["relevance"] < 100
    
    def test_evaluate_completeness(self):
        """测试完整性评估"""
        evaluator = CriticEvaluator()
        # 简短回答
        result = evaluator.evaluate(
            user_query="什么是人工智能？",
            response="AI"
        )
        assert result.dimension_scores["completeness"] < 100
    
    def test_should_retry(self):
        """测试是否应该重试"""
        evaluator = CriticEvaluator(threshold=70, enable_retry=True, max_retries=3)
        result = EvaluationResult(
            overall_score=60,
            dimension_scores={},
            passed=False,
            feedback=[],
            retry_recommended=True
        )
        assert evaluator.should_retry(result, 0) is True
        assert evaluator.should_retry(result, 2) is True
        assert evaluator.should_retry(result, 3) is False
    
    def test_get_degradation_response(self):
        """测试获取降级响应"""
        evaluator = CriticEvaluator()
        result = EvaluationResult(
            overall_score=60,
            dimension_scores={},
            passed=False,
            feedback=["反馈1", "反馈2"],
            retry_recommended=False
        )
        response = evaluator.get_degradation_response(result)
        assert "我正在努力完善这个回答" in response
        assert "反馈1" in response
    
    def test_weighted_score_calculation(self):
        """测试加权评分计算"""
        evaluator = CriticEvaluator()
        scores = {
            "factual_accuracy": 80,
            "completeness": 70,
            "relevance": 90,
            "logic": 75,
            "clarity": 85
        }
        overall = evaluator._calculate_overall_score(scores)
        assert 70 <= overall <= 90


class TestHITL:
    """人机协同模块测试"""
    
    def test_assess_low_risk(self):
        """测试评估低风险操作"""
        hitl = HITLManager()
        risk = hitl.assess("read_file", {"path": "test.txt"})
        assert risk == RiskLevel.LOW
    
    def test_assess_high_risk(self):
        """测试评估高风险操作"""
        hitl = HITLManager()
        risk = hitl.assess("delete_file", {"path": "test.txt"})
        assert risk == RiskLevel.HIGH
    
    def test_assess_critical(self):
        """测试评估致命风险操作"""
        hitl = HITLManager()
        risk = hitl.assess("execute_shell", {"command": "rm -rf /"})
        assert risk == RiskLevel.CRITICAL
    
    def test_assess_database_write(self):
        """测试评估写库操作"""
        hitl = HITLManager()
        risk = hitl.assess("insert", {"table": "users"})
        assert risk == RiskLevel.MEDIUM
    
    def test_request_approval_low_risk_auto_approve(self):
        """测试低风险操作自动批准"""
        hitl = HITLManager()
        result = hitl.request_approval("read_file", {"path": "test.txt"})
        assert result.approved is True
        assert result.status == ApprovalStatus.APPROVED
    
    def test_request_approval_critical_rejected(self):
        """测试致命风险操作被拒绝"""
        hitl = HITLManager()
        result = hitl.request_approval("format", {"drive": "C:"})
        assert result.approved is False
        assert result.status == ApprovalStatus.REJECTED
    
    def test_request_async_approval(self):
        """测试异步确认请求"""
        hitl = HITLManager()
        callback_called = []
        
        def callback(result):
            callback_called.append(result)
        
        request_id = hitl.request_async_approval(
            "delete_file",
            {"path": "test.txt"},
            callback=callback
        )
        
        assert request_id != "auto-approved"
        assert request_id != "rejected"
        
        # 批准请求
        hitl.approve_request(request_id)
        
        # 等待回调执行
        time.sleep(0.1)
        
        assert len(callback_called) == 1
        assert callback_called[0].approved is True
    
    def test_approve_and_reject_request(self):
        """测试批准和拒绝请求"""
        hitl = HITLManager()
        
        # 创建异步请求
        request_id = hitl.request_async_approval(
            "delete_file",
            {"path": "test.txt"}
        )
        
        # 验证请求存在且待处理
        request = hitl.get_request_status(request_id)
        assert request is not None
        assert request.status == ApprovalStatus.PENDING
        
        # 批准请求
        result = hitl.approve_request(request_id, "test_user")
        assert result is True
        
        # 验证状态已更新
        request = hitl.get_request_status(request_id)
        assert request.status == ApprovalStatus.APPROVED
        assert request.approver == "test_user"
    
    def test_timeout_handler(self):
        """测试超时处理"""
        hitl = HITLManager(timeout_seconds=1)
        
        callback_called = []
        
        def callback(result):
            callback_called.append(result)
        
        hitl.request_async_approval(
            "delete_file",
            {"path": "test.txt"},
            callback=callback,
            timeout_seconds=0.5
        )
        
        # 等待超时
        time.sleep(0.6)
        
        assert len(callback_called) == 1
        assert callback_called[0].status == ApprovalStatus.TIMEOUT
        assert callback_called[0].approved is False
    
    def test_cancel_request(self):
        """测试取消请求"""
        hitl = HITLManager()
        
        request_id = hitl.request_async_approval(
            "delete_file",
            {"path": "test.txt"}
        )
        
        result = hitl.cancel_request(request_id)
        assert result is True
        
        request = hitl.get_request_status(request_id)
        assert request is None or request.status == ApprovalStatus.CANCELLED
    
    def test_get_pending_requests(self):
        """测试获取待处理请求"""
        hitl = HITLManager()
        
        # 创建多个请求
        hitl.request_async_approval("delete_file", {"path": "test1.txt"})
        hitl.request_async_approval("delete_file", {"path": "test2.txt"})
        
        pending = hitl.get_pending_requests()
        assert len(pending) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
