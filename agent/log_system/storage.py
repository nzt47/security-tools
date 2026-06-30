"""
存储层 — SQLite + JSONL 双后端存储

结构化数据 (操作/性能/错误/行为/系统事件) -> SQLite
非结构化/原始数据 (LLM对话/传感器事件/内省中间过程) -> JSONL
"""

import os
import json
import uuid
import time
import sqlite3
import logging
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from .models import (
    LogLevel, LogCategory, LogEntry,
    PerformanceRecord, ErrorRecord, BehaviorRecord,
    Insight, ActionItem, KnowledgeFinding,
    LogQuery, LogStats,
)

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# 全局存储实例
_global_storage = None


def get_storage():
    """获取全局存储实例"""
    return _global_storage


def _set_storage(storage):
    """设置全局存储实例（内部使用）"""
    global _global_storage
    _global_storage = storage


# 默认路径
DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'logs', 'yunshu_logs.db')
DEFAULT_RAW_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'logs', 'raw')


class LogStorage:
    """日志存储 — 管理 SQLite 和 JSONL 双后端"""

    def __init__(self, db_path: str = None, raw_log_dir: str = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.raw_log_dir = raw_log_dir or DEFAULT_RAW_DIR
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._initialized = False

    # ── 数据库连接管理 ─────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self._local.conn = sqlite3.connect(
                self.db_path, check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    @contextmanager
    def _cursor(self):
        """获取游标的上下文管理器"""
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    def initialize(self):
        """初始化数据库表结构"""
        if self._initialized:
            return

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(self.raw_log_dir, exist_ok=True)

        with self._cursor() as c:
            # 操作日志表
            c.execute("""
                CREATE TABLE IF NOT EXISTS logs_operation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    level TEXT NOT NULL DEFAULT 'info',
                    category TEXT NOT NULL DEFAULT 'operation',
                    operation TEXT NOT NULL,
                    status TEXT DEFAULT 'done',
                    source TEXT DEFAULT '',
                    user_id TEXT DEFAULT '',
                    trace_id TEXT DEFAULT '',
                    duration_ms REAL DEFAULT 0,
                    tags TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}',
                    message TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # 性能指标表
            c.execute("""
                CREATE TABLE IF NOT EXISTS logs_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    metric_name TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT DEFAULT 'ms',
                    source TEXT DEFAULT '',
                    tags TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # 错误记录表
            c.execute("""
                CREATE TABLE IF NOT EXISTS logs_error (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    severity TEXT NOT NULL DEFAULT 'error',
                    message TEXT NOT NULL,
                    source TEXT DEFAULT '',
                    exception_type TEXT DEFAULT '',
                    traceback TEXT DEFAULT '',
                    context TEXT DEFAULT '{}',
                    resolved INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # 用户行为表
            c.execute("""
                CREATE TABLE IF NOT EXISTS logs_behavior (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    user_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    session_id TEXT DEFAULT '',
                    duration_ms REAL DEFAULT 0,
                    payload TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # 系统事件表
            c.execute("""
                CREATE TABLE IF NOT EXISTS logs_system (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    source TEXT DEFAULT '',
                    level TEXT DEFAULT 'info',
                    data TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # 内省洞察表
            c.execute("""
                CREATE TABLE IF NOT EXISTS logs_insight (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generated_at REAL NOT NULL,
                    type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    detail TEXT DEFAULT '',
                    confidence REAL DEFAULT 0,
                    source TEXT DEFAULT '',
                    evidence TEXT DEFAULT '{}',
                    tags TEXT DEFAULT '[]',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # 行动建议表
            c.execute("""
                CREATE TABLE IF NOT EXISTS logs_action_item (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'medium',
                    category TEXT NOT NULL DEFAULT 'performance',
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    rationale TEXT DEFAULT '',
                    expected_impact TEXT DEFAULT '',
                    effort TEXT DEFAULT 'medium',
                    status TEXT DEFAULT 'open',
                    insight_id TEXT DEFAULT '',
                    created_at_iso TEXT DEFAULT (datetime('now'))
                )
            """)
            # 知识发现表
            c.execute("""
                CREATE TABLE IF NOT EXISTS logs_knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    domain TEXT NOT NULL,
                    finding TEXT NOT NULL,
                    confidence REAL DEFAULT 0,
                    tags TEXT DEFAULT '[]',
                    evidence_refs TEXT DEFAULT '[]',
                    created_at_iso TEXT DEFAULT (datetime('now'))
                )
            """)

            # 创建索引
            for table, col in [
                ('logs_operation', 'timestamp'),
                ('logs_performance', 'timestamp'),
                ('logs_performance', 'metric_name'),
                ('logs_error', 'timestamp'),
                ('logs_error', 'severity'),
                ('logs_behavior', 'timestamp'),
                ('logs_behavior', 'user_id'),
                ('logs_system', 'timestamp'),
                ('logs_system', 'event_type'),
                ('logs_insight', 'generated_at'),
                ('logs_insight', 'type'),
            ]:
                try:
                    c.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_{col} ON {table}({col})")
                except Exception:
                    pass

        self._initialized = True
        logger.info("[LogSystem] 存储层初始化完成: db=%s, raw=%s", self.db_path, self.raw_log_dir)

    # ── 写入操作 ─────────────────────────────────────────────

    def write_entry(self, entry: LogEntry):
        """写入一条通用日志条目"""
        cat = entry.category.value if isinstance(entry.category, LogCategory) else entry.category
        tags_json = json.dumps(entry.tags, ensure_ascii=False)
        meta_json = json.dumps(entry.metadata, ensure_ascii=False)

        with self._write_lock, self._cursor() as c:
            c.execute(
                """INSERT INTO logs_operation
                   (timestamp, level, category, operation, status, source,
                    user_id, trace_id, duration_ms, tags, metadata, message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry.timestamp, entry.level.value if isinstance(entry.level, LogLevel) else entry.level,
                 cat, entry.message[:200], 'done', entry.source,
                 entry.user_id, entry.trace_id, entry.duration_ms,
                 tags_json, meta_json, entry.message)
            )

    def write_performance(self, record: PerformanceRecord):
        """写入性能记录"""
        tags_json = json.dumps(record.tags, ensure_ascii=False)
        with self._write_lock, self._cursor() as c:
            c.execute(
                """INSERT INTO logs_performance
                   (timestamp, metric_name, value, unit, source, tags)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (record.timestamp, record.metric_name, record.value,
                 record.unit, record.source, tags_json)
            )

    def write_error(self, record: ErrorRecord):
        """写入错误记录"""
        context_json = json.dumps(record.context, ensure_ascii=False)
        with self._write_lock, self._cursor() as c:
            c.execute(
                """INSERT INTO logs_error
                   (timestamp, severity, message, source, exception_type,
                    traceback, context, resolved)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.timestamp, record.severity, record.message[:1000],
                 record.source, record.exception_type,
                 record.traceback[:5000] if record.traceback else '',
                 context_json, 1 if record.resolved else 0)
            )

    def write_behavior(self, record: BehaviorRecord):
        """写入行为记录"""
        payload_json = json.dumps(record.payload, ensure_ascii=False)
        with self._write_lock, self._cursor() as c:
            c.execute(
                """INSERT INTO logs_behavior
                   (timestamp, user_id, action_type, session_id, duration_ms, payload)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (record.timestamp, record.user_id, record.action_type,
                 record.session_id, record.duration_ms, payload_json)
            )

    def write_raw(self, category: str, data: dict):
        """写入原始 JSONL 日志"""
        date_str = datetime.fromtimestamp(data.get('timestamp', time.time())).strftime('%Y/%m/%d')
        log_dir = os.path.join(self.raw_log_dir, category, date_str)
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"{category}.jsonl")
        line = json.dumps(data, ensure_ascii=False)
        with self._write_lock:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(line + '\n')

    def write_insight(self, insight: Insight):
        """写入内省洞察"""
        evidence_json = json.dumps(insight.evidence, ensure_ascii=False)
        tags_json = json.dumps(insight.tags, ensure_ascii=False)
        with self._write_lock, self._cursor() as c:
            c.execute(
                """INSERT INTO logs_insight
                   (generated_at, type, summary, detail, confidence, source, evidence, tags)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (insight.generated_at, insight.type, insight.summary[:500],
                 insight.detail[:2000] if insight.detail else '',
                 insight.confidence, insight.source_analysis,
                 evidence_json, tags_json)
            )

    def write_action_item(self, item: ActionItem):
        """写入行动建议"""
        with self._write_lock, self._cursor() as c:
            c.execute(
                """INSERT INTO logs_action_item
                   (created_at, priority, category, title, description,
                    rationale, expected_impact, effort, status, insight_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (item.created_at, item.priority, item.category,
                 item.title[:200], item.description[:1000],
                 item.rationale[:500], item.expected_impact[:500],
                 item.effort, item.status, item.insight_id)
            )

    def write_knowledge(self, finding: KnowledgeFinding):
        """写入知识发现"""
        tags_json = json.dumps(finding.tags, ensure_ascii=False)
        refs_json = json.dumps(finding.evidence_refs, ensure_ascii=False)
        with self._write_lock, self._cursor() as c:
            c.execute(
                """INSERT INTO logs_knowledge
                   (created_at, domain, finding, confidence, tags, evidence_refs)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (finding.created_at, finding.domain, finding.finding[:1000],
                 finding.confidence, tags_json, refs_json)
            )

    # ── 查询操作 ─────────────────────────────────────────────

    def query_operations(self, query: LogQuery) -> List[dict]:
        """查询操作日志"""
        return self._query_table('logs_operation', query)

    def query_performance(self, metric_name: str = None,
                          start: float = 0, end: float = 0,
                          limit: int = 100) -> List[dict]:
        """查询性能指标"""
        sql = "SELECT * FROM logs_performance WHERE 1=1"
        params = []
        if metric_name:
            sql += " AND metric_name = ?"
            params.append(metric_name)
        if start > 0:
            sql += " AND timestamp >= ?"
            params.append(start)
        if end > 0:
            sql += " AND timestamp <= ?"
            params.append(end)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return self._fetch(sql, params)

    def query_errors(self, severity: str = None,
                     start: float = 0, end: float = 0,
                     limit: int = 100) -> List[dict]:
        """查询错误记录"""
        sql = "SELECT * FROM logs_error WHERE 1=1"
        params = []
        if severity:
            sql += " AND severity = ?"
            params.append(severity)
        if start > 0:
            sql += " AND timestamp >= ?"
            params.append(start)
        if end > 0:
            sql += " AND timestamp <= ?"
            params.append(end)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return self._fetch(sql, params)

    def query_insights(self, insight_type: str = None,
                       limit: int = 20) -> List[dict]:
        """查询内省洞察"""
        sql = "SELECT * FROM logs_insight WHERE 1=1"
        params = []
        if insight_type:
            sql += " AND type = ?"
            params.append(insight_type)
        sql += " ORDER BY generated_at DESC LIMIT ?"
        params.append(limit)
        return self._fetch(sql, params)

    def query_action_items(self, status: str = None,
                           priority: str = None,
                           limit: int = 50) -> List[dict]:
        """查询行动建议"""
        sql = "SELECT * FROM logs_action_item WHERE 1=1"
        params = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if priority:
            sql += " AND priority = ?"
            params.append(priority)
        sql += " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at DESC LIMIT ?"
        params.append(limit)
        return self._fetch(sql, params)

    def query_knowledge(self, domain: str = None,
                        limit: int = 50) -> List[dict]:
        """查询知识发现"""
        sql = "SELECT * FROM logs_knowledge WHERE 1=1"
        params = []
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        sql += " ORDER BY confidence DESC, created_at DESC LIMIT ?"
        params.append(limit)
        return self._fetch(sql, params)

    # ── 统计聚合 ─────────────────────────────────────────────

    def get_stats(self, hours: float = 24) -> LogStats:
        """获取日志统计"""
        since = time.time() - hours * 3600
        stats = LogStats(time_range_hours=hours)

        with self._cursor() as c:
            # 总数
            c.execute("SELECT COUNT(*) as cnt FROM logs_operation WHERE timestamp >= ?", (since,))
            row = c.fetchone()
            stats.total_count = row['cnt'] if row else 0

            # 按分类统计
            c.execute("SELECT category, COUNT(*) as cnt FROM logs_operation WHERE timestamp >= ? GROUP BY category", (since,))
            for row in c.fetchall():
                stats.by_category[row['category']] = row['cnt']

            # 按级别统计
            c.execute("SELECT level, COUNT(*) as cnt FROM logs_operation WHERE timestamp >= ? GROUP BY level", (since,))
            for row in c.fetchall():
                stats.by_level[row['level']] = row['cnt']

            # 前10来源
            c.execute("SELECT source, COUNT(*) as cnt FROM logs_operation WHERE timestamp >= ? AND source != '' GROUP BY source ORDER BY cnt DESC LIMIT 10", (since,))
            stats.top_sources = [(row['source'], row['cnt']) for row in c.fetchall()]

            # 错误率
            c.execute("SELECT COUNT(*) as err FROM logs_operation WHERE timestamp >= ? AND level IN ('error','critical')", (since,))
            err_row = c.fetchone()
            err_count = err_row['err'] if err_row else 0
            stats.error_rate = err_count / stats.total_count if stats.total_count > 0 else 0.0

            # 平均耗时
            c.execute("SELECT duration_ms FROM logs_operation WHERE timestamp >= ? AND duration_ms > 0 ORDER BY duration_ms", (since,))
            rows = c.fetchall()
            durations = [r['duration_ms'] for r in rows if r['duration_ms']]
            if durations:
                stats.avg_duration_ms = sum(durations) / len(durations)
                sorted_d = sorted(durations)
                idx95 = int(len(sorted_d) * 0.95)
                idx99 = int(len(sorted_d) * 0.99)
                stats.p95_duration_ms = sorted_d[idx95] if idx95 < len(sorted_d) else sorted_d[-1]
                stats.p99_duration_ms = sorted_d[idx99] if idx99 < len(sorted_d) else sorted_d[-1]

        return stats

    def get_metric_trend(self, metric_name: str, hours: float = 24, bucket_minutes: int = 10) -> List[dict]:
        """获取指标趋势（按时间桶聚合）"""
        since = time.time() - hours * 3600
        bucket_seconds = bucket_minutes * 60
        sql = """
            SELECT
                CAST((timestamp - ?) / ? AS INTEGER) as bucket,
                MIN(timestamp) as bucket_start,
                COUNT(*) as sample_count,
                AVG(value) as avg_val,
                MIN(value) as min_val,
                MAX(value) as max_val
            FROM logs_performance
            WHERE metric_name = ? AND timestamp >= ?
            GROUP BY bucket
            ORDER BY bucket
        """
        return self._fetch(sql, (since, bucket_seconds, metric_name, since))

    def get_error_trend(self, hours: float = 24, bucket_minutes: int = 30) -> List[dict]:
        """获取错误趋势"""
        since = time.time() - hours * 3600
        bucket_seconds = bucket_minutes * 60
        sql = """
            SELECT
                CAST((timestamp - ?) / ? AS INTEGER) as bucket,
                MIN(timestamp) as bucket_start,
                COUNT(*) as error_count,
                GROUP_CONCAT(DISTINCT exception_type) as exception_types
            FROM logs_error
            WHERE timestamp >= ?
            GROUP BY bucket
            ORDER BY bucket
        """
        return self._fetch(sql, (since, bucket_seconds, since))

    # ── 内部方法 ─────────────────────────────────────────────

    def _query_table(self, table: str, query: LogQuery) -> List[dict]:
        """通用表查询"""
        sql = f"SELECT * FROM {table} WHERE 1=1"
        params = []
        if query.start_time > 0:
            sql += " AND timestamp >= ?"
            params.append(query.start_time)
        if query.end_time > 0:
            sql += " AND timestamp <= ?"
            params.append(query.end_time)
        if query.source:
            sql += " AND source LIKE ?"
            params.append(f"%{query.source}%")
        if query.user_id:
            sql += " AND user_id = ?"
            params.append(query.user_id)
        if query.text_search:
            sql += " AND (message LIKE ? OR operation LIKE ?)"
            params.append(f"%{query.text_search}%")
            params.append(f"%{query.text_search}%")
        order = "DESC" if query.order_desc else "ASC"
        sql += f" ORDER BY {query.order_by} {order} LIMIT ? OFFSET ?"
        params.append(query.limit)
        params.append(query.offset)
        return self._fetch(sql, params)

    def _fetch(self, sql: str, params: list) -> List[dict]:
        """执行查询并返回字典列表"""
        try:
            with self._cursor() as c:
                c.execute(sql, params)
                return [dict(row) for row in c.fetchall()]
        except Exception as e:
            logger.error("[LogSystem] 查询失败: %s\nSQL: %s\nParams: %s", e, sql, params)
            return []

    def close(self):
        """关闭存储"""
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    def vacuum(self):
        """压缩数据库"""
        with self._cursor() as c:
            c.execute("VACUUM")
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "storage", "action": "log", "msg": "[LogSystem] 数据库压缩完成"}, ensure_ascii=False))
