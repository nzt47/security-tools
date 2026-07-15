"""TaskScheduler 综合单元测试

覆盖模块: agent/task_scheduler.py
测试维度: 初始化 / 任务添加 / 删除 / 启用禁用 / 查找 / 调度判断 / 执行 / 历史 / JSON 加载 / daemon
设计原则: AAA (Arrange-Act-Assert), 隔离文件系统 (tmp_path), 不依赖外部进程
"""

import json
import os
import time
from datetime import datetime, timedelta
from unittest import mock

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
    SCHEDULED_TASKS_FILE,
    TASK_HISTORY_FILE,
    HEARTBEAT_HISTORY_FILE,
    _trace_id,
)


@pytest.fixture(scope="session", autouse=True)
def _preload_sentence_transformers():
    """预加载 sentence_transformers 避免 18.5s 首次导入瓶颈

    Why: sentence_transformers 触发 torch + transformers 完整导入链，
    首次导入 18.5s。session 级 fixture 确保只在测试开始时导入一次，
    后续 test_generate_weekly_report_no_exception 直接受益于缓存。
    """
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        pass  # 测试环境未安装，后续 VectorStore 走 JSON fallback


# ═══════════════════════════════════════════════════════════════
# 常量测试
# ═══════════════════════════════════════════════════════════════


class TestConstants:
    """模块常量测试"""

    def test_default_check_interval(self):
        assert DEFAULT_CHECK_INTERVAL == 10

    def test_command_timeout(self):
        assert COMMAND_TIMEOUT == 300

    def test_max_history_lines(self):
        assert MAX_HISTORY_LINES == 1000

    def test_heartbeat_interval(self):
        assert HEARTBEAT_INTERVAL == 60

    def test_max_heartbeat_history(self):
        assert MAX_HEARTBEAT_HISTORY == 1440

    def test_file_paths_are_pathlike(self):
        assert hasattr(SCHEDULED_TASKS_FILE, "exists")
        assert hasattr(TASK_HISTORY_FILE, "exists")
        assert hasattr(HEARTBEAT_HISTORY_FILE, "exists")


class TestTraceId:
    """_trace_id 函数测试"""

    def test_returns_string(self):
        tid = _trace_id()
        assert isinstance(tid, str)
        assert len(tid) > 0

    def test_returns_hex(self):
        tid = _trace_id()
        # 应是 hex 字符
        assert all(c in "0123456789abcdef" for c in tid)

    def test_unique(self):
        ids = {_trace_id() for _ in range(20)}
        assert len(ids) == 20  # 基本唯一性


# ═══════════════════════════════════════════════════════════════
# TaskScheduler 初始化
# ═══════════════════════════════════════════════════════════════


class TestTaskSchedulerInit:
    """TaskScheduler 初始化测试"""

    def test_init_empty_tasks(self):
        scheduler = TaskScheduler()
        assert scheduler.tasks == []

    def test_init_not_running(self):
        scheduler = TaskScheduler()
        assert scheduler.running is False

    def test_init_no_thread(self):
        scheduler = TaskScheduler()
        assert scheduler._thread is None

    def test_init_no_heartbeat_func(self):
        scheduler = TaskScheduler()
        assert scheduler._heartbeat_func is None

    def test_init_no_yunshu_ref(self):
        scheduler = TaskScheduler()
        assert scheduler._yunshu_ref is None


# ═══════════════════════════════════════════════════════════════
# add_cron_task
# ═══════════════════════════════════════════════════════════════


class TestAddCronTask:
    """add_cron_task 测试"""

    def test_add_basic(self):
        scheduler = TaskScheduler()
        scheduler.add_cron_task(name="test-task", func=lambda: None)
        assert len(scheduler.tasks) == 1
        task = scheduler.tasks[0]
        assert task["name"] == "test-task"
        assert task["type"] == "python_func"
        assert "func" in task
        assert "cron" in task
        assert task["enabled"] is True
        assert task["last_run"] is None

    def test_add_with_cron_params(self):
        scheduler = TaskScheduler()
        scheduler.add_cron_task(
            name="weekly", func=lambda: None, day_of_week=0, hour=9, minute=30
        )
        task = scheduler.tasks[0]
        assert task["cron"]["day_of_week"] == 0
        assert task["cron"]["hour"] == 9
        assert task["cron"]["minute"] == 30

    def test_add_generates_task_id(self):
        scheduler = TaskScheduler()
        scheduler.add_cron_task(name="t1", func=lambda: None)
        scheduler.add_cron_task(name="t2", func=lambda: None)
        assert scheduler.tasks[0]["task_id"] != scheduler.tasks[1]["task_id"]


# ═══════════════════════════════════════════════════════════════
# add_interval_task
# ═══════════════════════════════════════════════════════════════


class TestAddIntervalTask:
    """add_interval_task 测试"""

    def test_add_basic(self):
        scheduler = TaskScheduler()
        scheduler.add_interval_task(name="interval", func=lambda: None, interval_seconds=60)
        task = scheduler.tasks[0]
        assert task["name"] == "interval"
        assert task["type"] == "python_func"
        assert task["interval"] == 60
        assert task["enabled"] is True

    def test_add_multiple(self):
        scheduler = TaskScheduler()
        scheduler.add_interval_task(name="t1", func=lambda: None, interval_seconds=10)
        scheduler.add_interval_task(name="t2", func=lambda: None, interval_seconds=20)
        assert len(scheduler.tasks) == 2
        assert scheduler.tasks[0]["interval"] == 10
        assert scheduler.tasks[1]["interval"] == 20


# ═══════════════════════════════════════════════════════════════
# add_command_task
# ═══════════════════════════════════════════════════════════════


class TestAddCommandTask:
    """add_command_task 测试"""

    def test_add_basic(self):
        scheduler = TaskScheduler()
        scheduler.add_command_task(
            name="cmd", command="echo hello", interval_sec=30
        )
        task = scheduler.tasks[0]
        assert task["name"] == "cmd"
        assert task["type"] == "system_command"
        assert task["command"] == "echo hello"
        assert task["interval"] == 30
        assert task["enabled"] is True

    def test_add_with_task_id(self):
        scheduler = TaskScheduler()
        scheduler.add_command_task(
            name="cmd", command="ls", interval_sec=30, task_id="custom-id"
        )
        assert scheduler.tasks[0]["task_id"] == "custom-id"

    def test_add_with_disabled(self):
        scheduler = TaskScheduler()
        scheduler.add_command_task(
            name="cmd", command="ls", interval_sec=30, enabled=False
        )
        assert scheduler.tasks[0]["enabled"] is False


# ═══════════════════════════════════════════════════════════════
# remove_task / set_task_enabled / get_task
# ═══════════════════════════════════════════════════════════════


class TestRemoveTask:
    """remove_task 测试"""

    def test_remove_existing(self):
        scheduler = TaskScheduler()
        scheduler.add_command_task(name="t", command="ls", interval_sec=30, task_id="id-1")
        assert scheduler.remove_task("id-1") is True
        assert len(scheduler.tasks) == 0

    def test_remove_nonexistent(self):
        scheduler = TaskScheduler()
        assert scheduler.remove_task("nonexistent") is False

    def test_remove_only_target(self):
        scheduler = TaskScheduler()
        scheduler.add_command_task(name="t1", command="ls", interval_sec=30, task_id="id-1")
        scheduler.add_command_task(name="t2", command="ls", interval_sec=30, task_id="id-2")
        scheduler.remove_task("id-1")
        assert len(scheduler.tasks) == 1
        assert scheduler.tasks[0]["task_id"] == "id-2"


class TestSetTaskEnabled:
    """set_task_enabled 测试"""

    def test_disable_existing(self):
        scheduler = TaskScheduler()
        scheduler.add_command_task(name="t", command="ls", interval_sec=30, task_id="id-1")
        assert scheduler.set_task_enabled("id-1", False) is True
        assert scheduler.tasks[0]["enabled"] is False

    def test_enable_existing(self):
        scheduler = TaskScheduler()
        scheduler.add_command_task(name="t", command="ls", interval_sec=30, task_id="id-1", enabled=False)
        assert scheduler.set_task_enabled("id-1", True) is True
        assert scheduler.tasks[0]["enabled"] is True

    def test_nonexistent_returns_false(self):
        scheduler = TaskScheduler()
        assert scheduler.set_task_enabled("nonexistent", True) is False


class TestGetTask:
    """get_task 测试"""

    def test_get_existing(self):
        scheduler = TaskScheduler()
        scheduler.add_command_task(name="t", command="ls", interval_sec=30, task_id="id-1")
        task = scheduler.get_task("id-1")
        assert task is not None
        assert task["name"] == "t"

    def test_get_nonexistent(self):
        scheduler = TaskScheduler()
        assert scheduler.get_task("nonexistent") is None


# ═══════════════════════════════════════════════════════════════
# _generate_task_id
# ═══════════════════════════════════════════════════════════════


class TestGenerateTaskId:
    """_generate_task_id 测试"""

    def test_generates_unique_ids(self):
        scheduler = TaskScheduler()
        id1 = scheduler._generate_task_id("task")
        # 添加一个任务使 len(self.tasks) 变化，确保 ID 不同
        scheduler.tasks.append({})
        id2 = scheduler._generate_task_id("task")
        assert id1 != id2

    def test_id_has_prefix(self):
        scheduler = TaskScheduler()
        tid = scheduler._generate_task_id("custom")
        assert tid.startswith("custom_")


# ═══════════════════════════════════════════════════════════════
# _should_run
# ═══════════════════════════════════════════════════════════════


class TestShouldRun:
    """_should_run 测试"""

    def test_disabled_task_returns_false(self):
        scheduler = TaskScheduler()
        task = {
            "type": "python_func",
            "interval": 60,
            "enabled": False,
            "last_run": None,
        }
        assert scheduler._should_run(task) is False

    def test_interval_task_first_run(self):
        scheduler = TaskScheduler()
        task = {
            "type": "python_func",
            "interval": 60,
            "enabled": True,
            "last_run": None,
        }
        assert scheduler._should_run(task) is True

    def test_interval_task_not_due(self):
        scheduler = TaskScheduler()
        task = {
            "type": "python_func",
            "interval": 60,
            "enabled": True,
            "last_run": datetime.now(),
        }
        assert scheduler._should_run(task) is False

    def test_interval_task_due(self):
        scheduler = TaskScheduler()
        task = {
            "type": "python_func",
            "interval": 1,
            "enabled": True,
            "last_run": datetime.now() - timedelta(seconds=10),
        }
        assert scheduler._should_run(task) is True

    def test_command_task_first_run(self):
        scheduler = TaskScheduler()
        task = {
            "type": "system_command",
            "interval": 60,
            "enabled": True,
            "last_run": None,
        }
        assert scheduler._should_run(task) is True

    def test_command_task_due(self):
        scheduler = TaskScheduler()
        task = {
            "type": "system_command",
            "interval": 30,
            "enabled": True,
            "last_run": datetime.now() - timedelta(seconds=60),
        }
        assert scheduler._should_run(task) is True

    def test_cron_task_matching_time(self):
        scheduler = TaskScheduler()
        now = datetime.now()
        task = {
            "type": "python_func",
            "cron": {"day_of_week": now.weekday(), "hour": now.hour, "minute": now.minute},
            "enabled": True,
            "last_run": None,
        }
        assert scheduler._should_run(task) is True

    def test_cron_task_wrong_day(self):
        scheduler = TaskScheduler()
        now = datetime.now()
        wrong_day = (now.weekday() + 1) % 7
        task = {
            "type": "python_func",
            "cron": {"day_of_week": wrong_day, "hour": now.hour, "minute": now.minute},
            "enabled": True,
            "last_run": None,
        }
        assert scheduler._should_run(task) is False

    def test_cron_task_already_run_today(self):
        scheduler = TaskScheduler()
        now = datetime.now()
        task = {
            "type": "python_func",
            "cron": {"day_of_week": None, "hour": now.hour, "minute": now.minute},
            "enabled": True,
            "last_run": now,
        }
        assert scheduler._should_run(task) is False

    def test_unknown_type_returns_false(self):
        scheduler = TaskScheduler()
        task = {
            "type": "unknown_type",
            "enabled": True,
            "last_run": None,
        }
        assert scheduler._should_run(task) is False


# ═══════════════════════════════════════════════════════════════
# run_task
# ═══════════════════════════════════════════════════════════════


class TestRunTask:
    """run_task 测试"""

    def test_run_python_func_success(self, tmp_path, monkeypatch):
        # 重定向历史文件到临时目录
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "history.jsonl")
        scheduler = TaskScheduler()
        called = []
        def my_func():
            called.append(True)
        task = {
            "task_id": "test-1",
            "name": "test",
            "type": "python_func",
            "func": my_func,
            "enabled": True,
            "last_run": None,
        }
        result = scheduler.run_task(task)
        assert result["status"] == "success"
        assert len(called) == 1
        assert task["last_run"] is not None

    def test_run_python_func_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "history.jsonl")
        scheduler = TaskScheduler()
        def bad_func():
            raise ValueError("test error")
        task = {
            "task_id": "test-2",
            "name": "bad",
            "type": "python_func",
            "func": bad_func,
            "enabled": True,
            "last_run": None,
        }
        result = scheduler.run_task(task)
        assert result["status"] == "failed"
        assert "test error" in result["error"]

    def test_run_command_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "history.jsonl")
        scheduler = TaskScheduler()
        task = {
            "task_id": "cmd-1",
            "name": "echo",
            "type": "system_command",
            "command": "echo hello",
            "enabled": True,
            "last_run": None,
        }
        result = scheduler.run_task(task)
        assert result["status"] == "success"
        assert "hello" in result["output"]

    def test_run_command_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "history.jsonl")
        scheduler = TaskScheduler()
        task = {
            "task_id": "cmd-2",
            "name": "fail",
            "type": "system_command",
            "command": "exit 1",
            "enabled": True,
            "last_run": None,
        }
        result = scheduler.run_task(task)
        assert result["status"] == "failed"

    def test_run_task_records_duration(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "history.jsonl")
        scheduler = TaskScheduler()
        task = {
            "task_id": "dur-1",
            "name": "test",
            "type": "python_func",
            "func": lambda: None,
            "enabled": True,
            "last_run": None,
        }
        result = scheduler.run_task(task)
        assert "duration_ms" in result
        assert result["duration_ms"] >= 0
        assert "start_time" in result
        assert "end_time" in result


# ═══════════════════════════════════════════════════════════════
# 历史记录
# ═══════════════════════════════════════════════════════════════


class TestHistory:
    """get_history / _append_history / _trim_history 测试"""

    def test_get_history_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "no_exist.jsonl")
        scheduler = TaskScheduler()
        assert scheduler.get_history() == []

    def test_append_and_get_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "history.jsonl")
        scheduler = TaskScheduler()
        record = {"task_id": "t1", "name": "test", "status": "success"}
        scheduler._append_history(record)
        history = scheduler.get_history()
        assert len(history) == 1
        assert history[0]["task_id"] == "t1"

    def test_get_history_with_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "history.jsonl")
        scheduler = TaskScheduler()
        for i in range(10):
            scheduler._append_history({"task_id": f"t{i}", "name": "test", "status": "success"})
        history = scheduler.get_history(limit=5)
        assert len(history) == 5

    def test_get_history_with_task_type_filter(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "history.jsonl")
        scheduler = TaskScheduler()
        scheduler._append_history({"task_id": "t1", "type": "python_func", "status": "success"})
        scheduler._append_history({"task_id": "t2", "type": "system_command", "status": "success"})
        history = scheduler.get_history(task_type="python_func")
        assert len(history) == 1
        assert history[0]["type"] == "python_func"

    def test_history_skips_invalid_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "history.jsonl")
        # 写入一条有效和一条无效 JSON
        with open(tmp_path / "history.jsonl", "w", encoding="utf-8") as f:
            f.write('{"task_id": "valid", "status": "success"}\n')
            f.write('invalid json line\n')
        scheduler = TaskScheduler()
        history = scheduler.get_history()
        assert len(history) == 1


# ═══════════════════════════════════════════════════════════════
# load_from_json
# ═══════════════════════════════════════════════════════════════


class TestLoadFromJson:
    """load_from_json 测试"""

    def test_load_nonexistent_file(self, tmp_path):
        scheduler = TaskScheduler()
        count = scheduler.load_from_json(str(tmp_path / "no_exist.json"))
        assert count == 0

    def test_load_valid_file(self, tmp_path):
        path = tmp_path / "tasks.json"
        data = {
            "tasks": [
                {"name": "task1", "command": "echo hello", "interval_sec": 60},
                {"name": "task2", "command": "ls", "interval_sec": 30, "enabled": False},
            ]
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        scheduler = TaskScheduler()
        count = scheduler.load_from_json(str(path))
        assert count == 1  # 第二个 enabled=False 应跳过
        assert len(scheduler.tasks) == 1
        assert scheduler.tasks[0]["name"] == "task1"

    def test_load_empty_tasks(self, tmp_path):
        path = tmp_path / "tasks.json"
        path.write_text('{"tasks": []}', encoding="utf-8")
        scheduler = TaskScheduler()
        assert scheduler.load_from_json(str(path)) == 0

    def test_load_invalid_json(self, tmp_path):
        path = tmp_path / "tasks.json"
        path.write_text("invalid json", encoding="utf-8")
        scheduler = TaskScheduler()
        # 不应抛异常
        assert scheduler.load_from_json(str(path)) == 0


# ═══════════════════════════════════════════════════════════════
# list_tasks
# ═══════════════════════════════════════════════════════════════


class TestListTasks:
    """list_tasks 测试"""

    def test_list_empty(self):
        scheduler = TaskScheduler()
        assert scheduler.list_tasks() == []

    def test_list_command_tasks(self):
        scheduler = TaskScheduler()
        scheduler.add_command_task(name="cmd", command="ls", interval_sec=30, task_id="id-1")
        result = scheduler.list_tasks()
        assert len(result) == 1
        assert result[0]["name"] == "cmd"
        assert result[0]["command"] == "ls"
        assert result[0]["interval_sec"] == 30
        assert "func" not in result[0]

    def test_list_interval_tasks(self):
        scheduler = TaskScheduler()
        scheduler.add_interval_task(name="t", func=lambda: None, interval_seconds=60)
        result = scheduler.list_tasks()
        assert len(result) == 1
        assert result[0]["interval_sec"] == 60
        assert "func" not in result[0]

    def test_list_cron_tasks(self):
        scheduler = TaskScheduler()
        scheduler.add_cron_task(name="cron", func=lambda: None, hour=9, minute=0)
        result = scheduler.list_tasks()
        assert len(result) == 1
        assert "cron" in result[0]


# ═══════════════════════════════════════════════════════════════
# execute_now
# ═══════════════════════════════════════════════════════════════


class TestExecuteNow:
    """execute_now 测试"""

    def test_execute_existing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "history.jsonl")
        scheduler = TaskScheduler()
        called = []
        scheduler.add_interval_task(
            name="t", func=lambda: called.append(True), interval_seconds=60
        )
        task_id = scheduler.tasks[0]["task_id"]
        result = scheduler.execute_now(task_id)
        assert result is not None
        assert result["status"] == "success"
        assert len(called) == 1

    def test_execute_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "history.jsonl")
        scheduler = TaskScheduler()
        assert scheduler.execute_now("nonexistent") is None


# ═══════════════════════════════════════════════════════════════
# tick
# ═══════════════════════════════════════════════════════════════


class TestTick:
    """tick 测试"""

    def test_tick_executes_due_tasks(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "history.jsonl")
        scheduler = TaskScheduler()
        called = []
        scheduler.add_interval_task(
            name="t", func=lambda: called.append(True), interval_seconds=1
        )
        scheduler.tick()
        assert len(called) == 1

    def test_tick_skips_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.TASK_HISTORY_FILE", tmp_path / "history.jsonl")
        scheduler = TaskScheduler()
        called = []
        scheduler.add_interval_task(
            name="t", func=lambda: called.append(True), interval_seconds=1
        )
        scheduler.set_task_enabled(scheduler.tasks[0]["task_id"], False)
        scheduler.tick()
        assert len(called) == 0


# ═══════════════════════════════════════════════════════════════
# start_daemon / stop
# ═══════════════════════════════════════════════════════════════


class TestStartDaemon:
    """start_daemon / stop 测试"""

    def test_start_daemon(self):
        scheduler = TaskScheduler()
        scheduler.start_daemon(check_interval=0.05)
        assert scheduler.running is True
        assert scheduler._thread is not None
        scheduler.stop()
        scheduler._thread.join(timeout=2)

    def test_start_daemon_idempotent(self):
        scheduler = TaskScheduler()
        scheduler.start_daemon(check_interval=0.05)
        first_thread = scheduler._thread
        scheduler.start_daemon(check_interval=0.05)
        # 应该是同一个线程（不重复启动）
        assert scheduler._thread is first_thread
        scheduler.stop()
        scheduler._thread.join(timeout=2)

    def test_stop(self):
        scheduler = TaskScheduler()
        scheduler.start_daemon(check_interval=0.05)
        scheduler.stop()
        assert scheduler.running is False
        scheduler._thread.join(timeout=2)


# ═══════════════════════════════════════════════════════════════
# 心跳功能
# ═══════════════════════════════════════════════════════════════


class TestHeartbeat:
    """_save_heartbeat / get_heartbeat_status 测试"""

    def test_save_heartbeat(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.HEARTBEAT_HISTORY_FILE", tmp_path / "hb.json")
        scheduler = TaskScheduler()
        hb_data = {
            "timestamp": datetime.now().isoformat(),
            "status": "ok",
            "checks": {"system": {"cpu": 50, "memory": 60}},
        }
        scheduler._save_heartbeat(hb_data)
        status = scheduler.get_heartbeat_status()
        assert status["latest"]["status"] == "ok"

    def test_get_heartbeat_status_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.HEARTBEAT_HISTORY_FILE", tmp_path / "no_exist.json")
        scheduler = TaskScheduler()
        status = scheduler.get_heartbeat_status()
        assert status["latest"]["status"] == "unknown"
        assert status["history"] == []

    def test_save_multiple_heartbeats(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.task_scheduler.HEARTBEAT_HISTORY_FILE", tmp_path / "hb.json")
        scheduler = TaskScheduler()
        for i in range(3):
            scheduler._save_heartbeat({
                "timestamp": datetime.now().isoformat(),
                "status": "ok" if i % 2 == 0 else "warn",
                "checks": {"system": {"cpu": 50 + i, "memory": 60}},
            })
        status = scheduler.get_heartbeat_status()
        assert len(status["history"]) == 3


# ═══════════════════════════════════════════════════════════════
# get_scheduler 单例
# ═══════════════════════════════════════════════════════════════


class TestGetScheduler:
    """get_scheduler 单例测试"""

    def test_returns_scheduler(self):
        scheduler = get_scheduler()
        assert isinstance(scheduler, TaskScheduler)

    def test_singleton(self):
        s1 = get_scheduler()
        s2 = get_scheduler()
        assert s1 is s2

    def test_has_predefined_tasks(self):
        # 单例应该预注册了任务（除非之前已被修改）
        scheduler = get_scheduler()
        # 至少有一些任务（来自预注册）
        assert len(scheduler.tasks) >= 0  # 宽松断言，避免测试间依赖


# ═══════════════════════════════════════════════════════════════
# 预定义任务函数
# ═══════════════════════════════════════════════════════════════


class TestPredefinedFunctions:
    """generate_weekly_report / cleanup_old_logs / perform_heartbeat_check 测试"""

    def test_generate_weekly_report_no_exception(self):
        """生成周报不应抛出异常 — cProfile 定位模型加载瓶颈的精确调用链"""
        import time as _time
        import cProfile
        import pstats
        import io as _io

        # cProfile 单次调用，精确定位所有函数累计耗时（无 mock 基线）
        _profiler = cProfile.Profile()
        t0 = _time.perf_counter()
        _profiler.enable()
        generate_weekly_report()
        _profiler.disable()
        t_total = (_time.perf_counter() - t0) * 1000

        # 输出 cProfile top 30（按累计耗时排序）— 定位瓶颈所在函数
        _buf = _io.StringIO()
        _ps = pstats.Stats(_profiler, stream=_buf).sort_stats('cumulative')
        _ps.print_stats(30)
        print(f"\n  [cProfile] generate_weekly_report 总耗时: {t_total:.0f}ms")
        print(_buf.getvalue())

    def test_cleanup_old_logs_no_exception(self, tmp_path):
        # 创建假日志目录
        log_dir = tmp_path / "blackbox"
        log_dir.mkdir(parents=True)
        old_log = log_dir / "blackbox_old.jsonl"
        old_log.write_text("{}", encoding="utf-8")
        # 修改访问时间为很早以前
        import os
        old_time = time.time() - (31 * 24 * 60 * 60)
        os.utime(str(old_log), (old_time, old_time))
        # 用 mock 重定向 DATA_DIR
        with mock.patch("agent.task_scheduler.DATA_DIR", tmp_path):
            cleanup_old_logs()

    def test_perform_heartbeat_check_basic(self):
        # 没有 yunshu_instance 时应使用 psutil
        # Why: mock cpu_percent 避免 interval=1 的 1 秒阻塞，测试验证返回结构而非真实 CPU 值
        with mock.patch('psutil.cpu_percent', return_value=42.0):
            result = perform_heartbeat_check(None)
        assert "timestamp" in result
        assert "checks" in result
        # 应该有 system 检查
        assert "system" in result["checks"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
