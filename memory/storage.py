"""持久化存储 — 管理 memory_data/ 目录下的文件读写"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """存储操作异常"""
    pass


class Storage:
    """消息历史与摘要的持久化管理器

    文件结构：
        messages.jsonl       — 追加写消息历史
        summary.txt          — 当前摘要文本
        summary_version.txt  — 摘要版本号

    并发安全：
        使用 threading.Lock() 保护文件写入操作，确保多线程安全。
    """

    def __init__(self, data_dir: str = "./memory_data"):
        logger.info("[Storage] __init__ 开始初始化")
        self.data_dir = Path(data_dir)
        self.messages_file = self.data_dir / "messages.jsonl"
        self.summary_file = self.data_dir / "summary.txt"
        self.version_file = self.data_dir / "summary_version.txt"
        self._write_lock = threading.Lock()
        logger.info(f"[Storage] 数据目录: {self.data_dir}")
        logger.info(f"[Storage] 消息文件: {self.messages_file}")
        logger.info(f"[Storage] 摘要文件: {self.summary_file}")
        logger.info(f"[Storage] 版本文件: {self.version_file}")
        logger.info("[Storage] __init__ 初始化完成")

    def _ensure_dir(self):
        """确保数据目录存在"""
        logger.debug(f"[Storage._ensure_dir] 检查目录: {self.data_dir}")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"[Storage._ensure_dir] 目录确认完成")

    def save_message(self, message: dict) -> str:
        """保存单条消息，返回消息 ID（线程安全）"""
        logger.info(f"[Storage.save_message] 开始保存消息")
        self._ensure_dir()
        msg = {
            **message,
            "timestamp": message.get("timestamp", datetime.now(timezone.utc).isoformat())
        }
        logger.debug(f"[Storage.save_message] 消息内容: {msg}")
        try:
            with self._write_lock:
                logger.info(f"[Storage.save_message] 追加写入文件: {self.messages_file}")
                with open(self.messages_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                logger.info(f"[Storage.save_message] 消息写入成功")
        except OSError as e:
            logger.error(f"[Storage.save_message] 消息写入失败: {e}")
            raise StorageError(f"写入消息失败: {e}") from e
        result = msg.get("timestamp", "")
        logger.info(f"[Storage.save_message] 返回消息ID: {result}")
        return result

    def load_recent_messages(self, limit: int = 50) -> list[dict]:
        """加载最近 N 条消息（从末尾倒读）"""
        logger.info(f"[Storage.load_recent_messages] 开始加载最近 {limit} 条消息")
        if not self.messages_file.exists():
            logger.warning(f"[Storage.load_recent_messages] 文件不存在: {self.messages_file}")
            return []
        try:
            logger.info(f"[Storage.load_recent_messages] 读取文件: {self.messages_file}")
            with open(self.messages_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            logger.info(f"[Storage.load_recent_messages] 共读取 {len(lines)} 行")
            # 从末尾倒读 limit 条
            recent = [json.loads(line) for line in lines[-limit:]]
            logger.info(f"[Storage.load_recent_messages] 成功加载 {len(recent)} 条消息")
            return recent
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"[Storage.load_recent_messages] 加载失败: {e}")
            raise StorageError(f"读取消息失败: {e}") from e

    def save_summary(self, summary: str, version: int):
        """保存摘要（线程安全）"""
        logger.info(f"[Storage.save_summary] 开始保存摘要，版本: {version}")
        self._ensure_dir()
        try:
            with self._write_lock:
                logger.info(f"[Storage.save_summary] 写入摘要文件: {self.summary_file}")
                self.summary_file.write_text(summary, encoding="utf-8")
                logger.info(f"[Storage.save_summary] 写入版本文件: {self.version_file}")
                self.version_file.write_text(str(version), encoding="utf-8")
                logger.info(f"[Storage.save_summary] 摘要保存成功")
        except OSError as e:
            logger.error(f"[Storage.save_summary] 写入失败: {e}")
            raise StorageError(f"写入摘要失败: {e}") from e

    def load_summary(self) -> tuple[str, int] | None:
        """加载当前摘要，返回 (摘要文本, 版本号) 或 None"""
        logger.info("[Storage.load_summary] 开始加载摘要")
        if not self.summary_file.exists() or not self.version_file.exists():
            logger.warning(f"[Storage.load_summary] 摘要或版本文件不存在")
            return None
        try:
            logger.info(f"[Storage.load_summary] 读取摘要: {self.summary_file}")
            summary = self.summary_file.read_text(encoding="utf-8")
            logger.info(f"[Storage.load_summary] 读取版本: {self.version_file}")
            version = int(self.version_file.read_text(encoding="utf-8").strip())
            logger.info(f"[Storage.load_summary] 加载成功，版本: {version}")
            return summary, version
        except (OSError, ValueError) as e:
            logger.error(f"[Storage.load_summary] 加载失败: {e}")
            raise StorageError(f"读取摘要失败: {e}") from e

    def clear_summary(self):
        """清空摘要文件，重置版本号"""
        logger.info("[Storage.clear_summary] 开始清空摘要")
        try:
            with self._write_lock:
                if self.summary_file.exists():
                    self.summary_file.write_text("", encoding="utf-8")
                    logger.info("[Storage.clear_summary] 摘要文件已清空")
                if self.version_file.exists():
                    self.version_file.write_text("0", encoding="utf-8")
                    logger.info("[Storage.clear_summary] 版本号已重置为 0")
                logger.info("[Storage.clear_summary] 摘要清空成功")
        except OSError as e:
            logger.error(f"[Storage.clear_summary] 清空失败: {e}")
            raise StorageError(f"清空摘要失败: {e}") from e

    def clear_messages(self):
        """清空消息历史（保留摘要文件，线程安全）"""
        logger.info("[Storage.clear_messages] 开始清空消息")
        try:
            with self._write_lock:
                if self.messages_file.exists():
                    logger.info(f"[Storage.clear_messages] 清空文件: {self.messages_file}")
                    self.messages_file.write_text("", encoding="utf-8")
                    logger.info("[Storage.clear_messages] 消息清空成功")
                else:
                    logger.warning(f"[Storage.clear_messages] 文件不存在，无需清空")
        except OSError as e:
            logger.error(f"[Storage.clear_messages] 清空失败: {e}")
            raise StorageError(f"清空消息失败: {e}") from e
