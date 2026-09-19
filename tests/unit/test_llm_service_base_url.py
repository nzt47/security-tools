"""守卫：``LLMService`` 的 ``base_url`` 解析优先级。

【为什么需要这个文件】
    2026-09-19 端到端验证实测发现一个**静默缺陷**：``LLMService.__init__`` 此前只有
    "显式入参 > provider 内置默认"，**环境变量被跳过**。于是同一份 ``.env`` 在不同链路
    生效不同：

      * ``plugins/chat.py:987`` 与 ``agent/orchestrator/lifecycle_manager.py:1193``
        **显式读了 env** ⇒ 改 ``.env`` 生效；
      * ``memory/memory_manager.py:251-256`` 构造时**未传 base_url** ⇒ 静默回退到
        硬编码的 ``https://api.deepseek.com``（``OPENAI_COMPAT``）⇒ 改 ``.env`` **不生效**。

    换网关 / 自建代理时，这会被误判成"模型坏了"。修复后优先级统一为
    **显式入参 > ``LLM_BASE_URL`` > ``DEEPSEEK_BASE_URL`` > provider 内置默认**。

【本用例锁死的不变量】
    1. 显式入参最高优先（既有行为，回归保护）；
    2. 无入参时读 env（**本次修复的行为**，且 ``LLM_BASE_URL`` 优先于 ``DEEPSEEK_BASE_URL``）；
    3. 无入参且无 env 时回落内置默认（``deepseek`` ⇒ ``https://api.deepseek.com``）；
    4. ``memory.MemoryManager`` 构造出的服务能读到 env（**真实路径**，不只是单测直构）。
"""

from __future__ import annotations

import importlib

import pytest

_ENV_KEYS = ("LLM_BASE_URL", "DEEPSEEK_BASE_URL")
_VALID_KEY = "sk-test-valid-key-12345"  # 满足 MIN_API_KEY_LENGTH=10


@pytest.fixture()
def clean_env(monkeypatch):
    """清掉两个 base_url 环境变量，保证用例之间互不污染。"""
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _service(**kwargs):
    import memory.llm_service as M

    return M.LLMService(api_key=_VALID_KEY, **kwargs)


def test_explicit_base_url_wins(clean_env):
    """显式入参优先级最高 —— 既有行为，回归保护。"""
    s = _service(provider="deepseek", base_url="https://explicit/v1")
    assert s._base_url == "https://explicit/v1"


def test_env_llm_base_url_is_used_when_no_explicit(clean_env):
    """**本次修复的核心**：未传 base_url 时必须读 ``LLM_BASE_URL``。

    修复前该断言会失败（会得到内置默认 ``https://api.deepseek.com``）。
    """
    clean_env.setenv("LLM_BASE_URL", "https://gw.internal/v1")
    s = _service(provider="deepseek")
    assert s._base_url == "https://gw.internal/v1"


def test_env_llm_base_url_wins_over_deepseek_variant(clean_env):
    """``LLM_BASE_URL`` 优先于 ``DEEPSEEK_BASE_URL``（与 ``plugins/chat.py:987`` 读法一致）。"""
    clean_env.setenv("LLM_BASE_URL", "https://primary/v1")
    clean_env.setenv("DEEPSEEK_BASE_URL", "https://secondary/v1")
    s = _service(provider="deepseek")
    assert s._base_url == "https://primary/v1"


def test_deepseek_base_url_used_as_fallback_alias(clean_env):
    """只有 ``DEEPSEEK_BASE_URL`` 时也要生效。"""
    clean_env.setenv("DEEPSEEK_BASE_URL", "https://ds-only/v1")
    s = _service(provider="deepseek")
    assert s._base_url == "https://ds-only/v1"


def test_explicit_beats_env(clean_env):
    """显式入参压过 env（优先级顺序不能反）。"""
    clean_env.setenv("LLM_BASE_URL", "https://from-env/v1")
    s = _service(provider="deepseek", base_url="https://from-arg/v1")
    assert s._base_url == "https://from-arg/v1"


def test_builtin_default_still_applies_without_env(clean_env):
    """无入参、无 env ⇒ 仍回落 provider 内置默认（不因本次改动而丢失）。"""
    s = _service(provider="deepseek")
    assert s._base_url == "https://api.deepseek.com"


def test_provider_without_builtin_default_and_no_env_is_empty(clean_env):
    """既无内置默认又无 env 时为空串 —— 说明 env 回退不会凭空造出地址。"""
    s = _service(provider="openai")
    assert s._base_url == ""


def test_env_enables_previously_unmapped_provider(clean_env):
    """env 能让"无内置默认"的 provider 也拿到地址 —— 这是修复带来的新能力。"""
    clean_env.setenv("LLM_BASE_URL", "https://vendor-gw/v1")
    s = _service(provider="some-vendor")
    assert s._base_url == "https://vendor-gw/v1"


def test_whitespace_only_env_is_ignored(clean_env):
    """纯空白 env 视为未设置（不能把 ' ' 当地址）。"""
    clean_env.setenv("LLM_BASE_URL", "   ")
    s = _service(provider="deepseek")
    assert s._base_url == "https://api.deepseek.com"


def test_trailing_slash_normalized(clean_env):
    """尾斜杠被剥离（既有行为，回归保护）。"""
    clean_env.setenv("LLM_BASE_URL", "https://gw.internal/v1/")
    s = _service(provider="deepseek")
    assert s._base_url == "https://gw.internal/v1"


def test_memory_manager_reads_env(clean_env):
    """**真实路径**：``MemoryManager`` 构造出的服务必须能读到 env。

    这一条是本缺陷的原始现场 —— `memory/memory_manager.py:251-256` 未传 ``base_url``，
    修复前它拿到的永远是硬编码默认值。
    """
    clean_env.setenv("LLM_BASE_URL", "https://gw.internal/v1")
    import memory.llm_service as M

    importlib.reload(M)
    from memory.memory_manager import MemoryManager

    mm = MemoryManager(
        config={
            "llm": {
                "api_key": _VALID_KEY,
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "timeout": 30,
            }
        }
    )
    assert mm._llm_service is not None, "MemoryManager 未构造出 LLMService（检查 api_key 长度校验）"
    assert mm._llm_service._base_url == "https://gw.internal/v1"
