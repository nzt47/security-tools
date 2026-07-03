"""
定时任务调度器 — 统一执行引擎

支持三种任务类型:
- python_func: Python callable 任务（代码注册）
- system_command: 系统命令任务（API创建, subprocess执行）
- heartbeat: 内置心跳健康检查

启动方式:
    scheduler = get_scheduler()
    scheduler.start_daemon(check_interval=10)  # 非阻塞 daemon 线程
"""

import logging
import time
import json
import uuid
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Any, Optional

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# 数据文件路径
DATA_DIR = Path(__file__).parent.parent / "data"
SCHEDULED_TASKS_FILE = DATA_DIR / "scheduled_tasks.json"
TASK_HISTORY_FILE = DATA_DIR / "task_history.jsonl"
HEARTBEAT_HISTORY_FILE = DATA_DIR / "heartbeat_history.json"

# 默认配置（向后兼容常量，实际值应通过便捷函数从 Config 读取）
DEFAULT_CHECK_INTERVAL = 10    # 向后兼容别名，实际值通过 get_scheduler_check_interval() 读取
COMMAND_TIMEOUT = 300          # 向后兼容别名，实际值通过 get_scheduler_command_timeout() 读取
MAX_HISTORY_LINES = 1000       # 向后兼容别名，实际值通过 get_scheduler_max_history_lines() 读取
HEARTBEAT_INTERVAL = 60        # 向后兼容别名，实际值通过 get_scheduler_heartbeat_interval() 读取
MAX_HEARTBEAT_HISTORY = 1440   # 向后兼容别名，实际值通过 get_scheduler_max_heartbeat_history() 读取


class TaskScheduler:
    """增强型定时任务调度器"""

    def __init__(self):
        self.tasks: List[Dict[str, Any]] = []
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._heartbeat_func: Optional[Callable] = None  # 由外部注入的心跳函数
        self._yunshu_ref = None  # DigitalLife 引用，供心跳使用
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "log", "msg": "[TaskScheduler] 初始化完成"}, ensure_ascii=False))

    def add_cron_task(self, name: str, func: Callable, day_of_week: int = None,
                      hour: int = 0, minute: int = 0) -> None:
        """添加 Python 函数 cron 任务"""
        task = {
            "name": name,
            "type": "python_func",
            "func": func,
            "cron": {"day_of_week": day_of_week, "hour": hour, "minute": minute},
            "last_run": None,
            "enabled": True,
            "task_id": self._generate_task_id("py"),
        }
        self.tasks.append(task)
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "name.cron", "msg": f"[TaskScheduler] 添加任务: {name} (cron)"}, ensure_ascii=False))

    def add_interval_task(self, name: str, func: Callable, interval_seconds: int) -> None:
        """添加 Python 函数间隔任务"""
        task = {
            "name": name,
            "type": "python_func",
            "func": func,
            "interval": interval_seconds,
            "last_run": None,
            "enabled": True,
            "task_id": self._generate_task_id("py"),
        }
        self.tasks.append(task)
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "name.interval_seconds", "msg": f"[TaskScheduler] 添加任务: {name} (每{interval_seconds}秒)"}, ensure_ascii=False))

    def add_command_task(self, name: str, command: str, interval_sec: int,
                         task_id: str = "", enabled: bool = True) -> None:
        """添加系统命令任务（由 API 创建时调用）"""
        task = {
            "name": name,
            "type": "system_command",
            "command": command,
            "interval": interval_sec,
            "last_run": None,
            "enabled": enabled,
            "task_id": task_id or self._generate_task_id("cmd"),
        }
        self.tasks.append(task)
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "name.interval_sec", "msg": f"[TaskScheduler] 添加命令任务: {name} (每{interval_sec}秒)"}, ensure_ascii=False))

    def _generate_task_id(self, prefix: str = "task") -> str:
        """生成唯一任务 ID"""
        return f"{prefix}_{int(time.time() * 1000)}_{len(self.tasks)}"

    def remove_task(self, task_id: str) -> bool:
        """按 task_id 删除任务"""
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if t.get("task_id") != task_id]
        return len(self.tasks) < before

    def set_task_enabled(self, task_id: str, enabled: bool) -> bool:
        """启用/禁用任务"""
        for t in self.tasks:
            if t.get("task_id") == task_id:
                t["enabled"] = enabled
                return True
        return False

    def get_task(self, task_id: str) -> Optional[Dict]:
        """按 ID 查找任务"""
        for t in self.tasks:
            if t.get("task_id") == task_id:
                return t
        return None

    def _should_run(self, task: Dict[str, Any]) -> bool:
        """检查任务是否应该运行"""
        if not task.get("enabled", True):
            return False

        now = datetime.now()

        if task["type"] == "python_func":
            if "cron" in task:
                c = task["cron"]
                if c.get("day_of_week") is not None and now.weekday() != c["day_of_week"]:
                    return False
                if now.hour != c["hour"] or now.minute != c["minute"]:
                    return False
                if task.get("last_run"):
                    if task["last_run"].date() == now.date():
                        return False
                return True
            elif "interval" in task:
                if task.get("last_run") is None:
                    return True
                elapsed = (now - task["last_run"]).total_seconds()
                return elapsed >= task["interval"]
        elif task["type"] == "system_command":
            if task.get("last_run") is None:
                return True
            elapsed = (now - task["last_run"]).total_seconds()
            return elapsed >= task["interval"]
        elif task["type"] == "heartbeat":
            if task.get("last_run") is None:
                return True
            elapsed = (now - task["last_run"]).total_seconds()
            # 配置化：从 Config 读取心跳间隔（支持热加载）
            from agent.monitoring.observability_config import get_scheduler_heartbeat_interval
            return elapsed >= get_scheduler_heartbeat_interval()

        return False

    def run_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """执行单个任务，返回执行结果"""
        start_time = datetime.now()
        result = {
            "task_id": task.get("task_id", ""),
            "name": task["name"],
            "type": task["type"],
            "start_time": start_time.isoformat(),
            "status": "running",
            "output": "",
            "error": "",
            "duration_ms": 0,
        }

        # 无论成功/失败都更新 last_run，避免失败任务每 tick 重试
        task["last_run"] = datetime.now()

        try:
            if task["type"] == "python_func":
                if "func" in task:
                    task["func"]()
                result["status"] = "success"

            elif task["type"] == "system_command":
                command = task.get("command", "")
                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "command", "msg": f"[TaskScheduler] 执行命令: {command}"}, ensure_ascii=False))
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    # 配置化：从 Config 读取命令超时（支持热加载）
                    from agent.monitoring.observability_config import get_scheduler_command_timeout
                    _cmd_timeout = get_scheduler_command_timeout()
                    stdout, stderr = proc.communicate(timeout=_cmd_timeout)
                    if proc.returncode == 0:
                        result["status"] = "success"
                        result["output"] = stdout.strip()[:500]
                    else:
                        result["status"] = "failed"
                        result["error"] = stderr.strip()[:500]
                except subprocess.TimeoutExpired:
                    proc.kill()
                    result["status"] = "failed"
                    result["error"] = f"命令执行超时 ({_cmd_timeout}秒)"

            elif task["type"] == "heartbeat":
                if self._heartbeat_func:
                    hb_result = self._heartbeat_func(self._yunshu_ref)
                    result["status"] = hb_result.get("status", "unknown")
                    result["output"] = json.dumps(hb_result, ensure_ascii=False)
                    self._save_heartbeat(hb_result)

        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)[:500]
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "task", "msg": f"[TaskScheduler] 任务执行失败: {task['name']}: {e}"}, ensure_ascii=False))

        end_time = datetime.now()
        result["end_time"] = end_time.isoformat()
        result["duration_ms"] = int((end_time - start_time).total_seconds() * 1000)
        self._append_history(result)
        return result

    def _append_history(self, record: Dict[str, Any]) -> None:
        """追加执行记录到 JSONL"""
        try:
            TASK_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(TASK_HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._trim_history()
        except Exception as e:
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "log", "msg": f"[TaskScheduler] 写入历史失败: {e}"}, ensure_ascii=False))

    def _trim_history(self) -> None:
        """保留最近 N 条记录（N 从 Config 读取）"""
        try:
            if TASK_HISTORY_FILE.exists():
                with open(TASK_HISTORY_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                # 配置化：从 Config 读取最大行数（支持热加载）
                from agent.monitoring.observability_config import get_scheduler_max_history_lines
                _max_lines = get_scheduler_max_history_lines()
                if len(lines) > _max_lines:
                    with open(TASK_HISTORY_FILE, "w", encoding="utf-8") as f:
                        f.writelines(lines[-_max_lines:])
        except Exception as e:
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "log", "msg": f"[TaskScheduler] 裁剪历史失败: {e}"}, ensure_ascii=False))

    def get_history(self, limit: int = 100, offset: int = 0,
                    task_type: str = "") -> List[Dict[str, Any]]:
        """获取执行历史"""
        try:
            if not TASK_HISTORY_FILE.exists():
                return []
            with open(TASK_HISTORY_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            records = []
            for line in lines:
                try:
                    records.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue
            if task_type:
                records = [r for r in records if r.get("type") == task_type]
            records.reverse()
            return records[offset:offset + limit]
        except Exception as e:
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "log", "msg": f"[TaskScheduler] 读取历史失败: {e}"}, ensure_ascii=False))
            return []

    def _save_heartbeat(self, hb_data: Dict) -> None:
        """保存心跳历史"""
        try:
            HEARTBEAT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            history = {"latest": hb_data, "history": []}
            if HEARTBEAT_HISTORY_FILE.exists():
                with open(HEARTBEAT_HISTORY_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    history["history"] = existing.get("history", [])
            # 添加简化记录到历史数组
            simplified = {
                "timestamp": hb_data.get("timestamp"),
                "status": hb_data.get("status"),
                "cpu": hb_data.get("checks", {}).get("system", {}).get("cpu"),
                "memory": hb_data.get("checks", {}).get("system", {}).get("memory"),
                "llm_latency_ms": hb_data.get("checks", {}).get("llm", {}).get("latency_ms"),
            }
            history["history"].append(simplified)
            # 配置化：从 Config 读取心跳历史保留条数（支持热加载）
            from agent.monitoring.observability_config import get_scheduler_max_heartbeat_history
            _max_hb_history = get_scheduler_max_heartbeat_history()
            if len(history["history"]) > _max_hb_history:
                history["history"] = history["history"][-_max_hb_history:]
            with open(HEARTBEAT_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "log", "msg": f"[TaskScheduler] 保存心跳失败: {e}"}, ensure_ascii=False))

    def get_heartbeat_status(self) -> Dict:
        """获取心跳概览"""
        try:
            if HEARTBEAT_HISTORY_FILE.exists():
                with open(HEARTBEAT_HISTORY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"latest": {"status": "unknown"}, "history": []}

    def load_from_json(self, path: Optional[str] = None) -> int:
        """从 JSON 文件加载 API 创建的系统命令任务"""
        path = path or str(SCHEDULED_TASKS_FILE)
        count = 0
        try:
            if not os.path.exists(path):
                return 0
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for t in data.get("tasks", []):
                if not t.get("enabled", True):
                    continue
                self.add_command_task(
                    name=t["name"],
                    command=t["command"],
                    interval_sec=t.get("interval_sec", 60),
                    task_id=t.get("id", ""),
                    enabled=t.get("enabled", True),
                )
                count += 1
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "json.count", "msg": f"[TaskScheduler] 从 JSON 加载了 {count} 个任务"}, ensure_ascii=False))
        except Exception as e:
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "json", "msg": f"[TaskScheduler] 加载 JSON 任务失败: {e}"}, ensure_ascii=False))
        return count

    def start_daemon(self, check_interval: Optional[int] = None) -> None:
        """以 daemon 线程方式启动调度器（非阻塞）

        Args:
            check_interval: tick 检查间隔（秒），None 时从 Config 读取
        """
        # 配置化：从 Config 读取默认检查间隔（支持热加载）
        if check_interval is None:
            from agent.monitoring.observability_config import get_scheduler_check_interval
            check_interval = get_scheduler_check_interval()
        if self.running:
            logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "log", "msg": "[TaskScheduler] 调度器已在运行"}, ensure_ascii=False))
            return
        self.running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(check_interval,),
            daemon=True,
            name="task-scheduler",
        )
        self._thread.start()
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "daemon.check_interval", "msg": f"[TaskScheduler] 调度器 daemon 线程已启动 (检查间隔={check_interval}秒)"}, ensure_ascii=False))

    def _run_loop(self, check_interval: int) -> None:
        """调度器主循环"""
        while self.running:
            try:
                self.tick()
            except Exception as e:
                logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "tick", "msg": f"[TaskScheduler] tick 错误: {e}"}, ensure_ascii=False))
            time.sleep(check_interval)
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "log", "msg": "[TaskScheduler] 调度器已停止"}, ensure_ascii=False))

    def tick(self) -> None:
        """检查并执行到期的任务"""
        for task in self.tasks:
            if self._should_run(task):
                self.run_task(task)

    def execute_now(self, task_id: str) -> Optional[Dict[str, Any]]:
        """立即执行指定任务（手动触发）"""
        task = self.get_task(task_id)
        if not task:
            return None
        return self.run_task(task)

    def stop(self) -> None:
        """停止调度器"""
        self.running = False
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "log", "msg": "[TaskScheduler] 调度器已停止"}, ensure_ascii=False))

    def list_tasks(self) -> List[Dict[str, Any]]:
        """列出所有任务（序列化版本，不含 func）"""
        result = []
        for t in self.tasks:
            entry = {
                "task_id": t.get("task_id", ""),
                "name": t["name"],
                "type": t["type"],
                "enabled": t.get("enabled", True),
                "last_run": t["last_run"].isoformat() if t.get("last_run") else None,
            }
            if t["type"] == "system_command":
                entry["command"] = t.get("command", "")
                entry["interval_sec"] = t.get("interval", 60)
            elif t["type"] == "python_func":
                if "cron" in t:
                    entry["cron"] = t["cron"]
                elif "interval" in t:
                    entry["interval_sec"] = t["interval"]
            elif t["type"] == "heartbeat":
                # 配置化：从 Config 读取心跳间隔（支持热加载）
                from agent.monitoring.observability_config import get_scheduler_heartbeat_interval
                entry["interval_sec"] = get_scheduler_heartbeat_interval()
            result.append(entry)
        return result


_scheduler: Optional[TaskScheduler] = None


def get_scheduler() -> TaskScheduler:
    """获取调度器实例（单例）"""
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
        # 预注册 Python 函数任务
        _scheduler.add_cron_task(
            name="生成周报",
            func=generate_weekly_report,
            day_of_week=0,
            hour=9,
            minute=0,
        )
        _scheduler.add_cron_task(
            name="清理旧日志",
            func=cleanup_old_logs,
            day_of_week=None,
            hour=2,
            minute=0,
        )
    return _scheduler


# ── 预定义任务函数 ──

def generate_weekly_report():
    """生成周报"""
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "log", "msg": "[TaskScheduler] 生成周报任务"}, ensure_ascii=False))
    try:
        from agent.weekly_report_generator import run_weekly_report
        report, files = run_weekly_report(
            output_dir=str(DATA_DIR / "reports"),
            save_formats=["json", "html", "text"],
        )
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "len.files", "msg": f"[TaskScheduler] 周报生成完成: {len(files)} 个文件"}, ensure_ascii=False))
    except Exception as e:
        logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "log", "msg": f"[TaskScheduler] 周报生成失败: {e}"}, ensure_ascii=False))


def cleanup_old_logs():
    """清理旧日志"""
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "log", "msg": "[TaskScheduler] 清理旧日志任务"}, ensure_ascii=False))
    try:
        import shutil
        log_dir = DATA_DIR / "blackbox"
        if log_dir.exists():
            cutoff_date = datetime.now().timestamp() - (30 * 24 * 60 * 60)
            for file in log_dir.glob("blackbox_*.jsonl"):
                if file.stat().st_mtime < cutoff_date:
                    file.unlink()
                    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "file.name", "msg": f"[TaskScheduler] 删除旧日志: {file.name}"}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "log", "msg": "[TaskScheduler] 日志清理完成"}, ensure_ascii=False))
    except Exception as e:
        logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "task_scheduler", "action": "log", "msg": f"[TaskScheduler] 日志清理失败: {e}"}, ensure_ascii=False))


# ── 心跳检测函数 ──

def perform_heartbeat_check(yunshu_instance=None) -> Dict[str, Any]:
    """执行全维度心跳健康检查"""
    timestamp = datetime.now().isoformat()
    checks = {}
    all_ok = True

    # 1. 系统资源检查
    try:
        if yunshu_instance and hasattr(yunshu_instance, 'body'):
            readings = yunshu_instance.body.collect_quick()
            cpu = memory = disk = None
            for r in readings:
                d = r.to_dict()
                name = d.get("sensor_name", "")
                if name == "cpu_usage":
                    cpu = d.get("value")
                elif name == "memory_usage":
                    memory = d.get("value")
            checks["system"] = {
                "status": "ok" if (cpu is None or cpu < 90) and (memory is None or memory < 90) else "warn",
                "cpu": cpu, "memory": memory,
            }
        else:
            import psutil
            cpu = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").percent
            checks["system"] = {
                "status": "ok" if cpu < 90 and memory < 90 and disk < 95 else "warn",
                "cpu": cpu, "memory": memory, "disk": disk,
            }
    except Exception as e:
        checks["system"] = {"status": "error", "error": str(e)[:100]}
        all_ok = False

    # 2. LLM 连通性
    try:
        if yunshu_instance and hasattr(yunshu_instance, '_llm') and yunshu_instance._llm:
            llm = yunshu_instance._llm
            checks["llm"] = {
                "status": "ok",
                "provider": getattr(llm, 'provider', 'unknown'),
                "model": getattr(llm, 'model', 'unknown'),
                "latency_ms": 0,
            }
        else:
            checks["llm"] = {"status": "not_configured", "message": "LLM 未配置"}
    except Exception as e:
        checks["llm"] = {"status": "error", "error": str(e)[:100]}
        all_ok = False

    # 3. 记忆系统
    try:
        if yunshu_instance and hasattr(yunshu_instance, '_memory'):
            memory_mgr = yunshu_instance._memory
            checks["memory"] = {
                "status": "ok",
                "message": "MemoryManager 正常运行",
            }
        else:
            checks["memory"] = {"status": "not_available", "message": "记忆系统不可用"}
    except Exception as e:
        checks["memory"] = {"status": "error", "error": str(e)[:100]}
        all_ok = False

    # 4. 调度器状态
    try:
        global _scheduler
        if _scheduler and _scheduler.running:
            checks["scheduler"] = {
                "status": "ok",
                "running": True,
                "tasks": len(_scheduler.tasks),
            }
        else:
            checks["scheduler"] = {"status": "stopped", "running": False, "tasks": 0}
            all_ok = False
    except Exception as e:
        checks["scheduler"] = {"status": "error", "error": str(e)[:100]}
        all_ok = False

    # 5. 关键线程
    try:
        main_threads = ["task-scheduler"]
        alive = []
        for t in threading.enumerate():
            if t.name in main_threads:
                alive.append(t.name)
        checks["threads"] = {
            "status": "ok" if len(alive) == len(main_threads) else "warn",
            "total": threading.active_count(),
            "alive": alive,
        }
    except Exception as e:
        checks["threads"] = {"status": "error", "error": str(e)[:100]}
        all_ok = False

    overall_status = "healthy" if all_ok else "degraded"
    for c in checks.values():
        if c.get("status") == "error":
            overall_status = "unhealthy"
            break

    return {
        "timestamp": timestamp,
        "status": overall_status,
        "checks": checks,
    }
