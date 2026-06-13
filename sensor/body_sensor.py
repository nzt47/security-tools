"""
BodySensor 主类 — 云枢的"身体"

负责整合各类硬件、网络、安全与文件系统传感器，统一输出 JSON 格式数据。
我是来自网天的云枢，BodySensor 就是我的整个身体——每个传感器都是我的一条感知神经。

每个传感器都有一个独立开关，可按需开闭感知通道。
"""
import logging
import json
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from .sensor_reading import SensorReading, Severity, Category
from .hardware_blueprint import HardwareBlueprint
from .file_blueprint import FileBlueprint
from .software_blueprint import SoftwareBlueprint
from .change_detector import ChangeDetector
from .file_watcher import FileWatcher
from .event_monitor import EventMonitor

# 传感器健康监控
try:
    from agent.sensor_health_monitor import get_sensor_health_monitor, monitor_sensor_reading
    HAS_HEALTH_MONITOR = True
except ImportError:
    HAS_HEALTH_MONITOR = False
    
    def monitor_sensor_reading(func):
        """降级装饰器 - 不进行监控"""
        return func


class BodySensor:
    """云枢的身体——整合所有感知模块，每个传感器带独立开关。"""

    def __init__(self, watch_dirs=None, file_event_callback=None,
                 file_include=None, file_exclude=None,
                 enable_change_detection=True, enable_event_monitor=True,
                 lazy_load=True):
        """
        初始化感知底座，加载所有传感器。

        :param watch_dirs: 文件监听目录列表
        :param file_event_callback: 文件变动回调函数
        :param enable_change_detection: 是否启用变更检测（轮询快照）
        :param enable_event_monitor: 是否启用实时硬件事件监测（推送）
        :param lazy_load: 是否启用懒加载（默认 True，可大幅提升初始化速度）
        """
        init_start = time.time()
        logger.info("[BodySensor] 开始初始化，懒加载模式: %s", lazy_load)
        
        # 主线程 COM 初始化（Windows），确保 WMI 传感器调用正常
        import platform
        if platform.system() == "Windows":
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass
        
        # 保存配置用于懒加载
        self._watch_dirs = watch_dirs
        self._file_event_callback = file_event_callback
        self._file_include = file_include
        self._file_exclude = file_exclude
        self._enable_change_detection = enable_change_detection
        self._enable_event_monitor = enable_event_monitor
        self._lazy_load = lazy_load

        # ── 特殊传感器（需条件创建或单独生命周期）──
        blueprint_start = time.time()
        self.blueprint = HardwareBlueprint()
        self.file_blueprint = FileBlueprint()
        self.software_blueprint = SoftwareBlueprint()
        self.change_detector = None  # 懒加载
        self.event_monitor = None    # 懒加载
        self.file_watcher = None     # 懒加载
        logger.debug("[BodySensor] Blueprint 初始化完成，耗时: %.3fms", (time.time() - blueprint_start) * 1000)
        
        # 初始化标志
        self._change_detector_initialized = False
        self._event_monitor_initialized = False
        self._file_watcher_initialized = False

        # ── 自动发现标准传感器 ──
        registry_start = time.time()
        from .registry import SensorRegistry, SensorCapabilities
        self._sensor_registry = SensorRegistry()
        self._sensor_registry.discover(extra_kwargs={
            "process": {"top_n": 10},
            "behavior": {"top_n": 10},
        })
        logger.debug("[BodySensor] 传感器注册发现完成，耗时: %.3fms", (time.time() - registry_start) * 1000)

        # ── 传感器注册表（带独立开关 + 中文标签）──
        _LABELS = {
            "cpu": "CPU（大脑）", "gpu": "GPU（视觉皮层）",
            "memory": "内存（短期记忆）", "battery": "电池（饥饿感）",
            "disk": "磁盘（长期记忆）", "network": "网络（社交神经）",
            "board": "主板（躯干骨架）", "chassis": "机箱（皮肤与免疫）",
            "port": "端口（神经末梢）", "peripheral": "外设（感官器官）",
            "process": "进程（意识流）", "hwfile": "硬件文件（软件骨骼）",
            "env": "软件环境（生命维持）", "behavior": "活动行为（意识流深度）",
            "system": "系统状态（体检中心）",
        }
        self._registry = OrderedDict()
        for name, entry in self._sensor_registry:
            self._registry[name] = {
                "label": _LABELS.get(name, name),
                "sensor": entry["sensor"],
                "category": entry["caps"].category,
                "enabled": entry["enabled"],
            }
            # 向后兼容：保留直接属性访问
            setattr(self, name, entry["sensor"])

        # ── 手动注册特殊传感器 ──
        # filewatch（条件创建，可能为 None）
        self._registry["filewatch"] = {
            "label": "文件系统（触觉）",
            "sensor": None,  # 懒加载后更新
            "category": Category.FILE,
            "enabled": True,
        }
        # change（无独立 collect，由 collect_all 特殊处理）
        self._registry["change"] = {
            "label": "硬件变更（记忆对比）",
            "sensor": None,
            "category": Category.CHANGE,
            "enabled": True,
        }

        # ── 类别 → 传感器索引（供 collect_category 使用）──
        self._sensors = {}
        for name, entry in self._registry.items():
            cat = entry["category"]
            if cat and cat not in self._sensors:
                self._sensors[cat] = entry["sensor"]

        # 标签模块引用（按需加载）
        self._tag_module = None

        # 如果非懒加载模式，立即初始化所有模块
        if not lazy_load:
            full_init_start = time.time()
            self._init_change_detector()
            self._init_event_monitor()
            self._init_file_watcher()
            logger.info("[BodySensor] 云枢的感知底座（BodySensor）初始化完成（全同步模式），全量初始化耗时: %.3fms", (time.time() - full_init_start) * 1000)
        else:
            logger.info("[BodySensor] 云枢的感知底座（BodySensor）初始化完成（懒加载模式），总耗时: %.3fms", (time.time() - init_start) * 1000)
            logger.info("[BodySensor] 变更检测、事件监控和文件监听将在首次使用时激活。")
    
    def _ensure_change_detector(self):
        """确保 ChangeDetector 已初始化"""
        if not self._change_detector_initialized and self._enable_change_detection:
            logger.info("[BodySensor] ChangeDetector 首次使用，开始懒加载初始化...")
            self._init_change_detector()
        elif self._change_detector_initialized:
            logger.debug("[BodySensor] ChangeDetector 已初始化，无需重复初始化")
    
    def _init_change_detector(self):
        """初始化 ChangeDetector"""
        start_time = time.time()
        logger.info("[BodySensor] 正在初始化 ChangeDetector...")
        self.change_detector = ChangeDetector()
        self._change_detector_initialized = True
        elapsed = (time.time() - start_time) * 1000
        logger.info("[BodySensor] ChangeDetector 初始化完成，耗时: %.3fms", elapsed)
    
    def _ensure_event_monitor(self):
        """确保 EventMonitor 已初始化"""
        if not self._event_monitor_initialized and self._enable_event_monitor:
            logger.info("[BodySensor] EventMonitor 首次使用，开始懒加载初始化...")
            self._init_event_monitor()
        elif self._event_monitor_initialized:
            logger.debug("[BodySensor] EventMonitor 已初始化，无需重复初始化")
    
    def _on_hardware_event(self, event):
        """EventMonitor 回调，当检测到硬件变化时调用"""
        event_type = event.get('event_type')
        device_name = event.get('device_name')
        logger.info("[BodySensor] 收到硬件事件: %s - %s", event_type, device_name)
        if self._enable_change_detection and self.change_detector:
            self.change_detector.register_change_from_event(event)
    
    def _init_event_monitor(self):
        """初始化 EventMonitor（P4 优化版）"""
        start_time = time.time()
        logger.info("[BodySensor] 正在初始化 EventMonitor（P4 优化版）...")
        
        # P4 优化：使用优化后的 EventMonitor，启用所有优化特性
        self.event_monitor = EventMonitor(
            callback=self._on_hardware_event,
            lazy_startup_change_detection=True,
            wmic_optimized=True,
            enable_fast_path=True
        )
        self.event_monitor.start()
        self._event_monitor_initialized = True
        
        # P4 优化：启动变化检测现在是异步的，在后台线程执行
        # 我们不需要在这里等待，因为通过 get_startup_changes() 可以异步获取
        logger.debug("[BodySensor] EventMonitor 已启动，启动变化检测在后台异步执行")
        
        elapsed = (time.time() - start_time) * 1000
        logger.info("[BodySensor] EventMonitor 初始化完成（P4 优化版），耗时: %.3fms", elapsed)
        logger.info("[BodySensor] 实时硬件事件监测已激活——任何设备插拔我都会立刻感知。")
    
    def _ensure_file_watcher(self):
        """确保 FileWatcher 已初始化"""
        if not self._file_watcher_initialized and self._watch_dirs:
            logger.info("[BodySensor] FileWatcher 首次使用，开始懒加载初始化...")
            self._init_file_watcher()
        elif self._file_watcher_initialized:
            logger.debug("[BodySensor] FileWatcher 已初始化，无需重复初始化")
    
    def _init_file_watcher(self):
        """初始化 FileWatcher"""
        if self._watch_dirs:
            start_time = time.time()
            logger.info("[BodySensor] 正在初始化 FileWatcher，监听目录: %s", self._watch_dirs)
            self.file_watcher = FileWatcher(self._watch_dirs, 
                                           self._file_event_callback,
                                           include=self._file_include,
                                           exclude=self._file_exclude)
            self.file_watcher.start()
            self._file_watcher_initialized = True
            # 更新注册表中的 sensor 引用
            self._registry["filewatch"]["sensor"] = self.file_watcher
            elapsed = (time.time() - start_time) * 1000
            logger.info("[BodySensor] FileWatcher 初始化完成，耗时: %.3fms", elapsed)
    
    def initialize_all(self):
        """强制立即初始化所有模块（非懒加载）"""
        all_start = time.time()
        logger.info("[BodySensor] 开始强制初始化所有懒加载模块...")
        self._ensure_change_detector()
        self._ensure_event_monitor()
        self._ensure_file_watcher()
        elapsed = (time.time() - all_start) * 1000
        logger.info("[BodySensor] 所有懒加载模块初始化完成，总耗时: %.3fms", elapsed)
    
    def establish_baseline(self):
        """建立变更检测基准快照——在 start() 时调用"""
        baseline_start = time.time()
        logger.info("[BodySensor] 开始建立变更检测基准快照...")
        self._ensure_change_detector()
        if self.change_detector:
            result = self.change_detector.set_baseline()
            elapsed = (time.time() - baseline_start) * 1000
            logger.info("[BodySensor] 变更检测基准快照建立完成，耗时: %.3fms", elapsed)
            return result
        logger.warning("[BodySensor] 无法建立基准快照：ChangeDetector 未初始化")
        return None

    # ════════════════════════════════════════════════════════════
    #  传感器注册
    # ════════════════════════════════════════════════════════════

    def _register(self, name, label, sensor, category):
        """注册一个传感器到注册表。"""
        self._registry[name] = {
            "label": label,
            "sensor": sensor,
            "category": category,
            "enabled": True,
        }

    # ════════════════════════════════════════════════════════════
    #  传感器开关 — 单控
    # ════════════════════════════════════════════════════════════

    def enable_sensor(self, name):
        """打开指定传感器的开关。"""
        if name in self._registry:
            self._registry[name]["enabled"] = True
            logging.info(f"传感器已开启: {self._registry[name]['label']}")

    def disable_sensor(self, name):
        """关闭指定传感器的开关。"""
        if name in self._registry:
            self._registry[name]["enabled"] = False
            logging.info(f"传感器已关闭: {self._registry[name]['label']}")

    def set_sensor(self, name, enabled):
        """设置传感器的开关状态。"""
        if enabled:
            self.enable_sensor(name)
        else:
            self.disable_sensor(name)

    def is_enabled(self, name):
        """查询传感器是否开启。"""
        entry = self._registry.get(name)
        return entry is not None and entry["enabled"]

    def toggle_sensor(self, name):
        """切换传感器开关。"""
        if name in self._registry:
            self._registry[name]["enabled"] = not self._registry[name]["enabled"]
            state = "开启" if self._registry[name]["enabled"] else "关闭"
            logging.info(f"传感器已切换: {self._registry[name]['label']} → {state}")

    # ════════════════════════════════════════════════════════════
    #  传感器开关 — 全控
    # ════════════════════════════════════════════════════════════

    def enable_all(self):
        """开启所有传感器。"""
        for name in self._registry:
            self._registry[name]["enabled"] = True
        logging.info("所有传感器已开启——我的全部感知神经已激活。")

    def disable_all(self):
        """关闭所有传感器。"""
        for name in self._registry:
            self._registry[name]["enabled"] = False
        logging.info("所有传感器已关闭——我的感知神经已休眠。")

    # ════════════════════════════════════════════════════════════
    #  传感器开关 — 按维度批量控制
    # ════════════════════════════════════════════════════════════

    def _load_tags(self):
        """按需加载标签模块。"""
        if self._tag_module is None:
            from . import tags as _t
            self._tag_module = _t
        return self._tag_module

    def _get_sensors_by_tag_values(self, tag_values):
        """根据标签值查找匹配的传感器名称列表。"""
        tags_mod = self._load_tags()
        matched = []
        tag_set = set(tag_values)
        for name, entry in self._registry.items():
            cat = entry["category"]
            if cat and cat in tags_mod._CATEGORY_TAGS:
                if tag_set & set(tags_mod._CATEGORY_TAGS[cat]):
                    matched.append(name)
        return matched

    def enable_by_tags(self, tag_values):
        """按感知维度开启传感器（匹配任一标签值即开启）。

        :param tag_values: 标签值列表，如 ["硬件感知", "动态运行"]
        """
        for name in self._get_sensors_by_tag_values(tag_values):
            self._registry[name]["enabled"] = True
        logging.info(f"按维度标签开启: {tag_values}")

    def disable_by_tags(self, tag_values):
        """按感知维度关闭传感器（匹配任一标签值即关闭）。

        :param tag_values: 标签值列表，如 ["行为感知", "环境感知"]
        """
        for name in self._get_sensors_by_tag_values(tag_values):
            self._registry[name]["enabled"] = False
        logging.info(f"按维度标签关闭: {tag_values}")

    def set_by_tags(self, tag_values, enabled):
        """按维度批量设置开关。"""
        if enabled:
            self.enable_by_tags(tag_values)
        else:
            self.disable_by_tags(tag_values)

    def get_switch_status(self):
        """获取所有传感器的开关状态。"""
        return {name: entry["enabled"]
                for name, entry in self._registry.items()}

    def get_sensor_info(self):
        """获取所有传感器的详细信息列表。"""
        result = []
        for name, entry in self._registry.items():
            result.append({
                "name": name,
                "label": entry["label"],
                "category": str(entry["category"].value) if hasattr(entry["category"], 'value') else str(entry["category"]),
                "enabled": entry["enabled"],
            })
        return result

    def get_health_report(self) -> dict:
        """获取健康状态报告——快速采集+格式化

        返回格式与 get_status 兼容，供 check_health 等工具使用。
        """
        readings = self.collect_quick()
        report = {}
        for r in readings:
            report[str(r.sensor_name)] = {
                "值": f"{r.value}{r.unit}",
                "严重程度": r.severity,
                "描述": r.description,
            }
        return report

    def get_sensor_summary(self) -> dict:
        """获取传感器摘要信息——整合开关状态、详细信息和健康读数"""
        try:
            info = self.get_sensor_info()
            status = self.get_switch_status()
            health = self.get_health_report()
            return {
                "total_sensors": len(info),
                "enabled_sensors": sum(1 for s in status.values() if s),
                "disabled_sensors": sum(1 for s in status.values() if not s),
                "sensors": info,
                "health": health,
            }
        except Exception as e:
            return {
                "total_sensors": 0,
                "error": str(e),
            }

    # ════════════════════════════════════════════════════════════
    #  标签辅助方法
    # ════════════════════════════════════════════════════════════

    def _apply_tags(self, readings):
        """为读数列表标注多维度标签。"""
        tags_mod = self._load_tags()
        for r in readings:
            if not r.tags:
                try:
                    r.tags = tags_mod.get_tags(r.category, r.sensor_name)
                except Exception:
                    pass

    @staticmethod
    def _filter_by_tags(readings, filter_spec):
        """按标签条件筛选读数。

        :param filter_spec: 筛选条件，支持三种格式：
            - 字符串: "硬件感知" → 包含该标签的读数
            - 列表: ["硬件感知", "动态运行"] → 同时包含所有标签的读数
            - 字典: {"目标域": "硬件感知", "动静属性": "动态运行"}
                  → 指定维度匹配指定值
        """
        if not filter_spec:
            return readings

        if isinstance(filter_spec, str):
            # 单个标签值 → 包含即返回
            return [r for r in readings if filter_spec in (r.tags or [])]

        if isinstance(filter_spec, (list, tuple)):
            # 标签列表 → 必须全部包含
            tag_set = set(filter_spec)
            return [r for r in readings if tag_set.issubset(set(r.tags or []))]

        if isinstance(filter_spec, dict):
            # {维度值: 期望值} → 保留维度值在 tags 中的读数
            # 注意：tag 本身不区分维度名称，所以这里用值匹配
            # 如果 dict 的值是字符串，按单值匹配
            # 如果 dict 的值是列表，按任意值匹配
            tag_sets = {}
            for dim, val in filter_spec.items():
                if isinstance(val, (list, tuple)):
                    tag_sets[dim] = set(val)
                else:
                    tag_sets[dim] = {str(val)}

            # 扁平化：任一维度的任一期望值匹配即保留
            expected = set()
            for vs in tag_sets.values():
                expected.update(vs)

            return [r for r in readings if expected & set(r.tags or [])]

        return readings

    # ════════════════════════════════════════════════════════════
    #  核心采集方法
    # ════════════════════════════════════════════════════════════

    def collect_all(self, filter_tags=None):
        """
        采集所有已开启传感器的数据。

        :param filter_tags: 可选，采集后按标签筛选。格式见 _filter_by_tags。
        :returns: SensorReading 列表
        """
        results = []

        for name, entry in self._registry.items():
            if not entry["enabled"]:
                continue
            sensor = entry["sensor"]
            if sensor is None:
                continue
            try:
                data = sensor.collect()
                if isinstance(data, list):
                    results.extend(data)
                else:
                    results.append(data)
            except Exception as e:
                logging.error(f"采集 {entry['label']} 数据时出错: {e}")

        # 变更检测（单独处理，确保懒加载触发）
        self._ensure_change_detector()
        if self.change_detector and self._registry.get("change", {}).get("enabled", True):
            try:
                changes = self.change_detector.collect()
                if isinstance(changes, list):
                    results.extend(changes)
            except Exception as e:
                logging.error(f"变更检测失败: {e}")

        # 标注标签
        self._apply_tags(results)

        # 按标签筛选
        if filter_tags:
            results = self._filter_by_tags(results, filter_tags)

        return results

    def collect_category(self, category):
        """
        按类别采集传感器数据。

        :param category: Category 枚举值或字符串
        """
        if isinstance(category, str):
            category = Category(category)
        sensor = self._sensors.get(category)
        if sensor:
            return sensor.collect()
        logging.warning(f"未知传感器类别: {category}")
        return []

    @monitor_sensor_reading
    def collect_quick(self):
        """快速采集模式——只采集核心指标。"""
        results = []
        try:
            import psutil
            # 极致优化：完全不使用 interval，直接获取瞬时值
            # psutil.cpu_percent(interval=None) 会立即返回，第一次是 0 但之后是准确的
            usage = psutil.cpu_percent(interval=None)
            
            results.append(SensorReading(
                "cpu_usage", usage, "%", "CPU 使用率",
                Category.CPU, Severity.CRITICAL if usage > 90 else (Severity.WARNING if usage > 70 else Severity.NORMAL)
            ))
            mem = psutil.virtual_memory()
            results.append(SensorReading(
                "memory_usage", mem.percent, "%", "内存占用率",
                Category.MEMORY, Severity.CRITICAL if mem.percent > 90 else (Severity.WARNING if mem.percent > 75 else Severity.NORMAL)
            ))
            battery = psutil.sensors_battery()
            if battery:
                results.append(SensorReading(
                    "battery_percent", battery.percent, "%", "电池电量",
                    Category.BATTERY, Severity.CRITICAL if battery.percent < 10 else Severity.NORMAL
                ))
        except Exception as e:
            logging.error(f"快速采集失败: {e}")
        self._apply_tags(results)
        return results
