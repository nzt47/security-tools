
"""
云枢 TraceRecorder - 多维度数据采集器
持续记录数字生命的感知、交互、行为数据
"""

import logging
import threading
from datetime import datetime
from typing import Dict, Any, Optional, Callable
from pathlib import Path

from .memory_tree import SourceTree, TopicTree, GlobalTree

logger = logging.getLogger(__name__)


class TraceRecorder:
    """多维度数据采集器 - 云枢的感知记录中心"""

    def __init__(self, data_dir: str = "./data/lifetrace"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 三层记忆树
        self.source_tree = SourceTree(str(self.data_dir))
        self.topic_tree = TopicTree(str(self.data_dir))
        self.global_tree = GlobalTree(str(self.data_dir))

        # 回调注册
        self.callbacks: Dict[str, list] = {
            "chat": [],
            "sensor": [],
            "window": [],
            "file": [],
        }

        # 状态
        self.is_recording = False
        self._lock = threading.Lock()

        logger.info("TraceRecorder 初始化完成")

    def record_chat(
        self,
        role: str,
        content: str,
        metadata: Optional[Dict] = None,
        auto_topic: bool = True,
    ):
        """记录对话数据"""
        with self._lock:
            node = self.source_tree.record_chat(role, content, metadata)

            # 触发回调
            for callback in self.callbacks["chat"]:
                try:
                    callback(node)
                except Exception as e:
                    logger.error(f"Chat callback error: {e}")

            # 自动主题聚类（简化版）
            if auto_topic and role == "user":
                self._auto_classify_topic(content)

            logger.debug(f"记录对话: {role} - {content[:50]}...")
            return node

    def record_sensor(
        self,
        sensor_type: str,
        data: Dict[str, Any],
        metadata: Optional[Dict] = None,
    ):
        """记录传感器数据"""
        with self._lock:
            node = self.source_tree.record_sensor(sensor_type, data, metadata)

            for callback in self.callbacks["sensor"]:
                try:
                    callback(node)
                except Exception as e:
                    logger.error(f"Sensor callback error: {e}")

            logger.debug(f"记录传感器: {sensor_type}")
            return node

    def record_window(
        self,
        window_title: str,
        event_type: str,
        metadata: Optional[Dict] = None,
    ):
        """记录窗口活动"""
        with self._lock:
            node = self.source_tree.record_window(window_title, event_type, metadata)

            for callback in self.callbacks["window"]:
                try:
                    callback(node)
                except Exception as e:
                    logger.error(f"Window callback error: {e}")

            logger.debug(f"记录窗口: {window_title} - {event_type}")
            return node

    def record_file(
        self,
        file_path: str,
        event_type: str,
        metadata: Optional[Dict] = None,
    ):
        """记录文件变更"""
        with self._lock:
            node = self.source_tree.record_file(file_path, event_type, metadata)

            for callback in self.callbacks["file"]:
                try:
                    callback(node)
                except Exception as e:
                    logger.error(f"File callback error: {e}")

            logger.debug(f"记录文件: {file_path} - {event_type}")
            return node

    def add_to_topic(self, topic: str, content: str, tags: Optional[list] = None):
        """添加到主题记忆"""
        return self.topic_tree.add_to_topic(topic, content, tags)

    def get_topic_content(self, topic: str):
        """获取主题内容"""
        return self.topic_tree.get_topic_content(topic)

    def get_recent_chat(self, limit: int = 10):
        """获取最近的对话"""
        return self.source_tree.search_by_tag("chat")[:limit]

    def get_recent_sensor(self, limit: int = 10):
        """获取最近的传感器数据"""
        return self.source_tree.search_by_tag("sensor")[:limit]

    def register_callback(self, event_type: str, callback: Callable):
        """注册事件回调"""
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)
            logger.info(f"注册回调: {event_type}")

    def unregister_callback(self, event_type: str, callback: Callable):
        """注销事件回调"""
        if event_type in self.callbacks and callback in self.callbacks[event_type]:
            self.callbacks[event_type].remove(callback)

    def _auto_classify_topic(self, content: str):
        """自动主题分类（简化版）"""
        # 简单的关键词匹配
        keywords = {
            "工作": ["工作", "任务", "项目", "会议", "邮件"],
            "学习": ["学习", "教程", "文档", "编程", "代码"],
            "生活": ["生活", "休息", "娱乐", "游戏", "音乐"],
            "健康": ["健康", "运动", "饮食", "睡眠"],
        }

        for topic, words in keywords.items():
            if any(word in content for word in words):
                self.topic_tree.add_to_topic(topic, content, ["auto-classified"])
                break

    def get_statistics(self) -> Dict:
        """获取记忆统计"""
        return {
            "source_nodes": len(self.source_tree.nodes),
            "topic_nodes": len(self.topic_tree.nodes),
            "topics": list(self.topic_tree.topics.keys()),
            "summary": self.global_tree.load_summary(),
        }

