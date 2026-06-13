"""
内存优化模块测试
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from agent.memory_optimized import (
    ChromaInitProgress,
    ChromaInitStats,
)


class TestChromaInitProgress:
    """测试 ChromaDB 初始化进度"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_create_progress(self):
        """测试创建进度对象"""
        progress = ChromaInitProgress(
            stage="loading",
            progress=0.5,
            message="Loading data...",
            elapsed_ms=100.0,
        )
        
        assert progress.stage == "loading"
        assert progress.progress == 0.5
        assert progress.message == "Loading data..."

    @pytest.mark.unit
    @pytest.mark.p2
    def test_progress_range(self):
        """测试进度范围"""
        progress = ChromaInitProgress(
            stage="test",
            progress=0.75,
            message="Test",
            elapsed_ms=50.0,
        )
        
        assert 0.0 <= progress.progress <= 1.0


class TestChromaInitStats:
    """测试 ChromaDB 初始化统计"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_init(self):
        """测试初始化"""
        stats = ChromaInitStats()
        
        assert stats.total_inits == 0
        assert stats.successful_inits == 0
        assert stats.failed_inits == 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_record_init_success(self):
        """测试记录成功初始化"""
        stats = ChromaInitStats()
        
        stats.record_init(
            success=True,
            total_time_ms=500.0,
            is_async=False,
        )
        
        assert stats.total_inits == 1
        assert stats.successful_inits == 1
        assert stats.avg_time_ms == 500.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_record_init_failure(self):
        """测试记录失败初始化"""
        stats = ChromaInitStats()
        
        stats.record_init(
            success=False,
            total_time_ms=100.0,
            is_async=False,
        )
        
        assert stats.total_inits == 1
        assert stats.failed_inits == 1

    @pytest.mark.unit
    @pytest.mark.p2
    def test_record_async_init(self):
        """测试记录异步初始化"""
        stats = ChromaInitStats()
        
        stats.record_init(
            success=True,
            total_time_ms=200.0,
            is_async=True,
        )
        
        assert stats.async_inits == 1

    @pytest.mark.unit
    @pytest.mark.p2
    def test_record_with_stage_times(self):
        """测试记录带阶段时间的初始化"""
        stats = ChromaInitStats()
        
        stats.record_init(
            success=True,
            total_time_ms=500.0,
            stage_times={
                "connect": 100.0,
                "load": 300.0,
                "index": 100.0,
            },
        )
        
        assert "connect" in stats.stage_times
        assert stats.stage_times["connect"] == [100.0]

    @pytest.mark.unit
    @pytest.mark.p2
    def test_avg_time_multiple_inits(self):
        """测试多次初始化的平均时间"""
        stats = ChromaInitStats()
        
        stats.record_init(True, 200.0)
        stats.record_init(True, 400.0)
        
        assert stats.avg_time_ms == 300.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_fastest_slowest_time(self):
        """测试最快和最慢时间"""
        stats = ChromaInitStats()
        
        stats.record_init(True, 100.0)
        stats.record_init(True, 500.0)
        stats.record_init(True, 200.0)
        
        assert stats.fastest_time_ms == 100.0
        assert stats.slowest_time_ms == 500.0