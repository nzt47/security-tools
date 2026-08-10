"""定时调度系统测试 -- 测试 scheduling.py 的 Scheduler 类

覆盖范围：
- 任务添加、列出、取消、暂停、恢复
- 任务生命周期管理
- 持久化（保存/加载）
- cron 表达式验证

注意：为避免 schedule 库在 Windows 下的不稳定行为，使用 mock 替代实际的
_register_with_schedule / _unregister_from_schedule 调用。
"""
import os
import json
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from agent.scheduling import Scheduler


# ════════════════════════════════════════════════════════════════════════════════
#  任务添加测试
# ════════════════════════════════════════════════════════════════════════════════

class TestAddTask:
    """Scheduler.add_task 测试"""

    def test_add_simple_task(self):
        """添加最简单的定时任务"""
        sched = Scheduler()
        with patch.object(sched, "_register_with_schedule"), \
             patch.object(sched, "save_to_file"):
            result = sched.add_task(name="测试任务", interval_minutes=5)
        assert result["ok"] is True
        assert result["task"]["name"] == "测试任务"
        assert result["task"]["interval_minutes"] == 5
        assert result["task"]["enabled"] is True
        assert result["task"]["paused"] is False
        assert "id" in result["task"]
        assert result["task"]["id"].startswith("task_")
        assert result["task"]["run_count"] == 0

    def test_add_task_with_cron(self):
        """使用 cron 表达式创建任务"""
        sched = Scheduler()
        with patch.object(sched, "_register_with_schedule"), \
             patch.object(sched, "save_to_file"):
            result = sched.add_task(name="定时备份", cron_expr="0 9 * * *")
        assert result["ok"] is True
        assert result["task"]["cron_expr"] == "0 9 * * *"

    def test_add_task_with_action(self):
        """带 action 和 params 的任务"""
        sched = Scheduler()
        with patch.object(sched, "_register_with_schedule"), \
             patch.object(sched, "save_to_file"):
            result = sched.add_task(
                name="执行命令",
                action="run_shell_command",
                params={"command": "echo hello"},
                interval_minutes=10,
            )
        assert result["ok"] is True
        assert result["task"]["action"] == "run_shell_command"
        assert result["task"]["params"] == {"command": "echo hello"}

    def test_add_task_empty_name(self):
        """空名称应返回错误"""
        sched = Scheduler()
        result = sched.add_task(name="", interval_minutes=5)
        assert result["ok"] is False
        assert "error" in result

    def test_add_task_no_schedule(self):
        """未提供间隔或 cron 应返回错误"""
        sched = Scheduler()
        result = sched.add_task(name="无调度")
        assert result["ok"] is False
        assert "error" in result

    def test_add_task_disabled(self):
        """创建时即禁用的任务"""
        sched = Scheduler()
        with patch.object(sched, "_register_with_schedule"), \
             patch.object(sched, "save_to_file"):
            result = sched.add_task(name="禁用任务", interval_minutes=5, enabled=False)
        assert result["ok"] is True
        assert result["task"]["enabled"] is False

    def test_add_multiple_tasks_unique_ids(self):
        """添加多个任务应有唯一 ID"""
        sched = Scheduler()
        with patch.object(sched, "_register_with_schedule"), \
             patch.object(sched, "save_to_file"):
            r1 = sched.add_task(name="任务A", interval_minutes=1)
            r2 = sched.add_task(name="任务B", interval_minutes=2)
        assert r1["ok"] and r2["ok"]
        assert r1["task"]["id"] != r2["task"]["id"]


# ════════════════════════════════════════════════════════════════════════════════
#  任务列表与查询测试
# ════════════════════════════════════════════════════════════════════════════════

class TestListTasks:
    """任务列表与查询"""

    def test_list_empty(self):
        """无任务时的空列表"""
        sched = Scheduler()
        result = sched.get_tasks()
        assert result["ok"] is True
        assert result["tasks"] == []
        assert result["total"] == 0

    def test_list_multiple(self):
        """列出多个任务"""
        sched = Scheduler()
        with patch.object(sched, "_register_with_schedule"), \
             patch.object(sched, "save_to_file"):
            sched.add_task(name="T1", interval_minutes=1)
            sched.add_task(name="T2", interval_minutes=2)
            sched.add_task(name="T3", interval_minutes=3)

        result = sched.get_tasks()
        assert result["ok"] is True
        assert result["total"] == 3
        names = {t["name"] for t in result["tasks"]}
        assert names == {"T1", "T2", "T3"}

    def test_get_single_task(self):
        """查询单个任务"""
        sched = Scheduler()
        with patch.object(sched, "_register_with_schedule"), \
             patch.object(sched, "save_to_file"):
            add_result = sched.add_task(name="查询测试", interval_minutes=5)
        task_id = add_result["task"]["id"]

        task = sched.get_task(task_id)
        assert task is not None
        assert task["name"] == "查询测试"

    def test_get_nonexistent_task(self):
        """查询不存在的任务"""
        sched = Scheduler()
        task = sched.get_task("task_nonexistent")
        assert task is None


# ════════════════════════════════════════════════════════════════════════════════
#  任务取消测试
# ════════════════════════════════════════════════════════════════════════════════

class TestCancelTask:
    """取消任务"""

    def test_cancel_existing(self):
        """取消存在的任务"""
        sched = Scheduler()
        with patch.object(sched, "_register_with_schedule"), \
             patch.object(sched, "save_to_file"):
            add_result = sched.add_task(name="可取消", interval_minutes=5)
        task_id = add_result["task"]["id"]

        with patch.object(sched, "_unregister_from_schedule"), \
             patch.object(sched, "save_to_file"):
            result = sched.remove_task(task_id)
        assert result["ok"] is True
        assert result["cancelled"] is True

        # 取消后列表不再包含
        list_result = sched.get_tasks()
        ids = {t["id"] for t in list_result["tasks"]}
        assert task_id not in ids

    def test_cancel_nonexistent(self):
        """取消不存在的任务"""
        sched = Scheduler()
        result = sched.remove_task("task_fake")
        assert result["ok"] is False
        assert "error" in result


# ════════════════════════════════════════════════════════════════════════════════
#  暂停与恢复测试
# ════════════════════════════════════════════════════════════════════════════════

class TestPauseResume:
    """暂停与恢复任务"""

    def test_pause_task(self):
        """暂停任务"""
        sched = Scheduler()
        with patch.object(sched, "_register_with_schedule"), \
             patch.object(sched, "save_to_file"):
            add_result = sched.add_task(name="可暂停", interval_minutes=10)
        task_id = add_result["task"]["id"]

        with patch.object(sched, "_unregister_from_schedule"), \
             patch.object(sched, "save_to_file"):
            result = sched.pause_task(task_id)
        assert result["ok"] is True
        assert result["paused"] is True

        task = sched.get_task(task_id)
        assert task["paused"] is True

    def test_resume_task(self):
        """恢复任务"""
        sched = Scheduler()
        with patch.object(sched, "_register_with_schedule"), \
             patch.object(sched, "save_to_file"):
            add_result = sched.add_task(name="可恢复", interval_minutes=10)
        task_id = add_result["task"]["id"]

        with patch.object(sched, "_unregister_from_schedule"), \
             patch.object(sched, "save_to_file"):
            sched.pause_task(task_id)
        with patch.object(sched, "_register_with_schedule"), \
             patch.object(sched, "save_to_file"):
            result = sched.resume_task(task_id)
        assert result["ok"] is True
        assert result["resumed"] is True

        task = sched.get_task(task_id)
        assert task["paused"] is False

    def test_pause_nonexistent(self):
        """暂停不存在的任务"""
        sched = Scheduler()
        result = sched.pause_task("task_fake")
        assert result["ok"] is False

    def test_resume_nonexistent(self):
        """恢复不存在的任务"""
        sched = Scheduler()
        result = sched.resume_task("task_fake")
        assert result["ok"] is False


# ════════════════════════════════════════════════════════════════════════════════
#  任务生命周期测试
# ════════════════════════════════════════════════════════════════════════════════

class TestTaskLifecycle:
    """任务完整生命周期"""

    def test_full_lifecycle(self):
        """创建→查看→暂停→恢复→取消 完整流程"""
        sched = Scheduler()

        # 1. 创建
        with patch.object(sched, "_register_with_schedule"), \
             patch.object(sched, "save_to_file"):
            r = sched.add_task(name="生命周期测试", interval_minutes=5, action="test_action")
        assert r["ok"]
        task_id = r["task"]["id"]

        # 2. 查看
        task = sched.get_task(task_id)
        assert task is not None
        assert task["name"] == "生命周期测试"
        assert task["enabled"] is True

        # 3. 暂停
        with patch.object(sched, "_unregister_from_schedule"), \
             patch.object(sched, "save_to_file"):
            r = sched.pause_task(task_id)
        assert r["ok"]
        task = sched.get_task(task_id)
        assert task["paused"] is True

        # 4. 恢复
        with patch.object(sched, "_register_with_schedule"), \
             patch.object(sched, "save_to_file"):
            r = sched.resume_task(task_id)
        assert r["ok"]
        task = sched.get_task(task_id)
        assert task["paused"] is False

        # 5. 列出（应仍在列表中）
        tasks = sched.get_tasks()
        assert any(t["id"] == task_id for t in tasks["tasks"])

        # 6. 取消
        with patch.object(sched, "_unregister_from_schedule"), \
             patch.object(sched, "save_to_file"):
            r = sched.remove_task(task_id)
        assert r["ok"]
        task = sched.get_task(task_id)
        assert task is None

    def test_created_at_timestamp(self):
        """任务应有创建时间戳"""
        sched = Scheduler()
        with patch.object(sched, "_register_with_schedule"), \
             patch.object(sched, "save_to_file"):
            result = sched.add_task(name="时间戳", interval_minutes=1)
        assert "created_at" in result["task"]
        assert result["task"]["created_at"]


# ════════════════════════════════════════════════════════════════════════════════
#  持久化测试
# ════════════════════════════════════════════════════════════════════════════════

class TestPersistence:
    """持久化（保存/加载）测试"""

    def test_save_and_load(self, tmp_path, monkeypatch):
        """保存任务到文件，从文件加载"""
        import agent.scheduling as sch_mod
        original_data_dir = sch_mod.DATA_DIR
        schedules_file = tmp_path / "schedules.json"
        sch_mod.DATA_DIR = tmp_path
        monkeypatch.setattr(sch_mod, "SCHEDULES_FILE", schedules_file)

        try:
            sched1 = Scheduler()
            with patch.object(sched1, "_register_with_schedule"), \
                 patch.object(sched1, "save_to_file"):
                sched1.add_task(name="持久化测试1", interval_minutes=5)
                sched1.add_task(name="持久化测试2", cron_expr="*/10 * * * *")
            sched1.save_to_file()

            # 验证文件存在
            assert schedules_file.exists()

            # 使用新实例加载
            sched2 = Scheduler()
            with patch.object(sched2, "_register_with_schedule"):
                sched2.load_from_file()

            tasks = sched2.get_tasks()
            assert tasks["total"] == 2
            names = {t["name"] for t in tasks["tasks"]}
            assert "持久化测试1" in names
            assert "持久化测试2" in names
        finally:
            sch_mod.DATA_DIR = original_data_dir

    def test_load_empty_file(self, tmp_path, monkeypatch):
        """加载不存在的数据文件不报错"""
        import agent.scheduling as sch_mod
        original_data_dir = sch_mod.DATA_DIR
        monkeypatch.setattr(sch_mod, "SCHEDULES_FILE", tmp_path / "nonexistent.json")
        sch_mod.DATA_DIR = tmp_path

        try:
            sched = Scheduler()
            sched.load_from_file()  # 不应抛出异常
            tasks = sched.get_tasks()
            assert tasks["total"] == 0
        finally:
            sch_mod.DATA_DIR = original_data_dir


# ════════════════════════════════════════════════════════════════════════════════
#  Cron 表达式验证测试
# ════════════════════════════════════════════════════════════════════════════════

class TestCronValidation:
    """cron 表达式验证"""

    def test_valid_cron_every_5_minutes(self):
        """*/5 * * * *"""
        assert Scheduler.validate_cron_expr("*/5 * * * *") is True

    def test_valid_cron_every_hour(self):
        """0 * * * *"""
        assert Scheduler.validate_cron_expr("0 * * * *") is True

    def test_valid_cron_daily(self):
        """0 9 * * * — 每天 9:00"""
        assert Scheduler.validate_cron_expr("0 9 * * *") is True

    def test_valid_cron_weekly(self):
        """0 9 * * 1 — 每周一 9:00"""
        assert Scheduler.validate_cron_expr("0 9 * * 1") is True

    def test_valid_cron_comma_list(self):
        """0,30 * * * *"""
        assert Scheduler.validate_cron_expr("0,30 * * * *") is True

    def test_invalid_cron_too_few_fields(self):
        """字段不足"""
        assert Scheduler.validate_cron_expr("0 9 * *") is False

    def test_invalid_cron_too_many_fields(self):
        """字段过多"""
        assert Scheduler.validate_cron_expr("0 9 * * * * extra") is False

    def test_invalid_cron_empty(self):
        """空字符串"""
        assert Scheduler.validate_cron_expr("") is False

    def test_invalid_cron_text(self):
        """非法的 cron 文本"""
        assert Scheduler.validate_cron_expr("every day at 9") is False
