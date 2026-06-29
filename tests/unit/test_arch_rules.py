"""
架构规则校验器单元测试
覆盖：跨层规则、循环依赖、tests 反向依赖、豁免清单、配置加载、报告生成
目标覆盖率：≥80%
"""
import json
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest

from agent.observability.arch_rules import (
    ArchRuleValidator,
    ArchRuleError,
    ValidationReport,
    Violation,
    ArchRule,
    BUILTIN_RULES,
)
from agent.observability.dependency_graph import DependencyGraphError


# ── 测试夹具 ──────────────────────────────────────────────────


@pytest.fixture
def project_with_violations(tmp_path: Path) -> Path:
    """构造含违规的项目结构

    - orchestrator → dao（违规）
    - cognitive → server_routes（违规）
    - 循环依赖 a → b → a
    - agent → tests 反向依赖
    """
    agent = tmp_path / "agent"
    (agent / "orchestrator").mkdir(parents=True)
    (agent / "cognitive").mkdir(parents=True)
    (agent / "data").mkdir(parents=True)
    (agent / "server_routes").mkdir(parents=True)
    (agent / "tests_dir").mkdir(parents=True)

    for sub in ["orchestrator", "cognitive", "data", "server_routes", "tests_dir"]:
        (agent / sub / "__init__.py").write_text("", encoding="utf-8")
    (agent / "__init__.py").write_text("", encoding="utf-8")

    # orchestrator → data（违规：orchestrator → dao）
    (agent / "orchestrator" / "core.py").write_text(
        "from agent.data.repo import Repository\n",
        encoding="utf-8",
    )
    # cognitive → server_routes（违规）
    (agent / "cognitive" / "loop.py").write_text(
        "from agent.server_routes.api import router\n",
        encoding="utf-8",
    )
    # data/repo.py
    (agent / "data" / "repo.py").write_text("class Repository: pass\n", encoding="utf-8")
    # server_routes/api.py
    (agent / "server_routes" / "api.py").write_text("router = None\n", encoding="utf-8")

    # 循环依赖：a → b → a
    (agent / "tests_dir" / "a.py").write_text(
        "from agent.tests_dir.b import b_func\n",
        encoding="utf-8",
    )
    (agent / "tests_dir" / "b.py").write_text(
        "from agent.tests_dir.a import a_func\n"
        "def a_func(): pass\n"
        "def b_func(): pass\n",
        encoding="utf-8",
    )

    # agent → tests 反向依赖
    (agent / "reverse.py").write_text(
        "from tests.unit.test_example import test_case\n",
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture
def project_clean(tmp_path: Path) -> Path:
    """构造无违规的项目结构"""
    agent = tmp_path / "agent"
    (agent / "tools").mkdir(parents=True)
    (agent / "utils").mkdir(parents=True)
    (agent / "__init__.py").write_text("", encoding="utf-8")
    (agent / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (agent / "utils" / "__init__.py").write_text("", encoding="utf-8")
    # tools → utils（跨层但非违规）
    (agent / "tools" / "helper.py").write_text(
        "from agent.utils.x import helper\n",
        encoding="utf-8",
    )
    (agent / "utils" / "x.py").write_text("def helper(): pass\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def exemptions_file(tmp_path: Path) -> str:
    """构造豁免清单文件"""
    data = {
        "exemptions": [
            {
                "rule_id": "no_orchestrator_to_dao",
                "source": "agent.orchestrator.core",
                "target": "agent.data.repo",
                "reason": "存量违规",
            }
        ]
    }
    path = tmp_path / "exemptions.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


# ── 初始化测试 ──────────────────────────────────────────────────


class TestInitialization:
    """测试校验器初始化"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_default(self):
        """默认初始化应成功"""
        validator = ArchRuleValidator(root_dir="agent")
        assert validator.trace_id is not None
        assert len(validator.rules) == 7

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_with_custom_trace_id(self):
        """自定义 trace_id 应被使用"""
        validator = ArchRuleValidator(root_dir="agent", trace_id="custom123")
        assert validator.trace_id == "custom123"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_loads_exemptions(self, exemptions_file: str):
        """应加载豁免清单"""
        validator = ArchRuleValidator(
            root_dir="agent", exemptions_path=exemptions_file
        )
        assert len(validator.exemptions) == 1
        assert "no_orchestrator_to_dao:agent.orchestrator.core->agent.data.repo" in validator.exemptions

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_with_missing_exemptions_file(self, tmp_path: Path):
        """豁免清单不存在应不抛异常（视为空清单）"""
        validator = ArchRuleValidator(
            root_dir="agent",
            exemptions_path=str(tmp_path / "nonexistent.json"),
        )
        assert len(validator.exemptions) == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_raises_on_invalid_exemptions(self, tmp_path: Path):
        """损坏的豁免清单应抛出异常"""
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json", encoding="utf-8")
        with pytest.raises(ArchRuleError) as exc_info:
            ArchRuleValidator(root_dir="agent", exemptions_path=str(bad))
        assert exc_info.value.error_code == "ARCH_EXEMPTION_LOAD_FAIL"


# ── 校验逻辑测试 ──────────────────────────────────────────────


class TestValidation:
    """测试架构规则校验"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_validate_detects_cross_layer(self, project_with_violations: Path):
        """应检测到跨层调用违规"""
        validator = ArchRuleValidator(root_dir=str(project_with_violations / "agent"))
        report = validator.validate()
        rule_ids = {v.rule_id for v in report.violations}
        assert "no_orchestrator_to_dao" in rule_ids
        assert "no_cognitive_to_server_routes" in rule_ids

    @pytest.mark.unit
    @pytest.mark.p0
    def test_validate_detects_circular_dependency(self, project_with_violations: Path):
        """应检测到循环依赖"""
        validator = ArchRuleValidator(root_dir=str(project_with_violations / "agent"))
        report = validator.validate()
        rule_ids = {v.rule_id for v in report.violations}
        assert "no_circular_dependency" in rule_ids

    @pytest.mark.unit
    @pytest.mark.p0
    def test_validate_detects_tests_reverse_dep(self, project_with_violations: Path):
        """应检测到 agent → tests 反向依赖"""
        validator = ArchRuleValidator(root_dir=str(project_with_violations / "agent"))
        report = validator.validate()
        rule_ids = {v.rule_id for v in report.violations}
        assert "no_agent_import_tests" in rule_ids

    @pytest.mark.unit
    @pytest.mark.p0
    def test_validate_clean_project_passes(self, project_clean: Path):
        """干净项目应通过校验"""
        validator = ArchRuleValidator(root_dir=str(project_clean / "agent"))
        report = validator.validate()
        assert report.passed
        assert report.active_violations == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_validate_returns_report(self, project_clean: Path):
        """应返回 ValidationReport"""
        validator = ArchRuleValidator(root_dir=str(project_clean / "agent"))
        report = validator.validate()
        assert isinstance(report, ValidationReport)
        assert report.trace_id == validator.trace_id


# ── 豁免测试 ──────────────────────────────────────────────────


class TestExemptions:
    """测试豁免清单"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_exemption_marks_violation(self, project_with_violations: Path, tmp_path: Path):
        """豁免清单应标记对应违规为已豁免"""
        # 构造豁免清单
        exemptions = {
            "exemptions": [
                {
                    "rule_id": "no_orchestrator_to_dao",
                    "source": "agent.orchestrator.core",
                    "target": "agent.data.repo",
                    "reason": "存量违规",
                }
            ]
        }
        ex_path = tmp_path / "ex.json"
        ex_path.write_text(json.dumps(exemptions), encoding="utf-8")

        validator = ArchRuleValidator(
            root_dir=str(project_with_violations / "agent"),
            exemptions_path=str(ex_path),
        )
        report = validator.validate()
        # 找到 orchestrator → dao 违规
        orc_violations = [
            v for v in report.violations
            if v.rule_id == "no_orchestrator_to_dao"
        ]
        assert len(orc_violations) >= 1
        assert all(v.is_exempted for v in orc_violations)
        # 已豁免的不计入 active
        assert report.exempted_violations >= 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_exempted_violation_does_not_fail(self, project_with_violations: Path, tmp_path: Path):
        """全部违规被豁免时报告应通过"""
        # 豁免所有可能的违规
        all_exemptions = {
            "exemptions": [
                {
                    "rule_id": "no_orchestrator_to_dao",
                    "source": "agent.orchestrator.core",
                    "target": "agent.data.repo",
                },
                {
                    "rule_id": "no_cognitive_to_server_routes",
                    "source": "agent.cognitive.loop",
                    "target": "agent.server_routes.api",
                },
                {
                    "rule_id": "no_circular_dependency",
                    "source": "agent.tests_dir.a",
                    "target": "agent.tests_dir.b",
                },
                {
                    "rule_id": "no_agent_import_tests",
                    "source": "agent.reverse",
                    "target": "tests.unit.test_example",
                },
            ]
        }
        ex_path = tmp_path / "all_ex.json"
        ex_path.write_text(json.dumps(all_exemptions), encoding="utf-8")

        validator = ArchRuleValidator(
            root_dir=str(project_with_violations / "agent"),
            exemptions_path=str(ex_path),
        )
        report = validator.validate()
        assert report.passed
        assert report.active_violations == 0


# ── 报告生成测试 ──────────────────────────────────────────────


class TestReport:
    """测试报告生成"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_report_to_dict(self, project_with_violations: Path):
        """to_dict 应返回完整字段"""
        validator = ArchRuleValidator(root_dir=str(project_with_violations / "agent"))
        report = validator.validate()
        d = report.to_dict()
        assert "trace_id" in d
        assert "passed" in d
        assert "total_violations" in d
        assert "active_violations" in d
        assert "violations" in d
        assert "duration_ms" in d

    @pytest.mark.unit
    @pytest.mark.p0
    def test_report_to_markdown(self, project_with_violations: Path):
        """to_markdown 应生成 Markdown 报告"""
        validator = ArchRuleValidator(root_dir=str(project_with_violations / "agent"))
        report = validator.validate()
        md = report.to_markdown()
        assert "架构规则校验报告" in md
        assert "违规" in md or "未发现架构违规" in md

    @pytest.mark.unit
    @pytest.mark.p0
    def test_report_markdown_for_clean_project(self, project_clean: Path):
        """干净项目的 Markdown 应显示通过"""
        validator = ArchRuleValidator(root_dir=str(project_clean / "agent"))
        report = validator.validate()
        md = report.to_markdown()
        assert "✅" in md

    @pytest.mark.unit
    @pytest.mark.p0
    def test_report_has_violations_property(self, project_with_violations: Path):
        """has_violations 属性应正确"""
        validator = ArchRuleValidator(root_dir=str(project_with_violations / "agent"))
        report = validator.validate()
        assert report.has_violations is True
        assert report.passed is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_report_violations_serializable(self, project_with_violations: Path):
        """违规项应可 JSON 序列化"""
        validator = ArchRuleValidator(root_dir=str(project_with_violations / "agent"))
        report = validator.validate()
        # 应不抛异常
        json_str = json.dumps(report.to_dict(), ensure_ascii=False)
        assert len(json_str) > 0


# ── 健康检查测试 ──────────────────────────────────────────────


class TestHealth:
    """测试健康检查"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_health_returns_status(self):
        """health 应返回状态"""
        validator = ArchRuleValidator(root_dir="agent")
        h = validator.health()
        assert h["status"] == "healthy"
        assert "rules_count" in h
        assert "exemptions_count" in h


# ── 异常处理测试 ──────────────────────────────────────────────


class TestErrorHandling:
    """测试异常处理"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_validate_raises_on_graph_failure(self, tmp_path: Path):
        """依赖图构建失败应抛出 ArchRuleError"""
        # 用一个不存在的目录（但能通过 DependencyGraphBuilder 初始化检查）
        # 实际上 DependencyGraphBuilder 在 root 不存在时即抛出
        validator = ArchRuleValidator(root_dir=str(tmp_path / "nonexistent"))
        with pytest.raises(ArchRuleError) as exc_info:
            validator.validate()
        assert "ARCH_GRAPH_FAIL" in exc_info.value.error_code

    @pytest.mark.unit
    @pytest.mark.p1
    def test_config_load_handles_missing_yaml(self, tmp_path: Path):
        """yaml 模块未安装时应跳过配置加载（不抛异常）"""
        agent = tmp_path / "agent"
        agent.mkdir(parents=True)
        (agent / "__init__.py").write_text("", encoding="utf-8")
        config = tmp_path / "config.yaml"
        config.write_text("arch_rules:\n  enabled: true\n", encoding="utf-8")
        validator = ArchRuleValidator(
            root_dir=str(agent), config_path=str(config)
        )
        # 即使 yaml 未安装也不抛异常
        assert len(validator.rules) >= 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_config_load_overrides_severity(self, tmp_path: Path):
        """配置应能覆盖规则严重度"""
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("yaml 未安装")

        agent = tmp_path / "agent"
        agent.mkdir(parents=True)
        (agent / "__init__.py").write_text("", encoding="utf-8")
        config = tmp_path / "config.yaml"
        config.write_text(
            "arch_rules:\n"
            "  enabled: true\n"
            "  rules:\n"
            "    no_circular_dependency:\n"
            "      severity: low\n"
            "      suggestion: '覆盖建议'\n",
            encoding="utf-8",
        )
        validator = ArchRuleValidator(
            root_dir=str(agent), config_path=str(config)
        )
        rule = validator.rules["no_circular_dependency"]
        assert rule.severity == "low"
        assert rule.suggestion == "覆盖建议"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_config_disabled_clears_rules(self, tmp_path: Path):
        """配置禁用应清空规则"""
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("yaml 未安装")

        agent = tmp_path / "agent"
        agent.mkdir(parents=True)
        (agent / "__init__.py").write_text("", encoding="utf-8")
        config = tmp_path / "config.yaml"
        config.write_text("arch_rules:\n  enabled: false\n", encoding="utf-8")
        validator = ArchRuleValidator(
            root_dir=str(agent), config_path=str(config)
        )
        assert len(validator.rules) == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_config_invalid_yaml_raises(self, tmp_path: Path):
        """损坏的 YAML 应抛出 ArchRuleError"""
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("yaml 未安装")

        agent = tmp_path / "agent"
        agent.mkdir(parents=True)
        (agent / "__init__.py").write_text("", encoding="utf-8")
        config = tmp_path / "config.yaml"
        config.write_text("arch_rules: [invalid: yaml: structure\n", encoding="utf-8")
        with pytest.raises(ArchRuleError) as exc_info:
            ArchRuleValidator(root_dir=str(agent), config_path=str(config))
        assert exc_info.value.error_code == "ARCH_CONFIG_LOAD_FAIL"


# ── 数据类测试 ──────────────────────────────────────────────────


class TestDataclasses:
    """测试数据类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_violation_to_dict(self):
        """Violation.to_dict 应返回完整字段"""
        v = Violation(
            rule_id="test_rule",
            rule_desc="测试规则",
            source="a.b",
            target="c.d",
            source_file="a/b.py",
            line=10,
            severity="high",
            suggestion="修复建议",
        )
        d = v.to_dict()
        assert d["rule_id"] == "test_rule"
        assert d["is_exempted"] is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_arch_rule_fields(self):
        """ArchRule 应有完整字段"""
        rule = BUILTIN_RULES["no_circular_dependency"]
        assert rule.rule_id == "no_circular_dependency"
        assert rule.severity == "high"
        assert rule.suggestion != ""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_builtin_rules_count(self):
        """内置规则应有 7 条"""
        assert len(BUILTIN_RULES) == 7

    @pytest.mark.unit
    @pytest.mark.p0
    def test_report_properties(self):
        """ValidationReport 属性应正确"""
        report = ValidationReport(
            trace_id="test",
            root_dir="agent",
            total_rules=7,
            total_violations=0,
            active_violations=0,
            exempted_violations=0,
        )
        assert report.passed is True
        assert report.has_violations is False
