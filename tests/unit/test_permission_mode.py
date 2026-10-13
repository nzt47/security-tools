# -*- coding: utf-8 -*-
"""权限档位（CP_TOOL_PERMISSION_MODE）单元测试（2026-10-13）

对齐 DSH 的 sandbox/permission modes：仅可查看 / 工作区修改 / 自动审查 / 完全权限。
这里只钉**档位判定本身**（确定性、不碰审批台账）：
  · read_only：非只读能力 DENY，只读放行；
  · workspace：read/write 免审批放行；execute/govern 仍走既有分级（不 bypass）；
  · full：黑名单之外一律免审批；
  · 默认/非法值 ⇒ auto（逐字保持既有行为）。
"""
from __future__ import annotations

import pytest

from agent import tool_gate as tg


@pytest.fixture
def mode(monkeypatch):
    def _set(value):
        if value is None:
            monkeypatch.delenv(tg.PERMISSION_MODE_ENV, raising=False)
        else:
            monkeypatch.setenv(tg.PERMISSION_MODE_ENV, value)
        return value
    return _set


class TestPermissionModeParsing:
    def test_默认与非法值都按_auto(self, mode):
        mode(None)
        assert tg._permission_mode() == tg.MODE_AUTO
        mode("nonsense")
        assert tg._permission_mode() == tg.MODE_AUTO

    def test_四档都被识别(self, mode):
        for m in tg.PERMISSION_MODES:
            mode(m)
            assert tg._permission_mode() == m


class TestPermissionModeOutcome:
    def test_read_only_只读放行写拒绝(self, mode):
        mode(tg.MODE_READ_ONLY)
        assert tg._permission_mode_outcome("read_file") == (None, True)
        deny, bypass = tg._permission_mode_outcome("write_file")
        assert deny is not None and deny.get("error_code") == "PERMISSION_DENIED"
        assert bypass is False

    def test_workspace_读写免审批_执行不绕过(self, mode):
        mode(tg.MODE_WORKSPACE)
        assert tg._permission_mode_outcome("write_file") == (None, True)
        # shell_execute: plane=act / effect=execute ⇒ 仍走既有分级（不 bypass）
        assert tg._permission_mode_outcome("shell_execute") == (None, False)

    def test_full_执行也免审批(self, mode):
        mode(tg.MODE_FULL)
        assert tg._permission_mode_outcome("shell_execute") == (None, True)

    def test_auto_不改既有判定(self, mode):
        mode(tg.MODE_AUTO)
        assert tg._permission_mode_outcome("write_file") == (None, False)
        assert tg._permission_mode_outcome("shell_execute") == (None, False)


class TestPermissionModeContextvar:
    """请求级档位（contextvar）必须**优先于** env/覆盖层，且复位后回落到 env。"""

    def test_contextvar_优先且可复位(self, monkeypatch):
        monkeypatch.setenv(tg.PERMISSION_MODE_ENV, tg.MODE_FULL)
        token = tg.set_permission_mode(tg.MODE_READ_ONLY)
        try:
            assert tg._permission_mode() == tg.MODE_READ_ONLY
            assert tg.current_permission_mode() == tg.MODE_READ_ONLY
            # 档位即时生效：写工具被拒（而不是走 env 的 full 放行）
            deny = tg.check_tool_call("write_file", {})
            assert deny is not None and deny.get("error_code") == "PERMISSION_DENIED"
        finally:
            tg.reset_permission_mode(token)
        # 复位 ⇒ 回落 env
        assert tg.current_permission_mode() == ""
        assert tg._permission_mode() == tg.MODE_FULL

    def test_非法档位不写进_contextvar(self):
        token = tg.set_permission_mode("bogus")
        try:
            assert tg.current_permission_mode() == ""
            assert tg._permission_mode() == tg.MODE_AUTO
        finally:
            tg.reset_permission_mode(token)


class TestPermissionModeThroughGate:
    def test_read_only_闸门真的拒绝写工具(self, mode):
        mode(tg.MODE_READ_ONLY)
        r = tg.check_tool_call("write_file", {})
        assert r is not None and r.get("error_code") == "PERMISSION_DENIED"
        assert tg.check_tool_call("read_file", {}) is None

    def test_full_闸门对写工具放行(self, mode):
        mode(tg.MODE_FULL)
        assert tg.check_tool_call("write_file", {}) is None
