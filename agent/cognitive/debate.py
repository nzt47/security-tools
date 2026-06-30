"""辩论模块——多 Agent 辩论 + 共识投票

面对没有标准答案的复杂决策时，通过多个角度评估给出推荐。
设计思想：设计文档 4.4（多智能体博弈）

架构说明：
- 当前为基于规则的多角度评估（零 Token 消耗）
- 后续可升级为：
  * 每个 Perspective 由一个独立的 Subagent 评估
  * 多轮辩论收敛（辩论 → 反驳 → 再评估）
  * 贝叶斯共识计算

评估角度（Perspective）：
- SAFETY: 安全——操作的风险等级
- PERFORMANCE: 性能——对系统性能的影响
- USABILITY: 易用性——用户体验和操作的便捷性
- CORRECTNESS: 正确性——操作的准确和完整程度
"""
import logging
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class Perspective(Enum):
    """评估视角枚举"""
    SAFETY = "安全"
    PERFORMANCE = "性能"
    USABILITY = "易用性"
    CORRECTNESS = "正确性"


@dataclass
class DebateResult:
    """辩论结果

    Attributes:
        perspectives: 各视角评分 {视角名称: 分数}
        consensus_score: 共识评分 0.0~1.0
        recommendation: 推荐结论文本
        details: 详细辩论过程记录
    """
    perspectives: dict[str, float] = field(default_factory=dict)
    consensus_score: float = 0.0
    recommendation: str = ""
    details: list[str] = field(default_factory=list)


class DebateEngine:
    """多角度辩论引擎

    对复杂决策提案从多个独立视角进行评估，加权得出共识评分和推荐结论。

    用法:
        engine = DebateEngine()
        result = engine.debate("计划删除旧日志文件")
        print(result.recommendation, result.consensus_score)
    """

    # 各视角的默认权重
    PERSPECTIVE_WEIGHTS: dict[Perspective, float] = {
        Perspective.SAFETY: 0.40,
        Perspective.CORRECTNESS: 0.30,
        Perspective.PERFORMANCE: 0.15,
        Perspective.USABILITY: 0.15,
    }

    def debate(self, proposal: str, context: dict = None) -> DebateResult:
        """对提案进行多角度辩论评估

        Args:
            proposal: 待评估的提案（通常是系统要执行的操作描述）
            context: 可选的上下文信息

        Returns:
            DebateResult 辩论结果
        """
        context = context or {}
        result = DebateResult()
        details = []

        # 1. 从各视角独立评估
        for perspective in Perspective:
            score, detail = self._evaluate_perspective(perspective, proposal, context)
            result.perspectives[perspective.value] = score
            if detail:
                details.append("[%s] %s → %.2f" % (perspective.value, detail, score))

        # 2. 计算加权共识评分
        total_weight = 0.0
        weighted_sum = 0.0
        for perspective, weight in self.PERSPECTIVE_WEIGHTS.items():
            score = result.perspectives.get(perspective.value, 0.5)
            weighted_sum += score * weight
            total_weight += weight

        result.consensus_score = weighted_sum / total_weight if total_weight > 0 else 0.5

        # 3. 生成推荐结论
        result.recommendation = self._generate_recommendation(result.consensus_score, result.perspectives)
        result.details = details

        logger.info("[Cognitive] Debate: consensus=%.2f, recommendation=%s",
                    result.consensus_score, result.recommendation)

        return result

    def _evaluate_perspective(self, perspective: Perspective,
                               proposal: str,
                               context: dict) -> tuple[float, str]:
        """从单一角度评估提案

        Args:
            perspective: 评估视角
            proposal: 提案文本
            context: 上下文信息

        Returns:
            (评分, 评估说明)
        """
        proposal_lower = proposal.lower()
        detail = ""

        if perspective == Perspective.SAFETY:
            # 安全评估：检测危险操作
            dangerous_keywords = ["rm -rf", "format", "delete", "drop table",
                                  "shutdown", "reboot", "chmod -R"]
            for kw in dangerous_keywords:
                if kw in proposal_lower:
                    detail = "检测到危险关键字: %s" % kw
                    return 0.2, detail

            # 检查危险命令组合
            if "sudo" in proposal_lower or "admin" in proposal_lower:
                detail = "涉及特权操作"
                return 0.5, detail

            detail = "未检测到明显安全风险"
            return 0.9, detail

        elif perspective == Perspective.CORRECTNESS:
            # 正确性评估
            if len(proposal) < 10:
                detail = "提案过于简短，可能遗漏关键信息"
                return 0.4, detail

            # 检查是否包含矛盾内容
            if "但是" in proposal and "所以" in proposal:
                detail = "提案包含转折逻辑"
                return 0.6, detail

            detail = "提案结构完整"
            return 0.8, detail

        elif perspective == Perspective.PERFORMANCE:
            # 性能评估
            performance_heavy = ["批量", "大量", "全部", "循环", "递归",
                                 "all", "batch", "every", "loop"]
            for kw in performance_heavy:
                if kw in proposal_lower:
                    detail = "可能涉及大量操作: %s" % kw
                    return 0.5, detail

            detail = "操作量级适中"
            return 0.85, detail

        elif perspective == Perspective.USABILITY:
            # 易用性评估
            if len(proposal) > 200:
                detail = "方案描述较长"
                return 0.7, detail

            detail = "方案简洁明了"
            return 0.85, detail

        # 默认返回中等评分
        return 0.6, "默认评估"

    def _generate_recommendation(self, consensus_score: float,
                                  perspectives: dict[str, float]) -> str:
        """生成推荐结论

        Args:
            consensus_score: 共识评分
            perspectives: 各视角评分

        Returns:
            推荐结论文本
        """
        # 检查是否有视角给出极低评分（一票否决）
        for name, score in perspectives.items():
            if score < 0.3:
                return "不推荐执行（%s视角评分过低: %.2f）" % (name, score)

        # 基于共识评分生成推荐
        if consensus_score >= 0.8:
            return "推荐执行"
        elif consensus_score >= 0.6:
            return "建议修改后执行"
        elif consensus_score >= 0.4:
            return "需慎重考虑后执行"
        else:
            return "不推荐执行"


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "debate",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
