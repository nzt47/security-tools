"""S5 通信 · 回调反向通道 单元测试（母体 ↔ 分身）

锁死的契约（每条都可证伪，见测试类/函数名）：
  ① 默认关闭：工厂返回 None；缺省构造的投递器也零出站；执行器仍只写审计（逐字旧行为）；
  ② 开启 + 白名单内 host：恰好 1 次 POST，带 Authorization: Bearer，payload 不含密钥形态；
  ③ 白名单外 host / 非 http(s) scheme：零出站且 reason 非空（SSRF 守卫）；
  ④ 传输失败：executor outcome 不变（ok 保持 True），callback.error 非空（fail-soft）；
  ⑤ payload 含密钥形态（sk- 长串）：拒绝投递且零出站；
  ⑥ 入站无 token ⇒ 401；
  ⑦ 入站合法 ⇒ 落一条；同 delegation_id 再投 ⇒ deduplicated 且不重复落账；
  ⑧ 入站超大体 ⇒ 413；缺 delegation_id ⇒ 400；
  ⑨ summary 只作短摘要（截断 ≤1000），不执行、不解析。

外加「可证伪探针」（改产品逻辑即转红，见 TestFalsifiableProbes）：
  · 白名单是出站唯一放行判据（短路 check ⇒ 红）；
  · 密钥形态必须拒绝投递（删 assert_manifest_secret_free ⇒ 红）；
  · 入站重复投递不得重复落账（删 find 判重 ⇒ 红）。

本文件不 import app_server：最小 Flask app + register_routes，历史落 tmp（不碰仓库 data/）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict

import pytest
from flask import Flask

from agent.server_auth import _AUTH_DISABLED_FOR_TEST  # noqa: F401  仅确认可被 monkeypatch
from agent.server_routes import routes_subagent as routes_module
from agent.server_routes.routes_subagent import register_routes
from agent.subagent import callback_channel as cb
from agent.subagent.callback_channel import (
    CALLBACK_SUMMARY_MAX_CHARS,
    CallbackPolicy,
    HttpCallbackDispatcher,
    build_callback_dispatcher,
    build_callback_record,
    host_allowed,
)
from agent.subagent.credentials import ManifestSecretLeak
from agent.subagent.delegation import DelegationContext
from agent.subagent.delegation_history import GOAL_MAX_CHARS, DelegationHistory
from agent.subagent.executor import DelegationExecutor

AUTH = {"Authorization": "Bearer test-token"}
OK = "https://api.partner.com/hook"


# ════════════════════════════════════════════════════════════
#  出站：假传输 + 桩通道/执行器
# ════════════════════════════════════════════════════════════


class SpyTransport:
    """记录每次出站调用的假传输（返回状态码或抛异常）"""

    def __init__(self, status: int = 200, exc: Exception | None = None):
        self.status = status
        self.exc = exc
        self.calls: list = []

    def __call__(self, url: str, body: str, headers: Dict[str, str], timeout: float) -> int:
        self.calls.append({"url": url, "body": body,
                           "headers": dict(headers), "timeout": timeout})
        if self.exc is not None:
            raise self.exc
        return self.status


def _enabled_policy(hosts=("api.partner.com",)) -> CallbackPolicy:
    return CallbackPolicy(enabled=True, allowed_hosts=list(hosts))


def _success_line() -> str:
    return json.dumps({
        "status": "done",
        "summary": "完成",
        "artifacts": [{"path": "out/a.json"}],
        "self_eval": {"verdict": "pass", "score": 0.9, "summary": "全部完成"},
    }, ensure_ascii=False)


class _StubChannel:
    def __init__(self, output: str):
        self.output = output

    def __call__(self, invocation):
        from agent.subagent.channel import RawOutput

        return RawOutput(stdout=self.output, returncode=0)


def _ctx(**overrides) -> DelegationContext:
    data = {
        "goal": "把 docs/zh 下的设计稿抽取为可复现步骤序列",
        "constraints": ["只读仓库，不得修改任何文件"],
        "prior_artifacts": [],
        "prohibitions": ["不得对外发送数据"],
        "artifact_format": "文本要点",
        "budget_tokens": 4000,
        "timeout_seconds": 60.0,
        "callback_url": "https://api.partner.com/hook",
        "task_id": "task-1",
        "trace_id": "tr-1",
        "tenant_id": "default",
        "subject_id": "owner",
    }
    data.update(overrides)
    return DelegationContext(**data)


def _make_executor(tmp_path, **kw) -> DelegationExecutor:
    return DelegationExecutor(channel=_StubChannel(_success_line()),
                              workspace=str(tmp_path), **kw)


# ════════════════════════════════════════════════════════════
#  ① 默认关闭
# ════════════════════════════════════════════════════════════


class TestDefaultOff:
    def test_未开启时工厂返回None且零出站(self, monkeypatch, tmp_path):
        for name in (cb.ENV_HTTP, cb.ENV_ALLOW, cb.ENV_TOKEN):
            monkeypatch.delenv(name, raising=False)
        assert build_callback_dispatcher() is None

        # 即使有人绕过工厂直接 new 投递器，默认策略也必须零出站
        spy = SpyTransport()
        dispatcher = HttpCallbackDispatcher(transport=spy)
        result = dispatcher(OK, {"delegation_id": "dlg-1"})
        assert spy.calls == []
        assert result["delivered"] is False
        assert result["mode"] == "blocked"
        assert result["error"]

    def test_缺省执行器仍只写审计且形状逐字不变(self, tmp_path):
        executor = _make_executor(tmp_path)  # 不注入 callback_dispatcher
        ctx = _ctx()
        outcome = executor.execute(ctx, tools=("read_file",),
                                   authorized_capabilities=("read_file",))
        assert outcome.ok is True
        assert outcome.callback == {"callback_url": ctx.callback_url,
                                    "delivered": True, "error": "",
                                    "mode": "audit_record"}


# ════════════════════════════════════════════════════════════
#  ② 开启 + 白名单内 host
# ════════════════════════════════════════════════════════════


class TestEnabledDelivery:
    def test_白名单内host恰好一次POST且带Bearer(self, monkeypatch):
        monkeypatch.setenv(cb.ENV_HTTP, "1")
        monkeypatch.setenv(cb.ENV_ALLOW, "api.partner.com")
        monkeypatch.setenv(cb.ENV_TOKEN, "s3cr3t-token")
        monkeypatch.delenv(cb.ENV_TIMEOUT, raising=False)
        spy = SpyTransport(status=200)
        dispatcher = build_callback_dispatcher(transport=spy)
        assert isinstance(dispatcher, HttpCallbackDispatcher)

        payload = {"delegation_id": "dlg-1", "status": "success", "summary": "完成"}
        result = dispatcher(OK, payload)
        assert len(spy.calls) == 1
        call = spy.calls[0]
        assert call["url"] == OK
        assert call["headers"]["Authorization"] == "Bearer s3cr3t-token"
        sent = json.loads(call["body"])
        assert sent["delegation_id"] == "dlg-1"
        # 令牌原文绝不回显在返回值里
        assert "s3cr3t-token" not in json.dumps(result, ensure_ascii=False)
        assert result["delivered"] is True and result["status"] == 200

    def test_重试上界最多一次(self):
        spy = SpyTransport(exc=RuntimeError("boom"))
        dispatcher = HttpCallbackDispatcher(policy=_enabled_policy(),
                                            token="t", transport=spy, retries=1)
        result = dispatcher(OK, {"delegation_id": "dlg-1"})
        assert len(spy.calls) == 2  # 初次 + 最多 1 次重试
        assert result["delivered"] is False and result["error"]


# ════════════════════════════════════════════════════════════
#  ③ SSRF 守卫：白名单外 / 非 http scheme
# ════════════════════════════════════════════════════════════


class TestSsrfGuard:
    @pytest.mark.parametrize("url,reason_keyword", [
        ("http://evil.internal/hook", "白名单"),
        ("file:///etc/passwd", "scheme"),
        ("gopher://api.partner.com/", "scheme"),
        ("http://user:pass@api.partner.com/", "userinfo"),
        ("not-a-url", "scheme"),
    ])
    def test_非白名单或非http一律拒绝且零出站(self, url, reason_keyword):
        spy = SpyTransport()
        dispatcher = HttpCallbackDispatcher(policy=_enabled_policy(), token="t",
                                            transport=spy)
        result = dispatcher(url, {"delegation_id": "dlg-1"})
        assert spy.calls == []
        assert result["delivered"] is False
        assert result["mode"] == "blocked"
        assert reason_keyword in result["error"]

    def test_后缀白名单只匹配真实子域(self):
        policy = _enabled_policy((".example.com",))
        assert policy.check("https://a.example.com/x")[0] is True
        assert policy.check("https://notexample.com/x")[0] is False
        assert policy.check("https://evil-example.com/x")[0] is False
        assert host_allowed("a.example.com", (".example.com",)) is True
        assert host_allowed("example.com", (".example.com",)) is False


# ════════════════════════════════════════════════════════════
#  ④ fail-soft：传输失败不改委派结果
# ════════════════════════════════════════════════════════════


class TestFailSoft:
    def test_传输失败时outcome保持ok且callback有error(self, tmp_path):
        spy = SpyTransport(exc=RuntimeError("endpoint down"))
        dispatcher = HttpCallbackDispatcher(policy=_enabled_policy(), token="t",
                                            transport=spy)
        executor = _make_executor(tmp_path, callback_dispatcher=dispatcher)
        outcome = executor.execute(_ctx(), tools=("read_file",),
                                   authorized_capabilities=("read_file",))
        assert outcome.ok is True
        assert outcome.callback["delivered"] is False
        assert outcome.callback["error"]
        assert "RuntimeError" in outcome.callback["error"]

    def test_策略拒绝时outcome保持ok且callback有error(self, tmp_path):
        spy = SpyTransport()
        dispatcher = HttpCallbackDispatcher(policy=_enabled_policy(), token="t",
                                            transport=spy)
        executor = _make_executor(tmp_path, callback_dispatcher=dispatcher)
        outcome = executor.execute(_ctx(callback_url="http://evil.internal/x"),
                                   tools=("read_file",),
                                   authorized_capabilities=("read_file",))
        assert outcome.ok is True
        assert spy.calls == []
        assert outcome.callback["delivered"] is False
        assert outcome.callback["error"]


# ════════════════════════════════════════════════════════════
#  ⑤ 密钥闸门
# ════════════════════════════════════════════════════════════


class TestSecretGate:
    def test_密钥形态payload拒绝投递(self):
        spy = SpyTransport()
        dispatcher = HttpCallbackDispatcher(policy=_enabled_policy(), token="t",
                                            transport=spy)
        payload = {"delegation_id": "dlg-1", "summary": "sk-" + "A" * 32}
        with pytest.raises(ManifestSecretLeak):
            dispatcher(OK, payload)
        assert spy.calls == []


# ════════════════════════════════════════════════════════════
#  ⑥⑦⑧⑨ 入站路由
# ════════════════════════════════════════════════════════════


class _FakeYunshu:
    _subagent_mgr = None
    _llm = None

    def list_subagents(self):
        return []


@pytest.fixture
def client(monkeypatch, tmp_path):
    hist = DelegationHistory(path=str(tmp_path / "delegations.jsonl"))
    monkeypatch.setattr(routes_module, "delegation_history", hist)
    monkeypatch.setattr("agent.server_auth._AUTH_DISABLED_FOR_TEST", False)
    monkeypatch.setenv("FLASK_API_TOKEN", "test-token")
    state = type("S", (), {"Yunshu": _FakeYunshu()})()
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app, state)
    return app.test_client(), hist


class TestInboundRoute:
    def test_无token即401(self, client):
        c, _ = client
        resp = c.post("/api/subagent/callback", json={"delegation_id": "dlg-1"})
        assert resp.status_code == 401

    def test_合法投递落一条且幂等(self, client):
        c, hist = client
        body = {"delegation_id": "dlg-1", "status": "success",
                "trace_id": "tr-9", "summary": "完成", "artifact_count": 2,
                "source": "subprocess"}
        first = c.post("/api/subagent/callback", json=body, headers=AUTH)
        assert first.status_code == 200
        data = first.get_json()
        assert data["deduplicated"] is False and data["recorded"] is True
        assert hist.total() == 1

        second = c.post("/api/subagent/callback", json=body, headers=AUTH)
        assert second.status_code == 200
        assert second.get_json()["deduplicated"] is True
        assert hist.total() == 1  # 不重复落账
        rec = hist.query(limit=1)[0]
        assert rec["delegation_id"] == "dlg-1"
        assert rec["source"] == "callback"
        assert rec["ok"] is True
        assert rec["trace_id"] == "tr-9"
        assert rec["artifact_count"] == 2

    def test_超大体413(self, client):
        c, _ = client
        resp = c.post("/api/subagent/callback",
                      data=b"x" * (64 * 1024 + 1),
                      headers={**AUTH, "Content-Type": "application/json"})
        assert resp.status_code == 413

    def test_缺delegation_id为400(self, client):
        c, _ = client
        resp = c.post("/api/subagent/callback", json={"status": "success"}, headers=AUTH)
        assert resp.status_code == 400
        assert "delegation_id" in resp.get_json()["error"]

    def test_非法JSON为400(self, client):
        c, _ = client
        resp = c.post("/api/subagent/callback", data=b"{not json",
                      headers={**AUTH, "Content-Type": "application/json"})
        assert resp.status_code == 400

    def test_summary只作短摘要且截断(self):
        long_summary = "摘" * 5000
        rec = build_callback_record(
            {"delegation_id": "d", "status": "success",
             "summary": long_summary, "artifact_count": "3"},
            "d", now="2026-10-09T00:00:00+00:00")
        assert len(rec["summary"]) == CALLBACK_SUMMARY_MAX_CHARS
        assert len(rec["goal"]) == GOAL_MAX_CHARS
        assert rec["artifact_count"] == 3
        assert rec["source"] == "callback"

    def test_status映射ok(self):
        assert build_callback_record({"delegation_id": "d", "status": "success"},
                                     "d")["ok"] is True
        assert build_callback_record({"delegation_id": "d", "status": "error"},
                                     "d")["ok"] is False


# ════════════════════════════════════════════════════════════
#  可证伪探针（改产品逻辑即转红）
# ════════════════════════════════════════════════════════════


class TestFalsifiableProbes:
    def test_probe_白名单是出站唯一放行判据(self):
        """探针：把 CallbackPolicy.check 短路成恒放行 ⇒ 本用例转红。"""
        spy = SpyTransport()
        dispatcher = HttpCallbackDispatcher(policy=_enabled_policy(), token="t",
                                            transport=spy)
        result = dispatcher("http://169.254.169.254/latest/meta-data/",
                            {"delegation_id": "d"})
        assert spy.calls == []
        assert result["delivered"] is False and result["error"]

    def test_probe_密钥形态必须拒绝投递(self):
        """探针：删掉 dispatcher 里的 assert_manifest_secret_free ⇒ 本用例转红。"""
        spy = SpyTransport()
        dispatcher = HttpCallbackDispatcher(policy=_enabled_policy(), token="t",
                                            transport=spy)
        with pytest.raises(ManifestSecretLeak):
            dispatcher(OK, {"delegation_id": "d", "summary": "ghp_" + "b" * 30})
        assert spy.calls == []

    def test_probe_入站重复投递不得重复落账(self, client):
        """探针：删掉路由里的 delegation_history.find 判重 ⇒ 本用例转红。"""
        c, hist = client
        body = {"delegation_id": "dlg-probe", "status": "success"}
        c.post("/api/subagent/callback", json=body, headers=AUTH)
        c.post("/api/subagent/callback", json=body, headers=AUTH)
        assert hist.total() == 1
