"""
TaskScheduler 单元测试
测试 agent/task_scheduler.py 的功能
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from agent.task_scheduler import (
    TaskScheduler,
    get_scheduler,
    generate_weekly_report,
    cleanup_old_logs,
)


class TestTaskScheduler:
    """测试任务调度器类"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_init(self):
        """测试调度器初始化"""
        scheduler = TaskScheduler()
        assert scheduler.tasks == []
        assert scheduler.running is False

    @pytest.mark.unit
    @pytest.mark.p3
    def test_add_cron_task(self):
        """测试添加 Cron 任务"""
        scheduler = TaskScheduler()
        func = MagicMock()
        
        scheduler.add_cron_task("test_cron", func, day_of_week=0, hour=9, minute=30)
        
        assert len(scheduler.tasks) == 1
        task = scheduler.tasks[0]
        assert task["name"] == "test_cron"
        assert task["type"] == "cron"
        assert task["day_of_week"] == 0
        assert task["hour"] == 9
        assert task["minute"] == 30

    @pytest.mark.unit
    @pytest.mark.p3
    def test_add_interval_task(self):
        """测试添加间隔任务"""
        scheduler = TaskScheduler()
        func = MagicMock()
        
        scheduler.add_interval_task("test_interval", func, interval_seconds=60)
        
        assert len(scheduler.tasks) == 1
        task = scheduler.tasks[0]
        assert task["name"] == "test_interval"
        assert task["type"] == "interval"
        assert task["interval"] == 60

    @pytest.mark.unit
    @pytest.mark.p3
    def test_should_run_cron_task(self):
        """测试 Cron 任务是否应该运行"""
        scheduler = TaskScheduler()
        now = datetime.now()
        
        task = {
            "name": "test",
            "type": "cron",
            "func": MagicMock(),
            "day_of_week": now.weekday(),
            "hour": now.hour,
            "minute": now.minute,
            "last_run": None
        }
        
        assert scheduler._should_run(task) is True

    @pytest.mark.unit
    @pytest.mark.p3
    def test_should_run_cron_task_wrong_day(self):
        """测试 Cron 任务在错误日期不运行"""
        scheduler = TaskScheduler()
        
        task = {
            "name": "test",
            "type": "cron",
            "func": MagicMock(),
            "day_of_week": 0,  # 周一
            "hour": 9,
            "minute": 0,
            "last_run": None
        }
        
        # 如果今天不是周一，任务不应该运行
        if datetime.now().weekday() != 0:
            assert scheduler._should_run(task) is False

    @pytest.mark.unit
    @pytest.mark.p3
    def test_should_run_cron_task_already_ran(self):
        """测试 Cron 任务今天已运行"""
        scheduler = TaskScheduler()
        now = datetime.now()
        
        task = {
            "name": "test",
            "type": "cron",
            "func": MagicMock(),
            "day_of_week": now.weekday(),
            "hour": now.hour,
            "minute": now.minute,
            "last_run": now  # 今天已运行
        }
        
        assert scheduler._should_run(task) is False

    @pytest.mark.unit
    @pytest.mark.p3
    def test_should_run_interval_task_first_time(self):
        """测试间隔任务首次运行"""
        scheduler = TaskScheduler()
        
        task = {
            "name": "test",
            "type": "interval",
            "func": MagicMock(),
            "interval": 60,
            "last_run": None,
            "next_run": None
        }
        
        assert scheduler._should_run(task) is True

    @pytest.mark.unit
    @pytest.mark.p3
    def test_should_run_interval_task_elapsed(self):
        """测试间隔任务已过间隔时间"""
        scheduler = TaskScheduler()
        now = datetime.now()
        
        task = {
            "name": "test",
            "type": "interval",
            "func": MagicMock(),
            "interval": 60,
            "last_run": now - timedelta(seconds=120),  # 2分钟前运行过
            "next_run": None
        }
        
        assert scheduler._should_run(task) is True

    @pytest.mark.unit
    @pytest.mark.p3
    def test_should_run_interval_task_not_elapsed(self):
        """测试间隔任务未过间隔时间"""
        scheduler = TaskScheduler()
        now = datetime.now()
        
        task = {
            "name": "test",
            "type": "interval",
            "func": MagicMock(),
            "interval": 60,
            "last_run": now - timedelta(seconds=30),  # 30秒前运行过
            "next_run": None
        }
        
        assert scheduler._should_run(task) is False

    @pytest.mark.unit
    @pytest.mark.p3
    def test_run_task_success(self):
        """测试执行任务成功"""
        scheduler = TaskScheduler()
        func = MagicMock()
        
        task = {
            "name": "test",
            "type": "interval",
            "func": func,
            "interval": 60,
            "last_run": None,
            "next_run": None
        }
        
        scheduler.run_task(task)
        
        func.assert_called_once()
        assert task["last_run"] is not None

    @pytest.mark.unit
    @pytest.mark.p3
    def test_run_task_failure(self):
        """测试执行任务失败"""
        scheduler = TaskScheduler()
        func = MagicMock(side_effect=Exception("测试错误"))
        
        task = {
            "name": "test",
            "type": "interval",
            "func": func,
            "interval": 60,
            "last_run": None,
            "next_run": None
        }
        
        # 不应抛出异常，错误已被捕获
        scheduler.run_task(task)
        
        func.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p3
    def test_tick_no_tasks(self):
        """测试没有任务时的 tick"""
        scheduler = TaskScheduler()
        scheduler.tick()  # 不应抛出异常

    @pytest.mark.unit
    @pytest.mark.p3
    def test_tick_with_tasks(self):
        """测试有任务时的 tick"""
        scheduler = TaskScheduler()
        func = MagicMock()
        
        now = datetime.now()
        scheduler.add_cron_task(
            "test", func,
            day_of_week=now.weekday(),
            hour=now.hour,
            minute=now.minute
        )
        
        scheduler.tick()
        func.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p3
    def test_list_tasks(self):
        """测试列出任务"""
        scheduler = TaskScheduler()
        scheduler.add_interval_task("test1", MagicMock(), 60)
        scheduler.add_cron_task("test2", MagicMock(), day_of_week=0, hour=9, minute=0)
        
        tasks = scheduler.list_tasks()
        
        assert len(tasks) == 2
        assert tasks[0]["name"] == "test1"
        assert tasks[1]["name"] == "test2"

    @pytest.mark.unit
    @pytest.mark.p3
    def test_stop(self):
        """测试停止调度器"""
        scheduler = TaskScheduler()
        scheduler.running = True
        
        scheduler.stop()
        
        assert scheduler.running is False


class TestSchedulerSingleton:
    """测试调度器单例"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_get_scheduler_singleton(self):
        """测试获取调度器单例"""
        # 使用 patch 重置全局实例
        with patch('agent.task_scheduler._scheduler', None):
            scheduler1 = get_scheduler()
            scheduler2 = get_scheduler()
            
            assert scheduler1 is scheduler2

    @pytest.mark.unit
    @pytest.mark.p3
    def test_get_scheduler_has_default_tasks(self):
        """测试获取调度器包含默认任务"""
        with patch('agent.task_scheduler._scheduler', None):
            scheduler = get_scheduler()
            
            task_names = [t["name"] for t in scheduler.tasks]
            assert "生成周报" in task_names
            assert "清理旧日志" in task_names


class TestScheduledTasks:
    """测试预定义任务函数"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_generate_weekly_report(self):
        """测试生成周报任务"""
        with patch('agent.weekly_report_generator.run_weekly_report') as mock_report:
            mock_report.return_value = ("report content", ["file1.json", "file2.html"])
            generate_weekly_report()
            mock_report.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p3
    def test_generate_weekly_report_failure(self):
        """测试生成周报失败"""
        with patch('agent.weekly_report_generator.run_weekly_report') as mock_report:
            mock_report.side_effect = Exception("生成失败")
            # 不应抛出异常，错误已被捕获
            generate_weekly_report()

    @pytest.mark.unit
    @pytest.mark.p3
    def test_cleanup_old_logs(self):
        """测试清理旧日志任务"""
        import tempfile
        import os
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试日志文件
            old_file = os.path.join(tmpdir, "blackbox_20240101.jsonl")
            with open(old_file, "w") as f:
                f.write("test")
            
            # 修改文件时间为31天前
            old_time = (datetime.now() - timedelta(days=31)).timestamp()
            os.utime(old_file, (old_time, old_time))
            
            with patch('agent.task_scheduler.Path') as mock_path:
                mock_path.return_value.exists.return_value = True
                mock_path.return_value.glob.return_value = [type('obj', (), {'stat': lambda: type('stat', (), {'st_mtime': old_time})(), 'unlink': MagicMock()})()]
                cleanup_old_logs()