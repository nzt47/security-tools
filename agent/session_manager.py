"""SessionManager — 云枢多会话管理器

管理多个独立对话会话的创建、切换、删除，
每个会话对应 data/sessions/{id}/messages.jsonl。
"""

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class SessionNotFoundError(Exception):
    """会话不存在"""
    pass


class SessionManager:
    """多会话管理器"""

    def __init__(self, sessions_dir: str = "./data/sessions"):
        self._sessions_dir = Path(sessions_dir)
        self._index_path = self._sessions_dir / "sessions.json"
        self._current_id: str | None = None
        self._lock = threading.Lock()
        # [2026-08-13 并发审计 B] 消息文件追加/清空专用锁：Windows 上 open("a")
        # 的 O_APPEND 是 seek+write 组合（非原子），多线程并发追加会交错覆盖丢行；
        # 此锁与主锁分离，慢磁盘只阻塞追加方、不阻塞会话管理/读操作。
        self._append_lock = threading.Lock()
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_index()

    def _ensure_index(self):
        """确保 sessions.json 存在"""
        if not self._index_path.exists():
            self._index_path.write_text("[]", encoding="utf-8")

    def _read_index(self) -> list[dict]:
        """读取会话索引"""
        try:
            data = self._index_path.read_text(encoding="utf-8")
            return json.loads(data) if data else []
        except (json.JSONDecodeError, OSError):
            return []

    def _write_index(self, index: list[dict]):
        """写入会话索引"""
        self._index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def _generate_id(self) -> str:
        """生成唯一会话 ID"""
        return f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    def list_sessions(self, limit: int = 50) -> list[dict]:
        """获取会话列表，按 updated_at 降序"""
        with self._lock:
            sessions = self._read_index()
            sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
            return sessions[:limit]

    def create_session(
        self,
        title: str = "",
        mode: str = "normal",
        timezone: str | None = None,
        device_type: str | None = None,
        locale: str | None = None,
    ) -> dict:
        """创建新会话

        Args:
            title: 会话标题
            mode: 会话模式（normal 等）
            timezone: 用户时区（如 "Asia/Shanghai"），用于调整说话风格与临场感
            device_type: 设备类型（如 "mobile"/"desktop"），来自 User-Agent 启发式
            locale: 语言环境（如 "zh-CN"），来自 Accept-Language

        Returns:
            会话信息字典（含 timezone/device_type/locale 元数据）
        """
        session_id = self._generate_id()
        # 注意：timezone 参数遮蔽了模块级 datetime.timezone，本地别名导入规避
        from datetime import timezone as _dt_timezone
        now = datetime.now(_dt_timezone.utc).isoformat()
        session_info = {
            "id": session_id,
            "title": title or f"会话 {datetime.now().strftime('%m-%d %H:%M')}",
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
            "mode": mode,
            "timezone": timezone,
            "device_type": device_type,
            "locale": locale,
        }

        session_dir = self._sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        meta = {**session_info}
        (session_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        (session_dir / "messages.jsonl").write_text("", encoding="utf-8")

        with self._lock:
            index = self._read_index()
            index.append(session_info)
            self._write_index(index)
            # 【审计改进】_current_id 赋值移入锁内，保证与索引写入原子
            self._current_id = session_id
        logger.info("会话已创建: %s — %s", session_id, title)
        return session_info

    def get_session(self, session_id: str) -> dict | None:
        """获取会话信息"""
        with self._lock:
            sessions = self._read_index()
            for s in sessions:
                if s["id"] == session_id:
                    return dict(s)
        return None

    def get_session_metadata(self, session_id: str) -> dict | None:
        """获取会话元数据（时区/设备/语言等），用于决定系统说话风格与临场感

        优先读 meta.json（真实文件），回退到索引中的信息（可能缺少新字段）。

        Args:
            session_id: 会话标识

        Returns:
            元数据字典 {timezone, device_type, locale, mode, title, ...} 或 None
        """
        session_dir = self._sessions_dir / session_id
        meta_path = session_dir / "meta.json"
        meta: dict = {}
        # 【审计改进】文件读取加锁（与 update_session_metadata 写锁互斥）；
        # 锁内不调用任何其他锁方法（避免非重入锁死锁）
        with self._lock:
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    meta = {}
        # 索引兜底合并（get_session 内部取锁，须在锁外调用）
        index_info = self.get_session(session_id)
        if index_info:
            for k, v in index_info.items():
                meta.setdefault(k, v)
        return meta or None

    def update_session_metadata(self, session_id: str, **fields) -> bool:
        """更新会话元数据字段（时区/设备/语言等）

        Args:
            session_id: 会话标识
            **fields: 要更新的字段（如 timezone="Asia/Shanghai"）

        Returns:
            True 表示更新成功（会话不存在返回 False）
        """
        session_dir = self._sessions_dir / session_id
        meta_path = session_dir / "meta.json"
        if not session_dir.exists():
            return False

        with self._lock:
            try:
                meta: dict = {}
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        meta = {}
                meta.update(fields)
                meta["updated_at"] = datetime.now(timezone.utc).isoformat()
                meta_path.write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return True
            except OSError:
                return False

    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        with self._lock:
            index = self._read_index()
            new_index = [s for s in index if s["id"] != session_id]
            if len(new_index) == len(index):
                return False
            self._write_index(new_index)

        import shutil
        session_dir = self._sessions_dir / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir)

        # 【审计改进】_current_id 读写加锁（与 set_current/get_current_id 一致）
        with self._lock:
            if self._current_id == session_id:
                self._current_id = None

        logger.info("会话已删除: %s", session_id)
        return True

    def rename_session(self, session_id: str, title: str) -> bool:
        """重命名会话"""
        with self._lock:
            index = self._read_index()
            for s in index:
                if s["id"] == session_id:
                    s["title"] = title
                    self._write_index(index)
                    meta_path = self._sessions_dir / session_id / "meta.json"
                    if meta_path.exists():
                        try:
                            meta = json.loads(meta_path.read_text(encoding="utf-8"))
                            meta["title"] = title
                            meta_path.write_text(
                                json.dumps(meta, ensure_ascii=False, indent=2),
                                encoding="utf-8"
                            )
                        except Exception as e:
                            logger.warning("更新 meta.json 失败: %s", e)
                    logger.info("会话已重命名: %s → %s", session_id, title)
                    return True
        return False

    def set_current(self, session_id: str) -> bool:
        """设置当前会话（UI 选中态）

        【审计改进】加锁保护读写原子性。注意：Web 前端始终显式传
        session_id（localStorage + query 参数），本方法仅维护 UI
        "当前选中会话"展示态，不参与对话链路的会话归属决策。
        """
        # 【审计改进】会话存在性校验放锁外：get_session 内部会再取
        # self._lock（非重入锁），锁内嵌套调用将死锁（Timeout）
        if not self.get_session(session_id):
            return False
        with self._lock:
            self._current_id = session_id
        return True
    def get_current(self) -> dict | None:
        """获取当前会话信息"""
        with self._lock:
            _cid = self._current_id
        if not _cid:
            return None
        return self.get_session(_cid)

    def get_current_id(self) -> str | None:
        with self._lock:
            return self._current_id

    def add_message(self, session_id: str, role: str, content: str,
                    tool_calls: list | None = None,
                    tool_steps: list | None = None,
                    reasoning: str | None = None) -> dict:
        """添加消息到会话"""
        session_dir = self._sessions_dir / session_id
        if not session_dir.exists():
            raise SessionNotFoundError(f"会话不存在: {session_id}")

        msg = {
            "role": role,
            "content": content or "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if tool_steps:
            msg["tool_steps"] = tool_steps
        if reasoning:
            msg["reasoning"] = reasoning

        msg_file = session_dir / "messages.jsonl"
        # [2026-08-13 并发审计 B] 消息追加移出主锁 self._lock：慢磁盘不阻塞会话
        # 管理/读操作。但 Windows 上 open("a") 的 O_APPEND 是 seek+write 组合
        # （非原子），多线程并发追加会交错覆盖丢行——故用独立 _append_lock
        # 保护"open→write→flush"单次写（与 clear_messages 的清空互斥）。
        # index 读-改-写保持 self._lock 内：count 递增必须与写回原子（一致性
        # 不变式），index 为小文件，锁内 I/O 时间短。
        with self._append_lock:
            with open(msg_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        with self._lock:
            index = self._read_index()
            for s in index:
                if s["id"] == session_id:
                    s["message_count"] = s.get("message_count", 0) + 1
                    s["updated_at"] = msg["timestamp"]
                    self._write_index(index)
                    break

        return msg

    def get_messages(self, session_id: str, limit: int = 50,
                     offset: int = 0) -> list[dict]:
        """获取会话消息

        【审计改进】读取加锁，与 add_message 的写锁互斥，避免读-写竞态
        （读到写入中的不完整行）。锁内仅做文件 IO，不调用其他锁方法。
        """
        msg_file = self._sessions_dir / session_id / "messages.jsonl"
        with self._lock:
            if not msg_file.exists():
                return []

            try:
                with open(msg_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                return []

        messages = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if offset > 0:
            messages = messages[offset:]
        if limit > 0:
            messages = messages[-limit:]

        return messages

    def get_message_count(self, session_id: str) -> int:
        """获取会话消息总数

        【审计改进】读取加锁（与 add_message/clear_messages 写锁互斥）。
        """
        msg_file = self._sessions_dir / session_id / "messages.jsonl"
        with self._lock:
            if not msg_file.exists():
                return 0
            try:
                with open(msg_file, "r", encoding="utf-8") as f:
                    return sum(1 for line in f if line.strip())
            except OSError:
                return 0

    def clear_messages(self, session_id: str) -> bool:
        """清空会话消息"""
        session_dir = self._sessions_dir / session_id
        if not session_dir.exists():
            return False

        msg_file = session_dir / "messages.jsonl"
        # [2026-08-13 并发审计 B] 清空消息文件与追加互斥（_append_lock），
        # 避免清空与并发 add_message 交错产生残留行/丢消息。
        if msg_file.exists():
            with self._append_lock:
                msg_file.write_text("", encoding="utf-8")

        with self._lock:
            index = self._read_index()
            for s in index:
                if s["id"] == session_id:
                    s["message_count"] = 0
                    s["updated_at"] = datetime.now(timezone.utc).isoformat()
                    self._write_index(index)
                    return True
        return False
