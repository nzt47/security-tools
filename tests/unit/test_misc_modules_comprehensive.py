"""综合测试 - 覆盖多个0%覆盖率的工具模块

覆盖模块：
- agent/model_router/cost_tracker.py
- agent/software_manager.py
- agent/software_backends.py
- agent/system_prompt_manager.py
- agent/safety_guard.py
- agent/diff_tools.py

测试策略：AAA模式 + 参数化 + Mock外部依赖
"""

import pytest
import os
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open


# ============================================================================
# CostTracker 测试
# ============================================================================

class TestCostTrackerConstants:
    """测试 MODEL_COSTS 常量"""

    def test_model_costs_contains_gpt4(self):
        from agent.model_router.cost_tracker import MODEL_COSTS
        assert "gpt-4" in MODEL_COSTS
        assert "input" in MODEL_COSTS["gpt-4"]
        assert "output" in MODEL_COSTS["gpt-4"]

    def test_model_costs_contains_gpt35(self):
        from agent.model_router.cost_tracker import MODEL_COSTS
        assert "gpt-3.5-turbo" in MODEL_COSTS

    def test_model_costs_contains_gpt4o_mini(self):
        from agent.model_router.cost_tracker import MODEL_COSTS
        assert "gpt-4o-mini" in MODEL_COSTS

    def test_gpt4_more_expensive_than_gpt35(self):
        from agent.model_router.cost_tracker import MODEL_COSTS
        assert MODEL_COSTS["gpt-4"]["input"] > MODEL_COSTS["gpt-3.5-turbo"]["input"]
        assert MODEL_COSTS["gpt-4"]["output"] > MODEL_COSTS["gpt-3.5-turbo"]["output"]


class TestCostTrackerInit:
    """测试 CostTracker 初始化"""

    def test_init_creates_log_directory(self, tmp_path):
        from agent.model_router.cost_tracker import CostTracker
        log_path = str(tmp_path / "subdir" / "cost_log.jsonl")
        tracker = CostTracker(log_path=log_path)
        assert os.path.exists(str(tmp_path / "subdir"))

    def test_init_with_existing_file(self, tmp_path):
        from agent.model_router.cost_tracker import CostTracker
        log_path = tmp_path / "cost_log.jsonl"
        log_path.write_text(json.dumps({
            "timestamp": "2026-01-01T10:00:00",
            "model": "gpt-4",
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_usd": 0.006,
            "duration_ms": 1000.0,
            "task_type": "test",
            "trace_id": "abc123",
        }) + "\n", encoding="utf-8")
        tracker = CostTracker(log_path=str(log_path))
        summary = tracker.get_summary()
        assert summary["total_calls"] == 1

    def test_init_with_empty_daily_stats(self, tmp_path):
        from agent.model_router.cost_tracker import CostTracker
        tracker = CostTracker(log_path=str(tmp_path / "cost.jsonl"))
        summary = tracker.get_summary()
        assert summary["total_cost_usd"] == 0
        assert summary["total_calls"] == 0


class TestCostTrackerRecord:
    """测试 CostTracker.record 方法"""

    def test_record_writes_to_file(self, tmp_path):
        from agent.model_router.cost_tracker import CostTracker
        log_path = tmp_path / "cost.jsonl"
        tracker = CostTracker(log_path=str(log_path))
        tracker.record("gpt-4", 1000, 500, 1500.0, "chat", "trace1")
        content = log_path.read_text(encoding="utf-8")
        assert "gpt-4" in content
        assert "trace1" in content

    def test_record_calculates_cost_correctly(self, tmp_path):
        from agent.model_router.cost_tracker import CostTracker, MODEL_COSTS
        log_path = tmp_path / "cost.jsonl"
        tracker = CostTracker(log_path=str(log_path))
        tracker.record("gpt-4", 1000, 500, 1000.0)
        content = log_path.read_text(encoding="utf-8")
        record = json.loads(content.strip())
        expected_cost = 1000 / 1000 * MODEL_COSTS["gpt-4"]["input"] + \
                        500 / 1000 * MODEL_COSTS["gpt-4"]["output"]
        assert abs(record["cost_usd"] - round(expected_cost, 6)) < 0.0001

    def test_record_unknown_model_uses_default_cost(self, tmp_path):
        from agent.model_router.cost_tracker import CostTracker
        tracker = CostTracker(log_path=str(tmp_path / "cost.jsonl"))
        tracker.record("unknown-model", 1000, 500, 1000.0)
        summary = tracker.get_summary()
        assert summary["total_cost_usd"] > 0

    def test_record_updates_daily_stats(self, tmp_path):
        from agent.model_router.cost_tracker import CostTracker
        tracker = CostTracker(log_path=str(tmp_path / "cost.jsonl"))
        tracker.record("gpt-4", 100, 50, 500.0)
        summary = tracker.get_summary()
        assert summary["total_calls"] == 1
        assert len(summary["daily"]) == 1

    def test_record_multiple_calls_accumulate(self, tmp_path):
        from agent.model_router.cost_tracker import CostTracker
        tracker = CostTracker(log_path=str(tmp_path / "cost.jsonl"))
        tracker.record("gpt-4", 100, 50, 500.0)
        tracker.record("gpt-3.5-turbo", 200, 100, 300.0)
        summary = tracker.get_summary()
        assert summary["total_calls"] == 2


class TestCostTrackerGetSummary:
    """测试 CostTracker.get_summary 方法"""

    def test_summary_structure(self, tmp_path):
        from agent.model_router.cost_tracker import CostTracker
        tracker = CostTracker(log_path=str(tmp_path / "cost.jsonl"))
        summary = tracker.get_summary()
        assert "total_cost_usd" in summary
        assert "total_calls" in summary
        assert "daily" in summary

    def test_summary_with_multiple_days(self, tmp_path):
        from agent.model_router.cost_tracker import CostTracker
        log_path = tmp_path / "cost.jsonl"
        # 写入两天的记录
        log_path.write_text(
            json.dumps({"timestamp": "2026-01-01T10:00:00", "model": "gpt-4",
                        "input_tokens": 100, "output_tokens": 50,
                        "cost_usd": 0.006, "duration_ms": 1000.0}) + "\n" +
            json.dumps({"timestamp": "2026-01-02T10:00:00", "model": "gpt-4",
                        "input_tokens": 200, "output_tokens": 100,
                        "cost_usd": 0.012, "duration_ms": 2000.0}) + "\n",
            encoding="utf-8")
        tracker = CostTracker(log_path=str(log_path))
        summary = tracker.get_summary()
        assert len(summary["daily"]) == 2
        assert summary["total_calls"] == 2


class TestCostTrackerLoadExisting:
    """测试 CostTracker._load_existing 方法"""

    def test_load_existing_empty_file(self, tmp_path):
        from agent.model_router.cost_tracker import CostTracker
        log_path = tmp_path / "cost.jsonl"
        log_path.write_text("", encoding="utf-8")
        tracker = CostTracker(log_path=str(log_path))
        assert tracker.get_summary()["total_calls"] == 0

    def test_load_existing_corrupted_file(self, tmp_path):
        from agent.model_router.cost_tracker import CostTracker
        log_path = tmp_path / "cost.jsonl"
        log_path.write_text("invalid json\n{broken", encoding="utf-8")
        # 不应抛出异常，应优雅处理
        tracker = CostTracker(log_path=str(log_path))

    def test_load_existing_nonexistent_file(self, tmp_path):
        from agent.model_router.cost_tracker import CostTracker
        tracker = CostTracker(log_path=str(tmp_path / "nonexistent.jsonl"))
        assert tracker.get_summary()["total_calls"] == 0


# ============================================================================
# SoftwareManager 测试
# ============================================================================

class TestSoftwareManager:
    """测试 SoftwareManager 类"""

    def test_init_empty_software(self):
        from agent.software_manager import SoftwareManager
        mgr = SoftwareManager()
        assert mgr.get_installed_software() == []

    def test_check_updates_returns_list(self):
        from agent.software_manager import SoftwareManager
        mgr = SoftwareManager()
        result = mgr.check_updates()
        assert isinstance(result, list)

    def test_install_returns_true(self):
        from agent.software_manager import SoftwareManager
        mgr = SoftwareManager()
        assert mgr.install("test_software") is True

    def test_uninstall_returns_true(self):
        from agent.software_manager import SoftwareManager
        mgr = SoftwareManager()
        assert mgr.uninstall("test_software") is True

    def test_is_installed_false_for_unknown(self):
        from agent.software_manager import SoftwareManager
        mgr = SoftwareManager()
        assert mgr.is_installed("unknown") is False


class TestSafeCall:
    """测试 _safe_call 函数"""

    def test_safe_call_success(self):
        from agent.software_manager import _safe_call
        result = _safe_call(lambda x: x * 2, 5, action="test")
        assert result == 10

    def test_safe_call_reraises_exception(self):
        from agent.software_manager import _safe_call
        def failing_func():
            raise ValueError("test error")
        with pytest.raises(ValueError):
            _safe_call(failing_func, action="test")

    def test_safe_call_with_kwargs(self):
        from agent.software_manager import _safe_call
        def func(a, b=0):
            return a + b
        result = _safe_call(func, 1, b=2, action="test")
        assert result == 3


# ============================================================================
# SoftwareBackends 测试
# ============================================================================

class TestChocolateyBackend:
    """测试 ChocolateyBackend"""

    def test_install_returns_true(self):
        from agent.software_backends import ChocolateyBackend
        backend = ChocolateyBackend()
        assert backend.install("test_pkg") is True

    def test_install_with_version(self):
        from agent.software_backends import ChocolateyBackend
        backend = ChocolateyBackend()
        assert backend.install("test_pkg", "1.0.0") is True

    def test_uninstall_returns_true(self):
        from agent.software_backends import ChocolateyBackend
        backend = ChocolateyBackend()
        assert backend.uninstall("test_pkg") is True

    def test_update_returns_true(self):
        from agent.software_backends import ChocolateyBackend
        backend = ChocolateyBackend()
        assert backend.update("test_pkg") is True


class TestPipBackend:
    """测试 PipBackend"""

    def test_install_returns_true(self):
        from agent.software_backends import PipBackend
        backend = PipBackend()
        assert backend.install("test_pkg") is True

    def test_install_with_version(self):
        from agent.software_backends import PipBackend
        backend = PipBackend()
        assert backend.install("test_pkg", "1.0.0") is True

    def test_uninstall_returns_true(self):
        from agent.software_backends import PipBackend
        backend = PipBackend()
        assert backend.uninstall("test_pkg") is True

    def test_update_returns_true(self):
        from agent.software_backends import PipBackend
        backend = PipBackend()
        assert backend.update("test_pkg") is True


class TestNpmBackend:
    """测试 NpmBackend"""

    def test_install_returns_true(self):
        from agent.software_backends import NpmBackend
        backend = NpmBackend()
        assert backend.install("test_pkg") is True

    def test_install_with_version(self):
        from agent.software_backends import NpmBackend
        backend = NpmBackend()
        assert backend.install("test_pkg", "1.0.0") is True

    def test_uninstall_returns_true(self):
        from agent.software_backends import NpmBackend
        backend = NpmBackend()
        assert backend.uninstall("test_pkg") is True

    def test_update_returns_true(self):
        from agent.software_backends import NpmBackend
        backend = NpmBackend()
        assert backend.update("test_pkg") is True


class TestWebDownloadBackend:
    """测试 WebDownloadBackend"""

    def test_download_returns_true(self):
        from agent.software_backends import WebDownloadBackend
        backend = WebDownloadBackend()
        assert backend.download("http://example.com/file", "/tmp/file") is True


class TestGitHubBackend:
    """测试 GitHubBackend"""

    def test_clone_returns_true(self):
        from agent.software_backends import GitHubBackend
        backend = GitHubBackend()
        assert backend.clone("https://github.com/user/repo", "/tmp/repo") is True


class TestSoftwareBackendsSafeCall:
    """测试 software_backends._safe_call"""

    def test_safe_call_success(self):
        from agent.software_backends import _safe_call
        assert _safe_call(lambda: 42, action="test") == 42

    def test_safe_call_reraises(self):
        from agent.software_backends import _safe_call
        with pytest.raises(RuntimeError):
            _safe_call(lambda: (_ for _ in ()).throw(RuntimeError("fail")), action="test")


# ============================================================================
# SystemPromptManager 测试
# ============================================================================

class TestSystemPromptTemplate:
    """测试系统提示词模板管理"""

    def test_default_template_contains_placeholders(self):
        from agent.system_prompt_manager import DEFAULT_TEMPLATE
        assert "{current_date}" in DEFAULT_TEMPLATE
        assert "{body_status}" in DEFAULT_TEMPLATE
        assert "{mode_name}" in DEFAULT_TEMPLATE

    def test_default_template_contains_identity(self):
        from agent.system_prompt_manager import DEFAULT_TEMPLATE
        assert "云枢" in DEFAULT_TEMPLATE

    def test_get_template_returns_string(self):
        from agent.system_prompt_manager import get_template
        template = get_template()
        assert isinstance(template, str)
        assert len(template) > 0

    def test_save_and_read_template(self, tmp_path):
        from agent.system_prompt_manager import save_template, get_template, reset_template, SYSTEM_PROMPT_FILE
        # 使用 patch 指向临时文件
        test_file = str(tmp_path / "system_prompt.txt")
        with patch("agent.system_prompt_manager.SYSTEM_PROMPT_FILE", test_file):
            custom_content = "custom template content"
            assert save_template(custom_content) is True
            assert get_template() == custom_content
            assert reset_template() is True
            # 重置后应返回默认模板
            assert get_template() != custom_content

    def test_save_template_creates_directory(self, tmp_path):
        from agent.system_prompt_manager import save_template
        test_file = str(tmp_path / "subdir" / "system_prompt.txt")
        with patch("agent.system_prompt_manager.SYSTEM_PROMPT_FILE", test_file):
            assert save_template("test") is True
            assert os.path.exists(test_file)

    def test_has_custom_template_false_by_default(self, tmp_path):
        from agent.system_prompt_manager import has_custom_template
        test_file = str(tmp_path / "nonexistent_prompt.txt")
        with patch("agent.system_prompt_manager.SYSTEM_PROMPT_FILE", test_file):
            assert has_custom_template() is False

    def test_has_custom_template_true_after_save(self, tmp_path):
        from agent.system_prompt_manager import has_custom_template, save_template
        test_file = str(tmp_path / "custom_prompt.txt")
        with patch("agent.system_prompt_manager.SYSTEM_PROMPT_FILE", test_file):
            save_template("custom content")
            assert has_custom_template() is True

    def test_get_placeholder_descriptions(self):
        from agent.system_prompt_manager import get_placeholder_descriptions
        descriptions = get_placeholder_descriptions()
        assert isinstance(descriptions, dict)
        assert "current_date" in descriptions
        assert "body_status" in descriptions
        assert "mode_name" in descriptions
        assert "tool_status" in descriptions

    def test_reset_template_nonexistent_file(self, tmp_path):
        from agent.system_prompt_manager import reset_template
        test_file = str(tmp_path / "nonexistent.txt")
        with patch("agent.system_prompt_manager.SYSTEM_PROMPT_FILE", test_file):
            # 不应抛出异常
            assert reset_template() is True

    def test_get_template_empty_file_returns_default(self, tmp_path):
        from agent.system_prompt_manager import get_template, DEFAULT_TEMPLATE
        test_file = tmp_path / "empty_prompt.txt"
        test_file.write_text("", encoding="utf-8")
        with patch("agent.system_prompt_manager.SYSTEM_PROMPT_FILE", str(test_file)):
            result = get_template()
            assert result == DEFAULT_TEMPLATE


# ============================================================================
# SafetyGuard 测试
# ============================================================================

class TestSafetyGuardInit:
    """测试 SafetyGuard 初始化"""

    def test_init_with_default_keywords(self):
        from agent.safety_guard import SafetyGuard
        guard = SafetyGuard()
        assert guard._keywords is not None
        assert "critical" in guard._keywords
        assert "warning" in guard._keywords

    def test_init_with_custom_keywords_path(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        keywords_file = tmp_path / "keywords.json"
        keywords_file.write_text(json.dumps({
            "critical": [{"pattern": "rm -rf", "description": "dangerous", "category": "delete"}],
            "warning": [{"pattern": "sudo", "description": "privilege", "category": "privilege"}],
        }), encoding="utf-8")
        guard = SafetyGuard(keywords_path=str(keywords_file))
        assert len(guard._keywords["critical"]) == 1
        assert len(guard._keywords["warning"]) == 1

    def test_init_with_nonexistent_keywords_file(self):
        from agent.safety_guard import SafetyGuard
        guard = SafetyGuard(keywords_path="/nonexistent/path/keywords.json")
        # 应使用空列表作为后备
        assert guard._keywords["critical"] == []
        assert guard._keywords["warning"] == []


class TestSafetyGuardCheck:
    """测试 SafetyGuard.check 方法"""

    def test_check_empty_text_returns_safe(self):
        from agent.safety_guard import SafetyGuard
        guard = SafetyGuard()
        result = guard.check("")
        assert result["safe"] is True
        assert result["level"] == "safe"
        assert result["matches"] == []

    def test_check_none_text_returns_safe(self):
        from agent.safety_guard import SafetyGuard
        guard = SafetyGuard()
        result = guard.check(None)
        assert result["safe"] is True

    def test_check_safe_text(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        keywords_file = tmp_path / "keywords.json"
        keywords_file.write_text(json.dumps({
            "critical": [{"pattern": "rm -rf", "description": "danger", "category": "delete"}],
            "warning": [{"pattern": "sudo", "description": "privilege", "category": "priv"}],
        }), encoding="utf-8")
        guard = SafetyGuard(keywords_path=str(keywords_file))
        result = guard.check("hello world")
        assert result["safe"] is True

    def test_check_critical_pattern(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        keywords_file = tmp_path / "keywords.json"
        keywords_file.write_text(json.dumps({
            "critical": [{"pattern": "rm -rf", "description": "danger", "category": "delete"}],
            "warning": [],
        }), encoding="utf-8")
        guard = SafetyGuard(keywords_path=str(keywords_file))
        result = guard.check("rm -rf /")
        assert result["safe"] is False
        assert result["level"] == "critical"
        assert len(result["matches"]) == 1

    def test_check_warning_pattern(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        keywords_file = tmp_path / "keywords.json"
        keywords_file.write_text(json.dumps({
            "critical": [],
            "warning": [{"pattern": "sudo", "description": "privilege", "category": "priv"}],
        }), encoding="utf-8")
        guard = SafetyGuard(keywords_path=str(keywords_file))
        result = guard.check("sudo apt install")
        assert result["safe"] is False
        assert result["level"] == "warning"

    def test_check_critical_takes_precedence(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        keywords_file = tmp_path / "keywords.json"
        keywords_file.write_text(json.dumps({
            "critical": [{"pattern": "rm -rf", "description": "danger", "category": "delete"}],
            "warning": [{"pattern": "sudo", "description": "priv", "category": "priv"}],
        }), encoding="utf-8")
        guard = SafetyGuard(keywords_path=str(keywords_file))
        result = guard.check("sudo rm -rf /")
        assert result["level"] == "critical"

    def test_check_invalid_regex_pattern(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        keywords_file = tmp_path / "keywords.json"
        keywords_file.write_text(json.dumps({
            "critical": [{"pattern": "[invalid", "description": "bad", "category": "test"}],
            "warning": [],
        }), encoding="utf-8")
        guard = SafetyGuard(keywords_path=str(keywords_file))
        # 不应抛出异常，无效正则应被跳过
        result = guard.check("test text")
        assert result["safe"] is True

    def test_check_case_insensitive(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        keywords_file = tmp_path / "keywords.json"
        keywords_file.write_text(json.dumps({
            "critical": [{"pattern": "rm -rf", "description": "danger", "category": "delete"}],
            "warning": [],
        }), encoding="utf-8")
        guard = SafetyGuard(keywords_path=str(keywords_file))
        result = guard.check("RM -RF /")
        assert result["safe"] is False


class TestSafetyGuardAlerts:
    """测试 SafetyGuard 告警记录"""

    def test_alert_history_records_dangerous_text(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        keywords_file = tmp_path / "keywords.json"
        keywords_file.write_text(json.dumps({
            "critical": [{"pattern": "rm -rf", "description": "danger", "category": "delete"}],
            "warning": [],
        }), encoding="utf-8")
        guard = SafetyGuard(keywords_path=str(keywords_file))
        guard.check("rm -rf /")
        alerts = guard.get_alerts()
        assert len(alerts) == 1
        assert alerts[0]["level"] == "critical"

    def test_alert_history_limit(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        keywords_file = tmp_path / "keywords.json"
        keywords_file.write_text(json.dumps({
            "critical": [{"pattern": "rm -rf", "description": "danger", "category": "delete"}],
            "warning": [],
        }), encoding="utf-8")
        guard = SafetyGuard(keywords_path=str(keywords_file))
        guard._max_alerts = 5
        for _ in range(10):
            guard.check("rm -rf /")
        alerts = guard.get_alerts()
        assert len(alerts) <= 5

    def test_get_alerts_with_limit(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        keywords_file = tmp_path / "keywords.json"
        keywords_file.write_text(json.dumps({
            "critical": [{"pattern": "rm -rf", "description": "danger", "category": "delete"}],
            "warning": [],
        }), encoding="utf-8")
        guard = SafetyGuard(keywords_path=str(keywords_file))
        for _ in range(10):
            guard.check("rm -rf /")
        alerts = guard.get_alerts(limit=3)
        assert len(alerts) == 3

    def test_stats_after_checks(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        keywords_file = tmp_path / "keywords.json"
        keywords_file.write_text(json.dumps({
            "critical": [{"pattern": "rm -rf", "description": "danger", "category": "delete"}],
            "warning": [{"pattern": "sudo", "description": "priv", "category": "priv"}],
        }), encoding="utf-8")
        guard = SafetyGuard(keywords_path=str(keywords_file))
        guard.check("rm -rf /")
        guard.check("sudo apt")
        guard.check("hello")
        stats = guard.get_stats()
        assert stats["blocked_count"] == 1
        assert stats["warned_count"] == 1
        assert stats["total_alerts"] == 2


class TestSafetyGuardAddKeyword:
    """测试 SafetyGuard.add_keyword 方法"""

    def test_add_warning_keyword(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        guard = SafetyGuard(keywords_path=str(tmp_path / "kw.json"))
        guard._keywords = {"critical": [], "warning": []}
        guard.add_keyword("danger_pattern", "test description", level="warning", category="test")
        assert len(guard._keywords["warning"]) == 1

    def test_add_critical_keyword(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        guard = SafetyGuard(keywords_path=str(tmp_path / "kw.json"))
        guard._keywords = {"critical": [], "warning": []}
        guard.add_keyword("danger_pattern", "test description", level="critical", category="test")
        assert len(guard._keywords["critical"]) == 1

    def test_add_keyword_default_level_is_warning(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        guard = SafetyGuard(keywords_path=str(tmp_path / "kw.json"))
        guard._keywords = {"critical": [], "warning": []}
        guard.add_keyword("pattern", "desc")
        assert len(guard._keywords["warning"]) == 1


class TestSafetyGuardReload:
    """测试 SafetyGuard.reload 方法"""

    def test_reload_keywords(self, tmp_path):
        from agent.safety_guard import SafetyGuard
        keywords_file = tmp_path / "keywords.json"
        keywords_file.write_text(json.dumps({
            "critical": [{"pattern": "rm -rf", "description": "danger", "category": "delete"}],
            "warning": [],
        }), encoding="utf-8")
        guard = SafetyGuard(keywords_path=str(keywords_file))
        # 修改文件
        keywords_file.write_text(json.dumps({
            "critical": [
                {"pattern": "rm -rf", "description": "danger", "category": "delete"},
                {"pattern": "format", "description": "format", "category": "delete"},
            ],
            "warning": [],
        }), encoding="utf-8")
        guard.reload()
        assert len(guard._keywords["critical"]) == 2


class TestSafetyGuardGlobal:
    """测试全局函数"""

    def test_register_alert_callback(self):
        from agent.safety_guard import register_alert_callback, _alert_callbacks
        # 清理之前的回调
        original = list(_alert_callbacks)
        def callback(alert):
            pass
        register_alert_callback(callback)
        assert callback in _alert_callbacks
        # 恢复
        _alert_callbacks.clear()
        _alert_callbacks.extend(original)

    def test_get_safety_guard_singleton(self):
        from agent.safety_guard import get_safety_guard, _safety_guard
        import agent.safety_guard as mod
        mod._safety_guard = None
        guard1 = get_safety_guard()
        guard2 = get_safety_guard()
        assert guard1 is guard2


# ============================================================================
# DiffTools 测试
# ============================================================================

class TestDiffToolsConstants:
    """测试 diff_tools 常量"""

    def test_max_diff_file_size(self):
        from agent.diff_tools import MAX_DIFF_FILE_SIZE
        assert MAX_DIFF_FILE_SIZE == 10 * 1024 * 1024


class TestDiffFiles:
    """测试 diff_files 函数"""

    def test_diff_identical_files(self, tmp_path):
        from agent.diff_tools import diff_files
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("same content\n", encoding="utf-8")
        file2.write_text("same content\n", encoding="utf-8")
        result = diff_files(str(file1), str(file2))
        assert result["ok"] is True
        assert result["changes"] == 0

    def test_diff_different_files(self, tmp_path):
        from agent.diff_tools import diff_files
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("line1\nline2\n", encoding="utf-8")
        file2.write_text("line1\nline3\n", encoding="utf-8")
        result = diff_files(str(file1), str(file2))
        assert result["ok"] is True
        assert result["changes"] > 0
        assert "diff" in result

    def test_diff_nonexistent_file1(self, tmp_path):
        from agent.diff_tools import diff_files
        file2 = tmp_path / "file2.txt"
        file2.write_text("content", encoding="utf-8")
        result = diff_files(str(tmp_path / "nonexistent.txt"), str(file2))
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_diff_nonexistent_file2(self, tmp_path):
        from agent.diff_tools import diff_files
        file1 = tmp_path / "file1.txt"
        file1.write_text("content", encoding="utf-8")
        result = diff_files(str(file1), str(tmp_path / "nonexistent.txt"))
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_diff_with_context_lines(self, tmp_path):
        from agent.diff_tools import diff_files
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
        file2.write_text("a\nb\nX\nd\ne\n", encoding="utf-8")
        result = diff_files(str(file1), str(file2), context_lines=1)
        assert result["ok"] is True

    def test_diff_returns_additions_and_deletions(self, tmp_path):
        from agent.diff_tools import diff_files
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("line1\nline2\n", encoding="utf-8")
        file2.write_text("line1\nmodified\n", encoding="utf-8")
        result = diff_files(str(file1), str(file2))
        assert result["ok"] is True
        assert result["additions"] >= 1
        assert result["deletions"] >= 1

    def test_diff_empty_files(self, tmp_path):
        from agent.diff_tools import diff_files
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("", encoding="utf-8")
        file2.write_text("", encoding="utf-8")
        result = diff_files(str(file1), str(file2))
        assert result["ok"] is True
        assert result["changes"] == 0


# ============================================================================
# 集成测试
# ============================================================================

class TestIntegration:
    """集成测试"""

    def test_cost_tracker_full_lifecycle(self, tmp_path):
        """测试 CostTracker 完整生命周期"""
        from agent.model_router.cost_tracker import CostTracker
        log_path = tmp_path / "cost.jsonl"
        tracker = CostTracker(log_path=str(log_path))

        # 记录多次调用
        tracker.record("gpt-4", 1000, 500, 1000.0, "chat", "trace1")
        tracker.record("gpt-3.5-turbo", 500, 200, 500.0, "summary", "trace2")
        tracker.record("gpt-4o-mini", 2000, 1000, 2000.0, "translate", "trace3")

        summary = tracker.get_summary()
        assert summary["total_calls"] == 3
        assert summary["total_cost_usd"] > 0

        # 重新加载验证持久化
        tracker2 = CostTracker(log_path=str(log_path))
        summary2 = tracker2.get_summary()
        assert summary2["total_calls"] == 3

    def test_safety_guard_full_workflow(self, tmp_path):
        """测试 SafetyGuard 完整工作流"""
        from agent.safety_guard import SafetyGuard
        keywords_file = tmp_path / "keywords.json"
        keywords_file.write_text(json.dumps({
            "critical": [{"pattern": "rm -rf", "description": "danger", "category": "delete"}],
            "warning": [{"pattern": "sudo", "description": "priv", "category": "priv"}],
        }), encoding="utf-8")
        guard = SafetyGuard(keywords_path=str(keywords_file))

        # 检查安全文本
        assert guard.check("hello")["safe"] is True
        # 检查危险文本
        assert guard.check("rm -rf /")["safe"] is False
        # 检查警告文本
        assert guard.check("sudo apt")["level"] == "warning"

        # 动态添加关键词
        guard.add_keyword("format", "format disk", level="critical", category="delete")
        assert guard.check("format c:")["safe"] is False

        # 验证统计
        stats = guard.get_stats()
        assert stats["blocked_count"] >= 2
        assert stats["warned_count"] >= 1
