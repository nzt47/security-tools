"""会话消息全文索引（E1-4b）

【解决什么】会话列表只能按标题/时间找；想找"上次讨论检查点的那一轮"只能一个个点开翻。
本模块把每条消息写进 SQLite **FTS5 trigram** 索引，提供：

- `index_message()`：`SessionManager.add_message()` 提交后**同进程**追加一行（失败只 WARN）；
- `search()`：中文子串检索（≥3 字走 MATCH + `snippet()` 高亮；<3 字回落 `LIKE` + `LIMIT`）；
- `rebuild()`：全量重建（`scripts/rebuild_session_index.py` 用）；
- `stats()`：条数/库大小/是否启用（交接卡与探针读数）。

【为什么 trigram】`unicode61` 不切中文，`LIKE '%词%'` 在大表上会全扫；FTS5 的
`tokenize='trigram'` 对 CJK 子串可用且**不引新依赖**（不装 jieba）。代价：只支持 ≥3 字。

【边界】索引是**可丢弃的派生数据**（`data/session_index.db`，运行期状态、不入库）：
库丢了/删了只是"搜不到"，`rebuild()` 能从 `data/sessions/*/messages.jsonl` 全量重建。
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ENABLED_ENV = "CP_SESSION_INDEX_ENABLED"

#: 查询上限（服务端硬上限，防把上下文灌爆；路由再收一次）
MAX_QUERY_CHARS = 200
MAX_LIMIT = 50
DEFAULT_LIMIT = 20
#: 单条命中返回的片段长度（snippet 自带高亮标记）
SNIPPET_TOKENS = 12
#: 落库前截断：一条消息进索引最多这么多字符（超长消息只索引前半段）
MAX_INDEXED_CHARS = 8_000

_TRIGRAM_MIN = 3  # trigram 查询至少 3 个字

_write_lock = threading.Lock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    session_id TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    ts         TEXT,
    PRIMARY KEY (session_id, seq)
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, session_id UNINDEXED, seq UNINDEXED, tokenize='trigram'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def enabled() -> bool:
    raw = str(os.environ.get("CP_SESSION_INDEX_ENABLED", "1") or "").strip().lower()
    return raw not in ("0", "false", "no", "off", "disable", "disabled")


def db_path() -> Path:
    override = str(os.environ.get("CP_SESSION_INDEX_PATH", "") or "").strip()
    if override:
        return Path(override)
    root = Path(__file__).resolve().parents[2]
    return root / "data" / "session_index.db"


def _connect() -> Optional[sqlite3.Connection]:
    try:
        path = db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=10.0)
        conn.executescript(_SCHEMA)
        return conn
    except Exception as exc:  # noqa: BLE001 索引用不了只是"搜不到"，不影响会话
        logger.warning("[session-index] 打开索引失败: %s", type(exc).__name__)
        return None


def normalize_query(raw: str) -> str:
    """查询串归一（纯函数）：压平空白 + 去 FTS 语法字符 + 截断。"""
    text = " ".join(str(raw or "").split())
    text = re.sub(r'["\'^*(){}:\[\]\-+]', " ", text)
    text = " ".join(text.split())
    return text[:MAX_QUERY_CHARS]


def _clamp_limit(limit: Any) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, value))


def index_message(session_id: str, role: str, content: str, ts: str = "",
                  seq: Optional[int] = None) -> bool:
    """把一条消息写进索引（幂等：同 session+seq 覆盖）。返回是否写入。"""
    if not enabled() or not session_id:
        return False
    text = str(content or "")[:MAX_INDEXED_CHARS]
    if not text.strip():
        return False
    with _write_lock:
        conn = _connect()
        if conn is None:
            return False
        try:
            with conn:
                if seq is None:
                    row = conn.execute(
                        "SELECT COALESCE(MAX(seq) + 1, 0) FROM messages WHERE session_id = ?",
                        (session_id,)).fetchone()
                    seq = int(row[0] if row else 0)
                conn.execute(
                    "INSERT OR REPLACE INTO messages(session_id, seq, role, content, ts)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (session_id, int(seq), str(role or ""), text, str(ts or "")))
                conn.execute(
                    "DELETE FROM messages_fts WHERE session_id = ? AND seq = ?",
                    (session_id, int(seq)))
                conn.execute(
                    "INSERT INTO messages_fts(content, session_id, seq) VALUES (?, ?, ?)",
                    (text, session_id, int(seq)))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session-index] 写入失败: %s", type(exc).__name__)
            return False
        finally:
            conn.close()


def _row_to_hit(row) -> Dict[str, Any]:
    return {
        "session_id": row[0],
        "seq": int(row[1] or 0),
        "role": row[2],
        "snippet": row[3],
        "ts": row[4] or "",
    }


def search(query: str, limit: Any = DEFAULT_LIMIT) -> Dict[str, Any]:
    """检索会话消息。

    Returns:
        {"ok": bool, "mode": "match"|"like"|"empty", "hits": [...], "took_ms": float}
        任何异常都返回 ok=False（不抛），路由据此回 503/空结果。
    """
    import time

    started = time.perf_counter()
    text = normalize_query(query)
    capped = _clamp_limit(limit)
    if not enabled():
        return {"ok": False, "mode": "disabled", "hits": [], "took_ms": 0.0,
                "error": "索引已关闭"}
    if not text:
        return {"ok": True, "mode": "empty", "hits": [], "took_ms": 0.0}
    conn = _connect()
    if conn is None:
        return {"ok": False, "mode": "error", "hits": [], "took_ms": 0.0,
                "error": "索引不可用"}
    try:
        if len(text) >= _TRIGRAM_MIN:
            # role/ts 不在 FTS 表里（只索引正文）⇒ join messages 取回。
            # 注意：`snippet()` 的第一个参数必须是 **FTS 表名本身**，写别名会
            # "no such column: f"（SQLite 实测，2026-11-26）。
            rows = conn.execute(
                "SELECT messages_fts.session_id, messages_fts.seq, m.role,"
                " snippet(messages_fts, 0, '[', ']', '…', ?), m.ts"
                " FROM messages_fts JOIN messages AS m"
                "   ON m.session_id = messages_fts.session_id AND m.seq = messages_fts.seq"
                " WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?",
                (SNIPPET_TOKENS, '"%s"' % text.replace('"', " "), capped)).fetchall()
            mode = "match"
        else:
            like = "%" + text.replace("%", "").replace("_", "") + "%"
            rows = conn.execute(
                "SELECT session_id, seq, role, substr(content, 1, 120), ts"
                " FROM messages WHERE content LIKE ? ORDER BY seq DESC LIMIT ?",
                (like, capped)).fetchall()
            mode = "like"
        hits = [_row_to_hit(row) for row in rows]
        return {"ok": True, "mode": mode, "hits": hits,
                "took_ms": round((time.perf_counter() - started) * 1000.0, 2)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[session-index] 检索失败: %s", type(exc).__name__)
        return {"ok": False, "mode": "error", "hits": [], "took_ms": 0.0,
                "error": "检索失败（详见服务日志）"}
    finally:
        conn.close()


def clear(session_id: str = "") -> bool:
    """清掉某会话（或全部）的索引行（会话删除 / 重建时用）。"""
    with _write_lock:
        conn = _connect()
        if conn is None:
            return False
        try:
            with conn:
                if session_id:
                    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
                    conn.execute("DELETE FROM messages_fts WHERE session_id = ?", (session_id,))
                else:
                    conn.execute("DELETE FROM messages")
                    conn.execute("DELETE FROM messages_fts")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session-index] 清理失败: %s", type(exc).__name__)
            return False
        finally:
            conn.close()


def rebuild(sessions_dir: Optional[Path] = None) -> Dict[str, Any]:
    """从 `data/sessions/*/messages.jsonl` 全量重建索引。返回统计（可观测）。"""
    import json
    import time

    started = time.perf_counter()
    base = Path(sessions_dir) if sessions_dir else (Path(__file__).resolve().parents[2]
                                                    / "data" / "sessions")
    if not base.is_dir():
        return {"ok": False, "error": "会话目录不存在: %s" % base, "sessions": 0, "messages": 0}
    if not clear():
        return {"ok": False, "error": "清空旧索引失败", "sessions": 0, "messages": 0}
    sessions = 0
    messages = 0
    failed = 0
    for session_dir in sorted(base.iterdir()):
        msg_file = session_dir / "messages.jsonl"
        if not session_dir.is_dir() or not msg_file.is_file():
            continue
        sessions += 1
        seq = 0
        try:
            with open(msg_file, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        failed += 1
                        continue
                    if index_message(session_dir.name, str(row.get("role") or ""),
                                     str(row.get("content") or ""),
                                     str(row.get("timestamp") or ""), seq=seq):
                        messages += 1
                    seq += 1
        except OSError:
            failed += 1
    return {"ok": True, "sessions": sessions, "messages": messages, "failed": failed,
            "took_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "db": str(db_path())}


def stats() -> Dict[str, Any]:
    """索引读数（条数 / 库大小 / 开关）；打不开就如实回 0 与错误。"""
    info: Dict[str, Any] = {"enabled": enabled(), "db": str(db_path()),
                            "messages": 0, "sessions": 0, "size_bytes": 0}
    try:
        info["size_bytes"] = db_path().stat().st_size if db_path().is_file() else 0
    except OSError:
        pass
    conn = _connect()
    if conn is None:
        info["error"] = "索引不可用"
        return info
    try:
        info["messages"] = int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
        info["sessions"] = int(conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM messages").fetchone()[0])
    except Exception as exc:  # noqa: BLE001
        info["error"] = type(exc).__name__
    finally:
        conn.close()
    return info


__all__ = [
    "ENABLED_ENV", "MAX_QUERY_CHARS", "MAX_LIMIT", "DEFAULT_LIMIT",
    "enabled", "db_path", "normalize_query", "index_message", "search",
    "clear", "rebuild", "stats",
]
