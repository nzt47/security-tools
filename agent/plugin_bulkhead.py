"""插件级并发闸门（P1 bulkhead，2026-11-22）。

【解决什么】
14 个插件 / 226 条路由共用 waitress 的 16 个工作线程；任一插件路由变慢，
都会占住全局线程，最终把整站拖到排队。本模块给**每个插件**一把独立的
BoundedSemaphore：只限制“同时进入该插件”的请求数，不改变单请求的耗时。

【为什么默认 4、0 可关】
- 4 是“单插件突发”的经验上限；插件自己的慢查询应被自身超时/缓存治理，
  闸门只负责不让它吃光宿主线程。
- CP_PLUGIN_MAX_CONCURRENT=0 ⇒ 整支跳过（回滚用，不改变旧行为）。

【边界】
- 只做**非阻塞**获取：拿不到即 429，不排队（排队会把 waitress 线程一起钉住）。
- 只覆盖 HTTP 插件蓝图；工具 handler 有自己的并发额度（agent/tools）。
- 释放由 app_server 的 teardown_request 负责；本模块不依赖 Flask，便于单测。
"""
from __future__ import annotations

import threading
from typing import Dict


class PluginBulkhead:
    """按插件名分配 BoundedSemaphore；limit<=0 时整支禁用。"""

    def __init__(self, limit: int):
        try:
            self._limit = int(limit)
        except (TypeError, ValueError):
            self._limit = 0
        self._lock = threading.RLock()
        self._sems: Dict[str, threading.BoundedSemaphore] = {}

    @property
    def limit(self) -> int:
        return self._limit

    def _sem_for(self, name: str) -> threading.BoundedSemaphore:
        with self._lock:
            sem = self._sems.get(name)
            if sem is None:
                sem = threading.BoundedSemaphore(self._limit)
                self._sems[name] = sem
            return sem

    def acquire(self, name: str) -> bool:
        """非阻塞获取；limit<=0 恒 True（关闭闸门）。"""
        if self._limit <= 0:
            return True
        return bool(self._sem_for(str(name)).acquire(blocking=False))

    def release(self, name: str) -> None:
        """归还；limit<=0 或未登记时 no-op。"""
        if self._limit <= 0:
            return
        with self._lock:
            sem = self._sems.get(str(name))
        if sem is not None:
            try:
                sem.release()
            except ValueError:
                # 多释放一次不应把请求打成 500（闸门自身不能成为故障源）
                pass

    def snapshot(self) -> Dict[str, int]:
        """只读诊断：插件名 → 当前剩余额度（不保证瞬时精确）。"""
        with self._lock:
            return {
                name: (self._limit if self._limit <= 0 else sem._value)
                for name, sem in self._sems.items()
            }
