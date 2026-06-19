"""异步工具执行框架 — 基于线程池的异步任务管理

提供 AsyncExecutor 类，支持：
- 异步提交工具执行任务（submit）
- 查询任务状态（get_status）
- 获取任务结果（get_result）
- 取消任务（cancel）
- 列出所有任务（list_tasks）
- 结果 TTL 自动过期清理
- 任务持久化到 JSONL 文件

基于 concurrent.futures.ThreadPoolExecutor，不引入 asyncio。
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from uuid import uuid4

from agent.tools import call as call_tool

logger = __import__('logging').getLogger(__name__)


class AsyncExecutor:
    """异步任务执行器

    在线程池中异步执行工具调用，支持任务状态追踪和结果缓存。
    线程安全，支持任务持久化到 JSONL 文件。

    Attributes:
        max_workers: 线程池最大工作线程数
        result_ttl: 结果缓存时间（秒），超时自动清理
    """

    def __init__(self, max_workers: int = 3, result_ttl: int = 3600):
        """初始化异步执行器

        Args:
            max_workers: 线程池最大并发数，默认 3
            result_ttl: 任务完成后结果保留时间（秒），默认 3600（1小时）
        """
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._result_ttl = result_ttl
        self._tasks_file = "data/async_tasks.jsonl"

    def submit(self, name: str, tool_name: str, params: dict,
               timeout: int | None = None) -> dict:
        """提交异步任务

        Args:
            name: 任务名称（便于识别）
            tool_name: 要调用的工具名称
            params: 工具参数字典
            timeout: 任务超时秒数，None 表示不超时

        Returns:
            {"ok": True, "task_id": "task_xxx", "status": "pending"}
        """
        task_id = f"task_{uuid4().hex[:12]}"
        task = {
            "id": task_id,
            "name": name,
            "tool_name": tool_name,
            "params": params,
            "status": "pending",
            "progress": "",
            "result": None,
            "error": None,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "started_at": None,
            "completed_at": None,
            "timeout": timeout,
        }
        with self._lock:
            self._tasks[task_id] = task
        self._save_task(task)

        # 提交到线程池
        future = self._pool.submit(self._run_task, task_id, tool_name, params, timeout)
        future.add_done_callback(lambda f: self._on_complete(task_id, f))

        return {"ok": True, "task_id": task_id, "status": "pending"}

    def get_status(self, task_id: str) -> dict:
        """查询任务状态

        Args:
            task_id: 任务 ID

        Returns:
            任务状态字典，含 task_id、status、elapsed 等字段
        """
        with self._lock:
            task = self._tasks.get(task_id)
        if not task:
            return {"ok": False, "error": "任务不存在"}

        result = {
            "ok": True,
            "task_id": task_id,
            "status": task["status"],
            "progress": task.get("progress", ""),
        }
        if task["status"] in ("running", "completed", "failed"):
            result["elapsed"] = self._calc_elapsed(task)
        return result

    def get_result(self, task_id: str) -> dict:
        """获取任务结果

        任务完成后结果保留 result_ttl 秒，超时后自动清理。

        Args:
            task_id: 任务 ID

        Returns:
            - 完成: {"ok": True, "task_id": "...", "status": "completed",
                     "result": {...}, "completed_at": "..."}
            - 失败: {"ok": True, "task_id": "...", "status": "failed",
                     "error": "...", "completed_at": "..."}
            - 未完成: {"ok": True, "task_id": "...", "status": "...",
                       "message": "任务尚未完成"}
        """
        self._cleanup_expired()

        with self._lock:
            task = self._tasks.get(task_id)
        if not task:
            return {"ok": False, "error": "任务不存在"}

        if task["status"] == "completed":
            return {
                "ok": True,
                "task_id": task_id,
                "status": "completed",
                "result": task["result"],
                "completed_at": task["completed_at"],
            }
        if task["status"] == "failed":
            return {
                "ok": True,
                "task_id": task_id,
                "status": "failed",
                "error": task["error"],
                "completed_at": task["completed_at"],
            }
        return {
            "ok": True,
            "task_id": task_id,
            "status": task["status"],
            "message": "任务尚未完成",
        }

    def cancel(self, task_id: str) -> dict:
        """取消任务（仅 pending 或 running 状态可取消）

        Args:
            task_id: 任务 ID

        Returns:
            操作结果
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"ok": False, "error": "任务不存在"}

            if task["status"] in ("pending", "running"):
                task["status"] = "cancelled"
                task["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                task["error"] = "任务已被取消"
                self._save_task(task)
                return {"ok": True, "task_id": task_id, "status": "cancelled"}

            return {
                "ok": False,
                "error": f"无法取消状态为 '{task['status']}' 的任务",
            }

    def list_tasks(self) -> dict:
        """列出所有任务

        Returns:
            {"ok": True, "tasks": [...], "total": N}
        """
        self._cleanup_expired()
        with self._lock:
            task_list = list(self._tasks.values())

        # 按创建时间倒序排列
        task_list.sort(key=lambda t: t.get("created_at", ""), reverse=True)

        return {
            "ok": True,
            "tasks": task_list,
            "total": len(task_list),
        }

    def _run_task(self, task_id: str, tool_name: str, params: dict,
                  timeout: int | None):
        """在线程池中执行任务

        Args:
            task_id: 任务 ID
            tool_name: 工具名称
            params: 工具参数
            timeout: 超时秒数
        """
        # 标记为运行中
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = "running"
                self._tasks[task_id]["started_at"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%S"
                )

        try:
            # 调用工具
            result = call_tool(tool_name, **params)
            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "completed"
                    self._tasks[task_id]["result"] = result
                    self._tasks[task_id]["completed_at"] = time.strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    )
        except Exception as e:
            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "failed"
                    self._tasks[task_id]["error"] = str(e)
                    self._tasks[task_id]["completed_at"] = time.strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    )
            logger.error("异步任务 %s (%s) 执行失败: %s", task_id, tool_name, e)

    def _on_complete(self, task_id: str, future):
        """任务完成回调

        Args:
            task_id: 任务 ID
            future: Future 对象
        """
        # 检查是否有异常
        exc = future.exception()
        if exc:
            with self._lock:
                if task_id in self._tasks:
                    task = self._tasks[task_id]
                    if task["status"] not in ("completed", "failed", "cancelled"):
                        task["status"] = "failed"
                        task["error"] = str(exc)
                        task["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            logger.error("异步任务 %s 异常: %s", task_id, exc)

        # 保存最终状态
        with self._lock:
            task = self._tasks.get(task_id)
        if task:
            self._save_task(task)

    def _save_task(self, task: dict):
        """持久化任务记录到 JSONL 文件

        Args:
            task: 任务字典
        """
        try:
            os.makedirs("data", exist_ok=True)
            with open(self._tasks_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(task, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error("保存任务记录失败: %s", e)

    def _calc_elapsed(self, task: dict) -> float:
        """计算任务已用时间（秒）

        Args:
            task: 任务字典

        Returns:
            已用秒数，保留一位小数
        """
        if not task.get("started_at"):
            return 0.0
        try:
            start = time.mktime(time.strptime(task["started_at"],
                                              "%Y-%m-%dT%H:%M:%S"))
        except (ValueError, OverflowError):
            return 0.0

        if task.get("completed_at"):
            try:
                end = time.mktime(time.strptime(task["completed_at"],
                                                "%Y-%m-%dT%H:%M:%S"))
            except (ValueError, OverflowError):
                end = time.time()
        else:
            end = time.time()

        return round(end - start, 1)

    def _cleanup_expired(self):
        """清理过期结果

        将已完成/失败/取消且超过 result_ttl 秒的任务从内存中移除。
        """
        now = time.time()
        expired_ids = []
        with self._lock:
            for task_id, task in self._tasks.items():
                if task["status"] in ("completed", "failed", "cancelled"):
                    completed_at = task.get("completed_at")
                    if completed_at:
                        try:
                            completed_ts = time.mktime(
                                time.strptime(completed_at, "%Y-%m-%dT%H:%M:%S")
                            )
                        except (ValueError, OverflowError):
                            continue
                        if now - completed_ts > self._result_ttl:
                            expired_ids.append(task_id)

            for task_id in expired_ids:
                del self._tasks[task_id]

        if expired_ids:
            logger.info("清理了 %d 个过期任务结果", len(expired_ids))

    def shutdown(self, wait: bool = True):
        """关闭线程池

        Args:
            wait: 是否等待所有任务完成
        """
        self._pool.shutdown(wait=wait)
        logger.info("异步执行器线程池已关闭")


# 全局单例（由 DigitalLife 初始化）
_global_executor: AsyncExecutor | None = None


def get_async_executor(max_workers: int = 3,
                       result_ttl: int = 3600) -> AsyncExecutor:
    """获取全局异步执行器单例

    首次调用时创建，后续调用返回同一实例。

    Args:
        max_workers: 线程池大小（仅首次创建时生效）
        result_ttl: 结果 TTL 秒数（仅首次创建时生效）

    Returns:
        AsyncExecutor 全局实例
    """
    global _global_executor
    if _global_executor is None:
        _global_executor = AsyncExecutor(
            max_workers=max_workers,
            result_ttl=result_ttl,
        )
        logger.info(
            "全局异步执行器已初始化（max_workers=%d, result_ttl=%d）",
            max_workers, result_ttl,
        )
    return _global_executor


def reset_async_executor():
    """重置全局异步执行器（主要用于测试）"""
    global _global_executor
    if _global_executor is not None:
        _global_executor.shutdown(wait=False)
    _global_executor = None
