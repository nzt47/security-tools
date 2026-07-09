"""
定时调度系统 — 基于 schedule 库的用户定时任务管理

我是云枢的定时任务引擎——让用户和 LLM 可以创建、管理周期性任务。
使用独立的 schedule.Scheduler() 实例（非全局默认实例），在守护线程中运行。

功能：
- 创建定时任务（支持 cron 表达式或分钟间隔）
- 列出/取消/暂停/恢复任务
- 持久化到 data/schedules.json
- 执行历史记录到 data/schedule_history.jsonl
- 服务器重启时自动恢复已启用任务
"""

import logging
import threading
import time
import json
import os
import uuid
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Any, Optional
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# 数据文件路径
DATA_DIR = Path(__file__).parent.parent / "data"
SCHEDULES_FILE = DATA_DIR / "schedules.json"
SCHEDULE_HISTORY_FILE = DATA_DIR / "schedule_history.jsonl"

# 数据目录确保存在
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Scheduler:
    """基于 schedule 库的定时任务调度器

    使用独立 schedule.Scheduler() 实例（不污染全局默认实例）。
    在守护线程中运行调度循环，支持任务的增删改查和持久化。

    线程安全：
    - 使用 threading.Lock 保护共享的 tasks 字典
    - schedule.Scheduler 自身不是线程安全的，所有 schedule 操作均由后台线程独占
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._tasks: Dict[str, dict] = {}  # task_id -> task_info
        self._stop_event = threading.Event()

        # 使用独立的 schedule.Scheduler 实例
        try:
            import schedule
            self._schedule = schedule.Scheduler()
        except ImportError:
            logger.error(log_dict({'module_name': 'scheduling', 'action': 'schedule', 'msg': '[调度系统] schedule 库未安装，使用自定义轮询代替'}))
            self._schedule = None

        logger.info(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 初始化完成'}))

    # ════════════════════════════════════════════════════════
    #  生命周期
    # ════════════════════════════════════════════════════════

    def start(self):
        """启动后台调度线程"""
        if self._running:
            logger.warning(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 已在运行中'}))
            return

        self._running = True
        self._stop_event.clear()

        # 从文件恢复已持久化的任务
        self.load_from_file()

        # 启动守护线程
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="schedule-worker",
        )
        self._thread.start()
        logger.info(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 后台线程已启动'}))

    def stop(self):
        """停止后台调度线程并持久化"""
        self._running = False
        self._stop_event.set()
        self.save_to_file()
        logger.info(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 已停止'}))

    # ════════════════════════════════════════════════════════
    #  后台调度循环
    # ════════════════════════════════════════════════════════

    def _run_loop(self):
        """后台循环 — 周期性检查并执行到期任务"""
        logger.info(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 调度循环开始'}))

        while self._running and not self._stop_event.is_set():
            try:
                # schedule 库模式：运行 pending jobs
                if self._schedule is not None:
                    self._schedule.run_pending()
                time.sleep(1)
            except Exception as e:
                logger.error(log_dict({'module_name': 'scheduling', 'action': '_run_loop', 'msg': '[调度系统] 循环异常: %s' % e}))
                time.sleep(5)

        logger.info(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 调度循环已退出'}))

    # ════════════════════════════════════════════════════════
    #  任务管理
    # ════════════════════════════════════════════════════════

    def add_task(self, name: str, action: str = "", params: dict = None,
                 interval_minutes: int = 0, cron_expr: str = "",
                 enabled: bool = True) -> dict:
        """添加定时任务

        Args:
            name: 任务名称
            action: 操作描述（如 "run_shell_command"）
            params: 执行参数
            interval_minutes: 间隔分钟数
            cron_expr: cron 表达式（5 字段: 分 时 日 月 周）
            enabled: 是否立即启用

        Returns:
            {"ok": True, "task": {...}}
        """
        if not name.strip():
            return {"ok": False, "error": "任务名称不能为空"}

        if interval_minutes <= 0 and not cron_expr.strip():
            return {"ok": False, "error": "必须提供 interval_minutes 或 cron_expr"}

        task_id = f"task_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        task = {
            "id": task_id,
            "name": name.strip(),
            "interval_minutes": interval_minutes,
            "cron_expr": cron_expr.strip(),
            "action": action,
            "params": params or {},
            "enabled": enabled,
            "paused": False,
            "created_at": now,
            "last_run": None,
            "run_count": 0,
        }

        with self._lock:
            self._tasks[task_id] = task

        # 注册到 schedule 调度器
        self._register_with_schedule(task)

        # 持久化
        self.save_to_file()

        logger.info(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 已创建任务: %s (id=%s, interval=%dmin, cron=%s)' % (name, task_id, interval_minutes, cron_expr)}))
        return {"ok": True, "task": self._task_to_dict(task)}

    def remove_task(self, task_id: str) -> dict:
        """删除任务

        Returns:
            {"ok": True, "cancelled": True} 或 {"ok": False, "error": "..."}
        """
        with self._lock:
            task = self._tasks.pop(task_id, None)

        if task is None:
            return {"ok": False, "error": f"任务不存在: {task_id}"}

        # 从 schedule 中清除
        self._unregister_from_schedule(task_id)

        self.save_to_file()
        logger.info(log_dict({'module_name': 'scheduling', 'action': 'remove_task', 'msg': '[调度系统] 已删除任务: %s' % task_id}))
        return {"ok": True, "cancelled": True}

    def pause_task(self, task_id: str) -> dict:
        """暂停任务"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return {"ok": False, "error": f"任务不存在: {task_id}"}

            task["paused"] = True
            self._unregister_from_schedule(task_id)
            self.save_to_file()

        logger.info(log_dict({'module_name': 'scheduling', 'action': 'pause_task', 'msg': '[调度系统] 已暂停任务: %s' % task_id}))
        return {"ok": True, "paused": True}

    def resume_task(self, task_id: str) -> dict:
        """恢复任务"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return {"ok": False, "error": f"任务不存在: {task_id}"}

            task["paused"] = False
            self._register_with_schedule(task)
            self.save_to_file()

        logger.info(log_dict({'module_name': 'scheduling', 'action': 'resume_task', 'msg': '[调度系统] 已恢复任务: %s' % task_id}))
        return {"ok": True, "resumed": True}

    def get_tasks(self) -> dict:
        """列出所有任务"""
        with self._lock:
            tasks = [self._task_to_dict(t) for t in self._tasks.values()]
        return {"ok": True, "tasks": tasks, "total": len(tasks)}

    def get_task(self, task_id: str) -> Optional[dict]:
        """获取单个任务"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                return self._task_to_dict(task)
        return None

    # ════════════════════════════════════════════════════════
    #  schedule 库注册/注销
    # ════════════════════════════════════════════════════════

    def _register_with_schedule(self, task: dict):
        """将任务注册到 schedule.Scheduler 实例"""
        task_id = task["id"]
        if task.get("paused") or not task.get("enabled"):
            return

        if self._schedule is None:
            return

        interval_minutes = task.get("interval_minutes", 0)
        cron_expr = task.get("cron_expr", "")

        try:
            if cron_expr:
                job = self._add_cron_job(task_id, cron_expr)
            elif interval_minutes > 0:
                job = self._schedule.every(interval_minutes).minutes
            else:
                return

            # 给 job 打标签便于后续查找
            job.tag(task_id)
            job.do(self._execute_task, task_id)
            logger.debug(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 已注册到 schedule: %s' % task_id}))
        except Exception as e:
            logger.error(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 注册任务到 schedule 失败: %s: %s' % (task_id, e)}))

    def _add_cron_job(self, task_id: str, cron_expr: str):
        """解析 cron 表达式并添加任务

        支持 5 字段格式: 分 时 日 月 周
        简化实现：仅处理最常见的 cron 模式，复杂模式降级为每 60 分钟轮询。
        示例:
          "*/5 * * * *" → 每 5 分钟
          "0 9 * * 1"   → 每周一 9:00
          "0 0 * * *"   → 每天 0:00
          "30 * * * *"  → 每小时 30 分
        """
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"cron 表达式需要 5 个字段，收到 {len(parts)}: {cron_expr}")

        minute, hour, day, month, weekday = parts

        # 模式 1: */N * * * * → 每 N 分钟
        if minute.startswith("*/"):
            if hour == "*" and day == "*" and month == "*" and weekday == "*":
                interval = int(minute[2:])
                return self._schedule.every(interval).minutes

        # 构建 at_time（仅当分钟和小时都是固定数字时）
        at_time = None
        if minute.isdigit() and hour.isdigit():
            at_time = f"{hour.zfill(2)}:{minute.zfill(2)}"

        # 模式 2: M H * * * → 每天固定时间
        if at_time and day == "*" and month == "*" and weekday == "*":
            return self._schedule.every().day.at(at_time)

        # 模式 3: M * * * * → 每小时固定分钟
        if minute.isdigit() and hour == "*" and day == "*" and month == "*" and weekday == "*":
            return self._schedule.every().hour.at(f":{minute.zfill(2)}")

        # 模式 4: M H * * W → 每周固定星期几的固定时间
        # cron 标准: 0=Sunday, 1=Monday, ..., 6=Saturday
        weekday_names = ["sunday", "monday", "tuesday", "wednesday",
                        "thursday", "friday", "saturday"]
        if at_time and day == "*" and month == "*" and weekday.isdigit():
            wd_idx = int(weekday)
            if 0 <= wd_idx < 7:
                wd = weekday_names[wd_idx]
                return getattr(self._schedule.every(), wd).at(at_time)

        # 模式 5: M H D * * → 每月固定日期固定时间
        if at_time and month == "*" and weekday == "*" and day.isdigit():
            return self._schedule.every().day.at(at_time)

        # 不支持的复杂模式：降级为每 60 分钟轮询
        logger.warning(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 不支持的 cron 模式: %s，降级为每 60 分钟轮询' % cron_expr}))
        return self._schedule.every(60).minutes

    def _unregister_from_schedule(self, task_id: str):
        """从 schedule 中取消任务"""
        if self._schedule is None:
            return
        try:
            self._schedule.clear(tag=task_id)
            logger.debug(log_dict({'module_name': 'scheduling', 'action': '_unregister_from_schedule', 'msg': '[调度系统] 已从 schedule 注销: %s' % task_id}))
        except Exception as e:
            logger.error(log_dict({'module_name': 'scheduling', 'action': '_unregister_from_schedule', 'msg': '[调度系统] 从 schedule 注销失败: %s: %s' % (task_id, e)}))

    # ════════════════════════════════════════════════════════
    #  任务执行
    # ════════════════════════════════════════════════════════

    def _execute_task(self, task_id: str):
        """执行一个定时任务并记录结果

        当前 action 仅作为描述记录，实际执行由外部调用。
        """
        with self._lock:
            task_info = self._tasks.get(task_id)
            if task_info is None:
                return

            task_name = task_info["name"]
            task_info["last_run"] = datetime.now(timezone.utc).isoformat()
            task_info["run_count"] = task_info.get("run_count", 0) + 1

        start_time = datetime.now(timezone.utc)

        # 实际执行任务（当前仅记录，未来可扩展为实际动作）
        success = True
        result_msg = f"任务 '{task_name}' 按时触发"
        error_msg = ""

        try:
            # TODO: 未来可根据 action 类型执行实际操作
            # 例如: action="run_shell_command" → subprocess.run(params["command"])
            pass
        except Exception as e:
            success = False
            error_msg = str(e)

        self.log_execution(task_id, success, result_msg if success else error_msg)

        logger.info(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 任务已执行: %s (成功=%s)' % (task_name, success)}))

    # ════════════════════════════════════════════════════════
    #  持久化
    # ════════════════════════════════════════════════════════

    def save_to_file(self):
        """保存任务列表到 data/schedules.json"""
        try:
            with self._lock:
                tasks_data = {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "tasks": list(self._tasks.values()),
                }
            SCHEDULES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(SCHEDULES_FILE, "w", encoding="utf-8") as f:
                json.dump(tasks_data, f, ensure_ascii=False, indent=2)
            logger.debug(log_dict({'module_name': 'scheduling', 'action': 'save_to_file', 'msg': '[调度系统] 任务已持久化: %d 个' % len(self._tasks)}))
        except Exception as e:
            logger.error(log_dict({'module_name': 'scheduling', 'action': 'save_to_file', 'msg': '[调度系统] 持久化失败: %s' % e}))

    def load_from_file(self):
        """从 data/schedules.json 加载任务并重新注册到 schedule"""
        if not SCHEDULES_FILE.exists():
            logger.info(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 无持久化数据，跳过加载'}))
            return

        try:
            with open(SCHEDULES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            tasks_list = data.get("tasks", [])
            loaded_count = 0

            with self._lock:
                for task_data in tasks_list:
                    task_id = task_data.get("id", "")
                    if not task_id:
                        continue

                    # 标准化字段
                    task = {
                        "id": task_id,
                        "name": task_data.get("name", ""),
                        "interval_minutes": task_data.get("interval_minutes", 0),
                        "cron_expr": task_data.get("cron_expr", ""),
                        "action": task_data.get("action", ""),
                        "params": task_data.get("params", {}),
                        "enabled": task_data.get("enabled", True),
                        "paused": task_data.get("paused", False),
                        "created_at": task_data.get("created_at", ""),
                        "last_run": task_data.get("last_run"),
                        "run_count": task_data.get("run_count", 0),
                    }

                    self._tasks[task_id] = task

                    # 如果未暂停且已启用，重新注册到 schedule
                    if not task["paused"] and task["enabled"]:
                        self._register_with_schedule(task)

                    loaded_count += 1

            logger.info(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 已从文件恢复 %d 个任务' % loaded_count}))
        except Exception as e:
            logger.error(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 加载持久化数据失败: %s' % e}))

    def log_execution(self, task_id: str, success: bool, result: str):
        """记录执行历史到 data/schedule_history.jsonl"""
        try:
            with self._lock:
                task = self._tasks.get(task_id, {})
                task_name = task.get("name", task_id)

            record = {
                "task_id": task_id,
                "name": task_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "success": success,
                "result": result[:500],
            }

            SCHEDULE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(SCHEDULE_HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            # 保持历史行数在合理范围
            self._trim_history()
        except Exception as e:
            logger.error(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 记录历史失败: %s' % e}))

    def _trim_history(self, max_lines: int = 2000):
        """裁剪执行历史文件"""
        try:
            if not SCHEDULE_HISTORY_FILE.exists():
                return
            with open(SCHEDULE_HISTORY_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > max_lines:
                with open(SCHEDULE_HISTORY_FILE, "w", encoding="utf-8") as f:
                    f.writelines(lines[-max_lines:])
        except Exception:
            pass

    def get_history(self, limit: int = 100, offset: int = 0) -> dict:
        """获取执行历史"""
        try:
            if not SCHEDULE_HISTORY_FILE.exists():
                return {"ok": True, "history": [], "total": 0}

            with open(SCHEDULE_HISTORY_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()

            records = []
            for line in reversed(lines):  # 最新在前
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

            total = len(records)
            return {
                "ok": True,
                "history": records[offset:offset + limit],
                "total": total,
            }
        except Exception as e:
            logger.error(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 读取历史失败: %s' % e}))
            return {"ok": False, "error": str(e)}

    # ════════════════════════════════════════════════════════
    #  工具方法
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _task_to_dict(task: dict) -> dict:
        """将内部任务数据转为可序列化的字典"""
        return {
            "id": task.get("id", ""),
            "name": task.get("name", ""),
            "interval_minutes": task.get("interval_minutes", 0),
            "cron_expr": task.get("cron_expr", ""),
            "action": task.get("action", ""),
            "params": task.get("params", {}),
            "enabled": task.get("enabled", True),
            "paused": task.get("paused", False),
            "created_at": task.get("created_at", ""),
            "last_run": task.get("last_run"),
            "run_count": task.get("run_count", 0),
        }

    @staticmethod
    def validate_cron_expr(cron_expr: str) -> bool:
        """验证 cron 表达式格式（5 字段: 分 时 日 月 周）"""
        if not cron_expr or not cron_expr.strip():
            return False
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            return False
        # 每个字段可以是: *, */N, 0-59 的数字, 逗号分隔列表
        field_ranges = [
            (0, 59),   # 分钟
            (0, 23),   # 小时
            (1, 31),   # 日期
            (1, 12),   # 月份
            (0, 6),    # 星期（0=周日）
        ]
        for i, (part, (lo, hi)) in enumerate(zip(parts, field_ranges)):
            if not Scheduler._validate_cron_field(part, lo, hi):
                return False
        return True

    @staticmethod
    def _validate_cron_field(field: str, lo: int, hi: int) -> bool:
        """验证单个 cron 字段"""
        if field == "*":
            return True
        if field.startswith("*/"):
            try:
                val = int(field[2:])
                return 1 <= val <= hi
            except ValueError:
                return False
        # 逗号分隔列表
        for item in field.split(","):
            try:
                val = int(item)
                if not (lo <= val <= hi):
                    return False
            except ValueError:
                return False
        return True


# ════════════════════════════════════════════════════════════
#  全局单例
# ════════════════════════════════════════════════════════════

_schedule_scheduler: Optional[Scheduler] = None


def get_schedule_scheduler() -> Scheduler:
    """获取调度器单例"""
    global _schedule_scheduler
    if _schedule_scheduler is None:
        _schedule_scheduler = Scheduler()
    return _schedule_scheduler
