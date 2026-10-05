"""**模块级测试旁路钩子不得被永久改写**（2026-10-05）。

【解决什么 —— 一次真实的跨测试污染，只在单进程全量下显形】
`agent.server_auth._AUTH_DISABLED_FOR_TEST` 是给测试用的**显式**旁路钩子
（生产恒为 False；见 `test_s4_01_server_auth.py` 的说明）。

我在 P1-front 第三批的守卫里把它写成了：

    sa._AUTH_DISABLED_FOR_TEST = True     # 直接赋值 + module 作用域 fixture

那是**永久**改模块全局、不会自动还原。分片跑（多进程、每个 worker 只跑一小片）时看不出来；
但 CI 的「失败基线回归」把 2.3 万条用例放进**单进程**跑，于是它**泄漏给后续测试**：
下游 `test_experience_plugin.py::test_mutating_routes_require_token` 因闸门 fail-open
（本该 401 却放行）报 `AssertionError: 变更型接口必须鉴权` —— 而**功能完全正常**，
且该作业**每次都会红**。

本仓其余 20 余处用这个钩子的地方**一律**是 `monkeypatch.setattr(...)`（自动还原）。
本文件把这条约定变成机械断言：**禁止对这类钩子做直接赋值**，
且**禁止把它们放在非 function 作用域的 fixture 里** ——
monkeypatch 是 function 级，module/session 级 fixture 拿不到它，
而"拿不到"正是当初有人改成直接赋值的原因，也就是这次污染的源头。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"

#: 只允许经 monkeypatch 改写的"进程级测试开关"。
#: 它们都是**模块全局布尔**，一旦被永久改写就会污染同进程的其它测试。
HOOKS = ("_AUTH_DISABLED_FOR_TEST",)

#: 单引号或双引号（用 chr 拼，避免把引号写进本文件的正则字面量里）。
_Q = "[" + chr(39) + chr(34) + "]"

#: 非 function 作用域的 fixture 声明。
_NON_FUNCTION_SCOPE = re.compile(r"scope\s*=\s*" + _Q + r"(module|class|package|session)" + _Q)

#: `@pytest.fixture(scope=<非function>)` 紧跟 `def <name>(`
_SCOPED_FIXTURE = re.compile(
    r"@pytest\.fixture\([^)]*scope\s*=\s*" + _Q + r"(module|class|package|session)" + _Q + r"[^)]*\)"
    r"\s*\ndef\s+(\w+)\s*\("
)

#: `<任意点分前缀>_AUTH_DISABLED_FOR_TEST = ...`（直接赋值，排除 == 与 setattr）
_DIRECT_ASSIGN = re.compile(r"^\s*[\w\.]*" + re.escape(HOOKS[0]) + r"\s*=(?!=)", re.M)


#: **只**排除本文件自身：它的 docstring 里必须写出"错误写法"作为合成样例，
#: 否则判据无法被解释。这与"给扫描器自己开后门"是两回事 ——
#: 故下面用一条自检把这条豁免钉住（见 test_豁免范围只有本文件）。
_SELF = Path(__file__).resolve()


def _test_files():
    for p in sorted(TESTS.rglob("*.py")):
        if "__pycache__" in str(p):
            continue
        # 排除的是"本文件"，不是"这一类文件" —— 命中数必须恰好为 1
        if p.resolve() == _SELF:
            continue
        yield p


def _rel(p):
    return str(p.relative_to(ROOT)).replace("\\", "/")


class Test旁路钩子不得被永久改写:
    def test_没有对钩子的直接赋值(self):
        """全仓扫一遍：不得出现对**进程级测试开关**的直接赋值。"""
        offenders = []
        for p in _test_files():
            try:
                src = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in _DIRECT_ASSIGN.finditer(src):
                line = src[:m.start()].count(chr(10)) + 1
                offenders.append(_rel(p) + ":" + str(line) + " -> " + m.group(0).strip())
        assert not offenders, (
            "以下位置对**进程级测试开关**做了直接赋值（不会自动还原）：" + chr(10) + "  "
            + (chr(10) + "  ").join(offenders)
            + chr(10) + "后果：分片跑看不出来，但**单进程全量**（CI 的「失败基线回归」）里它会污染后续测试 —— "
              "实测下游 test_experience_plugin::test_mutating_routes_require_token 因此 fail-open 报红。"
            + chr(10) + "修法：改成 monkeypatch.setattr(模块, 钩子名, True)，"
              "并把所在 fixture 收窄到 function 作用域（monkeypatch 是 function 级）。"
        )

    def test_引用钩子的非_function_fixture_为零(self):
        """反向：凡在**非 function 作用域** fixture 体内引用这些钩子的，一律报出。"""
        bad = []
        for p in _test_files():
            try:
                src = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if not any(h in src for h in HOOKS):
                continue
            if not _NON_FUNCTION_SCOPE.search(src):
                continue
            for m in _SCOPED_FIXTURE.finditer(src):
                body = src[m.end():]
                end = body.find(chr(10) + "def ")
                body = body if end < 0 else body[:end]
                if any(h in body for h in HOOKS):
                    bad.append(_rel(p) + " :: " + m.group(2) + " (" + m.group(1) + ")")
        assert not bad, (
            "以下**非 function 作用域**的 fixture 引用了进程级测试开关：" + chr(10) + "  "
            + (chr(10) + "  ").join(bad)
            + chr(10) + "monkeypatch 是 function 级夹具，这些 fixture 拿不到它 —— "
              "这正是有人会改写成直接赋值的原因，而直接赋值会跨测试泄漏。"
            + chr(10) + "修法：把 fixture 收窄为 function 作用域并改用 monkeypatch。"
        )

    def test_扫描口径覆盖全集(self):
        """防本文件被改窄而静默通过：至少应扫到上百个测试文件。"""
        n = len(list(_test_files()))
        assert n > 100, (
            "只扫到 " + str(n) + " 个测试文件 —— 扫描口径可能失效（本断言防它静默通过）"
        )

    def test_豁免范围只有本文件(self):
        """自检：被排除的文件必须**恰好**是本文件，且全仓只有它被排除。

        【为什么必须自检】扫描器给自己开豁免是"门禁形同虚设"的经典入口。
        这里断言"排除项 == 1"，若有人把豁免扩大成"跳过 tests/unit/"之类，本用例会红。
        """
        all_files = [p for p in TESTS.rglob("*.py") if "__pycache__" not in str(p)]
        scanned = list(_test_files())
        excluded = [p for p in all_files if p.resolve() == _SELF]
        assert len(excluded) == 1, "本文件自身应恰好被排除 1 次"
        assert len(scanned) == len(all_files) - 1, (
            "被排除的文件数不是 1（扫到 " + str(len(scanned)) + " / 共 " + str(len(all_files))
            + "）—— 豁免被扩大了，门禁会失效。"
        )

    def test_判据对本仓现状成立(self):
        """把本次的**真实成因**钉成一个合成样例：直接赋值必须被抓住。"""
        sample = "sa._AUTH_DISABLED_FOR_TEST = True"
        assert _DIRECT_ASSIGN.search(sample), (
            "判据没有认出 " + sample + " —— 它正是本次跨测试污染的写法，判据失效了。"
        )
        ok = "monkeypatch.setattr(sa, " + chr(34) + "_AUTH_DISABLED_FOR_TEST" + chr(34) + ", True)"
        assert not _DIRECT_ASSIGN.search(ok), (
            "判据把**正确写法**误报为直接赋值：" + ok + " —— 会逼人绕开 monkeypatch。"
        )
