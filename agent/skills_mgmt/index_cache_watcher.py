"""L4 事件驱动实时失效 — watchdog 监听技能仓库 → 精确失效技能索引缓存

背景:
    L3 TTL 窗口内"外部直接修改文件"不可见（可见延迟 = 窗口）。
    L4 用 watchdog 监听 skills_repo 目录树，文件变更事件 → 精确失效
    对应技能（O(k)，k=实际变更数），彻底解决实时性问题。

职责:
    - 监听 skill.md 的创建/修改/删除/移动事件
    - 事件路径 → skill_id 提取 → 合并去重（背压）→ 批量 invalidate
    - 与既有 mtime/hash 校验兜底互不冲突（事件失效 + 校验双保险）

【不易】
    - 事件只做"精确失效"，不直接改缓存数据（失效必须回源）
    - watchdog 不可用 / 启动失败 → 降级返回 False（不阻塞，回退校验路径）
    - 事件延迟处理期间，get_metadata 的 stat+hash 校验仍保证最终一致
    - cache.invalidate 接口不变（仅内存状态变更，守持锁不 I/O）

【变易】
    - 背压：pending set 去重（上限 = 技能数）+ 后台线程批量 drain，
      高并发事件风暴时不 OOM、不重复失效
    - debounce 可配置（合并突发事件）
    - 可独立 start/stop；进程内多实例互不干扰

【简易】
    - 事件 handler 仅 add 到 pending（O(1)，不阻塞 watchdog 队列）
    - 复用 cache.invalidate（接口零改动）
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional, Set

from .observability import logger

# 背压上限：pending 去重集合的最大规模 = 技能目录数（天然有界）
# 事件风暴时超出部分被 set 合并，不额外分配内存


class _SkillEventProcessor:
    """背压合并队列：事件 → skill_id 去重 → 批量失效

    事件回调线程只做 `submit`（O(1) set add，加锁），后台 drain 线程
    每 debounce 秒批量取出并调用 invalidate。同一技能在窗口内被多次
    修改只失效一次（合并去重），高并发写入下 pending 集合有界
    （≤ 技能数），杜绝事件风暴导致的内存膨胀与重复解析。
    """

    def __init__(self, invalidate_cb: Callable[[str], None],
                 repo_path: str, debounce: float = 0.05):
        self._invalidate = invalidate_cb
        self._repo = Path(repo_path)
        self._debounce = debounce
        self._pending: Set[str] = set()   # 去重集合（背压核心）
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._drain_count = 0             # 累计失效次数（测试/监控）

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def drain_count(self) -> int:
        return self._drain_count

    def submit(self, skill_id: str) -> None:
        """事件到达（watchdog 回调线程）→ O(1) 入队，去重合并

        【简易】只做 set.add（内存），不阻塞 watchdog 事件队列
        """
        if not skill_id:
            return
        with self._lock:
            self._pending.add(skill_id)

    def drain(self) -> None:
        """批量取出并失效（幂等，逐技能调用 cache.invalidate）

        【不易】invalidate 是纯内存操作（锁内仅状态变更，日志锁外）
        """
        with self._lock:
            batch = self._pending
            self._pending = set()
        for sid in batch:
            try:
                self._invalidate(sid)
                self._drain_count += 1
            except Exception as e:  # noqa: BLE001  单技能失效失败不阻断批次
                logger.warning(json.dumps({
                    "module_name": "index_cache_watcher",
                    "action": "drain.invalidate_failed",
                    "skill_id": sid,
                    "error": str(e)[:200],
                }, ensure_ascii=False))

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="skill-cache-watcher-drain", daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._debounce):
            self.drain()
        self.drain()  # 收尾：停止前清空剩余事件

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.drain()  # 幂等收尾（可能在 stop 后仍被外部调用）


class SkillIndexCacheWatcher:
    """技能索引缓存事件监听 — watchdog Observer + 背压处理器

    用法:
        watcher = SkillIndexCacheWatcher(cache, repo_path)
        ok = watcher.start()      # watchdog 不可用/启动失败 → False（降级）
        ...
        watcher.stop()

    【不易】事件只做精确失效（invalidate）；watchdog 降级不影响主流程
    """

    def __init__(self, cache: Any, repo_path: Optional[str] = None,
                 debounce: float = 0.05):
        self._cache = cache
        self._repo_path = Path(repo_path or cache.fs.repo_path)
        self._processor = _SkillEventProcessor(
            cache.invalidate, str(self._repo_path), debounce,
        )
        self._observer: Any = None

    # ── 生命周期 ──────────────────────────────────────────

    def start(self) -> bool:
        """启动监听；watchdog 不可用或启动失败 → 返回 False（降级不阻塞）

        【变易】降级后回退到既有 mtime/hash 校验路径，功能不受影响
        """
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            logger.warning(json.dumps({
                "module_name": "index_cache_watcher",
                "action": "start.watchdog_missing",
                "fallback": "mtime_hash_validation",
            }, ensure_ascii=False))
            return False
        try:
            # 延迟继承 watchdog 基类：watchdog 按方法名分发，须有 dispatch
            # （watchdog 为可选依赖，故在 start 内动态合成，模块顶层不 import）
            class _EventHandler(_SkillIndexEventHandler, FileSystemEventHandler):
                pass
            handler = _EventHandler(self._processor, self._repo_path)
            self._observer = Observer()
            # recursive=True：技能目录下的 skill.md / scripts / temp 变更都需感知
            self._observer.schedule(handler, str(self._repo_path), recursive=True)
            self._observer.start()
            self._processor.start()
            logger.info(json.dumps({
                "module_name": "index_cache_watcher",
                "action": "start.ok",
                "repo_path": str(self._repo_path),
                "debounce": self._processor._debounce,
            }, ensure_ascii=False))
            return True
        except Exception as e:  # noqa: BLE001  启动失败降级，不抛给调用方
            logger.warning(json.dumps({
                "module_name": "index_cache_watcher",
                "action": "start.failed",
                "error": str(e)[:200],
                "fallback": "mtime_hash_validation",
            }, ensure_ascii=False))
            return False

    def stop(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
            self._observer = None
        self._processor.stop()

    def pending_count(self) -> int:
        """待失效技能数（测试/监控背压状态）"""
        return self._processor.pending

    def drain_count(self) -> int:
        """累计失效次数（测试/监控合并效果）"""
        return self._processor.drain_count


class _SkillIndexEventHandler:
    """watchdog 事件 handler — 事件路径 → skill_id → submit 背压队列

    【简易】不持有任何缓存引用，只做路径解析与入队
    """

    def __init__(self, processor: _SkillEventProcessor, repo_path: Path):
        self._processor = processor
        self._repo = Path(repo_path).resolve()

    def _skill_id_from(self, path: str) -> Optional[str]:
        """事件路径 → 技能目录名；非技能文件/隐藏目录返回 None

        - 顶层目录名以 `.` 或 `_` 开头（如 .index）→ 忽略
        - 技能目录下任意子文件（skill.md / scripts / temp）→ 归并到技能目录名
        """
        try:
            rel = Path(path).resolve().relative_to(self._repo)
        except ValueError:
            return None
        parts = rel.parts
        if not parts or parts[0].startswith((".", "_")):
            return None
        return parts[0]

    def on_created(self, event: Any) -> None:
        sid = self._skill_id_from(event.src_path)
        if sid:
            self._processor.submit(sid)

    def on_modified(self, event: Any) -> None:
        sid = self._skill_id_from(event.src_path)
        if sid:
            self._processor.submit(sid)

    def on_deleted(self, event: Any) -> None:
        sid = self._skill_id_from(event.src_path)
        if sid:
            self._processor.submit(sid)

    def on_moved(self, event: Any) -> None:
        for p in (event.src_path, event.dest_path):
            sid = self._skill_id_from(p)
            if sid:
                self._processor.submit(sid)
