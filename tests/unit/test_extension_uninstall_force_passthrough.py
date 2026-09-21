# -*- coding: utf-8 -*-
"""L37 守卫：force 必须能从「扩展卸载」链路透传到 SKILL 安装器

为什么需要它：SkillsInstaller.remove_skill 自 L20 起对「文件轨独占」技能默认拒绝
（避免不可逆 rmtree）。若 ExtensionManager.uninstall 不透传 force，则经由
POST /api/extensions/uninstall 的调用方**没有显式放行出口** —— 能力被移除而无补救手段。
本守卫钉住「透传确实发生」，且**默认值必须是 False**（不得因透传而放宽默认行为）。
"""
from __future__ import annotations

from agent.extensions.base import ExtensionType
from agent.extensions.manager import ExtensionManager


class _StubSkillInstaller:
    """记录 remove_skill 收到的实参，用于判定 force 是否真的透传下去。"""

    def __init__(self):
        self.calls = []

    def remove_skill(self, skill_id, force=False):
        self.calls.append({"skill_id": skill_id, "force": force})
        return True, "stub"


def _patch_installer(monkeypatch, stub):
    monkeypatch.setattr(ExtensionManager, "_get_installer",
                        lambda self, etype: stub, raising=True)


def test_force_defaults_to_false_and_is_passed_through(monkeypatch):
    stub = _StubSkillInstaller()
    _patch_installer(monkeypatch, stub)
    mgr = ExtensionManager()

    out = mgr.uninstall("skill", "some_skill")
    assert out.get("ok") is True, out
    assert stub.calls == [{"skill_id": "some_skill", "force": False}], (
        "默认调用必须把 force=False 透传下去（不得放宽默认行为）：%r" % (stub.calls,))


def test_force_true_is_passed_through(monkeypatch):
    stub = _StubSkillInstaller()
    _patch_installer(monkeypatch, stub)
    mgr = ExtensionManager()

    out = mgr.uninstall("skill", "some_skill", force=True)
    assert out.get("ok") is True, out
    assert stub.calls == [{"skill_id": "some_skill", "force": True}], (
        "force=True 未被透传 ⇒ 该链路没有显式放行出口：%r" % (stub.calls,))


def test_non_skill_types_are_unaffected_by_force(monkeypatch):
    """非 SKILL 类型不得收到 force（各安装器语义不同，透传会 TypeError）"""
    calls = []

    class _StubMCP:
        def uninstall_mcp(self, sid):
            calls.append(sid)
            return True, "stub"

    _patch_installer(monkeypatch, _StubMCP())
    mgr = ExtensionManager()
    out = mgr.uninstall(ExtensionType.MCP.value, "svc", force=True)
    assert out.get("ok") is True, out
    assert calls == ["svc"]
