"""S5 通信 · 静态对端 + 心跳 + 离线回退显式登记（agent/subagent/peers.py）守卫

本文件把 PR-E 的每条承诺逐条钉成**可证伪**断言（回退产品逻辑 ⇒ 立刻红）：

  G1 畸形环境变量 ⇒ 空表且**不抛**（含"跳过坏条目、保留好条目"的宽容口径）；
  G2 静态白名单是唯一出站来源：未配置/被禁用对端 ⇒ 替身 dispatcher **零调用**；
  G3 心跳失败逐对端 fail-soft：绝不抛，且**真的**写审计（action 正确、status=failed）；
  G4 凭据不回显：心跳载荷/返回值/日志里不出现 callback_url（含植入的标记串）；
  G5 离线回退显式登记：status=not_implemented，replay/outbox 均为 False；
  G6 周期注册：缺 CP_SUBAGENT_PEER_HEARTBEAT_SEC 就不注册（不写死数字默认）；
  G7 读面：GET /api/subagent/history 内联 peers 段（最小 Flask app + 替身，不 import app_server）；
  G8 静态纪律：peers.py 不做任何服务发现（无 socket/DNS/SRV/mDNS/注册中心痕迹）。

【不碰仓库 data/】路由用替身与 tmp_path 的 JSONL；不 import app_server（本仓有过
"手搓 Flask 全绿、线上 404"的教训，故另用静态 AST 断言真实入口的注册在别处覆盖）。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest
from flask import Flask

from agent.server_routes import routes_subagent as routes_module
from agent.server_routes.routes_subagent import register_routes
from agent.subagent.delegation_history import DelegationHistory
from agent.subagent.peers import (
    AUDIT_ACTION,
    ENV_HEARTBEAT_INTERVAL,
    ENV_PEERS,
    OFFLINE_FALLBACK_STATUS,
    PeerHealth,
    PeerTarget,
    StaticPeerRegistry,
    heartbeat_payload,
    offline_fallback_report,
    register_peer_heartbeats,
    send_heartbeats,
)

pytestmark = pytest.mark.timeout(900)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PEERS_SRC = _REPO_ROOT / "agent" / "subagent" / "peers.py"

#: 植入 URL 的"标记串"：任何返回值/日志里出现它都说明对端地址被回显了
_MARKER = "PEER_MARKER_9f3a1c7d5e"


class _Dispatcher:
    """替身 dispatcher：计数 + 可注入成功/失败"""

    def __init__(self, *, result=None, boom=False):
        self.calls = []
        self._result = result if result is not None else {
            "delivered": True, "status": 200, "error": ""}
        self._boom = boom

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        if self._boom:
            raise RuntimeError("network down")
        return dict(self._result)


class _Audit:
    """替身审计：记录调用并可注入写失败（返回 None 视作未写入）"""

    def __init__(self, *, boom=False, silent=False):
        self.calls = []
        self._boom = boom
        self._silent = silent

    def record(self, action, actor=None, subject="", payload=None, status="", **kw):
        self.calls.append({"action": action, "actor": actor, "subject": subject,
                           "payload": payload, "status": status})
        if self._boom:
            raise RuntimeError("audit down")
        return None if self._silent else {"n": len(self.calls)}


# ════════════════════════════════════════════════════════════
#  G1 畸形 env ⇒ 空表不抛
# ════════════════════════════════════════════════════════════


class TestG1MalformedEnv:
    @pytest.mark.parametrize("raw", [
        "{not json[",
        '{"a": ',
        "]=[",
        "name-only-no-separator",
        ",,,",
    ])
    def test_畸形原文得空表且不抛(self, raw, caplog):
        with caplog.at_level(logging.WARNING, logger="agent.subagent.peers"):
            reg = StaticPeerRegistry.from_env(raw)
        assert reg.targets == (), f"畸形配置应得空表，实际 {reg.targets}"
        assert reg.health() == {}

    def test_空串得空表(self):
        assert StaticPeerRegistry.from_env("").targets == ()
        assert StaticPeerRegistry.from_env("   ").targets == ()

    def test_坏条目被跳过_好条目保留(self):
        reg = StaticPeerRegistry.from_env("good=http://h/a,bad-no-eq,also=http://h/b")
        assert [t.name for t in reg.targets] == ["good", "also"]

    def test_环境变量畸形也不抛(self, monkeypatch, caplog):
        monkeypatch.setenv(ENV_PEERS, "{oops[")
        with caplog.at_level(logging.WARNING, logger="agent.subagent.peers"):
            reg = StaticPeerRegistry.from_env()
        assert reg.targets == ()
        assert any("CP_SUBAGENT_PEERS" in r.message or "对端" in r.message
                   for r in caplog.records), "畸形配置必须留下 warning，不得静默"

    def test_两种合法格式都解析(self):
        by_json = StaticPeerRegistry.from_env(json.dumps([
            {"name": "a", "callback_url": "http://h/a", "enabled": True},
            "b=http://h/b",
            {"name": "c", "url": "http://h/c", "enabled": False},
        ]))
        assert [(t.name, t.enabled) for t in by_json.targets] == [
            ("a", True), ("b", True), ("c", False)]
        by_pairs = StaticPeerRegistry.from_env("a=http://h/a,b=http://h/b")
        assert [t.name for t in by_pairs.targets] == ["a", "b"]
        # 同名字段去重（首个胜），健康表键唯一
        dupe = StaticPeerRegistry.from_env("a=http://h/1,a=http://h/2")
        assert [t.name for t in dupe.targets] == ["a"]


# ════════════════════════════════════════════════════════════
#  G2 白名单是唯一出站来源：未配置 / 禁用 ⇒ 零调用
# ════════════════════════════════════════════════════════════


class TestG2AllowlistOnly:
    def test_被禁用对端零出站(self):
        reg = StaticPeerRegistry.from_env(json.dumps([
            {"name": "on", "callback_url": "http://h/on", "enabled": True},
            {"name": "off", "callback_url": "http://h/off", "enabled": False},
        ]))
        disp = _Dispatcher()
        res = send_heartbeats(reg, disp)
        assert [url for url, _ in disp.calls] == ["http://h/on"], "禁用对端被出站了"
        assert res["sent"] == 1 and res["skipped"] == 1
        assert reg.health()["off"]["attempts"] == 0, "禁用对端不应被计入尝试"

    def test_未声明的对端永不出现(self):
        reg = StaticPeerRegistry.from_env("only=http://h/only")
        disp = _Dispatcher()
        send_heartbeats(reg, disp)
        assert [url for url, _ in disp.calls] == ["http://h/only"]

    def test_空表零出站(self):
        disp = _Dispatcher()
        res = send_heartbeats(StaticPeerRegistry.from_env("{bad["), disp)
        assert disp.calls == []
        assert res == {"sent": 0, "delivered": 0, "failed": 0, "skipped": 0,
                       "audited": 0, "peers": []}


# ════════════════════════════════════════════════════════════
#  G3 fail-soft + 审计真的写入
# ════════════════════════════════════════════════════════════


class TestG3FailSoftAudit:
    def test_心跳抛出也不抛且写审计(self, caplog):
        reg = StaticPeerRegistry.from_env("a=http://h/a")
        audit = _Audit()
        disp = _Dispatcher(boom=True)
        with caplog.at_level(logging.WARNING, logger="agent.subagent.peers"):
            res = send_heartbeats(reg, disp, audit=audit)
        assert res["failed"] == 1 and res["delivered"] == 0
        assert res["audited"] == 1, "审计应被真的写入"
        assert res["peers"][0]["audited"] is True
        assert len(audit.calls) == 1
        call = audit.calls[0]
        assert call["action"] == AUDIT_ACTION
        assert call["status"] == "failed"
        assert call["subject"] == "peer:a"
        assert call["payload"]["peer"] == "a"
        assert reg.health()["a"]["consecutive_failures"] == 1

    def test_一个对端失败不影响另一个(self):
        reg = StaticPeerRegistry.from_env("bad=http://h/bad,good=http://h/good")
        calls = []

        def disp(url, payload):
            calls.append(url)
            if url.endswith("bad"):
                raise RuntimeError("boom")
            return {"delivered": True, "status": 200}

        res = send_heartbeats(reg, disp)
        assert calls == ["http://h/bad", "http://h/good"], "逐对端必须继续"
        assert res["sent"] == 2 and res["failed"] == 1 and res["delivered"] == 1
        health = reg.health()
        assert health["bad"]["ok"] is False
        assert health["good"]["ok"] is True and health["good"]["last_ok_at"]

    def test_审计失败不影响心跳_且audited为False(self):
        reg = StaticPeerRegistry.from_env("a=http://h/a")
        res = send_heartbeats(reg, _Dispatcher(), audit=_Audit(boom=True))
        assert res["delivered"] == 1 and res["audited"] == 0
        res2 = send_heartbeats(reg, _Dispatcher(), audit=_Audit(silent=True))
        assert res2["audited"] == 0, "审计返回 None（未写入）不算写入"

    def test_审计缺失时不抛且audited为False(self):
        res = send_heartbeats(StaticPeerRegistry.from_env("a=http://h/a"),
                              _Dispatcher(), audit=None)
        assert res["audited"] == 0 and res["delivered"] == 1

    def test_健康态成功清零连续失败(self):
        reg = StaticPeerRegistry(
            [PeerTarget(name="a", callback_url="http://h/a")])
        send_heartbeats(reg, _Dispatcher(boom=True))
        send_heartbeats(reg, _Dispatcher(boom=True))
        assert reg.health()["a"]["consecutive_failures"] == 2
        send_heartbeats(reg, _Dispatcher())
        h = reg.health()["a"]
        assert h["consecutive_failures"] == 0 and h["ok"] is True
        assert h["attempts"] == 3 and h["last_status"] == 200


# ════════════════════════════════════════════════════════════
#  G4 凭据不回显
# ════════════════════════════════════════════════════════════


class TestG4NoCredentialEcho:
    def test_载荷没有敏感键(self):
        payload = heartbeat_payload("a", now=1.0)
        blob = json.dumps(payload, ensure_ascii=False).lower()
        for word in ("token", "password", "secret", "api_key", "apikey",
                     "credential", "bearer", "authorization"):
            assert word not in blob, f"心跳载荷出现敏感词 {word}"

    def test_返回值与日志不含对端地址标记(self, caplog):
        reg = StaticPeerRegistry.from_env("a=http://host/cb?x=" + _MARKER)
        audit = _Audit()
        with caplog.at_level(logging.DEBUG):
            res = send_heartbeats(reg, _Dispatcher(boom=True), audit=audit)
            send_heartbeats(reg, _Dispatcher(), audit=audit)
        assert _MARKER not in json.dumps(res, ensure_ascii=False), (
            "返回值回显了对端地址（凭据/查询串可能随之泄漏）")
        assert _MARKER not in caplog.text, "日志回显了对端地址"
        assert _MARKER not in json.dumps(audit.calls, ensure_ascii=False), (
            "审计载荷回显了对端地址")

    def test_快照只给声明字段(self):
        snap = StaticPeerRegistry.from_env(
            "a=http://host/cb?x=" + _MARKER).snapshot()
        assert snap["peers"] == [{"name": "a", "url": "http://host/cb?x=" + _MARKER,
                                  "enabled": True}]
        assert snap["service_discovery"] is False


# ════════════════════════════════════════════════════════════
#  G5 离线回退显式登记
# ════════════════════════════════════════════════════════════


class TestG5OfflineFallback:
    def test_状态是中性_not_implemented(self):
        report = offline_fallback_report()
        assert report["status"] == OFFLINE_FALLBACK_STATUS == "not_implemented"
        assert report["replay"] is False and report["outbox"] is False
        assert report["note"].strip(), "未实现必须写清反幻觉说明"

    def test_快照内联离线报告(self):
        snap = StaticPeerRegistry.from_env("a=http://h/a").snapshot()
        assert snap["offline_fallback"]["status"] == "not_implemented"


# ════════════════════════════════════════════════════════════
#  G6 周期注册：缺周期 ⇒ 不注册（不写死数字默认）
# ════════════════════════════════════════════════════════════


class _Scheduler:
    def __init__(self):
        self.tasks = []

    def add_interval_task(self, name, func, interval_seconds):
        self.tasks.append({"name": name, "func": func,
                           "interval_seconds": interval_seconds})


class TestG6Register:
    def test_缺周期不注册(self, monkeypatch):
        monkeypatch.delenv(ENV_HEARTBEAT_INTERVAL, raising=False)
        monkeypatch.setenv(ENV_PEERS, "a=http://h/a")
        sched = _Scheduler()
        out = register_peer_heartbeats(scheduler=sched)
        assert out["registered"] is False
        assert out["interval_seconds"] is None
        assert out["reason"] == "interval_not_configured"
        assert sched.tasks == [], "缺周期时不得偷偷注册"

    def test_周期非法不注册(self, monkeypatch):
        monkeypatch.setenv(ENV_HEARTBEAT_INTERVAL, "abc")
        monkeypatch.setenv(ENV_PEERS, "a=http://h/a")
        sched = _Scheduler()
        assert register_peer_heartbeats(scheduler=sched)["registered"] is False
        assert sched.tasks == []

    def test_有周期有对端才注册且tick能发心跳(self, monkeypatch):
        monkeypatch.setenv(ENV_HEARTBEAT_INTERVAL, "45")
        monkeypatch.setenv(ENV_PEERS, "a=http://h/a")
        sched = _Scheduler()
        disp = _Dispatcher()
        out = register_peer_heartbeats(scheduler=sched, dispatcher=disp)
        assert out["registered"] is True and out["interval_seconds"] == 45
        assert len(sched.tasks) == 1
        assert sched.tasks[0]["name"] == "subagent_peer_heartbeat"
        sched.tasks[0]["func"]()
        assert [url for url, _ in disp.calls] == ["http://h/a"]

    def test_有周期无对端不注册(self, monkeypatch):
        monkeypatch.setenv(ENV_HEARTBEAT_INTERVAL, "30")
        monkeypatch.delenv(ENV_PEERS, raising=False)
        sched = _Scheduler()
        out = register_peer_heartbeats(scheduler=sched)
        assert out["registered"] is False and out["reason"] == "no_peers"
        assert sched.tasks == []


# ════════════════════════════════════════════════════════════
#  G7 读面：/api/subagent/history 内联 peers 段
# ════════════════════════════════════════════════════════════


class _FakeYunshu:
    _subagent_mgr = None
    _llm = None

    def list_subagents(self):
        return []


def _make_client(monkeypatch, tmp_path):
    monkeypatch.setattr(routes_module, "delegation_history",
                        DelegationHistory(path=str(tmp_path / "d.jsonl")))
    tm = __import__("agent.subagent.task_board", fromlist=["task_board"])
    monkeypatch.setattr(tm, "task_board",
                        tm.TaskBoard(path=str(tmp_path / "b.jsonl")))
    monkeypatch.setattr(routes_module, "task_board", tm.task_board)
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app, type("S", (), {"Yunshu": _FakeYunshu()})())
    return app.test_client(), app


class TestG7HistoryPeersSegment:
    def test_peers段给出声明与健康且不新增路由(self, monkeypatch, tmp_path):
        monkeypatch.setenv(ENV_PEERS, json.dumps([
            {"name": "a", "callback_url": "http://h/a", "enabled": True},
            {"name": "b", "callback_url": "http://h/b", "enabled": False}]))
        client, app = _make_client(monkeypatch, tmp_path)
        rules = {str(r.rule) for r in app.url_map.iter_rules()}
        assert "/api/subagent/history" in rules
        assert "/api/subagent/peers" not in rules, "peers 段不得新增路由"

        body = client.get("/api/subagent/history").get_json()
        assert body["ok"] is True
        seg = body["peers"]
        assert [p["name"] for p in seg["peers"]] == ["a", "b"]
        assert seg["peers"][0]["url"] == "http://h/a"
        assert seg["peers"][1]["enabled"] is False
        assert seg["count"] == 2 and seg["enabled_count"] == 1
        assert set(seg["health"]) == {"a", "b"}
        assert seg["health"]["a"]["attempts"] == 0
        assert seg["offline_fallback"]["status"] == "not_implemented"

    def test_畸形配置读面仍200且空表(self, monkeypatch, tmp_path):
        monkeypatch.setenv(ENV_PEERS, "{broken[")
        client, _ = _make_client(monkeypatch, tmp_path)
        body = client.get("/api/subagent/history").get_json()
        assert body["ok"] is True
        assert body["peers"]["peers"] == [] and body["peers"]["count"] == 0

    def test_未配置时也是显式空段(self, monkeypatch, tmp_path):
        monkeypatch.delenv(ENV_PEERS, raising=False)
        client, _ = _make_client(monkeypatch, tmp_path)
        seg = client.get("/api/subagent/history").get_json()["peers"]
        assert seg["peers"] == [] and seg["service_discovery"] is False


# ════════════════════════════════════════════════════════════
#  G8 静态纪律：不做服务发现
# ════════════════════════════════════════════════════════════


class TestG8NoServiceDiscovery:
    def test_源码不含探测痕迹(self):
        src = _PEERS_SRC.read_text(encoding="utf-8")
        for banned in ("import socket", "getaddrinfo", "gethostbyname",
                       "dns.resolver", "mdns", "zeroconf", "consul",
                       "etcd", "srv_lookup", "service_discovery("):
            assert banned not in src, f"peers.py 出现服务发现痕迹: {banned}"

    def test_快照显式声明不做服务发现(self):
        assert StaticPeerRegistry.from_env("a=http://h/a").snapshot()[
            "service_discovery"] is False


# ════════════════════════════════════════════════════════════
#  额外：PeerHealth 形状（读面契约）
# ════════════════════════════════════════════════════════════


class TestPeerHealthShape:
    def test_默认即从未尝试(self):
        h = PeerHealth(name="a").to_dict()
        assert h == {"name": "a", "attempts": 0, "ok": False,
                     "consecutive_failures": 0, "last_status": None,
                     "last_error": "", "last_ok_at": None}

    def test_未知名字也不抛(self):
        reg = StaticPeerRegistry([])  # 空表
        h = reg.note_result("ghost", ok=True, status="201")
        assert h.attempts == 1 and h.last_status == 201
