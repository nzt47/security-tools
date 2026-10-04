"""前端 /api 字面量守卫（2026-10-04 · 阶段 5 / R5）—— **已从"只允许收缩"升级为"必须为零"**。

【演进过程，写在这里免得下一个人以为它一直是这样】
  ① 建基线时（本文件第一版）它守的是"只允许收缩"：因为当时 react 还剩 42 处 / 22 文件，
     直接断言 0 会让它长期红、进而被无视（本仓对"会长期红的门禁"有记录）。
  ② 长尾批次把 react 打到 **0** 之后，本文件按当初写下的承诺**升级为最强形式**：
     react 必须恰好为 0，不再需要权衡。
     （原话："真收口到 0 时本用例会红，那时应连同基线与本用例一起更新，而不是让它静默通过。"）
  ③ legacy（templates/ + static/）仍有 2 处，来自两个**待退役的遗留页**
     （templates/search-status.html、static/js/approval_console.js）。
     它们随 K9（legacy 模板 14 → 0 或 ≤2）一起收敛，故这一面仍用收缩式基线。

【一个必须保留的"假零"防线】stray == 0 有两个来源：真的收口了，或者**扫描器坏了**。
后者是典型的"看起来很绿" —— 故 test_扫描器仍在工作_常量层可见 用"常量层必须仍被扫到"
来把两者区分开。这条不能删。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "reports" / "frontend_stray_baseline.json"


@pytest.fixture(scope="module")
def cd():
    path = ROOT / "scripts" / "audit" / "contract_diff.py"
    spec = importlib.util.spec_from_file_location("cd_for_stray", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cd_for_stray"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def baseline():
    assert BASELINE.exists(), (
        "缺少收敛基线 " + str(BASELINE)
        + "。生成：python scripts/audit/contract_diff.py --write-stray-baseline "
          "reports/frontend_stray_baseline.json"
    )
    return json.loads(BASELINE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def current(cd):
    return cd.collect_stray_frontend_literals()


class TestReact必须为零:
    """React 侧已全部收口 —— 这里用**最强形式**断言，不再有"基线余量"。"""

    def test_react_字面量必须恰好为零(self, current):
        per_file = current.get("react", {})
        total = sum(per_file.values())
        assert total == 0, (
            "React 侧出现了常量层之外的 /api 字面量（共 " + str(total) + " 处）：\n  "
            + "\n  ".join(f + "=" + str(n) for f, n in sorted(per_file.items()))
            + "\n修法：在 src/api/endpoints.ts 里加一个导出（常量或构造函数）并改用它。"
            "该层是唯一被许可的端点字面量所在地。"
        )

    def test_扫描器仍在工作_常量层可见(self, cd):
        """**假零防线**：stray==0 也可能来自"扫描器坏了"。

        用"常量层必须仍被扫到"把两者分开 —— 常量层参与对拍（只是不计入 stray），
        若它从对拍结果里消失，说明扫描或排除逻辑坏了，此时 0 是假象。
        """
        lits = cd.collect_frontend_literals()["react"]
        assert lits, "扫描器没有扫到任何 React 字面量 —— stray==0 可能是假象"
        files = {f for fs in lits.values() for f in fs}
        assert cd.SANCTIONED_FRONTEND_LAYER in files, (
            "端点常量层没有出现在对拍结果里 —— 扫描或排除逻辑已失效（0 是假象）"
        )
        assert len(lits) > 50, (
            "对拍到的去重路径只有 " + str(len(lits)) + " 个，量级不对 —— 扫描可能只覆盖了部分文件"
        )


class TestLegacy仍用收缩式基线:
    """legacy 还有 2 处（两个待退役的遗留页）—— 这一面维持"只允许收缩"。"""

    def test_legacy_不得新增或变多(self, baseline, current):
        known = baseline["by_file"].get("legacy", {})
        bad = []
        for f, n in current.get("legacy", {}).items():
            was = known.get(f, 0)
            if n > was:
                bad.append(f + ": " + str(was) + " -> " + str(n))
        assert not bad, (
            "legacy 侧的硬编码字面量变多了（只允许收缩）：\n  " + "\n  ".join(sorted(bad))
        )

    def test_legacy_只允许收缩(self, baseline, current):
        stale = []
        for f, was in baseline["by_file"].get("legacy", {}).items():
            if current.get("legacy", {}).get(f, 0) == 0:
                stale.append(f + "（基线 " + str(was) + " -> 现 0）")
        assert not stale, (
            "以下 legacy 文件已达标但仍在基线里：\n  " + "\n  ".join(sorted(stale))
            + "\n请重跑 --write-stray-baseline 收缩基线（随 K9 退役这两个遗留页后应为空）"
        )


class Test基线文件自洽:
    def test_字段与量级(self, baseline, current):
        assert baseline["sanctioned_layer"] == "yunshu-ui/src/api/endpoints.ts"
        assert isinstance(baseline.get("note"), str) and baseline["note"]
        assert baseline["total"]["react"] == 0, "React 已收口，基线里 React 必须是 0"
        for label, per_file in baseline["by_file"].items():
            assert baseline["total"][label] == sum(per_file.values()), label
            assert baseline["sanctioned_layer"] not in per_file, label

    def test_总量不得高于基线(self, baseline, current):
        for label, per_file in current.items():
            assert sum(per_file.values()) <= baseline["total"][label], label + " 超过了基线总量"


class TestCI_确实带上了本守卫:
    def test_gate_包含本文件(self):
        src = (ROOT / ".github" / "workflows" / "contract-gate.yml").read_text(encoding="utf-8")
        assert "tests/unit/test_frontend_stray_baseline.py" in src, (
            "contract-gate.yml 没跑本文件 —— 守卫写了也没人跑"
        )
