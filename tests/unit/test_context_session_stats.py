"""agent.context_stats 会话读数暂存区单测（2026-10-14 对齐 DeepSeek Harness）"""
from __future__ import annotations

import pytest

import agent.context_stats as cs


@pytest.fixture(autouse=True)
def _reset_stats():
    """每个用例前后清空暂存区（**必须覆盖类内方法**）

    【2026-11-15 修 CI 串数】原来只有模块级的 `setup_function`，而它**只对模块里的
    普通函数生效**：`TestRecordTurn` 这些方法拿不到重置 ⇒ 同一进程里更早跑过的文件
    （真实对话路径 `plugins/chat.py:2801` 也会 `record_turn`）留下的读数会串进断言
    —— 每日定时 test.yml 上实测 `assert 11 == 2`（`turns` 期望 2，实际 11）。
    换成 autouse fixture 后与执行顺序无关。
    复现方式（修前必红）：同一进程里先 `record_turn("s1", model_ms=1)` 记 9 轮，
    再跑本文件 ⇒ 这个用例拿到 turns==11。
    """
    cs.reset()
    yield
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
