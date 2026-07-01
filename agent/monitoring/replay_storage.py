#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用户行为回放存储（Session Replay Storage）

设计目标：
- 双存储：gzip 文件 + SQLite 元数据（便于海量检索 + 节省磁盘）
- 三向关联：trace_id ↔ user_session_id ↔ error_id，支撑链路定位
- 线程安全：_lock + check_same_thread=False
- 边界显性化：所有失败分支抛 ReplayStorageError 携带业务错误码
- 结构化日志：所有关键节点输出 JSON 格式（trace_id/module_name/action/duration_ms）
- 健康检查：通过 storage_health_check() 暴露 DB 连接状态

错误码：
- REPLAY_ERR_INVALID_INPUT    : 输入参数校验失败
- REPLAY_ERR_STORAGE_FAILED    : 文件系统写入失败
- REPLAY_ERR_DB_FAILED         : SQLite 写入/查询失败
- REPLAY_ERR_NOT_FOUND         : replay_id 不存在
- REPLAY_ERR_DECODE_FAILED     : gzip/base64 解码失败

存储结构：
    {storage_root}/
    ├── 20260627/                       # 按日期分目录
    │   ├── <replay_id>.json.gz        # gzip 压缩的 rrweb 事件
    │   └── ...
    └── replay_meta.db                  # SQLite 元数据
"""

import base64
import datetime
import gzip
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 错误码常量
# ═══════════════════════════════════════════════════════════════

REPLAY_ERR_INVALID_INPUT = "REPLAY_ERR_001"     # 输入参数校验失败
REPLAY_ERR_STORAGE_FAILED = "REPLAY_ERR_002"      # 文件系统写入失败
REPLAY_ERR_DB_FAILED = "REPLAY_ERR_003"            # SQLite 写入/查询失败
REPLAY_ERR_NOT_FOUND = "REPLAY_ERR_004"             # replay_id 不存在
REPLAY_ERR_DECODE_FAILED = "REPLAY_ERR_005"         # gzip/base64 解码失败


class ReplayStorageError(Exception):
    """回放存储异常，携带业务错误码"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


# ═══════════════════════════════════════════════════════════════
# 结构化日志辅助
# ═══════════════════════════════════════════════════════════════

def _emit_log(action: str, log_level: str, trace_id: Optional[str], **fields) -> None:
    """输出 JSON 格式结构化日志（遵循可观测性约束）

    统一字段：trace_id / module_name / action / duration_ms

    注意：log_level 是日志级别参数（info/warning/error），调用方如需记录业务
    字段名 trace_id/level 等，应使用其他键名避免冲突（如 query_trace_id）。
    """
    payload: Dict[str, Any] = {
        "trace_id": trace_id or uuid.uuid4().hex,
        "module_name": "replay_storage",
        "action": action,
    }
    payload.update(fields)
    getattr(logger, log_level, logger.info)(json.dumps(payload, ensure_ascii=False, default=str))


def _ms(t0: float) -> float:
    return round((time.time() - t0) * 1000, 2)


# ═══════════════════════════════════════════════════════════════
# Schema
# ═══════════════════════════════════════════════════════════════

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS replay (
    replay_id        TEXT PRIMARY KEY,
    trace_id         TEXT,
    user_session_id  TEXT,
    error_id         TEXT,
    timestamp        TEXT NOT NULL,
    duration_sec     INTEGER DEFAULT 0,
    event_count      INTEGER DEFAULT 0,
    file_path        TEXT NOT NULL,
    size_bytes       INTEGER DEFAULT 0,
    compressed       INTEGER DEFAULT 0,
    encoding         TEXT DEFAULT 'json',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_replay_trace_id        ON replay(trace_id);
CREATE INDEX IF NOT EXISTS idx_replay_user_session_id ON replay(user_session_id);
CREATE INDEX IF NOT EXISTS idx_replay_error_id        ON replay(error_id);
CREATE INDEX IF NOT EXISTS idx_replay_timestamp       ON replay(timestamp);
"""

# replay_id 必须形如：UUID v4、或 'replay-' 前缀 + 字母数字
_REPLAY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_]{2,127}$")


# ═══════════════════════════════════════════════════════════════
# ReplayStorage 主类
# ═══════════════════════════════════════════════════════════════

class ReplayStorage:
    """用户行为回放存储（线程安全双存储）

    使用方式：
        storage = ReplayStorage("/data/replays")
        storage.store(replay_id="uuid", data=raw_json, trace_id="abc")
        meta = storage.get_by_id("uuid")
        events = storage.get_data_by_id("uuid")
    """

    def __init__(self, storage_root: str) -> None:
        """初始化存储

        Args:
            storage_root: 存储根目录（自动创建）
        Raises:
            ReplayStorageError: 创建目录或 DB 失败
        """
        action = "init"
        t0 = time.time()
        self._lock = threading.RLock()
        try:
            # 规范化路径
            self.storage_root = os.path.abspath(storage_root)
            os.makedirs(self.storage_root, exist_ok=True)
            self.db_path = os.path.join(self.storage_root, "replay_meta.db")

            # 初始化 SQLite（check_same_thread=False 配合 RLock 保证跨线程安全）
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                isolation_level=None,  # 自动提交
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(SCHEMA_SQL)

            _emit_log(action, "info", None, result="initialized",
                      duration_ms=_ms(t0),
                      storage_root=self.storage_root)
        except Exception as e:
            err = ReplayStorageError(REPLAY_ERR_STORAGE_FAILED, f"初始化失败: {e}")
            _emit_log(action, "error", None, result="init_failed",
                      duration_ms=_ms(t0), error=str(e),
                      exception_type=type(e).__name__, code=err.code)
            raise err from e

    # ── 文件系统辅助 ─────────────────────────────────────────

    def _date_dir(self, ts_iso: Optional[str] = None) -> str:
        """获取/创建按日期命名的子目录"""
        if ts_iso:
            try:
                dt = datetime.datetime.fromisoformat(ts_iso)
            except (ValueError, TypeError):
                dt = datetime.datetime.now()
        else:
            dt = datetime.datetime.now()
        date_str = dt.strftime("%Y%m%d")
        path = os.path.join(self.storage_root, date_str)
        os.makedirs(path, exist_ok=True)
        return path

    def _file_path_for(self, replay_id: str, ts_iso: Optional[str], compressed: bool) -> str:
        """生成 replay 文件绝对路径"""
        date_dir = self._date_dir(ts_iso)
        suffix = ".json.gz" if compressed else ".json"
        # 防止路径穿越：仅保留字母数字-_作为文件名
        safe_id = re.sub(r"[^A-Za-z0-9\-_]", "_", replay_id)
        return os.path.join(date_dir, f"{safe_id}{suffix}")

    # ── store ───────────────────────────────────────────────

    def store(
        self,
        replay_id: str,
        data: str,
        trace_id: Optional[str] = None,
        user_session_id: Optional[str] = None,
        error_id: Optional[str] = None,
        timestamp: Optional[str] = None,
        duration_sec: int = 0,
        event_count: int = 0,
        compressed: bool = False,
        encoding: str = "json",
    ) -> Dict[str, Any]:
        """存储一条回放

        Args:
            replay_id: 前端生成的回放 ID（唯一）
            data: 回放数据（compressed=True 时为 gzip-base64 编码）
            trace_id: OpenTelemetry 链路 ID
            user_session_id: 用户会话 ID
            error_id: Sentry 事件 ID
            timestamp: ISO 8601 时间戳（None 用当前时间）
            duration_sec: 时长（秒）
            event_count: 事件数
            compressed: 是否已 gzip-base64 编码
            encoding: 数据编码（json / gzip-base64）
        Returns:
            {"replay_id", "file_path", "size_bytes", "stored": True}
        Raises:
            ReplayStorageError: 输入无效 / 写文件失败 / 写 DB 失败
        """
        action = "store"
        t0 = time.time()
        tid = trace_id or uuid.uuid4().hex

        # 1. 参数校验
        if not replay_id or not _REPLAY_ID_PATTERN.match(replay_id):
            err = ReplayStorageError(
                REPLAY_ERR_INVALID_INPUT,
                f"replay_id 无效: {replay_id!r}",
            )
            _emit_log(action, "error", tid, result="invalid_replay_id",
                      duration_ms=_ms(t0), error=err.message, code=err.code)
            raise err
        if not data or not isinstance(data, (str, bytes)):
            err = ReplayStorageError(
                REPLAY_ERR_INVALID_INPUT,
                f"data 为空或类型错误: {type(data).__name__}",
            )
            _emit_log(action, "error", tid, result="invalid_data",
                      duration_ms=_ms(t0), error=err.message, code=err.code)
            raise err

        ts_iso = timestamp or datetime.datetime.now().isoformat()
        # 时间戳合法性校验
        try:
            datetime.datetime.fromisoformat(ts_iso)
        except (ValueError, TypeError) as e:
            err = ReplayStorageError(
                REPLAY_ERR_INVALID_INPUT,
                f"timestamp 格式无效: {ts_iso!r}",
            )
            _emit_log(action, "error", tid, result="invalid_timestamp",
                      duration_ms=_ms(t0), error=err.message, code=err.code,
                      raw_timestamp=str(ts_iso))
            raise err from e

        file_path = self._file_path_for(replay_id, ts_iso, compressed)

        with self._lock:
            # 2. 写文件
            try:
                if compressed and encoding == "gzip-base64":
                    # data 是 base64 编码的 gzip 数据，先解码
                    try:
                        gz_bytes = base64.b64decode(data)
                    except Exception as e:
                        err = ReplayStorageError(
                            REPLAY_ERR_DECODE_FAILED,
                            f"base64 解码失败: {e}",
                        )
                        _emit_log(action, "error", tid, result="b64_decode_failed",
                                  duration_ms=_ms(t0), error=err.message, code=err.code)
                        raise err from e
                    with open(file_path, "wb") as f:
                        f.write(gz_bytes)
                    size_bytes = len(gz_bytes)
                elif compressed:
                    # data 是 gzip 字节（可能是 str 编码后的）
                    try:
                        if isinstance(data, str):
                            data_bytes = data.encode("utf-8")
                        else:
                            data_bytes = data
                        with gzip.open(file_path, "wb") as f:
                            f.write(data_bytes)
                        size_bytes = os.path.getsize(file_path)
                    except Exception as e:
                        err = ReplayStorageError(
                            REPLAY_ERR_DECODE_FAILED,
                            f"gzip 编码失败: {e}",
                        )
                        _emit_log(action, "error", tid, result="gzip_encode_failed",
                                  duration_ms=_ms(t0), error=err.message, code=err.code)
                        raise err from e
                else:
                    # 未压缩的 JSON 字符串
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(data if isinstance(data, str) else data.decode("utf-8"))
                    size_bytes = os.path.getsize(file_path)

                _emit_log(action, "debug", tid, result="file_written",
                          duration_ms=_ms(t0),
                          file_path=file_path,
                          size_bytes=size_bytes)
            except ReplayStorageError:
                raise
            except OSError as e:
                err = ReplayStorageError(
                    REPLAY_ERR_STORAGE_FAILED,
                    f"文件写入失败: {e} (path={file_path})",
                )
                _emit_log(action, "error", tid, result="file_write_failed",
                          duration_ms=_ms(t0), error=err.message, code=err.code,
                          file_path=file_path, exception_type=type(e).__name__)
                raise err from e

            # 3. 写 SQLite（数据库失败需回滚已写文件）
            try:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO replay
                    (replay_id, trace_id, user_session_id, error_id, timestamp,
                     duration_sec, event_count, file_path, size_bytes,
                     compressed, encoding, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        replay_id, trace_id, user_session_id, error_id, ts_iso,
                        int(duration_sec), int(event_count), file_path, int(size_bytes),
                        1 if compressed else 0, encoding,
                        datetime.datetime.now().isoformat(),
                    ),
                )
                _emit_log(action, "debug", tid, result="db_written",
                          duration_ms=_ms(t0), replay_id=replay_id)
            except sqlite3.Error as e:
                # 数据库失败 → 清理已写文件，避免磁盘泄漏
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    _emit_log(action, "warning", tid, result="file_rolled_back",
                              file_path=file_path)
                except OSError as cleanup_err:
                    _emit_log(action, "error", tid, result="cleanup_failed",
                              error=str(cleanup_err), file_path=file_path)

                err = ReplayStorageError(
                    REPLAY_ERR_DB_FAILED,
                    f"SQLite 写入失败: {e}",
                )
                _emit_log(action, "error", tid, result="db_write_failed",
                          duration_ms=_ms(t0), error=err.message, code=err.code,
                          exception_type=type(e).__name__)
                raise err from e

        _emit_log(action, "info", tid, result="stored",
                  duration_ms=_ms(t0), replay_id=replay_id,
                  size_bytes=size_bytes, compressed=compressed)
        self._record_metrics(True, _ms(t0))
        return {
            "replay_id": replay_id,
            "file_path": file_path,
            "size_bytes": size_bytes,
            "stored": True,
        }

    # ── 查询：单条 ────────────────────────────────────────────

    def get_by_id(self, replay_id: str) -> Optional[Dict[str, Any]]:
        """按 replay_id 查询元数据"""
        action = "get_by_id"
        t0 = time.time()
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT * FROM replay WHERE replay_id = ?",
                    (replay_id,),
                ).fetchone()
            if not row:
                _emit_log(action, "debug", None, result="not_found",
                          duration_ms=_ms(t0), replay_id=replay_id)
                return None
            _emit_log(action, "debug", None, result="found",
                      duration_ms=_ms(t0), replay_id=replay_id)
            return dict(row)
        except sqlite3.Error as e:
            err = ReplayStorageError(REPLAY_ERR_DB_FAILED, f"查询失败: {e}")
            _emit_log(action, "error", None, result="db_query_failed",
                      duration_ms=_ms(t0), error=str(e), code=err.code)
            raise err from e

    def get_data_by_id(self, replay_id: str) -> Optional[str]:
        """按 replay_id 读取回放数据（解码后的 JSON 字符串）

        Returns:
            str: rrweb 事件 JSON 字符串；不存在时返回 None
        Raises:
            ReplayStorageError: DB 失败 / 文件读取失败 / 解码失败
        """
        action = "get_data_by_id"
        t0 = time.time()
        try:
            meta = self.get_by_id(replay_id)
            if not meta:
                return None

            file_path = meta["file_path"]
            if not os.path.exists(file_path):
                err = ReplayStorageError(
                    REPLAY_ERR_STORAGE_FAILED,
                    f"回放文件不存在: {file_path}（数据库记录存在，文件丢失）",
                )
                _emit_log(action, "error", None, result="file_missing",
                          duration_ms=_ms(t0), error=err.message, code=err.code,
                          file_path=file_path, replay_id=replay_id)
                raise err

            compressed = bool(meta.get("compressed", 0))
            encoding = meta.get("encoding", "json")

            if compressed and encoding == "gzip-base64":
                with open(file_path, "rb") as f:
                    gz_bytes = f.read()
                try:
                    raw_bytes = gzip.decompress(gz_bytes)
                except Exception as e:
                    err = ReplayStorageError(
                        REPLAY_ERR_DECODE_FAILED,
                        f"gzip 解压失败: {e}",
                    )
                    _emit_log(action, "error", None, result="gzip_decode_failed",
                              duration_ms=_ms(t0), error=err.message, code=err.code)
                    raise err from e
                return raw_bytes.decode("utf-8")
            elif compressed:
                with gzip.open(file_path, "rb") as f:
                    return f.read().decode("utf-8")
            else:
                with open(file_path, "r", encoding="utf-8") as f:
                    return f.read()
        except ReplayStorageError:
            raise
        except OSError as e:
            err = ReplayStorageError(
                REPLAY_ERR_STORAGE_FAILED,
                f"文件读取失败: {e}",
            )
            _emit_log(action, "error", None, result="file_read_failed",
                      duration_ms=_ms(t0), error=str(e), code=err.code)
            raise err from e

    # ── 查询：列表 ────────────────────────────────────────────

    def list_by_trace_id(self, trace_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """按 trace_id 查询回放列表"""
        return self._list_by_field("trace_id", trace_id, limit, "list_by_trace_id")

    def list_by_user_session(self, user_session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """按 user_session_id 查询回放列表"""
        return self._list_by_field("user_session_id", user_session_id, limit, "list_by_user_session")

    def list_by_time_range(
        self,
        start_time: str,
        end_time: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """按时间范围 [start_time, end_time] 查询回放列表"""
        action = "list_by_time_range"
        t0 = time.time()
        try:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT * FROM replay
                    WHERE timestamp >= ? AND timestamp <= ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (start_time, end_time, int(limit)),
                ).fetchall()
            _emit_log(action, "debug", None, result="ok",
                      duration_ms=_ms(t0),
                      count=len(rows),
                      start=start_time, end=end_time)
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            err = ReplayStorageError(REPLAY_ERR_DB_FAILED, f"查询失败: {e}")
            _emit_log(action, "error", None, result="db_query_failed",
                      duration_ms=_ms(t0), error=str(e), code=err.code)
            raise err from e

    def list_recent_24h(self, limit: int = 100) -> List[Dict[str, Any]]:
        """查询最近 24 小时回放列表"""
        action = "list_recent_24h"
        t0 = time.time()
        cutoff = (datetime.datetime.now() - datetime.timedelta(hours=24)).isoformat()
        try:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT * FROM replay
                    WHERE timestamp >= ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (cutoff, int(limit)),
                ).fetchall()
            _emit_log(action, "debug", None, result="ok",
                      duration_ms=_ms(t0), count=len(rows))
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            err = ReplayStorageError(REPLAY_ERR_DB_FAILED, f"查询失败: {e}")
            _emit_log(action, "error", None, result="db_query_failed",
                      duration_ms=_ms(t0), error=str(e), code=err.code)
            raise err from e

    def _list_by_field(self, field: str, value: str, limit: int, action: str) -> List[Dict[str, Any]]:
        """内部辅助：按单字段查询列表"""
        t0 = time.time()
        try:
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT * FROM replay WHERE {field} = ? ORDER BY timestamp DESC LIMIT ?",
                    (value, int(limit)),
                ).fetchall()
            # 注意：避免直接 **{field: value}，因为 field 可能等于 "trace_id"
            # 与 _emit_log 的位置参数 trace_id 冲突，改用 query_field/query_value
            _emit_log(action, "debug", None, result="ok",
                      duration_ms=_ms(t0), count=len(rows),
                      query_field=field, query_value=value)
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            err = ReplayStorageError(REPLAY_ERR_DB_FAILED, f"查询失败: {e}")
            _emit_log(action, "error", None, result="db_query_failed",
                      duration_ms=_ms(t0), error=str(e), code=err.code)
            raise err from e

    # ── 关联统计 ───────────────────────────────────────────

    def get_correlation_stats(self, hours: int = 24) -> Dict[str, Any]:
        """统计 trace_id ↔ user_session_id ↔ error_id 三向关联情况

        Returns:
            {
                "total_replays": int,
                "with_trace_id": int,
                "with_user_session_id": int,
                "with_error_id": int,
                "fully_correlated": int,  # 三向都齐全
                "by_error_id": [{"error_id": str, "count": int}, ...],
                "window_hours": int,
            }
        """
        action = "get_correlation_stats"
        t0 = time.time()
        cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
        try:
            with self._lock:
                total = self._conn.execute(
                    "SELECT COUNT(*) as c FROM replay WHERE timestamp >= ?",
                    (cutoff,),
                ).fetchone()["c"]
                with_trace = self._conn.execute(
                    "SELECT COUNT(*) as c FROM replay WHERE timestamp >= ? AND trace_id IS NOT NULL AND trace_id != ''",
                    (cutoff,),
                ).fetchone()["c"]
                with_session = self._conn.execute(
                    "SELECT COUNT(*) as c FROM replay WHERE timestamp >= ? AND user_session_id IS NOT NULL AND user_session_id != ''",
                    (cutoff,),
                ).fetchone()["c"]
                with_error = self._conn.execute(
                    "SELECT COUNT(*) as c FROM replay WHERE timestamp >= ? AND error_id IS NOT NULL AND error_id != ''",
                    (cutoff,),
                ).fetchone()["c"]
                fully = self._conn.execute(
                    """
                    SELECT COUNT(*) as c FROM replay
                    WHERE timestamp >= ?
                      AND trace_id IS NOT NULL AND trace_id != ''
                      AND user_session_id IS NOT NULL AND user_session_id != ''
                      AND error_id IS NOT NULL AND error_id != ''
                    """,
                    (cutoff,),
                ).fetchone()["c"]
                by_error = self._conn.execute(
                    """
                    SELECT error_id, COUNT(*) as c FROM replay
                    WHERE timestamp >= ? AND error_id IS NOT NULL AND error_id != ''
                    GROUP BY error_id ORDER BY c DESC LIMIT 50
                    """,
                    (cutoff,),
                ).fetchall()

            stats = {
                "total_replays": int(total),
                "with_trace_id": int(with_trace),
                "with_user_session_id": int(with_session),
                "with_error_id": int(with_error),
                "fully_correlated": int(fully),
                "by_error_id": [{"error_id": r["error_id"], "count": r["c"]} for r in by_error],
                "window_hours": int(hours),
            }
            _emit_log(action, "info", None, result="ok",
                      duration_ms=_ms(t0),
                      total=stats["total_replays"],
                      fully_correlated=stats["fully_correlated"])
            return stats
        except sqlite3.Error as e:
            err = ReplayStorageError(REPLAY_ERR_DB_FAILED, f"统计查询失败: {e}")
            _emit_log(action, "error", None, result="db_query_failed",
                      duration_ms=_ms(t0), error=str(e), code=err.code)
            raise err from e

    # ── 清理 ───────────────────────────────────────────────

    def cleanup_old_records(self, days: int = 30) -> int:
        """清理 days 天前的回放记录（文件 + DB）

        Args:
            days: 保留天数（0 ≤ days ≤ 36500），超过此范围抛出 ValueError

        Returns:
            int: 删除的记录数

        Raises:
            ValueError: days 为负数或超过 36500 时抛出
        """
        # 边界显性化：校验 days 参数，防止 OverflowError
        from agent.monitoring.observability_config import get_max_analyze_days
        max_days = get_max_analyze_days()
        if not isinstance(days, int) or days < 0:
            raise ValueError(f"days 必须为非负整数，得到: {days!r}")
        if days > max_days:
            raise ValueError(f"days 超过上限 {max_days}，得到: {days}")

        action = "cleanup_old_records"
        t0 = time.time()
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
        deleted_files = 0
        deleted_records = 0

        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT replay_id, file_path FROM replay WHERE timestamp < ?",
                    (cutoff,),
                ).fetchall()
            except sqlite3.Error as e:
                err = ReplayStorageError(REPLAY_ERR_DB_FAILED, f"查询过期记录失败: {e}")
                _emit_log(action, "error", None, result="db_query_failed",
                          duration_ms=_ms(t0), error=str(e), code=err.code)
                raise err from e

            for row in rows:
                # 删文件（容忍失败，继续删 DB）
                try:
                    if os.path.exists(row["file_path"]):
                        os.remove(row["file_path"])
                        deleted_files += 1
                except OSError as e:
                    _emit_log(action, "warning", None, result="file_delete_failed",
                              file_path=row["file_path"], error=str(e))

            # 删 DB 记录
            try:
                cur = self._conn.execute(
                    "DELETE FROM replay WHERE timestamp < ?",
                    (cutoff,),
                )
                deleted_records = cur.rowcount or 0
            except sqlite3.Error as e:
                err = ReplayStorageError(REPLAY_ERR_DB_FAILED, f"删除 DB 记录失败: {e}")
                _emit_log(action, "error", None, result="db_delete_failed",
                          duration_ms=_ms(t0), error=str(e), code=err.code)
                raise err from e

        _emit_log(action, "info", None, result="cleaned",
                  duration_ms=_ms(t0),
                  deleted_files=deleted_files,
                  deleted_records=deleted_records,
                  days=days)
        return deleted_records

    # ── 埋点 ───────────────────────────────────────────────

    def _record_metrics(self, success: bool, duration_ms: float) -> None:
        """埋点预留：记录业务指标（按 yunshu_replay_storage_* 命名规范）

        实际生产环境可对接 BusinessMetricsCollector；
        这里仅占位，失败不影响主流程。
        """
        try:
            # 占位：trackEvent('yunshu_replay_storage_store', {
            #     'success': success, 'duration_ms': duration_ms
            # })
            pass
        except Exception:
            # 埋点失败吞掉异常，不影响主业务
            pass

    # ── 关闭 ──────────────────────────────────────────────

    def close(self) -> None:
        """关闭 DB 连接"""
        action = "close"
        t0 = time.time()
        try:
            with self._lock:
                self._conn.close()
            _emit_log(action, "info", None, result="closed", duration_ms=_ms(t0))
        except Exception as e:
            _emit_log(action, "warning", None, result="close_failed",
                      duration_ms=_ms(t0), error=str(e))


# ═══════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════

_global_storage: Optional[ReplayStorage] = None
_global_storage_lock = threading.Lock()


def get_replay_storage(storage_root: Optional[str] = None) -> ReplayStorage:
    """获取全局 ReplayStorage 单例

    Args:
        storage_root: 首次创建时指定的根目录（None 时使用默认路径 ./data/replays）
    Returns:
        ReplayStorage 全局实例
    """
    global _global_storage
    action = "get_replay_storage"
    t0 = time.time()
    with _global_storage_lock:
        if _global_storage is None:
            root = storage_root or os.environ.get(
                "REPLAY_STORAGE_ROOT",
                os.path.join(os.getcwd(), "data", "replays"),
            )
            _global_storage = ReplayStorage(root)
            _emit_log(action, "info", None, result="created",
                      duration_ms=_ms(t0), storage_root=root)
        else:
            _emit_log(action, "debug", None, result="reuse",
                      duration_ms=_ms(t0))
        return _global_storage


def _reset_global_for_test() -> None:
    """测试辅助：重置全局单例（仅测试用）"""
    global _global_storage
    if _global_storage is not None:
        try:
            _global_storage.close()
        except Exception:
            pass
    _global_storage = None


# ═══════════════════════════════════════════════════════════════
# 健康检查（用于 /health 接口）
# ═══════════════════════════════════════════════════════════════

def storage_health_check() -> Dict[str, Any]:
    """返回回放存储依赖健康状态（供 /health 接口使用）

    Returns:
        {
            "storage_root": str,
            "db_path_exists": bool,
            "db_writable": bool,
            "disk_free_bytes": int,
        }
    """
    try:
        storage = _global_storage
        if storage is None:
            return {
                "storage_root": "(not initialized)",
                "db_path_exists": False,
                "db_writable": False,
                "disk_free_bytes": 0,
            }
        db_exists = os.path.exists(storage.db_path)
        # 测试 DB 是否可写
        try:
            storage._conn.execute("SELECT 1").fetchone()
            db_writable = True
        except Exception:
            db_writable = False
        # 磁盘剩余空间
        try:
            usage = os.statvfs(storage.storage_root)
            disk_free = usage.f_bavail * usage.f_frsize
        except (OSError, AttributeError):
            disk_free = 0
        return {
            "storage_root": storage.storage_root,
            "db_path_exists": db_exists,
            "db_writable": db_writable,
            "disk_free_bytes": int(disk_free),
        }
    except Exception as e:
        _emit_log("storage_health_check", "error", None,
                  result="check_failed", error=str(e))
        return {
            "storage_root": "(error)",
            "db_path_exists": False,
            "db_writable": False,
            "disk_free_bytes": 0,
            "error": str(e),
        }


__all__ = [
    # 错误码
    "REPLAY_ERR_INVALID_INPUT",
    "REPLAY_ERR_STORAGE_FAILED",
    "REPLAY_ERR_DB_FAILED",
    "REPLAY_ERR_NOT_FOUND",
    "REPLAY_ERR_DECODE_FAILED",
    # 异常类
    "ReplayStorageError",
    # 主类
    "ReplayStorage",
    # 全局单例
    "get_replay_storage",
    "_reset_global_for_test",
    # 健康检查
    "storage_health_check",
    # Schema
    "SCHEMA_SQL",
]
