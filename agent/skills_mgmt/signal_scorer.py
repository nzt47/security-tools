"""高价值记忆信号评分器

五维评分:
    - emotion (情绪强度 0.25): 从文本识别强正/强负/痛点词
    - pain (痛点深度 0.25): 失败次数 + 同 session 反复尝试 + 工具链复杂度
    - effort (努力程度 0.20): 工具调用链长度 + 参数复杂度 + task_text 长度
    - novelty (新颖性 0.15): 与已有技能的差异度
    - recurrence (时间频次 0.15): 同类任务的出现次数

降级策略:
    当 feedback 源无 comment 时 (text 为空或仅 rating), emotion 维度降级为中性 0.3,
    其权重按比例重新分配给 pain/effort/novelty, 保证高价值信号不被漏掉。

设计原则:
    - 边界显性化: signal_strength < threshold 的记忆被过滤, 不进入聚类
    - 可观测: 每条记忆的评分明细输出到日志
    - 幂等: 相同输入产生相同评分
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("agent.skills_mgmt")


# ──────────────────────────────────────────────
# 情绪词表 (硬编码, 后续可改为外部配置文件)
# ──────────────────────────────────────────────

EMOTION_MARKERS: Dict[str, List[str]] = {
    "strong_positive": [
        "太好了", "完美", "终于解决了", "非常好", "棒", "赞", "厉害",
        "amazing", "perfect", "love it", "great", "awesome", "👍", "❤️",
        "excellent", "wonderful", "终于", "搞定",
    ],
    "strong_negative": [
        "为什么不", "又失败了", "太烦了", "垃圾", "不行", "崩溃", "无语",
        "frustrating", "broken", "terrible", "awful", "hate", "wrong",
        "bug", "crash", "error", "失败", "错误", "不行",
    ],
    "pain_words": [
        "卡住", "搞不定", "不知道为什么", "反复", "又", "还是",
        "stuck", "cannot figure out", "struggling", "hard to",
        "怎么也", "总是", "一直", "没办法", "无解",
    ],
}


@dataclass
class SignalBreakdown:
    """单条记忆的评分明细 — 用于日志和测试断言"""
    emotion: float = 0.0
    pain: float = 0.0
    effort: float = 0.0
    novelty: float = 0.0
    recurrence: float = 0.0
    total: float = 0.0
    weights: Dict[str, float] = field(default_factory=dict)
    emotion_available: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "emotion": round(self.emotion, 3),
            "pain": round(self.pain, 3),
            "effort": round(self.effort, 3),
            "novelty": round(self.novelty, 3),
            "recurrence": round(self.recurrence, 3),
            "total": round(self.total, 3),
            "weights": self.weights,
            "emotion_available": self.emotion_available,
        }


class SignalScorer:
    """记忆信号评分器 — 从五个维度计算 signal_strength"""

    # 默认权重
    DEFAULT_WEIGHTS: Dict[str, float] = {
        "emotion": 0.25,
        "pain": 0.25,
        "effort": 0.20,
        "novelty": 0.15,
        "recurrence": 0.15,
    }

    # emotion 不可用时的降级权重 (重新分配到 pain/effort/novelty)
    DEGRADED_WEIGHTS: Dict[str, float] = {
        "emotion": 0.0,    # 置零
        "pain": 0.35,      # 0.25 + 0.10
        "effort": 0.28,    # 0.20 + 0.08
        "novelty": 0.21,   # 0.15 + 0.06
        "recurrence": 0.16, # 0.15 + 0.01 (微调保证和为 1.0)
    }

    NEUTRAL_EMOTION_SCORE = 0.3
    DEFAULT_FILTER_THRESHOLD = 0.4

    def __init__(self, *,
                 weights: Optional[Dict[str, float]] = None,
                 filter_threshold: float = DEFAULT_FILTER_THRESHOLD):
        self.weights = weights or dict(self.DEFAULT_WEIGHTS)
        self.filter_threshold = filter_threshold

    # ─── 主入口 ───

    def score(self, entry, all_entries: List,
              existing_skills: Optional[List] = None) -> Tuple[float, SignalBreakdown]:
        """计算单条记忆的 signal_strength

        Args:
            entry: MemoryEntry 实例
            all_entries: 同批次所有记忆 (用于 recurrence 和 pain 维度)
            existing_skills: 已有技能列表 (用于 novelty 维度)

        Returns:
            (total_score, breakdown) — 总分和明细
        """
        # 检测 emotion 是否可用 (task_text 非空才算)
        emotion_available = bool(entry.task_text and entry.task_text.strip())
        weights = (self.DEGRADED_WEIGHTS if not emotion_available
                   else self.weights)

        # 降级策略触发时, INFO 级别记录 (便于排查 feedback 无 comment 场景)
        if not emotion_available:
            logger.info(
                "[SignalScorer] DEGRADED_WEIGHTS 应用 | source_id=%s | "
                "task_text 为空 → emotion 权重置零, 重分配到 "
                "pain(0.35)/effort(0.28)/novelty(0.21)/recurrence(0.16)",
                entry.source_id,
            )

        scores = {
            "emotion": self._score_emotion(entry) if emotion_available
                       else self.NEUTRAL_EMOTION_SCORE,
            "pain": self._score_pain(entry, all_entries),
            "effort": self._score_effort(entry),
            "novelty": self._score_novelty(entry, existing_skills or []),
            "recurrence": self._score_recurrence(entry, all_entries),
        }
        total = sum(weights[k] * scores[k] for k in weights)

        breakdown = SignalBreakdown(
            emotion=scores["emotion"],
            pain=scores["pain"],
            effort=scores["effort"],
            novelty=scores["novelty"],
            recurrence=scores["recurrence"],
            total=total,
            weights=dict(weights),
            emotion_available=emotion_available,
        )
        # DEBUG 级别: 每个维度的原始分 / 权重 / 加权贡献
        logger.debug(
            "[SignalScorer] score | source_id=%s | total=%.3f | "
            "weight_set=%s | emotion_avail=%s\n"
            "  emotion    : raw=%.3f  w=%.2f  contrib=%.3f\n"
            "  pain       : raw=%.3f  w=%.2f  contrib=%.3f\n"
            "  effort     : raw=%.3f  w=%.2f  contrib=%.3f\n"
            "  novelty    : raw=%.3f  w=%.2f  contrib=%.3f\n"
            "  recurrence : raw=%.3f  w=%.2f  contrib=%.3f",
            entry.source_id, total,
            "DEGRADED" if not emotion_available else "DEFAULT",
            emotion_available,
            scores["emotion"], weights["emotion"],
            weights["emotion"] * scores["emotion"],
            scores["pain"], weights["pain"],
            weights["pain"] * scores["pain"],
            scores["effort"], weights["effort"],
            weights["effort"] * scores["effort"],
            scores["novelty"], weights["novelty"],
            weights["novelty"] * scores["novelty"],
            scores["recurrence"], weights["recurrence"],
            weights["recurrence"] * scores["recurrence"],
        )
        return total, breakdown

    def score_batch(self, entries: List,
                    existing_skills: Optional[List] = None,
                    ) -> List[Tuple[float, SignalBreakdown]]:
        """批量评分"""
        return [self.score(e, entries, existing_skills) for e in entries]

    def filter_high_value(self, entries: List,
                          threshold: Optional[float] = None,
                          ) -> List:
        """过滤掉低价值信号, 只保留 signal_strength >= threshold"""
        th = threshold if threshold is not None else self.filter_threshold
        result = [e for e in entries if e.signal_strength >= th]
        logger.info(
            "[SignalScorer] filter_high_value | 输入=%d | 保留=%d | "
            "过滤=%d | threshold=%.2f",
            len(entries), len(result), len(entries) - len(result), th,
        )
        # DEBUG: 逐条记录通过/过滤状态
        for e in entries:
            status = "PASS" if e.signal_strength >= th else "FILTER"
            logger.debug(
                "[SignalScorer]   %s | source_id=%s | "
                "signal=%.3f | success=%s | tools=%d | session=%s",
                status, e.source_id, e.signal_strength,
                e.success, len(e.tool_calls), e.session_id or "-",
            )
        return result

    # ─── 维度 1: 情绪强度 ───

    @staticmethod
    def _score_emotion(entry) -> float:
        """从文本识别情绪强度

        强正: 1.0 (用户高度满意)
        强负: 0.9 (用户强烈不满 — 高价值信号!)
        痛点词: 0.8 (用户卡住/反复尝试 — 高价值信号!)
        中性: 0.3
        """
        text = (entry.task_text or "").lower()
        if not text:
            return SignalScorer.NEUTRAL_EMOTION_SCORE
        # 检查强正
        for word in EMOTION_MARKERS["strong_positive"]:
            if word.lower() in text:
                return 1.0
        # 检查强负
        for word in EMOTION_MARKERS["strong_negative"]:
            if word.lower() in text:
                return 0.9
        # 检查痛点词
        for word in EMOTION_MARKERS["pain_words"]:
            if word.lower() in text:
                return 0.8
        return SignalScorer.NEUTRAL_EMOTION_SCORE

    # ─── 维度 2: 痛点深度 ───

    @staticmethod
    def _score_pain(entry, all_entries: List) -> float:
        """从失败模式识别痛点深度

        失败本身: +0.3
        同 session 反复失败: +0.1/次 (上限 0.4)
        工具链复杂度: +0.05/工具 (上限 0.3)
        """
        score = 0.0
        # 失败本身
        if not entry.success:
            score += 0.3
        # 同 session 反复失败
        if entry.session_id:
            same_session_fails = [
                e for e in all_entries
                if e.session_id == entry.session_id and not e.success
            ]
            score += min(0.4, len(same_session_fails) * 0.1)
        # 工具链复杂度
        score += min(0.3, len(entry.tool_calls) * 0.05)
        return min(1.0, score)

    # ─── 维度 3: 努力程度 ───

    @staticmethod
    def _score_effort(entry) -> float:
        """从工具链长度 + 参数复杂度 + task_text 长度评估"""
        score = 0.0
        # 工具调用链长度
        score += min(0.4, len(entry.tool_calls) * 0.08)
        # 参数复杂度
        score += min(0.3, len(entry.params) * 0.05)
        # task_text 长度
        score += min(0.3, len(entry.task_text or "") / 500)
        return min(1.0, score)

    # ─── 维度 4: 新颖性 ───

    @staticmethod
    def _score_novelty(entry, existing_skills: List) -> float:
        """与已有技能的差异度 — 越不相似越新颖"""
        if not existing_skills:
            return 1.0
        # 延迟导入避免循环依赖
        from .memory_abstractor import _tokenize, _jaccard
        entry_tokens = _tokenize(entry.task_text or "")
        if not entry_tokens:
            return 0.5  # 无文本 → 中等新颖性
        max_sim = 0.0
        for skill in existing_skills:
            skill_text = ((getattr(skill, "content", "") or "") +
                          " " + (getattr(skill, "name", "") or ""))
            skill_tokens = _tokenize(skill_text)
            sim = _jaccard(entry_tokens, skill_tokens)
            if sim > max_sim:
                max_sim = sim
        return max(0.0, 1.0 - max_sim)

    # ─── 维度 5: 时间频次 ───

    @staticmethod
    def _score_recurrence(entry, all_entries: List) -> float:
        """同类任务的出现次数"""
        from .memory_abstractor import _tokenize, _jaccard
        entry_tokens = _tokenize(entry.task_text or "")
        if not entry_tokens:
            return 0.0
        similar_count = sum(
            1 for e in all_entries
            if _jaccard(entry_tokens, _tokenize(e.task_text or "")) >= 0.5
        )
        return min(1.0, similar_count / 10.0)
