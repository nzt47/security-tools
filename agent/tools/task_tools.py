"""定时任务管理工具——从 system_tools.py 拆出

包含：定时任务的加载、保存、列表、创建、删除、开关等操作。
"""
import os
import json
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SCHEDULED_TASKS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "scheduled_tasks.json")

# 受控写入能力标识（TASK-S2-03 §6.6 escape.capability_id：本文件的本应写入路径）
_GOVERNED_CAPABILITY = "cp.governed.scheduled_task.write"


def _record_governed_write(writer: str, data: Optional[dict] = None) -> None:
    """登记一次受控写（TASK-S2-03 逃逸检测基线；best-effort，绝不阻断任务管理）

    同时登记当时的 ``task_ids`` 快照，供后续逃逸判定定位「被改的是哪个任务」。
    用户后续**手工编辑**该文件（未走本模块的 create/delete/toggle）将在下次读取时
    被 `detect_escapes()` 判定为逃逸（§3.8 任务生命周期「逃逸」）。
    """
    try:
        from agent.observability.escape import record_governed_write
        extra = None
        if isinstance(data, dict) and isinstance(data.get("tasks"), list):
            extra = {"task_ids": [str(t.get("id") or "") for t in data["tasks"]
                                  if isinstance(t, dict)]}
        record_governed_write(SCHEDULED_TASKS_FILE, writer=writer, extra=extra)
    except Exception:
        pass


def _detect_escapes() -> None:
    """检测受管任务文件被手工改动（TASK-S2-03；best-effort，只留痕不改写）"""
    try:
        from agent.observability.escape import detect_escapes
        detect_escapes([SCHEDULED_TASKS_FILE])
    except Exception:
        pass


def _load_tasks():
    try:
        with open(SCHEDULED_TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"tasks": []}


def _save_tasks(data):
    os.makedirs(os.path.dirname(SCHEDULED_TASKS_FILE), exist_ok=True)
    with open(SCHEDULED_TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_scheduled_tasks():
    """列出所有已注册的定时任务（读取前做一次逃逸检测）"""
    _detect_escapes()
    return _load_tasks()


def create_scheduled_task(name, command, interval_sec=60, enabled=True):
    """创建受控的定时任务（仅限白名单命令）"""
    # 白名单检查
    allowed = ["python", "echo", "dir", "type", "curl", "ping"]
    cmd_lower = command.lower()
    if not any(cmd_lower.startswith(a) for a in allowed):
        return {"ok": False, "error": f"命令不在白名单中。允许的命令: {', '.join(allowed)}"}

    data = _load_tasks()
    task = {
        "id": str(int(time.time() * 1000)),
        "name": name,
        "command": command,
        "interval_sec": interval_sec,
        "enabled": enabled,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "last_run": None,
        "run_count": 0,
    }
    data["tasks"].append(task)
    _save_tasks(data)
    _record_governed_write("create_scheduled_task", data)
    # 同步注册到运行中的调度器
    try:
        from agent.task_scheduler import get_scheduler
        scheduler = get_scheduler()
        if scheduler.running:
            scheduler.add_command_task(name, command, interval_sec, task["id"], enabled)
    except Exception:
        pass
    return {"ok": True, "task": task}


def delete_scheduled_task(task_id):
    """删除定时任务"""
    data = _load_tasks()
    before = len(data["tasks"])
    data["tasks"] = [t for t in data["tasks"] if t["id"] != task_id]
    _save_tasks(data)
    _record_governed_write("delete_scheduled_task", data)
    # 同步移除
    try:
        from agent.task_scheduler import get_scheduler
        scheduler = get_scheduler()
        if scheduler.running:
            scheduler.remove_task(task_id)
    except Exception:
        pass
    return {"ok": True, "deleted": before > len(data["tasks"])}


def toggle_scheduled_task(task_id, enabled):
    """启用/禁用定时任务"""
    data = _load_tasks()
    for t in data["tasks"]:
        if t["id"] == task_id:
            t["enabled"] = enabled
            _save_tasks(data)
            _record_governed_write("toggle_scheduled_task", data)
            # 同步状态
            try:
                from agent.task_scheduler import get_scheduler
                scheduler = get_scheduler()
                if scheduler.running:
                    scheduler.set_task_enabled(task_id, enabled)
            except Exception:
                pass
            return {"ok": True}
    return {"ok": False, "error": "任务不存在"}


__all__ = [
    "list_scheduled_tasks", "create_scheduled_task",
    "delete_scheduled_task", "toggle_scheduled_task",
]
