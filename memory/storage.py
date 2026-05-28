"""持久化存储 — 管理 memory_data/ 目录下的文件读写"""

import json
from datetime import datetime, timezone
from pathlib import Path


class StorageError(Exception):
    """存储操作异常"""
    pass


class Storage:
    """消息历史与摘要的持久化管理器

    文件结构：
        messages.jsonl       — 追加写消息历史
        summary.txt          — 当前摘要文本
        summary_version.txt  — 摘要版本号
    """

    def __init__(self, data_dir: str = "./memory_data"):
        self.data_dir = Path(data_dir)
        self.messages_file = self.data_dir / "messages.jsonl"
        self.summary_file = self.data_dir / "summary.txt"
        self.version_file = self.data_dir / "summary_version.txt"

    def _ensure_dir(self):
        """确保数据目录存在"""
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def save_message(self, message: dict) -> str:
        """保存单条消息，返回消息 ID"""
        self._ensure_dir()
        msg = {
            **message,
            "timestamp": message.get("timestamp", datetime.now(timezone.utc).isoformat())
        }
        try:
            with open(self.messages_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        except OSError as e:
            raise StorageError(f"写入消息失败: {e}") from e
        return msg.get("timestamp", "")

    def load_recent_messages(self, limit: int = 50) -> list[dict]:
        """加载最近 N 条消息（从末尾倒读）"""
        if not self.messages_file.exists():
            return []
        try:
            with open(self.messages_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # 从末尾倒读 limit 条
            recent = [json.loads(line) for line in lines[-limit:]]
            return recent
        except (OSError, json.JSONDecodeError) as e:
            raise StorageError(f"读取消息失败: {e}") from e

    def save_summary(self, summary: str, version: int):
        """保存摘要"""
        self._ensure_dir()
        try:
            self.summary_file.write_text(summary, encoding="utf-8")
            self.version_file.write_text(str(version), encoding="utf-8")
        except OSError as e:
            raise StorageError(f"写入摘要失败: {e}") from e

    def load_summary(self) -> tuple[str, int] | None:
        """加载当前摘要，返回 (摘要文本, 版本号) 或 None"""
        if not self.summary_file.exists() or not self.version_file.exists():
            return None
        try:
            summary = self.summary_file.read_text(encoding="utf-8")
            version = int(self.version_file.read_text(encoding="utf-8").strip())
            return summary, version
        except (OSError, ValueError) as e:
            raise StorageError(f"读取摘要失败: {e}") from e

    def clear_messages(self):
        """清空消息历史（保留摘要文件）"""
        try:
            if self.messages_file.exists():
                self.messages_file.write_text("", encoding="utf-8")
        except OSError as e:
            raise StorageError(f"清空消息失败: {e}") from e
