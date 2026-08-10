"""knowledge 结构化日志埋点单元测试（observability.py）。

验收线（不易）：
- trace_id 为完整 UUID4（128 bit / 32 hex），分布式多节点聚合不冲突。
- emit_structured_log 以 dict 作为 msg 输出，生产过滤器（DictToJsonFilter）
  序列化后为单行 JSON，顶层字段可被 Filebeat/Promtail 直接解析。
- knowledge_trace 上下文管理器：同一链路内 emit 共享 trace_id、显式传参
  优先、退出后恢复、并发线程 ContextVar 隔离（链路追踪完整性）。
"""
import contextlib
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.knowledge.observability import (
    _trace_id,
    emit_structured_log,
    get_trace_id,
    knowledge_trace,
)


@contextlib.contextmanager
def _capture_json_logs():
    """临时接管 agent.knowledge logger：dict msg → 单行 JSON，产出可解析行。"""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    from agent.logging_utils import DictToJsonFilter
    handler.addFilter(DictToJsonFilter())

    lg = logging.getLogger("agent.knowledge")
    old_handlers, old_propagate, old_level = list(lg.handlers), lg.propagate, lg.level
    try:
        lg.handlers = [handler]
        lg.propagate = False
        lg.setLevel(logging.INFO)
        yield buf
    finally:
        lg.handlers = old_handlers
        lg.propagate = old_propagate
        lg.setLevel(old_level)


def test_trace_id_full_uuid4_128bit():
    tid = _trace_id()
    assert len(tid) == 32  # 32 hex = 128 bit
    int(tid, 16)  # 必须为合法 hex


@pytest.mark.serial
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


@pytest.mark.serial
def test_knowledge_trace_shares_id_in_chain():
    """同一链路内多次 emit 共享 trace_id（ELK 按 trace_id 聚合整条链路）。"""
    with _capture_json_logs() as buf:
        with knowledge_trace() as tid:
            assert get_trace_id() == tid
            emit_structured_log("distill.llm_ok", slug="a")
            emit_structured_log("promote.card_ok", slug="a")
            emit_structured_log("kb_search.ok", query="q")
        rows = [json.loads(l) for l in buf.getvalue().splitlines()]
    assert len(rows) == 3
    assert {r["trace_id"] for r in rows} == {tid}
    assert {r["action"] for r in rows} == {
        "distill.llm_ok", "promote.card_ok", "kb_search.ok"}


@pytest.mark.serial
def test_knowledge_trace_explicit_param_priority():
    """显式 trace_id 传参优先于链路上下文。"""
    with _capture_json_logs() as buf:
        with knowledge_trace() as tid:
            emit_structured_log("distill.llm_ok", slug="a")
            emit_structured_log("distill.llm_failed", level="warning",
                                trace_id="x" * 32, reason="error")
        rows = [json.loads(l) for l in buf.getvalue().splitlines()]
    assert rows[0]["trace_id"] == tid      # 未传参 → 链路 id
    assert rows[1]["trace_id"] == "x" * 32  # 显式传参 → 优先


def test_knowledge_trace_restore_after_exit():
    """退出 knowledge_trace 后 ContextVar 恢复原值（无泄漏）。"""
    assert get_trace_id() == ""  # 基线：未在链路内
    with knowledge_trace("chain-001"):
        assert get_trace_id() == "chain-001"
    assert get_trace_id() == ""  # 退出后恢复


def test_knowledge_trace_no_leak_on_exception():
    """异常中断场景：yield 内抛异常，finally 仍恢复原值（不泄露上下文）。"""
    assert get_trace_id() == ""
    with pytest.raises(RuntimeError, match="boom"):
        with knowledge_trace("chain-002"):
            assert get_trace_id() == "chain-002"
            raise RuntimeError("boom")
    assert get_trace_id() == ""  # 异常传播后上下文已恢复


def test_knowledge_trace_nested_restore_on_exception():
    """嵌套链路 + 内层异常：恢复内层之前的链路段（token 精确复位）。"""
    with knowledge_trace("outer"):
        with pytest.raises(RuntimeError):
            with knowledge_trace("inner"):
                assert get_trace_id() == "inner"
                raise RuntimeError("inner boom")
        assert get_trace_id() == "outer"  # 内层中断后回到外层链路
    assert get_trace_id() == ""


@pytest.mark.serial
def test_knowledge_trace_concurrent_isolation():
    """线程并发：ContextVar 隔离，各线程链路 trace_id 不串扰。"""
    with _capture_json_logs() as buf:
        def worker(tid: str) -> None:
            with knowledge_trace(tid):
                emit_structured_log("distill.llm_ok", slug=f"note-{tid}")

        with ThreadPoolExecutor(max_workers=10) as pool:
            pool.map(worker, [f"node-{i}" for i in range(10)])
        rows = [json.loads(l) for l in buf.getvalue().splitlines()]

    tids = {r["trace_id"] for r in rows}
    assert len(tids) == 10  # 10 条链路互不串扰
    assert tids == {f"node-{i}" for i in range(10)}
