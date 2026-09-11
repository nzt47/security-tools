"""TASK-S4-02 集成测试：真实执行点的「决策-执行分离」与零行为回归

这是本任务**最关键的一份用例**，它验证的是接线而不是单元语义：

    1. ``agent/web/http_client.py`` 的 ``request()`` / ``download()`` 是出域执行点：
       deny 时**网络动作尚未发生**（mock 的 session 未被调用）；未覆盖时**逐字节
       保持既有行为**（mock 被正常调用）。
    2. 「读本地密钥 → 外发 HTTP」整条链路（§5.7 机制 4）：读端点打污点 ⇒ 写端点拒绝。
    3. ``PermissionGateway`` 的策略层**只收敛不放宽**：deny/ask 短路，allow 与未命中
       都回落既有三层；未启用策略层时行为与加装前一致。
    4. ``agent/policy`` **无网络副作用**：AST 静态检查 + 运行期 socket 探针。
    5. 仓库策略文件可装载、可校验；运行时产物已入 .gitignore。
"""
from __future__ import annotations

import ast
import json
import os
import re
import socket
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from policy_testkit import isolate_policy, make_policy, make_store, write_policy_file

import requests

from agent.guardrails.egress_guard import BLOCKED_ERROR_PREFIX, EgressGuard
from agent.policy.decisions import DecisionLog
from agent.policy.engine import DecisionObserver, PolicyEngine
from agent.policy.models import EFFECT_ALLOW, EFFECT_ASK, EFFECT_DENY, PolicyContext
from agent.policy.taint import is_secret_path, reset_secret_taint, scan_secret_material
from agent.permission_system import (
    ABACContext,
    PermissionGateway,
    Role,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PKG = REPO_ROOT / "agent" / "policy"
FAKE_KEY = "sk-" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3S"
FAKE_KEY2 = "sk-" + "Aa1Bb2Cc3Dd4Ee5Ff6Gg7H"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    return isolate_policy(tmp_path, monkeypatch)


def _mock_response():
    response = MagicMock()
    response.ok = True
    response.status_code = 200
    response.reason = "OK"
    response.headers = {"Content-Type": "text/html"}
    response.content = b"<html>ok</html>"
    response.text = "<html>ok</html>"
    response.encoding = "utf-8"
    response.cookies = {}
    response.history = []
    response.url = "https://example.com/"
    return response


# ════════════════════════════════════════════════════════════
#  执行点 1：HttpClient（数据出域）
# ════════════════════════════════════════════════════════════


class TestHttpClientEgressExecutionPoint:
    """P7.1-20：策略只出决策，**执行**在 HttpClient，且拦截发生在网络动作之前。"""

    def _client(self):
        from agent.web.http_client import HttpClient
        return HttpClient({"timeout": 5})

    @patch("requests.Session.request")
    def test_载荷带密钥时拦截且未发生网络动作(self, mock_request):
        mock_request.return_value = _mock_response()
        result = self._client().post("https://api.example.com/v1/collect",
                                     json_data={"key": FAKE_KEY})
        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["blocked_by"] == "policy.egress"
        assert BLOCKED_ERROR_PREFIX in result["error"]
        mock_request.assert_not_called()          # ← 决策-执行分离的核心断言
        assert FAKE_KEY not in json.dumps(result)  # 载荷不外泄到结果里

    @patch("requests.Session.request")
    def test_读取密钥后外发被拦(self, mock_request):
        """§5.7 机制 4 链路证据：先读敏感文件，再外发（载荷不含凭据也不行）。"""
        mock_request.return_value = _mock_response()
        secret = Path(tempfile.mkdtemp()) / "id_rsa"
        secret.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n",
                          encoding="utf-8")
        from agent.policy.taint import observe_file_read
        assert observe_file_read(str(secret), secret.read_text(encoding="utf-8"))
        result = self._client().post("https://api.example.com/v1/collect",
                                     json_data={"query": "hello"})
        assert result["blocked"] is True
        mock_request.assert_not_called()

    @patch("requests.Session.get")
    def test_download_也是执行点(self, mock_get):
        """``download()`` 走 ``_session.get`` 而非 ``request()``，必须单独接线。"""
        mock_get.return_value = _mock_response()
        result = self._client().download("https://api.example.com/f.bin",
                                         os.path.join(tempfile.mkdtemp(), "x.bin"),
                                         auth=FAKE_KEY)
        assert result["ok"] is False and result["blocked"] is True
        mock_get.assert_not_called()

    @patch("requests.Session.request")
    def test_无策略覆盖时行为与加装前一致(self, mock_request):
        mock_request.return_value = _mock_response()
        result = self._client().get("https://example.com/")
        assert result["ok"] is True
        assert result["status_code"] == 200
        mock_request.assert_called_once()   # ← 零行为回归：正常请求照发
        assert "blocked" not in result

    @patch("requests.Session.request")
    def test_内网出域不受污点影响(self, mock_request):
        """污点只拦**对外**出域：本机/内网调用没有外泄面。"""
        mock_request.return_value = _mock_response()
        from agent.policy.taint import mark_secret_read
        mark_secret_read("/x/id_rsa", content_kinds=["openssh_private_key"])
        assert self._client().get("http://127.0.0.1:8080/health")["ok"] is True
        mock_request.assert_called_once()

    @patch("requests.Session.request")
    def test_守卫关闭时完全回到既有行为(self, mock_request, monkeypatch):
        monkeypatch.setenv("CP_POLICY_EGRESS_GUARD", "0")
        mock_request.return_value = _mock_response()
        result = self._client().post("https://api.example.com/x",
                                     json_data={"key": FAKE_KEY})
        assert result["ok"] is True
        mock_request.assert_called_once()

    @patch("requests.Session.request")
    def test_策略层异常时失败开放(self, mock_request, monkeypatch):
        """通用硬约束 1：新增机制失败不得阻断主流程。"""
        import agent.policy.egress as egress_mod

        def boom(*args, **kwargs):
            raise RuntimeError("policy layer down")

        monkeypatch.setattr(egress_mod, "decide_egress", boom)
        mock_request.return_value = _mock_response()
        assert self._client().get("https://example.com/")["ok"] is True

    @patch("requests.Session.request")
    def test_拦截计数入_stats(self, mock_request):
        client = self._client()
        mock_request.return_value = _mock_response()
        client.post("https://api.example.com/x", json_data={"key": FAKE_KEY})
        stats = client.get_stats()
        assert stats["blocked_count"] >= 1
        assert stats["success_count"] == 0


class TestToolReadToEgressChain:
    """「读本地密钥 → 外发 HTTP」端到端（§5.7 机制 4，任务书 §二 硬约束 2）"""

    @pytest.fixture
    def read_tool(self):
        """注册文件工具并取出 read_file 的**真实包装实现**"""
        from agent import tools as tools_mod
        from agent.tools import file_tools_reg

        class _DummyDl:
            _permission = None

        file_tools_reg.register_all(_DummyDl())
        return tools_mod._registry["read_file"]["handler"]

    def test_读密钥之后外发被拒(self, read_tool, tmp_path):
        secret = tmp_path / ".env"
        secret.write_text(f"OPENAI_API_KEY={FAKE_KEY}\n", encoding="utf-8")

        result = read_tool(path=str(secret))
        assert result["ok"] is True                     # 读取本身不受影响
        assert is_secret_path(str(secret)) is True
        assert scan_secret_material(result.get("content", ""))  # 内容确实像凭据

        decision = EgressGuard.precheck(
            method="POST", url="https://api.example.com/v1/chat",
            json_data={"messages": ["hi"]})
        assert decision is not None and decision.allowed is False
        assert decision.evidence["data_class_source"] == "read_then_egress"

    def test_读普通文件不污染链路(self, read_tool, tmp_path):
        note = tmp_path / "notes.txt"
        note.write_text("hello world", encoding="utf-8")
        assert read_tool(path=str(note))["ok"] is True
        decision = EgressGuard.precheck(method="GET", url="https://example.com/")
        assert decision.allowed is True
        assert decision.matched is False

    def test_读敏感路径但内容普通不污染(self, read_tool, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("PORT=8080\nDEBUG=1\n", encoding="utf-8")
        assert read_tool(path=str(env_file))["ok"] is True
        assert EgressGuard.precheck(
            method="GET", url="https://example.com/").allowed is True


# ════════════════════════════════════════════════════════════
#  执行点 2：PermissionGateway（工具调用权限）
# ════════════════════════════════════════════════════════════


RBAC_POLICY = {
    "version": 1,
    "default_role": "guest",
    "roles": {
        "admin": {"allowed_tools": ["*"], "denied_tools": []},
        "developer": {"allowed_tools": ["web_search", "file_read"],
                      "denied_tools": ["system_format"]},
        "guest": {"allowed_tools": ["web_search"], "denied_tools": []},
    },
    "abac_rules": [],
}


@pytest.fixture
def gateway_factory(tmp_path):
    policy_file = tmp_path / "rbac.json"
    policy_file.write_text(json.dumps(RBAC_POLICY), encoding="utf-8")

    def _make(**kwargs):
        return PermissionGateway(policy_path=str(policy_file), **kwargs)

    return _make


class TestPermissionGatewayPolicyLayer:
    """策略层**只收敛不放宽**：allow 不授予权限，未命中回落既有三层。"""

    def test_默认不启用策略层(self, gateway_factory):
        gw = gateway_factory()
        assert gw._policy_enabled is False
        assert gw.check("file_read", {}, ABACContext(role=Role.GUEST)).allowed is False
        assert gw.check("web_search", {}, ABACContext(role=Role.GUEST)).allowed is True

    def test_未命中回落既有_RBAC(self, gateway_factory):
        engine = PolicyEngine(make_store(), cache_size=0, decision_log=False,
                              observer=DecisionObserver(enabled=False), inbox=False)
        gw = gateway_factory(policy_engine=engine)
        # 策略库只有内置不变量，本场景不命中 ⇒ 必须回落到 RBAC
        assert gw.check("file_read", {}, ABACContext(role=Role.GUEST)).allowed is False
        assert gw.check("file_read", {},
                        ABACContext(role=Role.DEVELOPER)).allowed is True

    def test_策略_deny_短路(self, gateway_factory):
        engine = PolicyEngine(
            make_store([make_policy(id="gw.deny", effect="deny", match={})]),
            cache_size=0, decision_log=False,
            observer=DecisionObserver(enabled=False), inbox=False)
        gw = gateway_factory(policy_engine=engine)
        # admin 本来允许一切；策略 deny 必须能收敛它
        result = gw.check("shell_execute", {}, ABACContext(role=Role.ADMIN))
        assert result.allowed is False
        assert result.reason == "权限不足"          # 不暴露策略细节

    def test_策略_ask_要求二次确认(self, gateway_factory):
        engine = PolicyEngine(
            make_store([make_policy(id="gw.ask", effect="ask", match={})]),
            cache_size=0, decision_log=False,
            observer=DecisionObserver(enabled=False), inbox=False)
        gw = gateway_factory(policy_engine=engine)
        result = gw.check("web_search", {}, ABACContext(role=Role.ADMIN))
        assert result.allowed is False
        assert result.requires_confirmation is True

    def test_策略_allow_不放宽既有拒绝(self, gateway_factory):
        """**核心安全断言**：策略层不是权限来源，allow 不能越过 RBAC。"""
        engine = PolicyEngine(
            make_store([make_policy(id="gw.allow", effect="allow", match={})]),
            cache_size=0, decision_log=False,
            observer=DecisionObserver(enabled=False), inbox=False)
        gw = gateway_factory(policy_engine=engine)
        assert gw.check("file_read", {}, ABACContext(role=Role.GUEST)).allowed is False
        assert gw.check("system_format", {},
                        ABACContext(role=Role.DEVELOPER)).allowed is False

    def test_策略层异常回落既有判定(self, gateway_factory):
        class Boom:
            def check(self, *a, **k):
                raise RuntimeError("engine down")

        gw = gateway_factory(policy_engine=Boom(), policy_enabled=True)
        assert gw.check("web_search", {}, ABACContext(role=Role.GUEST)).allowed is True

    def test_环境开关可启用默认引擎(self, gateway_factory, monkeypatch):
        monkeypatch.setenv("CP_POLICY_GATEWAY_ENABLED", "1")
        gw = gateway_factory()
        assert gw._policy_enabled is True
        # 默认引擎只有内置不变量，本场景不命中 ⇒ 行为等同于未启用
        assert gw.check("web_search", {}, ABACContext(role=Role.GUEST)).allowed is True

    def test_策略层不改变降级模式(self, gateway_factory, tmp_path):
        engine = PolicyEngine(make_store(), cache_size=0, decision_log=False,
                              observer=DecisionObserver(enabled=False), inbox=False)
        gw = PermissionGateway(policy_path="/nonexistent/x.json",
                               policy_engine=engine)
        assert gw.is_degraded is True
        assert gw.check("rm -rf /", {}, ABACContext(role=Role.ADMIN)).allowed is False


# ════════════════════════════════════════════════════════════
#  引擎无网络副作用（P7.1-20 的静态与运行期证据）
# ════════════════════════════════════════════════════════════


FORBIDDEN_IMPORT_ROOTS = {
    "requests", "urllib2", "httpx", "aiohttp", "socket",
    "ftplib", "smtplib", "telnetlib", "subprocess", "selenium", "paramiko",
    "websocket", "websockets", "http.client", "urllib3",
}
#: ``urllib`` 只有 ``urllib.parse`` 是纯字符串处理（无 IO），显式放行；
#: 其余（``urllib.request`` / ``urllib.error`` / 裸 ``urllib``）禁。
FORBIDDEN_URLLIB = {"urllib", "urllib.request", "urllib.error", "urllib.response"}
#: 这些模块名是**标准库里的非网络模块**，与上面的根名同形，不能一刀切禁掉
_ALLOWED_MODULE_NAMES = {"http.client"}


def _is_forbidden_module(module: str) -> bool:
    name = str(module or "")
    if not name:
        return False
    if name in _ALLOWED_MODULE_NAMES:
        return True
    if name == "urllib.parse":
        return False
    if name in FORBIDDEN_URLLIB or name.startswith("urllib."):
        return True
    root = name.split(".")[0]
    return root in FORBIDDEN_IMPORT_ROOTS or name in FORBIDDEN_IMPORT_ROOTS


def _iter_policy_modules():
    for path in sorted(POLICY_PKG.glob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


class TestNoNetworkSideEffects:
    """「引擎不得提供 http.send 类能力」——用静态检查 + 运行期探针证明。"""

    def test_策略包不导入任何网络或子进程库(self):
        offenders = []
        for path, tree in _iter_policy_modules():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if _is_forbidden_module(alias.name):
                            offenders.append(f"{path.name}:{node.lineno} import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = str(node.module or "")
                    if _is_forbidden_module(module):
                        offenders.append(f"{path.name}:{node.lineno} from {module}")
        assert offenders == [], f"策略包出现网络/子进程导入: {offenders}"

    @pytest.mark.parametrize("module,forbidden", [
        ("requests", True), ("socket", True), ("subprocess", True),
        ("urllib", True), ("urllib.request", True), ("httpx", True),
        ("urllib.parse", False), ("json", False), ("http.client", True),
    ])
    def test_导入黑名单判定自身可区分_parse_与_request(self, module, forbidden):
        assert _is_forbidden_module(module) is forbidden

    def test_策略包不调用_open_写文件以外的副作用(self):
        """``open(..., "w"/"a")`` 只允许出现在明确的落盘模块里（决策日志/收件箱）。"""
        allowed = {"decisions.py", "inbox.py", "signing.py", "store.py", "simulator.py"}
        offenders = []
        for path, tree in _iter_policy_modules():
            if path.name in allowed:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id == "open":
                    modes = [arg for arg in node.args[1:] if isinstance(arg, ast.Constant)]
                    if any(str(getattr(m, "value", "")) and
                           any(ch in str(m.value) for ch in "wa+") for m in modes):
                        offenders.append(f"{path.name}:{node.lineno}")
        assert offenders == [], f"非落盘模块出现写文件: {offenders}"

    def test_运行期决策不触碰_socket(self, monkeypatch):
        called = []

        def _probe(*args, **kwargs):
            called.append(1)
            raise AssertionError("决策路径触碰了 socket")

        monkeypatch.setattr(socket, "socket", _probe)
        monkeypatch.setattr(socket, "create_connection", _probe)
        engine = PolicyEngine(make_store([make_policy(id="ns.deny", effect="deny")]),
                              cache_size=0, decision_log=False,
                              observer=DecisionObserver(enabled=False), inbox=False)
        for _ in range(5):
            engine.check(PolicyContext.build(
                capability_id="cp.x.y",
                capability={"trust": {"data_class": "secret"}},
                target={"external": True}))
        assert called == []

    def test_装载期拒绝_http_send_类策略(self):
        from agent.policy.models import PolicyValidationError
        with pytest.raises(PolicyValidationError):
            make_store().add(make_policy(
                match={"field": "attributes.x", "op": "eq", "value": "http.send"}))

    def test_引擎公开_API_无执行类方法(self):
        engine = PolicyEngine(make_store(), cache_size=0, decision_log=False,
                              observer=DecisionObserver(enabled=False), inbox=False)
        public = [name for name in dir(engine) if not name.startswith("_")]
        for banned in ("send", "request", "execute", "run", "call_http", "fetch"):
            assert banned not in public


# ════════════════════════════════════════════════════════════
#  仓库策略文件与门禁产物
# ════════════════════════════════════════════════════════════


class TestRepositoryPolicyAsset:
    def test_策略文件可装载且自检通过(self):
        store = make_store(path=str(REPO_ROOT / "data" / "policies" / "policies.json"),
                           autoload=True)
        assert store.problems == []
        assert store.validate_all() == []
        ids = [p.id for p in store.active()]
        assert "builtin.invariant.secret-egress-deny" in ids
        assert "sec.opaque-destructive-deny" in ids
        assert "gov.confidential-external-ask" in ids
        assert "ops.freeze-external-egress" in ids

    def test_策略文件不含禁用_token(self):
        raw = (REPO_ROOT / "data" / "policies" / "policies.json").read_text(
            encoding="utf-8")
        assert "http.send" not in raw.replace("http.send()", "")  # 只允许作为文档描述

    def test_存量未分级输入不被任何策略命中(self):
        """**零行为回归的机器可读证据**：未分级输入的判定必须是 matched=False。"""
        store = make_store(path=str(REPO_ROOT / "data" / "policies" / "policies.json"),
                           autoload=True)
        engine = PolicyEngine(store, cache_size=0, decision_log=False,
                              observer=DecisionObserver(enabled=False), inbox=False)
        for capability_id, external in (("cp.filesystem.local.read", False),
                                        ("cp.web.search", True),
                                        ("cp.mcp.github.issue", True)):
            ctx = PolicyContext.from_descriptor(_Unclassified(capability_id, external))
            decision = engine.check(ctx)
            assert decision.matched is False, f"{capability_id} 被意外覆盖"
            assert decision.effect == EFFECT_ALLOW

    def test_内置不变量在未分级时也不触发(self):
        store = make_store(path=str(REPO_ROOT / "data" / "policies" / "policies.json"),
                           autoload=True)
        engine = PolicyEngine(store, cache_size=0, decision_log=False,
                              observer=DecisionObserver(enabled=False), inbox=False)
        assert engine.check(PolicyContext.from_descriptor(
            _Unclassified("cp.x.y", True))).effect == EFFECT_ALLOW

    def test_分级_secret_且外部时拒绝(self):
        store = make_store(path=str(REPO_ROOT / "data" / "policies" / "policies.json"),
                           autoload=True)
        engine = PolicyEngine(store, cache_size=0, decision_log=False,
                              observer=DecisionObserver(enabled=False), inbox=False)
        decision = engine.check(PolicyContext.build(
            capability_id="cp.x.y",
            capability={"trust": {"data_class": "secret"}},
            target={"external": True}))
        assert decision.effect == EFFECT_DENY

    def test_运行时产物已入_gitignore(self):
        ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        for entry in ("data/policies/decisions.jsonl", "data/policies/inbox.jsonl",
                      "data/policies/policy_signing_key.pem"):
            assert entry in ignored, f"缺 .gitignore 条目: {entry}"

    def test_match_子集文档随包交付(self):
        assert (POLICY_PKG / "MATCH_SUBSET.md").exists()

    def test_策略目录_README_存在(self):
        assert (REPO_ROOT / "data" / "policies" / "README.md").exists()


class _Unclassified:
    """§3.2 descriptor 的最小替身：**未分级**（data_class/risk_level 均为 None）"""

    def __init__(self, capability_id: str, external: bool):
        self.capability_id = capability_id
        self.trust = type("T", (), {"data_class": None, "risk_level": None,
                                    "requires_approval": False})()
        self.origin = type("O", (), {"source_type": "builtin",
                                     "external_endpoint": external,
                                     "provenance": "verified"})()
        self.evolution = type("E", (), {"stage": None})()
        self.tenancy = type("N", (), {"tenant_id": "default"})()


# ════════════════════════════════════════════════════════════
#  端到端：决策 → 日志 → 模拟
# ════════════════════════════════════════════════════════════


class TestDecisionLogToSimulation:
    def test_真实决策流可被模拟器重放(self, tmp_path):
        from agent.policy.simulator import simulate
        log = DecisionLog(str(tmp_path / "decisions.jsonl"))
        store = make_store([make_policy(
            id="hist.deny", effect="deny",
            match={"field": "target.external", "op": "eq", "value": True})])
        engine = PolicyEngine(store, cache_size=0, decision_log=log,
                              observer=DecisionObserver(enabled=False), inbox=False)
        for actor in ("alice", "bob", "carol"):
            engine.check(PolicyContext.build(
                capability_id="cp.web.search", actor=actor,
                capability={"trust": {"data_class": "internal"}},
                target={"external": True, "host": "h"}))
        log.close()
        assert len(DecisionLog(str(tmp_path / "decisions.jsonl")).read()) == 3

        report = simulate(make_policy(id="hist.deny", version="2.0.0",
                                      effect="allow",
                                      match={"field": "target.external", "op": "eq",
                                             "value": True}),
                          engine=engine,
                          log_path=str(tmp_path / "decisions.jsonl"))
        assert report.total == 3
        assert report.deny_to_allow == 3
        assert report.verdict == "needs_ack"
        assert report.replay_drift == 0     # 同一策略库重放 ⇒ 基线应完全一致
