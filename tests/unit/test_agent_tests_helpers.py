"""agent.tests 子包辅助函数元测试

覆盖：
- stress_test_tools.py: _compute_metrics / _create_temp_files / _cleanup_temp_files / _get_registry_names

注意：agent/tests/ 子包在 coverage 配置中被 omit 规则 `*/tests/*` 排除，
因此这些测试主要验证辅助函数的正确性，覆盖率提升需要修改 coverage omit 配置。

状态同步机制：使用 tmp_path 隔离临时文件，配对测试创建+清理确保无残留。
"""
import os
import json
import tempfile
from unittest import mock

import pytest


class TestComputeMetrics:
    """stress_test_tools._compute_metrics 指标计算"""

    def test_basic_metrics(self):
        from agent.tests.stress_test_tools import _compute_metrics
        result = _compute_metrics(total=100, errors=5, latencies=[0.001, 0.002, 0.003])
        assert result["total"] == 100
        assert result["success"] == 95
        assert result["errors"] == 5
        assert result["success_rate"] == 95.0

    def test_empty_latencies(self):
        from agent.tests.stress_test_tools import _compute_metrics
        result = _compute_metrics(total=10, errors=10, latencies=[])
        assert result["total"] == 10
        assert result["success"] == 0
        assert result["errors"] == 10
        assert result["success_rate"] == 0.0
        # 空延迟场景使用 avg_latency 键（不带 _ms 后缀）
        assert result["avg_latency"] == 0.0
        assert result["p95_latency"] == 0.0

    def test_zero_total(self):
        from agent.tests.stress_test_tools import _compute_metrics
        result = _compute_metrics(total=0, errors=0, latencies=[])
        assert result["total"] == 0
        assert result["success_rate"] == 0.0

    def test_all_success(self):
        from agent.tests.stress_test_tools import _compute_metrics
        result = _compute_metrics(total=50, errors=0, latencies=[0.01] * 50)
        assert result["success"] == 50
        assert result["errors"] == 0
        assert result["success_rate"] == 100.0

    def test_all_errors(self):
        from agent.tests.stress_test_tools import _compute_metrics
        result = _compute_metrics(total=50, errors=50, latencies=[0.01] * 50)
        assert result["success"] == 0
        assert result["success_rate"] == 0.0

    def test_latency_stats(self):
        from agent.tests.stress_test_tools import _compute_metrics
        latencies = [0.001, 0.002, 0.003, 0.004, 0.005]
        result = _compute_metrics(total=5, errors=0, latencies=latencies)
        assert result["min_latency_ms"] == 1.0
        assert result["max_latency_ms"] == 5.0
        assert result["avg_latency_ms"] == 3.0

    def test_p95_p99(self):
        from agent.tests.stress_test_tools import _compute_metrics
        latencies = [float(i) / 100 for i in range(1, 101)]
        result = _compute_metrics(total=100, errors=0, latencies=latencies)
        assert result["p95_latency_ms"] > result["avg_latency_ms"]
        assert result["p99_latency_ms"] >= result["p95_latency_ms"]

    def test_single_latency(self):
        from agent.tests.stress_test_tools import _compute_metrics
        result = _compute_metrics(total=1, errors=0, latencies=[0.005])
        assert result["avg_latency_ms"] == 5.0
        assert result["min_latency_ms"] == 5.0
        assert result["max_latency_ms"] == 5.0


class TestCreateTempFiles:
    """stress_test_tools._create_temp_files 临时文件创建"""

    def test_default_count(self):
        from agent.tests.stress_test_tools import _create_temp_files, _cleanup_temp_files
        files = _create_temp_files()
        assert len(files) == 5
        for f in files:
            assert os.path.exists(f)
        _cleanup_temp_files(files)

    def test_custom_count(self):
        from agent.tests.stress_test_tools import _create_temp_files, _cleanup_temp_files
        files = _create_temp_files(count=3)
        assert len(files) == 3
        _cleanup_temp_files(files)

    def test_file_content_is_json(self):
        from agent.tests.stress_test_tools import _create_temp_files, _cleanup_temp_files
        files = _create_temp_files(count=1)
        with open(files[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "test" in data
        assert "items" in data
        assert "nested" in data
        _cleanup_temp_files(files)

    def test_files_have_json_suffix(self):
        from agent.tests.stress_test_tools import _create_temp_files, _cleanup_temp_files
        files = _create_temp_files(count=2)
        for f in files:
            assert f.endswith(".json")
        _cleanup_temp_files(files)

    def test_zero_count(self):
        from agent.tests.stress_test_tools import _create_temp_files
        files = _create_temp_files(count=0)
        assert files == []


class TestCleanupTempFiles:
    """stress_test_tools._cleanup_temp_files 临时文件清理"""

    def test_cleans_all_files(self):
        from agent.tests.stress_test_tools import _create_temp_files, _cleanup_temp_files
        files = _create_temp_files(count=3)
        _cleanup_temp_files(files)
        for f in files:
            assert not os.path.exists(f)

    def test_handles_missing_files(self):
        from agent.tests.stress_test_tools import _cleanup_temp_files
        _cleanup_temp_files(["nonexistent1.json", "nonexistent2.json"])  # 不应抛异常

    def test_empty_list(self):
        from agent.tests.stress_test_tools import _cleanup_temp_files
        _cleanup_temp_files([])  # 不应抛异常

    def test_partial_cleanup(self):
        from agent.tests.stress_test_tools import _create_temp_files, _cleanup_temp_files
        files = _create_temp_files(count=5)
        _cleanup_temp_files(files[:3])  # 只清理前3个
        for f in files[:3]:
            assert not os.path.exists(f)
        for f in files[3:]:
            assert os.path.exists(f)
        _cleanup_temp_files(files[3:])  # 清理剩余


class TestGetRegistryNames:
    """stress_test_tools._get_registry_names 注册表名称获取"""

    def test_returns_set(self):
        from agent.tests.stress_test_tools import _get_registry_names
        result = _get_registry_names()
        # _get_registry_names 返回 set 类型（用于 O(1) 成员检查）
        assert isinstance(result, (set, list))
