"""
窗口活动传感器 — 监控前台窗口切换

通过 Win32 API 轮询当前前台窗口，检测切换事件并记录使用时长。
我是云枢的"注意力追踪器"——我知道用户在看什么、用什么。
"""
import time
import json
import threading
import logging
import os

logger = logging.getLogger(__name__)

try:
    import win32gui
    import win32process
    import psutil
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


class WindowSensor:
    """前台窗口活动监控传感器"""

    def __init__(self, config_path="data/window_config.json", save_callback=None):
        self._config_path = config_path
        self._save_callback = save_callback  # function(event_type, data)
        self._config = self._load_config()
        self._thread = None
        self._running = False
        self._current_process = None
        self._current_title = None
        self._last_switch_time = time.time()
        self._idle_start = None

    # ── 配置管理 ──

    def _load_config(self):
        defaults = {
            "enabled": False,  # 默认禁用，需要用户明确同意
            "poll_interval_sec": 1,
            "max_events": 500,
            "idle_timeout_sec": 300,
            "ignore_processes": [],
        }
        try:
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    defaults.update(loaded)
        except Exception as e:
            logger.warning(f"加载窗口配置失败: {e}")
        return defaults

    def save_config(self, new_config):
        self._config.update(new_config)
        try:
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存窗口配置失败: {e}")

    def get_config(self):
        return dict(self._config)

    # ── 数据采集 ──

    def collect(self):
        """采集当前窗口信息，返回 SensorReading 兼容格式"""
        if not HAS_WIN32:
            return None
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                proc = psutil.Process(pid)
                proc_name = proc.name()
            except Exception:
                proc_name = "unknown"
            return {
                "title": title or "",
                "process": proc_name,
                "pid": pid,
            }
        except Exception as e:
            logger.debug(f"窗口采集失败: {e}")
            return None

    # ── 后台监控 ──

    def start(self):
        if not HAS_WIN32:
            logger.warning("WindowSensor: win32gui/win32process 不可用，跳过启动")
            return
        if self._running:
            return
        self._running = True
        self._last_switch_time = time.time()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("WindowSensor 监控已启动 (间隔=%ss)", self._config["poll_interval_sec"])

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("WindowSensor 监控已停止")

    def is_running(self):
        return self._running

    def _poll_loop(self):
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                logger.debug(f"WindowSensor 轮询异常: {e}")
            time.sleep(self._config["poll_interval_sec"])

    def _poll_once(self):
        if not self._config["enabled"]:
            return

        info = self.collect()
        if not info:
            return

        proc = info["process"]
        title = info["title"]
        now = time.time()

        # 忽略列表
        if proc in self._config.get("ignore_processes", []):
            return

        # 空闲检测
        idle_timeout = self._config["idle_timeout_sec"]
        if not proc and not title:
            if self._idle_start is None and self._current_process is not None:
                elapsed = now - self._last_switch_time
                if elapsed >= idle_timeout:
                    self._idle_start = now
                    self._log_event("idle_start", self._current_process,
                                    self._current_title, "", "", elapsed)
                    self._current_process = None
                    self._current_title = None
            return

        # 从空闲恢复
        if self._idle_start is not None:
            idle_duration = now - self._idle_start
            self._idle_start = None
            self._last_switch_time = now
            self._current_process = proc
            self._current_title = title
            self._log_event("idle_end", "", "", proc, title, idle_duration)
            return

        # 窗口切换检测
        if proc != self._current_process or title != self._current_title:
            duration = now - self._last_switch_time
            if self._current_process is not None:
                self._log_event("switch", self._current_process,
                                self._current_title, proc, title, duration)
            self._current_process = proc
            self._current_title = title
            self._last_switch_time = now

    def _log_event(self, action, from_proc, from_title, to_proc, to_title, duration):
        if self._save_callback:
            try:
                self._save_callback("window_event", {
                    "action": action,
                    "from_process": from_proc or None,
                    "from_title": from_title or None,
                    "to_process": to_proc or None,
                    "to_title": to_title or None,
                    "duration_sec": round(duration, 1),
                })
            except Exception as e:
                logger.debug(f"WindowSensor 日志写入失败: {e}")

    # ── 当前状态 ──

    def get_current(self):
        if self._current_process:
            return {
                "process": self._current_process,
                "title": self._current_title,
                "elapsed_sec": round(time.time() - self._last_switch_time, 1),
                "is_idle": False,
            }
        if self._idle_start:
            return {
                "process": None,
                "title": None,
                "elapsed_sec": round(time.time() - self._idle_start, 1),
                "is_idle": True,
            }
        return {"process": None, "title": None, "elapsed_sec": 0, "is_idle": False}
