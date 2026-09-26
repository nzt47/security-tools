"""C2 背压三件套单测：HTTP 入口并发闸门 / 工具层并发配对 / 确认级低容量桶 / 回滚开关

覆盖（全部**离线**，不真起 20 个 HTTP 连接；真实并发压测见
`docs/audit_skill_governance/C2.md` 的「压测证据」节）：

    1. 并发上限生效：并发 N > 上限 ⇒ 超出的被**拒绝**（有界等待，不是无限等待）
    2. release 配对：异常路径（handler 抛异常 / 未知工具 / 应用抛异常）额度都归还，
       连跑 M 次后仍能重新占满上限（额度回到初值，无泄漏）
    3. 排队超时：等待超过阈值 ⇒ 429 + `error_code=SERVER_BUSY_TIMEOUT`（可识别拒绝）
    4. 流式响应：额度持有到**响应迭代结束/close**（不是视图返回就归还）
    5. 豁免路径（/api/health 等）不被闸门影响（A1 就绪门 / 心跳不被误伤）
    6. 回归：正常工具调用行为与限流报错文案一字未变；分类桶取值未变
    7. 回滚开关：置 0 ⇒ 回到改动前行为
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from flask import Flask, jsonify

from agent import tools
from agent.rate_limiter import (
    CONFIRM_LEVEL_LIMITS,
    ConcurrencyGate,
    ConcurrencyGateMiddleware,
    RateLimiter,
    build_http_gate_from_env,
    tool_limiter_from_env,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ════════════════════════════════════════════════════════════
#  夹具 / 工具函数
# ════════════════════════════════════════════════════════════


def _gated_app(gate: ConcurrencyGate, *, sleep: float = 0.0) -> Flask:
    """造一个把真实中间件装上的最小 Flask 应用（与 app_server.py 的装法一致）"""
    app = Flask("c2_backpressure_probe")
    app.testing = False

    @app.route("/api/chat", methods=["POST"])
    def _chat():
        if sleep:
            time.sleep(sleep)
        return jsonify({"ok": True, "slept": sleep})

    @app.route("/api/health")
    def _health():
        return jsonify({"ok": True, "path": "health"})

    @app.route("/boom")
    def _boom():
        raise RuntimeError("视图故意抛异常（验证额度归还）")

    app.wsgi_app = ConcurrencyGateMiddleware(app.wsgi_app, gate)
    return app


def _environ(path: str) -> dict:
    return {"PATH_INFO": path, "REQUEST_METHOD": "GET", "SERVER_NAME": "localhost",
            "SERVER_PORT": "80", "wsgi.version": (1, 0), "wsgi.url_scheme": "http",
            "wsgi.input": None, "wsgi.errors": None, "wsgi.multithread": True,
            "wsgi.multiprocess": False, "wsgi.run_once": False}


def _collector():
    status: dict = {}
    return status, (lambda s, h: status.update(status=s, headers=dict(h)))


@pytest.fixture(autouse=True)
def _tool_gate_off(monkeypatch):
    """工具调用用例：关掉审批门（与 tests/unit/test_injection_end_to_end.py 同一基线），
    本文件测的是**背压**，不是确认门。"""
    monkeypatch.setenv("CP_TOOL_GATE_APPROVAL_ENFORCE", "0")
    monkeypatch.delenv("CP_TOOL_GATE_ENABLED", raising=False)
    monkeypatch.delenv("CP_GUARDRAILS_GUARD_TOOL", raising=False)


@pytest.fixture
def probe_tools():
    """注册/清理探针工具（唯一来源名，退出时逐个注销，不污染全局注册表）"""
    source = "c2_backpressure_probe"
    registered: list = []

    def _register(name: str, handler):
        tools.register(name, "C2 probe", handler=handler, source=source)
        registered.append(name)
        return name

    yield _register
    for name in registered:
        tools.unregister(name)


# ════════════════════════════════════════════════════════════
#  1. 并发上限生效（闸门层）
# ════════════════════════════════════════════════════════════


class TestGateConcurrencyLimit:
    def test_saturated_gate_rejects_bounded_instead_of_waiting_forever(self):
        """占满上限后，下一个 acquire 在 queue_timeout 内**返回拒绝**（不是无限等）"""
        gate = ConcurrencyGate(max_concurrent=2, queue_timeout=0.3)
        assert gate.acquire("/api/chat").allowed
        assert gate.acquire("/api/chat").allowed

        t0 = time.monotonic()
        decision = gate.acquire("/api/chat")
        waited = time.monotonic() - t0

        assert decision.allowed is False
        assert decision.reason == "timeout"
        assert 0.25 <= waited < 3.0, "等待必须约等于 queue_timeout（有界），实测 %.2fs" % waited
        snap = gate.snapshot()
        assert snap["in_flight"] == 2 and snap["rejected_timeout"] == 1

    def test_released_slot_is_reusable(self):
        """归还后额度可复用（上限不是"一次用完就永久堵死"）"""
        gate = ConcurrencyGate(max_concurrent=1, queue_timeout=0.2)
        assert gate.acquire("/x").allowed is True
        assert gate.acquire("/x").allowed is False
        gate.release()
        assert gate.acquire("/x").allowed is True
        gate.release()
        assert gate.snapshot()["in_flight"] == 0

    def test_zero_timeout_never_queues(self):
        """queue_timeout<=0 ⇒ 不排队，拿不到额度立即拒"""
        gate = ConcurrencyGate(max_concurrent=1, queue_timeout=0)
        gate.acquire("/x")
        t0 = time.monotonic()
        assert gate.acquire("/x").allowed is False
        assert time.monotonic() - t0 < 0.2

    def test_max_queue_immediate_reject(self):
        """max_queue 打开时：等待队列满 ⇒ 立即拒绝（不做 5s 空等）"""
        gate = ConcurrencyGate(max_concurrent=1, queue_timeout=5.0, max_queue=1)
        gate.acquire("/x")                      # 占住执行额度
        holder = threading.Event()
        seen: list = []

        def _waiter():
            t0 = time.monotonic()
            d = gate.acquire("/x")              # 占住唯一的等待位
            seen.append((d.allowed, time.monotonic() - t0))
            holder.wait(2.0)

        th = threading.Thread(target=_waiter)
        th.start()
        time.sleep(0.15)                        # 等它进等待队列
        t0 = time.monotonic()
        d = gate.acquire("/x")
        fast = time.monotonic() - t0
        holder.set()
        th.join(3.0)
        assert d.allowed is False and d.reason == "queue_full"
        assert fast < 0.5, "max_queue 生效时必须立即拒（实测 %.2fs）" % fast

    def test_unpaired_release_does_not_inflate_capacity(self):
        """多还一次**不会**把额度虚增（配对错误只记账 + 告警，不破坏守恒）"""
        gate = ConcurrencyGate(max_concurrent=1, queue_timeout=0.1)
        gate.acquire("/x")
        gate.release()
        gate.release()                          # 未配对的多余归还
        snap = gate.snapshot()
        assert snap["in_flight"] == 0
        assert snap["unpaired_releases"] == 1
        assert gate.acquire("/x").allowed is True   # 上限仍是 1，未被虚增
        assert gate.acquire("/x").allowed is False


# ════════════════════════════════════════════════════════════
#  2. release 配对（异常 / 流式 / 应用抛异常）
# ════════════════════════════════════════════════════════════


class TestReleasePairing:
    def test_app_exception_releases_slot(self):
        """应用自身抛异常（WSGI 调用期）⇒ 额度立刻归还"""
        def _exploding_app(environ, start_response):
            raise RuntimeError("app boom")

        gate = ConcurrencyGate(max_concurrent=2, queue_timeout=0.1)
        mw = ConcurrencyGateMiddleware(_exploding_app, gate)
        for _ in range(5):
            with pytest.raises(RuntimeError):
                mw(_environ("/api/chat"), lambda s, h: None)
        assert gate.snapshot()["in_flight"] == 0

    def test_streaming_slot_held_until_close(self):
        """流式响应：额度持有到迭代结束/close（视图返回即归还 = 闸门管不住流式请求）"""
        def _streaming_app(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/event-stream")])

            def _gen():
                for _i in range(3):
                    yield b"data: x\n\n"

            return _gen()

        gate = ConcurrencyGate(max_concurrent=1, queue_timeout=0.1)
        mw = ConcurrencyGateMiddleware(_streaming_app, gate)
        status: dict = {}
        it = mw(_environ("/api/chat"), lambda s, h: status.update(status=s))
        assert next(it) == b"data: x\n\n"
        assert gate.snapshot()["in_flight"] == 1, "响应体没迭代完 ⇒ 额度必须还held住"
        assert gate.acquire("/api/chat").allowed is False
        it.close()
        assert gate.snapshot()["in_flight"] == 0
        assert gate.acquire("/api/chat").allowed is True

    def test_full_iteration_releases_slot(self):
        """正常迭代到 StopIteration ⇒ 额度归还"""
        def _app(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"a", b"b"]

        gate = ConcurrencyGate(max_concurrent=1, queue_timeout=0.1)
        mw = ConcurrencyGateMiddleware(_app, gate)
        body = b"".join(mw(_environ("/api/chat"), lambda s, h: None))
        assert body == b"ab"
        assert gate.snapshot()["in_flight"] == 0

    def test_flask_exception_path_returns_500_and_releases(self):
        """Flask 视图抛异常（500 响应）连跑 12 次：额度每次都回到 0"""
        gate = ConcurrencyGate(max_concurrent=2, queue_timeout=0.1)
        app = _gated_app(gate)
        client = app.test_client()
        # 【为什么显式 close】Flask 的 test_client **不会**替你排空/关闭应用的 WSGI
        #   迭代器（实测：不 close 时 in_flight 恒为 1）——真实服务器 waitress 会，
        #   见 waitress/task.py 的收尾 close()。这里补上这一步，等价于服务器的行为。
        for _i in range(12):
            resp = client.get("/boom")
            assert resp.status_code == 500
            assert gate.snapshot()["in_flight"] == 1, "未 close 前额度仍应被持有"
            resp.close()
            assert gate.snapshot()["in_flight"] == 0
        snap = gate.snapshot()
        assert snap["unpaired_releases"] == 0
        # 额度未泄漏 ⇒ 仍能占满上限
        assert gate.acquire("/api/chat").allowed is True
        assert gate.acquire("/api/chat").allowed is True
        assert gate.acquire("/api/chat").allowed is False


# ════════════════════════════════════════════════════════════
#  3. 排队超时（HTTP 层，可识别的 429）
# ════════════════════════════════════════════════════════════


class TestQueueTimeoutHttp:
    def test_second_request_gets_429_after_queue_timeout(self):
        """上限 1 + 视图 0.6s：并发的第 2 个请求等满 0.25s ⇒ 429（而不是一直等/被静默排队）"""
        gate = ConcurrencyGate(max_concurrent=1, queue_timeout=0.25,
                               exempt_prefixes=("/api/health",))
        app = _gated_app(gate, sleep=0.6)
        out: dict = {}

        def _worker(key: str):
            client = app.test_client()
            t0 = time.monotonic()
            resp = client.post("/api/chat")
            out[key] = {
                "status": resp.status_code,
                "elapsed": time.monotonic() - t0,
                "json": resp.get_json(silent=True),
                "retry_after": resp.headers.get("Retry-After"),
            }

        first = threading.Thread(target=_worker, args=("first",))
        first.start()
        time.sleep(0.1)                     # 保证 first 已拿到唯一额度
        second = threading.Thread(target=_worker, args=("second",))
        second.start()
        first.join(10)
        second.join(10)

        assert out["first"]["status"] == 200
        assert out["second"]["status"] == 429
        body = out["second"]["json"]
        assert body["error_code"] == "SERVER_BUSY_TIMEOUT"
        assert body["limit"] == 1 and body["gate"] == "http"
        assert out["second"]["retry_after"] == "1"
        # 排队被**上界**约束：约等于 queue_timeout，而不是无限等
        assert 0.2 <= out["second"]["elapsed"] < 0.55, out["second"]["elapsed"]

    def test_exempt_path_not_gated(self):
        """健康端点豁免：额度被占满时仍然 200（否则 A1 就绪门/心跳会被打红）"""
        gate = ConcurrencyGate(max_concurrent=1, queue_timeout=0.1,
                               exempt_prefixes=("/api/health",))
        app = _gated_app(gate)
        assert gate.acquire("/api/chat").allowed is True   # 占满唯一额度
        client = app.test_client()
        assert client.get("/api/health").status_code == 200
        assert client.post("/api/chat").status_code == 429
        assert gate.snapshot()["exempt"] == 1

    def test_exempt_request_does_not_release_others_slot(self):
        """豁免请求**完整走完**（真实服务器会 close/迭代完）也不得归还别人的额度

        【为什么必须有这条】Flask test_client 不排空响应体，会掩盖这个缺陷：
        实测未修复前服务 1 分钟产生 473 条"未配对的 release"，
        闸门被反复放水 ⇒ 8 条长轮询占不住 8 个额度，21 并发全部 200、0 个 429。
        """
        def _ok_app(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]

        gate = ConcurrencyGate(max_concurrent=1, queue_timeout=0.1,
                               exempt_prefixes=("/api/health",))
        mw = ConcurrencyGateMiddleware(_ok_app, gate)
        assert gate.acquire("/api/chat").allowed is True        # 占满唯一额度
        status, sink = _collector()
        body = b"".join(mw(_environ("/api/health"), sink))      # 豁免请求完整迭代
        assert body == b"ok"
        snap = gate.snapshot()
        assert snap["in_flight"] == 1, "豁免请求放掉了别人的额度"
        assert snap["unpaired_releases"] == 0
        assert gate.acquire("/api/chat").allowed is False, "额度被豁免请求放水，闸门失效"

    def test_reject_body_is_valid_json_and_identifiable(self):
        """拒绝响应是合法 JSON + Retry-After 头（客户端可识别、可退避）"""
        gate = ConcurrencyGate(max_concurrent=1, queue_timeout=0)
        gate.acquire("/api/chat")
        status, sink = _collector()
        mw = ConcurrencyGateMiddleware(lambda e, s: [b""], gate)
        body = b"".join(mw(_environ("/api/chat"), sink))
        assert status["status"].startswith("429")
        payload = json.loads(body.decode("utf-8"))
        assert payload["ok"] is False
        assert payload["error_code"] in ("SERVER_BUSY_TIMEOUT", "SERVER_BUSY")
        assert "Retry-After" in status["headers"]


# ════════════════════════════════════════════════════════════
#  4. 工具层并发闸门（acquire/release 成对）
# ════════════════════════════════════════════════════════════


class TestToolLayerConcurrency:
    def test_limiter_level_acquire_release(self):
        lim = RateLimiter(max_concurrent=2, concurrency_gate=True)
        assert lim.acquire_concurrent() is True
        assert lim.acquire_concurrent() is True
        assert lim.acquire_concurrent() is False
        lim.release()
        assert lim.acquire_concurrent() is True
        lim.release()
        lim.release()
        assert lim.get_status()["current_concurrent"] == 0

    def test_default_instance_keeps_legacy_behaviour(self):
        """默认构造（既有实例/既有单测）**不占**并发额度：acquire 恒 True 且计数为 0"""
        lim = RateLimiter()
        assert lim.concurrency_gate is False
        assert lim.acquire_concurrent() is True
        assert lim.get_status()["current_concurrent"] == 0

    def test_call_rejects_when_tool_concurrency_exhausted(self, monkeypatch, probe_tools):
        called: list = []
        probe_tools("c2_probe_saturated", lambda **kw: called.append(1) or {"ok": True})
        lim = RateLimiter(max_concurrent=2, concurrency_gate=True, level_buckets=False)
        monkeypatch.setattr(tools, "_rate_limiter", lim)
        assert lim.acquire_concurrent() and lim.acquire_concurrent()   # 占满

        out = tools.call("c2_probe_saturated")

        assert out == {"ok": False, "error": "工具并发调用过多，请稍后重试", "retry_after": 1.0}
        assert called == [], "被并发闸门拒绝的调用**不得**执行 handler"
        lim.release()
        lim.release()

    def test_release_paired_on_handler_exception(self, monkeypatch, probe_tools):
        def _boom(**kw):
            raise ValueError("handler boom")

        probe_tools("c2_probe_boom", _boom)
        # 分类桶给足令牌（100）：本用例要测的是**并发额度**的配对，
        # 桶容量默认 (10,1.0) 会在第 11 次先挡住（实测踩到：第 11 次起返回
        # "调用频率过高"，看起来像并发泄漏，其实不是）。
        lim = RateLimiter(limits={"default": (100, 10.0)},
                          max_concurrent=2, concurrency_gate=True, level_buckets=False)
        monkeypatch.setattr(tools, "_rate_limiter", lim)

        for _i in range(25):
            with pytest.raises(tools.ToolError):
                tools.call("c2_probe_boom")
            assert lim.get_status()["current_concurrent"] == 0, "异常路径泄漏了并发额度"

    def test_release_paired_on_unknown_tool_and_normal_path(self, monkeypatch, probe_tools):
        """未知工具（raise）/ 正常返回：额度都回到初值；连跑 M 次仍能整批占满"""
        probe_tools("c2_probe_ok", lambda **kw: {"ok": True})
        lim = RateLimiter(limits={"default": (100, 10.0)},
                          max_concurrent=2, concurrency_gate=True, level_buckets=False)
        monkeypatch.setattr(tools, "_rate_limiter", lim)

        for _i in range(25):
            with pytest.raises(tools.ToolError):
                tools.call("c2_probe_not_registered")
            assert lim.get_status()["current_concurrent"] == 0
            assert tools.call("c2_probe_ok") == {"ok": True}
            assert lim.get_status()["current_concurrent"] == 0

        # 额度回到初值 ⇒ 还能整批占满
        assert lim.acquire_concurrent() and lim.acquire_concurrent()
        assert lim.acquire_concurrent() is False
        lim.release()
        lim.release()

    def test_normal_call_behaviour_unchanged(self, monkeypatch, probe_tools):
        """回归：正常单请求行为不变（返回值逐字一致 + 额度不残留）"""
        probe_tools("c2_probe_echo", lambda **kw: {"ok": True, "echo": kw.get("text")})
        lim = tool_limiter_from_env()          # 生产构造（闸门开）
        monkeypatch.setattr(tools, "_rate_limiter", lim)
        assert tools.call("c2_probe_echo", text="hi") == {"ok": True, "echo": "hi"}
        assert lim.get_status()["current_concurrent"] == 0

    def test_rate_limit_rejection_copy_unchanged(self, monkeypatch, probe_tools):
        """回归：限流报错文案与字段一字未变，且速率判定仍**先于**并发闸门"""
        probe_tools("c2_probe_copy", lambda **kw: {"ok": True})

        class _DenyLimiter:
            concurrency_gate = True

            def check(self, _name):
                return False

            def wait_time(self, _name):
                return 2.5

            def acquire_concurrent(self):      # 走到这里就是顺序错了
                raise AssertionError("速率判定失败时不应再去占并发额度")

        monkeypatch.setattr(tools, "_rate_limiter", _DenyLimiter())
        out = tools.call("c2_probe_copy")
        assert out == {"ok": False, "error": "调用频率过高，请稍后重试", "retry_after": 2.5}


# ════════════════════════════════════════════════════════════
#  5. 确认级低容量桶（L2/L3）与分类桶回归
# ════════════════════════════════════════════════════════════


class TestConfirmLevelBuckets:
    def test_l3_tool_gets_low_capacity(self):
        lim = RateLimiter(level_buckets=True)
        assert CONFIRM_LEVEL_LIMITS["L3"] == (2, 0.1)
        assert [lim.check("shell_execute") for _ in range(3)] == [True, True, False]
        assert lim.wait_time("shell_execute") == 10.0     # 1/0.1，retry_after 不再骗人

    def test_l2_tool_bucket(self):
        lim = RateLimiter(level_buckets=True)
        assert CONFIRM_LEVEL_LIMITS["L2"] == (5, 0.5)
        results = [lim.check("write_file") for _ in range(7)]
        assert results[:5] == [True] * 5 and results[5:] == [False, False]

    def test_l0_tool_untouched_by_level_bucket(self):
        """L0/L1 只走分类桶（file=(15,1.0)）⇒ 前 15 次放行，第 16 次拒"""
        lim = RateLimiter(level_buckets=True)
        results = [lim.check("read_file") for _ in range(16)]
        assert results[:15] == [True] * 15 and results[15] is False

    def test_category_bucket_unchanged_without_switch(self):
        """回归：未开确认级桶的实例 = 改动前行为（分类桶内联实现未变）"""
        lim = RateLimiter()
        assert [lim.check("shell_execute") for _ in range(3)] == [True, True, False]
        assert [lim.check("http_get") for _ in range(2)] == [True, True]

    def test_category_token_refunded_when_level_bucket_rejects(self, monkeypatch):
        """确认级桶拒绝 ⇒ **退回**分类桶令牌

        证法：把 L3 容量压到 1，分类桶给 3 个令牌（无补充）。
          · 第 1 次：分类 3→2、L3 1→0 ⇒ 放行
          · 第 2 次：分类 2→1、L3 拒 ⇒ 退回到 2 ⇒ 拒绝
        随后把 L3 放宽再连打：若能放行 **3** 次才用尽，说明第 2 次确实退回了令牌
        （不退回的话只剩 1 个令牌，放行 1 次就被分类桶挡住）。
        """
        monkeypatch.setitem(CONFIRM_LEVEL_LIMITS, "L3", (1, 0.0))
        lim = RateLimiter(limits={"shell": (3, 0.0), "default": (10, 1.0)}, level_buckets=True)
        assert lim.check("shell_execute") is True            # 分类 3→2, L3 1→0
        tokens_before = lim._buckets["shell"]["tokens"]
        assert tokens_before == 2.0

        assert lim.check("shell_execute") is False           # 被确认级桶挡下
        assert lim._buckets["shell"]["tokens"] == tokens_before, "分类桶令牌必须被退回"

        # 对照：同样的分类桶参数、**不**开确认级桶 ⇒ 第 2 次是放行的；
        # 说明上面第 2 次的拒绝确实来自确认级桶（而不是分类桶）
        ref = RateLimiter(limits={"shell": (3, 0.0), "default": (10, 1.0)}, level_buckets=False)
        assert [ref.check("shell_execute") for _ in range(4)] == [True, True, True, False]

    def test_level_lookup_uses_single_source_of_truth(self):
        lim = RateLimiter(level_buckets=True)
        assert lim.get_confirm_level("shell_execute") == "L3"
        assert lim.get_confirm_level("edit") == "L2"
        assert lim.get_confirm_level("read_file") == "L0"
        assert lim.get_confirm_level("没有任何这个工具_xyz") == ""


# ════════════════════════════════════════════════════════════
#  6. 回滚开关（置 0 ⇒ 回到现状）
# ════════════════════════════════════════════════════════════


class TestRollbackSwitches:
    def test_http_gate_off_by_env(self, monkeypatch):
        monkeypatch.setenv("CP_HTTP_CONCURRENCY_GATE", "0")
        assert build_http_gate_from_env() is None

    def test_http_gate_defaults_and_overrides(self, monkeypatch):
        monkeypatch.delenv("CP_HTTP_CONCURRENCY_GATE", raising=False)
        for name in ("CP_HTTP_MAX_CONCURRENT", "CP_HTTP_QUEUE_TIMEOUT", "CP_HTTP_MAX_QUEUE"):
            monkeypatch.delenv(name, raising=False)
        gate = build_http_gate_from_env()
        assert (gate.max_concurrent, gate.queue_timeout, gate.max_queue) == (8, 20.0, 0)
        assert "/api/health" in gate.exempt_prefixes and "/metrics" in gate.exempt_prefixes

        monkeypatch.setenv("CP_HTTP_MAX_CONCURRENT", "3")
        monkeypatch.setenv("CP_HTTP_QUEUE_TIMEOUT", "1.5")
        gate2 = build_http_gate_from_env()
        assert (gate2.max_concurrent, gate2.queue_timeout) == (3, 1.5)

    def test_http_gate_bad_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("CP_HTTP_MAX_CONCURRENT", "八")
        monkeypatch.setenv("CP_HTTP_QUEUE_TIMEOUT", "很久")
        gate = build_http_gate_from_env()
        assert (gate.max_concurrent, gate.queue_timeout) == (8, 20.0)

    def test_tool_switches_off_restores_legacy(self, monkeypatch):
        monkeypatch.setenv("CP_TOOL_CONCURRENCY_GATE", "0")
        monkeypatch.setenv("CP_TOOL_LEVEL_BUCKET", "0")
        monkeypatch.delenv("CP_TOOL_MAX_CONCURRENT", raising=False)
        lim = tool_limiter_from_env()
        assert lim.concurrency_gate is False and lim._level_buckets is False
        assert lim.max_concurrent == 100            # 构造默认（改动前的形态）
        assert lim.acquire_concurrent() is True
        assert lim.get_status()["current_concurrent"] == 0
        assert [lim.check("shell_execute") for _ in range(3)] == [True, True, False]

    def test_tool_defaults_are_on(self, monkeypatch):
        for name in ("CP_TOOL_CONCURRENCY_GATE", "CP_TOOL_LEVEL_BUCKET", "CP_TOOL_MAX_CONCURRENT"):
            monkeypatch.delenv(name, raising=False)
        lim = tool_limiter_from_env()
        assert lim.concurrency_gate is True and lim._level_buckets is True
        assert lim.max_concurrent == 16
        # 分类桶**取值一字未改**
        assert lim._limits == {"default": (10, 1.0), "network": (5, 0.5),
                               "shell": (2, 0.2), "file": (15, 1.0)}


# ════════════════════════════════════════════════════════════
#  7. 接线事实（不改 A1：serve()/就绪门原样；闸门装在请求入口）
# ════════════════════════════════════════════════════════════


class TestWiringFacts:
    def test_app_server_installs_gate_at_request_entry(self):
        src = (REPO_ROOT / "app_server.py").read_text(encoding="utf-8")
        assert "ConcurrencyGateMiddleware(app.wsgi_app, _http_gate)" in src
        assert "build_http_gate_from_env()" in src
        idx = src.index("ConcurrencyGateMiddleware(app.wsgi_app")
        assert "except Exception as _http_gate_err" in src[idx:idx + 400]

    def test_a1_wiring_untouched(self):
        """A1 的 guarded_startup / 就绪门 / threads=16 一字未改"""
        src = (REPO_ROOT / "app_server.py").read_text(encoding="utf-8")
        assert 'serve(app, host="127.0.0.1", port=5678, threads=16)' in src
        assert "guarded_startup(" in src and "preflight=_startup_preflight" in src
