"""50 条无 comment 反馈 (仅 rating) 测试集 — 验证降级策略过滤效果

数据设计 (全部 task_text="", 模拟 feedback 源只存 LIKE/DISLIKE 没有 comment):
    Group A (15 条): 高痛点 — 反复失败 + 复杂工具链 + 同 session 多次失败
                     → 预期通过 0.4 阈值
    Group B (10 条): 中等痛点 — 单次失败 + 中等工具链, 无 session 关联
                     → 预期临界 (部分通过)
    Group C (15 条): 低痛点 — 成功 + 简单工具链
                     → 预期被过滤
    Group D (10 条): 噪声 — 成功 + 无工具无参数
                     → 预期全部被过滤 (仅 novelty 贡献 0.21)

验证:
    1. 全部 50 条 task_text 为空 → 全部触发 DEGRADED_WEIGHTS
    2. 高痛点信号 (失败+多工具+同session) 不被漏掉
    3. 低价值信号被正确过滤
    4. 过滤后保留率符合预期分布
    5. 阈值调整对过滤效果的影响
"""

from __future__ import annotations
import unittest
from typing import List, Tuple

from agent.skills_mgmt.memory_abstractor import MemoryEntry
from agent.skills_mgmt.signal_scorer import SignalScorer, SignalBreakdown


# ──────────────────────────────────────────────
# 50 条无 comment 测试数据构造
# ──────────────────────────────────────────────

def _make_no_comment_entry(source_id: str,
                            success: bool,
                            tool_count: int = 0,
                            param_count: int = 0,
                            session_id: str = "",
                            ) -> MemoryEntry:
    """构造无 comment 的 MemoryEntry (task_text 恒为空)"""
    return MemoryEntry(
        source="feedback",
        source_id=source_id,
        task_text="",  # 无 comment — 触发降级
        success=success,
        tool_calls=[{"name": f"tool_{i}"} for i in range(tool_count)],
        params={f"param_{i}": i for i in range(param_count)},
        tags=[],
        timestamp="2026-07-05T10:00:00",
        session_id=session_id,
    )


def build_50_no_comment_dataset() -> List[MemoryEntry]:
    """构造 50 条无 comment 反馈测试集

    分布:
        Group A (15 条): 高痛点 — 3 个 session, 每个 5 条反复失败 + 6-10 工具
        Group B (10 条): 中等痛点 — 单次失败 + 3-5 工具, 无 session
        Group C (15 条): 低痛点 — 成功 + 1-2 工具
        Group D (10 条): 噪声 — 成功 + 无工具
    """
    entries: List[MemoryEntry] = []

    # ─── Group A: 高痛点 (15 条) ───
    # 3 个 session, 每个 session 5 条反复失败, 工具链复杂
    for sess_idx in range(3):
        sess_id = f"sess-pain-{sess_idx + 1}"
        for i in range(5):
            entries.append(_make_no_comment_entry(
                source_id=f"A-s{sess_idx}-{i}",
                success=False,
                tool_count=6 + sess_idx + (i % 3),  # 6-11 工具
                param_count=3 + (i % 3),             # 3-5 参数
                session_id=sess_id,
            ))

    # ─── Group B: 中等痛点 (10 条) ───
    # 单次失败 + 中等工具链, 无 session 关联
    for i in range(10):
        entries.append(_make_no_comment_entry(
            source_id=f"B-{i}",
            success=False,
            tool_count=3 + (i % 3),  # 3-5 工具
            param_count=1 + (i % 2),  # 1-2 参数
            session_id="",           # 无 session → 无反复失败加成
        ))

    # ─── Group C: 低痛点 (15 条) ───
    # 成功 + 简单工具链
    for i in range(15):
        entries.append(_make_no_comment_entry(
            source_id=f"C-{i}",
            success=True,
            tool_count=1 + (i % 2),  # 1-2 工具
            param_count=i % 2,       # 0-1 参数
            session_id="",
        ))

    # ─── Group D: 噪声 (10 条) ───
    # 成功 + 无工具无参数
    for i in range(10):
        entries.append(_make_no_comment_entry(
            source_id=f"D-{i}",
            success=True,
            tool_count=0,
            param_count=0,
            session_id="",
        ))

    return entries


# ──────────────────────────────────────────────
# 测试
# ──────────────────────────────────────────────

class TestFiftyNoCommentDataset(unittest.TestCase):
    """50 条无 comment 反馈测试集 — 降级策略过滤效果验证"""

    @classmethod
    def setUpClass(cls):
        """一次性构造数据集, 所有测试共用"""
        cls.entries = build_50_no_comment_dataset()
        cls.scorer = SignalScorer(filter_threshold=0.4)
        # 评分 (无已有技能 → novelty 满分)
        cls.results: List[Tuple[float, SignalBreakdown]] = []
        for e in cls.entries:
            total, bd = cls.scorer.score(e, cls.entries, [])
            e.signal_strength = total
            cls.results.append((total, bd))

    # ─── 数据集完整性 ───

    def test_dataset_has_50_entries(self):
        """数据集包含 50 条记忆"""
        self.assertEqual(len(self.entries), 50)

    def test_all_entries_have_empty_task_text(self):
        """全部 50 条 task_text 为空 (模拟无 comment feedback)"""
        for e in self.entries:
            self.assertEqual(e.task_text, "",
                msg=f"source_id={e.source_id} task_text 应为空")

    def test_group_distribution(self):
        """4 组数据分布正确: A=15, B=10, C=15, D=10"""
        group_a = [e for e in self.entries if e.source_id.startswith("A-")]
        group_b = [e for e in self.entries if e.source_id.startswith("B-")]
        group_c = [e for e in self.entries if e.source_id.startswith("C-")]
        group_d = [e for e in self.entries if e.source_id.startswith("D-")]
        self.assertEqual(len(group_a), 15)
        self.assertEqual(len(group_b), 10)
        self.assertEqual(len(group_c), 15)
        self.assertEqual(len(group_d), 10)

    # ─── 降级权重验证 ───

    def test_all_entries_trigger_degraded_weights(self):
        """全部 50 条触发 DEGRADED_WEIGHTS (因为 task_text 全空)"""
        for total, bd in self.results:
            self.assertFalse(bd.emotion_available,
                msg="task_text 为空时 emotion_available 应为 False")
            self.assertEqual(bd.weights["emotion"], 0.0,
                msg="降级时 emotion 权重应为 0")
            self.assertEqual(bd.weights["pain"], 0.35,
                msg="降级时 pain 权重应为 0.35")

    # ─── 高痛点信号不被漏掉 (核心验证) ───

    def test_group_a_high_pain_all_pass_threshold(self):
        """Group A (高痛点) 全部通过 0.4 阈值 — 不被漏掉"""
        group_a_results = [
            (total, bd) for (total, bd), e in zip(self.results, self.entries)
            if e.source_id.startswith("A-")
        ]
        passed = sum(1 for total, _ in group_a_results if total >= 0.4)
        self.assertEqual(passed, 15,
            f"Group A 应全部通过 0.4 阈值, 实际通过 {passed}/15")

    def test_group_a_signal_strength_distribution(self):
        """Group A 信号强度分布合理 (高痛点 → 高分)"""
        group_a_scores = [
            total for (total, _), e in zip(self.results, self.entries)
            if e.source_id.startswith("A-")
        ]
        avg_a = sum(group_a_scores) / len(group_a_scores)
        # 高痛点平均信号强度应 >= 0.5
        self.assertGreater(avg_a, 0.5,
            f"Group A 平均信号强度应 > 0.5, 实际 {avg_a:.3f}")

    def test_group_a_pain_dimension_high(self):
        """Group A 的 pain 维度分数高 (> 0.5)"""
        group_a_pains = [
            bd.pain for (_, bd), e in zip(self.results, self.entries)
            if e.source_id.startswith("A-")
        ]
        for pain in group_a_pains:
            self.assertGreater(pain, 0.5,
                f"高痛点 entry 的 pain 维度应 > 0.5, 实际 {pain:.3f}")

    # ─── 中等痛点信号 ───

    def test_group_b_medium_pain_partial_pass(self):
        """Group B (中等痛点) 部分通过 — 临界分布"""
        group_b_scores = [
            total for (total, _), e in zip(self.results, self.entries)
            if e.source_id.startswith("B-")
        ]
        # 中等痛点应有部分通过 0.4 (至少 3 条), 但不要求全部
        passed = sum(1 for s in group_b_scores if s >= 0.4)
        self.assertGreaterEqual(passed, 3,
            f"Group B 应有至少 3 条通过 0.4, 实际 {passed}/10")

    def test_group_b_signal_between_a_and_c(self):
        """Group B 平均信号强度介于 A 和 C 之间"""
        avg_a = sum(t for (t, _), e in zip(self.results, self.entries)
                    if e.source_id.startswith("A-")) / 15
        avg_b = sum(t for (t, _), e in zip(self.results, self.entries)
                    if e.source_id.startswith("B-")) / 10
        avg_c = sum(t for (t, _), e in zip(self.results, self.entries)
                    if e.source_id.startswith("C-")) / 15
        self.assertGreater(avg_a, avg_b,
            f"Group A 平均应 > B (A={avg_a:.3f}, B={avg_b:.3f})")
        self.assertGreater(avg_b, avg_c,
            f"Group B 平均应 > C (B={avg_b:.3f}, C={avg_c:.3f})")

    # ─── 低价值信号被过滤 ───

    def test_group_c_low_pain_mostly_filtered(self):
        """Group C (低痛点) 大部分被过滤"""
        group_c_scores = [
            total for (total, _), e in zip(self.results, self.entries)
            if e.source_id.startswith("C-")
        ]
        filtered = sum(1 for s in group_c_scores if s < 0.4)
        # 至少 12/15 被过滤
        self.assertGreaterEqual(filtered, 12,
            f"Group C 应有至少 12 条被过滤, 实际 {filtered}/15")

    def test_group_d_noise_all_filtered(self):
        """Group D (噪声) 全部被过滤"""
        group_d_scores = [
            total for (total, _), e in zip(self.results, self.entries)
            if e.source_id.startswith("D-")
        ]
        for s in group_d_scores:
            self.assertLess(s, 0.4,
                f"噪声 entry 信号应 < 0.4, 实际 {s:.3f}")

    def test_group_d_only_novelty_contributes(self):
        """Group D 仅 novelty 贡献 (无 existing_skills → 1.0 × 0.21 = 0.21)"""
        group_d_results = [
            (total, bd) for (total, bd), e in zip(self.results, self.entries)
            if e.source_id.startswith("D-")
        ]
        for total, bd in group_d_results:
            self.assertAlmostEqual(bd.novelty, 1.0)
            self.assertAlmostEqual(bd.pain, 0.0)
            self.assertAlmostEqual(bd.effort, 0.0)
            # total ≈ 0.21 (novelty 1.0 × weight 0.21)
            self.assertAlmostEqual(total, 0.21, delta=0.05)

    # ─── 过滤效果整体验证 ───

    def test_filter_high_value_returns_subset(self):
        """filter_high_value 保留高价值信号, 过滤低价值"""
        kept = self.scorer.filter_high_value(self.entries)
        # 应保留 Group A (15) + 部分 Group B (≥3) = ≥18
        self.assertGreaterEqual(len(kept), 18,
            f"应保留至少 18 条 (A=15 + 部分 B), 实际 {len(kept)}")
        # 应过滤 Group D (10) + 大部分 Group C (≥12) = ≤22 保留
        self.assertLessEqual(len(kept), 25,
            f"应过滤大部分低价值, 保留 ≤25 条, 实际 {len(kept)}")

    def test_filtered_out_are_low_signal(self):
        """被过滤的全部是低信号强度 (< 0.4)"""
        kept = self.scorer.filter_high_value(self.entries)
        kept_ids = {e.source_id for e in kept}
        for e in self.entries:
            if e.source_id not in kept_ids:
                self.assertLess(e.signal_strength, 0.4,
                    f"{e.source_id} 应被过滤 (signal={e.signal_strength:.3f})")

    def test_kept_all_above_threshold(self):
        """保留的全部 signal_strength >= 0.4"""
        kept = self.scorer.filter_high_value(self.entries)
        for e in kept:
            self.assertGreaterEqual(e.signal_strength, 0.4,
                f"{e.source_id} signal 应 >= 0.4")

    # ─── 阈值敏感性 ───

    def test_lower_threshold_keeps_more(self):
        """阈值降到 0.3 → 保留更多 (含部分 Group B/C)"""
        kept_04 = self.scorer.filter_high_value(self.entries, threshold=0.4)
        kept_03 = self.scorer.filter_high_value(self.entries, threshold=0.3)
        self.assertGreater(len(kept_03), len(kept_04),
            "阈值 0.3 应比 0.4 保留更多条目")

    def test_higher_threshold_keeps_only_a(self):
        """阈值升到 0.6 → 仅保留 Group A 中的高分条目"""
        kept_06 = self.scorer.filter_high_value(self.entries, threshold=0.6)
        # 全部应来自 Group A
        for e in kept_06:
            self.assertTrue(e.source_id.startswith("A-"),
                f"阈值 0.6 应仅保留 Group A, 但包含 {e.source_id}")

    # ─── 降级 vs 默认路径对比 ───

    def test_degraded_path_preserves_high_pain_signals(self):
        """降级路径下高痛点信号不被漏掉 — 与默认路径对比

        构造同样 pain/effort 但有 comment 的对照组:
        - 有 comment (中性文本): 走 DEFAULT_WEIGHTS
        - 无 comment: 走 DEGRADED_WEIGHTS

        两者差距应小 (降级不显著拉低高痛点信号)
        """
        # 取 Group A 第一条作为基准, 构造有 comment 的对照
        baseline = self.entries[0]  # A-s0-0
        # 对照组: 同样字段但 task_text 非空
        with_comment = MemoryEntry(
            source=baseline.source,
            source_id=baseline.source_id + "-with-comment",
            task_text="中性反馈文本",  # 有 comment
            success=baseline.success,
            tool_calls=baseline.tool_calls,
            params=baseline.params,
            tags=baseline.tags,
            timestamp=baseline.timestamp,
            session_id=baseline.session_id,
        )
        # 评分时用同样的 all_entries (保证 pain/recurrence 一致)
        scorer = SignalScorer()
        total_no, bd_no = scorer.score(baseline, self.entries, [])
        total_with, bd_with = scorer.score(with_comment, self.entries, [])
        # 降级路径不显著拉低 (差距 < 0.15)
        self.assertLess(abs(total_no - total_with), 0.15,
            f"降级路径与默认路径差距应 < 0.15 "
            f"(无comment={total_no:.3f}, 有comment={total_with:.3f})")
        # 降级路径 emotion_available=False
        self.assertFalse(bd_no.emotion_available)
        self.assertTrue(bd_with.emotion_available)


if __name__ == "__main__":
    unittest.main(verbosity=2)
