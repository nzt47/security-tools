"""LLM-as-Judge 自检评审模块

实现独立的 Critic 模型评估机制，对最终输出进行事实性、完整性打分（0-100分）。
低于阈值（如70分）自动触发重试或降级。

设计原则：
- 双轨评估：事实性评估 + 完整性评估
- 可配置阈值：支持自定义分数阈值
- 自动重试：低于阈值自动触发重试机制
- 可扩展性：支持规则引擎和 LLM 驱动两种模式
- 容错机制：集成熔断器、限流和优雅降级

评估维度：
1. 事实性（Factual Accuracy）：回答内容与事实的一致性
2. 完整性（Completeness）：回答是否完整覆盖用户需求
3. 相关性（Relevance）：回答是否与用户问题相关
4. 逻辑性（Logic）：推理过程是否合理
5. 清晰度（Clarity）：表达是否清晰易懂
"""

import json
import logging
import time
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from agent.monitoring.tracing import get_trace_id
from agent.circuit_breaker import get_circuit_breaker, CircuitBreakerError
from agent.graceful_degrade import get_degrade_manager, DegradeModule

logger = logging.getLogger(__name__)


class EvaluationDimension(Enum):
    """评估维度枚举"""
    FACTUAL_ACCURACY = "factual_accuracy"  # 事实性
    COMPLETENESS = "completeness"          # 完整性
    RELEVANCE = "relevance"                # 相关性
    LOGIC = "logic"                        # 逻辑性
    CLARITY = "clarity"                    # 清晰度


@dataclass
class EvaluationResult:
    """评估结果"""
    overall_score: int                     # 综合评分（0-100）
    dimension_scores: Dict[str, int]       # 各维度评分
    passed: bool                           # 是否通过阈值
    feedback: List[str]                    # 反馈建议
    retry_recommended: bool                # 是否建议重试
    explanation: Optional[str] = None      # 评估说明


@dataclass
class EvidenceItem:
    """证据项"""
    statement: str                         # 被评估的陈述
    is_factual: bool                       # 是否符合事实
    confidence: float                      # 置信度（0-1）
    source: Optional[str] = None           # 证据来源


class CriticMode(Enum):
    """Critic 工作模式"""
    RULE_BASED = "rule_based"              # 规则引擎模式（零 Token 消耗）
    LLM_DRIVEN = "llm_driven"              # LLM 驱动模式（需要调用 LLM）


class CriticEvaluator:
    """Critic 评估器 - LLM-as-Judge 自检评审机制
    
    对智能体输出进行多维度评估，确保输出质量符合标准。
    
    集成容错机制：
    - 熔断器：当错误率超过阈值时自动熔断，防止级联故障
    - 优雅降级：Critic 不可用时自动跳过评估
    
    用法:
        evaluator = CriticEvaluator()
        result = evaluator.evaluate(
            user_query="什么是人工智能？",
            response="人工智能是计算机科学的一个分支...",
            context={"knowledge_base": [...]}
        )
        if not result.passed:
            # 触发重试或降级
            logger.warning(f"评估未通过，分数: {result.overall_score}")
    """
    
    def __init__(
        self,
        threshold: int = 70,
        mode: CriticMode = CriticMode.RULE_BASED,
        enable_retry: bool = True,
        max_retries: int = 3
    ):
        """
        初始化 Critic 评估器
        
        Args:
            threshold: 通过阈值（0-100），低于此分数触发重试或降级
            mode: 评估模式（规则引擎或 LLM 驱动）
            enable_retry: 是否启用自动重试
            max_retries: 最大重试次数
        """
        self.threshold = threshold
        self.mode = mode
        self.enable_retry = enable_retry
        self.max_retries = max_retries
        
        # 获取熔断器和降级管理器
        self._circuit_breaker = get_circuit_breaker("critic")
        self._degrade_manager = get_degrade_manager()
    
    def evaluate(
        self,
        user_query: str,
        response: str,
        context: Optional[Dict[str, Any]] = None,
        evidence: Optional[List[EvidenceItem]] = None
    ) -> EvaluationResult:
        """评估输出质量（集成熔断器和优雅降级）
        
        Args:
            user_query: 用户查询
            response: 智能体响应
            context: 上下文信息（如知识库内容、对话历史等）
            evidence: 证据列表（用于事实性验证）
        
        Returns:
            EvaluationResult 评估结果
        """
        trace_id = get_trace_id()
        start_time = time.time()
        
        # 检查是否应该跳过 Critic 评估（降级）
        if self._degrade_manager.should_skip(DegradeModule.CRITIC):
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "critic",
                "action": "evaluate",
                "duration_ms": 0,
                "status": "skipped",
                "reason": "Critic 服务不可用，已降级跳过评估"
            }))
            return EvaluationResult(
                overall_score=80,
                dimension_scores={},
                passed=True,
                feedback=["Critic 服务不可用，已跳过评估"],
                retry_recommended=False,
                explanation="Critic 服务不可用，已降级跳过评估"
            )
        
        # 检查熔断器状态
        try:
            if not self._circuit_breaker.allow_request():
                raise CircuitBreakerError(
                    name="critic",
                    message="Critic 熔断器已打开，请求被拒绝"
                )
        except CircuitBreakerError as e:
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "critic",
                "action": "evaluate",
                "duration_ms": 0,
                "status": "blocked",
                "reason": str(e)
            }))
            return EvaluationResult(
                overall_score=80,
                dimension_scores={},
                passed=True,
                feedback=["Critic 熔断器已打开，已降级跳过评估"],
                retry_recommended=False,
                explanation="Critic 熔断器已打开，已降级跳过评估"
            )
        
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "critic",
            "action": "evaluate",
            "duration_ms": 0,
            "mode": self.mode.value,
            "threshold": self.threshold
        }))
        
        try:
            # 根据模式执行评估
            if self.mode == CriticMode.RULE_BASED:
                dimension_scores, feedback = self._evaluate_with_rules(user_query, response, context)
            else:
                dimension_scores, feedback = self._evaluate_with_llm(user_query, response, context)
            
            # 记录成功
            self._circuit_breaker.record_success()
            
        except Exception as e:
            # 记录失败
            self._circuit_breaker.record_failure()
            
            # 使用降级机制处理
            degrade_result = self._degrade_manager.critic_evaluate_with_degrade(
                user_query, response, context
            )
            logger.error(json.dumps({
                "trace_id": trace_id,
                "module_name": "critic",
                "action": "evaluate",
                "duration_ms": (time.time() - start_time) * 1000,
                "error": str(e),
                "status": "degraded"
            }))
            return EvaluationResult(
                overall_score=degrade_result.get("overall_score", 75),
                dimension_scores={},
                passed=degrade_result.get("passed", True),
                feedback=degrade_result.get("feedback", ["Critic 评估失败，已降级"]),
                retry_recommended=False,
                explanation=degrade_result.get("reason", "Critic 评估失败，已降级")
            )
        
        # 计算综合评分（加权平均）
        overall_score = self._calculate_overall_score(dimension_scores)
        
        # 判断是否通过
        passed = overall_score >= self.threshold
        retry_recommended = not passed and self.enable_retry
        
        duration_ms = (time.time() - start_time) * 1000
        
        # 记录评估日志
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "critic",
            "action": "evaluate",
            "duration_ms": round(duration_ms, 2),
            "overall_score": overall_score,
            "passed": passed,
            "retry_recommended": retry_recommended,
            "dimension_scores": dimension_scores
        }))
        
        # 如果未通过，记录警告日志
        if not passed:
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "critic",
                "action": "evaluate",
                "duration_ms": round(duration_ms, 2),
                "warning": "评估未通过，触发重试或降级",
                "overall_score": overall_score,
                "threshold": self.threshold,
                "feedback": feedback
            }))
        
        return EvaluationResult(
            overall_score=overall_score,
            dimension_scores=dimension_scores,
            passed=passed,
            feedback=feedback,
            retry_recommended=retry_recommended,
            explanation=f"综合评分: {overall_score}/{self.threshold}，{'通过' if passed else '未通过'}"
        )
    
    def _evaluate_with_rules(
        self,
        user_query: str,
        response: str,
        context: Optional[Dict[str, Any]]
    ) -> Tuple[Dict[str, int], List[str]]:
        """使用规则引擎进行评估"""
        trace_id = get_trace_id()
        feedback = []
        scores = {}
        
        # 1. 事实性评估
        factual_score, factual_feedback = self._evaluate_factual_accuracy(response, context)
        scores[EvaluationDimension.FACTUAL_ACCURACY.value] = factual_score
        feedback.extend(factual_feedback)
        
        # 2. 完整性评估
        completeness_score, completeness_feedback = self._evaluate_completeness(user_query, response)
        scores[EvaluationDimension.COMPLETENESS.value] = completeness_score
        feedback.extend(completeness_feedback)
        
        # 3. 相关性评估
        relevance_score, relevance_feedback = self._evaluate_relevance(user_query, response)
        scores[EvaluationDimension.RELEVANCE.value] = relevance_score
        feedback.extend(relevance_feedback)
        
        # 4. 逻辑性评估
        logic_score, logic_feedback = self._evaluate_logic(response)
        scores[EvaluationDimension.LOGIC.value] = logic_score
        feedback.extend(logic_feedback)
        
        # 5. 清晰度评估
        clarity_score, clarity_feedback = self._evaluate_clarity(response)
        scores[EvaluationDimension.CLARITY.value] = clarity_score
        feedback.extend(clarity_feedback)
        
        logger.debug(json.dumps({
            "trace_id": trace_id,
            "module_name": "critic",
            "action": "_evaluate_with_rules",
            "scores": scores,
            "feedback": feedback
        }))
        
        return scores, feedback
    
    def _evaluate_factual_accuracy(
        self,
        response: str,
        context: Optional[Dict[str, Any]]
    ) -> Tuple[int, List[str]]:
        """评估事实性"""
        feedback = []
        
        # 检查响应是否为空
        if not response or len(response.strip()) == 0:
            return 0, ["响应内容为空"]
        
        # 检查是否包含明显错误的断言
        false_patterns = [
            (r"2\+2=5", "包含明显错误的数学断言"),
            (r"地球是平的", "包含错误的科学常识"),
            (r"中国首都是上海", "包含错误的地理知识"),
            (r"鲁迅原名周树人", "正确知识"),  # 正向例子，不计入错误
        ]
        
        false_count = 0
        for pattern, desc in false_patterns:
            import re
            if re.search(pattern, response):
                if "正确知识" not in desc:
                    false_count += 1
                    feedback.append(desc)
        
        # 如果有上下文知识，可以进行更精确的事实验证
        if context and "knowledge_base" in context:
            knowledge = context["knowledge_base"]
            if isinstance(knowledge, list):
                # 简单的关键词匹配验证
                for item in knowledge[:10]:  # 限制检查数量
                    item_str = str(item).lower()
                    if item_str in response.lower():
                        # 找到匹配的知识，视为正确
                        pass
        
        score = max(0, 100 - false_count * 30)
        
        return score, feedback
    
    def _evaluate_completeness(
        self,
        user_query: str,
        response: str
    ) -> Tuple[int, List[str]]:
        """评估完整性"""
        feedback = []
        
        # 检查响应长度
        response_len = len(response.strip())
        
        # 根据查询类型判断完整性
        query_keywords = [
            ("什么是", "定义类问题"),
            ("如何", "方法类问题"),
            ("为什么", "原因类问题"),
            ("列举", "列举类问题"),
            ("比较", "比较类问题"),
        ]
        
        expected_lengths = {
            "定义类问题": 50,
            "方法类问题": 100,
            "原因类问题": 80,
            "列举类问题": 150,
            "比较类问题": 120,
        }
        
        query_type = "普通问题"
        for keyword, q_type in query_keywords:
            if keyword in user_query:
                query_type = q_type
                break
        
        expected_len = expected_lengths.get(query_type, 80)
        
        # 根据长度评分
        if response_len >= expected_len:
            score = min(100, 70 + int((response_len - expected_len) / expected_len * 30))
        elif response_len >= expected_len * 0.5:
            score = 50 + int((response_len / expected_len) * 20)
        elif response_len > 0:
            score = 30 + int((response_len / expected_len) * 20)
        else:
            score = 0
        
        if response_len < expected_len * 0.5:
            feedback.append(f"回答可能不够完整，期望至少{expected_len}字符，实际{response_len}字符")
        
        return score, feedback
    
    def _evaluate_relevance(
        self,
        user_query: str,
        response: str
    ) -> Tuple[int, List[str]]:
        """评估相关性"""
        feedback = []
        
        if not user_query or not response:
            return 0, ["查询或响应为空"]
        
        # 提取关键词
        query_words = set([w for w in user_query.strip().split() if len(w) >= 2])
        response_words = set([w for w in response.strip().split() if len(w) >= 2])
        
        if not query_words:
            return 50, ["无法提取查询关键词"]
        
        # 计算关键词重叠率
        overlap = query_words & response_words
        
        if not overlap:
            score = 20
            feedback.append("回答与问题相关性较低，未包含查询中的关键词")
        elif len(overlap) >= len(query_words) * 0.7:
            score = 100
        elif len(overlap) >= len(query_words) * 0.5:
            score = 75
        elif len(overlap) >= len(query_words) * 0.3:
            score = 50
        else:
            score = 30
            feedback.append(f"回答相关性一般，仅匹配 {len(overlap)}/{len(query_words)} 个关键词")
        
        return score, feedback
    
    def _evaluate_logic(self, response: str) -> Tuple[int, List[str]]:
        """评估逻辑性"""
        feedback = []
        
        if not response:
            return 0, ["响应为空"]
        
        # 检查逻辑连接词的使用
        logic_connectors = ["因为", "所以", "因此", "但是", "然而", "首先", "其次", "最后"]
        connector_count = sum(1 for conn in logic_connectors if conn in response)
        
        # 检查矛盾陈述
        contradictions = [
            ("不是...而是", False),  # 正常对比
            ("既...又不", True),      # 矛盾
            ("既是...也是", False),   # 正常并列
            ("不可能...可能", True),  # 矛盾
        ]
        
        contradiction_count = 0
        for pattern, is_contradiction in contradictions:
            if pattern in response and is_contradiction:
                contradiction_count += 1
        
        # 根据逻辑连接词和矛盾情况评分
        base_score = 60
        connector_bonus = min(connector_count * 10, 30)
        contradiction_penalty = contradiction_count * 20
        
        score = max(0, base_score + connector_bonus - contradiction_penalty)
        
        if contradiction_count > 0:
            feedback.append("检测到潜在的逻辑矛盾")
        
        if connector_count == 0 and len(response) > 100:
            feedback.append("长回答缺少逻辑连接词，建议增加结构化表达")
        
        return score, feedback
    
    def _evaluate_clarity(self, response: str) -> Tuple[int, List[str]]:
        """评估清晰度"""
        feedback = []
        
        if not response:
            return 0, ["响应为空"]
        
        # 检查句子长度
        sentences = response.replace("。", "|").replace("！", "|").replace("？", "|").split("|")
        avg_sentence_len = sum(len(s.strip()) for s in sentences if s.strip()) / max(len(sentences), 1)
        
        # 检查段落结构
        paragraphs = response.split("\n")
        has_multiple_paragraphs = len([p for p in paragraphs if p.strip()]) > 1
        
        # 检查列表或编号结构
        has_list = any(char in response for char in ["1.", "2.", "3.", "- ", "•", "* "])
        
        # 评分逻辑
        score = 50
        
        # 句子长度评分（适中为好）
        if 15 <= avg_sentence_len <= 50:
            score += 25
        elif avg_sentence_len < 15:
            score += 10
            feedback.append("句子过短，可能影响表达的连贯性")
        else:
            score += 5
            feedback.append("句子过长，建议拆分以提高可读性")
        
        # 段落结构评分
        if has_multiple_paragraphs:
            score += 15
        elif len(response) > 200:
            feedback.append("长回答建议分段，提高可读性")
        
        # 列表结构评分
        if has_list:
            score += 10
        
        return min(100, score), feedback
    
    def _evaluate_with_llm(
        self,
        user_query: str,
        response: str,
        context: Optional[Dict[str, Any]]
    ) -> Tuple[Dict[str, int], List[str]]:
        """使用 LLM 进行评估（预留接口）
        
        当前返回默认分数，实际实现时需要调用 LLM。
        """
        trace_id = get_trace_id()
        
        logger.warning(json.dumps({
            "trace_id": trace_id,
            "module_name": "critic",
            "action": "_evaluate_with_llm",
            "warning": "LLM 评估模式尚未完全实现，使用规则引擎替代"
        }))
        
        # 回退到规则引擎
        return self._evaluate_with_rules(user_query, response, context)
    
    def _calculate_overall_score(self, dimension_scores: Dict[str, int]) -> int:
        """计算综合评分（加权平均）
        
        权重分配：
        - 事实性：30%
        - 完整性：25%
        - 相关性：20%
        - 逻辑性：15%
        - 清晰度：10%
        """
        weights = {
            EvaluationDimension.FACTUAL_ACCURACY.value: 0.30,
            EvaluationDimension.COMPLETENESS.value: 0.25,
            EvaluationDimension.RELEVANCE.value: 0.20,
            EvaluationDimension.LOGIC.value: 0.15,
            EvaluationDimension.CLARITY.value: 0.10,
        }
        
        total_score = 0
        total_weight = 0
        
        for dimension, score in dimension_scores.items():
            weight = weights.get(dimension, 0.2)
            total_score += score * weight
            total_weight += weight
        
        if total_weight > 0:
            overall_score = int(round(total_score / total_weight))
        else:
            overall_score = 0
        
        return max(0, min(100, overall_score))
    
    def should_retry(self, result: EvaluationResult, current_retry: int) -> bool:
        """判断是否应该重试
        
        Args:
            result: 评估结果
            current_retry: 当前重试次数
        
        Returns:
            True 如果应该重试，False 否则
        """
        return (
            self.enable_retry and
            not result.passed and
            current_retry < self.max_retries
        )
    
    def get_degradation_response(self, result: EvaluationResult) -> str:
        """获取降级响应
        
        当评估未通过且无法重试时，返回降级后的友好响应。
        
        Args:
            result: 评估结果
        
        Returns:
            降级响应文本
        """
        base_response = "我正在努力完善这个回答。"
        
        if result.feedback:
            feedback_str = "、".join(result.feedback[:3])
            base_response += f"根据评估，回答需要改进以下方面：{feedback_str}。"
        
        base_response += "请重新描述您的问题，我会尽力为您提供更准确的回答。"
        
        return base_response