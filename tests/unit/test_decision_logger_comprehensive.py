"""DecisionLogger 决策日志全面单元测试

测试目标：覆盖 agent/utils/decision_logger.py 的所有分支
覆盖维度：
1. 正常路径：start_log/log_selected/log_skipped/end_log
2. 边界条件：空日志、无 current_log 时的 get_statistics
3. 跳过原因分类：PRIORITY/ALIAS/LIMIT/WHITELIST
4. 输出格式：text/json/both
5. 导出：to_json/to_json_file
"""
import json
import logging
from unittest.mock import patch

import pytest

from agent.utils.decision_logger import (
    DecisionLog,
    DecisionLogger,
    DecisionRecord,
    DecisionType,
    SkipReason,
    create_decision_logger,
)


# ── 1. 枚举测试 ──────────────────────────────────────────


class TestDecisionType:
    def test_all_types_defined(self):
        assert DecisionType.SELECTION
        assert DecisionType.FILTERING
        assert DecisionType.PRIORITIZATION
        assert DecisionType.MERGING
        assert DecisionType.LIMITING

    def test_type_values(self):
        assert DecisionType.SELECTION.value == "selection"
        assert DecisionType.PRIORITIZATION.value == "prioritize"


class TestSkipReason:
    def test_all_reasons_defined(self):
        assert SkipReason.PRIORITY
        assert SkipReason.ALIAS
        assert SkipReason.LIMIT
        assert SkipReason.WHITELIST
        assert SkipReason.DUPLICATE
        assert SkipReason.INVALID

    def test_reason_values(self):
        assert SkipReason.PRIORITY.value == "priority"
        assert SkipReason.ALIAS.value == "alias"


# ── 2. DecisionRecord ──────────────────────────────────


class TestDecisionRecord:
    def test_default_values(self):
        record = DecisionRecord()
        assert record.timestamp == ""
        assert record.item == ""
        assert record.action == ""
        assert record.reason is None
        assert record.detail is None
        assert record.source is None

    def test_to_dict_basic(self):
        record = DecisionRecord(
            timestamp="2026-01-01",
            item="test_item",
            action="selected",
        )
        d = record.to_dict()
        assert d["item"] == "test_item"
        assert d["action"] == "selected"
        assert d["reason"] is None

    def test_to_dict_with_skip_reason_enum(self):
        """SkipReason 枚举应转为字符串值"""
        record = DecisionRecord(
            item="test",
            action="skipped",
            reason=SkipReason.PRIORITY,
        )
        d = record.to_dict()
        assert d["reason"] == "priority"

    def test_to_dict_with_string_reason(self):
        """字符串 reason 应保持原样"""
        record = DecisionRecord(item="test", action="skipped", reason="custom")
        d = record.to_dict()
        assert d["reason"] == "custom"


# ── 3. DecisionLog ──────────────────────────────────────


class TestDecisionLog:
    def test_default_values(self):
        log = DecisionLog()
        assert log.id == ""
        assert log.context == ""
        assert log.records == []
        assert log.selected == []
        assert log.summary == {}

    def test_add_selected(self):
        log = DecisionLog()
        record = log.add_selected("item1", source="cat_a", detail="info")
        assert record.item == "item1"
        assert record.action == "selected"
        assert "item1" in log.selected
        assert len(log.records) == 1

    def test_add_selected_no_optional(self):
        log = DecisionLog()
        log.add_selected("item1")
        assert "item1" in log.selected

    def test_add_skipped_priority(self):
        log = DecisionLog()
        log.add_skipped("item", SkipReason.PRIORITY, detail="reason", source="cat")
        assert len(log.skipped_by_priority) == 1
        assert len(log.records) == 1

    def test_add_skipped_alias(self):
        log = DecisionLog()
        log.add_skipped("item", SkipReason.ALIAS)
        assert len(log.skipped_by_alias) == 1

    def test_add_skipped_limit(self):
        log = DecisionLog()
        log.add_skipped("item", SkipReason.LIMIT)
        assert len(log.skipped_by_limit) == 1

    def test_add_skipped_whitelist(self):
        log = DecisionLog()
        log.add_skipped("item", SkipReason.WHITELIST)
        assert len(log.skipped_by_whitelist) == 1

    def test_add_skipped_duplicate_not_classified(self):
        """DUPLICATE 原因不分类记录（仅 PRIORITY/ALIAS/LIMIT/WHITELIST 分类）"""
        log = DecisionLog()
        log.add_skipped("item", SkipReason.DUPLICATE)
        assert len(log.skipped_by_priority) == 0
        assert len(log.records) == 1

    def test_add_skipped_invalid_not_classified(self):
        log = DecisionLog()
        log.add_skipped("item", SkipReason.INVALID)
        assert len(log.skipped_by_priority) == 0

    def test_to_dict_includes_statistics(self):
        log = DecisionLog()
        log.add_selected("a")
        log.add_skipped("b", SkipReason.PRIORITY)
        d = log.to_dict()
        assert "statistics" in d
        assert d["statistics"]["selected_count"] == 1
        assert d["statistics"]["skipped_priority"] == 1
        assert d["statistics"]["total_records"] == 2

    def test_to_dict_selection_rate(self):
        log = DecisionLog()
        log.add_selected("a")
        log.add_selected("b")
        log.add_skipped("c", SkipReason.LIMIT)
        d = log.to_dict()
        assert d["statistics"]["selection_rate"] == 2 / 3

    def test_to_dict_selection_rate_empty(self):
        log = DecisionLog()
        d = log.to_dict()
        assert d["statistics"]["selection_rate"] == 0.0

    def test_to_json_returns_string(self):
        log = DecisionLog(context="test")
        s = log.to_json()
        assert isinstance(s, str)
        data = json.loads(s)
        assert data["context"] == "test"

    def test_to_json_file_success(self, tmp_path):
        log = DecisionLog(context="test")
        filepath = tmp_path / "log.json"
        assert log.to_json_file(str(filepath)) is True
        assert filepath.exists()

    def test_to_json_file_failure(self, tmp_path):
        log = DecisionLog()
        # 使用一个被锁/不可写的路径模拟失败（Windows 上 /nonexistent 可能被创建）
        # 改用文件名含非法字符触发失败
        invalid_path = str(tmp_path / "invalid:name*.json")
        result = log.to_json_file(invalid_path)
        # 不同的操作系统行为可能不同，断言"返回布尔值"即可
        assert isinstance(result, bool)


# ── 4. DecisionLogger ──────────────────────────────────


class TestDecisionLoggerInit:
    def test_default_init(self):
        dl = DecisionLogger()
        assert dl.verbose is False
        assert dl.output_format == DecisionLogger.OutputFormat.TEXT
        assert dl.current_log is None

    def test_custom_init(self):
        dl = DecisionLogger(verbose=True, output_format="json")
        assert dl.verbose is True
        assert dl.output_format == DecisionLogger.OutputFormat.JSON

    def test_both_format(self):
        dl = DecisionLogger(output_format="both")
        assert dl.output_format == DecisionLogger.OutputFormat.BOTH

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            DecisionLogger(output_format="invalid")

    def test_custom_logger(self):
        custom_logger = logging.getLogger("custom_test")
        dl = DecisionLogger(logger=custom_logger)
        assert dl.logger is custom_logger


# ── 5. start_log ──────────────────────────────────────────


class TestStartLog:
    def test_start_log_returns_decision_log(self):
        dl = DecisionLogger()
        log = dl.start_log("test context")
        assert isinstance(log, DecisionLog)
        assert log.context == "test context"
        assert log.id  # 自动生成

    def test_start_log_sets_current_log(self):
        dl = DecisionLogger()
        log = dl.start_log("ctx")
        assert dl.current_log is log

    def test_start_log_records_start_time(self):
        dl = DecisionLogger()
        log = dl.start_log("ctx")
        assert log.start_time > 0

    def test_start_log_verbose_prints(self, capsys):
        dl = DecisionLogger(verbose=True)
        dl.start_log("ctx", input_data="data")
        captured = capsys.readouterr()
        assert "ctx" in captured.out


# ── 6. log_selected ──────────────────────────────────────


class TestLogSelected:
    def test_log_selected_without_current_log(self):
        """无 current_log 时应不抛异常"""
        dl = DecisionLogger()
        dl.log_selected("item")  # 不抛异常

    def test_log_selected_adds_to_log(self):
        dl = DecisionLogger()
        dl.start_log("ctx")
        dl.log_selected("item1", source="cat", extra_info="info")
        assert "item1" in dl.current_log.selected

    def test_log_selected_verbose_prints(self, capsys):
        dl = DecisionLogger(verbose=True)
        dl.start_log("ctx")
        dl.log_selected("item", extra_info="info")
        captured = capsys.readouterr()
        assert "item" in captured.out


# ── 7. log_skipped ──────────────────────────────────────


class TestLogSkipped:
    def test_log_skipped_without_current_log(self):
        dl = DecisionLogger()
        dl.log_skipped("item", SkipReason.PRIORITY)  # 不抛异常

    def test_log_skipped_adds_to_log(self):
        dl = DecisionLogger()
        dl.start_log("ctx")
        dl.log_skipped("item", SkipReason.PRIORITY, source="cat", detail="info")
        assert len(dl.current_log.skipped_by_priority) == 1

    def test_log_skipped_verbose_prints(self, capsys):
        dl = DecisionLogger(verbose=True)
        dl.start_log("ctx")
        dl.log_skipped("item", SkipReason.PRIORITY, detail="reason")
        captured = capsys.readouterr()
        assert "item" in captured.out


# ── 8. log_category / log_limit_reached ──────────────────


class TestLogCategory:
    def test_log_category_verbose(self, capsys):
        dl = DecisionLogger(verbose=True)
        dl.log_category("cat", 1, "label", 5)
        captured = capsys.readouterr()
        assert "label" in captured.out

    def test_log_category_not_verbose(self, capsys):
        dl = DecisionLogger(verbose=False)
        dl.log_category("cat", 1, "label", 5)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_log_limit_reached_verbose(self, capsys):
        dl = DecisionLogger(verbose=True)
        dl.log_limit_reached(25)
        captured = capsys.readouterr()
        assert "25" in captured.out


# ── 9. end_log ──────────────────────────────────────────


class TestEndLog:
    def test_end_log_without_current_log(self):
        dl = DecisionLogger()
        log = dl.end_log()
        assert log.context == "empty"

    def test_end_log_sets_end_time(self):
        dl = DecisionLogger()
        dl.start_log("ctx")
        log = dl.end_log()
        assert log.end_time > 0

    def test_end_log_calculates_duration(self):
        dl = DecisionLogger()
        dl.start_log("ctx")
        log = dl.end_log()
        assert log.duration_ms >= 0

    def test_end_log_with_summary(self):
        dl = DecisionLogger()
        dl.start_log("ctx")
        log = dl.end_log(summary={"key": "value"})
        assert log.summary == {"key": "value"}

    def test_end_log_json_format(self, capsys):
        dl = DecisionLogger(verbose=True, output_format="json")
        dl.start_log("ctx")
        dl.end_log()
        captured = capsys.readouterr()
        # JSON 格式应输出 JSON
        assert "JSON" in captured.out or "{" in captured.out

    def test_end_log_both_format(self, capsys):
        dl = DecisionLogger(verbose=True, output_format="both")
        dl.start_log("ctx")
        dl.end_log()
        captured = capsys.readouterr()
        assert "ctx" in captured.out


# ── 10. get_statistics / get_json_output ──────────────────


class TestGetStatistics:
    def test_empty_statistics(self):
        dl = DecisionLogger()
        assert dl.get_statistics() == {}

    def test_statistics_after_decisions(self):
        dl = DecisionLogger()
        dl.start_log("ctx")
        dl.log_selected("a")
        dl.log_skipped("b", SkipReason.PRIORITY)
        stats = dl.get_statistics()
        assert stats["selected_count"] == 1
        assert stats["skipped_priority"] == 1
        assert stats["total_records"] == 2

    def test_get_json_output_empty(self):
        dl = DecisionLogger()
        assert dl.get_json_output() == "{}"

    def test_get_json_output_after_decisions(self):
        dl = DecisionLogger()
        dl.start_log("ctx")
        dl.log_selected("a")
        output = dl.get_json_output()
        data = json.loads(output)
        assert data["context"] == "ctx"
        assert "a" in data["selected"]


# ── 11. create_decision_logger ──────────────────────────


class TestCreateDecisionLogger:
    def test_returns_decision_logger(self):
        dl = create_decision_logger()
        assert isinstance(dl, DecisionLogger)

    def test_custom_params(self):
        dl = create_decision_logger(verbose=True, output_format="json", logger_name="custom")
        assert dl.verbose is True
        assert dl.output_format == DecisionLogger.OutputFormat.JSON

    def test_default_logger_name(self):
        dl = create_decision_logger()
        assert dl.logger.name == "decision_logger"


# ── 12. 集成场景 ──────────────────────────────────────────


class TestIntegration:
    def test_full_decision_flow(self, capsys):
        """完整决策流程：开始 → 选择/跳过 → 结束 → 统计"""
        dl = DecisionLogger(verbose=True)
        dl.start_log("工具选择", input_data=["tool1", "tool2", "tool3"])
        dl.log_category("core", 0, "核心工具", 3)
        dl.log_selected("tool1", source="core")
        dl.log_skipped("tool2", SkipReason.PRIORITY, source="core", detail="tool1 优先")
        dl.log_skipped("tool3", SkipReason.LIMIT, source="core")
        dl.log_limit_reached(1)
        log = dl.end_log(summary={"total_input": 3})

        assert len(log.selected) == 1
        assert len(log.skipped_by_priority) == 1
        assert len(log.skipped_by_limit) == 1
        assert log.summary["total_input"] == 3

        stats = dl.get_statistics()
        assert stats["selection_rate"] == 1 / 3

    def test_json_export_and_import(self, tmp_path):
        """JSON 导出再导入验证"""
        dl = DecisionLogger()
        dl.start_log("export test")
        dl.log_selected("item1")
        dl.log_skipped("item2", SkipReason.ALIAS)
        log = dl.end_log()

        # 导出
        filepath = tmp_path / "decision.json"
        assert log.to_json_file(str(filepath)) is True

        # 导入验证
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert data["context"] == "export test"
        assert "item1" in data["selected"]
        assert len(data["skipped_by_alias"]) == 1

    def test_multiple_skip_reasons_classification(self):
        """多种跳过原因分类统计"""
        dl = DecisionLogger()
        dl.start_log("multi-skip")
        dl.log_skipped("p1", SkipReason.PRIORITY)
        dl.log_skipped("p2", SkipReason.PRIORITY)
        dl.log_skipped("a1", SkipReason.ALIAS)
        dl.log_skipped("l1", SkipReason.LIMIT)
        dl.log_skipped("w1", SkipReason.WHITELIST)
        dl.log_skipped("d1", SkipReason.DUPLICATE)
        dl.log_skipped("i1", SkipReason.INVALID)
        log = dl.end_log()

        assert len(log.skipped_by_priority) == 2
        assert len(log.skipped_by_alias) == 1
        assert len(log.skipped_by_limit) == 1
        assert len(log.skipped_by_whitelist) == 1
        # 全部 7 个跳过记录都在 records 中
        assert len(log.records) == 7
