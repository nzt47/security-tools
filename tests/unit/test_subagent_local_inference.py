"""本地推理执行档守卫（agent/subagent/local_inference.py + bundle/executor/路由接线）

本地推理 = 分身的第三执行后端（断网可跑）。本文件把它钉成可证伪断言
（回退接线 ⇒ 立刻红）：

  L1 后端词表：local 在 SUPPORTED_BACKENDS；未知后端仍 fail-closed；
  L2 后端映射：resolve_backend(local) 落到本地通道（注入桩优先），不回落 inproc；
  L3 缺省通道：注入 local_channel 即选它；显式开启 env 才自动构造；关着时旧行为；
  L4 协议等同：本地通道走同一套 task_file → JSON Lines（tier=jsonl）；
  L5 构造期零网络：构造本地通道不触达 LocalLLM.generate（探活只在调用时）；
  L6 不假装：未知引擎 fail-closed；adapter.provider 恒 "local"；status 不含端点值；
  L7 入出口认识 local：/api/subagent/list 的 channel 段反映 local；
  L8 能力面：local_inference=OWNED 且证据模块符号真实存在。

全部用注入替身，不碰真 Ollama、不起网络、不 import app_server。
"""
from __future__ import annotations

import json

import pytest
from flask import Flask

from agent.server_routes import routes_subagent as routes_module  # noqa: F401  (路由注册)
from agent.server_routes.routes_subagent import register_routes
from agent.subagent.bundle import (BACKEND_LOCAL, SUPPORTED_BACKENDS,
                                   UnsupportedBackend, get_backend, resolve_backend)
from agent.subagent.channel import (TIER_JSONL, ChannelInvocation, build_cli_argv,
                                    resolve_channel_output)
from agent.subagent.executor import (DelegationExecutor,
                                      LocalInferenceChannelExecutor,
                                      build_local_channel)
from agent.subagent.local_inference import (LocalInferenceError, LocalLLMAdapter,
                                            local_backend_enabled, local_status)


class _FakeLocal:
    """LocalLLM 替身：async generate（与 core.local_llm.LocalLLM 同形）"""

    def __init__(self, reply: str = '{"status":"done","summary":"ok"}') -> None:
        self._model = "fake-local"
        self.reply = reply
        self.calls: list = []

    async def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.reply


def _invocation(tmp_path, *, max_turns: int = 2) -> ChannelInvocation:
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"goal": "把设计稿抽取为步骤", "constraints": ["只读"]},
                               ensure_ascii=False), encoding="utf-8")
    argv = build_cli_argv("internal-llm", str(task), max_turns=max_turns)
    return ChannelInvocation(argv=argv, task_file=str(task), max_turns=max_turns,
                             timeout_seconds=30.0)


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    """每个用例从"两个通道都没配"的干净环境开始（避免宿主 .env 串味）"""
    monkeypatch.delenv("CP_SUBAGENT_AGENT_CLI", raising=False)
    monkeypatch.delenv("CP_SUBAGENT_LOCAL_ENABLED", raising=False)
    monkeypatch.delenv("CP_SUBAGENT_LOCAL_ENGINE", raising=False)
    monkeypatch.delenv("CP_SUBAGENT_LOCAL_MODEL", raising=False)
    monkeypatch.delenv("CP_SUBAGENT_LOCAL_API_BASE", raising=False)


# ════════════════════════════════════════════════════════════
#  L1 / L2 后端词表与映射
# ════════════════════════════════════════════════════════════


class TestL1BackendWordlist:
    def test_local_在后端词表且可解析(self):
        assert BACKEND_LOCAL == "local"
        assert BACKEND_LOCAL in SUPPORTED_BACKENDS
        assert get_backend({"runtime": {"backend": "local"}}) == "local"

    def test_未知后端仍然_fail_closed(self):
        with pytest.raises(UnsupportedBackend):
            get_backend({"runtime": {"backend": "container-x"}})


class TestL2ResolveBackend:
    def test_注入桩优先_不回落尝试(self):
        stub = object()
        ex = resolve_backend({"runtime": {"backend": "local"}}, channel=stub)
        assert ex._channel is stub, "resolve_backend(local) 没有使用注入的本地通道"

    def test_无注入时构造本地通道而非_inproc(self):
        ex = resolve_backend({"runtime": {"backend": "local"}})
        assert isinstance(ex._channel, LocalInferenceChannelExecutor), (
            "resolve_backend(local) 未落本地通道（回落到 inproc 就会红）")
        assert getattr(ex._llm, "provider", "") == "local"
        assert ex._llm is ex._channel.llm


# ════════════════════════════════════════════════════════════
#  L3 缺省通道选择
# ════════════════════════════════════════════════════════════


class TestL3DefaultChannel:
    def test_注入_local_channel_即选它(self):
        stub = object()
        ex = DelegationExecutor(local_channel=stub)
        assert ex._default_channel() is stub

    def test_默认关闭时旧行为_不自动走本地(self):
        assert local_backend_enabled() is False
        ex = DelegationExecutor()
        assert not isinstance(ex._channel, LocalInferenceChannelExecutor), (
            "本地档未开启却自动选了本地通道（默认行为被改坏）")

    def test_显式开启才自动构造本地通道(self, monkeypatch):
        monkeypatch.setenv("CP_SUBAGENT_LOCAL_ENABLED", "1")
        ex = DelegationExecutor()
        assert isinstance(ex._channel, LocalInferenceChannelExecutor)
        assert getattr(ex._llm, "provider", "") == "local", (
            "本地 adapter 未注入 llm ⇒ 第 3 级 LLM 抽取在本地档不可用")


# ════════════════════════════════════════════════════════════
#  L4 协议等同（离线、注入替身）
# ════════════════════════════════════════════════════════════


class TestL4ProtocolParity:
    def test_本地通道产出_jsonl_且读到_task_file(self, tmp_path):
        fake = _FakeLocal('{"status":"done","summary":"ok"}')
        channel = LocalInferenceChannelExecutor(LocalLLMAdapter(fake))
        inv = _invocation(tmp_path)
        out = resolve_channel_output(lambda: channel(inv), invocation=inv)
        assert out.ok is True and out.tier == TIER_JSONL
        assert out.payload.get("status") == "done"
        assert fake.calls, "本地 LLM 未被调用"
        assert "把设计稿抽取为步骤" in fake.calls[0], "task_file 内容未进入 prompt"

    def test_adapter_压平_system_与多轮(self):
        fake = _FakeLocal("plain")
        adapter = LocalLLMAdapter(fake, model="fake-local")
        text = adapter.chat([{"role": "user", "content": "你好"}], system_prompt="SYS")
        assert text == "plain"
        assert adapter.provider == "local"
        assert adapter.model == "fake-local"
        prompt = fake.calls[0]
        assert prompt.startswith("[system]") and "SYS" in prompt and "[user]" in prompt


# ════════════════════════════════════════════════════════════
#  L5 构造期零网络 / L6 不假装
# ════════════════════════════════════════════════════════════


class TestL5NoNetworkAtConstruction:
    def test_构造本地通道不触达_generate(self):
        fake = _FakeLocal()
        channel = build_local_channel(local=fake)
        assert fake.calls == [], "构造期就调用了 generate（探活/下载应只在真正调用时发生）"
        assert isinstance(channel, LocalInferenceChannelExecutor)


class TestL6Honest:
    def test_未知引擎_fail_closed(self):
        with pytest.raises(LocalInferenceError) as ei:
            build_local_channel(engine="vllm", local=_FakeLocal())
        assert ei.value.code == "E_LOCAL_ENGINE_UNSUPPORTED"

    def test_status_不泄漏端点值(self):
        status = local_status({"CP_SUBAGENT_LOCAL_API_BASE": "http://internal.example:11434",
                               "CP_SUBAGENT_LOCAL_ENABLED": "1"})
        assert status["enabled"] is True
        assert "internal.example" not in json.dumps(status)

    def test_enabled_开关解析(self):
        assert local_backend_enabled({"CP_SUBAGENT_LOCAL_ENABLED": "1"}) is True
        assert local_backend_enabled({"CP_SUBAGENT_LOCAL_ENABLED": "true"}) is True
        assert local_backend_enabled({"CP_SUBAGENT_LOCAL_ENABLED": "0"}) is False
        assert local_backend_enabled({}) is False


# ════════════════════════════════════════════════════════════
#  L7 入出口认识 local（最小 Flask app + 替身）
# ════════════════════════════════════════════════════════════


class _FakeYunshu:
    def __init__(self, llm=None):
        self._llm = llm
        self._subagent_mgr = None

    def list_subagents(self):
        return []


def _client():
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app, type("S", (), {"Yunshu": _FakeYunshu()})())
    return app.test_client()


class TestL7RouteChannelInfo:
    def test_未开启本地且无_llm_cli_则通道不可用(self):
        data = _client().get("/api/subagent/list").get_json()["data"]
        assert data["channel"]["local"] is False
        assert data["channel"]["ok"] is False

    def test_显式开启本地后通道可用(self, monkeypatch):
        monkeypatch.setenv("CP_SUBAGENT_LOCAL_ENABLED", "1")
        data = _client().get("/api/subagent/list").get_json()["data"]
        assert data["channel"]["local"] is True
        assert data["channel"]["ok"] is True, (
            "路由不认本地档 ⇒ 断网档在界面上等于没做（仍会被 E_DELEGATION_NO_CHANNEL 拒）")


# ════════════════════════════════════════════════════════════
#  L8 能力面（反幻觉 + 符号级）
# ════════════════════════════════════════════════════════════


class TestL8Capabilities:
    def test_local_inference_为_owned_且证据符号存在(self):
        from agent.subagent.capabilities import (OWNED, SOVEREIGNTY_FACES,
                                                 verify_faces)
        face = next(f for f in SOVEREIGNTY_FACES if f.key == "local_inference")
        assert face.state == OWNED
        assert verify_faces() == [], "能力面自检不通过（证据文件缺失/字段为空）"
        # 符号级：光把状态改 OWNED 而删掉实现，这里立刻红
        from agent.subagent.bundle import BACKEND_LOCAL as backend
        from agent.subagent.executor import LocalInferenceChannelExecutor as ex
        assert backend == "local" and ex is not None
