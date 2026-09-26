"""B3 六指标单元测试（P0 验收地基）

对应任务卡 B3 与 docs/audit_skill_governance/AUDIT_AND_PLAN.md §3 F2/F3 行。

覆盖范围:
1. 六个指标名**真实注册进默认 REGISTRY**（这是 /metrics 能看到名字的前提，
   只 new 一个 Counter 而不落进 REGISTRY 是本卡明令排除的伪交付）
2. route_depth / route_depth_histogram / route_duration_ms 由
   routing_observability.emit_route_decision 写入（每次请求恰好一次）
3. usage 真值解析：DeepSeek 前缀缓存字段 / OpenAI prompt_tokens_details /
   Anthropic / 无 usage / usage 全 0 / 响应被转成字符串
4. llm_tokens_total{kind} / llm_cost_usd_total / cache_hit_ratio 的数值正确性，
   以及「API 未上报缓存字段 → 不污染命中率分母」这条不变量
5. tool_selected_total 由 llm_monitor.create_from_api_call 按本轮 tools 计数
6. zero_recall_total 的记录函数可用（**调用点尚未接线**，见文件末尾的已知缺口守卫）
7. 既有指标不回归（yunshu_intent_layer_total 仍可写）

【不易】本文件全部使用**增量断言**（before/after 差值），不依赖测试执行顺序
（本仓启用了 pytest-randomly，顺序不保证）。
"""
from __future__ import annotations

import os

import pytest
from prometheus_client import REGISTRY, generate_latest

import agent.monitoring.prometheus as pm
from agent.llm_monitor import LLMMonitor
from agent.orchestrator import routing_observability as ro

SIX_METRICS = [
    "route_depth",
    "zero_recall_total",
    "tool_selected_total",
    "llm_tokens_total",
    "llm_cost_usd_total",
    "cache_hit_ratio",
]


# ════════════════════════════════════════════════════════════
#  测试用桩：极简的 pydantic 风格响应对象
# ════════════════════════════════════════════════════════════

class _Obj:
    """属性载体（模拟 openai SDK 的响应对象：属性访问而非 dict 下标）"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Msg:
    def __init__(self, content="", tool_calls=None, reasoning_content=""):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning_content


class _Choice:
    def __init__(self, message):
        self.message = message


def _resp(usage, content="ok", tool_calls=None):
    """构造一个带 usage 的 OpenAI 兼容响应"""
    return _Obj(choices=[_Choice(_Msg(content=content, tool_calls=tool_calls))],
                usage=usage)


def _sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {})


def _delta(name, labels, fn):
    """执行 fn 并返回样本值增量"""
    before = _sample(name, labels) or 0.0
    fn()
    after = _sample(name, labels) or 0.0
    return after - before


# ════════════════════════════════════════════════════════════
#  1. 指标真的注册进默认 REGISTRY（/metrics 的前提）
# ════════════════════════════════════════════════════════════

class TestMetricsAreRegistered:

    def test_六个指标名出现在_exporter_输出(self):
        """六指标名必须能被 generate_latest 输出（= curl /metrics 能看到）"""
        # 先各写一次，保证有 label 的指标（tool_selected_total）产生样本行
        pm.record_route(3, 12.5)
        pm.record_zero_recall("unit_test_warmup")
        pm.record_tool_selected([{"function": {"name": "_b3_warmup_tool"}}])
        pm.record_llm_usage(
            {"available": True, "prompt_tokens": 10, "completion_tokens": 2,
             "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10,
             "cache_reported": True, "reasoning_tokens": 0})

        text = generate_latest(REGISTRY).decode("utf-8")
        for name in SIX_METRICS:
            assert name in text, "指标名 %s 未出现在 exporter 输出中" % name

    def test_route_depth_的分布视图也注册(self):
        """route_depth_histogram / route_duration_ms 是 route_depth 的同源视图"""
        text = generate_latest(REGISTRY).decode("utf-8")
        assert "route_depth_histogram_count" in text
        assert "route_duration_ms_count" in text

    def test_无标签指标的样本可直读(self):
        assert _sample("route_depth") is not None
        assert _sample("zero_recall_total") is not None
        assert _sample("llm_cost_usd_total") is not None
        assert _sample("cache_hit_ratio") is not None


# ════════════════════════════════════════════════════════════
#  2. route_depth / route_duration_ms 由 emit_route_decision 写入
# ════════════════════════════════════════════════════════════

class TestRouteDepthMetric:

    @pytest.fixture(autouse=True)
    def _clean_ctx(self):
        ro.RouteContext.clear()
        yield
        ro.RouteContext.clear()

    def test_路由深度等于该请求累计的层数(self):
        """route_depth = RouteContext.layers 条数（一次请求走了几层路由）"""
        ro.RouteContext.init("b3-test-trace")
        ro.log_layer_result(ro.LAYER_INPUT_GUARD, ro.DECISION_PASS, "b3-test-trace",
                            duration_ms=0.1)
        ro.log_layer_result(ro.LAYER_WORKFLOW, ro.DECISION_MISS, "b3-test-trace",
                            duration_ms=0.2, level=10)
        ro.log_layer_result(ro.LAYER_TEMPLATE, ro.DECISION_HIT, "b3-test-trace",
                            duration_ms=0.3)

        hist_before = _sample("route_depth_histogram_count") or 0.0
        ro.emit_route_decision(ro.LAYER_TEMPLATE, "hit", "b3-test-trace")

        assert _sample("route_depth") == 3.0
        assert (_sample("route_depth_histogram_count") or 0.0) - hist_before == 1.0

    def test_路由耗时被观测为直方图(self):
        """E1 三指标先手：duration_ms 暴露为 Histogram（P95 用）"""
        ro.RouteContext.init("b3-test-trace-2")
        ro.log_layer_result(ro.LAYER_LLM, ro.DECISION_SUCCESS, "b3-test-trace-2",
                            duration_ms=1.0)
        before = _sample("route_duration_ms_count") or 0.0
        ro.emit_route_decision(ro.LAYER_LLM, "success", "b3-test-trace-2")
        assert (_sample("route_duration_ms_count") or 0.0) - before == 1.0
        assert _sample("route_duration_ms_sum") > 0

    def test_单层请求深度为1_无上下文为0(self):
        ro.RouteContext.init("b3-test-trace-3")
        ro.log_layer_result(ro.LAYER_REJECT, ro.DECISION_REJECT, "b3-test-trace-3")
        ro.emit_route_decision(ro.LAYER_REJECT, "reject", "b3-test-trace-3")
        assert _sample("route_depth") == 1.0

        ro.RouteContext.clear()
        ro.emit_route_decision(ro.LAYER_LLM, "success", "")
        assert _sample("route_depth") == 0.0

    def test_current_trace_id_同上下文内可关联(self):
        """B3 §4.2 的关联键来源：工具侧补 trace_id 时应取本函数"""
        assert ro.current_trace_id() == ""
        ro.RouteContext.init("b3-trace-join")
        assert ro.current_trace_id() == "b3-trace-join"
        ro.RouteContext.clear()
        assert ro.current_trace_id() == ""


# ════════════════════════════════════════════════════════════
#  3. usage 真值解析
# ════════════════════════════════════════════════════════════

class TestExtractUsage:

    def test_deepseek前缀缓存字段(self):
        """审计 E10 实测字段：prompt_cache_hit_tokens / prompt_cache_miss_tokens"""
        u = LLMMonitor.extract_usage(_resp(_Obj(
            prompt_tokens=6888, completion_tokens=120,
            prompt_cache_hit_tokens=256, prompt_cache_miss_tokens=6632,
            total_tokens=7008)))
        assert u["available"] is True
        assert u["prompt_tokens"] == 6888
        assert u["completion_tokens"] == 120
        assert u["prompt_cache_hit_tokens"] == 256
        assert u["prompt_cache_miss_tokens"] == 6632
        assert u["cache_reported"] is True

    def test_只有hit字段时miss用差值补齐(self):
        u = LLMMonitor.extract_usage(_resp(_Obj(
            prompt_tokens=1000, completion_tokens=10, prompt_cache_hit_tokens=960)))
        assert u["prompt_cache_hit_tokens"] == 960
        assert u["prompt_cache_miss_tokens"] == 40

    def test_openai_details形态(self):
        u = LLMMonitor.extract_usage(_resp(_Obj(
            prompt_tokens=500, completion_tokens=20,
            prompt_tokens_details=_Obj(cached_tokens=128),
            completion_tokens_details=_Obj(reasoning_tokens=64))))
        assert u["prompt_cache_hit_tokens"] == 128
        assert u["prompt_cache_miss_tokens"] == 372
        assert u["cache_reported"] is True
        assert u["reasoning_tokens"] == 64

    def test_anthropic形态(self):
        u = LLMMonitor.extract_usage(_Obj(usage=_Obj(
            input_tokens=300, output_tokens=40, cache_read_input_tokens=200)))
        assert u["prompt_tokens"] == 300
        assert u["completion_tokens"] == 40
        assert u["prompt_cache_hit_tokens"] == 200
        assert u["prompt_cache_miss_tokens"] == 100

    def test_未上报缓存字段时cache_reported为False(self):
        """不变量：区分「0 命中」与「不报缓存」——否则命中率分母被污染"""
        u = LLMMonitor.extract_usage(_resp(_Obj(prompt_tokens=400, completion_tokens=8)))
        assert u["available"] is True
        assert u["cache_reported"] is False
        assert u["prompt_cache_hit_tokens"] == 0

    def test_无usage或字符串响应(self):
        assert LLMMonitor.extract_usage(None)["available"] is False
        assert LLMMonitor.extract_usage("纯文本响应")["available"] is False
        assert LLMMonitor.extract_usage(_Obj(choices=[]))["available"] is False

    def test_usage全0视为未上报(self):
        u = LLMMonitor.extract_usage(_resp(_Obj(prompt_tokens=0, completion_tokens=0)))
        assert u["available"] is False

    def test_dict形态的usage同样可解析(self):
        u = LLMMonitor.extract_usage(
            {"usage": {"prompt_tokens": 10, "completion_tokens": 1,
                       "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10}})
        assert u["available"] is True
        assert u["cache_reported"] is True


# ════════════════════════════════════════════════════════════
#  4. usage → 指标（token / 成本 / 命中率）
# ════════════════════════════════════════════════════════════

class TestUsageMetrics:

    @pytest.fixture(autouse=True)
    def _reset_ratio(self):
        pm.reset_llm_cache_counters()
        yield
        pm.reset_llm_cache_counters()

    def _usage(self, **kw):
        base = {"available": True, "prompt_tokens": 0, "completion_tokens": 0,
                "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0,
                "cache_reported": True, "reasoning_tokens": 0}
        base.update(kw)
        return base

    def test_token按kind分别累计(self):
        u = self._usage(prompt_tokens=1000, completion_tokens=200,
                        prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=200)
        d_prompt = _delta("llm_tokens_total", {"kind": "prompt"},
                          lambda: pm.record_llm_usage(u))
        assert d_prompt == 1000.0
        assert _sample("llm_tokens_total", {"kind": "completion"}) >= 200.0
        assert _sample("llm_tokens_total", {"kind": "cached"}) >= 800.0

    def test_cached是prompt的子集(self):
        """kind=prompt 是输入总量，cached ⊂ prompt（口径写进 help 文本）"""
        u = self._usage(prompt_tokens=500, completion_tokens=0,
                        prompt_cache_hit_tokens=500, prompt_cache_miss_tokens=0)
        r = pm.record_llm_usage(u)
        assert r["prompt_tokens"] == 500
        assert r["cached_tokens"] == 500
        assert r["call_cache_hit_ratio"] == 1.0

    def test_本次与累计命中率(self):
        r1 = pm.record_llm_usage(self._usage(
            prompt_tokens=1000, completion_tokens=0,
            prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1000))
        assert r1["call_cache_hit_ratio"] == 0.0
        assert r1["cumulative_cache_hit_ratio"] == 0.0
        r2 = pm.record_llm_usage(self._usage(
            prompt_tokens=1000, completion_tokens=0,
            prompt_cache_hit_tokens=960, prompt_cache_miss_tokens=40))
        assert r2["call_cache_hit_ratio"] == 0.96
        # 累计口径 = Σhit/(Σhit+Σmiss) = 960/(960+1000+40) = 0.48
        # （分子分母都累计：只累计分母会把"命中"算漏，是把命中率做低的经典错误）
        assert abs(r2["cumulative_cache_hit_ratio"] - 0.48) < 1e-6
        assert abs(_sample("cache_hit_ratio") - 0.48) < 1e-6

    def test_未上报缓存时不改命中率分母(self):
        pm.record_llm_usage(self._usage(
            prompt_tokens=1000, completion_tokens=0,
            prompt_cache_hit_tokens=500, prompt_cache_miss_tokens=500))
        before = _sample("cache_hit_ratio")
        pm.record_llm_usage({"available": True, "prompt_tokens": 9999,
                             "completion_tokens": 0, "cache_reported": False})
        assert _sample("cache_hit_ratio") == before
        # 但 prompt token 仍然要计（输入是事实，不因缓存未上报而消失）
        assert _sample("llm_tokens_total", {"kind": "prompt"}) >= 9999.0

    def test_成本按价目表累计且可用env覆盖(self, monkeypatch):
        u = self._usage(prompt_tokens=0, completion_tokens=1_000_000,
                        prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0)
        monkeypatch.setenv("CP_LLM_PRICE_OUTPUT_USD_PER_MTOK", "2.0")
        r = pm.record_llm_usage(u)
        # 1,000,000 输出 token × $2.0/1M = $2.0
        assert abs(r["cost_usd"] - 2.0) < 1e-6
        # 非法值必须回退默认（0.60），不得抛异常
        monkeypatch.setenv("CP_LLM_PRICE_OUTPUT_USD_PER_MTOK", "not-a-number")
        r2 = pm.record_llm_usage(u)
        assert abs(r2["cost_usd"] - 0.60) < 1e-6

    def test_缓存命中token按较低单价计(self, monkeypatch):
        monkeypatch.setenv("CP_LLM_PRICE_INPUT_USD_PER_MTOK", "1.0")
        monkeypatch.setenv("CP_LLM_PRICE_CACHED_USD_PER_MTOK", "0.1")
        r = pm.record_llm_usage(self._usage(
            prompt_tokens=1_000_000, completion_tokens=0,
            prompt_cache_hit_tokens=1_000_000, prompt_cache_miss_tokens=0))
        # 全部命中缓存 ⇒ 1M × $0.1/1M = $0.1
        assert abs(r["cost_usd"] - 0.1) < 1e-6


# ════════════════════════════════════════════════════════════
#  5. tool_selected_total（工具被选中并下发）
# ════════════════════════════════════════════════════════════

class TestToolSelected:

    def test_按本轮下发的tools计数(self):
        tools = [{"type": "function", "function": {"name": "b3_tool_a"}},
                 {"type": "function", "function": {"name": "b3_tool_a"}},
                 {"type": "function", "function": {"name": "b3_tool_b"}}]
        d = _delta("tool_selected_total", {"tool": "b3_tool_a"},
                   lambda: pm.record_tool_selected(tools))
        assert d == 2.0
        assert (_sample("tool_selected_total", {"tool": "b3_tool_b"}) or 0.0) >= 1.0

    def test_支持裸字符串与无名元素(self):
        n = pm.record_tool_selected(["b3_bare_tool", {"function": {}}, None,
                                     {"name": "b3_plain_name"}])
        assert n == 2
        assert _sample("tool_selected_total", {"tool": "b3_bare_tool"}) >= 1.0
        assert _sample("tool_selected_total", {"tool": "b3_plain_name"}) >= 1.0

    def test_空列表不计数(self):
        assert pm.record_tool_selected([]) == 0
        assert pm.record_tool_selected(None) == 0

    def test_create_from_api_call_同时计usage与tool_selected(self):
        """端到端接线：llm_monitor.create_from_api_call 是三条出网路径的共同收口"""
        tools = [{"type": "function", "function": {"name": "b3_e2e_tool"}}]
        resp = _resp(_Obj(prompt_tokens=2000, completion_tokens=50,
                          prompt_cache_hit_tokens=1500,
                          prompt_cache_miss_tokens=500,
                          total_tokens=2050), content="回答")
        rec = LLMMonitor.create_from_api_call(
            system_prompt="sys", messages=[{"role": "user", "content": "hi"}],
            tools=tools, response_obj=resp, model="deepseek-flash",
            provider="openai", source="tool_calling")

        assert rec.usage_available is True
        assert rec.prompt_cache_hit_tokens == 1500
        assert rec.prompt_cache_miss_tokens == 500
        assert rec.cache_reported is True
        assert rec.call_cache_hit_ratio == 0.75
        assert rec.usage_cost_usd > 0
        assert _sample("tool_selected_total", {"tool": "b3_e2e_tool"}) >= 1.0

    def test_字符串响应不产生usage指标_也不双计(self):
        """_patched_do_chat 会把**内层已计量**的响应转成字符串再记一次 ⇒ 必须不计数"""
        before = _sample("llm_tokens_total", {"kind": "prompt"}) or 0.0
        rec = LLMMonitor.create_from_api_call(
            messages=[{"role": "user", "content": "hi"}],
            response_obj="这是一段字符串响应", source="chat")
        assert rec.usage_available is False
        assert (_sample("llm_tokens_total", {"kind": "prompt"}) or 0.0) == before


# ════════════════════════════════════════════════════════════
#  6. zero_recall_total（记录函数可用；调用点见已知缺口守卫）
# ════════════════════════════════════════════════════════════

class TestZeroRecall:

    def test_记录函数计数并输出结构化事件(self, caplog):
        import logging as _logging
        with caplog.at_level(_logging.INFO, logger="agent.monitoring.prometheus"):
            d = _delta("zero_recall_total", None,
                       lambda: pm.record_zero_recall("results_empty",
                                                     query_hash="deadbeef"))
        assert d == 1.0
        assert any("tool.zero_recall" in str(getattr(r, "msg", ""))
                   or "tool.zero_recall" in str(getattr(r, "message", ""))
                   for r in caplog.records), "零召回必须留结构化事件（不能静默）"

    def test_缺省reason不抛异常(self):
        d = _delta("zero_recall_total", None, lambda: pm.record_zero_recall())
        assert d == 1.0


# ════════════════════════════════════════════════════════════
#  7. 不回归既有指标
# ════════════════════════════════════════════════════════════

class TestNoRegression:

    def test_既有意图层指标仍可写(self):
        d = _delta("yunshu_intent_layer_total", {"layer": "b3_regression"},
                   lambda: pm.record_intent_layer("b3_regression"))
        assert d == 1.0

    def test_既有技能指标仍注册(self):
        text = generate_latest(REGISTRY).decode("utf-8")
        assert "yunshu_intent_layer_ratio" in text
        assert "context_assembler_duration_ms_count" in text

    def test_六指标不覆盖既有名字(self):
        """六个新名字与既有 23 个指标名无交集（改前清单见 B3.md §3）"""
        existing = {
            "python_gc_collections_total", "python_gc_objects_collected_total",
            "python_gc_objects_uncollectable_total", "python_info",
            "yunshu_active_connections", "yunshu_cpu_usage_percent",
            "yunshu_exporter_info", "yunshu_http_request_created",
            "yunshu_http_request_duration_seconds_bucket",
            "yunshu_http_request_duration_seconds_count",
            "yunshu_http_request_duration_seconds_created",
            "yunshu_http_request_duration_seconds_sum",
            "yunshu_http_request_total", "yunshu_memory_usage_percent",
            "context_assembler_degraded_created",
            "context_assembler_degraded_total",
            "context_assembler_duration_ms_bucket",
            "context_assembler_duration_ms_count",
            "context_assembler_duration_ms_created",
            "context_assembler_duration_ms_sum",
            "context_assembler_injected_created",
            "context_assembler_injected_tokens",
            "context_assembler_injected_total",
        }
        assert not (set(SIX_METRICS) & existing)


# ════════════════════════════════════════════════════════════
#  8. 已知缺口守卫（B3 交付边界，见 B3.md §4）
# ════════════════════════════════════════════════════════════

class TestKnownGapGuard:
    """【B3-W 已翻转】本类原为 B3 的「已知缺口守卫」，断言
    tool_router_hybrid.py 里**没有** record_zero_recall（B3 该文件不在允许
    文件集内）。B3-W 卡已接线 ⇒ 按原测试 docstring 的指示翻转为
    「断言接线确实存在」。分类结论见 docs/audit_skill_governance/B3W_REPORT.md §1。
    """

    def test_零召回埋点已接线_B3W(self):
        """接线守卫：早退分支必须留痕，且只有"零召回"两个分支计入计数器。"""
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(root, "agent", "tool_router_hybrid.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        assert "_note_retrieval_early_exit" in src, (
            "零召回/早退埋点被移除 —— B3-W 接线已回退")
        # 7 条早退分支全部留痕（6 条 return None + except 分支的既有 WARNING）
        for reason in ("helper_unavailable", "retriever_unavailable",
                       "results_none", "results_empty", "whitelist_empty",
                       "sort_empty"):
            assert '_note_retrieval_early_exit("%s")' % reason in src, reason
        # 语义铁律：只有"本来该召回却没召回"的两个分支计入 zero_recall_total
        assert "ZERO_RECALL_REASONS = (\"results_empty\", \"sort_empty\")" in src
        assert src.count("return None") >= 5

    def test_零召回计数器在空召回时真的动(self):
        """端到端：强制空召回 ⇒ zero_recall_total +1（F3 原始验收项）。"""
        import agent.tool_router_hybrid as trh
        from prometheus_client import generate_latest

        def _v():
            text = generate_latest().decode("utf-8", "replace")
            for line in text.splitlines():
                if line.startswith("zero_recall_total "):
                    return float(line.rsplit(" ", 1)[1])
            return None

        if not trh._HELPER_AVAILABLE:
            pytest.skip("helper 不可用，走不到检索分支")
        saved = getattr(trh, "_hybrid_instance", None)
        trh._hybrid_instance = _FakeEmptyRetriever()
        try:
            before = _v()
            trh.hybrid_select_tools("B3 守卫：强制空召回")
            after = _v()
        finally:
            trh._hybrid_instance = saved
        assert before is not None and after is not None
        assert after - before == 1.0


class _FakeEmptyRetriever:
    """只实现 hybrid_select_tools 会读的接口，query 恒返回空。"""

    def __init__(self):
        import agent.tool_router_hybrid as _t
        self.available = True
        self.degraded = False
        self._alpha = 0.5
        self._all_categories = set(_t.TOOL_CATEGORIES.keys())
        self._last_query_stats = {}

    def query(self, text, top_k=10):
        return []
