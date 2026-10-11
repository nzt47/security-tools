# -*- coding: utf-8 -*-
"""分身自主权档位（SubagentConfig.autonomy_level）单测。

覆盖：严格解析 / config-ctx 桥 / 工具闸门叠加 / 配置默认与状态回显。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.autonomy import AutonomyLevel
from agent.subagent import autonomy_gate as ag
from agent.subagent.container import SubagentConfig


class TestParse:
    def test_空与None不接入(self):
        assert ag.parse_autonomy_level("") == ""
        assert ag.parse_autonomy_level(None) == ""
        assert ag.parse_autonomy_level("   ") == ""

    def test_合法值归一(self):
        for raw, want in (("L1", "L1"), ("l3", "L3"), ("3", "L3"), ("L5", "L5"), (" 2 ", "L2")):
            assert ag.parse_autonomy_level(raw) == want

    def test_非法值返回None(self):
        for raw in ("L0", "L6", "X", "abc", "0", "6"):
            assert ag.parse_autonomy_level(raw) is None


class _Ctx:
    def __init__(self, metadata=None):
        self.metadata = metadata if metadata is not None else {}


class TestBridge:
    def test_空档不碰ctx(self):
        ctx = _Ctx({"a": 1})
        ag.attach_autonomy_metadata(ctx, SubagentConfig(name="x", model_id="m"))
        assert ctx.metadata == {"a": 1}

    def test_有效档写入metadata(self):
        ctx = _Ctx({})
        ag.attach_autonomy_metadata(ctx, SubagentConfig(name="x", model_id="m", autonomy_level="L2"))
        assert ctx.metadata["autonomy_level"] == "L2"
        assert ag.metadata_level(ctx.metadata) == AutonomyLevel.L2

    def test_metadata_level_缺失或非法为None(self):
        assert ag.metadata_level(None) is None
        assert ag.metadata_level({}) is None
        assert ag.metadata_level({"autonomy_level": "bogus"}) is None


class TestGate:
    def test_低档拒绝写与系统工具(self):
        assert ag.out_of_level_reason("write_file", AutonomyLevel.L1)
        assert ag.out_of_level_reason("shell", AutonomyLevel.L1)

    def test_低档允许只读(self):
        assert ag.out_of_level_reason("read_file", AutonomyLevel.L1) == ""
        assert ag.out_of_level_reason("web_search", AutonomyLevel.L1) == ""

    def test_中档允许写但拒绝系统级(self):
        assert ag.out_of_level_reason("write_file", AutonomyLevel.L3) == ""
        assert ag.out_of_level_reason("shell", AutonomyLevel.L3)

    def test_空名不判(self):
        assert ag.out_of_level_reason("", AutonomyLevel.L1) == ""


class TestConfig:
    def test_默认不接入(self):
        assert SubagentConfig(name="x", model_id="m").autonomy_level == ""

    def test_get_status回显(self):
        from agent.subagent.container import SubagentContainer
        c = SubagentContainer(SubagentConfig(name="x", model_id="m", autonomy_level="L4"))
        assert c.get_status()["autonomy_level"] == "L4"
