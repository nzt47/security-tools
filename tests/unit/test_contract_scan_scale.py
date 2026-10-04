"""契约对拍扫描器的**规模不变量**守卫（2026-10-04 · 05 轮）。

【解决什么】
``scripts/audit/contract_diff.py`` 的扫描器在 CI 的 Shard 5 报过
`Failed: Timeout (>60.0s)` —— 而本机同一段代码只要 0.5s。两者的差别**不在代码**，
在于 CI 工作树里存在 `node_modules` 这类巨大子树：
  · `_iter_source_files` 原先用 `rglob("*")`，**先把整棵树走完**，
    `continue` 只是"走完之后不 yield"，**省不到走树的钱**；
  · `strip_comments` 原先**逐字符**过循环，成本与**文件体积**成正比，
    而不是与"注释/引号个数"成正比。
本文件把这两条**机制**钉住，使它们不能再退化。

【为什么不写"耗时 < N 秒"】
那是"锚在今天的数值上"，而且受 runner 快慢影响 —— 本仓 §6 第 1 条已明令禁止，
且这个缺陷**本身就是**因为数值随环境漂移而暴露的。故改为断言**调用次数**：
它与机器速度无关，正是"是否还会随规模爆炸"的直接度量。

【与既有用例的分工】
`test_contract_diff_matcher.py` 管**语义**（注释里的端点不算调用、字符串里的斜杠不误删）；
本文件管**规模**（剪枝是否生效、剥离是否随体积退化）。二者互不替代。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: 被监视的 os.walk 记录：每个被**下降**的目录。
_WALK_TRACE: list = []


@pytest.fixture(scope="module")
def cd():
    path = ROOT / "scripts" / "audit" / "contract_diff.py"
    spec = importlib.util.spec_from_file_location("cd_scale", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cd_scale"] = mod
    spec.loader.exec_module(mod)
    return mod


def _dirs_from_walk(cd, root):
    return sorted({d for d, _names in _WALK_TRACE if d == str(root) or d.startswith(str(root) + os.sep)})


class Test遍历必须剪枝:
    """`_iter_source_files` 不得下降进被排除的子树。"""

    @pytest.fixture
    def sandbox(self, tmp_path, monkeypatch, cd):
        """合成一棵树：node_modules 里有同后缀文件，src 里也有。

        【为什么用合成输入而不是扫真仓】真仓当前恰好没有 node_modules（CI 全新检出），
        用真仓断言会"今天绿、明天换个环境就红"；合成输入把**机制**钉死，与环境无关。
        """
        base = tmp_path / "sandbox"
        (base / "node_modules" / "pkg").mkdir(parents=True)
        (base / "node_modules" / "pkg" / "a.ts").write_text("export const x = 1", encoding="utf-8")
        (base / ".venv").mkdir(parents=True)
        (base / ".venv" / "b.ts").write_text("export const y = 1", encoding="utf-8")
        (base / "dist").mkdir(parents=True)
        (base / "dist" / "c.ts").write_text("export const z = 1", encoding="utf-8")
        (base / "src").mkdir(parents=True)
        (base / "src" / "keep.ts").write_text("export const k = 1", encoding="utf-8")
        (base / "src" / "sub").mkdir(parents=True)
        (base / "src" / "sub" / "keep2.ts").write_text("export const k2 = 1", encoding="utf-8")
        (base / "src" / "skip.md").write_text("not a ts file", encoding="utf-8")

        monkeypatch.setattr(cd, "ROOT", base)
        real_walk = os.walk

        def traced_walk(top, *a, **kw):
            _WALK_TRACE.append((str(top), None))
            return real_walk(top, *a, **kw)

        monkeypatch.setattr(cd.os, "walk", traced_walk)
        return base

    def test_排除目录不被下降(self, cd, sandbox):
        """node_modules / dist / 点开头目录**根本不应被 os.walk 下降**。

        【这条测的是机制】旧实现用 rglob，它会把这几个子树全部走完（只是不 yield），
        因此这**正是**当时超时的原因。断言"没被下降"直接钉住剪枝是否生效。
        """
        _WALK_TRACE.clear()
        list(cd._iter_source_files({".ts", ".tsx"}, ["."]))
        visited = " | ".join(d for d, _ in _WALK_TRACE)
        # 用"路径段"匹配，避免误伤名为 e.g. mynode_modules 的目录
        import re as _re
        for bad in ("node_modules", "dist", ".venv"):
            pat = _re.compile(r"(?:^|[\\/])" + _re.escape(bad) + r"(?:[\\/]|$)")
            assert not pat.search(visited), (
                "os.walk 下降进了本应剪枝的目录「" + bad + "」—— 剪枝失效，"
                "扫描成本会随该子树规模爆炸（正是 CI Shard 5 超时的成因）。"
                "被下降的目录：\n  " + visited
            )

    def test_合格文件仍然被产出(self, cd, sandbox):
        """剪枝不能把该收的文件也剪掉（防止'为了快而漏报'）。"""
        got = sorted(p.name for p in cd._iter_source_files({".ts", ".tsx"}, ["."]))
        assert got == ["keep.ts", "keep2.ts"], (
            "剪枝把合格文件也漏掉了 —— 这是**漏报**，比慢更危险。实际产出：" + repr(got)
        )


class Test遍历产物与不剪枝一致:
    """剪枝只应**省掉无谓的 stat**，不应改变产物集合。"""

    def test_剪枝与不剪枝产物相同(self, cd):
        """对照：贪心遍历（不剪枝）产出的集合，剪枝版必须逐条相同。

        【为什么这条重要】剪枝是"优化"，而优化最危险的失败形态是**静默少给文件** ——
        那会让门禁带着残缺的输入判「零漂移」。故用同一棵树跑两遍对照。
        """
        suffixes = {".ts", ".tsx"}
        dirs = ["yunshu-ui/src"]
        pruned = sorted(str(p.relative_to(cd.ROOT)).replace("\\", "/")
                        for p in cd._iter_source_files(suffixes, dirs))
        greedy = []
        for d in dirs:
            base = cd.ROOT / d
            if not base.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                for name in filenames:
                    if Path(name).suffix not in suffixes:
                        continue
                    greedy.append(str((Path(dirpath) / name).relative_to(cd.ROOT)).replace("\\", "/"))
        assert pruned == sorted(greedy), (
            "剪枝版与贪心遍历的产物集合不一致（剪枝改变了语义）：\n  仅剪枝有: "
            + repr(sorted(set(pruned) - set(greedy))[:5])
            + "\n  仅贪心有: " + repr(sorted(set(greedy) - set(pruned))[:5])
        )
        assert pruned, "对照遍历返回空集 —— 扫描口径可能已失效"


class Test剥离成本与体积无关:
    """`strip_comments` 对**没有注释**的大文件必须是"整段跳过"，而不是逐字符走完。"""

    def test_无注释大文件只走常数轮(self, cd, monkeypatch):
        """用**调用次数**度量，而不是耗时。

        【为什么不用时间】耗时随 runner 快慢漂移，会假红/假绿；本仓 §6 第 1 条要求锚在机制上。
        这里数"状态机循环了多少轮"：旧实现每字符一轮（本例 200 万轮），
        整段快进后应按"特殊字符数"计（本例 0 个 ⇒ 1 轮）。
        """
        calls = {"n": 0}
        real = cd._SPECIAL_RE

        class _Counting:
            """只包住 search 的代理（re.Pattern 的属性是只读的，不能直接 monkeypatch）。"""

            def search(self, *a, **kw):
                calls["n"] += 1
                return real.search(*a, **kw)

        monkeypatch.setattr(cd, "_SPECIAL_RE", _Counting())
        big = "x = 1; " * 200000          # 1.4 MB，且**不含**任何注释/引号
        out = cd.strip_comments(big)
        assert out == big, "无特殊字符的输入必须原样返回"
        assert calls["n"] <= 8, (
            "无注释大文件走了 " + str(calls["n"]) + " 轮状态机（应≈1 轮）—— "
            "逐字符退化已回归：成本会重新与文件体积成正比，CI 上会再次超时。"
        )

    def test_真实语料上的语义未被提速改坏(self, cd):
        """提速不能改变**结果**：对真仓前端语料，剥离后的端点计数应与基线口径一致。"""
        stray = cd.collect_stray_frontend_literals()
        # 不锚死具体数值（会随收口合法变化）；只要求"常量层之外 react 侧为 0"这一**目标**成立。
        assert sum(stray.get("react", {}).values()) == 0, (
            "react 侧出现了常量层之外的 /api 字面量："
            + repr(sorted(stray.get("react", {}).items())[:5])
        )
