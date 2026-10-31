# -*- coding: utf-8 -*-
"""右坞「可交互浏览器」的纯函数/边界单测（不启动真浏览器）。

守三件事：
  1. 导航目标必须过 SSRF 判定（本机/内网/非 http(s) 一律拒）；
  2. 输入载荷只接受白名单类型，坐标/滚动量按视口夹紧、长度有上限；
  3. 没有会话时的调用要明确 404，而不是抛异常或假装成功。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.browser_remote import (  # noqa: E402
    MAX_SCROLL_PX,
    MAX_TEXT_CHARS,
    MAX_VIEWPORT,
    MIN_VIEWPORT,
    BrowserRemote,
    check_target,
    clamp_viewport,
    validate_input,
)


class TestCheckTarget:
    def test_non_http_scheme_rejected(self):
        for bad in ("javascript:alert(1)", "data:text/html,x", "file:///C:/x", ""):
            ok, reason = check_target(bad)
            assert ok is False and reason, bad

    def test_loopback_rejected(self):
        ok, _reason = check_target("http://127.0.0.1:5678/")
        assert ok is False

    def test_metadata_ip_rejected(self):
        ok, _reason = check_target("http://169.254.169.254/latest/meta-data/")
        assert ok is False


class TestClampViewport:
    def test_defaults_and_bounds(self):
        assert clamp_viewport(None, None) == (1280, 800)
        assert clamp_viewport(10, 10) == (MIN_VIEWPORT, MIN_VIEWPORT)
        assert clamp_viewport(99999, "abc") == (MAX_VIEWPORT, 800)


class TestValidateInput:
    def test_unknown_kind_rejected(self):
        with pytest.raises(ValueError):
            validate_input({"kind": "evaluate", "script": "alert(1)"}, 1280, 800)

    def test_click_is_clamped_to_viewport(self):
        cmd = validate_input({"kind": "click", "x": -5, "y": 99999}, 1280, 800)
        assert cmd == {"kind": "click", "x": 0, "y": 799}

    def test_type_length_capped(self):
        with pytest.raises(ValueError):
            validate_input({"kind": "type", "text": "x" * (MAX_TEXT_CHARS + 1)}, 1280, 800)
        assert validate_input({"kind": "type", "text": "ok"}, 1280, 800)["text"] == "ok"

    def test_key_must_be_whitelisted(self):
        with pytest.raises(ValueError):
            validate_input({"kind": "key", "key": "Control+Shift+Q"}, 1280, 800)
        assert validate_input({"kind": "key", "key": "Enter"}, 1280, 800)["key"] == "Enter"

    def test_scroll_is_clamped(self):
        assert validate_input({"kind": "scroll", "dy": 10**9}, 1280, 800)["dy"] == MAX_SCROLL_PX
        assert validate_input({"kind": "scroll", "dy": -(10**9)}, 1280, 800)["dy"] == -MAX_SCROLL_PX

    def test_navigate_rejects_private_target(self):
        with pytest.raises(ValueError):
            validate_input({"kind": "navigate", "url": "http://10.0.0.5/admin"}, 1280, 800)


class TestSessionState:
    def test_input_without_session_is_404(self):
        svc = BrowserRemote()
        result = svc.send_input("nope", {"kind": "click", "x": 1, "y": 1})
        assert result["ok"] is False and result["status"] == 404

    def test_frame_without_session_returns_error(self):
        svc = BrowserRemote()
        data, err = svc.frame("nope")
        assert data is None and err

    def test_open_rejects_private_target_without_launching(self):
        svc = BrowserRemote()
        result = svc.open("http://127.0.0.1:5678/", 800, 600)
        assert result["ok"] is False and result["status"] == 400
        # 没有真的起 loop 线程（起浏览器是重操作，不该被一次非法请求触发）
        assert svc._loop is None

    def test_close_without_session_is_idempotent(self):
        svc = BrowserRemote()
        assert svc.close() == {"ok": True, "closed": False}
