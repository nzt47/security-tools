"""TraceStore — 链路追踪内存存储

基于环形缓冲区的 Trace 存储，支持 Trace 生命周期管理和 Span 记录。
全局单例 trace_store。
"""

import os
import time
import logging
import json
import uuid
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from collections import deque
from threading import Lock

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



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
    """追踪记录

    spans 兼容两种形态：
    - TraceSpan 对象（由 TraceStore.add_span(trace_id, span) 写入，用于运行时追踪）
    - dict（由本类 add_span({...}) 写入，用于全链路回溯/演示场景，含 service/events 等扩展字段）
    """
    trace_id: str
    input: str = ""
    output: str = ""
    spans: List[Any] = field(default_factory=list)
    start_time: float = 0.0
    end_time: Optional[float] = None
    status: str = "active"
    duration_ms: Optional[float] = None

    def add_span(self, span: Any) -> None:
        """追加一个 span 到本 Trace。

        入参可为 dict（全链路回溯场景，含 span_id/service/operation/events 等键）
        或 TraceSpan 对象（运行时追踪场景）。直接 append 到 spans 列表，不做类型强约束，
        以兼容两种调用约定。
        """
        self.spans.append(span)

    def get_total_duration(self) -> float:
        """计算 Trace 总耗时（毫秒）。

        优先用 spans 的 start/end 时间跨度推算；spans 为空时回退到 duration_ms。
        兼容 dict 与 TraceSpan 两种 span 形态。
        """
        if not self.spans:
            return self.duration_ms or 0.0
        starts: List[float] = []
        ends: List[float] = []
        for s in self.spans:
            if isinstance(s, dict):
                st = s.get('start_time')
                en = s.get('end_time')
            else:
                st = getattr(s, 'start_time', None)
                en = getattr(s, 'end_time', None)
            if st is not None:
                starts.append(st)
            if en is not None:
                ends.append(en)
        if not starts or not ends:
            return self.duration_ms or 0.0
        return (max(ends) - min(starts)) * 1000.0


class TraceStore:
    """内存 Trace 存储（环形缓冲区）

    可选 storage_path 启用文件持久化：save_trace 写入 JSON 文件，load_trace 优先内存、
    回退文件。无 storage_path 时仅内存模式（保持向后兼容）。
    """

    def __init__(self, max_traces: int = 1000, storage_path: Optional[str] = None):
        self._traces: Dict[str, TraceRecord] = {}
        self._order: deque = deque(maxlen=max_traces)
        self._lock = Lock()
        self._max_traces = max_traces
        self._storage_path = storage_path

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
    def storage_path(self) -> Optional[str]:
        """持久化目录路径（None 表示纯内存模式）"""
        return self._storage_path

    def save_trace(self, record: TraceRecord) -> None:
        """保存 Trace 记录：写入内存缓存，并在配置了 storage_path 时持久化到 JSON 文件。

        持久化失败仅记录日志、不抛异常（best-effort），保证主流程不受磁盘问题影响。
        """
        trace_id = record.trace_id
        with self._lock:
            self._traces[trace_id] = record
            self._order.append(trace_id)
            while len(self._traces) > self._max_traces:
                oldest = self._order.popleft()
                self._traces.pop(oldest, None)
        if not self._storage_path:
            return
        try:
            os.makedirs(self._storage_path, exist_ok=True)
            file_path = os.path.join(self._storage_path, f"{trace_id}.json")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(self._serialize_record(record), f, ensure_ascii=False)
        except OSError as exc:
            logger.warning("save_trace 持久化失败 trace_id=%s: %s", trace_id, exc)

    def load_trace(self, trace_id: str) -> Optional[TraceRecord]:
        """加载 Trace 记录：优先从内存取，未命中且配置了 storage_path 时回退到 JSON 文件。

        从文件加载时 spans 保持 dict 形态（用于全链路回溯场景的 span.get('events') 等访问）。
        """
        with self._lock:
            record = self._traces.get(trace_id)
        if record is not None:
            return record
        if not self._storage_path:
            return None
        file_path = os.path.join(self._storage_path, f"{trace_id}.json")
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("load_trace 加载失败 trace_id=%s: %s", trace_id, exc)
            return None
        return TraceRecord(
            trace_id=data.get("trace_id", trace_id),
            input=data.get("input", ""),
            output=data.get("output", ""),
            start_time=data.get("start_time", 0.0),
            end_time=data.get("end_time"),
            status=data.get("status", "active"),
            duration_ms=data.get("duration_ms"),
            spans=data.get("spans", []),
        )

    @staticmethod
    def _span_to_dict(span: Any) -> Dict:
        """把 span 统一转为可 JSON 序列化的 dict（兼容 dict 与 TraceSpan 入参）"""
        if isinstance(span, dict):
            return span
        return {
            "span_id": span.span_id,
            "operation": span.operation,
            "start_time": span.start_time,
            "end_time": span.end_time,
            "duration_ms": span.duration_ms,
            "status": span.status,
            "metadata": getattr(span, "metadata", {}),
        }

    def _serialize_record(self, record: TraceRecord) -> Dict:
        """把 TraceRecord 序列化为 dict（spans 统一转 dict 便于 JSON 持久化与回溯访问）"""
        return {
            "trace_id": record.trace_id,
            "input": getattr(record, "input", ""),
            "output": getattr(record, "output", ""),
            "start_time": getattr(record, "start_time", 0.0),
            "end_time": getattr(record, "end_time", None),
            "status": getattr(record, "status", "active"),
            "duration_ms": getattr(record, "duration_ms", None),
            "spans": [self._span_to_dict(s) for s in record.spans],
        }

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


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "subscriber",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
