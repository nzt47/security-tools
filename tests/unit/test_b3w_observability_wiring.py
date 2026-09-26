# -*- coding: utf-8 -*-
"""B3-W 接线收尾卡单测：三处"已就绪却无人调用"断点的接线，以及反例。

背景与分类依据：docs/audit_skill_governance/B3W_REPORT.md

【并发/污染注意】\`_HELPER_AVAILABLE\`、\`trh._hybrid_instance\`、
zero_recall_total(Counter 单调不可重置)、llm_cache 累计计数
**都是模块级状态**，测试之间会互相污染。
⇒ 本文件一律「读初值 → 断言增量」或「自己注入假单例并还原」，
   **不硬编码任何绝对值**。

【非空转】本文件对每条接线都配了"反例"：
若把接线删掉，对应的正向用例会立刻变红（见 B3W_REPORT.md §4 变异证据）。
"""
from __future__ import annotations

import logging

import pytest

import agent.tool_router_hybrid as trh
from agent.monitoring import prometheus as pm
from agent.observability import tool_trace as tt
from agent.orchestrator import routing_observability as ro

try:
    from prometheus_client import generate_latest
    _PROM_OK = True
except Exception:  # pragma: no cover
    _PROM_OK = False

pytestmark = pytest.mark.skipif(not _PROM_OK, reason="prometheus_client 不可用")


# ══════════════════════════════════════════════════════════════════
#  工具
# ══════════════════════════════════════════════════════════════════

_ZERO = "zero_recall_total"


def _metric_text() -> str:
    return generate_latest().decode("utf-8", "replace")


def _sample(name: str, text: str | None = None):
    """从 /metrics 同源载荷读一个无标签样本值（不存在 -> None）。"""
    if text is None:
        text = _metric_text()
    for line in text.splitlines():
        if line.startswith(name + " ") or line.startswith(name + "{"):
            try:
                return float(line.rsplit(" ", 1)[1])
            except Exception:
                return None
    return None


def _zero_recall_delta(fn):
    """自己建基线：读初值 → 执行 → 读末值 → 返回增量。"""
    before = _sample(_ZERO)
    fn()
    after = _sample(_ZERO)
    assert before is not None and after is not None, "zero_recall_total 必须已注册"
    return after - before


def _events(caplog, *actions):
    """从 caplog 取出指定 action 的结构化事件（log_dict 产出的是 dict）。"""
    out = []
    for rec in caplog.records:
        msg = rec.msg
        if isinstance(msg, dict) and msg.get("action") in actions:
            out.append(msg)
    return out


class _FakeRetriever:
    """确定性假检索器：只实现 hybrid_select_tools 真正会读的接口。"""

    def __init__(self, results=None, *, available=True, raise_on_query=False):
        self._results = results
        self.available = available
        self.degraded = False
        self._alpha = 0.5
        self._all_categories = set(trh.TOOL_CATEGORIES.keys())
        self._last_query_stats = {}
        self._raise = raise_on_query
        self.calls = 0

    def query(self, text, top_k=10):
        self.calls += 1
        if self._raise:
            raise RuntimeError("B3W 单测强制异常")
        return self._results


@pytest.fixture(autouse=True)
def _isolate():
    """隔离模块级状态：单例 + 排序函数 + 日志传播。"""
    saved_instance = getattr(trh, "_hybrid_instance", None)
    saved_sort = trh._apply_alias_merge_and_priority_sort
    saved_helper = trh._HELPER_AVAILABLE
    loggers = [logging.getLogger(n) for n in (
        "agent", "agent.observability.tool_trace", "agent.tool_router_hybrid",
        "agent.monitoring.prometheus", "agent.orchestrator")]
    saved_log = [(lg, lg.level, lg.propagate, lg.disabled) for lg in loggers]
    ro.RouteContext.clear()
    try:
        yield
    finally:
        trh._hybrid_instance = saved_instance
        trh._apply_alias_merge_and_priority_sort = saved_sort
        trh._HELPER_AVAILABLE = saved_helper
        ro.RouteContext.clear()
        for lg, lvl, prop, dis in saved_log:
            lg.setLevel(lvl)
            lg.propagate = prop
            lg.disabled = dis


def _inject(fake) -> None:
    trh._hybrid_instance = fake


def _enable_debug_logging():
    for n in ("agent", "agent.observability.tool_trace",
              "agent.tool_router_hybrid", "agent.monitoring.prometheus"):
        lg = logging.getLogger(n)
        lg.setLevel(logging.DEBUG)
        lg.propagate = True
        lg.disabled = False


# ══════════════════════════════════════════════════════════════════
#  一、断点① trace_id 关联
# ══════════════════════════════════════════════════════════════════

class TestTraceIdJoin:

    def test_record_tool_retrieval_写入routing_trace_id(self, caplog):
        """record_tool_retrieval 必须把 routing_observability 的 trace_id
        落进 trace_id_ctx（与 route decision 同名）。"""
        _enable_debug_logging()
        trace = "unit-trace-join-0001"
        ro.RouteContext.init(trace)
        try:
            with caplog.at_level(logging.INFO):
                tt.ToolTraceRecorder.instance().record_tool_retrieval(
                    query="单测查询", top_k=5, latency_ms=1.0,
                    bm25_candidates=0, embed_candidates=0, fused_candidates=0,
                    alpha=0.5, degraded=False, tools_preview=[],
                    trace_id=ro.current_trace_id())
        finally:
            ro.RouteContext.clear()

        evs = _events(caplog, "tool_retrieval")
        assert evs, "未产出 tool_retrieval 事件"
        assert evs[-1]["trace_id_ctx"] == trace

    def test_hybrid_select_tools调用点带上trace_id(self, caplog):
        """【接线点】hybrid_select_tools 的调用点必须传 trace_id。

        删掉调用点的 trace_id=_retrieval_trace_id() ⇒ 本用例变红。
        """
        _enable_debug_logging()
        if not trh._HELPER_AVAILABLE:
            pytest.skip("helper 不可用，走不到该分支")
        _inject(_FakeRetriever(results=[("read_pdf", 1.0)]))
        trace = "unit-trace-join-0002"
        ro.RouteContext.init(trace)
        try:
            with caplog.at_level(logging.INFO):
                trh.hybrid_select_tools("读取 pdf 文件的内容")
        finally:
            ro.RouteContext.clear()

        evs = _events(caplog, "tool_retrieval")
        assert evs, "未产出 tool_retrieval 事件"
        assert evs[-1]["trace_id_ctx"] == trace, (
            "检索决策日志未带上 route decision 的 trace_id ⇒ 两条日志无法串联")

    def test_无请求上下文时记为空串(self, caplog):
        """无 RouteContext 时不得抛错、不得记成随机 id。"""
        _enable_debug_logging()
        assert ro.current_trace_id() == ""
        with caplog.at_level(logging.INFO):
            tt.ToolTraceRecorder.instance().record_tool_retrieval(
                query="无上下文查询", top_k=5, latency_ms=1.0,
                bm25_candidates=0, embed_candidates=0, fused_candidates=0,
                alpha=0.5, degraded=False, tools_preview=[],
                trace_id=ro.current_trace_id())
        evs = _events(caplog, "tool_retrieval")
        assert evs and evs[-1]["trace_id_ctx"] == ""


# ══════════════════════════════════════════════════════════════════
#  二、断点② 零召回分类接线
# ══════════════════════════════════════════════════════════════════

class TestZeroRecallWiring:
    """语义铁律：zero_recall 只记「本来该召回却没召回」。"""

    def test_分类表显式且稳定(self):
        """reason 必须是稳定可枚举短标签，不得拼动态字符串。"""
        assert trh.ZERO_RECALL_REASONS == ("results_empty", "sort_empty")
        import inspect
        src = inspect.getsource(trh.hybrid_select_tools)
        for reason in ("helper_unavailable", "retriever_unavailable",
                       "results_none", "results_empty", "whitelist_empty",
                       "sort_empty"):
            assert '_note_retrieval_early_exit("%s")' % reason in src, reason

    # ── 计入：真正"该召回却没召回" ──────────────────────────────
    def test_结果为空_计入零召回(self, caplog):
        if not trh._HELPER_AVAILABLE:
            pytest.skip("helper 不可用")
        _enable_debug_logging()
        _inject(_FakeRetriever(results=[]))
        with caplog.at_level(logging.INFO):
            d = _zero_recall_delta(lambda: trh.hybrid_select_tools("空召回单测"))
        assert d == 1.0, "检索已执行且候选为 0 ⇒ 必须计入零召回"
        evs = _events(caplog, "tool.zero_recall")
        assert evs and evs[-1]["reason"] == "results_empty"

    def test_排序后为空_计入零召回(self, caplog, monkeypatch):
        if not trh._HELPER_AVAILABLE:
            pytest.skip("helper 不可用")
        _enable_debug_logging()
        _inject(_FakeRetriever(results=[("read_pdf", 1.0)]))
        monkeypatch.setattr(trh, "_apply_alias_merge_and_priority_sort",
                            lambda *a, **k: [])
        with caplog.at_level(logging.INFO):
            d = _zero_recall_delta(lambda: trh.hybrid_select_tools("排序空单测"))
        assert d == 1.0, "候选非空却被漏斗末端全丢 ⇒ 必须计入零召回"
        evs = _events(caplog, "tool.zero_recall")
        assert evs and evs[-1]["reason"] == "sort_empty"

    # ── 不计入：能力未就绪 / 正常回退 / 异常降级 ─────────────────
    @pytest.mark.parametrize("tag,reason,setup", [
        ("helper_unavailable", "helper_unavailable", "helper"),
        ("retriever_unavailable", "retriever_unavailable", "retriever"),
        ("results_none", "results_none", "none"),
        ("whitelist_empty", "whitelist_empty", "whitelist"),
    ])
    def test_非零召回分支_不得污染计数器(self, caplog, monkeypatch,
                                       tag, reason, setup):
        """能力未就绪 / 调用方白名单 / 检索失败 ⇒ 只留痕，**不得** +1。"""
        _enable_debug_logging()
        if setup == "helper":
            trh._HELPER_AVAILABLE = False
            call = lambda: trh.hybrid_select_tools("单测")  # noqa: E731
        elif setup == "retriever":
            # 必须把 get_hybrid_retriever 打成返回 None（而不是把单例置 None）
            # —— 后者会让 get_hybrid_retriever 走双重检查锁**重建**真实检索器。
            monkeypatch.setattr(trh, "get_hybrid_retriever", lambda: None)
            call = lambda: trh.hybrid_select_tools("单测")  # noqa: E731
        else:
            if not trh._HELPER_AVAILABLE:
                pytest.skip("helper 不可用")
            if setup == "none":
                _inject(_FakeRetriever(results=None))
                call = lambda: trh.hybrid_select_tools("单测")  # noqa: E731
            else:
                _inject(_FakeRetriever(results=[("read_pdf", 1.0)]))
                call = lambda: trh.hybrid_select_tools(  # noqa: E731
                    "单测", ["__b3w_no_such_tool__"])

        with caplog.at_level(logging.DEBUG):
            d = _zero_recall_delta(call)
        assert d == 0.0, "%s 不应计入 zero_recall_total（会污染指标）" % tag
        evs = _events(caplog, "tool.retrieval.early_exit")
        assert any(e.get("reason") == reason for e in evs), (
            "%s 分支必须留痕（early_exit 事件），不能静默" % tag)
        assert not _events(caplog, "tool.zero_recall"), (
            "%s 不得产出 tool.zero_recall 事件" % tag)

    def test_异常路径_不计入零召回(self, caplog):
        """异常是**降级**不是零召回；且已有 WARNING 留痕。"""
        _enable_debug_logging()
        _inject(_FakeRetriever(raise_on_query=True))
        with caplog.at_level(logging.DEBUG):
            d = _zero_recall_delta(lambda: trh.hybrid_select_tools("异常单测"))
        assert d == 0.0
        assert not _events(caplog, "tool.zero_recall")

    def test_正常命中_不计入零召回(self):
        if not trh._HELPER_AVAILABLE:
            pytest.skip("helper 不可用")
        _inject(_FakeRetriever(results=[("read_pdf", 1.0)]))
        d = _zero_recall_delta(lambda: trh.hybrid_select_tools("读取 pdf 文件的内容"))
        assert d == 0.0

    def test_零召回事件可归因到具体请求(self, caplog):
        """零召回事件必须带上与 route decision 同源的 trace_id。"""
        if not trh._HELPER_AVAILABLE:
            pytest.skip("helper 不可用")
        _enable_debug_logging()
        _inject(_FakeRetriever(results=[]))
        trace = "unit-trace-zero-0001"
        ro.RouteContext.init(trace)
        try:
            with caplog.at_level(logging.INFO):
                trh.hybrid_select_tools("零召回归因单测")
                ro.emit_route_decision("unit_layer", "hit", trace,
                                       message="[B3W 单测] route decision")
        finally:
            ro.RouteContext.clear()
        z = _events(caplog, "tool.zero_recall")
        rd = _events(caplog, "orchestrator.process.route_decision")
        assert z and z[-1]["trace_id_ctx"] == trace
        assert rd and rd[-1]["trace_id_ctx"] == trace


# ══════════════════════════════════════════════════════════════════
#  三、断点③ LLM 响应缓存命中率出口
# ══════════════════════════════════════════════════════════════════

class TestLLMResponseCacheMetrics:

    def test_指标名存在且类型正确(self):
        text = _metric_text()
        assert "llm_response_cache_hit_ratio" in text
        assert "# TYPE llm_response_cache_hit_ratio gauge" in text
        assert "# TYPE llm_response_cache_hits_total counter" in text
        assert "# TYPE llm_response_cache_misses_total counter" in text
        assert "# TYPE llm_response_cache_entries gauge" in text

    def test_读数等于真实缓存自报值(self):
        """读数必须来自真实 LLMResponseCache.get_stats()，不是另起的计数器。"""
        from agent.llm_response_cache import llm_cache
        import time as _t
        key = "b3w-unit-%d" % (_t.time_ns())
        llm_cache.put(key, "b3w-unit-value")
        assert llm_cache.get(key) == "b3w-unit-value"

        stats = llm_cache.get_stats()
        hits = int(stats["total_hits"])
        misses = int(stats["total_misses"])
        text = _metric_text()

        assert _sample("llm_response_cache_hits_total", text) == float(hits)
        assert _sample("llm_response_cache_misses_total", text) == float(misses)
        total = hits + misses
        assert _sample("llm_response_cache_hit_ratio", text) == pytest.approx(
            (hits / total) if total else 0.0)
        assert _sample("llm_response_cache_entries", text) == float(
            int(stats["cache_size"]))

    def test_命中后计数器真实增长(self):
        """自己建基线：再查一次同一 key ⇒ 命中计数必须增长。"""
        from agent.llm_response_cache import llm_cache
        import time as _t
        key = "b3w-unit-grow-%d" % (_t.time_ns())
        llm_cache.put(key, "v")
        llm_cache.get(key)
        before = _sample("llm_response_cache_hits_total")
        llm_cache.get(key)          # 再命中一次
        after = _sample("llm_response_cache_hits_total")
        assert before is not None and after is not None
        assert after >= before + 1.0, "命中次数未同步到 /metrics"

    def test_与前缀缓存指标口径分离(self):
        """llm_response_cache_* 与 cache_hit_ratio（服务端前缀缓存）是两码事。"""
        text = _metric_text()
        assert "cache_hit_ratio" in text
        assert "llm_response_cache_hit_ratio" in text
        assert pm.cache_hit_ratio is not None
