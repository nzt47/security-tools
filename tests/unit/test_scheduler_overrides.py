"""调度器任务可见性与人工覆盖（2026-10-17）。

背景：`/api/scheduler/tasks` 只列文件登记的命令任务，真正跑 cron 的 python 任务
（周报、日志清理、零 token 维护…）不在列表里，「定时任务」页因此长期空白。
本轮把运行态任务暴露到 UI，并允许按名启停 / 改「每天 时:分」。

守卫目标：
1. 覆盖文件按**任务名**生效（id 每次启动重生成，不能当键）；
2. 非法时刻不会写坏任务，也不会写进文件；
3. 改时刻会清 `last_run`（否则当天新时刻会被"今天已跑过"挡掉）；
4. 重启后重新注册同名任务时，仍取到用户改过的时刻/启停。
"""

import json

import agent.task_scheduler as ts


def _write_overrides(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TestOverrideFile:
    def test_文件不存在时返回空(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ts, "OVERRIDES_FILE", tmp_path / "nope.json")
        assert ts._load_overrides() == {}

    def test_坏文件不抛且返回空(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(ts, "OVERRIDES_FILE", bad)
        assert ts._load_overrides() == {}

    def test_套用启停与时刻(self, tmp_path, monkeypatch):
        f = tmp_path / "ov.json"
        _write_overrides(f, {"任务A": {"enabled": False, "hour": 10, "minute": 5}})
        monkeypatch.setattr(ts, "OVERRIDES_FILE", f)
        task = ts._apply_override({
            "name": "任务A", "enabled": True,
            "cron": {"day_of_week": None, "hour": 9, "minute": 20},
        })
        assert task["enabled"] is False
        assert task["cron"]["hour"] == 10
        assert task["cron"]["minute"] == 5

    def test_非法时刻被忽略(self, tmp_path, monkeypatch):
        f = tmp_path / "ov.json"
        _write_overrides(f, {"任务A": {"hour": 99, "minute": -1}})
        monkeypatch.setattr(ts, "OVERRIDES_FILE", f)
        task = ts._apply_override({
            "name": "任务A", "enabled": True,
            "cron": {"day_of_week": None, "hour": 9, "minute": 20},
        })
        assert task["cron"]["hour"] == 9
        assert task["cron"]["minute"] == 20

    def test_未登记的任务不受影响(self, tmp_path, monkeypatch):
        f = tmp_path / "ov.json"
        _write_overrides(f, {"别人": {"enabled": False}})
        monkeypatch.setattr(ts, "OVERRIDES_FILE", f)
        task = ts._apply_override({
            "name": "任务A", "enabled": True,
            "cron": {"day_of_week": None, "hour": 9, "minute": 20},
        })
        assert task["enabled"] is True


class TestTaskManagement:
    def _make(self, tmp_path, monkeypatch):
        f = tmp_path / "ov.json"
        monkeypatch.setattr(ts, "OVERRIDES_FILE", f)
        sched = ts.TaskScheduler()
        sched.add_cron_task("维护·示例", func=lambda: None, hour=9, minute=20,
                            zero_token=True, owner="agent/maintenance/zero_token_jobs.py")
        return sched, f

    def test_改时刻并持久化(self, tmp_path, monkeypatch):
        sched, f = self._make(tmp_path, monkeypatch)
        assert sched.update_task_schedule("维护·示例", 13, 30) is True
        task = sched.find_task_by_name("维护·示例")
        assert task["cron"]["hour"] == 13 and task["cron"]["minute"] == 30
        assert task["last_run"] is None
        saved = json.loads(f.read_text(encoding="utf-8"))
        assert saved["维护·示例"] == {"hour": 13, "minute": 30}

    def test_越界时刻被拒且不落盘(self, tmp_path, monkeypatch):
        sched, f = self._make(tmp_path, monkeypatch)
        assert sched.update_task_schedule("维护·示例", 24, 0) is False
        assert sched.update_task_schedule("维护·示例", 9, 60) is False
        task = sched.find_task_by_name("维护·示例")
        assert task["cron"]["hour"] == 9
        assert not f.exists() or "维护·示例" not in json.loads(f.read_text(encoding="utf-8"))

    def test_改名不存在的任务返回False(self, tmp_path, monkeypatch):
        sched, _ = self._make(tmp_path, monkeypatch)
        assert sched.update_task_schedule("没有这个任务", 10, 0) is False

    def test_按名启停并持久化(self, tmp_path, monkeypatch):
        sched, f = self._make(tmp_path, monkeypatch)
        assert sched.set_task_enabled_by_name("维护·示例", False) is True
        assert sched.find_task_by_name("维护·示例")["enabled"] is False
        assert json.loads(f.read_text(encoding="utf-8"))["维护·示例"]["enabled"] is False

    def test_重启后仍按覆盖注册(self, tmp_path, monkeypatch):
        sched, f = self._make(tmp_path, monkeypatch)
        sched.update_task_schedule("维护·示例", 17, 20)
        sched.set_task_enabled_by_name("维护·示例", False)
        fresh = ts.TaskScheduler()
        fresh.add_cron_task("维护·示例", func=lambda: None, hour=9, minute=20)
        task = fresh.find_task_by_name("维护·示例")
        assert task["cron"]["hour"] == 17 and task["cron"]["minute"] == 20
        assert task["enabled"] is False

    def test_list_tasks带排程文案与零token(self, tmp_path, monkeypatch):
        sched, _ = self._make(tmp_path, monkeypatch)
        item = [t for t in sched.list_tasks() if t["name"] == "维护·示例"][0]
        assert item["schedule_text"] == "每天 09:20"
        assert item["zero_token"] is True
        assert item["owner"].endswith("zero_token_jobs.py")


class TestScheduleText:
    def test_每天与每周(self):
        assert ts._schedule_text({"cron": {"day_of_week": None, "hour": 9, "minute": 5}}) == "每天 09:05"
        assert ts._schedule_text({"cron": {"day_of_week": 0, "hour": 9, "minute": 0}}) == "每周一 09:00"

    def test_间隔与命令(self):
        assert ts._schedule_text({"interval": 300}) == "每 300 秒"
        assert ts._schedule_text({"type": "system_command", "interval": 60}) == "每 60 秒（命令）"
