"""任务6：主业务链路路由可观测性埋点单元测试

覆盖模块：agent/orchestrator/routing_observability.py
- log_layer_result 四字段契约（trace_id_ctx/layer/decision/duration_ms）
- 日志级别约定（命中/终态 INFO、中间未命中 DEBUG、拦截 WARNING）
- RouteTraffic 流量计数与每 N 次请求 INFO 汇总
- RouteContext 中间结果累积与请求结束清空
- emit_route_decision 最终路由决策（final_layer/layer_results/decision_basis）
- 埋点失败隔离（任何异常不阻断主链路）
"""
import logging

import pytest

from agent.orchestrator import routing_observability as ro
from agent.orchestrator.routing_observability import (
    log_layer_result,
    emit_route_decision,
    RouteTraffic,
    RouteContext,
    LAYER_INPUT_GUARD, LAYER_WORKFLOW, LAYER_TEMPLATE, LAYER_SEMANTIC, LAYER_LLM,
    LAYER_OUTPUT_GUARD, LAYER_REJECT,
    DECISION_PASS, DECISION_HIT, DECISION_MISS, DECISION_BLOCK,
    DECISION_MODIFIED, DECISION_SUCCESS, DECISION_ERROR, DECISION_REJECT,
)


@pytest.fixture(autouse=True)
def _isolate_state():
    """每个测试前重置流量计数与路由上下文，避免跨测试污染"""
    RouteTraffic.reset()
    RouteContext.clear()
    yield
    RouteTraffic.reset()
    RouteContext.clear()


# ════════════════════════════════════════════════════════════════
#  TestLogLayerResult: 统一层日志入口（四字段契约 + 级别约定）
# ════════════════════════════════════════════════════════════════

class TestLogLayerResult:
    def test_四字段契约_必含(self, caplog):
        # 四字段契约: trace_id_ctx/layer/decision/duration_ms 必含
        RouteContext.init("trace-001")
        with caplog.at_level(logging.INFO):
            log_layer_result(LAYER_SEMANTIC, DECISION_HIT, "trace-001",
                             duration_ms=12.345)
        payload = caplog.records[-1].getMessage()
        assert "trace-001" in payload
        assert "'layer': 'semantic'" in payload
        assert "'decision': 'hit'" in payload
        assert "'duration_ms': 12.3" in payload  # 保留 2 位

    def test_命中_终态级别INFO_未命中DEBUG(self, caplog):
        # 级别约定: 命中/终态 INFO、中间未命中 DEBUG
        RouteContext.init("trace-002")
        with caplog.at_level(logging.DEBUG):
            log_layer_result(LAYER_WORKFLOW, DECISION_HIT, "trace-002",
                             message="命中")
            log_layer_result(LAYER_WORKFLOW, DECISION_MISS, "trace-002",
                             level=logging.DEBUG, message="未命中")
        levels = [r.levelno for r in caplog.records]
        assert logging.INFO in levels
        assert logging.DEBUG in levels

    def test_拦截WARNING(self, caplog):
        # 拦截（InputGuard BLOCK）→ WARNING
        RouteContext.init("trace-003")
        with caplog.at_level(logging.WARNING):
            log_layer_result(LAYER_INPUT_GUARD, DECISION_BLOCK, "trace-003",
                             level=logging.WARNING, message="拦截")
        assert caplog.records[-1].levelno == logging.WARNING

    def test_action_缺省自动组装(self, caplog):
        # action 缺省时自动组装 orchestrator.layer.<layer>.<decision>
        RouteContext.init("trace-004")
        with caplog.at_level(logging.INFO):
            log_layer_result(LAYER_LLM, DECISION_ERROR, "trace-004")
        assert "orchestrator.layer.llm.error" in caplog.records[-1].getMessage()

    def test_自定义action与附加字段(self, caplog):
        # 自定义 action + 附加字段（workflow_id 等）透传
        RouteContext.init("trace-005")
        with caplog.at_level(logging.INFO):
            log_layer_result(LAYER_WORKFLOW, DECISION_HIT, "trace-005",
                             action="orchestrator.wfl.hit",
                             workflow_id="wf_1", score=0.8765)
        msg = caplog.records[-1].getMessage()
        assert "orchestrator.wfl.hit" in msg
        assert "'workflow_id': 'wf_1'" in msg
        assert "'score': 0.8765" in msg


# ════════════════════════════════════════════════════════════════
#  TestRouteTraffic: 流量分布计数与每 N 次请求汇总
# ════════════════════════════════════════════════════════════════

class TestRouteTraffic:
    def test_attempts_hits计数(self):
        # 命中决策计 hit，未命中只计 attempt
        log_layer_result(LAYER_SEMANTIC, DECISION_HIT, "t",
                         level=logging.DEBUG)
        log_layer_result(LAYER_SEMANTIC, DECISION_MISS, "t",
                         level=logging.DEBUG)
        snap = RouteTraffic.snapshot()
        assert snap[LAYER_SEMANTIC]["attempts"] == 2
        assert snap[LAYER_SEMANTIC]["hits"] == 1

    def test_每N次请求INFO汇总(self, caplog):
        # 每 N 次请求输出占比汇总（N 默认 50）
        RouteTraffic.reset()
        with caplog.at_level(logging.INFO):
            for i in range(50):
                RouteContext.init(f"req-{i}")
        assert any("orchestrator.traffic.summary" in r.getMessage()
                   for r in caplog.records)

    def test_请求数清零(self):
        RouteContext.init("r1")
        RouteContext.init("r2")
        RouteTraffic.reset()
        assert RouteTraffic.snapshot() == {}


# ════════════════════════════════════════════════════════════════
#  TestRouteContext: 中间结果累积与请求结束清空
# ════════════════════════════════════════════════════════════════

class TestRouteContext:
    def test_init与get(self):
        RouteContext.init("ctx-1")
        assert RouteContext.get().trace_id == "ctx-1"

    def test_clear清空(self):
        RouteContext.init("ctx-2")
        RouteContext.clear()
        assert RouteContext.get() is None

    def test_add_layer累积(self):
        RouteContext.init("ctx-3")
        log_layer_result(LAYER_TEMPLATE, DECISION_HIT, "ctx-3",
                         duration_ms=3.0, score=0.9)
        ctx = RouteContext.get()
        assert "template" in ctx.layers
        assert ctx.layers["template"]["outcome"] == DECISION_HIT
        assert ctx.layers["template"]["score"] == 0.9

    def test_并发隔离(self):
        # ContextVar 按协程/线程隔离，互不污染
        import threading
        RouteContext.init("main")
        results = []

        def worker():
            RouteContext.init("sub")
            results.append(RouteContext.get().trace_id)

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert results == ["sub"]
        assert RouteContext.get().trace_id == "main"


# ════════════════════════════════════════════════════════════════
#  TestEmitRouteDecision: 最终路由决策日志
# ════════════════════════════════════════════════════════════════

class TestEmitRouteDecision:
    def test_包含final_layer与layer_results(self, caplog):
        RouteContext.init("dec-1")
        log_layer_result(LAYER_WORKFLOW, DECISION_MISS, "dec-1",
                         level=logging.DEBUG)
        log_layer_result(LAYER_SEMANTIC, DECISION_HIT, "dec-1",
                         score=0.85)
        with caplog.at_level(logging.INFO):
            emit_route_decision(LAYER_SEMANTIC, DECISION_HIT, "dec-1",
                                message="[语义层] 命中",
                                basis_extra={"score": 0.85})
        msg = caplog.records[-1].getMessage()
        assert "orchestrator.process.route_decision" in msg
        assert "'final_layer': 'semantic'" in msg
        assert "'layer_results'" in msg
        assert "workflow" in msg  # 中间结果可还原完整链路

    def test_无上下文时不报错(self, caplog):
        # 【不易】RouteContext 未初始化也能安全输出（layer_results 为空）
        with caplog.at_level(logging.INFO):
            emit_route_decision(LAYER_LLM, DECISION_SUCCESS, "dec-2")
        assert "'layer_results': {}" in caplog.records[-1].getMessage()

    def test_耗时自init起(self, caplog):
        import time
        RouteContext.init("dec-3")
        time.sleep(0.01)
        with caplog.at_level(logging.INFO):
            emit_route_decision(LAYER_LLM, DECISION_SUCCESS, "dec-3")
        msg = caplog.records[-1].getMessage()
        # duration_ms 字段存在且非 None
        assert "'duration_ms':" in msg
        assert "None" not in msg


# ════════════════════════════════════════════════════════════════
#  TestFailIsolation: 埋点失败隔离
# ════════════════════════════════════════════════════════════════

class TestFailIsolation:
    def test_log_layer_result_异常不向上传播(self, caplog, monkeypatch):
        # 日志接口本身抛异常 → 降级 DEBUG，不抛给调用方
        def boom(*a, **kw):
            raise RuntimeError("formatter broken")
        monkeypatch.setattr(ro, "log_dict", boom)
        with caplog.at_level(logging.DEBUG):
            log_layer_result(LAYER_LLM, DECISION_HIT, "t")  # 不应抛异常

    def test_emit_route_decision_异常不向上传播(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("formatter broken")
        monkeypatch.setattr(ro, "log_dict", boom)
        emit_route_decision(LAYER_LLM, DECISION_SUCCESS, "t")  # 不应抛异常
