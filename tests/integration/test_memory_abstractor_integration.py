"""MemorySkillAbstractor 集成测试

覆盖 agent/skills_mgmt/memory_abstractor.py：
- 数据类 (MemoryEntry / MemoryCluster)
- 工具函数 (_tokenize / _jaccard / _slugify)
- 聚类算法 (cluster_memories / _build_cluster / _make_cluster_id)
- 模式提取 (_extract_root_cause / _extract_trigger_conditions / _extract_execution_steps
  / _extract_if_then_rules / _extract_anti_patterns / _extract_common_params)
- 草稿生成 (generate_skill_draft / _infer_config_schema)
- 质量门控 (check_quality_gate / _find_duplicate)
- 主流程 (abstract_new_skills / _process_cluster)
- 记忆加载 (_load_recent_memories / _load_workflow_memories / _load_feedback_memories
  / _load_long_term_memories)
- 信号评分 (_score_and_filter_signals)
- 时间工具 (_cutoff_ts / _parse_ts)
"""

import pytest
from unittest.mock import MagicMock, patch
from copy import deepcopy

from agent.skills_mgmt.memory_abstractor import (
    MemoryEntry,
    MemoryCluster,
    MemorySkillAbstractor,
    _tokenize,
    _jaccard,
    _slugify,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def abstractor():
    """MemorySkillAbstractor 实例（禁用信号评分以简化测试）"""
    return MemorySkillAbstractor(enable_signal_scoring=False)


@pytest.fixture
def abstractor_with_scoring():
    """MemorySkillAbstractor 实例（启用信号评分）"""
    return MemorySkillAbstractor(enable_signal_scoring=True)


def make_entry(task_text="test task", success=True, tool_calls=None,
               params=None, tags=None, source="feedback", source_id="1"):
    """构造 MemoryEntry 辅助函数"""
    return MemoryEntry(
        source=source,
        source_id=source_id,
        task_text=task_text,
        success=success,
        tool_calls=tool_calls or [],
        params=params or {},
        tags=tags or [],
    )


# ============================================================================
# 数据类
# ============================================================================

class TestMemoryEntry:
    def test_defaults(self):
        entry = MemoryEntry(source="test", source_id="1", task_text="hello")
        assert entry.success is True
        assert entry.tool_calls == []
        assert entry.params == {}
        assert entry.tags == []
        assert entry.timestamp == ""
        assert entry.session_id == ""
        assert entry.signal_strength == 0.0

    def test_with_values(self):
        entry = MemoryEntry(
            source="workflow", source_id="w1", task_text="task",
            success=False, tool_calls=[{"name": "tool1"}],
            params={"key": "val"}, tags=["tag1"],
            timestamp="2024-01-01", session_id="s1",
            signal_strength=0.8,
        )
        assert entry.source == "workflow"
        assert entry.success is False
        assert entry.tool_calls == [{"name": "tool1"}]
        assert entry.signal_strength == 0.8


class TestMemoryCluster:
    def test_size_property(self):
        cluster = MemoryCluster(cluster_id="c1")
        assert cluster.size == 0
        cluster.entries.append(make_entry())
        assert cluster.size == 1

    def test_success_count(self):
        cluster = MemoryCluster(
            cluster_id="c1",
            entries=[make_entry(success=True), make_entry(success=False), make_entry(success=True)],
        )
        assert cluster.success_count == 2

    def test_failure_count(self):
        cluster = MemoryCluster(
            cluster_id="c1",
            entries=[make_entry(success=True), make_entry(success=False)],
        )
        assert cluster.failure_count == 1

    def test_defaults(self):
        cluster = MemoryCluster(cluster_id="c1")
        assert cluster.entries == []
        assert cluster.representative_text == ""
        assert cluster.common_tool_names == []
        assert cluster.common_params == {}
        assert cluster.common_tags == []
        assert cluster.success_rate == 0.0
        assert cluster.root_cause_hypothesis == ""
        assert cluster.trigger_conditions == []


# ============================================================================
# 工具函数
# ============================================================================

class TestTokenize:
    def test_empty(self):
        assert _tokenize("") == set()
        assert _tokenize(None) == set()

    def test_english(self):
        tokens = _tokenize("Hello World test")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens

    def test_chinese(self):
        tokens = _tokenize("测试中文分词")
        assert "测" in tokens
        assert "试" in tokens

    def test_mixed(self):
        tokens = _tokenize("Hello 世界 test")
        assert "hello" in tokens
        assert "test" in tokens
        assert "世" in tokens
        assert "界" in tokens

    def test_numbers_and_underscores(self):
        tokens = _tokenize("var_123 test_456")
        assert "var_123" in tokens
        assert "test_456" in tokens


class TestJaccard:
    def test_empty_sets(self):
        assert _jaccard(set(), set()) == 0.0
        assert _jaccard({"a"}, set()) == 0.0
        assert _jaccard(set(), {"a"}) == 0.0

    def test_identical(self):
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint(self):
        assert _jaccard({"a"}, {"b"}) == 0.0

    def test_partial(self):
        result = _jaccard({"a", "b"}, {"b", "c"})
        assert 0.0 < result < 1.0
        assert result == 1 / 3  # 交集 1, 并集 3


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_empty(self):
        assert _slugify("") == "skill"
        assert _slugify("!!!") == "skill"

    def test_max_len(self):
        result = _slugify("a" * 50, max_len=10)
        assert len(result) <= 10

    def test_special_chars(self):
        assert _slugify("test@#$%value") == "test-value"

    def test_chinese(self):
        result = _slugify("测试 task")
        assert "task" in result


# ============================================================================
# 聚类算法
# ============================================================================

class TestClusterMemories:
    def test_empty_input(self, abstractor):
        assert abstractor.cluster_memories([]) == []

    def test_single_entry(self, abstractor):
        entries = [make_entry(task_text="unique task")]
        clusters = abstractor.cluster_memories(entries)
        assert len(clusters) == 1
        assert clusters[0].size == 1

    def test_similar_entries_merge(self, abstractor):
        """相似度高的条目合并到同一聚类"""
        entries = [
            make_entry(task_text="search for python tutorial", source_id="1"),
            make_entry(task_text="search for python tutorial guide", source_id="2"),
            make_entry(task_text="search for python tutorial help", source_id="3"),
        ]
        clusters = abstractor.cluster_memories(entries)
        assert len(clusters) == 1
        assert clusters[0].size == 3

    def test_dissimilar_entries_separate(self, abstractor):
        """不相似的条目分到不同聚类"""
        entries = [
            make_entry(task_text="cook pasta recipe italian food", source_id="1"),
            make_entry(task_text="python programming code algorithm", source_id="2"),
        ]
        clusters = abstractor.cluster_memories(entries)
        assert len(clusters) == 2

    def test_cluster_sorted_by_size(self, abstractor):
        """聚类按 size 降序"""
        entries = [
            make_entry(task_text="task alpha beta gamma delta", source_id="1"),
            make_entry(task_text="task alpha beta gamma delta", source_id="2"),
            make_entry(task_text="completely different unique words", source_id="3"),
        ]
        clusters = abstractor.cluster_memories(entries)
        assert clusters[0].size >= clusters[1].size

    def test_cluster_has_representative_text(self, abstractor):
        entries = [make_entry(task_text="a test task")]
        clusters = abstractor.cluster_memories(entries)
        assert clusters[0].representative_text == "a test task"

    def test_cluster_id_stable(self, abstractor):
        """相同输入产生相同 cluster_id"""
        entries = [make_entry(task_text="stable task", source_id="s1")]
        c1 = abstractor.cluster_memories(entries)
        c2 = abstractor.cluster_memories(entries)
        assert c1[0].cluster_id == c2[0].cluster_id


class TestBuildCluster:
    def test_build_fills_stats(self, abstractor):
        entries = [
            make_entry(task_text="test task", success=True, tool_calls=[{"name": "tool1"}],
                       params={"k": "v"}, tags=["tag1", "tag2"]),
            make_entry(task_text="test task longer", success=True, tool_calls=[{"name": "tool1"}],
                       params={"k": "v"}, tags=["tag1"]),
            make_entry(task_text="test task longest", success=False, tool_calls=[{"name": "tool1"}],
                       params={"k": "v"}, tags=["tag1"]),
        ]
        cluster = MemoryCluster(cluster_id="c1", entries=entries, representative_text="test task longest")
        result = abstractor._build_cluster(cluster)
        assert result.success_rate == 2 / 3
        assert "tool1" in result.common_tool_names
        assert result.common_params == {"k": "v"}
        assert "tag1" in result.common_tags
        assert result.avg_text_length > 0
        assert result.root_cause_hypothesis != ""
        assert len(result.trigger_conditions) > 0
        assert len(result.execution_steps) > 0

    def test_build_empty_cluster(self, abstractor):
        cluster = MemoryCluster(cluster_id="c1", entries=[])
        result = abstractor._build_cluster(cluster)
        assert result.entries == []

    def test_build_p0_fields(self, abstractor):
        """P0 结构化字段被填充"""
        entries = [
            make_entry(task_text="run tests", success=True, tool_calls=[{"name": "pytest"}]),
            make_entry(task_text="run tests", success=True, tool_calls=[{"name": "pytest"}]),
            make_entry(task_text="run tests", success=False, tool_calls=[{"name": "pytest"}]),
        ]
        cluster = MemoryCluster(cluster_id="c1", entries=entries, representative_text="run tests")
        result = abstractor._build_cluster(cluster)
        assert result.root_cause_hypothesis != ""
        assert isinstance(result.trigger_conditions, list)
        assert isinstance(result.execution_steps, list)
        assert isinstance(result.if_then_rules, list)
        assert isinstance(result.anti_patterns, list)


class TestExtractCommonParams:
    def test_no_common_keys(self):
        entries = [
            make_entry(params={"a": 1}),
            make_entry(params={"b": 2}),
        ]
        result = MemorySkillAbstractor._extract_common_params(entries)
        assert result == {}

    def test_common_key_same_value(self):
        entries = [
            make_entry(params={"a": 1, "b": 2}),
            make_entry(params={"a": 1, "c": 3}),
        ]
        result = MemorySkillAbstractor._extract_common_params(entries)
        assert result == {"a": 1}

    def test_common_key_different_values_takes_mode(self):
        entries = [
            make_entry(params={"a": 1}),
            make_entry(params={"a": 1}),
            make_entry(params={"a": 2}),
        ]
        result = MemorySkillAbstractor._extract_common_params(entries)
        assert result == {"a": 1}  # 众数

    def test_empty_entries(self):
        assert MemorySkillAbstractor._extract_common_params([]) == {}


# ============================================================================
# P0 模式提取
# ============================================================================

class TestExtractRootCause:
    def test_all_success(self):
        """全成功场景"""
        entries = [make_entry(success=True), make_entry(success=True)]
        result = MemorySkillAbstractor._extract_root_cause(
            entries, ["tool1"], {"k": "v"}, 1.0, "test task"
        )
        assert "持续有效" in result or "100%" in result

    def test_with_failures(self):
        """有失败条目"""
        entries = [
            make_entry(success=True, params={"needed": "yes"}),
            make_entry(success=False, params={}),
        ]
        result = MemorySkillAbstractor._extract_root_cause(
            entries, ["tool1"], {}, 0.5, "test task"
        )
        assert "50%" in result

    def test_success_only_params(self):
        """成功独有参数"""
        entries = [
            make_entry(success=True, params={"key1": "v1"}),
            make_entry(success=False, params={}),
        ]
        result = MemorySkillAbstractor._extract_root_cause(
            entries, [], {}, 0.5, "test"
        )
        assert "key1" in result

    def test_no_tools(self):
        entries = [make_entry(success=True)]
        result = MemorySkillAbstractor._extract_root_cause(
            entries, [], {}, 1.0, "test"
        )
        assert "无特定工具" in result


class TestExtractTriggerConditions:
    def test_from_tags(self):
        result = MemorySkillAbstractor._extract_trigger_conditions(
            ["python", "testing"], {}, "test task"
        )
        assert any("python" in c for c in result)

    def test_from_params(self):
        result = MemorySkillAbstractor._extract_trigger_conditions(
            [], {"timeout": 30}, "test"
        )
        assert any("timeout" in c for c in result)

    def test_from_text(self):
        result = MemorySkillAbstractor._extract_trigger_conditions(
            [], {}, "deploy application server"
        )
        assert len(result) > 0

    def test_max_5_conditions(self):
        result = MemorySkillAbstractor._extract_trigger_conditions(
            ["t1", "t2", "t3", "t4", "t5", "t6"],
            {"p1": 1, "p2": 2, "p3": 3},
            "some task text here"
        )
        assert len(result) <= 5


class TestExtractExecutionSteps:
    def test_with_tools(self):
        result = MemorySkillAbstractor._extract_execution_steps(
            ["tool1", "tool2"], {}
        )
        assert any("tool1" in s for s in result)
        assert any("tool2" in s for s in result)
        assert any("验证" in s for s in result)

    def test_with_params(self):
        result = MemorySkillAbstractor._extract_execution_steps(
            [], {"timeout": 30}
        )
        assert any("timeout" in s for s in result)

    def test_empty(self):
        result = MemorySkillAbstractor._extract_execution_steps([], {})
        assert len(result) == 1  # 只有验证步骤


class TestExtractIfThenRules:
    def test_from_params(self):
        result = MemorySkillAbstractor._extract_if_then_rules(
            {"timeout": 30}, [make_entry(success=True)], [], []
        )
        assert any("timeout" in r for r in result)

    def test_success_only_keys(self):
        """成功独有参数 → 警告规则"""
        success = [make_entry(success=True, params={"needed": "yes"})]
        failure = [make_entry(success=False, params={})]
        result = MemorySkillAbstractor._extract_if_then_rules(
            {}, success, failure, []
        )
        assert any("needed" in r for r in result)

    def test_failure_only_keys(self):
        """失败独有参数 → 警告规则"""
        success = [make_entry(success=True, params={})]
        failure = [make_entry(success=False, params={"bad": "yes"})]
        result = MemorySkillAbstractor._extract_if_then_rules(
            {}, success, failure, []
        )
        assert any("bad" in r for r in result)

    def test_tools_rule(self):
        result = MemorySkillAbstractor._extract_if_then_rules(
            {}, [make_entry(success=True)], [], ["tool1", "tool2"]
        )
        assert any("tool1" in r and "tool2" in r for r in result)

    def test_max_5_rules(self):
        result = MemorySkillAbstractor._extract_if_then_rules(
            {"p1": 1, "p2": 2, "p3": 3, "p4": 4, "p5": 5, "p6": 6},
            [make_entry(success=True)], [], []
        )
        assert len(result) <= 5


class TestExtractAntiPatterns:
    def test_with_failures(self):
        success = [make_entry(success=True, tool_calls=[{"name": "good_tool"}])]
        failure = [make_entry(success=False, tool_calls=[{"name": "bad_tool"}])]
        result = MemorySkillAbstractor._extract_anti_patterns(
            failure, success, "test task"
        )
        assert any("bad_tool" in p for p in result)

    def test_no_failures(self):
        """无失败条目 → 通用反例"""
        success = [make_entry(success=True)]
        result = MemorySkillAbstractor._extract_anti_patterns(
            [], success, "test task"
        )
        assert any("复杂多步骤" in p for p in result)

    def test_from_text_keywords(self):
        result = MemorySkillAbstractor._extract_anti_patterns(
            [], [], "deploy server application"
        )
        assert len(result) > 0

    def test_max_4_patterns(self):
        success = [make_entry(success=True, tool_calls=[{"name": "t1"}])]
        failure = [make_entry(success=False, tool_calls=[{"name": "t2"}], params={"bad": 1})]
        result = MemorySkillAbstractor._extract_anti_patterns(
            failure, success, "test task with keywords"
        )
        assert len(result) <= 4


# ============================================================================
# 草稿生成
# ============================================================================

class TestGenerateSkillDraft:
    def test_basic_draft(self, abstractor):
        cluster = MemoryCluster(
            cluster_id="cl-abc123",
            entries=[make_entry(task_text="test task")],
            representative_text="test task",
            common_tool_names=["tool1"],
            common_params={"k": "v"},
            common_tags=["tag1"],
            success_rate=1.0,
            root_cause_hypothesis="root cause",
            trigger_conditions=["cond1"],
            execution_steps=["step1"],
            if_then_rules=["rule1"],
            anti_patterns=["anti1"],
        )
        draft = abstractor.generate_skill_draft(cluster)
        assert draft["id"].startswith("mem-")
        assert "test task" in draft["name"]
        assert "1 条记忆" in draft["description"]
        assert "100%" in draft["description"]
        assert "## 核心原理" in draft["content"]
        assert "root cause" in draft["content"]
        assert "## 触发条件" in draft["content"]
        assert "## 执行步骤" in draft["content"]
        assert draft["content_type"] == "markdown"
        assert draft["category"] == "ai_generated"
        assert "memory-abstracted" in draft["tags"]
        assert draft["source"] == "memory_abstractor"
        assert draft["root_cause"] == "root cause"
        assert draft["triggers"] == ["cond1"]

    def test_empty_cluster_fields(self, abstractor):
        """聚类字段为空时的草稿"""
        cluster = MemoryCluster(
            cluster_id="cl-empty",
            entries=[make_entry()],
            representative_text="task",
        )
        draft = abstractor.generate_skill_draft(cluster)
        assert "未提取到根因假设" in draft["content"]
        assert "未提取到触发条件" in draft["content"]
        assert "未提取到执行步骤" in draft["content"]

    def test_skill_id_includes_cluster_hash(self, abstractor):
        cluster = MemoryCluster(
            cluster_id="cl-abcdef",
            entries=[make_entry(task_text="test")],
            representative_text="test",
        )
        draft = abstractor.generate_skill_draft(cluster)
        assert "abcdef" in draft["id"]


class TestInferConfigSchema:
    def test_empty(self):
        result = MemorySkillAbstractor._infer_config_schema({})
        assert result == {"type": "object", "properties": {}}

    def test_bool(self):
        result = MemorySkillAbstractor._infer_config_schema({"flag": True})
        assert result["properties"]["flag"]["type"] == "boolean"
        assert result["properties"]["flag"]["default"] is True

    def test_number(self):
        result = MemorySkillAbstractor._infer_config_schema({"count": 42})
        assert result["properties"]["count"]["type"] == "number"
        assert result["properties"]["count"]["default"] == 42

    def test_string(self):
        result = MemorySkillAbstractor._infer_config_schema({"name": "test"})
        assert result["properties"]["name"]["type"] == "string"
        assert result["properties"]["name"]["default"] == "test"

    def test_other_type(self):
        result = MemorySkillAbstractor._infer_config_schema({"data": [1, 2, 3]})
        assert result["properties"]["data"]["type"] == "string"


# ============================================================================
# 质量门控
# ============================================================================

class TestCheckQualityGate:
    def test_passes_all_checks(self, abstractor):
        cluster = MemoryCluster(
            cluster_id="c1",
            entries=[make_entry() for _ in range(5)],
            success_rate=0.9,
            execution_steps=["step1"],
        )
        passed, reasons, dup = abstractor.check_quality_gate(cluster)
        assert passed is True
        assert reasons == []
        assert dup == ""

    def test_fails_size(self, abstractor):
        cluster = MemoryCluster(
            cluster_id="c1",
            entries=[make_entry()],
            success_rate=1.0,
        )
        passed, reasons, _ = abstractor.check_quality_gate(cluster)
        assert passed is False
        assert any("聚类大小" in r for r in reasons)

    def test_fails_success_rate(self, abstractor):
        cluster = MemoryCluster(
            cluster_id="c1",
            entries=[make_entry() for _ in range(5)],
            success_rate=0.5,
        )
        passed, reasons, _ = abstractor.check_quality_gate(cluster)
        assert passed is False
        assert any("成功率" in r for r in reasons)

    def test_warns_complexity(self, abstractor):
        """复杂度超限 → 软警告（不阻止通过）"""
        cluster = MemoryCluster(
            cluster_id="c1",
            entries=[make_entry() for _ in range(5)],
            success_rate=1.0,
            execution_steps=["step"] * 15,
        )
        passed, reasons, _ = abstractor.check_quality_gate(cluster)
        assert passed is True  # 软警告不阻止
        assert any("[WARN]" in r for r in reasons)

    def test_duplicate_detection(self, abstractor):
        """检测到重复 → 不通过"""
        cluster = MemoryCluster(
            cluster_id="c1",
            entries=[make_entry() for _ in range(5)],
            success_rate=1.0,
        )
        # content 和 name 完全一致 → Jaccard = 1.0 >= 0.7
        draft = {"id": "draft1", "content": "search python tutorial guide", "name": "python search"}

        mock_svc = MagicMock()
        mock_skill = MagicMock()
        mock_skill.id = "existing-skill"
        mock_skill.content = "search python tutorial guide"
        mock_skill.name = "python search"
        mock_svc.list_all.return_value = [mock_skill]
        abstractor._skills_service = mock_svc

        passed, reasons, dup = abstractor.check_quality_gate(cluster, draft=draft)
        assert passed is False
        assert dup == "existing-skill"

    def test_no_duplicate(self, abstractor):
        """无重复 → 通过"""
        cluster = MemoryCluster(
            cluster_id="c1",
            entries=[make_entry() for _ in range(5)],
            success_rate=1.0,
        )
        draft = {"id": "draft1", "content": "unique content xyz", "name": "unique"}

        mock_svc = MagicMock()
        mock_svc.list_all.return_value = []
        abstractor._skills_service = mock_svc

        passed, _, dup = abstractor.check_quality_gate(cluster, draft=draft)
        assert passed is True
        assert dup == ""


class TestFindDuplicate:
    def test_finds_duplicate(self, abstractor):
        mock_svc = MagicMock()
        mock_skill = MagicMock()
        mock_skill.id = "dup-skill"
        mock_skill.content = "search for python tutorial"
        mock_skill.name = "python search"
        mock_svc.list_all.return_value = [mock_skill]
        abstractor._skills_service = mock_svc

        draft = {"content": "search for python tutorial", "name": "python search"}
        result = abstractor._find_duplicate(draft)
        assert result == "dup-skill"

    def test_no_duplicate(self, abstractor):
        mock_svc = MagicMock()
        mock_svc.list_all.return_value = []
        abstractor._skills_service = mock_svc

        draft = {"content": "unique", "name": "unique"}
        assert abstractor._find_duplicate(draft) is None

    def test_service_error_returns_none(self, abstractor):
        mock_svc = MagicMock()
        mock_svc.list_all.side_effect = RuntimeError("db error")
        abstractor._skills_service = mock_svc

        assert abstractor._find_duplicate({"content": "x", "name": "x"}) is None


# ============================================================================
# 主流程
# ============================================================================

class TestAbstractNewSkills:
    def test_insufficient_entries(self, abstractor):
        """记忆条目不足 → 返回空"""
        entries = [make_entry()]
        result = abstractor.abstract_new_skills(memory_entries=entries)
        assert result == []

    def test_basic_abstract(self, abstractor):
        """基本抽象流程"""
        entries = [
            make_entry(task_text="search python tutorial", source_id="1", success=True,
                       tool_calls=[{"name": "search"}]),
            make_entry(task_text="search python tutorial", source_id="2", success=True,
                       tool_calls=[{"name": "search"}]),
            make_entry(task_text="search python tutorial", source_id="3", success=True,
                       tool_calls=[{"name": "search"}]),
        ]
        result = abstractor.abstract_new_skills(memory_entries=entries, auto_register=False)
        assert len(result) > 0
        assert "cluster_id" in result[0]
        assert "quality_gate_passed" in result[0]
        assert "draft_skill_id" in result[0]

    def test_max_skills_limit(self, abstractor):
        """max_skills 限制"""
        entries = []
        for i in range(10):
            entries.append(make_entry(
                task_text=f"unique task {i} with distinct words",
                source_id=str(i),
            ))
        result = abstractor.abstract_new_skills(memory_entries=entries, max_skills=2)
        assert len(result) <= 2

    def test_results_sorted_by_quality(self, abstractor):
        """质量门控通过的优先"""
        entries = []
        for i in range(6):
            entries.append(make_entry(
                task_text="same task identical words",
                source_id=str(i),
                success=True if i < 5 else False,
            ))
        result = abstractor.abstract_new_skills(memory_entries=entries)
        if len(result) > 1:
            assert result[0]["quality_gate_passed"] >= result[1]["quality_gate_passed"]

    def test_auto_register_disabled(self, abstractor):
        """auto_register=False → registered=False"""
        entries = [
            make_entry(task_text="test task abc", source_id="1", success=True),
            make_entry(task_text="test task abc", source_id="2", success=True),
            make_entry(task_text="test task abc", source_id="3", success=True),
        ]
        result = abstractor.abstract_new_skills(memory_entries=entries, auto_register=False)
        assert all(not r["registered"] for r in result)

    def test_auto_register_enabled(self, abstractor):
        """auto_register=True → 尝试注册"""
        mock_svc = MagicMock()
        mock_skill = MagicMock()
        mock_skill.id = "new-skill-id"
        mock_svc.create_manual.return_value = mock_skill
        mock_svc.list_all.return_value = []
        abstractor._skills_service = mock_svc

        entries = [
            make_entry(task_text="unique task xyz abc", source_id="1", success=True),
            make_entry(task_text="unique task xyz abc", source_id="2", success=True),
            make_entry(task_text="unique task xyz abc", source_id="3", success=True),
        ]
        result = abstractor.abstract_new_skills(
            memory_entries=entries, auto_register=True
        )
        assert len(result) > 0
        if result[0]["quality_gate_passed"]:
            assert result[0]["registered"] is True
            assert result[0]["skill_id"] == "new-skill-id"

    def test_register_failure_handled(self, abstractor):
        """注册失败 → registered=False"""
        mock_svc = MagicMock()
        mock_svc.create_manual.side_effect = RuntimeError("db error")
        mock_svc.list_all.return_value = []
        abstractor._skills_service = mock_svc

        entries = [
            make_entry(task_text="test task def ghi", source_id="1", success=True),
            make_entry(task_text="test task def ghi", source_id="2", success=True),
            make_entry(task_text="test task def ghi", source_id="3", success=True),
        ]
        result = abstractor.abstract_new_skills(
            memory_entries=entries, auto_register=True
        )
        if result:
            assert result[0]["registered"] is False


class TestProcessCluster:
    def test_process_passing_cluster(self, abstractor):
        cluster = MemoryCluster(
            cluster_id="c1",
            entries=[make_entry() for _ in range(5)],
            representative_text="test task",
            success_rate=1.0,
            common_tool_names=["tool1"],
            common_params={},
            common_tags=["tag1"],
            execution_steps=["step1"],
            root_cause_hypothesis="cause",
            trigger_conditions=["cond1"],
        )
        result = abstractor._process_cluster(cluster, auto_register=False)
        assert result["cluster_id"] == "c1"
        assert result["cluster_size"] == 5
        assert result["quality_gate_passed"] is True
        assert result["registered"] is False
        assert "draft_skill_id" in result

    def test_process_with_avg_signal(self, abstractor):
        """计算 avg_signal_strength"""
        entries = [make_entry() for _ in range(3)]
        entries[0].signal_strength = 0.8
        entries[1].signal_strength = 0.6
        entries[2].signal_strength = 0.4
        cluster = MemoryCluster(
            cluster_id="c1",
            entries=entries,
            success_rate=1.0,
        )
        result = abstractor._process_cluster(cluster)
        assert abs(result["avg_signal_strength"] - 0.6) < 0.01


# ============================================================================
# 记忆加载
# ============================================================================

class TestLoadRecentMemories:
    def test_all_sources_fail(self, abstractor):
        """所有数据源失败 → 返回空"""
        with patch.object(abstractor, "_load_workflow_memories", side_effect=RuntimeError), \
             patch.object(abstractor, "_load_feedback_memories", side_effect=RuntimeError), \
             patch.object(abstractor, "_load_long_term_memories", side_effect=RuntimeError):
            result = abstractor._load_recent_memories(days=30)
        assert result == []

    def test_partial_failure(self, abstractor):
        """部分数据源失败 → 只返回成功的"""
        wf_entries = [make_entry(source="workflow")]
        with patch.object(abstractor, "_load_workflow_memories", return_value=wf_entries), \
             patch.object(abstractor, "_load_feedback_memories", side_effect=RuntimeError), \
             patch.object(abstractor, "_load_long_term_memories", return_value=[]):
            result = abstractor._load_recent_memories(days=30)
        assert len(result) == 1
        assert result[0].source == "workflow"


class TestLoadWorkflowMemories:
    def test_no_service(self, abstractor):
        """WorkflowLearningService 不可用 → 返回空"""
        with patch("builtins.__import__", side_effect=ImportError):
            result = abstractor._load_workflow_memories(days=30)
        assert result == []

    def test_loads_entries(self, abstractor):
        mock_svc = MagicMock()
        mock_svc.list_recent.return_value = [
            {"id": "w1", "task_text": "task", "success": True, "created_at": ""},
            {"id": "w2", "task_text": "task2", "success": False, "created_at": ""},
        ]
        with patch("agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._cutoff_ts", return_value=0):
            with patch.dict("sys.modules", {"agent.workflow_learning.service": MagicMock(WorkflowLearningService=lambda: mock_svc)}):
                result = abstractor._load_workflow_memories(days=30)
        assert len(result) == 2
        assert result[0].source == "workflow"


class TestLoadFeedbackMemories:
    def test_no_service(self, abstractor):
        with patch("builtins.__import__", side_effect=ImportError):
            result = abstractor._load_feedback_memories(days=30)
        assert result == []

    def test_loads_entries(self, abstractor):
        mock_collector = MagicMock()
        mock_collector.list_recent.return_value = [
            {"id": "f1", "comment": "good", "rating": 1, "timestamp": ""},
        ]
        with patch("agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._cutoff_ts", return_value=0):
            with patch.dict("sys.modules", {"agent.feedback_collector": MagicMock(FeedbackCollector=lambda: mock_collector)}):
                result = abstractor._load_feedback_memories(days=30)
        assert len(result) == 1
        assert result[0].source == "feedback"
        assert result[0].success is True


class TestLoadLongTermMemories:
    def test_no_service(self, abstractor):
        with patch("builtins.__import__", side_effect=ImportError):
            result = abstractor._load_long_term_memories(days=30)
        assert result == []

    def test_loads_entries(self, abstractor):
        mock_mgr = MagicMock()
        mock_mgr.list_recent.return_value = [
            {"id": "m1", "content": "memory", "success": True, "timestamp": ""},
        ]
        with patch("agent.skills_mgmt.memory_abstractor.MemorySkillAbstractor._cutoff_ts", return_value=0):
            with patch.dict("sys.modules", {"agent.memory_optimized": MagicMock(MemoryManager=lambda: mock_mgr)}):
                result = abstractor._load_long_term_memories(days=30)
        assert len(result) == 1
        assert result[0].source == "long_term_memory"


# ============================================================================
# 信号评分
# ============================================================================

class TestScoreAndFilterSignals:
    def test_filters_low_signal(self, abstractor_with_scoring):
        """低信号被过滤"""
        entries = [make_entry(task_text="test"), make_entry(task_text="test")]

        mock_scorer = MagicMock()
        mock_scorer.score.return_value = (0.2, {})  # 低于阈值 0.4
        mock_scorer.filter_high_value.return_value = []

        mock_svc = MagicMock()
        mock_svc.list_all.return_value = []
        abstractor_with_scoring._skills_service = mock_svc

        with patch("agent.skills_mgmt.signal_scorer.SignalScorer", return_value=mock_scorer):
            result = abstractor_with_scoring._score_and_filter_signals(entries)

        assert result == []

    def test_keeps_high_signal(self, abstractor_with_scoring):
        """高信号保留"""
        entries = [make_entry(task_text="test")]
        mock_scorer = MagicMock()
        mock_scorer.score.return_value = (0.8, {})
        mock_scorer.filter_high_value.return_value = entries

        mock_svc = MagicMock()
        mock_svc.list_all.return_value = []
        abstractor_with_scoring._skills_service = mock_svc

        with patch("agent.skills_mgmt.signal_scorer.SignalScorer", return_value=mock_scorer):
            result = abstractor_with_scoring._score_and_filter_signals(entries)

        assert len(result) == 1
        assert result[0].signal_strength == 0.8

    def test_service_error_uses_empty_skills(self, abstractor_with_scoring):
        """skills_service 失败 → existing_skills=[]"""
        entries = [make_entry()]
        mock_scorer = MagicMock()
        mock_scorer.score.return_value = (0.9, {})
        mock_scorer.filter_high_value.return_value = entries

        mock_svc = MagicMock()
        mock_svc.list_all.side_effect = RuntimeError("db error")
        abstractor_with_scoring._skills_service = mock_svc

        with patch("agent.skills_mgmt.signal_scorer.SignalScorer", return_value=mock_scorer):
            result = abstractor_with_scoring._score_and_filter_signals(entries)

        assert len(result) == 1


# ============================================================================
# 时间工具
# ============================================================================

class TestTimeUtils:
    def test_cutoff_ts(self):
        """_cutoff_ts 返回 days 天前的时间戳"""
        import time
        days = 7
        result = MemorySkillAbstractor._cutoff_ts(days)
        expected = time.time() - days * 86400
        assert abs(result - expected) < 5  # 允许 5 秒误差

    def test_parse_ts_empty(self):
        assert MemorySkillAbstractor._parse_ts("") == 0.0
        assert MemorySkillAbstractor._parse_ts(None) == 0.0

    def test_parse_ts_iso(self):
        result = MemorySkillAbstractor._parse_ts("2024-01-15T10:30:00")
        assert result > 0

    def test_parse_ts_with_tz(self):
        result = MemorySkillAbstractor._parse_ts("2024-01-15T10:30:00Z")
        assert result > 0

    def test_parse_ts_with_millis(self):
        result = MemorySkillAbstractor._parse_ts("2024-01-15T10:30:00.123456")
        assert result > 0

    def test_parse_ts_space_format(self):
        result = MemorySkillAbstractor._parse_ts("2024-01-15 10:30:00")
        assert result > 0

    def test_parse_ts_invalid(self):
        assert MemorySkillAbstractor._parse_ts("not a date") == 0.0


# ============================================================================
# 辅助方法
# ============================================================================

class TestResolveSkillsService:
    def test_with_injected_service(self, abstractor):
        mock_svc = MagicMock()
        abstractor._skills_service = mock_svc
        assert abstractor._resolve_skills_service() is mock_svc

    def test_lazy_load_success(self):
        abstractor = MemorySkillAbstractor(skills_service=None)
        mock_svc = MagicMock()
        with patch("agent.state_manager.get_skills_mgmt_service", return_value=mock_svc):
            assert abstractor._resolve_skills_service() is mock_svc

    def test_lazy_load_fallback(self):
        """state_manager 不可用 → 回退到 SkillsMgmtService"""
        abstractor = MemorySkillAbstractor(skills_service=None)
        mock_svc = MagicMock()
        # 只 patch state_manager 的 import 抛 ImportError，触发 except 分支
        with patch.dict("sys.modules", {"agent.state_manager": None}):
            with patch("agent.skills_mgmt.service.SkillsMgmtService", return_value=mock_svc):
                result = abstractor._resolve_skills_service()
        assert result is mock_svc
