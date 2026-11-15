# -*- coding: utf-8 -*-
"""按主线（分身）分窗：解析优先级 / 夹紧 / 推送（2026-11-15）

口径（与 `agent/lines/context_window.py` 的 docstring 一致）：
  运行时覆盖（面板/接口/环境变量） > 主线档案的 `context_window_tokens` > 内置默认。

覆盖四件事：
  1. `clamp_window` 把越界值夹进 [8192, 1048576]（非数字 ⇒ 下界）；
  2. `line_window` 只认**声明了**的主线，未声明/不存在 ⇒ (0, "")（绝不猜）；
  3. `resolve_window` 的三级优先级与**可复核来源**字符串；
  4. `apply_window_to` 把窗口推给正在跑的编排器，但**不动**用户手调的覆盖。
"""
from __future__ import annotations

import pytest

from agent.lines import context_window as CW
from agent.lines.models import LineProfile, LinesValidationError


# ── 1. 夹紧 ──────────────────────────────────────────────────

def test_clamp_window_bounds():
    assert CW.clamp_window(0) == CW.WINDOW_MIN
    assert CW.clamp_window(-5) == CW.WINDOW_MIN
    assert CW.clamp_window(4096) == CW.WINDOW_MIN
    assert CW.clamp_window(65536) == 65536
    assert CW.clamp_window(10 * CW.WINDOW_MAX) == CW.WINDOW_MAX
    assert CW.clamp_window("bad") == CW.WINDOW_MIN      # 非数字 ⇒ 下界（不是 0 也不是 MAX）


# ── 2. 只认"声明了"的主线 ─────────────────────────────────────

def test_line_window_reads_declared_value():
    """engineering 在档案里声明了 65536（真实档案，不 mock）"""
    value, source = CW.line_window("engineering")
    assert value == 65536
    assert source == "line:engineering(65536)"


def test_line_window_unknown_line_is_zero():
    value, source = CW.line_window("no-such-line-xyz")
    assert (value, source) == (0, "")


def test_line_window_defaults_to_active_line():
    """不传 line_id ⇒ 取 _active.json 的激活主线（本机是 engineering）"""
    from agent.lines.registry import get_line_registry
    active = get_line_registry().get_active()
    value, source = CW.line_window(None)
    if active:
        assert source.startswith(f"line:{active}(")
        assert value >= CW.WINDOW_MIN
    else:
        assert (value, source) == (0, "")


# ── 3. 优先级 + 来源 ────────────────────────────────────────

def test_resolve_window_priority():
    """运行时覆盖 > 主线声明 > 内置默认"""
    assert CW.resolve_window(32768, 131072) == (32768, "runtime_override(32768)")
    assert CW.resolve_window(None, 131072, line_id="engineering") == (
        65536, "line:engineering(65536)")
    assert CW.resolve_window(None, 131072, line_id="no-such-line-xyz") == (
        131072, "builtin_default(131072)")
    assert CW.resolve_window(0, 131072) == (65536, "line:engineering(65536)")


# ── 4. 推给正在跑的编排器 ────────────────────────────────────

class _FakeMemory:
    _token_limit = 131072


class _FakeOrchestrator:
    def __init__(self, override: bool = False):
        self._memory_token_limit = 131072
        self._memory_token_limit_source = "builtin_default(131072)"
        self._memory_token_limit_override = override
        self._memory = _FakeMemory()


def test_apply_window_to_pushes_line_value():
    app = _FakeOrchestrator()
    assert CW.apply_window_to(app, 131072) == (65536, "line:engineering(65536)")
    assert app._memory_token_limit == 65536
    assert app._memory._token_limit == 65536        # 压缩阈值同源（否则两套账）
    assert app._memory_token_limit_source == "line:engineering(65536)"


def test_apply_window_to_respects_user_override():
    """用户手调过窗口 ⇒ 切主线不该把它改回去"""
    app = _FakeOrchestrator(override=True)
    assert CW.apply_window_to(app, 131072) is None
    assert app._memory_token_limit == 131072


def test_apply_window_to_tolerates_missing_runtime():
    assert CW.apply_window_to(None, 131072) is None
    assert CW.apply_window_to(object(), 131072) is None


# ── 5. 档案字段本身 ─────────────────────────────────────────

def test_profile_round_trip_keeps_window_only_when_declared():
    prof = LineProfile.from_dict({"id": "x", "context_window_tokens": 65536})
    assert prof.context_window_tokens == 65536
    assert prof.to_dict()["context_window_tokens"] == 65536
    # 未声明 ⇒ 0 且**不写回**（别给存量档案平白加一行噪音）
    bare = LineProfile.from_dict({"id": "y"})
    assert bare.context_window_tokens == 0
    assert "context_window_tokens" not in bare.to_dict()


def test_profile_rejects_bad_window():
    with pytest.raises(LinesValidationError):
        LineProfile.from_dict({"id": "x", "context_window_tokens": "big"})
    too_small = LineProfile.from_dict({"id": "x", "context_window_tokens": 1024})
    assert any("context_window_tokens" in i for i in too_small.validate())
    too_big = LineProfile.from_dict({"id": "x", "context_window_tokens": 10 ** 9})
    assert any("context_window_tokens" in i for i in too_big.validate())
