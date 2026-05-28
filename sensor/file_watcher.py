"""
文件系统监听传感器 — 我的"触觉"监测器

基于 watchdog 库，实时监控文件变动，缓冲事件供轮询采集。

三种工作模式：
  1. 事件驱动：通过 callback 实时推送（原有方式）
  2. 轮询采集：缓冲事件，通过 collect() 批量拉取（新增）
  3. 过滤监控：按文件类型/模式过滤，聚焦硬件相关文件

变化就是我的触觉——每一次文件创建、修改、删除，都是神经末梢的一次电信号。
"""
import os
import re
import time
import fnmatch
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from .sensor_reading import SensorReading, Severity, Category, normal, warning, critical


# ═══════════════════════════════════════════════════════════════
#  事件缓冲区（线程安全）
# ═══════════════════════════════════════════════════════════════

class EventBuffer:
    """
    线程安全的事件缓冲区。

    支持防抖合并：同一文件的重复修改事件在窗口期内合并为一次。
    """

    def __init__(self, debounce_sec=2.0, max_events=500):
        self._buffer = []           # 待采集的事件
        self._debounce = {}         # path -> (index, timestamp) 防抖索引
        self._lock = threading.Lock()
        self._debounce_sec = debounce_sec
        self._max_events = max_events
        self._dropped = 0           # 因上限丢弃的事件数

    def push(self, reading):
        """压入一个事件（线程安全，带防抖）"""
        with self._lock:
            now = time.time()
            path = reading.value

            # 防抖：同一路径的 modified 事件在窗口期内合并
            if reading.sensor_name.endswith("_modified"):
                prev = self._debounce.get(path)
                if prev and (now - prev[1]) < self._debounce_sec:
                    # 更新已有事件的时间戳，不新增
                    idx = prev[0]
                    self._buffer[idx] = reading
                    self._debounce[path] = (idx, now)
                    return

            # 超出上限时丢弃最早的事件
            if len(self._buffer) >= self._max_events:
                self._buffer.pop(0)
                self._dropped += 1

            idx = len(self._buffer)
            self._buffer.append(reading)
            if reading.sensor_name.endswith("_modified"):
                self._debounce[path] = (idx, now)
            # 删除的事件清除防抖记录
            elif reading.sensor_name.endswith("_deleted"):
                self._debounce.pop(path, None)

    def drain(self):
        """取出并清空所有缓冲事件（线程安全）"""
        with self._lock:
            events = self._buffer[:]
            self._buffer.clear()
            self._debounce.clear()
            dropped = self._dropped
            self._dropped = 0
        return events, dropped

    @property
    def size(self):
        with self._lock:
            return len(self._buffer)

    @property
    def dropped(self):
        return self._dropped


# ═══════════════════════════════════════════════════════════════
#  文件名模式匹配
# ═══════════════════════════════════════════════════════════════

class PatternFilter:
    """
    文件过滤规则。

    支持 fnmatch glob 模式和正则表达式。
    优先级：排除规则优先于包含规则。
    """

    def __init__(self, include=None, exclude=None):
        """
        :param include: 包含模式列表，None = 全部包含
        :param exclude: 排除模式列表，None = 全部保留
        """
        self._include = include
        self._exclude = exclude or []
        # 编译 .git 等常见排除项
        self._auto_exclude = [".git", "__pycache__", "*.pyc", ".DS_Store", "Thumbs.db"]

    def accept(self, path):
        """判断路径是否应被监控"""
        name = os.path.basename(path)
        low_name = name.lower()

        # 自动排除
        for pat in self._auto_exclude:
            if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(low_name, pat):
                return False

        # 用户排除
        for pat in self._exclude:
            if fnmatch.fnmatch(name, pat) or pat in path:
                return False

        # 用户包含
        if self._include is not None:
            for pat in self._include:
                if fnmatch.fnmatch(name, pat) or pat in path:
                    return True
            return False  # 有包含规则但都不匹配 → 不包含

        return True

    @property
    def description(self):
        parts = []
        if self._include:
            parts.append(f"包含: {','.join(self._include)}")
        if self._exclude:
            parts.append(f"排除: {','.join(self._exclude)}")
        return " | ".join(parts) if parts else "全部"


# ═══════════════════════════════════════════════════════════════
#  文件系统事件处理器
# ═══════════════════════════════════════════════════════════════

class FileEventHandler(FileSystemEventHandler):
    """文件系统事件处理器 — 我的触觉神经末梢"""

    def __init__(self, buffer=None, callback=None, pattern_filter=None,
                 sensor_category=Category.FILE):
        super().__init__()
        self._buffer = buffer
        self.callback = callback
        self._filter = pattern_filter or PatternFilter()
        self._category = sensor_category

    def _make_reading(self, event):
        """将 watchdog 事件格式化为 SensorReading"""
        event_type = event.event_type
        src_path = event.src_path
        is_dir = event.is_directory

        type_labels = {
            "created": ("file_created", "文件创建"),
            "modified": ("file_modified", "文件修改"),
            "deleted": ("file_deleted", "文件删除"),
            "moved": ("file_moved", "文件移动"),
        }
        sensor_name, type_label = type_labels.get(event_type, ("file_event", "文件事件"))
        if is_dir:
            sensor_name = sensor_name.replace("file", "dir")
            type_label = type_label.replace("文件", "目录")

        sev = Severity.WARNING if event_type == "deleted" else Severity.NORMAL
        dest = getattr(event, 'dest_path', None)

        return SensorReading(
            sensor_name, src_path, "",
            f"{type_label}: {src_path}", self._category, sev,
            {"event_type": event_type, "is_directory": is_dir,
             "src_path": src_path, "dest_path": dest,
             "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
        )

    def _dispatch(self, event):
        """统一分发事件到缓冲区和回调"""
        # 过滤
        if not self._filter.accept(event.src_path):
            return
        if hasattr(event, 'dest_path') and event.dest_path:
            if not self._filter.accept(event.dest_path):
                return

        reading = self._make_reading(event)

        # 入缓冲区
        if self._buffer:
            self._buffer.push(reading)

        # 回调
        if self.callback:
            try:
                self.callback(reading)
            except Exception as e:
                logging.error(f"文件事件回调出错: {e}")

    def on_created(self, event):
        self._dispatch(event)

    def on_modified(self, event):
        self._dispatch(event)

    def on_deleted(self, event):
        self._dispatch(event)

    def on_moved(self, event):
        self._dispatch(event)


# ═══════════════════════════════════════════════════════════════
#  文件监听器
# ═══════════════════════════════════════════════════════════════

class FileWatcher:
    """
    文件系统监听器 — 我的触觉系统。

    支持实时回调 + 轮询采集双模式，带事件缓冲和防抖合并。
    """

    def __init__(self, watch_dirs, callback=None,
                 include=None, exclude=None,
                 debounce_sec=2.0, max_events=500):
        """
        初始化文件系统监听。

        :param watch_dirs: 监听的目录（字符串或列表）
        :param callback: 实时回调函数，接收 SensorReading
        :param include: 包含的文件模式列表，如 ["*.sys", "*.inf"]
                        None = 全部包含
        :param exclude: 排除的文件模式列表
        :param debounce_sec: 修改事件防抖窗口（秒）
        :param max_events: 缓冲区最大事件数
        """
        dirs = watch_dirs if isinstance(watch_dirs, list) else [watch_dirs]
        # 只保留存在的目录，记录不存在的
        self.watch_dirs = []
        self._missing_dirs = []
        for d in dirs:
            if os.path.isdir(d):
                self.watch_dirs.append(d)
            else:
                self._missing_dirs.append(d)

        self._filter = PatternFilter(include=include, exclude=exclude)
        self._buffer = EventBuffer(debounce_sec=debounce_sec, max_events=max_events)
        self.callback = callback
        self._category = Category.FILE

        self.observer = Observer()
        self.handler = FileEventHandler(
            buffer=self._buffer,
            callback=callback,
            pattern_filter=self._filter,
        )
        self._thread = None
        self._running = False
        self._history = []       # 事件历史（内存）
        self._stats = defaultdict(int)  # event_type -> count

    def start(self):
        """启动监听 — 张开我的触觉网络"""
        if self._running:
            return
        if not self.watch_dirs:
            logging.warning("没有有效的监听目录，触觉网络无法激活。")
            return

        for d in self.watch_dirs:
            self.observer.schedule(self.handler, d, recursive=True)

        if self._missing_dirs:
            logging.warning(f"以下目录不存在，已跳过: {self._missing_dirs}")

        self.observer.start()
        self._running = True
        logging.info(
            f"文件监听已启动（{len(self.watch_dirs)} 个目录）"
            + (f"，过滤: {self._filter.description}" if self._filter.description != "全部" else "")
        )

    def stop(self):
        """停止监听 — 收回触觉网络"""
        if not self._running:
            return
        self.observer.stop()
        self.observer.join()
        self._running = False
        logging.info("文件监听已停止")

    # ═══════════════════════════════════════════════════════════
    #  采集接口（供 BodySensor.collect_all 调用）
    # ═══════════════════════════════════════════════════════════

    def collect(self):
        """
        采集缓冲的文件变动事件。

        返回 SensorReading 列表：
          - 缓冲区的具体事件（created/modified/deleted）
          - 统计总览读数
        """
        readings = []
        events, dropped = self._buffer.drain()

        if not events and not dropped:
            return readings

        # 按类型统计
        type_count = defaultdict(int)
        for r in events:
            readings.append(r)
            self._history.append(r)
            et = r.metadata.get("event_type", "unknown")
            self._stats[et] += 1
            type_count[et] += 1

        # 裁剪历史（保留最近 1000 条）
        if len(self._history) > 1000:
            self._history = self._history[-1000:]

        # 统计总览
        total = sum(type_count.values())
        readings.insert(0, normal(
            "filewatch_events", total, "次",
            f"文件变动事件: {dict(type_count)}", self._category,
            {"by_type": dict(type_count), "dropped": dropped,
             "since_last_collect": total}
        ))

        return readings

    # ═══════════════════════════════════════════════════════════
    #  统计和状态
    # ═══════════════════════════════════════════════════════════

    @property
    def is_running(self):
        return self._running

    @property
    def watched_dirs(self):
        return self.watch_dirs[:]

    @property
    def event_count(self):
        """所有类型事件总数"""
        return sum(self._stats.values())

    def get_history(self, event_type=None, limit=50):
        """获取事件历史"""
        if event_type:
            return [r for r in self._history[-limit:] if r.metadata.get("event_type") == event_type]
        return self._history[-limit:]

    def get_stats(self):
        """获取事件统计"""
        return {
            "total": dict(self._stats),
            "all_count": sum(self._stats.values()),
            "buffer_size": self._buffer.size,
        }

    def __repr__(self):
        return (f"FileWatcher(dirs={len(self.watch_dirs)}, "
                f"running={self._running}, events={sum(self._stats.values())})")
