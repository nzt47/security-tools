# -*- coding: utf-8 -*-
"""通道「启用/停用」的**单一真相**守卫（2026-11-12 现场 bug：停用按钮点了没用）。

【现场】Owner 在 消息中心→通道→已安装 里点 email_smtp 的「停用」：
- 界面标签变成"已装（未启用）"，但报警照发（通道仍然在发）；
- 再点一次没有任何变化 —— 因为按钮按 `enabled` 取反，而 toggle 只改了 `status`。

根因两条，本文件各钉一组：
1. `ChannelInstaller.toggle_channel` 只写 `status`，而"发不发"的判据是 `enabled`
   （`send_message` / `channel_bridge._enabled_channels` 都读它）⇒ 点停用只改标签；
2. `discover_available_channels` 的模板不带 `enabled` ⇒ 装好的通道永远显示"已装（未启用）"。

口径（改这里要同批改测试）：**一处真相 = `is_channel_enabled(row)`** ——
`enabled` 为真 **且** status 不是 uninstalled/disabled（兼容历史"status 改了、enabled 没改"的行）。
"""
from __future__ import annotations

import pytest

from agent.extensions.base import ExtensionStatus, ExtensionType
from agent.extensions.channel_bridge import _enabled_channels
from agent.extensions.channels_installer import ChannelInstaller
from agent.extensions.store import ExtensionStore


@pytest.fixture()
def installer(tmp_path):
    store = ExtensionStore(data_file=str(tmp_path / "extensions.json"))
    inst = ChannelInstaller(store)
    inst.install_builtin_channel("email_smtp")
    return inst


def _row(inst, channel_id="email_smtp"):
    return inst._store.get(ExtensionType.CHANNEL, channel_id)


# ════════════════════════════════════════════════════════════
#  1) toggle 必须同时改 enabled 与 status
# ════════════════════════════════════════════════════════════

def test_toggle_disable_sets_both_enabled_and_status(installer):
    ok, _msg, state = installer.toggle_channel("email_smtp", False)
    assert ok is True and state is False
    row = _row(installer)
    assert row["enabled"] is False
    assert row["status"] == ExtensionStatus.DISABLED.value


def test_toggle_enable_sets_both_back(installer):
    installer.toggle_channel("email_smtp", False)
    ok, _msg, state = installer.toggle_channel("email_smtp", True)
    assert ok is True and state is True
    row = _row(installer)
    assert row["enabled"] is True
    assert row["status"] == ExtensionStatus.ENABLED.value


def test_toggle_twice_returns_to_start(installer):
    """界面按钮是"取反"语义：点两次必须回到原状（否则用户会以为按钮坏了）。"""
    before = bool(_row(installer).get("enabled", True))
    installer.toggle_channel("email_smtp", None)
    installer.toggle_channel("email_smtp", None)
    assert bool(_row(installer).get("enabled", True)) is before


# ════════════════════════════════════════════════════════════
#  2) 一处真相：is_channel_enabled
# ════════════════════════════════════════════════════════════

@pytest.mark.parametrize("row,expected", [
    ({"enabled": True, "status": "installed"}, True),   # 刚装、没启用过：仍然会发（历史口径）
    ({"enabled": True, "status": "enabled"}, True),
    ({"enabled": False, "status": "enabled"}, False),   # enabled 是否决票
    ({"enabled": True, "status": "disabled"}, False),   # 兼容"只改了 status"的历史行
    ({"enabled": True, "status": "uninstalled"}, False),
    ({}, False),                                        # 空行 = 没有这条通道
])
def test_is_channel_enabled_matrix(row, expected):
    assert ChannelInstaller.is_channel_enabled(row) is expected


def test_send_message_refuses_legacy_disabled_row(installer):
    """历史行（status=disabled、enabled=True）也必须发不出去 —— 否则"停用"形同虚设。"""
    installer._store.update_status(ExtensionType.CHANNEL, "email_smtp",
                                   ExtensionStatus.DISABLED, enabled=True)
    called = []
    installer.register_handler("email_smtp", lambda cfg, msg, **kw: called.append(msg) or "ok")
    ok, msg = installer.send_message("email_smtp", "hello")
    assert ok is False and "禁用" in msg
    assert called == []


def test_bridge_skips_legacy_disabled_row(installer):
    installer._store.update_status(ExtensionType.CHANNEL, "email_smtp",
                                   ExtensionStatus.DISABLED, enabled=True)
    assert _enabled_channels(installer) == []


def test_bridge_keeps_installed_but_never_enabled_row(installer):
    """刚装好、还没启用过的通道（status=installed, enabled=True）仍要能被报警外发使用。"""
    rows = _enabled_channels(installer)
    assert [r["ext_id"] for r in rows] == ["email_smtp"]


# ════════════════════════════════════════════════════════════
#  3) 模板卡片要带上"现在发不发"
# ════════════════════════════════════════════════════════════

def test_discover_reports_enabled_for_installed_channel(installer):
    payload = installer.discover_available_channels()
    by_id = {c["id"]: c for c in payload["builtin_channels"]}
    assert by_id["email_smtp"]["installed"] is True
    assert by_id["email_smtp"]["enabled"] is True

    installer.toggle_channel("email_smtp", False)
    payload2 = installer.discover_available_channels()
    by_id2 = {c["id"]: c for c in payload2["builtin_channels"]}
    assert by_id2["email_smtp"]["installed"] is True
    assert by_id2["email_smtp"]["enabled"] is False, "停用后模板卡片必须显示未启用"


# ════════════════════════════════════════════════════════════
#  4) 存储层：update_status 支持一并写 enabled
# ════════════════════════════════════════════════════════════

def test_store_update_status_writes_enabled_flag(tmp_path):
    store = ExtensionStore(data_file=str(tmp_path / "extensions.json"))
    inst = ChannelInstaller(store)
    inst.install_builtin_channel("webhook")
    store.update_status(ExtensionType.CHANNEL, "webhook", ExtensionStatus.DISABLED, enabled=False)
    row = store.get(ExtensionType.CHANNEL, "webhook")
    assert row["enabled"] is False and row["status"] == "disabled"
    store.update_status(ExtensionType.CHANNEL, "webhook", ExtensionStatus.ENABLED, enabled=True)
    row = store.get(ExtensionType.CHANNEL, "webhook")
    assert row["enabled"] is True and row["status"] == "enabled"


def test_store_update_status_without_enabled_keeps_it(tmp_path):
    """不传 enabled 时不要顺手改它（配置更新走同一个函数）。"""
    store = ExtensionStore(data_file=str(tmp_path / "extensions.json"))
    inst = ChannelInstaller(store)
    inst.install_builtin_channel("webhook")
    store.update_config(ExtensionType.CHANNEL, "webhook", {"webhook_url": "http://localhost/x"})
    row = store.get(ExtensionType.CHANNEL, "webhook")
    assert row["enabled"] is True
