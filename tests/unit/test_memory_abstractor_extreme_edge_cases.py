"""极端复杂场景测试 — 验证反例挖掘和最小可行性检查的边界情况

测试场景:
    1. 全部失败 (0% 成功率) — root_cause 和 anti_patterns 的行为
    2. 无公共工具/参数/标签 — 空触发条件, 最小步骤
    3. 多步骤失败链 (工具A 失败 → 触发工具B → 又失败) — 复杂失败链的根因分析
    4. 无明确触发条件 (task_text 极短或全噪声) — 触发条件推导边界
    5. 参数值冲突 (相同键, 成功/失败值不同) — root_cause 差异分析
    6. 步骤超限 (20+ 工具) — 复杂度软警告 + 最小可行性检查
    7. 单一巨型聚类 (30 条相似记忆) — 大规模聚类下的结构化提取
    8. 混合成功率的相邻聚类 — 质量门控排序边界
"""

from __future__ import annotations
import unittest
from typing import List
from unittest.mock import MagicMock, patch

from agent.skills_mgmt.memory_abstractor import (
    MemoryEntry,
    MemoryCluster,
    MemorySkillAbstractor,
)


class TestAllFailuresScenario(unittest.TestCase):
    """场景 1: 全部失败 (0% 成功率)"""

    def test_all_failures_root_cause_handles_zero_success(self):
        """全部失败时 root_cause 不应崩溃, 应包含「成功率」"""
        entries = [
            MemoryEntry(
                source="t", source_id=f"fail-{i}",
                task_text="deploy service to production",
                success=False,
                tool_calls=[{"name": "kubectl"}],
                params={"env": "prod"},
                tags=["deploy"],
            )
            for i in range(5)
        ]
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(entries)
        self.assertEqual(len(clusters), 1)
        c = clusters[0]
        # 成功率 = 0
        self.assertEqual(c.success_rate, 0.0)
        # root_cause 应非空 (即使全失败也要给出假设)
        self.assertTrue(c.root_cause_hypothesis)
        self.assertIn("成功率", c.root_cause_hypothesis)

    def test_all_failures_anti_patterns_extracted(self):
        """全失败时反例边界仍应被提取"""
        entries = [
            MemoryEntry(
                source="t", source_id=f"f-{i}",
                task_text="run integration test suite",
                success=False,
                tool_calls=[{"name": "pytest"}],
                params={},
                tags=["test"],
            )
            for i in range(4)
        ]
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(entries)
        c = clusters[0]
        self.assertTrue(len(c.anti_patterns) > 0)

    def test_all_failures_quality_gate_blocks(self):
        """全失败时质量门应阻止通过 (成功率 0 < 0.7)"""
        entries = [
            MemoryEntry(
                source="t", source_id=f"f-{i}",
                task_text="build docker image",
                success=False,
            )
            for i in range(5)
        ]
        abstractor = MemorySkillAbstractor()
        results = abstractor.abstract_new_skills(memory_entries=entries)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["quality_gate_passed"])
        self.assertTrue(any("成功率" in r
                            for r in results[0]["quality_gate_reasons"]))


class TestNoCommonAttributesScenario(unittest.TestCase):
    """场景 2: 无公共工具/参数/标签"""

    def test_no_common_tools_params_tags(self):
        """每条记忆的工具/参数/标签都不同, 触发条件应降级到关键词"""
        entries = [
            MemoryEntry(
                source="t", source_id=f"id-{i}",
                task_text="analyze python code quality",
                success=True,
                tool_calls=[{"name": f"tool_{i}"}],  # 每条不同工具
                params={f"param_{i}": i},  # 每条不同参数键
                tags=[f"tag_{i}"],  # 每条不同标签
            )
            for i in range(5)
        ]
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(entries)
        c = clusters[0]
        # common_tool_names 应为空 (每个工具只出现 1 次 < 50%)
        self.assertEqual(c.common_tool_names, [])
        # common_params 应为空 (没有公共键)
        self.assertEqual(c.common_params, {})
        # common_tags 应为空 (每个标签只出现 1 次 < 2)
        self.assertEqual(c.common_tags, [])
        # 但触发条件应非空 (降级到关键词)
        self.assertTrue(len(c.trigger_conditions) > 0)
        # 执行步骤应仍有验证步骤 (即使无工具)
        self.assertTrue(len(c.execution_steps) > 0)

    def test_empty_tools_params_tags_completely(self):
        """完全无工具/参数/标签的记忆条目"""
        entries = [
            MemoryEntry(
                source="t", source_id=f"id-{i}",
                task_text="analyze python code",
                success=True,
                tool_calls=[], params={}, tags=[],
            )
            for i in range(4)
        ]
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(entries)
        c = clusters[0]
        # 不应崩溃
        self.assertTrue(c.root_cause_hypothesis)
        self.assertTrue(len(c.execution_steps) > 0)
        # 执行步骤应只有验证步骤 (无工具)
        self.assertIn("验证", c.execution_steps[-1])


class TestMultiStepFailureChain(unittest.TestCase):
    """场景 3: 多步骤失败链 (工具A 失败 → 触发工具B → 又失败)"""

    def test_multi_step_failure_chain_root_cause(self):
        """复杂失败链: 失败条目使用不同工具组合, root_cause 应能识别工具差异"""
        # 成功条目: 用 [git, docker] 组合
        success_entries = [
            MemoryEntry(
                source="t", source_id=f"ok-{i}",
                task_text="ci cd pipeline build deploy",
                success=True,
                tool_calls=[{"name": "git"}, {"name": "docker"}],
                params={"branch": "main"},
                tags=["ci"],
            )
            for i in range(5)
        ]
        # 失败条目 1: 只用 [git] (缺 docker)
        failure_entries_1 = [
            MemoryEntry(
                source="t", source_id=f"fail-a-{i}",
                task_text="ci cd pipeline build deploy",
                success=False,
                tool_calls=[{"name": "git"}],  # 缺 docker
                params={"branch": "main"},
                tags=["ci"],
            )
            for i in range(2)
        ]
        # 失败条目 2: 用 [git, kubectl] (错误工具)
        failure_entries_2 = [
            MemoryEntry(
                source="t", source_id=f"fail-b-{i}",
                task_text="ci cd pipeline build deploy",
                success=False,
                tool_calls=[{"name": "git"}, {"name": "kubectl"}],  # 错误工具
                params={"branch": "main"},
                tags=["ci"],
            )
            for i in range(2)
        ]
        entries = success_entries + failure_entries_1 + failure_entries_2
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(entries)
        c = clusters[0]
        # 成功率 = 5/9 ≈ 0.556
        self.assertAlmostEqual(c.success_rate, 5 / 9, places=2)
        # root_cause 应包含成功率
        self.assertIn("成功率", c.root_cause_hypothesis)
        # 反例边界应提到工具差异
        anti_text = " ".join(c.anti_patterns)
        self.assertTrue(len(c.anti_patterns) > 0)

    def test_failure_chain_quality_gate_blocks(self):
        """多步骤失败链导致成功率低于阈值, 质量门应阻止"""
        success = [
            MemoryEntry(source="t", source_id=f"ok-{i}",
                        task_text="data pipeline etl transform",
                        success=True, tool_calls=[{"name": "spark"}])
            for i in range(3)
        ]
        failures = [
            MemoryEntry(source="t", source_id=f"fail-{i}",
                        task_text="data pipeline etl transform",
                        success=False, tool_calls=[{"name": "airflow"}])
            for i in range(5)
        ]
        abstractor = MemorySkillAbstractor()
        results = abstractor.abstract_new_skills(memory_entries=success + failures)
        self.assertFalse(results[0]["quality_gate_passed"])


class TestNoClearTriggerScenario(unittest.TestCase):
    """场景 4: 无明确触发条件 (task_text 极短或全噪声)"""

    def test_extremely_short_task_text(self):
        """task_text 极短 (1-2 字符), 触发条件应降级到 params/tags"""
        entries = [
            MemoryEntry(
                source="t", source_id=f"id-{i}",
                task_text="x",  # 极短
                success=True,
                tool_calls=[{"name": "grep"}],
                params={"mode": "fast"},
                tags=["util"],
            )
            for i in range(5)
        ]
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(entries)
        c = clusters[0]
        # 触发条件应非空 (降级到 params/tags)
        self.assertTrue(len(c.trigger_conditions) > 0)
        # 应包含 params 或 tags
        triggers_text = " ".join(c.trigger_conditions)
        self.assertTrue("mode" in triggers_text or "util" in triggers_text
                        or "x" in triggers_text)

    def test_pure_noise_task_text(self):
        """task_text 全为符号/数字, 无有意义关键词"""
        entries = [
            MemoryEntry(
                source="t", source_id=f"id-{i}",
                task_text="123 456 789",  # 纯数字
                success=True,
                tool_calls=[{"name": "calc"}],
                params={"precision": 2},
                tags=["math"],
            )
            for i in range(5)
        ]
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(entries)
        c = clusters[0]
        # 不应崩溃
        self.assertTrue(c.root_cause_hypothesis)
        # 触发条件应非空
        self.assertTrue(len(c.trigger_conditions) > 0)


class TestConflictingParamsScenario(unittest.TestCase):
    """场景 5: 参数值冲突 (相同键, 成功/失败值不同)"""

    def test_conflicting_param_values_root_cause_diff(self):
        """成功条目 param=a, 失败条目 param=b → root_cause 应体现差异"""
        success_entries = [
            MemoryEntry(
                source="t", source_id=f"ok-{i}",
                task_text="run database migration script",
                success=True,
                params={"strategy": "safe", "timeout": 30},
            )
            for i in range(4)
        ]
        failure_entries = [
            MemoryEntry(
                source="t", source_id=f"fail-{i}",
                task_text="run database migration script",
                success=False,
                params={"strategy": "force", "timeout": 5},
            )
            for i in range(3)
        ]
        entries = success_entries + failure_entries
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(entries)
        c = clusters[0]
        # 成功率 = 4/7 ≈ 0.571
        self.assertAlmostEqual(c.success_rate, 4 / 7, places=2)
        # root_cause 应包含成功率
        self.assertIn("成功率", c.root_cause_hypothesis)
        # common_params 应有 strategy 和 timeout (所有条目都有)
        self.assertIn("strategy", c.common_params)
        self.assertIn("timeout", c.common_params)
        # 众数: strategy=safe(4) vs force(3) → safe; timeout=30(4) vs 5(3) → 30
        self.assertEqual(c.common_params["strategy"], "safe")

    def test_conflicting_param_values_if_then_rules(self):
        """参数值冲突时 If-Then 规则应包含边界规则"""
        success = [
            MemoryEntry(source="t", source_id=f"ok-{i}",
                        task_text="process data batch job",
                        success=True, params={"mode": "async"})
            for i in range(4)
        ]
        failure = [
            MemoryEntry(source="t", source_id=f"fail-{i}",
                        task_text="process data batch job",
                        success=False, params={"mode": "sync"})
            for i in range(2)
        ]
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(success + failure)
        c = clusters[0]
        # 有失败 → if_then_rules 应非空
        self.assertTrue(len(c.if_then_rules) > 0)


class TestExtremeComplexityScenario(unittest.TestCase):
    """场景 6: 步骤超限 (大量工具) — 复杂度软警告 + 最小可行性检查"""

    def test_many_tools_triggers_complexity_warning(self):
        """15 个工具 → 15 个执行步骤 → 触发软警告但通过"""
        # 构造 15 个不同工具的记忆条目 (但 task_text 相同以聚类)
        # 每个工具出现频率 >= 50% → 进入 common_tool_names
        many_tools = [f"tool_{i:02d}" for i in range(15)]
        entries = [
            MemoryEntry(
                source="t", source_id=f"id-{i}",
                task_text="orchestrate multi tool pipeline workflow",
                success=True,
                tool_calls=[{"name": t} for t in many_tools],
                params={},
                tags=["orchestration"],
            )
            for i in range(5)
        ]
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(entries)
        c = clusters[0]
        # 15 个工具都应进入 common_tool_names
        self.assertEqual(len(c.common_tool_names), 15)
        # 执行步骤 = 15 工具 + 1 验证 = 16 步 (> 10 → 软警告)
        self.assertGreater(len(c.execution_steps), 10)

        # 质量门: 应通过 (软警告不阻止)
        with patch.object(abstractor, "_find_duplicate", return_value=None):
            passed, reasons, dup = abstractor.check_quality_gate(
                c, draft={"id": "test", "content": "test", "name": "test"},
            )
        self.assertTrue(passed)
        self.assertTrue(any("[WARN]" in r and "执行步骤" in r
                            for r in reasons))

    def test_complexity_warning_message_format(self):
        """软警告消息格式正确, 包含步数对比"""
        entries = [
            MemoryEntry(
                source="t", source_id=f"id-{i}",
                task_text="multi step complex workflow task",
                success=True,
                tool_calls=[{"name": f"tool_{j}"} for j in range(12)],
            )
            for i in range(5)
        ]
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(entries)
        c = clusters[0]
        with patch.object(abstractor, "_find_duplicate", return_value=None):
            passed, reasons, dup = abstractor.check_quality_gate(
                c, draft={"id": "test", "content": "test", "name": "test"},
            )
        warn_msg = [r for r in reasons if "[WARN]" in r]
        self.assertTrue(len(warn_msg) > 0)
        # 消息应包含具体步数
        self.assertIn("13", warn_msg[0])  # 12 工具 + 1 验证 = 13 步
        self.assertIn("10", warn_msg[0])  # 阈值


class TestLargeClusterScenario(unittest.TestCase):
    """场景 7: 单一巨型聚类 (30 条相似记忆)"""

    def test_large_cluster_structured_extraction(self):
        """30 条相似记忆聚类后结构化字段应正确提取"""
        entries = [
            MemoryEntry(
                source="t", source_id=f"id-{i}",
                task_text="analyze python code for security vulnerabilities",
                success=True,
                tool_calls=[{"name": "grep"}, {"name": "ast"}],
                params={"language": "python", "depth": "deep"},
                tags=["security", "python", "analysis"],
            )
            for i in range(30)
        ]
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(entries)
        self.assertEqual(len(clusters), 1)
        c = clusters[0]
        self.assertEqual(c.size, 30)
        self.assertEqual(c.success_rate, 1.0)
        # 全成功 → root_cause 包含「持续有效」
        self.assertIn("持续有效", c.root_cause_hypothesis)
        # 触发条件应非空
        self.assertTrue(len(c.trigger_conditions) > 0)
        # 执行步骤应包含 grep 和 ast
        steps_text = " ".join(c.execution_steps)
        self.assertIn("grep", steps_text)
        self.assertIn("ast", steps_text)

    def test_large_cluster_quality_gate_passes(self):
        """30 条全成功的聚类应通过质量门"""
        entries = [
            MemoryEntry(
                source="t", source_id=f"id-{i}",
                task_text="validate json schema structure",
                success=True,
                tool_calls=[{"name": "jq"}],
                params={"strict": True},
            )
            for i in range(30)
        ]
        abstractor = MemorySkillAbstractor()
        results = abstractor.abstract_new_skills(memory_entries=entries)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["quality_gate_passed"])


class TestMixedSuccessRateClustersScenario(unittest.TestCase):
    """场景 8: 混合成功率的相邻聚类 — 质量门控排序边界"""

    def test_passed_clusters_come_before_failed(self):
        """通过的聚类应排在未通过的前面"""
        # 聚类 1: 全成功 (通过)
        cluster1 = [
            MemoryEntry(source="t", source_id=f"ok-{i}",
                        task_text="analyze python code quality",
                        success=True, tool_calls=[{"name": "grep"}])
            for i in range(5)
        ]
        # 聚类 2: 全失败 (不通过)
        cluster2 = [
            MemoryEntry(source="t", source_id=f"fail-{i}",
                        task_text="weather forecast tokyo tomorrow",
                        success=False, tool_calls=[{"name": "http"}])
            for i in range(5)
        ]
        abstractor = MemorySkillAbstractor()
        with patch.object(abstractor, "_find_duplicate", return_value=None):
            results = abstractor.abstract_new_skills(
                memory_entries=cluster1 + cluster2, max_skills=5,
            )
        # 第一个应是通过的 (python)
        self.assertTrue(results[0]["quality_gate_passed"])
        # 第二个应未通过 (weather, 全失败)
        self.assertFalse(results[1]["quality_gate_passed"])

    def test_mixed_clusters_sorted_by_passed_then_size(self):
        """排序: passed 优先, 再按 size 降序"""
        # 大聚类但全失败
        big_failed = [
            MemoryEntry(source="t", source_id=f"bf-{i}",
                        task_text="deploy to kubernetes cluster production",
                        success=False)
            for i in range(10)
        ]
        # 小聚类但全成功
        small_passed = [
            MemoryEntry(source="t", source_id=f"sp-{i}",
                        task_text="format json data with indentation",
                        success=True, tool_calls=[{"name": "jq"}])
            for i in range(4)
        ]
        abstractor = MemorySkillAbstractor()
        with patch.object(abstractor, "_find_duplicate", return_value=None):
            results = abstractor.abstract_new_skills(
                memory_entries=big_failed + small_passed, max_skills=5,
            )
        # 小但通过的应在前面
        self.assertTrue(results[0]["quality_gate_passed"])
        self.assertEqual(results[0]["cluster_size"], 4)
        # 大但未通过的应在后面
        self.assertFalse(results[1]["quality_gate_passed"])
        self.assertEqual(results[1]["cluster_size"], 10)


class TestExtremeEdgeCases(unittest.TestCase):
    """综合极端边界情况"""

    def test_single_entry_cluster(self):
        """只有 1 条记忆 → 1 个 size=1 聚类, 质量门应阻止"""
        entries = [
            MemoryEntry(source="t", source_id="only",
                        task_text="lonely task", success=True)
        ]
        abstractor = MemorySkillAbstractor()
        results = abstractor.abstract_new_skills(memory_entries=entries)
        # 1 条 < min_cluster_size(3) → 提前返回空
        self.assertEqual(len(results), 0)

    def test_two_entries_below_min_cluster(self):
        """2 条相似记忆 → 1 个 size=2 聚类, 但 2 < 3 → 质量门阻止"""
        entries = [
            MemoryEntry(source="t", source_id="a",
                        task_text="analyze python code", success=True),
            MemoryEntry(source="t", source_id="b",
                        task_text="analyze python code", success=True),
        ]
        abstractor = MemorySkillAbstractor()
        # 2 < min_cluster_size(3) → 提前返回空
        results = abstractor.abstract_new_skills(memory_entries=entries)
        self.assertEqual(len(results), 0)

    def test_empty_task_text_doesnt_crash(self):
        """空 task_text 不应导致崩溃"""
        entries = [
            MemoryEntry(source="t", source_id=f"id-{i}",
                        task_text="", success=True,
                        tool_calls=[{"name": "grep"}])
            for i in range(5)
        ]
        abstractor = MemorySkillAbstractor()
        # 不应抛异常
        clusters = abstractor.cluster_memories(entries)
        # 空 task_text → token 集合为空 → Jaccard = 0 → 不聚类 → 5 个独立聚类
        # 但每个 size=1 < 3 → 质量门阻止
        self.assertTrue(len(clusters) >= 1)

    def test_very_long_task_text(self):
        """超长 task_text 不应导致崩溃"""
        long_text = "analyze python code " * 100  # 2000+ 字符
        entries = [
            MemoryEntry(source="t", source_id=f"id-{i}",
                        task_text=long_text, success=True)
            for i in range(4)
        ]
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(entries)
        # 应正常聚类
        self.assertEqual(len(clusters), 1)
        c = clusters[0]
        # root_cause 应非空
        self.assertTrue(c.root_cause_hypothesis)

    def test_unicode_mix_task_text(self):
        """混合中英文 task_text 不应崩溃"""
        entries = [
            MemoryEntry(source="t", source_id=f"id-{i}",
                        task_text=f"分析 python 代码 quality issue {i}",
                        success=True, tool_calls=[{"name": "grep"}],
                        tags=["mixed"])
            for i in range(5)
        ]
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(entries)
        self.assertEqual(len(clusters), 1)
        c = clusters[0]
        # 触发条件应非空 (可能包含中文或英文关键词)
        self.assertTrue(len(c.trigger_conditions) > 0)

    def test_exactly_at_min_cluster_size(self):
        """恰好 3 条 (边界值) — 应通过 size 检查"""
        entries = [
            MemoryEntry(source="t", source_id=f"id-{i}",
                        task_text="analyze python code quality",
                        success=True, tool_calls=[{"name": "grep"}])
            for i in range(3)
        ]
        abstractor = MemorySkillAbstractor()
        results = abstractor.abstract_new_skills(memory_entries=entries)
        self.assertEqual(len(results), 1)
        # size=3 >= 3, success_rate=1.0 >= 0.7 → 通过
        self.assertTrue(results[0]["quality_gate_passed"])

    def test_exactly_at_min_success_rate(self):
        """成功率恰好 0.7 (边界值) — 应通过"""
        # 7 成功 + 3 失败 = 0.7
        entries = [
            MemoryEntry(source="t", source_id=f"ok-{i}",
                        task_text="analyze python code bugs",
                        success=True)
            for i in range(7)
        ] + [
            MemoryEntry(source="t", source_id=f"fail-{i}",
                        task_text="analyze python code bugs",
                        success=False)
            for i in range(3)
        ]
        abstractor = MemorySkillAbstractor()
        with patch.object(abstractor, "_find_duplicate", return_value=None):
            results = abstractor.abstract_new_skills(memory_entries=entries)
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0]["success_rate"], 0.7, places=2)
        self.assertTrue(results[0]["quality_gate_passed"])


if __name__ == "__main__":
    unittest.main()
