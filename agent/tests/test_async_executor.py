"""异步执行器测试 -- 测试 async_executor.py 的 AsyncExecutor

覆盖范围：
- 提交任务（submit）
- 查询状态（get_status）
- 获取结果（get_result）
- 取消任务（cancel）
- 错误隔离
- 结果 TTL 清理

注意：使用 mock 避免线程池立即执行任务导致状态竞争。
"""
import time
import pytest
from unittest.mock import patch, MagicMock

from agent.async_executor import AsyncExecutor, reset_async_executor


# ════════════════════════════════════════════════════════════════════════════════
#  提交任务测试
# ════════════════════════════════════════════════════════════════════════════════

class TestSubmitTask:
    """AsyncExecutor.submit 测试"""

    def test_submit_returns_task_id(self):
        """提交任务返回 task_id（mock 线程池避免立即执行）"""
        executor = AsyncExecutor(max_workers=1, result_ttl=60)
        try:
            with patch.object(executor._pool, "submit"):
                result = executor.submit(
                    name="测试任务",
                    tool_name="web_search",
                    params={"query": "test"},
                )
            assert result["ok"] is True
            assert "task_id" in result
            assert result["task_id"].startswith("task_")
            assert result["status"] == "pending"
        finally:
            executor.shutdown(wait=False)

    def test_submit_multiple_tasks_unique_ids(self):
        """多个任务应有唯一 ID"""
        executor = AsyncExecutor(max_workers=1, result_ttl=60)
        try:
            with patch.object(executor._pool, "submit"):
                r1 = executor.submit(name="T1", tool_name="tool_a", params={})
                r2 = executor.submit(name="T2", tool_name="tool_b", params={})
            assert r1["task_id"] != r2["task_id"]
        finally:
            executor.shutdown(wait=False)


# ════════════════════════════════════════════════════════════════════════════════
#  查询状态测试
# ════════════════════════════════════════════════════════════════════════════════

class TestGetStatus:
    """get_status 测试"""

    def test_get_status_pending(self):
        """pending 状态查询（mock 线程池避免立即执行）"""
        executor = AsyncExecutor(max_workers=1, result_ttl=60)
        try:
            with patch.object(executor._pool, "submit"):
                r = executor.submit(name="状态测试", tool_name="test_tool", params={})
            task_id = r["task_id"]

            status = executor.get_status(task_id)
            assert status["ok"] is True
            assert status["task_id"] == task_id
            assert status["status"] == "pending"
        finally:
            executor.shutdown(wait=False)

    def test_get_status_nonexistent(self):
        """查询不存在的任务"""
        executor = AsyncExecutor(max_workers=1, result_ttl=60)
        try:
            result = executor.get_status("task_nonexistent")
            assert result["ok"] is False
            assert "error" in result
        finally:
            executor.shutdown(wait=False)


# ════════════════════════════════════════════════════════════════════════════════
#  获取结果测试
# ════════════════════════════════════════════════════════════════════════════════

class TestGetResult:
    """get_result 测试"""

    def _make_executor_with_completed(self):
        """创建 executor 并手动注入已完成任务"""
        executor = AsyncExecutor(max_workers=1, result_ttl=3600)
        task_id = "task_test_00001"
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        task = {
            "id": task_id,
            "name": "注入任务",
            "tool_name": "test_tool",
            "params": {},
            "status": "completed",
            "progress": "",
            "result": {"ok": True, "data": "hello"},
            "error": None,
            "created_at": now,
            "started_at": now,
            "completed_at": now,
            "timeout": None,
        }
        with executor._lock:
            executor._tasks[task_id] = task
        return executor, task_id

    def test_get_result_completed(self):
        """获取已完成任务的结果"""
        executor, task_id = self._make_executor_with_completed()
        try:
            result = executor.get_result(task_id)
            assert result["ok"] is True
            assert result["status"] == "completed"
            assert result["result"]["data"] == "hello"
        finally:
            executor.shutdown(wait=False)

    def test_get_result_failed(self):
        """获取失败任务的结果"""
        executor = AsyncExecutor(max_workers=1, result_ttl=3600)
        try:
            task_id = "task_failed_001"
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            task = {
                "id": task_id,
                "name": "失败任务",
                "tool_name": "bad_tool",
                "params": {},
                "status": "failed",
                "progress": "",
                "result": None,
                "error": "Something went wrong",
                "created_at": now,
                "started_at": now,
                "completed_at": now,
                "timeout": None,
            }
            with executor._lock:
                executor._tasks[task_id] = task

            result = executor.get_result(task_id)
            assert result["ok"] is True
            assert result["status"] == "failed"
            assert result["error"] == "Something went wrong"
        finally:
            executor.shutdown(wait=False)

    def test_get_result_pending(self):
        """获取未完成任务返回提示（mock 线程池避免立即执行）"""
        executor = AsyncExecutor(max_workers=1, result_ttl=60)
        try:
            with patch.object(executor._pool, "submit"):
                r = executor.submit(name="未完成", tool_name="slow_tool", params={})
            task_id = r["task_id"]

            result = executor.get_result(task_id)
            assert result["ok"] is True
            assert "message" in result
            assert "尚未完成" in result["message"]
        finally:
            executor.shutdown(wait=False)

    def test_get_result_nonexistent(self):
        """获取不存在任务的结果"""
        executor = AsyncExecutor(max_workers=1, result_ttl=60)
        try:
            result = executor.get_result("task_fake")
            assert result["ok"] is False
            assert "error" in result
        finally:
            executor.shutdown(wait=False)


# ════════════════════════════════════════════════════════════════════════════════
#  取消任务测试
# ════════════════════════════════════════════════════════════════════════════════

class TestCancelTask:
    """cancel 任务测试"""

    def test_cancel_pending(self):
        """取消 pending 状态的任务（mock 线程池避免立即执行）"""
        executor = AsyncExecutor(max_workers=1, result_ttl=60)
        try:
            with patch.object(executor._pool, "submit"):
                r = executor.submit(name="要取消", tool_name="test", params={})
            task_id = r["task_id"]

            result = executor.cancel(task_id)
            assert result["ok"] is True
            assert result["status"] == "cancelled"

            # 取消后状态确认
            status = executor.get_status(task_id)
            assert status["status"] == "cancelled"
        finally:
            executor.shutdown(wait=False)

    def test_cancel_completed(self):
        """不能取消已完成的任务"""
        executor = AsyncExecutor(max_workers=1, result_ttl=60)
        try:
            task_id = "task_done_001"
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            task = {
                "id": task_id,
                "name": "已完成",
                "tool_name": "test",
                "params": {},
                "status": "completed",
                "result": {"ok": True},
                "error": None,
                "created_at": now,
                "started_at": now,
                "completed_at": now,
                "timeout": None,
            }
            with executor._lock:
                executor._tasks[task_id] = task

            result = executor.cancel(task_id)
            assert result["ok"] is False
            assert "error" in result
        finally:
            executor.shutdown(wait=False)

    def test_cancel_nonexistent(self):
        """取消不存在的任务"""
        executor = AsyncExecutor(max_workers=1, result_ttl=60)
        try:
            result = executor.cancel("task_fake")
            assert result["ok"] is False
            assert "error" in result
        finally:
            executor.shutdown(wait=False)


# ════════════════════════════════════════════════════════════════════════════════
#  列出任务测试
# ════════════════════════════════════════════════════════════════════════════════

class TestListTasks:
    """list_tasks 测试"""

    def test_list_empty(self):
        """无任务时空列表"""
        executor = AsyncExecutor(max_workers=1, result_ttl=60)
        try:
            result = executor.list_tasks()
            assert result["ok"] is True
            assert result["tasks"] == []
            assert result["total"] == 0
        finally:
            executor.shutdown(wait=False)

    def test_list_multiple(self):
        """列出多个提交的任务"""
        executor = AsyncExecutor(max_workers=1, result_ttl=60)
        try:
            with patch.object(executor._pool, "submit"):
                executor.submit(name="A", tool_name="ta", params={})
                executor.submit(name="B", tool_name="tb", params={})

            result = executor.list_tasks()
            assert result["ok"] is True
            assert result["total"] == 2
            names = {t["name"] for t in result["tasks"]}
            assert names == {"A", "B"}
        finally:
            executor.shutdown(wait=False)


# ════════════════════════════════════════════════════════════════════════════════
#  TTL 清理测试
# ════════════════════════════════════════════════════════════════════════════════

class TestTtlCleanup:
    """结果 TTL 清理"""

    def test_cleanup_expired_results(self):
        """过期结果被自动清理"""
        executor = AsyncExecutor(max_workers=1, result_ttl=-1)  # TTL 设为负数，立即过期
        try:
            # 插入一个"已完成"的旧任务
            task_id = "task_old_001"
            old_time = "2000-01-01T00:00:00"  # 很久以前
            task = {
                "id": task_id,
                "name": "旧任务",
                "tool_name": "test",
                "params": {},
                "status": "completed",
                "result": {"ok": True},
                "error": None,
                "created_at": old_time,
                "started_at": old_time,
                "completed_at": old_time,
                "timeout": None,
            }
            with executor._lock:
                executor._tasks[task_id] = task

            # 调用 get_result 触发清理
            result = executor.get_result(task_id)
            # 由于 TTL 为负，任务应被清理
            assert result["ok"] is False  # 任务已被清理
        finally:
            executor.shutdown(wait=False)

    def test_cleanup_does_not_affect_pending(self):
        """清理不影响 pending 状态任务（mock 线程池避免立即执行）"""
        executor = AsyncExecutor(max_workers=1, result_ttl=-1)
        try:
            with patch.object(executor._pool, "submit"):
                r = executor.submit(name="新任务", tool_name="test", params={})
            task_id = r["task_id"]

            # 调用 cleanup
            executor._cleanup_expired()

            # pending 任务仍存在
            status = executor.get_status(task_id)
            assert status["ok"] is True
        finally:
            executor.shutdown(wait=False)


# ════════════════════════════════════════════════════════════════════════════════
#  关闭执行器测试
# ════════════════════════════════════════════════════════════════════════════════

class TestShutdown:
    """shutdown 测试"""

    def test_shutdown_no_wait(self):
        """不等待即关闭"""
        executor = AsyncExecutor(max_workers=1, result_ttl=60)
        executor.shutdown(wait=False)
        # 不应抛出异常

    def test_shutdown_after_submit(self):
        """提交任务后关闭（mock 线程池避免立即执行）"""
        executor = AsyncExecutor(max_workers=1, result_ttl=60)
        with patch.object(executor._pool, "submit"):
            executor.submit(name="最后任务", tool_name="test", params={})
        executor.shutdown(wait=False)
        # 关闭应成功（即使有 pending 任务）
