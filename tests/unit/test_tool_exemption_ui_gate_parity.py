# -*- coding: utf-8 -*-
"""逐工具豁免开关：**界面口径必须等于闸门实际行为**（2026-10-01 修假绿灯的守卫）。

## 缺陷现场

`agent/tool_gate.py::_confirm_level_outcome` 有一条**硬规则：L3 豁免一律忽略**
（L3 = 默认禁止，仅显式预授权 SA+scope 可执行）。
而 `agent/tool_exemptions.py::candidates()` 此前只看「是否被**描述符**门控」，
于是**实测**把 10 个 L3 工具全部标成 `exemptable=True, blocked_reason=''`：

    shell_execute / run_sandbox / generate_tool / connect_mcp / disconnect_mcp /
    scan_mcp / ext_install / ext_uninstall / ext_toggle / ext_configure

⇒ 用户在界面上给这些工具点「需确认」徽章，界面显示已豁免、覆盖层也确实写进去了，
**但闸门照样要求确认** —— 典型的「点了没反应」假绿灯。

## 为什么此前没被发现（这条注释本身就是错的）

`tool_gate.py` 该处注释声称：「实测 10 个 L3 工具**全部**被 data/descriptors.json 的
`trust.requires_approval` 描述符门控，而描述符判定在本层之前 ⇒ 本规则**当前零行为影响**」，
并据此断言「界面不会因此说谎」。

**实测推翻**：`data/descriptors.json` 有 32 条台账，`trust.requires_approval=true` 的
**一条都没有** ⇒ `descriptor_gated_tools()` 返回**空集** ⇒ 那条 L3 规则不是"零影响"，
而恰恰是这些徽章点不动的**唯一原因**。

## 本文件锁住的不变量

对 `candidates()` 列出的**每一个**工具：

    candidates()[i]["exemptable"]  ==  「把该工具写进豁免名单后，闸门是否真的因此放行」

这条是**行为级**对拍（真的去调闸门的判定函数），不是文本断言 ——
文本断言正是当初没能拦住这个缺陷的原因。
"""
from __future__ import annotations

import pytest

import agent.tool_exemptions as TE
import agent.tool_gate as TG


@pytest.fixture(autouse=True)
def _no_audit_noise(monkeypatch):
    """本文件会逐个工具试名单，禁掉「生效值变更」的审计写入，避免污染 data/。"""
    monkeypatch.setattr(TG, "_record_exempt_change_if_needed", lambda *a, **k: None)


def _gate_would_honor(name: str) -> bool:
    """把 name 写进豁免名单后，闸门**是否真的**因此放行（复刻闸门的真实判定）。"""
    lvl, _meta = TG._confirm_level_of(name)
    if lvl == "L3":
        return False          # 闸门硬规则：L3 豁免一律忽略
    return bool(TG._is_confirm_level_exempt(name))


class Test界面与闸门口径逐工具一致:
    def test_exemptable_必须等于闸门实际行为(self, monkeypatch):
        cands = TE.candidates()
        assert cands, "candidates() 返回空 ⇒ 口径失效，本测试会假通过"
        bad = []
        for c in cands:
            name = c["tool"]
            monkeypatch.setenv("CP_TOOL_CONFIRM_LEVEL_EXEMPT", name)
            honored = _gate_would_honor(name)
            if bool(c["exemptable"]) != honored:
                bad.append((name, c["level"], c["exemptable"], honored, c.get("blocked_reason", "")))
        assert bad == [], (
            "以下工具的『界面 exemptable』与『闸门实际是否放行』不一致 ⇒ 假绿灯/假红灯："
            "（工具, 级别, 界面说可豁免, 闸门实际放行, blocked_reason） = %r" % (bad,))

    def test_不可豁免的工具必须给出原因(self):
        """界面把徽章置灰时，必须同时告诉用户**为什么**、以及正路怎么走。

        否则用户只会看到"点了没反应"，仍然不知道 L3 只能走 SA 预授权。
        """
        missing = [c["tool"] for c in TE.candidates()
                   if not c["exemptable"] and not str(c.get("blocked_reason") or "").strip()]
        assert missing == [], "这些工具不可豁免却没写原因: %r" % (missing,)


class TestL3不可豁免:
    """把闸门那条硬规则钉住：**L3 命中豁免名单也必须照常走确认**。

    【为什么不能只断言 `_is_confirm_level_exempt(name) is False`】
    （本文件初版就是这么写的，实测转红才发现自己写错了。）
    `_is_confirm_level_exempt()` 只是**名单成员判断** —— 它**故意**对 L3 也返回 True；
    真正的 L3 否决发生在**下一层** `_confirm_level_outcome()` 里（那里才 warn + 继续走确认）。
    所以断言必须落在**裁决结果**上，否则测的是一个不存在的承诺。
    """

    @pytest.fixture()
    def _no_tickets(self, monkeypatch):
        """把「挂审批单」换成哨兵值：本测试要的是"**走到没走到**审批分支"，
        而不是真的往业主的审批收件箱里塞单子（真实调用会写 approval_records）。"""
        sentinel = {"error_code": "APPROVAL_REQUIRED", "_test_sentinel": True}
        monkeypatch.setattr(TG, "_tool_approval_outcome",
                            lambda *a, **k: dict(sentinel))

    @pytest.mark.parametrize("name", [
        "shell_execute", "run_sandbox", "generate_tool",
        "connect_mcp", "disconnect_mcp", "scan_mcp",
        "ext_install", "ext_uninstall", "ext_toggle", "ext_configure",
    ])
    def test_l3命中豁免也必须走确认(self, name, monkeypatch, _no_tickets):
        lvl, _m = TG._confirm_level_of(name)
        assert lvl == "L3", "%s 的级别变了（%s）—— 请同步本测试与交付报告 §9.14 的口径" % (name, lvl)
        # 【必须显式打开审批边界总开关】它关着时闸门走「告警一次后放行」，
        #   于是**任何**工具都会返回 None ⇒ 本测试会变成"永远通过"的空断言。
        #   测试环境默认把它置 0（见 conftest），所以这里必须自己打开。
        monkeypatch.setenv("CP_TOOL_GATE_APPROVAL_ENFORCE", "1")
        monkeypatch.setenv("CP_TOOL_CONFIRM_LEVEL_EXEMPT", name)
        # 名单成员判断对 L3 也返回 True（这是既有实现，勿当成 bug 改）
        assert TG._is_confirm_level_exempt(name) is True, \
            "%s：_is_confirm_level_exempt 只是名单成员判断，语义变了请同步本测试" % name
        out = TG._confirm_level_outcome(name, None)
        assert out is not None, (
            "%s 是 L3 却被豁免放行了 ⇒ 闸门的 L3 硬规则被破坏（这正是用户会踩的「假绿灯」）" % name)
        assert out.get("error_code") == "APPROVAL_REQUIRED", out

    @pytest.mark.parametrize("name", ["delegate", "fan_out", "write_file"])
    def test_l1_l2命中豁免则真的放行(self, name, monkeypatch, _no_tickets):
        """对照组：同一份名单，L1/L2 必须**真的**不再要确认（否则豁免功能等于没实现）。

        同样必须打开总开关，否则"放行"可能来自总开关而非豁免 ⇒ 断言无意义。
        """
        monkeypatch.setenv("CP_TOOL_GATE_APPROVAL_ENFORCE", "1")
        monkeypatch.setenv("CP_TOOL_CONFIRM_LEVEL_EXEMPT", name)
        assert TG._confirm_level_outcome(name, None) is None, \
            "%s 命中豁免名单却被拦 ⇒ 豁免开关无效果" % name

    def test_l3工具在界面上必须置灰(self):
        by_name = {c["tool"]: c for c in TE.candidates()}
        for name in ("shell_execute", "run_sandbox", "generate_tool"):
            c = by_name.get(name)
            assert c is not None, "%s 不在 candidates() 里（口径变了？）" % name
            assert c["exemptable"] is False, \
                "%s 是 L3 却显示可豁免 ⇒ 用户点了没反应（正是本次修的假绿灯）" % name
            assert "L3" in c["blocked_reason"], \
                "%s 的 blocked_reason 必须点明 L3 不可豁免，实际: %r" % (name, c["blocked_reason"])


class Test本次涉及的两个工具的级别:
    """`delegate` / `fan_out` 的实际级别与豁免可行性（业主关心的就是这两个）。

    锁住它们，避免"应不用审核"这种诉求被误当成"配置没生效"反复排查。
    """

    def test_delegate_是L1_可豁免(self, monkeypatch):
        lvl, meta = TG._confirm_level_of("delegate")
        assert lvl == "L1", "delegate 级别变了: %s" % lvl
        assert str(getattr(meta, "risk", "")) == "medium"
        monkeypatch.setenv("CP_TOOL_CONFIRM_LEVEL_EXEMPT", "delegate")
        assert TG._is_confirm_level_exempt("delegate") is True

    def test_fan_out_是L2_可豁免(self, monkeypatch):
        lvl, meta = TG._confirm_level_of("fan_out")
        assert lvl == "L2", "fan_out 级别变了: %s" % lvl
        assert str(getattr(meta, "risk", "")) == "high"
        monkeypatch.setenv("CP_TOOL_CONFIRM_LEVEL_EXEMPT", "fan_out")
        assert TG._is_confirm_level_exempt("fan_out") is True

    def test_工具名与canonical_id两种写法都能命中(self, monkeypatch):
        """写名单的人不必知道闸门内部用哪个键比对（既有承诺，勿回退）。"""
        monkeypatch.setenv("CP_TOOL_CONFIRM_LEVEL_EXEMPT", "cp.builtin.fan_out")
        assert TG._is_confirm_level_exempt("fan_out") is True
