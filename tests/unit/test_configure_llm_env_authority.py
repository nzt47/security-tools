"""configure_llm 的分层配置策略测试：**.env 为部署级权威（有值即优先）**

线上问题（2026-09-19 实测）：`agent/data/network_config.json` 里 llm 段仍是模板遗留值
（provider=openai, model=gpt-4），启动时 `network_config.apply_to_app()` 会把这两个值
**显式传入** `configure_llm()`；而旧实现只在"参数为空"时读 .env ⇒ gpt-4 盖掉 .env 的
deepseek 配置 ⇒ `Yunshu._llm` 用 gpt-4，工作台对话用 deepseek（两条链路两个模型），
子代理真委派必然 400：``The supported API model names are deepseek-flash, deepseek-v4-pro,
but you passed gpt-4``。

本方法 docstring 早就写明"`.env` 有值时环境变量优先（部署级配置权威）"，
故这里锁死该策略：
  1. .env 有值 ⇒ 覆盖调用方传入值（并留一条 warning 记录被覆盖的字段）；
  2. .env 为空 ⇒ 沿用调用方传入值（UI 修改在 .env 未配置时生效）；
  3. 两者都没有 api_key ⇒ 明确报错，不静默用错模型。
"""
from __future__ import annotations

import pytest

from agent.orchestrator.lifecycle_manager import LifecycleManager


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)


class _FakeLLM:
    """记录构造入参的 LLM 替身（只实现 configure_llm 需要的形状）"""

    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        _FakeLLM.last_kwargs = dict(kwargs)
        self.provider = kwargs.get("provider")
        self.model = kwargs.get("model")
        self.api_key = kwargs.get("api_key")
        self._base_url = kwargs.get("base_url", "")

    def _get_client(self):
        return object()


@pytest.fixture
def mgr():
    """只装配 configure_llm 所需的最小依赖（不跑完整核心系统初始化）

    configure_llm 收尾会 `_memory.clear_memory()` 与 `_reflection_history.clear()`，
    故这两个也要给替身（否则测到的是"依赖缺失"而不是被测的分层策略）。
    """
    m = object.__new__(LifecycleManager)
    m._llm = None
    m._llm_pro = None
    m._model_router = None
    m._llm_service_factory = _FakeLLM
    m._tool_calling_service = None
    m._tool_calling_service_factory = None
    m._config = {"tool_calling": {"enabled": False}, "subagent": {"enabled": False}}
    m._memory = type("M", (), {
        "_llm_service": None,
        "_summarizer": type("S", (), {"_llm": None})(),
        "clear_memory": lambda self=None: None,
    })()
    m._reflection_history = type("R", (), {"clear": lambda self=None: None})()
    return m


class TestEnvAuthority:
    def test_env_覆盖调用方传入的模板遗留值(self, mgr, monkeypatch):
        """network_config 传入 openai/gpt-4，.env 是 deepseek/deepseek-v4-flash ⇒ 用 env"""
        monkeypatch.setenv("LLM_PROVIDER", "DeepSeek")
        monkeypatch.setenv("LLM_API_KEY", "sk-env-" + "a" * 30)
        monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")

        result = mgr.configure_llm(provider="openai", api_key="sk-stale-key-1234567890",
                                   model="gpt-4", base_url="https://api.openai.com/v1")
        assert result.get("ok") is True
        assert _FakeLLM.last_kwargs["model"] == "deepseek-v4-flash"   # 不再是 gpt-4
        assert _FakeLLM.last_kwargs["provider"] == "deepseek"
        assert _FakeLLM.last_kwargs["base_url"] == "https://api.deepseek.com/v1"
        assert _FakeLLM.last_kwargs["api_key"].startswith("sk-env-")
        # 生效对象与记忆摘要器都指向同一个 LLM（避免"面板看 A、摘要用 B"）
        assert mgr._llm is not None
        assert mgr._memory._llm_service is mgr._llm
        assert mgr._memory._summarizer._llm is mgr._llm

    def test_env_为空时沿用调用方传入值(self, mgr, monkeypatch):
        """UI 修改生效的前提：.env 未配置该项"""
        monkeypatch.setenv("LLM_API_KEY", "")   # 空串 = 未配置
        result = mgr.configure_llm(provider="openai", api_key="sk-ui-key-1234567890",
                                   model="gpt-4o", base_url="https://api.openai.com/v1")
        assert result.get("ok") is True
        assert _FakeLLM.last_kwargs["model"] == "gpt-4o"
        assert _FakeLLM.last_kwargs["provider"] == "openai"

    def test_部分字段由_env_覆盖_其余沿用调用方(self, mgr, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "deepseek-chat")   # 只覆盖模型
        result = mgr.configure_llm(provider="openai", api_key="sk-x-1234567890", model="gpt-4")
        assert result.get("ok") is True
        assert _FakeLLM.last_kwargs["model"] == "deepseek-chat"
        assert _FakeLLM.last_kwargs["provider"] == "openai"   # 未被 .env 覆盖

    def test_无_api_key_时明确报错(self, mgr):
        result = mgr.configure_llm(provider="openai", model="gpt-4")
        assert result.get("ok") is False
        assert "API Key" in result.get("error", "")
