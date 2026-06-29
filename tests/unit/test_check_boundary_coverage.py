# -*- coding: utf-8 -*-
"""
scripts/check_boundary_coverage.py 单元测试

【测试目标】
覆盖关键词识别、模块归属推断、报告生成、新增模块阻断、配置加载/校验、
降级报告输出等关键路径，确保边界覆盖扫描脚本健壮可靠。

【测试维度】
- 功能测试：核心扫描流程
- 边界测试：空输入/非法配置/缺失字段
- 异常测试：文件读取失败/语法错误
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from check_boundary_coverage import (
    BoundaryScanner,
    ConfigLoader,
    ModuleResolver,
    NewModuleDetector,
    ReportGenerator,
    TestCase,
    TestFileParser,
    _generate_degraded_report,
    _trace_id,
)


# ═══════════════════════════════════════════════════════════════
#  测试夹具
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def sample_config() -> dict:
    """标准测试配置"""
    return {
        "global": {
            "keywords": {
                "boundary": ["boundary", "edge"],
                "empty": ["empty", "blank"],
                "timeout": ["timeout"],
                "null": ["null", "none"],
                "invalid": ["invalid"],
                "overflow": ["overflow"],
                "underflow": ["underflow"],
                "extreme": ["extreme", "max", "min", "zero"],
            },
            "module_root": "agent",
            "test_root": "tests",
            "legacy_exempt": False,
        },
        "modules": {
            "circuit_breaker": {
                "required_scenes": ["boundary", "timeout", "extreme"],
                "min_tests": 3,
                "description": "熔断器",
            },
            "memory": {
                "required_scenes": ["empty", "overflow", "null"],
                "min_tests": 3,
                "description": "记忆系统",
            },
        },
        "ci_policy": {
            "enforce_new_modules": True,
            "legacy_strategy": "warn",
            "report_path": "docs/observability/boundary_coverage_report.md",
            "json_report_path": "docs/observability/boundary_coverage_report.json",
        },
    }


@pytest.fixture
def temp_project(tmp_path: Path, sample_config: dict) -> Path:
    """构建临时项目结构，包含 agent/ 和 tests/ 目录"""
    # 创建 agent/ 模块目录
    (tmp_path / "agent" / "circuit_breaker").mkdir(parents=True)
    (tmp_path / "agent" / "memory").mkdir(parents=True)
    (tmp_path / "agent" / "new_module").mkdir(parents=True)  # 模拟新增模块

    # 创建测试文件
    tests_dir = tmp_path / "tests" / "unit"
    tests_dir.mkdir(parents=True)

    # circuit_breaker 边界测试（齐全）
    (tests_dir / "test_circuit_breaker_boundary.py").write_text(
        "def test_circuit_breaker_boundary_state_change():\n"
        "    assert True\n\n"
        "def test_circuit_breaker_timeout_recovery():\n"
        "    assert True\n\n"
        "def test_circuit_breaker_extreme_error_rate():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    # memory 边界测试（缺失 null 场景）
    (tests_dir / "test_memory_empty_input.py").write_text(
        "def test_memory_empty_input():\n"
        "    assert True\n\n"
        "def test_memory_overflow_huge_payload():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    # new_module 无任何边界测试（应被阻断）
    (tests_dir / "test_new_module_basic.py").write_text(
        "def test_new_module_basic_functionality():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    # 写入配置文件
    import yaml
    (tmp_path / "tests" / "boundary_config.yaml").write_text(
        yaml.dump(sample_config, allow_unicode=True),
        encoding="utf-8",
    )

    return tmp_path


# ═══════════════════════════════════════════════════════════════
#  ConfigLoader 测试
# ═══════════════════════════════════════════════════════════════

class TestConfigLoader:
    """配置加载器测试"""

    def test_load_valid_config(self, temp_project: Path):
        """功能测试：加载合法配置"""
        config_path = temp_project / "tests" / "boundary_config.yaml"
        loader = ConfigLoader(config_path)
        config = loader.load()
        assert "global" in config
        assert "modules" in config
        assert "ci_policy" in config
        assert "keywords" in config["global"]

    def test_load_missing_config_raises(self, tmp_path: Path):
        """边界测试：配置文件不存在应抛出明确异常"""
        with pytest.raises(FileNotFoundError, match="BOUNDARY_CONFIG_NOT_FOUND"):
            ConfigLoader(tmp_path / "nonexistent.yaml").load()

    def test_load_incomplete_config_raises(self, tmp_path: Path):
        """边界测试：配置缺少必要字段应抛出异常"""
        config_path = tmp_path / "incomplete.yaml"
        import yaml
        config_path.write_text(
            yaml.dump({"global": {}}, allow_unicode=True),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="BOUNDARY_CONFIG_INCOMPLETE"):
            ConfigLoader(config_path).load()

    def test_load_config_without_keywords_raises(self, tmp_path: Path):
        """边界测试：缺少 keywords 字段应抛出异常"""
        config_path = tmp_path / "no_keywords.yaml"
        import yaml
        config_path.write_text(
            yaml.dump({
                "global": {},
                "modules": {},
                "ci_policy": {},
            }, allow_unicode=True),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="BOUNDARY_CONFIG_NO_KEYWORDS"):
            ConfigLoader(config_path).load()


# ═══════════════════════════════════════════════════════════════
#  TestFileParser 测试
# ═══════════════════════════════════════════════════════════════

class TestTestFileParser:
    """测试文件解析器测试"""

    def test_parse_valid_file(self, tmp_path: Path):
        """功能测试：解析合法测试文件"""
        keywords_map = {
            "boundary": ["boundary", "edge"],
            "empty": ["empty"],
        }
        parser = TestFileParser(keywords_map)

        test_file = tmp_path / "test_sample_boundary.py"
        test_file.write_text(
            "def test_boundary_case():\n"
            "    pass\n\n"
            "def test_empty_input():\n"
            "    pass\n\n"
            "def test_normal_case():\n"
            "    pass\n",
            encoding="utf-8",
        )
        cases = parser.parse_file(test_file)
        assert len(cases) == 3

        boundary_case = next(c for c in cases if c.name == "test_boundary_case")
        assert boundary_case.is_boundary is True
        assert "boundary" in boundary_case.matched_keywords

        empty_case = next(c for c in cases if c.name == "test_empty_input")
        assert empty_case.is_boundary is True
        assert "empty" in empty_case.matched_keywords

        normal_case = next(c for c in cases if c.name == "test_normal_case")
        assert normal_case.is_boundary is False

    def test_parse_syntax_error_file(self, tmp_path: Path):
        """异常测试：语法错误的文件应返回空列表，不抛异常"""
        parser = TestFileParser({"boundary": ["boundary"]})
        bad_file = tmp_path / "test_bad.py"
        bad_file.write_text("def test_(\n", encoding="utf-8")
        cases = parser.parse_file(bad_file)
        assert cases == []

    def test_parse_nonexistent_file(self, tmp_path: Path):
        """异常测试：不存在的文件应返回空列表"""
        parser = TestFileParser({"boundary": ["boundary"]})
        cases = parser.parse_file(tmp_path / "nonexistent.py")
        assert cases == []

    def test_keyword_matching_case_insensitive(self, tmp_path: Path):
        """边界测试：关键词匹配应大小写不敏感"""
        parser = TestFileParser({"boundary": ["BOUNDARY"]})
        test_file = tmp_path / "test_mixed_case.py"
        test_file.write_text(
            "def test_Boundary_Case():\n    pass\n",
            encoding="utf-8",
        )
        cases = parser.parse_file(test_file)
        assert len(cases) == 1
        assert cases[0].is_boundary is True

    def test_multiple_scenes_matched(self, tmp_path: Path):
        """功能测试：单个测试可命中多个场景"""
        parser = TestFileParser({
            "boundary": ["boundary"],
            "empty": ["empty"],
        })
        test_file = tmp_path / "test_multi.py"
        test_file.write_text(
            "def test_boundary_and_empty():\n    pass\n",
            encoding="utf-8",
        )
        cases = parser.parse_file(test_file)
        assert len(cases) == 1
        assert set(cases[0].matched_keywords) == {"boundary", "empty"}


# ═══════════════════════════════════════════════════════════════
#  ModuleResolver 测试
# ═══════════════════════════════════════════════════════════════

class TestModuleResolver:
    """模块归属推断测试"""

    def test_resolve_by_filename(self, temp_project: Path):
        """功能测试：通过文件名匹配模块"""
        resolver = ModuleResolver(
            temp_project / "agent",
            temp_project / "tests",
        )
        test_file = temp_project / "tests" / "unit" / "test_circuit_breaker_boundary.py"
        module = resolver.resolve(test_file)
        assert module == "circuit_breaker"

    def test_resolve_by_import(self, temp_project: Path):
        """功能测试：通过 import 语句推断模块"""
        resolver = ModuleResolver(
            temp_project / "agent",
            temp_project / "tests",
        )
        test_file = temp_project / "tests" / "unit" / "test_other.py"
        test_file.write_text(
            "from agent.memory import MemoryStore\n\n"
            "def test_other():\n    pass\n",
            encoding="utf-8",
        )
        module = resolver.resolve(test_file)
        assert module == "memory"

    def test_resolve_unmapped_returns_none(self, temp_project: Path):
        """边界测试：无法归属的测试返回 None"""
        resolver = ModuleResolver(
            temp_project / "agent",
            temp_project / "tests",
        )
        test_file = temp_project / "tests" / "unit" / "test_unknown.py"
        test_file.write_text(
            "def test_unknown():\n    pass\n",
            encoding="utf-8",
        )
        module = resolver.resolve(test_file)
        assert module is None

    def test_resolve_longest_match_priority(self, tmp_path: Path):
        """边界测试：最长匹配优先（避免 memory_vector 误匹配 memory）"""
        (tmp_path / "agent" / "memory").mkdir(parents=True)
        (tmp_path / "agent" / "memory_vector").mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)
        resolver = ModuleResolver(tmp_path / "agent", tmp_path / "tests")
        test_file = tmp_path / "tests" / "test_memory_vector_boundary.py"
        test_file.write_text("def test_x():\n    pass\n", encoding="utf-8")
        module = resolver.resolve(test_file)
        assert module == "memory_vector"


# ═══════════════════════════════════════════════════════════════
#  BoundaryScanner 测试
# ═══════════════════════════════════════════════════════════════

class TestBoundaryScanner:
    """扫描器主类测试"""

    def test_scan_basic(self, temp_project: Path, sample_config: dict):
        """功能测试：扫描器正常执行"""
        scanner = BoundaryScanner(sample_config, temp_project, new_modules=set())
        result = scanner.scan()

        assert result.total_modules >= 2  # 至少 circuit_breaker + memory
        assert result.total_tests > 0
        assert result.total_boundary_tests > 0
        assert result.duration_ms >= 0
        assert result.trace_id  # 非空

    def test_scan_new_module_blocked(self, temp_project: Path, sample_config: dict):
        """边界测试：新增模块无边界测试应被阻断"""
        scanner = BoundaryScanner(
            sample_config,
            temp_project,
            new_modules={"new_module"},
        )
        result = scanner.scan()

        # new_module 无边界测试，应被阻断
        assert "new_module" in result.blocked_modules
        assert result.overall_status == "fail"

        # 找到 new_module 的报告
        new_mod_report = next(
            r for r in result.modules if r.module_name == "new_module"
        )
        assert new_mod_report.is_new_module is True
        assert new_mod_report.status == "❌"

    def test_scan_legacy_module_warn_not_block(
        self, temp_project: Path, sample_config: dict
    ):
        """边界测试：存量模块缺失场景应警告但不阻断"""
        scanner = BoundaryScanner(
            sample_config,
            temp_project,
            new_modules=set(),
        )
        result = scanner.scan()

        # memory 模块缺失 null 场景，应警告
        memory_report = next(
            r for r in result.modules if r.module_name == "memory"
        )
        assert memory_report.status == "⚠️"
        assert "null" in memory_report.missing_scenes
        # 存量模块不阻断
        assert "memory" not in result.blocked_modules

    def test_scan_circuit_breaker_passes(
        self, temp_project: Path, sample_config: dict
    ):
        """功能测试：场景齐全的模块应通过"""
        scanner = BoundaryScanner(
            sample_config, temp_project, new_modules=set()
        )
        result = scanner.scan()

        cb_report = next(
            r for r in result.modules if r.module_name == "circuit_breaker"
        )
        assert cb_report.status == "✅"
        assert cb_report.boundary_tests >= 3
        assert set(cb_report.covered_scenes) >= {"boundary", "timeout", "extreme"}


# ═══════════════════════════════════════════════════════════════
#  NewModuleDetector 测试
# ═══════════════════════════════════════════════════════════════

class TestNewModuleDetector:
    """新增模块检测器测试"""

    def test_detect_returns_empty_when_not_git(self, tmp_path: Path):
        """边界测试：非 git 仓库返回空集合"""
        detector = NewModuleDetector(tmp_path)
        result = detector.detect_new_modules()
        assert result == set()

    def test_detect_handles_subprocess_error(self, tmp_path: Path):
        """异常测试：subprocess 异常应被吞掉，返回空集合"""
        detector = NewModuleDetector(tmp_path)
        with patch("subprocess.run", side_effect=Exception("mock error")):
            result = detector.detect_new_modules()
        assert result == set()


# ═══════════════════════════════════════════════════════════════
#  ReportGenerator 测试
# ═══════════════════════════════════════════════════════════════

class TestReportGenerator:
    """报告生成器测试"""

    def _make_scan_result(self, status: str = "pass") -> object:
        """构造测试用 ScanResult"""
        from check_boundary_coverage import ScanResult, ModuleReport
        report = ModuleReport(
            module_name="test_module",
            description="测试模块",
            total_tests=10,
            boundary_tests=5,
            covered_scenes={"boundary", "empty"},
            required_scenes=["boundary", "empty"],
            min_tests=2,
            status="✅",
            is_new_module=False,
            missing_scenes=[],
            suggestions=[],
            test_cases=[{"name": "test_boundary", "file": "tests/test.py", "scenes": ["boundary"]}],
        )
        return ScanResult(
            trace_id="abc123",
            timestamp="2026-06-26T10:00:00",
            duration_ms=123.45,
            total_modules=1,
            total_tests=10,
            total_boundary_tests=5,
            modules=[report],
            blocked_modules=[],
            overall_status=status,
        )

    def test_generate_markdown_contains_required_sections(self, tmp_path: Path):
        """功能测试：Markdown 报告应包含必要章节"""
        gen = ReportGenerator(tmp_path, {"enforce_new_modules": True, "legacy_strategy": "warn"})
        result = self._make_scan_result()
        md = gen.generate_markdown(result)

        assert "# 边界覆盖扫描报告" in md
        assert "## 总览" in md
        assert "## 模块详情" in md
        assert "## 边界测试用例明细" in md
        assert "## CI 阻断策略" in md
        assert "test_module" in md

    def test_generate_markdown_blocked_status(self, tmp_path: Path):
        """边界测试：阻断状态下报告应显示阻断模块"""
        from check_boundary_coverage import ScanResult, ModuleReport
        gen = ReportGenerator(tmp_path, {"enforce_new_modules": True})
        report = ModuleReport(
            module_name="blocked_mod", description="", total_tests=0,
            boundary_tests=0, status="❌", is_new_module=True,
            missing_scenes=["boundary"],
            suggestions=["新增模块必须包含至少 1 个边界测试"],
        )
        result = ScanResult(
            trace_id="t1", timestamp="2026-06-26T10:00:00", duration_ms=10,
            total_modules=1, total_tests=0, total_boundary_tests=0,
            modules=[report], blocked_modules=["blocked_mod"],
            overall_status="fail",
        )
        md = gen.generate_markdown(result)
        assert "❌ 阻断" in md
        assert "blocked_mod" in md
        assert "CI 阻断" in md

    def test_generate_json_structure(self, tmp_path: Path):
        """功能测试：JSON 报告结构完整"""
        gen = ReportGenerator(tmp_path, {})
        result = self._make_scan_result()
        data = gen.generate_json(result)

        assert data["trace_id"] == "abc123"
        assert data["overall_status"] == "pass"
        assert data["total_modules"] == 1
        assert len(data["modules"]) == 1
        assert data["modules"][0]["module_name"] == "test_module"

    def test_status_badge_mapping(self, tmp_path: Path):
        """功能测试：状态徽章映射正确"""
        gen = ReportGenerator(tmp_path, {})
        assert "✅" in gen._status_badge("pass")
        assert "⚠️" in gen._status_badge("warn")
        assert "❌" in gen._status_badge("fail")


# ═══════════════════════════════════════════════════════════════
#  降级报告测试
# ═══════════════════════════════════════════════════════════════

class TestDegradedReport:
    """降级报告测试"""

    def test_degraded_report_generated_on_error(self, tmp_path: Path):
        """异常测试：错误时应生成降级报告"""
        output = tmp_path / "degraded.md"
        error = RuntimeError("测试错误")
        _generate_degraded_report(error, output)

        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert "降级" in content
        assert "RuntimeError" in content
        assert "测试错误" in content

    def test_degraded_report_with_trace_id(self, tmp_path: Path):
        """功能测试：降级报告应包含 trace_id"""
        output = tmp_path / "degraded.md"
        _generate_degraded_report(ValueError("err"), output)
        content = output.read_text(encoding="utf-8")
        assert "Trace ID" in content


# ═══════════════════════════════════════════════════════════════
#  工具函数测试
# ═══════════════════════════════════════════════════════════════

class TestUtilities:
    """工具函数测试"""

    def test_trace_id_length(self):
        """功能测试：trace_id 长度为 16"""
        tid = _trace_id()
        assert len(tid) == 16
        assert tid.isalnum()

    def test_trace_id_uniqueness(self):
        """功能测试：trace_id 应唯一"""
        ids = {_trace_id() for _ in range(100)}
        assert len(ids) == 100


# ═══════════════════════════════════════════════════════════════
#  CLI 入口测试
# ═══════════════════════════════════════════════════════════════

class TestCLI:
    """CLI 入口测试"""

    def test_cli_json_only_mode(self, temp_project: Path, capsys):
        """功能测试：--json-only 模式输出 JSON"""
        from check_boundary_coverage import main
        config_path = temp_project / "tests" / "boundary_config.yaml"
        # 修改 PROJECT_ROOT 不可行，直接使用 main 函数
        # 通过 patch PROJECT_ROOT 让脚本使用临时项目
        with patch("check_boundary_coverage.PROJECT_ROOT", temp_project):
            exit_code = main([
                "--config", str(config_path),
                "--json-only",
            ])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "overall_status" in data
        assert "modules" in data
        # exit_code 0 或 1 都可接受（取决于是否阻断）
        assert exit_code in (0, 1)

    def test_cli_returns_2_on_config_missing(self, tmp_path: Path, capsys):
        """异常测试：配置缺失应返回退出码 2"""
        from check_boundary_coverage import main
        with patch("check_boundary_coverage.PROJECT_ROOT", tmp_path):
            exit_code = main([
                "--config", str(tmp_path / "nonexistent.yaml"),
                "--json-only",
            ])
        assert exit_code == 2
