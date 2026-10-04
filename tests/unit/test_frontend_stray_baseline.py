"""前端 /api 字面量**收敛进度**守卫（2026-10-04 · 阶段 5 / R5）。

【解决什么】contract_diff 已经能报出"常量层之外还剩多少处 /api 字面量"
（frontend_stray_literals），但**只是报**：没有任何东西阻止它变大。
本文件把它变成**只允许收缩的基线**（纪律与 failures_baseline.txt、鉴权覆盖率基线同款）：
  · 新增文件带字面量 => 红（新代码必须走常量层）；
  · 已有文件字面量变多 => 红；
  · 修好后必须重跑 --write-stray-baseline 让基线收缩（否则基线会退化成永久豁免单）。

【为什么盯这个数而不是"去重路径数"】后者是合同**面**指标 ——
把字面量从页面搬进 src/api/endpoints.ts **不会**让它下降（路径还在，只是换了地方写）。
用它当进度会得出"改了一堆、数字没动"的错误结论（开发中实测过这个错觉）。
stray 才是"还要收口多少"，目标 0。

【当前进度】建基线时：react 42 处 / 22 文件，legacy 2 处 / 2 文件。
（从 256 处一路降下来：客户端层已全部收口 = 0；页面层第一批 8 个文件 -62。）
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


class Test收敛基线:
    def test_新增文件不得带字面量(self, baseline, current):
        """新代码必须走 src/api/endpoints.ts —— 这是本基线的主要作用。"""
        known = {f for group in baseline["by_file"].values() for f in group}
        new = []
        for label, per_file in current.items():
            for f, n in per_file.items():
                if f not in known and n > 0:
                    new.append(label + " " + f + "=" + str(n))
        assert not new, (
            "以下文件新引入了硬编码 /api 字面量（此前不在基线里）：\n  "
            + "\n  ".join(sorted(new))
            + "\n修法：改用 src/api/endpoints.ts 的常量/构造函数；"
            "确需新增端点就在端点层加一个导出。"
        )

    def test_已有文件不得变多(self, baseline, current):
        grown = []
        for label, per_file in current.items():
            for f, n in per_file.items():
                was = baseline["by_file"].get(label, {}).get(f, 0)
                if n > was:
                    grown.append(label + " " + f + ": " + str(was) + " -> " + str(n))
        assert not grown, (
            "以下文件的硬编码字面量变多了（本基线只允许收缩）：\n  "
            + "\n  ".join(sorted(grown))
        )

    def test_基线只允许收缩(self, baseline, current):
        """已清零的文件必须从基线删除，否则基线会退化成永久豁免单。"""
        stale = []
        for label, per_file in baseline["by_file"].items():
            for f, was in per_file.items():
                if current.get(label, {}).get(f, 0) == 0:
                    stale.append(label + " " + f + "（基线 " + str(was) + " -> 现 0）")
        assert not stale, (
            "以下文件已达标（0 处）但仍在基线里：\n  " + "\n  ".join(sorted(stale))
            + "\n请重跑：python scripts/audit/contract_diff.py --write-stray-baseline "
              "reports/frontend_stray_baseline.json"
        )

    def test_基线文件自洽(self, baseline, current):
        assert baseline["sanctioned_layer"] == "yunshu-ui/src/api/endpoints.ts"
        assert isinstance(baseline.get("note"), str) and baseline["note"]
        for label, per_file in baseline["by_file"].items():
            assert baseline["total"][label] == sum(per_file.values()), label
            assert baseline["sanctioned_layer"] not in per_file, label

    def test_当前总数不高于基线总数(self, baseline, current):
        for label, per_file in current.items():
            assert sum(per_file.values()) <= baseline["total"][label], (
                label + " 的 stray 总数超过了基线"
            )

    def test_检测器有分辨力(self, current):
        """证明扫描不是恒空集：当前确实还剩一些字面量在常量层之外。

        【锚法】不写死具体数字（那会随迁移进度合法变化 —— 本会话已因此红过三条 CI），
        只断言"仍有残留"。真收口到 0 时本用例会红，那时应连同基线与本用例一起更新，
        而不是让它静默通过。
        """
        assert sum(v for pf in current.values() for v in pf.values()) > 0, (
            "扫描返回 0 —— 要么真收口干净了（那就该更新基线并调整本用例），"
            "要么扫描逻辑失效了。两种情况都要人工确认，不能静默通过。"
        )

    def test_CI_确实带上了本守卫(self):
        src = (ROOT / ".github" / "workflows" / "contract-gate.yml").read_text(encoding="utf-8")
        assert "tests/unit/test_frontend_stray_baseline.py" in src, (
            "contract-gate.yml 没跑本文件 —— 基线写了也没人守"
        )
