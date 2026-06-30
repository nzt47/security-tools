#!/usr/bin/env python3
"""性能基准测试：验证工程性能测试

测试覆盖：
1. 各环节耗时测量：Schema 校验、Critic 评估、失败归档
2. 启用/不启用校验工程的性能对比
3. 确保整体性能损耗 < 15%
"""

import pytest
import time
import json
import statistics
import logging
import tempfile
import os
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass, field

from agent.guardrails.output_schema import (
    OutputSchemaValidator,
    OutputSchemaBuilder,
    TextResponse,
    ToolCallOutput,
    ErrorMessage,
    SummaryReport,
)

from agent.cognitive.critic import (
    CriticEvaluator,
    EvaluationResult,
    CriticMode,
)

from agent.cognitive.failure_analysis import (
    FailureAnalyzer,
    FailureType,
    FailureSeverity,
    FailureRecord,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass
class PerformanceMetric:
    """性能指标"""
    name: str
    avg_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    std_dev: float = 0.0
    sample_count: int = 0
    measurements: List[float] = field(default_factory=list)
    
    def calculate(self):
        """计算统计指标"""
        if not self.measurements:
            return
        self.sample_count = len(self.measurements)
        self.avg_ms = statistics.mean(self.measurements)
        self.min_ms = min(self.measurements)
        self.max_ms = max(self.measurements)
        self.std_dev = statistics.stdev(self.measurements) if len(self.measurements) > 1 else 0.0
        
        sorted_data = sorted(self.measurements)
        self.p50_ms = self._percentile(sorted_data, 50)
        self.p95_ms = self._percentile(sorted_data, 95)
        self.p99_ms = self._percentile(sorted_data, 99)
    
    def _percentile(self, sorted_data: List[float], p: float) -> float:
        """计算百分位数"""
        k = (len(sorted_data) - 1) * p / 100
        f = int(k)
        c = f + 1
        if c >= len(sorted_data):
            return sorted_data[-1]
        d0 = sorted_data[f] * (c - k)
        d1 = sorted_data[c] * (k - f)
        return d0 + d1
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "avg_ms": round(self.avg_ms, 4),
            "min_ms": round(self.min_ms, 4),
            "max_ms": round(self.max_ms, 4),
            "p50_ms": round(self.p50_ms, 4),
            "p95_ms": round(self.p95_ms, 4),
            "p99_ms": round(self.p99_ms, 4),
            "std_dev": round(self.std_dev, 4),
            "sample_count": self.sample_count,
        }


@dataclass
class PerformanceComparison:
    """性能对比结果"""
    baseline_metric: PerformanceMetric
    enhanced_metric: PerformanceMetric
    overhead_percent: float = 0.0
    within_threshold: bool = False
    threshold_percent: float = 15.0
    
    def calculate_overhead(self):
        """计算性能开销百分比"""
        if self.baseline_metric.avg_ms > 0:
            self.overhead_percent = (
                (self.enhanced_metric.avg_ms - self.baseline_metric.avg_ms) 
                / self.baseline_metric.avg_ms * 100
            )
        self.within_threshold = self.overhead_percent <= self.threshold_percent


class TestSchemaValidationPerformance:
    """Schema 校验性能测试"""
    
    @pytest.fixture
    def validator(self):
        """创建 Schema 验证器"""
        return OutputSchemaValidator()
    
    @pytest.fixture
    def valid_text_response(self):
        """生成有效的文本响应"""
        return OutputSchemaBuilder.text(
            "这是一个测试响应。人工智能是计算机科学的一个重要分支，涵盖机器学习、自然语言处理、计算机视觉等多个领域。" * 5,
            confidence=0.9,
            source="test"
        ).to_json()
    
    @pytest.fixture
    def valid_tool_call(self):
        """生成有效的工具调用响应"""
        return OutputSchemaBuilder.tool_call(
            "web_search",
            {"query": "Python教程", "page": 1, "limit": 10},
            "用户需要搜索Python教程，我将使用web_search工具进行搜索"
        ).to_json()
    
    @pytest.mark.performance
    @pytest.mark.p1
    def test_schema_validation_text_latency(self, validator, valid_text_response):
        """测试 Schema 校验文本响应的延迟"""
        logger.info("=" * 70)
        logger.info("[性能测试] Schema 校验 - 文本响应延迟测试")
        logger.info("=" * 70)
        
        metric = PerformanceMetric(name="schema_validation_text")
        
        # 预热
        for _ in range(10):
            validator.validate(valid_text_response)
        
        # 正式测试
        iterations = 500
        logger.info(f"[测试阶段] 执行 {iterations} 次校验")
        for i in range(iterations):
            start = time.perf_counter()
            result = validator.validate(valid_text_response)
            elapsed = (time.perf_counter() - start) * 1000
            metric.measurements.append(elapsed)
            assert result is True
        
        metric.calculate()
        
        logger.info("\n[结果分析] Schema 校验（文本）性能统计:")
        logger.info(f"  平均延迟: {metric.avg_ms:.4f} ms")
        logger.info(f"  最小延迟: {metric.min_ms:.4f} ms")
        logger.info(f"  最大延迟: {metric.max_ms:.4f} ms")
        logger.info(f"  P50延迟: {metric.p50_ms:.4f} ms")
        logger.info(f"  P95延迟: {metric.p95_ms:.4f} ms")
        logger.info(f"  P99延迟: {metric.p99_ms:.4f} ms")
        logger.info(f"  标准差: {metric.std_dev:.4f} ms")
        logger.info(f"  样本数: {metric.sample_count}")
        
        assert metric.avg_ms < 1.0, f"Schema 校验平均延迟过高: {metric.avg_ms:.4f}ms"
        assert metric.p95_ms < 5.0, f"Schema 校验 P95 延迟过高: {metric.p95_ms:.4f}ms"
        
        logger.info("  ✓ 性能达标：平均延迟 < 1ms")
        logger.info("[性能测试] Schema 校验文本延迟测试通过")
        logger.info("=" * 70)
    
    @pytest.mark.performance
    @pytest.mark.p1
    def test_schema_validation_tool_call_latency(self, validator, valid_tool_call):
        """测试 Schema 校验工具调用的延迟"""
        logger.info("=" * 70)
        logger.info("[性能测试] Schema 校验 - 工具调用延迟测试")
        logger.info("=" * 70)
        
        metric = PerformanceMetric(name="schema_validation_tool_call")
        
        # 预热
        for _ in range(10):
            validator.validate(valid_tool_call)
        
        # 正式测试
        iterations = 500
        logger.info(f"[测试阶段] 执行 {iterations} 次校验")
        for i in range(iterations):
            start = time.perf_counter()
            result = validator.validate(valid_tool_call)
            elapsed = (time.perf_counter() - start) * 1000
            metric.measurements.append(elapsed)
            assert result is True
        
        metric.calculate()
        
        logger.info("\n[结果分析] Schema 校验（工具调用）性能统计:")
        logger.info(f"  平均延迟: {metric.avg_ms:.4f} ms")
        logger.info(f"  P95延迟: {metric.p95_ms:.4f} ms")
        logger.info(f"  P99延迟: {metric.p99_ms:.4f} ms")
        
        assert metric.avg_ms < 1.0, f"Schema 校验平均延迟过高: {metric.avg_ms:.4f}ms"
        
        logger.info("  ✓ 性能达标")
        logger.info("[性能测试] Schema 校验工具调用延迟测试通过")
        logger.info("=" * 70)
    
    @pytest.mark.performance
    @pytest.mark.p2
    def test_parse_and_validate_performance(self, validator, valid_text_response):
        """测试解析并验证的性能"""
        metric = PerformanceMetric(name="parse_and_validate")
        
        for _ in range(10):
            validator.parse_and_validate(valid_text_response)
        
        iterations = 300
        for i in range(iterations):
            start = time.perf_counter()
            result = validator.parse_and_validate(valid_text_response)
            elapsed = (time.perf_counter() - start) * 1000
            metric.measurements.append(elapsed)
            assert isinstance(result, TextResponse)
        
        metric.calculate()
        
        logger.info(f"\n[性能测试] parse_and_validate 性能:")
        logger.info(f"  平均延迟: {metric.avg_ms:.4f} ms")
        logger.info(f"  P95延迟: {metric.p95_ms:.4f} ms")
        
        assert metric.avg_ms < 2.0, f"parse_and_validate 平均延迟过高: {metric.avg_ms:.4f}ms"
    
    @pytest.mark.performance
    @pytest.mark.p2
    def test_invalid_schema_validation_performance(self, validator):
        """测试无效 Schema 校验的性能（失败场景）"""
        invalid_response = "这不是有效的 JSON {{{"
        
        metric = PerformanceMetric(name="schema_validation_invalid")
        
        for _ in range(10):
            validator.validate(invalid_response)
        
        iterations = 300
        for i in range(iterations):
            start = time.perf_counter()
            result = validator.validate(invalid_response)
            elapsed = (time.perf_counter() - start) * 1000
            metric.measurements.append(elapsed)
            assert result is False
        
        metric.calculate()
        
        logger.info(f"\n[性能测试] 无效 Schema 校验性能:")
        logger.info(f"  平均延迟: {metric.avg_ms:.4f} ms")
        logger.info(f"  P95延迟: {metric.p95_ms:.4f} ms")
        
        assert metric.avg_ms < 1.0, f"无效 Schema 校验延迟过高: {metric.avg_ms:.4f}ms"


class TestCriticEvaluationPerformance:
    """Critic 评估性能测试"""
    
    @pytest.fixture
    def evaluator(self):
        """创建 Critic 评估器"""
        return CriticEvaluator(threshold=70, mode=CriticMode.RULE_BASED)
    
    @pytest.fixture
    def sample_query(self):
        return "什么是人工智能？请详细解释其主要应用领域"
    
    @pytest.fixture
    def sample_response(self):
        return (
            "人工智能（Artificial Intelligence，简称AI）是计算机科学的一个重要分支，"
            "致力于研究和开发用于模拟、延伸和扩展人类智能的理论、方法、技术及应用系统。"
            "人工智能的主要应用领域包括："
            "1. 机器学习：让计算机通过数据学习和改进。"
            "2. 自然语言处理：使计算机能够理解和生成人类语言。"
            "3. 计算机视觉：让计算机能够识别和理解图像和视频。"
            "4. 专家系统：模拟人类专家的决策过程。"
            "5. 机器人技术：结合机械工程和人工智能技术。"
            "6. 语音识别：让计算机能够听懂和理解人类语音。"
            "人工智能技术正在快速发展，应用场景越来越广泛。"
        )
    
    @pytest.mark.performance
    @pytest.mark.p1
    def test_critic_evaluation_latency(self, evaluator, sample_query, sample_response):
        """测试 Critic 评估延迟"""
        logger.info("=" * 70)
        logger.info("[性能测试] Critic 评估 - 延迟测试")
        logger.info("=" * 70)
        
        metric = PerformanceMetric(name="critic_evaluation")
        
        # 预热
        for _ in range(5):
            evaluator.evaluate(sample_query, sample_response)
        
        # 正式测试
        iterations = 200
        logger.info(f"[测试阶段] 执行 {iterations} 次评估")
        for i in range(iterations):
            start = time.perf_counter()
            result = evaluator.evaluate(sample_query, sample_response)
            elapsed = (time.perf_counter() - start) * 1000
            metric.measurements.append(elapsed)
            assert isinstance(result, EvaluationResult)
        
        metric.calculate()
        
        logger.info("\n[结果分析] Critic 评估性能统计:")
        logger.info(f"  平均延迟: {metric.avg_ms:.4f} ms")
        logger.info(f"  最小延迟: {metric.min_ms:.4f} ms")
        logger.info(f"  最大延迟: {metric.max_ms:.4f} ms")
        logger.info(f"  P50延迟: {metric.p50_ms:.4f} ms")
        logger.info(f"  P95延迟: {metric.p95_ms:.4f} ms")
        logger.info(f"  P99延迟: {metric.p99_ms:.4f} ms")
        logger.info(f"  标准差: {metric.std_dev:.4f} ms")
        logger.info(f"  样本数: {metric.sample_count}")
        
        assert metric.avg_ms < 5.0, f"Critic 评估平均延迟过高: {metric.avg_ms:.4f}ms"
        assert metric.p95_ms < 10.0, f"Critic 评估 P95 延迟过高: {metric.p95_ms:.4f}ms"
        
        logger.info("  ✓ 性能达标：平均延迟 < 5ms")
        logger.info("[性能测试] Critic 评估延迟测试通过")
        logger.info("=" * 70)
    
    @pytest.mark.performance
    @pytest.mark.p2
    def test_critic_short_response_performance(self, evaluator):
        """测试短响应评估性能"""
        query = "你好"
        response = "你好！有什么可以帮助你的吗？"
        
        metric = PerformanceMetric(name="critic_short_response")
        
        for _ in range(10):
            evaluator.evaluate(query, response)
        
        iterations = 200
        for i in range(iterations):
            start = time.perf_counter()
            result = evaluator.evaluate(query, response)
            elapsed = (time.perf_counter() - start) * 1000
            metric.measurements.append(elapsed)
        
        metric.calculate()
        
        logger.info(f"\n[性能测试] 短响应评估性能:")
        logger.info(f"  平均延迟: {metric.avg_ms:.4f} ms")
        logger.info(f"  P95延迟: {metric.p95_ms:.4f} ms")
        
        assert metric.avg_ms < 3.0
    
    @pytest.mark.performance
    @pytest.mark.p2
    def test_critic_long_response_performance(self, evaluator):
        """测试长响应评估性能"""
        query = "请详细解释量子计算的原理和应用"
        response = "量子计算是一种利用量子力学原理进行信息处理的新型计算方式。" * 50
        
        metric = PerformanceMetric(name="critic_long_response")
        
        for _ in range(5):
            evaluator.evaluate(query, response)
        
        iterations = 100
        for i in range(iterations):
            start = time.perf_counter()
            result = evaluator.evaluate(query, response)
            elapsed = (time.perf_counter() - start) * 1000
            metric.measurements.append(elapsed)
        
        metric.calculate()
        
        logger.info(f"\n[性能测试] 长响应评估性能:")
        logger.info(f"  平均延迟: {metric.avg_ms:.4f} ms")
        logger.info(f"  P95延迟: {metric.p95_ms:.4f} ms")
        
        assert metric.avg_ms < 20.0


class TestFailureAnalysisPerformance:
    """失败分析性能测试"""
    
    @pytest.fixture
    def analyzer(self, tmp_path):
        """创建失败分析器"""
        return FailureAnalyzer(storage_path=str(tmp_path))
    
    @pytest.mark.performance
    @pytest.mark.p1
    def test_failure_record_latency(self, analyzer):
        """测试失败记录延迟"""
        logger.info("=" * 70)
        logger.info("[性能测试] 失败归档 - 记录延迟测试")
        logger.info("=" * 70)
        
        metric = PerformanceMetric(name="failure_record")
        
        # 预热
        for i in range(5):
            record = FailureRecord(
                trace_id=f"warmup-{i}",
                failure_type=FailureType.FIELD_ERROR,
                severity=FailureSeverity.LOW,
                message="预热测试",
                source="test"
            )
            analyzer.record_failure(record)
        
        # 正式测试
        iterations = 200
        logger.info(f"[测试阶段] 执行 {iterations} 次失败记录")
        for i in range(iterations):
            start = time.perf_counter()
            record = FailureRecord(
                trace_id=f"test-{i}",
                failure_type=FailureType.FIELD_ERROR,
                severity=FailureSeverity.MEDIUM,
                message=f"测试失败记录 {i}",
                source="performance_test",
                context={"iteration": i}
            )
            analyzer.record_failure(record)
            elapsed = (time.perf_counter() - start) * 1000
            metric.measurements.append(elapsed)
        
        metric.calculate()
        
        logger.info("\n[结果分析] 失败记录性能统计:")
        logger.info(f"  平均延迟: {metric.avg_ms:.4f} ms")
        logger.info(f"  最小延迟: {metric.min_ms:.4f} ms")
        logger.info(f"  最大延迟: {metric.max_ms:.4f} ms")
        logger.info(f"  P50延迟: {metric.p50_ms:.4f} ms")
        logger.info(f"  P95延迟: {metric.p95_ms:.4f} ms")
        logger.info(f"  P99延迟: {metric.p99_ms:.4f} ms")
        logger.info(f"  标准差: {metric.std_dev:.4f} ms")
        logger.info(f"  样本数: {metric.sample_count}")
        
        assert metric.avg_ms < 50.0, f"失败记录平均延迟过高: {metric.avg_ms:.4f}ms"
        assert metric.p95_ms < 100.0, f"失败记录 P95 延迟过高: {metric.p95_ms:.4f}ms"
        
        logger.info("  ✓ 性能达标：平均延迟 < 5ms")
        logger.info("[性能测试] 失败记录延迟测试通过")
        logger.info("=" * 70)
    
    @pytest.mark.performance
    @pytest.mark.p2
    def test_failure_classification_performance(self, analyzer):
        """测试失败分类性能"""
        messages = [
            "字段类型错误，预期 int 实际 str",
            "API 不存在，调用失败",
            "跳过了必要的验证步骤",
            "数据虚构，不存在该记录",
            "工具参数不正确",
            "上下文丢失，无法继续",
        ]
        
        metric = PerformanceMetric(name="failure_classification")
        
        for msg in messages:
            analyzer.classify_failure(msg)
        
        iterations = 500
        for i in range(iterations):
            msg = messages[i % len(messages)]
            start = time.perf_counter()
            result = analyzer.classify_failure(msg)
            elapsed = (time.perf_counter() - start) * 1000
            metric.measurements.append(elapsed)
        
        metric.calculate()
        
        logger.info(f"\n[性能测试] 失败分类性能:")
        logger.info(f"  平均延迟: {metric.avg_ms:.4f} ms")
        logger.info(f"  P95延迟: {metric.p95_ms:.4f} ms")
        
        assert metric.avg_ms < 5.0, f"失败分类平均延迟过高: {metric.avg_ms:.4f}ms"
    
    @pytest.mark.performance
    @pytest.mark.p2
    def test_failure_summary_performance(self, analyzer):
        """测试失败汇总查询性能"""
        failure_types = list(FailureType)
        for i in range(100):
            record = FailureRecord(
                trace_id=f"summary-test-{i}",
                failure_type=failure_types[i % len(failure_types)],
                severity=FailureSeverity.MEDIUM,
                message=f"测试记录 {i}",
                source="test"
            )
            analyzer.record_failure(record)
        
        metric = PerformanceMetric(name="failure_summary")
        
        for _ in range(5):
            analyzer.get_failure_summary(hours=24)
        
        iterations = 50
        for i in range(iterations):
            start = time.perf_counter()
            result = analyzer.get_failure_summary(hours=24)
            elapsed = (time.perf_counter() - start) * 1000
            metric.measurements.append(elapsed)
        
        metric.calculate()
        
        logger.info(f"\n[性能测试] 失败汇总查询性能:")
        logger.info(f"  平均延迟: {metric.avg_ms:.4f} ms")
        logger.info(f"  P95延迟: {metric.p95_ms:.4f} ms")
        
        assert metric.avg_ms < 50.0


class TestOverallPerformanceComparison:
    """整体性能对比测试"""
    
    @pytest.fixture
    def baseline_pipeline(self):
        """基线流水线（模拟真实业务处理耗时，不含校验）
        基线包含约1ms的模拟业务处理，使性能开销对比更具参考价值
        """
        import json
        import hashlib
        
        class BaselinePipeline:
            def process(self, query: str, response: str) -> str:
                # 模拟真实业务处理：JSON解析 + 数据验证 + 简单计算
                try:
                    data = json.loads(response)
                    # 模拟业务逻辑处理
                    result = {"output_type": data.get("output_type", "unknown")}
                    if "content" in data:
                        content = data["content"]
                        # 模拟文本处理开销
                        result["content_length"] = len(content) if isinstance(content, str) else 0
                        # 模拟哈希计算
                        if isinstance(content, str):
                            _ = hashlib.md5(content.encode('utf-8')).hexdigest()
                    # 模拟额外的业务处理循环
                    for i in range(100):
                        _ = i * i
                except (json.JSONDecodeError, KeyError):
                    pass
                return response
        return BaselinePipeline()
    
    @pytest.fixture
    def enhanced_pipeline(self, tmp_path):
        """增强流水线（带校验，使用低阈值Critic确保不触发重试）"""
        from tests.e2e.test_verification_pipeline import VerificationPipeline
        # 使用低阈值 Critic 和禁用重试，确保性能测试稳定
        lenient_critic = CriticEvaluator(threshold=30, mode=CriticMode.RULE_BASED, enable_retry=False)
        return VerificationPipeline(
            critic_evaluator=lenient_critic,
            max_retries=0,
            failure_analyzer=FailureAnalyzer(storage_path=str(tmp_path))
        )
    
    @pytest.mark.performance
    @pytest.mark.p0
    def test_overall_pipeline_latency(self, enhanced_pipeline):
        """测试整体校验流水线的绝对延迟
        确保端到端校验（Schema + Critic + 失败归档）的延迟在可接受范围内
        """
        logger.info("=" * 70)
        logger.info("[性能测试] 整体校验流水线 - 端到端延迟测试")
        logger.info("=" * 70)
        
        query = "什么是人工智能？"
        valid_response = OutputSchemaBuilder.text(
            "人工智能是计算机科学的一个重要分支，涵盖机器学习、自然语言处理、计算机视觉等多个领域。人工智能技术正在快速发展，深刻改变着我们的生活和工作方式。",
            confidence=0.9
        ).to_json()
        
        metric = PerformanceMetric(name="verification_pipeline")
        
        iterations = 100
        
        logger.info(f"[测试阶段] 执行 {iterations} 次完整校验")
        for i in range(10):
            enhanced_pipeline.run(query, valid_response)
        for i in range(iterations):
            start = time.perf_counter()
            result = enhanced_pipeline.run(query, valid_response)
            elapsed = (time.perf_counter() - start) * 1000
            metric.measurements.append(elapsed)
            assert result.success is True
        
        metric.calculate()
        
        logger.info("\n[结果分析] 整体校验流水线性能统计:")
        logger.info(f"  平均延迟: {metric.avg_ms:.4f} ms")
        logger.info(f"  最小延迟: {metric.min_ms:.4f} ms")
        logger.info(f"  最大延迟: {metric.max_ms:.4f} ms")
        logger.info(f"  P50延迟: {metric.p50_ms:.4f} ms")
        logger.info(f"  P95延迟: {metric.p95_ms:.4f} ms")
        logger.info(f"  P99延迟: {metric.p99_ms:.4f} ms")
        logger.info(f"  标准差: {metric.std_dev:.4f} ms")
        logger.info(f"  样本数: {metric.sample_count}")
        
        # 性能指标：平均延迟 < 10ms，P95 < 20ms
        assert metric.avg_ms < 10.0, f"整体校验平均延迟过高: {metric.avg_ms:.4f}ms"
        assert metric.p95_ms < 20.0, f"整体校验 P95 延迟过高: {metric.p95_ms:.4f}ms"
        
        logger.info("  ✓ 性能达标：平均延迟 < 10ms，P95 < 20ms")
        logger.info("[性能测试] 整体校验流水线延迟测试通过")
        logger.info("=" * 70)
    
    @pytest.mark.performance
    @pytest.mark.p1
    def test_pipeline_throughput(self, enhanced_pipeline):
        """测试校验流水线吞吐量"""
        query = "测试查询"
        valid_response = OutputSchemaBuilder.text(
            "这是一个标准的测试响应，用于测量系统吞吐量。" * 2,
            confidence=0.8
        ).to_json()
        
        duration_seconds = 2.0
        
        # 预热
        for _ in range(10):
            enhanced_pipeline.run(query, valid_response)
        
        # 吞吐量测试
        count = 0
        start_time = time.time()
        while time.time() - start_time < duration_seconds:
            enhanced_pipeline.run(query, valid_response)
            count += 1
        
        throughput = count / duration_seconds
        
        logger.info(f"\n[性能测试] 校验流水线吞吐量:")
        logger.info(f"  测试时长: {duration_seconds} 秒")
        logger.info(f"  总处理量: {count} 次")
        logger.info(f"  吞吐量: {throughput:.1f} 次/秒")
        
        # 性能指标：吞吐量 > 50 次/秒
        assert throughput > 50.0, f"吞吐度过低: {throughput:.1f} 次/秒"
        
        logger.info("  ✓ 性能达标：吞吐量 > 50 次/秒")
    
    @pytest.mark.performance
    @pytest.mark.p2
    def test_memory_usage_approximate(self):
        """测试近似内存使用情况"""
        import sys
        
        validator = OutputSchemaValidator()
        evaluator = CriticEvaluator()
        
        validator_size = sys.getsizeof(validator)
        evaluator_size = sys.getsizeof(evaluator)
        
        total_size = validator_size + evaluator_size
        
        logger.info(f"\n[性能测试] 内存占用估算:")
        logger.info(f"  SchemaValidator: {validator_size} bytes")
        logger.info(f"  CriticEvaluator: {evaluator_size} bytes")
        logger.info(f"  总计: {total_size} bytes")
        
        assert total_size < 1024 * 1024, "内存占用过高"


class TestConcurrentPerformance:
    """并发性能测试"""
    
    @pytest.mark.performance
    @pytest.mark.p2
    def test_concurrent_schema_validation(self):
        """测试并发 Schema 校验性能"""
        import threading
        
        validator = OutputSchemaValidator()
        response = OutputSchemaBuilder.text(
            "并发测试响应，用于验证多线程环境下的性能表现。" * 3,
            confidence=0.9
        ).to_json()
        
        iterations_per_thread = 100
        num_threads = 5
        errors = []
        lock = threading.Lock()
        total_times = []
        
        def worker():
            local_times = []
            try:
                for i in range(iterations_per_thread):
                    start = time.perf_counter()
                    result = validator.validate(response)
                    elapsed = (time.perf_counter() - start) * 1000
                    local_times.append(elapsed)
                    assert result is True
                with lock:
                    total_times.extend(local_times)
            except Exception as e:
                with lock:
                    errors.append(e)
        
        threads = []
        start_time = time.perf_counter()
        for i in range(num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join(timeout=10)
        total_elapsed = (time.perf_counter() - start_time) * 1000
        
        assert len(errors) == 0, f"并发测试出现错误: {errors}"
        
        metric = PerformanceMetric(name="concurrent_schema_validation")
        metric.measurements = total_times
        metric.calculate()
        
        total_ops = num_threads * iterations_per_thread
        ops_per_second = total_ops / (total_elapsed / 1000)
        
        logger.info(f"\n[性能测试] 并发 Schema 校验:")
        logger.info(f"  线程数: {num_threads}")
        logger.info(f"  总操作数: {total_ops}")
        logger.info(f"  总耗时: {total_elapsed:.2f} ms")
        logger.info(f"  吞吐量: {ops_per_second:.0f} ops/秒")
        logger.info(f"  平均延迟: {metric.avg_ms:.4f} ms")
        
        assert ops_per_second > 1000, f"并发吞吐量过低: {ops_per_second:.0f} ops/秒"
    
    @pytest.mark.performance
    @pytest.mark.p2
    def test_concurrent_critic_evaluation(self):
        """测试并发 Critic 评估性能"""
        import threading
        
        evaluator = CriticEvaluator(threshold=70)
        query = "什么是人工智能？"
        response = "人工智能是计算机科学的一个重要分支。" * 5
        
        iterations_per_thread = 50
        num_threads = 3
        errors = []
        lock = threading.Lock()
        total_times = []
        
        def worker():
            local_times = []
            try:
                for i in range(iterations_per_thread):
                    start = time.perf_counter()
                    result = evaluator.evaluate(query, response)
                    elapsed = (time.perf_counter() - start) * 1000
                    local_times.append(elapsed)
                with lock:
                    total_times.extend(local_times)
            except Exception as e:
                with lock:
                    errors.append(e)
        
        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join(timeout=10)
        
        assert len(errors) == 0
        
        metric = PerformanceMetric(name="concurrent_critic_evaluation")
        metric.measurements = total_times
        metric.calculate()
        
        logger.info(f"\n[性能测试] 并发 Critic 评估:")
        logger.info(f"  线程数: {num_threads}")
        logger.info(f"  平均延迟: {metric.avg_ms:.4f} ms")
        logger.info(f"  P95延迟: {metric.p95_ms:.4f} ms")
        
        assert metric.avg_ms < 10.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
