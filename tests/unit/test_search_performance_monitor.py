"""
搜索性能监控模块测试 - 覆盖搜索延迟/成功率统计
"""

import pytest
import json
import time
import tempfile
from unittest.mock import Mock, patch, MagicMock

from agent.search_performance_monitor import (
    SearchPerformanceMonitor,
    get_performance_monitor,
    start_performance_monitor,
    stop_performance_monitor,
    run_manual_performance_check,
    get_performance_monitor_status,
    get_performance_history,
    get_performance_summary,
)


class TestSearchPerformanceMonitor:
    """测试搜索性能监控器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_monitor_init(self):
        """测试监控器初始化"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("agent.monitoring.search.PERFORMANCE_DATA_FILE",
                      f"{tmpdir}/search_performance.json"):
                monitor = SearchPerformanceMonitor(base_url="http://localhost:5678")

                assert monitor.base_url == "http://localhost:5678"
                assert not monitor._running
                assert monitor._interval == 300

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_interval(self):
        """测试设置检测间隔"""
        monitor = SearchPerformanceMonitor()

        monitor.set_interval(60)
        assert monitor._interval == 60

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_status(self):
        """测试获取状态"""
        monitor = SearchPerformanceMonitor()

        status = monitor.get_status()
        assert status['running'] is False
        assert 'check_count' in status
        assert 'history_count' in status

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_recent_history(self):
        """测试获取最近历史记录"""
        monitor = SearchPerformanceMonitor()

        history = monitor.get_recent_history(5)
        assert isinstance(history, list)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_run_manual_check(self):
        """测试手动执行检测（mock 网络请求）"""
        with patch('requests.post') as mock_post, \
             patch('requests.get') as mock_get:

            mock_post.return_value.json.return_value = {'ok': True}
            mock_get.return_value.json.return_value = {
                'ok': True,
                'results': []
            }

            monitor = SearchPerformanceMonitor()
            result = monitor.run_manual_check()

            assert result is not None
            assert 'check_id' in result
            assert 'timestamp' in result

    @pytest.mark.unit
    @pytest.mark.p1
    def test_performance_summary_empty(self, tmp_path):
        """测试空数据时的性能摘要"""
        data_file = tmp_path / "empty_performance.json"
        data_file.write_text('{"history": [], "check_count": 0}', encoding='utf-8')

        with patch("agent.monitoring.search.PERFORMANCE_DATA_FILE", str(data_file)):
            monitor = SearchPerformanceMonitor()

            summary = monitor.get_performance_summary()
            assert summary['status'] == 'no_data'
            assert 'message' in summary

    @pytest.mark.unit
    @pytest.mark.p1
    def test_performance_summary_with_data(self):
        """测试有数据时的性能摘要"""
        monitor = SearchPerformanceMonitor()

        for i in range(10):
            record = {
                'check_id': i + 1,
                'timestamp': time.time(),
                'engines': {
                    'tavily': {
                        'status': 'success' if i % 2 == 0 else 'failed',
                        'elapsed': 0.5 + i * 0.1,
                    }
                },
                'status': 'ok' if i % 2 == 0 else 'warning',
                'errors': []
            }
            monitor._performance_history.append(record)

        summary = monitor.get_performance_summary()
        assert summary['total_checks'] == 10
        assert summary['tavily_success_rate'] == 50.0
        assert summary['tavily_success_count'] == 5
        assert summary['tavily_failed_count'] == 5

    @pytest.mark.unit
    @pytest.mark.p1
    def test_start_stop(self):
        """测试启动和停止监控器"""
        monitor = SearchPerformanceMonitor()

        monitor.start()
        assert monitor._running is True

        monitor.stop()
        assert monitor._running is False

    @pytest.mark.unit
    @pytest.mark.p2
    def test_save_load_performance_data(self, tmp_path):
        """测试保存和加载性能数据"""
        data_file = tmp_path / "search_performance.json"

        with patch("agent.monitoring.search.PERFORMANCE_DATA_FILE", str(data_file)):
            monitor = SearchPerformanceMonitor()

            for i in range(5):
                record = {
                    'check_id': i + 1,
                    'timestamp': time.time(),
                    'engines': {},
                    'status': 'ok',
                    'errors': []
                }
                monitor._performance_history.append(record)

            monitor._check_count = 5
            monitor._save_performance_data()

            assert data_file.exists()

            with open(data_file, 'r', encoding='utf-8') as f:
                saved_data = json.load(f)
                assert saved_data['check_count'] == 5
                assert len(saved_data['history']) == 5

            new_monitor = SearchPerformanceMonitor()
            assert new_monitor._check_count == 5
            assert len(new_monitor._performance_history) == 5

    @pytest.mark.unit
    @pytest.mark.p2
    def test_history_limit(self, tmp_path):
        """测试历史记录限制"""
        data_file = tmp_path / "search_performance.json"

        with patch("agent.monitoring.search.PERFORMANCE_DATA_FILE", str(data_file)):
            monitor = SearchPerformanceMonitor()

            for i in range(150):
                record = {
                    'check_id': i + 1,
                    'timestamp': time.time(),
                    'engines': {},
                    'status': 'ok',
                    'errors': []
                }
                monitor._performance_history.append(record)

            assert len(monitor._performance_history) == 150

            monitor._save_performance_data()

            with open(data_file, 'r', encoding='utf-8') as f:
                saved_data = json.load(f)
                assert len(saved_data['history']) == 100


class TestGlobalFunctions:
    """测试全局函数"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_performance_monitor(self):
        """测试获取全局监控器"""
        global _performance_monitor
        _performance_monitor = None

        monitor = get_performance_monitor()
        assert monitor is not None
        assert isinstance(monitor, SearchPerformanceMonitor)

        monitor2 = get_performance_monitor()
        assert monitor is monitor2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_stop_performance_monitor(self):
        """测试启动和停止全局监控器"""
        global _performance_monitor
        _performance_monitor = None

        status = start_performance_monitor(interval_sec=60)
        assert status['running'] is True

        status = stop_performance_monitor()
        assert status['running'] is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_run_manual_check(self):
        """测试手动检测函数"""
        global _performance_monitor
        _performance_monitor = None

        with patch('requests.post') as mock_post, \
             patch('requests.get') as mock_get:

            mock_post.return_value.json.return_value = {'ok': True}
            mock_get.return_value.json.return_value = {
                'ok': True,
                'results': []
            }

            result = run_manual_performance_check()
            assert result is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_status_and_history(self):
        """测试获取状态和历史记录"""
        global _performance_monitor
        _performance_monitor = None

        status = get_performance_monitor_status()
        assert 'running' in status

        history = get_performance_history(limit=5)
        assert isinstance(history, list)

        summary = get_performance_summary()
        assert isinstance(summary, dict)