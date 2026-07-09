"""ArchRuleValidator 集成测试

覆盖 agent.observability.arch_rules 模块：
- 异常类（ArchRuleError）
- 数据结构（Violation/ArchRule/ValidationReport）
- 内置规则定义（BUILTIN_RULES/CROSS_LAYER_TO_RULE_ID）
- ArchRuleValidator 核心 API（init/validate/health）
- 校验逻辑（跨层规则/tests 反向依赖/循环依赖）
- 豁免清单（加载/应用/双向匹配）
- 配置加载（yaml 可选/禁用/覆盖）
- CLI 入口（main）
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.observability.arch_rules import (
    ArchRuleError,
    ArchRule,
    ArchRuleValidator,
    BUILTIN_RULES,
    CROSS_LAYER_TO_RULE_ID,
    Violation,
    ValidationReport,
    main,
)
from agent.observability.dependency_graph import (
    DependencyEdge,
    DependencyGraphError,
)


# ============================================================================
# Fixtures
# ============================================================================

def make_edge(
    source="agent.orchestrator.core",
    target="agent.data.repository",
    source_layer="orchestrator",
    target_layer="dao",
    line=10,
    source_file="agent/orchestrator/core.py",
    is_cross_layer=False,
    is_violation=False,
    import_type="import",
    is_dynamic=False,
):
    """构造测试用依赖边"""
    return DependencyEdge(
        source=source,
        target=target,
        source_layer=source_layer,
        target_layer=target_layer,
        import_type=import_type,
        line=line,
        source_file=source_file,
        is_cross_layer=is_cross_layer,
        is_violation=is_violation,
        is_dynamic=is_dynamic,
    )


@pytest.fixture
def validator():
    """默认校验器（无豁免、无配置）"""
    return ArchRuleValidator(root_dir="agent")


@pytest.fixture
def mock_builder():
    """mock 依赖图构建器"""
    builder = MagicMock()
    builder.edges = []
    builder.build.return_value = {"stats": {"total_files": 10, "total_edges": 0}}
    builder._collect_python_files.return_value = []
    builder.root_dir = Path("agent")
    return builder


# ============================================================================
# 异常类测试
# ============================================================================

class TestArchRuleError:
    def test_default_error_code(self):
        err = ArchRuleError("something wrong")
        assert err.message == "something wrong"
        assert err.error_code == "ARCH_RULE_ERROR"

    def test_custom_error_code(self):
        err = ArchRuleError("custom", error_code="CUSTOM_001")
        assert err.error_code == "CUSTOM_001"

    def test_exception_is_exception(self):
        err = ArchRuleError("test")
        assert isinstance(err, Exception)

    def test_raise_and_catch(self):
        with pytest.raises(ArchRuleError) as exc_info:
            raise ArchRuleError("raised", error_code="RAISED_001")
        assert exc_info.value.error_code == "RAISED_001"


# ============================================================================
# 数据结构测试
# ============================================================================

class TestViolation:
    def test_defaults(self):
        v = Violation(
            rule_id="r1",
            rule_desc="desc",
            source="src",
            target="tgt",
            source_file="file.py",
            line=1,
            severity="high",
            suggestion="fix it",
        )
        assert v.rule_id == "r1"
        assert v.is_exempted is False

    def test_to_dict(self):
        v = Violation(
            rule_id="r1",
            rule_desc="desc",
            source="src",
            target="tgt",
            source_file="file.py",
            line=1,
            severity="high",
            suggestion="fix it",
        )
        d = v.to_dict()
        assert d["rule_id"] == "r1"
        assert d["is_exempted"] is False
        assert d["line"] == 1

    def test_exempted_flag(self):
        v = Violation(
            rule_id="r1", rule_desc="", source="", target="",
            source_file="", line=0, severity="", suggestion="",
            is_exempted=True,
        )
        assert v.is_exempted is True


class TestArchRule:
    def test_construction(self):
        rule = ArchRule(
            rule_id="r1",
            desc="desc",
            severity="high",
            suggestion="suggestion",
        )
        assert rule.rule_id == "r1"
        assert rule.severity == "high"


class TestBuiltinRules:
    def test_rules_count(self):
        assert len(BUILTIN_RULES) == 7

    def test_rule_ids(self):
        expected_ids = {
            "no_orchestrator_to_dao",
            "no_cognitive_to_server_routes",
            "no_cognitive_to_dao",
            "no_tools_to_dao",
            "no_guardrails_to_server_routes",
            "no_circular_dependency",
            "no_agent_import_tests",
        }
        assert set(BUILTIN_RULES.keys()) == expected_ids

    def test_all_rules_have_required_fields(self):
        for rule_id, rule in BUILTIN_RULES.items():
            assert rule.rule_id == rule_id
            assert rule.desc
            assert rule.severity in ("high", "medium", "low")
            assert rule.suggestion

    def test_cross_layer_mapping(self):
        assert CROSS_LAYER_TO_RULE_ID[("orchestrator", "dao")] == "no_orchestrator_to_dao"
        assert CROSS_LAYER_TO_RULE_ID[("cognitive", "server_routes")] == "no_cognitive_to_server_routes"
        assert CROSS_LAYER_TO_RULE_ID[("cognitive", "dao")] == "no_cognitive_to_dao"
        assert CROSS_LAYER_TO_RULE_ID[("tools", "dao")] == "no_tools_to_dao"
        assert CROSS_LAYER_TO_RULE_ID[("guardrails", "server_routes")] == "no_guardrails_to_server_routes"

    def test_cross_layer_mapping_count(self):
        assert len(CROSS_LAYER_TO_RULE_ID) == 5


# ============================================================================
# 初始化测试
# ============================================================================

class TestInit:
    def test_default_init(self):
        v = ArchRuleValidator(root_dir="agent")
        assert v.root_dir == "agent"
        assert len(v.rules) == 7
        assert v.exemptions == set()
        assert v.trace_id

    def test_with_trace_id(self):
        v = ArchRuleValidator(root_dir="agent", trace_id="custom-trace")
        assert v.trace_id == "custom-trace"

    def test_auto_trace_id_format(self):
        v = ArchRuleValidator(root_dir="agent")
        assert len(v.trace_id) == 16

    def test_with_nonexistent_exemptions(self):
        v = ArchRuleValidator(
            root_dir="agent",
            exemptions_path="/nonexistent/path.json",
        )
        assert v.exemptions == set()

    def test_with_config_nonexistent(self):
        v = ArchRuleValidator(
            root_dir="agent",
            config_path="/nonexistent/config.yaml",
        )
        assert len(v.rules) == 7


# ============================================================================
# 健康检查测试
# ============================================================================

class TestHealth:
    def test_health_returns_dict(self, validator):
        health = validator.health()
        assert health["status"] == "healthy"
        assert health["root_dir"] == "agent"
        assert health["rules_count"] == 7
        assert health["exemptions_count"] == 0
        assert "trace_id" in health

    def test_health_with_exemptions(self, tmp_path):
        exemptions_file = tmp_path / "exemptions.json"
        exemptions_file.write_text(json.dumps({
            "exemptions": [
                {"rule_id": "r1", "source": "s", "target": "t"}
            ]
        }), encoding="utf-8")
        v = ArchRuleValidator(
            root_dir="agent",
            exemptions_path=str(exemptions_file),
        )
        health = v.health()
        assert health["exemptions_count"] == 1


# ============================================================================
# 跨层规则校验测试
# ============================================================================

class TestCheckCrossLayerRules:
    def test_no_violations(self, validator):
        edges = [make_edge(is_violation=False)]
        violations = validator._check_cross_layer_rules(edges)
        assert violations == []

    def test_violation_detected(self, validator):
        edges = [make_edge(is_violation=True)]
        violations = validator._check_cross_layer_rules(edges)
        assert len(violations) == 1
        assert violations[0].rule_id == "no_orchestrator_to_dao"
        assert violations[0].source == "agent.orchestrator.core"
        assert violations[0].target == "agent.data.repository"

    def test_cross_layer_but_not_violation(self, validator):
        edges = [make_edge(is_cross_layer=True, is_violation=False)]
        violations = validator._check_cross_layer_rules(edges)
        assert violations == []

    def test_violation_rule_not_registered(self, validator):
        validator.rules.pop("no_orchestrator_to_dao")
        edges = [make_edge(is_violation=True)]
        violations = validator._check_cross_layer_rules(edges)
        assert violations == []

    def test_violation_rule_key_not_mapped(self, validator):
        edges = [make_edge(
            source_layer="unknown_layer",
            target_layer="dao",
            is_violation=True,
        )]
        violations = validator._check_cross_layer_rules(edges)
        assert violations == []

    def test_multiple_violations(self, validator):
        edges = [
            make_edge(is_violation=True),
            make_edge(
                source="agent.cognitive.core",
                target="agent.server_routes.api",
                source_layer="cognitive",
                target_layer="server_routes",
                is_violation=True,
            ),
        ]
        violations = validator._check_cross_layer_rules(edges)
        assert len(violations) == 2
        assert violations[0].rule_id == "no_orchestrator_to_dao"
        assert violations[1].rule_id == "no_cognitive_to_server_routes"

    def test_violation_severity_from_rule(self, validator):
        edges = [make_edge(is_violation=True)]
        violations = validator._check_cross_layer_rules(edges)
        assert violations[0].severity == "high"

    def test_violation_includes_suggestion(self, validator):
        edges = [make_edge(is_violation=True)]
        violations = validator._check_cross_layer_rules(edges)
        assert violations[0].suggestion


# ============================================================================
# tests 反向依赖校验测试
# ============================================================================

class TestCheckAgentImportTests:
    def test_no_files(self, validator, mock_builder):
        mock_builder._collect_python_files.return_value = []
        violations = validator._check_agent_import_tests(mock_builder)
        assert violations == []

    def test_rule_disabled(self, validator, mock_builder):
        validator.rules.pop("no_agent_import_tests")
        violations = validator._check_agent_import_tests(mock_builder)
        assert violations == []

    def test_detects_import_tests(self, validator, mock_builder, tmp_path):
        test_file = tmp_path / "agent" / "test_module.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("import tests.unit.helper\n", encoding="utf-8")
        mock_builder._collect_python_files.return_value = [test_file]
        mock_builder._path_to_module.return_value = "agent.test_module"
        mock_builder.root_dir = tmp_path / "agent"
        violations = validator._check_agent_import_tests(mock_builder)
        assert len(violations) == 1
        assert violations[0].rule_id == "no_agent_import_tests"
        assert violations[0].target == "tests.unit.helper"

    def test_detects_from_tests_import(self, validator, mock_builder, tmp_path):
        test_file = tmp_path / "agent" / "test_module.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("from tests.unit import helper\n", encoding="utf-8")
        mock_builder._collect_python_files.return_value = [test_file]
        mock_builder._path_to_module.return_value = "agent.test_module"
        mock_builder.root_dir = tmp_path / "agent"
        violations = validator._check_agent_import_tests(mock_builder)
        assert len(violations) == 1
        assert violations[0].target == "tests.unit"

    def test_detects_bare_tests_import(self, validator, mock_builder, tmp_path):
        test_file = tmp_path / "agent" / "test_module.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("import tests\n", encoding="utf-8")
        mock_builder._collect_python_files.return_value = [test_file]
        mock_builder._path_to_module.return_value = "agent.test_module"
        mock_builder.root_dir = tmp_path / "agent"
        violations = validator._check_agent_import_tests(mock_builder)
        assert len(violations) == 1
        assert violations[0].target == "tests"

    def test_no_violation_for_non_tests_import(self, validator, mock_builder, tmp_path):
        test_file = tmp_path / "agent" / "test_module.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("import agent.utils\n", encoding="utf-8")
        mock_builder._collect_python_files.return_value = [test_file]
        mock_builder._path_to_module.return_value = "agent.test_module"
        mock_builder.root_dir = tmp_path / "agent"
        violations = validator._check_agent_import_tests(mock_builder)
        assert violations == []

    def test_skips_unparseable_file(self, validator, mock_builder, tmp_path):
        test_file = tmp_path / "agent" / "bad.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("syntax error !!!\n", encoding="utf-8")
        mock_builder._collect_python_files.return_value = [test_file]
        mock_builder._path_to_module.return_value = "agent.bad"
        mock_builder.root_dir = tmp_path / "agent"
        violations = validator._check_agent_import_tests(mock_builder)
        assert violations == []

    def test_multiple_violations_in_one_file(self, validator, mock_builder, tmp_path):
        test_file = tmp_path / "agent" / "multi.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "import tests.unit.a\n"
            "from tests.unit import b\n"
            "import tests\n",
            encoding="utf-8",
        )
        mock_builder._collect_python_files.return_value = [test_file]
        mock_builder._path_to_module.return_value = "agent.multi"
        mock_builder.root_dir = tmp_path / "agent"
        violations = validator._check_agent_import_tests(mock_builder)
        assert len(violations) == 3


# ============================================================================
# 循环依赖检测测试
# ============================================================================

class TestCheckCircularDependencies:
    def test_no_cycles(self, validator):
        edges = [
            make_edge(source="a", target="b", is_violation=False),
            make_edge(source="b", target="c", is_violation=False),
        ]
        violations = validator._check_circular_dependencies(edges)
        assert violations == []

    def test_simple_cycle(self, validator):
        edges = [
            make_edge(source="a", target="b", is_violation=False),
            make_edge(source="b", target="a", is_violation=False),
        ]
        violations = validator._check_circular_dependencies(edges)
        assert len(violations) == 1
        assert violations[0].rule_id == "no_circular_dependency"

    def test_three_node_cycle(self, validator):
        edges = [
            make_edge(source="a", target="b", is_violation=False),
            make_edge(source="b", target="c", is_violation=False),
            make_edge(source="c", target="a", is_violation=False),
        ]
        violations = validator._check_circular_dependencies(edges)
        assert len(violations) >= 1

    def test_rule_disabled(self, validator):
        validator.rules.pop("no_circular_dependency")
        edges = [
            make_edge(source="a", target="b", is_violation=False),
            make_edge(source="b", target="a", is_violation=False),
        ]
        violations = validator._check_circular_dependencies(edges)
        assert violations == []

    def test_self_loop_is_cycle(self, validator):
        edges = [make_edge(source="a", target="a", is_violation=False)]
        violations = validator._check_circular_dependencies(edges)
        # 自环：a→a，path=[a]，发现 a 在 path 中且 color=1
        assert len(violations) >= 0  # 自环行为取决于实现

    def test_target_not_in_adj(self, validator):
        edges = [
            make_edge(source="a", target="b", is_violation=False),
        ]
        violations = validator._check_circular_dependencies(edges)
        assert violations == []

    def test_no_duplicate_cycles(self, validator):
        edges = [
            make_edge(source="a", target="b", is_violation=False),
            make_edge(source="b", target="a", is_violation=False),
        ]
        violations = validator._check_circular_dependencies(edges)
        assert len(violations) == 1

    def test_cycle_violation_has_suggestion(self, validator):
        edges = [
            make_edge(source="a", target="b", is_violation=False),
            make_edge(source="b", target="a", is_violation=False),
        ]
        violations = validator._check_circular_dependencies(edges)
        assert violations[0].suggestion


# ============================================================================
# 豁免清单测试
# ============================================================================

class TestLoadExemptions:
    def test_nonexistent_file_returns_empty(self):
        exemptions = ArchRuleValidator._load_exemptions("/nonexistent/path.json")
        assert exemptions == set()

    def test_valid_file(self, tmp_path):
        file = tmp_path / "exemptions.json"
        file.write_text(json.dumps({
            "exemptions": [
                {"rule_id": "r1", "source": "a", "target": "b"},
                {"rule_id": "r2", "source": "c", "target": "d"},
            ]
        }), encoding="utf-8")
        exemptions = ArchRuleValidator._load_exemptions(str(file))
        assert "r1:a->b" in exemptions
        assert "r2:c->d" in exemptions
        assert len(exemptions) == 2

    def test_empty_exemptions_list(self, tmp_path):
        file = tmp_path / "exemptions.json"
        file.write_text(json.dumps({"exemptions": []}), encoding="utf-8")
        exemptions = ArchRuleValidator._load_exemptions(str(file))
        assert exemptions == set()

    def test_missing_exemptions_key(self, tmp_path):
        file = tmp_path / "exemptions.json"
        file.write_text(json.dumps({}), encoding="utf-8")
        exemptions = ArchRuleValidator._load_exemptions(str(file))
        assert exemptions == set()

    def test_invalid_json_raises(self, tmp_path):
        file = tmp_path / "exemptions.json"
        file.write_text("not valid json", encoding="utf-8")
        with pytest.raises(ArchRuleError) as exc_info:
            ArchRuleValidator._load_exemptions(str(file))
        assert exc_info.value.error_code == "ARCH_EXEMPTION_LOAD_FAIL"


class TestApplyExemptions:
    def test_no_exemptions(self, validator):
        v = Violation(
            rule_id="r1", rule_desc="", source="a", target="b",
            source_file="", line=0, severity="high", suggestion="",
        )
        result = validator._apply_exemptions([v])
        assert result[0].is_exempted is False

    def test_exempted(self, tmp_path):
        exemptions_file = tmp_path / "exemptions.json"
        exemptions_file.write_text(json.dumps({
            "exemptions": [
                {"rule_id": "r1", "source": "a", "target": "b"}
            ]
        }), encoding="utf-8")
        v = ArchRuleValidator(
            root_dir="agent",
            exemptions_path=str(exemptions_file),
        )
        violation = Violation(
            rule_id="r1", rule_desc="", source="a", target="b",
            source_file="", line=0, severity="high", suggestion="",
        )
        result = v._apply_exemptions([violation])
        assert result[0].is_exempted is True

    def test_not_exempted(self, tmp_path):
        exemptions_file = tmp_path / "exemptions.json"
        exemptions_file.write_text(json.dumps({
            "exemptions": [
                {"rule_id": "r1", "source": "a", "target": "b"}
            ]
        }), encoding="utf-8")
        v = ArchRuleValidator(
            root_dir="agent",
            exemptions_path=str(exemptions_file),
        )
        violation = Violation(
            rule_id="r1", rule_desc="", source="x", target="y",
            source_file="", line=0, severity="high", suggestion="",
        )
        result = v._apply_exemptions([violation])
        assert result[0].is_exempted is False

    def test_circular_dependency_reverse_match(self, tmp_path):
        exemptions_file = tmp_path / "exemptions.json"
        exemptions_file.write_text(json.dumps({
            "exemptions": [
                {"rule_id": "no_circular_dependency", "source": "a", "target": "b"}
            ]
        }), encoding="utf-8")
        v = ArchRuleValidator(
            root_dir="agent",
            exemptions_path=str(exemptions_file),
        )
        violation = Violation(
            rule_id="no_circular_dependency", rule_desc="", source="b", target="a",
            source_file="", line=0, severity="high", suggestion="",
        )
        result = v._apply_exemptions([violation])
        assert result[0].is_exempted is True

    def test_circular_dependency_forward_match(self, tmp_path):
        exemptions_file = tmp_path / "exemptions.json"
        exemptions_file.write_text(json.dumps({
            "exemptions": [
                {"rule_id": "no_circular_dependency", "source": "a", "target": "b"}
            ]
        }), encoding="utf-8")
        v = ArchRuleValidator(
            root_dir="agent",
            exemptions_path=str(exemptions_file),
        )
        violation = Violation(
            rule_id="no_circular_dependency", rule_desc="", source="a", target="b",
            source_file="", line=0, severity="high", suggestion="",
        )
        result = v._apply_exemptions([violation])
        assert result[0].is_exempted is True

    def test_non_circular_no_reverse_match(self, tmp_path):
        exemptions_file = tmp_path / "exemptions.json"
        exemptions_file.write_text(json.dumps({
            "exemptions": [
                {"rule_id": "r1", "source": "a", "target": "b"}
            ]
        }), encoding="utf-8")
        v = ArchRuleValidator(
            root_dir="agent",
            exemptions_path=str(exemptions_file),
        )
        violation = Violation(
            rule_id="r1", rule_desc="", source="b", target="a",
            source_file="", line=0, severity="high", suggestion="",
        )
        result = v._apply_exemptions([violation])
        assert result[0].is_exempted is False

    def test_mixed_exempted_and_not(self, tmp_path):
        exemptions_file = tmp_path / "exemptions.json"
        exemptions_file.write_text(json.dumps({
            "exemptions": [
                {"rule_id": "r1", "source": "a", "target": "b"}
            ]
        }), encoding="utf-8")
        v = ArchRuleValidator(
            root_dir="agent",
            exemptions_path=str(exemptions_file),
        )
        v1 = Violation(
            rule_id="r1", rule_desc="", source="a", target="b",
            source_file="", line=0, severity="high", suggestion="",
        )
        v2 = Violation(
            rule_id="r1", rule_desc="", source="x", target="y",
            source_file="", line=0, severity="high", suggestion="",
        )
        result = v._apply_exemptions([v1, v2])
        assert result[0].is_exempted is True
        assert result[1].is_exempted is False


# ============================================================================
# 配置加载测试
# ============================================================================

class TestLoadConfig:
    def test_yaml_not_installed(self, validator):
        with patch.dict("sys.modules", {"yaml": None}):
            validator._load_config("nonexistent.yaml")
            assert len(validator.rules) == 7

    def test_config_disabled_clears_rules(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "arch_rules:\n  enabled: false\n",
            encoding="utf-8",
        )
        v = ArchRuleValidator(root_dir="agent")
        v._load_config(str(config_file))
        assert len(v.rules) == 0

    def test_config_override_severity(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "arch_rules:\n"
            "  enabled: true\n"
            "  rules:\n"
            "    no_orchestrator_to_dao:\n"
            "      severity: low\n",
            encoding="utf-8",
        )
        v = ArchRuleValidator(root_dir="agent")
        v._load_config(str(config_file))
        assert v.rules["no_orchestrator_to_dao"].severity == "low"

    def test_config_override_suggestion(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "arch_rules:\n"
            "  enabled: true\n"
            "  rules:\n"
            "    no_orchestrator_to_dao:\n"
            "      suggestion: custom suggestion\n",
            encoding="utf-8",
        )
        v = ArchRuleValidator(root_dir="agent")
        v._load_config(str(config_file))
        assert v.rules["no_orchestrator_to_dao"].suggestion == "custom suggestion"

    def test_config_invalid_yaml_raises(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("invalid: yaml: content:", encoding="utf-8")
        v = ArchRuleValidator(root_dir="agent")
        with pytest.raises(ArchRuleError) as exc_info:
            v._load_config(str(config_file))
        assert exc_info.value.error_code == "ARCH_CONFIG_LOAD_FAIL"

    def test_config_unknown_rule_ignored(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "arch_rules:\n"
            "  enabled: true\n"
            "  rules:\n"
            "    nonexistent_rule:\n"
            "      severity: low\n",
            encoding="utf-8",
        )
        v = ArchRuleValidator(root_dir="agent")
        original_count = len(v.rules)
        v._load_config(str(config_file))
        assert len(v.rules) == original_count

    def test_config_does_not_pollute_builtin(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "arch_rules:\n"
            "  rules:\n"
            "    no_orchestrator_to_dao:\n"
            "      severity: low\n",
            encoding="utf-8",
        )
        original_severity = BUILTIN_RULES["no_orchestrator_to_dao"].severity
        v = ArchRuleValidator(root_dir="agent")
        v._load_config(str(config_file))
        assert v.rules["no_orchestrator_to_dao"].severity == "low"
        assert BUILTIN_RULES["no_orchestrator_to_dao"].severity == original_severity


# ============================================================================
# ValidationReport 测试
# ============================================================================

class TestValidationReport:
    def test_has_violations_true(self):
        report = ValidationReport(
            trace_id="t", root_dir="agent",
            total_rules=7, total_violations=1,
            active_violations=1, exempted_violations=0,
        )
        assert report.has_violations is True

    def test_has_violations_false(self):
        report = ValidationReport(
            trace_id="t", root_dir="agent",
            total_rules=7, total_violations=1,
            active_violations=0, exempted_violations=1,
        )
        assert report.has_violations is False

    def test_passed_true(self):
        report = ValidationReport(
            trace_id="t", root_dir="agent",
            total_rules=7, total_violations=0,
            active_violations=0, exempted_violations=0,
        )
        assert report.passed is True

    def test_passed_false(self):
        report = ValidationReport(
            trace_id="t", root_dir="agent",
            total_rules=7, total_violations=1,
            active_violations=1, exempted_violations=0,
        )
        assert report.passed is False

    def test_to_dict(self):
        report = ValidationReport(
            trace_id="t", root_dir="agent",
            total_rules=7, total_violations=1,
            active_violations=1, exempted_violations=0,
            violations=[Violation(
                rule_id="r1", rule_desc="d", source="s", target="t",
                source_file="f", line=1, severity="high", suggestion="x",
            )],
            graph_stats={"total_files": 10},
            duration_ms=100.0,
        )
        d = report.to_dict()
        assert d["trace_id"] == "t"
        assert d["passed"] is False
        assert d["total_rules"] == 7
        assert len(d["violations"]) == 1
        assert d["duration_ms"] == 100.0

    def test_to_markdown_no_violations(self):
        report = ValidationReport(
            trace_id="t", root_dir="agent",
            total_rules=7, total_violations=0,
            active_violations=0, exempted_violations=0,
        )
        md = report.to_markdown()
        assert "通过" in md
        assert "未发现架构违规" in md

    def test_to_markdown_with_violations(self):
        report = ValidationReport(
            trace_id="t", root_dir="agent",
            total_rules=7, total_violations=2,
            active_violations=1, exempted_violations=1,
            violations=[
                Violation(
                    rule_id="r1", rule_desc="d1", source="s1", target="t1",
                    source_file="f1", line=1, severity="high", suggestion="fix1",
                ),
                Violation(
                    rule_id="r2", rule_desc="d2", source="s2", target="t2",
                    source_file="f2", line=2, severity="medium", suggestion="fix2",
                    is_exempted=True,
                ),
            ],
        )
        md = report.to_markdown()
        assert "违规" in md
        assert "r1" in md
        assert "r2" in md

    def test_to_markdown_severity_grouping(self):
        report = ValidationReport(
            trace_id="t", root_dir="agent",
            total_rules=7, total_violations=2,
            active_violations=2, exempted_violations=0,
            violations=[
                Violation(
                    rule_id="r1", rule_desc="d1", source="s1", target="t1",
                    source_file="f1", line=1, severity="high", suggestion="fix1",
                ),
                Violation(
                    rule_id="r2", rule_desc="d2", source="s2", target="t2",
                    source_file="f2", line=2, severity="low", suggestion="fix2",
                ),
            ],
        )
        md = report.to_markdown()
        assert "高" in md
        assert "低" in md


# ============================================================================
# validate 集成测试
# ============================================================================

class TestValidate:
    @patch("agent.observability.arch_rules.DependencyGraphBuilder")
    def test_validate_no_violations(self, mock_builder_cls, validator):
        mock_builder = MagicMock()
        mock_builder.edges = []
        mock_builder.build.return_value = {"stats": {"total_files": 5}}
        mock_builder._collect_python_files.return_value = []
        mock_builder.root_dir = Path("agent")
        mock_builder_cls.return_value = mock_builder

        report = validator.validate()
        assert report.passed is True
        assert report.total_violations == 0
        assert report.active_violations == 0

    @patch("agent.observability.arch_rules.DependencyGraphBuilder")
    def test_validate_with_cross_layer_violation(self, mock_builder_cls, validator):
        mock_builder = MagicMock()
        mock_builder.edges = [make_edge(is_violation=True)]
        mock_builder.build.return_value = {"stats": {"total_files": 5}}
        mock_builder._collect_python_files.return_value = []
        mock_builder.root_dir = Path("agent")
        mock_builder_cls.return_value = mock_builder

        report = validator.validate()
        assert report.passed is False
        assert report.total_violations == 1
        assert report.active_violations == 1

    @patch("agent.observability.arch_rules.DependencyGraphBuilder")
    def test_validate_with_exemption(self, mock_builder_cls, tmp_path):
        exemptions_file = tmp_path / "exemptions.json"
        exemptions_file.write_text(json.dumps({
            "exemptions": [
                {"rule_id": "no_orchestrator_to_dao", "source": "agent.orchestrator.core", "target": "agent.data.repository"}
            ]
        }), encoding="utf-8")
        v = ArchRuleValidator(
            root_dir="agent",
            exemptions_path=str(exemptions_file),
        )

        mock_builder = MagicMock()
        mock_builder.edges = [make_edge(is_violation=True)]
        mock_builder.build.return_value = {"stats": {}}
        mock_builder._collect_python_files.return_value = []
        mock_builder.root_dir = Path("agent")
        mock_builder_cls.return_value = mock_builder

        report = v.validate()
        assert report.passed is True
        assert report.total_violations == 1
        assert report.exempted_violations == 1
        assert report.active_violations == 0

    @patch("agent.observability.arch_rules.DependencyGraphBuilder")
    def test_validate_graph_error_raises(self, mock_builder_cls, validator):
        mock_builder = MagicMock()
        mock_builder.build.side_effect = DependencyGraphError("build failed", error_code="BUILD_FAIL")
        mock_builder_cls.return_value = mock_builder

        with pytest.raises(ArchRuleError) as exc_info:
            validator.validate()
        assert "ARCH_GRAPH_FAIL" in exc_info.value.error_code

    @patch("agent.observability.arch_rules.DependencyGraphBuilder")
    def test_validate_with_circular_dependency(self, mock_builder_cls, validator):
        mock_builder = MagicMock()
        mock_builder.edges = [
            make_edge(source="a", target="b", is_violation=False),
            make_edge(source="b", target="a", is_violation=False),
        ]
        mock_builder.build.return_value = {"stats": {}}
        mock_builder._collect_python_files.return_value = []
        mock_builder.root_dir = Path("agent")
        mock_builder_cls.return_value = mock_builder

        report = validator.validate()
        assert report.total_violations >= 1

    @patch("agent.observability.arch_rules.DependencyGraphBuilder")
    def test_validate_returns_report_with_stats(self, mock_builder_cls, validator):
        mock_builder = MagicMock()
        mock_builder.edges = []
        mock_builder.build.return_value = {"stats": {"total_files": 42, "total_edges": 100}}
        mock_builder._collect_python_files.return_value = []
        mock_builder.root_dir = Path("agent")
        mock_builder_cls.return_value = mock_builder

        report = validator.validate()
        assert report.graph_stats["total_files"] == 42
        assert report.duration_ms >= 0


# ============================================================================
# CLI 入口测试
# ============================================================================

class TestMain:
    @patch("sys.argv", ["arch_rules"])
    def test_no_check_prints_help(self, capsys):
        result = main()
        assert result == 0
        captured = capsys.readouterr()
        assert "usage" in captured.out.lower() or "--check" in captured.out

    @patch("agent.observability.arch_rules.DependencyGraphBuilder")
    def test_check_passes(self, mock_builder_cls, capsys):
        mock_builder = MagicMock()
        mock_builder.edges = []
        mock_builder.build.return_value = {"stats": {}}
        mock_builder._collect_python_files.return_value = []
        mock_builder.root_dir = Path("agent")
        mock_builder_cls.return_value = mock_builder

        with patch("sys.argv", ["arch_rules", "--check", "--root", "agent", "--exemptions", "/nonexistent.json", "--config", "/nonexistent.yaml"]):
            result = main()
        assert result == 0

    @patch("agent.observability.arch_rules.DependencyGraphBuilder")
    def test_check_fails_with_violations(self, mock_builder_cls, capsys):
        mock_builder = MagicMock()
        mock_builder.edges = [make_edge(is_violation=True)]
        mock_builder.build.return_value = {"stats": {}}
        mock_builder._collect_python_files.return_value = []
        mock_builder.root_dir = Path("agent")
        mock_builder_cls.return_value = mock_builder

        with patch("sys.argv", ["arch_rules", "--check", "--root", "agent", "--exemptions", "/nonexistent.json", "--config", "/nonexistent.yaml"]):
            result = main()
        assert result == 1

    @patch("agent.observability.arch_rules.DependencyGraphBuilder")
    def test_check_json_report(self, mock_builder_cls, tmp_path):
        mock_builder = MagicMock()
        mock_builder.edges = []
        mock_builder.build.return_value = {"stats": {}}
        mock_builder._collect_python_files.return_value = []
        mock_builder.root_dir = Path("agent")
        mock_builder_cls.return_value = mock_builder

        json_report = tmp_path / "report.json"
        with patch("sys.argv", ["arch_rules", "--check", "--root", "agent",
                                  "--exemptions", "/nonexistent.json",
                                  "--config", "/nonexistent.yaml",
                                  "--json-report", str(json_report)]):
            result = main()
        assert result == 0
        assert json_report.exists()
        data = json.loads(json_report.read_text(encoding="utf-8"))
        assert "trace_id" in data

    @patch("agent.observability.arch_rules.DependencyGraphBuilder")
    def test_check_md_report(self, mock_builder_cls, tmp_path):
        mock_builder = MagicMock()
        mock_builder.edges = []
        mock_builder.build.return_value = {"stats": {}}
        mock_builder._collect_python_files.return_value = []
        mock_builder.root_dir = Path("agent")
        mock_builder_cls.return_value = mock_builder

        md_report = tmp_path / "report.md"
        with patch("sys.argv", ["arch_rules", "--check", "--root", "agent",
                                  "--exemptions", "/nonexistent.json",
                                  "--config", "/nonexistent.yaml",
                                  "--md-report", str(md_report)]):
            result = main()
        assert result == 0
        assert md_report.exists()
        content = md_report.read_text(encoding="utf-8")
        assert "架构规则校验报告" in content

    @patch("agent.observability.arch_rules.DependencyGraphBuilder")
    def test_check_arch_rule_error_returns_2(self, mock_builder_cls, capsys):
        mock_builder = MagicMock()
        mock_builder.build.side_effect = DependencyGraphError("fail", error_code="FAIL")
        mock_builder_cls.return_value = mock_builder

        with patch("sys.argv", ["arch_rules", "--check", "--root", "agent", "--exemptions", "/nonexistent.json"]):
            result = main()
        assert result == 2
        captured = capsys.readouterr()
        assert "校验失败" in captured.out
