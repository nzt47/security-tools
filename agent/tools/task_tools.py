"""定时任务管理工具——从 system_tools.py 拆出

包含：定时任务的加载、保存、列表、创建、删除、开关等操作。
"""
import os
import json
import time
import logging

logger = logging.getLogger(__name__)

SCHEDULED_TASKS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "scheduled_tasks.json")


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
    """列出所有已注册的定时任务"""
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
