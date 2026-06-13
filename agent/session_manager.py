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

    def create_session(self, title: str = "", mode: str = "normal") -> dict:
        """创建新会话"""
        session_id = self._generate_id()
        now = datetime.now(timezone.utc).isoformat()
        session_info = {
            "id": session_id,
            "title": title or f"会话 {datetime.now().strftime('%m-%d %H:%M')}",
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
            "mode": mode,
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
        """设置当前会话"""
        if not self.get_session(session_id):
            return False
        self._current_id = session_id
        return True

    def get_current(self) -> dict | None:
        """获取当前会话信息"""
        if not self._current_id:
            return None
        return self.get_session(self._current_id)

    def get_current_id(self) -> str | None:
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
        with self._lock:
            with open(msg_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
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
        """获取会话消息"""
        msg_file = self._sessions_dir / session_id / "messages.jsonl"
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

    def clear_messages(self, session_id: str) -> bool:
        """清空会话消息"""
        session_dir = self._sessions_dir / session_id
        if not session_dir.exists():
            return False

        msg_file = session_dir / "messages.jsonl"
        if msg_file.exists():
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
