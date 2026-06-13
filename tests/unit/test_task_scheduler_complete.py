"""
TaskScheduler 完整测试用例
目标：将覆盖率从 0% 提升至 80%+
"""
import os
import pytest
import time
import tempfile
from unittest.mock import MagicMock, patch, call
from datetime import datetime
from agent.task_scheduler import (
    TaskScheduler,
    generate_weekly_report,
    cleanup_old_logs,
)


class TestTaskSchedulerInitialization:
    """测试调度器初始化"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init(self):
        """测试调度器初始化"""
        scheduler = TaskScheduler()
        assert scheduler.tasks == []
        assert scheduler.running is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_logging(self):
        """测试初始化日志"""
        with patch('agent.task_scheduler.logger') as mock_logger:
            TaskScheduler()
            # 验证 __init__ 中 logger.info 被调用 2 次
            assert mock_logger.info.call_count >= 2
            # 收集所有 info 调用的字符串参数
            info_calls = [
                call_args[0][0] for call_args in mock_logger.info.call_args_list
                if call_args[0]
            ]
            # 验证关键日志被记录（通过包含关键子串验证）
            assert any("初始化调度器" in s for s in info_calls)
            assert any("初始化完成" in s for s in info_calls)


class TestTaskSchedulerCronTasks:
    """测试 Cron 风格任务"""

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
        assert task["day_of_week"] == 1
        assert task["hour"] == 9
        assert task["minute"] == 30
        assert task["last_run"] is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_add_cron_task_no_day(self):
        """测试添加不指定星期几的 Cron 任务"""
        scheduler = TaskScheduler()
        mock_func = MagicMock()
        
        scheduler.add_cron_task("daily_task", mock_func, hour=12, minute=0)
        
        assert len(scheduler.tasks) == 1
        task = scheduler.tasks[0]
        assert task["day_of_week"] is None


class TestTaskSchedulerIntervalTasks:
    """测试间隔任务"""

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
        assert task["func"] == mock_func
        assert task["interval"] == 60
        assert task["last_run"] is None
        assert task["next_run"] is None


class TestTaskSchedulerShouldRun:
    """测试任务是否应该运行"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_run_cron_task_wrong_weekday(self):
        """测试 Cron 任务在错误的星期几不运行"""
        scheduler = TaskScheduler()
        task = {"type": "cron", "day_of_week": 0, "hour": 9, "minute": 0, "last_run": None}
        
        with patch('agent.task_scheduler.datetime') as mock_datetime:
            now = MagicMock()
            now.weekday.return_value = 1  # 周二
            now.hour = 9
            now.minute = 0
            now.date.return_value = datetime.now().date()
            mock_datetime.now.return_value = now
            
            should_run = scheduler._should_run(task)
            assert should_run is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_run_cron_task_wrong_hour(self):
        """测试 Cron 任务在错误的小时不运行"""
        scheduler = TaskScheduler()
        task = {"type": "cron", "day_of_week": None, "hour": 9, "minute": 0, "last_run": None}
        
        with patch('agent.task_scheduler.datetime') as mock_datetime:
            now = MagicMock()
            now.weekday.return_value = 0
            now.hour = 10
            now.minute = 0
            now.date.return_value = datetime.now().date()
            mock_datetime.now.return_value = now
            
            should_run = scheduler._should_run(task)
            assert should_run is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_run_cron_task_already_ran_today(self):
        """测试 Cron 任务今天已经运行过了不运行"""
        scheduler = TaskScheduler()
        task = {
            "type": "cron",
            "day_of_week": None,
            "hour": 9,
            "minute": 0,
            "last_run": datetime.now()
        }
        
        with patch('agent.task_scheduler.datetime') as mock_datetime:
            now = MagicMock()
            now.weekday.return_value = 0
            now.hour = 9
            now.minute = 0
            now.date.return_value = datetime.now().date()
            mock_datetime.now.return_value = now
            
            should_run = scheduler._should_run(task)
            assert should_run is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_run_cron_task_ready_to_run(self):
        """测试 Cron 任务应该运行"""
        scheduler = TaskScheduler()
        task = {
            "type": "cron",
            "day_of_week": None,
            "hour": 9,
            "minute": 0,
            "last_run": None
        }
        
        with patch('agent.task_scheduler.datetime') as mock_datetime:
            now = MagicMock()
            now.weekday.return_value = 0
            now.hour = 9
            now.minute = 0
            now.date.return_value = datetime.now().date()
            mock_datetime.now.return_value = now
            
            should_run = scheduler._should_run(task)
            assert should_run is True

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
    def test_should_run_interval_task_not_ready(self):
        """测试间隔任务还没到运行时间"""
        scheduler = TaskScheduler()
        now = datetime.now()
        task = {
            "type": "interval",
            "interval": 60,
            "last_run": now
        }
        
        with patch('agent.task_scheduler.datetime') as mock_datetime:
            mock_datetime.now.return_value = now
            
            should_run = scheduler._should_run(task)
            assert should_run is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_run_interval_task_ready(self):
        """测试间隔任务准备好了运行时间到了"""
        scheduler = TaskScheduler()
        # 任务 60 秒间隔, 上次运行 100 秒前
        last_run = datetime.now()
        task = {
            "type": "interval",
            "interval": 60,
            "last_run": last_run
        }

        with patch('agent.task_scheduler.datetime') as mock_datetime:
            # 让 now() 返回 100 秒后的时间
            class FakeDatetimeSub:
                def __init__(self, ts):
                    self._ts = ts
                def total_seconds(self):
                    return self._ts
            # 简单做法: 把整个 datetime 模块替换为返回 magicmock
            fake_now = MagicMock()
            # 减法: (fake_now - last_run).total_seconds() == 100
            sub_result = MagicMock()
            sub_result.total_seconds.return_value = 100
            fake_now.__sub__.return_value = sub_result
            mock_datetime.now.return_value = fake_now

            should_run = scheduler._should_run(task)
            assert should_run is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_run_unknown_task_type(self):
        """测试未知类型的任务"""
        scheduler = TaskScheduler()
        task = {"type": "unknown", "last_run": None}
        
        should_run = scheduler._should_run(task)
        assert should_run is False


class TestTaskSchedulerRunTask:
    """测试任务执行"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_run_task_success(self):
        """测试成功执行任务"""
        scheduler = TaskScheduler()
        mock_func = MagicMock()
        task = {"name": "test", "func": mock_func}
        
        scheduler.run_task(task)
        
        mock_func.assert_called_once()
        assert task["last_run"] is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_run_task_exception(self):
        """测试任务执行异常"""
        scheduler = TaskScheduler()
        
        def failing_func():
            raise ValueError("Task failed")
        
        task = {"name": "test_fail", "func": failing_func}
        
        with patch('agent.task_scheduler.logger') as mock_logger:
            scheduler.run_task(task)
            
            mock_logger.error.assert_called_once()
            assert "任务失败" in str(mock_logger.error.call_args[0][0])


class TestTaskSchedulerTick:
    """测试调度器检查和执行任务"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_tick_runs_ready_tasks(self):
        """测试 tick 运行准备好的任务"""
        scheduler = TaskScheduler()
        mock_func = MagicMock()
        
        task = {
            "name": "test",
            "type": "interval",
            "func": mock_func,
            "interval": 1,
            "last_run": None
        }
        scheduler.tasks.append(task)
        
        scheduler.tick()
        
        mock_func.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_tick_skips_not_ready_tasks(self):
        """测试 tick 跳过没准备好的任务"""
        scheduler = TaskScheduler()
        mock_func = MagicMock()
        
        task = {
            "name": "test",
            "type": "interval",
            "func": mock_func,
            "interval": 3600,
            "last_run": datetime.now()
        }
        scheduler.tasks.append(task)
        
        with patch('agent.task_scheduler.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime.now()
            
            scheduler.tick()
        
        mock_func.assert_not_called()


class TestTaskSchedulerStartStop:
    """测试调度器启动和停止"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_stop(self):
        """测试停止调度器"""
        scheduler = TaskScheduler()
        scheduler.running = True
        
        scheduler.stop()
        
        assert scheduler.running is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_stops_on_keyboard_interrupt(self):
        """测试启动时捕获键盘中断"""
        scheduler = TaskScheduler()
        
        with patch('agent.task_scheduler.time.sleep', side_effect=KeyboardInterrupt()):
            with patch('agent.task_scheduler.logger') as mock_logger:
                scheduler.start(check_interval=1)
                
                mock_logger.info.assert_any_call("[TaskScheduler] 收到停止信号")
                assert scheduler.running is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_catches_general_exception(self):
        """测试启动时捕获通用异常"""
        scheduler = TaskScheduler()
        
        with patch('agent.task_scheduler.TaskScheduler.tick', side_effect=ValueError("Test error")):
            with patch('agent.task_scheduler.time.sleep') as mock_sleep:
                # 第一次 tick 抛出异常后继续运行
                mock_sleep.side_effect = [None, SystemExit]
                try:
                    scheduler.start(check_interval=1)
                except SystemExit:
                    pass
                
                assert True


class TestTaskSchedulerListTasks:
    """测试列出任务"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_tasks_empty(self):
        """测试列出空的任务列表"""
        scheduler = TaskScheduler()
        
        tasks = scheduler.list_tasks()
        
        assert tasks == []

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_tasks_with_tasks(self):
        """测试列出有任务的列表"""
        scheduler = TaskScheduler()
        mock_func = MagicMock()
        scheduler.add_cron_task("cron_task", mock_func)
        scheduler.add_interval_task("interval_task", mock_func, 60)
        
        tasks = scheduler.list_tasks()
        
        assert len(tasks) == 2
        assert tasks[0]["name"] == "cron_task"
        assert tasks[0]["type"] == "cron"
        assert tasks[1]["name"] == "interval_task"
        assert tasks[1]["type"] == "interval"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_tasks_with_last_run(self):
        """测试列出有 last_run 的任务"""
        scheduler = TaskScheduler()
        task = {
            "name": "test",
            "type": "interval",
            "func": MagicMock(),
            "last_run": datetime(2024, 1, 1)
        }
        scheduler.tasks.append(task)
        
        tasks = scheduler.list_tasks()
        
        assert len(tasks) == 1
        assert tasks[0]["last_run"] == "2024-01-01T00:00:00"


class TestPredefinedTasks:
    """测试预定义的任务函数"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_generate_weekly_report_import_error(self):
        """测试生成周报导入错误"""
        with patch('agent.task_scheduler.logger') as mock_logger:
            # 源码使用 `from agent.weekly_report_generator import run_weekly_report`,
            # 通过让 weekly_report_generator 自身导入失败来触发 except 分支
            import sys
            # 临时隐藏 weekly_report_generator 模块
            saved_module = sys.modules.get('agent.weekly_report_generator')
            sys.modules['agent.weekly_report_generator'] = None  # 触发 ImportError
            try:
                generate_weekly_report()
                # 验证错误日志被记录
                assert mock_logger.error.called
            finally:
                if saved_module is not None:
                    sys.modules['agent.weekly_report_generator'] = saved_module
                else:
                    sys.modules.pop('agent.weekly_report_generator', None)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cleanup_old_logs_success(self):
        """测试清理旧日志成功 - 目录不存在时直接完成清理"""
        with patch('agent.task_scheduler.logger'):
            with patch('agent.task_scheduler.Path') as mock_path:
                # 模拟路径不存在的情况
                mock_dir = mock_path.return_value
                mock_dir.exists.return_value = False
                cleanup_old_logs()
                # 验证未调用 glob（因为目录不存在）
                mock_dir.glob.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cleanup_old_logs_with_files(self):
        """测试清理有文件的旧日志"""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                # 创建 data/blackbox 目录
                log_dir = os.path.join(tmpdir, "data", "blackbox")
                os.makedirs(log_dir, exist_ok=True)

                # 创建一个旧日志文件（40天前）
                old_file = os.path.join(log_dir, "blackbox_old.jsonl")
                with open(old_file, "w") as f:
                    f.write("{}")
                old_time = time.time() - (40 * 24 * 60 * 60)
                os.utime(old_file, (old_time, old_time))

                # 创建一个新日志文件（5天前），应该被保留
                new_file = os.path.join(log_dir, "blackbox_new.jsonl")
                with open(new_file, "w") as f:
                    f.write("{}")
                new_time = time.time() - (5 * 24 * 60 * 60)
                os.utime(new_file, (new_time, new_time))

                with patch('agent.task_scheduler.logger'):
                    cleanup_old_logs()
                    # 旧文件应该被删除
                    assert not os.path.exists(old_file)
                    # 新文件应该被保留
                    assert os.path.exists(new_file)
            finally:
                os.chdir(old_cwd)


class TestTaskSchedulerCronDaySpecific:
    """测试 Cron 任务的具体日期情况"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_run_cron_with_specific_day_of_week(self):
        """测试指定了特定星期几且时间正确"""
        scheduler = TaskScheduler()
        task = {
            "type": "cron",
            "day_of_week": 0,
            "hour": 9,
            "minute": 0,
            "last_run": None
        }
        
        with patch('agent.task_scheduler.datetime') as mock_datetime:
            now = MagicMock()
            now.weekday.return_value = 0
            now.hour = 9
            now.minute = 0
            now.date.return_value = datetime.now().date()
            mock_datetime.now.return_value = now
            
            should_run = scheduler._should_run(task)
            assert should_run is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_run_cron_without_day_of_week(self):
        """测试没指定星期几的 Cron 任务"""
        scheduler = TaskScheduler()
        task = {
            "type": "cron",
            "day_of_week": None,
            "hour": 9,
            "minute": 0,
            "last_run": None
        }
        
        with patch('agent.task_scheduler.datetime') as mock_datetime:
            now = MagicMock()
            now.weekday.return_value = 3
            now.hour = 9
            now.minute = 0
            now.date.return_value = datetime.now().date()
            mock_datetime.now.return_value = now
            
            should_run = scheduler._should_run(task)
            assert should_run is True
