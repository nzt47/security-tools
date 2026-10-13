"""agent.context_stats 会话读数暂存区单测（2026-10-14 对齐 DeepSeek Harness）"""
from __future__ import annotations

import agent.context_stats as cs


def setup_function():
    cs.reset()


class TestRecordTurn:
    def test_累计模型与工具用时(self):
        cs.record_turn("s1", model_ms=100, tool_ms=20, ttft_ms=50)
        cs.record_turn("s1", model_ms=200, tool_ms=5, ttft_ms=70)
        got = cs.get_session_stats("s1")
        assert got["turns"] == 2
        assert got["model_ms"] == 300.0
        assert got["tool_ms"] == 25.0
        assert got["ttft_ms"] == 60.0          # (50+70)/2
        assert got["ttft_samples"] == 2

    def test_会话之间互不串扰(self):
        cs.record_turn("a", model_ms=10)
        cs.record_turn("b", model_ms=99)
        assert cs.get_session_stats("a")["model_ms"] == 10.0
        assert cs.get_session_stats("b")["model_ms"] == 99.0

    def test_无记录返回零值不报错(self):
        got = cs.get_session_stats("nope")
        assert got["turns"] == 0
        assert got["model_ms"] == 0.0
        assert got["ttft_ms"] is None

    def test_空会话id不记(self):
        cs.record_turn("", model_ms=5)
        assert cs.get_session_stats("")["turns"] == 0

    def test_breakdown取最近一轮(self):
        cs.record_turn("s", breakdown={"system_tokens": 1})
        cs.record_turn("s", breakdown={"system_tokens": 2, "tools_tokens": 3})
        assert cs.get_session_stats("s")["breakdown"] == {
            "system_tokens": 2, "tools_tokens": 3}

    def test_ttft样本上限为50(self):
        for i in range(60):
            cs.record_turn("s", ttft_ms=float(i))
        assert cs.get_session_stats("s")["ttft_samples"] == 50

    def test_reset可清单个会话(self):
        cs.record_turn("x", model_ms=1)
        cs.record_turn("y", model_ms=2)
        cs.reset("x")
        assert cs.get_session_stats("x")["turns"] == 0
        assert cs.get_session_stats("y")["turns"] == 1
