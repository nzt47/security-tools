"""全链路工具调用埋点与持久化

为工具调用提供统一的 trace 记录能力,回答"某次工具调用的输入/输出/延迟/是否成功"。

设计要点(三义):
- [不易] 不阻塞主路径: record() 仅入队,后台 daemon thread 批量写 SQLite
- [不易] 失败兜底: SQLite 写入失败 → 降级到内存 ring buffer,不抛异常
- [不易] 脱敏: input/output 仅存 SHA256 哈希前 16 位,不存原文
- [变易] 采样策略可配置: 高频工具 10% / 低频全量 / 危险操作 100%
- [简易] 集成点用 start_trace/finish_trace 包裹,改动最小化

持久化:
- SQLite(agent/data/tool_trace.db),表结构自动初始化
- 索引: (tool_name, timestamp), (session_id, timestamp), (success, timestamp)

查询接口:
- get_recent_traces(tool_name, limit)
- get_failed_traces(since)
- get_latency_p99(tool_name, window)
"""

from __future__ import annotations

import os
import re
import json
import time
import uuid
import random
import hashlib
import sqlite3
import logging
import threading
import contextvars
import queue as queue_module
from collections import deque
from dataclasses import dataclass, field
from typing import Any, List, Optional

logger = logging.getLogger("agent.observability.tool_trace")

# ════════════════════════════════════════════════════════════
#  路径与配置
# ════════════════════════════════════════════════════════════

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, "agent", "data", "tool_trace.db")
_DANGEROUS_CMDS_PATH = os.path.join(_PROJECT_ROOT, "data", "dangerous_commands.json")

# 高频工具 Top 20(按类别优先级 core > web > file,采样率 10%)
# 来源: agent/tool_router.py 的 TOOL_CATEGORIES 平铺取前 20
HIGH_FREQ_TOOLS = frozenset([
    # core(5)
    "get_status", "search_memory", "remember", "expand_context", "get_sensor_summary",
    # web(9)
    "web_search", "web_get", "web_post", "web_xpath", "web_css",
    "web_clean_data", "web_download", "web_batch", "fetch_news",
    # file(6)
    "read_file", "write_file", "list_directory", "get_file_info", "search_files", "compress",
])

# 采样率配置
HIGH_FREQ_SAMPLE_RATE = 0.1      # 高频工具 10% 采样
RING_BUFFER_MAXLEN = 1000         # 降级时内存 ring buffer 容量
WRITER_BATCH_SIZE = 100           # 后台线程单次批量写入大小
WRITER_POLL_INTERVAL = 1.0        # 后台线程轮询间隔(秒)

# ContextVar: 在同一调用链中传递 trace_id 和 permission_decision
# Why: PermissionGateway.check 在工具执行前调用,设置决策;_execute_safe 读取决策填入 trace
_trace_id_var: contextvars.ContextVar = contextvars.ContextVar("tool_trace_id", default=None)
_permission_decision_var: contextvars.ContextVar = contextvars.ContextVar(
    "tool_permission_decision", default=""
)


# ════════════════════════════════════════════════════════════
#  数据类
# ════════════════════════════════════════════════════════════

@dataclass
class ToolTraceRecord:
    """工具调用 trace 记录

    所有 hash 字段均为 SHA256 前 16 位,不存原文(脱敏)。
    """
    trace_id: str                              # 16 位 hex
    tool_name: str
    input_hash: str                            # 输入脱敏哈希
    output_hash: str                           # 输出脱敏哈希
    latency_ms: float                          # 工具执行耗时(毫秒)
    success: bool                              # 是否成功
    error_type: str = ""                       # 失败时的异常类名,成功时为空
    session_id: str = ""                       # 会话 ID
    user_role: str = "guest"                   # 用户角色
    timestamp: float = field(default_factory=time.time)  # Unix 时间戳
    permission_decision: str = ""              # 权限决策("allowed"/"denied"/"allowed:reason")

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "tool_name": self.tool_name,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "latency_ms": round(self.latency_ms, 2),
            "success": self.success,
            "error_type": self.error_type,
            "session_id": self.session_id,
            "user_role": self.user_role,
            "timestamp": self.timestamp,
            "permission_decision": self.permission_decision,
        }


@dataclass
class _TraceContext:
    """trace 执行上下文(start_trace 与 finish_trace 之间传递)"""
    trace_id: str
    tool_name: str
    input_hash: str
    is_dangerous: bool
    start_time: float
    session_id: str = ""
    user_role: str = "guest"


# ════════════════════════════════════════════════════════════
#  单例 Recorder
# ════════════════════════════════════════════════════════════

class ToolTraceRecorder:
    """工具调用 trace 记录器(单例)

    用法:
        recorder = ToolTraceRecorder.instance()
        ctx = recorder.start_trace("web_search", {"query": "..."})
        try:
            result = execute_tool(...)
            recorder.finish_trace(ctx, result, None)
        except Exception as e:
            recorder.finish_trace(ctx, None, e)
            raise

    持久化:
        - 异步写入: record() 仅入队,后台 daemon thread 批量写 SQLite
        - 降级: SQLite 失败 → 内存 ring buffer,不抛异常
    """

    _instance: Optional["ToolTraceRecorder"] = None
    _instance_lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None):
        """初始化 Recorder

        Args:
            db_path: SQLite 数据库路径,默认 agent/data/tool_trace.db
                     传 ":memory:" 用于测试
        """
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._queue: queue_module.Queue = queue_module.Queue()
        self._fallback_ring_buffer: deque = deque(maxlen=RING_BUFFER_MAXLEN)
        self._degraded: bool = False
        self._stopped: bool = False
        self._write_lock = threading.Lock()
        self._local = threading.local()
        self._critical_patterns: List[re.Pattern] = self._load_critical_patterns()

        # SQLite 初始化(失败则降级)
        if self._db_path == ":memory:":
            # 测试模式: 直接初始化内存库
            self._init_db()
        else:
            try:
                self._init_db()
            except Exception as e:
                logger.warning(f"SQLite 初始化失败,降级到 ring buffer: {e}")
                self._degraded = True

        # 启动后台写入线程
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="tool-trace-writer",
            daemon=True,
        )
        self._writer_thread.start()

    @classmethod
    def instance(cls) -> "ToolTraceRecorder":
        """获取单例"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例(测试用)

        Why: 测试间需隔离单例状态,避免数据库文件残留
        """
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance._stopped = True
                cls._instance = None

    # ── 公共接口: trace 生命周期 ──────────────────────────────

    def start_trace(
        self,
        func_name: str,
        input_data: Any,
        session_id: str = "",
        user_role: str = "guest",
    ) -> _TraceContext:
        """工具执行开始时调用,生成 trace 上下文

        Args:
            func_name: 工具名
            input_data: 工具输入参数(将被脱敏哈希)
            session_id: 会话 ID
            user_role: 用户角色

        Returns:
            _TraceContext: 传递给 finish_trace
        """
        trace_id = _trace_id_var.get() or _generate_trace_id()
        _trace_id_var.set(trace_id)
        input_hash = self.hash_content(input_data)
        is_dangerous = self._is_dangerous(input_data)
        return _TraceContext(
            trace_id=trace_id,
            tool_name=func_name,
            input_hash=input_hash,
            is_dangerous=is_dangerous,
            start_time=time.perf_counter(),
            session_id=session_id,
            user_role=user_role,
        )

    def finish_trace(
        self,
        ctx: _TraceContext,
        output_data: Any,
        exception: Optional[Exception],
    ) -> None:
        """工具执行结束时调用,构造 record 并记录

        Args:
            ctx: start_trace 返回的上下文
            output_data: 工具输出(将被脱敏哈希),异常时可为 None
            exception: 工具执行异常,成功时为 None

        Note:
            若无异常但 output_data 是 dict 且含 "ok" 字段,
            用 output_data["ok"] 推断 success(适配 _execute_safe 返回 dict 的约定)
        """
        latency_ms = (time.perf_counter() - ctx.start_time) * 1000
        success = exception is None
        # 适配工具返回 dict(含 "ok" 字段)的约定: 无异常但 ok=False 视为失败
        if success and isinstance(output_data, dict) and "ok" in output_data:
            success = bool(output_data["ok"])
        error_type = type(exception).__name__ if exception else ""
        # 无异常但 success=False: 工具返回错误结果,标记为 ToolError
        if not success and not error_type:
            error_type = "ToolError"
        output_hash = self.hash_content(output_data) if output_data is not None else ""
        permission_decision = _permission_decision_var.get()

        record = ToolTraceRecord(
            trace_id=ctx.trace_id,
            tool_name=ctx.tool_name,
            input_hash=ctx.input_hash,
            output_hash=output_hash,
            latency_ms=latency_ms,
            success=success,
            error_type=error_type,
            session_id=ctx.session_id,
            user_role=ctx.user_role,
            permission_decision=permission_decision,
        )
        # 危险操作强制记录(100% 采样),其他按采样策略
        self.record(record, force=ctx.is_dangerous)

        # 清理 ContextVar(防止泄漏到下一次调用)
        _trace_id_var.set(None)
        _permission_decision_var.set("")

    # ── 公共接口: record + 查询 ──────────────────────────────

    def record(self, record: ToolTraceRecord, force: bool = False) -> None:
        """记录 trace(异步入队 + 采样策略)

        Args:
            record: trace 记录
            force: 强制记录(跳过采样),用于危险操作
        """
        # 采样决策
        if not force:
            if record.tool_name in HIGH_FREQ_TOOLS:
                if random.random() >= HIGH_FREQ_SAMPLE_RATE:
                    return  # 90% 丢弃

        # 入队(不阻塞主路径)
        try:
            self._queue.put_nowait(record)
        except queue_module.Full:
            # 队列满,降级到 ring buffer
            self._fallback_ring_buffer.append(record)
        except Exception as e:
            # 兜底:任何异常都不影响主路径
            logger.debug(f"trace 入队失败,降级到 ring buffer: {e}")
            self._fallback_ring_buffer.append(record)

    def get_recent_traces(self, tool_name: str, limit: int = 100) -> List[ToolTraceRecord]:
        """查询指定工具最近的 trace

        Args:
            tool_name: 工具名
            limit: 最大返回数

        Returns:
            List[ToolTraceRecord]: 按 timestamp 倒序
        """
        if self._degraded:
            return [r for r in self._fallback_ring_buffer if r.tool_name == tool_name][-limit:]
        try:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT trace_id, tool_name, input_hash, output_hash, latency_ms, "
                "success, error_type, session_id, user_role, timestamp, permission_decision "
                "FROM tool_traces WHERE tool_name=? ORDER BY timestamp DESC LIMIT ?",
                (tool_name, limit)
            ).fetchall()
            return [self._row_to_record(row) for row in rows]
        except Exception as e:
            logger.warning(f"查询 recent_traces 失败: {e}")
            return []

    def get_failed_traces(self, since: float) -> List[ToolTraceRecord]:
        """查询指定时间后的失败 trace

        Args:
            since: Unix 时间戳

        Returns:
            List[ToolTraceRecord]: 按 timestamp 倒序
        """
        if self._degraded:
            return [r for r in self._fallback_ring_buffer
                    if not r.success and r.timestamp >= since]
        try:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT trace_id, tool_name, input_hash, output_hash, latency_ms, "
                "success, error_type, session_id, user_role, timestamp, permission_decision "
                "FROM tool_traces WHERE success=0 AND timestamp>=? ORDER BY timestamp DESC",
                (since,)
            ).fetchall()
            return [self._row_to_record(row) for row in rows]
        except Exception as e:
            logger.warning(f"查询 failed_traces 失败: {e}")
            return []

    def get_latency_p99(self, tool_name: str, window: int = 3600) -> float:
        """查询指定工具的 p99 延迟(毫秒)

        Args:
            tool_name: 工具名
            window: 时间窗口(秒),默认 3600(1 小时)

        Returns:
            float: p99 延迟(毫秒),无数据返回 0.0
        """
        cutoff = time.time() - window
        if self._degraded:
            latencies = sorted([
                r.latency_ms for r in self._fallback_ring_buffer
                if r.tool_name == tool_name and r.timestamp >= cutoff
            ])
        else:
            try:
                conn = self._get_conn()
                rows = conn.execute(
                    "SELECT latency_ms FROM tool_traces "
                    "WHERE tool_name=? AND timestamp>=? ORDER BY latency_ms",
                    (tool_name, cutoff)
                ).fetchall()
                latencies = [row[0] for row in rows]
            except Exception as e:
                logger.warning(f"查询 latency_p99 失败: {e}")
                return 0.0

        if not latencies:
            return 0.0
        # p99: 第 99 百分位
        idx = int(len(latencies) * 0.99)
        if idx >= len(latencies):
            idx = len(latencies) - 1
        return latencies[idx]

    # ── 公共接口: 权限决策 + 工具选择 ─────────────────────────

    def set_permission_decision(self, allowed: bool, reason: str = "") -> None:
        """设置当前上下文的权限决策(供 PermissionGateway.check 调用)

        Args:
            allowed: 是否允许
            reason: 原因(可选,截断 50 字符)
        """
        decision = "allowed" if allowed else "denied"
        if reason:
            decision += f":{reason[:50]}"
        _permission_decision_var.set(decision)

    def record_tool_selection(
        self,
        user_input: str,
        categories: set,
        tools: list,
    ) -> None:
        """记录工具选择决策(结构化日志,不持久化到 SQLite)

        Why: 工具选择决策是轻量事件,用结构化日志记录即可,
             SQLite 只持久化工具执行 trace(ToolTraceRecord)

        Args:
            user_input: 用户输入(脱敏哈希后记录)
            categories: 匹配的工具类别集合
            tools: 选中的工具列表
        """
        logger.info(json.dumps({
            "module_name": "tool_trace",
            "action": "tool_selection",
            "user_input_hash": self.hash_content(user_input),
            "categories": sorted(list(categories)),
            "tools_count": len(tools),
            "tools_preview": tools[:10],
        }, ensure_ascii=False))

    def record_circuit_event(
        self,
        scope: Any,
        session_id: str,
        user_id: str,
        tool_name: str,
        blocked: bool,
    ) -> None:
        """记录三级熔断事件(结构化日志,不持久化到 SQLite)

        Why: 熔断事件是轻量决策事件,用结构化日志记录即可,
             SQLite 只持久化工具执行 trace(ToolTraceRecord),
             避免增加 tool_traces 表 schema(简易原则)。

        Args:
            scope: CircuitScope 枚举值(SESSION/USER/GLOBAL),支持 str 或 Enum
            session_id: 会话 ID(脱敏哈希后记录)
            user_id: 用户 ID(脱敏哈希后记录)
            tool_name: 被熔断的工具名
            blocked: 是否被阻断(True=熔断拒绝, False=放行)
        """
        # 兼容 CircuitScope 枚举与裸字符串(简易: 不强制导入 CircuitScope)
        scope_value = scope.value if hasattr(scope, "value") else str(scope)
        logger.info(json.dumps({
            "module_name": "tool_trace",
            "action": "circuit_event",
            "scope": scope_value,
            "session_id_hash": self.hash_content(session_id),
            "user_id_hash": self.hash_content(user_id),
            "tool_name": tool_name,
            "blocked": bool(blocked),
        }, ensure_ascii=False))

    def record_tool_retrieval(
        self,
        query: str,
        top_k: int,
        latency_ms: float,
        bm25_candidates: int,
        embed_candidates: int,
        fused_candidates: int,
        alpha: float,
        degraded: bool,
        tools_preview: list,
    ) -> None:
        """记录工具检索决策(结构化日志,不持久化到 SQLite)

        Why: 检索决策是轻量事件,沿用 record_tool_selection/record_circuit_event 风格,
             SQLite 只持久化 ToolTraceRecord(执行 trace)。
        """
        logger.info(json.dumps({
            "module_name": "tool_trace",
            "action": "tool_retrieval",
            "query_hash": self.hash_content(query),
            "top_k": top_k,
            "latency_ms": round(latency_ms, 2),
            "bm25_candidates": bm25_candidates,
            "embed_candidates": embed_candidates,
            "fused_candidates": fused_candidates,
            "alpha": alpha,
            "degraded": degraded,
            "tools_preview": tools_preview[:10],
        }, ensure_ascii=False))

    # ── 脱敏与危险检测 ────────────────────────────────────────

    def hash_content(self, data: Any) -> str:
        """计算内容的 SHA256 哈希(前 16 位),不存原文

        Args:
            data: 任意可序列化对象

        Returns:
            str: 16 位 hex 哈希
        """
        try:
            content = json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)
        except Exception:
            content = str(data)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def _is_dangerous(self, data: Any) -> bool:
        """检查输入是否匹配危险命令 critical 模式

        Args:
            data: 任意可序列化对象

        Returns:
            bool: 匹配到 critical 模式返回 True
        """
        try:
            content = json.dumps(data, ensure_ascii=False, default=str)
        except Exception:
            content = str(data)
        for pattern in self._critical_patterns:
            try:
                if pattern.search(content):
                    return True
            except Exception:
                continue
        return False

    def _load_critical_patterns(self) -> List[re.Pattern]:
        """从 data/dangerous_commands.json 加载 critical 模式正则"""
        patterns: List[re.Pattern] = []
        try:
            with open(_DANGEROUS_CMDS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("critical", []):
                try:
                    patterns.append(re.compile(item["pattern"]))
                except (re.error, KeyError):
                    continue
        except Exception as e:
            logger.warning(f"加载危险命令模式失败: {e}")
        return patterns

    # ── SQLite 持久化 ────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """获取线程本地 SQLite 连接"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
            self._local.conn = sqlite3.connect(
                self._db_path, check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self) -> None:
        """初始化 SQLite 表结构 + 索引"""
        conn = self._get_conn()
        with self._write_lock:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tool_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    output_hash TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    success INTEGER NOT NULL,
                    error_type TEXT DEFAULT '',
                    session_id TEXT DEFAULT '',
                    user_role TEXT DEFAULT 'guest',
                    timestamp REAL NOT NULL,
                    permission_decision TEXT DEFAULT ''
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_time "
                "ON tool_traces(tool_name, timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_time "
                "ON tool_traces(session_id, timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_success_time "
                "ON tool_traces(success, timestamp)"
            )
            conn.commit()

    def _write_to_db(self, records: List[ToolTraceRecord]) -> None:
        """批量写入 SQLite(失败则降级到 ring buffer)"""
        if not records:
            return
        if self._degraded:
            for r in records:
                self._fallback_ring_buffer.append(r)
            return
        try:
            conn = self._get_conn()
            with self._write_lock:
                conn.executemany(
                    "INSERT INTO tool_traces "
                    "(trace_id, tool_name, input_hash, output_hash, latency_ms, "
                    "success, error_type, session_id, user_role, timestamp, permission_decision) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (r.trace_id, r.tool_name, r.input_hash, r.output_hash,
                         r.latency_ms, int(r.success), r.error_type,
                         r.session_id, r.user_role, r.timestamp, r.permission_decision)
                        for r in records
                    ]
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"SQLite 批量写入失败,降级到 ring buffer: {e}")
            self._degraded = True
            for r in records:
                self._fallback_ring_buffer.append(r)

    def _writer_loop(self) -> None:
        """后台写入线程: 批量消费队列写 SQLite"""
        while not self._stopped:
            batch: List[ToolTraceRecord] = []
            try:
                # 阻塞等待第一条
                first = self._queue.get(timeout=WRITER_POLL_INTERVAL)
                batch.append(first)
                # 非阻塞批量获取更多(最多 WRITER_BATCH_SIZE)
                while len(batch) < WRITER_BATCH_SIZE:
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue_module.Empty:
                        break
            except queue_module.Empty:
                continue
            except Exception as e:
                logger.debug(f"writer_loop 取队异常: {e}")
                continue

            if batch:
                self._write_to_db(batch)

    def _row_to_record(self, row: sqlite3.Row) -> ToolTraceRecord:
        """SQLite 行转 ToolTraceRecord"""
        return ToolTraceRecord(
            trace_id=row["trace_id"],
            tool_name=row["tool_name"],
            input_hash=row["input_hash"],
            output_hash=row["output_hash"],
            latency_ms=row["latency_ms"],
            success=bool(row["success"]),
            error_type=row["error_type"] or "",
            session_id=row["session_id"] or "",
            user_role=row["user_role"] or "guest",
            timestamp=row["timestamp"],
            permission_decision=row["permission_decision"] or "",
        )

    def flush(self, timeout: float = 2.0) -> bool:
        """等待队列清空 + DB 写入完成(测试用)

        Args:
            timeout: 最大等待时间(秒)

        Returns:
            bool: 队列是否已清空
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._queue.empty():
                # 队列空,但 writer 线程可能正在写入 DB,等待一小段时间确保写入完成
                time.sleep(0.02)
                return True
            time.sleep(0.01)
        return self._queue.empty()

    def clear(self) -> None:
        """清空数据库表 + ring buffer(测试用)"""
        self._queue.queue.clear()
        self._fallback_ring_buffer.clear()
        if not self._degraded:
            try:
                conn = self._get_conn()
                with self._write_lock:
                    conn.execute("DELETE FROM tool_traces")
                    conn.commit()
            except Exception as e:
                logger.warning(f"清空 tool_traces 失败: {e}")


# ════════════════════════════════════════════════════════════
#  兼容入口: 复用现有 trace_id 生成方式
# ════════════════════════════════════════════════════════════

def _generate_trace_id() -> str:
    """生成 16 位十六进制 Trace ID

    Why: 与 agent/observability/tracer.py 的 generate_trace_id() 保持一致,
         避免引入新的 trace_id 格式
    """
    return uuid.uuid4().hex[:16]


__all__ = [
    "ToolTraceRecord",
    "ToolTraceRecorder",
    "HIGH_FREQ_TOOLS",
]
