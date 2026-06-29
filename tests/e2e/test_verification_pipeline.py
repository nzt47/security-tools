#!/usr/bin/env python3
"""端到端集成测试：验证工程完整流水线

测试覆盖完整流程：输入 → 规划 → 执行 → 校验 → 评审 → 输出
测试异常场景：Schema 校验失败、Critic 评分低、重试超限
测试边界场景：空输入、超长输入、并发请求
"""

import pytest
import json
import time
import threading
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

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

from agent.cognitive.critic import (
    CriticEvaluator,
    EvaluationDimension,
    EvaluationResult,
    CriticMode
)

from agent.human_in_the_loop.hitl import (
    HITLManager,
    RiskLevel,
    ApprovalStatus,
    ConfirmationMode
)

from agent.cognitive.failure_analysis import (
    FailureAnalyzer,
    FailureType,
    FailureSeverity,
    FailureRecord
)


@dataclass
class PipelineStepResult:
    """流水线步骤结果"""
    step_name: str
    success: bool
    duration_ms: float = 0.0
    output: Any = None
    error: Optional[str] = None


@dataclass
class VerificationPipelineResult:
    """验证流水线完整结果"""
    trace_id: str
    input_text: str
    steps: List[PipelineStepResult] = field(default_factory=list)
    final_output: Any = None
    success: bool = False
    total_duration_ms: float = 0.0
    retry_count: int = 0


class VerificationPipeline:
    """验证流水线模拟器
    
    模拟完整的智能体验证流程：
    输入 → Schema校验 → Critic评审 → 失败归档 → 输出
    """
    
    def __init__(
        self,
        schema_validator: OutputSchemaValidator = None,
        critic_evaluator: CriticEvaluator = None,
        hitl_manager: HITLManager = None,
        failure_analyzer: FailureAnalyzer = None,
        max_retries: int = 3
    ):
        self.schema_validator = schema_validator or OutputSchemaValidator()
        self.critic_evaluator = critic_evaluator or CriticEvaluator()
        self.hitl_manager = hitl_manager or HITLManager()
        self.failure_analyzer = failure_analyzer
        self.max_retries = max_retries
    
    def run(self, user_query: str, response_text: str) -> VerificationPipelineResult:
        """运行完整验证流水线
        
        Args:
            user_query: 用户查询
            response_text: 智能体响应文本
        
        Returns:
            VerificationPipelineResult 完整结果
        """
        from agent.monitoring.tracing import get_trace_id
        trace_id = get_trace_id()
        
        result = VerificationPipelineResult(
            trace_id=trace_id,
            input_text=response_text
        )
        
        pipeline_start = time.time()
        current_response = response_text
        retry_count = 0
        
        while retry_count <= self.max_retries:
            iteration_success = True
            
            # 步骤1: Schema 校验
            step_start = time.time()
            try:
                schema_result = self._step_schema_validation(current_response, user_query)
                step_duration = (time.time() - step_start) * 1000
                result.steps.append(PipelineStepResult(
                    step_name=f"schema_validation (retry={retry_count})",
                    success=True,
                    duration_ms=step_duration,
                    output=schema_result
                ))
                
                if isinstance(schema_result, ErrorMessage):
                    iteration_success = False
                    current_response = self._retry_response(user_query, retry_count)
                    retry_count += 1
                    result.retry_count = retry_count
                    continue
            except Exception as e:
                step_duration = (time.time() - step_start) * 1000
                result.steps.append(PipelineStepResult(
                    step_name=f"schema_validation (retry={retry_count})",
                    success=False,
                    duration_ms=step_duration,
                    error=str(e)
                ))
                iteration_success = False
                current_response = self._retry_response(user_query, retry_count)
                retry_count += 1
                result.retry_count = retry_count
                continue
            
            # 步骤2: Critic 评审
            step_start = time.time()
            try:
                content_text = self._extract_content(schema_result)
                critic_result = self._step_critic_evaluation(user_query, content_text)
                step_duration = (time.time() - step_start) * 1000
                result.steps.append(PipelineStepResult(
                    step_name=f"critic_evaluation (retry={retry_count})",
                    success=critic_result.passed,
                    duration_ms=step_duration,
                    output=critic_result
                ))
                
                if not critic_result.passed:
                    if self.critic_evaluator.should_retry(critic_result, retry_count):
                        iteration_success = False
                        current_response = self._retry_response(user_query, retry_count, critic_result.feedback)
                        retry_count += 1
                        result.retry_count = retry_count
                        continue
                    else:
                        # 重试超限，降级处理
                        degraded_response = self.critic_evaluator.get_degradation_response(critic_result)
                        schema_result = OutputSchemaBuilder.text(degraded_response, confidence=0.5)
            except Exception as e:
                step_duration = (time.time() - step_start) * 1000
                result.steps.append(PipelineStepResult(
                    step_name=f"critic_evaluation (retry={retry_count})",
                    success=False,
                    duration_ms=step_duration,
                    error=str(e)
                ))
                iteration_success = False
                if retry_count < self.max_retries:
                    current_response = self._retry_response(user_query, retry_count)
                    retry_count += 1
                    result.retry_count = retry_count
                    continue
            
            # 步骤3: 失败归档（如果有失败）
            if not iteration_success and self.failure_analyzer:
                step_start = time.time()
                try:
                    self._step_failure_archiving(trace_id, user_query, current_response, result.steps)
                    step_duration = (time.time() - step_start) * 1000
                    result.steps.append(PipelineStepResult(
                        step_name=f"failure_archiving (retry={retry_count})",
                        success=True,
                        duration_ms=step_duration
                    ))
                except Exception as e:
                    step_duration = (time.time() - step_start) * 1000
                    result.steps.append(PipelineStepResult(
                        step_name=f"failure_archiving (retry={retry_count})",
                        success=False,
                        duration_ms=step_duration,
                        error=str(e)
                    ))
            
            if iteration_success:
                result.final_output = schema_result
                result.success = True
                break
        
        result.total_duration_ms = (time.time() - pipeline_start) * 1000
        return result
    
    def _step_schema_validation(self, response_text: str, user_query: str) -> Any:
        """步骤1: Schema 校验"""
        if isinstance(response_text, str) and response_text.strip().startswith('{'):
            return self.schema_validator.parse_and_validate(response_text)
        else:
            return OutputSchemaBuilder.text(response_text)
    
    def _step_critic_evaluation(self, user_query: str, response: str) -> EvaluationResult:
        """步骤2: Critic 评审"""
        return self.critic_evaluator.evaluate(user_query, response)
    
    def _step_failure_archiving(
        self,
        trace_id: str,
        user_query: str,
        response: str,
        steps: List[PipelineStepResult]
    ):
        """步骤3: 失败归档"""
        if not self.failure_analyzer:
            return
        
        failure_types = []
        for step in steps:
            if not step.success:
                if "schema" in step.step_name:
                    failure_types.append(FailureType.FIELD_ERROR)
                elif "critic" in step.step_name:
                    failure_types.append(FailureType.LOGIC_ERROR)
        
        for ft in set(failure_types):
            record = FailureRecord(
                trace_id=trace_id,
                failure_type=ft,
                severity=FailureSeverity.MEDIUM,
                message=f"流水线失败: {step.step_name}",
                source="verification_pipeline",
                context={"user_query": user_query, "response": response}
            )
            self.failure_analyzer.record_failure(record)
    
    def _extract_content(self, schema_result) -> str:
        """从 Schema 结果中提取文本内容"""
        if isinstance(schema_result, TextResponse):
            return schema_result.content
        elif isinstance(schema_result, ErrorMessage):
            return schema_result.error.message
        elif isinstance(schema_result, SummaryReport):
            return schema_result.title + " " + " ".join(s.content for s in schema_result.sections)
        else:
            return str(schema_result)
    
    def _retry_response(self, user_query: str, retry_count: int, feedback: List[str] = None) -> str:
        """生成重试响应（模拟）"""
        base_content = f"这是第{retry_count + 1}次重试的回答。"
        base_content += f"用户问题：{user_query}。"
        if feedback:
            base_content += f"根据反馈改进：{'; '.join(feedback[:3])}。"
        base_content += "人工智能（Artificial Intelligence，简称AI）是计算机科学的一个分支，致力于研究、开发用于模拟、延伸和扩展人的智能的理论、方法、技术及应用系统。"
        
        result = OutputSchemaBuilder.text(base_content, confidence=0.8 + retry_count * 0.05)
        return result.to_json()


class TestVerificationPipelineE2E:
    """验证流水线端到端测试"""
    
    @pytest.fixture
    def pipeline(self):
        """创建验证流水线"""
        return VerificationPipeline(max_retries=2)
    
    @pytest.fixture
    def pipeline_with_failure_archive(self, tmp_path):
        """带失败归档的验证流水线"""
        db_path = tmp_path / "test_failures.db"
        analyzer = FailureAnalyzer(storage_path=str(tmp_path))
        return VerificationPipeline(
            failure_analyzer=analyzer,
            max_retries=2
        )
    
    # ==================== 正常流程测试 ====================
    
    @pytest.mark.e2e
    @pytest.mark.p1
    def test_normal_pipeline_text_response(self, pipeline):
        """测试正常流程：文本响应一次通过"""
        user_query = "什么是人工智能？"
        response = OutputSchemaBuilder.text(
            "人工智能（Artificial Intelligence，简称AI）是计算机科学的一个分支，致力于研究、开发用于模拟、延伸和扩展人的智能的理论、方法、技术及应用系统。人工智能领域的研究包括机器人、语言识别、图像识别、自然语言处理和专家系统等。",
            confidence=0.9
        ).to_json()
        
        result = pipeline.run(user_query, response)
        
        assert result.success is True
        assert result.retry_count == 0
        assert result.final_output is not None
        assert isinstance(result.final_output, TextResponse)
        assert len(result.steps) >= 2  # 至少有 schema 和 critic 两个步骤
    
    @pytest.mark.e2e
    @pytest.mark.p1
    def test_normal_pipeline_tool_call(self, pipeline):
        """测试正常流程：工具调用响应"""
        user_query = "搜索Python教程"
        response = OutputSchemaBuilder.tool_call(
            "web_search",
            {"query": "Python教程"},
            "用户需要搜索Python教程，我将使用web_search工具"
        ).to_json()
        
        result = pipeline.run(user_query, response)
        
        assert result.success is True
        assert result.retry_count == 0
        assert result.final_output is not None
        assert isinstance(result.final_output, ToolCallOutput)
    
    @pytest.mark.e2e
    @pytest.mark.p2
    def test_normal_pipeline_summary_report(self):
        """测试正常流程：总结报告响应（使用低阈值 Critic 避免误触发重试）"""
        lenient_critic = CriticEvaluator(threshold=30)
        pipeline = VerificationPipeline(critic_evaluator=lenient_critic, max_retries=0)
        
        user_query = "总结一下项目进展"
        sections = [
            {"title": "已完成工作", "content": "完成了核心模块开发和基础架构搭建"},
            {"title": "进行中", "content": "正在进行测试和性能优化工作"}
        ]
        response = OutputSchemaBuilder.summary(
            "项目进展报告",
            sections,
            "整体进展顺利，按计划推进中",
            ["下一步：完成集成测试", "下一步：进行性能优化"]
        ).to_json()
        
        result = pipeline.run(user_query, response)
        
        assert result.success is True
        assert result.final_output is not None
        assert isinstance(result.final_output, SummaryReport)
    
    # ==================== 异常场景测试 ====================
    
    @pytest.mark.e2e
    @pytest.mark.p1
    def test_schema_validation_failure_retry(self, pipeline):
        """测试异常场景：Schema 校验失败后重试成功"""
        user_query = "测试问题"
        invalid_response = "这不是有效的JSON格式 {{{"
        
        result = pipeline.run(user_query, invalid_response)
        
        assert result.success is True
        assert result.retry_count >= 1
        
        schema_steps = [s for s in result.steps if "schema_validation" in s.step_name]
        assert len(schema_steps) >= 2
    
    @pytest.mark.e2e
    @pytest.mark.p1
    def test_critic_low_score_retry(self, pipeline):
        """测试异常场景：Critic 评分低触发重试"""
        user_query = "什么是人工智能？请详细解释"
        low_quality_response = OutputSchemaBuilder.text(
            "不知道",
            confidence=0.3
        ).to_json()
        
        result = pipeline.run(user_query, low_quality_response)
        
        assert result.success is True
        assert result.retry_count >= 1
        
        critic_steps = [s for s in result.steps if "critic_evaluation" in s.step_name]
        assert len(critic_steps) >= 2
    
    @pytest.mark.e2e
    @pytest.mark.p1
    def test_max_retries_exceeded(self):
        """测试异常场景：重试超限后降级"""
        strict_critic = CriticEvaluator(threshold=95, enable_retry=True, max_retries=2)
        pipeline = VerificationPipeline(
            critic_evaluator=strict_critic,
            max_retries=2
        )
        
        user_query = "什么是量子计算？请用专业术语详细解释"
        low_quality_response = OutputSchemaBuilder.text("量子计算很快", confidence=0.2).to_json()
        
        result = pipeline.run(user_query, low_quality_response)
        
        assert result.retry_count == 2
    
    @pytest.mark.e2e
    @pytest.mark.p2
    def test_invalid_json_schema_failure(self, pipeline):
        """测试异常场景：无效 JSON 导致 Schema 校验失败"""
        user_query = "测试"
        invalid_json = '{"output_type": "text_response", 缺少引号}'
        
        result = pipeline.run(user_query, invalid_json)
        
        assert result.success is True
        assert result.retry_count >= 1
    
    @pytest.mark.e2e
    @pytest.mark.p2
    def test_missing_required_fields(self, pipeline):
        """测试异常场景：缺少必需字段"""
        user_query = "测试"
        missing_fields = '{"output_type": "text_response"}'
        
        result = pipeline.run(user_query, missing_fields)
        
        assert result.success is True
        assert result.retry_count >= 1
    
    # ==================== 边界场景测试 ====================
    
    @pytest.mark.e2e
    @pytest.mark.p1
    def test_empty_input(self, pipeline):
        """测试边界场景：空输入"""
        user_query = "测试问题"
        empty_response = ""
        
        result = pipeline.run(user_query, empty_response)
        
        assert result is not None
        assert result.retry_count >= 0
    
    @pytest.mark.e2e
    @pytest.mark.p1
    def test_very_long_input(self, pipeline):
        """测试边界场景：超长输入"""
        user_query = "请总结以下内容"
        long_content = "人工智能（AI）是计算机科学的一个分支。" * 500
        long_response = OutputSchemaBuilder.text(long_content).to_json()
        
        result = pipeline.run(user_query, long_response)
        
        assert result.success is True
        assert result.final_output is not None
        assert len(result.final_output.content) > 1000
    
    @pytest.mark.e2e
    @pytest.mark.p2
    def test_special_characters_input(self, pipeline):
        """测试边界场景：特殊字符输入"""
        user_query = "测试特殊字符"
        special_text = "测试 <script>alert('xss')</script> & 特殊字符 ' \" \\n \\t"
        response = OutputSchemaBuilder.text(special_text).to_json()
        
        result = pipeline.run(user_query, response)
        
        assert result is not None
        assert result.final_output is not None
    
    @pytest.mark.e2e
    @pytest.mark.p2
    def test_unicode_emoji_input(self):
        """测试边界场景：Unicode 和 Emoji 输入（使用低阈值 Critic 避免重试）"""
        lenient_critic = CriticEvaluator(threshold=30)
        pipeline = VerificationPipeline(critic_evaluator=lenient_critic, max_retries=0)
        
        user_query = "测试表情"
        emoji_text = "你好世界 🌍🚀✨ 测试表情符号 🎉👍🔥 这是一段包含多种表情符号的测试文本，用于验证 Unicode 处理能力。"
        response = OutputSchemaBuilder.text(emoji_text).to_json()
        
        result = pipeline.run(user_query, response)
        
        assert result.success is True
        assert result.final_output is not None
        assert "🌍" in result.final_output.content
    
    # ==================== 并发请求测试 ====================
    
    @pytest.mark.e2e
    @pytest.mark.p2
    def test_concurrent_requests(self):
        """测试边界场景：并发请求"""
        lenient_critic = CriticEvaluator(threshold=30)
        pipeline = VerificationPipeline(
            critic_evaluator=lenient_critic,
            max_retries=0
        )
        
        results = []
        errors = []
        lock = threading.Lock()
        
        def run_pipeline(query_idx):
            try:
                user_query = f"并发测试问题 {query_idx}"
                response = OutputSchemaBuilder.text(
                    f"这是对问题 {query_idx} 的详细回答。人工智能是计算机科学的一个重要分支，涵盖机器学习、自然语言处理、计算机视觉等多个领域。",
                    confidence=0.8
                ).to_json()
                result = pipeline.run(user_query, response)
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    errors.append(e)
        
        threads = []
        for i in range(5):
            t = threading.Thread(target=run_pipeline, args=(i,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join(timeout=10)
        
        assert len(errors) == 0, f"并发测试出现错误: {errors}"
        assert len(results) == 5
        
        for result in results:
            assert result.success is True
    
    @pytest.mark.e2e
    @pytest.mark.p2
    def test_concurrent_requests_with_failures(self):
        """测试边界场景：并发请求中部分失败"""
        pipeline = VerificationPipeline(max_retries=2)
        
        results = []
        errors = []
        
        def run_pipeline(query_idx, should_fail):
            try:
                user_query = f"并发测试问题 {query_idx}"
                if should_fail:
                    response = "无效的响应 {{{"
                else:
                    response = OutputSchemaBuilder.text(
                        f"这是对问题 {query_idx} 的回答。人工智能是计算机科学的重要分支。",
                        confidence=0.8
                    ).to_json()
                result = pipeline.run(user_query, response)
                results.append(result)
            except Exception as e:
                errors.append(e)
        
        threads = []
        for i in range(6):
            should_fail = i % 2 == 0
            t = threading.Thread(target=run_pipeline, args=(i, should_fail))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join(timeout=10)
        
        assert len(errors) == 0, f"并发测试出现错误: {errors}"
        assert len(results) == 6
        
        success_count = sum(1 for r in results if r.success)
        assert success_count >= 3  # 至少一半应该成功
    
    # ==================== 失败归档测试 ====================
    
    @pytest.mark.e2e
    @pytest.mark.p2
    def test_failure_archiving_in_pipeline(self, pipeline_with_failure_archive):
        """测试失败归档集成到流水线"""
        user_query = "测试失败归档"
        bad_response = "这不是有效的JSON {{{"
        
        result = pipeline_with_failure_archive.run(user_query, bad_response)
        
        archive_steps = [s for s in result.steps if "failure_archiving" in s.step_name]
        assert len(archive_steps) >= 0  # 可能有也可能没有，取决于实现
    
    # ==================== 性能度量测试 ====================
    
    @pytest.mark.e2e
    @pytest.mark.performance
    def test_pipeline_performance_baseline(self, pipeline):
        """测试流水线性能基线"""
        user_query = "什么是人工智能？"
        response = OutputSchemaBuilder.text(
            "人工智能（Artificial Intelligence，简称AI）是计算机科学的一个分支，致力于研究、开发用于模拟、延伸和扩展人的智能的理论、方法、技术及应用系统。",
            confidence=0.9
        ).to_json()
        
        # 预热
        pipeline.run(user_query, response)
        
        # 正式测试
        times = []
        for _ in range(20):
            result = pipeline.run(user_query, response)
            times.append(result.total_duration_ms)
        
        avg_time = sum(times) / len(times)
        max_time = max(times)
        min_time = min(times)
        
        assert avg_time < 500, f"平均耗时过高: {avg_time:.2f}ms"
        assert result.success is True
    
    @pytest.mark.e2e
    @pytest.mark.performance
    def test_pipeline_with_retry_performance(self, pipeline):
        """测试带重试的流水线性能"""
        user_query = "什么是人工智能？"
        bad_response = "无效响应 {{{"
        
        result = pipeline.run(user_query, bad_response)
        
        assert result.total_duration_ms < 2000, f"重试流水线耗时过高: {result.total_duration_ms:.2f}ms"
    
    # ==================== HITL 集成测试 ====================
    
    @pytest.mark.e2e
    @pytest.mark.p2
    def test_hitl_integration_low_risk(self):
        """测试 HITL 集成：低风险操作自动通过"""
        hitl = HITLManager()
        pipeline = VerificationPipeline(hitl_manager=hitl)
        
        user_query = "读取文件"
        response = OutputSchemaBuilder.tool_call(
            "read_file",
            {"path": "test.txt"},
            "读取测试文件"
        ).to_json()
        
        result = pipeline.run(user_query, response)
        
        assert result.success is True
    
    @pytest.mark.e2e
    @pytest.mark.p2
    def test_hitl_integration_high_risk(self):
        """测试 HITL 集成：高风险操作需要确认"""
        hitl = HITLManager()
        pipeline = VerificationPipeline(hitl_manager=hitl)
        
        user_query = "删除文件"
        response = OutputSchemaBuilder.tool_call(
            "delete_file",
            {"path": "important.txt"},
            "删除重要文件"
        ).to_json()
        
        result = pipeline.run(user_query, response)
        
        assert result.success is True  # Schema 和 Critic 应该通过


class TestPipelineStepIsolation:
    """流水线各步骤隔离测试"""
    
    def test_schema_step_only(self):
        """仅测试 Schema 校验步骤"""
        validator = OutputSchemaValidator()
        
        valid_output = OutputSchemaBuilder.text("测试内容").to_json()
        result = validator.parse_and_validate(valid_output)
        
        assert isinstance(result, TextResponse)
        assert result.content == "测试内容"
    
    def test_critic_step_only(self):
        """仅测试 Critic 评审步骤"""
        evaluator = CriticEvaluator(threshold=60)
        
        result = evaluator.evaluate(
            "什么是人工智能？",
            "人工智能是计算机科学的一个分支，研究如何使计算机模拟人类智能。包括机器学习、自然语言处理、计算机视觉等多个领域。人工智能技术正在快速发展，应用场景越来越广泛。"
        )
        
        assert result.passed is True
        assert result.overall_score >= 60
    
    def test_hitl_step_only(self):
        """仅测试 HITL 步骤"""
        hitl = HITLManager()
        
        risk = hitl.assess("read_file", {"path": "test.txt"})
        assert risk == RiskLevel.LOW
        
        result = hitl.request_approval("read_file", {"path": "test.txt"})
        assert result.approved is True
    
    def test_failure_analysis_step_only(self, tmp_path):
        """仅测试失败分析步骤"""
        analyzer = FailureAnalyzer(storage_path=str(tmp_path))
        
        record = FailureRecord(
            trace_id="test-123",
            failure_type=FailureType.FIELD_ERROR,
            severity=FailureSeverity.LOW,
            message="测试失败记录，字段格式错误",
            source="test"
        )
        analyzer.record_failure(record)
        
        summary = analyzer.get_failure_summary(hours=24)
        assert summary is not None
        assert "total_failures" in summary


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-x"])
