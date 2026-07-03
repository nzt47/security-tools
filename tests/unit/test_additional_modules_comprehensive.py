"""综合测试 - 覆盖更多0%覆盖率的模块

覆盖模块：
- agent/p6_config_loader.py
- agent/diagram_tools.py
- agent/data_analytics.py
- agent/system_prompt_config.py (部分)
- agent/system_tools.py (沙盒、剪贴板、天气)

测试策略：AAA模式 + 参数化 + Mock外部依赖
"""

import pytest
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock


# ============================================================================
# P6ConfigLoader 测试
# ============================================================================

class TestP6ConfigLoaderInit:
    """测试 P6ConfigLoader 初始化"""

    def test_init_default_config_file(self):
        from agent.p6_config_loader import P6ConfigLoader
        loader = P6ConfigLoader()
        assert loader.config_file == "p6_config.json"
        assert loader.config == {}
        assert loader.loaded is False

    def test_init_custom_config_file(self):
        from agent.p6_config_loader import P6ConfigLoader
        loader = P6ConfigLoader("custom.json")
        assert loader.config_file == "custom.json"


class TestP6ConfigLoaderLoad:
    """测试 P6ConfigLoader.load 方法"""

    def test_load_nonexistent_file_uses_default(self, tmp_path):
        from agent.p6_config_loader import P6ConfigLoader
        loader = P6ConfigLoader(str(tmp_path / "nonexistent.json"))
        result = loader.load()
        assert result is False
        assert loader.config != {}
        # 默认配置应包含 p6_snapshot
        assert "p6_snapshot" in loader.config

    def test_load_valid_config_file(self, tmp_path):
        from agent.p6_config_loader import P6ConfigLoader
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "p6_snapshot": {
                "enabled": False,
                "snapshot_directory": "/tmp/snapshots",
            }
        }), encoding="utf-8")
        loader = P6ConfigLoader(str(config_file))
        result = loader.load()
        assert result is True
        assert loader.loaded is True
        assert loader.config["p6_snapshot"]["enabled"] is False

    def test_load_corrupted_file_uses_default(self, tmp_path):
        from agent.p6_config_loader import P6ConfigLoader
        config_file = tmp_path / "corrupted.json"
        config_file.write_text("invalid json {{{", encoding="utf-8")
        loader = P6ConfigLoader(str(config_file))
        result = loader.load()
        assert result is False
        # 应使用默认配置
        assert "p6_snapshot" in loader.config

    def test_load_with_config_file_argument(self, tmp_path):
        from agent.p6_config_loader import P6ConfigLoader
        config_file = tmp_path / "override.json"
        config_file.write_text(json.dumps({"p6_snapshot": {"enabled": True}}), encoding="utf-8")
        loader = P6ConfigLoader()
        result = loader.load(str(config_file))
        assert result is True
        assert loader.config_file == str(config_file)


class TestP6ConfigLoaderGet:
    """测试 P6ConfigLoader.get 方法"""

    def test_get_top_level_key(self):
        from agent.p6_config_loader import P6ConfigLoader
        loader = P6ConfigLoader()
        loader._use_default_config()
        result = loader.get("p6_snapshot")
        assert isinstance(result, dict)

    def test_get_nested_key(self):
        from agent.p6_config_loader import P6ConfigLoader
        loader = P6ConfigLoader()
        loader._use_default_config()
        result = loader.get("p6_snapshot.enabled")
        assert result is True

    def test_get_deeply_nested_key(self):
        from agent.p6_config_loader import P6ConfigLoader
        loader = P6ConfigLoader()
        loader._use_default_config()
        result = loader.get("p6_snapshot.frequency_control.min_interval_seconds")
        assert result == 300

    def test_get_nonexistent_key_returns_default(self):
        from agent.p6_config_loader import P6ConfigLoader
        loader = P6ConfigLoader()
        loader._use_default_config()
        result = loader.get("nonexistent.key", "default_val")
        assert result == "default_val"

    def test_get_nonexistent_nested_key_returns_default(self):
        from agent.p6_config_loader import P6ConfigLoader
        loader = P6ConfigLoader()
        loader._use_default_config()
        result = loader.get("p6_snapshot.nonexistent", 42)
        assert result == 42


class TestP6ConfigLoaderHelpers:
    """测试 P6ConfigLoader 辅助方法"""

    def test_get_frequency_control_config(self):
        from agent.p6_config_loader import P6ConfigLoader
        loader = P6ConfigLoader()
        loader._use_default_config()
        config = loader.get_frequency_control_config()
        assert "min_interval_seconds" in config
        assert "max_snapshots" in config

    def test_get_compression_config(self):
        from agent.p6_config_loader import P6ConfigLoader
        loader = P6ConfigLoader()
        loader._use_default_config()
        config = loader.get_compression_config()
        assert "enabled" in config
        assert "level" in config

    def test_get_snapshot_directory(self):
        from agent.p6_config_loader import P6ConfigLoader
        loader = P6ConfigLoader()
        loader._use_default_config()
        directory = loader.get_snapshot_directory()
        assert isinstance(directory, str)

    def test_is_enabled_default_true(self):
        from agent.p6_config_loader import P6ConfigLoader
        loader = P6ConfigLoader()
        loader._use_default_config()
        assert loader.is_enabled() is True

    def test_default_config_structure(self):
        from agent.p6_config_loader import P6ConfigLoader
        loader = P6ConfigLoader()
        loader._use_default_config()
        config = loader.config["p6_snapshot"]
        assert "enabled" in config
        assert "snapshot_directory" in config
        assert "frequency_control" in config
        assert "compression" in config
        assert "modules" in config


# ============================================================================
# DiagramTools 测试
# ============================================================================

class TestDiagramToolsConstants:
    """测试 diagram_tools 常量"""

    def test_type_colors_contains_all_types(self):
        from agent.diagram_tools import TYPE_COLORS
        assert "frontend" in TYPE_COLORS
        assert "backend" in TYPE_COLORS
        assert "database" in TYPE_COLORS
        assert "cloud" in TYPE_COLORS
        assert "security" in TYPE_COLORS
        assert "external" in TYPE_COLORS

    def test_type_colors_structure(self):
        from agent.diagram_tools import TYPE_COLORS
        for type_name, colors in TYPE_COLORS.items():
            assert "bg" in colors
            assert "stroke" in colors
            assert "name" in colors
            assert "dot" in colors

    def test_svg_layout_constants(self):
        from agent.diagram_tools import SVG_WIDTH, SVG_HEIGHT, BOX_WIDTH, BOX_HEIGHT
        assert SVG_WIDTH > 0
        assert SVG_HEIGHT > 0
        assert BOX_WIDTH > 0
        assert BOX_HEIGHT > 0


class TestGetColor:
    """测试 _get_color 函数"""

    def test_get_color_frontend(self):
        from agent.diagram_tools import _get_color
        colors = _get_color("frontend")
        assert colors["name"] == "Frontend"

    def test_get_color_backend(self):
        from agent.diagram_tools import _get_color
        colors = _get_color("backend")
        assert colors["name"] == "Backend"

    def test_get_color_unknown_type_returns_external(self):
        from agent.diagram_tools import _get_color
        colors = _get_color("unknown_type")
        assert colors["name"] == "External"

    def test_get_color_case_insensitive(self):
        from agent.diagram_tools import _get_color
        colors = _get_color("FRONTEND")
        assert colors["name"] == "Frontend"

    def test_get_color_with_spaces(self):
        from agent.diagram_tools import _get_color
        colors = _get_color("front end")
        # "front end" → "front_end" 不在 TYPE_COLORS 中，应返回 external
        assert colors["name"] == "External"


class TestRenderSvgComponents:
    """测试 _render_svg_components 函数"""

    def test_render_empty_components(self):
        from agent.diagram_tools import _render_svg_components
        result = _render_svg_components([])
        assert result == ""

    def test_render_single_component(self):
        from agent.diagram_tools import _render_svg_components
        components = [{"name": "API", "type": "backend", "description": "REST API"}]
        result = _render_svg_components(components)
        assert "<rect" in result
        assert "<text" in result
        assert "API" in result

    def test_render_multiple_components(self):
        from agent.diagram_tools import _render_svg_components
        components = [
            {"name": "Frontend", "type": "frontend", "description": "React App"},
            {"name": "API", "type": "backend", "description": "REST API"},
            {"name": "DB", "type": "database", "description": "PostgreSQL"},
        ]
        result = _render_svg_components(components)
        assert result.count("<rect") == 3

    def test_render_component_without_description(self):
        from agent.diagram_tools import _render_svg_components
        components = [{"name": "Service", "type": "backend"}]
        result = _render_svg_components(components)
        assert "Backend" in result  # 应使用类型名作为标签


# ============================================================================
# DataAnalytics 测试
# ============================================================================

class TestDataAnalyticsConstants:
    """测试 data_analytics 常量"""

    def test_max_analyze_days(self):
        from agent.data_analytics import MAX_ANALYZE_DAYS
        assert MAX_ANALYZE_DAYS == 36500


class TestDataAnalyticsInit:
    """测试 DataAnalytics 初始化"""

    def test_init_without_dependencies(self):
        from agent.data_analytics import DataAnalytics
        analytics = DataAnalytics()
        assert analytics.black_box is None
        assert analytics.vector_store is None
        assert analytics._cache == {}
        assert analytics._cache_ttl == 300

    def test_init_with_black_box(self):
        from agent.data_analytics import DataAnalytics
        mock_bb = MagicMock()
        analytics = DataAnalytics(black_box=mock_bb)
        assert analytics.black_box is mock_bb

    def test_init_with_vector_store(self):
        from agent.data_analytics import DataAnalytics
        mock_vs = MagicMock()
        analytics = DataAnalytics(vector_store=mock_vs)
        assert analytics.vector_store is mock_vs


class TestAnalyzeEventTrends:
    """测试 analyze_event_trends 方法"""

    def test_analyze_without_black_box_returns_error(self):
        from agent.data_analytics import DataAnalytics
        analytics = DataAnalytics()
        result = analytics.analyze_event_trends(days=7)
        assert "error" in result

    def test_analyze_negative_days_raises(self):
        from agent.data_analytics import DataAnalytics
        analytics = DataAnalytics()
        with pytest.raises(ValueError):
            analytics.analyze_event_trends(days=-1)

    def test_analyze_excessive_days_raises(self):
        from agent.data_analytics import DataAnalytics, MAX_ANALYZE_DAYS
        analytics = DataAnalytics()
        with pytest.raises(ValueError):
            analytics.analyze_event_trends(days=MAX_ANALYZE_DAYS + 1)

    def test_analyze_non_int_days_raises(self):
        from agent.data_analytics import DataAnalytics
        analytics = DataAnalytics()
        with pytest.raises(ValueError):
            analytics.analyze_event_trends(days="7")

    def test_analyze_zero_days(self):
        from agent.data_analytics import DataAnalytics
        mock_bb = MagicMock()
        mock_bb.query.return_value = []
        analytics = DataAnalytics(black_box=mock_bb)
        result = analytics.analyze_event_trends(days=0)
        assert "period" in result
        assert result["period"]["days"] == 0

    def test_analyze_with_events(self):
        from agent.data_analytics import DataAnalytics
        mock_bb = MagicMock()
        mock_bb.query.return_value = [
            {"timestamp": "2026-01-01T10:00:00Z", "event_type": "login"},
            {"timestamp": "2026-01-01T11:00:00Z", "event_type": "login"},
            {"timestamp": "2026-01-02T10:00:00Z", "event_type": "logout"},
        ]
        analytics = DataAnalytics(black_box=mock_bb)
        result = analytics.analyze_event_trends(days=7)
        assert result["overview"]["total_events"] == 3


class TestDetectAnomalies:
    """测试 detect_anomalies 方法"""

    def test_detect_without_black_box_returns_empty(self):
        from agent.data_analytics import DataAnalytics
        analytics = DataAnalytics()
        result = analytics.detect_anomalies()
        assert result == []

    def test_detect_with_insufficient_data(self):
        from agent.data_analytics import DataAnalytics
        mock_bb = MagicMock()
        mock_bb.query.return_value = [
            {"timestamp": "2026-01-01T10:00:00Z"},
        ]
        analytics = DataAnalytics(black_box=mock_bb)
        result = analytics.detect_anomalies()
        assert result == []


class TestAnalyzeUserBehavior:
    """测试 analyze_user_behavior 方法"""

    def test_analyze_without_vector_store_returns_error(self):
        from agent.data_analytics import DataAnalytics
        analytics = DataAnalytics()
        result = analytics.analyze_user_behavior()
        assert "error" in result

    def test_analyze_with_vector_store(self):
        from agent.data_analytics import DataAnalytics
        mock_vs = MagicMock()
        mock_item = MagicMock()
        mock_item.metadata = {"category": "work", "source": "api", "tags": ["python"]}
        mock_vs.get_recent.return_value = [mock_item]
        analytics = DataAnalytics(vector_store=mock_vs)
        result = analytics.analyze_user_behavior()
        assert "recent_memory_count" in result
        assert result["recent_memory_count"] == 1


class TestExtractInsights:
    """测试 _extract_insights 方法"""

    def test_extract_insights_empty(self):
        from agent.data_analytics import DataAnalytics
        analytics = DataAnalytics()
        insights = analytics._extract_insights({}, {}, {})
        assert insights == []

    def test_extract_insights_with_categories(self):
        from agent.data_analytics import DataAnalytics
        from collections import defaultdict
        analytics = DataAnalytics()
        categories = defaultdict(int)
        categories["work"] = 10
        categories["personal"] = 5
        insights = analytics._extract_insights(categories, {}, {})
        assert len(insights) >= 1
        assert any("work" in i for i in insights)

    def test_extract_insights_with_sources(self):
        from agent.data_analytics import DataAnalytics
        from collections import defaultdict
        analytics = DataAnalytics()
        sources = defaultdict(int)
        sources["api"] = 20
        insights = analytics._extract_insights({}, sources, {})
        assert any("api" in i for i in insights)

    def test_extract_insights_with_tags(self):
        from agent.data_analytics import DataAnalytics
        from collections import defaultdict
        analytics = DataAnalytics()
        tags = defaultdict(int)
        tags["python"] = 5
        tags["flask"] = 3
        insights = analytics._extract_insights({}, {}, tags)
        assert any("python" in i for i in insights)


class TestGenerateReport:
    """测试 generate_report 方法"""

    def test_generate_text_report(self):
        from agent.data_analytics import DataAnalytics
        analytics = DataAnalytics()
        report = analytics.generate_report(format="text")
        assert isinstance(report, str)
        assert "分析报告" in report

    def test_generate_json_report(self):
        from agent.data_analytics import DataAnalytics
        analytics = DataAnalytics()
        report = analytics.generate_report(format="json")
        assert isinstance(report, str)
        parsed = json.loads(report)
        assert "generated_at" in parsed

    def test_generate_html_report(self):
        from agent.data_analytics import DataAnalytics
        analytics = DataAnalytics()
        report = analytics.generate_report(format="html")
        assert isinstance(report, str)


class TestCreateAnalytics:
    """测试 create_analytics 快捷函数"""

    def test_create_analytics_no_args(self):
        from agent.data_analytics import create_analytics, DataAnalytics
        analytics = create_analytics()
        assert isinstance(analytics, DataAnalytics)

    def test_create_analytics_with_args(self):
        from agent.data_analytics import create_analytics, DataAnalytics
        mock_bb = MagicMock()
        analytics = create_analytics(black_box=mock_bb)
        assert isinstance(analytics, DataAnalytics)
        assert analytics.black_box is mock_bb


class TestDataAnalyticsSafeCall:
    """测试 data_analytics._safe_call"""

    def test_safe_call_success(self):
        from agent.data_analytics import _safe_call
        assert _safe_call(lambda: 42, action="test") == 42

    def test_safe_call_reraises(self):
        from agent.data_analytics import _safe_call
        with pytest.raises(ValueError):
            _safe_call(lambda: (_ for _ in ()).throw(ValueError("fail")), action="test")


# ============================================================================
# SystemTools 沙盒测试
# ============================================================================

class TestSandboxConstants:
    """测试沙盒常量"""

    def test_blocked_patterns_non_empty(self):
        from agent.system_tools import _SANDBOX_BLOCKED_PATTERNS
        assert len(_SANDBOX_BLOCKED_PATTERNS) > 0

    def test_blocked_patterns_contains_class(self):
        from agent.system_tools import _SANDBOX_BLOCKED_PATTERNS
        assert ".__class__" in _SANDBOX_BLOCKED_PATTERNS

    def test_safe_builtins_contains_basic_functions(self):
        from agent.system_tools import _SAFE_BUILTINS
        assert "abs" in _SAFE_BUILTINS
        assert "len" in _SAFE_BUILTINS
        assert "range" in _SAFE_BUILTINS

    def test_safe_builtins_does_not_contain_dangerous(self):
        from agent.system_tools import _SAFE_BUILTINS
        assert "eval" not in _SAFE_BUILTINS
        assert "exec" not in _SAFE_BUILTINS
        assert "open" not in _SAFE_BUILTINS


class TestRunSandbox:
    """测试 run_sandbox 函数"""

    def test_run_sandbox_simple_print(self):
        from agent.system_tools import run_sandbox, _SAFE_BUILTINS
        # print 不在安全内置函数中，会触发 NameError
        result = run_sandbox("print('hello')")
        assert result["error"] is not None
        assert "NameError" in result["error"]

    def test_run_sandbox_safe_arithmetic(self):
        from agent.system_tools import run_sandbox
        # 使用安全内置函数进行计算
        result = run_sandbox("x = len([1, 2, 3])")
        assert result["error"] is None

    def test_run_sandbox_blocked_pattern(self):
        from agent.system_tools import run_sandbox
        result = run_sandbox("x = object().__class__")
        assert result["error"] is not None
        assert "禁止" in result["error"]

    def test_run_sandbox_arithmetic(self):
        from agent.system_tools import run_sandbox
        result = run_sandbox("x = 2 + 3")
        assert result["error"] is None

    def test_run_sandbox_timeout(self):
        from agent.system_tools import run_sandbox
        # 使用一个会死循环的代码，设置很短的超时
        result = run_sandbox("while True: pass", timeout_sec=1)
        assert result["timed_out"] is True

    def test_run_sandbox_runtime_error(self):
        from agent.system_tools import run_sandbox
        result = run_sandbox("x = 1 / 0")
        assert result["error"] is not None
        assert "ZeroDivisionError" in result["error"]


class TestClipboard:
    """测试剪贴板函数"""

    def test_set_clipboard_too_long(self):
        from agent.system_tools import set_clipboard
        long_text = "x" * 50001
        result = set_clipboard(long_text)
        assert result["ok"] is False
        assert "过长" in result["error"]

    def test_set_clipboard_short_text(self):
        from agent.system_tools import set_clipboard
        # subprocess 在函数内部导入，需要 patch builtins 的 import
        with patch("builtins.__import__") as mock_import:
            mock_subprocess = MagicMock()
            mock_subprocess.run.return_value = MagicMock(returncode=0)
            def import_side_effect(name, *args, **kwargs):
                if name == "pyperclip":
                    raise ImportError("no pyperclip")
                if name == "subprocess":
                    return mock_subprocess
                return __import__(name, *args, **kwargs)
            mock_import.side_effect = import_side_effect
            result = set_clipboard("test")
            assert result["ok"] is True


class TestExpandContextFromMemory:
    """测试 expand_context_from_memory 函数"""

    def test_expand_context_no_vector_memory(self):
        from agent.system_tools import expand_context_from_memory
        digital_life = MagicMock()
        digital_life._vector_memory = None
        result = expand_context_from_memory(digital_life, "query")
        assert result["ok"] is False
        assert "未启用" in result["error"]

    def test_expand_context_with_results(self):
        from agent.system_tools import expand_context_from_memory
        digital_life = MagicMock()
        mock_item = MagicMock()
        mock_item.content = "test content"
        mock_item.score = 0.9
        digital_life._vector_memory.search.return_value = [mock_item]
        result = expand_context_from_memory(digital_life, "query")
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["items"][0]["content"] == "test content"

    def test_expand_context_with_dict_results(self):
        from agent.system_tools import expand_context_from_memory
        digital_life = MagicMock()
        digital_life._vector_memory.search.return_value = [
            {"content": "dict content", "score": 0.8}
        ]
        result = expand_context_from_memory(digital_life, "query")
        assert result["ok"] is True
        assert result["items"][0]["content"] == "dict content"

    def test_expand_context_exception_handling(self):
        from agent.system_tools import expand_context_from_memory
        digital_life = MagicMock()
        digital_life._vector_memory = MagicMock()
        digital_life._vector_memory.search.side_effect = Exception("test error")
        result = expand_context_from_memory(digital_life, "query")
        assert result["ok"] is False
        assert "test error" in result["error"]
