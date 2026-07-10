"""Scheduler 集成测试

覆盖 agent.scheduling 模块：
- Scheduler 初始化
- 生命周期（start/stop/_run_loop）
- 任务管理（add/remove/pause/resume/get_tasks/get_task）
- cron 表达式验证（validate_cron_expr/_validate_cron_field）
- _add_cron_job 5 种 cron 模式 + 降级
- _register_with_schedule/_unregister_from_schedule
- _execute_task 任务执行
- 持久化（save_to_file/load_from_file）
- 历史记录（log_execution/_trim_history/get_history）
- 全局单例 get_schedule_scheduler
"""

import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent.scheduling import (
    Scheduler,
    get_schedule_scheduler,
)

# 检测 schedule 第三方库是否可用（CI 可能未安装该可选依赖）
try:
    import schedule as _schedule_lib  # noqa: F401
    _HAS_SCHEDULE_LIB = True
except ImportError:
    _HAS_SCHEDULE_LIB = False


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def scheduler():
    """新建 Scheduler 实例"""
    s = Scheduler()
    yield s
    if s._running:
        s.stop()


@pytest.fixture
def reset_singleton():
    """重置全局 _schedule_scheduler 单例"""
    import agent.scheduling as module
    old = module._schedule_scheduler
    module._schedule_scheduler = None
    yield
    module._schedule_scheduler = old


@pytest.fixture
def patch_paths(tmp_path, monkeypatch):
    """将模块级文件路径重定向到临时目录"""
    schedules_file = tmp_path / "schedules.json"
    history_file = tmp_path / "schedule_history.jsonl"
    monkeypatch.setattr("agent.scheduling.SCHEDULES_FILE", schedules_file)
    monkeypatch.setattr("agent.scheduling.SCHEDULE_HISTORY_FILE", history_file)
    return {"schedules": schedules_file, "history": history_file}


# ============================================================================
# 初始化测试
# ============================================================================

class TestSchedulerInit:
    def test_init(self, scheduler):
        assert scheduler._running is False
        assert scheduler._thread is None
        assert scheduler._tasks == {}
        assert scheduler._stop_event is not None

    @pytest.mark.skipif(not _HAS_SCHEDULE_LIB, reason="schedule 库未安装")
    def test_init_with_schedule_lib(self, scheduler):
        # schedule 库已安装时 _schedule 不为 None
        assert scheduler._schedule is not None

    def test_init_without_schedule_lib(self):
        with patch.dict("sys.modules", {"schedule": None}):
            with patch("builtins.__import__", side_effect=ImportError("no schedule")):
                s = Scheduler()
                assert s._schedule is None


# ============================================================================
# 生命周期测试
# ============================================================================

class TestLifecycle:
    def test_start(self, scheduler, patch_paths):
        scheduler.start()
        assert scheduler._running is True
        assert scheduler._thread is not None
        assert scheduler._thread.daemon is True
        scheduler.stop()
        scheduler._thread.join(timeout=1)

    def test_start_already_running(self, scheduler, patch_paths):
        scheduler._running = True
        # 已运行时不重复启动
        scheduler.start()
        assert scheduler._thread is None

    def test_stop(self, scheduler, patch_paths):
        scheduler._running = True
        scheduler.stop()
        assert scheduler._running is False
        assert scheduler._stop_event.is_set()

    def test_stop_persists_tasks(self, scheduler, patch_paths):
        scheduler.stop()
        assert patch_paths["schedules"].exists()

    def test_run_loop_exits_on_stop(self, scheduler, patch_paths):
        scheduler._running = True
        scheduler._stop_event.clear()
        # 在短时间后设置 stop_event
        def stop_after_delay():
            time.sleep(0.1)
            scheduler._stop_event.set()
        import threading
        t = threading.Thread(target=stop_after_delay)
        t.start()
        scheduler._run_loop()
        t.join()
        # 循环退出
        assert scheduler._stop_event.is_set()

    def test_run_loop_handles_exception(self, scheduler, patch_paths):
        scheduler._running = True
        scheduler._stop_event.clear()
        mock_schedule = MagicMock()
        call_count = [0]
        def failing_run_pending():
            call_count[0] += 1
            if call_count[0] <= 1:
                raise RuntimeError("schedule error")
            scheduler._stop_event.set()
        mock_schedule.run_pending.side_effect = failing_run_pending
        scheduler._schedule = mock_schedule
        scheduler._run_loop()
        assert call_count[0] >= 2


# ============================================================================
# 任务管理测试
# ============================================================================

class TestTaskManagement:
    def test_add_task_interval(self, scheduler, patch_paths):
        result = scheduler.add_task("测试任务", interval_minutes=30)
        assert result["ok"] is True
        assert "task" in result
        assert result["task"]["name"] == "测试任务"
        assert result["task"]["interval_minutes"] == 30
        assert result["task"]["id"].startswith("task_")

    def test_add_task_cron(self, scheduler, patch_paths):
        result = scheduler.add_task("cron任务", cron_expr="*/5 * * * *")
        assert result["ok"] is True
        assert result["task"]["cron_expr"] == "*/5 * * * *"

    def test_add_task_empty_name(self, scheduler, patch_paths):
        result = scheduler.add_task("", interval_minutes=30)
        assert result["ok"] is False
        assert "空" in result["error"]

    def test_add_task_no_interval_or_cron(self, scheduler, patch_paths):
        result = scheduler.add_task("任务")
        assert result["ok"] is False
        assert "interval_minutes 或 cron_expr" in result["error"]

    def test_add_task_with_params(self, scheduler, patch_paths):
        result = scheduler.add_task("任务", action="run_shell_command",
                                     params={"cmd": "ls"}, interval_minutes=10)
        assert result["ok"] is True
        assert result["task"]["action"] == "run_shell_command"
        assert result["task"]["params"] == {"cmd": "ls"}

    def test_add_task_disabled(self, scheduler, patch_paths):
        result = scheduler.add_task("任务", interval_minutes=10, enabled=False)
        assert result["ok"] is True
        assert result["task"]["enabled"] is False

    def test_remove_task(self, scheduler, patch_paths):
        result = scheduler.add_task("任务", interval_minutes=10)
        task_id = result["task"]["id"]
        remove_result = scheduler.remove_task(task_id)
        assert remove_result["ok"] is True
        assert remove_result["cancelled"] is True
        assert scheduler.get_task(task_id) is None

    def test_remove_task_not_found(self, scheduler, patch_paths):
        result = scheduler.remove_task("nonexistent")
        assert result["ok"] is False
        assert "不存在" in result["error"]

    def test_pause_task(self, scheduler, patch_paths):
        result = scheduler.add_task("任务", interval_minutes=10)
        task_id = result["task"]["id"]
        pause_result = scheduler.pause_task(task_id)
        assert pause_result["ok"] is True
        assert pause_result["paused"] is True
        task = scheduler.get_task(task_id)
        assert task["paused"] is True

    def test_pause_task_not_found(self, scheduler, patch_paths):
        result = scheduler.pause_task("nonexistent")
        assert result["ok"] is False

    def test_resume_task(self, scheduler, patch_paths):
        result = scheduler.add_task("任务", interval_minutes=10)
        task_id = result["task"]["id"]
        scheduler.pause_task(task_id)
        resume_result = scheduler.resume_task(task_id)
        assert resume_result["ok"] is True
        assert resume_result["resumed"] is True
        task = scheduler.get_task(task_id)
        assert task["paused"] is False

    def test_resume_task_not_found(self, scheduler, patch_paths):
        result = scheduler.resume_task("nonexistent")
        assert result["ok"] is False

    def test_get_tasks(self, scheduler, patch_paths):
        scheduler.add_task("t1", interval_minutes=10)
        scheduler.add_task("t2", interval_minutes=20)
        result = scheduler.get_tasks()
        assert result["ok"] is True
        assert result["total"] == 2
        assert len(result["tasks"]) == 2

    def test_get_tasks_empty(self, scheduler, patch_paths):
        result = scheduler.get_tasks()
        assert result["ok"] is True
        assert result["total"] == 0

    def test_get_task(self, scheduler, patch_paths):
        result = scheduler.add_task("t1", interval_minutes=10)
        task_id = result["task"]["id"]
        task = scheduler.get_task(task_id)
        assert task is not None
        assert task["name"] == "t1"

    def test_get_task_not_found(self, scheduler, patch_paths):
        assert scheduler.get_task("nonexistent") is None


# ============================================================================
# cron 表达式验证测试
# ============================================================================

class TestCronValidation:
    def test_valid_wildcard(self):
        assert Scheduler.validate_cron_expr("* * * * *") is True

    def test_valid_interval(self):
        assert Scheduler.validate_cron_expr("*/5 * * * *") is True

    def test_valid_fixed_values(self):
        assert Scheduler.validate_cron_expr("30 9 * * *") is True

    def test_valid_all_fixed(self):
        assert Scheduler.validate_cron_expr("30 9 1 1 0") is True

    def test_valid_comma_list(self):
        assert Scheduler.validate_cron_expr("0,15,30,45 * * * *") is True

    def test_invalid_empty(self):
        assert Scheduler.validate_cron_expr("") is False

    def test_invalid_whitespace(self):
        assert Scheduler.validate_cron_expr("   ") is False

    def test_invalid_field_count(self):
        assert Scheduler.validate_cron_expr("* * *") is False
        assert Scheduler.validate_cron_expr("* * * * * *") is False

    def test_invalid_minute_range(self):
        assert Scheduler.validate_cron_expr("60 * * * *") is False

    def test_invalid_hour_range(self):
        assert Scheduler.validate_cron_expr("* 24 * * *") is False

    def test_invalid_day_range(self):
        assert Scheduler.validate_cron_expr("* * 0 * *") is False

    def test_invalid_month_range(self):
        assert Scheduler.validate_cron_expr("* * * 13 *") is False

    def test_invalid_weekday_range(self):
        assert Scheduler.validate_cron_expr("* * * * 7") is False

    def test_invalid_interval_value(self):
        assert Scheduler.validate_cron_expr("*/0 * * * *") is False

    def test_valid_field_star(self):
        assert Scheduler._validate_cron_field("*", 0, 59) is True

    def test_valid_field_interval(self):
        assert Scheduler._validate_cron_field("*/5", 0, 59) is True

    def test_invalid_field_interval_value(self):
        assert Scheduler._validate_cron_field("*/60", 0, 59) is False

    def test_valid_field_number(self):
        assert Scheduler._validate_cron_field("30", 0, 59) is True

    def test_invalid_field_number_out_of_range(self):
        assert Scheduler._validate_cron_field("70", 0, 59) is False

    def test_valid_field_comma_list(self):
        assert Scheduler._validate_cron_field("0,15,30", 0, 59) is True

    def test_invalid_field_comma_list(self):
        assert Scheduler._validate_cron_field("0,99,30", 0, 59) is False

    def test_invalid_field_non_numeric(self):
        assert Scheduler._validate_cron_field("abc", 0, 59) is False


# ============================================================================
# _add_cron_job 测试
# ============================================================================

class TestAddCronJob:
    def test_interval_minutes(self, scheduler):
        scheduler._schedule = MagicMock()
        scheduler._add_cron_job("task_1", "*/5 * * * *")
        scheduler._schedule.every.assert_called_with(5)

    def test_daily_fixed_time(self, scheduler):
        scheduler._schedule = MagicMock()
        mock_every = scheduler._schedule.every.return_value
        scheduler._add_cron_job("task_1", "30 9 * * *")
        scheduler._schedule.every.assert_called_once()
        mock_every.day.at.assert_called_with("09:30")

    def test_hourly_fixed_minute(self, scheduler):
        scheduler._schedule = MagicMock()
        mock_every = scheduler._schedule.every.return_value
        scheduler._add_cron_job("task_1", "30 * * * *")
        mock_every.hour.at.assert_called_with(":30")

    def test_weekly_fixed_day(self, scheduler):
        scheduler._schedule = MagicMock()
        mock_every = scheduler._schedule.every.return_value
        scheduler._add_cron_job("task_1", "0 9 * * 1")  # 周一 9:00
        mock_every.monday.at.assert_called_with("09:00")

    def test_monthly_fixed_day(self, scheduler):
        scheduler._schedule = MagicMock()
        mock_every = scheduler._schedule.every.return_value
        scheduler._add_cron_job("task_1", "0 9 15 * *")  # 每月 15 日 9:00
        mock_every.day.at.assert_called_with("09:00")

    def test_unsupported_pattern_fallback(self, scheduler):
        scheduler._schedule = MagicMock()
        scheduler._add_cron_job("task_1", "1,2,3 1,2 1,2 1,2 1,2")
        scheduler._schedule.every.assert_called_with(60)

    def test_invalid_field_count(self, scheduler):
        scheduler._schedule = MagicMock()
        with pytest.raises(ValueError, match="5 个字段"):
            scheduler._add_cron_job("task_1", "* * *")


# ============================================================================
# _register_with_schedule 测试
# ============================================================================

class TestRegisterWithSchedule:
    def test_register_paused_task(self, scheduler):
        scheduler._schedule = MagicMock()
        task = {"id": "t1", "paused": True, "enabled": True, "interval_minutes": 10, "cron_expr": ""}
        scheduler._register_with_schedule(task)
        scheduler._schedule.every.assert_not_called()

    def test_register_disabled_task(self, scheduler):
        scheduler._schedule = MagicMock()
        task = {"id": "t1", "paused": False, "enabled": False, "interval_minutes": 10, "cron_expr": ""}
        scheduler._register_with_schedule(task)
        scheduler._schedule.every.assert_not_called()

    def test_register_no_schedule_lib(self, scheduler):
        scheduler._schedule = None
        task = {"id": "t1", "paused": False, "enabled": True, "interval_minutes": 10, "cron_expr": ""}
        # 无 schedule 库时不报错
        scheduler._register_with_schedule(task)

    def test_register_interval_task(self, scheduler):
        scheduler._schedule = MagicMock()
        task = {"id": "t1", "paused": False, "enabled": True, "interval_minutes": 10, "cron_expr": ""}
        scheduler._register_with_schedule(task)
        scheduler._schedule.every.assert_called_with(10)

    def test_register_no_interval_no_cron(self, scheduler):
        scheduler._schedule = MagicMock()
        task = {"id": "t1", "paused": False, "enabled": True, "interval_minutes": 0, "cron_expr": ""}
        scheduler._register_with_schedule(task)
        scheduler._schedule.every.assert_not_called()

    def test_register_exception_handled(self, scheduler):
        scheduler._schedule = MagicMock()
        scheduler._schedule.every.side_effect = Exception("schedule error")
        task = {"id": "t1", "paused": False, "enabled": True, "interval_minutes": 10, "cron_expr": ""}
        # 异常被捕获，不抛出
        scheduler._register_with_schedule(task)


# ============================================================================
# _unregister_from_schedule 测试
# ============================================================================

class TestUnregisterFromSchedule:
    def test_unregister(self, scheduler):
        scheduler._schedule = MagicMock()
        scheduler._unregister_from_schedule("task_1")
        scheduler._schedule.clear.assert_called_with(tag="task_1")

    def test_unregister_no_schedule_lib(self, scheduler):
        scheduler._schedule = None
        # 无 schedule 库时不报错
        scheduler._unregister_from_schedule("task_1")

    def test_unregister_exception_handled(self, scheduler):
        scheduler._schedule = MagicMock()
        scheduler._schedule.clear.side_effect = Exception("clear error")
        # 异常被捕获
        scheduler._unregister_from_schedule("task_1")


# ============================================================================
# _execute_task 测试
# ============================================================================

class TestExecuteTask:
    def test_execute_task(self, scheduler, patch_paths):
        result = scheduler.add_task("t1", interval_minutes=10)
        task_id = result["task"]["id"]
        scheduler._execute_task(task_id)
        task = scheduler.get_task(task_id)
        assert task["last_run"] is not None
        assert task["run_count"] == 1

    def test_execute_task_not_found(self, scheduler, patch_paths):
        # 任务不存在时不报错
        scheduler._execute_task("nonexistent")

    def test_execute_increments_run_count(self, scheduler, patch_paths):
        result = scheduler.add_task("t1", interval_minutes=10)
        task_id = result["task"]["id"]
        scheduler._execute_task(task_id)
        scheduler._execute_task(task_id)
        scheduler._execute_task(task_id)
        task = scheduler.get_task(task_id)
        assert task["run_count"] == 3

    def test_execute_logs_history(self, scheduler, patch_paths):
        result = scheduler.add_task("t1", interval_minutes=10)
        task_id = result["task"]["id"]
        scheduler._execute_task(task_id)
        assert patch_paths["history"].exists()
        lines = patch_paths["history"].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["task_id"] == task_id
        assert record["success"] is True


# ============================================================================
# 持久化测试
# ============================================================================

class TestPersistence:
    def test_save_to_file(self, scheduler, patch_paths):
        scheduler.add_task("t1", interval_minutes=10)
        scheduler.save_to_file()
        assert patch_paths["schedules"].exists()
        data = json.loads(patch_paths["schedules"].read_text(encoding="utf-8"))
        assert "tasks" in data
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["name"] == "t1"

    def test_save_to_file_empty(self, scheduler, patch_paths):
        scheduler.save_to_file()
        data = json.loads(patch_paths["schedules"].read_text(encoding="utf-8"))
        assert data["tasks"] == []

    def test_load_from_file(self, scheduler, patch_paths):
        tasks_data = {
            "updated_at": "2026-01-01T00:00:00+00:00",
            "tasks": [
                {
                    "id": "task_abc123",
                    "name": "loaded_task",
                    "interval_minutes": 15,
                    "cron_expr": "",
                    "action": "test",
                    "params": {},
                    "enabled": True,
                    "paused": False,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "last_run": None,
                    "run_count": 0,
                }
            ]
        }
        patch_paths["schedules"].write_text(json.dumps(tasks_data), encoding="utf-8")
        scheduler.load_from_file()
        task = scheduler.get_task("task_abc123")
        assert task is not None
        assert task["name"] == "loaded_task"

    def test_load_from_file_no_file(self, scheduler, patch_paths):
        scheduler.load_from_file()
        assert scheduler._tasks == {}

    def test_load_from_file_invalid_json(self, scheduler, patch_paths):
        patch_paths["schedules"].write_text("invalid json", encoding="utf-8")
        scheduler.load_from_file()
        # 无效 JSON 不崩溃
        assert scheduler._tasks == {}

    def test_load_skips_paused_registration(self, scheduler, patch_paths):
        scheduler._schedule = MagicMock()
        tasks_data = {
            "updated_at": "2026-01-01T00:00:00+00:00",
            "tasks": [
                {
                    "id": "task_paused",
                    "name": "paused_task",
                    "interval_minutes": 15,
                    "enabled": True,
                    "paused": True,
                }
            ]
        }
        patch_paths["schedules"].write_text(json.dumps(tasks_data), encoding="utf-8")
        scheduler.load_from_file()
        # 暂停任务不注册到 schedule
        scheduler._schedule.every.assert_not_called()

    def test_load_skips_disabled_registration(self, scheduler, patch_paths):
        scheduler._schedule = MagicMock()
        tasks_data = {
            "tasks": [
                {"id": "t1", "name": "x", "interval_minutes": 10, "enabled": False, "paused": False}
            ]
        }
        patch_paths["schedules"].write_text(json.dumps(tasks_data), encoding="utf-8")
        scheduler.load_from_file()
        scheduler._schedule.every.assert_not_called()

    def test_round_trip_save_load(self, scheduler, patch_paths):
        scheduler.add_task("t1", interval_minutes=10, action="run", params={"k": "v"})
        scheduler.save_to_file()

        new_scheduler = Scheduler()
        new_scheduler.load_from_file()
        tasks = new_scheduler.get_tasks()
        assert tasks["total"] == 1
        assert tasks["tasks"][0]["name"] == "t1"
        assert tasks["tasks"][0]["action"] == "run"


# ============================================================================
# 历史记录测试
# ============================================================================

class TestHistory:
    def test_log_execution(self, scheduler, patch_paths):
        scheduler.log_execution("task_1", True, "成功")
        assert patch_paths["history"].exists()
        lines = patch_paths["history"].read_text(encoding="utf-8").strip().split("\n")
        record = json.loads(lines[0])
        assert record["task_id"] == "task_1"
        assert record["success"] is True
        assert record["result"] == "成功"

    def test_log_execution_truncates_result(self, scheduler, patch_paths):
        long_result = "x" * 1000
        scheduler.log_execution("task_1", True, long_result)
        lines = patch_paths["history"].read_text(encoding="utf-8").strip().split("\n")
        record = json.loads(lines[0])
        assert len(record["result"]) <= 500

    def test_get_history_empty(self, scheduler, patch_paths):
        result = scheduler.get_history()
        assert result["ok"] is True
        assert result["history"] == []
        assert result["total"] == 0

    def test_get_history(self, scheduler, patch_paths):
        for i in range(3):
            scheduler.log_execution(f"task_{i}", True, f"结果{i}")
        result = scheduler.get_history()
        assert result["ok"] is True
        assert result["total"] == 3
        # 最新在前
        assert result["history"][0]["task_id"] == "task_2"

    def test_get_history_with_limit(self, scheduler, patch_paths):
        for i in range(5):
            scheduler.log_execution(f"task_{i}", True, "")
        result = scheduler.get_history(limit=2)
        assert len(result["history"]) == 2
        assert result["total"] == 5

    def test_get_history_with_offset(self, scheduler, patch_paths):
        for i in range(5):
            scheduler.log_execution(f"task_{i}", True, "")
        result = scheduler.get_history(limit=2, offset=1)
        assert len(result["history"]) == 2
        assert result["history"][0]["task_id"] == "task_3"

    def test_get_history_skips_invalid_json(self, scheduler, patch_paths):
        with open(patch_paths["history"], "w", encoding="utf-8") as f:
            f.write(json.dumps({"task_id": "t1"}) + "\n")
            f.write("invalid json\n")
            f.write(json.dumps({"task_id": "t2"}) + "\n")
        result = scheduler.get_history()
        assert result["total"] == 2

    def test_trim_history(self, scheduler, patch_paths):
        with open(patch_paths["history"], "w", encoding="utf-8") as f:
            for i in range(5):
                f.write(json.dumps({"idx": i}) + "\n")
        scheduler._trim_history(max_lines=2)
        lines = patch_paths["history"].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    def test_trim_history_no_file(self, scheduler, patch_paths):
        scheduler._trim_history()
        assert not patch_paths["history"].exists()

    def test_trim_history_under_limit(self, scheduler, patch_paths):
        with open(patch_paths["history"], "w", encoding="utf-8") as f:
            f.write(json.dumps({"idx": 0}) + "\n")
        scheduler._trim_history(max_lines=10)
        lines = patch_paths["history"].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1


# ============================================================================
# _task_to_dict 测试
# ============================================================================

class TestTaskToDict:
    def test_task_to_dict(self):
        task = {
            "id": "t1", "name": "test", "interval_minutes": 10,
            "cron_expr": "", "action": "run", "params": {"k": "v"},
            "enabled": True, "paused": False,
            "created_at": "2026-01-01", "last_run": None, "run_count": 5,
        }
        result = Scheduler._task_to_dict(task)
        assert result["id"] == "t1"
        assert result["name"] == "test"
        assert result["interval_minutes"] == 10
        assert result["run_count"] == 5

    def test_task_to_dict_defaults(self):
        result = Scheduler._task_to_dict({})
        assert result["id"] == ""
        assert result["name"] == ""
        assert result["interval_minutes"] == 0
        assert result["enabled"] is True
        assert result["paused"] is False
        assert result["run_count"] == 0


# ============================================================================
# 全局单例测试
# ============================================================================

class TestGlobalSingleton:
    def test_get_schedule_scheduler(self, reset_singleton):
        s = get_schedule_scheduler()
        assert isinstance(s, Scheduler)

    def test_singleton(self, reset_singleton):
        s1 = get_schedule_scheduler()
        s2 = get_schedule_scheduler()
        assert s1 is s2
