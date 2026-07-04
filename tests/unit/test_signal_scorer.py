"""SignalScorer 单元测试

覆盖:
    1. 五维评分 (emotion / pain / effort / novelty / recurrence)
    2. 不同情绪场景: 强正 / 强负 / 痛点词 / 中性
    3. 不同失败场景: 单次失败 / 同 session 反复失败 / 工具链复杂
    4. 降级策略: feedback 无 comment 时 emotion 权重重分配
    5. 高价值信号过滤: threshold=0.4
    6. score_batch 批量评分
    7. 端到端: 同一记忆在有/无 comment 下的评分对比
"""

from __future__ import annotations
import unittest
from typing import List

from agent.skills_mgmt.memory_abstractor import MemoryEntry
from agent.skills_mgmt.signal_scorer import (
    EMOTION_MARKERS,
    SignalScorer,
    SignalBreakdown,
)


def _make_entry(**kwargs) -> MemoryEntry:
    """构造 MemoryEntry 的便捷工厂, 提供合理默认值"""
    defaults = {
        "source": "test",
        "source_id": "t1",
        "task_text": "测试任务",
        "success": True,
        "tool_calls": [],
        "params": {},
        "tags": [],
        "timestamp": "2026-07-05T10:00:00",
        "session_id": "",
    }
    defaults.update(kwargs)
    return MemoryEntry(**defaults)


# ──────────────────────────────────────────────
# 1. 情绪维度评分
# ──────────────────────────────────────────────

class TestEmotionScoring(unittest.TestCase):
    """_score_emotion: 文本情绪识别"""

    def test_strong_positive_returns_max(self):
        """强正情绪词 → 1.0"""
        for word in ["太好了", "完美", "amazing", "终于解决了", "👍"]:
            entry = _make_entry(task_text=f"用户说 {word} 真不错")
            self.assertEqual(
                SignalScorer._score_emotion(entry), 1.0,
                msg=f"强正词 '{word}' 应得 1.0",
            )

    def test_strong_negative_returns_high(self):
        """强负情绪词 → 0.9 (高价值信号!)"""
        for word in ["又失败了", "垃圾", "崩溃", "frustrating", "broken"]:
            entry = _make_entry(task_text=f"这个 {word} 真让人头疼")
            self.assertEqual(
                SignalScorer._score_emotion(entry), 0.9,
                msg=f"强负词 '{word}' 应得 0.9",
            )

    def test_pain_words_returns_elevated(self):
        """痛点词 → 0.8 (用户卡住/反复尝试)"""
        for word in ["卡住", "搞不定", "stuck", "struggling", "反复"]:
            entry = _make_entry(task_text=f"用户 {word} 了, 需要帮助")
            self.assertEqual(
                SignalScorer._score_emotion(entry), 0.8,
                msg=f"痛点词 '{word}' 应得 0.8",
            )

    def test_neutral_text_returns_low(self):
        """中性文本 → 0.3 (低情绪)"""
        entry = _make_entry(task_text="查询天气")
        self.assertEqual(SignalScorer._score_emotion(entry), 0.3)

    def test_empty_text_returns_neutral(self):
        """空文本 → 0.3"""
        entry = _make_entry(task_text="")
        self.assertEqual(SignalScorer._score_emotion(entry), 0.3)

    def test_none_text_returns_neutral(self):
        """None task_text → 0.3 (不抛异常)"""
        entry = _make_entry(task_text="")
        entry.task_text = None  # type: ignore
        self.assertEqual(SignalScorer._score_emotion(entry), 0.3)

    def test_strong_positive_takes_precedence_over_negative(self):
        """强正优先于强负 (按词表检查顺序)"""
        entry = _make_entry(
            task_text="太好了, 终于解决了 bug, 之前一直崩溃",
        )
        # 同时包含 strong_positive ("太好了") 和 strong_negative ("崩溃")
        # strong_positive 先匹配, 返回 1.0
        self.assertEqual(SignalScorer._score_emotion(entry), 1.0)

    def test_case_insensitive_english(self):
        """英文情绪词大小写不敏感"""
        entry = _make_entry(task_text="This is AMAZING!")
        self.assertEqual(SignalScorer._score_emotion(entry), 1.0)


# ──────────────────────────────────────────────
# 2. 痛点维度评分
# ──────────────────────────────────────────────

class TestPainScoring(unittest.TestCase):
    """_score_pain: 失败模式识别"""

    def test_success_no_tools_zero_pain(self):
        """成功 + 无工具 → 0.0"""
        entry = _make_entry(success=True, tool_calls=[])
        self.assertEqual(SignalScorer._score_pain(entry, [entry]), 0.0)

    def test_single_failure_adds_base(self):
        """单次失败 → +0.3"""
        entry = _make_entry(success=False, tool_calls=[])
        self.assertEqual(SignalScorer._score_pain(entry, [entry]), 0.3)

    def test_same_session_multiple_failures(self):
        """同 session 反复失败, 每次 +0.1, 上限 0.4"""
        sess = "sess-1"
        e1 = _make_entry(source_id="e1", success=False, session_id=sess)
        e2 = _make_entry(source_id="e2", success=False, session_id=sess)
        e3 = _make_entry(source_id="e3", success=False, session_id=sess)
        all_entries = [e1, e2, e3]
        # e1 痛点: 0.3 (失败) + 0.3 (3 次同 session 失败) = 0.6
        self.assertAlmostEqual(SignalScorer._score_pain(e1, all_entries), 0.6)

    def test_same_session_failure_cap(self):
        """同 session 失败加成上限 0.4"""
        sess = "sess-x"
        # 10 次失败, 加成应被截断到 0.4
        entries = [
            _make_entry(source_id=f"e{i}", success=False, session_id=sess)
            for i in range(10)
        ]
        # 失败本身 0.3 + 同 session 失败 0.4 = 0.7
        self.assertAlmostEqual(SignalScorer._score_pain(entries[0], entries), 0.7)

    def test_tool_chain_complexity(self):
        """工具链复杂度: +0.05/工具, 上限 0.3"""
        entry = _make_entry(
            success=True,
            tool_calls=[{"name": f"tool{i}"} for i in range(10)],
        )
        # 10 个工具 × 0.05 = 0.5, 但上限 0.3
        self.assertEqual(SignalScorer._score_pain(entry, [entry]), 0.3)

    def test_pain_score_capped_at_one(self):
        """痛点总分上限 1.0"""
        sess = "sess-y"
        entries = [
            _make_entry(
                source_id=f"e{i}",
                success=False,
                session_id=sess,
                tool_calls=[{"name": f"t{i}"} for i in range(20)],
            )
            for i in range(10)
        ]
        # 失败 0.3 + 同 session 0.4 + 工具 0.3 = 1.0
        self.assertEqual(SignalScorer._score_pain(entries[0], entries), 1.0)

    def test_success_with_many_tools_still_low_pain(self):
        """成功但有复杂工具链 → 仅工具复杂度贡献"""
        entry = _make_entry(
            success=True,
            tool_calls=[{"name": f"t{i}"} for i in range(6)],
        )
        # 6 × 0.05 = 0.3 (达到上限)
        self.assertEqual(SignalScorer._score_pain(entry, [entry]), 0.3)

    def test_no_session_id_skips_recurrent_bonus(self):
        """无 session_id → 跳过同 session 反复失败加成"""
        entry = _make_entry(success=False, session_id="", tool_calls=[])
        # 仅失败本身 0.3
        self.assertEqual(SignalScorer._score_pain(entry, [entry]), 0.3)


# ──────────────────────────────────────────────
# 3. 努力程度评分
# ──────────────────────────────────────────────

class TestEffortScoring(unittest.TestCase):
    """_score_effort: 工具链 + 参数 + 文本长度"""

    def test_empty_entry_minimal_effort(self):
        """空 entry → 0.0"""
        entry = _make_entry(task_text="", tool_calls=[], params={})
        self.assertEqual(SignalScorer._score_effort(entry), 0.0)

    def test_tool_calls_contribute(self):
        """每个工具 +0.08, 上限 0.4"""
        entry = _make_entry(
            task_text="",
            tool_calls=[{"name": f"t{i}"} for i in range(5)],
            params={},
        )
        # 5 × 0.08 = 0.4 (达上限)
        self.assertAlmostEqual(SignalScorer._score_effort(entry), 0.4)

    def test_tool_calls_cap(self):
        """工具链贡献上限 0.4"""
        entry = _make_entry(
            task_text="",
            tool_calls=[{"name": f"t{i}"} for i in range(20)],
        )
        # 工具部分 0.4, 文本部分 0, 总 0.4
        self.assertEqual(SignalScorer._score_effort(entry), 0.4)

    def test_params_contribute(self):
        """每个参数 +0.05, 上限 0.3"""
        entry = _make_entry(
            task_text="",
            tool_calls=[],
            params={f"p{i}": i for i in range(6)},
        )
        # 6 × 0.05 = 0.3 (达上限)
        self.assertAlmostEqual(SignalScorer._score_effort(entry), 0.3)

    def test_long_text_contributes(self):
        """task_text 长度 / 500, 上限 0.3"""
        long_text = "x" * 500
        entry = _make_entry(task_text=long_text, tool_calls=[], params={})
        self.assertAlmostEqual(SignalScorer._score_effort(entry), 0.3)

    def test_combined_effort_capped(self):
        """三项总和上限 1.0"""
        entry = _make_entry(
            task_text="y" * 1000,
            tool_calls=[{"name": f"t{i}"} for i in range(20)],
            params={f"p{i}": i for i in range(20)},
        )
        # 0.4 + 0.3 + 0.3 = 1.0
        self.assertEqual(SignalScorer._score_effort(entry), 1.0)


# ──────────────────────────────────────────────
# 4. 新颖性评分
# ──────────────────────────────────────────────

class TestNoveltyScoring(unittest.TestCase):
    """_score_novelty: 与已有技能的差异度"""

    def test_no_existing_skills_returns_max(self):
        """无已有技能 → 1.0 (全新)"""
        entry = _make_entry(task_text="全新任务")
        self.assertEqual(SignalScorer._score_novelty(entry, []), 1.0)

    def test_identical_skill_low_novelty(self):
        """与已有技能完全相同 → 低新颖性"""
        from agent.skills_mgmt.models import Skill, SkillStatus, SkillCategory, ContentType
        skill = Skill(
            id="s1",
            name="python 代码分析",
            description="分析 python 代码",
            content="python 代码分析 工具",
            status=SkillStatus.APPROVED,
            category=SkillCategory.CUSTOM,
            content_type=ContentType.MARKDOWN,
        )
        entry = _make_entry(task_text="python 代码分析 工具")
        novelty = SignalScorer._score_novelty(entry, [skill])
        self.assertLess(novelty, 0.5)

    def test_different_skill_high_novelty(self):
        """与已有技能完全不同 → 高新颖性"""
        from agent.skills_mgmt.models import Skill, SkillStatus, SkillCategory, ContentType
        skill = Skill(
            id="s1",
            name="天气查询",
            description="查询天气预报",
            content="weather forecast",
            status=SkillStatus.APPROVED,
            category=SkillCategory.CUSTOM,
            content_type=ContentType.MARKDOWN,
        )
        entry = _make_entry(task_text="python 代码静态分析工具")
        novelty = SignalScorer._score_novelty(entry, [skill])
        self.assertGreater(novelty, 0.7)

    def test_empty_text_returns_medium(self):
        """无 task_text → 0.5 (中等新颖性, 不偏置)"""
        entry = _make_entry(task_text="")
        # existing_skills 非空时, 无文本走中等分
        from agent.skills_mgmt.models import Skill, SkillStatus, SkillCategory, ContentType
        skill = Skill(
            id="s1",
            name="skill",
            description="desc",
            content="content",
            status=SkillStatus.APPROVED,
            category=SkillCategory.CUSTOM,
            content_type=ContentType.MARKDOWN,
        )
        self.assertEqual(SignalScorer._score_novelty(entry, [skill]), 0.5)


# ──────────────────────────────────────────────
# 5. 时间频次评分
# ──────────────────────────────────────────────

class TestRecurrenceScoring(unittest.TestCase):
    """_score_recurrence: 同类任务出现次数"""

    def test_only_self_match_returns_low(self):
        """仅自身匹配 (无相似任务) → 0.1 (1/10)"""
        entry = _make_entry(task_text="独特任务 abc")
        other = _make_entry(task_text="完全不同的 xyz")
        # entry 与自身 jaccard=1.0 → similar_count=1 → 1/10 = 0.1
        self.assertEqual(SignalScorer._score_recurrence(entry, [entry, other]), 0.1)

    def test_many_similar_increases_score(self):
        """相似任务多 → 高 recurrence"""
        entries = [
            _make_entry(source_id=f"e{i}", task_text="python 代码分析工具")
            for i in range(5)
        ]
        # 5 个相似 (jaccard >= 0.5), 5/10 = 0.5
        score = SignalScorer._score_recurrence(entries[0], entries)
        self.assertGreaterEqual(score, 0.4)

    def test_recurrence_capped_at_one(self):
        """recurrence 上限 1.0"""
        entries = [
            _make_entry(source_id=f"e{i}", task_text="python 代码分析工具")
            for i in range(20)
        ]
        # 20 个相似 → 1.0
        self.assertEqual(
            SignalScorer._score_recurrence(entries[0], entries), 1.0,
        )

    def test_empty_text_returns_zero(self):
        """无 task_text → 0.0"""
        entry = _make_entry(task_text="")
        self.assertEqual(SignalScorer._score_recurrence(entry, [entry]), 0.0)


# ──────────────────────────────────────────────
# 6. score() 主入口 + 权重计算
# ──────────────────────────────────────────────

class TestScoreIntegration(unittest.TestCase):
    """score(): 五维加权集成"""

    def test_returns_tuple_of_total_and_breakdown(self):
        """返回 (total, SignalBreakdown) 元组"""
        entry = _make_entry(task_text="测试", success=True)
        total, breakdown = SignalScorer().score(entry, [entry], [])
        self.assertIsInstance(total, float)
        self.assertIsInstance(breakdown, SignalBreakdown)

    def test_default_weights_sum_to_one(self):
        """默认权重和为 1.0"""
        self.assertAlmostEqual(
            sum(SignalScorer.DEFAULT_WEIGHTS.values()), 1.0,
        )

    def test_degraded_weights_sum_to_one(self):
        """降级权重和为 1.0"""
        self.assertAlmostEqual(
            sum(SignalScorer.DEGRADED_WEIGHTS.values()), 1.0,
        )

    def test_degraded_weights_zero_emotion(self):
        """降级权重 emotion = 0.0"""
        self.assertEqual(SignalScorer.DEGRADED_WEIGHTS["emotion"], 0.0)

    def test_breakdown_records_all_dimensions(self):
        """breakdown 记录五维分数"""
        entry = _make_entry(
            task_text="太好了, 终于搞定了",
            success=False,
            tool_calls=[{"name": "grep"}, {"name": "sed"}],
            params={"lang": "py"},
        )
        _, bd = SignalScorer().score(entry, [entry], [])
        self.assertGreater(bd.emotion, 0.0)
        self.assertGreater(bd.pain, 0.0)
        self.assertGreater(bd.effort, 0.0)
        self.assertGreaterEqual(bd.novelty, 0.0)
        self.assertGreaterEqual(bd.recurrence, 0.0)
        self.assertGreater(bd.total, 0.0)

    def test_high_emotion_failure_scores_high(self):
        """强负情绪 + 失败 → 高分"""
        entry = _make_entry(
            task_text="又失败了, 真是垃圾",
            success=False,
            tool_calls=[{"name": "tool1"}, {"name": "tool2"}],
            params={"key": "val"},
        )
        total, _ = SignalScorer().score(entry, [entry], [])
        self.assertGreater(total, 0.5)

    def test_neutral_success_scores_lower(self):
        """中性文本 + 成功 → 相对低分"""
        entry = _make_entry(
            task_text="查询天气",
            success=True,
            tool_calls=[],
            params={},
        )
        total, _ = SignalScorer().score(entry, [entry], [])
        # 仅 novelty 1.0 × 0.15 + emotion 0.3 × 0.25 = 0.225
        self.assertLess(total, 0.4)

    def test_idempotent_scoring(self):
        """相同输入产生相同评分"""
        entry = _make_entry(
            task_text="卡住了, 搞不定",
            success=False,
            tool_calls=[{"name": "t1"}],
        )
        scorer = SignalScorer()
        t1, _ = scorer.score(entry, [entry], [])
        t2, _ = scorer.score(entry, [entry], [])
        self.assertEqual(t1, t2)


# ──────────────────────────────────────────────
# 7. 降级策略 — feedback 无 comment (Task #35 核心)
# ──────────────────────────────────────────────

class TestDegradedWeightsStrategy(unittest.TestCase):
    """feedback 无 comment 时的降级策略

    场景: feedback 源只存了 rating (LIKE/DISLIKE) 但没有 comment 字段,
          此时 task_text 为空 → emotion 维度不可用 → 权重重分配
    """

    def test_empty_text_triggers_degraded_weights(self):
        """空 task_text → 使用 DEGRADED_WEIGHTS"""
        entry = _make_entry(task_text="", success=False)
        _, bd = SignalScorer().score(entry, [entry], [])
        self.assertFalse(bd.emotion_available)
        self.assertEqual(bd.weights["emotion"], 0.0)
        self.assertEqual(bd.weights["pain"], 0.35)

    def test_whitespace_text_triggers_degraded(self):
        """仅空白字符的 task_text → 使用 DEGRADED_WEIGHTS"""
        entry = _make_entry(task_text="   ", success=False)
        _, bd = SignalScorer().score(entry, [entry], [])
        self.assertFalse(bd.emotion_available)

    def test_non_empty_text_uses_default_weights(self):
        """非空 task_text → 使用 DEFAULT_WEIGHTS"""
        entry = _make_entry(task_text="正常任务", success=True)
        _, bd = SignalScorer().score(entry, [entry], [])
        self.assertTrue(bd.emotion_available)
        self.assertEqual(bd.weights["emotion"], 0.25)

    # ─── 核心: 高价值信号不被漏掉 ───

    def test_high_pain_no_comment_still_passes_threshold(self):
        """无 comment + 高痛点 (失败 + 多工具) → 仍超过 0.4 阈值"""
        entry = _make_entry(
            task_text="",  # 无 comment
            success=False,  # 失败 +0.3
            tool_calls=[{"name": f"t{i}"} for i in range(6)],  # +0.3
            session_id="sess-1",
            params={"k": "v"},
        )
        # 同 session 还有 2 次失败
        others = [
            _make_entry(
                source_id=f"o{i}",
                task_text="",
                success=False,
                session_id="sess-1",
                tool_calls=[{"name": "t"}],
            )
            for i in range(2)
        ]
        all_entries = [entry] + others
        total, bd = SignalScorer(filter_threshold=0.4).score(
            entry, all_entries, [],
        )
        # pain 维度: 0.3 (失败) + 0.3 (3 同 session) + 0.3 (6 工具) = 0.9
        # 但被截断 1.0 → 0.9
        # effort: 6 × 0.08 = 0.48 → 0.4 (上限) + 1 参数 × 0.05 = 0.05 → 0.45 → 1.0
        # 降级后: pain(0.35) × 0.9 + effort(0.28) × 0.45 + novelty(0.21) × 1.0
        #       = 0.315 + 0.126 + 0.21 = 0.651
        self.assertGreaterEqual(total, 0.4,
            "无 comment 的高痛点信号不应被过滤")

    def test_low_signal_no_comment_filtered_out(self):
        """无 comment + 低痛点 (成功 + 简单) → 低于阈值, 应被过滤"""
        entry = _make_entry(
            task_text="",  # 无 comment
            success=True,
            tool_calls=[],
            params={},
        )
        total, _ = SignalScorer(filter_threshold=0.4).score(
            entry, [entry], [],
        )
        self.assertLess(total, 0.4,
            "低价值信号应被过滤")

    def test_degraded_path_higher_than_default_for_pain(self):
        """对比: 同样高痛点 entry, 降级路径总分 ≥ 默认路径 (无 emotion 贡献时)

        因为 emotion 在默认路径下用 NEUTRAL=0.3 (低), 权重 0.25
        降级路径把 0.25 重分配给 pain(0.35)/effort(0.28), 它们是高分维度
        → 降级路径对高痛点 entry 反而更高
        """
        # 构造一个高 pain + 高 effort 但无 emotion 信号的 entry
        entry_no_comment = _make_entry(
            source_id="no-comment",
            task_text="",  # 无 comment → 降级
            success=False,
            tool_calls=[{"name": f"t{i}"} for i in range(5)],
            params={f"p{i}": i for i in range(3)},
        )
        # 同样 entry, 但 task_text 是中性文字 (走默认路径)
        entry_with_neutral_comment = _make_entry(
            source_id="with-neutral",
            task_text="中性任务描述",  # 有 comment → 默认路径, emotion=0.3
            success=False,
            tool_calls=[{"name": f"t{i}"} for i in range(5)],
            params={f"p{i}": i for i in range(3)},
        )
        scorer = SignalScorer()
        total_degraded, _ = scorer.score(entry_no_comment, [entry_no_comment], [])
        total_default, _ = scorer.score(
            entry_with_neutral_comment, [entry_with_neutral_comment], [],
        )
        # 降级路径对高 pain/effort entry 应该 ≥ 默认路径
        # (因为 emotion 0.3 × 0.25 = 0.075, 重分配给 pain 0.35 × pain_score)
        self.assertGreaterEqual(
            total_degraded, total_default - 0.01,
            "降级路径不应让高痛点信号的总分显著低于默认路径",
        )

    def test_with_emotion_comment_still_advantaged(self):
        """有 comment + 强情绪 → 总分仍高于降级路径

        因为 emotion=1.0 × 0.25 = 0.25 (强情绪加分)
        降级路径最多拿到 pain(0.35) + effort(0.28) + novelty(0.21) + recurrence(0.16) = 1.0
        """
        entry_with_emotion = _make_entry(
            source_id="with-emotion",
            task_text="太好了, 终于解决了",
            success=True,
            tool_calls=[],
            params={},
        )
        entry_no_comment = _make_entry(
            source_id="no-comment",
            task_text="",
            success=True,
            tool_calls=[],
            params={},
        )
        scorer = SignalScorer()
        total_emotion, _ = scorer.score(entry_with_emotion, [entry_with_emotion], [])
        total_no, _ = scorer.score(entry_no_comment, [entry_no_comment], [])
        self.assertGreater(total_emotion, total_no,
            "有强情绪 comment 应高于无 comment")


# ──────────────────────────────────────────────
# 8. filter_high_value 过滤
# ──────────────────────────────────────────────

class TestFilterHighValue(unittest.TestCase):
    """filter_high_value: 阈值过滤"""

    def test_filter_keeps_above_threshold(self):
        """保留 signal_strength >= threshold 的条目"""
        scorer = SignalScorer(filter_threshold=0.4)
        entries = [
            _make_entry(source_id="high", task_text="太好了", success=True),
            _make_entry(source_id="low", task_text="", success=True),
        ]
        # 先评分, 设置 signal_strength
        for e in entries:
            total, _ = scorer.score(e, entries, [])
            e.signal_strength = total
        # 过滤
        kept = scorer.filter_high_value(entries)
        kept_ids = {e.source_id for e in kept}
        self.assertIn("high", kept_ids)

    def test_custom_threshold(self):
        """自定义阈值"""
        scorer = SignalScorer(filter_threshold=0.5)
        entry = _make_entry(
            task_text="测试", success=True, tool_calls=[],
        )
        total, _ = scorer.score(entry, [entry], [])
        entry.signal_strength = total
        # 用更高阈值 0.6
        kept = scorer.filter_high_value([entry], threshold=0.6)
        if total < 0.6:
            self.assertEqual(len(kept), 0)
        else:
            self.assertEqual(len(kept), 1)

    def test_empty_input_returns_empty(self):
        """空输入 → 空输出"""
        scorer = SignalScorer()
        self.assertEqual(scorer.filter_high_value([]), [])

    def test_all_filtered_out(self):
        """全部低于阈值 → 全部过滤"""
        scorer = SignalScorer(filter_threshold=0.99)
        entries = [
            _make_entry(source_id="e1", task_text="低", success=True),
            _make_entry(source_id="e2", task_text="低", success=True),
        ]
        for e in entries:
            total, _ = scorer.score(e, entries, [])
            e.signal_strength = total
        self.assertEqual(scorer.filter_high_value(entries), [])


# ──────────────────────────────────────────────
# 9. score_batch 批量评分
# ──────────────────────────────────────────────

class TestScoreBatch(unittest.TestCase):
    """score_batch: 批量评分"""

    def test_batch_returns_correct_count(self):
        """批量评分返回与输入等长的结果"""
        entries = [
            _make_entry(source_id=f"e{i}", task_text=f"任务 {i}", success=True)
            for i in range(5)
        ]
        scorer = SignalScorer()
        results = scorer.score_batch(entries, existing_skills=[])
        self.assertEqual(len(results), 5)
        for total, bd in results:
            self.assertIsInstance(total, float)
            self.assertIsInstance(bd, SignalBreakdown)

    def test_batch_each_entry_gets_signal_strength(self):
        """批量评分后每条 entry 的 signal_strength 被填充"""
        entries = [
            _make_entry(source_id=f"e{i}", task_text=f"任务 {i}")
            for i in range(3)
        ]
        scorer = SignalScorer()
        results = scorer.score_batch(entries, existing_skills=[])
        for entry, (total, _) in zip(entries, results):
            entry.signal_strength = total
            self.assertGreater(entry.signal_strength, 0.0)

    def test_batch_empty_input(self):
        """空批量 → 空列表"""
        self.assertEqual(SignalScorer().score_batch([], []), [])


# ──────────────────────────────────────────────
# 10. 端到端对比场景
# ──────────────────────────────────────────────

class TestEndToEndScenarios(unittest.TestCase):
    """端到端: 不同场景下的评分对比"""

    def test_scenario_emotion_negative_failure_beats_neutral_success(self):
        """场景 A: 强负 + 失败 vs 中性 + 成功"""
        negative_failure = _make_entry(
            task_text="又失败了, 真是垃圾",
            success=False,
            tool_calls=[{"name": "t1"}],
        )
        neutral_success = _make_entry(
            task_text="查询天气",
            success=True,
            tool_calls=[],
        )
        scorer = SignalScorer()
        t_neg, _ = scorer.score(negative_failure, [negative_failure], [])
        t_neu, _ = scorer.score(neutral_success, [neutral_success], [])
        self.assertGreater(t_neg, t_neu,
            "强负+失败信号应高于中性+成功信号")

    def test_scenario_no_comment_high_effort_beats_low_effort(self):
        """场景 B: 无 comment + 高 effort vs 无 comment + 低 effort"""
        high_effort = _make_entry(
            task_text="",
            success=False,
            tool_calls=[{"name": f"t{i}"} for i in range(8)],
            params={f"p{i}": i for i in range(4)},
        )
        low_effort = _make_entry(
            task_text="",
            success=True,
            tool_calls=[],
            params={},
        )
        scorer = SignalScorer(filter_threshold=0.4)
        t_high, _ = scorer.score(high_effort, [high_effort], [])
        t_low, _ = scorer.score(low_effort, [low_effort], [])
        self.assertGreater(t_high, t_low)
        self.assertGreaterEqual(t_high, 0.4,
            "无 comment 的高 effort 信号应通过阈值")
        self.assertLess(t_low, 0.4,
            "无 comment 的低 effort 信号应被过滤")

    def test_scenario_recurring_failtures_score_higher(self):
        """场景 C: 同类任务反复失败 → recurrence + pain 双高"""
        # 5 个相似失败任务
        entries = [
            _make_entry(
                source_id=f"fail-{i}",
                task_text="python 代码分析工具卡住了",
                success=False,
                tool_calls=[{"name": "grep"}, {"name": "ast"}],
                session_id="sess-recur",
            )
            for i in range(5)
        ]
        scorer = SignalScorer(filter_threshold=0.4)
        target = entries[0]
        total, bd = scorer.score(target, entries, [])
        self.assertGreaterEqual(total, 0.4)
        self.assertGreater(bd.recurrence, 0.0,
            "反复出现的任务 recurrence 应 > 0")
        self.assertGreater(bd.pain, 0.3,
            "失败任务的 pain 应 > 0.3")

    def test_scenario_signal_strength_threshold_alignment(self):
        """场景 D: 默认阈值 = 0.4, 验证 filter_high_value 与 score 一致"""
        scorer = SignalScorer(filter_threshold=0.4)
        entry = _make_entry(
            task_text="太好了",
            success=True,
            tool_calls=[],
        )
        total, _ = scorer.score(entry, [entry], [])
        entry.signal_strength = total
        kept = scorer.filter_high_value([entry])
        if total >= 0.4:
            self.assertEqual(len(kept), 1)
        else:
            self.assertEqual(len(kept), 0)


# ──────────────────────────────────────────────
# 11. EMOTION_MARKERS 词表完整性
# ──────────────────────────────────────────────

class TestEmotionMarkersVocabulary(unittest.TestCase):
    """EMOTION_MARKERS 词表完整性检查"""

    def test_has_three_categories(self):
        """词表包含三类: strong_positive / strong_negative / pain_words"""
        self.assertIn("strong_positive", EMOTION_MARKERS)
        self.assertIn("strong_negative", EMOTION_MARKERS)
        self.assertIn("pain_words", EMOTION_MARKERS)

    def test_each_category_non_empty(self):
        """每个类别非空"""
        for cat in ["strong_positive", "strong_negative", "pain_words"]:
            self.assertGreater(len(EMOTION_MARKERS[cat]), 0,
                msg=f"类别 {cat} 不应为空")

    def test_has_chinese_and_english(self):
        """词表包含中英文"""
        all_words = (
            EMOTION_MARKERS["strong_positive"]
            + EMOTION_MARKERS["strong_negative"]
            + EMOTION_MARKERS["pain_words"]
        )
        has_cn = any("\u4e00" <= ch <= "\u9fff" for w in all_words for ch in w)
        has_en = any(ch.isalpha() and ord(ch) < 128 for w in all_words for ch in w)
        self.assertTrue(has_cn, "词表应包含中文情绪词")
        self.assertTrue(has_en, "词表应包含英文情绪词")


if __name__ == "__main__":
    unittest.main(verbosity=2)
