# -*- coding: utf-8 -*-
"""core/local_llm 引擎分支测试：vLLM（OpenAI 兼容）已实现，探活端点与引擎同源。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.local_llm as mod


class _Resp:
    def __init__(self, data): self._data = data
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self): return self._data


class _Session:
    def __init__(self, data, captured): self._data = data; self._capt = captured
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def get(self, url, timeout=None): self._capt.append(("GET", url)); return _Resp(self._data)
    def post(self, url, json=None, timeout=None): self._capt.append(("POST", url, json)); return _Resp(self._data)


class _AiohttpStub:
    def __init__(self, data): self.data = data; self.captured = []
    def ClientSession(self): return _Session(self.data, self.captured)


def _patch(monkeypatch, data):
    stub = _AiohttpStub(data)
    monkeypatch.setattr(mod, "aiohttp", stub)
    return stub


def test_vllm_generate_parses_choices_text(monkeypatch):
    stub = _patch(monkeypatch, {"choices": [{"text": "你好"}]})
    llm = mod.LocalLLM(engine="vllm", model="m")
    llm._available = True
    assert asyncio.run(llm.generate("hi")) == "你好"
    posts = [c for c in stub.captured if c[0] == "POST"]
    assert posts and posts[0][1].endswith("/v1/completions")
    assert posts[0][2]["model"] == "m" and posts[0][2]["stream"] is False


def test_vllm_generate_accepts_message_content(monkeypatch):
    _patch(monkeypatch, {"choices": [{"message": {"content": "hi"}}]})
    llm = mod.LocalLLM(engine="vllm", model="m"); llm._available = True
    assert asyncio.run(llm.generate("x")) == "hi"


def test_vllm_generate_empty_choices(monkeypatch):
    _patch(monkeypatch, {"choices": []})
    llm = mod.LocalLLM(engine="vllm", model="m"); llm._available = True
    assert asyncio.run(llm.generate("x")) == ""


def test_probe_endpoint_is_engine_specific(monkeypatch):
    stub = _patch(monkeypatch, {})
    assert asyncio.run(mod.LocalLLM(engine="vllm").check_available()) is True
    assert any(c[0] == "GET" and c[1].endswith("/v1/models") for c in stub.captured)
    stub2 = _patch(monkeypatch, {})
    asyncio.run(mod.LocalLLM(engine="ollama").check_available())
    assert any(c[0] == "GET" and c[1].endswith("/api/tags") for c in stub2.captured)


def test_unknown_engine_fail_closed(monkeypatch):
    _patch(monkeypatch, {})
    llm = mod.LocalLLM(engine="llamacpp"); llm._available = True
    assert asyncio.run(llm.generate("x")) is None


def test_subagent_local_adapter_supports_vllm():
    from agent.subagent.local_inference import SUPPORTED_LOCAL_ENGINES, local_status
    assert "vllm" in SUPPORTED_LOCAL_ENGINES
    view = local_status({"CP_SUBAGENT_LOCAL_ENGINE": "vllm"})
    assert view["engine"] == "vllm" and view["engine_supported"] is True
