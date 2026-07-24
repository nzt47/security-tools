"""MarkdownFileWatcher — Markdown → SQLite 反向同步 [TLM-L3]

职责：
- watchdog Observer 监听 watch_dir 下 .md 文件变更
- 500ms 去重窗口合并 Windows 多次触发的 on_modified（守不易约束）
- 解析 Front Matter → 三路 content_hash 比较 → 反向更新 SQLite 或记冲突

【不易】
- 反向同步幂等：file_hash == db_hash → 跳过（同文件多次触发只 1 次 SQLite 写入）
- 冲突以 content_hash 三路比较为准，不依赖文件时间戳
- 冲突不自动解决，只记日志 + 上报指标
- FileWatcher 启动失败不阻塞主进程（try-except 兜底）

【变易】
- 去重窗口可配置（默认 500ms）
- 无 Front Matter 的 .md 文件忽略（如 README）
- 向量重索引依赖 adapter._embedding_func（无则降级跳过）

【简易】
- per-path Timer 合并 burst 事件
- 反向更新复用 adapter.save_with_embedding（接口契约守不易）
- 反向更新前调 syncer.suppress_for_reverse 抑制回环重渲染
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

from agent.logging_utils import log_dict
from agent.memory.markdown_syncer import (
    compute_content_hash,
    parse_markdown_file,
)

logger = logging.getLogger(__name__)

# Windows watchdog 多次触发的去重窗口（毫秒），守不易约束
DEFAULT_DEDUP_MS = 500


class MarkdownFileWatcher:
    """[TLM-L3] 反向同步 — FileWatcher

    用法:
        watcher = MarkdownFileWatcher(watch_dir, adapter, syncer)
        watcher.start()        # 非阻塞，后台 Observer 线程
        ...
        watcher.stop()
    """

    def __init__(
        self,
        watch_dir: str,
        adapter: Any,
        syncer: Any,
        dedup_ms: int = DEFAULT_DEDUP_MS,
    ):
        self.watch_dir = os.path.abspath(watch_dir)
        self.adapter = adapter
        self.syncer = syncer
        self.dedup_ms = dedup_ms

        self._observer = None
        self._started = False
        # per-path 去重：{path: threading.Timer}
        self._dedup_lock = threading.Lock()
        self._dedup_timers: dict[str, threading.Timer] = {}
        # 正在处理中的 path 集合：防止 flush 的正向写入被自身 watcher 误处理
        # （主要靠幂等性，此集合作为快速短路）
        self._processing: set[str] = set()
        self._proc_lock = threading.Lock()

    # ── 生命周期 ──

    def start(self):
        """启动 watchdog Observer。启动失败不抛异常（守不易：不阻塞主进程）"""
        if self._started:
            return
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError as e:
            logger.error(
                log_dict({
                    "module_name": "file_watcher",
                    "action": "start.no_watchdog",
                    "msg": f"[FileWatcher] watchdog 未安装，反向同步禁用: {e}",
                })
            )
            return

        try:
            self._observer = Observer()
            # handler 必须继承 FileSystemEventHandler 才有 dispatch（Observer 依赖它路由事件）
            handler = _make_handler(self, FileSystemEventHandler)
            self._observer.schedule(handler, self.watch_dir, recursive=True)
            self._observer.daemon = True
            self._observer.start()
            self._started = True
            logger.info(
                log_dict({
                    "module_name": "file_watcher",
                    "action": "start.ok",
                    "msg": f"[FileWatcher] 已启动: watch_dir={self.watch_dir}, "
                           f"dedup={self.dedup_ms}ms",
                })
            )
        except Exception as e:
            # 启动失败兜底：记录但不抛（守不易）
            logger.error(
                log_dict({
                    "module_name": "file_watcher",
                    "action": "start.failed",
                    "msg": f"[FileWatcher] 启动失败，反向同步不可用（主进程继续）: {e}",
                })
            )
            self._observer = None
            self._started = False

    def stop(self):
        """停止 Observer，取消所有去重计时器"""
        with self._dedup_lock:
            for timer in self._dedup_timers.values():
                timer.cancel()
            self._dedup_timers.clear()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception as e:
                logger.warning("[FileWatcher] 停止 Observer 异常: %s", e)
            self._observer = None
        self._started = False

    # ── 事件处理（由 _Handler 调用）──

    def on_modified(self, src_path: str):
        """watchdog on_modified 入口：500ms 去重后处理"""
        path = os.path.abspath(src_path)
        # 过滤非 .md 与 .tmp 原子写临时文件
        if not path.endswith(".md") or path.endswith(".tmp"):
            logger.debug(
                log_dict({
                    "module_name": "file_watcher",
                    "action": "on_modified.filtered",
                    "msg": f"[FileWatcher] 事件过滤（非 .md/.tmp）: {path}",
                })
            )
            return
        # 自身处理中的路径短路（幂等性兜底外的快速路径）
        with self._proc_lock:
            if path in self._processing:
                logger.debug(
                    log_dict({
                        "module_name": "file_watcher",
                        "action": "on_modified.self_processing",
                        "msg": f"[FileWatcher] 自身处理中短路: {path}",
                    })
                )
                return
        logger.debug(
            log_dict({
                "module_name": "file_watcher",
                "action": "on_modified.accepted",
                "msg": f"[FileWatcher] 事件入队去重: {path}",
            })
        )
        self._schedule_dedup(path)

    def _schedule_dedup(self, path: str):
        """per-path 去重：每次事件重置 500ms 计时器，burst 合并为 1 次处理"""
        with self._dedup_lock:
            old = self._dedup_timers.pop(path, None)
            if old is not None:
                old.cancel()
                logger.debug(
                    log_dict({
                        "module_name": "file_watcher",
                        "action": "dedup.coalesced",
                        "msg": f"[FileWatcher] 去重命中: 重置 {self.dedup_ms}ms 计时器 "
                               f"（burst 合并）path={path}",
                    })
                )
            timer = threading.Timer(
                self.dedup_ms / 1000.0,
                self._process_file,
                args=(path,),
            )
            timer.daemon = True
            self._dedup_timers[path] = timer
            timer.start()

    def _process_file(self, path: str):
        """去重窗口到期后执行：解析 → 三路比较 → 反向更新/记冲突"""
        with self._dedup_lock:
            self._dedup_timers.pop(path, None)
        with self._proc_lock:
            self._processing.add(path)
        try:
            self._do_process(path)
        except Exception as e:
            # 单文件处理失败不影响 watcher 继续运行（守不易）
            logger.error(
                log_dict({
                    "module_name": "file_watcher",
                    "action": "process.failed",
                    "msg": f"[FileWatcher] 处理文件失败 path={path}: {e}",
                })
            )
        finally:
            with self._proc_lock:
                self._processing.discard(path)

    def _do_process(self, path: str):
        if not os.path.exists(path):
            return  # 文件已被删除（delete 走 forward 路径，此处忽略）
        parsed = parse_markdown_file(path)
        if parsed is None:
            logger.debug("[FileWatcher] 无 Front Matter，忽略: %s", path)
            return
        fm = parsed["front_matter"]
        sqlite_id = fm.get("sqlite_id")
        if not sqlite_id:
            logger.debug("[FileWatcher] Front Matter 无 sqlite_id，忽略: %s", path)
            return

        file_data = parsed["data"]
        file_hash = compute_content_hash(file_data)
        base_hash = fm.get("content_hash")  # 上次正向同步写入的基线

        # 读取 SQLite 当前状态
        raw = self.adapter.get_raw_memory(sqlite_id)
        if raw is None:
            # SQLite 已无此条：文件指向不存在的记忆 → 记冲突
            logger.warning(
                log_dict({
                    "module_name": "file_watcher",
                    "action": "reverse.missing_in_db",
                    "msg": f"[FileWatcher] SQLite 无此记忆 sqlite_id={sqlite_id}，"
                           f"文件或为孤儿，记冲突",
                })
            )
            self.adapter.record_sync_conflict(
                sqlite_id, db_hash=None, file_hash=file_hash,
                resolution="db_missing",
            )
            self._notify_conflict_metric()
            return

        db_hash = compute_content_hash(raw["data"])

        # 三路比较概览（排查同步延迟/冲突的核心诊断点）
        logger.debug(
            log_dict({
                "module_name": "file_watcher",
                "action": "dedup.compare",
                "msg": f"[FileWatcher] 三路比较 sqlite_id={sqlite_id} "
                       f"base={base_hash} db={db_hash} file={file_hash}",
            })
        )

        # 三路比较
        # 1) 幂等：文件与 DB 一致 → 跳过（含 forward 同步刚写完文件的情形）
        if file_hash == db_hash:
            logger.debug(
                log_dict({
                    "module_name": "file_watcher",
                    "action": "dedup.idempotent_skip",
                    "msg": f"[FileWatcher] 幂等跳过 sqlite_id={sqlite_id} "
                           f"(file==db={file_hash})",
                })
            )
            return

        # 2) 文件未变（== base），DB 已变 → DB 更新，等 forward 同步覆盖文件，跳过反向
        if base_hash is not None and file_hash == base_hash:
            logger.debug(
                log_dict({
                    "module_name": "file_watcher",
                    "action": "dedup.file_unchanged",
                    "msg": f"[FileWatcher] 文件未变(file==base={file_hash}) DB 已变"
                           f"(db={db_hash})，跳过反向 sqlite_id={sqlite_id}",
                })
            )
            return

        # 3) DB 未变（== base），文件已变 → 反向同步更新 SQLite
        if base_hash is not None and db_hash == base_hash:
            logger.debug(
                log_dict({
                    "module_name": "file_watcher",
                    "action": "dedup.reverse_trigger",
                    "msg": f"[FileWatcher] DB 未变(db==base={db_hash}) 文件已变"
                           f"(file={file_hash})，触发反向同步 sqlite_id={sqlite_id}",
                })
            )
            self._reverse_update(sqlite_id, raw, file_data)
            return

        # 4) 两者都偏离 base → 双向冲突，只记录不自动解决（守不易）
        logger.warning(
            log_dict({
                "module_name": "file_watcher",
                "action": "reverse.conflict",
                "msg": f"[FileWatcher] 冲突检出 sqlite_id={sqlite_id} "
                       f"base={base_hash} db={db_hash} file={file_hash}",
            })
        )
        self.adapter.record_sync_conflict(
            sqlite_id, db_hash=db_hash, file_hash=file_hash,
            resolution="unresolved",
        )
        self._notify_conflict_metric()

    def _reverse_update(self, sqlite_id: str, raw: dict, new_data: str):
        """反向更新 SQLite（复用 adapter.save_with_embedding 触发向量重索引）"""
        # 抑制 forward 重渲染（幂等性作为第二道保险）
        if self.syncer is not None:
            self.syncer.suppress_for_reverse(sqlite_id)

        metadata = raw.get("metadata") or {}
        # 异步反向更新（adapter.save_with_embedding 是 async）
        threading.Thread(
            target=self._async_reverse_update,
            args=(sqlite_id, new_data, metadata),
            daemon=True,
        ).start()

    def _async_reverse_update(self, sqlite_id: str, new_data: str, metadata: dict):
        """异步执行反向更新（运行 event_loop 调用 async 接口）"""
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            try:
                ok = loop.run_until_complete(
                    self.adapter.save_with_embedding(sqlite_id, new_data, metadata)
                )
            finally:
                loop.close()
            if ok:
                logger.info(
                    log_dict({
                        "module_name": "file_watcher",
                        "action": "reverse.updated",
                        "msg": f"[FileWatcher] 反向同步成功 sqlite_id={sqlite_id} "
                               f"→ SQLite 已更新 + 向量重索引已触发",
                    })
                )
                # 刷新文件 Front Matter 的 content_hash 基线（守不易：
                # 否则后续编辑因 base 停在旧值而误判冲突）
                if self.syncer is not None:
                    self.syncer.refresh_single(sqlite_id)
            else:
                logger.warning(
                    log_dict({
                        "module_name": "file_watcher",
                        "action": "reverse.update_failed",
                        "msg": f"[FileWatcher] 反向同步 save 返回 False sqlite_id={sqlite_id}",
                    })
                )
        except Exception as e:
            logger.error(
                log_dict({
                    "module_name": "file_watcher",
                    "action": "reverse.update_exception",
                    "msg": f"[FileWatcher] 反向同步异常 sqlite_id={sqlite_id}: {e}",
                })
            )

    def _notify_conflict_metric(self):
        """冲突上报指标（埋点失败不影响主流程）"""
        try:
            from agent.memory.observability import trackEvent
            trackEvent("markdown_sync_conflict", {"resolution": "unresolved"})
        except Exception as e:
            logger.debug("[FileWatcher] 冲突指标上报失败（忽略）: %s", e)


def _make_handler(watcher: "MarkdownFileWatcher", base_cls):
    """构造 watchdog 事件 handler（继承 FileSystemEventHandler 以获得 dispatch）

    Why: Observer.dispatch_events 调用 handler.dispatch(event) 路由到 on_modified 等，
    必须继承基类；直接用裸类会导致 AttributeError（事件静默丢失）。
    """

    class _Handler(base_cls):
        def on_modified(self, event):
            if getattr(event, "is_directory", False):
                return
            watcher.on_modified(event.src_path)

        def on_created(self, event):
            if getattr(event, "is_directory", False):
                return
            watcher.on_modified(event.src_path)

    return _Handler()
