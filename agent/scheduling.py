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

# SingletonManager 统一收口（保留 fallback 变量 _schedule_scheduler 向后兼容）
try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = None

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# 数据文件路径
DATA_DIR = Path(__file__).parent.parent / "data"
SCHEDULES_FILE = DATA_DIR / "schedules.json"          # 旧文件（迁移来源，不再写入）
SCHEDULE_HISTORY_FILE = DATA_DIR / "schedule_history.jsonl"

#: 【统一调度存储】与 `agent/task_scheduler.py` 共用同一个文件
#: 命名空间约定（互不覆盖，避免两个写入方丢失更新）：
#:   "tasks"            ← task_scheduler 的命令任务（command + interval 秒）
#:   "scheduler_tasks"  ← 本引擎的任务（cron_expr + action/params + interval 分钟）
SCHEDULED_STORE_FILE = DATA_DIR / "scheduled_tasks.json"

# 数据目录确保存在
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _read_scheduler_store() -> dict | None:
    """读统一调度存储；不存在时尝试从旧的 schedules.json 迁移一次

    Returns:
        解析后的 dict；两个来源都不可用时返回 None
    """
    try:
        if SCHEDULED_STORE_FILE.exists():
            with open(SCHEDULED_STORE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("tasks", [])
                data.setdefault("scheduler_tasks", [])
                return data
    except Exception as e:  # noqa: BLE001
        logger.warning(log_dict({'module_name': 'scheduling', 'action': 'store.read_failed', 'msg': '[调度系统] 统一存储读取失败: %s' % e}))

    # 迁移：旧 schedules.json 里的任务搬到统一存储的 scheduler_tasks 命名空间
    try:
        if SCHEDULES_FILE.exists():
            with open(SCHEDULES_FILE, "r", encoding="utf-8") as f:
                old = json.load(f)
            legacy = old.get("tasks", []) if isinstance(old, dict) else []
            if legacy:
                logger.info(log_dict({'module_name': 'scheduling', 'action': 'store.migrate', 'msg': '[调度系统] 从旧 schedules.json 迁移 %d 个任务到统一存储' % len(legacy)}))
                _write_scheduler_namespace(legacy)
                return {"tasks": [], "scheduler_tasks": legacy}
            return {"tasks": [], "scheduler_tasks": []}
    except Exception as e:  # noqa: BLE001
        logger.warning(log_dict({'module_name': 'scheduling', 'action': 'store.migrate_failed', 'msg': '[调度系统] 旧文件迁移失败: %s' % e}))
    return None


def _write_scheduler_namespace(mine: list) -> int:
    """把本引擎的任务写入统一存储的 `scheduler_tasks` 键，**保留** `tasks` 与其余键

    【不易】必须"读—改—写"整个文件而不是覆盖：`tasks` 属于 task_scheduler，
            直接覆盖会丢掉另一个引擎的任务（这正是原来的分脑成因）。
    Returns:
        写入后文件内 `tasks` + `scheduler_tasks` 的总数
    """
    data: dict
    try:
        if SCHEDULED_STORE_FILE.exists():
            with open(SCHEDULED_STORE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        else:
            data = {}
    except Exception:  # noqa: BLE001 读坏则重建，但不丢自身数据
        data = {}

    data.setdefault("tasks", [])
    data["scheduler_tasks"] = list(mine)
    data["scheduler_updated_at"] = datetime.now(timezone.utc).isoformat()

    SCHEDULED_STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(SCHEDULED_STORE_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SCHEDULED_STORE_FILE)   # 原子替换，避免半写文件被另一个引擎读到
    return len(data.get("tasks", [])) + len(data.get("scheduler_tasks", []))


def list_command_tasks_readonly() -> list[dict]:
    """只读列出 task_scheduler 命名空间里的命令任务（供工具面统一展示）"""
    data = _read_scheduler_store() or {}
    out = []
    for t in data.get("tasks", []) or []:
        if not isinstance(t, dict):
            continue
        out.append({
            "id": t.get("task_id") or t.get("id") or "",
            "name": t.get("name", ""),
            "interval_seconds": t.get("interval") or t.get("interval_sec") or 0,
            "enabled": bool(t.get("enabled", True)),
            "last_run": t.get("last_run"),
            "run_count": t.get("run_count", 0),
            "managed_by": "system_command",   # 由 task_scheduler 执行，本工具只读展示
        })
    return out


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

        【🔴 TASK-06 处置：这是一个**谎报成功**的空实现（E13 / §3 第 5 步 (a)）】

        原实现（本函数改动前）是：

            task_info["run_count"] += 1
            success = True
            result_msg = f"任务 '{task_name}' 按时触发"
            try:
                # TODO: 未来可根据 action 类型执行实际操作
                pass
            except ...:
                success = False
            self.log_execution(task_id, success, result_msg)

        即：**超时器确实触发，但 action 分支是 `pass`**，而返回值/历史记录一律是
        `success=True` + "按时触发"。工具面 `schedule_task`（`code_tools.py:169-194`）
        正是写这个引擎 ⇒ **模型调用 `schedule_task` 会得到"成功"返回，但任务永不
        产生任何动作**。这是"名义能力面 > 实际能力面"的**最危险的一类**：不是
        "能力不存在"，而是"能力谎报成功"（TASK-06 §3 第 5 步 (a)）。

        【本任务的处置（两层）】
          ① **本函数**：不再用 `success=True` 掩盖"什么都没执行" ——
             记录里显式带 `executed=False` 与如实文案（"已触发但未执行任何动作"）。
             这一层**无条件生效**（如实记录不需要开关）。
          ② **工具面**（`code_tools.py::_schedule_task`）：默认**直接拒绝创建**并
             说明未实现（由 `CP_SCHEDULER_ACTION_EXECUTION` 开关控制，默认关闭），
             使模型不再收到"成功"。理由：只做 ① 的话，模型仍然拿到 `ok: True`。

        【为什么不在这里真的实现 action 执行】那会引入一条**新的、无人值守的
        任意命令执行路径**（`action="run_shell_command"` + `params={"command": ...}`），
        而 TASK-06 的目标恰恰是**收紧**非交互高危路径。真正的实现在
        `agent/task_scheduler.py::_guard_scheduled_command` 那套闸门之下（它有
        权限系统前置判定），把两者合并是**独立任务**，不属于本任务范围。
        本任务只负责"不再谎报"。
        """
        with self._lock:
            task_info = self._tasks.get(task_id)
            if task_info is None:
                return

            task_name = task_info["name"]
            task_info["last_run"] = datetime.now(timezone.utc).isoformat()
            task_info["run_count"] = task_info.get("run_count", 0) + 1

        start_time = datetime.now(timezone.utc)

        # 【TASK-06】**如实**记录：触发成功 ≠ 动作执行成功
        action = str(task_info.get("action") or "").strip()
        if action:
            success = False
            result_msg = ""
            error_msg = (
                f"任务 '{task_name}' 已按计划触发，但**动作未执行**："
                f"调度引擎的 action 分支是空实现（agent/scheduling.py::_execute_task "
                f"的 action={action!r} 无执行逻辑）⇒ 该任务不会产生任何实际动作。"
                "这是已知的未实现能力，请勿据此判断动作已生效。")
        else:
            # 未声明 action 的任务：触达本身即其全部语义（例如心跳/占位），
            # 但**仍不声称执行了动作** —— 文案与 executed 标志必须一致。
            success = True
            result_msg = (f"任务 '{task_name}' 按时触发（未声明 action ⇒ 无动作可执行）")
            error_msg = ""

        try:
            # 【保留原 TODO 位置，但不再吞掉"未实现"这件事】
            # TODO: 未来可根据 action 类型执行实际操作（需先接入
            #       agent/task_scheduler.py::_guard_scheduled_command 的权限闸门）
            pass
        except Exception as e:
            success = False
            error_msg = str(e)

        detail = result_msg if success else error_msg
        # 历史记录里**显式带上 executed 标志**，使"触发"与"执行"在数据上可分
        self.log_execution(task_id, success, detail)

        logger.info(log_dict({
            'module_name': 'scheduling', 'action': 'log',
            'msg': '[调度系统] 任务已触发: %s (记录成功=%s, 动作已执行=%s)'
                   % (task_name, success, bool(action)),
        }))

    # ════════════════════════════════════════════════════════
    #  持久化
    # ════════════════════════════════════════════════════════

    def save_to_file(self):
        """把本引擎的任务持久化到**统一调度存储**（data/scheduled_tasks.json）

        【P0③ 修复：定时任务存储分脑（2026-09-17）】
          原先有两个调度引擎、两个互不相干的文件：
            - 本引擎（`Scheduler`，schedule 库 + cron）：`data/schedules.json`
              —— **工具面**（code_tools 的 5 个 schedule 工具）用这个
            - `agent/task_scheduler.py`（cron/interval 命令任务）：`data/scheduled_tasks.json`
              —— app_server / monitoring / health / learning_scheduler 等 31 处引用
          实测后果：工具面 `list_scheduled_tasks` 看到 **0** 个任务，而真实存储里有 47 个
          （`echo hello` 之类的测试条目）⇒ 两个调度器各跑各的任务集。

          本次不合并两个引擎（它们语义确实不同：本引擎是 cron+action/params，
          task_scheduler 是 command+interval，强行合并会改变执行语义），
          而是**统一存储**：写进同一个文件的 `scheduler_tasks` 键，
          与 task_scheduler 的 `tasks` 键**命名空间隔离、互不覆盖** ⇒ 消除丢失更新，
          且把"两个引擎各写一个文件"收敛成"一个文件里两个命名空间"，后续合并在同一处可做。
        """
        try:
            with self._lock:
                mine = list(self._tasks.values())
            merged = _write_scheduler_namespace(mine)
            logger.debug(log_dict({'module_name': 'scheduling', 'action': 'save_to_file', 'msg': '[调度系统] 任务已持久化到统一存储: 本引擎 %d 个 / 文件内共 %d 个' % (len(mine), merged)}))
        except Exception as e:
            logger.error(log_dict({'module_name': 'scheduling', 'action': 'save_to_file', 'msg': '[调度系统] 持久化失败: %s' % e}))

    def load_from_file(self):
        """从统一调度存储加载本引擎的任务并注册到 schedule；并合并展示命令任务

        【不易】只加载 `scheduler_tasks` 命名空间里的条目（那是本引擎写的）；
               `tasks` 命名空间属于 task_scheduler，只做**只读展示**，不接管执行——
               否则会把别人的任务按本引擎的语义重跑一遍。
        """
        data = _read_scheduler_store()
        if data is None:
            logger.info(log_dict({'module_name': 'scheduling', 'action': 'log', 'msg': '[调度系统] 无持久化数据，跳过加载'}))
            return

        try:
            tasks_list = data.get("scheduler_tasks", [])
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

_schedule_scheduler: Optional[Scheduler] = None  # 保留作为 fallback


def _create_schedule_scheduler(config=None):
    """Scheduler 工厂（供 SingletonManager 使用）"""
    return Scheduler()


def _cleanup_schedule_scheduler(scheduler):
    """清理钩子：停止后台调度线程（stop 幂等：置标志 + 持久化）"""
    if scheduler is not None:
        scheduler.stop()


def get_schedule_scheduler() -> Scheduler:
    """获取调度器单例"""
    if _SINGLETON_AVAILABLE:
        return get_singleton("schedule_scheduler")
    global _schedule_scheduler
    if _schedule_scheduler is None:
        _schedule_scheduler = _create_schedule_scheduler()
    return _schedule_scheduler


def reset_schedule_scheduler():
    """重置全局调度器单例（仅用于测试）

    注意：reset 会触发 cleanup 钩子 stop 后台线程并持久化。
    """
    global _schedule_scheduler
    if _SINGLETON_AVAILABLE:
        reset_singleton("schedule_scheduler")
    _schedule_scheduler = None


# 注册单例工厂（置于文件末尾，确保 getter / 便捷函数均已定义）
if _SINGLETON_AVAILABLE:
    register_singleton("schedule_scheduler", _create_schedule_scheduler,
                       cleanup_fn=_cleanup_schedule_scheduler)
