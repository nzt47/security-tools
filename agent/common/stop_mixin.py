"""StopMixin — 线程优雅关闭统一基类 [TLM-AUDIT-002]

【不易】提供统一的 stop+join 范式，确保子类线程生命周期受控：
       set event → join threads → _on_stop 钩子（flush/恢复）
【变易】子类可重写 _on_stop() 钩子实现自定义清理（如 flush 残留数据、恢复系统状态）
【简易】最小侵入：子类只需 register_thread() + 循环内检查 _should_stop()

设计来源：TLM 线程安全审计报告（docs/audit/2026-07-26-tlm-thread-safety-audit.md）
         统一 introspection / search / chaos_injector 等模块的线程关闭范式

用法示例:

    class MyWorker(StopMixin):
        def __init__(self):
            super().__init__()
            self._thread = None

        def start(self):
            self._stop_event.clear()  # 重置停止信号（支持重启）
            t = threading.Thread(target=self._loop, daemon=True, name="my-worker")
            t.start()
            self.register_thread(t)
            self._thread = t

        def _loop(self):
            while not self._should_stop():
                self.do_work()
                # 用 Event.wait 替代 time.sleep，支持 stop 时立即唤醒
                self._stop_event.wait(timeout=self._poll_interval)

        def _on_stop(self):
            # 子类重写：flush 残留数据到持久化存储
            self._flush_residual()
"""

from __future__ import annotations

import logging
import threading
from typing import List

logger = logging.getLogger(__name__)


class StopMixin:
    """线程优雅关闭统一基类

    提供 _stop_event + register_thread + stop(timeout) + _on_stop 钩子。
    子类继承后：
    1. 创建线程后调用 register_thread(thread) 注册到管理列表
    2. 线程循环内调用 _should_stop() 检查停止信号
    3. 用 _stop_event.wait(timeout) 替代 time.sleep（支持立即唤醒）
    4. 可选重写 _on_stop() 实现自定义清理（如 flush 残留数据）

    幂等性：stop() 二次调用直接返回 True（_stop_event 已 set）。
    """

    def __init__(self, *args, **kwargs):
        # 支持 cooperative 多继承：转发参数给下一个 __init__
        super().__init__(*args, **kwargs)
        # 统一的停止信号（Event 替代布尔标志，支持 wait 立即唤醒）
        self._stop_event: threading.Event = threading.Event()
        # 注册的线程句柄列表（stop 时统一 join）
        self._registered_threads: List[threading.Thread] = []
        # 保护 _registered_threads 的锁（register/stop 可能跨线程调用）
        self._thread_lock = threading.Lock()

    def register_thread(self, thread: threading.Thread) -> None:
        """子类创建线程后调用，注册到管理列表

        Why: StopMixin 需要持有线程句柄才能在 stop() 时 join。
        自动清理已退出的线程引用，避免列表无限增长。
        """
        with self._thread_lock:
            # 清理已退出的线程引用（避免长期运行后列表膨胀）
            self._registered_threads = [
                t for t in self._registered_threads if t.is_alive()
            ]
            self._registered_threads.append(thread)

    def _should_stop(self) -> bool:
        """子类循环内调用，检查停止信号

        Why: 比 `while self._running` 更优，因为 Event.set() 会自动唤醒
        阻塞在 Event.wait() 的线程，而布尔标志需要等到下一次 sleep 超时。
        """
        return self._stop_event.is_set()

    def stop(self, timeout: float = 5.0) -> bool:
        """统一优雅停止：set event → join all → _on_stop 钩子

        Args:
            timeout: join 超时（秒），超时后强制走 _on_stop 兜底
        Returns:
            True 如果所有线程都成功 join，False 如果有超时
        """
        # 幂等性：已停止直接返回
        if self._stop_event.is_set():
            return True
        self._stop_event.set()

        # 1. join 所有注册线程（_stop_event.set 已自动唤醒 Event.wait 阻塞）
        all_joined = True
        with self._thread_lock:
            threads_to_join = list(self._registered_threads)

        for t in threads_to_join:
            if t.is_alive():
                t.join(timeout=timeout)
                if t.is_alive():
                    logger.warning(
                        "[StopMixin] 线程 %s join 超时(%ss)，可能仍有未完成工作",
                        t.name, timeout,
                    )
                    all_joined = False

        # 2. 子类清理钩子（如 flush 残留数据、恢复系统状态）
        # Why: 即使 join 超时也执行兜底，避免数据丢失（守不易：数据完整性）
        try:
            self._on_stop()
        except Exception as e:
            logger.warning("[StopMixin] _on_stop 钩子异常: %s", e)

        return all_joined

    def _on_stop(self) -> None:
        """子类可重写的清理钩子（默认 no-op）

        典型用途：
        - flush 队列残留数据到持久化存储（如 tool_trace._flush_residual）
        - 释放外部资源（如文件句柄、网络连接）
        - 恢复系统状态（如 chaos_injector 的内存释放）

        Why: 钩子模式而非抽象方法，避免强制子类实现（守简易）。
        """
        pass

    def is_running(self) -> bool:
        """查询是否仍在运行（未收到停止信号）"""
        return not self._stop_event.is_set()
