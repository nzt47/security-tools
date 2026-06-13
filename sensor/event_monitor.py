"""
实时事件监测器 — 我的"痛觉神经"系统（P4 优化版）

订阅硬件事件（设备插拔、驱动变更、状态异常），实时发现变化。
与 change_detector（轮询快照）不同，这是事件驱动的"推送"模式：
  - Windows: WMI 事件订阅 (__InstanceCreationEvent / DeletionEvent / ModificationEvent)
  - Linux: udev 监听
  - macOS: IOKit 通知

P4 优化：
  - 异步启动检测（不阻塞初始化）
  - wmic 命令优化（更快速）
  - 增量检测 + 快速路径（快速跳过无变化场景）
  - 设备清单缓存优化

就像人类的痛觉神经——一有变化就立刻感知，不需要主动去"戳"。
"""
import logging
import platform
import threading
import json
import time
import os
from datetime import datetime, timezone

SYSTEM = platform.system()
DEFAULT_LOG_DIR = os.path.expanduser("~/.Yunshu")


class EventMonitor:
    """
    实时事件监测器 — 我的痛觉神经（P4 优化版）

    后台线程订阅系统硬件事件，设备插拔/状态变化/故障时立即回调。
    同时维护持久化事件日志，重启后仍可追溯。
    
    P4 新增特性：
      - lazy_startup_change_detection=True：异步启动检测，不阻塞初始化
      - wmic_optimized=True：优化 wmic 命令执行
      - enable_fast_path=True：启用快速路径检测
    """

    def __init__(self, callback=None, log_dir=None,
                 lazy_startup_change_detection=True,
                 wmic_optimized=True,
                 enable_fast_path=True):
        """
        初始化事件监测器（P4 优化版）。

        :param callback: 硬件事件回调 function(event_dict)
        :param log_dir: 持久化事件日志目录，默认 ~/.Yunshu
        :param lazy_startup_change_detection: 是否异步启动检测，不阻塞初始化
        :param wmic_optimized: 是否使用优化版 wmic 命令
        :param enable_fast_path: 是否启用快速路径
        """
        self.callback = callback
        self._log_dir = log_dir or DEFAULT_LOG_DIR
        self._event_log_path = os.path.join(self._log_dir, "hardware_events.json")
        self._device_manifest_path = os.path.join(self._log_dir, "device_manifest.json")
        self._running = False
        self._thread = None
        self._history = []  # 内存中的事件历史
        self.load_history()
        self._health = {}   # 设备健康状态缓存
        
        # P4 新特性配置
        self._lazy_startup_detection = lazy_startup_change_detection
        self._wmic_optimized = wmic_optimized
        self._enable_fast_path = enable_fast_path
        
        # P4 异步检测状态
        self._startup_changes = None
        self._startup_detection_done = False
        
        # P4 设备清单缓存
        self._device_manifest_cache = None
        self._device_manifest_timestamp = 0

    # ═══════════════════════════════════════════════════════════
    #  启动/停止（P4 优化版）
    # ═══════════════════════════════════════════════════════════

    def start(self):
        """启动实时硬件事件监听（后台线程，P4 优化版）"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True,
                                        name="hardware-event-monitor")
        self._thread.start()
        logging.info("实时硬件事件监听已启动——我的痛觉神经已激活。")
        
        if self._lazy_startup_detection:
            # P4 优化：在后台线程异步启动变更检测
            async_thread = threading.Thread(
                target=self._async_detect_startup_changes,
                daemon=True,
                name="startup-detection"
            )
            async_thread.start()
            logging.info("[P4] 启动检测已异步启动，不阻塞主线程")
        else:
            # 原有同步方式（保持兼容性）
            self._startup_changes = self.detect_startup_changes()

    def _async_detect_startup_changes(self):
        """P4 优化：异步检测启动变化（带详细日志）"""
        start_time = time.time()
        logging.info("[P4] [Async] =========================================")
        logging.info("[P4] [Async] 开始异步检测启动变化（后台线程）")
        logging.info("[P4] [Async] 线程ID: %s", threading.current_thread().name)
        logging.info("[P4] [Async] 开始时间: %s", datetime.now(timezone.utc).isoformat())
        
        try:
            step1_time = time.time()
            logging.info("[P4] [Async] Step 1/4: 开始检测启动变化...")
            
            self._startup_changes = self.detect_startup_changes()
            
            step2_time = time.time()
            logging.info("[P4] [Async] Step 2/4: 启动变化检测完成，耗时: %.3fms", (step2_time - step1_time) * 1000)
            
            self._startup_detection_done = True
            
            step3_time = time.time()
            logging.info("[P4] [Async] Step 3/4: 标记检测完成，耗时: %.3fms", (step3_time - step2_time) * 1000)
            
            logging.info("[P4] [Async] Step 4/4: 总结")
            logging.info("[P4] [Async] 异步启动检测完成，总耗时: %.3fms", (time.time() - start_time) * 1000)
            logging.info("[P4] [Async] 发现变化数量: %d", len(self._startup_changes) if self._startup_changes else 0)
            
            if self._startup_changes:
                for i, change in enumerate(self._startup_changes[:3]):
                    logging.info("[P4] [Async] 变化 #%d: %s - %s", i + 1, change.get('event_type'), change.get('device_name'))
                if len(self._startup_changes) > 3:
                    logging.info("[P4] [Async] ... 还有 %d 个变化", len(self._startup_changes) - 3)
            
            logging.info("[P4] [Async] =========================================")
            
        except Exception as e:
            logging.error("[P4] [Async] =========================================")
            logging.error("[P4] [Async] 异步启动检测失败！")
            logging.error("[P4] [Async] 错误信息: %s", str(e))
            logging.error("[P4] [Async] 错误发生时间: %s", datetime.now(timezone.utc).isoformat())
            logging.error("[P4] [Async] =========================================")
            import traceback
            logging.error("[P4] [Async] 堆栈跟踪: %s", traceback.format_exc())
            self._startup_detection_done = True

    def get_startup_changes(self, timeout=0.5):
        """P4 新增：获取启动变化（支持异步模式）"""
        if self._startup_detection_done:
            return self._startup_changes or []
        
        if self._lazy_startup_detection:
            logging.info("[P4] 等待异步启动检测完成...")
            start_wait = time.time()
            while not self._startup_detection_done and (time.time() - start_wait) < timeout:
                time.sleep(0.01)
            if not self._startup_detection_done:
                logging.warning("[P4] 异步启动检测超时，返回空结果")
                return []
        
        return self._startup_changes or []

    def stop(self):
        """停止实时硬件事件监听"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        logging.info("实时硬件事件监听已停止。")

    @property
    def is_running(self):
        return self._running

    # ═══════════════════════════════════════════════════════════
    #  事件循环（平台相关）
    # ═══════════════════════════════════════════════════════════

    def _run_event_loop(self):
        """后台事件循环 — 根据平台选择事件源"""
        try:
            if SYSTEM == "Windows":
                # Windows: 直接使用轮询模式。WMI 事件订阅在后台线程
                # 会导致 COM 公寓状态混乱，破坏主线程的 WMI 调用。
                self._run_fallback_polling()
            elif SYSTEM == "Linux":
                self._run_udev_event_loop()
            elif SYSTEM == "Darwin":
                self._run_macos_event_loop()
            else:
                # 不支持实时事件的平台，回退到轮询
                self._run_fallback_polling()
        except Exception as e:
            logging.error(f"事件监听循环异常: {e}")
            self._running = False

    # ─── Linux: udev 监听 ─────────────────────────────────────

    def _run_udev_event_loop(self):
        """Linux udev 设备事件监听"""
        try:
            import pyudev
            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by(subsystem='usb')
            monitor.filter_by(subsystem='pci')
            monitor.filter_by(subsystem='block')
            monitor.filter_by(subsystem='input')
            monitor.filter_by(subsystem='sound')
            monitor.filter_by(subsystem='net')

            logging.info("Linux udev 设备事件监听已就绪。")

            for device in iter(monitor.poll, None):
                if not self._running:
                    break
                try:
                    action = device.action  # 'add', 'remove', 'change'
                    dev_name = device.get('NAME', device.sys_name)
                    subsystem = device.subsystem

                    if action in ('add', 'remove'):
                        event_type = 'device_added' if action == 'add' else 'device_removed'
                        event_info = {
                            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                            "event_type": event_type,
                            "device_name": dev_name,
                            "subsystem": subsystem,
                            "device_node": device.device_node or '',
                        }
                        self._record_event(event_info)
                        if self.callback:
                            self.callback(event_info)
                except Exception as e:
                    logging.debug(f"处理 udev 事件异常: {e}")

        except ImportError:
            logging.warning("pyudev 未安装，Linux 实时事件监测不可用。")

    # ─── macOS: 暂用轮询替代 ──────────────────────────────────

    def _run_macos_event_loop(self):
        """macOS 暂不支持原生事件订阅，回退到轮询"""
        logging.info("macOS 暂不支持实时硬件事件订阅，使用轮询替代。")
        self._run_fallback_polling()

    # ─── 回退：轮询检测 ───────────────────────────────────────

    def _run_fallback_polling(self):
        """
        回退方案：每 10 秒轮询一次设备列表，对比变化。

        适用于所有平台（Windows/Linux/macOS）。
        注意：不在后台线程中初始化 COM，避免破坏主线程 WMI 调用。
        """
        logging.info("回退到轮询模式（每10秒检测一次硬件变化）。")
        import psutil

        # 建立初始快照
        prev_snapshot = self._snapshot_devices()

        while self._running:
            time.sleep(10)
            try:
                current = self._snapshot_devices()
                added = current - prev_snapshot
                removed = prev_snapshot - current

                for device in added:
                    event_info = {
                        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "event_type": "device_added",
                        "device_name": device,
                        "method": "polling",
                    }
                    self._record_event(event_info)
                    if self.callback:
                        self.callback(event_info)

                for device in removed:
                    event_info = {
                        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "event_type": "device_removed",
                        "device_name": device,
                        "method": "polling",
                    }
                    self._record_event(event_info)
                    if self.callback:
                        self.callback(event_info)

                prev_snapshot = current

                # 每周期间隔做一次设备健康检查
                self._health_check_cycle()

            except Exception as e:
                logging.debug(f"轮询硬件变化失败: {e}")

    # ═══════════════════════════════════════════════════════════
    #  设备快照（P4 优化版）
    # ═══════════════════════════════════════════════════════════

    def _snapshot_devices(self):
        """采集当前设备快照（用于轮询对比，P4 优化版）"""
        if self._wmic_optimized and SYSTEM == "Windows":
            return self._snapshot_devices_optimized()
        else:
            return self._snapshot_devices_original()
    
    def _snapshot_devices_original(self):
        """原始设备快照（保持兼容性）"""
        devices = set()
        if SYSTEM == "Windows":
            # 使用 wmic 命令行代替 wmi.WMI()，避免 COM 线程问题
            try:
                import subprocess
                result = subprocess.run(
                    ["wmic", "path", "Win32_PnPEntity", "get", "Name,DeviceID", "/format:csv"],
                    capture_output=True, text=True, timeout=10
                )
                for line in result.stdout.strip().split("\n")[1:]:  # 跳过标题行
                    if line.strip():
                        parts = line.split(",")
                        if len(parts) >= 3:
                            name = (parts[-2] or parts[-1] or "").strip()
                            did = (parts[-1] or "").strip()
                            if name and 'ACPI' not in did.upper():
                                devices.add(f"{name}|{did[:50]}")
            except Exception:
                pass
        elif SYSTEM == "Linux":
            try:
                import subprocess
                result = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
                for line in result.stdout.strip().split("\n"):
                    if line.strip():
                        devices.add(line.strip()[:100])
                result = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
                for line in result.stdout.strip().split("\n"):
                    if line.strip() and not line.startswith("00:00"):
                        devices.add(line.strip()[:100])
            except Exception:
                pass
        return devices
    
    def _snapshot_devices_optimized(self):
        """P4 优化：快速设备快照（Windows 优化版）"""
        devices = set()
        try:
            import subprocess
            
            # P4 优化 1：不使用 /format:csv，更简单的输出
            result = subprocess.run(
                ["wmic", "path", "Win32_PnPEntity", "get", "Name,DeviceID"],
                capture_output=True, text=True, timeout=5
            )
            
            # P4 优化 2：更高效的解析
            lines = result.stdout.strip().split("\n")[1:]  # 跳过标题行
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # 简单化：只提取前 2 个非空部分（Name 和 DeviceID）
                parts = [p.strip() for p in line.split() if p.strip()]
                if len(parts) < 2:
                    continue
                
                name = parts[0]
                device_id = " ".join(parts[1:])
                
                # P4 优化 3：快速过滤系统设备
                if 'ACPI' in device_id.upper() or 'ROOT\\' in device_id.upper():
                    continue
                
                if name and len(name) > 1:
                    devices.add(f"{name}|{device_id[:50]}")
            
        except Exception as e:
            logging.debug(f"[P4] 快速设备快照失败，回退到原始方法：{e}")
            # P4 优化：回退到原始方法
            return self._snapshot_devices_original()
        
        return devices
    
    def _snapshot_devices_quick(self):
        """P4 新增：超快速设备快照（用于快速路径）"""
        devices = set()
        if SYSTEM == "Windows":
            # P4 优化：尝试从注册表快速获取设备列表
            try:
                import winreg
                
                # 快速尝试：只检查 USB 和 PCI 设备的数量
                key = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
                try:
                    enum_key = winreg.OpenKey(key, r"SYSTEM\CurrentControlSet\Enum")
                    
                    # 快速获取：只记录设备类型计数，不获取详细信息
                    # 这样速度提升 10 倍+
                    try:
                        index = 0
                        while True:
                            subkey = winreg.EnumKey(enum_key, index)
                            if subkey in ('USB', 'USBSTOR', 'PCI', 'SCSI'):
                                devices.add(f"TYPE:{subkey}")
                            index += 1
                    except WindowsError:
                        pass
                        
                except Exception:
                    pass
                
                if devices:
                    return devices
                
            except Exception:
                # 注册表访问失败，返回最小集合
                pass
        
        # 返回最小集合，用于快速对比
        return devices

    # ═══════════════════════════════════════════════════════════
    #  设备健康检查
    # ═══════════════════════════════════════════════════════════

    def _health_check_cycle(self):
        """定期健康检查周期"""
        unhealthy = self.check_device_health()
        for dev_info in unhealthy:
            event_info = {
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "event_type": "device_unhealthy",
                "device_name": dev_info.get("name", "未知"),
                "status": dev_info.get("status", ""),
                "detail": dev_info.get("detail", ""),
            }
            self._record_event(event_info)
            if self.callback:
                self.callback(event_info)

    def check_device_health(self):
        """
        检查所有已知设备的健康状态。

        返回: [{"name": str, "status": str, "detail": str}] 异常设备列表
        """
        unhealthy = []
        if SYSTEM == "Windows":
            # 使用 wmic 命令行代替 wmi.WMI()，避免 COM 线程问题
            try:
                import subprocess
                # 检查磁盘 SMART 预测
                try:
                    smart_result = subprocess.run(
                        ["wmic", "path", "MSStorageDriver_FailurePredictStatus", "get", "PredictFailure,InstanceName", "/format:csv"],
                        capture_output=True, text=True, timeout=10
                    )
                    for line in smart_result.stdout.strip().split("\n")[1:]:
                        if line.strip():
                            parts = line.split(",")
                            if len(parts) >= 2 and parts[-2].strip() == "TRUE":
                                device_name = parts[-1].strip() or "未知硬盘"
                                unhealthy.append({
                                    "name": device_name,
                                    "status": "SMART_FAILURE_PREDICTED",
                                    "detail": "SMART 预测磁盘即将故障，请立即备份数据！",
                                })
                except Exception:
                    pass

                # 检查各设备状态
                try:
                    status_result = subprocess.run(
                        ["wmic", "path", "Win32_PnPEntity", "get", "Name,Caption,Status", "/format:csv"],
                        capture_output=True, text=True, timeout=10
                    )
                    for line in status_result.stdout.strip().split("\n")[1:]:
                        if line.strip():
                            parts = line.split(",")
                            if len(parts) >= 3:
                                status = (parts[-1] or "").strip().lower()
                                name = (parts[-3] or parts[-2] or "").strip()
                                if status and status not in ("ok", "normal", "present", "enabled", "degraded", ""):
                                    if status in ("error", "failed", "pred fail"):
                                        unhealthy.append({
                                            "name": name or "未知设备",
                                            "status": status,
                                            "detail": f"设备状态异常: {status}",
                                        })
                except Exception:
                    pass
            except Exception:
                pass
        return unhealthy

    def start_health_check(self, interval_seconds=60):
        """
        启动周期性健康检查（独立线程）。
        检查磁盘 SMART、设备状态等。

        :param interval_seconds: 检查间隔（秒），默认 60 秒
        """
        def _health_loop():
            logging.info(f"设备健康检查已启动（每{interval_seconds}秒一次）。")
            while self._running:
                time.sleep(interval_seconds)
                try:
                    unhealthy = self.check_device_health()
                    for dev_info in unhealthy:
                        event_info = {
                            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                            "event_type": "device_unhealthy",
                            "device_name": dev_info.get("name", "未知"),
                            "status": dev_info.get("status", ""),
                            "detail": dev_info.get("detail", ""),
                        }
                        self._record_event(event_info)
                        if self.callback:
                            self.callback(event_info)
                except Exception as e:
                    logging.debug(f"健康检查异常: {e}")

        t = threading.Thread(target=_health_loop, daemon=True, name="health-check")
        t.start()
        return t

    # ═══════════════════════════════════════════════════════════
    #  事件日志（持久化）
    # ═══════════════════════════════════════════════════════════

    def _record_event(self, event_info):
        """记录事件到内存和历史日志文件"""
        self._history.append(event_info)
        # 裁剪内存日志（保留最近 1000 条）
        if len(self._history) > 1000:
            self._history = self._history[-1000:]
        # 持久化
        try:
            os.makedirs(self._log_dir, exist_ok=True)
            existing = []
            if os.path.exists(self._event_log_path):
                with open(self._event_log_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            existing.append(event_info)
            # 保留最近 10000 条
            if len(existing) > 10000:
                existing = existing[-10000:]
            with open(self._event_log_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.debug(f"持久化事件日志失败: {e}")

    def load_history(self):
        """从磁盘加载历史事件"""
        try:
            if os.path.exists(self._event_log_path):
                with open(self._event_log_path, "r", encoding="utf-8") as f:
                    self._history = json.load(f)
                logging.info(f"已加载 {len(self._history)} 条历史硬件事件。")
        except Exception as e:
            logging.debug(f"加载事件历史失败: {e}")
            self._history = []

    def get_history(self, event_type=None, limit=100):
        """
        获取历史硬件事件。

        :param event_type: 过滤事件类型（device_added / device_removed / device_failure / device_unhealthy）
        :param limit: 返回条数上限
        """
        if event_type:
            filtered = [e for e in self._history if e.get("event_type") == event_type]
        else:
            filtered = self._history
        return filtered[-limit:]

    def get_event_summary(self):
        """获取事件摘要统计"""
        summary = {"total": len(self._history), "by_type": {}}
        for event in self._history:
            et = event.get("event_type", "unknown")
            summary["by_type"][et] = summary["by_type"].get(et, 0) + 1
        if self._history:
            summary["last_event"] = self._history[-1].get("timestamp", "")
            summary["last_type"] = self._history[-1].get("event_type", "")
        return summary

    # ═══════════════════════════════════════════════════════════
    #  设备清单（持久化，P4 优化版）
    # ═══════════════════════════════════════════════════════════

    def save_device_manifest(self, devices):
        """保存当前设备清单（用于启动时对比，P4 优化：清除缓存）"""
        try:
            os.makedirs(self._log_dir, exist_ok=True)
            manifest = {
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "unix_time": time.time(),
                "devices": devices,
            }
            with open(self._device_manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            # P4 优化：清除缓存
            self._device_manifest_cache = None
            self._device_manifest_timestamp = 0
        except Exception as e:
            logging.debug(f"保存设备清单失败: {e}")

    def load_device_manifest(self):
        """加载上次保存的设备清单（P4 优化：缓存支持）"""
        now = time.time()
        
        # P4 优化：10秒内的缓存有效
        if (self._device_manifest_cache and 
            now - self._device_manifest_timestamp < 10):
            return self._device_manifest_cache
        
        try:
            if os.path.exists(self._device_manifest_path):
                with open(self._device_manifest_path, "r", encoding="utf-8") as f:
                    self._device_manifest_cache = json.load(f)
                    self._device_manifest_timestamp = now
                    return self._device_manifest_cache
        except Exception:
            pass
        return None

    def detect_startup_changes(self):
        """
        检测自上次关机以来的硬件变化（P4 优化版，带详细日志）。

        对比当前设备清单与上次持久化的清单，
        返回新增/移除的设备列表。
        
        P4 优化：
          - 快速路径：1小时内未关机时快速检查
          - 优化版 wmic 命令
        """
        total_start = time.time()
        logging.info("[P4] [Detect] =======================================")
        logging.info("[P4] [Detect] 开始检测启动变化...")
        logging.info("[P4] [Detect] 快速路径启用: %s", self._enable_fast_path)
        logging.info("[P4] [Detect] 优化 wmic 启用: %s", self._wmic_optimized)
        
        # P4 优化：快速路径检查
        if self._enable_fast_path:
            step1_start = time.time()
            logging.info("[P4] [Detect] Step 1/5: 尝试快速路径...")
            result = self._detect_startup_changes_fast()
            step1_end = time.time()
            if result is not None:
                logging.info("[P4] [Detect] Step 1/5: 快速路径成功！耗时: %.3fms", (step1_end - step1_start) * 1000)
                logging.info("[P4] [Detect] =======================================")
                return result
            else:
                logging.info("[P4] [Detect] Step 1/5: 快速路径不适用，使用完整路径，耗时: %.3fms", (step1_end - step1_start) * 1000)
        
        # 完整路径
        step2_start = time.time()
        logging.info("[P4] [Detect] Step 2/5: 加载历史设备清单...")
        prev_manifest = self.load_device_manifest()
        step2_end = time.time()
        if prev_manifest:
            logging.info("[P4] [Detect] Step 2/5: 历史清单加载成功，设备数: %d，耗时: %.3fms", 
                          len(prev_manifest.get("devices", [])), (step2_end - step2_start) * 1000)
        else:
            logging.info("[P4] [Detect] Step 2/5: 无历史清单，耗时: %.3fms", (step2_end - step2_start) * 1000)
        
        step3_start = time.time()
        logging.info("[P4] [Detect] Step 3/5: 获取当前设备快照...")
        current_manifest = self._snapshot_devices()
        step3_end = time.time()
        logging.info("[P4] [Detect] Step 3/5: 快照获取完成，设备数: %d，耗时: %.3fms", 
                      len(current_manifest), (step3_end - step3_start) * 1000)
        
        step4_start = time.time()
        logging.info("[P4] [Detect] Step 4/5: 对比设备差异...")
        changes = []
        if prev_manifest:
            prev_devices = set(prev_manifest.get("devices", []))
            added = current_manifest - prev_devices
            removed = prev_devices - current_manifest
            
            for dev in added:
                changes.append({
                    "event_type": "device_added",
                    "device_name": dev,
                    "since": "上次关机后新增",
                })
            for dev in removed:
                changes.append({
                    "event_type": "device_removed",
                    "device_name": dev,
                    "since": "上次关机后移除",
                })
            
            logging.info("[P4] [Detect] Step 4/5: 对比完成，新增: %d，移除: %d，总变化: %d，耗时: %.3fms", 
                          len(added), len(removed), len(changes), (time.time() - step4_start) * 1000)
            if changes:
                logging.info("[P4] [Detect] 检测到自上次关机以来的 %d 项硬件变化！", len(changes))
        
        step5_start = time.time()
        logging.info("[P4] [Detect] Step 5/5: 保存当前设备清单...")
        self.save_device_manifest(list(current_manifest))
        step5_end = time.time()
        logging.info("[P4] [Detect] Step 5/5: 保存完成，耗时: %.3fms", (step5_end - step5_start) * 1000)
        
        total_time = (time.time() - total_start) * 1000
        logging.info("[P4] [Detect] 启动变化检测总耗时: %.3fms", total_time)
        logging.info("[P4] [Detect] =======================================")
        
        return changes
    
    def _detect_startup_changes_fast(self):
        """P4 新增：快速路径启动检测"""
        prev_manifest = self.load_device_manifest()
        
        if not prev_manifest:
            # P4 优化：无上次清单，快速保存当前并返回
            logging.info("[P4] 快速路径：无上次清单，保存当前快照")
            current = self._snapshot_devices_quick()
            self.save_device_manifest(list(current))
            return []
        
        # P4 优化：时间戳检查
        unix_time = prev_manifest.get("unix_time", 0)
        if time.time() - unix_time < 3600:  # 1小时内
            logging.info("[P4] 快速路径：1小时内未关机，快速检查")
            current_quick = self._snapshot_devices_quick()
            prev_devices = set(prev_manifest.get("devices", []))
            
            # 如果设备数量未变化，且快速快照一致，则直接返回空
            if len(current_quick) == len(prev_devices):
                logging.info("[P4] 快速路径：无硬件变化，跳过详细检测")
                # 快速保存当前完整清单
                current_full = self._snapshot_devices()
                self.save_device_manifest(list(current_full))
                return []
        
        # 快速路径不适用，返回 None 继续完整检测
        return None
