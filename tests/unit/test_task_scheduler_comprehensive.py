"""
TaskScheduler 增强版测试
"""
import pytest
import json
import os
from datetime import datetime
from unittest.mock import MagicMock, patch
from agent.task_scheduler import (
    TaskScheduler,
    get_scheduler,
    perform_heartbeat_check,
    generate_weekly_report,
    cleanup_old_logs,
    TASK_HISTORY_FILE,
    HEARTBEAT_HISTORY_FILE,
)


class TestTaskScheduler:
    """测试增强型 TaskScheduler"""

    @pytest.mark.unit
    def test_initialization(self):
        s = TaskScheduler()
        assert s is not None
        assert s.running is False
        assert len(s.tasks) == 0

    @pytest.mark.unit
    def test_add_cron_task(self):
        s = TaskScheduler()
        def dummy(): pass
        s.add_cron_task("test", dummy, day_of_week=0, hour=9, minute=0)
        assert len(s.tasks) == 1
        assert s.tasks[0]["type"] == "python_func"
        assert "cron" in s.tasks[0]

    @pytest.mark.unit
    def test_add_interval_task(self):
        s = TaskScheduler()
        def dummy(): pass
        s.add_interval_task("test", dummy, 60)
        assert len(s.tasks) == 1
        assert s.tasks[0]["interval"] == 60

    @pytest.mark.unit
    def test_add_command_task(self):
        s = TaskScheduler()
        s.add_command_task("ping", "ping 127.0.0.1", 300, task_id="cmd_001")
        assert len(s.tasks) == 1
        assert s.tasks[0]["type"] == "system_command"
        assert s.tasks[0]["command"] == "ping 127.0.0.1"

    @pytest.mark.unit
    def test_remove_task(self):
        s = TaskScheduler()
        s.add_command_task("test", "echo hi", 60, task_id="cmd_001")
        assert s.remove_task("cmd_001") is True
        assert len(s.tasks) == 0
        assert s.remove_task("nonexistent") is False

    @pytest.mark.unit
    def test_set_task_enabled(self):
        s = TaskScheduler()
        s.add_command_task("test", "echo hi", 60, task_id="cmd_001")
        assert s.set_task_enabled("cmd_001", False) is True
        assert s.tasks[0]["enabled"] is False

    @pytest.mark.unit
    def test_get_task(self):
        s = TaskScheduler()
        s.add_command_task("test", "echo hi", 60, task_id="cmd_001")
        task = s.get_task("cmd_001")
        assert task is not None
        assert task["name"] == "test"

    @pytest.mark.unit
    def test_execute_now(self):
        s = TaskScheduler()
        results = []
        def track():
            results.append("done")
        s.add_interval_task("test", track, 60)
        result = s.execute_now(s.tasks[0]["task_id"])
        assert result is not None
        assert result["status"] == "success"
        assert len(results) == 1

    @pytest.mark.unit
    def test_execute_now_nonexistent(self):
        s = TaskScheduler()
        assert s.execute_now("no_such_id") is None

    @pytest.mark.unit
    def test_list_tasks(self):
        s = TaskScheduler()
        s.add_command_task("test", "echo hi", 60, task_id="cmd_001")
        tasks = s.list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["name"] == "test"
        assert tasks[0]["type"] == "system_command"

    @pytest.mark.unit
    def test_start_daemon_and_stop(self):
        s = TaskScheduler()
        s.start_daemon(check_interval=1)
        assert s.running is True
        assert s._thread is not None
        assert s._thread.is_alive()
        s.stop()
        assert s.running is False

    @pytest.mark.unit
    def test_load_from_json(self, tmp_path):
        s = TaskScheduler()
        json_file = tmp_path / "tasks.json"
        tasks_data = {
            "tasks": [
                {"id": "1", "name": "task1", "command": "echo hello", "interval_sec": 60, "enabled": True},
                {"id": "2", "name": "task2", "command": "ping localhost", "interval_sec": 300, "enabled": False},
            ]
        }
        with open(json_file, "w") as f:
            json.dump(tasks_data, f)
        count = s.load_from_json(str(json_file))
        assert count == 1
        assert len(s.tasks) == 1
        assert s.tasks[0]["name"] == "task1"

    @pytest.mark.unit
    def test_perform_heartbeat_check_no_instance(self):
        result = perform_heartbeat_check(None)
        assert "timestamp" in result
        assert "status" in result
        assert "checks" in result

    @pytest.mark.unit
    def test_history_persistence(self, tmp_path):
        with patch("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "history.jsonl"):
            s = TaskScheduler()
            def dummy(): pass
            s.add_interval_task("test", dummy, 60)
            s.execute_now(s.tasks[0]["task_id"])
            history = s.get_history()
            assert len(history) >= 1
            assert history[0]["name"] == "test"
            assert history[0]["status"] == "success"

    @pytest.mark.unit
    def test_run_task_sets_last_run_even_on_failure(self):
        s = TaskScheduler()
        def failing():
            raise ValueError("oops")
        s.add_interval_task("fail", failing, 60)
        task = s.tasks[0]
        # last_run should be set before execution (so failed tasks don't retry immediately)
        assert task.get("last_run") is None
        s.run_task(task)
        assert task["last_run"] is not None

    @pytest.mark.unit
    def test_tick_with_disabled_task(self):
        s = TaskScheduler()
        def dummy(): pass
        s.add_interval_task("test", dummy, 60)
        s.tasks[0]["enabled"] = False
        # tick should not run disabled tasks
        s.tick()

    @pytest.mark.unit
    def test_generate_weekly_report(self):
        generate_weekly_report()

    @pytest.mark.unit
    def test_cleanup_old_logs(self):
        cleanup_old_logs()

    @pytest.mark.unit
    def test_get_scheduler_singleton(self):
        s1 = get_scheduler()
        s2 = get_scheduler()
        assert s1 is s2
        assert len(s1.tasks) >= 2
