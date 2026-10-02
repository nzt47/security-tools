# -*- coding: utf-8 -*-
"""loader 的六个 SKILLS_* 环境旋钮（§10 D-1 的回归钉子）

【为什么要有这个测试】
deploy/k8s/deployment.yaml 的 ConfigMap 与多份 runbook 长期教运维设置这六个变量，
但 agent/ 下**没有任何代码读它们** ⇒ 设了完全无效（实测 candidate_limit 恒为 0）。
2026-10-02 把它们接进 SkillLoader 的默认值。这个测试钉住两件事：

1. **不设 env 时行为逐字不变**（False/False/False/"none"/True/0）—— 否则等于偷偷改了检索行为；
2. **设了 env 就真的生效**，且显式传参永远优先于 env（调用方说了算）。

【不易】第 1 条是这套改动的安全底线：没有它，任何一次"回退值写错"都会静默改变线上检索结果。
【变易】解析函数是纯函数，直接单测；另有一条端到端断言（match → _tfidf_scan 真的收到 env 值）。
"""
from __future__ import annotations

import pytest

from agent.skills_mgmt import loader as L

ENV_NAMES = (
    "SKILLS_USE_INVERTED_INDEX",
    "SKILLS_CANDIDATE_LIMIT",
    "SKILLS_USE_VECTOR",
    "SKILLS_FUSION_MODE",
    "SKILLS_USE_BM25",
    "SKILLS_USE_RERANKER",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """每个用例都从"六个变量全未设"出发，避免宿主 .env 影响断言"""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_identical_to_pre_change_behaviour():
    """未设 env ⇒ 回退值与接入前逐字一致（False/False/False/none/True/0）"""
    assert L._resolve_use_vector(None) is False
    assert L._resolve_use_bm25(None) is False
    assert L._resolve_use_reranker(None) is False
    assert L._resolve_fusion_mode(None) == "none"
    assert L._resolve_use_inverted_index(None) is True
    assert L._resolve_candidate_limit(None) == 0


def test_env_values_are_read(monkeypatch):
    """设了 env 就生效（这是 §10 D-1 的全部目的）"""
    monkeypatch.setenv("SKILLS_USE_VECTOR", "true")
    monkeypatch.setenv("SKILLS_USE_BM25", "1")
    monkeypatch.setenv("SKILLS_USE_RERANKER", "off")
    monkeypatch.setenv("SKILLS_FUSION_MODE", "RRF")
    monkeypatch.setenv("SKILLS_USE_INVERTED_INDEX", "false")
    monkeypatch.setenv("SKILLS_CANDIDATE_LIMIT", "200")

    assert L._resolve_use_vector(None) is True
    assert L._resolve_use_bm25(None) is True
    assert L._resolve_use_reranker(None) is False
    assert L._resolve_fusion_mode(None) == "rrf"      # 大小写不敏感
    assert L._resolve_use_inverted_index(None) is False
    assert L._resolve_candidate_limit(None) == 200


def test_explicit_argument_beats_env(monkeypatch):
    """显式传参优先于 env —— env 只提供默认值，不能覆盖调用方的决定"""
    monkeypatch.setenv("SKILLS_USE_INVERTED_INDEX", "false")
    monkeypatch.setenv("SKILLS_CANDIDATE_LIMIT", "200")
    monkeypatch.setenv("SKILLS_USE_VECTOR", "true")

    assert L._resolve_use_inverted_index(True) is True
    assert L._resolve_candidate_limit(0) == 0
    assert L._resolve_use_vector(False) is False


@pytest.mark.parametrize("bad,attr,expected", [
    ("abc", "limit", 0),          # 非数字 ⇒ 回退默认
    ("-5", "limit", 0),           # 小于下限 ⇒ 回退默认（不是"截断成 0 却看起来生效"）
    ("maybe", "inverted", True),  # 无法识别为布尔 ⇒ 回退默认 True（不静默取 False）
])
def test_invalid_env_falls_back_without_crash(monkeypatch, bad, attr, expected):
    """非法取值不许 crash、也不许静默改变行为：一律回退默认值"""
    if attr == "limit":
        monkeypatch.setenv("SKILLS_CANDIDATE_LIMIT", bad)
        assert L._resolve_candidate_limit(None) == expected
    else:
        monkeypatch.setenv("SKILLS_USE_INVERTED_INDEX", bad)
        assert L._resolve_use_inverted_index(None) is expected


def test_invalid_fusion_mode_falls_back(monkeypatch):
    monkeypatch.setenv("SKILLS_FUSION_MODE", "bogus")
    assert L._resolve_fusion_mode(None) == "none"


def test_match_forwards_env_candidate_limit_to_scan(monkeypatch):
    """端到端：match() 未显式传参时，env 值真的走到了 _tfidf_scan（不是只存在解析函数里）"""
    monkeypatch.setenv("SKILLS_CANDIDATE_LIMIT", "200")
    monkeypatch.setenv("SKILLS_USE_INVERTED_INDEX", "true")

    seen = {}
    real_scan = L.SkillLoader._tfidf_scan

    def spy(self, index, query_tokens, enabled_only, min_score,
            use_inverted_index, candidate_limit=None):
        seen["candidate_limit"] = candidate_limit
        seen["use_inverted_index"] = use_inverted_index
        return real_scan(self, index, query_tokens, enabled_only, min_score,
                         use_inverted_index, candidate_limit)

    monkeypatch.setattr(L.SkillLoader, "_tfidf_scan", spy)
    loader = L.SkillLoader()
    loader.match("解析 PDF 文件", top_k=3)

    assert seen.get("candidate_limit") == 200, (
        "env 里的 candidate_limit 没有传到扫描层 ⇒ 旋钮仍未真正生效：%s" % seen)
    assert seen.get("use_inverted_index") is True
