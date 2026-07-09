"""TaskScheduler 集成测试

覆盖 agent.task_scheduler 模块：
- TaskScheduler 初始化
- 添加任务（cron/interval/command）
- 任务管理（remove/enable/get）
- _should_run 判定逻辑（cron/interval/system_command/heartbeat）
- run_task 执行（python_func/system_command/heartbeat）
- 历史记录（append/trim/get）
- 心跳持久化（save/get）
- load_from_json 加载
- 调度器生命周期（start_daemon/tick/execute_now/stop）
- list_tasks 序列化
- 全局单例 get_scheduler
- 预定义任务函数（generate_weekly_report/cleanup_old_logs）
- perform_heartbeat_check 心跳检测
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, mock_open

import pytest

from agent.task_scheduler import (
    TaskScheduler,
    get_scheduler,
    generate_weekly_report,
    cleanup_old_logs,
    perform_heartbeat_check,
    DEFAULT_CHECK_INTERVAL,
    COMMAND_TIMEOUT,
    MAX_HISTORY_LINES,
    HEARTBEAT_INTERVAL,
    MAX_HEARTBEAT_HISTORY,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def scheduler():
    """新建 TaskScheduler 实例（无全局污染）"""
    return TaskScheduler()


@pytest.fixture
def reset_scheduler_singleton():
    """重置全局 _scheduler 单例"""
    import agent.task_scheduler as module
    old = module._scheduler
    module._scheduler = None
    yield
    module._scheduler = old


@pytest.fixture
def patch_scheduler_config():
    """统一 mock observability_config 中的 scheduler 配置函数

    使用 side_effect=lambda 动态读取 config dict，测试中可修改 config 值实时生效。
    """
    config = {
        "check_interval": 10,
        "command_timeout": 300,
        "max_history_lines": 1000,
        "heartbeat_interval": 60,
        "max_heartbeat_history": 1440,
    }
    patches = [
        patch("agent.monitoring.observability_config.get_scheduler_check_interval", side_effect=lambda: config["check_interval"]),
        patch("agent.monitoring.observability_config.get_scheduler_command_timeout", side_effect=lambda: config["command_timeout"]),
        patch("agent.monitoring.observability_config.get_scheduler_max_history_lines", side_effect=lambda: config["max_history_lines"]),
        patch("agent.monitoring.observability_config.get_scheduler_heartbeat_interval", side_effect=lambda: config["heartbeat_interval"]),
        patch("agent.monitoring.observability_config.get_scheduler_max_heartbeat_history", side_effect=lambda: config["max_heartbeat_history"]),
    ]
    for p in patches:
        p.start()
    yield config
    for p in patches:
        p.stop()


@pytest.fixture
def patch_paths(tmp_path, monkeypatch):
    """将模块级文件路径重定向到临时目录，避免污染 data/"""
    history_file = tmp_path / "task_history.jsonl"
    heartbeat_file = tmp_path / "heartbeat_history.json"
    tasks_file = tmp_path / "scheduled_tasks.json"
    monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", history_file)
    monkeypatch.setattr("agent.task_scheduler.HEARTBEAT_HISTORY_FILE", heartbeat_file)
    monkeypatch.setattr("agent.task_scheduler.SCHEDULED_TASKS_FILE", tasks_file)
    return {
        "history": history_file,
        "heartbeat": heartbeat_file,
        "tasks": tasks_file,
    }


# ============================================================================
# 常量测试
# ============================================================================

class TestConstants:
    def test_default_constants(self):
        assert DEFAULT_CHECK_INTERVAL == 10
        assert COMMAND_TIMEOUT == 300
        assert MAX_HISTORY_LINES == 1000
        assert HEARTBEAT_INTERVAL == 60
        assert MAX_HEARTBEAT_HISTORY == 1440


# ============================================================================
# TaskScheduler 初始化测试
# ============================================================================

class TestTaskSchedulerInit:
    def test_init(self, scheduler):
        assert scheduler.tasks == []
        assert scheduler.running is False
        assert scheduler._thread is None
        assert scheduler._heartbeat_func is None
        assert scheduler._yunshu_ref is None

    def test_init_logger(self, scheduler, caplog):
        assert scheduler.tasks == []


# ============================================================================
# 添加任务测试
# ============================================================================

class TestAddTasks:
    def test_add_cron_task(self, scheduler):
        func = lambda: None
        scheduler.add_cron_task("周报", func, day_of_week=0, hour=9, minute=0)
        assert len(scheduler.tasks) == 1
        task = scheduler.tasks[0]
        assert task["name"] == "周报"
        assert task["type"] == "python_func"
        assert task["func"] is func
        assert task["cron"] == {"day_of_week": 0, "hour": 9, "minute": 0}
        assert task["last_run"] is None
        assert task["enabled"] is True
        assert task["task_id"].startswith("py_")

    def test_add_cron_task_defaults(self, scheduler):
        scheduler.add_cron_task("任务", lambda: None)
        task = scheduler.tasks[0]
        assert task["cron"] == {"day_of_week": None, "hour": 0, "minute": 0}

    def test_add_interval_task(self, scheduler):
        func = lambda: None
        scheduler.add_interval_task("定时", func, interval_seconds=60)
        assert len(scheduler.tasks) == 1
        task = scheduler.tasks[0]
        assert task["name"] == "定时"
        assert task["type"] == "python_func"
        assert task["interval"] == 60
        assert task["task_id"].startswith("py_")

    def test_add_command_task(self, scheduler):
        scheduler.add_command_task("cmd任务", "echo hello", interval_sec=30)
        assert len(scheduler.tasks) == 1
        task = scheduler.tasks[0]
        assert task["name"] == "cmd任务"
        assert task["type"] == "system_command"
        assert task["command"] == "echo hello"
        assert task["interval"] == 30
        assert task["enabled"] is True
        assert task["task_id"].startswith("cmd_")

    def test_add_command_task_custom_id(self, scheduler):
        scheduler.add_command_task("cmd", "ls", interval_sec=60, task_id="custom_id_123")
        assert scheduler.tasks[0]["task_id"] == "custom_id_123"

    def test_add_command_task_disabled(self, scheduler):
        scheduler.add_command_task("cmd", "ls", interval_sec=60, enabled=False)
        assert scheduler.tasks[0]["enabled"] is False

    def test_generate_task_id_unique(self, scheduler):
        scheduler.add_interval_task("t1", lambda: None, interval_seconds=10)
        scheduler.add_interval_task("t2", lambda: None, interval_seconds=10)
        ids = [t["task_id"] for t in scheduler.tasks]
        assert len(ids) == 2
        assert ids[0] != ids[1]

    def test_generate_task_id_prefix(self, scheduler):
        scheduler.add_interval_task("t", lambda: None, interval_seconds=10)
        scheduler.add_command_task("c", "ls", interval_sec=10)
        assert scheduler.tasks[0]["task_id"].startswith("py_")
        assert scheduler.tasks[1]["task_id"].startswith("cmd_")


# ============================================================================
# 任务管理测试
# ============================================================================

class TestTaskManagement:
    def test_remove_task(self, scheduler):
        scheduler.add_command_task("t1", "ls", interval_sec=60, task_id="id1")
        scheduler.add_command_task("t2", "ls", interval_sec=60, task_id="id2")
        assert scheduler.remove_task("id1") is True
        assert len(scheduler.tasks) == 1
        assert scheduler.tasks[0]["task_id"] == "id2"

    def test_remove_task_not_found(self, scheduler):
        scheduler.add_command_task("t1", "ls", interval_sec=60, task_id="id1")
        assert scheduler.remove_task("nonexistent") is False
        assert len(scheduler.tasks) == 1

    def test_set_task_enabled(self, scheduler):
        scheduler.add_command_task("t1", "ls", interval_sec=60, task_id="id1")
        assert scheduler.set_task_enabled("id1", False) is True
        assert scheduler.tasks[0]["enabled"] is False
        assert scheduler.set_task_enabled("id1", True) is True
        assert scheduler.tasks[0]["enabled"] is True

    def test_set_task_enabled_not_found(self, scheduler):
        assert scheduler.set_task_enabled("nonexistent", True) is False

    def test_get_task(self, scheduler):
        scheduler.add_command_task("t1", "ls", interval_sec=60, task_id="id1")
        task = scheduler.get_task("id1")
        assert task is not None
        assert task["name"] == "t1"

    def test_get_task_not_found(self, scheduler):
        assert scheduler.get_task("nonexistent") is None


# ============================================================================
# _should_run 测试
# ============================================================================

class TestShouldRun:
    def test_disabled_task(self, scheduler):
        scheduler.add_interval_task("t", lambda: None, interval_seconds=60)
        scheduler.tasks[0]["enabled"] = False
        assert scheduler._should_run(scheduler.tasks[0]) is False

    def test_cron_task_matching(self, scheduler):
        now = datetime.now()
        scheduler.add_cron_task("t", lambda: None, day_of_week=now.weekday(), hour=now.hour, minute=now.minute)
        assert scheduler._should_run(scheduler.tasks[0]) is True

    def test_cron_task_weekday_mismatch(self, scheduler):
        now = datetime.now()
        wrong_day = (now.weekday() + 1) % 7
        scheduler.add_cron_task("t", lambda: None, day_of_week=wrong_day, hour=now.hour, minute=now.minute)
        assert scheduler._should_run(scheduler.tasks[0]) is False

    def test_cron_task_hour_mismatch(self, scheduler):
        now = datetime.now()
        wrong_hour = (now.hour + 1) % 24
        scheduler.add_cron_task("t", lambda: None, day_of_week=now.weekday(), hour=wrong_hour, minute=now.minute)
        assert scheduler._should_run(scheduler.tasks[0]) is False

    def test_cron_task_minute_mismatch(self, scheduler):
        now = datetime.now()
        wrong_minute = (now.minute + 1) % 60
        scheduler.add_cron_task("t", lambda: None, day_of_week=now.weekday(), hour=now.hour, minute=wrong_minute)
        assert scheduler._should_run(scheduler.tasks[0]) is False

    def test_cron_task_already_run_today(self, scheduler):
        now = datetime.now()
        scheduler.add_cron_task("t", lambda: None, day_of_week=now.weekday(), hour=now.hour, minute=now.minute)
        scheduler.tasks[0]["last_run"] = now
        assert scheduler._should_run(scheduler.tasks[0]) is False

    def test_cron_task_no_weekday_constraint(self, scheduler):
        now = datetime.now()
        scheduler.add_cron_task("t", lambda: None, day_of_week=None, hour=now.hour, minute=now.minute)
        assert scheduler._should_run(scheduler.tasks[0]) is True

    def test_interval_task_first_run(self, scheduler):
        scheduler.add_interval_task("t", lambda: None, interval_seconds=60)
        assert scheduler._should_run(scheduler.tasks[0]) is True

    def test_interval_task_not_elapsed(self, scheduler):
        scheduler.add_interval_task("t", lambda: None, interval_seconds=60)
        scheduler.tasks[0]["last_run"] = datetime.now()
        assert scheduler._should_run(scheduler.tasks[0]) is False

    def test_interval_task_elapsed(self, scheduler):
        scheduler.add_interval_task("t", lambda: None, interval_seconds=60)
        scheduler.tasks[0]["last_run"] = datetime.now() - timedelta(seconds=61)
        assert scheduler._should_run(scheduler.tasks[0]) is True

    def test_system_command_first_run(self, scheduler):
        scheduler.add_command_task("t", "ls", interval_sec=60)
        assert scheduler._should_run(scheduler.tasks[0]) is True

    def test_system_command_not_elapsed(self, scheduler):
        scheduler.add_command_task("t", "ls", interval_sec=60)
        scheduler.tasks[0]["last_run"] = datetime.now()
        assert scheduler._should_run(scheduler.tasks[0]) is False

    def test_system_command_elapsed(self, scheduler):
        scheduler.add_command_task("t", "ls", interval_sec=60)
        scheduler.tasks[0]["last_run"] = datetime.now() - timedelta(seconds=61)
        assert scheduler._should_run(scheduler.tasks[0]) is True

    def test_heartbeat_first_run(self, scheduler, patch_scheduler_config):
        task = {"type": "heartbeat", "name": "hb", "last_run": None, "enabled": True}
        assert scheduler._should_run(task) is True

    def test_heartbeat_not_elapsed(self, scheduler, patch_scheduler_config):
        task = {"type": "heartbeat", "name": "hb", "last_run": datetime.now(), "enabled": True}
        assert scheduler._should_run(task) is False

    def test_heartbeat_elapsed(self, scheduler, patch_scheduler_config):
        task = {
            "type": "heartbeat", "name": "hb",
            "last_run": datetime.now() - timedelta(seconds=patch_scheduler_config["heartbeat_interval"] + 1),
            "enabled": True,
        }
        assert scheduler._should_run(task) is True

    def test_unknown_type(self, scheduler):
        task = {"type": "unknown", "name": "x", "enabled": True}
        assert scheduler._should_run(task) is False


# ============================================================================
# run_task 测试
# ============================================================================

class TestRunTask:
    def test_run_python_func_success(self, scheduler, patch_paths):
        called = [False]
        def func():
            called[0] = True
        scheduler.add_interval_task("t", func, interval_seconds=60)
        result = scheduler.run_task(scheduler.tasks[0])
        assert called[0] is True
        assert result["status"] == "success"
        assert result["name"] == "t"
        assert result["type"] == "python_func"
        assert "start_time" in result
        assert "end_time" in result
        assert result["duration_ms"] >= 0
        assert scheduler.tasks[0]["last_run"] is not None

    def test_run_python_func_no_func(self, scheduler, patch_paths):
        scheduler.add_interval_task("t", lambda: None, interval_seconds=60)
        del scheduler.tasks[0]["func"]
        result = scheduler.run_task(scheduler.tasks[0])
        # 没有 func 字段时不执行，但仍标记为 success（无异常）
        assert result["status"] == "success"

    def test_run_python_func_exception(self, scheduler, patch_paths):
        def func():
            raise ValueError("boom")
        scheduler.add_interval_task("t", func, interval_seconds=60)
        result = scheduler.run_task(scheduler.tasks[0])
        assert result["status"] == "failed"
        assert "boom" in result["error"]

    @patch("agent.task_scheduler.subprocess.Popen")
    def test_run_system_command_success(self, mock_popen, scheduler, patch_paths, patch_scheduler_config):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = ("hello\n", "")
        mock_popen.return_value = mock_proc

        scheduler.add_command_task("t", "echo hello", interval_sec=60)
        result = scheduler.run_task(scheduler.tasks[0])
        assert result["status"] == "success"
        assert result["output"] == "hello"
        mock_popen.assert_called_once()

    @patch("agent.task_scheduler.subprocess.Popen")
    def test_run_system_command_failed(self, mock_popen, scheduler, patch_paths, patch_scheduler_config):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = ("", "error msg\n")
        mock_popen.return_value = mock_proc

        scheduler.add_command_task("t", "false", interval_sec=60)
        result = scheduler.run_task(scheduler.tasks[0])
        assert result["status"] == "failed"
        assert "error msg" in result["error"]

    @patch("agent.task_scheduler.subprocess.Popen")
    def test_run_system_command_timeout(self, mock_popen, scheduler, patch_paths, patch_scheduler_config):
        import subprocess as sp
        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = sp.TimeoutExpired(cmd="test", timeout=1)
        mock_popen.return_value = mock_proc

        scheduler.add_command_task("t", "sleep 999", interval_sec=60)
        result = scheduler.run_task(scheduler.tasks[0])
        assert result["status"] == "failed"
        assert "超时" in result["error"]
        mock_proc.kill.assert_called_once()

    def test_run_heartbeat_success(self, scheduler, patch_paths, patch_scheduler_config):
        hb_result = {"status": "healthy", "timestamp": datetime.now().isoformat(), "checks": {}}
        scheduler._heartbeat_func = MagicMock(return_value=hb_result)
        task = {"type": "heartbeat", "name": "hb", "task_id": "hb_1", "last_run": None, "enabled": True}
        result = scheduler.run_task(task)
        assert result["status"] == "healthy"
        assert json.loads(result["output"])["status"] == "healthy"
        scheduler._heartbeat_func.assert_called_once()

    def test_run_heartbeat_no_func(self, scheduler, patch_paths, patch_scheduler_config):
        task = {"type": "heartbeat", "name": "hb", "task_id": "hb_1", "last_run": None, "enabled": True}
        result = scheduler.run_task(task)
        # 无心跳函数时 status 保持 running
        assert result["status"] == "running"

    def test_run_task_updates_last_run(self, scheduler, patch_paths):
        scheduler.add_interval_task("t", lambda: None, interval_seconds=60)
        assert scheduler.tasks[0]["last_run"] is None
        scheduler.run_task(scheduler.tasks[0])
        assert scheduler.tasks[0]["last_run"] is not None

    def test_run_task_records_history(self, scheduler, patch_paths):
        scheduler.add_interval_task("t", lambda: None, interval_seconds=60)
        scheduler.run_task(scheduler.tasks[0])
        assert patch_paths["history"].exists()
        lines = patch_paths["history"].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["name"] == "t"


# ============================================================================
# 历史记录测试
# ============================================================================

class TestHistory:
    def test_append_history(self, scheduler, patch_paths):
        record = {"name": "t", "status": "success"}
        scheduler._append_history(record)
        assert patch_paths["history"].exists()
        content = patch_paths["history"].read_text(encoding="utf-8")
        assert json.loads(content.strip())["name"] == "t"

    def test_append_history_creates_parent_dir(self, scheduler, tmp_path, monkeypatch):
        nested = tmp_path / "nested" / "deep" / "history.jsonl"
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", nested)
        scheduler._append_history({"name": "t"})
        assert nested.exists()

    def test_trim_history(self, scheduler, patch_paths, patch_scheduler_config):
        # 写入超过 max_history_lines 的记录
        with open(patch_paths["history"], "w", encoding="utf-8") as f:
            for i in range(5):
                f.write(json.dumps({"idx": i}) + "\n")
        # 设置 max_lines=2，触发裁剪
        patch_scheduler_config["max_history_lines"] = 2
        scheduler._trim_history()
        lines = patch_paths["history"].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        # 保留最后 2 条
        assert json.loads(lines[0])["idx"] == 3
        assert json.loads(lines[1])["idx"] == 4

    def test_trim_history_no_file(self, scheduler, patch_paths):
        # 文件不存在时不报错
        scheduler._trim_history()
        assert not patch_paths["history"].exists()

    def test_trim_history_under_limit(self, scheduler, patch_paths, patch_scheduler_config):
        with open(patch_paths["history"], "w", encoding="utf-8") as f:
            f.write(json.dumps({"idx": 0}) + "\n")
        scheduler._trim_history()
        lines = patch_paths["history"].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

    def test_get_history_empty(self, scheduler, patch_paths):
        assert scheduler.get_history() == []

    def test_get_history_all(self, scheduler, patch_paths):
        with open(patch_paths["history"], "w", encoding="utf-8") as f:
            for i in range(3):
                f.write(json.dumps({"name": f"t{i}", "type": "python_func"}) + "\n")
        records = scheduler.get_history()
        assert len(records) == 3
        # reverse 顺序（最新在前）
        assert records[0]["name"] == "t2"

    def test_get_history_with_limit(self, scheduler, patch_paths):
        with open(patch_paths["history"], "w", encoding="utf-8") as f:
            for i in range(5):
                f.write(json.dumps({"name": f"t{i}"}) + "\n")
        records = scheduler.get_history(limit=2)
        assert len(records) == 2
        assert records[0]["name"] == "t4"

    def test_get_history_with_offset(self, scheduler, patch_paths):
        with open(patch_paths["history"], "w", encoding="utf-8") as f:
            for i in range(5):
                f.write(json.dumps({"name": f"t{i}"}) + "\n")
        records = scheduler.get_history(limit=2, offset=1)
        assert len(records) == 2
        assert records[0]["name"] == "t3"

    def test_get_history_filter_by_type(self, scheduler, patch_paths):
        with open(patch_paths["history"], "w", encoding="utf-8") as f:
            f.write(json.dumps({"name": "t1", "type": "python_func"}) + "\n")
            f.write(json.dumps({"name": "t2", "type": "system_command"}) + "\n")
            f.write(json.dumps({"name": "t3", "type": "python_func"}) + "\n")
        records = scheduler.get_history(task_type="python_func")
        assert len(records) == 2
        assert all(r["type"] == "python_func" for r in records)

    def test_get_history_skip_invalid_json(self, scheduler, patch_paths):
        with open(patch_paths["history"], "w", encoding="utf-8") as f:
            f.write(json.dumps({"name": "t1"}) + "\n")
            f.write("invalid json line\n")
            f.write(json.dumps({"name": "t2"}) + "\n")
        records = scheduler.get_history()
        assert len(records) == 2

    def test_get_history_file_not_exists(self, scheduler, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "nonexistent.jsonl")
        assert scheduler.get_history() == []


# ============================================================================
# 心跳持久化测试
# ============================================================================

class TestHeartbeatPersistence:
    def test_save_heartbeat_new_file(self, scheduler, patch_paths, patch_scheduler_config):
        hb_data = {
            "timestamp": datetime.now().isoformat(),
            "status": "healthy",
            "checks": {
                "system": {"cpu": 50, "memory": 60},
                "llm": {"latency_ms": 100},
            },
        }
        scheduler._save_heartbeat(hb_data)
        assert patch_paths["heartbeat"].exists()
        data = json.loads(patch_paths["heartbeat"].read_text(encoding="utf-8"))
        assert data["latest"]["status"] == "healthy"
        assert len(data["history"]) == 1
        assert data["history"][0]["cpu"] == 50
        assert data["history"][0]["memory"] == 60
        assert data["history"][0]["llm_latency_ms"] == 100

    def test_save_heartbeat_append_history(self, scheduler, patch_paths, patch_scheduler_config):
        for i in range(3):
            scheduler._save_heartbeat({
                "timestamp": datetime.now().isoformat(),
                "status": "healthy",
                "checks": {"system": {"cpu": i, "memory": i}},
            })
        data = json.loads(patch_paths["heartbeat"].read_text(encoding="utf-8"))
        assert len(data["history"]) == 3

    def test_save_heartbeat_trim_history(self, scheduler, patch_paths, patch_scheduler_config):
        patch_scheduler_config["max_heartbeat_history"] = 2
        for i in range(5):
            scheduler._save_heartbeat({
                "timestamp": datetime.now().isoformat(),
                "status": "healthy",
                "checks": {"system": {"cpu": i, "memory": i}},
            })
        data = json.loads(patch_paths["heartbeat"].read_text(encoding="utf-8"))
        assert len(data["history"]) == 2
        # 保留最后 2 条
        assert data["history"][-1]["cpu"] == 4

    def test_get_heartbeat_status_file_exists(self, scheduler, patch_paths, patch_scheduler_config):
        scheduler._save_heartbeat({
            "timestamp": datetime.now().isoformat(),
            "status": "healthy",
            "checks": {},
        })
        status = scheduler.get_heartbeat_status()
        assert status["latest"]["status"] == "healthy"

    def test_get_heartbeat_status_no_file(self, scheduler, patch_paths):
        status = scheduler.get_heartbeat_status()
        assert status["latest"]["status"] == "unknown"
        assert status["history"] == []


# ============================================================================
# load_from_json 测试
# ============================================================================

class TestLoadFromJson:
    def test_load_valid_tasks(self, scheduler, patch_paths):
        tasks_data = {
            "tasks": [
                {"name": "t1", "command": "echo 1", "interval_sec": 60, "id": "id1", "enabled": True},
                {"name": "t2", "command": "echo 2", "interval_sec": 120, "id": "id2", "enabled": True},
            ]
        }
        patch_paths["tasks"].write_text(json.dumps(tasks_data), encoding="utf-8")
        count = scheduler.load_from_json()
        assert count == 2
        assert len(scheduler.tasks) == 2
        assert scheduler.tasks[0]["name"] == "t1"
        assert scheduler.tasks[0]["task_id"] == "id1"

    def test_load_skips_disabled(self, scheduler, patch_paths):
        tasks_data = {
            "tasks": [
                {"name": "t1", "command": "echo 1", "interval_sec": 60, "id": "id1", "enabled": True},
                {"name": "t2", "command": "echo 2", "interval_sec": 120, "id": "id2", "enabled": False},
            ]
        }
        patch_paths["tasks"].write_text(json.dumps(tasks_data), encoding="utf-8")
        count = scheduler.load_from_json()
        assert count == 1
        assert len(scheduler.tasks) == 1

    def test_load_default_interval(self, scheduler, patch_paths):
        tasks_data = {
            "tasks": [
                {"name": "t1", "command": "echo 1", "id": "id1", "enabled": True},
            ]
        }
        patch_paths["tasks"].write_text(json.dumps(tasks_data), encoding="utf-8")
        scheduler.load_from_json()
        assert scheduler.tasks[0]["interval"] == 60

    def test_load_file_not_exists(self, scheduler, patch_paths):
        count = scheduler.load_from_json()
        assert count == 0
        assert len(scheduler.tasks) == 0

    def test_load_custom_path(self, scheduler, tmp_path):
        custom = tmp_path / "custom_tasks.json"
        tasks_data = {"tasks": [
            {"name": "t1", "command": "echo", "interval_sec": 30, "id": "x", "enabled": True}
        ]}
        custom.write_text(json.dumps(tasks_data), encoding="utf-8")
        count = scheduler.load_from_json(str(custom))
        assert count == 1

    def test_load_invalid_json(self, scheduler, patch_paths):
        patch_paths["tasks"].write_text("invalid json", encoding="utf-8")
        count = scheduler.load_from_json()
        assert count == 0

    def test_load_empty_tasks(self, scheduler, patch_paths):
        patch_paths["tasks"].write_text(json.dumps({"tasks": []}), encoding="utf-8")
        count = scheduler.load_from_json()
        assert count == 0


# ============================================================================
# 调度器生命周期测试
# ============================================================================

class TestSchedulerLifecycle:
    def test_start_daemon(self, scheduler, patch_scheduler_config):
        scheduler.start_daemon(check_interval=0.01)
        assert scheduler.running is True
        assert scheduler._thread is not None
        assert scheduler._thread.daemon is True
        time.sleep(0.05)
        scheduler.stop()
        scheduler._thread.join(timeout=1)

    def test_start_daemon_already_running(self, scheduler, patch_scheduler_config):
        scheduler.running = True
        # 已运行时不重复启动
        scheduler.start_daemon(check_interval=1)
        assert scheduler._thread is None

    def test_start_daemon_default_interval(self, scheduler, patch_scheduler_config):
        scheduler.start_daemon()
        assert scheduler.running is True
        scheduler.stop()
        scheduler._thread.join(timeout=1)

    def test_stop(self, scheduler):
        scheduler.running = True
        scheduler.stop()
        assert scheduler.running is False

    def test_tick_executes_due_tasks(self, scheduler, patch_paths):
        called = [0]
        def func():
            called[0] += 1
        scheduler.add_interval_task("t", func, interval_seconds=60)
        scheduler.tick()
        assert called[0] == 1

    def test_tick_skips_not_due_tasks(self, scheduler, patch_paths):
        called = [0]
        def func():
            called[0] += 1
        scheduler.add_interval_task("t", func, interval_seconds=60)
        scheduler.tasks[0]["last_run"] = datetime.now()
        scheduler.tick()
        assert called[0] == 0

    def test_execute_now(self, scheduler, patch_paths):
        called = [False]
        def func():
            called[0] = True
        scheduler.add_interval_task("t", func, interval_seconds=60)
        result = scheduler.execute_now(scheduler.tasks[0]["task_id"])
        assert called[0] is True
        assert result is not None
        assert result["status"] == "success"

    def test_execute_now_not_found(self, scheduler, patch_paths):
        result = scheduler.execute_now("nonexistent")
        assert result is None

    def test_run_loop_handles_tick_exception(self, scheduler, patch_scheduler_config):
        call_count = [0]
        def failing_tick():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RuntimeError("tick error")
            # 第 3 次停止循环
            scheduler.running = False
        scheduler.tick = failing_tick
        scheduler.running = True
        scheduler._run_loop(check_interval=0.01)
        assert call_count[0] >= 3


# ============================================================================
# list_tasks 测试
# ============================================================================

class TestListTasks:
    def test_list_empty(self, scheduler):
        assert scheduler.list_tasks() == []

    def test_list_cron_task(self, scheduler):
        scheduler.add_cron_task("t", lambda: None, day_of_week=1, hour=9, minute=0)
        result = scheduler.list_tasks()
        assert len(result) == 1
        assert result[0]["name"] == "t"
        assert result[0]["type"] == "python_func"
        assert result[0]["cron"] == {"day_of_week": 1, "hour": 9, "minute": 0}
        assert result[0]["last_run"] is None

    def test_list_interval_task(self, scheduler):
        scheduler.add_interval_task("t", lambda: None, interval_seconds=60)
        result = scheduler.list_tasks()
        assert result[0]["interval_sec"] == 60

    def test_list_command_task(self, scheduler):
        scheduler.add_command_task("t", "echo hello", interval_sec=30)
        result = scheduler.list_tasks()
        assert result[0]["command"] == "echo hello"
        assert result[0]["interval_sec"] == 30

    def test_list_heartbeat_task(self, scheduler, patch_scheduler_config):
        scheduler.tasks.append({
            "task_id": "hb_1", "name": "heartbeat", "type": "heartbeat",
            "last_run": None, "enabled": True,
        })
        result = scheduler.list_tasks()
        assert result[0]["interval_sec"] == patch_scheduler_config["heartbeat_interval"]

    def test_list_task_with_last_run(self, scheduler):
        scheduler.add_interval_task("t", lambda: None, interval_seconds=60)
        scheduler.tasks[0]["last_run"] = datetime(2026, 1, 1, 12, 0, 0)
        result = scheduler.list_tasks()
        assert result[0]["last_run"] == "2026-01-01T12:00:00"


# ============================================================================
# 全局单例测试
# ============================================================================

class TestGlobalSingleton:
    def test_get_scheduler_returns_instance(self, reset_scheduler_singleton):
        s = get_scheduler()
        assert isinstance(s, TaskScheduler)

    def test_get_scheduler_singleton(self, reset_scheduler_singleton):
        s1 = get_scheduler()
        s2 = get_scheduler()
        assert s1 is s2

    def test_get_scheduler_preRegisters_tasks(self, reset_scheduler_singleton):
        s = get_scheduler()
        names = [t["name"] for t in s.tasks]
        assert "生成周报" in names
        assert "清理旧日志" in names


# ============================================================================
# 预定义任务函数测试
# ============================================================================

class TestPredefinedTasks:
    @patch("agent.task_scheduler.run_weekly_report", create=True)
    def test_generate_weekly_report_success(self, mock_report):
        mock_report.return_value = ({"summary": "ok"}, ["report.json", "report.html"])
        with patch.dict("sys.modules", {"agent.weekly_report_generator": MagicMock(run_weekly_report=mock_report)}):
            generate_weekly_report()

    def test_generate_weekly_report_failure(self):
        # import 失败时不抛异常
        generate_weekly_report()

    def test_cleanup_old_logs_no_dir(self):
        # data/blackbox 不存在时不报错
        cleanup_old_logs()

    def test_cleanup_old_logs_with_files(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "blackbox"
        log_dir.mkdir()
        old_file = log_dir / "blackbox_20250101.jsonl"
        old_file.write_text("old log", encoding="utf-8")
        # 设置 mtime 为 60 天前
        old_time = time.time() - (60 * 24 * 60 * 60)
        os.utime(str(old_file), (old_time, old_time))

        new_file = log_dir / "blackbox_20260701.jsonl"
        new_file.write_text("new log", encoding="utf-8")

        monkeypatch.setattr("agent.task_scheduler.DATA_DIR", tmp_path)
        cleanup_old_logs()
        assert not old_file.exists()
        assert new_file.exists()


# ============================================================================
# perform_heartbeat_check 测试
# ============================================================================

class TestPerformHeartbeatCheck:
    def test_check_no_yunshu_psutil(self):
        with patch("psutil.cpu_percent", return_value=30), \
             patch("psutil.virtual_memory") as mock_mem, \
             patch("psutil.disk_usage") as mock_disk:
            mock_mem.return_value.percent = 40
            mock_disk.return_value.percent = 50
            result = perform_heartbeat_check(None)
        assert result["status"] in ("healthy", "degraded", "unhealthy")
        assert "system" in result["checks"]
        assert result["checks"]["system"]["status"] == "ok"

    def test_check_system_warn_high_cpu(self):
        with patch("psutil.cpu_percent", return_value=95), \
             patch("psutil.virtual_memory") as mock_mem, \
             patch("psutil.disk_usage") as mock_disk:
            mock_mem.return_value.percent = 50
            mock_disk.return_value.percent = 50
            result = perform_heartbeat_check(None)
        assert result["checks"]["system"]["status"] == "warn"

    def test_check_system_warn_high_memory(self):
        with patch("psutil.cpu_percent", return_value=30), \
             patch("psutil.virtual_memory") as mock_mem, \
             patch("psutil.disk_usage") as mock_disk:
            mock_mem.return_value.percent = 95
            mock_disk.return_value.percent = 50
            result = perform_heartbeat_check(None)
        assert result["checks"]["system"]["status"] == "warn"

    def test_check_system_warn_high_disk(self):
        with patch("psutil.cpu_percent", return_value=30), \
             patch("psutil.virtual_memory") as mock_mem, \
             patch("psutil.disk_usage") as mock_disk:
            mock_mem.return_value.percent = 50
            mock_disk.return_value.percent = 98
            result = perform_heartbeat_check(None)
        assert result["checks"]["system"]["status"] == "warn"

    def test_check_system_error(self):
        with patch("psutil.cpu_percent", side_effect=Exception("psutil fail")):
            result = perform_heartbeat_check(None)
        assert result["checks"]["system"]["status"] == "error"
        assert result["status"] != "healthy"

    def test_check_with_yunshu_instance(self):
        yunshu = MagicMock()
        reading1 = MagicMock()
        reading1.to_dict.return_value = {"sensor_name": "cpu_usage", "value": 40}
        reading2 = MagicMock()
        reading2.to_dict.return_value = {"sensor_name": "memory_usage", "value": 50}
        yunshu.body.collect_quick.return_value = [reading1, reading2]
        yunshu._llm = MagicMock(provider="openai", model="gpt-4")
        yunshu._memory = MagicMock()

        result = perform_heartbeat_check(yunshu)
        assert result["checks"]["system"]["status"] == "ok"
        assert result["checks"]["llm"]["status"] == "ok"
        assert result["checks"]["memory"]["status"] == "ok"

    def test_check_llm_not_configured(self):
        yunshu = MagicMock()
        yunshu.body.collect_quick.return_value = []
        yunshu._llm = None
        yunshu._memory = MagicMock()
        result = perform_heartbeat_check(yunshu)
        assert result["checks"]["llm"]["status"] == "not_configured"

    def test_check_memory_not_available(self):
        yunshu = MagicMock()
        yunshu.body.collect_quick.return_value = []
        del yunshu._memory
        # hasattr 返回 False
        result = perform_heartbeat_check(yunshu)
        assert result["checks"]["memory"]["status"] == "not_available"

    def test_check_scheduler_stopped(self, reset_scheduler_singleton):
        result = perform_heartbeat_check(None)
        # _scheduler 未启动时为 stopped
        assert result["checks"]["scheduler"]["status"] == "stopped"
        assert result["checks"]["scheduler"]["running"] is False

    def test_check_scheduler_running(self, reset_scheduler_singleton, patch_scheduler_config):
        s = get_scheduler()
        s.running = True
        s.tasks = [{"name": "t1"}]
        result = perform_heartbeat_check(None)
        assert result["checks"]["scheduler"]["status"] == "ok"
        assert result["checks"]["scheduler"]["running"] is True
        assert result["checks"]["scheduler"]["tasks"] == 1
        s.stop()

    def test_check_threads(self):
        result = perform_heartbeat_check(None)
        assert "threads" in result["checks"]
        assert "total" in result["checks"]["threads"]

    def test_overall_status_healthy(self):
        yunshu = MagicMock()
        yunshu.body.collect_quick.return_value = []
        yunshu._llm = MagicMock(provider="openai", model="gpt-4")
        yunshu._memory = MagicMock()
        with patch("agent.task_scheduler._scheduler") as mock_s:
            mock_s.running = True
            mock_s.tasks = []
            result = perform_heartbeat_check(yunshu)
        assert result["status"] == "healthy"

    def test_overall_status_unhealthy_on_error(self):
        with patch("psutil.cpu_percent", side_effect=Exception("fail")):
            result = perform_heartbeat_check(None)
        assert result["status"] == "unhealthy"
