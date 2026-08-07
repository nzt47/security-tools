"""knowledge 结构化日志埋点单元测试（observability.py）。

验收线（不易）：
- trace_id 为完整 UUID4（128 bit / 32 hex），分布式多节点聚合不冲突。
- emit_structured_log 以 dict 作为 msg 输出，生产过滤器（DictToJsonFilter）
  序列化后为单行 JSON，顶层字段可被 Filebeat/Promtail 直接解析。
"""
import io
import json
import logging

from agent.knowledge.observability import _trace_id, emit_structured_log


def test_trace_id_full_uuid4_128bit():
    tid = _trace_id()
    assert len(tid) == 32  # 32 hex = 128 bit
    int(tid, 16)  # 必须为合法 hex


def test_emit_produces_parseable_json_line():
    # 模拟生产文件 handler：DictToJsonFilter 把 dict msg 序列化为单行 JSON
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    from agent.logging_utils import DictToJsonFilter
    handler.addFilter(DictToJsonFilter())

    lg = logging.getLogger("agent.knowledge")
    old_handlers = list(lg.handlers)
    old_propagate = lg.propagate
    try:
        lg.handlers = [handler]
        lg.propagate = False
        emit_structured_log("distill.llm_failed", level="warning",
                            duration_ms=50.31, source="a.md",
                            reason="error", error="超时")
    finally:
        lg.handlers = old_handlers
        lg.propagate = old_propagate

    data = json.loads(buf.getvalue().strip())  # 顶层 JSON 直接可解析
    assert data["module_name"] == "knowledge"
    assert data["action"] == "distill.llm_failed"
    assert data["duration_ms"] == 50.31
    assert data["reason"] == "error"
    assert len(data["trace_id"]) == 32
