"""TraceStore — 链路追踪内存存储

基于环形缓冲区的 Trace 存储，支持 Trace 生命周期管理和 Span 记录。
全局单例 trace_store。
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from collections import deque
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class TraceSpan:
    """操作跨度（一个 Trace 内的子操作）"""
    span_id: str
    operation: str
    start_time: float
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    status: str = "unknown"
    metadata: Dict = field(default_factory=dict)


@dataclass
class TraceRecord:
    """追踪记录"""
    trace_id: str
    input: str = ""
    output: str = ""
    spans: List[TraceSpan] = field(default_factory=list)
    start_time: float = 0.0
    end_time: Optional[float] = None
    status: str = "active"
    duration_ms: Optional[float] = None


class TraceStore:
    """内存 Trace 存储（环形缓冲区）"""

    def __init__(self, max_traces: int = 1000):
        self._traces: Dict[str, TraceRecord] = {}
        self._order: deque = deque(maxlen=max_traces)
        self._lock = Lock()
        self._max_traces = max_traces

    def start_trace(self, trace_id: str, user_input: str = "") -> TraceRecord:
        """开始一条新的 Trace"""
        with self._lock:
            record = TraceRecord(
                trace_id=trace_id,
                input=user_input,
                start_time=time.time(),
            )
            self._traces[trace_id] = record
            self._order.append(trace_id)
            # 超出容量时淘汰最旧
            while len(self._traces) > self._max_traces:
                oldest = self._order.popleft()
                self._traces.pop(oldest, None)
            return record

    def end_trace(self, trace_id: str, output: str = "", status: str = "success"):
        """结束一条 Trace"""
        with self._lock:
            record = self._traces.get(trace_id)
            if not record:
                return
            record.end_time = time.time()
            record.duration_ms = (record.end_time - record.start_time) * 1000
            record.output = output
            record.status = status

    def add_span(self, trace_id: str, span: TraceSpan):
        """向指定 Trace 添加 Span"""
        with self._lock:
            record = self._traces.get(trace_id)
            if record:
                record.spans.append(span)

    def get_trace(self, trace_id: str) -> Optional[TraceRecord]:
        """按 ID 获取 Trace"""
        return self._traces.get(trace_id)

    def get_recent(self, n: int = 10) -> List[TraceRecord]:
        """获取最近 N 条 Trace"""
        with self._lock:
            ids = list(self._order)[-n:]
            return [self._traces[tid] for tid in ids if tid in self._traces]

    def query(self, status: Optional[str] = None,
              min_duration: Optional[float] = None,
              max_duration: Optional[float] = None) -> List[TraceRecord]:
        """按条件查询 Trace"""
        with self._lock:
            results = []
            for tid in self._order:
                record = self._traces.get(tid)
                if not record:
                    continue
                if status and record.status != status:
                    continue
                if min_duration is not None and (record.duration_ms or 0) < min_duration:
                    continue
                if max_duration is not None and (record.duration_ms or 0) > max_duration:
                    continue
                results.append(record)
            return results

    def delete_trace(self, trace_id: str) -> bool:
        """删除指定 Trace"""
        with self._lock:
            if trace_id in self._traces:
                del self._traces[trace_id]
                return True
            return False

    def clear(self):
        """清空所有 Trace"""
        with self._lock:
            self._traces.clear()
            self._order.clear()

    @property
    def count(self) -> int:
        return len(self._traces)

    @property
    def stats(self) -> Dict:
        with self._lock:
            durations = [r.duration_ms for r in self._traces.values()
                         if r.duration_ms is not None]
            return {
                "total": len(self._traces),
                "active": sum(1 for r in self._traces.values() if r.status == "active"),
                "avg_duration_ms": sum(durations) / len(durations) if durations else 0,
                "max_traces": self._max_traces,
            }


# 全局单例
trace_store = TraceStore()
