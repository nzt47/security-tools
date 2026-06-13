"""
EnhancedTraceRecorder - 增强版数据采集器
整合窗口监控、OCR 识别等高级感知源
"""

import logging
import threading
import time
from typing import Dict, Any, Optional, Callable, List
from pathlib import Path

from .memory_tree import SourceTree, TopicTree, GlobalTree
from .trace_recorder import TraceRecorder

logger = logging.getLogger(__name__)


class EnhancedTraceRecorder(TraceRecorder):
    """增强版 TraceRecorder - 整合高级感知源"""

    def __init__(self, data_dir: str = "./data/lifetrace"):
        """
        初始化增强版记录器

        继承 TraceRecorder 的所有功能，并新增：
        - 窗口活动监控
        - OCR 屏幕识别
        - 应用使用统计
        """
        super().__init__(data_dir)

        # 窗口监控
        self._window_sensor = None
        self._window_callback = None
        self._is_monitoring_windows = False

        # OCR
        self._ocr_sensor = None
        self._ocr_callback = None

        # 应用统计
        self._app_usage: Dict[str, float] = {}
        self._current_app_start: Optional[str] = None
        self._app_start_time: Optional[float] = None

        logger.info("EnhancedTraceRecorder 初始化完成")

    # ════════════════════════════════════════════════════════════
    #  窗口活动监控
    # ════════════════════════════════════════════════════════════

    def enable_window_monitoring(self, poll_interval: float = 1.0):
        """
        启用窗口活动监控

        Args:
            poll_interval: 轮询间隔（秒）
        """
        if self._is_monitoring_windows:
            logger.warning("窗口监控已在运行")
            return

        try:
            from sensor.window_sensor import WindowSensor

            def on_window_event(event_type: str, data: Dict):
                """窗口事件回调"""
                if event_type == "window_event":
                    self.record_window(
                        window_title=data.get("to_title", ""),
                        event_type=data.get("action", ""),
                        metadata={
                            "from_process": data.get("from_process"),
                            "to_process": data.get("to_process"),
                            "duration_sec": data.get("duration_sec"),
                        }
                    )

                    # 更新应用使用统计
                    to_proc = data.get("to_process")
                    if to_proc:
                        self._update_app_usage(to_proc)

                elif event_type == "idle_start":
                    self.record_window(
                        window_title="空闲",
                        event_type="idle",
                        metadata={"duration_sec": data.get("duration_sec")}
                    )

                elif event_type == "idle_end":
                    self.record_window(
                        window_title=data.get("to_title", ""),
                        event_type="active",
                        metadata={"idle_duration": data.get("duration_sec")}
                    )

            self._window_callback = on_window_event
            self._window_sensor = WindowSensor(
                config_path="./data/window_config.json",
                save_callback=on_window_event
            )

            # 配置并启动
            self._window_sensor.save_config({
                "enabled": True,
                "poll_interval_sec": poll_interval,
            })
            self._window_sensor.start()
            self._is_monitoring_windows = True

            logger.info(f"窗口活动监控已启用（间隔 {poll_interval}s）")

        except ImportError as e:
            logger.error(f"WindowSensor 导入失败: {e}")
        except Exception as e:
            logger.error(f"窗口监控启动失败: {e}")

    def disable_window_monitoring(self):
        """禁用窗口活动监控"""
        if self._window_sensor:
            self._window_sensor.stop()
            self._is_monitoring_windows = False
            logger.info("窗口活动监控已禁用")

    def _update_app_usage(self, process_name: str):
        """更新应用使用时间统计"""
        now = time.time()

        # 结束上一个应用的时间统计
        if self._current_app_start and self._current_app_start != process_name:
            elapsed = now - self._app_start_time
            if self._current_app_start in self._app_usage:
                self._app_usage[self._current_app_start] += elapsed
            else:
                self._app_usage[self._current_app_start] = elapsed

        # 开始新应用的时间统计
        self._current_app_start = process_name
        self._app_start_time = now

    def get_app_usage_stats(self, since_hours: float = 24) -> Dict[str, float]:
        """
        获取应用使用统计

        Args:
            since_hours: 统计时间范围（小时）

        Returns:
            {应用名: 使用时长(秒)}
        """
        # 完成当前应用的统计
        if self._current_app_start:
            elapsed = time.time() - self._app_start_time
            self._app_usage[self._current_app_start] = \
                self._app_usage.get(self._current_app_start, 0) + elapsed

        return dict(self._app_usage)

    def get_most_used_apps(self, limit: int = 10) -> List[tuple]:
        """
        获取最常用的应用

        Returns:
            [(应用名, 使用时长秒), ...]
        """
        stats = self.get_app_usage_stats()
        sorted_apps = sorted(stats.items(), key=lambda x: x[1], reverse=True)
        return sorted_apps[:limit]

    # ════════════════════════════════════════════════════════════
    #  OCR 屏幕识别
    # ════════════════════════════════════════════════════════════

    def enable_ocr(self, capture_interval: float = 30.0):
        """
        启用 OCR 屏幕识别

        Args:
            capture_interval: 捕获间隔（秒）
        """
        if self._ocr_sensor:
            logger.warning("OCR 已在运行")
            return

        try:
            from sensor.ocr_sensor import OcrSensor

            def on_ocr_result(ocr_type: str, data: Dict):
                """OCR 结果回调"""
                if data.get("has_content"):
                    self.add_to_topic(
                        topic="屏幕内容",
                        content=data.get("text", ""),
                        tags=["ocr", "screen_content", data.get("timestamp", "")]
                    )

                    # 也记录到来源树
                    self.source_tree.add_node(
                        content=data.get("text", "")[:500],
                        node_type="leaf",
                        metadata={
                            "source": "ocr",
                            "ocr_type": ocr_type,
                            "timestamp": data.get("timestamp"),
                            "word_count": data.get("word_count", 0),
                        },
                        tags=["ocr", ocr_type]
                    )

            self._ocr_callback = on_ocr_result
            self._ocr_sensor = OcrSensor(
                config_path="./data/ocr_config.json",
                save_callback=on_ocr_result
            )

            # 保存配置
            self._ocr_sensor.save_config({
                "enabled": True,
                "capture_cooldown_sec": capture_interval,
            })

            logger.info(f"OCR 已启用（间隔 {capture_interval}s）")

        except ImportError as e:
            logger.error(f"OCR 库导入失败: {e}。请安装: pip install pytesseract opencv-python mss")
        except Exception as e:
            logger.error(f"OCR 启动失败: {e}")

    def disable_ocr(self):
        """禁用 OCR"""
        self._ocr_sensor = None
        logger.info("OCR 已禁用")

    def capture_now(self) -> Dict[str, Any]:
        """
        立即捕获当前屏幕内容

        Returns:
            OCR 结果字典
        """
        if not self._ocr_sensor:
            return {"error": "OCR 未启用"}

        try:
            return self._ocr_sensor.capture_and_recognize()
        except Exception as e:
            logger.error(f"屏幕捕获失败: {e}")
            return {"error": str(e)}

    def capture_window_now(self) -> Dict[str, Any]:
        """
        立即捕获当前窗口内容

        Returns:
            OCR 结果字典
        """
        if not self._ocr_sensor:
            return {"error": "OCR 未启用"}

        try:
            return self._ocr_sensor.capture_window()
        except Exception as e:
            logger.error(f"窗口捕获失败: {e}")
            return {"error": str(e)}

    # ════════════════════════════════════════════════════════════
    #  高级记录方法
    # ════════════════════════════════════════════════════════════

    def record_user_activity(
        self,
        activity_type: str,
        content: str,
        metadata: Optional[Dict] = None
    ):
        """
        记录用户活动

        Args:
            activity_type: 活动类型 (work/learn/entertainment/idle)
            content: 活动内容
            metadata: 额外元数据
        """
        node = self.source_tree.add_node(
            content=content,
            node_type="leaf",
            metadata={
                "source": "user_activity",
                "activity_type": activity_type,
                **(metadata or {})
            },
            tags=["activity", activity_type]
        )

        # 添加到对应主题
        topic_map = {
            "work": "工作",
            "learn": "学习",
            "entertainment": "娱乐",
            "idle": "空闲",
        }
        topic = topic_map.get(activity_type, "其他")
        self.add_to_topic(topic, content, ["activity", activity_type])

        return node

    def record_context_snapshot(
        self,
        window_title: str,
        screen_text: str,
        metadata: Optional[Dict] = None
    ):
        """
        记录上下文快照（窗口 + 屏幕内容）

        Args:
            window_title: 当前窗口标题
            screen_text: 屏幕 OCR 文字
            metadata: 额外元数据
        """
        snapshot_content = f"[{window_title}]\n{screen_text[:1000]}"

        return self.source_tree.add_node(
            content=snapshot_content,
            node_type="leaf",
            metadata={
                "source": "context_snapshot",
                "window_title": window_title,
                "text_length": len(screen_text),
                **(metadata or {})
            },
            tags=["snapshot", "context"]
        )

    def get_activity_summary(self, hours: int = 24) -> Dict[str, Any]:
        """
        获取活动摘要

        Args:
            hours: 时间范围（小时）

        Returns:
            活动摘要统计
        """
        from datetime import datetime, timedelta

        cutoff = datetime.now() - timedelta(hours=hours)
        cutoff_str = cutoff.isoformat()

        # 获取该时间段的所有活动
        activities = self.source_tree.search_by_tag("activity")

        stats = {
            "total_activities": len(activities),
            "by_type": {},
            "by_topic": {},
        }

        for node in activities:
            if node.created_at < cutoff_str:
                continue

            act_type = node.metadata.get("activity_type", "unknown")
            stats["by_type"][act_type] = stats["by_type"].get(act_type, 0) + 1

        # 获取主题分布
        topics = self.topic_tree.search_by_tag("topic")
        for node in topics:
            if node.created_at < cutoff_str:
                continue
            for tag in node.tags:
                if tag != "topic":
                    stats["by_topic"][tag] = stats["by_topic"].get(tag, 0) + 1

        # 应用使用统计
        app_stats = self.get_app_usage_stats(since_hours=hours)
        stats["top_apps"] = sorted(
            app_stats.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]

        return stats

    # ════════════════════════════════════════════════════════════
    #  生命周期管理
    # ════════════════════════════════════════════════════════════

    def shutdown(self):
        """关闭所有监控"""
        self.disable_window_monitoring()
        self.disable_ocr()
        logger.info("EnhancedTraceRecorder 已关闭")

    def get_capabilities(self) -> Dict[str, bool]:
        """获取当前可用功能"""
        capabilities = {
            "window_monitoring": self._is_monitoring_windows,
            "ocr_available": False,
            "ocr_enabled": self._ocr_sensor is not None,
        }

        try:
            from sensor.ocr_sensor import OcrSensor
            ocr = OcrSensor()
            capabilities["ocr_available"] = ocr.is_available
        except Exception:
            pass

        return capabilities
