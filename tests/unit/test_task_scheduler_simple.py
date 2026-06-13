"""
TaskScheduler 完整测试用例
目标：将覆盖率从 0% 提升至 80%+
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from agent.task_scheduler import TaskScheduler


class TestTaskSchedulerSimple:
    """简单的测试用例"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init(self):
        """测试初始化"""
        scheduler = TaskScheduler()
        assert scheduler.tasks == []
        assert scheduler.running is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_add_cron_task(self):
        """测试添加 Cron 任务"""
        scheduler = TaskScheduler()
        mock_func = MagicMock()
        
        scheduler.add_cron_task("test_cron", mock_func, day_of_week=1, hour=9, minute=30)
        
        assert len(scheduler.tasks) == 1
        task = scheduler.tasks[0]
        assert task["name"] == "test_cron"
        assert task["type"] == "cron"
        assert task["func"] == mock_func

    @pytest.mark.unit
    @pytest.mark.p0
    def test_add_interval_task(self):
        """测试添加间隔任务"""
        scheduler = TaskScheduler()
        mock_func = MagicMock()
        
        scheduler.add_interval_task("test_interval", mock_func, 60)
        
        assert len(scheduler.tasks) == 1
        task = scheduler.tasks[0]
        assert task["name"] == "test_interval"
        assert task["type"] == "interval"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_run_interval_task_first_time(self):
        """测试间隔任务第一次运行"""
        scheduler = TaskScheduler()
        task = {"type": "interval", "interval": 60, "last_run": None}
        
        should_run = scheduler._should_run(task)
        assert should_run is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_run_task_success(self):
        """测试成功执行任务"""
        scheduler = TaskScheduler()
        mock_func = MagicMock()
        task = {"name": "test", "func": mock_func}
        
        scheduler.run_task(task)
        
        mock_func.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_tick_empty(self):
        """测试 tick（空任务列表）"""
        scheduler = TaskScheduler()
        scheduler.tick()
        assert True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_stop(self):
        """测试停止"""
        scheduler = TaskScheduler()
        scheduler.running = True
        scheduler.stop()
        assert scheduler.running is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_tasks_empty(self):
        """测试空任务列表"""
        scheduler = TaskScheduler()
        tasks = scheduler.list_tasks()
        assert tasks == []

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_run_unknown_type(self):
        """测试未知类型"""
        scheduler = TaskScheduler()
        task = {"type": "unknown", "last_run": None}
        should_run = scheduler._should_run(task)
        assert should_run is False
