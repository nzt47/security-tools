"""记忆 → 技能自动抽象器测试

覆盖:
    1. 归一化 MemoryEntry 数据结构
    2. Jaccard 聚类算法
    3. 模式提取 (common_tool_names / common_params / common_tags)
    4. 草稿生成 (skill_id 稳定性 / 字段完整性)
    5. 质量门控 (聚类大小 / 成功率 / 重复检测)
    6. 端到端: 模拟记忆 → 抽象 → (可选) 注册
    7. P0 结构化字段提取 (根因/触发/步骤/规则/反例)
    8. 复杂度软警告门控
"""

from __future__ import annotations
import os
import tempfile
import unittest
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

from agent.skills_mgmt.memory_abstractor import (
    MemoryEntry,
    MemoryCluster,
    MemorySkillAbstractor,
    _jaccard,
    _slugify,
    _tokenize,
)
from agent.skills_mgmt.models import Skill, SkillStatus, SkillCategory, ContentType
from agent.skills_mgmt.store import SkillStore


class TestMemoryEntry(unittest.TestCase):
    """MemoryEntry 数据结构"""

    def test_default_fields(self):
        entry = MemoryEntry(source="test", source_id="t1", task_text="hello")
        self.assertTrue(entry.success)
        self.assertEqual(entry.tool_calls, [])
        self.assertEqual(entry.params, {})
        self.assertEqual(entry.tags, [])

    def test_with_all_fields(self):
        entry = MemoryEntry(
            source="workflow",
            source_id="wf-1",
            task_text="分析代码",
            success=True,
            tool_calls=[{"name": "grep", "params": {"pattern": "TODO"}}],
            params={"language": "python"},
            tags=["code-review"],
            timestamp="2026-07-04T10:00:00",
            session_id="sess-1",
        )
        self.assertEqual(entry.tool_calls[0]["name"], "grep")
        self.assertEqual(entry.params["language"], "python")


class TestTokenizerAndJaccard(unittest.TestCase):
    """分词与 Jaccard 相似度"""

    def test_tokenize_english(self):
        tokens = _tokenize("Hello World test123")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)
        self.assertIn("test123", tokens)

    def test_tokenize_chinese(self):
        tokens = _tokenize("代码分析工具")
        self.assertIn("代", tokens)
        self.assertIn("码", tokens)
        self.assertIn("分", tokens)

    def test_tokenize_empty(self):
        self.assertEqual(_tokenize(""), set())

    def test_jaccard_identical(self):
        s = {"a", "b", "c"}
        self.assertEqual(_jaccard(s, s), 1.0)

    def test_jaccard_disjoint(self):
        self.assertEqual(_jaccard({"a"}, {"b"}), 0.0)

    def test_jaccard_partial(self):
        # |A ∩ B| = 1, |A ∪ B| = 3
        self.assertAlmostEqual(_jaccard({"a", "b"}, {"a", "c"}), 1/3)

    def test_jaccard_empty(self):
        self.assertEqual(_jaccard(set(), {"a"}), 0.0)
        self.assertEqual(_jaccard({"a"}, set()), 0.0)


class TestSlugify(unittest.TestCase):
    """slug 生成"""

    def test_basic(self):
        self.assertEqual(_slugify("Hello World"), "hello-world")

    def test_special_chars(self):
        self.assertEqual(_slugify("Analyze Code!@#"), "analyze-code")

    def test_chinese(self):
        # 中文字符会被替换为 - (非字母数字), strip 后为空 → 返回默认 "skill"
        result = _slugify("分析 代码")
        # 全中文字符 → 全部被替换为 - → strip 后为空 → 返回 "skill"
        self.assertEqual(result, "skill")

    def test_max_len(self):
        result = _slugify("a" * 50, max_len=20)
        self.assertEqual(len(result), 20)

    def test_empty(self):
        self.assertEqual(_slugify(""), "skill")
        self.assertEqual(_slugify("!!!"), "skill")


class TestClustering(unittest.TestCase):
    """聚类算法"""

    def _make_entry(self, text: str, success: bool = True,
                     tools: List[str] = None) -> MemoryEntry:
        return MemoryEntry(
            source="test",
            source_id=f"id-{abs(hash(text))}",
            task_text=text,
            success=success,
            tool_calls=[{"name": t} for t in (tools or [])],
            tags=["test-tag"] if "test" in text else [],
        )

    def test_empty_input(self):
        abstractor = MemorySkillAbstractor()
        self.assertEqual(abstractor.cluster_memories([]), [])

    def test_single_entry_one_cluster(self):
        abstractor = MemorySkillAbstractor()
        entries = [self._make_entry("hello world")]
        clusters = abstractor.cluster_memories(entries)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].size, 1)

    def test_similar_entries_merged(self):
        """相似文本应合并到一个聚类"""
        abstractor = MemorySkillAbstractor(cluster_jaccard=0.3)
        entries = [
            self._make_entry("analyze python code for bugs"),
            self._make_entry("analyze python code quality"),
            self._make_entry("analyze python code style"),
        ]
        clusters = abstractor.cluster_memories(entries)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].size, 3)

    def test_disjoint_entries_separate(self):
        """完全不相关的文本保持独立"""
        abstractor = MemorySkillAbstractor(cluster_jaccard=0.5)
        entries = [
            self._make_entry("python code analysis"),
            self._make_entry("weather forecast tokyo"),
            self._make_entry("recipe for pasta carbonara"),
        ]
        clusters = abstractor.cluster_memories(entries)
        self.assertEqual(len(clusters), 3)

    def test_cluster_id_stable(self):
        """相同输入产生相同 cluster_id"""
        abstractor = MemorySkillAbstractor()
        entries = [
            self._make_entry("test task one"),
            self._make_entry("test task two"),
            self._make_entry("test task three"),
        ]
        clusters1 = abstractor.cluster_memories(entries)
        clusters2 = abstractor.cluster_memories(entries)
        self.assertEqual(
            sorted(c.cluster_id for c in clusters1),
            sorted(c.cluster_id for c in clusters2),
        )

    def test_cluster_id_changes_with_input(self):
        """不同输入产生不同 cluster_id"""
        abstractor = MemorySkillAbstractor()
        e1 = [self._make_entry("task alpha")]
        e2 = [self._make_entry("task beta")]
        c1 = abstractor.cluster_memories(e1)[0]
        c2 = abstractor.cluster_memories(e2)[0]
        self.assertNotEqual(c1.cluster_id, c2.cluster_id)


class TestPatternExtraction(unittest.TestCase):
    """模式提取"""

    def test_common_tool_names_high_frequency(self):
        """工具调用频率 >= 50% 才纳入 common_tool_names"""
        abstractor = MemorySkillAbstractor()
        # 用相同 task_text 保证条目聚类在一起
        entries = [
            MemoryEntry(source="t", source_id="id-0", task_text="analyze code",
                        tool_calls=[{"name": "grep"}, {"name": "cat"}]),
            MemoryEntry(source="t", source_id="id-1", task_text="analyze code",
                        tool_calls=[{"name": "grep"}, {"name": "ls"}]),
            MemoryEntry(source="t", source_id="id-2", task_text="analyze code",
                        tool_calls=[{"name": "grep"}, {"name": "find"}]),
            MemoryEntry(source="t", source_id="id-3", task_text="analyze code",
                        tool_calls=[{"name": "grep"}]),
        ]
        clusters = abstractor.cluster_memories(entries)
        self.assertEqual(len(clusters), 1, "4 条相同文本应聚成 1 个聚类")
        # 4 个条目都用了 grep → 应在 common_tool_names
        # cat/ls/find 各只出现 1 次 (25%) → 不应在
        self.assertIn("grep", clusters[0].common_tool_names)
        for low_freq_tool in ("cat", "ls", "find"):
            self.assertNotIn(low_freq_tool, clusters[0].common_tool_names)

    def test_common_params_intersection(self):
        """公共参数 = 所有条目都有的键的众数值"""
        abstractor = MemorySkillAbstractor()
        # 用相同 task_text 保证聚类
        entries = [
            MemoryEntry(source="t", source_id="id-0", task_text="analyze code",
                        params={"lang": "python", "verbose": True, "extra": "x"}),
            MemoryEntry(source="t", source_id="id-1", task_text="analyze code",
                        params={"lang": "python", "verbose": False}),
            MemoryEntry(source="t", source_id="id-2", task_text="analyze code",
                        params={"lang": "python", "verbose": True}),
        ]
        clusters = abstractor.cluster_memories(entries)
        self.assertEqual(len(clusters), 1)
        # 公共键: lang, verbose (extra 只有第一条有)
        common_params = clusters[0].common_params
        self.assertIn("lang", common_params)
        self.assertIn("verbose", common_params)
        self.assertNotIn("extra", common_params)
        # lang 全部是 python
        self.assertEqual(common_params["lang"], "python")
        # verbose: True 2次, False 1次 → 众数 True
        self.assertEqual(common_params["verbose"], True)

    def test_common_tags_threshold(self):
        """标签出现 >= 2 次才纳入 common_tags"""
        abstractor = MemorySkillAbstractor()
        # 用相同 task_text 保证聚类
        entries = [
            MemoryEntry(source="t", source_id="id-0", task_text="analyze code",
                        tags=["alpha", "beta"]),
            MemoryEntry(source="t", source_id="id-1", task_text="analyze code",
                        tags=["alpha", "gamma"]),
            MemoryEntry(source="t", source_id="id-2", task_text="analyze code",
                        tags=["alpha"]),
        ]
        clusters = abstractor.cluster_memories(entries)
        self.assertEqual(len(clusters), 1)
        self.assertIn("alpha", clusters[0].common_tags)
        # beta/gamma 各 1 次, 不应纳入
        self.assertNotIn("beta", clusters[0].common_tags)
        self.assertNotIn("gamma", clusters[0].common_tags)

    def test_success_rate_calculation(self):
        abstractor = MemorySkillAbstractor()
        entries = [
            MemoryEntry(source="t", source_id="id-0", task_text="task", success=True),
            MemoryEntry(source="t", source_id="id-1", task_text="task", success=True),
            MemoryEntry(source="t", source_id="id-2", task_text="task", success=False),
            MemoryEntry(source="t", source_id="id-3", task_text="task", success=True),
        ]
        clusters = abstractor.cluster_memories(entries)
        self.assertAlmostEqual(clusters[0].success_rate, 0.75)
        self.assertEqual(clusters[0].success_count, 3)
        self.assertEqual(clusters[0].failure_count, 1)


class TestDraftGeneration(unittest.TestCase):
    """技能草稿生成"""

    def test_draft_has_required_fields(self):
        abstractor = MemorySkillAbstractor()
        cluster = MemoryCluster(
            cluster_id="abc123",
            entries=[MemoryEntry(source="t", source_id=f"id-{i}",
                                  task_text=f"task {i}")
                     for i in range(3)],
            representative_text="analyze code quality",
            common_tool_names=["grep", "cat"],
            common_params={"verbose": True},
            common_tags=["code-review"],
            success_rate=0.9,
        )
        draft = abstractor.generate_skill_draft(cluster)

        for key in ("id", "name", "description", "content",
                    "content_type", "category", "tags",
                    "default_params", "config_schema",
                    "dependencies", "source", "author", "version"):
            self.assertIn(key, draft, f"missing field: {key}")

        self.assertTrue(draft["id"].startswith("mem-"))
        self.assertEqual(draft["content_type"], "markdown")
        self.assertEqual(draft["category"], "ai_generated")
        self.assertEqual(draft["source"], "memory_abstractor")
        self.assertIn("memory-abstracted", draft["tags"])

    def test_draft_id_includes_cluster_hash(self):
        """skill_id 包含 cluster_id 哈希, 保证幂等"""
        abstractor = MemorySkillAbstractor()
        cluster = MemoryCluster(
            cluster_id="abc123def456",
            entries=[MemoryEntry(source="t", source_id="id-0",
                                  task_text="task")],
            representative_text="test task",
        )
        draft = abstractor.generate_skill_draft(cluster)
        self.assertIn("abc123", draft["id"])

    def test_draft_content_includes_pattern_info(self):
        abstractor = MemorySkillAbstractor()
        cluster = MemoryCluster(
            cluster_id="abc",
            entries=[MemoryEntry(source="t", source_id="id-0",
                                  task_text="task")],
            representative_text="analyze code",
            common_tool_names=["grep"],
            common_params={"verbose": True},
            common_tags=["review"],
            success_rate=0.9,
        )
        draft = abstractor.generate_skill_draft(cluster)
        # 内容应包含常用工具、默认参数、标签
        self.assertIn("grep", draft["content"])
        self.assertIn("verbose", draft["content"])
        self.assertIn("review", draft["content"])

    def test_draft_config_schema_inferred_from_params(self):
        """config_schema 从 default_params 推断类型"""
        abstractor = MemorySkillAbstractor()
        cluster = MemoryCluster(
            cluster_id="abc",
            entries=[MemoryEntry(source="t", source_id="id-0",
                                  task_text="task")],
            representative_text="test",
            common_params={"verbose": True, "count": 5, "name": "test"},
        )
        draft = abstractor.generate_skill_draft(cluster)
        props = draft["config_schema"]["properties"]
        self.assertEqual(props["verbose"]["type"], "boolean")
        self.assertEqual(props["count"]["type"], "number")
        self.assertEqual(props["name"]["type"], "string")


# ──────────────────────────────────────────────
# P0 结构化字段提取测试 (方法论落地)
# ──────────────────────────────────────────────

class TestStructuredExtraction(unittest.TestCase):
    """P0 结构化模式提取 — 方法论落地测试

    验证根因/触发条件/执行步骤/If-Then 规则/反例边界的提取
    """

    def _make_cluster(self, *, success=True, failure=False,
                       tools=None, params=None, tags=None,
                       task_text="analyze python code for bugs"):
        entries = []
        for i in range(5 if success else 0):
            entries.append(MemoryEntry(
                source="t", source_id=f"ok-{i}",
                task_text=task_text,
                success=True,
                tool_calls=[{"name": t} for t in (tools or ["grep", "ast"])],
                params=dict(params or {"language": "python"}),
                tags=list(tags or ["code-review", "python"]),
            ))
        for i in range(3 if failure else 0):
            entries.append(MemoryEntry(
                source="t", source_id=f"fail-{i}",
                task_text=task_text,
                success=False,
                tool_calls=[{"name": t} for t in (tools or ["grep"])],
                params=dict(params or {"language": "ruby"}),
                tags=list(tags or ["code-review"]),
            ))
        abstractor = MemorySkillAbstractor()
        clusters = abstractor.cluster_memories(entries)
        return clusters[0] if clusters else None

    def test_root_cause_all_success(self):
        """全成功: root_cause 应包含「持续有效」"""
        cluster = self._make_cluster(success=True)
        self.assertIsNotNone(cluster)
        self.assertIn("持续有效", cluster.root_cause_hypothesis)

    def test_root_cause_with_failures(self):
        """有失败: root_cause 应包含「成功率」"""
        cluster = self._make_cluster(success=True, failure=True)
        self.assertIsNotNone(cluster)
        self.assertIn("成功率", cluster.root_cause_hypothesis)

    def test_trigger_conditions_non_empty(self):
        """触发条件非空"""
        cluster = self._make_cluster(success=True)
        self.assertIsNotNone(cluster)
        self.assertTrue(len(cluster.trigger_conditions) > 0)
        # 应包含 tag 或 keyword
        triggers_text = " ".join(cluster.trigger_conditions)
        self.assertTrue("python" in triggers_text or "code-review" in triggers_text)

    def test_execution_steps_include_tools(self):
        """执行步骤包含工具调用"""
        cluster = self._make_cluster(success=True, tools=["grep", "ast"])
        self.assertIsNotNone(cluster)
        steps_text = " ".join(cluster.execution_steps)
        self.assertIn("grep", steps_text)
        self.assertIn("ast", steps_text)

    def test_if_then_rules_with_failures(self):
        """有失败时 If-Then 规则应包含边界规则"""
        cluster = self._make_cluster(success=True, failure=True)
        self.assertIsNotNone(cluster)
        self.assertTrue(len(cluster.if_then_rules) > 0)

    def test_anti_patterns_with_failures(self):
        """有失败时反例边界应非空"""
        cluster = self._make_cluster(success=True, failure=True)
        self.assertIsNotNone(cluster)
        self.assertTrue(len(cluster.anti_patterns) > 0)

    def test_anti_patterns_all_success(self):
        """全成功时反例边界也应非空 (域外场景)"""
        cluster = self._make_cluster(success=True)
        self.assertIsNotNone(cluster)
        self.assertTrue(len(cluster.anti_patterns) > 0)


class TestDraftStructuredContent(unittest.TestCase):
    """草稿内容结构化验证"""

    def test_draft_includes_root_cause_section(self):
        """草稿 markdown 包含「核心原理」section"""
        abstractor = MemorySkillAbstractor()
        cluster = MemoryCluster(
            cluster_id="abc",
            entries=[MemoryEntry(source="t", source_id="id-0", task_text="task")],
            representative_text="analyze python code",
            common_tool_names=["grep"],
            common_params={"verbose": True},
            common_tags=["review"],
            success_rate=1.0,
            root_cause_hypothesis="测试根因",
            trigger_conditions=["条件1"],
            execution_steps=["步骤1"],
            if_then_rules=["规则1"],
            anti_patterns=["反例1"],
        )
        draft = abstractor.generate_skill_draft(cluster)
        self.assertIn("## 核心原理", draft["content"])
        self.assertIn("测试根因", draft["content"])

    def test_draft_includes_trigger_section(self):
        abstractor = MemorySkillAbstractor()
        cluster = MemoryCluster(
            cluster_id="abc",
            entries=[MemoryEntry(source="t", source_id="id-0", task_text="task")],
            representative_text="test",
            trigger_conditions=["触发条件A"],
        )
        draft = abstractor.generate_skill_draft(cluster)
        self.assertIn("## 触发条件", draft["content"])
        self.assertIn("触发条件A", draft["content"])

    def test_draft_includes_checklist_section(self):
        abstractor = MemorySkillAbstractor()
        cluster = MemoryCluster(
            cluster_id="abc",
            entries=[MemoryEntry(source="t", source_id="id-0", task_text="task")],
            representative_text="test",
            execution_steps=["步骤A", "步骤B"],
        )
        draft = abstractor.generate_skill_draft(cluster)
        self.assertIn("## 执行步骤", draft["content"])
        self.assertIn("- [ ] 步骤A", draft["content"])

    def test_draft_includes_if_then_section(self):
        abstractor = MemorySkillAbstractor()
        cluster = MemoryCluster(
            cluster_id="abc",
            entries=[MemoryEntry(source="t", source_id="id-0", task_text="task")],
            representative_text="test",
            if_then_rules=["IF x THEN y"],
        )
        draft = abstractor.generate_skill_draft(cluster)
        self.assertIn("## If-Then-Else", draft["content"])

    def test_draft_includes_anti_patterns_section(self):
        abstractor = MemorySkillAbstractor()
        cluster = MemoryCluster(
            cluster_id="abc",
            entries=[MemoryEntry(source="t", source_id="id-0", task_text="task")],
            representative_text="test",
            anti_patterns=["不适用场景X"],
        )
        draft = abstractor.generate_skill_draft(cluster)
        self.assertIn("## 反例边界", draft["content"])
        self.assertIn("不适用场景X", draft["content"])

    def test_draft_dict_has_structured_fields(self):
        """草稿 dict 包含 5 个结构化字段"""
        abstractor = MemorySkillAbstractor()
        cluster = MemoryCluster(
            cluster_id="abc",
            entries=[MemoryEntry(source="t", source_id="id-0", task_text="task")],
            representative_text="test",
            root_cause_hypothesis="rc",
            trigger_conditions=["tc"],
            execution_steps=["es"],
            if_then_rules=["ir"],
            anti_patterns=["ap"],
        )
        draft = abstractor.generate_skill_draft(cluster)
        for key in ("root_cause", "triggers", "steps",
                    "if_then_rules", "anti_patterns"):
            self.assertIn(key, draft)

    def test_cluster_has_structured_fields_after_build(self):
        """_build_cluster 后 cluster 应有结构化字段"""
        abstractor = MemorySkillAbstractor()
        entries = [
            MemoryEntry(source="t", source_id=f"id-{i}",
                        task_text="analyze python code",
                        success=True, tool_calls=[{"name": "grep"}],
                        params={"lang": "python"}, tags=["python"])
            for i in range(3)
        ]
        clusters = abstractor.cluster_memories(entries)
        c = clusters[0]
        self.assertTrue(c.root_cause_hypothesis)
        self.assertTrue(len(c.trigger_conditions) > 0)
        self.assertTrue(len(c.execution_steps) > 0)


class TestComplexityGate(unittest.TestCase):
    """复杂度软警告门控 (最小可行性规则)"""

    def _make_passing_cluster(self, steps_count: int) -> MemoryCluster:
        """构造一个能通过硬门控但有指定步数复杂度的 cluster"""
        entries = [
            MemoryEntry(source="t", source_id=f"ok-{i}",
                        task_text="analyze python code quality",
                        success=True, tool_calls=[{"name": "grep"}],
                        params={"lang": "python"}, tags=["python"])
            for i in range(5)
        ]
        cluster = MemoryCluster(
            cluster_id="cl-test123",
            entries=entries,
            representative_text="analyze python code quality",
            common_tool_names=["grep"],
            common_params={"lang": "python"},
            common_tags=["python"],
            success_rate=1.0,
            execution_steps=[f"step {i}" for i in range(steps_count)],
        )
        return cluster

    def test_complexity_warning_when_steps_exceed_max(self):
        """步数 > 10 时应产生软警告, 但不阻止通过"""
        abstractor = MemorySkillAbstractor()
        cluster = self._make_passing_cluster(steps_count=15)
        # mock _find_duplicate 返回 None (不重复)
        with patch.object(abstractor, "_find_duplicate", return_value=None):
            passed, reasons, dup = abstractor.check_quality_gate(
                cluster, draft={"id": "test", "content": "test", "name": "test"},
            )
        # 应通过 (软警告不阻止)
        self.assertTrue(passed)
        # 应有 WARN
        self.assertTrue(any("[WARN]" in r and "执行步骤" in r
                            for r in reasons))

    def test_no_warning_when_within_limit(self):
        """步数 <= 10 时无警告"""
        abstractor = MemorySkillAbstractor()
        cluster = self._make_passing_cluster(steps_count=5)
        with patch.object(abstractor, "_find_duplicate", return_value=None):
            passed, reasons, dup = abstractor.check_quality_gate(
                cluster, draft={"id": "test", "content": "test", "name": "test"},
            )
        self.assertTrue(passed)
        self.assertFalse(any("[WARN]" in r for r in reasons))

    def test_warning_doesnt_block_passing(self):
        """软警告不影响 passed=True"""
        abstractor = MemorySkillAbstractor()
        cluster = self._make_passing_cluster(steps_count=20)
        with patch.object(abstractor, "_find_duplicate", return_value=None):
            passed, reasons, dup = abstractor.check_quality_gate(
                cluster, draft={"id": "test", "content": "test", "name": "test"},
            )
        self.assertTrue(passed)
        # 硬失败应为 0 (WARN 不算硬失败)
        hard_failures = [r for r in reasons if not r.startswith("[WARN]")]
        self.assertEqual(len(hard_failures), 0)


class TestEndToEnd(unittest.TestCase):
    """端到端: 记忆 → 草稿 → 注册"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="e2e_mem_")
        self.store = SkillStore(path=os.path.join(self._tmpdir, "skills.json"))
        self.svc = MagicMock()
        self.svc.list_all.return_value = []
        self.svc.create_manual.side_effect = self._create_manual
        self._created: List[Skill] = []

    def tearDown(self):
        for fn in os.listdir(self._tmpdir):
            os.remove(os.path.join(self._tmpdir, fn))
        os.rmdir(self._tmpdir)

    def _create_manual(self, data: Dict[str, Any]) -> Skill:
        skill = Skill(**data)
        self.store.upsert(skill)
        self._created.append(skill)
        return skill

    def test_end_to_end_register(self):
        """5 条相似记忆 → 1 个草稿 → 注册成技能"""
        abstractor = MemorySkillAbstractor(skills_service=self.svc)
        entries = [
            MemoryEntry(source="workflow", source_id=f"wf-{i}",
                        task_text="analyze python code quality",
                        success=True, tool_calls=[{"name": "grep"}],
                        params={"lang": "python"}, tags=["python", "review"])
            for i in range(5)
        ]
        results = abstractor.abstract_new_skills(
            memory_entries=entries, auto_register=True,
        )
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertTrue(r["quality_gate_passed"])
        self.assertTrue(r["registered"])
        self.assertIsNotNone(r["skill_id"])
        self.assertEqual(len(self._created), 1)

    def test_idempotency(self):
        """重复调用产生相同 skill_id"""
        abstractor = MemorySkillAbstractor(skills_service=self.svc)
        entries = [
            MemoryEntry(source="workflow", source_id=f"wf-{i}",
                        task_text="analyze python code quality",
                        success=True, tool_calls=[{"name": "grep"}],
                        params={"lang": "python"}, tags=["python"])
            for i in range(5)
        ]
        r1 = abstractor.abstract_new_skills(memory_entries=entries)[0]
        r2 = abstractor.abstract_new_skills(memory_entries=entries)[0]
        self.assertEqual(r1["draft_skill_id"], r2["draft_skill_id"])


if __name__ == "__main__":
    unittest.main()
