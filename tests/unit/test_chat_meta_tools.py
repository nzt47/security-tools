"""E1-3a 修补：交互原语在任何主线装配下都必须进工具白名单。

背景（2026-11-25 真机）：默认主线 `engineering` 的 `line_whitelist()` 里没有
`ask_user_question` / `present`，工作台对话因此**根本拿不到这两个工具**
（模型的思考里写「可用工具里没有 ask_user_question」）。纯函数单测 + 路由单测都发现不了，
只有真模型轮才暴露。这里钉住合并函数本身，真机由 `.local/probe_e1_3_e2e_llm.py` 覆盖。
"""
from __future__ import annotations

import pytest

from plugins.chat import merge_meta_tools
from agent.tool_router import META_ALWAYS_TOOLS


def test_主线白名单缺交互原语时补齐():
    merged, added = merge_meta_tools(["read_file", "edit"])
    assert added == len(META_ALWAYS_TOOLS)
    assert merged[:2] == ["read_file", "edit"]          # 原顺序不动
    assert set(META_ALWAYS_TOOLS) <= set(merged)


def test_已有部分时只补缺的():
    merged, added = merge_meta_tools(["read_file", "ask_user_question"])
    assert added == 1 and "present" in merged
    assert merged.count("ask_user_question") == 1       # 不重复


def test_都在时不追加():
    merged, added = merge_meta_tools(["read_file"] + list(META_ALWAYS_TOOLS))
    assert added == 0
    assert merged == ["read_file"] + list(META_ALWAYS_TOOLS)


def test_空白名单原样返回():
    assert merge_meta_tools([]) == ([], 0)
    assert merge_meta_tools(None) == (None, 0)


def test_交互原语确实只有两个():
    # 数量哨兵：以后往 META_ALWAYS_TOOLS 里加东西必须同批重派生路由基线（见 §11.228 D.1）
    assert META_ALWAYS_TOOLS == ("ask_user_question", "present")
