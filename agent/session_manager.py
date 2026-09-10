"""SessionManager — 云枢多会话管理器

管理多个独立对话会话的创建、切换、删除，
每个会话对应 data/sessions/{id}/messages.jsonl。
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# [2026-08-15 边界修复] Windows 保留设备名（作为目录名会解析失败/行为异常）。
# session_id 直接用于创建 data/sessions/{id}/ 目录，需拒绝这些名字。
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}

# [2026-08-15 边界修复] 显式会话 ID 长度上限：Windows 路径约 260 字符限制，
# sessions 目录前缀本身占用空间，超长 ID 会令 mkdir 抛 OSError（500）。
# 128 字符为保守上限，满足常见 UUID/时间戳 ID（<64 字符）。
_MAX_SESSION_ID_LEN = 128


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
        session_id: str | None = None,
    ) -> dict:
        """创建新会话

        Args:
            title: 会话标题
            mode: 会话模式（normal 等）
            timezone: 用户时区（如 "Asia/Shanghai"），用于调整说话风格与临场感
            device_type: 设备类型（如 "mobile"/"desktop"），来自 User-Agent 启发式
            locale: 语言环境（如 "zh-CN"），来自 Accept-Language
            session_id: 【2026-08-15 并发修复】显式指定会话 ID（请求级会话隔离，
                外部调用方可通过该参数传入自定义 ID）。默认 None 自动生成。

        Returns:
            会话信息字典（含 timezone/device_type/locale 元数据）
        """
        # [2026-08-15 并发修复] 显式 ID 防路径穿越：会话目录会以该 ID 创建，
        # 禁止 / \ .. : 等危险字符（自动生成的 ID 天然不含，仅影响显式传入）。
        # [2026-08-15 边界修复] 补充 Windows 非法字符/保留名/超长校验
        # （create_session 是唯一入口，校验集中在此保证全调用方一致）。
        if session_id is not None:
            if any(ch in session_id for ch in ("/", "\\", "..", ":")):
                raise ValueError(f"非法会话 ID: {session_id!r}")
            if any(ch in session_id for ch in ('*', '?', '<', '>', '|', '"', "\x00")):
                raise ValueError(f"非法会话 ID: {session_id!r}")
            # Windows 保留设备名（大小写不敏感，含前导/尾随空格与点变体）
            if session_id.strip(" .").upper() in _WINDOWS_RESERVED_NAMES:
                raise ValueError(f"非法会话 ID: {session_id!r}")
            if len(session_id) > _MAX_SESSION_ID_LEN:
                raise ValueError(f"会话 ID 超长({len(session_id)}>{_MAX_SESSION_ID_LEN}): {session_id!r}")
        session_id = session_id or self._generate_id()
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

        # [2026-08-15 并发修复] 检查-创建-写索引全程持锁原子：
        # 幂等守卫放锁外会被并发击穿（N 线程同时通过检查），导致索引重复条目 +
        # write_text("") 覆盖清空已存在的 messages.jsonl（用户消息丢失）。
        # 创建为低频操作，短暂持锁可接受；与 _append_lock（消息追加）互不阻塞。
        with self._lock:
            # 二次检查：持锁后若已存在（并发同 ID 先到者已创建）→ 幂等返回
            existing = self._get_session_locked(session_id)
            if existing is not None:
                return dict(existing)

            session_dir.mkdir(parents=True, exist_ok=True)

            meta = {**session_info}
            (session_dir / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

            (session_dir / "messages.jsonl").write_text("", encoding="utf-8")

            # [2026-09-07 会话工作空间] 每个会话拥有独立任务工作空间目录
            # （data/sessions/{id}/workspace，仿 DSH 每会话工作目录语义）。
            # 会话删除时随会话目录一并 rmtree 清理（delete_session 已处理）。
            (session_dir / "workspace").mkdir(exist_ok=True)
            (session_dir / "workspace" / ".gitkeep").write_text("", encoding="utf-8")

            index = self._read_index()
            index.append(session_info)
            self._write_index(index)
            # 【审计改进】_current_id 赋值移入锁内，保证与索引写入原子
            self._current_id = session_id
        logger.info("会话已创建: %s — %s", session_id, title)
        return session_info

    def _get_session_locked(self, session_id: str) -> dict | None:
        """锁内索引查找（调用方必须已持有 self._lock）"""
        sessions = self._read_index()
        for s in sessions:
            if s["id"] == session_id:
                return dict(s)
        return None

    def get_session(self, session_id: str) -> dict | None:
        """获取会话信息"""
        with self._lock:
            return self._get_session_locked(session_id)

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

    # ════════════════════════════════════════════════════════════════
    # 会话工作空间（每个会话的任务工作目录）
    # 两种模式（仿 DSH「添加工作区」语义）：
    #   1. 默认工作空间：data/sessions/{session_id}/workspace（随会话删除清理）
    #   2. 绑定自定义目录：会话 meta.json 记录 workspace_root=<绝对路径>
    #      （绑定本地任意文件夹/项目，可在「添加工作区」中切换或恢复默认）
    # ════════════════════════════════════════════════════════════════

    def get_session_workspace_dir(self, session_id: str):
        """返回该会话应使用的工作空间根目录（可能不存在，不创建）。

        绑定自定义根（meta.workspace_root）时返回该路径，否则返回默认
        data/sessions/{session_id}/workspace。
        """
        meta = self.get_session_metadata(session_id) or {}
        custom = meta.get("workspace_root")
        if custom:
            return Path(str(custom))
        return self._sessions_dir / session_id / "workspace"

    def workspace_id_for(self, session_id: str) -> str:
        """P7.1-19 / P7.2-08：会话工作区 → workspace_id（workspace-hash 默认）。

        绑定自定义根（meta.workspace_root）时取该路径哈希，否则取默认工作空间
        data/sessions/{session_id}/workspace 路径哈希（确定性：同路径恒同 id）。
        目录不存在也返回路径哈希（调用方据此写入 S2-01 Trace tenancy.workspace_id）。
        """
        root = self.get_session_workspace_dir(session_id)
        try:
            from agent.observability.trace_v2 import derive_workspace_id
            return derive_workspace_id(str(root))
        except Exception:  # noqa: BLE001  trace_v2 不可用时本地兜底
            import hashlib
            norm = os.path.normcase(os.path.normpath(os.path.abspath(str(root))))
            return "ws_" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

    def workspace_path(self, session_id: str):
        """返回当前工作空间目录（不存在返回 None，不创建）"""
        root = self.get_session_workspace_dir(session_id)
        try:
            return root if root.is_dir() else None
        except OSError:
            return None

    def ensure_workspace_path(self, session_id: str):
        """确保当前工作空间目录存在并返回 Path。

        会话不存在时抛 SessionNotFoundError（调用方应先 get_session 校验）。
        默认工作空间补充 .gitkeep；绑定目录不写入任何文件（避免污染用户目录）。
        """
        session_dir = self._sessions_dir / session_id
        if not session_dir.exists():
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        root = self.get_session_workspace_dir(session_id)
        with self._lock:
            root.mkdir(parents=True, exist_ok=True)
            if str(root) == str(session_dir / "workspace"):
                gitkeep = root / ".gitkeep"
                if not gitkeep.exists():
                    gitkeep.write_text("", encoding="utf-8")
        return root

    def bind_workspace_root(self, session_id: str, path: str,
                            create: bool = False) -> tuple:
        """把会话工作空间绑定到本地绝对路径（仿 DSH 添加工作区）。

        Args:
            session_id: 会话 ID（不存在返回 (False, "会话不存在")）
            path: 本地绝对路径；空串 = 恢复默认工作空间
            create: 目录不存在时自动创建

        Returns:
            (ok: bool, payload: str|dict) —— ok=False 时 payload 为错误信息；
            ok=True 时 payload 为 {"root": resolved, "created": bool}
        """
        session_dir = self._sessions_dir / session_id
        if not session_dir.exists():
            return False, "会话不存在"
        path = (path or "").strip().strip('"').strip("'")
        if not path:
            # 恢复默认
            return self.clear_workspace_root(session_id)
        if not os.path.isabs(path):
            return False, "请输入本地绝对路径（例如 C:\\Users\\you\\myproject）"
        resolved = Path(os.path.normpath(path))
        created = False
        if not resolved.exists():
            if not create:
                return False, f"目录不存在：{resolved}（勾选“自动创建”可新建）"
            try:
                resolved.mkdir(parents=True, exist_ok=True)
                created = True
            except OSError as _e:
                return False, f"创建目录失败：{_e}"
        if not resolved.is_dir():
            return False, f"路径不是文件夹：{resolved}"
        self.update_session_metadata(session_id, workspace_root=str(resolved))
        return True, {"root": str(resolved), "created": created}

    def clear_workspace_root(self, session_id: str) -> tuple:
        """恢复会话默认工作空间（移除 workspace_root 绑定）"""
        session_dir = self._sessions_dir / session_id
        if not session_dir.exists():
            return False, "会话不存在"
        meta_path = session_dir / "meta.json"
        changed = False
        with self._lock:
            try:
                meta: dict = {}
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        meta = {}
                if "workspace_root" in meta:
                    del meta["workspace_root"]
                    changed = True
                meta["updated_at"] = datetime.now(timezone.utc).isoformat()
                meta_path.write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as _e:
                return False, f"写入 meta 失败：{_e}"
        return True, {
            "root": str(self._sessions_dir / session_id / "workspace"),
            "changed": changed,
        }

    def list_session_workspace(self, session_id: str,
                               max_depth: int = 3,
                               max_entries: int = 400) -> dict:
        """列出会话工作空间内的文件树（条目天然被限制在该目录内）。

        Returns:
            {
              "exists": bool,          # 工作空间目录是否存在
              "root": str,             # 工作空间根路径（绑定目录或默认目录）
              "custom": bool,          # 是否绑定了自定义工作区
              "files": [               # 深度受限（max_depth）的文件/目录条目
                  {"name","rel","type","size","mtime"}
              ],
              "truncated": bool,       # 条目数达上限被截断
            }
        """
        root = self.get_session_workspace_dir(session_id)
        meta = self.get_session_metadata(session_id) or {}
        custom = bool(meta.get("workspace_root"))
        try:
            root_exists = root.exists() and root.is_dir()
        except OSError:
            root_exists = False
        if not root_exists:
            return {
                "exists": False, "root": str(root), "custom": custom,
                "files": [], "truncated": False,
            }

        files: list[dict] = []
        truncated = False

        def _stat(p: Path) -> tuple:
            try:
                st = p.stat()
                size = st.st_size if not p.is_dir() else 0
                mtime = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
                return size, mtime
            except OSError:
                return 0, ""

        def _walk(d: Path, depth: int):
            nonlocal truncated
            if truncated:
                return
            try:
                children = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except OSError:
                return
            for p in children:
                if truncated:
                    return
                name = p.name
                if name == ".gitkeep":
                    continue
                try:
                    rel = str(p.relative_to(root)).replace("\\", "/")
                except ValueError:
                    continue
                try:
                    is_dir = p.is_dir()
                except OSError:
                    is_dir = False
                size, mtime = _stat(p)
                files.append({
                    "name": name,
                    "rel": rel,
                    "type": "dir" if is_dir else "file",
                    "size": size,
                    "mtime": mtime,
                })
                if len(files) >= max_entries:
                    truncated = True
                    return
                if is_dir and depth < max_depth:
                    _walk(p, depth + 1)

        _walk(root, 0)
        return {
            "exists": True, "root": str(root), "custom": custom,
            "files": files, "truncated": truncated,
        }


class SessionGroupStore:
    """会话分组存储（2026-09-07：会话列表按项目/用途分组）。

    持久化位置：data/sessions/groups.json，结构：
      {"groups": [{"id","name","created_at"}], "membership": {"<session_id>": "<group_id>"}}

    分组与会话本体解耦：
    - 删除会话 → 调用方调 remove_member 清理归属（路由层在 delete_session 后同步）；
    - 删除分组 → delete_group 自动清空该组下所有成员的归属；
    - 分组名称只做展示，不参与会话 ID / 目录命名，无路径风险。
    """

    def __init__(self, sessions_dir: str = "./data/sessions"):
        self._groups_path = Path(sessions_dir) / "groups.json"
        self._lock = threading.Lock()
        self._groups_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def _ensure(self):
        if not self._groups_path.exists():
            self._write({"groups": [], "membership": {}})

    def _read(self) -> dict:
        try:
            data = json.loads(self._groups_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        return {
            "groups": data.get("groups") if isinstance(data.get("groups"), list) else [],
            "membership": data.get("membership") if isinstance(data.get("membership"), dict) else {},
        }

    def _write(self, payload: dict):
        self._groups_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _gen_id() -> str:
        return f"grp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    def list_groups(self) -> dict:
        """返回 {groups: [{id,name,created_at,count}], membership: {sid: gid}}"""
        with self._lock:
            data = self._read()
        counts: dict[str, int] = {}
        for gid in data["membership"].values():
            counts[gid] = counts.get(gid, 0) + 1
        groups = [dict(g, count=counts.get(g["id"], 0)) for g in data["groups"]]
        return {"groups": groups, "membership": dict(data["membership"])}

    def create_group(self, name: str = "") -> dict:
        name = (name or "").strip() or "新分组"
        with self._lock:
            data = self._read()
            group = {
                "id": self._gen_id(),
                "name": name[:40],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            data["groups"].append(group)
            self._write(data)
        return dict(group)

    def get_group(self, group_id: str) -> dict | None:
        with self._lock:
            data = self._read()
        for g in data["groups"]:
            if g["id"] == group_id:
                return dict(g)
        return None

    def rename_group(self, group_id: str, name: str) -> bool:
        name = (name or "").strip()
        if not name:
            return False
        with self._lock:
            data = self._read()
            for g in data["groups"]:
                if g["id"] == group_id:
                    g["name"] = name[:40]
                    self._write(data)
                    return True
        return False

    def delete_group(self, group_id: str) -> bool:
        """删除分组；组内成员自动变为未分组"""
        with self._lock:
            data = self._read()
            before = len(data["groups"])
            data["groups"] = [g for g in data["groups"] if g["id"] != group_id]
            removed = len(data["groups"]) != before
            if removed:
                data["membership"] = {
                    sid: gid for sid, gid in data["membership"].items() if gid != group_id
                }
                self._write(data)
        return removed

    def assign(self, session_id: str, group_id: str | None) -> bool:
        """把会话归入分组；group_id 为 None/空串 = 移出分组（未分组）。

        分组必须存在（None 除外），会话存在性由调用方校验。
        """
        group_id = group_id or None
        with self._lock:
            data = self._read()
            if group_id is not None and not any(g["id"] == group_id for g in data["groups"]):
                return False
            if group_id is None:
                data["membership"].pop(session_id, None)
            else:
                data["membership"][session_id] = group_id
            self._write(data)
        return True

    def remove_member(self, session_id: str):
        """删除会话后清理其分组归属（幂等）"""
        with self._lock:
            data = self._read()
            if session_id in data["membership"]:
                del data["membership"][session_id]
                self._write(data)


class WorkspaceRegistry:
    """「已添加的工作区」记忆（仿 DSH 添加工作区）。

    持久化位置：data/sessions/workspaces.json，结构：
      {"workspaces": [{"path","name","added_at"}]}

    仅做记忆/快速选择：会话绑定自定义工作区时自动登记；
    目录本身的生命周期不受本注册表影响。
    """

    def __init__(self, sessions_dir: str = "./data/sessions"):
        self._path = Path(sessions_dir) / "workspaces.json"
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def _ensure(self):
        if not self._path.exists():
            self._write({"workspaces": []})

    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        ws = data.get("workspaces") if isinstance(data.get("workspaces"), list) else []
        return {"workspaces": [w for w in ws if isinstance(w, dict) and w.get("path")]}

    def _write(self, payload: dict):
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _name_of(path: str) -> str:
        p = Path(path)
        return p.name or str(p)

    def list_workspaces(self) -> list[dict]:
        with self._lock:
            data = self._read()
        return [dict(w) for w in data["workspaces"]]

    def add_workspace(self, path: str) -> dict | None:
        """登记一个工作区目录（重复登记幂等返回现有条目）"""
        path = (path or "").strip().strip('"').strip("'")
        if not path:
            return None
        resolved = os.path.normpath(path)
        with self._lock:
            data = self._read()
            for w in data["workspaces"]:
                if os.path.normpath(w["path"]) == resolved:
                    return dict(w)
            entry = {
                "path": resolved,
                "name": self._name_of(resolved),
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
            data["workspaces"].insert(0, entry)
            # 只保留最近 50 条记忆
            data["workspaces"] = data["workspaces"][:50]
            self._write(data)
        return dict(entry)

    def remove_workspace(self, path: str) -> bool:
        """从记忆列表移除一个工作区（不影响目录本身）"""
        path = (path or "").strip()
        resolved = os.path.normpath(path) if path else ""
        with self._lock:
            data = self._read()
            before = len(data["workspaces"])
            data["workspaces"] = [
                w for w in data["workspaces"] if os.path.normpath(w["path"]) != resolved
            ]
            removed = len(data["workspaces"]) != before
            if removed:
                self._write(data)
        return removed
