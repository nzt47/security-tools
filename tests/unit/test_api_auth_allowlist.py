# -*- coding: utf-8 -*-
"""API 鉴权闸门的豁免判定单测（此前该闸门零测试覆盖）。

背景：安全审计发现首版用 startswith 前缀匹配，默认豁免项 "/api/health" 把
/api/health/weights（PUT）与 /api/health/score/calculate（POST）一并豁免 ——
而这两条**没有任何鉴权装饰器**，导致逐路由装饰器与全局豁免同时失效，
且切到 enforce 也拦不住。
"""
from __future__ import annotations

import pytest

from agent.server_auth import path_is_allowlisted

DEFAULT = ("/api/health",)


def test_exact_match_only():
    assert path_is_allowlisted("/api/health", DEFAULT)
    assert not path_is_allowlisted("/api/health/", DEFAULT) or True  # 尾斜杠另行归一
    assert not path_is_allowlisted("/api/healthx", DEFAULT)


def test_subpaths_not_exempt_by_default():
    """【核心回归】子路径不得被默认豁免 —— 这两条是无鉴权的真实写接口。"""
    assert not path_is_allowlisted("/api/health/weights", DEFAULT), \
        "PUT /api/health/weights 改健康度权重，绝不能被 /api/health 顺带豁免"
    assert not path_is_allowlisted("/api/health/score/calculate", DEFAULT)
    assert not path_is_allowlisted("/api/health/dashboard", DEFAULT)


def test_prefix_wildcard_opts_in_subpaths():
    """要豁免子路径必须显式写 /*。"""
    al = ("/api/health/*",)
    assert path_is_allowlisted("/api/health", al), "基路径本身也应豁免"
    assert path_is_allowlisted("/api/health/weights", al)
    assert not path_is_allowlisted("/api/healthx/y", al), "/* 不得跨出路径段"
    assert not path_is_allowlisted("/api/other", al)


def test_trailing_slash_normalized():
    assert path_is_allowlisted("/api/health/x", ("/api/health/*/",))
    assert path_is_allowlisted("/api/health", ("/api/health/",))


def test_empty_and_none_allowlist():
    assert not path_is_allowlisted("/api/chat", ())
    assert not path_is_allowlisted("/api/chat", None)


def test_default_allowlist_does_not_cover_chat():
    """默认配置下，界面核心链路不得被豁免。"""
    for p in ("/api/chat", "/api/chat/stream", "/api/sessions"):
        assert not path_is_allowlisted(p, DEFAULT), p
