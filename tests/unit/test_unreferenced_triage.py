# -*- coding: utf-8 -*-
"""「活体但无静态引用」台账的守卫（2026-10-18 · D 项走②）。

【锁死四件事】
  1. 双向一致：52 条 unreferenced 必须全部有判定；台账里不能留已消解的陈旧项；
  2. 判定合法：只允许 keep / keep-ops / deprecate / retire，且 evidence 非空；
  3. 待办可执行：deprecate / retire 必须写 action（否则这本账只是形容词）；
  4. **证伪过**：台账里必须留下这三类"扫描器看不见的真消费者"的证据
     —— legacy JS 拼接、脚本绝对 URL、模板串。它们就是本轮复查的目的。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# 【2026-10-19 CI 实测超时】本模块的用例会走 `route_ui_coverage.classify()` 全仓扫描
# （662 个 py 文件逐个 ast.parse 取 docstring）：CI 上实测 **54.2s 通过 / 60.1s 超时**，
# 正好压在全局 `--timeout=60` 边界上 ⇒ 同一份代码两次 run 一次绿一次红。
# 按 pytest.ini 的既定约定「极慢测试应显式 @pytest.mark.timeout(N) 覆盖」把本模块放宽到 180s：
# 断言一字未动，只是给"扫描型"测试留出与机器负载无关的余量。
pytestmark = pytest.mark.timeout(180)


def _mod():
    return importlib.import_module("scripts.audit.unreferenced_triage")


class Test双向一致:
    def test_当前仓库台账与扫描结果完全一致(self):
        m = _mod()
        targets = m.unreferenced_norms()
        ledger = m.load_ledger()
        assert m.check(targets, ledger) == []

    def test_未登记的unreferenced会被抓到(self):
        m = _mod()
        problems = m.check(["/api/new", "/api/old"], {"/api/old": {"verdict": "keep", "evidence": "x"}})
        assert any("未登记" in p and "/api/new" in p for p in problems)

    def test_陈旧项会被抓到(self):
        m = _mod()
        problems = m.check([], {"/api/gone": {"verdict": "retire", "evidence": "x", "action": "删"}})
        assert any("陈旧" in p and "/api/gone" in p for p in problems)


class Test判定合法:
    def test_非法verdict与空证据都被拒(self):
        m = _mod()
        problems = m.check(
            ["/api/a", "/api/b"],
            {"/api/a": {"verdict": "maybe", "evidence": "x", "action": "y"},
             "/api/b": {"verdict": "keep", "evidence": "   "}},
        )
        assert any("/api/a" in p and "verdict 非法" in p for p in problems)
        assert any("/api/b" in p and "evidence" in p for p in problems)

    def test_retire与deprecate必须写action(self):
        m = _mod()
        problems = m.check(
            ["/api/a", "/api/b"],
            {"/api/a": {"verdict": "retire", "evidence": "x"},
             "/api/b": {"verdict": "deprecate", "evidence": "x"}},
        )
        assert any("/api/a" in p and "action" in p for p in problems)
        assert any("/api/b" in p and "action" in p for p in problems)

    def test_判定取值都必须能被汇总(self):
        """四类判定都要有汇总口径；deprecate 允许为 0（兼容窗口到期后的正常终态）。

        2026-10-20：13 条 deprecate 路由到期删除后 deprecate=0 —— 这正是该类的
        预期终点，故不再要求它恒 >0；将来新增 deprecate 时应重新出现。
        """
        m = _mod()
        counts = m.summarize(m.load_ledger())
        assert set(counts) == set(m.VERDICTS)
        # keep / keep-ops 必有实例（在用的台账条目）；retire / deprecate 允许为 0 ——
        # 那正是清理完成后的正常终态（2026-10-20：6 条 retire 删除、4 条复核改判 keep）。
        for verdict in ("keep", "keep-ops"):
            assert counts[verdict] > 0, counts
        assert counts["retire"] >= 0 and counts["deprecate"] >= 0


class Test证伪留痕:
    """本轮复查的实际价值：两类仍未被扫描器覆盖的消费者必须留在台账证据里，
    已被扫描器接管的那个（绝对 URL）则用"已不再是 unreferenced"来钉。"""

    def test_legacy_js拼接被记录(self):
        m = _mod()
        ev = " ".join(str(e.get("evidence") or "") for e in m.load_ledger().values())
        assert "static/js/approval_console.js" in ev, "base 常量 + 拼接的消费者必须留痕"

    def test_脚本绝对URL已由扫描器接管(self):
        """绝对 URL 的假阴性本轮修进了扫描器（不再只写在台账里）。

        台账只该记**仍未判定**的项：一旦 /api/news、/api/sandbox/run 被扫描器看见，它们
        就从 unreferenced 消失、台账也必须删登记（否则双向校核报陈旧项）。断言"已接管"
        比断言"写过证据"更接近事实。
        """
        import importlib

        coverage = importlib.import_module("scripts.audit.route_ui_coverage")
        entries = {e["norm"]: e for e in coverage.classify()["entries"]}
        for norm in ("/api/news", "/api/sandbox/run"):
            assert entries[norm]["category"] != "unreferenced", norm
            assert entries[norm]["non_ui_refs"], norm

    def test_模板串调用被记录(self):
        m = _mod()
        ev = " ".join(str(e.get("evidence") or "") for e in m.load_ledger().values())
        assert "log_dashboard.html" in ev
