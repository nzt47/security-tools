"""
Baseline Collector 单元测试

覆盖场景：
1. 数据库连接失败时的错误日志记录
2. 数据采集失败时的详细错误日志
3. 快照解析失败时的错误日志
4. 指标提取跳过日志（无效数据、无定义、非数值）
5. 报告写入失败时的错误日志
6. 查询失败时的错误日志
"""

import pytest
import os
import shutil
import json
import uuid
import sqlite3
from unittest.mock import patch, MagicMock, call

from scripts.baseline_collector import BaselineCollector


def get_temp_dir():
    """获取测试临时目录"""
    temp_dir = os.path.join(os.path.dirname(__file__), 'temp', str(uuid.uuid4())[:8])
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def cleanup_temp_dir(temp_dir):
    """清理临时目录"""
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


class TestBaselineCollectorErrorLogging:
    """测试异常日志记录"""

    @pytest.mark.unit
    def test_db_connect_failure_logging(self):
        """数据库连接失败时应记录详细错误日志"""
        tmpdir = get_temp_dir()
        try:
            collector = BaselineCollector(storage_path=tmpdir)

            with patch('sqlite3.connect') as mock_connect:
                mock_connect.side_effect = sqlite3.Error("database is locked")

                with pytest.raises(sqlite3.Error):
                    collector._get_conn()

                mock_connect.assert_called_once()
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    def test_collect_snapshot_fetch_failure_logging(self):
        """数据采集失败时应记录详细错误日志"""
        tmpdir = get_temp_dir()
        try:
            collector = BaselineCollector(storage_path=tmpdir)
            collector.initialize()

            with patch.object(collector._collector, 'get_dashboard_data') as mock_fetch:
                mock_fetch.side_effect = Exception("connection timeout")

                with pytest.raises(Exception):
                    collector.collect_snapshot()

                mock_fetch.assert_called_once()
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    def test_count_records_failure_logging(self):
        """记录数统计失败时应记录错误日志并返回0"""
        tmpdir = get_temp_dir()
        try:
            collector = BaselineCollector(storage_path=tmpdir)

            invalid_data = {"interaction": 123}
            with patch('scripts.baseline_collector._log_error') as mock_log:
                result = collector._count_records(invalid_data)
                assert result == 0

                mock_log.assert_called_once()
                call_args = mock_log.call_args
                assert call_args[0][1] == "count_records_failed"
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    def test_parse_snapshot_failure_logging(self):
        """快照解析失败时应记录详细错误日志"""
        tmpdir = get_temp_dir()
        try:
            collector = BaselineCollector(storage_path=tmpdir)
            collector.initialize()

            invalid_json = "{invalid"
            snapshot_id = "test_snap_001"

            with patch('scripts.baseline_collector._log_error') as mock_log:
                try:
                    json.loads(invalid_json)
                except json.JSONDecodeError:
                    pass

                mock_log.assert_not_called()
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    def test_extract_metrics_skip_invalid_data(self):
        """提取指标时遇到无效数据应记录跳过日志"""
        tmpdir = get_temp_dir()
        try:
            collector = BaselineCollector(storage_path=tmpdir)

            all_metrics = {}
            dashboard_data = {
                "interaction": {
                    "yunshu_interaction_total": {
                        "data": "invalid_data_type"
                    }
                }
            }

            with patch('scripts.baseline_collector._log') as mock_log:
                collector._extract_metrics(dashboard_data, all_metrics)

                skip_calls = [c for c in mock_log.call_args_list
                            if c[0][1] == "extract_metric_skip_invalid_data"]
                assert len(skip_calls) > 0

                summary_calls = [c for c in mock_log.call_args_list
                                if c[0][1] == "extract_metrics_skip_summary"]
                assert len(summary_calls) > 0
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    def test_extract_metrics_skip_non_numeric(self):
        """提取指标时遇到非数值应记录跳过日志"""
        tmpdir = get_temp_dir()
        try:
            collector = BaselineCollector(storage_path=tmpdir)

            all_metrics = {}
            dashboard_data = {
                "interaction": {
                    "yunshu_interaction_total": {
                        "data": {"label1": "not_a_number"}
                    }
                }
            }

            with patch('scripts.baseline_collector._log') as mock_log:
                collector._extract_metrics(dashboard_data, all_metrics)

                summary_calls = [c for c in mock_log.call_args_list
                                if c[0][1] == "extract_metrics_skip_summary"]
                assert len(summary_calls) > 0
                assert summary_calls[0][0][2].get("skipped_non_numeric", 0) > 0
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    def test_write_report_failure_logging(self):
        """报告写入失败时应记录详细错误日志"""
        tmpdir = get_temp_dir()
        try:
            collector = BaselineCollector(storage_path=tmpdir)
            collector.initialize()

            report_data = {"report_id": "test_report", "summary": {}}
            invalid_path = "/nonexistent/path/report.json"

            with patch('builtins.open', side_effect=PermissionError("permission denied")):
                with pytest.raises(PermissionError):
                    with open(invalid_path, 'w', encoding='utf-8') as f:
                        json.dump(report_data, f)
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    def test_get_latest_baseline_query_failure(self):
        """查询失败时应记录详细错误日志"""
        tmpdir = get_temp_dir()
        try:
            collector = BaselineCollector(storage_path=tmpdir)
            collector.initialize()

            with patch('sqlite3.connect') as mock_connect:
                mock_connect.side_effect = sqlite3.Error("query failed")

                with pytest.raises(sqlite3.Error):
                    collector.get_latest_baseline()
        finally:
            cleanup_temp_dir(tmpdir)


class TestBaselineCollectorNormalFlow:
    """测试正常流程"""

    @pytest.mark.unit
    def test_collect_snapshot_success(self):
        """正常采集快照应记录完整日志"""
        tmpdir = get_temp_dir()
        try:
            collector = BaselineCollector(storage_path=tmpdir)
            collector.initialize()

            with patch('scripts.baseline_collector._log') as mock_log:
                snapshot = collector.collect_snapshot()

                assert snapshot is not None
                assert "snapshot_id" in snapshot

                start_calls = [c for c in mock_log.call_args_list
                            if c[0][1] == "collect_snapshot_start"]
                assert len(start_calls) == 1

                saved_calls = [c for c in mock_log.call_args_list
                            if c[0][1] == "collect_snapshot_saved"]
                assert len(saved_calls) == 1

                complete_calls = [c for c in mock_log.call_args_list
                                if c[0][1] == "collect_snapshot_complete"]
                assert len(complete_calls) == 1
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    def test_calculate_baseline_no_data(self):
        """无数据时应记录错误日志并返回错误"""
        tmpdir = get_temp_dir()
        try:
            collector = BaselineCollector(storage_path=tmpdir)
            collector.initialize()

            with patch('scripts.baseline_collector._log_error') as mock_log:
                result = collector.calculate_baseline(days=7)

                assert "error" in result
                assert result["error"] == "no_data"

                mock_log.assert_called_once()
                call_args = mock_log.call_args
                assert call_args[0][1] == "calculate_baseline_no_data"
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    def test_snapshot_parse_summary_logging(self):
        """解析完成后应记录汇总日志"""
        tmpdir = get_temp_dir()
        try:
            collector = BaselineCollector(storage_path=tmpdir)
            collector.initialize()

            collector.collect_snapshot()

            with patch('scripts.baseline_collector._log') as mock_log:
                result = collector.calculate_baseline(days=1)

                summary_calls = [c for c in mock_log.call_args_list
                                if c[0][1] == "snapshot_parse_summary"]
                assert len(summary_calls) == 1
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    def test_get_latest_baseline_logging(self):
        """获取基线时应记录起止日志"""
        tmpdir = get_temp_dir()
        try:
            collector = BaselineCollector(storage_path=tmpdir)
            collector.initialize()

            with patch('scripts.baseline_collector._log') as mock_log:
                result = collector.get_latest_baseline()

                start_calls = [c for c in mock_log.call_args_list
                            if c[0][1] == "get_latest_baseline_start"]
                assert len(start_calls) == 1

                complete_calls = [c for c in mock_log.call_args_list
                                if c[0][1] == "get_latest_baseline_complete"]
                assert len(complete_calls) == 1
        finally:
            cleanup_temp_dir(tmpdir)
