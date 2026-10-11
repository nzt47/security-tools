# -*- coding: utf-8 -*-
"""运行期工具声明桥：只有「已注册 + 运维显式声明」的插件/MCP 工具进装配候选。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.lines.models import ToolMeta
from agent.subagent import runtime_tool_decls as rt


def _write(tmp_path, payload):
    p = tmp_path / "decls.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {rt.ENV_RUNTIME_TOOL_DECLS: str(p)}


def test_未配置时不动_meta():
    meta = {"read_file": ToolMeta(name="read_file")}
    out = rt.augment_meta(meta, ["read_file", "mcp_echo"], environ={})
    assert out == meta


def test_已注册且合法声明才并入(tmp_path):
    env = _write(tmp_path, {"mcp_echo": {"plane": "act", "effect": "execute", "risk": "high"}})
    meta = {"read_file": ToolMeta(name="read_file")}
    out = rt.augment_meta(meta, ["read_file", "mcp_echo"], environ=env)
    assert "mcp_echo" in out and out["mcp_echo"].risk == "high"
    assert set(out) == {"read_file", "mcp_echo"}


def test_未注册的声明不凭空进池(tmp_path):
    env = _write(tmp_path, {"ghost": {"plane": "act", "effect": "execute", "risk": "high"}})
    assert rt.augment_meta({}, ["read_file"], environ=env) == {}


@pytest.mark.parametrize("spec", [
    {"plane": "nope", "effect": "execute", "risk": "high"},
    {"plane": "act", "effect": "", "risk": "high"},
    {"plane": "act", "effect": "execute", "risk": "extreme"},
    {"plane": "act", "effect": "execute"},
])
def test_非法声明被跳过(tmp_path, spec):
    env = _write(tmp_path, {"mcp_echo": spec})
    assert rt.augment_meta({}, ["mcp_echo"], environ=env) == {}


def test_不覆盖已有_YAML_声明(tmp_path):
    env = _write(tmp_path, {"read_file": {"plane": "govern", "effect": "write", "risk": "critical"}})
    meta = {"read_file": ToolMeta(name="read_file", plane="perceive", effect="read", risk="low")}
    out = rt.augment_meta(meta, ["read_file"], environ=env)
    assert out["read_file"].plane == "perceive" and out["read_file"].risk == "low"


def test_坏文件_fail_soft(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert rt.augment_meta({}, ["x"], environ={rt.ENV_RUNTIME_TOOL_DECLS: str(p)}) == {}
