# -*- coding: utf-8 -*-
"""P5：LLM 重写的**严格引用校验**（`agent/skills_mgmt/transform/rewrite.py`）。

守四件事：
1. quote 必须逐字出现在原文（归一化空白/引号后），**编造的一句都不留**；
2. 行号由我们按 quote 反查，不信模型给的；
3. 一条都验不过 ⇒ ok=False + 明确原因（不给"看起来像样"的编造）；
4. 没有配 LLM ⇒ 明确说"没有可用的 LLM"，不静默降级成规则草稿。
"""
from __future__ import annotations

import json

import pytest

from agent.skills_mgmt.transform import rewrite as rw

SRC = """---
id: demo
name: 演示技能
---
# 演示技能

## 何时使用

- 需要在 Windows 上把路径翻成 WSL 格式时

## 步骤

- 先用 uname -r 判断是不是 WSL
- 再按盘符拼 /mnt/c/ 前缀
"""


def _chat(payload):
    def _call(prompt, system):
        assert "JSON" in (system or "")
        return json.dumps(payload, ensure_ascii=False)
    return _call


def _line_of(needle: str) -> int:
    for i, line in enumerate(SRC.splitlines(), start=1):
        if needle in line:
            return i
    raise AssertionError(needle)


def test_定位逐字片段():
    hit = rw.locate(SRC, "先用 uname -r 判断是不是 WSL")
    assert hit is not None
    line, source = hit
    assert line == _line_of("先用 uname -r 判断是不是 WSL") and "uname -r" in source
    assert rw.locate(SRC, "这句话原文里根本没有") is None
    assert rw.locate(SRC, "x" * (rw.MAX_QUOTE_CHARS + 1)) is None


def test_行尾片段也能定位():
    hit = rw.locate(SRC, "再按盘符拼 /mnt/c/ 前缀")
    assert hit is not None and hit[0] == _line_of("再按盘符拼 /mnt/c/ 前缀")


def test_编造的条目被丢弃():
    items = [
        {"section": "steps", "text": "先判断是否 WSL", "quote": "先用 uname -r 判断是不是 WSL"},
        {"section": "steps", "text": "还要设置注册表", "quote": "修改 HKCU\\Software 的注册表项"},
        {"section": "example", "text": "示例", "quote": "wslpath -a C:\\tmp"},
    ]
    out = rw.verify_items(SRC, items)
    assert len(out["kept"]) == 1
    assert out["kept"][0]["line"] == _line_of("先用 uname -r 判断是不是 WSL")
    assert len(out["dropped"]) == 2
    assert all("找不到" in d["reason"] for d in out["dropped"])


def test_section_白名单():
    out = rw.verify_items(SRC, [{"section": "secret", "text": "x", "quote": "先用 uname -r 判断是不是 WSL"}])
    assert out["kept"] == [] and out["dropped"][0]["reason"] == "section 不在白名单"


def test_draft_成功并渲染卡片():
    payload = {"items": [
        {"section": "when_to_use", "text": "在 Windows 需要 WSL 路径时", "quote": "需要在 Windows 上把路径翻成 WSL 格式时"},
        {"section": "steps", "text": "先判 WSL 再拼 /mnt/c", "quote": "先用 uname -r 判断是不是 WSL"},
        {"section": "steps", "text": "编的", "quote": "原文没有这句"},
    ]}
    out = rw.draft_sections(SRC, title="演示技能", chat=_chat(payload))
    assert out["ok"] is True
    assert out["stats"] == {"total": 3, "verified": 2}
    assert len(out["sections"]["when_to_use"]) == 1
    assert len(out["sections"]["steps"]) == 1
    assert "verified 2 / 3" in out["card_md"]
    assert "未通过校验" in out["card_md"]
    assert "编的" in out["card_md"], "未通过的条目必须在卡片里留痕（计数 + 摘要）"


def test_draft_全部验不过就明确失败():
    out = rw.draft_sections(SRC, chat=_chat({"items": [{"section": "steps", "text": "编", "quote": "不存在的引用"}]}))
    assert out["ok"] is False
    assert "严格引用校验未通过" in out["reason"]
    assert out["card_md"] if "card_md" in out else True


def test_没有_LLM_明确报错(monkeypatch):
    monkeypatch.setattr(rw, "default_chat", lambda: None)
    out = rw.draft_sections(SRC)
    assert out["ok"] is False and "没有可用的 LLM" in out["reason"]


def test_LLM_抛异常不炸路由(monkeypatch):
    def boom(prompt, system):
        raise TimeoutError("slow")

    out = rw.draft_sections(SRC, chat=boom)
    assert out["ok"] is False and "LLM 调用失败" in out["reason"]


def test_容忍_json_围栏():
    payload = {"items": [{"section": "steps", "text": "t", "quote": "先用 uname -r 判断是不是 WSL"}]}
    call = lambda prompt, system: "\u0060\u0060\u0060json\n" + json.dumps(payload) + "\n\u0060\u0060\u0060"
    out = rw.draft_sections(SRC, chat=call)
    assert out["ok"] is True and out["stats"]["verified"] == 1


def test_空正文直接失败():
    assert rw.draft_sections("   ", chat=_chat({"items": []}))["ok"] is False
