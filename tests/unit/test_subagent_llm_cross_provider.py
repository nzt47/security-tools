# -*- coding: utf-8 -*-
"""跨 provider LLM：opt-in + host 白名单 + 到达端密钥；失败一律回退且可见。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.subagent import llm_factory as lf
from agent.subagent.container import SubagentConfig

_CAPTURED = []


class _FakeLLMService:
    def __init__(self, provider="", api_key="", model="", timeout=30, base_url="", **kw):
        self.provider = provider
        self.model = model
        self.base_url = base_url
        _CAPTURED.append((provider, model, base_url, api_key))


class _Parent:
    model = "parent-model"
    provider = "openai"


@pytest.fixture(autouse=True)
def _patch_service(monkeypatch):
    import memory.llm_service as svc

    _CAPTURED.clear()
    monkeypatch.setattr(svc, "LLMService", _FakeLLMService)


def _override(**over):
    data = {"provider": "anthropic", "base_url": "https://api.partner.com/v1",
            "model": "claude-x",
            "api_key_ref": {"source": "partner", "name": "KEY", "env_var": "PARTNER_KEY"}}
    data.update(over)
    return data


def _env(**over):
    env = {lf.ENV_LLM_ALLOW: "api.partner.com", "PARTNER_KEY": "sk-" + "x" * 40}
    env.update(over)
    return env


def test_空覆盖返回None():
    assert lf.resolve_cross_provider_llm(None) is None
    assert lf.resolve_cross_provider_llm({}) is None


def test_成功构造并沿用白名单与到达端密钥():
    res = lf.resolve_cross_provider_llm(_override(), parent_llm=_Parent(), environ=_env())
    assert res is not None and res.source == "cross-provider"
    assert res.model == "claude-x" and "claude-x" in res.to_dict().values()
    assert _CAPTURED == [("anthropic", "claude-x", "https://api.partner.com/v1",
                          "sk-" + "x" * 40)]
    assert "sk-" not in str(res.to_dict()), "投影里绝不能出现密钥"


def test_白名单未配置一律拒绝并回退():
    env = _env()
    env[lf.ENV_LLM_ALLOW] = ""
    res = lf.resolve_cross_provider_llm(_override(), parent_llm=_Parent(), environ=env)
    assert res.source == "fallback-after-error" and res.error
    assert _CAPTURED == []


def test_host不在白名单被拒():
    env = _env()
    env[lf.ENV_LLM_ALLOW] = "other.com"
    res = lf.resolve_cross_provider_llm(_override(), parent_llm=_Parent(), environ=env)
    assert res.source == "fallback-after-error" and "白名单" in res.error
    assert _CAPTURED == []


def test_到达端无密钥被拒():
    env = _env()
    env.pop("PARTNER_KEY")
    res = lf.resolve_cross_provider_llm(_override(), parent_llm=_Parent(), environ=env)
    assert res.source == "fallback-after-error" and "api_key_ref" in res.error
    assert _CAPTURED == []


def test_缺字段被拒():
    res = lf.resolve_cross_provider_llm(_override(provider=""), parent_llm=_Parent(),
                                        environ=_env())
    assert res.source == "fallback-after-error"
    assert _CAPTURED == []


def test_配置默认不接入() :
    assert SubagentConfig(name="x", model_id="m").llm_override is None
