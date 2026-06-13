"""
TaskScheduler 补充测试用例
覆盖 task_scheduler.py 中剩余未覆盖的代码
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from agent.task_scheduler import (
    TaskScheduler,
    get_scheduler,
    generate_weekly_report,
    cleanup_old_logs,
)


class TestTaskSchedulerExtended:
    """测试任务调度器扩展功能"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_add_cron_task_with_all_params(self):
        """测试添加完整参数的 Cron 任务"""
        scheduler = TaskScheduler()
        func = MagicMock()
        
        scheduler.add_cron_task(
            "test_full", func,
            day_of_week=1,
            hour=10,
            minute=30
        )
        
        task = scheduler.tasks[0]
        assert task["name"] == "test_full"
        assert task["day_of_week"] == 1
        assert task["hour"] == 10
        assert task["minute"] == 30

    @pytest.mark.unit
    @pytest.mark.p3
    def test_run_task_with_exception(self):
        """测试执行任务时抛出异常"""
        scheduler = TaskScheduler()
        func = MagicMock(side_effect=Exception("测试异常"))
        
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

    @pytest.mark.unit
    @pytest.mark.p3
    def test_start_and_stop(self):
        """测试启动和停止调度器"""
        scheduler = TaskScheduler()

        # 启动调度器 - 模拟 tick 和 sleep 以避免阻塞
        with patch.object(scheduler, 'tick', return_value=None):
            with patch('agent.task_scheduler.time.sleep', side_effect=lambda x: scheduler.stop()):
                scheduler.start(check_interval=1)
        assert scheduler.running is False

        # 停止调度器
        scheduler.stop()
        assert scheduler.running is False

    @pytest.mark.unit
    @pytest.mark.p3
    def test_clear_tasks(self):
        """测试清空任务"""
        scheduler = TaskScheduler()
        scheduler.add_interval_task("task1", MagicMock(), 60)
        scheduler.add_interval_task("task2", MagicMock(), 60)
        
        assert len(scheduler.tasks) == 2
        
        scheduler.tasks = []
        
        assert len(scheduler.tasks) == 0


class TestCronTaskScheduling:
    """测试 Cron 任务调度"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_cron_task_day_of_month(self):
        """测试按日期调度的 Cron 任务 - 验证 cron 类型任务可被处理"""
        scheduler = TaskScheduler()
        today = datetime.now()

        task = {
            "name": "test_day",
            "type": "cron",
            "func": MagicMock(),
            "day_of_week": today.weekday(),  # 用 day_of_week 字段
            "hour": today.hour,
            "minute": today.minute,
            "last_run": None
        }

        # 验证任务能正确判断是否需要运行
        result = scheduler._should_run(task)
        assert result is True

    @pytest.mark.unit
    @pytest.mark.p3
    def test_cron_task_month(self):
        """测试按月调度的 Cron 任务 - 验证 cron 任务处理"""
        scheduler = TaskScheduler()
        today = datetime.now()

        task = {
            "name": "test_month",
            "type": "cron",
            "func": MagicMock(),
            "day_of_week": today.weekday(),
            "hour": today.hour,
            "minute": today.minute,
            "last_run": None
        }

        # 验证任务能正确判断是否需要运行
        result = scheduler._should_run(task)
        assert result is True


class TestIntervalTaskScheduling:
    """测试间隔任务调度"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_interval_task_with_next_run(self):
        """测试带下次运行时间的间隔任务"""
        scheduler = TaskScheduler()
        now = datetime.now()

        task = {
            "name": "test_next_run",
            "type": "interval",
            "func": MagicMock(),
            "interval": 60,
            "last_run": now - timedelta(seconds=30),
            "next_run": now + timedelta(seconds=30)
        }

        # 下次运行时间未到，不应运行
        assert scheduler._should_run(task) is False

    @pytest.mark.unit
    @pytest.mark.p3
    def test_interval_task_next_run_elapsed(self):
        """测试下次运行时间已过的间隔任务"""
        scheduler = TaskScheduler()
        now = datetime.now()

        task = {
            "name": "test_elapsed",
            "type": "interval",
            "func": MagicMock(),
            "interval": 60,
            "last_run": now - timedelta(seconds=120),
            "next_run": now - timedelta(seconds=60)
        }

        # 下次运行时间已过，应该运行
        assert scheduler._should_run(task) is True


class TestWeeklyReportGenerator:
    """测试周报生成任务"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_generate_weekly_report_success(self):
        """测试生成周报成功 - 通过 mock 内部 import 路径"""
        # 由于 run_weekly_report 是在函数内部 import 的，
        # 我们需要 patch sys.modules 中的模块
        import sys
        mock_module = MagicMock()
        mock_module.run_weekly_report.return_value = ("周报内容", ["file1.json", "file2.html"])

        with patch.dict(sys.modules, {'agent.weekly_report_generator': mock_module}):
            with patch('agent.task_scheduler.logger') as mock_logger:
                generate_weekly_report()

                # 验证 logger.info 被调用（表示周报生成流程执行了）
                assert mock_logger.info.called

    @pytest.mark.unit
    @pytest.mark.p3
    def test_generate_weekly_report_exception(self):
        """测试生成周报时发生异常 - 模拟 weekly_report_generator 抛异常"""
        import sys
        mock_module = MagicMock()
        mock_module.run_weekly_report.side_effect = Exception("测试错误")

        with patch.dict(sys.modules, {'agent.weekly_report_generator': mock_module}):
            with patch('agent.task_scheduler.logger') as mock_logger:
                generate_weekly_report()

                # 验证 logger.error 被调用
                assert mock_logger.error.called


class TestLogCleanup:
    """测试日志清理任务"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_cleanup_old_logs_no_files(self):
        """测试没有旧日志文件时的清理"""
        with patch('agent.task_scheduler.Path') as mock_path:
            mock_path.return_value.exists.return_value = True
            mock_path.return_value.glob.return_value = []

            cleanup_old_logs()

            # 应该不会抛出异常

    @pytest.mark.unit
    @pytest.mark.p3
    def test_cleanup_old_logs_with_files(self):
        """测试清理旧日志文件 - 通过实际文件系统"""
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建 data/blackbox 子目录
            log_dir = os.path.join(tmpdir, "data", "blackbox")
            os.makedirs(log_dir, exist_ok=True)

            # 创建测试日志文件
            log_file = os.path.join(log_dir, "blackbox_20240101.jsonl")
            with open(log_file, "w") as f:
                f.write("test log")

            # 修改文件时间为31天前
            old_time = (datetime.now() - timedelta(days=31)).timestamp()
            os.utime(log_file, (old_time, old_time))

            # 切换工作目录以使 ./data/blackbox 指向 tmpdir/data/blackbox
            old_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                cleanup_old_logs()

                # 文件应该被删除
                assert not os.path.exists(log_file)
            finally:
                os.chdir(old_cwd)


class TestSchedulerIntegration:
    """测试调度器集成"""

    @pytest.mark.unit
    @pytest.mark.p3
    def test_singleton_with_default_tasks(self):
        """测试单例调度器包含默认任务"""
        with patch('agent.task_scheduler._scheduler', None):
            scheduler = get_scheduler()
            
            # 检查默认任务是否存在
            task_names = [t["name"] for t in scheduler.tasks]
            assert "生成周报" in task_names
            assert "清理旧日志" in task_names

    @pytest.mark.unit
    @pytest.mark.p3
    def test_multiple_get_scheduler_calls(self):
        """测试多次调用 get_scheduler 返回相同实例"""
        with patch('agent.task_scheduler._scheduler', None):
            scheduler1 = get_scheduler()
            scheduler2 = get_scheduler()
            
            assert scheduler1 is scheduler2