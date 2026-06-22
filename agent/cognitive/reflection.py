"""反思模块——执行后的自我评估与纠错

在任务执行完成后评估结果质量，判断是否需要重试或调整策略。
设计思想：设计文档 4.2（反思与自我纠错）

架构说明：
- 当前为基于规则的质量评估（零 Token 消耗）
- 后续可引入 LLM 驱动的深度反思评估
- 重试逻辑受 MAX_RETRIES 硬限制保护，避免死循环

集成方式：
  reflection = ReflectionEngine()
  result = reflection.evaluate(task_id, input_text, output, elapsed_ms)
  if result.should_retry:
      # 重新执行任务
"""
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ReflectionResult:
    """反思评估结果

    Attributes:
        passed: 是否通过自我评估（score >= 0.6）
        score: 质量评分 0.0~1.0（1.0 = 完美）
        issues: 发现的问题列表
        suggestions: 改进建议列表
        should_retry: 是否需要重试（passed=False 且 score >= 0.3）
        retry_count: 当前已重试次数
    """
    passed: bool
    score: float
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    should_retry: bool = False
    retry_count: int = 0


class ReflectionEngine:
    """反思引擎——基于规则的任务质量评估

    通过多维度规则评估任务执行结果的质量：
    1. 空输出检测
    2. 错误信息检测
    3. 输出充分性检测（输入长但输出短）
    4. 执行耗时异常检测
    5. 工具调用失败检测
    """

    MAX_RETRIES = 3  # 最大重试次数——硬限制防死循环

    def __init__(self):
        self._retry_counts: dict[str, int] = {}

    def evaluate(self, task_id: str,
                 input_text: str,
                 output: str,
                 execution_time_ms: float = 0,
                 tool_calls: list = None) -> ReflectionResult:
        """评估任务执行结果

        Args:
            task_id: 任务唯一标识
            input_text: 原始输入文本
            output: LLM 返回的输出文本
            execution_time_ms: 执行耗时（毫秒）
            tool_calls: 工具调用记录列表

        Returns:
            ReflectionResult 评估结果
        """
        issues = []
        suggestions = []
        score = 1.0
        tool_calls = tool_calls or []

        # 维度 1：空输出检测——严重影响评分
        if not output or len(output.strip()) == 0:
            issues.append("输出为空")
            score -= 0.5

        # 维度 2：错误信息检测
        output_lower = (output or "").lower()
        if "错误" in (output or "") or "失败" in (output or "") or "error" in output_lower:
            issues.append("输出包含错误信息")
            score -= 0.3

        # 维度 3：输出过短检测（输入较长但输出很短，可能未完整回答）
        if len(input_text) > 50 and len(output or "") < 10:
            issues.append("输出过短，可能未完整回答用户")
            score -= 0.2

        # 维度 4：耗时异常检测
        if execution_time_ms > 30000:  # 超过 30 秒
            suggestions.append("执行耗时过长（%.1f秒），考虑优化流程" % (execution_time_ms / 1000))
            score -= 0.1

        # 维度 5：工具调用异常检测
        if tool_calls:
            failed_calls = [c for c in tool_calls if c.get("error") or c.get("status") == "error"]
            if failed_calls:
                issues.append("工具调用失败: %d/%d 个" % (len(failed_calls), len(tool_calls)))
                score -= 0.3 * len(failed_calls)

        # 维度 6：工具调用返回空结果检测
        if tool_calls:
            empty_results = [c for c in tool_calls
                             if c.get("status") == "success"
                             and c.get("type") == "tool_result"
                             and not c.get("summary")]
            if empty_results:
                suggestions.append("部分工具返回空结果，可能需要确认数据源状态")
                score -= 0.05 * len(empty_results)

        # 分数裁剪到 [0.0, 1.0]
        score = max(0.0, min(1.0, score))

        # 判断是否通过
        passed = score >= 0.6

        # 重试决策逻辑
        # - 未通过评估（passed=False）
        # - 分数不低于 0.3（分数太低说明方向性错误，不重试）
        # - 未超过最大重试次数
        current_retries = self._retry_counts.get(task_id, 0)
        should_retry = (not passed
                        and current_retries < self.MAX_RETRIES
                        and score >= 0.3)

        if should_retry:
            self._retry_counts[task_id] = current_retries + 1
            logger.info("[Cognitive] 触发重试: task_id=%s, score=%.2f, retry=%d/%d",
                        task_id, score, current_retries + 1, self.MAX_RETRIES)
        elif passed and task_id in self._retry_counts:
            # 清理已完成的重试记录
            del self._retry_counts[task_id]

        logger.info("[Cognitive] Reflection: task_id=%s, score=%.2f, passed=%s, "
                    "retry=%s (%d/%d), issues=%d",
                    task_id, score, passed, should_retry,
                    current_retries, self.MAX_RETRIES, len(issues))

        return ReflectionResult(
            passed=passed,
            score=score,
            issues=issues,
            suggestions=suggestions,
            should_retry=should_retry,
            retry_count=current_retries,
        )

    def get_retry_count(self, task_id: str) -> int:
        """获取指定任务的当前重试次数

        Args:
            task_id: 任务 ID

        Returns:
            当前重试次数（0 表示未触发过重试）
        """
        return self._retry_counts.get(task_id, 0)

    def reset_retry(self, task_id: str):
        """重置指定任务的重试计数

        Args:
            task_id: 任务 ID
        """
        self._retry_counts.pop(task_id, None)
