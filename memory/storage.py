"""持久化存储 — 管理 memory_data/ 目录下的文件读写"""

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

#: 每会话摘要文件名里的 sid 摘要长度（sha256 前 16 位十六进制）
#: 【为什么用 sha256 而不是原样拼】会话 id 来自请求，直接拼路径就是路径注入；
#:   本仓口径（docs/handoff/口径.md §4）只认三种断链：列目录+名字等值匹配、
#:   常量键→常量路径字典、sha256 摘要 —— 这里是第三种，文件名可预测且无分隔符。
_SESSION_SUMMARY_HEX = 16

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """存储操作异常"""
    pass


class Storage:
    """消息历史与摘要的持久化管理器

    文件结构：
        messages.jsonl              — 追加写消息历史
        summary.txt                 — **未分会话**（旧口径/维护线程）的摘要文本
        summary_version.txt         — 其版本号
        summary_<sha256(sid)[:16]>.txt         — 该会话自己的摘要（按会话分家时用）
        summary_<sha256(sid)[:16]>.version.txt — 其版本号

    说明：摘要方法都带 `session_id=""`，缺省 = 全局文件（逐字旧行为）；
    传了会话 id 才落到该会话的文件。这样存储层可以先支持分家，
    而"什么时候开始分家"由调用方（开关）决定，两件事不绑死。

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

    def find_message(self, msg_id: str) -> dict | None:
        """按 id 查一条消息（只读；找不到返回 None）。

        id 就是 `save_message` 写入的 `timestamp`（UTC ISO，含微秒）：
        本模块是**追加写 JSONL**，没有单独主键，时间戳实际上是唯一可用的稳定标识。
        """
        target = str(msg_id or "").strip()
        if not target or not self.messages_file.exists():
            return None
        try:
            with open(self.messages_file, "r", encoding="utf-8") as f:
                for line in f:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        record = json.loads(text)
                    except json.JSONDecodeError:
                        continue  # 坏行跳过：不能因为一行脏数据就整库查不了
                    if str(record.get("timestamp") or "") == target:
                        return record
        except OSError as e:
            logger.error(f"[Storage.find_message] 查询失败: {e}")
            raise StorageError(f"查询消息失败: {e}") from e
        return None

    def delete_message(self, msg_id: str) -> bool:
        """按 id **物理删除**一条消息（原子重写 JSONL；不可恢复）。

        【为什么是物理删而不是标记删】messages.jsonl 是**明文**落盘（本模块不加密）。
        标记删会把用户要求删掉的正文继续留在磁盘上——那等于"假删"，
        而这正是本次要修的缺陷形态（旧 `/api/memory/<index>` 恒返回 ok:true）。
        代价是不可恢复，因此 HTTP 层默认 dry-run、真删必须 confirm。

        【原子性】写 `messages.jsonl.tmp` 后 `os.replace` 覆盖；中途失败不会留下半截文件。
        未命中的行**原样按行写回**（不重新序列化），避免顺手改动别人的历史格式。
        """
        target = str(msg_id or "").strip()
        if not target:
            return False
        self._ensure_dir()
        with self._write_lock:
            if not self.messages_file.exists():
                return False
            try:
                with open(self.messages_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError as e:
                logger.error(f"[Storage.delete_message] 读取失败: {e}")
                raise StorageError(f"读取消息失败: {e}") from e
            kept: list[str] = []
            removed = 0
            for line in lines:
                if not line.strip():
                    kept.append(line)
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    kept.append(line)  # 坏行不删：删除只针对可识别的目标
                    continue
                if str(record.get("timestamp") or "") == target:
                    removed += 1
                    continue
                kept.append(line)
            if not removed:
                return False
            tmp = self.messages_file.with_name(self.messages_file.name + ".tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    f.writelines(kept)
                os.replace(tmp, self.messages_file)
            except OSError as e:
                logger.error(f"[Storage.delete_message] 重写失败: {e}")
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise StorageError(f"删除消息失败: {e}") from e
            logger.info(f"[Storage.delete_message] 已删除 {removed} 条 id={target}")
            return True

    def _summary_paths(self, session_id: str = "") -> tuple[Path, Path]:
        """返回 (摘要文件, 版本文件)。session_id 为空 = 全局文件（旧口径）。"""
        sid = str(session_id or "")
        if not sid:
            return self.summary_file, self.version_file
        key = hashlib.sha256(sid.encode("utf-8")).hexdigest()[:_SESSION_SUMMARY_HEX]
        return (self.data_dir / ("summary_%s.txt" % key),
                self.data_dir / ("summary_%s.version.txt" % key))

    def save_summary(self, summary: str, version: int, session_id: str = ""):
        """保存摘要（线程安全）。

        Args:
            session_id: 空 = 全局摘要（旧行为）；非空 = 该会话自己的摘要文件。
        """
        logger.info(f"[Storage.save_summary] 开始保存摘要，版本: {version}，会话: {session_id or '(全局)'}")
        summary_file, version_file = self._summary_paths(session_id)
        self._ensure_dir()
        try:
            with self._write_lock:
                logger.info(f"[Storage.save_summary] 写入摘要文件: {summary_file}")
                summary_file.write_text(summary, encoding="utf-8")
                logger.info(f"[Storage.save_summary] 写入版本文件: {version_file}")
                version_file.write_text(str(version), encoding="utf-8")
                logger.info(f"[Storage.save_summary] 摘要保存成功")
        except OSError as e:
            logger.error(f"[Storage.save_summary] 写入失败: {e}")
            raise StorageError(f"写入摘要失败: {e}") from e

    def load_summary(self, session_id: str = "") -> tuple[str, int] | None:
        """加载摘要，返回 (摘要文本, 版本号) 或 None。

        Args:
            session_id: 空 = 全局摘要（旧行为）；非空 = 该会话自己的摘要文件
                （没有就返回 None —— 是否回退全局由调用方决定，存储层不替它猜）。
        """
        summary_file, version_file = self._summary_paths(session_id)
        logger.info("[Storage.load_summary] 开始加载摘要（会话: %s）", session_id or "(全局)")
        if not summary_file.exists() or not version_file.exists():
            logger.warning(f"[Storage.load_summary] 摘要或版本文件不存在")
            return None
        try:
            logger.info(f"[Storage.load_summary] 读取摘要: {summary_file}")
            summary = summary_file.read_text(encoding="utf-8")
            logger.info(f"[Storage.load_summary] 读取版本: {version_file}")
            version = int(version_file.read_text(encoding="utf-8").strip())
            logger.info(f"[Storage.load_summary] 加载成功，版本: {version}")
            return summary, version
        except (OSError, ValueError) as e:
            logger.error(f"[Storage.load_summary] 加载失败: {e}")
            raise StorageError(f"读取摘要失败: {e}") from e

    def clear_summary(self, session_id: str = ""):
        """清空摘要文件并重置版本号（session_id 非空 = 只清该会话）"""
        logger.info("[Storage.clear_summary] 开始清空摘要（会话: %s）", session_id or "(全局)")
        summary_file, version_file = self._summary_paths(session_id)
        try:
            with self._write_lock:
                if summary_file.exists():
                    summary_file.write_text("", encoding="utf-8")
                    logger.info("[Storage.clear_summary] 摘要文件已清空")
                if version_file.exists():
                    version_file.write_text("0", encoding="utf-8")
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
