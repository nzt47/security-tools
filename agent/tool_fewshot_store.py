"""ToolFewshotStore — 真实成功调用采样存储(Dynamic Few-shot 数据源)

从真实成功工具调用中采样脱敏样本,供 orchestrator 注入 messages 动态区。

【不易】
  - 仅记录成功调用(output_data 的 ok 为真)
  - input/output 结构保留脱敏(复用 sensitive_data_filter,password→********)
  - tool_trace 仅存哈希的约束不动,本模块独立存储还原结构供 LLM 学习
  - 单例 + 同步写 SQLite(agent/data/tool_fewshot.db)
【变易】
  - 配置走 .env: FEWSHOT_ENABLED / FEWSHOT_WINDOW_DAYS / FEWSHOT_PER_TOOL
    / FEWSHOT_MAX_INPUT_LEN / FEWSHOT_MAX_OUTPUT_LEN
  - 采样窗口默认 7 天,每工具最多 2 个样本
【简易】
  - record() / get_recent() / sample_for_tools() 三个公开方法
  - 任何异常降级忽略,不阻塞主链路
"""
from __future__ import annotations

import os
import json
import time
import logging
import sqlite3
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except Exception:
        return default


def _env_bool(key: str, default: bool) -> bool:
    return os.environ.get(key, "1" if default else "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


FEWSHOT_ENABLED = _env_bool("FEWSHOT_ENABLED", True)
FEWSHOT_WINDOW_DAYS = _env_int("FEWSHOT_WINDOW_DAYS", 7)
FEWSHOT_PER_TOOL = _env_int("FEWSHOT_PER_TOOL", 2)
FEWSHOT_MAX_INPUT_LEN = _env_int("FEWSHOT_MAX_INPUT_LEN", 2000)
FEWSHOT_MAX_OUTPUT_LEN = _env_int("FEWSHOT_MAX_OUTPUT_LEN", 2000)

# 项目根目录下的数据目录(与 tool_trace 同级)
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "tool_fewshot.db",
)


class ToolFewshotStore:
    """Few-shot 样本存储(单例)"""

    _instance: Optional["ToolFewshotStore"] = None
    _lock = threading.Lock()

    def __init__(self, db_path: str = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._write_lock = threading.Lock()
        self._local = threading.local()
        self._init_db()

    # ── 单例 ──
    @classmethod
    def instance(cls) -> "ToolFewshotStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """测试用:重置单例"""
        with cls._lock:
            cls._instance = None

    # ── SQLite 连接(线程本地) ──
    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, timeout=5.0)
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    def _init_db(self):
        try:
            with self._write_lock:
                conn = self._get_conn()
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS fewshot_samples (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tool_name TEXT NOT NULL,
                        input_json TEXT NOT NULL,
                        output_json TEXT NOT NULL,
                        timestamp REAL NOT NULL,
                        session_id TEXT DEFAULT ''
                    )"""
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fewshot_tool_time ON fewshot_samples(tool_name, timestamp)"
                )
                conn.commit()
        except Exception as e:
            logger.warning("[FewshotStore] 初始化失败(降级为内存): %s", e)
            self._degraded = True

    # ── 脱敏 ──
    def _mask(self, data: Any) -> Any:
        """结构保留脱敏(复用 sensitive_data_filter)"""
        try:
            from agent.utils.sensitive_data_filter import get_default_filter
            return get_default_filter().filter_data(data)
        except Exception:
            return data

    def _is_success(self, output_data: Any) -> bool:
        """判定调用是否成功:output 的 ok 为真"""
        if isinstance(output_data, dict):
            return output_data.get("ok") is True
        return False

    def _truncate(self, data: Any, max_chars: int) -> str:
        """序列化并截断,保证输出合法 JSON(可被 json.loads 解析)"""
        s = json.dumps(data, ensure_ascii=False, default=str)
        if len(s) <= max_chars:
            return s
        return json.dumps(
            {"_truncated": True, "preview": s[:max_chars]},
            ensure_ascii=False,
        )

    # ── 记录(仅成功调用) ──
    def record(
        self,
        tool_name: str,
        input_data: Any,
        output_data: Any,
        session_id: str = "",
    ) -> bool:
        """记录一次成功调用样本(内部判定 ok;失败调用不记录)"""
        if not FEWSHOT_ENABLED:
            return False
        if not self._is_success(output_data):
            return False
        try:
            masked_input = self._mask(input_data)
            masked_output = self._mask(output_data)
            input_json = self._truncate(masked_input, FEWSHOT_MAX_INPUT_LEN)
            output_json = self._truncate(masked_output, FEWSHOT_MAX_OUTPUT_LEN)
            with self._write_lock:
                conn = self._get_conn()
                conn.execute(
                    "INSERT INTO fewshot_samples(tool_name, input_json, output_json, timestamp, session_id) "
                    "VALUES(?,?,?,?,?)",
                    (tool_name, input_json, output_json, time.time(), session_id or ""),
                )
                conn.commit()
            return True
        except Exception as e:
            logger.debug("[FewshotStore] record 降级忽略: %s", e)
            return False

    # ── 查询 ──
    def get_recent(
        self,
        tool_name: str,
        window_days: int = None,
        limit: int = None,
    ) -> List[Dict]:
        """查询某工具最近成功调用样本(按时间倒序)"""
        window_days = window_days or FEWSHOT_WINDOW_DAYS
        limit = limit or FEWSHOT_PER_TOOL
        if not FEWSHOT_ENABLED:
            return []
        try:
            since = time.time() - window_days * 86400
            with self._write_lock:
                conn = self._get_conn()
                rows = conn.execute(
                    "SELECT input_json, output_json FROM fewshot_samples "
                    "WHERE tool_name=? AND timestamp>=? ORDER BY timestamp DESC LIMIT ?",
                    (tool_name, since, limit),
                ).fetchall()
            samples = []
            for input_json, output_json in rows:
                try:
                    samples.append({
                        "input": json.loads(input_json),
                        "output": json.loads(output_json),
                    })
                except Exception:
                    continue
            return samples
        except Exception as e:
            logger.debug("[FewshotStore] get_recent 降级返回空: %s", e)
            return []

    def sample_for_tools(self, tool_names: List[str]) -> Dict[str, List[Dict]]:
        """批量采样:按工具名分组,每工具最多 FEWSHOT_PER_TOOL 个"""
        result: Dict[str, List[Dict]] = {}
        if not tool_names:
            return result
        for name in tool_names:
            samples = self.get_recent(name)
            if samples:
                result[name] = samples
        return result

    # ── 维护 ──
    def clear(self):
        """清空所有样本(测试/维护用)"""
        try:
            with self._write_lock:
                conn = self._get_conn()
                conn.execute("DELETE FROM fewshot_samples")
                conn.commit()
        except Exception:
            pass

    def cleanup_expired(self, window_days: int = None):
        """清理过期样本"""
        window_days = window_days or FEWSHOT_WINDOW_DAYS
        try:
            since = time.time() - window_days * 86400
            with self._write_lock:
                conn = self._get_conn()
                conn.execute("DELETE FROM fewshot_samples WHERE timestamp<?", (since,))
                conn.commit()
        except Exception:
            pass
