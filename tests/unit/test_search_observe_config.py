"""知识检索 KPI#1 语义查询观察埋点（复查补充 · B2）— 单元测试

覆盖：
  - 环境变量未设时读取 config.yaml learning.metrics.observe_knowledge_search
    （默认 true，复查接线后开启）
  - 环境变量优先级高于 config
  - 埋点异常静默（绝不阻塞检索主链路）
  - 开启后 record_semantic_query 被调用（saved_tokens=0，不改口径）

运行：python -m pytest tests/unit/test_search_observe_config.py -q
"""
import importlib

import pytest

import agent.knowledge.search as search_mod


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    """重置模块级缓存，隔离用例间的 env/config 读取"""
    monkeypatch.setattr(search_mod, "_OBSERVE_KNOWLEDGE_SEARCH", None)
    monkeypatch.setattr(search_mod, "_CONFIG_OBSERVE_CACHE", None)
    yield


def test_default_true_from_config():
    # config.yaml 默认已开启（复查接线）
    assert search_mod._observe_knowledge_search_enabled() is True


def test_env_overrides_config(monkeypatch):
    monkeypatch.setenv("LEARNING_METRICS_OBSERVE_KNOWLEDGE_SEARCH", "false")
    assert search_mod._observe_knowledge_search_enabled() is False


def test_env_true_overrides_config(monkeypatch):
    monkeypatch.setenv("LEARNING_METRICS_OBSERVE_KNOWLEDGE_SEARCH", "true")
    assert search_mod._observe_knowledge_search_enabled() is True


def test_invalid_env_falls_back_config(monkeypatch):
    monkeypatch.setenv("LEARNING_METRICS_OBSERVE_KNOWLEDGE_SEARCH", "banana")
    # 非 1/true/yes/on → 回退 config（默认 true）
    assert search_mod._observe_knowledge_search_enabled() is True


def test_emit_records_semantic_query(monkeypatch):
    recorded = {}

    class _FakeMetrics:
        def record_semantic_query(self, **kw):
            recorded.update(kw)

    monkeypatch.setattr(search_mod, "_OBSERVE_KNOWLEDGE_SEARCH", True)
    monkeypatch.setattr(
        "agent.learning_metrics.get_learning_metrics",
        lambda: _FakeMetrics())
    search_mod._emit_knowledge_semantic_metric(hit=True)
    assert recorded.get("hit") is True
    assert recorded.get("saved_tokens") == 0  # 不改 token 复用率口径


def test_emit_metrics_failure_is_silent(monkeypatch):
    def _boom():
        raise RuntimeError("metrics down")

    monkeypatch.setattr(search_mod, "_OBSERVE_KNOWLEDGE_SEARCH", True)
    monkeypatch.setattr("agent.learning_metrics.get_learning_metrics", _boom)
    # 埋点异常绝不抛出（不阻塞检索主链路）
    search_mod._emit_knowledge_semantic_metric(hit=False)


def test_emit_disabled_is_noop(monkeypatch):
    called = []

    monkeypatch.setattr(search_mod, "_OBSERVE_KNOWLEDGE_SEARCH", False)

    def _fake():
        called.append(1)
        raise AssertionError("关闭时不应调用 metrics")

    monkeypatch.setattr("agent.learning_metrics.get_learning_metrics", _fake)
    search_mod._emit_knowledge_semantic_metric(hit=True)
    assert called == []
